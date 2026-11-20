"""Owner review campaigns (ICO v2 G3a): every REVIEW_PERIOD_MONTHS a campaign lists every VM with its owner and the
cleanup signals; each owner gets ONE e-mail with their VMs. G3b adds decisions, reminders, escalation and closing.

Idempotency (what if it runs twice?):
* open_campaign: UNIQUE period_label — a second open of the same period (two pods, a retry, an admin click) fails
  and changes nothing; tick() only opens when the label is absent.
* review_notices is the outbox: one row per (campaign, recipient, kind), built in the same transaction as the items.
  deliver() claims a row (pending/failed → sending, its own commit) BEFORE the SMTP call, so two senders never send the
  same row; a crash after the claim leaves it "sending" and it is retried after CLAIM_TIMEOUT. SMTP errors are stored
  and retried next round, up to MAX_ATTEMPTS. dry_run campaigns keep the e-mails as "preview" and never send.
"""
import datetime as dt
import html
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cmdb.models import Ci
from ..config import get_settings
from ..models import AuditLog
from ..shared import mailer
from .models import ReviewCampaign, ReviewItem, ReviewNotice, System, SystemMember

logger = logging.getLogger("vco.owners.review")

MAX_ATTEMPTS = 5
CLAIM_TIMEOUT = dt.timedelta(minutes=30)
MAX_ROWS_IN_MAIL = 300
VN_OFFSET = dt.timedelta(hours=7)


class Exists(ValueError):
    """The period already has a campaign."""


# ---------------------------------------------------------------- calendar
def today_vn(now: Optional[dt.datetime] = None) -> dt.date:
    return ((now or dt.datetime.utcnow()) + VN_OFFSET).date()


def start_months() -> List[int]:
    out = sorted({int(x) for x in str(get_settings().review_start_months).split(",") if x.strip().isdigit() and 1 <= int(x) <= 12})
    return out or [1, 7]


def period_label(d: dt.date) -> str:
    """"2027-H1" / "2027-H2" for 6-month periods; "2027-P3" for other lengths (n-th period of the year)."""
    months = max(1, min(12, int(get_settings().review_period_months or 6)))
    n = (d.month - 1) // months + 1
    return f"{d.year}-H{n}" if months == 6 else f"{d.year}-P{n}"


# ---------------------------------------------------------------- signals
def _vn(v: Any, digits: int = 0) -> str:
    if v is None:
        return "—"
    s = f"{float(v):,.{digits}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _vm_rows(db: Session) -> Tuple[Dict[Tuple[str, str], dict], Dict[Tuple[str, str], dict]]:
    from .. import data_access
    _, vms, _ = data_access.get_latest_combined(db, None, include_import=False)
    by_name, by_uuid = {}, {}
    for v in vms:
        vc = v.get("vcenterKey") or ""
        by_name[(vc, (v.get("vm") or "").lower())] = v
        if v.get("instanceUuid"):
            by_uuid[(vc, str(v["instanceUuid"]).lower())] = v
    return by_name, by_uuid


def _backup_states(db: Session) -> Optional[Dict[Tuple[str, str], str]]:
    """(vcenterKey, vm) → protected | failing | unprotected; None when backup is not monitored."""
    from ..features import get_features, is_on
    if not get_settings().netbackups() or not is_on(get_features(db), "backup"):
        return None
    from ..backup.service import build_summary
    cov = (build_summary(db, "all", 7) or {}).get("coverage") or {}
    if not cov.get("available"):
        return None
    out = {}
    for state, key in (("unprotected", "unprotectedVms"), ("failing", "failingVms")):
        for r in cov.get(key) or []:
            out[((r.get("vcenterKey") or ""), (r.get("vm") or "").lower())] = state
    return out


def _snapshot_days(db: Session, today: dt.date) -> Dict[Tuple[str, str], float]:
    from ..models import VmSnapshotState
    now = dt.datetime.combine(today, dt.time()) - VN_OFFSET
    out = {}
    for vc, vm, oldest in db.query(VmSnapshotState.vcenter_key, VmSnapshotState.vm, VmSnapshotState.oldest_at):
        if oldest:
            out[(vc or "", (vm or "").lower())] = round(max(0.0, (now - oldest).total_seconds() / 86400), 1)
    return out


