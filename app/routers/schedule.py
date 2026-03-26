"""Global schedule API — all sent + scheduled emails across all campaigns."""
import logging
import os
from datetime import timedelta
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from pathlib import Path

from app.database import get_db
from app.campaign_lead_status import campaign_lead_schedule_eligibility_clause
from app.models import (
    QueueSlot, CampaignLead, Campaign, Sequence, Lead, Inbox, EmailLog,
    EmailOpen, EmailClick,
)
from app.queue_logic import recalculate_queue_after_sequence_change_for_leads, recalculate_queue_round_robin
from app import time as time_provider
from app.app_settings import get_scheduling_strategy
from smoke_test.validate_scheduled_emails import EmailScheduleValidator

log = logging.getLogger("quickly.schedule")

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


def _utc_iso(dt) -> "str | None":
    """Return an ISO-8601 UTC timestamp with a 'Z' suffix so browsers correctly
    convert it to the user's local timezone.  Naive datetime objects stored in
    this application are always UTC (server runs in UTC in Docker)."""
    if dt is None:
        return None
    return dt.isoformat() + "Z"


def _serialize_sent(
    el, lead, campaign, seq, inbox, campaign_lead, include_body: bool, include_events: bool
) -> dict:
    enr = (
        (getattr(campaign_lead, "enrollment_status", None) or "active")
        if campaign_lead is not None
        else (lead.status or "active")
    )
    return {
        "type": "sent",
        "log_id": el.id,
        "sent_at": _utc_iso(el.sent_at),
        "sent_date": el.sent_at.date().isoformat() if el.sent_at else None,
        "subject": el.subject or "",
        "message_id": el.message_id or "",
        "sequence_index": el.sequence_index,
        "sequence_id": seq.id if seq else None,
        "sequence_body": seq.body if (seq and include_body) else "",
        "sequence_is_html": bool(seq.is_html) if seq else False,
        "sequence_wait_days": seq.wait_days_after_previous if seq else 0,
        "lead_id": lead.id,
        "lead_email": lead.email,
        "lead_name": lead.name or "",
        "lead_status": enr,
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "campaign_sending_days": campaign.sending_days or [],
        "campaign_hours_start": campaign.sending_hours_start or "09:00",
        "campaign_hours_end": campaign.sending_hours_end or "17:00",
        "campaign_wait_minutes": campaign.wait_minutes_between or 5,
        "campaign_stop_on_reply": campaign.stop_on_reply,
        "inbox_id": el.inbox_id,
        "inbox_email": inbox.email if inbox else "",
        "inbox_display_name": inbox.display_name if inbox else "",
        "inbox_provider": inbox.provider if inbox else "",
        "opens": [
            {"ip": o.ip_address, "at": _utc_iso(o.opened_at)}
            for o in (el.opens if include_events else [])
        ],
        "clicks": [
            {"ip": c.ip_address, "at": _utc_iso(c.clicked_at)}
            for c in (el.clicks if include_events else [])
        ],
        "opened": bool(el.opens) if include_events else bool(el.opened),
        "clicked": bool(el.clicks) if include_events else bool(el.clicked),
    }


def _serialize_scheduled(slot, campaign_lead, lead, campaign, inbox, seq, include_body: bool) -> dict:
    enr = getattr(campaign_lead, "enrollment_status", None) or "active"
    return {
        "type": "scheduled",
        "slot_id": slot.id,
        "scheduled_at": _utc_iso(slot.scheduled_date),
        "scheduled_date": slot.scheduled_date.date().isoformat() if slot.scheduled_date else None,
        "position_in_day": slot.position_in_day,
        "sequence_index": slot.sequence_index,
        "subject": (seq.subject or "(reply in thread)") if seq else "",
        "sequence_id": seq.id if seq else None,
        "sequence_body": seq.body if (seq and include_body) else "",
        "sequence_is_html": bool(seq.is_html) if seq else False,
        "sequence_wait_days": seq.wait_days_after_previous if seq else 0,
        "inbox_id": inbox.id,
        "inbox_email": inbox.email,
        "inbox_display_name": inbox.display_name or "",
        "inbox_provider": getattr(inbox, "provider", "resend") or "resend",
        "inbox_max_per_day": inbox.max_emails_per_day,
        "lead_id": lead.id,
        "lead_email": lead.email,
        "lead_name": lead.name or "",
        "lead_status": enr,
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "campaign_sending_days": campaign.sending_days or [],
        "campaign_hours_start": campaign.sending_hours_start or "09:00",
        "campaign_hours_end": campaign.sending_hours_end or "17:00",
        "campaign_wait_minutes": campaign.wait_minutes_between or 5,
        "campaign_stop_on_reply": campaign.stop_on_reply,
    }


