"""Vận hành → Phần cứng (ICO v2 G4c): Dell OME servers, list → detail, PDF / Excel "Sức khoẻ phần cứng".
Read-only over the database (hardware/service.py); any signed-in user of the full app (get_current_user); the owner
filter (G2c) keeps the servers running a VM of the selected systems; Opex codes only for users who may see Chi phí."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..downloads import attachment
from ..features import get_features
from ..hardware import service
from ..models import User
from ..scope import OwnerScope, owner_scope_query
from ..security import get_current_user
from ..spend.fiscal import today_vn

router = APIRouter(prefix="/api/hardware", tags=["hardware"])
LEVEL_RX = "^(crit|bad|warn|ok)$"


def _opex_ok(db: Session, user: User) -> bool:
    from ..services.entities import _spend_ok
    return _spend_ok(user, get_features(db))


def _listing(db: Session, user: User, osc: OwnerScope, status: Optional[str], model: Optional[str], warranty_days: Optional[int]):
    out = service.listing(db, today_vn(), osc, status, model, warranty_days, _opex_ok(db, user))
    out["ownerScope"] = osc.describe(db) if osc.active else ""
    return out


@router.get("")
def servers(status: Optional[str] = Query(default=None, pattern=LEVEL_RX), model: Optional[str] = Query(default=None, max_length=128),
            warrantyDays: Optional[int] = Query(default=None, ge=0, le=3650), osc: OwnerScope = Depends(owner_scope_query),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _listing(db, user, osc, status, model, warrantyDays)


@router.get("/servers/{source_key}/{device_id}")
def server_detail(source_key: str, device_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.detail(db, source_key[:64], device_id, today_vn(), _opex_ok(db, user))


@router.get("/report.pdf")
def report_pdf(status: Optional[str] = Query(default=None, pattern=LEVEL_RX), model: Optional[str] = Query(default=None, max_length=128),
               warrantyDays: Optional[int] = Query(default=None, ge=0, le=3650), osc: OwnerScope = Depends(owner_scope_query),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..report_hardware import build_pdf
    data = _listing(db, user, osc, status, model, warrantyDays)
    return attachment(build_pdf(data, user.display_name or user.username), "ICO_Suc_khoe_phan_cung.pdf")


@router.get("/report.xlsx")
def report_xlsx(status: Optional[str] = Query(default=None, pattern=LEVEL_RX), model: Optional[str] = Query(default=None, max_length=128),
                warrantyDays: Optional[int] = Query(default=None, ge=0, le=3650), osc: OwnerScope = Depends(owner_scope_query),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..excel_hardware import build_xlsx
    data = _listing(db, user, osc, status, model, warrantyDays)
    data["firmwareRows"] = service.firmware_rows(db, [(i["sourceKey"], i["deviceId"]) for i in data["items"]])
    return attachment(build_xlsx(data), "ICO_Suc_khoe_phan_cung.xlsx")
