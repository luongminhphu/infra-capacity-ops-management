"""Hộp việc chung (trang chủ) and Lịch việc & nhắc hạn — docs/design/super-app.md §2.2.

Each module contributes items from data already in the database (snapshots, backup jobs,
spend tables): no call to vCenter / NetBackup here. Items carry the minimum role / feature
needed to see them; the router filters per user and applies the user's acks.

Levels follow the 4 standard statuses: crit (VƯỢT NGƯỠNG), bad (CẢNH BÁO), warn (THEO DÕI),
plus task (việc chờ ai đó xử lý) and info."""
from urllib.parse import quote
import datetime as dt
import hashlib
import logging
import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session
from .cmdb.models import vc_key
from .shared.text import vn_g

logger = logging.getLogger(__name__)

LEVELS = ("crit", "bad", "warn", "task", "info")
LEVEL_RANK = {k: i for i, k in enumerate(LEVELS)}
LEVEL_LABELS = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "task": "Chờ xử lý", "info": "Thông tin"}
SOURCE_LABELS = {"capacity": "Capacity", "backup": "Backup", "datastore": "Datastore", "snapshot": "Snapshot VM",
                 "alarm": "Alarm vCenter", "health": "Sức khoẻ host", "storage": "Storage", "asset": "Thiết bị Data Center", "license": "License", "spend": "Chi phí & hợp đồng",
                 "system": "Hệ thống", "review": "Rà soát owner", "hardware": "Phần cứng máy chủ", "config": "Lệch cấu hình",
                 "lifecycle": "Vòng đời & bảo mật", "contention": "Nghẽn tài nguyên"}
CACHE_SECONDS = 60
MAX_PER_SOURCE = 60
PUSH_LEVEL_PCT = 90.0
MAX_EVENTS = 20                # drill-down rows per inbox item          # decision 5: Discord alert at the 90 % level


