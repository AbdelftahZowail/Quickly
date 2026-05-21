"""Inbox API routes."""
# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import logging
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, exists, update as sa_update

from app.database import get_db
from app.models import Inbox, CampaignInbox, QueueSlot, EmailLog, CampaignLead
from app.schemas import (
    BeaconConnectFromInboxRequest,
    BeaconConnectRequest,
    BeaconPendingRegistrationCountResponse,
    ConnectUrlResponse,
    InboxCreate,
    InboxUpdate,
    InboxResponse,
    PauseInboxRequest,
)
from app.queue_logic import compute_effective_daily_limit
from app.settings_manager import settings as app_settings
from app.time import utcnow

log = logging.getLogger("quickly.routes")

router = APIRouter(prefix="/api/inboxes", tags=["inboxes"])


def _parse_beacon_setup_url(raw: str) -> tuple[str, str]:
    """Return (beacon_base_url, setup_token) from a Beacon setup URL."""
    p = urlparse(raw.strip())
    if p.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    if not p.netloc:
        raise ValueError("invalid URL")
    host = p.netloc.split("@")[-1]
    base = f"{p.scheme}://{host}".rstrip("/")
    token = (parse_qs(p.query).get("token") or [None])[0]
    if not token:
        raise ValueError("missing token query parameter")
    return base, token


async def _apply_beacon_connection(
    db: AsyncSession,
    inbox: Inbox,
    base: str,
    setup_token: str,
) -> Inbox:
    """Call Beacon connect API and persist fields on *inbox* (target row)."""
    base = base.strip().rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        raise HTTPException(422, "Invalid Beacon base URL")
    if not setup_token:
        raise HTTPException(422, "Missing Beacon setup token")

    webhook_secret = secrets.token_urlsafe(32)
    quickly_base = (app_settings.base_url or "").strip().rstrip("/")
    if not quickly_base.startswith("http"):
        raise HTTPException(500, "BASE_URL is not configured")

    connect_url = f"{base}/api/v1/connect"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                connect_url,
                json={
                    "quickly_base_url": quickly_base,
                    "webhook_secret": webhook_secret,
                    "inbox_id": inbox.id,
                },
                headers={"Authorization": f"Bearer {setup_token}"},
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    502,
                    f"Beacon connect failed: HTTP {resp.status_code} {resp.text[:200]}",
                )
            probe = await client.get(f"{base}/api/tracking-probe")
            if probe.status_code != 200 or not probe.json().get("ok"):
                raise HTTPException(502, "Beacon is reachable but tracking probe failed")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Could not reach Beacon: {e}") from e

    inbox.beacon_base_url = base
    inbox.beacon_setup_token = setup_token
    inbox.beacon_webhook_secret = webhook_secret
    inbox.beacon_connected = True
    inbox.tracking_domain = None
    await db.flush()
    await db.refresh(inbox)
    inbox.sent_today = 0
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    await _maybe_complete_ramp_up(inbox, db)
    await db.commit()
    log.info("beacon_connect applied: inbox_id=%s base=%s", inbox.id, base)

    from app.beacon_sync import sync_inbox_tracking_to_beacon

    try:
        n = await sync_inbox_tracking_to_beacon(db, inbox.id)
        if n:
            log.info("beacon_connect: synced %s prior tracking row(s) for inbox_id=%s", n, inbox.id)
    except Exception:
        log.exception(
            "beacon_connect: historical sync failed for inbox_id=%s (inbox is connected; retry by disconnect/reconnect or resend)",
            inbox.id,
        )

    return inbox


def _normalise_tracking_domain(raw: str | None) -> str:
    """Strip scheme, trailing slashes and paths from a user-supplied tracking domain.
    Returns a clean hostname string, or "" if the input is blank.
    """
    if not raw:
        return ""
    d = raw.strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    # keep only the hostname part
    d = d.split("/")[0].split("?")[0]
    return d


def _compute_effective_limit(inbox: Inbox) -> int:
    """Thin wrapper kept for backward compatibility; delegates to the shared implementation."""
    return compute_effective_daily_limit(inbox)


