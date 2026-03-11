"""Notification preferences API routes."""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import EmailNotificationConfig, WEBHOOK_EVENT_TYPES

log = logging.getLogger("quickly.notifications.routes")

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationConfigRequest(BaseModel):
    enabled: bool = False
    notification_email: str = ""
    events: list[str] = []
    rate_limit_per_hour: int = Field(default=10, ge=1, le=100)


class NotificationConfigResponse(BaseModel):
    enabled: bool
    notification_email: str
    events: list[str]
    rate_limit_per_hour: int

    class Config:
        from_attributes = True


@router.get("/config", response_model=NotificationConfigResponse)
async def get_notification_config(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's email notification configuration."""
    result = await db.execute(
        select(EmailNotificationConfig).where(EmailNotificationConfig.user_id == user.id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        return NotificationConfigResponse(
            enabled=False, notification_email="", events=[], rate_limit_per_hour=10,
        )
    return config


@router.put("/config", response_model=NotificationConfigResponse)
async def update_notification_config(
    data: NotificationConfigRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update the current user's email notification configuration."""
    # Validate event types
    invalid = [e for e in data.events if e not in WEBHOOK_EVENT_TYPES]
    if invalid:
        raise HTTPException(400, f"Invalid event types: {invalid}")

    result = await db.execute(
        select(EmailNotificationConfig).where(EmailNotificationConfig.user_id == user.id)
    )
    config = result.scalar_one_or_none()

    if config is None:
        config = EmailNotificationConfig(user_id=user.id)
        db.add(config)

    config.enabled = data.enabled
    config.notification_email = data.notification_email.strip()
    config.events = data.events
    config.rate_limit_per_hour = data.rate_limit_per_hour
    await db.flush()
    return config
