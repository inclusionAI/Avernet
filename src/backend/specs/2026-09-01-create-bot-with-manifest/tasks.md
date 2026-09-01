# Tasks: Creating a Bot With Its Configuration (W13)

Spec: `spec.md` · Plan: `plan.md` · Issue #1696.

> **Revision 5.** All open questions closed: terminal states split into
> `CREATE_FAILED` / `APPLY_FAILED` so the three failure modes are distinguishable,
> the submit response carries no state, and the endpoint refuses teclaw. Rev 4
> moved apply execution onto the task queue. Nothing adds a table or a column.

Five groups. A→B→C are a chain; D needs C; E proves the lot.

Conventions from `plan.md` that every task assumes:

- Phase A trigger is `create:pre_container`, phase B's is `create:on_container`.
- The materialiser gate is **derived from the registry**, never a written list.
- Nothing in a manifest may abort creation or touch the bot record.
- Both handlers are re-entrant, and the reason is **convergence, never "retry is
  off"** — at-least-once is structural.
- Phase A precedes creation; nothing inside `create_bot` is modified.

---

## Group A — Applying becomes durable work

## [x] Task 1: The apply task handler
- **Files:** `core/bot_config_manifest/apply/apply_task.py` (new),
  `di/modules/bot_management_module.py`
- **Done when:**
  - [x] One `TaskHandler` serves all three cases (spec D-10), registered into the
        `HandlerRegistry` at bootstrap with `wake_on_enqueue`.
  - [x] Its whole body runs inside `avernet_tenant_scope(payload["tenant"])`.
  - [x] It rebuilds the context rather than reading it from the payload: the bot
        record is re-read by `(entity_id, bot_id)`; for phase A there is no
        record, so `engine_type` / `bot_type` come from the payload and
        capabilities resolve from those.
  - [x] It re-reads and re-validates the document through `_parsed_or_empty`. A
        comment records why it is not in the payload: `MAX_DOCUMENT_BYTES` is
        64 KB and `ac_task_queue.payload` is `Text`, also 64 KB, so a large
        manifest would not fit — and the behaviour change this implies (read at
        execution rather than snapshot at enqueue) is stated there too.
  - [x] It writes the terminal record and releases the lock by token, on **every**
        path including a raising orchestrator — the same `finally` discipline
        `_run` has today.
- **Depends on:** —

## [x] Task 2: `start_apply` enqueues instead of spawning
- **Files:** `core/bot_config_manifest/services/config_manifest_apply_service.py`
- **Done when:**
  - [x] The lock, the re-validation, the `apply_id`, and the `RUNNING` record all
        stay **synchronous on the caller's thread**; only the work moves.
  - [x] The thread is replaced by an enqueue carrying the identifiers, the phase
        set, the trigger, the lock token and the tenant.
  - [x] `ApplyAccepted(apply_id, RUNNING)` is returned exactly as before.
  - [x] The docstring's "started, not awaited" reasoning is updated to say what
        now runs the work, and why that is durable where a thread was not.
  - [x] `POST …/config-manifest/apply` is unchanged in every observable way:
        `202` + `apply_id`, `ManifestApplyInProgressError` on a concurrent apply,
        a validation failure raised synchronously, the same report from the poll.
        **Its existing tests pass with their assertions untouched** — that is the
        check. Two waiting helpers change, because their subject is the execution
        mechanism itself; see the spec's corrected criterion.
- **Depends on:** Task 1

## [x] Task 3: `carry_from_apply_id`
- **Files:** the apply service, the protocol, `apply/outcomes.py` if a merge
  helper is needed
- **Done when:**
  - [x] `start_apply` accepts `carry_from_apply_id: str | None`, carried in the
        task payload.
  - [x] The named record's categories are prepended to the finished report and the
        summary re-derived over the union, so `APPLY_ORDER`'s order survives
        (`script` is position 0).
  - [x] A failed phase A carried into a clean phase B terminates `PARTIAL`.
  - [x] A missing or foreign id is ignored, not fatal.
  - [x] Phase A's own record is untouched by the carry.
- **Depends on:** Task 2

