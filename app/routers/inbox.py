"""Inbox API routes."""
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, exists, update as sa_update

from app.database import get_db
from app.models import Inbox, CampaignInbox, QueueSlot, EmailLog, CampaignLead
from app.schemas import InboxCreate, InboxUpdate, InboxResponse, PauseInboxRequest
from app.queue_logic import compute_effective_daily_limit

log = logging.getLogger("quickly.routes")

router = APIRouter(prefix="/api/inboxes", tags=["inboxes"])


def _normalise_tracking_domain(raw: str | None) -> str:
    """Strip scheme, trailing slashes and paths from a user-supplied tracking domain.
    Returns a clean hostname string, or "" if the input is blank.
    """
    if not raw:
        return ""
    d = raw.strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    # keep only the hostname part
    d = d.split("/")[0].split("?")[0]
    return d


def _compute_effective_limit(inbox: Inbox) -> int:
    """Thin wrapper kept for backward compatibility; delegates to the shared implementation."""
    return compute_effective_daily_limit(inbox)


async def _maybe_complete_ramp_up(inbox: Inbox, db: AsyncSession) -> None:
    """Automatically disable ramp-up once the effective limit reaches max.

    Called after computing the effective limit so the inbox record in the
    database reflects the completed state.
    """
    if not getattr(inbox, "ramp_up_enabled", False):
        return
    if _compute_effective_limit(inbox) >= inbox.max_emails_per_day:
        inbox.ramp_up_enabled = False
        await db.flush()


@router.get("", response_model=list[InboxResponse])
async def list_inboxes(db: AsyncSession = Depends(get_db)):
    # fetch all inboxes first
    result = await db.execute(select(Inbox).order_by(Inbox.id))
    inboxes = result.scalars().all()

    # compute how many emails have been sent today per inbox by grouping
    from sqlalchemy import func

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

    # count pending future queue slots per inbox
    now = datetime.utcnow()
    pending_res = await db.execute(
        select(QueueSlot.inbox_id, func.count(QueueSlot.id))
        .where(QueueSlot.scheduled_date > now)
        .group_by(QueueSlot.inbox_id)
    )
    pending_counts = {row[0]: row[1] for row in pending_res.all()}

    for i in inboxes:
        i.sent_today = counts.get(i.id, 0)
        i.pending_leads = pending_counts.get(i.id, 0)
        i.effective_max_per_day = _compute_effective_limit(i)
        await _maybe_complete_ramp_up(i, db)
    return inboxes


@router.post("", response_model=InboxResponse)
async def create_inbox(data: InboxCreate, db: AsyncSession = Depends(get_db)):
    # Normalise tracking domain: strip scheme, paths, whitespace
    td = _normalise_tracking_domain(data.tracking_domain)
    inbox = Inbox(
        email=data.email,
        display_name=data.display_name,
        max_emails_per_day=data.max_emails_per_day,
        wait_minutes_between=data.wait_minutes_between,
        max_jitter_seconds=data.max_jitter_seconds,
        provider=data.provider,
        tracking_domain=td or None,
        ramp_up_enabled=data.ramp_up_enabled,
        ramp_up_period_days=data.ramp_up_period_days,
        ramp_up_start=data.ramp_up_start,
        ramp_up_started_at=datetime.utcnow() if data.ramp_up_enabled else None,
    )
    db.add(inbox)
    await db.flush()
    await db.refresh(inbox)
    inbox.sent_today = 0
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    await _maybe_complete_ramp_up(inbox, db)
    return inbox


