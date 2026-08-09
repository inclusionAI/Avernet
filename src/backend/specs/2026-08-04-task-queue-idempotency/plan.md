# Plan: Task Queue Idempotency (opt-in, active-only enqueue dedup key)

Spec: `spec.md` · Issue: [#569](https://github.com/inclusionAI/Avernet/issues/569) · PR: [#789](https://github.com/inclusionAI/Avernet/pull/789)

## Approach

Add two nullable columns to `ac_task_queue`: `idempotency_key` (durable audit,
written once, never cleared) and `active_idempotency_key` (enforcement copy,
`NULL`ed on every terminal transition), with a unique index over
`(env, task_type, active_idempotency_key)`. Because MySQL/OceanBase have no
partial indexes, nulling the enforcement column is the portable way to express
"unique among live rows only". `enqueue` gains an optional `idempotency_key` and
returns an `EnqueueResult(record, created)` NamedTuple; a keyed insert that hits
the constraint re-`SELECT`s the live holder and returns it with `created=False`.

Adopting the key at any call site is a **separate, later change** — this plan
lands the mechanism only, so every existing call site keeps today's behavior via
the `NULL` opt-out.

## Affected Components

- `src/backend/src/agentclaw/community/core/task_queue/repository/models.py` — the ORM table; new columns + unique index.
- `src/backend/src/agentclaw/community/core/task_queue/repository/protocol.py` — the repository contract + the docstring that currently promises the opposite.
- `src/backend/src/agentclaw/community/core/task_queue/types.py` — `TaskRecord` field, new `EnqueueResult`.
- `src/backend/src/agentclaw/community/core/task_queue/services/task_queue_service.py` — the adopter-facing facade; pass-through + return shape.
- `src/backend/src/agentclaw/community/plugins/task_queue_repository.py` — insert path, and the four terminal writes that must release the key.
- `src/backend/src/agentclaw/community/core/task_queue/README.md` — the "How idempotency works" section.
- `src/backend/tests/community/plugins/test_task_queue_repository.py` — the eight cases from spec (g).

## Data Model Changes

Provisioned out of band; `repository/models.py` is the source of truth and no
DDL is checked in. What a deployment must end up with:

| Element | Requirement |
| --- | --- |
| `idempotency_key` | `varchar(190)` nullable; `utf8mb4_bin` on MySQL/OceanBase |
| `active_idempotency_key` | `varchar(190)` nullable; `utf8mb4_bin` on MySQL/OceanBase |
| `task_type` | also `utf8mb4_bin` there — the index's other scope column |
| unique index | `uk_env_task_type_active_idempotency_key (env, task_type, active_idempotency_key)`, `GLOBAL` on OceanBase |
| `env` | deliberately left on the table default |

Apply before deploying the release that writes these columns. Existing rows take
`NULL` in both, and `NULL`s are distinct in a unique index, so no existing row
can collide and no backfill or scrub is required.

> The collations are **not** cosmetic and are the part most easily lost when
> transcribing: `utf8mb4_bin` is what stops `publish:Bot-A:poll` and
> `publish:bot-a:poll` being one entry in the dedup index. It settles case but
> not trailing spaces — that half is closed in Python. Applying it to
> `task_type` on an already-provisioned table changes an *existing* column, so
> it rewrites data rather than being metadata-only.

`GLOBAL` matches the existing OceanBase index convention used elsewhere in the
repo (`core/service_bot/sql/ac_bot_publish.sql`).

**Index key length is not a blocker.** The composite key is
`(20 + 100 + 190) × 4 = 1240` bytes under `utf8mb4`. `ac_bot_publish` already
carries `UNIQUE KEY uk_oi_p_b_v (owner_id, publish_bot_id, version)` at
`(128 + 1024) × 4 + 8 ≈ 4.6 KB`, so the deployment demonstrably tolerates index
keys far past the 767-byte `COMPACT` limit. The spec's pre-flight item is
resolved; no column needs shortening.

```diff
# core/task_queue/repository/models.py — new columns after deadline_at
+    idempotency_key = Column(
+        IdempotencyKeyString,
+        nullable=True,
+        comment="caller-supplied enqueue dedup key; NULL = opted out. Audit only",
+    )
+    active_idempotency_key = Column(
+        IdempotencyKeyString,
+        nullable=True,
+        comment="enforcement copy of idempotency_key; NULLed on terminal transitions",
+    )
#
# Landed as IdempotencyKeyString / TaskTypeString — String(n) carrying a
# utf8mb4_bin variant on MySQL — rather than the plain String(190) planned
# here. The collation turned out to be load-bearing; see spec (d).
```

```diff
# core/task_queue/repository/models.py — __table_args__
     __table_args__ = (
         Index("idx_env_status_run_at", "env", "status", "run_at"),
         Index("idx_env_lease_expires_at", "env", "lease_expires_at"),
+        # Active-only enqueue dedup. NULL active key = opted out; engines treat
+        # NULLs as distinct in a unique index, which is what makes it opt-in.
+        Index(
+            "uk_env_task_type_active_idempotency_key",
+            "env", "task_type", "active_idempotency_key",
+            unique=True,
+        ),
     )
```

## API / Interface Changes

```python
# core/task_queue/types.py (new)
class EnqueueResult(NamedTuple):
    """Outcome of an enqueue. Destructures as ``(record, created)``.

    ``created`` is False only when a keyed enqueue joined a task that was
    already live under the same key.
    """
    record: TaskRecord
    created: bool
```

```diff
# core/task_queue/types.py — TaskRecord, after `env`
     env: str
+    idempotency_key: Optional[str] = None
     gmt_create: Optional[datetime] = None
```

`active_idempotency_key` is deliberately **not** projected onto `TaskRecord` —
it is an enforcement detail, not part of the record's meaning. Tests assert on
it by querying the model directly.

```diff
# core/task_queue/repository/protocol.py — NOT breaking; no call site
# consumes the current return value (verified across all 13).
  def enqueue(
      self,
      *,
      task_type: str,
      payload: dict,
      delay_seconds: int,
      deadline_seconds: int,
      env: str,
+     idempotency_key: Optional[str] = None,
- ) -> TaskRecord:
+ ) -> EnqueueResult:
```

```diff
# core/task_queue/services/task_queue_service.py
  def enqueue(
      self,
      task_type: str,
      payload: dict,
      deadline_seconds: int,
      *,
      delay_seconds: int = 0,
+     idempotency_key: Optional[str] = None,
- ) -> TaskRecord:
+ ) -> EnqueueResult:
```

## Key Files & Functions

### The insert path

```python
# plugins/task_queue_repository.py — replaces the plain-INSERT enqueue
def enqueue(self, *, task_type, payload, delay_seconds, deadline_seconds,
            env, idempotency_key=None) -> EnqueueResult:
    """Un-keyed → plain insert, always created=True (today's behavior).
    Keyed → try-insert; on *this* constraint, re-SELECT the live holder."""
    if idempotency_key is None:
        return EnqueueResult(self._insert(...), True)
    for _ in range(_KEYED_INSERT_ATTEMPTS):
        try:
            return EnqueueResult(self._insert(..., idempotency_key=...), True)
        except IntegrityError as exc:
            if not _is_active_idem_conflict(exc):
                raise                       # unrelated constraint — never swallow
            existing = self._find_active_by_key(env, task_type, idempotency_key)
            if existing is not None:
                return EnqueueResult(existing, False)
            stranded = self._find_stranded_key_holder(env, task_type, key)
            if stranded is not None:
                raise RuntimeError(...)     # permanent: retrying cannot help
            # holder went terminal between INSERT and SELECT → key is free again
    raise RuntimeError(...)                 # sustained churn: surface it
```

The loop implements the spec's bounded retry for the insert/re-`SELECT` race —
the conflicting row can reach a terminal state (releasing the key) in the window
between the failed insert and the lookup, leaving nothing to return.

The two causes of "conflict but no live holder" are separated deliberately.
`_find_stranded_key_holder` finds a *terminal* row still holding the key, which
retrying can never resolve, so it raises at once and names the row. Only genuine
races consume `_KEYED_INSERT_ATTEMPTS`, which is why that bound is 5 rather than
2: each additional loss needs another caller to claim *and* release the key
inside one microsecond-wide window, so exhausting it means sustained churn rather
than a fault. A bound is still kept — unbounded retry could spin forever.

```python
# plugins/task_queue_repository.py (new module-level helper)
def _is_active_idem_conflict(exc: IntegrityError) -> bool:
    """True only for uk_env_task_type_active_idempotency_key.

    Portable across both engines because they name the violation differently:
    MySQL/OceanBase report the *index* ("Duplicate entry … for key
    'uk_env_task_type_active_idempotency_key'"); SQLite reports the *columns* ("UNIQUE
    constraint failed: ac_task_queue.env, ac_task_queue.task_type, …").
    """
```

```python
# plugins/task_queue_repository.py (new private methods)
def _find_active_by_key(self, env: str, task_type: str, key: str) -> Optional[TaskRecord]:
    """The live holder of ``key``, or None. Filters on active_idempotency_key
    and excludes terminal statuses — the column alone should suffice, since
    terminal transitions null it, but a mixed-version writer can leave a
    terminal row still holding the key and it must not be returned as live."""

def _find_stranded_key_holder(self, env: str, task_type: str, key: str) -> Optional[int]:
    """Id of a terminal row still holding ``key``, or None. Always None in a
    consistent database; exists so a permanently-held key raises immediately
    instead of being mistaken for contention."""
```

**Transaction safety is already handled by the contract** — `orm_session()`
rolls back and closes on exception (`plugins/local/database.py:207-217`), and
the corp/OceanBase engine runs at `AUTOCOMMIT` (`plugin_api/database.py:42-44`).
So the `try` must wrap the **whole `with` block**, not sit inside it; the
re-`SELECT` then runs in a fresh, clean session. No savepoint needed.

### The four terminal writes that release the key

Each is already a single `UPDATE`, so adding the clear to its existing `SET`
dict makes "terminal but key still held" unrepresentable — there is no ordering
window to get wrong.

```diff
# plugins/task_queue_repository.py — claim_batch, past-deadline TIMED_OUT
                         self.Model.status: TaskStatus.TIMED_OUT.value,
                         self.Model.last_error: "deadline elapsed before execution",
                         self.Model.claimed_by: None,
                         self.Model.lease_expires_at: None,
+                        self.Model.active_idempotency_key: None,
```

```diff
# plugins/task_queue_repository.py — complete() → SUCCEEDED
                 self.Model.status: TaskStatus.SUCCEEDED.value,
                 self.Model.claimed_by: None,
                 self.Model.lease_expires_at: None,
+                self.Model.active_idempotency_key: None,
```

```diff
# plugins/task_queue_repository.py — reschedule() deadline-overshoot → TIMED_OUT
                         self.Model.status: TaskStatus.TIMED_OUT.value,
                         self.Model.last_error: (error or "deadline elapsed"),
                         self.Model.claimed_by: None,
                         self.Model.lease_expires_at: None,
+                        self.Model.active_idempotency_key: None,
```

```diff
# plugins/task_queue_repository.py — fail() → FAILED
                 self.Model.status: TaskStatus.FAILED.value,
                 self.Model.last_error: error,
                 self.Model.claimed_by: None,
                 self.Model.lease_expires_at: None,
+                self.Model.active_idempotency_key: None,
```

`reschedule()`'s `PENDING` branch  is **deliberately not touched** —
the task is still live, so it keeps holding its key. Same for `claim_batch`'s
`RUNNING` claim  and `renew_lease` .

These four are the complete set: a repo-wide grep for `TaskStatus.SUCCEEDED` /
`FAILED` / `TIMED_OUT` writes finds only lines 200, 232, 278, 293, and
`worker.py` reaches terminal states solely through `complete`/`fail`
(`worker.py:206, 264, 270`).

### Docs that currently promise the opposite

```diff
# core/task_queue/repository/protocol.py
-        JSON-serialized on write. Duplicate enqueues create distinct rows —
-        idempotency is a claim-time guarantee, not an insert-time one.
+        JSON-serialized on write. An un-keyed enqueue always creates a new row.
+        A keyed enqueue is deduped against *live* tasks sharing that key in the
+        same (env, task_type): it returns the existing task with
+        ``created=False`` instead of inserting. Terminal tasks release their
+        key, so the same key may legitimately be re-enqueued afterwards.
```

`README.md`'s "How idempotency works" gains an insert-time paragraph alongside
the existing claim-time one, plus the key convention
`<entity>:<entity_id>[:<qualifier>][:<generation>]` from spec (c).

## Dependencies

None. `IntegrityError` comes from `sqlalchemy.exc`, already a dependency.

## Risks & Mitigations

- **Risk:** A terminal transition is added later without clearing the key, silently reopening duplicates (the issue's stated fear).
  **Mitigation:** All four clears ride inside the same `UPDATE` as the status change, so the invariant cannot be violated by ordering. Test (5) covers all four paths independently, so a fifth terminal write added without a clear fails a test rather than leaking.
- **Risk:** A blanket `except IntegrityError` converts an unrelated constraint violation into a bogus "duplicate" and returns someone else's row.
  **Mitigation:** `_is_active_idem_conflict` matches the specific constraint on both engines; anything else re-raises. Test (7) pins it.
- **Risk:** Low-cardinality `(env, task_type)` index prefix clusters inserts on the same leaves under OceanBase.
  **Mitigation:** Accepted, and reasoned in spec (d) — multiple `NULL`s never conflict, so there is no lock contention, only leaf-page contention, at an enqueue rate in the single digits per second. The existing hot `idx_env_status_run_at` has the same prefix shape.
- **Risk:** The DDL ships after the release containing this change → `Unknown column` in prod, breaking the *entire* queue rather than just keyed callers. The ORM maps both columns unconditionally, so every `SELECT` projects them and every `INSERT` writes them even for an un-keyed enqueue.
  **Mitigation:** Rollout ordering below states "before this release", not "before the first keyed caller"; there is no fallback path, so ordering is mandatory, not best-effort.
- **Risk:** A case-insensitive default collation on MySQL/OceanBase makes `publish:Bot-A:poll` and `publish:bot-a:poll` the same key in the unique index (non-`_0900` ci collations also PAD SPACE, so `k1` == `k1 `), silently joining one caller's enqueue to another's task.
  **Mitigation:** Both key columns **and `task_type`** pin `utf8mb4_bin` via `with_variant` in the ORM, and the provisioned table must match — an index is only as precise as its least precise column. SQLite is BINARY natively, so no behavioural test can catch a regression — a test asserts the rendered MySQL `CREATE TABLE` carries the collation.

## Alternatives Considered

- **All-time uniqueness** (the issue's original lean) — rejected on the call-site audit in spec (a): it breaks progress-poll, publish retry, bot restart, and skills-pool reconcile.
- **A single high-cardinality token column** (`UNIQUE (env + task_type + key)` concatenated) — avoids the low-cardinality prefix, but `task_type` is `VARCHAR(100)` and `env` `VARCHAR(20)`, leaving under 70 chars for the caller's key inside the 190-char budget.
- **`INSERT ... ON DUPLICATE KEY UPDATE`** — MySQL/OceanBase only; would fork the deliberately unified SQLite+OceanBase repository body.
- **Partial/filtered unique index** (`UNIQUE ... WHERE status NOT IN (…)`) — SQLite supports it, MySQL/OceanBase do not. This is precisely why the second enforcement column exists.
- **Reusing `status` in the unique index** (`UNIQUE (env, task_type, key, status)`) — would permit one row per key *per terminal status*, which is not the invariant; and it still burns the key against a second `PENDING`.

## Rollout

```bash
# 1. Schema change to prod FIRST — the code below writes columns that must
#    already exist. Applied out of band; see models.py for the definition.

# 2. Then deploy Backend. Local/test SQLite gets the columns from create_all().
```

No feature flag. The mechanism is inert until a call site passes a key, and this
plan changes **zero** call sites — every one continues to pass no key and take
the `NULL` opt-out. Backwards-compatible by construction: the only signature
change is an added keyword-only argument with a default, plus a return type no
caller reads.

Rollback is `DROP INDEX` + `DROP COLUMN`; no data migration to unwind.

## Test Strategy

All in `tests/community/plugins/test_task_queue_repository.py` against real
in-memory SQLite (the existing `repo` fixture), matching spec (g). The
`_enqueue` helper  gains an `idempotency_key=None` pass-through.

```python
# tests/community/plugins/test_task_queue_repository.py
def test_keyed_duplicate_returns_existing_with_created_false(repo): ...
def test_multiple_null_keys_coexist(repo): ...            # the relied-upon engine property
def test_same_key_different_task_type_does_not_collide(repo): ...
def test_same_key_different_env_does_not_collide(repo): ...

@pytest.mark.parametrize("terminal", ["complete", "fail", "reschedule_overshoot", "claim_deadline"])
def test_terminal_transition_releases_key_and_allows_reenqueue(repo, terminal): ...

def test_reschedule_to_pending_retains_key(repo): ...
def test_unrelated_integrity_error_propagates(repo): ...  # must not read as duplicate
def test_session_usable_after_caught_integrity_error(repo): ...
```

Also update `test_enqueue_persists_pending_with_required_fields` and
`test_enqueue_payload_round_trips_as_json` for the `EnqueueResult` return shape,
and add one asserting an un-keyed enqueue still returns `created=True` with both
key columns `NULL` (the opt-out regression guard).

The OceanBase path is exercised by the existing unified-repository conformance
shape — the same body runs on both engines, and the one engine-specific detail
(`_is_active_idem_conflict`'s message matching) is why that helper is a pure
function over the exception, unit-testable without a MySQL instance.
