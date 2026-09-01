# Tasks: Creating a Bot With Its Configuration (W13)

Spec: `spec.md` · Plan: `plan.md` · Issue #1696.

> **Revision 2** — reworked after review on PR #1791. The device-activation
> listener is gone; a task-queue job carries the creation. Task 9 changed
> completely, Tasks 11 and 13 changed, and Task 17 (cleanup) is new.

Five groups. A→B→C are a chain; D needs C; E proves the lot. Nothing here adds a
table or a column.

Conventions from `plan.md` that every task assumes:

- Phase A trigger is `create:pre_container`, phase B's is `create:on_container`.
- The materialiser gate is **derived from the registry**, never a written list.
- Nothing in a manifest may abort creation or touch the bot record.
- The handler is re-entrant: every step asks "is this already done?".

---

## Group A — What the apply service still owes W13

## [ ] Task 1: `materialised_constructs()` on the apply service
- **Files:** `core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py`,
  `core/bot_config_manifest/services/config_manifest_apply_service.py`
- **Done when:**
  - [ ] The protocol declares `materialised_constructs() -> frozenset[ApplyConstruct]`
        as an `@abstractmethod`, and the service returns
        `frozenset(build_materialisers(...).keys())` — the same registry
        `_orchestrator()` builds, not a second list.
  - [ ] A test asserts it equals `{script, mcp}` **today** and that registering a
        stub materialiser for another construct widens it with no edit to the
        gate — the property W5/W6 depend on.
  - [ ] The docstring says why it is derived: a hand-written set drifts, and the
        drift is only observable as a failed apply on a bot that already exists.
- **Depends on:** —

## [ ] Task 2: `apply_now` — the same lifecycle without the thread
- **Files:** `core/bot_config_manifest/services/config_manifest_apply_service.py`,
  the protocol
- **Done when:**
  - [ ] `apply_now(...)` takes what `start_apply` takes and returns the finished
        `ApplyReport`.
  - [ ] Lock → validate → `RUNNING` → run → `finish` → release, in that order,
        sharing `start_apply`'s helpers rather than repeating them. The lock is
        released on every path, including a raising orchestrator.
  - [ ] A test asserts no thread is started and the record is terminal by the
        time it returns.
  - [ ] The docstring names the caller and the reason: creation must not proceed
        to provisioning before `script` is written, and a thread cannot promise
        that.
- **Depends on:** —

## [ ] Task 3: `carry_from_apply_id` — one story from two applies
- **Files:** same as Task 2, `apply/outcomes.py` if a merge helper is needed
- **Done when:**
  - [ ] `start_apply` and `apply_now` accept `carry_from_apply_id: str | None`.
  - [ ] When set, the named record's categories are prepended to the finished
        report and the summary re-derived over the union, so `APPLY_ORDER`'s
        order survives (`script` is position 0).
  - [ ] A failed phase A carried into a clean phase B terminates `PARTIAL`.
  - [ ] A missing or foreign id is ignored, not fatal: losing a reporting nicety
        must never fail an apply that worked.
  - [ ] Phase A's own record is untouched by the carry.
- **Depends on:** Task 2

---

## Group B — The creation seam

## [ ] Task 4: The creation preflight
- **Files:** `core/bot_config_manifest/creation.py` (new)
- **Done when:**
  - [ ] One function validates a document against an **engine type and bot type**
        (never a record) via the manifest service's `validate`, then refuses any
        **declared** construct absent from `materialised_constructs()`.
  - [ ] "Declared" is `declared_entries(parsed, construct) is not None` walked
        over `APPLY_ORDER`, so a declared-empty category counts as declared — it
        removes, which is a write.
  - [ ] The refusal names the construct and what would apply it; every violation
        is reported in one pass, matching `PUT`'s all-or-nothing shape.
  - [ ] The module docstring states why this is stricter than `PUT`: accepting
        here costs a Passport application, a user's click and a live bot before
        the failure appears.
- **Depends on:** Task 1

## [ ] Task 5: The creation seam object
- **Files:** `core/bot_config_manifest/creation.py`
- **Done when:**
  - [ ] A small service exposes four operations: `preflight(engine, bot_type)`,
        `persist(entity_id, bot_id)`, `phase_a(bot)` and `discard(entity_id, bot_id)`.
  - [ ] `persist` writes through the existing manifest service — same validation,
        same all-or-nothing, same storage key. No new repository call.
  - [ ] `phase_a` calls `apply_now(phases={PRE_CONTAINER}, trigger="create:pre_container")`
        and **never raises**: it returns the report, and a failure is a report,
        not an exception.
  - [ ] `phase_a` runs even when the document declares no `script` — the record is
        what tells the poll and the handler that phase A is done. A test pins
        this; it looks like a no-op worth optimising away.
  - [ ] `discard` deletes the stored manifest, idempotently.
