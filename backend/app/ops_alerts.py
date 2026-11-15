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


def messages(new: List[Dict[str, Any]], resolved: List[OpsAlertState], limit: int = 1900) -> List[str]:
    """Discord messages (≤ 2000 characters each); a long list is split, nothing is dropped."""
    from .spend.notify import discord_escape
    head = "**ICO — cảnh báo vận hành (mức 90%)**"
    lines = [f"• [{inbox.LEVEL_LABELS[it['level']]}] {discord_escape(it['title'])}" for it in new] + \
            [f"• Đã hết: {discord_escape(r.title)}" for r in resolved]
    out, cur = [], head
    for ln in lines:
        if len(cur) + 1 + len(ln) > limit:
            out.append(cur)
            cur = head + " (tiếp)"
        cur += "\n" + ln[:limit - 40]
    out.append(cur)
    return out


def _today():
    from .spend.fiscal import today_vn
    return today_vn()


def check_and_notify(db: Session, post=None) -> Dict[str, int]:
    """Compare the current push items with the stored state; send one Discord message per
    channel for the new ones (and the cleared ones). Returns counters."""
    from .shared.webhook import WebhookError as NotifyError, post_json as _post_json, validate_discord_url
    post = post or _post_json
    from .features import get_features
    items, _ = inbox._compute(db, _today())
    features = get_features(db)
    current = {it["key"]: it for it in items if it.get("push") and it["source"] in ("capacity", "backup")
               and not (it.get("feature") and features.get(it["feature"]) is False)}
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
    channels = _channels(db)
    if (new or resolved) and channels:
        for ch in channels:
            try:
                url = validate_discord_url(secrets_box.decrypt(ch.webhook_enc))
                for text in messages(new, resolved):
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
        else:
            # every channel failed: keep the new alerts un-armed so the next cycle retries them
            for s in rows.values():
                s.active = False
    db.commit()
    inbox.invalidate()
    return {"new": len(new), "resolved": len(resolved), "sent": sent, "failed": failed}
