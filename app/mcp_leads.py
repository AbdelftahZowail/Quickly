"""Remote MCP (Streamable HTTP) for Quickly leads — mounted at ``/api/mcp``."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.database import AsyncSessionLocal
from app.auth import try_resolve_user_for_mcp
from app.settings_manager import settings as app_settings

log = logging.getLogger("quickly.mcp_leads")

leads_mcp = FastMCP(
    "quickly-leads",
    instructions=(
        "Quickly leads API tools. Authenticate MCP HTTP requests with X-API-Key "
        "(Settings → API Keys) or Authorization: Bearer (JWT)."
    ),
    # Default FastMCP host is 127.0.0.1, which enables MCP DNS-rebinding checks with
    # localhost-only allowed Host headers — breaks real Hosts (e.g. quickly.example.com)
    # behind Caddy. Disable here; Quickly already terminates TLS and validates access.
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    stateless_http=True,
    streamable_http_path="/",
)

# Force session manager creation (used by StreamableHTTPASGIApp and app lifespan).
leads_mcp.streamable_http_app()
_mcp_http_handler = StreamableHTTPASGIApp(leads_mcp.session_manager)


def _api_base() -> str:
    return (app_settings.base_url or "http://localhost:8000").rstrip("/")


def _outbound_headers(ctx: Context) -> dict[str, str]:
    rc = ctx.request_context
    req = rc.request
    if not isinstance(req, Request):
        raise RuntimeError("MCP tools require an HTTP request context")
    h: dict[str, str] = {"Accept": "application/json"}
    ak = req.headers.get("x-api-key")
    auth = req.headers.get("authorization")
    if ak:
        h["X-API-Key"] = ak
    elif auth:
        h["Authorization"] = auth
    else:
        raise RuntimeError("Missing X-API-Key or Authorization on MCP request")
    return h


def _json_response(r: httpx.Response) -> str:
    try:
        data = r.json()
    except Exception:
        data = {"status_code": r.status_code, "text": r.text[:8000]}
    if r.is_error:
        data = {"error": True, "status_code": r.status_code, "detail": data}
    return json.dumps(data, indent=2, default=str)


@leads_mcp.tool()
async def list_leads(
    ctx: Context,
    q: str = "",
    status: str = "",
    bad_only: bool = False,
) -> str:
    """List leads with optional filters (search text, exact status, or bounced/invalid only)."""
    headers = _outbound_headers(ctx)
    headers["Content-Type"] = "application/json"
    params: dict[str, str] = {}
    if q.strip():
        params["q"] = q.strip()
    if status.strip():
        params["status"] = status.strip()
    if bad_only:
        params["bad_only"] = "true"
    url = f"{_api_base()}/api/leads"
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.get(url, headers=headers, params=params)
    return _json_response(r)


@leads_mcp.tool()
async def get_lead(ctx: Context, lead_id: int) -> str:
    """Get one lead by id, including campaign enrollments."""
    headers = _outbound_headers(ctx)
    url = f"{_api_base()}/api/leads/{lead_id}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(url, headers=headers)
    return _json_response(r)


@leads_mcp.tool()
async def update_lead(
    ctx: Context,
    lead_id: int,
    name: str | None = None,
    status: str | None = None,
    custom_data: dict[str, Any] | None = None,
) -> str:
    """Patch lead fields (name, status, custom_data). Status changes may trigger queue recalculation."""
    headers = _outbound_headers(ctx)
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if status is not None:
        body["status"] = status
    if custom_data is not None:
        body["custom_data"] = custom_data
    if not body:
        return json.dumps({"error": "Provide at least one of: name, status, custom_data"})
    url = f"{_api_base()}/api/leads/{lead_id}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.patch(url, headers={**headers, "Content-Type": "application/json"}, json=body)
    return _json_response(r)


@leads_mcp.tool()
async def delete_lead(ctx: Context, lead_id: int) -> str:
    """Delete a lead and associated logs; recalculates queue if enrolled."""
    headers = _outbound_headers(ctx)
    url = f"{_api_base()}/api/leads/{lead_id}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.delete(url, headers=headers)
    return _json_response(r)


@leads_mcp.tool()
async def add_campaign_leads(
    ctx: Context,
    campaign_id: int,
    leads: list[dict[str, Any]],
    skip_duplicates: bool = True,
    verify_emails: bool = False,
) -> str:
    """Add leads to a campaign. Each item should include at least email; optional name and custom_data object."""
    headers = _outbound_headers(ctx)
    if not leads:
        return json.dumps({"error": "leads array must not be empty"})
    url = f"{_api_base()}/api/campaigns/{campaign_id}/leads"
    params = {
        "skip_duplicates": "true" if skip_duplicates else "false",
        "verify_emails": "true" if verify_emails else "false",
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            url,
            headers={**headers, "Content-Type": "application/json"},
            params=params,
            json=leads,
        )
    return _json_response(r)


class _MCPAuthASGI:
    """Require the same auth as the REST API before handling MCP Streamable HTTP."""

    __slots__ = ("_inner",)

    def __init__(self, inner):
        self._inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._inner(scope, receive, send)
            return
        if scope["method"] == "OPTIONS":
            await self._inner(scope, receive, send)
            return
        raw = scope.get("headers") or []
        hdrs = {k.decode().lower(): v.decode() for k, v in raw}
        x_key = hdrs.get("x-api-key")
        auth = hdrs.get("authorization")
        user = None
        try:
            async with AsyncSessionLocal() as db:
                user = await try_resolve_user_for_mcp(db, x_api_key=x_key, authorization=auth)
                if user is not None:
                    await db.commit()
                else:
                    await db.rollback()
        except Exception:
            log.exception("mcp auth session failed")
            resp = JSONResponse({"detail": "Authentication failed"}, status_code=500)
            await resp(scope, receive, send)
            return
        if user is None:
            resp = JSONResponse({"detail": "Not authenticated"}, status_code=401)
            await resp(scope, receive, send)
            return
        await self._inner(scope, receive, send)


# Single ASGI stack for top-level Starlette Route (see main — cannot use Mount("/api/mcp"): it only
# matches /api/mcp/... with an extra segment, so /api/mcp would fall through to the SPA catch-all).
leads_mcp_http_asgi = _MCPAuthASGI(_mcp_http_handler)


@contextlib.asynccontextmanager
async def leads_mcp_lifespan():
    async with leads_mcp.session_manager.run():
        yield
