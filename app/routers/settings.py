"""Settings API routes for managing configuration."""
import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.settings_manager import settings


def _mask_secret(val: str) -> str:
    """Hide sensitive pieces of a string for display.

    This is a very small helper used by the settings API; it mirrors the
    behavior the front‑end expects and avoids exporting real secrets.
    """
    if not val:
        return ""
    if len(val) <= 4:
        return "****"
    return val[0] + "***" + val[-1]
from app.app_settings import (
    get_gmail_sync_config,
    save_gmail_sync_config,
    get_scheduling_strategy,
    set_scheduling_strategy,
    get_test_mode,
    set_test_mode,
    get_email_event_webhook_config,
    save_email_event_webhook_config,
    get_lead_reply_webhook_config,
    save_lead_reply_webhook_config,
)
from sqlalchemy import select
from app.models import EmailLog, EmailOpen
from app.webhooks import maybe_fire_email_event, fire_lead_reply_webhook

log = logging.getLogger("quickly.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])

class GmailSyncConfig(BaseModel):
    push_topic: str = ""
    webhook_token: str = ""
    sync_interval_minutes: int = 5


class EmailWebhookConfig(BaseModel):
    webhook_url: str = ""
    webhook_token: str = ""




# Time offset helpers ---------------------------------------------------------
@router.get('/time-offset')
async def get_time_offset():
    """Return the current persisted time offset (days)."""
    return {"time_offset_days": settings.time_offset_days}


from pydantic import BaseModel as _BaseModel

class _TimeOffset(_BaseModel):
    time_offset_days: int


@router.post('/time-offset')
async def set_time_offset(payload: _TimeOffset, db: AsyncSession = Depends(get_db)):
    """Set the persisted time offset (in days) and reload settings in this process."""
    try:
        from app.settings_manager import update_setting

        await update_setting(db, 'time_offset_days', str(int(payload.time_offset_days)))
        await db.commit()
        return {"ok": True, "time_offset_days": payload.time_offset_days}
    except Exception as e:
        log.error('set_time_offset failed: %s', e)
        raise HTTPException(500, f"Failed to set time offset: {e}")


# Email webhook configuration --------------------------------------------------
@router.get("/email-webhook")
async def get_email_webhook_config(db: AsyncSession = Depends(get_db)):
    """Return the webhook settings used for email limit / token events."""
    return await get_email_event_webhook_config(db)


@router.post("/email-webhook")
async def set_email_webhook_config(
    payload: EmailWebhookConfig,
    db: AsyncSession = Depends(get_db),
):
    try:
        await save_email_event_webhook_config(db, payload.webhook_url, payload.webhook_token)
        await db.commit()
        return {"ok": True, **(await get_email_event_webhook_config(db))}
    except Exception as e:
        log.error("set_email_webhook_config failed: %s", e)
        raise HTTPException(500, f"Failed to update email webhook config: {e}")


@router.post("/email-webhook/test")
async def test_email_webhook(db: AsyncSession = Depends(get_db)):
    """Trigger a test event to the configured email-events webhook."""
    try:
        await maybe_fire_email_event(db, "test", {"reason": "manual"})
        return {"ok": True}
    except Exception as e:
        log.error("test_email_webhook failed: %s", e)
        raise HTTPException(500, f"Failed to fire test webhook: {e}")


# Lead-reply webhook configuration --------------------------------------------

@router.get("/lead-reply-webhook")
async def get_lead_reply_webhook_endpoint(db: AsyncSession = Depends(get_db)):
    """Return the lead-reply webhook configuration."""
    return await get_lead_reply_webhook_config(db)


@router.post("/lead-reply-webhook")
async def set_lead_reply_webhook_config(
    payload: EmailWebhookConfig,
    db: AsyncSession = Depends(get_db),
):
    """Save the dedicated lead-reply webhook URL and optional bearer token."""
    try:
        await save_lead_reply_webhook_config(db, payload.webhook_url, payload.webhook_token)
        await db.commit()
        return {"ok": True, **(await get_lead_reply_webhook_config(db))}
    except Exception as e:
        log.error("set_lead_reply_webhook_config failed: %s", e)
        raise HTTPException(500, f"Failed to update lead reply webhook config: {e}")


@router.post("/lead-reply-webhook/test")
async def test_lead_reply_webhook(db: AsyncSession = Depends(get_db)):
    """Trigger a test event to the configured lead-reply webhook."""
    try:
        import datetime as _dt
        await fire_lead_reply_webhook(
            db,
            {
                "lead_email": "test@example.com",
                "lead_id": 0,
                "lead_name": "Test Lead",
                "thread_id": "test_thread",
                "inbox_id": 0,
                "inbox_email": "inbox@example.com",
                "message_id": "test_message",
                "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
            },
        )
        return {"ok": True}
    except Exception as e:
        log.error("test_lead_reply_webhook failed: %s", e)
        raise HTTPException(500, f"Failed to fire test webhook: {e}")

# Test mode ---------------------------------------------------------------

@router.get("/test-mode")
async def get_test_mode_endpoint(db: AsyncSession = Depends(get_db)):
    """Return whether test mode is currently enabled."""
    enabled = await get_test_mode(db)
    return {"test_mode": enabled}


class _TestModePayload(_BaseModel):
    test_mode: bool


@router.post("/test-mode")
async def set_test_mode_endpoint(
    payload: _TestModePayload,
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable test mode from the settings UI or API."""
    try:
        await set_test_mode(db, payload.test_mode)
        await db.commit()
        return {"ok": True, "test_mode": payload.test_mode}
    except Exception as e:
        log.error("set_test_mode failed: %s", e)
        raise HTTPException(500, f"Failed to update test mode: {e}")


# Survey tools / utility endpoints ----------------------------------------


@router.post("/add-opens")
async def add_opens_to_all_sent(db: AsyncSession = Depends(get_db)):
    """Insert a synthetic open event on every row in ``email_log``.

    This mirrors the ad‑hoc script used during development and is exposed
    behind the settings UI so that users can quickly mark all sent emails as
    opened.  An IPv4 placeholder is stored for each new ``EmailOpen`` row.
    """
    try:
        result = await db.execute(select(EmailLog))
        logs = result.scalars().all()
        count = 0
        for log in logs:
            op = EmailOpen(email_log_id=log.id, ip_address="127.0.0.1")
            db.add(op)
            if not log.opened:
                log.opened = True
            count += 1
        await db.commit()
        return {"added": count, "total": len(logs)}
    except Exception as e:
        log.error("add_opens_to_all_sent failed: %s", e)
        raise HTTPException(500, f"Failed to add opens: {e}")


# Scheduling strategy ---------------------------------------------------------

@router.get("/scheduling-strategy")
async def get_scheduling_strategy_endpoint(db: AsyncSession = Depends(get_db)):
    """Return the active scheduling strategy ('priority' or 'round_robin')."""
    strategy = await get_scheduling_strategy(db)
    return {"scheduling_strategy": strategy}


class _SchedulingStrategyPayload(_BaseModel):
    scheduling_strategy: str  # 'priority' | 'round_robin'


@router.post("/scheduling-strategy")
async def set_scheduling_strategy_endpoint(
    payload: _SchedulingStrategyPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Set the scheduling strategy used by *Recalculate All*.

    Changing the strategy affects the way ``Recalculate All`` distributes
    leads across campaigns.  Historically we would perform a full
    recalculation synchronously, but this can take several seconds when the
    database is large and it is unnecessary to block the request.  Instead
    we now schedule the work in a background task and return immediately.

    The frontend should warn the user if there are already leads enrolled in
    any campaign prior to making this request; it can check by calling
    ``GET /api/campaigns/has-leads`` and show a confirmation dialog.

    - **priority** – campaigns are processed in ascending ``priority`` order;
      use ``POST /api/campaigns/reorder`` to define that order.
    - **round_robin** – inbox capacity is divided evenly across all active
      campaigns and leads are scheduled in interleaved batches.
    """
    try:
        await set_scheduling_strategy(db, payload.scheduling_strategy)
        # commit before kicking off background work so that any subsequent
        # recalculation sees the new value.
        await db.commit()

        # schedule recalculation by POSTing to the public endpoint
        # `/api/schedule/recalculate-all`.  This mirrors the startup logic in
        # ``app.main`` and ensures any middleware or side effects run through
        # the normal request machinery.  We do not wait for the request to
        # complete before returning to the caller.
        from httpx import AsyncClient, ASGITransport
        from app.main import app

        async def _bg_recalc():
            try:
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post("/api/schedule/recalculate-all")
                    resp.raise_for_status()
            except Exception:
                log.exception("background recalculation via endpoint failed")

        background_tasks.add_task(_bg_recalc)

        return {"ok": True, "scheduling_strategy": payload.scheduling_strategy}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.error("set_scheduling_strategy failed: %s", e)
        raise HTTPException(500, f"Failed to update scheduling strategy: {e}")
