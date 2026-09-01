# Tasks: Creating a Bot With Its Configuration (W13)

Spec: `spec.md` · Plan: `plan.md` · Issue #1696.

> **Revision 4.** Group A now moves apply execution onto the task queue (spec D-9,
> D-10) — that is new work, and it changes the running-bot path's executor while
> leaving its contract alone. Group C loses the phase-B wait. Nothing adds a table
> or a column.

Six groups. A→B→C are a chain; D needs C; E proves the lot.

Conventions from `plan.md` that every task assumes:

- Phase A trigger is `create:pre_container`, phase B's is `create:on_container`.
- The materialiser gate is **derived from the registry**, never a written list.
- Nothing in a manifest may abort creation or touch the bot record.
- Both handlers are re-entrant, and the reason is **convergence, never "retry is
  off"** — at-least-once is structural.
- Phase A precedes creation; nothing inside `create_bot` is modified.

---

## Group A — Applying becomes durable work

## [ ] Task 1: The apply task handler
- **Files:** `core/bot_config_manifest/apply/apply_task.py` (new),
  `di/modules/bot_management_module.py`
- **Done when:**
  - [ ] One `TaskHandler` serves all three cases (spec D-10), registered into the
        `HandlerRegistry` at bootstrap with `wake_on_enqueue`.
  - [ ] Its whole body runs inside `avernet_tenant_scope(payload["tenant"])`.
  - [ ] It rebuilds the context rather than reading it from the payload: the bot
        record is re-read by `(entity_id, bot_id)`; for phase A there is no
        record, so `engine_type` / `bot_type` come from the payload and
        capabilities resolve from those.
  - [ ] It re-reads and re-validates the document through `_parsed_or_empty`. A
        comment records why it is not in the payload: `MAX_DOCUMENT_BYTES` is
        64 KB and `ac_task_queue.payload` is `Text`, also 64 KB, so a large
        manifest would not fit — and the behaviour change this implies (read at
        execution rather than snapshot at enqueue) is stated there too.
  - [ ] It writes the terminal record and releases the lock by token, on **every**
        path including a raising orchestrator — the same `finally` discipline
        `_run` has today.
- **Depends on:** —

## [ ] Task 2: `start_apply` enqueues instead of spawning
- **Files:** `core/bot_config_manifest/services/config_manifest_apply_service.py`
- **Done when:**
  - [ ] The lock, the re-validation, the `apply_id`, and the `RUNNING` record all
        stay **synchronous on the caller's thread**; only the work moves.
  - [ ] The thread is replaced by an enqueue carrying the identifiers, the phase
        set, the trigger, the lock token and the tenant.
  - [ ] `ApplyAccepted(apply_id, RUNNING)` is returned exactly as before.
  - [ ] The docstring's "started, not awaited" reasoning is updated to say what
        now runs the work, and why that is durable where a thread was not.
  - [ ] `POST …/config-manifest/apply` is unchanged in every observable way:
        `202` + `apply_id`, `ManifestApplyInProgressError` on a concurrent apply,
        a validation failure raised synchronously, the same report from the poll.
        **Its existing tests pass unedited** — that is the check.
- **Depends on:** Task 1

## [ ] Task 3: `carry_from_apply_id`
- **Files:** the apply service, the protocol, `apply/outcomes.py` if a merge
  helper is needed
- **Done when:**
  - [ ] `start_apply` accepts `carry_from_apply_id: str | None`, carried in the
        task payload.
  - [ ] The named record's categories are prepended to the finished report and the
        summary re-derived over the union, so `APPLY_ORDER`'s order survives
        (`script` is position 0).
  - [ ] A failed phase A carried into a clean phase B terminates `PARTIAL`.
  - [ ] A missing or foreign id is ignored, not fatal.
  - [ ] Phase A's own record is untouched by the carry.
- **Depends on:** Task 2

