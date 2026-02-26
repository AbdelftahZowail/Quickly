from app.time import persist_offset_days, clear_persisted_offset
from app.settings_manager import settings, load_settings_from_db
from app.models import AppSetting
from sqlalchemy import select


async def test_persist_and_clear_offset_updates_settings_and_db(session):
    # default should be 0
    assert settings.time_offset_days == 0

    await persist_offset_days(session, 5)

    # in-memory settings should be updated via update_setting -> reload
    assert settings.time_offset_days == 5

    # DB should contain the stored value
    data = await load_settings_from_db(session)
    assert data.get("time_offset_days") == "5"

    # Clear back to 0
    await clear_persisted_offset(session)
    assert settings.time_offset_days == 0
    data = await load_settings_from_db(session)
    # clearing persists '0'
    assert data.get("time_offset_days") == "0"


def test_env_var_inits_offset_default(monkeypatch):
    """TIME_OFFSET_DAYS from the environment should set the initial value.

    The application reads this only during Settings() initialization; once the
    DB has been seeded the persisted value takes precedence.
    """
    from app.settings_manager import Settings

    # valid integer should be accepted
    monkeypatch.setenv("TIME_OFFSET_DAYS", "7")
    s = Settings()
    assert s.time_offset_days == 7

    # invalid value falls back to zero rather than raising
    monkeypatch.setenv("TIME_OFFSET_DAYS", "notanint")
    s2 = Settings()
    assert s2.time_offset_days == 0


def test_env_var_inits_test_mode(monkeypatch):
    """TEST_MODE from the environment should set the initial value.

    This is similar to the time offset behavior: the environment variable is
    consulted only during ``Settings`` construction; afterwards the database
    value overrides.
    """
    from app.settings_manager import Settings

    for truthy in ("true", "1", "yes", "YeS"):
        monkeypatch.setenv("TEST_MODE", truthy)
        s = Settings()
        assert s.test_mode is True

    for falsy in ("false", "0", "no", ""):
        monkeypatch.setenv("TEST_MODE", falsy)
        s2 = Settings()
        assert s2.test_mode is False
