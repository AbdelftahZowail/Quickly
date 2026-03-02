import base64
import json
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import GmailAccount, GmailMessage, GmailSyncState, GmailThread, Inbox
from app.routers import unibox as unibox_router
from app.routers.unibox import UniboxLoadMoreRequest, UniboxSendRequest
from app.sender import SendResult
from app.unibox import _upsert_message_from_gmail, get_thread_messages, upsert_sent_message


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.mark.asyncio
async def test_get_unibox_merges_and_sorts(session):
    inbox_a = Inbox(email="a@example.com", provider="gmail")
    inbox_b = Inbox(email="b@example.com", provider="gmail")
    session.add_all([inbox_a, inbox_b])
    await session.flush()

    t1_time = _ms(datetime(2026, 2, 1, 10, 0, 0))
    t2_time = _ms(datetime(2026, 2, 2, 10, 0, 0))

    session.add_all(
        [
            GmailThread(inbox_id=inbox_a.id, thread_id="t-a", snippet="older", last_internal_date=t1_time),
            GmailThread(inbox_id=inbox_b.id, thread_id="t-b", snippet="newer", last_internal_date=t2_time),
            GmailMessage(
                inbox_id=inbox_a.id,
                message_id="m-a",
                thread_id="t-a",
                internal_date=t1_time,
                snippet="older",
                headers_json=json.dumps([{"name": "Subject", "value": "Subject A"}]),
                label_ids_json=json.dumps(["INBOX"]),
            ),
            GmailMessage(
                inbox_id=inbox_b.id,
                message_id="m-b",
                thread_id="t-b",
                internal_date=t2_time,
                snippet="newer",
                headers_json=json.dumps([{"name": "Subject", "value": "Subject B"}]),
                label_ids_json=json.dumps(["INBOX"]),
            ),
        ]
    )
    await session.flush()

    payload = await unibox_router.get_unibox(page=1, page_size=20, db=session)
    items = payload["items"]
    assert payload["total"] == 2
    assert items[0]["thread_id"] == "t-b"
    assert items[0]["gmail_account"] == "b@example.com"
    assert items[1]["thread_id"] == "t-a"


