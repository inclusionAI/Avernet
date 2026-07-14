# Plan: Publish Flow Service Refactor

## Approach
Decompose the 3185-line `PublishFlowService` into a thin public **facade** plus a
cohesive `publish_flow/` package of single-purpose collaborators (build, release,
progress-sync, restart, scale, rollback, eval, ext/state, provider-behavior,
task handlers). Structural behavior is preserved by moving logic largely verbatim
and characterizing under-tested entry points first. Three structural wins ride
along: a `ProviderBehavior` seam+router absorbing all six scattered teclaw
branches, one stage-parameterized release path replacing four near-duplicate
methods, and clear command/query separation.

On top of the restructure, the **backend-driven pipeline becomes durable**: every
backend stage advance — build, release (verify/online), restart, and the
BaaS-wait progress — is modeled as a persisted, idempotent `TaskQueueService`
task instead of a fire-and-forget `asyncio.create_task`. `/process` becomes a
uniform async-submit that enqueues the work and returns "in progress"; the
handlers do the durable work and chain forward (build → release → progress-poll),
so a pod reboot mid-build resumes instead of stranding the record. The one
genuinely long step (build) is kept safe on multi-pod by a new **lease-renewal**
primitive added to the shared task-queue infra. The manual go-live gate
(VALIDATING → ONLINE) stays user-triggered — a task never crosses it.

The import path `agentclaw...services.publish_flow_service.PublishFlowService`
stays stable: `publish_flow_service.py` remains the file that defines the facade
class; the extracted pieces live in a sibling `publish_flow/` package. This keeps
DI and caller imports unchanged.

## Stage Transition Ownership
Two ownership classes: a **user** initiates work at exactly two points; the
**task queue** performs and chains everything in between durably. The queue never
initiates a stage on its own and never crosses the go-live gate.

**The forward pipeline:**
```
DRAFT ──user /process──► BUILDING ┐
BUILDING → BUILT → VALIDATE_PUB   │ publish.verify_flow          (TASK)
VALIDATE_PUB ─────────────────────┘ → then enqueue progress_poll
VALIDATE_PUB → VALIDATING            publish.progress_poll        (TASK)
VALIDATING ──user /process (GATE)──► ONLINE_PUB
                                     publish.online_release       (TASK)
                                     → then enqueue progress_poll
ONLINE_PUB → SUCCESS                 publish.progress_poll        (TASK)
(any task raises) → FAILED                                        (TASK)
```

**User-initiated — the ONLY two advancing `/process` inputs** (endpoint enqueues,
returns "in progress"):
- `DRAFT` → enqueue `publish.verify_flow` (`POST /process`, was `:236`
  async-spawn). This one task carries `BUILDING → BUILT → VALIDATE_PUB`; a second
  user call is **not** needed.
- `VALIDATING` → enqueue `publish.online_release` — the **go-live gate**
  (`POST /process`, was `:251` inline-await — now async-submit). Nothing
  auto-enqueues this; only a user crosses the gate.
- `/process` on any **other** state (BUILDING/BUILT/VALIDATE_PUB/ONLINE_PUB/
  SUCCESS/FAILED/UPGRADED/RELEASED) is **describe-only** — returns current status,
  no enqueue, no mutation.
- Other user actions: `retry`, rollback (`UPGRADED → ONLINE_PUB`), scale, restart,
  and `/offline` (`SUCCESS → RELEASED`). retry/restart now enqueue durable tasks
  instead of `asyncio.create_task`.

