# Tasks: Public API — acli-managed CLIs on a bot

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Backend paths are relative to `apps/backend/`; engine paths to
`engine/adapter/src/engine/community/`.

## Task 1: [x] Engine — `AcliRunner`
- **Goal:** Run acli install / uninstall / market safely and turn its JSON into results or typed errors.
- **Files:** `core/acli/__init__.py`, `core/acli/runner.py`, `core/acli/tests/test_runner.py`
- **Done when:**
  - [x] Each operation runs the exact argv in the plan with `HOME=<parent of install_dir>/acli-home`, via `create_subprocess_exec`.
  - [x] Bad `name`, `keyword`, `install_dir` or page bounds are refused before any process starts.
  - [x] Uninstall of a name absent from `acli tool list` succeeds with `uninstalled: false`.
  - [x] Tests with a fake `acli` cover success, every error code, timeout and missing acli.
  - [x] Output is masked and truncated in logs and never returned raw.
- **Depends on:** —

## Task 2: [x] Engine — endpoints and capabilities
- **Goal:** Expose the runner to the platform.
- **Files:** `api/cli/router.py`, `api/cli/schemas.py`, `core/engine/capability.py`, `engines/openclaw/engine.py`, `engines/claude_code/engine.py`, `api/tests/test_cli_router.py`
- **Done when:**
  - [x] `POST /api/cli/acli/install`, `POST /api/cli/acli/uninstall`, `GET /api/cli/acli/market` follow the router's refusal conventions.
  - [x] The three capabilities are declared supported by openclaw and claude_code.
  - [x] Router tests cover success, refusal and bad input.
- **Depends on:** Task 1

## Task 3: [x] Backend — install directory and AgentPass add/remove
- **Goal:** Read and validate the install directory; add `add_cli` / `remove_cli` to `CliPassportScopeReconciler`, the existing class that is the only code allowed to write a bot's AgentPass CLI list (it rebuilds the whole MCP + CLI list and writes it in one call, because AgentPass replaces the list on every update).
- **Files:** `src/agentclaw/community/core/mcp/services/cli_capabilities.py`, `.../cli_passport_scope.py`, `tests/community/core/mcp/services/test_cli_capabilities.py`, the reconciler's test module
- **Done when:**
  - [x] The resolver exposes `managed_bin_dir`; an invalid value fails loading.
  - [x] `add_cli` / `remove_cli` carry the full MCP scope and all other CLI identities.
  - [x] `reconcile` behaviour and log lines unchanged (existing tests pass).
- **Depends on:** —

## Task 4: [x] Backend — engine port
- **Goal:** Call the engine endpoints and map every response.
- **Files:** `src/agentclaw/community/core/acli_clis/__init__.py`, `core/acli_clis/port.py`, `core/acli_clis/arca_port.py`, `tests/community/core/acli_clis/test_arca_port.py`
- **Done when:**
  - [x] One test per mapping in the plan (`not_running`, `engine_unsupported`, engine error code, `engine_unreachable`, success).
- **Depends on:** Task 2 (contract)

## Task 5: [x] Backend — shared permission check and `AcliCliService`
- **Goal:** Orchestrate list / search / add / remove with the plan's ordering and rollbacks. "Guard" here means the permission check run before every write — the caller owns the bot, the bot is an active service bot, and its engine can run acli. The CLI identity switch already does this check inline; it moves to `core/caller_identity/guard.py` so both use the same code.
- **Files:** `src/agentclaw/community/core/caller_identity/guard.py`, `core/caller_identity/service.py` (use the guard), `core/acli_clis/service.py`, `core/acli_clis/errors.py`, `tests/community/core/acli_clis/test_service.py`
- **Done when:**
  - [x] Every spec acceptance criterion under Search, List, Add, Remove and Rollback failures has a passing test.
  - [x] `update_cli_call_type` uses the shared guard; its existing tests pass; `service.py` does not grow.
- **Depends on:** Tasks 3, 4

## Task 6: Backend — public router and registration
- **Goal:** Publish the routes.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/bots/acli_clis.py`, `.../bots/schemas_acli_clis.py`, `openapi_v1/__init__.py`, `authorization_rules.py`, `admission_routes.py`, inventories, shared status map, schema snapshots, the four DI device modules, `tests/community/adapters/http/openapi_v1/test_acli_clis_endpoints.py`
- **Done when:**
  - [ ] Routes and status codes match the plan; `/clis/market` is not captured by `/clis/{cli_code}`.
  - [ ] Authorization, admission, inventory and endpoint-coverage gates pass.
  - [ ] The call-type route is unchanged.
- **Depends on:** Task 5

## Task 7: Docs
- **Goal:** Document the engine contract and public routes.
- **Files:** `docs/bot-config-manifest/engine-requirements.zh-CN.md`, `docs/openapi-v1/README.md`, `docs/openapi-v1/README.zh-CN.md`
- **Done when:**
  - [ ] Engine endpoints, install-directory rationale, public routes and error codes are documented.
- **Depends on:** Tasks 2, 6

## Task 8: Verification
- **Goal:** Confirm the feature meets the spec.
- **Done when:**
  - [ ] Every spec acceptance criterion maps to a passing test and is checked off in `spec.md`.
  - [ ] Backend touched packages, `tests/community/adapters/http/openapi_v1/`, architecture tests and Ruff pass; engine `core/acli` and `api` tests pass.
  - [ ] Every touched source file is under 1,000 lines.
  - [ ] The PR states what was not run (real acli install) and lists the manual sandbox check.
- **Depends on:** Tasks 1–7

---

## Groups

- **Group A — Engine:** Tasks 1, 2
  - Theme: a running bot can install, uninstall and search acli tools on request.
- **Group B — Grant and orchestration:** Tasks 3, 4, 5
  - Theme: the backend adds and removes a tool atomically across AgentPass and the container.
- **Group C — Public API:** Tasks 6, 7
  - Theme: callers can use it, and it is documented.
- **Group D — Verification:** Task 8
  - Theme: final spec acceptance check.
