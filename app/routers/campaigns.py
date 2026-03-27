"""Campaigns and sequences API routes."""
import csv
import io
import json
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload
from datetime import date
from typing import List

from app.database import get_db
from app.models import (
    Campaign,
    Sequence,
    SequenceVariant,
    CampaignLead,
    QueueSlot,
    Lead,
    LeadUnsubscribeToken,
    Inbox,
    EmailLog,
    CampaignInbox,
    LeadReply,
)
from app.campaign_lead_status import (
    ENROLLMENT_STATUSES,
    LEAD_INTERESTS,
    campaign_lead_may_receive_sends,
    campaign_lead_schedule_eligibility_clause,
    normalize_enrollment_status,
    normalize_interest,
)
from app.schemas import (
    CampaignCreate,
    CampaignUpdate,
    CampaignResponse,
    CampaignLeadAdd,
    CampaignLeadEnrollmentPatch,
    SequenceCreate,
    SequenceUpdate,
    SequenceResponse,
    SequenceVariantCreate,
    SequenceVariantUpdate,
    SequenceVariantResponse,
)
from app.queue_logic import reserve_slots_for_new_leads_bulk

log = logging.getLogger("quickly.routes")

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _apply_campaign_lead_add_options(cl: CampaignLead, lead: Lead, entry: CampaignLeadAdd) -> None:
    if entry.status:
        cl.enrollment_status = normalize_enrollment_status(entry.status)
    if entry.interest is not None:
        cl.interest_status = normalize_interest(entry.interest)
    if entry.email_verification_status is not None:
        raw = (entry.email_verification_status or "").strip().lower()
        if raw in ("", "null", "none"):
            lead.email_verification_status = None
        elif raw in ("valid", "invalid", "pending"):
            lead.email_verification_status = raw


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
        public_id=campaign.public_id or str(campaign.id),
        name=campaign.name,
        inbox_ids=inbox_ids,
        sending_days=campaign.sending_days or [0, 1, 2, 3, 4],
        sending_hours_start=campaign.sending_hours_start or "09:00",
        sending_hours_end=campaign.sending_hours_end or "17:00",
        stop_on_reply=campaign.stop_on_reply,
        paused=campaign.paused if hasattr(campaign, 'paused') else False,
        priority=campaign.priority if hasattr(campaign, 'priority') else 0,
        track_opens=bool(getattr(campaign, 'track_opens', False)),
        track_clicks=bool(getattr(campaign, 'track_clicks', False)),
        add_unsubscribe_header=bool(getattr(campaign, 'add_unsubscribe_header', True)),
        send_first_as_text=bool(getattr(campaign, 'send_first_as_text', False)),
        send_all_as_text=bool(getattr(campaign, 'send_all_as_text', False)),
        timezone=getattr(campaign, 'timezone', None),
        match_lead_provider=bool(getattr(campaign, 'match_lead_provider', False)),
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
        _eligible_cl = campaign_lead_schedule_eligibility_clause()
        res = await db.execute(
            select(CampaignLead.campaign_id, func.count())
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .join(Campaign, Campaign.id == CampaignLead.campaign_id)
            .where(CampaignLead.campaign_id.in_(campaign_ids), _eligible_cl)
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
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .join(Campaign, Campaign.id == CampaignLead.campaign_id)
            .where(CampaignLead.campaign_id.in_(campaign_ids), _eligible_cl)
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

        # bounced / unsubscribed enrollments per campaign
        res = await db.execute(
            select(CampaignLead.campaign_id, func.count())
            .where(
                CampaignLead.campaign_id.in_(campaign_ids),
                CampaignLead.enrollment_status == "bounced",
            )
            .group_by(CampaignLead.campaign_id)
        )
        for cid, cnt in res.all():
            stats_map.setdefault(cid, {})["bounced"] = cnt

        res = await db.execute(
            select(CampaignLead.campaign_id, func.count())
            .where(
                CampaignLead.campaign_id.in_(campaign_ids),
                CampaignLead.enrollment_status == "unsubscribed",
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
    # if not data.inbox_ids:
    #     raise HTTPException(400, "At least one inbox required")
    campaign = Campaign(
        name=data.name,
        sending_days=data.sending_days,
        sending_hours_start=data.sending_hours_start,
        sending_hours_end=data.sending_hours_end,
        stop_on_reply=data.stop_on_reply,
        paused=data.paused,
        priority=data.priority,
        track_opens=data.track_opens,
        track_clicks=data.track_clicks,
        add_unsubscribe_header=data.add_unsubscribe_header,
        send_first_as_text=data.send_first_as_text,
        send_all_as_text=data.send_all_as_text,
        timezone=data.timezone,
        match_lead_provider=data.match_lead_provider,
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
async def reorder_campaigns(
    data: CampaignReorder,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
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
    await db.commit()
    from app.routers.schedule import enqueue_global_recalculate

    enqueue_global_recalculate(background_tasks)
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
    _one_eligible = campaign_lead_schedule_eligibility_clause()
    res = await db.execute(
        select(func.count())
        .select_from(CampaignLead)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .where(CampaignLead.campaign_id == campaign_id, _one_eligible)
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
        .join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .where(CampaignLead.campaign_id == campaign_id, _one_eligible)
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
    campaign_id: int,
    data: CampaignUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    needs_global_recalc = False
    if data.name is not None:
        campaign.name = data.name
    if data.inbox_ids is not None:
        # if not data.inbox_ids:
        #     raise HTTPException(400, "At least one inbox required")
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
            needs_global_recalc = True

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
            needs_global_recalc = True

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
            log.info(
                "Campaign %s paused state changed (%s -> %s); triggering full recalculation",
                campaign_id,
                old_paused,
                data.paused,
            )
            needs_global_recalc = True
    if data.priority is not None:
        campaign.priority = data.priority
        # Changing priority affects the order campaigns are scheduled, so
        # rebuild globally.
        needs_global_recalc = True
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
    if data.match_lead_provider is not None:
        old_match = getattr(campaign, 'match_lead_provider', False)
        campaign.match_lead_provider = data.match_lead_provider
        if old_match != data.match_lead_provider:
            # Inbox assignment logic changed; rebuild queue so leads get the right inbox
            await db.flush()
            log.info(
                "Campaign %s match_lead_provider changed (%s -> %s); triggering queue recalculation",
                campaign_id, old_match, data.match_lead_provider,
            )
            needs_global_recalc = True
    if data.timezone is not None:
        old_tz = campaign.timezone
        campaign.timezone = data.timezone if data.timezone else None
        if campaign.timezone != old_tz:
            await db.flush()
            log.info("Campaign %s timezone changed (%s -> %s); triggering queue recalculation", campaign_id, old_tz, campaign.timezone)
            needs_global_recalc = True
    await db.flush()
    if needs_global_recalc:
        await db.commit()
        from app.routers.schedule import enqueue_global_recalculate

        enqueue_global_recalculate(background_tasks)
    inbox_map = await _get_inbox_ids_for_campaigns(db, [campaign_id])
    await db.commit()
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
        timezone=original.timezone,
        track_opens=original.track_opens,
        track_clicks=original.track_clicks,
        add_unsubscribe_header=original.add_unsubscribe_header,
        send_first_as_text=original.send_first_as_text,
        send_all_as_text=original.send_all_as_text,
        match_lead_provider=original.match_lead_provider,
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
        select(Sequence)
        .options(selectinload(Sequence.variants))
        .where(Sequence.campaign_id == campaign_id)
        .order_by(Sequence.position)
    )
    return result.scalars().all()


@router.post("/{campaign_id}/sequences", response_model=SequenceResponse)
async def create_sequence(
    campaign_id: int,
    data: SequenceCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
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
    log.info("create_sequence: campaign=%s position=%s id=%s", campaign_id, data.position, seq.id)
    # Recalculate queue so already-enrolled leads get slots for the new sequence
    cl_res = await db.execute(select(CampaignLead.id).where(CampaignLead.campaign_id == campaign_id))
    cl_ids = [r[0] for r in cl_res.all()]
    if cl_ids:
        await db.commit()
        from app.routers.schedule import enqueue_global_recalculate

        enqueue_global_recalculate(background_tasks)
    # Re-query with variants eagerly loaded
    result2 = await db.execute(
        select(Sequence).options(selectinload(Sequence.variants)).where(Sequence.id == seq.id)
    )
    await db.commit()
    return result2.scalar_one()


@router.patch("/{campaign_id}/sequences/{sequence_id}", response_model=SequenceResponse)
async def update_sequence(
    campaign_id: int,
    sequence_id: int,
    data: SequenceUpdate,
    background_tasks: BackgroundTasks,
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
            await db.commit()
            from app.routers.schedule import enqueue_global_recalculate

            enqueue_global_recalculate(background_tasks)
    await db.flush()
    # Re-query with variants eagerly loaded to avoid lazy-load MissingGreenlet error
    result2 = await db.execute(
        select(Sequence).options(selectinload(Sequence.variants)).where(Sequence.id == seq.id)
    )
    await db.commit()
    return result2.scalar_one()


@router.delete("/{campaign_id}/sequences/{sequence_id}")
async def delete_sequence(
    campaign_id: int,
    sequence_id: int,
    background_tasks: BackgroundTasks,
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
        await db.commit()
        from app.routers.schedule import enqueue_global_recalculate

        enqueue_global_recalculate(background_tasks)
    return {"ok": True}


# ---- Sequence Variants (A/B testing) ----

@router.get(
    "/{campaign_id}/sequences/{sequence_id}/variants",
    response_model=list[SequenceVariantResponse],
)
async def list_variants(
    campaign_id: int,
    sequence_id: int,
    db: AsyncSession = Depends(get_db),
):
    """List all A/B variants for a sequence step."""
    seq = await _get_sequence_or_404(campaign_id, sequence_id, db)
    result = await db.execute(
        select(SequenceVariant)
        .where(SequenceVariant.sequence_id == seq.id)
        .order_by(SequenceVariant.id)
    )
    return result.scalars().all()


@router.post(
    "/{campaign_id}/sequences/{sequence_id}/variants",
    response_model=SequenceVariantResponse,
    status_code=201,
)
async def create_variant(
    campaign_id: int,
    sequence_id: int,
    data: SequenceVariantCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new A/B variant for a sequence step."""
    seq = await _get_sequence_or_404(campaign_id, sequence_id, db)
    variant = SequenceVariant(
        sequence_id=seq.id,
        label=data.label or "",
        subject=data.subject,
        body=data.body,
        is_html=data.is_html,
        preview_text=data.preview_text,
        enabled=data.enabled,
    )
    db.add(variant)
    await db.flush()
    await db.refresh(variant)
    log.info("create_variant: sequence=%s variant=%s label=%r", seq.id, variant.id, variant.label)
    return variant


@router.patch(
    "/{campaign_id}/sequences/{sequence_id}/variants/{variant_id}",
    response_model=SequenceVariantResponse,
)
async def update_variant(
    campaign_id: int,
    sequence_id: int,
    variant_id: int,
    data: SequenceVariantUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an A/B variant (partial update)."""
    seq = await _get_sequence_or_404(campaign_id, sequence_id, db)
    result = await db.execute(
        select(SequenceVariant).where(
            SequenceVariant.id == variant_id,
            SequenceVariant.sequence_id == seq.id,
        )
    )
    variant = result.scalar_one_or_none()
    if not variant:
        raise HTTPException(404, "Variant not found")
    if data.label is not None:
        variant.label = data.label
    if data.subject is not None:
        variant.subject = data.subject
    if data.body is not None:
        variant.body = data.body
    if 'is_html' in data.model_fields_set:
        variant.is_html = data.is_html
    if 'preview_text' in data.model_fields_set:
        variant.preview_text = data.preview_text
    if data.enabled is not None:
        variant.enabled = data.enabled
    await db.flush()
    await db.refresh(variant)
    return variant


@router.delete("/{campaign_id}/sequences/{sequence_id}/variants/{variant_id}")
async def delete_variant(
    campaign_id: int,
    sequence_id: int,
    variant_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete an A/B variant."""
    seq = await _get_sequence_or_404(campaign_id, sequence_id, db)
    result = await db.execute(
        select(SequenceVariant).where(
            SequenceVariant.id == variant_id,
            SequenceVariant.sequence_id == seq.id,
        )
    )
    variant = result.scalar_one_or_none()
    if not variant:
        raise HTTPException(404, "Variant not found")
    await db.delete(variant)
    await db.flush()
    return {"ok": True}


async def _get_sequence_or_404(campaign_id: int, sequence_id: int, db: AsyncSession) -> Sequence:
    result = await db.execute(
        select(Sequence).where(
            Sequence.id == sequence_id,
            Sequence.campaign_id == campaign_id,
        )
    )
    seq = result.scalar_one_or_none()
    if not seq:
        raise HTTPException(404, "Sequence not found")
    return seq


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
            "status": getattr(cl, "enrollment_status", None) or "active",
            "interest": cl.interest_status,
            "custom_data": lead.custom_data or {},
            "enrolled_at": cl.enrolled_at.isoformat(),
            "stage": stage_label(lead.id),
            "opened": lead.id in opened_set,
            "clicked": lead.id in clicked_set,
            "replied": lead.id in replied_set,
            "sending_paused": cl.sending_paused,
            "email_verification_status": lead.email_verification_status,
            "provider": lead.provider,
        }
        for cl, lead in rows
    ]


@router.patch("/{campaign_id}/leads/{lead_id}")
async def patch_campaign_lead(
    campaign_id: int,
    lead_id: int,
    payload: CampaignLeadEnrollmentPatch,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Update enrollment status, interest, and/or sending_paused for this campaign."""
    result = await db.execute(
        select(CampaignLead).where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.lead_id == lead_id,
        )
    )
    cl = result.scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Campaign-lead enrolment not found")

    if payload.status is not None:
        st = (payload.status or "").strip().lower()
        if st not in ENROLLMENT_STATUSES:
            raise HTTPException(
                400,
                f"status must be one of: {', '.join(sorted(ENROLLMENT_STATUSES))}",
            )
        cl.enrollment_status = st

    if payload.interest is not None:
        raw = payload.interest
        if isinstance(raw, str) and raw.strip() == "":
            cl.interest_status = None
        else:
            norm = normalize_interest(str(raw) if raw is not None else "")
            if norm is None and raw not in (None, "", "null"):
                raise HTTPException(
                    400,
                    f"interest must be one of: {', '.join(sorted(LEAD_INTERESTS))} or empty to clear",
                )
            cl.interest_status = norm

    if payload.sending_paused is not None:
        cl.sending_paused = payload.sending_paused

    await db.flush()
    # Full global recalculation: schedule must mirror what the send job will deliver.
    await db.commit()
    from app.routers.schedule import enqueue_global_recalculate

    enqueue_global_recalculate(background_tasks)
    return {
        "ok": True,
        "status": cl.enrollment_status,
        "interest": cl.interest_status,
        "sending_paused": cl.sending_paused,
    }


class PreviewRequest(BaseModel):
    sequence_id: int
    lead_id: int | None = None
    variant_id: int | None = None  # If set, preview this A/B variant's content
    # Optional unsaved-content overrides (used when previewing before saving)
    subject_override: str | None = None
    body_override: str | None = None
    is_html_override: bool | None = None


@router.post("/{campaign_id}/preview")
async def preview_email(
    campaign_id: int,
    data: PreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Render a sequence email for preview (with optional lead variable substitution).

    Pass variant_id to preview a specific A/B variant's content instead of the
    default sequence content.
    """
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

    # Resolve variant content when a variant_id is supplied.
    # If unsaved overrides are provided, use them instead of the DB values.
    preview_body = data.body_override if data.body_override is not None else (seq.body or "")
    preview_subject = data.subject_override if data.subject_override is not None else (seq.subject or "")
    preview_is_html = data.is_html_override if data.is_html_override is not None else bool(seq.is_html)
    variant_label: str | None = None
    if data.variant_id is not None:
        from app.models import SequenceVariant
        var_result = await db.execute(
            select(SequenceVariant).where(
                SequenceVariant.id == data.variant_id,
                SequenceVariant.sequence_id == data.sequence_id,
            )
        )
        variant = var_result.scalar_one_or_none()
        if not variant:
            raise HTTPException(404, "Variant not found")
        preview_body = variant.body or ""
        preview_subject = variant.subject or seq.subject or ""  # fallback to sequence subject
        preview_is_html = bool(variant.is_html) if variant.is_html is not None else bool(seq.is_html)
        variant_label = variant.label or "Variant"

    from app.sender import render_body, get_lead_data

    # Build substitution data – fall back to placeholder strings when no lead given.
    lead_data: dict = {"name": "{{name}}", "email": "{{email}}"}
    if data.lead_id:
        lead_result = await db.execute(select(Lead).where(Lead.id == data.lead_id))
        lead = lead_result.scalar_one_or_none()
        if lead:
            lead_data = get_lead_data(lead)

    rendered_body = render_body(preview_body, lead_data)
    rendered_subject = render_body(preview_subject, lead_data)

    # For HTML sequences inject tracking with a placeholder log id so that
    # tracking URLs look realistic (they won't resolve until a real send).
    tracking_urls_note = None
    if preview_is_html and (campaign.track_clicks or campaign.track_opens):
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
        "is_html": preview_is_html,
        "sequence_position": seq.position,
        "variant_label": variant_label,
        "tracking_note": tracking_urls_note,
    }


class TestEmailRequest(BaseModel):
    sequence_id: int
    lead_id: int | None = None
    to_email: str
    variant_id: int | None = None  # If set, send this A/B variant's content


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

    # Resolve variant content when a variant_id is supplied.
    send_body = seq.body or ""
    send_subject = seq.subject or "(no subject)"
    send_is_html = bool(seq.is_html)
    if data.variant_id is not None:
        from app.models import SequenceVariant
        var_result = await db.execute(
            select(SequenceVariant).where(
                SequenceVariant.id == data.variant_id,
                SequenceVariant.sequence_id == data.sequence_id,
            )
        )
        variant = var_result.scalar_one_or_none()
        if variant:
            send_body = variant.body or ""
            send_subject = variant.subject or seq.subject or "(no subject)"
            send_is_html = bool(variant.is_html) if variant.is_html is not None else bool(seq.is_html)

    from app.sender import render_body, get_lead_data

    lead_data: dict = {"name": "Test User", "email": data.to_email}
    if data.lead_id:
        lead_result = await db.execute(select(Lead).where(Lead.id == data.lead_id))
        lead = lead_result.scalar_one_or_none()
        if lead:
            lead_data = get_lead_data(lead)

    rendered_body = render_body(send_body, lead_data)
    rendered_subject = render_body(send_subject, lead_data)

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
            is_html=send_is_html,
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




@router.get("/{campaign_id}/analytics/steps")
async def step_analytics(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Per-step (and per-variant) analytics for a campaign.

    Returns one entry per sequence step, each containing total counts plus
    a breakdown by variant (including the 'default' i.e. no-variant bucket).
    'opportunities' = leads that were sent the step AND are marked interested.
    """
    # Fetch all sequences with their variants
    seq_result = await db.execute(
        select(Sequence)
        .options(selectinload(Sequence.variants))
        .where(Sequence.campaign_id == campaign_id)
        .order_by(Sequence.position)
    )
    sequences = seq_result.scalars().all()

    # All email logs for this campaign (eager-load variant)
    log_result = await db.execute(
        select(EmailLog)
        .options(selectinload(EmailLog.variant))
        .where(EmailLog.campaign_id == campaign_id)
    )
    logs = log_result.scalars().all()

    # Interested leads for this campaign
    interested_result = await db.execute(
        select(CampaignLead.lead_id)
        .where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.interest_status == "interested",
        )
    )
    interested_lead_ids = {r[0] for r in interested_result.all()}

    # Replied lead IDs for this campaign
    replied_result = await db.execute(
        select(LeadReply.lead_id)
        .where(LeadReply.campaign_id == campaign_id)
        .distinct()
    )
    replied_lead_ids = {r[0] for r in replied_result.all()}

    # Build per-step metrics
    analytics = []
    for seq in sequences:
        step_logs = [el for el in logs if el.sequence_index == seq.position]

        # Build variant buckets:  key = variant_id (None for default)
        variant_map: dict = {}  # variant_id -> {label, enabled, sent, opens, clicks, replies, opportunities}

        # Populate "default" bucket
        variant_map[None] = {
            "variant_id": None,
            "variant_label": "Default",
            "sent": 0, "opens": 0, "clicks": 0, "replies": 0, "opportunities": 0,
            "enabled": True,
        }
        # Populate named variant buckets
        for v in seq.variants:
            variant_map[v.id] = {
                "variant_id": v.id,
                "variant_label": v.label or f"Variant {v.id}",
                "sent": 0, "opens": 0, "clicks": 0, "replies": 0, "opportunities": 0,
                "enabled": v.enabled,
            }

        for el in step_logs:
            vid = el.variant_id  # None or a variant id
            bucket = variant_map.get(vid)
            if bucket is None:
                # variant was deleted but logs remain — group under default
                bucket = variant_map[None]
            bucket["sent"] += 1
            if el.opened:
                bucket["opens"] += 1
            if el.clicked:
                bucket["clicks"] += 1
            if el.lead_id in replied_lead_ids:
                bucket["replies"] += 1
            if el.lead_id in interested_lead_ids:
                bucket["opportunities"] += 1

        total_sent = sum(b["sent"] for b in variant_map.values())
        total_opens = sum(b["opens"] for b in variant_map.values())
        total_clicks = sum(b["clicks"] for b in variant_map.values())
        total_replies = sum(b["replies"] for b in variant_map.values())
        total_opportunities = sum(b["opportunities"] for b in variant_map.values())

        analytics.append({
            "sequence_id": seq.id,
            "sequence_index": seq.position,
            "subject": seq.subject or "",
            "total_sent": total_sent,
            "total_opens": total_opens,
            "total_clicks": total_clicks,
            "total_replies": total_replies,
            "total_opportunities": total_opportunities,
            "variants": list(variant_map.values()),
        })

    return analytics


@router.get("/{campaign_id}/sent")
async def list_sent_emails(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Return sent email history for this campaign with full status details."""
    from sqlalchemy.orm import selectinload as _sl
    result = await db.execute(
        select(EmailLog, Lead, CampaignLead)
        .join(Lead, EmailLog.lead_id == Lead.id)
        .outerjoin(
            CampaignLead,
            (CampaignLead.lead_id == EmailLog.lead_id)
            & (CampaignLead.campaign_id == EmailLog.campaign_id),
        )
        .options(
            _sl(EmailLog.variant),
        )
        .where(EmailLog.campaign_id == campaign_id)
        .order_by(EmailLog.sent_at.desc())
    )
    rows = result.all()

    # get replied lead ids
    replied_result = await db.execute(
        select(LeadReply.lead_id)
        .where(LeadReply.campaign_id == campaign_id)
        .distinct()
    )
    replied_ids = {r[0] for r in replied_result.all()}

    return [
        {
            "log_id": el.id,
            "sent_date": el.sent_at.date().isoformat() if el.sent_at else None,
            "sent_at": el.sent_at.isoformat() if el.sent_at else None,
            "sequence_index": el.sequence_index,
            "subject": el.subject or "",
            "lead_id": el.lead_id,
            "lead_email": lead.email,
            "lead_name": lead.name or "",
            "lead_status": (getattr(cl, "enrollment_status", None) or "active") if cl else None,
            "interest": cl.interest_status if cl else None,
            "interest_status": cl.interest_status if cl else None,
            "opened": el.opened,
            "clicked": el.clicked,
            "replied": el.lead_id in replied_ids,
            "variant_id": el.variant_id,
            "variant_label": (el.variant.label if el.variant else None),
        }
        for el, lead, cl in rows
    ]


@router.delete("/{campaign_id}/leads/{lead_id}")
async def remove_lead_from_campaign(
    campaign_id: int,
    lead_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
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
    await db.commit()
    from app.routers.schedule import enqueue_global_recalculate

    enqueue_global_recalculate(background_tasks)
    log.info("remove_lead: campaign=%s lead=%s", campaign_id, lead_id)
    return {"ok": True}


@router.post("/{campaign_id}/leads")
async def bulk_add_leads_to_campaign(
    campaign_id: int,
    leads_data: list[CampaignLeadAdd],
    skip_duplicates: bool = Query(True, description="When True, leads already enrolled in any campaign are skipped and reported in duplicate_leads. When False, leads are added normally (already-enrolled leads in this campaign are silently skipped)."),
    verify_emails: bool = Query(False, description="When True and email verification is configured, verify each lead's email in the background after adding."),
    db: AsyncSession = Depends(get_db),
):
    """
    Add one or more leads to a campaign.
    For each entry: find existing lead by email (or create), then enroll if not already enrolled.
    Queues slots for each newly enrolled lead using the bulk scheduler only
    (does not run a global recalculate). Eligibility matches the send job
    (verification, interest, enrollment, ``stop_on_reply``, etc.).    """
    campaign_result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign_obj = campaign_result.scalar_one_or_none()
    if not campaign_obj:
        raise HTTPException(404, "Campaign not found")
    match_provider = getattr(campaign_obj, "match_lead_provider", False)

    if not leads_data:
        raise HTTPException(400, "No leads provided")

    # Deduplicate within the batch — keep only the first occurrence of each email
    seen_emails: set[str] = set()
    deduped: list[CampaignLeadAdd] = []
    for entry in leads_data:
        norm = entry.email.strip().lower()
        if norm not in seen_emails:
            seen_emails.add(norm)
            deduped.append(entry)
    duplicates_in_batch = len(leads_data) - len(deduped)
    leads_data = deduped

    results = []
    added = 0
    already_enrolled = 0
    errors = 0
    duplicate_leads: list[str] = []
    # Track new enrollments for bulk scheduling after provider detection
    new_enrollments: list[tuple[int, int, str, bool, bool]] = []  # cl.id, lead.id, email, has_prov, can_send

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
            if skip_duplicates:
                # Global check: skip if enrolled in any campaign
                existing_any = await db.execute(
                    select(CampaignLead).where(CampaignLead.lead_id == lead.id)
                )
                if existing_any.scalar_one_or_none():
                    duplicate_leads.append(email)
                    results.append({"email": email, "status": "already_enrolled"})
                    already_enrolled += 1
                    continue
            else:
                # Only check this campaign to avoid a DB constraint violation
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

            # Enroll — scheduling happens after all leads are enrolled (see below)
            cl = CampaignLead(campaign_id=campaign_id, lead_id=lead.id)
            db.add(cl)
            await db.flush()
            _apply_campaign_lead_add_options(cl, lead, entry)
            await db.flush()
            can_send = campaign_lead_may_receive_sends(cl, lead)
            new_enrollments.append((cl.id, lead.id, email, bool(lead.provider), can_send))
            results.append({"email": email, "status": "added", "lead_id": lead.id, "slots_created": 0})
            added += 1

        except Exception as exc:
            log.exception("bulk_add_leads: error processing email %s: %s", email, exc)
            results.append({"email": email, "status": "error", "detail": str(exc)})
            errors += 1

    # ── Detect providers then schedule all new enrollments ──────────────────
    if new_enrollments:
        replied_lead_ids: set[int] = set()
        if getattr(campaign_obj, "stop_on_reply", False):
            _lids = [e[1] for e in new_enrollments]
            _rep = await db.execute(
                select(LeadReply.lead_id).where(
                    LeadReply.campaign_id == campaign_id,
                    LeadReply.lead_id.in_(_lids),
                )
            )
            replied_lead_ids = {row[0] for row in _rep.all()}

        if match_provider:
            # Providers must be known before queue slot assignment so the correct
            # inbox type is selected (Google → Gmail, Office 365 → Office365).
            from app.email_provider import detect_provider_for_email
            needs_detection = [
                (cl_id, lead_id, email)
                for cl_id, lead_id, email, has_prov, _can in new_enrollments
                if not has_prov
            ]
            for _, lead_id, email in needs_detection:
                try:
                    provider = await detect_provider_for_email(email)
                    if provider:
                        lr = await db.execute(select(Lead).where(Lead.id == lead_id))
                        lead_obj = lr.scalar_one_or_none()
                        if lead_obj and lead_obj.provider != provider:
                            lead_obj.provider = provider
                            log.info("Provider detected for lead %s (%s): %s", lead_id, email, provider)
                except Exception as exc:
                    log.debug("Provider detection failed for lead %s %s: %s", lead_id, email, exc)
            if needs_detection:
                await db.flush()

        # When email verification is requested, mark all newly added leads as
        # "pending" and defer scheduling until _run_background_verification
        # resolves them in batch (only valid leads get slots).
        added_lead_ids = [e[1] for e in new_enrollments]
        if verify_emails and added_lead_ids:
            from app.email_verification import PENDING as _VER_PENDING
            _pend_res = await db.execute(select(Lead).where(Lead.id.in_(added_lead_ids)))
            for _pl in _pend_res.scalars().all():
                _pl.email_verification_status = _VER_PENDING
            await db.flush()
            to_schedule_cl_ids: list[int] = []
        else:
            to_schedule_cl_ids = [
                e[0] for e in new_enrollments if e[4] and e[1] not in replied_lead_ids
            ]

        if to_schedule_cl_ids:
            await reserve_slots_for_new_leads_bulk(db, to_schedule_cl_ids, campaign_id)

        # Count slots per lead and populate response
        new_cl_ids = [e[0] for e in new_enrollments]
        slot_counts_result = await db.execute(
            select(QueueSlot.campaign_lead_id, func.count(QueueSlot.id))
            .where(QueueSlot.campaign_lead_id.in_(new_cl_ids))
            .group_by(QueueSlot.campaign_lead_id)
        )
        slot_counts = {row[0]: row[1] for row in slot_counts_result.all()}
        cl_id_by_lead = {e[1]: e[0] for e in new_enrollments}
        for r in results:
            if r.get("status") == "added":
                cl_id = cl_id_by_lead.get(r["lead_id"])
                if cl_id:
                    r["slots_created"] = slot_counts.get(cl_id, 0)
                    log.info(
                        "bulk_add_leads: enrolled lead %s in campaign %s — %d slot(s)",
                        r["lead_id"], campaign_id, r["slots_created"],
                    )

    else:
        added_lead_ids = []

    # Trigger background email verification if requested
    if verify_emails and added_lead_ids:
        import asyncio
        asyncio.create_task(_run_background_verification(added_lead_ids))

    # When provider matching is disabled, still detect providers in the background
    # for leads that don't have one yet (future use / analytics).
    if not match_provider:
        needs_bg = [(lead_id, email) for _, lead_id, email, has_prov, _ in new_enrollments if not has_prov]
        if needs_bg:
            import asyncio
            asyncio.create_task(_detect_providers_background(needs_bg))

    return {
        "ok": True,
        "added": added,
        "already_enrolled": already_enrolled,
        "duplicate_leads": duplicate_leads,
        "duplicates_in_batch": duplicates_in_batch,
        "errors": errors,
        "results": results,
        "verification_queued": verify_emails and bool(added_lead_ids),
    }


# ── Background email verification ────────────────────────────────────────────

async def _detect_providers_background(lead_email_pairs: list[tuple[int, str]]) -> None:
    """Detect email provider for each lead via DNS MX lookup and persist the result."""
    from app.database import AsyncSessionLocal
    from app.email_provider import detect_provider_for_email

    async with AsyncSessionLocal() as db:
        for lead_id, email in lead_email_pairs:
            try:
                provider = await detect_provider_for_email(email)
                if provider:
                    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
                    lead = lead_result.scalar_one_or_none()
                    if lead and lead.provider != provider:
                        lead.provider = provider
                        await db.flush()
                        log.info("Provider detected for lead %s (%s): %s", lead_id, email, provider)
            except Exception as exc:
                log.debug("Provider detection failed for lead %s %s: %s", lead_id, email, exc)
        await db.commit()


async def _run_background_verification(lead_ids: list[int], *, reverify: bool = False):
    """Verify emails for the given lead IDs in the background.

    When *reverify* is True, leads that already have a verification status are
    re-checked, but only their status is updated when it actually changes.

    Valid leads are scheduled in rolling 60-second batches so that early
    finishers are not held up waiting for the full list to complete.
    """
    import asyncio as _asyncio
    from app.database import AsyncSessionLocal
    from app.app_settings import (
        get_setting,
        EMAIL_VERIFICATION_API_KEY,
        EMAIL_VERIFICATION_PROVIDER,
        EMAIL_VERIFICATION_ENABLED,
        EMAIL_VERIFICATION_CUSTOM_URL,
        EMAIL_VERIFICATION_CUSTOM_FIELD,
        EMAIL_VERIFICATION_CUSTOM_VALID_VALUES,
        EMAIL_VERIFICATION_CUSTOM_INVALID_VALUES,
        EMAIL_VERIFICATION_CUSTOM_METHOD,
    )
    from app.email_verification import verify_single, BLOCK_SEND_STATUSES, PENDING, CustomHttpProvider
    import json

    _FLUSH_INTERVAL = 60  # seconds between rolling schedule flushes

    async def _flush_valid(valid_ids: list[int], flush_db) -> None:
        """Schedule a batch of newly-verified valid leads (grouped by campaign)."""
        if not valid_ids:
            return
        from app.queue_logic import reserve_slots_for_new_leads_bulk as _rsfnlb
        _cl_res = await flush_db.execute(
            select(CampaignLead).where(CampaignLead.lead_id.in_(valid_ids))
        )
        _campaign_groups: dict[int, list[int]] = {}
        for _cl in _cl_res.scalars().all():
            _campaign_groups.setdefault(_cl.campaign_id, []).append(_cl.id)
        for _cid, _cl_ids in _campaign_groups.items():
            try:
                await _rsfnlb(flush_db, _cl_ids, _cid)
                log.info("Batch-scheduled %d verified leads for campaign %s", len(_cl_ids), _cid)
            except Exception as _exc:
                log.exception("Failed to schedule verified leads for campaign %s: %s", _cid, _exc)
        await flush_db.commit()

    async with AsyncSessionLocal() as db:
        try:
            enabled = (await get_setting(db, EMAIL_VERIFICATION_ENABLED) or "false").lower() in ("true", "1", "yes")
            if not enabled:
                log.info("Email verification disabled, skipping background verification")
                return
            provider_name = await get_setting(db, EMAIL_VERIFICATION_PROVIDER) or "mailtester_ninja"

            # Build the verifier callable based on provider
            if provider_name == "custom":
                custom_url = await get_setting(db, EMAIL_VERIFICATION_CUSTOM_URL) or ""
                custom_field = await get_setting(db, EMAIL_VERIFICATION_CUSTOM_FIELD) or ""
                valid_vals = json.loads(await get_setting(db, EMAIL_VERIFICATION_CUSTOM_VALID_VALUES) or "[]")
                invalid_vals = json.loads(await get_setting(db, EMAIL_VERIFICATION_CUSTOM_INVALID_VALUES) or "[]")
                custom_method = await get_setting(db, EMAIL_VERIFICATION_CUSTOM_METHOD) or "GET"
                if not custom_url or "{email}" not in custom_url:
                    log.warning("Custom email verification provider has no valid URL template configured")
                    return
                _cp = CustomHttpProvider(custom_url, custom_field, valid_vals, invalid_vals, custom_method)
                async def _do_verify(email: str) -> "VerificationResult":  # type: ignore[name-defined]  # noqa: E731
                    return await _cp.verify(email, "")
            else:
                api_key = await get_setting(db, EMAIL_VERIFICATION_API_KEY) or ""
                if not api_key:
                    log.warning("Email verification enabled but no API key configured")
                    return
                async def _do_verify(email: str) -> "VerificationResult":  # type: ignore[name-defined]  # noqa: E731
                    return await verify_single(email, api_key, provider_name)

            # Mark leads as pending (skip if we are only re-verifying – they may
            # already have a status we want to compare against)
            result = await db.execute(select(Lead).where(Lead.id.in_(lead_ids)))
            leads = result.scalars().all()
            if not reverify:
                for lead in leads:
                    lead.email_verification_status = PENDING
                await db.commit()

            # Rolling buffer: accumulated valid lead IDs waiting to be scheduled.
            # We flush every _FLUSH_INTERVAL seconds so early-verified leads are
            # scheduled without waiting for the entire batch to finish.
            pending_valid: list[int] = []
            last_flush_at = _asyncio.get_event_loop().time()

            for lead in leads:
                try:
                    old_verif = lead.email_verification_status
                    old_status = lead.status

                    vr = await _do_verify(lead.email)

                    # Re-fetch to avoid stale state from other writers
                    result = await db.execute(select(Lead).where(Lead.id == lead.id))
                    fresh_lead = result.scalar_one_or_none()
                    if fresh_lead:
                        new_verif = vr.status
                        new_status = fresh_lead.status  # may change below

                        # In reverify mode skip update when nothing changed
                        verif_changed = old_verif != new_verif
                        if reverify and not verif_changed:
                            log.info(
                                "Reverify lead %s (%s): status unchanged (%s)",
                                lead.id, lead.email, old_verif,
                            )
                            continue

                        fresh_lead.email_verification_status = new_verif
                        fresh_lead.email_verification_result = vr.raw

                        if new_verif in BLOCK_SEND_STATUSES:
                            new_status = fresh_lead.status
                            # Remove any existing queue slots
                            cl_result = await db.execute(
                                select(CampaignLead.id).where(CampaignLead.lead_id == lead.id)
                            )
                            cl_ids = [r[0] for r in cl_result.all()]
                            if cl_ids:
                                await db.execute(
                                    delete(QueueSlot).where(QueueSlot.campaign_lead_id.in_(cl_ids))
                                )
                        else:
                            pending_valid.append(lead.id)
                            new_status = fresh_lead.status

                        await db.commit()

                        # Per-lead change notification (useful for real-time UI polling)
                        if reverify and (verif_changed or old_status != new_status):
                            try:
                                from app.webhooks import fire_webhook_event as _fwe
                                async with AsyncSessionLocal() as _ndb:
                                    await _fwe(_ndb, "lead.verification_changed", {
                                        "lead_id": lead.id,
                                        "lead_email": lead.email,
                                        "old_verification_status": old_verif,
                                        "new_verification_status": new_verif,
                                        "old_lead_status": old_status,
                                        "new_lead_status": new_status,
                                    })
                            except Exception:
                                pass

                    log.info("Verified lead %s (%s): %s - %s", lead.id, lead.email, vr.status, vr.message)
                except Exception as exc:
                    log.exception("Failed to verify lead %s (%s): %s", lead.id, lead.email, exc)

                # Rolling flush: schedule accumulated valid leads every minute
                now = _asyncio.get_event_loop().time()
                if pending_valid and (now - last_flush_at) >= _FLUSH_INTERVAL:
                    await _flush_valid(pending_valid, db)
                    pending_valid = []
                    last_flush_at = _asyncio.get_event_loop().time()

            # Final flush for any remaining valid leads
            if pending_valid:
                await _flush_valid(pending_valid, db)

        except Exception as exc:
            log.exception("Background verification failed: %s", exc)
            # Record failure for health monitoring and fire alert event
            try:
                from app.app_settings import put_setting
                from app import time as _time
                err_msg = f"{type(exc).__name__}: {exc}"[:500]
                await put_setting(db, "email_verification_last_error", err_msg)
                await put_setting(db, "email_verification_last_error_at", _time.utcnow().isoformat() + "Z")
                from app.webhooks import fire_webhook_event
                await fire_webhook_event(db, "feature.error", {
                    "feature": "email_verification",
                    "label": "Email Verification",
                    "error": err_msg,
                })
                await db.commit()
            except Exception:
                pass


@router.post("/{campaign_id}/leads/verify")
async def trigger_verification_for_campaign(
    campaign_id: int,
    reverify: bool = Query(False, description="Re-verify already-verified leads"),
    db: AsyncSession = Depends(get_db),
):
    """Trigger email verification for leads in a campaign.

    By default only leads with no verification status are queued.  Pass
    ``?reverify=true`` to re-check leads that were already verified.
    The response always includes a ``needs_reverify`` boolean so the UI
    can prompt the user when there is nothing new to verify but verified
    leads exist.
    """
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Campaign not found")

    # Count already-verified leads for the needs_reverify hint
    verified_count_result = await db.execute(
        select(func.count())
        .select_from(Lead)
        .join(CampaignLead, CampaignLead.lead_id == Lead.id)
        .where(
            CampaignLead.campaign_id == campaign_id,
            Lead.email_verification_status.isnot(None),
        )
    )
    total_verified = verified_count_result.scalar() or 0

    if reverify:
        # Re-verify ALL leads (unverified + already-verified)
        result = await db.execute(
            select(Lead.id)
            .join(CampaignLead, CampaignLead.lead_id == Lead.id)
            .where(CampaignLead.campaign_id == campaign_id)
        )
    else:
        # Normal mode: only leads with no status yet
        result = await db.execute(
            select(Lead.id)
            .join(CampaignLead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id == campaign_id,
                Lead.email_verification_status.is_(None),
            )
        )
    lead_ids = [r[0] for r in result.all()]

    if not lead_ids:
        return {
            "ok": True,
            "message": "No unverified leads found",
            "queued": 0,
            "needs_reverify": total_verified > 0,
            "total_verified": total_verified,
        }

    import asyncio
    asyncio.create_task(_run_background_verification(lead_ids, reverify=reverify))
    return {"ok": True, "queued": len(lead_ids), "needs_reverify": False, "total_verified": total_verified}


@router.get("/{campaign_id}/leads/verification-status")
async def get_verification_status(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return verification status summary for leads in a campaign."""
    from sqlalchemy import case
    result = await db.execute(
        select(
            Lead.email_verification_status,
            func.count().label("count"),
        )
        .join(CampaignLead, CampaignLead.lead_id == Lead.id)
        .where(CampaignLead.campaign_id == campaign_id)
        .group_by(Lead.email_verification_status)
    )
    rows = result.all()
    status_counts = {status or "unverified": count for status, count in rows}
    return {"statuses": status_counts}


# ---- Export leads as CSV ----
@router.get("/{campaign_id}/leads/export")
async def export_campaign_leads(
    campaign_id: int,
    verification_status: str | None = Query(None, description="Filter by email verification status"),
    status: str | None = Query(None, description="Filter by per-campaign enrollment status"),
    db: AsyncSession = Depends(get_db),
):
    """Export leads for a campaign as a CSV file, with optional filtering."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = result.scalar_one_or_none()
    if not camp:
        raise HTTPException(404, "Campaign not found")

    query = (
        select(CampaignLead, Lead)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .where(CampaignLead.campaign_id == campaign_id)
    )
    if verification_status:
        if verification_status == "unverified":
            query = query.where(Lead.email_verification_status.is_(None))
        else:
            query = query.where(Lead.email_verification_status == verification_status)
    if status:
        query = query.where(CampaignLead.enrollment_status == status.strip().lower())
    query = query.order_by(CampaignLead.enrolled_at.desc())

    result = await db.execute(query)
    rows = result.all()
    lead_ids = [lead.id for _cl, lead in rows]
    opened_set: set[int] = set()
    clicked_set: set[int] = set()
    replied_set: set[int] = set()
    if lead_ids:
        o_r = await db.execute(
            select(EmailLog.lead_id)
            .where(
                EmailLog.campaign_id == campaign_id,
                EmailLog.lead_id.in_(lead_ids),
                EmailLog.opened == True,  # noqa: E712
            )
            .distinct()
        )
        opened_set = {r[0] for r in o_r.all()}
        c_r = await db.execute(
            select(EmailLog.lead_id)
            .where(
                EmailLog.campaign_id == campaign_id,
                EmailLog.lead_id.in_(lead_ids),
                EmailLog.clicked == True,  # noqa: E712
            )
            .distinct()
        )
        clicked_set = {r[0] for r in c_r.all()}
        rep_r = await db.execute(
            select(LeadReply.lead_id)
            .where(
                LeadReply.campaign_id == campaign_id,
                LeadReply.lead_id.in_(lead_ids),
            )
            .distinct()
        )
        replied_set = {r[0] for r in rep_r.all()}

    # Gather all custom_data keys
    all_keys = set()
    for _cl, lead in rows:
        if lead.custom_data:
            all_keys.update(lead.custom_data.keys())
    custom_keys = sorted(all_keys)

    output = io.StringIO()
    writer = csv.writer(output)
    header = list(_CAMPAIGN_LEADS_CSV_BUILTIN) + custom_keys
    writer.writerow(header)
    for cl, lead in rows:
        row = [
            lead.email,
            lead.name or "",
            getattr(cl, "enrollment_status", None) or "active",
            cl.interest_status or "",
            lead.email_verification_status or "",
            "1" if lead.id in opened_set else "0",
            "1" if lead.id in clicked_set else "0",
            "1" if lead.id in replied_set else "0",
        ]
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
    skip_duplicates: bool = Query(True, description="When True, leads already enrolled in any campaign are skipped and reported in duplicate_leads. When False, leads are added normally (already-enrolled leads in this campaign are silently skipped)."),
    verify_emails: bool = Query(False, description="When True, triggers background email verification for added leads"),
    db: AsyncSession = Depends(get_db),
):
    """Import leads from a CSV file. Expects columns: email, name, and any custom fields."""
    campaign_result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign_obj = campaign_result.scalar_one_or_none()
    if not campaign_obj:
        raise HTTPException(404, "Campaign not found")
    match_provider = getattr(campaign_obj, "match_lead_provider", False)

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

    raw_headers = [(f or "").strip() for f in reader.fieldnames]
    if not any(h.lower() == "email" for h in raw_headers if h):
        raise HTTPException(400, "CSV must have an 'email' column")

    _csv_reserved_lower = frozenset(
        {"email", "name", "status", "interest", "email_verification_status"}
    )

    added = 0
    already_enrolled = 0
    errors = 0
    duplicates_in_batch = 0
    duplicate_leads: list[str] = []
    results_list = []
    seen_emails: set[str] = set()
    # Track new enrollments for bulk scheduling after provider detection
    new_enrollments: list[tuple[int, int, str, bool, bool]] = []  # cl.id, lead.id, email, has_prov, can_send

    for row_num, row in enumerate(reader, start=2):
        by_header = {}
        for fk in reader.fieldnames or []:
            h = (fk or "").strip()
            if not h:
                continue
            raw_val = row.get(fk)
            by_header[h] = raw_val.strip() if raw_val else ""

        email = next(
            (by_header[h] for h in raw_headers if h.lower() == "email"),
            "",
        ).strip().lower()
        if not email:
            results_list.append({"row": row_num, "status": "error", "detail": "Empty email"})
            errors += 1
            continue

        if email in seen_emails:
            results_list.append({"row": row_num, "email": email, "status": "duplicate_in_file"})
            duplicates_in_batch += 1
            continue
        seen_emails.add(email)

        name = next(
            (by_header[h] for h in raw_headers if h.lower() == "name"),
            "",
        )
        custom_data = {}
        for h in raw_headers:
            if not h or h.lower() in _csv_reserved_lower:
                continue
            v = by_header.get(h, "")
            if v:
                custom_data[h] = v

        def _csv_cell(field_lower: str):
            for h in raw_headers:
                if h.lower() == field_lower:
                    v = by_header.get(h, "")
                    return v if v else None
            return None

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

            if skip_duplicates:
                # Global check: skip if enrolled in any campaign
                existing_any = await db.execute(
                    select(CampaignLead).where(CampaignLead.lead_id == lead.id)
                )
                if existing_any.scalar_one_or_none():
                    duplicate_leads.append(email)
                    results_list.append({"row": row_num, "email": email, "status": "already_enrolled"})
                    already_enrolled += 1
                    continue
            else:
                # Only check this campaign to avoid a DB constraint violation
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
            _row_opts = CampaignLeadAdd(
                email=email,
                name=name,
                custom_data=custom_data,
                status=_csv_cell("status"),
                interest=_csv_cell("interest"),
                email_verification_status=_csv_cell("email_verification_status"),
            )
            _apply_campaign_lead_add_options(cl, lead, _row_opts)
            await db.flush()
            can_send_csv = campaign_lead_may_receive_sends(cl, lead)
            new_enrollments.append((cl.id, lead.id, email, bool(lead.provider), can_send_csv))
            added += 1
            results_list.append({"row": row_num, "email": email, "status": "added", "lead_id": lead.id})
        except Exception as exc:
            log.exception("import_leads: error on row %d email=%s: %s", row_num, email, exc)
            results_list.append({"row": row_num, "email": email, "status": "error", "detail": str(exc)})
            errors += 1

    # ── Detect providers then schedule all new enrollments ──────────────────
    added_lead_ids_csv: list[int] = []
    if new_enrollments:
        replied_lead_ids_csv: set[int] = set()
        if getattr(campaign_obj, "stop_on_reply", False):
            _lids_csv = [e[1] for e in new_enrollments]
            _rep_csv = await db.execute(
                select(LeadReply.lead_id).where(
                    LeadReply.campaign_id == campaign_id,
                    LeadReply.lead_id.in_(_lids_csv),
                )
            )
            replied_lead_ids_csv = {row[0] for row in _rep_csv.all()}

        if match_provider:
            # Providers must be known before queue slot assignment so the correct
            # inbox type is selected (Google → Gmail, Office 365 → Office365).
            from app.email_provider import detect_provider_for_email
            needs_detection = [
                (cl_id, lead_id, email)
                for cl_id, lead_id, email, has_prov, _can in new_enrollments
                if not has_prov
            ]
            for _, lead_id, email in needs_detection:
                try:
                    provider = await detect_provider_for_email(email)
                    if provider:
                        lr = await db.execute(select(Lead).where(Lead.id == lead_id))
                        lead_obj = lr.scalar_one_or_none()
                        if lead_obj and lead_obj.provider != provider:
                            lead_obj.provider = provider
                            log.info("Provider detected for lead %s (%s): %s", lead_id, email, provider)
                except Exception as exc:
                    log.debug("Provider detection failed for lead %s %s: %s", lead_id, email, exc)
            if needs_detection:
                await db.flush()

        added_lead_ids_csv = [e[1] for e in new_enrollments]
        if verify_emails and added_lead_ids_csv:
            from app.email_verification import PENDING as _VER_PENDING_CSV
            _pend_res2 = await db.execute(select(Lead).where(Lead.id.in_(added_lead_ids_csv)))
            for _pl2 in _pend_res2.scalars().all():
                _pl2.email_verification_status = _VER_PENDING_CSV
            await db.flush()
            to_schedule_cl_ids_csv: list[int] = []
        else:
            to_schedule_cl_ids_csv = [
                e[0] for e in new_enrollments if e[4] and e[1] not in replied_lead_ids_csv
            ]

        if to_schedule_cl_ids_csv:
            await reserve_slots_for_new_leads_bulk(db, to_schedule_cl_ids_csv, campaign_id)

    # Trigger background email verification if requested
    if verify_emails and added_lead_ids_csv:
        import asyncio
        asyncio.create_task(_run_background_verification(added_lead_ids_csv))

    # When provider matching is disabled, still detect providers in the background
    # for leads that don't have one yet (future use / analytics).
    if not match_provider:
        needs_bg = [(lead_id, email) for _, lead_id, email, has_prov, _ in new_enrollments if not has_prov]
        if needs_bg:
            import asyncio
            asyncio.create_task(_detect_providers_background(needs_bg))

    return {
        "ok": True,
        "added": added,
        "already_enrolled": already_enrolled,
        "duplicate_leads": duplicate_leads,
        "duplicates_in_batch": duplicates_in_batch,
        "errors": errors,
        "total_rows": added + already_enrolled + duplicates_in_batch + errors,
        "verification_queued": verify_emails and bool(added_lead_ids_csv),
    }


@router.post("/{campaign_id}/leads/detect-providers")
async def detect_campaign_lead_providers(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Trigger background MX-based provider detection for all leads in this campaign.

    Updates ``lead.provider`` for every enrolled lead that does not yet have
    a detected provider (or re-detects if force=true).  Detection runs
    asynchronously; the endpoint returns immediately.
    """
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Campaign not found")

    # Fetch all distinct leads enrolled in this campaign
    lead_result = await db.execute(
        select(Lead.id, Lead.email)
        .join(CampaignLead, CampaignLead.lead_id == Lead.id)
        .where(CampaignLead.campaign_id == campaign_id)
        .distinct()
    )
    pairs = [(row.id, row.email) for row in lead_result.all()]

    if pairs:
        import asyncio
        asyncio.create_task(_detect_providers_background(pairs))

    return {"ok": True, "queued": len(pairs)}