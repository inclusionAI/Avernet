# OpenAPI V1 Session History Legacy Message Compatibility Design

**Date:** 2026-08-13
**Module:** BCS

## Problem

BCN currently exposes two Session history reads:

- `GET /sessions/{session_id}/messages` returns a bare array of rich legacy
  `GroupMessage` objects and is used by the Workbench and `bcs-cli`.
- `GET /openapi/v1/collaboration/sessions/{session_id}/messages` returns a
  cursor page whose messages use the smaller V1 `SessionMessage` model.

The implementations also diverge. The legacy route uses the existing
`GroupMessageHistoryService`, which owns the DB-versus-Bot history decision and
rich message normalization. The V1 facade reads `MessageRepoPort` directly and
projects persisted messages into the smaller V1 shape. Consequently V1 drops
fields the Workbench needs, including role, Bot display name, run identity,
history metadata, tool metadata, and attachments. Moving the Workbench to the
published OpenAPI path would therefore break message rendering.

## Decision

V1 will reuse the existing legacy Session history application capability. It
will not introduce a second history query implementation and will not change
the existing history service, Bot history implementation, repo port, store, or
database schema.

The two HTTP contracts will differ only at the response envelope:

```text
Legacy: GroupMessage[]
V1:     Envelope<GroupMessage[]>
```

Each object in V1 `data` must be byte-for-byte compatible in JSON field names,
values, ordering semantics, and optional-field omission with the corresponding
legacy `GroupMessage` object for the same authorized view and normalized query.

## Goals

- Preserve the standard V1 success and error envelopes.
- Make every V1 message item use the legacy `GroupMessage` wire contract.
- Reuse `GroupMessageHistoryService::get_session_history` for Chat and
  ManagerWorker Sessions.
- Reuse the legacy collaboration-runtime history behavior for StateMachine
  Sessions.
- Preserve V1 Gateway Principal and View Actor authorization rules.
- Preserve the legacy endpoint without changing its request or response.
- Keep `additionalProperties: false` and explicitly document every supported
  `GroupMessage` field in OpenAPI.
- Remove the V1 direct `MessageRepoPort` history read and its duplicate message
  projection logic.

## Non-goals

- Do not change `bcs-message`, `bcs-message-flow`, `MessageRepoPort`,
  `bcs-message-store`, Bot `chat.history`, or database tables.
- Do not add `next_cursor` or `has_more` to either HTTP response.
- Do not introduce composite cursor pagination into the unified facade.
- Do not make the V1 and legacy authentication models identical.
- Do not remove the legacy endpoint in this change.
- Do not change message send, WebSocket, callback, or streaming behavior.

## Public API Contract

### Request

The V1 request uses the query vocabulary already supported by the legacy
history application command:

```text
GET /openapi/v1/collaboration/sessions/{session_id}/messages
    ?view_bot_id=<actor-id>
    &limit=<integer>
    &before=<timestamp-ms>
```

- `before` is an optional non-negative integer millisecond timestamp, not a
  composite string cursor.
- V1 may retain its existing `limit` default and maximum validation at its
  delivery boundary. The normalized value is passed to the existing service.
- V1 continues to resolve omitted `view_bot_id` to the authenticated Human
  Actor and to authorize an explicit owned Bot before calling history.

### Success response

```json
{
  "code": 20000,
  "message": "OK",
  "data": [
    {
      "id": "message-id",
      "timestamp": 1786590000000,
      "sender": "bot-1",
      "content": "done",
      "message_type": "bot",
      "bot_name": "Worker",
      "role": "assistant",
      "run_id": "run-1",
      "historyMeta": {},
      "metadata": {},
      "attachments": []
    }
  ],
  "request_id": "request-id"
}
```

The `GroupMessage` schema follows the existing Rust serde behavior:

- Required: `id`, `timestamp`, `sender`, `content`, `message_type`, and `role`.
- Conditional: `bot_name`, `run_id`, `historyMeta`, `metadata`, and
  `attachments`.
- Optional values remain omitted when absent; an empty `run_id` remains
  omitted.
- `content` is the legacy normalized content. V1 must not stringify the raw
  persisted JSON independently.
- Tool calls, StateMachine panels, and attachments retain the legacy
  projection and metadata.

The old V1 message fields `session_seq`, `sender_id`, `sender_type`, `kind`, and
`created_at` are removed. This is an intentional breaking change to the
published V1 message-item contract. Repository search found no production
Workbench consumer of that shape; the Workbench still consumes the legacy
shape.

The V1 `SessionMessagePage` and `SessionMessagePageEnvelope` schemas are
replaced by a `SessionMessagesEnvelope` whose `data` is an array of
`GroupMessage`. The response does not retain a redundant `{ "messages": [] }`
object layer.

### Errors

V1 continues to return the standard `ErrorEnvelope`:

- missing or invalid Gateway Principal: `401 unauthenticated`
- unauthorized View Actor or non-Participant: `403 forbidden`
- missing Session: `404 session_not_found`
- invalid `limit` or `before`: `400 invalid_request`
- internal history or runtime failure: `500 internal_error`

Legacy status codes and legacy error JSON are unchanged. Existing fallback
semantics are also unchanged: if all compatible Bot sources produce no
history and the legacy service returns an empty message list, V1 returns a
successful Envelope containing an empty array.

## Application Design

### V1 facade

`bcs-app-session` remains responsible for V1-specific authorization:

