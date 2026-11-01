# Independent Engine regression: child run tracking

Date: 2026-11-01 (Asia/Shanghai). Status: initial run and latest focused run
complete; final full regression awaits explicit stable notification after
review fixes and rebase. No final full run has started.

Independent review subsequently identified F1 (a duplicated old parent final
can cover a child and prematurely end mixed announce fan-in) and F2 (pre-ACK
early-final fallback/promotion can bypass the child-completion guard). Main and
the test worker own RED tests and fixes. The intermediate green suites below
do not clear these findings; final verification must include their regressions.

## Scope and environment

- Shared checkout: `fix-subagent-final-state`; interpreter:
  `engine/adapter/.venv/bin/python`, CPython 3.13.5 on macOS arm64.
- Read the Engine child-completion spec, task review spec, project instructions,
  regression registry/memory and relevant OPTIMIZE guidance.
- Tests simulate actual Adapter `chat_stream` event boundaries. No Bot/ARCA
  deployment, Gateway/model consumption claim, or unrelated Claude services.
- Only this report and ignored `engine/adapter/pytest_report/child-runs-*`
  artifacts were authored. No production/test edits or Git mutations.
- Installed missing public CI dependency `socksio==1.0.0` in the supplied venv.

## Initial broad run

Executed `pytest src -q` with precisely the five community CI deselections in
`engine/adapter/scripts/ci_test.sh`. ACLI process tests were included.
Command, source hashes, stdout, JUnit, isolated coverage database, full-src XML
and JSON are under:
`engine/adapter/pytest_report/child-runs-initial-20261101/`.

| Measurement | Actual result |
| --- | --- |
| Window | 2026-11-01 16:58:30–17:01:04 +0800 |
| HEAD | `f475365f56a39b30eb21ffd7dd76584e81d454b8` |
| Observed github/dev | `6fb00420cf553fe36cde9f6eca4204a1f4d07200` |
| Test outcome | **4125 passed, 1 failed, 0 skipped, 5 deselected** |
| Passed / collected after deselections | 4125/4126 = **99.9758%** |
| Runtime | pytest 152.64 seconds; wrapper 153.60 seconds |
| Full-src measured line coverage | **56179/58937 = 95.3204%** |
| Additional production-only line coverage | **22614/24860 = 90.9654%** |
| Native `singlebox/ci/report_check.py` | **FAIL**, case pass rate below 100%; exits before coverage gates |

The full-src denominator intentionally follows CI's `--cov=<adapter>/src` and
includes executable test files. Production-only metrics exclude `test`/`tests`
directories, `test_*.py` and `*_test.py`; no production exclusion rules added.

**Snapshot limitation:** before/after SHA256 evidence records modifications to
`gateway_client.py`, `child_runs.py`, and `test_child_run_completion.py` during
the broad run. These results are initial evidence, not a clean final-version
full-suite PASS or an authoritative final changed-line coverage measurement.

The five deselections are the corp-only profile assertions in auth gate wiring,
corp binding parity, notification wiring, profile modules and router collection.
No additional failure was silently skipped or deselected.

## Verified ACLI baseline failure

Failing node:
`src/engine/community/core/acli/tests/test_operations.py::test_a_restart_marks_running_operations_interrupted_and_kills_acli`.
Its interrupted-state assertion succeeds; `leftover.wait(timeout=5)` raises
`subprocess.TimeoutExpired` at test line 244.

Independently materialized `engine/adapter/src` and `pyproject.toml` from exact
GitHub dev SHA `6fb00420cf553fe36cde9f6eca4204a1f4d07200` with read-only
`git archive`, under the ignored report directory. Ran the same test with the
same venv and the materialized baseline src as PYTHONPATH. Result: **1 failed**
in 5.09 seconds, same assertion and timeout. This proves baseline reproduction
in this local environment; it does not establish an OS root cause or a remote
Linux PASS. No ACLI source/test changes, Git worktrees or Git mutations used.

Evidence: `baseline-acli-junit.xml`, `baseline-acli.log`,
`baseline-acli-result.json`; baseline sources retained under `baseline/`.
Recheck the baseline identity/files after main's rebase if GitHub dev advances.

## Closest downstream regression and lint

Independently ran these complete directories:

- `src/engine/community/api/tests/transport`
- `src/engine/community/core/adapters/openclaw/tests`
- `src/engine/community/plugins/openclaw/tests`
- `src/engine/community/openclaw/client/tests`

