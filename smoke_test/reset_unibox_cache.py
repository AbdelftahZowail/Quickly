"""Utility for completely clearing any persisted Unibox sync/cache state.

This standalone script is intended for development and smoke-test scenarios where
we want to force the next Gmail sync to start from scratch.  It deletes all rows
from the three tables that constitute the "cached" view of a Gmail inbox:

* ``gmail_sync_state``   – stores history/watch/checkpoint information
* ``gmail_thread``       – metadata for each thread
* ``gmail_message``      – individual message metadata

Running the script against a real database is destructive; it should only be
used against test or local environments.

Usage from the workspace root::

    python smoke_test/reset_unibox_cache.py
"""

import os
import sys

# ensure project root is importable just like other smoke-test helpers
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import GmailSyncState, GmailThread, GmailMessage


async def _print_counts(session: AsyncSession) -> None:
    """Print simple row counts for each table."""
    for model, name in [
        (GmailSyncState, "sync state"),
        (GmailThread, "threads"),
        (GmailMessage, "messages"),
    ]:
        res = await session.execute(select(func.count()).select_from(model))
        print(f"    {name:8}: {res.scalar_one()}")


async def _delete_all(session: AsyncSession) -> None:
    """Delete rows in the proper order (messages -> threads -> sync state)."""
    # messages reference threads, so remove them first.  sync state is independent.
    await session.execute(delete(GmailMessage))
    await session.execute(delete(GmailThread))
    await session.execute(delete(GmailSyncState))


async def delete_unibox_cache(session: AsyncSession) -> None:
    """Public helper that erases any persistent unibox sync/cache state.

    This is essentially what the standalone script does, but it exists so that
    tests can invoke the logic without spinning up a subprocess.
    """
    await _delete_all(session)
    await session.commit()


async def main() -> None:
    async with AsyncSessionLocal() as session:
        print("Unibox cache/reset script starting")
        print("counts before:")
        await _print_counts(session)

        # perform the deletions and commit them
        await _delete_all(session)
        await session.commit()

        # double-check that everything is gone
        print("counts after deletion:")
        await _print_counts(session)
        print("Unibox caches have been cleared.  A subsequent sync will repopulate them.")


if __name__ == "__main__":
    asyncio.run(main())
