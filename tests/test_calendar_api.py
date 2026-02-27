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
