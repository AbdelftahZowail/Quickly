"""Comprehensive tests for app.queue_logic.

Covers:
  - Pure helper functions (_parse_time, _estimated_send_time, _time_to_minutes, next_business_date)
  - DB-level functions (count_slots_on_date, next_available_slot_position,
    _next_available_send_time_today, _get_preferred_inbox_for_lead, _pick_inbox_with_capacity)
  - Integration scenarios (reserve_slots_for_lead, reserve_slots_for_new_lead,
    recalculate_queue_after_sequence_change) with realistic multi-lead / multi-campaign data
"""
import pytest
from datetime import datetime, date, time, timedelta
from sqlalchemy import select, func

from app.models import QueueSlot, EmailLog
from app.queue_logic import (
    _parse_time,
    _estimated_send_time,
    _time_to_minutes,
    next_business_date,
    count_slots_on_date,
    next_available_slot_position,
    _next_available_send_time_today,
    _get_preferred_inbox_for_lead,
    _pick_inbox_with_capacity,
    reserve_slots_for_lead,
    reserve_slots_for_new_lead,
    recalculate_queue_after_sequence_change,
)
from tests.conftest import (
    make_inbox,
    make_campaign,
    make_sequence,
    make_lead,
    make_campaign_lead,
    make_campaign_inbox,
    make_email_log,
    make_queue_slot,
)


# ============================================================================
# 1. PURE FUNCTION TESTS (no database)
# ============================================================================


class TestParseTime:
    """Tests for _parse_time helper."""

    def test_valid_time(self):
        assert _parse_time("09:00") == time(9, 0)

    def test_valid_time_afternoon(self):
        assert _parse_time("17:30") == time(17, 30)

    def test_midnight(self):
        assert _parse_time("00:00") == time(0, 0)

    def test_boundary_2359(self):
        assert _parse_time("23:59") == time(23, 59)

    def test_24_00_maps_to_2359(self):
        """24:00 is treated as end-of-day 23:59."""
        assert _parse_time("24:00") == time(23, 59)

    def test_invalid_hour_returns_default(self):
        assert _parse_time("25:00") == time(9, 0)

    def test_invalid_minute_returns_default(self):
        assert _parse_time("10:61") == time(9, 0)

    def test_no_colon_returns_default(self):
        assert _parse_time("0900") == time(9, 0)

    def test_extra_colons_returns_default(self):
        assert _parse_time("09:00:00") == time(9, 0)

    def test_non_numeric_returns_default(self):
        assert _parse_time("abc") == time(9, 0)

    def test_empty_string_returns_default(self):
        assert _parse_time("") == time(9, 0)

    def test_whitespace_trimmed(self):
        assert _parse_time("  14:15  ") == time(14, 15)


class TestEstimatedSendTime:
    """Tests for _estimated_send_time."""

    def test_first_position(self):
        """Position 1 should return the start time itself (0 offset)."""
        assert _estimated_send_time("09:00", 5, 1) == time(9, 0)

    def test_second_position(self):
        assert _estimated_send_time("09:00", 5, 2) == time(9, 5)

    def test_tenth_position(self):
        assert _estimated_send_time("09:00", 5, 10) == time(9, 45)

    def test_large_offset_capped_at_2359(self):
        """If computed hour >= 24, cap to 23:59."""
        assert _estimated_send_time("23:00", 30, 10) == time(23, 59)

    def test_different_start_and_wait(self):
        assert _estimated_send_time("10:30", 15, 3) == time(11, 0)


class TestTimeToMinutes:
    """Tests for _time_to_minutes."""

    def test_midnight(self):
        assert _time_to_minutes(time(0, 0)) == 0

    def test_noon(self):
        assert _time_to_minutes(time(12, 0)) == 720

    def test_end_of_day(self):
        assert _time_to_minutes(time(23, 59)) == 1439

    def test_arbitrary(self):
        assert _time_to_minutes(time(9, 30)) == 570


class TestNextBusinessDate:
    """Tests for next_business_date."""

    def test_zero_delta_on_business_day(self):
        """Monday with Mon-Fri stays on Monday."""
        mon = date(2026, 2, 16)  # Monday
        assert mon.weekday() == 0
        assert next_business_date(mon, [0, 1, 2, 3, 4], 0) == mon

    def test_zero_delta_on_weekend_skips_forward(self):
        """Saturday with Mon-Fri should skip to Monday."""
        sat = date(2026, 2, 21)  # Saturday
        assert sat.weekday() == 5
        assert next_business_date(sat, [0, 1, 2, 3, 4], 0) == date(2026, 2, 23)

    def test_advance_one_business_day(self):
        mon = date(2026, 2, 16)
        assert next_business_date(mon, [0, 1, 2, 3, 4], 1) == date(2026, 2, 17)

    def test_advance_over_weekend(self):
        """Friday + 1 business day = Monday."""
        fri = date(2026, 2, 20)  # Friday
        assert fri.weekday() == 4
        result = next_business_date(fri, [0, 1, 2, 3, 4], 1)
        assert result == date(2026, 2, 23)  # Monday

    def test_advance_five_business_days(self):
        """Mon + 5 = next Monday."""
        mon = date(2026, 2, 16)
        result = next_business_date(mon, [0, 1, 2, 3, 4], 5)
        assert result == date(2026, 2, 23)

    def test_custom_sending_days_tue_thu(self):
        """Only Tue(1) and Thu(3)."""
        mon = date(2026, 2, 16)
        result = next_business_date(mon, [1, 3], 0)
        assert result == date(2026, 2, 17)  # Tue

    def test_custom_sending_days_advance(self):
        """Tue + 1 business day (only Tu/Th) = Thu."""
        tue = date(2026, 2, 17)
        result = next_business_date(tue, [1, 3], 1)
        assert result == date(2026, 2, 19)  # Thu

    def test_advance_many_days_custom(self):
        """Tue + 3 business days (only Tu/Th) = next Tue+1 week = following Thu."""
        tue = date(2026, 2, 17)
        # day1=Thu 19, day2=Tue 24, day3=Thu 26
        result = next_business_date(tue, [1, 3], 3)
        assert result == date(2026, 2, 26)


# ============================================================================
# 2. DATABASE-LEVEL FUNCTION TESTS
# ============================================================================


class TestCountSlotsOnDate:
    """Tests for count_slots_on_date."""

    async def test_empty_returns_zero(self, session):
        inbox = await make_inbox(session)
        count = await count_slots_on_date(session, inbox.id, date(2026, 3, 2))
        assert count == 0

    async def test_counts_correct_date(self, session):
        inbox = await make_inbox(session)
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # 2 slots on target day
        await make_queue_slot(session, cl.id, inbox.id, 0, datetime(2026, 3, 2, 9, 0))
        await make_queue_slot(session, cl.id, inbox.id, 1, datetime(2026, 3, 2, 9, 5))
        # 1 slot on a different day
        await make_queue_slot(session, cl.id, inbox.id, 2, datetime(2026, 3, 3, 9, 0))

        count = await count_slots_on_date(session, inbox.id, date(2026, 3, 2))
        assert count == 2

    async def test_different_inbox_not_counted(self, session):
        inbox_a = await make_inbox(session, email="a@test.com")
        inbox_b = await make_inbox(session, email="b@test.com")
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await make_queue_slot(session, cl.id, inbox_a.id, 0, datetime(2026, 3, 2, 9, 0))
        count = await count_slots_on_date(session, inbox_b.id, date(2026, 3, 2))
        assert count == 0


