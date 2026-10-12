# Workflow recovery clock service contract

The ClawMind engine consumes this internal service API from `@avernet/workflow`.
The owning source is `server/routes/internal/runs.ts`, exported by the package's
`./server/*` entrypoint. The clock router is mounted inside the existing internal
runs router and inherits the host's signature authentication and access boundary.
The caller's instance identity is a fencing identifier, not a substitute for host
authentication. Deployment must retain the existing internal-router protection.

## HTTP protocol

`PUT /api/internal/runs/:flowId/recovery-clock` accepts:

```json
{
  "mode": "claim",
  "ownerInstanceId": "persistent-instance-id",
  "bootId": "boot-id",
  "epoch": 0,
  "seq": 1,
  "elapsedMs": 1200000,
  "phase": "recovering",
  "expectedBootId": "previous-boot-id"
}
```

`mode` is `claim`, `checkpoint`, or `retry`; `phase` is `recovering`, `active`,
or `retry_pending`. `expectedBootId` is omitted on first claim. `requestId` is
required for `retry`. IDs are nonblank strings up to 190 characters; all counters
are nonnegative safe integers and elapsed time is milliseconds.

Success is `{ "ok": true, "clock": { "ownerInstanceId", "bootId", "epoch", "seq", "elapsedMs", "phase" } }`.
`GET` at the same path returns `{ "clock": object | null }`; an unknown/unclaimed
flow returns null. Writes for an unknown flow return 404. Invalid input returns
400, ownership/version/idempotency conflicts return 409, and storage failures
return 500 (unconfigured storage: 503). Missing schema is an error, never an empty
clock or a successful no-op.

- **Claim:** the run must exist, including a terminal run being manually retried.
  Initial phase is `recovering`. Existing ownership cannot change. A different
  boot requires matching `expectedBootId`, unchanged epoch, strictly higher seq,
  and elapsed time at least the stored value. A pending retry stays `retry_pending`
  across a new boot; other new-boot claims enter `recovering`. Same-tuple identical
  replay is accepted; stale writes and conflicting same-seq payloads are rejected.
- **Checkpoint:** owner, boot and epoch must match; seq advances and elapsed time
  never decreases. The phase can stay unchanged or become `active`. An identical
  same-seq replay succeeds. A checkpoint cannot create a retry window.
- **Retry:** owner and boot match; epoch is exactly the old epoch plus one,
  elapsedMs is zero, phase is `retry_pending`, and requestId is required. Seq starts
  at the caller's nonnegative value. Request IDs are durable and scoped to flow.
  Replaying the same request returns the current clock in that epoch, including
  after activation; changing its payload or replaying after another epoch is 409.
  A second retry cannot open while one is pending. Clock preparation leaves the
  run's status unchanged. The first checkpoint to active atomically restores
  status `running` and clears `completed_at`, preserving `started_at`. A cancellation
  that changes status after preparation prevents activation; reconciliation is
  required instead of silently undoing it.

The engine must reconcile a returned clock before executing. Automatic recovery
must check terminal run state and never activate a finished run. Claim permission
for terminal rows exists to support an explicit manual retry, not automatic rerun.

## Timeout and compatibility

`PUT /api/internal/runs/:flowId/fail-if-running` keeps its existing body fields
`reason`, `current_phase`, `total_duration_ms`, `completed_at`, adding optional:

```json
{"expectedClock":{"ownerInstanceId":"instance","bootId":"boot","epoch":0,"seq":12}}
```

The response remains `{ "success": true, "data": { "claimed": boolean } }`.
When a clock exists, an absent/mismatched version or a phase other than active
returns claimed false. Malformed expectedClock returns 400. Terminal writes still
require a nonterminal run. Only the winner triggers the terminal observer.
Legacy runs without a clock retain unguarded timeout and started-at reset behavior.
`PUT /:flowId/started-at` returns 409 for clock-enabled runs and cannot modify them.
The legacy `retry-failed` route also rejects clock-enabled runs with 409.
Legacy status/completion writes are rejected during retry preparation and cannot
resurrect or overwrite a terminal clock-enabled run. New engines must use the
retry clock protocol instead of legacy status or started-at reset for retry.

All clock writes, resets, status/completion and timeout operations lock the existing
flow_runs row inside one database transaction before reading the clock. MySQL/ZDAS
use that row lock across connections. SQLite serializes the connection's complete
async transactions and independent statements, using BEGIN IMMEDIATE; competing
external connections may receive a busy error and must retry. No local JavaScript
mutex is used as a substitute for the database row lock.

## Migration and rollout

Shared schema migration **140** adds `flow_recovery_clocks` and
`flow_recovery_retries`. It neither updates historical runs nor backfills guessed
elapsed time. SQLite auto-migration uses the existing database bootstrap. For
managed MySQL/ZDAS environments with DDL disabled, apply
[140-recovery-clock.mysql.sql](migrations/140-recovery-clock.mysql.sql) through the
normal DBA process before deploying the new API. The two unique keys prevent
multiple clocks per flow and duplicate retry requests. Retry key hashing keeps
the unique index within ZDAS's bounded index size; original request data is kept
for exact-payload validation. Both tables must use transactional storage.

Deploy schema, then compatible service API, then upgraded engines. Retire every
old watchdog that can touch the same bot's runs before enabling recovery clocks.
Other direct database writers and watchdog implementations in separate engine
packages must be audited at their deployment owner. The Workflow module itself
has no additional started-at timeout reaper; reporting queries still use the
original timestamp. Do not delete clocks, retry receipts, or the local engine
journal during rollback. Pause new execution and timeout reaping, then reconcile.

Local tests cover SQLite storage/transaction concurrency, clock and HTTP contracts,
retry/timeout races, terminal protection, migration, and MySQL/ZDAS DDL rendering.
Live MySQL/ZDAS transactions, deployed host signature enforcement, container restart,
forced process exit and persistent-volume reconstruction remain deployment checks.

## Cross-repository runtime acceptance test

An opt-in integration suite loads the actual ClawMind `WorkflowRecoveryRuntime`,
`RecoveryStore`, and signed `ApiClient` from a caller-supplied checkout. Run from
the `apps/evolverun/clawweb` workspace with Node 22 or newer:

```sh
CLAWMIND_ROOT=/path/to/ClawMind node_modules/.bin/vitest run \
  --root public/modules/workflow --config vitest.recovery-integration.config.ts
```

The nine scenarios cover API response parsing, normal checkpointing, preserved
elapsed time across downtime, repeated same-boot reconciliation, retry requests
lost before acceptance or after acceptance, fractional monotonic time, and actual
SIGKILL of a child engine process both before and after local retry activation.
Both SIGKILL tests reopen the same durable journal, reconcile through real HTTP,
and assert the retry stays in one epoch. The suite mounts the production router
and repository and uses the production SQLite transaction adapter with Node's
compatible `DatabaseSync` driver subset. This avoids mixing Node ABI versions with
the separately tested better-sqlite3 driver. It does not replace deployed host
authentication, MySQL/ZDAS or full container-persistence acceptance checks.
Without `CLAWMIND_ROOT`, the suite is skipped by ordinary unit-test runs.
