# Automating Quickly with n8n

Quickly includes a **community n8n node** (`n8n-nodes-quickly`) so you can call the same REST API the UI uses—campaigns, leads, sequences, inboxes, webhooks, Unibox, settings, and more—without hand-writing HTTP requests in every workflow.

---

## What you get

- **One node in the editor** — pick a **Resource** (e.g. Campaign, Lead, Inbox), then an **Operation** (list, create, update, export CSV, etc.). Fields appear based on what you selected.
- **Shared credentials** — **Quickly API** credentials store your Quickly **base URL** and either an **API key** (`X-API-Key`, recommended for automation) or a **Bearer JWT** from login.
- **Dynamic dropdowns** — campaigns, inboxes, leads, sequences, and similar fields can load choices from your Quickly instance. You can also **type an ID manually** (or use an expression) where the UI allows arbitrary values.
- **Same auth as the API** — behavior matches [API.md](API.md): API keys and JWTs are accepted the same way as for `curl` or other clients.

For **every path, query parameter, and JSON body**, the canonical reference is still **[API.md](API.md)**. The node is a structured front-end on top of that API.

---

## Documentation and install

All **build steps, Docker / `N8N_CUSTOM_EXTENSIONS` layout, credential fields, binary CSV import/export, Unibox thread format (`inbox_id||thread_id`), troubleshooting, and resource overview** live in the package README:

**→ [n8n-node README — full n8n integration guide](../n8n-node/README.md)**

Clone or browse the repo with that path open locally: `n8n-node/README.md`.

---

## Quick orientation

| Topic | Where to read |
|--------|----------------|
| Install & env vars (`N8N_CUSTOM_EXTENSIONS`, folder layout) | [n8n-node README](../n8n-node/README.md#install-in-n8n) |
| Credentials (API key vs Bearer, connection test) | [n8n-node README](../n8n-node/README.md#credentials-quickly-api) |
| What each **resource** covers | [n8n-node README](../n8n-node/README.md#resources-overview) |
| HTTP details for each endpoint | [API.md](API.md) |
| Outbound events from Quickly into other systems | [WEBHOOKS.md](WEBHOOKS.md) |

---

## Source code

TypeScript sources and `package.json` live under **`n8n-node/`** in this repository (keyword `n8n-community-node-package` for community installs).
