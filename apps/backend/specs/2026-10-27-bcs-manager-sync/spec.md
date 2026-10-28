# Backend→BCS bot manager sync — implementation contract and release validation record

Branch: `feat/backend-bcs-manager-sync` (16 commits ahead of `origin/REL20261027`;
merge-base `1131e4af8c30e35cfa3b28effcdaba7fb2d2174c`, computed fresh in this
session as `git merge-base origin/REL20261027 HEAD` with the released
`origin/REL20261027` ref already fetched at `1131e4af8…` in this checkout —
never a stale SHA or a direct target-tree diff).

This doc is the concise implementation contract plus the honest record of which
gates actually ran and which did not. The full design lives outside this
repository (`2026-10-26-backend-bcs-manager-sync-design.md`, § pointers
recorded in the plan); the operational procedure is
`apps/backend/docs/bot-manager-sync-operations.md`. Nothing here overrides an
accepted ADR or the architecture rules.

## 1. Implementation contract (what shipped)

- **Service API** — `agentclaw.community.api.bot_manager_sync.BotManagerSyncProtocol`
  (`request_sync` / `request_space_sync`): the only surface business code
  calls; verdicts `QUEUED / COALESCED / DISABLED / REJECTED`, never blocking on
  BCS, never raising for downstream states.
- **Core** — `core/bot_collaborator/manager_sync/`: `projector.py` (pure
  roster projection: `admin`+`member` explicit collaborators with Team Space
  eligibility, owner excluded, dedup+sorted, hard cap 1,000,
  `bcs_bot_id = "<bot_id>:<entity_id>"` stable identity), `service.py` /
  `dispatcher.py` / `retry.py` / `lifecycle.py` (bounded in-memory executor,
  per-Bot merge generations, bounded retry budgets, startup/shutdown via the
  kernel lifecycle), `repair.py` (maintenance-only first-backfill /
  targeted-compensation surface), `diagnostics.py` (structured logs/metrics
  without rosters or secrets).
- **Plugin API** — `plugin_api/bcs_managers.py` (`BcsManagersPlugin.replace_managers`,
  typed `added/removed/total` verdicts, `BcsManagersError` with stable kind/code,
  never raw response bodies) and `plugin_api/manager_sync_coordination.py`
  (`ManagerSyncCoordinator`: atomic claim/begin/finish/release,
  owner-hold markers, exact-attempt CAS clear, generation sentinel).
- **Plugins** — `plugins/community/bcs_managers.py` (one PUT per attempt via
  the Gateway origin with the per-tenant trusted-App bearer), local fakes in
  `plugins/local/`, and `plugins/community/manager_sync_coordination.py`
  (Redis-coordinator over atomic Lua: strict leases, TTL-less in-flight
  markers, generation verification) and `plugins/community/manager_sync_scripts.py`.
- **Triggers** — the existing collaborator callback plus post-commit hints
  from work orders (editor approvals), owner handoff (COMMITTING_OWNER, no
  Passport admin-sync path), Space member/maintenance flows, space join
  approval completion, and `BotSpaceService.change_space`. Every hint sits in
  its own local try/except; no trigger can change the business result.
- **Repository** — read-only queries in
  `core/repository/implementations/bot/manager_sync.py` (bot facts, roster,
  space-affected bots, repair paging). **No schema, no task persistence, no
  `ac_task_queue` use, no `Bot.ext` sync state.**
- **DI/config** — `di/bot_manager_sync_config.py` (`bot_manager_sync` block;
  strictly validated; **default off**; the block's six keys are the whole
  surface), `di/modules/bot_manager_sync_module.py` (stack + coordinator +
  repair + lifecycle bindings; disabled deployments resolve zero-resource
  twins), context-boundary READMEs updated for every new boundary.

## 2. Release decision record (design §12 — verbatim decision)