- **Depends on:** Tasks 2, 4

## [ ] Task 6: `entity_id` is resolved once
- **Files:** `core/bot_config_manifest/creation.py`, `core/bot_management/create_flow.py`
- **Done when:**
  - [ ] The `entity_id` the manifest is stored under at submission is the value
        `create_bot` will resolve for the record — taken from the **prepared**
        spec, after `_prepare_create`, not from the raw request.
  - [ ] The poll resolves the same value the same way, from the authenticated
        caller, never from a request parameter.
  - [ ] A test creates through the public surface and asserts the submitted row is
        found by the job's later read. A drifting second derivation stores a
        document nothing ever reads, and the apply then reports success having
        applied nothing.
- **Depends on:** Task 5

---

## Group C — Creation, and the job that carries it

## [ ] Task 7: The `pre_provision` seam in `create_bot`
- **Files:** `core/bot_management/services/bot_service.py`
- **Done when:**
  - [ ] `create_bot` takes keyword-only `pre_provision: Callable[[dict], None] | None = None`.
  - [ ] It is called **once**, after the row (and any template) exists and before
        every provisioning branch, with the bot record.
  - [ ] It is wrapped: an exception is logged and creation continues. The comment
        says why — a manifest-layer failure must not abort creation or leave a
        half-created bot (§2.7).
  - [ ] Not called at all when `None`; every existing caller is untouched.
  - [ ] The docstring states the contract the whole item rests on: this runs
        before the start command is composed.
- **Depends on:** —

## [ ] Task 8: `create_flow` calls the seam at submission
- **Files:** `core/bot_management/create_flow.py`
- **Done when:**
  - [ ] `create_bot_with_authorization` takes an optional creation-manifest seam:
        preflight runs **beside quota/name/engine, before Passport is applied
        for**, and persist runs after preflight passes.
  - [ ] `complete_bot_authorization` takes the seam too and passes
        `pre_provision=seam.phase_a` into `create_bot`, so a creation completed
        inline (a Passport token returned immediately) gets phase A as well.
  - [ ] With no seam supplied, both functions behave exactly as today.
- **Depends on:** Tasks 5, 7

## [ ] Task 9: The creation job handler
- **Files:** `core/bot_config_manifest/create_job.py` (new),
  `di/modules/bot_management_module.py`
- **Done when:**
  - [ ] A `TaskHandler` (a `task_type` plus `handle(payload) -> TaskOutcome`)
        registered into the `HandlerRegistry` at bootstrap, with
        `wake_on_enqueue` so a submission starts it immediately.
  - [ ] Its whole body runs inside `avernet_tenant_scope(payload["tenant"])`.
  - [ ] The step machine of `plan.md` §K-3: Passport pending → `Reschedule(5s)`;
        declined → `discard` then `Fail`; issued and no bot → create with
        `pre_provision=phase_a`; container not up → `Reschedule`; phase B not
        started → `start_apply(ON_CONTAINER, carry_from=<phase A id>)`; running →
        `Reschedule`; terminal → `Complete`.
  - [ ] **Every step is re-entrant.** Invoking the handler twice at any step does
        not create a second bot, start a second apply, or mint a second Passport
        application. A test drives each step twice.
  - [ ] The payload carries the creation attributes, the ids, and the tenant —
        everything the handler needs, since no request context exists at handler
        time.
- **Depends on:** Tasks 3, 5, 8

## [ ] Task 10: The deadline, and what happens at it
- **Files:** `core/bot_config_manifest/create_job.py`, the config module
- **Done when:**
  - [ ] The deadline is configurable with a default of 600 s, passed as
        `deadline_seconds` at enqueue, so the queue enforces it DB-side.
  - [ ] A creation that reaches it is `TIMED_OUT`, and the poll reports
        `AUTHORIZATION_EXPIRED` — never `AUTHORIZATION_REJECTED`, which would
        report a decision the user never made.
  - [ ] The stored manifest is deleted on every bot-less terminal — declined and
        timed out alike — and the delete is idempotent.
  - [ ] A test asserts no manifest row survives an abandoned creation. This is
        what replaces the feature switch.
- **Depends on:** Task 9

---

## Group D — The public surface

