"""Request ↔ business system ↔ VM (ICO v2 G7c).

System: the project of a request (first line's project, else "Dự án: …" in the body, else the department) is matched
  exactly — a mapping an admin confirmed (itsm_project_map), then a system code, name or project (norm_text) that only one
  system has. Anything else stays unmatched with up to 3 close suggestions (difflib) for the admin to confirm.
VM: each line's VM name against the CI registry (VMs, retired ones too — a decommissioned VM is retired):
  strong — the full VM name or the FQDN matches, and for a new VM it appeared within [created_at, resolved_at + 14 days]
           (vm_created event of the change log G6, else its first inventory snapshot); a reconfiguration / decommission
           matches the existing VM by full name / FQDN without a window;
  weak   — only the short name (first DNS label) matches, or a full-name match of a new VM outside the window (name reused).
Only strong links of new-VM requests with a system feed the owner assignment (source "itsm", lowest priority, G2b)."""
import datetime as dt
import difflib
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..shared.identity import norm_fqdn, short_name
from ..timeutil import to_iso_utc
from ..shared.text import norm_text
from .models import KIND_LABELS, ItsmProjectMap, ItsmRequest, ItsmRequestCi, ItsmRequestLine

WINDOW_AFTER = dt.timedelta(days=14)
_PROJECT_IN_BODY = re.compile(r"(?:d[ựu]\s*[áa]n|project)\s*[:：]\s*(?:<[^>]+>\s*)*([^<\n]{2,120})", re.I)


def project_of(req: ItsmRequest, lines: List[ItsmRequestLine]) -> Optional[str]:
    for ln in lines:
        if ln.project:
            return ln.project.strip()[:255]
    m = _PROJECT_IN_BODY.search(req.description or "")
    if m:
        return " ".join(m.group(1).split()).strip(" —-–:")[:255] or None
    return (req.department or "").strip()[:255] or None


class SystemMatcher:
    def __init__(self, db: Session):
        from ..owners.models import System
        self.systems = db.query(System).filter(System.status == "active").all()
        self.mapped = {m.project_norm: m.system_id for m in db.query(ItsmProjectMap)}
        keys: Dict[str, set] = defaultdict(set)
        for s in self.systems:
            for k in (norm_text(s.code), s.name_norm, norm_text(s.project or "")):
                if k:
                    keys[k].add(s.id)
        self.exact = {k: next(iter(v)) for k, v in keys.items() if len(v) == 1}   # a key two systems share is ambiguous

    def match(self, project: Optional[str]) -> Optional[int]:
        n = norm_text(project or "")
        return (self.mapped.get(n) or self.exact.get(n)) if n else None

    def suggest(self, project: str, k: int = 3) -> List[Dict[str, Any]]:
        n = norm_text(project)
        scored = []
        for s in self.systems:
            best = max(difflib.SequenceMatcher(None, n, x).ratio() for x in (s.name_norm, norm_text(s.project or ""), norm_text(s.code)) if x)
            if best >= 0.6:
                scored.append((best, s))
        scored.sort(key=lambda t: (-t[0], t[1].code))
        return [{"systemId": s.id, "code": s.code, "name": s.name, "score": round(r, 2)} for r, s in scored[:k]]


def _vm_index(db: Session):
    """VM CIs (active and retired) by lower-case name, and their fqdn / short_name keys."""
    import json
    from ..cmdb.models import Ci, CiKey
    vms: Dict[int, Tuple[str, str]] = {}                       # ci id → (vcenter key, VM name)
    by_name: Dict[str, List[int]] = defaultdict(list)
    for cid, name, attrs in db.query(Ci.id, Ci.name, Ci.attrs).filter(Ci.type == "vm"):
        vms[cid] = (json.loads(attrs or "{}").get("vcenterKey") or "", name)
        by_name[name.lower()].append(cid)
    keys: Dict[Tuple[str, str], List[int]] = defaultdict(list)
    for cid, kind, val in (db.query(CiKey.ci_id, CiKey.kind, CiKey.value_norm).join(Ci, Ci.id == CiKey.ci_id)
                           .filter(Ci.type == "vm", CiKey.kind.in_(("fqdn", "short_name")))):
        keys[(kind, val)].append(cid)
    return vms, by_name, keys


