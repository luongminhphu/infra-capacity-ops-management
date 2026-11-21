"""Event store + incident lifecycle (ICO v2 G5).

record_transitions() compares what is wrong now with ops_event_open and appends open / worse / better / clear rows to ops_event.
The watched items are the inbox items of the infrastructure sources (inbox.PROVIDERS, computed once per leader round and shared with
the Discord alerts), keyed by their alertKey — so the Hộp việc, the Discord alerts and the event store see the same objects.
A source that failed this round is "unknown": its open events stay open (no false clear).

Incidents: an event that opens on a CI without an unresolved incident opens one; other events of the same CI join it; when every
event of the incident has cleared it is resolved by "system". Flapping: ≥ FLAP_OPENS opens of one event within FLAP_HOURS → flapping,
announced once on the operations Discord channels."""
import datetime as dt
import json
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..inbox import SOURCE_LABELS
from ..shared.levels import LEVEL_RANK, worst
from ..timeutil import to_iso_utc as _iso, to_vn_str
from .models import LEVELS, OpsEvent, OpsEventOpen, OpsIncident

logger = logging.getLogger(__name__)

SOURCES = ("capacity", "backup", "datastore", "snapshot", "alarm", "health", "storage", "hardware", "config")
SOURCE_FEATURE = {"backup": "backup"}
# "vcenter" = one-off vCenter events from the change log (G6); not an inbox source
SOURCE_NAMES = {**SOURCE_LABELS, "vcenter": "Event vCenter"}
FLAP_OPENS = 4
FLAP_HOURS = 6
FLAP_PER_MESSAGE = 10                 # 10 × ≤ 190 characters: one Discord message
RETENTION_MONTHS = 13
CI_TYPES = ("vm", "hypervisor_host", "cluster", "datastore", "storage_array", "physical_server", "backup_client")
Current = Dict[str, Tuple[str, Optional[int], str, str]]      # event_key → (level, ci_id, title, detail)


# ---------------------------------------------------------------- CI of an inbox item
class CiResolver:
    """Maps an item's `ci` hint to a CI id with one query: {"type", "key"} = exact source_key; {"type", "name", "vc"?} = by name."""

    def __init__(self, db: Session):
        from ..cmdb.models import Ci
        self.by_key: Dict[Tuple[str, str], int] = {}
        self.by_name: Dict[Tuple[str, str, str], int] = {}
        for cid, typ, key, name, attrs in (db.query(Ci.id, Ci.type, Ci.source_key, Ci.name, Ci.attrs)
                                           .filter(Ci.status == "active", Ci.type.in_(CI_TYPES))):
            self.by_key[(typ, key)] = cid
            n = (name or "").lower()
            vc = json.loads(attrs or "{}").get("vcenterKey") or ""
            self.by_name.setdefault((typ, vc, n), cid)
            self.by_name.setdefault((typ, "", n), cid)          # lookups without vCenter: first match

    def __call__(self, hint: Optional[Dict[str, Any]]) -> Optional[int]:
        if not hint:
            return None
        if hint.get("key"):
            return self.by_key.get((hint["type"], hint["key"]))
        return self.by_name.get((hint["type"], hint.get("vc") or "", str(hint.get("name") or "").lower()))


def current_from_items(db: Session, items: Iterable[Dict[str, Any]]) -> Dict[str, Current]:
    """Inbox items of the watched sources → {source: {alertKey: (level, ci_id, title, detail)}}."""
    resolve = CiResolver(db)
    out: Dict[str, Current] = {s: {} for s in SOURCES}
    for it in items:
        if it["source"] in out and it["level"] in LEVELS:
            out[it["source"]][it["alertKey"]] = (it["level"], resolve(it.get("ci")), it["title"], it.get("detail") or "")
    return out