class TestNextAvailableSlotPosition:
    """Tests for next_available_slot_position."""

    async def test_first_slot_returns_one(self, session):
        inbox = await make_inbox(session)
        pos = await next_available_slot_position(session, inbox.id, date(2026, 3, 2))
        assert pos == 1

    async def test_after_existing_slots(self, session):
        inbox = await make_inbox(session)
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await make_queue_slot(session, cl.id, inbox.id, 0, datetime(2026, 3, 2, 9, 0))
        await make_queue_slot(session, cl.id, inbox.id, 1, datetime(2026, 3, 2, 9, 5))

        pos = await next_available_slot_position(session, inbox.id, date(2026, 3, 2))
        assert pos == 3


class TestNextAvailableSendTimeToday:
    """Tests for _next_available_send_time_today."""

    async def test_empty_day_returns_first_grid_slot(self, session):
        inbox = await make_inbox(session)
        # "now" at 08:00 on the target day — send window starts at 09:00
        now = datetime(2026, 3, 2, 8, 0)
        result = await _next_available_send_time_today(
            session, inbox.id, date(2026, 3, 2), "09:00", "17:00", 5, now,
        )
        assert result == datetime(2026, 3, 2, 9, 0)

    async def test_skips_past_now(self, session):
        inbox = await make_inbox(session)
        # now at 09:02 — the 09:00 slot is past; next grid is 09:05
        now = datetime(2026, 3, 2, 9, 2)
        result = await _next_available_send_time_today(
            session, inbox.id, date(2026, 3, 2), "09:00", "17:00", 5, now,
        )
        assert result == datetime(2026, 3, 2, 9, 5)

    async def test_skips_occupied_slot(self, session):
        inbox = await make_inbox(session)
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Occupy the 09:00 slot
        await make_queue_slot(session, cl.id, inbox.id, 0, datetime(2026, 3, 2, 9, 0))

        now = datetime(2026, 3, 2, 8, 0)
        result = await _next_available_send_time_today(
            session, inbox.id, date(2026, 3, 2), "09:00", "17:00", 5, now,
        )
        assert result == datetime(2026, 3, 2, 9, 5)

    async def test_returns_none_when_window_over(self, session):
        inbox = await make_inbox(session)
        # now is past the end window
        now = datetime(2026, 3, 2, 17, 5)
        result = await _next_available_send_time_today(
            session, inbox.id, date(2026, 3, 2), "09:00", "17:00", 5, now,
        )
        assert result is None

    async def test_different_day_ignores_now(self, session):
        """If 'now' is on a different calendar day, treat now_min as 0."""
        inbox = await make_inbox(session)
        now = datetime(2026, 3, 1, 22, 0)  # day before
        result = await _next_available_send_time_today(
            session, inbox.id, date(2026, 3, 2), "09:00", "17:00", 5, now,
        )
        assert result == datetime(2026, 3, 2, 9, 0)


class TestGetPreferredInboxForLead:
    """Tests for _get_preferred_inbox_for_lead (inbox persistence)."""

    async def test_no_history_returns_none(self, session):
        inbox = await make_inbox(session)
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        result = await _get_preferred_inbox_for_lead(
            session, lead.id, campaign.id, [inbox.id],
        )
        assert result is None

    async def test_returns_previously_used_inbox(self, session):
        inbox_a = await make_inbox(session, email="a@test.com")
        inbox_b = await make_inbox(session, email="b@test.com")
        campaign = await make_campaign(session)
        lead = await make_lead(session)

        await make_email_log(session, lead.id, campaign.id, inbox_id=inbox_a.id)

        result = await _get_preferred_inbox_for_lead(
            session, lead.id, campaign.id, [inbox_a.id, inbox_b.id],
        )
        assert result == inbox_a.id

    async def test_returns_none_if_previous_inbox_not_available(self, session):
        inbox_a = await make_inbox(session, email="a@test.com")
        inbox_b = await make_inbox(session, email="b@test.com")
        campaign = await make_campaign(session)
        lead = await make_lead(session)

        await make_email_log(session, lead.id, campaign.id, inbox_id=inbox_a.id)

        # Only inbox_b is available now (inbox_a removed from campaign)
        result = await _get_preferred_inbox_for_lead(
            session, lead.id, campaign.id, [inbox_b.id],
        )
        assert result is None

    async def test_uses_most_recent_log(self, session):
        inbox_a = await make_inbox(session, email="a@test.com")
        inbox_b = await make_inbox(session, email="b@test.com")
        campaign = await make_campaign(session)
        lead = await make_lead(session)

        await make_email_log(
            session, lead.id, campaign.id, inbox_id=inbox_a.id,
            sent_at=datetime(2026, 1, 1, 10, 0),
        )
        await make_email_log(
            session, lead.id, campaign.id, inbox_id=inbox_b.id,
            sent_at=datetime(2026, 1, 2, 10, 0),
        )

        result = await _get_preferred_inbox_for_lead(
            session, lead.id, campaign.id, [inbox_a.id, inbox_b.id],
        )
        assert result == inbox_b.id


class TestPickInboxWithCapacity:
    """Tests for _pick_inbox_with_capacity."""

    async def test_returns_first_inbox_round_robin_0(self, session):
        inbox_a = await make_inbox(session, email="a@test.com", max_emails_per_day=10)
        inbox_b = await make_inbox(session, email="b@test.com", max_emails_per_day=10)
        inboxes = [(inbox_a.id, 10), (inbox_b.id, 10)]
        result = await _pick_inbox_with_capacity(
            session, inboxes, date(2026, 3, 2), round_robin_index=0,
        )
        assert result == (inbox_a.id, 10)

    async def test_round_robin_picks_second(self, session):
        inbox_a = await make_inbox(session, email="a@test.com", max_emails_per_day=10)
        inbox_b = await make_inbox(session, email="b@test.com", max_emails_per_day=10)
        inboxes = [(inbox_a.id, 10), (inbox_b.id, 10)]
        result = await _pick_inbox_with_capacity(
            session, inboxes, date(2026, 3, 2), round_robin_index=1,
        )
        assert result == (inbox_b.id, 10)

    async def test_skips_full_inbox(self, session):
        inbox_a = await make_inbox(session, email="a@test.com", max_emails_per_day=1)
        inbox_b = await make_inbox(session, email="b@test.com", max_emails_per_day=10)
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Fill inbox_a to capacity (1 slot)
        await make_queue_slot(session, cl.id, inbox_a.id, 0, datetime(2026, 3, 2, 9, 0))

        inboxes = [(inbox_a.id, 1), (inbox_b.id, 10)]
        result = await _pick_inbox_with_capacity(
            session, inboxes, date(2026, 3, 2), round_robin_index=0,
        )
        assert result == (inbox_b.id, 10)

    async def test_returns_none_all_full(self, session):
        inbox = await make_inbox(session, max_emails_per_day=1)
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await make_queue_slot(session, cl.id, inbox.id, 0, datetime(2026, 3, 2, 9, 0))

        inboxes = [(inbox.id, 1)]
        result = await _pick_inbox_with_capacity(
            session, inboxes, date(2026, 3, 2), round_robin_index=0,
        )
        assert result is None

    async def test_empty_inboxes_returns_none(self, session):
        result = await _pick_inbox_with_capacity(
            session, [], date(2026, 3, 2), round_robin_index=0,
        )
        assert result is None


