# Flow timeout and terminal-write admission

## Frozen run budgets

New root runs and child runs resolve `effectiveFlowTimeoutMinutes` once, at
creation, and persist it in their TaskFlow state. A workflow's explicit
`flowTimeoutMinutes` overrides the global default; **zero is a valid disabled
budget**. Children resolve their own workflow budget, not their parent's.
Changing a deployed definition or the global configuration cannot shorten an
already-frozen run.

Watchdog and restart recovery use the same policy order:

1. Persisted `effectiveFlowTimeoutMinutes`.
2. A historical `workflowSnapshot`, whose nonempty `id` must match the run's
   `workflow_id`. Identity-only snapshots remain supported; nodes/title/version
   are not required just to establish timeout policy. Canonical
   `flowTimeoutMinutes` owns the policy when present; otherwise the historical
   `timeoutMinutes` spelling is accepted.
3. For a snapshot with neither timeout field, the global default. Never load a
   newer deployed override in place of that snapshot.
4. Only runs without a snapshot/pin may use the legacy workflow resolver, then
   the global default when the definition explicitly has no override.

Malformed JSON, invalid timeout fields, mismatched snapshot identity, unavailable
pinned snapshots or unavailable legacy definitions defer the claim. They never
fall back to a possibly shorter global timeout. The watchdog remains scheduled
when the current global default is zero so that older finite frozen budgets
still work.

## Scanning and arbitration

Candidate lists are discovery, not evidence of timeout policy. Each candidate
is reloaded with a full run detail/state snapshot. State can be in `state_json`,
TaskFlow, or historical `params_json._clawmind_state`. The attempt must agree
with the row's `retry_epoch`; missing historical epochs mean **epoch 0 only**.
TaskGuard accepts Mind's original `recoveryEpoch` as an execution-epoch alias.
If both aliases are present, both must be safe nonnegative integers and equal;
present-but-invalid values never fall back to the sibling or row. The local
recovery-clock `epoch` is not execution authority. Row epochs only check the
original state, never fill in a missing state generation. TaskGuard-generated
retry checkpoints retain canonical `retryEpoch` and advance an existing imported
alias together, only after admission succeeds.

Epoch compatibility is not clock compatibility. Mind's online-time recovery
journal is not available to this wall-clock scanner. A `recoveryPolicy` or
`recoveryEpoch` marker in **any** canonical, embedded or TaskFlow snapshot
isolates that run from TaskGuard watchdog claims and restart execution. Unknown
policy values also defer; a lagging mirror cannot hide another snapshot's clock
evidence. These runs require their Mind online-clock owner for recovery/timeout.
This prevents old shared `started_at` values from immediately timing out a fresh
retry, or counting waiting/offline time as execution. Unmarked historical
TaskGuard states retain their existing wall-clock policy.

In-process recovery/watchdog scans share a non-overlapping lane, released even
when a scan fails. Bounded watchdog pages rotate with a per-repository cursor;
disabled, long-budget or corrupt candidates cannot permanently monopolize the
first page. Removals may defer a shifted candidate until the next rotation.

Timeout admission is one atomic database update conditioned on **status exactly
`running` and the captured retry epoch**. Waiting, blocked, terminal and newer
attempts cannot be claimed. Only the winner may abort local execution, fail
TaskFlow using its captured revision, reconcile stale nodes, or announce the
terminal outcome. Its claim already contains the final timeout result, so it
must not issue a second completion write.

## Completion and retry contracts

Ordinary completion is awaited. Reconciliation, HTTP completion callbacks and
completion chat messages follow an acknowledged conditional write, not a
caught/rejected promise. A rejected boolean or HTTP 404/409 is permanent for
that invocation. Transient failures have two additional attempts with bounded
exponential backoff; no unbounded retry is introduced. Transport-level retries
remain independently bounded by the configured API client.

- Completion and timeout writes send `current_phase` and `retry_epoch`.
- `/phase` also retains its historical `phase` alias for older public servers.
- Public TaskGuard node reconciliation keeps the positional contract
  `reconcileStaleRunning(flowId, flowStatus, customErrorText?, retryEpoch?)`.
  The HTTP body uses `custom_error_text` and `retry_epoch`.
- Node reconciliation atomically requires a matching terminal parent status and
  captured parent epoch. Running parents and new attempts are never reconciled.
