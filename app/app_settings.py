"""Helpers for reading / writing application settings stored in the DB.

All configuration is now stored in the database instead of .env files,
allowing users to manage settings from the frontend interface.
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
from app.settings_manager import settings

log = logging.getLogger("quickly.app_settings")

# Canonical keys ---------------------------------------------------------------
GOOGLE_CLIENT_ID_KEY = "google_client_id"
GOOGLE_CLIENT_SECRET_KEY = "google_client_secret"
TEST_MODE_KEY = "test_mode"
SCHEDULING_STRATEGY_KEY = "scheduling_strategy"  # "priority" (default) | "round_robin"
GMAIL_PUSH_TOPIC_KEY = "gmail_push_topic"
GMAIL_PUSH_WEBHOOK_TOKEN_KEY = "gmail_push_webhook_token"
GMAIL_REPLY_SYNC_INTERVAL_MINUTES_KEY = "gmail_reply_sync_interval_minutes"

# custom tracking domain (hostname only, e.g. "mail.yourclient.com")
TRACKING_DOMAIN_KEY = "tracking_domain"


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

async def get_google_oauth_credentials(db: AsyncSession | None = None) -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` from the environment.

    The values are read from ``app.settings_manager.settings`` which itself
    picks them up from environment variables (or a ``.env`` file) when the
    process starts.  Older versions stored these credentials in the database,
    but the lookup was removed to avoid surprises; the database is now
    ignored entirely.  The ``db`` parameter is accepted for backwards
    compatibility with existing callers but is unused.
    """
    # simply return whatever is currently in memory (populated from env)
    return settings.google_client_id, settings.google_client_secret


async def save_google_oauth_credentials(db: AsyncSession, client_id: str, client_secret: str) -> None:
    """No-op wrapper retained for backward compatibility.

    The application no longer persists OAuth client credentials to the
    database; they are expected to be configured via environment variables.
    Calling this function will log a warning but otherwise do nothing.
    """
    log.warning("save_google_oauth_credentials called but persistence is disabled; "
                "credentials must be set via environment variables")
    # intentionally do not write anything to the database


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


# Tracking domain helpers ------------------------------------------------------

async def get_tracking_base_url(db: AsyncSession) -> str:
    """Return the app-level fallback base URL used when an inbox has no custom domain.

    Tracking domains are now per-inbox (``Inbox.tracking_domain``).  This
    function remains for backward-compatibility but no longer queries the DB.
    """
    return settings.base_url.rstrip("/")


def get_inbox_tracking_base(inbox, fallback_base_url: str) -> str:
    """Return the tracking base URL for *inbox*.

    If ``inbox.tracking_domain`` is set, returns ``https://<tracking_domain>``.
    Otherwise falls back to *fallback_base_url* (the app's own base URL).
    """
    td = getattr(inbox, "tracking_domain", None)
    if td:
        return f"https://{td}"
    return fallback_base_url.rstrip("/")
