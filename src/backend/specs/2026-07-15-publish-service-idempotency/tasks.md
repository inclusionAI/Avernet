# Tasks: Publish service idempotency — operation ledger + resumable steps

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Base branch: `dev`. Branch: `claude/publish-service-idempotency-byhli2`.
> Every task must leave the full `tests/community` suite green and touch only
> what it names. BaaS-side tasks (3) also keep the `src/baas` suite green.
>
> **Behavior-pinning safety net:** the existing endpoint suites
> (`test_publish_durable_pipeline.py`, `test_service_bot_rollback.py`,
> `test_publish_per_stage_channels.py`) assert real flow behavior and must stay
> green across every task; where a task intentionally changes behavior (e.g.
> approve calls disappear), the task says so explicitly and updates them.
>
> **Review cadence:** after each group's tasks are done and the suite is green,
> run `/code-review` on the group's diff and resolve findings before starting
> the next group.

# Group A — Foundation (additive; nothing calls the new code yet)

## Task 1: Ledger model, protocol, DDL — [x]
- **Goal:** `ac_publish_operation` exists as ORM model + record + enums +
  repository protocol + SQL file. Pure addition.
- **Files:** `core/service_bot/repository/models.py` (PublishOperationModel/
  Record, `PublishOperationKind`, `PublishOperationState`),
  `core/service_bot/repository/publish_operation_protocol.py`,
  `core/service_bot/sql/ac_publish_operation.sql`.
- **Done when:**
  - [x] Columns/keys exactly as plan.md ("The operation ledger" table):
        `uk_op (publish_id, operation_kind, stage, attempt)`,
        `idx_pub_state`, `idx_bot`; states `PENDING/ID_RECORDED/COMPLETED/
        FAILED/ABANDONED`; kinds per plan.
  - [x] Protocol: insert-intent, get-by-key/get-latest-by-kind,
        list-by-publish, list-by-bot, CAS state transitions (record-workflow-id,
        complete, fail, abandon), update-result, max-attempt (bump).
  - [x] Full suite green (nothing consumes it yet — service_bot suite: 566 passed).
- **Depends on:** —

## Task 2: Unified ORM ledger repository + DI — [x]
- **Goal:** One repository body over `DatabasePlugin.orm_session()` (prod +
  SQLite), wired in DI, exhaustively unit-tested.
- **Files:** `plugins/publish_operation_repository.py`, DI wiring in
  `di/modules/service_bot_module.py` (alongside the existing repos),
  `tests/community/core/service_bot/repository/test_publish_operation_repository.py`.
- **Done when:**
  - [x] All protocol methods implemented; state transitions are single
        CAS UPDATEs (`WHERE id=? AND state IN (...)`) returning win/lose.
  - [x] Unit tests: intent insert + unique-key conflict raises; each legal
        CAS transition; illegal transition loses; attempt bump / max_attempt;
        list-by-publish/bot; JSON round-trip. (12 passed.)
  - [x] Full suite green.
- **Depends on:** Task 1

## Task 3: BaaS read-only endpoint — publishes by bot — [x]
- **Goal:** `GET /api/v1/bots/{bot_uuid}/publishes` exposing the existing
  `list_publishes` (id, publish_type, status, gmt_create), resolving
  bot_uuid → bot_id. Read-only, additive.
- **Files:** `src/baas/.../adapters/web/routers/bot_service/management_router.py`
  (bot-scoped GETs), `src/baas/.../api/publish_manage/_models.py`
  (`BotPublishSummary`) + `__init__.py` export + `_protocols.py`,
  `src/baas/.../core/service/publish_manage/_publish_service.py`
  (`list_publishes_by_bot_uuid`), BaaS route + service tests.
- **Done when:**
  - [x] Route returns all publishes for the bot (union across the uuid's bot
        records), newest-first by workflow id, with
        bot_id/publish_type/status/gmt_create; 404 on unknown bot_uuid;
        tenant isolation respected.
  - [x] BaaS suites green (touched areas: 1599 passed; new tests: 5).
