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

            # Campaign: tracking & plain-text options (added with unsubscribe feature)
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS campaign
                        ADD COLUMN IF NOT EXISTS track_opens boolean NOT NULL DEFAULT false,
                        ADD COLUMN IF NOT EXISTS track_clicks boolean NOT NULL DEFAULT false,
                        ADD COLUMN IF NOT EXISTS add_unsubscribe_header boolean NOT NULL DEFAULT true,
                        ADD COLUMN IF NOT EXISTS send_first_as_text boolean NOT NULL DEFAULT false,
                        ADD COLUMN IF NOT EXISTS send_all_as_text boolean NOT NULL DEFAULT false;
                        """
                    )
                )
            except OperationalError:
                pass

            # Campaign: timezone support
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS campaign
                        ADD COLUMN IF NOT EXISTS timezone varchar(64);
                        """
                    )
                )
            except OperationalError:
                pass

            # Sequence: explicit HTML flag
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS sequence
                        ADD COLUMN IF NOT EXISTS is_html boolean;
                        """
                    )
                )
            except OperationalError:
                pass

            # Sequence: preview/preheader text for HTML emails
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS sequence
                        ADD COLUMN IF NOT EXISTS preview_text varchar(512);
                        """
                    )
                )
            except OperationalError:
                pass

            # CampaignLead: AI interest classification fields
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS campaign_lead
                        ADD COLUMN IF NOT EXISTS interest_status varchar(32),
                        ADD COLUMN IF NOT EXISTS sending_paused boolean NOT NULL DEFAULT false;
                        """
                    )
                )
            except OperationalError:
                pass

            # EmailLog: A/B variant tracking
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS email_log
                        ADD COLUMN IF NOT EXISTS variant_id integer
                            REFERENCES sequence_variant(id) ON DELETE SET NULL;
                        """
                    )
                )
            except OperationalError:
                pass

            # EmailLog: open tracking token (non-incremental)
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS email_log
                        ADD COLUMN IF NOT EXISTS open_token varchar(32);
                        """
                    )
                )
            except OperationalError:
                pass
            # Backfill existing rows with a random token (md5 is built-in, no pgcrypto needed)
            try:
                await conn.execute(
                    text(
                        """
                        UPDATE email_log SET open_token = substring(md5(random()::text || clock_timestamp()::text || id::text), 1, 32)
                        WHERE open_token IS NULL;
                        """
                    )
                )
            except OperationalError:
                pass
            # Create unique index after backfill
            try:
                await conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS ix_email_log_open_token
                        ON email_log (open_token) WHERE open_token IS NOT NULL;
                        """
                    )
                )
            except OperationalError:
                pass

            # Campaign: public_id (non-incremental)
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS campaign
                        ADD COLUMN IF NOT EXISTS public_id varchar(16);
                        """
                    )
                )
            except OperationalError:
                pass
            # Backfill existing campaigns with a random public_id (md5 built-in, no pgcrypto)
            try:
                await conn.execute(
                    text(
                        """
                        UPDATE campaign SET public_id = substring(md5(random()::text || clock_timestamp()::text || id::text), 1, 16)
                        WHERE public_id IS NULL;
                        """
                    )
                )
            except OperationalError:
                pass
            # Create unique index after backfill
            try:
                await conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS ix_campaign_public_id
                        ON campaign (public_id) WHERE public_id IS NOT NULL;
                        """
                    )
                )
            except OperationalError:
                pass
            # EmailLog: format_override
            # original migrations used a very short varchar(8); expand
            # to match the SQLAlchemy model (String(64)) and ensure
            # existing databases are altered as needed.
            try:
                # adding with VARCHAR(64) covers new databases; if the
                # column already exists this is a no-op
                await conn.execute(
                    text(
                        """
                        ALTER TABLE email_log
                        ADD COLUMN IF NOT EXISTS format_override varchar(64);
                        """
                    )
                )
                # guarantee the column is at least 64 characters long
                # (Postgres silently succeeds if the type is already
                # wide enough).  This will convert old varchar(8) cols.
                await conn.execute(
                    text(
                        """
                        ALTER TABLE email_log
                        ALTER COLUMN format_override TYPE varchar(64);
                        """
                    )
                )
            except OperationalError:
                pass

            # Inbox: ramp-up warm-up fields
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS inbox
                        ADD COLUMN IF NOT EXISTS ramp_up_enabled boolean NOT NULL DEFAULT false,
                        ADD COLUMN IF NOT EXISTS ramp_up_period_days integer NOT NULL DEFAULT 42;
                        """
                    )
                )
            except OperationalError:
                pass

            # Inbox: pause flag
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS inbox
                        ADD COLUMN IF NOT EXISTS paused boolean NOT NULL DEFAULT false;
                        """
                    )
                )
            except OperationalError:
                pass

            # Lead: email verification fields
            try:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE IF EXISTS lead
                        ADD COLUMN IF NOT EXISTS email_verification_status varchar(32),
                        ADD COLUMN IF NOT EXISTS email_verification_result jsonb;
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
            # Campaign tracking / plain-text options
            for col, typedef in [
                ("track_opens", "BOOLEAN NOT NULL DEFAULT 0"),
                ("track_clicks", "BOOLEAN NOT NULL DEFAULT 0"),
                ("add_unsubscribe_header", "BOOLEAN NOT NULL DEFAULT 1"),
                ("send_first_as_text", "BOOLEAN NOT NULL DEFAULT 0"),
                ("send_all_as_text", "BOOLEAN NOT NULL DEFAULT 0"),
                ("timezone", "VARCHAR(64)"),
            ]:
                try:
                    await conn.execute(
                        text(f"ALTER TABLE campaign ADD COLUMN {col} {typedef}")
                    )
                except Exception:
                    pass
            # Sequence explicit HTML flag
            try:
                await conn.execute(text("ALTER TABLE sequence ADD COLUMN is_html BOOLEAN"))
            except Exception:
                pass
            # Sequence preview text
            try:
                await conn.execute(text("ALTER TABLE sequence ADD COLUMN preview_text VARCHAR(512)"))
            except Exception:
                pass
            # CampaignLead: AI interest classification fields
            for col, typedef in [
                ("interest_status", "VARCHAR(32)"),
                ("sending_paused", "BOOLEAN NOT NULL DEFAULT 0"),
            ]:
                try:
                    await conn.execute(
                        text(f"ALTER TABLE campaign_lead ADD COLUMN {col} {typedef}")
                    )
                except Exception:
                    pass
            # EmailLog: A/B variant tracking
            try:
                await conn.execute(
                    text("ALTER TABLE email_log ADD COLUMN variant_id INTEGER REFERENCES sequence_variant(id)")
                )
            except Exception:
                pass
            # EmailLog: open tracking token
            try:
                await conn.execute(
                    text("ALTER TABLE email_log ADD COLUMN open_token VARCHAR(32)")
                )
            except Exception:
                pass
            # Campaign: public_id
            try:
                await conn.execute(
                    text("ALTER TABLE campaign ADD COLUMN public_id VARCHAR(16)")
                )
            except Exception:
                pass
            # EmailLog: format_override
            # for SQLite we don't enforce length anyway, but use 64 for
            # consistency with the model
            try:
                await conn.execute(
                    text("ALTER TABLE email_log ADD COLUMN format_override VARCHAR(64)")
                )
            except Exception:
                pass
            # Inbox: ramp-up warm-up fields
            for col, typedef in [
                ("ramp_up_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
                ("ramp_up_period_days", "INTEGER NOT NULL DEFAULT 42"),
            ]:
                try:
                    await conn.execute(
                        text(f"ALTER TABLE inbox ADD COLUMN {col} {typedef}")
                    )
                except Exception:
                    pass
            # Inbox: pause flag
            try:
                await conn.execute(
                    text("ALTER TABLE inbox ADD COLUMN paused BOOLEAN NOT NULL DEFAULT 0")
                )
            except Exception:
                pass
            # Lead: email verification fields
            for col, typedef in [
                ("email_verification_status", "VARCHAR(32)"),
                ("email_verification_result", "TEXT"),
            ]:
                try:
                    await conn.execute(
                        text(f"ALTER TABLE lead ADD COLUMN {col} {typedef}")
                    )
                except Exception:
                    pass

    # Load settings from database into memory
    async with AsyncSessionLocal() as session:
        await initialize_settings(session)
