"""Chi phí & Hợp đồng — Capex projects, their items and the planned Opex they generate."""
import datetime as dt
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..features import require_feature
from ..models import User
from ..spend import capex, codes, fiscal, service, settings as spend_settings
from ..spend.models import CapexItem, CapexProject, OpexItem, SpendCategory, SpendCostCenter, SpendVendor
from ..spend.roles import has_role, require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend/capex", tags=["spend-capex"], dependencies=[Depends(require_feature("spend"))])
MAX_ITEMS = 200


def _project(db: Session, code: str) -> CapexProject:
    p = db.query(CapexProject).filter(CapexProject.code == code).first()
    if not p:
        raise HTTPException(404, f"Không tìm thấy dự án Capex {code}.")
    return p


def _commit(db: Session, msg: str = "Dữ liệu vừa bị thay đổi bởi thao tác khác — tải lại và thử lại.") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, msg) from exc


def _bad(msg: str):
    raise HTTPException(400, msg)


def _id(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- input
def _apply_project(db: Session, p: CapexProject, data: Dict[str, Any], cfg: spend_settings.SpendSettings) -> None:
    p.name = service._text(data.get("name"), "tên dự án", 255, required=True)
    p.description = service._text(data.get("description"), "mô tả", 5000)
    p.owner_name = service._text(data.get("ownerName"), "người phụ trách", 128)
    gl = data.get("goLiveDate")
    p.go_live_date = service._date(gl, "Ngày go-live") if gl else None
    fy = data.get("fiscalYear")
    if fy in (None, ""):
        if not p.go_live_date:
            _bad("Nhập năm tài chính hoặc ngày go-live.")
        p.fiscal_year = fiscal.fiscal_year(p.go_live_date, cfg.fiscal_start_month)
    else:
        try:
            p.fiscal_year = int(fy)
        except (TypeError, ValueError):
            _bad("Năm tài chính không hợp lệ.")
    if not 2000 <= p.fiscal_year <= 2100:
        _bad("Năm tài chính không hợp lệ.")
    cc = data.get("costCenterId")
    if cc:
        if not db.get(SpendCostCenter, _id(cc)):
            _bad("Cost center không tồn tại.")
        p.cost_center_id = _id(cc)
    else:
        p.cost_center_id = None


def _pct(v: Any, label: str) -> Optional[Decimal]:
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v))
    except ArithmeticError:
        _bad(f"{label} phải là số.")
    if not Decimal(0) <= d <= Decimal(100):
        _bad(f"{label} phải từ 0 đến 100.")
    return d.quantize(Decimal("0.01"))


def _apply_item(db: Session, it: CapexItem, d: Dict[str, Any], user: User, request: Request, idx: int) -> None:
    n = f"Hạng mục {idx + 1}: "
    cat = db.get(SpendCategory, _id(d.get("categoryId")))
    if not cat:
        _bad(n + "chọn loại chi phí.")
    it.category_id = cat.id
    it.description = service._text(d.get("description"), n + "mô tả", 255, required=True)
    if d.get("vendorId") or d.get("vendorName"):
        it.vendor_id = service.resolve_vendor(db, d, user, request)
    else:
        it.vendor_id = None
    try:
        it.quantity = int(d.get("quantity") or 1)
    except (TypeError, ValueError):
        _bad(n + "số lượng phải là số nguyên.")
    if it.quantity <= 0:
        _bad(n + "số lượng phải lớn hơn 0.")
    it.amount_net = service._money(d.get("amountNet"), n + "số tiền trước VAT")
    it.vat_pct = _pct(d.get("vatPct", 10), n + "VAT") or Decimal("0.00")
    gross = d.get("amountGross")
    it.amount_gross = service._money(gross, n + "số tiền sau VAT") if gross not in (None, "") else service.gross_of(it.amount_net, it.vat_pct)
    if it.amount_gross < it.amount_net:
        _bad(n + "số tiền sau VAT không được nhỏ hơn trước VAT.")
    sd = d.get("startDate")
    it.start_date = service._date(sd, n + "ngày bắt đầu bảo hành") if sd else None
    try:
        it.coverage_years = int(d.get("coverageYears") or 0)
    except (TypeError, ValueError):
        _bad(n + "số năm bảo hành phải là số nguyên.")
    if not 0 <= it.coverage_years <= 10:
        _bad(n + "số năm bảo hành / license từ 0 đến 10.")
    it.renewal_pct = _pct(d.get("renewalPct"), n + "% gia hạn")
    ra = d.get("renewalAmount")
    it.renewal_amount = service._money(ra, n + "số tiền gia hạn") if ra not in (None, "") else None
    it.sort_order = idx
    if it.coverage_years > 0 and it.vendor_id is None:
        _bad(n + "có thời hạn bảo hành / license nên cần nhà cung cấp (để sinh Opex gia hạn).")
    it.updated_at = dt.datetime.utcnow()


