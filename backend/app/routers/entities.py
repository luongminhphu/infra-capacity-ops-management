"""Super app SA3 — tìm kiếm chung (Ctrl + K) and entity pages (thẻ Host / VM): HTTP layer over
services/entities.py (input limits + auth); the logic and the caches live in the service."""
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..security import get_current_user
from ..services import entities as svc

router = APIRouter(prefix="/api", tags=["entities"])


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=80), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hosts, VMs (name / IP / guest hostname), backup clients and — with a spend role — every
    business code (OPX / CPX / HD / BG / CTVP / TT) and vendors. At most 8 hits per type."""
    return svc.search(db, user, q)


@router.get("/entities/host")
def host_card(name: str = Query(min_length=1, max_length=255), vcenter: str = Query(default="", max_length=64),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.host_card(db, user, name, vcenter)


@router.get("/entities/vm")
def vm_card(name: str = Query(min_length=1, max_length=255), vcenter: str = Query(default="", max_length=64),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.vm_card(db, user, name, vcenter)


@router.get("/entities/server/{serial}")
def server_card(serial: str = Path(min_length=1, max_length=128), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Physical server (CI registry) by serial / service tag."""
    return svc.server_card(db, user, serial)


@router.get("/entities/datastore/{ci_id}")
def datastore_card(ci_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Datastore by CI id: capacity, array volume, VMs, impact."""
    return svc.datastore_card(db, user, ci_id)
