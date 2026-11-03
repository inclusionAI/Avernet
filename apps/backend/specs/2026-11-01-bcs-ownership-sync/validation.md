# Ownership sync validation

## Current integrated status (2026-11-01)

- **Production implemented**: Tasks 1–10 complete with separate spec/quality
  approvals; the Task 7-only notes below are historical, not current scope.
- **Backend CI green**: final Task 10 official run passed 28,528 tests,
  line coverage 90.79%, changed-line coverage 99.59% (490/492). Task 11 changes
  coverage registration/tests/docs and coverage-only launch instrumentation,
  not Backend domain/Plugin behavior; its affected suite passed 1,998 tests.
- **Owner functionality locally validated**: four real HTTP/TaskQueue→BCS
  readback stories plus existing managers acceptance passed in the live runs.
  A controlled Passport/Runtime fixture is not real external AAC/device evidence.
- **Full product gate NOT green**: the required default all-module attempt and
  official verifier failed (7 BaaS-backed readiness stories, port exhaustion;
  baseline BCS queue_draining, 167/169 endpoints). No threshold/story was relaxed.
  The initial new Plugin shutdown measurement failure and its narrow instrumentation
  correction are recorded separately below, not dismissed as baseline.
- **Real AAC/device validation remains unverified**. Final independent review
  approved the implementation with no Critical/Important findings, but did not
  certify merge readiness. No additional worktree, branch, push or PR was created.
  Final corrected all-module evidence is recorded at the end of this document.

## Historical Task 7 scope and status (2026-11-01)

The following section records Task 7 only, based on approved Tasks 1–6 at `014d69c800`. This is not final
feature validation. No repair command, README/context declarations, configuration,
workflow phases, or live acceptance changes are included. Broader implementation
and evidence remain for subsequent tasks and the main integration effort.

The handoff coordinator now requires `BotOwnershipSyncProtocol`. After
`OwnerHandoffRepository.finish()` returns from its committed transaction, it
retains the manager hint first, then submits an independently exception-isolated
ownership hint using journal tenant/environment/Bot PK and `OWNER_HANDOFF`.
Admission rejection, disablement, or exceptions do not alter business success.
Persistence failures retain the existing `step() == False` error/recovery contract;
no success or notification is emitted for a failed finish/commit.

## Production Bot Owner write audit

Audited `apps/backend/src` Python and SQL, including ORM assignments, bulk update
values, generic mutation allowlists, Bot construction, and the handoff call chain.
Searches included:

```bash
rg --pcre2 -n '(\.owner_id\s*=(?!=)|BotModel\.owner_id\s*:|SET\s+owner_id|set\s+owner_id)' apps/backend/src --glob '*.py' --glob '*.sql'
rg -n 'UPDATE.*ac_bots|update\(BotModel|bulk_update|bulk_insert|BotModel\(' apps/backend/src
rg -n 'update_by_owner\(' apps/backend/src
rg -n 'OwnerHandoffService\(' apps/backend/src apps/backend/tests
```

Findings:

- The only current production replacement of an existing Bot's `owner_id` is
  `core/repository/implementations/bot/owner_handoff.py`,
  `OwnerHandoffRepository.finish`: `bot.owner_id = row.new_owner_id`. Its single
  production finish caller is `OwnerHandoffService.step` in `COMMITTING_OWNER`.
  The HTTP handoff entrypoint creates journal work; TaskQueue delivery calls
  `step`, and reconciliation/retry enqueue recoverable work. None is an
  additional independent Owner-write completion point.
- That transaction also commits Space assignment, unfreeze, binding metadata,
  old-owner collaborator removal, and journal `SUCCEEDED`. Notification belongs
  after transaction exit, not before authorization/runtime/retirement completes.
- `bot/bot.py::_new_bot_model` supplies the initial owner to a new model;
  `initial_layout.py::insert` and `insert_with_initial_skill_layout` are plain
  inserts, not replacement of existing ownership. Creation-for-others paths
  use creation/mutation APIs, not a separate existing-Bot Owner replacement.
- `bot/mutations.py::update_by_owner` bulk writes only its fixed allowlist, which
  excludes `owner_id`. Its call sites cannot change ownership through that API.
  `update_space_by_owner` changes Space/modifier/time only; CAS/ext updates,
  soft deletion, explicit-env ext updates, and provisioning claims do not write
  the Bot Owner. The `bot_knowledge/repository/store.py` Bot update writes `id`
  to itself for locking, not `owner_id`. No raw SQL Bot Owner replacement was
  found in the audited source.
- Other assignment hits are economy governance notification/whitelist/ticket
  owners, Task discovery owners, or collaborator-lock in-memory fields, not
  Bot aggregate Owner writes. Space moves alone are not Owner changes.
- No additional triggers were invented or wired from these unrelated hits.

Direct constructor audit found exactly three callers (none in production src):

1. `tests/community/core/bot_management/test_owner_handoff.py`: explicit recording
   `ownership_sync=MagicMock()` shared by the persisted-world service factory.
