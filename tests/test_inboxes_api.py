from datetime import datetime, timedelta

import pytest

from app.models import Inbox
from app.routers import inbox as inbox_router
from tests.conftest import make_email_log


@pytest.mark.asyncio
async def test_list_inboxes_sent_today(session):
    """The inbox list API should include a `sent_today` field that reflects
    how many emails have been logged for that inbox on the current UTC day.
    """
    # create a fresh inbox in the database
    inbox = Inbox(email="foo@example.com")
    session.add(inbox)
    await session.flush()
    assert inbox.id is not None

    # before any logs the field should be zero; call the router directly
    listed = await inbox_router.list_inboxes(db=session)
    assert isinstance(listed, list)
    assert listed[0].sent_today == 0

    # insert a log timestamped today and verify the count increments
    await make_email_log(session, lead_id=1, campaign_id=1, inbox_id=inbox.id)
    await session.flush()
    listed = await inbox_router.list_inboxes(db=session)
    assert listed[0].sent_today == 1

    # a log from yesterday should not be counted
    yesterday = datetime.utcnow() - timedelta(days=1)
    await make_email_log(
        session,
        lead_id=2,
        campaign_id=1,
        inbox_id=inbox.id,
        sent_at=yesterday,
    )
    await session.flush()
    listed = await inbox_router.list_inboxes(db=session)
    assert listed[0].sent_today == 1  # still only the single today-based entry


@pytest.mark.asyncio
async def test_get_single_inbox_returns_sent_today(session):
    inbox = Inbox(email="bar@example.com")
    session.add(inbox)
    await session.flush()
    await make_email_log(session, lead_id=3, campaign_id=1, inbox_id=inbox.id)
    await session.flush()

    # invoke the router function directly instead of an HTTP call
    obj = await inbox_router.get_inbox(inbox.id, db=session)
    assert obj.sent_today == 1
