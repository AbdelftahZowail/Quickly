from fastapi.testclient import TestClient
import json
from datetime import datetime, timedelta

import pytest

from app.main import app

@pytest.mark.asyncio
async def test_google_callback_creates_inbox(monkeypatch, session):
    """A callback request should succeed and redirect to the inboxes page.
    The callback now validates a one-time CSRF state token; we pre-create
    an OAuthState record and include the token in the state parameter.
    """
    from app.models import OAuthState

    csrf_token = "test-csrf-token-abc123"
    state_record = OAuthState(
        state_token=csrf_token,
        purpose="inbox_google",
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    session.add(state_record)
    await session.commit()

    with TestClient(app) as client:
        # patch out any real network/DB interactions
        # stub out the helpers used by the callback so nothing hits Google
        monkeypatch.setattr(
            "app.routers.gmail_oauth._exchange_code",
            lambda *args, **kwargs: {
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_in": 3600,
            },
        )
        monkeypatch.setattr("app.routers.gmail_oauth._get_user_email", lambda token: "test@example.com")

        async def fake_get_google_oauth_credentials(db):
            return "cid", "csecret"

        monkeypatch.setattr(
            "app.app_settings.get_google_oauth_credentials",
            fake_get_google_oauth_credentials,
        )
        # avoid any background sync attempts which would call external APIs
        # patch the helper functions used inside the local _post_connect_tasks

        # also verify that the status endpoint reflects the in-memory env values
        from app.settings_manager import settings
        settings.google_client_id = "cid"
        settings.google_client_secret = "csecret"
        resp_status = client.get("/api/gmail/status")
        assert resp_status.status_code == 200
        assert resp_status.json()["configured"] is True

        state = json.dumps({"display_name": "Foo", "max_per_day": 10, "_csrf": csrf_token})
        # use `params` to ensure proper URL encoding of the JSON string
        resp = client.get(
            "/oauth/google/callback",
            params={"code": "abc123", "state": state},
            follow_redirects=False,
        )
        # should redirect back to inboxes with our email (303 before following)
        assert resp.status_code == 303

        # verify the endpoint still works when using the shared client fixture above
        location = resp.headers.get("location", "")
        assert "/inboxes" in location
        assert "connected=test%40example.com" in location

        # following the redirect should give the HTML page too
        follow = client.get(location)
        assert follow.status_code == 200

        # Verify that the new inbox/gmail account shows up in the API list
        resp2 = client.get("/api/gmail/accounts")
        assert resp2.status_code == 200
        data = resp2.json()
        assert any(acc["google_email"] == "test@example.com" for acc in data)
