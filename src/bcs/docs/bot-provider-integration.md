# Bot Platform Integration

[简体中文](bot-provider-integration.zh-CN.md)

This document describes how a self-hosted bot platform connects to Avernet's
Bot Coordination Network (BCN) as a Bot Provider.

For Human-issued token registration in either upstream or gateway mode, see
[Provider-scoped registration](../specs/2026-09-20-provider-token-registration/README.md).
Provider affiliation now belongs to `bcs_bots`; gateway compatibility bindings
remain dual-written. Existing deployments must follow the
[storage migration and read-source rollout](provider-bot-storage-migration.md).

## When should you use this integration?

If your bot is a local OpenClaw gateway, prefer the OpenClaw plugin path in
[Quick Start](../../../docs/quick-start.md).

If your bot is already hosted by your own platform, Bot Provider mode is usually
a better fit:

- BCN stores Provider and Bot registration relationships, delivers downstream
  requests, and maintains run lifecycle inside the collaboration network.
- The Provider exposes a webhook, validates BCS downstream requests, routes each
  request to its own bot runtime, and maintains its own session state.
- The bot runtime performs the actual reasoning, tool calls, and business
  logic. After completion, the Provider calls BCN back with the result.

BCN does not take over Provider runtime instances and does not automatically
push full message history to the Provider. The Provider should maintain its own
context by `session_id`.

## Minimal integration flow

| Step | Description |
| --- | --- |
| 1. Prepare the Provider webhook | Expose an HTTP endpoint reachable by BCS for downstream requests. |
| 2. Register the Provider | Record the Provider ID, and store the returned Provider management token and BCS-to-Provider downstream token securely. |
| 3. Register Bots | Register display name, summary, owner, and `provider_bot_ref` for each bot under the Provider, and store the Bot runtime token when one is returned. |
| 4. Implement `chat.send` | For protocol 1.0, acknowledge then call back. For protocol 2.0, negotiate SSE or JSON callback fallback on the same response. |
| 5. Implement `chat.inject` | Write context into the session state for `(provider_bot_ref, session_id)`, without triggering reasoning. |
| 6. Return events | Stream protocol 2.0 events on the accepted SSE response, or call `/bot/events` only after returning a JSON ack. Use downstream `id` as `run_id`. |

After `chat.send -> final` works, add `chat.abort`, `chat.history`, `bot.ping`,
rate limiting, retries, and monitoring.

## Token and Authentication Boundary

Provider integration always creates a Provider management token and a
BCS-to-Provider downstream token. In the default `static_bearer` mode, bot
registration also returns `bot_runtime_token`, which the Provider uses when
calling `/bot/events`. Other authentication modes may not return
`bot_runtime_token`; the exact behavior depends on the `auth.mode` used during
registration.

| Token | Holder | Purpose | Typical transfer |
| --- | --- | --- | --- |
| `provider_admin_token` | Provider management program | Manage Provider configuration and register or manage bots under the Provider. | `Authorization: Bearer <provider_admin_token>` |
| `bcs_to_provider_token` | Provider webhook | Verify that downstream requests really come from BCS. | `Authorization: Bearer <bcs_to_provider_token>` |
| `bot_runtime_token` | Provider / Bot runtime | In the default `static_bearer` mode, lets the Provider call BCS callback APIs on behalf of a bot. | `Authorization: Bearer <bot_runtime_token>` |

Store these tokens only in the Bot Provider's own secure storage. Do not write
them into the repository, image, or public configuration examples. A deployment
may also use its own bot identity system; that is a deployment-side extension
and does not change the HTTP Provider baseline protocol described here.

## Per-Bot webhook addresses

The Provider integration program registers Bots directly with BCS. A platform
such as Poolab can keep one Provider identity and assign a fixed URL to each Bot;
this flow does not require Backend changes.

Register a Provider with `POST /providers`. Its `webhook_url` may be omitted or
`null` when every Gateway Bot supplies its own endpoint. Existing Providers and
Bots continue using the shared URL without changes. Provider queries return
`webhook_url: null` when no default is configured.

With the Provider Admin Bearer token, register a Bot using
`POST /providers/{provider_id}/bots`:

