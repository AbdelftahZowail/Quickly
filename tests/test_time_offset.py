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