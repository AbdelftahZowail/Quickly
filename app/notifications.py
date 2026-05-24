"""Notification system — creates persistent in-app notifications and optionally
sends human-readable emails as an alternative to webhooks.

Each user can opt-in to email notifications for any subset of the standard
webhook event types.  Notifications are sent from the user's own OAuth-connected
email account (the one used for app login), NOT from campaign inboxes.

Rate limiting is per-user, per-hour (configurable).
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
import email.policy
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailNotificationConfig, Notification, User
from app import time as time_provider
from app.settings_manager import settings

log = logging.getLogger("quickly.notifications")


# ---------------------------------------------------------------------------
# Build structured notification from event data
# ---------------------------------------------------------------------------

def _deep_link(path: str) -> str:
    """Return an absolute URL for a given app path."""
    base = settings.base_url or ""
    return f"{base.rstrip('/')}{path}"


def build_notification(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Return a dict with ``title``, ``message``, and optional FKs for a notification.

    The dict has keys: title, message, lead_id, campaign_id, inbox_id.
    """
    ts = data.get("timestamp", time_provider.utcnow().isoformat() + "Z")
    lead_id = data.get("lead_id") or None
    campaign_id = data.get("campaign_id") or None
    inbox_id = data.get("inbox_id") or None

    if event_type == "email.sent":
        lead_email = data.get("lead_email", "a lead")
        title = f"Email sent to {lead_email}"
        campaign = data.get("campaign_name") or f"Campaign #{campaign_id}" if campaign_id else "a campaign"
        message = (
            f"An email was successfully sent to **{lead_email}** "
            f"in **{campaign}**."
        )
    elif event_type == "email.opened":
        title = f"Email opened by lead #{lead_id or '?'}"
        message = "A lead opened your email."
    elif event_type == "email.clicked":
        title = f"Link clicked by lead #{lead_id or '?'}"
        url = data.get("original_url", "—")
        message = f"A lead clicked a link: {url}"
    elif event_type == "email.bounced":
        lead_email = data.get("lead_email", "unknown")
        title = f"Email bounced — {lead_email}"
        message = (
            f"An email to **{lead_email}** bounced and could not be delivered."
        )
    elif event_type == "lead.replied":
        lead_email = data.get("lead_email", "a lead")
        lead_name = data.get("lead_name", "")
        display = f"{lead_name} ({lead_email})" if lead_name else lead_email
        title = f"Reply received from {display}"
        message = f"**{display}** replied to your campaign email."
    elif event_type == "lead.unsubscribed":
        lead_email = data.get("lead_email", "unknown")
        title = f"Lead unsubscribed — {lead_email}"
        message = f"**{lead_email}** clicked the unsubscribe link."
    elif event_type == "lead.status_changed":
        lead_email = data.get("lead_email", "?")
        old_s = data.get("old_status", "—")
        new_s = data.get("new_status", "—")
        title = f"Lead status changed — {lead_email}"
        message = f"**{lead_email}** status changed from *{old_s}* to *{new_s}*."
    elif event_type.startswith("lead."):
        label = event_type.replace("lead.", "").replace("_", " ").title()
        lead_email = data.get("lead_email", "?")
        title = f"Lead classified as {label} — {lead_email}"
        message = f"AI classified **{lead_email}**'s reply as *{label}*."
    elif event_type == "feature.error":
        feature_label = data.get("label", data.get("feature", "Unknown Feature"))
        error_msg = data.get("error", "Unknown error")
        title = f"Feature error — {feature_label}"
        message = f"A system feature encountered an error: {error_msg}"
    elif event_type == "daily_limit":
        inbox_email = data.get("inbox_email", "an inbox")
        title = f"Daily limit hit — {inbox_email}"
        message = f"**{inbox_email}** reached its daily sending limit."
    elif event_type == "rate_limit":
        inbox_email = data.get("inbox_email", "an inbox")
        title = f"Rate limit triggered — {inbox_email}"
        message = f"A rate limit was hit for **{inbox_email}**."
    elif event_type == "token_expired":
        inbox_email = data.get("inbox_email", "an inbox")
        title = f"OAuth token expired — {inbox_email}"
        message = (
            f"**{inbox_email}**'s OAuth token could not be refreshed. "
            f"Please reconnect the inbox."
        )
    else:
        title = f"Quickly notification — {event_type}"
        message = f"Event: {event_type} at {ts}"

    return {
        "title": title,
        "message": message,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "inbox_id": inbox_id,
    }