**Task-queue-driven (durable, idempotent, chained by the handlers):**
- `publish.verify_flow`: `BUILDING → BUILT → VALIDATE_PUB` — the build
  (`produce_artifact` `:468`, long-running → lease-renewed) followed by the verify
  release (mirrors today's single `_execute_full_verify_flow_async` `:300`). On
  success → enqueue `publish.progress_poll`. (BUILT is an internal checkpoint of
  this one task, not a hand-off point where a record can strand.)
- `publish.online_release`: `VALIDATING → ONLINE_PUB` (the online release/upgrade
  submit). On success → enqueue `publish.progress_poll`.
- `publish.restart[stage]`: the BaaS restart/upgrade submit (was
  `_restart_bot_async` `:1572`). On success → enqueue `publish.progress_poll`.
- `publish.progress_poll`: the BaaS-wait completion — `VALIDATE_PUB → VALIDATING`
  / `ONLINE_PUB → SUCCESS` (and restart variants). Self-reschedules until the
  BaaS publish is terminal. On online success it also flips the **previous**
  record `SUCCESS → UPGRADED` (`_upgrade_last_publish` `:1927`). Enqueued **only**
  on entry to `VALIDATE_PUB`/`ONLINE_PUB`.
- Any unrecoverable error inside a task writes `→ FAILED` (task-driven).

**Post-SUCCESS lifecycle states** (terminal; outside the forward pipeline —
`terminal_statuses` at `bot_publish_service.py:975`):
- `UPGRADED` — a record that was live online but was superseded when a newer
  version reached `SUCCESS`. Written **task-side** as the `progress_poll`
  side-effect above. Rollback target (`publish_rollback_mixin.py:80`).
- `RELEASED` — a live `SUCCESS` bot deliberately taken offline via the `/offline`
  endpoint (`bot_publish_service.py:996,1033`). **User-driven**; precondition for
  `destroy_publish_history` (`:3152`).

**Gate invariant:** `VALIDATING → ONLINE_PUB` is enqueued *only* by a user
`/process`. The `progress_poll` handler calls `sync_*`, a structural no-op for
`VALIDATING`/`DRAFT` (`_determine_sync_stage` returns `None` `:1907`), so it
cannot cross the gate or restart a build. The two intended behavior changes are
(1) `VALIDATING` `/process` now returns "in progress" like `DRAFT` already does,
and (2) the whole chain is crash-safe.

**Idempotency (re-run safety after crash / lease-expiry re-claim):** each handler
is a guarded checkpoint keyed off the record's own persisted state:
- `verify_flow`: the build sub-step re-runs only if not already `BUILT`+ with an
  artifact in `ext` (else skips to release); `produce_artifact` is overwrite-safe
  (same `migration_path` / content-addressed OSS). The release sub-step guards
  like `online_release` below.
- `online_release`/`restart`: **before** creating a BaaS bot, check
  `ext.publish.{stage}` / `ext.binding.{stage}` already recorded for this record
  → skip create, resume by enqueuing the poll. This `ext` guard is the **primary**
  idempotency mechanism (see Task 0 finding).
- `progress_poll`: already idempotent — re-reads the record, no-ops on terminal
  state (as `/sync` does today).
- Chain continuation is self-healing: a handler enqueues its successor **before**
  returning `Complete`; a failed enqueue raises → `Retry` re-runs the (idempotent)
  handler, so the chain never strands with no pending task.

**Task 0 finding — BaaS idempotency, per-path.** BaaS
(`src/baas/.../publish_manage/_publish_service.py`) does not persist or dedupe on
`request_id` (correlation/logging only, `:281,292`; no repo column, no unique
index). `create_publish` enforces **one active publish per BaaS `bot.id`**
(`get_active_by_bot_id`, `:307`), where "active" = publish `status NOT IN
(SUCCESS, FAILED, REJECTED, REVOKED)` (`_orm_repository.py:196`). A same-type
non-stale duplicate **returns the existing publish** (`:405-416`). But the check
is keyed on BaaS's **internal integer `bot.id`**, and the CREATE flow
(`_bot_management_service.py:249`) mints a **fresh bot** each call — inner
`create_bot` (`_bot_service.py:55`) generates a random `bot_uuid` and does an
**unconditional `insert_bot`**, ignoring our `tc_bot_id`/`user_id` for identity.
Therefore:
- **Upgrade / restart / scale** target an existing `bot_uuid` → stable `bot.id` →
  the active-publish check makes a re-issued call **idempotent** (returns the
  in-flight publish). The "crash between BaaS-call and ext-persist" window is
  covered here. ✅
- **First create** (verify/online first-release) has **no stable id** → a re-run
  after create-succeeds-but-before-ext-persist creates a **second orphan bot**.
  Not covered by BaaS; our `ext` guard alone cannot cover this sub-window because
  the id isn't persisted yet. ❌ (Pre-existing today; durability makes the retry
  automatic. Mitigation decided below.)
- `request_id` lacking `version` (`bot_build_service.py:151`) is **not** a
  create-collision bug — `request_id` is inert for dedupe. Out-of-scope cleanup.

**First-create idempotency — mitigation (DECIDED: Option C + persist-before-approve
invariant).** The first-create handler checks `ext.publish.{stage}` before
creating; if absent it creates (accepting a **rare orphan** if the pod dies in the
create→persist window — a leaked BaaS bot+device *record*, no running container,
its publish auto-FAILED by BaaS orphan/stale cleanup) and logs enough to find it.
The **hard invariant** that bounds the risk to only that pre-persist window:
```
create bot (BaaS) → persist ext.publish/binding.{stage} → approve → enqueue poll
```
Because `ext.publish/binding` is persisted **strictly before** `approve`, any
crash from approval onward re-runs to a guard hit (`ext.publish.{stage}` present →
skip create → idempotent re-approve + poll) — so an **approved/live bot is never
re-created or orphaned**. This ordering (today's `record_release_result` order,
`:2843` persist before `:2856` approve) is a tested requirement, not incidental.
Rejected: (A) at-most-once marker + fail-on-ambiguity — safer against the leak but
reintroduces a manual retry for that window; (B) reconcile-and-adopt — needs a
BaaS lookup by `tc_bot_id`/owner that does not exist (only `bot.id`/`bot_uuid`/
paged `list_bots`), i.e. a cross-repo BaaS change. A sweep for orphaned records is
a possible follow-up, out of scope here.

