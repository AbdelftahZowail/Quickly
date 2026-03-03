"""Settings API routes for managing configuration."""
import logging
import urllib.parse

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
)
from sqlalchemy import select
from app.models import EmailLog, EmailOpen, Webhook, WEBHOOK_EVENT_TYPES
from app.schemas import WebhookCreate, WebhookUpdate, WebhookResponse
from app.webhooks import fire_webhook_event

log = logging.getLogger("quickly.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])

class GmailSyncConfig(BaseModel):
    push_topic: str = ""
    webhook_token: str = ""
    sync_interval_minutes: int = 5


# ---------------------------------------------------------------------------
# Webhook CRUD
# ---------------------------------------------------------------------------

@router.get("/webhooks/events")
async def list_webhook_events():
    """Return the list of valid event types a webhook can subscribe to."""
    return {"events": list(WEBHOOK_EVENT_TYPES)}


@router.get("/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(db: AsyncSession = Depends(get_db)):
    """Return all configured webhooks ordered by creation date."""
    result = await db.execute(
        select(Webhook).order_by(Webhook.created_at.desc())
    )
    return result.scalars().all()


@router.post("/webhooks", response_model=WebhookResponse, status_code=201)
async def create_webhook(
    payload: WebhookCreate,
    db: AsyncSession = Depends(get_db),
):
    """Register a new outbound webhook endpoint."""
    invalid = [e for e in payload.events if e not in WEBHOOK_EVENT_TYPES]
    if invalid:
        raise HTTPException(400, f"Invalid event types: {invalid}")
    wh = Webhook(
        url=payload.url.strip(),
        secret=payload.secret,
        events=payload.events,
        active=payload.active,
        description=payload.description,
    )
    db.add(wh)
    await db.commit()
    await db.refresh(wh)
    return wh


@router.patch("/webhooks/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: int,
    payload: WebhookUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Partially update an existing webhook."""
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(404, "Webhook not found")
    if payload.events is not None:
        invalid = [e for e in payload.events if e not in WEBHOOK_EVENT_TYPES]
        if invalid:
            raise HTTPException(400, f"Invalid event types: {invalid}")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "url" and value is not None:
            value = value.strip()
        setattr(wh, field, value)
    await db.commit()
    await db.refresh(wh)
    return wh


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a webhook."""
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(404, "Webhook not found")
    await db.delete(wh)
    await db.commit()
    return {"ok": True}


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Fire a synthetic test event to a specific webhook."""
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(404, "Webhook not found")
    import datetime as _dt
    test_data = {
        "webhook_id": wh.id,
        "test": True,
        "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
    }
    # Fire directly to this one webhook regardless of its event subscriptions
    from app.webhooks import _post_webhook
    success = await _post_webhook(wh, "test", test_data)
    return {"ok": success}




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


# ---------------------------------------------------------------------------
# Server info (used by the frontend for DNS setup instructions)
# ---------------------------------------------------------------------------

@router.get("/server-info")
async def get_server_info():
    """Return the server's configured base URL and derived hostname.

    The frontend uses ``cname_target`` to tell users what hostname their
    custom tracking CNAME should point to.
    """
    base_url = settings.base_url.rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    # Strip port from netloc for use as a CNAME target
    cname_target = parsed.hostname or parsed.netloc or base_url
    return {
        "base_url": base_url,
        "cname_target": cname_target,
    }


# ---------------------------------------------------------------------------
# Verify a custom tracking domain is reachable and pointing here
# ---------------------------------------------------------------------------

@router.get("/verify-tracking-domain")
async def verify_tracking_domain(domain: str):
    """Probe a custom tracking domain to confirm it resolves to this server.

    Uses httpx to call ``https://<domain>/api/tracking-probe`` from the
    server side (no CORS restrictions).  Returns:

    * ``{"ok": true}``  — domain resolves and responds correctly
    * ``{"ok": false, "error": "<reason>"}``  — DNS/network/TLS failure
    """
    import httpx

    # Use removeprefix (not lstrip!) — lstrip strips individual characters,
    # so lstrip("https://") on "track.example.com" would eat the leading 't'.
    domain = domain.strip()
    for scheme in ("https://", "http://"):
        if domain.startswith(scheme):
            domain = domain[len(scheme):]
    domain = domain.rstrip("/")

    target = f"https://{domain}/api/tracking-probe"
    last_error: str = ""
    # Try twice: first with strict TLS verification, then without.
    # The second pass catches cases where Caddy is still provisioning the cert
    # or the caller is behind a private CA.
    for verify_ssl in (True, False):
        try:
            async with httpx.AsyncClient(
                timeout=10,
                follow_redirects=True,
                verify=verify_ssl,
            ) as client:
                resp = await client.get(target)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return {"ok": True}
                last_error = "Probe endpoint returned unexpected body"
            else:
                last_error = f"Probe returned HTTP {resp.status_code}"
            # Got a real HTTP response — no point retrying without SSL
            break
        except httpx.ConnectError as exc:
            last_error = f"Connection refused or DNS not resolving ({exc})"
        except httpx.TimeoutException:
            last_error = "Connection timed out (10 s)"
        except Exception as exc:
            last_error = str(exc)

    return {"ok": False, "error": last_error}
