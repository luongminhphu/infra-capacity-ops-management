"""One scope model for every page and report (super app): which vCenter, which clusters, which hosts.

Query conventions shared by the API:
* `vcenter`  — "all" / empty = every vCenter; a key = that vCenter ("import" = the manual import).
* `clusters` / `hosts` — comma separated. Omitted = no filter; given but empty = filter to nothing
  (a deliberate zero-item selection in the UI)."""
from dataclasses import dataclass
from typing import List, Optional

from fastapi import Query


def split_csv(value: Optional[str]) -> Optional[List[str]]:
    """None when the parameter is omitted; a list (possibly empty) when it is given."""
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def scope_keys(vcenter: Optional[str]) -> Optional[List[str]]:
    """vCenter keys for data_access.get_latest_combined (None = every vCenter). The manual import is
    always merged by get_latest_combined, so "import" needs no special key."""
    return None if (not vcenter or vcenter == "all") else [vcenter]


def scope_label(vcenter: Optional[str]) -> str:
    return "Toàn bộ vCenter" if (not vcenter or vcenter == "all") else ("Dữ liệu nhập tay" if vcenter == "import" else vcenter)


@dataclass
class Scope:
    vcenter: Optional[str] = None
    clusters: Optional[List[str]] = None
    hosts: Optional[List[str]] = None

    @classmethod
    def from_query(cls, vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None) -> "Scope":
        return cls(vcenter=vcenter if vcenter and vcenter != "all" else None, clusters=split_csv(clusters), hosts=split_csv(hosts))

    @property
    def keys(self) -> Optional[List[str]]:
        return scope_keys(self.vcenter)

    @property
    def label(self) -> str:
        parts = [scope_label(self.vcenter)]
        if self.clusters is not None:
            parts.append(f"cluster: {', '.join(self.clusters) or '(không chọn)'}")
        if self.hosts is not None:
            parts.append(f"host: {', '.join(self.hosts) or '(không chọn)'}")
        return " · ".join(parts)


# ---------------------------------------------------------------- owner scope (ICO v2 G2c)
@dataclass
class OwnerScope:
    """Filter by business system / owner: systems (ids or SYS codes), owners (e-mail), departments, projects,
    criticality — every given dimension must match (AND), several values in one dimension match any (OR).
    Inactive = no owner filter at all. Resolved to a set of VMs by owners/service.resolve_vm_scope (cached per round)."""
    systems: Optional[List[str]] = None
    owners: Optional[List[str]] = None
    departments: Optional[List[str]] = None
    projects: Optional[List[str]] = None
    criticality: Optional[List[int]] = None

    @classmethod
    def from_query(cls, systems: Optional[str] = None, owners: Optional[str] = None, departments: Optional[str] = None,
                   projects: Optional[str] = None, criticality: Optional[str] = None) -> "OwnerScope":
        crit = split_csv(criticality)
        return cls(systems=_limit(split_csv(systems)), owners=_limit([o.lower() for o in split_csv(owners) or []] if owners else None),
                   departments=_limit(split_csv(departments)), projects=_limit(split_csv(projects)),
                   criticality=[int(c) for c in crit if c.isdigit() and 1 <= int(c) <= 4] if crit is not None else None)

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.systems, self.owners, self.departments, self.projects, self.criticality))

    @property
    def label(self) -> str:
        return self._label(self.systems)

    def describe(self, db) -> str:
        """Like label, with SYS codes instead of numeric ids (for PDF / Excel scope lines and the page banner)."""
        if not self.systems:
            return self.label
        from .owners.models import System
        ids = [int(x) for x in self.systems if x.isdigit()]
        codes = dict(db.query(System.id, System.code).filter(System.id.in_(ids))) if ids else {}
        return self._label([codes.get(int(x), x) if x.isdigit() else x.upper() for x in self.systems])

    def _label(self, systems) -> str:
        parts = []
        for name, vals in (("hệ thống", systems), ("owner", self.owners), ("phòng ban", self.departments),
                           ("dự án", self.projects), ("mức quan trọng", self.criticality)):
            if vals is not None:
                shown = ", ".join(str(v) for v in vals[:5]) + (f" và {len(vals) - 5} khác" if len(vals) > 5 else "")
                parts.append(f"{name}: {shown or '(không chọn)'}")
        return "Đang lọc theo owner — " + " · ".join(parts) if parts else ""

    def resolve(self, db):
        """None when inactive, else {"names": {(vcenterKey, vm lower)}, "uuids": {(vcenterKey, uuid)}, "vms", "systems"}."""
        if not self.active:
            return None
        from .owners.service import resolve_vm_scope
        return resolve_vm_scope(db, self)

    def filter_vms(self, db, vms: List[dict]) -> List[dict]:
        r = self.resolve(db)
        return vms if r is None else [v for v in vms if in_vm_scope(r, v)]

    def filter_rows(self, db, hosts: List[dict], vms: List[dict]):
        """Keep the VMs in scope and the clusters that still have one (their hosts stay: capacity is measured against
        the whole cluster). capacity.py and core.js then run unchanged on the subset, so both keep the same numbers."""
        r = self.resolve(db)
        if r is None:
            return hosts, vms
        kept = [v for v in vms if in_vm_scope(r, v)]
        clusters = {((v.get("vcenterKey") or ""), v.get("cluster")) for v in kept}
        return [h for h in hosts if ((h.get("vcenterKey") or ""), h.get("cluster")) in clusters], kept


MAX_SCOPE_VALUES = 200


def _limit(vals: Optional[List[str]]) -> Optional[List[str]]:
    return None if vals is None else [v[:255] for v in vals[:MAX_SCOPE_VALUES]]


def in_vm_scope(resolved: dict, v: dict) -> bool:
    vc = v.get("vcenterKey") or ""
    uuid = str(v.get("instanceUuid") or "").strip().lower()
    return (uuid and (vc, uuid) in resolved["uuids"]) or (vc, str(v.get("vm") or "").lower()) in resolved["names"]


def owner_scope_query(systems: Optional[str] = Query(default=None, max_length=4000),
                      owners: Optional[str] = Query(default=None, max_length=4000),
                      departments: Optional[str] = Query(default=None, max_length=4000),
                      projects: Optional[str] = Query(default=None, max_length=4000),
                      criticality: Optional[str] = Query(default=None, max_length=20)) -> OwnerScope:
    """FastAPI dependency: the owner filter of a page / report request."""
    return OwnerScope.from_query(systems, owners, departments, projects, criticality)
