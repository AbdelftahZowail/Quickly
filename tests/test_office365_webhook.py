import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import select

from app.models import Office365Account, Office365GraphSubscription
from app.routers.office365_webhook import ensure_subscription
from tests.conftest import make_inbox


@pytest.mark.asyncio
async def test_ensure_subscription_creates_new(session, monkeypatch):
    inbox = await make_inbox(session, provider="office365")
    acct = Office365Account(
        inbox_id=inbox.id,
        microsoft_email="user@example.com",
        access_token="token",
        refresh_token="refresh",
        token_expiry=datetime.utcnow() + timedelta(hours=1),
    )
    session.add(acct)
    await session.flush()

    async def fake_fresh(db, inbox_id):
        return SimpleNamespace(access_token="fake-token")

    monkeypatch.setattr("app.routers.office365_webhook._fresh_token", fake_fresh)

    calls = {}
    def fake_graph(method, url, token, body=None):
        # record arguments
        calls['last'] = (method, url, token, body)
        if method == "POST":
            return {"id": "sub123"}
        raise RuntimeError("unexpected method")

    monkeypatch.setattr("app.routers.office365_webhook._graph_request", fake_graph)

    result = await ensure_subscription(session, inbox.id)
    assert result['action'] == 'created'
    assert result['subscription_id'] == 'sub123'

    stored = await session.execute(select(Office365GraphSubscription))
    sub = stored.scalar_one()
    assert sub.subscription_id == 'sub123'
    assert sub.inbox_id == inbox.id


@pytest.mark.asyncio
async def test_ensure_subscription_renews_existing(session, monkeypatch):
    inbox = await make_inbox(session, provider="office365")
    acct = Office365Account(
        inbox_id=inbox.id,
        microsoft_email="user2@example.com",
        access_token="token2",
        refresh_token="refresh2",
        token_expiry=datetime.utcnow() + timedelta(hours=1),
    )
    session.add(acct)
    await session.flush()

    # create an existing subscription that is about to expire
    old_expiry = datetime.utcnow() + timedelta(minutes=10)
    sub = Office365GraphSubscription(
        inbox_id=inbox.id,
        subscription_id="sub-old",
        client_state="secret",
        expiry=old_expiry,
    )
    session.add(sub)
    await session.flush()

    async def fake_fresh(db, inbox_id):
        return SimpleNamespace(access_token="fresh-token")

    monkeypatch.setattr("app.routers.office365_webhook._fresh_token", fake_fresh)

    calls = {}
    def fake_graph(method, url, token, body=None):
        calls['last'] = (method, url, token, body)
        if method == "PATCH":
            return {}
        raise RuntimeError("unexpected method")
    monkeypatch.setattr("app.routers.office365_webhook._graph_request", fake_graph)

    result = await ensure_subscription(session, inbox.id)
    assert result['action'] == 'renewed'
    assert result['subscription_id'] == 'sub-old'

    # expiry should have been updated to > old_expiry
    stored = await session.execute(select(Office365GraphSubscription))
    new_sub = stored.scalar_one()
    assert new_sub.expiry > old_expiry