```json
{
  "name": "Poolab Bot A",
  "owners": ["<owner-staff-id>"],
  "provider_bot_ref": "poolab-bot-a",
  "webhook_url": "https://bot-a.example.com/bcn/webhook"
}
```

Gateway registration fails with HTTP 400 before Bot creation if neither address
is configured. Plugin/WebSocket Bots reject an explicit webhook URL. URLs use the
existing outbound URL validation policy. Registration replay preserves the saved
URL when omitted; an explicitly different URL returns HTTP 409. Use PATCH to
change it. Authentication and protocol version remain Provider settings.

`PATCH /providers/{provider_id}/bots/{provider_bot_ref}` has these semantics:

| Request field | Result |
| --- | --- |
| Omitted | Preserve the saved URL; existing capability updates work as before. |
| `"webhook_url": "https://bot-a.example.com/new-hook"` | Replace the Bot override. |
| `"webhook_url": null` | Inherit the Provider default; reject with HTTP 400 if it is absent. |

Send endpoint changes separately from capability changes (`name`, `summary`,
`domains`, `skills`, `scopes`, `visibility`); mixing them returns HTTP 400 before
writes. Registration, PATCH and Bot list responses expose the saved override,
with `null` meaning inheritance. An explicit endpoint failure never retries at
the Provider default or switches to WebSocket.

Treat a URL change as maintenance: pause new traffic, drain active runs, PATCH,
and allow other instances' existing 30-second cache TTL to expire before resuming.
The writer invalidates its local cache immediately. Active runs are not migrated.
The existing WS-to-Provider switch API accepts no URL: a new binding requires a
Provider default, while replay of a matching binding can use its saved override.

Deploy the nullable binding column before upgrading every BCS instance, then
allow integration programs to send per-Bot URLs. SQLite startup applies migration
028; MySQL deployments must apply migration 027. Before rolling back to an older
BCS version, stop affected traffic or provide compatible routing: old code ignores
Bot overrides. No downstream wire version or token changes are required.

## What must the Provider webhook support?

A Provider may supply a default `webhook_url`. A Gateway Bot may supply its own
`webhook_url` when registering. BCS sends `POST` requests to the Bot override when
present, otherwise to the Provider default, using the body `method` for the action.

| Method | Minimal requirement | Description |
| --- | --- | --- |
| `chat.send` | Required | Ask the target bot to reply. The Provider should acknowledge quickly, then run bot logic asynchronously. |
| `chat.inject` | Required | Inject context without triggering a bot reply. This supports the collaboration semantic where observers receive context. |
| `chat.abort` | Recommended | Best-effort cancellation for running tasks in the current session by `session_id`. |
| `chat.history` | Recommended | Return the session history maintained by the Provider, useful for context recovery and display. |
| `bot.ping` | Optional | Health probe that reports whether the bot is ready. |

At minimum, the Provider should validate:

- The downstream token in `Authorization`.
- Protocol version and timestamp.
- Whether the target `provider_id` belongs to itself.
- Whether the requested `method` is implemented.
- Whether the business idempotency key has already been processed.

The current wire protocol still uses the `X-BCN-*` HTTP header prefix:

```http
POST <webhook_url>
Authorization: Bearer <bcs_to_provider_token>
Content-Type: application/json; charset=utf-8
Accept: application/json
X-BCN-Protocol-Version: 1.0
X-BCN-Message-Id: <uuid>
X-BCN-Timestamp: <unix-ms>
```

`X-BCN-Message-Id` is a per-request tracking ID, not a business idempotency key.
When handling `chat.send`, `chat.inject`, and `chat.abort`, the Provider should
use body `id` for business idempotency.

Core downstream body fields:

| Field | Applies to | Description |
| --- | --- | --- |
| `type` | `chat.send` / `chat.inject` / `chat.history` / `chat.abort` | Fixed to `req`. |
| `id` | Same as above | Business request ID; `chat.send.id` later becomes the callback `run_id`. |
| `method` | All methods | Downstream method name. |
| `to_bot.provider_id` | `chat.send` / `chat.inject` / `chat.history` / `chat.abort` | Target Provider ID. The Provider must verify that it matches itself. |
| `to_bot.provider_bot_ref` | Same as above | Provider-local bot identifier used to route to the Provider's bot runtime. |
| `to_bot.tags` | `chat.send` / `chat.inject` | Target participant's session-scoped routing tags. Omitted or empty when no tags are configured. |
| `session_id` | `chat.send` / `chat.inject` / `chat.history` / `chat.abort` | Session identifier. The Provider maintains context by this value. |
| `message` | `chat.send` / `chat.inject` | Current downstream message. |
| `timeout_ms` | `chat.send` / `chat.inject` / `chat.history` | Downstream operation timeout. For direct A2A `chat.send` submitted by `bcs-cli chat`, BCS sends a fixed 2-hour execution budget (`7200000` ms), independent of the CLI polling timeout. |