# ============================================================================
# 3. INTEGRATION TESTS — REALISTIC MULTI-LEAD / MULTI-CAMPAIGN SCENARIOS
# ============================================================================


class TestReserveSlotsForLead:
    """Tests for reserve_slots_for_lead with various realistic scenarios."""

    async def test_single_sequence_creates_one_slot(self, session):
        """A campaign with 1 sequence should create exactly 1 slot."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_hours_start="09:00", sending_hours_end="17:00")
        seq = await make_sequence(session, campaign.id, position=0)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=[seq],
            lead_id=lead.id,
            start_date=date(2026, 3, 2),  # Monday
        )

        result = await session.execute(
            select(func.count(QueueSlot.id)).where(QueueSlot.campaign_lead_id == cl.id)
        )
        assert result.scalar() == 1

    async def test_three_sequences_creates_three_slots(self, session):
        """3 sequences (with wait_days=0) should produce 3 slots."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        seqs = []
        for i in range(3):
            seqs.append(await make_sequence(session, campaign.id, position=i, wait_days_after_previous=0))
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=seqs,
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 3
        assert [s.sequence_index for s in slots] == [0, 1, 2]

    async def test_wait_days_respected(self, session):
        """Sequence with wait_days=2 should be scheduled 2 business days later."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        seq0 = await make_sequence(session, campaign.id, position=0, wait_days_after_previous=0)
        seq1 = await make_sequence(session, campaign.id, position=1, wait_days_after_previous=2)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        start = date(2026, 3, 2)  # Monday
        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=[seq0, seq1],
            lead_id=lead.id,
            start_date=start,
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 2

        date_0 = slots[0].scheduled_date.date()
        date_1 = slots[1].scheduled_date.date()
        # Seq 0 on Monday 3/2, Seq 1 should be 2 business days later = Wednesday 3/4
        assert date_0 == date(2026, 3, 2)
        assert date_1 == date(2026, 3, 4)

    async def test_skips_already_sent_sequences(self, session):
        """If last_sent_sequence_index=1, only sequences with index > 1 should be scheduled."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        seqs = []
        for i in range(4):
            seqs.append(await make_sequence(session, campaign.id, position=i))
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=seqs,
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
            last_sent_sequence_index=1,
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 2
        assert slots[0].sequence_index == 2
        assert slots[1].sequence_index == 3

    async def test_no_inboxes_creates_no_slots(self, session):
        campaign = await make_campaign(session)
        seq = await make_sequence(session, campaign.id, position=0)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[],
            sequences=[seq],
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
        )

        result = await session.execute(select(func.count(QueueSlot.id)))
        assert result.scalar() == 0

    async def test_no_sequences_creates_no_slots(self, session):
        inbox = await make_inbox(session)
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, 50, 5)],
            sequences=[],
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
        )

        result = await session.execute(select(func.count(QueueSlot.id)))
        assert result.scalar() == 0


class TestRoundRobinAndCapacity:
    """Tests verifying round-robin inbox assignment and capacity overflow."""

    async def test_round_robin_distributes_across_inboxes(self, session):
        """With 2 inboxes, slots should alternate between them."""
        inbox_a = await make_inbox(session, email="a@test.com", max_emails_per_day=50, wait_minutes_between=5)
        inbox_b = await make_inbox(session, email="b@test.com", max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        seqs = [
            await make_sequence(session, campaign.id, position=i)
            for i in range(4)
        ]
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[
                (inbox_a.id, inbox_a.max_emails_per_day, inbox_a.wait_minutes_between),
                (inbox_b.id, inbox_b.max_emails_per_day, inbox_b.wait_minutes_between),
            ],
            sequences=seqs,
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 4
        # First goes to inbox_a, second to inbox_b, etc.
        assert slots[0].inbox_id == inbox_a.id
        assert slots[1].inbox_id == inbox_b.id
        assert slots[2].inbox_id == inbox_a.id
        assert slots[3].inbox_id == inbox_b.id

    async def test_capacity_overflow_moves_to_next_day(self, session):
        """When inbox hits max_per_day, slots should spill to the next business day."""
        inbox = await make_inbox(session, max_emails_per_day=2, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])

        # Create 4 sequences — only 2 fit per day
        seqs = [
            await make_sequence(session, campaign.id, position=i)
            for i in range(4)
        ]
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, 2, 5)],
            sequences=seqs,
            lead_id=lead.id,
            start_date=date(2026, 3, 2),  # Monday
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 4

        # First 2 on Monday, next 2 on Tuesday
        assert slots[0].scheduled_date.date() == date(2026, 3, 2)
        assert slots[1].scheduled_date.date() == date(2026, 3, 2)
        assert slots[2].scheduled_date.date() == date(2026, 3, 3)
        assert slots[3].scheduled_date.date() == date(2026, 3, 3)

    async def test_sending_window_overflow_to_next_day(self, session):
        """When estimated send time exceeds sending_hours_end, slot moves to next day."""
        # Very tight window: 09:00–09:10 with 5-minute waits → only 3 slots fit (09:00, 09:05, 09:10)
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(
            session, sending_hours_start="09:00", sending_hours_end="09:10",
            sending_days=[0, 1, 2, 3, 4],
        )

        seqs = [
            await make_sequence(session, campaign.id, position=i)
            for i in range(5)
        ]
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=seqs,
            lead_id=lead.id,
            start_date=date(2026, 3, 2),  # Monday
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 5

        # Slots should overflow from one day to the next
        dates = [s.scheduled_date.date() for s in slots]
        assert dates[0] == date(2026, 3, 2)
        # later slots should be on subsequent days
        assert dates[-1] > date(2026, 3, 2)


