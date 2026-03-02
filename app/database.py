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


async def init_db():
    from app import models  # noqa: F401 - so Base.metadata has all tables
    from app.settings_manager import initialize_settings

    async with engine.begin() as conn:
        # create any completely missing tables
        await conn.run_sync(Base.metadata.create_all)

        # extra boolean columns are only needed when migrating older
        # Postgres databases; SQLite (used by the test suite) does not
        # support the variant of ALTER TABLE used here, so guard by
        # checking the dialect and swallowing any OperationalError.
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        if "sqlite" not in engine.dialect.name:
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS email_log
                        ADD COLUMN IF NOT EXISTS opened boolean NOT NULL default false,
                        ADD COLUMN IF NOT EXISTS clicked boolean NOT NULL default false;
                        """
                    )
                )
            except OperationalError:
                # ignore; columns may already exist or syntax unsupported
                pass

            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS gmail_thread
                        ADD COLUMN IF NOT EXISTS is_lead_thread boolean NOT NULL DEFAULT false,
                        ADD COLUMN IF NOT EXISTS unread_lead_reply boolean NOT NULL DEFAULT false;
                        """
                    )
                )
            except OperationalError:
                pass

    # SQLite: add missing columns individually (SQLite doesn't support
    # multi-column ALTER TABLE or IF NOT EXISTS for ADD COLUMN).
    if "sqlite" in engine.dialect.name:
        async with engine.begin() as conn:
            for col, typedef in [
                ("is_lead_thread", "BOOLEAN NOT NULL DEFAULT 0"),
                ("unread_lead_reply", "BOOLEAN NOT NULL DEFAULT 0"),
            ]:
                try:
                    await conn.execute(
                        text(f"ALTER TABLE gmail_thread ADD COLUMN {col} {typedef}")
                    )
                except Exception:
                    # Column already exists – ignore.
                    pass

    # Load settings from database into memory
    async with AsyncSessionLocal() as session:
        await initialize_settings(session)
