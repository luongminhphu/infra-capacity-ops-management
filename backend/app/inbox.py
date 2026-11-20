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
from .shared.text import vn_g

logger = logging.getLogger(__name__)

LEVELS = ("crit", "bad", "warn", "task", "info")
LEVEL_RANK = {k: i for i, k in enumerate(LEVELS)}
LEVEL_LABELS = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "task": "Chờ xử lý", "info": "Thông tin"}
SOURCE_LABELS = {"capacity": "Capacity", "backup": "Backup", "datastore": "Datastore", "snapshot": "Snapshot VM",
                 "alarm": "Alarm vCenter", "health": "Sức khoẻ host", "storage": "Storage", "asset": "Thiết bị Data Center", "license": "License", "spend": "Chi phí & hợp đồng",
                 "system": "Hệ thống", "review": "Rà soát owner", "hardware": "Phần cứng máy chủ"}
CACHE_SECONDS = 60
MAX_PER_SOURCE = 60
PUSH_LEVEL_PCT = 90.0          # decision 5: Discord alert at the 90 % level


def _key(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return raw if len(raw) <= 200 else raw[:150] + "#" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def item(source: str, level: str, key: str, title: str, detail: str = "", page: str = "", query: str = "",
         due: Optional[dt.date] = None, feature: Optional[str] = None, role: Optional[str] = None,
         push: bool = False, alert_key: Optional[str] = None) -> Dict[str, Any]:
    """role: None (any signed-in user) | 'spend:viewer|editor|approver' | 'admin'.
    alert_key: identity for Discord alerts when it must not change with the level (the inbox key does
    change, so a worsened item reappears after "Xong"); defaults to the key."""
    return {"key": key[:255], "source": source, "level": level, "title": title[:300], "detail": detail[:600], "page": page,
            "query": query, "due": due, "feature": feature, "role": role, "push": push,
            "alertKey": (alert_key or key)[:255]}


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
                        alert_key=_key("cap", r["name"])))      # BEQ-04: one alert per cluster, whatever the level
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
    for f in (rep.get("findings") or [])[:MAX_PER_SOURCE]:
        level = sev.get(f.get("severity"))
        if not level:
            continue
        out.append(item("backup", level, _key("bkf", f.get("area"), f.get("entity"), f.get("title")),
                        f"{f.get('title') or ''}{' — ' + f['entity'] if f.get('entity') else ''}",
                        f"{f.get('detail') or ''} {('→ ' + f['action']) if f.get('action') else ''}".strip(),
                        page="bk-overview", feature="backup"))
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
def datastore_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for d in infra.datastores(db)[:MAX_PER_SOURCE]:
        if d["level"] == "ok":
            continue
        out.append(item("datastore", d["level"], _key("ds", d["vcenterKey"], d["key"], d["level"]),
                        f"Datastore {d['name']}: dùng {_pct(d['usedPct'])}"
                        + (f" · đầy sau ~{d['daysToFull']:.0f} ngày" if d.get("daysToFull") is not None else ""),
                        "; ".join(d["reasons"]) + f". Cluster: {', '.join(d['clusters']) or '—'}.",
                        page="infra-ds", query=f"ds={quote(d['vcenterKey'] + ':' + d['key'])}",
                        alert_key=_key("ds", d["vcenterKey"], d["key"])))
    return out


def snapshot_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for s in infra.snapshots(db):
        if s["level"] in ("ok", "warn") or len(out) >= MAX_PER_SOURCE:      # watch-level snapshots: page only
            continue
        out.append(item("snapshot", s["level"], _key("snap", s["vcenterKey"], s["vm"], s["level"]),
                        f"VM {s['vm']}: {s['count']} snapshot, cũ nhất {s['ageDays']:.0f} ngày".replace(".", ","),
                        "; ".join(s["reasons"]) + ". Xoá / hợp nhất snapshot sau khi xác nhận với chủ VM.",
                        page="infra-snap", query=f"vm={quote(s['vm'])}", alert_key=_key("snap", s["vcenterKey"], s["vm"])))
    return out


def alarm_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for a in infra.alarms(db)[:MAX_PER_SOURCE]:
        if a["acknowledged"]:
            continue
        out.append(item("alarm", a["level"], _key("alarm", a["vcenterKey"], a["key"], a["status"]),
                        f"Alarm {a['alarm']} — {a['entity']}",
                        f"vCenter {a['vcenterKey']}, {a['entityType']}{(', cluster ' + a['cluster']) if a.get('cluster') else ''}.",
                        page="infra-alarm", alert_key=_key("alarm", a["vcenterKey"], a["key"])))
    return out


