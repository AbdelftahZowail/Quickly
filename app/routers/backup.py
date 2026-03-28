"""Admin backup/restore API (PostgreSQL)."""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.app_settings import get_backup_config, save_backup_config
from app.auth import require_admin, require_admin_short_session
from app.backup_delivery import wrap_pg_dump_for_backup_config
from app.backup_manifest import collect_backup_manifest
from app.backup_package import (
    BackupPackageError,
    pack_backup,
    read_backup_metadata,
    read_password_hint,
    unpack_backup,
)
from app.backup_pg import (
    BackupToolError,
    BackupUnsupportedError,
    dump_to_thread,
    local_disk_backups_enabled,
    resolve_backup_directory,
    validate_cron_expression,
    validate_dump_bytes_async,
)
from app.backup_restore_ops import restore_database_from_path
from app.backup_restore_staging import (
    consume_staged_dump,
    delete_staged_dump,
    purge_expired_staging,
    stage_decrypted_dump,
)
from app.backup_schedule import deliver_backup_payload, sync_scheduled_backup_job
from app.database import db_url, get_db
from app.scheduler import get_scheduler

log = logging.getLogger("quickly.backup.routes")

router = APIRouter(prefix="/api/settings/backup", tags=["backup"])

STAGING_KIND_ADMIN = "admin"
# Client (api.js) reloads the app when this header is present after successful restore.
RELOAD_AFTER_RESTORE_HEADER = "X-Quickly-Reload"
RELOAD_AFTER_RESTORE_VALUE = "1"


def _mask_header(val: str) -> str:
    if not val:
        return ""

    n = len(val)

    # very short → hide everything
    if n <= 4:
        return "*" * n

    # reveal proportionally (about 25% on each side)
    reveal = max(1, n // 4)
    masked_len = n - (reveal * 2)

    return val[:reveal] + ("*" * masked_len) + val[-reveal:]

class BackupConfigBody(BaseModel):
    schedule_enabled: bool = False
    cron_expression: str = Field("", max_length=256)
    save_local: bool = False
    local_relative_path: str = Field("backups", max_length=512)
    send_webhook: bool = False
    webhook_url: str = Field("", max_length=2048)
    webhook_auth_header: str = Field("", max_length=512)


class BackupEncryptionBody(BaseModel):
    encrypt_backups: bool = False
    backup_encryption_password: str = Field("", max_length=512)
    backup_encryption_hint: str = Field("", max_length=200)


class BackupDownloadBody(BaseModel):
    encrypt: bool = True
    password: str = ""
    password_hint: str = Field("", max_length=200)
    use_saved_encryption: bool = False


class RestoreExecuteBody(BaseModel):
    restore_token: str = Field(..., min_length=10, max_length=256)


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
    pwd = (cfg.get("backup_encryption_password") or "").strip()
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
        "encrypt_backups": cfg["encrypt_backups"],
        "backup_encryption_configured": bool(pwd),
        "backup_encryption_hint": cfg.get("backup_encryption_hint") or "",
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
        encrypt_backups=prev["encrypt_backups"],
        backup_encryption_password=prev["backup_encryption_password"],
        backup_encryption_hint=prev["backup_encryption_hint"],
    )
    await db.commit()

    cfg = await get_backup_config(db)
    sync_scheduled_backup_job(get_scheduler(), cfg)
    return await get_backup_settings(db, _admin)


