"""Chi phí & Hợp đồng — Kế hoạch ngân sách năm sau (docs/design/spend-budget-plan.md).

Roles: Xem reads; Biên tập creates / edits a draft and submits it; Duyệt approves, returns,
applies and revises. Every change is written to audit_log in the same transaction."""
import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..features import require_feature
from ..models import User
from ..spend import budget_plan as bp, fiscal, service, settings as spend_settings
from ..spend.models import PLAN_PRIORITIES, BudgetPlan, BudgetPlanLine, SpendCategory, SpendCostCenter, SpendVendor
from ..spend.roles import has_role, require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend/plans", tags=["spend-plans"], dependencies=[Depends(require_feature("spend"))])


def _plan(db: Session, plan_id: int) -> BudgetPlan:
    p = db.get(BudgetPlan, plan_id)
    if not p:
        raise HTTPException(404, "Không tìm thấy kế hoạch.")
    return p


def _draft(p: BudgetPlan) -> None:
    if p.status != "draft":
        raise HTTPException(409, f"Kế hoạch đang ở trạng thái “{bp.STATUS_LABELS[p.status]}” — chỉ sửa được bản nháp.")


STALE = "Kế hoạch vừa được người khác thay đổi — tải lại rồi thử lại."


def _check_version(p: BudgetPlan, data: Dict[str, Any]) -> None:
    """Early, friendly check; the real guard is the conditional UPDATE in _touch."""
    rv = data.get("rowVersion")
    if rv is not None and int(rv) != p.row_version:
        raise HTTPException(409, STALE)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Dữ liệu vừa bị thay đổi bởi thao tác khác — tải lại và thử lại.") from exc


def _touch(db: Session, p: BudgetPlan, user: User, expected: Any = None) -> None:
    service.bump_version(db, p, expected, STALE)
    p.updated_at, p.updated_by = dt.datetime.utcnow(), user.username


def _pct(v: Any, label: str) -> Decimal:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise HTTPException(400, f"{label} không hợp lệ.")
    if not Decimal(0) <= d <= Decimal(100):
        raise HTTPException(400, f"{label} phải từ 0 đến 100%.")
    return d.quantize(Decimal("0.01"))


def _lines(db: Session, p: BudgetPlan) -> List[BudgetPlanLine]:
    return (db.query(BudgetPlanLine).filter(BudgetPlanLine.plan_id == p.id)
            .order_by(BudgetPlanLine.sort_order, BudgetPlanLine.id).limit(bp.MAX_LINES).all())


def _plan_out(p: BudgetPlan) -> Dict[str, Any]:
    return {"id": p.id, "fiscalYear": p.fiscal_year, "version": p.version, "label": bp.plan_label(p), "name": p.name,
            "status": p.status, "statusLabel": bp.STATUS_LABELS[p.status], "upliftPct": float(p.uplift_pct),
            "budgetCap": p.budget_cap, "notes": p.notes, "rowVersion": p.row_version,
            "createdAt": to_iso_utc(p.created_at), "createdBy": p.created_by, "updatedAt": to_iso_utc(p.updated_at),
            "updatedBy": p.updated_by, "submittedAt": to_iso_utc(p.submitted_at), "submittedBy": p.submitted_by,
            "approvedAt": to_iso_utc(p.approved_at), "approvedBy": p.approved_by}


def _line_out(l: BudgetPlanLine, lk: service.Lookups, plan: BudgetPlan) -> Dict[str, Any]:
    cat, ven, cc = lk.categories.get(l.category_id), lk.vendors.get(l.vendor_id), lk.cost_centers.get(l.cost_center_id)
    eff = l.uplift_pct if l.uplift_pct is not None else (plan.uplift_pct if l.source == "renewal" else 0)
    return {"id": l.id, "source": l.source, "sourceLabel": bp.SOURCE_LABELS[l.source], "sourceCode": l.source_code, "sourceId": l.source_id,
            "kind": l.kind, "categoryId": l.category_id, "categoryName": cat.name if cat else None,
            "vendorId": l.vendor_id, "vendorName": ven.name if ven else None,
            "costCenterId": l.cost_center_id, "costCenterName": f"{cc.code} — {cc.name}" if cc else None,
            "title": l.title, "startDate": l.start_date.isoformat() if l.start_date else None, "terms": l.terms,
            "baseNet": l.base_net, "upliftPct": float(l.uplift_pct) if l.uplift_pct is not None else None,
            "effectiveUpliftPct": float(eff), "amountMode": l.amount_mode, "amountNet": l.amount_net,
            "vatPct": float(l.vat_pct), "amountGross": l.amount_gross, "priority": l.priority,
            "priorityLabel": bp.PRIORITY_LABELS[l.priority], "included": l.included, "note": l.note, "stale": l.stale,
            "appliedCode": l.applied_code, "detail": l.detail, "updatedAt": to_iso_utc(l.updated_at), "updatedBy": l.updated_by}


