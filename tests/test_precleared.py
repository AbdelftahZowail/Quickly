"""Precleared cold-email check in the per-slot send job (app/precleared.py).

The first email of a campaign to a lead is checked with Precleared before it
is sent; follow-ups, replies and test-mode sends are not.  ``hold`` mode (the
default) pauses a lead that is not ``ok_to_email`` and keeps the verdict,
reason, next step and receipt id on the lead; a check that could not run
keeps the slot for a later scan.  All HTTP goes through httpx.MockTransport:
no real calls are made.
"""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

import app.jobs as jobs_mod
from app import precleared
from app import time as time_provider
from app.app_settings import (
    PRECLEARED_API_KEY_KEY,
    PRECLEARED_LAST_ERROR,
    PRECLEARED_MODE_KEY,
    PRECLEARED_SENDER_COUNTRY_KEY,
    get_setting,
    put_setting,
)
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

CHECK_URL = "https://precleared.dev/api/v1/check"
KEYS_URL = "https://precleared.dev/api/v1/keys"
FREE_KEY = "pc_live_testkey_0123456789abcdef"


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.fixture(autouse=True)
def _precleared_defaults(monkeypatch, _precleared_off):
    """Default settings (hold, no key, GB), no cooldown, no key requested.

    Depends on conftest's ``_precleared_off`` so this runs after it and turns
    the check back on for this file."""
    precleared.reset_state()
    monkeypatch.setattr(settings, "precleared_mode", "")
    monkeypatch.setattr(settings, "precleared_api_key", "")
    monkeypatch.setattr(settings, "precleared_sender_country", "")
    monkeypatch.setattr(settings, "test_mode", False)
    yield
    precleared.reset_state()


# ── Mock Precleared ──────────────────────────────────────────────────────────

def _ok_reply(receipt: str = "rcpt_ok_1") -> httpx.Response:
    return httpx.Response(200, json={
        "verdict": "ok_to_email",
        "confidence": "high",
        "reasons": [{"rule_key": "uk_pecr_corporate", "text": "Named contact at a limited company",
                     "source_title": "PECR reg. 22", "source_url": "https://example.test/pecr"}],
        "next_step": "",
        "footer_text": "Say who you are, give a postal address and a way to opt out.",
        "facts": {"recipient_country": "GB"},
        "receipt": {"id": receipt},
        "usage": {"remaining_today": 9},
    })


def _hold_reply(verdict: str, receipt: str = "rcpt_hold_1") -> httpx.Response:
    return httpx.Response(200, json={
        "verdict": verdict,
        "confidence": "high",
        "reasons": [{"rule_key": "uk_pecr_sole_trader", "text": "Sole trader: consent needed under PECR",
                     "source_title": "PECR reg. 22", "source_url": "https://example.test/pecr"}],
        "next_step": "Ask for consent first or use another channel.",
        "facts": {"recipient_country": "GB"},
        "receipt": {"id": receipt},
    })


class _Precleared:
    """Scripted Precleared API: records every request, answers from a queue
    (or a callable) and can raise to simulate a dead network."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"unexpected Precleared request: {request.method} {request.url}")
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


def _mock(monkeypatch, api: _Precleared) -> _Precleared:
    monkeypatch.setattr(precleared, "_transport", httpx.MockTransport(api))
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
    return slot, cl, lead


async def _run_slot(session, monkeypatch, slot_id: int):
    """Run send_slot_job with the transport and webhooks faked; returns
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


# ── Verdicts ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ok_to_email_sends_and_keeps_receipt(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(_ok_reply("rcpt_42")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(
        session, inbox, custom_data={"company": "Acme Ltd", "country": "gb", "title": "CEO"},
    )

    sends, _events = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1 and sends[0]["to_email"] == "jane@acme.co.uk"
    assert await _count(session, EmailLog, inbox_id=inbox.id) == 1
    assert cl.sending_paused is False
    assert lead.precleared_verdict == "ok_to_email"
    assert lead.precleared_check["receipt_id"] == "rcpt_42"
    assert lead.precleared_check["checked_at"]

    # One call, tagged and with the lead's facts; the address source is never
    # assumed to be "published".
    assert api.calls == 1
    request = api.requests[0]
    assert str(request.url) == f"{CHECK_URL}?src=quickly"
    assert request.headers["user-agent"] == "quickly (+https://github.com/AbdelftahZowail/Quickly)"
    assert "authorization" not in request.headers
    assert api.body() == {
        "recipient_email": "jane@acme.co.uk",
        "sender_country": "GB",
        "address_source": "unknown",
        "existing_customer": False,
        "company_name": "Acme Ltd",
        "recipient_name": "Jane Doe",
        "recipient_country": "GB",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["consent_needed", "blocked"])