Result: **942 passed, 0 failed/skipped/deselected**, 5.58 seconds. Evidence:
`transport-ports-junit.xml`, `transport-ports.log`,
`transport-ports-result.json`. This includes transport ACK/resume/WebSocket,
OpenClaw chat/approval/session ports and direct client regression coverage.
This is an intermediate snapshot, not post-rebase verification.

Latest pre-rebase rerun at 17:04:24 +0800 on finalized constructor parent guard
and child compaction tests: **948 passed, 0 failed/skipped/deselected**,
8.91 seconds. Before/after hashes of all Python source files are identical.
Its independent full-src XML/JSON and JUnit are in
`engine/adapter/pytest_report/child-runs-latest-20261101/`. Production-only
working-tree changed coverage against observed github/dev: **158/160 = 98.75%**;
gateway client **56/58**, new child registry **102/102**. Uncovered changed
gateway lines: 505 (partial clearing of compaction retries), 786 (timeout reason
while compaction remains pending). These are pre-rebase metrics and precede
main's subsequent logging change; they do not replace final verification.

Ruff `check --select F,E9` on both changed production files and both changed
client test files: **PASS**. Production sizes at that check:
gateway client 1000 lines, child registry 135 lines. This is local lint evidence,
not an assertion that an unobserved remote SAST job passed.

## External boundary diagnostics

An ignored independent `boundary_probe.py` exercised accepted/duplicate/rejected
spawn, wrong-session terminal, child error, duplicate terminal, deferred/resumed
parent, successful final, and failed parent stream through public chat_stream.
It captured 21 log records without writing raw fixture credentials to artifacts.
Evidence is `boundary_probe.json`.

- Observed events: `sessions_spawn_accepted`, `child_spawn_duplicate`,
  `child_terminal_rejected`, `child_terminal`,
  `subagent_parent_final_deferred`, `subagent_parent_resumed`,
  `sessions_spawn_not_accepted`, `terminal_recv`, `send_response`.
- Parent/child business run/session IDs remain observable; event/state,
  duplicate/match and pending-count fields provide transition evidence.
- Nested synthetic token, Authorization, Cookie, password, secret, key,
  credential and session values: **8/8 absent** from captured success/failure
  logs. This verifies these payload paths, not a general recursive redactor.
- Initial required external-boundary log gate: **FAIL / evidence incomplete**. No
  `elapsedMs`, `elapsed_ms`, `durationMs` or `duration_ms` was observed. Current
  diagnostics use formatted event summaries, not a demonstrated common
  system/operation/status/duration schema. No production changes made to expand
  this task's logging contract during this initial probe. An unchanged RPC error-response path was not
  tested for arbitrary upstream error-message redaction by this probe.

Main subsequently added `system=openclaw direction=inbound
operation=child_tracking elapsedMs parentSessionKey` context to the new tracker
logs. The stable-production probe now **PASSES**, superseding the initial
missing-duration finding. It captured 39 records overall, including 10 tracker
records. Every tracker record contains system/direction/operation, normalized
parent session correlation, child run correlation and nonnegative elapsed time;
success and failure/rejection outcomes are present. Sensitive sentinel absence
remains **8/8 PASS**, including nested spawn results and announce/failure payloads.

Evidence: `engine/adapter/pytest_report/child-runs-boundary-20261101/`
(`boundary_probe.json`, `probe.log`, `snapshot.json`). Both production files
had identical hashes before/after the probe. The probe first required a fully
prefixed parent session and incorrectly rejected the supported short alias;
its assertion was corrected to the existing default-agent normalization rule.
The original diagnostic JSON is retained as `boundary_probe-first.json`.
This was a probe assertion correction, not a production failure or source fix.

Audit is scoped to the newly changed inbound child-tracking operation; no new
outbound HTTP/RPC behavior was introduced. Unchanged chat.send plumbing is not
required to adopt this new schema. A generic recursive redactor is not asserted
or required by this payload-projection verification. Final rebase verification
will compare the production hashes to this successful probe's snapshot.

## Coverage preflight and final status

- Initial casePassRate gate: **FAIL**, verified baseline failure retained.
- Initial lineCoverage numerical gate: above 70%, with mixed-snapshot limitation.
- Final production-only changeLineCoverage against github/dev: **PENDING**;
  include newly added/untracked child registry, exclude test files, and use the
  finalized source rather than treating uncommitted changes as absent from HEAD.
- Remote ACI: **NOT OBSERVED**; no actual PR/job result supplied or claimed.
- Overall: **FAIL / final full verification pending**. Initial regression
  cannot be reported as a final clean PASS. Baseline failure and diagnostic
  history are explicit and are not hidden by deselections. The new child-tracking
  diagnostic gate currently passes; no final full-suite PASS is claimed.
