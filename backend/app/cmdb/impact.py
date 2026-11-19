"""Impact analysis on the CI graph (ICO v2 G1c): what depends on a CI (downstream) and what it depends on (upstream).

Dependency direction per relation: for "A hosted_on B" a failure of B hits A (A is downstream of B); for
"A protected_by B" (VM → backup client) the client loses its source when the VM fails (B downstream of A).
"records" / "covers" (DC asset, Opex) are documentation, not dependencies, and are not walked.

  physical_server → hypervisor_host → vm → backup_client;   storage_array → storage_volume → datastore → vm
  cluster → hypervisor_host (member_of)

Breadth-first, one query per level (chunked IN lists), cycle-safe (a node is visited once), at most MAX_NODES nodes.
"""
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .models import Ci, CiLink

DEPENDS_ON_TARGET = ("hosted_on", "member_of", "runs_on", "stored_on", "backed_by", "on")   # from_ci depends on to_ci
DEPENDS_ON_SOURCE = ("protected_by",)                                                    # to_ci depends on from_ci
MAX_DEPTH = 4
MAX_NODES = 5000
MAX_ITEMS = 300
CHUNK = 500


def _edges(db: Session, frontier: List[int], down: bool) -> List[Tuple[int, int, str]]:
    """(from node, to node, confidence) one level further, in the walking direction."""
    out = []
    for i in range(0, len(frontier), CHUNK):
        part = frontier[i:i + CHUNK]
        if down:      # who depends on the frontier
            cond = or_(and_(CiLink.to_ci.in_(part), CiLink.relation.in_(DEPENDS_ON_TARGET)),
                       and_(CiLink.from_ci.in_(part), CiLink.relation.in_(DEPENDS_ON_SOURCE)))
        else:         # what the frontier depends on
            cond = or_(and_(CiLink.from_ci.in_(part), CiLink.relation.in_(DEPENDS_ON_TARGET)),
                       and_(CiLink.to_ci.in_(part), CiLink.relation.in_(DEPENDS_ON_SOURCE)))
        for f, t, rel, conf in db.query(CiLink.from_ci, CiLink.to_ci, CiLink.relation, CiLink.confidence).filter(cond):
            target_side = rel in DEPENDS_ON_TARGET
            src, dst = ((t, f) if target_side else (f, t)) if down else ((f, t) if target_side else (t, f))
            out.append((src, dst, conf))
    return out


def walk(db: Session, ci_id: int, direction: str = "down", depth: int = MAX_DEPTH,
         hidden: Optional[Set[str]] = None) -> Dict[str, Any]:
    """{"direction", "depth", "total", "counts": {type: n}, "weak": n reached only through a weak link,
    "items": [{id, type, name, status, level, weak}] (first MAX_ITEMS by level), "truncated"}. Retired CIs and
    `hidden` types are left out of the result (they are still walked through: a hidden type is never a parent)."""
    down = direction != "up"
    depth = max(1, min(int(depth), MAX_DEPTH))
    seen: Dict[int, Tuple[int, bool]] = {ci_id: (0, False)}
    frontier, truncated = [ci_id], False
    for level in range(1, depth + 1):
        if not frontier:
            break
        nxt = []
        for src, dst, conf in sorted(_edges(db, frontier, down)):
            if dst in seen or src not in seen:
                continue
            if len(seen) >= MAX_NODES + 1:
                truncated = True
                break
            seen[dst] = (level, seen[src][1] or conf == "weak")
            nxt.append(dst)
        frontier = [] if truncated else nxt
    ids = [i for i in seen if i != ci_id]
    rows: List[Ci] = []
    for i in range(0, len(ids), CHUNK):
        rows += db.query(Ci.id, Ci.type, Ci.name, Ci.status).filter(Ci.id.in_(ids[i:i + CHUNK])).all()
    hidden = hidden or set()
    items = [{"id": r.id, "type": r.type, "name": r.name, "status": r.status, "level": seen[r.id][0], "weak": seen[r.id][1]}
             for r in rows if r.status == "active" and r.type not in hidden]
    items.sort(key=lambda x: (x["level"], x["type"], x["name"], x["id"]))
    counts: Dict[str, int] = {}
    for it in items:
        counts[it["type"]] = counts.get(it["type"], 0) + 1
    return {"direction": "down" if down else "up", "depth": depth, "total": len(items), "counts": counts,
            "weak": sum(1 for it in items if it["weak"]), "items": items[:MAX_ITEMS],
            "truncated": truncated or len(items) > MAX_ITEMS}
