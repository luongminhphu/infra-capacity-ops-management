"""Daily expiry reminders (docs/design/spend-module.md §2.3, §6, §8).

Once-only per (item, end date, threshold, channel) is enforced by the UNIQUE constraint on
spend_reminder_log. Each run:
  1. pending rows older than STALE (process died mid-send) -> 'unknown', never re-sent
     (at-most-once: a duplicate reminder costs more trust than one missed threshold);
  2. for every active item expiring within the largest threshold, pick the SMALLEST threshold
     already reached; larger unsent ones become 'skipped' (catch-up after downtime sends one
     message, not three);
  3. new rows are committed as 'pending' BEFORE sending; failed rows are retried on later
     runs up to MAX_ATTEMPTS;
  4. one digest message per channel.
"""
import asyncio
import datetime as dt
import logging
import threading
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..features import get_features, is_on
from . import fiscal, service, settings as spend_settings
from .models import OpexItem, SpendNotifyChannel, SpendReminderLog, SpendSmtpConfig, SpendVendor
from .notify import NotifyError, ReminderLine, send

logger = logging.getLogger("vco.spend.reminder")

MAX_ATTEMPTS = 5
STALE = dt.timedelta(hours=1)
LOOP_SECONDS = 15 * 60
_lock = threading.Lock()


def pick_threshold(days_left: int, thresholds: Sequence[int]) -> Tuple[Optional[int], List[int]]:
    """(threshold to send now, larger thresholds to mark skipped). Nothing when not yet due or past due."""
    if days_left < 0:
        return None, []
    due = sorted(t for t in thresholds if days_left <= t)
    if not due:
        return None, []
    return due[0], due[1:]


def line_for(item: OpexItem, vendor: Optional[SpendVendor], today: dt.date, base_url: str) -> ReminderLine:
    return ReminderLine(
        code=item.code, title=item.title, vendor=vendor.name if vendor else "—",
        end_date=service.date_vn(item.end_date), days_left=(item.end_date - today).days,
        amount=service.money_vn(item.amount_gross), owner=item.owner_name, owner_email=item.owner_email,
        link=f"{base_url}/#/spend-opex?code={item.code}" if base_url else None)


def run(db: Session, now: Optional[dt.datetime] = None) -> Dict[str, object]:
    with _lock:
        return _run(db, now or dt.datetime.utcnow())


def _run(db: Session, now: dt.datetime) -> Dict[str, object]:
    cfg = spend_settings.load(db)
    today = fiscal.today_vn(now)
    stale = (db.query(SpendReminderLog).filter(SpendReminderLog.status == "pending", SpendReminderLog.created_at < now - STALE)
             .update({SpendReminderLog.status: "unknown",
                      SpendReminderLog.last_error: "Tiến trình dừng giữa lúc gửi — không gửi lại để tránh trùng."},
                     synchronize_session=False))
    db.commit()

    channels = db.query(SpendNotifyChannel).filter(SpendNotifyChannel.enabled.is_(True)).order_by(SpendNotifyChannel.id).all()
    horizon = today + dt.timedelta(days=max(cfg.reminder_days))
    items = (db.query(OpexItem).filter(OpexItem.status == "active", OpexItem.end_date >= today,
                                       OpexItem.end_date <= horizon).all())
    summary = {"at": now, "today": today, "items": len(items), "channels": len(channels), "stale": stale,
               "sent": 0, "failed": 0, "skipped": 0}
    if not channels or not items:
        _save_state(db, summary)
        return summary

    by_id = {i.id: i for i in items}
    existing = {(r.opex_id, r.end_date, r.threshold_days, r.channel_id): r
                for r in db.query(SpendReminderLog).filter(SpendReminderLog.opex_id.in_(list(by_id)))}
    to_send: Dict[int, List[SpendReminderLog]] = {c.id: [] for c in channels}
    for item in items:
        target, skipped = pick_threshold((item.end_date - today).days, cfg.reminder_days)
        if target is None:
            continue
        for ch in channels:
            for t in skipped:
                if (item.id, item.end_date, t, ch.id) not in existing:
                    db.add(SpendReminderLog(opex_id=item.id, end_date=item.end_date, threshold_days=t, channel_id=ch.id,
                                            status="skipped", created_at=now))
                    summary["skipped"] += 1
            row = existing.get((item.id, item.end_date, target, ch.id))
            if row is None:
                row = SpendReminderLog(opex_id=item.id, end_date=item.end_date, threshold_days=target, channel_id=ch.id,
                                       status="pending", attempts=0, created_at=now)
                db.add(row)
                to_send[ch.id].append(row)
            elif row.status == "failed" and row.attempts < MAX_ATTEMPTS:
                to_send[ch.id].append(row)
    try:
        db.commit()        # rows exist BEFORE anything is sent
    except IntegrityError:
        db.rollback()      # another run inserted the same rows: it owns those deliveries
        logger.warning("Reminder run raced with another run; skipped this cycle")
        return summary

    smtp_cfg = db.get(SpendSmtpConfig, 1)
    vendors = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_({i.vendor_id for i in items}))}
    for ch in channels:
        rows = to_send[ch.id]
        if not rows:
            continue
        lines = [line_for(by_id[r.opex_id], vendors.get(by_id[r.opex_id].vendor_id), today, cfg.app_base_url) for r in rows]
        error = None
        try:
            send(ch, smtp_cfg, lines)
        except NotifyError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 — a bug in one channel must not stop the others
            logger.exception("Unexpected error sending reminders to channel %s", ch.id)
            error = f"Lỗi không xác định: {type(exc).__name__}"
        for r in rows:
            r.attempts += 1
            if error:
                r.status, r.last_error = "failed", error[:500]
            else:
                r.status, r.sent_at, r.last_error = "sent", dt.datetime.utcnow(), None
        summary["failed" if error else "sent"] += len(rows)
        db.commit()
        if error:
            logger.warning("Reminder channel %s (%s) failed for %d items: %s", ch.id, ch.kind, len(rows), error)
    _save_state(db, summary)
    logger.info("Reminder run %s: %d items due window, sent=%d failed=%d skipped=%d stale=%d", today, summary["items"],
                summary["sent"], summary["failed"], summary["skipped"], stale)
    return summary


def _save_state(db: Session, summary: Dict[str, object]) -> None:
    spend_settings.set_state(db, "reminder", {k: summary[k] for k in ("at", "today", "sent", "failed", "skipped", "stale")})
    db.commit()


def tick(now: Optional[dt.datetime] = None) -> Optional[Dict[str, object]]:
    """Called every LOOP_SECONDS: runs when the module is on and the configured hour has passed.
    Re-running the same day only retries failures — every delivery is guarded by the log."""
    now = now or dt.datetime.utcnow()
    db = SessionLocal()
    try:
        if not is_on(get_features(db), "spend"):
            return None
        cfg = spend_settings.load(db)
        local = now.replace(tzinfo=dt.timezone.utc).astimezone(fiscal.VN_TZ)
        if local.hour < cfg.reminder_hour:
            return None
        summary = run(db, now)
        try:
            from .contracts import purge_due        # files of old contract versions after Final
            purge_due(db, now)
        except Exception:  # noqa: BLE001 — a purge error must not hide the reminder result
            db.rollback()
            logger.exception("Contract purge failed; retrying next cycle")
        return summary
    finally:
        db.close()


async def reminder_loop() -> None:
    from ..leader import wait_until_leader
    while True:
        await wait_until_leader()          # several replicas: only the leader pod sends reminders
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Spend reminder tick failed; retrying next cycle")
        await asyncio.sleep(LOOP_SECONDS)