class TestInboxPersistence:
    """Tests that once a lead gets contacted via inbox A, all future slots use inbox A."""

    async def test_lead_locks_to_previous_inbox(self, session):
        inbox_a = await make_inbox(session, email="a@test.com", max_emails_per_day=50, wait_minutes_between=5)
        inbox_b = await make_inbox(session, email="b@test.com", max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        seq0 = await make_sequence(session, campaign.id, position=0)
        seq1 = await make_sequence(session, campaign.id, position=1)
        seq2 = await make_sequence(session, campaign.id, position=2)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Simulate: lead was already emailed with inbox_b
        await make_email_log(session, lead.id, campaign.id, inbox_id=inbox_b.id, sequence_index=0)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[
                (inbox_a.id, inbox_a.max_emails_per_day, inbox_a.wait_minutes_between),
                (inbox_b.id, inbox_b.max_emails_per_day, inbox_b.wait_minutes_between),
            ],
            sequences=[seq0, seq1, seq2],
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
            last_sent_sequence_index=0,  # seq 0 already sent
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        # All remaining slots (seq 1, 2) should use inbox_b
        assert len(slots) == 2
        assert all(s.inbox_id == inbox_b.id for s in slots)


class TestMultipleLeadsMultipleCampaigns:
    """Realistic scenario: several leads enrolled in several campaigns with multiple inboxes."""

    async def test_ten_leads_two_campaigns_three_inboxes(self, session):
        """Create 10 leads, 2 campaigns, 3 inboxes and verify all slots are created correctly."""
        # Inboxes
        inbox_1 = await make_inbox(session, email="alpha@co.com", max_emails_per_day=10, wait_minutes_between=5)
        inbox_2 = await make_inbox(session, email="beta@co.com", max_emails_per_day=10, wait_minutes_between=5)
        inbox_3 = await make_inbox(session, email="gamma@co.com", max_emails_per_day=10, wait_minutes_between=5)

        # Campaign A: uses inbox 1 & 2, 3 sequences
        camp_a = await make_campaign(session, name="Campaign A", sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, camp_a.id, inbox_1.id, position=0)
        await make_campaign_inbox(session, camp_a.id, inbox_2.id, position=1)

        seq_a = []
        for i in range(3):
            seq_a.append(await make_sequence(
                session, camp_a.id, position=i,
                subject=f"CampA Seq{i}", body=f"Body A{i}",
                wait_days_after_previous=1 if i > 0 else 0,
            ))

        # Campaign B: uses inbox 2 & 3, 2 sequences
        camp_b = await make_campaign(session, name="Campaign B", sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, camp_b.id, inbox_2.id, position=0)
        await make_campaign_inbox(session, camp_b.id, inbox_3.id, position=1)

        seq_b = []
        for i in range(2):
            seq_b.append(await make_sequence(
                session, camp_b.id, position=i,
                subject=f"CampB Seq{i}", body=f"Body B{i}",
                wait_days_after_previous=2 if i > 0 else 0,
            ))

        # 10 leads — first 5 go to campaign A, next 5 go to campaign B
        leads = []
        for i in range(10):
            leads.append(await make_lead(session, email=f"lead{i}@example.com", name=f"Lead {i}"))

        # Enroll leads 0-4 in campaign A
        cls_a = []
        for i in range(5):
            cl = await make_campaign_lead(session, camp_a.id, leads[i].id)
            cls_a.append(cl)

        # Enroll leads 5-9 in campaign B
        cls_b = []
        for i in range(5, 10):
            cl = await make_campaign_lead(session, camp_b.id, leads[i].id)
            cls_b.append(cl)

        # Reserve slots for all campaign A leads
        start = date(2026, 3, 2)  # Monday
        for cl in cls_a:
            await reserve_slots_for_lead(
                session, cl.id, camp_a,
                inboxes=[
                    (inbox_1.id, inbox_1.max_emails_per_day, inbox_1.wait_minutes_between),
                    (inbox_2.id, inbox_2.max_emails_per_day, inbox_2.wait_minutes_between),
                ],
                sequences=seq_a,
                lead_id=cl.lead_id,
                start_date=start,
            )

        # Reserve slots for all campaign B leads
        for cl in cls_b:
            await reserve_slots_for_lead(
                session, cl.id, camp_b,
                inboxes=[
                    (inbox_2.id, inbox_2.max_emails_per_day, inbox_2.wait_minutes_between),
                    (inbox_3.id, inbox_3.max_emails_per_day, inbox_3.wait_minutes_between),
                ],
                sequences=seq_b,
                lead_id=cl.lead_id,
                start_date=start,
            )

        # --- Assertions ---

        # Campaign A: 5 leads × 3 sequences = 15 slots
        result = await session.execute(
            select(func.count(QueueSlot.id)).where(
                QueueSlot.campaign_lead_id.in_([cl.id for cl in cls_a])
            )
        )
        assert result.scalar() == 15

        # Campaign B: 5 leads × 2 sequences = 10 slots
        result = await session.execute(
            select(func.count(QueueSlot.id)).where(
                QueueSlot.campaign_lead_id.in_([cl.id for cl in cls_b])
            )
        )
        assert result.scalar() == 10

        # Verify campaign A round-robin: inboxes 1 and 2 both got assigned
        result = await session.execute(
            select(QueueSlot.inbox_id).where(
                QueueSlot.campaign_lead_id.in_([cl.id for cl in cls_a])
            )
        )
        inbox_ids_used_a = set(row[0] for row in result.all())
        assert inbox_1.id in inbox_ids_used_a
        assert inbox_2.id in inbox_ids_used_a

        # Verify campaign B round-robin: inboxes 2 and 3 both got assigned
        result = await session.execute(
            select(QueueSlot.inbox_id).where(
                QueueSlot.campaign_lead_id.in_([cl.id for cl in cls_b])
            )
        )
        inbox_ids_used_b = set(row[0] for row in result.all())
        assert inbox_2.id in inbox_ids_used_b
        assert inbox_3.id in inbox_ids_used_b

        # Verify wait_days: campaign A seq 1 should be >= 1 business day after seq 0
        for cl in cls_a:
            result = await session.execute(
                select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
            )
            slots = result.scalars().all()
            assert len(slots) == 3
            for i in range(1, len(slots)):
                assert slots[i].scheduled_date.date() >= slots[i - 1].scheduled_date.date()

        # Verify campaign B wait_days=2: seq 1 is at least 2 business days after seq 0
        for cl in cls_b:
            result = await session.execute(
                select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
            )
            slots = result.scalars().all()
            assert len(slots) == 2
            diff = (slots[1].scheduled_date.date() - slots[0].scheduled_date.date()).days
            assert diff >= 2  # At least 2 calendar days (could be more if weekend)

    async def test_shared_inbox_respects_capacity_across_campaigns(self, session):
        """
        inbox_2 is shared between campaigns A and B.
        Capacity filled by campaign A should reduce availability for campaign B.
        """
        inbox = await make_inbox(session, email="shared@co.com", max_emails_per_day=3, wait_minutes_between=5)

        camp_a = await make_campaign(session, name="Camp A")
        await make_campaign_inbox(session, camp_a.id, inbox.id)
        seq_a = [await make_sequence(session, camp_a.id, position=i) for i in range(3)]

        camp_b = await make_campaign(session, name="Camp B")
        await make_campaign_inbox(session, camp_b.id, inbox.id)
        seq_b = [await make_sequence(session, camp_b.id, position=i) for i in range(3)]

        lead_a = await make_lead(session, email="leada@test.com")
        cl_a = await make_campaign_lead(session, camp_a.id, lead_a.id)

        lead_b = await make_lead(session, email="leadb@test.com")
        cl_b = await make_campaign_lead(session, camp_b.id, lead_b.id)

        start = date(2026, 3, 2)

        # Reserve for campaign A first — fills 3 slots on day 1
        await reserve_slots_for_lead(
            session, cl_a.id, camp_a,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=seq_a,
            lead_id=lead_a.id,
            start_date=start,
        )

        # Now campaign B — inbox is full on day 1, should spill
        await reserve_slots_for_lead(
            session, cl_b.id, camp_b,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=seq_b,
            lead_id=lead_b.id,
            start_date=start,
        )

        # Campaign A slots: all 3 on the start date
        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl_a.id).order_by(QueueSlot.scheduled_date)
        )
        slots_a = result.scalars().all()
        assert len(slots_a) == 3
        assert all(s.scheduled_date.date() == start for s in slots_a)

        # Campaign B: day 1 is full, so at least some slots should be on later days
        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl_b.id).order_by(QueueSlot.scheduled_date)
        )
        slots_b = result.scalars().all()
        assert len(slots_b) == 3
        assert any(s.scheduled_date.date() > start for s in slots_b)

    async def test_twenty_leads_high_volume(self, session):
        """Stress test: 20 leads, 4 sequences, 2 inboxes with max_per_day=5."""
        inbox_1 = await make_inbox(session, email="i1@co.com", max_emails_per_day=5, wait_minutes_between=5)
        inbox_2 = await make_inbox(session, email="i2@co.com", max_emails_per_day=5, wait_minutes_between=5)

        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox_1.id, position=0)
        await make_campaign_inbox(session, campaign.id, inbox_2.id, position=1)

        seqs = [await make_sequence(session, campaign.id, position=i) for i in range(4)]

        leads = []
        cls = []
        for i in range(20):
            lead = await make_lead(session, email=f"l{i}@co.com", name=f"L{i}")
            leads.append(lead)
            cl = await make_campaign_lead(session, campaign.id, lead.id)
            cls.append(cl)

        start = date(2026, 3, 2)
        for cl in cls:
            await reserve_slots_for_lead(
                session, cl.id, campaign,
                inboxes=[
                    (inbox_1.id, inbox_1.max_emails_per_day, inbox_1.wait_minutes_between),
                    (inbox_2.id, inbox_2.max_emails_per_day, inbox_2.wait_minutes_between),
                ],
                sequences=seqs,
                lead_id=cl.lead_id,
                start_date=start,
            )

        # 20 leads × 4 sequences = 80 total slots
        result = await session.execute(select(func.count(QueueSlot.id)))
        assert result.scalar() == 80

        # No inbox should have more than 5 slots on any single day
        result = await session.execute(select(QueueSlot))
        all_slots = result.scalars().all()
        from collections import defaultdict
        day_inbox_count = defaultdict(int)
        for s in all_slots:
            day_inbox_count[(s.inbox_id, s.scheduled_date.date())] += 1
        for (iid, d), count in day_inbox_count.items():
            assert count <= 5, f"Inbox {iid} has {count} slots on {d} (max 5)"

        # Both inboxes should be used
        used_inboxes = set(s.inbox_id for s in all_slots)
        assert inbox_1.id in used_inboxes
        assert inbox_2.id in used_inboxes


