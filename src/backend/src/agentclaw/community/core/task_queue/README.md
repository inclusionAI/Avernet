# task_queue

Generic, durable, DB-backed distributed task queue. Callers persist a unit of
background work (a `task_type` + JSON payload) and an in-process `TaskWorker`
(one per pod) claims and runs it — **exactly one worker at a time**, enforced
at the database level.

## What it is for

Replaces the "spawn a daemon thread and `sleep`" pattern, which loses work on
restart and double-runs across pods. The motivating use case is polling an
external operation (e.g. a publish approval) until it reaches a terminal state:
a handler that reschedules itself until done, bounded by a wall-clock deadline.

## Pieces

- `repository/models.py` — `ac_task_queue` ORM table.
- `repository/protocol.py` — `TaskQueueRepositoryProtocol` (claim CAS + holder-guarded transitions). Impl: `plugins/task_queue_repository.py` (unified, runs on SQLite + OceanBase). **The DB owns all timing** — callers pass durations; the repo computes `run_at`/`lease`/`deadline` and every comparison with the DB clock (`now()`), so pod clock skew can't affect coordination.
- `types.py` — `TaskRecord`, the `TaskStatus` enum (`PENDING`/`RUNNING`/`SUCCEEDED`/`FAILED`/`TIMED_OUT`), and the handler outcomes `Complete` / `Reschedule` / `Retry` / `Fail`.
- `services/registry.py` — `TaskHandler` Protocol + `HandlerRegistry`.
- `services/task_queue_service.py` — `TaskQueueService.enqueue(...)`, the entry point adopters call.
- `services/worker.py` — `TaskWorker`, the Lifecycle that polls, claims, runs handlers, and applies outcomes.
- `examples.py` — `NoopTaskHandler` + `PollUntilTerminalExampleHandler` (not wired in prod).

## How idempotency works

Claiming is a row-level compare-and-swap UPDATE whose predicate only matches an
unclaimed (or lease-expired) row. Across N racing workers each task is won by
exactly one. A crashed worker's task is reclaimed after its lease expires. No
`SELECT … FOR UPDATE`. See `plugins/task_queue_repository.py`.

## Give-up

Every task carries a `deadline_at` (required at enqueue). Past it, the task is
retired `TIMED_OUT` — distinct from `FAILED` (a real failure / explicit `Fail`)
— enforced DB-side at claim and on reschedule. There is **no** max-attempts
cap: a raising handler keeps retrying (capped exponential backoff) until the
deadline.

## Status

Disabled by default (`task_queue_worker.enabled=false`). This iteration ships
the reusable component only — no production handler is registered. The prod
`ac_task_queue` table is created manually (no migrations here); mirror
`repository/models.py`, with `gmt_create`/`gmt_modified` as
`DEFAULT CURRENT_TIMESTAMP [ON UPDATE CURRENT_TIMESTAMP]` so the DB manages
them. Provision the table before enabling.

## Context Boundary

```yaml
purpose: "Generic durable distributed task queue: persist background work and have one in-process worker per pod claim and run it with DB-level single-claimer idempotency."
provides:
  - "TaskQueueService (enqueue)"
  - "TaskWorker (in-process claim/run lifecycle)"
  - "HandlerRegistry + TaskHandler protocol"
  - "TaskQueueRepositoryProtocol"
consumes:
  - "DatabasePlugin (via the repository impl in plugins/)"
  - "TaskQueueWorkerConfig"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.di.config
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.utils.env_utils
```