def signals_for(v: Optional[dict], backup: Optional[str], snap_days: Optional[float], last_decision: Optional[str],
                idle_days: int, snapshot_days: int) -> Tuple[Dict[str, Any], List[str]]:
    """Signals of one VM and the reasons it should be reviewed (thresholds: policy idle_days, REVIEW_SNAPSHOT_DAYS)."""
    v = v or {}
    sig = {"powerOffDays": v.get("powerOffDays"), "idleDays": v.get("lastActivityDays"), "cpuPct": v.get("cpuUsagePctRaw"),
           "ramPct": v.get("ramUsagePctRaw"), "vcpu": v.get("vcpu"), "ramGB": v.get("ramGB"), "diskProvGB": v.get("diskProvGB"),
           "poweredOn": v.get("isPoweredOn"), "backupCoverage": backup, "oldestSnapshotDays": snap_days,
           "osEolFlag": None, "lastReviewDecision": last_decision}
    reasons = []
    off, idle = sig["powerOffDays"], sig["idleDays"]
    if not v.get("isPoweredOn") and isinstance(off, (int, float)) and off >= idle_days:
        reasons.append(f"Tắt {_vn(off)} ngày")
    if v.get("isPoweredOn") and isinstance(idle, (int, float)) and idle >= idle_days:
        reasons.append(f"Không hoạt động {_vn(idle)} ngày")
    if backup == "unprotected" and v.get("isPoweredOn"):
        reasons.append("Chưa có backup")
    elif backup == "failing":
        reasons.append("Backup đang lỗi")
    if isinstance(snap_days, (int, float)) and snap_days >= snapshot_days:
        reasons.append(f"Snapshot {_vn(snap_days)} ngày")
    return sig, reasons


# ---------------------------------------------------------------- open
def open_campaign(db: Session, label: Optional[str] = None, due_days: Optional[int] = None, dry_run: Optional[bool] = None,
                  by: str = "system", now: Optional[dt.datetime] = None) -> ReviewCampaign:
    """Build the campaign, its items and the "open" e-mails in ONE transaction (commits). Raises Exists."""
    from .. import data_access
    s = get_settings()
    now = now or dt.datetime.utcnow()
    today = today_vn(now)
    label = (label or period_label(today))[:16]
    if db.query(ReviewCampaign.id).filter(ReviewCampaign.period_label == label).first():
        raise Exists(f"Kỳ {label} đã được mở.")
    if dry_run is None:      # the very first campaign is always a dry run (preview before anything is e-mailed)
        dry_run = True if not db.query(ReviewCampaign.id).first() else bool(s.review_dry_run)
    due = today + dt.timedelta(days=max(1, int(due_days if due_days is not None else s.review_due_days)))
    prev = db.query(ReviewCampaign).filter(ReviewCampaign.opened_at < now).order_by(ReviewCampaign.opened_at.desc()).first()
    last = {cid: d for cid, d in db.query(ReviewItem.ci_id, ReviewItem.decision).filter(ReviewItem.campaign_id == prev.id)} if prev else {}
    c = ReviewCampaign(period_label=label, opened_at=now, opened_by=by, due_at=due, status="open", dry_run=dry_run)
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Exists(f"Kỳ {label} đã được mở.") from None
    idle = int(data_access.get_effective_policy(db, s).idle_days)
    snap_limit = int(s.review_snapshot_days)
    by_name, by_uuid = _vm_rows(db)
    backup, snaps = _backup_states(db), _snapshot_days(db, today)
    members = {m.ci_id: sys_ for m, sys_ in db.query(SystemMember, System).join(System, System.id == SystemMember.system_id)
               .filter(System.status == "active")}
    from ..cmdb.models import CiKey
    uuids = dict(db.query(CiKey.ci_id, CiKey.value_norm).filter(CiKey.kind == "instance_uuid"))
    rows = []
    for ci in db.query(Ci).filter(Ci.type == "vm", Ci.status == "active").order_by(Ci.name, Ci.id):
        a = json.loads(ci.attrs or "{}")
        vc = a.get("vcenterKey") or ""
        v = by_uuid.get((vc, (uuids.get(ci.id) or "").lower())) or by_name.get((vc, ci.name.lower()))
        st = (backup or {}).get((vc, ci.name.lower()), "protected" if backup is not None and (v or {}).get("isPoweredOn") else None)
        sig, reasons = signals_for(v, st, snaps.get((vc, ci.name.lower())), last.get(ci.id), idle, snap_limit)
        sys_ = members.get(ci.id)
        rows.append({"campaign_id": c.id, "ci_id": ci.id, "vm_name": ci.name[:255], "vcenter_key": vc[:64],
                     "system_id": sys_.id if sys_ else None, "system_code": sys_.code if sys_ else None,
                     "system_name": sys_.name if sys_ else None, "owner_email": sys_.owner_email if sys_ else None,
                     "signals": json.dumps(sig, ensure_ascii=False, default=str), "flagged": bool(reasons),
                     "reasons": json.dumps(reasons, ensure_ascii=False), "decide_by_date": due})
    if rows:
        db.bulk_insert_mappings(ReviewItem, rows)
    c.item_count = len(rows)
    by_owner: Dict[str, List[dict]] = {}
    for r in rows:
        if r["owner_email"]:
            by_owner.setdefault(r["owner_email"], []).append(r)
    for email, items in sorted(by_owner.items()):
        subject, text, body = build_open_email(c, items)
        db.add(ReviewNotice(campaign_id=c.id, recipient=email, kind="open", status="preview" if dry_run else "pending",
                            subject=subject[:255], body_text=text, body_html=body, vm_count=len(items), created_at=now))
    db.add(AuditLog(username=by, action="owners.review.open",
                    detail=json.dumps({"period": label, "items": len(rows), "owners": len(by_owner), "dryRun": dry_run})))
    db.commit()
    return c


