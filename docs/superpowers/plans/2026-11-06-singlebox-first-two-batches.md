# Singlebox First Two Batches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Singlebox CI wall-clock time by fixing push change detection, making phase costs observable, reusing safe instrumented Rust build dependencies, and running only the isolated Owner handoff acceptance cases with bounded parallelism.

**Architecture:** Keep `singlebox_coverage.sh` as the canonical orchestration entrypoint and preserve the existing stack, coverage collection, denominators, and thresholds. The workflow will compare push ranges using the event `before` SHA; acceptance will split the Owner handoff target into a separate pytest invocation, merge its JUnit result with the serial result, and fail if either invocation fails.

**Tech Stack:** GitHub Actions YAML, Bash, Python 3 standard library, pytest/pytest-xdist, Rust/Cargo LLVM coverage.

**Spec:** Approved first two optimization batches from the Singlebox performance investigation (2026-11-06).

## Global Constraints

- Do not change coverage denominators, thresholds, test semantics, or the canonical `singlebox/ci/singlebox_coverage.sh` entrypoint.
- Do not cache or reuse stale `.profraw`, `.profdata`, JUnit, or rendered coverage reports.
- Push change detection must fail closed when its base cannot be resolved; it must not compare a push against the already-updated `origin/dev`.
- Owner handoff parallelism is opt-in and limited to the isolated `tests/community/acceptance/bot_ownership_sync` target; the default worker count is 2.
- Preserve artifact paths expected by `write_summary_artifacts`, artifact verification, and CI uploads.
- Keep source files below the repository 1,000-line limit and avoid unrelated formatting churn.

---

### Task 1: Add regression coverage for push-range detection and acceptance orchestration

**Files:**
- Modify: `singlebox/ci/tests/test_singlebox_coverage_workflow.py`
- Create: `singlebox/ci/tests/test_singlebox_acceptance_orchestration.py`

**Interfaces:**
- Tests inspect the workflow and shell orchestration text; no production API changes.

- [ ] **Step 1: Write failing workflow assertions**

Add tests asserting the workflow uses `github.event.before` for push comparison, does not use `origin/dev` as the push change-detection base, and uploads a phase timing artifact.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:
```bash
python3 -m pytest singlebox/ci/tests/test_singlebox_coverage_workflow.py -q
```
Expected: FAIL because the current workflow uses `origin/dev` for push detection and has no timing artifact assertion.

- [ ] **Step 3: Write failing orchestration tests**

Add text-level tests for `singlebox_coverage.sh` asserting that:
- the acceptance runner supports a separate Owner handoff target invocation;
- the owner invocation uses `-n 2` and `--dist=load` only for that target;
- the script creates/merges two JUnit files into the existing `reports/acceptance-junit.xml` path;
- a non-zero owner subprocess status is propagated.

Use `Path.read_text()` assertions only; do not invoke the full stack in unit tests.

- [ ] **Step 4: Run the focused tests and confirm failure**

Run:
```bash
python3 -m pytest singlebox/ci/tests/test_singlebox_acceptance_orchestration.py -q
```
Expected: FAIL because the current script has one pytest invocation and no JUnit merge helper.

- [ ] **Step 5: Commit the test-only red phase**

```bash
git add singlebox/ci/tests/test_singlebox_coverage_workflow.py singlebox/ci/tests/test_singlebox_acceptance_orchestration.py
git commit -m "test(ci): define singlebox optimization contracts"
```

---

### Task 2: Fix workflow push baselines and cache safe instrumented Rust dependencies

**Files:**
- Modify: `.github/workflows/singlebox-coverage.yml`
- Test: `singlebox/ci/tests/test_singlebox_coverage_workflow.py`

**Interfaces:**
- `SINGLEBOX_BASE_REF` remains the base consumed by BCS changed-line coverage.
- For pull requests it is `HEAD^1`; for pushes it is `${{ github.event.before }}`; for manual dispatch it is `origin/dev`.

