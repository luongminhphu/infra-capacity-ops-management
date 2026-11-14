"""Chi phí & Hợp đồng — Cải tạo văn phòng (IT): projects with scale, 3 workstreams, standard cost
groups, amounts per kind (dự toán / báo giá trúng / thực chi), history comparison, import of the
"Summarize" workbook, exports (Excel in the Summarize layout, PDF)."""
import datetime as dt
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import excel_renovation, report_renovation
from ..database import get_db
from ..features import require_feature
from ..models import User
from ..spend import codes, files, quotes as qm, renovation as rv, service
from ..spend.models import (
    COST_KINDS, RENOVATION_STATUSES, CapexProject, Quotation, QuotationLine, QuoteRequest, RenovationCost,
    RenovationCostGroup, RenovationProject, SpendVendor,
)
from ..spend.roles import has_role, require_spend
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/spend", tags=["spend-renovations"], dependencies=[Depends(require_feature("spend"))])
STATUS_LABELS = {"planning": "Lập kế hoạch", "executing": "Đang thi công", "closed": "Đã chốt"}
SCALE_FIELDS = (("nodes", "nodes", "số node mạng"), ("headcount", "headcount", "số nhân sự"), ("area_m2", "areaM2", "diện tích"),
                ("hub_rooms", "hubRooms", "số phòng hub"), ("meeting_rooms", "meetingRooms", "số phòng họp"),
                ("vc_rooms", "vcRooms", "số phòng họp có VC"), ("access_points", "accessPoints", "số access point"))
MAX_AMOUNT = 10 ** 13


def _bad(msg: str):
    raise HTTPException(400, msg)


# ================================================================ groups
def groups_by_ws(db: Session, active_only: bool = False) -> Dict[str, List[RenovationCostGroup]]:
    if db.query(RenovationCostGroup.id).first() is None:
        service.ensure_defaults(db)
    q = db.query(RenovationCostGroup)
    if active_only:
        q = q.filter(RenovationCostGroup.active.is_(True))
    out: Dict[str, List[RenovationCostGroup]] = {ws: [] for ws in rv.WORKSTREAMS}
    for g in q.order_by(RenovationCostGroup.workstream, RenovationCostGroup.seq, RenovationCostGroup.id):
        out[g.workstream].append(g)
    return out


def _group_out(g: RenovationCostGroup) -> Dict[str, Any]:
    return {"id": g.id, "workstream": g.workstream, "name": g.name, "seq": g.seq, "active": g.active,
            "aliases": [a for a in (g.aliases or "").splitlines() if a.strip()]}


@router.get("/renovation-groups")
def list_groups(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    return {ws: [_group_out(g) for g in gs] for ws, gs in groups_by_ws(db).items()}


@router.put("/renovation-groups")
def save_groups(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("admin")),
                db: Session = Depends(get_db)):
    """Full list per workstream: existing ids are updated, new rows created. Groups in use are deactivated, never deleted."""
    existing = {g.id: g for gs in groups_by_ws(db).values() for g in gs}
    for ws in rv.WORKSTREAMS:
        rows = data.get(ws)
        if rows is None:
            continue
        if not isinstance(rows, list) or len(rows) > 60:
            _bad("Tối đa 60 nhóm chi phí mỗi khối.")
        names = set()
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                _bad("Dữ liệu nhóm chi phí không hợp lệ.")
            name = service._text(r.get("name"), "tên nhóm chi phí", 255, required=True)
            if name.lower() in names:
                _bad(f"Nhóm “{name}” bị trùng trong khối {rv.WS_LABELS[ws]}.")
            names.add(name.lower())
            aliases = "\n".join(str(a).strip()[:120] for a in (r.get("aliases") or [])[:40] if str(a).strip()) or None
            g = existing.get(int(r["id"])) if r.get("id") else None
            if g is not None and g.workstream != ws:
                _bad("Nhóm chi phí không thuộc khối này.")
            if g is None:
                g = RenovationCostGroup(workstream=ws)
                db.add(g)
            g.name, g.aliases, g.seq, g.active = name, aliases, i, bool(r.get("active", True))
    service.audit(db, user, request, "spend.renovation.groups")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Tên nhóm chi phí trùng.") from exc
    return list_groups(user, db)


