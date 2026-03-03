# API Reference

Quickly exposes a JSON REST API over HTTP. **No authentication is required** — the service is designed as a personal tool and access should be restricted at the network level (firewall, VPN, or reverse-proxy auth).

All request/response bodies use `Content-Type: application/json`.

> **Base URL:** `http://localhost:8000` (or your configured domain)

---

## Table of Contents

- [Status](#status)
- [Campaigns](#campaigns)
- [Sequences](#sequences)
- [Leads](#leads)
- [Inboxes](#inboxes)
- [Schedule](#schedule)
- [Settings](#settings)
- [Webhooks](#webhooks)
- [Gmail OAuth](#gmail-oauth)
- [Unibox](#unibox)
- [Tracking](#tracking)
- [Test Mode](#test-mode)

---

## Status

### `GET /api/status`

Health check and scheduler status.

**Response:**

```json
{
  "schedule_running": true,
  "queue_check_interval_minutes": 1,
  "last_send_job_run": "2026-01-15T10:30:00Z",
  "last_send_job_sent_count": 5,
  "next_send_job_run": "2026-01-15T10:31:00Z",
  "test_mode": false,
  "app_mode": "production"
}
```

---

## Campaigns

### `GET /api/campaigns`

List all campaigns with aggregated stats.

**Response:** Array of campaign objects, each including a `stats` object:

```json
{
  "id": 1,
  "name": "Q1 Outreach",
  "inbox_ids": [1, 2],
  "sending_days": "1,2,3,4,5",
  "sending_hours_start": 9,
  "sending_hours_end": 17,
  "wait_minutes_between": 5,
  "stop_on_reply": true,
  "paused": false,
  "priority": 1,
  "timezone": "America/New_York",
  "track_opens": true,
  "track_clicks": true,
  "stats": {
    "total_leads": 150,
    "emails_sent": 300,
    "scheduled": 450,
    "total_opens": 120,
    "unique_opens": 80,
    "total_clicks": 45,
    "unique_clicks": 30,
    "replies": 15,
    "sequences": 3
  }
}
```

### `GET /api/campaigns/has-leads`

Check whether any campaign has enrolled leads. Useful before changing scheduling strategy.

**Response:** `{ "has_leads": true }`

### `POST /api/campaigns`

Create a new campaign.

**Body:**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | Yes | — | Campaign name |
| `inbox_ids` | int[] | Yes | — | Sending inboxes |
| `sending_days` | string | No | `"1,2,3,4,5"` | Comma-separated ISO weekdays (1=Mon) |
| `sending_hours_start` | int | No | `9` | Start of daily sending window (0-23) |
| `sending_hours_end` | int | No | `17` | End of daily sending window (0-23) |
| `wait_minutes_between` | int | No | `5` | Minimum minutes between sends per inbox |
| `stop_on_reply` | bool | No | `true` | Stop sending to a lead after they reply |
| `paused` | bool | No | `false` | Start campaign in paused state |
| `priority` | int | No | auto | Priority order (lower = higher priority) |
| `timezone` | string | No | `null` | IANA timezone (e.g. `America/New_York`) |
| `track_opens` | bool | No | `true` | Enable open tracking pixel |
| `track_clicks` | bool | No | `true` | Enable click tracking |
| `add_unsubscribe_header` | bool | No | `false` | Add List-Unsubscribe header |
| `send_first_as_text` | bool | No | `false` | Send first sequence as plain text |
| `send_all_as_text` | bool | No | `false` | Send all sequences as plain text |

**Response:** Campaign object

### `GET /api/campaigns/{id}`

Get a single campaign with full stats.

### `PATCH /api/campaigns/{id}`

Update campaign fields. Only include the fields you want to change. Triggers queue recalculation if schedule-related fields change.

**Body:** Any fields from `POST /api/campaigns` (all optional)

### `DELETE /api/campaigns/{id}`

Delete a campaign and all associated data (sequences, queue slots, logs). Leads that aren't enrolled in other campaigns are also deleted.

### `POST /api/campaigns/{id}/duplicate`

Duplicate a campaign including all sequences. Leads are not copied.

**Response:** The newly created campaign object.

### `POST /api/campaigns/reorder`

Set the priority order of all campaigns.

**Body:**

```json
{ "campaign_ids": [3, 1, 2] }
```

**Response:** `{ "ok": true, "order": [3, 1, 2] }`

---

## Sequences

Sequences are the individual email steps within a campaign. Each sequence has a position, optional subject (empty = thread reply), body, and wait days.

### `GET /api/campaigns/{id}/sequences`

List all sequences for a campaign, ordered by position.

### `POST /api/campaigns/{id}/sequences`

Add a new sequence step. Triggers queue recalculation.

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `position` | int | Yes | Step number (1-based) |
| `subject` | string | No | Email subject (empty = reply in thread) |
| `body` | string | Yes | Email body (HTML or plain text) |
| `wait_days_after_previous` | int | Yes | Days to wait after previous step |
| `is_html` | bool | No | Whether body is HTML |

### `PATCH /api/campaigns/{id}/sequences/{seq_id}`

Update a sequence. Recalculates queue if `wait_days_after_previous` changes.

### `DELETE /api/campaigns/{id}/sequences/{seq_id}`

Delete a sequence. Remaining sequences are renumbered. Triggers queue recalculation.

---

## Leads

Leads represent contacts enrolled in campaigns. Creating standalone leads is not supported — always add leads through a campaign.

### `GET /api/leads`

List all leads. Supports optional query parameter `?status=active|unsubscribed|bounced|replied`.

### `GET /api/leads/{id}`

Get a single lead.

### `PATCH /api/leads/{id}`

Update lead fields. Status changes trigger full queue recalculation.

**Body:**

| Field | Type | Description |
|---|---|---|
| `name` | string | Lead name |
| `custom_data` | object | Arbitrary key-value data for template substitution |
| `status` | string | `active`, `unsubscribed`, `bounced`, or `replied` |

### `DELETE /api/leads/{id}`

Delete a lead and all associated logs and replies. Triggers recalculation.

### `POST /api/leads/mark-replied`

Mark a lead as having replied to a specific campaign.

**Body:** `{ "lead_id": 42, "campaign_id": 1 }`

### `GET /api/leads/{id}/history`

Email send history for a lead across all campaigns.

**Response:**

```json
[
  {
    "campaign_id": 1,
    "campaign_name": "Q1 Outreach",
    "sequence_index": 0,
    "sent_at": "2026-01-15T10:30:00Z",
    "subject": "Quick question"
  }
]
```

### `POST /api/campaigns/{id}/leads`

Bulk add leads to a campaign. Creates leads that don't exist yet and schedules queue slots.

**Body:** Array of lead objects:

```json
[
  { "email": "alice@example.com", "name": "Alice", "custom_data": { "company": "Acme" } },
  { "email": "bob@example.com", "name": "Bob" }
]
```

**Response:**

```json
{
  "ok": true,
  "added": 2,
  "already_enrolled": 0,
  "errors": 0,
  "results": [
    { "email": "alice@example.com", "status": "added", "lead_id": 42, "slots_created": 3 }
  ]
}
```

### `GET /api/campaigns/{id}/leads`

List leads enrolled in a campaign with progress info.

**Response:** Array of objects with `lead_id`, `email`, `name`, `status`, `stage`, `opened`, `clicked`, `replied`.

### `DELETE /api/campaigns/{id}/leads/{lead_id}`

Remove a lead from a campaign and delete their pending queue slots.

---

## Inboxes

Inboxes are sending email addresses using Gmail OAuth. Each inbox has its own daily limit and optional custom tracking domain.

### `GET /api/inboxes`

List all inboxes. Each includes a `sent_today` count.

### `POST /api/inboxes`

Create an inbox.

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Sending email address |
| `display_name` | string | No | Sender display name |
| `max_emails_per_day` | int | No | Daily sending limit |
| `wait_minutes_between` | int | No | Cooldown between sends |
| `provider` | string | No | Always `gmail` |
| `tracking_domain` | string | No | Custom tracking domain hostname |

### `GET /api/inboxes/{id}`

Get a single inbox with today's sent count.

### `PATCH /api/inboxes/{id}`

Update inbox fields. Triggers queue recalculation if capacity changes.

### `DELETE /api/inboxes/{id}`

Delete an inbox. Fails if the inbox is assigned to campaigns or has pending queue slots.

---

## Schedule

View and manage the global sending schedule across all campaigns.

### `GET /api/schedule/sent`

All sent emails across all campaigns, including open and click event details.

**Response:** Array of sent email objects with `opens[]`, `clicks[]`, `opened`, `clicked` flags.

### `GET /api/schedule/scheduled`

All upcoming queue slots across all campaigns.

### `GET /api/schedule/stats`

Quick summary counts.

**Response:** `{ "total_sent": 500, "total_scheduled": 200, "total_campaigns": 5 }`

### `POST /api/schedule/sent/{log_id}/open`

Manually record an open event on a sent email.

**Body:** `{ "ip": "1.2.3.4" }` (optional)

### `POST /api/schedule/sent/{log_id}/click`

Manually record a click event on a sent email.

**Body:** `{ "ip": "1.2.3.4" }` (optional)

### `POST /api/schedule/validate-queue`

Run validation checks on all scheduled emails. Reports issues like slots assigned to paused campaigns, missing inboxes, or capacity violations.

**Response:**

```json
{
  "ok": true,
  "total_slots_checked": 500,
  "total_leads_checked": 100,
  "total_capacity": 50,
  "issues": [],
  "has_errors": false
}
```

### `POST /api/schedule/recalculate-all`

Full queue rebuild across all campaigns using the active scheduling strategy.

**Response:** `{ "ok": true, "strategy": "priority", "campaigns_processed": 5, "total_slots": 800 }`

---

## Settings

### `GET /api/settings/scheduling-strategy`

Get the active scheduling strategy (`priority` or `round_robin`).

### `POST /api/settings/scheduling-strategy`

Set the scheduling strategy. Triggers a background recalculation of all queues.

**Body:** `{ "scheduling_strategy": "priority" }`

### `GET /api/settings/time-offset`

Get the current time offset in days (used for testing / simulation).

### `POST /api/settings/time-offset`

Set the time offset.

**Body:** `{ "time_offset_days": 2 }`

### `GET /api/settings/test-mode`

Check if test mode is enabled.

### `POST /api/settings/test-mode`

Enable or disable test mode.

**Body:** `{ "test_mode": true }`

### `POST /api/settings/add-opens`

Debug utility: attach a synthetic open event to every sent email log.

### `GET /api/settings/server-info`

Get the server's base URL and CNAME target (used for custom tracking domain setup).

### `GET /api/settings/verify-tracking-domain`

Verify that a custom tracking domain resolves correctly to this server.

**Query param:** `?domain=mail.example.com`

---

## Webhooks

Quickly supports multiple outbound webhooks. Each webhook subscribes to any combination of event types and is called with a signed JSON body.

### `GET /api/settings/webhooks/events`

List all supported event type strings.

**Response:** `["email.sent", "email.opened", "email.clicked", "email.bounced", "lead.replied", "lead.unsubscribed", "lead.status_changed", "daily_limit", "rate_limit", "token_expired"]`

### `GET /api/settings/webhooks`

List all configured webhooks.

**Response:** Array of webhook objects:

```json
[
  {
    "id": 1,
    "url": "https://example.com/hook",
    "secret": "mytoken",
    "events": ["email.sent", "lead.replied"],
    "active": true,
    "description": "CRM integration",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  }
]
```

### `POST /api/settings/webhooks`

Create a new webhook.

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | string | Yes | HTTPS endpoint to POST to |
| `secret` | string | No | Bearer token for `Authorization` header |
| `events` | string[] | Yes | List of event types to subscribe to |
| `active` | bool | No | Whether to fire this webhook (default `true`) |
| `description` | string | No | Human-readable label |

### `PATCH /api/settings/webhooks/{id}`

Update a webhook. All fields are optional.

### `DELETE /api/settings/webhooks/{id}`

Delete a webhook.

### `POST /api/settings/webhooks/{id}/test`

Fire a test payload to the webhook. Returns `{ "ok": true }` if the endpoint responds with 2xx, or an error detail otherwise.

---

## Gmail OAuth

### `GET /api/gmail/status`

Check if Google OAuth credentials (client ID + secret) are configured on the server.

**Response:** `{ "configured": true, "redirect_uri": "https://yourdomain.com/oauth/google/callback" }`

### `GET /api/gmail/accounts`

List all connected Gmail accounts.

**Response:**

```json
[
  {
    "id": 1,
    "inbox_id": 3,
    "google_email": "outreach@gmail.com",
    "inbox_email": "outreach@gmail.com",
    "inbox_display_name": "Sales Team",
    "max_emails_per_day": 50,
    "token_expiry": "2026-01-15T12:00:00Z",
    "connected_at": "2026-01-01T00:00:00Z"
  }
]
```

### `GET /api/gmail/permissions`

Check OAuth scopes and permissions for all connected Gmail accounts.

### `GET /oauth/google/authorize`

Start the Google OAuth flow. Redirects the user to Google's consent screen.

**Query params:** `display_name` (optional), `max_per_day` (optional)

### `GET /oauth/google/callback`

OAuth callback handler. Creates or updates the inbox and Gmail account, then redirects to `/inboxes`.

### `DELETE /api/gmail/accounts/{id}`

Disconnect a Gmail account. Removes stored tokens and reverts the inbox provider.

---

## Unibox

Unified inbox for reading and replying to Gmail threads.

### `GET /api/unibox`

List conversations (paginated).

**Query params:** `page`, `page_size`, `leads_only` (show only lead threads)

### `GET /api/unibox/status`

Sync status for unibox inboxes.

**Query param:** `inbox_id` (optional, for a specific inbox)

### `GET /api/unibox/notifications`

Count of threads with unread lead replies.

**Response:** `{ "count": 3 }`

### `GET /api/unibox/threads/{thread_id}`

Get all messages in a thread. Hydrates message content on demand.

**Query param:** `inbox_id` (optional)

### `POST /api/unibox/threads/{thread_id}/mark-read`

Mark a thread's unread-lead-reply flag as cleared.

**Query param:** `inbox_id`

### `POST /api/unibox/sync`

Trigger a manual Gmail sync for one or all inboxes.

**Body:** `{ "inbox_id": 3 }` (optional; omit to sync all)

### `POST /api/unibox/load-more`

Trigger backfill — load older messages for one or all inboxes.

**Body:** `{ "inbox_id": 3, "window_days": 30 }`

### `POST /api/unibox/send`

Send an email via Gmail from the unibox.

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `inbox_id` | int | Yes | Sending inbox |
| `to_email` | string | Yes | Recipient |
| `subject` | string | Yes | Email subject |
| `body` | string | Yes | Email body |
| `thread_id` | string | No | Reply to existing thread |
| `in_reply_to` | string | No | Message-ID header for threading |
| `references` | string | No | References header for threading |
| `is_html` | bool | No | Whether body is HTML |

### `GET /api/unibox/events`

Server-Sent Events (SSE) stream for real-time unibox updates (new messages, sync status changes).

### `POST /api/unibox/gmail/push`

Google Pub/Sub push notification endpoint. Called automatically by Gmail when new messages arrive.

---

## Tracking

These endpoints are used internally by tracking pixels and redirect links embedded in sent emails. They are not included in the OpenAPI schema.

### `GET /o/{log_id}`

Open-tracking pixel. Returns a 1×1 transparent GIF and records an open event.

### `GET /c/{token}`

Click-tracking redirect. Records a click event and 302-redirects to the original URL.

### `GET /u/{token}`

Unsubscribe link. Marks the lead as unsubscribed, removes pending queue slots, and shows a confirmation page.

### `GET /api/caddy/ask`

Caddy on-demand TLS domain approval gate. Returns 200 if the domain is approved for certificate issuance, 403 otherwise.

**Query param:** `?domain=mail.example.com`

### `GET /api/tracking-probe`

Lightweight probe to verify the server is reachable at a given domain.

---

## Test Mode

### `GET /api/test/status`

Check if test mode is enabled.

### `POST /api/test/status`

Toggle test mode (legacy endpoint).

**Body:** `{ "test_mode": true }`

---

## Campaign Previews & Test Sends

### `POST /api/campaigns/{id}/preview`

Preview a rendered email for a specific sequence and lead. Template variables are substituted.

**Body:** `{ "sequence_id": 5, "lead_id": 42 }` (`lead_id` optional)

**Response:** `{ "subject": "Hi Alice", "body": "<p>...</p>", "is_html": true, "sequence_position": 1 }`

### `POST /api/campaigns/{id}/send-test`

Send a real test email using the campaign's first inbox.

**Body:** `{ "sequence_id": 5, "to_email": "test@example.com", "lead_id": 42 }`

**Response:** `{ "ok": true, "message_id": "<abc@mail.gmail.com>" }`

---

## Queue

### `GET /api/campaigns/{id}/queue`

List pending queue slots for a campaign.

**Response:**

```json
[
  {
    "slot_id": 100,
    "scheduled_date": "2026-01-20",
    "position_in_day": 3,
    "sequence_index": 1,
    "inbox_id": 2,
    "inbox_email": "outreach@gmail.com",
    "lead_email": "alice@example.com",
    "lead_name": "Alice"
  }
]
```

### `GET /api/campaigns/{id}/sent`

Sent email history for a campaign.

### `POST /api/campaigns/{id}/recalculate-queue`

Recalculate queue for a specific campaign after sequence or setting changes.

---

## Template Variables

Email bodies support Jinja2-style template substitution:

| Variable | Source |
|---|---|
| `{{name}}` | Lead name |
| `{{email}}` | Lead email |
| `{{company}}` | `custom_data.company` |
| `{{*}}` | Any key from lead's `custom_data` |

Example: `Hi {{name}}, I noticed {{company}} is growing fast...`

---

## Error Responses

All errors follow a consistent format:

```json
{
  "detail": "Campaign not found"
}
```

Common HTTP status codes:

| Code | Meaning |
|---|---|
| 200 | Success |
| 400 | Bad request / validation error |
| 404 | Resource not found |
| 405 | Method not allowed |
| 422 | Unprocessable entity (Pydantic validation) |
| 500 | Internal server error |
