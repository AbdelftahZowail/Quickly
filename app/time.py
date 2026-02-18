"""Application-wide time provider with configurable offset for testing/time-travel.

Usage:
    from app import time as time_provider
    now = time_provider.now()
    utcnow = time_provider.utcnow()
    today = time_provider.today()

The offset is taken (in days) from `app.settings_manager.settings.time_offset_days` so it can
be persisted via the existing AppSetting mechanism. The module provides helpers to
set/reset the persisted offset from scripts.
"""
from datetime import datetime, date, timedelta
from typing import Optional

from app.settings_manager import settings


def _offset_timedelta() -> timedelta:
    return timedelta(days=int(settings.time_offset_days or 0))


def now() -> datetime:
    """Local "now" adjusted by configured offset."""
    return datetime.now() + _offset_timedelta()


def utcnow() -> datetime:
    """UTC now adjusted by configured offset."""
    return datetime.utcnow() + _offset_timedelta()


def today() -> date:
    """Date for 'today' adjusted by configured offset."""
    return now().date()


def offset_days() -> int:
    return int(settings.time_offset_days or 0)


# The persistence helpers below interact with AppSetting via settings_manager
# (imported where needed) so scripts can set/reset the offset. We keep the
# DB-related helpers here as simple convenience functions.

from sqlalchemy.ext.asyncio import AsyncSession
from app.settings_manager import save_setting_to_db, update_setting


async def persist_offset_days(db: AsyncSession, days: int | None) -> None:
    """Persist an integer day offset (or clear if days is None).

    If days is None we save '0' which means no offset.
    """
    value = "0" if days is None else str(int(days))
    await update_setting(db, "time_offset_days", value)


async def clear_persisted_offset(db: AsyncSession) -> None:
    await persist_offset_days(db, 0)
