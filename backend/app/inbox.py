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

logger = logging.getLogger(__name__)

LEVELS = ("crit", "bad", "warn", "task", "info")
LEVEL_RANK = {k: i for i, k in enumerate(LEVELS)}
LEVEL_LABELS = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "task": "Chờ xử lý", "info": "Thông tin"}
SOURCE_LABELS = {"capacity": "Capacity", "backup": "Backup", "license": "License", "spend": "Chi phí & hợp đồng", "system": "Hệ thống"}
CACHE_SECONDS = 60
MAX_PER_SOURCE = 60
PUSH_LEVEL_PCT = 90.0          # decision 5: Discord alert at the 90 % level


def _key(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return raw if len(raw) <= 200 else raw[:150] + "#" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def item(source: str, level: str, key: str, title: str, detail: str = "", page: str = "", query: str = "",
         due: Optional[dt.date] = None, feature: Optional[str] = None, role: Optional[str] = None,
         push: bool = False) -> Dict[str, Any]:
    """role: None (any signed-in user) | 'spend:viewer|editor|approver' | 'admin'."""
    return {"key": key[:255], "source": source, "level": level, "title": title[:300], "detail": detail[:600], "page": page,
            "query": query, "due": due, "feature": feature, "role": role, "push": push}


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
                        page="dashboard", push=peak >= PUSH_LEVEL_PCT))
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
                        f"License {e.product} ({e.quantity:g} {e.metric}) {'đã hết hạn' if days < 0 else f'hết hạn sau {days} ngày'}",
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


PROVIDERS: List[Tuple[str, Callable[..., List[Dict[str, Any]]], bool]] = [
    ("capacity", capacity_items, False), ("backup", backup_items, False),
    ("license", license_items, True), ("spend", spend_items, True), ("system", system_items, False),
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
        try:
            items.extend(fn(db, today) if wants_today else fn(db))
        except Exception:  # noqa: BLE001 — one module down must not empty the home page
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
