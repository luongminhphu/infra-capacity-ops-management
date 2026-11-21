"""Vận hành → Sự cố (ICO v2 G5): incidents from the event store, their timeline, ack / assign / resolve, MTTA / MTTR."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..events import service
from ..models import User
from ..scope import OwnerScope, owner_scope_query
from ..security import get_current_user

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.get("")
def incidents(status: Optional[str] = Query(default=None, pattern="^(active|open|acked|resolved)$"),
              level: Optional[str] = Query(default=None, pattern="^(warn|bad|crit)$"),
              source: Optional[str] = Query(default=None, max_length=32), days: Optional[int] = Query(default=None, ge=1, le=400),
              osc: OwnerScope = Depends(owner_scope_query), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.list_incidents(db, status, level, source, days, osc)


@router.get("/stats")
def incident_stats(months: int = Query(default=6, ge=1, le=13), osc: OwnerScope = Depends(owner_scope_query),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.stats(db, months, osc)


@router.get("/{iid}")
def incident(iid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.incident_detail(db, iid)


@router.post("/{iid}/{action}")
def incident_action(iid: int, request: Request, action: str = Path(pattern="^(ack|assign|resolve)$"),
                    data: Dict[str, Any] = Body(default={}), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """ack / assign {assignee} / resolve {note?} — audit_log records who did it."""
    text = lambda k: str(data[k]) if isinstance(data.get(k), (str, int)) else None      # noqa: E731
    return service.act(db, user, iid, action, assignee=text("assignee"), note=text("note"),
                       ip=request.client.host if request.client else None)
