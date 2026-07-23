# Tasks: Online-Release Retry Regression Fix

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: `[x]` Rename the predicate to `is_current_online_deployment`

- **Goal:** Give the liveness predicate its real name and pin it as gate-only,
  before any behavior changes touch it.
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow_service.py`,
  `src/agentclaw/community/core/service_bot/services/publish_flow/tasks.py`,
  `tests/community/core/service_bot/services/test_publish_crash_windows.py`,
  `tests/community/core/service_bot/services/test_publish_tasks.py`,
  `tests/community/core/service_bot/services/test_bot_publish_service.py`
- **Done when:**
  - [ ] `publish_flow_service.py:867` method renamed; docstring states it is
        the online-release gate's predicate and must not be consulted by
        retry or restart (restart always re-deploys via BaaS).
  - [ ] `_completed_online_release_op` / `_online_release_superseded` unchanged.
  - [ ] Gate call site in `tasks.py:302` uses the new name; stale docstrings at
        `tasks.py:17-19` and `tasks.py:273-280` (pre-#341 `ext.publish.online`
        wording) rewritten to describe the ledger-driven liveness guard.
  - [ ] `grep -r is_online_release_recorded src/` under `src/backend` returns
        nothing (tests updated too; spec history docs excluded).
  - [ ] `pytest tests/community/core/service_bot/` green.
- **Depends on:** —

## Task 2: Ledger reflects deploy outcome — fail the op on observed workflow failure

- **Goal:** A deploy whose BaaS workflow failed must not read as live: the
  progress-sync failure path marks the ledger op carrying that workflow
  `FAILED`, so the gate re-runs the release and a failed deploy never
  supersedes a live one. Precondition for the retry re-route (Task 3).
- **Files:**
  `src/agentclaw/community/core/service_bot/repository/publish_operation_protocol.py`,
  `src/agentclaw/community/plugins/publish_operation_repository.py`,
  `src/agentclaw/community/core/service_bot/services/publish_flow/progress_sync_mixin.py`,
  ledger/repo + sync tests
- **Done when:**
  - [ ] `fail_by_workflow(publish_id, baas_publish_id, error) -> bool` on the
        protocol + ORM repo: finds this publish's op row by `baas_publish_id`,
        sets `FAILED` + error; permits `COMPLETED → FAILED` (outcome
        correction) and `ID_RECORDED → FAILED`; returns False (no-op) when no
        row matches or the row is already FAILED/ABANDONED.
  - [ ] `_handle_sync_failure` (`progress_sync_mixin.py:276-316`) takes
        `baas_publish_id` and calls `fail_by_workflow` before the record's
        FAILED write; both callers pass it (`advance_publish_progress`
        release wait, `sync_restart_progress` restart wait).
  - [ ] Tests: release-wait failure marks the release op FAILED; restart-wait
        failure marks the restart op FAILED; predicate false after a failed
        deploy (op was COMPLETED); a failed deploy no longer supersedes a
        genuinely live earlier release on the same bot; `fail_by_workflow`
        no-op cases covered.
  - [ ] `pytest tests/community/core/service_bot/` green.
- **Depends on:** Task 1

## Task 3: Re-route retry — dispatch by rollback status, flag only restart branches

- **Goal:** An `ONLINE_PUB`-pre-failure retry always re-enqueues
  `online_release`; the restart-vs-rerun heuristic and the predicate's retry
  consumer are deleted; `ext.retry` is written only when dispatching to restart.
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow/retry_ops_mixin.py`,
  retry tests under `tests/community/core/service_bot/services/`
- **Done when:**
  - [ ] `_retry_uses_baas_restart` deleted; module-level
        `_RESTART_RETRY_STATUSES = frozenset({VALIDATE_PUB, SUCCESS})`;
        dispatch in `retry()` computes `use_restart` from membership **before**
        the rollback write.
  - [ ] The single rollback write includes `retry=True` only when
        `use_restart`; the ONLINE_PUB/VALIDATING/verify branches clear any
        stale flag in that same write (no second ext write).
  - [ ] `retry()` / `_retry_via_restart` docstrings updated: why VALIDATE_PUB
        (verify self-advances, cannot re-enter — symmetry deferred) and
        SUCCESS (live record; gate would skip BaaS) stay on restart.
  - [ ] Tests: FAILED+source=ONLINE_PUB retry enqueues `online_release` and
        never calls `execute_restart` — both when the record's release is
        current and when it is not; FAILED+source=VALIDATE_PUB and
        FAILED+source=SUCCESS still restart; `ext.retry` present after
        restart-branch rollback, absent after release-branch rollback.
  - [ ] Test — the loop guard (end-to-end with Task 2): online deploy issued →
        workflow FAILED → retry → gate false → fresh ledger attempt →
        **second BaaS issue** → record converges (never skips, never
        strands).
  - [ ] Test: retry-then-poll for ONLINE_PUB — after the release re-run, the
        poll follows `ext.publish.online` (no `sync_restart_progress`
        redirect), covering the latent stranding bug from the plan.
  - [ ] `pytest tests/community/core/service_bot/` green.
