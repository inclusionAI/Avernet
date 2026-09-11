# Heartbeat-only Registration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Accept existing status heartbeats while retaining only their process-local liveness timestamp, eliminating dynamic-status storage and discovery matching.

**Architecture:** Both bot repositories retain `last_heartbeat`, connection state, identity, and capabilities, but no dynamic-status snapshot. The persistent repository no longer writes or reads status hashes. Inbound status payloads remain accepted and the HTTP update response still echoes the request; computed online status remains independent of these payloads.

**Tech Stack:** Rust, Tokio, SQLite test plugin, cache plugin contract, Axum HTTP/WS adapters.

## Approved behavior and propagation

- Remove `status`, `dynamic_summary`, `load`, and `updated_at` from stored registration state and `RegisteredBot`; retain the inbound `BotDynamicStatus` payload type.
- Remove dynamic-summary matching from application discovery and both repository implementations. Static names, summaries, skills, domains, scopes, visibility, and organization filters keep their semantics.
- Keep the existing 300-second `last_heartbeat` expiry and all connection/lifecycle-based `active`/`offline` calculations. Do not add cleanup scheduling or change disconnect policy.
- Remove status hash construction, serialization, reads, writes, and TTL refreshes. Existing keys expire naturally; no Redis cleanup commands or migrations are needed.
- Keep existing persistent-repository constructor signatures for downstream composition roots. Legacy cache/prefix arguments are accepted but neither retained nor used. Other stores still use the shared cache normally.
- `/providers/agentpass/resolve` serializes registration details without `bot.dynamic_status`, as explicitly authorized for this diagnostic endpoint. `POST /bots/status` retains its echo response; WS status acknowledgements remain `{ "updated": true }`.
- Update public Rust fixtures constructing `RegisteredBot`. OCB internal sources have no dynamic-status consumers or literals; no OCB code or gitlink changes are required.

## Task 1: Regressions before implementation

1. Run the existing bot-store unit tests as a baseline.
2. Add a cache implementation that fails any call and exercise real SQLite-backed registration, heartbeat, DB fallback, soft-delete enrichment, and reconnect through it.
3. Add heartbeat-expiry tests using manually aged private registration timestamps, including a previously renewed Bot and rejection of unknown Bot heartbeats.
4. Add repository/application discovery tests that reject a keyword present only in the inbound dynamic summary while still matching static metadata.
5. Extend the AgentPass HTTP contract test to assert absence of dynamic state and preserve identity/capability fields; preserve HTTP status echo and computed online-state tests.
6. Run focused tests and confirm the new expectations fail on the old implementation.

## Task 2: Remove dynamic status persistence

1. Remove the snapshot field from `bcs-domain::RegisteredBot` and both private registration records; update affected struct literals and obsolete snapshot assertions.
2. Change both `update_status` implementations to refresh only `last_heartbeat`, retaining known/unknown Bot outcomes.
3. Delete persistent status-cache helpers and their obsolete unit tests. Keep source-compatible constructors without retaining cache references.
4. Remove dynamic-summary selector branches. Keep the wire payload and echo types.
5. Update contract comments and current API documentation; remove obsolete cache-observation documentation caused by this change.

## Task 3: Validation

- `cargo test -p bcs-bot-store`
- `cargo test -p bcs-bot`
- `cargo test -p bcs-http`
- `cargo test -p bcs-ws`
- Compile affected workspace test consumers of the changed registration contract and run relevant architecture/contract gates.
- Review the diff for leftover status-cache access, accidental changes to heartbeat expiry or computed online state, and unrelated formatting.
- Record the actual validation results and any unavailable gate. Commit/push/PR creation follows a separate user request.

## Validation results

Implemented on `codex/bcs-heartbeat-only`, based on freshly fetched upstream
`dev` at `f35e9e68edae91d3c84bfce755db1f3a9fb79244`.

- Baseline bot-store unit tests: 46 passed, 5 ignored.
- Before implementation, the new regressions failed on status HSET, fallback
  HGETALL, retained registration payloads, dynamic-summary discovery, and the
  AgentPass response field, confirming that they exercised the old behavior.
- `cargo test -p bcs-bot-store -p bcs-bot -p bcs-http -p bcs-ws`:
  933 passed, 18 ignored, no failures across unit, integration, and doc tests.
- `cargo check --workspace --tests`: passed, including all consumers of
  `RegisteredBot` and their test fixtures. Existing unrelated warnings remain.
- The three SQLite-backed cache regressions were rerun after moving them to
  `tests/unit/heartbeat.rs`: all passed. They are included as a unit-test module
  so they can age the private monotonic heartbeat timestamp directly.
- Independent code review and `git diff --check`: no remaining findings.

The complete `scripts/ci/arch-check.sh` run was stopped during its prolonged
workspace test enumeration; it is **not** reported as passing. Port purity,
forbidden-symbol, store-boundary, and interceptor checks passed. Import rules,
trait naming, and the static R25 harness/entry checks have existing findings:
rerunning those checks on an archived clean base and comparing the final change
found no added findings. The configuration tests passed (119 tests), but their
gate also reports a missing explicit invalid-enum test pattern already absent
from the unchanged baseline configuration scope.

The new cache test double initially appeared in the production conformance scan
because its file was under `src`. Keeping these unit tests under `tests/unit`
correctly separates test implementations; the final static R25 findings match
the clean base exactly. No architecture checks or baselines were weakened.

No OCB sources or gitlink changes are needed for this implementation.
