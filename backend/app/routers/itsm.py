"""Nhu cầu hạ tầng → Yêu cầu ServiceDesk (ICO v2 G7b): review queue of the requests read from ServiceDesk Plus.

Reads: any user of the full app (get_current_user). Decisions (approve / skip), labels and "read again": admin only.
The original body is shown in a sandboxed iframe (no scripts, no external resources): see display_html."""
import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..downloads import attachment
from ..features import require_feature
from ..itsm import link, process
from ..itsm.models import BEFORE_FIELDS, KIND_LABELS, REQUEST_KINDS, STORED_FIELDS, ItsmLabelMap, ItsmProjectMap, ItsmRequest, ItsmRequestCi, ItsmRequestLine
from ..itsm.parse import DEFAULT_LABELS, parse_html
from ..models import AuditLog, User
from ..security import get_current_user, require_admin
from ..spend.roles import require_spend
from ..shared.text import norm_text
from ..timeutil import to_iso_utc as _iso

router = APIRouter(prefix="/api/itsm", tags=["itsm"])
STATUS_LABELS = {"parsed": "Đọc tự động", "review": "Chờ duyệt", "failed": "Bỏ qua", None: "Chưa đọc"}
FIELD_LABELS = {"vm_name": "Tên VM", "env": "Môi trường", "os": "Hệ điều hành", "vcpu": "vCPU", "ram_gb": "RAM (GB)",
                "disk_gb": "Disk (GB)", "qty": "Số lượng", "project": "Dự án", "ignore": "Bỏ qua cột này",
                "vcpu_before": "vCPU as-is", "ram_gb_before": "RAM as-is (GB)", "disk_gb_before": "Disk as-is (GB)"}
