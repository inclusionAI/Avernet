# Tasks: Public API — Operate Shared Bots and Published Stages

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Sequencing: the explicit-user-id change
> (`specs/2026-08-08-openapi-v1-explicit-user-id/`, PR #902) merged to `dev`
> as `5cdb614`; implementation bases on `dev`. Note the handlers there bind
> `UserIdDep` to a local named `owner_id` — Task 3 renames it.

## Task 1: Replace the shared-bot refusal with the operator adjudication  `[x]`

- **Goal:** One place that answers "may this caller operate that bot?" —
  owner, or collaborator ≥ MEMBER; anyone else gets the masked 404.
- **Files:**
  `src/backend/src/agentclaw/community/core/engine_runtime/relay.py`,
  `…/core/engine_runtime/gate.py`,
  `…/core/engine_runtime/models.py`,
  `…/core/engine_runtime/sharing.py` (retired),
  `src/backend/src/agentclaw/community/api/engine_runtime_service.py`
- **Done when:**
  - [x] `resolve_bot` / `resolve_bot_off_loop` take `caller_id`; when it is
        not the resolved owner, the caller's collaborator level is read from
        the repository the relay already holds, and below `MEMBER` raises
        `BotNotFoundError` — the same type, same public body, as an absent
        bot. Both ids are on a `logger.warning` line at the refusal.
  - [x] A collaborator-lookup failure logs and refuses (level `NONE`) — the
        fail-closed direction `bot_is_shared` pins today.
  - [x] `require_operable_bot` keeps only the bot-type allowlist; `is_shared`
        leaves its signature; the module docstring is rewritten — including
        correcting the `publish_bot_id` naming-scheme claim to the real
        separation (`ac_bots.binding_id` vs `ac_bot_publish.ext.binding`).
  - [x] `sharing.py` and `BotFacts.is_shared` are gone; `BotFacts` carries the
        resolved `owner_id`.
  - [x] `EngineRuntimeRelayProtocol` mirrors the new signatures and
        `tests/community/architecture/test_service_api_conformance.py` passes.
- **Depends on:** —

## Task 2: Stage-aware device resolution  `[x]`

- **Goal:** `stage` replaces `draft_device`; verify and online resolve their
  publish-record bindings; a dead stage refuses with its own error.
- **Files:**
  `…/core/engine_runtime/relay.py`,
  `…/core/engine_runtime/errors.py`,
  a small shared stage→binding helper in `…/core/engine_runtime/`,
  `…/api/engine_runtime_service.py`,
  `…/adapters/http/openapi_v1/responses.py`
- **Done when:**
  - [x] `call(..., stage: str)` replaces `draft_device: bool`. Review
        refinement: `stage` is **required with no default**, so the stage a
        handler gated on and the stage it forwards to cannot silently
        diverge; every call site states its stage explicitly.
  - [x] `online` resolves the newest publish record at `SUCCESS` via
        `ext.binding["online"]`; `verify` the newest at `VALIDATING` — or,
        when nothing validates, the newest `SUCCESS` record's **retained**
        verify binding while it is ACTIVE (cron's
        `_get_retained_verify_publish_record` rule, so the two surfaces
        agree on whether a runtime exists); both through
        `resolve_for_binding_invoke`, keyed on `facts.bot_pk`. Superseded
        statuses (`upgraded`, `released`, `failed`) do not resolve.
  - [x] A stage with no live runtime — including `verify`/`online` on a
        personal bot, and any unknown stage string from a programmatic
        caller — raises `EngineStageNotLiveError`, mapped to
        `(409, "No live runtime at the requested stage")` in
        `ENVELOPE_ERRORS`, and never falls back to another stage's binding.
  - [~] The stage→binding lookup lives in one helper
        (`core/engine_runtime/stage.py`) the relay calls; the connection
        service's side lands with Task 4.
- **Depends on:** Task 1

## Task 3: The parameters, on all fifteen HTTP operations  `[ ]`

- **Goal:** Every engine-runtime HTTP handler takes `owner_id` and `stage`
  from the query string and passes them through the seam.
- **Files:**
  `…/adapters/http/openapi_v1/engine_runtime/params.py` (new),
  `…/adapters/http/openapi_v1/engine_runtime/enums.py`,
  `…/adapters/http/openapi_v1/engine_runtime/gating.py`,
  `…/adapters/http/openapi_v1/engine_runtime/{sessions,engine,models,approvals}/router.py`,
  `…/adapters/http/openapi_v1/contracts.py`
- **Done when:**
  - [ ] `OwnerIdDep` (defaults to the request's `user_id`) and the `stage`
        parameter (new `RuntimeStage` documented enum: `draft` / `verify` /
        `online`, default `draft`) are defined once in `params.py` /
        `enums.py` and imported by every router — no per-router respelling.
  - [ ] `resolve_operable_bot` passes caller, owner and stage; all fifteen
        handlers forward with `stage=…` and none passes `draft_device`.
  - [ ] The `owner_id: UserIdDep` locals inherited from #902 are renamed
        `user_id`, so the dep's value (the caller) is never passed as the
        owner; the owner comes only from `OwnerIdDep`.
  - [ ] The 409 is documented on the engine-runtime groups (route- or
        group-level per how `ERROR_RESPONSES` carries 409 today — verify,
        then follow the existing pattern in `contracts.py`).
  - [ ] A request naming neither parameter is byte-for-byte today's behavior
        on every route.
  - [ ] `ruff check --select E,F,W` is clean on the package.
- **Depends on:** Task 2

## Task 4: The connection endpoint  `[ ]`

- **Goal:** The operator socket obeys the same adjudication and stage
  addressing as the HTTP groups.
- **Files:**
  `…/core/engine_runtime/connection.py`,
  `…/adapters/http/openapi_v1/engine_runtime/connection/router.py`
- **Done when:**
  - [ ] `build` takes caller, owner and stage; adjudicates with the Task 1
        rule before any binding or device work; resolves `verify`/`online`
        bindings through the Task 2 helper and `draft` through
        `get_active_by_bot_and_owner` as today.
  - [ ] `OperatorContext.staff_id` is the caller. A test pins that a public
        bot's non-collaborator is refused *before* `DeviceService` is
        consulted — its wider internal model must not widen this surface.
  - [ ] The router passes the new parameters; the connection service's
        docstrings drop the `publish_bot_id` naming-scheme claim (same
        correction as Task 1).
- **Depends on:** Task 2

## Task 5: Bring the existing suite to the widened contract  `[ ]`

- **Goal:** Every test that pins today's refusals flips deliberately, none
  silently.
- **Files:**
  `src/backend/tests/community/core/engine_runtime/test_connection.py`,
  `src/backend/tests/community/core/engine_runtime/test_relay.py`,
  `src/backend/tests/community/adapters/http/openapi_v1/engine_runtime/{test_sessions,test_engine_models,test_approvals,test_tenant_isolation}.py`,
  `…/engine_runtime/conftest.py`
- **Done when:**
  - [ ] The shared/public/collaborated 501 pins become served-to-operator /
        masked-404-to-stranger pins; the unknown-bot-type and fail-closed
        refusals stay refusals.
  - [ ] The `draft_device is True` forward assertions become
        `stage == "draft"` assertions; the `bot_is_shared` suite retires with
        `sharing.py`.
  - [ ] The `FakeRelay` harness models collaborator level and stage, keeping
        the `attempts` vs `calls` split so "no device was touched" stays a
        real assertion.
  - [ ] The stale "Only sessions is personal-only" docstring at
        `test_engine_models.py:202` is corrected.
  - [ ] `pytest tests/community/adapters/http/openapi_v1 tests/community/core/engine_runtime` is green.
- **Depends on:** Tasks 3, 4

## Task 6: Pin the widened contract  `[ ]`

- **Goal:** The adjudication matrix, the stage matrix, and the document-level
  convention each fail the build if broken later.
- **Files:**
  `src/backend/tests/community/adapters/http/openapi_v1/engine_runtime/test_operator_access.py` (new),
  `…/engine_runtime/test_stage_addressing.py` (new)
- **Done when:**
  - [ ] Operator matrix: owner and MEMBER+ served on all sixteen operations;
        below-member, stranger, and absent-bot answers are byte-identical;
        public visibility grants nothing; refusals log both ids.
  - [ ] Stage matrix: default-draft byte-compat, online↔`SUCCESS`,
        verify↔`VALIDATING` only, superseded records refuse, personal+stage
        is 409, no cross-stage fallback, socket and sessions address the same
        binding for the same (bot, stage), unknown stage value is 422.
  - [ ] Document-level, in the shape of `test_explicit_user_id.py`:
        `owner_id` and `stage` are optional query parameters on exactly the
        sixteen engine-runtime operations, the enum publishes three values,
        and the 409 is documented on exactly those operations.
- **Depends on:** Task 5

## Task 7: Write the rule where the next author will find it  `[ ]`

- **Goal:** The docs say who may operate what, at which stage, and what an
  operator sees — before an integrator discovers it.
- **Files:**
  `src/backend/docs/openapi-v1/README.md` + `README.zh-CN.md`,
  `src/backend/docs/openapi-v1/engine-surface.md` + `engine-surface.zh-CN.md`
- **Done when:**
  - [ ] The operator rule (owner or MEMBER+; public grants nothing; masked
        404), the stage addressing (parameters, defaults, the 409), and the
        device-wide exposure statement are in the README beside the
        explicit-user-id section, with the rejected alternatives recorded in
        one short paragraph.
  - [ ] `engine-surface.md`'s "personal bots only" ruling and its
        published-binding notes carry dated amendments; the multi-instance
        caveat (a stage answer describes the addressed instance) is stated.
  - [ ] The changelog gains this change's entry **and** a retroactive entry
        for the undocumented draft-service widening (PR #880), so the
        history is whole; the status board moves; both zh-CN mirrors match.
  - [ ] The follow-ups the spec names (collaborator access to data
        categories, routines' stage pin, `owner_entity_id` reconciliation)
        are listed as deferred items, not lost.
- **Depends on:** Task 6

## Task 8: Tests & Verification  `[ ]`

- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** —
- **Done when:**
  - [ ] Every acceptance criterion in `spec.md` checks off, including the
        negative ones (allowlist unwidened; end-user contract untouched;
        draft byte-compat; no path or body changes).
  - [ ] The generated document is diffed against the base: exactly sixteen
        operations gain `owner_id` and `stage`, the 409 appears on exactly
        those, and nothing else moves.
  - [ ] `pytest tests/community` is green — not just the touched subtrees.
  - [ ] `scripts/ci/python_sast_local.sh src/backend` passes; changed-line
        coverage meets the backend gate; singlebox coverage runs on the PR.
  - [ ] Anything that could not be run locally is named explicitly, with why.
- **Depends on:** Task 7

---

## Groups

- **Group A — The rules:** Tasks 1, 2
  - Theme: adjudication and stage resolution land in core with their errors
    and protocol changes; nothing HTTP-visible moves yet.
- **Group B — The surface:** Tasks 3, 4
  - Theme: the sixteen operations take the parameters and pass them through
    the seam; the socket obeys the same rules as the HTTP groups.
- **Group C — Tests:** Tasks 5, 6
  - Theme: today's refusal pins flip deliberately, and the widened contract —
    operator matrix, stage matrix, document conventions — gets pins of its
    own.
- **Group D — Docs and verification:** Tasks 7, 8
  - Theme: the rule written where it will be read (including the retroactive
    #880 entry), then the whole thing checked against the spec.
