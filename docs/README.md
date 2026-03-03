<p align="center">
  <img src="https://img.shields.io/badge/Self--Hosted-100%25-teal" alt="Self-Hosted" />
  <img src="https://img.shields.io/badge/License-MIT-blue" alt="License" />
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" alt="Docker" />
</p>

# Quickly

**Cold email infrastructure you own.** Quickly is a self-hosted email campaign platform that gives you full control over your outreach — no monthly fees, no per-seat pricing, no data leaving your server.

---

## Why Quickly?

| Pain point | Quickly's answer |
|---|---|
| SaaS tools charge per seat / per contact | One-time deploy, unlimited everything |
| Your data lives on someone else's servers | 100 % self-hosted — your VPS, your database |
| Complex setup with dozens of integrations | Single `docker compose up` and you're running |
| Difficult to customise sending behaviour | Open source, hackable, API-first |

---

## Features at a Glance

- **Multi-step sequences** — Build campaigns with any number of follow-up emails, each with configurable wait days and threading.
- **Smart queue** — Emails are scheduled across your inboxes respecting daily limits, business days, sending windows, and per-inbox cooldowns.
- **Multiple inboxes** — Spread sends across as many sending addresses as you want using **Gmail OAuth**. Run campaigns across multiple Gmail accounts simultaneously.
- **Open & click tracking** — Built-in pixel tracking and link wrapping with optional custom tracking domains (automatic HTTPS via Caddy).
- **Unified inbox (Unibox)** — Real-time Gmail sync with lead-reply detection, threaded view, and compose/reply — all inside the app.
- **Analytics dashboard** — Aggregated metrics, per-campaign progress, timeline charts, open rates, click rates, and reply rates.
- **Priority scheduling** — Drag campaigns to set send priority. Choose between priority-first or round-robin strategies.
- **Timezone support** — Set a timezone per campaign so schedules respect your recipients' local hours.
- **Webhooks** — Rich outbound webhook system: subscribe endpoints to any combination of 10 event types (email.sent, email.opened, email.clicked, email.bounced, lead.replied, lead.unsubscribed, lead.status_changed, daily_limit, rate_limit, token_expired). Multiple webhooks, bearer auth, per-webhook event filtering.
- **Test mode** — Preview every email before it goes out. Emails are simulated (not actually sent) so you can validate templates safely.
- **Full REST API** — 70+ endpoints covering every feature. Automate anything.
- **Dark mode** — Because of course.

---

## Quick Start

```bash
mkdir quickly && cd quickly

# Download the three files you need
curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/Caddyfile
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example

mv .env.example .env
# Edit .env — set CADDY_HOST, BASE_URL, and Google OAuth credentials

docker compose up -d
```

Open `https://yourdomain.com` — that's it. Caddy handles HTTPS automatically.

> See [INSTALL.md](INSTALL.md) for detailed installation guides covering fresh VPS, existing nginx, and local development setups.

---

## Screenshots

_Coming soon._

---

## How It Works

```
┌────────────┐     ┌──────────────┐     ┌─────────────┐
│  React UI  │────▶│  FastAPI      │────▶│  PostgreSQL  │
│  (Vite)    │◀────│  (Uvicorn)    │◀────│              │
└────────────┘     └──────┬───────┘     └─────────────┘
                          │
                   ┌──────▼───────┐
                   │  Send Job    │──▶ Gmail API (OAuth2)
                   │  (APScheduler)│
                   └──────────────┘
```

1. **Create inboxes** — Add your sending Gmail accounts (Gmail OAuth). Authorise each account with one click.
2. **Build campaigns** — Write multi-step sequences with subjects, HTML/text bodies, and wait days.
3. **Add leads** — Import leads into campaigns. The queue engine immediately reserves slots across your inboxes.
4. **Emails send automatically** — A background job checks the queue every minute and sends due emails, respecting daily limits, sending windows, and cooldowns.
5. **Track everything** — Opens, clicks, and replies are captured automatically. Webhooks notify your systems in real time.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, Tailwind CSS |
| Backend | Python 3.12, FastAPI, SQLAlchemy (async) |
| Database | PostgreSQL 15 |
| Email | Gmail API (OAuth2) |
| Deployment | Docker, Caddy (auto HTTPS) |
| Scheduling | APScheduler (in-process) |

---

## Documentation

| Document | Description |
|---|---|
| [INSTALL.md](INSTALL.md) | Full installation & deployment guide |
| [API.md](API.md) | Complete API reference (70+ endpoints) |
| [WEBHOOKS.md](WEBHOOKS.md) | Webhook setup, events & payloads |
| [CONTRIBUTORS.md](CONTRIBUTORS.md) | Contributing guidelines |

---

## Configuration

All runtime settings are managed from the **Settings** page in the web UI. Environment variables (`.env` file) are used for initial bootstrap only:

| Variable | Required | Description |
|---|---|---|
| `CADDY_HOST` | For HTTPS | Your domain name (e.g. `yourdomain.com`) |
| `BASE_URL` | Yes | Full URL including protocol (e.g. `https://yourdomain.com`) |
| `DATABASE_URL` | Auto | Set automatically by docker-compose; override for custom Postgres |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth client secret |
| `QUICKLY_MODE` | No | `production` (default in Docker) or `development` |

---

## API

Quickly exposes a comprehensive REST API with no authentication required (secure at the network level). Every UI action has an API equivalent.

```bash
# List campaigns
curl http://localhost:8000/api/campaigns

# Add leads to a campaign
curl -X POST http://localhost:8000/api/campaigns/1/leads \
  -H "Content-Type: application/json" \
  -d '[{"email": "alice@example.com", "name": "Alice"}]'

# Check scheduler status
curl http://localhost:8000/api/status
```

> Full reference: [API.md](API.md)

---

## Webhooks

Register multiple webhook endpoints, each subscribed to any combination of 10 event types. Quickly posts signed JSON payloads with a `Bearer` token you define.

```json
{
  "event": "email.opened",
  "timestamp": "2026-03-02T14:52:03Z",
  "data": {
    "lead_email": "prospect@example.com",
    "lead_name": "Alice",
    "campaign_id": 3,
    "inbox_email": "outreach@gmail.com"
  }
}
```

Events: `email.sent`, `email.opened`, `email.clicked`, `email.bounced`, `lead.replied`, `lead.unsubscribed`, `lead.status_changed`, `daily_limit`, `rate_limit`, `token_expired`.

> Full reference: [WEBHOOKS.md](WEBHOOKS.md)

---

## Contributing

Contributions are welcome! See [CONTRIBUTORS.md](CONTRIBUTORS.md) for guidelines.

---

## License

MIT
