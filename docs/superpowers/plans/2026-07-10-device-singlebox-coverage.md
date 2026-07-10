# Device Singlebox Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Avernet's real `singlebox_coverage.sh` run the devices acceptance suite, report honest module-level Core/Router/Plugin metrics, and raise devices coverage through a meaningful live local-provider lifecycle.

**Architecture:** Keep runtime evidence at existing boundaries: Python line data comes from coverage-instrumented Backend processes and Router hits come from FastAPI middleware. A small manifest-driven reporter filters the generated artifacts into module metrics; acceptance tests create real state through Backend and BaaS instead of seeding the database. Device Plugin API remains `not_applicable` until a real device-owned plugin boundary is exercised.

**Tech Stack:** Bash, Python 3.12, pytest, coverage.py JSON, FastAPI runtime route-hit JSONL, YAML.

## Global Constraints

- Work from Avernet `origin/dev`, not the older OCB-pinned mock-only coverage script.
- Do not add coverage imports or test-only branches to Core, Router handlers, or business services.
- Do not introduce `exclude_paths` or remove Router APIs from the denominator.
- All final metrics must come from `scripts/ci/singlebox_coverage.sh --mode real` artifacts.
- The real open-source singlebox creates a `local` provider binding; instance-list and restart success remain BaaS/Teclaw-only capability gaps.
- Existing default CI behavior must keep working when no module is selected.

---

### Task 1: Measure the Real Devices Baseline

**Files:**
- Read: `scripts/ci/singlebox_coverage.sh`
- Read: `src/backend/tests/community/acceptance/devices/test_device_query_lifecycle.py`
- Artifact: `/tmp/avernet-device-coverage-baseline/reports/backend-coverage.json`
- Artifact: `/tmp/avernet-device-coverage-baseline/raw/backend/router_hits.jsonl`

**Interfaces:**
- Consumes: `SINGLEBOX_COVERAGE_ACCEPTANCE_TARGET`, `SINGLEBOX_COVERAGE_ROOT`
- Produces: exact baseline Core percentage and distinct devices Router hit set

- [ ] **Step 1: Run the existing live devices target**

```bash
SINGLEBOX_COVERAGE_ACCEPTANCE_TARGET=tests/community/acceptance/devices \
SINGLEBOX_COVERAGE_ROOT=/tmp/avernet-device-coverage-baseline \
bash scripts/ci/singlebox_coverage.sh --mode real
```

Expected: the standalone stack starts, four existing devices acceptance tests pass, and Backend/BaaS coverage artifacts are generated.

- [ ] **Step 2: Calculate the baseline from generated artifacts**

Use a structured JSON parser to sum `covered_lines` and `num_statements` for files whose normalized path contains `src/agentclaw/community/core/devices/`. Deduplicate Router hit `key` values beginning with an operation declared by the devices router.

Expected: record exact Core numerator/denominator/percentage and Router covered keys. If the run fails before artifacts exist, stop and diagnose the singlebox failure before editing tests.

---

### Task 2: Add Manifest-Driven Module Reporting

**Files:**
- Create: `scripts/ci/singlebox_coverage_modules.yaml`
- Create: `scripts/ci/singlebox_coverage_report.py`
- Create: `scripts/ci/tests/test_singlebox_coverage_report.py`
- Modify: `scripts/ci/singlebox_coverage.sh`
- Modify: `scripts/test_singlebox_coverage_gate.sh`

**Interfaces:**
- Consumes: coverage.py JSON, Router/Plugin JSONL, module name, manifest
- Produces: `summary.json.modules.devices`, `summary.md`, and `dashboard.html` with Core/Router/Plugin module metrics

- [ ] **Step 1: Write failing reporter tests**

Create synthetic coverage and Router hit files and assert:

```python
report = build_module_report(
    manifest=manifest,
    module_name="devices",
    coverage=coverage,
    router_hits=router_hits,
    plugin_hits=[],
)
assert report["core"]["covered"] == 3
assert report["core"]["total"] == 5
assert report["core"]["percent"] == 60.0
assert report["router_api"]["covered"] == 2
assert report["router_api"]["total"] == 3
assert report["plugin_api"]["status"] == "not_applicable"
```

Also assert duplicate Router hits count once, unrelated files/routes are ignored, and an unknown module fails with a clear error.

- [ ] **Step 2: Run reporter tests and verify RED**

```bash
cd src/backend
uv run pytest ../../scripts/ci/tests/test_singlebox_coverage_report.py -q
```

Expected: FAIL because `singlebox_coverage_report.py` does not exist.

- [ ] **Step 3: Implement the minimal reporter and device manifest**

The manifest declares:

```yaml
modules:
  devices:
    system: backend
    core_paths:
      - src/agentclaw/community/core/devices/
    router_api:
      items:
        - POST /api/v1/devices
        - POST /api/v1/devices/{binding_id:int}/release
        - GET /api/v1/devices
        - GET /api/v1/devices/provider-inventory
        - GET /api/v1/devices/{binding_id:int}
        - GET /api/v1/devices/by-id/{device_id}
        - GET /api/v1/devices/{binding_id:int}/connection
        - GET /api/v1/devices/bots/{bot_id}/connection
        - GET /api/v1/devices/connectable
        - GET /api/v1/devices/connectable_admin
        - POST /api/v1/devices/callback/alive
        - POST /api/v1/devices/callback/status
        - POST /api/v1/devices/callback/bootstrap-auth
        - POST /api/v1/devices/exec_shell
        - POST /api/v1/devices/batch/env
        - GET /api/v1/devices/bots/{bot_id}/instances
        - GET /api/v1/devices/{binding_id:int}/instances
        - POST /api/v1/devices/{binding_id:int}/restart
    plugin_api:
      status: not_applicable
      reason: Device lifecycle has no honestly attributable runtime Plugin API denominator yet.
      items: []
    thresholds:
      core_min_percent: 43.36
      router_min_percent: 44.44
```