**Crash/restart recovery (why a mid-task reboot does not strand a record).**
Durability lives in the `ac_task_queue` **row**, which is committed at enqueue and
is independent of the publish record's status. If a pod dies mid-`verify_flow`
(e.g. after the build wrote `BUILT` but before verify-release), the task row is
still `RUNNING`; its lease stops being renewed, expires (≤ `lease_seconds`), and
is re-claimed by any worker — `claim_batch` treats a `RUNNING` row with
`lease_expires_at <= now()` as eligible (`protocol.py:66`). The re-run resumes
via the idempotency guard (skips the finished build, does verify-release). So a
record parked at `BUILT`/`BUILDING` is a recoverable checkpoint, not a dead end —
the exact failure that today's in-memory `asyncio.create_task` (`:238`) cannot
survive. Bounded recovery delay ≈ one lease interval. This holds whether
`verify_flow` is one task or two chained tasks; one task is chosen for
behavior-parity with today's single async method.

## Affected Components
- `core/service_bot/services/publish_flow_service.py` — **becomes the thin
  facade** (public API surface, delegates to runners). Target < ~400 lines.
- `core/service_bot/services/publish_flow/` (new package) — the decomposed
  concerns (see Key Files).
- `core/service_bot/services/deploy/provider_resolver.py` — unchanged; its
  `resolve_device_provider` tokens (`teclaw`/`baas`) key the new behavior router.
- `di/modules/service_bot_module.py` — assemble the `ProviderBehaviorRouter`,
  inject the new runners into the facade, and bind the publish task lifecycle
  (registers build/release/restart/progress-poll handlers).
- `core/task_queue/repository/protocol.py` — **new `renew_lease` method** (shared
  infra).
- `plugins/task_queue_repository.py` — implement `renew_lease` (SQLite +
  OceanBase; CAS-guarded on holder + RUNNING).
- `core/task_queue/services/worker.py` — **heartbeat**: renew the lease
  periodically while a handler runs (so long handlers keep their claim).
- `core/task_queue/services/task_queue_service.py` — unchanged (enqueue facade
  reused by the publish flow).
- `api/publish_flow_service.py` — `PublishFlowServiceProtocol`: update the two
  renamed public methods (`general_publish`/`general_teardown`) if renamed.
- `adapters/http/service_bot/router_publish.py` — update call sites for any
  renamed public method (only the eval methods are candidates; see Renames).
- `core/quality/services/task_processor.py` — caller of the eval methods +
  `get_baas_publish_progress`.
- `core/service_bot/services/bot_publish_service.py` — caller of
  `destroy_publish_history`.
- `core/service_bot/services/publish_approval_service.py` — caller of `process`.
- `core/service_bot/services/publish_rollback_mixin.py` — caller of
  `execute_rollback`.
- `configs/application.yaml`, `configs/application-community.yaml`,
  `configs/application-test.yaml` — task-queue worker enablement (see Rollout).
- `tests/community/config/golden/{community,singlebox}.json` — regenerated for
  the worker-enabled base.
- `tests/community/architecture/test_no_oversized_modules.py` — drop the
  `publish_flow_service.py` allowlist entry.

## Data Model Changes
**No schema change.** No new tables, columns, or migrations:
- `BotPublishRecord.ext` JSON keys unchanged: `binding.{stage}`, `publish.{stage}`,
  `restart.{stage}`, `scale.publish_id`, `config_artifact`, `migration_path`,
  `build_target_path`, `engine_overrides_by_stage.{stage}`, `source_status`,
  `error_message`, `retry`, `rollback_restored_from`, `biz_id`.
- `device_binding.device_props` keys unchanged (incl. the misspelled `bolt_id`
  at `publish_flow_service.py:2220` — left as-is: persisted-key change, out of
  scope).
- `ac_task_queue`: the new publish task types are **new `task_type` string
  values** only. `renew_lease` writes the existing `lease_expires_at` column — no
  new column. The prod table is provisioned manually per the task_queue README.

