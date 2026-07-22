# Tasks: Gateway v1 External API — Definition

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
> All paths are under `src/gateway/src/gateway/community/`. All handlers raise
> `NotImplementedError` (definition only). Keep `ruff` + strict `mypy`/`pyright` green and
> `src/gateway/scripts/ci_test.sh` passing throughout.

## Task 1: Shared response contract (envelope + pagination) — `[x]` done
- **Goal:** Provide the standard response envelope and pagination primitives every route reuses.
- **Files:** `adapters/web/contracts/__init__.py`, `adapters/web/contracts/envelope.py`, `adapters/web/contracts/pagination.py`
- **Done when:**
  - [x] `Envelope[T]` (Pydantic v2 generic) with `code: int`, `message: str`, `data: T | None`, `request_id: str`; plus `Page[T] = {total, items}`, and small shared payloads `Deleted`, `NameCheck`.
  - [x] Standard code constants defined (`200000, 201000, 202000, 204000`).
  - [x] `PageParams` query dependency (`page`, `page_size`) in `pagination.py`.
  - [x] `Envelope[SomeModel]` instantiates and `model_dump()` shows all four keys; ruff/mypy clean.
- **Depends on:** —

## Task 2: Per-route security metadata helper — `[x]` done
- **Goal:** One place that emits the `x-avernet-security` OpenAPI extension for the user principal.
- **Files:** `adapters/web/contracts/security.py`
- **Done when:**
  - [x] `user_principal()` returns `{"x-avernet-security": [{"first_party_user": {}}]}` (format per `src/gateway/docs/2026-07-21-auth-design.md` §8.1).
  - [x] Unit test asserts the exact shape.
- **Depends on:** —

## Task 3: Router aggregator + app wiring
- **Goal:** A single include point so `create_app()` mounts all group routers.
- **Files:** `adapters/web/routers/__init__.py` (new, `include_all(app)`), `adapters/web/app.py` (call it)
- **Done when:**
  - [ ] `include_all(app)` includes each registered group router; called in `create_app()` after middleware install.
  - [ ] `/health` and `/api/test` still work; `/openapi.json` is served; existing `tests/test_example.py` stays green.
- **Depends on:** Task 1

## Task 4: `bots` schemas
- **Goal:** Request/response DTOs for the bots group.
- **Files:** `adapters/web/routers/bots/__init__.py`, `adapters/web/routers/bots/schemas.py`
- **Done when:**
  - [ ] Models: `Bot`, `BotCreate` (`bot_name, bot_desc, engine, cluster_name, bot_type, payload`), `BotUpdate` (no `engine`), `BotAuthPending`, `BotAuthStatus`, `BotStatus`, `Ceiling`, `Passport`, `EngineConfig`.
  - [ ] No `entity_id`/ownership params (identity is implicit from the principal). Ruff/mypy clean.
- **Depends on:** Task 1

## Task 5: `bots` router (stubbed)
- **Goal:** The representative group's endpoints, proving envelope + security + generics end-to-end.
- **Files:** `adapters/web/routers/bots/router.py`
- **Done when:**
  - [ ] `APIRouter(prefix="/openapi/v1/bots")` with all plan operations (create 201/202, auth-status, list w/ `PageParams`, get, update, delete, restart, status, check-name, ceiling, passport, get/put engine-config).
  - [ ] Every route sets `response_model=Envelope[...]` and `openapi_extra=user_principal()`; handlers `raise NotImplementedError`.
  - [ ] Registered via the aggregator; `/openapi.json` shows all bots paths with correct methods, schemas, and `x-avernet-security`.
- **Depends on:** Tasks 2, 3, 4

## Task 6: OpenAPI contract test (Milestone 1 gate)
- **Goal:** Lock the contract invariants the whole design depends on.
- **Files:** `tests/test_openapi_contract.py`
- **Done when:**
  - [ ] Builds the app, GETs `/openapi.json`; asserts bots routes present under `/openapi/v1/bots`.
  - [ ] Asserts **every** `/openapi/v1/**` operation carries an `x-avernet-security` block.
  - [ ] Asserts the `Envelope` and `Page` schemas are present.
  - [ ] `ci_test.sh` green. **← End of Milestone 1: un-draft PR #345 for review to unblock other teams.**
- **Depends on:** Task 5

## Task 7: `identity` group
- **Goal:** Bot identity-file read/write endpoints.
- **Files:** `adapters/web/routers/identity/{__init__.py,schemas.py,router.py}`
- **Done when:**
  - [ ] `GET /openapi/v1/identity/bot/{bot_id}`, `GET/PUT /openapi/v1/identity/bot/{bot_id}/{file_type}` (file_type enum whitelist; PUT body `content`).
  - [ ] Envelope + `user_principal()`; stubbed; registered; visible in `/openapi.json`; contract test extended to assert this group.