2. `tests/community/endpoints/test_owner_handoff_router.py`: explicit
   `DisabledBotOwnershipSync()`.
3. `tests/community/contracts/test_passport.py`: explicit
   `DisabledBotOwnershipSync()` (additional caller beyond the two known fixtures).

Production construction remains injector-based, using the existing ownership
protocol binding from Task 6; no fallback/default `None` was introduced.

## TDD and focused regression evidence

Commands below ran from `apps/backend`, with prefix
`DEPLOY_PROFILE=test SERVER_ENV=dev uv run --frozen pytest`.

| Stage | Targets | Exit / result |
| --- | --- | --- |
| RED before production edits | `tests/community/core/bot_management/test_owner_handoff_ownership_sync.py -q` | 1; 7 failed, 3 passed, 17 warnings, 1.08s |
| RED helper before production edits | `tests/community/core/bot_management/ownership_sync/test_notifications.py -q` | 1; 5 failed, 17 warnings, 0.17s |
| GREEN requested suite | Command below | 0; 77 passed, 18 warnings, 3.93s |
| Final GREEN after test formatting/review | Same command | 0; 77 passed, 18 warnings, 3.99s |

The first RED observed an empty notification callback list despite real persisted
`SUCCEEDED`/new owner/new Space. The helper RED failed because the helper did not
exist. The recording fixture was wired into the required constructor during GREEN.

```bash
DEPLOY_PROFILE=test SERVER_ENV=dev uv run --frozen pytest \
  tests/community/core/bot_management/test_owner_handoff.py \
  tests/community/core/bot_management/test_owner_handoff_ownership_sync.py \
  tests/community/core/bot_management/test_owner_handoff_spaces.py \
  tests/community/core/bot_management/test_owner_handoff_delivery.py \
  tests/community/endpoints/test_owner_handoff_router.py \
  tests/community/core/bot_management/ownership_sync/test_notifications.py -q
```

New regression coverage checks fresh repository sessions observe committed
`SUCCEEDED`/new Owner/new personal Space at submission, exact scoped kwargs,
manager hint ordering, no hints at begin/wait/cancel or failed finish/commit,
terminal query/replay/retry/redrive suppression, recovery completion, independent
manager/ownership failure isolation, disabled/rejected admission, unchanged
single issue/runtime/retirement counts, and no credential admin re-entry.
Helper tests assert exact allowed log fields and absence of secret payloads,
authorization URLs, exception objects, or traceback arguments.

## Supplemental checks and outstanding evidence

Also ran (same pytest prefix):

```bash
tests/community/contracts/test_passport.py \
  tests/community/architecture/test_module_boundaries.py \
  tests/community/architecture/test_no_fastapi_in_core.py \
  tests/community/architecture/test_core_no_concrete_plugin_imports.py \
  tests/community/architecture/test_kernel_no_imports.py \
  tests/community/architecture/test_service_api_conformance.py -q
```

Result: **exit 1; 229 passed, 1 failed, 19 warnings, 5.35s**.
`test_declared_deps_cover_actual_imports` reports undeclared ownership dependencies
in manager_sync, bot_management, repository, plugin_api, and plugins.local.
The bot_management list includes this task's new `api.bot_ownership_sync` import;
other reported ownership kernel/plugin/service imports are from earlier tasks.
This is not a clean architecture gate: README/dependency declarations are deferred
to Task 9 by the approved plan and the explicit Task 7-only scope. No allowlist,
gate, or README was changed to conceal the failure. All other selected checks,
including the updated Passport constructor/consumer test, passed.

Local diagnostic logs are `/tmp/task7-red.log`, `/tmp/task7-helper-red.log`,
`/tmp/task7-green.log`, `/tmp/task7-green-final.log`, and
`/tmp/task7-extra-green.log`; these are ephemeral and not committed artifacts.

Targeted Ruff check of the new tests and notification helper passed (exit 0);
an initial fixture-import F811 warning was resolved with explicit fixture
re-export. `git diff --check` passed. All seven added/modified Python files are
below 1,000 lines: service 381, notifications 69, Passport tests 174, handoff tests
643, router tests 113, helper tests 61, ownership handoff tests 177.

No full Backend CI/SAST, full architecture suite, live BCS/Singlebox coverage,
artifact verification, or production AAC/device validation was run for this
bounded task. Those remain subsequent-task evidence; local persisted SQLite and
Passport/Runtime protocol doubles do not establish real external credential safety.
Review was performed locally without delegation, as requested.


## Controller evidence through Task 8 (2026-11-01)

Implementation remains on `feat/ownership-sync-backend` in the original worktree;
no additional worktree, remote push or PR was created. Each completed slice below
received separate spec-compliance and code-quality reviews. These scoped results
do not substitute for the final architecture, full CI or live acceptance gates.

