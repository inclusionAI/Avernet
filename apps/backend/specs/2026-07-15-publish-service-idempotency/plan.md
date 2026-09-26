# Plan: Publish service idempotency — operation ledger + resumable steps

Spec: `spec.md` in this directory. Issue: #197.

## Approach

One new persistence primitive (the **operation ledger** table + repository),
one new orchestration primitive (the **step runner** that executes every BaaS
mutation as `open (intent) → acquire (workflow) → finalize`, with the
existing progress poll driving workflows to terminal), and then a
per-operation rewiring pass that
converts each publish operation onto those primitives. The durable task queue
(existing) absorbs the remaining fire-and-forget paths. One read-only BaaS
endpoint is added (publishes-by-bot) because the adopt-by-query mechanism
requires it and nothing routes it today.

Delivery is four sequential groups, each independently green:

- **A. Foundation** — ledger table/repository/DI, step runner, request-id
  scheme, BaaS read-only endpoint + client method, ledger backfill-from-ext
  helper. No behavior change yet (nothing calls the runner).
- **B. Release legs** — first/upgrade release (verify + online) onto the
  runner; all-auto approval (every mutation requests server-side
  auto-approval; every client approve call is deleted; teclaw's post-upgrade
  MCP refresh moves to the poll's success handler); `retry()` decision from
  ledger state.
- **C. Remaining operations** — restart (durable task), offline (CAS +
  durable destroy task), rollback (transactional flips + runner), scale,
  eval (runner + TTL teardown task), approval (intent-first `puid`,
  durable AGREED-trigger task). Progress-sync readers switch to the ledger.
- **D. Quality** — `baas_service.py` boilerplate consolidation, method
  decomposition to ≤80 lines, dead ext-marker writes removed after one
  transition release.

## The operation ledger

New table `ac_publish_operation` (backend DB, same store as `ac_bot_publish`
and `ac_task_queue`):

| column | type | notes |
|---|---|---|
| `id` | bigint PK autoincrement | |
| `gmt_create` / `gmt_modified` | timestamp | `gmt_create` is the in-doubt fence |
| `env` | varchar(32) | same env discipline as task queue |
| `publish_id` | bigint | agentclaw publish record id (0/null for eval-teardown by bot_uuid) |
| `operation_kind` | varchar(64) | enum below |
| `stage` | varchar(16) | verify / online / eval / '' |
| `attempt` | int | bumped when a prior attempt is ABANDONED |
| `state` | varchar(32) | `PENDING → ID_RECORDED → COMPLETED`, plus `FAILED`, `ABANDONED` |
| `request_id` | varchar(128) | deterministic, see scheme below |
| `bot_uuid` | varchar(128) | target BaaS bot (empty for creations until adopted/recorded) |
| `baas_publish_id` | bigint nullable | the workflow id, written at ID_RECORDED |
| `params` | text (JSON) | operation inputs needed to re-issue (version, target ids, counts) |
| `result` | text (JSON) | step results (created draft id for offline, approval puid, teardown ids) |
| `last_error` | text | last step failure |
| `operator` | varchar(128) | |

Keys: `UNIQUE uk_op (publish_id, operation_kind, stage, attempt)` — the
operation identity a re-run uses to find its intent; `INDEX idx_pub_state
(publish_id, state)` for "any in-flight op for this record?" checks;
`INDEX idx_bot (bot_uuid)` for orphan sweeps.

`operation_kind` values: `verify_first_release`, `verify_upgrade`,
`online_first_release`, `online_upgrade`, `restart`, `scale`,
`offline_destroy`, `rollback_deploy`, `destroy_stage`, `eval_publish`,
`eval_teardown`, `approval_create`. (The verify-vs-first split is recorded as
it resolves at runtime; an upgrade that falls back to first-release abandons
the upgrade op and opens a first-release op — the fallback becomes visible in
the ledger instead of implicit.)

State transitions are CAS-guarded UPDATEs (`WHERE id=? AND state=?`), same
optimistic-lock idiom as `update_publish_status`. All ledger writes go through
a new repository:

- Protocol `PublishOperationRepositoryProtocol` in
  `core/service_bot/repository/publish_operation_protocol.py`.
- Unified ORM impl `plugins/publish_operation_repository.py` (mirrors
  `bot_publish_repository.py`: single body over `DatabasePlugin.orm_session()`,
  runs on OceanBase and SQLite).
- DDL `core/service_bot/sql/ac_publish_operation.sql`.
- DI wiring in `service_bot_module` next to the existing repositories.

## The step runner

`core/service_bot/services/publish_flow/operation_runner.py` —
`PublishOperationRunner`, injected with the ledger repository and
`BaasService`. It is the **only** way flow code issues a BaaS mutation.
API (all synchronous DB ops; the BaaS call is the caller-supplied coroutine):

```python
op = runner.open_operation(publish_id=..., kind=..., stage=..., params=...,
                           bot_uuid=...)          # find-or-create intent (PENDING)
op = await runner.acquire_workflow(op, issue=make_baas_call)   # steps 2+3
runner.complete_operation(op) / runner.fail_operation(op, err) / runner.abandon_operation(op, reason)
```

**All-auto approval (decision).** Every BaaS mutation requests server-side
auto-approval (`auto_approve_publish=True`), and **every client-side approve
call is deleted** — there is no approve step. Verified BaaS semantics behind
the decision (`_bot_management_service._auto_approve_publish`,
`_publish_service.approve_stage`):

- The flag stamps `extra_config.auto_approve` and a server-side loop
  approves every stage gate; while the flag is set, public approve calls
  are **silently ignored** (`_publish_service.py:1420-1427`) — so today's
  client approves on arca/baas workflows are already no-ops, and teclaw is
  the only regime where the client approve is load-bearing.
- All request surfaces already accept the flag: create/update via
  `BotConfig`, scale/restart/stop/destroy/update-devices as explicit request
  fields (`management_router.py:91-153, 313-499`). Flipping teclaw and
  destroy to auto is a client-payload change only — no BaaS server change.
- Consequences accepted: workflows begin executing ~1s after create (first
  releases already behave this way — `release()` approves immediately at
  `bot_build_service.py:538-545`; upgrades/restarts/rollbacks gain the same
  transient execute-before-persist window, which the ledger resume
  converges), and a workflow stuck PENDING by a BaaS-side crash of its
  auto-approve loop is un-drivable from our side (approves ignored) — its
  recovery is **abandon + reissue** via the existing retry path, the same
  recovery already accepted for creation orphans. The ledger makes such a
  workflow visible (op stuck at ID_RECORDED with a non-advancing poll).
- teclaw's post-upgrade MCP refresh (`refresh_after_upgrade`), today gated
  on the approve call's return (`release_stage.py:302-314`), moves to the
  progress poll's SUCCESS handler — triggering on observed deploy success
  rather than approve acceptance.

`acquire_workflow` implements the confirmed resume algorithm:

1. `op.baas_publish_id` set → return (already recorded; caller proceeds).
2. Else if `op.bot_uuid` set (existing-bot mutation) → **ledger differencing**:
   fetch the bot's full workflow list via the new client call, drop every
   workflow id any ledger row for this bot already claims, fence by
   `gmt_create >= op.gmt_create` and by the publish type this kind maps to
   (`UPDATE`/`RESTART`/`SCALE_*`/`STOP`...). Exactly one unclaimed match →
   adopt: CAS `PENDING→ID_RECORDED` with its id. No match → `issue()` now,
   then CAS-record the returned id. (More than one match is impossible by
   SVC-PUB-15 + sequential issuance; treat as FAILED + alert if ever seen.)
3. Else (creation, no bot to list under) → `issue()` and record. A crash
   inside this call is the accepted bounded-orphan window: the PENDING row
   with `kind ∈ {*_first_release, eval_publish}` and no id **is** the orphan
   flag; the next attempt abandons it and proceeds (see OQ1 below).

**Legacy fence / backfill**: `open_operation` for a publish record that
predates the ledger seeds rows from the ext markers it can trust
(`ext.publish.<stage>`, `ext.restart.<stage>`, `ext.scale.publish_id`) as
`ID_RECORDED` (approval state unknown → verify-then-act resolves it). This is
what makes the timestamp fence in step 2 sufficient.

## BaaS surface additions

- **Required (read-only)**: `GET /api/v1/bots/{bot_uuid}/publishes` →
  resolves bot_uuid → bot_id, returns `list_publishes` (id, publish_type,
  status, gmt_create). Service + repo methods already exist; only the route,
  request/response models, and a `BaasService.list_bot_publishes(bot_uuid)`
  client method are new.
- **Optional, decision-gated (spec OQ1b)**: persist `request_id` on
  `baas_publish` + unique `(tenant, request_id)` + return-existing-on-replay
  in `create_bot`/`create_publish`. **Recommendation: defer to a follow-up
  issue.** The creation in-doubt window is sub-second (between the HTTP send
  and the ledger write), its failure mode is now a flagged, never-approved,
  traffic-less orphan that the abandon path reports, and the change carries a
  prod DDL migration on the BaaS table. Not needed for any acceptance
  criterion as scoped. Revisit if observed orphan rate is nonzero.

## Per-operation wiring

| Operation | Today | Change |
|---|---|---|
| Verify/online first release | `ReleaseStageRunner.first_release` 4-step inline | runner op (kind per stage); binding insert + `record_release_ext` keep their order but each records into `result` so re-runs skip; auto-approve requested at create, no client approve |
| Verify/online upgrade | `upgrade_release` inline | runner op; BOT_NOT_FOUND fallback = `abandon(upgrade op)` + open first-release op |
| Restart | fire-and-forget `asyncio.create_task` | `restart_bot` validates + writes intent + enqueues new durable task `service_bot.publish.restart`; handler runs the runner steps; old marker no longer cleared pre-submit (the new op row supersedes it; `sync_restart_progress` reads the latest restart op) |
| Scale | timestamp request_id, ext write after call | runner op; deterministic request id; `sync_scale_progress` reads ledger |
| Offline | 3 un-CAS'd status writes + fire-and-forget destroy | status writes get `source_status`; created-draft id recorded in op `result` (re-run skips creation); destroy = `offline_destroy` op + durable task `service_bot.publish.destroy` |
| Rollback record flips | two separate CAS writes | new combined repo method executes both flips in **one** `orm_session()` transaction; `execute_rollback` deploy leg becomes a `rollback_deploy` runner op (poll enqueue unchanged) |
| Destroy-by-stage (incl. after-online verify teardown) | stop → approve → binding release, best-effort | `destroy_stage` runner op (auto-approved stop); binding-release is a resumable step |
| Eval publish/teardown | nothing persisted | `eval_publish` op records bot_uuid/workflow id in `result`; enqueue TTL `eval_teardown` durable task at publish (OQ3) |
| Approval create | `start_approval` then persist puid | intent row first (`approval_create`, puid into `result` after the call — same in-doubt shape; platform query support checked in implementation, else bounded duplicate is archived by the existing `_archive_approval` path) |
| Approval AGREED trigger | inline call, lost on crash | AGREED callback write also enqueues durable `service_bot.publish.approval_trigger` task; handler calls `process()`/offline (already status-CAS-guarded → idempotent) (OQ4) |
| `retry()` | `is_online_release_recorded` heuristic | reads the online op's ledger state: `ID_RECORDED` ⇒ BaaS-restart branch; `PENDING` ⇒ resume the op (adopt-or-issue); a workflow stuck un-auto-approved ⇒ abandon + reissue |
| New-version publish / user retry to earlier phase | n/a | abandons any non-terminal op rows of the superseded/failed record (spec abandonment criterion) |

`is_online_release_recorded` and the `ext.restart/scale` markers: readers move
to the ledger in group C; ext markers stay **dual-written** for one release
(external dashboards / #157 consumers), then the writes are removed in group D.

## Request-id scheme

`generate_request_id` is replaced by `operation_request_id(op)`:
`pub{publish_id}.{kind}.{stage}.a{attempt}` (fits varchar(128); readable in
BaaS logs). Deterministic per logical operation,
never reused across operations. `restart_devices`' internal `uuid4` and
scale's timestamp id are replaced by the op-derived id. (BaaS treats these as
opaque correlation strings — no server assumption.)

## Key files & functions

New:
- `core/service_bot/repository/publish_operation_protocol.py`
- `core/service_bot/repository/models.py` — `PublishOperationModel/Record`, state/kind enums
- `core/service_bot/sql/ac_publish_operation.sql`
- `plugins/publish_operation_repository.py`
- `core/service_bot/services/publish_flow/operation_runner.py`
- task handlers in `publish_flow/tasks.py`: `PublishRestartHandler`, `PublishDestroyHandler`, `PublishEvalTeardownHandler`, `PublishApprovalTriggerHandler`
- BaaS: publishes-by-bot route in `adapters/web/routers/bot_service/management_router.py` (or publish_router), models in `api/publish_manage/_models.py`
- backend client: `BaasService.list_bot_publishes(bot_uuid)`

Rewired (main):
- `publish_flow/release_stage.py` (both legs onto the runner)
- `bot_build_service.py` — `release()` loses its internal approve (~L538-571); `upgrade()`/`release()` slim down (decomposition)
- `publish_flow/restart_mixin.py` — `restart_bot` → validate+enqueue; `_restart_bot_async` body → task handler using the runner
- `publish_flow/scale_mixin.py`, `publish_flow/rollback_ops_mixin.py`, `publish_flow/eval_publish_mixin.py`
- `bot_publish_service.py` — `offline_publish` (CAS + task), `update_device_binding_with_props` (single-transaction combined write), new combined rollback-flip repo method + `publish_rollback_mixin.rollback_publish`
- `publish_approval_service.py` — `_create_new_approval` intent-first; callback enqueues trigger task
- `publish_flow_service.py` — `retry()` ledger-driven; `is_online_release_recorded` delegates to ledger
- `publish_flow/progress_sync_mixin.py` — restart/scale readers onto ledger;
  sync-success handler gains the teclaw `refresh_after_upgrade` trigger
- `publish_flow/baas_publish_ops_mixin.py` — `approve_baas_publish` deleted
  (all-auto); `get_baas_publish_progress` remains
- `baas_service.py` — extract `_post_workflow_mutation(path, payload)` shared by destroy/stop/restart/scale/upgrade; decompose `_build_create_bot_payload`

## Data model changes

- New table `ac_publish_operation` (above). Created from ORM metadata locally,
  DDL shipped for prod (same convention as `ac_task_queue`).
- No changes to `ac_bot_publish`. Ext keys `restart`/`scale` become
  transitional (dual-written in C, removed in D); `ext.publish.<stage>` and
  `ext.binding.<stage>` remain (poll + binding flows read them).
- Optional OQ1b DDL on BaaS deferred (not in this change).

## Spec open questions — resolutions

- **OQ1a (reconcile-before-create)**: answered NO client-side — BaaS bots have
  no name uniqueness and `list_bots` has no name filter. Creation keeps the
  bounded-orphan semantics; the PENDING-no-id ledger row is the orphan flag.
- **OQ1b**: defer (recommendation above); tracked as a follow-up issue when
  this lands.
- **OQ2 (atomicity)**: same-transaction **within a repository method** — the
  `orm_session()` context commits on exit, so multi-write repo methods (the
  rollback double-flip, binding status+props) are single transactions.
  Cross-repo/cross-table pairs (ledger row + publish record CAS) stay
  ledger-first + convergent re-run; no session-sharing machinery is
  introduced.
- **OQ3 (eval orphans)**: automated — a durable `eval_teardown` task with a
  TTL delay enqueued at eval publish; a crashed teardown re-runs (destroy is
  an existing-bot mutation → fully idempotent via the runner).
- **OQ4 (AGREED re-drive)**: durable trigger task enqueued by the callback
  write; callback redelivery becomes a harmless duplicate (the task and the
  status-CAS'd `process()` both converge).

## Test strategy

- **Crash-window harness** (`tests/community/core/service_bot/services/test_publish_crash_windows.py`):
  the runner accepts an injectable `checkpoint(step_name)` hook (no-op in
  prod). Tests parametrize `(operation, crash_after_step)`: run the operation
  against real repositories on SQLite + a scripted fake `BaasService` that
  records every mutation call; raise at the checkpoint; re-run the
  operation/handler; assert exactly one create/upgrade call reached the fake,
  every mutation payload requested `auto_approve_publish=True`, no approve
  call was ever sent, the workflow id is in the ledger, and the publish
  record reached its expected status. Existing-bot ops also get an
  "in-doubt + already-terminal" case
  (fake lists the workflow as SUCCESS before resume) proving adoption skips
  to the record steps.
- **Ledger repository unit tests**: CAS transitions, unique-key attempt
  bumps, backfill-from-ext seeding.
- **Runner unit tests**: differencing edge cases (pre-ledger rows fenced by
  timestamp, publish-type mismatch ignored, multiple-match → FAILED).
- **Endpoint/E2E**: extend `test_publish_durable_pipeline.py` with restart
  and offline legs driven through the worker; approval-trigger task case in
  the approval endpoint tests; BaaS route test for publishes-by-bot.
- **Regression**: full `tests/community`; the existing `test_publish_tasks.py`
  fake-flow tests updated where handler wiring changed.

## Risks & mitigations

- **BaaS's own auto-approve loop is fire-and-forget** (server-side
  `asyncio.create_task`, bounded iterations) — and under all-auto it is the
  sole approval mechanism: a BaaS pod crash right after create can leave a
  workflow at PENDING with public approves ignored — un-drivable from our
  side. Accepted trade-off of the all-auto decision: the ledger makes the
  stuck workflow visible (non-advancing poll on an `ID_RECORDED` op) and
  recovery is abandon + reissue via the existing retry path — the same
  recovery already accepted for creation orphans. Hardening the loop itself
  (e.g. approving the first gate synchronously in the create request) is a
  candidate follow-up issue on the BaaS side.

- **Ledger/differencing mis-adoption** (claiming a workflow that isn't ours):
  fenced three ways (ledger-known ids, intent timestamp, publish type); the
  impossible >1-match case fails loudly instead of guessing.
- **Transition-period readers** (#157 dashboards reading `ext.restart`):
  dual-write through one release; removal is its own group-D commit that can
  be delayed independently.
- **In-flight records at deploy time**: the legacy backfill seeds ledger rows
  from ext on first touch; records mid-`*_PUB` keep converging via the
  unchanged poll task.
- **Behavioral drift in decomposition**: groups B/C land behavior change with
  tests first; group D is mechanical refactor over a green suite.
- **BaaS route addition**: read-only, additive; gated by its own unit tests;
  no existing endpoint changes.

## Alternatives considered

- **A client-driven approve step (`approve_workflow`, status-confirmed
  verify-then-act)**: kept the direct unstick lever for non-auto workflows
  and zero teclaw behavior change, at the cost of preserving three approval
  regimes and an extra runner step. Rejected after verifying that BaaS
  ignores public approves on auto-approve workflows anyway (the lever only
  ever worked for teclaw) and that abandon+reissue — which the ledger
  provides regardless — covers the stuck-PENDING case uniformly.
- **Request-id dedup on BaaS as the primary mechanism (OQ1b-first)**: exact,
  but requires prod DDL + write-path changes on BaaS for a window the ledger
  already bounds; deferred.
- **Ledger in ext instead of a table**: rejected in the issue discussion —
  ext is a blind-overwrite JSON blob with RMW races; a table gets CAS, keys,
  and queryability (orphan sweeps).
- **Saga/outbox framework**: heavier machinery than needed; the outbox
  concern (advance+enqueue atomicity) is explicitly postponed to #198, and
  the ledger row itself can anchor it later.
- **Reusing `ac_task_queue` rows as the ledger**: task rows are execution
  attempts, not operation identity — lease reclaim and re-enqueue create
  multiple rows per logical operation, exactly what the ledger must not do.
