# E2E Test Architecture Reference

## Test Group Lifecycle

Each E2E test group follows this pattern (from `scripts/lib/test-stages.sh`):

```
run_e2e_<group>() {
    1. Set SESSION_LABEL="<group-name>"
    2. _start_app "$overlay" ["$MOCK_ENV"]   → boot server with config + optional mock
    3. _run_pytest uv run pytest tests/e2e/<group>/ -v -m "<marker>"  → run tests
    4. _stop_app                              → stop server, dump coverage
}
```

Coverage isolation: each group's coverage dumps to `$COVERAGE_E2E_DIR/$SESSION_LABEL/.coverage`.

## Creating a New Test Group (step-by-step)

### 1. Create test directory
```bash
mkdir -p tests/e2e/<group_name>
touch tests/e2e/<group_name>/__init__.py
```

### 2. Register marker in pyproject.toml
Add under `[tool.pytest.ini_options.markers]`:
```toml
<marker_name>: marks e2e tests for <description>
```
And add `--strict-markers` to `addopts` if not already present.

### 3. Add stage function in scripts/lib/test-stages.sh
```bash
run_e2e_<group_name>() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E <group description>"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    export SESSION_LABEL="<group-name>"
    _start_app "$overlay" "OPTIONAL_MOCK_ENV"
    _run_pytest uv run pytest tests/e2e/<group_name>/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "<marker_name>" \
        --junitxml="$REPORT_DIR/e2e-<group-name>.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}
```

### 4. Wire into run_e2e_tests() chain
Add after the last existing group call:
```bash
run_e2e_<group_name> "$mode" "$overlay" || ec=$((ec + $?))
```

### 5. If different config needed: create overlay
```bash
# Copy and modify
cp configs/overlays/e2e-sqlite.yaml configs/overlays/e2e-<name>.yaml
```

### 6. If mock env vars needed: pass to _start_app
```bash
_start_app "$overlay" "PAAS_MOCK_CREATE_FAILURE"  # sets PAAS_MOCK_MODE=true + PAAS_MOCK_CREATE_FAILURE=true
```

## Stub Plugin Enhancement Pattern

Stub plugins implement SPI Protocols. To add configurable behavior:

```python
# In plugins/<domain>/stub/_stub_<name>.py
import os

class StubXxxPlugin:
    def some_method(self, *args):
        if os.environ.get("BAAS_STUB_XXX_FAILURE"):
            raise SomeError("simulated failure")
        # default behavior
```

Then pass `BAAS_STUB_XXX_FAILURE` as a mock env var in the stage function.

## Existing Test Groups (for reference)

| Group | Marker | Overlay | Mock Env | Dir |
|-------|--------|---------|----------|-----|
| baseline | `e2e and baseline` | `e2e-sqlite` | none | `tests/e2e/baseline/` |
| mock-hook | `mock_paas_hook_failure` | `e2e-sqlite` | `PAAS_MOCK_HOOK_FAILURE` | `tests/e2e/mock_paas_failure/` |
| mock-create | `mock_paas_create_failure` | `e2e-sqlite` | `PAAS_MOCK_CREATE_FAILURE` | `tests/e2e/mock_paas_failure/` |
| mock-destroy | `mock_paas_destroy_failure` | `e2e-sqlite` | `PAAS_MOCK_DESTROY_FAILURE` | `tests/e2e/mock_paas_failure/` |
| mock-device-not-found | `mock_paas_device_not_found` | `e2e-sqlite` | `PAAS_MOCK_DEVICE_NOT_FOUND` | `tests/e2e/mock_paas_failure/` |

## Key Files

| File | Purpose |
|------|---------|
| `scripts/lib/test-stages.sh` | All `run_e2e_*` functions + `_merge_e2e_coverage` |
| `scripts/lib/app-lifecycle.sh` | `_start_app`, `_stop_app`, health checks |
| `scripts/app.sh` | Low-level `do_start`/`do_stop` with coverage collection |
| `scripts/lib/common.sh` | `COVERAGE_E2E_DIR`, `REPORT_DIR`, logging |
| `pyproject.toml` | pytest markers under `[tool.pytest.ini_options]` |
| `tests/e2e/conftest.py` | Shared fixtures: `http_client`, `api`, `created_bot`, `created_paas_device` |
| `configs/overlays/e2e-sqlite.yaml` | Default E2E config (all plugins stub) |
| `bootstrap/plugins/_plugin_core.py` | PluginContainer with Selectors |
| `core/service/paas/_factory.py` | PaaS service factory (checks `PAAS_MOCK_MODE`) |

## Coverage Exclusions

- `/stub/` and `/mock/` — excluded at collection time (`scripts/app.sh`: `--omit=*/stub/*,*/mock/*`)
- `/tests/`, `.pyx`, `dependency_injector` — excluded in report display (`scripts/lib/test-stages.sh`)
- `/real/` plugins — kept but deprioritized (Wave 9 only)