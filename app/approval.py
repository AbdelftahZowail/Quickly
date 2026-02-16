"""
Test mode: approve pending emails from the Python console.

When TEST_MODE=true, the send job adds emails to pending_send instead of sending.
Run from backend Python console to send them:

  import asyncio
  from app.approval import list_pending, approve_one, approve_all

  # Show pending queue
  asyncio.run(list_pending())

  # Approve and send one (by id)
  asyncio.run(approve_one(1))

  # Approve and send all
  asyncio.run(approve_all())

Or from shell:
  python -m app.approval           # approve all
  python -m app.approval 3        # approve pending id 3
  python -m app.approval --list   # list pending only
"""
import asyncio
import sys
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import PendingSend, EmailLog
from app.sender import send_email


async def list_pending(session: Optional[AsyncSession] = None) -> list:
    """Return list of pending sends (for display). Caller can print or use in API."""
    async def _do(sess: AsyncSession):
        result = await sess.execute(
            select(PendingSend).order_by(PendingSend.created_at)
        )
        rows = result.scalars().all()
        return [
            {
                "id": p.id,
                "to": p.to_email,
                "subject": p.subject,
                "from": p.from_email,
                "campaign_id": p.campaign_id,
                "sequence_index": p.sequence_index,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in rows
        ]
    if session is not None:
        return await _do(session)
    async with AsyncSessionLocal() as sess:
        return await _do(sess)


async def approve_one(pending_id: int, session: Optional[AsyncSession] = None) -> bool:
    """Send one pending email and remove it from the queue. Returns True if sent."""
    async def _do(sess: AsyncSession):
        result = await sess.execute(select(PendingSend).where(PendingSend.id == pending_id))
        pending = result.scalar_one_or_none()
        if not pending:
            return False
        message_id = send_email(
            to_email=pending.to_email,
            subject=pending.subject,
            body=pending.body,
            from_email=pending.from_email,
            from_name=pending.from_name or "",
            reply_to_msg_id=pending.reply_to_msg_id,
            is_html=pending.is_html,
        )
        if message_id:
            sess.add(EmailLog(
                lead_id=pending.lead_id,
                campaign_id=pending.campaign_id,
                sequence_index=pending.sequence_index,
                subject=pending.subject,
                message_id=message_id,
            ))
            await sess.delete(pending)
            await sess.commit()
            return True
        return False
    if session is not None:
        return await _do(session)
    async with AsyncSessionLocal() as sess:
        return await _do(sess)


async def approve_all(session: Optional[AsyncSession] = None) -> int:
    """Send all pending emails in order. Returns count sent."""
    async def _do(sess: AsyncSession):
        result = await sess.execute(select(PendingSend).order_by(PendingSend.created_at))
        pending_list = result.scalars().all()
        count = 0
        for pending in pending_list:
            message_id = send_email(
                to_email=pending.to_email,
                subject=pending.subject,
                body=pending.body,
                from_email=pending.from_email,
                from_name=pending.from_name or "",
                reply_to_msg_id=pending.reply_to_msg_id,
                is_html=pending.is_html,
            )
            if message_id:
                sess.add(EmailLog(
                    lead_id=pending.lead_id,
                    campaign_id=pending.campaign_id,
                    sequence_index=pending.sequence_index,
                    subject=pending.subject,
                    message_id=message_id,
                ))
                await sess.delete(pending)
                count += 1
        await sess.commit()
        return count
    if session is not None:
        return await _do(session)
    async with AsyncSessionLocal() as sess:
        return await _do(sess)


def _run_list():
    async def _():
        items = await list_pending()
        for x in items:
            print(x)
        print(f"Total: {len(items)} pending")
    asyncio.run(_())


def _run_approve_one(pending_id: int):
    async def _():
        ok = await approve_one(pending_id)
        print("Sent" if ok else "Failed or not found")
    asyncio.run(_())


def _run_approve_all():
    async def _():
        n = await approve_all()
        print(f"Sent {n} email(s)")
    asyncio.run(_())


if __name__ == "__main__":
    if len(sys.argv) <= 1:
        _run_approve_all()
    elif sys.argv[1] == "--list":
        _run_list()
    else:
        try:
            _run_approve_one(int(sys.argv[1]))
        except ValueError:
            print("Usage: python -m app.approval [--list | pending_id]")
            sys.exit(1)
