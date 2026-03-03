from __future__ import annotations

"""Email sending with template substitution and threading. Gmail OAuth only."""
import base64
import json
import logging
import re
import traceback
import urllib.request
import urllib.error
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid
from pathlib import Path
from typing import Optional, Dict, Any

# google client libraries for Gmail API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.settings_manager import settings
from app import time as time_provider
from app.models import GmailAccount
from app.routers.gmail_oauth import refresh_access_token  # needed for token refresh when sending via gmail

log = logging.getLogger("quickly.sender")


@dataclass
class SendResult:
    """Returned by send_email with IDs needed for threading."""
    message_id: str            # RFC 822 Message-ID (e.g. <xxx@domain>)
    thread_id: Optional[str] = None  # Gmail threadId (only for Gmail provider)
    gmail_message_id: Optional[str] = None  # Gmail API message id when available

    def __bool__(self):
        return bool(self.message_id)


@dataclass
class SendFailure:
    """Returned when sending fails permanently (e.g. bounce, invalid recipient).

    Callers should NOT retry the send.  The lead should be marked with the
    appropriate status (e.g. ``bounced``) and remaining queue slots deleted.
    """
    error_type: str   # "bounce", "invalid_recipient", "permission_denied", "auth_failed"
    message: str

    def __bool__(self):
        return False


# ---- Gmail API file logger ----
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_GMAIL_LOG_PATH = _LOG_DIR / "gmail_api.log"


def _log_gmail_call(
    to_email: str,
    from_email: str,
    subject: str,
    raw_mime: str,
    api_payload: dict,
    thread_id: Optional[str] = None,
    status: str = "SENDING",
    response: str = "",
    error: Optional[str] = None,
) -> None:
    """Append full raw email data for every Gmail API call to logs/gmail_api.log."""
    entry = {
        "timestamp": time_provider.utcnow().isoformat() + "Z",
        "to": to_email,
        "from": from_email,
        "subject": subject,
        "thread_id": thread_id,
        "status": status,
        "raw_mime": raw_mime,
        "api_payload_keys": list(api_payload.keys()),
        "has_threadId": "threadId" in api_payload,
    }
    if response:
        entry["response"] = response
    if error:
        entry["error"] = error
    try:
        with open(_GMAIL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, indent=2) + "\n---\n")
    except OSError:
        log.warning("Could not write to gmail log file at %s", _GMAIL_LOG_PATH)


def render_body(body: str, lead_data: Dict[str, Any]) -> str:
    """Replace {{field}} with lead_data[field]. Supports {{name}}, {{email}}, {{company}}, etc."""
    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        return str(lead_data.get(key, match.group(0)))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", repl, body)


def get_lead_data(lead) -> Dict[str, Any]:
    """Build dict for template substitution from a Lead model."""
    data = {"name": lead.name or "", "email": lead.email or ""}
    if getattr(lead, "custom_data", None) and isinstance(lead.custom_data, dict):
        data.update(lead.custom_data)
    return data


def _fetch_gmail_message_id(gmail_id: str, access_token: str) -> Optional[str]:
    """
    Fetch the real Message-ID header that Gmail assigned to a sent message.
    Gmail replaces locally-generated Message-IDs with its own (e.g. <CABcD...@mail.gmail.com>).
    We need the real one so In-Reply-To / References work for threading.
    """
    url = (
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{gmail_id}"
        f"?format=metadata&metadataHeaders=Message-Id"
    )
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            for header in data.get("payload", {}).get("headers", []):
                if header.get("name", "").lower() == "message-id":
                    real_id = header["value"]
                    log.info("Gmail: fetched real Message-ID=%s for gmail_id=%s", real_id, gmail_id)
                    return real_id
    except Exception as e:
        log.warning("Gmail: failed to fetch real Message-ID for %s: %s", gmail_id, e)
    return None


