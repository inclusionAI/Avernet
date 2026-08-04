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
- `src/backend/src/agentclaw/community/core/task_queue/sql/` — **new directory**; the prod DDL, per the per-module `sql/` convention already used by `core/session_resources/sql/` and `core/service_bot/sql/`.
- `src/backend/src/agentclaw/community/core/task_queue/README.md` — the "How idempotency works" section.
- `src/backend/tests/community/plugins/test_task_queue_repository.py` — the eight cases from spec (g).

## Data Model Changes

```sql
-- core/task_queue/sql/2026_08_04_task_queue_idempotency.sql
-- Apply BEFORE deploying Backend code that writes these columns.
-- Existing rows take NULL in both columns; NULLs are distinct in a unique
-- index, so no existing row can collide and no backfill or scrub is required.

ALTER TABLE `ac_task_queue`
  ADD COLUMN `idempotency_key` varchar(190) DEFAULT NULL
    COMMENT '调用方提供的入队去重键；NULL 表示不去重',
  ADD COLUMN `active_idempotency_key` varchar(190) DEFAULT NULL
    COMMENT '去重键的执行副本；进入终态时置 NULL 以释放该键',
  ADD UNIQUE KEY `uk_env_task_type_active_idem`
    (`env`, `task_type`, `active_idempotency_key`) GLOBAL;
```

`GLOBAL` matches the existing OceanBase index convention in
`core/service_bot/sql/ac_bot_publish.sql:22-26`.

**Index key length is not a blocker.** The composite key is
`(20 + 100 + 190) × 4 = 1240` bytes under `utf8mb4`. `ac_bot_publish` already
carries `UNIQUE KEY uk_oi_p_b_v (owner_id, publish_bot_id, version)` at
`(128 + 1024) × 4 + 8 ≈ 4.6 KB`, so the deployment demonstrably tolerates index
keys far past the 767-byte `COMPACT` limit. The spec's pre-flight item is
resolved; no column needs shortening.

```diff
# core/task_queue/repository/models.py:79 — new columns after deadline_at
+    idempotency_key = Column(
+        String(190),
+        nullable=True,
+        comment="caller-supplied enqueue dedup key; NULL = opted out. Audit only",
+    )
+    active_idempotency_key = Column(
+        String(190),
+        nullable=True,
+        comment="enforcement copy of idempotency_key; NULLed on terminal transitions",
+    )
```

```diff
# core/task_queue/repository/models.py:100 — __table_args__
     __table_args__ = (
         Index("idx_env_status_run_at", "env", "status", "run_at"),
         Index("idx_env_lease_expires_at", "env", "lease_expires_at"),
+        # Active-only enqueue dedup. NULL active key = opted out; engines treat
+        # NULLs as distinct in a unique index, which is what makes it opt-in.
+        Index(
+            "uk_env_task_type_active_idem",
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
# core/task_queue/types.py:71 — TaskRecord, after `env`
     env: str
+    idempotency_key: Optional[str] = None
     gmt_create: Optional[datetime] = None
```

`active_idempotency_key` is deliberately **not** projected onto `TaskRecord` —
it is an enforcement detail, not part of the record's meaning. Tests assert on
it by querying the model directly.

```diff
# core/task_queue/repository/protocol.py:36 — NOT breaking; no call site
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
# core/task_queue/services/task_queue_service.py:24
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
# plugins/task_queue_repository.py — replaces the plain-INSERT enqueue at :98
def enqueue(self, *, task_type, payload, delay_seconds, deadline_seconds,
            env, idempotency_key=None) -> EnqueueResult:
    """Un-keyed → plain insert, always created=True (today's behavior).
    Keyed → try-insert; on *this* constraint, re-SELECT the live holder."""
    if idempotency_key is None:
        return EnqueueResult(self._insert(...), True)
    for _ in range(2):                      # bounded: see the race note below
        try:
            return EnqueueResult(self._insert(..., idempotency_key=...), True)
        except IntegrityError as exc:
            if not _is_active_idem_conflict(exc):
                raise                       # unrelated constraint — never swallow
            existing = self._find_active_by_key(env, task_type, idempotency_key)
            if existing is not None:
                return EnqueueResult(existing, False)
            # holder went terminal between INSERT and SELECT → key is free again
    raise RuntimeError(...)                 # two consecutive losses: surface it
```

The `for _ in range(2)` is the spec's bounded retry for the insert/re-`SELECT`
race — the conflicting row can reach a terminal state (releasing the key) in the
window between the failed insert and the lookup, leaving nothing to return.

