# Tasks: Public API — One Collaborator Authorization Seam

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Sequencing: bases on `dev_refactory_collaboration`. Task 1 answers the one
> mechanical unknown in the plan (does a custom route class survive
> `include_router`?) before anything else is built on it — if it does not, the
> plan's named fallback changes Task 3 only, and nothing downstream.

## Task 1: Prove the attach mechanism  `[ ]`

- **Goal:** Settle whether `APIRouter(route_class=…)` survives assembly, before
  36 modules are edited on the assumption that it does.
- **Files:**
  `src/backend/tests/community/adapters/http/openapi_v1/test_authorization_inventory.py` (new)
- **Done when:**
  - [ ] A throwaway `APIRoute` subclass on one existing sub-router is still that
        subclass after `build_public_router()` assembles it, asserted by a test
        that survives as `test_every_route_is_a_public_api_route`.
  - [ ] A dependency appended inside that subclass is present in the assembled
        route's **effective** dependant, not merely in `route.dependencies`.
  - [ ] If either fails: `plan.md`'s *Risks* fallback is adopted — a post-build
        pass using `fastapi.dependencies.utils.get_parameterless_sub_dependant`
        — and Task 3's shape is rewritten to match. Recorded in `plan.md`
        either way, in one line.
- **Depends on:** —

## Task 2: The mode vocabulary and the table  `[ ]`

- **Goal:** One row per public operation, saying what that operation requires —
  including the rows that say "adjudicated elsewhere, here is where".
- **Files:**
  `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py` (new)
- **Done when:**
  - [ ] `Editors(level, mutates)`, `ServiceChecked(level, where)` and the
        `OWNER_SCOPED` / `SELF_CHECKED` / `NO_BOT` sentinels exist, each with a
        docstring saying what it means and what it costs to be wrong.
  - [ ] `AUTHORIZATION` covers **every** operation on the surface, exactly once,
        keyed `(method, path)` like `ADMISSION`.
  - [ ] Every row reflects **today's** behaviour, verified against the code it
        names: the `ServiceChecked` rows cite a real module path and the level
        that module really enforces; harness is the only `Editors` row.
  - [ ] The module docstring states the reversal explicitly — this table, not
        the route, is now where an operation's authorization is declared — and
        why (`plan.md` *Alternatives Considered*).
- **Depends on:** —

## Task 3: The route class and the build-time refusal  `[ ]`

- **Goal:** Make the table apply itself, and make an operation missing from it
  impossible to serve.
- **Files:**
  `…/openapi_v1/authorization.py`,
  `…/openapi_v1/__init__.py:387`,
  36 router modules under `…/openapi_v1/` (one-line `route_class=` each)
- **Done when:**
  - [ ] `PublicAPIRoute.__init__` looks the operation up and raises
        `PublicRouteNotAuthorized` — naming method and path — when it is absent.
  - [ ] An `Editors` row appends its dependency; every other row appends
        nothing.
  - [ ] All 36 `APIRouter(...)` constructions under `openapi_v1/` pass
        `route_class=PublicAPIRoute`.
  - [ ] `_assert_every_route_authorized(public)` runs at the end of
        `build_public_router` and fails on a route not built through the class,
        and on a table row matching no route.
  - [ ] The app still starts and every existing openapi_v1 test passes
        unchanged.
- **Depends on:** Tasks 1, 2

## Task 4: The seam  `[ ]`

- **Goal:** One place that resolves the bot, adjudicates the level, enforces the
  lock and writes the audit record — fail-closed at every step.
- **Files:**
  `…/openapi_v1/editors_gate.py` (new),
  `…/openapi_v1/errors.py`,
  `src/backend/src/agentclaw/community/adapters/http/app.py:659`
- **Done when:**
  - [ ] `require_editors(rule)` returns a `yield` dependency reading `bot_id`
        from the path and `owner_id` from `OwnerIdDep`, and **nothing else** —
        check and action read the same values by construction.
  - [ ] Level comes from `resolve_operable_permission_level`; an unresolvable
        bot, owner or level yields `NONE` and refuses. The interceptor's
        `permission_skipped` fail-open (`interceptor/collaborator.py:186`) is
        not ported, and a comment says so.
  - [ ] Below-level raises `EditorPermissionError` → 404, byte-identical to an
        absent bot.
  - [ ] `mutates=True` requires the edit lock **only when the bot has
        collaborators**, raising `EditLockRequiredError` → 423 naming the
        holder; reads never consult the lock.
  - [ ] A successful non-owner mutation writes one `ac_bot_collab_log` row; an
        owner's writes none; a write failure is swallowed and logged.
  - [ ] Level, lock and audit are three independent inputs — no setting
        silently disables another.
  - [ ] Both new error types have `app.py` handlers, because a
        dependency-raised error never reaches `@envelope_errors`
        (`errors.py:36`).
- **Depends on:** Task 2

