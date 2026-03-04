"""Campaigns and sequences API routes."""
import csv
import io
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from datetime import date
from typing import List

from app.database import get_db
from app.models import (
    Campaign,
    Sequence,
    CampaignLead,
    QueueSlot,
    Lead,
    LeadUnsubscribeToken,
    Inbox,
    EmailLog,
    CampaignInbox,
    LeadReply,
)
from app.schemas import (
    CampaignCreate,
    CampaignUpdate,
    CampaignResponse,
    CampaignLeadAdd,
    SequenceCreate,
    SequenceUpdate,
    SequenceResponse,
)
from app.queue_logic import reserve_slots_for_new_leads_bulk, recalculate_queue_after_sequence_change_for_leads

log = logging.getLogger("quickly.routes")

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _campaign_to_response(
    campaign: Campaign,
    inbox_ids: list[int],
    stats: dict | None = None,
) -> CampaignResponse:
    # ``stats`` is a map with keys matching the fields of CampaignStats
    if stats is None:
        stats = {}
    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        inbox_ids=inbox_ids,
        sending_days=campaign.sending_days or [0, 1, 2, 3, 4],
        sending_hours_start=campaign.sending_hours_start or "09:00",
        sending_hours_end=campaign.sending_hours_end or "17:00",
        wait_minutes_between=campaign.wait_minutes_between,
        stop_on_reply=campaign.stop_on_reply,
        paused=campaign.paused if hasattr(campaign, 'paused') else False,
        priority=campaign.priority if hasattr(campaign, 'priority') else 0,
        track_opens=bool(getattr(campaign, 'track_opens', False)),
        track_clicks=bool(getattr(campaign, 'track_clicks', False)),
        add_unsubscribe_header=bool(getattr(campaign, 'add_unsubscribe_header', True)),
        send_first_as_text=bool(getattr(campaign, 'send_first_as_text', False)),
        send_all_as_text=bool(getattr(campaign, 'send_all_as_text', False)),
        timezone=getattr(campaign, 'timezone', None),
        created_at=campaign.created_at,
        stats=stats,
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
    # retrieve base objects
    result = await db.execute(select(Campaign).order_by(Campaign.priority, Campaign.id))
    campaigns = result.scalars().all()
    campaign_ids = [c.id for c in campaigns]
    inbox_map = await _get_inbox_ids_for_campaigns(db, campaign_ids)
    # gather aggregate stats in a few grouped queries
    stats_map: dict[int, dict] = {}
    if campaign_ids:
        # lead counts.  only active leads are considered for progress; a
        # replied/unsubscribed/bounced lead will have its status changed and
        # therefore no longer contributes to the denominator used by the
        # frontend progress bar.  (``CampaignLead`` rows are not deleted so we
        # can still reference the historical total if needed elsewhere, but
        # analytics should focus on remaining work.)
        from app.models import Lead
        res = await db.execute(
            select(CampaignLead.campaign_id, func.count())
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id.in_(campaign_ids),
                Lead.status == "active",
            )
            .group_by(CampaignLead.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["total_leads"] = cnt

        # email counts
        res = await db.execute(
            select(EmailLog.campaign_id, func.count())
            .where(EmailLog.campaign_id.in_(campaign_ids))
            .group_by(EmailLog.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["emails_sent"] = cnt

        # pending scheduled emails (queue slots)
        res = await db.execute(
            select(CampaignLead.campaign_id, func.count(QueueSlot.id))
            .join(QueueSlot, QueueSlot.campaign_lead_id == CampaignLead.id)
            # only slots for active leads contribute; slots for replied/unsubscribed
            # leads should already have been removed but keep the filter for safety.
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id.in_(campaign_ids),
                Lead.status == "active",
            )
            .group_by(CampaignLead.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["scheduled"] = cnt
        # open counts
        res = await db.execute(
            select(EmailLog.campaign_id, func.count())
            .where(EmailLog.campaign_id.in_(campaign_ids), EmailLog.opened == True)
            .group_by(EmailLog.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["open_rate"] = cnt  # temporarily store count
        # click counts
        res = await db.execute(
            select(EmailLog.campaign_id, func.count())
            .where(EmailLog.campaign_id.in_(campaign_ids), EmailLog.clicked == True)
            .group_by(EmailLog.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["click_rate"] = cnt  # temporarily store count
        # reply counts
        res = await db.execute(
            select(LeadReply.campaign_id, func.count())
            .where(LeadReply.campaign_id.in_(campaign_ids))
            .group_by(LeadReply.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["replies"] = cnt
        # sequence counts (for calculating potential total emails)
        res = await db.execute(
            select(Sequence.campaign_id, func.count())
            .where(Sequence.campaign_id.in_(campaign_ids))
            .group_by(Sequence.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["sequences"] = cnt

        # bounced lead counts per campaign
        res = await db.execute(
            select(CampaignLead.campaign_id, func.count())
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id.in_(campaign_ids),
                Lead.status == "bounced",
            )
            .group_by(CampaignLead.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["bounced"] = cnt

        # unsubscribed lead counts per campaign
        res = await db.execute(
            select(CampaignLead.campaign_id, func.count())
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id.in_(campaign_ids),
                Lead.status == "unsubscribed",
            )
            .group_by(CampaignLead.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["unsubscribed"] = cnt
    # convert raw counts stored in open_rate/click_rate keys into fractions
    for cid, stats in stats_map.items():
        sent = stats.get("emails_sent", 0) or 0
        if sent > 0:
            if "open_rate" in stats:
                stats["open_rate"] = stats["open_rate"] / sent
            if "click_rate" in stats:
                stats["click_rate"] = stats["click_rate"] / sent
        else:
            stats["open_rate"] = stats.get("open_rate", 0)
            stats["click_rate"] = stats.get("click_rate", 0)

    return [
        _campaign_to_response(c, inbox_map.get(c.id, []), stats_map.get(c.id))
        for c in campaigns
    ]


# utility used by Settings.jsx before changing strategy – the front end can
# ask for confirmation when there are active leads that would be
# re-scheduled by a strategy switch.
@router.get("/has-leads")
async def campaigns_have_leads(db: AsyncSession = Depends(get_db)):
    """Return whether any campaign currently contains enrolled leads.

    The frontend periodically calls this when the scheduling strategy is
    about to be changed so that the user can be warned before triggering a
    potentially expensive global recalculation.
    """
    # counting rows is cheaper than loading objects
    result = await db.execute(select(func.count(CampaignLead.id)))
    count = result.scalar() or 0
    return {"has_leads": bool(count)}


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
        paused=data.paused,
        priority=data.priority,
        track_opens=data.track_opens,
        track_clicks=data.track_clicks,
        add_unsubscribe_header=data.add_unsubscribe_header,
        send_first_as_text=data.send_first_as_text,
        send_all_as_text=data.send_all_as_text,
        timezone=data.timezone,
    )
    db.add(campaign)
    await db.flush()
    for pos, inbox_id in enumerate(data.inbox_ids):
        db.add(CampaignInbox(campaign_id=campaign.id, inbox_id=inbox_id, position=pos))
    await db.refresh(campaign)
    return _campaign_to_response(campaign, list(data.inbox_ids))


class CampaignReorder(BaseModel):
    """Body for POST /api/campaigns/reorder — explicit priority ordering.

    ``campaign_ids`` is the desired order from highest-priority (index 0) to
    lowest.  Each campaign's ``priority`` column is set to its index in this
    list so that ``priority=0`` == highest priority.
    """
    campaign_ids: List[int]


@router.post("/reorder")
async def reorder_campaigns(data: CampaignReorder, db: AsyncSession = Depends(get_db)):
    """Set the priority order of campaigns used by the priority-based scheduling strategy.

    Pass a list of all campaign IDs in the desired order (highest priority first).
    Each campaign's ``priority`` is updated to its position in the list (0 = highest).
    """
    if not data.campaign_ids:
        raise HTTPException(400, "campaign_ids must not be empty")

    result = await db.execute(
        select(Campaign).where(Campaign.id.in_(data.campaign_ids))
    )
    campaigns_by_id = {c.id: c for c in result.scalars().all()}

    missing = [cid for cid in data.campaign_ids if cid not in campaigns_by_id]
    if missing:
        raise HTTPException(404, f"Campaign IDs not found: {missing}")

    for priority_index, cid in enumerate(data.campaign_ids):
        campaigns_by_id[cid].priority = priority_index

    await db.flush()
    log.info(
        "reorder_campaigns: updated priority for %d campaigns -> %s",
        len(data.campaign_ids),
        {cid: idx for idx, cid in enumerate(data.campaign_ids)},
    )
    # changing campaign order affects scheduling;
    from app.routers.schedule import recalculate_all_campaigns
    await recalculate_all_campaigns(db)
    return {"ok": True, "order": data.campaign_ids}


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    inbox_map = await _get_inbox_ids_for_campaigns(db, [campaign_id])
    # compute stats for single campaign
    stats: dict = {}
    # lead count – only active leads contribute to progress
    from app.models import Lead
    res = await db.execute(
        select(func.count())
        .select_from(CampaignLead)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .where(
            CampaignLead.campaign_id == campaign_id,
            Lead.status == "active",
        )
    )
    stats["total_leads"] = res.scalar() or 0
    # emails
    res = await db.execute(
        select(func.count())
        .select_from(EmailLog)
        .where(EmailLog.campaign_id == campaign_id)
    )
    stats["emails_sent"] = res.scalar() or 0
    # scheduled
    res = await db.execute(
        select(func.count(QueueSlot.id))
        .select_from(QueueSlot)
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .where(
            CampaignLead.campaign_id == campaign_id,
            Lead.status == "active",
        )
    )
    stats["scheduled"] = res.scalar() or 0
    # replies
    res = await db.execute(
        select(func.count())
        .select_from(LeadReply)
        .where(LeadReply.campaign_id == campaign_id)
    )
    stats["replies"] = res.scalar() or 0
    # sequences
    res = await db.execute(
        select(func.count())
        .select_from(Sequence)
        .where(Sequence.campaign_id == campaign_id)
    )
    stats["sequences"] = res.scalar() or 0
    return _campaign_to_response(campaign, inbox_map.get(campaign_id, []), stats)


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    # gather lead IDs so we can decide whether any should also be removed
    cl_res = await db.execute(
        select(CampaignLead.lead_id).where(CampaignLead.campaign_id == campaign_id)
    )
    lead_ids = [r[0] for r in cl_res.all()]

    # delete any unsubscribe tokens belonging to this campaign before we
    # remove the campaign itself; the FK constraint otherwise trips during
    # the flush when SQLAlchemy issues the DELETE on campaign.
    await db.execute(delete(LeadUnsubscribeToken).where(LeadUnsubscribeToken.campaign_id == campaign_id))

    # Cascade handles sequences, campaign_leads (and their queue_slots),
    # campaign_inboxes, etc.  after flushing we can inspect which of the
    # previously-associated leads are now orphans and delete them as well.
    await db.delete(campaign)
    await db.flush()

    orphan_ids: list[int] = []
    if lead_ids:
        # any remaining CampaignLead rows for these leads? if not, the lead
        # belonged exclusively to the deleted campaign and can be removed.
        res2 = await db.execute(
            select(CampaignLead.lead_id)
            .where(CampaignLead.lead_id.in_(lead_ids))
            .group_by(CampaignLead.lead_id)
        )
        remaining = {r[0] for r in res2.all()}
        orphan_ids = [lid for lid in lead_ids if lid not in remaining]

    if orphan_ids:
        # mirror the logic in delete_lead to clean up associated logs/replies
        await db.execute(delete(EmailLog).where(EmailLog.lead_id.in_(orphan_ids)))
        await db.execute(delete(LeadReply).where(LeadReply.lead_id.in_(orphan_ids)))
        await db.execute(delete(Lead).where(Lead.id.in_(orphan_ids)))
        log.info("delete_campaign: also removed %s orphan leads", len(orphan_ids))

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
        await db.flush()
        # Recalculate queue since inbox assignments changed
        log.info("Campaign %s inbox list changed; triggering queue recalculation", campaign_id)
        # gather all campaign lead ids; run full recalculation to ensure
        # other campaigns can take advantage of capacity changes too
        cl_res = await db.execute(select(CampaignLead.id).where(CampaignLead.campaign_id == campaign_id))
        cl_ids = [r[0] for r in cl_res.all()]
        if cl_ids:
            from app.routers.schedule import recalculate_all_campaigns
            await recalculate_all_campaigns(db)
    
    schedule_changed = False
    if data.sending_days is not None:
        campaign.sending_days = data.sending_days
        schedule_changed = True
    if data.sending_hours_start is not None:
        campaign.sending_hours_start = data.sending_hours_start
        schedule_changed = True
    if data.sending_hours_end is not None:
        campaign.sending_hours_end = data.sending_hours_end
        schedule_changed = True
    
    if schedule_changed:
        await db.flush()
        log.info("Campaign %s sending schedule changed; triggering queue recalculation", campaign_id)
        cl_res = await db.execute(select(CampaignLead.id).where(CampaignLead.campaign_id == campaign_id))
        cl_ids = [r[0] for r in cl_res.all()]
        if cl_ids:
            from app.routers.schedule import recalculate_all_campaigns
            await recalculate_all_campaigns(db)
    
    if data.wait_minutes_between is not None:
        campaign.wait_minutes_between = data.wait_minutes_between
    if data.stop_on_reply is not None:
        campaign.stop_on_reply = data.stop_on_reply
    if data.paused is not None:
        # paused toggle impacts scheduling order and slot existence
        old_paused = campaign.paused
        campaign.paused = data.paused
        if old_paused != data.paused:
            # run a full recalculation so that paused campaigns drop out of the
            # schedule (or are added back when resumed) and other campaigns can
            # move into the newly freed capacity.  Using the global routine is
            # simpler than trying to reason about individual leads.
            from app.routers.schedule import recalculate_all_campaigns
            log.info(
                "Campaign %s paused state changed (%s -> %s); triggering full recalculation",
                campaign_id,
                old_paused,
                data.paused,
            )
            # we can call the router helper directly
            await recalculate_all_campaigns(db)
    if data.priority is not None:
        campaign.priority = data.priority
        # Changing priority affects the order campaigns are scheduled, so
        # rebuild globally.
        from app.routers.schedule import recalculate_all_campaigns
        await recalculate_all_campaigns(db)
    # Tracking and delivery options (no queue recalculation needed)
    if data.track_opens is not None:
        campaign.track_opens = data.track_opens
    if data.track_clicks is not None:
        campaign.track_clicks = data.track_clicks
    if data.add_unsubscribe_header is not None:
        campaign.add_unsubscribe_header = data.add_unsubscribe_header
    if data.send_first_as_text is not None:
        campaign.send_first_as_text = data.send_first_as_text
    if data.send_all_as_text is not None:
        campaign.send_all_as_text = data.send_all_as_text
    if data.timezone is not None:
        campaign.timezone = data.timezone if data.timezone else None
    await db.flush()
    inbox_map = await _get_inbox_ids_for_campaigns(db, [campaign_id])
    return _campaign_to_response(campaign, inbox_map.get(campaign_id, []))


@router.post("/{campaign_id}/duplicate", response_model=CampaignResponse)
async def duplicate_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Create a copy of a campaign with all its sequences, but no enrolled leads."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(404, "Campaign not found")
    
    # Get inbox associations
    inbox_map = await _get_inbox_ids_for_campaigns(db, [campaign_id])
    inbox_ids = inbox_map.get(campaign_id, [])
    
    # Get sequences
    seq_result = await db.execute(
        select(Sequence).where(Sequence.campaign_id == campaign_id).order_by(Sequence.position)
    )
    sequences = seq_result.scalars().all()
    
    # Create new campaign
    new_campaign = Campaign(
        name=f"{original.name} (Copy)",
        sending_days=original.sending_days,
        sending_hours_start=original.sending_hours_start,
        sending_hours_end=original.sending_hours_end,
        wait_minutes_between=original.wait_minutes_between,
        stop_on_reply=original.stop_on_reply,
    )
    db.add(new_campaign)
    await db.flush()
    
    # Copy inbox associations
    for pos, inbox_id in enumerate(inbox_ids):
        db.add(CampaignInbox(campaign_id=new_campaign.id, inbox_id=inbox_id, position=pos))
    
    # Copy sequences
    for seq in sequences:
        new_seq = Sequence(
            campaign_id=new_campaign.id,
            position=seq.position,
            subject=seq.subject,
            body=seq.body,
            wait_days_after_previous=seq.wait_days_after_previous,
            is_html=seq.is_html,
            preview_text=seq.preview_text,
        )
        db.add(new_seq)
    
    await db.flush()
    await db.refresh(new_campaign)
    log.info("duplicate_campaign: original=%s new=%s sequences=%d", campaign_id, new_campaign.id, len(sequences))
    
    return _campaign_to_response(new_campaign, inbox_ids)


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
        is_html=data.is_html,
        preview_text=data.preview_text,
    )
    db.add(seq)
    await db.flush()
    await db.refresh(seq)
    log.info("create_sequence: campaign=%s position=%s id=%s", campaign_id, data.position, seq.id)
    # Recalculate queue so already-enrolled leads get slots for the new sequence
    cl_res = await db.execute(select(CampaignLead.id).where(CampaignLead.campaign_id == campaign_id))
    cl_ids = [r[0] for r in cl_res.all()]
    if cl_ids:
        from app.routers.schedule import recalculate_all_campaigns
        await recalculate_all_campaigns(db)
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
    if 'is_html' in data.model_fields_set:
        seq.is_html = data.is_html
    if 'preview_text' in data.model_fields_set:
        seq.preview_text = data.preview_text
    if data.wait_days_after_previous is not None:
        seq.wait_days_after_previous = data.wait_days_after_previous
        await db.flush()
        cl_res = await db.execute(select(CampaignLead.id).where(CampaignLead.campaign_id == campaign_id))
        cl_ids = [r[0] for r in cl_res.all()]
        if cl_ids:
            from app.routers.schedule import recalculate_all_campaigns
            await recalculate_all_campaigns(db)
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
    cl_res = await db.execute(select(CampaignLead.id).where(CampaignLead.campaign_id == campaign_id))
    cl_ids = [r[0] for r in cl_res.all()]
    if cl_ids:
        from app.routers.schedule import recalculate_all_campaigns
        await recalculate_all_campaigns(db)
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
    opened_set: set[int] = set()
    clicked_set: set[int] = set()
    replied_set: set[int] = set()

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

        # leads that opened at least one email in this campaign
        opened_result = await db.execute(
            select(EmailLog.lead_id)
            .where(
                EmailLog.campaign_id == campaign_id,
                EmailLog.lead_id.in_(lead_ids),
                EmailLog.opened == True,
            )
            .distinct()
        )
        opened_set = {r[0] for r in opened_result.all()}

        # leads that clicked at least one link in this campaign
        clicked_result = await db.execute(
            select(EmailLog.lead_id)
            .where(
                EmailLog.campaign_id == campaign_id,
                EmailLog.lead_id.in_(lead_ids),
                EmailLog.clicked == True,
            )
            .distinct()
        )
        clicked_set = {r[0] for r in clicked_result.all()}

        # leads that replied in this campaign
        replied_result = await db.execute(
            select(LeadReply.lead_id)
            .where(
                LeadReply.campaign_id == campaign_id,
                LeadReply.lead_id.in_(lead_ids),
            )
            .distinct()
        )
        replied_set = {r[0] for r in replied_result.all()}

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
            "custom_data": lead.custom_data or {},
            "enrolled_at": cl.enrolled_at.isoformat(),
            "stage": stage_label(lead.id),
            "opened": lead.id in opened_set,
            "clicked": lead.id in clicked_set,
            "replied": lead.id in replied_set,
            "interest_status": cl.interest_status,
            "sending_paused": cl.sending_paused,
        }
        for cl, lead in rows
    ]


class _CampaignLeadPatch(BaseModel):
    interest_status: str | None = None    # "interested", "not_interested", or null to clear
    sending_paused: bool | None = None


@router.patch("/{campaign_id}/leads/{lead_id}")
async def patch_campaign_lead(
    campaign_id: int,
    lead_id: int,
    payload: _CampaignLeadPatch,
    db: AsyncSession = Depends(get_db),
):
    """Update interest_status and/or sending_paused for a campaign-lead pair.

    Used by the frontend to let users override the AI classification — e.g.
    re-enable sending for a lead that was marked not_interested, or manually
    mark a lead as interested/not_interested.
    """
    result = await db.execute(
        select(CampaignLead).where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.lead_id == lead_id,
        )
    )
    cl = result.scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Campaign-lead enrolment not found")

    VALID_INTEREST_STATUSES = {"interested", "not_interested", "out_of_office", "wrong_person", "auto_reply", ""}
    if payload.interest_status is not None:
        if payload.interest_status not in VALID_INTEREST_STATUSES:
            raise HTTPException(400, f"interest_status must be one of: {', '.join(sorted(VALID_INTEREST_STATUSES - {''}))}, or empty string to clear")
        cl.interest_status = payload.interest_status if payload.interest_status else None

    if payload.sending_paused is not None:
        cl.sending_paused = payload.sending_paused
        # If re-enabling sending, re-create queue slots for this lead
        if not payload.sending_paused:
            from app.queue_logic import reserve_slots_for_new_leads_bulk
            campaign = await db.get(Campaign, campaign_id)
            if campaign:
                await reserve_slots_for_new_leads_bulk(db, campaign, [cl])

    await db.commit()
    return {"ok": True, "interest_status": cl.interest_status, "sending_paused": cl.sending_paused}


class PreviewRequest(BaseModel):
    sequence_id: int
    lead_id: int | None = None


@router.post("/{campaign_id}/preview")
async def preview_email(
    campaign_id: int,
    data: PreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Render a sequence email for preview (with optional lead variable substitution)."""
    seq_result = await db.execute(
        select(Sequence).where(
            Sequence.id == data.sequence_id,
            Sequence.campaign_id == campaign_id,
        )
    )
    seq = seq_result.scalar_one_or_none()
    if not seq:
        raise HTTPException(404, "Sequence not found")

    camp_result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = camp_result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")

    from app.sender import render_body, get_lead_data

    # Build substitution data – fall back to placeholder strings when no lead given.
    lead_data: dict = {"name": "{{name}}", "email": "{{email}}"}
    if data.lead_id:
        lead_result = await db.execute(select(Lead).where(Lead.id == data.lead_id))
        lead = lead_result.scalar_one_or_none()
        if lead:
            lead_data = get_lead_data(lead)

    rendered_body = render_body(seq.body or "", lead_data)
    rendered_subject = render_body(seq.subject or "", lead_data)

    # For HTML sequences inject tracking with a placeholder log id so that
    # tracking URLs look realistic (they won't resolve until a real send).
    tracking_urls_note = None
    if seq.is_html and (campaign.track_clicks or campaign.track_opens):
        try:
            from app.settings_manager import settings as app_settings
            from app.tracking import inject_tracking_html
            tracking_base = (app_settings.tracking_base_url or "").rstrip("/")
            if tracking_base:
                rendered_body, _pairs = inject_tracking_html(
                    rendered_body,
                    email_log_id=0,
                    tracking_base=tracking_base,
                    track_opens=campaign.track_opens,
                    track_clicks=campaign.track_clicks,
                )
                tracking_urls_note = "Tracking URLs use placeholder log id=0 (preview only)"
        except Exception:
            pass  # non-fatal; show untracked version

    return {
        "subject": rendered_subject,
        "body": rendered_body,
        "is_html": bool(seq.is_html),
        "sequence_position": seq.position,
        "tracking_note": tracking_urls_note,
    }


class TestEmailRequest(BaseModel):
    sequence_id: int
    lead_id: int | None = None
    to_email: str


@router.post("/{campaign_id}/send-test")
async def send_test_email(
    campaign_id: int,
    data: TestEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send a rendered test email to the specified address using the campaign's first inbox."""
    import asyncio

    seq_result = await db.execute(
        select(Sequence).where(
            Sequence.id == data.sequence_id,
            Sequence.campaign_id == campaign_id,
        )
    )
    seq = seq_result.scalar_one_or_none()
    if not seq:
        raise HTTPException(404, "Sequence not found")

    camp_result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = camp_result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")

    # Get the first inbox for this campaign
    inbox_res = await db.execute(
        select(Inbox)
        .join(CampaignInbox, Inbox.id == CampaignInbox.inbox_id)
        .where(CampaignInbox.campaign_id == campaign_id)
        .order_by(CampaignInbox.position, CampaignInbox.inbox_id)
        .limit(1)
    )
    inbox = inbox_res.scalar_one_or_none()
    if not inbox:
        raise HTTPException(400, "No inboxes configured for this campaign")

    from app.sender import render_body, get_lead_data

    lead_data: dict = {"name": "Test User", "email": data.to_email}
    if data.lead_id:
        lead_result = await db.execute(select(Lead).where(Lead.id == data.lead_id))
        lead = lead_result.scalar_one_or_none()
        if lead:
            lead_data = get_lead_data(lead)

    rendered_body = render_body(seq.body or "", lead_data)
    rendered_subject = render_body(seq.subject or "(no subject)", lead_data)

    # Get Gmail account if needed
    gmail_account = None
    if getattr(inbox, "provider", "") == "gmail":
        from app.models import GmailAccount
        ga_result = await db.execute(
            select(GmailAccount).where(GmailAccount.email == inbox.email)
        )
        gmail_account = ga_result.scalar_one_or_none()

    from app.sender import send_email

    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: send_email(
            to_email=data.to_email,
            subject=f"[TEST] {rendered_subject}",
            body=rendered_body,
            from_email=inbox.email or "",
            from_name=inbox.display_name or "",
            is_html=bool(seq.is_html),
            provider=getattr(inbox, "provider", "resend") or "resend",
            gmail_account=gmail_account,
        ),
    )

    if not result:
        raise HTTPException(500, "Failed to send test email — check inbox configuration")

    log.info(
        "send_test_email: campaign=%s sequence=%s to=%s inbox=%s",
        campaign_id, data.sequence_id, data.to_email, inbox.email,
    )
    return {"ok": True, "message_id": result.message_id}


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




@router.get("/{campaign_id}/sent")
async def list_sent_emails(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Return sent email history for this campaign (for the schedule view)."""
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


@router.post("/{campaign_id}/leads")
async def bulk_add_leads_to_campaign(
    campaign_id: int,
    leads_data: list[CampaignLeadAdd],
    db: AsyncSession = Depends(get_db),
):
    """
    Add one or more leads to a campaign.
    For each entry: find existing lead by email (or create), then enroll if not already enrolled.
    Queues slots for each newly enrolled lead using the bulk schedule
    (accepts a single id as well; does NOT perform a full recalculate).    """
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Campaign not found")

    if not leads_data:
        raise HTTPException(400, "No leads provided")

    results = []
    added = 0
    already_enrolled = 0
    errors = 0

    for entry in leads_data:
        email = entry.email.strip().lower()
        if not email:
            results.append({"email": entry.email, "status": "error", "detail": "Empty email"})
            errors += 1
            continue

        try:
            # Find or create lead by email
            lead_result = await db.execute(select(Lead).where(Lead.email == email))
            lead = lead_result.scalar_one_or_none()
            if not lead:
                lead = Lead(
                    email=email,
                    name=entry.name or "",
                    custom_data=entry.custom_data or {},
                )
                db.add(lead)
                await db.flush()  # Assigns lead.id
                log.info("bulk_add_leads: created new lead %s (email=%s)", lead.id, email)
            else:
                # Update name/custom_data if supplied and lead has no existing values
                changed = False
                if entry.name and not lead.name:
                    lead.name = entry.name
                    changed = True
                if entry.custom_data and not lead.custom_data:
                    lead.custom_data = entry.custom_data
                    changed = True
                if changed:
                    await db.flush()

            # Check enrollment
            existing_cl = await db.execute(
                select(CampaignLead).where(
                    CampaignLead.campaign_id == campaign_id,
                    CampaignLead.lead_id == lead.id,
                )
            )
            if existing_cl.scalar_one_or_none():
                results.append({"email": email, "status": "already_enrolled"})
                already_enrolled += 1
                continue

            # Enroll and queue
            cl = CampaignLead(campaign_id=campaign_id, lead_id=lead.id)
            db.add(cl)
            await db.flush()
            # schedule using bulk API even for a single lead
            await reserve_slots_for_new_leads_bulk(db, [cl.id], campaign_id)

            # Count slots created
            slot_count_result = await db.execute(
                select(func.count(QueueSlot.id)).where(QueueSlot.campaign_lead_id == cl.id)
            )
            slots = slot_count_result.scalar() or 0
            log.info(
                "bulk_add_leads: enrolled lead %s in campaign %s — %d slot(s)",
                lead.id, campaign_id, slots,
            )
            results.append({"email": email, "status": "added", "lead_id": lead.id, "slots_created": slots})
            added += 1

        except Exception as exc:
            log.exception("bulk_add_leads: error processing email %s: %s", email, exc)
            results.append({"email": email, "status": "error", "detail": str(exc)})
            errors += 1

    return {
        "ok": True,
        "added": added,
        "already_enrolled": already_enrolled,
        "errors": errors,
        "results": results,
    }


# ---- Export leads as CSV ----
@router.get("/{campaign_id}/leads/export")
async def export_campaign_leads(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Export all leads for a campaign as a CSV file."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = result.scalar_one_or_none()
    if not camp:
        raise HTTPException(404, "Campaign not found")

    result = await db.execute(
        select(CampaignLead, Lead)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .where(CampaignLead.campaign_id == campaign_id)
        .order_by(CampaignLead.enrolled_at.desc())
    )
    rows = result.all()

    # Gather all custom_data keys
    all_keys = set()
    for _cl, lead in rows:
        if lead.custom_data:
            all_keys.update(lead.custom_data.keys())
    custom_keys = sorted(all_keys)

    output = io.StringIO()
    writer = csv.writer(output)
    header = ["email", "name"] + custom_keys
    writer.writerow(header)
    for _cl, lead in rows:
        row = [lead.email, lead.name or ""]
        for k in custom_keys:
            val = (lead.custom_data or {}).get(k, "")
            row.append(str(val) if val is not None else "")
        writer.writerow(row)

    output.seek(0)
    safe_name = camp.name.replace(" ", "_").replace("/", "_")[:30]
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="leads_{safe_name}.csv"'},
    )


# ---- Import leads from CSV ----
@router.post("/{campaign_id}/leads/import")
async def import_campaign_leads(
    campaign_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import leads from a CSV file. Expects columns: email, name, and any custom fields."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Campaign not found")

    contents = await file.read()
    text = contents.decode("utf-8-sig")  # handle BOM from Excel

    # Try to detect delimiter
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text[:2048])
    except csv.Error:
        dialect = csv.excel  # fallback

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    if not reader.fieldnames:
        raise HTTPException(400, "CSV file appears to be empty or has no headers")

    # Normalize field names
    fields = [f.strip().lower() for f in reader.fieldnames]
    if "email" not in fields:
        raise HTTPException(400, "CSV must have an 'email' column")

    added = 0
    already_enrolled = 0
    errors = 0
    results_list = []

    for row_num, row in enumerate(reader, start=2):
        # Normalize keys
        normalized = {k.strip().lower(): v.strip() if v else "" for k, v in row.items() if k}
        email = normalized.get("email", "").strip().lower()
        if not email:
            results_list.append({"row": row_num, "status": "error", "detail": "Empty email"})
            errors += 1
            continue

        name = normalized.get("name", "")
        # Everything else is custom data
        custom_data = {}
        for k, v in normalized.items():
            if k not in ("email", "name") and v:
                custom_data[k] = v

        try:
            lead_result = await db.execute(select(Lead).where(Lead.email == email))
            lead = lead_result.scalar_one_or_none()
            if not lead:
                lead = Lead(email=email, name=name, custom_data=custom_data)
                db.add(lead)
                await db.flush()
            else:
                changed = False
                if name and not lead.name:
                    lead.name = name
                    changed = True
                if custom_data:
                    merged = {**(lead.custom_data or {}), **custom_data}
                    if merged != lead.custom_data:
                        lead.custom_data = merged
                        changed = True
                if changed:
                    await db.flush()

            existing_cl = await db.execute(
                select(CampaignLead).where(
                    CampaignLead.campaign_id == campaign_id,
                    CampaignLead.lead_id == lead.id,
                )
            )
            if existing_cl.scalar_one_or_none():
                results_list.append({"row": row_num, "email": email, "status": "already_enrolled"})
                already_enrolled += 1
                continue

            cl = CampaignLead(campaign_id=campaign_id, lead_id=lead.id)
            db.add(cl)
            await db.flush()
            await reserve_slots_for_new_leads_bulk(db, [cl.id], campaign_id)
            added += 1
            results_list.append({"row": row_num, "email": email, "status": "added"})
        except Exception as exc:
            log.exception("import_leads: error on row %d email=%s: %s", row_num, email, exc)
            results_list.append({"row": row_num, "email": email, "status": "error", "detail": str(exc)})
            errors += 1

    return {
        "ok": True,
        "added": added,
        "already_enrolled": already_enrolled,
        "errors": errors,
        "total_rows": added + already_enrolled + errors,
    }
