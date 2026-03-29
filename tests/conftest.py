"""Shared fixtures for queue_logic tests.

Provides an async database session and factory helpers that create Campaign,
Inbox, Sequence, Lead, CampaignLead, CampaignInbox, EmailLog, and QueueSlot
records for realistic integration testing.
"""

import os
# default the test database to an in-memory SQLite URL; this allows the
# application to boot without a running Postgres instance while still
# exercising real SQLAlchemy behavior.  Users may override by setting
# TEST_DATABASE_URL in their environment.
os.environ.setdefault("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:?cache=shared")
# Production Docker sets QUICKLY_PREBUILT_IMAGE=1; tests default to “source” layout unless overridden.
os.environ.setdefault("QUICKLY_PREBUILT_IMAGE", "0")

# tests rely on a Postgres (or other SQLAlchemy) database.  Specify a
# connection string via TEST_DATABASE_URL; if unset the regular
# ``settings.database_url`` will be used.  The previous SQLite-specific
# helpers have been removed.

import contextlib
import uuid

import pytest
import pytest_asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.database import Base
from app.models import (
    Campaign, Inbox, Sequence, Lead, CampaignLead,
    CampaignInbox, QueueSlot, EmailLog, EmailClick, LeadReply,
    AppSetting, LeadUnsubscribeToken,
)


@pytest.fixture(autouse=True)
def quickly_test_logs_dir(tmp_path, monkeypatch):
    """Route schedule debug output away from repo ``logs/`` (often not writable in CI)."""
    d = tmp_path / "logs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUICKLY_TEST_LOGS_DIR", str(d))


@contextlib.asynccontextmanager
async def _noop_mcp_lifespan():
    yield


@pytest.fixture(autouse=True)
def _disable_mcp_lifespan_repeat_app_starts(monkeypatch):
    """FastMCP's session_manager.run() is single-use; TestClient restarts the app per test."""
    monkeypatch.setattr("app.mcp_leads.leads_mcp_lifespan", _noop_mcp_lifespan)


@pytest_asyncio.fixture
async def engine():
    """Create a database engine for tests.

    If ``TEST_DATABASE_URL`` is defined, use it.  Otherwise fall back to an
    in-memory SQLite database (shared cache, small connection pool) so the
    entire test suite can be run without a Postgres server.
    """
    from app.settings_manager import settings
    from sqlalchemy.exc import OperationalError

    url = os.getenv("TEST_DATABASE_URL") or settings.database_url
    eng = None
    if url and not url.startswith("sqlite"):
        # try connecting; if any error occurs, fall back to SQLite
        try:
            eng = create_async_engine(url, echo=False)
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        except Exception:
            # best effort dispose then forget
            try:
                await eng.dispose()
            except Exception:
                pass
            eng = None

    if eng is None:
        # Shared in-memory SQLite with multiple pooled connections. StaticPool
        # uses a single connection; tests that run global recalc in AsyncSessionLocal
        # while holding a test session would otherwise block each other on SQLite.
        from sqlalchemy.pool import AsyncAdaptedQueuePool

        # Named shared-cache DB so every pooled connection shares one schema.
        # ``:memory:?cache=shared`` alone does not reliably attach all pool
        # connections to the same database, which breaks background sessions.
        _mem = uuid.uuid4().hex
        # ``uri=true`` is required: without it SQLAlchemy resolves ``file:...`` to a
        # cwd-relative path and drops ``mode=memory`` from the connect string, so
        # each test leaves a real on-disk file named ``file:qtest_<uuid>``.
        eng = create_async_engine(
            f"sqlite+aiosqlite:///file:qtest_{_mem}?uri=true&mode=memory&cache=shared",
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 30.0},
            poolclass=AsyncAdaptedQueuePool,
            pool_size=5,
            max_overflow=10,
        )
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Do not run create_all from a sync_engine "connect" listener when using a
    # multi-connection pool: create_all(bind=engine) checks out another pooled
    # connection while the connect hook is still running, which deadlocks.
    # Shared-cache in-memory SQLite already sees tables created above.

    # ensure the global app.database engine matches the test engine so
    # requests via FastAPI use the same database connection state.  Without
    # this, two independent engines would each open their own in-memory
    # database and dropping tables in one would not affect the other, which
    # was the cause of intermittent ``no such table`` errors when running
    # the full suite.
    from app import database as _adb
    # Save originals so we can restore them after the test.  Without this,
    # the next test that calls TestClient(app) would find app.database.engine
    # pointing to a disposed engine, causing init_db() to hang.
    _original_engine = _adb.engine
    _original_session_maker = _adb.AsyncSessionLocal
    _adb.engine = eng
    _adb.AsyncSessionLocal = async_sessionmaker(
        eng, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
    )

    yield eng

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()
    # Restore the original engine/sessionmaker so subsequent tests that use
    # TestClient(app) (and thus trigger init_db()) don't get a dead engine.
    _adb.engine = _original_engine
    _adb.AsyncSessionLocal = _original_session_maker
    # Recreate the schema on the original engine so that tests which use
    # TestClient(app) without the engine fixture find the tables ready.
    # (drop_all above also clears the shared in-memory SQLite used by the
    # original engine, so we need to re-populate it here.)
    async with _original_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def session(engine):
    """Provide a clean async session that rolls back after each test."""
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with async_session() as sess:
        # patch commit/flush to avoid sqlite thread issues by running
        # synchronous flush/commit on the active connection instead of
        # letting the default implementations spawn background threads.
        async def _sync_flush():
            await sess.run_sync(lambda sync: sync.flush())
        async def _sync_commit():
            await sess.run_sync(lambda sync: sync.commit())
        sess.flush = _sync_flush
        sess.commit = _sync_commit
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
    provider: str = "gmail",
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


async def make_email_click(
    session: AsyncSession,
    email_log_id: int,
    ip_address: str = "1.2.3.4",
    clicked_at: datetime | None = None,
) -> EmailClick:
    click = EmailClick(
        email_log_id=email_log_id,
        ip_address=ip_address,
        clicked_at=clicked_at or datetime.utcnow(),
    )
    session.add(click)
    await session.flush()
    return click


async def make_unsubscribe_token(
    session: AsyncSession,
    lead_id: int,
    campaign_id: int,
    token: str = "tok",
) -> LeadUnsubscribeToken:
    row = LeadUnsubscribeToken(
        lead_id=lead_id,
        campaign_id=campaign_id,
        token=token,
    )
    session.add(row)
    await session.flush()
    return row


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
