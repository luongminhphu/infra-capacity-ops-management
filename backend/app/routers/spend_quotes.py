"""Chi phí & Hợp đồng — vendor profiles, quotation requests (3 quotes), checks, comparison,
vendor evaluation form (EVALUATION FORM layout), selection.

Vendor documents use the vendor SHORT NAME (service.vendor_label) everywhere: comparison
headers, evaluation columns, conclusions, exports and tờ trình."""
import datetime as dt
import json
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import excel_quotes
from ..database import get_db
from ..features import require_feature
from ..models import User
from ..spend import ai, codes, files, fiscal, quotes as qm, service, settings as spend_settings
from ..spend.models import (
    WORKSTREAMS, CapexItem, CapexProject, Contract, EvaluationCriterion, EvaluationScore, OpexItem, QuoteRequest, Quotation,
    QuotationFinding, QuotationLine, RenovationProject, SpendAiConfig, SpendFile, SpendVendor,
)
from ..spend.roles import has_role, require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend", tags=["spend-quotes"], dependencies=[Depends(require_feature("spend"))])
STATUS_LABELS = {"draft": "Nháp", "comparing": "Đang so sánh", "selected": "Đã chọn NCC", "closed": "Đóng"}
STATE_LABELS = {"pending": "Chưa đọc", "extracted": "Đã đọc — chờ kiểm tra", "verified": "Đã kiểm tra", "failed": "Không đọc được"}
VENDOR_STATUS = {"active": "Đang dùng", "paused": "Tạm ngừng", "blocked": "Không hợp tác"}
MAX_LINES = 500


def _bad(msg: str):
    raise HTTPException(400, msg)


def _d(v: Any) -> Optional[Decimal]:
    return qm.parse_number(v)


def _iso(d) -> Optional[str]:
    return d.isoformat() if d else None


def _num(v: Optional[Decimal]) -> Optional[float]:
    return None if v is None else float(v)


# ================================================================ vendors
def _vendor_out(v: SpendVendor) -> Dict[str, Any]:
    return {"id": v.id, "name": v.name, "shortName": v.short_name, "label": service.vendor_label(v), "taxCode": v.tax_code,
            "aliases": [a for a in (v.aliases or "").splitlines() if a.strip()], "address": v.address,
            "contactName": v.contact_name, "contactEmail": v.contact_email, "contactPhone": v.contact_phone,
            "website": v.website, "tags": [t.strip() for t in (v.tags or "").split(",") if t.strip()], "notes": v.notes,
            "status": v.status or "active", "statusLabel": VENDOR_STATUS.get(v.status or "active"), "statusReason": v.status_reason,
            "active": v.active}


