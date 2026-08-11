# BCN Provider SSE HITL Implementation Plan

> **Execution rule:** implement each task test-first. Add one failing contract or
> unit test, run it and confirm the expected failure, then add only the
> production code needed to pass it.

**Goal:** Implement Provider 2.0 human-in-the-loop interactions end to end in
BCN: ingest `event: interaction` from the original Provider SSE, forward it to
the existing Workbench WebSocket, accept `interaction.resolve`, deliver the
resolution to the original Provider webhook, and track/replay the interaction
lifecycle in process memory.

**Architecture:** Add a transport-neutral `InteractionService` Application API
and a dedicated `bcs-interaction` service. The service coordinates a replaceable
`InteractionStorePort`, `CanResolveInteraction`, `InteractionProviderPort`, and
the existing `FrontendDeliveryPort`. Provider HTTP and Workbench WebSocket stay
delivery adapters; the bootstrap crate resolves their intentional circular
dependency by constructing the Provider transport first and injecting the
finished interaction service through a setter.

**Technology:** Rust 2024, Tokio, async-trait, serde/serde_json, reqwest, Axum
test servers, Cargo workspace tests.

**Approved specification:**
`src/bcs/docs/plans/2026-08-12-bcn-provider-sse-hitl-design.md`

## Baseline

The isolated worktree was created from `e44799fd` on branch
`codex/bcn-provider-sse-hitl`. The first `cargo test --workspace` compiled the
workspace and passed all completed suites except the existing
`bcs/tests/e2e_ws_messaging.rs::test_group_create_and_list`, whose local
`GET /groups` request timed out after 60 seconds while the suite ran in
parallel. Re-run that test alone during final verification.

## Task 1: Define the interaction wire contract

**Files:**

- Modify: `crates/contracts/bcs-protocol/src/stream/event.rs`
- Modify: `crates/contracts/bcs-protocol/src/stream/parse.rs`
- Modify: `crates/contracts/bcs-protocol/src/http/provider.rs`
- Modify: `crates/contracts/bcs-protocol/src/lib.rs` or existing re-export files
- Test: inline tests in `stream/parse.rs`
- Test: existing Provider HTTP contract tests that construct
  `ProviderWebhookRequest`

1. Add failing parser tests for flat top-level `interaction` requested and
   resolved payloads for `exec`, `ask_user`, and `mode_switch`.
2. Assert common fields (`runId`, `seq`, `ts`, optional `sessionKey`, `phase`,
   `interactionId`, `kind`) and preservation of the complete raw JSON payload.
3. Run `cargo test -p bcs-protocol stream::parse::tests` and confirm the new
   tests fail because `interaction` is `Unknown`.
4. Add `InteractionPhase`, `InteractionKind`, `InteractionEvent`, and
   `StreamEvent::Interaction`; parse only the common envelope and retain
   kind-specific JSON as raw data so the BCS boundary remains forward
   compatible with Provider-defined options.
5. Add optional `params: Option<Value>` to `ProviderWebhookRequest`. Existing
   request shapes omit it; `interaction.resolve` uses it for the shared domain
   payload.
6. Re-run the protocol tests and all `bcs-protocol` tests.

## Task 2: Define the Application API, core model, and outbound ports

**Files:**

- Create: `crates/service-api/bcs-service-api/src/core/interaction.rs`
- Create: `crates/service-api/bcs-service-api/src/application/interaction.rs`
- Create: `crates/service-api/bcs-service-api/src/port/interaction.rs`
- Modify: core/application/port module re-exports
- Modify: `crates/service-api/bcs-service-api/src/lib.rs`
- Test: inline pure transition tests in `core/interaction.rs`

1. Add failing core tests for `Pending -> Accepted -> Resolved`,
   `Pending -> Resolved`, invalidation of active states, and rejection of
   terminal-state reopening.
2. Define `InteractionKey`, `InteractionStatus`, and pure transition errors.
3. Define Application commands/results for Provider requested/resolved,
   Frontend resolve, pending replay, run invalidation, and terminal cleanup.
4. Define `InteractionService`, `InteractionStorePort`,
   `InteractionProviderPort`, and `CanResolveInteraction` traits. Keep HTTP,
   SSE, WebSocket frames, and response status codes out of these contracts.
5. Store the trusted `BotDeliveryTarget`, Provider bypass headers, Provider run
   ID, BCS session/group/bot/run metadata, raw requested payload, accepted
   idempotency key/fingerprint, audit fields, status, and terminal timestamp in
   `InteractionRecord`.
