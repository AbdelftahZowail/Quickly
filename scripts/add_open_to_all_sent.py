"""Ad-hoc helper for the database.

Insert an "open" event against every row currently in ``email_log`` and
mark the log opened.  Useful when you just want to fake opens for all of
a campaign's emails (or the entire database) without hitting the normal
webhook route.

Usage
-----
    python scripts/add_open_to_all_sent.py

The script uses :class:`app.database.AsyncSessionLocal` so it will respect
your normal configuration (e.g. local Postgres, sqlite, etc.).
"""

import os
import sys

# ensure the project root is on sys.path when the script is launched from
# inside the ``scripts`` directory itself.  this matches the pattern used
# in other smoke-test helpers.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import EmailLog, EmailOpen


async def main():
    added = 0
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(EmailLog))
        logs = result.scalars().all()

        for log in logs:
            # record a synthetic open regardless of whether the row was
            # previously marked as opened.  the ip address is arbitrary;
            # use whatever makes sense for your testing.
            op = EmailOpen(email_log_id=log.id, ip_address="127.0.0.1")
            session.add(op)

            if not log.opened:
                log.opened = True

            added += 1

        await session.commit()

    print(f"attached open event to {added} email logs (total rows: {len(logs)})")


if __name__ == "__main__":
    asyncio.run(main())
