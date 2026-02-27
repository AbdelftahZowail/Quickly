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
