<p align="center">
  <img src="https://img.shields.io/badge/Self--Hosted-100%25-teal" alt="Self-Hosted Cold Email Platform" />
  <img src="https://img.shields.io/badge/License-MIT-blue" alt="MIT License - Free Forever" />
  <img src="https://img.shields.io/badge/Docker-One--Command_Deploy-2496ED?logo=docker&logoColor=white" alt="Docker Ready" />
</p>

<h1 align="center">Quickly — Self-Hosted Cold Email Software for Unlimited Outreach</h1>

<p align="center"><strong>Own your cold email infrastructure. No SaaS fees. No per-seat pricing. No data leaving your server.</strong></p>

<p align="center">
  <a href="#quick-start">Deploy in 5 minutes →</a> &nbsp;|&nbsp;
  <a href="INSTALL.md">Full Install Guide</a> &nbsp;|&nbsp;
  <a href="API.md">API Docs</a> &nbsp;|&nbsp;
  <a href="WEBHOOKS.md">Webhooks</a>
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

### Smart Inbox Rotation & Warm-Up
Spread sends across as many inboxes as you want — **Gmail and Microsoft accounts are both supported**, including personal Outlook.com addresses and business Microsoft 365 / Office 365 accounts. Quickly's queue engine respects per-inbox daily limits, business-day schedules, configurable sending windows, and per-inbox cooldowns. New accounts? The **ramp-up warm-up** gradually increases daily send volume over a configurable period to protect deliverability.

### AI-Powered Reply Classification
Every reply is automatically classified by AI into one of six categories: `interested`, `not_interested`, `out_of_office`, `wrong_person`, `auto_reply`, or `unsubscribed`. Supports 19 AI providers including OpenAI, Anthropic Claude, Google Gemini, Mistral, Groq, and Ollama (for fully offline classification).

### Unified Inbox (Unibox)
A built-in inbox sync layer gives you real-time reply detection, threaded conversation view, and compose/reply — all inside Quickly. Works across your Gmail and Microsoft accounts side-by-side. No tab-switching. No missed replies.

### Open & Click Tracking with Custom Domains
Built-in pixel tracking and link wrapping capture every open and click. Bring your own tracking domain with automatic HTTPS via Caddy. Filter out your own opens and clicks by registering known IPs.

### Full Analytics Dashboard
See aggregated metrics, per-campaign breakdowns, per-step performance, timeline charts, open rates, click rates, reply rates, bounces, and unsubscribes — all in one view.

### Webhooks — React to Every Email Event
Register any number of webhook endpoints and subscribe them to any combination of **15 real-time events**:

`email.sent` · `email.opened` · `email.clicked` · `email.bounced` · `lead.replied` · `lead.unsubscribed` · `lead.status_changed` · `lead.interested` · `lead.not_interested` · `lead.out_of_office` · `lead.wrong_person` · `lead.auto_reply` · `daily_limit` · `rate_limit` · `token_expired`

Each webhook supports Bearer token authentication and per-endpoint event filtering. Connect Quickly to your CRM, Slack, Zapier, or any custom pipeline.

### Email Verification
Verify lead addresses before sending via mailtester.ninja or any custom HTTP provider. Trigger verification per campaign with live status tracking — stop wasting sends on bad addresses.

### Priority Scheduling & Timezone Awareness
Drag campaigns to reorder send priority. Choose **priority-first** (exhaust the top campaign's slots first) or **round-robin** (spread sends evenly). Set a timezone per campaign so emails land during your recipients' business hours, not yours.

### CSV Import / Export, Full REST API & n8n Integration
Bulk-import leads from CSV in seconds. Export any campaign's leads with full open/click/reply status at any time. Automate everything with 90+ REST API endpoints — every UI action has an API equivalent. For no-code automation, Quickly ships with a **custom n8n node** that exposes all API endpoints directly in your n8n workflows, making it trivial to wire Quickly into any automation pipeline without writing a line of code.

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

1. **Connect inboxes** — Authorize Gmail accounts or Microsoft 365 / Outlook accounts with one OAuth click each.
2. **Build sequences** — Write multi-step campaigns with subjects, bodies, and wait days.
3. **Import leads** — Upload a CSV; the queue engine instantly reserves send slots across inboxes.
4. **Emails go out automatically** — A background job runs every minute, respecting limits, windows, and cooldowns.
5. **Track everything** — Opens, clicks, replies, AI classifications, and webhook events fire in real time.

---

## Quick Start

**Option 1: Deploy on Railway (free, no server needed)**

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app)

