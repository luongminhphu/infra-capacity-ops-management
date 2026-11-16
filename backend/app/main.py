import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import hashlib

from .collectors.scheduler import scheduler_loop
from .config import get_settings, jwt_secret_problem
from .database import SessionLocal, engine, ensure_schema
from . import mcp_server, observability
from .routers import admin, ai_admin, ai_data as ai_data_router, report_archive as report_archive_router, users_admin as users_admin_router, bom as bom_router, exports as exports_router, appconfig, entities, home, auth, sources as sources_router, backup, compliance, dashboard, data, export, imports, licensing, reports, settings as settings_router, sizing, spend, spend_admin, spend_capex, spend_contracts, spend_import, spend_plans, spend_payments, spend_proposals, spend_quotes, spend_renovations, spend_reports, vcenters
from .spend import reminders as spend_reminders, service as spend_service
from .security import ensure_bootstrap_admin
from .shared.stored_text import InvalidStoredValue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s")
for _h in logging.getLogger().handlers:   # request id on every line (audit OBS-02); "-" outside a request
    _h.addFilter(observability.RequestIdLogFilter())
logger = logging.getLogger("vco.main")
observability.install(engine)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

_scheduler_task = None


def check_startup_secrets(settings) -> None:
    """Refuse to start with a guessable JWT_SECRET (forged admin cookies, readable stored
    secrets); warn when SECRETS_KEY is unset (stored secrets then derive from JWT_SECRET)."""
    problem = jwt_secret_problem(settings.jwt_secret)
    if problem:
        logger.critical("Từ chối khởi động: JWT_SECRET không an toàn — đặt chuỗi ngẫu nhiên dài "
                        "(openssl rand -hex 32). / Refusing to start: %s.", problem)
        raise RuntimeError(f"Unsafe JWT_SECRET: {problem}. Set a long random value (openssl rand -hex 32).")
    if not settings.secure_cookies():
        logger.warning("Cookie phiên không có cờ Secure (COOKIE_SECURE=false và PUBLIC_URL không phải https): "
                       "chỉ chấp nhận khi chạy thử. / Session cookie is not Secure.")
    if not (settings.secrets_key or "").strip():
        logger.warning("Chưa đặt SECRETS_KEY: mật khẩu nguồn lưu trong DB đang mã hoá bằng khoá suy ra từ "
                       "JWT_SECRET. / SECRETS_KEY is not set; stored secrets use a key derived from JWT_SECRET.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    check_startup_secrets(settings)
    ensure_schema()

    db = SessionLocal()
    try:
        ensure_bootstrap_admin(db)
        spend_service.ensure_defaults(db)
    finally:
        db.close()

    global _scheduler_task
    reminder_task = None
    if settings.enable_scheduler:
        _scheduler_task = asyncio.create_task(scheduler_loop(settings))
        # Daily expiry reminders of the Chi phí & Hợp đồng module (own loop: polling interval is unrelated).
        reminder_task = asyncio.create_task(spend_reminders.reminder_loop())
        logger.info("Background vCenter/NetBackup poll scheduler started (interval=%s min)", settings.poll_interval_minutes)
    else:
        logger.info("Background scheduler disabled (ENABLE_SCHEDULER=false)")

    flush_task = asyncio.create_task(observability.flush_loop())

    yield

    flush_task.cancel()
    if reminder_task:
        reminder_task.cancel()
    if _scheduler_task:
        _scheduler_task.cancel()
    observability.flush(force_all_buckets=True)


app = FastAPI(title="Infra Capacity Ops Management API", lifespan=lifespan)


@app.exception_handler(InvalidStoredValue)
async def _invalid_stored_value(request: Request, exc: InvalidStoredValue):
    """SCH-10: a JSON / CSV text column got a value of the wrong shape — refuse with 422, nothing saved."""
    logger.warning("Rejected write on %s %s: %s", request.method, request.url.path, exc.label)
    return JSONResponse(status_code=422, content={"detail": exc.message})

settings = get_settings()


def cors_options(origins):
    """CORS kwargs. Same-origin frontend needs none; a wildcard never goes with credentials
    (Starlette would echo any Origin with Allow-Credentials, exposing the session cookie)."""
    allow_credentials = True
    if "*" in origins:
        logger.warning("CORS_ORIGINS='*': tắt allow_credentials — hãy khai báo danh sách origin cụ thể. "
                       "/ Wildcard CORS origin configured; credentials disabled.")
        allow_credentials = False
    return {"allow_origins": origins, "allow_credentials": allow_credentials,
            "allow_methods": ["*"], "allow_headers": ["*"]}


_cors_origins = settings.cors_origin_list()
if _cors_origins:
    app.add_middleware(CORSMiddleware, **cors_options(_cors_origins))

app.include_router(auth.router)
app.include_router(vcenters.router)
app.include_router(dashboard.router)
app.include_router(data.router)
app.include_router(compliance.router)
app.include_router(imports.router)
app.include_router(export.router)
app.include_router(reports.router)
app.include_router(settings_router.router)
app.include_router(sizing.router)
app.include_router(licensing.router)
app.include_router(licensing.license_router)
app.include_router(backup.router)
app.include_router(admin.router)
app.include_router(appconfig.router)
app.include_router(sources_router.router)
app.include_router(spend.router)
app.include_router(spend_admin.router)
app.include_router(spend_capex.router)
app.include_router(spend_plans.router)
app.include_router(spend_import.router)
app.include_router(spend_reports.router)
app.include_router(spend_contracts.router)
app.include_router(spend_proposals.router)
app.include_router(spend_payments.router)
app.include_router(spend_quotes.router)
app.include_router(spend_renovations.router)
app.include_router(home.router)
app.include_router(entities.router)
app.include_router(ai_admin.router)
app.include_router(ai_data_router.router)
app.include_router(report_archive_router.router)
app.include_router(users_admin_router.router)
app.include_router(bom_router.router)
app.include_router(exports_router.router)
app.include_router(mcp_server.router)
# Outermost: measures every /api and /mcp request (route template, latency, DB time).
app.add_middleware(observability.TimingMiddleware)
from .report_archive import ArchiveMiddleware  # noqa: E402
app.add_middleware(ArchiveMiddleware)
# Compress JSON / HTML / JS / CSS above 1 KB (audit API-01: /data/latest 150 KB -> 15 KB).
# Inside the archive middleware's caller chain so archived PDFs are stored uncompressed.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
from .http_guard import BodyLimitMiddleware, SecurityHeadersMiddleware  # noqa: E402
# Added last = outermost: oversize bodies are refused before any route reads them, and every
# response (API, static, errors) carries the security headers.
app.add_middleware(SecurityHeadersMiddleware, https=lambda: get_settings().secure_cookies())
app.add_middleware(BodyLimitMiddleware, max_bytes=lambda: max(1, get_settings().max_request_mb) * 1024 * 1024)


@app.get("/api/health")
async def health():
    """Liveness: the process answers. No I/O and `async` (not the threadpool), so a busy
    threadpool or a database outage never gets the pod restarted."""
    return {"status": "ok", "app": "Infra Capacity Ops Management"}


@app.get("/api/health/ready")
def health_ready():
    """Readiness: the database answers SELECT 1 and, when the scheduler is on, its loop ticked
    recently. 503 with a short reason otherwise (no hostnames, no error text)."""
    from sqlalchemy import text
    from .collectors.scheduler import heartbeat_status

    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001 — reported as not ready; detail goes to the log only
        logger.warning("Readiness: database check failed (%s)", type(exc).__name__, exc_info=True)
        checks["database"] = {"ok": False, "reason": "database_unavailable"}
    s = get_settings()
    if s.enable_scheduler:
        hb = heartbeat_status(s)
        if _scheduler_task is not None and _scheduler_task.done():
            hb = {**hb, "ok": False, "reason": "scheduler_stopped"}
        checks["scheduler"] = hb
    else:
        checks["scheduler"] = {"ok": True, "disabled": True}
    ready = all(c["ok"] for c in checks.values())
    if not ready:
        logger.warning("Readiness failed: %s", ", ".join(f"{k}={c.get('reason')}" for k, c in checks.items() if not c["ok"]))
    return JSONResponse({"status": "ok" if ready else "not_ready", "checks": checks}, status_code=200 if ready else 503)


def _asset_version() -> str:
    """Short fingerprint of the frontend JS/CSS. index.html references assets
    as `js/core.js?v=<fingerprint>` so browsers fetch new files after every
    deploy instead of reusing a heuristically cached copy."""
    h = hashlib.sha1()
    for f in sorted(list(FRONTEND_DIR.glob("js/**/*.js")) + list(FRONTEND_DIR.glob("css/*.css"))):
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


@app.middleware("http")
async def _static_no_cache(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        # Always revalidate static files (ETag keeps it cheap); versioned
        # query strings make the actual cache-busting deterministic.
        response.headers["Cache-Control"] = "no-cache"
    return response


if FRONTEND_DIR.exists():
    _INDEX_FILE = FRONTEND_DIR / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index_html():
        html = _INDEX_FILE.read_text(encoding="utf-8").replace("__ASSET_V__", _asset_version())
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
