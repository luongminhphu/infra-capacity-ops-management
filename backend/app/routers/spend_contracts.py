"""Chi phí & Hợp đồng — contracts with immutable versions (one branch), comparison, Final / Signed,
and the delayed purge of old version files."""
import datetime as dt
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..features import require_feature
from ..models import AuditLog, User
from ..spend import codes, contracts, files, fiscal, service, settings as spend_settings
from ..spend.models import (
    EDITOR_PARTIES, CapexProject, Contract, ContractVersion, OpexItem, SpendFile, SpendVendor,
)
from ..spend.roles import require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend/contracts", tags=["spend-contracts"], dependencies=[Depends(require_feature("spend"))])


def _contract(db: Session, code: str) -> Contract:
    c = db.query(Contract).filter(Contract.code == code).first()
    if not c:
        raise HTTPException(404, f"Không tìm thấy hợp đồng {code}.")
    return c


def _summary(c: Contract, links: Dict[str, Any], vendor: Optional[str]) -> Dict[str, Any]:
    return {
        "id": c.id, "code": c.code, "title": c.title, "status": c.status, "statusLabel": contracts.STATUS_LABELS[c.status],
        "vendorId": c.vendor_id, "vendorName": vendor, **links,
        "currentVersion": c.current_version_no, "finalVersion": c.final_version_no,
        "purgeDueAt": to_iso_utc(c.purge_due_at), "purgeCancelledAt": to_iso_utc(c.purge_cancelled_at),
        "purgeCancelledBy": c.purge_cancelled_by, "purgeDoneAt": to_iso_utc(c.purge_done_at),
        "updatedAt": to_iso_utc(c.updated_at or c.created_at), "updatedBy": c.updated_by or c.created_by,
    }


def _links(db: Session, rows: List[Contract]) -> Dict[int, Dict[str, Any]]:
    opex_ids = {c.opex_id for c in rows if c.opex_id}
    cpx_ids = {c.capex_project_id for c in rows if c.capex_project_id}
    opex = {o.id: o for o in db.query(OpexItem).filter(OpexItem.id.in_(opex_ids))} if opex_ids else {}
    cpx = {p.id: p for p in db.query(CapexProject).filter(CapexProject.id.in_(cpx_ids))} if cpx_ids else {}
    out = {}
    for c in rows:
        if c.opex_id:
            o = opex.get(c.opex_id)
            out[c.id] = {"linkKind": "opex", "linkCode": o.code if o else None, "linkTitle": o.title if o else None}
        else:
            p = cpx.get(c.capex_project_id)
            out[c.id] = {"linkKind": "capex", "linkCode": p.code if p else None, "linkTitle": p.name if p else None}
    return out


def _detail(db: Session, c: Contract) -> Dict[str, Any]:
    vendor = db.get(SpendVendor, c.vendor_id) if c.vendor_id else None
    versions = db.query(ContractVersion).filter_by(contract_id=c.id).order_by(ContractVersion.version_no).all()
    fl = {f.id: f for f in db.query(SpendFile).filter(SpendFile.id.in_([v.file_id for v in versions] + [c.signed_file_id or 0]))}
    signed = fl.get(c.signed_file_id)
    return {
        **_summary(c, _links(db, [c])[c.id], vendor.name if vendor else None),
        "note": c.note, "createdBy": c.created_by, "createdAt": to_iso_utc(c.created_at),
        "finalizedAt": to_iso_utc(c.finalized_at), "finalizedBy": c.finalized_by,
        "signedAt": to_iso_utc(c.signed_at), "signedBy": c.signed_by,
        "signedFile": service.file_out(signed) if signed else None,
        "versions": [{
            "versionNo": v.version_no, "party": v.editor_party, "partyLabel": contracts.PARTY_LABELS[v.editor_party],
            "note": v.note, "uploadedBy": v.uploaded_by, "uploadedAt": to_iso_utc(v.uploaded_at),
            "file": service.file_out(fl[v.file_id]) if v.file_id in fl else None,
            "hasText": v.text_content is not None, "isFinal": v.version_no == c.final_version_no,
        } for v in reversed(versions)],
    }


def _touch(c: Contract, user: User) -> None:
    c.updated_at, c.updated_by = dt.datetime.utcnow(), user.username


