"""VMs per business system — ONE source of numbers for the "Danh sách VM theo hệ thống" PDF, its Excel and the
owner review e-mail drafts (owners/mail_drafts.py).

build(db, osc) → {"systems": [...], "unowned": [...] | None, "totals": {...}, "scope": str, "backupMonitored": bool}
* Systems: active systems of the owner filter (scope_systems), with owner / backup / support / manager and their VMs.
* VM rows: CI registry (name, vCenter, cluster, host, power, vCPU, RAM) enriched with the latest collected row
  (disk, guest OS, IP, datastore) and the backup state (protected / failing / unprotected, None = not monitored).
* Unowned VMs (active, in no system) only without an owner filter — a filtered report is about those systems.
"""
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..cmdb.models import Ci
from . import review, service
from .models import System, SystemMember

BACKUP_LABELS = {"protected": "Có backup", "failing": "Backup lỗi", "unprotected": "Chưa backup", None: "—"}


def _vm(c: Ci, by_name: dict, backup: Optional[dict]) -> Dict[str, Any]:
    a = json.loads(c.attrs or "{}")
    vc = a.get("vcenterKey") or ""
    key = (vc, (c.name or "").lower())
    row = by_name.get(key) or {}
    power = row.get("isPoweredOn") if row else a.get("powerOn")
    if backup is None:
        b = None
    else:
        b = backup.get(key) or ("protected" if power else None)     # powered-off VMs are not expected to be backed up
    return {"ciId": c.id, "vm": c.name, "vcenterKey": vc, "cluster": row.get("cluster") or a.get("cluster"),
            "host": row.get("host") or a.get("host"), "powerOn": bool(power),
            "vcpu": row.get("vcpu") if row.get("vcpu") is not None else a.get("vcpu"),
            "ramGB": row.get("ramGB") if row.get("ramGB") is not None else a.get("ramGB"),
            "diskGB": row.get("diskProvGB"), "os": row.get("guestFullName") or row.get("toolsGuestFullName"),
            "ip": row.get("ipAddress"), "hostname": row.get("guestHostname"), "datastore": row.get("datastore"), "backup": b, "backupLabel": BACKUP_LABELS.get(b, "—"),
            "status": c.status}


def _totals(vms: List[Dict[str, Any]]) -> Dict[str, Any]:
    num = lambda k: round(sum(float(v.get(k) or 0) for v in vms), 1)           # noqa: E731
    return {"vms": len(vms), "on": sum(1 for v in vms if v["powerOn"]), "off": sum(1 for v in vms if not v["powerOn"]),
            "vcpu": num("vcpu"), "ramGB": num("ramGB"), "diskGB": num("diskGB"),
            "unprotected": sum(1 for v in vms if v["backup"] == "unprotected"),
            "failing": sum(1 for v in vms if v["backup"] == "failing")}


def system_out(s: System) -> Dict[str, Any]:
    return {"id": s.id, "code": s.code, "name": s.name, "project": s.project, "department": s.department,
            "env": s.env, "envLabel": service.ENV_LABELS.get(s.env, s.env), "criticality": s.criticality,
            "criticalityLabel": service.CRITICALITY_LABELS.get(s.criticality, str(s.criticality)),
            "ownerEmail": s.owner_email, "ownerName": s.owner_name, "backupEmail": s.deputy_email, "backupName": s.deputy_name,
            "managerEmail": s.manager_email, "supportEmails": service.emails_of(s.support_emails), "supportName": s.support_name,
            "supportContact": s.support_contact, "costCenter": s.cost_center}


def build(db: Session, osc=None, include_unowned: bool = True, owner: Optional[str] = None) -> Dict[str, Any]:
    """owner (e-mail, lower case): only the systems of that owner — one e-mail draft, no rebuild of every system."""
    by_name, _ = review._vm_rows(db)
    backup = review._backup_states(db)
    active = bool(osc is not None and osc.active)
    systems = service.scope_systems(db, osc if active else None).order_by(System.code).all()
    if owner:
        systems = [s for s in systems if (s.owner_email or "").lower() == owner]
    sids = [s.id for s in systems]
    members: Dict[int, List[Ci]] = {i: [] for i in sids}
    for i in range(0, len(sids), 500):
        for sid, c in (db.query(SystemMember.system_id, Ci).join(Ci, Ci.id == SystemMember.ci_id)
                       .filter(SystemMember.system_id.in_(sids[i:i + 500]), Ci.type == "vm", Ci.status == "active")):
            members[sid].append(c)
    out = []
    for s in systems:
        vms = sorted((_vm(c, by_name, backup) for c in members[s.id]), key=lambda v: (v["vm"] or "").lower())
        out.append({**system_out(s), "vms": vms, "totals": _totals(vms)})
    unowned = None
    if include_unowned and not active:
        rows = (db.query(Ci).outerjoin(SystemMember, SystemMember.ci_id == Ci.id)
                .filter(Ci.type == "vm", Ci.status == "active", SystemMember.id.is_(None)).order_by(Ci.name, Ci.id).all())
        unowned = [_vm(c, by_name, backup) for c in rows]
    all_vms = [v for s in out for v in s["vms"]]
    totals = {**_totals(all_vms), "systems": len(out), "noOwner": sum(1 for s in out if not s["ownerEmail"]),
              "noVm": sum(1 for s in out if not s["vms"]), "unowned": len(unowned) if unowned is not None else None}
    return {"systems": out, "unowned": unowned, "totals": totals, "scope": osc.describe(db) if active else "",
            "backupMonitored": backup is not None}
