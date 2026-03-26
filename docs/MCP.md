# Model Context Protocol (MCP) — Quickly Leads

Quickly exposes a **remote MCP server** over **Streamable HTTP** so AI clients (Cursor, Claude Desktop, etc.) can call lead-related tools without running a local Python MCP process. The server is part of the main FastAPI app and uses the same authentication as the REST API.

---

## Endpoint

| Item | Value |
|------|--------|
| **URL** | `{BASE_URL}/api/mcp` |
| **Transport** | Streamable HTTP (use `mcp-remote` with `--transport http-only` when connecting from stdio-only clients) |
| **Auth** | `X-API-Key: <key>` and/or `Authorization: Bearer <jwt>` on every request (same as [API.md](API.md)) |

`BASE_URL` is the value you set in the environment (e.g. `https://quickly.example.com`). The UI **Settings → MCP (AI agents)** shows the resolved URL after login.

---

## Authentication

1. **API key (recommended for agents)** — Create a key under **Settings → API Keys**. Send it as:

   ```http
   X-API-Key: <your-api-key>
   ```

2. **JWT Bearer** — Same access token as the REST API (`Authorization: Bearer …`). Tokens expire (see API docs); API keys are usually simpler for long-lived MCP setups.

MCP requests are checked before the MCP session runs. Tool calls that proxy to `/api/leads` and `/api/campaigns/...` reuse the credentials from the **incoming MCP HTTP request**, so each user/session only accesses their own data.

---

## Tools

| Tool | Purpose |
|------|--------|
| `list_leads` | List leads (`q`, `status`, `bad_only`) — same filters as `GET /api/leads` |
| `get_lead` | `GET /api/leads/{id}` |
| `update_lead` | `PATCH /api/leads/{id}` (`name`, `enrollment_status`, `custom_data`) |
| `delete_lead` | `DELETE /api/leads/{id}` |
| `add_campaign_leads` | `POST /api/campaigns/{campaign_id}/leads` (bulk add / enroll) |

Standalone lead creation is not exposed as a separate MCP tool; use `add_campaign_leads` (same rules as the REST API).

---

## Cursor and `mcp-remote`

Most MCP clients only support **stdio**. Use **[mcp-remote](https://www.npmjs.com/package/mcp-remote)** so the editor runs a small Node bridge to Quickly’s HTTPS endpoint.

### From the UI

1. Log in → **Settings → MCP (AI agents)**.
2. Create an **API key** if needed.
3. Click **Copy MCP fragment** and merge the `mcpServers` entry into your MCP config (Cursor: `~/.cursor/mcp.json`).

The fragment uses environment substitution so secrets are not embedded in JSON with awkward escaping:

```json
{
  "mcpServers": {
    "quickly-leads": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-domain.com/api/mcp",
        "--header",
        "X-API-Key:${QUICKLY_MCP_API_KEY}",
        "--transport",
        "http-only"
      ],
      "env": {
        "QUICKLY_MCP_API_KEY": "your-key-here"
      }
    }
  }
}
```

### JWT instead of an API key

Use a header without spaces after the colon (Cursor/Windows caveat per mcp-remote docs), and put the full `Bearer …` value in `env`:

```json
"args": [
  "-y",
  "mcp-remote",
  "https://your-domain.com/api/mcp",
  "--header",
  "Authorization:${QUICKLY_MCP_AUTH}",
  "--transport",
  "http-only"
],
"env": {
  "QUICKLY_MCP_AUTH": "Bearer eyJ..."
}
```

### Local HTTP (development only)

If Quickly is only on `http://localhost:8000`, add **`--allow-http`** after the URL in `args` (see mcp-remote README). Do not use this on untrusted networks.

---

## Setup helper API

`GET /api/settings/mcp-setup` (authenticated) returns:

| Field | Description |
|-------|-------------|
| `api_base_url` | Configured app base URL |
| `mcp_http_url` | Full MCP endpoint (`…/api/mcp`) |
| `cursor_mcp_fragment` | Ready-to-merge `mcpServers` object for `npx mcp-remote` |

See [API.md — Settings](API.md#settings) for the full Settings API.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| **404 / 405** on `/api/mcp` | Old deployment without the dedicated MCP routes; upgrade Quickly. |
| **401** | Missing or invalid `X-API-Key` / Bearer token. |
| **421 Misdirected Request** / “Invalid Host header” | Fixed in current Quickly: MCP DNS rebinding checks are disabled for the public Host your reverse proxy sends. Upgrade if you still see this. |
| **Connection / TLS errors** | Check `BASE_URL`, certificate, and that the client can reach the host. |

---

## Related documentation

- [API.md](API.md) — REST reference (leads, campaigns, auth)
- [README.md](../README.md) — Install and overview
