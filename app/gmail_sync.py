"""Gmail reply synchronization and mailbox mirroring.

This module maintains a durable mirror of threads/messages/attachments in
the main database as described in the unibox design.  It implements initial
and incremental history syncs, lazy body/attachment fetching, and the
reply-detection logic used by campaigns.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import getaddresses

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import time as time_provider
from app.app_settings import get_gmail_sync_config, get_google_oauth_credentials
from app.database import AsyncSessionLocal
from app.models import (
    EmailLog,
    GmailAccount,
    GmailSyncState,
    Inbox,
    Lead,
    LeadReply,
    GmailThread,
    GmailMessage,
    GmailAttachment,
)
from app.routers.gmail_oauth import refresh_access_token

# helpers from unibox router let us keep list/detail caches roughly in sync when
# new inbound messages are seen by the history sync job.  We intentionally
# import individual functions rather than the whole module to avoid a
# circular import (unibox itself does not import gmail_sync).
from app.routers.unibox import (
    _invalidate_unibox_caches,
    _invalidate_persisted_caches,
    _get_gmail_thread_metadata,
)


log = logging.getLogger("quickly.gmail_sync")

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailApiError(RuntimeError):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


def _extract_header(headers: list[dict], name: str) -> str:
    wanted = name.lower()
    for header in headers:
        if str(header.get("name", "")).lower() == wanted:
            return str(header.get("value", "")).strip()
    return ""


def _extract_emails(value: str) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for _label, address in getaddresses([value]):
        email_value = address.strip().lower()
        if email_value and email_value not in seen:
            seen.add(email_value)
            out.append(email_value)
    return out


def _normalize_message_id(value: str | None) -> str:
    if not value:
        return ""
    out = value.strip()
    if out.startswith("<") and out.endswith(">"):
        out = out[1:-1]
    return out.strip().lower()


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
        raise GmailApiError(f"Gmail API error ({exc.code} {exc.reason}): {detail}", code=exc.code) from exc
    except Exception as exc:
        raise GmailApiError(f"Gmail API request failed: {exc}") from exc


def parse_gmail_push_payload(payload: dict) -> tuple[str, str]:
    """Return (email_address, history_id) from a Gmail Pub/Sub push payload."""
    # Native Gmail payload (tests/manual usage)
    direct_email = str(payload.get("emailAddress") or "").strip().lower()
    direct_history = str(payload.get("historyId") or "").strip()
    if direct_email and direct_history:
        return direct_email, direct_history

    message = payload.get("message") or {}
    data_b64 = message.get("data")
    if not data_b64:
        return "", ""

    try:
        decoded = base64.urlsafe_b64decode(data_b64 + "=" * (-len(data_b64) % 4)).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception:
        return "", ""

    email_value = str(parsed.get("emailAddress") or "").strip().lower()
    history_id = str(parsed.get("historyId") or "").strip()
    return email_value, history_id


async def _ensure_access_token(db: AsyncSession, gmail_account: GmailAccount) -> str | None:
    if gmail_account.token_expiry and gmail_account.token_expiry <= time_provider.utcnow():
        client_id, client_secret = await get_google_oauth_credentials(db)
        refreshed = refresh_access_token(gmail_account, client_id, client_secret)
        if not refreshed:
            return None
        await db.flush()
        return refreshed
    return gmail_account.access_token or None


async def _get_or_create_sync_state(db: AsyncSession, inbox_id: int) -> GmailSyncState:
    result = await db.execute(select(GmailSyncState).where(GmailSyncState.inbox_id == inbox_id))
    state = result.scalar_one_or_none()
    if state:
        return state
    state = GmailSyncState(inbox_id=inbox_id, last_history_id="")
    db.add(state)
    await db.flush()
    return state


def _as_utc_datetime_from_ms(value: str | int | None) -> datetime | None:
    try:
        ms = int(value)
    except Exception:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)


async def _get_profile_history_id(access_token: str) -> str:
    profile = _gmail_request_json(access_token, f"{GMAIL_API_BASE}/profile")
    return str(profile.get("historyId") or "").strip()


async def _save_thread_and_messages(
    db: AsyncSession,
    inbox_id: int,
    thread_payload: dict,
) -> None:
    """Insert or update a thread and all of its messages (metadata only).

    The Gmail API returns thread objects with an array of messages; we
    eagerly persist all of that metadata so the frontend can later query it
    without talking to Google.  Bodies are deliberately omitted and are
    fetched lazily.
    """
    if not thread_payload:
        return

    thread_id = str(thread_payload.get("id") or "").strip()
    if not thread_id:
        return

    history_id = str(thread_payload.get("historyId") or "").strip()
    snippet = str(thread_payload.get("snippet") or "")

    messages = thread_payload.get("messages") or []
    last_internal: int | None = None
    for msg in messages:
        try:
            internal = int(msg.get("internalDate") or 0)
        except Exception:
            internal = 0
        if internal and (last_internal is None or internal > last_internal):
            last_internal = internal

    # upsert thread row
    existing_thread = await db.get(GmailThread, (inbox_id, thread_id))
    if existing_thread:
        if history_id:
            existing_thread.history_id = history_id
        if snippet:
            existing_thread.snippet = snippet
        if last_internal is not None:
            existing_thread.last_internal_date = last_internal
    else:
        db.add(
            GmailThread(
                inbox_id=inbox_id,
                thread_id=thread_id,
                history_id=history_id,
                snippet=snippet,
                last_internal_date=last_internal,
            )
        )

    # upsert each message
    for msg in messages:
        msg_id = str(msg.get("id") or "").strip()
        if not msg_id:
            continue
        try:
            internal = int(msg.get("internalDate") or 0)
        except Exception:
            internal = None
        snippet_msg = str(msg.get("snippet") or "")
        headers = (msg.get("payload") or {}).get("headers") or []
        headers_json = json.dumps(headers)
        label_ids = msg.get("labelIds") or []
        label_ids_json = json.dumps(label_ids)

        existing_msg = await db.get(GmailMessage, (inbox_id, msg_id))
        if existing_msg:
            existing_msg.thread_id = thread_id
            if internal is not None:
                existing_msg.internal_date = internal
            if snippet_msg:
                existing_msg.snippet = snippet_msg
            existing_msg.headers_json = headers_json
            existing_msg.label_ids_json = label_ids_json
        else:
            db.add(
                GmailMessage(
                    inbox_id=inbox_id,
                    message_id=msg_id,
                    thread_id=thread_id,
                    internal_date=internal,
                    snippet=snippet_msg,
                    headers_json=headers_json,
                    label_ids_json=label_ids_json,
                    body_fetched=False,
                )
            )
    # flush here so callers can rely on rows being visible immediately
    await db.flush()


async def _initial_sync(
    access_token: str,
    inbox_id: int,
    db: AsyncSession,
    state: GmailSyncState,
) -> None:
    """Perform initial sync of the last week of messages into the local mirror."""
    latest_history = ""
    oldest_date: int | None = None
    page_token = ""

    while True:
        url = f"{GMAIL_API_BASE}/threads?q=newer_than:7d&maxResults=500"
        if page_token:
            url += "&pageToken=" + urllib.parse.quote(page_token)

        resp = _gmail_request_json(access_token, url)
        for thread in resp.get("threads") or []:
            tid = str(thread.get("id") or "").strip()
            if not tid:
                continue
            thread_data = _gmail_request_json(
                access_token,
                f"{GMAIL_API_BASE}/threads/{urllib.parse.quote(tid)}?format=metadata",
            )
            await _save_thread_and_messages(db, inbox_id, thread_data)
            hist = str(thread_data.get("historyId") or "").strip()
            if hist and (not latest_history or int(hist) > int(latest_history)):
                latest_history = hist
            for msg in thread_data.get("messages") or []:
                try:
                    internal = int(msg.get("internalDate") or 0)
                except Exception:
                    internal = 0
                if internal and (oldest_date is None or internal < oldest_date):
                    oldest_date = internal
        page_token = str(resp.get("nextPageToken") or "").strip()
        if not page_token:
            break

    if latest_history:
        state.anchor_history_id = latest_history
        state.latest_history_id = latest_history
    if oldest_date is not None:
        state.oldest_internal_date = oldest_date
    await db.flush()


async def _bounded_resync(
    access_token: str,
    inbox_id: int,
    db: AsyncSession,
    state: GmailSyncState,
) -> None:
    """Recover from an expired history window by re-syncing the last week."""
    # reuse the initial sync logic; the only difference is that callers will
    # update the history id afterwards
    await _initial_sync(access_token, inbox_id, db, state)


async def _ensure_message_body(
    db: AsyncSession,
    inbox_id: int,
    message_id: str,
) -> GmailMessage | None:
    """Fetch the full message body from Gmail if it hasn't been pulled yet.

    Returns the database row, whether it was updated or not.  If the inbox
    doesn't have a linked Gmail account or the token cannot be refreshed we
    simply return the row as stored (bodies may be empty).
    """
    row = await db.get(GmailMessage, (inbox_id, message_id))
    if not row:
        return None
    if row.body_fetched:
        return row

    # acquire an access token for the inbox
    ga = None
    result = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox_id))
    ga = result.scalar_one_or_none()
    access_token = None
    if ga:
        access_token = await _ensure_access_token(db, ga)
    if not access_token:
        # no token means we can't fetch; just return what we have
        return row

    try:
        full = _gmail_request_json(
            access_token,
            f"{GMAIL_API_BASE}/messages/{urllib.parse.quote(message_id)}?format=full",
        )
    except GmailApiError:
        return row

    # parse bodies and attachments
    payload = full.get("payload") or {}
    # reuse parser from unibox to avoid duplication
    from app.routers.unibox import _extract_payload_bodies

    plain_body, html_body = _extract_payload_bodies(payload)

    # attachments metadata walk
    attachments: list[dict] = []

    def walk_parts(part: dict) -> None:
        if not isinstance(part, dict):
            return
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        filename = part.get("filename")
        mime_type = part.get("mimeType")
        size = int(body.get("size") or 0)
        if attachment_id:
            attachments.append(
                {
                    "attachment_id": str(attachment_id),
                    "filename": str(filename or ""),
                    "mime_type": str(mime_type or ""),
                    "size": size,
                }
            )
        for child in part.get("parts") or []:
            walk_parts(child)

    walk_parts(payload)

    row.body_plain = plain_body
    row.body_html = html_body
    row.body_fetched = True
    await db.flush()

    # store attachment rows lazily
    for att in attachments:
        existing = await db.execute(
            select(GmailAttachment)
            .where(
                GmailAttachment.inbox_id == inbox_id,
                GmailAttachment.message_id == message_id,
                GmailAttachment.attachment_id == att["attachment_id"],
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            GmailAttachment(
                inbox_id=inbox_id,
                message_id=message_id,
                attachment_id=att["attachment_id"],
                filename=att["filename"],
                mime_type=att["mime_type"],
                size=att["size"],
                downloaded=False,
            )
        )
    await db.flush()
    return row


async def _download_gmail_attachment(
    db: AsyncSession,
    inbox_id: int,
    attachment_id: str,
) -> GmailAttachment | None:
    row = await db.execute(
        select(GmailAttachment).where(
            GmailAttachment.inbox_id == inbox_id,
            GmailAttachment.attachment_id == attachment_id,
        )
    )
    att = row.scalar_one_or_none()
    if not att:
        return None
    if att.downloaded:
        return att

    # need message id & inbox account to fetch
    if not att.message_id:
        return att
    ga = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox_id))
    ga = ga.scalar_one_or_none()
    if not ga:
        return att
    token = await _ensure_access_token(db, ga)
    if not token:
        return att

    try:
        payload = _gmail_request_json(
            token,
            f"{GMAIL_API_BASE}/messages/{urllib.parse.quote(att.message_id)}/attachments/{urllib.parse.quote(att.attachment_id)}",
        )
        data_b64 = payload.get("data") or ""
        if data_b64:
            att.data = base64.urlsafe_b64decode(data_b64 + "=" * (-len(data_b64) % 4))
            att.downloaded = True
            await db.flush()
    except GmailApiError:
        pass
    return att


async def _find_campaign_context_for_inbound(
    db: AsyncSession,
    inbox_id: int,
    thread_id: str,
    in_reply_to_header: str,
) -> tuple[EmailLog | None, Lead | None]:
    if thread_id:
        by_thread = await db.execute(
            select(EmailLog, Lead)
            .join(Lead, EmailLog.lead_id == Lead.id)
            .where(
                EmailLog.inbox_id == inbox_id,
                EmailLog.thread_id == thread_id,
            )
            .order_by(EmailLog.sent_at.desc())
        )
        row = by_thread.first()
        if row:
            return row[0], row[1]

    normalized = _normalize_message_id(in_reply_to_header)
    if normalized:
        raw_values = {in_reply_to_header.strip(), normalized, f"<{normalized}>"}
        by_mid = await db.execute(
            select(EmailLog, Lead)
            .join(Lead, EmailLog.lead_id == Lead.id)
            .where(
                EmailLog.inbox_id == inbox_id,
                or_(*[EmailLog.message_id == value for value in raw_values if value]),
            )
            .order_by(EmailLog.sent_at.desc())
        )
        row = by_mid.first()
        if row:
            return row[0], row[1]

    return None, None


async def _mark_lead_replied(db: AsyncSession, lead_id: int, campaign_id: int) -> bool:
    existing = await db.execute(
        select(LeadReply).where(
            LeadReply.lead_id == lead_id,
            LeadReply.campaign_id == campaign_id,
        )
    )
    if existing.scalar_one_or_none():
        return False
    db.add(LeadReply(lead_id=lead_id, campaign_id=campaign_id))
    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if lead and lead.status != "replied":
        lead.status = "replied"
    return True


async def sync_gmail_history_for_account(
    db: AsyncSession,
    inbox: Inbox,
    gmail_account: GmailAccount,
    hinted_history_id: str = "",
) -> dict:
    access_token = await _ensure_access_token(db, gmail_account)
    if not access_token:
        return {"ok": False, "error": "token_refresh_failed", "replies_added": 0}

    state = await _get_or_create_sync_state(db, inbox.id)
    # prefer anchor_history_id (newer) but fall back for compatibility
    stored_history_id = str(state.anchor_history_id or state.last_history_id or "").strip()
    hinted = hinted_history_id.strip()

    # if our mirror is empty (likely after upgrade) perform an initial sync
    from sqlalchemy import select, func
    from app.models import GmailThread

    cnt = await db.execute(
        select(func.count()).select_from(GmailThread).where(GmailThread.inbox_id == inbox.id)
    )
    if cnt.scalar_one() == 0:
        # perform initial import but do not treat as bootstrap response
        await _initial_sync(access_token, inbox.id, db, state)
        # ensure any previous empty cached list/detail payloads are dropped so
        # the next unibox request will hit the database (not the stale empty
        # result that may have been stored before the mirror existed).
        try:
            _invalidate_unibox_caches(inbox_id=inbox.id)
        except Exception:
            pass
        try:
            await _invalidate_persisted_caches(db, provider="gmail", inbox_id=inbox.id)
        except Exception:
            pass
        # even if stored_history_id existed, keep it unchanged (we'll update below)
    if stored_history_id and hinted:
        try:
            start_history_id = str(max(int(stored_history_id), int(hinted)))
        except Exception:
            start_history_id = stored_history_id
    else:
        start_history_id = stored_history_id or hinted

    if not start_history_id:
        # First sync: grab the last week of messages and establish an anchor.
        baseline_history_id = await _get_profile_history_id(access_token)
        if baseline_history_id:
            # perform an initial import of recent threads
            await _initial_sync(access_token, inbox.id, db, state)
            # clear caches after bootstrap as well
            try:
                _invalidate_unibox_caches(inbox_id=inbox.id)
            except Exception:
                pass
            try:
                await _invalidate_persisted_caches(db, provider="gmail", inbox_id=inbox.id)
            except Exception:
                pass
            state.anchor_history_id = baseline_history_id
            state.latest_history_id = baseline_history_id
            state.last_history_id = baseline_history_id
        state.last_sync_at = time_provider.utcnow()
        await db.flush()
        return {
            "ok": True,
            "bootstrap": True,
            "replies_added": 0,
            "history_id": baseline_history_id,
        }

    message_candidates: dict[str, dict] = {}
    thread_ids: set[str] = set()
    current_history_id = start_history_id
    next_page_token = ""
    pages = 0

    try:
        # history requests may page for a long time if the account is very active.
        # the previous implementation capped the loop at 8 pages which could leave
        # the local mirror stale in high‑volume environments.  Rather than hard
        # limit we iterate until no token (or a very high safety cap) and log if
        # we ever do hit a safety threshold.
        while True:
            pages += 1
            if pages > 50:
                log.warning(
                    "sync_gmail_history_for_account: too many history pages for inbox %s",
                    inbox.id,
                )
                break

            # Gmail requires each historyTypes value as a separate query
            # parameter; a comma-separated list will trigger a 400 error (seen
            # in production when push notifications arrive).  build the URL
            # manually to ensure we repeat the key.
            history_url = (
                f"{GMAIL_API_BASE}/history?"
                f"startHistoryId={urllib.parse.quote(start_history_id)}"
                "&historyTypes=messageAdded"
                "&historyTypes=messageDeleted"
                "&historyTypes=labelAdded"
                "&historyTypes=labelRemoved"
                "&labelId=INBOX"
                "&maxResults=500"
            )
            if next_page_token:
                history_url += "&pageToken=" + urllib.parse.quote(next_page_token)

            payload = _gmail_request_json(access_token, history_url)
            if payload.get("historyId"):
                current_history_id = str(payload["historyId"])

            for item in payload.get("history") or []:
                history_item_id = str(item.get("id") or "").strip()
                if history_item_id:
                    current_history_id = history_item_id

                # messagesAdded events are the most common when a new message
                # lands in the inbox.
                for added in item.get("messagesAdded") or []:
                    message = added.get("message") or {}
                    message_id = str(message.get("id") or "").strip()
                    if not message_id:
                        continue
                    message_candidates[message_id] = {
                        "thread_id": str(message.get("threadId") or "").strip(),
                        "label_ids": list(message.get("labelIds") or []),
                    }
                    tid = message_candidates[message_id].get("thread_id")
                    if tid:
                        thread_ids.add(tid)

                # occasionally Gmail reports that a message gains the INBOX label
                # via the labelsAdded array rather than messagesAdded.  handle that
                # case as well so we don't miss threads moved into inbox after
                # initial delivery.
                for lbl in item.get("labelsAdded") or []:
                    message = lbl.get("message") or {}
                    message_id = str(message.get("id") or "").strip()
                    if not message_id:
                        continue
                    message_candidates[message_id] = {
                        "thread_id": str(message.get("threadId") or "").strip(),
                        "label_ids": list(message.get("labelIds") or []),
                    }
                    tid = message_candidates[message_id].get("thread_id")
                    if tid:
                        thread_ids.add(tid)

                # TODO: could handle deletes/label changes here if desired

            next_page_token = str(payload.get("nextPageToken") or "").strip()
            if not next_page_token:
                break
    except GmailApiError as exc:
        # Gmail returns 404 when historyId is too old; re-baseline.
        if exc.code == 404:
            # bounded resync of recent threads instead of simple baseline
            await _bounded_resync(access_token, inbox.id, db, state)
            # after resync we treat as successful reset
            baseline_history_id = await _get_profile_history_id(access_token)
            if baseline_history_id:
                state.anchor_history_id = baseline_history_id
                state.latest_history_id = baseline_history_id
                state.last_history_id = baseline_history_id
            state.last_sync_at = time_provider.utcnow()
            await db.flush()
            return {
                "ok": True,
                "reset_history": True,
                "replies_added": 0,
                "history_id": baseline_history_id,
            }
        raise

    # persist metadata for any threads touched by history changes
    for tid in thread_ids:
        try:
            thread_payload = _gmail_request_json(
                access_token,
                f"{GMAIL_API_BASE}/threads/{urllib.parse.quote(tid)}?format=metadata",
            )
        except GmailApiError:
            continue
        await _save_thread_and_messages(db, inbox.id, thread_payload)

    replies_added = 0
    inbound_checked = 0

    for gmail_message_id, hint in message_candidates.items():
        label_ids = set(hint.get("label_ids") or [])
        if label_ids and "INBOX" not in label_ids:
            continue

        msg_url = (
            f"{GMAIL_API_BASE}/messages/{urllib.parse.quote(gmail_message_id)}"
            "?format=metadata"
            "&metadataHeaders=From"
            "&metadataHeaders=To"
            "&metadataHeaders=Cc"
            "&metadataHeaders=Subject"
            "&metadataHeaders=Message-Id"
            "&metadataHeaders=In-Reply-To"
        )
        try:
            message_payload = _gmail_request_json(access_token, msg_url)
        except GmailApiError:
            continue

        headers = ((message_payload.get("payload") or {}).get("headers") or [])
        from_email = (_extract_emails(_extract_header(headers, "From")) or [""])[0]
        if not from_email:
            continue
        if from_email == inbox.email.lower():
            # Outbound/self message; ignore.
            continue

        inbound_checked += 1
        thread_id = str(message_payload.get("threadId") or hint.get("thread_id") or "").strip()
        in_reply_to = _extract_header(headers, "In-Reply-To")
        email_log, lead = await _find_campaign_context_for_inbound(db, inbox.id, thread_id, in_reply_to)
        if not email_log or not lead:
            continue

        # Strict match so unrelated participants in the same thread don't stop campaigns.
        if (lead.email or "").lower().strip() != from_email:
            continue

        if await _mark_lead_replied(db, lead.id, email_log.campaign_id):
            replies_added += 1
            log.info(
                "Reply detected: inbox=%s lead=%s campaign=%s thread=%s from=%s",
                inbox.email,
                lead.email,
                email_log.campaign_id,
                thread_id,
                from_email,
            )

    if current_history_id:
        state.last_history_id = current_history_id
        # advance both anchor and latest; anchor is our checkpoint for next call
        state.anchor_history_id = current_history_id
        state.latest_history_id = current_history_id
    state.last_sync_at = time_provider.utcnow()
    await db.flush()

    # whenever we have new history candidates we should clear any unibox
    # caches so the frontend list endpoint will re-query the database and pick
    # up the new rows.  The previous implementation also pre‑fetched metadata
    # from Gmail; that's no longer required because the mirror is authoritative.
    if message_candidates:
        try:
            _invalidate_unibox_caches(inbox_id=inbox.id)
        except Exception:
            pass
        try:
            await _invalidate_persisted_caches(db, provider="gmail", inbox_id=inbox.id)
        except Exception:
            pass

    if replies_added:
        from app.routers.calendar import recalculate_all_campaigns

        await recalculate_all_campaigns(db)

    return {
        "ok": True,
        "replies_added": replies_added,
        "messages_checked": inbound_checked,
        "history_id": current_history_id,
    }


async def sync_gmail_inbox_by_email(db: AsyncSession, email: str, history_id: str = "") -> dict:
    normalized = (email or "").strip().lower()
    if not normalized:
        return {"ok": False, "error": "missing_email", "replies_added": 0}

    result = await db.execute(
        select(GmailAccount, Inbox)
        .join(Inbox, GmailAccount.inbox_id == Inbox.id)
        .where(
            or_(
                Inbox.email == normalized,
                GmailAccount.google_email == normalized,
            )
        )
    )
    row = result.first()
    if not row:
        return {"ok": False, "error": "inbox_not_found", "replies_added": 0}
    gmail_account, inbox = row
    return await sync_gmail_history_for_account(db, inbox, gmail_account, hinted_history_id=history_id)


async def sync_all_gmail_inboxes(db: AsyncSession) -> dict:
    result = await db.execute(
        select(GmailAccount, Inbox)
        .join(Inbox, GmailAccount.inbox_id == Inbox.id)
        .order_by(Inbox.id)
    )
    rows = result.all()
    processed = 0
    total_replies = 0
    errors: list[str] = []
    for gmail_account, inbox in rows:
        try:
            sync_result = await sync_gmail_history_for_account(db, inbox, gmail_account)
            processed += 1
            total_replies += int(sync_result.get("replies_added") or 0)
        except Exception as exc:
            errors.append(f"{inbox.email}: {exc}")
    return {
        "ok": not errors,
        "processed": processed,
        "replies_added": total_replies,
        "errors": errors,
    }


async def renew_gmail_watch_for_all(db: AsyncSession) -> dict:
    config = await get_gmail_sync_config(db)
    topic_name = str(config.get("push_topic") or "").strip()
    if not topic_name:
        return {"ok": False, "reason": "push_topic_not_configured", "renewed": 0}

    result = await db.execute(
        select(GmailAccount, Inbox)
        .join(Inbox, GmailAccount.inbox_id == Inbox.id)
        .order_by(Inbox.id)
    )
    rows = result.all()
    renewed = 0
    errors: list[str] = []

    for gmail_account, inbox in rows:
        token = await _ensure_access_token(db, gmail_account)
        if not token:
            errors.append(f"{inbox.email}: token refresh failed")
            continue
        try:
            payload = _gmail_request_json(
                token,
                f"{GMAIL_API_BASE}/watch",
                method="POST",
                payload={
                    "topicName": topic_name,
                    "labelIds": ["INBOX"],
                    "labelFilterBehavior": "INCLUDE",
                },
            )
            state = await _get_or_create_sync_state(db, inbox.id)
            history_id = str(payload.get("historyId") or "").strip()
            if history_id:
                state.last_history_id = history_id
            expiration = _as_utc_datetime_from_ms(payload.get("expiration"))
            if expiration:
                state.watch_expiration = expiration
            state.last_sync_at = time_provider.utcnow()
            renewed += 1
        except Exception as exc:
            errors.append(f"{inbox.email}: {exc}")

    await db.flush()
    return {"ok": not errors, "renewed": renewed, "errors": errors}


async def run_gmail_reply_sync_job() -> None:
    async with AsyncSessionLocal() as session:
        try:
            result = await sync_all_gmail_inboxes(session)
            await session.commit()
            log.info(
                "gmail_reply_sync_job: processed=%s replies_added=%s errors=%s",
                result.get("processed"),
                result.get("replies_added"),
                len(result.get("errors") or []),
            )
        except Exception as exc:
            await session.rollback()
            log.error("gmail_reply_sync_job failed: %s", exc)


async def run_gmail_watch_renew_job() -> None:
    async with AsyncSessionLocal() as session:
        try:
            result = await renew_gmail_watch_for_all(session)
            await session.commit()
            log.info(
                "gmail_watch_renew_job: renewed=%s errors=%s",
                result.get("renewed"),
                len(result.get("errors") or []),
            )
        except Exception as exc:
            await session.rollback()
            log.error("gmail_watch_renew_job failed: %s", exc)
