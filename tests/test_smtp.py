"""Tests for the generic SMTP / IMAP inbox provider."""
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import sender as sender_mod
from app.models import (
    Campaign,
    CampaignInbox,
    CampaignLead,
    EmailLog,
    Inbox,
    Lead,
    LeadReply,
    Sequence,
    SmtpAccount,
    SmtpMessage,
    SmtpThread,
)
from app.routers import smtp as smtp_router
from app.smtp_utils import (
    normalise_message_id,
    parse_imap_message,
    validate_smtp_account_payload,
)


def _valid_payload(**overrides):
    base = {
        "smtp_host": "mail.example.com",
        "smtp_port": 587,
        "smtp_username": "user@example.com",
        "smtp_password": "secret",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "imap_host": "",
        "imap_port": 993,
        "imap_username": "",
        "imap_password": "",
        "imap_use_ssl": True,
    }
    base.update(overrides)
    return base


# ---- validation ------------------------------------------------------------


def test_validate_ok_send_only():
    assert validate_smtp_account_payload(_valid_payload()) is None


def test_validate_ok_with_imap():
    p = _valid_payload(
        imap_host="imap.example.com",
        imap_username="user@example.com",
        imap_password="secret",
    )
    assert validate_smtp_account_payload(p) is None


def test_validate_missing_host():
    assert validate_smtp_account_payload(_valid_payload(smtp_host="")) == "smtp_host is required"


def test_validate_tls_and_ssl_conflict():
    assert (
        validate_smtp_account_payload(_valid_payload(smtp_use_tls=True, smtp_use_ssl=True))
        == "Use either STARTTLS or implicit SSL, not both"
    )


def test_validate_bad_port():
    assert "smtp_port" in validate_smtp_account_payload(_valid_payload(smtp_port=0))


def test_validate_imap_needs_auth():
    err = validate_smtp_account_payload(_valid_payload(imap_host="imap.example.com"))
    assert err == "imap_username is required when imap_host is set"


def test_validate_password_optional_on_update():
    p = _valid_payload(smtp_password="")
    assert validate_smtp_account_payload(p) == "smtp_password is required"
    assert validate_smtp_account_payload(p, require_password=False) is None


def test_validate_rejects_plain_smtp():
    """No STARTTLS and no implicit SSL must be refused (cleartext password)."""
    p = _valid_payload(smtp_use_tls=False, smtp_use_ssl=False, imap_host="")
    assert "STARTTLS or implicit SSL" in validate_smtp_account_payload(p)


def test_plain_smtp_allowed_with_dev_flag(monkeypatch):
    """SMTP_ALLOW_PLAIN_SMTP=true re-allows plaintext (local relay doubles)."""
    from app.settings_manager import settings as app_settings

    monkeypatch.setattr(app_settings, "test_mode", False)
    monkeypatch.setenv("SMTP_ALLOW_PLAIN_SMTP", "true")
    p = _valid_payload(smtp_use_tls=False, smtp_use_ssl=False, imap_host="")
    assert validate_smtp_account_payload(p) is None


def test_validate_rejects_private_smtp_host(monkeypatch):
    """SSRF guard: literal loopback/private hosts are refused outside test mode."""
    from app.settings_manager import settings as app_settings

    monkeypatch.setattr(app_settings, "test_mode", False)
    p = _valid_payload(smtp_host="127.0.0.1")
    assert "private" in (validate_smtp_account_payload(p) or "")
    p6 = _valid_payload(smtp_host="169.254.169.254")
    assert "private" in (validate_smtp_account_payload(p6) or "")


def test_validate_allows_private_host_in_test_mode(monkeypatch):
    from app.settings_manager import settings as app_settings

    monkeypatch.setattr(app_settings, "test_mode", True)
    p = _valid_payload(smtp_host="127.0.0.1")
    assert validate_smtp_account_payload(p) is None