@pytest.mark.asyncio
async def test_get_unibox_thread_messages_are_chronological(session):
    inbox = Inbox(email="owner@example.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    older = _ms(datetime(2026, 2, 1, 9, 0, 0))
    newer = _ms(datetime(2026, 2, 1, 9, 5, 0))
    session.add(GmailThread(inbox_id=inbox.id, thread_id="thr-1", snippet="latest", last_internal_date=newer))
    session.add_all(
        [
            GmailMessage(
                inbox_id=inbox.id,
                message_id="m-2",
                thread_id="thr-1",
                internal_date=newer,
                snippet="sent",
                headers_json=json.dumps([{"name": "Subject", "value": "Re: Hi"}]),
                label_ids_json=json.dumps(["SENT"]),
            ),
            GmailMessage(
                inbox_id=inbox.id,
                message_id="m-1",
                thread_id="thr-1",
                internal_date=older,
                snippet="incoming",
                headers_json=json.dumps([{"name": "Subject", "value": "Hi"}]),
                label_ids_json=json.dumps(["INBOX"]),
            ),
        ]
    )
    await session.flush()

    payload = await unibox_router.get_unibox_thread("thr-1", inbox_id=inbox.id, db=session)
    messages = payload["messages"]
    assert [m["message_id"] for m in messages] == ["m-1", "m-2"]
    assert messages[0]["direction"] == "received"
    assert messages[1]["direction"] == "sent"


@pytest.mark.asyncio
async def test_unibox_send_inserts_sent_message_after_confirmed_send(session, monkeypatch):
    inbox = Inbox(email="sender@example.com", display_name="Sender", provider="gmail")
    session.add(inbox)
    await session.flush()

    session.add(
        GmailAccount(
            inbox_id=inbox.id,
            google_email="sender@example.com",
            access_token="token",
            refresh_token="refresh",
        )
    )
    await session.flush()

    def fake_send_email(**kwargs):
        return SendResult(message_id="<rfc-1@example.com>", thread_id="t-send", gmail_message_id="gm-send-1")

    monkeypatch.setattr("app.routers.unibox.send_email", fake_send_email)

    req = UniboxSendRequest(
        inbox_id=inbox.id,
        to_email="lead@example.com",
        subject="Hello",
        body="Thanks for your time",
        is_html=False,
    )

    res = await unibox_router.send_unibox_email(req, db=session)
    assert res["ok"] is True
    assert res["thread_id"] == "t-send"

    msg_row = await session.execute(
        select(GmailMessage).where(GmailMessage.inbox_id == inbox.id, GmailMessage.message_id == "gm-send-1")
    )
    msg = msg_row.scalar_one_or_none()
    assert msg is not None
    assert "SENT" in json.loads(msg.label_ids_json)

    thread_row = await session.execute(
        select(GmailThread).where(GmailThread.inbox_id == inbox.id, GmailThread.thread_id == "t-send")
    )
    assert thread_row.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_unibox_send_does_not_insert_on_gmail_failure(session, monkeypatch):
    inbox = Inbox(email="sender2@example.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    session.add(
        GmailAccount(
            inbox_id=inbox.id,
            google_email="sender2@example.com",
            access_token="token",
            refresh_token="refresh",
        )
    )
    await session.flush()

    monkeypatch.setattr("app.routers.unibox.send_email", lambda **kwargs: None)

    req = UniboxSendRequest(
        inbox_id=inbox.id,
        to_email="lead2@example.com",
        subject="Hello",
        body="Body",
    )

    with pytest.raises(HTTPException) as exc:
        await unibox_router.send_unibox_email(req, db=session)
    assert exc.value.status_code == 502

    row = await session.execute(select(GmailMessage).where(GmailMessage.inbox_id == inbox.id))
    assert row.scalars().first() is None


@pytest.mark.asyncio
async def test_unibox_load_more_empty_when_no_gmail_inboxes(session):
    res = await unibox_router.trigger_unibox_load_more(
        UniboxLoadMoreRequest(),
        db=session,
    )
    assert res["ok"] is True
    assert res["queued"] == 0
    assert res["inbox_ids"] == []


@pytest.mark.asyncio
async def test_unibox_load_more_queues_selected_inbox(session, monkeypatch):
    inbox = Inbox(email="older@example.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    called: dict[str, int | str] = {}

    async def fake_queue_backfill_for_inbox(inbox_id: int, *, window_days: int, reason: str):
        called["inbox_id"] = inbox_id
        called["window_days"] = window_days
        called["reason"] = reason

    monkeypatch.setattr(
        "app.routers.unibox.queue_backfill_for_inbox",
        fake_queue_backfill_for_inbox,
    )

    res = await unibox_router.trigger_unibox_load_more(
        UniboxLoadMoreRequest(inbox_id=inbox.id, window_days=7),
        db=session,
    )
    assert res["ok"] is True
    assert res["queued"] == 1
    assert res["inbox_ids"] == [inbox.id]
    assert called["inbox_id"] == inbox.id
    assert called["window_days"] == 7
    assert called["reason"] == "manual-backfill"


@pytest.mark.asyncio
async def test_gmail_push_does_not_advance_history_checkpoint(session, monkeypatch):
    inbox = Inbox(email="push-owner@example.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    session.add(
        GmailAccount(
            inbox_id=inbox.id,
            google_email="push-owner@example.com",
            access_token="token",
            refresh_token="refresh",
        )
    )
    session.add(
        GmailSyncState(
            inbox_id=inbox.id,
            anchor_history_id="100",
            latest_history_id="100",
            last_history_id="100",
        )
    )
    await session.flush()

    called: dict[str, int | str] = {}

    async def fake_queue_sync_for_inbox(inbox_id: int, reason: str = "push"):
        called["inbox_id"] = inbox_id
        called["reason"] = reason

    monkeypatch.setattr("app.routers.unibox.queue_sync_for_inbox", fake_queue_sync_for_inbox)

    payload = {"emailAddress": "push-owner@example.com", "historyId": "200"}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")

    class DummyRequest:
        async def json(self):
            return {"message": {"data": raw}}

    res = await unibox_router.gmail_push_webhook(request=DummyRequest(), db=session)
    assert res["ok"] is True
    assert called["inbox_id"] == inbox.id
    assert called["reason"] == "push"

    state_row = await session.execute(select(GmailSyncState).where(GmailSyncState.inbox_id == inbox.id))
    state = state_row.scalar_one()
    assert state.latest_history_id == "100"
    assert state.last_history_id == "100"


@pytest.mark.asyncio
async def test_upsert_sent_message_stays_at_thread_bottom_when_local_time_is_behind(session, monkeypatch):
    inbox = Inbox(email="order@example.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    session.add(
        GmailThread(
            inbox_id=inbox.id,
            thread_id="thr-order",
            snippet="older",
            last_internal_date=5000,
        )
    )
    session.add(
        GmailMessage(
            inbox_id=inbox.id,
            message_id="m-old",
            thread_id="thr-order",
            internal_date=5000,
            snippet="older",
            headers_json=json.dumps([{"name": "Subject", "value": "Hi"}]),
            label_ids_json=json.dumps(["INBOX"]),
        )
    )
    await session.flush()

    # Simulate local optimistic timestamp being behind the latest thread message.
    monkeypatch.setattr("app.unibox._now_epoch_ms", lambda: 4000)

    sent = await upsert_sent_message(
        session,
        inbox_id=inbox.id,
        thread_id="thr-order",
        gmail_message_id="m-sent",
        rfc_message_id="<sent@example.com>",
        subject="Re: Hi",
        to_email="lead@example.com",
        from_email="order@example.com",
        body="Thanks",
        is_html=False,
    )
    assert sent.internal_date == 5001

    payload = await get_thread_messages(session, thread_id="thr-order", inbox_id=inbox.id)
    ids = [m["message_id"] for m in payload["messages"]]
    assert ids[-1] == "m-sent"


@pytest.mark.asyncio
async def test_metadata_upsert_does_not_clobber_existing_downloaded_body(session):
    inbox = Inbox(email="keep-body@example.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    msg_time = _ms(datetime(2026, 2, 1, 12, 0, 0))
    session.add(GmailThread(inbox_id=inbox.id, thread_id="thr-keep", snippet="old", last_internal_date=msg_time))
    session.add(
        GmailMessage(
            inbox_id=inbox.id,
            message_id="m-keep",
            thread_id="thr-keep",
            internal_date=msg_time,
            snippet="old snippet",
            headers_json=json.dumps([{"name": "Subject", "value": "Keep"}]),
            label_ids_json=json.dumps(["INBOX"]),
            body_fetched=True,
            body_plain="already downloaded",
            body_html="",
        )
    )
    await session.flush()

    metadata_payload = {
        "id": "m-keep",
        "threadId": "thr-keep",
        "internalDate": str(msg_time),
        "snippet": "new metadata snippet",
        "historyId": "h-1",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Keep"},
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "keep-body@example.com"},
            ]
        },
    }
    await _upsert_message_from_gmail(
        session,
        inbox_id=inbox.id,
        gmail_message=metadata_payload,
        include_body=False,
    )

    row = await session.execute(
        select(GmailMessage).where(GmailMessage.inbox_id == inbox.id, GmailMessage.message_id == "m-keep")
    )
    msg = row.scalar_one()
    assert msg.body_fetched is True
    assert msg.body_plain == "already downloaded"


