# StateMachine Terminal Result Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the StateMachine failure/abort path with Issue #2610 by making terminal results structured, sanitized, durable, idempotent, recoverable, and consumable by the undercover referee without adding speculative defensive behavior.

**Architecture:** Keep Bot disconnect detection in the WebSocket adapter and state transitions in collaboration runtime/store. Extend the existing terminal-result publication boundary into one canonical Run-scoped checkpoint that is written with the terminal CAS, then delivered after commit through the existing message-flow/Send machinery and repaired by the progression scanner. Keep raw diagnostics internal; expose only a bounded typed result to the manager and public event consumers.

**Tech Stack:** Rust/Tokio, BCS service-api/store/runtime/message-flow contracts, serde JSON event schemas, Python referee Skill scripts/tests, Singlebox fault-injection tooling.

**Spec:** `docs/specs/2026-11-21-state-machine-disconnect-recovery/spec.md` plus Issue #2610's terminal-result contract.

## Global Constraints

- Preserve the repository architecture constitution and BCS module boundary rules.
- Do not put transport or database policy in the collaboration domain core.
- Do not hold a database transaction across network delivery.
- Do not expose raw error text, node artifacts, or player words in manager/public result payloads.
- Use one canonical Run-scoped terminal-result identity; do not maintain parallel success/failure checkpoints.
- Do not add a database migration unless the existing checkpoint schema cannot represent the required terminal result.
- Keep the bounded disconnect lookup and recovery scan limits explicit.
- Do not run global formatting; keep Rust diffs local and style-preserving.
- Keep new/modified source files below the repository's enforced size limit.

## File Map

- Modify `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/collaboration.rs`: add the typed terminal-result transition/checkpoint inputs and reason categories used by stores/runtime.
- Modify `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/collaboration_publication.rs` and `apps/bcs/crates/service-api/bcs-service-api/src/port/state_machine_result.rs`: unify completed/failed/aborted result payload semantics and stable identity without breaking success callers.
- Modify `apps/bcs/crates/services/bcs-collaboration-store/src/lib.rs` and `apps/bcs/crates/services/bcs-collaboration-store/src/publication.rs` or add a focused `terminal_result.rs`: implement save/claim/finish/recover operations with CAS and bounded state.
- Modify `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs`, `runtime_publication.rs`, and add a focused terminal-result helper only if the existing file remains readable: classify failures, build sanitized results, terminate atomically, and recover pending terminal results.
- Modify `apps/bcs/crates/services/bcs-message-flow/src/group_flow/*`, queued payload/admission modules, and bootstrap wiring only where the existing Send queue requires explicit `run_result` handling.
- Modify `apps/bcs/crates/bootstrap/bcs/src/state_machine_progression_scanner.rs` and state-machine result contract tests for post-crash recovery.
- Modify `apps/bcs/api-contracts/events/v1/catalog.yaml`, event envelope schema, and metadata-only fixtures for failed/aborted public events.
- Modify `singlebox/agents/6bots_undercover_game_profile/referee/skills/undercover-game-referee/SKILL.md` and phase references to consume typed results, use status-first retries, and reject stale/aborted results.
- Add/modify focused Rust tests under `apps/bcs/crates/services/bcs-collaboration-runtime/tests/runtime_progression/`, store/message-flow contract tests, and Python tests under the referee skill.
- Add a bounded fault-injection runner and report under the existing `docs/specs/2026-11-21-state-machine-disconnect-recovery/` scope; keep it test/tooling-only.

### Task 1: Lock the terminal-result contract with failing tests

- [ ] Add Rust contract tests for the terminal result fields: run id, workflow name, terminal status, failed node/attempt, reason category, bounded completed-node list, and creator.
- [ ] Add assertions that public/manager content contains no raw error text, player words, node artifacts, or unbounded data.
- [ ] Add event-envelope fixture assertions for `state_machine.run.failed` and `state_machine.run.aborted`.
- [ ] Run the focused tests and confirm they fail for the current raw-error/legacy payload behavior.
- [ ] Introduce typed enums/structs and stable serialization with the smallest API change that preserves completed-result callers.
- [ ] Run the focused contract tests and confirm they pass.