```python
# plugins/task_queue_repository.py (new module-level helper)
def _is_active_idem_conflict(exc: IntegrityError) -> bool:
    """True only for uk_env_task_type_active_idem.

    Portable across both engines because they name the violation differently:
    MySQL/OceanBase report the *index* ("Duplicate entry … for key
    'uk_env_task_type_active_idem'"); SQLite reports the *columns* ("UNIQUE
    constraint failed: ac_task_queue.env, ac_task_queue.task_type, …").
    """
```

```python
# plugins/task_queue_repository.py (new private method)
def _find_active_by_key(self, env: str, task_type: str, key: str) -> Optional[TaskRecord]:
    """The live holder of ``key``, or None. Filters on active_idempotency_key,
    so terminal rows (which released it) are invisible here by construction."""
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
# plugins/task_queue_repository.py:200 — claim_batch, past-deadline TIMED_OUT
                         self.Model.status: TaskStatus.TIMED_OUT.value,
                         self.Model.last_error: "deadline elapsed before execution",
                         self.Model.claimed_by: None,
                         self.Model.lease_expires_at: None,
+                        self.Model.active_idempotency_key: None,
```

```diff
# plugins/task_queue_repository.py:232 — complete() → SUCCEEDED
                 self.Model.status: TaskStatus.SUCCEEDED.value,
                 self.Model.claimed_by: None,
                 self.Model.lease_expires_at: None,
+                self.Model.active_idempotency_key: None,
```

```diff
# plugins/task_queue_repository.py:278 — reschedule() deadline-overshoot → TIMED_OUT
                         self.Model.status: TaskStatus.TIMED_OUT.value,
                         self.Model.last_error: (error or "deadline elapsed"),
                         self.Model.claimed_by: None,
                         self.Model.lease_expires_at: None,
+                        self.Model.active_idempotency_key: None,
```

```diff
# plugins/task_queue_repository.py:293 — fail() → FAILED
                 self.Model.status: TaskStatus.FAILED.value,
                 self.Model.last_error: error,
                 self.Model.claimed_by: None,
                 self.Model.lease_expires_at: None,
+                self.Model.active_idempotency_key: None,
```

`reschedule()`'s `PENDING` branch at `:250-255` is **deliberately not touched** —
the task is still live, so it keeps holding its key. Same for `claim_batch`'s
`RUNNING` claim at `:168-176` and `renew_lease` at `:308-311`.

These four are the complete set: a repo-wide grep for `TaskStatus.SUCCEEDED` /
`FAILED` / `TIMED_OUT` writes finds only lines 200, 232, 278, 293, and
`worker.py` reaches terminal states solely through `complete`/`fail`
(`worker.py:206, 264, 270`).

### Docs that currently promise the opposite

```diff
# core/task_queue/repository/protocol.py:49
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
- **Risk:** The DDL ships after code that writes the columns → `Unknown column` in prod.
  **Mitigation:** Rollout ordering below; the columns are write-only-if-present nowhere — there is no fallback path, so ordering is mandatory, not best-effort.

## Alternatives Considered

- **All-time uniqueness** (the issue's original lean) — rejected on the call-site audit in spec (a): it breaks progress-poll, publish retry, bot restart, and skills-pool reconcile.
- **A single high-cardinality token column** (`UNIQUE (env + task_type + key)` concatenated) — avoids the low-cardinality prefix, but `task_type` is `VARCHAR(100)` and `env` `VARCHAR(20)`, leaving under 70 chars for the caller's key inside the 190-char budget.
- **`INSERT ... ON DUPLICATE KEY UPDATE`** — MySQL/OceanBase only; would fork the deliberately unified SQLite+OceanBase repository body.
- **Partial/filtered unique index** (`UNIQUE ... WHERE status NOT IN (…)`) — SQLite supports it, MySQL/OceanBase do not. This is precisely why the second enforcement column exists.
- **Reusing `status` in the unique index** (`UNIQUE (env, task_type, key, status)`) — would permit one row per key *per terminal status*, which is not the invariant; and it still burns the key against a second `PENDING`.

## Rollout

```bash
# 1. DDL to prod FIRST — the code below writes columns that must already exist.
mysql < src/backend/src/agentclaw/community/core/task_queue/sql/2026_08_04_task_queue_idempotency.sql

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
`_enqueue` helper at `:60` gains an `idempotency_key=None` pass-through.

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
