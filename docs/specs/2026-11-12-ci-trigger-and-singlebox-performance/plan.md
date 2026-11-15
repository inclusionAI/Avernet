# CI trigger isolation and conservative Singlebox performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent BCS-only workflow edits from running Backend unit tests and remove four measured avoidable costs in Singlebox coverage without changing any test or coverage contract.

**Architecture:** Move the existing Backend job into a dedicated workflow so path-based detection is precise. Keep the canonical Singlebox entrypoint, overlap only disjoint coverage-report producers, reuse fresh panel output, retain `rust-cache` as the sole Cargo cache owner, and exclude only reconstructable artifact dependencies.

**Tech Stack:** GitHub Actions YAML, Bash, Python 3 standard library, pytest, existing Singlebox shell fixtures.

**Spec:** [CI trigger isolation and conservative Singlebox performance](spec.md)

## Global constraints

- Follow `docs/agents/development.md` and `docs/agents/validation.md`.
- Do not change coverage thresholds, denominators, test commands, test inventory, artifact verification or required-check semantics.
- Do not parallelize acceptance/shared stories or the three `cargo llvm-cov report` calls.
- Do not modify the already oversized `singlebox/singlebox.sh`.
- Keep every added/modified source file below 1,000 physical lines.
- The user explicitly approved the spec without committing it. Do not run any git mutation or add commit steps; repository rules require separate confirmation for each git mutation.

---

### Task 1: Define Backend workflow isolation regressions

**Files:**
- Modify: `singlebox/ci/tests/test_unit_test_workflow_diff.py`
- Modify: `singlebox/ci/tests/test_refactory_collaboration_workflow_triggers.py`
- Modify: `singlebox/ci/tests/test_bcs_ci_build_reuse.py`
- Modify: `singlebox/ci/tests/test_bcs_ci_change_detection.py`
- Modify: `singlebox/ci/tests/test_bcs_cargo_test_tools.py`

- [x] **Step 1: Add failing dedicated-workflow and path-selection assertions**

Add this constant beside `WORKFLOW_PATH` in `test_unit_test_workflow_diff.py`:

```python
BACKEND_WORKFLOW_PATH = REPO_ROOT / ".github/workflows/backend-unit-tests.yml"
BACKEND_DIFF_PATHS = (
    "apps/backend",
    "singlebox/ci/legacy_skill_compatibility.sh",
    ".github/workflows/backend-unit-tests.yml",
)
```

In `test_backend_worker_crashes_fail_fast` and `test_backend_test_failures_keep_diagnostics_before_job_timeout`, read the Backend job from `BACKEND_WORKFLOW_PATH` instead of extracting it from `WORKFLOW_PATH`.

Add this executable path-selection test to the same class:

```python
    def test_backend_pathspec_ignores_bcs_workflow_but_catches_backend_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            repository.mkdir()
            _git(repository, "init", "--initial-branch=target")
            _git(repository, "config", "user.name", "CI Test")
            _git(repository, "config", "user.email", "ci-test@example.com")
            _write(repository, "apps/bcs/baseline.txt", "baseline\n")
            _write(repository, ".github/workflows/unit-tests.yml", "bcs: baseline\n")
            for path in BACKEND_DIFF_PATHS:
                _write(repository, f"{path}/baseline.txt" if path == "apps/backend" else path, "baseline\n")
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "baseline")

            _write(repository, ".github/workflows/unit-tests.yml", "bcs: changed\n")
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "BCS CI only")
            self.assertEqual(
                _git(repository, "diff", "--name-only", "HEAD^1", "--", *BACKEND_DIFF_PATHS),
                "",
            )

            _write(repository, ".github/workflows/backend-unit-tests.yml", "backend: changed\n")
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "Backend CI")
            self.assertEqual(
                _git(repository, "diff", "--name-only", "HEAD^1", "--", *BACKEND_DIFF_PATHS),
                ".github/workflows/backend-unit-tests.yml",
            )
```

Add a static contract test proving the new workflow owns the Backend detector and retains the stable check name:

```python
    def test_backend_workflow_owns_only_backend_change_detection(self) -> None:
        workflow = BACKEND_WORKFLOW_PATH.read_text(encoding="utf-8")
        shared = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("name: Backend unit tests", workflow)
        self.assertIn(".github/workflows/backend-unit-tests.yml", workflow)
        self.assertIn("apps/backend singlebox/ci/legacy_skill_compatibility.sh", workflow)
        self.assertNotIn(".github/workflows/unit-tests.yml || true", workflow)
        self.assertNotIn("\n  backend:\n", shared)
        self.assertIn("group: backend-unit-tests-${{ github.workflow }}-${{ github.ref }}", workflow)
        self.assertIn("group: unit-tests-${{ github.workflow }}-${{ github.ref }}", shared)
```

