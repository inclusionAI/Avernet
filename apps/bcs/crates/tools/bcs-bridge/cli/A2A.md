# Serving Bridge agents over A2A

Bridge can expose its configured engines as A2A **servers**. This is distinct
from BCS's outbound A2A client: BCS or another A2A client discovers the Bridge
Agent Card and sends requests to Bridge, which runs its existing engine adapter.
No additional engine process launcher or BCS runtime is introduced.

## Enable

Add `[a2a]` to a gateway Provider configuration, then restart Bridge. It is
absent/disabled by default. Gateway webhook delivery continues to work on the
same listener. Plugin-only Providers cannot enable this HTTP adapter.

```toml
provider_id = "local-coding"
mode = "gateway"
listen = "127.0.0.1:21100"
state_path = "./bridge-state.sqlite3"

[a2a]
public_url = "http://127.0.0.1:21100"

[[bot]]
provider_bot_ref = "coder"
engine = "codex"
cwd = "/absolute/path/to/workspace"
```

Use the existing credential source, **not a token saved into the TOML**:

```bash
# Supply a strong private bearer through your secret/runtime environment.
# BCS_BRIDGE_PROVIDER_TOKENS is a JSON object: Provider ID -> bearer credential.
bcs-bridge --config ./bridge.toml start
```

The public distribution resolves credentials from `BCS_BRIDGE_PROVIDER_TOKENS`;
internal distributions retain their configured credential source. The A2A
operations require `Authorization: Bearer <the target Provider's credential>`.
This credential authorizes **all Bots in that Provider**; use separate Providers
and credentials for distinct trust principals. There is no anonymous execution.
A handwritten local gateway configuration can serve A2A without registering a
Bot with BCS first; BCS registration of the resulting Card is a separate step.

For schema-version 2 multi-Provider documents, use `[provider.a2a]` under the
appropriate `[[provider]]` and `[[provider.bot]]` for its Bots. Each enabled
Provider shares its gateway listener but keeps separate credentials, Tasks and
session storage. Existing registration/init commands are unchanged; this opt-in
is configured in TOML, not a new registration mode.

`public_url` is the **external Bridge root**, including any reverse-proxy path
prefix, not the final Bot endpoint. HTTPS is required except for explicit
loopback HTTP. Bridge does not terminate TLS; use a correctly configured reverse
proxy for nonlocal access. The proxy must preserve/strip its configured prefix
consistently, forward Authorization and stream SSE without buffering. Request
Host/Forwarded headers never control the Agent Card URL. For BCS deployment,
choose an address reachable and allowed by BCS's outbound policy; localhost is
only suitable when BCS and Bridge share the relevant host/network namespace.

## Discovery and bindings

Each Bot's public, credential-free Card is at:

```text
GET /a2a/{provider_id}/{provider_bot_ref}/.well-known/agent-card.json
```

When exactly one Bot is exposed on a listener, the same Card is also available
at `/.well-known/agent-card.json`. With multiple Bots the root path returns 404;
use the explicit Card URL. IDs in URLs must be encoded as individual path
segments. Cards contain safe generic metadata and advertise only actual
capabilities. They do not expose workspace paths, models/options or credentials.

The Card advertises A2A protocol version **1.0**, with **HTTP+JSON** and
**JSONRPC** bindings at `/a2a/{provider_id}/{provider_bot_ref}`. Models and error
details use the same pinned `a2a-lf = 0.4.2` as BCS. A missing `A2A-Version`
header selects 1.0; other values receive `VERSION_NOT_SUPPORTED`. No A2A 0.3
method aliases or automatic downgrade are provided.

| Operation | HTTP+JSON, relative to Bot endpoint | JSON-RPC method |
| --- | --- | --- |
| Send | `POST /message:send` | `SendMessage` |
| Streaming send | `POST /message:stream` | `SendStreamingMessage` |
| Query | `GET /tasks/{id}` | `GetTask` |
| Cancel | `POST /tasks/{id}:cancel` | `CancelTask` |
| Subscribe | `POST /tasks/{id}:subscribe` | `SubscribeToTask` |
| List | `GET /tasks` | `ListTasks` |

JSON-RPC requests are POSTed to the Bot endpoint itself. Streamed responses are
SSE `data:` records containing standard `StreamResponse` objects, or JSON-RPC
result/error envelopes retaining the original request ID. REST errors use
ProtoJSON Status with typed `google.rpc.ErrorInfo`; RPC errors preserve the A2A
numeric code and typed details. Authentication failures use HTTP 401.