@router.get("/vendor-profiles")
def list_vendor_profiles(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    vendors = db.query(SpendVendor).order_by(SpendVendor.name).all()
    opex = dict(db.query(OpexItem.vendor_id, func.sum(OpexItem.amount_gross)).group_by(OpexItem.vendor_id).all())
    capex = dict(db.query(CapexItem.vendor_id, func.sum(CapexItem.amount_gross)).group_by(CapexItem.vendor_id).all())
    contracts = dict(db.query(Contract.vendor_id, func.count(Contract.id)).group_by(Contract.vendor_id).all())
    quotes = dict(db.query(Quotation.vendor_id, func.count(Quotation.id)).group_by(Quotation.vendor_id).all())
    won = dict(db.query(Quotation.vendor_id, func.count(Quotation.id))
               .join(QuoteRequest, QuoteRequest.selected_quotation_id == Quotation.id).group_by(Quotation.vendor_id).all())
    return [{**_vendor_out(v), "opexTotal": int(opex.get(v.id) or 0), "capexTotal": int(capex.get(v.id) or 0),
             "contracts": contracts.get(v.id, 0), "quotes": quotes.get(v.id, 0), "won": won.get(v.id, 0)} for v in vendors]


@router.get("/vendor-profiles/{vendor_id}")
def vendor_profile(vendor_id: int, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    v = db.get(SpendVendor, vendor_id)
    if not v:
        raise HTTPException(404, "Không tìm thấy nhà cung cấp.")
    opex = db.query(OpexItem).filter_by(vendor_id=v.id).order_by(OpexItem.end_date.desc()).limit(100).all()
    items = (db.query(CapexItem, CapexProject).join(CapexProject, CapexProject.id == CapexItem.project_id)
             .filter(CapexItem.vendor_id == v.id).order_by(CapexProject.fiscal_year.desc()).limit(100).all())
    cts = db.query(Contract).filter_by(vendor_id=v.id).order_by(Contract.id.desc()).limit(100).all()
    qs = (db.query(Quotation, QuoteRequest).join(QuoteRequest, QuoteRequest.id == Quotation.request_id)
          .filter(Quotation.vendor_id == v.id).order_by(QuoteRequest.id.desc()).limit(100).all())
    totals = _eval_totals(db, [r.id for _, r in qs])
    return {
        **_vendor_out(v),
        "opex": [{"code": o.code, "title": o.title, "fiscalYear": o.fiscal_year, "status": o.status, "amountGross": o.amount_gross,
                  "endDate": _iso(o.end_date)} for o in opex],
        "capexItems": [{"projectCode": p.code, "projectName": p.name, "fiscalYear": p.fiscal_year, "description": i.description,
                        "amountGross": i.amount_gross} for i, p in items],
        "contracts": [{"code": c.code, "title": c.title, "status": c.status} for c in cts],
        "quotes": [{"code": r.code, "title": r.title, "status": r.status, "won": r.selected_quotation_id == q.id,
                    "total": _quote_total_vnd(q), "evalTotal": _num(totals.get((r.id, q.id))),
                    "evalClass": qm.classify(totals.get((r.id, q.id)))} for q, r in qs],
    }


@router.put("/vendor-profiles/{vendor_id}")
def update_vendor_profile(vendor_id: int, request: Request, data: Dict[str, Any] = Body(...),
                          user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    v = db.get(SpendVendor, vendor_id)
    if not v:
        raise HTTPException(404, "Không tìm thấy nhà cung cấp.")
    t = service._text
    name = t(data.get("name"), "tên nhà cung cấp", 255, required=True)
    short = t(data.get("shortName"), "tên viết tắt", 64)
    if short and any(c in short for c in "\n\r\t"):
        _bad("Tên viết tắt không hợp lệ.")
    if short:
        clash = (db.query(SpendVendor).filter(func.lower(SpendVendor.short_name) == short.lower(), SpendVendor.id != v.id).first())
        if clash:
            raise HTTPException(409, f"Tên viết tắt “{short}” đang dùng cho {clash.name}.")
    status = data.get("status") or "active"
    if status not in VENDOR_STATUS:
        _bad("Trạng thái nhà cung cấp không hợp lệ.")
    if status != "active" and not str(data.get("statusReason") or "").strip():
        _bad("Ghi lý do khi tạm ngừng / không hợp tác.")
    if status != (v.status or "active") and not has_role(user, "approver"):
        raise HTTPException(403, "Đổi trạng thái nhà cung cấp cần quyền Duyệt.")
    raw_aliases = data.get("aliases") or []
    if not isinstance(raw_aliases, list):
        _bad("Tên gọi khác không hợp lệ.")
    aliases = [str(a).strip()[:255] for a in raw_aliases if str(a).strip()][:20]
    _no_name_clash(db, v, [name] + ([short] if short else []) + aliases)
    email = t(data.get("contactEmail"), "email liên hệ", 255)
    if email and not service.EMAIL_RE.match(email):
        _bad("Email liên hệ không hợp lệ.")
    before = _vendor_out(v)
    v.name, v.name_norm, v.short_name = name, service.norm_name(name), short
    v.aliases = "\n".join(aliases) or None
    v.tax_code = t(data.get("taxCode"), "mã số thuế", 32)
    v.address = t(data.get("address"), "địa chỉ", 500)
    v.contact_name = t(data.get("contactName"), "người liên hệ", 128)
    v.contact_email, v.contact_phone = email, t(data.get("contactPhone"), "điện thoại", 64)
    v.website = t(data.get("website"), "website", 255)
    v.tags = ", ".join(x.strip()[:60] for x in (data.get("tags") or []) if str(x).strip())[:500] or None
    v.notes = t(data.get("notes"), "ghi chú", 5000)
    v.status, v.status_reason = status, t(data.get("statusReason"), "lý do", 500) if status != "active" else None
    v.active = status == "active"
    service.audit(db, user, request, "spend.vendor.profile", id=v.id, before={k: before[k] for k in ("name", "shortName", "status")},
                  after={"name": name, "shortName": short, "status": status})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Tên hoặc tên viết tắt trùng với nhà cung cấp khác.") from exc
    return _vendor_out(v)


def _no_name_clash(db: Session, v: SpendVendor, names: List[str]) -> None:
    """Name, short name and aliases must not point at another vendor (uploads resolve vendors by them)."""
    mine = {service.norm_name(n) for n in names if n}
    for o in db.query(SpendVendor).filter(SpendVendor.id != v.id):
        theirs = {o.name_norm} | ({service.norm_name(o.short_name)} if o.short_name else set()) | \
                 {service.norm_name(a) for a in (o.aliases or "").splitlines() if a.strip()}
        hit = mine & theirs
        if hit:
            raise HTTPException(409, f"Tên “{next(iter(hit))}” đã dùng cho nhà cung cấp {o.name}.")


# ================================================================ helpers
def _request(db: Session, code: str) -> QuoteRequest:
    r = db.query(QuoteRequest).filter(QuoteRequest.code == code).first()
    if not r:
        raise HTTPException(404, f"Không tìm thấy gói báo giá {code}.")
    return r


def _quotation(db: Session, r: QuoteRequest, qid: int) -> Quotation:
    q = db.query(Quotation).filter(Quotation.id == qid, Quotation.request_id == r.id).first()
    if not q:
        raise HTTPException(404, "Không tìm thấy báo giá trong gói này.")
    return q


def _usable_vendor(v: Optional[SpendVendor]) -> None:
    if v is not None and (v.status or "active") == "blocked":
        raise HTTPException(409, f"{service.vendor_label(v)} đang ở trạng thái Không hợp tác — đổi trạng thái ở trang Nhà cung cấp nếu cần.")


def _editable(r: QuoteRequest) -> None:
    if r.status in ("selected", "closed"):
        raise HTTPException(409, "Gói đã chọn nhà cung cấp — mở lại (quyền Duyệt) để sửa.")


def _fx(q: Quotation) -> Decimal:
    return Decimal(1) if (q.currency or "VND") == "VND" else Decimal(q.fx_rate or 0)


def _quote_total_vnd(q: Quotation) -> Optional[int]:
    if q.stated_total is None:
        return None
    return qm.to_int(Decimal(q.stated_total) * _fx(q)) if q.currency != "VND" else q.stated_total


def _lines(db: Session, q: Quotation) -> List[QuotationLine]:
    return db.query(QuotationLine).filter_by(quotation_id=q.id).order_by(QuotationLine.seq).all()


def _as_qm_lines(lines: List[QuotationLine]) -> List[qm.Line]:
    return [qm.Line(l.line_type, l.description, l.model, l.unit, l.qty, l.unit_price, l.amount) for l in lines]


def run_checks(db: Session, r: QuoteRequest, q: Quotation) -> Dict[str, Any]:
    """Recompute findings of one quotation (stored, replaced each time)."""
    lines = _lines(db, q)
    findings, comp = qm.check(_as_qm_lines(lines), _dec(q.stated_subtotal), _dec(q.stated_discount), _dec(q.stated_vat),
                              _dec(q.stated_total), _dec(q.vat_pct), q.currency or "VND", _dec(q.fx_rate))
    db.query(QuotationFinding).filter_by(quotation_id=q.id).delete(synchronize_session=False)
    for f in findings:
        db.add(QuotationFinding(quotation_id=q.id, line_id=lines[f.line_index].id if f.line_index is not None else None,
                                code=f.code, severity=f.severity, expected=f.expected, actual=f.actual, message=f.message[:500]))
    if q.vat_pct is None and comp.get("vatPct") is not None:
        q.vat_pct = comp["vatPct"]
    return comp


def _dec(v) -> Optional[Decimal]:
    return None if v is None else Decimal(v)


def _eval_totals(db: Session, request_ids: List[int]) -> Dict[tuple, Optional[Decimal]]:
    if not request_ids:
        return {}
    crit = db.query(EvaluationCriterion).filter(EvaluationCriterion.request_id.in_(request_ids)).all()
    by_req: Dict[int, List[EvaluationCriterion]] = {}
    for c in crit:
        by_req.setdefault(c.request_id, []).append(c)
    scores = {(s.criterion_id, s.quotation_id): Decimal(s.score)
              for s in db.query(EvaluationScore).filter(EvaluationScore.criterion_id.in_([c.id for c in crit] or [0]))}
    out = {}
    qids = db.query(Quotation.id, Quotation.request_id).filter(Quotation.request_id.in_(request_ids)).all()
    for qid, rid in qids:
        cs = by_req.get(rid, [])
        out[(rid, qid)] = qm.weighted_total((Decimal(c.weight), scores.get((c.id, qid))) for c in cs) if cs else None
    return out


def _quotation_out(db: Session, q: Quotation, vendors: Dict[int, SpendVendor], with_lines: bool = False) -> Dict[str, Any]:
    v = vendors.get(q.vendor_id)
    fs = db.query(QuotationFinding).filter_by(quotation_id=q.id).order_by(QuotationFinding.id).all()
    lines = _lines(db, q)
    items = [l for l in lines if l.line_type == "item"]
    computed = sum((Decimal(l.amount) if l.amount is not None else (Decimal(l.qty) * Decimal(l.unit_price) if l.qty is not None and l.unit_price is not None else Decimal(0))) for l in items)
    f = db.get(SpendFile, q.file_id) if q.file_id else None
    out = {
        "id": q.id, "vendorId": q.vendor_id, "vendorLabel": service.vendor_label(v), "vendorName": v.name if v else None,
        "vendorHasShortName": bool(v and v.short_name), "quoteNo": q.quote_no, "quoteDate": _iso(q.quote_date),
        "validUntil": _iso(q.valid_until), "currency": q.currency, "fxRate": _num(q.fx_rate), "vatPct": _num(q.vat_pct),
        "statedSubtotal": q.stated_subtotal, "statedDiscount": q.stated_discount, "statedVat": q.stated_vat, "statedTotal": q.stated_total,
        "computedSubtotal": qm.to_int(computed) if items else None,
        "subtotalVnd": qm.to_int(computed * _fx(q)) if items else None, "totalVnd": _quote_total_vnd(q),
        "deliveryTerms": q.delivery_terms, "warrantyTerms": q.warranty_terms, "paymentTerms": q.payment_terms, "notes": q.notes,
        "state": q.state, "stateLabel": STATE_LABELS[q.state], "method": q.extraction_method, "message": q.extraction_message,
        "verifiedBy": q.verified_by, "verifiedAt": to_iso_utc(q.verified_at), "rowVersion": q.row_version,
        "file": service.file_out(f) if f else None, "itemCount": len(items),
        "findings": [{"id": x.id, "lineId": x.line_id, "code": x.code, "severity": x.severity, "message": x.message,
                      "expected": _num(x.expected), "actual": _num(x.actual)} for x in fs],
    }
    if with_lines:
        out["lines"] = [{"id": l.id, "seq": l.seq, "lineType": l.line_type, "description": l.description, "model": l.model,
                         "unit": l.unit, "qty": _num(l.qty), "unitPrice": _num(l.unit_price), "amount": _num(l.amount),
                         "matchKey": l.match_key} for l in lines]
    return out


def _evaluation_out(db: Session, r: QuoteRequest, quotations: List[Quotation], vendors: Dict[int, SpendVendor]) -> Dict[str, Any]:
    crit = db.query(EvaluationCriterion).filter_by(request_id=r.id).order_by(EvaluationCriterion.section, EvaluationCriterion.seq).all()
    scores = {(s.criterion_id, s.quotation_id): Decimal(s.score)
              for s in db.query(EvaluationScore).filter(EvaluationScore.criterion_id.in_([c.id for c in crit] or [0]))}
    totals = {q.id: qm.weighted_total((Decimal(c.weight), scores.get((c.id, q.id))) for c in crit) for q in quotations}
    price_hint = qm.suggest_price_score({q.id: _dec(_quote_total_vnd(q)) for q in quotations})
    return {
        "department": r.eval_department, "date": _iso(r.eval_date), "service": r.eval_service or r.title,
        "conclusion": r.eval_conclusion, "evaluator": r.eval_evaluator, "approver": r.eval_approver,
        "criteria": [{"id": c.id, "section": c.section, "seq": c.seq, "name": c.name, "weight": float(c.weight),
                      "scores": {str(q.id): _num(scores.get((c.id, q.id))) for q in quotations}} for c in crit],
        "totals": {str(qid): _num(t) for qid, t in totals.items()},
        "classes": {str(qid): qm.classify(t) for qid, t in totals.items()},
        "priceScoreHint": {str(k): float(v) for k, v in price_hint.items()},
        "columns": [{"quotationId": q.id, "label": service.vendor_label(vendors.get(q.vendor_id))} for q in quotations],
    }


def _detail(db: Session, r: QuoteRequest) -> Dict[str, Any]:
    quotations = db.query(Quotation).filter_by(request_id=r.id).order_by(Quotation.id).all()
    vendors = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_([q.vendor_id for q in quotations] or [0]))}
    cpx = db.get(CapexProject, r.capex_project_id) if r.capex_project_id else None
    rp = db.get(RenovationProject, r.renovation_project_id) if r.renovation_project_id else None
    sel = next((q for q in quotations if q.id == r.selected_quotation_id), None)
    warnings = []
    if r.estimate_amount is not None and sel is not None:
        sub = _quotation_out(db, sel, vendors)["subtotalVnd"]
        if sub is not None and r.estimate_amount and abs(sub - r.estimate_amount) > r.estimate_amount / 100:
            warnings.append(f"Dự toán {qm.vnd(Decimal(r.estimate_amount))} khác giá trị báo giá được chọn trước VAT "
                            f"{qm.vnd(Decimal(sub))} (lệch {qm.vnd(Decimal(sub - r.estimate_amount))}).")
    no_short = sorted({service.vendor_label(vendors.get(q.vendor_id)) for q in quotations
                       if vendors.get(q.vendor_id) and not vendors[q.vendor_id].short_name})
    if no_short:
        warnings.append("Chưa có tên viết tắt: " + ", ".join(no_short) + " — nhập ở trang Nhà cung cấp để dùng thống nhất trong tài liệu.")
    return {
        "id": r.id, "code": r.code, "title": r.title, "workstream": r.workstream, "workstreamLabel": qm.WORKSTREAM_LABELS[r.workstream],
        "description": r.description, "capex": {"code": cpx.code, "name": cpx.name} if cpx else None,
        "renovation": {"code": rp.code, "name": rp.name, "floor": rp.floor} if rp else None,
        "estimateAmount": r.estimate_amount, "minQuotes": r.min_quotes, "status": r.status, "statusLabel": STATUS_LABELS[r.status],
        "selectedQuotationId": r.selected_quotation_id, "selectionReason": r.selection_reason,
        "selectedBy": r.selected_by, "selectedAt": to_iso_utc(r.selected_at),
        "quotations": [_quotation_out(db, q, vendors) for q in quotations],
        "evaluation": _evaluation_out(db, r, quotations, vendors), "warnings": warnings,
        "aiEnabled": ai.enabled(db.get(SpendAiConfig, 1)),
        "aiSummary": _ai_summary_out(r),
        "contracts": [{"code": c.code, "title": c.title} for c in db.query(Contract).filter_by(capex_project_id=r.capex_project_id)]
        if r.capex_project_id else [],
        "createdBy": r.created_by, "createdAt": to_iso_utc(r.created_at), "updatedBy": r.updated_by, "updatedAt": to_iso_utc(r.updated_at),
    }


def _ai_summary_out(r: QuoteRequest) -> Optional[Dict[str, Any]]:
    if not r.ai_summary:
        return None
    try:
        data = json.loads(r.ai_summary)
    except ValueError:
        return None
    return {**data, "at": to_iso_utc(r.ai_summary_at), "by": r.ai_summary_by}


def _seed_criteria(db: Session, r: QuoteRequest) -> None:
    for i, (name, w) in enumerate(qm.STANDARD_CRITERIA, 1):
        db.add(EvaluationCriterion(request_id=r.id, section="A", seq=i, name=name, weight=w))
    ext = qm.EXTENDED_DEFAULTS[r.workstream]
    share = Decimal("0.30") / len(ext)
    for i, name in enumerate(ext, 1):
        db.add(EvaluationCriterion(request_id=r.id, section="B", seq=i, name=name, weight=share.quantize(Decimal("0.0001"))))


# ================================================================ requests
@router.get("/quotes")
def list_requests(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    rows = db.query(QuoteRequest).order_by(QuoteRequest.id.desc()).limit(500).all()
    counts = dict(db.query(Quotation.request_id, func.count(Quotation.id)).group_by(Quotation.request_id).all())
    sel_ids = [r.selected_quotation_id for r in rows if r.selected_quotation_id]
    sel = {q.id: q for q in db.query(Quotation).filter(Quotation.id.in_(sel_ids))} if sel_ids else {}
    vendors = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_([q.vendor_id for q in sel.values()] or [0]))}
    return [{"code": r.code, "title": r.title, "workstream": r.workstream, "workstreamLabel": qm.WORKSTREAM_LABELS[r.workstream],
             "status": r.status, "statusLabel": STATUS_LABELS[r.status], "quotations": counts.get(r.id, 0), "minQuotes": r.min_quotes,
             "selectedVendor": service.vendor_label(vendors.get(sel[r.selected_quotation_id].vendor_id)) if r.selected_quotation_id in sel else None,
             "selectedTotal": _quote_total_vnd(sel[r.selected_quotation_id]) if r.selected_quotation_id in sel else None,
             "updatedAt": to_iso_utc(r.updated_at or r.created_at)} for r in rows]


