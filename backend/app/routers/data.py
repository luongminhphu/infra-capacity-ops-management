from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import data_access
from ..database import get_db
from ..models import User
from ..scope import OwnerScope, owner_scope_query
from ..security import get_current_user

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/latest")
def latest_data(
    vcenter: Optional[str] = Query(default="all"),
    include_import: bool = Query(default=True),
    group_by_vcenter: bool = Query(default=False),
    osc: OwnerScope = Depends(owner_scope_query),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Raw normalized host/vm rows (same camelCase shape the original
    single-file app's normalize functions produce) for the requested scope, so
    the frontend can reuse its existing buildCapacityModel/buildHostRows/etc.
    JS pipeline unchanged regardless of whether the data came from a live
    vCenter poll or a manual CSV/Excel import."""
    if group_by_vcenter:
        grouped = data_access.get_latest_grouped(db, include_import=include_import)
        if osc.active:
            grouped["hosts"], grouped["vms"] = osc.filter_rows(db, grouped.get("hosts") or [], grouped.get("vms") or [])
            for part in (grouped.get("byVcenter") or {}).values():
                part["hosts"], part["vms"] = osc.filter_rows(db, part.get("hosts") or [], part.get("vms") or [])
            grouped["ownerScope"] = osc.describe(db)
        return grouped
    keys = None if (not vcenter or vcenter == "all") else [vcenter]
    host_rows, vm_rows, run_meta = data_access.get_latest_combined(db, keys, include_import=include_import)
    host_rows, vm_rows = osc.filter_rows(db, host_rows, vm_rows)
    return {"hosts": host_rows, "vms": vm_rows, "runMeta": run_meta, "ownerScope": osc.describe(db) or None}
