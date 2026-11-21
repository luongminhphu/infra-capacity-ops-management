"""Nghẽn tài nguyên (ICO v2 G9c): latest CPU ready / co-stop / balloon / swap / disk latency per VM / host, cluster findings,
history of one VM / host from metrics.py. Read-only API (every signed-in user)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..contention import service
from ..database import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/contention", tags=["contention"])


@router.get("")
def report(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.report(db)


@router.get("/history")
def history(kind: str = Query(pattern="^(vm|host)$"), name: str = Query(min_length=1, max_length=255),
            vc: str = Query(default="", max_length=64), days: int = Query(default=7, ge=1, le=90),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.history(db, kind, name, vc, days)
