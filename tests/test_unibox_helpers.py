import base64
import json

from app.routers.unibox import (
    _decode_cursor,
    _decode_gmail_base64,
    _derive_reply_subject,
    _encode_cursor,
    _extract_emails,
    _extract_payload_bodies,
    _html_to_text,
    _normalize_message_id,
)


def test_extract_emails_parses_multiple_addresses():
    value = 'Alice <alice@example.com>, "Bob Team" <bob@example.com>'
    emails = _extract_emails(value)
    assert emails == ["alice@example.com", "bob@example.com"]


def test_normalize_message_id_removes_brackets_and_lowercases():
    assert _normalize_message_id("<ABC-123@EXAMPLE.COM>") == "abc-123@example.com"
    assert _normalize_message_id("  abc@example.com  ") == "abc@example.com"


def test_html_to_text_removes_tags_and_unescapes():
    html_value = "<p>Hello&nbsp;<b>Team</b><br>Line 2</p>"
    text = _html_to_text(html_value)
    assert "Hello Team" in text
    assert "Line 2" in text


def test_extract_payload_bodies_prefers_plain_but_falls_back_to_html():
    plain = "SGVsbG8gdGV4dA"
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": plain}},
        ],
    }
    body_text, body_html = _extract_payload_bodies(payload)
    assert body_text == "Hello text"
    assert body_html == ""

    html_only = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": "PHA-SGVsbG8gPGI-Qm9keTwvYj48L3A-"}},
        ],
    }
    body_text2, body_html2 = _extract_payload_bodies(html_only)
    assert "Hello Body" in body_text2
    assert "<p>Hello <b>Body</b></p>" in body_html2


def test_decode_gmail_base64_and_reply_subject():
    assert _decode_gmail_base64("SGVsbG8") == "Hello"
    assert _derive_reply_subject("Status Update") == "Re: Status Update"
    assert _derive_reply_subject("Re: Existing") == "Re: Existing"


def test_cursor_encode_decode_roundtrip():
    cursor = _encode_cursor({1: "abc", 9: "xyz"})
    assert cursor
    decoded = _decode_cursor(cursor)
    assert decoded == {1: "abc", 9: "xyz"}


def test_cursor_decode_invalid_returns_empty():
    assert _decode_cursor("not-base64") == {}


# ensure the thread metadata helper writes through to the persistent cache so
# subsequent calls (even after the in‑memory TTL expires) can reuse the value.
import pytest
from sqlalchemy import select, delete

@pytest.mark.asyncio
async def test_metadata_persistence(session, monkeypatch):
    from app.routers.unibox import (
        _get_gmail_thread_metadata,
        _THREAD_META_CACHE,
    )

    # stub Gmail API to return a predictable payload
    def fake_request(token, url, method="GET", payload=None):
        return {"id": "threadX", "foo": "bar"}

    monkeypatch.setattr("app.routers.unibox._gmail_request_json", fake_request)

    # first call with force_refresh should store both in-memory and persisted data
    meta1 = await _get_gmail_thread_metadata("tok", 5, "threadX", force_refresh=True, db=session)
    assert meta1["id"] == "threadX"

    # drop in-memory cache and call again without refresh; persistent store should
    # supply the result.
    _THREAD_META_CACHE.clear()
    meta2 = await _get_gmail_thread_metadata("tok", 5, "threadX", force_refresh=False, db=session)
    assert meta2 == meta1


