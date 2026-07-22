# Plan: Gateway v1 External API — Definition

## Approach

Define the v1 external API as **FastAPI delivery adapters on the gateway** — one router
per resource group under base path `/openapi/v1`, with Pydantic v2 request/response models,
a shared response **envelope**, and per-route **`x-avernet-security`** metadata (via FastAPI
`openapi_extra`). Handlers are **stubs** (`raise NotImplementedError`) — the deliverable is
the served OpenAPI, not behavior. Wire the routers into `create_app()` so `/openapi.json`
renders the full contract. **Delivery-first, two milestones:** Milestone 1 lands the global
contract + one representative group (`bots`) and goes up for review to unblock the gateway
and backend teams; Milestone 2 adds the remaining six groups.

> **Why define response models on a pass-through gateway?** The models are consumed at runtime
> only by FastAPI's **OpenAPI generation** (serving `/openapi.json` / the runtime publish-register
> module) — that is their sole purpose here. When the gateway later proxies real traffic, the
> handler returns the downstream response as a raw `Response`, which FastAPI forwards **without**
> applying `response_model`; the gateway never constructs an `Envelope` for actual traffic. The
> backend produces the envelope; the gateway defines the contract and relays. `Envelope` lives in
> its own shared contract module so the backend can reuse the same definition rather than declaring
> a second one.

## Affected Components

- `src/gateway/src/gateway/community/adapters/web/` — **all changes live here.** New routers +
  shared contract module + app wiring. (Rule 8: `adapters/` = delivery; `api/` stays reserved
  for future Service API Protocols.)
- `src/gateway/src/gateway/community/adapters/web/app.py:19` — `create_app()` gains router includes.
- `src/gateway/tests/` — new tests asserting the served OpenAPI.
- **No** changes to `backend`, `engine`, `bcs`, `baas`. **No** core/business logic, **no** Service
  API Protocols yet (deferred to the backend-implementation pass — see Risks).

## Data Model Changes

None. No DB, no migrations. Pydantic models are transport DTOs only.

## API / Interface Changes

All routes under `/openapi/v1`. All JSON responses use the envelope
`Envelope[T] = { code: int (6-digit), message: str, data: T | null, request_id: str }`.
List payloads use `Page[T] = { total: int, items: list[T] }`. Every route declares
`x-avernet-security: [{ first_party_user: {} }]` (user principal; scope vocabulary deferred).
Identity of the caller is implicit from the principal — **no `entity_id` params**.

**bots** (`/openapi/v1/bots`) — representative group, Milestone 1
- `POST ""` → 201 `Envelope[Bot]` (created) or 202 `Envelope[BotAuthPending]` (Passport two-phase; body: `bot_name, bot_desc, engine, cluster_name, bot_type, payload`)
- `GET "/{bot_id}/auth-status"` → `Envelope[BotAuthStatus]`
- `GET ""` → `Envelope[Page[Bot]]` (query: `keyword, engine, status, page, page_size`)
- `GET "/{bot_id}"` → `Envelope[Bot]`
- `PUT "/{bot_id}"` → `Envelope[Bot]` (no `engine`)
- `DELETE "/{bot_id}"` → `Envelope[Deleted]`
- `POST "/{bot_id}/restart"` → `Envelope[Bot]`
- `GET "/{bot_id}/status"` → `Envelope[BotStatus]`
- `GET "/check-name"` (query `name`) → `Envelope[NameCheck]`
- `GET "/ceiling"` → `Envelope[Ceiling]`
- `GET "/{bot_id}/passport"` → `Envelope[Passport]`
- `GET "/{bot_id}/engine-config"` / `PUT "/{bot_id}/engine-config"` → `Envelope[EngineConfig]`

**identity** (`/openapi/v1/identity`) — M2
- `GET "/bot/{bot_id}"` → `Envelope[IdentityFileList]`
- `GET "/bot/{bot_id}/{file_type}"` → `Envelope[IdentityFile]`
- `PUT "/bot/{bot_id}/{file_type}"` → `Envelope[IdentityFileRef]` (body `content`; `file_type` = enum whitelist)

**resources** (`/openapi/v1/resources`) — M2
- `GET ""` → `Envelope[Page[Resource]]`; `POST ""` → `Envelope[Resource]` (body `type: file|link|folder`, unified; Yuque = a `link`)
- `GET "/{resource_id}"` / `PUT "/{resource_id}"` → `Envelope[Resource]`; `DELETE "/{resource_id}"` → `Envelope[Deleted]`
- `GET "/{resource_id}/download"` → binary stream (not enveloped); `GET "/{resource_id}/preview"` → `Envelope[Preview]`
- `GET "/check-name"` → `Envelope[NameCheck]`; `POST "/upload"` (multipart) → `Envelope[Resource]`

**mcp** (`/openapi/v1/mcp`) — M2
- `GET "/servers"` → `Envelope[Page[McpServer]]`; `GET "/servers/{server_code}"` → `Envelope[McpServerDetail]`
- `GET "/servers/{server_code}/permissions"` → `Envelope[McpPermission]`; `GET "/tenants"` → `Envelope[list[McpTenant]]`
- `GET "/servers/{server_code}/config"` / `PUT "/servers/{server_code}/config"` → `Envelope[McpConfig]`

**routines** (`/openapi/v1/routines`) — M2 (was cron)
- `GET ""` → `Envelope[Page[Routine]]` (query `bot_id, status`); `POST ""` → `Envelope[Routine]`
  (body `bot_id, name, trigger: {type: "schedule", cron: str}, command`)