async def test_definite_hold_pauses_lead_with_reason_and_receipt(session, monkeypatch, verdict):
    api = _mock(monkeypatch, _Precleared(_hold_reply(verdict, "rcpt_hold_7")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox)

    sends, events = await _run_slot(session, monkeypatch, slot.id)

    assert sends == [], "a held lead must not be emailed"
    assert await _count(session, EmailLog, inbox_id=inbox.id) == 0
    # Enrolment paused, its queue slots dropped, the verdict kept on the lead.
    assert cl.sending_paused is True
    assert await _count(session, QueueSlot, campaign_lead_id=cl.id) == 0
    assert lead.precleared_verdict == verdict
    check = lead.precleared_check
    assert check["reason"] == "Sole trader: consent needed under PECR (PECR reg. 22)"
    assert check["next_step"] == "Ask for consent first or use another channel."
    assert check["receipt_id"] == "rcpt_hold_7"
    # Surfaced through the usual status event, with the reason.
    held = [data for event, data in events if event == "lead.status_changed"]
    assert len(held) == 1
    assert held[0]["lead_email"] == "jane@acme.co.uk"
    assert held[0]["sending_paused"] is True
    assert held[0]["precleared_verdict"] == verdict
    assert held[0]["precleared_receipt_id"] == "rcpt_hold_7"
    assert held[0]["reason"] == (
        f"Precleared: {verdict}: Sole trader: consent needed under PECR (PECR reg. 22). "
        "Next step: Ask for consent first or use another channel."
    )
    assert api.calls == 1


@pytest.mark.asyncio
async def test_unknown_holds_lead_and_is_never_cached(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(_hold_reply("unknown", "rcpt_u1"), _ok_reply("rcpt_u2")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox)

    sends, _events = await _run_slot(session, monkeypatch, slot.id)

    assert sends == []
    assert cl.sending_paused is True
    assert lead.precleared_verdict == "unknown"
    assert lead.precleared_check["receipt_id"] == "rcpt_u1"

    # Asking again (the operator resumed the lead) makes a fresh call.
    config = await precleared.load_config(session)
    result = await precleared.check_lead(session, lead, config)
    assert result.ok and not result.cached
    assert api.calls == 2
    assert lead.precleared_verdict == "ok_to_email"


# ── The check could not run ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_network_error_holds_in_hold_mode_and_keeps_slot(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(httpx.ConnectError("no route to host")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox)

    sends, events = await _run_slot(session, monkeypatch, slot.id)

    assert sends == []
    assert await _count(session, EmailLog, inbox_id=inbox.id) == 0
    # The lead is not blamed: not paused, no verdict, slot kept for retry.
    assert cl.sending_paused is False
    assert lead.precleared_verdict is None
    assert await _count(session, QueueSlot, campaign_lead_id=cl.id) == 1
    # The operator is told once, through the feature.error event.
    errors = [data for event, data in events if event == "feature.error"]
    assert len(errors) == 1
    assert errors[0]["feature"] == "precleared"
    assert "could not be reached" in errors[0]["error"]
    assert "could not be reached" in (await get_setting(session, PRECLEARED_LAST_ERROR))
    assert api.calls == 1


@pytest.mark.asyncio
async def test_network_error_sends_in_warn_mode(session, monkeypatch):
    monkeypatch.setattr(settings, "precleared_mode", "warn")
    api = _mock(monkeypatch, _Precleared(httpx.ConnectError("no route to host")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox)

    sends, _events = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1
    assert cl.sending_paused is False
    assert api.calls == 1


@pytest.mark.asyncio
async def test_warn_mode_records_a_hold_verdict_but_sends(session, monkeypatch):
    await put_setting(session, PRECLEARED_MODE_KEY, "warn")
    _mock(monkeypatch, _Precleared(_hold_reply("consent_needed", "rcpt_w1")))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox)

    sends, events = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1
    assert cl.sending_paused is False
    assert lead.precleared_verdict == "consent_needed"
    assert lead.precleared_check["receipt_id"] == "rcpt_w1"
    assert not [e for e, _ in events if e == "lead.status_changed"]


@pytest.mark.asyncio
async def test_off_mode_makes_no_call(session, monkeypatch):
    monkeypatch.setattr(settings, "precleared_mode", "off")
    api = _mock(monkeypatch, _Precleared())  # any request would fail the test
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox)

    sends, _events = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1
    assert api.calls == 0
    assert lead.precleared_verdict is None


@pytest.mark.asyncio
async def test_follow_up_is_not_checked(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared())
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox, sequence_index=1)

    sends, _events = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1
    assert api.calls == 0