def _send_via_gmail(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "",
    reply_to_msg_id: Optional[str] = None,
    references: Optional[str] = None,
    is_html: bool = False,
    access_token: str = "",
    gmail_account: GmailAccount | None = None,
    thread_id: Optional[str] = None,
    list_unsubscribe_url: Optional[str] = None,
) -> Optional[SendResult]:
    """
    Send one email via Gmail API using an OAuth access token or ``GmailAccount``
    model instance.  When a model is provided the function will:

      * ensure the token is up‑to‑date (refreshing it if expired),
      * build a ``google-api-python-client`` service object,
      * automatically retry on transient errors,
      * update the ``GmailAccount`` object with any refreshed access token or
        expiry returned by the library.

    The return value is the same ``SendResult`` as before: ``message_id`` is
    the RFC‑822 Message‑ID header (the library requires a second API call to
    fetch the real value) and ``thread_id`` is the Gmail thread identifier.
    """
    # prefer the token on the account if one is supplied
    if gmail_account:
        # make sure the access token is fresh; the helper will update the model
        if gmail_account.token_expiry and gmail_account.token_expiry <= time_provider.utcnow():
            refreshed = refresh_access_token(gmail_account)
            if not refreshed:
                log.error("Gmail send: token refresh failed for %s", gmail_account.google_email)
                return None
        access_token = gmail_account.access_token or ""

    if not access_token:
        log.error("Gmail send: no access token for %s", from_email)
        return None

    # build credentials object; supplying refresh/secret info allows the
    # client library to refresh automatically and keep ``creds.token``
    # up‑to‑date.
    creds_kwargs: dict[str, object] = {"token": access_token}
    if gmail_account and gmail_account.refresh_token:
        creds_kwargs.update(
            {
                "refresh_token": gmail_account.refresh_token,
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            }
        )
    creds = Credentials(**creds_kwargs)  # type: ignore[arg-type]

    # construct the gmail service; ``cache_discovery=False`` avoids writing
    # files to disk in environments without a home directory.
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        log.error("Failed to build Gmail service: %s", e)
        return None

    # compose the message exactly as before
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    message_id = make_msgid()
    msg["Message-ID"] = message_id

    if reply_to_msg_id:
        ref = reply_to_msg_id if reply_to_msg_id.startswith("<") else f"<{reply_to_msg_id}>"
        msg["In-Reply-To"] = ref
        msg["References"] = references or ref

    if list_unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    part = MIMEText(body, "html" if is_html else "plain", "utf-8")
    msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    body_payload: Dict[str, Any] = {"raw": raw}
    if thread_id:
        body_payload["threadId"] = thread_id

    # log pre-send state (do not include creds accidentally)
    _log_gmail_call(to_email, from_email, subject, msg.as_string(), body_payload, thread_id)

    try:
        send_resp = (
            service.users()
            .messages()
            .send(userId="me", body=body_payload)
            .execute(num_retries=3)
        )
        gmail_thread_id = send_resp.get("threadId")
        gmail_msg_id = send_resp.get("id")

        # if the library automatically refreshed the token, save it back to
        # the model so the caller can commit it.
        if gmail_account and creds.token and creds.token != access_token:
            gmail_account.access_token = creds.token
            if getattr(creds, "expiry", None):
                gmail_account.token_expiry = creds.expiry

        # second call to get the real Message-ID header
        real_message_id = message_id
        if gmail_msg_id:
            get_resp = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=gmail_msg_id,
                    format="metadata",
                    metadataHeaders=["Message-Id"],
                )
                .execute(num_retries=3)
            )
            for header in get_resp.get("payload", {}).get("headers", []):
                if header.get("name", "").lower() == "message-id":
                    real_message_id = header["value"]
                    break

        _log_gmail_call(
            to_email,
            from_email,
            subject,
            msg.as_string(),
            body_payload,
            thread_id,
            status="200 OK",
            response=json.dumps({**send_resp, "real_message_id": real_message_id}),
        )
        log.info(
            "Gmail API: sent to=%s gmail_id=%s threadId=%s real_message_id=%s",
            to_email,
            gmail_msg_id,
            gmail_thread_id,
            real_message_id,
        )
        return SendResult(
            message_id=real_message_id,
            thread_id=gmail_thread_id,
            gmail_message_id=gmail_msg_id,
        )
    except HttpError as e:
        err_body = ""
        try:
            err_body = e.content.decode()
        except Exception:
            pass
        _log_gmail_call(
            to_email,
            from_email,
            subject,
            msg.as_string(),
            body_payload,
            thread_id,
            status=f"ERROR {e.resp.status}",
            error=f"{e.resp.reason}: {err_body}",
        )
        status_code = e.resp.status
        log.error("Gmail API error %s %s: %s", status_code, e.resp.reason, err_body)

        # --- classify the error ---------------------------------------------------
        # Permanent errors should NOT be retried — the lead's queue slots should
        # be cleared and the lead marked appropriately.
        if status_code == 400:
            # Bad request: typically invalid recipient, malformed message, or
            # the message was rejected by Gmail (e.g. classified as spam).
            return SendFailure(
                error_type="bounce",
                message=f"Gmail rejected the message (400): {err_body[:300]}",
            )
        if status_code in (401, 403):
            # Auth / permission: token expired or account suspended.
            err_type = "auth_failed" if status_code == 401 else "permission_denied"
            return SendFailure(
                error_type=err_type,
                message=f"Gmail auth/permission error ({status_code}): {err_body[:300]}",
            )
        if status_code == 404:
            return SendFailure(
                error_type="invalid_recipient",
                message=f"Gmail 404 — user or resource not found: {err_body[:300]}",
            )
        # 429 / 5xx are transient (rate-limit or server issue) → return None so
        # the caller can retry later.
        return None
    except Exception as e:
        _log_gmail_call(
            to_email,
            from_email,
            subject,
            msg.as_string(),
            body_payload,
            thread_id,
            status="ERROR",
            error=str(e),
        )
        log.error("Gmail API error: %s", e)
        return None