def test_sanitize_connection_error():
    from app.smtp_utils import sanitize_connection_error

    assert sanitize_connection_error("SMTP authentication failed: (535, b'bad')") == \
        "Authentication failed — check username/password"
    assert sanitize_connection_error("SMTP connection failed: timed out") == "Connection timed out"
    assert sanitize_connection_error("SMTP connection failed: [Errno 111] Connection refused") == \
        "Connection refused"
    assert sanitize_connection_error("SMTP connection failed: [Errno -2] Name or service not known") == \
        "DNS resolution failed — check the hostname"
    assert sanitize_connection_error("weird banner 220-mail.example.com ESMTP") == \
        "Connection failed (details in server logs)"


def test_normalise_message_id():
    assert normalise_message_id("abc@x.com") == "<abc@x.com>"
    assert normalise_message_id("<abc@x.com>") == "<abc@x.com>"
    assert normalise_message_id(None) == ""
    assert normalise_message_id("  ") == ""


# ---- IMAP parsing ----------------------------------------------------------


def _raw_email():
    msg = EmailMessage()
    msg["From"] = "Lead Person <lead@example.com>"
    msg["To"] = "me@mydomain.com"
    msg["Subject"] = "Re: Hello"
    msg["Message-ID"] = "<reply123@mail.example.com>"
    msg["In-Reply-To"] = "<root456@mail.example.com>"
    msg["Date"] = "Thu, 04 Sep 2026 10:00:00 +0000"
    msg.set_content("Sounds great, let's talk!")
    return msg.as_bytes()


def test_parse_imap_message():
    parsed = parse_imap_message(_raw_email())
    assert parsed["from"] == "lead@example.com"
    assert parsed["to"] == ["me@mydomain.com"]
    assert parsed["subject"] == "Re: Hello"
    assert parsed["message_id"] == "<reply123@mail.example.com>"
    assert parsed["in_reply_to"] == "<root456@mail.example.com>"
    assert "Sounds great" in parsed["body_plain"]
    assert isinstance(parsed["date"], datetime)


# ---- SMTP sending (mocked transport) ---------------------------------------


class _FakeSMTP:
    def __init__(self, behaviour="ok"):
        self.behaviour = behaviour
        self.sent = []

    def sendmail(self, from_addr, to_addrs, msg):
        self.sent.append((from_addr, to_addrs))
        if self.behaviour == "refused":
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (550, b"mailbox unavailable")})
        if self.behaviour == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        return {}

    def quit(self):
        pass

    def close(self):
        pass


def _smtp_account(**overrides):
    kwargs = {
        "inbox_id": 1,
        "smtp_host": "mail.example.com",
        "smtp_port": 587,
        "smtp_username": "u",
        "smtp_password": "p",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    }
    kwargs.update(overrides)
    return SmtpAccount(**kwargs)


def test_send_via_smtp_ok(monkeypatch):
    fake = _FakeSMTP("ok")
    monkeypatch.setattr("app.smtp_utils._smtp_connect", lambda account, timeout=30: fake)
    acct = _smtp_account()
    res = sender_mod._send_via_smtp(
        to_email="lead@example.com",
        subject="Hi",
        body="Hello",
        from_email="me@mydomain.com",
        smtp_account=acct,
    )
    assert isinstance(res, sender_mod.SendResult)
    assert res.message_id.startswith("<")
    # New thread: thread key equals our own Message-ID.
    assert res.thread_id == res.message_id
    assert fake.sent and fake.sent[0][1] == ["lead@example.com"]


def test_send_via_smtp_thread_key_from_references(monkeypatch):
    fake = _FakeSMTP("ok")
    monkeypatch.setattr("app.smtp_utils._smtp_connect", lambda account, timeout=30: fake)
    acct = _smtp_account()
    res = sender_mod._send_via_smtp(
        to_email="lead@example.com",
        subject="Re: Hi",
        body="Follow up",
        from_email="me@mydomain.com",
        reply_to_msg_id="<root@mail.example.com>",
        references="<root@mail.example.com>",
        smtp_account=acct,
    )
    assert res.thread_id == "<root@mail.example.com>"


