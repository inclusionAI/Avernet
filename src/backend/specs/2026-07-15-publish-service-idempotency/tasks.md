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

## Task 2: Unified ORM ledger repository + DI — [ ]
- **Goal:** One repository body over `DatabasePlugin.orm_session()` (prod +
  SQLite), wired in DI, exhaustively unit-tested.
- **Files:** `plugins/publish_operation_repository.py`, DI wiring in
  `di/modules/service_bot_module.py` (alongside the existing repos),
  `tests/community/core/service_bot/repository/test_publish_operation_repository.py`.
- **Done when:**
  - [ ] All protocol methods implemented; state transitions are single
        CAS UPDATEs (`WHERE id=? AND state=?`) returning win/lose.
  - [ ] Unit tests: intent insert + unique-key conflict → existing row
        returned; each legal CAS transition; illegal transition loses;
        attempt bump creates a fresh row keyed `attempt+1`.
  - [ ] Full suite green.
- **Depends on:** Task 1

## Task 3: BaaS read-only endpoint — publishes by bot — [ ]
- **Goal:** `GET /api/v1/bots/{bot_uuid}/publishes` exposing the existing
  `list_publishes` (id, publish_type, status, gmt_create), resolving
  bot_uuid → bot_id. Read-only, additive.
- **Files:** `src/baas/.../adapters/web/routers/bot_service/publish_router.py`
  (or management_router — follow whichever owns bot-scoped GETs),
  `src/baas/.../api/publish_manage/_models.py` (response model),
  BaaS route tests.
- **Done when:**
  - [ ] Route returns all publishes for the bot, newest first, with
        publish_type/status/gmt_create; 404 on unknown bot_uuid; tenant
        isolation respected.
  - [ ] BaaS suite green.
- **Depends on:** —

## Task 4: Backend client + request-id scheme — [ ]
- **Goal:** `BaasService.list_bot_publishes(bot_uuid)` client method and the
  deterministic `operation_request_id(op)` helper
  (`pub{publish_id}.{kind}.{stage}.a{attempt}`).
- **Files:** `core/service_bot/services/baas_service.py`,
  `core/service_bot/services/publish_flow/operation_runner.py` (id helper can
  live here), unit tests.
- **Done when:**
  - [ ] Client method parses the Task-3 response; error-normalized like
        sibling GETs.
  - [ ] `operation_request_id` unit-tested: deterministic, distinct across
        kind/stage/attempt, ≤128 chars.
  - [ ] Full suite green.
- **Depends on:** Task 3 (contract), Task 1 (op record shape)

## Task 5: `PublishOperationRunner` — [ ]
- **Goal:** The step runner: `open_operation` (find-or-create intent +
  legacy backfill-from-ext), `acquire_workflow` (memory → ledger differencing
  adopt → issue), `complete/fail/abandon_operation`. No flow code calls it yet.
- **Files:** `core/service_bot/services/publish_flow/operation_runner.py`,
  `tests/community/core/service_bot/services/test_operation_runner.py`.
- **Done when:**
  - [ ] Resume algorithm per plan: id recorded → return; bot_uuid set →
        differencing over `list_bot_publishes` (subtract ledger-known ids,
        fence `gmt_create >= op.gmt_create` + publish-type map, adopt single
        unclaimed match whatever its status, issue when none, loud FAILED on
        >1); creation (no bot_uuid) → issue + record (bounded-orphan window).
  - [ ] `open_operation` seeds `ID_RECORDED` rows from legacy ext markers
        (`ext.publish.<stage>`, `ext.restart.<stage>`, `ext.scale.publish_id`)
        on first touch of a pre-ledger record.
  - [ ] Runner accepts an injectable `checkpoint(step_name)` hook (no-op
        default) — the crash-window testing seam.
  - [ ] Unit tests cover: adopt-pending, adopt-already-SUCCESS,
        adopt-already-FAILED, no-match-issue, pre-ledger fence, type fence,
        >1-match FAILED, checkpoint hook firing order.
  - [ ] Full suite green.
- **Depends on:** Tasks 2, 4

# Group B — All-auto approval + release legs

## Task 6: Auto-approve on every mutation payload — [ ]
- **Goal:** teclaw create/update `BotConfig` and the destroy client payload
  request `auto_approve_publish=True`; every other mutation already does.
  Client approves still in place (they become server-ignored no-ops — safe
  overlap, no behavior cliff).
- **Files:** `core/service_bot/services/baas_service.py`
  (`_build_teclaw_payload` / `create_teclaw_bot` / `update_teclaw_bot` /
  `destroy_bot`), payload unit tests.
- **Done when:**
  - [ ] Payload tests assert `auto_approve_publish=True` (or
        `config.auto_approve_publish=True`) on create/update/teclaw-create/
        teclaw-update/stop/scale/destroy/restart-devices payloads.
  - [ ] Full suite green (endpoint suites unchanged — approves still sent).
- **Depends on:** —

