"""Tài sản → Thiết bị Data Center (dc_assets.py).

GET    /api/assets/dc                          — danh sách + tuổi + bảo hành / license ghép theo SN (mọi người dùng;
                                                 chi tiết Opex chỉ cho người có quyền xem Chi phí)
POST   /api/assets/dc/import/preview           — đọc file Excel, không ghi (admin)
POST   /api/assets/dc/import/commit?markMissing — ghi các dòng hợp lệ trong một giao dịch (admin)
POST   /api/assets/dc · PUT /api/assets/dc/{id} — thêm / sửa tay một thiết bị (admin)
POST   /api/assets/dc/decision                 — quyết định Thay thế / Dùng tiếp (admin hoặc Biên tập Chi phí)
DELETE /api/assets/dc/{id}                     — xoá một thiết bị (admin)
File .xlsx tối đa 10 MB."""
import json

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from .. import dc_assets, inbox
from ..config import get_settings
from ..database import get_db
from ..features import get_features
from ..models import AuditLog, DcAsset, User
from ..security import get_current_user, require_admin
from ..shared.xlsx_io import read_upload
from ..spend.fiscal import today_vn

router = APIRouter(prefix="/api/assets/dc", tags=["dc-assets"])
MAX_BYTES = 10 * 1024 * 1024


def _opex_detail(db: Session, user: User) -> bool:
    from ..services.entities import _spend_ok
    return _spend_ok(user, get_features(db))


@router.get("")
def list_assets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = get_settings()
    return dc_assets.listing(db, today_vn(), s.asset_age_limit_months, s.asset_age_warn_months, _opex_detail(db, user))


@router.get("/warranty-check")
def warranty_check(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dell warranty (OME) ↔ Opex reconciliation (hardware/warranty.py). Opex code / title only for users who may
    see Chi phí; the others get the coverage state and end date."""
    from sqlalchemy import func
    from ..hardware import warranty
    from ..hardware.models import HwSourceState
    keys = [c.key for c in get_settings().hardware()]
    detail = _opex_detail(db, user)
    items = warranty.reconcile(db, today_vn()) if keys else []
    if not detail:
        for it in items:
            if it["opex"]:
                it["opex"] = {"state": it["opex"]["state"], "end": it["opex"]["end"]}
    at = db.query(func.max(HwSourceState.warranty_at)).filter(HwSourceState.source_key.in_(keys or [""])).scalar()
    return {"configured": bool(keys), "warrantyAt": at.isoformat() + "Z" if at else None, "opexDetail": detail,
            "cases": [{"case": k, "code": v[0], "label": v[1], "level": v[2]} for k, v in warranty.CASES.items()],
            "items": items}


@router.post("/import/preview")
async def preview(request: Request, file: UploadFile = File(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    data = await read_upload(request, file, MAX_BYTES)

    def _run():
        parsed = dc_assets.parse(data)
        return dc_assets.public_plan(parsed, dc_assets.plan(db, parsed, False))
    try:
        return await run_in_threadpool(_run)
    except dc_assets.ImportError_ as exc:
        raise HTTPException(422, str(exc))


@router.post("/import/commit")
async def commit(request: Request, markMissing: bool = False, file: UploadFile = File(...),
                 user: User = Depends(require_admin), db: Session = Depends(get_db)):
    data = await read_upload(request, file, MAX_BYTES)
    name = (file.filename or "")[:120]

    def _run():
        parsed = dc_assets.parse(data)
        p = dc_assets.plan(db, parsed, markMissing)
        try:
            res = dc_assets.apply(db, p, user.username)
            db.add(AuditLog(username=user.username, action="dc_assets.import",
                            client_ip=request.client.host if request.client else None,
                            detail=json.dumps({"file": name, **res, "errors": len(parsed["errors"])})))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {**res, "errors": len(parsed["errors"])}
    try:
        res = await run_in_threadpool(_run)
    except dc_assets.ImportError_ as exc:
        raise HTTPException(422, str(exc))
    inbox.invalidate()
    return res


def _save(request: Request, db: Session, user: User, a, data: Dict[str, Any], action: str):
    try:
        a = dc_assets.save_manual(db, a, data, user.username)
        db.flush()
    except dc_assets.ImportError_ as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    db.add(AuditLog(username=user.username, action=action, client_ip=request.client.host if request.client else None,
                    detail=json.dumps({"id": a.id, "deviceId": a.device_id, "assetTag": a.asset_tag})))
    db.commit()
    inbox.invalidate()
    return {"id": a.id}


@router.post("")
def create_asset(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    return _save(request, db, user, None, data, "dc_assets.create")


@router.put("/{asset_id}")
def update_asset(asset_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    a = db.get(DcAsset, asset_id)
    if not a:
        raise HTTPException(404, "Không có thiết bị này.")
    return _save(request, db, user, a, data, "dc_assets.update")


def _decider(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    """Lifecycle decisions belong to the budget planners too: admin, or a spend editor (Chi phí bật)."""
    from ..security import is_admin
    from ..spend.roles import has_role
    if is_admin(user) or (get_features(db).get("spend") is not False and has_role(user, "editor")):
        return user
    raise HTTPException(403, "Cần quyền quản trị hoặc Biên tập Chi phí.")


@router.post("/decision")
def decide(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(_decider), db: Session = Depends(get_db)):
    """Thay thế / Dùng tiếp for some devices: `ids`, or every device of a plan line (`groupOf` = its sourceId,
    `fiscalYear` = the plan's year)."""
    if data.get("groupOf"):
        from ..spend import fiscal, settings as spend_settings
        try:
            year = int(data.get("fiscalYear"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Thiếu năm kế hoạch.")
        _ys, ye = fiscal.fiscal_range(year, spend_settings.load(db).fiscal_start_month)
        assets = dc_assets.group_of(db, int(data["groupOf"]), ye)
    else:
        ids = [int(i) for i in (data.get("ids") or [])][:2000]
        assets = db.query(DcAsset).filter(DcAsset.id.in_(ids or [-1])).all()
    if not assets:
        raise HTTPException(404, "Không có thiết bị nào.")
    try:
        n = dc_assets.set_decision(db, assets, data, user.username)
    except dc_assets.ImportError_ as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    db.add(AuditLog(username=user.username, action="dc_assets.decision", client_ip=request.client.host if request.client else None,
                    detail=json.dumps({"decision": data.get("decision"), "reason": data.get("reason"), "until": data.get("until"),
                                       "devices": [a.device_id for a in assets][:50], "count": n})))
    db.commit()
    inbox.invalidate()
    return {"updated": n}


@router.delete("/{asset_id}")
def delete_asset(asset_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.get(DcAsset, asset_id)
    if not a:
        raise HTTPException(404, "Không có thiết bị này.")
    db.add(AuditLog(username=user.username, action="dc_assets.delete", client_ip=request.client.host if request.client else None,
                    detail=json.dumps({"deviceId": a.device_id, "assetTag": a.asset_tag})))
    db.delete(a)
    db.commit()
    inbox.invalidate()
    return {"ok": True}
