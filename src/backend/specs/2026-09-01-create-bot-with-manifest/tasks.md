# Tasks: Creating a Bot With Its Configuration (W13)

Spec: `spec.md` · Plan: `plan.md` · Issue #1696.

Five groups. A→B→C are a chain (each needs the one before); D needs C; E proves
the lot. Nothing here adds a table or a column.

Conventions from `plan.md` that every task assumes:

- Phase A trigger is `create:pre_container`, phase B's is `create:on_container`.
- The materialiser gate is **derived from the registry**, never a written list.
- Nothing in a manifest may abort creation or touch the bot record.

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
        report and the summary is re-derived over the union, so `APPLY_ORDER`'s
        order survives (`script` is position 0).
  - [ ] A failed phase A carried into a clean phase B terminates `PARTIAL`.
  - [ ] A missing or foreign `carry_from_apply_id` is ignored, not fatal: it is a
        reporting nicety, and losing it must never fail an apply that worked.
  - [ ] Phase A's own record is untouched by the carry.
- **Depends on:** Task 2

---

## Group B — The creation seam

## [ ] Task 4: The creation preflight
- **Files:** `core/bot_config_manifest/creation.py` (new)
- **Done when:**
  - [ ] One function validates a document against an **engine type and bot type**
        (never a record) via the manifest service's `validate`, and then refuses
        any **declared** construct absent from `materialised_constructs()`.
  - [ ] "Declared" is `declared_entries(parsed, construct) is not None` walked
        over `APPLY_ORDER`, so a declared-empty category counts as declared —
        it removes, which is a write.
  - [ ] The refusal names the construct and what would apply it; every violation
        is reported in one pass, matching `PUT`'s all-or-nothing shape.
  - [ ] The module docstring states plainly why this is stricter than `PUT`:
        accepting here costs a Passport application, a user's click and a live
        bot before the failure appears.
- **Depends on:** Task 1

## [ ] Task 5: The creation seam object
- **Files:** `core/bot_config_manifest/creation.py`
- **Done when:**
  - [ ] A small service exposes exactly four operations: `preflight(engine, bot_type)`,
        `persist(entity_id, bot_id)`, `revalidate(entity_id, bot_id, engine, bot_type)`
        and `phase_a(bot)`.
  - [ ] `persist` writes through the existing manifest service — same validation,
        same all-or-nothing, same storage key. No new repository call.
  - [ ] `phase_a` calls `apply_now(phases={PRE_CONTAINER}, trigger="create:pre_container")`
        and **never raises**: it returns the report, and a failure is a report,
        not an exception.
  - [ ] `phase_a` runs even when the document declares no `script` — the empty
        record is the marker Task 9's listener keys on. A test pins this; it looks
        like a no-op worth optimising away, and removing it silently breaks
        phase B.
- **Depends on:** Tasks 2, 4

## [ ] Task 6: `entity_id` is resolved once
- **Files:** `core/bot_config_manifest/creation.py`, `core/bot_management/create_flow.py`
- **Done when:**
  - [ ] The `entity_id` the manifest is stored under in leg 1 is the value
        `create_bot` will resolve for the record — taken from the **prepared**
        spec, after `_prepare_create`, not from the raw request.
  - [ ] A test creates through the public surface and asserts the leg-1 row is
        found by the leg-2 read. A drifting second derivation stores a document
        nothing ever reads, and the apply reports success having applied nothing.
- **Depends on:** Task 5

---

## Group C — Wiring it into creation

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

## [ ] Task 8: `create_flow` calls the seam at its three points
- **Files:** `core/bot_management/create_flow.py`
- **Done when:**
  - [ ] Both `create_bot_with_authorization` and `complete_bot_authorization`
        take an optional creation-manifest seam.
  - [ ] Leg 1: preflight runs **beside quota/name/engine, before Passport is
        applied for**, and persist runs after preflight passes.
  - [ ] Leg 2: `revalidate` runs before `create_bot`, against the engine
        completion will actually use, and its failure creates nothing.
  - [ ] Both legs pass `pre_provision=seam.phase_a` into `create_bot`, so a
        creation completed inline (a Passport token returned immediately) gets
        phase A too — not only the polled path.
  - [ ] With no seam supplied, both functions behave exactly as today.
- **Depends on:** Tasks 5, 7

## [ ] Task 9: The phase-B listener
- **Files:** `core/bot_config_manifest/apply/create_listener.py` (new),
  `di/modules/bot_management_module.py`
- **Done when:**
  - [ ] A `LifecycleBase` participant subscribes to `DeviceActivatedEvent` in
        `startup()`, following `SkillSymlinkListener`'s shape (idempotent
        subscribe, bot resolved from the binding).
  - [ ] It acts only when the bot has a manifest **and** the latest apply record's
        trigger is `create:pre_container`.
  - [ ] It then calls `start_apply(phases={ON_CONTAINER}, trigger="create:on_container",
        carry_from_apply_id=<phase A's id>)`.
  - [ ] The tenant is bound at the `Thread(...)` construction site, inline, never
        as a decorator — the import-time capture trap is named in a comment.
  - [ ] It does nothing on: a restart activation, a bot with no manifest, a second
        activation after phase B has run, and a bot whose latest apply is explicit.
        Each is a test.
