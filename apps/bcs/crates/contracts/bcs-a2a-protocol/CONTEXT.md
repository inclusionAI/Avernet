# bcs-a2a-protocol Context

A2A (Agent2Agent) protocol v1.0 wire contract for the JSON-RPC binding. It is
the single source of A2A types for the bcs-bridge A2A server
(`tools/bcs-bridge/core/src/a2a/`) and the BCS `a2a/1.0` Provider downlink
(`adapters/http/bcs-provider-http/src/a2a.rs`). Unrelated to
`bcs_protocol::a2a`, which models BCS-internal bot-to-bot chat.

Normative source: `specification/a2a.proto` (package `lf.a2a.v1`) of the A2A
specification. JSON follows ProtoJSON: camelCase fields, full enum names
(`ROLE_USER`, `TASK_STATE_WORKING`), `oneof` members as mutually exclusive keys
(no `kind` discriminator). v0.3 shapes are rejected.

## Provides

- Core objects: `Message`, `Part` (`text` | `raw` | `url` | `data`), `Task`,
  `TaskStatus`, `Artifact`, `TaskStatusUpdateEvent`, `TaskArtifactUpdateEvent`,
  `StreamResponse`, `SendMessageResponse`, push notification config.
- Request params: `SendMessageRequest`, `GetTaskRequest`, `TaskIdRequest`
  (cancel/subscribe), `PushConfigRequest`.
- `AgentCard` and capability/skill/extension types.
- JSON-RPC 2.0 envelope and the A2A error model (`A2aErrorCode`, ErrorInfo
  reasons), method names, `A2A-Version` / `A2A-Extensions` headers.
- Avernet extension URIs and `metadata` keys, kept identical to the Python
  engine adapter (`engine/adapter/.../transport/a2a/`):
  - `extensions::PROGRESS` — WORKING `metadata.kind`: `tool`, `thinking`,
    `lifecycle`, `commentary`, `commentary.delta`, `interaction.requested`,
    `interaction.resolved`; `engine_frame` carries the lossless engine payload.
  - `extensions::REPLAY` — `SubscribeToTask` honours SSE `Last-Event-ID`;
    every event carries `metadata.seq` equal to its SSE `id`.
  - `extensions::INJECT`, `extensions::CANCEL`.
  - `meta::PENDING_INTERACTIONS` — `Task.metadata` mirror of unanswered HITL
    requests for polling/snapshot consumers.

## Consumes

- `serde`, `serde_json` only.

## Allowed dependencies

- Serialization crates.

## Forbidden dependencies

- `bootstrap/*`, `adapters/*`, `services/*`, `plugin-api/*`, `plugins/*`
- Other contract crates (A2A is an external standard, independent of BCN)
- Runtime HTTP/SSE clients or servers

## Configuration

- No runtime configuration. Protocol version negotiation (`1.0`) is performed by
  the server/client adapters.

## Runtime ownership

Stable wire objects only. Task lifecycle, delivery modes and BCN translation
belong to the bridge server and the BCS downlink adapter.

## Tests

- `cargo test -p bcs-a2a-protocol` — golden tests including the spec's sample
  AgentCard round-trip and v0.3 rejection.
