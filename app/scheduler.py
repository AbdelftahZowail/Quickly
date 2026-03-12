"""APScheduler singleton with PostgreSQL job store.

The scheduler is created once during application lifespan (main.py) and stored
here as a module-level variable so that routers can access it without threading
the request object everywhere.

Job store:
  - PostgreSQL (sync SQLAlchemy via psycopg2) when a real Postgres URL is configured.
  - Memory (no persistence) when running against SQLite (tests).

Job IDs for email slots follow the pattern ``slot_<slot_id>`` so they can be
found and removed by prefix when the queue is recalculated or cleared.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

if TYPE_CHECKING:
    from app.models import QueueSlot  # noqa: F401 – type-checking only

log = logging.getLogger("quickly.scheduler")

# Module-level scheduler instance.  Set by main.py during lifespan startup.
_scheduler: AsyncIOScheduler | None = None


def set_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Register the running scheduler instance (called once from main.py)."""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> AsyncIOScheduler | None:
    """Return the running scheduler, or None if not yet initialised."""
    return _scheduler


# ---------------------------------------------------------------------------
# Helpers used by routers / jobs
# ---------------------------------------------------------------------------

def make_job_id(slot_id: int) -> str:
    return f"slot_{slot_id}"


def remove_slot_jobs(scheduler: AsyncIOScheduler) -> int:
    """Remove every ``slot_*`` job from *scheduler*.  Returns the count removed."""
    removed = 0
    for job in scheduler.get_jobs():
        if job.id.startswith("slot_"):
            job.remove()
            removed += 1
    if removed:
        log.info("remove_slot_jobs: removed %d slot jobs", removed)
    return removed


def schedule_slots(scheduler: AsyncIOScheduler, slots: list) -> int:
    """Add a one-shot DateTrigger job for each slot in *slots*.

    Uses ``replace_existing=True`` so the function is idempotent – calling it
    twice for the same slot just resets the fire time.  Only slots whose
    ``scheduled_date`` is in the future (or within the misfire grace window)
    will actually fire; past slots are silently discarded by APScheduler once
    the grace period expires.

    Returns the number of jobs added.
    """
    # Import here to avoid circular imports at module load time.
    from app.jobs import send_slot_job  # noqa: PLC0415

    added = 0
    for slot in slots:
        job_id = make_job_id(slot.id)
        scheduler.add_job(
            send_slot_job,
            trigger=DateTrigger(run_date=slot.scheduled_date),
            id=job_id,
            args=[slot.id],
            replace_existing=True,
            # Allow up to 1 hour for a misfired job (e.g. after a restart).
            misfire_grace_time=3600,
        )
        added += 1
    if added:
        log.info("schedule_slots: added %d slot jobs", added)
    return added


def build_jobstores(db_url: str) -> dict:
    """Return an APScheduler ``jobstores`` dict appropriate for *db_url*.

    For PostgreSQL we use the persisted SQLAlchemyJobStore (requires psycopg2).
    For SQLite (tests) we fall back to the in-memory MemoryJobStore so test
    runs don't need psycopg2 and don't leave rows in any DB.
    """
    if db_url.startswith("sqlite"):
        log.info("build_jobstores: using MemoryJobStore (SQLite / test environment)")
        return {}  # APScheduler defaults to MemoryJobStore when none is specified

    # Derive a synchronous PostgreSQL URL from the async asyncpg URL:
    #   postgresql+asyncpg://... → postgresql://...
    sync_url = db_url.replace("+asyncpg", "")
    if sync_url.startswith("postgres://"):
        sync_url = sync_url.replace("postgres://", "postgresql://", 1)

    try:
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore  # noqa: PLC0415
        log.info("build_jobstores: using SQLAlchemyJobStore (%s...)", sync_url[:40])
        return {"default": SQLAlchemyJobStore(url=sync_url)}
    except Exception as exc:
        log.warning(
            "build_jobstores: could not create SQLAlchemyJobStore (%s); "
            "falling back to MemoryJobStore",
            exc,
        )
        return {}