## [ ] Task 11: Request and response models
- **Files:** `adapters/http/openapi_v1/bots/schemas_create_with_manifest.py` (new)
- **Done when:**
  - [ ] The create body carries the manifest document plus the same creation
        attributes the existing create body accepts.
  - [ ] The **poll has no body and no query parameters** — `bot_id` in the path is
        the whole of its input. There is nothing for a caller to re-send, which is
        the property that makes "the validated manifest is the applied manifest"
        structural rather than promised.
  - [ ] A `CreationState` enum with the seven states, and a response carrying the
        state, the authorization handles (while awaiting), and — at both terminal
        states — the apply report **and the bot**, so `FAILED` can never read as
        "no bot was created".
- **Depends on:** —

## [ ] Task 12: The two routes
- **Files:** `adapters/http/openapi_v1/bots/create_with_manifest.py` (new),
  `adapters/http/openapi_v1/__init__.py`
- **Done when:**
  - [ ] `POST /openapi/v1/bots/with-manifest` returns `202` with `bot_id`,
        `AWAITING_AUTHORIZATION` and both handles; it carries the same bars as the
        existing create (refused to an application caller — creation spends the
        user's quota and no bot exists for a grant to cover).
  - [ ] `GET /openapi/v1/bots/{bot_id}/with-manifest/status` answers the state
        table in `plan.md` §K-7, including the provisioning-failure edge reported
        as `FAILED` with a message naming provisioning. A `GET`, because it
        observes — the job drives.
  - [ ] The router is mounted where its `{bot_id}` literal cannot be captured by a
        wildcard group. **No feature switch** (spec D-7).
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
  - [ ] Phase A completes **before** provisioning is entered — asserted on
        recorded call order, not on timing.
  - [ ] The startup-script row is present when the payload is composed, and a
        manifest with a `script` produces a first boot carrying it.
  - [ ] A phase A that fails still creates and provisions the bot, and the failure
        appears only in the report.
- **Depends on:** Task 8

## [ ] Task 14: Tenancy
- **Files:** `tests/community/bot_config_manifest/test_creation_tenancy.py` (new)
- **Done when:**
  - [ ] The tenant observed inside phase A and inside the handler equals the
        tenant of the submitting request.
  - [ ] A handler invoked with a payload whose tenant differs from the process
        default reads and writes under the payload's tenant — the test that would
        pass by accident if the scope were dropped is written so it *fails*
        instead, since `get_current_avernet_tenant()` returns the default rather
        than raising.
- **Depends on:** Task 9

## [ ] Task 15: Endpoint tests
- **Files:** `tests/community/endpoints/test_openapi_create_with_manifest.py` (new)
- **Done when:**
  - [ ] Full flow through the app: submit → `202` → poll `AWAITING_AUTHORIZATION`
        → authorize → `CREATING` → `APPLYING` → `READY`, with the report carrying
        entries from **both** phases.
  - [ ] An invalid manifest is refused `422` with every violation named, and
        **Passport is never called** — asserted on the plugin, not inferred.
  - [ ] A manifest declaring a construct with no materialiser is refused at
        submission, naming it.
  - [ ] A `PARTIAL` apply reports `FAILED`, the response **carries the bot**, and
        the bot record is untouched.
  - [ ] `AUTHORIZATION_REJECTED` and `AUTHORIZATION_EXPIRED` are terminal, create
        nothing, and leave no manifest row.
  - [ ] Creation with no manifest through this endpoint reports `READY`.
- **Depends on:** Task 12

## [ ] Task 16: Nothing else moved
- **Files:** existing suites
- **Done when:**
  - [ ] Every existing create, auth-status, config-manifest, apply and
        startup-script test passes **unedited**.
  - [ ] A bot created by the existing endpoint and given a manifest by `PUT` still
        applies with no restart, by the same path as before.
- **Depends on:** Tasks 9, 12

## [ ] Task 17: Documentation
- **Files:** `core/bot_config_manifest/README.md`,
  `docs/bot-config-manifest/user-manual.zh-CN.md`,
  `docs/bot-config-manifest/work-items.zh-CN.md` (+ the English `work-items.md`)
- **Done when:**
  - [ ] The user manual documents the create-with-manifest flow, the poll states,
        the deadline, `PARTIAL → FAILED`, and that `FAILED` leaves a running bot.
  - [ ] The `script`-dependency rule is written where a manifest author will read
        it, marked as iteration 1 only and pointing at #1508.
  - [ ] The README's Context Boundary block lists the creation seam and the job.
  - [ ] W13's row in both work-items documents records what shipped: the job and
        its deadline, the retirement of the feature switch (with #1698 no longer
        its gate), and that the teclaw first-artifact guarantee stays W8's.
- **Depends on:** Task 15