def _key(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return raw if len(raw) <= 200 else raw[:150] + "#" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def item(source: str, level: str, key: str, title: str, detail: str = "", page: str = "", query: str = "",
         due: Optional[dt.date] = None, feature: Optional[str] = None, role: Optional[str] = None,
         push: bool = False, alert_key: Optional[str] = None, page_label: str = "",
         facts: Optional[List[Dict[str, Any]]] = None, links: Optional[List[Dict[str, Any]]] = None,
         events: Optional[List[Dict[str, Any]]] = None, ci: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """role: None (any signed-in user) | 'spend:viewer|editor|approver' | 'admin'.
    alert_key: identity for Discord alerts when it must not change with the level (the inbox key does
    change, so a worsened item reappears after "Xong"); defaults to the key.
    page_label: text of the "open page" button when page + query point at this very object.
    facts: deep-dive rows of the detail pane, {label, value, kind?} (kind 'datetime' = ISO time).
    links: extra jumps, {label, page, query} (a page of the app), {label, entity: {type, name, vc}} (an entity card)
    or {label, href} (an external https link built by a checked helper, e.g. vcenter/links.object_url).
    events: the concrete events behind the item (alerts, failed jobs, sensors, parts), newest / worst first,
    {level, title, detail?, at?, page?, query?, entity?} — the drill-down to the root cause (max MAX_EVENTS).
    ci: the CI the item is about, for the event store (events/service.CiResolver): {type, key} or {type, name, vc?}."""
    return {"key": key[:255], "source": source, "level": level, "title": title[:300], "detail": detail[:600], "page": page,
            "query": query, "pageLabel": page_label, "due": due, "feature": feature, "role": role, "push": push,
            "alertKey": (alert_key or key)[:255], "facts": facts or [], "links": links or [],
            "events": (events or [])[:MAX_EVENTS], "ci": ci}


def _fact(label: str, value: Any, kind: str = "") -> Optional[Dict[str, Any]]:
    if value is None or value == "":
        return None
    return {"label": label, "value": value, **({"kind": kind} if kind else {})}


def _facts(*rows: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if r]


def _entity_link(etype: str, name: Optional[str], vc: Optional[str]) -> List[Dict[str, Any]]:
    """Entity card link for a host / VM (other types have no card addressed by name)."""
    if etype not in ("host", "vm") or not name:
        return []
    return [{"label": f"Thẻ {'host' if etype == 'host' else 'VM'} {name}", "entity": {"type": etype, "name": name, "vc": vc or ""}}]


def _event(level: str, title: str, detail: str = "", at: Optional[str] = None, **jump: Any) -> Dict[str, Any]:
    ev = {"level": level, "title": str(title)[:300], "detail": str(detail or "")[:500], "at": at}
    ev.update({k: v for k, v in jump.items() if v})
    return ev


def _num(v: Optional[float], digits: int = 1) -> str:
    """vi-VN number for detail text (1.234,5)."""
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "—"
    return f"{v:,.{digits}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


ALARM_STATUS_TEXT = {"red": "Đỏ", "yellow": "Vàng"}
ALARM_EVENT_LEVEL = {"red": "crit", "yellow": "bad"}
SEVERITY_LEVEL = {"critical": "crit", "error": "crit", "major": "bad", "warning": "warn", "minor": "warn", "info": "na"}
ENTITY_TEXT = {"host": "Host", "vm": "VM", "datastore": "Datastore", "cluster": "Cluster", "other": "Khác"}


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0f}%"


# ---------------------------------------------------------------- providers
def capacity_items(db: Session) -> List[Dict[str, Any]]:
    from . import capacity, data_access
    from .config import get_settings
    hosts, vms, _ = data_access.get_latest_combined(db, None)
    if not hosts:
        return []
    policy = data_access.get_effective_policy(db, get_settings())
    out = []
    host_rows: Dict[str, List[Dict[str, Any]]] = {}
    for h in capacity.build_host_rows(hosts, vms, policy):
        host_rows.setdefault(h.get("cluster") or "", []).append(h)

    def _hot(h: Dict[str, Any]) -> float:
        return max([x for x in (h.get("cpuPct"), h.get("ramPct")) if isinstance(x, (int, float)) and math.isfinite(x)] or [0])
    for r in capacity.build_cluster_rows(hosts, vms, policy):
        cls = r["overall"]["cls"]
        m = r["model"]
        cpu, ram = (v if isinstance(v, (int, float)) and math.isfinite(v) else None for v in (m.get("cpuPct"), m.get("ramPct")))
        peak = max([x for x in (cpu, ram) if x is not None] or [0])
        if cls not in ("crit", "bad", "warn") and peak < PUSH_LEVEL_PCT:
            continue
        level = cls if cls in ("crit", "bad", "warn") else "warn"
        out.append(item("capacity", level, _key("cap", r["name"], level),
                        f"Cluster {r['name']}: CPU {_pct(cpu)} · RAM {_pct(ram)}",
                        f"{r['hosts']} host, {r['vms']} VM — {r['overall']['text']}. Xem chi tiết và đề xuất ở Capacity.",
                        page="dashboard", push=peak >= PUSH_LEVEL_PCT,
                        alert_key=_key("cap", r["name"]),      # BEQ-04: one alert per cluster, whatever the level
                        ci={"type": "cluster", "name": r["name"]},
                        facts=_facts(_fact("CPU (so với ngưỡng)", _pct(cpu)), _fact("RAM (so với ngưỡng)", _pct(ram)),
                                     _fact("Overcommit vCPU : core", _num(m.get("cpuOvercommit"), 2)),
                                     _fact("RAM cấp / RAM vật lý", _num(m.get("ramOvercommit"), 2)),
                                     _fact("Host · VM bật", f"{r['hosts']} · {m.get('poweredOnCount', 0)}")),
                        events=[_event(h["overall"]["cls"] if h["overall"]["cls"] in ("ok", "warn", "bad", "crit") else "na",
                                       f"Host {h.get('host')}: CPU {_pct(h.get('cpuPct'))} · RAM {_pct(h.get('ramPct'))}",
                                       f"{h.get('vmCount', 0)} VM bật, {_num(h.get('vcpuUsed'), 0)} vCPU, {_num(h.get('ramUsedGB'), 0)} GB RAM cấp.",
                                       entity={"type": "host", "name": h.get("host"), "vc": h.get("vcenterKey") or ""})
                                for h in sorted(host_rows.get(r["name"], []), key=_hot, reverse=True)[:8]]))
    return out


def backup_items(db: Session) -> List[Dict[str, Any]]:
    from .config import get_settings
    if not get_settings().netbackups():
        return []
    from .backup.service import build_summary
    rep = build_summary(db, "all", 7)
    if not rep.get("available", True) and not rep.get("kpis"):
        return []
    out = []
    k = rep.get("kpis") or {}
    for metric, label, page in (("successRate", "Tỷ lệ backup thành công 7 ngày", "bk-jobs"),
                                ("coveragePct", "Độ phủ backup VM", "bk-coverage"),
                                ("rpoCompliancePct", "Client đạt RPO", "bk-clients")):
        v = k.get(metric)
        if v is not None and v < PUSH_LEVEL_PCT:
            out.append(item("backup", "bad", _key("bk", metric, "lt90"), f"{label}: {v:.1f}% (dưới 90%)".replace(".", ","),
                            "Mức cảnh báo vận hành — đã gửi Discord nếu có kênh nhận cảnh báo.", page=page, feature="backup", push=True))
    sev = {"critical": "bad", "warning": "warn"}     # VƯỢT NGƯỠNG is kept for overdue / > 100 % items
    failed = rep.get("failedJobs") or []
    group_key = {g["label"]: g["group"] for g in rep.get("failureGroups") or []}

    def job_event(j: Dict[str, Any]) -> Dict[str, Any]:
        return _event("crit" if (j.get("status") or 0) > 1 else "warn",
                      f"Job {j.get('jobId')} · {j.get('client') or '—'} · status {j.get('status')}",
                      f"{(j.get('statusText') or '').rstrip('.')}. Policy {j.get('policy') or '—'}{(', ' + j['schedule']) if j.get('schedule') else ''}.".strip(),
                      j.get("startTime"), page="bk-jobs", query=f"q={quote(str(j.get('jobId')))}")
    for f in rep.get("findings") or []:
        level = sev.get(f.get("severity"))
        if not level:
            continue
        area, ent = f.get("area"), f.get("entity") or ""
        page, query, page_label, events = "bk-overview", "", "", []
        if area == "Client" and not ent.endswith(" client"):
            page, query, page_label = "bk-clients", f"client={quote(ent)}", "Xem client này"
            events = [job_event(j) for j in failed if j.get("client") == ent]
        elif area == "Client":
            names = [n.strip() for n in (f.get("detail") or "").split(",") if n.strip()]
            page = "bk-clients"
            events = [_event("warn", n, "Quá RPO", page="bk-clients", query=f"client={quote(n)}") for n in names]
        elif area == "Nguyên nhân lỗi":
            page, page_label = "bk-jobs", "Xem job lỗi"
            events = [job_event(j) for j in failed if j.get("group") == ent or group_key.get(ent) == j.get("group")]
        elif area == "Policy" and not ent.endswith(" policy"):
            page, query, page_label = "bk-jobs", f"q={quote(ent)}", "Xem job của policy"
            events = [job_event(j) for j in failed if j.get("policy") == ent]
        elif area == "Job" and (f.get("title") or "").startswith("Job "):
            page, query, page_label = "bk-jobs", f"q={quote((f.get('title') or '').split()[1])}", "Xem job này"
        elif area == "Job" and "thành công" in (f.get("title") or ""):
            page = "bk-jobs"
            events = [job_event(j) for j in failed]
        elif area == "Storage":
            page = "bk-storage"
        out.append(item("backup", level, _key("bkf", f.get("area"), f.get("entity"), f.get("title")),
                        f"{f.get('title') or ''}{' — ' + f['entity'] if f.get('entity') else ''}",
                        f"{f.get('detail') or ''} {('→ ' + f['action']) if f.get('action') else ''}".strip(),
                        page=page, query=query, page_label=page_label, feature="backup",
                        facts=_facts(_fact("Nhóm", area), _fact("Đối tượng", ent)), events=events,
                        ci={"type": "backup_client", "name": ent} if area == "Client" and ent else None))
    return out


def license_items(db: Session, today: dt.date) -> List[Dict[str, Any]]:
    from .models import LicenseEntitlement
    out = []
    for e in db.query(LicenseEntitlement).filter(LicenseEntitlement.valid_to.isnot(None), LicenseEntitlement.kind == "owned"):
        days = (e.valid_to.date() - today).days
        if days > 90:
            continue
        level = "crit" if days < 0 else ("bad" if days <= 30 else "warn")
        out.append(item("license", level, _key("lic", e.id, level),
                        f"License {e.product} ({vn_g(e.quantity)} {e.metric}) {'đã hết hạn' if days < 0 else f'hết hạn sau {days} ngày'}",
                        f"Phạm vi: {e.scope_cluster or e.scope_vcenter or 'toàn bộ'}.", page="lic-assess",
                        due=e.valid_to.date(), feature="license"))
    return out


def spend_items(db: Session, today: dt.date) -> List[Dict[str, Any]]:
    from .spend import service, settings as spend_settings
    from .spend.models import Contract, ContractPayment, OpexItem, Proposal, Quotation, QuoteRequest
    cfg = spend_settings.load(db)
    out = []
    horizon = today + dt.timedelta(days=max(cfg.reminder_days))
    for o in (db.query(OpexItem).filter(OpexItem.status == "active", OpexItem.end_date <= horizon)
              .order_by(OpexItem.end_date).limit(MAX_PER_SOURCE)):
        st = service.derived_status(o, today, cfg)
        level = {"crit": "crit", "bad": "bad", "warn": "warn"}.get(st["tone"])
        if not level:
            continue
        d = st["daysLeft"]
        out.append(item("spend", level, _key("opx", o.code, level), f"{o.code} — {o.title}",
                        f"{'Quá hạn ' + str(-d) + ' ngày' if d < 0 else 'Hết hạn sau ' + str(d) + ' ngày'} ({service.date_vn(o.end_date)}) · "
                        f"{service.money_vn(o.amount_gross)}.", page="spend-opex", query=f"code={quote(o.code)}", due=o.end_date,
                        feature="spend", role="spend:viewer"))
    pays = (db.query(ContractPayment, Contract).join(Contract, Contract.id == ContractPayment.contract_id)
            .filter(ContractPayment.status.in_(("pending", "submitted")), ContractPayment.due_date.isnot(None),
                    ContractPayment.due_date <= today + dt.timedelta(days=30))
            .order_by(ContractPayment.due_date).limit(MAX_PER_SOURCE).all())
    for p, c in pays:
        d = (p.due_date - today).days
        level = "crit" if d < 0 else ("bad" if d <= 7 else "warn")
        out.append(item("spend", level, _key("pay", p.id, level), f"{c.code} — đợt {p.seq}: {p.title}",
                        f"{'Quá hạn ' + str(-d) + ' ngày' if d < 0 else 'Đến hạn sau ' + str(d) + ' ngày'} ({service.date_vn(p.due_date)}) · "
                        f"{service.money_vn(p.amount)} · {'đã nộp hồ sơ' if p.status == 'submitted' else 'chờ hồ sơ'}.",
                        page="spend-contracts", due=p.due_date, feature="spend", role="spend:viewer"))
    comparing = db.query(QuoteRequest).filter(QuoteRequest.status == "comparing").limit(MAX_PER_SOURCE).all()
    # Quotation counts per (request, state) in one grouped query (not one query per request).
    q_counts: Dict[Tuple[int, str], int] = {}
    if comparing:
        q_counts = {(rid, st): n for rid, st, n in
                    db.query(Quotation.request_id, Quotation.state, func.count(Quotation.id))
                    .filter(Quotation.request_id.in_([r.id for r in comparing]),
                            Quotation.state.in_(("verified", "extracted")))
                    .group_by(Quotation.request_id, Quotation.state).all()}
    for r in comparing:
        verified = q_counts.get((r.id, "verified"), 0)
        waiting = q_counts.get((r.id, "extracted"), 0)
        if waiting:
            out.append(item("spend", "task", _key("qtv", r.code, waiting), f"{r.code} — {waiting} báo giá chờ kiểm tra",
                            f"{r.title}: đối chiếu dòng đọc được với file gốc rồi bấm “Đã kiểm tra”.", page="spend-quotes",
                            feature="spend", role="spend:editor"))
        if verified >= r.min_quotes:
            out.append(item("spend", "task", _key("qts", r.code), f"{r.code} — đủ {verified} báo giá, chờ chọn nhà cung cấp",
                            r.title, page="spend-quotes", feature="spend", role="spend:approver"))
    for p in db.query(Proposal).filter(Proposal.status == "submitted").limit(MAX_PER_SOURCE):
        out.append(item("spend", "task", _key("tt", p.code), f"{p.code} — tờ trình chờ duyệt", p.subject,
                        page="spend-proposals", feature="spend", role="spend:approver"))
    from .spend.models import BudgetPlan
    for bp in db.query(BudgetPlan).filter(BudgetPlan.status == "submitted").limit(MAX_PER_SOURCE):
        out.append(item("spend", "task", _key("kh", bp.id, bp.row_version), f"Kế hoạch ngân sách FY {bp.fiscal_year} (v{bp.version}) chờ duyệt",
                        bp.name, page="spend-plan", feature="spend", role="spend:approver"))
    return out


def system_items(db: Session) -> List[Dict[str, Any]]:
    """Admin-only: AI cost of the month at / over the alert threshold (decision: 50 USD) or the budget."""
    from . import ai_platform
    cfg = ai_platform.config(db)
    if cfg is None or not cfg.provider or cfg.provider == "none":
        return []
    spent = ai_platform.month_cost(db)
    budget = cfg.budget_usd if cfg.budget_usd is not None else 100
    alert = cfg.alert_usd if cfg.alert_usd is not None else 50
    if spent < alert:
        return []
    level = "crit" if spent >= budget else "bad"
    month = dt.date.today().strftime("%Y-%m")
    return [item("system", level, _key("ai-cost", month, level),
                 f"Chi phí AI tháng này {spent:.2f} USD".replace(".", ",") + (" — đã chạm hạn mức, AI tạm dừng" if level == "crit" else f" (≥ {alert} USD)"),
                 f"Hạn mức {budget} USD / tháng. Xem theo tác vụ ở Cài đặt → AI & MCP.", page="set-ai", role="admin")]


# ---------------------------------------------------------------- infrastructure health (app/infra.py)
ALARM_CI_TYPE = {"host": "hypervisor_host"}           # alarm entityType → CI type (vm / datastore / cluster are the same)


def datastore_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for d in infra.datastores(db):
        if d["level"] == "ok":
            continue
        out.append(item("datastore", d["level"], _key("ds", d["vcenterKey"], d["key"], d["level"]),
                        f"Datastore {d['name']}: dùng {_pct(d['usedPct'])}"
                        + (f" · đầy sau ~{d['daysToFull']:.0f} ngày" if d.get("daysToFull") is not None else ""),
                        "; ".join(d["reasons"]) + f". Cluster: {', '.join(d['clusters']) or '—'}.",
                        page="infra-ds", query=f"ds={quote(d['vcenterKey'] + '|' + d['key'])}",
                        page_label="Xem datastore này", alert_key=_key("ds", d["vcenterKey"], d["key"]),
                        ci={"type": "datastore", "key": vc_key(d["vcenterKey"], d["key"])},
                        facts=_facts(_fact("vCenter", d["vcenterKey"]), _fact("Cluster", ", ".join(d["clusters"])),
                                     _fact("Đã dùng", _pct(d["usedPct"])),
                                     _fact("Đầy sau", f"~{d['daysToFull']:.0f} ngày" if d.get("daysToFull") is not None else None),
                                     _fact("Storage", f"{d['storage']['array']} · {d['storage']['volume']}" if d.get("storage") else None)),
                        events=[_event(d["level"], r) for r in d["reasons"]]))
    return out


def snapshot_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for s in infra.snapshots(db):
        if s["level"] in ("ok", "warn"):      # watch-level snapshots: page only
            continue
        out.append(item("snapshot", s["level"], _key("snap", s["vcenterKey"], s["vm"], s["level"]),
                        f"VM {s['vm']}: {s['count']} snapshot, cũ nhất {s['ageDays']:.0f} ngày".replace(".", ","),
                        "; ".join(s["reasons"]) + ". Xoá / hợp nhất snapshot sau khi xác nhận với chủ VM.",
                        page="infra-snap", query=f"vm={quote(s['vm'])}", page_label="Xem snapshot của VM",
                        alert_key=_key("snap", s["vcenterKey"], s["vm"]), ci={"type": "vm", "name": s["vm"], "vc": s["vcenterKey"]},
                        facts=_facts(_fact("vCenter", s["vcenterKey"]), _fact("Cluster", s.get("cluster")), _fact("Host", s.get("host")),
                                     _fact("Snapshot cũ nhất", s.get("oldestAt"), "datetime")),
                        events=[_event(s["level"], f"Snapshot {i.get('name') or '—'}", i.get("description") or "", i.get("createdAt"))
                                for i in sorted(s.get("items") or [], key=lambda i: i.get("createdAt") or "")],
                        links=_entity_link("vm", s["vm"], s["vcenterKey"])))
    return out


def alarm_items(db: Session) -> List[Dict[str, Any]]:
    """One item per active alarm; events = every active alarm on the same object + its hardware sensors
    (several alarms on one host usually share one root cause)."""
    from . import infra
    out = []
    every = infra.alarms(db)
    by_entity: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for x in every:
        by_entity.setdefault((x["vcenterKey"], x["entityType"], x["entity"]), []).append(x)
    sensors = {(h["vcenterKey"], h["host"]): h for h in infra.host_health(db) if h["level"] != "ok"}
    for a in every:
        if a["acknowledged"]:
            continue
        out.append(item("alarm", a["level"], _key("alarm", a["vcenterKey"], a["key"], a["status"]),
                        f"Alarm {a['alarm']} — {a['entity']}",
                        f"vCenter {a['vcenterKey']}, {a['entityType']}{(', cluster ' + a['cluster']) if a.get('cluster') else ''}.",
                        page="infra-alarm", query=f"alarm={quote(a['vcenterKey'] + '|' + a['key'])}", page_label="Xem alarm này",
                        alert_key=_key("alarm", a["vcenterKey"], a["key"]),
                        ci={"type": ALARM_CI_TYPE.get(a["entityType"], a["entityType"]), "name": a["entity"], "vc": a["vcenterKey"]},
                        facts=_facts(_fact("Alarm", a["alarm"]), _fact("Trạng thái trên vCenter", ALARM_STATUS_TEXT.get(a["status"], a["status"])),
                                     _fact("Đối tượng", f"{ENTITY_TEXT.get(a['entityType'], a['entityType'])} {a['entity']}"),
                                     _fact("Cluster", a.get("cluster")), _fact("vCenter", a["vcenterKey"]),
                                     _fact("Kích hoạt lúc", a.get("at"), "datetime")),
                        events=[_event(ALARM_EVENT_LEVEL.get(x["status"], "warn"), f"Alarm {x['alarm']}",
                                       ("Alarm đang xem. " if x is a else "Cùng đối tượng. ") + ("Đã xác nhận trên vCenter." if x["acknowledged"] else "Chưa xác nhận."),
                                       x.get("at"), page="infra-alarm", query=f"alarm={quote(x['vcenterKey'] + '|' + x['key'])}")
                                for x in by_entity.get((a["vcenterKey"], a["entityType"], a["entity"]), [])]
                        + [_event(i.get("state") == "red" and "crit" or "bad", f"Cảm biến {i.get('name')}",
                                  f"{i.get('kind') or ''}{(' · ' + str(i['reading'])) if i.get('reading') is not None else ''}".strip(" ·"),
                                  page="infra-health", query=f"host={quote(a['entity'])}")
                           for i in (sensors.get((a["vcenterKey"], a["entity"])) or {}).get("issues", []) if a["entityType"] == "host"],
                        links=([{"label": "Mở trên vSphere Client", "href": a["vsphereUrl"]}] if a.get("vsphereUrl") else [])
                        + _entity_link(a["entityType"], a["entity"], a["vcenterKey"])
                        + ([{"label": f"Datastore {a['entity']}", "page": "infra-ds", "query": f"q={quote(a['entity'])}"}]
                           if a["entityType"] == "datastore" else [])
                        + ([{"label": f"Sức khoẻ phần cứng {a['entity']}", "page": "infra-health", "query": f"host={quote(a['entity'])}"}]
                           if a["entityType"] == "host" else [])))
    return out


def health_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for h in infra.host_health(db):
        if h["level"] == "ok" or h["maintenance"]:
            continue
        out.append(item("health", h["level"], _key("hh", h["vcenterKey"], h["host"], h["level"]),
                        f"Host {h['host']}: phần cứng {infra.LEVEL_TEXT[h['level']].lower()}",
                        "; ".join(h["reasons"][:4]) or "Trạng thái tổng của host không xanh.",
                        page="infra-health", query=f"host={quote(h['host'])}", page_label="Xem sức khoẻ host này",
                        alert_key=_key("hh", h["vcenterKey"], h["host"]), ci={"type": "hypervisor_host", "key": vc_key(h["vcenterKey"], h["host"])},
                        facts=_facts(_fact("vCenter", h["vcenterKey"]), _fact("Cluster", h.get("cluster")),
                                     _fact("Kết nối", h.get("connection")), _fact("Trạng thái tổng", h.get("overallStatus"))),
                        events=([_event("crit", f"Host {h['connection']} với vCenter")] if h["connection"] != "connected" else [])
                        + [_event(i.get("state") == "red" and "crit" or "bad", f"{i.get('name')}",
                                  f"{i.get('kind') or ''}{(' · ' + str(i['reading'])) if i.get('reading') is not None else ''}".strip(" ·"))
                           for i in h["issues"]],
                        links=_entity_link("host", h["host"], h["vcenterKey"])))
    return out


def config_items(db: Session) -> List[Dict[str, Any]]:
    """Baseline deviations (G9a): one item per host / cluster deviation; VM deviations grouped per rule and cluster."""
    from .baseline import service as baseline
    rep = baseline.report(db)
    out, vm_groups = [], {}
    for f in rep["findings"]:
        if f["scope"] == "vm":
            vm_groups.setdefault((f["vcenterKey"], f["cluster"] or "", f["rule"]), []).append(f)
            continue
        ci = {"type": "hypervisor_host" if f["scope"] == "host" else "cluster", "key": vc_key(f["vcenterKey"], f["entity"])}
        out.append(item("config", f["level"], _key("cfg", f["code"], f["level"]), f"{f['scope'] == 'host' and 'Host' or 'Cluster'} {f['entity']}: chưa đạt “{f['label']}”",
                        f"{f['detail']} · mã {f['code']}", page="infra-config", query=f"q={quote(f['entity'])}", page_label="Xem lệch cấu hình",
                        alert_key=_key("cfg", f["code"]), ci=ci,
                        facts=_facts(_fact("Mã", f["code"]), _fact("vCenter", f["vcenterKey"]), _fact("Cluster", f.get("cluster"))),
                        links=_entity_link(f["scope"], f["entity"], f["vcenterKey"])))
    for (vc, cluster, rule), rows in vm_groups.items():
        lv = rows[0]["level"]
        names = ", ".join(f"{r['entity']}{' (' + r['ip'] + ')' if r.get('ip') else ''}" for r in rows[:8]) + (" …" if len(rows) > 8 else "")
        out.append(item("config", lv, _key("cfg", vc, cluster, rule, lv), f"{len(rows)} VM ở cluster {cluster or '—'}: chưa đạt “{rows[0]['label']}”",
                        names, page="infra-config", query=f"rule={quote(rule)}&q={quote(cluster)}", page_label="Xem lệch cấu hình",
                        alert_key=_key("cfg", vc, cluster, rule),
                        ci={"type": "cluster", "key": vc_key(vc, cluster)} if cluster else None,
                        events=[_event(lv, f"{r['entity']}", f"{r['detail']} · mã {r['code']}", entity={"type": "vm", "name": r["entity"], "vc": vc})
                                for r in rows]))
    return out


def lifecycle_items(db: Session) -> List[Dict[str, Any]]:
    """G9b: versions past / near End of General Support (grouped per product line), open VMSAs (one per advisory), TLS certificates
    expiring (one per endpoint). Unreadable certificates and versions without an EoGS row stay on the page only."""
    from .lifecycle import service as lifecycle
    rep = lifecycle.report(db)
    out = []
    lines: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in rep["lifecycle"]:
        if r["level"] in ("crit", "bad", "warn"):
            lines.setdefault((r["product"], r["line"], r["level"]), []).append(r)
    for (product, line, lv), rows in lines.items():
        r0 = rows[0]
        when = "đã hết hỗ trợ" if r0["daysLeft"] < 0 else f"hết hỗ trợ sau {r0['daysLeft']} ngày"
        out.append(item("lifecycle", lv, _key("lc", "eogs", product, line, lv), f"{len(rows)} {r0['productLabel']} {line} {when} ({'/'.join(reversed(r0['eogs'].split('-')))})",
                        ", ".join(x["name"] for x in rows[:10]) + (" …" if len(rows) > 10 else ""), page="infra-lifecycle",
                        page_label="Xem vòng đời", alert_key=_key("lc", "eogs", product, line),
                        events=[_event(lv, x["name"], f"{x['productLabel']} {x['version']} build {x['build']}", entity={"type": "host", "name": x["name"], "vc": x["vcenterKey"]} if product == "esxi" else None)
                                for x in rows]))
    adv: Dict[str, List[Dict[str, Any]]] = {}
    for r in rep["vmsa"]:
        adv.setdefault(r["vmsa"], []).append(r)
    for vmsa, rows in adv.items():
        r0 = max(rows, key=lambda x: x["cvss"])
        out.append(item("lifecycle", r0["level"], _key("lc", "vmsa", vmsa, r0["level"]), f"{vmsa} (CVSS {r0['cvss']:g}) chưa vá trên {len(rows)} đối tượng",
                        f"{r0.get('title') or ''} · " + ", ".join(f"{x['name']} (build {x['build']} < {x['fixedBuild']})" for x in rows[:8]),
                        page="infra-lifecycle", query=f"q={quote(vmsa)}", page_label="Xem VMSA", alert_key=_key("lc", "vmsa", vmsa),
                        events=[_event(x["level"], x["name"], f"{x['productLabel']} {x['version']} build {x['build']}, cần ≥ {x['fixedBuild']}") for x in rows]))
    for c in rep["certs"]:
        if c["level"] not in ("crit", "bad", "warn"):
            continue
        when = "đã hết hạn" if c["daysLeft"] < 0 else f"hết hạn sau {c['daysLeft']} ngày"
        out.append(item("lifecycle", c["level"], _key("lc", "tls", c["target"], c["level"]), f"Chứng chỉ {c['kindLabel']} {c['name']} {when}",
                        f"{c['host']}:{c['port']} · {c.get('issuer') or ''}{' · tự ký' if c.get('selfSigned') else ''}",
                        page="infra-lifecycle", query=f"q={quote(c['host'])}", page_label="Xem chứng chỉ", alert_key=_key("lc", "tls", c["target"]),
                        ci={"type": "hypervisor_host", "key": vc_key(c["vcenterKey"], c["host"])} if c["kind"] == "esxi" else None,
                        facts=_facts(_fact("Hết hạn", c["notAfter"], "datetime"), _fact("Subject", c.get("subject")), _fact("Issuer", c.get("issuer")))))
    return out


def contention_items(db: Session) -> List[Dict[str, Any]]:
    """G9c: clusters where ≥ 20 % of the VMs are contended (one item each, the VMs as events), then each VM / host at CẢNH BÁO
    that is not already inside a cluster item."""
    from .contention import service as contention
    rep = contention.report(db)
    out, covered = [], set()
    for c in rep["clusters"]:
        if c["level"] == "ok":
            continue
        rows = [v for v in rep["vms"] if v["vcenterKey"] == c["vcenterKey"] and (v.get("cluster") or "") == c["cluster"] and v["level"] != "ok"]
        covered |= {(v["vcenterKey"], v["name"]) for v in rows}
        out.append(item("contention", c["level"], _key("ct", "cl", c["vcenterKey"], c["cluster"], c["level"]),
                        f"Cluster {c['cluster']}: {c['hot']}/{c['vms']} VM bị nghẽn ({_num(c['sharePct'])} %)",
                        f"CPU ready trung bình {_num(c['readyAvg'], 2)} %. VM nghẽn: " + ", ".join(v["name"] for v in rows[:8]) + (" …" if len(rows) > 8 else ""),
                        page="cap-contention", query=f"q={quote(c['cluster'])}", page_label="Xem nghẽn tài nguyên",
                        alert_key=_key("ct", "cl", c["vcenterKey"], c["cluster"]), ci={"type": "cluster", "key": vc_key(c["vcenterKey"], c["cluster"])},
                        events=[_event(v["level"], f"{v['name']}{' (' + v['ip'] + ')' if v.get('ip') else ''}", "; ".join(v["reasons"]),
                                       entity={"type": "vm", "name": v["name"], "vc": v["vcenterKey"]}) for v in rows]))
    for r in rep["vms"] + rep["hosts"]:
        if r["level"] != "bad" or (r["vcenterKey"], r["name"]) in covered:
            continue
        what = "VM" if r["kind"] == "vm" else "Host"
        out.append(item("contention", "bad", _key("ct", r["kind"], r["vcenterKey"], r["name"]), f"{what} {r['name']}: nghẽn tài nguyên",
                        "; ".join(r["reasons"]), page="cap-contention", query=f"q={quote(r['name'])}", page_label="Xem nghẽn tài nguyên",
                        alert_key=_key("ct", r["kind"], r["vcenterKey"], r["name"]),
                        ci={"type": "vm", "name": r["name"], "vc": r["vcenterKey"]} if r["kind"] == "vm"
                        else {"type": "hypervisor_host", "key": vc_key(r["vcenterKey"], r["name"])},
                        facts=_facts(_fact("IP", r.get("ip")), _fact("Cluster", r.get("cluster")), _fact("vCenter", r["vcenterKey"])),
                        links=_entity_link(r["kind"], r["name"], r["vcenterKey"])))
    return out


def storage_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for a in infra.storage(db)["arrays"]:
        if a["level"] == "ok":
            continue
        out.append(item("storage", a["level"], _key("st", a["sourceKey"], a["level"]),
                        f"Storage {a['name']}{(' (' + a['model'] + ')') if a.get('model') else ''}: {infra.LEVEL_TEXT[a['level']].lower()}",
                        "; ".join(a["reasons"][:4]) + ".", page="infra-storage", query=f"array={quote(a['sourceKey'])}",
                        page_label="Xem mảng này", alert_key=_key("st", a["sourceKey"]), ci={"type": "storage_array", "key": a["sourceKey"]},
                        facts=_facts(_fact("Hãng · model", " · ".join(x for x in (a.get("vendor"), a.get("model")) if x)),
                                     _fact("Phiên bản", a.get("version")), _fact("Đã dùng", _pct(a.get("usedPct"))),
                                     _fact("Độ trễ đọc / ghi", f"{_num(a.get('latencyReadMs'), 2)} / {_num(a.get('latencyWriteMs'), 2)} ms"
                                           if a.get("latencyReadMs") is not None else None),
                                     _fact("Lỗi đọc gần nhất", a.get("lastError")), _fact("Đọc được lần cuối", a.get("lastOkAt"), "datetime")),
                        events=[_event(SEVERITY_LEVEL.get(str(x.get("severity") or "").lower(), "warn"),
                                       x.get("summary") or "Alert", f"{(x.get('severity') or '').upper()}{(' · ' + x['component']) if x.get('component') else ''}"
                                       f"{(' · #' + str(x['id'])) if x.get('id') else ''}", x.get("at"))
                                for x in sorted(a.get("alerts") or [], key=lambda x: (LEVEL_RANK.get(SEVERITY_LEVEL.get(str(x.get("severity") or "").lower(), "warn"), 9),
                                                                                   -(dt.datetime.fromisoformat(x["at"].replace("Z", "+00:00")).timestamp() if x.get("at") else 0)))]
                        + [_event("bad", str(hw.get("name") or hw.get("component") or hw) if isinstance(hw, dict) else str(hw),
                                  str(hw.get("status") or hw.get("detail") or "") if isinstance(hw, dict) else "")
                           for hw in a.get("hardwareIssues") or []]))
    return out


def asset_items(db: Session, today: dt.date) -> List[Dict[str, Any]]:
    """Grouped by level (a Data Center holds hundreds of devices): ≥ 5 years without HW warranty, ≥ 5 years,
    reaching 5 years within the warning window."""
    from . import dc_assets
    from .config import get_settings
    s = get_settings()
    data = dc_assets.listing(db, today, s.asset_age_limit_months, s.asset_age_warn_months, False)
    by_level: Dict[str, List[Dict[str, Any]]] = {"crit": [], "bad": [], "warn": []}
    for a in data["items"]:
        if not a["removed"] and a["level"] in by_level:
            by_level[a["level"]].append(a)
    limit = dc_assets.age_text(s.asset_age_limit_months)
    titles = {"crit": f"{{n}} thiết bị Data Center dùng từ {limit}, không còn bảo hành phần cứng",
              "bad": f"{{n}} thiết bị Data Center dùng từ {limit}",
              "warn": f"{{n}} thiết bị Data Center sắp đủ {limit} hoặc hết bảo hành / license"}
    out = []
    for level, rows in by_level.items():
        if not rows:
            continue
        names = ", ".join(r["deviceId"] or r["assetTag"] or r["serial"] or "?" for r in rows[:6])
        more = f" và {len(rows) - 6} thiết bị khác" if len(rows) > 6 else ""
        out.append(item("asset", level, _key("as", level, len(rows)), titles[level].format(n=vn_g(len(rows))),
                        f"{names}{more}. Đưa vào Kế hoạch năm sau → Thay thế thiết bị.", page="dc-assets",
                        query=f"level={level}", alert_key=_key("as", level)))
    out.extend(warranty_items(db, today))
    return out


WARRANTY_TITLES = {
    "no_opex": "{n} máy chủ có bảo hành Dell nhưng chưa có Opex bảo hành",
    "opex_before_dell": "{n} máy chủ có Opex bảo hành hết hạn trước bảo hành Dell",
    "dell_expired_opex_active": "{n} máy chủ hết bảo hành Dell nhưng Opex bảo hành còn hiệu lực",
    "expired_critical": "{n} máy chủ hết bảo hành đang chạy VM của hệ thống quan trọng (mức 1–2)",
}
WARRANTY_ACTIONS = {
    "no_opex": "Bổ sung service tag vào Opex bảo hành, hoặc ghi nhận bảo hành đi kèm máy.",
    "opex_before_dell": "Kiểm tra lại ngày Opex / có cần gia hạn khi Dell còn bảo hành.",
    "dell_expired_opex_active": "Kiểm tra Opex có đúng máy (bảo hành bên thứ ba?) hay danh sách SN sai.",
    "expired_critical": "Gia hạn bảo hành hoặc chuyển VM — dòng gia hạn có ở Kế hoạch năm sau → Bảo hành Dell.",
}


def warranty_items(db: Session, today: dt.date) -> List[Dict[str, Any]]:
    """Dell warranty (OME) ↔ Opex reconciliation (hardware/warranty.py), one item per case with its codes."""
    from .hardware import warranty
    by_case: Dict[str, List[Dict[str, Any]]] = {}
    for f in warranty.reconcile(db, today):
        by_case.setdefault(f["case"], []).append(f)
    out = []
    for case, rows in by_case.items():
        level = warranty.CASES[case][2]
        refs = ", ".join(f"{r['code']} {r['serviceTag']}" for r in rows[:6]) + (f" và {len(rows) - 6} máy khác" if len(rows) > 6 else "")
        out.append(item("asset", level, _key("aw", case, len(rows)), WARRANTY_TITLES[case].format(n=vn_g(len(rows))),
                        f"Đối soát: {refs}. {WARRANTY_ACTIONS[case]}", page="dc-assets", query=f"warranty={case}",
                        alert_key=_key("aw", case)))
    return out


def hardware_items(db: Session) -> List[Dict[str, Any]]:
    """Dell OME (G4b): servers with a failed / degraded part, disks predicting failure (VƯỢT NGƯỠNG), firmware
    behind the baseline (THEO DÕI, grouped)."""
    from .hardware.models import HwAlert, HwComponentHealth, HwDeviceState, HwDiskState, HwFirmwareState, HwPartState
    from .config import get_settings
    keys = [c.key for c in get_settings().hardware()]
    if not keys:
        return []
    devs = {(d.source_key, d.device_id): d for d in db.query(HwDeviceState).filter(HwDeviceState.source_key.in_(keys))}
    if not devs:
        return []

    from .shared.identity import norm_serial

    def link(d) -> Dict[str, Any]:            # Phần cứng → this server (#/hardware?s=&d=), card of the physical server
        return {"ci": {"type": "physical_server", "key": norm_serial(d.service_tag)} if d.service_tag else None, "page": "hardware", "query": f"s={quote(d.source_key)}&d={d.device_id}", "page_label": "Xem máy chủ này",
                "links": ([{"label": f"Thẻ máy chủ {d.service_tag}", "page": "entity", "query": f"type=server&name={quote(d.service_tag)}"}]
                          if d.service_tag else []),
                "facts": _facts(_fact("Model", d.model), _fact("Service tag", d.service_tag), _fact("iDRAC", d.idrac_ip),
                                _fact("Nguồn", d.power_state), _fact("Mã trạng thái OME", d.status_code))}
    reasons: Dict[Tuple[str, int], List[str]] = {}
    evs: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for h in db.query(HwComponentHealth).filter(HwComponentHealth.source_key.in_(keys), HwComponentHealth.status.in_(("bad", "crit"))):
        reasons.setdefault((h.source_key, h.device_id), []).append(f"{h.subsystem}: {h.detail or h.status}")
        evs.setdefault((h.source_key, h.device_id), []).append(_event(h.status, f"Nhóm {h.subsystem}", h.detail or f"Mã OME {h.status_code or '—'}"))
    for p in db.query(HwPartState).filter(HwPartState.source_key.in_(keys), HwPartState.status.in_(("bad", "crit"))):
        reasons.setdefault((p.source_key, p.device_id), []).append(f"{p.name}{(' (' + p.model + ')') if p.model else ''} lỗi")
        evs.setdefault((p.source_key, p.device_id), []).append(
            _event(p.status, f"{p.name}{(' (' + p.model + ')') if p.model else ''}",
                   " · ".join(x for x in (p.detail, f"SN {p.serial}" if p.serial else "") if x)))
    since = dt.datetime.utcnow() - dt.timedelta(days=7)
    for al in (db.query(HwAlert).filter(HwAlert.source_key.in_(keys), HwAlert.at >= since, HwAlert.severity.in_(("warn", "bad", "crit")))
               .order_by(HwAlert.at.desc()).limit(500)):
        evs.setdefault((al.source_key, al.device_id), []).append(
            _event(al.severity, f"{al.message_id or 'Alert'} · {al.category or ''}".strip(" ·"), al.message or "",
                   al.at.isoformat() + "Z" if al.at else None))
    out = []
    for k, d in devs.items():
        level = d.status if d.status in ("bad", "crit") else ("bad" if k in reasons else None)
        if level is None:
            continue
        out.append(item("hardware", level, _key("hw", d.source_key, d.device_id, level),
                        f"Máy chủ {d.name} ({d.service_tag or d.device_id}): linh kiện {'hỏng' if level == 'crit' else 'cảnh báo'}",
                        "; ".join(dict.fromkeys(reasons.get(k) or [f"Trạng thái OME {d.status_code or '—'}"]))[:500] + ".",
                        alert_key=_key("hw", d.source_key, d.device_id), events=evs.get(k, []), **link(d)))
    for x in db.query(HwDiskState).filter(HwDiskState.source_key.in_(keys), HwDiskState.predictive_failure.is_(True)):
        d = devs.get((x.source_key, x.device_id))
        if d is None:
            continue
        out.append(item("hardware", "crit", _key("hwd", x.source_key, x.device_id, x.slot),
                        f"Ổ đĩa dự báo hỏng — {d.name} ({d.service_tag or d.device_id})",
                        f"{x.slot}{(' · ' + x.model) if x.model else ''}{(' · SN ' + x.serial) if x.serial else ''}. Thay ổ trước khi hỏng hẳn.",
                        events=[_event("crit", f"Ổ {x.slot}: dự báo hỏng (predictive failure)",
                                       " · ".join(v for v in (x.model, x.media, f"{_num(x.size_gb, 0)} GB" if x.size_gb else "", f"SN {x.serial}" if x.serial else "") if v))]
                        + evs.get((x.source_key, x.device_id), []), **link(d)))
    behind = {(f.source_key, f.device_id) for f in db.query(HwFirmwareState.source_key, HwFirmwareState.device_id)
              .filter(HwFirmwareState.source_key.in_(keys), HwFirmwareState.compliance.in_(("upgrade", "downgrade")))}
    behind &= set(devs)
    if behind:
        names = sorted(devs[k].name for k in behind)
        out.append(item("hardware", "warn", _key("hwf", len(behind)), f"{vn_g(len(behind))} máy chủ có firmware lệch baseline OME",
                        ", ".join(names[:6]) + (f" và {len(names) - 6} máy khác" if len(names) > 6 else "") + ".",
                        page="set-sources", alert_key="hwf"))
    return out


def review_items(db: Session, today: dt.date) -> List[Dict[str, Any]]:
    """Owner reviews (G3b): undecided VMs of closed periods (team hạ tầng decides), VMs to re-assign
    (chuyển owner / không phải của tôi), "Thu hồi" past its date while the VM is still active (chưa dọn)."""
    from .owners import review
    from .owners.models import ReviewCampaign, ReviewItem
    out = []
    for c, n in (db.query(ReviewCampaign, func.count(ReviewItem.id)).join(ReviewItem, ReviewItem.campaign_id == ReviewCampaign.id)
                 .filter(ReviewCampaign.status == "closed", ReviewItem.owner_email.isnot(None), ReviewItem.decision.is_(None))
                 .group_by(ReviewCampaign.id).all()):
        out.append(item("review", "task", _key("rv-undecided", c.id, n), f"Kỳ rà soát {c.period_label}: {vn_g(n)} VM chờ team hạ tầng xác nhận",
                        "Owner chưa quyết định trước khi kỳ đóng. Mở Cài đặt → Rà soát owner → Kết quả để quyết định thay.",
                        page="set-reviews", role="admin", alert_key=_key("rv-undecided", c.id)))
    q = db.query(func.count(ReviewItem.id)).filter(ReviewItem.decision.in_(("transfer", "not_mine")), ReviewItem.resolved_at.is_(None)).scalar() or 0
    if q:
        out.append(item("review", "task", _key("rv-queue", q), f"{vn_g(q)} VM cần gán lại owner sau rà soát",
                        "Owner báo chuyển owner / không phải của mình. Gán lại ở Hệ thống & Owner rồi đánh dấu đã xử lý.",
                        page="set-reviews", role="admin", alert_key="rv-queue"))
    left = review.not_cleaned(db, today)
    if left:
        names = ", ".join(i.vm_name for i in left[:6]) + (f" và {len(left) - 6} VM khác" if len(left) > 6 else "")
        out.append(item("review", "bad", _key("rv-left", len(left)), f"{vn_g(len(left))} VM đã quyết định thu hồi nhưng chưa dọn",
                        f"Quá ngày thu hồi dự kiến mà VM vẫn còn trong vCenter: {names}.", page="set-reviews", role="admin",
                        alert_key="rv-left"))
    return out


PROVIDERS: List[Tuple[str, Callable[..., List[Dict[str, Any]]], bool]] = [
    ("capacity", capacity_items, False), ("backup", backup_items, False),
    ("datastore", datastore_items, False), ("snapshot", snapshot_items, False),
    ("alarm", alarm_items, False), ("health", health_items, False), ("storage", storage_items, False), ("asset", asset_items, True),
    ("license", license_items, True), ("spend", spend_items, True), ("system", system_items, False),
    ("review", review_items, True), ("hardware", hardware_items, False), ("config", config_items, False),
    ("lifecycle", lifecycle_items, False), ("contention", contention_items, False),
]

_cache: Dict[str, Any] = {"at": 0.0, "items": None, "errors": []}
_lock = threading.Lock()
_compute_lock = threading.Lock()
MIN_REFRESH_SECONDS = 10


def collect(db: Session, today: Optional[dt.date] = None, use_cache: bool = True) -> Tuple[List[Dict[str, Any]], List[str]]:
    """All items of every module (unfiltered), cached for CACHE_SECONDS. A failing provider is
    reported, never blocks the others."""
    from .spend.fiscal import today_vn
    with _lock:
        age = time.monotonic() - _cache["at"]
        # A forced refresh is ignored while the cache is younger than MIN_REFRESH_SECONDS.
        if _cache["items"] is not None and (age < MIN_REFRESH_SECONDS or (use_cache and age < CACHE_SECONDS)):
            return _cache["items"], _cache["errors"]
    with _compute_lock:                  # single flight: concurrent callers wait for one recompute
        with _lock:
            if _cache["items"] is not None and time.monotonic() - _cache["at"] < MIN_REFRESH_SECONDS:
                return _cache["items"], _cache["errors"]
        items, errors = _compute(db, today or today_vn())
        return _capped(items), errors


def _compute(db: Session, today: dt.date) -> Tuple[List[Dict[str, Any]], List[str]]:
    items, errors = [], []
    for name, fn, wants_today in PROVIDERS:
        # Each provider runs in a SAVEPOINT: on PostgreSQL a failed SQL statement aborts the whole
        # transaction, so without it one broken module would fail every later one (and the Discord
        # 90 % alerts that reuse this session) — BEQ-02.
        sp = db.begin_nested()
        try:
            items.extend(fn(db, today) if wants_today else fn(db))
            sp.commit()
        except Exception:  # noqa: BLE001 — one module down must not empty the home page
            sp.rollback()
            logger.exception("Inbox provider %s failed", name)
            errors.append(name)
    items.sort(key=lambda x: (LEVEL_RANK.get(x["level"], 9), x["due"] or dt.date.max, x["title"]))
    with _lock:
        _cache.update(at=time.monotonic(), items=_capped(items), errors=errors)
    return items, errors


def _capped(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What people see (collect): the worst MAX_PER_SOURCE items per source. _compute returns every item, so the Discord
    alerts and the event store never take an item cut from the list for a cleared one."""
    seen: Dict[str, int] = {}
    out = []
    for it in items:
        seen[it["source"]] = seen.get(it["source"], 0) + 1
        if seen[it["source"]] <= MAX_PER_SOURCE:
            out.append(it)
    return out


def invalidate() -> None:
    with _lock:
        _cache.update(items=None)


def visible(it: Dict[str, Any], user, features: Dict[str, bool]) -> bool:
    from .security import is_admin
    from .spend.roles import has_role
    if it.get("feature") and features.get(it["feature"]) is False:
        return False
    role = it.get("role")
    if role == "admin":
        return is_admin(user)
    if role and role.startswith("spend:"):
        return has_role(user, role.split(":", 1)[1])
    return True