# ---------------------------------------------------------------------------
# Token refresh helpers (reuse existing provider-specific logic)
# ---------------------------------------------------------------------------

def _refresh_google_notif_token(user: User) -> bool:
    """Refresh the user's Google notification token.  Returns True on success."""
    data = urllib.parse.urlencode({
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": user.notif_refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read().decode())
            user.notif_access_token = token_data["access_token"]
            if token_data.get("refresh_token"):
                user.notif_refresh_token = token_data["refresh_token"]
            user.notif_token_expiry = time_provider.utcnow() + timedelta(
                seconds=token_data.get("expires_in", 3600)
            )
            return True
    except Exception as e:
        log.error("Failed to refresh Google notification token for user %s: %s", user.id, e)
        return False


def _refresh_microsoft_notif_token(user: User) -> bool:
    """Refresh the user's Microsoft notification token.  Returns True on success."""
    tenant_id = settings.office365_tenant_id or "common"
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": settings.office365_client_id,
        "client_secret": settings.office365_client_secret,
        "refresh_token": user.notif_refresh_token,
        "grant_type": "refresh_token",
        "scope": "openid email profile User.Read Mail.Send offline_access",
    }).encode()
    req = urllib.request.Request(
        token_url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read().decode())
            user.notif_access_token = token_data["access_token"]
            if token_data.get("refresh_token"):
                user.notif_refresh_token = token_data["refresh_token"]
            user.notif_token_expiry = time_provider.utcnow() + timedelta(
                seconds=token_data.get("expires_in", 3600)
            )
            return True
    except Exception as e:
        log.error("Failed to refresh Microsoft notification token for user %s: %s", user.id, e)
        return False


# ---------------------------------------------------------------------------
# Send a notification email via the user's own OAuth account
# ---------------------------------------------------------------------------

