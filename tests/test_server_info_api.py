"""Settings server-info endpoint (tracking / deployment hints)."""

from fastapi.testclient import TestClient

from app.main import app


def test_server_info_includes_cname_ui_flag():
    from app.auth import get_current_user as _real_auth

    async def _fake_auth():
        return object()

    app.dependency_overrides[_real_auth] = _fake_auth
    try:
        with TestClient(app) as client:
            r = client.get("/api/settings/server-info")
        assert r.status_code == 200
        data = r.json()
        assert "base_url" in data
        assert "cname_target" in data
        assert data.get("custom_tracking_cname_ui_enabled") is True
    finally:
        app.dependency_overrides.pop(_real_auth, None)


def test_server_info_hides_cname_ui_when_prebuilt(monkeypatch):
    monkeypatch.setenv("QUICKLY_PREBUILT_IMAGE", "1")
    from app.auth import get_current_user as _real_auth

    async def _fake_auth():
        return object()

    app.dependency_overrides[_real_auth] = _fake_auth
    try:
        with TestClient(app) as client:
            r = client.get("/api/settings/server-info")
        assert r.status_code == 200
        assert r.json().get("custom_tracking_cname_ui_enabled") is False
    finally:
        app.dependency_overrides.pop(_real_auth, None)


def test_server_info_cname_ui_override(monkeypatch):
    monkeypatch.setenv("QUICKLY_PREBUILT_IMAGE", "1")
    monkeypatch.setenv("QUICKLY_TRACKING_CNAME_UI", "1")
    from app.auth import get_current_user as _real_auth

    async def _fake_auth():
        return object()

    app.dependency_overrides[_real_auth] = _fake_auth
    try:
        with TestClient(app) as client:
            r = client.get("/api/settings/server-info")
        assert r.status_code == 200
        assert r.json().get("custom_tracking_cname_ui_enabled") is True
    finally:
        app.dependency_overrides.pop(_real_auth, None)
