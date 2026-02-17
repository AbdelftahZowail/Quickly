"""Test mode API: status, pending queue, approve/reject (for frontend approval page)."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func

from app.settings_manager import settings
from app.database import get_db
from app.models import PendingSend, EmailLog, Campaign
from app.sender import send_email

log = logging.getLogger("campaign_engine.routes")

router = APIRouter(prefix="/api/test", tags=["test"])


@router.get("/status")
async def test_status():
    """Return whether test mode is on."""
    return {"test_mode": settings.test_mode}


@router.get("/pending")
async def get_pending(db: AsyncSession = Depends(get_db)):
    """Return pending emails waiting for approval. Includes campaign name for display."""
    if not settings.test_mode:
        return []
    result = await db.execute(
        select(PendingSend, Campaign.name)
        .outerjoin(Campaign, PendingSend.campaign_id == Campaign.id)
        .order_by(PendingSend.created_at)
    )
    rows = result.all()
    return [
        {
            "id": p.id,
            "to": p.to_email,
            "from": p.from_email,
            "from_name": p.from_name or "",
            "subject": p.subject,
            "body": p.body,
            "is_html": p.is_html,
            "campaign_id": p.campaign_id,
            "campaign_name": campaign_name or f"Campaign {p.campaign_id}",
            "sequence_index": p.sequence_index,
            "lead_id": p.lead_id,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p, campaign_name in rows
    ]


@router.post("/pending/{pending_id}/approve")
async def approve_one(pending_id: int, db: AsyncSession = Depends(get_db)):
    """Approve and send one pending email."""
    result = await db.execute(select(PendingSend).where(PendingSend.id == pending_id))
    pending = result.scalar_one_or_none()
    if not pending:
        raise HTTPException(404, "Pending email not found")
    result = send_email(
        to_email=pending.to_email,
        subject=pending.subject,
        body=pending.body,
        from_email=pending.from_email,
        from_name=pending.from_name or "",
        reply_to_msg_id=pending.reply_to_msg_id,
        is_html=pending.is_html,
        thread_id=getattr(pending, 'thread_id', None),
    )
    if not result:
        raise HTTPException(500, "Failed to send email. Check Resend API key and logs.")
    db.add(EmailLog(
        lead_id=pending.lead_id,
        campaign_id=pending.campaign_id,
        sequence_index=pending.sequence_index,
        subject=pending.subject,
        message_id=result.message_id,
        thread_id=result.thread_id,
    ))
    await db.delete(pending)
    await db.flush()
    log.info("approve_one: sent pending %s to %s (message_id=%s)", pending_id, pending.to_email, result.message_id)
    return {"ok": True, "message_id": result.message_id}


@router.post("/pending/approve-all")
async def approve_all(db: AsyncSession = Depends(get_db)):
    """Approve and send all pending emails."""
    result = await db.execute(select(PendingSend).order_by(PendingSend.created_at))
    pending_list = result.scalars().all()
    sent = 0
    failed = 0
    for pending in pending_list:
        result = send_email(
            to_email=pending.to_email,
            subject=pending.subject,
            body=pending.body,
            from_email=pending.from_email,
            from_name=pending.from_name or "",
            reply_to_msg_id=pending.reply_to_msg_id,
            is_html=pending.is_html,
            thread_id=getattr(pending, 'thread_id', None),
        )
        if result:
            db.add(EmailLog(
                lead_id=pending.lead_id,
                campaign_id=pending.campaign_id,
                sequence_index=pending.sequence_index,
                subject=pending.subject,
                message_id=result.message_id,
                thread_id=result.thread_id,
            ))
            await db.delete(pending)
            sent += 1
        else:
            failed += 1
    await db.flush()
    log.info("approve_all: sent=%d failed=%d", sent, failed)
    return {"ok": True, "sent": sent, "failed": failed}


@router.delete("/pending/{pending_id}")
async def reject_one(pending_id: int, db: AsyncSession = Depends(get_db)):
    """Reject (discard) one pending email without sending."""
    result = await db.execute(select(PendingSend).where(PendingSend.id == pending_id))
    pending = result.scalar_one_or_none()
    if not pending:
        raise HTTPException(404, "Pending email not found")
    await db.delete(pending)
    await db.flush()
    log.info("reject_one: discarded pending %s to %s", pending_id, pending.to_email)
    return {"ok": True}


@router.delete("/pending/all")
async def reject_all(db: AsyncSession = Depends(get_db)):
    """Reject (discard) all pending emails without sending."""
    result = await db.execute(delete(PendingSend))
    count = result.rowcount
    await db.flush()
    log.info("reject_all: discarded %d pending emails", count)
    return {"ok": True, "discarded": count}