- `GET "/{routine_id}"` → `Envelope[Routine]`; `PATCH "/{routine_id}"` → `Envelope[Routine]`; `DELETE "/{routine_id}"` → `Envelope[Deleted]`
- `POST "/{routine_id}/run"` → `Envelope[RoutineRun]`; `GET "/{routine_id}/runs"` → `Envelope[Page[RoutineRun]]`

**skills** (`/openapi/v1/skills` + bot sub-resource) — M2
- `GET "/skills"` → `Envelope[Page[Skill]]` (catalog); `GET "/skills/{skill_id}"` → `Envelope[SkillDetail]`
- `GET "/bots/{bot_id}/skills"` → `Envelope[list[BotSkill]]`; `POST "/bots/{bot_id}/skills"` → `Envelope[BotSkill]`;
  `DELETE "/bots/{bot_id}/skills/{skill_id}"` → `Envelope[Deleted]`

**channels** (`/openapi/v1/channels`) — M2
- `GET ""` → `Envelope[Page[Channel]]` (query `bot_id`); `POST ""` → `Envelope[Channel]` (type `dingding`)
- `GET "/{channel_id}"` / `PUT "/{channel_id}"` → `Envelope[Channel]`; `PATCH "/{channel_id}"` (toggle `status`) → `Envelope[Channel]`; `DELETE "/{channel_id}"` → `Envelope[Deleted]`

## Key Files & Functions

Shared contract (Milestone 1):
- `adapters/web/contracts/envelope.py` (new) — `Envelope[T]` (Pydantic v2 generic), `Page[T]`, `Deleted`,
  `NameCheck`, and standard-code constants (`200000/201000/202000/...`).
- `adapters/web/contracts/security.py` (new) — `user_principal()` → `{"x-avernet-security": [{"first_party_user": {}}]}`
  for use as each route's `openapi_extra`. Format mirrors `src/gateway/docs/2026-07-21-auth-design.md` §8.1.
- `adapters/web/contracts/pagination.py` (new) — `PageParams` (page/page_size query dependency).
- `adapters/web/routers/__init__.py` (new) — `include_all(app)` aggregator.

Per group (Milestone 1 = `bots`; Milestone 2 = the rest):
- `adapters/web/routers/<group>/router.py` (new) — `APIRouter(prefix="/openapi/v1/<group>")`, stubbed handlers.
- `adapters/web/routers/<group>/schemas.py` (new) — Pydantic request/response models.

Wiring:
- `adapters/web/app.py` — in `create_app()`, call `include_all(app)` after middleware install.

Tests:
- `tests/test_openapi_contract.py` (new) — build app, GET `/openapi.json`, assert: base path present per group;
  every `/openapi/v1/**` operation carries `x-avernet-security`; envelope schema shape.

## Dependencies

None new. `fastapi>=0.100`, `pydantic>=2.0`, `httpx`/`pytest`/`pytest-asyncio` already in `pyproject.toml`.

## Risks & Mitigations

- **Risk:** Defining delivery routers without Service API Protocols brushes Rule 8/3 ("Service API only as
  HTTP router" red flag). **Mitigation:** This pass is definition-only with stubbed handlers and no core yet;
  Service API Protocols are introduced in the backend-implementation pass (Rule 24 incremental). Note it in the PR.
- **Risk:** `x-avernet-security` format must match the (someone-else-owned) route-security compiler in #301.
  **Mitigation:** mirror #301 §8.1 exactly; keep the format in one helper so it's changeable in one place.
- **Risk:** Envelope-wrapping + Pydantic generics may render awkward or fail under strict type-checking.
  **Mitigation:** the OpenAPI-contract test is the gate; verify generics + `response_model=Envelope[X]` early
  in Milestone 1 (that's the whole point of proving the pattern on `bots` first).
- **Risk:** `resources`/`skills` shapes derived from code, docs not yet received. **Mitigation:** isolated per
  group; adjust on delivery without touching the foundation.
- **Risk:** Incoming auto-generation module may expect vanilla FastAPI. **Mitigation:** use only standard
  `APIRouter`/`openapi_extra`; no custom OpenAPI hacks.

## Alternatives Considered

- **Hand-write `openapi/*.yaml`** (as batch-1 did) — rejected; user wants FastAPI-generated OpenAPI + an
  incoming auto-gen module.
- **Put routers under `api/`** — rejected; Rule 8 reserves `api/` for Service API Protocols.
- **Define `Principal`/auth models here** — rejected; that's the gateway auth-plugin's job (#301), out of scope.
  We only *declare* the requirement via `x-avernet-security`.
- **One big PR** — rejected; user wants the auth/global-contract foundation reviewable first to unblock others.

## Rollout

- **Milestone 1 (PR for review → merge):** shared contract module + `bots` group + app wiring + OpenAPI test.
  Land on the current branch; un-draft **PR #345** for review; merge to unblock gateway/backend teams.
- **Milestone 2 (follow-up PR):** `identity`, `resources`, `mcp`, `routines`, `skills`, `channels`. If #345
  merged first, restart the branch from `dev` for M2 (per branch policy).
- Net-new surface: no backward-compat concerns. Forward-compat: no `stage`; agents addressed as their live
  definition so a future version/snapshot layer is additive.

## Test Strategy

- **Contract test (must-have):** app boots; `/openapi.json` served; each group's routes present under
  `/openapi/v1`; **every** exposed operation has an `x-avernet-security` block (mirrors #301's CI gate intent);
  envelope + `Page` schemas present.
- **Smoke:** existing `tests/test_example.py` still green (`/health`, `/api/test`).
- **Not tested:** handler invocation (stubs raise `NotImplementedError` by design).
- Lint/type: `ruff`, `mypy`/`pyright` strict clean; `src/gateway/scripts/ci_test.sh` green.
