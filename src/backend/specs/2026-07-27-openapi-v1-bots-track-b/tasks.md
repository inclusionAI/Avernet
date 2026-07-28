# Tasks: Public API Bots Category (Track B)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Each task lands with the internal suite green and (from Task 3 on) its own new
tests. Ordered so shared plumbing exists before the handlers that use it.

## Task 1: Response + envelope helper  `[x]`
- **Goal:** One place that turns a payload + request into the standard
  `Envelope`/`Page`, and maps domain errors to enveloped error responses.
- **Files:** `adapters/http/openapi_v1/responses.py` (new),
  `tests/community/adapters/http/openapi_v1/test_responses.py` (new).
- **Done when:**
  - [x] `envelope(data, request, *, code=CODE_OK, message="OK")`,
        `page(total, items, request)`, `created(...)`, `accepted(...)`,
        `deleted(request)` implemented; `request_id` read from
        `request.state.trace_id` (falls back to `""` when unset).
  - [x] `@envelope_errors` decorator maps the domain errors named in the plan
        (`BotNotFoundError`→404, `BotNameExistsError`→409, `BotNameInvalidError`
        →400, `BotLimitExceededError`/`DeviceLimitError`→409,
        `BotPermissionError`→404, `BotInvalidLifecycleStateError`→409,
        `PassportError`→502, `ClusterMismatchError`→400) to an `Envelope`
        (`data=None`, mapped 6-digit code); unmapped exceptions propagate.
  - [x] Tests: each builder's code/message/request_id; trace-id fallback; the
        decorator's mapping for one mapped error and one pass-through. 8 passed.
- **Depends on:** —

## Task 2: Cluster ↔ engine rule  `[x]`
- **Goal:** The `ACRA`/`ANDC` ↔ engine bijection in one place, both directions.
- **Files:** `adapters/http/openapi_v1/clusters.py` (new) + its test.
- **Done when:**
  - [x] `cluster_for_engine(engine) -> "ANDC" | "ACRA"` returns `ANDC` iff
        `engine == "teclaw"`, else `ACRA`.
  - [x] `validate_engine_cluster(engine, cluster) -> None` raises
        `ClusterMismatchError` when the pair breaks the bijection.
  - [x] Tests: `teclaw`/`ANDC` ok; non-teclaw/`ACRA` ok; both mismatches raise;
        cluster names come only from the enum. 9 passed.
- **Depends on:** —

## Task 3: Schema updates  `[x]`
- **Goal:** Public `Bot`/`BotCreate` express `cluster_name` as the validated enum
  and advertise the combination rule in the contract.
- **Files:** `adapters/http/openapi_v1/bots/schemas.py`,
  `adapters/http/openapi_v1/errors.py` (new — breaks an import cycle: the
  lightweight schema/cluster layer must not transitively import `bot_service`).
- **Done when:**
  - [x] `Bot.cluster_name` and `BotCreate.cluster_name` are the `ClusterName`
        `Literal["ACRA", "ANDC"]`; field descriptions state the engine↔cluster
        rule so it shows in the generated OpenAPI. Bad enum values rejected.
  - [x] No other schema field changes; existing fields untouched.
        (`BotUpdate.cluster_name` left as-is per scope — flagged for review since
        cluster is engine-derived and engine is immutable on update.)
- **Depends on:** —

## Task 4: Extract the create / auth-status flow  `[x]`
- **Goal:** Move the create + auth-status orchestration out of the internal
  router into a reusable module both surfaces call, with **no behavior change**
  to the internal API.
- **Files:** `core/bot_management/create_flow.py` (new),
  `adapters/http/bot_management/router.py` (internal handlers delegate).