@pytest.mark.asyncio
async def test_get_unibox_thread_hydrates_on_demand_when_body_missing(session, monkeypatch):
    inbox = Inbox(email="ondemand@example.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    session.add(
        GmailAccount(
            inbox_id=inbox.id,
            google_email="ondemand@example.com",
            access_token="token",
            refresh_token="refresh",
        )
    )

    msg_time = _ms(datetime(2026, 2, 3, 9, 0, 0))
    session.add(
        GmailThread(
            inbox_id=inbox.id,
            thread_id="thr-on-demand",
            snippet="preview",
            last_internal_date=msg_time,
        )
    )
    session.add(
        GmailMessage(
            inbox_id=inbox.id,
            message_id="m-on-demand",
            thread_id="thr-on-demand",
            internal_date=msg_time,
            snippet="preview",
            headers_json=json.dumps(
                [
                    {"name": "Subject", "value": "Need body"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "To", "value": "ondemand@example.com"},
                ]
            ),
            label_ids_json=json.dumps(["INBOX"]),
            body_fetched=False,
            body_plain="",
            body_html="",
        )
    )
    await session.flush()

    encoded_body = base64.urlsafe_b64encode(b"Hydrated body content").decode("ascii").rstrip("=")

    def fake_get_thread(access_token: str, thread_id: str, *, payload_format: str = "full"):
        assert access_token == "token"
        assert thread_id == "thr-on-demand"
        assert payload_format == "full"
        return {
            "id": "thr-on-demand",
            "messages": [
                {
                    "id": "m-on-demand",
                    "threadId": "thr-on-demand",
                    "internalDate": str(msg_time),
                    "snippet": "preview",
                    "historyId": "h2",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "Subject", "value": "Need body"},
                            {"name": "From", "value": "sender@example.com"},
                            {"name": "To", "value": "ondemand@example.com"},
                        ],
                        "body": {"data": encoded_body},
                    },
                }
            ],
        }

    monkeypatch.setattr("app.unibox._gmail_get_thread", fake_get_thread)

    payload = await unibox_router.get_unibox_thread("thr-on-demand", inbox_id=inbox.id, db=session)
    assert payload["messages"][0]["body_plain"] == "Hydrated body content"

    row = await session.execute(
        select(GmailMessage).where(
            GmailMessage.inbox_id == inbox.id,
            GmailMessage.message_id == "m-on-demand",
        )
    )
    msg = row.scalar_one()
    assert msg.body_fetched is True
    assert msg.body_plain == "Hydrated body content"


