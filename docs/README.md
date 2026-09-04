<p align="center">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT License" />
  <img src="https://img.shields.io/badge/Self--Hosted-100%25-teal" alt="Self-Hosted Cold Email Platform" />
  <img src="https://img.shields.io/badge/Docker-One--Command_Deploy-2496ED?logo=docker&logoColor=white" alt="Docker Ready" />
  <img src="https://img.shields.io/badge/PRs-Welcome-brightgreen.svg" alt="PRs Welcome" />
</p>

<h1 align="center">Quickly — Open-Source Cold Email Platform</h1>

<p align="center"><strong>Own your cold email infrastructure. No SaaS fees. No per-seat pricing. No data leaving your server.</strong></p>

<p align="center">
  <a href="#quick-start">Deploy in 5 minutes →</a> &nbsp;|&nbsp;
  <a href="INSTALL.md">Installation Guide</a> &nbsp;|&nbsp;
  <a href="API.md">API Docs</a> &nbsp;|&nbsp;
  <a href="N8N.md">n8n</a> &nbsp;|&nbsp;
  <a href="WEBHOOKS.md">Webhooks</a> &nbsp;|&nbsp;
  <a href="CONTRIBUTORS.md">Contributing</a>
</p>

---

## What Is Quickly?

**Quickly is a free, open-source cold email platform you self-host on your own server.** It replaces paid cold email tools like Instantly, Smartlead, and Lemlist with a single Docker deployment that you own outright — unlimited contacts, unlimited campaigns, unlimited sends.

If you've ever asked *"Is there a self-hosted alternative to Instantly?"* or *"How do I run cold email campaigns without paying per contact?"* — Quickly is your answer.

> **Quickly gives sales teams, agencies, and indie hackers the same multi-step sequencing, inbox rotation, AI reply detection, and analytics as enterprise tools — at zero monthly cost.**

---

## Why Teams Choose Quickly Over Paid Cold Email Tools

| What you're paying for today | What Quickly gives you instead |
|---|---|
| $97–$500/month SaaS fees (Instantly, Smartlead, Lemlist) | Deploy free on Railway or any PaaS — or self-host on your own server |
| Per-seat pricing that scales against you | Unlimited team members, unlimited contacts |
| Your lead data stored on vendor servers | 100% self-hosted — your infrastructure, your Postgres database |
| Vendor lock-in and feature gating | Open source, fully hackable, 90+ REST API endpoints |
| Complex onboarding with multiple integrations | Single `docker compose up` — live in under 5 minutes |

---

## Core Features

### Multi-Step Email Sequences
Build campaigns with unlimited follow-up steps. Set custom wait days between each touch, toggle thread continuation, and write HTML or plain-text bodies per step. Every sequence element is fully configurable.

### A/B Testing Built In
Create multiple subject line or body variants per sequence step. Quickly randomly selects a variant at send time and tracks open rates, click rates, and reply rates per variant — so you always know what messaging wins.

### Smart Inbox Rotation, Warm-Up & Jitter
Spread sends across as many inboxes as you want — **Gmail, Microsoft, and generic SMTP accounts are all supported**, including personal Outlook.com addresses, business Microsoft 365 / Office 365 accounts, and relays like Amazon SES. Quickly's queue engine respects per-inbox daily limits, business-day schedules, configurable sending windows, and per-inbox cooldowns. New accounts? The **ramp-up warm-up** gradually increases daily send volume over a configurable period to protect deliverability. **Random jitter** adds a configurable random delay (up to N seconds) to each scheduled send time so your outreach doesn't fire in perfectly uniform bursts — configurable per inbox.

### AI-Powered Reply Classification
Every reply is automatically classified by AI into one of six categories: `interested`, `not_interested`, `out_of_office`, `wrong_person`, `auto_reply`, or `unsubscribed`. Supports 19 AI providers including OpenAI, Anthropic Claude, Google Gemini, Mistral, Groq, and Ollama (for fully offline classification).

### Unified Inbox (Unibox)
A built-in inbox sync layer gives you real-time reply detection, threaded conversation view, and compose/reply — all inside Quickly. Works across your Gmail and Microsoft accounts side-by-side. No tab-switching. No missed replies.

