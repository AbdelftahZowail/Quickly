from __future__ import annotations

"""Email sending with template substitution and threading. Gmail and Office 365."""
import base64
import json
import logging
import re
import traceback
import urllib.request
import urllib.error
from dataclasses import dataclass
import email.policy
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Optional, Dict, Any

# google client libraries for Gmail API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.settings_manager import settings
from app import time as time_provider
from app.models import GmailAccount, Office365Account, SmtpAccount
from app.routers.gmail_oauth import refresh_access_token  # needed for token refresh when sending via gmail
from app.routers.office365_oauth import refresh_access_token as refresh_office365_token

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


# ---- MIME construction (Python stdlib, no external deps) ----

def _strip_html_tags(html: str) -> str:
    """Convert HTML to plain text by converting block-level tags to newlines."""
    html = re.sub(r'</?(p|div|br|tr)[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    # Decode common HTML entities
    html = (
        html
        .replace('&amp;', '&')
        .replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&nbsp;', ' ')
        .replace('&quot;', '"')
        .replace('&#39;', "'")
    )
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


def _strip_html_document_wrapper(html: str) -> str:
    """Extract useful body fragment from a possible full-document HTML string.

    Handles every real-world input shape:
    - Fragment HTML (no <html> tag): returned as-is.
    - Full document with <body>…</body>: body content extracted, wrapper
      discarded.
    - Full document with <body> but missing </body> (malformed): everything
      from <body> to end-of-string is used (non-greedy `.*?` with optional
      </body> anchor handles both cases in one regex).
    - No <body> tag at all: entire <head> section is stripped (removing
      <meta>, <style>, etc.) and the remaining wrapper tags are removed.
    - Preheader / any content placed *before* the <html> tag (e.g. the
      hidden preview-text div that jobs.py prepends): that content is
      preserved and prepended to the extracted body fragment.
    """
    html_m = re.search(r'<html\b', html, flags=re.IGNORECASE)
    if not html_m:
        # Already a fragment — return unchanged.
        return html.strip()

    # Preserve anything before <html> (e.g. the preheader div).
    before_html = html[:html_m.start()].strip()
    doc = html[html_m.start():]

    # Non-greedy capture from <body> to the first </body> (or end-of-string
    # when </body> is absent).  The trailing (?:</body>.*)? greedily swallows
    # </body></html> and anything else so they don't appear in group(1).
    body_m = re.search(
        r'<body[^>]*>(.*?)(?:</body>.*)?$',
        doc,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if body_m:
        body_content = body_m.group(1).strip()
    else:
        # No <body> tag at all — strip the entire <head> section (which
        # includes <meta>, <style>, <title> etc.) then strip remaining
        # structural open/close tags.
        body_content = re.sub(
            r'<head\b[^>]*>.*?</head>', '', doc,
            flags=re.IGNORECASE | re.DOTALL,
        )
        body_content = re.sub(
            r'</?(?:html|body)[^>]*>', '', body_content,
            flags=re.IGNORECASE,
        ).strip()

    return ((before_html + '\n' + body_content).strip() if before_html
            else body_content.strip())


# ---- Email quoting helpers (Gmail-style) -----------------------------------------------

def _format_attribution_date(dt: datetime) -> str:
    """Format a datetime as a Gmail-style attribution date string.

    e.g. 'Wed, Mar 4, 2026 at 1:18\u202fPM'  (narrow no-break space before AM/PM)
    """
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    day_name = days[dt.weekday()]
    month_name = months[dt.month - 1]
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    minute = f"{dt.minute:02d}"
    NARROW_NBSP = "\u202f"
    return f"{day_name}, {month_name} {dt.day}, {dt.year} at {hour12}:{minute}{NARROW_NBSP}{ampm}"


def build_quote_plain(prev_plain: str, from_name: str, from_email: str, sent_at: datetime) -> str:
    """Return a Gmail-style plain-text quote block to append after the reply body.

    Format::

        On Wed, Mar 4, 2026 at 1:18 PM John Doe <john@example.com> wrote:
        > Previous line 1
        > Previous line 2
    """
    date_str = _format_attribution_date(sent_at)
    attribution = f"On {date_str} {from_name} <{from_email}> wrote:"
    quoted_lines = "\n".join(
        f"> {line}" if line else ">"
        for line in prev_plain.splitlines()
    )
    return f"\n\n{attribution}\n{quoted_lines}\n"


def build_quote_html(prev_html: str, from_name: str, from_email: str, sent_at: datetime) -> str:
    """Return a Gmail-style HTML quote block to append after the reply body.

    Produces the canonical Gmail blockquote structure that email clients
    recognise and collapse by default.
    """
    import html as _html_module
    date_str = _format_attribution_date(sent_at)
    escaped_name = _html_module.escape(from_name)
    escaped_addr = _html_module.escape(from_email)
    escaped_date = _html_module.escape(date_str)
    attribution = (
        f'<div dir="ltr" class="gmail_attr">On {escaped_date} '
        f'{escaped_name} &lt;<a href="mailto:{escaped_addr}">{escaped_addr}</a>&gt; wrote:<br></div>'
    )
    return (
        f'<br><div class="gmail_quote gmail_quote_container">'
        f'{attribution}'
        f'<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;'
        f'border-left:1px solid rgb(204,204,204);padding-left:1ex">'
        f'{prev_html}'
        f'\n</blockquote></div>'
    )


def _plain_to_quoted_html(plain: str) -> str:
    """Convert plain text to a minimal HTML block suitable for use inside a quote."""
    import html as _html_module
    escaped = _html_module.escape(plain)
    return "<div>" + escaped.replace("\n", "<br>") + "</div>"


def _build_email_message(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "",
    reply_to_msg_id: Optional[str] = None,
    references: Optional[str] = None,
    is_html: bool = False,
    message_id: Optional[str] = None,
    list_unsubscribe_url: Optional[str] = None,
) -> EmailMessage:
    """Build an EmailMessage using Python's stdlib email module (policy.SMTP)."""
    msg = EmailMessage(policy=email.policy.SMTP)

    # Set body content first — set_content/add_alternative must be called
    # before headers are written so the MIME structure is established cleanly.
    # cte='quoted-printable' is explicit: never allow base64 on body parts.
    if is_html:
        # Strip full-document wrapper (<html>/<head>/<body>) — the MIME part
        # must contain only the body fragment, not a complete HTML document.
        clean_html = _strip_html_document_wrapper(body).strip()
        # Plain-text first, then the HTML alternative.
        # Spam filters compare both parts — content must match.
        plain = _strip_html_tags(clean_html)
        log.debug(
            "_build_email_message: html_len=%d clean_html_len=%d "
            "has_html_tag=%s has_meta_tag=%s",
            len(body),
            len(clean_html),
            bool(re.search(r'<html\b', clean_html, re.IGNORECASE)),
            bool(re.search(r'<meta\b', clean_html, re.IGNORECASE)),
        )
        msg.set_content(plain, cte='quoted-printable')
        msg.add_alternative(clean_html, subtype='html', cte='quoted-printable')
    else:
        msg.set_content(body.strip(), cte='quoted-printable')

    # Headers are added after the body is set.
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id

    if reply_to_msg_id:
        ref = reply_to_msg_id if reply_to_msg_id.startswith("<") else f"<{reply_to_msg_id}>"
        msg["In-Reply-To"] = ref
        msg["References"] = references or ref

    if list_unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    return msg


def build_raw_mime(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "",
    reply_to_msg_id: Optional[str] = None,
    references: Optional[str] = None,
    is_html: bool = False,
    message_id: Optional[str] = None,
    list_unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Build a raw RFC 2822 MIME string and return it base64url-encoded for
    the Gmail API.  No external dependencies — uses Python's stdlib only.

    Plugs directly into::

        gmail.users().messages().send(
            userId="me",
            body={"raw": build_raw_mime(...), "threadId": thread_id},
        ).execute()
    """
    msg = _build_email_message(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email=from_email,
        from_name=from_name,
        reply_to_msg_id=reply_to_msg_id,
        references=references,
        is_html=is_html,
        message_id=message_id,
        list_unsubscribe_url=list_unsubscribe_url,
    )
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


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
    google_client_id: str = "",
    google_client_secret: str = "",
    retry_on_auth_fail: bool = True,
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
                "client_id": google_client_id or settings.google_client_id,
                "client_secret": google_client_secret or settings.google_client_secret,
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

    # compose the MIME message using Python stdlib (no external deps)
    message_id = make_msgid()
    msg = _build_email_message(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email=from_email,
        from_name=from_name,
        reply_to_msg_id=reply_to_msg_id,
        references=references,
        is_html=is_html,
        message_id=message_id,
        list_unsubscribe_url=list_unsubscribe_url,
    )

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
            gmail_account.updated_at = datetime.utcnow()

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
            if retry_on_auth_fail and gmail_account and gmail_account.refresh_token:
                _cid = google_client_id or settings.google_client_id
                _csec = google_client_secret or settings.google_client_secret
                refreshed = refresh_access_token(gmail_account, _cid, _csec)
                if refreshed:
                    return _send_via_gmail(
                        to_email=to_email,
                        subject=subject,
                        body=body,
                        from_email=from_email,
                        from_name=from_name,
                        reply_to_msg_id=reply_to_msg_id,
                        references=references,
                        is_html=is_html,
                        access_token=gmail_account.access_token or "",
                        gmail_account=gmail_account,
                        thread_id=thread_id,
                        list_unsubscribe_url=list_unsubscribe_url,
                        google_client_id=google_client_id,
                        google_client_secret=google_client_secret,
                        retry_on_auth_fail=False,
                    )
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
    google_client_id: str = "",
    google_client_secret: str = "",
    office365_account: Optional[Office365Account] = None,
    office365_client_id: str = "",
    office365_client_secret: str = "",
    office365_tenant_id: str = "",
    conversation_id: Optional[str] = None,
    reply_graph_message_id: Optional[str] = None,
    smtp_account: Optional[SmtpAccount] = None,
) -> Optional[SendResult | SendFailure]:
    """Send one email via Gmail API or Microsoft Graph API.

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
            thread_id=thread_id or conversation_id,
            status="TEST_MODE",
            response=f"fake_message_id={fake_id}",
        )
        return SendResult(
            message_id=fake_id,
            thread_id=thread_id or conversation_id or "fake-thread",
        )

    if provider == "smtp":
        if not smtp_account:
            log.error("send_email: no SMTP credentials for %s", from_email)
            return SendFailure(error_type="auth_failed", message="No SMTP credentials provided")
        return _send_via_smtp(
            to_email=to_email,
            subject=subject,
            body=body,
            from_email=from_email,
            from_name=from_name,
            reply_to_msg_id=reply_to_msg_id,
            references=references,
            is_html=is_html,
            smtp_account=smtp_account,
            list_unsubscribe_url=list_unsubscribe_url,
        )

    if provider == "office365":
        if not office365_account:
            log.error("send_email: no Office 365 credentials for %s", from_email)
            return SendFailure(error_type="auth_failed", message="No Office 365 credentials provided")
        return _send_via_office365(
            to_email=to_email,
            subject=subject,
            body=body,
            from_email=from_email,
            from_name=from_name,
            reply_to_msg_id=reply_to_msg_id,
            references=references,
            is_html=is_html,
            office365_account=office365_account,
            conversation_id=conversation_id,
            list_unsubscribe_url=list_unsubscribe_url,
            office365_client_id=office365_client_id,
            office365_client_secret=office365_client_secret,
            office365_tenant_id=office365_tenant_id,
            reply_graph_message_id=reply_graph_message_id,
        )

    # Default: Gmail
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
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
    )


# ---- Office 365 / Microsoft Graph sending ----

_O365_LOG_PATH = _LOG_DIR / "office365_api.log"


def _log_office365_call(
    to_email: str,
    from_email: str,
    subject: str,
    *,
    conversation_id: Optional[str] = None,
    status: str = "SENDING",
    response: str = "",
    error: Optional[str] = None,
) -> None:
    """Append log entry for every Office 365 Graph API call."""
    entry = {
        "timestamp": time_provider.utcnow().isoformat() + "Z",
        "to": to_email,
        "from": from_email,
        "subject": subject,
        "conversation_id": conversation_id,
        "status": status,
    }
    if response:
        entry["response"] = response
    if error:
        entry["error"] = error
    try:
        with open(_O365_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, indent=2) + "\n---\n")
    except OSError:
        log.warning("Could not write to office365 log file at %s", _O365_LOG_PATH)


