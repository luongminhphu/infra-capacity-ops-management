"""Chi phí & Hợp đồng IT — Opex, reference lists, attachments, module settings.

Every endpoint states its spend role (roles.require_spend); the whole router answers 404
when the module is switched off (Cài đặt → Tính năng).
"""
import datetime as dt
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..features import require_feature
from ..models import AuditLog, HostSnapshot, SnapshotRun, User
from ..spend import capex, fiscal, files, service, settings as spend_settings
from ..spend.models import (
    CATEGORY_GROUPS, CapexProject, Contract, OpexAsset, OpexAttachment, OpexItem, SpendCategory, SpendCostCenter, SpendFile, SpendReminderLog, SpendVendor,
)
from ..spend.roles import LABELS, effective_role, require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend", tags=["spend"], dependencies=[Depends(require_feature("spend"))])

PAGE_MAX = 200


def _item(db: Session, code: str) -> OpexItem:
    item = db.query(OpexItem).filter(OpexItem.code == code).first()
    if not item:
        raise HTTPException(404, f"Không tìm thấy khoản Opex {code}.")
    return item


def _commit(db: Session, conflict: str = "Dữ liệu vừa bị thay đổi bởi thao tác khác — tải lại và thử lại.") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, conflict) from exc


def _detail(db: Session, item: OpexItem) -> Dict[str, Any]:
    cfg = spend_settings.load(db)
    lk = service.Lookups(db)
    linked_ids = [i for i in (item.renewed_from_id,) if i]
    codes_by_id = dict(db.query(OpexItem.id, OpexItem.code).filter(OpexItem.id.in_(linked_ids)).all()) if linked_ids else {}
    succ = db.query(OpexItem.code).filter(OpexItem.renewed_from_id == item.id).scalar()
    out = service.serialize(item, lk, fiscal.today_vn(), cfg, codes_by_id, succ)
    out["capexProjectCode"] = (db.query(CapexProject.code).filter(CapexProject.id == item.capex_project_id).scalar()
                               if item.capex_project_id else None)
    out["managedByCapex"] = capex.is_managed(item)
    out["contracts"] = [{"code": c.code, "title": c.title, "status": c.status}
                        for c in db.query(Contract).filter(Contract.opex_id == item.id).order_by(Contract.id)]
    out["assets"] = [{"id": a.id, "serial": a.serial, "description": a.description, "vcenterKey": a.vcenter_key,
                      "hostName": a.host_name} for a in db.query(OpexAsset).filter_by(opex_id=item.id).order_by(OpexAsset.id)]
    out["attachments"] = service.attachments(db, item)
    return out


# ---------------------------------------------------------------- meta & reference lists
@router.get("/meta")
def meta(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    cfg = spend_settings.load(db)
    today = fiscal.today_vn()
    years = sorted({y for (y,) in db.query(OpexItem.fiscal_year).distinct()} |
                   {fiscal.fiscal_year(today, cfg.fiscal_start_month) + d for d in (-1, 0, 1)})
    return {
        "role": effective_role(user), "roleLabel": LABELS[effective_role(user)],
        "settings": cfg.public(), "currentFiscalYear": fiscal.fiscal_year(today, cfg.fiscal_start_month),
        "fiscalYears": years, "today": today.isoformat(),
        "categories": [{"id": c.id, "code": c.code, "name": c.name, "grp": c.grp, "active": c.active,
                        "renewalPct": float(c.renewal_pct) if c.renewal_pct is not None else None}
                       for c in db.query(SpendCategory).order_by(SpendCategory.sort_order, SpendCategory.id)],
        "categoryGroups": list(CATEGORY_GROUPS),
        "vendors": [{"id": v.id, "name": v.name, "shortName": v.short_name, "taxCode": v.tax_code, "active": v.active}
                    for v in db.query(SpendVendor).order_by(SpendVendor.name)],
        "costCenters": [{"id": c.id, "code": c.code, "name": c.name, "active": c.active}
                        for c in db.query(SpendCostCenter).order_by(SpendCostCenter.code)],
        "statusLabels": service.STATUS_LABELS, "cycleLabels": service.CYCLE_LABELS,
    }


@router.post("/categories")
def create_category(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("admin")),
                    db: Session = Depends(get_db)):
    row = SpendCategory(code=str(data.get("code") or "").strip().upper()[:32])
    _apply_category(row, data)
    if not row.code or not row.code.replace("_", "").isalnum():
        raise HTTPException(400, "Mã loại chỉ gồm chữ, số và dấu gạch dưới.")
    db.add(row)
    service.audit(db, user, request, "spend.category.create", code=row.code, name=row.name)
    _commit(db, f"Mã loại {row.code} đã tồn tại.")
    return {"id": row.id}


