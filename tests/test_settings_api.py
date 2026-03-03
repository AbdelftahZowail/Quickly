import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from app.app_settings import get_test_mode, set_test_mode
from app.routers.test_mode import test_status, set_test_status, _TestModePayload
from app.settings_manager import settings
from app.database import AsyncSessionLocal


@pytest.mark.asyncio
async def test_test_mode_settings_api(engine):
    """Verify test‑mode helper functions and legacy status route.

    We avoid exercising the HTTP layer here because startup events and the
    in‑memory SQLite engine can be tricky when the full suite runs.  The
    engine fixture already creates a clean schema; just open a session and
    call the underlying helpers directly.
    """

    # make sure the schema exists on whatever engine FastAPI will use
    from app.database import engine as _engine, Base as _Base
    async with _engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)

    # import sessionmaker after the engine fixture has patched it so we
    # don't accidentally use the stale session bound to the original engine
    from app.database import AsyncSessionLocal

    # open a session backed by the test engine
    async with AsyncSessionLocal() as db:  # type: AsyncSession
        # ensure settings have been initialized (mimics init_db startup)
        from app.settings_manager import initialize_settings

        await initialize_settings(db)

        # default should be False
        assert await get_test_mode(db) is False
        assert await test_status() == {"test_mode": False}

        # toggle via helper and verify in memory cache
        await set_test_mode(db, True)
        assert await get_test_mode(db) is True
        assert settings.test_mode is True
        assert await test_status() == {"test_mode": True}

        # legacy POST handler should also work
        payload = _TestModePayload(test_mode=False)
        resp = await set_test_status(payload, db)
        assert resp["test_mode"] is False
        assert await get_test_mode(db) is False
        assert settings.test_mode is False

        # confirm Google OAuth credentials are **not** seeded into the database
        from sqlalchemy import select
        from app.models import AppSetting

        result = await db.execute(
            select(AppSetting).where(
                AppSetting.key.in_(
                    ["google_client_id", "google_client_secret"]
                )
            )
        )
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_add_opens_setting_endpoint(session):
    """Verify the ``POST /api/settings/add-opens`` helper adds events.

    Create a minimal email log then invoke the new endpoint via HTTP;
    afterwards the database should contain an ``EmailOpen`` and the log’s
    ``opened`` flag should be true.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import select
    from app.models import EmailLog, EmailOpen
    from tests.conftest import make_campaign, make_lead, make_email_log

    # create related objects and a single log row
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    log = await make_email_log(session, lead.id, campaign.id)
    log_id = log.id  # save before expiring
    await session.commit()

    with TestClient(app) as client:
        resp = client.post("/api/settings/add-opens")
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 1
        assert data["total"] == 1

    # expire the session cache so we read fresh data committed by the endpoint
    session.expire_all()

    # inspect database to confirm open event was written and flag toggled
    result = await session.execute(select(EmailOpen))
    opens = result.scalars().all()
    assert len(opens) == 1
    assert opens[0].email_log_id == log_id

    # refresh the log row
    result = await session.execute(select(EmailLog).where(EmailLog.id == log_id))
    refreshed = result.scalar_one()
    assert refreshed.opened is True


@pytest.mark.asyncio
async def test_google_oauth_credentials_ignore_db(engine):
    """Even if the DB contains values, the helpers always honor the env.

    This mirrors the desired behaviour of the running application after the
    recent refactor: credentials are supplied from the environment and the
    database is no longer consulted.
    """
    from app.app_settings import get_google_oauth_credentials
    from app.models import AppSetting
    from sqlalchemy import insert

    # ensure schema exists
    from app.database import engine as _engine, Base as _Base
    async with _engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)

    # set environment values in the settings object manually
    settings.google_client_id = "env-id"
    settings.google_client_secret = "env-secret"

    # use the supplied engine directly to avoid stale sessionmaker imports
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    engine_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with engine_session() as db:
        # write conflicting values into the DB directly
        await db.execute(insert(AppSetting).values(key="google_client_id", value="db-id"))
        await db.execute(insert(AppSetting).values(key="google_client_secret", value="db-secret"))
        await db.commit()

        cid, csec = await get_google_oauth_credentials(db)
        assert cid == "env-id"
        assert csec == "env-secret"