@router.get("/{inbox_id}", response_model=InboxResponse)
async def get_inbox(inbox_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")

    # attach today's sent count as above
    from sqlalchemy import func
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
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    await _maybe_complete_ramp_up(inbox, db)
    return inbox


@router.patch("/{inbox_id}", response_model=InboxResponse)
async def update_inbox(inbox_id: int, data: InboxUpdate, db: AsyncSession = Depends(get_db)):
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
    if data.max_jitter_seconds is not None:
        inbox.max_jitter_seconds = data.max_jitter_seconds
        capacity_changed = True
    if data.provider is not None:
        inbox.provider = data.provider
    if data.tracking_domain is not None:
        inbox.tracking_domain = _normalise_tracking_domain(data.tracking_domain) or None
    if data.ramp_up_enabled is not None:
        if data.ramp_up_enabled != inbox.ramp_up_enabled:
            capacity_changed = True
        inbox.ramp_up_enabled = data.ramp_up_enabled
        # Reset the start date whenever ramp-up is turned on
        if data.ramp_up_enabled:
            inbox.ramp_up_started_at = datetime.utcnow()
    if data.ramp_up_period_days is not None:
        if data.ramp_up_period_days != inbox.ramp_up_period_days:
            capacity_changed = True
        inbox.ramp_up_period_days = data.ramp_up_period_days
    if data.ramp_up_start is not None:
        if data.ramp_up_start != inbox.ramp_up_start:
            capacity_changed = True
            # Reset the clock when the starting number changes
            if inbox.ramp_up_enabled:
                inbox.ramp_up_started_at = datetime.utcnow()
        inbox.ramp_up_start = data.ramp_up_start
    if data.paused is not None:
        inbox.paused = data.paused
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
        from app.routers.schedule import recalculate_all_campaigns
        await recalculate_all_campaigns(db)
    await db.refresh(inbox)
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    await _maybe_complete_ramp_up(inbox, db)
    return inbox


@router.post("/{inbox_id}/pause", response_model=InboxResponse)
async def pause_inbox(inbox_id: int, body: PauseInboxRequest, db: AsyncSession = Depends(get_db)):
    """Pause an inbox. Choose what happens to leads currently assigned to it:
    - action='pause_leads': set sending_paused=True on all affected CampaignLeads.
    - action='reassign': pause and run a full recalculation so remaining inboxes
      automatically absorb the leads.
    """
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    if inbox.paused:
        raise HTTPException(400, "Inbox is already paused")
    if body.action not in ("pause_leads", "reassign"):
        raise HTTPException(400, "action must be 'pause_leads' or 'reassign'")

    now = datetime.utcnow()

    # Mark inbox as paused first so downstream logic (recalc) excludes it
    inbox.paused = True
    await db.flush()

    if body.action == "pause_leads":
        # Find all CampaignLead IDs that have future queue slots on this inbox
        cl_id_rows = await db.execute(
            select(QueueSlot.campaign_lead_id)
            .where(QueueSlot.inbox_id == inbox_id, QueueSlot.scheduled_date > now)
            .distinct()
        )
        cl_ids = [r[0] for r in cl_id_rows.all()]
        if cl_ids:
            await db.execute(
                sa_update(CampaignLead)
                .where(CampaignLead.id.in_(cl_ids))
                .values(sending_paused=True)
            )
        # Remove the now-orphaned future slots so they don't appear in the schedule
        from sqlalchemy import delete as sa_delete
        await db.execute(
            sa_delete(QueueSlot)
            .where(QueueSlot.inbox_id == inbox_id, QueueSlot.scheduled_date > now)
        )
        log.info("pause_inbox: inbox=%s paused %d leads and removed their queue slots", inbox_id, len(cl_ids))

    elif body.action == "reassign":
        # Full recalculation: the scheduler rebuilds slots across all active
        # inboxes, automatically excluding the now-paused one.
        from app.routers.schedule import recalculate_all_campaigns
        await recalculate_all_campaigns(db)
        log.info("pause_inbox: inbox=%s slots redistributed via recalculation", inbox_id)

    await db.refresh(inbox)
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    inbox.sent_today = 0
    return inbox


@router.post("/{inbox_id}/unpause", response_model=InboxResponse)
async def unpause_inbox(inbox_id: int, db: AsyncSession = Depends(get_db)):
    """Resume a paused inbox.

    Also un-pauses any CampaignLeads that were paused because of this inbox
    (i.e. leads in campaigns using this inbox that have sending_paused=True)
    and triggers a full queue recalculation so they get new slots.
    """
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")

    inbox.paused = False
    await db.flush()

    # Un-pause leads that belong to campaigns using this inbox
    campaign_id_rows = await db.execute(
        select(CampaignInbox.campaign_id).where(CampaignInbox.inbox_id == inbox_id)
    )
    campaign_ids = [r[0] for r in campaign_id_rows.all()]
    if campaign_ids:
        resumed = await db.execute(
            sa_update(CampaignLead)
            .where(
                CampaignLead.campaign_id.in_(campaign_ids),
                CampaignLead.sending_paused == True,  # noqa: E712
            )
            .values(sending_paused=False)
        )
        log.info(
            "unpause_inbox: inbox=%s resumed %s leads across campaigns %s",
            inbox_id, resumed.rowcount, campaign_ids,
        )

    # Rebuild queue slots for the now-active inbox
    from app.routers.schedule import recalculate_all_campaigns
    await recalculate_all_campaigns(db)

    await db.refresh(inbox)
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    inbox.sent_today = 0
    log.info("unpause_inbox: inbox=%s resumed and queue recalculated", inbox_id)
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
