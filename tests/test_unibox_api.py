import json
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import GmailAccount, GmailMessage, GmailThread, Inbox
from app.routers import unibox as unibox_router
from app.routers.unibox import UniboxLoadMoreRequest, UniboxSendRequest
from app.sender import SendResult


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