- **Depends on:** Task 6

## Task 8: `resources` group
- **Goal:** Unified files+links resource endpoints.
- **Files:** `adapters/web/routers/resources/{__init__.py,schemas.py,router.py}`
- **Done when:**
  - [ ] `GET ""`, `POST ""` (unified `type: file|link|folder`; Yuque = link), `GET/PUT/DELETE /{resource_id}`, `GET /{resource_id}/download` (binary, not enveloped), `GET /{resource_id}/preview`, `GET /check-name`, `POST /upload` (multipart).
  - [ ] Envelope + `user_principal()`; stubbed; registered; contract test extended.
- **Depends on:** Task 6

## Task 9: `mcp` group
- **Goal:** MCP market + unified config endpoints.
- **Files:** `adapters/web/routers/mcp/{__init__.py,schemas.py,router.py}`
- **Done when:**
  - [ ] `GET /servers`, `GET /servers/{server_code}`, `GET /servers/{server_code}/permissions`, `GET /tenants`, `GET/PUT /servers/{server_code}/config`.
  - [ ] Envelope + `user_principal()`; stubbed; registered; contract test extended.
- **Depends on:** Task 6

## Task 10: `routines` group
- **Goal:** Scheduled-task endpoints (was cron), stable schema with nested trigger.
- **Files:** `adapters/web/routers/routines/{__init__.py,schemas.py,router.py}`
- **Done when:**
  - [ ] `GET ""` (filter `bot_id,status`), `POST ""` (body `bot_id, name, trigger:{type:"schedule",cron}, command`), `GET/PATCH/DELETE /{routine_id}`, `POST /{routine_id}/run`, `GET /{routine_id}/runs`.
  - [ ] `trigger` modeled as a nested object (extensible); Envelope + `user_principal()`; stubbed; registered; contract test extended.
- **Depends on:** Task 6

## Task 11: `skills` group (catalog + bot sub-resource)
- **Goal:** Skill catalog plus an agent's installed skills.
- **Files:** `adapters/web/routers/skills/{__init__.py,schemas.py,router.py}`
- **Done when:**
  - [ ] `GET /openapi/v1/skills`, `GET /openapi/v1/skills/{skill_id}`; `GET/POST /openapi/v1/bots/{bot_id}/skills`, `DELETE /openapi/v1/bots/{bot_id}/skills/{skill_id}`.
  - [ ] Envelope + `user_principal()`; stubbed; registered; contract test extended.
- **Depends on:** Task 6

## Task 12: `channels` group
- **Goal:** Channel config CRUD (DingTalk).
- **Files:** `adapters/web/routers/channels/{__init__.py,schemas.py,router.py}`
- **Done when:**
  - [ ] `GET ""` (filter `bot_id`), `POST ""` (type `dingding`), `GET/PUT /{channel_id}`, `PATCH /{channel_id}` (toggle `status`), `DELETE /{channel_id}`.
  - [ ] Envelope + `user_principal()`; stubbed; registered; contract test extended.
- **Depends on:** Task 6

## Task 13: Full verification & acceptance
- **Goal:** Confirm the served OpenAPI meets every spec acceptance criterion.
- **Files:** `tests/test_openapi_contract.py` (finalize)
- **Done when:**
  - [ ] All 7 groups present under `/openapi/v1`; every operation has `x-avernet-security`; all responses use the envelope.
  - [ ] No `stage`/connection/`entity_id` leakage; `routines` (not cron); `cluster_name` (not container).
  - [ ] `ruff`, `mypy`/`pyright` strict, and `ci_test.sh` all green; spec acceptance criteria check off.
- **Depends on:** Tasks 7–12

---

## Groups

- **Group A — Contract foundation:** Tasks 1, 2, 3
  - Theme: Served skeleton + shared envelope/pagination/security so any route can be added uniformly.
- **Group B — Bots + Milestone 1 gate:** Tasks 4, 5, 6
  - Theme: Prove the full pattern on the anchor group; concludes Milestone 1 → un-draft **PR #345** for review to unblock gateway/backend teams.
- **Group C — Configure-the-agent groups:** Tasks 7, 8, 9
  - Theme: identity, resources, mcp — defining an agent's behavior, knowledge, and tools.
- **Group D — Automate & integrate groups:** Tasks 10, 11, 12
  - Theme: routines, skills, channels — scheduling, capabilities, and external binding.
- **Group E — Verification:** Task 13
  - Theme: Final spec acceptance check across the whole served contract.
