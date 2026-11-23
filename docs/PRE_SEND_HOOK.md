# Pre-send decision hook

Before sending to a lead, Quickly can ask one HTTPS endpoint whether to go
ahead. The endpoint answers **allow**, **hold** or **deny**.

Off by default and fails open by default. Everything is configured in
**Settings → Pre-send hook**.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| Endpoint URL | empty (off) | HTTPS, or `http://localhost` for development. |
| Signing secret | empty | When set, every request is signed (see below). |
| If the hook fails | `allow` | `allow` sends and logs a warning; `hold` keeps the queue slot and asks again next scan. |
| Check | `first` | `first` = first email of a sequence; `all` = every email. |
| Send fields | `minimal` | `minimal` = lead id + email; `contact` adds name, company and custom fields. |

The endpoint is given 3 seconds to answer. Test-mode sends never call the hook.
Environment variables (`PRE_SEND_HOOK_URL`, `PRE_SEND_HOOK_SECRET`,
`PRE_SEND_HOOK_ON_ERROR`, `PRE_SEND_HOOK_SCOPE`, `PRE_SEND_HOOK_FIELDS`) override
the saved settings.

## Request

One `POST` per decision:

```json
{
  "event": "pre_send",
  "version": 1,
  "timestamp": "2026-11-07T15:04:05.123456Z",
  "lead": { "id": 42, "email": "jane@acme.co.uk" },
  "campaign": { "id": 5, "name": "November outreach" }
}
```

With **Send fields = contact**, `lead` also carries `name`, `company` and
`custom_data`.

Headers:

```
Content-Type: application/json
User-Agent: Quickly-PreSendHook/1
X-Quickly-Event: pre_send
X-Quickly-Timestamp: 1762527845
X-Quickly-Signature: v1=<hex HMAC-SHA256>      (only when a secret is set)
```

The signature is `HMAC-SHA256(secret, "<X-Quickly-Timestamp>.<raw request body>")`,
hex encoded. Verify it and reject stale timestamps if replay matters.

## Response

Answer `200` with JSON:

```json
{ "decision": "allow" }
```

| Field | Required | Meaning |
|---|---|---|
| `decision` | yes | `allow`, `hold` or `deny`. |
| `reason` | no | One line, kept on the lead and in logs (300 characters at most). |
| `reference` | no | Your id for this decision (120 characters at most), kept on the lead and sent in the `lead.status_changed` webhook on a deny. |
| `retry_after_seconds` | no | For `hold` only: when to ask again. Default 6 hours, maximum 7 days. |

What Quickly does:

- **allow**: the email is sent. Reused for the same lead and hook for 30 days.
- **hold**: nothing is sent. The queue slot stays and the lead is asked again
  after `retry_after_seconds`.
- **deny**: nothing is sent. The enrolment is paused, its queue slots are
  dropped, and a `lead.status_changed` webhook fires with `reason`,
  `pre_send_decision` and `pre_send_reference`. Reused for 30 days.

Anything else (non-200, timeout, invalid JSON, unknown decision) is an
**error** and follows **If the hook fails**. Errors are never cached.

## What is recorded

Each lead gets `pre_send_decision` (`allow`, `hold`, `deny`, `error_allow` or
`error_hold`) and `pre_send_check` (`reason`, `reference`, `error`, `checked_at`,
`retry_at`, `host`). Both are returned by the leads API.