def send_email(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "",
    reply_to_msg_id: Optional[str] = None,
    references: Optional[str] = None,
    is_html: bool = False,
    provider: str = "",
    gmail_access_token: str = "",
    gmail_account: Optional[GmailAccount] = None,
    thread_id: Optional[str] = None,
    list_unsubscribe_url: Optional[str] = None,
) -> Optional[SendResult | SendFailure]:
    """Send one email via Gmail API.

    Returns:
        ``SendResult``  — success (truthy).
        ``SendFailure`` — permanent error, do NOT retry (falsy).
        ``None``        — transient error, safe to retry later.

    In test mode the send is simulated: no email is actually delivered but a
    fake ``SendResult`` is returned so that the rest of the pipeline (DB
    logging, analytics, webhooks) proceeds normally.
    """
    if not provider:
        provider = "gmail"

    if provider != "gmail":
        log.warning("Unsupported provider %r — only 'gmail' is supported, ignoring", provider)

    if settings.test_mode:
        fake_id = make_msgid()
        log.info(
            "TEST MODE: simulated send to=%s subject=%r from=%s — no email actually sent",
            to_email, subject, from_email,
        )
        _log_gmail_call(
            to_email, from_email, subject,
            raw_mime="(test mode — no MIME built)",
            api_payload={},
            thread_id=thread_id,
            status="TEST_MODE",
            response=f"fake_message_id={fake_id}",
        )
        return SendResult(message_id=fake_id, thread_id=thread_id or "fake-thread")

    if not (gmail_access_token or gmail_account):
        log.error("send_email: no gmail credentials for %s", from_email)
        return SendFailure(error_type="auth_failed", message="No Gmail credentials provided")

    return _send_via_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email=from_email,
        from_name=from_name,
        reply_to_msg_id=reply_to_msg_id,
        references=references,
        is_html=is_html,
        access_token=gmail_access_token,
        gmail_account=gmail_account,
        thread_id=thread_id,
        list_unsubscribe_url=list_unsubscribe_url,
    )
