"""Settings API routes for managing configuration."""
import logging

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.settings_manager import settings, update_settings
from app.app_settings import (
    get_google_oauth_credentials,
    save_google_oauth_credentials,
    get_test_mode,
    set_test_mode,
    get_gmail_sync_config,
    save_gmail_sync_config,
    get_scheduling_strategy,
    set_scheduling_strategy,
)

log = logging.getLogger("campaign_engine.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


class GoogleOAuthConfig(BaseModel):
    client_id: str
    client_secret: str


class GmailSyncConfig(BaseModel):
    push_topic: str = ""
    webhook_token: str = ""
    sync_interval_minutes: int = 5


class AllSettings(BaseModel):
    base_url: str
    email_provider: str
    resend_api_key: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_use_tls: bool
    google_client_id: str
    google_client_secret: str
    queue_check_interval_minutes: int
    test_mode: bool


@router.get("/")
async def get_all_settings(db: AsyncSession = Depends(get_db)):
    """Get all application settings (sensitive values are partially masked)."""
    test_mode_value = await get_test_mode(db)
    
    gmail_sync = await get_gmail_sync_config(db)

    return {
        "base_url": settings.base_url,
        "email_provider": settings.email_provider,
        "resend_api_key": _mask_secret(settings.resend_api_key),
        "resend_api_key_configured": bool(settings.resend_api_key),
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_user": settings.smtp_user,
        "smtp_password": _mask_secret(settings.smtp_password),
        "smtp_password_configured": bool(settings.smtp_password),
        "smtp_use_tls": settings.smtp_use_tls,
        "google_client_id": _mask_secret(settings.google_client_id),
        "google_client_id_configured": bool(settings.google_client_id),
        "google_client_secret": _mask_secret(settings.google_client_secret),
        "google_client_secret_configured": bool(settings.google_client_secret),
        "queue_check_interval_minutes": settings.queue_check_interval_minutes,
        "test_mode": test_mode_value,
        "time_offset_days": settings.time_offset_days,
        "gmail_push_topic": gmail_sync.get("push_topic") or "",
        "gmail_push_topic_configured": gmail_sync.get("push_topic_configured", False),
        "gmail_push_webhook_token": _mask_secret(gmail_sync.get("webhook_token") or ""),
        "gmail_push_webhook_token_configured": gmail_sync.get("webhook_token_configured", False),
        "gmail_reply_sync_interval_minutes": gmail_sync.get("sync_interval_minutes", 5),
    }


@router.put("/")
async def update_all_settings(
    new_settings: AllSettings,
    db: AsyncSession = Depends(get_db),
):
    """Update all application settings."""
    try:
        # Prepare data for database
        data = {
            "base_url": new_settings.base_url,
            "email_provider": new_settings.email_provider,
            "resend_api_key": new_settings.resend_api_key,
            "smtp_host": new_settings.smtp_host,
            "smtp_port": str(new_settings.smtp_port),
            "smtp_user": new_settings.smtp_user,
            "smtp_password": new_settings.smtp_password,
            "smtp_use_tls": str(new_settings.smtp_use_tls),
            "google_client_id": new_settings.google_client_id,
            "google_client_secret": new_settings.google_client_secret,
            "queue_check_interval_minutes": str(new_settings.queue_check_interval_minutes),
            "test_mode": str(new_settings.test_mode),
        }
        
        await update_settings(db, data)
        log.info("Settings updated successfully")
        return {"ok": True, "message": "Settings updated successfully"}
    except Exception as e:
        log.error("Failed to update settings: %s", e)
        raise HTTPException(500, f"Failed to update settings: {str(e)}")


@router.get("/test-mode")
async def get_test_mode_status(db: AsyncSession = Depends(get_db)):
    """Get test mode status."""
    enabled = await get_test_mode(db)
    return {"test_mode": enabled}


@router.post("/test-mode")
async def update_test_mode(
    enabled: bool,
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable test mode."""
    try:
        await set_test_mode(db, enabled)
        await db.commit()
        return {"ok": True, "test_mode": enabled}
    except Exception as e:
        log.error("Failed to update test mode: %s", e)
        raise HTTPException(500, f"Failed to update test mode: {str(e)}")


@router.get("/google-oauth")
async def get_google_oauth(db: AsyncSession = Depends(get_db)):
    """Check whether Google OAuth credentials are configured (does NOT expose secrets)."""
    client_id, client_secret = await get_google_oauth_credentials(db)
    configured = bool(client_id and client_secret)
    # Only return a masked hint so the frontend knows *something* is set
    return {
        "configured": configured,
        "client_id_hint": _mask_secret(client_id) if client_id else "",
    }


@router.post("/google-oauth")
async def save_google_oauth(
    config: GoogleOAuthConfig,
    db: AsyncSession = Depends(get_db),
):
    """Save Google OAuth credentials to the database."""
    if not config.client_id or not config.client_secret:
        raise HTTPException(400, "Both client_id and client_secret are required")

    try:
        await save_google_oauth_credentials(db, config.client_id, config.client_secret)
        await db.commit()
        return {"ok": True, "message": "Credentials saved."}
    except Exception as e:
        log.error("save_google_oauth: failed to save credentials: %s", e)
        raise HTTPException(500, f"Failed to save credentials: {str(e)}")


@router.get("/gmail-sync")
async def get_gmail_sync_settings(db: AsyncSession = Depends(get_db)):
    """Return Gmail push/poll sync settings."""
    cfg = await get_gmail_sync_config(db)
    return {
        "push_topic": cfg.get("push_topic") or "",
        "push_topic_configured": cfg.get("push_topic_configured", False),
        "webhook_token": _mask_secret(cfg.get("webhook_token") or ""),
        "webhook_token_configured": cfg.get("webhook_token_configured", False),
        "sync_interval_minutes": cfg.get("sync_interval_minutes", 5),
    }


@router.post("/gmail-sync")
async def save_gmail_sync_settings(
    config: GmailSyncConfig,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Persist Gmail push/poll sync settings."""
    try:
        token_value = config.webhook_token or ""
        if "***" in token_value:
            existing_cfg = await get_gmail_sync_config(db)
            token_value = existing_cfg.get("webhook_token") or ""
        await save_gmail_sync_config(
            db,
            push_topic=config.push_topic or "",
            webhook_token=token_value,
            sync_interval_minutes=max(1, int(config.sync_interval_minutes or 5)),
        )
        await db.commit()
        # if a push topic is configured we should renew watches immediately so
        # incoming messages will trigger sync right away instead of waiting for
        # the six‑hour renewal job. schedule this as a background task to avoid
        # blocking the request.
        from app.gmail_sync import renew_gmail_watch_for_all
        from app.database import AsyncSessionLocal

        async def _renew():
            async with AsyncSessionLocal() as session:
                try:
                    await renew_gmail_watch_for_all(session)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    log.exception("automatic watch renewal failed after settings update")

        background_tasks.add_task(_renew)

        return {"ok": True, "message": "Gmail sync settings saved. Restart server to apply new interval."}
    except Exception as e:
        log.error("save_gmail_sync_settings failed: %s", e)
        raise HTTPException(500, f"Failed to save Gmail sync settings: {e}")


# Time offset helpers ---------------------------------------------------------
@router.get('/time-offset')
async def get_time_offset():
    """Return the current persisted time offset (days)."""
    return {"time_offset_days": settings.time_offset_days}


from pydantic import BaseModel as _BaseModel

class _TimeOffset(_BaseModel):
    time_offset_days: int


@router.post('/time-offset')
async def set_time_offset(payload: _TimeOffset, db: AsyncSession = Depends(get_db)):
    """Set the persisted time offset (in days) and reload settings in this process."""
    try:
        from app.settings_manager import update_setting

        await update_setting(db, 'time_offset_days', str(int(payload.time_offset_days)))
        await db.commit()
        return {"ok": True, "time_offset_days": payload.time_offset_days}
    except Exception as e:
        log.error('set_time_offset failed: %s', e)
        raise HTTPException(500, f"Failed to set time offset: {e}")

def _mask_secret(value: str) -> str:
    """Mask a secret value for display, showing only first few characters."""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:8]}***"