def _send_via_office365(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "",
    reply_to_msg_id: Optional[str] = None,
    references: Optional[str] = None,
    is_html: bool = False,
    office365_account: Office365Account | None = None,
    conversation_id: Optional[str] = None,
    list_unsubscribe_url: Optional[str] = None,
    office365_client_id: str = "",
    office365_client_secret: str = "",
    office365_tenant_id: str = "",
    reply_graph_message_id: Optional[str] = None,
    retry_on_auth_fail: bool = True,
) -> Optional[SendResult | SendFailure]:
    """Send one email via Microsoft Graph API using raw MIME format.

    Both send paths (new message and reply) POST or PUT raw MIME so that
    standard RFC headers — List-Unsubscribe, List-Unsubscribe-Post, In-Reply-To,
    References, etc. — are transmitted verbatim without any Graph API
    restriction on header names.
    """
    if not office365_account:
        log.error("Office 365 send: no account provided for %s", from_email)
        return SendFailure(error_type="auth_failed", message="No Office 365 account")

    access_token = office365_account.access_token
    if not access_token:
        log.error("Office 365 send: no access token for %s", from_email)
        return None

    # ── choose send strategy ────────────────────────────────────────────────
    # When we have a real Graph message ID (not a local surrogate), we use the
    # Graph Reply API.  This is the ONLY reliable way to keep replies in the
    # same Outlook thread: Exchange threads by its proprietary conversationIndex
    # header (a binary blob), which Graph sets automatically on the reply draft.
    # sendMail cannot set conversationIndex, so it always creates a new thread
    # from Outlook's perspective even if In-Reply-To / References are correct.
    use_reply_api = (
        reply_graph_message_id
        and not reply_graph_message_id.startswith("local-")
    )

    # Build the MIME message once — same path as Gmail, so List-Unsubscribe,
    # List-Unsubscribe-Post, In-Reply-To, References etc. are all set as
    # normal RFC 2822 headers without any provider-specific workaround.
    message_id = make_msgid()
    mime_msg = _build_email_message(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email=from_email,
        from_name=from_name,
        reply_to_msg_id=reply_to_msg_id,
        references=references,
        is_html=is_html,
        message_id=message_id,
        list_unsubscribe_url=list_unsubscribe_url,
    )
    raw_mime: bytes = mime_msg.as_bytes()

    def _do_json(url: str, payload: bytes, method: str = "POST") -> tuple[int, bytes]:
        req = urllib.request.Request(
            url,
            data=payload if payload else None,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), r.read()

    def _do_mime(url: str, mime_bytes: bytes, method: str = "POST", content_type: str = "text/plain") -> tuple[int, bytes]:
        req = urllib.request.Request(
            url,
            data=mime_bytes,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": content_type,
            },
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), r.read()

    send_time = time_provider.utcnow()
    _log_office365_call(to_email, from_email, subject, conversation_id=conversation_id)

    try:
        if use_reply_api:
            # ── Graph Reply API (3-step): createReply → upload MIME → send ──
            # Step 1: create a reply draft so Graph assigns the correct
            # conversationIndex (the binary Outlook threading blob).
            create_url = (
                f"https://graph.microsoft.com/v1.0/me/messages/"
                f"{reply_graph_message_id}/createReply"
            )
            _, create_body = _do_json(create_url, b"{}")
            draft = json.loads(create_body)
            draft_id = draft["id"]
            draft_conv_id = draft.get("conversationId") or conversation_id

            # Step 2: replace the draft content with our full MIME payload.
            # Using $value with Content-Type: message/rfc822 sets all RFC
            # headers (including List-Unsubscribe-Post) while the server-side
            # conversationId / conversationIndex assigned in step 1 are
            # preserved as message-object properties.
            _do_mime(
                f"https://graph.microsoft.com/v1.0/me/messages/{draft_id}/$value",
                raw_mime,
                method="PUT",
                content_type="message/rfc822",
            )

            # Step 3: send the draft.
            _do_json(
                f"https://graph.microsoft.com/v1.0/me/messages/{draft_id}/send",
                b"{}",
            )

            sent_conversation_id = draft_conv_id
        else:
            # ── sendMail with raw MIME (new conversation / local-surrogate) ──
            # Graph's sendMail MIME endpoint requires Content-Type: text/plain
            # with the request body as base64-encoded MIME (not raw bytes).
            _do_mime(
                "https://graph.microsoft.com/v1.0/me/sendMail",
                base64.b64encode(raw_mime),
                content_type="text/plain",
            )
            sent_conversation_id = conversation_id

        # Fetch the real RFC 2822 internetMessageId and (for new threads) the
        # conversationId that Microsoft assigned to the message.
        # The locally generated message_id is used as fallback.
        sent_message_id = message_id
        real_ids = _fetch_sent_message_ids(access_token, to_email, send_time)
        if real_ids:
            sent_message_id = real_ids.get("internetMessageId") or sent_message_id
            if not sent_conversation_id:
                sent_conversation_id = real_ids.get("conversationId") or sent_conversation_id

        _log_office365_call(
            to_email, from_email, subject,
            conversation_id=sent_conversation_id,
            status="202 Accepted",
            response=json.dumps({"message_id": sent_message_id, "conversation_id": sent_conversation_id}),
        )
        log.info(
            "Office 365 Graph API: sent to=%s message_id=%s conversationId=%s",
            to_email, sent_message_id, sent_conversation_id,
        )
        return SendResult(
            message_id=sent_message_id,
            thread_id=sent_conversation_id,
            gmail_message_id=None,
        )
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()
        except Exception:
            pass
        status_code = e.code
        _log_office365_call(
            to_email, from_email, subject,
            conversation_id=conversation_id,
            status=f"ERROR {status_code}",
            error=err_body[:500],
        )
        log.error("Office 365 Graph API error %s: %s", status_code, err_body[:300])

        if status_code == 400:
            return SendFailure(
                error_type="bounce",
                message=f"Microsoft rejected the message (400): {err_body[:300]}",
            )
        if status_code in (401, 403):
            if retry_on_auth_fail and office365_account.refresh_token:
                _cid = office365_client_id or settings.office365_client_id
                _csec = office365_client_secret or settings.office365_client_secret
                _tid = office365_tenant_id or settings.office365_tenant_id
                refreshed = refresh_office365_token(office365_account, _cid, _csec, _tid)
                if refreshed:
                    return _send_via_office365(
                        to_email=to_email,
                        subject=subject,
                        body=body,
                        from_email=from_email,
                        from_name=from_name,
                        reply_to_msg_id=reply_to_msg_id,
                        references=references,
                        is_html=is_html,
                        office365_account=office365_account,
                        conversation_id=conversation_id,
                        list_unsubscribe_url=list_unsubscribe_url,
                        office365_client_id=office365_client_id,
                        office365_client_secret=office365_client_secret,
                        office365_tenant_id=office365_tenant_id,
                        reply_graph_message_id=reply_graph_message_id,
                        retry_on_auth_fail=False,
                    )
            err_type = "auth_failed" if status_code == 401 else "permission_denied"
            return SendFailure(
                error_type=err_type,
                message=f"Microsoft auth/permission error ({status_code}): {err_body[:300]}",
            )
        if status_code == 404:
            return SendFailure(
                error_type="invalid_recipient",
                message=f"Microsoft 404 — resource not found: {err_body[:300]}",
            )
        # 429 / 5xx are transient
        return None
    except Exception as e:
        _log_office365_call(
            to_email, from_email, subject,
            conversation_id=conversation_id,
            status="ERROR",
            error=str(e),
        )
        log.error("Office 365 Graph API error: %s", e)
        return None