Add `REPO_ROOT / ".github/workflows/backend-unit-tests.yml"` to `WORKFLOW_PATHS` in `test_refactory_collaboration_workflow_triggers.py` so both `pull_request` and `push` branch coverage are checked for the new workflow.

- [x] **Step 2: Update BCS job extraction to use the next remaining job**

After the Backend job is removed, `engine` is the next job. In all three BCS test files, replace BCS-job extraction from:

```python
workflow.split('\n  backend:', 1)[0]
```

with:

```python
workflow.split('\n  engine:', 1)[0]
```

Apply this only where the intent is to isolate the BCS job, not where the test intentionally reads the whole shared workflow.

- [x] **Step 3: Run the tests and confirm the expected red state**

Run:

```bash
apps/backend/.venv/bin/python -m pytest -q \
  singlebox/ci/tests/test_unit_test_workflow_diff.py \
  singlebox/ci/tests/test_refactory_collaboration_workflow_triggers.py \
  singlebox/ci/tests/test_bcs_ci_change_detection.py \
  singlebox/ci/tests/test_bcs_cargo_test_tools.py \
  singlebox/ci/tests/test_bcs_ci_build_reuse.py
```

Expected: FAIL because `.github/workflows/backend-unit-tests.yml` does not exist and the Backend job is still in the shared workflow. The BCS job-extraction edit itself must not cause the expected failure; diagnose it separately if it does.

---

### Task 2: Split the Backend workflow without changing its gate

**Files:**
- Create: `.github/workflows/backend-unit-tests.yml`
- Modify: `.github/workflows/unit-tests.yml`
- Modify: `singlebox/ci/tests/test_unit_test_workflow_diff.py`
- Test: `singlebox/ci/tests/test_refactory_collaboration_workflow_triggers.py`

- [x] **Step 1: Create the dedicated Backend workflow**

Create `.github/workflows/backend-unit-tests.yml` with this complete content:

```yaml
name: Backend Unit Tests

on:
  pull_request:
    branches:
      - dev
      - dev_refactory_collaboration
      - main
      - 'REL*'
  push:
    branches:
      - dev
      - dev_refactory_collaboration
      - main
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: backend-unit-tests-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend:
    name: Backend unit tests
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      # PR: compare against the checked-out merge commit's exact base tree.
      # push/dispatch: compare against dev (repo default branch) instead.
      BACKEND_BASE_REF: ${{ github.event_name == 'pull_request' && 'HEAD^1' || 'origin/dev' }}
    defaults:
      run:
        working-directory: apps/backend
    steps:
      - name: Check out repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Detect backend changes vs base
        id: changes
        working-directory: ${{ github.workspace }}
        shell: bash
        run: |
          # Make sure the base ref is present locally (shallow clones may not have it).
          git fetch --no-tags --depth=1 origin "${BACKEND_BASE_REF#origin/}" 2>/dev/null || true
          # Diff the base ref against the working tree. PR runs check out a merge commit,
          # for which "<base>...HEAD" collapses to nothing; diffing base against the tree
          # sees all PR-introduced changes regardless of the merge-commit checkout.
          mapfile -t files < <(git diff --name-only "${BACKEND_BASE_REF}" -- apps/backend singlebox/ci/legacy_skill_compatibility.sh .github/workflows/backend-unit-tests.yml || true)
          n=${#files[@]}
          if [ "$n" -eq 0 ]; then
            echo "skip=true" >> "$GITHUB_OUTPUT"
            echo "No Backend-relevant changes vs ${BACKEND_BASE_REF}; skipping backend tests."
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
            echo "Detected ${n} Backend-relevant changed file(s) vs ${BACKEND_BASE_REF}."
            printf '%s\n' "${files[@]}" | sed 's/^/  - /'
          fi

      - name: Set up Python
        if: steps.changes.outputs.skip != 'true'
        uses: actions/setup-python@v5
        with:
          # Backend targets Python >=3.12,<3.13 (see apps/backend/pyproject.toml).
          python-version: '3.12'

      - name: Install uv
        if: steps.changes.outputs.skip != 'true'
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
          cache-dependency-glob: apps/backend/uv.lock

      - name: Run unit tests with coverage
        if: steps.changes.outputs.skip != 'true'
        # ci_test.sh runs pytest with coverage and enforces the case-pass-rate /
        # line-coverage / changed-line-coverage gates via scripts/ci/report_check.py.
        run: bash scripts/ci_test.sh --base "$BACKEND_BASE_REF"

      - name: Enforce Legacy Skill compatibility gate
        if: steps.changes.outputs.skip != 'true'
        working-directory: ${{ github.workspace }}
        run: bash singlebox/ci/legacy_skill_compatibility.sh

      - name: Upload backend test results
        if: always() && steps.changes.outputs.skip != 'true'
        uses: actions/upload-artifact@v4
        with:
          name: backend-testresult
          path: apps/backend/pytest_report/
          if-no-files-found: ignore
```