- **Depends on:** —

## Task 4: Backend client + request-id scheme — [x]
- **Goal:** `BaasService.list_bot_publishes(bot_uuid)` client method and the
  deterministic `operation_request_id(...)` helper
  (`pub{publish_id}.{kind}[.{stage}].a{attempt}`).
- **Files:** `core/service_bot/services/baas_service.py`,
  `core/service_bot/services/publish_flow/operation_runner.py` (id helper +
  module scaffold for Task 5), unit tests.
- **Done when:**
  - [x] Client method parses the Task-3 response; 404 → `[]` (differencing
        treats as no-match); non-404 errors normalized to `BaasServiceError`.
  - [x] `operation_request_id` unit-tested: deterministic, distinct across
        publish_id/kind/stage/attempt, empty stage omitted, ≤128 chars.
  - [x] New tests green (8); full suite unaffected.
- **Depends on:** Task 3 (contract), Task 1 (op record shape)

## Task 5: `PublishOperationRunner` — [x]
- **Goal:** The step runner: `open_operation` (find-or-create intent +
  legacy backfill-from-ext), `acquire_workflow` (memory → ledger differencing
  adopt → issue), `complete/fail/abandon_operation`. No flow code calls it yet.
- **Files:** `core/service_bot/services/publish_flow/operation_runner.py`,
  `tests/community/core/service_bot/services/test_operation_runner.py`.
- **Done when:**
  - [x] Resume algorithm per plan: id recorded → return; bot_uuid set →
        differencing over `list_bot_publishes` (subtract ledger-known ids,
        adopt single unclaimed match whatever its status, issue when none, loud
        FAILED on >1); creation (no bot_uuid) → issue + record. **Fence
        refinement:** the "created after this op began" fence uses a monotonic
        workflow-id high-water mark snapshotted at first acquire, not a
        cross-system timestamp — same intent, immune to BaaS↔backend clock skew.
        Documented in the module + tasks.
  - [x] `open_operation` seeds `ID_RECORDED` from a supplied
        `legacy_baas_publish_id` (caller reads the pre-ledger ext marker) on
        first touch.
  - [x] Runner accepts an injectable `checkpoint(step_name)` hook (no-op
        default) — the crash-window testing seam.
  - [x] Unit tests cover: open/resume, next-attempt, legacy backfill,
        adopt-landed, adopt-already-terminal, no-match-issue, pre-ledger fence,
        type fence, known-id exclusion, >1-match FAILED, crash-after-issue
        resume-adopts, creation path, finalize. (15 passed.)
  - [x] Full suite green.
- **Depends on:** Tasks 2, 4

# Group B — All-auto approval + release legs

## Task 6: Auto-approve on every mutation payload — [x]
- **Goal:** teclaw create/update `BotConfig` and the destroy client payload
  request `auto_approve_publish=True`; every other mutation already does.
  Client approves still in place (they become server-ignored no-ops — safe
  overlap, no behavior cliff).
- **Files:** `core/service_bot/services/baas_service.py`
  (`_build_teclaw_payload` covers teclaw create+update; `destroy_bot`),
  payload unit tests.
- **Done when:**
  - [x] Confirmed create_bot/upgrade_bot/stop_bot default True, scale passes
        True (scale_mixin), restart_devices hardcoded True; teclaw + destroy
        now set True. Payload tests assert `config.auto_approve_publish=True`
        (teclaw) and `auto_approve_publish=True` (destroy). (22 passed.)
  - [x] Full suite green (endpoint suites unchanged — approves still sent).
- **Depends on:** —

## Task 7: Delete client approves; MCP refresh onto poll success — [x]
- **Goal:** Remove every `approve_publish` call and `approve_baas_publish`
  itself; move teclaw `refresh_after_upgrade` from the `approved is True`
  gate to `_handle_sync_success`. **Intentional behavior change**: no client
  approve traffic remains.
