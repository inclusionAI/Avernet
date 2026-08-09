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

A key must be **non-empty and at most 190 characters** — the stored column
width — and both are enforced in Python, raising `ValueError`. That check is
not belt-and-braces: the engines disagree about overflow and the disagreement
is invisible to the SQLite suite. SQLite ignores `VARCHAR` length, strict
MySQL/OceanBase raises `DataError`, and non-strict **silently truncates** —
which would collapse two distinct keys onto one stored value and hand the
caller somebody else's task with `created=False`. Note that some id columns
are much wider than 190 (`ac_bot_publish.publish_bot_id` is `varchar(1024)`),
so hash the variable part rather than embedding a long id directly. Empty
string is rejected for the mirror-image reason: `None` is the opt-out, so `""`
would otherwise be one global dedup slot per `(env, task_type)`.

Leading and trailing whitespace is rejected too, for a reason the collation
below cannot fix on its own: MySQL/OceanBase compare with a **PAD SPACE**
collation, under which `"k1"` and `"k1 "` are the *same* entry in the unique
index — while SQLite keeps them apart, so the suite would never see it. Rather
than depend on a NO PAD collation being present on every OceanBase version,
the ends are constrained so the collision is unreachable: if no accepted key
carries trailing whitespace, padding can never merge two accepted keys.
Internal spacing is untouched and keys are stored **verbatim** — validation
rejects, it never trims.

**Mechanism.** A second column, `active_idempotency_key`, mirrors the key while
the task is live and is nulled by every terminal transition; the unique index is
over `(env, task_type, active_idempotency_key)`. MySQL/OceanBase have no partial
indexes, so nulling a plain column is the portable way to say "unique among live
rows only". The opt-out works because **both engines treat NULLs as distinct in
a unique index** — that is a *relied-upon* property, not an incidental one, and
it is covered by a test.

Both key columns pin **`utf8mb4_bin`** on MySQL/OceanBase, and that is
load-bearing. Keys are compared byte-for-byte, but the usual `utf8mb4_*_ci`
default is case-insensitive — `publish:Bot-A:poll` and `publish:bot-a:poll`
would be the *same* key in the unique index, letting one caller's enqueue
silently join a different caller's task. SQLite compares BINARY already, so this
divergence is invisible to the suite; the collation is what makes the two
engines agree with the "stored verbatim" contract.

`utf8mb4_bin` closes case folding but **not** space padding — it is itself a
PAD SPACE collation, so the trailing-space half of the problem is closed by the
validation rule above rather than by the collation. The two are complementary,
and neither alone is sufficient.

**`task_type` pins the same collation**, because a unique index is only as
precise as its least precise column. Left on the default, `Job` and `job` would
be one index entry — two registered handlers sharing a single dedup slot, so a
keyed enqueue for one joins the other's live task. Pinning it costs nothing
elsewhere: on this table `task_type` is compared in SQL only by the dedup
lookup and appears in no other index. Note that on an already-provisioned table
this is a change to an *existing* column rather than a new one, so it rewrites
data — expect a table rebuild rather than a metadata-only change.

**`env` deliberately does not.** It is scoped by the same index, but unlike
`task_type` it is compared by the claim/reclaim eligibility filter and carries
`idx_env_status_run_at` and `idx_env_lease_expires_at`, so changing its
collation would alter pre-existing behaviour and rebuild those indexes — far
wider than the risk, given `env` comes from deployment config rather than
per-call input. A test pins this as a decision so it isn't "fixed" by a
consistency edit later.

Padding on `task_type` is rejected at **two** boundaries, because neither covers
the other. `enqueue` rejects it per row whenever a key is supplied — `enqueue`
never consults the registry, and the worker tolerates persisted types with no
handler, so a row can carry a type no registry ever saw. The harm there runs
opposite to intuition: the padded row is the one that *cannot* run, but it shares
a dedup slot with the bare type, so a live `job ` row holding `k1` makes a
legitimate `job` enqueue for `k1` join it with `created=False` — the work
suppressed is the work that would have run.

The same boundary also bounds `task_type` to the stored column width, for the
same reason the key is bounded: a non-strict server truncates an over-long value,
filing the row under the *truncated* scope while the holder lookup searches for
the full string. The duplicate then conflicts with a row it cannot find and
raises, where the contract promises the live holder with `created=False`.

Un-keyed enqueues are deliberately exempt from both rules: their
`active_idempotency_key` is `NULL`, so they never enter the index and cannot
collide however they are stored.

