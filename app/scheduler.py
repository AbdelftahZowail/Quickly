"""APScheduler singleton with PostgreSQL job store.

The scheduler is created once during application lifespan (main.py) and stored
here as a module-level variable so that routers can access it without threading
the request object everywhere.

Job store:
  - PostgreSQL (sync SQLAlchemy via psycopg2) when a real Postgres URL is configured.
  - Memory (no persistence) when running against SQLite (tests).

Email sending is handled by a single periodic ``run_slot_scan_job`` worker
(registered in main.py) that scans for due QueueSlot rows rather than by
individual per-slot DateTrigger jobs.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
