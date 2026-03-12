from fastapi.testclient import TestClient
from app.main import app


def test_spa_routes_return_index_html():
    # ensure that navigation to client-side paths yields the SPA entrypoint
    client = TestClient(app)
    for path in ["/", "/campaigns", "/unibox", "/some/random/path"]:
        resp = client.get(path)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "<div id=\"root\">" in resp.text


def test_api_routes_still_work():
    # /api/status now requires authentication; override the auth dependency so
    # the test can verify the route exists and returns the expected JSON fields.
    from app.auth import get_current_user as _real_auth

    async def _fake_auth():
        return object()  # any non-None user object satisfies the dependency

    app.dependency_overrides[_real_auth] = _fake_auth
    try:
        client = TestClient(app)
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("schedule_running") in (True, False)
        # new field: server_time should be present, carry a 'Z' UTC suffix and be parseable
        assert isinstance(data.get("server_time"), str)
        assert data["server_time"].endswith("Z"), "server_time must end with 'Z' (UTC)"
        from datetime import datetime, timezone
        # datetime.fromisoformat does not accept 'Z' on Python < 3.11; strip it instead
        datetime.fromisoformat(data["server_time"].rstrip("Z"))
    finally:
        app.dependency_overrides.pop(_real_auth, None)
