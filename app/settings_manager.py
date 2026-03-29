"""Application settings manager - stores all configuration in database.

Replaces the old .env-based config system. Settings are loaded from the database
at startup and cached in memory for fast synchronous access.

Environment variables are still respected on startup (see README or
SETUP_POSTGRES_WINDOWS.md). A `.env` file in the project root will be loaded by
python-dotenv automatically.

``BASE_URL``: if set in the environment (non-empty after stripping), it always
wins over the value restored from the database (e.g. after a backup restore) and
the database row is updated to match on each startup.
"""
from dotenv import load_dotenv
import logging
from sqlalchemy.ext.asyncio import AsyncSession

# load environment variables from .env file if present
load_dotenv()

log = logging.getLogger("quickly.settings")


class Settings:
    """In-memory cache of application settings loaded from database."""
    
    def __init__(self):
        # Database
        # By default the application connects to PostgreSQL.  Override via
        # ``DATABASE_URL`` (or ``TEST_DATABASE_URL`` when running tests).
        import os

        # some defaults can be overridden via environment variables; most values
        # stored in the database supersede these once settings are loaded, except
        # ``BASE_URL`` when present in the environment (see ``initialize_settings``).
        self.database_url: str = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost/quickly",
        )
        
        # Base URL (used by Google redirect calculation, links, etc.). Initial
        # default from env; DB restore may overwrite until startup reconciliation.
        self.base_url: str = os.getenv("BASE_URL", "http://localhost:8000")
        
        # Google OAuth (also stored in AppSetting for backward compat)
        self.google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
        self.google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

        # Office 365 / Microsoft OAuth
        self.office365_client_id: str = os.getenv("OFFICE365_CLIENT_ID", "")
        self.office365_client_secret: str = os.getenv("OFFICE365_CLIENT_SECRET", "")
        self.office365_tenant_id: str = os.getenv("OFFICE365_TENANT_ID", "common")
        
        # Background job interval
        self.queue_check_interval_minutes: int = 1
        
        # Test mode
        # the default may be overridden by the TEST_MODE environment variable
        # (true/1/yes for enabled).  The value is persisted in the database
        # once the application initializes; DB entries always take
        # precedence thereafter.
        self.test_mode: bool = os.getenv("TEST_MODE", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        # Time travel offset (integer days). 0 means real now.
        # Can be overridden via the TIME_OFFSET_DAYS environment variable when
        # the application first starts (useful for tests or quick manual
        # adjustments).  The live value is stored in the database and usually
        # controlled via the simulate_queue_2_days script or the web UI.
        try:
            self.time_offset_days: int = int(os.getenv("TIME_OFFSET_DAYS", "0"))
        except ValueError:
            self.time_offset_days = 0
    
    @property
    def google_redirect_uri(self) -> str:
        return f"{self.base_url.rstrip('/')}/oauth/google/callback"

    @property
    def office365_redirect_uri(self) -> str:
        return f"{self.base_url.rstrip('/')}/oauth/office365/callback"

    @property
    def app_google_redirect_uri(self) -> str:
        return f"{self.base_url.rstrip('/')}/oauth/app/google/callback"

    @property
    def app_microsoft_redirect_uri(self) -> str:
        return f"{self.base_url.rstrip('/')}/oauth/app/microsoft/callback"
    
    def reload_from_dict(self, data: dict):
        """Update settings from a dictionary (typically loaded from DB)."""
        if "database_url" in data:
            self.database_url = data["database_url"]
        if "base_url" in data:
            self.base_url = data["base_url"]
        # Google OAuth credentials are supplied exclusively via environment
        # variables (or a `.env` file) and are **not** read from the
        # database.  Prior releases stored them in the `app_setting` table,
        # but that behavior was removed so the process always uses the value
        # that was present when it started.
        if "queue_check_interval_minutes" in data:
            self.queue_check_interval_minutes = int(data["queue_check_interval_minutes"])
        if "test_mode" in data:
            self.test_mode = str(data["test_mode"]).lower() in ("true", "1", "yes")
        if "time_offset_days" in data:
            try:
                self.time_offset_days = int(data["time_offset_days"])
            except Exception:
                self.time_offset_days = 0


# Global settings instance
settings = Settings()


# Database interaction functions
async def load_settings_from_db(db: AsyncSession) -> dict:
    """Load all settings from database into a dictionary."""
    from sqlalchemy import select
    from app.models import AppSetting
    
    result = await db.execute(select(AppSetting))
    rows = result.scalars().all()
    
    data = {}
    for row in rows:
        data[row.key] = row.value
    
    return data


async def save_setting_to_db(db: AsyncSession, key: str, value: str) -> None:
    """Save a single setting to the database."""
    from sqlalchemy import select
    from app.models import AppSetting
    
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    
    await db.flush()


async def save_all_settings_to_db(db: AsyncSession, data: dict) -> None:
    """Save multiple settings to the database."""
    for key, value in data.items():
        await save_setting_to_db(db, key, str(value))


async def _sync_base_url_from_environment(db: AsyncSession) -> None:
    """Apply ``BASE_URL`` from the process environment over any DB value.

    Restores from backup can reintroduce an old public URL; deployment config
    (compose, platform env, ``.env``) should be authoritative whenever it is set.
    """
    import os

    env_base = (os.getenv("BASE_URL") or "").strip()
    if not env_base:
        return
    settings.base_url = env_base
    await save_setting_to_db(db, "base_url", env_base)
    await db.commit()
    log.info("BASE_URL taken from environment (database value updated to match).")


async def reload_settings(db: AsyncSession):
    """Reload settings from database into memory cache."""
    data = await load_settings_from_db(db)
    settings.reload_from_dict(data)
    log.info("Settings reloaded from database")


async def initialize_settings(db: AsyncSession):
    """Initialize settings at application startup.
    
    Loads from database if available, otherwise uses defaults.
    """
    data = await load_settings_from_db(db)
    
    if not data:
        # First run - save defaults to database
        log.info("No settings found in database, saving defaults")
        default_data = {
            "database_url": settings.database_url,
            "base_url": settings.base_url,
            "queue_check_interval_minutes": str(settings.queue_check_interval_minutes),
            "test_mode": str(settings.test_mode),
            "time_offset_days": str(settings.time_offset_days),
        }
        await save_all_settings_to_db(db, default_data)
        await db.commit()
    else:
        # Remove any legacy OAuth entries so they can't accidentally
        # confuse administrators looking at the table.  These values are
        # ignored by the running process but keeping them serves no purpose.
        from sqlalchemy import delete
        from app.models import AppSetting

        await db.execute(
            delete(AppSetting).where(
                AppSetting.key.in_(["google_client_id", "google_client_secret"])
            )
        )
        await db.commit()

        settings.reload_from_dict(data)
        log.info("Settings loaded from database")

    # When BASE_URL is configured in the environment, it overrides DB (including
    # values from an older backup) and the stored setting is brought in sync.
    await _sync_base_url_from_environment(db)

    # Ensure all persistent security secrets exist (generates + saves if absent).
    await _ensure_secrets(db)


async def _ensure_secrets(db: AsyncSession) -> None:
    """Auto-generate and persist security secrets that are missing from both
    the environment and the database.

    Priority: env var > existing DB value > auto-generated value.
    This runs every startup so that adding an env var later overrides a
    previously auto-generated DB value.
    """
    import os as _os
    import secrets as _secrets

    # Fresh snapshot so we see anything written by initialize_settings above.
    data = await load_settings_from_db(db)
    changed = False

    # ------------------------------------------------------------------ #
    # 1.  JWT / API-key signing secret (QUICKLY_SECRET_KEY)               #
    # ------------------------------------------------------------------ #
    env_secret = _os.getenv("QUICKLY_SECRET_KEY", "")
    db_secret = data.get("quickly_secret_key", "")

    if env_secret:
        final_secret = env_secret
        if not db_secret:
            await save_setting_to_db(db, "quickly_secret_key", env_secret)
            changed = True
    elif db_secret:
        final_secret = db_secret
    else:
        final_secret = _secrets.token_urlsafe(64)
        await save_setting_to_db(db, "quickly_secret_key", final_secret)
        changed = True
        log.info("Auto-generated QUICKLY_SECRET_KEY and stored in the database.")

    try:
        from app.auth import _reinit_secret_key
        _reinit_secret_key(final_secret)
    except Exception:
        log.exception("Failed to reinitialise auth secret key.")

    # ------------------------------------------------------------------ #
    # 2.  Fernet encryption key (QUICKLY_ENCRYPTION_KEY)                  #
    # ------------------------------------------------------------------ #
    env_enc = _os.getenv("QUICKLY_ENCRYPTION_KEY", "")
    db_enc = data.get("quickly_encryption_key", "")

    if env_enc:
        final_enc = env_enc
        if not db_enc:
            await save_setting_to_db(db, "quickly_encryption_key", env_enc)
            changed = True
    elif db_enc:
        final_enc = db_enc
    else:
        from cryptography.fernet import Fernet
        final_enc = Fernet.generate_key().decode()
        await save_setting_to_db(db, "quickly_encryption_key", final_enc)
        changed = True
        log.info("Auto-generated QUICKLY_ENCRYPTION_KEY and stored in the database.")

    try:
        from app.security import init_encryption
        init_encryption(final_enc)
    except Exception:
        log.exception("Failed to reinitialise Fernet encryption.")

    # ------------------------------------------------------------------ #
    # 3.  Gmail push webhook token (GMAIL_PUSH_WEBHOOK_TOKEN)             #
    # ------------------------------------------------------------------ #
    db_token = data.get("gmail_push_webhook_token", "")
    if not db_token:
        env_token = _os.getenv("GMAIL_PUSH_WEBHOOK_TOKEN", "")
        if env_token:
            await save_setting_to_db(db, "gmail_push_webhook_token", env_token)
            changed = True
            log.info("Seeded gmail_push_webhook_token from environment.")
        else:
            new_token = _secrets.token_urlsafe(32)
            await save_setting_to_db(db, "gmail_push_webhook_token", new_token)
            changed = True
            log.info("Auto-generated gmail_push_webhook_token and stored in the database.")

    # ------------------------------------------------------------------ #
    # 4.  Gmail push topic (GMAIL_PUSH_TOPIC) – seed from env if present  #
    # ------------------------------------------------------------------ #
    db_topic = data.get("gmail_push_topic", "")
    if not db_topic:
        env_topic = _os.getenv("GMAIL_PUSH_TOPIC", "")
        if env_topic:
            await save_setting_to_db(db, "gmail_push_topic", env_topic)
            changed = True
            log.info("Seeded gmail_push_topic from environment.")

    if changed:
        await db.commit()


async def update_setting(db: AsyncSession, key: str, value: str):
    """Update a single setting in DB and reload into memory."""
    await save_setting_to_db(db, key, value)
    await db.commit()
    await reload_settings(db)


async def update_settings(db: AsyncSession, data: dict):
    """Update multiple settings in DB and reload into memory."""
    await save_all_settings_to_db(db, data)
    await db.commit()
    await reload_settings(db)