- **Done when:**
  - [x] `create_flow.py` exposes `create_bot_with_authorization` (→ `Created` |
        `AuthPending`) and `complete_bot_authorization` (→ `AuthStatusResult`).
        Both take the owner identity + injected services/plugins as arguments
        (no FastAPI/`Request` coupling); typed against the concrete `BotService`
        so no api-layer boundary is crossed.
  - [x] The internal `create_bot` and `get_auth_status` handlers delegate; the
        inline orchestration is gone from the router. `generate_bot_id` stays in
        the router (patch point); `PassportError` re-mapped to 5400 there.
  - [x] The **unmodified** internal suite passes: 142 in
        `test_router.py` + `test_bot_passport.py`, plus module-boundary and
        no-fastapi-in-core guards. No internal test edited.
- **Depends on:** —

## Task 5: Additive list filters  `[x]`
- **Goal:** Let the list-by-conditions query filter by `engine` and `status`
  (and confirm `keyword`) so the public list returns exact totals.
- **Files:** `core/bot_management/services/bot_service.py`,
  `core/bot_management/repository/protocol.py`,
  `plugins/bot_repository.py`, `tests/community/plugins/test_bot_repository_unified.py`.
- **Done when:**
  - [x] `list_bots_by_conditions` accepts optional `owner_id` / `engine` /
        `status` (keyword = existing `bot_name`) that narrow the query; omitting
        them reproduces today's result set and `total` exactly.
        **Plan correction:** also added `owner_id` — the plan named
        `list_bots_by_conditions`, but that method was *not* owner-scoped, and
        the public list must return only the caller's bots. `owner_id` scopes it.
  - [x] A repository-level test (where the SQL filtering lives) proves each
        filter narrows and `total` matches; keyword composes with the filters.
  - [x] Internal suite unmodified and green (prod repo + bot_public caller: 23;
        unified repo incl. new test: 33).
- **Depends on:** —

## Task 6: Read endpoints — get, list, check-name, ceiling, status, passport  `[x]`
- **Goal:** Wire the six read handlers.
- **Files:** `adapters/http/openapi_v1/bots/router.py`,
  `adapters/http/openapi_v1/principal.py` (new — caller-id seam),
  `adapters/http/openapi_v1/errors.py` + `responses.py` (MissingPrincipalError→401),
  `tests/.../openapi_v1/test_bots_endpoints.py` (new).
- **Done when:**
  - [x] `GET /bots/{id}` → `bot_service.get_bot(id, owner)` → `_to_bot`
        (incl. `cluster_name` via `cluster_for_engine`).
  - [x] `GET /bots` → `list_bots_by_conditions(owner_id, keyword→bot_name,
        engine, status, page)` → `Page[Bot]`.
  - [x] `GET /bots/check-name` → `check_bot_name_exists(name)` → `NameCheck`.
  - [x] `GET /bots/ceiling` → `PolicyService.get_bots_ceiling(entity_id=owner)`
        → `Ceiling`.
  - [x] `GET /bots/{id}/status` → `get_bot` + assemble → `BotStatus`.
  - [x] `GET /bots/{id}/passport` → `get_bot` guard + `query_agent_passport` →
        `Passport` (missing passport → 404).
  - [x] All six wrapped by `@envelope_errors`; owner from `caller_owner_id`,
        tenant bound by middleware. 10 endpoint tests pass (incl. 401/404-mask).
- **Depends on:** 1, 2, 5

## Task 7: Mutating endpoints — update, delete, restart, engine-config r/w  `[x]`
- **Goal:** Wire the five straightforward mutating handlers.
- **Files:** `adapters/http/openapi_v1/bots/router.py`, `test_bots_endpoints.py`.
- **Done when:**
  - [x] `PUT /bots/{id}` → `update_bot(bot_name, bot_desc)`; `engine` not
        accepted; `cluster_name`/`engine_options` accepted for schema symmetry
        but not update drivers (engine immutable; config via engine-config).
  - [x] `DELETE /bots/{id}` → `delete_bot(...)` → `Deleted`.
  - [x] `POST /bots/{id}/restart` → `restart_bot(...)` → `Bot`.
  - [x] `GET`/`PUT /bots/{id}/engine-config` → `get_bot` prelude +
        `EngineConfigService.read_bot_config` / `write_bot_config` (async) →
        free-form `dict` pass-through (missing entity → masked 404).
  - [x] All wrapped by `@envelope_errors`. 16 endpoint tests pass.
