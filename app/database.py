"""Database connection and session."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

# Import settings from the new settings manager (not config.py)
from app.settings_manager import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
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


def _migrate_campaign_inbox(conn):
    """One-time migration: drop campaign.inbox_id and backfill campaign_inbox (old schema)."""
    cur = conn.execute(text("PRAGMA table_info(campaign)"))
    columns = [row[1] for row in cur.fetchall()]
    if "inbox_id" not in columns:
        return
    # Ensure campaign_inbox exists (create_all already ran)
    cur = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='campaign_inbox'"
    ))
    if cur.fetchone():
        conn.execute(text(
            "INSERT OR IGNORE INTO campaign_inbox (campaign_id, inbox_id, position) "
            "SELECT id, inbox_id, 0 FROM campaign WHERE inbox_id IS NOT NULL"
        ))
    # SQLite 3.35+ supports DROP COLUMN
    try:
        conn.execute(text("ALTER TABLE campaign DROP COLUMN inbox_id"))
    except Exception:
        # Older SQLite: recreate campaign without inbox_id
        conn.execute(text(
            "CREATE TABLE campaign_new (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(255) NOT NULL, "
            "sending_days JSON, sending_hours_start VARCHAR(5), sending_hours_end VARCHAR(5), "
            "wait_minutes_between INTEGER, stop_on_reply BOOLEAN, created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO campaign_new (id, name, sending_days, sending_hours_start, sending_hours_end, "
            "wait_minutes_between, stop_on_reply, created_at) "
            "SELECT id, name, sending_days, sending_hours_start, sending_hours_end, "
            "wait_minutes_between, stop_on_reply, created_at FROM campaign"
        ))
        conn.execute(text("DROP TABLE campaign"))
        conn.execute(text("ALTER TABLE campaign_new RENAME TO campaign"))


def _migrate_inbox_provider(conn):
    """Add provider column to inbox table if missing."""
    cur = conn.execute(text("PRAGMA table_info(inbox)"))
    columns = [row[1] for row in cur.fetchall()]
    if "provider" not in columns:
        conn.execute(text("ALTER TABLE inbox ADD COLUMN provider VARCHAR(32) DEFAULT 'resend'"))


def _migrate_thread_id(conn):
    """Add thread_id column to email_log and pending_send tables if missing."""
    for table in ("email_log", "pending_send"):
        cur = conn.execute(text(f"PRAGMA table_info({table})"))
        columns = [row[1] for row in cur.fetchall()]
        if "thread_id" not in columns:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN thread_id VARCHAR(512) DEFAULT NULL"))


async def init_db():
    from app import models  # noqa: F401 - so Base.metadata has all tables
    from app.settings_manager import initialize_settings
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_campaign_inbox)
        await conn.run_sync(_migrate_inbox_provider)
        await conn.run_sync(_migrate_thread_id)
    
    # Load settings from database into memory
    async with AsyncSessionLocal() as session:
        await initialize_settings(session)
