"""Settings API routes for managing configuration."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.settings_manager import settings, update_settings, reload_settings
from app.app_settings import (
    get_google_oauth_credentials,
    save_google_oauth_credentials,
    get_test_mode,
    set_test_mode,
)

log = logging.getLogger("campaign_engine.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


class GoogleOAuthConfig(BaseModel):
    client_id: str
    client_secret: str


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