### Task 2: Make terminal state and result checkpoint atomic and idempotent

- [ ] Add a failing store test for first terminal CAS saving exactly one canonical result checkpoint.
- [ ] Add failing tests for concurrent failure/abort, duplicate terminal transitions, success-checkpoint conflict, and session/group boundary rejection.
- [ ] Implement the store transition/checkpoint operation using the existing eventful transition and checkpoint primitives; do not add a new migration unless required.
- [ ] Add claim/finish/retry/uncertain outcomes for post-commit delivery without retrying ambiguous external calls blindly.
- [ ] Run store contract tests and verify duplicate transitions and checkpoint replay are rejected/idempotent.

### Task 3: Integrate runtime failure classification and recovery

- [ ] Add failing runtime tests for disconnect failure, human timeout, startup/delivery/publication failure categories, user abort, late node result, and scanner recovery.
- [ ] Implement category mapping at the error boundary instead of parsing error strings in the referee.
- [ ] Keep raw diagnostics in internal Run/error storage and build the sanitized terminal result separately.
- [ ] Update `fail_run`/abort handling so the first successful terminal transition creates the result checkpoint and public event; later callers only observe the existing terminal state.
- [ ] Update progression recovery to publish pending results exactly once after a restart.
- [ ] Run focused runtime progression tests, including the existing disconnect regression.

### Task 4: Wire the existing Send queue and public event contracts

- [ ] Add failing message-flow tests for managed queued recipients, legacy synchronous recipients, empty/rejected admissions, duplicate result identity, and delivery uncertainty.
- [ ] Implement one `run_result` terminal message type using the canonical checkpoint; do not create a second parallel result path.
- [ ] Update event catalog/schema/fixtures and conformance tests for failed/aborted metadata-only events.
- [ ] Update scanner/bootstrap wiring and metrics without moving transport policy into runtime core.
- [ ] Run affected message-flow, eventing, store, and runtime tests.

### Task 5: Strengthen the referee recovery contract

- [ ] Add Python tests for structured failure parsing, status-first phase recovery, two-retry ceiling, stale run/result rejection, and `user_aborted` no-retry.
- [ ] Implement only the required command routing changes; keep the existing `卡住了` path as a final fallback.
- [ ] Update phase-machine/reference docs with the typed result fields and recovery behavior.
- [ ] Run referee unit tests and the existing profile contract tests.

### Task 6: Add deterministic and real fault-injection evidence

- [ ] Add a deterministic mock-model fault-injection runner that can repeat 10 failures and assert 60-second recovery semantics without external rate-limit flakiness.
- [ ] Add assertions for one result, one public event, no stale artifact advancement, and no fourth retry.
- [ ] Run the 10-case deterministic batch and save a concise report under the existing spec directory.
- [ ] Run one real-model singlebox smoke injection and record the observed timeline separately from deterministic CI evidence.

### Task 7: Final validation and delivery package

- [ ] Run `git diff --check`, focused Rust tests, affected message-flow/store tests, referee tests, and the deterministic fault-injection batch.
- [ ] Run the required BCS compile/check command and record any unrelated pre-existing migration-text failures precisely.
- [ ] Review the diff for raw secrets, raw errors in public payloads, unnecessary defensive branches, and file-size violations.
- [ ] Update `docs/specs/2026-11-21-state-machine-disconnect-recovery/spec.md` and `research.md` with the final contract and evidence.
- [ ] Prepare the PR body with Problem, Solution, Validation, Compatibility and risk, Spec, and Related issues.
- [ ] Commit only the intended files with a scoped `fix(bcs)` message; do not include runtime state or API keys.

## Verification Commands

```bash
cd apps/bcs
cargo test -p bcs-collaboration-runtime --test runtime_progression
cargo test -p bcs-collaboration-store --lib
cargo test -p bcs-message-flow
cargo check -p bcs -p bcs-ws -p bcs-collaboration-runtime -p bcs-collaboration-store
```

```bash
cd D:\ava\Avernet
 git diff --check
```

The final report must distinguish deterministic CI evidence, real singlebox evidence, and any unrelated pre-existing failures; no success claim may rely on a prior run alone.
