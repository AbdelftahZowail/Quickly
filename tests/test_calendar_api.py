import pytest

from fastapi.testclient import TestClient
from app.main import app


def test_calendar_api_basic_endpoints():
    # No manual initialization required; the shared engine fixture
    # ensures the schema is in place and is wired into the FastAPI app.
    """Verify that the calendar-related APIs exist and return the expected
    shape even when the database is empty.
    """
    with TestClient(app) as client:
        # stats endpoint should succeed and return integer counts
        resp = client.get("/api/calendar/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert isinstance(stats.get("total_sent"), int)
        assert isinstance(stats.get("total_scheduled"), int)
        assert isinstance(stats.get("total_campaigns"), int)

        # sent / scheduled lists should simply be arrays
        resp = client.get("/api/calendar/sent")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        resp = client.get("/api/calendar/scheduled")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        # recalculate-all and validate-queue should return at least the 'ok' flag
        resp = client.post("/api/calendar/recalculate-all")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert "campaigns_processed" in data

        resp = client.post("/api/calendar/validate-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert "total_slots_checked" in data


@pytest.mark.asyncio
async def test_calendar_sent_includes_opens_clicks(session):
    """When logs have associated opens/clicks we should return them without
    triggering lazy-loading errors.

    Previously the route performed a simple join and then accessed
    ``el.opens``/``el.clicks`` in the response comprehension, causing a
    MissingGreenlet exception under AsyncSession.  This regression test
    builds a minimal record set and validates the JSON structure produced
    by the endpoint.
    """
    from app.models import EmailOpen, EmailClick
    from tests.conftest import make_campaign, make_lead, make_email_log

    # create a campaign/lead and log entry
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    log = await make_email_log(session, lead.id, campaign.id)

    # attach an open & click
    op = EmailOpen(email_log_id=log.id, ip_address="1.2.3.4")
    clk = EmailClick(email_log_id=log.id, ip_address="5.6.7.8")
    session.add_all([op, clk])
    await session.flush()

    with TestClient(app) as client:
        resp = client.get("/api/calendar/sent")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data, "expected at least one log"
        entry = data[0]
        assert entry["opens"][0]["ip"] == "1.2.3.4"
        assert entry["clicks"][0]["ip"] == "5.6.7.8"
