# Wave 4: PaaS Service Layer

**Priority**: 🔴 Critical. `_local_paas_service.py` is 20.5% on 516 lines — a 410-line gap.
**Target files**: 5. **New groups**: 1 (`paas_operations`).
**Prerequisite**: None — `LocalPaasService` is constructed directly (not through stub plugin). TeClaw stub already exists.
**Estimated phases**: 5.

---

## Phase 3.0: Infrastructure Setup — New Test Group

**Goal**: Create the `paas_operations` test group. No stub enhancement needed — `LocalPaasService` is constructed directly
(not through an SPI/stub pattern), and `TeClaw` stub already exists at `plugins/sandbox/teclaw/_stub.py`.

**Note on error paths**: `LocalPaasService` error paths are tested through the mock mode already (Wave 3.3).
The existing `PAAS_MOCK_*` env vars inject errors at the factory level before `LocalPaasService` is even called.

### Step A: Create test group

1. Create `tests/e2e/paas_operations/__init__.py`

2. Register marker in `pyproject.toml` under `[tool.pytest.ini_options.markers]`:
```toml
paas_operations: marks e2e tests for PaaS service layer operations
```

3. Add to `run_e2e_tests()` chain in `scripts/lib/test-stages.sh` (after `run_e2e_mock_failure_device_not_found`):
```bash
run_e2e_paas_operations() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E PaaS operations tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    export SESSION_LABEL="paas-operations"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/paas_operations/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "paas_operations" \
        --junitxml="$REPORT_DIR/e2e-paas-operations.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}
```
And add to wire: `run_e2e_paas_operations "$mode" "$overlay" || ec=$((ec + $?))`

**Verification**:
1. `just test-e2e-full` — new group appears in output, 0 tests (empty dir)
2. Check: `tmp/coverage/baas-e2e/paas-operations/` directory created with coverage file

---

## Phase 3.1: Local PaaS Service — Create/Connect Paths

**Goal**: Exercise `_local_paas_service.py` create, connect, and info paths (20.5% → 50%+)

**Files to create/modify**:
- `tests/e2e/paas_operations/test_local_paas_create.py` (new)

**What to test**:
- Create local PaaS device with valid config
- Create local PaaS device with missing required fields
- Connect to existing local PaaS device
- Get device info for local PaaS device
- List local PaaS instances

**Verification**:
1. `just test-e2e-full`
2. Check: `_local_paas_service.py` ≥ 50%
3. Check: no test failures

---

## Phase 3.2: Local PaaS Service — Destroy/Scale/Restart Paths

**Goal**: Exercise `_local_paas_service.py` lifecycle operations (50% → 70%+)

**Files to create/modify**:
- `tests/e2e/paas_operations/test_local_paas_lifecycle.py` (new)

**What to test**:
- Restart local PaaS device
- Scale up local PaaS device
- Scale down local PaaS device
- Destroy local PaaS device
- Update local PaaS device config
- Idempotent destroy (already destroyed)

**Verification**:
1. `just test-e2e-full`
2. Check: `_local_paas_service.py` ≥ 70%
3. Check: no test failures

---

## Phase 3.3: Local PaaS Service — Error Paths

**Goal**: Exercise `_local_paas_service.py` error handling (70% → 90%+)

**Files to create/modify**:
- `tests/e2e/paas_operations/test_local_paas_errors.py` (new)

**What to test**:
- Create with invalid config → error
- Create when stub reports failure (env var) → error
- Destroy when device not found → error/idempotent
- Scale beyond limits → error
- Command execution on destroyed device → error

**Verification**:
1. `just test-e2e-full`
2. Check: `_local_paas_service.py` ≥ 90%
3. Check: no test failures

---

## Phase 3.4: TeClaw + Standalone PaaS Services

**Goal**: Exercise `_teclaw_paas_service.py` (62.7% → 90%+) and `_standalone_paas_service.py` (69.6% → 90%+)

**Files to create/modify**:
- `tests/e2e/paas_operations/test_teclaw_paas.py` (new)
- `tests/e2e/paas_operations/test_standalone_paas.py` (new)

**What to test**:
- TeClaw: create, connect, resolve_ws, update, destroy, error paths
- Standalone: create, connect, get_info, list_instances, destroy, error paths

**Verification**:
1. `just test-e2e-full`
2. Check: `_teclaw_paas_service.py` ≥ 90%
3. Check: `_standalone_paas_service.py` ≥ 90%
4. Check: no test failures

---

## Phase 3.5: PaaS Facade + Factory

**Goal**: Exercise remaining paths in `_facade.py` (62.4% → 90%+) and `_factory.py` (77.1% → 90%+)

**Files to create/modify**:
- `tests/e2e/paas_operations/test_paas_facade.py` (new)

**What to test**:
- Facade dispatch to each platform type (Local, Arca, K8s, Desktop, Poolab, Sigma, TeClaw)
- Facade with invalid platform type → error
- Factory platform selection for each type
- Factory with mock mode enabled
- Factory with unsupported platform

**Verification**:
1. `just test-e2e-full`
2. Check: `_facade.py` ≥ 90%
3. Check: `_factory.py` ≥ 90%
4. Check: no test failures

---

## Wave 3 Completion Check

After all phases complete:
1. `just test-e2e-full` — all tests pass
2. `_local_paas_service.py` ≥ 90%
3. `_facade.py` ≥ 90%
4. `_factory.py` ≥ 90%
5. `_teclaw_paas_service.py` ≥ 90%
6. `_standalone_paas_service.py` ≥ 90%