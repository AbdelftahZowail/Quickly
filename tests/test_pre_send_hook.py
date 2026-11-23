"""Pre-send decision hook (app/pre_send_hook.py) in the per-slot send job.

With ``PRE_SEND_HOOK_URL`` set, the first email of a sequence to a lead is
preceded by one signed POST to that URL, which answers allow, hold or deny.
All HTTP goes through httpx.MockTransport: no real calls are made.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

import app.jobs as jobs_mod
from app import pre_send_hook as hook
from app import time as time_provider
from app.app_settings import (
    PRE_SEND_HOOK_FIELDS_KEY,
    PRE_SEND_HOOK_ON_ERROR_KEY,
    PRE_SEND_HOOK_SCOPE_KEY,
    PRE_SEND_HOOK_URL_KEY,
    get_setting,
    put_setting,
)
from app.routers import settings as settings_routes
from app.models import EmailLog, Lead, QueueSlot, SmtpAccount
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
    """Hook on at HOOK_URL with a secret, fail open; test mode off so sends run."""
    monkeypatch.setattr(settings, "pre_send_hook_url", HOOK_URL)
    monkeypatch.setattr(settings, "pre_send_hook_secret", SECRET)
    monkeypatch.setattr(settings, "pre_send_hook_on_error", "")
    monkeypatch.setattr(settings, "pre_send_hook_scope", "")
    monkeypatch.setattr(settings, "pre_send_hook_fields", "")
    monkeypatch.setattr(settings, "test_mode", False)
    yield
    monkeypatch.setattr(hook, "_transport", None)


class _Hook:
    """Records every request and answers from a queue of responses."""

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


def _reply(decision: str, **extra) -> httpx.Response:
    return httpx.Response(200, json={"decision": decision, **extra})


def _mock(monkeypatch, api: _Hook) -> _Hook:
    monkeypatch.setattr(hook, "_transport", httpx.MockTransport(api))
    return api


async def _make_smtp_inbox(session, email: str = "relay@example.com"):
    inbox = await make_inbox(session, email=email, provider="smtp")
    session.add(SmtpAccount(
        inbox_id=inbox.id, smtp_host="smtp.example.com", smtp_port=587,
        smtp_username="user", smtp_password="pass", smtp_use_tls=True,
    ))
    await session.flush()
    return inbox


async def _make_due_slot(session, inbox, *, lead_email="jane@acme.co.uk", sequence_index=0):
    campaign = await make_campaign(
        session, sending_days=[0, 1, 2, 3, 4, 5, 6],
        sending_hours_start="00:00", sending_hours_end="23:59",
    )
    await make_sequence(session, campaign.id, position=0)
    await make_sequence(session, campaign.id, position=1, subject="", wait_days_after_previous=2)
    lead = await make_lead(session, email=lead_email, name="Jane Doe")
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


# ── Configuration ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_config_defaults_and_url_guard(session, monkeypatch):
    assert hook.url_allowed("https://hooks.example.test/x")
    assert hook.url_allowed("http://localhost:9000/hook")
    assert hook.url_allowed("http://127.0.0.1/hook")
    assert not hook.url_allowed("http://hooks.example.test/x")
    assert not hook.url_allowed("ftp://hooks.example.test/x")

    monkeypatch.setattr(settings, "pre_send_hook_url", "")
    config = await hook.load_config(session)
    assert config.enabled is False
    assert config.on_error == hook.ON_ERROR_ALLOW

    monkeypatch.setattr(settings, "pre_send_hook_url", "http://hooks.example.test/x")
    assert (await hook.load_config(session)).enabled is False

    monkeypatch.setattr(settings, "pre_send_hook_url", HOOK_URL)
    assert (await hook.load_config(session)).enabled is True


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


# ── Request ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_minimal_payload_and_allow(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(_reply("allow", reason="Known customer", reference="crm-77")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, campaign = await _make_due_slot(session, inbox)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1 and sends[0]["to_email"] == "jane@acme.co.uk"
    assert await _count(session, EmailLog, inbox_id=inbox.id) == 1
    assert cl.sending_paused is False
    assert lead.pre_send_decision == "allow"
    assert lead.pre_send_check["reason"] == "Known customer"
    assert lead.pre_send_check["reference"] == "crm-77"
    assert lead.pre_send_check["host"] == "hooks.example.test"

    assert api.calls == 1
    assert api.body() == {
        "event": "pre_send",
        "version": 1,
        "timestamp": api.body()["timestamp"],
        "lead": {"id": lead.id, "email": "jane@acme.co.uk"},
        "campaign": {"id": campaign.id, "name": campaign.name},
    }


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


@pytest.mark.asyncio
async def test_scope_all_asks_for_every_email(session, monkeypatch):
    await put_setting(session, PRE_SEND_HOOK_SCOPE_KEY, "all")
    api = _mock(monkeypatch, _Hook(_reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox, sequence_index=1)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1
    assert api.calls == 1
    assert lead.pre_send_decision == "allow"


@pytest.mark.asyncio
async def test_contact_fields_are_sent_when_configured(session, monkeypatch):
    await put_setting(session, PRE_SEND_HOOK_FIELDS_KEY, "contact")
    api = _mock(monkeypatch, _Hook(_reply("allow")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, campaign = await _make_due_slot(session, inbox)
    lead.company = "Acme Ltd"
    lead.custom_data = {"Source": "webinar", "Score": 7}
    await session.flush()

    await _run_slot(session, monkeypatch, slot.id)

    body = api.body()
    assert body["lead"] == {
        "id": lead.id,
        "email": "jane@acme.co.uk",
        "name": "Jane Doe",
        "company": "Acme Ltd",
        "custom_data": {"Source": "webinar", "Score": "7"},
    }
    assert body["campaign"] == {"id": campaign.id, "name": campaign.name}


# ── Settings API ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_settings_endpoint_round_trip(session, monkeypatch):
    monkeypatch.setattr(settings, "pre_send_hook_url", "")
    monkeypatch.setattr(settings, "pre_send_hook_secret", "")

    result = await settings_routes.set_pre_send_hook(
        settings_routes._PreSendHookPayload(
            url=HOOK_URL,
            secret=SECRET,
            on_error="hold",
            scope="all",
            fields="contact",
        ),
        db=session,
        _admin=None,
    )
    assert result == {
        "url": HOOK_URL,
        "secret_set": True,
        "on_error": "hold",
        "scope": "all",
        "fields": "contact",
    }
    assert await get_setting(session, PRE_SEND_HOOK_URL_KEY) == HOOK_URL
    assert await get_setting(session, PRE_SEND_HOOK_SCOPE_KEY) == "all"
    assert await get_setting(session, PRE_SEND_HOOK_FIELDS_KEY) == "contact"

    # Saving without a secret keeps the stored one; clear_secret removes it.
    await settings_routes.set_pre_send_hook(
        settings_routes._PreSendHookPayload(url=HOOK_URL),
        db=session,
        _admin=None,
    )
    config = await hook.load_config(session)
    assert config.secret == SECRET
    assert (config.scope, config.fields) == ("first", "minimal")

    removed = await settings_routes.set_pre_send_hook(
        settings_routes._PreSendHookPayload(url=HOOK_URL, clear_secret=True),
        db=session,
        _admin=None,
    )
    assert removed["secret_set"] is False


@pytest.mark.asyncio
async def test_settings_endpoint_rejects_bad_input(session):
    from fastapi import HTTPException

    for kwargs in (
        {"url": "http://hooks.example.test/x"},
        {"url": "https://hooks.example.test/x", "on_error": "ignore"},
        {"url": "https://hooks.example.test/x", "scope": "some"},
        {"url": "https://hooks.example.test/x", "fields": "everything"},
        {"url": "https://169.254.169.254/latest"},
    ):
        with pytest.raises(HTTPException) as exc:
            await settings_routes.set_pre_send_hook(
                settings_routes._PreSendHookPayload(**kwargs), db=session, _admin=None
            )
        assert exc.value.status_code == 400


# ── Response parsing and cache ───────────────────────────────────────────────

def test_parse_response_allow_hold_deny_and_bad_status():
    allow = hook.parse_response(_reply("allow", reason="Fine"))
    assert (allow.decision, allow.ok, allow.reason) == ("allow", True, "Fine")

    hold = hook.parse_response(_reply("hold", retry_after_seconds=1800))
    assert hold.decision == "hold" and hold.retry_after == timedelta(seconds=1800)

    deny = hook.parse_response(_reply("deny", reference="rcpt_9"))
    assert (deny.decision, deny.reference) == ("deny", "rcpt_9")

    assert hook.parse_response(httpx.Response(500, text="boom")).decision == "error"
    assert hook.parse_response(httpx.Response(200, text="not json")).decision == "error"
    assert hook.parse_response(_reply("maybe")).decision == "error"


def test_cache_allow_ttl_and_hold_retry():
    config = hook.HookConfig(
        url=HOOK_URL, secret="", on_error="allow", scope="first", fields="minimal"
    )
    now = datetime(2026, 1, 1, 12, 0, 0)

    def lead_with(checked_at: datetime, decision: str = "allow", retry_at: datetime | None = None) -> Lead:
        return Lead(pre_send_check={
            "decision": decision,
            "checked_at": checked_at.isoformat() + "Z",
            "retry_at": retry_at.isoformat() + "Z" if retry_at else None,
            "host": config.host,
        })

    fresh = hook.cached_decision(lead_with(now - timedelta(days=1)), config, now)
    assert fresh is not None and fresh.decision == "allow" and fresh.cached

    assert hook.cached_decision(lead_with(now - timedelta(days=31)), config, now) is None
    assert hook.cached_decision(lead_with(now, decision="deny", retry_at=None), config, now).decision == "deny"

    assert hook.cached_decision(lead_with(now, "hold", now + timedelta(hours=1)), config, now) is not None
    assert hook.cached_decision(lead_with(now, "hold", now - timedelta(hours=1)), config, now) is None

    other_host = Lead(pre_send_check={"decision": "allow", "checked_at": now.isoformat() + "Z", "host": "other.test"})
    assert hook.cached_decision(other_host, config, now) is None


# ── Gate and decisions in the send job ──────────────────────────────────────

@pytest.mark.asyncio
async def test_gate_asks_only_for_the_first_email(session, monkeypatch):
    api = _mock(monkeypatch, _Hook())
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox, sequence_index=1)

    sends, _ = await _run_slot(session, monkeypatch, slot.id)

    assert api.calls == 0
    assert lead.pre_send_decision is None
    assert len(sends) == 1


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
    held = [data for event, data in events if event == "lead.status_changed"]
    assert len(held) == 1
    assert held[0]["sending_paused"] is True
    assert held[0]["pre_send_decision"] == "deny"
    assert held[0]["pre_send_reference"] == "rcpt_9"
    assert held[0]["reason"] == "Pre-send hook: deny: Consent needed first (ref rcpt_9)"


@pytest.mark.asyncio
async def test_hold_keeps_the_slot_and_does_not_ask_again_until_retry(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(_reply("hold", retry_after_seconds=1800)))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead, _ = await _make_due_slot(session, inbox)

    sends, events = await _run_slot(session, monkeypatch, slot.id)

    assert sends == []
    assert cl.sending_paused is False, "a hold is not a pause"
    assert await _count(session, QueueSlot, campaign_lead_id=cl.id) == 1, "the slot stays for a later scan"
    assert lead.pre_send_decision == "hold"
    assert lead.pre_send_check["retry_at"]
    assert events == []

    sends, _ = await _run_slot(session, monkeypatch, slot.id)
    assert sends == [] and api.calls == 1


# ── Failure handling ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_hook_that_cannot_answer_fails_open_by_default(session, monkeypatch):
    api = _mock(monkeypatch, _Hook(httpx.Response(500, text="boom")))
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
