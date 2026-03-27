<p align="center">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT License" />
  <img src="https://img.shields.io/badge/Self--Hosted-100%25-teal" alt="Self-Hosted Cold Email Platform" />
  <img src="https://img.shields.io/badge/Docker-One--Command_Deploy-2496ED?logo=docker&logoColor=white" alt="Docker One-Command Deploy" />
  <img src="https://img.shields.io/badge/PRs-Welcome-brightgreen.svg" alt="PRs Welcome" />
  <img src="https://img.shields.io/badge/AI_Reply_Classification-19_Providers-orange" alt="AI Reply Classification" />
</p>

<h1 align="center">Quickly — Open-Source Self-Hosted Cold Email Platform</h1>

<p align="center"><strong>A simple, focused cold email platform you host yourself. Built to feel just like Instantly, Smartlead, and Lemlist — but free and fully under your control.</strong></p>

<p align="center">
  <a href="docs/INSTALL.md">Installation Guide</a> &nbsp;|&nbsp;
  <a href="docs/API.md">API Docs</a> &nbsp;|&nbsp;
  <a href="docs/MCP.md">MCP (AI agents)</a> &nbsp;|&nbsp;
  <a href="docs/WEBHOOKS.md">Webhooks</a> &nbsp;|&nbsp;
  <a href="docs/CONTRIBUTORS.md">Contributing</a>
</p>

---
![Quickly dashboard showing campaign analytics and inbox rotation](docs/assets/app_demo.gif)
---

## What is Quickly?

**Quickly is a free, open-source cold email platform designed to be extremely simple and purpose-built for cold outreach.** It gives sales teams, agencies, and indie hackers the same multi-inbox sequencing, AI reply classification, and campaign analytics you expect from tools like Instantly, Smartlead, and Lemlist — but self-hosted on your own infrastructure.

Emails are sent through **Google and Microsoft’s official APIs using OAuth 2.0**, meaning your campaigns use **Gmail and Microsoft 365's own sending infrastructure and IP reputation** rather than shared SMTP servers. The result is deliverability that matches — and often exceeds — traditional SaaS cold email tools.

Quickly focuses on doing **cold email extremely well**, without unnecessary complexity. If you've used Instantly, Smartlead, or Lemlist before, you'll feel immediately at home.

```bash
# Get up and running in under 5 minutes
docker compose up -d
```

If this saves you money, please consider dropping a ⭐ — it helps others find the project.

---

## Why Quickly? (vs. Instantly, Smartlead, Lemlist)

| What you're paying for today             | What Quickly gives you instead                         |
| ---------------------------------------- | ------------------------------------------------------ |
| $97–$500/month in SaaS fees              | Deploy free on Railway or any Linux VPS                |
| Per-seat pricing that scales against you | Unlimited team members, unlimited contacts             |
| Your lead data on vendor servers         | 100% self-hosted — your server, your Postgres database |
| Vendor lock-in and feature gating        | MIT licensed, fully hackable, 90+ REST API endpoints   |
| Complex multi-tool onboarding            | One `docker compose up` — live in under 5 minutes      |

> **Quickly is the open-source alternative to Instantly, Smartlead, and Lemlist** — built to feel familiar, but simpler, self-hosted, and fully under your control.

---

## Features

### Core Sending

* **Multi-step email sequences** — Unlimited follow-up steps, configurable wait days, automatic reply threading, HTML or plain-text bodies
* **Smart inbox rotation** — Spread sends across unlimited Gmail and Microsoft 365 inboxes, with per-inbox daily limits respected automatically
* **OAuth2 sending via Gmail & Microsoft 365** — Emails are sent using Google and Microsoft’s official APIs, leveraging their trusted infrastructure and IP reputation instead of shared SMTP servers
* **Warm-up scheduling** — Built-in ramp-up and configurable send-time jitter to protect deliverability
* **Bounce detection** — Permanent failures are caught automatically; affected leads are stopped to protect sender reputation

### Personalization & Testing

* **Template variables** — Use `{{name}}`, `{{company}}`, `{{title}}`, or any custom field from your CSV in subject lines and email bodies
* **A/B testing** — Multiple subject/body variants per step; Quickly tracks open rates, click rates, and reply rates per variant and selects randomly at send time
* **CSV import** — Any CSV column beyond `email` automatically becomes a template variable

### Inbox & Replies

