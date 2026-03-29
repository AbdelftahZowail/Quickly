"""Database connection and session."""
from __future__ import annotations

import ipaddress
import os
import ssl
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from app.settings_manager import settings

# Query keys handled by libpq/SQLAlchemy ssl logic; we drop these when supplying
# our own asyncpg ``ssl`` context (e.g. Railway proxy / internal hostnames).
_LIBPQ_SSL_QUERY_KEYS = frozenset({"sslmode", "ssl", "sslfactory", "channel_binding"})


def _strip_libpq_ssl_query(url: str) -> str:
    p = urlparse(url)
    pairs = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in _LIBPQ_SSL_QUERY_KEYS
    ]
    return urlunparse(p._replace(query=urlencode(pairs)))


def _parse_pg_url_hostname(url: str) -> str:
    h = urlparse(url).hostname or ""
    return h.strip("[]").lower()


def _railway_like_db_host(url: str) -> bool:
    host = _parse_pg_url_hostname(url)
    if not host:
        return False
    return (
        "rlwy.net" in host
        or "railway.internal" in host
        or host.endswith(".railway.app")
    )


def _railway_env_db_numeric_host(url: str) -> bool:
    """Private DB URLs on Railway sometimes use a bare IP; certs never match that host."""
    if not os.getenv("RAILWAY_ENVIRONMENT", "").strip():
        return False
    host = _parse_pg_url_hostname(url)
    if not host:
        return False
    if host in ("postgres", "db"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_loopback


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _non_verifying_tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _asyncpg_relaxed_ssl_connect_args(url: str) -> dict | None:
    """Use TLS to the server but skip certificate hostname verification.

    Needed when the DB is behind a PaaS proxy (hostname ≠ cert SAN), e.g. Railway.
    Other hosts: set ``QUICKLY_DATABASE_SSL_RELAXED=1`` (or ``true`` / ``yes`` / ``on``).
    """
    if not url.startswith("postgresql+asyncpg"):
        return None
    if not (
        _env_truthy("QUICKLY_DATABASE_SSL_RELAXED")
        or _railway_like_db_host(url)
        or _railway_env_db_numeric_host(url)
    ):
        return None
    return {"ssl": _non_verifying_tls_context()}

# use TEST_DATABASE_URL during testing, otherwise fall back to configured URL
# in settings.  The application expects a PostgreSQL compatible URI; old
# SQLite logic has been removed as this project now starts fresh with
# Postgres only.
db_url = os.getenv("TEST_DATABASE_URL", settings.database_url)

# Railway (and some other platforms) provide DATABASE_URL as plain
# "postgresql://" or "postgres://".  SQLAlchemy's async engine requires the
# asyncpg dialect, so rewrite the scheme when it is missing.
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

relaxed_ssl = _asyncpg_relaxed_ssl_connect_args(db_url)
if relaxed_ssl is not None:
    db_url = _strip_libpq_ssl_query(db_url)

# If the database URL refers to SQLite we need the StaticPool/"
# check_same_thread" combination so that an in-memory database survives
# across multiple connections.  This is primarily for the test suite when
# ``TEST_DATABASE_URL`` is set to a memory URL; production (Postgres) is
# unaffected.
engine_kwargs: dict = {"echo": False}
if db_url.startswith("sqlite"):
    from sqlalchemy.pool import StaticPool

    # treat URL as URI (needed when using query params like cache=shared)
    engine_kwargs.update(
        {
            "connect_args": {"check_same_thread": False, "uri": True},
            "poolclass": StaticPool,
        }
    )
elif relaxed_ssl is not None:
    engine_kwargs["connect_args"] = relaxed_ssl

engine = create_async_engine(
    db_url,
    **engine_kwargs,
)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)
Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# SQLite-specific migration helpers removed: starting fresh with PostgreSQL.
# A clean database will be created by init_db() using SQLAlchemy metadata.


async def _run_migrations(conn) -> None:
    """Apply incremental schema changes to existing databases.

    DDL is idempotent (IF NOT EXISTS, etc.). One-time data backfills are
    gated via ``_app_schema_migrations`` so they do not overwrite newer state
    on every startup.
    """
    from sqlalchemy import text

    # Postgres-only: ADD COLUMN IF NOT EXISTS is not valid on SQLite builds used in CI;
    # tests use create_all() from models (schema already current).
    if conn.dialect.name != "postgresql":
        return

    pg_alters = [
        # 2026-03-24: ramp-up starting number (default 1 preserves old behaviour)
        "ALTER TABLE inbox ADD COLUMN IF NOT EXISTS ramp_up_start INTEGER NOT NULL DEFAULT 1",
        # 2026-03-24: track when ramp-up was last enabled (NULL = use created_at as fallback)
        "ALTER TABLE inbox ADD COLUMN IF NOT EXISTS ramp_up_started_at TIMESTAMP WITHOUT TIME ZONE NULL",
        # 2026-03-25: per-campaign enrollment status (lead.status is no longer used for this)
        "ALTER TABLE campaign_lead ADD COLUMN IF NOT EXISTS enrollment_status VARCHAR(32) NOT NULL DEFAULT 'active'",
        # 2026-03-29: Beacon standalone tracking proxy (per inbox)
        "ALTER TABLE inbox ADD COLUMN IF NOT EXISTS beacon_base_url VARCHAR(512) NULL",
        "ALTER TABLE inbox ADD COLUMN IF NOT EXISTS beacon_setup_token VARCHAR(128) NULL",
        "ALTER TABLE inbox ADD COLUMN IF NOT EXISTS beacon_webhook_secret VARCHAR(256) NULL",
        "ALTER TABLE inbox ADD COLUMN IF NOT EXISTS beacon_connected BOOLEAN NOT NULL DEFAULT FALSE",
    ]
    for stmt in pg_alters:
        await conn.execute(text(stmt))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS _app_schema_migrations (id VARCHAR(128) PRIMARY KEY)"
        )
    )

    # Backfill enrollment from legacy lead.status + interest.
    # Sync from lead.status must run once only: enrollment is authoritative afterward; API updates cl only.
    once = await conn.execute(
        text(
            """
            INSERT INTO _app_schema_migrations (id)
            VALUES ('20260325_backfill_enrollment_from_lead_legacy_status')
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """
        )
    )
    if once.fetchone() is not None:
        await conn.execute(
            text(
                """
                UPDATE campaign_lead AS cl
                SET enrollment_status = CASE
                    WHEN l.status = 'unsubscribed' THEN 'unsubscribed'
                    WHEN l.status = 'bounced' THEN 'bounced'
                    ELSE cl.enrollment_status
                END
                FROM lead AS l
                WHERE l.id = cl.lead_id
                  AND l.status IN ('unsubscribed', 'bounced')
                """
            )
        )
    await conn.execute(
        text(
            """
            UPDATE campaign_lead
            SET enrollment_status = 'wrong_person',
                interest_status = NULL
            WHERE interest_status = 'wrong_person'
            """
        )
    )
    await conn.execute(
        text(
            """
            UPDATE campaign_lead
            SET enrollment_status = 'unsubscribed',
                interest_status = NULL
            WHERE interest_status = 'unsubscribed'
            """
        )
    )


async def init_db():
    from app import models  # noqa: F401 - so Base.metadata has all tables
    from app.settings_manager import initialize_settings

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _run_migrations(conn)

    # Load settings from database into memory
    async with AsyncSessionLocal() as session:
        await initialize_settings(session)