## API / Interface Changes
- **HTTP: one intended change (`/process`), rest unchanged.** `/process` on
  DRAFT/BUILT/VALIDATING now enqueues a task and returns an "in progress"
  `PublishFlowResult` (status = current, message "已提交…", `bot_uuid`/
  `baas_publish_id`/`device_binding_id` absent) instead of blocking and returning
  the advanced state with fresh ids. DRAFT is already async today; BUILT/
  VALIDATING now match. All other routes unchanged.
- **New task types** (registered in `HandlerRegistry`):
  - `service_bot.publish.verify_flow` — payload `{publish_id, operator}`. Runs
    build + verify release (`BUILDING → BUILT → VALIDATE_PUB`); enqueued by a
    user `/process` on `DRAFT`.
  - `service_bot.publish.online_release` — payload `{publish_id, operator}`. Runs
    the online release (`VALIDATING → ONLINE_PUB`); enqueued by a user `/process`
    on `VALIDATING` (the gate).
  - `service_bot.publish.restart` — payload `{publish_id, operator, stage}`.
  - `service_bot.publish.progress_poll` — payload `{publish_id}`.
  Each handler is idempotent and, on success, enqueues the next task in the chain.
  Deadlines: verify_flow/online_release/restart ~3600s; progress_poll ~86400s
  (matches devices poll deadline). Poll cadence via `Reschedule(delay~5-10s)`.
- **New shared-infra method — `TaskQueueRepositoryProtocol.renew_lease`:**
  ```python
  def renew_lease(self, *, task_id: int, worker_id: str, lease_seconds: int) -> bool:
      """Extend a held task's lease to now()+lease_seconds. CAS-guarded on
      claimed_by == worker_id AND status == RUNNING. False if no longer held."""
  ```
  The worker starts a heartbeat (interval ~ `lease_seconds/3`) around each
  handler run; on a `False` return it stops renewing (lease already lost). Timing
  stays DB-side (durations in, DB computes `lease_expires_at`), consistent with
  the component's clock-skew-free contract.
- **New internal seam — `ProviderBehavior`** (`publish_flow/provider_behavior.py`):
  ```python
  class ProviderBehavior(Protocol):
      async def stage_build_files(self, *, artifact, bot, bot_id, owner_id, publish_id) -> None: ...
      def refresh_after_upgrade(self, *, bot_uuid: str, bot: dict) -> None: ...
      @property
      def supports_scale(self) -> bool: ...
      @property
      def destroys_verify_bot_on_online(self) -> bool: ...
  ```
  Impls: `TeclawProviderBehavior` (holds `resolver`, `device_fs_dispatcher`,
  `teclaw_file_promotion`, `build_service`; stages files, refreshes MCP rule,
  `supports_scale=False`, `destroys_verify_bot_on_online=False`) and
  `DefaultProviderBehavior` (all no-ops / `True`). Selected by a
  `ProviderBehaviorRouter.resolve(device_provider)` mirroring
  `DeployArtifactProducerRouter` (`deploy/producer.py:50`).
- **New internal seam — release stage table.** One
  `ReleaseStageRunner.first_release(...)` and `.upgrade_release(...)`
  parameterized by a small `StageSpec(stage, source_status, target_status,
  request_label)` replacing the four `_execute_*` methods.
- **New task types** — build/release/restart/progress_poll (see API section);
  handlers thin over the runners.
- **New shared-infra method** — `renew_lease` + worker heartbeat (see API).
- **Public method renames** — see Renames; callers updated in the same change.

## Key Files & Functions
New package `core/service_bot/services/publish_flow/`:
- `__init__.py` — re-exports the runner classes for DI.
- `ext_state.py` (new) — `PublishExtState`: `get_latest_ext`,
  `merge_and_update_ext`, `update_status`, `owner_id`, `clear_retry_flag`,
  `stamp_stage_on_stored_artifact` (was `_restamp_ext_artifact`),
  `stage_overrides`/`artifact_for_stage`/`store_stage_overrides`
  (from `publish_flow_service.py:133-196, 2871-2939`). Shared by all runners.
- `provider_behavior.py` (new) — `ProviderBehavior` protocol, `TeclawProviderBehavior`
  (absorbs `_stage_teclaw_files` `:373`, MCP-refresh branches `:896-906`/`:1269-1279`),
  `DefaultProviderBehavior`, `ProviderBehaviorRouter`.
- `build_stage.py` (new) — `BuildStageRunner.run(...)` from `_execute_build_phase`
  (`:419`), delegating file-staging to `ProviderBehavior.stage_build_files`.
  Idempotent-guarded (skip the build if already BUILT+ with an artifact in `ext`).
  Invoked by the `verify_flow` handler (which then runs the verify release);
  not enqueued on its own.