class TestReserveSlotsForNewLead:
    """Tests for reserve_slots_for_new_lead (the higher-level wrapper)."""

    async def test_creates_slots_for_all_sequences(self, session):
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        await make_campaign_inbox(session, campaign.id, inbox.id)

        for i in range(3):
            await make_sequence(session, campaign.id, position=i, wait_days_after_previous=0)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_new_lead(session, cl.id, campaign.id, start_date=date(2026, 3, 2))

        result = await session.execute(
            select(func.count(QueueSlot.id)).where(QueueSlot.campaign_lead_id == cl.id)
        )
        assert result.scalar() == 3

    async def test_no_sequences_creates_nothing(self, session):
        inbox = await make_inbox(session)
        campaign = await make_campaign(session)
        await make_campaign_inbox(session, campaign.id, inbox.id)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_new_lead(session, cl.id, campaign.id)

        result = await session.execute(select(func.count(QueueSlot.id)))
        assert result.scalar() == 0

    async def test_no_inboxes_creates_nothing(self, session):
        campaign = await make_campaign(session)
        await make_sequence(session, campaign.id, position=0)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_new_lead(session, cl.id, campaign.id)

        result = await session.execute(select(func.count(QueueSlot.id)))
        assert result.scalar() == 0

    async def test_missing_campaign_lead_handles_gracefully(self, session):
        """Passing an invalid campaign_lead_id should not crash."""
        campaign = await make_campaign(session)
        await reserve_slots_for_new_lead(session, 9999, campaign.id)
        # No exception raised

    async def test_missing_campaign_handles_gracefully(self, session):
        """Passing an invalid campaign_id should not crash."""
        inbox = await make_inbox(session)
        campaign = await make_campaign(session)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_new_lead(session, cl.id, 9999)
        # No exception raised