- [ ] **Step 1: Implement event-aware base selection**

Set the workflow environment so push events use `github.event.before`. In the detection step:
- resolve the configured base with `git rev-parse --verify <base>^{commit}`;
- fetch only a named remote ref when the base is `origin/...` and it is absent;
- use `git diff --name-only <base> <GITHUB_SHA> -- <relevant paths>`;
- fail the step when the base cannot be resolved instead of treating the list as empty.

- [ ] **Step 2: Add a dedicated Rust cache for instrumented dependencies**

Add an `actions/cache@v4` entry for the instrumented target's dependency/build state, keyed by:
- runner OS;
- Rust toolchain channel/version;
- `apps/bcs/Cargo.lock`;
- coverage build inputs (`RUSTFLAGS`, dev profile debug mode, and the target layout).

Cache dependency/build artifacts needed by Cargo, but never profraw/report directories. Do not make a cache hit skip `cargo build`; the build must still validate the current checkout and regenerate the current binary when Cargo says it is stale. Save the cache only on trusted push events.

- [ ] **Step 3: Run the focused workflow tests and shell syntax checks**

Run:
```bash
python3 -m pytest singlebox/ci/tests/test_singlebox_coverage_workflow.py -q
bash -n singlebox/ci/singlebox_coverage.sh
bash -n singlebox/singlebox.sh
```
Expected: PASS.

- [ ] **Step 4: Commit the workflow optimization**

```bash
git add .github/workflows/singlebox-coverage.yml singlebox/ci/tests/test_singlebox_coverage_workflow.py
 git commit -m "perf(ci): use push ranges and cache singlebox rust builds"
```

---

### Task 3: Add machine-readable Singlebox phase timings

**Files:**
- Modify: `singlebox/ci/singlebox_coverage.sh`
- Modify: `.github/workflows/singlebox-coverage.yml`
- Modify: `singlebox/ci/tests/test_singlebox_coverage_workflow.py`

**Interfaces:**
- New artifact: `singlebox/.dependencies/coverage/singlebox/reports/phase-timings.tsv`.
- Each row is `phase<TAB>start_epoch<TAB>end_epoch<TAB>duration_seconds<TAB>status`.

- [ ] **Step 1: Add timing artifact assertions**

Extend the workflow test to require `phase-timings.tsv` in the uploaded coverage artifact and require the script to initialize it before starting the stack.

- [ ] **Step 2: Implement a Bash timing helper**

In `singlebox_coverage.sh`, add a helper that records start/end/duration/status for named phases without swallowing the wrapped command's exit status. Record at least:
- `stack_setup_and_start`;
- `backend_acceptance`;
- `bcs_e2e`;
- `coverage_flush_and_cleanup`;
- `python_coverage_reports`;
- `summary_and_artifacts`.

Keep existing status variables and failure ordering unchanged.

- [ ] **Step 3: Include the timing file in artifact upload**

Add `singlebox/.dependencies/coverage/singlebox/reports/phase-timings.tsv` to `.github/workflows/singlebox-coverage.yml`.

- [ ] **Step 4: Run focused tests**

Run:
```bash
python3 -m pytest singlebox/ci/tests/test_singlebox_coverage_workflow.py -q
bash -n singlebox/ci/singlebox_coverage.sh
```
Expected: PASS.

- [ ] **Step 5: Commit phase timing instrumentation**

```bash
git add singlebox/ci/singlebox_coverage.sh .github/workflows/singlebox-coverage.yml singlebox/ci/tests/test_singlebox_coverage_workflow.py
git commit -m "perf(ci): record singlebox phase timings"
```

---

### Task 4: Split Owner handoff acceptance and merge JUnit reports

**Files:**
- Modify: `singlebox/ci/singlebox_coverage.sh`
- Create: `singlebox/ci/merge_junit_reports.py`
- Test: `singlebox/ci/tests/test_singlebox_acceptance_orchestration.py`