@router.put("/categories/{cat_id}")
def update_category(cat_id: int, request: Request, data: Dict[str, Any] = Body(...),
                    user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    row = db.get(SpendCategory, cat_id)
    if not row:
        raise HTTPException(404, "Không tìm thấy loại chi phí.")
    before = {"name": row.name, "renewalPct": row.renewal_pct, "active": row.active}
    _apply_category(row, data)
    service.audit(db, user, request, "spend.category.update", code=row.code, before=before,
                  after={"name": row.name, "renewalPct": row.renewal_pct, "active": row.active})
    _commit(db)
    return {"id": row.id}


def _apply_category(row: SpendCategory, data: Dict[str, Any]) -> None:
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 128:
        raise HTTPException(400, "Tên loại chi phí bắt buộc, tối đa 128 ký tự.")
    grp = data.get("grp") or row.grp or "other"
    if grp not in CATEGORY_GROUPS:
        raise HTTPException(400, "Nhóm loại chi phí không hợp lệ.")
    pct = data.get("renewalPct")
    if pct in (None, ""):
        row.renewal_pct = None
    else:
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            raise HTTPException(400, "% gia hạn phải là số.")
        if not 0 <= pct <= 100:
            raise HTTPException(400, "% gia hạn phải từ 0 đến 100.")
        row.renewal_pct = round(pct, 2)
    row.name, row.grp = name, grp
    row.active = bool(data.get("active", True))
    row.sort_order = int(data.get("sortOrder") or row.sort_order or 0)


@router.post("/vendors")
def create_vendor(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                  db: Session = Depends(get_db)):
    vid = service.resolve_vendor(db, {"vendorName": data.get("name")}, user, request)
    v = db.get(SpendVendor, vid)
    tax = str(data.get("taxCode") or "").strip()[:32]
    if tax:
        v.tax_code = tax
    _commit(db)
    return {"id": v.id, "name": v.name}


@router.put("/vendors/{vendor_id}")
def update_vendor(vendor_id: int, request: Request, data: Dict[str, Any] = Body(...),
                  user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    v = db.get(SpendVendor, vendor_id)
    if not v:
        raise HTTPException(404, "Không tìm thấy nhà cung cấp.")
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 255:
        raise HTTPException(400, "Tên nhà cung cấp bắt buộc, tối đa 255 ký tự.")
    service.audit(db, user, request, "spend.vendor.update", before=v.name, after=name)
    v.name, v.name_norm = name, service.norm_name(name)
    v.tax_code = str(data.get("taxCode") or "").strip()[:32] or None
    v.active = bool(data.get("active", True))
    _commit(db, "Đã có nhà cung cấp trùng tên.")
    return {"id": v.id}


@router.post("/cost-centers")
def create_cost_center(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                       db: Session = Depends(get_db)):
    code, name = str(data.get("code") or "").strip()[:32], str(data.get("name") or "").strip()[:128]
    if not code or not name:
        raise HTTPException(400, "Cost center cần mã và tên.")
    row = SpendCostCenter(code=code, name=name)
    db.add(row)
    service.audit(db, user, request, "spend.costcenter.create", code=code, name=name)
    _commit(db, f"Mã cost center {code} đã tồn tại.")
    return {"id": row.id}


@router.put("/cost-centers/{cc_id}")
def update_cost_center(cc_id: int, request: Request, data: Dict[str, Any] = Body(...),
                       user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    row = db.get(SpendCostCenter, cc_id)
    if not row:
        raise HTTPException(404, "Không tìm thấy cost center.")
    name = str(data.get("name") or "").strip()[:128]
    if not name:
        raise HTTPException(400, "Cost center cần tên.")
    row.name, row.active = name, bool(data.get("active", True))
    service.audit(db, user, request, "spend.costcenter.update", code=row.code, name=name, active=row.active)
    _commit(db)
    return {"id": row.id}


# ---------------------------------------------------------------- module settings
@router.get("/settings")
def get_module_settings(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return spend_settings.load(db).public()


@router.put("/settings")
def put_module_settings(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("admin")),
                        db: Session = Depends(get_db)):
    current = spend_settings.load(db)
    try:
        new = spend_settings.validate(data, current)
    except spend_settings.SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    spend_settings.save(db, new)
    service.audit(db, user, request, "spend.settings.update", before=current.public(), after=new.public())
    db.commit()
    return new.public()


# ---------------------------------------------------------------- Opex
@router.get("/opex")
def list_opex(fy: Optional[int] = None, status: Optional[str] = None, category: Optional[int] = None,
              vendor: Optional[int] = None, costCenter: Optional[int] = None, q: Optional[str] = None,
              origin: Optional[str] = None, capexProject: Optional[str] = None,
              expiringWithin: Optional[int] = Query(default=None, ge=0, le=3650),
              page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=PAGE_MAX),
              user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    cfg = spend_settings.load(db)
    today = fiscal.today_vn()
    qry = db.query(OpexItem)
    if fy:
        qry = qry.filter(OpexItem.fiscal_year == fy)
    if category:
        qry = qry.filter(OpexItem.category_id == category)
    if vendor:
        qry = qry.filter(OpexItem.vendor_id == vendor)
    if costCenter:
        qry = qry.filter(OpexItem.cost_center_id == costCenter)
    if origin in ("manual", "capex", "renewal"):
        qry = qry.filter(OpexItem.origin == origin)
    if capexProject:
        qry = qry.filter(OpexItem.capex_project_id.in_(db.query(CapexProject.id).filter(CapexProject.code == capexProject)))
    horizon = today + dt.timedelta(days=max(cfg.reminder_days))
    if status in ("planned", "active", "renewed", "stopped"):
        qry = qry.filter(OpexItem.status == status)
    elif status == "expiring":
        qry = qry.filter(OpexItem.status == "active", OpexItem.end_date >= today, OpexItem.end_date <= horizon)
    elif status == "overdue":
        qry = qry.filter(OpexItem.status == "active", OpexItem.end_date < today)
    elif status == "open":   # what still needs attention: planned + active
        qry = qry.filter(OpexItem.status.in_(("planned", "active")))
    elif status not in (None, "", "all"):
        raise HTTPException(400, "Trạng thái lọc không hợp lệ.")
    if expiringWithin is not None:
        qry = qry.filter(OpexItem.status == "active", OpexItem.end_date <= today + dt.timedelta(days=expiringWithin))
    if q:
        like = f"%{q.strip()[:100]}%"
        vendor_ids = [v for (v,) in db.query(SpendVendor.id).filter(SpendVendor.name.ilike(like))]
        serial_ids = db.query(OpexAsset.opex_id).filter(OpexAsset.serial.ilike(like))
        qry = qry.filter(or_(OpexItem.code.ilike(like), OpexItem.title.ilike(like), OpexItem.contract_ref.ilike(like),
                             OpexItem.po_ref.ilike(like), OpexItem.owner_name.ilike(like),
                             OpexItem.vendor_id.in_(vendor_ids), OpexItem.id.in_(serial_ids)))
    total, net, gross = qry.with_entities(func.count(OpexItem.id), func.coalesce(func.sum(OpexItem.amount_net), 0),
                                          func.coalesce(func.sum(OpexItem.amount_gross), 0)).one()
    rows = qry.order_by(OpexItem.end_date, OpexItem.id).offset((page - 1) * size).limit(size).all()
    lk = service.Lookups(db)
    ids = [r.id for r in rows]
    successors = dict(db.query(OpexItem.renewed_from_id, OpexItem.code).filter(OpexItem.renewed_from_id.in_(ids)).all()) if ids else {}
    pred_ids = [r.renewed_from_id for r in rows if r.renewed_from_id]
    preds = dict(db.query(OpexItem.id, OpexItem.code).filter(OpexItem.id.in_(pred_ids)).all()) if pred_ids else {}
    cpx_ids = {r.capex_project_id for r in rows if r.capex_project_id}
    cpx = dict(db.query(CapexProject.id, CapexProject.code).filter(CapexProject.id.in_(cpx_ids)).all()) if cpx_ids else {}
    items = []
    for r in rows:
        s = service.serialize(r, lk, today, cfg, preds, successors.get(r.id))
        s["capexProjectCode"] = cpx.get(r.capex_project_id)
        items.append(s)
    # Whole-register counters for the list header (independent of the filters above), in the same
    # request instead of two extra calls with status=expiring / overdue (audit API-04).
    active = OpexItem.status == "active"
    expiring_n, overdue_n = db.query(
        func.coalesce(func.sum(case((and_(active, OpexItem.end_date >= today, OpexItem.end_date <= horizon), 1), else_=0)), 0),
        func.coalesce(func.sum(case((and_(active, OpexItem.end_date < today), 1), else_=0)), 0),
    ).one()
    return {"total": total, "page": page, "size": size, "sumNet": int(net), "sumGross": int(gross), "items": items,
            "counts": {"expiring": int(expiring_n), "overdue": int(overdue_n)}}


@router.post("/opex")
def create_opex(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                db: Session = Depends(get_db)):
    cfg = spend_settings.load(db)
    item = service.create(db, data, cfg, user, request)
    _commit(db, "Không tạo được mã Opex (trùng do thao tác đồng thời) — thử lại.")
    return _detail(db, item)


@router.get("/opex/{code}")
def get_opex(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return _detail(db, _item(db, code))


@router.put("/opex/{code}")
def update_opex(code: str, request: Request, data: Dict[str, Any] = Body(...),
                user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    item = _item(db, code)
    if item.status in ("renewed", "stopped"):
        raise HTTPException(409, "Khoản đã gia hạn hoặc đã ngừng không sửa được — kích hoạt lại hoặc sửa khoản kế nhiệm.")
    try:
        version = int(data.get("rowVersion"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Thiếu rowVersion.")
    service.bump_version(db, item, version)
    cfg = spend_settings.load(db)
    before = service.snapshot(item)
    service.apply_fields(db, item, data, cfg, user, request, creating=False)
    service.replace_assets(db, item, data.get("assets"))
    item.updated_at, item.updated_by = dt.datetime.utcnow(), user.username
    if item.origin == "capex" and item.status == "planned":
        item.amount_mode = "manual"     # edited by hand: Capex changes no longer overwrite the amount
    service.audit(db, user, request, "spend.opex.update", code=item.code, changes=service.diff(before, service.snapshot(item)))
    _commit(db)
    return _detail(db, item)


@router.post("/opex/{code}/renew")
def renew_opex(code: str, request: Request, data: Dict[str, Any] = Body(default={}),
               user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    item = _item(db, code)
    new = service.renew(db, item, data, spend_settings.load(db), user, request)
    _commit(db, "Khoản này đã được gia hạn.")
    return _detail(db, new)


def _command(name: str, minimum: str):
    def handler(code: str, request: Request, data: Dict[str, Any] = Body(default={}),
                user: User = Depends(require_spend(minimum)), db: Session = Depends(get_db)):
        item = _item(db, code)
        service.transition(db, item, name, user, request, reason=(data or {}).get("reason"))
        _commit(db)
        return _detail(db, item)
    handler.__name__ = f"{name}_opex"
    return handler


router.post("/opex/{code}/confirm")(_command("confirm", "approver"))
router.post("/opex/{code}/stop")(_command("stop", "editor"))
router.post("/opex/{code}/reactivate")(_command("reactivate", "approver"))


@router.delete("/opex/{code}")
def delete_opex(code: str, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    item = _item(db, code)
    if db.query(OpexItem.id).filter(OpexItem.renewed_from_id == item.id).first():
        raise HTTPException(409, "Khoản đã có khoản gia hạn kế nhiệm — không xoá được, hãy chuyển sang Ngừng.")
    from ..spend.models import ProposalOpex
    if db.query(ProposalOpex.opex_id).filter(ProposalOpex.opex_id == item.id).first():
        raise HTTPException(409, "Khoản đã có tờ trình gắn vào — không xoá được, hãy chuyển sang Ngừng.")
    if db.query(Contract.id).filter(Contract.opex_id == item.id).first():
        raise HTTPException(409, "Khoản đã có hợp đồng gắn vào — không xoá được, hãy chuyển sang Ngừng.")
    file_rows = (db.query(SpendFile).join(OpexAttachment, OpexAttachment.file_id == SpendFile.id)
                 .filter(OpexAttachment.opex_id == item.id).all())
    service.audit(db, user, request, "spend.opex.delete", code=item.code, before=service.snapshot(item),
                  files=[{"name": f.original_name, "sha256": f.sha256} for f in file_rows])
    # Explicit child deletes: SQLite (tests, dev) does not enforce ON DELETE CASCADE by default.
    db.query(OpexAttachment).filter(OpexAttachment.opex_id == item.id).delete(synchronize_session=False)
    db.query(OpexAsset).filter(OpexAsset.opex_id == item.id).delete(synchronize_session=False)
    db.query(SpendReminderLog).filter(SpendReminderLog.opex_id == item.id).delete(synchronize_session=False)
    for f in file_rows:
        db.delete(f)
    db.flush()
    db.delete(item)
    _commit(db)
    for f in file_rows:
        files.remove(f)
    return {"ok": True}


@router.get("/opex/{code}/history")
def opex_history(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    item = _item(db, code)
    # audit detail is JSON with "code": "<code>" — the code format makes the LIKE exact enough.
    rows = (db.query(AuditLog).filter(AuditLog.action.like("spend.opex.%"),
                                      or_(AuditLog.detail.like(f'%"code": "{item.code}"%'),
                                          AuditLog.detail.like(f'%"newCode": "{item.code}"%')))
            .order_by(AuditLog.at.desc()).limit(200).all())
    return [{"at": to_iso_utc(r.at), "username": r.username, "action": r.action, "detail": r.detail} for r in rows]


# ---------------------------------------------------------------- attachments
@router.post("/opex/{code}/attachments")
async def upload_attachment(code: str, request: Request, file: UploadFile = File(...),
                            user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    item = _item(db, code)
    if db.query(OpexAttachment).filter_by(opex_id=item.id).count() >= 50:
        raise HTTPException(400, "Tối đa 50 file đính kèm cho một khoản.")
    data = await files.read_upload(file)
    row, tmp, final = files.store(db, data, file.filename, user.username)
    try:
        db.add(OpexAttachment(opex_id=item.id, file_id=row.id))
        service.audit(db, user, request, "spend.opex.attach", code=item.code, file=row.original_name, sha256=row.sha256)
        db.commit()
        files.finalize(tmp, final)
    except Exception:
        db.rollback()
        files.discard(tmp)
        raise
    return service.attachments(db, item)


@router.delete("/opex/{code}/attachments/{file_id}")
def delete_attachment(code: str, file_id: int, request: Request, user: User = Depends(require_spend("editor")),
                      db: Session = Depends(get_db)):
    item = _item(db, code)
    link = db.query(OpexAttachment).filter_by(opex_id=item.id, file_id=file_id).first()
    if not link:
        raise HTTPException(404, "Không tìm thấy file đính kèm.")
    row = db.get(SpendFile, file_id)
    service.audit(db, user, request, "spend.opex.detach", code=item.code, file=row.original_name, sha256=row.sha256)
    db.delete(link)
    db.flush()
    db.delete(row)
    db.commit()
    files.remove(row)
    return service.attachments(db, item)


@router.get("/files/{file_id}")
def download_file(file_id: int, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    row = db.get(SpendFile, file_id)
    if not row:
        raise HTTPException(404, "Không tìm thấy file.")
    if row.purged_at:
        raise HTTPException(410, "File đã được xoá theo chính sách lưu trữ (còn giữ thông tin kiểm toán).")
    path = files.path_of(row)
    if not path.is_file():
        raise HTTPException(404, "File không còn trên volume lưu trữ.")
    ascii_name = "".join(c if c.isascii() and c.isalnum() or c in "._-" else "_" for c in row.original_name)
    return FileResponse(path, media_type=row.mime, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(row.original_name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


# ---------------------------------------------------------------- hosts known from vCenter (soft link)
@router.get("/hosts")
def known_hosts(q: Optional[str] = None, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    """Hosts of the latest successful live run per vCenter, to link a device to inventory."""
    latest = (db.query(SnapshotRun.vcenter_key, func.max(SnapshotRun.id))
              .filter(SnapshotRun.status == "ok").group_by(SnapshotRun.vcenter_key).all())
    run_ids = [rid for _, rid in latest]
    if not run_ids:
        return []
    qry = db.query(HostSnapshot.vcenter_key, HostSnapshot.host, HostSnapshot.vendor, HostSnapshot.model, HostSnapshot.cluster) \
        .filter(HostSnapshot.run_id.in_(run_ids))
    if q:
        qry = qry.filter(HostSnapshot.host.ilike(f"%{q.strip()[:100]}%"))
    return [{"vcenterKey": r.vcenter_key, "hostName": r.host, "vendor": r.vendor, "model": r.model, "cluster": r.cluster}
            for r in qry.order_by(HostSnapshot.host).limit(100)]
