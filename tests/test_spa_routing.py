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
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.json().get("scheduler_running") in (True, False)
