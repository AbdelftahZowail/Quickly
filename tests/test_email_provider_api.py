"""Tests for the new email-provider lookup endpoint."""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_detect_endpoint_success(monkeypatch):
    """The route should call the detection helper and return its value."""

    async def fake_detect(email: str):
        return "Google Workspace"

    # the router imported the helper at module import time, so patch the name
    # in the router rather than the original module.
    monkeypatch.setattr("app.routers.email_provider.detect_provider_for_email", fake_detect)

    resp = client.get("/api/email-provider/detect", params={"email": "foo@bar.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"email": "foo@bar.com", "provider": "Google Workspace"}


def test_detect_endpoint_invalid_email():
    """Requests missing the '@' should be rejected with 400."""

    resp = client.get("/api/email-provider/detect", params={"email": "not-an-email"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid email address"


def test_detect_endpoint_none(monkeypatch):
    """A lookup that returns ``None`` is passed through unchanged."""

    async def fake_detect(email: str):
        return None

    monkeypatch.setattr("app.routers.email_provider.detect_provider_for_email", fake_detect)

    resp = client.get("/api/email-provider/detect", params={"email": "foo@bar.com"})
    assert resp.status_code == 200
    assert resp.json() == {"email": "foo@bar.com", "provider": None}
