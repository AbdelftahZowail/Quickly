from fastapi.testclient import TestClient
import asyncio

from app.main import app
from app.database import init_db


def test_test_mode_settings_api():
    # initialize schema
    asyncio.run(init_db())
    client = TestClient(app)

    # initially no setting should exist, so we default to False
    resp = client.get("/api/settings/test-mode")
    assert resp.status_code == 200
    assert resp.json() == {"test_mode": False}

    # status endpoint (legacy) should also show False
    resp2 = client.get("/api/test/status")
    assert resp2.status_code == 200
    assert resp2.json()["test_mode"] is False

    # toggle on via new settings endpoint
    resp = client.post("/api/settings/test-mode", json={"test_mode": True})
    assert resp.status_code == 200
    assert resp.json()["test_mode"] is True

    # GET again and verify /api/status picks it up
    resp = client.get("/api/settings/test-mode")
    assert resp.json()["test_mode"] is True
    resp = client.get("/api/status")
    assert resp.json()["test_mode"] is True

    # legacy POST should also work and allow disabling
    resp = client.post("/api/test/status", json={"test_mode": False})
    assert resp.status_code == 200
    assert resp.json()["test_mode"] is False
    # and set in db
    resp = client.get("/api/settings/test-mode")
    assert resp.json()["test_mode"] is False
