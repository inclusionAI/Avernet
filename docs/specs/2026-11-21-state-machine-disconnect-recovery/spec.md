# StateMachine Bot 断连失败恢复

## Owning modules

- `apps/bcs/crates/adapters/ws/bcs-ws`: detects the transport disconnect.
- `apps/bcs/crates/services/bcs-collaboration-runtime`: owns node/run state transitions and public events.
- `apps/bcs/crates/services/bcs-collaboration-store`: persists bounded active-node lookup and atomic terminal transitions.
- `singlebox/agents/6bots_undercover_game_profile/referee`: consumes the failure result and retries the current phase.

## Problem

When a Bot disconnected, BCS could keep an assigned StateMachine node in `pending` until a later dispatch or the normal 420-second timeout. The manager did not receive a durable failure result, so the game remained stuck until a human sent `卡住了`.

## Contract

1. A WebSocket Bot disconnect processes at most the first 128 currently non-terminal (`pending`, `ready`, `running`, or `retry_scheduled`) node records assigned to that Bot, ordered by `(run_id, node_id)`. Pending dispatches are included so the Run fails without waiting for a later delivery attempt. Candidates beyond the bound are not processed by that callback and have no immediate-failure guarantee; reaching the bound emits a warning rather than turning the WebSocket cleanup path into an unbounded database drain. For the six-player Undercover profile, the bound is not reached.
2. Any selected non-terminal assigned node is treated as disconnect-sensitive, including a pending node that is waiting on upstream work or may later be skipped by branch selection; the fail-fast policy is intentional. The first successful failure CAS transitions the Run to `failed`; concurrent or late events are ignored by the existing attempt/run fences.
3. The first terminal transition persists one structured, sanitized manager result and the public `state_machine.run.failed` event in the same local store transaction. The manager message uses the stable `run-result:{run_id}` identity while successful legacy results retain `state-machine-result:{run_id}`.
4. The result contains only bounded metadata: workflow, terminal status, failed node/attempt, reason category, and completed node IDs. Raw error text, node artifacts, and player words remain internal diagnostics.
5. User cancellation records `state_machine.run.aborted`, publishes a structured `user_aborted` result, and never enters automatic retry notification handling. Session and Group cleanup use `session_closed` and `group_closed` reason categories respectively.
6. The referee skill uses the current `uc status` phase, then retries `open-round` or `open-vote` at most twice. It never infers a new phase from a stale failed node name.

## Checkpoint and message identity semantics

The implementation deliberately keeps the existing durable delivery checkpoint key
`smrun:{run_id}:chat-result` and `operation_kind=chat_result`. This is the
persistence/lease identity used by the existing Send queue and recovery scanner;
it is not the external message identity.

- A successful legacy result is published with `state-machine-result:{run_id}`.
- A failed or aborted terminal result is published with `run-result:{run_id}`.
- `terminal_result != null` identifies the terminal-result payload inside the
  durable checkpoint.
- An existing checkpoint is immutable. A terminal transition inserts its
  checkpoint only when no checkpoint exists; it never replaces an already saved
  success, failure, aborted, pending, delivering, or superseded payload.

This keeps the delivery lifecycle and restart recovery unchanged while giving
terminal results a distinct public message identity. The storage contract tests
cover Memory, SQLite, and the real MySQL Text/Prepared paths.

## Compatibility and risk

No database migration is required. Existing chat-result checkpoints remain immutable. Terminal failure/abort transitions write the checkpoint before commit; delivery happens after commit and can be repaired by the progression scanner, including after an aborted Run is recovered from a process restart. Successful publication behavior remains unchanged; an ambiguous or already-created success checkpoint is never overwritten by a terminal failure result.

## Validation

- Deterministic contract tests cover sanitized terminal-result rendering and the `run-result` message identity.
- Immediate disconnect regression test in `runtime_progression/failure_recovery.rs`.
- Real Singlebox fault injection: 10/10 independent Bot kills reopened the same speech phase within 60 seconds; stuck=0, late=0; all 10 original terminal-result checkpoints finished `delivered`. Detailed CSV and summary are in `fault-injection-10-runs.csv` and `fault-injection-10-runs.md`.
- Runtime progression suite: 271 passing tests, including the pending-node disconnect regression, disconnect terminal-result idempotence regression, and cross-platform migration assertions.
- Terminal-result storage contracts pass for Memory and SQLite; the real MySQL contract passes in Text and Prepared protocols for fixed-loop v1 and v2.
- `cargo test -p bcs-cli --test e2e custom_collaboration_test`: 11 passed.
- Event contract tests: 13 passed.
- Undercover referee Python tests: 52 passed.
- `cargo check -p bcs -p bcs-ws -p bcs-message-flow -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-service-api`: passed.
- Manual fault injection reproduced the original stuck path before the change: Bot disconnect at `2026-11-20 16:19:42`, node failure only after the 420-second timeout.
