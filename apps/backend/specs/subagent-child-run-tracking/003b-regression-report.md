# Engine regression report: child run tracking

Date: 2026-11-01, Asia/Shanghai. **Final local verification complete.**

**Feature and nearby regression: PASS. Full Engine preflight: FAIL**, solely
because the unchanged ACLI restart-process test fails in this environment and
also fails on the exact GitHub dev baseline. No failure was deselected or
removed from the pass-rate denominator. Remote CI is not certified here.

## Immutable source and execution

- Branch: `rebase/subagent-child-runs-on-dev`.
- Base / merge base: `6fb00420cf553fe36cde9f6eca4204a1f4d07200` (`github/dev`).
- Tested HEAD: `50a78e1e28878c1ad8124c41f1ebbda39f19429b`.
- Environment: macOS arm64, CPython 3.13.5,
  `engine/adapter/.venv/bin/python`.
- Full run: 17:15:35–17:18:20 +0800; pytest 162.43 seconds,
  wrapper 163.71 seconds. The expected 60-second graceful polling-stop test ran.
- HEAD before/after is identical. **All Engine Python source/test SHA256
  fingerprints before/after are identical; changed_during_run=[]**.
- Read the feature/review specs, project instructions, OPTIMIZE guidance and
  regression registry/memory. Only this report and ignored report artifacts
  were authored; no production/test edits, Git mutation, Bot/ARCA deployment,
  Gateway/model calls or unrelated Claude services. Missing public CI dependency
  `socksio==1.0.0` was installed in the supplied venv.

## Final results

| Suite | Passed | Failed | Skipped | Deselected | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Full Engine `pytest src` | 4266 | 1 | 0 | 5 | FAIL; verified baseline failure |
| Closest transport/ports/core/client directories | 976 | 0 | 0 | 0 | PASS |
| OpenClaw client subset of nearby run | 178 | 0 | 0 | 0 | PASS |
| Child-completion subset of client run | 48 | 0 | 0 | 0 | PASS |
| New inbound child-tracking logging probe | — | 0 | — | — | PASS |
| Project local SAST script; ruff F/E9 | — | 0 | — | — | PASS |

Subset counts are not added to full-suite totals. The nearby run took 7.13
seconds and independently retained identical before/after Python fingerprints.
It included these complete directories:

- `src/engine/community/api/tests/transport`
- `src/engine/community/core/adapters/openclaw/tests`
- `src/engine/community/plugins/openclaw/tests`
- `src/engine/community/openclaw/client/tests`

All three independently reviewed findings have regression assertions passing on
the final source: F1 duplicated old parent final/mixed announce coverage; F2
pre-ACK empty-final promotion while a child is pending; F3 superseded parent
spawn-result cursor rewind, including both rejected-old-final and
accepted-current-final cases. Stale original-parent final, child compaction
before/after accepted spawn, bounded early-cache eviction, exact child identities,
duplicates, ACK ordering, approvals, inject and downstream resume/port suites
also pass. This is local event-driven Adapter verification, not proof of model
consumption or deployed container behavior.

The full command follows `engine/adapter/scripts/ci_test.sh`: `pytest src -q`,
the five standard community deselections, isolated coverage data,
`--cov=<adapter>/src`, JUnit, full-src coverage XML/JSON and timing output.
The exact executable argument list is preserved in `command.json`.

The only deselected nodes are the standard corp assertions:
auth_gate_wiring/corp_profile_is_distinct,
corp_binding_parity/corp_profile_wires_corp_services,
notification_wiring/corp_profile_provides_dingtalk,
profile_modules/corp_column_is_not_community_fallback, and
router_collection/corp_preserves_internal_production_aicoding_routes.
ACLI tests ran normally.

## Coverage and actual preflight gates

| Metric | Numerator / denominator | Percent | Threshold | Result |
| --- | ---: | ---: | ---: | --- |
| casePassRate | 4266/4267 | 99.9766% | 100% | **FAIL** |
| lineCoverage, CI full-src scope | 57545/60147 statements | 95.6739% | >=70% | PASS |
| Additional production-only line coverage | 23042/25161 statements | 91.5782% | >=70% | PASS |
| Production changeLineCoverage, github/dev..HEAD | 169/171 changed statements | 98.8304% | >=90% | PASS |
| New child_runs.py | 110/110 statements | 100% | — | PASS |
| Changed gateway_client.py | 59/61 changed statements | 96.7213% | — | Above change threshold |

The actual project `singlebox/ci/report_check.py` was run with the original
full-src JUnit/XML, exact base/head, and unchanged 100/70/90 thresholds.
**Exit code 1: case pass rate 99.98% (4266/4267)**. The native checker exits at
that failure before its coverage checks; it did not produce an overall PASS.

Coverage was additionally calculated independently from the same complete
coverage dataset. CI's full-src denominator includes executable test files.
For the requested production changed-line metric, an explicit
`coverage-production.xml` projection removes `test`/`tests` directories,
`test_*.py` and `*_test.py`; the untouched full-src XML/JSON are retained.
The project's own `check_change_coverage` function, using its actual
`changed_lines(base, head)` and the production projection, independently reports
**98.83% (169/171, required >=90%)**. No thresholds or source exclusion rules
were changed. This matches the separate review's 169/171 measurement.

The committed diff and the working-tree measurement agree: both production
files are committed and unchanged. The only uncovered changed statements are:

| File:line | Behavior not reached by final pytest coverage |
| --- | --- |
| gateway_client.py:502 | Diagnostic for rejecting a spawn identity that aliases an authorized parent |
| gateway_client.py:786 | Timeout reason while an accepted child remains pending |

The separate logging probe exercises parent-identity rejection, but its
execution was deliberately not merged into pytest coverage to inflate this
metric. No additional tests were started after final full-suite completion.

