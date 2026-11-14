"""Encryption of secrets stored in the database (data source passwords / API keys).

Key: SECRETS_KEY (any long random string, e.g. `openssl rand -hex 32`, kept in the K8s Secret).
When it is not set, a key is derived from JWT_SECRET so the feature still works, and the UI
asks the admin to set SECRETS_KEY — changing JWT_SECRET would otherwise make saved secrets
unreadable (they then have to be typed again; nothing else breaks).
"""
import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

PREFIX = "f1:"


class SecretUnreadable(Exception):
    """Saved with another key — the secret has to be entered again."""


def _fernet() -> Fernet:
    s = get_settings()
    material = (s.secrets_key or "").strip() or ("jwt:" + s.jwt_secret)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()))


def dedicated_key() -> bool:
    return bool((get_settings().secrets_key or "").strip())


def encrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(token: Optional[str]) -> str:
    if not token:
        return ""
    if not token.startswith(PREFIX):
        raise SecretUnreadable("unknown format")
    try:
        return _fernet().decrypt(token[len(PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable("wrong key") from exc
