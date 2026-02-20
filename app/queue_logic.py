"""Queue slot reservation and recalculation logic."""
import logging
from datetime import datetime, date, time, timedelta
from app import time as time_provider
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
    cache: Optional[dict] = None,
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

    # Use cache if available (pre-seeded with scheduled minutes per inbox+day)
    time_key = ("times", inbox_id, day)
    if cache is not None and time_key in cache:
        existing_minutes = cache[time_key].copy()
    elif cache is not None and ("_preseeded",) in cache:
        existing_minutes = set()
    else:
        day_start = datetime.combine(day, time(0, 0))
        day_end = datetime.combine(day + timedelta(days=1), time(0, 0))
        result = await session.execute(
            select(QueueSlot.scheduled_date).where(
                QueueSlot.inbox_id == inbox_id,
                QueueSlot.scheduled_date >= day_start,
                QueueSlot.scheduled_date < day_end,
            )
        )
        existing_minutes = set()
        for (dt,) in result.all():
            existing_minutes.add((dt.hour * 60 + dt.minute))
        if cache is not None:
            cache[time_key] = existing_minutes.copy()

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


async def count_slots_on_date(
    session: AsyncSession, 
    inbox_id: int, 
    day: date,
    cache: Optional[dict] = None,
) -> int:
    """Count queue slots for this inbox on the given date. 
    Uses cache if provided to avoid redundant queries.
    """
    if cache is not None:
        key = (inbox_id, day)
        if key in cache:
            return cache[key]
        # If cache is pre-seeded, any missing key means 0 slots on this date
        if ("_preseeded",) in cache:
            return 0
    
    day_start = datetime.combine(day, datetime.min.time())
    day_end = datetime.combine(day + timedelta(days=1), datetime.min.time())
    q = (
        select(func.count(QueueSlot.id))
        .where(
            QueueSlot.inbox_id == inbox_id,
            QueueSlot.scheduled_date >= day_start,
            QueueSlot.scheduled_date < day_end,
        )
    )
    result = await session.execute(q)
    count = result.scalar() or 0
    
    if cache is not None:
        cache[key] = count
    
    return count





async def _get_preferred_inbox_for_lead(
    session: AsyncSession,
    lead_id: int,
    campaign_id: int,
    available_inbox_ids: List[int],
    campaign_lead_id: Optional[int] = None,
) -> Optional[int]:
    """
    Check if this lead was already contacted with an inbox in this campaign.
    Checks both sent emails (EmailLog) and existing queue slots (QueueSlot).
    If yes and that inbox is still available, return it (inbox persistence).
    Otherwise return None to use round-robin.
    """
    # First check EmailLog for sent emails (highest priority)
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
        log.info("Inbox persistence (from EmailLog): lead %s -> inbox %s", lead_id, inbox_id)
        return inbox_id
    
    # If no sent emails, check existing QueueSlot records
    # Get campaign_lead_id if not provided
    if campaign_lead_id is None:
        cl_result = await session.execute(
            select(CampaignLead.id)
            .where(
                CampaignLead.lead_id == lead_id,
                CampaignLead.campaign_id == campaign_id,
            )
        )
        campaign_lead_id = cl_result.scalar_one_or_none()
    
    if campaign_lead_id:
        slot_result = await session.execute(
            select(QueueSlot.inbox_id)
            .where(QueueSlot.campaign_lead_id == campaign_lead_id)
            .order_by(QueueSlot.sequence_index.asc())
            .limit(1)
        )
        inbox_id = slot_result.scalar_one_or_none()
        if inbox_id and inbox_id in available_inbox_ids:
            log.info("Inbox persistence (from QueueSlot): lead %s -> inbox %s", lead_id, inbox_id)
            return inbox_id
    
    return None


async def next_available_slot_position(
    session: AsyncSession, inbox_id: int, day: date, cache: Optional[dict] = None
) -> int:
    """Next position_in_day (1-based) for this inbox on this day."""
    count = await count_slots_on_date(session, inbox_id, day, cache)
    return count + 1


async def _check_all_sequences_fit(
    session: AsyncSession,
    start_date: date,
    sequences: List[Sequence],
    inboxes: List[Tuple[int, int, int]],  # (inbox_id, max_per_day, wait_minutes)
    sending_days: List[int],
    last_sent_sequence_index: int,
    cache: Optional[dict] = None,
) -> bool:
    """
    Lookahead check: if we schedule first sequence on start_date, 
    will ALL follow-ups fit within inbox capacity limits?
    Returns True if all sequences can be scheduled without exceeding any inbox's daily limit.
    Returns False if any sequence would overflow capacity.
    """
    to_schedule = [
        (i, seq)
        for i, seq in enumerate(sequences)
        if i > last_sent_sequence_index
    ]
    
    if not to_schedule:
        return True
    
    # Simulate where each sequence would land and account for simulated inserts
    simulated_loads = {}
    simulated_dates = []
    for idx, seq in to_schedule:
        is_follow_up = (idx > 0)
        
        if is_follow_up and simulated_dates:
            # Follow-up: calculate based on previous sequence's date
            prev_date = simulated_dates[-1]
            wait_days = seq.wait_days_after_previous
            current_date = next_business_date(prev_date, sending_days, wait_days)
        else:
            # First sequence: use the provided start_date
            current_date = start_date
        
        simulated_dates.append(current_date)
        
        # Check if ANY inbox has capacity on this date (including simulated loads)
        has_capacity = False
        for inbox_id, max_per_day, _ in inboxes:
            count = await count_slots_on_date(session, inbox_id, current_date, cache)
            simulated_key = (inbox_id, current_date)
            simulated_count = simulated_loads.get(simulated_key, 0)
            if (count + simulated_count) < max_per_day:
                has_capacity = True
                simulated_loads[simulated_key] = simulated_count + 1
                break
        
        if not has_capacity:
            # This date is at full capacity for all inboxes
            return False
    
    return True


