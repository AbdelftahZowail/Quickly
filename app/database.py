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


def _migrate_inbox_wait_minutes(conn):
    """Add wait_minutes_between column to inbox table if missing."""
    cur = conn.execute(text("PRAGMA table_info(inbox)"))
    columns = [row[1] for row in cur.fetchall()]
    if "wait_minutes_between" not in columns:
        conn.execute(text("ALTER TABLE inbox ADD COLUMN wait_minutes_between INTEGER DEFAULT 5 NOT NULL"))


def _migrate_email_log_inbox_id(conn):
    """Add inbox_id column to email_log table if missing."""
    cur = conn.execute(text("PRAGMA table_info(email_log)"))
    columns = [row[1] for row in cur.fetchall()]
    if "inbox_id" not in columns:
        conn.execute(text("ALTER TABLE email_log ADD COLUMN inbox_id INTEGER"))
        # Add foreign key constraint is done by SQLAlchemy on next create_all


def _migrate_queue_slot_unique_constraint(conn):
    """Add unique constraint on (campaign_lead_id, sequence_index) to queue_slot."""
    # Check if constraint already exists by checking indexes
    cur = conn.execute(text("PRAGMA index_list(queue_slot)"))
    indexes = [row[1] for row in cur.fetchall()]
    if "uq_campaign_lead_sequence" in indexes:
        return  # Already migrated
    
    # SQLite doesn't support adding constraints to existing tables
    # Must recreate table with the constraint
    cur = conn.execute(text("PRAGMA table_info(queue_slot)"))
    columns = cur.fetchall()
    
    # Check if table exists and has data
    cur = conn.execute(text("SELECT COUNT(*) FROM queue_slot"))
    count = cur.fetchone()[0]
    
    if count == 0:
        # No data, safe to recreate
        conn.execute(text("DROP TABLE IF EXISTS queue_slot"))
        # Let SQLAlchemy recreate it with the constraint
        from app.models import QueueSlot
        QueueSlot.__table__.create(conn, checkfirst=True)
    else:
        # Has data - preserve it with a migration
        conn.execute(text("DROP TABLE IF EXISTS queue_slot_new"))
        conn.execute(text("""
            CREATE TABLE queue_slot_new (
                id INTEGER NOT NULL PRIMARY KEY,
                campaign_lead_id INTEGER NOT NULL,
                inbox_id INTEGER NOT NULL,
                sequence_index INTEGER NOT NULL,
                scheduled_date DATETIME NOT NULL,
                position_in_day INTEGER NOT NULL,
                FOREIGN KEY(campaign_lead_id) REFERENCES campaign_lead(id),
                FOREIGN KEY(inbox_id) REFERENCES inbox(id),
                CONSTRAINT uq_campaign_lead_sequence UNIQUE (campaign_lead_id, sequence_index)
            )
        """))
        # Copy data, removing duplicates if any exist (keep first occurrence)
        conn.execute(text("""
            INSERT OR IGNORE INTO queue_slot_new 
            (id, campaign_lead_id, inbox_id, sequence_index, scheduled_date, position_in_day)
            SELECT id, campaign_lead_id, inbox_id, sequence_index, scheduled_date, position_in_day
            FROM queue_slot
            ORDER BY id
        """))
        conn.execute(text("DROP TABLE queue_slot"))
        conn.execute(text("ALTER TABLE queue_slot_new RENAME TO queue_slot"))


def _migrate_campaign_priority(conn):
    """Add priority column to campaign table if missing (default 0 = highest priority)."""
    cur = conn.execute(text("PRAGMA table_info(campaign)"))
    columns = [row[1] for row in cur.fetchall()]
    if "priority" not in columns:
        conn.execute(text("ALTER TABLE campaign ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"))


def _migrate_gmail_sync_state_columns(conn):
    """Add mirror sync state columns if missing."""
    cur = conn.execute(text("PRAGMA table_info(gmail_sync_state)"))
    columns = [row[1] for row in cur.fetchall()]
    if "anchor_history_id" not in columns:
        conn.execute(text("ALTER TABLE gmail_sync_state ADD COLUMN anchor_history_id VARCHAR(64) DEFAULT ''"))
    if "latest_history_id" not in columns:
        conn.execute(text("ALTER TABLE gmail_sync_state ADD COLUMN latest_history_id VARCHAR(64) DEFAULT ''"))
    if "oldest_internal_date" not in columns:
        conn.execute(text("ALTER TABLE gmail_sync_state ADD COLUMN oldest_internal_date BIGINT"))
    # Backfill from legacy field when possible.
    conn.execute(
        text(
            "UPDATE gmail_sync_state "
            "SET anchor_history_id = COALESCE(NULLIF(anchor_history_id, ''), last_history_id, ''), "
            "latest_history_id = COALESCE(NULLIF(latest_history_id, ''), last_history_id, '')"
        )
    )


async def init_db():
    from app import models  # noqa: F401 - so Base.metadata has all tables
    from app.settings_manager import initialize_settings
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_campaign_inbox)
        await conn.run_sync(_migrate_inbox_provider)
        await conn.run_sync(_migrate_thread_id)
        await conn.run_sync(_migrate_inbox_wait_minutes)
        await conn.run_sync(_migrate_email_log_inbox_id)
        await conn.run_sync(_migrate_queue_slot_unique_constraint)
        await conn.run_sync(_migrate_campaign_priority)
        await conn.run_sync(_migrate_gmail_sync_state_columns)
    
    # Load settings from database into memory
    async with AsyncSessionLocal() as session:
        await initialize_settings(session)