| Task | Commit | Actual verification |
| --- | --- | --- |
| 1: contracts/projector | `d158bf1ef5` | 73 new tests; independent projector regression 82 passed; both reviews approved |
| 2: SQL facts | `3675d94d45` | 42 Owner/manager reader tests; independent 17 Owner reader tests; both reviews approved |
| 3: delivery Plugins | `978b0526e9` | 124 new tests, 49 selected regressions; independent 124 + 41 existing manager HTTP tests; both reviews approved |
| 4: orchestration/consumer | `bdfc9bf099` | 227 scoped tests and 2 protocol-contract gates; independent 128 tests; 29 service tests at 100% scoped statement coverage, not whole-module coverage; both reviews approved |
| 5: shared dispatcher | `2f4a5cdc61` | 136 manager-sync tests independently reproduced in both reviews; existing manager tests retained |
| 6: config/DI/lifecycle | `014d69c800` | 262 requested tests, 121 contracts/core and 3 unchanged config snapshots; independent review runs 262 and 188; both reviews approved |
| 7: completed handoff | `f3b527b517` | 77 requested tests; controller independently reran 15 new trigger/helper tests; both reviews approved |
| 8: manual admission | `736e70f2c7`, `e06fa2da2a` | Initial 41 requested and 318 regressions; spec review required real-SQL queued-owner evidence, added in follow-up; 59 focused tests passed, spec re-review and quality review approved |

Controller integration run after Task 8 (before metadata completion): **1,568
passed, 18 warnings, 18.12s**, exit 0. Selection covers ownership Core, handoff,
bot_collaborator, Spaces, work orders, both fact readers, both consumer contracts,
Owner Plugins, DI and configuration. Log: `/tmp/ownership-integrated-pre-docs.log`.

Earlier wide baseline (after Tasks 1/2): **27,803 passed, 40 skipped, 683 warnings,
6 subtests passed in 316.60s**, exit 0. Command was `pytest tests/community
--ignore=tests/community/architecture
--ignore=tests/community/repository/bot/test_ownership_sync_reader.py -q -n 4
--dist loadfile --max-worker-restart=0`; the excluded new reader had separate
coverage, while new boundary metadata was still staged. This is a baseline, not
a claim that final full CI passed. Log: `/tmp/ownership-wide-baseline.log`.

Official changed-file SAST through `2f4a5cdc61`, using `f2c1758b2` as base, exited
0 (`/tmp/ownership-sast-staged.log`). Later commits require their own final run.
Staged full architecture after Task 4 reported 397 passed and 2 failures, both
missing boundary/provides declarations assigned to Task 9; no gate was relaxed.

### Live environment preflight and known external interference

The first official focused singlebox attempt stopped at BCSFuse startup health
checking. A warm retry with loopback `NO_PROXY`/`no_proxy`, `FRONTEND_PORT=18000`
(to avoid an existing external frontend) and `BCS_COVERAGE_FORCE_REBUILD=0` passed
the existing **13 managers acceptance tests**, with manager-sync Core **60.15%**.
The focused runner exited 0. A separately invoked official artifact verifier
failed: BaaS **44.47% < 45%**. The focused run is therefore not a complete artifact
baseline pass. Evidence: `/tmp/ownership-singlebox-warm.log` and
`/tmp/ownership-baseline-reports/`.

Before Owner business integration, the default all-module runner was also
attempted. It reported **8 failed, 53 passed** acceptance tests; all eight failures
were device/runtime stories with BaaS publish failure. The BaaS cause was
`DeviceAllocateError: No free port available in range 20010-20099`. Listener
inspection confirmed that most ports belong to long-running processes in an
unrelated worktree. Those processes were not stopped. This failure predates the
Owner business integration and is retained as environment evidence, not hidden by
lowering thresholds. Evidence: `/tmp/ownership-singlebox-all-baseline.log` and
`/tmp/ownership-all-baseline-reports/`. Final all-module validation remains required.

Only orphaned processes positively identified as belonging to this worktree's
own test runs were terminated. Setup-generated changes to `apps/bcsfuse/uv.lock`
were restored, not committed. Logs and runtime data are not committed artifacts.


## Production implementation gates through Task 9

Task 9 commits `5ccc8caace` and `eaaa069f53` completed the boundary declarations,
operations documentation, explicit singlebox switch and effective-config snapshots.
Both independent review stages approved. The full architecture/contracts/config/DI
selection passed **434 tests** (42 warnings, 54.64s). The three exact Core→Service
API exceptions document legal transport-free consumption by handoff, notification
helper and repair; no broad dependency or protocol-contract exemption was added.

Controller ran the official Backend CI with four workers:

```bash
BACKEND_CI_PYTEST_WORKERS=4 bash apps/backend/scripts/ci_test.sh \
  --base f2c1758b227a772d340d8eab21611ae03ad3bb6c --head HEAD
```

Result: **exit 0; 28,501 passed, 40 skipped, 707 warnings, 6 subtests passed in
347.38s**. Reported case pass rate **100%**, total line coverage **90.79%** against
75%, and changed-line coverage **99.59% (490/492)** against 80%. Log:
`/tmp/ownership-backend-ci.log`; official JUnit/XML reports are under
`apps/backend/pytest_report/` (generated, not committed). The only commit during
this run was the documentation-only capacity clarification `eaaa069f53`.
This validates production implementation through Task 9; subsequent acceptance
fixture changes still require their own verification.

