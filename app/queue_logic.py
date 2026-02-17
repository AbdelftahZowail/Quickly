"""Queue slot reservation and recalculation logic."""
import logging
from datetime import datetime, date, time, timedelta
from typing import List, Optional, Tuple
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign, Sequence, CampaignLead, QueueSlot, Inbox, EmailLog, CampaignInbox

log = logging.getLogger("campaign_engine.queue")


def _parse_time(s: str) -> time:
    """Parse 'HH:MM' string to a time object."""
    try:
        parts = s.strip().split(":")
        if len(parts) != 2:
            return time(9, 0)
        h = int(parts[0])
        m = int(parts[1])
        if h == 24 and m == 0:
            return time(23, 59)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return time(9, 0)
        return time(h, m)
    except Exception:
        return time(9, 0)


def _estimated_send_time(sending_hours_start: str, wait_minutes: int, position_in_day: int) -> time:
    """Compute the estimated send time for a given position on a day."""
    start = _parse_time(sending_hours_start)
    offset = (position_in_day - 1) * wait_minutes
    total_minutes = start.hour * 60 + start.minute + offset
    h = total_minutes // 60
    m = total_minutes % 60
    if h >= 24:
        h = 23
        m = 59
    return time(h, m)


def _time_to_minutes(t: time) -> int:
    """Minutes from midnight."""
    return t.hour * 60 + t.minute