def health_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for h in infra.host_health(db)[:MAX_PER_SOURCE]:
        if h["level"] == "ok" or h["maintenance"]:
            continue
        out.append(item("health", h["level"], _key("hh", h["vcenterKey"], h["host"], h["level"]),
                        f"Host {h['host']}: phần cứng {infra.LEVEL_TEXT[h['level']].lower()}",
                        "; ".join(h["reasons"][:4]) or "Trạng thái tổng của host không xanh.",
                        page="infra-health", alert_key=_key("hh", h["vcenterKey"], h["host"])))
    return out


def storage_items(db: Session) -> List[Dict[str, Any]]:
    from . import infra
    out = []
    for a in infra.storage(db)["arrays"]:
        if a["level"] == "ok":
            continue
        out.append(item("storage", a["level"], _key("st", a["sourceKey"], a["level"]),
                        f"Storage {a['name']}{(' (' + a['model'] + ')') if a.get('model') else ''}: {infra.LEVEL_TEXT[a['level']].lower()}",
                        "; ".join(a["reasons"][:4]) + ".", page="infra-storage", alert_key=_key("st", a["sourceKey"])))
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
    from .hardware.models import HwComponentHealth, HwDeviceState, HwDiskState, HwFirmwareState, HwPartState
    from .config import get_settings
    keys = [c.key for c in get_settings().hardware()]
    if not keys:
        return []
    devs = {(d.source_key, d.device_id): d for d in db.query(HwDeviceState).filter(HwDeviceState.source_key.in_(keys))}
    if not devs:
        return []

    def link(d) -> Dict[str, str]:            # physical server card (CI registry) by service tag
        return {"page": "entity", "query": f"type=server&name={quote(d.service_tag)}"} if d.service_tag else {"page": "set-sources"}
    reasons: Dict[Tuple[str, int], List[str]] = {}
    for h in db.query(HwComponentHealth).filter(HwComponentHealth.source_key.in_(keys), HwComponentHealth.status.in_(("bad", "crit"))):
        reasons.setdefault((h.source_key, h.device_id), []).append(f"{h.subsystem}: {h.detail or h.status}")
    for p in db.query(HwPartState).filter(HwPartState.source_key.in_(keys), HwPartState.status.in_(("bad", "crit"))):
        reasons.setdefault((p.source_key, p.device_id), []).append(f"{p.name}{(' (' + p.model + ')') if p.model else ''} lỗi")
    out = []
    for k, d in devs.items():
        level = d.status if d.status in ("bad", "crit") else ("bad" if k in reasons else None)
        if level is None:
            continue
        out.append(item("hardware", level, _key("hw", d.source_key, d.device_id, level),
                        f"Máy chủ {d.name} ({d.service_tag or d.device_id}): linh kiện {'hỏng' if level == 'crit' else 'cảnh báo'}",
                        "; ".join(dict.fromkeys(reasons.get(k) or [f"Trạng thái OME {d.status_code or '—'}"]))[:500] + ".",
                        alert_key=_key("hw", d.source_key, d.device_id), **link(d)))
    for x in db.query(HwDiskState).filter(HwDiskState.source_key.in_(keys), HwDiskState.predictive_failure.is_(True)):
        d = devs.get((x.source_key, x.device_id))
        if d is None:
            continue
        out.append(item("hardware", "crit", _key("hwd", x.source_key, x.device_id, x.slot),
                        f"Ổ đĩa dự báo hỏng — {d.name} ({d.service_tag or d.device_id})",
                        f"{x.slot}{(' · ' + x.model) if x.model else ''}{(' · SN ' + x.serial) if x.serial else ''}. Thay ổ trước khi hỏng hẳn.",
                        **link(d)))
    behind = {(f.source_key, f.device_id) for f in db.query(HwFirmwareState.source_key, HwFirmwareState.device_id)
              .filter(HwFirmwareState.source_key.in_(keys), HwFirmwareState.compliance.in_(("upgrade", "downgrade")))}
    behind &= set(devs)
    if behind:
        names = sorted(devs[k].name for k in behind)
        out.append(item("hardware", "warn", _key("hwf", len(behind)), f"{vn_g(len(behind))} máy chủ có firmware lệch baseline OME",
                        ", ".join(names[:6]) + (f" và {len(names) - 6} máy khác" if len(names) > 6 else "") + ".",
                        page="set-sources", alert_key="hwf"))
    return out[:MAX_PER_SOURCE]


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
    ("review", review_items, True), ("hardware", hardware_items, False),
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
        return _compute(db, today or today_vn())


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
        _cache.update(at=time.monotonic(), items=items, errors=errors)
    return items, errors


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
