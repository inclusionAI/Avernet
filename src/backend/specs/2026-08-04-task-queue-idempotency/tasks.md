# Tasks: Task Queue Idempotency (opt-in, active-only enqueue dedup key)

Spec: `spec.md` · Plan: `plan.md` · Issue: [#569](https://github.com/inclusionAI/Avernet/issues/569) · PR: [#789](https://github.com/inclusionAI/Avernet/pull/789)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Add the schema columns, unique index, and prod DDL
- **Goal:** `ac_task_queue` carries `idempotency_key` and `active_idempotency_key` with the active-only unique index, in both the ORM definition and the checked-in prod DDL.
- **Files:** `core/task_queue/repository/models.py`, `core/task_queue/sql/2026_08_04_task_queue_idempotency.sql` (new dir + file)
- **Done when:**
  - [ ] Both columns exist on `TaskQueueModel` as `String(190), nullable=True` with comments.
  - [ ] `__table_args__` carries `Index("uk_env_task_type_active_idem", "env", "task_type", "active_idempotency_key", unique=True)`.
  - [ ] The `.sql` file mirrors the ORM exactly and uses the `GLOBAL` index keyword, matching `core/service_bot/sql/ac_bot_publish.sql:22-26`.
  - [ ] The file header states the DDL must be applied before the code that writes the columns, and that no backfill is required.
  - [ ] `Base.metadata.create_all()` builds the table with the index on SQLite (existing `repo` fixture still constructs).
- **Depends on:** —
- **Note:** This task is inert at runtime — nothing writes the columns yet.

## Task 2: Add `EnqueueResult` and project the key onto `TaskRecord`
- **Goal:** The value types can express "(record, created)" and carry the audit key.
- **Files:** `core/task_queue/types.py`, `core/task_queue/repository/models.py`
- **Done when:**
  - [ ] `EnqueueResult(NamedTuple)` with `record: TaskRecord` and `created: bool`, docstringed to say `created=False` only when a keyed enqueue joined a live task.
  - [ ] `TaskRecord` gains `idempotency_key: Optional[str] = None`, placed after `env` (all following fields already have defaults).
  - [ ] `to_record()` projects `idempotency_key`.
  - [ ] `active_idempotency_key` is **not** projected — it is an enforcement detail (asserted in tests by querying the model directly).
  - [ ] Exported from `core/task_queue/types.py`'s public surface consistently with `TaskRecord`.
- **Depends on:** Task 1
- **Note:** `Optional[str]` here is contract-intentional (opt-out is a real state), so it does not violate the `T | None` rule in `CLAUDE.md`.

## Task 3: Release the key on every terminal transition
- **Goal:** All four terminal writes null `active_idempotency_key` in the same `UPDATE` as the status change.
- **Files:** `plugins/task_queue_repository.py`
- **Done when:**
  - [ ] `claim_batch` past-deadline `TIMED_OUT` (`:200`) clears the key.
  - [ ] `complete()` → `SUCCEEDED` (`:232`) clears the key.
  - [ ] `reschedule()` deadline-overshoot → `TIMED_OUT` (`:278`) clears the key.
  - [ ] `fail()` → `FAILED` (`:293`) clears the key.
  - [ ] `reschedule()`'s `PENDING` branch (`:250-255`), `claim_batch`'s `RUNNING` claim (`:168-176`), and `renew_lease` (`:308-311`) are **unchanged** — those tasks are still live and keep their key.
  - [ ] Each clear is inside the existing `SET` dict, not a separate statement.
- **Depends on:** Task 1
- **Note:** Sequenced **before** the insert path deliberately. Shipping release-without-insert is a no-op; shipping insert-without-release would silently behave as all-time-unique — the exact failure mode this design exists to avoid.

## Task 4: Implement the keyed insert path
- **Goal:** `enqueue` dedupes a keyed submission against the live holder and returns `EnqueueResult`.
- **Files:** `plugins/task_queue_repository.py`
- **Done when:**
  - [ ] `enqueue` accepts `idempotency_key: Optional[str] = None` and returns `EnqueueResult`.
  - [ ] `idempotency_key is None` → plain insert, `created=True`, both columns `NULL`, no conflict machinery on the path.
  - [ ] A keyed insert writes the value to **both** columns.
  - [ ] `_is_active_idem_conflict(exc)` is a module-level pure function over the exception, matching the MySQL/OceanBase form (index name in the message) **and** the SQLite form (column list). Anything else re-raises.
  - [ ] `_find_active_by_key(env, task_type, key)` filters on `active_idempotency_key`, so terminal rows are invisible by construction.
  - [ ] The `try` wraps the whole `with self._db.orm_session()` block — the context manager already rolls back and closes on exception (`plugins/local/database.py:207-217`), so the re-`SELECT` runs in a fresh session with no savepoint.
  - [ ] The insert/re-`SELECT` race is bounded to two attempts; two consecutive losses raise rather than looping.
  - [ ] The existing `[task_queue.enqueue]` log line distinguishes created from joined.
- **Depends on:** Tasks 2, 3

## Task 5: Update the contract and the docs that promise the opposite
- **Goal:** The protocol, the facade, and the README describe insert-time dedup.
- **Files:** `core/task_queue/repository/protocol.py`, `core/task_queue/services/task_queue_service.py`, `core/task_queue/README.md`
- **Done when:**
  - [ ] `TaskQueueRepositoryProtocol.enqueue` takes `idempotency_key: Optional[str] = None` and returns `EnqueueResult`.
  - [ ] `protocol.py:49-50`'s "Duplicate enqueues create distinct rows — idempotency is a claim-time guarantee, not an insert-time one" is replaced per `plan.md`.
  - [ ] The module docstring at `protocol.py:16-22` ("**Claiming** is where idempotency is enforced") is reconciled — claim-time is no longer the only enforcement point.
  - [ ] `TaskQueueService.enqueue` forwards `idempotency_key` and returns `EnqueueResult`.
  - [ ] `README.md`'s "How idempotency works" gains an insert-time section alongside claim-time, states that terminal tasks release their key, and documents the convention `<entity>:<entity_id>[:<qualifier>][:<generation>]`.
  - [ ] README notes that multiple `NULL` keys coexisting is a **relied-upon** engine property, not an assumption.
  - [ ] The README Context Boundary block still reflects what the module provides.
- **Depends on:** Task 4
- **Note:** protocol.py and README.md change together in one commit, per spec (g).

## Task 6: Test enqueue dedup, opt-out, and scoping
- **Goal:** Cover spec (g) cases 1–4 plus the opt-out regression guard.
- **Files:** `tests/community/plugins/test_task_queue_repository.py`
- **Done when:**
  - [ ] The `_enqueue` helper at `:60` unwraps `.record` so the **22 existing call sites stay untouched**; a separate `_enqueue_result` helper returns the full `EnqueueResult` for new tests.
  - [ ] Duplicate keyed enqueue returns the existing record with `created=False` and inserts no second row (row count asserted).
  - [ ] Multiple `NULL` keys coexist — the relied-upon engine property, asserted explicitly.
  - [ ] The same key under a different `task_type` does not collide.
  - [ ] The same key under a different `env` does not collide.
  - [ ] An un-keyed enqueue returns `created=True` with both key columns `NULL`.
  - [ ] A keyed enqueue writes the same value to both columns.
- **Depends on:** Task 4

## Task 7: Test key release, retention, and error handling
- **Goal:** Cover spec (g) cases 5–8 — the invariant that makes active-only correct.
- **Files:** `tests/community/plugins/test_task_queue_repository.py`
- **Done when:**
  - [ ] Parametrized over all four terminal paths (`complete`, `fail`, `reschedule`-overshoot, claim-path `TIMED_OUT`): the key is released **and** re-enqueue on the same key then succeeds with `created=True`.
  - [ ] `reschedule` back to `PENDING` **retains** the key, and a re-enqueue on it still returns `created=False`.
  - [ ] An unrelated `IntegrityError` propagates rather than being read as a duplicate.
  - [ ] The session is usable after a caught `IntegrityError` (a subsequent enqueue on the same repo succeeds).
  - [ ] `_is_active_idem_conflict` is unit-tested against both the MySQL/OceanBase and SQLite message forms, without needing a MySQL instance.
- **Depends on:** Tasks 3, 4

## Task 8: Verify spec acceptance
- **Goal:** Confirm every acceptance criterion in `spec.md` holds.
- **Files:** —
- **Done when:**
  - [ ] Acceptance 1 — columns + unique index exist in ORM and in the checked-in prod DDL.
  - [ ] Acceptance 2 — `enqueue` takes an optional key and returns `(record, created)`.
  - [ ] Acceptance 3 — a keyed enqueue against a live holder returns it with `created=False` and inserts no row.
  - [ ] Acceptance 4 — every terminal transition releases the key; re-enqueue then creates a new task.
  - [ ] Acceptance 5 — **un-keyed enqueues behave exactly as today**; all 22 pre-existing repository tests pass unmodified in behavior.
  - [ ] Acceptance 6 — README and protocol describe insert-time dedup; the eight tests pass on SQLite.
  - [ ] Zero call sites changed — `grep` confirms no adopter passes `idempotency_key` in this PR.
  - [ ] Backend module gates pass (`OCB_PRE_PUSH_RUN_CI=1` per `AGENTS.md`).
- **Depends on:** Tasks 5, 6, 7

---

## Groups

- **Group A — Foundation:** Tasks 1, 2
  - Theme: Schema, DDL, and value types. Entirely inert at runtime — nothing writes the columns yet.
- **Group B — Repository behavior:** Tasks 3, 4
  - Theme: The mechanism itself — keys are released on terminal, then keyed inserts dedupe against live holders. Release lands first so no intermediate state behaves as all-time-unique.
- **Group C — Contract & docs:** Task 5
  - Theme: The protocol, facade, and README stop promising insert-time duplicates are inevitable.
- **Group D — Verification:** Tasks 6, 7, 8
  - Theme: The eight spec (g) cases, plus confirmation that un-keyed behavior is untouched.