- [x] **Step 2: Remove only the old Backend job from the shared workflow**

In `.github/workflows/unit-tests.yml`, delete the block beginning with exactly `  backend:` and ending immediately before `  engine:`. Preserve the BCS job and every later job byte-for-byte. Use this checked edit rather than a line-number-only deletion:

```bash
python3 - <<'PY'
from pathlib import Path
path = Path('.github/workflows/unit-tests.yml')
text = path.read_text(encoding='utf-8')
start = text.index('\n  backend:\n')
end = text.index('\n  engine:\n', start)
path.write_text(text[:start] + text[end:], encoding='utf-8')
PY
```

- [x] **Step 3: Re-run the trigger and BCS tooling tests**

Run the Task 1 Step 3 command again. Expected: PASS, including the BCS job-isolation assertions and the new workflow's branch-trigger checks.

- [x] **Step 4: Parse both workflows and verify the stable job identity**

Run:

```bash
apps/backend/.venv/bin/python - <<'PY'
from pathlib import Path
import yaml
for name in ('unit-tests.yml', 'backend-unit-tests.yml'):
    data = yaml.safe_load((Path('.github/workflows') / name).read_text())
    assert data['jobs']
backend = yaml.safe_load(Path('.github/workflows/backend-unit-tests.yml').read_text())
assert backend['jobs']['backend']['name'] == 'Backend unit tests'
shared = yaml.safe_load(Path('.github/workflows/unit-tests.yml').read_text())
assert 'backend' not in shared['jobs']
print('workflow YAML and Backend job identity verified')
PY
```

Expected: `workflow YAML and Backend job identity verified`.

---

### Task 3: Remove duplicate Cargo caching and artifact dependencies

**Files:**
- Modify: `singlebox/ci/tests/test_singlebox_coverage_workflow.py`
- Modify: `.github/workflows/singlebox-coverage.yml`

- [x] **Step 1: Write failing workflow assertions**

Add this test to `test_singlebox_coverage_workflow.py`:

```python
def test_singlebox_has_one_cargo_cache_and_excludes_only_rebuildable_dependencies():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    upload = workflow.split("- name: Upload singlebox artifacts", 1)[1]

    assert "~/.cargo/registry" not in workflow
    assert "~/.cargo/git" not in workflow
    assert workflow.count("uses: Swatinem/rust-cache@") == 1
    assert "cache-workspace-crates: false" in workflow
    assert "cache-bin: false" in workflow
    assert "save-if: ${{ github.event_name == 'push' }}" in workflow

    assert "!**/node_modules/**" in upload
    for preserved in (
        "singlebox/.dependencies/logs/**",
        "singlebox/.dependencies/coverage/singlebox/**",
        "singlebox/.dependencies/standalone/**",
        "singlebox/.dependencies/coverage/singlebox/reports/phase-timings.tsv",
        "!singlebox/.dependencies/coverage/**/collaboration-repair-provider.json",
        "!singlebox/.dependencies/coverage/**/.repair-provider-*",
    ):
        assert preserved in upload
```

Run:

```bash
apps/backend/.venv/bin/python -m pytest -q \
  singlebox/ci/tests/test_singlebox_coverage_workflow.py::test_singlebox_has_one_cargo_cache_and_excludes_only_rebuildable_dependencies
```

Expected: FAIL on the registry paths and missing `node_modules` exclusion.

- [x] **Step 2: Delete the duplicate cache step**