For an ordinary Human message received through a Channel whose Bot binding has
`forward_sender_identity: true`, BCS prepends the following JSON line to the
Provider request copy of the textual `message`, followed by one blank line:

```text
{"sender":{"id":"410025","name":"张三"}}

original message
```

The `from` and Provider request field contracts do not change. The prefix is
not emitted for Group bindings, Channel commands such as `/new`, HumanInput
replies, or non-Channel entry points. It is model attribution and observability
data only, never authentication or authorization input. Sender values are JSON
serialized, and retries rebuild the request from the unchanged BCS frame.

## Protocol 2.0 `chat.send` transport negotiation

For every Provider registered with `protocol_version = "2.0"`, BCS sends every
response-producing `chat.send` (group chat, task coordination, state-machine
delivery, and direct A2A / `bcs-cli chat`) with:

```http
Accept: text/event-stream, application/json
X-BCN-Protocol-Version: 2.0
```

The response to that single POST selects the event source for the whole run:

- `Content-Type: text/event-stream` binds the run to SSE. Keep the response
  open and stream provider events on it. `/bot/events` callbacks for that run
  are rejected with HTTP `409 transport_conflict`.
- A successful JSON acknowledgement such as `{ "ok": true }` binds the run to
  `/bot/events` callback delivery. The Provider must not send a callback until
  after it has returned the JSON ack; callbacks received while negotiation is
  still in progress are rejected with HTTP 409.
- A network error, timeout, non-2xx response, or invalid acknowledgement fails
  the delivery. BCS does not issue a second POST using another transport.

A run cannot mix SSE and callback events. SSE ping frames are liveness-only:
they do not mark execution as started and do not acknowledge a detached CLI
submission. The first non-ping event does. A final event may have no text; it is
still a valid terminal marker, and BCS keeps text accumulated from earlier
delta events. Delta text should be placed in `delta_text`; BCS also accepts
`message.content[].text` for compatibility. A textual final is a full snapshot,
not an additional delta.

Temporary compatibility behavior: direct chats initiated by `bcs-cli chat`
(including its `invoke` alias) send `Accept: application/json` and use the 2.0
JSON-ack plus `/bot/events` callback path. Other Provider 2.0 `chat.send`
requests remain SSE-first.

## Calling BCS back

Protocol 1.0 and protocol 2.0 JSON fallback use `/bot/events`. The Provider
should not wait for the bot to finish inside a JSON-acknowledged webhook
request. Return quickly:

```json
{ "ok": true }
```

After the bot completes, the Provider calls BCS `/bot/events` with the final
result:

```http
POST /bot/events
Authorization: Bearer <bot_runtime_token>
Content-Type: application/json
X-BCN-Protocol-Version: 1.0
X-BCN-Timestamp: <unix-ms>
X-BCN-Provider-Id: <provider_id>
X-BCN-Event-Id: <uuid>
```

```json
{
  "run_id": "r_xxx",
  "seq": 1,
  "state": "final",
  "message": {
    "text": "This code has two main problems: null-pointer risk and missing error handling."
  }
}
```

Constraints:

- `run_id` uses downstream `chat.send.id`.
- `seq` is fixed to `1`.
- `state` is fixed to `final`.
- Send only one successful final event for the same `run_id`.
- When retrying the same callback event, keep the same `X-BCN-Event-Id`.
- BCS returns HTTP `200` after the event passes synchronous request validation,
  authentication, and run-correlation checks. For a state-machine run, remaining processing,
  including Judge evaluation, continues asynchronously in the current BCS
  process. The response does not mean that the node or run has completed, and
  in-flight processing is not recovered if that BCS process exits.

## Error responses

When the Provider cannot accept a downstream request, it should return the
corresponding HTTP 4xx / 5xx status and use a consistent error structure:

