# Tasks: Public API — One Collaborator Authorization Seam

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Sequencing: bases on `dev_refactory_collaboration`. Task 1 answers the one
> mechanical unknown in the plan (does a custom route class survive
> `include_router`?) before anything else is built on it — if it does not, the
> plan's named fallback changes Task 3 only, and nothing downstream.
>
> Scope note: no edit lock in the seam (`spec.md` *Decisions* 1) and no harness
> fix (`spec.md` *Out of Scope*). Every table row in this change records
> today's behaviour; nothing adopts the seam yet.

## Task 1: Prove the attach mechanism  `[x]`

- **Goal:** Settle whether `APIRouter(route_class=…)` survives assembly, before
  36 modules are edited on the assumption that it does.
- **Files:**
  `src/backend/tests/community/adapters/http/openapi_v1/test_authorization_inventory.py` (new)
- **Done when:**
  - [x] A custom `APIRoute` subclass on a sub-router is still that subclass
        after assembly — `test_route_class_survives_include_router`. FastAPI
        0.138.2 defers `include_router` into a lazy wrapper, so routes are
        walked through `effective_route_contexts()`; the shared walker lives in
        `tests/…/openapi_v1/_route_walk.py`.
  - [x] A dependency appended inside that subclass is in the assembled route's
        **effective** dependant, not merely in `route.dependencies`, runs on a
        real request, and publishes its query parameter into the schema.
  - [x] Neither failed, so the fallback is **not** adopted. Recorded in
        `plan.md` *Risks*, together with the one correction the probe produced:
        `PublicAPIRoute` raises at module import, earlier than
        `build_public_router()`.
- **Depends on:** —

## Task 2: The mode vocabulary and the table  `[x]`

- **Goal:** One row per public operation, saying what that operation requires —
  including the rows that say "adjudicated elsewhere, here is where".
- **Files:**
  `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py` (new)
- **Done when:**
  - [x] `Check(level)`, `ServiceChecked(level, where)` and the `NoCheck(reason)`, and the
        `OWNER_SCOPED` / `SELF_CHECKED` scaffolding sentinels exist, each with a docstring
        saying what it means and what it costs to be wrong. No `mutates` field.
  - [x] `AUTHORIZATION` covers **every** operation on the surface, exactly once,
        keyed `(method, path)` like `ADMISSION`.
  - [x] Every row reflects **today's** behaviour, verified against the code it
        names — harness included, defect and all. No row is `Check` in this
        change.
  - [x] Each `ServiceChecked.where` is an importable module path that really
        contains a permission call.
  - [x] The module docstring names `Check` / `NoCheck` as the permanent
        vocabulary and `ServiceChecked` / `OWNER_SCOPED` / `SELF_CHECKED` as
        scaffolding, each with the row change that retires it (`spec.md` *The
        Final Shape*).
  - [x] A `scaffolding_row_count()` helper reports how many rows still sit in
        the scaffolding modes, so the migration's remaining distance is a
        number a follow-up session can watch go to zero.
  - [x] The module docstring states the reversal explicitly — this table, not
        the route, is now where an operation's authorization is declared — and
        why (`plan.md` *Alternatives Considered*).
- **Depends on:** —

## Task 3: The route class and the assembly-time refusal  `[x]`

- **Goal:** Make the table apply itself, and make an operation missing from it
  impossible to serve.
- **Files:**
  `…/openapi_v1/authorization.py`,
  `…/openapi_v1/__init__.py:387`,
  36 router modules under `…/openapi_v1/` (one-line `route_class=` each)
- **Done when:**
  - [x] `PublicAPIRoute.__init__` looks the operation up and raises
        `PublicRouteNotAuthorized` — naming method and path — when it is absent.
  - [x] A `Check` row appends its dependency; every other row appends
        nothing.
  - [x] All 36 `APIRouter(...)` constructions under `openapi_v1/` pass
        `route_class=PublicAPIRoute`.
  - [x] `_assert_every_route_authorized(public)` runs at the end of
        `build_public_router` and raises on a route not built through the class,
        and on a table row matching no route — so the **app does not start**,
        rather than a test merely failing.
  - [x] The app still starts and every existing openapi_v1 test passes
        unchanged.
- **Depends on:** Tasks 1, 2

## Task 4: The seam  `[x]`

- **Goal:** One place that resolves the bot, adjudicates the level and writes
  the audit record — fail-closed on every check.
- **Files:**
  `…/openapi_v1/bot_access.py` (new),
  `…/openapi_v1/errors.py`,
  `src/backend/src/agentclaw/community/adapters/http/app.py:659`
