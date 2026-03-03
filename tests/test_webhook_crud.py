"""Integration tests for the Webhook CRUD API (app.routers.settings).

Router functions are called directly with the `session` fixture so no HTTP
layer or app startup is required.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from app.models import Webhook, WEBHOOK_EVENT_TYPES
from app.schemas import WebhookCreate, WebhookUpdate
from app.routers.settings import (
    list_webhooks,
    create_webhook,
    update_webhook,
    delete_webhook,
    list_webhook_events,
)


# ---------------------------------------------------------------------------
# GET /api/settings/webhooks/events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_webhook_events_returns_all_types():
    result = await list_webhook_events()

    assert "events" in result
    assert set(result["events"]) == set(WEBHOOK_EVENT_TYPES)
    assert len(result["events"]) == len(WEBHOOK_EVENT_TYPES)


# ---------------------------------------------------------------------------
# GET /api/settings/webhooks — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_webhooks_empty(session):
    result = await list_webhooks(db=session)

    assert result == []


@pytest.mark.asyncio
async def test_list_webhooks_returns_existing(session):
    wh = Webhook(url="https://example.com/hook", events=["email.sent"], active=True)
    session.add(wh)
    await session.flush()

    result = await list_webhooks(db=session)

    assert len(result) == 1
    assert result[0].url == "https://example.com/hook"


@pytest.mark.asyncio
async def test_list_webhooks_returns_multiple(session):
    for i in range(3):
        session.add(Webhook(url=f"https://example.com/hook{i}", events=[], active=True))
    await session.flush()

    result = await list_webhooks(db=session)

    assert len(result) == 3


# ---------------------------------------------------------------------------
# POST /api/settings/webhooks — create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_webhook_persists_to_db(session):
    payload = WebhookCreate(
        url="https://hook.example.com/",
        secret="s3cr3t",
        events=["email.sent", "email.opened"],
        active=True,
        description="My webhook",
    )

    wh = await create_webhook(payload=payload, db=session)

    assert wh.id is not None
    assert wh.url == "https://hook.example.com/"
    assert wh.secret == "s3cr3t"
    assert set(wh.events) == {"email.sent", "email.opened"}
    assert wh.active is True
    assert wh.description == "My webhook"


@pytest.mark.asyncio
async def test_create_webhook_trims_url_whitespace(session):
    payload = WebhookCreate(url="  https://example.com/hook  ", events=[])

    wh = await create_webhook(payload=payload, db=session)

    assert wh.url == "https://example.com/hook"


@pytest.mark.asyncio
async def test_create_webhook_invalid_event_type_raises_400(session):
    payload = WebhookCreate(url="https://example.com/hook", events=["not.a.real.event"])

    with pytest.raises(HTTPException) as exc_info:
        await create_webhook(payload=payload, db=session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_webhook_empty_events_allowed(session):
    """An empty events list (= subscribe to all) must be accepted."""
    payload = WebhookCreate(url="https://example.com/hook", events=[])

    wh = await create_webhook(payload=payload, db=session)

    assert wh.events == []


@pytest.mark.asyncio
async def test_create_webhook_all_event_types_valid(session):
    """Subscribing to every known event type must not raise."""
    payload = WebhookCreate(url="https://example.com/hook", events=list(WEBHOOK_EVENT_TYPES))

    wh = await create_webhook(payload=payload, db=session)

    assert set(wh.events) == set(WEBHOOK_EVENT_TYPES)


# ---------------------------------------------------------------------------
# PATCH /api/settings/webhooks/{id} — update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_webhook_changes_url(session):
    wh = Webhook(url="https://old.example.com/hook", events=[], active=True)
    session.add(wh)
    await session.flush()

    updated = await update_webhook(
        webhook_id=wh.id,
        payload=WebhookUpdate(url="https://new.example.com/hook"),
        db=session,
    )

    assert updated.url == "https://new.example.com/hook"


@pytest.mark.asyncio
async def test_update_webhook_changes_active_flag(session):
    wh = Webhook(url="https://example.com/hook", events=[], active=True)
    session.add(wh)
    await session.flush()

    updated = await update_webhook(
        webhook_id=wh.id,
        payload=WebhookUpdate(active=False),
        db=session,
    )

    assert updated.active is False


@pytest.mark.asyncio
async def test_update_webhook_changes_events(session):
    wh = Webhook(url="https://example.com/hook", events=["email.sent"], active=True)
    session.add(wh)
    await session.flush()

    updated = await update_webhook(
        webhook_id=wh.id,
        payload=WebhookUpdate(events=["email.opened", "lead.replied"]),
        db=session,
    )

    assert set(updated.events) == {"email.opened", "lead.replied"}


@pytest.mark.asyncio
async def test_update_webhook_partial_only_changes_given_fields(session):
    wh = Webhook(url="https://example.com/hook", secret="original", events=[], active=True)
    session.add(wh)
    await session.flush()

    updated = await update_webhook(
        webhook_id=wh.id,
        payload=WebhookUpdate(secret="new-secret"),  # only secret
        db=session,
    )

    assert updated.secret == "new-secret"
    assert updated.url == "https://example.com/hook"  # unchanged


@pytest.mark.asyncio
async def test_update_webhook_invalid_event_raises_400(session):
    wh = Webhook(url="https://example.com/hook", events=[], active=True)
    session.add(wh)
    await session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await update_webhook(
            webhook_id=wh.id,
            payload=WebhookUpdate(events=["bogus.event"]),
            db=session,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_update_webhook_not_found_raises_404(session):
    with pytest.raises(HTTPException) as exc_info:
        await update_webhook(
            webhook_id=99999,
            payload=WebhookUpdate(active=False),
            db=session,
        )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/settings/webhooks/{id} — delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_webhook_removes_from_db(session):
    wh = Webhook(url="https://example.com/hook", events=[], active=True)
    session.add(wh)
    await session.flush()
    wh_id = wh.id

    result = await delete_webhook(webhook_id=wh_id, db=session)

    assert result == {"ok": True}

    remaining = await session.execute(select(func.count(Webhook.id)).where(Webhook.id == wh_id))
    assert remaining.scalar() == 0


@pytest.mark.asyncio
async def test_delete_webhook_not_found_raises_404(session):
    with pytest.raises(HTTPException) as exc_info:
        await delete_webhook(webhook_id=99999, db=session)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_webhook_does_not_affect_others(session):
    wh1 = Webhook(url="https://one.example.com/hook", events=[], active=True)
    wh2 = Webhook(url="https://two.example.com/hook", events=[], active=True)
    session.add(wh1)
    session.add(wh2)
    await session.flush()

    await delete_webhook(webhook_id=wh1.id, db=session)

    remaining = await session.execute(select(func.count(Webhook.id)))
    assert remaining.scalar() == 1
