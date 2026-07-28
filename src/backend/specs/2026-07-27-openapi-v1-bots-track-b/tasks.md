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

## Task 4: Extract the create / auth-status flow  `[ ]`
- **Goal:** Move the create + auth-status orchestration out of the internal
  router into a reusable module both surfaces call, with **no behavior change**
  to the internal API.
- **Files:** `core/bot_management/create_flow.py` (new),
  `adapters/http/bot_management/router.py` (internal handlers delegate).
- **Done when:**
  - [ ] `create_flow.py` exposes a create entry point returning a discriminated
        result (created bot dict **or** an auth-pending `{bot_id, iframe_url}`),
        and an auth-status entry point returning `{status, message?, bot?}`.
        Both take the owner identity + injected services/plugins as arguments
        (no FastAPI/`Request` coupling).
  - [ ] The internal `create_bot` and `get_auth_status` handlers call these
        entry points; the ~250 lines of inline orchestration are gone from the
        router.
  - [ ] The **unmodified** internal test suite for bot_management passes (this is
        the behavior-preservation guard). No internal test edited.
- **Depends on:** —

## Task 5: Additive list filters  `[ ]`
- **Goal:** Let the list-by-conditions query filter by `engine` and `status`
  (and confirm `keyword`) so the public list returns exact totals.
- **Files:** `core/bot_management/services/bot_service.py` (+ repository/protocol
  if the query is built there), existing internal service tests.
- **Done when:**
  - [ ] `list_bots_by_conditions` accepts optional `engine` / `status` (and a
        keyword param) that narrow the query; omitting them reproduces today's
        result set and `total` exactly.
  - [ ] A new service-level test proves each filter narrows and `total` matches.
  - [ ] Internal suite unmodified and green.
- **Depends on:** —

## Task 6: Read endpoints — get, list, check-name, ceiling, status, passport  `[ ]`
- **Goal:** Wire the six read handlers.
- **Files:** `adapters/http/openapi_v1/bots/router.py`.
- **Done when:**
  - [ ] `GET /bots/{id}` → `bot_service.get_bot(id, user_id=principal)` →
        `_to_bot` (incl. `cluster_name` via `cluster_for_engine`).
  - [ ] `GET /bots` → `list_bots_by_conditions` with keyword/engine/status +
        pagination → `Page[Bot]`.
  - [ ] `GET /bots/check-name` → `check_bot_name_exists(name)` → `NameCheck`.
  - [ ] `GET /bots/ceiling` → `PolicyService.get_bots_ceiling(entity_id=principal)`
        → `Ceiling`.
  - [ ] `GET /bots/{id}/status` → `get_bot` + assemble → `BotStatus`.
  - [ ] `GET /bots/{id}/passport` → `get_bot` guard + `query_agent_passport` →
        `Passport`.
  - [ ] All six wrapped by `@envelope_errors`; identity from `require_principal`,
        tenant via the middleware/seam.
- **Depends on:** 1, 2, 5

## Task 7: Mutating endpoints — update, delete, restart, engine-config r/w  `[ ]`
- **Goal:** Wire the five straightforward mutating handlers.
- **Files:** `adapters/http/openapi_v1/bots/router.py`.
- **Done when:**
  - [ ] `PUT /bots/{id}` → `update_bot(...)`; `engine` not accepted; returns
        updated `Bot`.
  - [ ] `DELETE /bots/{id}` → `delete_bot(...)` → `Deleted`.
  - [ ] `POST /bots/{id}/restart` → `restart_bot(...)` → `Bot`.
  - [ ] `GET`/`PUT /bots/{id}/engine-config` → `get_bot` prelude +
        `EngineConfigService.read_bot_config` / `write_bot_config` (async) →
        free-form `dict` pass-through.
  - [ ] All wrapped by `@envelope_errors`.
- **Depends on:** 1, 2

## Task 8: Create + auth-status endpoints  `[ ]`
- **Goal:** Wire the two Passport-entangled handlers to the shared flow.
- **Files:** `adapters/http/openapi_v1/bots/router.py`.
- **Done when:**
  - [ ] `POST /bots` validates the engine↔cluster pair, calls the create entry
        point, and returns `201 Envelope[Bot]` or `202 Envelope[BotAuthPending]`
        by result variant.
  - [ ] `GET /bots/{id}/auth-status` calls the auth-status entry point →
        `BotAuthStatus` (maps the bot dict when `ISSUED`).
  - [ ] Both wrapped by `@envelope_errors`.
- **Depends on:** 1, 2, 3, 4

## Task 9: Endpoint tests  `[ ]`
- **Goal:** Prove each endpoint's success shape and the tenant guarantee.
- **Files:** `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py` (new).
- **Done when:**
  - [ ] One happy-path test per endpoint: status code, envelope `code`, and a
        spot-check of mapped `data` fields (incl. `cluster_name` derivation).
  - [ ] Cross-tenant test: a bot created under tenant A is unreachable (404) via
        `GET`/`PUT`/`DELETE`/status/passport/engine-config when the request is
        bound to tenant B — never returned, never mutated.
  - [ ] List-filter test: `keyword`/`engine`/`status` each narrow; `total` exact.
  - [ ] Create test: valid pair → 201; invalid engine↔cluster pair → 400; the
        empty-token path → 202 `BotAuthPending`.
  - [ ] Test harness binds `resolve_avernet_tenant` to a controllable tenant so
        both tenants are exercisable while the real authenticator stays a stub.
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
