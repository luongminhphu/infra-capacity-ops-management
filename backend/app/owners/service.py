"""Hệ thống & Owner — business rules (CRUD, assignment, owner change, coverage). Routers and the Excel import call
this module; every change writes an audit_log row. Callers commit (except where noted)."""
import datetime as dt
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session

from ..cmdb.models import Ci
from ..models import AuditLog, User
from ..shared.text import norm_text
from .models import ENVS, SOURCES, OwnerSequence, System, SystemMember

EMAIL = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$")
ENV_ALIASES = {"production": "prod", "prd": "prod", "test": "uat", "staging": "uat", "development": "dev",
               "disaster recovery": "dr", "khac": "other"}
ENV_LABELS = {"prod": "Production", "uat": "UAT", "dev": "Dev", "dr": "DR", "other": "Khác"}
CRITICALITY_LABELS = {1: "1 — Rất quan trọng", 2: "2 — Quan trọng", 3: "3 — Trung bình", 4: "4 — Thấp"}
LIMITS = {"name": 255, "project": 128, "department": 128, "owner_name": 128, "deputy_name": 128, "cost_center": 64, "note": 500,
          "support_name": 128, "support_contact": 255}
MAX_SUPPORT_EMAILS = 5
MAX_SELECT = 5000                 # "select all" of the unowned list (assign accepts lists this long)
FIELDS = {"name": "name", "project": "project", "department": "department", "ownerEmail": "owner_email",
          "ownerName": "owner_name", "deputyEmail": "deputy_email", "deputyName": "deputy_name", "managerEmail": "manager_email",
          "supportEmails": "support_emails", "supportName": "support_name", "supportContact": "support_contact",
          "costCenter": "cost_center", "criticality": "criticality", "env": "env", "status": "status", "note": "note"}
MAX_ASSIGN = 2000


class Invalid(ValueError):
    """Validation error; the message is Vietnamese and safe to show."""


# ---------------------------------------------------------------- permissions / audit
def can_manage(user: User) -> bool:
    from ..security import is_admin
    if (getattr(user, "access_scope", None) or "full") != "full":      # an owner-portal user never manages systems
        return False
    return is_admin(user) or bool(getattr(user, "owner_admin", False))


def audit(db: Session, user: User, action: str, client_ip: Optional[str] = None, **detail) -> None:
    db.add(AuditLog(username=user.username if user else None, action=action, client_ip=client_ip,
                    detail=json.dumps(detail, ensure_ascii=False, default=str)))


def next_code(db: Session, prefix: str = "SYS") -> str:
    """Reserve the next number inside the caller's transaction (row lock on PostgreSQL; UNIQUE code as backstop)."""
    res = db.execute(update(OwnerSequence).where(OwnerSequence.kind == prefix).values(last_no=OwnerSequence.last_no + 1))
    if res.rowcount == 0:
        db.add(OwnerSequence(kind=prefix, last_no=1))
        db.flush()
        no = 1
    else:
        no = db.query(OwnerSequence.last_no).filter_by(kind=prefix).scalar()
    return f"{prefix}-{no:04d}"


# ---------------------------------------------------------------- validation
def norm_email(v: Any, label: str) -> Optional[str]:
    s = str(v or "").strip().lower()
    if not s:
        return None
    if len(s) > 255 or not EMAIL.match(s):
        raise Invalid(f"{label} không đúng định dạng email: “{str(v)[:80]}”.")
    return s


def norm_emails(v: Any, label: str) -> Optional[str]:
    """A list or a text separated by , ; or spaces → validated, de-duplicated CSV (at most MAX_SUPPORT_EMAILS)."""
    items = v if isinstance(v, (list, tuple)) else re.split(r"[,;\s]+", str(v or ""))
    out: List[str] = []
    for x in items:
        e = norm_email(x, label)
        if e and e not in out:
            out.append(e)
    if len(out) > MAX_SUPPORT_EMAILS:
        raise Invalid(f"{label}: tối đa {MAX_SUPPORT_EMAILS} email.")
    return ",".join(out) or None


def emails_of(csv: Optional[str]) -> List[str]:
    return [e for e in (csv or "").split(",") if e]


