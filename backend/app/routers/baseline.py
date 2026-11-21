"""Baseline cấu hình ESXi / cluster / VM (ICO v2 G9a): report (everyone), rules and exceptions (admin), Excel / PDF."""
import json
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..baseline import service
from ..baseline.models import CfgException
from ..database import get_db
from ..downloads import attachment
from ..models import AuditLog, User
from ..security import get_current_user, require_admin

router = APIRouter(prefix="/api/baseline", tags=["baseline"])


def _changed(db: Session, user: User, action: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    from .. import inbox
    db.add(AuditLog(username=user.username, action=action, detail=json.dumps(detail, ensure_ascii=False, default=str)))
    db.commit()
    inbox.invalidate()
    return service.report(db)


@router.get("")
def report(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.report(db)


@router.put("/rules/{rule}")
def save_rule(rule: str, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        service.save_rule(db, rule, data, user.username)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _changed(db, user, "baseline.rule", {"rule": rule, **{k: data.get(k) for k in ("enabled", "level", "param")}})


@router.post("/exceptions")
def add_exception(data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..spend.fiscal import today_vn
    try:
        e = service.add_exception(db, data, user.username, today_vn())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _changed(db, user, "baseline.exception.add", {"rule": e.rule, "scope": e.scope, "entity": e.entity, "until": e.until, "reason": e.reason})


@router.delete("/exceptions/{eid}")
def delete_exception(eid: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    e = db.get(CfgException, eid)
    if e is None:
        raise HTTPException(404, "Không có ngoại lệ này.")
    detail = {"rule": e.rule, "entity": e.entity}
    db.delete(e)
    return _changed(db, user, "baseline.exception.delete", detail)


@router.get("/report.xlsx")
def report_xlsx(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..excel_baseline import build_xlsx
    return attachment(build_xlsx(service.report(db)), "ICO_Baseline_cau_hinh.xlsx")


@router.get("/report.pdf")
def report_pdf(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..report_baseline import build_pdf
    return attachment(build_pdf(service.report(db), user.display_name or user.username), "ICO_Baseline_cau_hinh.pdf")
