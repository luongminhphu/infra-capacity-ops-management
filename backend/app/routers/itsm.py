"""Nhu cầu hạ tầng → Yêu cầu ServiceDesk (ICO v2 G7b): review queue of the requests read from ServiceDesk Plus.

Reads: any user of the full app (get_current_user). Decisions (approve / skip), labels and "read again": admin only.
The original body is shown in a sandboxed iframe (no scripts, no external resources): see display_html."""
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..itsm import process
from ..itsm.models import BEFORE_FIELDS, REQUEST_KINDS, STORED_FIELDS, ItsmLabelMap, ItsmRequest, ItsmRequestLine
from ..itsm.parse import DEFAULT_LABELS, parse_html
from ..models import AuditLog, User
from ..security import get_current_user, require_admin
from ..shared.text import norm_text
from ..timeutil import to_iso_utc as _iso

router = APIRouter(prefix="/api/itsm", tags=["itsm"])
KIND_LABELS = {"new_vm": "Cấp phát mới (VM provisioning)", "resize": "Nâng cấp / tinh chỉnh (VM reconfiguration)",
               "decommission": "Xoá VM (VM Decommision)", "other": "Khác"}
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
    return {**_out(r, lines), "bodyHtml": display_html(r.description), "unknownLabels": res["unknownLabels"],
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
