"""Shared fixtures for queue_logic tests.

Provides an async in-memory SQLite database session and factory helpers
that create Campaign, Inbox, Sequence, Lead, CampaignLead, CampaignInbox,
EmailLog, and QueueSlot records for realistic integration testing.
"""
import pytest
import pytest_asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.database import Base
from app.models import (
    Campaign, Inbox, Sequence, Lead, CampaignLead,
    CampaignInbox, QueueSlot, EmailLog, LeadReply,
)


@pytest_asyncio.fixture
async def engine():
    """Create an in-memory async SQLite engine and tear it down after the test."""
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    """Provide a clean async session that rolls back after each test."""
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with async_session() as sess:
        yield sess


# ---------------------------------------------------------------------------
# Factory helpers – return the ORM instance after flush so .id is populated.
# ---------------------------------------------------------------------------

async def make_inbox(
    session: AsyncSession,
    email: str = "inbox@test.com",
    max_emails_per_day: int = 50,
    wait_minutes_between: int = 5,
    display_name: str = "",
    provider: str = "resend",
) -> Inbox:
    inbox = Inbox(
        email=email,
        display_name=display_name,
        max_emails_per_day=max_emails_per_day,
        wait_minutes_between=wait_minutes_between,
        provider=provider,
    )
    session.add(inbox)
    await session.flush()
    return inbox


async def make_campaign(
    session: AsyncSession,
    name: str = "Test Campaign",
    sending_days: list | None = None,
    sending_hours_start: str = "09:00",
    sending_hours_end: str = "17:00",
    wait_minutes_between: int = 5,
    stop_on_reply: bool = True,
    paused: bool = False,
) -> Campaign:
    campaign = Campaign(
        name=name,
        sending_days=sending_days if sending_days is not None else [0, 1, 2, 3, 4],
        sending_hours_start=sending_hours_start,
        sending_hours_end=sending_hours_end,
        wait_minutes_between=wait_minutes_between,
        stop_on_reply=stop_on_reply,
        paused=paused,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def make_sequence(
    session: AsyncSession,
    campaign_id: int,
    position: int = 0,
    subject: str = "Hello",
    body: str = "Hi there",
    wait_days_after_previous: int = 0,
) -> Sequence:
    seq = Sequence(
        campaign_id=campaign_id,
        position=position,
        subject=subject,
        body=body,
        wait_days_after_previous=wait_days_after_previous,
    )
    session.add(seq)
    await session.flush()
    return seq


async def make_lead(
    session: AsyncSession,
    email: str = "lead@example.com",
    name: str = "Test Lead",
) -> Lead:
    lead = Lead(email=email, name=name)
    session.add(lead)
    await session.flush()
    return lead


async def make_campaign_lead(
    session: AsyncSession,
    campaign_id: int,
    lead_id: int,
) -> CampaignLead:
    cl = CampaignLead(campaign_id=campaign_id, lead_id=lead_id)
    session.add(cl)
    await session.flush()
    return cl


async def make_campaign_inbox(
    session: AsyncSession,
    campaign_id: int,
    inbox_id: int,
    position: int = 0,
) -> CampaignInbox:
    ci = CampaignInbox(
        campaign_id=campaign_id,
        inbox_id=inbox_id,
        position=position,
    )
    session.add(ci)
    await session.flush()
    return ci


async def make_email_log(
    session: AsyncSession,
    lead_id: int,
    campaign_id: int,
    sequence_index: int = 0,
    inbox_id: int | None = None,
    sent_at: datetime | None = None,
    subject: str = "Test",
) -> EmailLog:
    log = EmailLog(
        lead_id=lead_id,
        campaign_id=campaign_id,
        sequence_index=sequence_index,
        inbox_id=inbox_id,
        sent_at=sent_at or datetime.utcnow(),
        subject=subject,
    )
    session.add(log)
    await session.flush()
    return log


async def make_queue_slot(
    session: AsyncSession,
    campaign_lead_id: int,
    inbox_id: int,
    sequence_index: int = 0,
    scheduled_date: datetime | None = None,
    position_in_day: int = 1,
) -> QueueSlot:
    slot = QueueSlot(
        campaign_lead_id=campaign_lead_id,
        inbox_id=inbox_id,
        sequence_index=sequence_index,
        scheduled_date=scheduled_date or datetime(2026, 3, 2, 9, 0),
        position_in_day=position_in_day,
    )
    session.add(slot)
    await session.flush()
    return slot
