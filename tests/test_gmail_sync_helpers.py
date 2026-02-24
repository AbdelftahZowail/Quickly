import base64
import json
import pytest
from sqlalchemy import select

from app.gmail_sync import parse_gmail_push_payload


def test_parse_gmail_push_payload_from_pubsub_shape():
    inner = {"emailAddress": "Inbox@Example.com", "historyId": "123456"}
    encoded = base64.urlsafe_b64encode(json.dumps(inner).encode("utf-8")).decode("ascii")
    payload = {"message": {"data": encoded}}
    email, history_id = parse_gmail_push_payload(payload)
    assert email == "inbox@example.com"
    assert history_id == "123456"


def test_parse_gmail_push_payload_from_direct_shape():
    payload = {"emailAddress": "leadbox@example.com", "historyId": "77"}
    email, history_id = parse_gmail_push_payload(payload)
    assert email == "leadbox@example.com"
    assert history_id == "77"


def test_parse_gmail_push_payload_invalid():
    email, history_id = parse_gmail_push_payload({"message": {"data": "not-base64"}})
    assert email == ""
    assert history_id == ""


# when the sync job observes an incoming message it should clear any cached
# unibox list/detail payloads and try to pre‑populate the thread metadata so
# a subsequent `/api/unibox/conversations` request will surface the new
# conversation without needing to refetch headers.
async def test_sync_history_invalidates_and_prefetches(
    session, monkeypatch
):
    from app.models import GmailAccount, Inbox
    from app.gmail_sync import sync_gmail_history_for_account
    from app.routers import unibox

    # prepare inbox & account
    inbox = await session.execute(
        select(Inbox).where(Inbox.email == "test@inbox.com")
    )
    if not inbox.scalars().first():
        inbox = Inbox(email="test@inbox.com", provider="gmail")
        session.add(inbox)
        await session.flush()
    else:
        inbox = inbox.scalars().first()
    gmail_account = GmailAccount(
        inbox_id=inbox.id,
        google_email="test@inbox.com",
        access_token="tok",
        refresh_token="ref",
    )
    session.add(gmail_account)
    await session.flush()

    # capture calls
    invalidated = False
    prefetched: list[str] = []

    def fake_invalidate(inbox_id=None, thread_id=None):
        nonlocal invalidated
        invalidated = True

    async def fake_invalidate_persisted(db, provider=None, inbox_id=None, thread_id=None):
        # no-op
        return

    async def fake_get_meta(token, inbox_id, thread_id, force_refresh=False, db=None):
        prefetched.append(thread_id)
        return {"id": thread_id}

    monkeypatch.setattr(unibox, "_invalidate_unibox_caches", fake_invalidate)
    monkeypatch.setattr(unibox, "_invalidate_persisted_caches", fake_invalidate_persisted)
    monkeypatch.setattr(unibox, "_get_gmail_thread_metadata", fake_get_meta)
    # the gmail_sync module imported these helpers directly, so patch there too
    import app.gmail_sync as gs
    monkeypatch.setattr(gs, "_invalidate_unibox_caches", fake_invalidate)
    monkeypatch.setattr(gs, "_invalidate_persisted_caches", fake_invalidate_persisted)
    monkeypatch.setattr(gs, "_get_gmail_thread_metadata", fake_get_meta)

    # stub gmail API responses: history then thread metadata and message metadata
    def fake_request(token, url, method="GET", payload=None):
        if "history?" in url:
            return {
                "historyId": "10",
                "history": [
                    {
                        "id": "1",
                        "messagesAdded": [
                            {"message": {"id": "msg1", "threadId": "thread123", "labelIds": ["INBOX"]}}
                        ],
                    }
                ],
            }
        if "/threads/" in url:
            # return minimal metadata payload for saving
            return {
                "id": "thread123",
                "historyId": "5",
                "snippet": "snip",
                "messages": [
                    {
                        "id": "msg1",
                        "threadId": "thread123",
                        "internalDate": "1600000000000",
                        "snippet": "hello",
                        "payload": {"headers": []},
                        "labelIds": ["INBOX"],
                    }
                ],
            }
        if "/messages/" in url:
            # only need minimal headers for the reply detection path
            return {"payload": {"headers": [{"name": "From", "value": "external@example.com"}]}}
        # should not be reached in this simple test
        return {}

    monkeypatch.setattr("app.gmail_sync._gmail_request_json", fake_request)

    result = await sync_gmail_history_for_account(session, inbox, gmail_account, hinted_history_id="1")
    assert result.get("ok")
    assert invalidated, "unibox caches were not invalidated"
    # previously we prefetched metadata, but the new mirror avoids extra calls

    # verify the thread/message were stored locally
    from app.models import GmailThread, GmailMessage

    thr = await session.get(GmailThread, (inbox.id, "thread123"))
    assert thr is not None, "thread row not created"
    msgrow = await session.get(GmailMessage, (inbox.id, "msg1"))
    assert msgrow is not None, "message row not created"
    assert msgrow.thread_id == "thread123"


