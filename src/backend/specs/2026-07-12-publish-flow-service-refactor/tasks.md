# Tasks: Publish Flow Service Refactor

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Base branch: `dev`. Every task must leave the full suite green
> (`pytest` under the `test` profile) and touch only what it names.

## Task 0: Verify BaaS create idempotency on `request_id` — [x] DONE
- **Goal:** Empirically determine whether BaaS `create_bot` / `create_teclaw_bot`
  dedupe a re-submitted create carrying the same deterministic `request_id`, so
  the release-handler idempotency guard is designed on fact, not assumption.
- **Files:** investigated BaaS monorepo source
  `src/baas/packages/community/src/secbaas/core/service/publish_manage/_publish_service.py`
  + `bot_build_service.py:151` (`generate_request_id`).
- **Finding (recorded in `plan.md`):** BaaS does **not** dedupe on `request_id`
  (not persisted, correlation-only). It enforces one active publish per
  `(bot_id, publish_type)`; a same-type non-stale duplicate **returns the existing
  publish** (`_publish_service.py:405-416`). ⇒ Our `ext.publish.{stage}` guard is
  the **primary** idempotency mechanism; BaaS active-publish return is a
  **secondary net** covering the create-then-crash-before-persist window. The
  missing `version` in `request_id` is inert (not a create-collision bug).