1. Require the authenticated User from `AuthenticatedCaller`.
2. Resolve omitted `view_bot_id` to `human_<user.id>` or validate an explicit
   exact-`created_by` owned Bot.
3. Load the Session and require the selected View Actor to be a Participant.
4. Convert the authenticated Human into the existing transport-neutral
   `CallerContext::Human` and construct `SessionHistoryCommand` with the
   explicit resolved View Actor.
5. For Chat and ManagerWorker, call the injected
   `GroupMessageHistoryService::get_session_history` and return its
   `messages` unchanged.
6. For StateMachine, call the injected
   `CollaborationRuntimeService::get_state_machine_session_history` with the
   same authorization behavior as the legacy route and return its messages
   unchanged.

The facade does not perform per-message conversion.

### HTTP adapters

The legacy adapter continues returning `Json(result.messages)`.

The V1 adapter returns:

```rust
Json(Envelope::success(20_000, "OK", messages, request_id))
```

The adapters do not query a repo, call a Bot, normalize content, enrich
attachments, or implement message visibility policy.

## Data Flow

```text
OpenAPI V1 request
  -> bcs-api-http
  -> bcs-app-session (V1 authorization and source selection)
  -> existing GroupMessageHistoryService or CollaborationRuntimeService
  -> Vec<GroupMessage>
  -> Envelope<Vec<GroupMessage>>

Legacy request
  -> bcs-http
  -> existing GroupMessageHistoryService or CollaborationRuntimeService
  -> Vec<GroupMessage>
  -> bare Vec<GroupMessage>
```

For Chat and ManagerWorker, the existing history implementation retains all
current behavior:

- configured DB-versus-legacy cutoff
- persisted message visibility and owner filtering
- Bot/Provider `chat.history` fallback
- protocol-version-aware Bot history keys
- Bot-name, role, tool metadata, queued-message, and content normalization
- persisted StateMachine panel-anchor merging where currently applicable
- attachment metadata and short-lived URL enrichment

## Crate Dependencies

Delivery crates continue to depend only on Service API contracts. Concrete
selection remains in the `bcs` composition root.

```text
bcs-api-http ----------> bcs-service-api
bcs-http --------------> bcs-service-api
bcs-http --------------> bcs-services-container -> bcs-service-api
bcs-app-session -------> bcs-service-api
bcs-app-session -------> bcs-domain
bcs-message -----------> bcs-service-api + bcs-domain
bcs-message-flow ------> bcs-service-api + bcs-domain
bcs-message-store -----> bcs-service-api

bcs bootstrap ---------> all concrete implementations above
```

The direct production dependency from `bcs-app-session` to `bcs-message` is
removed. `bcs-app-session` receives `Arc<dyn GroupMessageHistoryService>` and
the collaboration runtime contract through dependency injection. Neither HTTP
adapter gains a dependency on `bcs-message` or another adapter.

No concrete dependency is added from `bcs-message` to `bcs-message-flow`,
`bcs-message-store`, or `bcs-collaboration-runtime`.

## Contract and Test Strategy

### Cross-adapter compatibility

Add a test that serves both adapters against the same recording history
service and verifies, for equivalent authorized inputs:

```rust
assert_eq!(v1_response["data"], legacy_response);
```

The fixture must cover content, role, Bot name, run ID, history metadata, tool
metadata, attachments, message order, and optional-field omission.

### V1 facade

- Prove V1 delegates Chat and ManagerWorker history to the injected
  `GroupMessageHistoryService`.
- Prove the resolved Human or owned-Bot View Actor is passed explicitly.
- Prove returned `GroupMessage` values are not reprojected.
- Prove StateMachine history calls the collaboration runtime.
- Prove the facade no longer reads `MessageRepoPort` for history.

### OpenAPI and route contract

- Success `data` is an array of complete `GroupMessage` objects.
- `SessionMessagePage`, `SessionMessage`, `MessageSenderKind`, and
  `SessionMessageKind` are absent when no longer referenced.
- `next_cursor` and `has_more` are absent.
- `before` is an integer timestamp.
- `GroupMessage` and the Envelope retain `additionalProperties: false`.
- Error responses retain the standard V1 error envelope and stable codes.
- The generated Gateway `bcn.openapi.json` matches the BCS YAML contract.

### Regression

- Legacy route still returns a bare array.
- Existing `bcs-message`, `bcs-message-flow`, message-store, Provider history,
  and attachment tests remain unchanged and pass.
- No database migration or repo-port contract test change is expected.

## Validation

Run at least:

```bash
cargo test -p bcs-app-session
cargo test -p bcs-api-http
cargo test -p bcs-http
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
```

Also regenerate the Gateway BCN OpenAPI artifact through the repository's
deterministic exporter and run the affected Gateway schema compatibility tests.

## Compatibility and Risk

- The legacy HTTP contract is unchanged.
- V1 success and error envelopes are preserved.
- The V1 message-item schema is intentionally replaced with the legacy
  `GroupMessage` schema. Any existing consumer of the old V1-only fields must
  migrate.
- V1 timestamp pagination inherits legacy behavior, including the possibility
  that messages sharing a timestamp at a page boundary are skipped. Fixing
  pagination requires a separate contract change and is outside this scope.
- V1 and legacy authorization remain intentionally different. Compatibility
  means identical message wire format and common history implementation after
  each API has authorized and normalized its selected Actor.

## Rollback

Rollback restores the old V1 `SessionMessagePageEnvelope`, direct repo read,
and projection types. No database or stored-message rollback is needed because
this change modifies only contracts, application delegation, and delivery
serialization.