# ---------------------------------------------------------------- transitions
def record_transitions(db: Session, current: Dict[str, Current], unknown: Iterable[str] = (),
                       now: Optional[dt.datetime] = None, points: Iterable[Dict[str, Any]] = ()) -> Dict[str, int]:
    """current: {source: {event_key: (level, ci_id, title, detail)}} for every watched source; sources in `unknown` (failed this
    round) keep their open events untouched. Idempotent: the same `current` twice writes nothing the second time. Commits.
    points: one-off events that open no state (G6: HA restart, host failed — {source, key, level, ciId, title, detail, at}).
    New events (opens + points) are placed upstream first (physical server → host → VM), so a group forms in one round."""
    now = now or dt.datetime.utcnow()
    skip = set(unknown)
    open_rows = {(r.source, r.event_key): r for r in db.query(OpsEventOpen)}
    incidents = {i.group_key: i for i in db.query(OpsIncident).filter(OpsIncident.status != "resolved")}
    by_id = {i.id: i for i in incidents.values()}
    counts = Counter()
    touched: Set[int] = set()

    new_keys = [(s, k) for s, cur in current.items() if s not in skip for k in cur if (s, k) not in open_rows]
    recent = _recent_opens(db, [k for _, k in new_keys], now)

    def event(src, key, ci_id, inc_id, level, transition, title, detail=""):
        db.add(OpsEvent(at=now, source=src, ci_id=ci_id, incident_id=inc_id, event_key=key, level=level, transition=transition,
                        title=title[:500], detail=(detail or "")[:2000]))
        counts[transition] += 1

    fresh: List[Dict[str, Any]] = [dict(p, point=True) for p in points]
    for src, cur in current.items():
        if src in skip:
            continue
        for key, (level, ci_id, title, detail) in cur.items():
            row = open_rows.get((src, key))
            if row is None:
                fresh.append({"source": src, "key": key, "level": level, "ciId": ci_id, "title": title, "detail": detail})
                continue
            row.last_seen, row.title = now, title[:500]
            if row.ci_id is None and ci_id is not None:       # CI appeared after the event opened (sổ CI built later)
                row.ci_id = ci_id
            if level != row.level:
                event(src, key, row.ci_id, row.incident_id, level,
                      "worse" if LEVEL_RANK[level] < LEVEL_RANK[row.level] else "better", title, detail)
                row.level = level
                inc = by_id.get(row.incident_id)
                if inc is not None:
                    inc.peak_level = worst(inc.peak_level, level)
    corr = _Correlator(db, now, incidents, by_id, [f["ciId"] for f in fresh]) if fresh else None
    for f in sorted(fresh, key=lambda f: corr.rank(f["ciId"])):
        src, key, level, ci_id, title = f["source"], f["key"], f["level"], f["ciId"], f["title"]
        at = f.get("at") or now
        inc = _incident_for(db, incidents, by_id, src, key, ci_id, level, title, at, corr)        # `at` = event time
        inc.event_count += 1
        if not f.get("point"):
            if recent.get(key, 0) + 1 >= FLAP_OPENS:
                inc.flapping = True
            db.add(OpsEventOpen(source=src, event_key=key, ci_id=ci_id, incident_id=inc.id, level=level, title=title[:500],
                                opened_at=now, last_seen=now))
        db.add(OpsEvent(at=at, source=src, ci_id=ci_id, incident_id=inc.id, event_key=key, level=level, transition="open",
                        title=title[:500], detail=(f.get("detail") or "")[:2000]))
        counts["point" if f.get("point") else "open"] += 1
    for (src, key), row in open_rows.items():
        if src in skip or src not in current or key in current[src]:
            continue
        event(src, key, row.ci_id, row.incident_id, None, "clear", row.title)
        touched.add(row.incident_id)
        db.delete(row)
    db.flush()
    still_open = {i for (i,) in db.query(OpsEventOpen.incident_id).filter(OpsEventOpen.incident_id.in_(list(touched) or [-1]))}
    for iid in touched - still_open:
        inc = by_id.get(iid)
        if inc is not None:
            inc.status, inc.resolved_at, inc.resolved_by = "resolved", now, "system"
            counts["resolved"] += 1
    db.commit()
    return dict(counts)