An independent cross-layer production integration review of
`f2c1758b2..eaaa069f53` approved the commit→notification→dispatcher→facts→HTTP→ack
and managers-follow-up path with no critical/important findings. Its separate
focused run passed **336 tests in 5.89s**. This review excluded pending Task 10
fixtures and did not treat accepted best-effort ordering/loss risks as defects.
Official production-range SAST exited 0 (`/tmp/ownership-sast-production.log`),
and all **44** added/modified source and test files at that point were at most
1,000 lines. Later fixture files remain subject to final verification.

The pre-Task-10 all-module baseline also reproduced the BCS E2E failure
`human posts a persistent group message` (HTTP 400 instead of 200), with endpoint
coverage 167/169 (98.82%). It appears at line 897 of
`/tmp/ownership-singlebox-all-baseline.log`; the retained baseline summary records
`systems.bcs.e2e_status=failed`. The earlier warm focused baseline passed that
story and 169/169 endpoints. Subsequent repetitions must report this as a
pre-existing reproduced gate failure, not silently as a passing BCS suite.


## Task 10 and final Backend CI evidence

Task 10 (`a30e750462`) completed the isolated acceptance fixture and four real
HTTP/TaskQueue→BCS readback stories; separate spec and quality reviews approved.
The enabled focused run passed **17 acceptance tests in 315.43s**, manager Core
**61.44%**. BCS's persistent-group-message story returned the already reproduced
`400 queue_draining` (167/169 endpoints); the runner and artifact baseline were
not green. Artifact verification also reported BaaS **44.47% < 45%**. Reports:
`singlebox/.dependencies/coverage/owner-enabled-live4/reports/`.

The independent default-off deployment passed **14 acceptance tests in 2.55s**,
manager Core **58.86%**, BCS endpoints **169/169**; runner exit 0, separate artifact
verifier exit 1 because BaaS **44.50% < 45%**. Reports:
`singlebox/.dependencies/coverage/owner-default-off/reports/`. This preserves the
ordinary disabled-deployment evidence without replacing the default all-module run.

Official Backend CI was rerun after Task 10, with the same command as Task 9 and
`BACKEND_CI_PYTEST_WORKERS=4`: **exit 0; 28,528 passed, 40 skipped, 707 warnings,
6 subtests passed in 355.72s**; case pass rate **100%**, line coverage **90.79%**,
changed-line coverage **99.59% (490/492)**. Log:
`/tmp/ownership-backend-ci-final.log`. Follow-up `ee2c738deb` only replaced a dummy
fixture token with `test-token`; 35 fixture/Plugin unit tests and the credential
scan passed. Task 11 changes no production code, so the full Backend CI is not
repeated; its affected regression suite is rerun below. Earlier Task 7 partial
and deferred-metadata failures above are historical, superseded by Task 9/10 CI.

## Task 11 initial runtime coverage registration

The approved initial floor uses the first valid completed fresh enabled Backend
runtime report from Task 10, with unchanged production code:

- Source: `singlebox/.dependencies/coverage/owner-enabled-live4/reports/backend-coverage.json`.
- SHA-256 (recomputed before registration):
  `e8e970a9f3f8127598cf4d5f1e721c1119fafb91f13dc826e893fbc206c84931`.
- Complete Core prefix: `src/agentclaw/community/core/bot_management/ownership_sync/`.

| File | Runtime covered statements | Total statements |
| --- | ---: | ---: |
| `__init__.py` | 0 | 0 |
| `notifications.py` | 11 | 16 |
| `projector.py` | 9 | 11 |
| `repair.py` | 14 | 34 |
| `service.py` | 53 | 71 |
| **Total** | **87** | **132** |

Initial floor: `floor(87 / 132 * 10000) / 100 = 65.90%` (raw
65.909090…%). No repair/error exclusions, unit coverage, maintenance-driver
coverage, or artificial Core calls contribute. This completed Backend measurement
is valid even though the broader BCS/BaaS product gate independently failed;
it does not establish a passing unified baseline. No existing module, threshold,
or gate was changed (manager remains **54.03%**).

The enabled registration has no Router API denominator: the upstream handoff
module owns those routes. Plugin API is applicable, with production
`src/agentclaw/community/plugins/community/bcs_ownership.py` evidence for exactly
`HttpBcsOwnershipPlugin.replace_ownership` and `HttpBcsOwnershipPlugin.aclose`;
`plugin_min_percent` remains **100%**, including real stack shutdown.

TDD command from repository root:

```bash
apps/backend/.venv/bin/python -m pytest \
  singlebox/ci/tests/test_ownership_sync_coverage_registration.py -q
```

RED: exit 1, **1 failed in 0.04s**, missing enabled ownership registration
(`/tmp/ownership-task11-registration-red.log`). GREEN: exit 0, **1 passed in
0.05s** after registration. Targeted Ruff check passed. The official manifest
checker exited 0 (`singlebox coverage manifest is valid`):