@router.get("/sent")
async def global_sent(
    days_back: int = Query(30, ge=0, le=3650),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    include_body: bool = Query(False),
    include_events: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """All sent emails across every campaign with full details."""
    # We need: EmailLog + Lead + Campaign + Sequence (matched by campaign_id & sequence_index)
    SeqAlias = aliased(Sequence)
    InboxAlias = aliased(Inbox)
    ClAlias = aliased(CampaignLead)
    query = (
        select(EmailLog, Lead, Campaign, SeqAlias, InboxAlias, ClAlias)
        .join(Lead, EmailLog.lead_id == Lead.id)
        .join(Campaign, EmailLog.campaign_id == Campaign.id)
        .outerjoin(
            ClAlias,
            (ClAlias.lead_id == EmailLog.lead_id)
            & (ClAlias.campaign_id == EmailLog.campaign_id),
        )
        .outerjoin(
            SeqAlias,
            (SeqAlias.campaign_id == Campaign.id)
            & (SeqAlias.position == EmailLog.sequence_index),
        )
        .outerjoin(InboxAlias, EmailLog.inbox_id == InboxAlias.id)
        .order_by(EmailLog.sent_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if include_events:
        query = query.options(
            selectinload(EmailLog.opens),
            selectinload(EmailLog.clicks),
        )
    if days_back > 0:
        since = time_provider.now() - timedelta(days=days_back)
        query = query.where(EmailLog.sent_at >= since)

    result = await db.execute(query)
    rows = result.all()

    return [
        _serialize_sent(el, lead, campaign, seq, inbox, cl, include_body, include_events)
        for el, lead, campaign, seq, inbox, cl in rows
    ]


@router.get("/scheduled")
async def global_scheduled(
    days_ahead: int = Query(30, ge=0, le=3650),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    include_body: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """All upcoming queue slots across every campaign with full details."""
    SeqAlias = aliased(Sequence)
    query = (
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
        .where(
            CampaignLead.sending_paused == False,  # noqa: E712
            CampaignLead.enrollment_status.in_(["active", "contacted"]),
        )
        .order_by(QueueSlot.scheduled_date.asc(), QueueSlot.position_in_day)
        .limit(limit)
        .offset(offset)
    )
    now = time_provider.now()
    if days_ahead > 0:
        end = now + timedelta(days=days_ahead)
        query = query.where(QueueSlot.scheduled_date >= now, QueueSlot.scheduled_date <= end)
    else:
        query = query.where(QueueSlot.scheduled_date >= now)

    result = await db.execute(query)
    rows = result.all()

    return [
        _serialize_scheduled(slot, cl, lead, campaign, inbox, seq, include_body)
        for slot, cl, campaign, lead, inbox, seq in rows
    ]


@router.get("/sent/{log_id}")
async def sent_detail(log_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch a single sent email with full sequence body and events."""
    SeqAlias = aliased(Sequence)
    InboxAlias = aliased(Inbox)
    ClAlias = aliased(CampaignLead)
    result = await db.execute(
        select(EmailLog, Lead, Campaign, SeqAlias, InboxAlias, ClAlias)
        .options(
            selectinload(EmailLog.opens),
            selectinload(EmailLog.clicks),
        )
        .join(Lead, EmailLog.lead_id == Lead.id)
        .join(Campaign, EmailLog.campaign_id == Campaign.id)
        .outerjoin(
            ClAlias,
            (ClAlias.lead_id == EmailLog.lead_id)
            & (ClAlias.campaign_id == EmailLog.campaign_id),
        )
        .outerjoin(
            SeqAlias,
            (SeqAlias.campaign_id == Campaign.id)
            & (SeqAlias.position == EmailLog.sequence_index),
        )
        .outerjoin(InboxAlias, EmailLog.inbox_id == InboxAlias.id)
        .where(EmailLog.id == log_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Sent email not found")
    el, lead, campaign, seq, inbox, cl = row
    return _serialize_sent(el, lead, campaign, seq, inbox, cl, include_body=True, include_events=True)


@router.get("/scheduled/{slot_id}")
async def scheduled_detail(slot_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch a single scheduled slot with full sequence body."""
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
        .where(QueueSlot.id == slot_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Scheduled slot not found")
    slot, _cl, campaign, lead, inbox, seq = row
    return _serialize_scheduled(slot, lead, campaign, inbox, seq, include_body=True)



@router.post("/sent/{log_id}/open")
async def log_open(log_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    """Record an open event against a specific EmailLog entry.

    Payload may include an optional `ip` field.
    """
    # create open row and mark flag
    op = EmailOpen(email_log_id=log_id, ip_address=payload.get("ip"))
    db.add(op)
    await db.flush()
    await db.execute(
        select(EmailLog).where(EmailLog.id == log_id)
    )
    await db.execute(
        EmailLog.__table__.update().where(EmailLog.id == log_id).values(opened=True)
    )
    await db.commit()
    return {"ok": True}

@router.post("/sent/{log_id}/click")
async def log_click(log_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    """Record a click event against a specific EmailLog entry."""
    clk = EmailClick(email_log_id=log_id, ip_address=payload.get("ip"))
    db.add(clk)
    await db.execute(
        EmailLog.__table__.update().where(EmailLog.id == log_id).values(clicked=True)
    )
    await db.commit()
    return {"ok": True}

@router.get("/stats")
async def global_stats(db: AsyncSession = Depends(get_db)):
    """Quick summary for the schedule header."""
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


async def order_campaign_leads_prioritizing_partials(
    session: AsyncSession,
    campaign_leads: list[CampaignLead],
) -> list[int]:
    """Reorder `campaign_leads` so that partially-sent leads appear first across
    campaigns while keeping leads from the same campaign grouped together.

    Ordering produced: c1_partial, c2_partial, ..., c1_new, c2_new, ...

    Side effect: writes a file under `logs/` listing `campaign_lead_id\tcampaign_id\tcampaign_name`
    in the produced order.
    Returns the ordered list of `CampaignLead.id`.
    """
    if not campaign_leads:
        return []

    # Preserve first-seen campaign ordering
    campaign_order: list[int] = []
    for cl in campaign_leads:
        if cl.campaign_id not in campaign_order:
            campaign_order.append(cl.campaign_id)

    # Group leads by campaign while keeping original order
    campaign_to_leads: dict[int, list[CampaignLead]] = {cid: [] for cid in campaign_order}
    for cl in campaign_leads:
        campaign_to_leads[cl.campaign_id].append(cl)

    # Build mapping lead_id -> lead_email for the provided campaign_leads
    lead_ids = [cl.lead_id for cl in campaign_leads]
    lead_id_to_email: dict[int, str] = {}
    if lead_ids:
        lead_rows = await session.execute(select(Lead.id, Lead.email).where(Lead.id.in_(lead_ids)))
        lead_id_to_email = {r.id: r.email for r in lead_rows.all()}

    # Determine which (campaign_id, lead_email) pairs have prior sent emails.
    # Match by campaign_id **and** lead email (not lead id) as requested.
    campaign_ids = list(campaign_to_leads.keys())
    email_log_rows = []
    partial_pairs = set()
    if campaign_ids and lead_id_to_email:
        q = await session.execute(
            select(
                EmailLog.id,
                EmailLog.lead_id,
                Lead.email.label("lead_email"),
                EmailLog.campaign_id,
                EmailLog.sequence_index,
                EmailLog.sent_at,
            )
            .join(Lead, EmailLog.lead_id == Lead.id)
            .where(
                EmailLog.campaign_id.in_(campaign_ids),
                Lead.email.in_(lead_id_to_email.values()),
            )
            .order_by(EmailLog.sent_at.desc())
        )
        email_log_rows = q.all()
        partial_pairs = {(r.campaign_id, r.lead_email) for r in email_log_rows if r.lead_email}

    # Partition campaign_leads into partials (same campaign + matching lead email)
    campaign_to_partials: dict[int, list[CampaignLead]] = {cid: [] for cid in campaign_order}
    campaign_to_new: dict[int, list[CampaignLead]] = {cid: [] for cid in campaign_order}

    for cl in campaign_leads:
        email = lead_id_to_email.get(cl.lead_id, "")
        if (cl.campaign_id, email) in partial_pairs:
            campaign_to_partials[cl.campaign_id].append(cl)
        else:
            campaign_to_new[cl.campaign_id].append(cl)

    # Build ordered list: all campaigns' partials first (preserving campaign order),
    # then all campaigns' new leads
    ordered: list[CampaignLead] = []
    for cid in campaign_order:
        ordered.extend(campaign_to_partials.get(cid, []))
    for cid in campaign_order:
        ordered.extend(campaign_to_new.get(cid, []))

    # Write email logs + before/after ordering to `logs/partial.txt`.
    # New file format (overwrite on each run):
    #   # EMAIL_LOGS
    #   # lead_id\tlead_email\tcampaign_id\tsequence_index\tsent_at
    #   <lead_id>\t<lead_email>\t<campaign_id>\t<sequence_index>\t<sent_at_iso>
    #   \n
    #   # BEFORE
    #   <lead_email>\t<campaign_id>
    #   # AFTER
    #   <lead_email>\t<campaign_id>
    _logs_override = os.environ.get("QUICKLY_TEST_LOGS_DIR")
    if _logs_override:
        logs_dir = Path(_logs_override)
    else:
        project_root = Path(__file__).resolve().parents[2]
        logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    out_path = logs_dir / "partial.txt"
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write("# EMAIL_LOGS\n")
        fh.write("# lead_id\tlead_email\tcampaign_id\tsequence_index\tsent_at\n")
        for r in email_log_rows:
            sent_str = r.sent_at.isoformat() if r.sent_at else ""
            fh.write(f"{r.lead_id}\t{r.lead_email}\t{r.campaign_id}\t{r.sequence_index}\t{sent_str}\n")
        fh.write("\n# BEFORE\n")
        for cl in campaign_leads:
            fh.write(f"{lead_id_to_email.get(cl.lead_id, '')}\t{cl.campaign_id}\n")
        fh.write("# AFTER\n")
        for cl in ordered:
            fh.write(f"{lead_id_to_email.get(cl.lead_id, '')}\t{cl.campaign_id}\n")

    return [cl.id for cl in ordered]


@router.post("/recalculate-all")
async def recalculate_all_campaigns(db: AsyncSession = Depends(get_db)):
    """Recalculate queue slots for all campaigns while preserving inbox assignments.

    Dispatches to the correct scheduling strategy:
    - **priority** (default): campaigns are processed in ascending ``priority`` order;
      leads within each campaign are ordered with partially-sent leads first.
    - **round_robin**: inbox capacity is divided evenly across all active campaigns;
      leads are interleaved in batch-size chunks to keep the slot-count cache alive
      across campaign boundaries.
    """
    strategy = await get_scheduling_strategy(db)
    log.info("recalculate_all_campaigns: starting global recalculation (strategy=%s)", strategy)

    await clear_queue(db)

    # Count existing slots *after* the clear (should be 0, logged for diagnostics)
    slot_count_before = await db.execute(select(func.count(QueueSlot.id)))
    initial_slots = slot_count_before.scalar() or 0
    log.info("recalculate_all_campaigns: %d slots remain after clear", initial_slots)

    # Fetch all campaigns — order by priority (ascending) for consistent behaviour
    result = await db.execute(select(Campaign).order_by(Campaign.priority, Campaign.id))
    campaigns = result.scalars().all()

    if not campaigns:
        log.warning("recalculate_all_campaigns: no campaigns found")
        return {"ok": True, "campaigns_processed": 0, "total_slots": 0, "initial_slots": initial_slots,
                "strategy": strategy}

    campaign_ids = [c.id for c in campaigns]
    # only include leads that are still active; inactive leads will be
    # cleared when we wiped the queue at the top of this routine.
    # Also exclude leads belonging to paused campaigns — the priority strategy
    # handles this inside _recalculate_queue_for_campaign_leads, but the
    # round-robin path (recalculate_queue_round_robin) has no per-campaign
    # paused check and would otherwise re-create slots for paused campaigns.
    active_campaign_ids = [c.id for c in campaigns if not getattr(c, "paused", False)]
    _sched_eligible = campaign_lead_schedule_eligibility_clause()
    cl_result = await db.execute(
        select(CampaignLead)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .where(
            CampaignLead.campaign_id.in_(active_campaign_ids),
            _sched_eligible,
        )
        .order_by(CampaignLead.campaign_id, CampaignLead.id)
    )
    _cls_unsorted = cl_result.scalars().all()
    # Sort by Campaign.priority order (campaign_ids is already priority-sorted).
    # The DB query orders by raw campaign_id (integer), which ignores user-defined priority.
    _priority_index = {cid: idx for idx, cid in enumerate(campaign_ids)}
    campaign_leads = sorted(
        _cls_unsorted,
        key=lambda cl: (_priority_index.get(cl.campaign_id, len(campaign_ids)), cl.id),
    )

    if not campaign_leads:
        log.warning("recalculate_all_campaigns: no CampaignLead rows found for campaigns %s", campaign_ids)
        campaigns_processed = len(campaigns)
        slot_count = await db.execute(select(func.count(QueueSlot.id)))
        total_slots = slot_count.scalar() or 0
        return {"ok": True, "campaigns_processed": campaigns_processed, "initial_slots": initial_slots,
                "total_slots": total_slots, "strategy": strategy}

    if strategy == "round_robin":
        # ── Round-robin: reorder leads as interleaved batches, share one cache ──
        # First put partially-sent leads for each campaign ahead of new ones so
        # follow-ups are still prioritised within each batch.
        ordered_cl_ids = await order_campaign_leads_prioritizing_partials(db, campaign_leads)
        if ordered_cl_ids:
            log.info(
                "recalculate_all_campaigns[round_robin]: %d campaign_leads across %d campaigns",
                len(ordered_cl_ids), len(campaigns),
            )
            await recalculate_queue_round_robin(db, ordered_cl_ids)
        else:
            log.warning("recalculate_all_campaigns[round_robin]: no leads to process")
    else:
        # ── Priority (default): process campaigns in priority order ──
        ordered_cl_ids = await order_campaign_leads_prioritizing_partials(db, campaign_leads)
        if ordered_cl_ids:
            log.info(
                "recalculate_all_campaigns[priority]: %d campaign_leads across %d campaigns (partials first)",
                len(ordered_cl_ids), len(campaigns),
            )
            await recalculate_queue_after_sequence_change_for_leads(db, ordered_cl_ids)
        else:
            log.warning("recalculate_all_campaigns[priority]: no leads to process")

    campaigns_processed = len(campaigns)

    slot_count = await db.execute(select(func.count(QueueSlot.id)))
    total_slots = slot_count.scalar() or 0

    log.info(
        "recalculate_all_campaigns: completed — strategy=%s, processed %d campaigns, %d -> %d slots",
        strategy, campaigns_processed, initial_slots, total_slots,
    )
    return {
        "ok": True,
        "strategy": strategy,
        "campaigns_processed": campaigns_processed,
        "initial_slots": initial_slots,
        "total_slots": total_slots,
    }

@router.post("/validate-queue")
async def validate_queue(db: AsyncSession = Depends(get_db)):
    """Run scheduled-emails validation (uses validate_scheduled_emails.EmailScheduleValidator)."""
    log.info("validate_queue: starting validation of scheduled emails")
    validator = EmailScheduleValidator(db)
    result = await validator.validate_all()

    issues = [
        {
            "check_name": i.check_name,
            "severity": i.severity,
            "lead_email": i.lead_email,
            "campaign_name": i.campaign_name,
            "details": i.details,
        }
        for i in result.issues
    ]

    return {
        "ok": True,
        "total_slots_checked": result.total_slots_checked,
        "total_leads_checked": result.total_leads_checked,
        "total_capacity": result.total_capacity,
        "total_empty_slots": result.total_empty_slots,
        "inbox_stats": result.inbox_stats,
        "issues": issues,
        "has_errors": result.has_errors(),
    }
