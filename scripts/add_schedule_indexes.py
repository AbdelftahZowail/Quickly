"""Create schedule-related indexes for existing databases."""
import asyncio
from sqlalchemy import text

from app.database import engine


async def main() -> None:
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_queue_slot_scheduled_date ON queue_slot (scheduled_date)",
        "CREATE INDEX IF NOT EXISTS ix_queue_slot_scheduled_date_pos ON queue_slot (scheduled_date, position_in_day)",
        "CREATE INDEX IF NOT EXISTS ix_email_log_sent_at ON email_log (sent_at)",
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))


if __name__ == "__main__":
    asyncio.run(main())