## [x] Task 4: `materialised_constructs()`
- **Files:** the apply service and its protocol
- **Done when:**
  - [x] The protocol declares `materialised_constructs() -> frozenset[ApplyConstruct]`
        as an `@abstractmethod`; the service returns
        `frozenset(build_materialisers(...).keys())` — the same registry
        `_orchestrator()` builds, not a second list.
  - [x] A test asserts it equals `{script, mcp}` **today**, and that registering a
        stub materialiser widens it with no edit to the gate.
  - [x] The docstring says why it is derived: a hand-written set drifts, and the
        drift is only observable as a failed apply on a bot that already exists.
- **Depends on:** —

---

## Group B — The creation seam

## [x] Task 5: The creation preflight
- **Files:** `core/bot_config_manifest/creation.py` (new)
- **Done when:**
  - [x] One function validates a document against an **engine type and bot type**
        (never a record) via `validate`, then refuses any **declared** construct
        absent from `materialised_constructs()`.
  - [x] "Declared" is `declared_entries(parsed, construct) is not None` walked
        over `APPLY_ORDER`, so a declared-empty category counts — it removes,
        which is a write.
  - [x] Every violation is reported in one pass, naming the construct and what
        would apply it.
  - [x] **A teclaw engine is refused** by the same function, naming W8 as where
        teclaw creation lives. The check goes through `is_teclaw` — the engine
        authority the capability resolver already takes — never a hand-rolled
        `== "teclaw"`.
  - [x] The module docstring states why this is stricter than `PUT`, and why the
        teclaw refusal is structural rather than a missing materialiser
        (`plan.md` K-9a).
- **Depends on:** Task 4

## [x] Task 6: The creation seam object
- **Files:** `core/bot_config_manifest/creation.py`
- **Done when:**
  - [x] Four operations: `preflight(engine, bot_type)`, `persist(entity_id, bot_id)`,
        `phase_a(...)` and `discard(entity_id, bot_id)`.
  - [x] `persist` writes through the existing manifest service — same validation,
        same all-or-nothing, same storage key.
  - [x] `phase_a` calls `start_apply(phases={PRE_CONTAINER}, trigger="create:pre_container")`
        with the creation attributes rather than a bot record, and **never
        raises** — a failure becomes a report.
  - [x] `phase_a` runs even when the document declares no `script`; the record is
        what tells the job phase A is done. A test pins this — it looks like a
        no-op worth optimising away.
  - [x] `discard` deletes the stored manifest **and any startup-script row phase A
        wrote**, idempotently.
- **Depends on:** Tasks 2, 5

## [x] Task 7: `entity_id` is resolved once
- **Files:** `core/bot_config_manifest/creation.py`, `core/bot_management/create_flow.py`
- **Done when:**
  - [x] The `entity_id` the manifest is stored under at submission is the value
        `create_bot` will resolve — taken from the **prepared** spec, after
        `_prepare_create`, not from the raw request.
  - [x] The poll resolves the same value the same way, from the authenticated
        caller, never from a request parameter.
  - [x] A test creates through the public surface and asserts the submitted row is
        found by the job's later read.
- **Depends on:** Task 6

---

## Group C — Creation, and the job that carries it

## [x] Task 8: Submission, without inline creation
- **Files:** `core/bot_management/create_flow.py`
- **Done when:**
  - [x] Submission runs policy, preflight (manifest **beside** quota/name/engine,
        **before** Passport), persist, then the Passport application — and
        **stops**. It does not take `create_bot_with_authorization`'s
        inline-create branch (`plan.md` K-9).
  - [x] `complete_bot_authorization` and `bot_service.create_bot` are **not
        modified at all**.
  - [x] Existing callers of `create_bot_with_authorization` behave exactly as
        today.
- **Depends on:** Task 6

## [x] Task 9: The creation job handler
- **Files:** `core/bot_config_manifest/create_job.py` (new),
  `di/modules/bot_management_module.py`
- **Done when:**
  - [x] A `TaskHandler` registered at bootstrap with `wake_on_enqueue`, its whole
        body inside `avernet_tenant_scope(payload["tenant"])`.
  - [x] The step machine of `plan.md` §K-5: Passport pending → `Reschedule(5s)`;
        declined → `discard` then `Fail`; issued and phase A not done → start
        phase A and `Reschedule` until its record is terminal; phase A done and no
        bot → `complete_bot_authorization(...)`; container not up → `Reschedule`;
        then start phase B with `carry_from` and **`Complete` without waiting for
        it**.
  - [x] Phase A's record is found with `last_apply(entity_id, bot_id)` and
        recognised by its `create:pre_container` trigger — the same read the poll
        makes, so no repository method is added.
  - [x] **Every step is re-entrant.** Invoking the handler twice at any step does
        not create a second bot, start a second apply, or mint a second Passport
        application. A test drives each step twice.
  - [x] The payload carries the creation attributes, the ids, the authorization
        handles and the tenant — everything the job and the poll need, since no
        request context exists at handler time.
