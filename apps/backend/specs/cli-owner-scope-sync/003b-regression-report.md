---
agent: tc-engine-regression
status: PASS
created: 2026-09-20
iteration: 1
---

# Backend regression report

## Environment and boundary

Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/fix-cli-owner-scope-sync`.
Python: repository `src/backend/.venv`, frozen dependencies prepared by parent.
Scope: backend identity service and Passport scope reconciler. No engine/relay changes, running services, remote bot mutations, deployment, or production QA were needed or performed. New regressions are backend pytest tests, not engine scenarios; no global engine registry changes.
Base: `b660c2bb016a9eed0100133736f5444d483f4ef1`. Head: uncommitted working-tree implementation. Coverage maps `git diff --unified=0 <base> -- src/backend/src` to coverage executable statements; it does not use the empty base..HEAD diff.

## Results

Independent complete backend community run at 2026-09-20 11:15:51 +08:00:

```sh
cd src/backend
COVERAGE_FILE=/tmp/cli-owner-regression-independent/.coverage .venv/bin/python -m pytest tests/community -n 4 --dist loadfile --junitxml=/tmp/cli-owner-regression-independent/junit.xml --cov=agentclaw.community --cov-report=json:/tmp/cli-owner-regression-independent/coverage.json --cov-report=term:skip-covered
```

- Collected: 19,379; passed: **19,336**; skipped: **43**; failed/errors: **0/0**.
- Executed-case pass rate: **19,336/19,336 = 100%**. Skips are not described as executed passes.
- Elapsed: **327.13 seconds**, 562 existing dependency/runtime warnings.
- Independent Ruff default and preview `F,E203,E265` checks pass on both changed production files and both changed/new test files.
- `git diff --check`: PASS.
- Repository `scripts/ci/report_check.py` JUnit and total-line gates: PASS. Its legacy JUnit calculation counts skips in its 19,379/19,379 metric; actual executed counts are explicitly separated above.

## Coverage preflight

| Metric | Evidence | Threshold | Result |
| --- | --- | --- | --- |
| Executed test pass rate | 19,336/19,336, 43 skipped, 0 failed | 100% | PASS |
| Full `agentclaw.community` line coverage | 101,011/113,404 = 89.0718% | >=75% repository threshold | PASS |
| Changed executable lines | 4/4 = 100% | >=90% task threshold | PASS |
| Identity service file | 204/216 = 94.44% | Information | PASS |
| Scope reconciler file | 143/145 = 98.62% | Information | PASS |

Changed executable statements: `cli_passport_scope.py` lines 109, 169, 187, 191, all covered. The service's new keyword argument is part of an existing multiline call and does not form a separate coverage statement; real service→reconciler integration assertions validate the requested owner reaches the Passport update. No new exclusions, lowered thresholds, or assertion-free coverage padding were introduced. Existing coverage exclusions are retained. This run's coverage source is the complete `agentclaw.community` package, explicitly distinguished from the repository CI script's `--cov=src` setting.

## Behavior and diagnostics evidence

The new test module executes 10 cases through the real identity service/reconciler with external dependency doubles. It checks default/custom CLI owner transitions with and without a sparse caller row, caller direction, unrelated CLI/MCP identity and metadata preservation, bootstrap without explicit intent, mapping immutability, and failure compensation including revision fencing. Existing tests in the full suite also cover authorization and protection guards.

Captured logs assert `cli_call_type_update_requested/succeeded`, `cli_passport_reconcile_requested`, `agentpass_cli_scope_update_requested/succeeded` on success. Failure cases assert outbound failure, compensation and API failure events, exception class, and absence of success events. Requested and outgoing CLI identity maps contain CLI identifiers and normalized identity modes. A nested credential fixture and credential-bearing exception use a synthetic marker that is asserted absent from logs. No new raw Passport payload, headers or credentials are logged. Existing system/request context and duration fields are retained.

## Local artifacts and remote status

- `/tmp/cli-owner-regression-independent/pytest.log`
- `/tmp/cli-owner-regression-independent/junit.xml`
- `/tmp/cli-owner-regression-independent/coverage.json`
- `/tmp/cli-owner-regression-independent/coverage.xml`
- `/tmp/cli-owner-regression-independent/summarize.py` (working-tree diff mapper)

Local backend regression and coverage preflight: **PASS**.
Remote ACI/CI: **PENDING / not yet observed**. Local success is not a remote CI result. Parent must rerun/report the committed base/head gate and follow any changes introduced by rebase.
