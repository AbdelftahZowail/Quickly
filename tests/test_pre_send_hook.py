"""Pre-send decision hook (app/pre_send_hook.py) in the per-slot send job.

With ``PRE_SEND_HOOK_URL`` set, the first email of a sequence to a lead is
preceded by one signed POST to that URL, which answers allow, hold or deny.
Off by default, fail open by default, minimal data by default.  All HTTP goes
through httpx.MockTransport: no real calls are made.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

import app.jobs as jobs_mod
from app import pre_send_hook as hook
from app import time as time_provider
from app.app_settings import PRE_SEND_HOOK_ON_ERROR_KEY, PRE_SEND_HOOK_URL_KEY, put_setting
from app.models import EmailLog, QueueSlot, SmtpAccount
from app.sender import SendResult
from app.settings_manager import settings
from tests.conftest import (
    make_campaign,
    make_campaign_inbox,
    make_campaign_lead,
    make_inbox,
    make_lead,
    make_queue_slot,
    make_sequence,
)

HOOK_URL = "https://hooks.example.test/pre-send"
SECRET = "shh-0123456789"


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.fixture(autouse=True)
def _hook_defaults(monkeypatch):
    """Hook on at HOOK_URL with a secret, three-second timeout, fail open,
    minimal fields, first email only; test mode off so sends really run."""
    monkeypatch.setattr(settings, "pre_send_hook_url", HOOK_URL)
    monkeypatch.setattr(settings, "pre_send_hook_secret", SECRET)
    monkeypatch.setattr(settings, "pre_send_hook_timeout", "")
    monkeypatch.setattr(settings, "pre_send_hook_on_error", "")
    monkeypatch.setattr(settings, "pre_send_hook_fields", "")
    monkeypatch.setattr(settings, "pre_send_hook_scope", "")
    monkeypatch.setattr(settings, "test_mode", False)
    yield
    monkeypatch.setattr(hook, "_transport", None)


# ── Scripted hook ────────────────────────────────────────────────────────────

def _reply(decision: str, **extra) -> httpx.Response:
    return httpx.Response(200, json={"decision": decision, **extra})


class _Hook:
    """Records every request and answers from a queue of responses,
    callables or exceptions."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"unexpected hook request: {request.method} {request.url}")
        reply = self.replies.pop(0)
        if callable(reply):
            return reply(request)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def calls(self) -> int:
        return len(self.requests)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def _mock(monkeypatch, api: _Hook) -> _Hook:
    monkeypatch.setattr(hook, "_transport", httpx.MockTransport(api))
    return api


# ── Fixtures for the send job ────────────────────────────────────────────────

async def _make_smtp_inbox(session, email: str = "relay@example.com"):
    inbox = await make_inbox(session, email=email, provider="smtp")
    session.add(SmtpAccount(
        inbox_id=inbox.id, smtp_host="smtp.example.com", smtp_port=587,
        smtp_username="user", smtp_password="pass", smtp_use_tls=True,
    ))
    await session.flush()
    return inbox


async def _make_due_slot(session, inbox, *, lead_email="jane@acme.co.uk", sequence_index=0,
                         custom_data=None):
    campaign = await make_campaign(
        session, sending_days=[0, 1, 2, 3, 4, 5, 6],
        sending_hours_start="00:00", sending_hours_end="23:59",
    )
    await make_sequence(session, campaign.id, position=0)
    await make_sequence(session, campaign.id, position=1, subject="", wait_days_after_previous=2)
    lead = await make_lead(session, email=lead_email, name="Jane Doe")
    if custom_data is not None:
        lead.custom_data = custom_data
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    slot = await make_queue_slot(
        session, cl.id, inbox.id, sequence_index=sequence_index,
        scheduled_date=time_provider.now() - timedelta(minutes=1),
    )
    await session.flush()
    return slot, cl, lead, campaign