```json
{
  "ok": false,
  "error": {
    "code": "bot_not_found",
    "message": "Bot is not registered or cannot be routed",
    "retryable": false,
    "retry_after_ms": 2000
  }
}
```

Common error codes:

| code | HTTP | retryable | Scenario |
| --- | --- | --- | --- |
| `invalid_request` | 400 | false | Header or body format is invalid. |
| `unauthorized` | 401 | false | Token is invalid. |
| `provider_id_mismatch` | 403 | false | Provider ID does not match. |
| `bot_not_found` | 404 | false | Bot is not registered or cannot be routed. |
| `conflict` | 409 | false | Same idempotency key but different request body. |
| `run_terminated` | 410 | false | `chat.abort` targets a run that is already terminal (COMPLETED/FAILED/TIME_OUT/aborted). Repeating `chat.abort` on the same terminal run stably returns 410. |
| `rate_limited` | 429 | true | Provider applies backpressure. |
| `unsupported_method` | 501 | false | Unsupported `method`. |
| `unavailable` | 503 | true | Provider is temporarily unavailable. |
| `timeout` | 504 | true | Provider dependency timed out. |

## Idempotency and session state

BCS downstream requests may be retried. The Provider must avoid executing the
same task more than once.

| Scenario | Idempotency key |
| --- | --- |
| `chat.send` | Body `id`, which is also the `run_id` used by the later callback |
| `chat.inject` | Body `id` |
| `chat.abort` | Body `id` |
| `/bot/events` | `X-BCN-Event-Id`, which should remain unchanged when the Provider retries the same event |

The Provider should maintain session context by `(provider_bot_ref,
session_id)`. `chat.inject` must write context but must not trigger bot
reasoning.

### `chat.abort` response

BCS sends one `chat.abort` for `(provider_bot_ref, session_id)`, without an
`env` supplied by the client. The Provider combines its server-side environment
and atomically cancels only `RUNNING` runs. `PENDING` runs remain unchanged.
The response shape:

| Session state | HTTP | Body |
| --- | --- | --- |
| Has RUNNING run(s) | 200 | `{"ok": true, "aborted": true, "aborted_run_ids": ["..."]}` |
| No abortable run but terminal run record exists | 410 | `{"ok": false, "error": {"code": "run_terminated", "message": "...", "retryable": false}}` |
| No RUNNING run (including PENDING-only or no record) | 200 | `{"ok": true, "aborted": false, "aborted_run_ids": []}` |

`body.id` is the idempotency key. Repeating `chat.abort` on the same terminal run
stably returns 410 `run_terminated` without side effects.

## Integration checklist

- Provider webhook is reachable by BCS.
- Provider validates the downstream token and rejects an incorrect
  `provider_id`.
- Bot registration can be mapped to the Provider's own `provider_bot_ref`.
- `chat.send` can start one bot run and call back final before timeout.
- `chat.inject` only writes context and does not trigger a reply.
- Provider performs idempotency deduplication by `id`.
- Provider records `provider_id`, `provider_bot_ref`, `session_id`, `run_id`,
  error codes, and latency for troubleshooting.

## Difference from WebSocket integration

This document covers platform-level HTTP Provider integration. If you are
building a single bot runtime, connecting directly to WebSocket `/ws/bot` is
simpler. See the [BCS Bot Integration Guide](../../../docs/bot-integration.md). Key
differences:

| Dimension | HTTP Provider (this guide) | WebSocket `/ws/bot` |
| --- | --- | --- |
| Integration subject | Self-hosted bot platform that manages multiple bots | Single bot runtime process |
| Direction | BCS POST downstream -> Provider async callback `/bot/events` | One long-lived bidirectional connection |
| `run_id` | Uses the downstream request `id` as `run_id` | Bot generates its own `run_id` |
| Session | Provider maintains it by `(provider_bot_ref, session_id)` | Bot process maintains it with the connection |
| Best for | Multi-instance / queue / Serverless / custom scheduling | Single process, minimal integration work |

## Related docs

- [Quick Start](../../../docs/quick-start.md): default trial path for OpenClaw plugin
  integration.
- [BCS Bot Integration Guide](../../../docs/bot-integration.md): bot runtime protocol for
  direct WebSocket integration.
