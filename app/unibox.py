"""Unibox backend services: DB queries, Gmail sync, Office 365 sync, and realtime signaling."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html as _html_lib
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, desc, exists, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import time as time_provider
from app.app_settings import get_gmail_sync_config, get_google_oauth_credentials, get_office365_oauth_credentials
from app.webhooks import maybe_fire_email_event, fire_lead_reply_webhook
from app.database import AsyncSessionLocal
from app.models import (
    GmailAccount, GmailMessage, GmailSyncState, GmailThread,
    Office365Account, Office365Message, Office365SyncState, Office365Thread,
    Inbox, Lead, LeadReply, EmailLog, CampaignLead, QueueSlot,
)
from app.routers.gmail_oauth import refresh_access_token
from app.routers.office365_oauth import refresh_access_token as refresh_office365_token
from app.campaign_lead_status import ENROLLMENT_STATUSES, LEAD_INTERESTS

log = logging.getLogger("quickly.unibox")

GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
FULL_SYNC_PROGRESS_COMMIT_INTERVAL = 5
INITIAL_SYNC_WINDOW_DAYS = 7
BACKFILL_WINDOW_DAYS = 7
RECENT_RECOVERY_WINDOW_HOURS = 24
THREAD_HYDRATION_COMMIT_INTERVAL = 3
GMAIL_METADATA_HEADERS = [
    "From",
    "To",
    "Subject",
    "Date",
    "Message-Id",
    "In-Reply-To",
    "References",
]


def _parse_message_limit_from_env(env_name: str, *, default_raw: str, minimum: int) -> int | None:
    raw_value = (os.getenv(env_name, default_raw) or "").strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        parsed = int(default_raw)
    if parsed <= 0:
        return None
    return max(minimum, parsed)


# 0 (default) means unlimited for the window so we do not silently skip messages.
INITIAL_SYNC_MAX_MESSAGES = _parse_message_limit_from_env(
    "UNIBOX_INITIAL_SYNC_MAX_MESSAGES",
    default_raw="0",
    minimum=50,
)
BACKFILL_SYNC_MAX_MESSAGES = _parse_message_limit_from_env(
    "UNIBOX_BACKFILL_SYNC_MAX_MESSAGES",
    default_raw="0",
    minimum=50,
)
RECENT_RECOVERY_MAX_MESSAGES = _parse_message_limit_from_env(
    "UNIBOX_RECENT_RECOVERY_MAX_MESSAGES",
    default_raw="120",
    minimum=20,
)


class GmailAPIError(RuntimeError):
    """Raised when Gmail API returns an error."""

    def __init__(self, status_code: int, body: str):
        super().__init__(f"Gmail API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class UniboxEventBroker:
    """In-memory fan-out broker for SSE clients."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass


unibox_events = UniboxEventBroker()

_sync_lock = asyncio.Lock()
_inflight_inbox_syncs: set[int] = set()

_initial_list_sync_lock = asyncio.Lock()
_inflight_initial_list_syncs: set[int] = set()

_thread_hydration_lock = asyncio.Lock()
_inflight_hydration_inboxes: set[int] = set()


def _parse_headers(headers_json: str) -> list[dict[str, str]]:
    try:
        data = json.loads(headers_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict):
            out.append(
                {
                    "name": str(item.get("name", "")),
                    "value": str(item.get("value", "")),
                }
            )
    return out


def _header_value(headers_json: str, name: str) -> str:
    lname = name.lower()
    for hdr in _parse_headers(headers_json):
        if hdr["name"].lower() == lname:
            return hdr["value"]
    return ""


async def _single_lead_campaign_pair_for_thread(
    db: AsyncSession,
    thread_id: str,
) -> tuple[int, int] | None:
    """If exactly one (lead_id, campaign_id) is tied to *thread_id* in EmailLog, return it."""
    if not thread_id:
        return None
    res = await db.execute(
        select(EmailLog.lead_id, EmailLog.campaign_id).where(EmailLog.thread_id == thread_id).distinct()
    )
    pairs = {(int(r[0]), int(r[1])) for r in res.all()}
    if len(pairs) != 1:
        return None
    return next(iter(pairs))


def _extract_email_only(header_value: str) -> str:
    """Extract bare email address from a From/To header value.

    Handles both ``Name <email>`` and bare ``email`` formats.
    Returns lowercase email or empty string if nothing found.
    """
    import re as _re
    angle_match = _re.search(r"<([^>]+)>", header_value)
    if angle_match:
        return angle_match.group(1).strip().lower()
    bare_match = _re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", header_value, _re.IGNORECASE)
    if bare_match:
        return bare_match.group(0).strip().lower()
    return ""


