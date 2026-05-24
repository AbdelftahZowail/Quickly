"""Global schedule API — all sent + scheduled emails across all campaigns."""
import logging
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, Query, HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from pathlib import Path

from app import database as _app_db
from app.database import get_db
from app.campaign_lead_status import campaign_lead_schedule_eligibility_clause
from app.models import (
    QueueSlot, CampaignLead, Campaign, Sequence, SequenceVariant,
    Lead, Inbox, EmailLog, EmailOpen, EmailClick, CustomEmailOverride,
)
from app.sender import render_body, get_lead_data
from app.queue_logic import recalculate_queue_after_sequence_change_for_leads, recalculate_queue_round_robin
from app import time as time_provider
from app.app_settings import (
    get_scheduling_strategy,
    get_setting,
    put_setting,
    GLOBAL_RECALC_FINISHED_AT_KEY,
)
from smoke_test.validate_scheduled_emails import EmailScheduleValidator

log = logging.getLogger("quickly.schedule")

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


async def run_recalculate_all_in_new_session() -> None:
    """Run a full global recalculate in a fresh session (for background tasks).

    Do not use a module-level asyncio.Lock: pytest-asyncio may use a different
    event loop per test, which leaves a process-global lock unusable and causes
    hangs. Production overlap is rare; callers already commit before enqueue.
    """
    async with _app_db.AsyncSessionLocal() as session:
        try:
            await recalculate_all_campaigns(session)
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("run_recalculate_all_in_new_session failed")


def enqueue_global_recalculate(background_tasks: BackgroundTasks) -> None:
    """Queue a global recalculate to run after the response is sent."""
    background_tasks.add_task(run_recalculate_all_in_new_session)


async def _record_global_recalc_finished(db: AsyncSession) -> None:
    """Persist a fresh timestamp so clients can detect async recalc completion."""
    await put_setting(
        db,
        GLOBAL_RECALC_FINISHED_AT_KEY,
        datetime.now(timezone.utc).isoformat(),
    )
    await db.flush()


def _utc_iso(dt) -> "str | None":
    """Return an ISO-8601 UTC timestamp with a 'Z' suffix so browsers correctly
    convert it to the user's local timezone.  Naive datetime objects stored in
    this application are always UTC (server runs in UTC in Docker)."""
    if dt is None:
        return None
    return dt.isoformat() + "Z"