```bash
apps/backend/.venv/bin/python singlebox/ci/singlebox_coverage_manifest_check.py \
  --manifest singlebox/ci/singlebox_coverage_modules.yaml --backend-root apps/backend
apps/backend/.venv/bin/ruff check \
  singlebox/ci/tests/test_ownership_sync_coverage_registration.py
```

The initial selected manifest/report/workflow/verifier tests reported **30 passed,
1 failed in 0.16s**, exit 1 (`/tmp/ownership-task11-manifest-tests.log`). The strict
expected-module-list guard already omitted `manager_sync` at HEAD (reproduced in
`/tmp/ownership-task11-manifest-baseline.log`); adding `ownership_sync` extended
that directly affected gap. With explicit user scope approval, Task 11 also
updates **only the exact expected list** in
`singlebox/ci/tests/test_singlebox_coverage_report.py` to include `manager_sync`
and `ownership_sync` in manifest order. Strict equality and every other assertion
are retained. This is a related guard correction, not a wholly pre-existing
failure and not a relaxed test.

After that correction, the same selection passed **31 tests in 0.19s**, exit 0
(`/tmp/ownership-task11-manifest-final.log`):

```bash
apps/backend/.venv/bin/python -m pytest \
  singlebox/ci/tests/test_ownership_sync_coverage_registration.py \
  singlebox/ci/tests/test_singlebox_coverage_manifest_check.py \
  singlebox/ci/tests/test_singlebox_coverage_report.py \
  singlebox/ci/tests/test_singlebox_coverage_workflow.py \
  singlebox/ci/tests/test_verify_singlebox_coverage_artifacts.py -q
apps/backend/.venv/bin/ruff check \
  singlebox/ci/tests/test_ownership_sync_coverage_registration.py \
  singlebox/ci/tests/test_singlebox_coverage_report.py
```

The initial full `singlebox/ci/tests` run reported **159 passed, 5 failed,
64 subtests passed in 34.49s**, exit 1 (`/tmp/ownership-task11-ci-tests.log`).
Besides the now-corrected related module-list guard, the other **four** failures
reproduce with HEAD's manifest and unchanged test/tool files (baseline selection
**85 passed, 5 failed in 4.24s**, `/tmp/ownership-task11-ci-baseline.log`):
conservative credential-expression classification, BCS pre-push dispatch,
workflow branch triggers, and a stale `singlebox/ci/pre_push.sh` path in the
design-guard test. Those four remain separate, out-of-scope and unfixed; no
assertion/tool was bypassed to conceal them.

The complete affected Python selection printed in Task 11 of `plan.md` ran from
`apps/backend` with `DEPLOY_PROFILE=test SERVER_ENV=dev uv run --frozen pytest`
and the listed targets: **exit 0; 1,998 passed, 43 warnings in 79.29s**.
Log: `/tmp/ownership-task11-affected.log`. This includes full architecture,
contracts, DI/config and both ownership/managers regressions.


### Task 11 fresh focused run (before shutdown-collection correction)

```bash
NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 \
FRONTEND_PORT=18000 BCS_COVERAGE_FORCE_REBUILD=0 \
bash singlebox/ci/singlebox_coverage.sh \
  --coverage-root "$PWD/singlebox/.dependencies/coverage/ownership-task11-focused" \
  --module ownership_sync --module manager_sync
apps/backend/.venv/bin/python singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/ownership-task11-focused/reports
```

Runner **exit 1**; **17 acceptance passed, 18 warnings in 309.24s**. Ownership
Core **87/132 = 65.91%**, manager Core **451/734 = 61.44%**, both above their
floors. BCS E2E passed: endpoints **169/169**, CLI leaves **66/66**, runtime line
**52.23%**, methods **46.33%**. Ownership Plugin evidence was only **1/2 = 50%**:
`replace_ownership` covered, `aclose` missing. The unchanged **100% Plugin floor
correctly fails**. This is not a green focused module gate. Separate official
artifact verifier **exit 1**: failed summary status and BaaS **44.50% < 45%**.
Logs: `/tmp/ownership-task11-focused.log`,
`/tmp/ownership-task11-focused-verifier.log`; complete artifacts under
`singlebox/.dependencies/coverage/ownership-task11-focused/reports/`.

Investigation distinguishes missing shutdown measurement from a demonstrated
production close failure. The runner saves coverage via USR1 before stopping
Backend; `singlebox/env/utils.sh` sends SIGTERM. Uvicorn's installed
`capture_signals()` restores and re-raises SIGTERM after graceful shutdown.
Coverage's `sigterm` option is disabled, so that later execution is lost: the
report also omits the entire sync lifecycle shutdown body. Backend log showed
application shutdown complete, but that alone is not proof of Plugin close.
A standalone stdlib-only reproducer under `/tmp/ownership-task11-signal-probe/`
confirmed the mechanism without calling any production Core: default coverage
saved only lines 1–7 despite a written shutdown marker; `sigterm=true` preserved
lines 8–11 as well (both subprocesses exited -15). Probe log:
`/tmp/ownership-task11-signal-probe.log`. No probe coverage was merged into any
Backend report. Scope expansion for a TDD-backed harness fix was requested and
subsequently approved below; no Plugin denominator was relaxed.