async def _next_available_send_time_today(
    session: AsyncSession,
    inbox_id: int,
    day: date,
    sending_start: str,
    sending_end: str,
    wait_minutes: int,
    now: datetime,
) -> Optional[datetime]:
    """
    Next available send time today that is >= now and within the sending window.
    Respects existing slots (no double-booking) and the wait_minutes grid.
    Returns None if no slot is left today (window ended or at capacity by time).
    """
    start_t = _parse_time(sending_start)
    end_t = _parse_time(sending_end)
    start_min = _time_to_minutes(start_t)
    end_min = _time_to_minutes(end_t)
    BUFFER_MINUTES = 1

    now_min = (
        _time_to_minutes(now.time()) + BUFFER_MINUTES
        if now.date() == day
        else 0
    )

    day_start = datetime.combine(day, time(0, 0))
    day_end = datetime.combine(day + timedelta(days=1), time(0, 0))
    # Use WITH_FOR_UPDATE to prevent race conditions when multiple requests schedule simultaneously
    result = await session.execute(
        select(QueueSlot.scheduled_date).where(
            QueueSlot.inbox_id == inbox_id,
            QueueSlot.scheduled_date >= day_start,
            QueueSlot.scheduled_date < day_end,
        ).with_for_update()
    )
    existing_minutes = set()
    for (dt,) in result.all():
        existing_minutes.add((dt.hour * 60 + dt.minute))

    # First candidate: next slot on the grid that is >= max(now, start)
    base = max(now_min, start_min)
    # Round up to next grid: start_min + k * wait_minutes >= base
    k = 0 if base <= start_min else ((base - start_min + wait_minutes - 1) // wait_minutes)
    candidate_min = start_min + k * wait_minutes
    while candidate_min <= end_min:
        if candidate_min not in existing_minutes and candidate_min >= now_min:
            return datetime.combine(day, time(candidate_min // 60, candidate_min % 60))
        candidate_min += wait_minutes
    return None


def next_business_date(from_date: date, sending_days: List[int], delta_days: int) -> date:
    """Advance from_date by delta_days counting only business days (in sending_days)."""
    if delta_days <= 0:
        d = from_date
        while d.weekday() not in sending_days:
            d += timedelta(days=1)
        return d
    count = 0
    d = from_date
    while count < delta_days:
        d += timedelta(days=1)
        if d.weekday() in sending_days:
            count += 1
    return d


async def count_slots_on_date(session: AsyncSession, inbox_id: int, day: date) -> int:
    """Count queue slots for this inbox on the given date. Uses FOR UPDATE to prevent race conditions."""
    day_start = datetime.combine(day, datetime.min.time())
    day_end = datetime.combine(day + timedelta(days=1), datetime.min.time())
    q = (
        select(func.count(QueueSlot.id))
        .where(
            QueueSlot.inbox_id == inbox_id,
            QueueSlot.scheduled_date >= day_start,
            QueueSlot.scheduled_date < day_end,
        )
        .with_for_update()
    )
    result = await session.execute(q)
    return result.scalar() or 0


async def _get_preferred_inbox_for_lead(
    session: AsyncSession,
    lead_id: int,
    campaign_id: int,
    available_inbox_ids: List[int],
) -> Optional[int]:
    """
    Check if this lead was already contacted with an inbox in this campaign.
    If yes and that inbox is still available, return it (inbox persistence).
    Otherwise return None to use round-robin.
    """
    result = await session.execute(
        select(EmailLog.inbox_id)
        .where(
            EmailLog.lead_id == lead_id,
            EmailLog.campaign_id == campaign_id,
            EmailLog.inbox_id.isnot(None),
        )
        .order_by(EmailLog.sent_at.desc())
        .limit(1)
    )
    inbox_id = result.scalar_one_or_none()
    if inbox_id and inbox_id in available_inbox_ids:
        log.info("Inbox persistence: lead %s -> inbox %s", lead_id, inbox_id)
        return inbox_id
    return None


async def next_available_slot_position(
    session: AsyncSession, inbox_id: int, day: date
) -> int:
    """Next position_in_day (1-based) for this inbox on this day."""
    count = await count_slots_on_date(session, inbox_id, day)
    return count + 1


async def _pick_inbox_with_capacity(
    session: AsyncSession,
    inboxes: List[Tuple[int, int]],  # (inbox_id, max_per_day)
    day: date,
    round_robin_index: int,
) -> Optional[Tuple[int, int]]:
    """Return (inbox_id, max_per_day) for an inbox that has capacity on day, or None."""
    if not inboxes:
        return None
    for i in range(len(inboxes)):
        idx = (round_robin_index + i) % len(inboxes)
        inbox_id, max_per_day = inboxes[idx]
        count = await count_slots_on_date(session, inbox_id, day)
        if count < max_per_day:
            return (inbox_id, max_per_day)
    return None


async def reserve_slots_for_lead(
    session: AsyncSession,
    campaign_lead_id: int,
    campaign: Campaign,
    inboxes: List[Tuple[int, int, int]],  # (inbox_id, max_per_day, wait_minutes_between)
    sequences: List[Sequence],
    lead_id: int,  # Add lead_id for inbox persistence
    start_date: Optional[date] = None,
    last_sent_sequence_index: int = -1,
) -> None:
    """
    Reserve queue slots for one campaign_lead.
    Each slot is assigned to an inbox that has capacity that day.
    Implements inbox persistence: once a lead is contacted with an inbox, always use that inbox.
    """
    sending_days = campaign.sending_days or [0, 1, 2, 3, 4]
    if not inboxes:
        log.warning("reserve_slots_for_lead: no inboxes for campaign %s", campaign.id)
        return
    if not sequences:
        log.warning("reserve_slots_for_lead: no sequences for campaign %s", campaign.id)
        return

    to_schedule = [
        (i, seq)
        for i, seq in enumerate(sequences)
        if i > last_sent_sequence_index
    ]
    if not to_schedule:
        log.info("reserve_slots_for_lead: nothing to schedule (all already sent) cl=%s", campaign_lead_id)
        return

    # Check for inbox persistence: if this lead was already contacted, prefer that inbox
    available_inbox_ids = [inbox[0] for inbox in inboxes]
    preferred_inbox_id = await _get_preferred_inbox_for_lead(
        session, lead_id, campaign.id, available_inbox_ids
    )
    
    # If we have a preferred inbox, filter to use only that one
    if preferred_inbox_id:
        inboxes = [inbox for inbox in inboxes if inbox[0] == preferred_inbox_id]
        log.info("Using inbox persistence: lead %s locked to inbox %s", lead_id, preferred_inbox_id)

    today = date.today()
    if start_date is None:
        start_date = today
    current_date = next_business_date(start_date, sending_days, 0)
    if current_date < today:
        current_date = next_business_date(today, sending_days, 0)
    scheduled_dates: List[date] = []
    round_robin = 0

    log.info(
        "reserve_slots_for_lead: cl=%s campaign=%s inboxes=%s sequences=%d start=%s last_sent=%d",
        campaign_lead_id, campaign.id, [i[0] for i in inboxes], len(to_schedule), start_date, last_sent_sequence_index,
    )

    sending_start = campaign.sending_hours_start or "09:00"
    sending_end = campaign.sending_hours_end or "17:00"
    end_time = _parse_time(sending_end)
    now = datetime.now()
    today = date.today()

    for idx, seq in to_schedule:
        if idx > 0:
            if scheduled_dates:
                prev_send_date = scheduled_dates[-1]
            elif last_sent_sequence_index >= 0 and start_date is not None:
                # Recalculation: reference the date the last email was actually sent
                prev_send_date = start_date
            else:
                prev_send_date = current_date
            wait_days = seq.wait_days_after_previous
            current_date = next_business_date(prev_send_date, sending_days, wait_days)
            # Never schedule in the past
            if current_date < today:
                current_date = next_business_date(today, sending_days, 0)

        safety = 0
        while safety < 365:
            safety += 1
            picked = await _pick_inbox_with_capacity(
                session, 
                [(i[0], i[1]) for i in inboxes],  # Just (id, max_per_day) for capacity check
                current_date, 
                round_robin
            )
            if picked is not None:
                inbox_id, max_per_day = picked
                # Get wait_minutes for this specific inbox
                wait_min = next(i[2] for i in inboxes if i[0] == inbox_id)
                pos = await next_available_slot_position(session, inbox_id, current_date)

                scheduled_dt: datetime
                if current_date == today:
                    # Today: check if estimated time is in the past
                    est_time = _estimated_send_time(sending_start, wait_min, pos)
                    if est_time <= now.time():
                        next_dt = await _next_available_send_time_today(
                            session, inbox_id, today,
                            sending_start, sending_end, wait_min, now,
                        )
                        if next_dt is not None:
                            scheduled_dt = next_dt
                            log.info(
                                "  -> using next slot today for seq=%d: %s (was past, now %s)",
                                idx, scheduled_dt.strftime("%H:%M"), now.strftime("%H:%M"),
                            )
                        else:
                            log.info(
                                "  -> no slot left today for seq=%d (window ended); moving to next day",
                                idx,
                            )
                            current_date += timedelta(days=1)
                            while current_date.weekday() not in sending_days:
                                current_date += timedelta(days=1)
                            continue
                    else:
                        scheduled_dt = datetime.combine(current_date, est_time)
                else:
                    # Future day: check if estimated time exceeds sending window
                    est_t = _estimated_send_time(sending_start, wait_min, pos)
                    if est_t > end_time:
                        # Overflow to next business day
                        log.info(
                            "  -> seq=%d pos=%d would send at %s (past %s); moving to next day",
                            idx, pos, est_t.strftime("%H:%M"), end_time.strftime("%H:%M"),
                        )
                        current_date += timedelta(days=1)
                        while current_date.weekday() not in sending_days:
                            current_date += timedelta(days=1)
                        continue
                    scheduled_dt = datetime.combine(current_date, est_t)

                slot = QueueSlot(
                    campaign_lead_id=campaign_lead_id,
                    inbox_id=inbox_id,
                    sequence_index=idx,
                    scheduled_date=scheduled_dt,
                    position_in_day=pos,
                )
                session.add(slot)
                # CRITICAL: flush so the next iteration's capacity queries see this slot
                await session.flush()
                log.info(
                    "  -> slot created: seq=%d date=%s inbox=%d pos=%d est_time=%s",
                    idx, current_date, inbox_id, pos,
                    _estimated_send_time(sending_start, wait_min, pos).strftime("%H:%M"),
                )
                scheduled_dates.append(current_date)
                # Update round-robin index
                inbox_tuple = next(i for i in inboxes if i[0] == inbox_id)
                round_robin = (inboxes.index(inbox_tuple) + 1) % len(inboxes)
                break
            current_date += timedelta(days=1)
            while current_date.weekday() not in sending_days:
                current_date += timedelta(days=1)

    log.info("reserve_slots_for_lead: done, created %d slots", len(scheduled_dates))


async def reserve_slots_for_new_lead(
    session: AsyncSession, campaign_lead_id: int, campaign_id: int, start_date: Optional[date] = None
) -> None:
    """When a new lead is added to a campaign, reserve all sequence slots across campaign inboxes."""
    log.info("reserve_slots_for_new_lead: cl=%s campaign=%s", campaign_lead_id, campaign_id)

    # Get campaign_lead to extract lead_id
    cl_result = await session.execute(
        select(CampaignLead).where(CampaignLead.id == campaign_lead_id)
    )
    cl = cl_result.scalar_one_or_none()
    if not cl:
        log.error("reserve_slots_for_new_lead: campaign_lead %s not found", campaign_lead_id)
        return

    result = await session.execute(
        select(Campaign).where(Campaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        log.error("reserve_slots_for_new_lead: campaign %s not found", campaign_id)
        return

    result = await session.execute(
        select(CampaignInbox, Inbox)
        .join(Inbox, CampaignInbox.inbox_id == Inbox.id)
        .where(CampaignInbox.campaign_id == campaign_id)
        .order_by(CampaignInbox.position, CampaignInbox.inbox_id)
    )
    rows = result.all()
    inboxes = [(row[1].id, row[1].max_emails_per_day, row[1].wait_minutes_between) for row in rows]
    if not inboxes:
        log.warning("reserve_slots_for_new_lead: no inboxes for campaign %s", campaign_id)
        return

    result = await session.execute(
        select(Sequence).where(Sequence.campaign_id == campaign_id).order_by(Sequence.position)
    )
    sequences = list(result.scalars().all())
    if not sequences:
        log.warning("reserve_slots_for_new_lead: no sequences for campaign %s — no slots created", campaign_id)
        return

    log.info("reserve_slots_for_new_lead: found %d inboxes, %d sequences", len(inboxes), len(sequences))
    await reserve_slots_for_lead(
        session, campaign_lead_id, campaign, inboxes, sequences,
        lead_id=cl.lead_id,
        start_date=start_date or date.today(),
        last_sent_sequence_index=-1,
    )


async def recalculate_queue_after_sequence_change(session: AsyncSession, campaign_id: int) -> None:
    """After editing/adding/deleting sequences: delete pending queue slots, recalculate from last sent per lead."""
    log.info("recalculate_queue: campaign=%s", campaign_id)

    result = await session.execute(
        select(Campaign).where(Campaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        log.error("recalculate_queue: campaign %s not found", campaign_id)
        return

    result = await session.execute(
        select(CampaignInbox, Inbox)
        .join(Inbox, CampaignInbox.inbox_id == Inbox.id)
        .where(CampaignInbox.campaign_id == campaign_id)
        .order_by(CampaignInbox.position, CampaignInbox.inbox_id)
    )
    rows = result.all()
    inboxes = [(row[1].id, row[1].max_emails_per_day, row[1].wait_minutes_between) for row in rows]
    if not inboxes:
        log.warning("recalculate_queue: no inboxes for campaign %s", campaign_id)
        return

    result = await session.execute(
        select(Sequence).where(Sequence.campaign_id == campaign_id).order_by(Sequence.position)
    )
    sequences = list(result.scalars().all())

    result = await session.execute(
        select(CampaignLead).where(CampaignLead.campaign_id == campaign_id)
    )
    campaign_leads = result.scalars().all()
    log.info("recalculate_queue: %d leads, %d sequences, %d inboxes", len(campaign_leads), len(sequences), len(inboxes))

    for cl in campaign_leads:
        sent_result = await session.execute(
            select(func.max(EmailLog.sequence_index)).where(
                EmailLog.lead_id == cl.lead_id,
                EmailLog.campaign_id == campaign_id,
            )
        )
        last_sent = sent_result.scalar()
        if last_sent is None:
            last_sent = -1

        # Delete all pending queue slots for this lead
        del_result = await session.execute(
            delete(QueueSlot).where(
                QueueSlot.campaign_lead_id == cl.id,
                QueueSlot.sequence_index > last_sent,
            )
        )
        await session.flush()
        log.info("  lead %s: last_sent=%d, deleted %d old slots", cl.lead_id, last_sent, del_result.rowcount)

        if not sequences:
            continue

        start_date = date.today()
        if last_sent >= 0:
            sent_date_result = await session.execute(
                select(EmailLog.sent_at).where(
                    EmailLog.lead_id == cl.lead_id,
                    EmailLog.campaign_id == campaign_id,
                    EmailLog.sequence_index == last_sent,
                ).order_by(EmailLog.sent_at.desc()).limit(1)
            )
            row = sent_date_result.scalar_one_or_none()
            if row is not None:
                start_date = row.date()  # Pass the actual sent date so wait_days are computed correctly

        await reserve_slots_for_lead(
            session, cl.id, campaign, inboxes, sequences,
            lead_id=cl.lead_id,
            start_date=start_date,
            last_sent_sequence_index=last_sent,
        )

    log.info("recalculate_queue: done for campaign %s", campaign_id)