@pytest.mark.asyncio
async def test_sync_with_history_but_empty_mirror(session, monkeypatch):
    """If the sync state exists but the local tables are empty we still pull data."""
    from app.models import GmailAccount, Inbox, GmailThread, GmailMessage, GmailSyncState
    from app.gmail_sync import sync_gmail_history_for_account
    from sqlalchemy import delete

    inbox = await session.execute(
        select(Inbox).where(Inbox.email == "empty@inbox.com")
    )
    if not inbox.scalars().first():
        inbox = Inbox(email="empty@inbox.com", provider="gmail")
        session.add(inbox)
        await session.flush()
    else:
        inbox = inbox.scalars().first()
    gmail_account = GmailAccount(
        inbox_id=inbox.id,
        google_email="empty@inbox.com",
        access_token="tok",
        refresh_token="ref",
    )
    session.add(gmail_account)
    # create stale sync state
    state = GmailSyncState(inbox_id=inbox.id, last_history_id="999")
    session.add(state)
    await session.flush()

    # ensure mirror tables empty
    await session.execute(delete(GmailMessage).where(GmailMessage.inbox_id == inbox.id))
    await session.execute(delete(GmailThread).where(GmailThread.inbox_id == inbox.id))
    await session.flush()

    # stub profile to avoid early exit
    async def fake_profile(token):
        return "999"
    monkeypatch.setattr("app.gmail_sync._get_profile_history_id", fake_profile)

    # stub history call to return one added message
    def fake_request(token, url, method="GET", payload=None):
        if "history?" in url:
            return {
                "historyId": "1000",
                "history": [
                    {"id": "1000", "messagesAdded": [{"message": {"id": "xm1", "threadId": "xthr", "labelIds": ["INBOX"]}}]}            ],
            }
        if "/threads/" in url:
            return {"id":"xthr","historyId":"1001","snippet":"s","messages":[{"id":"xm1","threadId":"xthr","internalDate":"1600000000000","snippet":"","payload":{"headers":[]},"labelIds":["INBOX"]}]}
        if "/messages/" in url:
            return {"payload": {"headers": [{"name": "From", "value": "a@a.com"}]}}
        return {}
    monkeypatch.setattr("app.gmail_sync._gmail_request_json", fake_request)

    res = await sync_gmail_history_for_account(session, inbox, gmail_account)
    assert res.get("ok")
    thr = await session.get(GmailThread, (inbox.id, "xthr"))
    assert thr is not None
    msg = await session.get(GmailMessage, (inbox.id, "xm1"))
    assert msg is not None


@pytest.mark.asyncio
async def test_cache_cleared_on_initial_sync(session, monkeypatch):
    """An existing stale cache should be removed when initial sync runs."""
    from app.models import Inbox, GmailAccount, UniboxCache
    from app.gmail_sync import sync_gmail_history_for_account

    inbox = await session.execute(select(Inbox).where(Inbox.email == "cache@inbox.com"))
    if not inbox.scalars().first():
        inbox = Inbox(email="cache@inbox.com", provider="gmail")
        session.add(inbox)
        await session.flush()
    else:
        inbox = inbox.scalars().first()
    gmail_account = GmailAccount(
        inbox_id=inbox.id,
        google_email="cache@inbox.com",
        access_token="tok",
        refresh_token="ref",
    )
    session.add(gmail_account)
    await session.flush()

    # insert stale cache entry
    key = "list:" + json.dumps({"inbox_id": None, "server_only": False}, separators=(',', ':'), sort_keys=True)
    session.add(UniboxCache(cache_key=key, payload={"items": [], "cache_status": "miss"}))
    await session.flush()

    # stub profile and initial sync responses
    async def fake_profile(token):
        return "2000"
    monkeypatch.setattr("app.gmail_sync._get_profile_history_id", fake_profile)

    def fake_request(token, url, method="GET", payload=None):
        if "threads?" in url:
            return {"threads": [{"id": "cth"}]}
        if "/threads/" in url:
            return {"id": "cth", "historyId": "2001", "snippet": "s", "messages": []}
        return {}
    monkeypatch.setattr("app.gmail_sync._gmail_request_json", fake_request)

    await sync_gmail_history_for_account(session, inbox, gmail_account)
    # stale cache should be gone
    stale = await session.get(UniboxCache, key)
    assert stale is None


@pytest.mark.asyncio
async def test_initial_sync_creates_threads(session, monkeypatch):
    from app.models import GmailAccount, Inbox, GmailThread, GmailMessage, GmailSyncState
    from app.gmail_sync import sync_gmail_history_for_account

    # new inbox/account without any history state
    inbox = await session.execute(
        select(Inbox).where(Inbox.email == "init@inbox.com")
    )
    if not inbox.scalars().first():
        inbox = Inbox(email="init@inbox.com", provider="gmail")
        session.add(inbox)
        await session.flush()
    else:
        inbox = inbox.scalars().first()
    gmail_account = GmailAccount(
        inbox_id=inbox.id,
        google_email="init@inbox.com",
        access_token="tok",
        refresh_token="ref",
    )
    session.add(gmail_account)
    await session.flush()

    # intercept profile call as async helper
    async def fake_profile(token):
        return "555"
    monkeypatch.setattr("app.gmail_sync._get_profile_history_id", fake_profile)

    # stub Gmail responses for threads.list and threads.get
    def fake_request(token, url, method="GET", payload=None):
        if "threads?" in url:
            return {"threads": [{"id": "thA"}]}
        if "/threads/" in url:
            return {
                "id": "thA",
                "historyId": "123",
                "snippet": "foo",
                "messages": [
                    {
                        "id": "mA",
                        "threadId": "thA",
                        "internalDate": "1600000000000",
                        "snippet": "bar",
                        "payload": {"headers": []},
                        "labelIds": ["INBOX"],
                    }
                ],
            }
        return {}

    monkeypatch.setattr("app.gmail_sync._gmail_request_json", fake_request)

    result = await sync_gmail_history_for_account(session, inbox, gmail_account)
    assert result.get("bootstrap")
    # check state updated
    state = await session.execute(select(GmailSyncState).where(GmailSyncState.inbox_id == inbox.id))
    state = state.scalar_one()
    assert state.anchor_history_id == "555"
    assert state.latest_history_id == "555"

    thr = await session.get(GmailThread, (inbox.id, "thA"))
    assert thr is not None
    msgrow = await session.get(GmailMessage, (inbox.id, "mA"))
    assert msgrow is not None