class TestRecalculateQueueAfterSequenceChange:
    """Tests for recalculate_queue_after_sequence_change."""

    async def test_deletes_old_slots_and_recreates(self, session):
        """After adding a sequence, old pending slots are deleted and new ones built."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        await make_campaign_inbox(session, campaign.id, inbox.id)

        seq0 = await make_sequence(session, campaign.id, position=0)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Create initial slots
        await reserve_slots_for_new_lead(session, cl.id, campaign.id, start_date=date(2026, 3, 2))

        result = await session.execute(select(func.count(QueueSlot.id)))
        assert result.scalar() == 1

        # Now add a second sequence
        seq1 = await make_sequence(session, campaign.id, position=1, wait_days_after_previous=1)

        # Recalculate
        await recalculate_queue_after_sequence_change(session, campaign.id)

        # Should have 2 slots now
        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 2
        assert slots[0].sequence_index == 0
        assert slots[1].sequence_index == 1

    async def test_preserves_sent_sequences(self, session):
        """If seq 0 was already sent, only seq 1+ should be re-reserved."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        await make_campaign_inbox(session, campaign.id, inbox.id)

        seq0 = await make_sequence(session, campaign.id, position=0)
        seq1 = await make_sequence(session, campaign.id, position=1, wait_days_after_previous=1)
        seq2 = await make_sequence(session, campaign.id, position=2, wait_days_after_previous=1)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Simulate: seq 0 was already sent
        await make_email_log(
            session, lead.id, campaign.id,
            sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime(2026, 3, 2, 10, 0),
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        # Seq 0 already sent → only seq 1, 2 scheduled
        assert len(slots) == 2
        assert slots[0].sequence_index == 1
        assert slots[1].sequence_index == 2

    async def test_recalculate_with_multiple_leads(self, session):
        """Recalculate works correctly when multiple leads are enrolled."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        await make_campaign_inbox(session, campaign.id, inbox.id)

        seqs = [
            await make_sequence(session, campaign.id, position=i, wait_days_after_previous=0)
            for i in range(2)
        ]

        # 5 leads enrolled
        cls = []
        for i in range(5):
            lead = await make_lead(session, email=f"recalc{i}@test.com")
            cl = await make_campaign_lead(session, campaign.id, lead.id)
            cls.append(cl)

        # Initial reservation
        for cl in cls:
            await reserve_slots_for_new_lead(session, cl.id, campaign.id, start_date=date(2026, 3, 2))

        # Should have 5 leads × 2 seqs = 10 slots
        result = await session.execute(select(func.count(QueueSlot.id)))
        assert result.scalar() == 10

        # Simulate: leads 0 and 1 already received seq 0
        for cl in cls[:2]:
            await make_email_log(
                session, cl.lead_id, campaign.id,
                sequence_index=0, inbox_id=inbox.id,
                sent_at=datetime(2026, 3, 2, 10, 0),
            )

        # Add a 3rd sequence
        await make_sequence(session, campaign.id, position=2, wait_days_after_previous=1)

        # Recalculate
        await recalculate_queue_after_sequence_change(session, campaign.id)

        # Leads 0,1: kept seq 0 slot + new seq 1, 2 = 3 slots each = 6
        # Leads 2,3,4: nothing sent → re-reserved seq 0, 1, 2 = 3 slots each = 9
        # Total: 15
        result = await session.execute(select(func.count(QueueSlot.id)))
        assert result.scalar() == 15

    async def test_recalculate_missing_campaign_handles_gracefully(self, session):
        """Recalculation with an invalid campaign_id should not crash."""
        await recalculate_queue_after_sequence_change(session, 9999)


class TestWeekendSkipping:
    """Verifies that scheduling correctly skips non-sending days."""

    async def test_friday_overflow_skips_to_monday(self, session):
        """If capacity is reached on Friday, next slots go to Monday (not Sat/Sun)."""
        inbox = await make_inbox(session, max_emails_per_day=1, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])

        seqs = [await make_sequence(session, campaign.id, position=i) for i in range(2)]
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Start on Friday March 6 2026
        fri = date(2026, 3, 6)
        assert fri.weekday() == 4  # Friday

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, 1, 5)],
            sequences=seqs,
            lead_id=lead.id,
            start_date=fri,
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 2
        assert slots[0].scheduled_date.date() == fri  # Friday
        assert slots[1].scheduled_date.date() == date(2026, 3, 9)  # Monday

    async def test_weekend_only_sending_days(self, session):
        """Campaign that only sends on weekends should schedule on Sat/Sun."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[5, 6])  # Sat, Sun

        seq = await make_sequence(session, campaign.id, position=0)
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Start on Monday — should skip to Saturday
        mon = date(2026, 3, 2)
        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, 50, 5)],
            sequences=[seq],
            lead_id=lead.id,
            start_date=mon,
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
        )
        slots = result.scalars().all()
        assert len(slots) == 1
        assert slots[0].scheduled_date.date().weekday() in [5, 6]


class TestScheduledTimes:
    """Verify that the actual scheduled_date times are correct based on position."""

    async def test_positions_have_correct_time_offsets(self, session):
        """First email at 09:00, second at 09:05, third at 09:10 etc."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(
            session, sending_hours_start="09:00", sending_hours_end="17:00",
        )

        seqs = [await make_sequence(session, campaign.id, position=i) for i in range(4)]
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=seqs,
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        times = [s.scheduled_date.time() for s in slots]
        assert times[0] == time(9, 0)
        assert times[1] == time(9, 5)
        assert times[2] == time(9, 10)
        assert times[3] == time(9, 15)

    async def test_different_wait_minutes(self, session):
        """With wait_minutes=15, positions are 15 minutes apart."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=15)
        campaign = await make_campaign(
            session, sending_hours_start="10:00", sending_hours_end="17:00",
        )

        seqs = [await make_sequence(session, campaign.id, position=i) for i in range(3)]
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=seqs,
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
        )

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        times = [s.scheduled_date.time() for s in slots]
        assert times[0] == time(10, 0)
        assert times[1] == time(10, 15)
        assert times[2] == time(10, 30)


class TestUniqueConstraint:
    """Verify the unique constraint on (campaign_lead_id, sequence_index)."""

    async def test_no_duplicate_sequence_index_per_lead(self, session):
        """reserve_slots_for_lead should never create two slots with the same sequence_index for one lead."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session)
        seqs = [await make_sequence(session, campaign.id, position=i) for i in range(5)]
        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await reserve_slots_for_lead(
            session, cl.id, campaign,
            inboxes=[(inbox.id, inbox.max_emails_per_day, inbox.wait_minutes_between)],
            sequences=seqs,
            lead_id=lead.id,
            start_date=date(2026, 3, 2),
        )

        result = await session.execute(
            select(QueueSlot.sequence_index).where(QueueSlot.campaign_lead_id == cl.id)
        )
        indices = [r[0] for r in result.all()]
        assert len(indices) == len(set(indices)), "Duplicate sequence indices found!"


# ============================================================================
# 4. RECALCULATION-SPECIFIC TESTS
#    Verifies that recalculation keeps the natural sequence flow, never
#    re-sends past emails, and correctly respects wait_days from the
#    actual date the last email was sent.
# ============================================================================


class TestRecalculationWaitDaysRespected:
    """
    Core user requirement: when recalculating, if a sequence says 'wait 2 days
    after previous' and the previous was sent on Monday, the next should be
    Wednesday — regardless of when the recalculation happens.
    """

    async def test_wait_days_applied_from_last_sent_date(self, session):
        """
        Seq 0 sent Monday. Seq 1 has wait_days=3.
        After recalculation, seq 1 must land on Thursday (Mon + 3 biz days),
        NOT on whatever day the recalculation runs.
        Uses future dates so 'today' never interferes.
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        seq0 = await make_sequence(session, campaign.id, position=0, wait_days_after_previous=0)
        seq1 = await make_sequence(session, campaign.id, position=1, wait_days_after_previous=3)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        sent_monday = date(2027, 6, 7)  # Monday, far in the future
        await make_email_log(
            session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(sent_monday, time(10, 0)),
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
        )
        slots = result.scalars().all()
        assert len(slots) == 1
        assert slots[0].sequence_index == 1
        # Monday + 3 business days = Thursday June 10
        assert slots[0].scheduled_date.date() == date(2027, 6, 10)

    async def test_wait_days_chain_preserved_across_sequences(self, session):
        """
        Seq 0 sent Monday. Seq 1 wait=1, Seq 2 wait=2, Seq 3 wait=1.
        Expected chain: Mon → Tue → Thu → Fri.
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        await make_sequence(session, campaign.id, position=0, wait_days_after_previous=0)
        await make_sequence(session, campaign.id, position=1, wait_days_after_previous=1)
        await make_sequence(session, campaign.id, position=2, wait_days_after_previous=2)
        await make_sequence(session, campaign.id, position=3, wait_days_after_previous=1)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        sent_monday = date(2027, 6, 7)  # Monday
        await make_email_log(
            session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(sent_monday, time(10, 0)),
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 3
        dates = [s.scheduled_date.date() for s in slots]
        # Seq 1: Mon+1 = Tue June 8
        assert dates[0] == date(2027, 6, 8)
        # Seq 2: Tue+2 = Thu June 10
        assert dates[1] == date(2027, 6, 10)
        # Seq 3: Thu+1 = Fri June 11
        assert dates[2] == date(2027, 6, 11)

    async def test_wait_days_with_weekend_crossing(self, session):
        """
        Seq 0 sent Thursday. Seq 1 wait=2 (should land on Monday, skipping weekend).
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        await make_sequence(session, campaign.id, position=0, wait_days_after_previous=0)
        await make_sequence(session, campaign.id, position=1, wait_days_after_previous=2)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        sent_thu = date(2027, 6, 10)  # Thursday
        assert sent_thu.weekday() == 3
        await make_email_log(
            session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(sent_thu, time(10, 0)),
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
        )
        slots = result.scalars().all()
        assert len(slots) == 1
        # Thu + 2 biz days = Fri(1) + Mon(2) = Monday June 14
        assert slots[0].scheduled_date.date() == date(2027, 6, 14)