Delete the complete `Cache Cargo registry and git` step from `.github/workflows/singlebox-coverage.yml`, including its comments, `uses`, `path`, `key`, and `restore-keys`. Keep the immediately following `Cache instrumented BCS target` step and its `rust-cache` settings unchanged.

- [x] **Step 3: Add the dependency exclusion after all artifact includes**

At the end of the upload `path` list, immediately after `singlebox/.dependencies/coverage/singlebox/reports/phase-timings.tsv`, add:

```yaml
            !**/node_modules/**
```

Do not add exclusions for coverage raw data, standalone runtime, HTML, JUnit, databases or logs.

- [x] **Step 4: Verify the workflow tests**

Run:

```bash
apps/backend/.venv/bin/python -m pytest -q singlebox/ci/tests/test_singlebox_coverage_workflow.py
```

Expected: all tests PASS.

---

### Task 4: Reuse fresh BCS panel output

**Files:**
- Create: `singlebox/ci/tests/test_bcs_panel_asset.py`
- Modify: `singlebox/modules/bcs.sh:114-163`

- [x] **Step 1: Write the failing executable panel test**

Create `singlebox/ci/tests/test_bcs_panel_asset.py`:

```python
from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
BCS_MODULE = ROOT / "singlebox/modules/bcs.sh"


def test_panel_build_reuses_fresh_output_and_rebuilds_stale_output(tmp_path: Path) -> None:
    panel = tmp_path / "apps/bcs/assets/panel"
    (panel / "src").mkdir(parents=True)
    (panel / "node_modules").mkdir()
    (panel / "package.json").write_text('{"scripts":{"build":"vite build"}}\n')
    (panel / "package-lock.json").write_text('{}\n')
    (panel / "src/index.ts").write_text('export const panel = true;\n')
    (panel / "node_modules/.package-lock.json").write_text('{}\n')
    for path in (panel / "package.json", panel / "package-lock.json", panel / "src/index.ts"):
        subprocess.run(["touch", "-t", "202001010000", str(path)], check=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "npm-calls"
    npm = bin_dir / "npm"
    npm.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
        "if [[ \"$1 $2\" == 'run build' ]]; then\n"
        "  mkdir -p dist\n"
        "  printf 'panel-output\\n' > dist/index.umd.js\n"
        "fi\n"
    )
    npm.chmod(0o755)

    script = f'''
set -euo pipefail
BCS_DIR={tmp_path}/apps/bcs
LOG_DIR={tmp_path}/logs
DEP_DIR={tmp_path}/deps
REQUIRED_NODE_MAJOR=22
NPM_REGISTRY_URL=https://registry.npmjs.org
check_node_available() {{ return 0; }}
check_command() {{ return 0; }}
log_info() {{ :; }}
log_error() {{ :; }}
log_warn() {{ :; }}
source {BCS_MODULE}
build_bcs_panel_asset
touch -t 203001010000 "$BCS_PANEL_ASSET_DIR/dist/index.umd.js"
build_bcs_panel_asset
touch -t 203101010000 "$BCS_PANEL_ASSET_DIR/src/index.ts"
build_bcs_panel_asset
'''
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALL_LOG": str(calls)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    build_calls = [line for line in calls.read_text().splitlines() if line == "run build"]
    assert len(build_calls) == 2, build_calls
```

Run:

```bash
apps/backend/.venv/bin/python -m pytest -q singlebox/ci/tests/test_bcs_panel_asset.py
```

Expected: FAIL because all three invocations run `npm run build` instead of only the first and stale-input invocations.

- [x] **Step 2: Implement the freshness predicate**

Add this function immediately before `build_bcs_panel_asset` in `singlebox/modules/bcs.sh`:

```bash
bcs_panel_asset_stale() {
    local output="${BCS_PANEL_ASSET_DIR}/dist/index.umd.js"
    local input

    if [ ! -f "$output" ]; then
        return 0
    fi
    while IFS= read -r input; do
        if [ "$input" -nt "$output" ]; then
            return 0
        fi
    done < <(
        find "${BCS_PANEL_ASSET_DIR}" \
            -path "${BCS_PANEL_ASSET_DIR}/node_modules" -prune -o \
            -path "${BCS_PANEL_ASSET_DIR}/dist" -prune -o \
            -type f -print
    )
    return 1
}
```

- [x] **Step 3: Skip only the build when the output is fresh**

Keep dependency freshness handling unchanged. Immediately after the existing:

```bash
    if bcs_panel_asset_deps_stale; then
        install_bcs_panel_asset_deps || return 1
    fi
```