@pytest.mark.asyncio
async def test_hydrate_thread_not_found_skips(session, monkeypatch):
    # ensure that a 404 from _gmail_get_thread does not raise at the HTTP level
    inbox = Inbox(email="missingthread@example.com", provider="gmail")
    session.add(inbox)
    await session.flush()

    session.add(
        GmailAccount(
            inbox_id=inbox.id,
            google_email="missingthread@example.com",
            access_token="token",
            refresh_token="refresh",
        )
    )

    msg_time = _ms(datetime(2026, 2, 3, 9, 0, 0))
    session.add(
        GmailThread(
            inbox_id=inbox.id,
            thread_id="thr-missing",
            snippet="preview",
            last_internal_date=msg_time,
        )
    )
    session.add(
        GmailMessage(
            inbox_id=inbox.id,
            message_id="m-missing",
            thread_id="thr-missing",
            internal_date=msg_time,
            snippet="preview",
            headers_json=json.dumps(
                [
                    {"name": "Subject", "value": "Need body"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "To", "value": "missingthread@example.com"},
                ]
            ),
            label_ids_json=json.dumps(["INBOX"]),
            body_fetched=False,
            body_plain="",
            body_html="",
        )
    )
    await session.flush()

    # import inside the test so the module name is defined
    import app.unibox

    def fake_get_thread(access_token: str, thread_id: str, *, payload_format: str = "full"):
        raise app.unibox.GmailAPIError(404, "thread not found")

    monkeypatch.setattr("app.unibox._gmail_get_thread", fake_get_thread)

    # the router should still return an empty body rather than raising
    payload = await unibox_router.get_unibox_thread("thr-missing", inbox_id=inbox.id, db=session)
    assert payload["messages"][0]["body_plain"] == ""

    msg_row = await session.execute(
        select(GmailMessage).where(
            GmailMessage.inbox_id == inbox.id,
            GmailMessage.message_id == "m-missing",
        )
    )
    msg = msg_row.scalar_one()
    assert msg.body_fetched is False