## Baseline failures and historical results

Baseline sources were independently materialized by read-only `git archive`
from exact dev SHA `6fb00420cf553fe36cde9f6eca4204a1f4d07200`, with complete
Engine src and its pyproject under an ignored artifact directory. Baseline runs
used the same venv and the materialized src as PYTHONPATH. No new worktree or
unrelated ACLI modification was used.

| ACLI test in core/acli/tests/test_operations.py | Exact dev reproduction | Final 50a result |
| --- | --- | --- |
| test_a_restart_marks_running_operations_interrupted_and_kills_acli | FAIL, 5.09s; interrupted-state assertion succeeds, leftover.wait(timeout=5) raises TimeoutExpired at line 244 | **FAIL**, 5.017s, same timeout |
| test_an_operation_over_its_limit_fails_with_timeout | FAIL, 0.70s, with coverage enabled; calls()[1] raises IndexError at line 213 | **PASS**, 0.508s |

After rebase, dev remains that exact SHA. Current runner.py, operations.py,
test_operations.py, conftest.py and fake_acli.py match baseline content byte for
byte; evidence is in `baseline-file-parity.json`. This proves baseline
reproduction in the local environment. It does not prove an OS-specific root
cause, load causality, or an unobserved Linux CI PASS. The second failure was
transient across runs and is not listed as a failure of the final run.

| Historical snapshot | Outcome | Limitation |
| --- | --- | --- |
| Initial f475 working tree | 4125 passed, 1 failed, 5 deselected | Production/test changes during run |
| Rebased 9425 | 4263 passed, 2 failed, 5 deselected | Test changes during run; superseded by F3 fix |
| **Final 50a** | **4266 passed, 1 failed, 5 deselected** | **Stable source; authoritative local result** |

Earlier artifacts remain intact; historical counts were not rewritten or
combined into a fabricated final pass rate.

## New child-tracking boundary logs

Final SHA probe: **PASS**, 39 captured records overall, including 10 new
`ChildRuns` transition records. Every new tracker record contains
`system=openclaw direction=inbound operation=child_tracking`, normalized
`parentSessionKey`, child/run correlation and nonnegative `elapsedMs`.
Accepted/completed/duplicate/pending fields and event names identify successful,
duplicate, rejected and failed transitions.

Observed events include accepted/duplicate/rejected spawn, child terminal and
wrong-session rejection, announce final/duplicate, deferred/resumed parent and
successful/failed stream completion. Nested synthetic token, Authorization,
Cookie, password, secret, key, credential and session values were absent from
the captured success/failure logs: **8/8 PASS**. Only projected business IDs and
status metadata were checked; no general recursive-redactor claim is made.

Scope is the newly changed child-tracking inbound operation. No new outbound
HTTP/RPC behavior was introduced, and unchanged chat.send plumbing is outside
this new schema gate. The final production fingerprints remained unchanged
during the probe. Historical missing-duration evidence was superseded by main's
logging change. A temporary probe assertion that demanded a fully prefixed parent
session was corrected to support the existing default-agent alias; its original
JSON remains in historical artifacts and was not a production defect.

## Lint, artifacts and handoff

The project SAST script ran against `github/dev..HEAD` using the available
flake8 fallback; no diagnostics. This verifies that local fallback, not
unavailable enterprise-only FLA analyzers or remote SAST. Ruff F/E9 on the two
production and two changed test files passed. Production sizes: gateway client
**1000 lines**, child registry **179 lines**, within the 1000-line limit.

All paths below are relative to `engine/adapter/pytest_report/` and ignored:

- `child-runs-final-50a78e1e-20261101/`: authoritative `junit.xml`,
  `coverage.xml`, `coverage.json`, `coverage-production.xml`, isolated
  `.coverage`, `pytest.log`, `command.json`, source snapshots/result JSON,
  `metrics.json`, `gates.json`, `report-check.log`,
  `production-changed-line-gate.log`, boundary JSON/log/snapshot, acceptance
  results, baseline parity, SAST and ruff logs.
- `child-runs-final-50a78e1e-ports-20261101/`: final nearby suite JUnit,
  stdout and unchanged-source evidence.
- `child-runs-initial-20261101/`: initial broad run, complete materialized dev
  baseline, `baseline-acli-junit.xml`, `baseline-acli.log`, baseline metadata.
- `child-runs-final-20261101/`: historical 9425 full run and
  `baseline-timeout-1-junit.xml`, `baseline-timeout-1.log`,
  `baseline-timeout-results.json` proving the second baseline reproduction.

Remote ACI was not independently queried by this regression worker. Main's
subsequent verification of the exact source head is appended below.

**Final conclusion: feature/nearby/logging/coverage checks PASS; overall local
Engine preflight FAIL on the retained, verified baseline ACLI restart failure.**
Report complete; no further test runs or report writes planned.

## Main: verified remote Linux Engine evidence

The exact final runtime/test head `50a78e1e28878c1ad8124c41f1ebbda39f19429b`
passed the [remote Engine job](https://github.com/inclusionAI/Avernet/actions/runs/41292333281/job/124806102851).
Main downloaded `engine-testresult` from that run into ignored
`engine/adapter/pytest_report/remote-50a78e1e/` and independently ran the
repository report gate using its JUnit/XML and exact base/head. Exit 0:

- casePassRate: 4267/4267 = 100%.
- Full-src lineCoverage: 95.68% (CI's denominator includes executable tests).
- Full-diff changeLineCoverage: 791/795 = 99.50%.

These remote results do not erase the local macOS baseline failure above.
Production-only changed coverage remains separately measured at 169/171
(98.83%). Backend/Singlebox and any subsequent documentation-only head must
still be checked independently; this appendix does not claim all PR CI PASS.