insert:

```bash
    if ! bcs_panel_asset_stale; then
        log_info "BCS panel asset is up to date, skipping npm build"
        return 0
    fi
```

Retain the existing `npm run build` and mandatory `dist/index.umd.js` verification for every stale/missing-output path.

- [x] **Step 4: Verify the panel regression and shell syntax**

Run:

```bash
apps/backend/.venv/bin/python -m pytest -q singlebox/ci/tests/test_bcs_panel_asset.py
bash -n singlebox/modules/bcs.sh
```

Expected: one test PASS and `bash -n` exits 0.

---

### Task 5: Overlap Python reports with BCS aggregation safely

**Files:**
- Modify: `singlebox/ci/tests/test_singlebox_parallel_pipeline.py`
- Modify: `singlebox/ci/singlebox_coverage.sh:31-32,271-283,332-377`

- [x] **Step 1: Add a failing overlap barrier to the executable pipeline fixture**

In the `pipeline` fixture's fake `uv` script, extend the `coverage combine` branch so it records both Python components and does not complete before BCS aggregate starts:

```bash
      component="$(basename "$COVERAGE_FILE" .coverage)"
      touch "$CASE_ROOT/python-${component}-started"
      for attempt in {1..100}; do
        [[ -f "$CASE_ROOT/aggregate-started" ]] && break
        sleep 0.02
      done
      test -f "$CASE_ROOT/aggregate-started"
```

In the fake `e2e_coverage.sh` aggregate branch, before `echo aggregate >> "$CASE_ROOT/order"`, insert:

```bash
test -f "$CASE_ROOT/python-backend-started"
test -f "$CASE_ROOT/python-baas-started"
touch "$CASE_ROOT/aggregate-started"
```

The aggregate branch must still contain its existing `test -f "$CASE_ROOT/companion-finished"` assertion. Add to `test_overlap_join_before_aggregation_and_failure_status`:

```python
    assert (root / 'python-backend-started').exists()
    assert (root / 'python-baas-started').exists()
    assert (root / 'aggregate-started').exists()
    assert (root / 'python-services-stopped').exists()
```

Model BaaS's USR1-only save in the same fixture: do not seed default raw coverage files. Make fake `ps` print PID 424242 with a `$CASE_ROOT`-qualified `coverage run` command only before `python-services-stopped`, and make fake `kill` create raw Backend/BaaS coverage only for `-USR1`. Record every `ps` and `kill` invocation, and require the stop marker before either Python start. Disable Bash's builtin `kill` in the fixture via `BASH_ENV` so the PATH fake receives USR1; forward every other signal to `/bin/kill`.

After the successful run, prove both save and cleanup behavior with:

```python
    raw = root / 'singlebox/.dependencies/coverage/singlebox/raw'
    for component in ('backend', 'baas'):
        assert not list((raw / component).glob('.coverage*'))
    assert (root / 'kill.log').read_text().splitlines() == ['-USR1 424242']
    assert (root / 'ps.log').read_text().splitlines() == ['ax -o pid= -o command='] * 2
```

The kill log proves one live-writer save; the ps log proves one pre-stop flush and one final-cleanup flush. A duplicate flush in `coverage_flush_and_cleanup` must make the count three and fail the test.

Run:

```bash
apps/backend/.venv/bin/python -m pytest -q \
  singlebox/ci/tests/test_singlebox_parallel_pipeline.py::test_overlap_join_before_aggregation_and_failure_status
```

Expected: FAIL because the current aggregate starts before either Python report branch.

- [x] **Step 2: Add a failing cancellation/reaping assertion**

Add this complete test to `test_singlebox_parallel_pipeline.py`. It uses a file barrier rather than an arbitrary long sleep: cleanup cannot finish until the test releases the Python report branches, and the fake stack shutdown proves they finished first.