class TestRecalculationElapsedWait:
    """
    When the wait period has partially or fully elapsed between the original
    send and the recalculation, the system should be smart:
    - If time remains → schedule at the correct remaining offset
    - If wait fully elapsed → schedule ASAP (today/next business day)
    - Never schedule in the past
    """

    async def test_partial_wait_remaining_schedules_at_correct_date(self, session):
        """
        Seq 0 sent 1 day ago. Seq 1 wait=3. 2 biz days still remain.
        Should schedule 2 biz days from now, NOT 3 from now.
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        await make_sequence(session, campaign.id, position=0)
        await make_sequence(session, campaign.id, position=1, wait_days_after_previous=3)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Sent "yesterday" — find a past weekday
        today = date.today()
        sent_date = today - timedelta(days=1)
        # Make sure sent_date is a weekday
        while sent_date.weekday() > 4:
            sent_date -= timedelta(days=1)

        await make_email_log(
            session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(sent_date, time(10, 0)),
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
        )
        slots = result.scalars().all()
        assert len(slots) == 1

        # The slot should be at exactly sent_date + 3 business days
        expected = next_business_date(sent_date, [0, 1, 2, 3, 4], 3)
        actual = slots[0].scheduled_date.date()
        assert actual == expected, (
            f"Expected {expected} (sent_date={sent_date} + 3 biz days), got {actual}"
        )

    async def test_fully_elapsed_wait_schedules_asap(self, session):
        """
        Seq 0 sent 2 weeks ago. Seq 1 wait=1. Wait fully elapsed.
        Should schedule on today (or next business day), NOT in the past.
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        await make_sequence(session, campaign.id, position=0)
        await make_sequence(session, campaign.id, position=1, wait_days_after_previous=1)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        # Sent 2 weeks ago
        old_date = date.today() - timedelta(days=14)
        while old_date.weekday() > 4:
            old_date -= timedelta(days=1)
        await make_email_log(
            session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(old_date, time(10, 0)),
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
        )
        slots = result.scalars().all()
        assert len(slots) == 1

        # Should be today or the next business day — never in the past
        today = date.today()
        assert slots[0].scheduled_date.date() >= today

    async def test_never_schedules_in_the_past(self, session):
        """
        Seq 0 sent 30 days ago. Seq 1 wait=2, Seq 2 wait=1.
        All waits have long elapsed. Everything should land on today or later.
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        await make_sequence(session, campaign.id, position=0)
        await make_sequence(session, campaign.id, position=1, wait_days_after_previous=2)
        await make_sequence(session, campaign.id, position=2, wait_days_after_previous=1)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        old_date = date.today() - timedelta(days=30)
        while old_date.weekday() > 4:
            old_date -= timedelta(days=1)
        await make_email_log(
            session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(old_date, time(10, 0)),
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        assert len(slots) == 2
        today = date.today()
        for s in slots:
            assert s.scheduled_date.date() >= today, f"Slot seq {s.sequence_index} scheduled in the past: {s.scheduled_date.date()}"


class TestRecalculationNoResend:
    """Recalculation must NEVER create slots for already-sent sequences."""

    async def test_sent_sequences_never_rescheduled(self, session):
        """
        Seq 0 and 1 already sent. Recalculate creates slots only for seq 2, 3.
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        for i in range(4):
            await make_sequence(session, campaign.id, position=i, wait_days_after_previous=1 if i > 0 else 0)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        sent_mon = date(2027, 6, 7)
        await make_email_log(
            session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(sent_mon, time(10, 0)),
        )
        await make_email_log(
            session, lead.id, campaign.id, sequence_index=1, inbox_id=inbox.id,
            sent_at=datetime.combine(sent_mon + timedelta(days=1), time(10, 0)),
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        slots = result.scalars().all()
        indices = [s.sequence_index for s in slots]
        assert 0 not in indices, "Seq 0 was already sent — should NOT be rescheduled"
        assert 1 not in indices, "Seq 1 was already sent — should NOT be rescheduled"
        assert 2 in indices
        assert 3 in indices

    async def test_all_sent_creates_no_slots(self, session):
        """If all sequences are already sent, recalculation creates zero new slots."""
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        for i in range(3):
            await make_sequence(session, campaign.id, position=i)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        for i in range(3):
            await make_email_log(
                session, lead.id, campaign.id, sequence_index=i, inbox_id=inbox.id,
                sent_at=datetime(2027, 6, 7 + i, 10, 0),
            )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(func.count(QueueSlot.id)).where(QueueSlot.campaign_lead_id == cl.id)
        )
        assert result.scalar() == 0


