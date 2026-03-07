# Webhooks

Quickly can notify external services of important events via outbound webhooks. This is useful for integrating with Slack, Zapier, CRMs, or any custom automation.

---

## Overview

- Webhooks are **outbound only** — Quickly POSTs JSON to your endpoint when events occur.
- Calls are **asynchronous** — failures are logged but never interrupt email sending or sync.
- **Multiple webhooks** supported — add as many endpoints as you need.
- Each webhook **subscribes to specific event types** — or leave the events list empty to receive everything.
- Optional **bearer token** authentication per webhook.

---

## Configuration

Configure webhooks from the **Settings** page in the web UI (under the Webhooks section), or via the REST API.

### Create a Webhook

```bash
curl -X POST http://localhost:8000/api/settings/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/hooks/quickly",
    "secret": "my-bearer-token",
    "events": ["email.sent", "email.bounced", "lead.replied"],
    "description": "Main CRM integration"
  }'
```

### List All Webhooks

```bash
curl http://localhost:8000/api/settings/webhooks
```

### Update a Webhook

```bash
curl -X PATCH http://localhost:8000/api/settings/webhooks/1 \
  -H "Content-Type: application/json" \
  -d '{"active": false}'
```

### Delete a Webhook

```bash
curl -X DELETE http://localhost:8000/api/settings/webhooks/1
```

### Test a Webhook

```bash
# Generic test event
curl -X POST http://localhost:8000/api/settings/webhooks/1/test

# Simulate a specific event type (see exact payload format)
curl -X POST http://localhost:8000/api/settings/webhooks/1/test-event \
  -H "Content-Type: application/json" \
  -d '{"event": "email.sent"}'
```

### Get Available Event Types

```bash
curl http://localhost:8000/api/settings/webhooks/events
```

---

## Payload Format

Every webhook call sends a `POST` request with the following JSON structure:

```json
{
  "event": "<event_type>",
  "data": { ... },
  "timestamp": "2026-01-15T10:30:00Z"
}
```

If a bearer secret is configured the request includes:

```
Authorization: Bearer <your_secret>
Content-Type: application/json
```

---

## Event Types

### `email.sent`

Fires when an email is successfully sent to a lead from a campaign.

```json
{
  "event": "email.sent",
  "data": {
    "email_log_id": 123,
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "campaign_id": 5,
    "inbox_id": 3,
    "inbox_email": "outreach@gmail.com",
    "subject": "Quick question about...",
    "sequence_index": 0,
    "message_id": "<CAF1...@mail.gmail.com>",
    "thread_id": "179a..."
  }
}
```

### `email.opened`

Fires when a lead opens an email (via tracking pixel).

```json
{
  "event": "email.opened",
  "data": {
    "email_log_id": 123,
    "lead_id": 42,
    "campaign_id": 5,
    "ip_address": "203.0.113.1"
  }
}
```

### `email.clicked`

Fires when a lead clicks a tracked link in an email.

```json
{
  "event": "email.clicked",
  "data": {
    "email_log_id": 123,
    "lead_id": 42,
    "campaign_id": 5,
    "original_url": "https://yoursite.com/demo",
    "ip_address": "203.0.113.1"
  }
}
```

### `email.bounced`

Fires when a send fails permanently (invalid recipient, rejection).

```json
{
  "event": "email.bounced",
  "data": {
    "lead_id": 42,
    "lead_email": "bad@nonexistent.com",
    "campaign_id": 5,
    "inbox_id": 3,
    "error_type": "bounce",
    "error_message": "Gmail rejected the message (400): ..."
  }
}
```

### `lead.replied`

Fires when an incoming message is matched to a known lead.

```json
{
  "event": "lead.replied",
  "data": {
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "lead_name": "Alice Long",
    "thread_id": "179a...",
    "inbox_id": 3,
    "inbox_email": "outreach@gmail.com",
    "message_id": "<CAF1...@mail.gmail.com>"
  }
}
```

### `lead.unsubscribed`

Fires when a lead clicks the unsubscribe link.

```json
{
  "event": "lead.unsubscribed",
  "data": {
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "campaign_id": 5
  }
}
```

### `lead.status_changed`

Fires when a lead's status transitions (e.g. active → bounced, active → unsubscribed).

```json
{
  "event": "lead.status_changed",
  "data": {
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "old_status": "active",
    "new_status": "bounced",
    "reason": "Gmail rejected the message (400): ..."
  }
}
```

### `lead.interested`

Fires when the AI classifier determines a lead's reply shows positive interest. Requires AI features to be enabled in Settings.

```json
{
  "event": "lead.interested",
  "data": {
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "lead_name": "Alice Long",
    "campaign_id": 5,
    "classification": "interested",
    "reply_snippet": "Yes, I'd love to learn more about your product!"
  }
}
```

### `lead.not_interested`

Fires when the AI classifier determines a lead's reply is negative or a rejection. The lead's sending is automatically paused for the campaign. Requires AI features to be enabled in Settings.

