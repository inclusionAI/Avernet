# Wave 1: Broad Coverage — Health, Repos, Routers, Services, Repositories

**Priority**: 🟢 Broadest impact — ~120 files, all testable by extending existing baseline tests or wiring existing test directories.
**Target files**: ~120. **New groups**: 3 (`health_check`, wire `open_api`, wire `websocket`).
**Infrastructure needed**: None. All targets reachable through existing HTTP API + stub plugins.

---

## Phase 1.0: Infrastructure Setup — Three New Test Groups

### Step A: Create `health_check` test group

1. Create `tests/e2e/health_check/__init__.py`

2. Register marker in `pyproject.toml`:
```toml
health_check: marks e2e tests for health check provider behavior
```

3. Add to `test-stages.sh` (after `run_e2e_websocket`):
```bash
run_e2e_health_check() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Health check tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    export SESSION_LABEL="health-check"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/health_check/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "health_check" \
        --junitxml="$REPORT_DIR/e2e-health-check.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}
```

### Step B: Wire `open_api` test group

Directory `tests/e2e/open_api/` already exists with tests.

1. Register marker: `open_api: marks e2e tests for OpenAPI endpoints`

2. Add stage function (after health_check):
```bash
run_e2e_open_api() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Open API tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    export SESSION_LABEL="open-api"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/open_api/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "open_api" \
        --junitxml="$REPORT_DIR/e2e-open-api.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}
```

### Step C: Wire `websocket` test group

Directory `tests/e2e/websocket/` already exists with tests.

1. Register marker: `websocket: marks e2e tests for WebSocket endpoints`

2. Add stage function (after open_api):
```bash
run_e2e_websocket() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E WebSocket tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    export SESSION_LABEL="websocket"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/websocket/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "websocket" \
        --junitxml="$REPORT_DIR/e2e-websocket.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}
```

### Step D: Wire into `run_e2e_tests()` chain

After the existing groups:
```bash
run_e2e_health_check "$mode" "$overlay" || ec=$((ec + $?))
run_e2e_open_api "$mode" "$overlay" || ec=$((ec + $?))
run_e2e_websocket "$mode" "$overlay" || ec=$((ec + $?))
```

**Verification**:
1. `just test-e2e-full` — new groups appear, existing open_api/websocket tests run
2. Check: `tmp/coverage/baas-e2e/{health-check,open-api,websocket}/` directories created

---

## Phase 1.1: Health Check — Bot Service + Device Providers

**Goal**: Exercise bot health check service and device provider chain.

**Files to create/modify**:
- `tests/e2e/health_check/test_bot_health.py` (new)

**What to test**:
- GET `/health/checker/bot` — healthy response with devices
- GET `/health/checker/bot` — bot with no devices
- GET `/health/checker/bot/devices` — list devices for health
- Health check with invalid bot UUID → error
- Health check pagination/filtering

**Target files** (health_check module):
- `core/service/health_check/bot/_service.py` (17.6% → 60%+)
- `core/service/health_check/bot/_service_device_provider.py` (22.2% → 60%+)
- `core/service/health_check/bot/_personal_device_provider.py` (22.6% → 60%+)
- `core/service/health_check/bot/_device_source_provider.py` (35.8% → 70%+)

**Verification**:
1. `just test-e2e-full`
2. Check: all bot health files ≥ 60%
3. Check: no test failures

---

## Phase 1.2: Health Check — Sandbox + PaaS Providers

**Goal**: Exercise sandbox device router and PaaS health providers.

**Files to create/modify**:
- `tests/e2e/health_check/test_sandbox_health.py` (new)
- `tests/e2e/health_check/test_paas_health.py` (new)

**What to test**:
- GET `/health/checker/sandbox` — list devices by platform
- GET `/health/checker/paas` — PaaS health overview
- GET `/health/checker/paas/{arca,poolab,sigma,local}` — per-provider health
- PaaS health with no devices

**Target files**:
- `core/service/health_check/sandbox/_sandbox_device_router.py` (37.8% → 70%+)
- `core/service/health_check/paas/_arca_paas_health_provider.py` (36.7% → 70%+)
- `core/service/health_check/paas/_poolab_paas_health_provider.py` (28.6% → 70%+)
- `core/service/health_check/paas/_paas_health_provider_factory.py` (61.9% → 90%+)

**Verification**:
1. `just test-e2e-full`
2. Check: all paas/sandbox health files ≥ 70%
3. Check: no test failures

---