class TestRecalculationMultiLeadDifferentProgress:
    """
    Realistic scenario: multiple leads at different stages of the sequence.
    Recalculation should handle each lead independently.
    """

    async def test_leads_at_different_progress_levels(self, session):
        """
        3 leads:
          - Lead A: nothing sent (fresh) → gets all 4 sequences
          - Lead B: seq 0 sent → gets seq 1, 2, 3 with correct wait offsets
          - Lead C: seq 0 + 1 sent → gets seq 2, 3 with correct wait offsets
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        await make_sequence(session, campaign.id, position=0, wait_days_after_previous=0)
        await make_sequence(session, campaign.id, position=1, wait_days_after_previous=2)
        await make_sequence(session, campaign.id, position=2, wait_days_after_previous=1)
        await make_sequence(session, campaign.id, position=3, wait_days_after_previous=3)

        sent_base = date(2027, 6, 7)  # Monday (far future)

        # Lead A: fresh
        lead_a = await make_lead(session, email="a@test.com")
        cl_a = await make_campaign_lead(session, campaign.id, lead_a.id)

        # Lead B: seq 0 sent on Monday
        lead_b = await make_lead(session, email="b@test.com")
        cl_b = await make_campaign_lead(session, campaign.id, lead_b.id)
        await make_email_log(
            session, lead_b.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(sent_base, time(10, 0)),
        )

        # Lead C: seq 0 sent Monday, seq 1 sent Wednesday
        lead_c = await make_lead(session, email="c@test.com")
        cl_c = await make_campaign_lead(session, campaign.id, lead_c.id)
        await make_email_log(
            session, lead_c.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(sent_base, time(10, 0)),
        )
        await make_email_log(
            session, lead_c.id, campaign.id, sequence_index=1, inbox_id=inbox.id,
            sent_at=datetime.combine(date(2027, 6, 9), time(10, 0)),  # Wed
        )

        await recalculate_queue_after_sequence_change(session, campaign.id)

        # --- Lead A: all 4 sequences ---
        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl_a.id).order_by(QueueSlot.sequence_index)
        )
        slots_a = result.scalars().all()
        assert len(slots_a) == 4
        assert [s.sequence_index for s in slots_a] == [0, 1, 2, 3]

        # --- Lead B: seq 1, 2, 3 ---
        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl_b.id).order_by(QueueSlot.sequence_index)
        )
        slots_b = result.scalars().all()
        assert len(slots_b) == 3
        assert [s.sequence_index for s in slots_b] == [1, 2, 3]
        # Seq 1: Mon(June 7) + 2 biz days = Wed June 9
        assert slots_b[0].scheduled_date.date() == date(2027, 6, 9)
        # Seq 2: Wed + 1 biz day = Thu June 10
        assert slots_b[1].scheduled_date.date() == date(2027, 6, 10)
        # Seq 3: Thu + 3 biz days = Tue June 15 (crosses weekend)
        assert slots_b[2].scheduled_date.date() == date(2027, 6, 15)

        # --- Lead C: seq 2, 3 only ---
        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl_c.id).order_by(QueueSlot.sequence_index)
        )
        slots_c = result.scalars().all()
        assert len(slots_c) == 2
        assert [s.sequence_index for s in slots_c] == [2, 3]
        # Seq 2: Wed(June 9) + 1 biz day = Thu June 10
        assert slots_c[0].scheduled_date.date() == date(2027, 6, 10)
        # Seq 3: Thu + 3 biz days = Tue June 15
        assert slots_c[1].scheduled_date.date() == date(2027, 6, 15)

    async def test_ten_leads_mixed_progress_recalculation(self, session):
        """
        10 leads, each at a different progress level (0-2 sent).
        Add a new sequence and recalculate. Verify correct slot counts.
        """
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        # Original 3 sequences
        for i in range(3):
            await make_sequence(session, campaign.id, position=i, wait_days_after_previous=1 if i > 0 else 0)

        sent_base = date(2027, 6, 7)  # Monday

        leads_and_cls = []
        for i in range(10):
            lead = await make_lead(session, email=f"mixed{i}@test.com")
            cl = await make_campaign_lead(session, campaign.id, lead.id)
            leads_and_cls.append((lead, cl))

            # Leads 0-3: nothing sent
            # Leads 4-6: seq 0 sent
            # Leads 7-9: seq 0 and 1 sent
            if i >= 4:
                await make_email_log(
                    session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
                    sent_at=datetime.combine(sent_base, time(10, 0)),
                )
            if i >= 7:
                await make_email_log(
                    session, lead.id, campaign.id, sequence_index=1, inbox_id=inbox.id,
                    sent_at=datetime.combine(sent_base + timedelta(days=1), time(10, 0)),
                )

        # Now add a 4th sequence and recalculate
        await make_sequence(session, campaign.id, position=3, wait_days_after_previous=2)

        await recalculate_queue_after_sequence_change(session, campaign.id)

        # Expected slot counts:
        # Leads 0-3 (nothing sent): 4 sequences each = 16
        # Leads 4-6 (seq 0 sent): seq 1,2,3 = 3 each = 9
        # Leads 7-9 (seq 0+1 sent): seq 2,3 = 2 each = 6
        # Total: 31
        result = await session.execute(select(func.count(QueueSlot.id)))
        total = result.scalar()
        assert total == 31, f"Expected 31 slots total, got {total}"

        # Verify no lead has a slot for an already-sent sequence
        for lead, cl in leads_and_cls:
            result = await session.execute(
                select(QueueSlot.sequence_index).where(QueueSlot.campaign_lead_id == cl.id)
            )
            scheduled_indices = set(r[0] for r in result.all())

            # Check sent sequences are not scheduled
            sent_result = await session.execute(
                select(EmailLog.sequence_index).where(
                    EmailLog.lead_id == lead.id, EmailLog.campaign_id == campaign.id,
                )
            )
            sent_indices = set(r[0] for r in sent_result.all())
            overlap = scheduled_indices & sent_indices
            assert not overlap, (
                f"Lead {lead.email}: sent indices {sent_indices} overlap with scheduled {scheduled_indices}"
            )


class TestRecalculationIdempotent:
    """Running recalculate twice should produce the same result."""

    async def test_double_recalculate_same_result(self, session):
        inbox = await make_inbox(session, max_emails_per_day=50, wait_minutes_between=5)
        campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
        await make_campaign_inbox(session, campaign.id, inbox.id)

        for i in range(3):
            await make_sequence(session, campaign.id, position=i, wait_days_after_previous=1 if i > 0 else 0)

        lead = await make_lead(session)
        cl = await make_campaign_lead(session, campaign.id, lead.id)

        await make_email_log(
            session, lead.id, campaign.id, sequence_index=0, inbox_id=inbox.id,
            sent_at=datetime.combine(date(2027, 6, 7), time(10, 0)),
        )

        # First recalculate
        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        first_run = [(s.sequence_index, s.scheduled_date.date()) for s in result.scalars().all()]

        # Second recalculate
        await recalculate_queue_after_sequence_change(session, campaign.id)

        result = await session.execute(
            select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id).order_by(QueueSlot.sequence_index)
        )
        second_run = [(s.sequence_index, s.scheduled_date.date()) for s in result.scalars().all()]

        assert first_run == second_run, (
            f"Recalculation is not idempotent:\n  1st: {first_run}\n  2nd: {second_run}"
        )
