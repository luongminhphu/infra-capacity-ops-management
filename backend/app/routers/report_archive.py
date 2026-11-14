"""Tài liệu → Kho báo cáo: list / download / delete archived PDF reports (super app SA6)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, defer

from .. import report_archive as ra
from ..database import get_db
from ..features import get_features
from ..models import ReportArchive, User
from ..security import get_current_user, is_admin

router = APIRouter(prefix="/api/report-archive", tags=["report-archive"])


@router.get("")
def list_archive(kind: Optional[str] = Query(default=None, max_length=32), mine: bool = False, limit: int = Query(default=200, ge=1, le=500),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(ReportArchive).options(defer(ReportArchive.content)).order_by(ReportArchive.created_at.desc(), ReportArchive.id.desc())
    if kind:
        q = q.filter(ReportArchive.kind == kind)
    if mine:
        q = q.filter(ReportArchive.created_by == user.username)
    feats = get_features(db)
    rows = [r for r in q.limit(ra.MAX_ROWS) if ra.can_read(r, user, feats)][:limit]
    return {"items": [ra.out(r) for r in rows], "kinds": ra.KIND_LABELS, "maxRows": ra.MAX_ROWS, "retentionDays": ra.RETENTION_DAYS}


def _row(db: Session, user: User, archive_id: int) -> ReportArchive:
    r = db.get(ReportArchive, archive_id)
    if not r or not ra.can_read(r, user, get_features(db)):
        raise HTTPException(404, "Không tìm thấy báo cáo trong kho.")
    return r


@router.get("/{archive_id}/file")
def download(archive_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from urllib.parse import quote
    r = _row(db, user, archive_id)
    ascii_name = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in r.filename)
    return Response(r.content, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(r.filename)}",
        "X-Content-Type-Options": "nosniff"})


@router.delete("/{archive_id}")
def delete(archive_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _row(db, user, archive_id)
    if r.created_by != user.username and not is_admin(user):
        raise HTTPException(403, "Chỉ người lưu hoặc admin xoá được báo cáo này.")
    db.delete(r)
    db.commit()
    return {"ok": True}