```python
def test_parent_cancellation_reaps_python_reports_before_stack_cleanup(pipeline):
    import time

    root = pipeline
    uv = root / 'fake-bin/uv'
    source = uv.read_text()
    source = source.replace(
        'test -f "$CASE_ROOT/aggregate-started"',
        '''test -f "$CASE_ROOT/aggregate-started"
      for attempt in {1..400}; do
        [[ -f "$CASE_ROOT/allow-python-finish" ]] && break
        sleep 0.02
      done
      test -f "$CASE_ROOT/allow-python-finish"''',
        1,
    )
    source = source.replace(
        "printf '%s\\n' 'Name Stmts Miss Cover'",
        '''component="$(basename "$COVERAGE_FILE" .coverage)"
      touch "$CASE_ROOT/python-${component}-finished"
      printf '%s\\n' 'Name Stmts Miss Cover' ''',
        1,
    )
    uv.write_text(source)

    launcher = root / 'singlebox/singlebox.sh'
    launcher.write_text(launcher.read_text().replace(
        "printf '%s\\n' \"$*\"",
        '''if [[ "$*" == "--standalone stop all" ]]; then
  test -f "$CASE_ROOT/python-backend-finished" || exit 81
  test -f "$CASE_ROOT/python-baas-finished" || exit 82
  touch "$CASE_ROOT/cleanup-after-cancel"
fi
printf '%s\\n' "$*"''',
    ))

    env = {**os.environ, 'PATH': str(root / 'fake-bin') + ':' + os.environ['PATH'],
           'PYTHON': sys.executable, 'CASE_ROOT': str(root),
           'SINGLEBOX_STUB_LOG': str(root / 'singlebox.log'),
           'UV_STUB_LOG': str(root / 'uv.log')}
    with (root / 'cancel-python.log').open('w') as log:
        process = subprocess.Popen(
            ['bash', 'singlebox/ci/singlebox_coverage.sh'],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while not (root / 'aggregate-started').exists():
                assert process.poll() is None, (root / 'cancel-python.log').read_text()
                assert time.monotonic() < deadline
                time.sleep(0.02)
            process.send_signal(signal.SIGTERM)
            time.sleep(1.0)
            assert not (root / 'python-backend-finished').exists()
            assert not (root / 'python-baas-finished').exists()
            (root / 'allow-python-finish').touch()
            assert process.wait(timeout=15) == 143
            assert (root / 'cleanup-after-cancel').exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
```

Add `import signal` beside the other imports at the top of the file. The new test uses only existing fixture commands plus a bounded 400-iteration file barrier, so it cannot leave a legitimate report child sleeping indefinitely.

Run the new test and confirm it fails because cleanup currently does not reap the Python report PIDs before `stop all`.

- [x] **Step 3: Track and reap report PIDs on every cleanup path**

At the top of `singlebox_coverage.sh`, add:

```bash
backend_report_pid=""
baas_report_pid=""
```

Add this helper beside `stop_bcs_companions`:

```bash
reap_python_coverage_reports() {
  if [[ -n "$backend_report_pid" ]]; then
    wait "$backend_report_pid" 2>/dev/null || true
    backend_report_pid=""
  fi
  if [[ -n "$baas_report_pid" ]]; then
    wait "$baas_report_pid" 2>/dev/null || true
    baas_report_pid=""
  fi
}
```

Call `reap_python_coverage_reports` at the beginning of `cleanup_real_singlebox`, before companion shutdown, final coverage flushing or `singlebox stop all`. This deliberately waits for bounded existing report work rather than orphaning children on cancellation; the GitHub job timeout remains the outer hang guard.

- [x] **Step 4: Move Python report startup before BCS aggregate and join afterward**

After `bcs_companion_join` and its status capture, retain all status variables. Replace the current sequential BCS-report → cleanup → Python-report block with this ordering and equivalent existing commands:

