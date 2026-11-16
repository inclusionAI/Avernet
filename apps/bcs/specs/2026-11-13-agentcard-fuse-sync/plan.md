# Implementation plan

Status: implemented locally; see [delivery](delivery.md) for validation and gaps. [Specification](spec.md) governs scope.

## 1. Workspace and capacity gate

- Use an isolated `codex/` worktree based on a newly verified `origin/dev`.
- Record HEAD, dirty state, disk free bytes and expected checkout/build growth.
- User override for implementation: edit the existing selected RPC worktree
  directly; preserve all unrelated dirty files and do not create another host worktree.
- Recheck capacity before each checkout, dependency fetch and Cargo build/test.
  Stop if estimated peak build/download space plus a safety margin is not available.
  A small source checkout fitting on disk does not imply Rust builds will fit.
- Do not delete user worktrees/caches or run cleanup commands to obtain capacity.
- Specification creation does not authorize commit, push, deployment or live writes.

## 2. Public Core and default assembly

Affected public files:

- `apps/bcs/crates/services/bcs-bot/src/core/a2a_provider_registration.rs`
- `apps/bcs/crates/services/bcs-bot/src/core/a2a_bot.rs`
- `apps/bcs/crates/bootstrap/bcs/src/server/a2a_wiring.rs`
- `apps/bcs/crates/bootstrap/bcs/src/server/server_infrastructure.rs`

Add optional injected catalog ports; notify/log only after successful persistence.
Reuse the existing queue. No `CatalogChangePort` signature or queue-entry change.
Keep logs to locally available identifiers; no new trace-context propagation.

Run closest public Core/application conformance tests and add success/failure
notification assertions. Test source-only, visibility-only and combined updates.

## 3. Public embedding extension and minimal host wiring

Public work:

- Extend `BcsServerExtensions` with optional shared `CardChangeQueue` injection.
- Add an extensions-capable resolved-config startup entry; existing entry delegates
  with defaults. Do not duplicate logging initialization or runtime lifecycle code.
- Use the supplied queue in existing producer wiring and the sole Fuse worker.
- Document/reject incompatible explicit queue + disabled/V1 configuration.

Host work is restricted to dependency revision, startup/composition and tests:

- Assemble one shared queue and pass it to public startup.
- Add a `CatalogChangePort` assembly parameter for the independent RPC A2A cores.
- No provider handler, proto, mapping, owner policy, Card projection or Fuse client
  implementation changes. Do not opportunistically remove existing wrappers.
- Verify RPC publication/readiness and queue-worker lifetime on startup/shutdown.

This small host wiring change is required for the currently separate service
instances. Keeping two business implementations is explicitly forbidden.

## 4. Public Fuse HTTP logging

Primary file: `apps/bcs/crates/external-clients/bcs-fuse-client/src/cards.rs`.

Split the chained send/status/decode/ack expression sufficiently to record real
status and total duration without changing error/timeout semantics. Use fixed
error categories and `bcs_observation` target. Log only ID/type/name/counts/known
labels and acknowledgment metadata. Omit unavailable provider or origin IDs.

Add stub HTTP tests for success, non-2xx, transport timeout, malformed body and
negative acknowledgment, plus redaction and INFO file-filter assertions.
No extra provider queries or extra metadata fields in the Fuse wire body.

## 5. End-to-end focused acceptance

- Prioritize the three V1 REST write routes using real application/Core/SQLite and
  a local Fuse stub. Existing adapter-mock tests alone cannot prove synchronization.
- Legacy REST smoke and real RPC fixture call the same public cores.
- Assert injected queue identity/consumption, not just port invocation.
- Preserve queue merge/retry policy; do not require one HTTP request per mutation.
- Capture upstream Core IDs and downstream HTTP ID/ack; accept absent original
  trace/request IDs in the detached worker.
- Existing Group synchronization and disabled/V1 behavior must not regress.

Only when disk capacity is sufficient, run focused package tests for `bcs-bot`,
`bcs-app-a2a-bot`, `bcs-api-http`, `bcs-http`, `bcs-fusion`, `bcs-fuse-client` and
bootstrap tests selected by changed boundaries; run affected host provider and
architecture tests. Use the repository-required gates before any later push.
Do not use whole-workspace formatting. Check changed source files remain under
applicable line-count limits. Record any test blocked by disk/toolchain separately.

## 6. Documentation and delivery

Update the owning catalog and crate context documents only after implementation
is verified. Record public and host commits independently. Preproduction validation
is a separate authorized step: confirm the deployed revisions and use unique test
Bots, then check DB, Fuse saved acknowledgment and logs. Search visibility requires
separate index-readiness evidence, not merely a successful PUT.