# ---- Generic SMTP sending ----

_SMTP_LOG_PATH = _LOG_DIR / "smtp_api.log"


def _log_smtp_call(
    to_email: str,
    from_email: str,
    subject: str,
    *,
    thread_id: Optional[str] = None,
    status: str = "SENDING",
    response: str = "",
    error: Optional[str] = None,
) -> None:
    """Append a log entry for every SMTP send (mirrors the Gmail/O365 logs)."""
    entry = {
        "timestamp": time_provider.utcnow().isoformat() + "Z",
        "to": to_email,
        "from": from_email,
        "subject": subject,
        "thread_id": thread_id,
        "status": status,
    }
    if response:
        entry["response"] = response
    if error:
        entry["error"] = error
    try:
        with open(_SMTP_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, indent=2) + "\n---\n")
    except OSError:
        log.warning("Could not write to smtp log file at %s", _SMTP_LOG_PATH)


def _send_via_smtp(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "",
    reply_to_msg_id: Optional[str] = None,
    references: Optional[str] = None,
    is_html: bool = False,
    smtp_account: SmtpAccount | None = None,
    list_unsubscribe_url: Optional[str] = None,
) -> Optional[SendResult | SendFailure]:
    """Send one email via a generic SMTP relay (stdlib ``smtplib``).

    Threading works exactly like the Gmail path: follow-ups carry
    ``In-Reply-To`` / ``References`` and reuse the root ``Message-ID`` as the
    thread key, so the unibox IMAP sync can group them without any
    provider-specific thread API.
    """
    if not smtp_account:
        log.error("SMTP send: no account provided for %s", from_email)
        return SendFailure(error_type="auth_failed", message="No SMTP account")
    if not (smtp_account.smtp_host or "").strip():
        return SendFailure(error_type="auth_failed", message="SMTP host is not configured")

    import smtplib
    import socket

    message_id = make_msgid()
    mime_msg = _build_email_message(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email=from_email,
        from_name=from_name,
        reply_to_msg_id=reply_to_msg_id,
        references=references,
        is_html=is_html,
        message_id=message_id,
        list_unsubscribe_url=list_unsubscribe_url,
    )
    raw_bytes: bytes = mime_msg.as_bytes()
    # Thread key: the root message of the chain (first References entry) or
    # our own Message-ID for a new thread.  Stored on EmailLog.thread_id so
    # follow-ups, unibox grouping, and In-Reply-To all stay consistent.
    if references:
        thread_key = references.split()[0]
    elif reply_to_msg_id:
        thread_key = reply_to_msg_id if reply_to_msg_id.startswith("<") else f"<{reply_to_msg_id}>"
    else:
        thread_key = message_id

    _log_smtp_call(to_email, from_email, subject, thread_id=thread_key)
    try:
        from app.smtp_utils import _smtp_connect

        client = _smtp_connect(smtp_account, timeout=30)
        try:
            client.sendmail(from_email, [to_email], raw_bytes)
        finally:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass
        _log_smtp_call(
            to_email, from_email, subject,
            thread_id=thread_key,
            status="250 OK",
            response=json.dumps({"message_id": message_id, "thread_key": thread_key}),
        )
        log.info("SMTP: sent to=%s message_id=%s thread_key=%s", to_email, message_id, thread_key)
        return SendResult(message_id=message_id, thread_id=thread_key, gmail_message_id=None)
    except smtplib.SMTPRecipientsRefused as e:
        err = str(e)[:300]
        _log_smtp_call(to_email, from_email, subject, thread_id=thread_key, status="ERROR", error=err)
        return SendFailure(error_type="invalid_recipient", message=f"SMTP recipient refused: {err}")
    except smtplib.SMTPSenderRefused as e:
        err = str(e)[:300]
        _log_smtp_call(to_email, from_email, subject, thread_id=thread_key, status="ERROR", error=err)
        # Sender-side failure (bad envelope auth, relay policy, MAIL FROM rejected) —
        # NOT a recipient bounce. jobs.py handles "auth_failed" by pausing the inbox
        # instead of marking leads as bounced (which would irreversibly poison the
        # campaign when only the relay configuration is broken).
        return SendFailure(error_type="auth_failed", message=f"SMTP sender refused: {err}")
    except smtplib.SMTPDataError as e:
        err = str(e)[:300]
        _log_smtp_call(to_email, from_email, subject, thread_id=thread_key, status="ERROR", error=err)
        # 5xx at DATA time is a permanent rejection (content/policy); 4xx is transient.
        code = getattr(e, "smtp_code", 0) or 0
        if 500 <= code < 600:
            return SendFailure(error_type="bounce", message=f"SMTP rejected the message ({code}): {err}")
        return None
    except smtplib.SMTPAuthenticationError as e:
        err = str(e)[:300]
        _log_smtp_call(to_email, from_email, subject, thread_id=thread_key, status="ERROR", error=err)
        return SendFailure(error_type="auth_failed", message=f"SMTP authentication failed: {err}")
    except (smtplib.SMTPException, socket.error, OSError) as e:
        _log_smtp_call(to_email, from_email, subject, thread_id=thread_key, status="ERROR", error=str(e)[:300])
        log.error("SMTP send error: %s", e)
        return None
    except Exception as e:
        _log_smtp_call(to_email, from_email, subject, thread_id=thread_key, status="ERROR", error=str(e)[:300])
        log.error("SMTP send error: %s", e)
        return None