def test_send_via_smtp_recipient_refused(monkeypatch):
    fake = _FakeSMTP("refused")
    monkeypatch.setattr("app.smtp_utils._smtp_connect", lambda account, timeout=30: fake)
    res = sender_mod._send_via_smtp(
        to_email="bad@example.com",
        subject="Hi",
        body="Hello",
        from_email="me@mydomain.com",
        smtp_account=_smtp_account(),
    )
    assert isinstance(res, sender_mod.SendFailure)
    assert res.error_type == "invalid_recipient"


def test_send_via_smtp_auth_failed(monkeypatch):
    fake = _FakeSMTP("auth")
    monkeypatch.setattr("app.smtp_utils._smtp_connect", lambda account, timeout=30: fake)
    res = sender_mod._send_via_smtp(
        to_email="lead@example.com",
        subject="Hi",
        body="Hello",
        from_email="me@mydomain.com",
        smtp_account=_smtp_account(),
    )
    assert isinstance(res, sender_mod.SendFailure)
    assert res.error_type == "auth_failed"


def test_send_email_dispatch_smtp_requires_account():
    res = sender_mod.send_email(
        to_email="lead@example.com",
        subject="Hi",
        body="Hello",
        from_email="me@mydomain.com",
        provider="smtp",
        smtp_account=None,
    )
    assert isinstance(res, sender_mod.SendFailure)
    assert res.error_type == "auth_failed"


def test_send_via_smtp_sender_refused_is_not_bounce(monkeypatch):
    """CR-01: a sender-side SMTPSenderRefused must map to auth_failed (inbox
    pause), never to 'bounce' — otherwise one broken relay marks every lead
    in the campaign as permanently bounced."""
    import smtplib as _smtplib

    class _RefusingClient:
        def __init__(self, *a, **k):
            pass

        def sendmail(self, from_addr, to_addrs, msg):
            raise _smtplib.SMTPSenderRefused(550, "relay auth failed", from_addr)

        def quit(self):
            return (221, b"bye")

        def close(self):
            pass

    monkeypatch.setattr(
        "app.smtp_utils._smtp_connect", lambda account, timeout=15.0: _RefusingClient()
    )

    account = SmtpAccount(
        smtp_host="mail.example.com", smtp_port=587, smtp_username="u",
        smtp_password="p", smtp_use_tls=True, smtp_use_ssl=False,
    )
    res = sender_mod._send_via_smtp(
        to_email="lead@example.com", subject="Hi", body="Hello",
        from_email="me@mydomain.com", smtp_account=account,
    )
    assert isinstance(res, sender_mod.SendFailure)
    assert res.error_type == "auth_failed"


# ---- Campaign test-send path (send_test_email) ------------------------------


@pytest.mark.asyncio
async def test_send_test_email_passes_smtp_account(session, monkeypatch):
    """Regression: campaign test sends from an SMTP inbox must load the
    inbox's SmtpAccount and hand it to send_email (P2 review finding)."""
    from app.routers import campaigns as campaigns_router
    from app.routers.campaigns import TestEmailRequest

    campaign = Campaign(name="T", sending_days=[], timezone="UTC")
    session.add(campaign)
    await session.flush()

    inbox = Inbox(email="me@mydomain.com", provider="smtp")
    session.add(inbox)
    await session.flush()
    session.add(CampaignInbox(campaign_id=campaign.id, inbox_id=inbox.id, position=0))

    seq = Sequence(campaign_id=campaign.id, position=0, subject="Hi", body="Hello")
    session.add(seq)
    await session.flush()

    acct = SmtpAccount(
        inbox_id=inbox.id,
        smtp_host="mail.example.com",
        smtp_port=587,
        smtp_username="user@example.com",
        smtp_password="secret",
        smtp_use_tls=True,
        smtp_use_ssl=False,
    )
    session.add(acct)
    await session.flush()

    captured = {}

    def _fake_send_email(**kwargs):
        captured.update(kwargs)
        return sender_mod.SendResult(message_id="test-123", thread_id="t")

    monkeypatch.setattr(sender_mod, "send_email", _fake_send_email)

    result = await campaigns_router.send_test_email(
        campaign.id, TestEmailRequest(sequence_id=seq.id, to_email="me@me.com"), db=session
    )
    assert result["ok"] is True
    assert captured["provider"] == "smtp"
    assert captured["smtp_account"] is acct