## Phase 1.3: OpenAPI Routers — Messages, Sessions, Runs

**Goal**: Exercise open_api router paths.

**Files to create/modify**:
- Extend `tests/e2e/open_api/test_open_api_messages.py`
- Extend `tests/e2e/open_api/test_open_api_sessions.py`
- Extend `tests/e2e/open_api/test_open_api_runs.py`

**What to test**:
- Message router: create/list messages, invalid params → 422
- Session router: create/list/delete sessions, pagination
- Run router: create/get/cancel runs, error cases

**Target files**:
- `adapters/web/routers/open_api/message_router.py` (20.8% → 60%+)
- `adapters/web/routers/open_api/session_router.py` (22.5% → 60%+)
- `adapters/web/routers/open_api/run_router.py` (27.3% → 60%+)
- `adapters/web/routers/open_api/dependencies.py` (40.7% → 70%+)

**Verification**:
1. `just test-e2e-full`
2. Check: all open_api router files ≥ 60%
3. Check: no test failures

---

## Phase 1.4: Admin + Config + BCN Downlink Routers

**Goal**: Exercise admin and config management router paths.

**Files to create/modify**:
- `tests/e2e/baseline/test_admin_router.py` (new)
- Extend `tests/e2e/baseline/test_api_gateway_admin.py`
- Extend `tests/e2e/baseline/test_bcn_downlink.py`

**What to test**:
- Admin API gateway: list/create/revoke keys, duplicate handling
- Admin publish: force-cancel, force-retry
- BCN downlink: receive/validate/respond, error cases
- Config management: list/create/update/delete configs

**Target files**:
- `adapters/web/routers/admin/api_gateway_router.py` (42.2% → 70%+)
- `adapters/web/routers/admin/publish_admin_router.py` (86.7% → 90%+)
- `adapters/web/routers/bcn_downlink/bcn_router.py` (47.0% → 70%+)
- `adapters/web/routers/config_management/api_gateway_router.py` (65.7% → 90%+)
- `adapters/web/routers/config_management/qpm_config_router.py` (74.1% → 90%+)
- `adapters/web/routers/config_management/device_template_router.py` (81.7% → 90%+)

**Verification**:
1. `just test-e2e-full`
2. Check: admin/config router files ≥ 90%
3. Check: no test failures

---

## Phase 1.5: Bot Service Routers

**Goal**: Exercise bot service dispatcher and router paths.

**Files to create/modify**:
- `tests/e2e/baseline/test_bot_service_routers.py` (new)

**What to test**:
- Bot service: open_folder, cmd, http, wss, http_conn endpoints
- Bot service: start_progress_error_handler paths
- Bot service: publish router paths
- Bot runtime dispatchers: device_selector, wss, http, cmd, open_folder

**Target files**:
- `adapters/web/routers/bot_service/open_folder_router.py` (62.3% → 90%+)
- `adapters/web/routers/bot_service/cmd_router.py` (63.2% → 90%+)
- `adapters/web/routers/bot_service/http_router.py` (63.6% → 90%+)
- `adapters/web/routers/bot_service/wss_router.py` (68.0% → 90%+)
- `adapters/web/routers/bot_service/http_conn_router.py` (69.6% → 90%+)
- `adapters/web/routers/bot_service/publish_router.py` (85.1% → 90%+)
- `adapters/web/routers/bot_service/start_progress_error_handler.py` (34.5% → 60%+)
- `core/service/bot_runtime/dispatcher/_device_selector.py` (25.0% → 60%+)
- `core/service/bot_runtime/dispatcher/_wss_dispatcher.py` (39.5% → 60%+)
- `core/service/bot_runtime/dispatcher/_base_dispatcher.py` (51.5% → 80%+)
- `core/service/bot_runtime/dispatcher/_http_conn_info_dispatcher.py` (55.8% → 80%+)
- `core/service/bot_runtime/dispatcher/_fetch_start_progress_dispatcher.py` (44.4% → 70%+)
- `core/service/bot_runtime/dispatcher/_http_dispatcher.py` (78.9% → 90%+)
- `core/service/bot_runtime/dispatcher/_open_folder_dispatcher.py` (78.9% → 90%+)
- `core/service/bot_runtime/dispatcher/_cmd_dispatcher.py` (80.0% → 90%+)

**Verification**:
1. `just test-e2e-full`
2. Check: bot service router + dispatcher files ≥ 90%
3. Check: no test failures

---

## Phase 1.6: Miscellaneous Services + Internal Routers