LABEL_FIELDS = {k: v for k, v in FIELD_LABELS.items() if k not in BEFORE_FIELDS}
_DROP_TAGS = re.compile(r"<\s*/?\s*(img|link|meta|base|form|input|button|svg|video|audio|source|object|embed|iframe)\b[^>]*>", re.I)
_STYLE_ATTR = re.compile(r"\s+(style|src|href|background)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)


def display_html(html: Optional[str]) -> str:
    """Body for the sandboxed iframe: already cleaned at storage (no scripts / handlers); here also no element or attribute
    that would make the reader's browser fetch something (images, links, styles with url())."""
    return _STYLE_ATTR.sub("", _DROP_TAGS.sub("", html or ""))


def _out(r: ItsmRequest, lines: Optional[List[ItsmRequestLine]] = None) -> Dict[str, Any]:
    d = {"id": r.id, "source": r.source_key, "reqId": r.req_id, "template": r.template, "subject": r.subject, "kind": r.kind,
         "kindLabel": KIND_LABELS.get(r.kind, r.kind), "requester": r.requester_name, "requesterEmail": r.requester_email,
         "department": r.department, "status": r.status, "approvalStatus": r.approval_status, "createdAt": _iso(r.created_at),
         "parseStatus": r.parse_status, "parseLabel": STATUS_LABELS.get(r.parse_status, r.parse_status),
         "confidence": r.confidence, "reviewedBy": r.reviewed_by, "reviewedAt": _iso(r.reviewed_at)}
    if lines is not None:
        d["lines"] = [{"lineNo": x.line_no, **{f: getattr(x, f) for f in STORED_FIELDS}} for x in lines]
    return d


@router.get("/requests")
def requests(status: str = Query(default="review", pattern="^(review|parsed|failed|reviewed|all)$"),
             kind: Optional[str] = Query(default=None, max_length=16), q: Optional[str] = Query(default=None, max_length=120),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    qry = db.query(ItsmRequest)
    if status == "reviewed":
        qry = qry.filter(ItsmRequest.reviewed_at.isnot(None))
    elif status != "all":
        qry = qry.filter(ItsmRequest.parse_status == status, ItsmRequest.reviewed_at.is_(None) if status == "review" else True)
    if kind in REQUEST_KINDS:
        qry = qry.filter(ItsmRequest.kind == kind)
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(ItsmRequest.subject.ilike(like), ItsmRequest.req_id.ilike(like), ItsmRequest.requester_email.ilike(like)))
    rows = qry.order_by(ItsmRequest.created_at.desc(), ItsmRequest.id.desc()).limit(1001).all()
    counts = dict(db.query(ItsmRequest.parse_status, func.count(ItsmRequest.id)).group_by(ItsmRequest.parse_status).all())
    pending = db.query(func.count(ItsmRequest.id)).filter(ItsmRequest.parse_status == "review", ItsmRequest.reviewed_at.is_(None)).scalar()
    total = sum(counts.values())
    with_table = db.query(func.count(ItsmRequest.id)).filter(ItsmRequest.description.ilike("%<table%")).scalar() or 0
    auto_tables = (db.query(func.count(ItsmRequest.id)).filter(ItsmRequest.description.ilike("%<table%"),
                                                               ItsmRequest.parse_status == "parsed", ItsmRequest.reviewed_at.is_(None)).scalar() or 0)
    return {"items": [_out(r) for r in rows[:1000]], "truncated": len(rows) > 1000,
            "summary": {"total": total, "parsed": counts.get("parsed", 0), "pending": pending,
                        "counted": process.counted(db.query(func.count(ItsmRequest.id))).scalar(),
                        "failed": counts.get("failed", 0), "notParsed": counts.get(None, 0),
                        "autoTablePct": round(auto_tables * 100 / with_table, 1) if with_table else None},
            "kinds": [{"key": k, "label": v} for k, v in KIND_LABELS.items()]}


def _req(db: Session, rid: int) -> ItsmRequest:
    r = db.get(ItsmRequest, rid)
    if r is None:
        raise HTTPException(404, "Không có yêu cầu này.")
    return r


@router.get("/requests/{rid}")
def request_detail(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _req(db, rid)
    lines = db.query(ItsmRequestLine).filter_by(request_id=r.id).order_by(ItsmRequestLine.line_no).all()
    res = parse_html(r.description, process.labels(db))
    from ..cmdb.models import Ci
    from ..owners.models import System
    s = db.get(System, r.system_id) if r.system_id else None
    from .. import vm_ip
    vms = vm_ip.attach(db, [{"lineNo": n, "match": m, "ciId": cid, "vm": name, "retired": st == "retired",
                             "vcenterKey": json.loads(attrs or "{}").get("vcenterKey") or ""}
                            for n, m, cid, name, st, attrs in
                            db.query(ItsmRequestCi.line_no, ItsmRequestCi.match, Ci.id, Ci.name, Ci.status, Ci.attrs)
                            .join(Ci, Ci.id == ItsmRequestCi.ci_id).filter(ItsmRequestCi.request_id == r.id)
                            .order_by(ItsmRequestCi.line_no, Ci.name)])
    return {**_out(r, lines), "bodyHtml": display_html(r.description), "unknownLabels": res["unknownLabels"],
            "project": r.project, "sdpUrl": link.sdp_url(r.source_key, r.req_id),
            "system": {"id": s.id, "code": s.code, "name": s.name} if s else None,
            "vms": vms,
            "fields": [{"key": k, "label": FIELD_LABELS[k] + (" to-be" if r.kind == "resize" and k in ("vcpu", "ram_gb", "disk_gb") else "")}
                       for k in _fields(r.kind)],
            "labelFields": [{"key": k, "label": v} for k, v in LABEL_FIELDS.items()]}


REVIEW_ORDER = ("vm_name", "vcpu", "ram_gb", "disk_gb", "qty", "env", "os", "project")     # sizes first: what is checked


def _fields(kind: str) -> List[str]:
    """Columns of the review table: a reconfiguration shows As-is next to To-be for vCPU / RAM / Disk."""
    out = []
    for f in REVIEW_ORDER:
        if kind == "resize" and f in ("vcpu", "ram_gb", "disk_gb"):
            out.append(f + "_before")
        out.append(f)
    return out


@router.post("/requests/{rid}/review")
def review(rid: int, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """{action: approve | skip, lines?: [...], kind?} — approve saves the (edited) lines; the decision is final."""
    action = data.get("action")
    if action not in ("approve", "skip"):
        raise HTTPException(400, "Thao tác phải là approve hoặc skip.")
    lines = data.get("lines")
    if lines is not None and (not isinstance(lines, list) or not all(isinstance(x, dict) for x in lines)):
        raise HTTPException(422, "lines phải là danh sách dòng.")
    r = _req(db, rid)
    process.review(db, r, user, action, lines, data.get("kind"))
    db.add(AuditLog(username=user.username, action=f"itsm.review.{action}", detail=f'{{"request": "{r.req_id}"}}'))
    db.commit()
    return request_detail(rid, user, db)


@router.get("/projects")
def projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Projects named in the requests → system: confirmed mapping, exact match, or close suggestions to confirm (G7c)."""
    from ..owners.models import System
    systems = {s.id: s for s in db.query(System)}
    mapped = {m.project_norm for m in db.query(ItsmProjectMap)}
    rows = (process.counted(db.query(ItsmRequest.project, ItsmRequest.system_id, func.count(ItsmRequest.id)))
            .filter(ItsmRequest.project.isnot(None)).group_by(ItsmRequest.project, ItsmRequest.system_id).all())
    matcher = link.SystemMatcher(db) if any(sid is None for _, sid, _ in rows) else None
    out = []
    for project, sid, n in sorted(rows, key=lambda x: (x[1] is not None, -x[2], x[0])):
        s = systems.get(sid)
        out.append({"project": project, "requests": n, "system": {"id": s.id, "code": s.code, "name": s.name} if s else None,
                    "how": None if s is None else "mapped" if norm_text(project) in mapped else "exact",
                    "suggestions": matcher.suggest(project) if s is None else []})
    return {"projects": out, "systems": [{"id": s.id, "code": s.code, "name": s.name}
                                         for s in sorted(systems.values(), key=lambda s: s.code) if s.status == "active"]}


@router.post("/projects")
def map_project(data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """{project, systemId} confirms project → system for every request naming it; systemId null removes the mapping."""
    from ..owners.models import System
    project = str(data.get("project") or "").strip()[:255]
    key = norm_text(project)
    if not key:
        raise HTTPException(422, "Thiếu tên dự án.")
    sid = data.get("systemId")
    m = db.query(ItsmProjectMap).filter_by(project_norm=key).first()
    if sid is None:
        if m is not None:
            db.delete(m)
    else:
        s = db.get(System, sid) if isinstance(sid, int) else None
        if s is None or s.status != "active":
            raise HTTPException(422, "Hệ thống không tồn tại hoặc đã ngừng.")
        if m is None:
            db.add(ItsmProjectMap(project_norm=key, project=project, system_id=s.id, created_by=user.username))
        else:
            m.system_id, m.created_by = s.id, user.username
    db.add(AuditLog(username=user.username, action="itsm.project.map", detail=json.dumps({"project": project, "systemId": sid})))
    db.flush()
    link.run(db)        # re-link now; owner assignment ("itsm" source) follows at the next round
    return projects(user, db)


@router.get("/labels")
def get_labels(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    custom = db.query(ItsmLabelMap).order_by(ItsmLabelMap.label_norm).all()
    return {"custom": [{"id": x.id, "label": x.label_norm, "field": x.field, "fieldLabel": FIELD_LABELS.get(x.field),
                        "createdBy": x.created_by} for x in custom],
            "builtIn": [{"label": k, "field": v, "fieldLabel": FIELD_LABELS[v]} for k, v in sorted(DEFAULT_LABELS.items())],
            "fields": [{"key": k, "label": v} for k, v in LABEL_FIELDS.items()]}


@router.post("/labels")
def add_label(data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """A table label seen on the review screen → a standard field; every request no person decided is read again."""
    label, field = norm_text(str(data.get("label") or ""))[:128], data.get("field")
    if not label or field not in LABEL_FIELDS:
        raise HTTPException(400, "Nhập nhãn và chọn trường chuẩn.")
    row = db.query(ItsmLabelMap).filter_by(label_norm=label).first()
    if row is None:
        db.add(ItsmLabelMap(label_norm=label, field=field, created_by=user.username))
    else:
        row.field, row.created_by = field, user.username
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Nhãn vừa được thêm bởi người khác — tải lại.") from None
    return {"label": label, "field": field, "reparsed": process.reparse(db)}


@router.delete("/labels/{lid}")
def delete_label(lid: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.get(ItsmLabelMap, lid)
    if row is None:
        raise HTTPException(404, "Không có nhãn này.")
    db.delete(row)
    db.commit()
    return {"ok": True, "reparsed": process.reparse(db)}


@router.get("/stats")
def request_stats(months: int = Query(default=12, ge=1, le=24), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """VM provisioning / reconfiguration / decommission over the last `months` months (default one year)."""
    return {**process.stats(db, months), "kinds": [{"key": k, "label": v} for k, v in KIND_LABELS.items()]}


# ---------------------------------------------------------------- G8: annual review + next year's plan
def _year(year: Optional[int]) -> int:
    from ..spend.fiscal import today_vn
    cur = today_vn().year
    y = year or cur
    if not cur - 5 <= y <= cur:
        raise HTTPException(400, f"Năm tổng kết từ {cur - 5} đến {cur}.")
    return y


@router.get("/annual")
def annual_view(year: Optional[int] = Query(default=None), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Tổng kết năm `year` + kế hoạch năm `year` + 1 (itsm/annual.py — same numbers as the PDF / Excel)."""
    from ..itsm import annual
    return jsonable_encoder(annual.build(db, _year(year)))


@router.put("/annual/{plan_year}/inputs")
def annual_inputs(plan_year: int, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Projects typed for next year + parameters (factor on / off, standard host, unit price, target %)."""
    from ..itsm import annual
    year = _year(plan_year - 1)
    try:
        params, projects = annual.clean_inputs(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    annual.save_inputs(db, year + 1, params, projects, user.username)
    db.add(AuditLog(username=user.username, action="itsm.plan.inputs", detail=json.dumps({"year": year + 1, "projects": len(projects)})))
    db.commit()
    return jsonable_encoder(annual.build(db, year))


@router.post("/annual/{plan_year}/budget", dependencies=[Depends(require_feature("spend"))])
def annual_to_budget(plan_year: int, request: Request, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    """“Đưa vào Kế hoạch ngân sách”: freeze the plan and create / update the Capex line “Mở rộng hạ tầng ảo hoá <năm>” of the
    draft budget plan of that fiscal year (one line per year: source "capacity", sourceId = year). Without a draft plan the result
    is kept and the line appears when the plan is created."""
    from ..itsm import annual
    from ..spend import budget_plan as bp, service as spend_service, settings as spend_settings
    from ..spend.models import BudgetPlan
    d = annual.build(db, _year(plan_year - 1))
    snap = annual.push(db, d, user.username)
    db.flush()
    plan = (db.query(BudgetPlan).filter(BudgetPlan.fiscal_year == d["planYear"], BudgetPlan.status == "draft")
            .order_by(BudgetPlan.version.desc()).first())
    stats = bp.refresh(db, plan, spend_settings.load(db), user, only="capacity") if plan else None
    spend_service.audit(db, user, request, "spend.plan.capacity", year=d["planYear"], nodes=snap["nodes"], amount=snap["amount"],
                        plan=bp.plan_label(plan) if plan else None, lines=stats)
    db.commit()
    return {"snapshot": snap, "plan": {"id": plan.id, "label": bp.plan_label(plan)} if plan else None, "lines": stats}


def _annual_report(year: Optional[int], db: Session):
    from ..itsm import annual
    return annual.build(db, _year(year))


@router.get("/annual/report.pdf")
def annual_pdf(year: Optional[int] = Query(default=None), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..report_annual import build_pdf
    d = _annual_report(year, db)
    return attachment(build_pdf(d, user.display_name or user.username), f"ICO_Nhu_cau_ha_tang_{d['year']}_ke_hoach_{d['planYear']}.pdf")


@router.get("/annual/report.xlsx")
def annual_xlsx(year: Optional[int] = Query(default=None), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..excel_annual import build_xlsx
    d = _annual_report(year, db)
    return attachment(build_xlsx(d), f"ICO_Nhu_cau_ha_tang_{d['year']}_ke_hoach_{d['planYear']}.xlsx")