async def _pick_inbox_with_capacity(
    session: AsyncSession,
    inboxes: List[Tuple[int, int]],  # (inbox_id, max_per_day)
    day: date,
    round_robin_index: int,
    cache: Optional[dict] = None,
) -> Optional[Tuple[int, int]]:
    """Return (inbox_id, max_per_day) for an inbox that has capacity on day, or None."""
    if not inboxes:
        return None
    for i in range(len(inboxes)):
        idx = (round_robin_index + i) % len(inboxes)
        inbox_id, max_per_day = inboxes[idx]
        count = await count_slots_on_date(session, inbox_id, day, cache)
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
    forced_inbox_id: Optional[int] = None,  # Force specific inbox (for recalculation)
    cache: Optional[dict] = None,  # Slot count cache for performance
) -> None:
    """
    Reserve queue slots for one campaign_lead.
    Each slot is assigned to an inbox that has capacity that day.
    Implements inbox persistence: once a lead is contacted with an inbox, always use that inbox.
    forced_inbox_id: If provided, skip inbox persistence check and use this inbox.
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

    # Check for inbox persistence
    available_inbox_ids = [inbox[0] for inbox in inboxes]
    preferred_inbox_id = forced_inbox_id  # Use forced inbox if provided
    
    if preferred_inbox_id is None:
        # Check if this lead was already contacted
        preferred_inbox_id = await _get_preferred_inbox_for_lead(
            session, lead_id, campaign.id, available_inbox_ids, campaign_lead_id
        )
    
    # Lock the lead to a single inbox for all scheduled sequence slots.
    # If we already have a persisted/forced inbox, enforce it immediately.
    locked_inbox_id = preferred_inbox_id
    if locked_inbox_id:
        inboxes = [inbox for inbox in inboxes if inbox[0] == locked_inbox_id]
        log.info("Using inbox persistence: lead %s locked to inbox %s", lead_id, locked_inbox_id)

    today = time_provider.today()
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
    now = time_provider.now()
    today = time_provider.today()

    # LOOKAHEAD OPTIMIZATION for new leads:
    # Find a start date (and inbox, if needed) where all follow-ups fit without exceeding capacity.
    if last_sent_sequence_index == -1 and len(to_schedule) > 1:
        lookahead_attempts = 0
        max_lookahead_attempts = 30  # Try up to 30 days ahead
        original_start = current_date
        
        while lookahead_attempts < max_lookahead_attempts:
            fit_found = False
            if locked_inbox_id is not None:
                fit_found = await _check_all_sequences_fit(
                    session, current_date, sequences, inboxes,
                    sending_days, last_sent_sequence_index, cache
                )
            else:
                for candidate in inboxes:
                    if await _check_all_sequences_fit(
                        session, current_date, sequences, [candidate],
                        sending_days, last_sent_sequence_index, cache
                    ):
                        locked_inbox_id = candidate[0]
                        inboxes = [candidate]
                        fit_found = True
                        log.info(
                            "  -> Lookahead optimization: selected inbox %s for lead %s",
                            locked_inbox_id, lead_id
                        )
                        break

            if fit_found:
                if current_date != original_start:
                    log.info(
                        "  -> Lookahead optimization: moved start from %s to %s (all sequences will fit)",
                        original_start, current_date
                    )
                break
            
            # Try next business day
            current_date = next_business_date(current_date, sending_days, 1)
            lookahead_attempts += 1
        
        if lookahead_attempts >= max_lookahead_attempts:
            log.warning(
                "  -> Lookahead optimization: could not find ideal start date after %d attempts; proceeding anyway",
                max_lookahead_attempts
            )
            current_date = original_start  # Reset to original if we exhausted attempts

    for idx, seq in to_schedule:
        is_follow_up = (idx > 0)
        
        if is_follow_up:
            if scheduled_dates:
                prev_send_date = scheduled_dates[-1]
            elif last_sent_sequence_index >= 0 and start_date is not None:
                # Recalculation: reference the date the last email was actually sent
                prev_send_date = start_date
            else:
                prev_send_date = current_date
            wait_days = seq.wait_days_after_previous
            
            # Follow-ups MUST be scheduled exactly wait_days after the previous email
            # Do NOT use load balancing for follow-ups
            current_date = next_business_date(prev_send_date, sending_days, wait_days)
            # Never schedule in the past
            if current_date < today:
                current_date = next_business_date(today, sending_days, 0)
        else:
            # For the first sequence (idx == 0), start from current date (pack days to capacity)
            if current_date < today:
                current_date = next_business_date(today, sending_days, 0)

        safety = 0
        while safety < 365:
            safety += 1
            candidate_inboxes = inboxes
            if locked_inbox_id is not None:
                candidate_inboxes = [i for i in inboxes if i[0] == locked_inbox_id]

            picked = await _pick_inbox_with_capacity(
                session, 
                [(i[0], i[1]) for i in candidate_inboxes],  # Just (id, max_per_day) for capacity check
                current_date, 
                round_robin,
                cache
            )
            
            # Follow-ups must not exceed inbox daily capacity.
            # If target date is full, leave this and remaining follow-ups unscheduled.
            if picked is None and is_follow_up:
                # Inbox is full on the ideal follow-up date; advance one business day
                # and retry rather than abandoning the remaining sequences entirely.
                log.warning(
                    "  -> seq=%d: inbox full on follow-up date %s; advancing to next business day",
                    idx, current_date
                )
                current_date += timedelta(days=1)
                while current_date.weekday() not in sending_days:
                    current_date += timedelta(days=1)
                continue
            
            if picked is not None:
                inbox_id, max_per_day = picked
                if locked_inbox_id is None:
                    # First successful assignment defines the lead's inbox for all future slots.
                    locked_inbox_id = inbox_id
                    log.info("Locked lead %s to inbox %s", lead_id, locked_inbox_id)
                # Get wait_minutes for this specific inbox
                wait_min = next(i[2] for i in inboxes if i[0] == inbox_id)
                pos = await next_available_slot_position(session, inbox_id, current_date, cache)

                scheduled_dt: datetime
                if current_date == today:
                    # Today: check if estimated time is in the past
                    est_time = _estimated_send_time(sending_start, wait_min, pos)
                    if est_time <= now.time():
                        next_dt = await _next_available_send_time_today(
                            session, inbox_id, today,
                            sending_start, sending_end, wait_min, now,
                            cache,
                        )
                        if next_dt is not None:
                            scheduled_dt = next_dt
                            log.info(
                                "  -> using next slot today for seq=%d: %s (was past, now %s)",
                                idx, scheduled_dt.strftime("%H:%M"), now.strftime("%H:%M"),
                            )
                        else:
                            # No slot left today (window ended or every grid point is taken).
                            # Move to the next business day for ALL sequence types — stacking
                            # emails at end_time violates the inbox rate-limit and causes a pile-up.
                            log.info(
                                "  -> no slot left today for seq=%d (window ended/full); moving to next day",
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
                        # Time would overflow the sending window — the inbox is full for this day.
                        # Move to the next business day for ALL sequence types; forcing end_time
                        # causes a pile-up of multiple leads at the same timestamp.
                        log.info(
                            "  -> seq=%d pos=%d would send at %s (past %s); moving to next day",
                            idx, pos, est_t.strftime("%H:%M"), end_time.strftime("%H:%M"),
                        )
                        current_date += timedelta(days=1)
                        while current_date.weekday() not in sending_days:
                            current_date += timedelta(days=1)
                        continue
                    else:
                        scheduled_dt = datetime.combine(current_date, est_t)

                slot = QueueSlot(
                    campaign_lead_id=campaign_lead_id,
                    inbox_id=inbox_id,
                    sequence_index=idx,
                    scheduled_date=scheduled_dt,
                    position_in_day=pos,
                )
                session.add(slot)
                # Update caches immediately (count + scheduled times)
                if cache is not None:
                    cache_key = (inbox_id, current_date)
                    cache[cache_key] = cache.get(cache_key, pos - 1) + 1
                    time_key = ("times", inbox_id, current_date)
                    if time_key not in cache:
                        cache[time_key] = set()
                    cache[time_key].add(scheduled_dt.hour * 60 + scheduled_dt.minute)
                else:
                    # Without cache, must flush for subsequent queries to see this slot
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
            
            # Advance to next business day (for both first emails and follow-ups)
            current_date += timedelta(days=1)
            while current_date.weekday() not in sending_days:
                current_date += timedelta(days=1)

    log.info("reserve_slots_for_lead: done, created %d slots", len(scheduled_dates))


async def _fetch_campaign_scheduling_data(
    session: AsyncSession, campaign_id: int
) -> Optional[Tuple[Campaign, List[Tuple[int, int, int]], List[Sequence]]]:
    """
    Fetch campaign, inboxes, and sequences for scheduling.
    Returns (campaign, inboxes, sequences) or None if campaign not found.
    inboxes: [(inbox_id, max_emails_per_day, wait_minutes_between), ...]
    """
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        log.error("_fetch_campaign_scheduling_data: campaign %s not found", campaign_id)
        return None

    result = await session.execute(
        select(CampaignInbox, Inbox)
        .join(Inbox, CampaignInbox.inbox_id == Inbox.id)
        .where(CampaignInbox.campaign_id == campaign_id)
        .order_by(CampaignInbox.position, CampaignInbox.inbox_id)
    )
    rows = result.all()
    inboxes = [(row[1].id, row[1].max_emails_per_day, row[1].wait_minutes_between) for row in rows]

    result = await session.execute(
        select(Sequence).where(Sequence.campaign_id == campaign_id).order_by(Sequence.position)
    )
    sequences = list(result.scalars().all())

    return (campaign, inboxes, sequences)


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

    # Fetch campaign data
    data = await _fetch_campaign_scheduling_data(session, campaign_id)
    if not data:
        return
    campaign, inboxes, sequences = data

    if not inboxes:
        log.warning("reserve_slots_for_new_lead: no inboxes for campaign %s", campaign_id)
        return
    if not sequences:
        log.warning("reserve_slots_for_new_lead: no sequences for campaign %s — no slots created", campaign_id)
        return

    log.info("reserve_slots_for_new_lead: found %d inboxes, %d sequences", len(inboxes), len(sequences))
    
    # Create a cache for this single lead reservation
    slot_cache: dict = {}
    
    await reserve_slots_for_lead(
        session, campaign_lead_id, campaign, inboxes, sequences,
        lead_id=cl.lead_id,
        start_date=start_date or time_provider.today(),
        last_sent_sequence_index=-1,
        cache=slot_cache,
    )
    await session.flush()


async def reserve_slots_for_new_leads_bulk(
    session: AsyncSession,
    campaign_lead_ids: List[int],
    campaign_id: int,
    start_date: Optional[date] = None,
) -> None:
    """
    Efficiently schedule queue slots for a batch of newly enrolled leads.

    Unlike calling reserve_slots_for_new_lead in a loop (which re-fetches campaign
    data and resets the cache for every lead), this function:
      1. Fetches campaign/inbox/sequence data exactly once.
      2. Pre-seeds a shared slot cache from existing QueueSlot rows (one query).
      3. Iterates all new campaign_leads sharing that single cache.
      4. Flushes once at the very end.

    This gives the same DB efficiency as recalculate_queue_after_sequence_change
    but scoped only to the freshly-enrolled leads.
    """
    if not campaign_lead_ids:
        return

    log.info(
        "reserve_slots_for_new_leads_bulk: campaign=%s leads=%d",
        campaign_id, len(campaign_lead_ids),
    )

    # ── Step 1: fetch campaign / inboxes / sequences once ────────────────────
    data = await _fetch_campaign_scheduling_data(session, campaign_id)
    if not data:
        return
    campaign, inboxes, sequences = data

    if not inboxes:
        log.warning("reserve_slots_for_new_leads_bulk: no inboxes for campaign %s", campaign_id)
        return
    if not sequences:
        log.warning("reserve_slots_for_new_leads_bulk: no sequences for campaign %s — no slots created", campaign_id)
        return

    # ── Step 2: fetch CampaignLead rows for the given ids ────────────────────
    cl_result = await session.execute(
        select(CampaignLead).where(CampaignLead.id.in_(campaign_lead_ids))
    )
    campaign_leads = list(cl_result.scalars().all())

    if not campaign_leads:
        log.warning("reserve_slots_for_new_leads_bulk: no CampaignLead rows found for ids %s", campaign_lead_ids)
        return

    # ── Step 3: pre-seed cache from existing slots (single bulk query) ────────
    inbox_ids = [i[0] for i in inboxes]
    slot_cache: dict = {}

    if inbox_ids:
        preseed_result = await session.execute(
            select(QueueSlot.inbox_id, QueueSlot.scheduled_date)
            .where(
                QueueSlot.inbox_id.in_(inbox_ids),
                QueueSlot.scheduled_date >= datetime.combine(time_provider.today(), time(0, 0)),
            )
        )
        for row in preseed_result.all():
            dt = row.scheduled_date
            day = dt.date() if isinstance(dt, datetime) else dt
            # count cache
            count_key = (row.inbox_id, day)
            slot_cache[count_key] = slot_cache.get(count_key, 0) + 1
            # time cache
            time_key = ("times", row.inbox_id, day)
            if time_key not in slot_cache:
                slot_cache[time_key] = set()
            slot_cache[time_key].add(dt.hour * 60 + dt.minute)

    slot_cache[("_preseeded",)] = True  # signal: missing keys mean 0
    log.info(
        "reserve_slots_for_new_leads_bulk: pre-seeded cache with %d date entries",
        sum(1 for k in slot_cache if isinstance(k, tuple) and len(k) == 2 and isinstance(k[0], int)),
    )

    # ── Step 4: schedule each lead sharing the same cache ────────────────────
    base_start = start_date or time_provider.today()
    for cl in campaign_leads:
        await reserve_slots_for_lead(
            session, cl.id, campaign, inboxes, sequences,
            lead_id=cl.lead_id,
            start_date=base_start,
            last_sent_sequence_index=-1,  # brand-new leads: schedule everything
            cache=slot_cache,
        )

    # ── Step 5: single flush ──────────────────────────────────────────────────
    await session.flush()
    log.info(
        "reserve_slots_for_new_leads_bulk: done for campaign %s (%d leads)",
        campaign_id, len(campaign_leads),
    )


async def recalculate_queue_after_sequence_change(session: AsyncSession, campaign_id: int) -> None:
    """After editing/adding/deleting sequences: delete pending queue slots, recalculate from last sent per lead."""
    log.info("recalculate_queue: campaign=%s", campaign_id)

    # Fetch campaign data
    data = await _fetch_campaign_scheduling_data(session, campaign_id)
    if not data:
        return
    campaign, inboxes, sequences = data

    # Fetch all leads in the campaign
    result = await session.execute(
        select(CampaignLead).where(CampaignLead.campaign_id == campaign_id)
    )
    campaign_leads = list(result.scalars().all())

    await _recalculate_queue_for_campaign_leads(
        session,
        campaign=campaign,
        inboxes=inboxes,
        sequences=sequences,
        campaign_leads=campaign_leads,
        log_prefix="recalculate_queue",
    )


async def _recalculate_queue_for_campaign_leads(
    session: AsyncSession,
    campaign: Campaign,
    inboxes: List[Tuple[int, int, int]],
    sequences: List[Sequence],
    campaign_leads: List[CampaignLead],
    *,
    log_prefix: str,
) -> None:
    """Recalculate queue for a specific set of campaign leads in one campaign."""
    campaign_id = campaign.id

    if not inboxes:
        log.warning("%s: no inboxes for campaign %s", log_prefix, campaign_id)
        return

    if not campaign_leads:
        log.info("%s: no leads in campaign %s", log_prefix, campaign_id)
        return

    # ============================================================================
    # BULK QUERY OPTIMIZATION: Fetch all lead metadata upfront (eliminates N+1)
    # ============================================================================

    lead_ids = [cl.lead_id for cl in campaign_leads]

    # Query 1: Get max sequence_index and corresponding sent_at per lead
    email_log_query = await session.execute(
        select(
            EmailLog.lead_id,
            func.max(EmailLog.sequence_index).label('max_seq'),
            func.max(EmailLog.sent_at).label('last_sent_at')
        )
        .where(
            EmailLog.lead_id.in_(lead_ids),
            EmailLog.campaign_id == campaign_id,
        )
        .group_by(EmailLog.lead_id)
    )
    # Build lookup dicts
    last_sent_by_lead = {}  # lead_id -> max_sequence_index
    last_sent_date_by_lead = {}  # lead_id -> sent_at.date()
    for row in email_log_query.all():
        last_sent_by_lead[row.lead_id] = row.max_seq if row.max_seq is not None else -1
        if row.last_sent_at:
            last_sent_date_by_lead[row.lead_id] = row.last_sent_at.date()

    # Query 2: Get preferred inbox from EmailLog (most recent sent email per lead)
    preferred_inbox_query = await session.execute(
        select(EmailLog.lead_id, EmailLog.inbox_id)
        .where(
            EmailLog.lead_id.in_(lead_ids),
            EmailLog.campaign_id == campaign_id,
            EmailLog.inbox_id.isnot(None),
        )
        .distinct(EmailLog.lead_id)
        .order_by(EmailLog.lead_id, EmailLog.sent_at.desc())
    )
    preferred_inbox_from_log = {row.lead_id: row.inbox_id for row in preferred_inbox_query.all()}

    # Query 3: Get preferred inbox from QueueSlot (for leads without sent emails)
    cl_ids = [cl.id for cl in campaign_leads]
    preferred_inbox_from_slot_query = await session.execute(
        select(QueueSlot.campaign_lead_id, QueueSlot.inbox_id)
        .where(QueueSlot.campaign_lead_id.in_(cl_ids))
        .distinct(QueueSlot.campaign_lead_id)
        .order_by(QueueSlot.campaign_lead_id, QueueSlot.sequence_index.asc())
    )
    preferred_inbox_from_slot = {row.campaign_lead_id: row.inbox_id for row in preferred_inbox_from_slot_query.all()}

    # ============================================================================
    # END BULK QUERIES
    # ============================================================================

    # Sort leads: those with sent emails first (to prioritize follow-ups), then new leads
    # This ensures follow-ups get their target dates before new leads fill capacity
    partially_sent_leads = [cl for cl in campaign_leads if cl.lead_id in last_sent_by_lead]
    new_leads = [cl for cl in campaign_leads if cl.lead_id not in last_sent_by_lead]

    # Process in priority order: partially-sent first, then new
    ordered_leads = partially_sent_leads + new_leads
    log.info(
        "%s: %d leads (%d partially-sent, %d new), %d sequences, %d inboxes",
        log_prefix,
        len(ordered_leads), len(partially_sent_leads), len(new_leads), len(sequences), len(inboxes)
    )

    available_inbox_ids = [inbox[0] for inbox in inboxes]
    inbox_ids = [i[0] for i in inboxes]

    # ========================================================================
    # PHASE 1: BULK DELETE old queue slots (single flush instead of N flushes)
    # ========================================================================
    total_deleted = 0

    # New leads: delete ALL their queue slots in one query
    new_lead_cl_ids = [cl.id for cl in new_leads]
    if new_lead_cl_ids:
        del_result = await session.execute(
            delete(QueueSlot).where(QueueSlot.campaign_lead_id.in_(new_lead_cl_ids))
        )
        total_deleted += del_result.rowcount

    # Partially-sent leads: group by last_sent value for batch deletes
    by_last_sent: dict = {}
    for cl in partially_sent_leads:
        ls = last_sent_by_lead[cl.lead_id]
        by_last_sent.setdefault(ls, []).append(cl.id)

    for last_sent_val, cl_ids_batch in by_last_sent.items():
        del_result = await session.execute(
            delete(QueueSlot).where(
                QueueSlot.campaign_lead_id.in_(cl_ids_batch),
                QueueSlot.sequence_index > last_sent_val,
            )
        )
        total_deleted += del_result.rowcount

    await session.flush()  # Single flush for all deletes
    log.info("%s: bulk-deleted %d old slots across %d leads", log_prefix, total_deleted, len(ordered_leads))

    # ========================================================================
    # PHASE 2: PRE-SEED CACHES (count + time from one query, zero DB hits during reservation)
    # ========================================================================
    slot_cache: dict = {}

    if inbox_ids:
        preseed_result = await session.execute(
            select(QueueSlot.inbox_id, QueueSlot.scheduled_date)
            .where(
                QueueSlot.inbox_id.in_(inbox_ids),
                QueueSlot.scheduled_date >= datetime.combine(time_provider.today(), time(0, 0)),
            )
        )
        for row in preseed_result.all():
            dt = row.scheduled_date
            day = dt.date() if isinstance(dt, datetime) else dt
            # Count cache
            count_key = (row.inbox_id, day)
            slot_cache[count_key] = slot_cache.get(count_key, 0) + 1
            # Time cache
            time_key = ("times", row.inbox_id, day)
            if time_key not in slot_cache:
                slot_cache[time_key] = set()
            slot_cache[time_key].add(dt.hour * 60 + dt.minute)

        # Also count today's emails already sent (their QueueSlots were deleted after sending).
        # Without this, the capacity check sees only remaining unsent slots and allows
        # far too many new slots to be added today.
        _today = time_provider.today()
        today_sent_result = await session.execute(
            select(EmailLog.inbox_id, func.count(EmailLog.id).label("sent_count"))
            .where(
                EmailLog.inbox_id.in_(inbox_ids),
                EmailLog.sent_at >= datetime.combine(_today, time(0, 0)),
            )
            .group_by(EmailLog.inbox_id)
        )
        for row in today_sent_result.all():
            count_key = (row.inbox_id, _today)
            slot_cache[count_key] = slot_cache.get(count_key, 0) + row.sent_count

    slot_cache[("_preseeded",)] = True  # Signal that cache is complete
    log.info(
        "%s: pre-seeded cache with %d date entries",
        log_prefix,
        sum(1 for k in slot_cache if isinstance(k, tuple) and len(k) == 2 and isinstance(k[0], int)),
    )

    # ========================================================================
    # PHASE 3: RESERVE new slots for all leads (cache-only, zero DB queries)
    # ========================================================================
    if not sequences:
        log.info("%s: no sequences, nothing to reserve", log_prefix)
        log.info("%s: done for campaign %s", log_prefix, campaign_id)
        return

    for cl in ordered_leads:
        last_sent = last_sent_by_lead.get(cl.lead_id, -1)

        # Determine preferred inbox from pre-fetched data
        preferred_inbox = None
        if cl.lead_id in preferred_inbox_from_log:
            ibx = preferred_inbox_from_log[cl.lead_id]
            if ibx in available_inbox_ids:
                preferred_inbox = ibx
        elif cl.id in preferred_inbox_from_slot:
            ibx = preferred_inbox_from_slot[cl.id]
            if ibx in available_inbox_ids:
                preferred_inbox = ibx

        start_date = last_sent_date_by_lead.get(cl.lead_id, time_provider.today())

        await reserve_slots_for_lead(
            session, cl.id, campaign, inboxes, sequences,
            lead_id=cl.lead_id,
            start_date=start_date,
            last_sent_sequence_index=last_sent,
            forced_inbox_id=preferred_inbox,
            cache=slot_cache,
        )

    await session.flush()  # Single flush for all new slots
    log.info("%s: done for campaign %s", log_prefix, campaign_id)


async def recalculate_queue_after_sequence_change_for_leads(
    session: AsyncSession,
    campaign_lead_ids: List[int],
) -> None:
    """
    Recalculate queue for an explicit list of campaign leads.

    Leads are processed in caller order.
    Adjacent leads with the same campaign_id are grouped together.
    This may result in multiple groups per campaign.
    """
    if not campaign_lead_ids:
        return

    log.info("recalculate_queue_for_leads: leads=%d", len(campaign_lead_ids))

    # Dedupe while preserving caller order
    unique_ids = list(dict.fromkeys(campaign_lead_ids))

    cl_result = await session.execute(
        select(CampaignLead).where(CampaignLead.id.in_(unique_ids))
    )
    cl_by_id = {cl.id: cl for cl in cl_result.scalars().all()}

    ordered_campaign_leads = [
        cl_by_id[cl_id]
        for cl_id in unique_ids
        if cl_id in cl_by_id
    ]

    if not ordered_campaign_leads:
        log.warning("recalculate_queue_for_leads: no CampaignLead rows found")
        return

    missing_ids = [cl_id for cl_id in unique_ids if cl_id not in cl_by_id]
    if missing_ids:
        log.warning(
            "recalculate_queue_for_leads: %d campaign_lead ids not found",
            len(missing_ids),
        )

    # Group ONLY adjacent leads with the same campaign_id
    groups: list[tuple[int, list[CampaignLead]]] = []

    for cl in ordered_campaign_leads:
        if not groups or groups[-1][0] != cl.campaign_id:
            groups.append((cl.campaign_id, [cl]))
        else:
            groups[-1][1].append(cl)

    # Process each consecutive group independently
    for campaign_id, campaign_leads in groups:
        data = await _fetch_campaign_scheduling_data(session, campaign_id)
        if not data:
            continue

        campaign, inboxes, sequences = data
        await _recalculate_queue_for_campaign_leads(
            session,
            campaign=campaign,
            inboxes=inboxes,
            sequences=sequences,
            campaign_leads=campaign_leads,
            log_prefix="recalculate_queue_for_leads",
        )


async def recalculate_queue_round_robin(
    session: AsyncSession,
    campaign_lead_ids: List[int],
    batch_size: Optional[int] = None,
) -> None:
    """Round-robin recalculation: distribute inbox capacity evenly across campaigns.

    Unlike the priority-based approach (which processes one campaign at a time
    and rebuilds a fresh cache for each), this function:

    1. Fetches all campaign data and builds ONE shared slot-count cache covering
       every inbox across every campaign.
    2. Calculates ``batch_size = total_daily_inbox_capacity // num_active_campaigns``
       (or uses the caller-supplied value) to determine how many leads from each
       campaign are scheduled before rotating to the next campaign.
    3. Interleaves lead scheduling in ``batch_size`` chunks per campaign, cycling
       through all campaigns (round-robin) until every lead is processed.  The
       shared cache persists across campaign switches so inbox load is visible to
       every reservation call — no cache invalidation occurs on rotation.
    4. Performs a single ``session.flush()`` at the very end.

    This ensures inbox capacity is spread evenly across campaigns and avoids the
    performance penalty of rebuilding the cache when switching campaigns.
    """
    if not campaign_lead_ids:
        return

    log.info("recalculate_queue_round_robin: %d campaign_lead ids", len(campaign_lead_ids))

    # Dedupe while preserving order
    unique_ids = list(dict.fromkeys(campaign_lead_ids))

    cl_result = await session.execute(
        select(CampaignLead).where(CampaignLead.id.in_(unique_ids))
    )
    cl_by_id = {cl.id: cl for cl in cl_result.scalars().all()}
    ordered_cls = [cl_by_id[cl_id] for cl_id in unique_ids if cl_id in cl_by_id]

    if not ordered_cls:
        log.warning("recalculate_queue_round_robin: no CampaignLead rows found")
        return

    # Collect unique campaign IDs (preserving first-seen order from the caller)
    campaign_ids_ordered: List[int] = []
    for cl in ordered_cls:
        if cl.campaign_id not in campaign_ids_ordered:
            campaign_ids_ordered.append(cl.campaign_id)

    # Fetch scheduling data for all campaigns
    campaign_data: dict = {}  # campaign_id -> (campaign, inboxes, sequences)
    for cid in campaign_ids_ordered:
        data = await _fetch_campaign_scheduling_data(session, cid)
        if data:
            campaign_data[cid] = data

    active_campaign_ids = [cid for cid in campaign_ids_ordered if cid in campaign_data]
    if not active_campaign_ids:
        log.warning("recalculate_queue_round_robin: no valid campaign data found")
        return

    num_campaigns = len(active_campaign_ids)

    # Collect all unique inbox IDs across all active campaigns and total capacity
    all_inbox_ids: List[int] = []
    seen_inbox_ids: set = set()
    total_daily_capacity = 0
    for cid in active_campaign_ids:
        _, inboxes, _ = campaign_data[cid]
        for inbox_id, max_per_day, _ in inboxes:
            if inbox_id not in seen_inbox_ids:
                seen_inbox_ids.add(inbox_id)
                all_inbox_ids.append(inbox_id)
                total_daily_capacity += max_per_day

    # Batch size: how many leads per campaign per rotation cycle
    if batch_size is None:
        batch_size = max(1, total_daily_capacity // num_campaigns)

    log.info(
        "recalculate_queue_round_robin: %d campaigns, %d unique inboxes, "
        "total_capacity=%d/day, batch_size=%d/campaign",
        num_campaigns, len(all_inbox_ids), total_daily_capacity, batch_size,
    )

    # Group leads by campaign (preserving per-campaign order)
    leads_by_campaign: dict = {cid: [] for cid in active_campaign_ids}
    for cl in ordered_cls:
        if cl.campaign_id in leads_by_campaign:
            leads_by_campaign[cl.campaign_id].append(cl)

    # ===========================================================================
    # PHASE 1: Pre-fetch email metadata for all campaigns' leads (bulk queries)
    # ===========================================================================
    last_sent_by_cl: dict = {}       # cl.id -> last_sent_sequence_index (int)
    last_sent_date_by_cl: dict = {}  # cl.id -> date of last sent email
    preferred_inbox_by_cl: dict = {} # cl.id -> preferred inbox_id

    for cid in active_campaign_ids:
        _, inboxes, _ = campaign_data[cid]
        cls_for_campaign = leads_by_campaign[cid]
        if not cls_for_campaign:
            continue

        lead_ids_c = [cl.lead_id for cl in cls_for_campaign]
        cl_ids_c = [cl.id for cl in cls_for_campaign]
        available_inbox_ids = [i[0] for i in inboxes]

        # Max sequence_index + last sent_at per lead
        email_log_rows = await session.execute(
            select(
                EmailLog.lead_id,
                func.max(EmailLog.sequence_index).label("max_seq"),
                func.max(EmailLog.sent_at).label("last_sent_at"),
            )
            .where(EmailLog.lead_id.in_(lead_ids_c), EmailLog.campaign_id == cid)
            .group_by(EmailLog.lead_id)
        )
        lead_last_sent: dict = {}
        lead_last_date: dict = {}
        for row in email_log_rows.all():
            lead_last_sent[row.lead_id] = row.max_seq if row.max_seq is not None else -1
            if row.last_sent_at:
                lead_last_date[row.lead_id] = row.last_sent_at.date()
        for cl in cls_for_campaign:
            last_sent_by_cl[cl.id] = lead_last_sent.get(cl.lead_id, -1)
            if cl.lead_id in lead_last_date:
                last_sent_date_by_cl[cl.id] = lead_last_date[cl.lead_id]

        # Preferred inbox from EmailLog (most recent sent email per lead)
        pref_log_rows = await session.execute(
            select(EmailLog.lead_id, EmailLog.inbox_id)
            .where(
                EmailLog.lead_id.in_(lead_ids_c),
                EmailLog.campaign_id == cid,
                EmailLog.inbox_id.isnot(None),
            )
            .distinct(EmailLog.lead_id)
            .order_by(EmailLog.lead_id, EmailLog.sent_at.desc())
        )
        lead_pref_inbox = {r.lead_id: r.inbox_id for r in pref_log_rows.all()}

        # Preferred inbox from QueueSlot (fallback for uncontacted leads)
        pref_slot_rows = await session.execute(
            select(QueueSlot.campaign_lead_id, QueueSlot.inbox_id)
            .where(QueueSlot.campaign_lead_id.in_(cl_ids_c))
            .distinct(QueueSlot.campaign_lead_id)
            .order_by(QueueSlot.campaign_lead_id, QueueSlot.sequence_index.asc())
        )
        cl_pref_inbox = {r.campaign_lead_id: r.inbox_id for r in pref_slot_rows.all()}

        for cl in cls_for_campaign:
            pref = None
            if cl.lead_id in lead_pref_inbox:
                ibx = lead_pref_inbox[cl.lead_id]
                if ibx in available_inbox_ids:
                    pref = ibx
            elif cl.id in cl_pref_inbox:
                ibx = cl_pref_inbox[cl.id]
                if ibx in available_inbox_ids:
                    pref = ibx
            if pref is not None:
                preferred_inbox_by_cl[cl.id] = pref

    # ===========================================================================
    # PHASE 2: Bulk delete old queue slots for ALL campaigns
    # ===========================================================================
    total_deleted = 0
    for cid in active_campaign_ids:
        cls_for_campaign = leads_by_campaign[cid]
        if not cls_for_campaign:
            continue

        brand_new = [cl for cl in cls_for_campaign if last_sent_by_cl.get(cl.id, -1) < 0]
        partially_sent = [cl for cl in cls_for_campaign if last_sent_by_cl.get(cl.id, -1) >= 0]

        if brand_new:
            del_r = await session.execute(
                delete(QueueSlot).where(QueueSlot.campaign_lead_id.in_([cl.id for cl in brand_new]))
            )
            total_deleted += del_r.rowcount

        by_last_sent: dict = {}
        for cl in partially_sent:
            ls = last_sent_by_cl[cl.id]
            by_last_sent.setdefault(ls, []).append(cl.id)
        for last_sent_val, cl_ids_batch in by_last_sent.items():
            del_r = await session.execute(
                delete(QueueSlot).where(
                    QueueSlot.campaign_lead_id.in_(cl_ids_batch),
                    QueueSlot.sequence_index > last_sent_val,
                )
            )
            total_deleted += del_r.rowcount

    await session.flush()
    log.info("recalculate_queue_round_robin: bulk-deleted %d old slots", total_deleted)

    # ===========================================================================
    # PHASE 3: Build ONE shared cache covering ALL inboxes from ALL campaigns
    # ===========================================================================
    shared_cache: dict = {}

    if all_inbox_ids:
        preseed_result = await session.execute(
            select(QueueSlot.inbox_id, QueueSlot.scheduled_date)
            .where(
                QueueSlot.inbox_id.in_(all_inbox_ids),
                QueueSlot.scheduled_date >= datetime.combine(time_provider.today(), time(0, 0)),
            )
        )
        for row in preseed_result.all():
            dt = row.scheduled_date
            day = dt.date() if isinstance(dt, datetime) else dt
            count_key = (row.inbox_id, day)
            shared_cache[count_key] = shared_cache.get(count_key, 0) + 1
            time_key = ("times", row.inbox_id, day)
            if time_key not in shared_cache:
                shared_cache[time_key] = set()
            shared_cache[time_key].add(dt.hour * 60 + dt.minute)

        # Also account for today's already-sent emails (QueueSlots deleted post-send)
        _today = time_provider.today()
        today_sent = await session.execute(
            select(EmailLog.inbox_id, func.count(EmailLog.id).label("sent_count"))
            .where(
                EmailLog.inbox_id.in_(all_inbox_ids),
                EmailLog.sent_at >= datetime.combine(_today, time(0, 0)),
            )
            .group_by(EmailLog.inbox_id)
        )
        for row in today_sent.all():
            count_key = (row.inbox_id, _today)
            shared_cache[count_key] = shared_cache.get(count_key, 0) + row.sent_count

    shared_cache[("_preseeded",)] = True  # signal: missing keys → 0 slots
    log.info(
        "recalculate_queue_round_robin: shared cache pre-seeded — %d date entries, %d inboxes",
        sum(1 for k in shared_cache if isinstance(k, tuple) and len(k) == 2 and isinstance(k[0], int)),
        len(all_inbox_ids),
    )

    # ===========================================================================
    # PHASE 4: Interleaved reservation — batch_size leads per campaign per cycle
    # ===========================================================================
    campaign_queues: dict = {cid: list(leads_by_campaign[cid]) for cid in active_campaign_ids}

    total_reserved = 0
    any_remaining = True
    while any_remaining:
        any_remaining = False
        for cid in active_campaign_ids:
            queue = campaign_queues[cid]
            if not queue:
                continue

            campaign, inboxes, sequences = campaign_data[cid]
            batch = queue[:batch_size]
            campaign_queues[cid] = queue[batch_size:]

            if campaign_queues[cid]:
                any_remaining = True

            for cl in batch:
                last_sent = last_sent_by_cl.get(cl.id, -1)
                forced_inbox = preferred_inbox_by_cl.get(cl.id, None)
                start_date = last_sent_date_by_cl.get(cl.id, time_provider.today())

                await reserve_slots_for_lead(
                    session, cl.id, campaign, inboxes, sequences,
                    lead_id=cl.lead_id,
                    start_date=start_date,
                    last_sent_sequence_index=last_sent,
                    forced_inbox_id=forced_inbox,
                    cache=shared_cache,  # shared — survives campaign rotation
                )
                total_reserved += 1

    await session.flush()
    log.info(
        "recalculate_queue_round_robin: done — reserved slots for %d leads across %d campaigns "
        "(batch_size=%d, total_capacity=%d/day)",
        total_reserved, num_campaigns, batch_size, total_daily_capacity,
    )


# Backwards-compatible public API ------------------------------------------------
async def recalculate_queue(session: AsyncSession, campaign_id: int) -> None:
    """Compatibility wrapper kept for older callers (e.g. scripts).

    Forwards to `recalculate_queue_after_sequence_change` which contains the
    current implementation.
    """
    await recalculate_queue_after_sequence_change(session, campaign_id)
