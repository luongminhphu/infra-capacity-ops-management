"""IP of a VM next to its name, everywhere a VM is listed (rule of the product owner: no VM row without its IP).

The VMware name of a VM often differs from the hostname its system owner knows; the IP (VMware Tools, guest IP) is
what lets them recognise it. One index (vCenter key, VM name lower) → IP from the latest successful run of every
vCenter (+ the manual import), cached for CACHE_S seconds per process — the pages and reports call it once per
response, never per row.

attach(db, rows) adds `ip` to dict rows that lack it (keys: vm / vcenterKey by default) and returns the rows.
One build at a time per process (_build_lock): concurrent requests after expiry wait for it instead of each
re-reading the latest snapshot."""
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

CACHE_S = 60
_lock = threading.Lock()
_build_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "idx": None}


def invalidate() -> None:
    with _lock:
        _cache.update(at=0.0, idx=None)


def _cached() -> Optional[Dict[Tuple[str, str], str]]:
    with _lock:
        if _cache["idx"] is not None and time.monotonic() - _cache["at"] < CACHE_S:
            return _cache["idx"]
    return None


def index(db: Session) -> Dict[Tuple[str, str], str]:
    hit = _cached()
    if hit is not None:
        return hit
    with _build_lock:
        hit = _cached()                                       # built by another thread while this one waited
        return hit if hit is not None else _build(db)


def _build(db: Session) -> Dict[Tuple[str, str], str]:
    from . import data_access
    _, vms, _ = data_access.get_latest_combined(db, None)
    idx: Dict[Tuple[str, str], str] = {}
    for v in vms:
        ip = (v.get("ipAddress") or "").strip()
        name = (v.get("vm") or "").strip().lower()
        if ip and name:
            idx[(v.get("vcenterKey") or "", name)] = ip
            idx.setdefault(("", name), ip)            # lookups without vCenter (backup clients, imports): first match
    with _lock:
        _cache.update(at=time.monotonic(), idx=idx)
    return idx


def ip_of(idx: Dict[Tuple[str, str], str], vm: Optional[str], vcenter_key: Optional[str] = None) -> Optional[str]:
    name = (vm or "").strip().lower()
    if not name:
        return None
    hit = idx.get((vcenter_key or "", name)) or (idx.get(("", name)) if not vcenter_key else None)
    if hit or vcenter_key or "." not in name:
        return hit
    from .shared.identity import short_name
    return idx.get(("", short_name(name)))                   # FQDN client name → VM short name


def attach(db: Session, rows: List[Dict[str, Any]], vm_key: str = "vm", vc_key: Optional[str] = "vcenterKey", field: str = "ip",
           only: Optional[Callable[[Dict[str, Any]], bool]] = None) -> List[Dict[str, Any]]:
    """Set row[field] from the index when missing; returns `rows`. vc_key=None: rows without a vCenter (backup clients),
    matched on the name alone. `only(row)` limits it to some rows (e.g. VM alarms)."""
    idx = None
    for r in rows:
        if r.get(field) or (only is not None and not only(r)):
            continue
        if idx is None:
            idx = index(db)
        r[field] = ip_of(idx, r.get(vm_key), r.get(vc_key) if vc_key else None)
    return rows