async def _maybe_complete_ramp_up(inbox: Inbox, db: AsyncSession) -> None:
    """Automatically disable ramp-up once the effective limit reaches max.

    Called after computing the effective limit so the inbox record in the
    database reflects the completed state.
    """
    if not getattr(inbox, "ramp_up_enabled", False):
        return
    if _compute_effective_limit(inbox) >= inbox.max_emails_per_day:
        inbox.ramp_up_enabled = False
        await db.flush()


@router.get("", response_model=list[InboxResponse])
async def list_inboxes(db: AsyncSession = Depends(get_db)):
    # fetch all inboxes first
    result = await db.execute(select(Inbox).order_by(Inbox.id))
    inboxes = result.scalars().all()

    # compute how many emails have been sent today per inbox by grouping
    from sqlalchemy import func

    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)

    # count logs per inbox in period
    count_res = await db.execute(
        select(EmailLog.inbox_id, func.count(EmailLog.id))
        .where(EmailLog.sent_at >= start, EmailLog.sent_at < end)
        .group_by(EmailLog.inbox_id)
    )
    counts = {row[0]: row[1] for row in count_res.all()}

    # count pending future queue slots per inbox
    now = datetime.utcnow()
    pending_res = await db.execute(
        select(QueueSlot.inbox_id, func.count(QueueSlot.id))
        .where(QueueSlot.scheduled_date > now)
        .group_by(QueueSlot.inbox_id)
    )
    pending_counts = {row[0]: row[1] for row in pending_res.all()}

    for i in inboxes:
        i.sent_today = counts.get(i.id, 0)
        i.pending_leads = pending_counts.get(i.id, 0)
        i.effective_max_per_day = _compute_effective_limit(i)
        await _maybe_complete_ramp_up(i, db)
    return inboxes


@router.post("", response_model=InboxResponse)
async def create_inbox(data: InboxCreate, db: AsyncSession = Depends(get_db)):
    # Normalise tracking domain: strip scheme, paths, whitespace
    td = _normalise_tracking_domain(data.tracking_domain)
    inbox = Inbox(
        email=data.email,
        display_name=data.display_name,
        max_emails_per_day=data.max_emails_per_day,
        wait_minutes_between=data.wait_minutes_between,
        max_jitter_seconds=data.max_jitter_seconds,
        provider=data.provider,
        tracking_domain=td or None,
        ramp_up_enabled=data.ramp_up_enabled,
        ramp_up_period_days=data.ramp_up_period_days,
        ramp_up_start=data.ramp_up_start,
        ramp_up_step_size=data.ramp_up_step_size,
        ramp_up_started_at=datetime.utcnow() if data.ramp_up_enabled else None,
    )
    db.add(inbox)
    await db.flush()
    await db.refresh(inbox)
    inbox.sent_today = 0
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    await _maybe_complete_ramp_up(inbox, db)
    return inbox


@router.get("/{inbox_id}", response_model=InboxResponse)
async def get_inbox(inbox_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")

    # attach today's sent count as above
    from sqlalchemy import func
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)
    count_res = await db.execute(
        select(func.count(EmailLog.id))
        .where(
            EmailLog.inbox_id == inbox_id,
            EmailLog.sent_at >= start,
            EmailLog.sent_at < end,
        )
    )
    inbox.sent_today = count_res.scalar() or 0
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    await _maybe_complete_ramp_up(inbox, db)
    return inbox


