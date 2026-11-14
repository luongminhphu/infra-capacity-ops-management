"""Kho báo cáo (super app SA6): keep a copy of each PDF report the user chose to archive.

The browser sends `X-ICO-Archive: 1` with a PDF export when "Lưu vào kho" is ticked in the report
center; ArchiveMiddleware stores the 200 `application/pdf` response of that request. The endpoint
itself already authenticated the user, so a response only exists when they were allowed to export it.
Reading back keeps the same boundary: spend PDFs need a spend role, disabled modules are hidden."""
import datetime as dt
import hashlib
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, unquote, urlencode

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import ReportArchive, User

logger = logging.getLogger("vco.report_archive")

HEADER = "x-ico-archive"
MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 1000
RETENTION_DAYS = 400
KINDS = [  # (path prefix, kind, label, feature)
    ("/api/spend/renovations", "renovation", "Cải tạo văn phòng", "spend"),
    ("/api/spend/", "spend", "Ngân sách & chi phí", "spend"),
    ("/api/backup/", "backup", "Backup", "backup"),
    ("/api/licensing/", "license", "License", "license"),
    ("/api/sizing/", "sizing", "Sizing máy chủ", "sizing"),
    ("/api/reports/", "capacity", "Capacity", None),
]
KIND_LABELS = {k: lbl for _, k, lbl, _ in KINDS} | {"other": "Khác"}
KIND_FEATURE = {k: f for _, k, _, f in KINDS}
_SECRET_PARAM = re.compile(r"(token|key|secret|password|logo)", re.IGNORECASE)


def kind_of(path: str) -> str:
    return next((k for prefix, k, _, _ in KINDS if path.startswith(prefix)), "other")


def _filename(content_disposition: str, path: str) -> str:
    m = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition or "") or re.search(r'filename="?([^";]+)"?', content_disposition or "")
    name = unquote(m.group(1)) if m else path.rstrip("/").rsplit("/", 1)[-1] + ".pdf"
    name = re.sub(r"[\x00-\x1f/\\]", "_", name).strip()[:200] or "bao-cao.pdf"
    return name if name.lower().endswith(".pdf") else name + ".pdf"


def save(username: str, path: str, query: str, content_disposition: str, body: bytes, now: Optional[dt.datetime] = None) -> Optional[int]:
    """Store one archived PDF (own DB session; runs in a worker thread). Returns its id."""
    if not body.startswith(b"%PDF") or len(body) > MAX_BYTES:
        return None
    now = now or dt.datetime.utcnow()
    kind = kind_of(path)
    fname = _filename(content_disposition, path)
    params = urlencode([(k, v[:100]) for k, v in parse_qsl(query or "", keep_blank_values=False) if not _SECRET_PARAM.search(k)][:30])[:1000]
    db = SessionLocal()
    try:
        row = ReportArchive(kind=kind, scope="spend" if KIND_FEATURE.get(kind) == "spend" else "general",
                            title=f"{KIND_LABELS[kind]} — {fname[:-4]}"[:255], filename=fname, params=params or None,
                            size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), content=body,
                            created_by=username, created_at=now)
        db.add(row)
        db.flush()
        db.query(ReportArchive).filter(ReportArchive.created_at < now - dt.timedelta(days=RETENTION_DAYS)).delete(synchronize_session=False)
        keep = [i for (i,) in db.query(ReportArchive.id).order_by(ReportArchive.created_at.desc(), ReportArchive.id.desc()).limit(MAX_ROWS)]
        if len(keep) >= MAX_ROWS:
            db.query(ReportArchive).filter(ReportArchive.id.notin_(keep)).delete(synchronize_session=False)
        db.commit()
        return row.id
    except Exception:  # noqa: BLE001 — archiving must never break the download
        db.rollback()
        logger.exception("report archive failed")
        return None
    finally:
        db.close()


def can_read(row: ReportArchive, user: User, features: Dict[str, bool]) -> bool:
    from .spend.roles import has_role
    feat = KIND_FEATURE.get(row.kind)
    if feat and features.get(feat) is False:
        return False
    if row.scope == "spend":
        return has_role(user, "viewer")
    return True


def out(row: ReportArchive) -> Dict[str, Any]:
    from .timeutil import to_iso_utc
    return {"id": row.id, "kind": row.kind, "kindLabel": KIND_LABELS.get(row.kind, row.kind), "title": row.title,
            "filename": row.filename, "params": row.params, "sizeBytes": row.size_bytes, "sha256": row.sha256,
            "createdBy": row.created_by, "createdAt": to_iso_utc(row.created_at)}


class ArchiveMiddleware:
    """Pure ASGI middleware: tees the body of an opted-in PDF response, then stores it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        if headers.get(HEADER) != "1":
            return await self.app(scope, receive, send)
        state = {"pdf": False, "status": 0, "cd": "", "chunks": [], "size": 0}

        async def tee(message):
            if message["type"] == "http.response.start":
                h = {k.decode().lower(): v.decode("latin-1") for k, v in message.get("headers") or []}
                state.update(status=message["status"], pdf=h.get("content-type", "").startswith("application/pdf"),
                             cd=h.get("content-disposition", ""))
            elif message["type"] == "http.response.body" and state["pdf"] and state["status"] == 200:
                chunk = message.get("body") or b""
                state["size"] += len(chunk)
                if state["size"] <= MAX_BYTES:
                    state["chunks"].append(chunk)
                await send(message)
                if not message.get("more_body"):
                    await self._store(scope, headers, state)    # after the user has the file
                return
            await send(message)
        await self.app(scope, receive, tee)

    @staticmethod
    async def _store(scope, headers, state):
        from starlette.concurrency import run_in_threadpool

        from .security import COOKIE_NAME, decode_access_token
        if state["size"] > MAX_BYTES:
            return
        cookie = next((p.split("=", 1)[1] for p in headers.get("cookie", "").split("; ") if p.startswith(COOKIE_NAME + "=")), None)
        username = decode_access_token(cookie) if cookie else None
        if not username:
            return
        await run_in_threadpool(save, username, scope["path"], scope.get("query_string", b"").decode("latin-1"),
                                state["cd"], b"".join(state["chunks"]))