- **Depends on:** 1, 2

## Task 8: Create + auth-status endpoints  `[x]`
- **Goal:** Wire the two Passport-entangled handlers to the shared flow.
- **Files:** `adapters/http/openapi_v1/bots/router.py`, `test_bots_endpoints.py`.
- **Done when:**
  - [x] `POST /bots` validates the engine↔cluster pair, allocates the id, calls
        `create_bot_with_authorization`, and returns `201 Envelope[Bot]` or
        `202 Envelope[BotAuthPending]` by result variant.
  - [x] `GET /bots/{id}/auth-status` calls `complete_bot_authorization` →
        `BotAuthStatus` (maps the bot dict when `ISSUED`).
  - [x] Both wrapped by `@envelope_errors`. 22 endpoint tests pass.
  - **Known gaps (flagged):** `engine_options` accepted but not wired (internal
        create has no such input); GET auth-status carries no body, so completion
        uses defaults for any body-sourced attributes.
- **Depends on:** 1, 2, 3, 4

## Task 9: Endpoint tests  `[x]`
- **Goal:** Prove each endpoint's success shape and the tenant guarantee.
- **Files:** `tests/.../openapi_v1/test_bots_endpoints.py` (24 tests),
  `tests/.../openapi_v1/test_bots_tenant_isolation.py` (5 tests, real guard).
- **Done when:**
  - [x] Happy-path test per endpoint: status code, envelope `code`, mapped
        `data` fields (incl. `cluster_name` derivation).
  - [x] Cross-tenant: a bot created under tenant A is unreachable/immutable from
        tenant B (get→None, list→0, update/delete→no-op) through the real Track A
        guard on a live repo — the mechanism behind the handlers' masked 404.
  - [x] List filters: endpoint passthrough (`owner_id`+keyword/engine/status/page
        reach the service) + repo-level narrowing with exact totals (Task 5).
  - [x] Create: valid pair → 201; invalid engine↔cluster → 400; empty-token →
        202 `BotAuthPending`; auth-status preserves re-supplied attributes.
  - [x] Harness overrides `require_principal` (caller) and drives the tenant via
        `avernet_tenant_scope` (what the middleware sets) — real authenticator
        stays a stub.
- **Depends on:** 6, 7, 8

## Task 10: Full-suite + lint gate  `[ ]`
- **Goal:** Green across the board and no architecture-boundary violations.
- **Files:** — (CI/verification only).
- **Done when:**
  - [ ] New + internal suites pass locally (`cd src/backend`, `uv run` per the
        Stage-1 notes; `--default-index https://pypi.org/simple`).
  - [ ] Any new cross-module import declared in the module `README.md`
        `## Context Boundary`; `tests/community/architecture/` green.
  - [ ] Remote CI green on the PR (push `--no-verify`, rely on remote gates).
- **Depends on:** 9

## Groups

Execution units for SDD Phase 4. Review after each group with code changes;
check in with the user only where noted.

- **Group A — Shared primitives:** Tasks 1, 2, 3. New code in the `openapi_v1`
  package only (response/envelope helper, cluster↔engine rule, schema enum). No
  existing behavior touched. Review; no check-in.
- **Group B — Internal seams:** Tasks 4, 5. The two behavior-preserving edits to
  `bot_management` internals (create-flow extraction, additive list filters),
  guarded by the unmodified internal suite. Review **and check in** — highest
  blast radius.
- **Group C — Handlers:** Tasks 6, 7, 8. Wire all 13 routes to the Group A/B
  pieces. Review.
- **Group D — Tests & gate:** Tasks 9, 10. Endpoint + cross-tenant + filter
  tests, then the full-suite/architecture gate and the acceptance-criteria walk.
  Review; final verification.
