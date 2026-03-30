# n8n-nodes-quickly

Community node package for **Quickly** (this repository) — call the Quickly JSON REST API from n8n workflows (campaigns, leads, sequences, inboxes, webhooks, Unibox, settings, and more).

Full HTTP details (paths, bodies, query params, auth) are in the main repo: **[`docs/API.md`](../docs/API.md)**. A short **n8n-focused overview** (with a link back here) lives in **[`docs/N8N.md`](../docs/N8N.md)**.

---

## Requirements

- **n8n** self-hosted (community nodes enabled). Tested with n8n **2.x**; should work on recent **1.x** builds that support `allowArbitraryValues` on options fields.
- A running **Quickly** instance reachable from n8n (same host, LAN, or public URL).
- **Authentication:** Quickly API key (`X-API-Key`) or JWT access token from `POST /api/auth/login` (`Authorization: Bearer …`).

---

## Build

From this directory:

```bash
npm install
npm run build
```

Output is emitted to `dist/`. The `n8n` block in `package.json` points n8n at the compiled credential and node files.

---

## Install in n8n

`docker cp ./dist n8n:/home/node/.n8n/custom/n8n-nodes-quickly/dist`
`docker restart n8n`

### Option A — Custom extensions directory (common for Docker)

1. Build the package (above).
2. Copy the **whole package folder** (including `package.json`, `dist/`, and `node_modules` from `npm install`) to a path n8n can read, e.g.  
   `/home/node/.n8n/custom/n8n-nodes-quickly`
3. Set the environment variable:

   ```bash
   N8N_CUSTOM_EXTENSIONS=/home/node/.n8n/custom/n8n-nodes-quickly
   ```

4. Restart n8n.

The directory layout should look like:

```text
n8n-nodes-quickly/
├── package.json
├── dist/
│   ├── credentials/
│   └── nodes/
│       └── Quickly/
└── node_modules/
```

### Option B — npm install into n8n’s `custom` folder

If your setup documents installing community packages with npm into a custom directory, install this package there (from a published tarball, git URL, or `npm pack`), then point `N8N_CUSTOM_EXTENSIONS` at that folder or follow your host’s community-node instructions.

### Option C — n8n Community Nodes UI

If you publish `n8n-nodes-quickly` to npm with the keyword **`n8n-community-node-package`**, users can install it from **Settings → Community nodes** (depending on n8n version and admin settings).

---

## Credentials: Quickly API

Create credentials in n8n: **Quickly API** (`quicklyApi`).

| Field | Description |
|--------|-------------|
| **Base URL** | Root URL of Quickly, e.g. `https://quickly.example.com` or `http://localhost:8000`. Trailing slashes are stripped automatically. |
| **Authentication** | **API Key** (recommended for automation) or **Bearer Token (JWT)**. |
| **API Key** | Raw key from Quickly **Settings → API Keys** (`qk_live_…`). Sent as `X-API-Key`. |
| **Access Token** | JWT from `POST /api/auth/login`. Sent as `Authorization: Bearer …`. Tokens expire (default 30 minutes); refresh via Quickly’s auth flow if needed. |

**Connection test** calls `GET /api/auth/me`. It uses programmatic auth (no fragile expressions), so both API key and Bearer modes are supported.

---

## Node: Quickly

Add the **Quickly** node to a workflow and attach **Quickly API** credentials.

### Parameters

1. **Resource** — API area (Campaign, Lead, Inbox, …).
2. **Operation** — Reloads when you change the resource; pick the action to run.

Optional and conditional fields appear based on resource + operation.

### Dynamic lists and manual IDs

Many fields load options from Quickly (campaigns, inboxes, leads, sequences, etc.). Those fields support **arbitrary values**: you can **select from the list or type an ID** (or an n8n expression) when the UI allows it.

**Unibox → Thread** (get thread / mark read): the loader stores values as `inbox_id||thread_id`. You can pick from the list or type that form manually, e.g. `3||abc123thread`.

**Campaign → Reorder** uses a JSON array of IDs, e.g. `[3,1,2]`, not the multi-select control.

### Expressions

Where n8n allows expressions, you can reference previous nodes, e.g. `{{ $json.id }}`, for IDs and other parameters.

### Binary data

- **Lead → Export CSV** and **Campaign Lead → Export CSV** return **CSV as binary** on the item (plus a small `json` metadata object).
- **Campaign Lead → Import CSV** expects an **input binary property** (default name `data`) containing the CSV file.

### Errors and **Continue On Fail**

Execution uses try/catch per item. Enable **Continue On Fail** on the node to record errors in the output JSON instead of stopping the workflow.

---

## Resources overview

| Resource | What it covers (high level) |
|----------|-----------------------------|
| **Account** | Current user (`GET /api/auth/me`). |
| **Campaign** | CRUD, duplicate, reorder, queue, sent, analytics, preview, test send. |
| **Sequence** | Steps under a campaign. |
| **Sequence Variant** | A/B variants per sequence. |
| **Lead** | Global leads: list, filters, export, update, delete, bulk actions, recover, history, replies. |
| **Campaign Lead** | Enroll, list, remove, enrollment patch, provider detect, verification, CSV import/export. |
| **Inbox** | CRUD, pause, unpause. |
| **Schedule** | Global sent/scheduled/stats, validate queue, recalculate all, manual open/click on a log. |
| **Status** | `GET /api/status`, `GET /api/system-health`. |
| **Settings** | Scheduling, time offset, test mode, server/tracking/MCP, known IPs, AI, email verification. |
| **Webhook** | Event types, CRUD, test. |
| **Notification** | Email notification config. |
| **Unibox** | Conversations, threads, sync, send. |
| **Email Account (OAuth)** | Gmail / Office 365 status, list, disconnect (no browser OAuth in this node). |

Endpoints that are intentionally **not** exposed here include browser OAuth redirects, tracking pixels/redirects, MCP stream HTTP, and similar internal or non-REST flows. See [`docs/API.md`](../docs/API.md) for the full surface area.

---

## Rate limits and errors

Quickly may return **429** (rate limit), **401** (auth), **404**, **422**, etc. See **Error Responses** in [`docs/API.md`](../docs/API.md).

---

## Development

```bash
npm run watch   # rebuild on TypeScript changes
```

Package name: **`n8n-nodes-quickly`**. Keyword for community discovery: **`n8n-community-node-package`**.

---

## License

MIT (same as `package.json` unless you change it).
