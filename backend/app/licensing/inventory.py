"""Hardware / guest-OS inventory with the OS classification (overrides applied): shared by
Tài sản → ESXi & Hệ điều hành, the License module, the VM card and the AI OS suggestions."""
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .. import data_access
from ..models import VmOsOverride
from ..scope import scope_keys as _scope_keys
from ..timeutil import to_iso_utc
from . import assess as assess_mod
from . import os_classify


def _parse_clusters(cluster: Optional[str], clusters: Optional[Any]) -> Dict[str, Any]:
    """Normalise the two ways a cluster scope can arrive:
    - `cluster`  : single name from the global filter (legacy, name-only);
    - `clusters` : comma-separated string or list of "vcenterKey::cluster"
                   keys chosen in the License tab (purchase scope, may be several).
    Returns {"clusterKeys": [...] | None, "clusters": [names] | None}."""
    keys: List[str] = []
    names: List[str] = []
    items: List[str] = []
    if isinstance(clusters, str):
        items = [x.strip() for x in clusters.split(",") if x.strip()]
    elif isinstance(clusters, (list, tuple)):
        items = [str(x).strip() for x in clusters if str(x).strip()]
    for it in items:
        if "::" in it:
            keys.append(it)
            names.append(it.split("::", 1)[1])
        else:
            names.append(it)
    if cluster and cluster != "all":
        # global filter narrows further: keep only selected keys matching that name
        if keys:
            keys = [k for k in keys if k.split("::", 1)[1] == cluster] or [f"::{cluster}"]
            names = [cluster]
        else:
            names = [cluster]
    if not keys and not names:
        return {"clusterKeys": None, "clusters": None}
    return {"clusterKeys": keys or None, "clusters": sorted(set(names)) or None}


def _filter_scope(rows: List[Dict[str, Any]], sc: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not sc.get("clusterKeys") and not sc.get("clusters"):
        return rows
    return [r for r in rows if assess_mod.cluster_in_scope(r, sc)]


def _overrides_map(db: Session) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    # Oldest first so the newest override wins if pre-index duplicates exist (DAT-08).
    for o in db.query(VmOsOverride).order_by(VmOsOverride.updated_at.asc(), VmOsOverride.id.asc()).all():
        d = _override_dict(o)
        out[os_classify.override_key(o.vcenter_key, o.instance_uuid, o.vm)] = d
    return out


def _override_dict(o: VmOsOverride) -> Dict[str, Any]:
    return {"id": o.id, "vcenterKey": o.vcenter_key, "instanceUuid": o.instance_uuid, "vm": o.vm,
            "category": o.category, "osVersion": o.os_version, "osEdition": o.os_edition, "note": o.note,
            "updatedBy": o.updated_by, "updatedAt": to_iso_utc(o.updated_at)}


def _load_inventory(db: Session, vcenter: Optional[str], cluster: Optional[str] = None, clusters: Optional[Any] = None):
    hosts, vms, run_meta = data_access.get_latest_combined(db, _scope_keys(vcenter), include_import=True)
    sc = _parse_clusters(cluster, clusters)
    all_clusters = sorted({f"{h.get('vcenterKey') or ''}::{h.get('cluster') or ''}" for h in hosts})
    hosts = _filter_scope(hosts, sc)
    vms = _filter_scope(vms, sc)
    classified = os_classify.classify_all(vms, _overrides_map(db))
    return hosts, classified, {"runs": run_meta, "allClusterKeys": all_clusters}


