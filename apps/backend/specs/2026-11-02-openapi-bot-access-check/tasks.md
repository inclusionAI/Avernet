# Tasks: Open API Bot Access Check

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Add the app allowlist config [x]
- **Goal:** The deployment can list which `app_name`s may call the access check. The list ships empty.
- **Files:** `src/agentclaw/community/di/openapi_bot_access_check_config.py` (new),
  `src/agentclaw/community/di/modules/openapi_bot_access_check_config_module.py` (new),
  `src/agentclaw/community/di/modules/openapi_user_id_bypass_config_module.py`,
  `src/agentclaw/community/di/container.py`,
  `src/agentclaw/community/configs/application.yaml`,
  `tests/community/di/test_openapi_bot_access_check_config.py` (new)
- **Done when:**
  - [x] `OpenApiBotAccessCheckConfig.app_names` defaults to an empty set, and the shipped `application.yaml` resolves to empty.
  - [x] The provider rejects a non-list value, non-string or blank entries, and unknown keys under `bot_access_check`.
  - [x] The user-id bypass module still accepts the `openapi` block when it contains `bot_access_check`.
  - [x] The container registers the module and resolves it eagerly, so a malformed block fails startup.
  - [x] The new config tests pass, and so do the existing bypass-config tests.
- **Depends on:** —

## Task 2: Add the access-check route [x]
- **Goal:** `GET /openapi/v1/bots/{bot_id}/access?entity_id=&user_id=` answers whether a user has access to a Bot, for allowlisted apps only, and stays out of the published schema.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/bot_access_check/{__init__,router,schemas}.py` (new),
  `.../openapi_v1/errors.py`, `.../openapi_v1/responses.py`,
  `.../openapi_v1/admission_routes.py`, `.../openapi_v1/authorization_rules.py`,
  `.../openapi_v1/__init__.py`
- **Done when:**
  - [x] The route is mounted in `_OPEN_SUBGROUPS`, ahead of the `{bot_id}` wildcard, with `include_in_schema=False`. The module is marked transitional.
  - [x] It has an `AdmissionMode.OPEN` row and a `NoCheck(...)` row with a reason.
  - [x] `entity_id` and `user_id` are required, and there is no stage input.
  - [x] A caller whose `app_name` is not on the allowlist, or who has no app on the credential, gets 403 "Forbidden". The refusal happens before any Bot lookup.
  - [x] The level comes from `resolve_operable_permission_level`. `has_access` is `level >= required_level`, and `required_level` defaults to `member`.
  - [x] No Bot at `(bot_id, entity_id)` returns 200 with `has_access=false` and `level="none"`.
  - [x] A collaborator lookup exception is not caught and becomes a 500.
  - [x] Blocking reads run off the event loop (`asyncio.to_thread`).
  - [x] `responses.py` and `authorization_rules.py` stay under 1000 lines.
- **Depends on:** Task 1

## Task 3: Route tests
- **Goal:** Pin the behavior and keep the surface-wide inventory and convention tests green.
- **Files:** `tests/community/adapters/http/openapi_v1/test_bot_access_check.py` (new)
- **Done when:**
  - [ ] Each case has a passing test: owner → owner; admin editor passes the admin bar; a member fails the admin bar but reports `member`; a Space member → `member`; a revoked editor → `none`; unknown Bot → 200 with no access; missing `entity_id` or `user_id` → 422; a non-allowlisted app or a human caller → 403; a lookup failure → 500; the route is absent from `app.openapi()`.
  - [ ] `test_admission_inventory.py`, `test_authorization_inventory.py`, `test_path_convention.py`, `test_explicit_user_id.py`, `test_openapi_error_schema.py` and `test_schema_docs.py` pass.
- **Depends on:** Task 2

## Task 4: Tests & Verification
- **Goal:** Confirm every spec acceptance criterion holds.
- **Done when:**
  - [ ] `DEPLOY_PROFILE=test uv run pytest tests/community/adapters/http/openapi_v1 tests/community/di -q` passes.
  - [ ] `./scripts/ci_test.sh` passes, with changed-line coverage ≥ 80%.
  - [ ] Every acceptance criterion in `spec.md` is checked off against a named test.
- **Depends on:** Task 3

---

## Groups

- **Group A — Config:** Task 1
  - Theme: The deployment can declare allowlisted apps; nothing is exposed yet.
- **Group B — Endpoint:** Tasks 2, 3
  - Theme: The hidden route works end to end with tests, and the surface conventions hold.
- **Group C — Verification:** Task 4
  - Theme: Final spec acceptance check.
