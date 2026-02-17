"""Inbox API routes."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, exists

from app.database import get_db
from app.models import Inbox, CampaignInbox, QueueSlot
from app.schemas import InboxCreate, InboxUpdate, InboxResponse

log = logging.getLogger("campaign_engine.routes")

router = APIRouter(prefix="/api/inboxes", tags=["inboxes"])


@router.get("", response_model=list[InboxResponse])
async def list_inboxes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Inbox).order_by(Inbox.id))
    return result.scalars().all()


@router.post("", response_model=InboxResponse)
async def create_inbox(data: InboxCreate, db: AsyncSession = Depends(get_db)):
    inbox = Inbox(
        email=data.email,
        display_name=data.display_name,
        max_emails_per_day=data.max_emails_per_day,
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
    return inbox


@router.patch("/{inbox_id}", response_model=InboxResponse)
async def update_inbox(inbox_id: int, data: InboxUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    if data.display_name is not None:
        inbox.display_name = data.display_name
    if data.max_emails_per_day is not None:
        inbox.max_emails_per_day = data.max_emails_per_day
    if data.provider is not None:
        inbox.provider = data.provider
    await db.flush()
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
