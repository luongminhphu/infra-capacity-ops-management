"""Chi phí & Hợp đồng — tờ trình: templates (Mẫu A / B), recipients, AI provider, proposals."""
import datetime as dt
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import secrets_box
from ..database import get_db
from ..features import require_feature
from ..models import User
from ..spend import ai, capex, codes, contracts, files, fiscal, proposal_doc, service, settings as spend_settings
from ..spend.models import (
    QuoteRequest,
    PROPOSAL_KINDS, RECIPIENT_ROLES, CapexItem, CapexProject, Contract, OpexAsset, OpexItem, Proposal, ProposalContract,
    ProposalOpex, ProposalRecipient, ProposalTemplate, SpendAiConfig, SpendCategory, SpendFile, SpendVendor,
)
from ..spend.roles import require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend/proposals", tags=["spend-proposals"], dependencies=[Depends(require_feature("spend"))])
KIND_LABELS = {"A": "Mẫu A — trình Tổng Giám Đốc", "B": "Mẫu B — trình Ban Mua sắm / COO"}
STATUS_LABELS = {"draft": "Nháp", "submitted": "Đã trình", "approved": "Đã duyệt", "rejected": "Không duyệt"}
MAX_OPEX = 50


def _bad(msg: str):
    raise HTTPException(400, msg)


# ---------------------------------------------------------------- templates
def _template_out(t: ProposalTemplate, used: int = 0) -> Dict[str, Any]:
    outline = json.loads(t.outline_json)
    return {"id": t.id, "kind": t.kind, "kindLabel": KIND_LABELS[t.kind], "name": t.name, "description": t.description,
            "active": t.active, "fileId": t.file_id, "uploadedBy": t.uploaded_by, "uploadedAt": to_iso_utc(t.uploaded_at),
            "outline": outline, "usedBy": used}


@router.get("/templates")
def list_templates(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    used = {}
    for (tid,) in db.query(Proposal.template_id):
        used[tid] = used.get(tid, 0) + 1
    return [_template_out(t, used.get(t.id, 0)) for t in db.query(ProposalTemplate).order_by(ProposalTemplate.kind, ProposalTemplate.id)]


@router.post("/templates")
async def upload_template(request: Request, file: UploadFile = File(...), kind: str = Form(...), name: str = Form(...),
                          description: Optional[str] = Form(default=None), user: User = Depends(require_spend("admin")),
                          db: Session = Depends(get_db)):
    if kind not in PROPOSAL_KINDS:
        _bad("Loại mẫu phải là A hoặc B.")
    name = service._text(name, "tên mẫu", 128, required=True)
    data = await files.read_upload(file)
    try:
        contracts.docx_paragraphs(data)                 # UTF-8, no DOCTYPE / ENTITY in the main part
        outline = proposal_doc.outline(data)
    except (contracts.DocxTextError, proposal_doc.TemplateError) as exc:
        raise HTTPException(415, str(exc)) from exc
    row, tmp, final = files.store(db, data, file.filename, user.username, allowed=(files.DOCX,))
    try:
        t = ProposalTemplate(kind=kind, name=name, description=service._text(description, "mô tả", 500), file_id=row.id,
                             outline_json=json.dumps(outline, ensure_ascii=False), active=True, uploaded_by=user.username)
        db.add(t)
        db.flush()
        service.audit(db, user, request, "spend.proposal.template_upload", id=t.id, kind=kind, name=name, sha256=row.sha256,
                      sections=len(outline["sections"]))
        db.commit()
        files.finalize(tmp, final)
    except Exception:
        db.rollback()
        files.discard(tmp)
        raise
    return _template_out(t)


@router.put("/templates/{template_id}")
def update_template(template_id: int, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("admin")),
                    db: Session = Depends(get_db)):
    t = db.get(ProposalTemplate, template_id)
    if not t:
        raise HTTPException(404, "Không tìm thấy mẫu.")
    t.name = service._text(data.get("name", t.name), "tên mẫu", 128, required=True)
    t.description = service._text(data.get("description", t.description), "mô tả", 500)
    t.active = bool(data.get("active", t.active))
    service.audit(db, user, request, "spend.proposal.template_update", id=t.id, name=t.name, active=t.active)
    db.commit()
    return _template_out(t)


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    t = db.get(ProposalTemplate, template_id)
    if not t:
        raise HTTPException(404, "Không tìm thấy mẫu.")
    if db.query(Proposal.id).filter(Proposal.template_id == t.id).first():
        raise HTTPException(409, "Mẫu đã được dùng cho tờ trình — chỉ tắt được (bỏ chọn “Dùng”), không xoá.")
    f = db.get(SpendFile, t.file_id)
    service.audit(db, user, request, "spend.proposal.template_delete", id=t.id, name=t.name)
    db.delete(t)
    db.flush()
    db.delete(f)
    db.commit()
    files.remove(f)
    return {"ok": True}