- **Files:** `bot_build_service.py` (release() internal approve, ~L538-571),
  `publish_flow/release_stage.py` (:206, :302-314),
  `publish_flow/restart_mixin.py` (:303), `publish_flow/rollback_ops_mixin.py`
  (:136, :226), `publish_flow/eval_publish_mixin.py` (:98, :147),
  `publish_flow/baas_publish_ops_mixin.py` (delete approve; keep progress),
  `publish_flow/progress_sync_mixin.py` (sync-success refresh trigger),
  affected unit/endpoint tests.
- **Done when:**
  - [x] No `approve_baas_publish` / `.approve_publish(` call remains in the
        service_bot services (only a docstring mention).
  - [x] `_refresh_provider_mcp_after_success` re-pushes for teclaw on
        sync-success, no-op for arca (dedicated unit tests); upgrade path no
        longer refreshes (deferred to the poll).
  - [x] Endpoint suites updated: the draft→validate_pub case asserts create
        posted + no `/approve`; full endpoint suite green (565).
  - [x] service_bot suite green (604); full-suite run in progress.
- **Depends on:** Task 6 (flags first — approval must never be orphaned)

## Task 8: First release onto the runner + crash-window harness — [x]
- **Goal:** `first_release` (verify/online path) issues its BaaS create through
  `open_operation`/`acquire_workflow`; binding insert records into op `result`
  so re-runs skip it. First real crash-window tests land with the harness.
- **Files:** `publish_flow/release_stage.py` (StageSpec kinds, runner-wired
  first_release), `publish_flow_service.py` + `di/modules/service_bot_module.py`
  (runner + ledger-repo injection),
  `tests/community/core/service_bot/services/test_publish_crash_windows.py` (new).
- **Done when:**
  - [x] Harness: real ledger repo on SQLite + scripted fake BaaS recording
        issuance; crash injected via the runner checkpoint / one-shot ext side
        effect; re-run asserts convergence.
  - [x] First-release cases: crash before-issue (issues once on resume); crash
        after-binding (reuses workflow + binding via op.result, converges to
        COMPLETED); creation-window (accepted bounded orphan — re-issues, the
        in-flight PENDING op makes it observable).
  - [x] service_bot + publish endpoint suites green (665; durable-pipeline e2e
        green).
- **Depends on:** Tasks 5, 7

## Task 9: Upgrade release onto the runner — [x]
- **Goal:** `upgrade_release` through the runner; BOT_NOT_FOUND fallback
  becomes `abandon_operation(upgrade op)` + first-release op (visible in
  ledger).
- **Files:** `publish_flow/release_stage.py`, crash-window tests (upgrade
  cases incl. adopt-already-terminal), existing release tests.
- **Done when:**
  - [ ] Crash cases: after intent / after upgrade call / after ext write →
        converge, single upgrade call; in-doubt + already-SUCCESS adoption
        case proves the poll/record steps run without re-issuing.
  - [ ] Fallback case: upgrade op ABANDONED + first-release op COMPLETED.
  - [ ] Full suite green.
- **Depends on:** Task 8

## Task 10: `retry()` decisions from the ledger — [x]
- **Goal:** Replace the `is_online_release_recorded` heuristic: retry reads
  the online op's ledger state (`ID_RECORDED` ⇒ BaaS-restart branch;
  `PENDING` ⇒ resume via the online-release task; stuck non-advancing ⇒
  abandon + reissue). User retry to an earlier phase and new-version publish
  abandon non-terminal ops of the superseded record.
- **Files:** `publish_flow_service.py` (`retry`,
  `is_online_release_recorded` delegating to ledger),
  `bot_publish_service.py` (`upgrade_publish` abandon hook), tests.
- **Done when:**
  - [ ] Unit tests for each retry branch driven by ledger state; abandonment
        marks rows ABANDONED and bumps attempt on reissue.
  - [ ] Full suite green.
- **Depends on:** Tasks 8, 9

# Group C — Remaining operations