* **Unified inbox (Unibox)** — Real-time reply detection across all connected Gmail and Microsoft 365 accounts, with threaded view and compose/reply
* **AI reply classification** — Automatically classifies every reply as `interested`, `not_interested`, `out_of_office`, `wrong_person`, `auto_reply`, or `unsubscribed`. Supports 19 AI providers including **Ollama** for fully offline, on-premise classification
* **Lead provider matching** — DNS lookup routes sends through inboxes that match the lead's email provider

### Tracking & Analytics

* **Open & click tracking** — Pixel tracking and link wrapping with custom domain support (automatic HTTPS via Caddy)
* **Full analytics** — Per-campaign stats, per-step performance, open/click/reply rates, and timeline charts
* **System health dashboard** — Live status for inbox token health, tracking domain reachability, and AI provider connectivity

### Developer & Automation

* **Webhooks** — 15 real-time event types: `email.sent`, `email.opened`, `email.clicked`, `email.bounced`, `lead.replied`, `lead.interested`, `lead.unsubscribed`, `lead.status_changed`, and more
* **REST API** — 90+ endpoints secured by JWT auth and API keys. Custom n8n node included
* **MCP (Model Context Protocol)** — Remote Streamable HTTP server at `/api/mcp` for leads tools; connect with `mcp-remote` from Cursor or other clients ([docs/MCP.md](docs/MCP.md))
* **Email verification** — Verify lead addresses before sending via any HTTP provider
* **Test mode** — Simulate sends, opens, and clicks without delivering real emails
* **Priority scheduling** — Drag campaigns to set priority; choose priority-first or round-robin strategies

---

## Quick Start

### Option A: Deploy on Railway (Fastest — no server needed)

> Up and running in under 5 minutes with automatic HTTPS. Railway has a free tier.

