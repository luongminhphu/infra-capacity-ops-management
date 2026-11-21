"""Operations alerts pushed to Discord (decision 5 of docs/design/super-app.md, extended by the rules of
Cài đặt → Cảnh báo — app/alert_rules.py): capacity / backup at the 90 % level, datastore, snapshot, vCenter
alarm and host health items at the level chosen per source. Each alert is sent once per channel while it stays active and
re-armed when it clears, so a flapping metric does not spam the channel. Delivery state is kept
per channel (`sent_channels`): a channel that failed is retried on the next run.

Channels: Discord channels of Cài đặt → Chi phí & nhắc hạn ticked "Nhận cảnh báo vận hành"
(webhook encrypted with secrets_box, Discord-only URL check against SSRF)."""
import datetime as dt
import threading
import logging
from typing import Any, Dict, List, Set

from sqlalchemy.orm import Session

from . import inbox, secrets_box
from .models import OpsAlertState

logger = logging.getLogger(__name__)


def _channels(db: Session):
    from .spend.models import SpendNotifyChannel
    return db.query(SpendNotifyChannel).filter(SpendNotifyChannel.kind == "discord", SpendNotifyChannel.enabled.is_(True),
                                               SpendNotifyChannel.ops_alerts.is_(True)).all()


def chunks(new: List[Dict[str, Any]], resolved: List[OpsAlertState], limit: int = 1900):
    """Discord messages (≤ 2000 characters each) with the alerts each one carries: [(text, new_items, resolved_states)].
    A long list is split, nothing is dropped; the caller marks each part delivered as soon as it is posted."""
    from .spend.notify import discord_escape
    head = "**ICO — cảnh báo vận hành**"
    src = lambda it: inbox.SOURCE_LABELS.get(it.get("source"), "")  # noqa: E731
    lines = [(f"• [{inbox.LEVEL_LABELS[it['level']]}] {src(it) + ': ' if src(it) else ''}{discord_escape(it['title'])}", it, None)
             for it in new] + [(f"• Đã hết: {discord_escape(r.title)}", None, r) for r in resolved]
    out, cur, n_items, r_items = [], head, [], []
    for ln, it, r in lines:
        if len(cur) + 1 + len(ln) > limit:
            out.append((cur, n_items, r_items))
            cur, n_items, r_items = head + " (tiếp)", [], []
        cur += "\n" + ln[:limit - 40]
        (n_items.append(it) if it is not None else r_items.append(r))
    out.append((cur, n_items, r_items))
    return out


def messages(new: List[Dict[str, Any]], resolved: List[OpsAlertState], limit: int = 1900) -> List[str]:
    return [text for text, _, _ in chunks(new, resolved, limit)]


def _today():
    from .spend.fiscal import today_vn
    return today_vn()


from .alert_rules import SOURCES as _RULE_SOURCES

PUSH_SOURCES = tuple(_RULE_SOURCES)     # sources that can push (Cài đặt → Cảnh báo decides which do)
_KEY_PREFIX_SOURCE = {"cap": "capacity", "bk": "backup", "bkf": "backup", "ds": "datastore", "snap": "snapshot",
                      "alarm": "alarm", "hh": "health", "st": "storage", "as": "asset", "aw": "asset",
                      "hw": "hardware", "hwd": "hardware", "hwf": "hardware", "cfg": "config"}


def _source_of(s: OpsAlertState) -> str:
    """Provider of a stored alert (rows written before the `source` column: from the key prefix)."""
    return s.source or _KEY_PREFIX_SOURCE.get(s.key.split("|", 1)[0], "")


def _sent(s: OpsAlertState) -> Set[str]:
    return {c for c in (s.sent_channels or "").split(",") if c}


def _set_sent(s: OpsAlertState, ids: Set[str]) -> None:
    s.sent_channels = ",".join(sorted(ids))


def _akey(it: Dict[str, Any]) -> str:
    return it.get("alertKey") or it["key"]


_run_lock = threading.Lock()


def check_and_notify(db: Session, post=None, computed=None) -> Dict[str, int]:
    """Serialised (BEQ-03): the scheduler round and "Chạy ngay" never send the same alert twice —
    the second caller waits, then sees what the first one recorded. computed: (items, errors) of inbox._compute already
    run this round (the leader shares it with the event store)."""
    from .leader import db_source_lock
    with _run_lock, db_source_lock("job", "ops-alerts") as mine:      # _run_lock: this pod; the DB lock: every pod
        if not mine:                    # the leader's round (or another pod's "Chạy ngay") is sending right now
            return {"new": 0, "resolved": 0, "sent": 0, "failed": 0}
        return _check_and_notify(db, post, computed)


