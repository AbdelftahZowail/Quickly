"""End-to-end SMTP tests against real local test doubles.

``test_mode`` fakes sends (no delivery), so it cannot verify the SMTP stack.
These tests drive the real code paths instead:

* outbound: queue slot -> ``run_send_job`` -> ``smtplib`` -> a real local
  SMTP relay (``aiosmtpd`` on 127.0.0.1), asserting the delivered bytes,
  ``EmailLog``, queue consumption, unibox mirror, and webhooks;
* inbound: IMAP reply bytes -> ``_sync_inbox_smtp`` -> ``LeadReply``,
  queue cleanup, thread flags, and NDR bounce handling.

The only fakes are the network peers (local relay + in-memory IMAP4).
"""
import asyncio
import socket
from datetime import timedelta
from email.message import EmailMessage

import pytest

aiosmtpd_controller = pytest.importorskip("aiosmtpd.controller", reason="aiosmtpd not installed")
aiosmtpd_smtp = pytest.importorskip("aiosmtpd.smtp", reason="aiosmtpd not installed")
Controller = aiosmtpd_controller.Controller
AuthResult = aiosmtpd_smtp.AuthResult
from sqlalchemy import func, select

from app import time as time_provider
from app import unibox as unibox_mod
from app.jobs import run_send_job
from app.models import (
    CampaignLead,
    EmailLog,
    Inbox,
    Lead,
    LeadReply,
    QueueSlot,
    SmtpAccount,
    SmtpMessage,
    SmtpSyncState,
    SmtpThread,
)
from app.settings_manager import settings
from tests.conftest import (
    make_campaign,
    make_campaign_inbox,
    make_campaign_lead,
    make_lead,
    make_queue_slot,
    make_sequence,
)


# ---------------------------------------------------------------------------
# Test doubles: local SMTP relay + in-memory IMAP server
# ---------------------------------------------------------------------------


def _allow_all_auth(server, session, envelope, mechanism, auth_data):
    return AuthResult(success=True)


class _RecordingHandler:
    def __init__(self):
        self.received: list[tuple[str, list[str], bytes]] = []

    async def handle_DATA(self, server, session, envelope):
        self.received.append((envelope.mail_from, list(envelope.rcpt_tos), bytes(envelope.content)))
        return "250 OK"


@pytest.fixture()
def smtp_relay():
    """A real SMTP server on 127.0.0.1 accepting any credentials."""
    handler = _RecordingHandler()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    controller = Controller(
        handler, hostname="127.0.0.1", port=port,
        authenticator=_allow_all_auth, auth_require_tls=False,
    )
    controller.start()
    yield port, handler.received
    controller.stop()


class _FakeSocket:
    def settimeout(self, timeout):
        pass


class _FakeIMAP:
    """Speaks just enough IMAP4 for ``_fetch_smtp_new_messages``."""

    def __init__(self, messages: list[tuple[int, bytes]], uidvalidity: int = 4242):
        self._messages = sorted(messages)
        self._uidvalidity = uidvalidity

    def socket(self):
        return _FakeSocket()

    def status(self, mailbox, query):
        return "OK", [f'{mailbox} (UIDVALIDITY {self._uidvalidity} UIDNEXT 99999)'.encode()]

    def uid(self, command, *args):
        if command == "search":
            # args: (None, "UID {n}:*")
            criterion = args[-1] if args else "UID 1:*"
            try:
                low = int(str(criterion).split("UID")[1].split(":")[0].strip())
            except (IndexError, ValueError):
                low = 1
            uids = [str(uid) for uid, _ in self._messages if uid >= low]
            return "OK", [" ".join(uids).encode()]
        if command == "fetch":
            uid = int(args[0])
            for candidate_uid, raw in self._messages:
                if candidate_uid == uid:
                    return "OK", [(f'{uid} (RFC822 {{{len(raw)}}})'.encode(), raw)]
            return "NO", []
        raise AssertionError(f"unexpected IMAP command: {command}")

    def logout(self):
        return "OK", []


class _SessionCtx:
    """Reuse the test session for code paths that open their own session."""

    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _reply_bytes(*, from_addr: str, to_addr: str, subject: str, body: str,
                 message_id: str, in_reply_to: str = "") -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg["Date"] = "Fri, 05 Sep 2026 10:00:00 +0000"
    msg.set_content(body)
    return msg.as_bytes()