# ---------------------------------------------------------------- recipients
@router.get("/recipients")
def list_recipients(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    rows = db.query(ProposalRecipient).order_by(ProposalRecipient.kind, ProposalRecipient.sort_order, ProposalRecipient.id).all()
    return {"roles": [{"value": k, "label": v} for k, v in proposal_doc.ROLE_LABELS.items()],
            "items": [{"id": r.id, "kind": r.kind, "role": r.role, "roleLabel": proposal_doc.ROLE_LABELS[r.role],
                       "salutation": r.salutation, "name": r.name, "title": r.title} for r in rows]}


@router.put("/recipients/{kind}")
def replace_recipients(kind: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("admin")),
                       db: Session = Depends(get_db)):
    """Replace the whole list for one template kind (order = order in the signature table)."""
    if kind not in PROPOSAL_KINDS:
        _bad("Loại mẫu phải là A hoặc B.")
    items = data.get("items") or []
    if len(items) > 20:
        _bad("Tối đa 20 người cho một mẫu.")
    rows = []
    for i, it in enumerate(items):
        if it.get("role") not in RECIPIENT_ROLES:
            _bad(f"Dòng {i + 1}: vai trò không hợp lệ.")
        rows.append(ProposalRecipient(kind=kind, role=it["role"], sort_order=i,
                                      salutation=service._text(it.get("salutation"), "xưng hô", 16),
                                      name=service._text(it.get("name"), f"họ tên dòng {i + 1}", 128, required=True),
                                      title=service._text(it.get("title"), f"chức vụ dòng {i + 1}", 255, required=True)))
    db.query(ProposalRecipient).filter(ProposalRecipient.kind == kind).delete(synchronize_session=False)
    db.add_all(rows)
    service.audit(db, user, request, "spend.proposal.recipients", kind=kind, count=len(rows))
    db.commit()
    return list_recipients(user, db)


# ---------------------------------------------------------------- AI config
def _ai_out(c: Optional[SpendAiConfig]) -> Dict[str, Any]:
    if not c:
        return {"provider": "none", "model": ai.DEFAULT_MODEL, "effort": "medium", "hasApiKey": False, "allowExternal": False,
                "enabled": False, "secretsKeyConfigured": secrets_box.dedicated_key()}
    return {"provider": c.provider, "model": c.model, "effort": c.effort, "hasApiKey": bool(c.api_key_enc),
            "allowExternal": c.allow_external, "enabled": ai.enabled(c), "updatedBy": c.updated_by,
            "updatedAt": to_iso_utc(c.updated_at), "secretsKeyConfigured": secrets_box.dedicated_key()}