## [ ] Task 4: `materialised_constructs()`
- **Files:** the apply service and its protocol
- **Done when:**
  - [ ] The protocol declares `materialised_constructs() -> frozenset[ApplyConstruct]`
        as an `@abstractmethod`; the service returns
        `frozenset(build_materialisers(...).keys())` — the same registry
        `_orchestrator()` builds, not a second list.
  - [ ] A test asserts it equals `{script, mcp}` **today**, and that registering a
        stub materialiser widens it with no edit to the gate.
  - [ ] The docstring says why it is derived: a hand-written set drifts, and the
        drift is only observable as a failed apply on a bot that already exists.
- **Depends on:** —

---

## Group B — The creation seam

## [ ] Task 5: The creation preflight
- **Files:** `core/bot_config_manifest/creation.py` (new)
- **Done when:**
  - [ ] One function validates a document against an **engine type and bot type**
        (never a record) via `validate`, then refuses any **declared** construct
        absent from `materialised_constructs()`.
  - [ ] "Declared" is `declared_entries(parsed, construct) is not None` walked
        over `APPLY_ORDER`, so a declared-empty category counts — it removes,
        which is a write.
  - [ ] Every violation is reported in one pass, naming the construct and what
        would apply it.
  - [ ] The module docstring states why this is stricter than `PUT`.
- **Depends on:** Task 4

## [ ] Task 6: The creation seam object
- **Files:** `core/bot_config_manifest/creation.py`
- **Done when:**
  - [ ] Four operations: `preflight(engine, bot_type)`, `persist(entity_id, bot_id)`,
        `phase_a(...)` and `discard(entity_id, bot_id)`.
  - [ ] `persist` writes through the existing manifest service — same validation,
        same all-or-nothing, same storage key.
  - [ ] `phase_a` calls `start_apply(phases={PRE_CONTAINER}, trigger="create:pre_container")`
        with the creation attributes rather than a bot record, and **never
        raises** — a failure becomes a report.
  - [ ] `phase_a` runs even when the document declares no `script`; the record is
        what tells the job phase A is done. A test pins this — it looks like a
        no-op worth optimising away.
  - [ ] `discard` deletes the stored manifest **and any startup-script row phase A
        wrote**, idempotently.
- **Depends on:** Tasks 2, 5

## [ ] Task 7: `entity_id` is resolved once
- **Files:** `core/bot_config_manifest/creation.py`, `core/bot_management/create_flow.py`
- **Done when:**
  - [ ] The `entity_id` the manifest is stored under at submission is the value
        `create_bot` will resolve — taken from the **prepared** spec, after
        `_prepare_create`, not from the raw request.
  - [ ] The poll resolves the same value the same way, from the authenticated
        caller, never from a request parameter.
  - [ ] A test creates through the public surface and asserts the submitted row is
        found by the job's later read.
- **Depends on:** Task 6

---

## Group C — Creation, and the job that carries it

## [ ] Task 8: Submission, without inline creation
- **Files:** `core/bot_management/create_flow.py`
- **Done when:**
  - [ ] Submission runs policy, preflight (manifest **beside** quota/name/engine,
        **before** Passport), persist, then the Passport application — and
        **stops**. It does not take `create_bot_with_authorization`'s
        inline-create branch (`plan.md` K-9).
  - [ ] `complete_bot_authorization` and `bot_service.create_bot` are **not
        modified at all**.
  - [ ] Existing callers of `create_bot_with_authorization` behave exactly as
        today.
- **Depends on:** Task 6

## [ ] Task 9: The creation job handler
- **Files:** `core/bot_config_manifest/create_job.py` (new),
  `di/modules/bot_management_module.py`
- **Done when:**
  - [ ] A `TaskHandler` registered at bootstrap with `wake_on_enqueue`, its whole
        body inside `avernet_tenant_scope(payload["tenant"])`.
  - [ ] The step machine of `plan.md` §K-5: Passport pending → `Reschedule(5s)`;
        declined → `discard` then `Fail`; issued and phase A not done → start
        phase A and `Reschedule` until its record is terminal; phase A done and no
        bot → `complete_bot_authorization(...)`; container not up → `Reschedule`;
        then start phase B with `carry_from` and **`Complete` without waiting for
        it**.
  - [ ] Phase A's record is found with `last_apply(entity_id, bot_id)` and
        recognised by its `create:pre_container` trigger — the same read the poll
        makes, so no repository method is added.
  - [ ] **Every step is re-entrant.** Invoking the handler twice at any step does
        not create a second bot, start a second apply, or mint a second Passport
        application. A test drives each step twice.
  - [ ] The payload carries the creation attributes, the ids, the authorization
        handles and the tenant — everything the job and the poll need, since no
        request context exists at handler time.