# ---------------------------------------------------------------------------
# Outbound: slot -> send job -> real local relay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_smtp_send_through_real_relay(session, smtp_relay, monkeypatch):
    port, received = smtp_relay

    inbox = Inbox(email="sender@e2e.example", display_name="E2E Sender", provider="smtp")
    session.add(inbox)
    await session.flush()
    session.add(
        SmtpAccount(
            inbox_id=inbox.id,
            smtp_host="127.0.0.1",
            smtp_port=port,
            smtp_username="sender@e2e.example",
            smtp_password="secret",
            smtp_use_tls=False,
            smtp_use_ssl=False,
        )
    )
    campaign = await make_campaign(
        session, sending_days=[0, 1, 2, 3, 4, 5, 6],
        sending_hours_start="00:00", sending_hours_end="23:59",
    )
    seq = await make_sequence(
        session, campaign.id, subject="Hello {{name}}", body="Hi {{name}}, quick note.",
    )
    assert seq.id is not None
    lead = await make_lead(session, email="ada@lead.example", name="Ada")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    slot = await make_queue_slot(
        session, cl.id, inbox.id,
        scheduled_date=time_provider.now() - timedelta(minutes=1),
    )
    await session.flush()

    events: list[tuple[str, dict]] = []

    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.AsyncSessionLocal", lambda: _SessionCtx(session))
    monkeypatch.setattr(settings, "test_mode", False)

    await run_send_job()

    # 1. The relay really got exactly one delivery, fully rendered.
    assert len(received) == 1
    mail_from, rcpt_tos, content = received[0]
    assert mail_from == "sender@e2e.example"
    assert rcpt_tos == ["ada@lead.example"]
    text = content.decode("utf-8", errors="replace")
    assert "Subject: Hello Ada" in text
    assert "Hi Ada, quick note." in text
    assert "Message-ID:" in text
    assert "List-Unsubscribe:" in text

    # 2. Pipeline state: log written, slot consumed, enrollment advanced.
    log = (await session.execute(select(EmailLog))).scalar_one()
    assert log.inbox_id == inbox.id
    assert log.message_id.startswith("<")
    assert log.thread_id == log.message_id  # new thread
    assert (await session.execute(select(func.count(QueueSlot.id)))).scalar() == 0
    await session.refresh(cl)
    assert cl.enrollment_status == "completed"  # single-sequence campaign: last step sent
    assert any(name == "email.sent" for name, _ in events)

    # 3. Unibox mirror has the sent message under the same thread key.
    thread = (
        await session.execute(
            select(SmtpThread).where(
                SmtpThread.inbox_id == inbox.id, SmtpThread.thread_key == log.thread_id
            )
        )
    ).scalar_one()
    assert thread.is_lead_thread is True
    sent_mirror = (
        await session.execute(
            select(SmtpMessage).where(
                SmtpMessage.inbox_id == inbox.id, SmtpMessage.thread_key == log.thread_id
            )
        )
    ).scalar_one()
    assert sent_mirror.direction == "sent"
    assert sent_mirror.rfc_message_id == log.message_id