- **Depends on:** Tasks 1, 2

## Task 4: Extract the deploy atom in `operation_runner.py`

- **Goal:** One shared open → acquire (uniform `BOT_NOT_FOUND` classification)
  → validate sequence, with abandon-on-bot-gone, usable by all three deploy
  operations; no behavior change yet.
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow/operation_runner.py`,
  new unit tests (crash-windows harness)
- **Done when:**
  - [ ] `TargetBotGoneError` defined; `acquire_deploy_workflow(runner, *,
        publish_id, kind, stage, operator, issue, bot_uuid=None, params=None)`
        implemented per the plan's contract: wraps `issue` to raise
        `TargetBotGoneError` on `{success: False, error_code: "BOT_NOT_FOUND"}`,
        abandons the op and re-raises on that signal, validates
        `baas_publish_id` (+ `bot_uuid` for creation kinds), returns the op
        un-completed.
  - [ ] Contains no skip-if-current-deployment logic and no binding/ext writes
        (asserted by a test that a live current deployment still reaches
        `issue`).
  - [ ] Unit tests: happy path records workflow id; BOT_NOT_FOUND abandons and
        raises; crash-before-record resumes via adopt (existing-bot kind) /
        re-issue (creation kind) using the `test_publish_crash_windows.py`
        seams.
  - [ ] `pytest tests/community/core/service_bot/` green.
- **Depends on:** —

## Task 5: Rebase `first_release` / `upgrade_release` onto the atom

- **Goal:** `release_stage.py` loses its hand-rolled open/acquire/validate and
  `_BotNotFoundError`; behavior byte-for-byte preserved.
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow/release_stage.py`
- **Done when:**
  - [ ] `first_release` (`release_stage.py:174-204`) uses
        `acquire_deploy_workflow`; binding + `record_release_ext` +
        `complete_operation` unchanged.
  - [ ] `upgrade_release` (`release_stage.py:274-314`) uses the atom;
        `_BotNotFoundError` deleted; `except TargetBotGoneError` → run
        `fallback` (op already abandoned by the atom — no double abandon).
  - [ ] Existing release/crash-window tests pass **unmodified** (rename-only
        edits from Task 1 aside) — the extraction is pure code motion.
- **Depends on:** Task 4

## Task 6: Rebase `execute_restart` onto the atom + crash-safe recreate leg