def appeared(db: Session, vms: Dict[int, Tuple[str, str]], ci_ids: List[int]) -> Dict[int, dt.datetime]:
    """When each VM appeared: its vm_created event (change log G6), else its first inventory snapshot."""
    from ..events.models import ChangeLog
    from ..models import VmSnapshot
    out: Dict[int, dt.datetime] = {}
    first: Dict[Tuple[str, str], dt.datetime] = {}
    names = sorted({vms[c][1] for c in ci_ids})
    for i in range(0, len(ci_ids), 500):
        out.update(db.query(ChangeLog.ci_id, func.min(ChangeLog.at)).filter(
            ChangeLog.ci_id.in_(ci_ids[i:i + 500]), ChangeLog.kind == "vm_created").group_by(ChangeLog.ci_id).all())
    for i in range(0, len(names), 500):
        for vc, vm, at in (db.query(VmSnapshot.vcenter_key, VmSnapshot.vm, func.min(VmSnapshot.captured_at))
                           .filter(VmSnapshot.vm.in_(names[i:i + 500])).group_by(VmSnapshot.vcenter_key, VmSnapshot.vm)):
            first[(vc or "", vm)] = at
    return {c: out.get(c) or first.get(vms[c]) for c in ci_ids}


def _candidates(name: str, by_name, keys) -> List[Tuple[int, str, bool]]:
    """(ci id, matched by, full match?) for one requested VM name."""
    n = name.strip().lower()
    full = {c: "name" for c in by_name.get(n, [])}
    if "." in n:
        for c in keys.get(("fqdn", norm_fqdn(n)), []):
            full.setdefault(c, "fqdn")
    if full:
        return [(c, how, True) for c, how in full.items()]
    sn = short_name(n)
    return [(c, "short_name", False) for c in keys.get(("short_name", sn), [])] if sn else []


def link_vms(db: Session, request_ids: Optional[List[int]] = None, now: Optional[dt.datetime] = None) -> Dict[str, int]:
    """Rebuild itsm_request_ci for the counted requests (or only `request_ids`). Caller commits."""
    from .process import counted
    now = now or dt.datetime.utcnow()
    q = counted(db.query(ItsmRequest)).filter(ItsmRequest.kind.in_(("new_vm", "resize", "decommission")))
    if request_ids is not None:
        q = q.filter(ItsmRequest.id.in_(request_ids or [-1]))
    reqs = {r.id: r for r in q}
    ids = list(reqs)
    found = []
    vms, by_name, keys = _vm_index(db)
    for i in range(0, len(ids), 500):
        for ln in db.query(ItsmRequestLine).filter(ItsmRequestLine.request_id.in_(ids[i:i + 500]), ItsmRequestLine.vm_name.isnot(None)):
            found += [(reqs[ln.request_id], ln.line_no, c, how, full) for c, how, full in _candidates(ln.vm_name, by_name, keys)]
    seen = appeared(db, vms, sorted({f[2] for f in found if f[4] and f[0].kind == "new_vm"}))
    rows = {}
    for r, line_no, cid, how, strong in found:
        if strong and r.kind == "new_vm":       # a new VM must have appeared while the request was being fulfilled
            at = seen.get(cid)
            strong = bool(at and r.created_at and r.created_at <= at <= (r.resolved_at or now) + WINDOW_AFTER)
            how += "+window" if strong else "+outside_window"
        rows[(r.id, line_no, cid)] = {"request_id": r.id, "line_no": line_no, "ci_id": cid,
                                      "match": "strong" if strong else "weak", "matched_by": how}
    dq = db.query(ItsmRequestCi)
    if request_ids is not None:
        dq = dq.filter(ItsmRequestCi.request_id.in_(request_ids or [-1]))
    dq.delete(synchronize_session=False)
    db.bulk_insert_mappings(ItsmRequestCi, list(rows.values()))
    strong = sum(1 for x in rows.values() if x["match"] == "strong")
    return {"strong": strong, "weak": len(rows) - strong}