def _apply_request(db: Session, r: QuoteRequest, data: Dict[str, Any]) -> None:
    r.title = service._text(data.get("title"), "tên gói", 255, required=True)
    ws = data.get("workstream") or r.workstream or "other"
    if ws not in WORKSTREAMS:
        _bad("Khối không hợp lệ.")
    r.workstream = ws
    r.description = service._text(data.get("description"), "mô tả", 5000)
    est = data.get("estimateAmount")
    r.estimate_amount = service._money(est, "Dự toán") if est not in (None, "") else None
    try:
        r.min_quotes = int(data.get("minQuotes") or r.min_quotes or 3)
    except (TypeError, ValueError):
        _bad("Số báo giá tối thiểu không hợp lệ.")
    if not 1 <= r.min_quotes <= 10:
        _bad("Số báo giá tối thiểu từ 1 đến 10.")
    code = str(data.get("capexCode") or "").strip()
    if code:
        p = db.query(CapexProject).filter_by(code=code).first()
        if not p:
            _bad(f"Không có dự án Capex {code}.")
        r.capex_project_id = p.id
    else:
        r.capex_project_id = None
    rcode = str(data.get("renovationCode") or "").strip()
    if rcode:
        rp = db.query(RenovationProject).filter_by(code=rcode).first()
        if not rp:
            _bad(f"Không có dự án cải tạo {rcode}.")
        if r.workstream not in ("cabling", "network", "vc"):
            _bad("Gói gắn dự án cải tạo phải thuộc khối Cabling, Network hoặc Video Conference.")
        r.renovation_project_id = rp.id
    else:
        r.renovation_project_id = None