**Goal**: Exercise internal router, relay session, tenant, auth, QPM, BCN, device binding query paths.

**Files to create/modify**:
- `tests/e2e/baseline/test_internal_routers.py` (new)
- `tests/e2e/baseline/test_misc_services.py` (new)
- Extend `tests/e2e/baseline/test_tenant_api.py`
- Extend `tests/e2e/baseline/test_qpm_config.py`
- Extend `tests/e2e/baseline/test_device_binding_query.py` (new)

**What to test**:
- Internal router: health, internal endpoints
- Relay session: CRUD, tracking
- Tenant: create/update/isolate
- QPM: set/get per bot
- BCN service: up/downlink lifecycle
- Device binding query: by bot/device, paginate
- Bot session: create/join/leave
- SSE registry: converter selection
- Callback: HTTP callback dispatch

**Target files** — all api-reachable service + router + repo files currently <90%.

**Verification**:
1. `just test-e2e-full`
2. All misc service/router files ≥ 90% where achievable
3. Check: no test failures

---

## Phase 1.7: Repositories — Data Access Layer

**Goal**: Exercise repository query paths through API calls.

**Files to create/modify**:
- `tests/e2e/baseline/test_repository_coverage.py` (new)

**What to test** (via existing API endpoints):
- Device binding repo: list by bot, filter by status, paginate
- Bot session repo: find by ID, list active, filter
- Bot run queue repo: enqueue/dequeue, query by bot/status
- Device template repo: list, filter by type, paginate
- AC bot/publish repos: query by tenant, filter
- Local user machine repo: register, find by user
- WS relay session repo: find by session, list active
- ORM model files: exercised through repo queries

**Target files** (all repository + ORM model files under `core/repository/`).

**Verification**:
1. `just test-e2e-full`
2. All repository files ≥ 90% where achievable
3. Check: no test failures

---

## Phase 1.8: Bootstrap + Config + API Models

**Goal**: Exercise bootstrap lifecycle and config paths.

**Files to create/modify**:
- `tests/e2e/baseline/test_bootstrap.py` (new)
- `tests/e2e/baseline/test_api_models.py` (new)

**What to test**:
- App startup: verify health after boot (exercises _lifecycle, _cron, _container)
- Config: overlay loading, missing config handling
- API models: Pydantic validation, serialization, enum handling

**Target files**:
- `bootstrap/_lifecycle.py` (56.2% → 80%+)
- `bootstrap/_cron.py` (70.4% → 90%+)
- `bootstrap/_container.py` (78.3% → 90%+)
- `config/_config_loader.py` (85.7% → 90%+)
- All `api/**/_models.py` and `_exceptions.py` files <90%

**Verification**:
1. `just test-e2e-full`
2. Bootstrap/config files ≥ 90%
3. API model files ≥ 90%
4. Check: no test failures

---

## Phase 1.9: PaaS Service, Facade, Factory (Quick Wins)

**Goal**: Quick wins on paas files already at 60-89%.

**Files to create/modify**:
- `tests/e2e/baseline/test_paas_services.py` (new)

**What to test** (via existing API + mock mode):
- PaaS facade: dispatch to each platform type
- PaaS factory: platform selection, mock mode
- PaaS services: Arca, Poolab, Sigma, TeClaw, Standalone — create/connect/info paths
- Hook executor: trigger paths

**Target files**:
- `core/service/paas/_arca_paas_service.py` (47.6% → 70%+)
- `core/service/paas/_poolab_paas_service.py` (83.7% → 90%+)
- `core/service/paas/_sigma_paas_service.py` (89.2% → 90%+)
- `core/service/paas/_teclaw_paas_service.py` (62.7% → 90%+)
- `core/service/paas/_standalone_paas_service.py` (69.6% → 90%+)
- `core/service/paas/_facade.py` (62.4% → 80%+)
- `core/service/paas/_factory.py` (77.1% → 90%+)
- `core/service/paas/_hook_executor.py` (43.8% → 70%+)

**Verification**:
1. `just test-e2e-full`
2. All paas files ≥ 90% (arca at 70%+)
3. Check: no test failures

---

## Phase 1.10: Final Push — Close Remaining Gaps

**Goal**: Identify and close any remaining files still under 90% after Phases 1.1–1.9.

**Files to create/modify**: Targeted tests for specific files still below 90%.

**Verification**:
1. `just test-e2e-full` — all tests pass
2. 150+ of 162 achievable files ≥ 90%
3. Merged E2E total significantly above 56%