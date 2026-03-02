"""FastAPI application and web UI."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
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

    # fire-and-forget; lifetime of task tied to event loop
    asyncio.create_task(kickoff())
    asyncio.create_task(queue_sync_for_all_inboxes(reason="startup"))

    yield
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

# Web UI
BASE_DIR = Path(__file__).resolve().parent.parent

# When running in production we serve the compiled frontend from the
# `frontend/dist` directory so the entire application is available on a
# single port.  API routes live under `/api` (and a few legacy prefixes like
# `/oauth`), so there is no chance of conflicting with client–side paths.
#
# The static mount handles asset files directly and html=True allows the
# index.html file to be returned for the root path.  A catch‑all route below
# ensures that any unmatched request (e.g. /unibox, /campaigns/123) returns
# the SPA entrypoint rather than a 404.

# serve only the compiled asset files.  We avoid mounting the entire
# build at "/" because that would capture *all* requests (including
# /api/*) and prevent our API routes from ever running.  The SPA entrypoint is
# handled below by explicit path operations.
app.mount(
    "/assets",
    StaticFiles(directory=str(BASE_DIR / "frontend" / "dist" / "assets")),
    name="assets",
)

# legacy static directory is no longer needed once the React UI covers all
# pages but we keep it around for now; it won't conflict because it's mounted
# at /static.
if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# the status endpoint should be defined before the SPA fallback so it isn't
# swallowed by the catch‑all route (route order matters).
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

# root and catch‑all for SPA.  Only run when the path does *not* match a known
# API or asset prefix.  Since this route is added after all routers, any
# /api/ request will be handled by the API router first; we still defensively
# check the prefix to avoid accidentally serving index.html for APIs.

@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(str(BASE_DIR / "frontend" / "dist" / "index.html"))

@app.get("/{full_path:path}", response_class=FileResponse)
async def spa(request: Request, full_path: str):
    # If the path looks like an API or asset request, raise 404 so that the
    # normal routing machinery handles it (routes have already been examined
    # in order, but this is an extra guard).
    if request.url.path.startswith("/api") or request.url.path.startswith("/assets") or request.url.path.startswith("/oauth"):
        raise HTTPException(status_code=404)
    return FileResponse(str(BASE_DIR / "frontend" / "dist" / "index.html"))