- `release_stage.py` (new) — `ReleaseStageRunner` unifying
  `_execute_verify_first_release` (`:718`), `_execute_verify_upgrade` (`:793`),
  `_execute_first_release` (`:1070`), `_execute_upgrade_release` (`:1136`) into
  `first_release`/`upgrade_release` + the verify/online binding resolvers
  (`_resolve_verify_binding` `:643`, `_should_upgrade_online` was
  `_should_execute_upgrade_release` `:1023`) + `record_release_result` (`:2774`).
  Calls `enqueue_progress_poll` after obtaining a `baas_publish_id`.
- `progress_sync.py` (new) — `ProgressSynchronizer`: `sync_publish_progress`
  (`:2385`), `sync_restart_progress` (`:2576`), `sync_scale_progress` (`:2514`),
  `_handle_sync_success` (`:2238`), `_handle_sync_failure` (`:2342`),
  `_update_binding_on_success` (`:2188`), `_mark_previous_publish_superseded`
  (was `_upgrade_last_publish` `:1913`), `_stage_for_sync`/`_stage_for_restart`
  (`:1881`/`:1898`), `get_baas_publish_progress` (`:2703`), `_approve_baas_publish`
  (`:2727`). Destroy-verify-on-online now gated by
  `ProviderBehavior.destroys_verify_bot_on_online`.
- `restart_ops.py` (new) — `RestartRunner.restart_bot` (`:1407`) +
  `_restart_bot_async` (`:1572`) + `_refresh_publish_handle` (`:178`). Enqueues a
  progress poll after submit.
- `scale_ops.py` (new) — `ScaleRunner.scale_bot` (`:1708`) +
  `_resolve_scale_target_count`/`_read_device_count_from_bot_ext`/
  `_get_default_scale_target_count`/`_normalize_device_count` (`:1801-1879`).
  `supports_scale` gate via `ProviderBehavior`.
- `rollback_ops.py` (new) — `RollbackRunner.execute_rollback` (`:1969`) +
  `_destroy_bot_by_stage` (`:2094`) + `destroy_publish_history` (`:3113`).
- `eval_publish.py` (new) — `EvalPublisher.publish_eval_environment` (was
  `general_publish` `:2941`) + `teardown_eval_environment` (was `general_teardown`
  `:3029`).
- `tasks.py` (new) — the durable task layer: task-type constants, payload
  builders + validators (mirroring `baas_publish_task_handlers.py` `_require_*`),
  enqueue helpers (`enqueue_verify_flow`/`enqueue_online_release`/
  `enqueue_restart`/`enqueue_progress_poll`), the four handlers
  (`PublishVerifyFlowHandler`, `PublishOnlineReleaseHandler`,
  `PublishRestartHandler`, `PublishProgressPollHandler`) thin over the runners,
  and `PublishTaskLifecycle(LifecycleBase)` that registers
  all four in the shared `HandlerRegistry` — mirroring
  `devices/services/baas_publish_task_handlers.py:531` +
  `di/modules/devices_module.py:229`. Each handler re-loads the record and guards
  on its persisted state (idempotency) before acting.

Shared task-queue infra (used by publish + devices):
- `core/task_queue/repository/protocol.py` — add `renew_lease` (see API).
- `plugins/task_queue_repository.py` — implement `renew_lease` (holder+RUNNING
  CAS `UPDATE ... SET lease_expires_at = now()+:lease WHERE id=:id AND
  claimed_by=:wid AND status='RUNNING'`), on both SQLite and OceanBase.
- `core/task_queue/services/worker.py` — wrap `_run_one`'s `handler.handle` in a
  heartbeat task that calls `renew_lease` every ~`lease_seconds/3`; cancel on
  return; stop renewing on a `False` (lease already lost). Add
  `heartbeat_interval` derivation (or reuse `lease_seconds`).

Facade `publish_flow_service.py` (rewritten thin):
- `PublishFlowService.__init__` keeps the same DI-injected collaborators, plus
  `TaskQueueService` (enqueue) and the runners.
- `process` (`:198`) → dispatch table: DRAFT → `enqueue_verify_flow`; VALIDATING
  → `enqueue_online_release` (the gate); each returns an "in progress"
  `PublishFlowResult`. BUILDING/BUILT/VALIDATE_PUB/ONLINE_PUB/SUCCESS/FAILED/
  UPGRADED/RELEASED → side-effect-free `_describe_status(record)`; unknown →
  raise `PublishStatusInvalidError`. (BUILT is no longer a user advance point —
  the `verify_flow` task owns `BUILT → VALIDATE_PUB`.)
