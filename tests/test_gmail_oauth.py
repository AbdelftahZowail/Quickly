from fastapi.testclient import TestClient
import json




from app.main import app

def test_google_callback_creates_inbox(monkeypatch):
    import asyncio
    from app.database import init_db
    asyncio.run(init_db())
    client = TestClient(app)
    """A callback request without an explicit background_tasks query should
    succeed and redirect to the inboxes page.  Previously FastAPI treated the
    missing ``background_tasks`` argument as a required query parameter when
    the handler lacked the proper import.
    """
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
    monkeypatch.setattr("app.gmail_sync.sync_gmail_inbox_by_email", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.gmail_sync.renew_gmail_watch_for_all", lambda *args, **kwargs: None)


    state = json.dumps({"display_name": "Foo", "max_per_day": 10})
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