@router.get(
    "/{inbox_id}/beacon/pending-registration-count",
    response_model=BeaconPendingRegistrationCountResponse,
)
async def beacon_pending_registration_count(
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Count of tracking registrations that would sync to Beacon (development only)."""
    if os.environ.get("QUICKLY_MODE", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    from app.beacon_sync import collect_inbox_beacon_items

    items = await collect_inbox_beacon_items(db, inbox_id)
    return BeaconPendingRegistrationCountResponse(count=len(items))


@router.patch("/{inbox_id}", response_model=InboxResponse)
async def update_inbox(
    inbox_id: int,
    data: InboxUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    
    capacity_changed = False
    if data.display_name is not None:
        inbox.display_name = data.display_name
    if data.max_emails_per_day is not None:
        inbox.max_emails_per_day = data.max_emails_per_day
        capacity_changed = True
    if data.wait_minutes_between is not None:
        inbox.wait_minutes_between = data.wait_minutes_between
        capacity_changed = True
    if data.max_jitter_seconds is not None:
        inbox.max_jitter_seconds = data.max_jitter_seconds
        capacity_changed = True
    if data.provider is not None:
        inbox.provider = data.provider
    if data.tracking_domain is not None:
        inbox.tracking_domain = _normalise_tracking_domain(data.tracking_domain) or None
    if data.ramp_up_enabled is not None:
        was_enabled = inbox.ramp_up_enabled
        if data.ramp_up_enabled != inbox.ramp_up_enabled:
            capacity_changed = True
        inbox.ramp_up_enabled = data.ramp_up_enabled
        # Only reset the ramp-up clock when turning it on (not on every save while enabled)
        if data.ramp_up_enabled and not was_enabled:
            inbox.ramp_up_started_at = datetime.utcnow()
    if data.ramp_up_period_days is not None:
        if data.ramp_up_period_days != inbox.ramp_up_period_days:
            capacity_changed = True
        inbox.ramp_up_period_days = data.ramp_up_period_days
    if data.ramp_up_start is not None:
        if data.ramp_up_start != inbox.ramp_up_start:
            capacity_changed = True
            # Reset the clock when the starting number changes
            if inbox.ramp_up_enabled:
                inbox.ramp_up_started_at = datetime.utcnow()
        inbox.ramp_up_start = data.ramp_up_start
    if data.ramp_up_step_size is not None:
        if data.ramp_up_step_size != inbox.ramp_up_step_size:
            capacity_changed = True
            # Reset the clock when the step size changes
            if inbox.ramp_up_enabled:
                inbox.ramp_up_started_at = datetime.utcnow()
        inbox.ramp_up_step_size = data.ramp_up_step_size
    if data.paused is not None:
        if data.paused and not inbox.paused:
            # Pausing — freeze the ramp-up clock
            inbox.ramp_up_paused_at = datetime.utcnow()
        elif not data.paused and inbox.paused:
            # Unpausing — shift ramp_up_started_at forward by pause duration
            if inbox.ramp_up_paused_at is not None:
                paused_dur = datetime.utcnow() - inbox.ramp_up_paused_at
                if inbox.ramp_up_enabled and inbox.ramp_up_started_at is not None:
                    inbox.ramp_up_started_at += paused_dur
            inbox.ramp_up_paused_at = None
        inbox.paused = data.paused
    await db.flush()
    
    # If capacity or timing changed, recalculate queue globally rather than
    # just per-campaign.  A full recalculation will rebalance across campaigns
    # and is easier to reason about; the existing per-campaign loop worked but
    # became redundant after we added global recalc support.
    if capacity_changed:
        from app.models import CampaignInbox, CampaignLead
        campaign_result = await db.execute(
            select(CampaignInbox.campaign_id)
            .where(CampaignInbox.inbox_id == inbox_id)
            .distinct()
        )
        campaign_ids = [cid for (cid,) in campaign_result.all()]
        log.info("Inbox %s capacity changed; campaigns touched %s", inbox_id, campaign_ids)
        await db.commit()
        from app.routers.schedule import enqueue_global_recalculate

        enqueue_global_recalculate(background_tasks)
    await db.refresh(inbox)
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    await _maybe_complete_ramp_up(inbox, db)
    await db.commit()
    return inbox


@router.post("/{inbox_id}/beacon/connect", response_model=InboxResponse)
async def beacon_connect(
    inbox_id: int,
    body: BeaconConnectRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register this Quickly inbox with a Beacon instance using its setup URL."""
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    try:
        base, setup_token = _parse_beacon_setup_url(body.setup_url)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    return await _apply_beacon_connection(db, inbox, base, setup_token)


@router.post("/{inbox_id}/beacon/connect-from", response_model=InboxResponse)
async def beacon_connect_from_inbox(
    inbox_id: int,
    body: BeaconConnectFromInboxRequest,
    db: AsyncSession = Depends(get_db),
):
    """Connect this inbox to the same Beacon as *source_inbox_id* without pasting the setup URL."""
    if inbox_id == body.source_inbox_id:
        raise HTTPException(422, "source_inbox_id must be a different inbox")

    tgt = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = tgt.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")

    src = await db.execute(select(Inbox).where(Inbox.id == body.source_inbox_id))
    source = src.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Source inbox not found")
    if not getattr(source, "beacon_connected", False):
        raise HTTPException(422, "Source inbox is not connected to Beacon")
    base = (getattr(source, "beacon_base_url", None) or "").strip().rstrip("/")
    setup_token = getattr(source, "beacon_setup_token", None)
    if not base or not setup_token:
        raise HTTPException(422, "Source inbox is missing Beacon credentials")

    return await _apply_beacon_connection(db, inbox, base, setup_token)


@router.post("/{inbox_id}/beacon/disconnect", response_model=InboxResponse)
async def beacon_disconnect(inbox_id: int, db: AsyncSession = Depends(get_db)):
    """Clear Beacon configuration for this inbox."""
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    base = (inbox.beacon_base_url or "").strip().rstrip("/")
    setup = inbox.beacon_setup_token
    if inbox.beacon_connected and base and setup:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{base}/api/v1/disconnect",
                    json={"inbox_id": inbox_id},
                    headers={"Authorization": f"Bearer {setup}"},
                )
                if resp.status_code >= 400:
                    log.warning(
                        "beacon_disconnect: Beacon returned HTTP %s for inbox_id=%s",
                        resp.status_code,
                        inbox_id,
                    )
        except httpx.RequestError as e:
            log.warning("beacon_disconnect: Beacon unreachable for inbox_id=%s: %s", inbox_id, e)
    inbox.beacon_base_url = None
    inbox.beacon_setup_token = None
    inbox.beacon_webhook_secret = None
    inbox.beacon_connected = False
    await db.flush()
    await db.refresh(inbox)
    inbox.sent_today = 0
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    await _maybe_complete_ramp_up(inbox, db)
    await db.commit()
    return inbox