@pytest.mark.asyncio
async def test_list_conversations_from_db(session):
    """When threads/messages are stored locally the list API should return them."""
    from app.routers import unibox
    from app.models import Inbox, GmailAccount, GmailThread, GmailMessage

    # prepare inbox/account and a thread/message
    inbox = await session.execute(
        select(Inbox).where(Inbox.email == "dbtest@inbox.com")
    )
    if not inbox.scalars().first():
        inbox = Inbox(email="dbtest@inbox.com", provider="gmail")
        session.add(inbox)
        await session.flush()
    else:
        inbox = inbox.scalars().first()
    gmail_account = GmailAccount(
        inbox_id=inbox.id,
        google_email="dbtest@inbox.com",
        access_token="tok",
        refresh_token="ref",
    )
    session.add(gmail_account)
    await session.flush()

    # drop any existing threads/messages for cleanliness
    await session.execute(delete(GmailMessage).where(GmailMessage.inbox_id == inbox.id))
    await session.execute(delete(GmailThread).where(GmailThread.inbox_id == inbox.id))
    await session.flush()

    thread = GmailThread(
        inbox_id=inbox.id,
        thread_id="thr1",
        history_id="1",
        snippet="hello",
        last_internal_date=1600000000000,
    )
    session.add(thread)
    await session.flush()

    msg = GmailMessage(
        inbox_id=inbox.id,
        message_id="msg1",
        thread_id="thr1",
        internal_date=1600000000000,
        snippet="hi",
        headers_json=json.dumps([
            {"name": "From", "value": "alice@example.com"},
            {"name": "To", "value": "dbtest@inbox.com"},
        ]),
        label_ids_json=json.dumps(["INBOX"]),
    )
    session.add(msg)
    await session.flush()

    # clear any conversation/server caches to force fresh DB read
    from app.routers.unibox import _CONVERSATION_LIST_CACHE, _SERVER_CONTEXT_CACHE
    _CONVERSATION_LIST_CACHE.clear()
    _SERVER_CONTEXT_CACHE.clear()

    payload = await unibox.list_conversations(
        inbox_id=None,
        server_only=False,
        has_lead=False,
        participant_email=None,
        q=None,
        cursor=None,
        page_size=40,
        refresh=False,
        include_inboxes=False,
        include_detail=False,
        detail_provider=None,
        detail_inbox_id=None,
        detail_thread_id=None,
        db=session,
    )

    items = payload.get("items")
    assert items and len(items) == 1
    conv = items[0]
    assert conv["thread_id"] == "thr1"
    assert conv["snippet"] == "hello"
    assert "alice@example.com" in conv.get("participants", [])
    # new fields expected by the modern UI
    assert "inbox_email" in conv
    assert "provider" in conv
    assert conv["provider"] == inbox.provider

    # participant_email filter should include the thread when matching
    payload2 = await unibox.list_conversations(
        inbox_id=None,
        server_only=False,
        has_lead=False,
        participant_email="alice@example.com",
        q=None,
        cursor=None,
        page_size=40,
        refresh=False,
        include_inboxes=False,
        include_detail=False,
        detail_provider=None,
        detail_inbox_id=None,
        detail_thread_id=None,
        db=session,
    )
    assert payload2.get("items")


@pytest.mark.asyncio
async def test_list_conversations_non_gmail_provider(session):
    """Server‑only conversation for a non‑Gmail inbox should show that inbox's
    provider rather than "server".
    """
    from app.routers import unibox
    from app.models import Inbox, EmailLog, Lead, Campaign

    # create a basic lead/campaign/log linked to an SMTP inbox
    inbox = Inbox(email="smtpconv@i.com", provider="smtp")
    session.add(inbox)
    await session.flush()

    lead = Lead(email="a@b.com")
    campaign = Campaign(name="foo")
    session.add_all([lead, campaign])
    await session.flush()

    log = EmailLog(
        inbox_id=inbox.id,
        lead_id=lead.id,
        campaign_id=campaign.id,
        subject="hello",
        message_id="m",
    )
    session.add(log)
    await session.flush()

    # clear caches just in case
    from app.routers.unibox import _CONVERSATION_LIST_CACHE, _SERVER_CONTEXT_CACHE
    _CONVERSATION_LIST_CACHE.clear()
    _SERVER_CONTEXT_CACHE.clear()

    payload3 = await unibox.list_conversations(
        inbox_id=None,
        server_only=False,
        has_lead=False,
        participant_email=None,
        q=None,
        cursor=None,
        page_size=40,
        refresh=False,
        include_inboxes=False,
        include_detail=False,
        detail_provider=None,
        detail_inbox_id=None,
        detail_thread_id=None,
        db=session,
    )
    items = payload3.get("items")
    assert items and len(items) >= 1
    providers = {item["provider"] for item in items}
    assert "smtp" in providers