After the strict module-list correction, the complete `singlebox/ci/tests` rerun
reported **160 passed, 4 failed, 64 subtests passed in 39.50s**, exit 1
(`/tmp/ownership-task11-ci-final.log`). The four remaining baseline failures are
listed separately above; the related registration/report guard is now green.

### Approved shutdown instrumentation correction

User authorized the directly required coverage-harness scope expansion:
`singlebox/modules/backend.sh` plus the new
`singlebox/verity/test_backend_coverage_shutdown.sh`. Generic termination,
ownership guards, all source/omit rules and existing gates remain unchanged.
Backend coverage mode now generates a config under the ignored runtime coverage
directory, copies any detected existing TOML/INI configuration using structured
parsers, changes only `run:sigterm` to true, and passes it to the same
`coverage run --parallel-mode --save-signal=USR1 --source=...` command.
No normal deployment config or runtime Plugin code changes.

The new shell regression first failed because the config generator was absent
(exit 127, `/tmp/ownership-task11-shutdown-red.log`), then passed after the minimal
implementation (exit 0, `/tmp/ownership-task11-shutdown-green.log`). Its final
negative/positive control runs the same real subprocess shutdown in both modes:
the marker is written in both, but the post-flush statement appears in coverage
only with the generated SIGTERM-saving config. No production Core or Plugin is
called by this diagnostic. It also preserves source/omit/branch/report/path
settings for TOML, INI, prefixed setup.cfg and default/no-config cases. All generated
probe coverage stays outside runtime Backend artifacts. Final shell commands:

```bash
bash singlebox/verity/test_backend_coverage_shutdown.sh
bash singlebox/verity/test_singlebox_coverage_gate.sh
bash singlebox/verity/test_singlebox_service_guards.sh
bash -n singlebox/modules/backend.sh singlebox/verity/test_backend_coverage_shutdown.sh
```

All exited 0; logs `/tmp/ownership-task11-shutdown-final.log`,
`/tmp/ownership-task11-shell-gate.log`, `/tmp/ownership-task11-shell-services.log`.
The in-progress default all-module run had already launched Backend with the
old config; its artifacts remain unmodified historical evidence. A new focused
run with the correction must establish actual lifecycle/Plugin shutdown coverage.

### Task 11 required default all-module attempt

```bash
NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 \
FRONTEND_PORT=18000 BCS_COVERAGE_FORCE_REBUILD=0 \
bash singlebox/ci/singlebox_coverage.sh
apps/backend/.venv/bin/python singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

Runner **exit 1**; acceptance **58 passed, 7 failed, 18 warnings in 382.85s**.
All seven failures were BaaS-backed bot/device readiness paths: dormant recycle,
device provider lifecycle, expert chat session, directory download, workspace
file roundtrip, and the two harness diagnose stories. They reported BaaS publish
failure; current-run BaaS PID **16786** log at **21:26:14** shows
`DeviceAllocateError: No free port available in range 20010-20099`
(`/Users/zony/logs/secbaas/core-service-error.log`). This reproduces the
pre-feature environment failure class, with seven failures on this attempt,
not the earlier baseline's eight. Foreign port listeners were not terminated.

BCS independently reproduced the baseline persistent-group-message
`400 queue_draining` (E2E log lines 194–195): endpoints **167/169 = 98.82%**,
CLI **66/66**, runtime line **52.36%**, methods **46.45%**. Backend total runtime
coverage **48.10%**, BaaS **46.56%** (BaaS floor passed on this broader run).
The separate official verifier **exit 1** reported failed summary/BCS status,
7 JUnit failures, and BCS Router API coverage below 100%.

Module threshold errors were devices Core **43.28 < 43.36**, Router **16.67 <
44.44**; expert_chat Core **42.20 < 61.18**, Router **63.64 < 100**; files Core
**28.16 < 63.52**, Router **16.67 < 100**; harness Core **37.93 < 40.86**; and
the **new**, directly addressed ownership Plugin shutdown measurement gap
**50 < 100** (Backend was already running with old coverage config). The latter
is not dismissed as a pre-existing baseline error. Ownership Core remained
**87/132 = 65.91%**. None of these gates/denominators was lowered or excluded.

Canonical complete reports: `singlebox/.dependencies/coverage/singlebox/reports/`
(summary JSON/Markdown/dashboard, JUnit/acceptance log, Backend/BaaS JSON/text/HTML,
and copied BCS artifacts). Backend report SHA-256:
`d657fb33c30e1b998801d9ab70266c79f6f29e6a289765bf5fa6d221f6564485`.
Logs: `/tmp/ownership-task11-all.log`, `/tmp/ownership-task11-all-verifier.log`.
The initial new focused Backend report SHA-256 is
`e70ee4e0ddf2e1c8586ecb154959aa66d6b0be4c8bf07e3a61ff8a178a4d44a0`.

Before the corrected focused rerun, only completed-run orphan engine PID
**23548** was terminated, after confirming both its exact command rooted in
this worktree's `engine/adapter/.venv` and cwd `engine/adapter/src` under this
worktree. No foreign engine/gateway or external frontend on port 8000 was stopped.


The generated config deliberately uses `backend-runtime.ini` (not `.coveragerc`)
when no config exists, because the runner combines `.coverage*` data files.
A second regression first failed on that filename collision (exit 1,
`/tmp/ownership-task11-config-name-red.log`), then passed after reserving that
prefix for data only. The configured live Backend command retained
`--parallel-mode`, `--save-signal=USR1` and the unchanged complete source root.

Final scoped manifest/report/workflow/verifier rerun: **31 passed in 0.27s**,
exit 0; Ruff on both touched Python tests exited 0. The full implementation-range
source/test audit checked **58 files**, all at most **647 lines**. Task 11's
modified/new source files are registration test **56**, report test **568**,
Backend shell module **328**, shutdown shell regression **125** lines.
The manifest's entire pre-existing structure was compared against `ee2c738deb`
with only the new `ownership_sync` entry removed: exact equality, so **all existing
registrations, floors and gates remain unchanged**. `git diff --check` and shell
syntax checks passed. Setup-generated `apps/bcsfuse/uv.lock` was restored; logs,
coverage configs, databases and runtime files are not staged.

### Corrected focused runtime evidence — actual lifecycle close

```bash
NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 \
FRONTEND_PORT=18000 BCS_COVERAGE_FORCE_REBUILD=0 \
bash singlebox/ci/singlebox_coverage.sh \
  --coverage-root "$PWD/singlebox/.dependencies/coverage/ownership-task11-focused-final" \
  --module ownership_sync --module manager_sync
