"""Admin-only: personal MCP tokens and the MCP audit trail."""
import datetime as dt
import json
import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import mcp_config
from ..database import get_db
from ..mcp_server import TOKEN_PREFIX, hash_token
from ..models import ApiToken, AuditLog, User
from ..security import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    days: int = Field(default=90, ge=1, le=365)


def _iso(t: Optional[dt.datetime]) -> Optional[str]:
    return t.replace(microsecond=0).isoformat() + "Z" if t else None


def _audit(db: Session, user: User, action: str, request: Request, **detail) -> None:
    db.add(AuditLog(username=user.username, action=action, detail=json.dumps(detail, default=str),
                    client_ip=(request.client.host if request.client else None)))


@router.get("/mcp")
def mcp_info(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = mcp_config.current(db)
    rows = db.query(ApiToken, User).join(User, User.id == ApiToken.user_id).order_by(ApiToken.created_at.desc()).limit(200).all()
    now = dt.datetime.utcnow()
    return {
        "enabled": cfg.enabled, "maxDays": cfg.token_max_days, "maskHosts": cfg.mask_hosts,
        "config": _config_out(cfg), "defaults": mcp_config.defaults(), "saved": list(cfg.saved),
        "tokens": [{"id": t.id, "name": t.name, "prefix": t.prefix, "owner": u.username, "mine": u.id == user.id,
                    "createdAt": _iso(t.created_at), "expiresAt": _iso(t.expires_at), "lastUsedAt": _iso(t.last_used_at),
                    "status": "revoked" if t.revoked_at else ("expired" if t.expires_at < now else "active")}
                   for t, u in rows],
    }


def _config_out(cfg: mcp_config.McpConfig) -> Dict[str, Any]:
    return {"enabled": cfg.enabled, "tokenMaxDays": cfg.token_max_days, "maskHosts": cfg.mask_hosts,
            "ratePerMinute": cfg.rate_per_minute, "allowedOrigins": list(cfg.allowed_origins)}


@router.put("/mcp/config")
def save_config(request: Request, body: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Settings → AI / MCP server. Saved values win over the ConfigMap; every pod reads them within
    mcp_config.CACHE_S seconds. Turning host masking off needs confirmMaskOff (host names reach the AI)."""
    try:
        values = mcp_config.validate(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    before = mcp_config.current(db)
    if values.get("maskHosts") == "false" and before.mask_hosts and body.get("confirmMaskOff") is not True:
        raise HTTPException(400, "Tắt che host/IP sẽ gửi tên máy chủ / IP nội bộ cho AI — cần xác nhận.")
    mcp_config.save(db, values)
    db.flush()
    after = mcp_config.current(db)
    _audit(db, user, "mcp.config", request, before=_config_out(before), after=_config_out(after))
    db.commit()
    mcp_config.invalidate()
    return {**_config_out(mcp_config.current(db)), "saved": list(mcp_config.current(db).saved)}


@router.delete("/mcp/config")
def reset_config(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Back to the ConfigMap values."""
    before = mcp_config.current(db)
    mcp_config.reset(db)
    _audit(db, user, "mcp.config.reset", request, before=_config_out(before))
    db.commit()
    mcp_config.invalidate()
    return _config_out(mcp_config.current(db))


@router.post("/mcp/tokens")
def create_token(body: TokenCreate, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = mcp_config.current(db)
    if not cfg.enabled:
        raise HTTPException(400, "MCP server đang tắt — bật ở Cài đặt → AI / MCP server.")
    days = min(body.days, cfg.token_max_days)
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    tok = ApiToken(user_id=user.id, name=body.name.strip(), token_hash=hash_token(raw), prefix=raw[:14], scope="mcp",
                   expires_at=dt.datetime.utcnow() + dt.timedelta(days=days))
    db.add(tok)
    _audit(db, user, "mcp.token.create", request, name=tok.name, prefix=tok.prefix, days=days)
    db.commit()
    # The token is shown once; only its SHA-256 is stored.
    return {"id": tok.id, "token": raw, "expiresAt": _iso(tok.expires_at)}


@router.delete("/mcp/tokens/{token_id}")
def revoke_token(token_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    tok = db.get(ApiToken, token_id)
    if not tok:
        raise HTTPException(404, "Không tìm thấy token")
    if not tok.revoked_at:
        tok.revoked_at = dt.datetime.utcnow()
        _audit(db, user, "mcp.token.revoke", request, prefix=tok.prefix)
        db.commit()
    return {"ok": True}


@router.get("/mcp/audit")
def audit(limit: int = 100, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (db.query(AuditLog).filter(AuditLog.action.like("mcp.%")).order_by(AuditLog.at.desc())
            .limit(max(1, min(limit, 500))).all())
    return [{"at": _iso(r.at), "user": r.username, "action": r.action, "detail": json.loads(r.detail or "{}"),
             "ip": r.client_ip} for r in rows]