- **Deploy order:** record the release Backend build SHA and the deployed dev
  BCS version independently → confirm the coordination topology and the Redis
  **non-eviction guarantee** → configure and verify the trusted App
  (`managers:sync`), Human registration and the target Bot's stable identity →
  deploy the Backend with the feature **default-off** → verify shared
  coordination and unknown-result recovery → run the first sync + permission
  acceptance for a controlled Bot → only then widen notifications.
- **Without the Redis non-eviction guarantee this release keeps the feature
  closed.** No automatic degradation is shipped: the single-replica
  restricted form (single instance/process/logical writer, no overlapping
  releases, external stop-send + old-request-completion confirmation before
  manual recovery) would still need its own explicitly approved contract,
  tests and operational duties. This release adds **no** local-only
  production mode and **no** new configuration item for such a form.
- **Rollback** stops new notifications/sends and drains bounded work only; it
  never rewrites managers already in BCS (close ≠ revoke — §7 below) and there
  is no SQL migration to revert. Lost in-memory tasks are not promised back;
  recovery is the runbook's manual backfill/compensation.

## 3. Evidence separation (what gate proves what)

- The **external live suite** — tests →
  `tests/community/acceptance/bcs_manager_sync_external/` — is the only
  compatibility evidence for this feature: controlled release Backend +
  deployed dev BCS, normal Backend HTTP mutations, owner-credential BCS reads.
  The directory is deliberately outside the release singlebox manifest
  (`singlebox/ci/singlebox_coverage_modules.yaml` — unchanged by this branch):
  the default singlebox builds the release-tree BCS whose gates say nothing
  about this feature, and no module or threshold was added or cut.
- The Backend's existing singlebox/coverage/SAST/architecture gates below
  prove what the feature must **not** have changed (business semantics,
  boundaries, file sizes, schema).
- BCS-side authority proof: the dev-source Rust tests (independent dev
  worktree, fixed SHA) prove the **contract**; they cannot prove the
  deployment actually running at an endpoint — deployment verification is a
  separate release step.

## 4. First-registration / owner / empty-roster acceptance guidance

1. Ownership evidence is read from **both sides**: the Backend owner of the
   case Bot and the BCS owner summary of the stable target must agree before
   any first sync (the live fixture asserts this precondition).
2. A first empty roster is acceptable only with the Bot's **BCS registration
   evidence** (registry entry + version + stable identity), every paged key
   accounted for (`queued + coalesced + rejected` = paged, `rejected_keys`
   empty), and every emptied key's `ACKNOWLEDGED_EMPTY` backed by that
   existence proof. A queued counter or an all-zero sweep is submission, not
   acceptance; an ordinary App's 403/404 on a read is never a data answer
   (runbook §6).
3. Unknown humans and unregistered targets are never auto-created or guessed;
   identity completion happens through the registration path and a fresh
   explicit re-request (runbook §2).

## 5. Redis coordination ownership (who owns what)

- **Generation sentinel** `mgrsync:v1:{sha256(target_id)}:generation`:
  initialized only by operations with all senders stopped (runbook §3),
  pinned as `coordination_generation` in the deployment config, required to
  match before any lease/marker operation; it is never auto-created by
  workers and a missing/mismatched sentinel stops every sender.
- **Non-eviction:** coordination markers are TTL-less by design; the shared
  Redis must protect them from normal eviction — a cache that cannot give
  that guarantee means the feature stays off (§2).
- **Drain/unknown ownership:** the in-flight marker blocks new snapshots for
  the Bot. Clearing it is a controlled human step — stop old senders, prove
  from BCS server-side evidence (request/audit) the ended attempt, CAS-clear
  exactly that attempt id with operator + evidence, then re-request; a fixed
  wait or a fetched roster is never proof (runbook §4/§5).

## 6. Unknown-recovery drill summary