@pytest.mark.asyncio
async def test_test_mode_sends_are_not_checked(session, monkeypatch):
    monkeypatch.setattr(settings, "test_mode", True)
    api = _mock(monkeypatch, _Precleared())
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox)

    sends, _events = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1
    assert api.calls == 0


@pytest.mark.asyncio
async def test_cooldown_skips_fresh_calls_but_keeps_using_stored_verdicts(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(_ok_reply("rcpt_c1"), httpx.ReadTimeout("slow")))
    config = await precleared.load_config(session)
    cleared = await make_lead(session, email="cleared@acme.co.uk")
    waiting = await make_lead(session, email="waiting@acme.co.uk")
    another = await make_lead(session, email="another@acme.co.uk")

    assert (await precleared.check_lead(session, cleared, config)).ok
    timed_out = await precleared.check_lead(session, waiting, config)
    assert timed_out.unavailable and "too long" in timed_out.error
    assert api.calls == 2

    # Inside the cooldown: no call for a new address, same error back...
    again = await precleared.check_lead(session, another, config)
    assert again.unavailable and again.error == timed_out.error
    assert api.calls == 2
    # ...while a stored verdict still answers.
    cached = await precleared.check_lead(session, cleared, config)
    assert cached.ok and cached.cached


# ── Allowance, keys and rate limits ──────────────────────────────────────────

def _daily_limit_reply(with_create_key: bool = True) -> httpx.Response:
    body = {
        "error": "daily_limit",
        "message": "Daily limit reached",
        "operator_message": "Precleared's 10 free checks a day are used up. Get a free key at https://precleared.dev/get-key?ref=limit",
        "get_key_url": "https://precleared.dev/get-key?ref=limit",
        "limit": 10, "resets_at": "2026-11-01T00:00:00Z", "retry_after": 3600,
    }
    if with_create_key:
        body["create_key"] = {"method": "POST", "url": KEYS_URL, "body": {"email": "<your operator's email>"}}
    return httpx.Response(429, json=body)


