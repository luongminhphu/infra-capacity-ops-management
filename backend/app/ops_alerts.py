"""Operations alerts pushed to Discord (decision 5 of docs/design/super-app.md): capacity and
backup items at the 90 % level. Each alert is sent once while it stays active and re-armed
when it clears, so a flapping metric does not spam the channel.

Channels: Discord channels of Cài đặt → Chi phí & nhắc hạn ticked "Nhận cảnh báo vận hành"
(webhook encrypted with secrets_box, Discord-only URL check against SSRF)."""
import datetime as dt
import logging
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from . import inbox, secrets_box
from .models import OpsAlertState

logger = logging.getLogger(__name__)


def _channels(db: Session):
    from .spend.models import SpendNotifyChannel
    return db.query(SpendNotifyChannel).filter(SpendNotifyChannel.kind == "discord", SpendNotifyChannel.enabled.is_(True),
                                               SpendNotifyChannel.ops_alerts.is_(True)).all()


def message(new: List[Dict[str, Any]], resolved: List[OpsAlertState]) -> str:
    from .spend.notify import discord_escape
    lines = ["**ICO — cảnh báo vận hành (mức 90%)**"]
    for it in new:
        lines.append(f"• [{inbox.LEVEL_LABELS[it['level']]}] {discord_escape(it['title'])}")
    for r in resolved:
        lines.append(f"• Đã hết: {discord_escape(r.title)}")
    return "\n".join(lines)[:1900]


def check_and_notify(db: Session, post=None) -> Dict[str, int]:
    """Compare the current push items with the stored state; send one Discord message per
    channel for the new ones (and the cleared ones). Returns counters."""
    from .spend.notify import NotifyError, _post_json, validate_discord_url
    post = post or _post_json
    items, _ = inbox.collect(db, use_cache=False)
    current = {it["key"]: it for it in items if it.get("push") and it["source"] in ("capacity", "backup")}
    states = {s.key: s for s in db.query(OpsAlertState).all()}
    now = dt.datetime.utcnow()
    new = [it for k, it in current.items() if not (states.get(k) and states[k].active)]
    resolved = [s for k, s in states.items() if s.active and k not in current]
    rows = {}
    for it in new:
        s = states.get(it["key"]) or OpsAlertState(key=it["key"], first_at=now)
        if not s.active:
            s.first_at = now
        s.title, s.active, s.resolved_at, s.last_error = it["title"][:500], True, None, None
        db.add(s)
        rows[it["key"]] = s
    for s in resolved:
        s.active, s.resolved_at = False, now
    sent = failed = 0
    if new or resolved:
        text = message(new, resolved)
        for ch in _channels(db):
            try:
                url = validate_discord_url(secrets_box.decrypt(ch.webhook_enc))
                post(url, {"content": text, "allowed_mentions": {"parse": []}, "username": "ICO cảnh báo"})
                sent += 1
            except (NotifyError, secrets_box.SecretUnreadable, ValueError) as exc:
                failed += 1
                logger.warning("Ops alert to channel %s failed: %s", ch.id, exc)
                for s in rows.values():
                    s.last_error = str(exc)[:500]
        if sent:
            for s in rows.values():
                s.last_sent_at = now
    db.commit()
    inbox.invalidate()
    return {"new": len(new), "resolved": len(resolved), "sent": sent, "failed": failed}
