"""FastAPI application and web UI."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

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
from app.routers import calendar as calendar_router
from app.routers import settings as settings_router
from app.jobs import run_send_job, last_send_job_run, last_send_job_sent_count


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler = AsyncIOScheduler()
    interval_minutes = max(1, settings.queue_check_interval_minutes)
    scheduler.add_job(
        run_send_job,
        "cron",
        minute=f"*/{interval_minutes}",
        second=0,
        id="send_queue",
    )
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown()


app = FastAPI(title="Campaign Engine", lifespan=lifespan)

app.include_router(inbox.router)
app.include_router(leads.router)
app.include_router(campaigns.router)
app.include_router(test_mode.router)
app.include_router(gmail_oauth.router)
app.include_router(calendar_router.router)
app.include_router(settings_router.router)

# Web UI
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request):
    return templates.TemplateResponse("campaigns.html", {"request": request})


@app.get("/campaigns/add", response_class=HTMLResponse)
async def campaign_add_page(request: Request):
    return templates.TemplateResponse("campaign_add.html", {"request": request})


@app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail_page(request: Request, campaign_id: int):
    return templates.TemplateResponse(
        "campaign_detail.html", {"request": request, "campaign_id": campaign_id}
    )




@app.get("/inboxes", response_class=HTMLResponse)
async def inboxes_page(request: Request):
    return templates.TemplateResponse("inboxes.html", {"request": request})


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    return templates.TemplateResponse("calendar.html", {"request": request})



@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


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