@pytest.mark.asyncio
async def test_reply_uses_inbox_provider(session, monkeypatch):
    from app.routers.unibox import reply_in_thread, UniboxReplyRequest

    # create a non-gmail inbox and stub send_email so we can observe the args
    from app.models import Inbox
    inbox = Inbox(email="smtp@i.com", provider="smtp")
    session.add(inbox)
    await session.flush()

    sent = {}
    def fake_send_email(**kwargs):
        sent.update(kwargs)
        # simulate a successful send
        class DummyResult:
            message_id = "x"
            thread_id = None
        return DummyResult()
    monkeypatch.setattr("app.routers.unibox.send_email", fake_send_email)

    body = UniboxReplyRequest(
        provider="smtp",
        inbox_id=inbox.id,
        thread_id="irrelevant",
        to_email="foo@bar",
        subject="hey",
        body="body",
        is_html=False,
    )
    # should not raise
    await reply_in_thread(body, db=session)
    assert sent.get("provider") == "smtp"
    assert sent.get("from_email") == "smtp@i.com"
    assert sent.get("subject") == "hey"

    # and if an inbox without a provider is used we should get a 400
    inbox2 = Inbox(email="noprov@i.com", provider="")
    session.add(inbox2)
    await session.flush()
    body2 = UniboxReplyRequest(
        inbox_id=inbox2.id,
        thread_id="irrelevant",
        to_email="foo@bar",
        subject="hey",
        body="body",
    )
    with pytest.raises(Exception) as excinfo:
        await reply_in_thread(body2, db=session)
    assert "no provider" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_message_detail_fetches_body(session):
    from app.routers import unibox
    from app.models import Inbox, GmailMessage, GmailAccount

    inbox = await session.execute(
        select(Inbox).where(Inbox.email == "body@inbox.com")
    )
    if not inbox.scalars().first():
        inbox = Inbox(email="body@inbox.com", provider="gmail")
        session.add(inbox)
        await session.flush()
    else:
        inbox = inbox.scalars().first()

    gmail_account = GmailAccount(
        inbox_id=inbox.id,
        google_email="body@inbox.com",
        access_token="tok",
        refresh_token="ref",
    )
    session.add(gmail_account)
    await session.flush()

    # message without body
    msg = GmailMessage(
        inbox_id=inbox.id,
        message_id="m1",
        thread_id="t1",
        internal_date=1600000000000,
        snippet="snip",
        headers_json=json.dumps([]),
        label_ids_json=json.dumps([]),
        body_fetched=False,
    )
    session.add(msg)
    await session.flush()

    # patch gmail sync to return body
    from app.gmail_sync import _gmail_request_json

    def fake_request(token, url, method="GET", payload=None):
        if "/messages/m1" in url and "format=full" in url:
            return {"payload": {"mimeType": "text/plain", "body": {"data": "SGVsbG8gd29ybGQ="}}}
        return {}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.gmail_sync._gmail_request_json", fake_request)

    result = await unibox.get_message_detail(
        provider="gmail", inbox_id=inbox.id, message_id="m1", refresh=True, db=session
    )
    assert result.get("body_plain") == "Hello world"
    monkeypatch.undo()

    # create a fake attachment row and exercise download route
    from app.models import GmailAttachment
    att = GmailAttachment(
        inbox_id=inbox.id,
        message_id="m1",
        attachment_id="att1",
        filename="foo.txt",
        mime_type="text/plain",
        size=5,
        downloaded=False,
    )
    session.add(att)
    await session.flush()

    def fake_attach(token, url, method="GET", payload=None):
        if "/attachments/att1" in url:
            return {"data": base64.b64encode(b"hello").decode("ascii")}
        return {}

    monkeypatch2 = pytest.MonkeyPatch()
    monkeypatch2.setattr("app.gmail_sync._gmail_request_json", fake_attach)

    meta = await unibox.get_attachment(inbox_id=inbox.id, attachment_id="att1", download=False, db=session)
    assert meta.get("downloaded") is False
    monkeypatch2.undo()