def _send_via_gmail(user: User, to: str, subject: str, body: str) -> bool:
    """Send a plain-text email via Gmail API using the user's notification token."""
    import base64
    msg = EmailMessage(policy=email.policy.SMTP)
    msg["To"] = to
    msg["From"] = user.email
    msg["Subject"] = subject
    msg.set_content(body, cte="quoted-printable")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = json.dumps({"raw": raw}).encode()

    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {user.notif_access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:
        log.error("Gmail notification send failed for user %s: %s", user.id, e)
        return False


def _send_via_microsoft(user: User, to: str, subject: str, body: str) -> bool:
    """Send a plain-text email via Microsoft Graph API using the user's notification token."""
    payload = json.dumps({
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": "false",
    }).encode()

    req = urllib.request.Request(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        data=payload,
        headers={
            "Authorization": f"Bearer {user.notif_access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 202)
    except Exception as e:
        log.error("Microsoft notification send failed for user %s: %s", user.id, e)
        return False


async def _send_notification_for_user(
    db: AsyncSession,
    user: User,
    config: EmailNotificationConfig,
    subject: str,
    body: str,
) -> bool:
    """Send a single notification email via the user's OAuth provider."""
    to = config.notification_email or user.email

    # Refresh token if near expiry
    if user.notif_token_expiry and user.notif_token_expiry <= time_provider.utcnow() + timedelta(minutes=5):
        if user.oauth_provider == "google":
            if not _refresh_google_notif_token(user):
                return False
        elif user.oauth_provider == "microsoft":
            if not _refresh_microsoft_notif_token(user):
                return False
        await db.flush()

    if not user.notif_access_token:
        log.warning("User %s has no notification access token — skipping", user.id)
        return False

    if user.oauth_provider == "google":
        return _send_via_gmail(user, to, subject, body)
    elif user.oauth_provider == "microsoft":
        return _send_via_microsoft(user, to, subject, body)
    else:
        log.warning("User %s has unsupported OAuth provider '%s'", user.id, user.oauth_provider)
        return False


# ---------------------------------------------------------------------------
# In-app notification persistence
# ---------------------------------------------------------------------------

async def create_in_app_notification(
    db: AsyncSession, user_id: int, event_type: str, data: dict[str, Any]
) -> Notification | None:
    """Create and persist a ``Notification`` row for the given user and event.

    Returns the created notification or ``None`` on failure (never raises).
    """
    try:
        info = build_notification(event_type, data)
        notif = Notification(
            user_id=user_id,
            event_type=event_type,
            title=info["title"],
            message=info["message"],
            data_json=data,
            lead_id=info.get("lead_id"),
            campaign_id=info.get("campaign_id"),
            inbox_id=info.get("inbox_id"),
        )
        db.add(notif)
        await db.flush()
        return notif
    except Exception:
        log.exception("Failed to create in-app notification for user=%s event=%s", user_id, event_type)
        return None


async def get_unread_notification_count(db: AsyncSession, user_id: int) -> int:
    """Return the number of unread notifications for a user."""
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
    )
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# Main entry point — called from fire_webhook_event
# ---------------------------------------------------------------------------

async def dispatch_notification(
    db: AsyncSession, event_type: str, data: dict[str, Any]
) -> None:
    """Create in-app notifications for all active users and optionally send emails.

    In-app notifications are always created for every active user.
    Email delivery is checked per-user via their EmailNotificationConfig (if one exists).
    Per-user event filtering and email rate limiting are applied.
    Failures are logged but never raised.
    """
    try:
        # Query all active users and their optional notification configs
        users_result = await db.execute(
            select(User).where(User.is_active == True)  # noqa: E712
        )
        users = users_result.scalars().all()

        if not users:
            return

        user_ids = [u.id for u in users]
        configs_result = await db.execute(
            select(EmailNotificationConfig).where(
                EmailNotificationConfig.user_id.in_(user_ids)
            )
        )
        config_map: dict[int, EmailNotificationConfig] = {
            c.user_id: c for c in configs_result.scalars().all()
        }

        now = time_provider.utcnow()

        for user in users:
            config = config_map.get(user.id)

            # In-app notification: always create (no event filtering for in-app)
            await create_in_app_notification(db, user.id, event_type, data)

            # Email channel: only if config exists, enabled, and event matches
            if config is None or not config.enabled:
                continue

            if config.events and event_type not in config.events:
                continue

            # Rate limiting for email only
            if config.rate_window_start is None or (now - config.rate_window_start).total_seconds() >= 3600:
                config.rate_window_start = now
                config.notifications_sent_this_hour = 0

            if config.notifications_sent_this_hour >= config.rate_limit_per_hour:
                log.debug(
                    "Rate limit reached for user %s (%d/%d this hour) — skipping email",
                    user.id, config.notifications_sent_this_hour, config.rate_limit_per_hour,
                )
                continue

            info = build_notification(event_type, data)
            body = info["message"] + (
                f"\n\n—\n"
                f"View in Quickly: {_deep_link('/notifications')}\n"
            )
            if info.get("lead_id"):
                body += f"View lead: {_deep_link(f'/leads/{info['lead_id']}')}\n"
            if info.get("campaign_id"):
                body += f"View campaign: {_deep_link(f'/campaigns/{info['campaign_id']}')}\n"

            success = await _send_notification_for_user(
                db, user, config, info["title"], body
            )

            if success:
                config.notifications_sent_this_hour += 1
                await db.flush()
    except Exception:
        log.exception("Unexpected error in dispatch_notification")