- `retry` (`:1296`) → maps `source_status` directly to an enqueue (it must **not**
  route through `process()`, since `process` on BUILT is now describe-only):
  `DRAFT`/`BUILDING`/`BUILT` → `enqueue_verify_flow` (the build sub-step skips
  when already BUILT); `VALIDATING` → `enqueue_online_release`;
  `VALIDATE_PUB`/`ONLINE_PUB`/`SUCCESS` → `enqueue_restart`. This preserves
  today's retry semantics (`:1382`) under the new task model.
- `restart_bot` → `enqueue_restart`; the rest (`execute_rollback`, `scale_bot`,
  three `sync_*`, `get_baas_publish_progress`, `get_publish_bot_status`,
  `destroy_publish_history`, eval methods) → thin delegations. `execute_rollback`
  stays inline-await (user-facing, short) unless we opt to task-ify it too
  (decide in `tasks`).

DI (`di/modules/service_bot_module.py`):
- New `@provider` `provider_behavior_router` (assembles teclaw/baas/arca → behavior).
- New `@provider` `publish_task_lifecycle` (singleton `LifecycleBase`, injects
  `HandlerRegistry`, `TaskQueueService`, the runners) so discovery runs its
  `bootstrap()` before `TaskWorker.startup()` (verified path:
  `kernel/lifecycle.py:172`, `adapters/http/app.py:158`).

## Dependencies
No new third-party packages. New internal dependencies: the publish flow now
depends on `TaskQueueService` (enqueue) and `HandlerRegistry` (register), both
already DI-bound in the base-installed `TaskQueueModule`; and on the new
`renew_lease` repo method.

## Risks & Mitigations
- **Risk:** Enabling `task_queue_worker` at base turns the worker on for *every*
  registered handler globally — including the devices BaaS create/restart pollers
  (`baas_publish_task_handlers.py`), which are currently dormant everywhere.
  **Mitigation:** Those handlers are built to run and are idempotent (single-claim
  CAS, terminal-state guards, deadline-bounded). Call this out for explicit
  sign-off; if undesired, scope enablement to prod/dev overlays instead of base.
  Keep the worker **off in `application-test.yaml`** so the suite doesn't spawn a
  background loop.
- **Risk:** For the two poll-driven states (`VALIDATE_PUB`, `ONLINE_PUB` — see
  Stage Transition Ownership), both the new handler *and* a frontend `/sync`
  could drive the same record. This is a narrow, benign overlap, not a
  general "every stage double-advances" problem. **Mitigation:** both go through
  the same `sync_publish_progress` path, which uses
  `update_publish_status_with_ext` optimistic-locking on `source_status`
  (`:2276`) — one wins the transition, the other sees the new state and no-ops.
  The task queue's single-claimer guarantees one poll runs at a time per task.
  No other transition class is affected (`BUILDING` self-completes in-process;
  the manual gates are user-only).
- **Risk:** The poll handler mis-advances a manual or in-process transition
  (the go-live gate `VALIDATING→ONLINE`, or a `DRAFT` build). **Mitigation:**
  structural, not conventional — the handler only calls `sync_*`, and
  `_determine_sync_stage` returns `None` for `VALIDATING`/`DRAFT`, so those calls
  no-op; and a poll is enqueued *only* on entry to `VALIDATE_PUB`/`ONLINE_PUB`,
  never for `VALIDATING`/`DRAFT`. The gate can only be crossed by a human
  `/process`, exactly as today.
- **Risk (correctness, highest):** A re-run of a `build`/`release`/`restart` task
  after a crash or lease-expiry re-claim **double-creates a BaaS bot** or corrupts
  the artifact. **Mitigation:** each handler guards on the record's persisted
  state before mutating (skip create when `ext.publish.{stage}`/`binding.{stage}`
  already recorded → resume by polling). **Task 0 (done):** BaaS does *not* dedupe
  on `request_id` — it dedupes on active-publish-per-`(bot_id, publish_type)`,
  returning the existing publish for a same-type duplicate while one is active.
  So the `ext` guard is primary; BaaS's active-publish return is a secondary net
  (covers the create-then-crash-before-persist window). Idempotency re-run tests
  are mandatory (Test Strategy).
- **Risk:** `renew_lease` (shared infra) has a bug and a handler loses its lease
  mid-run → concurrent re-claim + double-run despite the heartbeat.
  **Mitigation:** CAS-guarded exactly like the other transitions; unit-tested on
  both SQLite and OceanBase paths (renew succeeds while held, returns `False`
  after takeover, extends `lease_expires_at`); heartbeat interval ≤ lease/3 gives
  two renew attempts of headroom; idempotency (above) is the backstop even if a
  double-run slips through.
