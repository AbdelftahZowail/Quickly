Trigger# API & Webhooks

This project exposes a simple JSON API and also allows external services to
be notified of important email-related events via a single configurable
webhook.

The webhook mechanism is intentionally minimal so it can grow organically as
new event types are added.  This document explains the current behaviour and
provides guidance for future enhancements.

---

## Enabling the webhook

Two application settings control the webhook;
they are editable via the **Settings** page or directly through the
`app_settings` API.

| Setting key                        | Description |
|-----------------------------------|-------------|
| `email_events_webhook_url`        | Full URL of the outbound webhook endpoint.  If empty, no events are fired. |
| `email_events_webhook_token`      | Optional bearer token.  If set the header `Authorization: Bearer <token>` is
                                     included with each POST. |

When the URL is configured the server will make an *asynchronous* `POST`
request to it for each event.  Failures are logged but do **not** interrupt
normal processing; the email send/sync flows will continue even if the webhook
endpoint is unreachable.

## Payload format

Every webhook call has the following top-level JSON structure:

```json
{
  "event": "<event_type>",
  "data": { /* event-specific details */ }
}
```

The `event` field is a short string naming the occurrence, e.g. `daily_limit`.
The `data` object contains contextual information; future events should keep
this pattern so receivers can easily parse common metadata.

### Current event types

| Event type      | When it fires                                                                        | Example `data` fields |
|-----------------|--------------------------------------------------------------------------------------|-----------------------|
| `daily_limit`   | Just before `run_send_job` would send a message that breaks the inbox's daily cap.   | `inbox_id`, `inbox_email`, `date` |
| `rate_limit`    | When the next scheduled slot violates the `wait_minutes_between` rule.              | `inbox_id`, `inbox_email`, `last_sent`, `now`, `wait_minutes` |
| `token_expired` | Any time a Gmail OAuth token cannot be refreshed (send job *or* unibox sync).      | `inbox_id`, `inbox_email`, `lead_id` (send only) or `at` (sync) |

> **Note:** slots that trigger `daily_limit` or `rate_limit` are left in the
> queue so they can be re‑calculated or retried later.

New event types should be added here and tests updated accordingly.

## Adding new webhook events

1. **Define the event name and payload schema.**  Keep names short
e.g. `bounce_detected`, `sync_error`, etc.
2. **Fire the event** by calling `maybe_fire_email_event(db, event_type, data)`
   from the appropriate module.  `db` should be an `AsyncSession` already
   in scope.
3. **Write tests** that monkeypatch `maybe_fire_email_event` (see
   `tests/test_jobs.py`) and assert the correct event is queued.
4. **Document** the new event in this file and update any existing docs or
   README sections where useful.

Because the call is non‑blocking and failures are ignored, you can safely add
webhook calls to existing critical paths without worrying about cascading
effects.

## API endpoints

The application exposes a standard REST-style API for performing the core
operations; refer to the [README](README.md) for a complete reference.
Most interactions are not authenticated – the service is intended as a
personal tool and assumes you will protect access at the network level.

The only webhook-related API is the settings endpoints described earlier.  No
special endpoints are required for delivery; the webhook is outbound only.

---

Having this dedicated document will make it easier to understand the
webhook architecture when new developers join the project or new event types
are planned.  The send job and unibox modules already exercise the webhook
for limits and token issues; the tests ensure the behaviour doesn't
accidentally regress.