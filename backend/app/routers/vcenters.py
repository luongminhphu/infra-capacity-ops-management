
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import data_access
from ..config import get_settings
from ..collectors.locks import SourceBusy
from ..collectors.scheduler import sync_vcenter, sync_vcenters, vcenter_result
from ..database import get_db
from ..models import SnapshotRun, User
from ..security import get_current_user, is_admin, require_admin
from ..timeutil import to_iso_utc
from ..vcenter.client import VCenterConnectionError

logger = logging.getLogger("vco.routers.vcenters")

router = APIRouter(prefix="/api/vcenters", tags=["vcenters"])


def _vcenter_public(cfg, db: Session, admin: bool = False):
    last_run = data_access.latest_run_for_key(db, cfg.key, source="live")
    last_error_run = (
        db.query(SnapshotRun)
        .filter(SnapshotRun.vcenter_key == cfg.key, SnapshotRun.status == "error")
        .order_by(SnapshotRun.id.desc())
        .first()
    )
    return {
        "key": cfg.key,
        "name": cfg.name,
        "host": cfg.host,
        "demo": cfg.demo,
        "lastSyncAt": to_iso_utc(last_run.finished_at) if last_run else None,
        "lastStatus": "ok" if last_run else None,
        # error text can name hosts / ports of the source: admins only (they are the ones who can act on it)
        "lastError": (last_error_run.error_message if admin else "Lần đồng bộ gần nhất bị lỗi.")
        if last_error_run and (not last_run or last_error_run.id > last_run.id) else None,
    }


@router.get("")
def list_vcenters(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings()
    return [_vcenter_public(cfg, db, is_admin(user)) for cfg in settings.vcenters()]


@router.post("/{key}/sync")
def sync_one(key: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    settings = get_settings()
    cfg = next((c for c in settings.vcenters() if c.key == key), None)
    if not cfg:
        raise HTTPException(status_code=404, detail="Không tìm thấy vCenter với key này")
    try:
        run = sync_vcenter(db, cfg, settings)
    except SourceBusy as exc:            # the scheduler (or another click) is syncing it right now
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VCenterConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — details go to the log, not to the client
        logger.exception("vCenter sync failed (key=%s)", key)
        raise HTTPException(status_code=502, detail="Đồng bộ thất bại. Xem log máy chủ để biết chi tiết.") from exc
    out = vcenter_result(cfg, run)
    return {**out, "finishedAt": to_iso_utc(out["finishedAt"])}


@router.post("/sync-all")
def sync_all(user: User = Depends(require_admin)):
    # Every vCenter in parallel, each in its own DB session.
    results = sync_vcenters(get_settings())
    return {"results": [{**r, "finishedAt": to_iso_utc(r["finishedAt"])} if r.get("ok") else r for r in results]}