- **Risk:** `/process` async-submit change breaks a caller that reads the
  synchronous ids or the advanced state from the response. **Mitigation:**
  confirmed acceptable with the product owner (spec Open Questions); the two
  in-repo non-router callers (`publish_approval_service.py:551`,
  `task_processor.py` uses eval methods, not `process`) do not read those fields —
  verify during implementation. Router `test_router_publish_coverage.py` updated
  to the new response shape.
- **Risk:** Behavior drift while moving 3000 lines. **Mitigation:**
  characterization tests land first (Test Strategy); logic moves verbatim; the
  existing ~55 unit tests + endpoint tests must stay green (with import-path,
  rename, and the deliberate `/process`-response edits only).
- **Risk:** Modifying `worker.py`/`protocol.py` regresses the devices pollers that
  share the infra. **Mitigation:** `renew_lease` is purely additive (new method +
  a heartbeat that only *extends* an existing lease); existing task_queue tests
  must stay green; add worker heartbeat tests.
- **Risk:** Golden config test breaks. **Mitigation:** regenerate
  `community.json`/`singlebox.json` in the same change; document the expected diff
  (`task_queue_worker.enabled: true`).
- **Risk:** `di/modules/service_bot_module.py` construction cycle
  (`BotPublishService`↔`PublishFlowService`, broken by a lazy provider at
  `:265`). **Mitigation:** keep the facade's constructor identity/binding
  unchanged; runners receive the facade's already-resolved collaborators, not a
  re-entrant `injector.get`.

## Alternatives Considered
- **Hang provider behavior on the existing producer seam** instead of a new
  `ProviderBehavior`. Rejected: the producer's job is *building an artifact*
  (`produce_artifact(bot, version)`), a sync call with no deploy-time context;
  the varying behaviors (MCP refresh, scale-support, destroy-verify) are
  *deploy-time* and need different deps. Overloading the producer would blur two
  concerns.
- **Make the task queue the sole driver and delete `/sync`.** Rejected:
  regresses any environment where the worker is off, and drops a useful manual/
  debug lever. Keeping `/sync` as an idempotent redundant driver costs nothing.
- **Rename persisted `ext`/`device_props` keys** (e.g. `bolt_id`→`bot_id`,
  `source_status`→`failed_from_status`). Rejected: requires data migration /
  back-compat reads; out of scope per spec.
- **One giant reshaped method for `process` with explicit advance/query split at
  the endpoint.** Rejected in favor of keeping `process` as the public method
  (its endpoint + Protocol + approval-service caller) with an internal
  `_describe_status`, preserving the contract with minimal caller churn.

## Renames (proposed — confirm before `tasks`)
Public (callers updated in-repo):
- `general_publish` → `publish_eval_environment` (callers: `task_processor.py:144`,
  Protocol `api/publish_flow_service.py`).
- `general_teardown` → `teardown_eval_environment` (`task_processor.py:295`).

Internal (no external callers):
- `_upgrade_last_publish` → `_mark_previous_publish_superseded`.
- `_should_execute_upgrade_release` → `_should_upgrade_online`.
- `_restamp_ext_artifact` → `_stamp_stage_on_stored_artifact`.
- `_determine_sync_stage`/`_determine_restart_stage` → `_stage_for_sync`/`_stage_for_restart`.
- `_stage_teclaw_files` → moves into `TeclawProviderBehavior.stage_build_files`.

Left as-is (clear enough / risk not worth it): `process`, `retry`, `restart_bot`,
`scale_bot`, `execute_rollback`, `sync_publish_progress`, `sync_restart_progress`,
`sync_scale_progress`, `get_baas_publish_progress`, `get_publish_bot_status`,
`destroy_publish_history`. **Flagged, not renamed:** persisted `bolt_id`
device-prop typo (`:2220`) and `ext.source_status` — both persisted, out of scope.

## Rollout
- **Config:** add `task_queue_worker: {enabled: true}` under `user_config` in
  `configs/application.yaml`; delete the `task_queue_worker` block from
  `configs/application-community.yaml`; add explicit
  `task_queue_worker: {enabled: false}` to `configs/application-test.yaml`.
  Regenerate `tests/community/config/golden/{community,singlebox}.json`
  (→ `enabled: true` in both the raw-user_config and resolved sections).
- **Single PR, sequenced internally** (per decision) so each increment is a green
  checkpoint: (1) characterization tests; (2) module split + provider seam +
  release dedup + renames (behavior-preserving; `/process` still inline);
  (3) `renew_lease` shared-infra + worker heartbeat; (4) durable task handlers +
  `/process` async-submit + config/worker enable. The commits map to these so a
  bisect can isolate the behavior-changing step (4).