`HandlerRegistry.register` additionally rejects a task type that folds onto an
already-registered one. That is second line of defence, not the enforcement:
the collation settles case *across processes*, while this catches the PAD SPACE
half (`job ` vs `job`, which `utf8mb4_bin` still merges) and fails loudly at
startup instead of at the first keyed enqueue in production. It cannot see
outside its own process — a rolling deploy renaming a type by case alone has
each version holding only its own spelling — which is exactly why the scope is
enforced in the schema as well.

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
mechanism landed first so adoption can be reviewed per call site.

**Adoption must ship in a strictly later release than this mechanism, and that
is a correctness requirement rather than a review preference.** Key release
happens in the terminal transitions: `complete`, `fail`, the reschedule-overshoot
timeout, and the claim-scan deadline retirement each null
`active_idempotency_key` in the same `UPDATE` as the status change. A worker
running code from *before* this change knows nothing about that column, so if it
claims a keyed task it will set a terminal status and leave the key populated.
Nothing ever releases it after that: the next enqueue with that key hits the
unique index forever.

That mixed-version window can only open if keyed rows exist while pre-change
workers are still running — which requires adoption in the *same* release as the
mechanism. Ship them separately and the window never exists, because by the time
any call site can pass a key, every pod already releases it. An adoption PR that
also had to drain the fleet first would be a far worse change than one that
simply comes second.

`_find_active_by_key` additionally excludes terminal rows, so a stale key
produced this way (or by a manual DB edit, or by a future transition that forgets
the release) surfaces as a raised `RuntimeError` from `enqueue` rather than as a
finished task handed back with `created=False`. That is a loud failure on an
inconsistent row, not a repair — see its docstring.

## Provisioning

**`repository/models.py` is the source of truth for the schema.** No DDL is
checked in: the deployed tables are provisioned out of band, so a copy in the
repo would be a second definition to keep in step rather than an authority. Read
the ORM model — column types, nullability, collations, and the index are all
declared there with the reasoning inline.

**The schema change must be applied before deploying the release that contains
it** — not merely before the first call site passes a key. The ORM maps both new
columns unconditionally, so every `SELECT` projects them and every `INSERT`
writes them even for an un-keyed enqueue; against a table without them the whole
queue fails with "unknown column".

That applies to **any** deployment that has provisioned `ac_task_queue`, not just
prod. `CommunityDatabase` is a pure connection provider and never runs
`create_all`, so a community schema is operator-provisioned and does not pick the
columns up automatically. What has to exist:

- `idempotency_key` and `active_idempotency_key` — nullable, matching the width
  in `models.py`, both pinning `utf8mb4_bin` on MySQL/OceanBase.
- `task_type` also pinning `utf8mb4_bin` there — it is the index's other scope
  column, and an index is only as precise as its least precise column.
- `UNIQUE (env, task_type, active_idempotency_key)` — **`GLOBAL` on OceanBase**,
  matching the convention used by every other unique index in the deployment.
  This one cannot be read off `models.py`: SQLAlchemy's `Index` has no way to
  express `GLOBAL`, so the ORM renders a plain unique index and the modifier
  exists only here. Without it the index can be partition-local, which would let
  the same active key exist once per partition and defeat dedup entirely.
- `env` deliberately left on the table default — see the index comment in
  `models.py` for why widening it is a much larger change.

On SQLite none of the collation clauses apply: it compares `TEXT` as `BINARY`
natively, which is already the semantics the key contract needs.

**No backfill.** Both columns are new and nullable, every existing row takes
`NULL`, and all supported engines treat `NULL`s as distinct in a unique index —
so no two existing rows can collide however many duplicate enqueues the table
already holds, and the index can be created alongside the columns.

**PostgreSQL is not a supported store for this component.** `CommunityDatabase`
will connect to it, but `_now_plus` branches on SQLite and treats every other
dialect as MySQL, emitting `date_add(now(), INTERVAL n SECOND)` — which
PostgreSQL does not have. Since all the repository's timing is DB-side, the queue
does not work there at all. Supporting it means teaching `_now_plus` a third
dialect, not writing DDL.

Deployments that never provisioned the table need nothing: the worker is disabled
by default and nothing reads it. The local/test profile needs nothing either,
since its schema is rebuilt from the ORM metadata on every start — note that
`create_all(checkfirst=True)` adds missing *tables*, not missing *columns*, so a
long-lived local SQLite file predating this change needs recreating.

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
