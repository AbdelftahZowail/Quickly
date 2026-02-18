"""Global calendar API — all sent + scheduled emails across all campaigns."""
import logging
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import get_db
from app.models import (
    QueueSlot, CampaignLead, Campaign, Sequence, Lead, Inbox, EmailLog,
)
from app.queue_logic import recalculate_queue_after_sequence_change

log = logging.getLogger("campaign_engine.calendar")

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("/sent")
async def global_sent(db: AsyncSession = Depends(get_db)):
    """All sent emails across every campaign with full details."""
    # We need: EmailLog + Lead + Campaign + Sequence (matched by campaign_id & sequence_index)
    SeqAlias = aliased(Sequence)
    result = await db.execute(
        select(EmailLog, Lead, Campaign, SeqAlias)
        .join(Lead, EmailLog.lead_id == Lead.id)
        .join(Campaign, EmailLog.campaign_id == Campaign.id)
        .outerjoin(
            SeqAlias,
            (SeqAlias.campaign_id == Campaign.id)
            & (SeqAlias.position == EmailLog.sequence_index),
        )
        .order_by(EmailLog.sent_at.desc())
    )
    rows = result.all()

    return [
        {
            "type": "sent",
            "log_id": el.id,
            "sent_at": el.sent_at.isoformat() if el.sent_at else None,
            "sent_date": el.sent_at.date().isoformat() if el.sent_at else None,
            "subject": el.subject or "",
            "message_id": el.message_id or "",
            "sequence_index": el.sequence_index,
            "sequence_body": seq.body if seq else "",
            "sequence_wait_days": seq.wait_days_after_previous if seq else 0,
            "lead_id": lead.id,
            "lead_email": lead.email,
            "lead_name": lead.name or "",
            "lead_status": lead.status,
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "campaign_sending_days": campaign.sending_days or [],
            "campaign_hours_start": campaign.sending_hours_start or "09:00",
            "campaign_hours_end": campaign.sending_hours_end or "17:00",
            "campaign_wait_minutes": campaign.wait_minutes_between or 5,
            "campaign_stop_on_reply": campaign.stop_on_reply,
        }
        for el, lead, campaign, seq in rows
    ]


@router.get("/scheduled")
async def global_scheduled(db: AsyncSession = Depends(get_db)):
    """All upcoming queue slots across every campaign with full details."""
    SeqAlias = aliased(Sequence)
    result = await db.execute(
        select(QueueSlot, CampaignLead, Campaign, Lead, Inbox, SeqAlias)
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .join(Campaign, CampaignLead.campaign_id == Campaign.id)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .join(Inbox, QueueSlot.inbox_id == Inbox.id)
        .outerjoin(
            SeqAlias,
            (SeqAlias.campaign_id == Campaign.id)
            & (SeqAlias.position == QueueSlot.sequence_index),
        )
        .order_by(QueueSlot.scheduled_date.asc(), QueueSlot.position_in_day)
    )
    rows = result.all()

    return [
        {
            "type": "scheduled",
            "slot_id": slot.id,
            "scheduled_at": slot.scheduled_date.isoformat() if slot.scheduled_date else None,
            "scheduled_date": slot.scheduled_date.date().isoformat() if slot.scheduled_date else None,
            "position_in_day": slot.position_in_day,
            "sequence_index": slot.sequence_index,
            "subject": (seq.subject or "(reply in thread)") if seq else "",
            "sequence_body": seq.body if seq else "",
            "sequence_wait_days": seq.wait_days_after_previous if seq else 0,
            "inbox_id": inbox.id,
            "inbox_email": inbox.email,
            "inbox_display_name": inbox.display_name or "",
            "inbox_provider": getattr(inbox, "provider", "resend") or "resend",
            "inbox_max_per_day": inbox.max_emails_per_day,
            "lead_id": lead.id,
            "lead_email": lead.email,
            "lead_name": lead.name or "",
            "lead_status": lead.status,
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "campaign_sending_days": campaign.sending_days or [],
            "campaign_hours_start": campaign.sending_hours_start or "09:00",
            "campaign_hours_end": campaign.sending_hours_end or "17:00",
            "campaign_wait_minutes": campaign.wait_minutes_between or 5,
            "campaign_stop_on_reply": campaign.stop_on_reply,
        }
        for slot, cl, campaign, lead, inbox, seq in rows
    ]


@router.get("/stats")
async def global_stats(db: AsyncSession = Depends(get_db)):
    """Quick summary for the calendar header."""
    sent_count = await db.execute(select(func.count(EmailLog.id)))
    scheduled_count = await db.execute(select(func.count(QueueSlot.id)))
    campaign_count = await db.execute(select(func.count(Campaign.id)))
    return {
        "total_sent": sent_count.scalar() or 0,
        "total_scheduled": scheduled_count.scalar() or 0,
        "total_campaigns": campaign_count.scalar() or 0,
    }


async def clear_queue(db: AsyncSession = Depends(get_db)):
    """Delete all pending (future) queue slots across all campaigns."""
    log.info("clear_queue: deleting all queue slots")
    result = await db.execute(delete(QueueSlot))
    deleted = result.rowcount
    await db.commit()
    log.info("clear_queue: deleted %d queue slots", deleted)
    return {"ok": True, "deleted": deleted}

@router.post("/recalculate-all")
async def recalculate_all_campaigns(db: AsyncSession = Depends(get_db)):
    """Recalculate queue slots for all campaigns while preserving inbox assignments."""
    log.info("recalculate_all_campaigns: starting global recalculation")
    await clear_queue(db)
    # Count existing slots before recalculation
    slot_count_before = await db.execute(select(func.count(QueueSlot.id)))
    initial_slots = slot_count_before.scalar() or 0
    log.info("recalculate_all_campaigns: starting with %d existing queue slots", initial_slots)
    
    # Get all campaigns
    result = await db.execute(select(Campaign))
    campaigns = result.scalars().all()
    
    if not campaigns:
        log.warning("recalculate_all_campaigns: no campaigns found")
        return {"ok": True, "campaigns_processed": 0, "total_slots": 0, "initial_slots": initial_slots}
    
    campaigns_processed = 0
    for campaign in campaigns:
        log.info("recalculate_all_campaigns: processing campaign_id=%s name=%s", campaign.id, campaign.name)
        # Each campaign handles its own slot deletion while preserving inbox assignments
        await recalculate_queue_after_sequence_change(db, campaign.id)
        campaigns_processed += 1
    
    # Get updated slot count
    slot_count = await db.execute(select(func.count(QueueSlot.id)))
    total_slots = slot_count.scalar() or 0
    
    log.info("recalculate_all_campaigns: completed - processed %d campaigns, %d -> %d slots", 
             campaigns_processed, initial_slots, total_slots)
    
    return {
        "ok": True, 
        "campaigns_processed": campaigns_processed,
        "initial_slots": initial_slots,
        "total_slots": total_slots
    }
