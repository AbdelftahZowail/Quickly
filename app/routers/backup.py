"""Admin backup/restore API (PostgreSQL)."""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.app_settings import get_backup_config, save_backup_config
from app.auth import require_admin, require_admin_short_session
from app.backup_pg import (
    BackupToolError,
    BackupUnsupportedError,
    dump_to_thread,
    local_disk_backups_enabled,
    resolve_backup_directory,
    validate_cron_expression,
)
from app.backup_restore_ops import restore_database_from_bytes
from app.backup_schedule import deliver_backup_payload, sync_scheduled_backup_job
from app.database import db_url, get_db
from app.scheduler import get_scheduler
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("quickly.backup.routes")

router = APIRouter(prefix="/api/settings/backup", tags=["backup"])


def _mask_header(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return "****"
    return val[:3] + "***" + val[-3:]


class BackupConfigBody(BaseModel):
    schedule_enabled: bool = False
    cron_expression: str = Field("", max_length=256)
    save_local: bool = False
    local_relative_path: str = Field("backups", max_length=512)
    send_webhook: bool = False
    webhook_url: str = Field("", max_length=2048)
    webhook_auth_header: str = Field("", max_length=512)


@router.get("/config")
async def get_backup_settings(
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    cfg = await get_backup_config(db)
    disk_on = local_disk_backups_enabled()
    resolved = None
    if disk_on:
        try:
            resolved = str(resolve_backup_directory(cfg["local_relative_path"]))
        except ValueError:
            resolved = None
    return {
        "schedule_enabled": cfg["schedule_enabled"],
        "cron_expression": cfg["cron_expression"],
        "save_local": cfg["save_local"],
        "local_relative_path": cfg["local_relative_path"],
        "local_disk_available": disk_on,
        "local_backup_resolved": resolved,
        "send_webhook": cfg["send_webhook"],
        "webhook_url": cfg["webhook_url"],
        "webhook_auth_configured": bool(cfg["webhook_auth_header"]),
        "webhook_auth_header_masked": _mask_header(cfg["webhook_auth_header"]),
    }


@router.put("/config")
async def put_backup_settings(
    body: BackupConfigBody,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    if body.schedule_enabled:
        if not validate_cron_expression(body.cron_expression):
            raise HTTPException(
                status_code=400,
                detail="cron_expression must be five fields: minute hour day month day_of_week",
            )
    if body.send_webhook and not (body.webhook_url or "").strip():
        raise HTTPException(status_code=400, detail="webhook_url is required when send_webhook is enabled")

    if body.save_local and not local_disk_backups_enabled():
        raise HTTPException(
            status_code=400,
            detail=(
                "Saving backups on the server disk is only available with Docker Compose (dev/build). "
                "Use “POST to webhook” for prebuilt image or cloud deployments."
            ),
        )
    if body.save_local:
        try:
            resolve_backup_directory(body.local_relative_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    prev = await get_backup_config(db)
    if not body.send_webhook:
        auth_header = ""
    else:
        auth_header = body.webhook_auth_header.strip()
        if not auth_header and prev["webhook_auth_header"]:
            auth_header = prev["webhook_auth_header"]

    await save_backup_config(
        db,
        schedule_enabled=body.schedule_enabled,
        cron_expression=body.cron_expression,
        save_local=body.save_local,
        local_relative_path=body.local_relative_path,
        send_webhook=body.send_webhook,
        webhook_url=body.webhook_url,
        webhook_auth_header=auth_header,
    )
    await db.commit()

    cfg = await get_backup_config(db)
    sync_scheduled_backup_job(get_scheduler(), cfg)
    return await get_backup_settings(db, _admin)


@router.post("/run")
async def run_backup_now(
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    try:
        payload = await dump_to_thread(db_url)
    except BackupUnsupportedError:
        raise HTTPException(
            status_code=501,
            detail="Backup requires PostgreSQL; current DATABASE_URL is not supported.",
        )
    except BackupToolError as e:
        raise HTTPException(status_code=500, detail=str(e))

    cfg = await get_backup_config(db)
    result = await deliver_backup_payload(payload, cfg)
    log.info("Manual backup completed: %s", result)
    return {"ok": True, **result}


@router.get("/download")
async def download_backup(_admin=Depends(require_admin)):
    try:
        data = await dump_to_thread(db_url)
    except BackupUnsupportedError:
        raise HTTPException(
            status_code=501,
            detail="Backup requires PostgreSQL; current DATABASE_URL is not supported.",
        )
    except BackupToolError as e:
        raise HTTPException(status_code=500, detail=str(e))
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"quickly-backup-{ts}.dump"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore")
async def restore_backup(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _admin=Depends(require_admin_short_session),
):
    raw = await file.read()
    if not raw or len(raw) < 64:
        raise HTTPException(status_code=400, detail="Invalid or empty backup file")
    try:
        await restore_database_from_bytes(raw, background_tasks)
    except BackupUnsupportedError:
        raise HTTPException(status_code=501, detail="Restore requires PostgreSQL.")
    except BackupToolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log.warning("Database restored from upload by admin user id=%s", getattr(_admin, "id", "?"))
    return {"ok": True, "detail": "Database restored; migrations applied. Queue recalculation is running."}