# Scheduling strategy ---------------------------------------------------------

@router.get("/scheduling-strategy")
async def get_scheduling_strategy_endpoint(db: AsyncSession = Depends(get_db)):
    """Return the active scheduling strategy ('priority' or 'round_robin')."""
    strategy = await get_scheduling_strategy(db)
    return {"scheduling_strategy": strategy}


class _SchedulingStrategyPayload(_BaseModel):
    scheduling_strategy: str  # 'priority' | 'round_robin'


@router.post("/scheduling-strategy")
async def set_scheduling_strategy_endpoint(
    payload: _SchedulingStrategyPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Set the scheduling strategy used by *Recalculate All*.

    Changing the strategy affects the way ``Recalculate All`` distributes
    leads across campaigns.  Historically we would perform a full
    recalculation synchronously, but this can take several seconds when the
    database is large and it is unnecessary to block the request.  Instead
    we now schedule the work in a background task and return immediately.

    The frontend should warn the user if there are already leads enrolled in
    any campaign prior to making this request; it can check by calling
    ``GET /api/campaigns/has-leads`` and show a confirmation dialog.

    - **priority** – campaigns are processed in ascending ``priority`` order;
      use ``POST /api/campaigns/reorder`` to define that order.
    - **round_robin** – inbox capacity is divided evenly across all active
      campaigns and leads are scheduled in interleaved batches.
    """
    try:
        await set_scheduling_strategy(db, payload.scheduling_strategy)
        # commit before kicking off background work so that any subsequent
        # recalculation sees the new value.
        await db.commit()

        # schedule recalculation by POSTing to the public endpoint
        # `/api/calendar/recalculate-all`.  This mirrors the startup logic in
        # ``app.main`` and ensures any middleware or side effects run through
        # the normal request machinery.  We do not wait for the request to
        # complete before returning to the caller.
        from httpx import AsyncClient, ASGITransport
        from app.main import app

        async def _bg_recalc():
            try:
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post("/api/calendar/recalculate-all")
                    resp.raise_for_status()
            except Exception:
                log.exception("background recalculation via endpoint failed")

        background_tasks.add_task(_bg_recalc)

        return {"ok": True, "scheduling_strategy": payload.scheduling_strategy}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.error("set_scheduling_strategy failed: %s", e)
        raise HTTPException(500, f"Failed to update scheduling strategy: {e}")