# ---------------------------------------------------------------- e-mail
def portal_link(campaign_id: int) -> str:
    base = get_settings().public_base()
    return f"{base}/#/owner/review/{campaign_id}" if base else ""


def build_open_email(c: ReviewCampaign, items: List[dict]) -> Tuple[str, str, str]:
    """(subject, text, html) of the e-mail of one owner. Every value from vCenter / users is HTML-escaped; no login token."""
    e = html.escape
    due = c.due_at.strftime("%d/%m/%Y")
    items = sorted(items, key=lambda r: (not r["flagged"], r["system_code"] or "", r["vm_name"].lower()))
    flagged = sum(1 for r in items if r["flagged"])
    sig = [json.loads(r["signals"]) for r in items]
    tot = {k: sum(float(s.get(k) or 0) for s in sig) for k in ("vcpu", "ramGB", "diskProvGB")}
    subject = f"[ICO] Rà soát hệ thống {c.period_label} — {len(items)} VM cần xác nhận trước {due}"
    link = portal_link(c.id)
    intro = (f"ICO mở kỳ rà soát {c.period_label}. Bạn là owner của {len(items)} VM dưới đây"
             f"{f', trong đó {flagged} VM nên rà (tắt / không hoạt động lâu, chưa backup, snapshot cũ)' if flagged else ''}. "
             f"Đề nghị xác nhận từng VM (còn dùng, thu hồi, giảm cấu hình, chuyển owner) trước {due}.")
    text = [intro, "", f"Tổng: {_vn(tot['vcpu'])} vCPU · {_vn(tot['ramGB'])} GB RAM · {_vn(tot['diskProvGB'])} GB disk", ""]
    for r, s in zip(items[:MAX_ROWS_IN_MAIL], sig[:MAX_ROWS_IN_MAIL]):
        why = "; ".join(json.loads(r["reasons"]))
        text.append(f"- {r['vm_name']} ({r['system_code'] or '—'}): {_vn(s.get('vcpu'))} vCPU, {_vn(s.get('ramGB'))} GB RAM"
                    + (f" — NÊN RÀ: {why}" if why else ""))
    if len(items) > MAX_ROWS_IN_MAIL:
        text.append(f"… và {len(items) - MAX_ROWS_IN_MAIL} VM khác (xem trên ICO).")
    text += ["", f"Xác nhận trên ICO: {link}" if link else "Xác nhận trên ICO: đăng nhập ICO bằng tài khoản Microsoft → Cổng Owner.",
             "Email này không chứa liên kết đăng nhập; ICO yêu cầu đăng nhập bằng tài khoản công ty."]
    td = "padding:6px 8px;border-bottom:1px solid #CED5DE;vertical-align:top"
    rows = "".join(
        f"<tr><td style='{td}'><b>{e(r['vm_name'])}</b><br><span style='color:#465F83'>{e(r['vcenter_key'])}</span></td>"
        f"<td style='{td}'>{e(r['system_code'] or '—')}<br><span style='color:#465F83'>{e(r['system_name'] or '')}</span></td>"
        f"<td style='{td};text-align:right'>{e(_vn(s.get('vcpu')))}</td><td style='{td};text-align:right'>{e(_vn(s.get('ramGB')))}</td>"
        f"<td style='{td};text-align:right'>{e(_vn(s.get('diskProvGB')))}</td>"
        f"<td style='{td}'>{('<b style=' + chr(34) + 'color:#9A3412' + chr(34) + '>Nên rà</b>: ' + e('; '.join(json.loads(r['reasons'])))) if r['flagged'] else 'Đang dùng bình thường'}</td></tr>"
        for r, s in zip(items[:MAX_ROWS_IN_MAIL], sig[:MAX_ROWS_IN_MAIL]))
    more = f"<p style='color:#465F83'>… và {len(items) - MAX_ROWS_IN_MAIL} VM khác — xem đủ trên ICO.</p>" if len(items) > MAX_ROWS_IN_MAIL else ""
    button = (f"<p><a href=\"{e(link)}\" style='display:inline-block;background:#165AB8;color:#FFFFFF;padding:10px 18px;"
              f"border-radius:6px;text-decoration:none;font-weight:bold'>Xác nhận trên ICO</a></p>") if link else \
        "<p><b>Xác nhận trên ICO:</b> đăng nhập ICO bằng tài khoản Microsoft → Cổng Owner.</p>"
    body = (f"<div style='font-family:Arial,sans-serif;font-size:14px;color:#0B2D5C'>"
            f"<p>{e(intro)}</p>"
            f"<p><b>Tổng tài nguyên:</b> {e(_vn(tot['vcpu']))} vCPU · {e(_vn(tot['ramGB']))} GB RAM · {e(_vn(tot['diskProvGB']))} GB disk</p>"
            f"<table style='border-collapse:collapse;font-size:13px' cellspacing='0'><tr>"
            f"<th style='{td};text-align:left'>VM</th><th style='{td};text-align:left'>Hệ thống</th><th style='{td}'>vCPU</th>"
            f"<th style='{td}'>RAM (GB)</th><th style='{td}'>Disk (GB)</th><th style='{td};text-align:left'>Tín hiệu</th></tr>"
            f"{rows}</table>{more}{button}"
            f"<p style='color:#465F83;font-size:12px'>Email này không chứa liên kết đăng nhập; ICO yêu cầu đăng nhập bằng tài khoản công ty. "
            f"Hạn xác nhận: {e(due)}.</p></div>")
    return subject, "\n".join(text), body


