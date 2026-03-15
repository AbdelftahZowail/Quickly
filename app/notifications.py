"""Email notification system — sends human-readable emails as an alternative to webhooks.

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailNotificationConfig, User
from app import time as time_provider
from app.settings_manager import settings

log = logging.getLogger("quickly.notifications")


# ---------------------------------------------------------------------------
# Human-readable email templates per event type
# ---------------------------------------------------------------------------

def format_notification_email(event_type: str, data: dict[str, Any]) -> tuple[str, str]:
    """Return ``(subject, body)`` for a notification email.

    The body is written as a normal, human-readable email — no JSON dumps.
    """
    ts = data.get("timestamp", time_provider.utcnow().isoformat() + "Z")

    if event_type == "email.sent":
        subject = f"Email sent to {data.get('lead_email', 'a lead')}"
        body = (
            f"An email was successfully sent.\n\n"
            f"To: {data.get('lead_email', '—')}\n"
            f"From: {data.get('inbox_email', '—')}\n"
            f"Subject: {data.get('subject', '—')}\n"
            f"Campaign ID: {data.get('campaign_id', '—')}\n"
            f"Sequence: #{data.get('sequence_index', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "email.opened":
        subject = f"Email opened by lead #{data.get('lead_id', '?')}"
        body = (
            f"A lead opened your email.\n\n"
            f"Lead ID: {data.get('lead_id', '—')}\n"
            f"Campaign ID: {data.get('campaign_id', '—')}\n"
            f"IP: {data.get('ip_address', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "email.clicked":
        subject = f"Link clicked by lead #{data.get('lead_id', '?')}"
        body = (
            f"A lead clicked a link in your email.\n\n"
            f"Lead ID: {data.get('lead_id', '—')}\n"
            f"Campaign ID: {data.get('campaign_id', '—')}\n"
            f"URL: {data.get('original_url', '—')}\n"
            f"IP: {data.get('ip_address', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "email.bounced":
        subject = f"Email bounced — {data.get('lead_email', 'unknown')}"
        body = (
            f"An email bounced and could not be delivered.\n\n"
            f"Lead: {data.get('lead_email', '—')}\n"
            f"Inbox: {data.get('inbox_id', '—')}\n"
            f"Error: {data.get('error_message', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "lead.replied":
        subject = f"Reply received from {data.get('lead_email', 'a lead')}"
        body = (
            f"Good news — a lead replied to your campaign email!\n\n"
            f"Lead: {data.get('lead_email', '—')} ({data.get('lead_name', '')})\n"
            f"Inbox: {data.get('inbox_email', '—')}\n"
            f"Thread: {data.get('thread_id', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "lead.unsubscribed":
        subject = f"Lead unsubscribed — {data.get('lead_email', 'unknown')}"
        body = (
            f"A lead clicked the unsubscribe link.\n\n"
            f"Lead: {data.get('lead_email', '—')}\n"
            f"Campaign ID: {data.get('campaign_id', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "lead.status_changed":
        subject = f"Lead status changed — {data.get('lead_email', '?')}"
        body = (
            f"A lead's status was updated.\n\n"
            f"Lead: {data.get('lead_email', '—')}\n"
            f"Old status: {data.get('old_status', '—')}\n"
            f"New status: {data.get('new_status', '—')}\n"
            f"Reason: {data.get('reason', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type.startswith("lead."):
        # AI classification events (interested, not_interested, out_of_office, etc.)
        label = event_type.replace("lead.", "").replace("_", " ").title()
        subject = f"Lead classified as {label} — {data.get('lead_email', '?')}"
        body = (
            f"AI classified a lead's reply.\n\n"
            f"Classification: {label}\n"
            f"Lead: {data.get('lead_email', '—')}\n"
            f"Campaign ID: {data.get('campaign_id', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "feature.error":
        feature_label = data.get("label", data.get("feature", "Unknown Feature"))
        error_msg = data.get("error", "Unknown error")
        subject = f"Feature error — {feature_label}"
        body = (
            f"A system feature encountered an error during operation.\n\n"
            f"Feature: {feature_label}\n"
            f"Error: {error_msg}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "daily_limit":
        subject = f"Daily limit hit — {data.get('inbox_email', 'an inbox')}"
        body = (
            f"An inbox reached its daily sending limit.\n\n"
            f"Inbox: {data.get('inbox_email', '—')}\n"
            f"Date: {data.get('date', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "rate_limit":
        subject = f"Rate limit triggered — {data.get('inbox_email', 'an inbox')}"
        body = (
            f"A rate limit was hit for an inbox.\n\n"
            f"Inbox: {data.get('inbox_email', '—')}\n"
            f"Wait minutes: {data.get('wait_minutes', '—')}\n"
            f"Time: {ts}\n"
        )
    elif event_type == "token_expired":
        subject = f"OAuth token expired — {data.get('inbox_email', 'an inbox')}"
        body = (
            f"An inbox's OAuth token could not be refreshed. "
            f"Please reconnect the inbox.\n\n"
            f"Inbox: {data.get('inbox_email', '—')}\n"
            f"Error: {data.get('error', '—')}\n"
            f"Time: {ts}\n"
        )
    else:
        subject = f"Quickly notification — {event_type}"
        body = (
            f"Event: {event_type}\n"
            f"Time: {ts}\n\n"
            f"Details:\n" +
            "\n".join(f"  {k}: {v}" for k, v in data.items()) + "\n"
        )

    # Append a footer
    body += (
        f"\n—\n"
        f"This is an automated notification from Quickly.\n"
        f"You can manage your notification preferences in Settings.\n"
    )
    return subject, body


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
# Main entry point — called alongside fire_webhook_event
# ---------------------------------------------------------------------------

async def fire_email_notification(
    db: AsyncSession, event_type: str, data: dict[str, Any]
) -> None:
    """Send email notifications for *event_type* to all opted-in users.

    Rate limiting and event filtering are applied per-user.
    Failures are logged but never raised.
    """
    try:
        result = await db.execute(
            select(EmailNotificationConfig, User)
            .join(User, EmailNotificationConfig.user_id == User.id)
            .where(
                EmailNotificationConfig.enabled == True,  # noqa: E712
                User.is_active == True,  # noqa: E712
            )
        )
        rows = result.all()

        if not rows:
            return

        now = time_provider.utcnow()

        for config, user in rows:
            # Event filter
            if config.events and event_type not in config.events:
                continue

            # Rate limiting: reset counter if we're in a new hour window
            if config.rate_window_start is None or (now - config.rate_window_start).total_seconds() >= 3600:
                config.rate_window_start = now
                config.notifications_sent_this_hour = 0

            if config.notifications_sent_this_hour >= config.rate_limit_per_hour:
                log.debug(
                    "Rate limit reached for user %s (%d/%d this hour) — skipping notification",
                    user.id, config.notifications_sent_this_hour, config.rate_limit_per_hour,
                )
                continue

            subject, body = format_notification_email(event_type, data)
            success = await _send_notification_for_user(db, user, config, subject, body)

            if success:
                config.notifications_sent_this_hour += 1
                await db.flush()
    except Exception:
        log.exception("Unexpected error in fire_email_notification")