@router.get("/ai")
def get_ai(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    """Viewers see whether AI is on (to show the button); the key itself is never returned."""
    return _ai_out(db.get(SpendAiConfig, 1))


@router.put("/ai")
def put_ai(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    c = db.get(SpendAiConfig, 1) or SpendAiConfig(id=1, updated_by=user.username)
    provider = data.get("provider", c.provider or "none")
    if provider not in ("none", "anthropic"):
        _bad("Nhà cung cấp AI không hợp lệ.")
    model = str(data.get("model") or c.model or ai.DEFAULT_MODEL).strip()
    if not model.replace("-", "").replace(".", "").isalnum() or len(model) > 64:
        _bad("Tên model không hợp lệ.")
    effort = data.get("effort", c.effort or "medium")
    if effort not in ("low", "medium", "high"):
        _bad("Mức effort không hợp lệ.")
    c.provider, c.model, c.effort = provider, model, effort
    c.allow_external = bool(data.get("allowExternal", c.allow_external))
    changed = False
    if data.get("apiKey"):
        c.api_key_enc, changed = secrets_box.encrypt(str(data["apiKey"]).strip()), True
    elif data.get("clearApiKey"):
        c.api_key_enc, changed = None, True
    c.updated_at, c.updated_by = dt.datetime.utcnow(), user.username
    db.merge(c)
    service.audit(db, user, request, "spend.ai.update", provider=provider, model=model, allowExternal=c.allow_external, keyChanged=changed)
    db.commit()
    return _ai_out(db.get(SpendAiConfig, 1))


@router.post("/ai/test")
def test_ai(request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    try:
        return {"ok": True, "message": ai.test_connection(db.get(SpendAiConfig, 1))}
    except ai.AiError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — the test must answer, not 500
        return {"ok": False, "message": f"Lỗi: {type(exc).__name__}"}


# ---------------------------------------------------------------- proposals
def _proposal(db: Session, code: str) -> Proposal:
    p = db.query(Proposal).filter(Proposal.code == code).first()
    if not p:
        raise HTTPException(404, f"Không tìm thấy tờ trình {code}.")
    return p


def _detail(db: Session, p: Proposal) -> Dict[str, Any]:
    t = db.get(ProposalTemplate, p.template_id)
    opex = (db.query(OpexItem).join(ProposalOpex, ProposalOpex.opex_id == OpexItem.id)
            .filter(ProposalOpex.proposal_id == p.id).order_by(OpexItem.code).all())
    cpx = db.get(CapexProject, p.capex_project_id) if p.capex_project_id else None
    qr = db.get(QuoteRequest, p.quote_request_id) if p.quote_request_id else None
    f = db.get(SpendFile, p.file_id) if p.file_id else None
    linked = (db.query(Contract).join(ProposalContract, ProposalContract.contract_id == Contract.id)
              .filter(ProposalContract.proposal_id == p.id).order_by(Contract.code).all())
    return {
        "id": p.id, "code": p.code, "kind": p.kind, "kindLabel": KIND_LABELS[p.kind], "subject": p.subject,
        "department": p.department, "contextNote": p.context_note, "status": p.status, "statusLabel": STATUS_LABELS[p.status],
        "template": {"id": t.id, "name": t.name, "active": t.active} if t else None,
        "opex": [{"code": o.code, "title": o.title, "amountGross": o.amount_gross, "status": o.status} for o in opex],
        "capex": {"code": cpx.code, "name": cpx.name} if cpx else None,
        "quote": {"code": qr.code, "title": qr.title} if qr else None,
        "file": service.file_out(f) if f else None, "aiProvider": p.ai_provider_used, "aiModel": p.ai_model,
        "generatedAt": to_iso_utc(p.generated_at), "generatedBy": p.generated_by,
        "contracts": [{"code": c.code, "title": c.title, "status": c.status} for c in linked],
        "createdBy": p.created_by, "createdAt": to_iso_utc(p.created_at), "updatedBy": p.updated_by,
        "updatedAt": to_iso_utc(p.updated_at),
    }


@router.get("")
def list_proposals(opex: Optional[str] = None, capex: Optional[str] = None, user: User = Depends(require_spend("viewer")),
                   db: Session = Depends(get_db)):
    q = db.query(Proposal)
    if opex:
        q = q.filter(Proposal.id.in_(db.query(ProposalOpex.proposal_id).join(OpexItem, OpexItem.id == ProposalOpex.opex_id)
                                      .filter(OpexItem.code == opex)))
    if capex:
        q = q.filter(Proposal.capex_project_id.in_(db.query(CapexProject.id).filter(CapexProject.code == capex)))
    rows = q.order_by(Proposal.id.desc()).limit(500).all()
    cpx = dict(db.query(CapexProject.id, CapexProject.code).filter(CapexProject.id.in_({p.capex_project_id for p in rows if p.capex_project_id})))
    qrs = dict(db.query(QuoteRequest.id, QuoteRequest.code).filter(QuoteRequest.id.in_({p.quote_request_id for p in rows if p.quote_request_id})))
    counts: Dict[int, int] = {}
    for (pid,) in db.query(ProposalOpex.proposal_id).filter(ProposalOpex.proposal_id.in_([p.id for p in rows])):
        counts[pid] = counts.get(pid, 0) + 1
    return [{"code": p.code, "kind": p.kind, "subject": p.subject, "status": p.status, "statusLabel": STATUS_LABELS[p.status],
             "capexCode": cpx.get(p.capex_project_id), "quoteCode": qrs.get(p.quote_request_id), "opexCount": counts.get(p.id, 0), "hasFile": p.file_id is not None,
             "aiProvider": p.ai_provider_used, "updatedAt": to_iso_utc(p.updated_at or p.created_at),
             "updatedBy": p.updated_by or p.created_by} for p in rows]


def _quote(db: Session, data: Dict[str, Any]) -> Optional[QuoteRequest]:
    code = str(data.get("quoteCode") or "").strip()
    if not code:
        return None
    r = db.query(QuoteRequest).filter_by(code=code).first()
    if not r:
        _bad(f"Không có gói báo giá {code}.")
    return r


def _links(db: Session, data: Dict[str, Any], quote: Optional[QuoteRequest] = None):
    opex_codes = [str(c).strip() for c in (data.get("opexCodes") or []) if str(c).strip()]
    capex_code = str(data.get("capexCode") or "").strip()
    if quote is not None and not opex_codes and not capex_code:
        return [], (db.get(CapexProject, quote.capex_project_id) if quote.capex_project_id else None)
    if bool(opex_codes) == bool(capex_code):
        _bad("Tờ trình gắn với một dự án Capex, một / nhiều khoản Opex (không cả hai) hoặc một gói so sánh báo giá.")
    if len(opex_codes) > MAX_OPEX:
        _bad(f"Tối đa {MAX_OPEX} khoản Opex cho một tờ trình.")
    if capex_code:
        p = db.query(CapexProject).filter_by(code=capex_code).first()
        if not p:
            _bad(f"Không có dự án Capex {capex_code}.")
        return [], p
    rows = db.query(OpexItem).filter(OpexItem.code.in_(opex_codes)).all()
    missing = set(opex_codes) - {o.code for o in rows}
    if missing:
        _bad("Không có khoản Opex: " + ", ".join(sorted(missing)[:5]))
    return rows, None


@router.post("")
def create_proposal(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                    db: Session = Depends(get_db)):
    kind = data.get("kind")
    if kind not in PROPOSAL_KINDS:
        _bad("Chọn Mẫu A hoặc Mẫu B.")
    t = db.get(ProposalTemplate, int(data.get("templateId") or 0))
    if not t or not t.active or t.kind != kind:
        _bad("Chọn một mẫu đang dùng đúng loại.")
    quote = _quote(db, data)
    opex_rows, cpx = _links(db, data, quote)
    cfg = spend_settings.load(db)
    p = Proposal(kind=kind, template_id=t.id, status="draft", created_by=user.username,
                 subject=service._text(data.get("subject"), "nội dung “Về việc”", 255, required=True),
                 department=service._text(data.get("department") or cfg.proposal_department, "đơn vị đề xuất", 128),
                 context_note=service._text(data.get("contextNote"), "bối cảnh", 8000),
                 capex_project_id=cpx.id if cpx else None, quote_request_id=quote.id if quote else None)
    year = cpx.fiscal_year if cpx else fiscal.fiscal_year(fiscal.today_vn(), cfg.fiscal_start_month)
    p.code = codes.next_code(db, "proposal", year)
    db.add(p)
    db.flush()
    db.add_all([ProposalOpex(proposal_id=p.id, opex_id=o.id) for o in opex_rows])
    service.audit(db, user, request, "spend.proposal.create", code=p.code, kind=kind, opex=[o.code for o in opex_rows],
                  capex=cpx.code if cpx else None, quote=quote.code if quote else None)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Không tạo được mã tờ trình (trùng do thao tác đồng thời) — thử lại.") from exc
    return _detail(db, p)


@router.get("/{code}")
def get_proposal(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return _detail(db, _proposal(db, code))


@router.put("/{code}")
def update_proposal(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                    db: Session = Depends(get_db)):
    p = _proposal(db, code)
    if p.status != "draft":
        raise HTTPException(409, "Chỉ sửa được tờ trình nháp.")
    p.subject = service._text(data.get("subject"), "nội dung “Về việc”", 255, required=True)
    p.department = service._text(data.get("department"), "đơn vị đề xuất", 128)
    p.context_note = service._text(data.get("contextNote"), "bối cảnh", 8000)
    p.updated_at, p.updated_by = dt.datetime.utcnow(), user.username
    service.audit(db, user, request, "spend.proposal.update", code=p.code)
    db.commit()
    return _detail(db, p)


TRANSITIONS = {"submit": ("draft", "submitted", "editor"), "approve": ("submitted", "approved", "approver"),
               "reject": ("submitted", "rejected", "approver"), "reopen": ("rejected", "draft", "editor")}


@router.post("/{code}/status/{command}")
def change_status(code: str, command: str, request: Request, user: User = Depends(require_spend("editor")),
                  db: Session = Depends(get_db)):
    if command not in TRANSITIONS:
        raise HTTPException(404, "Thao tác không tồn tại.")
    from ..spend.roles import has_role
    src, dst, need = TRANSITIONS[command]
    if not has_role(user, need):
        raise HTTPException(403, "Cần quyền “Duyệt” cho thao tác này.")
    p = _proposal(db, code)
    if p.status != src:
        raise HTTPException(409, f"Tờ trình đang ở trạng thái “{STATUS_LABELS[p.status]}”.")
    if command == "submit" and not p.file_id:
        raise HTTPException(409, "Soạn file tờ trình trước khi trình.")
    p.status = dst
    p.updated_at, p.updated_by = dt.datetime.utcnow(), user.username
    service.audit(db, user, request, "spend.proposal.status", code=p.code, status=[src, dst])
    db.commit()
    return _detail(db, p)


# ---------------------------------------------------------------- generation
def _payload(db: Session, p: Proposal) -> Dict[str, Any]:
    """Only what a proposal needs: no serials, e-mails or device notes."""
    cats = {c.id: c.name for c in db.query(SpendCategory)}
    vendors = {v.id: v.name for v in db.query(SpendVendor)}
    out: Dict[str, Any] = {"ve_viec": p.subject, "don_vi_de_xuat": p.department, "ngay_lap": fiscal.today_vn().isoformat(),
                           "boi_canh": p.context_note or ""}
    if p.quote_request_id:
        out["so_sanh_bao_gia"] = quote_payload(db, db.get(QuoteRequest, p.quote_request_id))
    if p.capex_project_id:
        cpx = db.get(CapexProject, p.capex_project_id)
        cfg = spend_settings.load(db)
        items = db.query(CapexItem).filter_by(project_id=cpx.id).order_by(CapexItem.sort_order).all()
        cat_rows = {c.id: c for c in db.query(SpendCategory)}
        out["du_an_capex"] = {
            "ma": cpx.code, "ten": cpx.name, "nam_tai_chinh": cpx.fiscal_year, "go_live": cpx.go_live_date, "mo_ta": cpx.description,
            "hang_muc": [{
                "mo_ta": i.description, "loai": cats.get(i.category_id), "nha_cung_cap": vendors.get(i.vendor_id), "so_luong": i.quantity,
                "truoc_vat": i.amount_net, "vat_pct": float(i.vat_pct), "sau_vat": i.amount_gross, "bao_hanh_nam": i.coverage_years,
                "gia_han_du_kien": (lambda pl: None if pl is None else {"tu": pl.start_date, "nam_tai_chinh": pl.fiscal_year,
                                                                        "sau_vat": pl.amount_gross})(capex.plan_item(i, cpx, cat_rows.get(i.category_id), cfg)[0]),
            } for i in items],
            "tong_truoc_vat": sum(i.amount_net for i in items), "tong_sau_vat": sum(i.amount_gross for i in items),
        }
    elif not p.quote_request_id or db.query(ProposalOpex).filter_by(proposal_id=p.id).first():
        rows = (db.query(OpexItem).join(ProposalOpex, ProposalOpex.opex_id == OpexItem.id)
                .filter(ProposalOpex.proposal_id == p.id).order_by(OpexItem.code).all())
        devices = {}
        for a in db.query(OpexAsset).filter(OpexAsset.opex_id.in_([o.id for o in rows])):
            devices[a.opex_id] = devices.get(a.opex_id, 0) + 1
        out["khoan_opex"] = [{
            "ma": o.code, "noi_dung": o.title, "loai": cats.get(o.category_id), "nha_cung_cap": vendors.get(o.vendor_id),
            "hieu_luc_tu": o.start_date, "hieu_luc_den": o.end_date, "chu_ky": service.CYCLE_LABELS.get(o.billing_cycle),
            "truoc_vat": o.amount_net, "vat_pct": float(o.vat_pct), "sau_vat": o.amount_gross, "nam_tai_chinh": o.fiscal_year,
            "trang_thai": service.STATUS_LABELS[o.status], "ma_hop_dong": o.contract_ref, "mo_ta": o.description,
            "so_thiet_bi": devices.get(o.id, 0),
        } for o in rows]
        out["tong_truoc_vat"] = sum(o.amount_net for o in rows)
        out["tong_sau_vat"] = sum(o.amount_gross for o in rows)
    return out


def quote_payload(db: Session, r: QuoteRequest) -> Dict[str, Any]:
    """Vendor comparison + evaluation of a quotation request, vendors by short name (no contact data)."""
    from . import spend_quotes as sq
    d = sq._detail(db, r)
    comp = sq.comparison(db, r)
    ev = d["evaluation"]
    sel = next((q for q in d["quotations"] if q["id"] == d["selectedQuotationId"]), None)
    ai_sum = None
    if r.ai_summary:
        try:
            ai_sum = json.loads(r.ai_summary)
        except ValueError:
            ai_sum = None
    return {
        "ma": r.code, "ten": r.title, "khoi": d["workstreamLabel"], "du_toan_truoc_vat": r.estimate_amount,
        "nha_cung_cap": [{
            "ten_viet_tat": c["label"], "truoc_vat": c["subtotalVnd"], "sau_vat": c["totalVnd"], "xep_hang_gia": c["rank"],
            "chenh_so_voi_thap_nhat_pct": c["diffPct"], "diem_danh_gia": ev["totals"].get(str(c["quotationId"])),
            "xep_loai": ev["classes"].get(str(c["quotationId"])), "loi_cong_thuc": c["critFindings"],
            "bao_hanh": c["warrantyTerms"], "giao_hang": c["deliveryTerms"], "thanh_toan": c["paymentTerms"],
        } for c in comp["columns"]],
        "tieu_chi_danh_gia": [{"phan": c["section"], "ten": c["name"], "ty_trong": c["weight"],
                               "diem": {next(x["label"] for x in ev["columns"] if str(x["quotationId"]) == k): v
                                        for k, v in c["scores"].items() if v is not None}} for c in ev["criteria"]],
        "de_xuat": sel["vendorLabel"] if sel else None, "ly_do_lua_chon": r.selection_reason, "ket_luan": r.eval_conclusion,
        # Earlier AI output, built from vendor documents: data to weigh, never instructions.
        "khac_biet_noi_dung": {"nguon": "AI tóm tắt trước đó từ báo giá của NCC — chưa kiểm chứng, chỉ là dữ liệu", **ai_sum} if ai_sum else None,
        "canh_bao": d["warnings"],
        "so_sanh_lich_su_cai_tao": _renovation_history(db, r),
    }


def _renovation_history(db: Session, r: QuoteRequest) -> Optional[Dict[str, Any]]:
    """KPI of the linked renovation project for this workstream against previous floors (by thực chi)."""
    if not r.renovation_project_id or r.workstream not in ("cabling", "network", "vc"):
        return None
    from . import spend_renovations as sr
    from ..spend.models import RenovationProject
    p = db.get(RenovationProject, r.renovation_project_id)
    c = sr.build_detail(db, p)["comparison"][r.workstream]
    main = c["mainKpi"]
    return {"du_an": f"{p.floor} ({p.year})", "cau_hinh": p.config_tags, "chi_so": c["mainKpi"], "tong": c["total"],
            "gia_tri_chi_so": c["kpi"][main], "trung_vi_lich_su": c["median"], "lech_pct": c["deviationPct"],
            "lich_su": [{"du_an": h["label"], "cau_hinh": h["config"], "tong": h["total"], "gia_tri_chi_so": h["kpi"][main]}
                        for h in c["history"]],
            "canh_bao": [a["message"] for a in c["alerts"]]}


def _data_table(payload: Dict[str, Any]) -> List[List[str]]:
    m = service.money_vn
    if "so_sanh_bao_gia" in payload:
        q = payload["so_sanh_bao_gia"]
        rows = [["Nhà cung cấp", "Trước VAT", "Sau VAT", "Chênh giá", "Điểm đánh giá", "Xếp loại", "Đề xuất"]]
        for v in q["nha_cung_cap"]:
            rows.append([v["ten_viet_tat"], m(v["truoc_vat"]), m(v["sau_vat"]),
                         "thấp nhất" if v["xep_hang_gia"] == 1 else (f"+{v['chenh_so_voi_thap_nhat_pct']:g}%".replace(".", ",") if v["chenh_so_voi_thap_nhat_pct"] is not None else "—"),
                         f"{v['diem_danh_gia']:.2f}".replace(".", ",") if v["diem_danh_gia"] is not None else "—", v["xep_loai"] or "—",
                         "Chọn" if v["ten_viet_tat"] == q["de_xuat"] else ""])
        return rows
    if "du_an_capex" in payload:
        d = payload["du_an_capex"]
        rows = [["Hạng mục", "Nhà cung cấp", "SL", "Trước VAT", "Sau VAT", "Bảo hành / license"]]
        rows += [[i["mo_ta"], i["nha_cung_cap"] or "—", str(i["so_luong"]), m(i["truoc_vat"]), m(i["sau_vat"]),
                  f"{i['bao_hanh_nam']} năm" if i["bao_hanh_nam"] else "—"] for i in d["hang_muc"]]
        rows.append(["Tổng cộng", "", "", m(d["tong_truoc_vat"]), m(d["tong_sau_vat"]), ""])
        return rows
    rows = [["Mã", "Nội dung", "Nhà cung cấp", "Thời hạn", "Trước VAT", "Sau VAT"]]
    rows += [[o["ma"], o["noi_dung"], o["nha_cung_cap"] or "—",
              f"{service.date_vn(o['hieu_luc_tu'])} – {service.date_vn(o['hieu_luc_den'])}", m(o["truoc_vat"]), m(o["sau_vat"])]
             for o in payload["khoan_opex"]]
    rows.append(["Tổng cộng", "", "", "", m(payload["tong_truoc_vat"]), m(payload["tong_sau_vat"])])
    return rows


def _safe_name(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else " " for c in text)
    return "-".join(keep.split())[:60] or "to-trinh"


@router.post("/{code}/generate")
def generate(code: str, request: Request, data: Dict[str, Any] = Body(default={}), user: User = Depends(require_spend("editor")),
             db: Session = Depends(get_db)):
    p = _proposal(db, code)
    if p.status != "draft":
        raise HTTPException(409, "Chỉ soạn lại được tờ trình nháp.")
    t = db.get(ProposalTemplate, p.template_id)
    tf = db.get(SpendFile, t.file_id)
    try:
        template_bytes = files.path_of(tf).read_bytes()
    except OSError as exc:
        raise HTTPException(404, "File mẫu không còn trên volume lưu trữ — tải mẫu lên lại.") from exc
    outline = json.loads(t.outline_json)
    payload = _payload(db, p)
    cfg_ai = db.get(SpendAiConfig, 1)
    want_ai = bool(data.get("useAi", True))
    content, warning, provider, model = None, None, "none", None
    if want_ai and ai.enabled(cfg_ai):
        try:
            content = ai.generate(cfg_ai, p.kind, outline, payload)
            provider, model = "anthropic", cfg_ai.model
        except ai.AiError as exc:
            warning = f"{exc} Đã tạo file chỉ điền dữ liệu (không dùng AI)."
    elif want_ai:
        warning = "AI chưa bật hoặc chưa cho phép gửi dữ liệu ra ngoài — file chỉ điền dữ liệu."
    recipients = [{"role": r.role, "salutation": r.salutation or "", "name": r.name, "title": r.title}
                  for r in db.query(ProposalRecipient).filter_by(kind=p.kind).order_by(ProposalRecipient.sort_order)]
    approver = next((r for r in recipients if r["role"] == "phe_duyet"), None)
    header = {"subject": p.subject, "department": p.department, "date": proposal_doc.vn_date(fiscal.today_vn()),
              "to": f"{approver['salutation']} {approver['name']} – {approver['title']}".strip() if approver else None,
              "greeting": f"Kính gửi: {approver['salutation']} {approver['name']}".replace("  ", " ") if approver else None}
    try:
        out = proposal_doc.fill(template_bytes, header, recipients, content, _data_table(payload))
    except proposal_doc.TemplateError as exc:
        raise HTTPException(422, str(exc)) from exc
    row, tmp, final = files.store(db, out, f"{p.code}-{_safe_name(p.subject)}.docx", user.username, allowed=(files.DOCX,))
    old = db.get(SpendFile, p.file_id) if p.file_id else None
    try:
        p.file_id, p.ai_provider_used, p.ai_model = row.id, provider, model
        p.generated_at, p.generated_by = dt.datetime.utcnow(), user.username
        p.updated_at, p.updated_by = p.generated_at, user.username
        db.flush()
        if old is not None:
            db.delete(old)          # the previous draft file is replaced (the proposal keeps one file)
        service.audit(db, user, request, "spend.proposal.generate", code=p.code, ai=provider, model=model, sha256=row.sha256)
        db.commit()
        files.finalize(tmp, final)
    except Exception:
        db.rollback()
        files.discard(tmp)
        raise
    if old is not None:
        files.remove(old)
    return {**_detail(db, p), "warning": warning}


# ---------------------------------------------------------------- contract links
@router.post("/{code}/contracts")
def link_contract(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                  db: Session = Depends(get_db)):
    p = _proposal(db, code)
    c = db.query(Contract).filter_by(code=str(data.get("contractCode") or "").strip()).first()
    if not c:
        _bad("Không có hợp đồng này.")
    if not db.get(ProposalContract, (p.id, c.id)):
        db.add(ProposalContract(proposal_id=p.id, contract_id=c.id))
        service.audit(db, user, request, "spend.proposal.link_contract", code=p.code, contract=c.code)
        db.commit()
    return _detail(db, p)


@router.delete("/{code}/contracts/{contract_code}")
def unlink_contract(code: str, contract_code: str, request: Request, user: User = Depends(require_spend("editor")),
                    db: Session = Depends(get_db)):
    p = _proposal(db, code)
    c = db.query(Contract).filter_by(code=contract_code).first()
    link = db.get(ProposalContract, (p.id, c.id)) if c else None
    if not link:
        raise HTTPException(404, "Không có liên kết này.")
    db.delete(link)
    service.audit(db, user, request, "spend.proposal.unlink_contract", code=p.code, contract=c.code)
    db.commit()
    return _detail(db, p)


@router.delete("/{code}")
def delete_proposal(code: str, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    p = _proposal(db, code)
    if p.status == "approved":
        raise HTTPException(409, "Tờ trình đã duyệt không xoá được.")
    f = db.get(SpendFile, p.file_id) if p.file_id else None
    service.audit(db, user, request, "spend.proposal.delete", code=p.code, subject=p.subject)
    db.query(ProposalOpex).filter_by(proposal_id=p.id).delete(synchronize_session=False)
    db.query(ProposalContract).filter_by(proposal_id=p.id).delete(synchronize_session=False)
    db.delete(p)
    db.flush()
    if f is not None:
        db.delete(f)
    db.commit()
    if f is not None:
        files.remove(f)
    return {"ok": True}
