# BCS streaming registration recovery implementation plan

**Goal:** Recover expired, disconnected, unpersisted Bot registrations and reuse identity reads within a streaming handshake.

**Architecture:** Core delegates a complete streaming admission to the repository contract. Stores own credential resolution, persistent identity lookup, and memory mutation. Ordinary get semantics, Provider policy, client retries and background cleanup remain unchanged.

**Base:** origin/dev at 0a0a4d00b15c1af6fd50208af8add8cbccf1fdf4.

## Completed implementation

1. Reproduce expired temporary identity refusal with the SQLite store; run the regression and observe its failure.
2. Keep the existing store organization and inline legacy tests. Defer file-size refactoring to a separate task; add only the admission logic and regression tests required for this fix.
3. Add a streaming admission repo operation accepting optional Bot ID/token. Preserve token-first identity resolution, real-token protection and MOCK promotion. Require all repository implementations, including the conformance wrapper, to provide the operation; keep workflow implementation out of the contract crate.
4. Implement per-request identity lookup reuse and conditional cleanup in SQL and local-file stores. Serialize admission with identity mutations without holding the global Bot map across DB I/O. Propagate read/write errors and reject deleted identities.
5. Delegate the Core streaming path to the operation; reuse loaded capabilities and avoid the existing repeated get/load calls.
6. Verify temporary/persistent/deleted/MOCK identities, active connections, failures, concurrency and SQL counts. Run store, Core, service API and affected adapter tests; inspect line counts and architecture checks.

## Acceptance

- Expired + disconnected + no persistent identity can receive a new token; old mappings are removed.
- Unexpired temporary identities require the original token; persisted identities remain protected after heartbeat expiry.
- Active connections are never reclaimed by the expiry path.
- DB failures never become missing identities; MOCK persistence failure never updates memory.
- Hot identity checks add no SQL; expired missing identity performs one point read per admission.
- No new background polling, cache TTL policy or client protocol change.

## Baseline

`cargo test --offline --manifest-path src/bcs/Cargo.toml -p bcs-bot-store --lib`: 48 passed, 4 existing external-DB tests ignored.

## Verified behavior and query budgets

The following budgets count SQL statements inside streaming Core/repository
admission, including capability refresh. They exclude Provider checks and later
onboard operations. Cold token reads may require one additional verification read
if a local identity writer overlaps the pre-lock lookup.

| Scenario | SQL reads | SQL writes | Result |
| --- | ---: | ---: | --- |
| New Bot ID, no token | 1 | 0 | Temporary registration |
| Expired, disconnected temporary identity, remembered old token | 1 | 0 | New token; all old mappings removed |
| Unexpired temporary identity, no credential | 0 | 0 | Refused |
| Valid temporary token reconnect | 1 | 0 | Same identity and token |
| Hot durable reconnect | 1 | 0 | Same identity; no capability write-back |
| Uncontended cold token reconnect | 1 | 0 | Full row reused |
| Expired durable identity, no credential | 1 | 0 | Refused |
| Soft-deleted identity, supplied Bot ID | 1 | 0 | Refused |
| MOCK promotion | 1 | 1 | Conditional durable token update before attachment |

The reproduced production-shaped stale temporary path formerly made two empty
identity reads and then refused the Bot. The new path makes one identity read
and admits it. This statement count does not forecast total production QPS: other
checks still run and actual reconnect traffic must be measured after rollout.

## Compatibility and review

The repository port gains a required internal method; SQL/local implementations
and the conformance wrapper were updated together. External protocol and database
schema remain unchanged. Authenticated reconnect semantics are retained; an active
identity cannot be reclaimed as a new registration. No background cleanup runs.

General MemoryBotRepo reads historically keep disconnected token-bearing objects
visible. Streaming admission uses heartbeat age directly, without changing those
read semantics. Both stores require persistent absence before expired recovery.

Independent review found and fixed two races: newly rotated durable tokens must
not be rejected by old memory, and a concurrent runtime credential update must not
be overwritten by an admission snapshot. Tests first reproduced each failure.
Token-index hints are validated against storage, and MOCK promotion preserves
runtime credentials while expired temporary recovery discards them.

The existing `lib.rs` and `memory.rs` organization and inline tests are retained.
The three added production files contain the new admission logic; the three
added test files cover its regressions. Existing oversized roots remain intact
per the requested scope: file-size refactoring belongs to a separate task.

## Final source layout validation

- `cargo test --offline --manifest-path src/bcs/Cargo.toml -p bcs-bot-store -p bcs-bot`: 267 passed, 0 failed, 4 existing external-DB tests ignored, across 23 test binaries/doc-test runs.
- `git diff --check`: passed.
- Existing methods and inline legacy tests are restored to their original files. The original roots remain oversized (`lib.rs`: 3,773 lines; `memory.rs`: 2,747 lines); the source-size requirement is deferred with the explicitly requested separate refactor. No CI rule or allowlist was changed.

## Earlier broader validation

The following results were obtained before restoring the original source layout;
only the focused Store and Core suites above were rerun after that adjustment.

- Host `cargo test --offline --manifest-path src/bcs/Cargo.toml -p bcs-bot-store -p bcs-bot -p bcs-service-api -p bcs-ws`: 636 passed, 0 failed, 18 existing ignored tests, across 65 test binaries/doc-test runs.
- Host `cargo test --offline --manifest-path src/bcs/Cargo.toml -p bcs --lib config --quiet`: 126 passed. Sandbox runs of socket/OTLP tests failed with `Operation not permitted`; the same tests passed on the host.
- `cargo check --offline --manifest-path src/bcs/Cargo.toml -p bcs-bot-store -p bcs-bot -p bcs-service-api -p bcs-ws --all-targets`: passed. Existing warnings remain in unchanged provider code/tests.
- Architecture runner is not green on DEV: dependency script fails at line 36 (`unbound variable`), reproduced from the unchanged base archive. Static import, trait-naming and R25 failures match the base exactly (4, 6 and 151 distinct failures; zero added findings). Port purity, forbidden-symbol, store-boundary and interceptor checks passed. Full-workspace conformance discovery was interrupted; no claim is made that the complete architecture runner or full-workspace/Singlebox suite passed.
- Independent code review completed; reported token-rotation and runtime-credential races were fixed and covered by regression tests.

Integration branch: `codex/bcs-streaming-registration`, targeting
`inclusionAI/Avernet:dev`. Production deployment is outside this change.
