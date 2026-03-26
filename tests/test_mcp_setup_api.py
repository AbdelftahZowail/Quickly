"""MCP setup settings endpoint."""

from fastapi.testclient import TestClient

from app.main import app


def test_mcp_setup_public():
    """MCP setup is public (URLs + Cursor fragment only; secrets are placeholders)."""
    with TestClient(app) as client:
        r = client.get("/api/settings/mcp-setup")
    assert r.status_code == 200
    data = r.json()
    assert "mcp_http_url" in data and "cursor_mcp_fragment" in data


def test_mcp_setup_returns_fragment():
    from app.auth import get_current_user as _real_auth

    async def _fake_auth():
        return object()

    app.dependency_overrides[_real_auth] = _fake_auth
    try:
        with TestClient(app) as client:
            r = client.get("/api/settings/mcp-setup")
        assert r.status_code == 200
        data = r.json()
        assert "api_base_url" in data
        assert data["api_base_url"].startswith("http")
        assert "mcp_http_url" in data
        assert data["mcp_http_url"].endswith("/api/mcp")
        frag = data["cursor_mcp_fragment"]
        assert "mcpServers" in frag
        assert "quickly-leads" in frag["mcpServers"]
        inner = frag["mcpServers"]["quickly-leads"]
        assert "command" in inner and "args" in inner and "env" in inner
        assert inner["env"]["QUICKLY_MCP_API_KEY"] == "__PASTE_API_KEY_FROM_SETTINGS__"
    finally:
        app.dependency_overrides.pop(_real_auth, None)