6. Run `cargo test -p bcs-service-api interaction`.

## Task 3: Implement the process-local Store and InteractionManagement

**Files:**

- Create: `crates/services/bcs-interaction/Cargo.toml`
- Create: `crates/services/bcs-interaction/src/lib.rs`
- Create: `crates/services/bcs-interaction/src/memory_store.rs`
- Create: `crates/services/bcs-interaction/src/management.rs`
- Create: `crates/services/bcs-interaction/tests/interaction_management.rs`
- Modify: `src/bcs/Cargo.toml`

1. Add failing Store tests for insert/idempotent duplicate/conflicting
   duplicate, session pending index, multiple independent interactions in one
   run, per-interaction in-flight guard, run invalidation, and terminal-only
   cleanup.
2. Implement `MemoryInteractionStore` using a Tokio lock around records plus
   run/session indexes. Never hold the lock across Provider or Frontend awaits.
3. Add failing Application tests with recording fakes for requested
   publication, real-time authorization, trusted route usage, retryable and
   non-retryable Provider failures, accepted duplicate suppression, different
   post-acceptance resolution rejection, Provider `resolved`, and replay.
4. Implement `InteractionManagement` orchestration. Fingerprint canonical JSON
   by recursively sorting object keys before serialization, then hash the
   canonical bytes together with kind and interaction ID.
5. Map Provider outcomes exactly as approved: transport/unreadable/default
   failure stays `Pending` and is retryable; explicit `retryable=false`
   invalidates; `ok=true` accepts.
6. Build Frontend interaction event envelopes in the Application service using
   server-owned routing and the raw Provider payload. Do not log answer or
   command bodies.
7. Run `cargo test -p bcs-interaction`.

## Task 4: Add exact-session resolve authorization

**Files:**

- Modify: `crates/services/bcs-group/src/application/management.rs`
- Modify: `crates/services/bcs-group/tests/management.rs`

1. Add failing tests for a non-Absent Human participant, an Absent Human, a
   Human owning a Bot in the exact session, ownership only elsewhere in the
   group, missing session, and unauthenticated caller.
2. Implement `CanResolveInteraction` for `GroupManagement` using the trusted
   session ID, current session participants, current registry ownership, and
   the existing actor/staff normalization helpers.
3. Keep this policy separate from chat-send authorization and do not accept a
   Frontend `fromActorId`.
4. Run the focused `bcs-group` tests.

## Task 5: Support Provider SSE ingestion and resolve POST

**Files:**

- Modify: `crates/adapters/http/bcs-provider-http/src/sse.rs`
- Modify: `crates/adapters/http/bcs-provider-http/src/lib.rs`
- Modify: `crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs`

1. Add failing SSE tests proving top-level interaction uses the shared seq
   counter, is not terminal, and does not enter MessageFlow.
2. Add a failing integration test whose fake Provider keeps the original SSE
   open, emits requested/resolved events, and records an independent
   `interaction.resolve` JSON request.
3. Add an injectable `InteractionService` to `HttpProviderTransport`.
4. Pass the original trusted Provider target and bypass headers into the SSE
   reader. Route `StreamEvent::Interaction` to `on_provider_requested` or
   `on_provider_resolved`; route agent/chat exactly as before.
5. Implement `InteractionProviderPort` on `HttpProviderTransport`. Reuse the
   original URL guard, token, Provider 2.0 headers, request client, and finite
   JSON ACK parser. Send `method=interaction.resolve`, the stored session/group
   and bot target, and the shared resolution payload in `params`. Never request
   `text/event-stream` for this POST.
6. On every SSE close/terminal path call `invalidate_run` best-effort after the
   message flow terminates the run.
7. Retain old `agent/approval` handling as unsupported compatibility behavior;
   the implemented HITL wire shape is the new top-level interaction event.
8. Run `cargo test -p bcs-provider-http`.

## Task 6: Add Workbench resolve and pending replay

**Files:**

- Modify: `crates/adapters/ws/bcs-ws/src/web/dispatcher.rs`
- Modify: `crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs`
- Modify: `crates/adapters/ws/bcs-ws/tests/group_session_ws.rs`

1. Add failing frame tests for `method=interaction.resolve` success,
   retryable Provider failure, explicit non-retryable failure, malformed
   request, missing interaction, unauthorized caller, and token scope
   consistency.