# ================================================================ helpers
def _project(db: Session, code: str) -> RenovationProject:
    p = db.query(RenovationProject).filter(RenovationProject.code == code).first()
    if not p:
        raise HTTPException(404, f"Không tìm thấy dự án {code}.")
    return p


def _view(p: RenovationProject, costs: List[RenovationCost], gws: Dict[int, str]) -> rv.ProjectView:
    amounts: Dict[str, Dict[int, Dict[str, int]]] = {}
    for c in costs:
        ws = gws.get(c.group_id)
        if ws:
            amounts.setdefault(ws, {}).setdefault(c.group_id, {})[c.kind] = c.amount
    return rv.ProjectView(id=p.id, code=p.code, label=f"{p.floor} ({p.year})", year=p.year, nodes=p.nodes, headcount=p.headcount,
                          vc_rooms=p.vc_rooms, access_points=p.access_points, config=p.config_tags, amounts=amounts)


def _costs_of(db: Session, ids: List[int]) -> Dict[int, List[RenovationCost]]:
    out: Dict[int, List[RenovationCost]] = {i: [] for i in ids}
    if ids:
        for c in db.query(RenovationCost).filter(RenovationCost.project_id.in_(ids)):
            out[c.project_id].append(c)
    return out


def _project_out(p: RenovationProject) -> Dict[str, Any]:
    return {"id": p.id, "code": p.code, "name": p.name, "floor": p.floor, "year": p.year, "status": p.status,
            "statusLabel": STATUS_LABELS[p.status], "isHistorical": p.is_historical,
            **{key: getattr(p, attr) for attr, key, _ in SCALE_FIELDS},
            "configTags": p.config_tags, "notes": p.notes, "rowVersion": p.row_version,
            "closedAt": to_iso_utc(p.closed_at), "closedBy": p.closed_by,
            "createdBy": p.created_by, "createdAt": to_iso_utc(p.created_at), "updatedBy": p.updated_by,
            "updatedAt": to_iso_utc(p.updated_at)}


def history_for(db: Session, p: RenovationProject) -> List[RenovationProject]:
    """Previous projects: historical ones and closed ones of earlier years (or same year, other floor)."""
    return (db.query(RenovationProject).filter(RenovationProject.id != p.id)
            .filter((RenovationProject.is_historical.is_(True)) | (RenovationProject.status == "closed"))
            .filter(RenovationProject.year <= p.year)
            .order_by(RenovationProject.year.desc(), RenovationProject.floor).limit(20).all())


