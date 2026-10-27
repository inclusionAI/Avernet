# Retire Service Session Policies

## Approved scope and behavior

Remove Group.service_spec (timeout_seconds, max_concurrency, callback_config), the Service Session timeout scanner, Session callback dispatch/recovery, and callback-only lifecycle state/contracts. Keep authenticated Service Session invocation/query, normal completion and result persistence, activation CAS, state-machine node timeouts/recovery, Provider/IM/event callbacks, and legacy Task callback integration outside this Session feature.

Service invocation remains authorized by its existing service-key/Bot checks and Group membership; service_spec no longer gates admission. task.complete resolves the actual service Session, rather than Group configuration. Group detail consistently supplies the latest running Session. Clients supplying retired service_spec fields remain compatible: BCS consumes and ignores the value without persisting or executing it; the retired settings endpoint returns a retirement response. Normal completion remains atomic with its completion event. Reactivation no longer depends on callback state, including rows saved by older versions.

## Alternatives and compatibility

Disabling scanners alone leaves callback-pending reactivation dependencies and unused persistence contracts. Keeping a no-op callback dispatcher also leaves unnecessary Group reads and leases. The chosen removal changes the API contract explicitly and removes all runtime SQL reads/writes of retired columns. Existing numbered migrations stay frozen. An explicit forward cleanup migration removes retired columns/indexes after all old binaries have stopped; no production database operation is part of this change. Preserve historical legacy callback tables and unrelated callback concepts.

## Propagation and cost

Affected: domain/protocol DTOs, Group and Session Service APIs/repository ports, memory/MySQL implementations, HTTP/WS adapters, collaboration runtime, bootstrap, conformance fixtures and API documentation. Removal eliminates per-process running-service OFFSET scans, per-leader callback scans, callback Group loads/claims/confirms, and invocation policy COUNT queries. Preserve bounded running-service keyset reads used by state-machine recovery. No new cache, scanner or network call.

## Implementation plan

1. Add behavior tests: authenticated invocation without service_spec; compatibility acceptance and discard of retired request configuration; normal Session completion and reactivation regardless of persisted old callback state. Run the tests against the baseline and record expected failures.
2. Remove timeout/recovery task wiring and Session callback call sites. Remove callback-only contracts and state from memory/MySQL Session stores, keep completion events and activation fencing.
3. Remove service_spec configuration/types/store projection/mutation, retire settings handling, remove max-concurrency checks, and replace configuration-presence branches with Session-based behavior. Propagate fixture and client DTO changes.
4. Add forward schema cleanup and migration tests/documentation without editing historical migrations; preserve upgrade compatibility until cleanup.
5. Update affected conformance tests, docs and context. Keep existing file boundaries under the user-approved file-size exception; do not globally format.
6. Run focused tests, affected crates/workspace checks, architecture checks and migration validation where available. Record limitations and review the final diff.

## Validation checklist

- [ ] Baseline and expected failing regression tests
- [ ] No running Session policy scanner / callback sender or callback leases
- [ ] Legacy create config accepted and discarded; service invocation and authentication retained
- [ ] Memory and DB completion/reactivation and activation CAS conformance
- [ ] Forward migration upgrade/fresh schema validation
- [ ] Affected HTTP/WS/Group/Session/runtime tests and compilation
- [ ] Changed-source line counts and architecture validation

## Authorization

The user approved retirement after reviewing production configuration and usage, and requested implementation in a new worktree on 2026-10-27. No additional approval is needed to implement this local change. Production rollout and migration execution are separate operations.


User clarification: the existing source file length restriction is waived for this change. Keep the retirement focused; do not split unrelated code solely to reduce file size.

The removed Rust Service API fields/methods require a breaking contract version bump: bcs-service-api 0.4.0 → 1.0.0, following the repository API gate. Rebuild consumers together; this does not publish a release or change Plugin APIs.

User clarification: leave Backend code unchanged. Its optional legacy service_spec pass-through must not cause Group creation errors. Accept and discard this wire field, including null, without retaining runtime policy/configuration.

## Rebase compatibility

The latest dev branch already owns MySQL 036 and SQLite 037 for Bot authority.
This PR has not been deployed; its cleanup migrations move to MySQL 037 and
SQLite 038, preserving the upstream migrations and their checksums. The removed
scanner/callback wiring follows the upstream server and WebSocket dispatcher
file splits. Bot authority and terminal-output protections remain intact.
