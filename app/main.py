"""FastAPI application and web UI."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
import asyncio

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
from app.jobs import run_send_job, last_send_job_run, last_send_job_sent_count
from app.unibox import queue_sync_for_all_inboxes, run_unibox_sync_job


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

# enable CORS so the frontend (running on a different port) can talk to
# the API.  The UI typically runs at http://localhost:5173 during
# development; allow it (and other origins, if needed).  We explicitly
# list the origin(s) instead of using "*" to avoid issues with
# credentials later, but using "*" is acceptable for a local dev setup.
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inbox.router)
app.include_router(leads.router)
app.include_router(campaigns.router)
app.include_router(test_mode.router)
app.include_router(gmail_oauth.router)
app.include_router(schedule_router.router)
app.include_router(settings_router.router)
app.include_router(unibox_router.router)

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
async def api_status(request: Request):
    """Schedule and send-job status so you can verify the worker is running."""
    schedule = getattr(request.app.state, "schedule", None)
    job = schedule.get_job("send_queue") if (schedule and schedule.running) else None
    next_run = job.next_run_time.isoformat() if (job and getattr(job, "next_run_time", None)) else None
    return {
        "schedule_running": schedule is not None and schedule.running,
        "queue_check_interval_minutes": settings.queue_check_interval_minutes,
        "last_send_job_run": last_send_job_run.isoformat() if last_send_job_run else None,
        "last_send_job_sent_count": last_send_job_sent_count,
        "next_send_job_run": next_run,
        "test_mode": settings.test_mode,
    }


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(str(BASE_DIR / "frontend" / "dist" / "index.html"))


@app.get("/{full_path:path}", response_class=FileResponse)
async def spa(request: Request, full_path: str):
    # Guard: let the normal routing machinery handle API / asset requests.
    if (
        request.url.path.startswith("/api")
        or request.url.path.startswith("/assets")
        or request.url.path.startswith("/oauth")
    ):
        raise HTTPException(status_code=404)
    return FileResponse(str(BASE_DIR / "frontend" / "dist" / "index.html"))

