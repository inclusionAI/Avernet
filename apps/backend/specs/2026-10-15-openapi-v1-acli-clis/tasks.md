# Tasks: Public API — acli-managed CLIs on a bot

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Paths under `src/` and `tests/` are relative to `apps/backend/` unless they
start with `engine/`.

## Task 1: Expose catalog, install spec and defaults from the manifest resolver
- **Goal:** Let callers read the catalog and each tool's install definition without re-parsing the YAML.
- **Files:** `src/agentclaw/community/core/mcp/services/cli_capabilities.py`,
  `tests/community/core/mcp/services/test_cli_capabilities.py`
- **Done when:**
  - [ ] `catalog_items()`, `install_spec(code)`, `default_cli_codes(engine, template)` exist and return what the plan specifies.
  - [ ] `managed_bin_dir` is validated (absolute, no `..`) at load; an invalid value fails loading.
  - [ ] Existing resolver tests still pass; new tests cover each accessor and the validation.
- **Depends on:** —

## Task 2: Add `add_cli` / `remove_cli` to the scope reconciler
- **Goal:** Make the reconciler the single writer for adding and removing one CLI.
- **Files:** `src/agentclaw/community/core/mcp/services/cli_passport_scope.py`,
  `tests/community/core/mcp/services/test_cli_passport_scope.py` (or the existing reconciler test module)
- **Done when:**
  - [ ] `add_cli` adds the catalog item with `owner` identity; re-adding leaves the scope unchanged and skips the AgentPass write.
  - [ ] `remove_cli` drops only the target code.
  - [ ] Both carry the full MCP scope with identities and all other CLI identities into `update_passport`.
  - [ ] `reconcile` behaviour and its log lines are unchanged (existing tests pass).
  - [ ] AgentPass failure propagates from both methods.
- **Depends on:** Task 1

## Task 3: Engine — `AcliInstaller`
- **Goal:** Install and uninstall one acli tool in the container, safely.
- **Files:** `engine/adapter/src/engine/community/core/acli/__init__.py`, `core/acli/installer.py`, `core/acli/tests/test_installer.py`
- **Done when:**
  - [ ] Install runs exactly `acli install <package> --install-dir <dir> --yes` via argv (no shell), then probes `<dir>/<executable> --version`.
  - [ ] Invalid package/executable/dir is refused before any process starts.
  - [ ] Outcomes covered by tests with a fake `acli` on a temp `PATH`: success, install non-zero, probe failure, acli missing (`acli_unavailable`), timeout.
  - [ ] Uninstall removes the file; an absent file is success; it never follows a path outside `<dir>`.
  - [ ] Captured output is truncated and credential-masked before logging; it never appears in a returned reason.
- **Depends on:** —

## Task 4: Engine — endpoints and capabilities
- **Goal:** Expose the installer to the platform.
- **Files:** `engine/adapter/src/engine/community/api/cli/router.py`, `api/cli/schemas.py`,
  `core/engine/capability.py`, `engines/openclaw/engine.py`, `engines/claude_code/engine.py`,
  `api/tests/test_cli_router.py`
- **Done when:**
  - [ ] `POST /api/cli/acli/install` and `POST /api/cli/acli/uninstall` exist; refusals are `200` + `success:false`, never `404`.
  - [ ] `CLI_ACLI_INSTALL` / `CLI_ACLI_UNINSTALL` are declared supported by openclaw and claude_code.
  - [ ] Router tests cover success, refusal and bad input; `router.py` stays under 1,000 lines.
- **Depends on:** Task 3

## Task 5: Backend — install port
- **Goal:** Call the engine endpoints and turn every response into an outcome.
- **Files:** `src/agentclaw/community/core/acli_clis/__init__.py`, `core/acli_clis/port.py`,
  `core/acli_clis/arca_port.py`, `tests/community/core/acli_clis/test_arca_port.py`
- **Done when:**
  - [ ] `AcliInstallPort` protocol and `InstallOutcome` exist as in the plan.
  - [ ] With a fake transport: success → `installed`/`uninstalled`; no device → `not_running`; `404` → `failed`/`engine_unsupported`; non-2xx, `success:false`, timeout → `failed`.
  - [ ] The port never raises for an engine refusal.
- **Depends on:** Task 4 (endpoint contract)

## Task 6: Backend — `AcliCliService`, shared guard and errors
- **Goal:** Orchestrate list / add / remove with the spec's ordering and failure rules.
- **Files:** `src/agentclaw/community/core/acli_clis/service.py`, `core/acli_clis/errors.py`,
  `src/agentclaw/community/core/caller_identity/` (extract the owner/active/profile guard into a small shared helper; `service.py` uses it),
  `tests/community/core/acli_clis/test_service.py`
- **Done when:**
  - [ ] Every spec acceptance criterion under Catalog, List, Add and Remove has a test against fake reconciler + fake port.
  - [ ] `update_cli_call_type` uses the extracted guard and its existing tests pass unchanged.
  - [ ] `core/caller_identity/service.py` does not grow.
- **Depends on:** Tasks 2, 5

## Task 7: Backend — public router and registration
- **Goal:** Publish the four new routes.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/bots/acli_clis.py`,
  `openapi_v1/bots/schemas_acli_clis.py`, `openapi_v1/__init__.py`,
  `openapi_v1/authorization_rules.py`, `openapi_v1/admission_routes.py`,
  shared HTTP status map, route/endpoint inventories and schema snapshots,
  DI device modules (`di/modules/infrastructure/{community,singlebox,test}/devices.py`, `di/modules/testing_devices_module.py`),
  `tests/community/adapters/http/openapi_v1/test_acli_clis_endpoints.py`
- **Done when:**
  - [ ] The routes in the plan's table respond as specified, including status codes for each error.
  - [ ] The catalog route is reachable and not captured by the `{bot_id}` wildcard.
  - [ ] Authorization, admission, inventory and endpoint-coverage gates pass with the new routes.
  - [ ] The existing call-type route is unchanged.
- **Depends on:** Task 6

## Task 8: Docs
- **Goal:** Record the new public routes and engine contract.
- **Files:** `apps/backend/docs/bot-config-manifest/engine-requirements.zh-CN.md`,
  `apps/backend/docs/openapi-v1/README.md`, `apps/backend/docs/openapi-v1/README.zh-CN.md`
- **Done when:**
  - [ ] §4 documents both acli endpoints and the install-directory exception.
  - [ ] The OpenAPI README lists the CLI routes with their error codes and the restart limitation.
- **Depends on:** Tasks 4, 7

## Task 9: Verification
- **Goal:** Confirm the feature meets the spec.
- **Done when:**
  - [ ] Every spec acceptance criterion maps to a passing test (checked off in `spec.md`).
  - [ ] Backend: touched packages, `tests/community/adapters/http/openapi_v1/`, architecture tests, Ruff — all pass.
  - [ ] Engine: `core/acli`, `api` tests pass.
  - [ ] Every touched source file is under 1,000 lines.
  - [ ] What could not be run (real acli install in E2E) is stated for the PR.
- **Depends on:** Tasks 1–8

---

## Groups

- **Group A — Grant:** Tasks 1, 2
  - Theme: the backend can add and remove one CLI in AgentPass safely.
- **Group B — Install in the container:** Tasks 3, 4, 5
  - Theme: the engine can install/uninstall an acli tool and the backend can ask it to.
- **Group C — Public API:** Tasks 6, 7, 8
  - Theme: callers can list, add, update and remove CLIs end to end, documented.
- **Group D — Verification:** Task 9
  - Theme: final spec acceptance check.