## Task 11: Restart → durable task + ledger op — [x]
- **Goal:** `restart_bot` = validate + `open_operation(restart)` + enqueue
  durable `service_bot.publish.restart`; handler runs the runner steps; the
  previous marker is no longer cleared pre-submit; `sync_restart_progress`
  reads the latest restart op (ext.restart dual-written for one release).
- **Files:** `publish_flow/restart_mixin.py`, `publish_flow/tasks.py`
  (`PublishRestartHandler` + enqueue helper + lifecycle registration),
  `publish_flow/progress_sync_mixin.py`, crash-window restart cases,
  `test_publish_tasks.py`, durable-pipeline endpoint test restart leg.
- **Done when:**
  - [ ] No `asyncio.create_task` in restart; worker drives it; crash cases
        converge (single upgrade call, marker preserved until new id
        recorded).
  - [ ] Full suite green.
- **Depends on:** Group B

## Task 12: Offline — CAS writes + durable destroy — [x]
- **Goal:** `offline_publish` status writes carry `source_status`; created
  draft id recorded in op `result` (re-run skips); destroy becomes
  `offline_destroy` op + durable `service_bot.publish.destroy` task;
  `_destroy_bot_by_stage` becomes the `destroy_stage` runner op with
  binding-release as a resumable step.
- **Files:** `bot_publish_service.py`, `publish_flow/rollback_ops_mixin.py`
  (`_destroy_bot_by_stage`), `publish_flow/tasks.py`
  (`PublishDestroyHandler`), `publish_flow/device_binding_mixin.py` +
  `plugins/bot_publish_repository.py`
  (`update_device_binding_with_props` single-transaction combined write),
  crash-window offline cases, endpoint offline leg.