def _detail(db: Session, p: BudgetPlan, user: User) -> Dict[str, Any]:
    cfg = spend_settings.load(db)
    lines = _lines(db, p)
    lk = service.Lookups(db)
    out = {**_plan_out(p), "lines": [_line_out(l, lk, p) for l in lines], "summary": bp.summary(db, p, lines, cfg),
           "fiscalRange": [d.isoformat() for d in fiscal.fiscal_range(p.fiscal_year, cfg.fiscal_start_month)],
           "can": {"edit": p.status == "draft" and has_role(user, "editor"),
                   "submit": p.status == "draft" and has_role(user, "editor"),
                   "approve": p.status == "submitted" and has_role(user, "approver"),
                   "return": p.status == "submitted" and has_role(user, "approver"),
                   "apply": p.status == "approved" and has_role(user, "approver"),
                   "revise": p.status == "approved" and has_role(user, "editor")}}
    if p.status in ("approved", "superseded"):
        out["actuals"] = bp.actuals(db, p, lines)
    return out


# ---------------------------------------------------------------- plans
@router.get("")
def list_plans(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    cfg = spend_settings.load(db)
    cur = fiscal.fiscal_year(fiscal.today_vn(), cfg.fiscal_start_month)
    plans = db.query(BudgetPlan).order_by(BudgetPlan.fiscal_year.desc(), BudgetPlan.version.desc()).limit(200).all()
    totals = {}
    for pid, inc, gross in db.query(BudgetPlanLine.plan_id, BudgetPlanLine.included, BudgetPlanLine.amount_gross).filter(
            BudgetPlanLine.plan_id.in_([p.id for p in plans] or [-1])):
        t = totals.setdefault(pid, {"total": 0, "count": 0})
        if inc:
            t["total"] += gross
            t["count"] += 1
    return {"currentFiscalYear": cur, "nextFiscalYear": cur + 1,
            "items": [{**_plan_out(p), **totals.get(p.id, {"total": 0, "count": 0})} for p in plans]}


@router.post("")
def create_plan(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                db: Session = Depends(get_db)):
    cfg = spend_settings.load(db)
    cur = fiscal.fiscal_year(fiscal.today_vn(), cfg.fiscal_start_month)
    try:
        year = int(data.get("fiscalYear") or cur + 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "Năm tài chính không hợp lệ.")
    if not cur - 1 <= year <= cur + 5:
        raise HTTPException(400, f"Lập kế hoạch cho FY {cur - 1} đến {cur + 5}.")
    if db.query(BudgetPlan.id).filter(BudgetPlan.fiscal_year == year, BudgetPlan.status.in_(("draft", "submitted"))).first():
        raise HTTPException(409, f"FY {year} đã có kế hoạch đang soạn / chờ duyệt — mở bản đó để tiếp tục.")
    if db.query(BudgetPlan.id).filter(BudgetPlan.fiscal_year == year, BudgetPlan.status == "approved").first():
        raise HTTPException(409, f"FY {year} đã có kế hoạch được duyệt — dùng “Sửa đổi” để lập bản mới từ bản đó.")
    version = (db.query(BudgetPlan.version).filter(BudgetPlan.fiscal_year == year).order_by(BudgetPlan.version.desc()).limit(1).scalar() or 0) + 1
    p = BudgetPlan(fiscal_year=year, version=version, status="draft", created_by=user.username, row_version=1,
                   name=service._text(data.get("name"), "tên kế hoạch", 255) or f"Kế hoạch ngân sách IT FY {year}",
                   uplift_pct=_pct(data.get("upliftPct", 5), "% tăng giá gia hạn"),
                   budget_cap=service._money(data["budgetCap"], "Trần ngân sách") if data.get("budgetCap") not in (None, "") else None,
                   notes=service._text(data.get("notes"), "ghi chú", 5000))
    db.add(p)
    db.flush()
    stats = bp.refresh(db, p, cfg, user)
    service.audit(db, user, request, "spend.plan.create", plan=bp.plan_label(p), lines=stats)
    _commit(db)
    return _detail(db, p, user)


@router.get("/{plan_id}")
def get_plan(plan_id: int, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return _detail(db, _plan(db, plan_id), user)


@router.put("/{plan_id}")
def update_plan(plan_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                db: Session = Depends(get_db)):
    p = _plan(db, plan_id)
    _draft(p)
    _check_version(p, data)
    before = {"upliftPct": str(p.uplift_pct), "budgetCap": p.budget_cap, "name": p.name}
    if "name" in data:
        p.name = service._text(data.get("name"), "tên kế hoạch", 255, required=True)
    if "notes" in data:
        p.notes = service._text(data.get("notes"), "ghi chú", 5000)
    if "budgetCap" in data:
        p.budget_cap = service._money(data["budgetCap"], "Trần ngân sách") if data["budgetCap"] not in (None, "") else None
    if "upliftPct" in data:
        p.uplift_pct = _pct(data["upliftPct"], "% tăng giá gia hạn")
        for l in _lines(db, p):
            bp.line_amounts(l, p)
    _touch(db, p, user)
    service.audit(db, user, request, "spend.plan.update", plan=bp.plan_label(p),
                  changes=service.diff(before, {"upliftPct": str(p.uplift_pct), "budgetCap": p.budget_cap, "name": p.name}))
    _commit(db)
    return _detail(db, p, user)


@router.post("/{plan_id}/refresh")
def refresh_plan(plan_id: int, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    p = _plan(db, plan_id)
    _draft(p)
    stats = bp.refresh(db, p, spend_settings.load(db), user)
    _touch(db, p, user)
    service.audit(db, user, request, "spend.plan.refresh", plan=bp.plan_label(p), lines=stats)
    _commit(db)
    return {**_detail(db, p, user), "refreshed": stats}


@router.delete("/{plan_id}")
def delete_plan(plan_id: int, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    p = _plan(db, plan_id)
    _draft(p)
    service.audit(db, user, request, "spend.plan.delete", plan=bp.plan_label(p))
    db.query(BudgetPlanLine).filter(BudgetPlanLine.plan_id == p.id).delete(synchronize_session=False)
    db.delete(p)
    _commit(db)
    return {"ok": True}


# ---------------------------------------------------------------- lines
def _apply_line(db: Session, l: BudgetPlanLine, p: BudgetPlan, data: Dict[str, Any], creating: bool) -> None:
    if creating or l.source == "new":
        if "kind" in data or creating:
            if data.get("kind") not in ("opex", "capex"):
                raise HTTPException(400, "Loại dòng phải là Opex hoặc Capex.")
            l.kind = data["kind"]
        if "title" in data or creating:
            l.title = service._text(data.get("title"), "nội dung", 255, required=True)
        if "categoryId" in data or creating:
            if not db.get(SpendCategory, int(data.get("categoryId") or 0)):
                raise HTTPException(400, "Chọn loại chi phí.")
            l.category_id = int(data["categoryId"])
        if "baseNet" in data:
            l.base_net = service._money(data["baseNet"], "Số tiền trước VAT")
    if "vendorId" in data:
        vid = data.get("vendorId")
        if vid and not db.get(SpendVendor, int(vid)):
            raise HTTPException(400, "Nhà cung cấp không tồn tại.")
        l.vendor_id = int(vid) if vid else None
    if "costCenterId" in data:
        cc = data.get("costCenterId")
        if cc and not db.get(SpendCostCenter, int(cc)):
            raise HTTPException(400, "Cost center không tồn tại.")
        l.cost_center_id = int(cc) if cc else None
    if "startDate" in data:
        l.start_date = service._date(data["startDate"], "Ngày bắt đầu") if data["startDate"] else None
        if l.start_date:
            ys, ye = fiscal.fiscal_range(p.fiscal_year, spend_settings.load(db).fiscal_start_month)
            if not ys <= l.start_date <= ye:
                raise HTTPException(400, f"Ngày bắt đầu phải nằm trong FY {p.fiscal_year}.")
    if "vatPct" in data:
        l.vat_pct = _pct(data["vatPct"], "VAT")
    if "upliftPct" in data:
        l.uplift_pct = _pct(data["upliftPct"], "% tăng giá") if data["upliftPct"] not in (None, "") else None
    if "priority" in data:
        if data["priority"] not in PLAN_PRIORITIES:
            raise HTTPException(400, "Mức ưu tiên không hợp lệ.")
        l.priority = data["priority"]
    if "included" in data:
        l.included = bool(data["included"])
    if "note" in data:
        l.note = service._text(data.get("note"), "ghi chú", 1000)
    if "amountNet" in data:
        if data["amountNet"] in (None, ""):
            l.amount_mode = "auto"                      # back to base × (1 + uplift)
        else:
            l.amount_mode, l.amount_net = "manual", service._money(data["amountNet"], "Số tiền kế hoạch")
    if not l.included and l.source != "new" and not l.note:
        raise HTTPException(400, "Ghi lý do khi bỏ một dòng khỏi kế hoạch (vd: ngừng dịch vụ, gộp hợp đồng).")
    bp.line_amounts(l, p)


@router.post("/{plan_id}/lines")
def add_line(plan_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
             db: Session = Depends(get_db)):
    p = _plan(db, plan_id)
    _draft(p)
    if db.query(BudgetPlanLine.id).filter(BudgetPlanLine.plan_id == p.id).count() >= bp.MAX_LINES:
        raise HTTPException(400, f"Tối đa {bp.MAX_LINES} dòng.")
    order = (db.query(BudgetPlanLine.sort_order).filter(BudgetPlanLine.plan_id == p.id)
             .order_by(BudgetPlanLine.sort_order.desc()).limit(1).scalar() or 0) + 1
    l = BudgetPlanLine(plan_id=p.id, source="new", amount_mode="auto", priority="should", included=True, base_net=0,
                       terms=1, vat_pct=Decimal(10), sort_order=order, updated_at=dt.datetime.utcnow(), updated_by=user.username)
    _apply_line(db, l, p, data, creating=True)
    db.add(l)
    _touch(db, p, user)
    db.flush()
    service.audit(db, user, request, "spend.plan.line.add", plan=bp.plan_label(p), line=l.id, title=l.title, amountGross=l.amount_gross)
    _commit(db)
    return _detail(db, p, user)


@router.put("/{plan_id}/lines/{line_id}")
def update_line(plan_id: int, line_id: int, request: Request, data: Dict[str, Any] = Body(...),
                user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    p = _plan(db, plan_id)
    _draft(p)
    l = db.get(BudgetPlanLine, line_id)
    if not l or l.plan_id != p.id:
        raise HTTPException(404, "Không tìm thấy dòng.")
    before = {"amountGross": l.amount_gross, "included": l.included, "priority": l.priority, "note": l.note}
    _apply_line(db, l, p, data, creating=False)
    l.updated_at, l.updated_by = dt.datetime.utcnow(), user.username
    _touch(db, p, user)
    service.audit(db, user, request, "spend.plan.line.update", plan=bp.plan_label(p), line=l.id, title=l.title,
                  changes=service.diff(before, {"amountGross": l.amount_gross, "included": l.included, "priority": l.priority, "note": l.note}))
    _commit(db)
    return _detail(db, p, user)


@router.delete("/{plan_id}/lines/{line_id}")
def delete_line(plan_id: int, line_id: int, request: Request, user: User = Depends(require_spend("editor")),
                db: Session = Depends(get_db)):
    p = _plan(db, plan_id)
    _draft(p)
    l = db.get(BudgetPlanLine, line_id)
    if not l or l.plan_id != p.id:
        raise HTTPException(404, "Không tìm thấy dòng.")
    if l.source != "new" and not l.stale:
        raise HTTPException(409, "Dòng gợi ý từ dữ liệu không xoá được — bỏ khỏi kế hoạch (kèm lý do) thay vì xoá.")
    service.audit(db, user, request, "spend.plan.line.delete", plan=bp.plan_label(p), line=l.id, title=l.title, amountGross=l.amount_gross)
    db.delete(l)
    _touch(db, p, user)
    _commit(db)
    return _detail(db, p, user)


# ---------------------------------------------------------------- export
@router.get("/{plan_id}/export.xlsx")
def export_plan(plan_id: int, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    from ..excel_budget_plan import build_plan_xlsx
    p = _plan(db, plan_id)
    data = build_plan_xlsx(_detail(db, p, user))
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="ke-hoach-ngan-sach-{p.fiscal_year}-v{p.version}.xlsx"',
                             "Cache-Control": "no-store"})


# ---------------------------------------------------------------- workflow (declared last: /{plan_id}/{command} must not shadow /lines, /refresh)
MOVES = {  # command -> (from, to, minimum role)
    "submit": ("draft", "submitted", "editor"),
    "return": ("submitted", "draft", "approver"),
    "approve": ("submitted", "approved", "approver"),
}


@router.post("/{plan_id}/{command}")
def move_plan(plan_id: int, command: str, request: Request, data: Dict[str, Any] = Body(default={}),
              user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    p = _plan(db, plan_id)
    cfg = spend_settings.load(db)
    if command == "apply":
        if not has_role(user, "approver"):
            raise HTTPException(403, "Cần quyền “Duyệt” trong module Chi phí & Hợp đồng.")
        if p.status != "approved":
            raise HTTPException(409, "Chỉ áp dụng được kế hoạch đã duyệt.")
        # Claim the plan BEFORE creating anything: a double click / second approver sending the
        # same rowVersion gets 0 rows here (on PostgreSQL it waits for the first to commit) -> 409.
        _touch(db, p, user, (data or {}).get("rowVersion"))
        out = bp.apply(db, p, cfg, user, request)
        _commit(db)
        return {**_detail(db, p, user), "applied": out}
    if command == "revise":
        if p.status != "approved":
            raise HTTPException(409, "Chỉ sửa đổi được kế hoạch đã duyệt.")
        if db.query(BudgetPlan.id).filter(BudgetPlan.fiscal_year == p.fiscal_year, BudgetPlan.status.in_(("draft", "submitted"))).first():
            raise HTTPException(409, "Đã có bản sửa đổi đang soạn cho năm này.")
        version = (db.query(BudgetPlan.version).filter(BudgetPlan.fiscal_year == p.fiscal_year)
                   .order_by(BudgetPlan.version.desc()).limit(1).scalar() or 0) + 1
        n = BudgetPlan(fiscal_year=p.fiscal_year, version=version, status="draft", name=p.name, uplift_pct=p.uplift_pct,
                       budget_cap=p.budget_cap, notes=p.notes, revised_from_id=p.id, created_by=user.username, row_version=1)
        db.add(n)
        db.flush()
        cols = ("source", "source_id", "source_code", "kind", "category_id", "vendor_id", "cost_center_id", "title", "start_date",
                "terms", "base_net", "uplift_pct", "amount_mode", "amount_net", "vat_pct", "amount_gross", "priority", "included",
                "note", "stale", "applied_code", "sort_order")
        for l in _lines(db, p):
            db.add(BudgetPlanLine(plan_id=n.id, updated_at=dt.datetime.utcnow(), updated_by=user.username,
                                  **{c: getattr(l, c) for c in cols}))
        service.audit(db, user, request, "spend.plan.revise", plan=bp.plan_label(p), newPlan=bp.plan_label(n))
        _commit(db)
        return _detail(db, n, user)
    if command not in MOVES:
        raise HTTPException(404, "Thao tác không hợp lệ.")
    src, dst, role = MOVES[command]
    if not has_role(user, role):
        raise HTTPException(403, f"Cần quyền “{'Duyệt' if role == 'approver' else 'Biên tập'}” trong module Chi phí & Hợp đồng.")
    if p.status != src:
        raise HTTPException(409, f"Không thể thực hiện khi kế hoạch đang “{bp.STATUS_LABELS[p.status]}”.")
    _check_version(p, data or {})
    now = dt.datetime.utcnow()
    if command == "submit":
        if not db.query(BudgetPlanLine.id).filter(BudgetPlanLine.plan_id == p.id, BudgetPlanLine.included.is_(True)).first():
            raise HTTPException(400, "Kế hoạch chưa có dòng nào được đưa vào.")
        p.submitted_at, p.submitted_by = now, user.username
    if command == "approve":
        for old in db.query(BudgetPlan).filter(BudgetPlan.fiscal_year == p.fiscal_year, BudgetPlan.status == "approved",
                                               BudgetPlan.id != p.id):
            old.status = "superseded"
            service.bump_version(db, old, None, STALE)
        p.approved_at, p.approved_by = now, user.username
    p.status = dst
    _touch(db, p, user)
    service.audit(db, user, request, f"spend.plan.{command}", plan=bp.plan_label(p), status=[src, dst],
                  reason=service._text((data or {}).get("reason"), "lý do", 500))
    _commit(db)
    return _detail(db, p, user)
