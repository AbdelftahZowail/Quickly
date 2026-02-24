"""Unified inbox API routes.

The backend no longer queries Gmail directly for every list/detail request.
Instead the background sync job maintains a durable SQLite mirror (see
:mod:`app.models` tables ``gmail_thread``, ``gmail_message`` and
``gmail_attachment``).  Frontend endpoints read from that local cache; bodies
and attachments are pulled lazily when requested.

Aggregates conversations across connected inbox providers (Gmail now),
enriches threads with server-side campaign metadata, and supports replying
from the app UI.
"""
from __future__ import annotations

import base64
import html
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import getaddresses

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import time as time_provider
from app.app_settings import get_google_oauth_credentials
from app.database import get_db
from app.models import (
    Campaign,
    EmailLog,
    GmailAccount,
    Inbox,
    Lead,
    UniboxCache,
    GmailThread,
    GmailMessage,
    GmailAttachment,
)
from app.routers.gmail_oauth import refresh_access_token
from app.sender import send_email

log = logging.getLogger("campaign_engine.unibox")

router = APIRouter(prefix="/api/unibox", tags=["unibox"])

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

_CONVERSATION_CACHE_TTL_SECONDS = 60.0
_CONVERSATION_CACHE_MAX_AGE_SECONDS = 3600.0
_THREAD_META_CACHE_TTL_SECONDS = 300.0
_THREAD_FULL_CACHE_TTL_SECONDS = 120.0
_THREAD_DETAIL_CACHE_TTL_SECONDS = 120.0
_THREAD_DETAIL_CACHE_MAX_AGE_SECONDS = 21600.0
_SERVER_CONTEXT_CACHE_TTL_SECONDS = 30.0

_CONVERSATION_LIST_CACHE: dict[str, tuple[float, dict]] = {}
_THREAD_META_CACHE: dict[str, tuple[float, dict]] = {}
_THREAD_FULL_CACHE: dict[str, tuple[float, dict]] = {}
_THREAD_DETAIL_CACHE: dict[str, tuple[float, dict]] = {}
_SERVER_CONTEXT_CACHE: dict[str, tuple[float, dict]] = {}


class UniboxReplyRequest(BaseModel):
    provider: str = "gmail"
    inbox_id: int
    thread_id: str
    to_email: str
    subject: str | None = None
    body: str
    is_html: bool = False


def _cache_get(cache: dict[str, tuple[float, dict]], key: str, ttl_seconds: float) -> dict | None:
    now_monotonic = time.monotonic()
    cached = cache.get(key)
    if not cached:
        return None
    ts, value = cached
    if (now_monotonic - ts) > ttl_seconds:
        cache.pop(key, None)
        return None
    return value


def _cache_set(cache: dict[str, tuple[float, dict]], key: str, value: dict) -> None:
    cache[key] = (time.monotonic(), value)


async def _persisted_cache_get(
    db: AsyncSession,
    key: str,
    max_age_seconds: float,
) -> tuple[dict | None, float | None]:
    row = await db.get(UniboxCache, key)
    if not row:
        return None, None
    updated_at = row.updated_at
    if not updated_at:
        return row.payload, None
    age_seconds = (time_provider.utcnow() - updated_at).total_seconds()
    if max_age_seconds and age_seconds > max_age_seconds:
        return None, None
    return row.payload, age_seconds


async def _persisted_cache_set(db: AsyncSession, key: str, payload: dict) -> None:
    row = await db.get(UniboxCache, key)
    if row:
        row.payload = payload
        row.updated_at = time_provider.utcnow()
        return
    db.add(UniboxCache(cache_key=key, payload=payload))


def _cache_status_from_age(age_seconds: float | None, fresh_seconds: float) -> str:
    if age_seconds is None:
        return "hit"
    return "stale" if age_seconds > fresh_seconds else "hit"


def _conversation_list_cache_key(params: dict) -> str:
    return "list:" + json.dumps(params, separators=(",", ":"), sort_keys=True)


def _thread_detail_cache_key(provider: str, inbox_id: int, thread_id: str) -> str:
    return f"detail:{provider}:{inbox_id}:{thread_id}"


def _clear_cache_by_prefix(cache: dict[str, tuple[float, dict]], prefix: str) -> None:
    for key in [k for k in cache.keys() if k.startswith(prefix)]:
        cache.pop(key, None)


def _invalidate_unibox_caches(inbox_id: int | None = None, thread_id: str | None = None) -> None:
    _CONVERSATION_LIST_CACHE.clear()
    _SERVER_CONTEXT_CACHE.clear()
    _THREAD_DETAIL_CACHE.clear()
    if inbox_id is None:
        return
    prefix = f"{inbox_id}:"
    if thread_id:
        _THREAD_META_CACHE.pop(f"{inbox_id}:{thread_id}", None)
        _clear_cache_by_prefix(_THREAD_FULL_CACHE, f"{inbox_id}:{thread_id}:")
        return
    _clear_cache_by_prefix(_THREAD_META_CACHE, prefix)
    _clear_cache_by_prefix(_THREAD_FULL_CACHE, prefix)


async def _invalidate_persisted_caches(
    db: AsyncSession,
    provider: str | None = None,
    inbox_id: int | None = None,
    thread_id: str | None = None,
) -> None:
    await db.execute(delete(UniboxCache).where(UniboxCache.cache_key.like("list:%")))
    if provider and inbox_id and thread_id:
        detail_key = _thread_detail_cache_key(provider, inbox_id, thread_id)
        await db.execute(delete(UniboxCache).where(UniboxCache.cache_key == detail_key))
        return
    await db.execute(delete(UniboxCache).where(UniboxCache.cache_key.like("detail:%")))


