"""Hệ thống & Owner (ICO v2 G2a) — owners/service.py, owners/excel.py.

Read (every signed-in user):
GET  /api/systems?q=&owner=&department=&project=&criticality=&env=&status=&offset=&limit=   (X-Total-Count)
GET  /api/systems/facets · /api/systems/coverage?q=&vcenter=&offset=&limit= · /api/systems/export.xlsx
GET  /api/systems/{id} · /api/systems/{id}/members · /api/systems/vm/{ci_id}
Write (admin or a user with "Quản trị Hệ thống & Owner"):
POST /api/systems · PUT /api/systems/{id} · DELETE /api/systems/{id}
POST /api/systems/{id}/members {ciIds, move} · DELETE /api/systems/{id}/members {ciIds}
POST /api/systems/import/preview · /api/systems/import/commit   (.xlsx ≤ 10 MB)
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..owners import excel, service
from ..pagination import paginate
from ..security import get_current_user
from ..shared.filetype import XLSX, FileRejected, detect_type

router = APIRouter(prefix="/api/systems", tags=["owners"])
MAX_BYTES = 10 * 1024 * 1024
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def require_owner_admin(user: User = Depends(get_current_user)) -> User:
    if not service.can_manage(user):
        raise HTTPException(403, "Cần quyền quản trị Hệ thống & Owner (Cài đặt → Người dùng & quyền).")
    return user


def _ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _ids(data: Dict[str, Any]) -> list:
    ids = data.get("ciIds")
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise HTTPException(422, "ciIds phải là danh sách số nguyên.")
    if len(ids) > service.MAX_SELECT:
        raise HTTPException(422, f"Tối đa {service.MAX_SELECT} VM mỗi lần.")
    return ids


def _invalid(fn):
    try:
        return fn()
    except service.Invalid as exc:
        raise HTTPException(400, str(exc))


@router.get("")
def list_systems(response: Response, q: str = Query("", max_length=80), owner: str = Query("", max_length=255),
                 department: str = Query("", max_length=128), project: str = Query("", max_length=128),
                 criticality: Optional[int] = Query(None, ge=1, le=4), env: str = Query("", max_length=8),
                 status: str = Query("active", max_length=8), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = paginate(service.listing(db, q, owner, department, project, criticality, env, status), response, offset, limit)
    counts = service.member_counts(db, (s.id for s in rows))
    return {"items": [service.out(s, counts.get(s.id, 0)) for s in rows], "canManage": service.can_manage(user)}


@router.get("/facets")
def facets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {**service.facets(db), "envs": [{"value": k, "label": v} for k, v in service.ENV_LABELS.items()],
            "criticality": [{"value": k, "label": v} for k, v in service.CRITICALITY_LABELS.items()],
            "canManage": service.can_manage(user)}


@router.get("/coverage")
def coverage(q: str = Query("", max_length=400), vcenter: str = Query("", max_length=2000), offset: int = Query(0, ge=0),
             limit: int = Query(100, ge=1, le=500), cluster: str = Query("", max_length=4000),
             power: str = Query("", pattern="^(on|off)?$"), folder: str = Query("", max_length=255), ids: bool = Query(False),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.coverage(db, q, vcenter, offset, limit, cluster, power, folder, ids)


@router.get("/export.xlsx")
def export_xlsx(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = excel.export(db)
    return Response(data, media_type=XLSX_MIME, headers={"Content-Disposition": 'attachment; filename="he-thong-owner.xlsx"'})


@router.get("/vm/{ci_id}")
def system_of_vm(ci_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"system": service.system_of_vm(db, ci_id), "canManage": service.can_manage(user)}


@router.get("/{system_id}")
def get_system(system_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = service.get(db, system_id)
    return {**service.out(s, service.member_counts(db, [s.id]).get(s.id, 0)), "canManage": service.can_manage(user)}


@router.get("/{system_id}/members")
def get_members(system_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service.get(db, system_id)
    return {"items": service.members(db, system_id)}


@router.post("")
def create_system(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_owner_admin),
                  db: Session = Depends(get_db)):
    s = _invalid(lambda: service.create(db, user, data, _ip(request)))
    db.commit()
    return service.out(s, 0)


@router.put("/{system_id}")
def update_system(system_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_owner_admin),
                  db: Session = Depends(get_db)):
    s = service.get(db, system_id)
    _invalid(lambda: service.update_system(db, user, s, data, _ip(request)))
    db.commit()
    return service.out(s)


@router.delete("/{system_id}")
def delete_system(system_id: int, request: Request, user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    service.delete(db, user, service.get(db, system_id), _ip(request))      # 409 while the system still has VMs
    db.commit()
    return {"ok": True}


@router.post("/{system_id}/members")
def add_members(system_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_owner_admin),
                db: Session = Depends(get_db)):
    s = service.get(db, system_id)
    res = _invalid(lambda: service.assign(db, user, s, _ids(data), bool(data.get("move")), _ip(request)))
    db.commit()
    return res


@router.delete("/{system_id}/members")
def remove_members(system_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_owner_admin),
                   db: Session = Depends(get_db)):
    s = service.get(db, system_id)
    n = service.unassign(db, user, s, _ids(data), _ip(request))
    db.commit()
    return {"removed": n}


async def _read(request: Request, file: UploadFile) -> bytes:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BYTES + 64 * 1024:
        raise HTTPException(413, "File quá lớn (tối đa 10 MB).")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File quá lớn (tối đa 10 MB).")
    try:
        detect_type(data, allowed=(XLSX,))
    except FileRejected as exc:
        raise HTTPException(exc.status, f"{exc} File .xls cũ: mở bằng Excel rồi lưu lại dạng .xlsx.")
    return data


@router.post("/import/preview")
async def import_preview(request: Request, file: UploadFile = File(...), user: User = Depends(require_owner_admin),
                         db: Session = Depends(get_db)):
    data = await _read(request, file)
    try:
        return await run_in_threadpool(lambda: excel.public_plan(excel.plan(db, excel.parse(data))))
    except excel.ImportError_ as exc:
        raise HTTPException(422, str(exc))


@router.post("/import/commit")
async def import_commit(request: Request, file: UploadFile = File(...), user: User = Depends(require_owner_admin),
                        db: Session = Depends(get_db)):
    data = await _read(request, file)

    def _run():
        p = excel.plan(db, excel.parse(data))
        try:
            res = excel.apply(db, user, p, _ip(request))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return res
    try:
        return await run_in_threadpool(_run)
    except excel.ImportError_ as exc:
        raise HTTPException(422, str(exc))