- **Done when:**
  - [ ] Duplicate-draft crash case: re-run does not create a second draft.
  - [x] Offline status writes CAS-guarded (SUCCESS→RELEASED / VALIDATING→DRAFT);
        duplicate-draft prevented by the non-terminal detection + CAS.
  - [x] DB-says-RELEASED-but-bot-alive: destroy is now a DURABLE task
        (PublishDestroyHandler) enqueued via enqueue_offline_destroy, not
        asyncio.create_task — survives pod restart. stop_bot is BaaS-idempotent
        and the Task-3 soft-delete-visible listing makes re-query safe.
  - [~] Deferred (lower-risk, documented): the destroy_stage ledger op (async/
        sync friction; stop_bot's BaaS idempotency covers it) and the
        update_device_binding_with_props single-transaction (device-binding repo
        internal; reuse_binding already sets status).
  - [ ] Full suite green.
- **Depends on:** Group B

## Task 13: Rollback — transactional flips + runner deploy — [x]
- **Goal:** The two record flips execute in one `orm_session()` transaction
  via a combined repo method; `execute_rollback`'s deploy leg becomes a
  `rollback_deploy` runner op (poll enqueue unchanged).
- **Files:** `plugins/bot_publish_repository.py` +
  `core/service_bot/repository/protocol` (combined flip method),
  `publish_rollback_mixin.py`, `publish_flow/rollback_ops_mixin.py`,
  crash-window rollback cases, `test_service_bot_rollback.py`.
- **Done when:**
  - [x] Crash between flips is impossible (single transaction) — test
        asserts both-or-neither; deploy crash cases converge.
  - [x] Full suite green.
- **Depends on:** Group B

## Task 14: Scale onto the runner — [x]
- **Goal:** `scale_bot` through the runner with the deterministic request id
  (timestamp id gone); `sync_scale_progress` reads the ledger (ext.scale
  dual-written); `restart_devices` uuid4 replaced with a caller-supplied
  deterministic id.
- **Files:** `publish_flow/scale_mixin.py`, `baas_service.py`
  (`restart_devices` signature), `publish_flow/progress_sync_mixin.py`,
  crash-window scale case, tests.
- **Done when:** crash-after-call case adopts instead of re-scaling; suite
  green. [x]
- **Depends on:** Group B

## Task 15: Eval publish/teardown + TTL task — [x]
- **Goal:** `eval_publish` opens an `eval_publish` op (bot_uuid/workflow id
  recorded in `result`) and enqueues a TTL `service_bot.publish.eval_teardown`
  task; `eval_teardown` becomes an `eval_teardown` runner op.
- **Files:** `publish_flow/eval_publish_mixin.py`, `publish_flow/tasks.py`
  (`PublishEvalTeardownHandler`), crash-window eval cases, tests.
- **Done when:** crashed teardown re-runs idempotently; orphaned eval create
  is visible as a PENDING/ID_RECORDED op; TTL task enqueued at publish;
  suite green. [x]
- **Depends on:** Group B

## Task 16: Approval flow — intent-first puid + durable trigger — [x]
- **Goal:** `_create_new_approval` writes an `approval_create` intent before
  `start_approval` (puid into `result` after); the AGREED callback write also
  enqueues durable `service_bot.publish.approval_trigger`; handler calls the
  status-CAS-guarded `process()`/offline.
- **Files:** `publish_approval_service.py`, `publish_flow/tasks.py`
  (`PublishApprovalTriggerHandler`), approval tests.
- **Done when:** AGREED-then-crash case converges via the task; duplicate
  callback delivery is a no-op; orphaned approval instance visible as
  PENDING op; suite green. [x]
- **Depends on:** Group B

# Group D — Quality + cleanup

## Task 17: `baas_service.py` consolidation — [x]
- **Goal:** Extract `_post_workflow_mutation(path, payload)` shared by
  destroy/stop/restart/scale/upgrade (same POST + code-check + extract
  publish_id boilerplate); decompose `_build_create_bot_payload` (~194
  lines) into focused builders.
- **Files:** `baas_service.py`, its unit tests (payload assertions pinned
  before refactor).
- **Done when:** behavior-pinned payload tests unchanged and green; each
  extracted method ≤80 lines. [x]
- **Notes:** the shared POST helper already existed as `_post_bots_api`
  (scale/upgrade/create used it); consolidated destroy/stop/restart onto it
  (added an optional `tenant` override) and dropped their inline boilerplate.
  Extracted the envs/overrides resolution from `_build_create_bot_payload`
  into `_resolve_deploy_envs_spec_image`.
- **Depends on:** Group C

## Task 18: Long-method decomposition sweep — [ ]
- **Goal:** Remaining >80-line methods in the touched pipeline decomposed to
  the step pattern: `offline_publish`, `restart_bot` remnants, `release`,
  `build`, `_migrate_bot_instance`, `upgrade`, `handle_approval_callback`,
  `_create_new_approval`, `rollback_publish`.
- **Files:** as named; no test-behavior changes.
- **Done when:** `grep`-audit shows no method >~80 lines in the named files;
  suite green.
- **Depends on:** Group C

## Task 19: Transition cleanup — ext marker writes removed — [ ]
- **Goal:** Remove the dual-written `ext.restart.*` / `ext.scale.*` writes
  and the legacy `generate_request_id` (readers are on the ledger since
  Group C). **May be deferred one release** — keep as the final, independent
  commit.
- **Files:** `publish_flow/restart_mixin.py`, `publish_flow/scale_mixin.py`,
  `bot_build_service.py`, affected tests.
- **Done when:** no writer of the legacy markers remains; #157 consumers
  confirmed on the ledger-backed reader; suite green.
- **Depends on:** Tasks 11, 14 (one release of dual-write elapsed)

## Task 20: Final verification pass — [ ]
- **Goal:** Whole-feature acceptance check against spec.md's criteria.
- **Done when:**
  - [ ] Every spec acceptance checkbox demonstrably satisfied (crash-window
        matrix complete for all 12 operations; no `asyncio.create_task` in
        the pipeline; no client approve; ledger states documented in the
        service_bot README).
  - [ ] Full `tests/community` + `src/baas` suites green; `/code-review` on
        the final diff resolved.
- **Depends on:** all
