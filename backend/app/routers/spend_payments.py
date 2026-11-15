"""Chi phí & Hợp đồng — payment instalments of a contract and their document checklist.

Rules (enforced here, never only in the UI):
* instalments and checklist are edited while the instalment is pending (editor);
* "submitted" (documents handed to accounting) only when every required document is ticked;
* "paid" is recorded by an approver; undoing a payment is admin only;
* every id in a path is looked up inside the contract named by the path (no cross-contract access).
"""
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..features import require_feature
from ..models import User
from ..spend import files, fiscal, service
from ..spend.models import (
    CapexItem, Contract, ContractPayment, OpexItem, PaymentDocDefault, PaymentDocument, SpendFile, SpendVendor,
)
from ..spend.roles import require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend", tags=["spend-payments"], dependencies=[Depends(require_feature("spend"))])
STATUS_LABELS = {"pending": "Chờ hồ sơ", "submitted": "Đã nộp hồ sơ", "paid": "Đã thanh toán"}
DUE_SOON_DAYS = 30
MAX_PAYMENTS = 60
MAX_DOCS = 40


def _bad(msg: str):
    raise HTTPException(400, msg)


def _contract(db: Session, code: str) -> Contract:
    c = db.query(Contract).filter(Contract.code == code).first()
    if not c:
        raise HTTPException(404, f"Không tìm thấy hợp đồng {code}.")
    return c


def _payment(db: Session, c: Contract, payment_id: int) -> ContractPayment:
    p = db.query(ContractPayment).filter(ContractPayment.id == payment_id, ContractPayment.contract_id == c.id).first()
    if not p:
        raise HTTPException(404, "Không tìm thấy đợt thanh toán của hợp đồng này.")
    return p


def _doc(db: Session, p: ContractPayment, doc_id: int) -> PaymentDocument:
    d = db.query(PaymentDocument).filter(PaymentDocument.id == doc_id, PaymentDocument.payment_id == p.id).first()
    if not d:
        raise HTTPException(404, "Không tìm thấy hồ sơ trong đợt này.")
    return d


def contract_value(db: Session, c: Contract) -> Optional[int]:
    """Reference value after VAT: the linked Opex item, or the total of the Capex project's items."""
    if c.opex_id:
        o = db.get(OpexItem, c.opex_id)
        return o.amount_gross if o else None
    total = db.query(func.coalesce(func.sum(CapexItem.amount_gross), 0)).filter(CapexItem.project_id == c.capex_project_id).scalar()
    return int(total or 0)


def _tone(p: ContractPayment, today: dt.date) -> Dict[str, Any]:
    if p.status == "paid":
        return {"tone": "ok", "dueLabel": "Đã thanh toán"}
    if p.due_date is None:
        return {"tone": "na", "dueLabel": p.condition or "Chưa có hạn"}
    days = (p.due_date - today).days
    if days < 0:
        return {"tone": "crit", "dueLabel": f"Quá hạn {-days} ngày", "daysLeft": days}
    if days <= DUE_SOON_DAYS:
        return {"tone": "warn", "dueLabel": f"Còn {days} ngày", "daysLeft": days}
    return {"tone": "na", "dueLabel": f"Còn {days} ngày", "daysLeft": days}