apps/backend/.venv/bin/python singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/ownership-task11-focused-final/reports
```

Runner **exit 0**; **17 passed, 18 warnings in 315.87s**. Ownership Core
**87/132 = 65.91%** against 65.90; Plugin **2/2 = 100%** against 100;
manager Core **514/734 = 70.03%** against unchanged 54.03. BCS passed with
**169/169 endpoints**, **66/66 CLI leaves**, runtime lines **52.24%**, methods
**46.35%**. Summary status passed, no module threshold failures. The actual
runtime Backend coverage now includes `HttpBcsOwnershipPlugin.aclose` lines
**112–114** and lifecycle stop lines **259, 262–264, 266, 270, 273**. This
establishes real lifecycle invocation, not an assumed close or a synthetic
Plugin call; the earlier 50% result was lost post-flush shutdown data.

Separate official artifact verifier **exit 1**, **only BaaS 44.50% < 45%**;
Backend runtime total **46.74%**. This focused run is a passing module/runner
result, not a passing unified artifact baseline. Complete reports are preserved
at `singlebox/.dependencies/coverage/ownership-task11-focused-final/reports/`.
Backend JSON SHA-256:
`5166524b2c52b0312f0dabcee9fd077552582c58e080ba64ddeef400fd26958b`.
Logs: `/tmp/ownership-task11-focused-final.log`,
`/tmp/ownership-task11-focused-final-verifier.log`.

All three shell regression/verity commands and shell syntax checks were rerun
on the final instrumentation and exited 0. Main requested an additional default
all-module run on this final instrumentation, rather than treating the old
50%-Plugin canonical artifact as final. Pre-correction all-module reports were
preserved at
`singlebox/.dependencies/coverage/ownership-task11-all-before-sigterm/reports/`;
the earlier canonical paths in that historical section refer to this preserved
snapshot. Completed-run orphan engine PID **40700** was verified by exact
worktree command and cwd before termination for the new run; foreign processes
and the external frontend were not touched.


## Final integrated verification after daemon recovery (2026-11-01)

The daemon interrupted the controller after Task 11 commit `066df82529`; recovery
first verified the existing branch and clean worktree and confirmed no previous
runner was still active. No implementation was repeated. The final default run
used the corrected coverage-only SIGTERM instrumentation from that commit:

```bash
NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 \
FRONTEND_PORT=18000 BCS_COVERAGE_FORCE_REBUILD=0 \
bash singlebox/ci/singlebox_coverage.sh
apps/backend/.venv/bin/python singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

- Runner **exit 1**; acceptance **58 passed, 7 failed**, 18 warnings, **381.81s**.
  The four ownership stories all passed, verified against the final JUnit.
- Ownership **Core 87/132 = 65.91%**, floor 65.90%; **Plugin 2/2 = 100%**,
  floor 100%. Both actual delivery and lifecycle `aclose` are covered.
- Manager Core **514/734 = 70.03%**, unchanged floor 54.03%.
- BCS runtime lines **52.28%**, methods
  **46.40%**, CLI **66/66**; the existing persistent-group
  message story again returned `400 queue_draining`, leaving endpoints
  **167/169 = 98.82%** and BCS E2E status failed.
- Backend total runtime coverage **48.24%**; BaaS
  **46.56%**. The BaaS aggregate floor passed in this full run;
  do not confuse it with the lower BaaS percentage in focused runs.