@router.put("/encryption")
async def put_backup_encryption(
    body: BackupEncryptionBody,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Persist backup password / hint and encrypt flag (scheduled & Run backup now)."""
    prev = await get_backup_config(db)
    new_pw = body.backup_encryption_password.strip()
    if new_pw:
        enc_pw = new_pw
    else:
        enc_pw = prev["backup_encryption_password"]

    hint_in = body.backup_encryption_hint.strip()
    if new_pw:
        hint_to_save = hint_in
    elif hint_in:
        hint_to_save = hint_in
    else:
        hint_to_save = prev["backup_encryption_hint"]

    if body.encrypt_backups and not (enc_pw or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Encrypt automatic backups is on but no password is set. Enter a password or turn encryption off.",
        )
    await save_backup_config(
        db,
        schedule_enabled=prev["schedule_enabled"],
        cron_expression=prev["cron_expression"],
        save_local=prev["save_local"],
        local_relative_path=prev["local_relative_path"],
        send_webhook=prev["send_webhook"],
        webhook_url=prev["webhook_url"],
        webhook_auth_header=prev["webhook_auth_header"],
        encrypt_backups=body.encrypt_backups,
        backup_encryption_password=enc_pw,
        backup_encryption_hint=hint_to_save,
    )
    await db.commit()
    cfg = await get_backup_config(db)
    pwd = (cfg.get("backup_encryption_password") or "").strip()
    return {
        "ok": True,
        "encrypt_backups": cfg["encrypt_backups"],
        "backup_encryption_configured": bool(pwd),
        "backup_encryption_hint": cfg.get("backup_encryption_hint") or "",
    }


@router.post("/run")
async def run_backup_now(
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    try:
        raw_dump = await dump_to_thread(db_url)
    except BackupUnsupportedError:
        raise HTTPException(
            status_code=501,
            detail="Backup requires PostgreSQL; current DATABASE_URL is not supported.",
        )
    except BackupToolError as e:
        raise HTTPException(status_code=500, detail=str(e))

    cfg = await get_backup_config(db)
    try:
        enc = bool(cfg.get("encrypt_backups") and (cfg.get("backup_encryption_password") or "").strip())
        manifest = await collect_backup_manifest(db, encrypted=enc)
        wrapped = wrap_pg_dump_for_backup_config(manifest, raw_dump, cfg)
    except BackupPackageError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = await deliver_backup_payload(wrapped, cfg)
    log.info("Manual backup completed: %s", result)
    return {"ok": True, **result}


@router.post("/download")
async def download_backup(
    body: BackupDownloadBody,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    try:
        raw_dump = await dump_to_thread(db_url)
    except BackupUnsupportedError:
        raise HTTPException(
            status_code=501,
            detail="Backup requires PostgreSQL; current DATABASE_URL is not supported.",
        )
    except BackupToolError as e:
        raise HTTPException(status_code=500, detail=str(e))

    cfg = await get_backup_config(db)
    try:
        if body.use_saved_encryption:
            pwd = (cfg.get("backup_encryption_password") or "").strip()
            if not pwd:
                raise HTTPException(
                    status_code=400,
                    detail="No saved backup password. Save one under Scheduled & remote backup below.",
                )
            hint = (cfg.get("backup_encryption_hint") or "").strip()
            manifest = await collect_backup_manifest(db, encrypted=True)
            wrapped = pack_backup(
                manifest,
                raw_dump,
                encrypt=True,
                password=pwd,
                password_hint=hint,
            )
        elif body.encrypt:
            manifest = await collect_backup_manifest(db, encrypted=True)
            wrapped = pack_backup(
                manifest,
                raw_dump,
                encrypt=True,
                password=body.password or None,
                password_hint=body.password_hint,
            )
        else:
            manifest = await collect_backup_manifest(db, encrypted=False)
            wrapped = pack_backup(
                manifest,
                raw_dump,
                encrypt=False,
                password_hint=body.password_hint,
            )
    except HTTPException:
        raise
    except BackupPackageError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"quickly-backup-{ts}.qbk"
    return StreamingResponse(
        io.BytesIO(wrapped),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore/metadata")
async def restore_metadata(
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    _admin=Depends(require_admin_short_session),
):
    """Backup summary from file only (no password). Encrypted files expose a plaintext preview with masked admin emails."""
    raw = await file.read()
    if not raw or len(raw) < 32:
        raise HTTPException(status_code=400, detail="Invalid or empty backup file")
    try:
        backup_preview, encrypted, hint = read_backup_metadata(raw)
    except BackupPackageError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    current = await collect_backup_manifest(db, encrypted=False)
    return {
        "backup_preview": backup_preview,
        "encrypted": encrypted,
        "password_required": encrypted,
        "password_hint": hint or "",
        "current_database": current,
    }


@router.post("/restore/preview")
async def restore_preview(
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    password: str = Form(""),
    _admin=Depends(require_admin_short_session),
):
    raw = await file.read()
    if not raw or len(raw) < 32:
        raise HTTPException(status_code=400, detail="Invalid or empty backup file")
    purge_expired_staging()
    pw = (password or "").strip() or None
    try:
        manifest, dump = unpack_backup(raw, password=pw)
    except BackupPackageError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        await validate_dump_bytes_async(dump)
    except BackupToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    current = await collect_backup_manifest(db, encrypted=False)
    token, ttl = stage_decrypted_dump(dump, kind=STAGING_KIND_ADMIN)
    hint = read_password_hint(raw)
    return {
        "restore_token": token,
        "expires_in_seconds": ttl,
        "password_hint": hint or "",
        "backup": manifest,
        "current_database": current,
    }


@router.post("/restore/execute")
async def restore_execute(
    body: RestoreExecuteBody,
    background_tasks: BackgroundTasks,
    response: Response,
    _admin=Depends(require_admin_short_session),
):
    path = consume_staged_dump(body.restore_token, expected_kind=STAGING_KIND_ADMIN)
    if path is None:
        raise HTTPException(status_code=400, detail="Invalid or expired restore confirmation. Run preview again.")
    try:
        await restore_database_from_path(path, background_tasks)
    except BackupUnsupportedError:
        raise HTTPException(status_code=501, detail="Restore requires PostgreSQL.")
    except BackupToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        delete_staged_dump(path)

    log.warning("Database restored from staged upload by admin user id=%s", getattr(_admin, "id", "?"))
    response.headers[RELOAD_AFTER_RESTORE_HEADER] = RELOAD_AFTER_RESTORE_VALUE
    return {"ok": True, "detail": "Database restored; migrations applied. Queue recalculation is running."}