@pytest.mark.asyncio
async def test_send_test_email_smtp_missing_account_500(session, monkeypatch):
    """SMTP inbox with no SmtpAccount still surfaces a failure (not a crash)."""
    from fastapi import HTTPException

    from app.routers import campaigns as campaigns_router
    from app.routers.campaigns import TestEmailRequest

    campaign = Campaign(name="T", sending_days=[], timezone="UTC")
    session.add(campaign)
    await session.flush()

    inbox = Inbox(email="me@mydomain.com", provider="smtp")
    session.add(inbox)
    await session.flush()
    session.add(CampaignInbox(campaign_id=campaign.id, inbox_id=inbox.id, position=0))

    seq = Sequence(campaign_id=campaign.id, position=0, subject="Hi", body="Hello")
    session.add(seq)
    await session.flush()

    monkeypatch.setattr(sender_mod, "send_email", lambda **kw: sender_mod.SendFailure(error_type="auth_failed", message="No SMTP credentials provided"))

    with pytest.raises(HTTPException):
        await campaigns_router.send_test_email(
            campaign.id, TestEmailRequest(sequence_id=seq.id, to_email="me@me.com"), db=session
        )


# ---- SMTP router CRUD ------------------------------------------------------


@pytest.mark.asyncio
async def test_smtp_crud_and_test(session, monkeypatch):
    inbox = Inbox(email="me@mydomain.com", provider="smtp")
    session.add(inbox)
    await session.flush()

    saved = await smtp_router.upsert_smtp_account(
        inbox.id, smtp_router.SmtpAccountUpsert(**_valid_payload()), db=session, _user=object()
    )
    assert saved["smtp_host"] == "mail.example.com"
    assert saved["has_smtp_password"] is True

    fetched = await smtp_router.get_smtp_account(inbox.id, db=session, _user=object())
    assert fetched["inbox_id"] == inbox.id

    # Update without password keeps the stored secret.
    saved2 = await smtp_router.upsert_smtp_account(
        inbox.id,
        smtp_router.SmtpAccountUpsert(**_valid_payload(smtp_host="mail2.example.com", smtp_password="")),
        db=session,
        _user=object(),
    )
    assert saved2["smtp_host"] == "mail2.example.com"
    assert saved2["has_smtp_password"] is True

    class _R:
        def __init__(self, ok, error="", detail=""):
            self.ok = ok
            self.error = error
            self.detail = detail

    async def _fake_test(account):
        return _R(True, "", "SMTP ok"), _R(True, "", "skipped")

    def _fake_test_sync(account):
        return _R(True, "", "SMTP ok"), _R(True, "", "skipped")

    monkeypatch.setattr(smtp_router, "test_account_connections", _fake_test_sync)
    result = await smtp_router.test_smtp_account(inbox.id, db=session, _user=object())
    assert result["ok"] is True

    # Validation error surfaces as 400.
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await smtp_router.upsert_smtp_account(
            inbox.id,
            smtp_router.SmtpAccountUpsert(**_valid_payload(smtp_host="", smtp_password="x")),
            db=session,
            _user=object(),
        )


@pytest.mark.asyncio
async def test_smtp_rejects_non_smtp_inbox(session):
    inbox = Inbox(email="g@gmail.com", provider="gmail")
    session.add(inbox)
    await session.flush()
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await smtp_router.upsert_smtp_account(
            inbox.id, smtp_router.SmtpAccountUpsert(**_valid_payload()), db=session, _user=object()
        )


# ---- Unibox SMTP mirror + reply detection ----------------------------------


