"""Hệ thống & Owner (ICO v2 G2a) — owners/service.py, owners/excel.py.

Read (every signed-in user):
GET  /api/systems?q=&owner=&department=&project=&criticality=&env=&status=&offset=&limit=   (X-Total-Count)
GET  /api/systems/facets · /api/systems/coverage?q=&vcenter=&offset=&limit= · /api/systems/export.xlsx
GET  /api/systems/{id} · /api/systems/{id}/members · /api/systems/vm/{ci_id}
Write (admin or a user with "Quản trị Hệ thống & Owner"):
POST /api/systems · PUT /api/systems/{id} · DELETE /api/systems/{id}
POST /api/systems/{id}/members {ciIds, move} · DELETE /api/systems/{id}/members {ciIds}
POST /api/systems/import/preview · /api/systems/import/commit   (.xlsx ≤ 10 MB)
Reports (every signed-in user, owner filter = the usual systems / owners / departments / projects / criticality params):
GET  /api/systems/report.pdf · /api/systems/report.xlsx      — Danh sách VM theo hệ thống (owners/inventory.py)
Review e-mail drafts, ISO 27001 (owner admin; owners/mail_drafts.py — files only, nothing is sent):
GET/PUT/DELETE /api/systems/mail-template                   — template (DELETE = back to the default)
GET  /api/systems/mail-drafts?period=&due=                   — owners that get a draft
GET  /api/systems/mail-drafts/preview?owner=&period=&due=    — subject / text / HTML of one draft (unsaved template: POST)
GET  /api/systems/mail-drafts/download?owner=&period=&due=&attach=  — one .eml, or every owner as .zip without owner
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..database import get_db
from ..downloads import attachment, stamp
from ..models import User
from ..owners import excel, service
from ..pagination import paginate
from ..owners import mail_drafts
from ..scope import OwnerScope, owner_scope_query
from ..security import get_current_user
from ..shared.filetype import XLSX, FileRejected, detect_type

router = APIRouter(prefix="/api/systems", tags=["owners"])
MAX_BYTES = 10 * 1024 * 1024


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
    return attachment(data, "he-thong-owner.xlsx")


@router.get("/report.pdf")
def report_pdf(osc: OwnerScope = Depends(owner_scope_query), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from .. import report_systems
    from ..owners import inventory
    data = report_systems.build_pdf(inventory.build(db, osc), user.display_name or user.username)
    return attachment(data, f"vm-theo-he-thong-{stamp()}.pdf")


@router.get("/report.xlsx")
def report_xlsx(osc: OwnerScope = Depends(owner_scope_query), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from .. import excel_systems
    from ..owners import inventory
    data = excel_systems.build_xlsx(inventory.build(db, osc))
    return attachment(data, f"vm-theo-he-thong-{stamp()}.xlsx")


def _draft_args(period: Optional[str], due: Optional[str]):
    try:
        return mail_drafts.clean_period(period, due)
    except mail_drafts.Invalid as exc:
        raise HTTPException(400, str(exc))


@router.get("/mail-template")
def mail_template(user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    return mail_drafts.get_template(db)


@router.put("/mail-template")
def save_mail_template(data: Dict[str, Any] = Body(...), user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    try:
        out = mail_drafts.save_template(db, user, data.get("subject"), data.get("body"))
    except mail_drafts.Invalid as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return out


@router.delete("/mail-template")
def reset_mail_template(user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    out = mail_drafts.reset_template(db, user)
    db.commit()
    return out


@router.get("/mail-drafts")
def mail_draft_list(period: str = Query("", max_length=16), due: str = Query("", max_length=10),
                    osc: OwnerScope = Depends(owner_scope_query), user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    p, d = _draft_args(period, due)
    res = mail_drafts.owners(db, osc)
    return {"period": p, "due": d.isoformat(), "owners": [mail_drafts.summary(o) for o in res["owners"]], "noOwner": res["noOwner"],
            "truncated": res["truncated"], "scope": res["scope"]}


def _one_owner(db: Session, osc: OwnerScope, owner: str) -> Dict[str, Any]:
    email = owner.strip().lower()
    res = mail_drafts.owners(db, osc, owner=email)
    o = next((x for x in res["owners"] if x["email"] == email), None)
    if o is None:
        raise HTTPException(404, "Không có owner này trong phạm vi.")
    return {**o, "_backup": res["backupMonitored"], "_scope": res["scope"]}


def _sender(user: User) -> str:
    return user.display_name or user.email or user.username


@router.api_route("/mail-drafts/preview", methods=["GET", "POST"])
def mail_draft_preview(owner: str = Query(..., max_length=255), period: str = Query("", max_length=16), due: str = Query("", max_length=10),
                       data: Optional[Dict[str, Any]] = Body(default=None), osc: OwnerScope = Depends(owner_scope_query),
                       user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    """GET = the saved template; POST {subject, body} = preview of an unsaved template (nothing stored)."""
    p, d = _draft_args(period, due)
    tpl = mail_drafts.get_template(db)
    if data:
        try:
            tpl["subject"], tpl["body"] = mail_drafts._check(data.get("subject"), data.get("body"))
        except mail_drafts.Invalid as exc:
            raise HTTPException(400, str(exc))
    o = _one_owner(db, osc, owner)
    subject, text, body = mail_drafts.render(tpl, o, p, d, _sender(user), o["_backup"])
    return {"to": o["email"], "cc": o["cc"], "subject": subject, "text": text, "html": body}


@router.get("/mail-drafts/download")
def mail_draft_download(owner: str = Query("", max_length=255), period: str = Query("", max_length=16), due: str = Query("", max_length=10),
                        attach: bool = Query(True), osc: OwnerScope = Depends(owner_scope_query),
                        user: User = Depends(require_owner_admin), db: Session = Depends(get_db)):
    p, d = _draft_args(period, due)
    tpl = mail_drafts.get_template(db)
    sender = _sender(user)
    smtp = mail_drafts.smtp_from(db)
    if owner.strip():
        o = _one_owner(db, osc, owner)
        name, data = mail_drafts.eml(o, mail_drafts.render(tpl, o, p, d, sender, o["_backup"]), p, attach, smtp)
        service.audit(db, user, "owners.mail_drafts.download", owners=1, period=p)
        db.commit()
        return attachment(data, name)
    res = mail_drafts.owners(db, osc)
    if not res["owners"]:
        raise HTTPException(404, "Không có hệ thống nào có owner trong phạm vi.")
    files = [mail_drafts.eml(o, mail_drafts.render(tpl, o, p, d, sender, res["backupMonitored"]), p, attach, smtp) for o in res["owners"]]
    service.audit(db, user, "owners.mail_drafts.download", owners=len(files), period=p)
    db.commit()
    return attachment(mail_drafts.zip_all(files), f"email-ra-soat-{mail_drafts._slug(p)}.zip")


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
