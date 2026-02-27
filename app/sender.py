"""Email sending with template substitution and threading. Supports Resend API, SMTP, and Gmail OAuth."""
import base64
import json
import logging
import re
import smtplib
import traceback
import urllib.request
import urllib.error
import resend
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

# ---- Resend API file logger ----
# Every Resend API call (request + response) is appended to logs/resend_api.log
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_RESEND_LOG_PATH = _LOG_DIR / "resend_api.log"
_GMAIL_LOG_PATH = _LOG_DIR / "gmail_api.log"


def _log_resend_call(
    payload: dict,
    status: str,
    response_body: str,
    error: Optional[str] = None,
) -> None:
    """Append a structured log entry for a Resend API call to the log file."""
    entry = {
        "timestamp": time_provider.utcnow().isoformat() + "Z",
        "to": payload.get("to"),
        "from": payload.get("from"),
        "subject": payload.get("subject"),
        "status": status,
        "response": response_body,
    }
    if error:
        entry["error"] = error
    try:
        with open(_RESEND_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        log.warning("Could not write to resend log file at %s", _RESEND_LOG_PATH)


def _test_mode_redirect(to_email: str) -> str:
    """In test mode, redirect any recipient to delivered+{tag}@resend.dev so no real emails are sent."""
    # Sanitize the original address into a +tag: john@example.com -> john_at_example.com
    tag = to_email.replace("@", "_at_")
    redirected = f"delivered+{tag}@resend.dev"
    log.info("TEST MODE: redirecting %s -> %s", to_email, redirected)
    return redirected


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


def _send_via_resend(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "",
    reply_to_msg_id: Optional[str] = None,
    references: Optional[str] = None,
    is_html: bool = False,
) -> Optional[SendResult]:
    """
    Send one email via Resend Python SDK. Returns SendResult with a proper
    RFC 822 Message-ID for threading.
    """
    if not settings.resend_api_key:
        log.error("Resend API key not set — cannot send email")
        return None

    resend.api_key = settings.resend_api_key

    from_str = f"{from_name} <{from_email}>" if from_name else from_email
    params: resend.Emails.SendParams = {
        "from": from_str,
        "to": [to_email],
        "subject": subject,
    }
    if is_html:
        params["html"] = body
    else:
        params["text"] = body

    # Generate a proper RFC 822 Message-ID so follow-ups can reference it
    custom_message_id = make_msgid()
    headers: Dict[str, str] = {"Message-ID": custom_message_id}

    if reply_to_msg_id:
        header_val = reply_to_msg_id if reply_to_msg_id.startswith("<") else f"<{reply_to_msg_id}>"
        headers["In-Reply-To"] = header_val
        headers["References"] = references or header_val

    params["headers"] = headers

    # Log payload for debugging
    log_payload = {"from": from_str, "to": [to_email], "subject": subject}

    try:
        email = resend.Emails.send(params)
        _log_resend_call(log_payload, status="200 OK", response_body=str(email))
        log.info("Resend SDK: sent to=%s message_id=%s", to_email, custom_message_id)
        return SendResult(message_id=custom_message_id)
    except Exception as e:
        tb = traceback.format_exc()
        _log_resend_call(log_payload, status="ERROR", response_body="", error=f"{e}\n{tb}")
        log.error("Resend SDK error sending to %s: %s\n%s", to_email, e, tb)
        return None


def _send_via_smtp(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "",
    reply_to_msg_id: Optional[str] = None,
    references: Optional[str] = None,
    is_html: bool = False,
) -> Optional[SendResult]:
    """Send one email via SMTP. Returns SendResult with message_id for threading."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    msg["Date"] = time_provider.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

    message_id = make_msgid()
    msg["Message-ID"] = message_id
    if reply_to_msg_id:
        ref = reply_to_msg_id if reply_to_msg_id.startswith("<") else f"<{reply_to_msg_id}>"
        msg["In-Reply-To"] = ref
        msg["References"] = references or ref

    part = MIMEText(body, "html" if is_html else "plain", "utf-8")
    msg.attach(part)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return SendResult(message_id=message_id)
    except Exception:
        return None


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
        log.error("Gmail API error %s %s: %s", e.resp.status, e.resp.reason, err_body)
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
    gmail_account: "GmailAccount" | None = None,
    thread_id: Optional[str] = None,
) -> Optional[SendResult]:
    """
    Send one email. The caller **must** specify a provider, typically the one
    configured on the sending inbox. The global EMAIL_PROVIDER setting is no
    longer consulted; it was previously used as a fallback but that behaviour
    caused confusion when an inbox had a different provider.

    Route based on provider:
      - "gmail"  → Gmail API with OAuth token
      - "resend" → Resend SDK (if API key set)
      - "smtp"   → SMTP

    Returns a SendResult with message_id (RFC 822) and thread_id (Gmail only),
    or None on failure.

    In test mode (TEST_MODE=true):
      - Gmail provider: skips sending entirely, returns a fake SendResult (everything
        still gets logged to DB / email_log as if it was delivered).
      - Resend/SMTP: redirects recipient to delivered+{tag}@resend.dev.
    """
    # Determine effective provider; require it be passed explicitly
    effective = provider
    if not effective:
        raise ValueError("send_email() requires a provider")

    if settings.test_mode and effective == "gmail":
        # Simulate a successful send without hitting Gmail API
        fake_id = make_msgid()
        log.info(
            "TEST MODE (gmail): simulated send to=%s subject=%r from=%s — no email actually sent",
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

    if settings.test_mode:
        to_email = _test_mode_redirect(to_email)

    if effective == "gmail" and (gmail_access_token or gmail_account):
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
        )
    if effective == "resend" and settings.resend_api_key:
        return _send_via_resend(
            to_email=to_email,
            subject=subject,
            body=body,
            from_email=from_email,
            from_name=from_name,
            reply_to_msg_id=reply_to_msg_id,
            references=references,
            is_html=is_html,
        )
    return _send_via_smtp(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email=from_email,
        from_name=from_name,
        reply_to_msg_id=reply_to_msg_id,
        references=references,
        is_html=is_html,
    )