- **Safety net for the behavior changes:** `/sync` and `/restart_status` remain
  as idempotent manual drivers, so even if the worker is disabled the flow is
  still completable; the manual go-live gate is unchanged.
- **Backwards-compat:** existing in-flight publish records advance unchanged
  (handlers read the same `ext`; `/sync` still works). No migration. A record
  mid-flight at cutover with no enqueued task is picked up by the next `/sync` or
  by a `/process` re-issue, exactly as today.

## Test Strategy
**Characterize first** (add to `tests/community/core/service_bot/services/test_publish_flow_service.py`
before moving code) — the thin-coverage entry points:
- `process()` dispatch per status (DRAFT/BUILT/VALIDATING advance; BUILDING/
  VALIDATE_PUB/ONLINE_PUB/SUCCESS/FAILED describe-only; unknown → raise).
- `sync_publish_progress()` wrapper: missing baas-publish-id guard, progress-fetch
  error, SUCCESS/FAILED/other dispatch, retry-flag redirect to restart-sync.
- `sync_restart_progress()` — currently zero coverage: SUCCESS/FAILED/in-progress/
  missing-handle, and the VALIDATING/SUCCESS "stable state" no-update branch.
- `restart_bot()` submit path: stage resolution, `ext.restart` write, async
  scheduling, provider branch.
- `retry()` across `source_status`: happy path (restart succeeds), ONLINE_PUB,
  BUILT/DRAFT (→ process), non-FAILED rejection.
- online first-vs-upgrade positive selection (`_should_upgrade_online` True path).

**Task 0 (before design):** confirm BaaS `create_bot`/`create_teclaw_bot`
idempotency on the deterministic `request_id` — this decides whether the release
handler needs an extra "existing bot lookup" guard. Drives the idempotency design.

**Preserve:** all existing unit tests in that file, the teclaw endpoint cases in
`tests/community/endpoints/test_publish_per_stage_channels.py`, and the router
coverage in `tests/community/adapters/http/service_bot/test_router_publish_coverage.py`
must pass with import-path/rename edits — plus the deliberate `/process` response
edits (BUILT/VALIDATING now return "in progress"; update those specific
assertions).

**New tests for new seams:**
- `ProviderBehaviorRouter` resolves teclaw→Teclaw, baas/arca→Default; each
  behavior's four members do the right thing (unit).
- `ReleaseStageRunner` parameterization parity: verify/online first & upgrade
  produce the same transitions/ext writes as the four old methods (unit).
- **Durability / idempotency (the must-haves):**
  - Each task handler (`verify_flow`/`online_release`/`restart`/`progress_poll`)
    run twice on the same record produces the same end state and side effects
    exactly once (no second BaaS create, no duplicate binding) — the crash-resume
    simulation (unit, mocked collaborators asserting call-once on create). For
    `verify_flow` specifically: a re-run after the build sub-step succeeded skips
    the rebuild and proceeds to release.
  - `progress_poll`: PENDING→Reschedule, SUCCESS/FAILED→Complete with the correct
    transition, terminal/mismatched record→Complete (no-op), retry-flag record
    routes through restart-sync.
  - Chaining: `verify_flow` success enqueues `progress_poll`; `online_release`
    success enqueues `progress_poll`; a failed successor-enqueue raises → Retry
    (assert enqueue calls + the self-healing re-run).
- **Lease renewal (shared infra):** `renew_lease` returns `True`+extends
  `lease_expires_at` while held, `False` after another worker takes over
  (SQLite; OceanBase path exercised where the impl is shared). Worker heartbeat:
  a handler that runs longer than `lease_seconds` keeps its claim and its outcome
  write still succeeds (unit via `run_once` with a slow fake handler + a fake
  clock/lease). Existing task_queue tests stay green (additive change).
- **`/process` async-submit:** DRAFT enqueues `verify_flow`, VALIDATING enqueues
  `online_release`, each returning an "in progress" result with no synchronous
  ids; the state flips only when the handler runs. BUILT (and other non-DRAFT/
  non-VALIDATING states) `/process` is describe-only — asserts no enqueue, no
  mutation (unit + one endpoint case draining background work via `async_client`).
- **Config:** golden regeneration passes; assert `TaskQueueWorkerConfig.enabled
  is False` under the test profile and `True` under community/singlebox.
- **Integration (endpoint harness):** a full DRAFT→…→SUCCESS run driven purely by
  the worker (no manual `/sync`) for both teclaw and non-teclaw, using the
  in-memory SQLite + BaaS HTTP stub, draining the worker via `run_once`.

**Architecture guard:** `test_no_oversized_modules.py` — every new module < 1000
lines and the `publish_flow_service.py` allowlist entry removed.