@router.post("/{inbox_id}/pause", response_model=InboxResponse)
async def pause_inbox(
    inbox_id: int,
    body: PauseInboxRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Pause an inbox. Choose what happens to leads currently assigned to it:
    - action='pause_leads': set sending_paused=True on all affected CampaignLeads.
    - action='reassign': pause and run a full recalculation so remaining inboxes
      automatically absorb the leads.
    """
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    if inbox.paused:
        raise HTTPException(400, "Inbox is already paused")
    if body.action not in ("pause_leads", "reassign"):
        raise HTTPException(400, "action must be 'pause_leads' or 'reassign'")

    now = datetime.utcnow()

    # Mark inbox as paused first so downstream logic (recalc) excludes it
    inbox.paused = True
    inbox.ramp_up_paused_at = now  # freeze warm-up clock
    await db.flush()

    if body.action == "pause_leads":
        # Find all CampaignLead IDs that have future queue slots on this inbox
        cl_id_rows = await db.execute(
            select(QueueSlot.campaign_lead_id)
            .where(QueueSlot.inbox_id == inbox_id, QueueSlot.scheduled_date > now)
            .distinct()
        )
        cl_ids = [r[0] for r in cl_id_rows.all()]
        if cl_ids:
            await db.execute(
                sa_update(CampaignLead)
                .where(CampaignLead.id.in_(cl_ids))
                .values(sending_paused=True)
            )
        # Remove the now-orphaned future slots so they don't appear in the schedule
        from sqlalchemy import delete as sa_delete
        await db.execute(
            sa_delete(QueueSlot)
            .where(QueueSlot.inbox_id == inbox_id, QueueSlot.scheduled_date > now)
        )
        log.info("pause_inbox: inbox=%s paused %d leads and removed their queue slots", inbox_id, len(cl_ids))

    elif body.action == "reassign":
        # Full recalculation: the scheduler rebuilds slots across all active
        # inboxes, automatically excluding the now-paused one.
        await db.commit()
        from app.routers.schedule import enqueue_global_recalculate

        enqueue_global_recalculate(background_tasks)
        log.info("pause_inbox: inbox=%s slots redistributed via recalculation (queued)", inbox_id)

    await db.refresh(inbox)
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    inbox.sent_today = 0
    await db.commit()
    return inbox


@router.post("/{inbox_id}/unpause", response_model=InboxResponse)
async def unpause_inbox(
    inbox_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Resume a paused inbox.

    Also un-pauses any CampaignLeads that were paused because of this inbox
    (i.e. leads in campaigns using this inbox that have sending_paused=True)
    and triggers a full queue recalculation so they get new slots.
    """
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")

    # Un-freeze warm-up: shift start date forward by the pause duration
    if inbox.ramp_up_enabled and inbox.ramp_up_paused_at is not None:
        paused_dur = datetime.utcnow() - inbox.ramp_up_paused_at
        if inbox.ramp_up_started_at is not None:
            inbox.ramp_up_started_at += paused_dur
    inbox.ramp_up_paused_at = None

    inbox.paused = False
    await db.flush()

    # Un-pause leads that belong to campaigns using this inbox
    campaign_id_rows = await db.execute(
        select(CampaignInbox.campaign_id).where(CampaignInbox.inbox_id == inbox_id)
    )
    campaign_ids = [r[0] for r in campaign_id_rows.all()]
    if campaign_ids:
        resumed = await db.execute(
            sa_update(CampaignLead)
            .where(
                CampaignLead.campaign_id.in_(campaign_ids),
                CampaignLead.sending_paused == True,  # noqa: E712
            )
            .values(sending_paused=False)
        )
        log.info(
            "unpause_inbox: inbox=%s resumed %s leads across campaigns %s",
            inbox_id, resumed.rowcount, campaign_ids,
        )

    # Rebuild queue slots for the now-active inbox
    await db.commit()
    from app.routers.schedule import enqueue_global_recalculate

    enqueue_global_recalculate(background_tasks)

    await db.refresh(inbox)
    inbox.effective_max_per_day = _compute_effective_limit(inbox)
    inbox.sent_today = 0
    log.info("unpause_inbox: inbox=%s resumed and queue recalculated", inbox_id)
    await db.commit()
    return inbox


@router.delete("/{inbox_id}")
async def delete_inbox(inbox_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    # Remove campaign assignments referencing this inbox
    await db.execute(
        CampaignInbox.__table__.delete().where(CampaignInbox.inbox_id == inbox_id)
    )
    # Nullify inbox_id on email logs (preserve logs, break FK constraint)
    await db.execute(
        EmailLog.__table__.update().where(EmailLog.inbox_id == inbox_id).values(inbox_id=None)
    )
    await db.delete(inbox)
    await db.flush()
    log.info("delete_inbox: deleted inbox %s (%s)", inbox_id, inbox.email)
    return {"ok": True}


@router.post("/{inbox_id}/generate-connect-url", response_model=ConnectUrlResponse)
async def generate_connect_url(
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Generate a one-time URL to pre-authenticate the OAuth flow for this inbox.

    Returns a URL like ``https://yourdomain.com/oauth/connect/<token>`` that can be
    opened in any browser (including one where the user is *not* logged into Quickly)
    to kick off the OAuth consent flow for the inbox's configured provider.
    """
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    if inbox.provider not in ("gmail", "office365"):
        raise HTTPException(400, f"Cannot generate connect URL for provider '{inbox.provider}'")

    token = secrets.token_urlsafe(32)
    inbox.connect_token = token
    inbox.connect_token_expires_at = utcnow() + timedelta(minutes=15)
    await db.flush()

    base = app_settings.base_url.rstrip("/")
    url = f"{base}/oauth/connect/{token}"
    log.info("generate_connect_url: inbox %s token=%s…", inbox_id, token[:12])
    return ConnectUrlResponse(url=url)