- Official artifact verifier **exit 1**: failed summary/BCS status, 7 acceptance
  failures, and BCS endpoint coverage below 100%. There is **no remaining Owner
  Plugin coverage failure** after the instrumentation correction.

The seven failures are the same readiness paths as the immediately preceding
all-module attempt: dormant lifecycle, device provider lifecycle, expert chat,
directory download, workspace file roundtrip, and two harness diagnose stories.
Current BaaS PID **73439** logged `No free port available in range 20010-20099`
at **21:54:20**. The resulting devices/expert_chat/files/harness Core or Router
coverage failures remain visible in `summary.json`. Foreign worktree listeners
were not terminated, and no test or threshold was weakened to hide them.

Final canonical evidence remains in
`singlebox/.dependencies/coverage/singlebox/reports/`: summary JSON/Markdown,
dashboard, acceptance JUnit/log, Backend/BaaS reports, and BCS artifacts.
Backend JSON SHA-256: `cc85e602063616eb09be8cd914dc8e611213c994cd53f742830a66e0b3979644`.
Controller logs: `/tmp/ownership-task11-all-final.log`,
`/tmp/ownership-task11-all-final.exit`, and
`/tmp/ownership-task11-all-final-verifier.log`.

### Final independent checks and disposition

- Task 11 spec review approved the complete denominator and unchanged existing
  gates; independently reran **20** registration/report tests and the shutdown
  probe. Task 11 quality review approved; independently reran **27** relevant
  manifest/report tests and the shutdown probe.
- The final whole-feature reviewer approved `f2c1758b2..066df82529` with no
  introduced Critical/Important or actionable Minor findings, independently
  rerunning **422 passing tests** plus the real shutdown-coverage regression.
  This is code approval, **not** certification of all product gates.
- Controller independently reran complete architecture + config + both consumer
  contracts on Task 11 code: **412 passed**, 42 warnings, **59.61s**;
  log `/tmp/ownership-final-architecture.log`.
- Controller reran the two changed coverage registration/report suites:
  **20 passed in 0.26s**, plus the real shutdown probe, whitespace and official
  secret checks: passed. Final implementation-range Python SAST was rerun with
  `f2c1758b2..HEAD` and exited 0 (`/tmp/ownership-sast-verified-final.log`).
- The official complete Backend CI result remains **28,528 passed / 40 skipped**,
  **90.79%** total line coverage and **99.59% (490/492)** changed-line coverage.
  Later changes are coverage registration/launch instrumentation and documents;
  their separate verification is recorded above rather than misrepresented as
  another full Backend CI run.

After the final runner exited, only orphan adapter PIDs **75691** and **81594**
were terminated, with both executable command and cwd confirmed inside this
worktree. No owned OpenClaw gateway remained in the subsequent process audit.
Setup-generated `apps/bcsfuse/uv.lock` was restored; runtime data and reports remain
uncommitted. The original branch/worktree is retained; no merge, push or PR.

**Disposition:** implementation and Owner-specific functional/module gates are
complete and code-reviewed. Required all-product validation was executed but is
**blocked/not green** on the documented baseline/environment failures. Keep the
unmet PASS steps unchecked; do not merge on the basis of focused results alone.
Real external AAC/credential-retirement/device acceptance remains unrun because
only the isolated local protocol fixture was provisioned, not those deployment
credentials or devices.

## PR #3425 CI lifecycle-test correction (2026-11-02)

The first remote Backend unit run (`41396836062`, head `49ad733deb`) failed
one lifecycle assertion: the test compared service/close loop lists including
their lengths. A managers round can finish before Owner admission; confirmed
Owner delivery then correctly triggers a second managers round. The sibling
HTTP-pool test made the same incorrect assumption about delivery/client counts.
No production behavior, gate, threshold, or skip was changed.

Both tests now explicitly exercise coalesced admission before worker startup
and separate admission after the first managers round drains. They assert exact
round counts / HTTP endpoint order independently of shared-loop affinity and
retain pool closure, thread cleanup, idempotent shutdown, and close-failure checks.

Validation on the rebased branch:

- Deterministic reproduction with the old assertions: **5 failed, 10 passed**;
  all five separate-round cases exposed the same cardinality assumption.
- Owner/managers Core suites: **285 passed**.
- Lifecycle suite: **15 passed**, repeated successfully in **10 independent runs**.
- Official complete Backend CI (`BACKEND_CI_PYTEST_WORKERS=4 bash
  apps/backend/scripts/ci_test.sh --base origin/dev --head HEAD`): **28,830 passed,
  40 skipped, 6 subtests passed**, 378.47s; total line coverage **90.84%**,
  changed-line coverage **99.59% (490/492)**; gate exit 0.
- Official Python SAST and `git diff --check`: passed; changed test file 293 lines.
- Independent review approved with no actionable findings and independently
  reran the 15 lifecycle cases.
- The original-head remote Singlebox run (`41396836106`) completed successfully.
  That is newer CI evidence than the earlier local all-module failures above,
  not a claim that this test-only follow-up's remote checks have already passed.