# ---------------------------------------------------------------- deliver (outbox)
def _smtp(db: Session) -> Optional[mailer.SmtpSettings]:
    from ..spend.notify import smtp_settings
    return smtp_settings(db, from_name="ICO – Rà soát owner")


def deliver(db: Session, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Send the pending / failed e-mails of campaigns that are not dry runs. Safe to run from several places at once."""
    now = now or dt.datetime.utcnow()
    db.query(ReviewNotice).filter(ReviewNotice.status == "sending", ReviewNotice.claimed_at < now - CLAIM_TIMEOUT) \
        .update({"status": "failed", "error": "Gửi bị gián đoạn — gửi lại."}, synchronize_session=False)
    db.commit()
    ids = [i for (i,) in db.query(ReviewNotice.id).join(ReviewCampaign, ReviewCampaign.id == ReviewNotice.campaign_id)
           .filter(ReviewCampaign.dry_run.is_(False), ReviewNotice.status.in_(("pending", "failed")),
                   ReviewNotice.attempts < MAX_ATTEMPTS).order_by(ReviewNotice.id)]
    out = {"sent": 0, "failed": 0, "waiting": 0, "error": None}
    if not ids:
        return out
    try:
        cfg = _smtp(db)
    except Exception as exc:  # noqa: BLE001 — undecryptable password: a stored, safe message
        cfg, out["error"] = None, str(exc)[:300]
    if cfg is None:
        out["error"] = out["error"] or "Chưa cấu hình máy chủ SMTP (Cài đặt → Chi phí & nhắc hạn)."
        db.query(ReviewNotice).filter(ReviewNotice.id.in_(ids)).update({"error": out["error"]}, synchronize_session=False)
        db.commit()
        out["waiting"] = len(ids)
        return out
    for nid in ids:
        claimed = db.execute(update(ReviewNotice).where(ReviewNotice.id == nid, ReviewNotice.status.in_(("pending", "failed")),
                                                        ReviewNotice.attempts < MAX_ATTEMPTS)
                             .values(status="sending", claimed_at=now, attempts=ReviewNotice.attempts + 1)
                             .execution_options(synchronize_session=False)).rowcount
        db.commit()
        if not claimed:                       # another sender took it
            continue
        n = db.get(ReviewNotice, nid)
        try:
            mailer.send(cfg, [n.recipient], n.subject, n.body_text, n.body_html)
        except Exception as exc:  # noqa: BLE001 — every transport error is stored and retried
            n.status, n.error = "failed", str(exc)[:300]
            out["failed"] += 1
        else:
            n.status, n.sent_at, n.error = "sent", dt.datetime.utcnow(), None
            out["sent"] += 1
        db.commit()
    return out


def send_campaign(db: Session, c: ReviewCampaign, by: str) -> Dict[str, Any]:
    """Leave dry-run mode: the previewed e-mails become pending and go out now."""
    if c.status != "open":
        raise ValueError("Kỳ đã đóng.")
    c.dry_run = False
    db.query(ReviewNotice).filter(ReviewNotice.campaign_id == c.id, ReviewNotice.status == "preview") \
        .update({"status": "pending"}, synchronize_session=False)
    db.add(AuditLog(username=by, action="owners.review.send", detail=json.dumps({"period": c.period_label})))
    db.commit()
    return deliver(db)


# ---------------------------------------------------------------- leader round
def tick(db: Session, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Called on the leader each scheduler round: open the period's campaign once (in a start month), then deliver."""
    now = now or dt.datetime.utcnow()
    today = today_vn(now)
    opened = None
    if today.month in start_months():
        label = period_label(today)
        if not db.query(ReviewCampaign.id).filter(ReviewCampaign.period_label == label).first():
            try:
                opened = open_campaign(db, label, now=now).period_label
            except Exists:
                db.rollback()
    staged = advance(db, now)
    verified = verify(db, now)
    return {"opened": opened, **staged, "verified": verified, **deliver(db, now)}


# ================================================================ G3b — decisions
DECISION_LABELS = {"keep": "Còn dùng", "decommission": "Thu hồi", "resize": "Giảm cấu hình", "transfer": "Chuyển owner",
                   "not_mine": "Không phải của tôi"}
MAX_NOTE = 500
_EMAIL = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$")


class Invalid(ValueError):
    """A decision that cannot be saved; the message is Vietnamese and safe to show."""


def clean_decision(data: Dict[str, Any], item: ReviewItem, today: dt.date) -> Dict[str, Any]:
    """Validated column values of one decision (fields of other decisions are cleared)."""
    d = data.get("decision")
    if d not in DECISION_LABELS:
        raise Invalid("Quyết định không hợp lệ.")
    out: Dict[str, Any] = {"decision": d, "action_date": None, "new_vcpu": None, "new_ram_gb": None, "transfer_to": None}
    note = " ".join(str(data.get("note") or "").split())
    if len(note) > MAX_NOTE:
        raise Invalid(f"Ghi chú dài quá {MAX_NOTE} ký tự.")
    out["note"] = note or None
    if d == "decommission":
        try:
            day = dt.date.fromisoformat(str(data.get("actionDate") or ""))
        except ValueError:
            raise Invalid("Thu hồi cần ngày dự kiến (yyyy-mm-dd).") from None
        if not today <= day <= today + dt.timedelta(days=365):
            raise Invalid("Ngày thu hồi dự kiến phải từ hôm nay đến 12 tháng tới.")
        out["action_date"] = day
    elif d == "resize":
        sig = json.loads(item.signals or "{}")
        for key, col, cur, label in (("newVcpu", "new_vcpu", sig.get("vcpu"), "vCPU"), ("newRamGB", "new_ram_gb", sig.get("ramGB"), "RAM")):
            v = data.get(key)
            if v in (None, ""):
                continue
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise Invalid(f"{label} đề xuất phải là số nguyên dương.")
            if isinstance(cur, (int, float)) and v >= cur:
                raise Invalid(f"{label} đề xuất ({v}) phải nhỏ hơn hiện tại ({_vn(cur)}).")
            out[col] = v
        if out["new_vcpu"] is None and out["new_ram_gb"] is None:
            raise Invalid("Giảm cấu hình cần vCPU hoặc RAM đề xuất.")
    elif d == "transfer":
        to = str(data.get("transferTo") or "").strip().lower()
        if not _EMAIL.match(to) or len(to) > 255:
            raise Invalid("Chuyển owner cần email người nhận hợp lệ.")
        if to == (item.owner_email or ""):
            raise Invalid("Email người nhận trùng owner hiện tại.")
        out["transfer_to"] = to
    return out


def visible_items(db: Session, principal, campaign_id: int):
    """Items of a campaign the caller may see / decide: owner scope = systems they own or deputise (current) or items
    addressed to their e-mail; full users = every item."""
    from sqlalchemy import or_
    from .access import identities
    q = db.query(ReviewItem).filter(ReviewItem.campaign_id == campaign_id)
    if principal.system_ids is None:
        return q
    ids, mails = sorted(principal.system_ids), sorted(identities(principal.user))
    conds = []
    if ids:
        conds.append(ReviewItem.system_id.in_(ids))
    if mails:
        conds.append(ReviewItem.owner_email.in_(mails))
    return q.filter(or_(*conds)) if conds else q.filter(ReviewItem.id == -1)


def decide(db: Session, c: ReviewCampaign, items: List[ReviewItem], data: Dict[str, Any], by: str, infra: bool = False,
           now: Optional[dt.datetime] = None) -> int:
    """Save one decision on several items (caller has checked they are visible). Owners: only while the campaign is
    open. infra=True: the infrastructure team settles items after closing too. Commits; one audit row."""
    now = now or dt.datetime.utcnow()
    if c.status != "open" and not infra:
        raise Invalid("Kỳ rà soát đã đóng — liên hệ team hạ tầng.")
    if not items:
        raise Invalid("Chưa chọn VM nào.")
    today = today_vn(now)
    for it in items:
        for col, v in clean_decision(data, it, today).items():
            setattr(it, col, v)
        it.decided_by, it.decided_at = by, now
        it.resolved_at = it.resolved_by = None
    db.add(AuditLog(username=by, action="owners.review.decide",
                    detail=json.dumps({"period": c.period_label, "decision": data.get("decision"), "infra": infra,
                                       "items": [i.id for i in items][:200]}, ensure_ascii=False)))
    db.commit()
    return len(items)


def resolve(db: Session, item: ReviewItem, by: str) -> None:
    """Admin queue: a "transfer" / "not mine" item has been re-assigned in Hệ thống & Owner (G2a)."""
    if item.decision not in ("transfer", "not_mine"):
        raise Invalid("Chỉ xử lý được VM chuyển owner / không phải của tôi.")
    item.resolved_at, item.resolved_by = dt.datetime.utcnow(), by
    db.add(AuditLog(username=by, action="owners.review.resolve", detail=json.dumps({"item": item.id, "vm": item.vm_name})))
    db.commit()


# ================================================================ G3b — reminders, escalation, closing
STAGES = (("closing", 7), ("escalation", 0), ("reminder2", -2), ("reminder1", -7))   # days relative to the due date


def stage_for(c: ReviewCampaign, today: dt.date) -> Optional[str]:
    """The latest stage reached (earlier stages that were missed while ICO was down are not sent late)."""
    for kind, offset in STAGES:
        if today >= c.due_at + dt.timedelta(days=offset):
            return kind
    return None


def _add_notice(db: Session, c: ReviewCampaign, recipient: str, kind: str, mail: Tuple[str, str, str], n: int, now: dt.datetime) -> bool:
    if db.query(ReviewNotice.id).filter_by(campaign_id=c.id, recipient=recipient, kind=kind).first():
        return False
    subject, text, body = mail
    db.add(ReviewNotice(campaign_id=c.id, recipient=recipient, kind=kind, status="preview" if c.dry_run else "pending",
                        subject=subject[:255], body_text=text, body_html=body, vm_count=n, created_at=now))
    return True


def _mail(title: str, intro: str, items: List[ReviewItem], c: ReviewCampaign, extra: str = "") -> Tuple[str, str, str]:
    e = html.escape
    link = portal_link(c.id)
    lines = [intro, ""] + [f"- {i.vm_name} ({i.system_code or '—'}){' — owner ' + i.owner_email if i.owner_email else ''}"
                           for i in items[:MAX_ROWS_IN_MAIL]]
    if len(items) > MAX_ROWS_IN_MAIL:
        lines.append(f"… và {len(items) - MAX_ROWS_IN_MAIL} VM khác.")
    if extra:
        lines += ["", extra]
    lines += ["", f"Xác nhận trên ICO: {link}" if link else "Xác nhận trên ICO: đăng nhập ICO → Cổng Owner."]
    td = "padding:6px 8px;border-bottom:1px solid #CED5DE;vertical-align:top"
    rows = "".join(f"<tr><td style='{td}'><b>{e(i.vm_name)}</b></td><td style='{td}'>{e(i.system_code or '—')} {e(i.system_name or '')}</td>"
                   f"<td style='{td}'>{e(i.owner_email or '—')}</td></tr>" for i in items[:MAX_ROWS_IN_MAIL])
    button = (f"<p><a href=\"{e(link)}\" style='display:inline-block;background:#165AB8;color:#FFFFFF;padding:10px 18px;"
              f"border-radius:6px;text-decoration:none;font-weight:bold'>Xác nhận trên ICO</a></p>") if link else ""
    body = (f"<div style='font-family:Arial,sans-serif;font-size:14px;color:#0B2D5C'><p>{e(intro)}</p>"
            f"{f'<p><b>{e(extra)}</b></p>' if extra else ''}"
            f"<table style='border-collapse:collapse;font-size:13px' cellspacing='0'><tr><th style='{td};text-align:left'>VM</th>"
            f"<th style='{td};text-align:left'>Hệ thống</th><th style='{td};text-align:left'>Owner</th></tr>{rows}</table>{button}"
            f"<p style='color:#465F83;font-size:12px'>Email này không chứa liên kết đăng nhập.</p></div>")
    return title, "\n".join(lines), body


def advance(db: Session, now: Optional[dt.datetime] = None) -> Dict[str, int]:
    """Reminders, escalation and closing of open campaigns from the clock (idempotent: one notice per recipient
    and kind; closing happens once)."""
    now = now or dt.datetime.utcnow()
    today = today_vn(now)
    s = get_settings()
    infra = (s.review_infra_email or "").strip().lower()
    out = {"reminders": 0, "escalations": 0, "closed": 0, "missingManager": 0}
    for c in db.query(ReviewCampaign).filter(ReviewCampaign.status == "open").all():
        stage = stage_for(c, today)
        if stage is None:
            continue
        due = c.due_at.strftime("%d/%m/%Y")
        open_items = db.query(ReviewItem).filter(ReviewItem.campaign_id == c.id, ReviewItem.owner_email.isnot(None),
                                                 ReviewItem.decision.is_(None)).order_by(ReviewItem.vm_name).all()
        if stage in ("reminder1", "reminder2"):
            by_owner: Dict[str, List[ReviewItem]] = {}
            for it in open_items:
                by_owner.setdefault(it.owner_email, []).append(it)
            for email, its in sorted(by_owner.items()):
                lead = "Nhắc lần cuối" if stage == "reminder2" else "Nhắc rà soát"
                title = f"[ICO] {lead} {c.period_label} — còn {len(its)} VM chưa xác nhận, hạn {due}"
                intro = f"Kỳ rà soát {c.period_label} còn {len(its)} VM của bạn chưa được xác nhận. Hạn: {due}."
                out["reminders"] += _add_notice(db, c, email, stage, _mail(title, intro, its, c), len(its), now)
        elif stage == "escalation":
            sys_ids = {i.system_id for i in open_items if i.system_id}
            managers = dict(db.query(System.id, System.manager_email).filter(System.id.in_(sys_ids))) if sys_ids else {}
            by_mgr: Dict[str, List[ReviewItem]] = {}
            missing: List[ReviewItem] = []
            for it in open_items:
                m = managers.get(it.system_id)
                (by_mgr.setdefault(m, []) if m else missing).append(it)
                if it.escalated_at is None:
                    it.escalated_at = now
            for email, its in sorted(by_mgr.items()):
                owners = ", ".join(sorted({i.owner_email for i in its}))
                title = f"[ICO] Rà soát {c.period_label} quá hạn — {len(its)} VM chưa được owner xác nhận"
                intro = (f"Kỳ rà soát {c.period_label} đã đến hạn {due}. {len(its)} VM của nhóm bạn quản lý (owner: {owners}) "
                         f"chưa được xác nhận. Đề nghị nhắc owner hoặc xác nhận thay trước khi kỳ đóng.")
                out["escalations"] += _add_notice(db, c, email, "escalation", _mail(title, intro, its, c), len(its), now)
            if missing:
                out["missingManager"] += len(missing)
                if infra:
                    title = f"[ICO] Rà soát {c.period_label} quá hạn — {len(missing)} VM, hệ thống thiếu quản lý"
                    intro = (f"Kỳ rà soát {c.period_label} đã đến hạn {due}. {len(missing)} VM chưa được xác nhận và hệ thống của chúng "
                             f"chưa khai quản lý trực tiếp, nên email leo thang gửi về team hạ tầng.")
                    out["escalations"] += _add_notice(db, c, infra, "escalation",
                                                      _mail(title, intro, missing, c, "Cảnh báo: thiếu quản lý trực tiếp — bổ sung ở Hệ thống & Owner."),
                                                      len(missing), now)
                else:
                    logger.warning("Review %s: %d VM escalated without manager and REVIEW_INFRA_EMAIL is empty", c.period_label, len(missing))
        elif stage == "closing":
            c.status, c.closed_at = "closed", now
            out["closed"] += 1
            if infra:
                title = f"[ICO] Đóng kỳ rà soát {c.period_label} — {len(open_items)} VM chờ team hạ tầng xác nhận"
                intro = (f"Kỳ rà soát {c.period_label} đã đóng. {len(open_items)} VM chưa có quyết định của owner, "
                         f"chờ team hạ tầng xác nhận (Trang chủ → hộp việc, nguồn Rà soát owner).")
                _add_notice(db, c, infra, "closing", _mail(title, intro, open_items, c), len(open_items), now)
            db.add(AuditLog(username="system", action="owners.review.close",
                            detail=json.dumps({"period": c.period_label, "undecided": len(open_items)})))
        db.commit()
    return out


def verify(db: Session, now: Optional[dt.datetime] = None) -> int:
    """Next-period check of "Thu hồi": the VM's CI is retired → verified_at. Returns the number newly verified."""
    now = now or dt.datetime.utcnow()
    rows = (db.query(ReviewItem).join(Ci, Ci.id == ReviewItem.ci_id)
            .filter(ReviewItem.decision == "decommission", ReviewItem.verified_at.is_(None), Ci.status == "retired").all())
    for it in rows:
        it.verified_at = now
    if rows:
        db.commit()
    return len(rows)


def not_cleaned(db: Session, today: dt.date) -> List[ReviewItem]:
    """"Thu hồi" past its planned date whose VM is still active in the CI registry."""
    return (db.query(ReviewItem).join(Ci, Ci.id == ReviewItem.ci_id)
            .filter(ReviewItem.decision == "decommission", ReviewItem.verified_at.is_(None), ReviewItem.action_date < today,
                    Ci.status == "active").order_by(ReviewItem.action_date, ReviewItem.vm_name).all())


# ================================================================ G3b — results
def summary(db: Session, c: ReviewCampaign) -> Dict[str, Any]:
    """Results of a campaign (page, PDF, Excel): response rate, VMs per decision, reclaimed resources (+ VND)."""
    s = get_settings()
    items = db.query(ReviewItem).filter(ReviewItem.campaign_id == c.id).order_by(ReviewItem.owner_email, ReviewItem.vm_name).all()
    owned = [i for i in items if i.owner_email]
    decided = [i for i in owned if i.decision]
    counts = {k: sum(1 for i in items if i.decision == k) for k in DECISION_LABELS}
    rec = {"vcpu": 0.0, "ramGB": 0.0, "diskGB": 0.0}
    rows = []
    for i in items:
        sig = json.loads(i.signals or "{}")
        vcpu, ram, disk = float(sig.get("vcpu") or 0), float(sig.get("ramGB") or 0), float(sig.get("diskProvGB") or 0)
        r = {"vcpu": 0.0, "ramGB": 0.0, "diskGB": 0.0}
        if i.decision == "decommission":
            r = {"vcpu": vcpu, "ramGB": ram, "diskGB": disk}
        elif i.decision == "resize":
            r = {"vcpu": max(0.0, vcpu - i.new_vcpu) if i.new_vcpu else 0.0, "ramGB": max(0.0, ram - i.new_ram_gb) if i.new_ram_gb else 0.0,
                 "diskGB": 0.0}
        for k in rec:
            rec[k] += r[k]
        rows.append({"id": i.id, "vm": i.vm_name, "vcenterKey": i.vcenter_key, "systemCode": i.system_code, "systemName": i.system_name,
                     "ownerEmail": i.owner_email, "flagged": bool(i.flagged), "reasons": json.loads(i.reasons or "[]"),
                     "decision": i.decision, "decisionLabel": DECISION_LABELS.get(i.decision, "Chờ team hạ tầng xác nhận"
                                                                                    if c.status == "closed" else "Chưa quyết định"),
                     "actionDate": i.action_date.isoformat() if i.action_date else None, "newVcpu": i.new_vcpu, "newRamGB": i.new_ram_gb,
                     "transferTo": i.transfer_to, "note": i.note, "decidedBy": i.decided_by,
                     "decidedAt": i.decided_at.isoformat() + "Z" if i.decided_at else None,
                     "escalated": i.escalated_at is not None, "verified": i.verified_at is not None,
                     "resolved": i.resolved_at is not None, "vcpu": vcpu, "ramGB": ram, "diskGB": disk, "reclaim": r})
    costs = (s.review_unit_cost_vcpu, s.review_unit_cost_ram_gb, s.review_unit_cost_disk_gb)
    monthly = round(rec["vcpu"] * costs[0] + rec["ramGB"] * costs[1] + rec["diskGB"] * costs[2]) if any(costs) else None
    per_owner: Dict[str, Dict[str, int]] = {}
    for i in owned:
        o = per_owner.setdefault(i.owner_email, {"items": 0, "decided": 0})
        o["items"] += 1
        o["decided"] += bool(i.decision)
    from .. import vm_ip
    vm_ip.attach(db, rows)
    return {"campaign": {"id": c.id, "periodLabel": c.period_label, "status": c.status, "dryRun": bool(c.dry_run),
                         "openedAt": c.opened_at.isoformat() + "Z", "dueAt": c.due_at.isoformat(),
                         "closedAt": c.closed_at.isoformat() + "Z" if c.closed_at else None},
            "totals": {"items": len(items), "owned": len(owned), "unowned": len(items) - len(owned), "decided": len(decided),
                       "undecided": len(owned) - len(decided), "flagged": sum(1 for i in items if i.flagged),
                       "responsePct": round(len(decided) * 100.0 / len(owned), 1) if owned else None,
                       "escalated": sum(1 for i in items if i.escalated_at), "verified": sum(1 for i in items if i.verified_at),
                       "queue": sum(1 for i in items if i.decision in ("transfer", "not_mine") and not i.resolved_at)},
            "byDecision": [{"decision": k, "label": v, "count": counts[k]} for k, v in DECISION_LABELS.items()],
            "reclaim": {**{k: round(v, 1) for k, v in rec.items()}, "vndMonthly": monthly,
                        "vndYearly": monthly * 12 if monthly is not None else None},
            "owners": [{"ownerEmail": k, **v, "pct": round(v["decided"] * 100.0 / v["items"], 1)} for k, v in sorted(per_owner.items())],
            "items": rows}