def _decode_cursor(cursor: str | None) -> dict[int, str]:
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[int, str] = {}
    for k, v in payload.items():
        try:
            inbox_id = int(k)
        except Exception:
            continue
        token = str(v or "").strip()
        if token:
            out[inbox_id] = token
    return out


def _encode_cursor(cursor_map: dict[int, str]) -> str | None:
    clean = {str(k): v for k, v in cursor_map.items() if v}
    if not clean:
        return None
    raw = json.dumps(clean, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _normalize_message_id(value: str | None) -> str:
    if not value:
        return ""
    out = value.strip()
    if out.startswith("<") and out.endswith(">"):
        out = out[1:-1]
    return out.strip().lower()


def _extract_header(headers: list[dict], name: str) -> str:
    wanted = name.lower()
    for header in headers:
        if str(header.get("name", "")).lower() == wanted:
            return str(header.get("value", "")).strip()
    return ""


def _extract_emails(value: str) -> list[str]:
    if not value:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for _label, address in getaddresses([value]):
        email_value = address.strip().lower()
        if email_value and email_value not in seen:
            seen.add(email_value)
            out.append(email_value)
    return out


def _decode_gmail_base64(value: str) -> str:
    if not value:
        return ""
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</(p|div|li|tr|h1|h2|h3|h4|h5|h6)>", "\n", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_payload_bodies(payload: dict) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict) -> None:
        mime_type = str(part.get("mimeType", "")).lower()
        body = part.get("body") or {}
        raw_data = body.get("data")
        parts = part.get("parts") or []

        if raw_data:
            decoded = _decode_gmail_base64(raw_data)
            if decoded.strip():
                if mime_type.startswith("text/plain"):
                    plain_parts.append(decoded)
                elif mime_type.startswith("text/html"):
                    html_parts.append(decoded)

        for child in parts:
            if isinstance(child, dict):
                walk(child)

    walk(payload or {})

    plain_text = "\n\n".join(p.strip() for p in plain_parts if p.strip()).strip()
    html_text = "\n\n".join(p.strip() for p in html_parts if p.strip()).strip()
    if not plain_text and html_text:
        plain_text = _html_to_text(html_text)
    return plain_text, html_text


def _iso_from_internal_ms(value: str | int | None) -> str:
    try:
        timestamp = int(value) / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _gmail_request_json(access_token: str, url: str, method: str = "GET", payload: dict | None = None) -> dict:
    data: bytes | None = None
    headers = {"Authorization": f"Bearer {access_token}"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise RuntimeError(f"Gmail API error ({exc.code} {exc.reason}): {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Gmail API request failed: {exc}") from exc


async def _get_gmail_thread_metadata(
    access_token: str,
    inbox_id: int,
    thread_id: str,
    force_refresh: bool = False,
    db: AsyncSession | None = None,
) -> dict:
    """Fetch headers-only metadata for a Gmail thread.

    The result is cached in-memory and, when ``db`` is provided, persisted in the
    ``unibox_cache`` table under the ``meta:<inbox>:<thread>`` key.  Calling with
    ``force_refresh`` skips both caches and re-queries Gmail.
    """
    cache_key = f"{inbox_id}:{thread_id}"
    if not force_refresh:
        # first check the in-memory TTL cache
        cached = _cache_get(_THREAD_META_CACHE, cache_key, _THREAD_META_CACHE_TTL_SECONDS)
        if cached:
            return cached
        # next try the persistent store if a session is available
        if db is not None:
            persisted_key = "meta:" + cache_key
            persisted, age = await _persisted_cache_get(db, persisted_key, _THREAD_META_CACHE_TTL_SECONDS)
            if persisted:
                _cache_set(_THREAD_META_CACHE, cache_key, persisted)
                return persisted
    detail_url = (
        f"{GMAIL_API_BASE}/threads/{urllib.parse.quote(thread_id)}"
        "?format=metadata"
        "&metadataHeaders=From"
        "&metadataHeaders=To"
        "&metadataHeaders=Cc"
        "&metadataHeaders=Subject"
        "&metadataHeaders=Message-Id"
    )
    payload = _gmail_request_json(access_token, detail_url)
    _cache_set(_THREAD_META_CACHE, cache_key, payload)
    if db is not None:
        # asynchronously persist without blocking callers
        try:
            await _persisted_cache_set(db, "meta:" + cache_key, payload)
        except Exception:
            pass
    return payload


def _get_gmail_thread_full(
    access_token: str,
    inbox_id: int,
    thread_id: str,
    force_refresh: bool = False,
) -> dict:
    cache_key = f"{inbox_id}:{thread_id}:full"
    if not force_refresh:
        cached = _cache_get(_THREAD_FULL_CACHE, cache_key, _THREAD_FULL_CACHE_TTL_SECONDS)
        if cached:
            return cached
    full_url = f"{GMAIL_API_BASE}/threads/{urllib.parse.quote(thread_id)}?format=full"
    payload = _gmail_request_json(access_token, full_url)
    _cache_set(_THREAD_FULL_CACHE, cache_key, payload)
    return payload


def _derive_reply_subject(current_subject: str) -> str:
    subject = (current_subject or "").strip()
    if not subject:
        return "Re: (no subject)"
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


def _server_entry_from_row(email_log: EmailLog, lead: Lead, campaign: Campaign) -> dict:
    return {
        "log_id": email_log.id,
        "inbox_id": email_log.inbox_id,
        "lead_id": lead.id,
        "lead_email": lead.email,
        "lead_name": lead.name or "",
        "lead_status": lead.status,
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "subject": email_log.subject or "",
        "message_id": email_log.message_id or "",
        "thread_id": email_log.thread_id or "",
        "sent_at": email_log.sent_at.isoformat() if email_log.sent_at else "",
    }


async def _build_db_conversation(inbox: Inbox, thread: GmailThread, db: AsyncSession) -> dict:
    """Construct a list entry for a thread using the local mirror tables."""
    # fetch messages belonging to the thread
    result = await db.execute(
        select(GmailMessage)
        .where(
            GmailMessage.inbox_id == inbox.id,
            GmailMessage.thread_id == thread.thread_id,
        )
        .order_by(GmailMessage.internal_date.asc())
    )
    msgs = result.scalars().all()

    participant_set: set[str] = set()
    snippet = thread.snippet or ""
    subject = ""
    for msg in msgs:
        try:
            headers = json.loads(msg.headers_json or "[]")
        except Exception:
            headers = []
        from_header = _extract_header(headers, "From")
        to_header = _extract_header(headers, "To")
        cc_header = _extract_header(headers, "Cc")
        subj = _extract_header(headers, "Subject")
        if subj:
            subject = subj
        # accumulate participants
        for address in _extract_emails(from_header):
            participant_set.add(address)
        for address in _extract_emails(to_header):
            participant_set.add(address)
        for address in _extract_emails(cc_header):
            participant_set.add(address)
    if not subject:
        subject = snippet or "(no subject)"
    last_message_at = _iso_from_internal_ms(thread.last_internal_date)
    external = [p for p in participant_set if p != (inbox.email or "").lower()]

    return {
        "provider": "gmail",
        "source": "db",
        "inbox_id": inbox.id,
        "inbox_email": inbox.email,
        "inbox_display_name": inbox.display_name or "",
        "thread_id": thread.thread_id,
        "subject": subject,
        "snippet": snippet,
        "last_message_at": last_message_at,
        "message_count": len(msgs),
        "participants": sorted(participant_set),
        "external_participants": sorted(external),
        "has_unread": False,
        "from_server": False,
        "server_sent_count": 0,
        "linked_lead": None,
        "campaigns": [],
        "can_reply": True,
    }


async def _ensure_gmail_access_token(db: AsyncSession, gmail_account: GmailAccount) -> str | None:
    if gmail_account.token_expiry and gmail_account.token_expiry <= time_provider.utcnow():
        client_id, client_secret = await get_google_oauth_credentials(db)
        refreshed = refresh_access_token(gmail_account, client_id, client_secret)
        if not refreshed:
            return None
        await db.flush()
        return refreshed
    return gmail_account.access_token or None


async def _load_server_context(db: AsyncSession, inbox_ids: list[int], force_refresh: bool = False) -> dict:
    if not inbox_ids:
        return {"by_thread": {}, "by_message_id": {}, "by_inbox_group": {}}

    cache_key = ",".join(str(i) for i in sorted(set(inbox_ids)))
    if not force_refresh:
        cached = _cache_get(_SERVER_CONTEXT_CACHE, cache_key, _SERVER_CONTEXT_CACHE_TTL_SECONDS)
        if cached:
            return cached

    result = await db.execute(
        select(EmailLog, Lead, Campaign)
        .join(Lead, EmailLog.lead_id == Lead.id)
        .join(Campaign, EmailLog.campaign_id == Campaign.id)
        .where(EmailLog.inbox_id.in_(inbox_ids))
        .order_by(EmailLog.sent_at.desc())
    )

    by_thread: dict[tuple[int, str], list[dict]] = {}
    by_message_id: dict[str, list[dict]] = {}
    by_inbox_group: dict[tuple[int, int, int], list[dict]] = {}

    for email_log, lead, campaign in result.all():
        entry = _server_entry_from_row(email_log, lead, campaign)

        if email_log.thread_id:
            key = (email_log.inbox_id or -1, email_log.thread_id)
            by_thread.setdefault(key, []).append(entry)

        normalized_mid = _normalize_message_id(email_log.message_id)
        if normalized_mid:
            by_message_id.setdefault(normalized_mid, []).append(entry)

        if email_log.inbox_id is not None:
            group_key = (email_log.inbox_id, lead.id, campaign.id)
            by_inbox_group.setdefault(group_key, []).append(entry)

    out = {
        "by_thread": by_thread,
        "by_message_id": by_message_id,
        "by_inbox_group": by_inbox_group,
    }
    _cache_set(_SERVER_CONTEXT_CACHE, cache_key, out)
    return out


def _pick_lead_and_campaign(entries: list[dict]) -> tuple[dict | None, list[dict]]:
    if not entries:
        return None, []
    latest = entries[0]
    lead = {
        "id": latest["lead_id"],
        "email": latest["lead_email"],
        "name": latest["lead_name"],
        "status": latest["lead_status"],
    }
    campaigns_by_id: dict[int, dict] = {}
    for entry in entries:
        cid = entry["campaign_id"]
        if cid not in campaigns_by_id:
            campaigns_by_id[cid] = {"id": cid, "name": entry["campaign_name"]}
    return lead, list(campaigns_by_id.values())


def _build_gmail_conversation(
    inbox: Inbox,
    thread_payload: dict,
    server_context: dict,
) -> dict:
    # legacy helper retained for backward compatibility; new code uses
    # _build_db_conversation instead.
    messages = thread_payload.get("messages") or []
    participants: set[str] = set()
    external_participants: set[str] = set()
    normalized_message_ids: list[str] = []
    unread = False
    last_message_at = ""
    snippet = thread_payload.get("snippet", "") or ""
    subject = ""

    for message in messages:
        payload = message.get("payload") or {}
        headers = payload.get("headers") or []

        from_header = _extract_header(headers, "From")
        to_header = _extract_header(headers, "To")
        cc_header = _extract_header(headers, "Cc")
        subject_header = _extract_header(headers, "Subject")
        message_id_header = _extract_header(headers, "Message-Id")
        sent_at = _iso_from_internal_ms(message.get("internalDate"))

        if sent_at and (not last_message_at or sent_at > last_message_at):
            last_message_at = sent_at
            if subject_header:
                subject = subject_header
            message_snippet = (message.get("snippet") or "").strip()
            if message_snippet:
                snippet = message_snippet

        for address in _extract_emails(from_header):
            participants.add(address)
        for address in _extract_emails(to_header):
            participants.add(address)
        for address in _extract_emails(cc_header):
            participants.add(address)

        normalized_mid = _normalize_message_id(message_id_header)
        if normalized_mid:
            normalized_message_ids.append(normalized_mid)

        if "UNREAD" in (message.get("labelIds") or []):
            unread = True

    inbox_email = (inbox.email or "").lower()
    for participant in participants:
        if participant != inbox_email:
            external_participants.add(participant)

    thread_id = thread_payload.get("id", "")
    server_entries = list(server_context["by_thread"].get((inbox.id, thread_id), []))
    if not server_entries:
        for message_id in normalized_message_ids:
            for entry in server_context["by_message_id"].get(message_id, []):
                if entry["inbox_id"] == inbox.id:
                    server_entries.append(entry)

    # Most recent server entry first.
    server_entries.sort(key=lambda item: item.get("sent_at") or "", reverse=True)
    linked_lead, campaigns = _pick_lead_and_campaign(server_entries)

    return {
        "provider": "gmail",
        "source": "gmail",
        "inbox_id": inbox.id,
        "inbox_email": inbox.email,
        "inbox_display_name": inbox.display_name or "",
        "thread_id": thread_id,
        "subject": subject or "(no subject)",
        "snippet": snippet,
        "last_message_at": last_message_at,
        "message_count": len(messages),
        "participants": sorted(participants),
        "external_participants": sorted(external_participants),
        "has_unread": unread,
        "from_server": bool(server_entries),
        "server_sent_count": len(server_entries),
        "linked_lead": linked_lead,
        "campaigns": campaigns,
        "can_reply": True,
    }


async def _map_leads_by_email(db: AsyncSession, emails: set[str]) -> dict[str, Lead]:
    if not emails:
        return {}
    result = await db.execute(select(Lead).where(Lead.email.in_(emails)))
    leads = result.scalars().all()
    return {lead.email.lower(): lead for lead in leads}


def _conversation_matches_filters(
    conversation: dict,
    server_only: bool,
    has_lead: bool,
    participant_email: str,
    query_text: str,
) -> bool:
    if server_only and not conversation.get("from_server"):
        return False
    if has_lead and not conversation.get("linked_lead"):
        return False
    if participant_email:
        participants = {p.lower() for p in conversation.get("participants", [])}
        if participant_email.lower() not in participants:
            return False
    if query_text:
        haystack = " ".join(
            [
                conversation.get("subject", ""),
                conversation.get("snippet", ""),
                " ".join(conversation.get("participants", [])),
                " ".join(c.get("name", "") for c in conversation.get("campaigns", [])),
                (conversation.get("linked_lead") or {}).get("email", ""),
                (conversation.get("linked_lead") or {}).get("name", ""),
            ]
        ).lower()
        if query_text.lower() not in haystack:
            return False
    return True


def _pick_detail_target(
    items: list[dict],
    provider: str | None,
    inbox_id: int | None,
    thread_id: str | None,
) -> dict | None:
    if provider and inbox_id is not None and thread_id:
        for item in items:
            if (
                item.get("provider") == provider
                and int(item.get("inbox_id") or 0) == int(inbox_id)
                and item.get("thread_id") == thread_id
            ):
                return item
    return items[0] if items else None


def _serialize_inboxes(inboxes: list[Inbox]) -> list[dict]:
    return [
        {
            "id": inbox.id,
            "email": inbox.email,
            "provider": inbox.provider,
            "display_name": inbox.display_name or "",
        }
        for inbox in inboxes
    ]


async def _build_conversation_detail(
    provider: str,
    inbox_id: int,
    thread_id: str,
    refresh: bool,
    db: AsyncSession,
) -> dict:
    inbox_result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = inbox_result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")

    if provider == "gmail":
        # read thread from local mirror
        thr = await db.get(GmailThread, (inbox_id, thread_id))
        if not thr:
            raise HTTPException(404, "Thread not found")

        # optionally refresh bodies for this thread if requested
        result = await db.execute(
            select(GmailMessage)
            .where(
                GmailMessage.inbox_id == inbox_id,
                GmailMessage.thread_id == thread_id,
            )
            .order_by(GmailMessage.internal_date.asc())
        )
        msgs: list[GmailMessage] = result.scalars().all()

        server_context = await _load_server_context(db, [inbox_id], force_refresh=refresh)
        thread_entries = list(server_context["by_thread"].get((inbox_id, thread_id), []))

        messages_out: list[dict] = []
        participant_set: set[str] = set()

        # always ensure we have bodies for display; refresh just forces
        # a second pass if some fetches fail or the cache wants busting.
        from app.gmail_sync import _ensure_message_body
        for msgrow in msgs:
            if not msgrow.body_fetched:
                await _ensure_message_body(db, inbox_id, msgrow.message_id)
        if refresh:
            # reload rows in case any were just updated
            result = await db.execute(
                select(GmailMessage)
                .where(
                    GmailMessage.inbox_id == inbox_id,
                    GmailMessage.thread_id == thread_id,
                )
                .order_by(GmailMessage.internal_date.asc())
            )
            msgs = result.scalars().all()

        for msgrow in msgs:
            headers = []
            try:
                headers = json.loads(msgrow.headers_json or "[]")
            except Exception:
                pass
            from_header = _extract_header(headers, "From")
            to_header = _extract_header(headers, "To")
            cc_header = _extract_header(headers, "Cc")
            subject_header = _extract_header(headers, "Subject")
            message_id_header = _extract_header(headers, "Message-Id")
            in_reply_to = _extract_header(headers, "In-Reply-To")

            normalized_mid = _normalize_message_id(message_id_header)
            by_mid_entries = server_context["by_message_id"].get(normalized_mid, [])
            is_server_sent = any(entry["inbox_id"] == inbox_id for entry in by_mid_entries)

            plain_body = msgrow.body_plain or ""
            html_body = msgrow.body_html or ""
            if not plain_body:
                plain_body = (msgrow.snippet or "").strip()

            from_emails = _extract_emails(from_header)
            to_emails = _extract_emails(to_header)
            cc_emails = _extract_emails(cc_header)
            for value in from_emails + to_emails + cc_emails:
                participant_set.add(value)

            messages_out.append(
                {
                    "id": msgrow.message_id,
                    "thread_id": thread_id,
                    "sent_at": _iso_from_internal_ms(msgrow.internal_date),
                    "from_header": from_header,
                    "to_header": to_header,
                    "cc_header": cc_header,
                    "from_email": from_emails[0] if from_emails else "",
                    "to_emails": to_emails,
                    "cc_emails": cc_emails,
                    "subject": subject_header or "(no subject)",
                    "snippet": msgrow.snippet or "",
                    "message_id": message_id_header,
                    "in_reply_to": in_reply_to,
                    "body_text": plain_body,
                    "body_html": html_body,
                    "is_from_inbox": (from_emails[0] if from_emails else "") == inbox.email.lower(),
                    "is_server_sent": is_server_sent,
                }
            )

        linked_lead, campaigns = _pick_lead_and_campaign(thread_entries)
        if not linked_lead:
            external_emails = {p for p in participant_set if p != inbox.email.lower()}
            leads_by_email = await _map_leads_by_email(db, external_emails)
            for email_value in external_emails:
                lead = leads_by_email.get(email_value)
                if lead:
                    linked_lead = {
                        "id": lead.id,
                        "email": lead.email,
                        "name": lead.name or "",
                        "status": lead.status,
                    }
                    break

        subject = ""
        if messages_out:
            subject = messages_out[-1]["subject"] or ""
        if not subject:
            subject = thr.snippet or "(no subject)" or "(no subject)"

        return {
            "provider": "gmail",
            "inbox_id": inbox.id,
            "inbox_email": inbox.email,
            "thread_id": thread_id,
            "subject": subject,
            "participants": sorted(participant_set),
            "linked_lead": linked_lead,
            "campaigns": campaigns,
            "messages": messages_out,
            "can_reply": True,
            "cache_status": "miss",
            "fetched_at": time_provider.utcnow().isoformat(),
        }

    if provider == "server":
        match = re.fullmatch(r"lead-(\d+)-campaign-(\d+)", thread_id)
        if not match:
            raise HTTPException(404, "Server conversation not found")
        lead_id = int(match.group(1))
        campaign_id = int(match.group(2))
        result = await db.execute(
            select(EmailLog, Lead, Campaign)
            .join(Lead, EmailLog.lead_id == Lead.id)
            .join(Campaign, EmailLog.campaign_id == Campaign.id)
            .where(
                EmailLog.inbox_id == inbox_id,
                EmailLog.lead_id == lead_id,
                EmailLog.campaign_id == campaign_id,
            )
            .order_by(EmailLog.sent_at.asc())
        )
        rows = result.all()
        if not rows:
            raise HTTPException(404, "Server conversation not found")

        lead = rows[0][1]
        campaign = rows[0][2]
        messages = []
        for email_log, _lead, _campaign in rows:
            messages.append(
                {
                    "id": f"email-log-{email_log.id}",
                    "thread_id": thread_id,
                    "sent_at": email_log.sent_at.isoformat() if email_log.sent_at else "",
                    "from_header": inbox.email,
                    "to_header": lead.email,
                    "cc_header": "",
                    "from_email": inbox.email,
                    "to_emails": [lead.email],
                    "cc_emails": [],
                    "subject": email_log.subject or "(no subject)",
                    "snippet": email_log.subject or "",
                    "message_id": email_log.message_id or "",
                    "in_reply_to": "",
                    "body_text": "",
                    "body_html": "",
                    "is_from_inbox": True,
                    "is_server_sent": True,
                }
            )
        return {
            "provider": "server",
            "inbox_id": inbox.id,
            "inbox_email": inbox.email,
            "thread_id": thread_id,
            "subject": messages[-1]["subject"],
            "participants": [inbox.email.lower(), lead.email.lower()],
            "linked_lead": {
                "id": lead.id,
                "email": lead.email,
                "name": lead.name or "",
                "status": lead.status,
            },
            "campaigns": [{"id": campaign.id, "name": campaign.name}],
            "messages": messages,
            "can_reply": False,
            "cache_status": "miss",
            "fetched_at": time_provider.utcnow().isoformat(),
        }

    raise HTTPException(400, "Unsupported provider")


async def _fetch_conversation_detail(
    provider: str,
    inbox_id: int,
    thread_id: str,
    refresh: bool,
    db: AsyncSession,
) -> dict:
    cache_key = _thread_detail_cache_key(provider, inbox_id, thread_id)
    if not refresh:
        cached = _cache_get(_THREAD_DETAIL_CACHE, cache_key, _THREAD_DETAIL_CACHE_TTL_SECONDS)
        if cached:
            out = dict(cached)
            out["cache_status"] = "hit"
            return out
        persisted, age_seconds = await _persisted_cache_get(
            db,
            cache_key,
            _THREAD_DETAIL_CACHE_MAX_AGE_SECONDS,
        )
        if persisted:
            out = dict(persisted)
            out["cache_status"] = _cache_status_from_age(age_seconds, _THREAD_DETAIL_CACHE_TTL_SECONDS)
            if age_seconds is not None:
                out["cache_age_seconds"] = int(age_seconds)
            return out

    detail = await _build_conversation_detail(provider, inbox_id, thread_id, refresh, db)
    _cache_set(_THREAD_DETAIL_CACHE, cache_key, detail)
    await _persisted_cache_set(db, cache_key, detail)
    return detail


@router.get("/conversations")
async def list_conversations(
    inbox_id: int | None = Query(None),
    server_only: bool = Query(False),
    has_lead: bool = Query(False),
    participant_email: str | None = Query(None),
    q: str | None = Query(None),
    cursor: str | None = Query(None),
    page_size: int = Query(40, ge=10, le=100),
    refresh: bool = Query(False),
    include_inboxes: bool = Query(False),
    include_detail: bool = Query(False),
    detail_provider: str | None = Query(None),
    detail_inbox_id: int | None = Query(None),
    detail_thread_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    decoded_cursor = _decode_cursor(cursor)
    cache_params = {
        "inbox_id": inbox_id,
        "server_only": server_only,
        "has_lead": has_lead,
        "participant_email": (participant_email or "").strip().lower(),
        "q": (q or "").strip().lower(),
        "page_size": page_size,
        "cursor": decoded_cursor,
    }
    conversation_cache_key = _conversation_list_cache_key(cache_params)

    async def attach_extras(payload: dict, inbox_rows: list | None = None) -> dict:
        out = dict(payload)
        if include_inboxes:
            if inbox_rows is None:
                inbox_result = await db.execute(select(Inbox).order_by(Inbox.id))
                inboxes = inbox_result.scalars().all()
            else:
                inboxes = [inbox for inbox, _ga in inbox_rows]
            out["inboxes"] = _serialize_inboxes(inboxes)
        if include_detail:
            items = out.get("items") or []
            target = _pick_detail_target(items, detail_provider, detail_inbox_id, detail_thread_id)
            if target:
                detail = await _fetch_conversation_detail(
                    target.get("provider") or "",
                    int(target.get("inbox_id") or 0),
                    target.get("thread_id") or "",
                    refresh=refresh,
                    db=db,
                )
                out["detail"] = detail
        return out

    if not refresh:
        cached = _cache_get(_CONVERSATION_LIST_CACHE, conversation_cache_key, _CONVERSATION_CACHE_TTL_SECONDS)
        if cached:
            out = dict(cached)
            out["cache_status"] = "hit"
            return await attach_extras(out)
        persisted, age_seconds = await _persisted_cache_get(
            db,
            conversation_cache_key,
            _CONVERSATION_CACHE_MAX_AGE_SECONDS,
        )
        if persisted:
            out = dict(persisted)
            out["cache_status"] = _cache_status_from_age(age_seconds, _CONVERSATION_CACHE_TTL_SECONDS)
            if age_seconds is not None:
                out["cache_age_seconds"] = int(age_seconds)
            return await attach_extras(out)

    inbox_query = (
        select(Inbox, GmailAccount)
        .outerjoin(GmailAccount, GmailAccount.inbox_id == Inbox.id)
        .order_by(Inbox.id)
    )
    if inbox_id is not None:
        inbox_query = inbox_query.where(Inbox.id == inbox_id)
    inbox_rows = (await db.execute(inbox_query)).all()

    if not inbox_rows:
        payload = {
            "items": [],
            "warnings": [],
            "provider_support": {"gmail": "supported", "microsoft365": "planned"},
            "next_cursor": None,
            "has_more": False,
            "cache_status": "miss",
            "fetched_at": time_provider.utcnow().isoformat(),
        }
        _cache_set(_CONVERSATION_LIST_CACHE, conversation_cache_key, payload)
        await _persisted_cache_set(db, conversation_cache_key, payload)
        return await attach_extras(payload, inbox_rows=inbox_rows)

    inbox_ids = [inbox.id for inbox, _ga in inbox_rows]
    server_context = await _load_server_context(db, inbox_ids, force_refresh=refresh)

    warnings: list[str] = []
    conversations: list[dict] = []
    seen_gmail_threads: set[tuple[int, str]] = set()
    next_cursor_map: dict[int, str] = {}

    participant_filter = (participant_email or "").strip().lower()
    query_filter = (q or "").strip()
    gmail_inboxes = [(inbox, ga) for inbox, ga in inbox_rows if inbox.provider == "gmail" and ga]

    # rather than calling Gmail directly we use our local mirror tables
    for inbox, gmail_account in gmail_inboxes:
        if inbox.provider == "gmail":
            # load all threads for this inbox, later paging could be added
            thread_q = select(GmailThread).where(GmailThread.inbox_id == inbox.id)
            thread_q = thread_q.order_by(GmailThread.last_internal_date.desc())
            res = await db.execute(thread_q)
            for thr in res.scalars().all():
                conv = await _build_db_conversation(inbox, thr, db)
                conversations.append(conv)
                seen_gmail_threads.add((inbox.id, thr.thread_id))

    # Add server-side sent history groups as fallback conversations:
    # - for non-Gmail inboxes (Resend/SMTP)
    # - for Gmail sends that are not visible in the fetched Gmail thread window
    inbox_map = {inbox.id: inbox for inbox, _ga in inbox_rows}
    for (group_inbox_id, lead_id, campaign_id), entries in server_context["by_inbox_group"].items():
        if group_inbox_id not in inbox_map:
            continue
        inbox = inbox_map[group_inbox_id]
        latest = entries[0]
        thread_key = latest.get("thread_id", "")
        if thread_key and (group_inbox_id, thread_key) in seen_gmail_threads:
            continue

        linked_lead, campaigns = _pick_lead_and_campaign(entries)
        participants = [p for p in {latest["lead_email"], inbox.email.lower()} if p]
        fallback_thread_id = thread_key or f"lead-{lead_id}-campaign-{campaign_id}"
        can_reply = bool(inbox.provider == "gmail" and thread_key)

        conversations.append(
            {
                "provider": "gmail" if can_reply else "server",
                "source": "server_log",
                "inbox_id": inbox.id,
                "inbox_email": inbox.email,
                "inbox_display_name": inbox.display_name or "",
                "thread_id": fallback_thread_id,
                "subject": latest.get("subject") or "(no subject)",
                "snippet": latest.get("subject") or "",
                "last_message_at": latest.get("sent_at") or "",
                "message_count": len(entries),
                "participants": participants,
                "external_participants": [latest["lead_email"]],
                "has_unread": False,
                "from_server": True,
                "server_sent_count": len(entries),
                "linked_lead": linked_lead,
                "campaigns": campaigns,
                "can_reply": can_reply,
            }
        )

    # Fill lead mapping for conversations with known participants but no server linkage.
    participant_emails = set()
    for conversation in conversations:
        for email_value in conversation.get("external_participants", []):
            participant_emails.add(email_value.lower())
    leads_by_email = await _map_leads_by_email(db, participant_emails)
    for conversation in conversations:
        if conversation.get("linked_lead"):
            continue
        for email_value in conversation.get("external_participants", []):
            lead = leads_by_email.get(email_value.lower())
            if lead:
                conversation["linked_lead"] = {
                    "id": lead.id,
                    "email": lead.email,
                    "name": lead.name or "",
                    "status": lead.status,
                }
                break

    filtered = [
        c
        for c in conversations
        if _conversation_matches_filters(
            c,
            server_only=server_only,
            has_lead=has_lead,
            participant_email=participant_filter,
            query_text=query_filter,
        )
    ]

    filtered.sort(key=lambda item: item.get("last_message_at") or "", reverse=True)
    payload = {
        "items": filtered[:page_size],
        "warnings": warnings,
        "provider_support": {"gmail": "supported", "microsoft365": "planned"},
        "next_cursor": _encode_cursor(next_cursor_map),
        "has_more": bool(next_cursor_map),
        "cache_status": "miss",
        "fetched_at": time_provider.utcnow().isoformat(),
    }
    _cache_set(_CONVERSATION_LIST_CACHE, conversation_cache_key, payload)
    await _persisted_cache_set(db, conversation_cache_key, payload)
    return await attach_extras(payload, inbox_rows=inbox_rows)


@router.get("/conversations/{provider}/{inbox_id}/{thread_id}")
async def get_conversation_detail(
    provider: str,
    inbox_id: int,
    thread_id: str,
    refresh: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    return await _fetch_conversation_detail(provider, inbox_id, thread_id, refresh, db)


@router.get("/messages/{provider}/{inbox_id}/{message_id}")
async def get_message_detail(
    provider: str,
    inbox_id: int,
    message_id: str,
    refresh: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    if provider != "gmail":
        raise HTTPException(400, "Only Gmail messages are supported")
    row = await db.get(GmailMessage, (inbox_id, message_id))
    if not row:
        raise HTTPException(404, "Message not found")
    if refresh and not row.body_fetched:
        from app.gmail_sync import _ensure_message_body

        await _ensure_message_body(db, inbox_id, message_id)
        await db.refresh(row)
    return {
        "message_id": row.message_id,
        "thread_id": row.thread_id,
        "sent_at": _iso_from_internal_ms(row.internal_date),
        "snippet": row.snippet,
        "headers": json.loads(row.headers_json or "[]"),
        "label_ids": json.loads(row.label_ids_json or "[]"),
        "body_plain": row.body_plain if row.body_fetched else "",
        "body_html": row.body_html if row.body_fetched else "",
        "body_fetched": row.body_fetched,
    }


@router.get("/attachments/{inbox_id}/{attachment_id}")
async def get_attachment(
    inbox_id: int,
    attachment_id: str,
    download: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GmailAttachment).where(
            GmailAttachment.inbox_id == inbox_id,
            GmailAttachment.attachment_id == attachment_id,
        )
    )
    att = result.scalar_one_or_none()
    if not att:
        raise HTTPException(404, "Attachment not found")
    if download and not att.downloaded:
        from app.gmail_sync import _download_gmail_attachment

        await _download_gmail_attachment(db, inbox_id, attachment_id)
        await db.refresh(att)
    payload: dict = {
        "attachment_id": att.attachment_id,
        "filename": att.filename,
        "mime_type": att.mime_type,
        "size": att.size,
        "downloaded": att.downloaded,
    }
    if att.downloaded and att.data is not None:
        payload["data_base64"] = base64.b64encode(att.data).decode("ascii")
    return payload


@router.post("/reply")
async def reply_in_thread(body: UniboxReplyRequest, db: AsyncSession = Depends(get_db)):
    if body.provider != "gmail":
        raise HTTPException(400, "Only Gmail replies are supported right now (Microsoft 365 is planned).")

    to_email = (body.to_email or "").strip().lower()
    if "@" not in to_email:
        raise HTTPException(400, "A valid recipient email is required.")

    inbox_result = await db.execute(select(Inbox).where(Inbox.id == body.inbox_id))
    inbox = inbox_result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    if inbox.provider != "gmail":
        raise HTTPException(400, "This inbox is not configured as Gmail.")

    ga_result = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox.id))
    gmail_account = ga_result.scalar_one_or_none()
    if not gmail_account:
        raise HTTPException(404, "No Gmail OAuth account linked to this inbox.")

    access_token = await _ensure_gmail_access_token(db, gmail_account)
    if not access_token:
        raise HTTPException(502, "Could not refresh Gmail token")

    meta_url = (
        f"{GMAIL_API_BASE}/threads/{urllib.parse.quote(body.thread_id)}"
        "?format=metadata"
        "&metadataHeaders=Subject"
        "&metadataHeaders=Message-Id"
    )
    try:
        thread_payload = _gmail_request_json(access_token, meta_url)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))

    chain_ids: list[str] = []
    latest_message_id = None
    latest_subject = ""
    for message in sorted(
        thread_payload.get("messages") or [],
        key=lambda m: int(m.get("internalDate") or 0),
    ):
        headers = (message.get("payload") or {}).get("headers") or []
        msg_id = _extract_header(headers, "Message-Id")
        if msg_id:
            latest_message_id = msg_id
            normalized = _normalize_message_id(msg_id)
            if normalized:
                chain_ids.append(f"<{normalized}>")
        msg_subject = _extract_header(headers, "Subject")
        if msg_subject:
            latest_subject = msg_subject

    subject = (body.subject or "").strip() or _derive_reply_subject(latest_subject)
    references = " ".join(chain_ids) if chain_ids else None

    send_result = send_email(
        to_email=to_email,
        subject=subject,
        body=body.body,
        from_email=inbox.email,
        from_name=inbox.display_name or "",
        reply_to_msg_id=latest_message_id,
        references=references,
        is_html=body.is_html,
        provider="gmail",
        gmail_access_token=access_token,
        thread_id=body.thread_id,
    )
    if not send_result:
        raise HTTPException(502, "Failed to send Gmail reply")

    # If this thread is tied to campaign sends, store this manual reply in email_log
    # so "server-sent only" filters can include it.
    linkage_result = await db.execute(
        select(EmailLog)
        .where(
            EmailLog.inbox_id == inbox.id,
            EmailLog.thread_id == body.thread_id,
        )
        .order_by(EmailLog.sent_at.desc())
    )
    linkage = linkage_result.scalars().first()
    linked = False
    if linkage:
        db.add(
            EmailLog(
                lead_id=linkage.lead_id,
                campaign_id=linkage.campaign_id,
                inbox_id=inbox.id,
                sequence_index=-1,
                subject=subject,
                message_id=send_result.message_id,
                thread_id=send_result.thread_id or body.thread_id,
            )
        )
        await db.flush()
        linked = True

    _invalidate_unibox_caches(inbox_id=inbox.id, thread_id=(send_result.thread_id or body.thread_id))
    await _invalidate_persisted_caches(
        db,
        provider="gmail",
        inbox_id=inbox.id,
        thread_id=(send_result.thread_id or body.thread_id),
    )

    return {
        "ok": True,
        "provider": "gmail",
        "inbox_id": inbox.id,
        "thread_id": send_result.thread_id or body.thread_id,
        "message_id": send_result.message_id,
        "linked_to_campaign_history": linked,
    }