## Task 5: Harness onto the seam  `[ ]`

- **Goal:** Close the one live hole — a check keyed on something other than what
  the handler acts on — by making harness an ordinary adjudicated group.
- **Files:**
  `…/openapi_v1/harness/router.py`,
  `…/openapi_v1/harness/schemas.py`
- **Done when:**
  - [ ] `require_harness_bot_access` and `HarnessBotAccessDep` are gone,
        including the `bot_id == "default"` early return and the
        `bot_repo.get_by_id` ownership resolve.
  - [ ] All six operations take `OwnerIdDep`; their table rows are `Editors` —
        reads at `MEMBER`, `apply` / `rollback` / `diagnose` mutating at
        `ADMIN`.
  - [ ] A body or query `entity_id` is accepted only when it equals the
        authorized owner; disagreement refuses, omission uses the authorized
        owner. Every `engine.*` / `_run_scan` call site passes the authorized
        value, not the request's.
  - [ ] `schemas.py` marks `entity_id` optional and documents the agreement
        rule.
- **Depends on:** Tasks 3, 4

## Task 6: Pin the contract  `[ ]`

- **Goal:** Make each property fail for a different, named mistake.
- **Files:**
  `…/tests/community/adapters/http/openapi_v1/test_authorization_inventory.py`,
  `…/test_editors_gate.py` (new),
  `…/harness/test_harness_authorization.py` (new),
  `…/test_authorization_is_inert.py` (new)
- **Done when:**
  - [ ] Inventory: every route has a row, no row lacks a route, every route is a
        `PublicAPIRoute`, an unlisted route fails to build, and `AUTHORIZATION`
        and `ADMISSION` cover the same operations.
  - [ ] Gate: owner passes every level; below-level is 404 not 403; an
        unresolvable bot refuses; a collaborator-lookup failure refuses; a
        lockless mutation is 423 naming the holder; a read never locks; a
        non-owner mutation audits once; an owner's audits none; an audit failure
        does not fail the request.
  - [ ] Harness: `default` no longer bypasses; a disagreeing `entity_id` is
        refused; a MEMBER cannot `apply`; another owner's bot is 404.
  - [ ] Inertness: a sweep asserts every non-harness operation answers
        status-for-status as it did before.
  - [ ] `test_admission_inventory.py`, `test_principal_seam.py` and
        `engine_runtime/test_operator_access.py` pass unmodified.
- **Depends on:** Task 5

## Task 7: Write the rule where the next author looks  `[ ]`

- **Goal:** Leave the convention findable, and the reversal recorded rather than
  discovered.
- **Files:**
  `src/backend/docs/openapi-v1/README.md`,
  `src/backend/specs/2026-08-21-openapi-v1-collaborator-authorization-seam/plan.md`
- **Done when:**
  - [ ] A section states the convention — `bot_id` on the path, `user_id` and
        `owner_id` on the query, authorization declared in `AUTHORIZATION` and
        never on a handler — and that omission is a startup failure.
  - [ ] The dated changelog gains an entry for the seam and for the harness
        correction, in the style of the 2026-08-09 entry.
  - [ ] #906 and #907 are annotated as now being table edits, with the row
        change spelled out.
  - [ ] The trade-off against "the decision is visible on the route that carries
        it" (`principal.py`, `admission.py`) is written down as a deliberate
        reversal.
- **Depends on:** Task 6

## Task 8: Tests & Verification  `[ ]`

- **Goal:** Ensure the feature meets every spec acceptance criterion.
- **Files:** —
- **Done when:**
  - [ ] Every `spec.md` acceptance criterion checks off, walked one by one.
  - [ ] `OCB_PRE_PUSH_RUN_CI=1 scripts/ci/pre_push.sh` passes for the backend
        module, per `AGENTS.md`.
  - [ ] The three live Open Questions in `spec.md` are either answered in the
        doc or restated as follow-ups; none is left implicitly decided by code.
  - [ ] PR opened per `AGENTS.md` *Pull Request Conventions* —
        `feat(openapi-v1): …` with Problem / Solution / Validation.
- **Depends on:** Task 7

---

## Groups

- **Group A — The declaration:** Tasks 1, 2
  - Theme: settle the attach mechanism, then write the table that records what
    the surface enforces today. Nothing is applied yet and nothing can regress.
- **Group B — The mechanism:** Tasks 3, 4
  - Theme: the table starts applying itself and the seam exists — the build now
    refuses an undeclared operation, while every group's answers stay as they
    were.
- **Group C — The first adopter:** Task 5
  - Theme: harness becomes an ordinary adjudicated group, which closes the live
    check-versus-act hole and proves the seam end to end.
- **Group D — Pins and docs:** Tasks 6, 7
  - Theme: each property gets a test that fails for its own mistake, and the
    convention plus the reversal are written where the next author reads.
- **Group E — Verification:** Task 8
  - Theme: final spec acceptance check and the module gates.
