"""Fully reset persisted Unibox email cache and sync checkpoints.

This script is destructive and should only be used in local/dev/smoke-test DBs.
It removes:
  - gmail_attachment rows
  - gmail_message rows
  - gmail_thread rows
  - gmail_sync_state rows

Deleting gmail_sync_state is what forces the next sync to run the initial-sync
path again.

Usage:
  python smoke_test/reset_unibox_email_cache_full.py
  python smoke_test/reset_unibox_email_cache_full.py --inbox-id 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure project root is importable (same pattern used by other smoke_test scripts).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import AsyncSessionLocal
from app.models import GmailAttachment, GmailMessage, GmailSyncState, GmailThread


async def _count_rows(session: AsyncSession, model: type, inbox_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(model)
    if inbox_id is not None and hasattr(model, "inbox_id"):
        stmt = stmt.where(model.inbox_id == inbox_id)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def _print_counts(session: AsyncSession, inbox_id: int | None) -> None:
    scope = f"inbox_id={inbox_id}" if inbox_id is not None else "all inboxes"
    print(f"Scope: {scope}")
    print(f"  attachments : {await _count_rows(session, GmailAttachment, inbox_id)}")
    print(f"  messages    : {await _count_rows(session, GmailMessage, inbox_id)}")
    print(f"  threads     : {await _count_rows(session, GmailThread, inbox_id)}")
    print(f"  sync_state  : {await _count_rows(session, GmailSyncState, inbox_id)}")


async def reset_unibox_email_cache_full(session: AsyncSession, *, inbox_id: int | None = None) -> None:
    """Delete all persisted unibox cache/sync rows (optionally for a single inbox)."""
    models = [GmailAttachment, GmailMessage, GmailThread, GmailSyncState]
    for model in models:
        stmt = delete(model)
        if inbox_id is not None and hasattr(model, "inbox_id"):
            stmt = stmt.where(model.inbox_id == inbox_id)
        await session.execute(stmt)
    await session.commit()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset persisted Unibox email cache and sync checkpoints.")
    parser.add_argument(
        "--inbox-id",
        type=int,
        default=None,
        help="Optional Gmail inbox id to reset. Omit to reset all inboxes.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    inbox_id: int | None = args.inbox_id

    async with AsyncSessionLocal() as session:
        print("Unibox full cache reset starting...")
        print("Counts before:")
        await _print_counts(session, inbox_id)

        await reset_unibox_email_cache_full(session, inbox_id=inbox_id)

        print("Counts after:")
        await _print_counts(session, inbox_id)
        print("Done. Next sync for affected inbox(es) will run initial sync again.")


if __name__ == "__main__":
    asyncio.run(main())