Example send body:

```json
{
  "message": {
    "messageId": "a-unique-client-message-id",
    "role": "ROLE_USER",
    "parts": [{"text": "Explain the tests in this workspace"}]
  },
  "configuration": {"returnImmediately": true}
}
```

`SendMessage` normally waits for completion. `returnImmediately: true` returns
an accepted Task for polling; streaming sends return a Task snapshot followed by
artifact/status updates. Engine text deltas are streamed immediately as transient
`working` status messages, separate from the `answer` artifact: Claude/Codex
progress commentary is not guaranteed to be a prefix of their final answer.
Each status message contains one chunk, not an accumulated answer; snapshots
retain only the latest progress message, and progress is not added to history.
The authoritative final text is published once as `answer` (`append=false`,
`lastChunk=true`), followed by the terminal status after runtime cleanup. No
provisional text enters answer artifacts in streams, queries or subscriptions.
An empty final answer remains empty; progress is never used as a fallback answer.

Progress is still live over SSE, but the answer artifact itself is not streamed
token by token. Clients must render status messages separately if they want to
show progress. The existing BCS A2A client treats them as diagnostic messages,
not answer deltas, so this Bridge-only change does not make progress appear in
BCS's answer UI. Native Bridge webhook/Plugin WebSocket output is unchanged.
Internal tool observations, thinking and engine diagnostics are not exposed as
text output.

For another turn, send a **new messageId** and the returned `contextId`, omitting
`taskId`. This resumes the existing durable engine session while creating a new
Task. Only one Task can run in a given Bot/context at a time. Concurrent sends to
a busy context fail before executing another engine turn. In-place continuation
of a Task is unsupported; this endpoint never advertises input-wait support.

## Task lifecycle and limits

- HTTP disconnect does **not** cancel accepted work. Re-query or subscribe to
  the exact Task; a subscription starts with a current snapshot, not a token
  cursor replay. `Last-Event-ID` recovery is not advertised.
- Cancellation addresses one Task, never whatever newer run happens to occupy
  the same context. A canceled response is returned only after runtime cleanup
  confirms cancellation. Completed/failed Tasks return `TASK_NOT_CANCELABLE`.
  If cancellation is not confirmed within 10 seconds, query the Task; no
  cancellation success is invented. Repeated confirmed cancellation is safe.
- The same Bot/messageId and identical message/metadata reuse the accepted Task
  during its retention window; conflicting content is rejected. Delivery choice
  and history projection may differ. This is not durable exactly-once delivery:
  do not automatically resend ambiguous work after restart/expiry.
- Tasks and deduplication receipts are **in memory**, retained for five minutes
  after terminal. At most 128 Tasks per Provider are admitted; unexpired/active
  Tasks are not evicted to admit more work. Restart loses Task lookup, results
  and receipts. Engine session mappings remain in the existing SQLite store;
  durable A2A job recovery is not implemented.
- Requests, cumulative progress text and final answers are limited to 1 MiB each.
  Progress retains only the latest chunk, not a growing transcript. Runtime's
  existing 32 MiB / 16,384-record event retention budget and execution deadline also apply. The
  completed A2A run's replay buffer is released; its bounded Task result remains.
- Each subscriber buffers 64 events. A lagging subscriber receives an error and
  closes without canceling work; query/resubscribe for the current Task.
- `historyLength` defaults to zero and limits returned input-message history.
  `ListTasks` supports context/status/time filters, history/artifact inclusion,
  and scoped pagination (default 50, maximum 100). Expired pagination cursors
  fail explicitly; listing is not a persistent cross-restart snapshot.

## Deliberate exclusions

This version supports **text/plain** only. File/Data parts, output media types
without a text match, push notification requests, nonempty tenant selection,
message extensions/reference Tasks and in-place Task continuation fail
explicitly. gRPC, SOFARPC, extended Cards, native tool events and Structured
Input/HITL extensions are not advertised. If an engine requests interactive
approval, the Task fails with a safe explanation and the pending execution is
denied/canceled; it cannot hang awaiting a reply this endpoint cannot accept.

A source checkout/configuration change does not upgrade a running Bridge.
Build/restart the appropriate public or internal distribution before testing
with BCS, and register the externally reachable Card with a matching Bearer
credential through BCS's existing A2A registration flow.