- **Depends on:** Tasks 3, 6, 8

## [x] Task 10: The deadline, and what happens at it
- **Files:** `create_job.py`, the config module
- **Done when:**
  - [x] The deadline is configurable, default 600 s, passed as `deadline_seconds`
        so the queue enforces it DB-side.
  - [x] A creation that reaches it is `TIMED_OUT`, and the poll reports
        `AUTHORIZATION_EXPIRED` — never `AUTHORIZATION_REJECTED`, which would
        report a decision the user never made.
  - [x] The manifest **and any phase-A startup-script row** are deleted on every
        bot-less terminal, idempotently.
  - [x] A test asserts neither row survives an abandoned creation. This is what
        replaces the feature switch.
- **Depends on:** Task 9

---

## Group D — The public surface

## [x] Task 11: Request and response models
- **Files:** `adapters/http/openapi_v1/bots/schemas_create_with_manifest.py` (new)
- **Done when:**
  - [x] The create body carries the manifest plus the same creation attributes the
        existing create body accepts.
  - [x] The **poll has no body and no query parameters** — `bot_id` in the path is
        its whole input.
  - [x] A `CreationState` enum with these eight states — `AWAITING_AUTHORIZATION`,
        `AUTHORIZATION_REJECTED`, `AUTHORIZATION_EXPIRED`, `CREATING`,
        `CREATE_FAILED`, `APPLYING`, `READY`, `APPLY_FAILED`.
  - [x] The poll response carries the state, the authorization handles while
        awaiting, and — at `READY` and `APPLY_FAILED` — the apply report **and the
        bot**.
  - [x] **The submit response has no state field at all.** The enum appears only
        on the poll, so no terminal value can be returned by submission. A test
        pins that the submit model has no such field.
- **Depends on:** —

## [x] Task 12: The two routes
- **Files:** `adapters/http/openapi_v1/bots/create_with_manifest.py` (new),
  `adapters/http/openapi_v1/__init__.py`
- **Done when:**
  - [x] `POST /openapi/v1/bots/with-manifest` returns `202` with `bot_id` and
        both handles, carrying the same bars as the existing create (refused to
        an application caller). **No state** — that line said
        `AWAITING_AUTHORIZATION` until rev 5 confined the vocabulary to the poll,
        and this was the one place the reconciliation missed.
  - [x] `GET /openapi/v1/bots/{bot_id}/with-manifest/status` answers `plan.md`
        §K-8 and **makes no external call** — no AgentPass query, no work started,
        nothing written. A test asserts the Passport plugin is never touched.
  - [x] The three failure modes answer differently and without prose: an invalid
        manifest is a `422` at submission; a bot that could not be created or never
        came up is `CREATE_FAILED`; a running bot with an incomplete manifest is
        `APPLY_FAILED`. A test asserts all three.
  - [x] A teclaw creation is refused at submission, naming W8.
  - [x] The router is mounted where its `{bot_id}` literal cannot be captured by a
        wildcard group. **No feature switch.**
  - [x] Route docstrings state: the manifest is submitted once and never
        re-submitted; iteration 1's rule that a `script` must not depend on
        anything else the same manifest declares; and that `FAILED` leaves a
        running bot.
- **Depends on:** Tasks 9, 11

---

## Group E — Proof