def _recent_opens(db: Session, keys: List[str], now: dt.datetime) -> Dict[str, int]:
    out: Dict[str, int] = {}
    since = now - dt.timedelta(hours=FLAP_HOURS)
    for i in range(0, len(keys), 500):
        for key, n in (db.query(OpsEvent.event_key, func.count(OpsEvent.id))
                       .filter(OpsEvent.event_key.in_(keys[i:i + 500]), OpsEvent.transition == "open", OpsEvent.at >= since)
                       .group_by(OpsEvent.event_key)):
            out[key] = n
    return out


def _incident_for(db, incidents, by_id, src, key, ci_id, level, title, now, corr=None) -> OpsIncident:
    group = f"ci:{ci_id}" if ci_id is not None else f"ev:{src}:{key}"[:255]
    inc = incidents.get(group)
    if inc is None and ci_id is not None and corr is not None:
        inc = corr.join(ci_id, now)                  # G6: an incident of a strongly linked CI opened within 15 minutes of it
        if inc is not None:
            incidents[group] = inc
    if inc is None:
        inc = OpsIncident(ci_id=ci_id, root_ci_id=ci_id, group_key=group, source=src, title=title[:500], status="open",
                          peak_level=level, opened_at=now, event_count=0)
        db.add(inc)
        db.flush()
        incidents[group] = by_id[inc.id] = inc
    else:
        inc.peak_level = worst(inc.peak_level, level)
    return inc


# ---------------------------------------------------------------- G6: grouping along the CI graph
TYPE_RANK = {"physical_server": 0, "storage_array": 0, "storage_volume": 1, "cluster": 1, "hypervisor_host": 2,
             "datastore": 2, "vm": 3, "backup_client": 4}


class _Correlator:
    """Joins a new event to an unresolved incident opened within EVENT_CORRELATE_MINUTES on a CI linked to it by STRONG
    dependency links (upstream: what it runs on; downstream: what runs on it). Weak links (name / IP guesses) never group.
    Several such incidents (e.g. two VMs restarted before their host's alarm arrived) are merged into the oldest; the
    incident's root becomes the most upstream CI that has an event (PSU of the physical server, not the VM)."""

    def __init__(self, db: Session, now: dt.datetime, incidents: Dict[str, OpsIncident], by_id: Dict[int, OpsIncident],
                 ci_ids: Iterable[Optional[int]]):
        from ..cmdb.impact import DEPENDS_ON_SOURCE, DEPENDS_ON_TARGET
        from ..cmdb.models import Ci, CiLink
        from ..config import get_settings
        self.db, self.incidents, self.by_id = db, incidents, by_id
        self.window = dt.timedelta(minutes=get_settings().event_correlate_minutes)
        self.up: Dict[int, Set[int]] = defaultdict(set)      # ci → what it depends on
        self.down: Dict[int, Set[int]] = defaultdict(set)
        for f, t, rel in (db.query(CiLink.from_ci, CiLink.to_ci, CiLink.relation)
                          .filter(CiLink.confidence == "strong", CiLink.relation.in_(DEPENDS_ON_TARGET + DEPENDS_ON_SOURCE))):
            dep, on = (f, t) if rel in DEPENDS_ON_TARGET else (t, f)
            self.up[dep].add(on)
            self.down[on].add(dep)
        ids = {i for i in ci_ids if i} | {x for i in by_id.values() for x in (i.ci_id, i.root_ci_id) if x}
        self.types = dict(db.query(Ci.id, Ci.type).filter(Ci.id.in_(list(ids) or [-1]))) if ids else {}

    def rank(self, ci_id: Optional[int]) -> int:
        return TYPE_RANK.get(self.types.get(ci_id), 5)

    def _reach(self, ci_id: int, graph: Dict[int, Set[int]], depth: int = 4) -> Set[int]:
        seen, frontier = set(), {ci_id}
        for _ in range(depth):
            frontier = {n for c in frontier for n in graph.get(c, ())} - seen - {ci_id}
            if not frontier:
                break
            seen |= frontier
        return seen

    def join(self, ci_id: int, at: dt.datetime) -> Optional[OpsIncident]:
        """`at` = time of the event (a vCenter event read late keeps its own time): incidents opened within the window
        around it count."""
        related = self._reach(ci_id, self.up) | self._reach(ci_id, self.down)
        hits = sorted({i.id: i for i in self.by_id.values()
                       if i.status != "resolved" and abs(i.opened_at - at) <= self.window and ({i.ci_id, i.root_ci_id} & related)}.values(),
                      key=lambda i: (i.opened_at, i.id))
        if not hits:
            return None
        target = hits[0]
        roots = {target.root_ci_id or target.ci_id, ci_id}
        for other in hits[1:]:
            roots.add(other.root_ci_id or other.ci_id)
            self._merge(other, target)
        roots.discard(None)
        # the most upstream CI with an event: none of the other roots is above it
        top = [r for r in roots if not (self._reach(r, self.up) & (roots - {r}))]
        target.root_ci_id = min(top, key=lambda r: (self.rank(r), r)) if top else target.root_ci_id
        return target

    def _merge(self, other: OpsIncident, target: OpsIncident) -> None:
        self.db.flush()
        self.db.query(OpsEvent).filter(OpsEvent.incident_id == other.id).update({"incident_id": target.id}, synchronize_session="evaluate")
        self.db.query(OpsEventOpen).filter(OpsEventOpen.incident_id == other.id).update({"incident_id": target.id},
                                                                                         synchronize_session="evaluate")
        target.event_count += other.event_count
        target.peak_level = worst(target.peak_level, other.peak_level)
        target.flapping = bool(target.flapping or other.flapping)
        if other.acked_at and not target.acked_at:
            target.acked_at, target.acked_by = other.acked_at, other.acked_by
        target.assignee = target.assignee or other.assignee
        for k in [k for k, v in self.incidents.items() if v is other]:
            self.incidents[k] = target
        self.by_id.pop(other.id, None)
        self.db.delete(other)                   # created minutes ago by the same round(s); its events now belong to target


