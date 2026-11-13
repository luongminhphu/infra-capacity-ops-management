"""Admin-only: personal MCP tokens and the MCP audit trail."""
import datetime as dt
import json
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
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
    s = get_settings()
    rows = db.query(ApiToken, User).join(User, User.id == ApiToken.user_id).order_by(ApiToken.created_at.desc()).limit(200).all()
    now = dt.datetime.utcnow()
    return {
        "enabled": s.mcp_enabled, "maxDays": s.mcp_token_max_days, "maskHosts": s.mcp_mask_hosts,
        "tokens": [{"id": t.id, "name": t.name, "prefix": t.prefix, "owner": u.username, "mine": u.id == user.id,
                    "createdAt": _iso(t.created_at), "expiresAt": _iso(t.expires_at), "lastUsedAt": _iso(t.last_used_at),
                    "status": "revoked" if t.revoked_at else ("expired" if t.expires_at < now else "active")}
                   for t, u in rows],
    }


@router.post("/mcp/tokens")
def create_token(body: TokenCreate, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    if not s.mcp_enabled:
        raise HTTPException(400, "MCP server đang tắt (MCP_ENABLED=false)")
    days = min(body.days, s.mcp_token_max_days)
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