```bash
  # BaaS uses `coverage run --parallel-mode --save-signal=USR1` without
  # SIGTERM handling, so a direct stop can exit without writing any coverage.
  # Save Backend/BaaS data while their writers are still live; the combines
  # below delete these raw inputs.
  flush_coverage_processes || true
  # Backend and BaaS must have exited before `coverage combine` runs: combine
  # deletes the raw inputs, while both services write their final
  # shutdown/lifespan coverage only during stop. Stop Backend first so its
  # shutdown hooks can still call BaaS/BCS; the final cleanup's `stop all` is
  # idempotent, and BCS stays up for its own aggregate below. This phase is
  # fail-closed: partial data must never be combined.
  timed_phase "python_services_stop" env \
    OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE="$model_config_mode" \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    bash "$repo_root/singlebox/singlebox.sh" --standalone stop backend baas

  local backend_report_log="$coverage_root/backend-report.log"
  local baas_report_log="$coverage_root/baas-report.log"
  local report_phase_start report_phase_end
  report_phase_start="$(date +%s)"

  combine_python_coverage "backend" "$repo_root/apps/backend" "$coverage_root/raw/backend" \
    > "$backend_report_log" 2>&1 &
  backend_report_pid=$!
  combine_python_coverage "baas" "$repo_root/apps/baas" "$coverage_root/raw/baas" \
    > "$baas_report_log" 2>&1 &
  baas_report_pid=$!

  timed_phase "bcs_coverage_report" run_bcs_e2e --aggregate-only || aggregate_status=$?

  wait "$backend_report_pid" || backend_coverage_status=$?
  backend_report_pid=""
  wait "$baas_report_pid" || baas_coverage_status=$?
  baas_report_pid=""
  cat "$backend_report_log" "$baas_report_log"
  report_phase_end="$(date +%s)"
  record_phase_timing "python_coverage_reports" "$report_phase_start" "$report_phase_end" "$((backend_coverage_status || baas_coverage_status))"

  if [[ "$bcs_e2e_status" -eq 0 ]]; then
    bcs_e2e_status="$companion_status"
  fi
  if [[ "$bcs_e2e_status" -eq 0 ]]; then
    bcs_e2e_status="$aggregate_status"
  fi
  coverage_flush_and_cleanup() {
    # cleanup_real_singlebox already flushes live coverage writers before
    # stopping the stack. Flushing again here, after combine deleted the raw
    # inputs, would let a late signal recreate empty raw coverage files.
    cleanup_real_singlebox
  }
  timed_phase "coverage_flush_and_cleanup" coverage_flush_and_cleanup || true
  trap - EXIT TERM INT
```

Keep the existing status checks, summary/module generation and final return order unchanged. Do not declare these PIDs `local` again in `run_real_singlebox`; the cleanup helper must see the same variables. The pre-stop USR1 save and the stop-before-combine barrier are both mandatory: the first preserves BaaS data, the second preserves shutdown/lifespan data before combine deletes inputs. Phase timings now overlap and must not be summed as wall-clock time.

- [x] **Step 5: Verify the pipeline and canonical shell gate**

Run:

```bash
apps/backend/.venv/bin/python -m pytest -q \
  singlebox/ci/tests/test_singlebox_parallel_pipeline.py \
  singlebox/ci/tests/test_singlebox_e2e_phases.py \
  singlebox/ci/tests/test_singlebox_acceptance_orchestration.py \
  singlebox/ci/tests/test_singlebox_companion_coverage.py
bash singlebox/verity/test_singlebox_coverage_gate.sh
bash -n singlebox/ci/singlebox_coverage.sh
```

Expected: all Python tests PASS, `PASS: singlebox coverage gate tests`, and shell syntax exits 0.

---

### Task 6: Update durable references and run the complete focused verification

**Files:**
- Modify: `apps/backend/README.md:142-154`
- Modify: `docs/agents/validation.md:64-115`
- Modify: `.github/workflows/backend-unit-tests.yml` (restore the two-line working-directory rationale)
- Modify: `CLAUDE.md` (update the CI workflow map)
- Modify: `singlebox/ci/tests/test_just_test_coverage_gate.py` (update the workflow comment only)
- Modify: `docs/specs/2026-11-12-ci-trigger-and-singlebox-performance/spec.md`
- Create: `docs/specs/2026-11-12-ci-trigger-and-singlebox-performance/validation.md`

- [x] **Step 1: Update the owning verification documentation**

In `apps/backend/README.md`, beside the full Backend CI command, add a link to `../../.github/workflows/backend-unit-tests.yml` and state that the dedicated workflow watches only Backend sources, the Legacy Skill entrypoint and its own workflow file.

In `docs/agents/validation.md`, add a short note beside the module-gate table:

```markdown
GitHub's `Backend unit tests` check runs from
`.github/workflows/backend-unit-tests.yml`; the shared `unit-tests.yml` no longer
owns a Backend job. BCS-only edits to the shared workflow therefore do not select
Backend unit tests, while Backend workflow edits still run the unchanged gate.
```

Also document that `python_coverage_reports` overlaps `bcs_coverage_report`; their durations are separate producer measurements and must not be added as serial wall-clock time. Document the mandatory pre-stop USR1 save for BaaS and the `python_services_stop` barrier before either combine; `coverage combine` deletes raw inputs, so both must precede report generation.

Restore the original two-line comment above `working-directory: ${{ github.workspace }}` in `backend-unit-tests.yml`, explaining that the job default is `apps/backend` and the root override is required for the `-- apps/backend` pathspec. Update the CI map in `CLAUDE.md` to include `backend-unit-tests.yml`, and update only the stale workflow comment in `test_just_test_coverage_gate.py` so it no longer says Backend uses `unit-tests.yml`.