```json
{
  "event": "lead.not_interested",
  "data": {
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "lead_name": "Alice Long",
    "campaign_id": 5,
    "classification": "not_interested",
    "reply_snippet": "Please remove me from your list."
  }
}
```

### `lead.out_of_office`

Fires when the AI classifier determines a lead's reply is an out-of-office auto-response. Sending is not paused in this case.

```json
{
  "event": "lead.out_of_office",
  "data": {
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "lead_name": "Alice Long",
    "campaign_id": 5,
    "classification": "out_of_office",
    "reply_snippet": "I am out of the office until March 15th."
  }
}
```

### `lead.wrong_person`

Fires when the AI classifier determines the reply indicates the email reached the wrong person (e.g. misaddressed or forwarded).

```json
{
  "event": "lead.wrong_person",
  "data": {
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "lead_name": "Alice Long",
    "campaign_id": 5,
    "classification": "wrong_person",
    "reply_snippet": "I think you have the wrong person."
  }
}
```

### `lead.auto_reply`

Fires when the AI classifier determines the reply is an automated message (e.g. email delivery notification, vacation responder that isn't out-of-office).

```json
{
  "event": "lead.auto_reply",
  "data": {
    "lead_id": 42,
    "lead_email": "prospect@example.com",
    "lead_name": "Alice Long",
    "campaign_id": 5,
    "classification": "auto_reply",
    "reply_snippet": "This is an automated response."
  }
}
```

### `daily_limit`

Fires when the send job is about to exceed an inbox's daily sending cap.

```json
{
  "event": "daily_limit",
  "data": {
    "inbox_id": 3,
    "inbox_email": "outreach@gmail.com",
    "date": "2026-01-15"
  }
}
```

### `rate_limit`

Fires when the next scheduled slot would violate the `wait_minutes_between` cooldown.

```json
{
  "event": "rate_limit",
  "data": {
    "inbox_id": 3,
    "inbox_email": "outreach@gmail.com",
    "last_sent": "2026-01-15T10:25:00Z",
    "now": "2026-01-15T10:27:00Z",
    "wait_minutes": 5
  }
}
```

### `token_expired`

Fires when a Gmail OAuth token cannot be refreshed.

```json
{
  "event": "token_expired",
  "data": {
    "inbox_id": 3,
    "inbox_email": "outreach@gmail.com"
  }
}
```

---

## Handling Webhooks

### Reliability

- Webhook calls are **fire-and-forget**. If your endpoint is unreachable or returns an error, the event is logged server-side but not retried.
- Design your handler to be **idempotent** — while duplicate delivery is unlikely, it's good practice.
- Respond with any `2xx` status code to acknowledge receipt.

### Example: Slack Notification

```python
# Flask example
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
SLACK_WEBHOOK = "https://hooks.slack.com/services/T.../B.../xxx"

@app.route("/hooks/quickly", methods=["POST"])
def quickly_webhook():
    payload = request.json
    event = payload["event"]
    data = payload["data"]

    if event == "lead.replied":
        text = f"Reply from {data['lead_name']} ({data['lead_email']})"
    elif event == "email.bounced":
        text = f"Bounced: {data['lead_email']} — {data['error_type']}"
    elif event == "daily_limit":
        text = f"Daily limit reached for {data['inbox_email']}"
    elif event == "token_expired":
        text = f"Token expired for {data['inbox_email']} — re-authorize!"
    else:
        text = f"Quickly event: {event}"

    requests.post(SLACK_WEBHOOK, json={"text": text})
    return jsonify(ok=True)
```

### Example: Zapier

1. Create a **Webhooks by Zapier** trigger (Catch Hook).
2. Copy the Zapier webhook URL.
3. In **Settings → Webhooks**, click **Add Webhook** and paste the URL.
4. Select the events you care about (or leave all checked).
5. Click **Test** to send a sample event.
6. Build your Zap with the parsed event data.

---

## Adding Custom Event Types

If you're contributing to Quickly and need a new webhook event:

1. Add the event name to `WEBHOOK_EVENT_TYPES` in `app/models.py`.
2. Fire it by calling `fire_webhook_event(db, event_type, data)` from your code.
3. Write tests (monkeypatch `fire_webhook_event` and assert the correct event type/data).
4. Document the new event in this file.

The webhook call is non-blocking and failure-safe, so adding new events to critical paths is safe.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/settings/webhooks` | List all webhooks |
| `POST` | `/api/settings/webhooks` | Create a webhook |
| `GET` | `/api/settings/webhooks/events` | List valid event types (15 total) |
| `PATCH` | `/api/settings/webhooks/{id}` | Update a webhook |
| `DELETE` | `/api/settings/webhooks/{id}` | Delete a webhook |
| `POST` | `/api/settings/webhooks/{id}/test` | Fire generic test event |
| `POST` | `/api/settings/webhooks/{id}/test-event` | Fire simulated event with sample data |