# ---------------------------------------------------------------- list / create
@router.get("")
def list_contracts(status: Optional[str] = None, opex: Optional[str] = None, capex: Optional[str] = None,
                   q: Optional[str] = None, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    qry = db.query(Contract)
    if status in contracts.STATUS_LABELS:
        qry = qry.filter(Contract.status == status)
    if opex:
        qry = qry.filter(Contract.opex_id.in_(db.query(OpexItem.id).filter(OpexItem.code == opex)))
    if capex:
        qry = qry.filter(Contract.capex_project_id.in_(db.query(CapexProject.id).filter(CapexProject.code == capex)))
    if q:
        like = f"%{q.strip()[:100]}%"
        qry = qry.filter(Contract.code.ilike(like) | Contract.title.ilike(like))
    rows = qry.order_by(Contract.id.desc()).limit(500).all()
    links = _links(db, rows)
    vendors = dict(db.query(SpendVendor.id, SpendVendor.name).filter(SpendVendor.id.in_({c.vendor_id for c in rows if c.vendor_id})))
    return [_summary(c, links[c.id], vendors.get(c.vendor_id)) for c in rows]


@router.post("")
def create_contract(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                    db: Session = Depends(get_db)):
    title = service._text(data.get("title"), "tên hợp đồng", 255, required=True)
    opex_code, capex_code = (data.get("opexCode") or "").strip(), (data.get("capexCode") or "").strip()
    if bool(opex_code) == bool(capex_code):
        raise HTTPException(400, "Hợp đồng phải gắn với đúng một mã Opex hoặc một mã Capex.")
    c = Contract(title=title, status="draft", current_version_no=0, created_by=user.username,
                 note=service._text(data.get("note"), "ghi chú", 5000))
    if opex_code:
        o = db.query(OpexItem).filter_by(code=opex_code).first()
        if not o:
            raise HTTPException(400, f"Không có khoản Opex {opex_code}.")
        c.opex_id, c.vendor_id, year = o.id, o.vendor_id, o.fiscal_year
    else:
        p = db.query(CapexProject).filter_by(code=capex_code).first()
        if not p:
            raise HTTPException(400, f"Không có dự án Capex {capex_code}.")
        c.capex_project_id, year = p.id, p.fiscal_year
    if data.get("vendorId") or data.get("vendorName"):
        c.vendor_id = service.resolve_vendor(db, data, user, request)
    c.code = codes.next_code(db, "contract", year)
    db.add(c)
    db.flush()
    service.audit(db, user, request, "spend.contract.create", code=c.code, title=title, opex=opex_code or None, capex=capex_code or None)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Không tạo được mã hợp đồng (trùng do thao tác đồng thời) — thử lại.") from exc
    return _detail(db, c)


@router.get("/{code}")
def get_contract(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return _detail(db, _contract(db, code))


@router.put("/{code}")
def update_contract(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                    db: Session = Depends(get_db)):
    """Title, vendor and note only; the link to Opex / Capex never changes (create a new contract)."""
    c = _contract(db, code)
    if c.status in ("final", "signed"):
        raise HTTPException(409, "Hợp đồng đã chốt / đã ký không sửa được thông tin.")
    c.title = service._text(data.get("title"), "tên hợp đồng", 255, required=True)
    c.note = service._text(data.get("note"), "ghi chú", 5000)
    if data.get("vendorId") or data.get("vendorName"):
        c.vendor_id = service.resolve_vendor(db, data, user, request)
    _touch(c, user)
    service.audit(db, user, request, "spend.contract.update", code=c.code, title=c.title)
    db.commit()
    return _detail(db, c)


# ---------------------------------------------------------------- versions
@router.post("/{code}/versions")
async def upload_version(code: str, request: Request, file: UploadFile = File(...), editorParty: str = Form(...),
                         baseVersion: int = Form(...), note: Optional[str] = Form(default=None),
                         user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    if c.status not in ("draft", "negotiating"):
        raise HTTPException(409, "Hợp đồng đã chốt — không nhận version mới.")
    if editorParty not in EDITOR_PARTIES:
        raise HTTPException(400, "Bên chỉnh sửa phải là Legal, Finance hoặc Đối tác.")
    if baseVersion != c.current_version_no:
        raise HTTPException(409, f"Đã có version v{c.current_version_no} mới hơn bản bạn đang sửa (v{baseVersion}) — "
                                 f"tải v{c.current_version_no} về, cập nhật rồi upload lại.")
    data = await files.read_upload(file)
    try:
        paragraphs = contracts.docx_paragraphs(data) if data[:4] == b"PK\x03\x04" else None
    except contracts.DocxTextError as exc:
        raise HTTPException(415, str(exc)) from exc
    row, tmp, final = files.store(db, data, file.filename, user.username, allowed=(files.DOCX,))
    try:
        # Single branch: only the request that still sees `baseVersion` as current moves it forward.
        res = db.execute(update(Contract).where(Contract.id == c.id, Contract.current_version_no == baseVersion,
                                                Contract.status.in_(("draft", "negotiating")))
                         .values(current_version_no=baseVersion + 1, updated_at=dt.datetime.utcnow(), updated_by=user.username,
                                 status="negotiating" if baseVersion + 1 >= 2 else Contract.status))
        if res.rowcount != 1:
            raise HTTPException(409, "Một version khác vừa được upload — tải bản mới nhất rồi thử lại.")
        db.add(ContractVersion(contract_id=c.id, version_no=baseVersion + 1, editor_party=editorParty, file_id=row.id,
                               text_content=json.dumps(paragraphs, ensure_ascii=False),
                               note=service._text(note, "ghi chú", 1000), uploaded_by=user.username))
        service.audit(db, user, request, "spend.contract.version", code=c.code, version=baseVersion + 1, party=editorParty,
                      file=row.original_name, sha256=row.sha256)
        db.commit()
        files.finalize(tmp, final)
    except IntegrityError as exc:
        db.rollback()
        files.discard(tmp)
        raise HTTPException(409, "Một version khác vừa được upload — tải bản mới nhất rồi thử lại.") from exc
    except Exception:
        db.rollback()
        files.discard(tmp)
        raise
    db.refresh(c)
    return _detail(db, c)


@router.get("/{code}/diff")
def diff_versions(code: str, from_: int = Query(alias="from", ge=1), to: int = Query(ge=1),
                  user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    if from_ >= to:
        raise HTTPException(400, "Chọn version cũ hơn ở “từ” và mới hơn ở “đến”.")
    rows = (db.query(ContractVersion).filter(ContractVersion.contract_id == c.id, ContractVersion.version_no >= from_,
                                             ContractVersion.version_no <= to).order_by(ContractVersion.version_no).all())
    if len(rows) != to - from_ + 1:
        raise HTTPException(404, "Không có đủ các version trong khoảng này.")
    chain = []
    for v in rows:
        paras = contracts.paragraphs_of(v)
        if paras is None:
            raise HTTPException(410, f"Nội dung v{v.version_no} đã bị xoá theo chính sách lưu trữ — không so sánh được.")
        chain.append((v.version_no, v.editor_party, paras))
    out = contracts.compare(chain)
    out.update({"from": from_, "to": to, "parties": contracts.PARTY_LABELS,
                "versions": [{"versionNo": v.version_no, "party": v.editor_party, "uploadedBy": v.uploaded_by,
                              "uploadedAt": to_iso_utc(v.uploaded_at)} for v in rows]})
    return out


# ---------------------------------------------------------------- status
@router.post("/{code}/negotiate")
def start_negotiation(code: str, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    if c.status != "draft":
        raise HTTPException(409, "Chỉ hợp đồng nháp mới chuyển sang đàm phán.")
    c.status = "negotiating"
    _touch(c, user)
    service.audit(db, user, request, "spend.contract.status", code=c.code, status=["draft", "negotiating"])
    db.commit()
    return _detail(db, c)


@router.post("/{code}/finalize")
def finalize(code: str, request: Request, data: Dict[str, Any] = Body(default={}), user: User = Depends(require_spend("approver")),
             db: Session = Depends(get_db)):
    c = _contract(db, code)
    if c.status not in ("draft", "negotiating"):
        raise HTTPException(409, "Hợp đồng đã chốt.")
    if c.current_version_no < 1:
        raise HTTPException(409, "Chưa có version nào để chốt.")
    no = int(data.get("versionNo") or c.current_version_no)
    if not db.query(ContractVersion.id).filter_by(contract_id=c.id, version_no=no).first():
        raise HTTPException(400, f"Không có version v{no}.")
    cfg = spend_settings.load(db)
    before = c.status
    now = dt.datetime.utcnow()
    c.status, c.final_version_no, c.finalized_at, c.finalized_by = "final", no, now, user.username
    c.purge_due_at = now + dt.timedelta(days=cfg.purge_days)
    c.purge_cancelled_at = c.purge_cancelled_by = None
    _touch(c, user)
    service.audit(db, user, request, "spend.contract.finalize", code=c.code, status=[before, "final"], finalVersion=no,
                  purgeDueAt=c.purge_due_at)
    db.commit()
    return _detail(db, c)


@router.post("/{code}/sign")
async def sign(code: str, request: Request, file: UploadFile = File(...), user: User = Depends(require_spend("approver")),
               db: Session = Depends(get_db)):
    c = _contract(db, code)
    if c.status != "final":
        raise HTTPException(409, "Chỉ hợp đồng đã chốt Final mới cập nhật bản ký.")
    data = await files.read_upload(file)
    row, tmp, final = files.store(db, data, file.filename, user.username, allowed=(files.PDF,))
    try:
        c.status, c.signed_file_id, c.signed_at, c.signed_by = "signed", row.id, dt.datetime.utcnow(), user.username
        _touch(c, user)
        service.audit(db, user, request, "spend.contract.sign", code=c.code, file=row.original_name, sha256=row.sha256)
        db.commit()
        files.finalize(tmp, final)
    except Exception:
        db.rollback()
        files.discard(tmp)
        raise
    return _detail(db, c)


@router.post("/{code}/purge/cancel")
def cancel_purge(code: str, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    if c.purge_due_at is None or c.purge_done_at is not None:
        raise HTTPException(409, "Không có lịch xoá nào đang chờ.")
    c.purge_cancelled_at, c.purge_cancelled_by = dt.datetime.utcnow(), user.username
    _touch(c, user)
    service.audit(db, user, request, "spend.contract.purge_cancel", code=c.code)
    db.commit()
    return _detail(db, c)


@router.post("/{code}/purge/resume")
def resume_purge(code: str, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    if c.purge_cancelled_at is None or c.purge_done_at is not None:
        raise HTTPException(409, "Lịch xoá không ở trạng thái đã huỷ.")
    c.purge_cancelled_at = c.purge_cancelled_by = None
    c.purge_due_at = max(c.purge_due_at, dt.datetime.utcnow())
    _touch(c, user)
    service.audit(db, user, request, "spend.contract.purge_resume", code=c.code)
    db.commit()
    return _detail(db, c)


@router.get("/{code}/history")
def contract_history(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    rows = (db.query(AuditLog).filter(AuditLog.action.like("spend.contract.%"), AuditLog.detail.like(f'%"code": "{c.code}"%'))
            .order_by(AuditLog.at.desc()).limit(200).all())
    return [{"at": to_iso_utc(r.at), "username": r.username, "action": r.action, "detail": r.detail} for r in rows]


@router.delete("/{code}")
def delete_contract(code: str, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    if c.status in ("final", "signed"):
        raise HTTPException(409, "Hợp đồng đã chốt / đã ký không xoá được.")
    versions = db.query(ContractVersion).filter_by(contract_id=c.id).all()
    fl = db.query(SpendFile).filter(SpendFile.id.in_([v.file_id for v in versions])).all() if versions else []
    service.audit(db, user, request, "spend.contract.delete", code=c.code, title=c.title,
                  files=[{"name": f.original_name, "sha256": f.sha256} for f in fl])
    for v in versions:
        db.delete(v)
    db.flush()
    for f in fl:
        db.delete(f)
    db.delete(c)
    db.commit()
    for f in fl:
        files.remove(f)
    return {"ok": True}


@router.post("/purge/run")
def run_purge(request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    """Run the purge now (the daily job does it too); only contracts whose waiting time is over."""
    service.audit(db, user, request, "spend.contract.purge_run")
    db.commit()
    return {"purged": contracts.purge_due(db, username=user.username)}