def _parse_labels(label_ids_json: str) -> list[str]:
    try:
        labels = json.loads(label_ids_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(labels, list):
        return []
    return [str(x) for x in labels]


def _decode_base64url(data: str) -> bytes:
    padded = data + ("=" * ((4 - (len(data) % 4)) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _decode_gmail_body(data: str) -> str:
    try:
        return _decode_base64url(data).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime_type = str(part.get("mimeType", ""))
        body = part.get("body", {}) or {}
        data = body.get("data")
        if isinstance(data, str) and data:
            decoded = _decode_gmail_body(data)
            if mime_type == "text/plain":
                plain_parts.append(decoded)
            elif mime_type == "text/html":
                html_parts.append(decoded)
            elif not mime_type and decoded:
                plain_parts.append(decoded)

        for child in part.get("parts", []) or []:
            if isinstance(child, dict):
                walk(child)

    if isinstance(payload, dict):
        walk(payload)

    return ("\n".join(plain_parts).strip(), "\n".join(html_parts).strip())


def _now_epoch_ms() -> int:
    return int(time_provider.utcnow().timestamp() * 1000)


def _epoch_ms_to_dt(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    try:
        return datetime.utcfromtimestamp(ms / 1000.0)
    except Exception:
        return None


def _dt_to_iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.isoformat() + "Z"


# Matches common quoted-reply markers so we can strip them from snippets.
_QUOTE_START_RE = re.compile(
    r"(^|\n)[ \t]*>"  # lines starting with >
    r"|(^|\n)-{3,}"  # separator lines like ---
    r"|\bOn .{10,120}wrote:",  # "On <date> <person> wrote:"
    re.IGNORECASE | re.DOTALL,
)

# ── NDR / bounce-detection patterns ──────────────────────────────────────────
# Senders that indicate an automated delivery failure notification.
_MAILER_DAEMON_RE = re.compile(r"^(mailer-daemon|postmaster)@", re.IGNORECASE)
# Subjects that strongly suggest an NDR.
_BOUNCE_SUBJECT_RE = re.compile(
    r"(delivery.*(failed|failure|status|notification|problem)"
    r"|undeliverable"
    r"|mail.*not.*deliver"
    r"|returned.*mail"
    r"|failure.*notice)",
    re.IGNORECASE,
)
# DSN standard: Final-Recipient / Original-Recipient / X-Failed-Recipients
_FINAL_RECIPIENT_RE = re.compile(
    r"(?:Final-Recipient|Original-Recipient)\s*:\s*(?:rfc822\s*;\s*)?([^\s;,\r\n]+)",
    re.IGNORECASE,
)
_X_FAILED_RE = re.compile(r"X-Failed-Recipients\s*:\s*([^\r\n,]+)", re.IGNORECASE)
# Generic email-address pattern (used as fallback near 5xx codes).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Permanent SMTP error (5xx).
_SMTP_5XX_RE = re.compile(r"\b5[0-9]{2}\b|\b5\.[0-9]\.[0-9]\b")


def _extract_bounced_recipient(body_plain: str, body_html: str, snippet: str = "") -> str | None:
    """Try to extract the original recipient address from an NDR/bounce email.

    Tries, in order:
    1. DSN standard headers (Final-Recipient / Original-Recipient)
    2. X-Failed-Recipients header
    3. Email addresses found on lines containing a 5xx SMTP error code
    Returns the address in lower-case, or *None* if nothing is found.
    """
    text = body_plain or _strip_html_tags(body_html) or snippet

    # 1. DSN standard
    m = _FINAL_RECIPIENT_RE.search(text)
    if m:
        addr = m.group(1).strip().lower().strip("<>")
        if "@" in addr:
            return addr

    # 2. X-Failed-Recipients
    m = _X_FAILED_RE.search(text)
    if m:
        addr = m.group(1).strip().lower()
        if "@" in addr:
            return addr

    # 3. Email address near a 5xx error line
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SMTP_5XX_RE.search(line):
            window = "\n".join(lines[max(0, i - 2) : i + 3])
            for candidate in _EMAIL_RE.findall(window):
                cl = candidate.lower()
                if not _MAILER_DAEMON_RE.match(cl):
                    return cl

    return None
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Hidden preheader divs should not contribute to visible snippets.
_PREHEADER_RE = re.compile(
    r"<div[^>]*style=[^>]*display\s*:\s*none[^>]*>.*?</div>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_html_tags(text: str) -> str:
    """Strip HTML tags and decode entities to get plain text."""
    no_preheader = _PREHEADER_RE.sub("", text)
    no_tags = _HTML_TAG_RE.sub(" ", no_preheader)
    return _html_lib.unescape(no_tags)


def _strip_quoted(text: str) -> str:
    """Return text with quoted/reply sections removed."""
    m = _QUOTE_START_RE.search(text)
    if m:
        return text[: m.start()].rstrip()
    return text


def _body_snippet(plain: str, html: str, fallback: str = "") -> str:
    if plain:
        base = plain
    elif html:
        base = _strip_html_tags(html)
    else:
        base = _strip_html_tags(fallback) if "<" in fallback else fallback
    base = _strip_quoted(base)
    compact = " ".join(base.split())
    return compact[:180]


def _gmail_request_json(
    method: str,
    url: str,
    access_token: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if params:
        encoded = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}?{encoded}"

    body = None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=body, method=method, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:
            body_text = str(exc)
        raise GmailAPIError(exc.code, body_text) from exc
    except Exception as exc:
        raise GmailAPIError(0, str(exc)) from exc


def _gmail_get_profile(access_token: str) -> dict[str, Any]:
    return _gmail_request_json("GET", f"{GMAIL_API_ROOT}/profile", access_token)


def _gmail_list_message_ids(
    access_token: str,
    *,
    query: str | None = None,
    max_messages: int | None = None,
    include_spam_trash: bool = True,
) -> list[str]:
    out: list[str] = []
    page_token: str | None = None

    while True:
        params: dict[str, Any] = {
            "maxResults": 500,
            "includeSpamTrash": "true" if include_spam_trash else "false",
        }
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        payload = _gmail_request_json("GET", f"{GMAIL_API_ROOT}/messages", access_token, params=params)

        for item in payload.get("messages", []) or []:
            if isinstance(item, dict) and item.get("id"):
                out.append(str(item["id"]))
                if max_messages and len(out) >= max_messages:
                    return out

        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return out


def _gmail_list_all_message_ids(access_token: str, *, max_messages: int | None = None) -> list[str]:
    return _gmail_list_message_ids(access_token, max_messages=max_messages)


def _gmail_list_message_ids_in_window(
    access_token: str,
    *,
    start_dt: datetime,
    end_dt: datetime,
    max_messages: int | None = None,
) -> list[str]:
    start_unix = int(start_dt.timestamp())
    end_unix = int(end_dt.timestamp())
    query = f"after:{start_unix} before:{end_unix}"
    return _gmail_list_message_ids(access_token, query=query, max_messages=max_messages)


def _gmail_get_message(
    access_token: str,
    gmail_message_id: str,
    *,
    payload_format: str = "full",
) -> dict[str, Any]:
    params: dict[str, Any] = {"format": payload_format}
    if payload_format == "metadata":
        params["metadataHeaders"] = GMAIL_METADATA_HEADERS
    return _gmail_request_json(
        "GET",
        f"{GMAIL_API_ROOT}/messages/{gmail_message_id}",
        access_token,
        params=params,
    )


def _gmail_get_thread(
    access_token: str,
    thread_id: str,
    *,
    payload_format: str = "full",
) -> dict[str, Any]:
    params: dict[str, Any] = {"format": payload_format}
    if payload_format == "metadata":
        params["metadataHeaders"] = GMAIL_METADATA_HEADERS
    return _gmail_request_json(
        "GET",
        f"{GMAIL_API_ROOT}/threads/{thread_id}",
        access_token,
        params=params,
    )


@dataclass
class GmailHistoryDelta:
    added_ids: set[str]
    deleted_ids: set[str]
    touched_threads: set[str]
    latest_history_id: str


def _gmail_history_delta(access_token: str, start_history_id: str) -> GmailHistoryDelta:
    page_token: str | None = None
    added_ids: set[str] = set()
    deleted_ids: set[str] = set()
    touched_threads: set[str] = set()
    latest_history_id = start_history_id

    while True:
        params: dict[str, Any] = {
            "startHistoryId": start_history_id,
            "maxResults": 500,
            "historyTypes": ["messageAdded", "messageDeleted"],
        }
        if page_token:
            params["pageToken"] = page_token

        payload = _gmail_request_json("GET", f"{GMAIL_API_ROOT}/history", access_token, params=params)
        latest_history_id = str(payload.get("historyId") or latest_history_id)

        history_rows = payload.get("history", []) or []
        for row in history_rows:
            if not isinstance(row, dict):
                continue
            row_hid = row.get("id")
            if row_hid:
                latest_history_id = str(row_hid)

            for entry in row.get("messagesAdded", []) or []:
                msg = entry.get("message", {}) if isinstance(entry, dict) else {}
                msg_id = msg.get("id")
                thread_id = msg.get("threadId")
                if msg_id:
                    added_ids.add(str(msg_id))
                if thread_id:
                    touched_threads.add(str(thread_id))

            for entry in row.get("messagesDeleted", []) or []:
                msg = entry.get("message", {}) if isinstance(entry, dict) else {}
                msg_id = msg.get("id")
                thread_id = msg.get("threadId")
                if msg_id:
                    deleted_ids.add(str(msg_id))
                if thread_id:
                    touched_threads.add(str(thread_id))

            # Some history rows provide only "messages" for metadata changes.
            for msg in row.get("messages", []) or []:
                if isinstance(msg, dict):
                    mid = msg.get("id")
                    tid = msg.get("threadId")
                    if mid:
                        added_ids.add(str(mid))
                    if tid:
                        touched_threads.add(str(tid))

        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return GmailHistoryDelta(
        added_ids=added_ids,
        deleted_ids=deleted_ids,
        touched_threads=touched_threads,
        latest_history_id=latest_history_id,
    )


def _gmail_register_watch(access_token: str, topic_name: str) -> tuple[str, datetime | None]:
    payload = {"topicName": topic_name}
    res = _gmail_request_json("POST", f"{GMAIL_API_ROOT}/watch", access_token, payload=payload)
    history_id = str(res.get("historyId", ""))
    expiration_raw = res.get("expiration")
    expiration_dt: datetime | None = None
    if expiration_raw:
        try:
            expiration_dt = datetime.utcfromtimestamp(int(expiration_raw) / 1000.0)
        except Exception:
            expiration_dt = None
    return history_id, expiration_dt


async def _get_or_create_sync_state(db: AsyncSession, inbox_id: int) -> GmailSyncState:
    res = await db.execute(select(GmailSyncState).where(GmailSyncState.inbox_id == inbox_id))
    state = res.scalar_one_or_none()
    if state:
        return state
    state = GmailSyncState(inbox_id=inbox_id)
    db.add(state)
    await db.flush()
    return state


async def _ensure_access_token(db: AsyncSession, account: GmailAccount) -> str:
    now_utc = time_provider.utcnow()
    if account.token_expiry and account.token_expiry <= (now_utc + timedelta(minutes=5)):
        client_id, client_secret = await get_google_oauth_credentials(db)
        refreshed = refresh_access_token(account, client_id, client_secret)
        if not refreshed:
            # notify webhook; callers will usually catch the exception
            try:
                await maybe_fire_email_event(
                    db,
                    "token_expired",
                    {"inbox_id": account.inbox_id, "at": now_utc.isoformat()},
                )
            except Exception:
                log.exception("failed firing token_expired webhook")
            raise RuntimeError(f"Could not refresh Gmail access token for inbox_id={account.inbox_id}")
        await db.flush()
    return account.access_token


async def _refresh_gmail_token(db: AsyncSession, account: GmailAccount) -> bool:
    client_id, client_secret = await get_google_oauth_credentials(db)
    refreshed = refresh_access_token(account, client_id, client_secret)
    if refreshed:
        await db.flush()
    return bool(refreshed)


async def _gmail_call_with_refresh(
    db: AsyncSession,
    account: GmailAccount,
    func,
    *args,
    **kwargs,
):
    """Call a Gmail API helper, refreshing once on auth errors."""
    try:
        return await asyncio.to_thread(func, account.access_token, *args, **kwargs)
    except GmailAPIError as exc:
        if exc.status_code in (401, 403):
            refreshed = await _refresh_gmail_token(db, account)
            if refreshed:
                return await asyncio.to_thread(func, account.access_token, *args, **kwargs)
            try:
                await maybe_fire_email_event(
                    db,
                    "token_expired",
                    {"inbox_id": account.inbox_id, "at": time_provider.utcnow().isoformat()},
                )
            except Exception:
                log.exception("failed firing token_expired webhook after Gmail auth failure")
        raise


async def _fire_lead_reply_webhook_bg(data: dict[str, Any]) -> None:
    """Fire the lead-reply webhook in a background task with its own DB session."""
    try:
        async with AsyncSessionLocal() as session:
            await fire_lead_reply_webhook(session, data)
    except Exception as exc:
        log.warning("Background lead-reply webhook task failed: %s", exc)


async def _classify_and_notify_bg(
    lead_id: int,
    lead_email: str,
    lead_name: str,
    campaign_ids: list[int],
    reply_text: str,
    thread_id: str = "",
) -> None:
    """Background task: classify a lead reply with AI and fire interest webhooks.

    On classification failure the normal lead.replied webhook was already sent,
    so we simply log and move on.
    """
    try:
        from app.ai_classifier import classify_reply, is_ai_enabled
        from app.webhooks import fire_webhook_event

        async with AsyncSessionLocal() as session:
            if not await is_ai_enabled(session):
                return

            # Fetch full thread messages for richer AI context
            thread_messages: list[dict] = []
            if thread_id:
                from app.models import GmailMessage as _GM
                import json as _json
                msgs_res = await session.execute(
                    select(_GM)
                    .where(_GM.thread_id == thread_id)
                    .order_by(_GM.internal_date.asc())
                )
                for gm in msgs_res.scalars().all():
                    ts_ms = gm.internal_date
                    ts_str = ""
                    if ts_ms:
                        from datetime import datetime as _dt
                        try:
                            ts_str = _dt.utcfromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%d %H:%M UTC")
                        except Exception:
                            ts_str = str(ts_ms)
                    # Extract From header
                    frm = ""
                    try:
                        headers = _json.loads(gm.headers_json or "[]")
                        for h in headers:
                            if isinstance(h, dict) and h.get("name", "").lower() == "from":
                                frm = h.get("value", "")
                                break
                    except Exception:
                        pass
                    body = gm.body_plain or ""
                    if not body and gm.body_html:
                        # Strip basic HTML tags for plain text fallback
                        import re as _re
                        body = _re.sub(r'<[^>]+>', ' ', gm.body_html)
                    thread_messages.append({"from": frm, "timestamp": ts_str, "body": body})

            # Fallback: look up the original email subject/body if no thread messages
            email_subject = ""
            email_body = ""
            if not thread_messages and thread_id and lead_id:
                from app.models import Sequence as _Seq
                first_log_res = await session.execute(
                    select(EmailLog)
                    .where(
                        EmailLog.thread_id == thread_id,
                        EmailLog.lead_id == lead_id,
                    )
                    .order_by(EmailLog.sent_at.asc())
                    .limit(1)
                )
                first_log = first_log_res.scalar_one_or_none()
                if first_log:
                    email_subject = first_log.subject or ""
                    seq_res = await session.execute(
                        select(_Seq).where(
                            _Seq.campaign_id == first_log.campaign_id,
                            _Seq.position == first_log.sequence_index,
                        )
                    )
                    seq = seq_res.scalar_one_or_none()
                    if seq:
                        email_body = seq.body or ""

            classification = await classify_reply(
                session, reply_text,
                email_subject=email_subject,
                email_body=email_body,
                thread_messages=thread_messages or None,
            )
            if classification is None:
                return  # classifier failed — normal webhook already sent

            from app.models import CampaignLead as _CL

            for camp_id in campaign_ids:
                cl_res = await session.execute(
                    select(_CL).where(
                        _CL.lead_id == lead_id,
                        _CL.campaign_id == camp_id,
                    )
                )
                cl = cl_res.scalar_one_or_none()
                if not cl:
                    continue
                if classification == "unsubscribed":
                    cl.enrollment_status = "unsubscribed"
                    cl.interest_status = None
                elif classification == "wrong_person":
                    cl.enrollment_status = "wrong_person"
                    cl.interest_status = None
                elif classification in ("not_interested", "out_of_office"):
                    cl.interest_status = classification
                elif classification == "auto_reply":
                    cl.interest_status = "auto_reply"
                elif classification == "interested":
                    cl.interest_status = "interested"

            await session.flush()
            from app.routers.schedule import recalculate_all_campaigns

            await recalculate_all_campaigns(session)
            await session.commit()

            # Fire the appropriate webhook event
            event_type = f"lead.{classification}"
            for camp_id in campaign_ids:
                webhook_data = {
                    "lead_id": lead_id,
                    "lead_email": lead_email,
                    "lead_name": lead_name,
                    "campaign_id": camp_id,
                    "classification": classification,
                    "reply_snippet": reply_text[:500],
                }
                await fire_webhook_event(session, event_type, webhook_data)

    except Exception as exc:
        log.warning("Background AI classification task failed: %s", exc)


async def _upsert_thread(
    db: AsyncSession,
    *,
    inbox_id: int,
    thread_id: str,
    history_id: str = "",
    snippet: str = "",
    last_internal_date: int | None = None,
) -> GmailThread:
    res = await db.execute(
        select(GmailThread).where(
            GmailThread.inbox_id == inbox_id,
            GmailThread.thread_id == thread_id,
        )
    )
    thread = res.scalar_one_or_none()
    if thread is None:
        thread = GmailThread(
            inbox_id=inbox_id,
            thread_id=thread_id,
            history_id=history_id or "",
            snippet=snippet or "",
            last_internal_date=last_internal_date,
        )
        db.add(thread)
        await db.flush()
        return thread

    if history_id:
        thread.history_id = history_id
    if snippet:
        thread.snippet = snippet
    if last_internal_date is not None and (
        thread.last_internal_date is None or last_internal_date >= thread.last_internal_date
    ):
        thread.last_internal_date = last_internal_date
    return thread


async def _upsert_message_from_gmail(
    db: AsyncSession,
    *,
    inbox_id: int,
    gmail_message: dict[str, Any],
    include_body: bool = True,
) -> tuple[GmailMessage, bool]:
    gmail_msg_id = str(gmail_message.get("id", ""))
    thread_id = str(gmail_message.get("threadId", ""))
    if not gmail_msg_id or not thread_id:
        raise ValueError("Gmail message payload missing id/threadId")

    internal_date_raw = gmail_message.get("internalDate")
    internal_date = int(internal_date_raw) if internal_date_raw not in (None, "") else None
    snippet = _strip_quoted(str(gmail_message.get("snippet", "")))
    history_id = str(gmail_message.get("historyId", ""))
    labels = gmail_message.get("labelIds", []) or []
    payload = gmail_message.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    headers_json = json.dumps(headers)
    label_ids_json = json.dumps(labels)
    body_plain = ""
    body_html = ""
    if include_body:
        body_plain, body_html = _extract_bodies(payload)

    await _upsert_thread(
        db,
        inbox_id=inbox_id,
        thread_id=thread_id,
        history_id=history_id,
        snippet=snippet,
        last_internal_date=internal_date,
    )

    res = await db.execute(
        select(GmailMessage).where(
            GmailMessage.inbox_id == inbox_id,
            GmailMessage.message_id == gmail_msg_id,
        )
    )
    row = res.scalar_one_or_none()
    created = False
    if row is None:
        row = GmailMessage(
            inbox_id=inbox_id,
            message_id=gmail_msg_id,
            thread_id=thread_id,
            internal_date=internal_date,
            snippet=snippet,
            headers_json=headers_json,
            label_ids_json=label_ids_json,
            body_fetched=include_body,
            body_plain=body_plain,
            body_html=body_html,
        )
        db.add(row)
        created = True
    else:
        row.thread_id = thread_id
        row.internal_date = internal_date
        row.snippet = snippet
        row.headers_json = headers_json
        row.label_ids_json = label_ids_json
        if include_body:
            row.body_fetched = True
            row.body_plain = body_plain
            row.body_html = body_html

    # ---------- Lead-reply detection (new received messages only) ----------
    if created and "SENT" not in [lbl.upper() for lbl in labels]:
        from_email_addr = _extract_email_only(
            _header_value(headers_json, "From")
        )
        lead: Lead | None = None
        campaign_ids_from_thread_only: list[int] | None = None

        if from_email_addr:
            lead_res = await db.execute(
                select(Lead).where(func.lower(Lead.email) == from_email_addr)
            )
            lead = lead_res.scalar_one_or_none()

        if lead is None:
            pair = await _single_lead_campaign_pair_for_thread(db, thread_id)
            if pair is not None:
                lid, cid = pair
                lead_res_fb = await db.execute(select(Lead).where(Lead.id == lid))
                lead = lead_res_fb.scalar_one_or_none()
                if lead is not None:
                    campaign_ids_from_thread_only = [cid]

        if lead is not None:
            reply_from_addr = from_email_addr or _extract_email_only(
                _header_value(headers_json, "From")
            ) or (lead.email or "").lower()

            # Mark the thread as a lead thread & unread.
            thread_res = await db.execute(
                select(GmailThread).where(
                    GmailThread.inbox_id == inbox_id,
                    GmailThread.thread_id == thread_id,
                )
            )
            thread_obj = thread_res.scalar_one_or_none()
            if thread_obj is not None:
                thread_obj.is_lead_thread = True
                thread_obj.unread_lead_reply = True

            if campaign_ids_from_thread_only is not None:
                campaign_ids = list(campaign_ids_from_thread_only)
            else:
                # Find which campaign(s) this thread belongs to via EmailLog,
                # then record a LeadReply so stop_on_reply works correctly.
                # Primary lookup: match by Gmail thread_id stored in EmailLog.
                log_res = await db.execute(
                    select(EmailLog.campaign_id).where(
                        EmailLog.thread_id == thread_id,
                        EmailLog.lead_id == lead.id,
                    ).distinct()
                )
                campaign_ids = [r[0] for r in log_res.all()]

                # Fallback: if no EmailLog row carries this thread_id (e.g. reply
                # came before the first send or thread_id wasn't recorded), mark
                # all campaigns the lead is currently enrolled in.
                if not campaign_ids:
                    cl_res = await db.execute(
                        select(CampaignLead.campaign_id).where(
                            CampaignLead.lead_id == lead.id
                        )
                    )
                    campaign_ids = [r[0] for r in cl_res.all()]

            for camp_id in campaign_ids:
                existing_reply = await db.execute(
                    select(LeadReply).where(
                        LeadReply.lead_id == lead.id,
                        LeadReply.campaign_id == camp_id,
                    )
                )
                if existing_reply.scalar_one_or_none() is None:
                    db.add(LeadReply(lead_id=lead.id, campaign_id=camp_id))
                cl_touch = await db.execute(
                    select(CampaignLead).where(
                        CampaignLead.lead_id == lead.id,
                        CampaignLead.campaign_id == camp_id,
                    )
                )
                _clt = cl_touch.scalar_one_or_none()
                if _clt is not None and _clt.enrollment_status == "active":
                    _clt.enrollment_status = "contacted"

            # Delete all remaining QueueSlots for this lead+campaign so
            # the queue is clean and the send job doesn't re-skip them.
            if campaign_ids:
                cl_ids_res = await db.execute(
                    select(CampaignLead.id).where(
                        CampaignLead.lead_id == lead.id,
                        CampaignLead.campaign_id.in_(campaign_ids),
                    )
                )
                cl_ids = [r[0] for r in cl_ids_res.all()]
                if cl_ids:
                    await db.execute(
                        delete(QueueSlot).where(
                            QueueSlot.campaign_lead_id.in_(cl_ids)
                        )
                    )

            await db.flush()
            # Resolve inbox email for the webhook payload.
            inbox_res = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
            inbox_obj = inbox_res.scalar_one_or_none()
            webhook_data: dict[str, Any] = {
                "lead_email": reply_from_addr,
                "lead_id": lead.id,
                "lead_name": lead.name or "",
                "thread_id": thread_id,
                "inbox_id": inbox_id,
                "inbox_email": inbox_obj.email if inbox_obj else "",
                "message_id": gmail_msg_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            # SSE notification fires immediately (in-memory, no commit needed).
            asyncio.create_task(
                unibox_events.publish(
                    {
                        "type": "unibox.notification",
                        "reason": "lead_reply",
                        "inbox_id": inbox_id,
                        "thread_id": thread_id,
                        "lead_id": lead.id,
                        "lead_email": reply_from_addr,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                )
            )
            # Webhook fires in the background after current transaction commits.
            asyncio.create_task(_fire_lead_reply_webhook_bg(webhook_data))
            # AI classification fires in a separate background task.
            # It uses the body of the reply message for classification.
            reply_body = body_plain or body_html or snippet or ""
            asyncio.create_task(
                _classify_and_notify_bg(
                    lead_id=lead.id,
                    lead_email=reply_from_addr,
                    lead_name=lead.name or "",
                    campaign_ids=campaign_ids,
                    reply_text=reply_body,
                    thread_id=thread_id,
                )
            )
    # -----------------------------------------------------------------------

    # ---------- NDR / bounce detection (mailer-daemon messages) ----------
    # Delayed bounces arrive as a real email FROM mailer-daemon / postmaster
    # rather than as an immediate API error.  Detect them during inbox sync and
    # mark the affected lead as bounced so it is excluded from future sends.
    if created and "SENT" not in [lbl.upper() for lbl in labels]:
        from_hdr = _header_value(headers_json, "From")
        from_addr_ndr = _extract_email_only(from_hdr) or from_hdr.strip()
        if _MAILER_DAEMON_RE.match(from_addr_ndr):
            subject_hdr = _header_value(headers_json, "Subject")
            # Accept any mailer-daemon sender or a subject that clearly
            # indicates a delivery failure.
            if not subject_hdr or _BOUNCE_SUBJECT_RE.search(subject_hdr):
                bounced_addr = _extract_bounced_recipient(body_plain, body_html, snippet)
                if bounced_addr:
                    b_lead_res = await db.execute(
                        select(Lead).where(func.lower(Lead.email) == bounced_addr)
                    )
                    b_lead = b_lead_res.scalar_one_or_none()
                    if b_lead is not None:
                        b_rows = (
                            await db.execute(
                                select(CampaignLead).where(CampaignLead.lead_id == b_lead.id)
                            )
                        ).scalars().all()
                        if any(
                            getattr(r, "enrollment_status", None) not in ("bounced", "unsubscribed")
                            for r in b_rows
                        ):
                            log.info(
                                "NDR bounce detected for lead_id=%s email=%s (inbox_id=%s)",
                                b_lead.id, bounced_addr, inbox_id,
                            )
                            for _bcl in b_rows:
                                _bcl.enrollment_status = "bounced"
                            b_cl_ids = [r.id for r in b_rows]
                            if b_cl_ids:
                                await db.execute(
                                    delete(QueueSlot).where(
                                        QueueSlot.campaign_lead_id.in_(b_cl_ids)
                                    )
                                )
                            await db.flush()
                            b_camp_ids = list({r.campaign_id for r in b_rows})
                            for b_camp_id in b_camp_ids:
                                try:
                                    from app.webhooks import fire_webhook_event as _fwe
                                    await _fwe(db, "email.bounced", {
                                        "lead_id": b_lead.id,
                                        "lead_email": b_lead.email,
                                        "campaign_id": b_camp_id,
                                        "inbox_id": inbox_id,
                                        "error_type": "bounce",
                                        "error_message": snippet[:300] or subject_hdr,
                                        "timestamp": time_provider.utcnow().isoformat() + "Z",
                                    })
                                    await _fwe(db, "lead.status_changed", {
                                        "lead_id": b_lead.id,
                                        "lead_email": b_lead.email,
                                        "campaign_id": b_camp_id,
                                        "old_enrollment_status": "contacted",
                                        "new_enrollment_status": "bounced",
                                        "reason": f"NDR from {from_addr_ndr}",
                                        "timestamp": time_provider.utcnow().isoformat() + "Z",
                                    })
                                except Exception:
                                    log.exception(
                                        "Failed to fire bounce webhook for lead_id=%s", b_lead.id
                                    )
    # -----------------------------------------------------------------------

    # ---------- Sent-to-lead detection (outbound messages only) ----------
    # Mark the thread as a lead thread when we sent the email to a lead,
    # even if the lead hasn't replied yet — so the unibox shows it.
    if created and "SENT" in [lbl.upper() for lbl in labels]:
        to_email_addr = _extract_email_only(
            _header_value(headers_json, "To")
        )
        if to_email_addr:
            to_lead_res = await db.execute(
                select(Lead).where(func.lower(Lead.email) == to_email_addr)
            )
            to_lead = to_lead_res.scalar_one_or_none()
            if to_lead is not None:
                sent_thread_res = await db.execute(
                    select(GmailThread).where(
                        GmailThread.inbox_id == inbox_id,
                        GmailThread.thread_id == thread_id,
                    )
                )
                sent_thread_obj = sent_thread_res.scalar_one_or_none()
                if sent_thread_obj is not None and not sent_thread_obj.is_lead_thread:
                    sent_thread_obj.is_lead_thread = True
                    await db.flush()
    # -----------------------------------------------------------------------

    await db.flush()
    return row, created


async def upsert_sent_message(
    db: AsyncSession,
    *,
    inbox_id: int,
    thread_id: str,
    gmail_message_id: str | None,
    rfc_message_id: str,
    subject: str,
    to_email: str,
    from_email: str,
    body: str,
    is_html: bool,
) -> GmailMessage:
    now_ms = _now_epoch_ms()
    # Keep optimistic sent messages in final visual position immediately.
    # If local clock/time-offset is behind Gmail timestamps, force monotonic
    # order by pinning this message after the latest known thread message.
    last_internal_res = await db.execute(
        select(func.max(GmailMessage.internal_date)).where(
            GmailMessage.inbox_id == inbox_id,
            GmailMessage.thread_id == thread_id,
        )
    )
    last_internal_date = last_internal_res.scalar_one()
    if last_internal_date is not None and now_ms <= int(last_internal_date):
        now_ms = int(last_internal_date) + 1

    snippet = _body_snippet("" if is_html else body, body if is_html else "", fallback=body)
    headers = [
        {"name": "From", "value": from_email},
        {"name": "To", "value": to_email},
        {"name": "Subject", "value": subject},
        {"name": "Message-Id", "value": rfc_message_id},
        {"name": "Date", "value": time_provider.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")},
    ]
    headers_json = json.dumps(headers)

    message_pk = (gmail_message_id or "").strip()
    if not message_pk:
        digest = hashlib.sha1(rfc_message_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
        message_pk = f"local-{digest}"

    await _upsert_thread(
        db,
        inbox_id=inbox_id,
        thread_id=thread_id,
        snippet=snippet,
        last_internal_date=now_ms,
    )

    res = await db.execute(
        select(GmailMessage).where(
            GmailMessage.inbox_id == inbox_id,
            GmailMessage.message_id == message_pk,
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        row = GmailMessage(
            inbox_id=inbox_id,
            message_id=message_pk,
            thread_id=thread_id,
            internal_date=now_ms,
            snippet=snippet,
            headers_json=headers_json,
            label_ids_json=json.dumps(["SENT"]),
            body_fetched=True,
            body_plain="" if is_html else body,
            body_html=body if is_html else "",
        )
        db.add(row)
    else:
        row.thread_id = thread_id
        row.internal_date = now_ms
        row.snippet = snippet
        row.headers_json = headers_json
        row.label_ids_json = json.dumps(["SENT"])
        row.body_fetched = True
        row.body_plain = "" if is_html else body
        row.body_html = body if is_html else ""

    await db.flush()
    return row


async def upsert_sent_o365_message(
    db: AsyncSession,
    *,
    inbox_id: int,
    conversation_id: str,
    internet_message_id: str,
    subject: str,
    to_email: str,
    from_email: str,
    body: str,
    is_html: bool,
) -> Office365Message | None:
    """Immediately save a just-sent Office 365 message to the local mirror.

    This is the O365 equivalent of ``upsert_sent_message`` for Gmail: it lets
    the unibox thread view show the outbound email right away, before the next
    scheduled sync cycle picks it up from SentItems.

    We use a ``local-<hash>`` prefix as the message_id PK since we don't have
    the Graph API message id at send time.  The real entry (with the proper
    Graph id) will be created when SentItems are synced; the local copy will
    remain as a harmless extra record until then, since thread views are
    ordered and deduplicated by ``internet_message_id`` in the UI.

    Returns None when *conversation_id* is empty (first email where Microsoft
    hasn't yet assigned a conversation; the sync will create the entry later).
    """
    if not conversation_id:
        return None

    now_dt = time_provider.utcnow()

    # Use a stable hash of the RFC Message-ID as the local surrogate PK.
    digest = hashlib.sha1(internet_message_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    message_pk = f"local-{digest}"

    body_html = body if is_html else ""
    body_plain = _strip_html_tags(body) if is_html else body

    # Ensure the thread row exists / is up-to-date.
    thread = await _upsert_o365_thread(db, inbox_id, conversation_id, subject, now_dt)

    # Check if a local entry already exists (idempotent).
    existing_res = await db.execute(
        select(Office365Message).where(
            Office365Message.inbox_id == inbox_id,
            Office365Message.message_id == message_pk,
        )
    )
    existing = existing_res.scalar_one_or_none()
    if existing:
        return existing

    row = Office365Message(
        inbox_id=inbox_id,
        message_id=message_pk,
        conversation_id=conversation_id,
        internet_message_id=internet_message_id,
        received_at=now_dt,
        subject=subject,
        from_address=from_email.lower(),
        to_addresses=json.dumps([to_email.lower()]),
        body_plain=body_plain,
        body_html=body_html,
        is_read=True,
        has_attachments=False,
    )
    db.add(row)

    # Mark the thread as a lead thread if the recipient is a known lead.
    lead_res = await db.execute(select(Lead).where(func.lower(Lead.email) == to_email.lower()))
    to_lead = lead_res.scalar_one_or_none()
    if to_lead is not None and not thread.is_lead_thread:
        thread.is_lead_thread = True

    await db.flush()
    return row


async def _delete_message(db: AsyncSession, inbox_id: int, message_id: str) -> str | None:
    res = await db.execute(
        select(GmailMessage).where(
            GmailMessage.inbox_id == inbox_id,
            GmailMessage.message_id == message_id,
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        return None
    thread_id = row.thread_id
    await db.delete(row)
    return thread_id


async def _cleanup_thread_if_empty(db: AsyncSession, inbox_id: int, thread_id: str) -> None:
    count_res = await db.execute(
        select(func.count())
        .select_from(GmailMessage)
        .where(GmailMessage.inbox_id == inbox_id, GmailMessage.thread_id == thread_id)
    )
    msg_count = count_res.scalar_one() or 0
    if msg_count > 0:
        last_res = await db.execute(
            select(func.max(GmailMessage.internal_date)).where(
                GmailMessage.inbox_id == inbox_id,
                GmailMessage.thread_id == thread_id,
            )
        )
        latest = last_res.scalar_one()
        thread_res = await db.execute(
            select(GmailThread).where(
                GmailThread.inbox_id == inbox_id,
                GmailThread.thread_id == thread_id,
            )
        )
        thread = thread_res.scalar_one_or_none()
        if thread:
            thread.last_internal_date = latest
        return

    thread_res = await db.execute(
        select(GmailThread).where(
            GmailThread.inbox_id == inbox_id,
            GmailThread.thread_id == thread_id,
        )
    )
    thread = thread_res.scalar_one_or_none()
    if thread:
        await db.delete(thread)


def _unibox_lead_status_filter_criterion(lead_sq, lead_status: str):
    """Filter unibox rows by enrollment, replied (LeadReply), or interest_status."""
    ls = (lead_status or "").strip().lower()
    if ls in ENROLLMENT_STATUSES:
        return lead_sq.c.lead_status == ls
    if ls == "replied":
        return exists(
            select(1).select_from(LeadReply).where(
                LeadReply.lead_id == lead_sq.c.lead_id,
                LeadReply.campaign_id == lead_sq.c.campaign_id,
            )
        )
    if ls in LEAD_INTERESTS:
        return lead_sq.c.interest_status == ls
    return false()


async def list_unibox_conversations(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    leads_only: bool = False,
    lead_status: str | None = None,
) -> dict[str, Any]:
    offset = (page - 1) * page_size

    # Shared: one lead per thread (works for both Gmail thread_id and O365 conversation_id
    # because EmailLog.thread_id stores the conversation id for both providers).
    # Pipeline badge uses CampaignLead.enrollment_status (not legacy Lead.status).
    lead_sq = (
        select(
            EmailLog.thread_id.label("thread_id"),
            Lead.email.label("lead_email"),
            CampaignLead.enrollment_status.label("lead_status"),
            EmailLog.lead_id.label("lead_id"),
            EmailLog.campaign_id.label("campaign_id"),
            CampaignLead.interest_status.label("interest_status"),
            func.row_number()
            .over(
                partition_by=EmailLog.thread_id,
                order_by=EmailLog.id,
            )
            .label("rn"),
        )
        .join(Lead, Lead.id == EmailLog.lead_id)
        .join(
            CampaignLead,
            and_(
                CampaignLead.lead_id == EmailLog.lead_id,
                CampaignLead.campaign_id == EmailLog.campaign_id,
            ),
        )
        .where(EmailLog.thread_id.isnot(None))
        .subquery()
    )

    # ── Gmail threads ─────────────────────────────────────────────────────
    latest_message_sq = (
        select(
            GmailMessage.inbox_id.label("inbox_id"),
            GmailMessage.thread_id.label("thread_id"),
            GmailMessage.snippet.label("snippet"),
            GmailMessage.headers_json.label("headers_json"),
            GmailMessage.internal_date.label("internal_date"),
            func.row_number()
            .over(
                partition_by=(GmailMessage.inbox_id, GmailMessage.thread_id),
                order_by=(desc(GmailMessage.internal_date), desc(GmailMessage.message_id)),
            )
            .label("rn"),
        )
        .subquery()
    )

    gmail_stmt = (
        select(
            GmailThread.inbox_id,
            GmailThread.thread_id,
            GmailThread.last_internal_date,
            GmailThread.snippet.label("thread_snippet"),
            GmailThread.is_lead_thread,
            GmailThread.unread_lead_reply,
            Inbox.email.label("account_email"),
            latest_message_sq.c.snippet.label("last_snippet"),
            latest_message_sq.c.headers_json.label("last_headers_json"),
            lead_sq.c.lead_email.label("lead_email"),
            lead_sq.c.lead_status.label("lead_status"),
        )
        .join(Inbox, Inbox.id == GmailThread.inbox_id)
        .outerjoin(
            latest_message_sq,
            and_(
                latest_message_sq.c.inbox_id == GmailThread.inbox_id,
                latest_message_sq.c.thread_id == GmailThread.thread_id,
                latest_message_sq.c.rn == 1,
            ),
        )
        .outerjoin(
            lead_sq,
            and_(
                lead_sq.c.thread_id == GmailThread.thread_id,
                lead_sq.c.rn == 1,
            ),
        )
    )
    if leads_only:
        gmail_stmt = gmail_stmt.where(GmailThread.is_lead_thread.is_(True))
    if lead_status:
        gmail_stmt = gmail_stmt.where(_unibox_lead_status_filter_criterion(lead_sq, lead_status))

    gmail_rows = (await db.execute(gmail_stmt)).all()

    # ── Office 365 threads ────────────────────────────────────────────────
    o365_latest_msg_sq = (
        select(
            Office365Message.inbox_id.label("inbox_id"),
            Office365Message.conversation_id.label("thread_id"),
            Office365Message.body_plain.label("snippet"),
            func.row_number()
            .over(
                partition_by=(Office365Message.inbox_id, Office365Message.conversation_id),
                order_by=desc(Office365Message.received_at),
            )
            .label("rn"),
        )
        .subquery()
    )

    o365_stmt = (
        select(
            Office365Thread.inbox_id,
            Office365Thread.conversation_id.label("thread_id"),
            Office365Thread.last_received_at,
            Office365Thread.subject,
            Office365Thread.is_lead_thread,
            Office365Thread.unread_lead_reply,
            Inbox.email.label("account_email"),
            o365_latest_msg_sq.c.snippet.label("last_snippet"),
            lead_sq.c.lead_email.label("lead_email"),
            lead_sq.c.lead_status.label("lead_status"),
        )
        .join(Inbox, Inbox.id == Office365Thread.inbox_id)
        .outerjoin(
            o365_latest_msg_sq,
            and_(
                o365_latest_msg_sq.c.inbox_id == Office365Thread.inbox_id,
                o365_latest_msg_sq.c.thread_id == Office365Thread.conversation_id,
                o365_latest_msg_sq.c.rn == 1,
            ),
        )
        .outerjoin(
            lead_sq,
            and_(
                lead_sq.c.thread_id == Office365Thread.conversation_id,
                lead_sq.c.rn == 1,
            ),
        )
    )
    if leads_only:
        o365_stmt = o365_stmt.where(Office365Thread.is_lead_thread.is_(True))
    if lead_status:
        o365_stmt = o365_stmt.where(_unibox_lead_status_filter_criterion(lead_sq, lead_status))

    o365_rows = (await db.execute(o365_stmt)).all()

    # ── Merge both providers into a single sorted list ────────────────────
    items: list[dict[str, Any]] = []

    for row in gmail_rows:
        subject = _header_value(row.last_headers_json or "", "Subject") or "(no subject)"
        last_snippet = row.last_snippet or row.thread_snippet or ""
        ts_dt = _epoch_ms_to_dt(row.last_internal_date)
        items.append(
            {
                "thread_id": row.thread_id,
                "inbox_id": row.inbox_id,
                "inbox_account": row.account_email,
                "subject": subject,
                "last_message_snippet": last_snippet,
                "timestamp": _dt_to_iso(ts_dt),
                "_sort_ts": ts_dt.timestamp() if ts_dt else 0.0,
                "is_lead_thread": bool(row.is_lead_thread),
                "unread_lead_reply": bool(row.unread_lead_reply),
                "lead_email": row.lead_email or None,
                "lead_status": row.lead_status or None,
                "provider": "gmail",
            }
        )

    for row in o365_rows:
        ts_dt = row.last_received_at
        items.append(
            {
                "thread_id": row.thread_id,
                "inbox_id": row.inbox_id,
                "inbox_account": row.account_email,
                "subject": row.subject or "(no subject)",
                "last_message_snippet": (row.last_snippet or "")[:200],
                "timestamp": _dt_to_iso(ts_dt),
                "_sort_ts": ts_dt.timestamp() if ts_dt else 0.0,
                "is_lead_thread": bool(row.is_lead_thread),
                "unread_lead_reply": bool(row.unread_lead_reply),
                "lead_email": row.lead_email or None,
                "lead_status": row.lead_status or None,
                "provider": "office365",
            }
        )

    # Unread lead replies pinned to top, then most recent first.
    items.sort(key=lambda x: (int(x["unread_lead_reply"]), x["_sort_ts"]), reverse=True)

    total = len(items)
    page_items = items[offset: offset + page_size]
    for item in page_items:
        del item["_sort_ts"]

    return {
        "items": page_items,
        "page": page,
        "page_size": page_size,
        "total": total,
    }


async def get_notification_count(db: AsyncSession) -> int:
    """Return the number of threads with an unread lead reply (Gmail + Office 365)."""
    gmail_stmt = select(func.count()).select_from(GmailThread).where(
        GmailThread.unread_lead_reply.is_(True)
    )
    o365_stmt = select(func.count()).select_from(Office365Thread).where(
        Office365Thread.unread_lead_reply.is_(True)
    )
    gmail_count = int((await db.execute(gmail_stmt)).scalar_one() or 0)
    o365_count = int((await db.execute(o365_stmt)).scalar_one() or 0)
    return gmail_count + o365_count


async def mark_thread_read(
    db: AsyncSession,
    *,
    thread_id: str,
    inbox_id: int,
) -> bool:
    """Clear the unread_lead_reply flag on a thread (Gmail or Office 365).

    Returns True if the thread was found and updated, False otherwise.
    """
    # Try Gmail thread first
    res = await db.execute(
        select(GmailThread).where(
            GmailThread.inbox_id == inbox_id,
            GmailThread.thread_id == thread_id,
        )
    )
    thread = res.scalar_one_or_none()

    if thread is None:
        # Try Office 365 conversation
        o365_res = await db.execute(
            select(Office365Thread).where(
                Office365Thread.inbox_id == inbox_id,
                Office365Thread.conversation_id == thread_id,
            )
        )
        o365_thread = o365_res.scalar_one_or_none()
        if o365_thread is None:
            return False
        if o365_thread.unread_lead_reply:
            o365_thread.unread_lead_reply = False
            await db.flush()
            new_count = await get_notification_count(db)
            asyncio.create_task(
                unibox_events.publish(
                    {
                        "type": "unibox.notification.count",
                        "count": new_count,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                )
            )
        return True

    if thread.unread_lead_reply:
        thread.unread_lead_reply = False
        await db.flush()
        # Broadcast updated notification count via SSE.
        new_count = await get_notification_count(db)
        asyncio.create_task(
            unibox_events.publish(
                {
                    "type": "unibox.notification.count",
                    "count": new_count,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
            )
        )
    return True


async def _get_o365_thread_messages(
    db: AsyncSession,
    *,
    thread_id: str,
    inbox_id: int,
) -> dict[str, Any] | None:
    """Return a thread+messages dict for an Office 365 conversation."""
    thread_res = await db.execute(
        select(Office365Thread, Inbox.email)
        .join(Inbox, Inbox.id == Office365Thread.inbox_id)
        .where(
            Office365Thread.inbox_id == inbox_id,
            Office365Thread.conversation_id == thread_id,
        )
    )
    row = thread_res.first()
    if row is None:
        return None
    thread, account_email = row

    msg_rows = await db.execute(
        select(Office365Message)
        .where(
            Office365Message.inbox_id == inbox_id,
            Office365Message.conversation_id == thread_id,
        )
        .order_by(Office365Message.received_at.asc(), Office365Message.created_at.asc())
    )
    messages = msg_rows.scalars().all()

    inbox_email_lower = account_email.lower()
    out_messages: list[dict[str, Any]] = []
    for msg in messages:
        direction = "sent" if msg.from_address.lower() == inbox_email_lower else "received"
        try:
            to_str = ", ".join(json.loads(msg.to_addresses or "[]"))
        except Exception:
            to_str = msg.to_addresses or ""
        out_messages.append(
            {
                "message_id": msg.message_id,
                "thread_id": thread_id,
                "timestamp": _dt_to_iso(msg.received_at),
                "snippet": (msg.body_plain or "")[:200],
                "body_plain": msg.body_plain or "",
                "body_html": msg.body_html or "",
                "subject": msg.subject or "",
                "from": msg.from_address or "",
                "to": to_str,
                "direction": direction,
                "label_ids": [],
            }
        )

    return {
        "thread_id": thread_id,
        "inbox_id": inbox_id,
        "inbox_account": account_email,
        "subject": thread.subject or "(no subject)",
        "last_message_timestamp": _dt_to_iso(thread.last_received_at),
        "messages": out_messages,
    }


async def get_thread_messages(
    db: AsyncSession,
    *,
    thread_id: str,
    inbox_id: int | None = None,
) -> dict[str, Any] | None:
    chosen_inbox_id = inbox_id
    if chosen_inbox_id is None:
        inbox_rows = (
            await db.execute(
                select(GmailMessage.inbox_id)
                .where(GmailMessage.thread_id == thread_id)
                .distinct()
            )
        ).all()
        inbox_ids = [int(row[0]) for row in inbox_rows]
        if not inbox_ids:
            # Not in Gmail – try Office 365
            o365_rows = (
                await db.execute(
                    select(Office365Message.inbox_id)
                    .where(Office365Message.conversation_id == thread_id)
                    .distinct()
                )
            ).all()
            o365_inbox_ids = [int(row[0]) for row in o365_rows]
            if not o365_inbox_ids:
                return None
            if len(o365_inbox_ids) > 1:
                raise ValueError("thread_id exists in multiple inboxes; pass inbox_id explicitly")
            return await _get_o365_thread_messages(db, thread_id=thread_id, inbox_id=o365_inbox_ids[0])
        if len(inbox_ids) > 1:
            raise ValueError("thread_id exists in multiple inboxes; pass inbox_id explicitly")
        chosen_inbox_id = inbox_ids[0]

    thread_row = await db.execute(
        select(GmailThread, Inbox.email)
        .join(Inbox, Inbox.id == GmailThread.inbox_id)
        .where(
            GmailThread.inbox_id == chosen_inbox_id,
            GmailThread.thread_id == thread_id,
        )
    )
    thread_with_account = thread_row.first()
    if not thread_with_account:
        # Not a Gmail thread – try Office 365
        return await _get_o365_thread_messages(db, thread_id=thread_id, inbox_id=chosen_inbox_id)
    thread, account_email = thread_with_account

    msg_rows = await db.execute(
        select(GmailMessage)
        .where(
            GmailMessage.inbox_id == chosen_inbox_id,
            GmailMessage.thread_id == thread_id,
        )
        .order_by(GmailMessage.internal_date.asc(), GmailMessage.created_at.asc())
    )
    messages = msg_rows.scalars().all()

    out_messages: list[dict[str, Any]] = []
    subject = "(no subject)"
    for msg in messages:
        headers_json = msg.headers_json or "[]"
        msg_subject = _header_value(headers_json, "Subject")
        if msg_subject and subject == "(no subject)":
            subject = msg_subject
        labels = _parse_labels(msg.label_ids_json or "[]")
        direction = "sent" if "SENT" in labels else "received"
        out_messages.append(
            {
                "message_id": msg.message_id,
                "thread_id": msg.thread_id,
                "timestamp": _dt_to_iso(_epoch_ms_to_dt(msg.internal_date)),
                "snippet": msg.snippet,
                "body_plain": msg.body_plain,
                "body_html": msg.body_html,
                "subject": msg_subject or "",
                "from": _header_value(headers_json, "From"),
                "to": _header_value(headers_json, "To"),
                "direction": direction,
                "label_ids": labels,
            }
        )

    if subject == "(no subject)":
        fallback_subject = _header_value(messages[-1].headers_json if messages else "[]", "Subject")
        if fallback_subject:
            subject = fallback_subject

    return {
        "thread_id": thread_id,
        "inbox_id": chosen_inbox_id,
        "inbox_account": account_email,
        "subject": subject,
        "last_message_timestamp": _dt_to_iso(_epoch_ms_to_dt(thread.last_internal_date)),
        "messages": out_messages,
    }


async def _set_initial_list_sync_state(inbox_id: int, in_progress: bool) -> None:
    async with _initial_list_sync_lock:
        if in_progress:
            _inflight_initial_list_syncs.add(inbox_id)
        else:
            _inflight_initial_list_syncs.discard(inbox_id)
        inflight_count = len(_inflight_initial_list_syncs)

    await unibox_events.publish(
        {
            "type": "unibox.sync.status",
            "phase": "initial-list",
            "inbox_id": inbox_id,
            "in_progress": in_progress,
            "inflight_count": inflight_count,
            "timestamp": _dt_to_iso(time_provider.utcnow()),
        }
    )


async def get_unibox_sync_status(*, inbox_id: int | None = None) -> dict[str, Any]:
    async with _initial_list_sync_lock:
        inflight_ids = sorted(_inflight_initial_list_syncs)
    if inbox_id is not None:
        inflight_ids = [value for value in inflight_ids if value == inbox_id]
    return {
        "initial_list_sync_in_progress": bool(inflight_ids),
        "inflight_inbox_ids": inflight_ids,
    }


async def _thread_requires_body_hydration(db: AsyncSession, *, inbox_id: int, thread_id: str) -> bool:
    total_stmt = (
        select(func.count())
        .select_from(GmailMessage)
        .where(
            GmailMessage.inbox_id == inbox_id,
            GmailMessage.thread_id == thread_id,
        )
    )
    total_messages = int((await db.execute(total_stmt)).scalar_one() or 0)
    if total_messages == 0:
        return True

    missing_stmt = (
        select(func.count())
        .select_from(GmailMessage)
        .where(
            GmailMessage.inbox_id == inbox_id,
            GmailMessage.thread_id == thread_id,
            GmailMessage.body_fetched.is_(False),
        )
    )
    missing_messages = int((await db.execute(missing_stmt)).scalar_one() or 0)
    return missing_messages > 0


async def _hydrate_thread_from_gmail(
    db: AsyncSession,
    *,
    inbox_id: int,
    thread_id: str,
    account: GmailAccount,
) -> bool:
    try:
        payload = await _gmail_call_with_refresh(
            db,
            account,
            _gmail_get_thread,
            thread_id,
            payload_format="full",
        )
    except GmailAPIError as exc:
        if exc.status_code == 404:
            log.debug(
                "hydrate thread skipped missing thread %s for inbox_id=%s",
                thread_id,
                inbox_id,
            )
            return False
        raise

    raw_messages = payload.get("messages", []) or []
    hydrated_any = False
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        try:
            await _upsert_message_from_gmail(
                db,
                inbox_id=inbox_id,
                gmail_message=item,
                include_body=True,
            )
            hydrated_any = True
        except ValueError:
            continue
    return hydrated_any


async def hydrate_thread_on_demand(
    db: AsyncSession,
    *,
    thread_id: str,
    inbox_id: int,
) -> bool:
    if not await _thread_requires_body_hydration(db, inbox_id=inbox_id, thread_id=thread_id):
        return False

    account_res = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox_id))
    account = account_res.scalar_one_or_none()
    if account is None:
        return False

    try:
        await _ensure_access_token(db, account)
        hydrated = await _hydrate_thread_from_gmail(
            db,
            inbox_id=inbox_id,
            thread_id=thread_id,
            account=account,
        )
    except Exception:
        log.exception("On-demand thread hydration failed inbox_id=%s thread_id=%s", inbox_id, thread_id)
        return False

    if not hydrated:
        return False

    await db.flush()
    return True


async def hydrate_pending_threads_for_inbox(
    inbox_id: int,
    *,
    thread_ids: list[str] | None = None,
    reason: str = "initial-thread-hydration",
) -> int:
    if not await _acquire_hydration_inflight(inbox_id):
        return 0

    touched: set[tuple[int, str]] = set()
    try:
        async with AsyncSessionLocal() as db:
            inbox_res = await db.execute(select(Inbox).where(Inbox.id == inbox_id, Inbox.provider == "gmail"))
            inbox = inbox_res.scalar_one_or_none()
            if not inbox:
                await db.rollback()
                return 0

            account_res = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox_id))
            account = account_res.scalar_one_or_none()
            if account is None:
                await db.rollback()
                return 0

            await _ensure_access_token(db, account)

            candidate_ids = [str(item) for item in (thread_ids or []) if str(item).strip()]
            if not candidate_ids:
                pending_stmt = (
                    select(GmailMessage.thread_id)
                    .where(
                        GmailMessage.inbox_id == inbox_id,
                        GmailMessage.body_fetched.is_(False),
                    )
                    .group_by(GmailMessage.thread_id)
                    .order_by(desc(func.max(GmailMessage.internal_date)))
                )
                pending_rows = (await db.execute(pending_stmt)).all()
                candidate_ids = [str(row[0]) for row in pending_rows if row and row[0]]

            if not candidate_ids:
                await db.rollback()
                return 0

            # Preserve order while removing duplicates.
            ordered_ids = list(dict.fromkeys(candidate_ids))

            for idx, thread_id in enumerate(ordered_ids, start=1):
                if not await _thread_requires_body_hydration(db, inbox_id=inbox_id, thread_id=thread_id):
                    continue
                try:
                    hydrated = await _hydrate_thread_from_gmail(
                        db,
                        inbox_id=inbox_id,
                        thread_id=thread_id,
                        account=account,
                    )
                except Exception:
                    log.exception(
                        "Background thread hydration failed inbox_id=%s thread_id=%s",
                        inbox_id,
                        thread_id,
                    )
                    continue

                if hydrated:
                    touched.add((inbox_id, thread_id))

                if idx % THREAD_HYDRATION_COMMIT_INTERVAL == 0:
                    await db.commit()

            await db.commit()
    except Exception:
        log.exception("Background hydration runner failed inbox_id=%s", inbox_id)
        return 0
    finally:
        await _release_hydration_inflight(inbox_id)

    for i_id, thread_id in touched:
        await unibox_events.publish(
            {
                "type": "unibox.thread.updated",
                "reason": reason,
                "inbox_id": i_id,
                "thread_id": thread_id,
                "timestamp": _dt_to_iso(time_provider.utcnow()),
            }
        )
    return len(touched)


async def queue_thread_hydration_for_inbox(
    inbox_id: int,
    *,
    thread_ids: list[str] | None = None,
    reason: str = "initial-thread-hydration",
) -> None:
    queued_thread_ids = list(dict.fromkeys([str(item) for item in (thread_ids or []) if str(item).strip()]))

    async def _runner() -> None:
        await hydrate_pending_threads_for_inbox(
            inbox_id,
            thread_ids=queued_thread_ids or None,
            reason=reason,
        )

    asyncio.create_task(_runner())


async def _sync_inbox(
    db: AsyncSession,
    inbox: Inbox,
    reason: str,
) -> tuple[set[tuple[int, str]], set[str]]:
    touched_threads: set[tuple[int, str]] = set()
    hydrate_after_commit_thread_ids: set[str] = set()

    account_res = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox.id))
    account = account_res.scalar_one_or_none()
    if account is None:
        return touched_threads, hydrate_after_commit_thread_ids

    state = await _get_or_create_sync_state(db, inbox.id)
    await _ensure_access_token(db, account)

    profile = await _gmail_call_with_refresh(db, account, _gmail_get_profile)
    profile_history_id = str(profile.get("historyId", ""))

    do_full_sync = not state.anchor_history_id
    delta: GmailHistoryDelta | None = None
    if not do_full_sync:
        start_history_id = state.latest_history_id or state.last_history_id or state.anchor_history_id
        try:
            delta = await _gmail_call_with_refresh(db, account, _gmail_history_delta, start_history_id)
        except GmailAPIError as exc:
            # startHistoryId can become stale; full sync is required then.
            if exc.status_code in (400, 404):
                do_full_sync = True
                delta = None
            else:
                raise

    full_sync_thread_ids: set[str] = set()
    if do_full_sync:
        await _set_initial_list_sync_state(inbox.id, True)
        try:
            # Initial sync stage 1: fetch metadata only so list can render quickly.
            sync_end = time_provider.utcnow()
            sync_start = sync_end - timedelta(days=INITIAL_SYNC_WINDOW_DAYS)
            message_ids = await _gmail_call_with_refresh(
                db,
                account,
                _gmail_list_message_ids_in_window,
                start_dt=sync_start,
                end_dt=sync_end,
                max_messages=INITIAL_SYNC_MAX_MESSAGES,
            )
            state.anchor_history_id = profile_history_id
            state.latest_history_id = profile_history_id
            state.last_history_id = profile_history_id

            total_messages = len(message_ids)
            oldest_synced_ms: int | None = None
            for idx, msg_id in enumerate(message_ids, start=1):
                try:
                    payload = await _gmail_call_with_refresh(
                        db,
                        account,
                        _gmail_get_message,
                        msg_id,
                        payload_format="metadata",
                    )
                except GmailAPIError as exc:
                    # If the message vanished between listing and fetch, ignore it rather
                    # than aborting the whole sync. 404s are relatively common when users
                    # delete or move messages concurrently.
                    if exc.status_code == 404:
                        log.debug(
                            "initial sync skipped missing message %s for inbox_id=%s",
                            msg_id,
                            inbox.id,
                        )
                        continue
                    raise

                row, _created = await _upsert_message_from_gmail(
                    db,
                    inbox_id=inbox.id,
                    gmail_message=payload,
                    include_body=False,
                )
                touched_threads.add((inbox.id, row.thread_id))
                full_sync_thread_ids.add(row.thread_id)
                if row.internal_date is not None:
                    oldest_synced_ms = (
                        row.internal_date if oldest_synced_ms is None else min(oldest_synced_ms, row.internal_date)
                    )

                if idx % FULL_SYNC_PROGRESS_COMMIT_INTERVAL == 0:
                    state.last_sync_at = time_provider.utcnow()
                    await db.commit()
                    log.info(
                        "Unibox initial list sync progress inbox_id=%s processed=%s/%s",
                        inbox.id,
                        idx,
                        total_messages,
                    )
            if oldest_synced_ms is not None:
                if state.oldest_internal_date is None or oldest_synced_ms < state.oldest_internal_date:
                    state.oldest_internal_date = oldest_synced_ms
            elif state.oldest_internal_date is None:
                state.oldest_internal_date = int(sync_start.timestamp() * 1000)
        finally:
            await _set_initial_list_sync_state(inbox.id, False)

        if full_sync_thread_ids:
            hydrate_after_commit_thread_ids = set(full_sync_thread_ids)
    elif delta is not None:
        for msg_id in delta.added_ids:
            try:
                payload = await _gmail_call_with_refresh(db, account, _gmail_get_message, msg_id)
            except GmailAPIError as exc:
                if exc.status_code == 404:
                    log.debug(
                        "delta sync skipped missing message %s for inbox_id=%s",
                        msg_id,
                        inbox.id,
                    )
                    continue
                raise

            row, _created = await _upsert_message_from_gmail(db, inbox_id=inbox.id, gmail_message=payload)
            touched_threads.add((inbox.id, row.thread_id))
            if row.internal_date is not None:
                if state.oldest_internal_date is None or row.internal_date < state.oldest_internal_date:
                    state.oldest_internal_date = row.internal_date

        for msg_id in delta.deleted_ids:
            deleted_thread_id = await _delete_message(db, inbox.id, msg_id)
            if deleted_thread_id:
                await _cleanup_thread_if_empty(db, inbox.id, deleted_thread_id)
                touched_threads.add((inbox.id, deleted_thread_id))

        for tid in delta.touched_threads:
            touched_threads.add((inbox.id, tid))

        newest_history = delta.latest_history_id or profile_history_id
        if newest_history:
            state.latest_history_id = newest_history
            state.last_history_id = newest_history

    # Safety net for drift/races: probe a window and import any missing ids
    # that history delta did not include.
    if reason == "manual" and not do_full_sync:
        # Manual sync should reconcile the full initial window (7 days) so
        # users can recover any previously skipped messages in that range.
        recovered_threads = await _recover_recent_missing_messages(
            db,
            inbox_id=inbox.id,
            account=account,
            window_hours=INITIAL_SYNC_WINDOW_DAYS * 24,
            max_messages=INITIAL_SYNC_MAX_MESSAGES,
        )
        touched_threads.update(recovered_threads)
    elif reason == "push" and not do_full_sync:
        recovered_threads = await _recover_recent_missing_messages(
            db,
            inbox_id=inbox.id,
            account=account,
        )
        touched_threads.update(recovered_threads)

    cfg = await get_gmail_sync_config(db)
    topic = (cfg.get("push_topic") or "").strip()
    renew_watch = (
        bool(topic)
        and (
            state.watch_expiration is None
            or state.watch_expiration <= (time_provider.utcnow() + timedelta(hours=6))
        )
    )
    if renew_watch:
        try:
            watch_history_id, watch_expiration = await _gmail_call_with_refresh(
                db,
                account,
                _gmail_register_watch,
                topic,
            )
            if watch_history_id:
                state.latest_history_id = watch_history_id
                state.last_history_id = watch_history_id
                if not state.anchor_history_id:
                    state.anchor_history_id = watch_history_id
            state.watch_expiration = watch_expiration
        except Exception:
            log.exception("Failed to renew Gmail watch for inbox_id=%s", inbox.id)

    state.last_sync_at = time_provider.utcnow()
    await db.flush()
    log.info(
        "Unibox sync inbox_id=%s reason=%s touched_threads=%s full_sync=%s",
        inbox.id,
        reason,
        len(touched_threads),
        do_full_sync,
    )
    return touched_threads, hydrate_after_commit_thread_ids


async def _backfill_older_window(
    db: AsyncSession,
    inbox: Inbox,
    *,
    window_days: int = BACKFILL_WINDOW_DAYS,
) -> tuple[set[tuple[int, str]], dict[str, Any]]:
    touched_threads: set[tuple[int, str]] = set()
    account_res = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox.id))
    account = account_res.scalar_one_or_none()
    if account is None:
        return touched_threads, {"messages_synced": 0, "range_start": None, "range_end": None}

    state = await _get_or_create_sync_state(db, inbox.id)
    await _ensure_access_token(db, account)

    end_dt = _epoch_ms_to_dt(state.oldest_internal_date) if state.oldest_internal_date else time_provider.utcnow()
    if end_dt is None:
        end_dt = time_provider.utcnow()
    start_dt = end_dt - timedelta(days=max(1, int(window_days)))

    message_ids = await _gmail_call_with_refresh(
        db,
        account,
        _gmail_list_message_ids_in_window,
        start_dt=start_dt,
        end_dt=end_dt,
        max_messages=BACKFILL_SYNC_MAX_MESSAGES,
    )

    oldest_synced_ms: int | None = None
    total_messages = len(message_ids)
    for idx, msg_id in enumerate(message_ids, start=1):
        try:
            payload = await _gmail_call_with_refresh(db, account, _gmail_get_message, msg_id)
        except GmailAPIError as exc:
            if exc.status_code == 404:
                log.debug(
                    "backfill skipped missing message %s for inbox_id=%s",
                    msg_id,
                    inbox.id,
                )
                continue
            raise

        row, _created = await _upsert_message_from_gmail(db, inbox_id=inbox.id, gmail_message=payload)
        touched_threads.add((inbox.id, row.thread_id))
        if row.internal_date is not None:
            oldest_synced_ms = row.internal_date if oldest_synced_ms is None else min(oldest_synced_ms, row.internal_date)

        if idx % FULL_SYNC_PROGRESS_COMMIT_INTERVAL == 0:
            state.last_sync_at = time_provider.utcnow()
            await db.commit()
            log.info(
                "Unibox backfill progress inbox_id=%s processed=%s/%s",
                inbox.id,
                idx,
                total_messages,
            )

    if oldest_synced_ms is not None:
        if state.oldest_internal_date is None or oldest_synced_ms < state.oldest_internal_date:
            state.oldest_internal_date = oldest_synced_ms
    else:
        state.oldest_internal_date = int(start_dt.timestamp() * 1000)

    state.last_sync_at = time_provider.utcnow()
    await db.flush()
    meta = {
        "messages_synced": total_messages,
        "range_start": _dt_to_iso(start_dt),
        "range_end": _dt_to_iso(end_dt),
    }
    log.info(
        "Unibox backfill inbox_id=%s messages_synced=%s range_start=%s range_end=%s",
        inbox.id,
        total_messages,
        meta["range_start"],
        meta["range_end"],
    )
    return touched_threads, meta


async def _recover_recent_missing_messages(
    db: AsyncSession,
    *,
    inbox_id: int,
    account: GmailAccount,
    window_hours: int = RECENT_RECOVERY_WINDOW_HOURS,
    max_messages: int | None = RECENT_RECOVERY_MAX_MESSAGES,
) -> set[tuple[int, str]]:
    end_dt = time_provider.utcnow()
    start_dt = end_dt - timedelta(hours=max(1, int(window_hours)))
    recent_ids = await _gmail_call_with_refresh(
        db,
        account,
        _gmail_list_message_ids_in_window,
        start_dt=start_dt,
        end_dt=end_dt,
        max_messages=max_messages,
    )
    if not recent_ids:
        return set()

    existing_rows = await db.execute(
        select(GmailMessage.message_id).where(
            GmailMessage.inbox_id == inbox_id,
            GmailMessage.message_id.in_(recent_ids),
        )
    )
    existing_ids = {str(row[0]) for row in existing_rows.all()}
    missing_ids = [msg_id for msg_id in recent_ids if msg_id not in existing_ids]
    if not missing_ids:
        return set()

    touched_threads: set[tuple[int, str]] = set()
    for msg_id in missing_ids:
        try:
            payload = await _gmail_call_with_refresh(db, account, _gmail_get_message, msg_id)
        except GmailAPIError as exc:
            if exc.status_code == 404:
                log.debug(
                    "recent recovery skipped missing message %s for inbox_id=%s",
                    msg_id,
                    inbox_id,
                )
                continue
            raise

        row, _created = await _upsert_message_from_gmail(db, inbox_id=inbox_id, gmail_message=payload)
        touched_threads.add((inbox_id, row.thread_id))

    log.info(
        "Unibox recent recovery inbox_id=%s recovered_messages=%s window_hours=%s",
        inbox_id,
        len(missing_ids),
        window_hours,
    )
    return touched_threads


async def _acquire_inflight(inbox_id: int) -> bool:
    async with _sync_lock:
        if inbox_id in _inflight_inbox_syncs:
            return False
        _inflight_inbox_syncs.add(inbox_id)
        return True


async def _release_inflight(inbox_id: int) -> None:
    async with _sync_lock:
        _inflight_inbox_syncs.discard(inbox_id)


async def _acquire_hydration_inflight(inbox_id: int) -> bool:
    async with _thread_hydration_lock:
        if inbox_id in _inflight_hydration_inboxes:
            return False
        _inflight_hydration_inboxes.add(inbox_id)
        return True


async def _release_hydration_inflight(inbox_id: int) -> None:
    async with _thread_hydration_lock:
        _inflight_hydration_inboxes.discard(inbox_id)


async def sync_single_inbox(inbox_id: int, reason: str = "scheduled") -> bool:
    if not await _acquire_inflight(inbox_id):
        return False

    touched: set[tuple[int, str]] = set()
    hydrate_thread_ids: set[str] = set()
    try:
        async with AsyncSessionLocal() as db:
            inbox_res = await db.execute(select(Inbox).where(Inbox.id == inbox_id, Inbox.provider.in_(["gmail", "office365"])))
            inbox = inbox_res.scalar_one_or_none()
            if not inbox:
                await db.rollback()
                return False
            if inbox.provider == "office365":
                touched = await _sync_inbox_office365(db, inbox, reason)
                hydrate_thread_ids = set()
            else:
                touched, hydrate_thread_ids = await _sync_inbox(db, inbox, reason)
            await db.commit()
    except Exception:
        log.exception("Failed unibox sync for inbox_id=%s reason=%s", inbox_id, reason)
        return False
    finally:
        await _release_inflight(inbox_id)

    if hydrate_thread_ids:
        await queue_thread_hydration_for_inbox(
            inbox_id,
            thread_ids=sorted(hydrate_thread_ids),
            reason="initial-thread-hydration",
        )

    for i_id, thread_id in touched:
        await unibox_events.publish(
            {
                "type": "unibox.thread.updated",
                "reason": reason,
                "inbox_id": i_id,
                "thread_id": thread_id,
                "timestamp": _dt_to_iso(time_provider.utcnow()),
            }
        )
    return True


async def backfill_single_inbox(
    inbox_id: int,
    *,
    window_days: int = BACKFILL_WINDOW_DAYS,
    reason: str = "manual-backfill",
) -> bool:
    if not await _acquire_inflight(inbox_id):
        return False

    touched: set[tuple[int, str]] = set()
    try:
        async with AsyncSessionLocal() as db:
            inbox_res = await db.execute(select(Inbox).where(Inbox.id == inbox_id, Inbox.provider.in_(["gmail", "office365"])))
            inbox = inbox_res.scalar_one_or_none()
            if not inbox:
                await db.rollback()
                return False
            if inbox.provider == "office365":
                # Office 365 doesn't have a separate backfill; re-use full sync
                touched = await _sync_inbox_office365(db, inbox, reason)
                _meta = {}
            else:
                touched, _meta = await _backfill_older_window(db, inbox, window_days=window_days)
            await db.commit()
    except Exception:
        log.exception("Failed unibox backfill for inbox_id=%s reason=%s", inbox_id, reason)
        return False
    finally:
        await _release_inflight(inbox_id)

    for i_id, thread_id in touched:
        await unibox_events.publish(
            {
                "type": "unibox.thread.updated",
                "reason": reason,
                "inbox_id": i_id,
                "thread_id": thread_id,
                "timestamp": _dt_to_iso(time_provider.utcnow()),
            }
        )
    return True


async def sync_all_inboxes(reason: str = "scheduled") -> int:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(Inbox.id).where(Inbox.provider.in_(["gmail", "office365"])))
        inbox_ids = [row[0] for row in rows.all()]

    synced = 0
    for inbox_id in inbox_ids:
        ok = await sync_single_inbox(inbox_id, reason=reason)
        if ok:
            synced += 1
    return synced


async def run_unibox_sync_job() -> None:
    count = await sync_all_inboxes(reason="scheduled")
    log.info("Periodic unibox sync finished: synced=%s", count)


async def queue_sync_for_inbox(inbox_id: int, reason: str = "push") -> None:
    async def _runner() -> None:
        await sync_single_inbox(inbox_id, reason=reason)

    asyncio.create_task(_runner())


async def queue_sync_for_all_inboxes(reason: str = "startup") -> None:
    async def _runner() -> None:
        await sync_all_inboxes(reason=reason)

    asyncio.create_task(_runner())


async def queue_backfill_for_inbox(
    inbox_id: int,
    *,
    window_days: int = BACKFILL_WINDOW_DAYS,
    reason: str = "manual-backfill",
) -> None:
    async def _runner() -> None:
        await backfill_single_inbox(inbox_id, window_days=window_days, reason=reason)

    asyncio.create_task(_runner())


async def queue_backfill_for_all_inboxes(
    *,
    window_days: int = BACKFILL_WINDOW_DAYS,
    reason: str = "manual-backfill",
) -> None:
    async def _runner() -> None:
        async with AsyncSessionLocal() as db:
            rows = await db.execute(select(Inbox.id).where(Inbox.provider.in_(["gmail", "office365"])))
            inbox_ids = [row[0] for row in rows.all()]
        for inbox_id in inbox_ids:
            await backfill_single_inbox(inbox_id, window_days=window_days, reason=reason)

    asyncio.create_task(_runner())


# ---------------------------------------------------------------------------
# Office 365 / Microsoft Graph sync implementation
# ---------------------------------------------------------------------------

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
O365_SYNC_WINDOW_DAYS = 7


def _graph_request_json(
    method: str,
    url: str,
    access_token: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make an HTTP request to Microsoft Graph API and return JSON."""
    if params:
        encoded = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}?{encoded}"

    body = None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=body, method=method, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:
            body_text = str(exc)
        raise GmailAPIError(exc.code, body_text) from exc
    except Exception as exc:
        raise GmailAPIError(0, str(exc)) from exc


def _graph_list_messages(
    access_token: str,
    *,
    folder: str = "",
    top: int = 100,
    filter_str: str = "",
    select_fields: str = "id,conversationId,internetMessageId,receivedDateTime,subject,from,toRecipients,body,isRead,hasAttachments",
    delta_link: str = "",
    use_delta_endpoint: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch messages from Microsoft Graph, returning (messages, next_delta_link).

    If *delta_link* is provided, uses it for incremental sync.
    If *use_delta_endpoint* is True (and no delta_link), uses the /delta endpoint
    so that Graph returns a deltaLink usable for future incremental syncs.
    Otherwise fetches from the regular messages endpoint with optional folder/filter.
    """
    messages: list[dict[str, Any]] = []
    next_link: str | None = None

    if delta_link:
        url = delta_link
    elif use_delta_endpoint:
        _folder = folder or "Inbox"
        base = f"{GRAPH_API_BASE}/me/mailFolders/{_folder}/messages/delta"
        params: dict[str, str] = {
            "$top": str(top),
            "$select": select_fields,
            # NOTE: $orderby is not supported on the delta endpoint
        }
        if filter_str:
            params["$filter"] = filter_str
        url = f"{base}?{urllib.parse.urlencode(params)}"
    else:
        base = f"{GRAPH_API_BASE}/me/mailFolders/{folder}/messages" if folder else f"{GRAPH_API_BASE}/me/messages"
        params = {
            "$top": str(top),
            "$select": select_fields,
            "$orderby": "receivedDateTime desc",
        }
        if filter_str:
            params["$filter"] = filter_str
        url = f"{base}?{urllib.parse.urlencode(params)}"

    new_delta_link = ""

    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Prefer": "odata.maxpagesize=100",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8")
            except Exception:
                pass
            raise GmailAPIError(exc.code, body_text) from exc

        for msg in data.get("value", []):
            if isinstance(msg, dict):
                messages.append(msg)

        # Check for delta link or next page
        if "@odata.deltaLink" in data:
            new_delta_link = data["@odata.deltaLink"]
        next_link = data.get("@odata.nextLink")
        url = next_link if next_link else ""

    return messages, new_delta_link


async def _ensure_o365_access_token(db: AsyncSession, account: Office365Account) -> str:
    """Ensure the Office 365 access token is fresh, refreshing if needed."""
    now_utc = time_provider.utcnow()
    if account.token_expiry and account.token_expiry <= (now_utc + timedelta(minutes=5)):
        client_id, client_secret, tenant_id = await get_office365_oauth_credentials(db)
        refreshed = refresh_office365_token(account, client_id, client_secret, tenant_id)
        if not refreshed:
            try:
                await maybe_fire_email_event(
                    db,
                    "token_expired",
                    {"inbox_id": account.inbox_id, "at": now_utc.isoformat()},
                )
            except Exception:
                log.exception("failed firing token_expired webhook")
            raise RuntimeError(f"Could not refresh Office 365 access token for inbox_id={account.inbox_id}")
        await db.flush()
    return account.access_token


async def _refresh_o365_token(db: AsyncSession, account: Office365Account) -> bool:
    client_id, client_secret, tenant_id = await get_office365_oauth_credentials(db)
    refreshed = refresh_office365_token(account, client_id, client_secret, tenant_id)
    if refreshed:
        await db.flush()
    return bool(refreshed)


async def _o365_call_with_refresh(
    db: AsyncSession,
    account: Office365Account,
    func,
    *args,
    **kwargs,
):
    """Call an Office 365 Graph helper, refreshing once on auth errors."""
    try:
        return await asyncio.to_thread(func, account.access_token, *args, **kwargs)
    except GmailAPIError as exc:
        if exc.status_code in (401, 403):
            refreshed = await _refresh_o365_token(db, account)
            if refreshed:
                return await asyncio.to_thread(func, account.access_token, *args, **kwargs)
            try:
                await maybe_fire_email_event(
                    db,
                    "token_expired",
                    {"inbox_id": account.inbox_id, "at": time_provider.utcnow().isoformat()},
                )
            except Exception:
                log.exception("failed firing token_expired webhook after O365 auth failure")
        raise


async def _get_or_create_o365_sync_state(db: AsyncSession, inbox_id: int) -> Office365SyncState:
    res = await db.execute(select(Office365SyncState).where(Office365SyncState.inbox_id == inbox_id))
    state = res.scalar_one_or_none()
    if state:
        return state
    state = Office365SyncState(inbox_id=inbox_id)
    db.add(state)
    await db.flush()
    return state


async def _upsert_o365_thread(
    db: AsyncSession,
    inbox_id: int,
    conversation_id: str,
    subject: str,
    received_at: datetime | None,
) -> Office365Thread:
    """Get or create an Office365Thread row."""
    res = await db.execute(
        select(Office365Thread).where(
            Office365Thread.inbox_id == inbox_id,
            Office365Thread.conversation_id == conversation_id,
        )
    )
    thread = res.scalar_one_or_none()
    if thread:
        if received_at and (thread.last_received_at is None or received_at > thread.last_received_at):
            thread.last_received_at = received_at
        if subject and not thread.subject:
            thread.subject = subject
        thread.updated_at = time_provider.utcnow()
        return thread
    thread = Office365Thread(
        inbox_id=inbox_id,
        conversation_id=conversation_id,
        subject=subject or "",
        last_received_at=received_at,
    )
    db.add(thread)
    await db.flush()
    return thread


async def _upsert_o365_message(
    db: AsyncSession,
    inbox_id: int,
    graph_message: dict[str, Any],
) -> tuple[Office365Message, bool]:
    """Upsert a message from Microsoft Graph API data. Returns (row, created)."""
    msg_id = str(graph_message.get("id", ""))
    conv_id = str(graph_message.get("conversationId", ""))
    internet_msg_id = str(graph_message.get("internetMessageId", ""))
    subject = str(graph_message.get("subject", ""))

    received_str = graph_message.get("receivedDateTime", "")
    received_at = None
    if received_str:
        try:
            received_at = datetime.fromisoformat(received_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    from_data = graph_message.get("from", {}) or {}
    from_addr = ""
    if isinstance(from_data, dict):
        ea = from_data.get("emailAddress", {}) or {}
        from_addr = str(ea.get("address", "")).lower()

    to_list = []
    for recip in graph_message.get("toRecipients", []) or []:
        if isinstance(recip, dict):
            ea = recip.get("emailAddress", {}) or {}
            addr = str(ea.get("address", "")).lower()
            if addr:
                to_list.append(addr)

    body_data = graph_message.get("body", {}) or {}
    body_type = str(body_data.get("contentType", "")).lower()
    body_content = str(body_data.get("content", ""))
    body_html = body_content if body_type == "html" else ""
    body_plain = body_content if body_type == "text" else ""
    if body_html and not body_plain:
        body_plain = _strip_html_tags(body_html)

    is_read = bool(graph_message.get("isRead", False))
    has_attachments = bool(graph_message.get("hasAttachments", False))

    # Ensure thread exists
    await _upsert_o365_thread(db, inbox_id, conv_id, subject, received_at)

    # Check existing
    res = await db.execute(
        select(Office365Message).where(
            Office365Message.inbox_id == inbox_id,
            Office365Message.message_id == msg_id,
        )
    )
    existing = res.scalar_one_or_none()
    if existing:
        existing.subject = subject
        existing.body_plain = body_plain
        existing.body_html = body_html
        existing.is_read = is_read
        existing.updated_at = time_provider.utcnow()
        return existing, False

    # Before inserting, delete any local surrogate that was created at send-time
    # for this exact RFC 2822 Message-ID so the message doesn't appear twice.
    if internet_msg_id:
        local_res = await db.execute(
            select(Office365Message).where(
                Office365Message.inbox_id == inbox_id,
                Office365Message.internet_message_id == internet_msg_id,
                Office365Message.message_id.startswith("local-"),
            )
        )
        for local_row in local_res.scalars().all():
            await db.delete(local_row)

    row = Office365Message(
        inbox_id=inbox_id,
        message_id=msg_id,
        conversation_id=conv_id,
        internet_message_id=internet_msg_id,
        received_at=received_at,
        subject=subject,
        from_address=from_addr,
        to_addresses=json.dumps(to_list),
        body_plain=body_plain,
        body_html=body_html,
        is_read=is_read,
        has_attachments=has_attachments,
    )
    db.add(row)
    await db.flush()
    return row, True


async def _sync_inbox_office365(
    db: AsyncSession,
    inbox: Inbox,
    reason: str,
) -> set[tuple[int, str]]:
    """Sync an Office 365 inbox using Microsoft Graph API.

    Returns set of (inbox_id, conversation_id) for touched threads.
    """
    touched_threads: set[tuple[int, str]] = set()

    account_res = await db.execute(select(Office365Account).where(Office365Account.inbox_id == inbox.id))
    account = account_res.scalar_one_or_none()
    if account is None:
        return touched_threads

    state = await _get_or_create_o365_sync_state(db, inbox.id)
    await _ensure_o365_access_token(db, account)

    sync_end = time_provider.utcnow()
    sync_start = sync_end - timedelta(days=O365_SYNC_WINDOW_DAYS)
    date_filter = f"receivedDateTime ge {sync_start.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    # ── Helper: sync one folder (Inbox / SentItems / JunkEmail) ────────────
    async def _sync_folder(
        folder: str,
        delta_attr: str,
    ) -> list[dict[str, Any]]:
        """Sync a single mailFolder using delta or full scan.

        Returns the list of changed messages.  Updates *state.<delta_attr>*
        and calls db.flush() internally but does NOT commit.
        """
        stored_delta: str = getattr(state, delta_attr, "") or ""
        folder_messages: list[dict[str, Any]] = []
        _delta_expired = False

        if stored_delta:
            try:
                msgs, new_delta = await _o365_call_with_refresh(
                    db,
                    account,
                    _graph_list_messages,
                    delta_link=stored_delta,
                )
            except GmailAPIError as exc:
                if exc.status_code in (400, 410):
                    log.info(
                        "O365 delta link expired for folder=%s inbox_id=%s; full scan",
                        folder, inbox.id,
                    )
                    setattr(state, delta_attr, "")
                    _delta_expired = True
                else:
                    raise

            if not _delta_expired:
                folder_messages = msgs
                if new_delta:
                    setattr(state, delta_attr, new_delta)
                await db.flush()
                return folder_messages

        # Full scan for this folder
        msgs, new_delta = await _o365_call_with_refresh(
            db,
            account,
            _graph_list_messages,
            folder=folder,
            filter_str=date_filter,
            use_delta_endpoint=True,
        )
        folder_messages = msgs
        if new_delta:
            setattr(state, delta_attr, new_delta)
        await db.flush()
        return folder_messages

    # ── Sync Inbox ────────────────────────────────────────────────────────
    inbox_messages = await _sync_folder("Inbox", "delta_link")
    for idx, msg in enumerate(inbox_messages, start=1):
        row, _created = await _upsert_o365_message(db, inbox.id, msg)
        touched_threads.add((inbox.id, row.conversation_id))
        if idx % FULL_SYNC_PROGRESS_COMMIT_INTERVAL == 0:
            state.last_sync_at = time_provider.utcnow()
            await db.commit()
            log.info(
                "Unibox O365 Inbox sync progress inbox_id=%s processed=%s/%s",
                inbox.id, idx, len(inbox_messages),
            )

    # ── Sync SentItems ────────────────────────────────────────────────────
    sent_messages = await _sync_folder("SentItems", "sent_delta_link")
    for idx, msg in enumerate(sent_messages, start=1):
        row, _created = await _upsert_o365_message(db, inbox.id, msg)
        touched_threads.add((inbox.id, row.conversation_id))
        if idx % FULL_SYNC_PROGRESS_COMMIT_INTERVAL == 0:
            state.last_sync_at = time_provider.utcnow()
            await db.commit()
            log.info(
                "Unibox O365 SentItems sync progress inbox_id=%s processed=%s/%s",
                inbox.id, idx, len(sent_messages),
            )

    # ── Sync JunkEmail (Spam/Junk) ─────────────────────────────────────────
    junk_messages = await _sync_folder("JunkEmail", "junk_delta_link")
    for idx, msg in enumerate(junk_messages, start=1):
        row, _created = await _upsert_o365_message(db, inbox.id, msg)
        touched_threads.add((inbox.id, row.conversation_id))
        if idx % FULL_SYNC_PROGRESS_COMMIT_INTERVAL == 0:
            state.last_sync_at = time_provider.utcnow()
            await db.commit()
            log.info(
                "Unibox O365 JunkEmail sync progress inbox_id=%s processed=%s/%s",
                inbox.id, idx, len(junk_messages),
            )

    all_messages = inbox_messages + sent_messages + junk_messages
    state.last_sync_at = time_provider.utcnow()
    await db.flush()

    # Detect lead replies (inbound) and mark sent-to-lead threads (outbound)
    if all_messages:
        await _detect_o365_lead_replies(db, inbox, inbox_messages + junk_messages)
        await _detect_o365_sent_to_lead(db, inbox, sent_messages)

    log.info(
        "Unibox O365 sync inbox_id=%s reason=%s inbox=%s sent=%s junk=%s touched=%s",
        inbox.id, reason,
        len(inbox_messages), len(sent_messages), len(junk_messages),
        len(touched_threads),
    )
    return touched_threads


async def _detect_o365_lead_replies(
    db: AsyncSession,
    inbox: Inbox,
    messages: list[dict[str, Any]],
) -> None:
    """Detect lead replies among newly synced Office 365 messages (Inbox + JunkEmail).

    Mirrors the Gmail reply detection logic: any inbound message whose sender
    is a known lead marks the thread as a lead thread and fires reply logic.
    """
    inbox_email = inbox.email.lower()

    for msg in messages:
        from_data = msg.get("from", {}) or {}
        from_ea = from_data.get("emailAddress", {}) or {}
        from_addr = str(from_ea.get("address", "")).lower()

        if not from_addr or from_addr == inbox_email:
            continue  # Skip our own outgoing messages

        conv_id = str(msg.get("conversationId", ""))
        if not conv_id:
            continue

        lead_res = await db.execute(
            select(Lead).where(func.lower(Lead.email) == from_addr)
        )
        lead = lead_res.scalar_one_or_none()
        campaign_ids_from_thread_only: list[int] | None = None
        if lead is None:
            pair = await _single_lead_campaign_pair_for_thread(db, conv_id)
            if pair is None:
                continue
            lid, cid = pair
            lead_res_fb = await db.execute(select(Lead).where(Lead.id == lid))
            lead = lead_res_fb.scalar_one_or_none()
            if lead is None:
                continue
            campaign_ids_from_thread_only = [cid]

        # Always mark the thread as a lead thread when this lead sent us a message,
        # regardless of whether they're in an active campaign (matches Gmail behaviour).
        thread_res = await db.execute(
            select(Office365Thread).where(
                Office365Thread.inbox_id == inbox.id,
                Office365Thread.conversation_id == conv_id,
            )
        )
        thread = thread_res.scalar_one_or_none()
        if thread and not thread.is_lead_thread:
            thread.is_lead_thread = True
            thread.unread_lead_reply = True
            await db.flush()

        if campaign_ids_from_thread_only is not None:
            campaign_ids = list(campaign_ids_from_thread_only)
        else:
            # Find campaign_leads associated with this inbox so we can record a reply
            # and cancel remaining queue slots.  Primary lookup: EmailLog thread_id.
            log_res = await db.execute(
                select(EmailLog.campaign_id).where(
                    EmailLog.thread_id == conv_id,
                    EmailLog.lead_id == lead.id,
                ).distinct()
            )
            campaign_ids = [r[0] for r in log_res.all()]

            # Fallback: any campaign this lead is enrolled in that uses this inbox.
            if not campaign_ids:
                from app.models import CampaignInbox
                cl_res = await db.execute(
                    select(CampaignLead.campaign_id)
                    .join(CampaignInbox, CampaignLead.campaign_id == CampaignInbox.campaign_id)
                    .where(
                        CampaignLead.lead_id == lead.id,
                        CampaignInbox.inbox_id == inbox.id,
                    )
                )
                campaign_ids = [r[0] for r in cl_res.all()]

        for camp_id in campaign_ids:
            existing_reply = await db.execute(
                select(LeadReply).where(
                    LeadReply.lead_id == lead.id,
                    LeadReply.campaign_id == camp_id,
                )
            )
            if existing_reply.scalar_one_or_none():
                continue

            db.add(LeadReply(lead_id=lead.id, campaign_id=camp_id))

            _clr = await db.execute(
                select(CampaignLead).where(
                    CampaignLead.lead_id == lead.id,
                    CampaignLead.campaign_id == camp_id,
                )
            )
            _cl_o = _clr.scalar_one_or_none()
            if _cl_o is not None and _cl_o.enrollment_status == "active":
                _cl_o.enrollment_status = "contacted"

            # Delete remaining queue slots
            cl_ids_res = await db.execute(
                select(CampaignLead.id).where(
                    CampaignLead.lead_id == lead.id,
                    CampaignLead.campaign_id == camp_id,
                )
            )
            cl_ids = [r[0] for r in cl_ids_res.all()]
            if cl_ids:
                await db.execute(delete(QueueSlot).where(QueueSlot.campaign_lead_id.in_(cl_ids)))

            # Ensure thread marked as lead thread + unread
            if thread and not thread.unread_lead_reply:
                thread.is_lead_thread = True
                thread.unread_lead_reply = True

            await db.flush()

            # Fire webhook
            try:
                await fire_lead_reply_webhook(db, {
                    "lead_id": lead.id,
                    "lead_email": from_addr,
                    "campaign_id": camp_id,
                    "inbox_id": inbox.id,
                    "conversation_id": conv_id,
                    "timestamp": time_provider.utcnow().isoformat() + "Z",
                })
            except Exception:
                log.exception("Failed to fire lead reply webhook for lead_id=%s", lead.id)

            log.info(
                "O365 lead reply detected: lead=%s campaign=%s inbox=%s conv=%s",
                lead.email, camp_id, inbox.id, conv_id,
            )

    # ── NDR / delayed bounce detection ────────────────────────────────────
    # After processing normal lead replies, iterate again over the same batch
    # looking for mailer-daemon / postmaster bounce notifications.
    for msg in messages:
        from_data = msg.get("from", {}) or {}
        from_ea = from_data.get("emailAddress", {}) or {}
        from_addr_ndr = str(from_ea.get("address", "")).lower()

        if not _MAILER_DAEMON_RE.match(from_addr_ndr):
            continue

        subject_ndr = str(msg.get("subject", ""))
        if subject_ndr and not _BOUNCE_SUBJECT_RE.search(subject_ndr):
            continue  # sender is mailer-daemon but subject doesn't look like a bounce

        body_data = msg.get("body", {}) or {}
        body_type = str(body_data.get("contentType", "")).lower()
        body_content = str(body_data.get("content", ""))
        body_plain_ndr = body_content if body_type == "text" else _strip_html_tags(body_content)
        body_html_ndr = body_content if body_type == "html" else ""

        bounced_addr = _extract_bounced_recipient(body_plain_ndr, body_html_ndr)
        if not bounced_addr:
            continue

        b_lead_res = await db.execute(
            select(Lead).where(func.lower(Lead.email) == bounced_addr)
        )
        b_lead = b_lead_res.scalar_one_or_none()
        if b_lead is None:
            continue
        b_rows_o365 = (
            await db.execute(select(CampaignLead).where(CampaignLead.lead_id == b_lead.id))
        ).scalars().all()
        if not b_rows_o365 or all(
            getattr(r, "enrollment_status", None) in ("bounced", "unsubscribed")
            for r in b_rows_o365
        ):
            continue

        log.info(
            "O365 NDR bounce detected for lead_id=%s email=%s (inbox_id=%s)",
            b_lead.id, bounced_addr, inbox.id,
        )
        for _bcl in b_rows_o365:
            _bcl.enrollment_status = "bounced"
        b_cl_ids = [r.id for r in b_rows_o365]
        if b_cl_ids:
            await db.execute(
                delete(QueueSlot).where(QueueSlot.campaign_lead_id.in_(b_cl_ids))
            )
        await db.flush()

        b_camp_res = await db.execute(
            select(CampaignLead.campaign_id).where(
                CampaignLead.lead_id == b_lead.id
            ).distinct()
        )
        for (b_camp_id,) in b_camp_res.all():
            try:
                from app.webhooks import fire_webhook_event as _fwe
                await _fwe(db, "email.bounced", {
                    "lead_id": b_lead.id,
                    "lead_email": b_lead.email,
                    "campaign_id": b_camp_id,
                    "inbox_id": inbox.id,
                    "error_type": "bounce",
                    "error_message": subject_ndr[:300],
                    "timestamp": time_provider.utcnow().isoformat() + "Z",
                })
                await _fwe(db, "lead.status_changed", {
                    "lead_id": b_lead.id,
                    "lead_email": b_lead.email,
                    "campaign_id": b_camp_id,
                    "old_enrollment_status": "active",
                    "new_enrollment_status": "bounced",
                    "reason": f"NDR from {from_addr_ndr}",
                    "timestamp": time_provider.utcnow().isoformat() + "Z",
                })
            except Exception:
                log.exception("Failed to fire bounce webhook for lead_id=%s", b_lead.id)


async def _detect_o365_sent_to_lead(
    db: AsyncSession,
    inbox: Inbox,
    messages: list[dict[str, Any]],
) -> None:
    """Mark O365 threads as lead threads when we sent an email to a known lead.

    Mirrors the Gmail 'Sent-to-lead detection' in _upsert_message_from_gmail.
    Called for SentItems messages so that outbound emails show up in the
    unibox even before the lead replies.
    """
    inbox_email = inbox.email.lower()

    for msg in messages:
        conv_id = str(msg.get("conversationId", ""))
        if not conv_id:
            continue

        # Determine the TO address(es)
        for recip in msg.get("toRecipients", []) or []:
            if not isinstance(recip, dict):
                continue
            ea = recip.get("emailAddress", {}) or {}
            to_addr = str(ea.get("address", "")).lower()
            if not to_addr or to_addr == inbox_email:
                continue

            lead_res = await db.execute(
                select(Lead).where(func.lower(Lead.email) == to_addr)
            )
            lead = lead_res.scalar_one_or_none()
            if not lead:
                continue

            # Mark the thread as a lead thread
            thread_res = await db.execute(
                select(Office365Thread).where(
                    Office365Thread.inbox_id == inbox.id,
                    Office365Thread.conversation_id == conv_id,
                )
            )
            thread = thread_res.scalar_one_or_none()
            if thread and not thread.is_lead_thread:
                thread.is_lead_thread = True
                await db.flush()
                log.debug(
                    "O365 sent-to-lead thread marked: lead=%s inbox=%s conv=%s",
                    to_addr, inbox.id, conv_id,
                )
            break  # one matching lead recipient is enough


def decode_push_message_data(raw_data: str) -> dict[str, Any]:
    decoded = _decode_base64url(raw_data).decode("utf-8", errors="ignore")
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}