# ---------------------------------------------------------------------------
# Inbound: IMAP reply -> sync -> LeadReply + queue cleanup (+ NDR bounce)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_smtp_imap_reply_and_bounce(session, monkeypatch):
    inbox = Inbox(email="sender@e2e.example", provider="smtp")
    session.add(inbox)
    await session.flush()
    session.add(
        SmtpAccount(
            inbox_id=inbox.id,
            smtp_host="127.0.0.1",
            smtp_port=2525,
            smtp_username="sender@e2e.example",
            smtp_password="secret",
            smtp_use_tls=False,
            smtp_use_ssl=False,
            imap_host="127.0.0.1",
            imap_username="sender@e2e.example",
            imap_password="secret",
        )
    )
    campaign = await make_campaign(
        session, sending_days=[0, 1, 2, 3, 4, 5, 6],
        sending_hours_start="00:00", sending_hours_end="23:59",
    )
    await make_sequence(session, campaign.id)
    lead = await make_lead(session, email="ada@lead.example", name="Ada")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    bounced_lead = await make_lead(session, email="gone@lead.example", name="Gone")
    bounced_cl = await make_campaign_lead(session, campaign.id, bounced_lead.id)

    root_mid = "<root-e2e@mail.example.com>"
    session.add(
        EmailLog(
            lead_id=lead.id, campaign_id=campaign.id, inbox_id=inbox.id,
            sequence_index=0, subject="Hello", message_id=root_mid, thread_id=root_mid,
        )
    )
    bounced_root = "<root-bounced@mail.example.com>"
    session.add(
        EmailLog(
            lead_id=bounced_lead.id, campaign_id=campaign.id, inbox_id=inbox.id,
            sequence_index=0, subject="Hello", message_id=bounced_root, thread_id=bounced_root,
        )
    )
    # A future follow-up slot that the reply must cancel.
    follow_up = await make_queue_slot(
        session, cl.id, inbox.id, sequence_index=1,
        scheduled_date=time_provider.now() + timedelta(days=1), position_in_day=1,
    )
    await session.flush()

    ndr_body = (
        "This is the mail system at host mail.example.com.\n"
        "Final-Recipient: rfc822; gone@lead.example\n"
        "Action: failed\nStatus: 5.1.1\n"
    )
    ndr = EmailMessage()
    ndr["From"] = "MAILER-DAEMON@mail.example.com"
    ndr["To"] = "sender@e2e.example"
    ndr["Subject"] = "Undelivered Mail Returned to Sender"
    ndr["Message-ID"] = "<ndr-1@mail.example.com>"
    ndr["Date"] = "Fri, 05 Sep 2026 11:00:00 +0000"
    ndr.set_content(ndr_body)

    fake_imap = _FakeIMAP([
        (1, _reply_bytes(
            from_addr="Ada <ada@lead.example>", to_addr="sender@e2e.example",
            subject="Re: Hello", body="Yes, let's talk!",
            message_id="<reply-e2e@mail.example.com>", in_reply_to=root_mid,
        )),
        (2, ndr.as_bytes()),
    ])
    monkeypatch.setattr("app.smtp_utils._imap_connect", lambda account, timeout=30: fake_imap)

    touched = await unibox_mod._sync_inbox_smtp(session, inbox, "e2e")
    assert (inbox.id, root_mid) in touched
    await asyncio.sleep(0.2)  # let background webhook/classify tasks settle

    # Reply side effects.
    reply = (
        await session.execute(
            select(LeadReply).where(
                LeadReply.lead_id == lead.id, LeadReply.campaign_id == campaign.id
            )
        )
    ).scalar_one_or_none()
    assert reply is not None
    assert (await session.execute(
        select(QueueSlot).where(QueueSlot.id == follow_up.id)
    )).scalar_one_or_none() is None
    thread = (
        await session.execute(
            select(SmtpThread).where(
                SmtpThread.inbox_id == inbox.id, SmtpThread.thread_key == root_mid
            )
        )
    ).scalar_one()
    assert thread.is_lead_thread is True
    assert thread.unread_lead_reply is True

    # NDR side effects: bounced enrollment, no reply row for the daemon.
    await session.refresh(bounced_cl)
    assert bounced_cl.enrollment_status == "bounced"

    # Checkpoint advanced: a second sync sees nothing new and changes nothing.
    reply_count = (await session.execute(select(func.count(LeadReply.id)))).scalar()
    touched2 = await unibox_mod._sync_inbox_smtp(session, inbox, "e2e-again")
    assert touched2 == set()
    assert (await session.execute(select(func.count(LeadReply.id)))).scalar() == reply_count
    state = (
        await session.execute(select(SmtpSyncState).where(SmtpSyncState.inbox_id == inbox.id))
    ).scalar_one()
    assert state.last_uid == 2
    assert state.uidvalidity == 4242


# ---------------------------------------------------------------------------
# Test mode still simulates (no delivery) — documented contrast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_mode_simulates_smtp_send(monkeypatch):
    from app.sender import SendResult, send_email

    monkeypatch.setattr(settings, "test_mode", True)
    try:
        res = send_email(
            to_email="ada@lead.example", subject="Hi", body="Hello",
            from_email="sender@e2e.example", provider="smtp", smtp_account=None,
        )
    finally:
        monkeypatch.setattr(settings, "test_mode", False)
    assert isinstance(res, SendResult) and bool(res)