## Task 7: Delete client approves; MCP refresh onto poll success — [ ]
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
  - [ ] `grep -r approve_publish src/backend/src/agentclaw/community/core/service_bot/services` →
        no mutation-path hits.
  - [ ] `refresh_after_upgrade` fires exactly once on sync-success of an
        upgrade-kind operation for teclaw (test), never for arca (no-op).
  - [ ] Endpoint suites updated: no approve call expected anywhere.
  - [ ] Full suite green.
- **Depends on:** Task 6 (flags first — approval must never be orphaned)

## Task 8: First release onto the runner + crash-window harness — [ ]
- **Goal:** `first_release` (verify/online/eval-create path) issues its BaaS
  create through `open_operation`/`acquire_workflow`; binding insert and
  `record_release_ext` record into op `result` so re-runs skip them. First
  real crash-window tests land with the harness.
- **Files:** `publish_flow/release_stage.py`, `publish_flow_service.py`
  (runner injection), `tests/community/core/service_bot/services/test_publish_crash_windows.py`
  (new: harness + first-release cases).
- **Done when:**
  - [ ] Harness per plan: real repos on SQLite + scripted fake BaasService
        recording mutation calls; parametrized `(operation, crash_after_step)`.
  - [ ] First-release cases: crash after intent / after create / after
        binding / after ext write → re-run converges; exactly one create
        reached the fake; auto-approve requested; no approve call; workflow
        id in ledger; record status correct. Creation-window case asserts
        the abandoned PENDING row (bounded orphan) is visible.
  - [ ] Full suite green.
- **Depends on:** Tasks 5, 7

## Task 9: Upgrade release onto the runner — [ ]
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

## Task 10: `retry()` decisions from the ledger — [ ]
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

## Task 11: Restart → durable task + ledger op — [ ]
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

## Task 12: Offline — CAS writes + durable destroy — [ ]
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
  - [ ] DB-says-RELEASED-but-bot-alive crash case: destroy task re-runs to
        completion; binding RELEASED.
  - [ ] Full suite green.
- **Depends on:** Group B

## Task 13: Rollback — transactional flips + runner deploy — [ ]
- **Goal:** The two record flips execute in one `orm_session()` transaction
  via a combined repo method; `execute_rollback`'s deploy leg becomes a
  `rollback_deploy` runner op (poll enqueue unchanged).
- **Files:** `plugins/bot_publish_repository.py` +
  `core/service_bot/repository/protocol` (combined flip method),
  `publish_rollback_mixin.py`, `publish_flow/rollback_ops_mixin.py`,
  crash-window rollback cases, `test_service_bot_rollback.py`.
- **Done when:**
  - [ ] Crash between flips is impossible (single transaction) — test
        asserts both-or-neither; deploy crash cases converge.
  - [ ] Full suite green.
- **Depends on:** Group B

## Task 14: Scale onto the runner — [ ]
- **Goal:** `scale_bot` through the runner with the deterministic request id
  (timestamp id gone); `sync_scale_progress` reads the ledger (ext.scale
  dual-written); `restart_devices` uuid4 replaced with a caller-supplied
  deterministic id.
- **Files:** `publish_flow/scale_mixin.py`, `baas_service.py`
  (`restart_devices` signature), `publish_flow/progress_sync_mixin.py`,
  crash-window scale case, tests.
- **Done when:** crash-after-call case adopts instead of re-scaling; suite
  green.
- **Depends on:** Group B

## Task 15: Eval publish/teardown + TTL task — [ ]
- **Goal:** `eval_publish` opens an `eval_publish` op (bot_uuid/workflow id
  recorded in `result`) and enqueues a TTL `service_bot.publish.eval_teardown`
  task; `eval_teardown` becomes an `eval_teardown` runner op.
- **Files:** `publish_flow/eval_publish_mixin.py`, `publish_flow/tasks.py`
  (`PublishEvalTeardownHandler`), crash-window eval cases, tests.
- **Done when:** crashed teardown re-runs idempotently; orphaned eval create
  is visible as a PENDING/ID_RECORDED op; TTL task enqueued at publish;
  suite green.
- **Depends on:** Group B

## Task 16: Approval flow — intent-first puid + durable trigger — [ ]
- **Goal:** `_create_new_approval` writes an `approval_create` intent before
  `start_approval` (puid into `result` after); the AGREED callback write also
  enqueues durable `service_bot.publish.approval_trigger`; handler calls the
  status-CAS-guarded `process()`/offline.
- **Files:** `publish_approval_service.py`, `publish_flow/tasks.py`
  (`PublishApprovalTriggerHandler`), approval tests.
- **Done when:** AGREED-then-crash case converges via the task; duplicate
  callback delivery is a no-op; orphaned approval instance visible as
  PENDING op; suite green.
- **Depends on:** Group B

# Group D — Quality + cleanup

## Task 17: `baas_service.py` consolidation — [ ]
- **Goal:** Extract `_post_workflow_mutation(path, payload)` shared by
  destroy/stop/restart/scale/upgrade (same POST + code-check + extract
  publish_id boilerplate); decompose `_build_create_bot_payload` (~194
  lines) into focused builders.
- **Files:** `baas_service.py`, its unit tests (payload assertions pinned
  before refactor).
- **Done when:** behavior-pinned payload tests unchanged and green; each
  extracted method ≤80 lines.
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
