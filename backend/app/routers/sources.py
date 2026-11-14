"""Settings → Nguồn dữ liệu: add / edit / test / disable / delete vCenter and NetBackup servers.
Admin only. Secrets are write-only: responses say whether one is saved, never what it is."""
import datetime as dt
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import secrets_box, sources
from ..config import get_settings
from ..database import get_db
from ..models import AuditLog, DataSource, User
from ..security import require_admin

router = APIRouter(prefix="/api/admin/sources", tags=["sources"])


def _audit(db: Session, user: User, action: str, request: Request, row: DataSource, **extra) -> None:
    db.add(AuditLog(username=user.username, action=action, client_ip=request.client.host if request.client else None,
                    detail=json.dumps({"kind": row.kind, "key": row.key, "host": row.host, **extra})))


def _row(db: Session, source_id: int) -> DataSource:
    row = db.get(DataSource, source_id)
    if not row:
        raise HTTPException(404, "Không tìm thấy nguồn dữ liệu")
    return row


@router.get("")
def list_sources(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    rows = db.query(DataSource).order_by(DataSource.kind, DataSource.id).all()
    ui_keys = {(r.kind, r.key) for r in rows}
    env = [sources.public_env(c, "vcenter", ("vcenter", c.key) in ui_keys) for c in s.env_vcenters()] + \
          [sources.public_env(c, "netbackup", ("netbackup", c.key) in ui_keys) for c in s.env_netbackups()]
    return {"secretsKeyConfigured": secrets_box.dedicated_key(),
            "domainTypes": list(sources.NBU_DOMAIN_TYPES),
            "items": [sources.public(r) for r in rows] + env}


@router.post("")
def create_source(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    row = DataSource(enabled=True)
    try:
        sources.apply_input(row, data, creating=True)
    except sources.SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    if db.query(DataSource).filter_by(kind=row.kind, key=row.key).first():
        raise HTTPException(409, f"Mã {row.key} đã tồn tại.")
    row.updated_by, row.updated_at = user.username, dt.datetime.utcnow()
    db.add(row)
    db.flush()
    _audit(db, user, "source.create", request, row)
    db.commit()
    sources.invalidate_cache()
    return sources.public(row)


@router.put("/{source_id}")
def update_source(source_id: int, request: Request, data: Dict[str, Any] = Body(...),
                  user: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _row(db, source_id)
    try:
        sources.apply_input(row, data, creating=False)
    except sources.SourceError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    row.updated_by, row.updated_at = user.username, dt.datetime.utcnow()
    _audit(db, user, "source.update", request, row,
           secretChanged=bool(data.get("password") or data.get("apiKey") or data.get("clearPassword") or data.get("clearApiKey")))
    db.commit()
    sources.invalidate_cache()
    return sources.public(row)


@router.delete("/{source_id}")
def delete_source(source_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Removes the connection only; collected history stays under the same key."""
    row = _row(db, source_id)
    _audit(db, user, "source.delete", request, row)
    db.delete(row)
    db.commit()
    sources.invalidate_cache()
    return {"ok": True}


@router.post("/test")
def test_source(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """Test the values in the form (not saved). An empty password / API key uses the saved one."""
    source_id: Optional[int] = data.get("id")
    existing = _row(db, int(source_id)) if source_id else None
    probe = DataSource(kind=existing.kind if existing else data.get("kind"), key=existing.key if existing else "test",
                       password_enc=existing.password_enc if existing else None,
                       api_key_enc=existing.api_key_enc if existing else None, options=existing.options if existing else None)
    try:
        sources.apply_input(probe, {**data, "key": probe.key, "kind": probe.kind}, creating=existing is None)
        message, ok = sources.test_connection(sources.to_config(probe), probe.kind), True
    except sources.SourceError as exc:
        message, ok = str(exc), False
    if existing:
        existing.last_test_at, existing.last_test_ok, existing.last_test_message = dt.datetime.utcnow(), ok, message[:1000]
        _audit(db, user, "source.test", request, existing, ok=ok)
        db.commit()
    return {"ok": ok, "message": message}


@router.post("/import-env")
def import_env(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Copy every variable-based source (credentials included) into Settings; existing keys are skipped."""
    s = get_settings()
    added = []
    for kind, configs in (("vcenter", s.env_vcenters()), ("netbackup", s.env_netbackups())):
        for cfg in configs:
            if db.query(DataSource).filter_by(kind=kind, key=cfg.key).first():
                continue
            row = sources.row_from_env(cfg, kind, user.username)
            db.add(row)
            db.flush()
            _audit(db, user, "source.import_env", request, row)
            added.append(f"{kind}:{cfg.key}")
    db.commit()
    sources.invalidate_cache()
    return {"added": added}
