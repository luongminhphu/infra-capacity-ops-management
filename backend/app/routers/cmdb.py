"""Sổ CI (ICO v2 G1b) — read only, every signed-in user; parts hidden by feature / role (cmdb/query.py).

GET /api/ci?type=&q=&status=active|retired|all&limit=   — list / search by name or key (serial, IP, NAA…)
GET /api/ci/export.xlsx?type=&status=                    — Sổ CI (Excel)
GET /api/ci/stats                                        — CIs per type, hosts with a serial, weak links
GET /api/ci/{id}                                         — one CI with its keys and links in both directions
GET /api/ci/{id}/impact?direction=down|up&depth=        — downstream / upstream impact (cmdb/impact.py)
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..cmdb import query as svc
from ..database import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/ci", tags=["cmdb"])


@router.get("")
def list_cis(type: Optional[str] = Query(default=None, max_length=24), q: Optional[str] = Query(default=None, max_length=80),
             status: str = Query(default="active", max_length=8), limit: int = Query(default=50, ge=1, le=svc.MAX_LIMIT),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.list_cis(db, user, type, q, status, limit)


@router.get("/export.xlsx")
def ci_export(type: Optional[str] = Query(default=None, max_length=24), status: str = Query(default="active", pattern="^(active|retired|all)$"),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sổ CI (Excel): CIs the caller may see, their keys and links (excel_cmdb.py)."""
    from fastapi import HTTPException
    from .. import excel_cmdb
    from ..downloads import attachment, stamp
    from ..cmdb.models import CI_TYPES
    if type and type not in CI_TYPES:
        raise HTTPException(422, "Loại CI không hợp lệ.")
    data = excel_cmdb.build_xlsx(db, user, type, status)
    return attachment(data, f"so-ci-{stamp()}.xlsx")


@router.get("/stats")
def ci_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.stats(db, user)


@router.get("/{ci_id}")
def ci_detail(ci_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.get_ci(db, user, ci_id)


@router.get("/{ci_id}/impact")
def ci_impact(ci_id: int, direction: str = Query(default="down", max_length=4), depth: int = Query(default=4, ge=1, le=4),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """down: what fails with this CI (host → VM → backup client…); up: what this CI depends on."""
    return svc.impact(db, user, ci_id, direction, depth)


@router.get("/{ci_id}/timeline")
def ci_timeline(ci_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Events of this CI, newest first (G5 event store); 404 like the card when the CI is hidden for the caller."""
    from ..events import service as events
    svc.get_ci(db, user, ci_id)
    return {"items": events.ci_timeline(db, ci_id)}


@router.get("/{ci_id}/changes")
def ci_changes(ci_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Change log of this CI, newest first (G6); 404 like the card when the CI is hidden for the caller."""
    from ..events import changes
    svc.get_ci(db, user, ci_id)
    return {"items": changes.for_ci(db, ci_id)}