def _apply_items(db: Session, p: CapexProject, rows: Optional[List[Dict[str, Any]]], user: User,
                 request: Request) -> Dict[str, List[str]]:
    """Create / update / delete items. Opex generated by a deleted item is handled first:
    managed rows are removed, confirmed / edited rows are kept and unlinked from the item."""
    removed = {"created": [], "updated": [], "removed": [], "kept": []}
    if rows is None:
        return removed
    if len(rows) > MAX_ITEMS:
        _bad(f"Tối đa {MAX_ITEMS} hạng mục cho một dự án.")
    current = {i.id: i for i in db.query(CapexItem).filter(CapexItem.project_id == p.id)}
    keep = set()
    for idx, d in enumerate(rows):
        iid = d.get("id")
        if iid:
            it = current.get(_id(iid))
            if it is None:
                _bad(f"Hạng mục {idx + 1} không thuộc dự án này.")
        else:
            it = CapexItem(project_id=p.id)
        _apply_item(db, it, d, user, request, idx)     # fields first: vendor lookups autoflush the session
        db.add(it)
        db.flush()
        keep.add(it.id)
    for iid, it in current.items():
        if iid not in keep:
            for o in db.query(OpexItem).filter(OpexItem.capex_item_id == iid).all():
                capex._drop(db, o, removed, user, request, p)
            db.flush()
            db.delete(it)
    return removed


# ---------------------------------------------------------------- output
def _detail(db: Session, p: CapexProject) -> Dict[str, Any]:
    cfg = spend_settings.load(db)
    cats = {c.id: c for c in db.query(SpendCategory).all()}
    items = db.query(CapexItem).filter(CapexItem.project_id == p.id).order_by(CapexItem.sort_order, CapexItem.id).all()
    vendors = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_({i.vendor_id for i in items if i.vendor_id}))}
    opex = db.query(OpexItem).filter(OpexItem.capex_project_id == p.id).order_by(OpexItem.start_date, OpexItem.id).all()
    by_item = {o.capex_item_id: o for o in opex if o.capex_item_id is not None and o.origin == "capex"}
    cc = db.get(SpendCostCenter, p.cost_center_id) if p.cost_center_id else None
    linked_ids = {o.id for o in by_item.values()}
    return {
        **_summary(p, sum(i.amount_net for i in items), sum(i.amount_gross for i in items), len(items)),
        "description": p.description, "costCenterId": p.cost_center_id,
        "costCenterName": f"{cc.code} — {cc.name}" if cc else None,
        "generating": p.status in capex.GENERATING_STATUSES,
        "items": [capex.item_view(i, p, cats, cfg, by_item.get(i.id), vendors) for i in items],
        # Opex of the project no longer tied to an item (item removed after the Opex was confirmed / edited).
        "orphanOpex": [{"code": o.code, "title": o.title, "status": o.status, "fiscalYear": o.fiscal_year,
                        "amountGross": o.amount_gross} for o in opex if o.id not in linked_ids],
        "createdBy": p.created_by, "createdAt": to_iso_utc(p.created_at),
        "updatedBy": p.updated_by, "updatedAt": to_iso_utc(p.updated_at),
    }


def _summary(p: CapexProject, net: int, gross: int, n_items: int) -> Dict[str, Any]:
    return {"id": p.id, "code": p.code, "name": p.name, "fiscalYear": p.fiscal_year, "status": p.status,
            "statusLabel": capex.PROJECT_LABELS[p.status], "ownerName": p.owner_name,
            "goLiveDate": p.go_live_date.isoformat() if p.go_live_date else None,
            "amountNet": int(net or 0), "amountGross": int(gross or 0), "itemCount": n_items, "rowVersion": p.row_version}