def _out(db: Session, c: Contract) -> Dict[str, Any]:
    today = fiscal.today_vn()
    payments = db.query(ContractPayment).filter_by(contract_id=c.id).order_by(ContractPayment.seq).all()
    docs: Dict[int, List[PaymentDocument]] = {}
    for d in (db.query(PaymentDocument).filter(PaymentDocument.payment_id.in_([p.id for p in payments]))
              .order_by(PaymentDocument.sort_order, PaymentDocument.id) if payments else []):
        docs.setdefault(d.payment_id, []).append(d)
    fl = {f.id: f for f in db.query(SpendFile).filter(SpendFile.id.in_([d.file_id for ds in docs.values() for d in ds if d.file_id]))}
    value = contract_value(db, c)
    planned = sum(p.amount for p in payments)
    paid = sum((p.paid_amount if p.paid_amount is not None else p.amount) for p in payments if p.status == "paid")
    items = []
    for p in payments:
        ds = docs.get(p.id, [])
        req = [d for d in ds if d.required]
        items.append({
            "id": p.id, "seq": p.seq, "title": p.title, "amount": p.amount,
            "percent": float(p.percent) if p.percent is not None else None,
            "dueDate": p.due_date.isoformat() if p.due_date else None, "condition": p.condition,
            "status": p.status, "statusLabel": STATUS_LABELS[p.status], **_tone(p, today),
            "submittedAt": to_iso_utc(p.submitted_at), "submittedBy": p.submitted_by,
            "paidAt": p.paid_at.isoformat() if p.paid_at else None, "paidBy": p.paid_by, "paidAmount": p.paid_amount,
            "paymentRef": p.payment_ref, "note": p.note, "rowVersion": p.row_version,
            "docsDone": sum(1 for d in ds if d.done), "docsTotal": len(ds),
            "requiredMissing": [d.name for d in req if not d.done],
            "documents": [{"id": d.id, "name": d.name, "required": d.required, "done": d.done, "doneAt": to_iso_utc(d.done_at),
                           "doneBy": d.done_by, "note": d.note,
                           "file": service.file_out(fl[d.file_id]) if d.file_id in fl else None} for d in ds],
        })
    return {
        "contractCode": c.code, "contractValue": value, "plannedTotal": planned, "paidTotal": paid,
        "remaining": planned - paid, "differenceFromValue": (planned - value) if value is not None else None,
        "overdue": sum(1 for i in items if i["tone"] == "crit"), "items": items,
    }


def _touch(p: ContractPayment, user: User) -> None:
    p.row_version += 1
    p.updated_at, p.updated_by = dt.datetime.utcnow(), user.username


def _snapshot(p: ContractPayment) -> Dict[str, Any]:
    return {"seq": p.seq, "title": p.title, "amount": p.amount, "dueDate": p.due_date, "status": p.status,
            "paidAt": p.paid_at, "paidAmount": p.paid_amount}


def _apply(db: Session, c: Contract, p: ContractPayment, data: Dict[str, Any]) -> None:
    p.title = service._text(data.get("title"), "nội dung đợt", 255, required=True)
    pct = data.get("percent")
    if pct in (None, ""):
        p.percent = None
    else:
        try:
            p.percent = Decimal(str(pct)).quantize(Decimal("0.01"))
        except ArithmeticError:
            _bad("Tỷ lệ % không hợp lệ.")
        if not Decimal(0) <= p.percent <= Decimal(100):
            _bad("Tỷ lệ % phải từ 0 đến 100.")
    if data.get("amount") not in (None, ""):
        p.amount = service._money(data.get("amount"), "Số tiền đợt")
    elif p.percent is not None:
        value = contract_value(db, c)
        if not value:
            _bad("Hợp đồng chưa có giá trị tham chiếu (Opex / Capex) để tính theo % — nhập số tiền.")
        p.amount = int((Decimal(value) * p.percent / Decimal(100)).quantize(Decimal(1), ROUND_HALF_UP))
    else:
        _bad("Nhập số tiền hoặc tỷ lệ % của đợt.")
    due = data.get("dueDate")
    p.due_date = service._date(due, "Hạn thanh toán") if due else None
    p.condition = service._text(data.get("condition"), "điều kiện thanh toán", 500)
    p.note = service._text(data.get("note"), "ghi chú", 2000)