When recording the cache change, state precisely that `rust-cache` retains registry indexes/archives and git databases but re-materializes `registry/src` and git checkouts; do not describe it as a byte-for-byte superset of the removed cache. Record the rationale for excluding workspace crates and coverage profiles in `validation.md`.

- [x] **Step 2: Run all affected workflow and CI tooling tests together**

Run:

```bash
apps/backend/.venv/bin/python -m pytest -q \
  singlebox/ci/tests/test_unit_test_workflow_diff.py \
  singlebox/ci/tests/test_refactory_collaboration_workflow_triggers.py \
  singlebox/ci/tests/test_singlebox_coverage_workflow.py \
  singlebox/ci/tests/test_bcs_panel_asset.py \
  singlebox/ci/tests/test_singlebox_parallel_pipeline.py \
  singlebox/ci/tests/test_singlebox_e2e_phases.py \
  singlebox/ci/tests/test_singlebox_acceptance_orchestration.py \
  singlebox/ci/tests/test_singlebox_companion_coverage.py \
  singlebox/ci/tests/test_bcs_ci_change_detection.py \
  singlebox/ci/tests/test_bcs_cargo_test_tools.py \
  singlebox/ci/tests/test_bcs_ci_build_reuse.py \
  singlebox/ci/tests/test_merge_junit_reports.py
```

Expected: all applicable tests PASS. If an environment-only Rust test is skipped, report its skip reason; if a previously documented baseline failure remains, reproduce it and identify it separately rather than claiming a clean full suite.

- [x] **Step 3: Run canonical shell, YAML and whitespace checks**

Run:

```bash
bash singlebox/verity/test_singlebox_coverage_gate.sh
bash -n singlebox/ci/singlebox_coverage.sh singlebox/modules/bcs.sh
apps/backend/.venv/bin/python - <<'PY'
from pathlib import Path
import yaml
for path in (
    Path('.github/workflows/unit-tests.yml'),
    Path('.github/workflows/backend-unit-tests.yml'),
    Path('.github/workflows/singlebox-coverage.yml'),
):
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert data['jobs'], path
print('all changed workflows parse successfully')
PY
git diff --check
```

Expected: the canonical shell suite passes, Bash syntax is valid, all three workflows parse, and `git diff --check` produces no output.

- [x] **Step 4: Inspect scope, file sizes and unchanged gates**

Run:

```bash
git status --short
git diff --stat
git diff -- .github/workflows/unit-tests.yml .github/workflows/backend-unit-tests.yml \
  .github/workflows/singlebox-coverage.yml singlebox/ci/singlebox_coverage.sh \
  singlebox/modules/bcs.sh
wc -l .github/workflows/unit-tests.yml .github/workflows/backend-unit-tests.yml \
  singlebox/ci/singlebox_coverage.sh singlebox/modules/bcs.sh \
  singlebox/ci/tests/test_unit_test_workflow_diff.py \
  singlebox/ci/tests/test_singlebox_parallel_pipeline.py \
  singlebox/ci/tests/test_bcs_panel_asset.py
```

Confirm that no source file is 1,000 lines or larger, no acceptance/shared-story parallelism was introduced, no coverage threshold or test command changed, and `Backend unit tests` remains the Backend job name.

- [x] **Step 5: Record only observed validation and update the spec status**

Create `validation.md` in this spec directory with these sections:

```markdown
# Validation

## Local execution

## Trigger-selection evidence

## Coverage orchestration evidence

## File-size and diff hygiene

## Remote runtime gaps
```

Record every command's actual exit status and test count from Steps 2–4. Under `Remote runtime gaps`, state explicitly that no full local product stack or real GitHub performance run was executed if that remains true; do not invent a speedup or coverage result. Update the spec status to reflect local implementation and verification only, and link `validation.md`. Remote job duration, phase timings, artifact size/file count and unchanged coverage remain remote acceptance evidence, not local claims.

- [x] **Step 6: Final review without committing or pushing**

Re-read the final diff against `spec.md`, confirm all acceptance criteria have either executable local evidence or an explicitly recorded remote-validation gap, and report the changed files, tests run, failures/skips and remaining remote measurements. Do not commit, push, create a PR or trigger a remote run without a separate user instruction.
