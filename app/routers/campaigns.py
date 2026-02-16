"""Campaigns and sequences API routes."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from datetime import date

from app.database import get_db
from app.models import Campaign, Sequence, CampaignLead, QueueSlot, Lead, Inbox, EmailLog, CampaignInbox
from app.schemas import (
    CampaignCreate,
    CampaignUpdate,
    CampaignResponse,
    SequenceCreate,
    SequenceUpdate,
    SequenceResponse,
)
from app.queue_logic import reserve_slots_for_new_lead, recalculate_queue_after_sequence_change

log = logging.getLogger("campaign_engine.routes")

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _campaign_to_response(campaign: Campaign, inbox_ids: list[int]) -> CampaignResponse:
    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        inbox_ids=inbox_ids,
        sending_days=campaign.sending_days or [0, 1, 2, 3, 4],
        sending_hours_start=campaign.sending_hours_start or "09:00",
        sending_hours_end=campaign.sending_hours_end or "17:00",
        wait_minutes_between=campaign.wait_minutes_between,
        stop_on_reply=campaign.stop_on_reply,
        created_at=campaign.created_at,
    )


async def _get_inbox_ids_for_campaigns(db: AsyncSession, campaign_ids: list[int]) -> dict[int, list[int]]:
    if not campaign_ids:
        return {}
    result = await db.execute(
        select(CampaignInbox.campaign_id, CampaignInbox.inbox_id)
        .where(CampaignInbox.campaign_id.in_(campaign_ids))
        .order_by(CampaignInbox.position, CampaignInbox.inbox_id)
    )
    rows = result.all()
    out: dict[int, list[int]] = {cid: [] for cid in campaign_ids}
    for cid, iid in rows:
        out[cid].append(iid)
    return out


@router.get("", response_model=list[CampaignResponse])
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).order_by(Campaign.id))
    campaigns = result.scalars().all()
    campaign_ids = [c.id for c in campaigns]
    inbox_map = await _get_inbox_ids_for_campaigns(db, campaign_ids)
    return [
        _campaign_to_response(c, inbox_map.get(c.id, []))
        for c in campaigns
    ]


@router.post("", response_model=CampaignResponse)
async def create_campaign(data: CampaignCreate, db: AsyncSession = Depends(get_db)):
    if not data.inbox_ids:
        raise HTTPException(400, "At least one inbox required")
    campaign = Campaign(
        name=data.name,
        sending_days=data.sending_days,
        sending_hours_start=data.sending_hours_start,
        sending_hours_end=data.sending_hours_end,
        wait_minutes_between=data.wait_minutes_between,
        stop_on_reply=data.stop_on_reply,
    )
    db.add(campaign)
    await db.flush()
    for pos, inbox_id in enumerate(data.inbox_ids):
        db.add(CampaignInbox(campaign_id=campaign.id, inbox_id=inbox_id, position=pos))
    await db.refresh(campaign)
    return _campaign_to_response(campaign, list(data.inbox_ids))


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    inbox_map = await _get_inbox_ids_for_campaigns(db, [campaign_id])
    return _campaign_to_response(campaign, inbox_map.get(campaign_id, []))


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    # Cascade handles sequences, campaign_leads (and their queue_slots), campaign_inboxes, etc.
    await db.delete(campaign)
    await db.flush()
    log.info("delete_campaign: deleted campaign %s (%s)", campaign_id, campaign.name)
    return {"ok": True}


@router.patch("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: int, data: CampaignUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if data.name is not None:
        campaign.name = data.name
    if data.inbox_ids is not None:
        if not data.inbox_ids:
            raise HTTPException(400, "At least one inbox required")
        await db.execute(delete(CampaignInbox).where(CampaignInbox.campaign_id == campaign_id))
        await db.flush()
        for pos, inbox_id in enumerate(data.inbox_ids):
            db.add(CampaignInbox(campaign_id=campaign_id, inbox_id=inbox_id, position=pos))
    if data.sending_days is not None:
        campaign.sending_days = data.sending_days
    if data.sending_hours_start is not None:
        campaign.sending_hours_start = data.sending_hours_start
    if data.sending_hours_end is not None:
        campaign.sending_hours_end = data.sending_hours_end
    if data.wait_minutes_between is not None:
        campaign.wait_minutes_between = data.wait_minutes_between
    if data.stop_on_reply is not None:
        campaign.stop_on_reply = data.stop_on_reply
    await db.flush()
    inbox_map = await _get_inbox_ids_for_campaigns(db, [campaign_id])
    return _campaign_to_response(campaign, inbox_map.get(campaign_id, []))


# ---- Sequences ----
@router.get("/{campaign_id}/sequences", response_model=list[SequenceResponse])
async def list_sequences(campaign_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Sequence).where(Sequence.campaign_id == campaign_id).order_by(Sequence.position)
    )
    return result.scalars().all()


@router.post("/{campaign_id}/sequences", response_model=SequenceResponse)
async def create_sequence(
    campaign_id: int, data: SequenceCreate, db: AsyncSession = Depends(get_db)
):
    # Verify campaign exists
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Campaign not found")
    seq = Sequence(
        campaign_id=campaign_id,
        position=data.position,
        subject=data.subject,
        body=data.body,
        wait_days_after_previous=data.wait_days_after_previous,
    )
    db.add(seq)
    await db.flush()
    await db.refresh(seq)
    log.info("create_sequence: campaign=%s position=%s id=%s", campaign_id, data.position, seq.id)
    # Recalculate queue so already-enrolled leads get slots for the new sequence
    await recalculate_queue_after_sequence_change(db, campaign_id)
    return seq


@router.patch("/{campaign_id}/sequences/{sequence_id}", response_model=SequenceResponse)
async def update_sequence(
    campaign_id: int,
    sequence_id: int,
    data: SequenceUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Sequence).where(
            Sequence.id == sequence_id,
            Sequence.campaign_id == campaign_id,
        )
    )
    seq = result.scalar_one_or_none()
    if not seq:
        raise HTTPException(404, "Sequence not found")
    if data.subject is not None:
        seq.subject = data.subject
    if data.body is not None:
        seq.body = data.body
    if data.wait_days_after_previous is not None:
        seq.wait_days_after_previous = data.wait_days_after_previous
        await db.flush()
        await recalculate_queue_after_sequence_change(db, campaign_id)
    await db.refresh(seq)
    return seq


@router.delete("/{campaign_id}/sequences/{sequence_id}")
async def delete_sequence(
    campaign_id: int,
    sequence_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Sequence).where(
            Sequence.id == sequence_id,
            Sequence.campaign_id == campaign_id,
        )
    )
    seq = result.scalar_one_or_none()
    if not seq:
        raise HTTPException(404, "Sequence not found")
    deleted_position = seq.position
    await db.delete(seq)
    await db.flush()
    # Re-number remaining sequences to close the gap
    remaining = await db.execute(
        select(Sequence)
        .where(Sequence.campaign_id == campaign_id, Sequence.position > deleted_position)
        .order_by(Sequence.position)
    )
    for s in remaining.scalars().all():
        s.position -= 1
    await db.flush()
    await recalculate_queue_after_sequence_change(db, campaign_id)
    return {"ok": True}


# ---- Enrolled leads and queue ----
@router.get("/{campaign_id}/leads")
async def list_campaign_leads(campaign_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CampaignLead, Lead)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .where(CampaignLead.campaign_id == campaign_id)
        .order_by(CampaignLead.enrolled_at.desc())
    )
    rows = result.all()
    lead_ids = [lead.id for _cl, lead in rows]
    last_sent_map: dict[int, int] = {}
    if lead_ids:
        last_sent_result = await db.execute(
            select(EmailLog.lead_id, func.max(EmailLog.sequence_index).label("last_index"))
            .where(
                EmailLog.campaign_id == campaign_id,
                EmailLog.lead_id.in_(lead_ids),
            )
            .group_by(EmailLog.lead_id)
        )
        last_sent_map = {r.lead_id: r.last_index for r in last_sent_result.all()}
    seq_count_result = await db.execute(
        select(func.count(Sequence.id)).where(Sequence.campaign_id == campaign_id)
    )
    total_sequences = seq_count_result.scalar() or 0
    def stage_label(lead_id: int) -> str:
        last_index = last_sent_map.get(lead_id, -1)
        if total_sequences == 0:
            return "—"
        next_step = last_index + 1
        if next_step >= total_sequences:
            return "Complete"
        return f"Step {next_step + 1}"
    return [
        {
            "campaign_lead_id": cl.id,
            "lead_id": lead.id,
            "email": lead.email,
            "name": lead.name,
            "status": lead.status,
            "enrolled_at": cl.enrolled_at.isoformat(),
            "stage": stage_label(lead.id),
        }
        for cl, lead in rows
    ]


@router.get("/{campaign_id}/queue")
async def list_queue(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Queue slots for this campaign; includes inbox email per slot."""
    # Also count raw slots for debugging
    raw_count = await db.execute(
        select(func.count(QueueSlot.id))
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .where(CampaignLead.campaign_id == campaign_id)
    )
    total_slots = raw_count.scalar() or 0
    log.info("list_queue: campaign=%s total_raw_slots=%d", campaign_id, total_slots)

    result = await db.execute(
        select(QueueSlot, CampaignLead, Lead, Inbox)
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .join(Inbox, QueueSlot.inbox_id == Inbox.id)
        .where(CampaignLead.campaign_id == campaign_id)
        .order_by(QueueSlot.scheduled_date, QueueSlot.position_in_day)
    )
    rows = result.all()
    log.info("list_queue: campaign=%s joined_rows=%d", campaign_id, len(rows))
    return [
        {
            "slot_id": slot.id,
            "scheduled_date": slot.scheduled_date.isoformat(),
            "position_in_day": slot.position_in_day,
            "sequence_index": slot.sequence_index,
            "inbox_id": slot.inbox_id,
            "inbox_email": inbox.email,
            "lead_email": lead.email,
            "lead_name": lead.name,
        }
        for slot, _cl, lead, inbox in rows
    ]


