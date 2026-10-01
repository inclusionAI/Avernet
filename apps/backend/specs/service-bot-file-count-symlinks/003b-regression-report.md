# File-count symlink regression report

## Scope and environment

- Worktree: `service-bot-file-count-symlinks`; GitHub base `REL20260922`.
- Existing chain: Backend file-count router/service → provider transport → Engine file router/OpenClaw adapter → isolated scanner worker.
- Allowed changes: 120-second scanner, 150-second per-instance Backend budget, confined symlinks and skip diagnostics, associated tests/contracts. No auth/binding/router schema changes, deployment, Bot writes, or gitlink update.
- Reused Python environments from `service-bot-publish-ignore-ops-rel20260917`; cwd and `PYTHONPATH` point to the current worktree. Coverage uses independent report files.
- Legacy Claude Code live-run directory `/Users/helloworld/Desktop/codes/teamclaw/ocb_worktrees/local-claude-code-engine/src/engine` is absent. That unrelated model/relay suite was NOT RUN; no shared services were started or stopped. Real filesystem-to-Engine HTTP contracts are tested in process, not claimed as deployed API QA.

## Results

| Suite | Result | Evidence |
|---|---|---|
| Backend full community | PASS, 19709 passed, 43 skipped, 0 failed; 255.14 s | `src/backend/pytest_report/regression-junit.xml`, `regression-cov.xml` |
| Backend architecture | PASS, 314/314, 0 skipped, 0 failed; 56.68 s | `src/backend/pytest_report/regression-architecture.xml` |
| Engine full community | PASS, 2861/2861, 0 skipped/failed, 5 CI-defined corp-only deselections; 100.24 s | `src/engine/pytest_report/symlink-engine-junit.xml`, `symlink-engine-cov.xml`, independently checked |
| Backend runtime + real Engine HTTP/filesystem | PASS, 53/53, 0 skipped/failed; 2.89 s | `src/backend/pytest_report/regression-file-count-contract.xml`; rerun after scanner changes stabilized |
| Independent real-filesystem stress check | PASS | 201 directory aliases × 3 files = 603; 201 active-ancestor cycles, one dangling and one outside link skipped; linked root counts 3; direct `/etc` request remains `path_forbidden` |

## Boundary logs and security assertions

- Backend tests cover `backend.file_count.engine_request`, `engine_response`, `engine_failure`, provider transport timeout 150 for both BaaS and ARCA, request correlation, status/count/error fields and non-disclosure of raw authorization/error credentials.
- Engine route tests cover `engine.file_count.request`, `response`, `failure`, cancellation/cleanup correlation, and exception credential suppression.
- Scanner test covers `engine.file_count.scan_completed`, `request_id`, aggregate skipped reasons, 120-second timeout and no resolved link target logging. Recursive redaction tests cover token, authorization, cookie, password, secret, key, credential and session fields.
- All cited behavior/log/redaction assertions passed in the full or focused suites. Independent stress check used a disposable local temporary directory; no remote Bot files were accessed or changed.

## Coverage gates

- Use repository `scripts/ci/report_check.py` with fixed base and actual changed tree/commit, never `base == HEAD` while edits remain uncommitted.
- Thresholds: Backend total >=75% (repository stricter than generic 70%); Engine total >=70%; changed executable lines >=90% for both modules.
- `report_check.py` includes skipped tests in its pass-rate numerator. Report true passed/skipped/failed separately; do not claim skipped cases executed.
- Base `cf20a597b2e058c9baa0d017d0b432a7235f08d3`; tested tree `6ddb337acb53766d7cf95cd5fc58a999dd965646`. Source/tests matched this tree when gates were run.
- Latest tree `f93de31cde47209b31010579537135912766146f` includes the equivalent test-variable rename described below; Engine gate independently rerun against this tree with unchanged metrics and PASS. Main agent reran Backend gate against the same latest tree and reported PASS.

| Module | Repository case-pass metric | True executed / skipped / failed | Total executable line coverage | Changed executable line coverage | Preflight |
|---|---|---|---|---|---|
| Backend | 19752/19752 = 100% | 19709 / 43 / 0 | 102483/114849 = 89.23% | 1/1 = 100% | PASS |
| Engine | 2861/2861 = 100% | 2861 / 0 / 0; deselected 5 | 41388/44161 = 93.72% | 266/267 = 99.63% | PASS |

- Backend skips are existing conditional cases: removed/deferred cleanup and roster functionality, unavailable live singlebox/preprod credentials/environments, and empty/inapplicable parameter sets. They were not executed and are not represented as verified behavior. Every executed test passed.
- Engine coverage follows CI's `--cov=src`, including colocated test files. Backend coverage follows CI's source tree. No coverage exclusions, threshold reductions, or unasserted padding were added.
- After the full run, the main agent renamed only a test context variable (`token` → `request_context`) to avoid a secrets-check false positive; no runtime behavior or line positions changed, and the focused tests were rerun by that agent. Final commit-based gate must be repeated by the main agent after rebase.
- Remote ACI PENDING / not observed by this local regression agent; local PASS is not remote CI PASS.

## Commands and artifacts

- Backend cwd `src/backend`: `DEPLOY_PROFILE=test PYTHONPATH=src:. COVERAGE_FILE=.coverage.regression COVERAGE_CORE=sysmon <backend-venv>/python -m pytest tests/community -q -n 4 --dist loadfile --max-worker-restart=0 --continue-on-collection-errors --junitxml=pytest_report/regression-junit.xml --cov=src --cov-report=xml:pytest_report/regression-cov.xml --cov-report=term:skip-covered`.
- Architecture: same interpreter/env, `-m pytest tests/community/architecture -q --junitxml=pytest_report/regression-architecture.xml`.
- Integration: same interpreter/env, `-m pytest tests/community/core/service_bot/test_file_count_engine_contract.py tests/community/core/service_bot/test_file_count_runtime.py -q --junitxml=pytest_report/regression-file-count-contract.xml`.
- Both module reports independently checked with `scripts/ci/report_check.py --junit <module-junit> --coverage <module-cov> --source-root src/<module>/src --base cf20a597b2e058c9baa0d017d0b432a7235f08d3 --head 6ddb337acb53766d7cf95cd5fc58a999dd965646 --min-case-pass-rate 100 --min-line-coverage <75-backend/70-engine> --min-change-line-coverage 90`.
- New feature regression cases live in the repository's collected `test_file_count_symlinks.py`, existing scanner tests, and Backend contract/runtime tests. No unrelated legacy agent registry or skill definitions modified.

## Current verdict

Local scoped regression and fixed-tree coverage preflight PASS. Legacy model/relay live suite and deployed API QA NOT RUN; remote ACI remains PENDING until actual PR jobs are inspected.
