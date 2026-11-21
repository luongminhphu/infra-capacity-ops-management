"""Sync one ServiceDesk Plus source (ICO v2 G7a): backfill from `backfill_from` in small batches, then incremental reads by
last_updated_time every `interval_minutes`. Runs on the leader pod (scheduler round) or Đồng bộ ngay (admin), under the
source lock. Progress (cursor + rows) is committed after every page, so a run stopped half-way (deadline, pod restart,
SDP error) continues from the last page next time."""
import datetime as dt
import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import ItsmConfig, ItsmError
from .models import ItsmRequest, ItsmSourceState
from .sdp import client as make_client, dt_to_ms, parse_request

logger = logging.getLogger(__name__)

MAX_PER_RUN = 200                 # detail reads per run: the backfill moves in small batches (rate limit, deadline)
_FIELDS = {"template": "template", "subject": "subject", "kind": "kind", "requester_name": "requesterName",
           "requester_email": "requesterEmail", "department": "department", "status": "status",
           "approval_status": "approvalStatus", "created_at": "createdAt", "resolved_at": "resolvedAt", "approved_at": "approvedAt",
           "description": "description"}


def _state(db: Session, cfg: ItsmConfig) -> ItsmSourceState:
    row = db.query(ItsmSourceState).filter_by(source_key=cfg.key).first()
    if row is None:
        row = ItsmSourceState(source_key=cfg.key, kind=cfg.kind, name=(cfg.name or cfg.key)[:255], backfill_done=False)
        db.add(row)
        db.flush()
    return row


def due(row: ItsmSourceState, cfg: ItsmConfig, now: dt.datetime) -> bool:
    """Backfill: every round until done. Afterwards: every interval_minutes."""
    return (not row.backfill_done or row.captured_at is None
            or now - row.captured_at >= dt.timedelta(minutes=max(1, cfg.interval_minutes)))


def upsert(db: Session, key: str, p: Dict[str, Any], now: dt.datetime) -> None:
    row = db.query(ItsmRequest).filter_by(source_key=key, req_id=p["reqId"]).first()
    if row is None:
        row = ItsmRequest(source_key=key, req_id=p["reqId"])
        db.add(row)
    if row.description != p.get("description") and row.reviewed_at is None:
        row.parse_status = None                       # new / changed body: read again (G7b); a person's decision stays
    for col, k in _FIELDS.items():
        if col != "kind" or row.reviewed_at is None:  # a kind set by a person stays
            setattr(row, col, p.get(k))
    row.updated_at = dt.datetime.fromtimestamp(p["updatedMs"] / 1000, dt.timezone.utc).replace(tzinfo=None) if p.get("updatedMs") else None
    row.udf = json.dumps(p.get("udf") or {}, ensure_ascii=False)
    row.fetched_at = now


def sync_itsm(db: Session, cfg: ItsmConfig, force: bool = False, max_requests: int = MAX_PER_RUN,
              now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    from ..collectors.locks import source_lock
    with source_lock("itsm", cfg.key):
        now = now or dt.datetime.utcnow()
        row = _state(db, cfg)
        if row.backfill_from != cfg.backfill_from:            # the start date changed: read again from it
            row.backfill_from, row.cursor_ms, row.backfill_done = cfg.backfill_from, None, False
        if not force and not due(row, cfg, now):
            db.commit()
            return {"key": cfg.key, "ok": True, "idle": True, "requests": row.requests or 0}
        after = row.cursor_ms if row.cursor_ms is not None else dt_to_ms(cfg.backfill_from) - 1
        fetched, done = 0, False
        try:
            c = make_client(cfg)
            start = 1
            while fetched < max_requests:
                rows, more = c.list_page(after, start)
                for r in rows:
                    if fetched >= max_requests:
                        break
                    p = parse_request(c.get(str(r.get("id"))))
                    if not p["reqId"]:
                        continue
                    upsert(db, cfg.key, p, now)
                    fetched += 1
                    if p.get("updatedMs") and (row.cursor_ms is None or p["updatedMs"] > row.cursor_ms):
                        row.cursor_ms = p["updatedMs"]
                db.commit()                                   # progress survives a stop half-way
                if fetched >= max_requests:
                    break
                if not more:
                    done = True
                    break
                start += len(rows)
        except Exception as exc:  # noqa: BLE001 — recorded on the source row, then re-raised
            msg = str(exc) if isinstance(exc, ItsmError) else f"Lỗi đọc ServiceDesk Plus: {type(exc).__name__}"
            logger.warning("ITSM sync failed for %s: %s", cfg.key, msg)
            db.rollback()
            row = _state(db, cfg)
            row.last_error, row.last_error_at = msg[:1000], dt.datetime.utcnow()
            db.commit()
            raise ItsmError(msg) from exc
        if done:
            row.backfill_done = True
        row.kind, row.name = cfg.kind, (cfg.name or cfg.key)[:255]
        row.requests = db.query(func.count(ItsmRequest.id)).filter(ItsmRequest.source_key == cfg.key).scalar()
        row.captured_at = row.last_ok_at = now
        row.last_error = row.last_error_at = None
        db.commit()
        from .process import process
        try:
            parsed = process(db, cfg.key)              # body tables → lines; AI fallback → review queue (G7b)
        except Exception:  # noqa: BLE001 — the requests are stored; parsing runs again next round
            db.rollback()
            logger.exception("ITSM parse failed for %s", cfg.key)
            parsed = {}
        logger.info("Synced SDP %s: %d request(s) read, backfill %s, cursor %s", cfg.key, fetched,
                    "done" if row.backfill_done else "in progress", row.cursor_ms)
        return {"key": cfg.key, "ok": True, "read": fetched, "backfillDone": bool(row.backfill_done), "requests": row.requests,
                **{f"parse_{k}": v for k, v in parsed.items()}}