def link_systems(db: Session) -> Dict[str, int]:
    """Project of every counted request → system_id (exact / confirmed mapping only). Caller commits."""
    from .process import counted
    m = SystemMatcher(db)
    reqs = counted(db.query(ItsmRequest)).all()
    lines = defaultdict(list)
    for ln in db.query(ItsmRequestLine).filter(ItsmRequestLine.request_id.in_([r.id for r in reqs] or [-1]), ItsmRequestLine.project.isnot(None)):
        lines[ln.request_id].append(ln)
    hit = 0
    for r in reqs:
        r.project = project_of(r, lines[r.id])
        r.system_id = m.match(r.project)
        hit += r.system_id is not None
    return {"matched": hit, "unmatched": len(reqs) - hit}


def run(db: Session) -> Dict[str, int]:
    out = {**link_systems(db), **link_vms(db)}
    db.commit()
    return out


def owner_sources(db: Session) -> Dict[int, Tuple[int, str]]:
    """VM CI → (system_id, reason) from strong links of new-VM requests matched to a system (owner source "itsm")."""
    out: Dict[int, Tuple[int, str]] = {}
    for cid, sid, req_id in (db.query(ItsmRequestCi.ci_id, ItsmRequest.system_id, ItsmRequest.req_id)
                             .join(ItsmRequest, ItsmRequest.id == ItsmRequestCi.request_id)
                             .filter(ItsmRequestCi.match == "strong", ItsmRequest.kind == "new_vm", ItsmRequest.system_id.isnot(None))
                             .order_by(ItsmRequest.created_at.desc())):
        out.setdefault(cid, (sid, f"Yêu cầu ServiceDesk #{req_id}"))
    return out


def sdp_url(source_key: str, req_id: str) -> Optional[str]:
    """Link to the request in ServiceDesk Plus: ITSM_BASE_URL, else https://<host>:<port> of the source; http(s) only."""
    from urllib.parse import quote, urlparse
    from ..config import get_settings
    st = get_settings()
    base = (st.itsm_base_url or "").strip().rstrip("/")
    if not base:
        cfg = next((c for c in st.itsm() if c.key == source_key), None)
        base = f"https://{cfg.host}:{cfg.port}" if cfg and cfg.host and not cfg.demo else ""
    u = urlparse(base)
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    return f"{base}/WorkOrder.do?woMode=viewWO&woID={quote(str(req_id), safe='')}"


def vm_requests(db: Session, ci_id: int, current: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Requests linked to one VM CI for its card: requested resources (the line) next to the current ones."""
    out = []
    for link, r, ln in (db.query(ItsmRequestCi, ItsmRequest, ItsmRequestLine)
                        .join(ItsmRequest, ItsmRequest.id == ItsmRequestCi.request_id)
                        .join(ItsmRequestLine, (ItsmRequestLine.request_id == ItsmRequestCi.request_id)
                              & (ItsmRequestLine.line_no == ItsmRequestCi.line_no))
                        .filter(ItsmRequestCi.ci_id == ci_id).order_by(ItsmRequest.created_at.desc()).limit(20)):
        out.append({"id": r.id, "reqId": r.req_id, "kind": r.kind, "kindLabel": KIND_LABELS.get(r.kind, r.kind),
                    "subject": r.subject, "status": r.status, "createdAt": to_iso_utc(r.created_at),
                    "match": link.match, "matchedBy": link.matched_by, "url": sdp_url(r.source_key, r.req_id),
                    "requested": {"vcpu": ln.vcpu, "ramGB": ln.ram_gb, "diskGB": ln.disk_gb,
                                  "vcpuBefore": ln.vcpu_before, "ramGBBefore": ln.ram_gb_before, "diskGBBefore": ln.disk_gb_before},
                    "current": {"vcpu": current.get("vcpu"), "ramGB": current.get("ramGB"), "diskGB": current.get("diskProvGB")}})
    return out