async def _run_slot(session, monkeypatch, slot_id: int):
    """Run send_slot_job with sending and webhooks faked; returns
    (send_email kwargs list, fired events list)."""
    sends: list[dict] = []
    events: list[tuple[str, dict]] = []

    def fake_send(**kwargs):
        sends.append(kwargs)
        return SendResult(message_id="<x@test>", thread_id="t")

    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.send_email", fake_send)
    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.webhooks.fire_webhook_event", fake_webhook)
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))
    jobs_mod._inbox_auth_cooldown_until.clear()

    await jobs_mod.send_slot_job(slot_id)
    return sends, events


async def _count(session, model, **where):
    stmt = select(func.count(model.id))
    for key, value in where.items():
        stmt = stmt.where(getattr(model, key) == value)
    return (await session.execute(stmt)).scalar()


# ── Off by default ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_url_means_no_request_and_a_normal_send(session, monkeypatch):
    monkeypatch.setattr(settings, "pre_send_hook_url", "")
    api = _mock(monkeypatch, _Hook())
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1
    assert api.calls == 0
    assert lead.pre_send_decision is None


@pytest.mark.asyncio
async def test_url_from_app_setting_row_when_env_is_empty(session, monkeypatch):
    monkeypatch.setattr(settings, "pre_send_hook_url", "")
    await put_setting(session, PRE_SEND_HOOK_URL_KEY, HOOK_URL)
    api = _mock(monkeypatch, _Hook(_reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1 and api.calls == 1
    assert lead.pre_send_decision == "allow"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://hooks.example.test/x", "ftp://hooks.example.test/x", "not a url"])
async def test_insecure_or_invalid_url_is_ignored(session, monkeypatch, url):
    monkeypatch.setattr(settings, "pre_send_hook_url", url)
    api = _mock(monkeypatch, _Hook())
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1 and api.calls == 0


def test_plain_http_is_allowed_only_for_the_local_machine():
    assert hook.url_allowed("http://localhost:9000/hook")
    assert hook.url_allowed("http://127.0.0.1/hook")
    assert hook.url_allowed("https://hooks.example.test/hook")
    assert not hook.url_allowed("http://hooks.example.test/hook")


# ── Decisions ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_allow_sends_and_records_the_decision(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(_reply("allow", reason="Known customer", reference="crm-77")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, campaign = await _make_due_slot(session, inbox, custom_data={"company": "Acme Ltd"})

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1 and sends[0]["to_email"] == "jane@acme.co.uk"
    assert await _count(session, EmailLog, inbox_id=inbox.id) == 1
    assert cl.sending_paused is False
    assert lead.pre_send_decision == "allow"
    assert lead.pre_send_check["reason"] == "Known customer"
    assert lead.pre_send_check["reference"] == "crm-77"
    assert lead.pre_send_check["host"] == "hooks.example.test"
    assert lead.pre_send_check["checked_at"]

    # Minimal data by default: the address and the campaign, no name, no custom data.
    assert api.calls == 1
    assert api.body() == {
        "event": "pre_send",
        "version": 1,
        "timestamp": api.body()["timestamp"],
        "lead": {"id": lead.id, "email": "jane@acme.co.uk"},
        "campaign": {"id": campaign.id, "public_id": campaign.public_id, "name": campaign.name},
        "sequence_index": 0,
    }


@pytest.mark.asyncio
async def test_deny_pauses_the_enrolment_and_keeps_the_reason(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(_reply("deny", reason="Consent needed first", reference="rcpt_9")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, events = await _run_slot(session, monkeypatch, slot.id)

    assert sends == [], "a denied lead must not be emailed"
    assert await _count(session, EmailLog, inbox_id=inbox.id) == 0
    assert cl.sending_paused is True
    assert await _count(session, QueueSlot, campaign_lead_id=cl.id) == 0
    assert lead.pre_send_decision == "deny"
    assert lead.pre_send_check["reason"] == "Consent needed first"
    assert lead.pre_send_check["reference"] == "rcpt_9"
    held = [data for event, data in events if event == "lead.status_changed"]
    assert len(held) == 1
    assert held[0]["sending_paused"] is True
    assert held[0]["pre_send_decision"] == "deny"
    assert held[0]["pre_send_reference"] == "rcpt_9"
    assert held[0]["reason"] == "Pre-send hook: deny: Consent needed first (ref rcpt_9)"


@pytest.mark.asyncio
async def test_hold_keeps_the_slot_and_does_not_ask_again_until_retry(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(_reply("hold", reason="Country not settled", retry_after_seconds=1800)))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, events = await _run_slot(session, monkeypatch, slot.id)

    assert sends == []
    assert cl.sending_paused is False, "a hold is not a pause"
    assert await _count(session, QueueSlot, campaign_lead_id=cl.id) == 1, "the slot stays for a later scan"
    assert lead.pre_send_decision == "hold"
    assert lead.pre_send_check["retry_at"]
    assert events == []

    # A second scan inside the retry window reuses the hold without a new request.
    sends, _ = await _run_slot(session, monkeypatch, slot.id)
    assert sends == [] and api.calls == 1


@pytest.mark.asyncio
async def test_a_hold_is_asked_again_after_its_retry_time(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(_reply("hold", retry_after_seconds=60), _reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)
    await _run_slot(session, monkeypatch, slot.id)
    assert api.calls == 1

    # Pretend the hold was recorded two minutes ago.
    check = dict(lead.pre_send_check)
    past = time_provider.utcnow() - timedelta(minutes=2)
    check["checked_at"] = past.isoformat() + "Z"
    check["retry_at"] = (past + timedelta(seconds=60)).isoformat() + "Z"
    lead.pre_send_check = check
    await session.flush()

    sends, _ = await _run_slot(session, monkeypatch, slot.id)
    assert api.calls == 2 and len(sends) == 1
    assert lead.pre_send_decision == "allow"


@pytest.mark.asyncio
async def test_a_recent_decision_is_reused_for_the_same_lead(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(_reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)
    await _run_slot(session, monkeypatch, slot.id)
    assert api.calls == 1

    # The same lead enrolled in another campaign (sent from another inbox, so
    # the inbox wait time does not get in the way): no second request within 30 days.
    inbox2 = await _make_smtp_inbox(session, email="relay2@example.com")
    campaign2 = await make_campaign(
        session, sending_days=[0, 1, 2, 3, 4, 5, 6],
        sending_hours_start="00:00", sending_hours_end="23:59",
    )
    await make_sequence(session, campaign2.id, position=0)
    cl2 = await make_campaign_lead(session, campaign2.id, lead.id)
    await make_campaign_inbox(session, campaign2.id, inbox2.id)
    slot2 = await make_queue_slot(
        session, cl2.id, inbox2.id, sequence_index=0,
        scheduled_date=time_provider.now() - timedelta(minutes=1),
    )
    await session.flush()

    sends, _ = await _run_slot(session, monkeypatch, slot2.id)
    assert len(sends) == 1 and api.calls == 1


# ── Failure handling ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("slow"),
    httpx.ConnectError("down"),
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"decision": "maybe"}),
])
async def test_a_hook_that_cannot_answer_fails_open_by_default(session, monkeypatch, failure):
    api = _mock(monkeypatch, _Hook(failure))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1, "fail open: the send goes ahead"
    assert cl.sending_paused is False
    assert lead.pre_send_decision == "error_allow"
    assert lead.pre_send_check["error"]


@pytest.mark.asyncio
async def test_on_error_hold_keeps_the_slot_and_asks_again_next_scan(session, monkeypatch):
    await put_setting(session, PRE_SEND_HOOK_ON_ERROR_KEY, "hold")
    api = _mock(monkeypatch, _Hook(httpx.ConnectTimeout("slow"), _reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)
    assert sends == []
    assert await _count(session, QueueSlot, campaign_lead_id=cl.id) == 1
    assert lead.pre_send_decision == "error_hold"

    # Errors are never cached: the next scan asks again.
    sends, _ = await _run_slot(session, monkeypatch, slot.id)
    assert len(sends) == 1 and api.calls == 2


# ── Signing ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_requests_are_signed_with_the_secret(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(_reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    await _run_slot(session, monkeypatch, slot.id)

    request = api.requests[0]
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-quickly-event"] == "pre_send"
    assert request.headers["user-agent"] == "Quickly-PreSendHook/1"
    timestamp = request.headers["x-quickly-timestamp"]
    assert timestamp.isdigit()
    expected = hmac.new(SECRET.encode(), timestamp.encode() + b"." + request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-quickly-signature"] == f"v1={expected}"
    assert "authorization" not in request.headers


@pytest.mark.asyncio
async def test_no_secret_means_no_signature_header(session, monkeypatch):
    monkeypatch.setattr(settings, "pre_send_hook_secret", "")
    api = _mock(monkeypatch, _Hook(_reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    await _run_slot(session, monkeypatch, slot.id)

    assert "x-quickly-signature" not in api.requests[0].headers


@pytest.mark.asyncio
async def test_a_signed_response_is_verified(session, monkeypatch):
    def signed(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"decision": "deny", "reason": "No"}).encode()
        sig = "v1=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        return httpx.Response(200, content=body, headers={"X-Hook-Signature": sig, "Content-Type": "application/json"})

    api = _mock(monkeypatch, _Hook(signed))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)
    assert sends == [] and lead.pre_send_decision == "deny"


@pytest.mark.asyncio
async def test_a_wrongly_signed_response_counts_as_an_error(session, monkeypatch):
    def badly_signed(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"decision": "deny"}).encode()
        return httpx.Response(200, content=body, headers={"X-Hook-Signature": "v1=deadbeef", "Content-Type": "application/json"})

    api = _mock(monkeypatch, _Hook(badly_signed))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)
    assert len(sends) == 1, "fail open: a forged deny cannot stop sending"
    assert lead.pre_send_decision == "error_allow"
    assert "signature" in lead.pre_send_check["error"]


# ── Scope and data ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_follow_ups_are_not_asked_by_default(session, monkeypatch):
    api = _mock(monkeypatch, _Hook())
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox, sequence_index=1)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert api.calls == 0
    assert lead.pre_send_decision is None
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_scope_all_asks_for_follow_ups_too(session, monkeypatch):
    monkeypatch.setattr(settings, "pre_send_hook_scope", "all")
    api = _mock(monkeypatch, _Hook(_reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox, sequence_index=1)

    await _run_slot(session, monkeypatch, slot.id)

    assert api.calls == 1
    assert api.body()["sequence_index"] == 1


@pytest.mark.asyncio
async def test_contact_fields_add_the_name_and_custom_data(session, monkeypatch):
    monkeypatch.setattr(settings, "pre_send_hook_fields", "contact")
    api = _mock(monkeypatch, _Hook(_reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(
        session, inbox, custom_data={"company": "Acme Ltd", "country": "GB", "score": 7, "nested": {"x": 1}},
    )

    await _run_slot(session, monkeypatch, slot.id)

    body = api.body()
    assert body["lead"]["name"] == "Jane Doe"
    assert body["lead"]["custom_data"] == {"company": "Acme Ltd", "country": "GB", "score": 7}


@pytest.mark.asyncio
async def test_test_mode_never_calls_the_hook(session, monkeypatch):
    monkeypatch.setattr(settings, "test_mode", True)
    api = _mock(monkeypatch, _Hook())
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert api.calls == 0
    assert lead.pre_send_decision is None


# ── Pure helpers ─────────────────────────────────────────────────────────────

def test_timeout_is_clamped_between_one_and_ten_seconds():
    assert hook._clamp_timeout("") == 3.0
    assert hook._clamp_timeout("0.1") == 1.0
    assert hook._clamp_timeout("60") == 10.0
    assert hook._clamp_timeout("4.5") == 4.5


def test_hold_retry_is_capped_at_seven_days():
    assert hook._retry_after(None) is None
    assert hook._retry_after(-5) is None
    assert hook._retry_after(90) == timedelta(seconds=90)
    assert hook._retry_after(10 ** 9) == timedelta(days=7)