1. Create a new project on [Railway](https://railway.com) → **Deploy a Docker image** → `azowail/quickly:latest`
2. Add a **PostgreSQL** database plugin — Railway injects `DATABASE_URL` automatically
3. Set your environment variables (see [Environment Variables](#environment-variables) below)
4. Deploy — Railway provides a public HTTPS URL automatically

→ [Full Railway walkthrough](docs/INSTALL.md#option-a-railway--paas-no-server-needed)

---

### Option B: Self-host on a VPS (Docker Compose)

```bash
docker volume create quickly_pgdata   # Postgres data — do once before first start

mkdir quickly && cd quickly
curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/Caddyfile
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example
mv .env.example .env
```

Edit `.env` and set at minimum:

```env
CADDY_HOST=yourdomain.com
BASE_URL=https://yourdomain.com
QUICKLY_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(64))">
```

```bash
docker compose up -d
```

Caddy automatically provisions and renews a Let's Encrypt TLS certificate. Open `https://yourdomain.com` — Quickly is live.

**Already running Caddy on the server?** Download `docker-compose-not-host.yml` from the same release instead, start with `docker compose -f docker-compose-not-host.yml up -d`, and add `reverse_proxy 127.0.0.1:5050` for your domain in the host Caddyfile.

→ [Full VPS guide, firewall, Postgres volume, and not-host layout](docs/INSTALL.md#option-b-vps-with-docker-compose)

---

### Option C: Local Development

```bash
git clone https://github.com/azowail/quickly.git
cd quickly

python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

pip install -r requirements.txt
cp .env.example .env
# Edit .env: set BASE_URL=http://localhost:8000

uvicorn app.main:app --reload    # API at http://localhost:8000

# In a separate terminal:
cd frontend && npm install && npm run dev   # UI at http://localhost:5173
```

→ [Full local dev guide including Docker dev stack](docs/INSTALL.md#option-d-local-development)

---

## First Login

On first deploy, visit your Quickly URL — a registration form appears to create the initial admin account. **Registration closes after the first account is created.** Additional users must be invited by an admin from the Settings page.

---

## Connecting Inboxes

Quickly supports Gmail (via Google OAuth) and Microsoft 365 / Outlook (via Azure OAuth). Gmail and Microsoft inboxes can be mixed freely in the same campaign.

* → [Connect Gmail inboxes (Google Cloud OAuth setup)](docs/INSTALL.md#step-3a-connect-gmail-inboxes)
* → [Connect Office 365 / Outlook inboxes (Azure portal setup)](docs/INSTALL.md#step-3b-connect-office-365--outlook-inboxes)

---

## Environment Variables

The `.env` file is intentionally minimal. All runtime settings — AI providers, sending windows, warm-up schedules, tracking domains — are configured from the **Settings page** in the UI after deployment.

| Variable                  | Required       | Description                                                                       |
| ------------------------- | -------------- | --------------------------------------------------------------------------------- |
| `BASE_URL`                | **Yes**        | Full URL with protocol, e.g. `https://yourdomain.com`                             |
| `CADDY_HOST`              | For HTTPS      | Your domain — Caddy auto-provisions a Let's Encrypt certificate                   |
| `DATABASE_URL`            | Auto           | Set by `docker-compose.yml`; only override for an external Postgres instance      |
| `GOOGLE_CLIENT_ID`        | For Gmail      | Google OAuth 2.0 client ID                                                        |
| `GOOGLE_CLIENT_SECRET`    | For Gmail      | Google OAuth 2.0 client secret                                                    |
| `OFFICE365_CLIENT_ID`     | For Office 365 | Microsoft Entra app (client) ID                                                   |
| `OFFICE365_CLIENT_SECRET` | For Office 365 | Microsoft Entra client secret                                                     |
| `OFFICE365_TENANT_ID`     | No             | Defaults to `common` (multi-tenant); set to your tenant ID for single-tenant orgs |
| `QUICKLY_SECRET_KEY`      | Recommended    | JWT signing secret                                                                |
| `CORS_ORIGINS`            | No             | Comma-separated allowed origins                                                   |

See [`.env.example`](.env.example) for the full annotated reference.

---

## Tech Stack

| Layer         | Technology                                                |
| ------------- | --------------------------------------------------------- |
| Frontend      | React 18, Vite, Tailwind CSS                              |
| Backend       | Python 3.12, FastAPI, SQLAlchemy 2.0 (async)              |
| Database      | PostgreSQL 15                                             |
| Email APIs    | Gmail API (OAuth2) · Microsoft Graph API (OAuth2)         |
| Reverse Proxy | Caddy (automatic HTTPS + custom tracking domains)         |
| Scheduling    | APScheduler (in-process, PostgreSQL job store)            |
| Auth          | JWT HS256, bcrypt, HMAC API keys, Fernet token encryption |
| Automation    | Custom n8n node (covers all API endpoints)                |
| AI agent I/O  | MCP over HTTPS (`/api/mcp`) — leads tools via `mcp-remote` |

## Documentation

| Document | What's inside |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | **Full installation guide** — Railway, VPS, nginx migration, local dev, Gmail & Office 365 OAuth setup |
| [docs/API.md](docs/API.md) | Complete REST API reference — 90+ endpoints |
| [docs/MCP.md](docs/MCP.md) | **MCP for AI clients** — endpoint, auth, tools, Cursor / `mcp-remote` config, troubleshooting |
| [docs/WEBHOOKS.md](docs/WEBHOOKS.md) | All 15 webhook event types, payload schemas, and authentication |
| [docs/CONTRIBUTORS.md](docs/CONTRIBUTORS.md) | Contributing guidelines and local dev environment setup |

---

## Frequently Asked Questions

**Is Quickly really free?**
Yes. Quickly is MIT licensed. You pay nothing to Quickly — your only cost is the server you host it on (or Railway's free tier).

**How does Quickly compare to Instantly or Smartlead?**
Quickly covers the core features both tools offer: multi-step sequences, inbox rotation, A/B testing, AI reply classification, open/click tracking, and a unified inbox. The difference is ownership — your data stays on your server, and there are no monthly fees or seat limits.

**Does Quickly support Microsoft 365 / Outlook?**
Yes. Quickly connects to Gmail via Google OAuth and to Microsoft 365, Office 365, and personal Outlook.com accounts via Microsoft Graph. Both inbox types can be mixed in the same campaign.

**Can I use Quickly without a custom domain?**
Yes. You can deploy on Railway and use the generated subdomain, or run Quickly over plain HTTP without `CADDY_HOST` set.

**Does AI reply classification require an OpenAI key?**
No. Quickly supports 19 AI providers including Anthropic, Google Gemini, Mistral, Groq, Cohere, and Ollama — which lets you run classification fully locally with no external API calls.

---

## Contributing

Contributions are welcome — bug reports, feature requests, and pull requests. See [docs/CONTRIBUTORS.md](docs/CONTRIBUTORS.md) for setup instructions and contribution guidelines.

If Quickly is saving you money or replacing a paid tool in your workflow, a ⭐ goes a long way toward helping others find it.

---

## License

[MIT](LICENSE) — free to use, modify, and self-host forever.
