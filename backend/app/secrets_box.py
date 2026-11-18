"""Encryption of secrets stored in the database (data source passwords / API keys, SMTP password,
Discord webhooks, AI API key).

Key: SECRETS_KEY (any long random string, e.g. `openssl rand -hex 32`, kept in the K8s Secret).
When it is not set, a key is derived from JWT_SECRET so the feature still works, and the UI
asks the admin to set SECRETS_KEY — changing JWT_SECRET would otherwise make saved secrets
unreadable (they then have to be typed again; nothing else breaks).

Key change without retyping: new values are always encrypted with the current key; reading also tries
the previous keys — the JWT_SECRET-derived one (secrets saved before SECRETS_KEY was set) and the
comma-separated SECRETS_KEY_PREVIOUS (to replace SECRETS_KEY itself). `rotate()` (run at start-up)
re-encrypts every stored secret with the current key, so the previous key can be removed afterwards.
"""
import base64
import hashlib
import logging
from typing import Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

PREFIX = "f1:"
logger = logging.getLogger(__name__)


class SecretUnreadable(Exception):
    """Saved with another key — the secret has to be entered again."""


def _fernet_of(material: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()))


def _materials() -> List[str]:
    """Current key first, then the keys still accepted for reading."""
    s = get_settings()
    current = (s.secrets_key or "").strip()
    legacy = "jwt:" + s.jwt_secret
    if not current:
        return [legacy]
    previous = [k.strip() for k in (s.secrets_key_previous or "").split(",") if k.strip() and k.strip() != current]
    return [current, *previous, legacy]


def _fernet() -> Fernet:
    return _fernet_of(_materials()[0])


def dedicated_key() -> bool:
    return bool((get_settings().secrets_key or "").strip())


def encrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def _decrypt(token: str) -> Tuple[str, bool]:
    """(plain text, encrypted with the current key)."""
    if not token.startswith(PREFIX):
        raise SecretUnreadable("unknown format")
    body = token[len(PREFIX):].encode()
    for i, material in enumerate(_materials()):
        try:
            return _fernet_of(material).decrypt(body).decode(), i == 0
        except InvalidToken:
            continue
    raise SecretUnreadable("wrong key")


def decrypt(token: Optional[str]) -> str:
    if not token:
        return ""
    return _decrypt(token)[0]


def _columns():
    """Every encrypted column (model, attribute). A new *_enc column must be listed here."""
    from .models import DataSource
    from .spend.models import SpendAiConfig, SpendNotifyChannel, SpendSmtpConfig
    return [(DataSource, "password_enc"), (DataSource, "api_key_enc"), (SpendNotifyChannel, "webhook_enc"),
            (SpendSmtpConfig, "password_enc"), (SpendAiConfig, "api_key_enc")]


def rotate(db) -> Dict[str, int]:
    """Re-encrypt with the current key every secret readable only with a previous key. Idempotent; a
    secret no key opens is left as it is (the UI asks to type it again). Caller commits."""
    out = {"rotated": 0, "current": 0, "unreadable": 0}
    for model, attr in _columns():
        col = getattr(model, attr)
        for row in db.query(model).filter(col.isnot(None)):
            token = getattr(row, attr)
            if not token:
                continue
            try:
                plain, current = _decrypt(token)
            except SecretUnreadable:
                out["unreadable"] += 1
                continue
            if current:
                out["current"] += 1
            else:
                setattr(row, attr, encrypt(plain))
                out["rotated"] += 1
    return out


def status(db) -> Dict[str, int]:
    """How many stored secrets use the current key, a previous key, or none (no change made)."""
    out = {"current": 0, "previous": 0, "unreadable": 0}
    for model, attr in _columns():
        col = getattr(model, attr)
        for (token,) in db.query(col).filter(col.isnot(None)):
            if not token:
                continue
            try:
                out["current" if _decrypt(token)[1] else "previous"] += 1
            except SecretUnreadable:
                out["unreadable"] += 1
    return out


def rotate_at_startup() -> None:
    """Called once per pod start. Only does something once SECRETS_KEY (or a new one) is set."""
    from .database import SessionLocal
    if not dedicated_key():
        return
    db = SessionLocal()
    try:
        res = rotate(db)
        db.commit()
        if res["rotated"] or res["unreadable"]:
            logger.info("Secrets re-encrypted with SECRETS_KEY: %(rotated)s; still unreadable: %(unreadable)s", res)
    except Exception:  # noqa: BLE001 — never block the start-up; the previous keys keep working for reading
        db.rollback()
        logger.exception("Secret re-encryption failed")
    finally:
        db.close()
