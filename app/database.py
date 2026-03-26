"""Database connection and session."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from app.settings_manager import settings
import os

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