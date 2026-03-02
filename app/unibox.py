"""Unibox backend services: DB queries, Gmail sync, and realtime signaling."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import time as time_provider
from app.app_settings import get_gmail_sync_config, get_google_oauth_credentials
from app.webhooks import maybe_fire_email_event
from app.database import AsyncSessionLocal
from app.models import GmailAccount, GmailMessage, GmailSyncState, GmailThread, Inbox
from app.routers.gmail_oauth import refresh_access_token

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


def _body_snippet(plain: str, html: str, fallback: str = "") -> str:
    base = plain or html or fallback or ""
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
    if account.token_expiry and account.token_expiry <= (now_utc + timedelta(minutes=1)):
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
    snippet = str(gmail_message.get("snippet", ""))
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


async def list_unibox_conversations(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    offset = (page - 1) * page_size

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

    count_stmt = select(func.count()).select_from(GmailThread)
    total = (await db.execute(count_stmt)).scalar_one() or 0

    stmt = (
        select(
            GmailThread.inbox_id,
            GmailThread.thread_id,
            GmailThread.last_internal_date,
            GmailThread.snippet.label("thread_snippet"),
            Inbox.email.label("account_email"),
            latest_message_sq.c.snippet.label("last_snippet"),
            latest_message_sq.c.headers_json.label("last_headers_json"),
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
        .order_by(desc(GmailThread.last_internal_date), desc(GmailThread.updated_at))
        .offset(offset)
        .limit(page_size)
    )

    rows = (await db.execute(stmt)).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        subject = _header_value(row.last_headers_json or "", "Subject") or "(no subject)"
        last_snippet = row.last_snippet or row.thread_snippet or ""
        timestamp = _dt_to_iso(_epoch_ms_to_dt(row.last_internal_date))
        items.append(
            {
                "thread_id": row.thread_id,
                "inbox_id": row.inbox_id,
                "gmail_account": row.account_email,
                "subject": subject,
                "last_message_snippet": last_snippet,
                "timestamp": timestamp,
            }
        )

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": int(total),
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
            return None
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
        return None
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
        "gmail_account": account_email,
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
    access_token: str,
) -> bool:
    try:
        payload = await asyncio.to_thread(
            _gmail_get_thread,
            access_token,
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
        access_token = await _ensure_access_token(db, account)
        hydrated = await _hydrate_thread_from_gmail(
            db,
            inbox_id=inbox_id,
            thread_id=thread_id,
            access_token=access_token,
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

            access_token = await _ensure_access_token(db, account)

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
                        access_token=access_token,
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
    access_token = await _ensure_access_token(db, account)

    profile = await asyncio.to_thread(_gmail_get_profile, access_token)
    profile_history_id = str(profile.get("historyId", ""))

    do_full_sync = not state.anchor_history_id
    delta: GmailHistoryDelta | None = None
    if not do_full_sync:
        start_history_id = state.latest_history_id or state.last_history_id or state.anchor_history_id
        try:
            delta = await asyncio.to_thread(_gmail_history_delta, access_token, start_history_id)
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
            message_ids = await asyncio.to_thread(
                _gmail_list_message_ids_in_window,
                access_token,
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
                    payload = await asyncio.to_thread(
                        _gmail_get_message,
                        access_token,
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
                payload = await asyncio.to_thread(_gmail_get_message, access_token, msg_id)
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
            access_token=access_token,
            window_hours=INITIAL_SYNC_WINDOW_DAYS * 24,
            max_messages=INITIAL_SYNC_MAX_MESSAGES,
        )
        touched_threads.update(recovered_threads)
    elif reason == "push" and not do_full_sync:
        recovered_threads = await _recover_recent_missing_messages(
            db,
            inbox_id=inbox.id,
            access_token=access_token,
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
            watch_history_id, watch_expiration = await asyncio.to_thread(
                _gmail_register_watch,
                access_token,
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
    access_token = await _ensure_access_token(db, account)

    end_dt = _epoch_ms_to_dt(state.oldest_internal_date) if state.oldest_internal_date else time_provider.utcnow()
    if end_dt is None:
        end_dt = time_provider.utcnow()
    start_dt = end_dt - timedelta(days=max(1, int(window_days)))

    message_ids = await asyncio.to_thread(
        _gmail_list_message_ids_in_window,
        access_token,
        start_dt=start_dt,
        end_dt=end_dt,
        max_messages=BACKFILL_SYNC_MAX_MESSAGES,
    )

    oldest_synced_ms: int | None = None
    total_messages = len(message_ids)
    for idx, msg_id in enumerate(message_ids, start=1):
        try:
            payload = await asyncio.to_thread(_gmail_get_message, access_token, msg_id)
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
    access_token: str,
    window_hours: int = RECENT_RECOVERY_WINDOW_HOURS,
    max_messages: int | None = RECENT_RECOVERY_MAX_MESSAGES,
) -> set[tuple[int, str]]:
    end_dt = time_provider.utcnow()
    start_dt = end_dt - timedelta(hours=max(1, int(window_hours)))
    recent_ids = await asyncio.to_thread(
        _gmail_list_message_ids_in_window,
        access_token,
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
            payload = await asyncio.to_thread(_gmail_get_message, access_token, msg_id)
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
            inbox_res = await db.execute(select(Inbox).where(Inbox.id == inbox_id, Inbox.provider == "gmail"))
            inbox = inbox_res.scalar_one_or_none()
            if not inbox:
                await db.rollback()
                return False
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
            inbox_res = await db.execute(select(Inbox).where(Inbox.id == inbox_id, Inbox.provider == "gmail"))
            inbox = inbox_res.scalar_one_or_none()
            if not inbox:
                await db.rollback()
                return False
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
        rows = await db.execute(select(Inbox.id).where(Inbox.provider == "gmail"))
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
            rows = await db.execute(select(Inbox.id).where(Inbox.provider == "gmail"))
            inbox_ids = [row[0] for row in rows.all()]
        for inbox_id in inbox_ids:
            await backfill_single_inbox(inbox_id, window_days=window_days, reason=reason)

    asyncio.create_task(_runner())


def decode_push_message_data(raw_data: str) -> dict[str, Any]:
    decoded = _decode_base64url(raw_data).decode("utf-8", errors="ignore")
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}