- **Goal:** Restart shares the atom, and its `BOT_NOT_FOUND` leg becomes
  abandon → fresh `FIRST_RELEASE` op → **new** binding → ext dual-writes,
  closing the documented orphan window (`restart_mixin.py:247-256` note).
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow/restart_mixin.py`,
  restart tests
- **Done when:**
  - [ ] Happy path (`restart_mixin.py:224-280`) runs through
        `acquire_deploy_workflow` with `kind=RESTART`; ext.restart dual-write,
        handle refresh, `complete_operation` preserved; the
        no-catch-around-acquire contract (`restart_mixin.py:273-279` comment)
        preserved for the happy path.
  - [ ] New `_recreate_restart_target`: on `TargetBotGoneError` (RESTART op
        abandoned with reason "BOT_NOT_FOUND -> recreate"), runs
        `acquire_deploy_workflow` with `kind=FIRST_RELEASE` for the same
        stage; mints a new binding via `create_release_binding` recorded into
        the op `result` (re-run skips); `_mutate_and_update_ext` writes
        `ext.binding.<stage>`, `ext.publish.<stage>`, `ext.restart.<stage>` to
        the new ids (no status change); `refresh_publish_handle` +
        `complete_operation`.
  - [ ] Known-limitation comment (`restart_mixin.py:247-256`) deleted —
        replaced by the new guarantee's doc.
  - [ ] Tests: BOT_NOT_FOUND → RESTART op ABANDONED, FIRST_RELEASE op
        COMPLETED, new binding created (old binding not reused),
        `ext.restart.<stage>` = new workflow id so `sync_restart_progress`
        still resolves progress; crash between create and record converges
        without a second orphan (crash-window seam); restart of a live,
        current deployment still issues the BaaS call (point-2 guard);
        verify-stage restart BOT_NOT_FOUND gets the same recreate.
  - [ ] `pytest tests/community/core/service_bot/` green.
- **Depends on:** Task 4 (atom); Task 1 (predicate name used in tests)

## Task 7: DI-world publish harness — LocalBaas + world fixture + baseline lifecycle

- **Goal:** A reusable DI-world harness for publish flows: the real app wiring
  (TEST profile, per-test injector, in-memory SQLite) with **local
  implementations only at system boundaries** — a new stateful LocalBaas
  double for the one boundary that has none today (`BaasService` write paths
  are documented-unmocked; `LocalHttpClient` raises on any call). Everything
  else — `PublishFlowService`, task handlers, `BotBuildService` (teclaw
  compose+freeze build), repositories, ext/state plumbing — is production
  code resolved through the injector.
- **Files:**
  `tests/community/e2e/publish_boundary/__init__.py` (new),
  `tests/community/e2e/publish_boundary/local_baas.py` (new),
  `tests/community/e2e/publish_boundary/harness.py` (new),
  `tests/community/e2e/publish_boundary/test_lifecycle_baseline.py` (new)
- **Done when:**
  - [ ] `LocalBaasService`: stateful in-memory double of the `BaasService`
        surface the publish pipeline touches (create/upgrade/restart/scale
        workflow issuance with monotonic workflow ids, per-bot workflow
        timelines for `list_bot_publishes`, workflow progress for
        `get_baas_publish_progress`, stop/destroy, container-provider
        resolution → teclaw). Test controls: advance a workflow
        `ACTIVE → SUCCESS/FAILED`, delete a bot (subsequent upgrade/restart
        returns `BOT_NOT_FOUND`), and a call journal for issue-count
        assertions.
  - [ ] Harness fixture (`publish_world`): builds the TEST-profile injector
        with the LocalBaas bound over `BaasService`/`BaasServiceProtocol`
        (module-override pattern per `testing_devices_module.py`), attaches
        the app, exposes `world`, an httpx client, and
        `drain_tasks()` — loops `TaskWorker.run_once()` to quiescence so
        durable publish tasks (`verify_flow`, `online_release`,
        `progress_poll`, `restart`) run deterministically between steps.
        Object-storage / engine-ext / approval boundaries use the existing
        local plugins (`plugins/local/oss_storage.py`,
        `engine_ext_client.py`, `bot_publish_approval.py`).
  - [ ] Baseline scenario **L0 — full teclaw lifecycle via endpoints**:
        `create_first_publish` → drain (build + verify release) → verify wait
        → LocalBaas SUCCESS → drain → approval (local) → `process_publish` →
        drain (online first release) → LocalBaas SUCCESS → drain →
        record SUCCESS. Asserts: verify+online `FIRST_RELEASE` ops COMPLETED,
        bindings ACTIVE, `is_current_online_deployment` true, exactly one
        online bot in LocalBaas.
  - [ ] Harness does not touch `_flows/`, acceptance manifests, or coverage
        gates; confirm against the `Pre-push Module Selection` contract in
        `AGENTS.md` before finalizing file placement.
- **Depends on:** — (harness itself); scenarios in Tasks 8-9 depend on the
  production changes.

## Task 8: DI-world regression scenarios — retry, failure outcome, cross-record liveness

- **Goal:** The regression fix proven end-to-end through the production
  wiring: retry recovery, deploy-failure outcome correction, and
  cross-publish liveness — all via endpoints + task drains.
- **Files:**
  `tests/community/e2e/publish_boundary/test_retry_and_failure_flows.py` (new)
- **Done when:**
  - [ ] **R1 — online BaaS-wait failure → retry re-issues (the core loop
        guard):** v1 live → `upgrade_publish` v2 → online upgrade issued →
        LocalBaas fails the workflow → drain → v2 FAILED and its UPGRADE op
        outcome-corrected to FAILED → `retry_publish` → drain → a **second**
        issue reaches LocalBaas (fresh attempt) → LocalBaas SUCCESS → drain →
        v2 SUCCESS. Asserts: exactly 2 upgrade issues, no restart issuance,
        no duplicate bot/binding.
  - [ ] **R2 — failed deploy never supersedes:** at R1's failure point
        (before retry), v1's `is_current_online_deployment` still true; after
        R1 completes, v1 superseded (status UPGRADED), v2 current.
  - [ ] **R3 — retry interleaved with a later publish:** v1 FAILED in
        ONLINE_PUB (release work failed pre-issue) → v2 publishes and lands
        on the shared bot → v1 `retry_publish` → v1's release path re-runs
        (upgrade over the shared bot; no restart of any stale workflow); final
        ledger timeline consistent, exactly one bot.
  - [ ] **R4 — verify-stage retry unchanged (deferred-symmetry guard):**
        verify BaaS-wait failure → `retry_publish` → restart branch fires
        (RESTART op at verify stage, `ext.retry` set); no verify release
        re-run.
  - [ ] **R5 — SUCCESS-record retry (failed restart-sync):** live record's
        restart workflow fails → record FAILED (source SUCCESS) → retry →
        re-restart via the restart branch (restart op attempt+1; release gate
        never consulted).
  - [ ] **R6 — online_release task redelivery idempotency:** after the online
        release issued, re-run the task handler (simulated lease-expiry
        redelivery) → gate skips (release live/in-flight), no second issue in
        LocalBaas.
- **Depends on:** Tasks 3, 7

## Task 9: DI-world operational scenarios — chains, rollback, restart, recreate

- **Goal:** The multi-record operational flows on a shared online bot proven
  through production wiring: upgrade chains, rollback-then-re-promote,
  restart semantics, and the recreate leg.
- **Files:**
  `tests/community/e2e/publish_boundary/test_chain_and_restart_flows.py` (new)
- **Done when:**
  - [ ] **C1 — upgrade chain:** v1 first-release → SUCCESS → v2
        `upgrade_publish` → SUCCESS on the same bot; v1 predicate false /
        status UPGRADED, v2 true; exactly one online bot ever created.
  - [ ] **C2 — rollback-then-re-promote (#5984 shape):** v2 live → rollback
        (ROLLBACK_DEPLOY lands on the bot, v2 demoted) → re-promote v2
        through verify → online → gate re-runs the release (predicate false
        at entry) → v2 current again; no stranded poll (the original #341
        symptom asserted dead: `ext.publish.online` present, poll advances).
  - [ ] **C3 — restart always hits BaaS:** v live and current →
        `restart_publish` → RESTART workflow issued in LocalBaas (call
        journal) despite the release being current; restart-sync drives it;
        record stays SUCCESS.
  - [ ] **C4 — restart recreate on gone bot:** delete the bot in LocalBaas →
        `restart_publish` → RESTART op ABANDONED, FIRST_RELEASE op COMPLETED,
        **new** bot + **new** binding (old binding not reused),
        `ext.binding/publish/restart.<stage>` updated, restart-status
        endpoint still resolves progress; record status unchanged.
  - [ ] **C5 — recreate after upgrade chain:** C1 then C4 — recreated
        record's predicate true (FIRST_RELEASE/UPGRADE coexistence,
        max-by-`baas_publish_id`).
  - [ ] Every scenario asserts no duplicate bots/bindings in LocalBaas and a
        consistent ledger timeline (`list_by_bot`).
- **Depends on:** Tasks 6, 7

## Task 10: Full-suite verification & spec acceptance check

- **Goal:** Feature meets every spec acceptance criterion; branch is
  push-clean against the release target.
- **Files:** — (verification only)
- **Done when:**
  - [ ] Full `pytest tests/community/core/service_bot/` and the new
        `tests/community/e2e/publish_boundary/` green; no other backend suite
        regressed (run the module-selection pre-push contract).
  - [ ] Every `spec.md` acceptance criterion checked off against a concrete
        test or diff (verify-flow-untouched confirmed by `git diff` showing no
        verify release/retry logic edits beyond renames/docstrings).
  - [ ] Pre-push hook run with merge target `origin/REL20260723`
        (`avernet.prePush.mergeTarget` already configured).
- **Depends on:** Tasks 1-9

---

## Groups

> Groups bundle tasks into end-to-end units. `implement` executes one group at
> a time and runs code review on each group before moving on.

- **Group A — Predicate + ledger outcome + retry re-route (the regression fix):** Tasks 1, 2, 3
  - Theme: the ledger learns deploy outcome, retry stops consulting the
    liveness predicate, ONLINE_PUB retries always re-drive the release path,
    and the predicate gets its gate-only name.
- **Group B — Deploy atom + restart recreate:** Tasks 4, 5, 6
  - Theme: one shared crash-safe deploy shape; restart's BOT_NOT_FOUND leg
    becomes idempotent with a fresh op + new binding.
- **Group C — DI-world cross-boundary coverage:** Tasks 7, 8, 9
  - Theme: LocalBaas + production wiring; retry/failure and
    chain/restart flows driven through endpoints with deterministic task
    drains.
- **Group D — Verification:** Task 10
  - Theme: final spec acceptance check.