def _check_and_notify(db: Session, post=None, computed=None) -> Dict[str, int]:
    """Compare the current push items with the stored state and tell each channel what it has not
    been told yet: the alerts it has not received, and "Đã hết" for the ones it received that have
    cleared. Delivery is tracked per channel, so a channel that failed gets them on a later run.

    Alerts of a provider that failed this run (DB blip, bug) are left exactly as they are:
    a missing provider is "unknown", not "cleared" — no false "Đã hết" followed by a re-alert.
    Returns counters."""
    from .shared.webhook import WebhookError as NotifyError, post_json as _post_json, validate_discord_url
    post = post or _post_json
    from .features import get_features
    items, errors = computed or inbox._compute(db, _today())
    unknown = {e for e in errors if e in PUSH_SOURCES}
    if unknown:
        logger.warning("Ops alerts: provider(s) %s failed; their alerts are kept unchanged this run", ", ".join(sorted(unknown)))
    from . import alert_rules
    features = get_features(db)
    rules = alert_rules.rules(db)
    quiet_now = alert_rules.quiet(db).active()
    current = {it.get("alertKey") or it["key"]: it for it in items if it["source"] in rules and rules[it["source"]].wants(it)
               and not (it.get("feature") and features.get(it["feature"]) is False)}
    states = {s.key: s for s in db.query(OpsAlertState).all()}
    channels = _channels(db)
    channel_ids = {str(ch.id) for ch in channels}
    now = dt.datetime.utcnow()

    for s in states.values():
        if s.sent_channels is None:      # row from before per-channel tracking
            _set_sent(s, set(channel_ids) if s.active and s.last_sent_at else set())

    activated = []
    for k, it in current.items():
        s = states.get(k)
        if s is None or not s.active:
            s = s or OpsAlertState(key=k)
            s.first_at, s.resolved_at, s.last_error, s.active = now, None, None, True
            _set_sent(s, set())          # a re-armed alert is owed to every channel again
            db.add(s)
            states[k] = s
            activated.append(k)
        s.title, s.source = it["title"][:500], it["source"]
    resolved = []
    for k, s in states.items():
        if s.active and k not in current and _source_of(s) not in unknown:
            s.active, s.resolved_at = False, now
            resolved.append(k)
    # Channels deleted since: nothing owed to them any more.
    for s in states.values():
        if _sent(s) - channel_ids and not s.active:
            _set_sent(s, _sent(s) & channel_ids)

    pending = set(activated)
    sent = failed = 0
    for ch in channels:
        cid = str(ch.id)
        to_new = [current[k] for k in current if cid not in _sent(states[k]) and rules[current[k]["source"]].to_channel(cid)]
        to_resolve = [s for s in states.values() if not s.active and cid in _sent(s)]
        if quiet_now:                 # quiet hours: only VƯỢT NGƯỠNG now, the rest is owed until they end
            to_new = [it for it in to_new if it["level"] == "crit"]
            to_resolve = []
        pending.update(_akey(it) for it in to_new)
        if not to_new and not to_resolve:
            continue
        try:
            url = validate_discord_url(secrets_box.decrypt(ch.webhook_enc))
            for text, part_new, part_resolved in chunks(to_new, to_resolve):
                post(url, {"content": text, "allowed_mentions": {"parse": []}, "username": "ICO cảnh báo"})
                for it in part_new:      # delivered now: a later part failing must not send this one again
                    s = states[_akey(it)]
                    _set_sent(s, _sent(s) | {cid})
                    s.last_sent_at = now
                for s in part_resolved:
                    _set_sent(s, _sent(s) - {cid})
        except (NotifyError, secrets_box.SecretUnreadable, ValueError) as exc:
            failed += 1
            logger.warning("Ops alert to channel %s failed: %s", ch.id, exc)
            for it in to_new:
                if cid not in _sent(states[_akey(it)]):
                    states[_akey(it)].last_error = str(exc)[:500]
            continue
        except Exception as exc:  # noqa: BLE001 — a bug must not skip the commit of the other channels' state
            failed += 1
            logger.exception("Ops alert to channel %s failed unexpectedly", ch.id)
            for it in to_new:
                states[_akey(it)].last_error = f"{type(exc).__name__}"[:500]
            continue
        sent += 1
    db.commit()
    inbox.invalidate()
    return {"new": len(pending), "resolved": len(resolved), "sent": sent, "failed": failed}
