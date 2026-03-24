"""Migration: add ramp_up_start column to inbox table.

Run once against an existing database:
    python scripts/add_ramp_up_start.py
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.database import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "ALTER TABLE inbox ADD COLUMN IF NOT EXISTS ramp_up_start INTEGER NOT NULL DEFAULT 1"
            )
        )
        await db.commit()
        print("Migration complete: ramp_up_start column added (or already existed).")


if __name__ == "__main__":
    asyncio.run(main())