def norm_env(v: Any) -> str:
    s = norm_text(str(v or "")) or "prod"
    s = ENV_ALIASES.get(s, s)
    if s not in ENVS:
        raise Invalid(f"Môi trường “{str(v)[:30]}” không hợp lệ (prod, uat, dev, dr, other).")
    return s


def norm_criticality(v: Any) -> int:
    if v in (None, ""):
        return 3
    try:
        n = int(float(str(v).strip().split()[0].rstrip("—-")))
    except (ValueError, IndexError):
        raise Invalid(f"Mức độ quan trọng “{str(v)[:20]}” không hợp lệ (1–4).") from None
    if not 1 <= n <= 4:
        raise Invalid(f"Mức độ quan trọng phải từ 1 (quan trọng nhất) đến 4, không phải {n}.")
    return n


def clean(data: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
    """camelCase payload → validated column values (only the keys present when partial)."""
    out: Dict[str, Any] = {}
    for key, col in FIELDS.items():
        if partial and key not in data:
            continue
        v = data.get(key)
        if col in ("owner_email", "deputy_email", "manager_email"):
            label = {"owner_email": "Email owner", "deputy_email": "Email người thay thế", "manager_email": "Email quản lý trực tiếp"}[col]
            out[col] = norm_email(v, label)
        elif col == "support_emails":
            out[col] = norm_emails(v, "Email support")
        elif col == "criticality":
            out[col] = norm_criticality(v)
        elif col == "env":
            out[col] = norm_env(v)
        elif col == "status":
            s = str(v or "active").strip().lower()
            if s not in ("active", "retired"):
                raise Invalid("Trạng thái phải là active hoặc retired.")
            out[col] = s
        else:
            s = " ".join(str(v or "").split())
            if len(s) > LIMITS[col]:
                raise Invalid(f"“{key}” dài quá {LIMITS[col]} ký tự.")
            out[col] = s or None
    if "name" in out and not out["name"]:
        raise Invalid("Cần tên hệ thống.")
    return out


# ---------------------------------------------------------------- systems
def out(s: System, members: Optional[int] = None) -> Dict[str, Any]:
    return {"id": s.id, "code": s.code, "name": s.name, "project": s.project, "department": s.department,
            "ownerEmail": s.owner_email, "ownerName": s.owner_name, "deputyEmail": s.deputy_email, "deputyName": s.deputy_name,
            "managerEmail": s.manager_email, "supportEmails": emails_of(s.support_emails), "supportName": s.support_name,
            "supportContact": s.support_contact, "costCenter": s.cost_center, "criticality": s.criticality,
            "criticalityLabel": CRITICALITY_LABELS.get(s.criticality, str(s.criticality)), "env": s.env,
            "envLabel": ENV_LABELS.get(s.env, s.env), "status": s.status, "note": s.note,
            "updatedAt": s.updated_at.isoformat() + "Z" if s.updated_at else None, "updatedBy": s.updated_by,
            **({"vmCount": members} if members is not None else {})}


def get(db: Session, system_id: int) -> System:
    s = db.get(System, system_id)
    if s is None:
        raise HTTPException(404, "Không có hệ thống này.")
    return s


def _name_free(db: Session, name: str, except_id: Optional[int] = None) -> str:
    nn = norm_text(name)[:255]
    q = db.query(System.id).filter(System.name_norm == nn)
    if except_id:
        q = q.filter(System.id != except_id)
    if q.first():
        raise Invalid(f"Đã có hệ thống tên “{name}”.")
    return nn


def create(db: Session, user: User, data: Dict[str, Any], ip: Optional[str] = None) -> System:
    vals = clean(data)
    vals["name_norm"] = _name_free(db, vals["name"])
    now = dt.datetime.utcnow()
    s = System(code=next_code(db), created_at=now, created_by=user.username, updated_at=now, updated_by=user.username, **vals)
    db.add(s)
    db.flush()
    audit(db, user, "owners.system.create", ip, id=s.id, code=s.code, name=s.name, owner=s.owner_email)
    invalidate_scope_cache()
    return s


def update_system(db: Session, user: User, s: System, data: Dict[str, Any], ip: Optional[str] = None) -> System:
    vals = clean(data, partial=True)
    if "name" in vals:
        vals["name_norm"] = _name_free(db, vals["name"], s.id)
    changed = {c: [getattr(s, c), v] for c, v in vals.items() if getattr(s, c) != v and c != "name_norm"}
    if not changed:
        return s
    for c, v in vals.items():
        setattr(s, c, v)
    s.updated_at, s.updated_by = dt.datetime.utcnow(), user.username
    # Owner change (chuyển owner) gets its own audit action: G3 reviews follow the owner.
    action = "owners.system.owner_change" if "owner_email" in changed else "owners.system.update"
    audit(db, user, action, ip, id=s.id, code=s.code, changes=changed)
    invalidate_scope_cache()
    return s


def delete(db: Session, user: User, s: System, ip: Optional[str] = None) -> int:
    """Delete a system that has no VM any more. Check and delete are ONE statement (DELETE … WHERE NOT EXISTS member):
    a VM assigned at the same moment makes the delete fail (409) instead of being unassigned silently by the cascade."""
    from sqlalchemy import delete as sa_delete, exists
    n = db.query(func.count(SystemMember.id)).filter(SystemMember.system_id == s.id).scalar() or 0
    if n:
        raise HTTPException(409, f"Hệ thống {s.code} còn {n} VM — chuyển VM sang hệ thống khác hoặc bỏ gán hết rồi mới xoá.")
    code, name, sid = s.code, s.name, s.id
    res = db.execute(sa_delete(System).where(System.id == sid, ~exists().where(SystemMember.system_id == sid))
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise HTTPException(409, f"Hệ thống {code} vừa được gán thêm VM — tải lại rồi thử lại.")
    db.expunge(s)
    audit(db, user, "owners.system.delete", ip, id=sid, code=code, name=name)
    invalidate_scope_cache()
    return 0


def listing(db: Session, q: str = "", owner: str = "", department: str = "", project: str = "",
            criticality: Optional[int] = None, env: str = "", status: str = "active"):
    """Filtered query (ordered, id tiebreak) for paginate()."""
    qry = db.query(System)
    if status in ("active", "retired"):
        qry = qry.filter(System.status == status)
    for col, val in ((System.department, department), (System.project, project)):
        if val:
            qry = qry.filter(func.lower(col) == val.strip().lower())
    if owner:
        o = _like(owner)
        qry = qry.filter(or_(System.owner_email.like(o), System.deputy_email.like(o), func.lower(System.owner_name).like(o),
                             func.lower(System.deputy_name).like(o), System.support_emails.like(o),
                             func.lower(System.support_name).like(o)))
    if criticality:
        qry = qry.filter(System.criticality == criticality)
    if env:
        qry = qry.filter(System.env == env)
    if q:
        t = _like(q)
        qry = qry.filter(or_(func.lower(System.code).like(t), func.lower(System.name).like(t), func.lower(System.project).like(t)))
    return qry.order_by(System.criticality, System.name, System.id)


def _like(q: str) -> str:
    return "%" + str(q).strip().lower().replace("\\", " ").replace("%", " ").replace("_", " ")[:80] + "%"


def member_counts(db: Session, ids: Iterable[int]) -> Dict[int, int]:
    ids = list(ids)
    if not ids:
        return {}
    return dict(db.query(SystemMember.system_id, func.count(SystemMember.id))
                .filter(SystemMember.system_id.in_(ids)).group_by(SystemMember.system_id).all())


def facets(db: Session) -> Dict[str, List[str]]:
    dep = [d for (d,) in db.query(System.department).filter(System.department.isnot(None)).distinct().order_by(System.department)]
    prj = [p for (p,) in db.query(System.project).filter(System.project.isnot(None)).distinct().order_by(System.project)]
    return {"departments": dep[:500], "projects": prj[:500]}


# ---------------------------------------------------------------- members
def _vm_out(c: Ci, m: Optional[SystemMember] = None) -> Dict[str, Any]:
    a = json.loads(c.attrs or "{}")
    return {"ciId": c.id, "vm": c.name, "vcenterKey": a.get("vcenterKey") or "", "cluster": a.get("cluster"), "host": a.get("host"),
            "folderPath": a.get("folderPath"),
            "powerOn": a.get("powerOn"), "vcpu": a.get("vcpu"), "ramGB": a.get("ramGB"), "status": c.status,
            **({"source": m.source, "assignedBy": m.assigned_by,
                "assignedAt": m.assigned_at.isoformat() + "Z" if m.assigned_at else None} if m is not None else {})}


def members(db: Session, system_id: int, limit: int = 2000) -> List[Dict[str, Any]]:
    rows = (db.query(SystemMember, Ci).join(Ci, Ci.id == SystemMember.ci_id).filter(SystemMember.system_id == system_id)
            .order_by(Ci.name, Ci.id).limit(limit).all())
    return [_vm_out(c, m) for m, c in rows]


def _vms(db: Session, ci_ids: List[int]) -> Dict[int, Ci]:
    ids = sorted({int(i) for i in ci_ids})
    if not ids:
        raise Invalid("Chưa chọn VM nào.")
    if len(ids) > MAX_ASSIGN:
        raise Invalid(f"Tối đa {MAX_ASSIGN} VM mỗi lần.")
    found = {c.id: c for c in db.query(Ci).filter(Ci.id.in_(ids), Ci.type == "vm")}
    missing = [i for i in ids if i not in found]
    if missing:
        raise Invalid(f"Không có VM id {', '.join(map(str, missing[:10]))} trong sổ CI.")
    return found


def assign(db: Session, user: User, s: System, ci_ids: List[int], move: bool = False, ip: Optional[str] = None,
           audit_action: str = "owners.member.assign") -> Dict[str, Any]:
    """Manual assignment. A VM already assigned by hand to another system is refused (409) unless `move`;
    an automatic assignment is replaced (manual wins)."""
    vms = _vms(db, ci_ids)
    cur = {m.ci_id: m for m in db.query(SystemMember).filter(SystemMember.ci_id.in_(list(vms)))}
    conflicts = [(cid, m) for cid, m in cur.items() if m.system_id != s.id and m.source == "manual"]
    if conflicts and not move:
        names = {sid: code for sid, code in db.query(System.id, System.code).filter(System.id.in_({m.system_id for _, m in conflicts}))}
        lst = ", ".join(f"{vms[cid].name} ({names.get(m.system_id, '?')})" for cid, m in conflicts[:10])
        raise HTTPException(409, f"{len(conflicts)} VM đã thuộc hệ thống khác: {lst}. Chọn “Chuyển sang hệ thống này” để chuyển.")
    now, added, moved, same = dt.datetime.utcnow(), 0, 0, 0
    for cid in vms:
        m = cur.get(cid)
        if m is None:
            db.add(SystemMember(ci_id=cid, system_id=s.id, source="manual", confidence="strong", assigned_by=user.username, assigned_at=now))
            added += 1
        elif m.system_id == s.id and m.source == "manual":
            same += 1
        else:
            moved += m.system_id != s.id
            same += m.system_id == s.id
            m.system_id, m.source, m.confidence, m.assigned_by, m.assigned_at = s.id, "manual", "strong", user.username, now
    audit(db, user, audit_action, ip, system=s.code, added=added, moved=moved, vms=[vms[c].name for c in list(vms)[:50]])
    invalidate_scope_cache()
    return {"added": added, "moved": moved, "unchanged": same}


def unassign(db: Session, user: User, s: System, ci_ids: List[int], ip: Optional[str] = None) -> int:
    ids = sorted({int(i) for i in ci_ids})[:MAX_ASSIGN]
    n = (db.query(SystemMember).filter(SystemMember.system_id == s.id, SystemMember.ci_id.in_(ids))
         .delete(synchronize_session=False)) if ids else 0
    audit(db, user, "owners.member.unassign", ip, system=s.code, removed=n, ciIds=ids[:50])
    invalidate_scope_cache()
    return n


def assign_auto(db: Session, ci_id: int, system_id: int, source: str, confidence: str = "weak") -> str:
    """Automatic assignment (attribute / tag / rule / itsm, from G2b): never overrides a manual one.
    Returns "added" | "updated" | "kept_manual" | "unchanged"."""
    if source not in SOURCES or source == "manual":
        raise ValueError("automatic source expected")
    invalidate_scope_cache()
    m = db.query(SystemMember).filter(SystemMember.ci_id == ci_id).first()
    if m is None:
        db.add(SystemMember(ci_id=ci_id, system_id=system_id, source=source, confidence=confidence, assigned_by=f"auto:{source}"))
        return "added"
    if m.source == "manual":
        return "kept_manual"
    if (m.system_id, m.source, m.confidence) == (system_id, source, confidence):
        return "unchanged"
    m.system_id, m.source, m.confidence, m.assigned_by, m.assigned_at = system_id, source, confidence, f"auto:{source}", dt.datetime.utcnow()
    return "updated"


def system_of_vm(db: Session, ci_id: int) -> Optional[Dict[str, Any]]:
    row = (db.query(SystemMember, System).join(System, System.id == SystemMember.system_id)
           .filter(SystemMember.ci_id == ci_id).first())
    if row is None:
        return None
    m, s = row
    return {**out(s), "source": m.source, "assignedBy": m.assigned_by,
            "assignedAt": m.assigned_at.isoformat() + "Z" if m.assigned_at else None}


# ---------------------------------------------------------------- coverage (tab "VM chưa có owner")
def coverage(db: Session, q: str = "", vcenter: str = "", offset: int = 0, limit: int = 100, cluster: str = "",
             power: str = "", folder: str = "", ids: bool = False) -> Dict[str, Any]:
    """Active VMs of the CI registry: with an owner (system with owner e-mail), in a system without owner, unassigned.
    The unassigned list is filtered by name (several terms separated by commas = any of them; * = any text), vCenter and
    cluster (comma-separated = any of them), power (on / off) and folder (substring). `facets` count the unassigned VMs per
    vCenter / cluster (before those two filters) for the filter menus; ids=True also returns every matching CI id
    (select all, at most MAX_SELECT)."""
    total = db.query(func.count(Ci.id)).filter(Ci.type == "vm", Ci.status == "active").scalar() or 0
    rows = (db.query(System.owner_email, func.count(SystemMember.id)).join(SystemMember, SystemMember.system_id == System.id)
            .join(Ci, Ci.id == SystemMember.ci_id).filter(Ci.type == "vm", Ci.status == "active").group_by(System.owner_email).all())
    owned = sum(n for email, n in rows if email)
    no_owner = sum(n for email, n in rows if not email)
    unq = (db.query(Ci).outerjoin(SystemMember, SystemMember.ci_id == Ci.id)
           .filter(Ci.type == "vm", Ci.status == "active", SystemMember.id.is_(None)))
    terms = [t.strip().lower() for t in (q or "").split(",") if t.strip()][:20]
    if terms:
        unq = unq.filter(or_(*[func.lower(Ci.name).like(_like(t).replace("*", "%")) for t in terms]))
    vms = [_vm_out(c) for c in unq.order_by(Ci.name, Ci.id).all()]
    if power in ("on", "off"):
        vms = [v for v in vms if bool(v["powerOn"]) == (power == "on")]
    if folder.strip():
        f = folder.strip().lower()
        vms = [v for v in vms if f in (v.get("folderPath") or "").lower()]
    vcs = {x for x in (vcenter or "").split(",") if x}
    cls = {x for x in (cluster or "").split(",") if x}
    facets_vc: Dict[str, int] = {}
    facets_cl: Dict[str, int] = {}
    for v in vms:                                   # menus: counts with the other filters applied
        if not cls or (v["cluster"] or "") in cls:
            facets_vc[v["vcenterKey"]] = facets_vc.get(v["vcenterKey"], 0) + 1
        if not vcs or v["vcenterKey"] in vcs:
            facets_cl[v["cluster"] or ""] = facets_cl.get(v["cluster"] or "", 0) + 1
    if vcs:
        vms = [v for v in vms if v["vcenterKey"] in vcs]
    if cls:
        vms = [v for v in vms if (v["cluster"] or "") in cls]
    return {"total": total, "owned": owned, "inSystemNoOwner": no_owner, "unassigned": total - owned - no_owner,
            "ownedPct": round(owned * 100.0 / total, 1) if total else None,
            "items": vms[offset:offset + limit], "itemsTotal": len(vms),
            "facets": {"vcenters": [{"value": k, "count": n} for k, n in sorted(facets_vc.items())],
                       "clusters": [{"value": k, "count": n} for k, n in sorted(facets_cl.items(), key=lambda x: x[0].lower())]},
            **({"ids": [v["ciId"] for v in vms[:MAX_SELECT]]} if ids else {})}


def find_vms(db: Session, pairs: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], List[Ci]]:
    """(vm name lower, vcenter key) → active VM CIs (vcenter "" = any). One query for all names (Excel import)."""
    pairs = list(pairs)
    names = sorted({n for n, _ in pairs if n})
    by_name: Dict[str, List[Ci]] = {}
    for i in range(0, len(names), 500):
        for c in db.query(Ci).filter(Ci.type == "vm", Ci.status == "active", func.lower(Ci.name).in_(names[i:i + 500])):
            by_name.setdefault(c.name.lower(), []).append(c)
    out_: Dict[Tuple[str, str], List[Ci]] = {}
    for n, vc in pairs:
        cands = by_name.get(n, [])
        if vc:
            cands = [c for c in cands if (json.loads(c.attrs or "{}").get("vcenterKey") or "").lower() == vc.lower()]
        out_[(n, vc)] = cands
    return out_


# ---------------------------------------------------------------- owner scope → VMs (G2c), cached per round
import threading as _threading  # noqa: E402
import time as _time  # noqa: E402

SCOPE_TTL = 60.0
_scope_cache: Dict[Any, Tuple[float, Dict[str, Any]]] = {}
_scope_lock = _threading.Lock()


def invalidate_scope_cache() -> None:
    """Called after any change to systems / assignments (here, rules.apply, the CI build)."""
    with _scope_lock:
        _scope_cache.clear()


def scope_systems(db: Session, sc, *cols):
    """Active systems matching an OwnerScope (every given dimension ANDed; owners match owner or backup e-mail).
    `cols` = what to select (default: the System rows)."""
    q = db.query(*(cols or (System,))).filter(System.status == "active")
    if sc is None:
        return q
    if sc.systems is not None:
        ids = [int(x) for x in sc.systems if x.isdigit()]
        codes = [x.upper() for x in sc.systems if not x.isdigit()]
        q = q.filter(or_(System.id.in_(ids or [-1]), System.code.in_(codes or [""])))
    if sc.owners is not None:
        q = q.filter(or_(func.lower(System.owner_email).in_(sc.owners or [""]), func.lower(System.deputy_email).in_(sc.owners or [""])))
    if sc.departments is not None:
        q = q.filter(func.lower(System.department).in_([d.lower() for d in sc.departments] or [""]))
    if sc.projects is not None:
        q = q.filter(func.lower(System.project).in_([p.lower() for p in sc.projects] or [""]))
    if sc.criticality is not None:
        q = q.filter(System.criticality.in_(sc.criticality or [-1]))
    return q


def resolve_vm_scope(db: Session, sc) -> Dict[str, Any]:
    """Systems matching the owner filter → their VMs as {(vcenterKey, vm name lower)} / {(vcenterKey, instanceUuid)}."""
    from ..cmdb.models import CiKey
    key = (tuple(sc.systems or ()) if sc.systems is not None else None, tuple(sc.owners or ()) if sc.owners is not None else None,
           tuple(sc.departments or ()) if sc.departments is not None else None,
           tuple(sc.projects or ()) if sc.projects is not None else None,
           tuple(sc.criticality or ()) if sc.criticality is not None else None)
    now = _time.monotonic()
    with _scope_lock:
        hit = _scope_cache.get(key)
        if hit and now - hit[0] < SCOPE_TTL:
            return hit[1]
    sids = [i for (i,) in scope_systems(db, sc, System.id)]
    names, uuids, n = set(), set(), 0
    if sids:
        rows = (db.query(Ci.id, Ci.name, Ci.attrs).join(SystemMember, SystemMember.ci_id == Ci.id)
                .filter(SystemMember.system_id.in_(sids), Ci.type == "vm").all())
        vc_of = {}
        for cid, name, attrs in rows:
            vc = json.loads(attrs or "{}").get("vcenterKey") or ""
            vc_of[cid] = vc
            names.add((vc, (name or "").lower()))
        n = len(rows)
        ids = list(vc_of)
        for i in range(0, len(ids), 500):
            for cid, val in db.query(CiKey.ci_id, CiKey.value_norm).filter(CiKey.ci_id.in_(ids[i:i + 500]), CiKey.kind == "instance_uuid"):
                uuids.add((vc_of[cid], val.lower()))
    out = {"names": names, "uuids": uuids, "vms": n, "systems": len(sids)}
    with _scope_lock:
        _scope_cache[key] = (_time.monotonic(), out)
    return out
