"""Sổ CI (ICO v2 G1b) — read only, every signed-in user; parts hidden by feature / role (cmdb/query.py).

GET /api/ci?type=&q=&status=active|retired|all&limit=   — list / search by name or key (serial, IP, NAA…)
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
