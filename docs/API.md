# API Reference

Quickly exposes a JSON REST API over HTTP. **All endpoints require authentication** via a Bearer token (JWT access token) or the httpOnly `access_token` cookie set at login.

All request/response bodies use `Content-Type: application/json`.

**MCP (Model Context Protocol):** Quickly also serves a **Streamable HTTP** MCP endpoint at **`GET/POST /api/mcp`** for AI agents (leads tools). It uses the same API key or Bearer authentication as the REST API. See **[docs/MCP.md](MCP.md)** for client setup (`mcp-remote`, Cursor) and tool list.

**n8n:** To drive this API from **self-hosted n8n** with a built-in node (resources, operations, credentials), see **[N8N.md](N8N.md)** and the **[n8n package README](../n8n-node/README.md)** for install and usage.

> **Base URL:** `http://localhost:8000` (or your configured domain)

### Authentication Header

```http
Authorization: Bearer <access_token>
```

Obtain an access token via `POST /api/auth/login`. Tokens expire after 30 minutes. Use `POST /api/auth/refresh` (with the httpOnly refresh-token cookie) to get a new one.

The tracking endpoints (`/o/`, `/c/`, `/u/`) are **public** (no auth) — they are embedded in sent emails and must be reachable by recipients.

---

## Table of Contents