def _fetch_sent_message_ids(
    access_token: str,
    to_email: str,
    sent_after: datetime,
) -> Dict[str, str] | None:
    """Fetch the real internetMessageId and conversationId from SentItems.

    Searches for the most recently sent message to *to_email* with a
    sentDateTime at or after *sent_after* (the moment just before the
    sendMail call).  This is reliable because:
      - We just sent the message seconds ago to that exact recipient.
      - The 5-minute time window prevents matching any older messages.

    Returns a dict with 'internetMessageId' and 'conversationId', or None
    if the fetch fails (caller falls back to a generated ID).
    """
    import urllib.parse as _up
    # Give a 10-second buffer before the send time to handle minor clock skew.
    window_start = sent_after - timedelta(seconds=10)
    window_str = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    escaped_to = to_email.replace("'", "''")
    filter_str = (
        f"toRecipients/any(r:r/emailAddress/address eq '{escaped_to}') "
        f"and sentDateTime ge {window_str}"
    )
    params = _up.urlencode({
        "$filter": filter_str,
        "$top": "1",
        "$orderby": "sentDateTime desc",
        "$select": "internetMessageId,conversationId",
        "$count": "true",
    })
    url = f"https://graph.microsoft.com/v1.0/me/mailFolders/SentItems/messages?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "ConsistencyLevel": "eventual",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            messages = data.get("value", [])
            if messages:
                msg = messages[0]
                return {
                    "internetMessageId": msg.get("internetMessageId", ""),
                    "conversationId": msg.get("conversationId", ""),
                }
    except Exception as e:
        log.warning("Failed to fetch sent message IDs from Office 365: %s", e)
    return None
