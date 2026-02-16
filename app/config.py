"""Application configuration."""
from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./campaign.db"
    # Base URL (used for OAuth redirect URIs, links, etc.)
    base_url: str = "http://localhost:8000"
    # Email: "resend" uses Resend API, "smtp" uses SMTP, "gmail" uses Gmail OAuth
    email_provider: Literal["resend", "smtp", "gmail"] = "resend"
    # Resend.com API (https://resend.com/api-keys)
    resend_api_key: str = ""
    # SMTP (used when email_provider=smtp)
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    # Google OAuth 2.0 for Gmail / G Suite
    google_client_id: str = ""
    google_client_secret: str = ""
    # Background job: how often to check for due emails (default every 1 min)
    queue_check_interval_minutes: int = 1
    # Test mode: queue emails for manual approval instead of sending (see approval module)
    test_mode: bool = False

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.base_url.rstrip('/')}/oauth/google/callback"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