### Open & Click Tracking with Custom Domains
Built-in pixel tracking and link wrapping capture every open and click. **Quickly Beacon** is the recommended way to use your own tracking hostname on prebuilt Docker / Railway-style deployments. Advanced self-hosters can still point a CNAME at Quickly when **host Caddy** or in-compose Caddy handles on-demand TLS (see [INSTALL.md](INSTALL.md#quickly-beacon-recommended-custom-tracking-hostnames)). Filter out your own opens and clicks by registering known IPs.

### Full Analytics Dashboard
See aggregated metrics, per-campaign breakdowns, per-step performance, timeline charts, open rates, click rates, reply rates, bounces, and unsubscribes — all in one view.

### Lead Provider Matching
Quickly detects each lead's email provider via DNS MX lookup (Google Workspace, Office 365, etc.) and can automatically route sends through matching inboxes. Google leads get Gmail senders; Microsoft leads get Office 365 senders. Toggle per campaign with zero configuration.

### System Health Dashboard
A dedicated **System Health** page gives you a live overview of every connected inbox's token status, OAuth scope coverage, tracking domain reachability, AI feature connectivity, and unibox sync mode — all in one place. No digging through logs.

### Email Notifications
Get notified by email when specific events occur (e.g. a lead replies as `interested`, a token expires, or a bounce happens). Configure per-user, per-event notification preferences with an hourly rate limit.

### Webhooks — React to Every Email Event
Register any number of webhook endpoints and subscribe them to any combination of **15 real-time events**:

`email.sent` · `email.opened` · `email.clicked` · `email.bounced` · `lead.replied` · `lead.unsubscribed` · `lead.status_changed` · `lead.interested` · `lead.not_interested` · `lead.out_of_office` · `lead.wrong_person` · `lead.auto_reply` · `daily_limit` · `rate_limit` · `token_expired`

Each webhook supports Bearer token authentication and per-endpoint event filtering. Connect Quickly to your CRM, Slack, Zapier, or any custom pipeline.

### Email Verification
Verify lead addresses before sending via mailtester.ninja or any custom HTTP provider. Trigger verification per campaign with live status tracking — stop wasting sends on bad addresses.

### Priority Scheduling & Timezone Awareness
Drag campaigns to reorder send priority. Choose **priority-first** (exhaust the top campaign's slots first) or **round-robin** (spread sends evenly). Set a timezone per campaign so emails land during your recipients' business hours, not yours.

### CSV Import / Export, Full REST API & n8n Integration
Bulk-import leads from CSV in seconds. Export any campaign's leads with full open/click/reply status at any time. Automate everything with 90+ REST API endpoints secured by JWT auth and API keys — every UI action has an API equivalent. For workflow automation, Quickly ships with a **custom n8n node** so you can build pipelines in n8n without hand-coding HTTP for every call — see **[Automating with n8n](#automating-with-n8n)** and the dedicated **[N8N.md](N8N.md)** guide.

### Onboarding & Deliverability Tips
A guided onboarding checklist walks new users through connecting inboxes, building their first campaign, and configuring tracking. A dedicated **Deliverability Tips** page covers DNS setup, warm-up best practices, and sending guidelines to maximize inbox placement.

---

## How Quickly Works

```
┌────────────┐      ┌──────────────┐     ┌─────────────┐
│  React UI  │────▶│  FastAPI     │────▶│  PostgreSQL │
│  (Vite)    │◀────│  (Uvicorn)   │◀────│             │
└────────────┘      └─────┬────────┘     └─────────────┘
                          │
                   ┌──────▼───────┐
                   │  Send Queue  │──▶ Gmail API (OAuth2)
                   │ (APScheduler)│──▶ Microsoft Graph API (OAuth2)
                   └──────────────┘
```

1. **Log in** — On first deploy, register your admin account directly from the login page.
2. **Connect inboxes** — Authorize Gmail accounts or Microsoft 365 / Outlook accounts with one OAuth click each.
3. **Build sequences** — Write multi-step campaigns with subjects, bodies, and wait days.
4. **Import leads** — Upload a CSV; the queue engine instantly reserves send slots across inboxes.
5. **Emails go out automatically** — A background scheduler runs every minute, respecting limits, windows, and cooldowns.
6. **Track everything** — Opens, clicks, replies, AI classifications, and webhook events fire in real time.

---

## Quick Start

**Option 1: Deploy on Railway (free, no server needed)**

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app)

Click the button above to spin up Quickly on [Railway](https://railway.com) in seconds — no VPS, no DevOps, completely free to start. Railway handles the infrastructure; you just configure your OAuth credentials and go.

**Option 2: Self-host with Docker (any VPS or PaaS)**

If you plan to use **only [Quickly Beacon](INSTALL.md#quickly-beacon-recommended-custom-tracking-hostnames)** for custom tracking (recommended on the prebuilt image), use **`docker-compose.no-caddy.yml`** — Postgres + Quickly on port **8000**, no Caddy in Compose; put your own reverse proxy in front for HTTPS. See [INSTALL.md — Beacon-only](INSTALL.md#beacon-only-docker-compose-no-caddy).

```bash
mkdir quickly && cd quickly

curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.no-caddy.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example
mv .env.example .env
# Set BASE_URL and your OAuth credentials; terminate HTTPS with nginx, Traefik, host Caddy, etc.

docker compose -f docker-compose.no-caddy.yml up -d
```

**All-in-one with Caddy in Docker** (Let's Encrypt for your main app domain in one stack):

```bash
mkdir quickly && cd quickly

curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/Caddyfile
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example

mv .env.example .env
# Set CADDY_HOST, BASE_URL, and your OAuth credentials in .env

docker compose up -d
```

**That's it** for the Caddy stack — open `https://yourdomain.com` and Caddy provisions HTTPS automatically.

Quickly is optimized to run on any Docker-compatible platform: Railway, Render, Fly.io, a bare VPS, or your own machine. There's no mandatory infrastructure cost.

> Need a step-by-step walkthrough? [INSTALL.md](INSTALL.md) is an interactive guide — pick your deployment path (Railway, fresh VPS, nginx, or local dev) and it walks you through every step.

---

## Frequently Asked Questions

**Is Quickly really free?**
Yes. Quickly is MIT-licensed open-source software with no per-contact fees, no seat limits, and no feature tiers. You can run it completely free on platforms like Railway — no server costs at all. If you prefer full control, deploying to your own VPS typically costs $5–$20/month.

**What email providers does Quickly support?**
Quickly supports **Gmail** (via Google OAuth), **Microsoft accounts** — including personal Outlook.com addresses and business Microsoft 365 / Office 365 accounts (via Microsoft Graph API OAuth2) — and **any SMTP relay** (Amazon SES, Brevo, self-hosted, …) with optional IMAP reply sync. You can mix all inbox types in the same campaign.

**How is Quickly different from Instantly or Smartlead?**
Instantly and Smartlead are hosted SaaS products that store your data on their servers and charge monthly fees per inbox or contact. Quickly runs entirely on your infrastructure — your leads never leave your server.

**Does Quickly support AI reply classification?**
Yes. Quickly integrates with 19 AI providers (OpenAI, Anthropic, Gemini, Groq, Mistral, Ollama, and more) to automatically classify replies into categories like `interested`, `not_interested`, and `out_of_office`.

**Can I use Quickly with a team?**
Yes. There are no seat limits. Deploy once, give your entire team access.

**Is there an API? Can I use it with n8n?**
Yes — 90+ REST endpoints covering every feature in the UI, all secured via JWT and API keys. Quickly also ships with a **custom n8n node** for drag-and-drop workflows. Start with [N8N.md](N8N.md) (overview and link to the full package README); HTTP details are in [API.md](API.md).

**How does authentication work?**
Quickly has a built-in user system. On first run, visit the login page and register your admin account. Log in with username/password, or with Google/Microsoft OAuth. For automation, generate API keys from the Settings page.

**What happens when someone unsubscribes?**
Quickly automatically catches unsubscribe replies via AI classification and marks those leads so they never receive another email.

---

## Environment Configuration

The `.env` file is intentionally minimal. All runtime settings (test mode, time offset, AI provider keys, sending windows, warm-up schedules, tracking domains, etc.) are managed from the **Settings page** in the UI after deploy and stored in the database.

| Variable | Required | Description |
|---|---|---|
| `CADDY_HOST` | For HTTPS | Your domain (e.g. `mail.yourdomain.com`) |
| `BASE_URL` | Yes | Full URL with protocol (e.g. `https://mail.yourdomain.com`) |
| `DATABASE_URL` | Auto | Set by docker-compose; override for external Postgres |
| `GOOGLE_CLIENT_ID` | For Gmail | Google OAuth 2.0 client ID |
| `GOOGLE_CLIENT_SECRET` | For Gmail | Google OAuth 2.0 client secret |
| `OFFICE365_CLIENT_ID` | For Office 365 | Microsoft Entra app (client) ID |
| `OFFICE365_CLIENT_SECRET` | For Office 365 | Microsoft Entra app client secret |
| `OFFICE365_TENANT_ID` | No | Directory (tenant) ID. Defaults to `common` (multi-tenant) |
| `QUICKLY_SECRET_KEY` | Recommended | JWT signing secret. Auto-generated if not set (tokens reset on restart) |
| `CORS_ORIGINS` | No | Comma-separated allowed origins for CORS (default: localhost dev setup) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, Tailwind CSS |
| Backend | Python 3.12, FastAPI, SQLAlchemy (async) |
| Database | PostgreSQL 15 |
| Email | Gmail API (OAuth2) · Microsoft Graph API (OAuth2) |
| Reverse Proxy | Caddy (automatic HTTPS) |
| Deployment | Docker, Docker Compose — Railway, Render, Fly.io, or any VPS |
| Scheduling | APScheduler (in-process background jobs, PostgreSQL job store) |
| Auth | JWT (HS256), bcrypt passwords, HMAC API keys, Fernet token encryption |
| Automation | Custom n8n node — [N8N.md](N8N.md) |

---

## Webhook Payload Example

```json
{
  "event": "lead.interested",
  "timestamp": "2026-03-02T14:52:03Z",
  "data": {
    "lead_email": "prospect@example.com",
    "lead_name": "Alice",
    "campaign_id": 3,
    "inbox_email": "outreach@yourdomain.com"
  }
}
```

> Full event reference with all 15 payload schemas: [WEBHOOKS.md](WEBHOOKS.md)

---

## Documentation

| Document | What's inside |
|---|---|
| [INSTALL.md](INSTALL.md) | **Interactive installation guide** — choose Railway, fresh VPS, nginx, or local dev, then follow step-by-step through Gmail / Office 365 / SMTP setup and first campaign |
| [INSTALL.md — Step 3B](INSTALL.md#step-3b-connect-office-365--outlook-inboxes) | Azure portal walkthrough for connecting Office 365 / Outlook inboxes |
| [API.md](API.md) | Complete REST API reference — all endpoints including auth, system health, provider matching, notifications |
| [N8N.md](N8N.md) | **n8n automation** — custom Quickly node, credentials, pointers to install and [full package README](../n8n-node/README.md) |
| [WEBHOOKS.md](WEBHOOKS.md) | All 15 event types, payload schemas, auth setup |
| [CONTRIBUTORS.md](CONTRIBUTORS.md) | How to contribute, dev environment setup |

---

## API Examples

```bash
# Authenticate and get a token
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "yourpassword"}'

# List all campaigns (with Bearer token)
curl http://localhost:8000/api/campaigns \
  -H "Authorization: Bearer <access_token>"

# Add leads to a campaign
curl -X POST http://localhost:8000/api/campaigns/1/leads \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '[{"email": "alice@example.com", "name": "Alice"}]'

# Check scheduler status
curl http://localhost:8000/api/status \
  -H "Authorization: Bearer <access_token>"
```

---

## Automating with n8n

Self-hosted Quickly pairs naturally with **self-hosted n8n**: the same API keys and base URL you use for scripts work in workflows. Quickly ships a **community node package** (`n8n-nodes-quickly`) that maps the REST API into a single **Quickly** node—choose a **resource** (Campaign, Lead, Inbox, Webhook, Unibox, …) and an **operation**, then fill the fields n8n shows for that pair. Credentials are stored once (**Quickly API**: base URL + API key or Bearer token), and the connection test hits `GET /api/auth/me` so you know n8n can reach your instance before you run a workflow.

You still use **[API.md](API.md)** whenever you need exact request shapes, query parameters, or response fields; the node is a typed UI on top of those routes. Outbound **webhooks** from Quickly into n8n (or anything else) are documented in **[WEBHOOKS.md](WEBHOOKS.md)**.

**Common patterns** people build with the Quickly n8n node:

- Sync interested leads into a CRM (HubSpot, Pipedrive, Notion)
- Slack or Telegram alerts when a lead is classified as `interested`
- Chain **Webhook** triggers from Quickly into enrichment, then **Campaign Lead → Add** to enroll new contacts
- Scheduled pulls of campaign analytics into Sheets or a data warehouse
- Form → n8n → **Lead** / **Campaign Lead** flows without maintaining raw `curl` in Code nodes

**Next step:** read **[N8N.md](N8N.md)** for a short overview and a direct link to the **[full package README](../n8n-node/README.md)** (build, `N8N_CUSTOM_EXTENSIONS`, Docker layout, CSV/binary behavior, manual IDs, troubleshooting).

---



Quickly is actively developed and contributions are welcome. If you've found a bug, have a feature request, or want to submit a PR, see [CONTRIBUTORS.md](CONTRIBUTORS.md) for guidelines.

If Quickly is saving you money or replacing a paid tool in your stack, **please consider dropping a ⭐ — it helps others find the project.**

---

## License

[MIT](../LICENSE) — free to use, modify, and self-host forever.

---

<p align="center">
  <strong>Stop paying per contact. Own your cold email stack.</strong><br/>
  <a href="#quick-start">Deploy Quickly in 5 minutes →</a>
</p>