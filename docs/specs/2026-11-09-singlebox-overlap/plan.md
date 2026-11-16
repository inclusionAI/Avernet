# Singlebox isolated test overlap implementation plan

> **For agentic workers:** Use superpowers:executing-plans in the existing worktree. Execute and verify each task before integration.

**Goal:** Overlap isolated Caller/OAuth execution with the shared-stack acceptance and BCS stories, and validate three Owner workers without changing production handoff timing.

**Architecture:** The canonical Singlebox entrypoint retains one product stack and the same coverage denominator. A supervised companion branch builds both required-features binaries once, then runs the existing isolated Caller/OAuth scripts. Acceptance and shared stories remain serial relative to each other. The parent joins the companion branch before the BCS reporting/cleanup phase. Existing standalone E2E scripts continue to build and run everything by default.

**Tech stack:** Bash, Python standard-library subprocess/process groups, Cargo, pytest-xdist.

**Spec:** User-approved first two proposals in the follow-up performance investigation, 2026-11-09. Baseline run 42565738267: 27m00s, acceptance 73 cases, Owner 268.182s, BCS E2E 466s.

## Invariants

- Production BCS is still built and launched without test-utils; companion binaries are separate targets.
- No scope/threshold/denominator/ignore-list reduction. Shared HTTP/CLI hit logs survive phase separation.
- Only Owner handoff gets three pytest workers; one/two remain valid fallback settings.
- Do not aggregate or delete runtime profiles before all companion processes have exited.
- Failures in acceptance, shared stories, companion build/run, or aggregation fail the gate, but available diagnostic reports are retained.
- Interruptions terminate only owned child process groups, not the shared stack or unrelated developer processes.
- Preserve the canonical report/JUnit paths and current-source Cargo validation.

## Tasks

### 1. Explicit BCS execution phases
Files: `apps/bcs/scripts/e2e_coverage.sh`, `singlebox/ci/tests/test_singlebox_e2e_phases.py`.
- [x] RED: execute a temporary fixture repo with shell stand-ins, assert shared-only runs stories without companions, shutdown or report; aggregate-only preserves existing logs/profiles through report and executes all gates; reject incompatible flags; default executes all tests.
- [x] GREEN: add `--shared-tests-only` and `--aggregate-only`, both requiring `--skip-start`, no `--no-stop` combination. Preserve default flow and gate statuses. Aggregate-only consumes existing evidence without truncating it.
- [x] Verify focused pytest and shell syntax.

### 2. Supervise isolated companion build/execution
Files: `apps/bcs/scripts/build_e2e_companions.sh`, existing caller/oauth wrappers, `apps/bcs/scripts/ci/companion_coverage.py`, `singlebox/ci/tests/test_singlebox_companion_coverage.py`, existing OAuth regression test.
- [x] RED: fake Cargo plus executable fixture scripts record build-before-run, two bin flags, profile namespaces, execution overlap, one failing companion while the other finishes, build-failure invalidation and SIGTERM process-group cleanup.
- [x] GREEN: common build script accepts caller/oauth/all and preserves current compiler flags. Default wrappers still build; explicit `--run-only` requires the relevant executable. Supervisor builds both together, starts isolated run-only wrappers with dedicated logs, and records timings/status. Signals terminate owned process groups and return failure.
- [x] Verify regression suite, including default OAuth build-failure behavior.

### 3. Integrate into the canonical runner; three Owner workers
Files: `singlebox/ci/singlebox_coverage.sh`, `singlebox/ci/tests/test_singlebox_acceptance_orchestration.py`, `singlebox/verity/test_singlebox_coverage_gate.sh`.
- [x] RED: execute orchestration fixtures and prove companions start before acceptance; shared stories finish and companions are joined before aggregation; failures/cancellation cannot produce success. Exercise default 3, fallback 1/2 and rejected worker counts using argv observations rather than string-only checks.
- [x] GREEN: start companion supervisor after stack provisioning/restart, keep PID in cleanup scope, run acceptance then shared stories, join, aggregate. Keep original JUnit merging and status priority. Emit realtime phase start/end records in addition to TSV; companion build/run timing uses a separate JSON artifact to avoid concurrent TSV writers.
- [x] Verify the changed shell gate fixtures and full relevant Python tests.

### 4. Runtime validation and publication
- [ ] Run focused tests, `bash -n`, changed-file Python compilation, `git diff --check`, source line counts.
- [ ] If available, run real Owner group repeatedly at three workers on an isolated live stack; otherwise disclose local runtime limitation and validate in CI.
- [ ] Push to PR #3711 with normal pre-push secret checks targeting origin/dev. Record fresh CI timing, test inventory and gate results; never infer hot-cache performance from a cold run.
- [ ] Keep the three-worker change only if runtime validation succeeds without retries; diagnose failures before expanding concurrency.