def notify_flapping(db: Session, post=None, now: Optional[dt.datetime] = None) -> int:
    """Tell each operations Discord channel once per flapping incident that is open or closed within FLAP_HOURS (a flapping
    incident often auto-resolves between rounds; an old one is not announced when a first channel is added). Delivery is tracked per channel
    (flap_sent_channels), so a channel that failed gets it next round and the others are not told twice; flap_alerted is
    set when every channel has it. At most FLAP_PER_MESSAGE incidents per round (the rest follow next round)."""
    from .. import ops_alerts, secrets_box
    from ..shared.webhook import WebhookError, post_json, validate_discord_url
    from ..spend.notify import discord_escape
    from sqlalchemy import or_
    recent = (now or dt.datetime.utcnow()) - dt.timedelta(hours=FLAP_HOURS)
    pending = (db.query(OpsIncident).filter(OpsIncident.flapping.is_(True), OpsIncident.flap_alerted.is_(False),
                                            or_(OpsIncident.status != "resolved", OpsIncident.resolved_at >= recent))
               .order_by(OpsIncident.id).limit(FLAP_PER_MESSAGE).all())
    channels = ops_alerts._channels(db) if pending else []
    if not pending or not channels:
        return 0
    sent = {i.id: set(filter(None, (i.flap_sent_channels or "").split(","))) for i in pending}
    for ch in channels:
        cid = str(ch.id)
        todo = [i for i in pending if cid not in sent[i.id]]
        if not todo:
            continue
        text = "**ICO — sự cố chập chờn**\n" + "\n".join(
            f"• Sự cố #{i.id}: {discord_escape(i.title[:150])} — mở / hết ≥ {FLAP_OPENS} lần trong {FLAP_HOURS} giờ" for i in todo)
        try:
            (post or post_json)(validate_discord_url(secrets_box.decrypt(ch.webhook_enc)),
                                {"content": text[:1900], "allowed_mentions": {"parse": []}, "username": "ICO cảnh báo"})
        except (WebhookError, secrets_box.SecretUnreadable, ValueError) as exc:
            logger.warning("Flapping alert to channel %s failed: %s", ch.id, exc)
            continue
        for i in todo:
            sent[i.id].add(cid)
    every = {str(ch.id) for ch in channels}
    for i in pending:
        i.flap_sent_channels = ",".join(sorted(sent[i.id])) or None
        i.flap_alerted = every <= sent[i.id]
    db.commit()
    return sum(1 for i in pending if i.flap_alerted)


