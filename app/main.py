"""FastAPI application and web UI."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import asyncio
import urllib.parse as _urlparse

# Configure logging so our debug messages show up
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("quickly").setLevel(logging.DEBUG)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import init_db
from app.settings_manager import settings
from app.routers import inbox, leads, campaigns, test_mode
from app.routers import gmail_oauth
from app.routers import schedule as schedule_router
from app.routers import settings as settings_router
from app.routers import unibox as unibox_router
from app.routers import tracking as tracking_router
from app.jobs import run_send_job, last_send_job_run, last_send_job_sent_count
from app.unibox import queue_sync_for_all_inboxes, run_unibox_sync_job
from app import time as time_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # APScheduler renamed class; keep variable named `schedule` for legacy compatibility
    schedule = AsyncIOScheduler()
    interval_minutes = max(1, settings.queue_check_interval_minutes)
    unibox_interval_minutes = 5

    from app.database import AsyncSessionLocal
    from app.app_settings import get_gmail_sync_config

    async with AsyncSessionLocal() as db:
        try:
            sync_cfg = await get_gmail_sync_config(db)
            unibox_interval_minutes = max(1, int(sync_cfg.get("sync_interval_minutes", 5)))
        except Exception:
            logging.getLogger("quickly").exception("Failed loading unibox sync config; using default interval")
            unibox_interval_minutes = 5

    schedule.add_job(
        run_send_job,
        "cron",
        minute=f"*/{interval_minutes}",
        second=0,
        id="send_queue",
    )
    schedule.add_job(
        run_unibox_sync_job,
        "cron",
        minute=f"*/{unibox_interval_minutes}",
        second=10,
        id="unibox_sync",
    )
    schedule.start()
    app.state.schedule = schedule

    # perform a global recalculation on startup to ensure the queue reflects
    # any configuration changes that may have occurred while the server was down.
    # this addresses the "server starts/restarts" trigger from the docs.
    # we don't want to block startup so launch a background task with a brief
    # delay; failures are logged but ignored.
    from httpx import AsyncClient, ASGITransport

    async def kickoff():
        # give the server a moment to finish starting
        await asyncio.sleep(1)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/schedule/recalculate-all")
                resp.raise_for_status()
        except Exception as e:
            logging.getLogger("quickly.routes").error(
                "startup recalculation failed: %s", e
            )

    # Track startup tasks so we can cancel them cleanly on shutdown.
    # Without this, rapid reloads leave open DB transactions which cause
    # "unexpected EOF on client connection with an open transaction" errors.
    startup_tasks = [
        asyncio.create_task(kickoff()),
        asyncio.create_task(queue_sync_for_all_inboxes(reason="startup")),
    ]

    yield

    # Cancel any still-running startup tasks before the event loop closes.
    for task in startup_tasks:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    schedule.shutdown()


app = FastAPI(title="Quickly", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Security middleware (CSP, HSTS, X-Frame-Options, …)
# ---------------------------------------------------------------------------
from app.security import SecurityHeadersMiddleware  # noqa: E402

app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# CORS – configurable from environment, defaults to localhost dev setup.
# ---------------------------------------------------------------------------
import os as _os
from fastapi.middleware.cors import CORSMiddleware

_cors_origins_str = _os.getenv("CORS_ORIGINS", "http://localhost:5173")
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

# ---------------------------------------------------------------------------
# Rate limiting – 200 req/min per IP by default on all routes
# ---------------------------------------------------------------------------
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Auth router (public endpoints: login, register, setup-status, refresh)
# ---------------------------------------------------------------------------
from app.routers import auth as auth_router
app.include_router(auth_router.router)

# ---------------------------------------------------------------------------
# Protected routers – all require authentication
# ---------------------------------------------------------------------------
from app.auth import get_current_user as _auth_dep

_auth_deps = [Depends(_auth_dep)]

inbox.router.dependencies = _auth_deps
leads.router.dependencies = _auth_deps
campaigns.router.dependencies = _auth_deps
test_mode.router.dependencies = _auth_deps
gmail_oauth.router.dependencies = _auth_deps
schedule_router.router.dependencies = _auth_deps
settings_router.router.dependencies = _auth_deps
unibox_router.router.dependencies = _auth_deps
# tracking_router has public endpoints (open pixel, click redirect, unsubscribe)
# so it does NOT get global auth — individual endpoints handle auth internally.

app.include_router(inbox.router)
app.include_router(leads.router)
app.include_router(campaigns.router)
app.include_router(test_mode.router)
app.include_router(gmail_oauth.router)
app.include_router(schedule_router.router)
app.include_router(settings_router.router)
app.include_router(unibox_router.router)
app.include_router(tracking_router.router)

# ---------------------------------------------------------------------------
# Static assets and SPA fallback
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# Serve compiled frontend assets.  Mounted at /assets so API routes are never
# shadowed.  The SPA entrypoint and catch-all are handled by explicit routes
# below so that /api/* is always matched by the routers first.
app.mount(
    "/assets",
    StaticFiles(directory=str(BASE_DIR / "frontend" / "dist" / "assets")),
    name="assets",
)

if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.get("/api/status")
async def api_status(request: Request, user=Depends(_auth_dep)):
    """Schedule and send-job status so you can verify the worker is running."""
    import os
    schedule = getattr(request.app.state, "schedule", None)
    job = schedule.get_job("send_queue") if (schedule and schedule.running) else None
    next_run = job.next_run_time.isoformat() if (job and getattr(job, "next_run_time", None)) else None
    return {
        "schedule_running": schedule is not None and schedule.running,
        "queue_check_interval_minutes": settings.queue_check_interval_minutes,
        "last_send_job_run": (last_send_job_run.isoformat() + "Z") if last_send_job_run else None,
        "last_send_job_sent_count": last_send_job_sent_count,
        "next_send_job_run": next_run,
        # include a server timestamp so the frontend can display true server time
        # (useful during development when the UI and backend may be running on
        # different machines or when the clock is offset via time_offset_days).
        # The 'Z' suffix lets browsers treat the value as UTC automatically.
        "server_time": time_provider.now().isoformat() + "Z",
        "test_mode": settings.test_mode,
        "app_mode": os.environ.get("QUICKLY_MODE", "development").lower(),
    }


@app.get("/", response_class=FileResponse)
async def index(request: Request):
    # ── Custom tracking domain guard (same logic as the SPA catch-all) ────
    host = request.headers.get("host", "").split(":")[0]
    own_host = (
        _urlparse.urlparse(settings.base_url).netloc.split(":")[0]
        if settings.base_url
        else ""
    )
    if host and own_host and host != own_host:
        from app.database import AsyncSessionLocal
        from app.models import Inbox
        from sqlalchemy import select as _select
        async with AsyncSessionLocal() as _db:
            _res = await _db.execute(_select(Inbox).where(Inbox.tracking_domain == host))
            if _res.scalar_one_or_none() is not None:
                return JSONResponse({"ts": None, "ref": 0}, status_code=200)
    return FileResponse(str(BASE_DIR / "frontend" / "dist" / "index.html"))


@app.get("/{full_path:path}", response_class=FileResponse)
async def spa(request: Request, full_path: str):
    # ── Custom tracking domain guard ─────────────────────────────────────────
    # Requests arriving on a custom tracking domain (CNAME'd to this server)
    # should only ever hit the known tracking routes (/o/, /c/, /u/, …).
    # Any other path is silently answered with a deliberately vague JSON so
    # a recipient who stumbles on the URL cannot tell what the server is.
    host = request.headers.get("host", "").split(":")[0]
    own_host = (
        _urlparse.urlparse(settings.base_url).netloc.split(":")[0]
        if settings.base_url
        else ""
    )
    if host and own_host and host != own_host:
        from app.database import AsyncSessionLocal
        from app.models import Inbox
        from sqlalchemy import select as _select
        async with AsyncSessionLocal() as _db:
            _res = await _db.execute(_select(Inbox).where(Inbox.tracking_domain == host))
            if _res.scalar_one_or_none() is not None:
                return JSONResponse({"ts": None, "ref": 0}, status_code=200)
    # ── Normal SPA / API routing ─────────────────────────────────────────────
    # Guard: let the normal routing machinery handle API / asset requests.
    if (
        request.url.path.startswith("/api")
        or request.url.path.startswith("/assets")
        or request.url.path.startswith("/oauth")
        or request.url.path.startswith("/o/")
        or request.url.path.startswith("/c/")
    ):
        raise HTTPException(status_code=404)
    return FileResponse(str(BASE_DIR / "frontend" / "dist" / "index.html"))

