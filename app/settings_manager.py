"""Application settings manager - stores all configuration in database.

Replaces the old .env-based config system. Settings are loaded from the database
at startup and cached in memory for fast synchronous access.
"""
import logging
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("campaign_engine.settings")


class Settings:
    """In-memory cache of application settings loaded from database."""
    
    def __init__(self):
        # Database
        self.database_url: str = "sqlite+aiosqlite:///./campaign.db"
        
        # Base URL
        self.base_url: str = "http://localhost:8000"
        
        # Email provider
        self.email_provider: Literal["resend", "smtp", "gmail"] = "resend"
        
        # Resend API
        self.resend_api_key: str = ""
        
        # SMTP settings
        self.smtp_host: str = "localhost"
        self.smtp_port: int = 1025
        self.smtp_user: str = ""
        self.smtp_password: str = ""
        self.smtp_use_tls: bool = False
        
        # Google OAuth (also stored in AppSetting for backward compat)
        self.google_client_id: str = ""
        self.google_client_secret: str = ""
        
        # Background job interval
        self.queue_check_interval_minutes: int = 1
        
        # Test mode
        self.test_mode: bool = False

        # Time travel offset (integer days). 0 means real now.
        self.time_offset_days: int = 0
    
    @property
    def google_redirect_uri(self) -> str:
        return f"{self.base_url.rstrip('/')}/oauth/google/callback"
    
    def reload_from_dict(self, data: dict):
        """Update settings from a dictionary (typically loaded from DB)."""
        if "database_url" in data:
            self.database_url = data["database_url"]
        if "base_url" in data:
            self.base_url = data["base_url"]
        if "email_provider" in data:
            self.email_provider = data["email_provider"]
        if "resend_api_key" in data:
            self.resend_api_key = data["resend_api_key"]
        if "smtp_host" in data:
            self.smtp_host = data["smtp_host"]
        if "smtp_port" in data:
            self.smtp_port = int(data["smtp_port"])
        if "smtp_user" in data:
            self.smtp_user = data["smtp_user"]
        if "smtp_password" in data:
            self.smtp_password = data["smtp_password"]
        if "smtp_use_tls" in data:
            self.smtp_use_tls = str(data["smtp_use_tls"]).lower() in ("true", "1", "yes")
        if "google_client_id" in data:
            self.google_client_id = data["google_client_id"]
        if "google_client_secret" in data:
            self.google_client_secret = data["google_client_secret"]
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
            "email_provider": settings.email_provider,
            "resend_api_key": settings.resend_api_key,
            "smtp_host": settings.smtp_host,
            "smtp_port": str(settings.smtp_port),
            "smtp_user": settings.smtp_user,
            "smtp_password": settings.smtp_password,
            "smtp_use_tls": str(settings.smtp_use_tls),
            "google_client_id": settings.google_client_id,
            "google_client_secret": settings.google_client_secret,
            "queue_check_interval_minutes": str(settings.queue_check_interval_minutes),
            "test_mode": str(settings.test_mode),
            "time_offset_days": str(settings.time_offset_days),
        }
        await save_all_settings_to_db(db, default_data)
        await db.commit()
    else:
        settings.reload_from_dict(data)
        log.info("Settings loaded from database")


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
