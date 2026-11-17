"""MCP server settings: edited in Settings → AI (admin), stored in app_settings as mcp.<key>.

A value saved in the UI wins; a key never saved falls back to the environment (ConfigMap ico-config:
MCP_ENABLED, MCP_TOKEN_MAX_DAYS, MCP_MASK_HOSTS, MCP_ALLOWED_ORIGINS, MCP_RATE_PER_MINUTE), so an existing
deployment behaves the same until an admin saves. Saved rows are cached per process for CACHE_S seconds:
every pod picks a change up within that time without a redeploy.
"""
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from .config import get_settings
from .models import AppSetting

CACHE_S = 15
PREFIX = "mcp."
# key → (settings attribute, kind, min, max)
FIELDS: Dict[str, Tuple[str, str, int, int]] = {
    "enabled": ("mcp_enabled", "bool", 0, 1),
    "tokenMaxDays": ("mcp_token_max_days", "int", 1, 365),
    "maskHosts": ("mcp_mask_hosts", "bool", 0, 1),
    "ratePerMinute": ("mcp_rate_per_minute", "int", 10, 1000),
    "allowedOrigins": ("mcp_allowed_origins", "origins", 0, 0),
}
_ORIGIN = re.compile(r"^https?://[a-z0-9.-]+(:\d{1,5})?$", re.I)
MAX_ORIGINS = 10
LABELS = {"enabled": "Bật MCP", "tokenMaxDays": "Hạn token tối đa", "maskHosts": "Che host / IP",
          "ratePerMinute": "Giới hạn yêu cầu / phút", "allowedOrigins": "Origin"}


@dataclass(frozen=True)
class McpConfig:
    enabled: bool
    token_max_days: int
    mask_hosts: bool
    rate_per_minute: int
    allowed_origins: Tuple[str, ...]
    saved: Tuple[str, ...]          # keys whose value comes from the UI (the others: environment)


_lock = threading.Lock()
_rows: Optional[Tuple[float, Dict[str, str]]] = None


def invalidate() -> None:
    global _rows
    with _lock:
        _rows = None


def _read_rows(db: Optional[Session]) -> Dict[str, str]:
    global _rows
    now = time.monotonic()
    with _lock:
        if _rows is not None and now - _rows[0] < CACHE_S:
            return _rows[1]
    try:
        if db is not None:
            rows = {r.key[len(PREFIX):]: r.value for r in db.query(AppSetting).filter(AppSetting.key.like(PREFIX + "%"))}
        else:
            from .database import SessionLocal
            s = SessionLocal()
            try:
                rows = {r.key[len(PREFIX):]: r.value for r in s.query(AppSetting).filter(AppSetting.key.like(PREFIX + "%"))}
            finally:
                s.close()
    except Exception:            # DB unreachable: environment values, retried at the next call
        return {}
    with _lock:
        _rows = (now, rows)
    return rows


def split_origins(text: Any) -> Tuple[str, ...]:
    return tuple(o.strip().rstrip("/") for o in re.split(r"[,\s]+", str(text or "")) if o.strip())


def _env(key: str) -> Any:
    attr, kind, _lo, _hi = FIELDS[key]
    v = getattr(get_settings(), attr)
    return split_origins(v) if kind == "origins" else v


def _parse(key: str, raw: str) -> Any:
    _attr, kind, lo, hi = FIELDS[key]
    if kind == "bool":
        return raw == "true"
    if kind == "int":
        return max(lo, min(hi, int(raw)))
    return split_origins(raw)


def current(db: Optional[Session] = None) -> McpConfig:
    rows = _read_rows(db)
    vals: Dict[str, Any] = {}
    saved = []
    for key in FIELDS:
        if key in rows:
            try:
                vals[key] = _parse(key, rows[key])
                saved.append(key)
                continue
            except (TypeError, ValueError):
                pass
        vals[key] = _env(key)
    return McpConfig(enabled=bool(vals["enabled"]), token_max_days=int(vals["tokenMaxDays"]), mask_hosts=bool(vals["maskHosts"]),
                     rate_per_minute=int(vals["ratePerMinute"]), allowed_origins=tuple(vals["allowedOrigins"]), saved=tuple(saved))


def defaults() -> Dict[str, Any]:
    """The environment values, shown next to each field ("mặc định từ ConfigMap")."""
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in ((k, _env(k)) for k in FIELDS)}


def validate(body: Dict[str, Any]) -> Dict[str, str]:
    """Values from the form → strings to store. Raises ValueError with a Vietnamese sentence."""
    out: Dict[str, str] = {}
    for key, (_attr, kind, lo, hi) in FIELDS.items():
        if key not in body:
            continue
        v = body[key]
        if kind == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{LABELS[key]}: phải là bật / tắt.")
            out[key] = "true" if v else "false"
        elif kind == "int":
            if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).strip().isdigit():
                raise ValueError(f"{LABELS[key]} phải là số nguyên từ {lo} đến {hi}.")
            n = int(str(v).strip())
            if not lo <= n <= hi:
                raise ValueError(f"{LABELS[key]} phải từ {lo} đến {hi}.")
            out[key] = str(n)
        else:
            items = split_origins(v if isinstance(v, str) else ",".join(map(str, v or [])))
            if len(items) > MAX_ORIGINS:
                raise ValueError(f"Tối đa {MAX_ORIGINS} origin.")
            bad = [o for o in items if not _ORIGIN.match(o)]
            if bad:
                raise ValueError(f"Origin không hợp lệ: {bad[0][:60]} — dạng https://ten-may[:cổng], không dấu *, không đường dẫn.")
            text = ",".join(items)
            if len(text) > 255:
                raise ValueError("Danh sách origin quá dài.")
            out[key] = text
    return out


def save(db: Session, values: Dict[str, str]) -> None:
    for key, value in values.items():
        row = db.get(AppSetting, PREFIX + key)
        if row is None:
            db.add(AppSetting(key=PREFIX + key, value=value))
        else:
            row.value = value
    invalidate()


def reset(db: Session) -> None:
    """Back to the environment values."""
    db.query(AppSetting).filter(AppSetting.key.like(PREFIX + "%")).delete(synchronize_session=False)
    invalidate()
