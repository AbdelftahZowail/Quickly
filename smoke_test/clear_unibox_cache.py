"""Utility to purge unibox cache rows from the database.

Run this script from the project root:

    python smoke_test/clear_unibox_cache.py

It will connect using the same async engine used by the app and delete
all records from the ``unibox_cache`` table.  This is handy when testing
UI behaviour with a fresh cache.
"""

import sys
import os
import asyncio

# ensure project root is on path so imports like ``from app...`` work
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import engine
from app.models import UniboxCache
from sqlalchemy import delete


async def main():
    async with engine.begin() as conn:
        await conn.execute(delete(UniboxCache))
        await conn.commit()
    print("unibox cache cleared")


if __name__ == "__main__":
    asyncio.run(main())