- **Done when:**
  - [x] `require_check(rule)` returns a `yield` dependency reading `bot_id`
        from the path and `owner_id` from `OwnerIdDep`, and **nothing else** —
        check and action read the same values by construction.
  - [x] Level comes from `resolve_operable_permission_level`; an unresolvable
        bot, owner or level yields `NONE` and refuses. The interceptor's
        `permission_skipped` fail-open (`interceptor/collaborator.py:186`) is
        not ported, and a comment says so.
  - [x] Below-level raises `BotAccessRefusedError` → 404, byte-identical to an
        absent bot.
  - [x] A successful non-owner action on a non-read method writes one
        `ac_bot_collab_log` row; a read writes none; an owner's writes none.
  - [x] An audit write failure logs at error level with bot, owner, actor and
        route, and does not fail the request (`spec.md` *Decisions* 2).
  - [x] The module imports no lock service and calls none — asserted by
        `test_gate_never_touches_the_lock_service`.
  - [x] `BotAccessRefusedError` has an `app.py` handler, because a
        dependency-raised error never reaches `@envelope_errors`
        (`errors.py:36`). No lock error type is added.
- **Note:** the gate pins listed under Task 5 landed here, with the code
  they pin, rather than a task later — untested enforcement is not worth
  committing. Task 5 keeps the inventory and inertness sweeps.
- **Depends on:** Task 2

## Task 5: Pin the contract  `[x]`

- **Goal:** Make each property fail for a different, named mistake.
- **Files:**
  `…/tests/community/adapters/http/openapi_v1/test_authorization_inventory.py`,
  `…/test_bot_access.py` (new),
  `…/test_authorization_is_inert.py` (new)
- **Done when:**
  - [x] Inventory: every route has a row, no row lacks a route, every route is a
        `PublicAPIRoute`, an unlisted route fails assembly, every
        `ServiceChecked` row cites a real enforcer, every `NoCheck` row carries
        a non-empty reason, and `AUTHORIZATION` and `ADMISSION` cover the same
        operations.
  - [x] `test_scaffolding_burn_down_is_reported` asserts the scaffolding count
        is published and matches the table — the test that goes to zero when the
        final shape is reached, and that will fail loudly if a *new* row is ever
        added in a scaffolding mode.
  - [x] Gate, over a fixture router declaring `Check` rows so the path is
        exercised despite having no shipped caller: owner passes every level;
        below-level is 404 not 403; an unresolvable bot refuses; a
        collaborator-lookup failure refuses; a non-owner write audits once; a
        read audits none; an owner's write audits none; an audit failure logs
        and does not fail the request; the lock service is never touched.
  - [x] Inertness: a sweep asserts every operation answers status-for-status as
        it did before, and that the service-level edit locks in channels and
        service publications still fire.
  - [x] `test_admission_inventory.py`, `test_principal_seam.py` and
        `engine_runtime/test_operator_access.py` pass unmodified.
- **Depends on:** Tasks 3, 4

## Task 6: Write the rule where the next author looks  `[ ]`

- **Goal:** Leave the convention findable, and the reversal recorded rather than
  discovered.
- **Files:**
  `src/backend/docs/openapi-v1/README.md`,
  `src/backend/specs/2026-08-21-openapi-v1-collaborator-authorization-seam/plan.md`
- **Done when:**
  - [ ] A section states the convention — `bot_id` on the path, `user_id` and
        `owner_id` on the query, authorization declared in `AUTHORIZATION` and
        never on a handler — and that omission stops the app from starting.
  - [ ] The dated changelog gains an entry for the seam, in the style of the
        2026-08-09 entry, saying plainly that it is inert on arrival.
  - [ ] #906 and #907 are annotated as now being table edits, with the row
        change spelled out (`OWNER_SCOPED` → `Check(...)`).
  - [ ] The two deferrals are recorded where they will be found: no edit lock in
        the seam, and the harness defect as that group's own change.
  - [ ] The trade-off against "the decision is visible on the route that carries
        it" (`principal.py`, `admission.py`) is written down as a deliberate
        reversal.
- **Depends on:** Task 5

## Task 7: Tests & Verification  `[ ]`

- **Goal:** Ensure the feature meets every spec acceptance criterion.
- **Files:** —
- **Done when:**
  - [ ] Every `spec.md` acceptance criterion checks off, walked one by one.
  - [ ] `OCB_PRE_PUSH_RUN_CI=1 scripts/ci/pre_push.sh` passes for the backend
        module, per `AGENTS.md`.
  - [ ] `spec.md` *Follow-ups* still names every deferred piece, and nothing
        deferred was quietly decided in code instead.
  - [ ] PR #1323 body is updated to the trimmed scope and marked ready for
        review, per `AGENTS.md` *Pull Request Conventions*.
- **Depends on:** Task 6

---

## Groups

- **Group A — The declaration:** Tasks 1, 2
  - Theme: settle the attach mechanism, then write the table that records what
    the surface enforces today. Nothing is applied yet and nothing can regress.
- **Group B — The mechanism:** Tasks 3, 4
  - Theme: the table starts applying itself and the seam exists — assembly now
    refuses an undeclared operation, while every group's answers stay as they
    were.
- **Group C — Pins and docs:** Tasks 5, 6
  - Theme: each property gets a test that fails for its own mistake — including
    the seam's own path, which has no shipped caller yet — and the convention
    plus the two deferrals are written where the next author reads.
- **Group D — Verification:** Task 7
  - Theme: final spec acceptance check and the module gates.
