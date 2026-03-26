"""Open-pixel tracking, click redirects, and Caddy on-demand TLS ask endpoint.

Routes
------
GET /o/{log_id}        — open tracking pixel (1×1 transparent GIF)
GET /c/{token}         — click-tracking redirect to original URL
GET /api/caddy/ask     — Caddy on_demand_tls domain approval gate

These routes work on any hostname (the app's own domain *or* a custom
tracking domain that CNAMEs here), so click/open tracking survives even
without a custom domain configured.
"""
from __future__ import annotations

import ipaddress
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.models import EmailLog, EmailOpen, EmailClick, TrackedLink, Inbox, Lead, LeadUnsubscribeToken, CampaignLead, QueueSlot, KnownIP
from app.tracking import PIXEL_GIF, is_domain_pending
from app import time as time_provider
from app.webhooks import fire_webhook_event

log = logging.getLogger("quickly.tracking")

router = APIRouter(tags=["tracking"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_ip(request: Request) -> str | None:
    """Extract the client IP from the request, respecting proxy headers."""
    return (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    ) or None


async def _is_known_ip(db: AsyncSession, ip: str | None) -> bool:
    """Return True if *ip* belongs to the app user (should be filtered)."""
    if not ip:
        return False
    now = time_provider.utcnow()
    result = await db.execute(
        select(KnownIP).where(
            KnownIP.ip_address == ip,
            # permanent entries never expire; session entries expire after expires_at
            (KnownIP.permanent == True) | (KnownIP.expires_at > now),  # noqa: E712
        )
    )
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Open-tracking pixel
# ---------------------------------------------------------------------------

@router.get("/o/{token}", include_in_schema=False)
async def open_pixel(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return a 1×1 transparent GIF and record the email open.

    The *token* is a random URL-safe string stored in ``EmailLog.open_token``.
    For backwards compatibility with older emails that used integer log IDs,
    we fall back to an integer lookup when the token doesn't match.
    """
    # Try token-based lookup first, then fall back to integer ID for old emails
    result = await db.execute(select(EmailLog).where(EmailLog.open_token == token))
    email_log = result.scalar_one_or_none()
    if email_log is None:
        try:
            log_id = int(token)
            result = await db.execute(select(EmailLog).where(EmailLog.id == log_id))
            email_log = result.scalar_one_or_none()
        except (ValueError, TypeError):
            pass

    if email_log:
        ip = _extract_ip(request)

        # Skip recording if this IP belongs to the app user
        if await _is_known_ip(db, ip):
            log.debug("open_pixel: skipping known IP %s for log_id=%s", ip, email_log.id)
            return Response(
                content=PIXEL_GIF,
                media_type="image/gif",
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )

        if not email_log.opened:
            email_log.opened = True

        db.add(
            EmailOpen(
                email_log_id=email_log.id,
                ip_address=ip,
                opened_at=time_provider.utcnow(),
            )
        )
        await db.commit()

        # Fire webhook event for email open
        await fire_webhook_event(db, "email.opened", {
            "email_log_id": email_log.id,
            "lead_id": email_log.lead_id,
            "campaign_id": email_log.campaign_id,
            "ip_address": ip,
            "timestamp": time_provider.utcnow().isoformat() + "Z",
        })

    return Response(
        content=PIXEL_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# ---------------------------------------------------------------------------
# Click-tracking redirect
# ---------------------------------------------------------------------------

@router.get("/c/{token}", include_in_schema=False)
async def click_redirect(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Record a link click and 302-redirect the recipient to the original URL."""
    result = await db.execute(
        select(TrackedLink).where(TrackedLink.token == token)
    )
    tracked = result.scalar_one_or_none()

    if not tracked:
        return Response(status_code=404, content="Not found")

    email_log_result = await db.execute(
        select(EmailLog).where(EmailLog.id == tracked.email_log_id)
    )
    email_log = email_log_result.scalar_one_or_none()

    if email_log:
        ip = _extract_ip(request)

        # Skip recording if this IP belongs to the app user
        if not await _is_known_ip(db, ip):
            if not email_log.clicked:
                email_log.clicked = True

            db.add(
                EmailClick(
                    email_log_id=tracked.email_log_id,
                    ip_address=ip,
                    clicked_at=time_provider.utcnow(),
                )
            )
            await db.commit()

            # Fire webhook event for email click
            await fire_webhook_event(db, "email.clicked", {
                "email_log_id": tracked.email_log_id,
                "lead_id": email_log.lead_id,
                "campaign_id": email_log.campaign_id,
                "original_url": tracked.original_url,
                "ip_address": ip,
                "timestamp": time_provider.utcnow().isoformat() + "Z",
            })
        else:
            log.debug("click_redirect: skipping known IP %s for log_id=%s", ip, email_log.id)

    return RedirectResponse(url=tracked.original_url, status_code=302)


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------

_UNSUBSCRIBE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Unsubscribed</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           display: flex; align-items: center; justify-content: center;
           min-height: 100vh; margin: 0; background: #f9fafb; }
    .card { background: #fff; border-radius: 12px; padding: 48px 40px;
            box-shadow: 0 4px 24px rgba(0,0,0,.08); text-align: center; max-width: 420px; }
    .icon { font-size: 48px; margin-bottom: 16px; }
    h1 { margin: 0 0 8px; font-size: 24px; color: #111; }
    p  { color: #555; margin: 0; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✓</div>
    <h1>You've been unsubscribed</h1>
    <p>You will no longer receive emails from us.<br>
       If this was a mistake please contact the sender directly.</p>
  </div>
</body>
</html>"""

_ALREADY_UNSUBSCRIBED_HTML = _UNSUBSCRIBE_HTML.replace(
    "You've been unsubscribed",
    "Already unsubscribed",
).replace(
    "You will no longer receive emails from us.<br>If this was a mistake please contact the sender directly.",
    "You are already unsubscribed and will not receive any more emails from us.",
)


@router.get("/u/{token}", include_in_schema=False)
async def unsubscribe(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Process an unsubscribe request.

    Works both as a one-click server-side action (no browser needed – the
    email client just fires a POST/GET) and as a human-readable page when
    opened in a browser.  The token encodes the (lead, campaign) pair so we
    know exactly which campaign to stop.

    Actions:
      1. Mark this lead's **enrollment** in that campaign as ``unsubscribed``.
      2. Delete remaining ``QueueSlot`` rows for this lead+campaign so no
         further emails are dispatched.
    """
    result = await db.execute(
        select(LeadUnsubscribeToken).where(LeadUnsubscribeToken.token == token)
    )
    row = result.scalar_one_or_none()

    if not row:
        return Response(
            content="<p>Invalid or expired unsubscribe link.</p>",
            media_type="text/html",
            status_code=404,
        )

    lead_res = await db.execute(select(Lead).where(Lead.id == row.lead_id))
    lead = lead_res.scalar_one_or_none()

    already_done = False
    cl_res = await db.execute(
        select(CampaignLead).where(
            CampaignLead.lead_id == row.lead_id,
            CampaignLead.campaign_id == row.campaign_id,
        )
    )
    cl = cl_res.scalar_one_or_none()
    if not cl:
        already_done = True
    else:
        if cl.enrollment_status == "unsubscribed":
            already_done = True
        else:
            cl.enrollment_status = "unsubscribed"
            cl.interest_status = None
            log.info(
                "Unsubscribed lead_id=%s via campaign_id=%s token=%s",
                row.lead_id, row.campaign_id, token,
            )
        await db.execute(
            delete(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
        )

    await db.commit()

    # Fire webhook events for unsubscribe
    if not already_done and lead and cl:
        await fire_webhook_event(db, "lead.unsubscribed", {
            "lead_id": row.lead_id,
            "lead_email": lead.email if lead else "",
            "campaign_id": row.campaign_id,
            "timestamp": time_provider.utcnow().isoformat() + "Z",
        })
        await fire_webhook_event(db, "lead.status_changed", {
            "lead_id": row.lead_id,
            "lead_email": lead.email if lead else "",
            "campaign_id": row.campaign_id,
            "old_enrollment_status": "active",
            "new_enrollment_status": "unsubscribed",
            "timestamp": time_provider.utcnow().isoformat() + "Z",
        })

    html = _ALREADY_UNSUBSCRIBED_HTML if already_done else _UNSUBSCRIBE_HTML
    return Response(content=html, media_type="text/html")


# ---------------------------------------------------------------------------
# Caddy on_demand_tls ask endpoint
# ---------------------------------------------------------------------------

@router.get("/api/caddy/ask", include_in_schema=False)
async def caddy_ask(
    domain: str,
    db: AsyncSession = Depends(get_db),
):
    """Caddy on_demand_tls ask endpoint.

    Returns HTTP 200 to allow TLS certificate provisioning for *domain*, or
    HTTP 403 to deny.  Caddy calls this as:

        GET /api/caddy/ask?domain=<hostname>

    We approve ONLY domains that are explicitly saved as a custom tracking
    domain on an inbox record.  Everything else — including the app's own
    primary host, bare hostnames (e.g. ``localhost``), and IP addresses —
    is rejected so that Caddy never attempts to obtain a public certificate
    for something that would inevitably fail (and loop).

    The app's primary domain does not need on_demand_tls; Caddy's explicit
    site block handles that automatically.
    """
    # Reject bare hostnames (no dot) — they cannot receive public certs.
    # This covers "localhost", single-label internal names, etc.
    # These are expected/routine in dev; log at DEBUG only.
    if "." not in domain:
        log.debug("caddy/ask: skipped bare hostname domain=%s", domain)
        return Response(status_code=403, content="domain not allowed")

    # Reject IP addresses (v4 and v6) — ACME CAs do not issue certs for IPs
    # unless using specific IP SAN support, which Caddy's on_demand flow
    # does not use.  Also expected/routine; DEBUG only.
    try:
        ipaddress.ip_address(domain)
        log.debug("caddy/ask: skipped IP address domain=%s", domain)
        return Response(status_code=403, content="domain not allowed")
    except ValueError:
        pass  # not an IP — continue

    # Approve domains explicitly configured as a tracking domain on an inbox.
    result = await db.execute(
        select(Inbox).where(Inbox.tracking_domain == domain)
    )
    if result.scalar_one_or_none() is not None:
        log.info("caddy/ask: approved domain=%s (per-inbox tracking domain)", domain)
        return Response(status_code=200)

    # Also approve domains that are temporarily pending cert provisioning
    # (user clicked "Check" before saving the inbox — allow Caddy to get a cert
    # so the connection check can succeed; expires after 24 hours).
    if is_domain_pending(domain):
        log.info("caddy/ask: approved domain=%s (pending cert provisioning)", domain)
        return Response(status_code=200)

    # An unknown public-looking domain is worth a warning.
    log.warning("caddy/ask: rejected unknown domain=%s", domain)
    return Response(status_code=403, content="domain not allowed")


# ---------------------------------------------------------------------------
# Lightweight probe — confirms this server is reachable at a given domain
# ---------------------------------------------------------------------------

@router.get("/api/tracking-probe", include_in_schema=False)
async def tracking_probe():
    """Return a simple JSON OK.

    The backend verification endpoint fetches this URL via the *custom*
    domain to confirm that the CNAME resolves to this server.
    """
    return {"ok": True}
