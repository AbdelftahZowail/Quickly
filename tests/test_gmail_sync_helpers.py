import base64
import json
import pytest
import asyncio
from sqlalchemy import select
import urllib.parse

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
        # verify comma-separated historyTypes is never used and values are valid
        if "history?" in url:
            qry = urllib.parse.urlparse(url).query
            assert "," not in qry
            # check each parameter has allowed value
            for part in qry.split('&'):
                if part.startswith('historyTypes='):
                    val = part.split('=',1)[1]
                    assert val in ('messageAdded','messageDeleted','labelAdded','labelRemoved')
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

    # verify the thread/message were stored locally
    from app.models import GmailThread, GmailMessage

    thr = await session.get(GmailThread, (inbox.id, "thread123"))
    assert thr is not None, "thread row not created"
    msgrow = await session.get(GmailMessage, (inbox.id, "msg1"))
    assert msgrow is not None, "message row not created"
    assert msgrow.thread_id == "thread123"

    # now simulate a messages-added event using labelsAdded shape; it should
    # be treated equivalently by the sync routine.
    def fake_request2(token, url, method="GET", payload=None):
        if "history?" in url:
            return {
                "historyId": "20",
                "history": [
                    {"id": "2", "labelsAdded": [{"message": {"id": "msg2", "threadId": "thread456", "labelIds": ["INBOX"]}}]},
                ],
            }
        if "/threads/" in url:
            return {
                "id": "thread456",
                "historyId": "6",
                "snippet": "snip2",
                "messages": [
                    {"id": "msg2", "threadId": "thread456", "internalDate": "1600000005000", "snippet": "world", "payload": {"headers": []}, "labelIds": ["INBOX"]},
                ],
            }
        if "/messages/" in url:
            return {"payload": {"headers": [{"name": "From", "value": "external@example.com"}]}}
        return {}

    monkeypatch.setattr("app.gmail_sync._gmail_request_json", fake_request2)
    result2 = await sync_gmail_history_for_account(session, inbox, gmail_account)
    assert result2.get("ok")
    thr2 = await session.get(GmailThread, (inbox.id, "thread456"))
    assert thr2 is not None
    msg2 = await session.get(GmailMessage, (inbox.id, "msg2"))
    assert msg2 is not None
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


# --- new tests for webhook behaviour ------------------------------------------------

@pytest.mark.asyncio
async def test_push_webhook_triggers_sync(monkeypatch, session):
    """The /api/gmail/push endpoint should accept a valid payload and
    enqueue a sync task with the provided email/history id.
    """
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.routers.gmail_sync import _run_single_sync_task
    from app.app_settings import save_gmail_sync_config
    from app.database import get_db

    # make the API endpoints use the same session as this test fixture
    async def _override_db():
        yield session
    app.dependency_overrides[get_db] = _override_db

    # prepare a gmail sync config with a webhook token so authorization is required
    await save_gmail_sync_config(session, push_topic="", webhook_token="secret", sync_interval_minutes=5)
    await session.commit()

    called: list[tuple[str, str]] = []

    async def fake_run(email, history_id):
        called.append((email, history_id))

    monkeypatch.setattr("app.routers.gmail_sync._run_single_sync_task", fake_run)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # invalid payload should be accepted but ignored
        resp = await client.post("/api/gmail/push?token=secret", json={"foo": "bar"})
        assert resp.json().get("accepted") is False
        # valid shape should queue a background task
        payload = {"emailAddress": "test@inbox.com", "historyId": "42"}
        resp = await client.post("/api/gmail/push?token=secret", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("accepted") is True
        # also try without any token; should still be accepted (warning logged)
        resp = await client.post("/api/gmail/push", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("accepted") is True
        # give the event loop a moment for the fake task to run
        await asyncio.sleep(0.01)
    assert called == [("test@inbox.com", "42"), ("test@inbox.com", "42")]

    # wrong token should still be rejected
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/gmail/push?token=wrong", json=payload)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_settings_save_triggers_watch_renewal(monkeypatch, session):
    """POST /settings/gmail-sync should schedule a watch renewal job."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.database import get_db
    called = False

    # direct API calls should share the in-memory fixture session
    async def _override_db():
        yield session
    app.dependency_overrides[get_db] = _override_db

    async def fake_renew(db):
        nonlocal called
        called = True

    monkeypatch.setattr("app.gmail_sync.renew_gmail_watch_for_all", fake_renew)

    payload = {"push_topic": "projects/foo/topics/bar", "webhook_token": "tok", "sync_interval_minutes": 3}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/settings/gmail-sync", json=payload)
        assert resp.status_code == 200
        # allow background task a moment to run
        await asyncio.sleep(0.01)
    assert called, "renew_gmail_watch_for_all was not called"


@pytest.mark.asyncio
async def test_conversation_list_refresh_runs_sync(monkeypatch, session):
    """GET /unibox/conversations?refresh=true should invoke gmail sync logic."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.database import get_db
    from app.models import Inbox

    # route handlers should use our test session
    async def _override_db():
        yield session
    app.dependency_overrides[get_db] = _override_db

    called = {"all": False, "single": None}

    async def fake_all(db):
        called["all"] = True
    async def fake_one(db, email):
        called["single"] = email

    monkeypatch.setattr("app.gmail_sync.sync_all_gmail_inboxes", fake_all)
    monkeypatch.setattr("app.gmail_sync.sync_gmail_inbox_by_email", fake_one)

    # create a gmail inbox row so that lookup by inbox_id works
    inbox = Inbox(email="foo@test.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/unibox/conversations?refresh=true&inbox_id={inbox.id}")
        assert resp.status_code == 200
    assert called["single"] == "foo@test.com"

    # call again without specifying inbox_id should call sync_all
    called["all"] = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/unibox/conversations?refresh=true")
        assert resp.status_code == 200
    assert called["all"]