@router.post("/{campaign_id}/recalculate-queue")
async def recalculate_queue(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Manually recalculate the queue, preserving already-sent sequences."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Campaign not found")
    await recalculate_queue_after_sequence_change(db, campaign_id)
    # Return the new slot count
    slot_count = await db.execute(
        select(func.count(QueueSlot.id))
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .where(CampaignLead.campaign_id == campaign_id)
    )
    n = slot_count.scalar() or 0
    log.info("recalculate_queue: campaign=%s slots=%d", campaign_id, n)
    return {"ok": True, "slots": n}


@router.get("/{campaign_id}/sent")
async def list_sent_emails(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Return sent email history for this campaign (for the calendar view)."""
    result = await db.execute(
        select(EmailLog, Lead)
        .join(Lead, EmailLog.lead_id == Lead.id)
        .where(EmailLog.campaign_id == campaign_id)
        .order_by(EmailLog.sent_at)
    )
    rows = result.all()
    return [
        {
            "log_id": el.id,
            "sent_date": el.sent_at.date().isoformat() if el.sent_at else None,
            "sent_at": el.sent_at.isoformat() if el.sent_at else None,
            "sequence_index": el.sequence_index,
            "subject": el.subject,
            "lead_id": el.lead_id,
            "lead_email": lead.email,
            "lead_name": lead.name,
        }
        for el, lead in rows
    ]


@router.delete("/{campaign_id}/leads/{lead_id}")
async def remove_lead_from_campaign(
    campaign_id: int, lead_id: int, db: AsyncSession = Depends(get_db)
):
    """Remove a lead from a campaign. Deletes enrollment and pending queue slots."""
    result = await db.execute(
        select(CampaignLead).where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.lead_id == lead_id,
        )
    )
    cl = result.scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Lead not enrolled in this campaign")
    # Delete queue slots for this enrollment (cascade would handle it, but be explicit)
    await db.execute(delete(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id))
    await db.delete(cl)
    await db.flush()
    log.info("remove_lead: campaign=%s lead=%s", campaign_id, lead_id)
    return {"ok": True}


@router.post("/{campaign_id}/leads/{lead_id}")
async def add_lead_to_campaign(
    campaign_id: int, lead_id: int, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Campaign not found")
    existing = await db.execute(
        select(CampaignLead).where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.lead_id == lead_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"ok": True, "message": "Already in campaign"}
    cl = CampaignLead(campaign_id=campaign_id, lead_id=lead_id)
    db.add(cl)
    await db.flush()
    log.info("add_lead_to_campaign: campaign=%s lead=%s cl=%s", campaign_id, lead_id, cl.id)
    await reserve_slots_for_new_lead(db, cl.id, campaign_id)
    # Verify slots were created
    slot_count = await db.execute(
        select(func.count(QueueSlot.id)).where(QueueSlot.campaign_lead_id == cl.id)
    )
    n = slot_count.scalar() or 0
    log.info("add_lead_to_campaign: %d queue slots created for cl=%s", n, cl.id)
    return {"ok": True, "slots_created": n}
