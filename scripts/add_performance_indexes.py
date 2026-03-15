"""
Migration: add composite performance indexes.

Run once against an existing database that was created before these indexes
were added to models.py.  Safe to run multiple times — all statements use
CREATE INDEX IF NOT EXISTS so they are no-ops on a database that already has
the index.

Usage (from the project root, with venv active):
    python scripts/add_performance_indexes.py

The script resolves DATABASE_URL the same way the application does, so make
sure the environment variable is set (or a .env file is loaded) before running.
"""
import asyncio
import os
import sys

# allow import of app modules from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app.database import engine

# Each tuple is (index_name, table, columns, description)
# Written as raw DDL so they apply even when SQLAlchemy metadata isn't fully
# available (e.g. a stripped production image).
INDEXES = [
    (
        "ix_queue_slot_inbox_date",
        "queue_slot",
        "(inbox_id, scheduled_date)",
        "Send job: filters slots by inbox + date on every tick",
    ),
    (
        "ix_email_log_inbox_sent_at",
        "email_log",
        "(inbox_id, sent_at)",
        "Send job: daily-count and last-sent-time per inbox",
    ),
    (
        "ix_email_log_lead_campaign",
        "email_log",
        "(lead_id, campaign_id)",
        "Follow-up thread-building and inbox-persistence checks",
    ),
    (
        "ix_email_log_campaign",
        "email_log",
        "(campaign_id)",
        "Campaign-level aggregate queries (list_campaigns analytics)",
    ),
    (
        "ix_campaign_lead_campaign_id",
        "campaign_lead",
        "(campaign_id)",
        "Bulk analytics and full-recalculation scans by campaign",
    ),
    (
        "ix_campaign_lead_lead_campaign",
        "campaign_lead",
        "(lead_id, campaign_id)",
        "Inbox-persistence lookup and duplicate-enrollment guard",
    ),
    (
        "ix_lead_reply_lead_campaign",
        "lead_reply",
        "(lead_id, campaign_id)",
        "stop_on_reply check runs inside per-slot send loop",
    ),
]


async def run():
    print("Connecting to database …")
    async with engine.begin() as conn:
        for name, table, columns, description in INDEXES:
            ddl = f"CREATE INDEX IF NOT EXISTS {name} ON {table} {columns};"
            print(f"  [{table}] {name} — {description}")
            await conn.execute(text(ddl))
    print("\nAll indexes applied successfully.")


if __name__ == "__main__":
    asyncio.run(run())
