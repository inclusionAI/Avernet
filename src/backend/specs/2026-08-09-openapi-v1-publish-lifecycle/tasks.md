# Tasks: Public API — Publish Lifecycle for Service Bots

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Sequencing: bases on `dev` at `5e8bc36` (the access expansion, PR #904). This
> reuses that change's `OwnerIdDep` and its `BotFacts` shape but does not depend
> on any of its behaviour, so a rebase is the only coupling.
>
> Paths below are relative to `src/backend/src/agentclaw/community/` unless the
> path starts with `src/` or `tests/`.

---

## Group A — Core policy

### Task 1: Extract the role bar into the collaborator domain  `[ ]`

- **Goal:** one level-parameterised, fail-closed "does this caller hold at
  least level L on this bot?" that both the operator surfaces and publishing
  can call, owned by the domain that owns role policy.
- **Files:** `core/bot_collaborator/access.py` (new),
  `core/engine_runtime/gate.py`, `core/bot_collaborator/README.md`,
  `core/engine_runtime/README.md`
- **Done when:**
  - [ ] `access.py` exposes `resolve_permission_level(...)` and
        `require_bot_role(..., min_level)`; the latter raises `BotNotFoundError`
        and logs caller id (`%r`) and owner id at the refusal.
  - [ ] A collaborator-lookup exception resolves to `PermissionLevel.NONE` and
        logs — the fail-closed direction is preserved exactly.
  - [ ] `gate.py` keeps `OPERATOR_LEVEL`, `require_bot_operator` and
        `resolve_operator_level` as delegations, with `__all__` unchanged, so
        no import site anywhere moves.
  - [ ] Both modules' `## Context Boundary` sections declare the new
        cross-module import.
  - [ ] `tests/community/core/engine_runtime/` and
        `tests/…/openapi_v1/engine_runtime/test_operator_access.py` pass
        **unmodified** — the proof the extraction is behaviour-preserving.
  - [ ] `tests/community/architecture/` passes.
- **Depends on:** —

### Task 2: Release errors  `[ ]`

- **Goal:** two dependency-free precondition errors the adapter can map without
  importing the service layer.
- **Files:** `core/service_bot/services/release_errors.py` (new)
- **Done when:**
  - [ ] `ReleaseNotStartableError` and `ReleaseNotPromotableError` exist, both
        subclassing `BotPublishServiceError`.
  - [ ] Each docstring states the exact precondition it reports and the fixed
        public message it maps to.
- **Depends on:** —

### Task 3: `ReleaseLifecycleService`  `[ ]`

- **Goal:** every domain decision in this feature, in one transport-agnostic
  place: target resolution + role bar, the draft-resolution table, the two
  guarded advances, and the two scoped reads.
- **Files:** `core/service_bot/services/release_lifecycle_service.py` (new),
  `core/service_bot/README.md`
- **Done when:**
  - [ ] `PUBLISH_LEVEL = PermissionLevel.ADMIN` and
        `PUBLISH_READ_LEVEL = PermissionLevel.MEMBER` are named once, with the
        rationale from spec Decision 2 in the module docstring.
  - [ ] `_resolve_target` runs owner-scoped `get_bot` → `require_bot_role` →
        service-type check **in that order**, and returns `BotFacts`. An async
        façade runs it via `asyncio.to_thread`.
  - [ ] `start_verify_release` implements the draft-resolution table in
        `plan.md` §4 exactly, including the no-side-effect in-flight branch and
        the 409 branch for `FAILED`/`UPGRADED`/`RELEASED`.
  - [ ] The first release's name is derived from the bot's own name; no public
        release-name field is introduced.
  - [ ] `promote_release` calls `process` **only** from `VALIDATING`; already
        promoted / already online return `started=False`; everything else
        raises `ReleaseNotPromotableError`.
  - [ ] Both writes return `ReleaseAdvance(record, started)` with the record
        **re-read after** `process`.
  - [ ] `_load_release` looks up by `(publish_bot_id, owner_id, version, env)`
        **and** asserts `record.source_bot_pk == facts.bot_pk`, raising
        `PublishNotFoundError` otherwise.
  - [ ] `list_releases` and `list_release_operations` are keyed on `bot_pk` /
        the proven record id respectively, page in the service, and return
        `(total, items)`. The ledger sorts ascending by `(gmt_create, id)`.
  - [ ] The in-service pagination choice is justified in the docstring rather
        than left to be re-litigated.
  - [ ] No HTTP type, no response-shaped dict, no `ext` read anywhere in the
        module.
- **Depends on:** Tasks 1, 2

### Task 4: Service API protocol + DI  `[ ]`

- **Files:** `api/release_lifecycle_service.py` (new),
  `di/modules/service_bot_module.py`,
  `tests/community/architecture/test_service_api_conformance.py`
- **Done when:**
  - [ ] `ReleaseLifecycleServiceProtocol` is `@runtime_checkable` with **real**
        signatures, not `*args/**kwargs`.
  - [ ] The `(Protocol, ReleaseLifecycleService)` pair is registered in the
        conformance gate and the gate passes.
  - [ ] The binding sits beside `_publish_flow_service_protocol`, singleton,
        and resolves in the community composition root.
- **Depends on:** Task 3

---

## Group B — Public surface

### Task 5: Schemas and projections  `[ ]`

- **Goal:** the published contract, and the mappings that make an unmapped
  internal value a CI failure instead of a 500.
- **Files:** `adapters/http/openapi_v1/publish/schemas.py` (new),
  `adapters/http/openapi_v1/publish/__init__.py` (new)
- **Done when:**
  - [ ] `ReleaseState` carries the ten published values from `plan.md` §2 and a
        `PublishStatus → ReleaseState` map that is exhaustive by construction.
  - [ ] `ReleaseOperationKind` and `ReleaseOperationState` mirror their internal
        enums value-for-value, both maps exhaustive.
  - [ ] `Release` publishes exactly `version`, `state`, `message`, `bot_id`,
        `name`, `description`, `created_at`, `updated_at`.
  - [ ] `ReleaseOperation` publishes exactly `kind`, `stage`, `attempt`,
        `state`, `operator`, `created_at`, `updated_at`.
  - [ ] The projection functions never read `ext`, `params`, `result`,
        `last_error`, `bot_uuid`, `baas_publish_id` or `request_id`.
  - [ ] The `failed` message is the fixed `"Publish failed"`; the internal
        `error_message` is never interpolated.
  - [ ] The request body model for the two writes sets `extra="forbid"`.
- **Depends on:** —

### Task 6: The router  `[ ]`

- **Files:** `adapters/http/openapi_v1/publish/router.py` (new),
  `adapters/http/openapi_v1/__init__.py`
- **Done when:**
  - [ ] Five routes at the paths in `plan.md` §1, prefix `/bots/publish`.
  - [ ] Every handler takes `PrincipalDep`, `UserIdDep`, `OwnerIdDep`
        (imported from `engine_runtime/params.py`, not re-declared),
        `request: Request`, and `@envelope_errors`.
  - [ ] The two writes return 202 when `started`, 200 when not, using the same
        mechanism `bots/router.py` uses for its 201/202 split.
  - [ ] The lists use the shared `PageParamsDep` and `page(...)`.
  - [ ] No domain branching in the router — it maps parameters in and
        projections out, nothing else.
  - [ ] The group is added to `_SUBGROUPS` in `openapi_v1/__init__.py` with
        `USER_SCOPED_ERROR_RESPONSES`, above the bots router.
- **Depends on:** Tasks 4, 5

### Task 7: Error mapping  `[ ]`

- **Files:** `adapters/http/openapi_v1/responses.py`
- **Done when:**
  - [ ] Every row of the `plan.md` §6 table is present, leaves before
        `BotPublishServiceError`.
  - [ ] **Both** `BotNotFoundError` classes are mapped (the `service_bot` one is
        a different class from the `bot_management` one already in the table),
        with byte-identical 404 messages.
  - [ ] The four 409s get distinct business subcodes in `ERROR_SUBCODES`.
  - [ ] No mapped message interpolates `str(exc)`.
- **Depends on:** Task 2

---

## Group C — Tests

### Task 8: Core service tests  `[ ]`

- **Files:** `tests/community/core/service_bot/services/test_release_lifecycle_service.py` (new)
- **Done when:**
  - [ ] The draft-resolution table is covered row by row, including that the
        in-flight branch performs **no** write.
  - [ ] `_load_release` rejects a version whose `source_bot_pk` differs.
  - [ ] The ledger read cannot return a row from another release.
  - [ ] The read and write bar constants are asserted, so changing one is a
        deliberate test edit.
- **Depends on:** Task 3

### Task 9: Endpoint, access, projection and isolation tests  `[ ]`

- **Files:** `tests/community/adapters/http/openapi_v1/publish/` (new package:
  `__init__.py`, `conftest.py`, `test_publish_endpoints.py`,
  `test_release_state_machine.py`, `test_publish_access.py`,
  `test_publish_projection.py`, `test_publish_tenant_isolation.py`)
- **Done when:**
  - [ ] All five handlers, success and every mapped error, including the
        202/200 split and an `extra="forbid"` 422.
  - [ ] Start and promote are exercised from **each** of the ten
        `PublishStatus` values, plus both CAS-loser paths.
  - [ ] The role matrix (owner / admin / member / stranger × writes and reads)
        holds, and every refusal is byte-identical to a missing bot.
  - [ ] The three enum maps are asserted exhaustive over their internal enums.
  - [ ] The withheld field sets are asserted absent from both payloads.
  - [ ] Cross-tenant and cross-bot lookups are masked 404s against the real
        Track A guard.
- **Depends on:** Tasks 6, 7

### Task 10: Amend the surface-wide contract tests  `[ ]`

- **Files:** `tests/community/adapters/http/openapi_v1/test_explicit_user_id.py`,
  `…/test_path_convention.py`, `…/test_openapi_error_schema.py`
- **Done when:**
  - [ ] `test_explicit_user_id` expects 61 user-scoped operations and the same
        4 exempt ones.
  - [ ] `test_path_convention` passes with `publish` in the routed reserved
        list and absent from the unrouted one (it asserts the two are
        disjoint).
  - [ ] `test_openapi_error_schema` passes: the group documents exactly the
        user-scoped set, and no 501/504.
- **Depends on:** Tasks 6, 11

---

## Group D — Documentation and gates

### Task 11: Handoff documentation  `[ ]`

- **Files:** `src/backend/docs/openapi-v1/README.md`,
  `src/backend/docs/openapi-v1/README.zh-CN.md`
- **Done when:**
  - [ ] Track B status board gains a `publish` row.
  - [ ] A new endpoint section carries the five-row table, the `PublishStatus →
        ReleaseState` map, the ledger's published/withheld field sets, and the
        two role bars with their reasoning.
  - [ ] `publish` is added to the `<!-- reserved-component-names -->` fenced
        list (routed, not unrouted).
  - [ ] The deferred-items line pointing at #909 is struck.
  - [ ] A dated changelog line is appended.
  - [ ] The Chinese mirror carries every one of the above.
- **Depends on:** Task 6

### Task 12: Full gates and PR  `[ ]`

- **Done when:**
  - [ ] `tests/community/architecture/` green — run **after** every new
        cross-module import is declared, not before.
  - [ ] The internal publish suites pass **unmodified**:
        `tests/community/adapters/http/service_bot/test_router_publish_coverage.py`,
        `tests/community/core/service_bot/services/test_publish_flow_service.py`,
        `tests/community/e2e/publish_boundary/`.
  - [ ] The engine-runtime operator and stage suites pass **unmodified**.
  - [ ] Full `tests/community` green; backend SAST/lint green.
  - [ ] Draft PR opened, titled
        `feat(backend): publish service-bot releases on the openapi v1 surface`,
        with the `Problem` / `Solution` / `Validation` sections from
        `.github/pull_request_template.md`, linking #909 and this spec
        directory. State explicitly what could not be run locally and why.
- **Depends on:** all