- Omitted epochs authorize only epoch 0, never whichever epoch exists at cleanup.
- Manual retry calls `/api/internal/runs/:flowId/retry-resume` with captured
  epoch **+1** before execution. It cannot reopen a settled run through a legacy
  `status=running` mirror, even on epoch 0. Replaying the same admitted retry is
  idempotent only while that generation remains running. Cancellation cannot be
  reopened.
- Retry admission atomically checkpoints the caller's captured next-attempt state
  together with `retry_epoch`. Public `resumeForRetry(flowId, retryEpoch,
  stateJson?)` / retry-resume sends optional `state_json`; servers must save it in
  the same admission transaction. SQL also synchronizes historical embedded state.
  Without supplied state, a valid stored historical checkpoint advances in that
  transaction. Replays never overwrite checkpoints from an already-running
  admitted generation. No unfenced `/state` request substitutes for this write.
- TaskGuard manual retry freezes `retryStartedAtMs` in the next-attempt state
  sent to that same atomic admission. The shared `started_at` remains immutable.
  Both watchdog and startup recovery measure the retry window from the captured
  state anchor, so a fresh retry does not inherit the previous attempt's used
  time. Rejected admission leaves the local epoch/anchor unchanged. Present
  invalid/future/conflicting anchors defer; only historical states with no
  anchor retain original-start accounting. No `/started-at` or `/result-json`
  write is used for retry; deprecated-route rejection is not swallowed.
- Waiting/blocked queue transitions use nonterminal status/phase mirrors, never
  completion timestamps, so an admitted same-epoch resume remains possible.

SQLite installations apply schema migration 36 (`retry_epoch`, `state_json`).
API installations require the matching public-server epoch-admission contract
including atomic optional retry `state_json` persistence;
there is deliberately no compatibility downgrade that reopens a terminal run
or drops the attempt fence.

## Detached driver failure supervision

The final background driver promise (including asynchronous rejection handlers
and `finally`/redrive failures) is observed without replacing the rejected
promise returned on the handle. The diagnostic observer has no settlement,
reconciliation, publication or slot-release authority, and reporter failures
are observed too. Crash handling requires native revision CAS and acknowledged
shared completion before any terminal notification or slot release. A rejected
closeout remains rejected; it is not retried as a different terminal intent.

Driver ownership is invocation-local and flow-ID scoped, including its captured
execution epoch. Synchronous children never inherit the parent's epoch, ownership
checks or closeout-admitted flag through async context.
A replacement revokes old closeout/cleanup authority; checks after awaited
admission prevent stale reconciliation or notification. Terminal callers check
the original invocation again after awaited closeout returns and before slot
release or final business messages; a replacement in that Promise-continuation
window rejects the old handle without borrowing authority from the new epoch.
Old cleanup cannot
delete replacement bindings, release its flow slot, or re-drive a newer epoch.
Already-admitted completion also prevents a later publication/cleanup failure
from entering a second crash completion. Revision-conflict re-drive remains
bounded and only within the original execution epoch.

This is promise supervision, not a durable pending-completion journal. Rejected
writes remain inspectable failures requiring the valid owner/manual recovery;
there is no automatic cross-restart closeout replay or exactly-once outbox.

## Safe rollout order

Pause or isolate every old writer/watchdog that can incorrectly apply the
120-minute global budget before changing server admission. Then deploy the
server/schema contract, upgrade and verify all writers/Bots in the shared
instance group, and only then resume them. Removing the server's old timeout
re-arbitration is not independently safe while an incorrect old writer is still
active; epoch fencing does not correct a wrong timeout decision within the same
attempt. Rollback must preserve the same writer isolation.

## Verification and limits

The local suites include real SQLite CAS/multi-connection tests, localhost HTTP
wire/rejection tests, actual controller completion/startup/recovery tests and
policy tests for 8200 vs 120, zero, corrupt fields and historical snapshots.
Run `npm test` and `node_modules/.bin/tsc --noEmit` from the TaskGuard directory.

Claims are distributed database arbitration; TaskFlow's local revision store is
not a distributed execution lease. This change does not add distributed
recovery ownership or an exactly-once notification outbox. A process crash after
a durable claim can still leave best-effort notification delivery incomplete.
Synchronous restart recovery serializes the scan lane until that recovery pass
finishes; it does not introduce an independent watchdog worker. Existing
wall-clock accounting is retained for TaskGuard states (using the captured
retry anchor when present), not replaced by online-time journaling; marked Mind
runs are isolated as above. Cross-repository/public-server integration and rollout
are separate from these local TaskGuard tests.
