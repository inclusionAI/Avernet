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

Two independent guarantees, answering two different questions.

### Claim time — "who runs it?"

Claiming is a row-level compare-and-swap UPDATE whose predicate only matches an
unclaimed (or lease-expired) row. Across N racing workers each task is won by
exactly one. A crashed worker's task is reclaimed after its lease expires. No
`SELECT … FOR UPDATE`. See `plugins/task_queue_repository.py`.

### Enqueue time — "should this row exist at all?"

**Opt-in.** Pass an `idempotency_key` to `enqueue(...)` and at most one **live**
task will exist for that key within its `(env, task_type)`. A duplicate enqueue
inserts nothing and returns the existing task:

```python
record, created = task_queue_service.enqueue(
    PROGRESS_POLL_TASK,
    build_poll_payload(publish_id=publish_id),
    deadline_seconds=_POLL_TASK_DEADLINE_SECONDS,
    idempotency_key=f"publish:{publish_id}:poll",
)
if not created:
    ...  # joined a poll that was already in flight
```

Pass no key (the default) and nothing changes: every enqueue creates a distinct
row, which is what recurring polls, timers, and genuine fan-out want.

**Dedup is active-only, not all-time.** Reaching a terminal state (`SUCCEEDED` /
`FAILED` / `TIMED_OUT`) *releases* the key, so the same key can legitimately be
enqueued again afterwards. That is deliberate — several call sites depend on it:
a publish poll runs once per stage, a retry re-runs a failed stage, a bot
restarts more than once, and skills-pool reconcile is level-triggered. An
all-time-unique key would silently swallow all of those. Scope a key to a
generation (`publish:123:online:g2`) only when you want the opposite.

Key convention:

```
<entity>:<entity_id>[:<qualifier>][:<generation>]

publish:1234:online_release
skills_pool:prod:e-9:bot-7
session_resource:r-42:v3
```

**Mechanism.** A second column, `active_idempotency_key`, mirrors the key while
the task is live and is nulled by every terminal transition; the unique index is
over `(env, task_type, active_idempotency_key)`. MySQL/OceanBase have no partial
indexes, so nulling a plain column is the portable way to say "unique among live
rows only". The opt-out works because **both engines treat NULLs as distinct in
a unique index** — that is a *relied-upon* property, not an incidental one, and
it is covered by a test.

**One edge worth knowing.** A task whose deadline has passed but which no worker
has scanned yet is still non-terminal, so it still holds its key and a duplicate
enqueue joins it. The next claim scan retires it `TIMED_OUT` and frees the key.
This only bites when the worker is down or behind by longer than the task's own
deadline.

Not covered: pulling an already-queued task forward when a duplicate arrives
with a sooner `run_at` (debounce). Out of scope for now — a call site that needs
it should stay un-keyed.

## Give-up

Every task carries a `deadline_at` (required at enqueue). Past it, the task is
retired `TIMED_OUT` — distinct from `FAILED` (a real failure / explicit `Fail`)
— enforced DB-side at claim and on reschedule. There is **no** max-attempts
cap: a raising handler keeps retrying (capped exponential backoff) until the
deadline.

## Status

The BaaS and Teclaw lifecycle components register production handlers during
`bootstrap()` in every deployment profile. The worker processes them only when
`task_queue_worker.enabled=true`. The production `ac_task_queue` table must be
provisioned before enabling the worker; local and test SQLite schema bootstrap
creates it from the shared ORM metadata.

Enqueue idempotency is available but **not yet adopted by any call site** — the
mechanism landed first so adoption can be reviewed per call site. Its DDL is
`sql/2026_08_04_task_queue_idempotency.sql`, which must be applied to prod
before deploying code that passes a key.

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