@pytest.mark.asyncio
async def test_conversation_detail_db(session):
    from app.routers import unibox
    from app.models import Inbox, GmailThread, GmailMessage, GmailAccount

    inbox = await session.execute(
        select(Inbox).where(Inbox.email == "detail@inbox.com")
    )
    if not inbox.scalars().first():
        inbox = Inbox(email="detail@inbox.com", provider="gmail")
        session.add(inbox)
        await session.flush()
    else:
        inbox = inbox.scalars().first()
    gmail_account = GmailAccount(
        inbox_id=inbox.id,
        google_email="detail@inbox.com",
        access_token="tok",
        refresh_token="ref",
    )
    session.add(gmail_account)
    await session.flush()

    # insert thread and two messages
    thread = GmailThread(
        inbox_id=inbox.id,
        thread_id="dt1",
        history_id="888",
        snippet="snp",
        last_internal_date=1600000001000,
    )
    session.add(thread)
    await session.flush()
    m1 = GmailMessage(
        inbox_id=inbox.id,
        message_id="m1",
        thread_id="dt1",
        internal_date=1600000000000,
        snippet="one",
        headers_json=json.dumps([{"name":"From","value":"a@a.com"}]),
        label_ids_json=json.dumps(["INBOX"]),
    )
    m2 = GmailMessage(
        inbox_id=inbox.id,
        message_id="m2",
        thread_id="dt1",
        internal_date=1600000001000,
        snippet="two",
        headers_json=json.dumps([{"name":"From","value":"b@b.com"}]),
        label_ids_json=json.dumps(["INBOX"]),
    )
    session.add_all([m1, m2])
    await session.flush()

    detail = await unibox.get_conversation_detail("gmail", inbox.id, "dt1", refresh=False, db=session)
    assert detail.get("thread_id") == "dt1"
    assert isinstance(detail.get("messages"), list)
    assert len(detail.get("messages")) == 2
    assert detail["messages"][0]["message_id"] == "m1"
    assert detail["messages"][1]["message_id"] == "m2"


@pytest.mark.asyncio
async def test_conversation_detail_auto_fetch_html(session, monkeypatch):
    """Detail endpoint should fetch HTML body automatically even without refresh."""
    from app.routers import unibox
    from app.models import Inbox, GmailThread, GmailMessage, GmailAccount

    inbox = await session.execute(select(Inbox).where(Inbox.email == "html2@inbox.com"))
    if not inbox.scalars().first():
        inbox = Inbox(email="html2@inbox.com", provider="gmail")
        session.add(inbox)
        await session.flush()
    else:
        inbox = inbox.scalars().first()
    gmail_account = GmailAccount(
        inbox_id=inbox.id,
        google_email="html2@inbox.com",
        access_token="tok",
        refresh_token="ref",
    )
    session.add(gmail_account)
    await session.flush()

    thread = GmailThread(
        inbox_id=inbox.id,
        thread_id="htmlthr",
        history_id="777",
        snippet="s",
        last_internal_date=1600000000000,
    )
    session.add(thread)
    await session.flush()
    msg = GmailMessage(
        inbox_id=inbox.id,
        message_id="hm2",
        thread_id="htmlthr",
        internal_date=1600000000000,
        snippet="snip",
        headers_json=json.dumps([{"name":"From","value":"a@a.com"}]),
        label_ids_json=json.dumps(["INBOX"]),
        body_fetched=False,
    )
    session.add(msg)
    await session.flush()

    def fake_request(token, url, method="GET", payload=None):
        if "/messages/hm2" in url and "format=full" in url:
            return {"payload": {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/html", "body": {"data": "PHA-SGVsbG8gd29ybGQ8L3A-"}}]}}
        return {}
    monkeypatch.setattr("app.gmail_sync._gmail_request_json", fake_request)

    detail = await unibox.get_conversation_detail("gmail", inbox.id, "htmlthr", refresh=False, db=session)
    assert detail.get("messages")[0].get("body_html") == "<p>Hello world</p>"