# ---------------------------------------------------------------- instalments
@router.get("/contracts/{code}/payments")
def list_payments(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return _out(db, _contract(db, code))


@router.post("/contracts/{code}/payments")
def create_payment(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    c = _contract(db, code)
    if db.query(ContractPayment).filter_by(contract_id=c.id).count() >= MAX_PAYMENTS:
        _bad(f"Tối đa {MAX_PAYMENTS} đợt thanh toán.")
    seq = (db.query(func.max(ContractPayment.seq)).filter_by(contract_id=c.id).scalar() or 0) + 1
    p = ContractPayment(contract_id=c.id, seq=seq, status="pending", created_by=user.username, row_version=1)
    _apply(db, c, p, data)
    db.add(p)
    db.flush()
    names = data.get("documents")
    if names is None:      # default checklist from Settings
        defaults = db.query(PaymentDocDefault).filter_by(active=True).order_by(PaymentDocDefault.sort_order, PaymentDocDefault.id).all()
        rows = [(d.name, d.required) for d in defaults]
    else:
        rows = [(service._text(n.get("name") if isinstance(n, dict) else n, "tên hồ sơ", 255, required=True),
                 bool(n.get("required", True)) if isinstance(n, dict) else True) for n in names[:MAX_DOCS]]
    db.add_all([PaymentDocument(payment_id=p.id, name=n, required=r, sort_order=i) for i, (n, r) in enumerate(rows)])
    service.audit(db, user, request, "spend.payment.create", code=c.code, seq=seq, after=_snapshot(p))
    db.commit()
    return _out(db, c)


@router.put("/contracts/{code}/payments/{payment_id}")
def update_payment(code: str, payment_id: int, request: Request, data: Dict[str, Any] = Body(...),
                   user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    if p.status != "pending":
        raise HTTPException(409, "Chỉ sửa được đợt đang chờ hồ sơ — mở lại đợt trước (quyền Duyệt).")
    if int(data.get("rowVersion") or 0) != p.row_version:
        raise HTTPException(409, "Đợt này vừa được người khác sửa — tải lại.")
    before = _snapshot(p)
    _apply(db, c, p, data)
    _touch(p, user)
    service.audit(db, user, request, "spend.payment.update", code=c.code, seq=p.seq, changes=service.diff(before, _snapshot(p)))
    db.commit()
    return _out(db, c)


@router.delete("/contracts/{code}/payments/{payment_id}")
def delete_payment(code: str, payment_id: int, request: Request, user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    if p.status != "pending":
        raise HTTPException(409, "Chỉ xoá được đợt đang chờ hồ sơ.")
    ds = db.query(PaymentDocument).filter_by(payment_id=p.id).all()
    fl = [db.get(SpendFile, d.file_id) for d in ds if d.file_id]
    service.audit(db, user, request, "spend.payment.delete", code=c.code, before=_snapshot(p))
    for d in ds:
        db.delete(d)
    db.flush()
    for f in fl:
        db.delete(f)
    db.delete(p)
    db.commit()
    for f in fl:
        files.remove(f)
    return _out(db, c)


# ---------------------------------------------------------------- checklist
@router.post("/contracts/{code}/payments/{payment_id}/documents")
def add_document(code: str, payment_id: int, request: Request, data: Dict[str, Any] = Body(...),
                 user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    if p.status != "pending":
        raise HTTPException(409, "Đợt đã nộp hồ sơ / đã thanh toán — không thêm hồ sơ.")
    if db.query(PaymentDocument).filter_by(payment_id=p.id).count() >= MAX_DOCS:
        _bad(f"Tối đa {MAX_DOCS} hồ sơ cho một đợt.")
    order = (db.query(func.max(PaymentDocument.sort_order)).filter_by(payment_id=p.id).scalar() or 0) + 1
    name = service._text(data.get("name"), "tên hồ sơ", 255, required=True)
    db.add(PaymentDocument(payment_id=p.id, name=name, required=bool(data.get("required", True)), sort_order=order))
    service.audit(db, user, request, "spend.payment.doc_add", code=c.code, seq=p.seq, doc=name)
    db.commit()
    return _out(db, c)


@router.put("/contracts/{code}/payments/{payment_id}/documents/{doc_id}")
def tick_document(code: str, payment_id: int, doc_id: int, request: Request, data: Dict[str, Any] = Body(...),
                  user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    d = _doc(db, p, doc_id)
    if p.status != "pending":
        raise HTTPException(409, "Đợt đã nộp hồ sơ / đã thanh toán — mở lại đợt để sửa checklist.")
    if "done" in data:
        done = bool(data["done"])
        if done != d.done:
            d.done = done
            d.done_at, d.done_by = (dt.datetime.utcnow(), user.username) if done else (None, None)
            service.audit(db, user, request, "spend.payment.doc_tick", code=c.code, seq=p.seq, doc=d.name, done=done)
    if "note" in data:
        d.note = service._text(data.get("note"), "ghi chú hồ sơ", 500)
    if "required" in data:
        d.required = bool(data["required"])
    db.commit()
    return _out(db, c)


@router.delete("/contracts/{code}/payments/{payment_id}/documents/{doc_id}")
def delete_document(code: str, payment_id: int, doc_id: int, request: Request, user: User = Depends(require_spend("editor")),
                    db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    d = _doc(db, p, doc_id)
    if p.status != "pending":
        raise HTTPException(409, "Đợt đã nộp hồ sơ / đã thanh toán — không xoá hồ sơ.")
    f = db.get(SpendFile, d.file_id) if d.file_id else None
    service.audit(db, user, request, "spend.payment.doc_delete", code=c.code, seq=p.seq, doc=d.name)
    db.delete(d)
    db.flush()
    if f is not None:
        db.delete(f)
    db.commit()
    if f is not None:
        files.remove(f)
    return _out(db, c)


@router.post("/contracts/{code}/payments/{payment_id}/documents/{doc_id}/file")
async def attach_document_file(code: str, payment_id: int, doc_id: int, request: Request, file: UploadFile = File(...),
                               user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    d = _doc(db, p, doc_id)
    if p.status != "pending":
        raise HTTPException(409, "Đợt đã nộp hồ sơ / đã thanh toán — mở lại đợt để thay file hồ sơ.")
    data = await files.read_upload(file)
    row, tmp, final = files.store(db, data, file.filename, user.username)
    old = db.get(SpendFile, d.file_id) if d.file_id else None
    try:
        d.file_id = row.id
        db.flush()
        if old is not None:
            db.delete(old)
        service.audit(db, user, request, "spend.payment.doc_file", code=c.code, seq=p.seq, doc=d.name, file=row.original_name,
                      sha256=row.sha256)
        db.commit()
        files.finalize(tmp, final)
    except Exception:
        db.rollback()
        files.discard(tmp)
        raise
    if old is not None:
        files.remove(old)
    return _out(db, c)


# ---------------------------------------------------------------- status
@router.post("/contracts/{code}/payments/{payment_id}/submit")
def submit_payment(code: str, payment_id: int, request: Request, user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    if p.status != "pending":
        raise HTTPException(409, "Đợt không ở trạng thái chờ hồ sơ.")
    missing = [d.name for d in db.query(PaymentDocument).filter_by(payment_id=p.id, required=True, done=False)]
    if missing:
        raise HTTPException(409, "Còn thiếu hồ sơ bắt buộc: " + "; ".join(missing[:10]))
    p.status, p.submitted_at, p.submitted_by = "submitted", dt.datetime.utcnow(), user.username
    _touch(p, user)
    service.audit(db, user, request, "spend.payment.submit", code=c.code, seq=p.seq)
    db.commit()
    return _out(db, c)


@router.post("/contracts/{code}/payments/{payment_id}/reopen")
def reopen_payment(code: str, payment_id: int, request: Request, user: User = Depends(require_spend("approver")),
                   db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    if p.status != "submitted":
        raise HTTPException(409, "Chỉ mở lại được đợt đã nộp hồ sơ, chưa thanh toán.")
    p.status, p.submitted_at, p.submitted_by = "pending", None, None
    _touch(p, user)
    service.audit(db, user, request, "spend.payment.reopen", code=c.code, seq=p.seq)
    db.commit()
    return _out(db, c)


@router.post("/contracts/{code}/payments/{payment_id}/paid")
def mark_paid(code: str, payment_id: int, request: Request, data: Dict[str, Any] = Body(default={}),
              user: User = Depends(require_spend("approver")), db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    if p.status != "submitted":
        raise HTTPException(409, "Chỉ ghi nhận thanh toán cho đợt đã nộp đủ hồ sơ.")
    p.paid_at = service._date(data["paidAt"], "Ngày thanh toán") if data.get("paidAt") else fiscal.today_vn()
    p.paid_amount = service._money(data["paidAmount"], "Số tiền đã trả") if data.get("paidAmount") not in (None, "") else p.amount
    p.payment_ref = service._text(data.get("paymentRef"), "số chứng từ", 64)
    p.status, p.paid_by = "paid", user.username
    _touch(p, user)
    service.audit(db, user, request, "spend.payment.paid", code=c.code, seq=p.seq, paidAt=p.paid_at, paidAmount=p.paid_amount,
                  paymentRef=p.payment_ref)
    db.commit()
    return _out(db, c)


@router.post("/contracts/{code}/payments/{payment_id}/unpay")
def undo_paid(code: str, payment_id: int, request: Request, user: User = Depends(require_spend("admin")),
              db: Session = Depends(get_db)):
    c = _contract(db, code)
    p = _payment(db, c, payment_id)
    if p.status != "paid":
        raise HTTPException(409, "Đợt chưa được ghi nhận thanh toán.")
    before = _snapshot(p)
    p.status, p.paid_at, p.paid_by, p.paid_amount, p.payment_ref = "submitted", None, None, None, None
    _touch(p, user)
    service.audit(db, user, request, "spend.payment.unpay", code=c.code, seq=p.seq, before=before)
    db.commit()
    return _out(db, c)


# ---------------------------------------------------------------- default checklist (admin)
@router.get("/admin/payment-docs")
def list_defaults(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return [{"id": d.id, "name": d.name, "required": d.required, "active": d.active}
            for d in db.query(PaymentDocDefault).order_by(PaymentDocDefault.sort_order, PaymentDocDefault.id)]


@router.put("/admin/payment-docs")
def replace_defaults(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("admin")),
                     db: Session = Depends(get_db)):
    items = data.get("items") or []
    if len(items) > MAX_DOCS:
        _bad(f"Tối đa {MAX_DOCS} hồ sơ mặc định.")
    seen, rows = set(), []
    for i, it in enumerate(items):
        name = service._text(it.get("name"), f"tên hồ sơ dòng {i + 1}", 255, required=True)
        if name.lower() in seen:
            _bad(f"Trùng tên hồ sơ: {name}")
        seen.add(name.lower())
        rows.append(PaymentDocDefault(name=name, required=bool(it.get("required", True)), active=bool(it.get("active", True)),
                                      sort_order=i))
    db.query(PaymentDocDefault).delete(synchronize_session=False)
    db.flush()
    db.add_all(rows)
    service.audit(db, user, request, "spend.payment.defaults", count=len(rows))
    db.commit()
    return list_defaults(user, db)


@router.get("/payments")
def all_payments(status: str = "open", user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    """Every instalment of every contract (Hợp đồng & thanh toán → Đợt thanh toán), soonest first.
    status: open (chưa thanh toán) | paid | all."""
    if status not in ("open", "paid", "all"):
        _bad("Trạng thái lọc không hợp lệ.")
    q = db.query(ContractPayment, Contract).join(Contract, Contract.id == ContractPayment.contract_id)
    if status == "open":
        q = q.filter(ContractPayment.status.in_(("pending", "submitted")))
    elif status == "paid":
        q = q.filter(ContractPayment.status == "paid")
    rows = q.order_by(ContractPayment.due_date.is_(None), ContractPayment.due_date, Contract.code, ContractPayment.seq).limit(1000).all()
    today = fiscal.today_vn()
    vendors = {v.id: service.vendor_label(v) for v in db.query(SpendVendor).filter(SpendVendor.id.in_({c.vendor_id for _, c in rows if c.vendor_id} or {0}))}
    docs: Dict[int, List[PaymentDocument]] = {}
    for d in db.query(PaymentDocument).filter(PaymentDocument.payment_id.in_([p.id for p, _ in rows] or [0])):
        docs.setdefault(d.payment_id, []).append(d)
    out = []
    for p, c in rows:
        ds = docs.get(p.id, [])
        out.append({"contractCode": c.code, "contractTitle": c.title, "vendor": vendors.get(c.vendor_id), "seq": p.seq, "id": p.id,
                    "title": p.title, "amount": p.amount, "dueDate": p.due_date.isoformat() if p.due_date else None,
                    "condition": p.condition, "status": p.status, "statusLabel": STATUS_LABELS[p.status],
                    "paidAt": p.paid_at.isoformat() if p.paid_at else None, "paidAmount": p.paid_amount,
                    "docsDone": sum(1 for d in ds if d.done), "docsTotal": len(ds),
                    "requiredMissing": [d.name for d in ds if d.required and not d.done], **_tone(p, today)})
    return {"items": out, "openTotal": sum(x["amount"] for x in out if x["status"] != "paid")}