@pytest.mark.asyncio
async def test_smtp_reply_detection(session):
    from app import unibox as unibox_mod

    inbox = Inbox(email="me@mydomain.com", provider="smtp")
    session.add(inbox)
    await session.flush()
    lead = Lead(email="lead@example.com", name="Lead")
    session.add(lead)
    await session.flush()
    campaign = Campaign(name="C", sending_days=[0, 1, 2, 3, 4])
    session.add(campaign)
    await session.flush()
    cl = CampaignLead(campaign_id=campaign.id, lead_id=lead.id)
    session.add(cl)
    await session.flush()
    session.add(CampaignInbox(campaign_id=campaign.id, inbox_id=inbox.id, position=0))
    root_mid = make_msgid()
    session.add(
        EmailLog(
            lead_id=lead.id,
            campaign_id=campaign.id,
            inbox_id=inbox.id,
            sequence_index=0,
            subject="Hello",
            message_id=root_mid,
            thread_id=root_mid,
        )
    )
    await session.flush()

    parsed = {
        "subject": "Re: Hello",
        "message_id": "<reply@mail.example.com>",
        "in_reply_to": root_mid,
        "references": [root_mid],
        "from": "lead@example.com",
        "to": ["me@mydomain.com"],
        "body_plain": "Yes, interested!",
        "body_html": "",
        "date": datetime.utcnow(),
        "snippet": "Yes, interested!",
    }
    row, created, touched = await unibox_mod._process_smtp_inbound_message(
        session, inbox=inbox, parsed=parsed, message_pk="1:42", thread_key=root_mid
    )
    assert created is True
    assert touched == (inbox.id, root_mid)

    from sqlalchemy import select

    thread = (
        await session.execute(
            select(SmtpThread).where(
                SmtpThread.inbox_id == inbox.id, SmtpThread.thread_key == root_mid
            )
        )
    ).scalar_one()
    assert thread.is_lead_thread is True
    assert thread.unread_lead_reply is True

    reply = (
        await session.execute(
            select(LeadReply).where(
                LeadReply.lead_id == lead.id, LeadReply.campaign_id == campaign.id
            )
        )
    ).scalar_one_or_none()
    assert reply is not None

    # Thread view shows sent + received.
    await unibox_mod.upsert_sent_smtp_message(
        session,
        inbox_id=inbox.id,
        thread_key=root_mid,
        internet_message_id=root_mid,
        subject="Hello",
        to_email="lead@example.com",
        from_email="me@mydomain.com",
        body="Hello",
        is_html=False,
    )
    payload = await unibox_mod._get_smtp_thread_messages(
        session, thread_id=root_mid, inbox_id=inbox.id
    )
    assert payload is not None
    assert len(payload["messages"]) == 2
    assert {m["direction"] for m in payload["messages"]} == {"sent", "received"}

    # Idempotent on re-processing the same UID.
    row2, created2, _ = await unibox_mod._process_smtp_inbound_message(
        session, inbox=inbox, parsed=parsed, message_pk="1:42", thread_key=root_mid
    )
    assert created2 is False
    assert row2.message_id == row.message_id


@pytest.mark.asyncio
async def test_smtp_list_conversations(session):
    from app import unibox as unibox_mod

    inbox = Inbox(email="me@mydomain.com", provider="smtp")
    session.add(inbox)
    await session.flush()
    thread_key = make_msgid()
    session.add(
        SmtpThread(
            inbox_id=inbox.id,
            thread_key=thread_key,
            subject="Hello",
            is_lead_thread=True,
            last_received_at=datetime.utcnow(),
        )
    )
    session.add(
        SmtpMessage(
            inbox_id=inbox.id,
            message_id="1:7",
            thread_key=thread_key,
            subject="Hello",
            from_address="lead@example.com",
            body_plain="hi",
            direction="received",
            received_at=datetime.utcnow(),
        )
    )
    await session.flush()

    result = await unibox_mod.list_unibox_conversations(session, page=1, page_size=50)
    smtp_items = [i for i in result["items"] if i["provider"] == "smtp"]
    assert len(smtp_items) == 1
    assert smtp_items[0]["thread_id"] == thread_key

    count = await unibox_mod.get_notification_count(session)
    assert count >= 0
    assert await unibox_mod.mark_thread_read(session, thread_id=thread_key, inbox_id=inbox.id) is True