# ---------------------------------------------------------------- endpoints
@router.get("")
def list_projects(fy: Optional[int] = None, status: Optional[str] = None, q: Optional[str] = None,
                  user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    qry = db.query(CapexProject)
    if fy:
        qry = qry.filter(CapexProject.fiscal_year == fy)
    if status in capex.PROJECT_STATUSES:
        qry = qry.filter(CapexProject.status == status)
    if q:
        like = f"%{q.strip()[:100]}%"
        qry = qry.filter((CapexProject.code.ilike(like)) | (CapexProject.name.ilike(like)))
    projects = qry.order_by(CapexProject.fiscal_year.desc(), CapexProject.id.desc()).limit(500).all()
    ids = [p.id for p in projects]
    totals = {pid: (n, g, c) for pid, n, g, c in db.query(
        CapexItem.project_id, func.sum(CapexItem.amount_net), func.sum(CapexItem.amount_gross), func.count(CapexItem.id))
        .filter(CapexItem.project_id.in_(ids)).group_by(CapexItem.project_id)} if ids else {}
    opex = {pid: (n, g) for pid, n, g in db.query(
        OpexItem.capex_project_id, func.count(OpexItem.id), func.sum(OpexItem.amount_gross))
        .filter(OpexItem.capex_project_id.in_(ids)).group_by(OpexItem.capex_project_id)} if ids else {}
    out = []
    for p in projects:
        n, g, c = totals.get(p.id, (0, 0, 0))
        on, og = opex.get(p.id, (0, 0))
        out.append({**_summary(p, n, g, c), "opexCount": on, "opexGross": int(og or 0)})
    return out


@router.post("/preview")
def preview(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
            db: Session = Depends(get_db)):
    """The Opex a project would generate, from the form values, without saving anything."""
    cfg = spend_settings.load(db)
    try:
        p = CapexProject(code="(mới)", status="approved", created_by=user.username)
        _apply_project(db, p, data, cfg)
        items = []
        for idx, d in enumerate((data.get("items") or [])[:MAX_ITEMS]):
            it = CapexItem(id=-(idx + 1))
            _apply_item(db, it, d, user, request, idx)      # a new vendor name is flushed, then rolled back
            items.append(it)
        cats = {c.id: c for c in db.query(SpendCategory).all()}
        rows = []
        for it in items:
            v = capex.item_view(it, p, cats, cfg, None, {})
            rows.append({"index": -it.id - 1, "plan": v["plan"], "noPlanReason": v["noPlanReason"], "effectivePct": v["effectivePct"]})
        return {"items": rows}
    finally:
        db.rollback()      # nothing from a preview is ever written


@router.post("")
def create_project(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    cfg = spend_settings.load(db)
    p = CapexProject(status="draft", created_by=user.username, row_version=1)
    _apply_project(db, p, data, cfg)
    p.code = codes.next_code(db, "capex", p.fiscal_year)
    db.add(p)
    db.flush()
    _apply_items(db, p, data.get("items") or [], user, request)
    service.audit(db, user, request, "spend.capex.create", code=p.code, name=p.name)
    _commit(db, "Không tạo được mã Capex (trùng do thao tác đồng thời) — thử lại.")
    return _detail(db, p)


@router.get("/{code}")
def get_project(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return _detail(db, _project(db, code))


@router.put("/{code}")
def update_project(code: str, request: Request, data: Dict[str, Any] = Body(...),
                   user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    p = _project(db, code)
    if p.status == "closed":
        raise HTTPException(409, "Dự án đã đóng — mở lại (quyền Duyệt) để sửa.")
    try:
        version = int(data.get("rowVersion"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Thiếu rowVersion.")
    if version != p.row_version:
        raise HTTPException(409, "Dự án vừa được người khác sửa — tải lại để xem bản mới nhất.")
    cfg = spend_settings.load(db)
    before = {"name": p.name, "fiscalYear": p.fiscal_year, "goLiveDate": p.go_live_date,
              "items": [(i.id, i.amount_net, i.coverage_years, i.start_date) for i in db.query(CapexItem).filter_by(project_id=p.id)]}
    _apply_project(db, p, data, cfg)
    removed = _apply_items(db, p, data.get("items"), user, request)
    p.row_version += 1
    p.updated_at, p.updated_by = dt.datetime.utcnow(), user.username
    db.flush()
    result = capex.reconcile(db, p, cfg, user, request)
    for k in result:
        result[k] = removed[k] + result[k]
    after = {"name": p.name, "fiscalYear": p.fiscal_year, "goLiveDate": p.go_live_date,
             "items": [(i.id, i.amount_net, i.coverage_years, i.start_date) for i in db.query(CapexItem).filter_by(project_id=p.id)]}
    service.audit(db, user, request, "spend.capex.update", code=p.code, before=before, after=after, opex=result)
    _commit(db)
    return {**_detail(db, p), "reconcile": result}


@router.post("/{code}/status")
def change_status(code: str, request: Request, data: Dict[str, Any] = Body(...),
                  user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    p = _project(db, code)
    target = data.get("status")
    need = capex.PROJECT_TRANSITIONS.get((p.status, target))
    if need is None:
        raise HTTPException(409, f"Không chuyển được từ “{capex.PROJECT_LABELS[p.status]}” sang trạng thái này.")
    if not has_role(user, need):
        raise HTTPException(403, "Cần quyền “Duyệt” cho thao tác này.")
    before = p.status
    p.status = target
    p.row_version += 1
    p.updated_at, p.updated_by = dt.datetime.utcnow(), user.username
    db.flush()
    result = capex.reconcile(db, p, spend_settings.load(db), user, request)
    service.audit(db, user, request, "spend.capex.status", code=p.code, status=[before, target], opex=result)
    _commit(db)
    return {**_detail(db, p), "reconcile": result}


@router.post("/{code}/opex/confirm")
def confirm_opex(code: str, request: Request, data: Dict[str, Any] = Body(...),
                 user: User = Depends(require_spend("approver")), db: Session = Depends(get_db)):
    """Confirm several planned Opex of this project at once (they stop following Capex edits)."""
    p = _project(db, code)
    wanted = [str(c) for c in (data.get("codes") or [])][:MAX_ITEMS]
    if not wanted:
        _bad("Chọn ít nhất một khoản Opex.")
    rows = db.query(OpexItem).filter(OpexItem.code.in_(wanted), OpexItem.capex_project_id == p.id).all()
    if len(rows) != len(set(wanted)):
        raise HTTPException(404, "Có khoản Opex không thuộc dự án này.")
    for o in rows:
        service.transition(db, o, "confirm", user, request)
    _commit(db)
    return _detail(db, p)


@router.delete("/{code}")
def delete_project(code: str, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    p = _project(db, code)
    opex = db.query(OpexItem).filter(OpexItem.capex_project_id == p.id).all()
    from ..spend.models import Contract, Proposal, ProposalOpex
    if (db.query(Proposal.id).filter(Proposal.capex_project_id == p.id).first()
            or db.query(ProposalOpex.opex_id).filter(ProposalOpex.opex_id.in_([o.id for o in opex] or [0])).first()):
        raise HTTPException(409, "Dự án (hoặc Opex của dự án) đã có tờ trình — không xoá được, hãy đóng dự án.")
    if db.query(Contract.id).filter((Contract.capex_project_id == p.id) | (Contract.opex_id.in_([o.id for o in opex] or [0]))).first():
        raise HTTPException(409, "Dự án (hoặc Opex của dự án) đã có hợp đồng — không xoá được, hãy đóng dự án.")
    unmanaged = [o.code for o in opex if not capex.is_managed(o)]
    if unmanaged:
        raise HTTPException(409, f"Dự án có Opex đã xác nhận / đã sửa ({', '.join(unmanaged[:5])}) — không xoá được, hãy đóng dự án.")
    for o in opex:
        capex._delete_opex(db, o)
    db.flush()
    db.query(CapexItem).filter(CapexItem.project_id == p.id).delete(synchronize_session=False)
    service.audit(db, user, request, "spend.capex.delete", code=p.code, name=p.name, removedOpex=[o.code for o in opex])
    db.delete(p)
    _commit(db)
    return {"ok": True}
