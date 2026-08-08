# Task Queue Idempotency: an opt-in, active-only enqueue dedup key

GitHub issue: [#569](https://github.com/inclusionAI/Avernet/issues/569)

## Summary

`ac_task_queue` enforces single-claimer semantics at *claim* time and nothing at
*insert* time, so N enqueues of the same logical unit of work produce N rows and
the work runs N times. This adds an **opt-in, caller-supplied idempotency key**
that dedupes *submissions*, scoped to the rows that are still live.

The decisive design finding — established by auditing every enqueue call site in
`main`, not assumed — is that **all-time uniqueness is not viable**. Four
existing call-site families legitimately re-enqueue the same logical key after
the previous task reached a terminal state. An all-time-unique key would
silently swallow those re-enqueues and break publish polling, publish retry, bot
restart, and skills-pool reconcile. The scope is therefore **active-only**:
at most one *live* task per key, with terminal rows releasing the key.

Opt-in means a `NULL` key is never deduped, so every call site that does not
adopt a key keeps today's behavior exactly.

## Motivation

Duplicate enqueue windows are real in `main` today: a retried API call, a
redelivered approval callback, a re-run stage, or a reconcile signal fanning in
from several sources each insert a fresh row. Even when the handler's *effect*
is idempotent (issue #197's operation ledger), the duplicate row is not free —
it consumes a claim slot, holds a lease, carries its own `deadline_at`, and
multiplies poll traffic against BaaS.

Dedup belongs where the insert happens. `core/session_resources/service.py:395-434`
already hand-rolls this exact feature — `cas_start_materialization` gates the
enqueue and a failed enqueue is compensated by `cas_finish_materialization` —
because the queue cannot do it. Without a queue-level key, every adopter builds
its own gate and no two build it the same way.

This is complementary to #197, not a substitute: the ledger makes *handler
effects* crash-safe; this key makes *submissions* deduped.

## Decision record

The issue asked seven questions. Each has a recorded decision below, with the
evidence that drove it.

### Q1 — Should `ac_task_queue` own insert-time idempotency?

**Yes**, as an opt-in nullable key. The objections raised in the issue's
"Against" list are addressed in [Index cost](#index-cost-under-oceanbase) and
[Uniqueness scope](#a--uniqueness-scope-active-only) rather than waved away.

The rejected alternative — leave the queue as-is and require every adopter to
gate its own enqueue behind a domain CAS — is rejected on the evidence of
`session_resources`: it is the only adopter that built the gate, it took ~40
lines plus a compensation path, and none of the other twelve call sites
replicated it.

### (a) — Uniqueness scope: **active-only**

The issue leaned all-time-unique. **The audit says it does not hold.** Every
enqueue call site in `main` was checked against the question "can this same
logical key legitimately be re-enqueued after the previous task went terminal?"

Four families answer yes:

| Call site | `task_type` | Natural key | Why all-time-unique breaks it |
| --- | --- | --- | --- |
| `publish_flow/tasks.py:301` and `:348`, `retry_ops_mixin.py:213`, `rollback_ops_mixin.py:174` | `PROGRESS_POLL_TASK` | `publish_id` | The poll is enqueued **twice per publish, sequentially**: once by the verify-flow handler on reaching `VALIDATE_PUB`, then again by the online-release handler on reaching `ONLINE_PUB`. The first poll is `SUCCEEDED` by the time the second is enqueued, so an all-time key is already burned → **the online publish is never polled and hangs at `ONLINE_PUB` until its deadline**. |
| `retry_ops_mixin.py:242` (`_retry_via_online_release`), `:260` (`_retry_via_verify_flow`) | `ONLINE_RELEASE_TASK`, `VERIFY_FLOW_TASK` | `publish_id` | Retry re-enqueues **the same stage for the same publish** after the previous attempt failed terminally. That is the whole point of retry. An all-time key makes the publish retry path a no-op. |
| `restart_mixin.py:81` (`restart_bot`) | `RESTART_TASK` | `publish_id:stage` | A user can restart the same published bot repeatedly; `publish_id` and `stage` are unchanged across restarts. An all-time key allows **exactly one restart per publish record, forever**. |
| `reconcile_task.py:239` / `:532`, `operator_commands.py:65`, `recovery_service.py:148` | `SKILLS_POOL_RECONCILE_TASK` | scope `(env, entity_id, bot_id)` | Reconcile is level-triggered and fans in from four sources. An all-time key on the scope **burns that bot's scope permanently after its first reconcile** — no bot ever reconciles twice. |

Each of these is exactly the "burned key" cost the issue named as the real
design cost of all-time uniqueness. Per the direction given, the fallback
applies: **active-only unique**.

Active-only is also the *natural* semantics for these sites, not merely a
tolerable one — "at most one live poll per publish", "at most one live reconcile
per bot scope", "at most one live restart per publish" are the invariants the
call sites actually want, and it collapses the reconcile fan-in for free.

For completeness, the call sites where all-time uniqueness *would* have held —
all because their natural key already carries a generation or a
freshly-minted id, so re-runs never reuse a key:

| Call site | `task_type` | Natural key | Why it is already generation-scoped |
| --- | --- | --- | --- |
| `session_resources/service.py:414` | `session_resource.materialize` | `resource_id:task_version` | `task_version` is bumped by `cas_start_materialization` per attempt. |
| `teclaw_provision_service.py:243` | `TECLAW_CREATE_PUBLISH_POLL_TASK` | `binding_id:publish_id` | `publish_id` is minted by BaaS per provision. |
| `baas_publish_lifecycle.py:37` | `BAAS_CREATE_PUBLISH_POLL_TASK` | `binding_id:publish_id` | Same. |
| `bot_service.py:4198` | `BAAS_RESTART_PUBLISH_POLL_TASK` | `binding_id:publish_id` | A fresh `publish_id` per restart. |
| `baas_publish_task_handlers.py:232` | `BAAS_CREATE_INIT_TASK` | `binding_id:publish_id` | Chained once per publish. |
| `reconcile_task.py:239` | `SKILLS_POOL_QUARANTINE_CLEANUP_TASK` | `scope:migration_generation` | Generation-scoped by construction. |
| `publish_draft_restore_mixin.py:309` | `DRAFT_RESTORE_TASK` | `draft_publish_id:operation_id` | `operation_id` is per ledger op. |
| `publish_approval_service.py:578` | `APPROVAL_TRIGGER_TASK` | `publish_id:action` | One trigger per approval decision. |

Active-only is a strict superset of the guarantee these sites need, so choosing
it costs them nothing.

**Windowed / TTL dedup** is rejected: stateful, hard to reason about, and no
call site asked for it.

### (b) — Conflict behavior at enqueue: return the existing record plus `created`

`enqueue` returns a `(record, created)` pair and **never raises for a plain
duplicate**. Callers that need to branch — "did I actually schedule work, or
join an in-flight task?" — read `created`.

The issue worried this return-shape change would ripple through every adopter.
It does not: **zero call sites consume `enqueue`'s return value today**
(verified across all thirteen). The ripple is empty, so the shape can change
freely.

Concretely the return type is a `NamedTuple`, so it destructures as the plain
`(record, created)` pair that was chosen while still carrying named fields and
leaving room to add fields later without breaking callers again.

The hazard the issue flagged — handing back a *terminal* existing record to a
caller that then waits forever on work that will never run — **cannot occur
under active-only**, because a terminal row has already released its key and the
enqueue creates a fresh row. This is a second, independent argument for
active-only over all-time.

**Sub-question — should a duplicate enqueue carrying a sooner `run_at` pull the
existing task forward (debounce)?** **Out of scope for v1**, explicitly. It is
recorded here rather than silently omitted because one call site
(`EVAL_TEARDOWN_TASK`, see (f)) genuinely needs it and therefore stays un-keyed
until it exists.

### (c) — Key ownership: caller-supplied, optional

Derivation from a payload hash is rejected outright: `teclaw_provision_service.py:250`,
`baas_publish_lifecycle.py:44`, and `bot_service.py:4205` all put
`started_at_epoch_s=time.time()` in the payload, so a payload hash is different
on every call by construction.

Convention, to be written into `core/task_queue/README.md` rather than left to
each adopter:

```
<entity>:<entity_id>[:<qualifier>][:<generation>]
```

for example `publish:1234:online_release`, `skills_pool:prod:e-9:bot-7`,
`session_resource:r-42:v3`.

### (d) — Schema and index shape

Two columns, because active-only cannot be expressed as a plain unique index
without one:

```sql
ALTER TABLE ac_task_queue
  ADD COLUMN idempotency_key VARCHAR(190) NULL
    COMMENT 'caller-supplied submission dedup key; NULL = opted out (never deduped)',
  ADD COLUMN active_idempotency_key VARCHAR(190) NULL
    COMMENT 'enforcement copy; NULLed on every terminal transition to release the key',
  ADD UNIQUE INDEX uk_env_task_type_active_idem (env, task_type, active_idempotency_key);
```

- `idempotency_key` is the **durable audit** value. It is written once at
  enqueue and never cleared, so "which task handled key X?" stays answerable
  after the task finishes.
- `active_idempotency_key` is the **enforcement** value. It is set equal to
  `idempotency_key` at enqueue and set to `NULL` on every terminal transition.

The separate enforcement column is required because MySQL and OceanBase have no
partial/filtered indexes — there is no `UNIQUE ... WHERE status NOT IN (...)`.
Nulling a plain column is the portable way to express "this key is only unique
among live rows", and it keeps the implementation unified across SQLite and
OceanBase as the repository deliberately is.

**Invariant:** `active_idempotency_key IS NOT NULL` ⟺ `idempotency_key IS NOT
NULL` **AND** `status` is non-terminal.

**Rejected alternative — a single high-cardinality token column** (`UNIQUE
(active_dedup_token)` where the token is `env + task_type + key` concatenated).
It would avoid the low-cardinality index prefix, but `task_type` is
`VARCHAR(100)` and `env` is `VARCHAR(20)`, so a 190-char budget would leave
under 70 characters for the caller's actual key. Not worth it.

#### Index cost under OceanBase

The honest accounting, since the issue asked for this to be reasoned about
rather than assumed away:

- The index adds **one secondary-index write per insert** and **one per terminal
  transition**. Enqueue volume is low (single-digit per second at most across
  all task types), so this is not a throughput concern at current rates.
- Dedup lookups are point lookups on a unique index — the cheapest possible
  shape.
- `NULL`s *are* stored in the index on InnoDB/OceanBase, so index size tracks
  total rows, not live rows. The index is not "small because most rows are
  terminal".
- The real risk is the **low-cardinality `(env, task_type)` prefix**: all rows of
  one type in one env share it, so inserts cluster on the same index leaves.
  This is a leaf-page contention concern, not a lock-contention one — multiple
  `NULL`s never conflict with each other, so no insert ever waits on another's
  key. The existing hot claim-scan index `idx_env_status_run_at` already has the
  same prefix shape, so this is not a new class of risk on this table.

**Pre-flight check before applying the DDL** (this is a real constraint, not a
formality): the composite index key is `20 + 100 + 190 = 310` characters, which
under `utf8mb4` is **1240 bytes**. That fits InnoDB's 3072-byte limit under
`DYNAMIC`/`COMPRESSED` row format (the default since MySQL 5.7) but **exceeds
the 767-byte limit under `REDUNDANT`/`COMPACT`**. Before shipping, confirm the
prod `ac_task_queue` row format and OceanBase's index key-length limit. If the
limit is 767 bytes, the fallback is to shorten `task_type` in the index
(`uk_env_task_type_active_idem (env, task_type(40), active_idempotency_key(100))`)
— a prefix index still enforces uniqueness correctly here only if the prefixes
are non-truncating, so prefer raising the row format over shortening.

#### Rollout ordering

Per `repository/models.py:1-9`, the prod OceanBase table is created manually and
must mirror the ORM definition. So:

1. Apply the DDL above to prod, checked in as
   `core/task_queue/sql/2026_08_04_task_queue_idempotency.sql`, following the
   per-module `sql/` convention already used by `core/session_resources/sql/`
   and `core/service_bot/sql/`.
2. Only then deploy **the release containing this change** — not merely the
   later change that starts passing a key. The ORM maps both columns
   unconditionally, so every `SELECT` projects them and every `INSERT` writes
   them even for an un-keyed enqueue; against a table without the columns the
   whole queue fails with "unknown column".

Both columns pin `utf8mb4_bin`. Keys are compared byte-for-byte, and the usual
`utf8mb4_*_ci` default would make `publish:Bot-A:poll` and `publish:bot-a:poll`
the same key in the unique index (non-`_0900` ci collations also PAD SPACE, so
`k1` == `k1 `), silently joining one caller's enqueue to another's task. SQLite
compares BINARY already, so no behavioural test can catch a regression here —
the rendered MySQL DDL is asserted directly instead.

Local and test SQLite get the columns free from `create_all`.

### (e) — Insert path: try-insert → catch `IntegrityError` → re-`SELECT`

Chosen over `INSERT ... ON DUPLICATE KEY UPDATE`, which is MySQL/OceanBase-only
and would fork the deliberately unified implementation.

Two correctness requirements, both of which need explicit test coverage:

- **Scope the `IntegrityError` to this constraint.** A blanket `except
  IntegrityError` would silently convert an unrelated constraint violation into
  a bogus "duplicate" and return someone else's row. The handler must confirm
  the violated constraint is `uk_env_task_type_active_idem` and re-raise
  otherwise.
- **Leave the transaction usable.** A failed `INSERT` poisons the enclosing
  `orm_session()` transaction; the re-`SELECT` must run on a clean transaction
  (rollback to a savepoint, or perform the lookup in a fresh session).

There is an inherent race: the conflicting row can go terminal between the
failed insert and the re-`SELECT`, leaving nothing to return. The insert is
therefore retried once, and only a second consecutive failure surfaces as an
error. This is bounded and must be covered by a test.

### (f) — Adopter migration

Adoption is **per call site and independently reviewable**. No call site is
forced to take a key; a call site that takes none behaves exactly as today.

Recommended first adopters, chosen because they have a real duplicate window and
an unambiguous natural key:

| Call site | Key |
| --- | --- |
| `PROGRESS_POLL_TASK` | `publish:<publish_id>:poll` |
| `SKILLS_POOL_RECONCILE_TASK` | `skills_pool:<env>:<entity_id>:<bot_id>` |
| `VERIFY_FLOW_TASK` / `ONLINE_RELEASE_TASK` / `RESTART_TASK` / `DESTROY_TASK` | `publish:<publish_id>:<stage_or_task>` |
| `BAAS_CREATE_PUBLISH_POLL_TASK` / `TECLAW_CREATE_PUBLISH_POLL_TASK` / `BAAS_RESTART_PUBLISH_POLL_TASK` | `binding:<binding_id>:publish:<publish_id>` |

**Deliberately left un-keyed in v1:**

- **`EVAL_TEARDOWN_TASK`** — `eval_publish_mixin.py:126` enqueues a TTL safety
  net with `delay_seconds=TTL`, and `:172` enqueues an explicit early teardown
  with `delay_seconds=0`. These are two *different intents* sharing one natural
  key. Under **either** uniqueness scope the early teardown would be swallowed
  by the still-pending safety net, delaying teardown to the full TTL — a
  behavior regression. This is precisely the debounce/pull-forward case deferred
  in (b). It stays un-keyed until pull-forward exists. (Secondary reason:
  `eval_teardown()` defaults `publish_id=0`, so `publish_id` is not a usable key
  component there anyway; the key would have to be `bot_uuid`.)
- **Recurring polls and timers** that legitimately want distinct rows.

**`session_resources` keeps its hand-rolled gate.** It guards its own state
machine (`cas_start_materialization` / `cas_finish_materialization` and the
`task_version` it mints), which is a separate concern from submission dedup.
Its key is already generation-scoped, so it can adopt a key later for
defense-in-depth without removing the gate.

### (g) — Contract docs and tests

Both places that currently promise the opposite change **in the same commit**:

- `core/task_queue/README.md` — the "How idempotency works" section, which
  today describes claim-time only.
- `core/task_queue/repository/protocol.py:49-50` — the "Duplicate enqueues
  create distinct rows" docstring.

Test coverage in `tests/community/plugins/test_task_queue_repository.py`:

1. Duplicate keyed enqueue returns the existing record with `created=False` and
   inserts no second row.
2. **Multiple `NULL` keys coexist** — the relied-upon engine property that makes
   opt-in work. Asserted explicitly, not assumed.
3. The same key under a different `task_type` does not collide.
4. The same key under a different `env` does not collide.
5. **The key is released on each of the four terminal transitions** —
   `complete`, `fail`, `reschedule`-to-`TIMED_OUT`, and the claim-path
   `TIMED_OUT` — and re-enqueue after each succeeds with `created=True`.
6. `reschedule` back to `PENDING` **retains** the key (the task is still live).
7. An unrelated `IntegrityError` propagates rather than being read as a
   duplicate.
8. The `orm_session()` transaction is usable after the caught `IntegrityError`.

## Answers to the two rollout worries

**Backfill — there is none, and none is needed.** `idempotency_key` and
`active_idempotency_key` are *new nullable* columns, so every pre-existing row
takes `NULL`. Both MySQL/OceanBase and SQLite treat `NULL`s as distinct in a
unique index, so no two existing rows can collide no matter how many duplicates
the table already contains. The unique index can therefore be created in the
same DDL statement as the columns — there is no duplicate-scrub step, and no
window in which the index could fail to build because of existing data.

**Adopters that carry no key are the same case.** A call site that passes no
key inserts `NULL` into `active_idempotency_key`, which never collides with
anything — including other `NULL`s. That is exactly the mechanism that makes the
feature opt-in, and it is why "we have a unique index on that (+ other related
columns)" does not constrain un-adopted call sites at all. Because the whole
design leans on it, multiple-`NULL`s-coexist is called out as a relied-upon
engine property in the docstring and pinned by test (2) above rather than left
as a tacit assumption.

## Out of scope

- Handler-level **effect** idempotency — that is #197's operation ledger.
- Any change to claim-time semantics, leases, or the deadline model.
- Debounce / pull-forward of an already-queued task (see (b) and (f)).

## Acceptance

1. `ac_task_queue` carries `idempotency_key` and `active_idempotency_key` with
   the unique index, provisioned in prod before the writing code ships.
2. `enqueue` accepts an optional `idempotency_key` and returns `(record, created)`.
3. A keyed enqueue whose key is held by a live task returns that task with
   `created=False` and inserts no row.
4. Every terminal transition releases the key; a subsequent enqueue on the same
   key creates a new task.
5. Un-keyed enqueues behave exactly as they do today.
6. `README.md` and `protocol.py` describe insert-time dedup; the eight tests
   above pass on SQLite, with the OceanBase path exercised by the existing
   conformance shape.
