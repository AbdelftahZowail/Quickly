"""Helpers for reading / writing application settings stored in the DB.

All configuration is now stored in the database instead of .env files,
allowing users to manage settings from the frontend interface.
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting

log = logging.getLogger("quickly.app_settings")

# Canonical keys ---------------------------------------------------------------
GOOGLE_CLIENT_ID_KEY = "google_client_id"
GOOGLE_CLIENT_SECRET_KEY = "google_client_secret"
TEST_MODE_KEY = "test_mode"
SCHEDULING_STRATEGY_KEY = "scheduling_strategy"  # "priority" (default) | "round_robin"
GMAIL_PUSH_TOPIC_KEY = "gmail_push_topic"
GMAIL_PUSH_WEBHOOK_TOKEN_KEY = "gmail_push_webhook_token"
GMAIL_REPLY_SYNC_INTERVAL_MINUTES_KEY = "gmail_reply_sync_interval_minutes"


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


# Scheduling strategy convenience ---------------------------------------------

VALID_SCHEDULING_STRATEGIES = ("priority", "round_robin")


async def get_scheduling_strategy(db: AsyncSession) -> str:
    """Return the active scheduling strategy ('priority' or 'round_robin')."""
    value = await get_setting(db, SCHEDULING_STRATEGY_KEY)
    if value in VALID_SCHEDULING_STRATEGIES:
        return value
    return "priority"  # default


async def set_scheduling_strategy(db: AsyncSession, strategy: str) -> None:
    """Persist the scheduling strategy ('priority' or 'round_robin')."""
    if strategy not in VALID_SCHEDULING_STRATEGIES:
        raise ValueError(f"Invalid strategy '{strategy}'; must be one of {VALID_SCHEDULING_STRATEGIES}")
    await put_setting(db, SCHEDULING_STRATEGY_KEY, strategy)
    await db.flush()
    log.info("Scheduling strategy set to '%s'", strategy)


# Gmail reply sync / push settings --------------------------------------------

async def get_gmail_sync_config(db: AsyncSession) -> dict:
    """Return Gmail push/poll sync settings."""
    topic = await get_setting(db, GMAIL_PUSH_TOPIC_KEY) or ""
    webhook_token = await get_setting(db, GMAIL_PUSH_WEBHOOK_TOKEN_KEY) or ""
    interval_raw = await get_setting(db, GMAIL_REPLY_SYNC_INTERVAL_MINUTES_KEY) or "5"
    try:
        interval_minutes = max(1, int(interval_raw))
    except Exception:
        interval_minutes = 5
    return {
        "push_topic": topic,
        "push_topic_configured": bool(topic),
        "webhook_token": webhook_token,
        "webhook_token_configured": bool(webhook_token),
        "sync_interval_minutes": interval_minutes,
    }


async def save_gmail_sync_config(
    db: AsyncSession,
    push_topic: str,
    webhook_token: str,
    sync_interval_minutes: int,
) -> None:
    """Persist Gmail push/poll sync settings."""
    await put_setting(db, GMAIL_PUSH_TOPIC_KEY, push_topic.strip())
    await put_setting(db, GMAIL_PUSH_WEBHOOK_TOKEN_KEY, webhook_token.strip())
    await put_setting(db, GMAIL_REPLY_SYNC_INTERVAL_MINUTES_KEY, str(max(1, int(sync_interval_minutes))))
    await db.flush()
