# BCS and OpenClaw plugin `chat.abort`

## Scope of this PR

This change deliberately implements only the BCS and public OpenClaw BCN plugin
parts of `chat.abort`:

- BCS keeps the abort request `id` separate from the required target `run_id`
  (the original `chat.send.id`) in Provider HTTP and WebSocket frames.
- BCS routes abort only to the bot/provider that owns the target run and does
  not publish a premature frontend terminal event from the ACK.
- The OpenClaw BCN plugin resolves the BCS-visible `run_id` to its active local
  execution, aborts the existing controller, returns the structured ACK, and
  emits the authoritative `chat.event state=aborted` event.
- Contract and plugin tests cover required IDs, Provider body mapping, owner
  routing, active abort, not-running, session mismatch, and aborted emission.

No BaaS or Engine production code is changed by this PR. Their implementation
will be delivered by the owning team and verified in a later integration pass.

Engine's existing direct-frontend WebSocket contract is intentionally unchanged:
the frontend may omit `runId`, and Engine resolves the active execution within
the same WebSocket connection and session. The required `run_id` below applies
to BCS Provider and OpenClaw BCN plugin delivery, not to that direct Engine path.

## Wire contract

- `id` is the abort request idempotency key.
- `run_id` is required, non-empty, and identifies exactly one original
  `chat.send` run. Session-wide abort is not supported.
- Only a running target can be accepted. Pending, unknown, repeated, and
  terminal targets return `ok=true`, `aborted=false`, `reason=not_running`.
- An accepted request returns `ok=true`, `aborted=true`, `run_id=...`.
- The ACK records acceptance only. BCS closes the run after the later
  `state=aborted` event.
- Abort has no operation-local `tags` field. Shared-envelope `to_bot.tags` must
  not be used to select a new bot, binding, connection, or plugin instance.

## BaaS and Engine handoff

The follow-up BaaS integration must provide the following behavior before
end-to-end rollout without changing Engine's direct-frontend compatibility:

1. Validate the run, bot, and session ownership and accept abort only while the
   persisted run is `RUNNING`.
2. Persist an execution owner for each run, including instance, worker,
   binding, Engine run ID when available, and session key. A process-local session scan is not
   sufficient for a multi-machine deployment.
3. Route abort to the original execution owner. Same-process calls may use a
   local execution registry; cross-worker and cross-instance forwarding must
   use an authenticated internal control channel. Missing or unreachable
   ownership must fail closed.
4. Reuse the original Engine connection; abort must never use connection-pool
   load balancing or tags to select a replacement route. BaaS should pass an
   exact Engine run ID when its execution contract provides one. Engine retains
   its existing same-WebSocket, session-scoped fallback for direct frontend
   callers that omit `runId`.
5. Treat `ABORTED` as an independent terminal state. Final, error, timeout, and
   aborted updates must use first-writer-wins conditional transitions.
6. Propagate Engine `state=aborted` through non-stream, SSE, queue, and BCN
   uplink paths so BCS receives one authoritative aborted event for the run.
7. Provide durable or shared idempotency for abort request `id`; the same ID
   with a different target must return conflict.

Expected failures remain `BINDING_NOT_FOUND`, `ENGINE_ROUTE_UNAVAILABLE`, and
`ENGINE_ABORT_FAILED`, subject to final agreement with the BaaS owner.

## Integration acceptance

The later joint verification must cover:

- `BCS -> Provider/BaaS -> original Engine owner -> aborted event -> BCS run closed`.
- `chat.send` and `chat.abort` landing on different BaaS machines.
- Original owner disappearance, duplicate abort, final/abort races, and
  binding/session ownership mismatch.
- `BCS -> OpenClaw BCN plugin -> active controller -> aborted event -> BCS run closed`.