- **Depends on:** Tasks 3, 5

---

## Group D — The public surface

## [ ] Task 10: Request and response models
- **Files:** `adapters/http/openapi_v1/bots/schemas_create_with_manifest.py` (new)
- **Done when:**
  - [ ] The create body carries the manifest document plus the same creation
        attributes the existing create body accepts.
  - [ ] The poll body echoes the creation attributes — and **never** a manifest.
        A field for one would let a caller validate one document and apply
        another; the model is where that is made impossible.
  - [ ] A `CreationState` enum with exactly the six states, and a response
        carrying the state, the bot (once it exists), the authorization handles
        (while awaiting) and the apply report (at both terminal states).
- **Depends on:** —

## [ ] Task 11: The two routes and the switch
- **Files:** `adapters/http/openapi_v1/bots/create_with_manifest.py` (new),
  `adapters/http/openapi_v1/__init__.py`
- **Done when:**
  - [ ] `POST /openapi/v1/bots/with-manifest` returns `202` with `bot_id`,
        `AWAITING_AUTHORIZATION` and both handles; it carries the same bars as the
        existing create (refused to an application caller — creation spends the
        user's quota and no bot exists for a grant to cover).
  - [ ] `POST /openapi/v1/bots/{bot_id}/with-manifest/status` drives completion and
        answers the state table in `plan.md` §K-7, including the provisioning-failure
        edge reported as `FAILED` with a message naming provisioning.
  - [ ] Both are gated by `BOT_CONFIG_MANIFEST_CREATE_ENABLED`, read per request,
        default off, answering `404` when off. The comment carries §2.11's reason
        and names #1698 as the precondition for turning it on.
  - [ ] The router is mounted where its `{bot_id}` literal cannot be captured by a
        wildcard group.
  - [ ] Route docstrings state: the manifest is submitted once and never
        re-submitted; iteration 1's rule that a `script` must not depend on
        anything else the same manifest declares; and that `FAILED` leaves a
        running bot.
- **Depends on:** Tasks 8, 10

---

## Group E — Proof

## [ ] Task 12: The ordering proof
- **Files:** `tests/community/bot_config_manifest/test_creation_ordering.py` (new)
- **Done when:**
  - [ ] Phase A completes **before** provisioning is entered — asserted on
        recorded call order, not on timing.
  - [ ] The startup-script row is present when the payload is composed, and a
        manifest with a `script` produces a first boot carrying it.
  - [ ] A phase A that fails still creates and provisions the bot, and the failure
        appears only in the report.
- **Depends on:** Task 8

## [ ] Task 13: Tenancy
- **Files:** `tests/community/bot_config_manifest/test_creation_tenancy.py` (new)
- **Done when:**
  - [ ] The tenant observed inside phase A equals the request's tenant.
  - [ ] The tenant observed inside phase B — on the listener's thread, with no
        request behind it — equals the tenant the bot was created under.
  - [ ] A test asserts the wrap happens at the construction site (a
        module-level decorator would capture at import); the comment in the code
        points at this test.
- **Depends on:** Task 9

## [ ] Task 14: Endpoint tests
- **Files:** `tests/community/endpoints/test_openapi_create_with_manifest.py` (new)
- **Done when:**
  - [ ] Full flow through the app: submit → `202` → poll `AWAITING_AUTHORIZATION`
        → authorize → `CREATING` → `APPLYING` → `READY`, with the report carrying
        entries from **both** phases.
  - [ ] An invalid manifest is refused `422` with every violation named, and
        **Passport is never called** — asserted on the plugin, not inferred.
  - [ ] A manifest declaring a construct with no materialiser is refused at
        submission, naming it.
  - [ ] A `PARTIAL` apply reports `FAILED`, the bot is running, and the bot record
        is untouched.
  - [ ] `AUTHORIZATION_REJECTED` is terminal and creates nothing.
  - [ ] Creation with no manifest through this endpoint reports `READY`.
  - [ ] The switch off answers `404` on both routes.
- **Depends on:** Task 11

## [ ] Task 15: Nothing else moved
- **Files:** existing suites
- **Done when:**
  - [ ] Every existing create, auth-status, config-manifest, apply and
        startup-script test passes **unedited**.
  - [ ] A bot created by the existing endpoint and given a manifest by `PUT`
        still applies with no restart, by the same path as before.
- **Depends on:** Tasks 11, 9

## [ ] Task 16: Documentation
- **Files:** `core/bot_config_manifest/README.md`,
  `docs/bot-config-manifest/user-manual.zh-CN.md`,
  `docs/bot-config-manifest/work-items.zh-CN.md` (+ the English `work-items.md`)
- **Done when:**
  - [ ] The user manual documents the create-with-manifest flow, the six poll
        states, `PARTIAL → FAILED`, and that `FAILED` leaves a running bot.
  - [ ] The `script`-dependency rule is written where a manifest author will read
        it, marked as iteration 1 only and pointing at #1508.
  - [ ] The README's Context Boundary block lists the creation seam and the
        listener.
  - [ ] W13's row in both work-items documents records what shipped and what did
        not — the teclaw first-artifact guarantee stays W8's, and the endpoint is
        off until #1698.
- **Depends on:** Task 14