def _points(db: Session) -> List[Dict[str, Any]]:
    """Change-log events that are incidents by themselves (HA restart, host failed / lost) not turned into events yet;
    marked handled here (committed with the transitions)."""
    from .changes import KIND_LABELS, POINT_LEVEL
    from .models import ChangeLog
    rows = (db.query(ChangeLog).filter(ChangeLog.handled.is_(False), ChangeLog.kind.in_(list(POINT_LEVEL)))
            .order_by(ChangeLog.at, ChangeLog.id).limit(500).all())
    for r in rows:
        r.handled = True
    return [{"source": "vcenter", "key": f"vce|{r.vcenter_key}|{r.vc_event_id}", "level": POINT_LEVEL[r.kind], "ciId": r.ci_id,
             "title": f"{KIND_LABELS[r.kind]}: {r.object_name or '—'}", "detail": r.message or "", "at": r.at} for r in rows]


def _backfill_ci(db: Session) -> None:
    """Change-log rows stored during a sync before the CI existed (first sync, VM created this round): the CI build of this
    round has run since, so look them up again (last 2 days only)."""
    from ..cmdb.models import vc_key
    from .models import ChangeLog
    rows = (db.query(ChangeLog).filter(ChangeLog.ci_id.is_(None), ChangeLog.object_name.isnot(None),
                                       ChangeLog.object_type.in_(("vm", "host")),
                                       ChangeLog.at >= dt.datetime.utcnow() - dt.timedelta(days=2)).limit(5000).all())
    if not rows:
        return
    resolve = CiResolver(db)
    for r in rows:
        r.ci_id = resolve({"type": "vm", "name": r.object_name, "vc": r.vcenter_key} if r.object_type == "vm"
                          else {"type": "hypervisor_host", "key": vc_key(r.vcenter_key, r.object_name)})


def run(db: Session, items: List[Dict[str, Any]], errors: Iterable[str], post=None) -> Dict[str, int]:
    """Leader round: inbox items already computed for the Discord alerts + one-off vCenter events → transitions,
    grouping along the CI graph, flapping alert."""
    _backfill_ci(db)
    counts = record_transitions(db, current_from_items(db, items), [e for e in errors if e in SOURCES], points=_points(db))
    counts["flapAlerted"] = notify_flapping(db, post)
    return counts


def purge(db: Session, now: Optional[dt.datetime] = None) -> int:
    """Events older than RETENTION_MONTHS (incidents stay: they are the monthly statistics)."""
    from ..collectors.retention import months_ago
    from .changes import purge as purge_changes
    cutoff = months_ago(now or dt.datetime.utcnow(), RETENTION_MONTHS)
    n = db.query(OpsEvent).filter(OpsEvent.at < cutoff).delete(synchronize_session=False)
    db.commit()
    return n + purge_changes(db, cutoff)


# ---------------------------------------------------------------- reads
def _hidden_sources(db: Session) -> Set[str]:
    from ..features import get_features, is_on
    f = get_features(db)
    return {s for s, feat in SOURCE_FEATURE.items() if not is_on(f, feat)}


def scope_ci_ids(db: Session, osc) -> Optional[Set[int]]:
    """CIs of the owner filter: the VMs of the selected systems + what they run on / are stored on (one hop of ci_link)."""
    if osc is None or not osc.active:
        return None
    from ..cmdb.models import CiLink
    from ..owners.models import System, SystemMember
    from ..owners.service import scope_systems
    sids = [i for (i,) in scope_systems(db, osc, System.id)]
    vms = {c for (c,) in db.query(SystemMember.ci_id).filter(SystemMember.system_id.in_(sids or [-1]))}
    out = set(vms)
    ids = list(vms)
    for i in range(0, len(ids), 500):
        out |= {t for (t,) in db.query(CiLink.to_ci).filter(CiLink.from_ci.in_(ids[i:i + 500]))}
    return out


