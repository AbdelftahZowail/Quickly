"""APScheduler hook for scheduled PostgreSQL backups."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.triggers.cron import CronTrigger

from app.app_settings import get_backup_config
from app.backup_delivery import wrap_pg_dump_for_backup_config
from app.backup_manifest import collect_backup_manifest
from app.backup_pg import (
    BackupUnsupportedError,
    dump_to_thread,
    local_disk_backups_enabled,
    post_backup_webhook,
    validate_cron_expression,
    write_local_backup,
)
from app.database import AsyncSessionLocal, db_url

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger("quickly.backup_schedule")

SCHEDULED_BACKUP_JOB_ID = "scheduled_pg_backup"


async def deliver_backup_payload(data: bytes, cfg: dict) -> dict:
    """Write local file and/or POST to webhook per *cfg* (from :func:`get_backup_config`)."""
    out: dict = {"local_path": None, "local_skipped": None, "webhook_ok": False, "webhook_error": None}
    fname = "quickly-backup.qbk"

    if cfg.get("save_local"):
        if not local_disk_backups_enabled():
            out["local_skipped"] = (
                "Local disk backups are off on this deployment (typical for prebuilt images). Use a webhook."
            )
        else:
            try:
                path = write_local_backup(data, user_relative_path=cfg.get("local_relative_path"))
                out["local_path"] = str(path) if path else None
            except ValueError as e:
                out["local_skipped"] = str(e)

    if cfg.get("send_webhook") and cfg.get("webhook_url"):
        auth = (cfg.get("webhook_auth_header") or "").strip() or None
        try:
            await post_backup_webhook(cfg["webhook_url"], data, fname, authorization=auth)
            out["webhook_ok"] = True
        except Exception as e:
            log.exception("Backup webhook failed")
            out["webhook_error"] = str(e)

    return out


async def run_scheduled_backup_job() -> None:
    async with AsyncSessionLocal() as db:
        cfg = await get_backup_config(db)
    if not cfg["schedule_enabled"]:
        return
    try:
        raw_dump = await dump_to_thread(db_url)
    except BackupUnsupportedError:
        log.warning("Scheduled backup skipped: database is not PostgreSQL")
        return
    except Exception:
        log.exception("Scheduled backup: pg_dump failed")
        return
    try:
        async with AsyncSessionLocal() as db:
            cfg = await get_backup_config(db)
            enc = bool(cfg.get("encrypt_backups") and (cfg.get("backup_encryption_password") or "").strip())
            manifest = await collect_backup_manifest(db, encrypted=enc)
            wrapped = wrap_pg_dump_for_backup_config(manifest, raw_dump, cfg)
    except Exception:
        log.exception("Scheduled backup: packaging failed")
        return
    result = await deliver_backup_payload(wrapped, cfg)
    log.info("Scheduled backup finished: %s", result)


def sync_scheduled_backup_job(schedule: AsyncIOScheduler | None, cfg: dict) -> None:
    """Register or remove the cron job from *cfg*."""
    if schedule is None:
        return
    try:
        schedule.remove_job(SCHEDULED_BACKUP_JOB_ID)
    except Exception:
        pass

    if not cfg.get("schedule_enabled"):
        return

    expr = (cfg.get("cron_expression") or "").strip()
    if not expr or not validate_cron_expression(expr):
        log.warning("Scheduled backup enabled but cron expression missing or invalid")
        return

    try:
        trigger = CronTrigger.from_crontab(expr)
    except Exception as e:
        log.warning("Invalid backup cron %r: %s", expr, e)
        return

    schedule.add_job(
        run_scheduled_backup_job,
        trigger=trigger,
        id=SCHEDULED_BACKUP_JOB_ID,
        replace_existing=True,
    )
    log.info("Scheduled PostgreSQL backup registered: %s", expr)


async def register_scheduled_backup_from_db(schedule: AsyncIOScheduler | None) -> None:
    async with AsyncSessionLocal() as db:
        cfg = await get_backup_config(db)
    sync_scheduled_backup_job(schedule, cfg)