Click the button above to spin up Quickly on [Railway](https://railway.com) in seconds — no VPS, no DevOps, completely free to start. Railway handles the infrastructure; you just configure your OAuth credentials and go.

**Option 2: Self-host with Docker (any VPS or PaaS)**

```bash
mkdir quickly && cd quickly

curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/Caddyfile
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example

mv .env.example .env
# Set CADDY_HOST, BASE_URL, and your OAuth credentials in .env

docker compose up -d
```

**That's it.** Open `https://yourdomain.com` — Caddy provisions HTTPS automatically.

Quickly is optimized to run on any Docker-compatible platform: Railway, Render, Fly.io, a bare VPS, or your own machine. There's no mandatory infrastructure cost.

> Need a step-by-step walkthrough? [INSTALL.md](INSTALL.md) covers Railway deployment, fresh VPS setup, existing Nginx setups, and local development.

---

## Frequently Asked Questions

**Is Quickly really free?**
Yes. Quickly is MIT-licensed open-source software with no per-contact fees, no seat limits, and no feature tiers. You can run it completely free on platforms like Railway — no server costs at all. If you prefer full control, deploying to your own VPS typically costs $5–$20/month.

**What email providers does Quickly support?**
Quickly supports **Gmail** (via Google OAuth) and **Microsoft accounts** — including personal Outlook.com addresses and business Microsoft 365 / Office 365 accounts (via Microsoft Graph API OAuth2). You can mix Gmail and Microsoft inboxes in the same campaign.

**How is Quickly different from Instantly or Smartlead?**
Instantly and Smartlead are hosted SaaS products that store your data on their servers and charge monthly fees per inbox or contact. Quickly runs entirely on your infrastructure — your leads never leave your server.

**Does Quickly support AI reply classification?**
Yes. Quickly integrates with 19 AI providers (OpenAI, Anthropic, Gemini, Groq, Mistral, Ollama, and more) to automatically classify replies into categories like `interested`, `not_interested`, and `out_of_office`.

**Can I use Quickly with a team?**
Yes. There are no seat limits. Deploy once, give your entire team access.

**Is there an API? Can I use it with n8n?**
Yes — 90+ REST endpoints covering every feature in the UI. Quickly also ships with a **custom n8n node** that wraps all API endpoints, so you can build automation workflows in n8n without writing any code. See [API.md](API.md) for the full reference.

**What happens when someone unsubscribes?**
Quickly automatically catches unsubscribe replies via AI classification and marks those leads so they never receive another email.

---

## Environment Configuration

| Variable | Required | Description |
|---|---|---|
| `CADDY_HOST` | For HTTPS | Your domain (e.g. `mail.yourdomain.com`) |
| `BASE_URL` | Yes | Full URL with protocol (e.g. `https://mail.yourdomain.com`) |
| `DATABASE_URL` | Auto | Set by docker-compose; override for external Postgres |
| `GOOGLE_CLIENT_ID` | For Gmail | Google OAuth 2.0 client ID |
| `GOOGLE_CLIENT_SECRET` | For Gmail | Google OAuth 2.0 client secret |
| `MICROSOFT_CLIENT_ID` | For Outlook/M365 | Microsoft Entra app client ID |
| `MICROSOFT_CLIENT_SECRET` | For Outlook/M365 | Microsoft Entra app client secret |
| `QUICKLY_MODE` | No | `production` (default in Docker) or `development` |

All other settings — AI provider keys, sending windows, warm-up schedules, tracking domains — are managed from the **Settings page** in the UI after deploy.

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
| Scheduling | APScheduler (in-process background jobs) |
| Automation | Custom n8n node (all 90+ endpoints) |

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
| [INSTALL.md](INSTALL.md) | Railway deploy, VPS setup, Nginx config, local dev, OAuth guides (Google & Microsoft) |
| [API.md](API.md) | Complete REST API reference — 90+ endpoints |
| [WEBHOOKS.md](WEBHOOKS.md) | All 15 event types, payload schemas, auth setup |
| [CONTRIBUTORS.md](CONTRIBUTORS.md) | How to contribute, dev environment setup |

---

## API Examples

```bash
# List all campaigns
curl http://localhost:8000/api/campaigns

# Add leads to a campaign
curl -X POST http://localhost:8000/api/campaigns/1/leads \
  -H "Content-Type: application/json" \
  -d '[{"email": "alice@example.com", "name": "Alice"}]'

# Check scheduler status
curl http://localhost:8000/api/status
```

---

## Automation with n8n

Quickly ships with a **custom n8n node** that gives you drag-and-drop access to every Quickly API endpoint inside your n8n workflows. No custom HTTP request nodes, no manual auth setup — just connect and build.

Common automation patterns people build with the Quickly n8n node:

- Sync interested leads directly into your CRM (HubSpot, Pipedrive, Notion)
- Post Slack notifications when a lead replies as `interested`
- Trigger follow-up sequences based on webhook events
- Pull campaign analytics into Google Sheets on a schedule
- Add leads from form submissions or enrichment tools automatically

> Full API reference: [API.md](API.md) · Webhook event reference: [WEBHOOKS.md](WEBHOOKS.md)

---



Quickly is actively developed and contributions are welcome. If you've found a bug, have a feature request, or want to submit a PR, see [CONTRIBUTORS.md](CONTRIBUTORS.md) for guidelines.

If Quickly is saving you money or replacing a paid tool in your stack, **please consider dropping a ⭐ — it helps others find the project.**

---

## License

MIT — free to use, modify, and self-host forever.

---

<p align="center">
  <strong>Stop paying per contact. Own your cold email stack.</strong><br/>
  <a href="#quick-start">Deploy Quickly in 5 minutes →</a>
</p>