def _ci_names(db: Session, ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    from ..cmdb.models import Ci
    ids = [i for i in set(ids) if i]
    out = {}
    for i in range(0, len(ids), 500):
        for cid, typ, name in db.query(Ci.id, Ci.type, Ci.name).filter(Ci.id.in_(ids[i:i + 500])):
            out[cid] = {"id": cid, "type": typ, "name": name}
    return out


def incident_out(i: OpsIncident, cis: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    return {"id": i.id, "title": i.title, "source": i.source, "sourceLabel": SOURCE_NAMES.get(i.source, i.source), "status": i.status, "level": i.peak_level, "ci": cis.get(i.ci_id),
            "root": cis.get(i.root_ci_id) if i.root_ci_id and i.root_ci_id != i.ci_id else None,
            "openedAt": _iso(i.opened_at), "ackedAt": _iso(i.acked_at), "ackedBy": i.acked_by, "resolvedAt": _iso(i.resolved_at),
            "resolvedBy": i.resolved_by, "assignee": i.assignee, "eventCount": i.event_count, "flapping": bool(i.flapping),
            "note": i.note}


def event_out(e: OpsEvent) -> Dict[str, Any]:
    return {"id": e.id, "at": _iso(e.at), "source": e.source, "sourceLabel": SOURCE_NAMES.get(e.source, e.source), "ciId": e.ci_id, "incidentId": e.incident_id, "level": e.level,
            "transition": e.transition, "title": e.title, "detail": e.detail}


def list_incidents(db: Session, status: Optional[str] = None, level: Optional[str] = None, source: Optional[str] = None,
                   days: Optional[int] = None, osc=None, limit: int = 500) -> Dict[str, Any]:
    q = db.query(OpsIncident)
    hidden = _hidden_sources(db)
    if hidden:
        q = q.filter(OpsIncident.source.notin_(hidden))
    if status == "active":
        q = q.filter(OpsIncident.status != "resolved")
    elif status:
        q = q.filter(OpsIncident.status == status)
    if level:
        q = q.filter(OpsIncident.peak_level == level)
    if source:
        q = q.filter(OpsIncident.source == source)
    if days:
        q = q.filter(OpsIncident.opened_at >= dt.datetime.utcnow() - dt.timedelta(days=days))
    ids = scope_ci_ids(db, osc)
    if ids is not None:
        q = q.filter(OpsIncident.ci_id.in_(list(ids) or [-1]))
    rows = q.order_by(OpsIncident.opened_at.desc(), OpsIncident.id.desc()).limit(limit + 1).all()
    cis = _ci_names(db, [x for r in rows[:limit] for x in (r.ci_id, r.root_ci_id)])
    return {"items": [incident_out(r, cis) for r in rows[:limit]], "truncated": len(rows) > limit,
            "sources": [{"key": k, "label": SOURCE_NAMES[k]} for k in (*SOURCES, "vcenter") if k not in hidden]}


def get_incident(db: Session, iid: int) -> OpsIncident:
    inc = db.get(OpsIncident, iid)
    if inc is None or inc.source in _hidden_sources(db):
        raise HTTPException(404, "Không có sự cố này.")
    return inc


def incident_detail(db: Session, iid: int) -> Dict[str, Any]:
    inc = get_incident(db, iid)
    from . import changes
    evs = db.query(OpsEvent).filter(OpsEvent.incident_id == iid).order_by(OpsEvent.at, OpsEvent.id).limit(1000).all()
    cis = {inc.ci_id, inc.root_ci_id} | {e.ci_id for e in evs}
    return {**incident_out(inc, _ci_names(db, [inc.ci_id, inc.root_ci_id])), "events": [event_out(e) for e in evs],
            "changesBefore": changes.before(db, cis, inc.opened_at)}        # G6: what changed on these CIs in the 24 h before


def ci_timeline(db: Session, ci_id: int, limit: int = 300) -> List[Dict[str, Any]]:
    hidden = _hidden_sources(db)
    q = db.query(OpsEvent).filter(OpsEvent.ci_id == ci_id)
    if hidden:
        q = q.filter(OpsEvent.source.notin_(hidden))
    return [event_out(e) for e in q.order_by(OpsEvent.at.desc(), OpsEvent.id.desc()).limit(limit)]


def act(db: Session, user, iid: int, action: str, assignee: Optional[str] = None, note: Optional[str] = None,
        ip: Optional[str] = None) -> Dict[str, Any]:
    """ack / assign / resolve by a person (audit_log). Resolving with events still open is allowed: a new event opens a new incident."""
    from ..owners.service import audit
    inc = get_incident(db, iid)
    now = dt.datetime.utcnow()
    who = user.display_name or user.username
    if action == "ack":
        if inc.status != "open":
            raise HTTPException(409, "Chỉ nhận được sự cố đang mở.")
        inc.status, inc.acked_at, inc.acked_by = "acked", now, who
    elif action == "assign":
        inc.assignee = (assignee or "").strip()[:255] or None
    elif action == "resolve":
        if inc.status == "resolved":
            raise HTTPException(409, "Sự cố đã đóng.")
        inc.status, inc.resolved_at, inc.resolved_by = "resolved", now, who
        inc.acked_at, inc.acked_by = inc.acked_at or now, inc.acked_by or who
    else:
        raise HTTPException(400, "Thao tác không hợp lệ.")
    if note is not None:
        inc.note = note.strip()[:2000] or None
    audit(db, user, f"incident.{action}", ip, id=iid, assignee=inc.assignee)
    db.commit()
    return incident_out(inc, _ci_names(db, [inc.ci_id]))


# ---------------------------------------------------------------- statistics
def _avg_min(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 1) if vals else None


def stats(db: Session, months: int = 6, osc=None, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """MTTA (open → ack) and MTTR (open → resolved) in minutes per Vietnam month, per source and per system; top 10 CIs."""
    from ..collectors.retention import months_ago
    from ..owners.models import System, SystemMember
    now = now or dt.datetime.utcnow()
    vn_now = now + dt.timedelta(hours=7)                     # the window is Vietnam months, like _vn_month
    since = months_ago(vn_now, months - 1).replace(day=1, hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(hours=7)
    q = db.query(OpsIncident).filter(OpsIncident.opened_at >= since)
    hidden = _hidden_sources(db)
    if hidden:
        q = q.filter(OpsIncident.source.notin_(hidden))
    ids = scope_ci_ids(db, osc)
    if ids is not None:
        q = q.filter(OpsIncident.ci_id.in_(list(ids) or [-1]))
    rows = q.all()
    sys_of = dict(db.query(SystemMember.ci_id, System.name).join(System, System.id == SystemMember.system_id)
                  .filter(SystemMember.ci_id.in_({r.ci_id for r in rows if r.ci_id} or {-1})))

    def group(key) -> List[Dict[str, Any]]:
        g: Dict[str, Dict[str, list]] = defaultdict(lambda: {"n": [], "tta": [], "ttr": []})
        for r in rows:
            k = key(r)
            if k is None:
                continue
            g[k]["n"].append(r)
            if r.acked_at:
                g[k]["tta"].append((r.acked_at - r.opened_at).total_seconds() / 60)
            if r.resolved_at:
                g[k]["ttr"].append((r.resolved_at - r.opened_at).total_seconds() / 60)
        return [{"key": k, "label": SOURCE_NAMES.get(k, k), "incidents": len(v["n"]), "crit": sum(1 for r in v["n"] if r.peak_level == "crit"),
                 "open": sum(1 for r in v["n"] if r.status != "resolved"), "mttaMin": _avg_min(v["tta"]), "mttrMin": _avg_min(v["ttr"])}
                for k, v in sorted(g.items())]
    top = Counter(r.ci_id for r in rows if r.ci_id).most_common(10)
    cis = _ci_names(db, [c for c, _ in top])
    return {"byMonth": group(lambda r: to_vn_str(r.opened_at, "%Y-%m")), "bySource": group(lambda r: r.source),
            "bySystem": group(lambda r: sys_of.get(r.ci_id)),
            "topCis": [{"ci": cis.get(c), "incidents": n} for c, n in top],
            "open": sum(1 for r in rows if r.status != "resolved"), "total": len(rows), "months": months}