## [x] Task 13: The ordering proof
- **Files:** `tests/community/core/bot_config_manifest/creation/test_creation_ordering.py`
  (new — under `core/`, where this package's suites actually live)
- **Done when:**
  - [x] Phase A completes **before creation is called at all** — asserted on
        recorded call order, not timing.
  - [x] The startup-script row is present when the payload is composed, and a
        manifest with a `script` produces a first boot carrying it.
  - [x] A phase A that fails still creates and provisions the bot, and the failure
        appears only in the report.
- **Depends on:** Task 9

## [x] Task 14: Durability and re-entrancy
- **Files:** `tests/community/core/bot_config_manifest/apply/test_apply_task.py` (new);
  the job's own double-drive stayed in `creation/test_create_job.py`, beside the
  step machine it is about
- **Done when:**
  - [x] An apply whose handler is invoked twice converges: the second run writes
        nothing.
  - [x] The lock is released on every path, including a raising orchestrator, and
        a task that never runs leaves a lock the TTL reaps.
  - [x] The creation job invoked twice at each step does not double-act.
  - [x] A comment or docstring near the re-entrancy tests states the reason —
        convergence and the lock, **not** an absence of retry.
- **Depends on:** Tasks 1, 9

## [ ] Task 15: Tenancy
- **Files:** `tests/community/bot_config_manifest/test_creation_tenancy.py` (new)
- **Done when:**
  - [ ] The tenant observed inside the apply handler and inside the creation job
        equals the submitting request's.
  - [ ] Each test uses a payload tenant that differs from the process default, so
        dropping the scope makes it **fail** rather than pass by coincidence —
        `get_current_avernet_tenant()` returns the default rather than raising.
- **Depends on:** Tasks 1, 9

## [ ] Task 16: Endpoint tests
- **Files:** `tests/community/endpoints/test_openapi_create_with_manifest.py` (new)
- **Done when:**
  - [ ] Full flow: submit → `202` → poll `AWAITING_AUTHORIZATION` → authorize →
        `CREATING` → `APPLYING` → `READY`, the report carrying **both** phases.
  - [ ] An invalid manifest is refused `422` with every violation named, and
        **Passport is never called** — asserted on the plugin.
  - [ ] A construct with no materialiser is refused at submission, naming it.
  - [ ] A `PARTIAL` apply reports `APPLY_FAILED`, the response **carries the
        bot**, and the bot record is untouched.
  - [ ] A creation whose provisioning fails reports `CREATE_FAILED`, never
        `APPLY_FAILED`.
  - [ ] `AUTHORIZATION_REJECTED` and `AUTHORIZATION_EXPIRED` are terminal, create
        nothing, and leave no manifest or startup-script row.
  - [ ] Creation with no manifest reports `READY`.
- **Depends on:** Task 12

## [ ] Task 17: Nothing else moved
- **Files:** existing suites
- **Done when:**
  - [ ] Every existing create, auth-status, config-manifest, apply and
        startup-script test passes **unedited** — the load-bearing check on Task 2,
        since moving apply onto the queue must change no contract.
  - [ ] A bot created by the existing endpoint and given a manifest by `PUT` still
        applies with no restart, by the same path as before.
- **Depends on:** Tasks 2, 12

## [ ] Task 18: Documentation
- **Files:** `core/bot_config_manifest/README.md`,
  `docs/bot-config-manifest/user-manual.zh-CN.md`,
  `docs/bot-config-manifest/work-items.zh-CN.md` (+ the English `work-items.md`)
- **Done when:**
  - [ ] The user manual documents the create-with-manifest flow, the poll states,
        the deadline, `PARTIAL → FAILED`, and that `FAILED` leaves a running bot.
  - [ ] The `script`-dependency rule is written where a manifest author will read
        it, marked iteration 1 only and pointing at #1508.
  - [ ] **The operational precondition is written where an operator will find
        it**: applying runs on the task queue, so the worker must be enabled and
        `ac_task_queue` provisioned, and with the worker off creations do not
        merely slow down — they never complete.
  - [ ] The README's Context Boundary block lists the creation seam, the apply
        task and the creation job.
  - [x] **Both work-items documents are already reconciled** (done ahead of
        implementation, since they are the source of truth and the spec had
        overtaken them). Nine sites in each, kept in parity: W13's header note,
        its engine scope, its dependency gate, its out-of-scope, the poll states,
        the `PARTIAL` bullets, the two-phase criteria, §2.11's feature-flag
        paragraph, the task-queue tenant note, the §6 ordering claim — and **W8's
        criteria**, which now say W8 owns teclaw creation *including lifting this
        endpoint's refusal*, the one piece that would otherwise have fallen
        between the two items.
  - [ ] Re-check that parity still holds at the end of implementation: anything
        the code forced to differ from the spec must land in both work-items
        files, not just the spec.
- **Depends on:** Task 16
