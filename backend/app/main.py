import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import hashlib

from .collectors.scheduler import scheduler_loop
from .config import get_settings
from .database import SessionLocal, engine, ensure_schema
from . import mcp_server, observability
from .routers import admin, ai_admin, ai_data as ai_data_router, report_archive as report_archive_router, bom as bom_router, appconfig, entities, home, auth, sources as sources_router, backup, compliance, dashboard, data, export, imports, licensing, reports, settings as settings_router, sizing, spend, spend_admin, spend_capex, spend_contracts, spend_payments, spend_proposals, spend_quotes, spend_renovations, spend_reports, vcenters
from .spend import reminders as spend_reminders, service as spend_service
from .security import ensure_bootstrap_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("vco.main")
observability.install(engine)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

_scheduler_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
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

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(backup.router)
app.include_router(admin.router)
app.include_router(appconfig.router)
app.include_router(sources_router.router)
app.include_router(spend.router)
app.include_router(spend_admin.router)
app.include_router(spend_capex.router)
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
app.include_router(bom_router.router)
app.include_router(mcp_server.router)
# Outermost: measures every /api and /mcp request (route template, latency, DB time).
app.add_middleware(observability.TimingMiddleware)
from .report_archive import ArchiveMiddleware  # noqa: E402
app.add_middleware(ArchiveMiddleware)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Infra Capacity Ops Management"}


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