def build_detail(db: Session, p: RenovationProject, history_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    gmap = groups_by_ws(db)
    gws = {g.id: ws for ws, gs in gmap.items() for g in gs}
    hist = history_for(db, p)
    if history_ids is not None:
        hist = [h for h in hist if h.id in set(history_ids)]
    costs = _costs_of(db, [p.id] + [h.id for h in hist])
    cur = _view(p, costs[p.id], gws)
    comp = rv.compare(cur, [_view(h, costs[h.id], gws) for h in hist],
                      {ws: [(g.id, g.name) for g in gs if g.active or any(c.group_id == g.id for c in costs[p.id])]
                       for ws, gs in gmap.items()})
    matrix = {}
    for c in costs[p.id]:
        matrix.setdefault(str(c.group_id), {})[c.kind] = {"amount": c.amount, "source": c.source, "note": c.note,
                                                          "updatedBy": c.updated_by, "updatedAt": to_iso_utc(c.updated_at)}
    reqs = db.query(QuoteRequest).filter(QuoteRequest.renovation_project_id == p.id).order_by(QuoteRequest.id).all()
    sel_ids = [r.selected_quotation_id for r in reqs if r.selected_quotation_id]
    sel = {q.id: q for q in db.query(Quotation).filter(Quotation.id.in_(sel_ids or [0]))}
    vendors = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_([q.vendor_id for q in sel.values()] or [0]))}
    cpx = db.get(CapexProject, p.capex_project_id) if p.capex_project_id else None
    warnings = []
    for ws in rv.WORKSTREAMS:
        est = sum(v["estimate"]["amount"] for k, v in matrix.items() if gws.get(int(k)) == ws and "estimate" in v)
        aw = sum(v["awarded"]["amount"] for k, v in matrix.items() if gws.get(int(k)) == ws and "awarded" in v)
        if est and aw and abs(aw - est) > est / 100:
            warnings.append(f"{rv.WS_LABELS[ws]}: dự toán {rv._vn(est)} khác báo giá trúng {rv._vn(aw)} "
                            f"(lệch {rv._vn(aw - est)}, {((aw - est) * 100 / est):+.1f}%).".replace(".0%", "%"))
    for attr, key, label in SCALE_FIELDS[:1] + SCALE_FIELDS[5:6]:
        if getattr(p, attr) is None:
            warnings.append(f"Chưa nhập {label} — không tính được chi phí / {('node' if attr == 'nodes' else 'phòng họp VC')}.")
    return {
        **_project_out(p), "capex": {"code": cpx.code, "name": cpx.name} if cpx else None,
        "groups": {ws: [_group_out(g) for g in gs] for ws, gs in gmap.items()},
        "costs": matrix, "comparison": comp,
        "historyAvailable": [{"id": h.id, "code": h.code, "label": f"{h.floor} ({h.year})", "config": h.config_tags}
                             for h in history_for(db, p)],
        "quoteRequests": [{"code": r.code, "title": r.title, "workstream": r.workstream, "status": r.status,
                           "selectedVendor": service.vendor_label(vendors.get(sel[r.selected_quotation_id].vendor_id))
                           if r.selected_quotation_id in sel else None,
                           "selectedSubtotal": sel[r.selected_quotation_id].stated_subtotal if r.selected_quotation_id in sel else None}
                          for r in reqs],
        "warnings": warnings,
    }


def _int_or_none(v: Any, label: str) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        _bad(f"{label[:1].upper()}{label[1:]} phải là số nguyên.")
    if not 0 <= n <= 1_000_000:
        _bad(f"{label[:1].upper()}{label[1:]} không hợp lệ.")
    return n


def _apply(db: Session, p: RenovationProject, data: Dict[str, Any]) -> None:
    t = service._text
    p.name = t(data.get("name"), "tên dự án", 255, required=True)
    p.floor = (t(data.get("floor"), "tầng", 32, required=True) or "").upper().replace(" ", "")
    try:
        p.year = int(data.get("year") or 0)
    except (TypeError, ValueError):
        _bad("Năm không hợp lệ.")
    if not 2000 <= p.year <= 2100:
        _bad("Năm không hợp lệ.")
    for attr, key, label in SCALE_FIELDS:
        setattr(p, attr, _int_or_none(data.get(key), label))
    p.config_tags = ", ".join(rv.tags_of(t(data.get("configTags"), "cấu hình", 255))) or None
    p.notes = t(data.get("notes"), "ghi chú", 5000)
    p.is_historical = bool(data.get("isHistorical", p.is_historical))
    st = data.get("status") or p.status or "planning"
    if st not in RENOVATION_STATUSES or (st == "closed" and p.status != "closed" and not p.is_historical):
        _bad("Trạng thái không hợp lệ (chốt dự án bằng nút “Chốt thực chi”).")
    p.status = "closed" if p.is_historical else st
    code = str(data.get("capexCode") or "").strip()
    if code:
        cpx = db.query(CapexProject).filter_by(code=code).first()
        if not cpx:
            _bad(f"Không có dự án Capex {code}.")
        p.capex_project_id = cpx.id
    else:
        p.capex_project_id = None


