"""Helpers for reading / writing application settings stored in the DB.

All configuration is now stored in the database instead of .env files,
allowing users to manage settings from the frontend interface.
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting

log = logging.getLogger("campaign_engine.app_settings")

# Canonical keys ---------------------------------------------------------------
GOOGLE_CLIENT_ID_KEY = "google_client_id"
GOOGLE_CLIENT_SECRET_KEY = "google_client_secret"
TEST_MODE_KEY = "test_mode"


# Generic helpers --------------------------------------------------------------

async def get_setting(db: AsyncSession, key: str) -> str | None:
    """Return the value for *key*, or ``None`` if not set."""
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else None


async def put_setting(db: AsyncSession, key: str, value: str) -> None:
    """Insert or update *key* with *value*."""
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()


# Google OAuth convenience -----------------------------------------------------

async def get_google_oauth_credentials(db: AsyncSession) -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` from the DB."""
    client_id = await get_setting(db, GOOGLE_CLIENT_ID_KEY) or ""
    client_secret = await get_setting(db, GOOGLE_CLIENT_SECRET_KEY) or ""
    return client_id, client_secret


async def save_google_oauth_credentials(db: AsyncSession, client_id: str, client_secret: str) -> None:
    """Persist Google OAuth credentials to the database and reload settings."""
    await put_setting(db, GOOGLE_CLIENT_ID_KEY, client_id)
    await put_setting(db, GOOGLE_CLIENT_SECRET_KEY, client_secret)
    await db.flush()
    # Reload settings into memory cache
    from app.settings_manager import reload_settings
    await reload_settings(db)
    log.info("Google OAuth credentials saved to database")


# Test mode convenience --------------------------------------------------------

async def get_test_mode(db: AsyncSession) -> bool:
    """Return whether test mode is enabled."""
    value = await get_setting(db, TEST_MODE_KEY)
    return value and value.lower() in ("true", "1", "yes")


async def set_test_mode(db: AsyncSession, enabled: bool) -> None:
    """Enable or disable test mode."""
    await put_setting(db, TEST_MODE_KEY, "true" if enabled else "false")
    await db.flush()
    # Reload settings into memory cache
    from app.settings_manager import reload_settings
    await reload_settings(db)
    log.info("Test mode %s", "enabled" if enabled else "disabled")