2. Add failing connect tests proving a session-bound connection receives its
   connect ACK followed by all and only `Pending` events. Assert a group-level
   user-bound connection does not trigger session replay.
3. Add `InteractionService` to `WebDispatchState` and dispatch the new method.
   Parse required `bcsRunId`, `interactionId`, `idempotencyKey`, and preserve
   the kind-specific resolution fields as one JSON object.
4. Derive the resolver only from `WorkbenchConnectionAuth`; validate optional
   client session/group fields against the binding but never use them for
   routing.
5. Extend the error sender to accept a complete `ErrorShape`. Use
   `interaction_resolve_failed` plus `retryable` and
   `details.interactionStatus` for provider/workflow failures; retain the
   existing generic codes for malformed, unauthorized, and missing requests.
6. After successful session-bound connect registration and ACK, call
   `list_pending` and send each normal interaction event directly to that
   connection. Duplicate live/snapshot delivery is intentional and harmless.
7. Run `cargo test -p bcs-ws`.

## Task 7: Wire the service and lifecycle in every composition path

**Files:**

- Modify: `crates/service-api/bcs-services-container/src/services.rs`
- Modify: service-container tests and test-support noops
- Modify: `crates/bootstrap/bcs/Cargo.toml`
- Modify: `crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/Cargo.toml` workspace dependencies

1. Add failing service-container builder tests proving `InteractionService` is
   required in production and supplied by test support.
2. Add an `interaction` field/builder method to `Services` and a no-op
   implementation for generic adapter tests.
3. In each bootstrap path, construct one `MemoryInteractionStore`, construct
   `InteractionManagement` with the same Provider transport, GroupManagement,
   and Frontend delivery instances, inject the service back into Provider
   transport, expose it through `Services`, and pass it to `WebDispatchState`.
4. Compose terminal observation rather than replacing the existing admin
   observer: add a small fan-out observer if needed so existing behavior and
   interaction invalidation both run best-effort.
5. Reuse `async_chat_run_retention_ms` for terminal cleanup. Perform lazy
   cleanup on interaction operations; do not add a dedicated timer or delete
   active records directly.
6. Run service-container tests and `cargo check -p bcs`.

## Task 8: Synchronize protocol documentation and contract examples

**Files:**

- Add/update: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md`
- Modify: `src/bcs/docs/plans/2026-08-12-bcn-provider-sse-hitl-design.md`
- Add/update any Provider protocol fixture or compatibility matrix touched by
  the adapter contract tests

1. Bring the local Provider 2.0 protocol document into the feature worktree
   without overwriting unrelated user work in the original worktree.
2. Replace stale `resolving`/single-pending/first-writer language with multiple
   independent pending interactions, transient `in_flight`, and
   `Pending/Accepted/Resolved/Invalidated`.
3. Document current implementation status, reconnect replay, exact-session
   authorization, retryability default, process-local loss on restart, and
   terminal retention.
4. Ensure requested/resolved samples for all three kinds and WebSocket/Provider
   resolve examples match the implemented serialization exactly.
5. Run `git diff --check` and search the docs for the superseded assumptions.

## Task 9: End-to-end verification and self-review

**Files:**

- Extend the closest existing Provider/Workbench E2E or adapter integration
  tests; do not introduce a second private test harness.

1. Exercise a fake Provider that holds one SSE open, emits two concurrent
   requested interactions, receives resolution HTTP calls in reverse order,
   emits corresponding resolved events, then finishes chat normally.
2. Exercise a retryable Provider failure followed by an explicit Human retry,
   and a concurrent same-interaction attempt rejected by the in-flight guard.
3. Run focused packages:

   ```bash
   cargo test -p bcs-protocol
   cargo test -p bcs-service-api
   cargo test -p bcs-interaction
   cargo test -p bcs-group
   cargo test -p bcs-provider-http
   cargo test -p bcs-ws
   cargo test -p bcs-services-container
   ```

4. Re-run the baseline timeout alone:

   ```bash
   cargo test -p bcs --test e2e_ws_messaging test_group_create_and_list -- --exact
   ```

5. Run `cargo test --workspace` and the applicable architecture checks. Do not
   run global `cargo fmt`; format only touched files if needed.
6. Inspect `git diff`, `git diff --check`, secret/private endpoint scans, and
   all new public-contract re-exports. Confirm no BAAS, Frontend source, DB
   migration, or `bcs_messages` write was added.
7. Commit coherent slices with conventional messages and report any test that
   remains unavailable or flaky with exact evidence.