def _serialize_sent(
    el, lead, campaign, seq, inbox, campaign_lead, include_body: bool, include_events: bool,
    resolved_content: dict | None = None,
) -> dict:
    enr = (
        (getattr(campaign_lead, "enrollment_status", None) or "active")
        if campaign_lead is not None
        else (lead.status or "active")
    )
    # Use pre-resolved content (with variables rendered, variant applied) if available
    if resolved_content and include_body:
        body = resolved_content["body"]
        is_html = resolved_content["is_html"]
        variant_id = resolved_content.get("variant_id")
        has_variants = resolved_content.get("has_variants", False)
    else:
        body = seq.body if (seq and include_body) else ""
        is_html = bool(seq.is_html) if seq else False
        variant_id = None
        has_variants = False
    return {
        "type": "sent",
        "log_id": el.id,
        "sent_at": _utc_iso(el.sent_at),
        "sent_date": el.sent_at.date().isoformat() if el.sent_at else None,
        "subject": el.subject or "",
        "message_id": el.message_id or "",
        "sequence_index": el.sequence_index,
        "sequence_id": seq.id if seq else None,
        "sequence_body": body,
        "sequence_is_html": is_html,
        "sequence_wait_days": seq.wait_days_after_previous if seq else 0,
        "variant_id": variant_id,
        "has_variants": has_variants,
        "lead_id": lead.id,
        "lead_email": lead.email,
        "lead_name": lead.name or "",
        "lead_status": enr,
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "campaign_sending_days": campaign.sending_days or [],
        "campaign_hours_start": campaign.sending_hours_start or "09:00",
        "campaign_hours_end": campaign.sending_hours_end or "17:00",
        "campaign_wait_minutes": (inbox.wait_minutes_between if inbox else None) or 5,
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


def _serialize_scheduled(
    slot, campaign_lead, lead, campaign, inbox, seq,
    include_body: bool,
    resolved_content: dict | None = None,
) -> dict:
    enr = getattr(campaign_lead, "enrollment_status", None) or "active"
    # Use pre-resolved content (with variables rendered, overrides applied) if available
    if resolved_content:
        subject_display = resolved_content["subject"]
        body = resolved_content["body"] if include_body else ""
        is_html = resolved_content["is_html"]
        variant_id = resolved_content.get("variant_id")
        has_variants = resolved_content.get("has_variants", False)
    else:
        # Fallback: raw sequence content (no resolution)
        subject_display = (seq.fallback_subject or seq.subject or "(reply in thread)") if seq else "(reply in thread)"
        body = seq.body if (seq and include_body) else ""
        is_html = bool(seq.is_html) if seq else False
        variant_id = None
        has_variants = False
    return {
        "type": "scheduled",
        "slot_id": slot.id,
        "scheduled_at": _utc_iso(slot.scheduled_date),
        "scheduled_date": slot.scheduled_date.date().isoformat() if slot.scheduled_date else None,
        "position_in_day": slot.position_in_day,
        "sequence_index": slot.sequence_index,
        "subject": subject_display,
        "sequence_id": seq.id if seq else None,
        "sequence_body": body,
        "sequence_is_html": is_html,
        "sequence_wait_days": seq.wait_days_after_previous if seq else 0,
        "variant_id": variant_id,
        "has_variants": has_variants,
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
        "campaign_wait_minutes": inbox.wait_minutes_between or 5,
        "campaign_stop_on_reply": campaign.stop_on_reply,
    }


def _variants_loaded(sequence) -> bool:
    """Check if ``sequence.variants`` is already in the instance dict
    (pre-loaded via selectinload) without triggering a lazy load."""
    try:
        from sqlalchemy.orm.attributes import instance_state
        state = instance_state(sequence)
        return "variants" in state.dict
    except Exception:
        return False


async def _resolve_content(
    db: AsyncSession,
    sequence: Sequence | None,
    lead: Lead,
    campaign_lead: CampaignLead | None,
    campaign: Campaign | None,
    variant_id: int | None,
) -> dict:
    """Resolve the effective subject, body, and is_html for a sent or scheduled email.

    This mirrors the resolution logic from ``jobs.py``'s ``run_send_job``:
      1. A/B variant selected via *variant_id* (from slot or email log)
      2. Personalized sequence override (CustomEmailOverride)
      3. Fallback content for personalized sequences
      4. Variable substitution via ``render_body()``

    Returns ``{subject, body, is_html, variant_id, has_variants}``.
    """
    if sequence is None:
        return {
            "subject": "(reply in thread)",
            "body": "",
            "is_html": False,
            "variant_id": None,
            "has_variants": False,
        }

    # ── Step 1: Resolve base content (variant → default) ──────────────────
    seq_subject = sequence.subject
    seq_body = sequence.body
    seq_is_html = sequence.is_html
    chosen_variant_id = None
    has_variants = False

    if getattr(sequence, "sequence_type", "standard") != "personalized":
        # Standard sequence: check for A/B variant
        variants = (
            sequence.variants
            if _variants_loaded(sequence)
            else []
        )
        enabled_variants = [v for v in variants if v.enabled]
        if enabled_variants:
            has_variants = True
            # Use the pre-assigned (or sent) variant if one exists
            if variant_id is not None:
                chosen = next(
                    (v for v in enabled_variants if v.id == variant_id),
                    None,
                )
                if chosen is not None:
                    chosen_variant_id = chosen.id
                    if chosen.subject is not None:
                        seq_subject = chosen.subject
                    if chosen.body:
                        seq_body = chosen.body
                    if chosen.is_html is not None:
                        seq_is_html = chosen.is_html
            # If no variant, default content is shown

    # ── Step 2: Personalized sequence override ────────────────────────────
    if getattr(sequence, "sequence_type", "standard") == "personalized":
        if campaign_lead is not None:
            ov_res = await db.execute(
                select(CustomEmailOverride).where(
                    CustomEmailOverride.campaign_lead_id == campaign_lead.id,
                    CustomEmailOverride.sequence_id == sequence.id,
                )
            )
            override = ov_res.scalar_one_or_none()
            if override:
                if override.subject is not None:
                    seq_subject = override.subject
                elif sequence.fallback_subject:
                    seq_subject = sequence.fallback_subject
                if override.body is not None:
                    seq_body = override.body
                elif sequence.fallback_body:
                    seq_body = sequence.fallback_body
                if override.is_html is not None:
                    seq_is_html = override.is_html
            elif sequence.fallback_subject or sequence.fallback_body:
                if sequence.fallback_subject:
                    seq_subject = sequence.fallback_subject
                if sequence.fallback_body:
                    seq_body = sequence.fallback_body

    # ── Step 3: Subject resolution ────────────────────────────────────────
    effective_subject = seq_subject
    subject_display = (effective_subject or "").strip()
    if not subject_display:
        subject_display = "(reply in thread)"

    # ── Step 4: is_html resolution ────────────────────────────────────────
    if seq_is_html is not None:
        is_html = bool(seq_is_html)
    else:
        import re
        is_html = bool(re.search(r'<[a-zA-Z][^>]*>', seq_body or ""))

    # ── Step 5: Variable substitution ─────────────────────────────────────
    lead_data = get_lead_data(lead)
    rendered_subject = render_body(subject_display, lead_data)
    rendered_body = render_body(seq_body, lead_data) if seq_body else ""

    return {
        "subject": rendered_subject,
        "body": rendered_body,
        "is_html": is_html,
        "variant_id": chosen_variant_id,
        "has_variants": has_variants,
    }


async def _resolve_scheduled_content(
    db: AsyncSession,
    slot: QueueSlot,
    sequence: Sequence | None,
    lead: Lead,
    campaign_lead: CampaignLead | None,
    campaign: Campaign,
    inbox: Inbox,
) -> dict:
    """Resolve content for a scheduled slot (reads variant_id from slot)."""
    return await _resolve_content(
        db, sequence, lead, campaign_lead, campaign, slot.variant_id
    )


async def _resolve_sent_content(
    db: AsyncSession,
    email_log: EmailLog,
    sequence: Sequence | None,
    lead: Lead,
    campaign_lead: CampaignLead | None,
    campaign: Campaign,
) -> dict:
    """Resolve the effective body/is_html for a sent email.

    The subject is taken from *email_log.subject* (already rendered at send
    time).  The body is reconstructed from the sequence and the variant that
    was actually sent (*email_log.variant_id*), with variable substitution.
    """
    resolved = await _resolve_content(
        db, sequence, lead, campaign_lead, campaign, email_log.variant_id
    )
    # Override subject with the sent-time rendered value
    resolved["subject"] = email_log.subject or resolved["subject"]
    return resolved


async def _assign_variants_to_slots(db: AsyncSession) -> None:
    """Pre-assign A/B variants to all queue slots that have sequences with
    enabled variants.  Runs after every recalculation.

    For each slot whose sequence has enabled A/B variants, randomly pick one
    variant and store its id on the slot.  Slots for sequences without
    variants or with only disabled variants are left with ``variant_id = NULL``
    (meaning default content will be used at send time).
    """
    import random

    # Fetch all queue slots joined with campaign_lead → campaign → sequence
    # to get the sequence for each slot.
    SeqAlias = aliased(Sequence)
    rows = await db.execute(
        select(QueueSlot, SeqAlias)
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .join(Campaign, CampaignLead.campaign_id == Campaign.id)
        .join(
            SeqAlias,
            (SeqAlias.campaign_id == Campaign.id)
            & (SeqAlias.position == QueueSlot.sequence_index),
        )
        .options(selectinload(SeqAlias.variants))
    )

    updates = 0
    for slot, seq in rows.all():
        enabled_variants = [
            v for v in getattr(seq, 'variants', []) if v.enabled
        ]
        if not enabled_variants:
            # No variants to assign — leave variant_id as NULL
            if slot.variant_id is not None:
                slot.variant_id = None
                updates += 1
            continue
        # options: None = default content, or any enabled variant
        chosen = random.choice([None] + enabled_variants)
        if chosen is None:
            # Default content chosen — set variant_id to None
            if slot.variant_id is not None:
                slot.variant_id = None
                updates += 1
            continue
        if slot.variant_id != chosen.id:
            slot.variant_id = chosen.id
            updates += 1

    if updates:
        await db.flush()
        log.info("_assign_variants_to_slots: updated %d slot(s)", updates)
    else:
        log.info("_assign_variants_to_slots: no changes needed")



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

    # When include_body is requested, resolve content for all sent emails
    resolved_map: dict[int, dict] = {}  # log_id -> resolved_content
    if include_body:
        # Pre-load sequence variants for all relevant sequences
        seq_ids = {seq.id for _el, _lead, _camp, seq, _inbox, _cl in rows if seq is not None}
        if seq_ids:
            seqs_result = await db.execute(
                select(Sequence)
                .options(selectinload(Sequence.variants))
                .where(Sequence.id.in_(seq_ids))
            )
            seqs_with_variants = {s.id: s for s in seqs_result.scalars().all()}
            for row_idx in range(len(rows)):
                el, lead, campaign, seq, inbox, cl = rows[row_idx]
                if seq is not None and seq.id in seqs_with_variants:
                    seq.variants = seqs_with_variants[seq.id].variants

        for el, lead, campaign, seq, inbox, cl in rows:
            resolved_map[el.id] = await _resolve_sent_content(
                db, el, seq, lead, cl, campaign
            )

    return [
        _serialize_sent(el, lead, campaign, seq, inbox, cl, include_body, include_events,
                        resolved_content=resolved_map.get(el.id))
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

    # When include_body is requested, batch-resolve content for all slots
    resolved_map: dict[int, dict] = {}  # slot_id -> resolved_content
    if include_body:
        # Pre-load sequence variants for all relevant sequences
        seq_ids = {seq.id for _slot, _cl, _camp, _lead, _inbox, seq in rows if seq is not None}
        if seq_ids:
            seqs_result = await db.execute(
                select(Sequence)
                .options(selectinload(Sequence.variants))
                .where(Sequence.id.in_(seq_ids))
            )
            seqs_with_variants = {s.id: s for s in seqs_result.scalars().all()}
            # Re-attach variants to the aliased sequences in rows
            for row_idx in range(len(rows)):
                slot, cl, campaign, lead, inbox, seq = rows[row_idx]
                if seq is not None and seq.id in seqs_with_variants:
                    seq.variants = seqs_with_variants[seq.id].variants

        for slot, cl, campaign, lead, inbox, seq in rows:
            resolved_map[slot.id] = await _resolve_scheduled_content(
                db, slot, seq, lead, cl, campaign, inbox
            )

    return [
        _serialize_scheduled(
            slot, cl, lead, campaign, inbox, seq, include_body,
            resolved_content=resolved_map.get(slot.id),
        )
        for slot, cl, campaign, lead, inbox, seq in rows
    ]


@router.get("/sent/{log_id}")
async def sent_detail(log_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch a single sent email with full sequence body, variables rendered, and events."""
    SeqAlias = aliased(Sequence)
    InboxAlias = aliased(Inbox)
    ClAlias = aliased(CampaignLead)
    result = await db.execute(
        select(EmailLog, Lead, Campaign, SeqAlias, InboxAlias, ClAlias)
        .options(
            selectinload(EmailLog.opens),
            selectinload(EmailLog.clicks),
            selectinload(SeqAlias.variants),
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
    resolved = await _resolve_sent_content(db, el, seq, lead, cl, campaign)
    return _serialize_sent(el, lead, campaign, seq, inbox, cl, include_body=True, include_events=True,
                           resolved_content=resolved)


@router.get("/scheduled/{slot_id}")
async def scheduled_detail(slot_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch a single scheduled slot with full sequence body, variables rendered,
    and all overrides/applicable variants applied."""
    SeqAlias = aliased(Sequence)
    result = await db.execute(
        select(QueueSlot, CampaignLead, Campaign, Lead, Inbox, SeqAlias)
        .options(selectinload(SeqAlias.variants))
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
    resolved = await _resolve_scheduled_content(db, slot, seq, lead, _cl, campaign, inbox)
    return _serialize_scheduled(slot, _cl, lead, campaign, inbox, seq, include_body=True, resolved_content=resolved)



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
    finished_at = await get_setting(db, GLOBAL_RECALC_FINISHED_AT_KEY)
    return {
        "total_sent": sent_count.scalar() or 0,
        "total_scheduled": scheduled_count.scalar() or 0,
        "total_campaigns": campaign_count.scalar() or 0,
        "global_recalc_finished_at": finished_at,
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


async def recalculate_all_campaigns(db: AsyncSession) -> dict:
    """Recalculate queue slots for all campaigns while preserving inbox assignments.

    Callers: send job (await inline), HTTP ``sync=true``, smoke tests, or
    :func:`run_recalculate_all_in_new_session` for deferred runs.

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
        await _record_global_recalc_finished(db)
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
        await _record_global_recalc_finished(db)
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

    # Pre-assign A/B variants to all newly created slots
    await _assign_variants_to_slots(db)

    slot_count = await db.execute(select(func.count(QueueSlot.id)))
    total_slots = slot_count.scalar() or 0

    log.info(
        "recalculate_all_campaigns: completed — strategy=%s, processed %d campaigns, %d -> %d slots",
        strategy, campaigns_processed, initial_slots, total_slots,
    )
    await _record_global_recalc_finished(db)
    return {
        "ok": True,
        "strategy": strategy,
        "campaigns_processed": campaigns_processed,
        "initial_slots": initial_slots,
        "total_slots": total_slots,
    }


@router.post("/recalculate-all")
async def recalculate_all_endpoint(
    background_tasks: BackgroundTasks,
    sync: bool = Query(
        False,
        description="If true, run inline and return full stats (tests / startup). "
        "Default defers work until after the response.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Queue a global recalculate by default; use ``sync=true`` to block until done."""
    if sync:
        return await recalculate_all_campaigns(db)
    enqueue_global_recalculate(background_tasks)
    return {"ok": True, "accepted": True}


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
