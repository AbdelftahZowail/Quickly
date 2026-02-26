"""Inbox API routes."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, exists

from app.database import get_db
from app.models import Inbox, CampaignInbox, QueueSlot, EmailLog
from app.schemas import InboxCreate, InboxUpdate, InboxResponse

log = logging.getLogger("quickly.routes")

router = APIRouter(prefix="/api/inboxes", tags=["inboxes"])


@router.get("", response_model=list[InboxResponse])
async def list_inboxes(db: AsyncSession = Depends(get_db)):
    # fetch all inboxes first
    result = await db.execute(select(Inbox).order_by(Inbox.id))
    inboxes = result.scalars().all()

    # compute how many emails have been sent today per inbox by grouping
    from sqlalchemy import func
    from datetime import datetime, timedelta

    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)

    # count logs per inbox in period
    count_res = await db.execute(
        select(EmailLog.inbox_id, func.count(EmailLog.id))
        .where(EmailLog.sent_at >= start, EmailLog.sent_at < end)
        .group_by(EmailLog.inbox_id)
    )
    counts = {row[0]: row[1] for row in count_res.all()}

    for i in inboxes:
        i.sent_today = counts.get(i.id, 0)
    return inboxes


@router.post("", response_model=InboxResponse)
async def create_inbox(data: InboxCreate, db: AsyncSession = Depends(get_db)):
    inbox = Inbox(
        email=data.email,
        display_name=data.display_name,
        max_emails_per_day=data.max_emails_per_day,
        wait_minutes_between=data.wait_minutes_between,
        provider=data.provider,
    )
    db.add(inbox)
    await db.flush()
    await db.refresh(inbox)
    return inbox


@router.get("/{inbox_id}", response_model=InboxResponse)
async def get_inbox(inbox_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")

    # attach today's sent count as above
    from sqlalchemy import func
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)
    count_res = await db.execute(
        select(func.count(EmailLog.id))
        .where(
            EmailLog.inbox_id == inbox_id,
            EmailLog.sent_at >= start,
            EmailLog.sent_at < end,
        )
    )
    inbox.sent_today = count_res.scalar() or 0
    return inbox


@router.patch("/{inbox_id}", response_model=InboxResponse)
async def update_inbox(inbox_id: int, data: InboxUpdate, db: AsyncSession = Depends(get_db)):
    from app.queue_logic import recalculate_queue_after_sequence_change_for_leads
    
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    
    capacity_changed = False
    if data.display_name is not None:
        inbox.display_name = data.display_name
    if data.max_emails_per_day is not None:
        inbox.max_emails_per_day = data.max_emails_per_day
        capacity_changed = True
    if data.wait_minutes_between is not None:
        inbox.wait_minutes_between = data.wait_minutes_between
        capacity_changed = True
    if data.provider is not None:
        if data.provider != inbox.provider:
            # provider switch may indicate credentials revoked / toggle-off
            capacity_changed = True
        inbox.provider = data.provider
    await db.flush()
    
    # If capacity or timing changed, recalculate queue globally rather than
    # just per-campaign.  A full recalculation will rebalance across campaigns
    # and is easier to reason about; the existing per-campaign loop worked but
    # became redundant after we added global recalc support.
    if capacity_changed:
        from app.models import CampaignInbox, CampaignLead
        campaign_result = await db.execute(
            select(CampaignInbox.campaign_id)
            .where(CampaignInbox.inbox_id == inbox_id)
            .distinct()
        )
        campaign_ids = [cid for (cid,) in campaign_result.all()]
        log.info("Inbox %s capacity changed; campaigns touched %s", inbox_id, campaign_ids)
        from app.routers.calendar import recalculate_all_campaigns
        await recalculate_all_campaigns(db)
    await db.refresh(inbox)
    return inbox


@router.delete("/{inbox_id}")
async def delete_inbox(inbox_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    # Check if this inbox is assigned to any campaign
    in_use = await db.execute(
        select(exists().where(CampaignInbox.inbox_id == inbox_id))
    )
    if in_use.scalar():
        raise HTTPException(
            400,
            "Inbox is assigned to one or more campaigns. Remove it from those campaigns first.",
        )
    # Check if any pending queue slots reference this inbox
    has_slots = await db.execute(
        select(exists().where(QueueSlot.inbox_id == inbox_id))
    )
    if has_slots.scalar():
        raise HTTPException(
            400,
            "Inbox has pending queue slots. Remove those first.",
        )
    await db.delete(inbox)
    await db.flush()
    log.info("delete_inbox: deleted inbox %s (%s)", inbox_id, inbox.email)
    return {"ok": True}