@router.post("/quotes")
def create_request(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    cfg = spend_settings.load(db)
    r = QuoteRequest(status="draft", created_by=user.username, min_quotes=3)
    _apply_request(db, r, data)
    r.code = codes.next_code(db, "quote", fiscal.fiscal_year(fiscal.today_vn(), cfg.fiscal_start_month))
    r.eval_department = cfg.proposal_department or "ITD"
    r.eval_date, r.eval_service = fiscal.today_vn(), r.title
    db.add(r)
    db.flush()
    _seed_criteria(db, r)
    service.audit(db, user, request, "spend.quote.create", code=r.code, title=r.title)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Không tạo được mã gói (trùng do thao tác đồng thời) — thử lại.") from exc
    return _detail(db, r)


@router.get("/quotes/{code}")
def get_request(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return _detail(db, _request(db, code))


@router.put("/quotes/{code}")
def update_request(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    r = _request(db, code)
    _editable(r)
    _apply_request(db, r, data)
    r.updated_at, r.updated_by = dt.datetime.utcnow(), user.username
    service.audit(db, user, request, "spend.quote.update", code=r.code)
    db.commit()
    return _detail(db, r)


@router.delete("/quotes/{code}")
def delete_request(code: str, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    r = _request(db, code)
    _editable(r)
    qs = db.query(Quotation).filter_by(request_id=r.id).all()
    fl = [db.get(SpendFile, q.file_id) for q in qs if q.file_id]
    service.audit(db, user, request, "spend.quote.delete", code=r.code, title=r.title)
    crit_ids = [c.id for c in db.query(EvaluationCriterion.id).filter_by(request_id=r.id)]
    db.query(EvaluationScore).filter(EvaluationScore.criterion_id.in_(crit_ids or [0])).delete(synchronize_session=False)
    db.query(EvaluationCriterion).filter_by(request_id=r.id).delete(synchronize_session=False)
    for q in qs:
        db.query(QuotationFinding).filter_by(quotation_id=q.id).delete(synchronize_session=False)
        db.query(QuotationLine).filter_by(quotation_id=q.id).delete(synchronize_session=False)
        db.delete(q)
    db.flush()
    for f in fl:
        db.delete(f)
    db.delete(r)
    db.commit()
    for f in fl:
        files.remove(f)
    return {"ok": True}


# ================================================================ quotations
def _store_extracted(db: Session, q: Quotation, ex: qm.Extracted) -> None:
    db.query(QuotationLine).filter_by(quotation_id=q.id).delete(synchronize_session=False)
    for i, l in enumerate(ex.lines[:MAX_LINES], 1):
        db.add(QuotationLine(quotation_id=q.id, seq=i, line_type=l.line_type, description=(l.description or "—")[:2000],
                             model=l.model, unit=l.unit, qty=l.qty, unit_price=l.unit_price, amount=l.amount))
    q.vat_pct = ex.vat_pct
    q.stated_subtotal, q.stated_discount = qm.to_int(ex.subtotal), qm.to_int(ex.discount)
    q.stated_vat, q.stated_total = qm.to_int(ex.vat), qm.to_int(ex.total)
    has_items = any(l.line_type == "item" for l in ex.lines)
    q.state = "extracted" if has_items else "failed"
    q.extraction_message = (ex.message or ("" if has_items else "Không đọc được dòng hàng nào."))[:500] or None
    q.verified_at = q.verified_by = None


@router.post("/quotes/{code}/quotations")
async def add_quotation(code: str, request: Request, file: Optional[UploadFile] = File(default=None),
                        vendorId: Optional[int] = Form(default=None), vendorName: Optional[str] = Form(default=None),
                        user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    r = _request(db, code)
    _editable(r)
    vid = service.resolve_vendor(db, {"vendorId": vendorId, "vendorName": vendorName}, user, request)
    if db.query(Quotation.id).filter_by(request_id=r.id, vendor_id=vid).first():
        raise HTTPException(409, "Nhà cung cấp này đã có báo giá trong gói — sửa hoặc thay file của báo giá đó.")
    _usable_vendor(db.get(SpendVendor, vid))
    q = Quotation(request_id=r.id, vendor_id=vid, currency="VND", state="pending", created_by=user.username, row_version=1)
    tmp = final = None
    try:
        _pending = None
        if file is not None and file.filename:
            data = await files.read_upload(file)
            row, tmp, final = files.store(db, data, file.filename, user.username)
            q.file_id = row.id
            if row.mime == files.XLSX:
                q.extraction_method = "excel"
                _pending = await run_in_threadpool(qm.read_xlsx, data)
            elif row.mime == files.DOCX:
                q.extraction_method = "docx"
                _pending = await run_in_threadpool(qm.read_docx, data)
            # PDF: read with AI on request (may take a minute)
        else:
            q.extraction_method = "manual"
        db.add(q)
        db.flush()
        if _pending is not None:
            _store_extracted(db, q, _pending)
            db.flush()
            run_checks(db, r, q)
        if r.status == "draft":
            r.status = "comparing"
        service.audit(db, user, request, "spend.quote.add", code=r.code, quotation=q.id, vendor=vid, method=q.extraction_method)
        db.commit()
        if tmp is not None:
            files.finalize(tmp, final)
    except Exception:
        db.rollback()
        files.discard(tmp)
        raise
    return _detail(db, r)


@router.get("/quotes/{code}/quotations/{qid}")
def get_quotation(code: str, qid: int, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    r = _request(db, code)
    q = _quotation(db, r, qid)
    return _quotation_out(db, q, {q.vendor_id: db.get(SpendVendor, q.vendor_id)}, with_lines=True)


@router.post("/quotes/{code}/quotations/{qid}/extract")
def extract_quotation(code: str, qid: int, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    """Read the attached file again: Excel / Word by code, PDF (scan) with the AI."""
    r = _request(db, code)
    _editable(r)
    q = _quotation(db, r, qid)
    f = db.get(SpendFile, q.file_id) if q.file_id else None
    if f is None or f.purged_at:
        raise HTTPException(409, "Báo giá chưa có file — nhập dòng bằng tay.")
    data = files.path_of(f).read_bytes()
    if f.mime == files.XLSX:
        ex, q.extraction_method = qm.read_xlsx(data), "excel"
    elif f.mime == files.DOCX:
        ex, q.extraction_method = qm.read_docx(data), "docx"
    else:
        try:
            raw = ai.extract_quotation(db.get(SpendAiConfig, 1), data)
        except ai.AiError as exc:
            q.state, q.extraction_message = "failed", str(exc)[:500]
            if ai.enabled(db.get(SpendAiConfig, 1)):
                service.audit(db, user, request, "spend.quote.ai_extract", code=r.code, quotation=q.id, sha256=f.sha256, failed=True)
            db.commit()
            raise HTTPException(422, str(exc)) from exc
        ex, q.extraction_method = _from_ai(q, raw), "ai"
        service.audit(db, user, request, "spend.quote.ai_extract", code=r.code, quotation=q.id, sha256=f.sha256)
    _store_extracted(db, q, ex)
    db.flush()
    run_checks(db, r, q)
    q.row_version += 1
    db.commit()
    return _quotation_out(db, q, {q.vendor_id: db.get(SpendVendor, q.vendor_id)}, with_lines=True)


def _from_ai(q: Quotation, raw: Dict[str, Any]) -> qm.Extracted:
    ex = qm.Extracted(message="Đọc bằng AI — đối chiếu với file gốc trước khi bấm “Đã kiểm tra”.")
    for l in raw.get("lines", []):
        t = l.get("line_type") if l.get("line_type") in ("item", "discount", "subtotal", "vat", "total", "note") else "item"
        ex.lines.append(qm.Line(t, str(l.get("description") or "")[:2000], str(l.get("model") or "")[:255] or None,
                                str(l.get("unit") or "")[:32] or None, _d(l.get("qty")), _d(l.get("unit_price")), _d(l.get("amount"))))
    ex.vat_pct, ex.subtotal, ex.discount = _d(raw.get("vat_pct")), _d(raw.get("subtotal")), _d(raw.get("discount"))
    ex.vat, ex.total = _d(raw.get("vat")), _d(raw.get("total"))
    q.quote_no = str(raw.get("quote_no") or "")[:64] or q.quote_no
    for attr, key in (("quote_date", "quote_date"), ("valid_until", "valid_until")):
        try:
            setattr(q, attr, dt.date.fromisoformat(str(raw.get(key) or "")[:10]))
        except ValueError:
            pass
    cur = str(raw.get("currency") or "").strip().upper()[:3]
    if cur in ("USD", "EUR", "SGD", "JPY"):
        q.currency = cur
    for attr, key in (("delivery_terms", "delivery_terms"), ("warranty_terms", "warranty_terms"), ("payment_terms", "payment_terms")):
        val = str(raw.get(key) or "").strip()[:500]
        if val:
            setattr(q, attr, val)
    return ex


@router.put("/quotes/{code}/quotations/{qid}")
def update_quotation(code: str, qid: int, request: Request, data: Dict[str, Any] = Body(...),
                     user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    """Header + full list of lines as checked by a person. Any edit clears "Đã kiểm tra"."""
    r = _request(db, code)
    _editable(r)
    q = _quotation(db, r, qid)
    if int(data.get("rowVersion") or 0) != q.row_version:
        raise HTTPException(409, "Báo giá vừa được người khác sửa — tải lại.")
    t = service._text
    q.quote_no = t(data.get("quoteNo"), "số báo giá", 64)
    for attr, key, label in (("quote_date", "quoteDate", "Ngày báo giá"), ("valid_until", "validUntil", "Hiệu lực")):
        setattr(q, attr, service._date(data[key], label) if data.get(key) else None)
    cur = str(data.get("currency") or "VND").upper()
    if len(cur) != 3 or not cur.isalpha():
        _bad("Mã tiền tệ gồm 3 chữ cái (VND, USD…).")
    q.currency = cur
    q.fx_rate = _d(data.get("fxRate")) if cur != "VND" else None
    if q.fx_rate is not None and not Decimal(0) < q.fx_rate < Decimal(10_000_000):
        _bad("Tỷ giá phải lớn hơn 0.")
    q.vat_pct = _d(data.get("vatPct"))
    for attr, key in (("stated_subtotal", "statedSubtotal"), ("stated_discount", "statedDiscount"), ("stated_vat", "statedVat"),
                      ("stated_total", "statedTotal")):
        setattr(q, attr, qm.to_int(_d(data.get(key))))
    for attr, key, label in (("delivery_terms", "deliveryTerms", "giao hàng"), ("warranty_terms", "warrantyTerms", "bảo hành"),
                             ("payment_terms", "paymentTerms", "thanh toán")):
        setattr(q, attr, t(data.get(key), f"điều khoản {label}", 500))
    q.notes = t(data.get("notes"), "ghi chú", 5000)
    rows = data.get("lines")
    if rows is not None:
        if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
            _bad("Danh sách dòng không hợp lệ.")
        if len(rows) > MAX_LINES:
            _bad(f"Tối đa {MAX_LINES} dòng.")
        db.query(QuotationLine).filter_by(quotation_id=q.id).delete(synchronize_session=False)
        for i, l in enumerate(rows, 1):
            lt = l.get("lineType") or "item"
            if lt not in ("item", "discount", "subtotal", "vat", "total", "note"):
                _bad(f"Dòng {i}: loại dòng không hợp lệ.")
            db.add(QuotationLine(quotation_id=q.id, seq=i, line_type=lt,
                                 description=t(l.get("description"), f"mô tả dòng {i}", 2000, required=True),
                                 model=t(l.get("model"), "model", 255), unit=t(l.get("unit"), "đơn vị", 32),
                                 qty=_d(l.get("qty")), unit_price=_d(l.get("unitPrice")), amount=_d(l.get("amount")),
                                 match_key=t(l.get("matchKey"), "khoá ghép", 255)))
        if q.extraction_method is None:
            q.extraction_method = "manual"
    q.state = "extracted" if q.state in ("verified", "pending", "failed") else q.state
    q.verified_at = q.verified_by = None
    q.row_version += 1
    q.updated_at, q.updated_by = dt.datetime.utcnow(), user.username
    db.flush()
    run_checks(db, r, q)
    service.audit(db, user, request, "spend.quote.edit", code=r.code, quotation=q.id, total=q.stated_total)
    db.commit()
    return _quotation_out(db, q, {q.vendor_id: db.get(SpendVendor, q.vendor_id)}, with_lines=True)


@router.post("/quotes/{code}/quotations/{qid}/verify")
def verify_quotation(code: str, qid: int, request: Request, data: Dict[str, Any] = Body(default={}), user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    """A person confirms the lines match the vendor's document (the vendor's own arithmetic errors stay as findings)."""
    r = _request(db, code)
    _editable(r)
    q = _quotation(db, r, qid)
    if not db.query(QuotationLine.id).filter_by(quotation_id=q.id, line_type="item").first():
        raise HTTPException(409, "Báo giá chưa có dòng hàng nào.")
    if data.get("rowVersion") is not None and int(data["rowVersion"]) != q.row_version:
        raise HTTPException(409, "Báo giá vừa được sửa / đọc lại — tải lại và kiểm tra trước khi xác nhận.")
    q.state, q.verified_at, q.verified_by = "verified", dt.datetime.utcnow(), user.username
    q.row_version += 1
    service.audit(db, user, request, "spend.quote.verify", code=r.code, quotation=q.id)
    db.commit()
    return _detail(db, r)


@router.delete("/quotes/{code}/quotations/{qid}")
def delete_quotation(code: str, qid: int, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    r = _request(db, code)
    _editable(r)
    q = _quotation(db, r, qid)
    f = db.get(SpendFile, q.file_id) if q.file_id else None
    service.audit(db, user, request, "spend.quote.remove", code=r.code, quotation=q.id, vendor=q.vendor_id)
    db.query(EvaluationScore).filter_by(quotation_id=q.id).delete(synchronize_session=False)
    db.query(QuotationFinding).filter_by(quotation_id=q.id).delete(synchronize_session=False)
    db.query(QuotationLine).filter_by(quotation_id=q.id).delete(synchronize_session=False)
    db.delete(q)
    db.flush()
    if f is not None:
        db.delete(f)
    db.commit()
    if f is not None:
        files.remove(f)
    return _detail(db, r)


# ================================================================ comparison
def comparison(db: Session, r: QuoteRequest) -> Dict[str, Any]:
    quotations = db.query(Quotation).filter_by(request_id=r.id).order_by(Quotation.id).all()
    vendors = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_([q.vendor_id for q in quotations] or [0]))}
    per = []
    for q in quotations:
        fx = _fx(q)
        lines = [{"id": l.id, "description": l.description, "model": l.model, "unit": l.unit, "qty": _num(l.qty),
                  "unitPrice": _num(l.unit_price), "amount": _num(l.amount), "matchKey": l.match_key,
                  "amountVnd": (Decimal(l.amount) * fx) if l.amount is not None else None,
                  "unitPriceVnd": (Decimal(l.unit_price) * fx) if l.unit_price is not None else None}
                 for l in _lines(db, q) if l.line_type == "item"]
        per.append((q.id, lines))
    rows = qm.align(per)
    for row in rows:
        for c in row["cells"].values():
            c["amountVnd"] = qm.to_int(c["amountVnd"]) if c["amountVnd"] is not None else None
            c["unitPriceVnd"] = qm.to_int(c["unitPriceVnd"]) if c["unitPriceVnd"] is not None else None
        row["cells"] = {str(k): v for k, v in row["cells"].items()}
        row["minQuotation"] = str(row["minQuotation"]) if row["minQuotation"] is not None else None
    outs = {q.id: _quotation_out(db, q, vendors) for q in quotations}
    subs = {qid: o["subtotalVnd"] for qid, o in outs.items() if o["subtotalVnd"] is not None}
    lo = min(subs.values()) if subs else None
    ranked = sorted(subs, key=subs.get)
    common = [r_ for r_ in rows if len(r_["cells"]) == len(quotations)]
    common_tot = {str(q.id): sum((c["amountVnd"] or 0) for r_ in common for k, c in r_["cells"].items() if k == str(q.id)) for q in quotations}
    return {
        "columns": [{"quotationId": q.id, "label": service.vendor_label(vendors.get(q.vendor_id)), "state": q.state,
                     "subtotalVnd": outs[q.id]["subtotalVnd"], "totalVnd": outs[q.id]["totalVnd"], "vatPct": outs[q.id]["vatPct"],
                     "rank": ranked.index(q.id) + 1 if q.id in ranked else None,
                     "diffPct": round((subs[q.id] - lo) * 100 / lo, 1) if q.id in subs and lo else None,
                     "commonTotalVnd": common_tot[str(q.id)], "findings": len(outs[q.id]["findings"]),
                     "critFindings": sum(1 for f in outs[q.id]["findings"] if f["severity"] == "crit"),
                     "deliveryTerms": q.delivery_terms, "warrantyTerms": q.warranty_terms, "paymentTerms": q.payment_terms,
                     "validUntil": _iso(q.valid_until)} for q in quotations],
        "rows": rows, "commonRows": len(common),
    }


@router.get("/quotes/{code}/comparison")
def get_comparison(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return comparison(db, _request(db, code))


@router.post("/quotes/{code}/match")
def set_match(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
              db: Session = Depends(get_db)):
    """Put a line in a comparison row (key of that row), or back to automatic (key empty)."""
    r = _request(db, code)
    _editable(r)
    line = (db.query(QuotationLine).join(Quotation, Quotation.id == QuotationLine.quotation_id)
            .filter(QuotationLine.id == int(data.get("lineId") or 0), Quotation.request_id == r.id).first())
    if not line:
        raise HTTPException(404, "Không tìm thấy dòng trong gói này.")
    line.match_key = service._text(data.get("key"), "khoá ghép", 255)
    db.commit()
    return comparison(db, r)


# ================================================================ evaluation
@router.put("/quotes/{code}/evaluation")
def save_evaluation(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                    db: Session = Depends(get_db)):
    r = _request(db, code)
    _editable(r)
    t = service._text
    r.eval_department = t(data.get("department"), "bộ phận đánh giá", 64)
    r.eval_date = service._date(data["date"], "Ngày đánh giá") if data.get("date") else None
    r.eval_service = t(data.get("service"), "loại hình sản phẩm, dịch vụ", 255)
    r.eval_conclusion = t(data.get("conclusion"), "kết luận", 255)
    r.eval_evaluator = t(data.get("evaluator"), "người đánh giá", 128)
    r.eval_approver = t(data.get("approver"), "trưởng bộ phận", 128)
    qids = {q.id for q in db.query(Quotation).filter_by(request_id=r.id)}
    crit_in = data.get("criteria") or []
    if not isinstance(crit_in, list) or len(crit_in) > 14:
        _bad("Phiếu đánh giá tối đa 14 tiêu chí.")
    parsed = []
    for i, c in enumerate(crit_in):
        if not isinstance(c, dict) or not isinstance(c.get("scores") or {}, dict) or len(c.get("scores") or {}) > len(qids):
            _bad(f"Tiêu chí {i + 1}: dữ liệu không hợp lệ.")
        sec = c.get("section")
        if sec not in ("A", "B"):
            _bad(f"Tiêu chí {i + 1}: phần phải là A hoặc B.")
        w = _d(c.get("weight"))
        w = w.quantize(Decimal("0.0001")) if w is not None else None
        if w is None or not Decimal(0) < w <= 1:
            _bad(f"Tiêu chí {i + 1}: tỷ trọng từ 0 đến 1 (vd 0,15).")
        parsed.append((sec, t(c.get("name"), f"tên tiêu chí {i + 1}", 255, required=True), w, c.get("scores") or {}))
    err = qm.validate_criteria([(s, n, w) for s, n, w, _ in parsed])
    if err:
        _bad(err)
    old = [c.id for c in db.query(EvaluationCriterion.id).filter_by(request_id=r.id)]
    db.query(EvaluationScore).filter(EvaluationScore.criterion_id.in_(old or [0])).delete(synchronize_session=False)
    db.query(EvaluationCriterion).filter_by(request_id=r.id).delete(synchronize_session=False)
    seqs = {"A": 0, "B": 0}
    for sec, name, w, scores in parsed:
        seqs[sec] += 1
        c = EvaluationCriterion(request_id=r.id, section=sec, seq=seqs[sec], name=name, weight=w)
        db.add(c)
        db.flush()
        for k, v in scores.items():
            if v in (None, ""):
                continue
            try:
                qid = int(k)
            except ValueError:
                continue
            if qid not in qids:
                _bad("Điểm cho báo giá không thuộc gói này.")
            s = _d(v)
            if s is None or not Decimal(1) <= s <= Decimal(10):
                _bad(f"Điểm “{name}” phải từ 1 đến 10.")
            db.add(EvaluationScore(criterion_id=c.id, quotation_id=qid, score=s, scored_by=user.username))
    r.updated_at, r.updated_by = dt.datetime.utcnow(), user.username
    service.audit(db, user, request, "spend.quote.evaluation", code=r.code)
    db.commit()
    return _detail(db, r)


# ================================================================ selection
@router.post("/quotes/{code}/select")
def select_vendor(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("approver")),
                  db: Session = Depends(get_db)):
    r = _request(db, code)
    if r.status not in ("draft", "comparing"):
        raise HTTPException(409, "Gói đã chọn nhà cung cấp.")
    q = _quotation(db, r, int(data.get("quotationId") or 0))
    if q.state != "verified":
        raise HTTPException(409, "Chỉ chọn được báo giá đã kiểm tra.")
    verified = db.query(Quotation).filter_by(request_id=r.id, state="verified").count()
    reason = service._text(data.get("reason"), "lý do lựa chọn", 5000)
    if verified < r.min_quotes and not reason:
        raise HTTPException(409, f"Mới có {verified}/{r.min_quotes} báo giá đã kiểm tra — ghi lý do (vd độc quyền, chỉ 1 NCC) để chọn.")
    v = db.get(SpendVendor, q.vendor_id)
    _usable_vendor(v)
    r.status, r.selected_quotation_id, r.selection_reason = "selected", q.id, reason
    r.selected_at, r.selected_by = dt.datetime.utcnow(), user.username
    if not r.eval_conclusion:
        r.eval_conclusion = f"Đề xuất chọn {service.vendor_label(v)}"
    service.audit(db, user, request, "spend.quote.select", code=r.code, quotation=q.id, vendor=service.vendor_label(v),
                  total=_quote_total_vnd(q), verified=verified)
    db.commit()
    return _detail(db, r)


@router.post("/quotes/{code}/reopen")
def reopen_request(code: str, request: Request, user: User = Depends(require_spend("approver")), db: Session = Depends(get_db)):
    r = _request(db, code)
    if r.status != "selected":
        raise HTTPException(409, "Gói chưa chọn nhà cung cấp.")
    r.status, r.selected_quotation_id, r.selected_at, r.selected_by = "comparing", None, None, None
    service.audit(db, user, request, "spend.quote.reopen", code=r.code)
    db.commit()
    return _detail(db, r)


# ================================================================ exports
def _download(content: bytes, name: str) -> Response:
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={
        "Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{urlquote(name)}", "Cache-Control": "private, no-store"})


@router.get("/quotes/{code}/comparison.xlsx")
def export_comparison(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    r = _request(db, code)
    d = _detail(db, r)
    lines = {q["id"]: get_quotation(code, q["id"], user, db)["lines"] for q in d["quotations"]}
    return _download(excel_quotes.build_comparison_xlsx(d, comparison(db, r), lines), f"so-sanh-bao-gia-{r.code}.xlsx")


@router.get("/quotes/{code}/evaluation.xlsx")
def export_evaluation(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    r = _request(db, code)
    return _download(excel_quotes.build_evaluation_xlsx(_detail(db, r)), f"evaluation-form-{r.code}.xlsx")


# ================================================================ V3 — AI differences, unit price history, quick-create
AI_LINES_PER_QUOTE = 150


def _line_vnd(l: QuotationLine, q: Quotation) -> Dict[str, Optional[int]]:
    fx = _fx(q)
    amount = l.amount if l.amount is not None else (Decimal(l.qty) * Decimal(l.unit_price) if l.qty is not None and l.unit_price is not None else None)
    return {"unitPriceVnd": qm.to_int(Decimal(l.unit_price) * fx) if l.unit_price is not None else None,
            "amountVnd": qm.to_int(Decimal(amount) * fx) if amount is not None else None}


def ai_payload(db: Session, r: QuoteRequest) -> Dict[str, Any]:
    """What the AI sees: scope of the request, and per vendor (short name) its lines, totals and terms.
    No contacts, internal notes, files or users."""
    quotations = db.query(Quotation).filter_by(request_id=r.id).order_by(Quotation.id).all()
    vendors = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_([q.vendor_id for q in quotations] or [0]))}
    out = []
    for q in quotations:
        lines = [l for l in _lines(db, q) if l.line_type in ("item", "note")][:AI_LINES_PER_QUOTE]
        out.append({
            "ncc": service.vendor_label(vendors.get(q.vendor_id)), "da_kiem_tra": q.state == "verified",
            "tong_truoc_vat_vnd": _quotation_out(db, q, vendors)["subtotalVnd"], "tong_ncc_ghi_vnd": _quote_total_vnd(q),
            "vat_pct": _num(q.vat_pct), "hieu_luc_den": _iso(q.valid_until), "giao_hang": q.delivery_terms,
            "bao_hanh": q.warranty_terms, "thanh_toan": q.payment_terms,
            "dong": [{"loai": l.line_type, "mo_ta": l.description[:300], "model": l.model, "dvt": l.unit, "sl": _num(l.qty),
                      **({"don_gia_vnd": _line_vnd(l, q)["unitPriceVnd"], "thanh_tien_vnd": _line_vnd(l, q)["amountVnd"]}
                         if l.line_type == "item" else {})} for l in lines],
        })
    return {"goi": r.title, "khoi": qm.WORKSTREAM_LABELS[r.workstream], "pham_vi": (r.description or "")[:3000] or None,
            "bao_gia": out}


@router.post("/quotes/{code}/ai-summary")
def ai_summary(code: str, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    """AI: differences of content / configuration between the quotations (not prices: those are computed)."""
    r = _request(db, code)
    if db.query(Quotation).filter_by(request_id=r.id).count() < 2:
        raise HTTPException(409, "Cần ít nhất 2 báo giá để so sánh.")
    cfg = db.get(SpendAiConfig, 1)
    if not ai.enabled(cfg):
        raise HTTPException(409, "AI chưa bật hoặc chưa cho phép gửi dữ liệu ra ngoài (Cài đặt → Tờ trình & AI).")
    payload = ai_payload(db, r)
    service.audit(db, user, request, "spend.quote.ai_summary", code=r.code, vendors=[v["ncc"] for v in payload["bao_gia"]])
    db.commit()                                          # the outbound send is recorded even if the call fails
    try:
        res = ai.compare_quotes(cfg, payload)
    except ai.AiError as exc:
        raise HTTPException(422, str(exc)) from exc
    r.ai_summary = json.dumps(res, ensure_ascii=False)
    r.ai_summary_at, r.ai_summary_by = dt.datetime.utcnow(), user.username
    db.commit()
    return _detail(db, r)


@router.get("/price-history")
def price_history(q: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    """Unit prices of an item (model or words of the description) across verified quotations, vendors and years."""
    term = " ".join(re.sub(r"[%_\\]+", " ", q or "").split())[:80]     # wildcards are never user-controlled
    if len(term) < 3:
        _bad("Nhập ít nhất 3 ký tự (model hoặc tên hàng).")
    like = f"%{term}%"
    rows = (db.query(QuotationLine, Quotation, QuoteRequest)
            .join(Quotation, Quotation.id == QuotationLine.quotation_id)
            .join(QuoteRequest, QuoteRequest.id == Quotation.request_id)
            .filter(Quotation.state == "verified", QuotationLine.line_type == "item")
            .filter((QuotationLine.model.ilike(like)) | (QuotationLine.description.ilike(like)))
            .order_by(QuoteRequest.id.desc()).limit(300).all())
    vendors = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_({q_.vendor_id for _, q_, _ in rows} or {0}))}
    items, stats = [], {}
    for l, q_, r in rows:
        when = q_.quote_date or (q_.verified_at.date() if q_.verified_at else None)
        price = _line_vnd(l, q_)["unitPriceVnd"]
        items.append({"date": _iso(when), "year": when.year if when else None, "vendor": service.vendor_label(vendors.get(q_.vendor_id)),
                      "request": r.code, "requestTitle": r.title, "description": l.description, "model": l.model, "unit": l.unit,
                      "qty": _num(l.qty), "unitPriceVnd": price, "selected": r.selected_quotation_id == q_.id})
        if price is not None and when:
            stats.setdefault((l.model or "").strip().upper() or "—", {}).setdefault(when.year, []).append(price)
    import statistics
    return {"items": items, "truncated": len(rows) == 300,
            "byModel": [{"model": m, "years": [{"year": y, "count": len(v), "min": min(v), "median": int(statistics.median(v)), "max": max(v)}
                                               for y, v in sorted(ys.items())]} for m, ys in sorted(stats.items())]}


def _selected(db: Session, r: QuoteRequest) -> Quotation:
    if r.status != "selected" or not r.selected_quotation_id:
        raise HTTPException(409, "Gói chưa chọn nhà cung cấp.")
    q = db.get(Quotation, r.selected_quotation_id)
    if q is None:
        raise HTTPException(409, "Báo giá được chọn không còn.")
    return q


@router.post("/quotes/{code}/capex")
def create_capex_from_quote(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                            db: Session = Depends(get_db)):
    """Capex project from the selected quotation: one item per line, or one item for the whole quotation."""
    from . import spend_capex
    r = db.query(QuoteRequest).filter(QuoteRequest.code == code).with_for_update().first() or _request(db, code)
    q = _selected(db, r)
    if r.capex_project_id:
        raise HTTPException(409, "Gói đã gắn dự án Capex.")
    v = db.get(SpendVendor, q.vendor_id)
    vat = Decimal(q.vat_pct) if q.vat_pct is not None else Decimal(10)
    mode = data.get("mode") or "total"
    if mode not in ("total", "lines"):
        _bad("Cách tạo hạng mục không hợp lệ.")
    base = {"categoryId": data.get("categoryId"), "vendorId": v.id, "vatPct": str(vat),
            "coverageYears": data.get("coverageYears") or 0, "startDate": data.get("startDate")}
    if mode == "lines":
        lines = [l for l in _lines(db, q) if l.line_type == "item"]
        if len(lines) > spend_capex.MAX_ITEMS:
            _bad(f"Báo giá có {len(lines)} dòng (tối đa {spend_capex.MAX_ITEMS}) — chọn tạo 1 hạng mục tổng.")
        items = []
        for l in lines:
            amt = _line_vnd(l, q)["amountVnd"]
            if not amt or amt < 0:
                continue
            qty = int(l.qty) if l.qty is not None and Decimal(l.qty) == int(l.qty) and l.qty > 0 else 1
            items.append({**base, "description": (f"{l.description} {l.model or ''}").strip()[:255], "quantity": qty, "amountNet": amt})
        if not items:
            _bad("Báo giá không có dòng hàng có giá trị — chọn tạo 1 hạng mục tổng.")
        if any(l.line_type == "discount" for l in _lines(db, q)):
            _bad("Báo giá có dòng chiết khấu — chọn tạo 1 hạng mục tổng để khớp giá trị sau chiết khấu.")
    else:
        sub = _quotation_out(db, q, {v.id: v})["subtotalVnd"]
        if not sub:
            _bad("Báo giá chưa có giá trị trước VAT.")
        items = [{**base, "description": f"{r.title} — {service.vendor_label(v)}"[:255], "quantity": 1, "amountNet": sub}]
    body = {"name": service._text(data.get("name"), "tên dự án", 255) or r.title, "description": f"Tạo từ gói báo giá {r.code}.",
            "fiscalYear": data.get("fiscalYear") or int(r.code.split("-")[1]), "goLiveDate": data.get("goLiveDate"), "items": items}
    cfg = spend_settings.load(db)
    p = CapexProject(status="draft", created_by=user.username, row_version=1)
    spend_capex._apply_project(db, p, body, cfg)
    p.code = codes.next_code(db, "capex", p.fiscal_year)
    db.add(p)
    db.flush()
    spend_capex._apply_items(db, p, items, user, request)
    r.capex_project_id = p.id
    service.audit(db, user, request, "spend.quote.to_capex", code=r.code, capex=p.code, mode=mode, items=len(items))
    spend_capex._commit(db, "Không tạo được mã Capex (trùng do thao tác đồng thời) — thử lại.")
    return {**_detail(db, r), "capexCode": p.code}


@router.post("/quotes/{code}/contract")
def create_contract_from_quote(code: str, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    r = db.query(QuoteRequest).filter(QuoteRequest.code == code).with_for_update().first() or _request(db, code)
    q = _selected(db, r)
    if not r.capex_project_id:
        raise HTTPException(409, "Tạo (hoặc gắn) dự án Capex cho gói trước, hợp đồng gắn với dự án Capex.")
    cpx = db.get(CapexProject, r.capex_project_id)
    v = db.get(SpendVendor, q.vendor_id)
    existing = db.query(Contract).filter_by(capex_project_id=cpx.id, vendor_id=v.id).first()
    if existing:
        raise HTTPException(409, f"Đã có hợp đồng {existing.code} với {service.vendor_label(v)} cho dự án {cpx.code}.")
    c = Contract(title=f"Hợp đồng {r.title} — {service.vendor_label(v)}"[:255], status="draft", current_version_no=0,
                 created_by=user.username, capex_project_id=cpx.id, vendor_id=v.id, note=f"Tạo từ gói báo giá {r.code}.")
    try:
        c.code = codes.next_code(db, "contract", cpx.fiscal_year)
        db.add(c)
        service.audit(db, user, request, "spend.quote.to_contract", code=r.code, contract=c.code)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Không tạo được mã hợp đồng — thử lại.") from exc
    return {**_detail(db, r), "contractCode": c.code}