- **Done when:**
  - [x] Written conclusion in `plan.md` ("does not dedupe on `request_id`";
        guard = skip when `ext.publish.{stage}` recorded, no extra bot lookup
        needed because BaaS's active-publish return covers the pre-persist gap).
  - [x] Task 11 idempotency design references this conclusion.
- **Depends on:** —

## Task 1: Characterization tests for thin-coverage entry points — [x] DONE
- **Goal:** Pin *current* behavior of the under-tested public methods before any
  code moves, so the refactor is provably behavior-preserving.
- **Files:** `tests/community/core/service_bot/services/test_publish_flow_service.py`
- **Done when:**
  - [ ] `process()` dispatch per status characterized (today: DRAFT async-build,
        BUILT verify-release, VALIDATING online-release advance; BUILDING/
        VALIDATE_PUB/ONLINE_PUB/SUCCESS/FAILED describe-only; unknown → raise).
  - [ ] `sync_publish_progress()` wrapper: missing baas-publish-id guard,
        progress-fetch error, SUCCESS/FAILED/other dispatch, retry-flag redirect.
  - [ ] `sync_restart_progress()` (currently zero coverage): SUCCESS / FAILED /
        in-progress / missing-handle / VALIDATING-SUCCESS stable-state no-update.
  - [ ] `restart_bot()` submit path: stage resolution, `ext.restart` write,
        async scheduling, teclaw vs non-teclaw.
  - [ ] `retry()` across `source_status`: happy path, ONLINE_PUB, BUILT/DRAFT,
        non-FAILED rejection.
  - [ ] `_should_execute_upgrade_release` positive online-upgrade selection.
  - [ ] All new tests pass against the *unmodified* service.
- **Depends on:** —

## Task 2: Create `publish_flow/` package + extract `PublishExtState` — [x] DONE
- **Goal:** Stand up the new package and move the ext/state helpers into it; the
  facade delegates to `PublishExtState`. No behavior change.
- **Files:** `core/service_bot/services/publish_flow/__init__.py` (new),
  `.../publish_flow/ext_state.py` (new), `.../publish_flow_service.py`
- **Done when:**
  - [ ] `PublishExtState` holds `get_latest_ext`, `merge_and_update_ext`,
        `update_status`, `owner_id`, `clear_retry_flag`,
        `stamp_stage_on_stored_artifact` (was `_restamp_ext_artifact`),
        `stage_overrides`/`artifact_for_stage`/`store_stage_overrides`
        (`publish_flow_service.py:133-196, 2871-2939`).
  - [ ] Facade delegates to it; full suite green.
- **Depends on:** Task 1

## Task 3: Provider-behavior seam + router (kills the 6 teclaw branches) — [x] DONE
- **Goal:** Route all provider-specific behavior through a `ProviderBehavior`
  interface selected by `device_provider`, removing every inline
  `active_engine=="teclaw"` / `provider==TECLAW` conditional. (Spec goal #3.)
- **Files:** `.../publish_flow/provider_behavior.py` (new),
  `.../publish_flow_service.py`, `di/modules/service_bot_module.py`,
  `tests/community/core/service_bot/services/test_provider_behavior.py` (new)
- **Done when:**
  - [ ] `ProviderBehavior` protocol + `TeclawProviderBehavior` (stages build
        files `:373`, refreshes MCP rule `:896-906`/`:1269-1279`,
        `supports_scale=False`, `destroys_verify_bot_on_online=False`) +
        `DefaultProviderBehavior` (no-ops / `True`) + `ProviderBehaviorRouter`.
  - [ ] All 6 branch sites (`:466/480`, `:896`, `:1271`, `:1735`, `:2312`,
        `:2810`) now go through the seam; no `TECLAW` literal remains in stage
        logic.
  - [ ] DI `provider_behavior_router` provider assembles teclaw→Teclaw,
        baas/arca→Default.
  - [ ] Unit tests: router resolution + each behavior member. Full suite green
        (existing teclaw tests still assert the same outcomes).
- **Depends on:** Task 2

## Task 4: Unify verify/online release into `ReleaseStageRunner` — [x] DONE
- **Goal:** Replace the four near-duplicate release methods with one
  stage-parameterized path. (Spec goal #4.)
- **Files:** `.../publish_flow/release_stage.py` (new),
  `.../publish_flow_service.py`
- **Done when:**
  - [ ] `ReleaseStageRunner.first_release(...)` / `.upgrade_release(...)` +
        `StageSpec(stage, source_status, target_status, request_label)` replace
        `_execute_verify_first_release` (`:718`), `_execute_verify_upgrade`
        (`:793`), `_execute_first_release` (`:1070`), `_execute_upgrade_release`
        (`:1136`); includes `_resolve_verify_binding` (`:643`),
        `_should_upgrade_online` (was `_should_execute_upgrade_release` `:1023`),
        `record_release_result` (`:2774`).
  - [ ] MCP-refresh + binding-provider go through `ProviderBehavior` (Task 3).
  - [ ] Parity tests: verify/online × first/upgrade produce the same transitions
        and `ext` writes as before. Full suite green.
- **Depends on:** Task 3

## Task 5: Extract `BuildStageRunner` — [x] DONE
- **Goal:** Move the build phase into its own runner; file-staging goes through
  `ProviderBehavior.stage_build_files`.
- **Files:** `.../publish_flow/build_stage.py` (new), `.../publish_flow_service.py`
- **Done when:**
  - [ ] `BuildStageRunner.run(...)` from `_execute_build_phase` (`:419`);
        `_stage_teclaw_files` (`:373`) now lives in `TeclawProviderBehavior`.
  - [ ] Build idempotency guard present (skip build when already BUILT+ with
        artifact) but not yet wired to a task (that is Task 11).
  - [ ] Build-phase tests (arca mount, external artifact, failure) green.
- **Depends on:** Task 3

## Task 6: Extract progress-sync (mixin) — [x] DONE
- **Goal:** Move status/progress sync into its own module; destroy-verify gated
  by `ProviderBehavior`.
- **Files:** `.../publish_flow/progress_sync.py` (new), `.../publish_flow_service.py`
- **Done when:**
  - [ ] Holds `sync_publish_progress` (`:2385`), `sync_restart_progress`
        (`:2576`), `sync_scale_progress` (`:2514`), `_handle_sync_success`
        (`:2238`), `_handle_sync_failure` (`:2342`), `_update_binding_on_success`
        (`:2188`), `_mark_previous_publish_superseded` (was `_upgrade_last_publish`
        `:1913`), `_stage_for_sync`/`_stage_for_restart` (`:1881`/`:1898`),
        `get_baas_publish_progress` (`:2703`), `_approve_baas_publish` (`:2727`).
  - [ ] Destroy-verify-on-online uses `ProviderBehavior.destroys_verify_bot_on_online`.
  - [ ] Characterization tests from Task 1 still green.
- **Depends on:** Task 3

## Task 7: Extract restart/scale/rollback/eval mixins — [x] DONE (renames pending)
- **Goal:** Move the remaining operations out and apply the public + internal
  renames, updating every caller. (Spec goal #5.)
- **Files:** `.../publish_flow/restart_ops.py`, `scale_ops.py`, `rollback_ops.py`,
  `eval_publish.py` (all new), `.../publish_flow_service.py`,
  `api/publish_flow_service.py`, `adapters/http/service_bot/router_publish.py`,
  `core/quality/services/task_processor.py`
- **Done when:**
  - [ ] `RestartRunner` (`restart_bot` `:1407` + `_restart_bot_async` `:1572` +
        `_refresh_publish_handle` `:178`), `ScaleRunner` (`scale_bot` `:1708` +
        device-count helpers `:1801-1879`), `RollbackRunner` (`execute_rollback`
        `:1969` + `_destroy_bot_by_stage` `:2094` + `destroy_publish_history`
        `:3113`), `EvalPublisher`.
  - [ ] Public renames applied + callers updated: `general_publish` →
        `publish_eval_environment` (`task_processor.py:144`, Protocol),
        `general_teardown` → `teardown_eval_environment` (`task_processor.py:295`).
  - [ ] Internal renames applied: `_upgrade_last_publish` →
        `_mark_previous_publish_superseded`, `_should_execute_upgrade_release` →
        `_should_upgrade_online`, `_restamp_ext_artifact` →
        `_stamp_stage_on_stored_artifact`, `_determine_sync_stage`/
        `_determine_restart_stage` → `_stage_for_sync`/`_stage_for_restart`.
  - [ ] `PublishFlowServiceProtocol` updated for the two public renames; full
        suite green.
- **Depends on:** Task 4, Task 6

## Task 8: Slim the facade + drop the oversized-module allowlist entry — [x] DONE
- **Goal:** `publish_flow_service.py` becomes a thin facade delegating to the
  runners; every new module is under the 1000-line cap. (Spec goals #1, #2.)
- **Files:** `.../publish_flow_service.py`, `di/modules/service_bot_module.py`,
  `tests/community/architecture/test_no_oversized_modules.py`
- **Done when:**
  - [ ] Facade holds the runners; `process` dispatches via a table with a
        side-effect-free `_describe_status(record)` for the non-advancing states
        (command/query split; `/process` still inline-await here — async-submit
        lands in Task 12).
  - [ ] DI constructs/injects the runners; construction cycle intact
        (`service_bot_module.py:265` lazy provider unchanged).
  - [ ] `publish_flow_service.py` allowlist entry removed from
        `test_no_oversized_modules.py`; every publish module < 1000 lines
        (`test_no_new_oversized_files` + `test_allowlist_entries_still_oversized`
        pass). Full suite green.
- **Depends on:** Task 5, Task 7

## Task 9: Add `renew_lease` to the task-queue repository — [x] DONE
- **Goal:** Give the shared infra a lease-extension primitive (additive).
- **Files:** `core/task_queue/repository/protocol.py`,
  `plugins/task_queue_repository.py`,
  `tests/community/core/task_queue/` (repo tests)
- **Done when:**
  - [ ] `renew_lease(*, task_id, worker_id, lease_seconds) -> bool` added to the
        protocol, CAS-guarded on `claimed_by == worker_id AND status == RUNNING`,
        DB-computed `lease_expires_at = now()+lease_seconds`.
  - [ ] Implemented in the unified repo (runs on SQLite + OceanBase).
  - [ ] Tests: renews while held (extends `lease_expires_at`, returns `True`);
        returns `False` after another worker takes over / terminal. Existing
        task-queue tests green.
- **Depends on:** —

## Task 10: Worker lease-renewal heartbeat — [x] DONE
- **Goal:** Keep a long-running handler's claim alive so it is not re-claimed
  and double-executed on multi-pod.
- **Files:** `core/task_queue/services/worker.py`,
  `tests/community/core/task_queue/` (worker tests)
- **Done when:**
  - [ ] `_run_one` wraps `handler.handle` with a heartbeat task calling
        `renew_lease` every ≈ `lease_seconds/3`; cancelled on return; stops on a
        `False` return (lease already lost).
  - [ ] Test: a handler running longer than `lease_seconds` keeps its claim and
        its outcome write still succeeds (fake clock/lease + slow fake handler
        via `run_once`). Existing worker + devices-poller tests green.
- **Depends on:** Task 9

## Task 11: Durable publish task handlers + idempotency + lifecycle
- **Goal:** Model build/release/restart/poll as persisted, idempotent,
  self-chaining tasks. (Durability + issue #2.)
- **Files:** `.../publish_flow/tasks.py` (new),
  `tests/community/core/service_bot/services/test_publish_tasks.py` (new)
- **Done when:**
  - [ ] Task-type constants, payload builders + validators, enqueue helpers
        (`enqueue_verify_flow`/`enqueue_online_release`/`enqueue_restart`/
        `enqueue_progress_poll`).
  - [ ] Handlers thin over the runners: `PublishVerifyFlowHandler`
        (build+verify-release → enqueue poll), `PublishOnlineReleaseHandler`
        (→ enqueue poll), `PublishRestartHandler` (→ enqueue poll),
        `PublishProgressPollHandler` (Reschedule until terminal; drives
        `SUCCESS→UPGRADED` side-effect; reuses `sync_*`).
  - [ ] Idempotency guards per Task 0's conclusion (skip build when BUILT+; skip
        BaaS-create when `ext.publish.{stage}` recorded); successor enqueued
        before `Complete`; failed enqueue raises → Retry.
  - [ ] **First-create = Option C + invariant:** the first-release create sub-step
        checks `ext.publish.{stage}` before creating; ordering is strictly
        `create → persist ext.publish/binding → approve → enqueue poll` so an
        approved/live bot is never re-created on resume. Test the invariant: a
        re-run after `ext.publish.{stage}` is persisted (incl. post-approve) calls
        BaaS create **zero** times and re-approves idempotently. The pre-persist
        orphan window is accepted + logged (assert the log/marker for later
        sweep), not failed.
  - [ ] `PublishTaskLifecycle(LifecycleBase)` registers all four handlers.
  - [ ] Unit tests: idempotent re-run (create called once), chaining (enqueue
        successor), poll Reschedule/Complete/no-op, retry-flag routes to
        restart-sync, crash-resume (re-run from BUILT skips rebuild).
- **Depends on:** Task 8, Task 10, Task 0

## Task 12: Wire tasks into DI + facade `/process` async-submit + retry mapping
- **Goal:** Make `/process` uniform async-submit and route retry/restart through
  the durable tasks. (Intended behavior changes.)
- **Files:** `di/modules/service_bot_module.py`, `.../publish_flow_service.py`,
  `tests/community/adapters/http/service_bot/test_router_publish_coverage.py`,
  `tests/community/core/service_bot/services/test_publish_flow_service.py`
- **Done when:**
  - [ ] `publish_task_lifecycle` DI provider (singleton `LifecycleBase`) bound in
        a base-installed module; `bootstrap()` runs before `TaskWorker.startup()`.
  - [ ] `process`: DRAFT → `enqueue_verify_flow`; VALIDATING → `enqueue_online_release`
        (gate); all other states describe-only (incl. BUILT); each advancing call
        returns an "in progress" `PublishFlowResult` (no synchronous ids).
  - [ ] `retry` maps `source_status` → enqueue directly (DRAFT/BUILDING/BUILT →
        verify_flow; VALIDATING → online_release; VALIDATE_PUB/ONLINE_PUB/SUCCESS
        → restart) — **not** via `process()`; `restart_bot` → `enqueue_restart`.
  - [ ] Router coverage + `/process` tests updated to the new async-submit
        response shape. Full suite green.
- **Depends on:** Task 11

## Task 13: Enable the task-queue worker in base config + regenerate goldens
- **Goal:** Turn autonomous/durable advancement on in every profile except test.
- **Files:** `configs/application.yaml`, `configs/application-community.yaml`,
  `configs/application-test.yaml`,
  `tests/community/config/golden/{community,singlebox,test}.json`
- **Done when:**
  - [ ] `task_queue_worker: {enabled: true}` under `user_config` in base
        `application.yaml`; block removed from `application-community.yaml`;
        explicit `{enabled: false}` in `application-test.yaml`.
  - [ ] `community.json` + `singlebox.json` goldens regenerated (raw-user_config
        echo + resolved section → `enabled: true`); `test.json` stays `false`.
  - [ ] Config/golden tests pass; assert `TaskQueueWorkerConfig.enabled is False`
        under the test profile.
- **Depends on:** Task 12

## Task 14: End-to-end durable-pipeline integration tests
- **Goal:** Prove a full run advances autonomously (no manual `/sync`) and
  survives a simulated crash, for both provider families.
- **Files:** `tests/community/endpoints/` (new integration case, or extend the
  publish-flow harness), reusing the in-memory SQLite + BaaS HTTP stub +
  `run_once` worker drain.
- **Done when:**
  - [ ] DRAFT `/process` → worker drives BUILDING→BUILT→VALIDATE_PUB→VALIDATING
        (verify_flow + poll); VALIDATING `/process` → ONLINE_PUB→SUCCESS, for
        teclaw and non-teclaw.
  - [ ] Crash-resume: a `verify_flow` task interrupted after BUILT is re-claimed
        (lease-expiry) and completes to VALIDATE_PUB without a second BaaS create.
  - [ ] Gate held: the poll never advances VALIDATING→ONLINE.
- **Depends on:** Task 13

## Task 15: Tests & Verification
- **Goal:** Confirm the feature meets every spec acceptance criterion.
- **Files:** whole suite.
- **Done when:**
  - [ ] All spec `## Acceptance Criteria` boxes check off (endpoints unchanged
        except the two intended changes; transitions/persisted shape preserved;
        provider-agnostic; release dedup; ≤1000-line modules; renames + callers;
        durable/idempotent/lease-renewed tasks; async `/process`).
  - [ ] Full `pytest` suite green under the test profile;
        `test_no_oversized_modules.py` green with the allowlist entry gone.
  - [ ] Draft PR body updated to describe the two intended behavior changes.
- **Depends on:** Task 14

---

## Groups

- **Group A — Characterize & scaffold:** Tasks 0, 1
  - Theme: Establish the BaaS-idempotency fact and pin current behavior with
    tests before any code moves.
- **Group B — Structural refactor (behavior-preserving):** Tasks 2, 3, 4, 5, 6, 7, 8
  - Theme: Decompose into `publish_flow/`, add the provider-behavior seam, unify
    the release paths, apply renames, slim the facade under the size cap —
    `/process` still inline; suite green throughout.
- **Group C — Lease-renewal infra:** Tasks 9, 10
  - Theme: Additive shared-infra primitive so long tasks hold their claim.
- **Group D — Durable task pipeline:** Tasks 11, 12, 13, 14
  - Theme: Persisted, idempotent, self-chaining stage tasks; uniform async
    `/process`; worker enabled; end-to-end + crash-resume proof.
- **Group E — Verification:** Task 15
  - Theme: Final spec acceptance check.
