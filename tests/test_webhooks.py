"""Unit tests for app.webhooks — fire_webhook_event dispatch and _post_webhook delivery.

Uses the `session` fixture from conftest for DB-backed tests and
monkeypatches / unittest.mock for HTTP calls so no real network is needed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Webhook
from app.webhooks import fire_webhook_event, _post_webhook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_webhook(
    session,
    url: str = "https://example.com/hook",
    events: list[str] | None = None,
    active: bool = True,
    secret: str = "",
) -> Webhook:
    wh = Webhook(
        url=url,
        events=events if events is not None else [],
        active=active,
        secret=secret,
    )
    session.add(wh)
    await session.flush()
    return wh


def _make_mock_client(status_code: int = 200, raise_exc: Exception | None = None):
    """Return a mock httpx.AsyncClient context manager."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code

    mock_client = AsyncMock()
    if raise_exc:
        mock_client.post = AsyncMock(side_effect=raise_exc)
    else:
        mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# fire_webhook_event — dispatch logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_webhooks_returns_without_error(session):
    """With no webhooks in the DB fire_webhook_event must complete silently."""
    # Should not raise
    await fire_webhook_event(session, "email.sent", {"x": 1})


@pytest.mark.asyncio
async def test_fires_active_webhook_with_matching_event(session, monkeypatch):
    wh = await _make_webhook(session, events=["email.sent"])

    calls: list[tuple[int, str]] = []

    async def fake_post(webhook, event_type, data):
        calls.append((webhook.id, event_type))
        return True

    monkeypatch.setattr("app.webhooks._post_webhook", fake_post)
    await fire_webhook_event(session, "email.sent", {})

    assert calls == [(wh.id, "email.sent")]


@pytest.mark.asyncio
async def test_skips_inactive_webhook(session, monkeypatch):
    await _make_webhook(session, events=["email.sent"], active=False)

    calls: list = []

    async def fake_post(webhook, event_type, data):
        calls.append(webhook.id)
        return True

    monkeypatch.setattr("app.webhooks._post_webhook", fake_post)
    await fire_webhook_event(session, "email.sent", {})

    assert calls == []


@pytest.mark.asyncio
async def test_skips_webhook_subscribed_to_different_event(session, monkeypatch):
    await _make_webhook(session, events=["email.opened"])  # only opened

    calls: list = []

    async def fake_post(webhook, event_type, data):
        calls.append(event_type)
        return True

    monkeypatch.setattr("app.webhooks._post_webhook", fake_post)
    # fire a different event
    await fire_webhook_event(session, "email.sent", {})

    assert calls == []


@pytest.mark.asyncio
async def test_empty_events_list_subscribes_to_all(session, monkeypatch):
    """A webhook with events=[] must receive every event type."""
    wh = await _make_webhook(session, events=[])  # subscribe to everything

    calls: list[str] = []

    async def fake_post(webhook, event_type, data):
        calls.append(event_type)
        return True

    monkeypatch.setattr("app.webhooks._post_webhook", fake_post)
    await fire_webhook_event(session, "email.sent", {})
    await fire_webhook_event(session, "lead.replied", {})

    assert "email.sent" in calls
    assert "lead.replied" in calls


@pytest.mark.asyncio
async def test_multiple_webhooks_each_receives_event(session, monkeypatch):
    wh1 = await _make_webhook(session, url="https://a.com/hook", events=["email.sent"])
    wh2 = await _make_webhook(session, url="https://b.com/hook", events=["email.sent"])

    called_ids: list[int] = []

    async def fake_post(webhook, event_type, data):
        called_ids.append(webhook.id)
        return True

    monkeypatch.setattr("app.webhooks._post_webhook", fake_post)
    await fire_webhook_event(session, "email.sent", {})

    assert set(called_ids) == {wh1.id, wh2.id}


@pytest.mark.asyncio
async def test_only_matching_webhook_fires_when_mixed(session, monkeypatch):
    wh_match = await _make_webhook(session, events=["email.sent"])
    wh_other = await _make_webhook(session, events=["email.opened"])

    called_ids: list[int] = []

    async def fake_post(webhook, event_type, data):
        called_ids.append(webhook.id)
        return True

    monkeypatch.setattr("app.webhooks._post_webhook", fake_post)
    await fire_webhook_event(session, "email.sent", {})

    assert called_ids == [wh_match.id]


# ---------------------------------------------------------------------------
# _post_webhook — HTTP delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_webhook_returns_true_on_2xx():
    wh = Webhook(id=1, url="https://example.com/hook", secret="", events=[], active=True)
    mock_client = _make_mock_client(status_code=200)

    with patch("app.webhooks.httpx.AsyncClient", return_value=mock_client):
        result = await _post_webhook(wh, "email.sent", {"x": 1})

    assert result is True


@pytest.mark.asyncio
async def test_post_webhook_returns_false_on_4xx():
    wh = Webhook(id=1, url="https://example.com/hook", secret="", events=[], active=True)
    mock_client = _make_mock_client(status_code=400)

    with patch("app.webhooks.httpx.AsyncClient", return_value=mock_client):
        result = await _post_webhook(wh, "email.sent", {})

    assert result is False


@pytest.mark.asyncio
async def test_post_webhook_returns_false_on_5xx():
    wh = Webhook(id=1, url="https://example.com/hook", secret="", events=[], active=True)
    mock_client = _make_mock_client(status_code=500)

    with patch("app.webhooks.httpx.AsyncClient", return_value=mock_client):
        result = await _post_webhook(wh, "email.sent", {})

    assert result is False


@pytest.mark.asyncio
async def test_post_webhook_handles_network_error_gracefully():
    """A network exception must be swallowed and return False."""
    import httpx

    wh = Webhook(id=1, url="https://bad.invalid/hook", secret="", events=[], active=True)
    mock_client = _make_mock_client(raise_exc=httpx.ConnectError("unreachable"))

    with patch("app.webhooks.httpx.AsyncClient", return_value=mock_client):
        result = await _post_webhook(wh, "email.sent", {})

    assert result is False


@pytest.mark.asyncio
async def test_post_webhook_sends_bearer_token():
    """When secret is set the Authorization header must be 'Bearer <secret>'."""
    wh = Webhook(id=1, url="https://example.com/hook", secret="my-secret", events=[], active=True)
    mock_client = _make_mock_client(status_code=200)

    with patch("app.webhooks.httpx.AsyncClient", return_value=mock_client):
        await _post_webhook(wh, "email.sent", {})

    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs.args[1] if call_kwargs.args else {}
    # headers are passed as kwarg
    headers = mock_client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer my-secret"


@pytest.mark.asyncio
async def test_post_webhook_no_auth_header_when_no_secret():
    """When secret is empty there must be no Authorization header."""
    wh = Webhook(id=1, url="https://example.com/hook", secret="", events=[], active=True)
    mock_client = _make_mock_client(status_code=200)

    with patch("app.webhooks.httpx.AsyncClient", return_value=mock_client):
        await _post_webhook(wh, "email.sent", {})

    headers = mock_client.post.call_args.kwargs["headers"]
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_post_webhook_payload_contains_event_and_data():
    """The JSON payload must include 'event', 'data', and 'timestamp' keys."""
    wh = Webhook(id=1, url="https://example.com/hook", secret="", events=[], active=True)
    mock_client = _make_mock_client(status_code=200)

    with patch("app.webhooks.httpx.AsyncClient", return_value=mock_client):
        await _post_webhook(wh, "email.sent", {"lead_id": 99})

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["event"] == "email.sent"
    assert payload["data"]["lead_id"] == 99
    assert "timestamp" in payload
