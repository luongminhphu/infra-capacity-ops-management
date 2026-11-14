"""AI on highly variable data (super app SA5): backup failures, import column mapping, OS
classification, VM changes. Suggestions only — applying them uses the existing APIs."""
import datetime as dt
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ai_data, ai_platform
from ..database import get_db
from ..features import get_features
from ..models import ImportMapping, User
from ..security import get_current_user
from ..spend import ai as transport

router = APIRouter(prefix="/api", tags=["ai-data"])


def _feature(db: Session, key: str) -> None:
    if not get_features(db).get(key, True):
        raise HTTPException(404, "Tính năng đang tắt (Cài đặt → Tính năng).")


def _ai(fn):
    try:
        return fn()
    except ai_platform.AiBlocked as exc:
        raise HTTPException(409, str(exc)) from exc
    except transport.AiError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------------------------------------------------------------- A3
@router.post("/ai/backup-analysis")
def backup_analysis(server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _feature(db, "backup")
    from . import backup as backup_router
    rep = backup_router.build_summary(db, server, days)
    return _ai(lambda: ai_data.analyze_backup(db, user, rep))


# ---------------------------------------------------------------- A4
def _target(v: Any) -> str:
    if v not in ai_data.FIELDS:
        raise HTTPException(400, "Loại file phải là hosts hoặc vms.")
    return v


def _headers(v: Any):
    try:
        return ai_data.clean_headers(v)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/import-mappings/lookup")
def mapping_lookup(data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Saved mapping for this file layout (same set of column names), if any, plus the field list."""
    target, headers = _target(data.get("target")), _headers(data.get("headers"))
    row = db.query(ImportMapping).filter(ImportMapping.target == target, ImportMapping.signature == ai_data.signature(headers)).first()
    saved = None
    if row:
        mp = ai_data.clean_mapping(target, json.loads(row.mapping_json), headers)
        if mp:
            saved = {**ai_data.mapping_out(row), "mapping": mp}
    return {"fields": ai_data.fields_out(target), "saved": saved}


@router.post("/ai/column-mapping")
def column_mapping(data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target, headers = _target(data.get("target")), _headers(data.get("headers"))
    return _ai(lambda: ai_data.suggest_mapping(db, user, target, headers, data.get("samples")))


@router.put("/import-mappings")
def mapping_save(data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Store the mapping the user confirmed, keyed by the layout's header signature."""
    target, headers = _target(data.get("target")), _headers(data.get("headers"))
    mp = ai_data.clean_mapping(target, data.get("mapping"), headers)
    missing = [ai_data.FIELDS[target][f][0] for f in ai_data.REQUIRED[target] if f not in mp]
    if missing:
        raise HTTPException(400, "Thiếu cột bắt buộc: " + ", ".join(missing) + ".")
    source = "ai" if data.get("source") == "ai" else "manual"
    sig = ai_data.signature(headers)
    row = db.query(ImportMapping).filter(ImportMapping.target == target, ImportMapping.signature == sig).first()
    now = dt.datetime.utcnow()
    if not row:
        if db.query(ImportMapping).count() >= ai_data.MAX_MAPPINGS:
            raise HTTPException(409, f"Đã lưu tối đa {ai_data.MAX_MAPPINGS} cách ánh xạ — xoá bớt trước khi lưu thêm.")
        row = ImportMapping(target=target, signature=sig, created_by=user.username, created_at=now, uses=0)
        db.add(row)
    row.headers_json, row.mapping_json, row.source = json.dumps(headers, ensure_ascii=False), json.dumps(mp), source
    row.updated_by, row.updated_at = user.username, now
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Cách ánh xạ này vừa được người khác lưu — tải lại rồi thử lại.") from None
    return ai_data.mapping_out(row)


@router.post("/import-mappings/{mapping_id}/used")
def mapping_used(mapping_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(ImportMapping).filter(ImportMapping.id == mapping_id).update({ImportMapping.uses: ImportMapping.uses + 1})
    db.commit()
    if not n:
        raise HTTPException(404, "Không tìm thấy cách ánh xạ.")
    return {"ok": True}


# ---------------------------------------------------------------- A5
@router.post("/ai/os-classify")
def os_classify(vcenter: Optional[str] = Query(default="all"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # The OS page (Tài sản → Hệ điều hành) is not part of the License module: no feature gate.
    from . import licensing as lic
    _, classified, _ = lic._load_inventory(db, vcenter)
    return _ai(lambda: ai_data.classify_os(db, user, classified))


# ---------------------------------------------------------------- A6
@router.post("/ai/vm-changes")
def vm_changes(vcenter: Optional[str] = Query(default="all"), months: int = Query(default=12),
               clusters: Optional[str] = Query(default=None), hosts: Optional[str] = Query(default=None),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from .. import history
    from .dashboard import _scope_keys, _split_csv
    ch = history.get_vm_changes(db, _scope_keys(vcenter), months, clusters=_split_csv(clusters), hosts=_split_csv(hosts))
    return _ai(lambda: ai_data.summarize_changes(db, user, ch))