The drill shape (implemented in the external live suite, destructive
posture): a held/dropped in-flight PUT after its bytes were accepted produces
an `unknown` outcome and plants the marker; later snapshots for the key are
blocked (verified through the maintenance coordinator's `read_blocked`);
recovery replays exactly the runbook's §4/§5 flow — evidence collection,
`clear_confirmed_attempt(key, attempt_id, operator_id, evidence)` returning
`True`, explicit `request_bot(key)` re-request, and only then roster
convergence is asserted. The owner-drill variant plants the owner-check hold
on handoff completion and asserts BCS ownership is **never** auto-transferred
(design §3.3); its marker is deliberately left to the §5.3 both-sides ops
reconciliation — the suite never fakes it, and its final report line says so.

## 7. Compatibility and risk

- **Default-off switch:** `bot_manager_sync.enabled=false` everywhere in this
  release. Disabled deployments keep the exact prior business behavior
  (notifications draw `DISABLED`; zero threads/clients/Redis dependencies).
- **Async de-permission window (real and acknowledged):** after a removal or
  a leave commits on the Backend, the person's BCS manager grant may persist
  until the next successful projection. Disabling the feature or a failed
  projection never revokes what was already written. Logs/metrics separate
  add-failures from revocation-failures; the window closes only via the
  projection (or the runbook's manual compensation).
- Business guarantees: BCS faults, queue rejection, cache failure, retry
  exhaustion never change collaborator/work-order/owner-handoff results;
  database write failures still fail upward; no `synced=true` is added to any
  existing HTTP schema.

## 8. Gate record (what actually ran, 2026-10-27, worktree c1509531e)

Every entry below is a real command with its real outcome. Nothing was run
against a deployed BCS in this record; deployment-side acceptance is §10.

| Gate | Command | Result |
| --- | --- | --- |
| Focused module regression | `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_collaborator tests/community/core/work_orders tests/community/core/spaces tests/community/core/bot_management/test_owner_handoff.py tests/community/core/bot_management/services/test_bot_space_service.py tests/community/contracts/test_bcs_managers.py tests/community/contracts/test_manager_sync_coordination.py tests/community/architecture -q` | **PASS — 989 passed, 0 failed** (~209 s) |
| Architecture, oversized-files gate | `DEPLOY_PROFILE=test uv run pytest tests/community/architecture/test_no_oversized_modules.py -q` | **PASS — 2 passed** (the gate scans `src/agentclaw/**` only; see §9) |
| Real-Redis coordination suite | `MANAGER_SYNC_TEST_REDIS_URL=redis://127.0.0.1:6399/0 DEPLOY_PROFILE=test uv run pytest tests/community/integration/test_manager_sync_redis.py -q` | **PASS — 14 passed, 0 failed, 0 skipped** (throwaway Redis on 6399) |
| Python SAST (branch-diff form used by the pre-push hook / CI) | `singlebox/ci/python_sast_local.sh apps/backend 1 --base 1131e4af8c30e35cfa3b28effcdaba7fb2d2174c --head HEAD` | **PASS — 0 findings** (after a one-line `SpaceRecord` import fix in `space_member_service.py`; see §9) |
| Python SAST (whole-path local form, brief step-5 literal) | `singlebox/ci/python_sast_local.sh apps/backend` | **FAIL as invoked locally — 1,645 hits, all in `.venv/site-packages`** (local flake8 fallback scans the venv; `apps/backend/src` has zero findings). The branch-diff form the pre-push hook runs is the authority and passes |
| Backend CI gate incl. changed-line coverage ≥80% / total ≥75% | `apps/backend/scripts/ci_test.sh --base 1131e4af8c30e35cfa3b28effcdaba7fb2d2174c --head HEAD` | **PASS on the coverage verdict** — via §8.1 (run 2 completed with only the 12 pre-existing base failures; the deselected rerun + the repo's own `report_check.py` gives changed-line 94.22% ≥ 80%, total 90.48% ≥ 75%, case pass rate 100%). The literal `ci_test.sh` invocation itself stops at those 12 base failures — recorded, with the base-classification proving none is branch-introduced |
| BCS authority Rust tests (contract authority; independent dev worktree) | `git worktree add /tmp/avernet-bcs-dev origin/dev` → SHA **a0c407a5a929c1125f7db76401c17c9254e00e9f** → `(cd apps/bcs && cargo test -p bcs-api-http --test bot_authority_routes)` | **PASS — 19 tests passed; 0 failed** (run with `CARGO_TARGET_DIR=/home/admin/cargo-bcs-dev-target`: `/shared/cargo/target` is root-owned and unwritable in this environment — exact blocker below) |
| Unified singlebox coverage + artifact verifier | `singlebox/ci/singlebox_coverage.sh` then `python3 singlebox/ci/verify_singlebox_coverage_artifacts.py --reports-dir singlebox/.dependencies/coverage/singlebox/reports` | See §8.2 — attempts 1-2 environment-blocked upstream; attempt 3 ran the full stack (BCS E2E gate PASSED) but failed its Backend acceptance leg in the non-isolated session environment. **FAIL (environment-not-isolated)**; the artifact verifier was therefore not run (never claimed as pass) |
| External live acceptance suite | `RUN_ACCEPTANCE=1 uv run pytest -o addopts= tests/community/acceptance/bcs_manager_sync_external/test_bcs_manager_sync_live.py -v` | **NOT RUN (live scenarios)** — no controlled release Backend endpoint/credentials exists in this session (§10). The suite's loudness was verified: enabled without config → 9 loud fixture errors naming every missing key, no skips, no fallback; without `RUN_ACCEPTANCE=1` the repo conftest reports skipped (NOT RUN) |
| Whole backend suite (regression context; same command as Task 8: `DEPLOY_PROFILE=test uv run pytest tests/community`) | Not re-run in this task | **NOT RUN** — Task 8 already ran it at the branch (26,268 passed / 15 failed / 54 skipped) with all 15 reproduced identically at the pre-change commit; §8.3 records this branch's classification re-check |

### 8.1 `ci_test.sh --base 1131e4af8 --head HEAD` (the coverage gate)

- **Run 1 (as configured, `xdist -n auto`): ABORTED — pytest INTERNALERROR**:
  the 32 GiB memory cgroup killed an xdist worker (`Memory cgroup out of
  memory: Killed process …`), and xdist died with
  `KeyError: <WorkerController gw27>`. Environment resource failure, **not** a
  code failure — 96 visible cores made `-n auto` spawn ~96 workers under a
  32 GiB cap.
- **Run 2 (BACKEND_CI_PYTEST_WORKERS=4): the full suite completed** —
  `12 failed, 26321 passed, 54 skipped, 2 subtests passed in 1090.57s`.
  Every one of the 12 failures is in §8.3's pre-classified base set (they
  reproduce identically at the merge-base); **zero failures are
  branch-introduced** (Task 8's 15 = these 12 + 3 arch failures the branch
  itself has since fixed). Because `ci_test.sh` stops when pytest does not
  pass cleanly, its changed-line-coverage step did not run in that
  invocation; the coverage verdict was produced by running the same
  pytest/coverage invocation with **exactly those 12 pre-classified base
  tests `--deselect`ed** (a recorded deviation — none of the 12 touches a
  branch-changed file), then applying the repo's own
  `singlebox/ci/report_check.py` with the gate's thresholds:

```
pytest (12 base tests deselected): 26321 passed, 54 skipped, 0 failed in 1055.60s
case pass rate:      100.00% (26377/26377, required >= 100.00%)
line coverage:       90.48%  (required >= 75.00%)
change line coverage: 94.22% (1629/1729, required >= 80.00%)  → PASS
```

Deselecting the 12 pre-classified failures is conservative for the
changed-line verdict in this direction: any lines those tests would have
exercised are simply not counted, so the 94.22% is a lower bound for the
with-them run; none of the 12 is a test of a branch-changed module.

### 8.2 Unified singlebox coverage — three attempts, three environment blockers

1. **Attempt 1 (`singlebox_coverage.sh` as-is): FAILED at frontend setup —**
   `npm ci` in `apps/frontend` fails at the `husky install` postinstall
   (`husky - .git can't be found`) in this checkout layout. Local-layout
   artifact, not a repo defect (CI checkouts run prepare scripts against a
   plain `.git` directory).
2. **Attempt 2 (`HUSKY=0`): got through frontend install, BAAS, gateway and
   bot spawn; FAILED starting the singlebox BCS** — `run sqlite migrations:
   invalid database input: sqlite migration checksum mismatch for version 34
   (bot_authority): applied=bec911a6…, current=11e5e13d… (expected
   name=group_context, dialect=sqlite)`. The throwaway coverage-runtime BCS
   state mixes a dev-built runtime with this tree's on-disk migration set.
3. **Attempt 3 (HUSKY=0, the stale throwaway `bcs.db` removed): the full
   release stack ran** — frontend build, BAAS, gateway, 5 bots, BCS E2E
   gate **PASSED** (`E2E line coverage 48.90% ≥ 41`, `method 43.13% ≥ 36`,
   endpoint coverage 165/165), coverage reports were collected — but the
   Backend acceptance leg ended `13 failed, 50 passed`
   (expert_chat caller-connection ×12, cron lifecycle ×1 — all **outside
   any branch-changed module**) and with that the all-module thresholds
   tripped (`bot_collaborator core 49.86% < 53.88%`,
   `cron core 37.01% < 41.84%`, `cron router API 88.89% < 100%`,
   `expert_chat core 52.34% < 61.18%`, `expert_chat router API 90.91% <
   100%`): *"singlebox coverage collected, but Backend acceptance failed
   with exit code 1"*. Root cause class in this session: the acceptance
   stack ran concurrently with a parallel 26k-test coverage sweep on a
   host whose memory cgroup is 32 GiB — the environment was never isolated
   for this gate. Recorded as **FAIL**, not retried a fourth time; no
   registration/threshold was touched, and its verdict concerns only the
   release default modules (never the new feature, §3).
   `verify_singlebox_coverage_artifacts.py` was consequently not run
   (the run never reached "all modules green" to verify).

The record for release: the singlebox gate's pass/fail in this session says
nothing about the new feature either way (§3); the release pipeline's own CI
run remains the authority, and CI will have the normal `.git` layout and a
clean coverage runtime.

### 8.3 Base-classification of the whole-suite failures found in Task 8

All 12 non-architecture whole-suite failures (config golden ×3, space
config-isolation ×3, resource handoff ×2, expert-chat seed ×2, skills path
factory ×1, sqlite observation ×1) were re-run at the **merge-base**
`1131e4af8` in a temp worktree (`/tmp/avernet-base-classify`, UV sharing the
feature worktree's venv via `UV_PROJECT_ENVIRONMENT`, identical `uv.lock` —
the branch did not touch `uv.lock`/`pyproject.toml`):

**12 failed exactly as at HEAD — every one reproduces at base.** None are
branch-introduced. (The 3 architecture-compliance failures were already
resolved on the branch by `b5149480c` and Task 11's conformance suites.)

### 8.4 Terminal outputs of the long gates

The exact tails of every long/background gate run of §8 live in
`.superpowers/sdd/2026-10-27-backend-bcs-manager-sync-implementation/task-12-report.md`
(these are outside the released repository).

## 9. File-size, schema and audit record

- **Scoped 1,000-line audit (branch-own constraint: 新增/修改源文件 ≤ 1,000 行
  — source files):** every branch-changed `apps/backend/src/**/*.py` file is
  ≤ 1,000 lines — largest: `collaborator_service.py` 991,
  `work_order_service.py` 960, `dispatcher.py` 660. The repo gate
  (`test_no_oversized_modules.py`, `src/agentclaw/**` only) is **PASS**.
- **Known follow-up (explicitly not a gate breach):**
  `tests/community/core/work_orders/test_work_order_service.py` is 1,246
  lines with branch changes. Test files have no enforced cap; the repo gate
  verifies this by scanning only `src/`. Suggested owner-split is a
  future cleanup task, not part of this release.
- `git diff --check` (merge-base..HEAD): **clean**.
- **Schema zero:** `git diff --name-only <merge-base>..HEAD -- apps/backend/migrations apps/backend/alembic` is **empty**, and a grep of the full branch diff for `CREATE TABLE` / `ADD COLUMN` / `ALTER` finds **nothing**. No `ac_task_queue` write and no `Bot.ext` sync-state field was added (grep of the branch diff and of the new `manager_sync` module: no hits). Migrations are untouched repo-wide.
- **Forced one-line fix this task had to make:** the branch-diff SAST gate
  flags `F821 undefined name 'SpaceRecord'` in
  `core/spaces/services/space_member_service.py` (Task 8's helper annotation
  without the import — latent only, `from __future__ import annotations`
  present). Fixed by adding `SpaceRecord` to the existing models import in
  the same commit as this task's deliverables; no other line of that file
  changed. Recorded as a gate-driven mechanical fix, not new scope.

## 10. External live acceptance — NOT RUN (exact reasons)

- **Scenario results: NOT RUN, feature NOT verified against any deployed
  BCS.** Reason: this session has **no controlled release-Backend instance
  and no deployed dev-BCS endpoint/credentials** (the environment's ground
  truth), so the external live scenario cannot execute here. Nothing in this
  record may be read as the feature being live-accepted.
- What was verified **here** about the suite: it collects (9 tests); with
  `RUN_ACCEPTANCE=1` and no configuration it fails loudly on every test
  (fixture error naming all 14 missing `MANAGER_SYNC_LIVE_*` keys); with the
  env unset the repo conftest skips it with a plain NOT-RUN reason; it is
  outside the release singlebox manifest; secrets come only from the
  environment; destructive drills opt in explicitly and are never allowed
  near a shared deployment.
- **Enable checklist before go-live:** run the base matrix green against the
  controlled release Backend + deployed dev BCS (backend build SHA + BCS
  version + env/tenant recorded as evidence); then the destructive drills in
  the isolated fault-capable posture (incl. the two maintenance legs). Until
  the base matrix passes, the feature stays `enabled=false` everywhere.
## 11. Validation addendum (2026-10-28) — singlebox enablement, memory coordination

This addendum records the singlebox enablement of the feature in its
**single-replica downgrade form** (`coordination_mode: memory`): the
in-process `MemoryManagerSyncCoordinator` (the same contract the conformance
suites pin) with the real trusted-App Gateway delivery. It changes nothing in
§1–§10: production stays `redis` + shared non-evicting Redis (§3), and the
external live suite (§10) remains the ONLY live feature acceptance.

### 11.1 What is now measured on singlebox that was not before

PR #3208 lowered the `bot_collaborator` singlebox floor 53.88 → 49.36 because
~1,300 manager-sync Core statements were structurally uncoverable while the
feature shipped disabled. With `/standalone start all` now enabling the
feature (memory coordination, real delivery), the dispatcher/lifecycle/retry
paths run inside the acceptance flow, so:

- `bot_collaborator.core_min_percent` is restored to **53.88** (the lowering
  is folded into this change), and
- a new `manager_sync` module registers `core_paths:
  [src/agentclaw/community/core/bot_collaborator/manager_sync/]` with the
  acceptance target `tests/community/acceptance/bot_collaborator` and a
  threshold from THIS branch's measured run (see `singlebox/ci/
  singlebox_coverage_modules.yaml`).

Rounds on this stack still cannot reach `send_confirmed`: a singlebox Bot's
stable identity `<bot_id>:<entity_id>` is never an ownership-initialized BCS
bot (no provider registration flow exists in this stack), so BCS answers
`total=0` and rounds end `send_drift`/`acknowledged-empty`. The manifest
comment records exactly that boundary.

### 11.2 Measured numbers (real run, this branch)

- Unit/contract/DI suites (re-run after the C2 re-review fixes): `tests/
  community/di` **564 passed** (incl. the `coordination_mode` parse pins, the
  SINGLEBOX memory-mode wiring pin — `HttpBcsManagersPlugin` +
  `MemoryManagerSyncCoordinator` over the test column's Recording double —
  the same-availability-checks pin (`missing-credential` also in memory
  mode), the pre/prod structural refusal pin, and the redis-mode-declared
  pin: the singlebox override never delivers in-process coordination under
  a redis declaration — `shared-redis-required`, coordinator None);
  `tests/community/core/bot_collaborator/manager_sync` **148 passed**;
  `tests/community/contracts` + the combined manager-sync run 709 passed
  on the earlier full invocation; config golden regen +
  `tests/community/config`, `test_config_isolation.py`,
  `test_profile_and_modules_for.py` green; gateway unit suite **890
  passed** (incl. the dev-seed pins: empty config seeds nothing, seeded
  key resolves through `find_app_by_credential`, schema-less-boot
  regression, idempotence, credential-drift ERROR, malformed-key
  never-legacy). `tests/community/di/test_bot_manager_sync_config.py` +
  `tests/community/contracts/test_manager_sync_coordination.py`:
  40 passed.
- Live stack: backend startup shows `[MANAGER-SYNC-LIFECYCLE] started` with
  zero `syncer unavailable` reasons; six real backend-driven rounds during
  `tests/community/acceptance/bot_collaborator` (12 passed), each forwarded
  by the gateway to the dev BCS managers endpoint (200 envelopes).
- Unified singlebox coverage (`singlebox/ci/singlebox_coverage.sh` +
  `verify_singlebox_coverage_artifacts.py`):
  `bot_collaborator` core **58.04%** (1281/2207; restored floor 53.88 — the
  PR #3208 measurement with the feature disabled was 49.86%),
  `manager_sync` core **54.03%** (442/818; threshold pinned to the measured
  value). Both module gates PASS in the real-run report; every other
  registered module passes its floor too. The run's remaining reds are
  environment-classified, none branch-introduced: `expert_chat` fails with
  the IDENTICAL measured values recorded in §8.2 attempt 3
  (core 52.34% < 61.18, router 90.91% < 100; all 12 acceptance failures are
  `expert_chat/test_caller_connection_api.py` on the host's SQLite missing
  `RETURNING` — `SQL error: near "RETURNING": syntax error`), and the BCS
  e2e coverage leg reads 37.94%/33.31% against the 41/36 defaults while
  every story-surface metric is complete (Router API 165/165, CLI 66/66) —
  current dev's larger instrumented denominator, not the 15 toml-line BCS
  delta of this change. `verify_singlebox_coverage_artifacts.py`
  consequently reports exactly those three artifacts red (summary.status,
  the expert_chat junit failures, the BCS e2e thresholds) and the rest
  present: backend coverage json/text/html, baas coverage json/text/html,
  acceptance junit/log, summary/dashboard. Full numbers and terminal
  output in `.superpowers/sdd/pr-3208-review-fixes/fix-C2-singlebox-report.md`.

### 11.3 The singlebox trusted-App chain (all dev-only literals, marked)

- Backend config: `bot_manager_sync.enabled: true`, `coordination_mode:
  memory`, `env: dev` (= `get_current_env()` under `SERVER_ENV=dev` — the
  env singlebox Bots are created with), `gateway_base_url:
  http://127.0.0.1:8889`, tenant `teamclaw` → secret name
  `singlebox_manager_sync_app_credential`.
- Credential: the deterministic dev key `SingleboxManagerSyncDevAppKey000`
  (32-char base62 — the verifier's exact shape), exported by
  `singlebox/modules/backend.sh` into the SecretResolver and seeded into the
  local gateway's in-memory `avernet_application` registry from
  `user_config.local_dev_apps` (config-gated: an empty list — every
  production config — seeds nothing; the plugin's PBKDF2 prefix+hash path is
  the production one).
- BCS: `[bot_authority] trusted_apps` rule `singlebox-manager-sync` /
  `teamclaw` / `["managers:sync"]` in `bcs-config-local.toml` — byte-exact
  app+tenant scope, deny-everything default for every other App (verified
  live: unknown app name → 403, the sync-only rule denies ownership reads).
- `memory` is refused outside the dev family: a pre/prod deployment asking
  for it raises a structural config error (pinned by test), never a silent
  degraded build.