# ================================================================ projects
@router.get("/renovations")
def list_projects(user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    ps = db.query(RenovationProject).order_by(RenovationProject.year.desc(), RenovationProject.floor).limit(500).all()
    gws = {g.id: ws for ws, gs in groups_by_ws(db).items() for g in gs}
    costs = _costs_of(db, [p.id for p in ps])
    out = []
    for p in ps:
        v = _view(p, costs[p.id], gws)
        order = rv.PREFERENCE_HISTORY if p.is_historical or p.status == "closed" else rv.PREFERENCE_CURRENT
        per = {}
        for ws in rv.WORKSTREAMS:
            total, _ = rv.ws_total(v, ws, order)
            k = rv.kpis(ws, total, v)
            per[ws] = {"total": total, "kpi": k[rv.MAIN_KPI[ws]]}
        out.append({**_project_out(p), "workstreams": per,
                    "total": sum(x["total"] for x in per.values() if x["total"] is not None) if any(x["total"] is not None for x in per.values()) else None})
    return out


@router.post("/renovations")
def create_project(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    p = RenovationProject(created_by=user.username, status="planning", is_historical=False, row_version=1)
    _apply(db, p, data)
    if db.query(RenovationProject.id).filter_by(floor=p.floor, year=p.year).first():
        raise HTTPException(409, f"Đã có dự án {p.floor} năm {p.year}.")
    p.code = codes.next_code(db, "renovation", p.year)
    db.add(p)
    service.audit(db, user, request, "spend.renovation.create", code=p.code, floor=p.floor, year=p.year)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Trùng mã hoặc trùng tầng / năm — thử lại.") from exc
    return build_detail(db, p)


@router.get("/renovations/{code}")
def get_project(code: str, history: Optional[str] = None, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    ids = None
    if history:
        try:
            ids = [int(x) for x in history.split(",") if x.strip()][:20]
        except ValueError:
            _bad("Danh sách dự án so sánh không hợp lệ.")
    return build_detail(db, _project(db, code), ids)


@router.put("/renovations/{code}")
def update_project(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    p = _project(db, code)
    if int(data.get("rowVersion") or 0) != p.row_version:
        raise HTTPException(409, "Dự án vừa được người khác sửa — tải lại.")
    if p.status == "closed" and not has_role(user, "approver"):
        raise HTTPException(403, "Dự án đã chốt — cần quyền Duyệt để sửa.")
    floor, year = p.floor, p.year
    _apply(db, p, data)
    if (p.floor, p.year) != (floor, year) and db.query(RenovationProject.id).filter(
            RenovationProject.floor == p.floor, RenovationProject.year == p.year, RenovationProject.id != p.id).first():
        raise HTTPException(409, f"Đã có dự án {p.floor} năm {p.year}.")
    p.row_version += 1
    p.updated_at, p.updated_by = dt.datetime.utcnow(), user.username
    service.audit(db, user, request, "spend.renovation.update", code=p.code)
    db.commit()
    return build_detail(db, p)


@router.delete("/renovations/{code}")
def delete_project(code: str, request: Request, user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    p = _project(db, code)
    if db.query(QuoteRequest.id).filter_by(renovation_project_id=p.id).first():
        raise HTTPException(409, "Dự án đang gắn gói báo giá — bỏ liên kết trước khi xoá.")
    service.audit(db, user, request, "spend.renovation.delete", code=p.code, floor=p.floor, year=p.year)
    db.query(RenovationCost).filter_by(project_id=p.id).delete(synchronize_session=False)
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.put("/renovations/{code}/costs")
def save_costs(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
               db: Session = Depends(get_db)):
    """[{groupId, kind, amount|null, note}] — null removes the value. Thực chi needs an approver."""
    p = _project(db, code)
    if p.status == "closed" and not has_role(user, "approver"):
        raise HTTPException(403, "Dự án đã chốt — cần quyền Duyệt để sửa số liệu.")
    rows = data.get("costs")
    if not isinstance(rows, list) or len(rows) > 600:
        _bad("Danh sách số liệu không hợp lệ.")
    groups = {g.id for gs in groups_by_ws(db).values() for g in gs}
    existing = {(c.group_id, c.kind): c for c in db.query(RenovationCost).filter_by(project_id=p.id)}
    changed = []
    for r in rows:
        if not isinstance(r, dict):
            _bad("Dữ liệu không hợp lệ.")
        try:
            gid = int(r.get("groupId"))
        except (TypeError, ValueError):
            _bad("Nhóm chi phí không hợp lệ.")
        kind = r.get("kind")
        if gid not in groups or kind not in COST_KINDS:
            _bad("Nhóm chi phí hoặc loại số liệu không hợp lệ.")
        if kind == "actual" and not p.is_historical and not has_role(user, "approver"):
            raise HTTPException(403, "Nhập thực chi cần quyền Duyệt.")
        amt = r.get("amount")
        c = existing.get((gid, kind))
        if amt in (None, ""):
            if c is not None:
                changed.append((gid, kind, c.amount, None))
                db.delete(c)
            continue
        val = qm.to_int(qm.parse_number(amt))           # accepts 2.000.000.000 / 2,000,000,000 / 2000000000
        if val is None or not 0 <= val <= MAX_AMOUNT:
            _bad("Số tiền không hợp lệ.")
        if c is None:
            c = RenovationCost(project_id=p.id, group_id=gid, kind=kind, amount=val, source="manual", updated_by=user.username)
            db.add(c)
            changed.append((gid, kind, None, val))
        elif c.amount != val or (r.get("note") or None) != c.note:
            changed.append((gid, kind, c.amount, val))
            c.amount, c.source = val, "manual"
        c.note = service._text(r.get("note"), "ghi chú", 500)
        c.updated_at, c.updated_by = dt.datetime.utcnow(), user.username
    if changed:
        p.row_version += 1
        service.audit(db, user, request, "spend.renovation.costs", code=p.code, changes=changed[:100])
    db.commit()
    return build_detail(db, p)


# ================================================================ link with quotations
def _line_amount_vnd(l: QuotationLine, q: Quotation) -> Optional[int]:
    fx = Decimal(1) if (q.currency or "VND") == "VND" else Decimal(q.fx_rate or 0)
    if l.amount is not None:
        a = Decimal(l.amount)
    elif l.qty is not None and l.unit_price is not None:
        a = Decimal(l.qty) * Decimal(l.unit_price)
    else:
        return None
    return int((a * fx).quantize(Decimal(1)))


def awarded_from_quotes(db: Session, p: RenovationProject) -> Dict[str, Any]:
    gmap = groups_by_ws(db, active_only=True)
    reqs = db.query(QuoteRequest).filter(QuoteRequest.renovation_project_id == p.id).all()
    per: Dict[int, int] = {}
    unmatched, used = [], []
    for r in reqs:
        if r.workstream not in rv.WORKSTREAMS or not r.selected_quotation_id:
            continue
        q = db.get(Quotation, r.selected_quotation_id)
        gl = [(g.id, g.name, g.aliases) for g in gmap[r.workstream]]
        ids = {g.id for g in gmap[r.workstream]}
        used.append(r.code)
        for l in db.query(QuotationLine).filter_by(quotation_id=q.id, line_type="item").order_by(QuotationLine.seq):
            amt = _line_amount_vnd(l, q)
            if amt is None:
                continue
            gid = l.cost_group_id if l.cost_group_id in ids else rv.match_group(f"{l.description} {l.model or ''}", gl)
            if gid is None:
                unmatched.append({"lineId": l.id, "request": r.code, "workstream": r.workstream, "description": l.description,
                                  "model": l.model, "amount": amt})
                continue
            per[gid] = per.get(gid, 0) + amt
    return {"amounts": per, "unmatched": unmatched, "requests": used}


@router.get("/renovations/{code}/quote-mapping")
def quote_mapping(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    m = awarded_from_quotes(db, _project(db, code))
    return {"amounts": {str(k): v for k, v in m["amounts"].items()}, "unmatched": m["unmatched"], "requests": m["requests"]}


@router.post("/renovations/{code}/pull-quotes")
def pull_quotes(code: str, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    """Báo giá trúng per cost group = lines of the selected quotation of each linked request (before VAT)."""
    p = _project(db, code)
    if p.status == "closed":
        raise HTTPException(409, "Dự án đã chốt.")
    m = awarded_from_quotes(db, p)
    if not m["requests"]:
        raise HTTPException(409, "Chưa có gói báo giá nào của dự án đã chọn nhà cung cấp.")
    if m["unmatched"]:
        raise HTTPException(409, f"Còn {len(m['unmatched'])} dòng báo giá chưa gắn nhóm chi phí — gắn nhóm trước khi lấy số.")
    ws_of = {g.id: ws for ws, gs in groups_by_ws(db).items() for g in gs}
    pulled_ws = {r.workstream for r in db.query(QuoteRequest).filter(QuoteRequest.code.in_(m["requests"]))}
    for c in db.query(RenovationCost).filter_by(project_id=p.id, kind="awarded"):
        if ws_of.get(c.group_id) in pulled_ws:
            db.delete(c)
    db.flush()
    for gid, amt in m["amounts"].items():
        db.add(RenovationCost(project_id=p.id, group_id=gid, kind="awarded", amount=amt, source="quote", updated_by=user.username,
                              note="Từ " + ", ".join(m["requests"])))
    p.row_version += 1
    service.audit(db, user, request, "spend.renovation.pull_quotes", code=p.code, requests=m["requests"],
                  total=sum(m["amounts"].values()))
    db.commit()
    return build_detail(db, p)


@router.put("/renovations/{code}/line-group")
def set_line_group(code: str, request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_spend("editor")),
                   db: Session = Depends(get_db)):
    p = _project(db, code)
    try:
        line_id, gid = int(data.get("lineId") or 0), (int(data["groupId"]) if data.get("groupId") else None)
    except (TypeError, ValueError):
        _bad("Dữ liệu không hợp lệ.")
    row = (db.query(QuotationLine, QuoteRequest).join(Quotation, Quotation.id == QuotationLine.quotation_id)
           .join(QuoteRequest, QuoteRequest.id == Quotation.request_id)
           .filter(QuotationLine.id == line_id, QuoteRequest.renovation_project_id == p.id).first())
    if not row:
        raise HTTPException(404, "Không tìm thấy dòng báo giá của dự án này.")
    line, r = row
    if gid is not None:
        g = db.get(RenovationCostGroup, gid)
        if not g or g.workstream != r.workstream:
            _bad("Nhóm chi phí không thuộc khối của gói báo giá.")
    line.cost_group_id = gid
    db.commit()
    return quote_mapping(code, user, db)


# ================================================================ close
@router.post("/renovations/{code}/close")
def close_project(code: str, request: Request, user: User = Depends(require_spend("approver")), db: Session = Depends(get_db)):
    """Chốt thực chi: groups without thực chi take báo giá trúng (source noted); the project becomes history."""
    p = _project(db, code)
    if p.status == "closed":
        raise HTTPException(409, "Dự án đã chốt.")
    costs = db.query(RenovationCost).filter_by(project_id=p.id).all()
    have = {c.group_id for c in costs if c.kind == "actual"}
    copied = 0
    for c in costs:
        if c.kind == "awarded" and c.group_id not in have:
            db.add(RenovationCost(project_id=p.id, group_id=c.group_id, kind="actual", amount=c.amount, source=c.source,
                                  note="Chốt theo báo giá trúng", updated_by=user.username))
            copied += 1
    if not have and not copied:
        raise HTTPException(409, "Chưa có thực chi hay báo giá trúng nào để chốt.")
    p.status, p.closed_at, p.closed_by = "closed", dt.datetime.utcnow(), user.username
    p.row_version += 1
    service.audit(db, user, request, "spend.renovation.close", code=p.code, copied=copied)
    db.commit()
    return build_detail(db, p)


@router.post("/renovations/{code}/reopen")
def reopen_project(code: str, request: Request, user: User = Depends(require_spend("approver")), db: Session = Depends(get_db)):
    p = _project(db, code)
    if p.status != "closed" or p.is_historical:
        raise HTTPException(409, "Chỉ mở lại được dự án đã chốt (không phải dự án lịch sử nhập từ file).")
    p.status, p.closed_at, p.closed_by = "executing", None, None
    p.row_version += 1
    service.audit(db, user, request, "spend.renovation.reopen", code=p.code)
    db.commit()
    return build_detail(db, p)


# ================================================================ import of the Summarize workbook
def _preview(db: Session, res: rv.SummarizeResult) -> Dict[str, Any]:
    gmap = groups_by_ws(db)
    out = []
    for ip in res.projects:
        blocks = {}
        for ws, labels in ip.amounts.items():
            gl = [(g.id, g.name, g.aliases) for g in gmap[ws]]
            names = {g.id: g.name for g in gmap[ws]}
            lines = []
            for lab, amt in labels.items():
                gid = rv.match_group(lab, gl)
                lines.append({"label": lab, "amount": amt, "groupId": gid, "group": names.get(gid)})
            blocks[ws] = {"total": sum(labels.values()), "stated": ip.stated.get(ws), "lines": lines}
        exist = db.query(RenovationProject).filter(RenovationProject.floor == ip.floor).order_by(RenovationProject.year.desc()).all()
        out.append({"floor": ip.floor, "nodes": ip.nodes, "headcount": ip.headcount, "configTags": ip.config,
                    "vcRooms": ip.vc_rooms, "meetingRooms": ip.meeting_rooms, "blocks": blocks,
                    "existing": [{"code": e.code, "year": e.year, "isHistorical": e.is_historical} for e in exist]})
    return {"projects": out, "findings": res.findings, "message": res.message}


@router.post("/renovations/import")
async def import_summarize(request: Request, file: UploadFile = File(...), apply: Optional[str] = Form(default=None),
                           user: User = Depends(require_spend("admin")), db: Session = Depends(get_db)):
    """Without `apply`: preview. With `apply` = {"projects": [{floor, year, kind, name, include}]}: create / update
    historical projects (status closed) and their amounts; unknown labels become new cost groups."""
    data = await files.read_upload(file)
    try:
        if files.detect_type(data, (files.XLSX,)) != files.XLSX:
            raise HTTPException(415, "Chỉ nhận file .xlsx.")
    except files.FileRejected as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    res = await run_in_threadpool(rv.read_summarize, data)
    prev = _preview(db, res)
    if not apply:
        return prev
    try:
        choices = json.loads(apply)
        picks = {str(c["floor"]).upper(): c for c in choices.get("projects", []) if isinstance(c, dict) and c.get("include", True)}
    except (ValueError, TypeError, KeyError, AttributeError):
        _bad("Lựa chọn nhập không hợp lệ.")
    gmap = groups_by_ws(db)
    created, updated = [], []
    for ip in res.projects:
        c = picks.get(ip.floor)
        if not c:
            continue
        kind = c.get("kind") or "actual"
        if kind not in COST_KINDS:
            _bad("Loại số liệu không hợp lệ.")
        try:
            year = int(c.get("year"))
        except (TypeError, ValueError):
            _bad(f"Nhập năm cho {ip.floor}.")
        if not 2000 <= year <= 2100:
            _bad(f"Năm của {ip.floor} không hợp lệ.")
        p = db.query(RenovationProject).filter_by(floor=ip.floor, year=year).first()
        if p is None:
            p = RenovationProject(floor=ip.floor, year=year, name=service._text(c.get("name"), "tên", 255) or f"Cải tạo {ip.floor}",
                                  status="closed", is_historical=True, created_by=user.username, row_version=1,
                                  code=codes.next_code(db, "renovation", year))
            db.add(p)
            created.append(ip.floor)
        else:
            if p.status != "closed" and not p.is_historical:
                _bad(f"{ip.floor} {year} là dự án đang làm — không ghi đè bằng số liệu nhập.")
            updated.append(ip.floor)
            p.row_version += 1
        for attr, val in (("nodes", ip.nodes), ("headcount", ip.headcount), ("config_tags", ip.config),
                          ("vc_rooms", ip.vc_rooms), ("meeting_rooms", ip.meeting_rooms)):
            if getattr(p, attr) is None and val is not None:
                setattr(p, attr, val)
        db.flush()
        sums: Dict[int, int] = {}
        for ws, labels in ip.amounts.items():
            gl = [(g.id, g.name, g.aliases) for g in gmap[ws]]
            for lab, amt in labels.items():
                gid = rv.match_group(lab, gl)
                if gid is None:
                    g = RenovationCostGroup(workstream=ws, name=lab[:255], seq=len(gmap[ws]), active=True)
                    db.add(g)
                    db.flush()
                    gmap[ws].append(g)
                    gl.append((g.id, g.name, g.aliases))
                    gid = g.id
                sums[gid] = sums.get(gid, 0) + amt
        old = {x.group_id: x for x in db.query(RenovationCost).filter_by(project_id=p.id, kind=kind)}
        for gid, amt in sums.items():
            x = old.get(gid)
            if x is None:
                db.add(RenovationCost(project_id=p.id, group_id=gid, kind=kind, amount=amt, source="import",
                                      note=f"Nhập từ {files.clean_name(file.filename, files.XLSX)}", updated_by=user.username))
            else:
                x.amount, x.source, x.updated_by, x.updated_at = amt, "import", user.username, dt.datetime.utcnow()
    service.audit(db, user, request, "spend.renovation.import", file=files.clean_name(file.filename, files.XLSX),
                  created=created, updated=updated)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Trùng dự án (tầng / năm) — thử lại.") from exc
    return {**prev, "created": created, "updated": updated}


# ================================================================ exports
def _download(content: bytes, name: str, media: str) -> Response:
    return Response(content, media_type=media, headers={
        "Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{urlquote(name)}", "Cache-Control": "private, no-store"})


def _export_data(db: Session, p: RenovationProject) -> Dict[str, Any]:
    d = build_detail(db, p)
    vendors = {}
    for r in db.query(QuoteRequest).filter(QuoteRequest.renovation_project_id == p.id).order_by(QuoteRequest.id):
        qs = db.query(Quotation).filter_by(request_id=r.id).order_by(Quotation.id).all()
        vs = {v.id: v for v in db.query(SpendVendor).filter(SpendVendor.id.in_([q.vendor_id for q in qs] or [0]))}
        if r.workstream in rv.WORKSTREAMS and r.workstream not in vendors:
            vendors[r.workstream] = {"request": r.code, "selected": r.selected_quotation_id,
                                     "columns": [{"id": q.id, "label": service.vendor_label(vs.get(q.vendor_id)),
                                                  "subtotal": q.stated_subtotal} for q in qs]}
    d["vendors"] = vendors
    return d


@router.get("/renovations/{code}/export.xlsx")
def export_xlsx(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    p = _project(db, code)
    return _download(excel_renovation.build_xlsx(_export_data(db, p)), f"cai-tao-{p.floor}-{p.year}.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/renovations/{code}/export.pdf")
def export_pdf(code: str, user: User = Depends(require_spend("viewer")), db: Session = Depends(get_db)):
    p = _project(db, code)
    return _download(report_renovation.build_pdf(_export_data(db, p), user.username), f"cai-tao-{p.floor}-{p.year}.pdf", "application/pdf")
