"""Set-based writes of the CI registry: read the current set once, compute the diff, bulk insert / update / delete.

Every function is idempotent: called twice with the same input, the second call writes nothing.
`last_seen` moves forward only when it is older than SEEN_GRANULARITY, so consecutive polls do not rewrite
every row (and two builds in a row leave the tables byte-identical). Callers commit.
"""
import datetime as dt
import json
from typing import Any, Dict, Iterable, List, Set, Tuple

from sqlalchemy import delete, insert, update
from sqlalchemy.orm import Session

from .models import Ci, CiKey, CiLink

SEEN_GRANULARITY = dt.timedelta(hours=1)
CHUNK = 500                                   # IN (...) lists stay well under SQLite / PostgreSQL limits

Identity = Tuple[str, str, str]               # (type, source_kind, source_key)
LinkId = Tuple[int, int, str]                 # (from_ci, to_ci, relation)


def _dumps(attrs: Dict[str, Any]) -> str:
    return json.dumps(attrs or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _chunks(seq: List[Any]) -> Iterable[List[Any]]:
    for i in range(0, len(seq), CHUNK):
        yield seq[i:i + CHUNK]


def _stale(seen: dt.datetime, now: dt.datetime) -> bool:
    return seen is None or now - seen >= SEEN_GRANULARITY


def upsert_cis(db: Session, desired: Dict[Identity, Dict[str, Any]], now: dt.datetime) -> Tuple[Dict[Identity, int], Dict[str, int]]:
    """desired: identity → {"name", "attrs"}. Inserts new CIs, updates name / attrs, reactivates retired ones.
    Returns (identity → id for every desired CI, counters {"inserted", "updated"})."""
    existing = {(r.type, r.source_kind, r.source_key): r for r in
                db.query(Ci.id, Ci.type, Ci.source_kind, Ci.source_key, Ci.name, Ci.status, Ci.attrs, Ci.last_seen)}
    new_rows, upd_rows = [], []
    for ident, d in desired.items():
        name, attrs = str(d.get("name") or ident[2])[:255], _dumps(d.get("attrs") or {})
        cur = existing.get(ident)
        if cur is None:
            new_rows.append({"type": ident[0], "source_kind": ident[1], "source_key": ident[2], "name": name, "attrs": attrs,
                             "status": "active", "first_seen": now, "last_seen": now})
        elif cur.name != name or cur.attrs != attrs or cur.status != "active" or _stale(cur.last_seen, now):
            upd_rows.append({"id": cur.id, "name": name, "attrs": attrs, "status": "active",
                             "last_seen": now if _stale(cur.last_seen, now) or cur.status != "active" else cur.last_seen})
    if new_rows:
        db.execute(insert(Ci), new_rows)
    if upd_rows:
        db.execute(update(Ci), upd_rows)
    ids = {ident: r.id for ident, r in existing.items() if ident in desired}
    if new_rows:
        types = sorted({r["type"] for r in new_rows})
        for r in db.query(Ci.id, Ci.type, Ci.source_kind, Ci.source_key).filter(Ci.type.in_(types)):
            ident = (r.type, r.source_kind, r.source_key)
            if ident in desired:
                ids[ident] = r.id
    return ids, {"inserted": len(new_rows), "updated": len(upd_rows)}


def set_keys(db: Session, desired: Dict[int, Set[Tuple[str, str]]]) -> Dict[str, int]:
    """desired: ci_id → {(kind, value_norm)} for the CIs seen in this build; their keys become exactly that set.
    Keys of CIs not in `desired` (not seen this time, retired) are kept so a lookup still finds them."""
    if not desired:
        return {"inserted": 0, "deleted": 0}
    have: Dict[int, Dict[Tuple[str, str], int]] = {}
    for r in db.query(CiKey.id, CiKey.ci_id, CiKey.kind, CiKey.value_norm):
        if r.ci_id in desired:
            have.setdefault(r.ci_id, {})[(r.kind, r.value_norm)] = r.id
    drop, add = [], []
    for ci_id, keys in desired.items():
        cur = have.get(ci_id, {})
        drop += [kid for k, kid in cur.items() if k not in keys]
        add += [{"ci_id": ci_id, "kind": k, "value_norm": v} for (k, v) in sorted(keys) if (k, v) not in cur]
    for part in _chunks(drop):
        db.execute(delete(CiKey).where(CiKey.id.in_(part)))
    if add:
        db.execute(insert(CiKey), add)
    return {"inserted": len(add), "deleted": len(drop)}


def set_links(db: Session, desired: Dict[LinkId, Dict[str, Any]], now: dt.datetime) -> Dict[str, int]:
    """desired: (from, to, relation) → {"source", "confidence", "attrs"}: the whole current graph.
    Links no longer produced are deleted (a VM that moved is no longer hosted_on its old host)."""
    have = {(r.from_ci, r.to_ci, r.relation): r for r in
            db.query(CiLink.id, CiLink.from_ci, CiLink.to_ci, CiLink.relation, CiLink.source, CiLink.confidence,
                     CiLink.attrs, CiLink.last_seen)}
    add, upd = [], []
    for lid, d in desired.items():
        attrs = _dumps(d.get("attrs") or {})
        cur = have.get(lid)
        if cur is None:
            add.append({"from_ci": lid[0], "to_ci": lid[1], "relation": lid[2], "source": d["source"],
                        "confidence": d["confidence"], "attrs": attrs, "first_seen": now, "last_seen": now})
        elif (cur.source, cur.confidence, cur.attrs) != (d["source"], d["confidence"], attrs) or _stale(cur.last_seen, now):
            upd.append({"id": cur.id, "source": d["source"], "confidence": d["confidence"], "attrs": attrs,
                        "last_seen": now if _stale(cur.last_seen, now) else cur.last_seen})
    drop = [r.id for lid, r in have.items() if lid not in desired]
    for part in _chunks(drop):
        db.execute(delete(CiLink).where(CiLink.id.in_(part)))
    if add:
        db.execute(insert(CiLink), add)
    if upd:
        db.execute(update(CiLink), upd)
    return {"inserted": len(add), "updated": len(upd), "deleted": len(drop)}


def retire_unseen(db: Session, now: dt.datetime, days: int) -> int:
    """Active CIs not seen for `days` become retired (never deleted). Returns the number retired."""
    cutoff = now - dt.timedelta(days=max(1, int(days)))
    res = db.execute(update(Ci).where(Ci.status == "active", Ci.last_seen < cutoff)
                     .values(status="retired").execution_options(synchronize_session=False))
    return int(res.rowcount or 0)