Implement pure functions for path normalization, line aggregation, JSONL key loading, ratio formatting, threshold validation, summary merge, Markdown rendering, and HTML rendering.

- [ ] **Step 4: Run reporter tests and verify GREEN**

```bash
cd src/backend
uv run pytest ../../scripts/ci/tests/test_singlebox_coverage_report.py -q
```

Expected: all reporter tests pass.

- [ ] **Step 5: Wire optional module reporting into the Bash entrypoint**

Add options:

```text
--acceptance-target PATH
--module NAME
```

When `--module devices` is present, run the reporter after coverage combine and summary creation. When absent, preserve current default CI output. Extend the shell stub test so it verifies selected module arguments reach the reporter and required module artifacts are checked.

- [ ] **Step 6: Verify the coverage gate tests**

```bash
bash scripts/test_singlebox_coverage_gate.sh
```

Expected: PASS.

- [ ] **Step 7: Commit the reporter**

```bash
git add scripts/ci/singlebox_coverage_modules.yaml \
  scripts/ci/singlebox_coverage_report.py \
  scripts/ci/tests/test_singlebox_coverage_report.py \
  scripts/ci/singlebox_coverage.sh \
  scripts/test_singlebox_coverage_gate.sh
git commit -m "feat(ci): report device singlebox coverage"
```

---

### Task 3: Add the Live Device Lifecycle Acceptance Story

**Files:**
- Modify: `src/backend/tests/community/acceptance/devices/test_device_query_lifecycle.py`
- Reuse: `src/backend/tests/community/acceptance/_fixtures/live_personal_bot.py`
- Test: `src/backend/tests/community/e2e/test_devices_flows.py`

**Interfaces:**
- Consumes: `create_live_personal_bot`, live Backend/BaaS stack, devices HTTP APIs
- Produces: one live lifecycle test that creates real product state and reaches at least eight distinct devices Router operations across the suite

- [ ] **Step 1: Add the live lifecycle test before changing production code**

The test must:

```python
bot = create_live_personal_bot(client, user_id=user_id, bot_name_prefix="Device Acceptance")
binding_id = bot["binding_id"]
device_id = bot["device_id"]
```

Then validate owner list/get/by-id/connection/connectable responses, validate another user receives the ownership error, assert local-provider instances/restart return the documented capability error, release the binding, and read it back as `RELEASED`.

- [ ] **Step 2: Run the new acceptance test against the real stack**

```bash
RUN_ACCEPTANCE=1 uv run pytest \
  tests/community/acceptance/devices/test_device_query_lifecycle.py::test_device_live_local_provider_lifecycle \
  -v -s
```

Expected: either PASS using existing product behavior, or a concrete product-contract failure. Do not modify production code unless the failure demonstrates a real defect; if so, add the narrowest failing regression test first.

- [ ] **Step 3: Run fast device regressions**

```bash
uv run pytest tests/community/e2e/test_devices_flows.py -q
```

Expected: four existing E2E tests pass.

- [ ] **Step 4: Commit the acceptance story**

```bash
git add src/backend/tests/community/acceptance/devices/test_device_query_lifecycle.py
git commit -m "test(device): cover live singlebox lifecycle"
```

---

### Task 4: Run the Final Real Coverage Gate

**Files:**
- Artifact: `/tmp/avernet-device-coverage-final/reports/summary.json`
- Artifact: `/tmp/avernet-device-coverage-final/reports/dashboard.html`
- Artifact: `/tmp/avernet-device-coverage-final/reports/backend-coverage.json`

**Interfaces:**
- Consumes: Tasks 2 and 3
- Produces: verified final devices Core/Router/Plugin metrics from Avernet's canonical script

- [ ] **Step 1: Run the canonical script**

```bash
bash scripts/ci/singlebox_coverage.sh \
  --mode real \
  --module devices \
  --acceptance-target tests/community/acceptance/devices \
  --coverage-root /tmp/avernet-device-coverage-final
```

Expected: acceptance JUnit has no failures or skips; Core is at least 43.36%,
Router is at least 44.44%, and Plugin API is `not_applicable` with the manifest
reason.

- [ ] **Step 2: Cross-check report consistency**

Verify `summary.json`, `summary.md`, and `dashboard.html` show identical devices metrics. Confirm the Router covered-item list is a subset of the 18 manifest entries and line totals equal a direct sum from `backend-coverage.json`.

- [ ] **Step 3: Run final focused verification**

```bash
bash scripts/test_singlebox_coverage_gate.sh
cd src/backend
uv run pytest ../../scripts/ci/tests/test_singlebox_coverage_report.py \
  tests/community/e2e/test_devices_flows.py -q
```

Expected: all tests pass with no failures.

- [ ] **Step 4: Record final status**

```bash
git status --short --branch
git log --oneline --decorate -5
```

Expected: only intentional committed changes remain; generated runtime artifacts are outside the repository or ignored.