**Interfaces:**
- `merge_junit_reports.py <output> <input...>` writes one valid JUnit XML document at the existing acceptance report path.
- It preserves every `<testcase>`, sums `tests`, `errors`, `failures`, `skipped`, and `time`, and keeps suite metadata from the first input.

- [ ] **Step 1: Add failing merger unit tests**

Create tests with two temporary JUnit XML files containing distinct cases and one failing case. Assert the merged output contains all cases, aggregate counts, and the failure element. Add a missing/invalid input test that exits non-zero.

- [ ] **Step 2: Run merger tests and confirm failure**

Run:
```bash
python3 -m pytest singlebox/ci/tests/test_merge_junit_reports.py -q
```
Expected: FAIL because the merger module does not exist.

- [ ] **Step 3: Implement the minimal XML merger**

Use only Python standard-library `xml.etree.ElementTree`. Reject missing inputs, malformed XML, and documents without a `testsuites` root. Preserve testcase XML children exactly while aggregating suite-level counters.

- [ ] **Step 4: Add the acceptance split**

In `run_acceptance_smoke`:
- identify `tests/community/acceptance/bot_ownership_sync` as the special target;
- run all other selected targets serially into `acceptance-serial-junit.xml`;
- run the owner target with `pytest -n "${SINGLEBOX_ACCEPTANCE_OWNER_WORKERS:-2}" --dist=load` into `acceptance-owner-junit.xml`;
- reject worker values below 1 or above 2;
- run both invocations even if the first fails so reports remain diagnostic;
- merge both reports into the existing `acceptance-junit.xml` path;
- return non-zero if either invocation failed or the merged report is missing.

If the owner target is not selected, retain the original single invocation and report path.

- [ ] **Step 5: Run focused tests and a local synthetic orchestration check**

Run:
```bash
python3 -m pytest singlebox/ci/tests/test_merge_junit_reports.py singlebox/ci/tests/test_singlebox_acceptance_orchestration.py -q
bash -n singlebox/ci/singlebox_coverage.sh
```
Expected: PASS.

- [ ] **Step 6: Commit JUnit splitting and merge support**

```bash
git add singlebox/ci/singlebox_coverage.sh singlebox/ci/merge_junit_reports.py singlebox/ci/tests/test_merge_junit_reports.py singlebox/ci/tests/test_singlebox_acceptance_orchestration.py
git commit -m "perf(ci): parallelize isolated owner handoff acceptance"
```

---

### Task 5: Full verification and report

**Files:**
- Modify: none unless verification exposes a defect.

- [ ] **Step 1: Run all relevant CI tooling tests**

```bash
python3 -m pytest \
  singlebox/ci/tests/test_singlebox_coverage_workflow.py \
  singlebox/ci/tests/test_singlebox_acceptance_orchestration.py \
  singlebox/ci/tests/test_merge_junit_reports.py \
  singlebox/ci/tests/test_singlebox_coverage_report.py \
  singlebox/ci/tests/test_verify_singlebox_coverage_artifacts.py -q
```

- [ ] **Step 2: Run shell and static checks**

```bash
bash -n .github/workflows/singlebox-coverage.yml 2>/dev/null || true
bash -n singlebox/ci/singlebox_coverage.sh
bash -n singlebox/singlebox.sh
python3 -m compileall -q singlebox/ci
 git diff --check dev...HEAD
```

- [ ] **Step 3: Inspect changed file sizes and diff**

```bash
git diff --stat dev...HEAD
git diff --name-only dev...HEAD
```
Confirm no source file exceeds 1,000 lines and no coverage thresholds, denominators, or artifact verification requirements changed.

- [ ] **Step 4: Report unavailable validation explicitly**

If a full GitHub Singlebox run cannot be executed locally, report that limitation and provide the expected CI validation: push-range detection, phase timing artifact, JUnit testcase count preservation, owner worker cap, and unchanged coverage gates.
