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
logging.getLogger("campaign_engine").setLevel(logging.DEBUG)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import init_db
from app.settings_manager import settings
from app.routers import inbox, leads, campaigns, test_mode
from app.routers import gmail_oauth
from app.routers import gmail_sync as gmail_sync_router
from app.routers import calendar as calendar_router
from app.routers import settings as settings_router
from app.routers import unibox as unibox_router
from app.gmail_sync import run_gmail_reply_sync_job, run_gmail_watch_renew_job
from app.app_settings import get_gmail_sync_config
from app.jobs import run_send_job, last_send_job_run, last_send_job_sent_count


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler = AsyncIOScheduler()
    interval_minutes = max(1, settings.queue_check_interval_minutes)
    gmail_sync_interval_minutes = 5
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        try:
            sync_cfg = await get_gmail_sync_config(session)
            gmail_sync_interval_minutes = max(1, int(sync_cfg.get("sync_interval_minutes") or 5))
        except Exception:
            gmail_sync_interval_minutes = 5
    scheduler.add_job(
        run_send_job,
        "cron",
        minute=f"*/{interval_minutes}",
        second=0,
        id="send_queue",
    )
    scheduler.add_job(
        run_gmail_reply_sync_job,
        "cron",
        minute=f"*/{gmail_sync_interval_minutes}",
        second=20,
        id="gmail_reply_sync",
    )
    scheduler.add_job(
        run_gmail_watch_renew_job,
        "cron",
        hour="*/6",
        minute=15,
        second=0,
        id="gmail_watch_renew",
    )
    scheduler.start()
    app.state.scheduler = scheduler

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
                resp = await client.post("/api/calendar/recalculate-all")
                resp.raise_for_status()
        except Exception as e:
            logging.getLogger("campaign_engine.routes").error(
                "startup recalculation failed: %s", e
            )

    # fire-and-forget; lifetime of task tied to event loop
    asyncio.create_task(kickoff())

    yield
    scheduler.shutdown()


app = FastAPI(title="Campaign Engine", lifespan=lifespan)

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
app.include_router(gmail_sync_router.router)
app.include_router(calendar_router.router)
app.include_router(settings_router.router)
app.include_router(unibox_router.router)

# Web UI
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "active": "home"})


@app.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request):
    return templates.TemplateResponse("campaigns.html", {"request": request, "active": "campaigns"})


@app.get("/campaigns/add", response_class=HTMLResponse)
async def campaign_add_page(request: Request):
    return templates.TemplateResponse("campaign_add.html", {"request": request, "active": "campaigns"})


@app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail_page(request: Request, campaign_id: int):
    return templates.TemplateResponse(
        "campaign_detail.html", {"request": request, "campaign_id": campaign_id, "active": "campaigns"}
    )




@app.get("/inboxes", response_class=HTMLResponse)
async def inboxes_page(request: Request):
    return templates.TemplateResponse("inboxes.html", {"request": request, "active": "inboxes"})


@app.get("/unibox", response_class=HTMLResponse)
async def unibox_page(request: Request):
    return templates.TemplateResponse("unibox.html", {"request": request, "active": "unibox"})


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    return templates.TemplateResponse("calendar.html", {"request": request, "active": "calendar"})



@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})


@app.get("/api/status")
async def api_status(request: Request):
    """Scheduler and send-job status so you can verify the worker is running."""
    scheduler = getattr(request.app.state, "scheduler", None)
    job = scheduler.get_job("send_queue") if (scheduler and scheduler.running) else None
    next_run = job.next_run_time.isoformat() if (job and getattr(job, "next_run_time", None)) else None
    return {
        "scheduler_running": scheduler is not None and scheduler.running,
        "queue_check_interval_minutes": settings.queue_check_interval_minutes,
        "last_send_job_run": last_send_job_run.isoformat() if last_send_job_run else None,
        "last_send_job_sent_count": last_send_job_sent_count,
        "next_send_job_run": next_run,
        "test_mode": settings.test_mode,
    }