- **Depends on:** Tasks 3, 6, 8

## [ ] Task 10: The deadline, and what happens at it
- **Files:** `create_job.py`, the config module
- **Done when:**
  - [ ] The deadline is configurable, default 600 s, passed as `deadline_seconds`
        so the queue enforces it DB-side.
  - [ ] A creation that reaches it is `TIMED_OUT`, and the poll reports
        `AUTHORIZATION_EXPIRED` — never `AUTHORIZATION_REJECTED`, which would
        report a decision the user never made.
  - [ ] The manifest **and any phase-A startup-script row** are deleted on every
        bot-less terminal, idempotently.
  - [ ] A test asserts neither row survives an abandoned creation. This is what
        replaces the feature switch.
- **Depends on:** Task 9

---

## Group D — The public surface

## [ ] Task 11: Request and response models
- **Files:** `adapters/http/openapi_v1/bots/schemas_create_with_manifest.py` (new)
- **Done when:**
  - [ ] The create body carries the manifest plus the same creation attributes the
        existing create body accepts.
  - [ ] The **poll has no body and no query parameters** — `bot_id` in the path is
        its whole input.
  - [ ] A `CreationState` enum with the seven states, and a response carrying the
        state, the authorization handles while awaiting, and — at both terminal
        states — the apply report **and the bot**.
- **Depends on:** —

## [ ] Task 12: The two routes
- **Files:** `adapters/http/openapi_v1/bots/create_with_manifest.py` (new),
  `adapters/http/openapi_v1/__init__.py`
- **Done when:**
  - [ ] `POST /openapi/v1/bots/with-manifest` returns `202` with `bot_id`,
        `AWAITING_AUTHORIZATION` and both handles, carrying the same bars as the
        existing create (refused to an application caller).
  - [ ] `GET /openapi/v1/bots/{bot_id}/with-manifest/status` answers `plan.md`
        §K-8 and **makes no external call** — no AgentPass query, no work started,
        nothing written. A test asserts the Passport plugin is never touched.
  - [ ] The provisioning-failure edge reports `FAILED` with a message naming
        provisioning, not the manifest.
  - [ ] The router is mounted where its `{bot_id}` literal cannot be captured by a
        wildcard group. **No feature switch.**
  - [ ] Route docstrings state: the manifest is submitted once and never
        re-submitted; iteration 1's rule that a `script` must not depend on
        anything else the same manifest declares; and that `FAILED` leaves a
        running bot.
- **Depends on:** Tasks 9, 11

---

## Group E — Proof

## [ ] Task 13: The ordering proof
- **Files:** `tests/community/bot_config_manifest/test_creation_ordering.py` (new)
- **Done when:**
  - [ ] Phase A completes **before creation is called at all** — asserted on
        recorded call order, not timing.
  - [ ] The startup-script row is present when the payload is composed, and a
        manifest with a `script` produces a first boot carrying it.
  - [ ] A phase A that fails still creates and provisions the bot, and the failure
        appears only in the report.
- **Depends on:** Task 9

## [ ] Task 14: Durability and re-entrancy
- **Files:** `tests/community/bot_config_manifest/test_apply_task.py` (new)
- **Done when:**
  - [ ] An apply whose handler is invoked twice converges: the second run writes
        nothing.
  - [ ] The lock is released on every path, including a raising orchestrator, and
        a task that never runs leaves a lock the TTL reaps.
  - [ ] The creation job invoked twice at each step does not double-act.
  - [ ] A comment or docstring near the re-entrancy tests states the reason —
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
  - [ ] A `PARTIAL` apply reports `FAILED`, the response **carries the bot**, and
        the bot record is untouched.
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
  - [ ] W13's row in both work-items documents records what shipped, including
        that applying moved onto the queue for every path.
- **Depends on:** Task 16
