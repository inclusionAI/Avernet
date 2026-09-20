# Backend regression report

## Scope and environment

- Date: 2026-09-20; task: `service-bot-build-ignore-db`.
- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`.
- Python 3.12.13, coverage.py 7.14.3, four pytest workers, real local SQLite and `/usr/bin/rsync`.
- Backend-only change: no Engine/relay startup, remote Bot mutation, deployment, or model call was required or performed.
- Base: `7d39e392b99d8bf8351b029c3128d97d1c230411` (`github/dev`). Working-tree evidence head: temporary Git tree `95949e0032cdbfabfc0f92713b2100110a5411f0`, built with a separate temporary index; real branch/index were not modified. Final commit/base verification remains a separate release step.

## Execution

Executed the repository's `src/backend/scripts/ci_test.sh` with `BACKEND_CI_SKIP_INSTALL=1`, `BACKEND_CI_PYTEST_WORKERS=4`, and the Backend venv first in PATH. Then reran `scripts/ci/report_check.py` against the above base/tree with `--min-case-pass-rate 100 --min-line-coverage 75 --min-change-line-coverage 90`. No thresholds, coverage omissions, or business code were changed by the regression agent.

| Round | Passed | Failed | Skipped | Collected | Duration | Result |
|---|---:|---:|---:|---:|---:|---|
| 1 | 19523 | 8 | 43 | 19574 | 239.24 s | FAIL |
| 2 | 19533 | 0 | 43 | 19576 | 227.23 s | PASS |

Round 1 exposed six existing test constructors missing the newly required rules repository dependency, the missing registered GET error scenario, and a newly added mocked persistence test placed under the no-mocks endpoint directory. The implementation team repaired the constructors, registered the error scenario, and moved the stage persistence test to the appropriate service suite. Round 2 reran the entire suite; production source was unchanged between these rounds.

## Final coverage gates

| Metric | Evidence | Threshold | Result |
|---|---|---|---|
| casePassRate | 100%; 19533 executed passed, 0 failed, 43 skipped; 19576 collected | 100% | PASS under repository gate semantics |
| lineCoverage | 101652 / 113986 = 89.18% | >= 75% (stricter than 70%) | PASS |
| changeLineCoverage | 254 / 257 = 98.83% | >= 90% | PASS |

The repository checker includes skipped cases in its reported `19576/19576`; skips are not claimed as executed successes. The 43 skips are existing environment-gated live/preproduction/singlebox scenarios, removed durable-cleanup cases, and inapplicable parameter combinations. New build-ignore cases were not skipped.

Remaining uncovered changed executable lines: `core/repository/implementations/build_ignore.py:118` (CAS retry exhaustion), `core/service_bot/services/build_ignore_service.py:142-143` (large-list log summary). No coverage suppression was added.

## Behavioral evidence

- Real DI HTTP add/query/remove roundtrip and unauthorized-user denial passed.
- All four registered endpoint scenarios passed: GET happy, GET missing Bot, POST happy, POST invalid path.
- SQLite idempotency/revision, concurrent initial writes, tenant/environment/entity/engine isolation, invalid paths and size limits passed.
- Real rsync temporary-directory tests validated main copy, extra root mapping, exact subtree exclusion, stale build-target removal on retry, and no source deletion; excluded extra includes are not probed or restored by fallback.
- Actual BuildStageRunner/service/repository test verified the rule snapshot is persisted in publication `ext` without losing existing metadata.
- Existing publish-ignore, build configuration, draft restore, Skills artifacts, producer, architecture, and DI suites passed in the full run.

## Boundary logging

Tests assert `backend.build_ignore.request`, `.response`, and `.failure` events, normalized path handling, and absence of a deliberately sensitive database-error payload from logs. Build tests exercise fixed snapshots and transfer filtering. No new outbound Engine/BaaS call is introduced. These are local behavioral assertions, not a claim that remote production logs were inspected.

## Artifacts and status

- Full run logs: `/tmp/db-ignore-backend-ci.log`, `/tmp/db-ignore-backend-ci-round2.log`.
- JUnit: `src/backend/pytest_report/TEST-junit.xml`.
- Coverage: `src/backend/pytest_report/TEST-cov.xml`.
- Local regression and coverage: **PREFLIGHT PASS** for the stated snapshot.
- Remote ACI: **PENDING / not run by this agent**. Local results do not imply remote job success.