@pytest.mark.asyncio
async def test_keyless_daily_limit_creates_a_free_key_once_and_retries(session, monkeypatch, caplog):
    def keys_endpoint(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == KEYS_URL and request.method == "POST"
        assert json.loads(request.content) == {"email": "relay@example.com"}
        return httpx.Response(201, json={"key": FREE_KEY, "key_prefix": "pc_live_test",
                                         "free_per_day": 25, "terms_url": "https://precleared.dev/terms",
                                         "message": "Save this key; it is shown once."})

    def keyed_check(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {FREE_KEY}"
        return _ok_reply("rcpt_keyed")

    api = _mock(monkeypatch, _Precleared(_daily_limit_reply(), keys_endpoint, keyed_check))
    inbox = await _make_smtp_inbox(session)
    slot, cl, lead = await _make_due_slot(session, inbox)

    with caplog.at_level("INFO"):
        sends, events = await _run_slot(session, monkeypatch, slot.id)

    assert len(sends) == 1
    assert api.calls == 3
    assert lead.precleared_check["receipt_id"] == "rcpt_keyed"
    # The key is kept in the settings table and never logged.
    assert await get_setting(session, PRECLEARED_API_KEY_KEY) == FREE_KEY
    assert FREE_KEY not in caplog.text
    assert not [e for e, _ in events if e == "feature.error"]

    # Later checks use the stored key; no second key request.
    api.replies.append(keyed_check)
    other = await make_lead(session, email="second@acme.co.uk")
    result = await precleared.check_lead(session, other, await precleared.load_config(session))
    assert result.ok and api.calls == 4


@pytest.mark.asyncio
async def test_keyless_daily_limit_without_operator_email_holds_with_message(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(_daily_limit_reply()))
    config = await precleared.load_config(session)
    lead = await make_lead(session, email="jane@acme.co.uk")

    result = await precleared.check_lead(session, lead, config)  # no operator email known

    assert result.unavailable
    assert result.error.startswith("Precleared's 10 free checks a day are used up")
    assert "https://precleared.dev/get-key" in result.error
    assert api.calls == 1
    assert await get_setting(session, PRECLEARED_API_KEY_KEY) is None


@pytest.mark.asyncio
async def test_key_request_is_made_once_per_process(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(
        _daily_limit_reply(),
        httpx.Response(429, json={"error": "too_many_keys", "message": "Key limit reached for this network"}),
        _daily_limit_reply(),
    ))
    config = await precleared.load_config(session)
    lead = await make_lead(session, email="jane@acme.co.uk")

    first = await precleared.check_lead(session, lead, config, operator_email="relay@example.com")
    assert first.unavailable and "Key limit reached" in first.error
    assert api.calls == 2

    precleared.reset_state()  # lift the cooldown but not the "asked once" flag
    precleared._key_requested = True
    second = await precleared.check_lead(session, lead, config, operator_email="relay@example.com")
    assert second.unavailable and "used up" in second.error
    assert api.calls == 3, "no second POST to the keys endpoint"


@pytest.mark.asyncio
async def test_rate_limited_waits_retry_after_and_retries_once(session, monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(precleared, "_sleep", fake_sleep)
    api = _mock(monkeypatch, _Precleared(
        httpx.Response(429, json={"error": "rate_limited", "retry_after": 2}),
        _ok_reply("rcpt_r1"),
    ))
    config = await precleared.load_config(session)
    lead = await make_lead(session, email="jane@acme.co.uk")

    result = await precleared.check_lead(session, lead, config)

    assert result.ok and result.receipt_id == "rcpt_r1"
    assert waits == [2.0]
    assert api.calls == 2


@pytest.mark.asyncio
async def test_key_out_of_checks_holds_with_operator_message(session, monkeypatch):
    monkeypatch.setattr(settings, "precleared_api_key", "pc_live_secret_key_value_1234")
    api = _mock(monkeypatch, _Precleared(httpx.Response(402, json={
        "error": "payment_required",
        "operator_message": "Today's free checks on this key are used and it has no credits left.",
        "buy_url": "https://precleared.dev/buy?k=abc",
    })))
    config = await precleared.load_config(session)
    lead = await make_lead(session, email="jane@acme.co.uk")

    result = await precleared.check_lead(session, lead, config)

    assert result.unavailable
    assert result.error == (
        "Today's free checks on this key are used and it has no credits left. https://precleared.dev/buy?k=abc"
    )
    assert api.requests[0].headers["authorization"] == "Bearer pc_live_secret_key_value_1234"
    assert "pc_live_secret_key_value_1234" not in result.error


@pytest.mark.asyncio
async def test_rejected_key_holds_and_points_at_the_key(session, monkeypatch):
    monkeypatch.setattr(settings, "precleared_api_key", "pc_live_wrong")
    _mock(monkeypatch, _Precleared(httpx.Response(401, json={"error": "invalid_key"})))
    config = await precleared.load_config(session)
    lead = await make_lead(session, email="jane@acme.co.uk")

    result = await precleared.check_lead(session, lead, config)

    assert result.unavailable
    assert "PRECLEARED_API_KEY" in result.error and "precleared.dev/get-key" in result.error


@pytest.mark.asyncio
async def test_bad_field_holds_only_that_lead(session, monkeypatch):
    """A 400 is about the lead's data, not Precleared: no cooldown for others."""
    api = _mock(monkeypatch, _Precleared(
        httpx.Response(400, json={"error": "invalid_request", "message": "recipient_country must be a country code"}),
        _ok_reply("rcpt_next"),
    ))
    config = await precleared.load_config(session)
    odd = await make_lead(session, email="odd@acme.co.uk")
    odd.custom_data = {"country": "ZZ"}

    result = await precleared.check_lead(session, odd, config)

    assert result.verdict == "unknown" and not result.ok
    assert result.reason == "Precleared rejected the request: recipient_country must be a country code"
    assert "resume the lead" in result.next_step
    assert odd.precleared_verdict == "unknown"
    # The next lead is still checked.
    other = await make_lead(session, email="fine@acme.co.uk")
    assert (await precleared.check_lead(session, other, config)).ok
    assert api.calls == 2


@pytest.mark.asyncio
async def test_uk_country_column_is_sent_as_gb(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(_ok_reply()))
    config = await precleared.load_config(session)
    lead = await make_lead(session, email="jane@acme.co.uk")
    lead.custom_data = {"country": "uk"}

    await precleared.check_lead(session, lead, config)

    assert api.body()["recipient_country"] == "GB"


@pytest.mark.asyncio
async def test_server_error_and_bad_answers_hold(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(
        httpx.Response(503, text="upstream down"),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"verdict": "maybe"}),
    ))
    config = await precleared.load_config(session)
    for expected in ("HTTP 503", "without a known verdict", "without a known verdict"):
        precleared.reset_state()
        lead = await make_lead(session, email=f"{api.calls}@acme.co.uk")
        result = await precleared.check_lead(session, lead, config)
        assert result.unavailable and expected in result.error
        assert lead.precleared_verdict is None


# ── Cache and settings ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_definite_verdict_is_reused_for_30_days(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(_hold_reply("consent_needed", "rcpt_1"), _ok_reply("rcpt_2")))
    config = await precleared.load_config(session)
    lead = await make_lead(session, email="jane@acme.co.uk")

    first = await precleared.check_lead(session, lead, config)
    second = await precleared.check_lead(session, lead, config)
    assert first.verdict == second.verdict == "consent_needed"
    assert second.cached and second.receipt_id == "rcpt_1"
    assert second.reason == first.reason and second.next_step == first.next_step
    assert api.calls == 1

    # Thirty days on, the question is asked again.
    stale = time_provider.utcnow() - timedelta(days=precleared.CACHE_DAYS, minutes=1)
    lead.precleared_check = {**lead.precleared_check, "checked_at": stale.isoformat()}
    third = await precleared.check_lead(session, lead, config)
    assert third.ok and not third.cached
    assert api.calls == 2


@pytest.mark.asyncio
async def test_changed_facts_force_a_fresh_check(session, monkeypatch):
    api = _mock(monkeypatch, _Precleared(_ok_reply("rcpt_1"), _ok_reply("rcpt_2")))
    config = await precleared.load_config(session)
    lead = await make_lead(session, email="jane@acme.co.uk")

    await precleared.check_lead(session, lead, config)
    lead.custom_data = {"company": "Acme Holdings Ltd"}
    result = await precleared.check_lead(session, lead, config)

    assert not result.cached and result.receipt_id == "rcpt_2"
    assert api.calls == 2
    assert api.body(1)["company_name"] == "Acme Holdings Ltd"


@pytest.mark.asyncio
async def test_environment_wins_over_app_settings_rows(session, monkeypatch):
    await put_setting(session, PRECLEARED_MODE_KEY, "warn")
    await put_setting(session, PRECLEARED_API_KEY_KEY, "pc_live_from_db")
    await put_setting(session, PRECLEARED_SENDER_COUNTRY_KEY, "us")

    from_db = await precleared.load_config(session)
    assert (from_db.mode, from_db.api_key, from_db.sender_country) == ("warn", "pc_live_from_db", "US")

    monkeypatch.setattr(settings, "precleared_mode", "OFF")
    monkeypatch.setattr(settings, "precleared_api_key", "pc_live_from_env")
    monkeypatch.setattr(settings, "precleared_sender_country", "ae")
    from_env = await precleared.load_config(session)
    assert (from_env.mode, from_env.api_key, from_env.sender_country) == ("off", "pc_live_from_env", "AE")


@pytest.mark.asyncio
async def test_bad_setting_values_fall_back_to_defaults(session, monkeypatch):
    monkeypatch.setattr(settings, "precleared_mode", "sometimes")
    monkeypatch.setattr(settings, "precleared_sender_country", "FR")

    config = await precleared.load_config(session)

    assert config.mode == "hold"
    assert config.sender_country == "GB"


@pytest.mark.asyncio
async def test_lead_response_carries_the_verdict(session, monkeypatch):
    from app.routers.leads import _lead_query_with_campaigns, _lead_to_response

    _mock(monkeypatch, _Precleared(_hold_reply("consent_needed", "rcpt_api")))
    lead = await make_lead(session, email="jane@acme.co.uk")
    await precleared.check_lead(session, lead, await precleared.load_config(session))
    await session.flush()

    loaded = (await session.execute(_lead_query_with_campaigns().where(Lead.id == lead.id))).scalar_one()
    response = _lead_to_response(loaded, set(), set(), set())

    assert response.precleared_verdict == "consent_needed"
    assert response.precleared_check["receipt_id"] == "rcpt_api"
    assert response.precleared_check["next_step"] == "Ask for consent first or use another channel."
