"""Chi phí & Hợp đồng — budget plan report (JSON for the dashboard, PDF, Excel) and saved filters.

The three outputs call the same spend.report.build_budget_report with the same validated
filters. Filters come as a JSON body (POST) or as ?filters=<json> (GET, for plain links).
"""
import datetime as dt
import json
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import excel_spend, report_spend
from ..database import get_db
from ..features import require_feature
from ..models import ReportLogo, User
from ..spend import fiscal, report, service, settings as spend_settings
from ..spend.models import SpendSavedFilter
from ..spend.roles import has_role, require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend/reports", tags=["spend-reports"], dependencies=[Depends(require_feature("spend"))])
MAX_FILTER_JSON = 4000


def _build(db: Session, data: Optional[Dict[str, Any]]):
    cfg = spend_settings.load(db)
    try:
        f = report.parse_filters(data, cfg, fiscal.today_vn())
    except report.FilterError as exc:
        raise HTTPException(400, str(exc)) from exc
    return f, report.build_budget_report(db, f, cfg)


def _from_query(filters: Optional[str]) -> Dict[str, Any]:
    if not filters:
        return {}
    if len(filters) > MAX_FILTER_JSON:
        raise HTTPException(400, "Bộ lọc quá dài.")
    try:
        data = json.loads(filters)
    except ValueError as exc:
        raise HTTPException(400, "Bộ lọc không phải JSON hợp lệ.") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "Bộ lọc không hợp lệ.")
    return data


def _logo(db: Session) -> Optional[str]:
    row = db.get(ReportLogo, 1)       # PDF cover logo, validated data: URL (routers/settings.py)
    return f"data:{row.mime_type};base64,{row.data_base64}" if row else None


def _download(content: bytes, media: str, name: str) -> Response:
    return Response(content, media_type=media, headers={
        "Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store"})


@router.post("/budget")
def budget_json(data: Dict[str, Any] = Body(default={}), user: User = Depends(require_spend("viewer")),
                db: Session = Depends(get_db)):
    f, r = _build(db, data)
    out = report.to_json(r)
    out["scopeLabel"] = report.filter_summary(db, f)
    return out


def _pdf(db: Session, data: Dict[str, Any], request: Request, user: User) -> Response:
    f, r = _build(db, data)
    label = report.filter_summary(db, f)
    if data.get("ai") or request.query_params.get("ai") in ("1", "true"):
        from ..ai_tasks import budget_facts
        from .ai_admin import pdf_ai_box
        pdf_ai_box(db, user, "budget", lambda: {"pham_vi": label, **budget_facts(r)})
    pdf = report_spend.build_budget_pdf(r, label, _logo(db))
    service.audit(db, user, request, "spend.report.pdf", filters=report.public_filters(f))
    db.commit()
    return _download(pdf, "application/pdf", f"ke-hoach-ngan-sach-IT-{fiscal.today_vn():%Y%m%d}.pdf")


def _xlsx(db: Session, data: Dict[str, Any], request: Request, user: User) -> Response:
    f, r = _build(db, data)
    content = excel_spend.build_budget_xlsx(r, report.filter_summary(db, f))
    service.audit(db, user, request, "spend.report.xlsx", filters=report.public_filters(f))
    db.commit()
    return _download(content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     f"ke-hoach-ngan-sach-IT-{fiscal.today_vn():%Y%m%d}.xlsx")


@router.post("/budget.pdf")
def budget_pdf_post(request: Request, data: Dict[str, Any] = Body(default={}), user: User = Depends(require_spend("viewer")),
                    db: Session = Depends(get_db)):
    return _pdf(db, data, request, user)


@router.get("/budget.pdf")
def budget_pdf_get(request: Request, filters: Optional[str] = Query(default=None), user: User = Depends(require_spend("viewer")),
                   db: Session = Depends(get_db)):
    return _pdf(db, _from_query(filters), request, user)


@router.post("/budget.xlsx")
def budget_xlsx_post(request: Request, data: Dict[str, Any] = Body(default={}), user: User = Depends(require_spend("viewer")),
                     db: Session = Depends(get_db)):
    return _xlsx(db, data, request, user)


@router.get("/budget.xlsx")
def budget_xlsx_get(request: Request, filters: Optional[str] = Query(default=None), user: User = Depends(require_spend("viewer")),
                    db: Session = Depends(get_db)):
    return _xlsx(db, _from_query(filters), request, user)


# ---------------------------------------------------------------- saved filters
def _filter_out(row: SpendSavedFilter, user: User) -> Dict[str, Any]:
    try:
        filters = json.loads(row.filters_json)
    except ValueError:
        filters = {}
    return {"id": row.id, "name": row.name, "shared": row.shared, "owner": row.owner_username,
            "mine": row.owner_username == user.username, "filters": filters, "createdAt": to_iso_utc(row.created_at)}


@router.get("/filters")
def list_filters(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    rows = (db.query(SpendSavedFilter).filter(or_(SpendSavedFilter.owner_username == user.username, SpendSavedFilter.shared.is_(True)))
            .order_by(SpendSavedFilter.name).limit(200).all())
    return [_filter_out(r, user) for r in rows]


@router.post("/filters")
def save_filter(data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 100:
        raise HTTPException(400, "Tên bộ lọc bắt buộc, tối đa 100 ký tự.")
    try:
        f = report.parse_filters(data.get("filters") or {}, spend_settings.load(db), fiscal.today_vn())
    except report.FilterError as exc:
        raise HTTPException(400, str(exc)) from exc
    text = json.dumps(report.public_filters(f), ensure_ascii=False)
    if len(text) > MAX_FILTER_JSON:
        raise HTTPException(400, "Bộ lọc quá dài.")
    if db.query(SpendSavedFilter).filter_by(owner_username=user.username).count() >= 50:
        raise HTTPException(400, "Tối đa 50 bộ lọc cho mỗi người.")
    row = db.query(SpendSavedFilter).filter_by(owner_username=user.username, name=name).first()
    if row is None:
        row = SpendSavedFilter(owner_username=user.username, name=name, created_at=dt.datetime.utcnow())
        db.add(row)
    row.filters_json, row.shared = text, bool(data.get("shared"))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bộ lọc cùng tên vừa được lưu — thử lại.") from exc
    return _filter_out(row, user)


@router.delete("/filters/{filter_id}")
def delete_filter(filter_id: int, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    row = db.get(SpendSavedFilter, filter_id)
    # Not found and not yours answer the same (no hint that another user's filter exists).
    if row is None or (row.owner_username != user.username and not has_role(user, "admin")):
        raise HTTPException(404, "Không tìm thấy bộ lọc.")
    db.delete(row)
    db.commit()
    return {"ok": True}