- [Authentication](#authentication)
- [Status](#status)
- [System Health](#system-health)
- [Campaigns](#campaigns)
- [Sequences](#sequences)
- [Sequence Variants (A/B Testing)](#sequence-variants-ab-testing)
- [Leads](#leads)
- [Provider Matching](#provider-matching)
- [Inboxes](#inboxes)
- [Schedule](#schedule)
- [Settings](#settings)
- [Model Context Protocol (MCP)](#model-context-protocol-mcp)
- [n8n (workflow automation)](N8N.md)
- [Webhooks](#webhooks)
- [Gmail OAuth](#gmail-oauth)
- [Microsoft / Office 365 OAuth](#microsoft--office-365-oauth)
- [App OAuth (Login with Google/Microsoft)](#app-oauth-login-with-googlemicrosoft)
- [Unibox](#unibox)
- [Notifications](#notifications)
- [Tracking](#tracking)
- [Test Mode](#test-mode)
- [Known IPs](#known-ips)
- [Email Verification](#email-verification)

---

## Authentication

### `GET /api/auth/setup-status`

Check whether initial setup is complete (i.e. whether the first admin account exists). Public — no auth required.

**Response:** `{ "setup_complete": false }`

When `setup_complete` is `false`, the frontend shows the registration form so the first user can create an admin account.

### `POST /api/auth/register`

Register the first (admin) user. Closes automatically after the first account is created — subsequent accounts must be invited by an admin.

**Body:**

| Field | Type | Description |
|---|---|---|
| `username` | string | 3–150 chars, alphanumeric + hyphens/underscores |
| `email` | string | Valid email address |
| `password` | string | Min 8 chars, must include uppercase, lowercase, and a digit |

**Response:** `201 Created` with user object.

### `POST /api/auth/login`

Authenticate and receive a JWT access token.

**Body:** `{ "username": "admin", "password": "..." }`

**Response:**

```json
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 1800 }
```

Also sets two httpOnly cookies: `refresh_token` (7-day, `/api/auth` path) and `access_token` (30-min, `/api` path).

### `POST /api/auth/refresh`

Exchange the `refresh_token` cookie for a new access token (token rotation). No body required.

**Response:** Same shape as `/login`. Both cookies are rotated.

### `POST /api/auth/logout`

Clears the `refresh_token` cookie.

**Response:** `{ "detail": "Logged out" }`

### `GET /api/auth/me`

Return the currently authenticated user.

**Response:**

```json
{ "id": 1, "username": "admin", "email": "admin@example.com", "role": "admin", "is_active": true, "created_at": "2026-03-01T00:00:00Z" }
```

### `PUT /api/auth/change-password`

Change the current user's password.

**Body:** `{ "current_password": "...", "new_password": "..." }`

---

### User Management (admin only)

### `POST /api/auth/users`

Create a new user (admin only). Returns `201 Created`.

**Body:** Same as `/register` plus optional `{ "role": "user"|"admin" }`.

### `GET /api/auth/users`

List all users (admin only).

---

### API Keys

API keys allow programmatic access without a username/password login. Each key is shown once at creation; only a hash is stored.

### `POST /api/auth/api-keys`

Create an API key for the current user.

**Body:**

| Field | Type | Description |
|---|---|---|
| `name` | string | Descriptive label |
| `scopes` | string[] | Optional scope list |
| `expires_in_days` | int\|null | Days until expiry (null = never) |

**Response:** `{ "id": 1, "key": "qk_live_...", "name": "...", ... }` — `key` is shown **once only**.

### `GET /api/auth/api-keys`

List API keys for the current user (keys are masked, never returned in full).

### `DELETE /api/auth/api-keys/{key_id}`

Revoke an API key.

---

## Status

### `GET /api/status`

Health check and scheduler status.  The SPA uses this endpoint for the
<strong>Schedule</strong> page banner.  When running in development mode the
response also carries a `server_time` field, which lets you verify the
backend clock (useful when using a container or when the time is being
manipulated via `time_offset_days`).

**Response:**

```json
{
  "schedule_running": true,
  "queue_check_interval_minutes": 1,
  "last_send_job_run": "2026-01-15T10:30:00Z",
  "last_send_job_sent_count": 5,
  "next_send_job_run": "2026-01-15T10:31:00Z",
  "server_time": "2026-01-15T10:30:05Z",
  "test_mode": false,
  "app_mode": "production"
}
```

---

## System Health

### `GET /api/system-health`

Aggregated health snapshot for all monitored services. The frontend **System Health** page polls this endpoint to show live operational status without multiple round-trips.

**Response:**

```json
{
  "google_oauth": {
    "configured": true,
    "accounts": [
      {
        "id": 1,
        "inbox_id": 2,
        "google_email": "outreach@gmail.com",
        "inbox_display_name": "Sales",
        "token_status": "valid",
        "token_valid": true,
        "token_expiry": "2026-03-15T14:00:00Z",
        "can_refresh": true,
        "login_status": "valid",
        "login_valid": true,
        "missing_scopes": []
      }
    ]
  },
  "microsoft_oauth": {
    "configured": true,
    "accounts": [
      {
        "id": 1,
        "inbox_id": 3,
        "microsoft_email": "sales@company.com",
        "inbox_display_name": "Office 365",
        "token_status": "valid",
        "token_valid": true,
        "token_expiry": "2026-03-15T14:00:00Z",
        "login_status": "valid",
        "login_valid": true
      }
    ]
  },
  "inboxes": [
    {
      "id": 2,
      "email": "outreach@gmail.com",
      "display_name": "Sales",
      "provider": "gmail",
      "paused": false,
      "effective_max_per_day": 32,
      "tracking_domain": "track.yourdomain.com",
      "tracking_domain_status": "ok"
    }
  ],
  "unibox_sync": {
    "push_enabled": true,
    "push_topic": "projects/my-project/topics/gmail-push",
    "sync_interval_minutes": 5,
    "initial_list_sync_in_progress": false,
    "inflight_inbox_ids": []
  },
  "ai_features": [
    {
      "id": "reply_classifier",
      "label": "Reply Interest Classifier",
      "enabled": true,
      "api_key_set": true,
      "connection_tested": true,
      "last_error": "",
      "last_error_at": ""
    }
  ],
  "email_verification": {
    "enabled": true,
    "provider": "mailtester_ninja",
    "api_key_set": true,
    "connection_tested": true,
    "last_error": "",
    "last_error_at": ""
  },
  "flags": {
    "test_mode": false
  }
}
```

The `tracking_domain_status` field for each inbox is `"ok"`, `"error"`, or `null` (if no tracking domain configured). The health endpoint probes live tokens against the Gmail and Microsoft Graph APIs to report real-time validity.

---

## Campaigns

### `GET /api/campaigns`

List all campaigns with aggregated stats.

**Response:** Array of campaign objects, each including a `stats` object:

```json
{
  "id": 1,
  "public_id": "aBcDeFgH",
  "name": "Q1 Outreach",
  "inbox_ids": [1, 2],
  "sending_days": [0, 1, 2, 3, 4],
  "sending_hours_start": "09:00",
  "sending_hours_end": "17:00",
  "stop_on_reply": true,
  "paused": false,
  "priority": 1,
  "timezone": "America/New_York",
  "track_opens": false,
  "track_clicks": false,
  "add_unsubscribe_header": true,
  "send_first_as_text": false,
  "send_all_as_text": false,
  "stats": {
    "total_leads": 150,
    "emails_sent": 300,
    "scheduled": 450,
    "open_rate": 0.42,
    "click_rate": 0.15,
    "replies": 15,
    "sequences": 3,
    "bounced": 2,
    "unsubscribed": 1
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
| `sending_days` | int[] | No | `[0,1,2,3,4]` | Array of weekdays to send (0=Mon … 6=Sun) |
| `sending_hours_start` | string | No | `"09:00"` | Start of daily sending window (HH:MM) |
| `sending_hours_end` | string | No | `"17:00"` | End of daily sending window (HH:MM) |
| `stop_on_reply` | bool | No | `true` | Stop sending to a lead after they reply |
| `paused` | bool | No | `false` | Start campaign in paused state |
| `priority` | int | No | auto | Priority order (lower = higher priority) |
| `timezone` | string | No | `null` | IANA timezone (e.g. `America/New_York`) |
| `track_opens` | bool | No | `false` | Enable open tracking pixel |
| `track_clicks` | bool | No | `false` | Enable click tracking |
| `add_unsubscribe_header` | bool | No | `true` | Add List-Unsubscribe header |
| `send_first_as_text` | bool | No | `false` | Send first sequence as plain text |
| `send_all_as_text` | bool | No | `false` | Send all sequences as plain text |
| `match_lead_provider` | bool | No | `false` | Prefer Gmail inboxes for Google leads, Office 365 inboxes for Microsoft leads |

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
| `preview_text` | string | No | Preheader / preview text (shown in inbox fragments) |

### `PATCH /api/campaigns/{id}/sequences/{seq_id}`

Update a sequence. Recalculates queue if `wait_days_after_previous` changes. Supports `preview_text`.

### `DELETE /api/campaigns/{id}/sequences/{seq_id}`

Delete a sequence. Remaining sequences are renumbered. Triggers queue recalculation.

---

## Sequence Variants (A/B Testing)

Each sequence step can have multiple A/B variants. When a step has one or more enabled variants, the sender picks one at random (uniform distribution among default + all enabled variants).

### `GET /api/campaigns/{id}/sequences/{seq_id}/variants`

List all A/B variants for a sequence step.

### `POST /api/campaigns/{id}/sequences/{seq_id}/variants`

Create a new variant. Returns `201 Created`.

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `label` | string | No | Descriptive name (e.g. "A", "B") |
| `subject` | string | No | Override subject (null = use sequence subject) |
| `body` | string | Yes | Email body |
| `is_html` | bool | No | Override HTML flag (null = use sequence flag) |
| `preview_text` | string | No | Override preheader text |
| `enabled` | bool | No | Whether this variant participates in draws (default `true`) |

**Response:** Variant object.

### `PATCH /api/campaigns/{id}/sequences/{seq_id}/variants/{variant_id}`

Partially update a variant. All fields optional.

### `DELETE /api/campaigns/{id}/sequences/{seq_id}/variants/{variant_id}`

Delete a variant. Existing email logs referencing the variant are preserved.

---

## Leads

Leads represent contacts. **Creating a lead without enrolling them in a campaign is not supported** — use `POST /api/campaigns/{id}/leads` (or CSV import on a campaign) to create and enroll. The global **`/api/leads`** routes below are for listing, exporting, updating, deleting, and recovering leads across all campaigns (used by the **Leads** page in the app).

---

### Global lead operations (`/api/leads`)

#### `GET /api/leads`

List leads with optional filters. Each item includes enrollments and verification state.

**Query params:**

| Param | Type | Description |
|---|---|---|
| `q` | string | Substring match on **email** or **name** (case-insensitive). |
| `status` | string | Filter leads that have **any** enrollment with this **enrollment** status: `active`, `contacted`, `completed`, `bounced`, `unsubscribed`, `wrong_person`. Use `invalid` for `email_verification_status == invalid` (or legacy lead `status`), `replied` for leads with any `LeadReply`. |
| `bad_only` | bool | When `true`, keep only leads with **invalid/risky** verification **or** legacy **`invalid`/`bounced`** lead status **or** **any** enrollment with status **`bounced`**. Default `false`. |
| `interest` | string | Filter by **per-enrollment** reply intent on **some** campaign: `interested`, `not_interested`, `out_of_office`, `auto_reply`. Use **`unset`** (or `none`, `clear`, etc.) for enrollments where **`interest_status` is null** (no interest set). Invalid values return **400**. |

**Filter stacking:** `q`, `bad_only`, `status`, and `interest` are combined with **AND** when more than one is set. When both **`status`** (enrollment) and **`interest`** are set, the match is a **single** enrollment that satisfies **both** (same `CampaignLead` row). When **`status`** is `invalid` or `replied`, the interest condition still applies via a separate enrollment `EXISTS` (any campaign).

**Response:** Array of lead objects:

| Field | Description |
|---|---|
| `id`, `email`, `name` | Core lead fields |
| `custom_data` | Object of custom template fields |
| `provider` | Detected email provider (e.g. Google / Microsoft), or `null` |
| `email_verification_status` | Typically `valid`, `invalid`, `pending`, or `null` (other provider values may appear) |
| `created_at` | Creation timestamp |
| `campaigns` | Per enrollment: `campaign_id`, `campaign_public_id`, `campaign_name`, `enrolled_at`, **`status`** (enrollment), **`interest`** (reply intent), **`opened`**, **`clicked`**, **`replied`** (booleans for that campaign), `sending_paused` |
| `interactions` | On **`GET /api/leads/{id}`** only: merged timeline of outbound sends and inbound mirrored messages / reply markers (see below). Empty on list. |

#### `GET /api/leads/export`

Download leads as **CSV** (`text/csv`). Uses the **same filters** as `GET /api/leads` (`q`, `status`, `bad_only`, `interest`), with the same stacking rules.

**Columns:** `id`, `email`, `name`, `email_verification_status`, `campaigns` (semicolon-separated names), **`enrollments_json`** (JSON array of per-campaign enrollment slices: `campaign_id`, `campaign_public_id`, `campaign_name`, `status`, `interest`, `opened`, `clicked`, `replied`, `sending_paused`), `enrolled_earliest` (ISO date), then every **`custom_data` key** (sorted alphabetically). Object/array values in `custom_data` are JSON-encoded in the cell.

**Response:** File download (`Content-Disposition: attachment; filename="leads_export.csv"` or `leads_bounced_invalid.csv` when `bad_only=true`).

#### `POST /api/leads/bulk-delete`

Delete many leads in one request. Removes associated `EmailLog` and `LeadReply` rows per lead, then runs a **single** full queue recalculation if any lead was deleted.

**Body:**

```json
{ "lead_ids": [1, 2, 3] }
```

**Response:** `{ "ok": true, "deleted": 3 }`

#### `POST /api/leads/bulk-status`

Set the same **enrollment status** on **every** `CampaignLead` row for each given lead. Triggers **one** full queue recalculation if anything changed.

**Body:**

```json
{ "lead_ids": [1, 2, 3], "enrollment_status": "active" }
```

**Response:** `{ "ok": true, "updated": 3 }` (`updated` counts enrollment rows updated).

#### `POST /api/leads/bulk-recover`

Fix bounced/invalid (or any) leads: update **email**, set legacy lead row to **`active`**, **reset every enrollment** to `active` (clear interest, un-pause), clear **provider** so it can be re-detected, then either queue **one** background verification batch for all recovered IDs (when verification is enabled and `verify_email` is true) or run **one** full queue recalculation.

**Body:**

```json
{
  "items": [
    { "lead_id": 10, "email": "fixed@example.com" },
    { "lead_id": 11, "email": "other@example.com" }
  ],
  "verify_email": true
}
```

| Field | Type | Description |
|---|---|---|
| `items` | array | Each entry: `lead_id` (int), `email` (new address, normalized to lowercase). |
| `verify_email` | bool | Default `true`. If the account has email verification enabled and this is `true`, recovered leads are marked pending and verified in the background; otherwise the queue is recalculated immediately. |

**Response:**

```json
{
  "recovered": 2,
  "recovered_ids": [10, 11],
  "errors": [
    { "lead_id": 99, "detail": "not_found" }
  ]
}
```

`errors` entries may use `detail`: `not_found`, `empty_email`, or `duplicate_email` (new email already used by another lead).

#### `POST /api/leads/recover-import`

Same recovery rules as **`bulk-recover`**, but input is a **multipart** CSV upload.

**Query params:**

| Param | Default | Description |
|---|---|---|
| `verify_emails` | `true` | Passed through as bulk-recover’s `verify_email`. |

**Form data:** `file` — CSV with a header row containing **`id`** (or **`lead_id`**) and **`email`**, or a headerless file whose **first two columns** are id and email. Extra columns are ignored.

**Response:** Same JSON shape as **`POST /api/leads/bulk-recover`**.

#### `POST /api/leads`

Returns **`405`** — standalone lead creation is not allowed. Use **`POST /api/campaigns/{id}/leads`**.

#### `POST /api/leads/mark-replied`

Mark a lead as having replied to a specific campaign (stop-on-reply bookkeeping).

**Body:** `{ "lead_id": 42, "campaign_id": 1 }`

**Response:** `{ "ok": true }`

#### `GET /api/leads/{id}`

Get one lead with full **`campaigns`** array (same shape as list) plus **`interactions`**: chronologically sorted events with `direction` (`outbound` \| `inbound`), `kind` (`sent` \| `received` \| `reply_marker`), `at` (ISO time), and campaign / subject / snippet fields when available.

#### `PATCH /api/leads/{id}`

Update lead fields. Setting **`enrollment_status`** applies to **all** enrollments for this lead and triggers a full queue recalculation.

**Body (all optional):**

| Field | Type | Description |
|---|---|---|
| `name` | string | Lead name |
| `custom_data` | object | Replaces custom data used in templates |
| `enrollment_status` | string | `active`, `contacted`, `completed`, `bounced`, `unsubscribed`, `wrong_person` — applied to every campaign enrollment |

**Response:** Updated lead object (same shape as `GET`).

#### `POST /api/leads/{id}/recover`

Update this lead’s **email**, set **`active`**, clear **provider**, then verify-or-recalc (same semantics as one row in **`bulk-recover`**).

**Body:**

| Field | Type | Description |
|---|---|---|
| `email` | string | New email address |
| `verify_email` | bool | Default `true` |

**Errors:** `409` if the email is already used by another lead.

**Response:** Updated lead object.

#### `DELETE /api/leads/{id}`

Delete the lead and associated logs/replies; recalculates the queue if the lead was enrolled anywhere.

**Response:** `{ "ok": true }`

#### `GET /api/leads/{id}/history`

Email send history for the lead across all campaigns.

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

#### `GET /api/leads/{id}/replies`

Reply **markers** per campaign (when a reply was recorded for stop-on-reply / Unibox). Full thread content lives in **Unibox**.

**Response:**

```json
[
  {
    "campaign_id": 1,
    "campaign_name": "Q1 Outreach",
    "replied_at": "2026-01-15T11:00:00Z"
  }
]
```

---

### Campaign-scoped lead operations

### `POST /api/campaigns/{id}/leads`

Bulk add leads to a campaign. Creates leads that don't exist yet and schedules queue slots.

**Query params:**

| Param | Default | Description |
|---|---|---|
| `skip_duplicates` | `true` | When `true`, leads enrolled in any campaign are skipped. When `false`, only leads already in *this* campaign are skipped. |
| `verify_emails` | `false` | When `true` and email verification is configured, triggers background verification for newly added leads. |

**Body:** Array of lead objects:

```json
[
  { "email": "alice@example.com", "name": "Alice", "custom_data": { "company": "Acme" } },
  { "email": "bob@example.com", "name": "Bob", "status": "active", "interest": null, "email_verification_status": "valid" }
]
```

Optional per-row fields: **`status`** (enrollment: `active`, `contacted`, `completed`, `bounced`, `unsubscribed`, `wrong_person`), **`interest`** (`interested`, `not_interested`, `out_of_office`, `auto_reply`, or omitted), **`email_verification_status`** (`valid`, `invalid`, `pending`, or clear with empty / `null`). Rows that are not sendable (e.g. unsubscribed) do not receive queue slots.

**Response:**

```json
{
  "ok": true,
  "added": 2,
  "already_enrolled": 0,
  "duplicate_leads": [],
  "duplicates_in_batch": 0,
  "errors": 0,
  "verification_queued": false,
  "results": [
    { "email": "alice@example.com", "status": "added", "lead_id": 42, "slots_created": 3 }
  ]
}
```

### `GET /api/campaigns/{id}/leads`

List leads enrolled in a campaign with progress info.

**Response:** Array of objects:

| Field | Description |
|---|---|
| `campaign_lead_id` | Enrollment row ID |
| `lead_id` | Lead ID |
| `email` | Lead email |
| `name` | Lead name |
| `status` | **Enrollment** status for this campaign: `active`, `contacted`, `completed`, `bounced`, `unsubscribed`, `wrong_person` |
| `interest` | Reply intent: `interested`, `not_interested`, `out_of_office`, `auto_reply`, or `null` |
| `custom_data` | Key-value custom fields |
| `enrolled_at` | Enrollment timestamp |
| `stage` | Current step label (e.g. "Step 2", "Complete") |
| `opened` | Whether this lead opened any email in this campaign |
| `clicked` | Whether this lead clicked any link |
| `replied` | Whether this lead replied |
| `sending_paused` | Manual pause for this enrollment |
| `email_verification_status` | Verification status or `null` if unverified |

### `DELETE /api/campaigns/{id}/leads/{lead_id}`

Remove a lead from a campaign and delete their pending queue slots.

---

## Provider Matching

Provider matching performs DNS MX-record lookups to detect each lead's email provider (Google Workspace, Office 365, etc.) and optionally routes sends through matching inboxes.

### `POST /api/campaigns/{id}/leads/detect-providers`

Trigger background provider detection for all leads in a campaign. Re-probes DNS MX records for every lead and updates the `provider` field.

**Response:** `{ "ok": true, "queued": 150 }`

The detected `provider` value (e.g. `"google"`, `"office365"`) is stored on the Lead and shown as a badge in the campaign leads table. When `match_lead_provider` is enabled on the campaign, the queue engine filters inboxes by provider to maximize deliverability.

### `POST /api/campaigns/{id}/leads/verify`

Trigger background email verification for all unverified leads in a campaign. Requires email verification to be configured in Settings.

**Response:** `{ "ok": true, "queued": 42 }`

### `GET /api/campaigns/{id}/leads/verification-status`

Return a summary count of leads grouped by their `email_verification_status`.

**Response:**

```json
{ "statuses": { "valid": 80, "invalid": 5, "catch_all": 10, "unverified": 55 } }
```

### `GET /api/campaigns/{id}/leads/export`

Export the campaign's leads as a CSV file.

**Query params:** `verification_status` (filter), `status` (filter on **enrollment** status for this campaign), `interest` (filter on this campaign’s enrollment: same values as **`GET /api/leads`** — `interested`, `not_interested`, `out_of_office`, `auto_reply`, or **`unset`** / `none` / `clear` for null interest). Params **stack** with AND. Invalid `interest` returns **400**.

**Response:** `text/csv` with columns: `email`, `name`, **`status`** (enrollment), **`interest`**, `email_verification_status`, **`opened`**, **`clicked`**, **`replied`** (`0`/`1`), then any `custom_data` keys.

### `POST /api/campaigns/{id}/leads/import`

Import leads from a CSV file. Expects an `email` column; `name` and any extra columns become `custom_data`. Reserved columns (not merged into `custom_data`): **`status`**, **`interest`**, **`email_verification_status`** (same semantics as bulk add).

**Query params:** `skip_duplicates` (default `true`), `verify_emails` (default `false`)

**Form data:** `file` (multipart CSV upload)

**Response:** Same shape as the bulk add response.

---

## Inboxes

Inboxes are sending email addresses connected via Gmail OAuth, Office 365 OAuth, or generic SMTP credentials (Amazon SES or any SMTP relay + optional IMAP for reply sync). Each inbox has its own daily limit, optional warm-up ramp, optional jitter, and optional custom tracking domain.

### `GET /api/inboxes`

List all inboxes. Each includes a `sent_today` count.

### `POST /api/inboxes`

Create an inbox manually (most inboxes are created automatically during the OAuth flow).

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Sending email address |
| `display_name` | string | No | Sender display name |
| `max_emails_per_day` | int | No | Daily sending limit (default `50`) |
| `wait_minutes_between` | int | No | Cooldown between sends (default `5`) |
| `provider` | string | No | `gmail`, `office365`, or `smtp` (generic SMTP; configure credentials via `/api/smtp/inboxes/{id}`) |
| `tracking_domain` | string | No | Custom tracking domain hostname |
| `ramp_up_enabled` | bool | No | Enable send-volume warm-up ramp (default `false`) |
| `ramp_up_period_days` | int | No | Ramp-up duration in days (default `42`) |
| `max_jitter_seconds` | int | No | Max random seconds added to each scheduled send time (default `180`; set to `0` to disable) |

The `GET /api/inboxes` and `GET /api/inboxes/{id}` responses include computed fields:

| Field | Description |
|---|---|
| `sent_today` | Emails sent from this inbox today (UTC) |
| `pending_leads` | Pending queue slots assigned to this inbox |
| `effective_max_per_day` | Actual daily limit accounting for ramp-up |
| `paused` | Whether the inbox is paused |

### `GET /api/inboxes/{id}`

Get a single inbox with today’s sent count.

### `PATCH /api/inboxes/{id}`

Update inbox fields. Triggers queue recalculation if capacity changes. Also accepts `ramp_up_enabled`, `ramp_up_period_days`, and `paused`.

### `DELETE /api/inboxes/{id}`

Delete an inbox. Fails if the inbox is assigned to campaigns or has pending queue slots.

### `POST /api/inboxes/{id}/pause`

Pause an inbox. Choose how to handle leads currently assigned to it.

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | Yes | `pause_leads` — set `sending_paused=true` on affected leads; or `reassign` — run a full recalculation so other inboxes absorb the leads |

**Response:** Updated inbox object.

### `POST /api/inboxes/{id}/unpause`

Resume a paused inbox. Also un-pauses any `CampaignLead` rows that were paused because of this inbox, then triggers a full queue recalculation.

**Response:** Updated inbox object.

### SMTP inboxes (`/api/smtp`)

Generic SMTP/IMAP inboxes (Amazon SES or any SMTP relay). Create the inbox first via `POST /api/inboxes` with `provider: "smtp"`, then store its credentials here. Passwords are never returned — responses carry `has_smtp_password` / `has_imap_password` booleans only. SMTP connections require STARTTLS or implicit SSL and verify TLS certificates; hosts resolving to private/internal addresses are rejected unless `SMTP_ALLOW_PRIVATE_HOSTS=true` is set.

### `PUT /api/smtp/inboxes/{id}`

Create or replace the SMTP/IMAP credentials for an SMTP inbox. On update, an empty password field means "keep the stored secret" (create requires one). Does not test the connection — call the test endpoint explicitly.

**Body:** `smtp_host`, `smtp_port` (default `587`), `smtp_username`, `smtp_password`, `smtp_use_tls` (default `true`), `smtp_use_ssl` (default `false`), plus optional `imap_host`, `imap_port` (default `993`), `imap_username`, `imap_password`, `imap_use_ssl` (default `true`) for inbound reply sync.

### `GET /api/smtp/inboxes/{id}`

Fetch the stored SMTP/IMAP settings (no secrets) including the last connection-test result (`last_tested_at`, `last_test_ok`, `last_test_error`).

### `POST /api/smtp/inboxes/{id}/test`

Test the stored SMTP connection (and IMAP when configured), persist the result on the account, and return per-protocol `{ok, error, detail}`. Test errors are sanitised to short category messages; full detail goes to the server logs.

### `DELETE /api/smtp/inboxes/{id}`

Remove the SMTP credentials (the inbox row itself is kept for history).

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

**Response:**

```json
{
  "total_sent": 500,
  "total_scheduled": 200,
  "total_campaigns": 5,
  "global_recalc_finished_at": "2026-03-26T12:00:00+00:00"
}
```

`global_recalc_finished_at` is an ISO-8601 UTC timestamp written when the last **global** queue recalculation finished successfully (`null` until the first run). The Schedule page uses it to detect async recalc completion without guessing from slot counts.

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

**Default (async):** Returns immediately after enqueueing work. The HTTP handler does not wait for the rebuild to finish.

```json
{ "ok": true, "accepted": true }
```

**Synchronous rebuild:** Use query `?sync=true` when the caller must wait for completion (tests, scripts, debugging). Response includes full stats:

```json
{
  "ok": true,
  "strategy": "priority",
  "campaigns_processed": 5,
  "total_slots": 800
}
```

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

Get the server's base URL, CNAME target (when the legacy custom-domain workflow applies), and whether the inbox UI should offer that CNAME flow.

**Response fields:**

| Field | Type | Description |
|-------|------|-------------|
| `base_url` | string | Configured application base URL |
| `cname_target` | string | Hostname derived from `base_url` for CNAME instructions |
| `custom_tracking_cname_ui_enabled` | boolean | `false` on prebuilt image deployments (`QUICKLY_PREBUILT_IMAGE=1`) unless `QUICKLY_TRACKING_CNAME_UI=1` — operators use Beacon for custom tracking hostnames instead |

### `GET /api/settings/verify-tracking-domain`

Verify that a custom tracking domain resolves correctly to this server.

**Query param:** `?domain=mail.example.com`

### `GET /api/settings/mcp-setup`

Return the MCP HTTP URL and a **Cursor / mcp-remote** config fragment (authenticated).

**Response:**

| Field | Type | Description |
|-------|------|-------------|
| `api_base_url` | string | Application base URL (`BASE_URL` or inferred from the request) |
| `mcp_http_url` | string | Streamable MCP endpoint, e.g. `https://your-host.com/api/mcp` |
| `cursor_mcp_fragment` | object | JSON suitable to merge under `mcpServers` — uses `npx -y mcp-remote`, `--transport http-only`, and `X-API-Key:${QUICKLY_MCP_API_KEY}` in `env` |

The UI **Settings → MCP (AI agents)** loads this endpoint and offers a copy action.

---

## Model Context Protocol (MCP)

Quickly embeds an MCP server (**FastMCP**, stateless Streamable HTTP) at **`/api/mcp`**. Clients such as Cursor typically connect via **[mcp-remote](https://www.npmjs.com/package/mcp-remote)** pointing at `https://<your-host>/api/mcp` with `--transport http-only`.

### Authentication

Every MCP HTTP request must include either:

- **`X-API-Key: <raw key>`** (API keys from **Settings → API Keys**), or  
- **`Authorization: Bearer <access_token>`** (same JWT as the REST API).

Unauthenticated requests receive **401**. Tool execution forwards these headers to internal `httpx` calls against the existing REST routes, so authorization matches the logged-in user / key owner.

### Tools

| Tool | Maps to |
|------|--------|
| `list_leads` | `GET /api/leads` (optional `q`, `status`, `bad_only`, `interest`; filters stack) |
| `get_lead` | `GET /api/leads/{id}` |
| `update_lead` | `PATCH /api/leads/{id}` |
| `delete_lead` | `DELETE /api/leads/{id}` |
| `add_campaign_leads` | `POST /api/campaigns/{campaign_id}/leads` |

For behaviour, query parameters, and bodies, see the **[Leads](#leads)** and **[Campaigns](#campaigns)** sections above.

### Client setup

Step-by-step instructions (Cursor, Bearer vs API key, local `--allow-http`, troubleshooting) are in **[docs/MCP.md](MCP.md)**.

---

## Webhooks

Quickly supports multiple outbound webhooks. Each webhook subscribes to any combination of event types and is called with a signed JSON body.

### `GET /api/settings/webhooks/events`

List all supported event type strings.

**Response:** `["email.sent", "email.opened", "email.clicked", "email.bounced", "lead.replied", "lead.unsubscribed", "lead.status_changed", "lead.interested", "lead.not_interested", "lead.out_of_office", "lead.wrong_person", "lead.auto_reply", "daily_limit", "rate_limit", "token_expired"]`

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

Start the Google OAuth inbox-connection flow. Redirects the user to Google's consent screen.

**Query params:** `display_name` (optional), `max_per_day` (optional)

### `GET /oauth/google/callback`

OAuth callback handler. Creates or updates the inbox and Gmail account, then redirects to `/inboxes`.

### `DELETE /api/gmail/accounts/{id}`

Disconnect a Gmail account. Removes stored tokens and reverts the inbox provider.

---

## Microsoft / Office 365 OAuth

### `GET /api/office365/status`

Check if Microsoft OAuth credentials are configured on the server.

**Response:** `{ "configured": true, "redirect_uri": "https://yourdomain.com/oauth/office365/callback" }`

### `GET /api/office365/accounts`

List all connected Office 365 accounts.

**Response:**

```json
[
  {
    "id": 1,
    "inbox_id": 4,
    "microsoft_email": "sales@company.com",
    "inbox_display_name": "Office 365 Sales",
    "max_emails_per_day": 50,
    "token_expiry": "2026-01-15T12:00:00Z",
    "connected_at": "2026-01-01T00:00:00Z"
  }
]
```

### `GET /oauth/office365/authorize`

Start the Microsoft Office 365 inbox-connection OAuth flow. Redirects to Microsoft's consent screen.

**Query params:** `display_name` (optional), `max_per_day` (optional)

### `GET /oauth/office365/callback`

OAuth callback handler. Creates or updates the inbox and Office 365 account, then redirects to `/inboxes`.

### `DELETE /api/office365/accounts/{id}`

Disconnect an Office 365 account. Removes stored tokens and reverts the inbox provider.

---

## App OAuth (Login with Google/Microsoft)

These endpoints allow users to log in to Quickly itself using their Google or Microsoft account instead of a username/password. They are separate from the inbox-connection OAuth flows.

### `GET /oauth/app/google/authorize`

Start the app-level Google login flow. Redirects to Google's consent screen. On completion, creates or signs in the user and sets JWT cookies.

### `GET /oauth/app/google/callback`

OAuth callback for app-level Google login.

### `GET /oauth/app/microsoft/authorize`

Start the app-level Microsoft login flow.

### `GET /oauth/app/microsoft/callback`

OAuth callback for app-level Microsoft login.

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

## Notifications

Email notifications let Quickly send you an email (via your connected Gmail or Microsoft account) when specific events occur.

### `GET /api/notifications/config`

Get the current user's email notification configuration.

**Response:**

```json
{
  "enabled": true,
  "notification_email": "you@example.com",
  "events": ["lead.interested", "email.bounced"],
  "rate_limit_per_hour": 10
}
```

### `PUT /api/notifications/config`

Create or update the current user's notification configuration.

**Body:**

| Field | Type | Description |
|---|---|---|
| `enabled` | bool | Enable/disable notifications |
| `notification_email` | string | Email address to send notifications to |
| `events` | string[] | Event types to notify on (same set as webhooks) |
| `rate_limit_per_hour` | int | Max notifications per hour (1–100, default `10`) |

**Response:** Updated config object.

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

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `sequence_id` | int | Yes | Sequence to preview |
| `lead_id` | int | No | Lead for variable substitution |
| `variant_id` | int | No | Preview a specific A/B variant instead of the default content |

**Response:** `{ "subject": "Hi Alice", "body": "<p>...</p>", "is_html": true, "sequence_position": 1, "variant_label": "B", "tracking_note": "..." }`

### `POST /api/campaigns/{id}/send-test`

Send a real test email using the campaign's first inbox.

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `sequence_id` | int | Yes | Sequence to send |
| `to_email` | string | Yes | Recipient address |
| `lead_id` | int | No | Lead for variable substitution |
| `variant_id` | int | No | Send a specific A/B variant |

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

Sent email history for a campaign, including variant and interest status per row.

### `POST /api/campaigns/{id}/recalculate-queue`

Recalculate queue for a specific campaign after sequence or setting changes.

### `GET /api/campaigns/{id}/analytics/steps`

Per-step breakdown of sent/opens/clicks/replies, plus per-variant metrics for campaigns using A/B testing.

**Response:** Array of step objects:

```json
[
  {
    "sequence_id": 1,
    "sequence_index": 0,
    "subject": "Hey {{name}}",
    "total_sent": 100,
    "total_opens": 42,
    "total_clicks": 10,
    "total_replies": 5,
    "total_opportunities": 5,
    "variants": [
      { "variant_id": null, "variant_label": "Default", "sent": 50, "opens": 22, "clicks": 5, "replies": 3, "opportunities": 3, "enabled": true },
      { "variant_id": 7, "variant_label": "B", "sent": 50, "opens": 20, "clicks": 5, "replies": 2, "opportunities": 2, "enabled": true }
    ]
  }
]
```

---

## AI Classification

AI features are configured per-feature. Currently the only feature is `reply_classifier`, which classifies lead replies as interested, not interested, out of office, etc.

### `GET /api/settings/ai`

Return settings for **all** AI features.

**Response:**

```json
{
  "features": [
    {
      "id": "reply_classifier",
      "label": "Reply Interest Classifier",
      "description": "...",
      "enabled": true,
      "provider": "openai",
      "model": "gpt-4o",
      "api_key_set": true,
      "api_key_masked": "sk***ey"
    }
  ]
}
```

### `GET /api/settings/ai/{feature_id}`

Return settings for a single AI feature (e.g. `reply_classifier`). Same shape as one item in the `features` array above.

### `POST /api/settings/ai/{feature_id}`

Save settings for a feature.

**Body:**

| Field | Type | Description |
|---|---|---|
| `enabled` | bool | Enable/disable this feature |
| `provider` | string | Provider: `openai`, `anthropic`, `gemini`, `mistral`, `groq`, `cohere`, `together`, `deepseek`, `openrouter`, `perplexity`, `xai`, `ollama`, and more |
| `model` | string | Model name (e.g. `gpt-4o`, `claude-sonnet-4-5`) |
| `api_key` | string | API key (omit to keep the existing stored key) |

### `POST /api/settings/ai/{feature_id}/verify`

Verify that credentials work by sending a test prompt. Any field left empty is filled from the stored DB value.

**Body:** `{"provider": "openai", "model": "gpt-4o", "api_key": "sk-..."}`

**Response:** `{"ok": true}` or `{"ok": false, "error": "..."}`

### `GET /api/settings/ai/providers`

Return a list of all supported AI providers (most popular first).

**Response:** `{"providers": [{"value": "openai", "label": "OpenAI"}, ...]}`

### `GET /api/settings/ai/providers/{provider}/models`

Fetch available models for a provider. Pass `?api_key=<key>` to use an unsaved key.

**Response:** `{"models": [{"id": "gpt-4o", "name": "GPT-4o"}, ...]}`

### `PATCH /api/campaigns/{id}/leads/{lead_id}`

Update **enrollment status**, **interest**, and/or **sending_paused** for this campaign. Moving into a terminal enrollment (`bounced`, `unsubscribed`, `wrong_person`, `completed`) or setting interest to `not_interested` / `out_of_office` **clears queue slots**; clearing those (and unpausing) can **re-schedule** via `reserve_slots_for_new_leads_bulk`.

**Body:**

| Field | Type | Description |
|---|---|---|
| `status` | string | Enrollment: `active`, `contacted`, `completed`, `bounced`, `unsubscribed`, `wrong_person` |
| `interest` | string\|null | Same as `interest_status` (alias): `interested`, `not_interested`, `out_of_office`, `auto_reply`, or `""` to clear |
| `sending_paused` | bool\|null | Manual pause |

**Response:** `{"ok": true, "status": "active", "interest": "interested", "sending_paused": false}`

---

## Known IPs

The Known IPs system filters out opens and clicks from your own IP addresses, preventing self-opens/clicks from inflating analytics.

### `GET /api/settings/known-ips`

List all known IPs. The response includes a `current_ip` field (the caller's detected IP) and a `is_current` flag per entry.

**Response:**

```json
{
  "known_ips": [
    {
      "id": 1,
      "ip_address": "203.0.113.5",
      "permanent": true,
      "is_current": false,
      "last_seen_at": "2026-03-01T10:00:00Z",
      "expires_at": null
    }
  ],
  "current_ip": "203.0.113.5"
}
```

### `POST /api/settings/known-ips`

Manually add a known IP address.

**Body:** `{ "ip_address": "203.0.113.5", "permanent": true }`

**Response:** `{ "ok": true, "id": 1 }` (201 Created)

### `DELETE /api/settings/known-ips/{id}`

Remove a known IP entry.

### `POST /api/settings/known-ips/heartbeat`

Register the caller's IP as a non-permanent known IP (auto-expires after 7 days). Called automatically by the frontend on each session.

**Response:** `{ "ok": true, "ip": "203.0.113.5", "known_ip_id": 1 }`

---

## Email Verification

Email verification checks whether a lead's email address is deliverable before sending. When configured, invalid or risky addresses are automatically marked as bounced and their queue slots removed.

Supported providers: `mailtester_ninja` (requires API key) and `custom` (any HTTP endpoint).

### `GET /api/settings/email-verification`

Return the current email verification configuration.

**Response:**

```json
{
  "enabled": true,
  "provider": "mailtester_ninja",
  "api_key_set": true,
  "api_key_masked": "sk***ey",
  "providers": ["mailtester_ninja", "custom"],
  "custom_url": "",
  "custom_field_path": "",
  "custom_valid_values": [],
  "custom_invalid_values": [],
  "custom_method": "GET"
}
```

### `POST /api/settings/email-verification`

Save email verification settings.

**Body:**

| Field | Type | Description |
|---|---|---|
| `enabled` | bool | Enable/disable verification |
| `provider` | string | `mailtester_ninja` or `custom` |
| `api_key` | string | API key (omit to keep existing; not used for `custom`) |
| `custom_url` | string | URL template with `{email}` placeholder (custom provider only) |
| `custom_field_path` | string | Dot-path to the status field in the JSON response |
| `custom_valid_values` | string[] | Field values that indicate a valid address |
| `custom_invalid_values` | string[] | Field values that indicate an invalid address |
| `custom_method` | string | `GET` or `POST` |

### `POST /api/settings/email-verification/test`

Test the saved credentials by verifying a known address.

**Response:** `{ "ok": true, "status": "valid", "message": "..." }`

### `POST /api/settings/email-verification/test-custom`

Test a custom provider configuration **without saving it** first. Picks sample inbox/lead/synthetic addresses and returns verification results for each.

**Body:**

```json
{
  "url_template": "https://api.example.com/verify?email={email}",
  "field_path": "result.status",
  "valid_values": ["valid"],
  "invalid_values": ["invalid", "disposable"],
  "method": "GET",
  "test_emails": []
}
```

**Response:** `{ "results": [{"email": "...", "source": "inbox", "status": "valid", "raw_field_value": "...", "raw_response": {...}}] }`

---

## Template Variables

Email bodies support Jinja2-style template substitution:

| Variable | Source |
|---|---|
| `{{name}}` | Lead name |
| `{{email}}` | Lead email |
| `{{company}}` | `custom_data.company` |
| `{{*}}` | Any key from lead's `custom_data` |
| `{{unsubscribe_link}}` | Auto-generated one-click unsubscribe URL |

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
| 401 | Unauthorized — missing or invalid token |
| 403 | Forbidden — insufficient permissions |
| 404 | Resource not found |
| 405 | Method not allowed |
| 409 | Conflict — duplicate resource |
| 422 | Unprocessable entity (Pydantic validation) |
| 429 | Too many requests (rate limit: 200 req/min) |
| 500 | Internal server error |
