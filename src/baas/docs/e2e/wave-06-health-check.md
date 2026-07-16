# Wave 6: Health Check — Final Push to 90%

**Priority**: 🟡 Medium. Health check logic, 9 files with significant gaps.
**Target files**: 9 plus repositories, routers, remaining services. **New groups**: 1 (`health_check`).
**Prerequisite**: None — health check tests are API-driven. We test HTTP endpoints (`/health`, `/health/checker/*`), not internal functions directly.
**Estimated phases**: 10 (health + repos + routers + services merged).

---

## Phase 5.0: Infrastructure Setup — New Test Group

### Step A: Create test group

1. Create `tests/e2e/health_check/__init__.py`

2. Register marker in `pyproject.toml`:
```toml
health_check: marks e2e tests for health check provider behavior
```

3. Add to `test-stages.sh`:
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

### Step B: Wire into `run_e2e_tests()`

After `run_e2e_bot_run_concurrency`:
```bash
run_e2e_health_check "$mode" "$overlay" || ec=$((ec + $?))
```

### Step C: Note on stub health providers

The health check providers are wired through `PaasHealthProviderFactory`. In stub mode, the factory returns stub implementations. To control their behavior, we use the existing health check API endpoints and test the actual HTTP responses. No new stub plugins needed — we test through the `/health` and `/health/checker/*` endpoints.

**Verification**:
1. `just test-e2e-full` — new group appears, 0 tests
2. Check: `tmp/coverage/baas-e2e/health-check/` directory created

---

## Phase 5.1: Bot Health Check Service

**Goal**: Exercise `core/service/health_check/bot/_service.py` (17.6% → 70%+)

**Files to create/modify**:
- `tests/e2e/health_check/test_bot_health_service.py` (new)

**What to test**:
- GET `/health/checker/bot` — healthy response
- Bot health check with no devices
- Bot health check with multiple devices
- Bot health check with device in different states
- Bot health check with invalid bot UUID
- Health check pagination/filtering

**Verification**:
1. `just test-e2e-full`
2. Check: `health_check/bot/_service.py` ≥ 70%
3. Check: no test failures

---

## Phase 5.2: Bot Health — Device Providers

**Goal**: Exercise `_service_device_provider.py` (22.2% → 70%+), `_personal_device_provider.py` (22.6% → 70%+), `_device_source_provider.py` (35.8% → 80%+)

**Files to create/modify**:
- `tests/e2e/health_check/test_bot_device_providers.py` (new)

**What to test**:
- GET `/health/checker/bot/devices` — list devices for bot health
- Service device provider: resolve devices for service bot
- Personal device provider: resolve devices for personal bot
- Device source provider: aggregate from multiple sources
- Provider with no available devices

**Verification**:
1. `just test-e2e-full`
2. Check: `_service_device_provider.py` ≥ 70%
3. Check: `_personal_device_provider.py` ≥ 70%
4. Check: `_device_source_provider.py` ≥ 80%
5. Check: no test failures

---

## Phase 5.3: Sandbox Device Router

**Goal**: Exercise `health_check/sandbox/_sandbox_device_router.py` (37.8% → 80%+)

**Files to create/modify**:
- `tests/e2e/health_check/test_sandbox_device_router.py` (new)

**What to test**:
- GET `/health/checker/sandbox` — list sandbox devices
- Sandbox device router: query by platform type
- Sandbox device router: filter by status
- Sandbox device router: no results
- Sandbox device router: with templates

**Verification**:
1. `just test-e2e-full`
2. Check: `_sandbox_device_router.py` ≥ 80%
3. Check: no test failures

---

## Phase 5.4: PaaS Health Providers

**Goal**: Exercise PaaS-specific health providers via the health checker API

**Files to create/modify**:
- `tests/e2e/health_check/test_paas_health_providers.py` (new)

**What to test**:
- GET `/health/checker/paas` — PaaS health overview
- GET `/health/checker/paas/{provider}` for each: arca, poolab, sigma, local
- PaaS health provider factory: selection logic
- PaaS health provider with no devices
- PaaS health provider configuration

**Verification**:
1. `just test-e2e-full`
2. Check: `_arca_paas_health_provider.py` ≥ 80% (target: 36.7% → 80%)
3. Check: `_poolab_paas_health_provider.py` ≥ 80% (target: 28.6% → 80%)
4. Check: `_paas_health_provider_factory.py` ≥ 90% (target: 61.9% → 90%)
5. Check: no test failures

---

## Phase 5.5: Health Check — Final Push to 90%

**Goal**: Close remaining gaps on all health check files

**Files to create/modify**:
- Extend Phase 5.1–5.4 test files with additional edge cases
- Add error-path tests: invalid params, missing entities, partial failures

**Verification**:
1. `just test-e2e-full`
2. Check: all 9 health_check files ≥ 90%
3. Check: no test failures

---

## Wave 5 Completion Check

After all phases complete:
1. `just test-e2e-full` — all tests pass
2. All 9 `core/service/health_check/**/*.py` files ≥ 90%
3. New `health_check` group produces coverage