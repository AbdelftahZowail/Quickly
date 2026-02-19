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

from app.settings_manager import settings
from app import time as time_provider

log = logging.getLogger("campaign_engine.sender")


@dataclass
class SendResult:
    """Returned by send_email with IDs needed for threading."""
    message_id: str            # RFC 822 Message-ID (e.g. <xxx@domain>)
    thread_id: Optional[str] = None  # Gmail threadId (only for Gmail provider)

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
    thread_id: Optional[str] = None,
) -> Optional[SendResult]:
    """
    Send one email via Gmail API using an OAuth access token.
    Returns SendResult with Message-ID header and Gmail threadId for threading.
    """
    if not access_token:
        log.error("Gmail send: no access token for %s", from_email)
        return None

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
    payload_dict: Dict[str, Any] = {"raw": raw}
    if thread_id:
        payload_dict["threadId"] = thread_id
    payload = json.dumps(payload_dict).encode("utf-8")

    # Log full raw email for debugging
    _log_gmail_call(to_email, from_email, subject, msg.as_string(), payload_dict, thread_id)

    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read().decode())
            gmail_thread_id = resp_data.get("threadId")
            gmail_msg_id = resp_data.get("id")

            # Fetch the REAL Message-ID that Gmail assigned (not our local one)
            # Gmail replaces Message-ID on send, so we must read it back.
            real_message_id = message_id  # fallback to local one
            if gmail_msg_id:
                real_message_id = _fetch_gmail_message_id(gmail_msg_id, access_token) or message_id

            _log_gmail_call(to_email, from_email, subject, msg.as_string(), payload_dict, thread_id,
                            status="200 OK",
                            response=json.dumps({**resp_data, "real_message_id": real_message_id}))
            log.info("Gmail API: sent to=%s gmail_id=%s threadId=%s real_message_id=%s",
                     to_email, gmail_msg_id, gmail_thread_id, real_message_id)
            return SendResult(message_id=real_message_id, thread_id=gmail_thread_id)
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()
        except Exception:
            pass
        _log_gmail_call(to_email, from_email, subject, msg.as_string(), payload_dict, thread_id,
                        status=f"ERROR {e.code}", error=f"{e.reason}: {err_body}")
        log.error("Gmail API error %s %s: %s", e.code, e.reason, err_body)
        return None
    except Exception as e:
        _log_gmail_call(to_email, from_email, subject, msg.as_string(), payload_dict, thread_id,
                        status="ERROR", error=str(e))
        log.error("Gmail API error: %s\n%s", e, traceback.format_exc())
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
    thread_id: Optional[str] = None,
) -> Optional[SendResult]:
    """
    Send one email. Route based on provider:
      - "gmail"  → Gmail API with OAuth token
      - "resend" → Resend SDK (if API key set)
      - "smtp"   → SMTP
    Falls back to the global EMAIL_PROVIDER setting if provider is empty.

    Returns a SendResult with message_id (RFC 822) and thread_id (Gmail only),
    or None on failure.

    In test mode (TEST_MODE=true):
      - Gmail provider: skips sending entirely, returns a fake SendResult (everything
        still gets logged to DB / email_log as if it was delivered).
      - Resend/SMTP: redirects recipient to delivered+{tag}@resend.dev.
    """
    # Determine effective provider
    effective = provider or settings.email_provider

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

    if effective == "gmail" and gmail_access_token:
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
