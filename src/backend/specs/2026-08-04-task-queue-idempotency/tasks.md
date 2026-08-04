# Tasks: Task Queue Idempotency (opt-in, active-only enqueue dedup key)

Spec: `spec.md` · Plan: `plan.md` · Issue: [#569](https://github.com/inclusionAI/Avernet/issues/569) · PR: [#789](https://github.com/inclusionAI/Avernet/pull/789)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1 `[x]`: Add the schema columns, unique index, and prod DDL
- **Goal:** `ac_task_queue` carries `idempotency_key` and `active_idempotency_key` with the active-only unique index, in both the ORM definition and the checked-in prod DDL.
- **Files:** `core/task_queue/repository/models.py`, `core/task_queue/sql/2026_08_04_task_queue_idempotency.sql` (new dir + file)
- **Done when:**
  - [x] Both columns exist on `TaskQueueModel` as `String(190), nullable=True` with comments.
  - [x] `__table_args__` carries `Index("uk_env_task_type_active_idem", "env", "task_type", "active_idempotency_key", unique=True)`.
  - [x] The `.sql` file mirrors the ORM exactly and uses the `GLOBAL` index keyword, matching `core/service_bot/sql/ac_bot_publish.sql:22-26`.
  - [x] The file header states the DDL must be applied before the code that writes the columns, and that no backfill is required.
  - [x] `Base.metadata.create_all()` builds the table with the index on SQLite (existing `repo` fixture still constructs). Verified: index reports `unique=1` over `['env', 'task_type', 'active_idempotency_key']`; 21 existing tests pass unchanged.
- **Depends on:** —
- **Note:** This task is inert at runtime — nothing writes the columns yet.

## Task 2 `[x]`: Add `EnqueueResult` and project the key onto `TaskRecord`
- **Goal:** The value types can express "(record, created)" and carry the audit key.
- **Files:** `core/task_queue/types.py`, `core/task_queue/repository/models.py`
- **Done when:**
  - [x] `EnqueueResult(NamedTuple)` with `record: TaskRecord` and `created: bool`, docstringed to say `created=False` only when a keyed enqueue joined a live task.
  - [x] `TaskRecord` gains `idempotency_key: Optional[str] = None`, placed after `env` (all following fields already have defaults).
  - [x] `to_record()` projects `idempotency_key`.
  - [x] `active_idempotency_key` is **not** projected — it is an enforcement detail (asserted in tests by querying the model directly).
  - [x] Exported from `core/task_queue/types.py`'s public surface consistently with `TaskRecord`. No-op: neither `types.py` nor the package `__init__.py` declares `__all__` or re-exports, so `EnqueueResult` is reachable exactly as `TaskRecord` is.
- **Depends on:** Task 1
- **Note:** `Optional[str]` here is contract-intentional (opt-out is a real state), so it does not violate the `T | None` rule in `CLAUDE.md`.

## Task 3 `[x]`: Release the key on every terminal transition
- **Goal:** All four terminal writes null `active_idempotency_key` in the same `UPDATE` as the status change.
- **Files:** `plugins/task_queue_repository.py`
- **Done when:**
  - [x] `claim_batch` past-deadline `TIMED_OUT` (now `:205`) clears the key.
  - [x] `complete()` → `SUCCEEDED` (now `:238`) clears the key.
  - [x] `reschedule()` deadline-overshoot → `TIMED_OUT` (now `:287`) clears the key.
  - [x] `fail()` → `FAILED` (now `:304`) clears the key.
  - [x] `reschedule()`'s `PENDING` branch, `claim_batch`'s `RUNNING` claim, and `renew_lease` are **unchanged** — those tasks are still live and keep their key.
  - [x] Each clear is inside the existing `SET` dict, not a separate statement.
  - [x] The module docstring documents the active-only invariant, enumerates the four releasing transitions, and names the three that deliberately retain the key. 21 existing tests pass.
- **Depends on:** Task 1
- **Note:** Sequenced **before** the insert path deliberately. Shipping release-without-insert is a no-op; shipping insert-without-release would silently behave as all-time-unique — the exact failure mode this design exists to avoid.

## Task 4 `[x]`: Implement the keyed insert path
- **Goal:** `enqueue` dedupes a keyed submission against the live holder and returns `EnqueueResult`.
- **Files:** `plugins/task_queue_repository.py`, `tests/community/plugins/test_task_queue_repository.py` (helper only)
- **Done when:**
  - [x] `enqueue` accepts `idempotency_key: Optional[str] = None` and returns `EnqueueResult`.
  - [x] `idempotency_key is None` → plain insert, `created=True`, both columns `NULL`, no conflict machinery on the path.
  - [x] A keyed insert writes the value to **both** columns.
  - [x] `_is_active_idem_conflict(exc)` is a module-level pure function over the exception, matching the MySQL/OceanBase form (index name in the message) **and** the SQLite form (column list). Anything else re-raises.
  - [x] `_find_active_by_key(env, task_type, key)` filters on `active_idempotency_key`, so terminal rows are invisible by construction.
  - [x] The `try` wraps the whole `with self._db.orm_session()` block — via the extracted `_insert` helper, so the failed INSERT is rolled back and closed by the context manager and the re-`SELECT` runs in a fresh session with no savepoint.
  - [x] The insert/re-`SELECT` race is bounded to two attempts; two consecutive losses raise rather than looping.
  - [x] The existing `[task_queue.enqueue]` log line distinguishes created from joined.
- **Note:** The `_enqueue` test-helper unwrap (originally listed under Task 6) was pulled forward into this task — the return-type change breaks all 21 pre-existing tests otherwise, and leaving the tree red across two tasks is worse than the small reordering.
- **Depends on:** Tasks 2, 3

## Task 5 `[x]`: Update the contract and the docs that promise the opposite
- **Goal:** The protocol, the facade, and the README describe insert-time dedup.
- **Files:** `core/task_queue/repository/protocol.py`, `core/task_queue/services/task_queue_service.py`, `core/task_queue/README.md`, `core/task_queue/__init__.py`, plus two test helpers (see note)
- **Done when:**
  - [x] `TaskQueueRepositoryProtocol.enqueue` takes `idempotency_key: Optional[str] = None` and returns `EnqueueResult`.
  - [x] `protocol.py`'s "Duplicate enqueues create distinct rows — idempotency is a claim-time guarantee, not an insert-time one" is replaced per `plan.md`.
  - [x] The protocol module docstring is reconciled — it now frames claim-time and enqueue-time as two guarantees answering two different questions.
  - [x] `TaskQueueService.enqueue` forwards `idempotency_key` and returns `EnqueueResult`.
  - [x] `README.md`'s "How idempotency works" gains an insert-time section alongside claim-time, states that terminal tasks release their key, and documents the convention `<entity>:<entity_id>[:<qualifier>][:<generation>]`.
  - [x] README notes that multiple `NULL` keys coexisting is a **relied-upon** engine property, not an assumption.
  - [x] The README Context Boundary block still reflects what the module provides (unchanged — no new provides/consumes).
  - [x] The package `__init__.py` docstring mentions enqueue dedup alongside claim-time.
  - [x] The past-deadline-but-unscanned edge found during Group B review is documented in both the protocol docstring and the README.
- **Note:** The return-type change reached two test files the plan did not anticipate: `tests/community/core/task_queue/test_task_worker.py` (its `_World.enqueue` helper, now unwrapping `.record`) and `tests/community/endpoints/test_draft_restore_durable_task.py` (which destructures the pair). Full backend suite: 10305 passed.
- **Depends on:** Task 4
- **Note:** protocol.py and README.md change together in one commit, per spec (g).

## Task 6 `[x]`: Test enqueue dedup, opt-out, and scoping
- **Goal:** Cover spec (g) cases 1–4 plus the opt-out regression guard.
- **Files:** `tests/community/plugins/test_task_queue_repository.py`
- **Done when:**
  - [x] The `_enqueue` helper unwraps `.record` so the **22 existing call sites stay untouched**; a separate `_enqueue_result` helper returns the full `EnqueueResult` for new tests. *(Landed in Task 4 — see its note.)*
  - [x] Duplicate keyed enqueue returns the existing record with `created=False` and inserts no second row (row count asserted).
  - [x] Multiple `NULL` keys coexist — the relied-upon engine property, asserted explicitly.
  - [x] The same key under a different `task_type` does not collide.
  - [x] The same key under a different `env` does not collide.
  - [x] An un-keyed enqueue returns `created=True` with both key columns `NULL`.
  - [x] A keyed enqueue writes the same value to both columns.
- **Depends on:** Task 4

## Task 7 `[x]`: Test key release, retention, and error handling
- **Goal:** Cover spec (g) cases 5–8 — the invariant that makes active-only correct.
- **Files:** `tests/community/plugins/test_task_queue_repository.py`
- **Done when:**
  - [x] Parametrized over all four terminal paths (`complete`, `fail`, `reschedule`-overshoot, claim-path `TIMED_OUT`): the key is released **and** re-enqueue on the same key then succeeds with `created=True`.
  - [x] `reschedule` back to `PENDING` **retains** the key, and a re-enqueue on it still returns `created=False`. Also asserted for a `RUNNING` task.
  - [x] An unrelated `IntegrityError` propagates rather than being read as a duplicate.
  - [x] The session is usable after a caught `IntegrityError` (a subsequent enqueue on the same repo succeeds).
  - [x] `_is_active_idem_conflict` is unit-tested against both the MySQL/OceanBase and SQLite message forms, without needing a MySQL instance.
  - [x] Added beyond the plan, to close the changed-line coverage gate: the insert/re-`SELECT` race where the holder goes terminal in the window (the retry path), the two-attempt ceiling raising, and the past-deadline-but-unscanned edge documented as current behavior.
- **Depends on:** Tasks 3, 4
- **Note:** These tests were the fix for the CI changed-line coverage gate — the keyed insert path and `_is_active_idem_conflict` were entirely uncovered, putting changed-line coverage at 56.25% against an 80% minimum. Module coverage went 81% → 98%; the two lines still uncovered (`_now_plus`'s mysql branch, `claim_batch`'s `limit <= 0` guard) are pre-existing and outside this PR's diff.

## Task 8 `[x]`: Verify spec acceptance
- **Goal:** Confirm every acceptance criterion in `spec.md` holds.
- **Files:** —
- **Done when:**
  - [x] Acceptance 1 — columns + unique index exist in ORM (`models.py`) and in the checked-in prod DDL (`sql/2026_08_04_task_queue_idempotency.sql`); SQLite `create_all` reports the index as `unique=1`.
  - [x] Acceptance 2 — `enqueue` takes an optional key and returns `(record, created)` across all three layers (protocol, service, repository).
  - [x] Acceptance 3 — `test_duplicate_keyed_enqueue_returns_existing_and_inserts_nothing` asserts `created=False`, same id, and a row count of 1.
  - [x] Acceptance 4 — `test_terminal_transition_releases_key_and_allows_reenqueue`, parametrized over all four terminal paths.
  - [x] Acceptance 5 — un-keyed enqueues behave exactly as today: all pre-existing tests pass with only their helper unwrapping the pair, and `test_unkeyed_enqueue_creates_a_row_with_both_key_columns_null` pins it directly.
  - [x] Acceptance 6 — README and protocol describe insert-time dedup; 39 repository tests pass on SQLite.
  - [x] Zero call sites changed — the only `idempotency_key` hits outside `core/task_queue/` are an unrelated local variable in `data_init_service.py` (a message-API `idempotencyKey`).
  - [x] Backend module gate passes locally: **10323 passed, 3 skipped**; `report_check.py` against `origin/dev` reports case pass rate 100.00%, line coverage 84.78% (≥75), **change line coverage 100.00% (48/48, ≥80)** — the same 48-line denominator CI reported, up from 27/48.
- **Depends on:** Tasks 5, 6, 7
- **Open, not blocking:** the past-deadline-but-unscanned edge (documented in `protocol.py`, `README.md`, and pinned by `test_past_deadline_task_not_yet_scanned_still_holds_its_key`) is recorded as current behavior awaiting a decision. Changing it would be a spec change.

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
