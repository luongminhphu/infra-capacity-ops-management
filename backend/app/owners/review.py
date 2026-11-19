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
    return {"opened": opened, **deliver(db, now)}
