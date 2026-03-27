"""Post-restore steps: schema/migrations and queue reconciliation."""
from __future__ import annotations

from fastapi import BackgroundTasks

from app.backup_pg import restore_from_upload_to_thread
from app.database import AsyncSessionLocal, engine, init_db, db_url
from app.settings_manager import reload_settings


async def reinitialize_after_restore(background_tasks: BackgroundTasks) -> None:
    """Run migrations and reload settings; schedule global queue recalculation."""
    await init_db()
    async with AsyncSessionLocal() as db:
        await reload_settings(db)

    from app.routers.schedule import run_recalculate_all_in_new_session

    background_tasks.add_task(run_recalculate_all_in_new_session)


async def restore_database_from_bytes(raw: bytes, background_tasks: BackgroundTasks) -> None:
    """Close the pool, pg_restore from upload bytes, then migrate and recalculate."""
    await engine.dispose()
    await restore_from_upload_to_thread(db_url, raw)
    await reinitialize_after_restore(background_tasks)
