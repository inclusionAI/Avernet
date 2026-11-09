# Coding-only restart backup

## Validate a completed backup only once (2026-11-05)

After the helper returns a validated `committed` or `partial_success` result,
restart proceeds without querying that receipt again. `running` still polls
until a terminal result within the existing 1500-second backup wait budget.
Backup failures still follow `fastdisk_fail_block`; successful completion cannot
be turned into a backup failure by a later receipt-query error or timeout.

The shared prepare/verify strategy contract is unchanged: final hooks still
check target inventory, bindings, operation ownership and the mutation fence,
but perform no container backup command. This applies to coding BaaS, direct
ARCA and published/Caller restart consumers of the same strategy. Other engines,
non-fastdisk skip behavior and container scripts are unchanged.

## Runtime-preserving backup veto (2026-11-04)

Runtime readiness and restart progress are independent. Ordinary coding admission
keeps the current Bot status, binding and startup markers. QUEUED/BACKING_UP
failures write only `ext.coding_restart`: FAILED, failure_phase and the sanitized
error_message. They never write runtime FAILED/start_status. The mutation fence
clears obsolete startup markers and enters PENDING only after validated backup
completion and the final target checks. Actual post-submission failures keep the existing lifecycle.
BaaS binding PENDING is also delayed until that final check passes.

Published restart admission and failure remain in the coding strategy. The
publish record's status, version, binding and global deployment errors are
preserved on a backup veto. `ext.restart` stores captured engine, operation_id,
stage, operation status/phase/message and the existing precondition_error.
Each explicitly submitted task gets its own operation ID; queue redelivery uses
that same ID, and a new manual attempt gets a fresh container backup receipt.
The operation runner still owns adoption and never reissues an accepted workflow.

The OpenAPI restart acceptance response also retains the operation view/ID so
clients can correlate their subsequent status polls. Both `GET /api/bots/{bot_id}/status` and
`GET /openapi/v1/bots/{bot_id}/status` project an optional `data.ext.aicoding.restart`:
`{operation_id, status: RUNNING|SUCCEEDED|FAILED, phase, message}`. Original
bot_status/status, binding status, is_ready and runtime error_message keep their
meaning. The published `POST /api/service-bot/publish/{publish_id}/restart_status`
adds the same object while preserving the existing publication status and nested
BaaS progress. A queued/pre-backup/blocked operation is answered by the strategy
before querying any old workflow. Legacy/default strategies contribute nothing;
the old JSON shape is retained when there is no restart view. No database schema
or configuration change is required.

The response envelope is extensible: shared schemas declare only a generic `ext`
object. `EngineProvisioningStrategy.project_status_ext` supplies its content;
AICoding alone owns the `aicoding.restart` namespace, the projection of persisted
journals, and the backup-precondition result. Other engines may contribute their
own namespace without changing HTTP handlers or common orchestration. Existing
response extension keys are preserved, and projection never writes the stored
JSON. The existing persistence locations (`Bot.ext.coding_restart` and
`BotPublishRecord.ext.restart`) are not migrated or exposed as the public journal.

The published `restart_status` envelope retains `success=false` for a backup
veto, while `data.status` remains the original publication status. That outcome
is supplied by the strategy through the neutral result `success` flag; it is
not serialized inside `data`. Consumers must read `data.ext.aicoding.restart`
before interpreting the outer `success` flag. `RUNNING` means continue polling,
`FAILED` means stop and display its message without disconnecting the preserved
runtime, and `SUCCEEDED` means the restart workflow completed. Without this
extension, consumers retain their existing nested BaaS-progress logic.

The published OpenAPI surface uses `POST /openapi/v1/bots/{bot_id}/lifecycle/restart`
with `stage=online` or `prestable` (and the existing `staging` alias). Its
202 acknowledgement retains the accepted operation in `data.ext.aicoding.restart`,
including its `operation_id`.
`GET /openapi/v1/bots/{bot_id}/lifecycle` exposes the same four-field restart
view on the matching `data.items[].ext.aicoding.restart`, independently of that card's
`status`/`internal_status`/`deployment`. A veto keeps a running card running;
it is not a new publication failure. Different version cards never share the
restart result. The GET is a read-only projection; the existing background
restart poll persists provider terminal results, so GET adds no workflow I/O.

The lifecycle facade calls the strategy dispatcher without interpreting coding
metadata. Its public projection asks the coding policy for stable, actionable
failure guidance; raw helper/provider diagnostics, credentials and physical
container identifiers are not exposed. Legacy/non-coding cards and acknowledgements omit empty `ext`;
no top-level `restart` or new top-level `operation_id` field is added. No existing required request field or HTTP status changes.

Frontend restart polling must correlate operation_id. ACTIVE while a restart is
RUNNING is not operation success, and restart FAILED must not disconnect the
serving Bot. Do not clear a serving binding at admission. Only deployment/runtime
state may drive online/offline UI. Legacy responses without the new contract keep
their original behavior. No API response claims that a preserved runtime is
healthy if its original runtime state was already unhealthy.

The helper installed in the actual container is authoritative. Current scripts
master uses live backup and clears its barrier on failure; an old installed
stop-before-backup bundle cannot be made non-disruptive by changing status JSON.
Backend timeout does not imply worker exit. Do not delete barriers or alter
production container processes as part of this state-contract fix.

## Non-blocking backup failure warning (2026-11-04)

A confirmed fastdisk backup failure with `fastdisk_fail_block` disabled continues
restart and adds an optional warning to `ext.aicoding.restart` on the ordinary,
published and OpenAPI status projections:

```json
{
  "status": "RUNNING",
  "warning": {
    "code": "backup_failed_continue",
    "message": "快盘备份失败，当前配置不阻断重启，存在数据丢失风险。"
  }
}
```

This warning is not a failure status: consumers show a warning once per polling
session for the current operation and keep polling until its terminal result.
It remains visible in SUCCEEDED/FAILED so a consumer that first observes a
terminal result does not miss the risk. A new manual operation clears the old
warning; queue redelivery of that same operation retains it. Normal Bot polling
correlates operation_id; the existing published page polls by publish_id.
A blocking backup failure instead returns FAILED with its existing sanitized
message: stop polling, show an error, and retain the old runtime.

The coding strategy persists only a fixed warning code, merging the existing
journal/ext atomically. It exposes no helper output, credentials or physical
container IDs. Warning persistence failures propagate rather than returning a
restart success with a silently missing risk notice. Non-fastdisk/failed initial
detection and non-coding engines do not gain this warning. Valid partial backup
receipts still count as success and retain their distinct logs without a new
backup-failure toast. The shared lifecycle and generic response schemas are
unchanged.

## Restart wait and diagnostic events (2026-11-05)

Confirmed fastdisk waits up to 1500 seconds, with or without backup blocking.
Durable queue time, all replicas and task redelivery still share the existing
1500-second backup budget; increasing the per-target limit does not reset it.
Backup/restart failure diagnostics use WARN rather than ERROR. Their failed
status, correlation fields, safe stack and frontend failure semantics remain.

An initial failed detector logs `event=aicoding_restart_fastdisk_detect`; invalid
probe output is `reason=detection_failed`, not a transport failure. Detection
failure means unconfirmed/non-fastdisk and skips the backup helper entirely.
A successful non-fastdisk detection emits no backup event. Inventory/binding
checks use `event=aicoding_restart_probe`, not a backup-failed event. Only a
confirmed fastdisk target invokes the helper and emits
`event=aicoding_restart_backup`. Diagnostic events retain their available
correlation fields, without raw commands, helper output or credentials.
These diagnostic changes preserve receipt checks, the blocking switch and
other-engine behavior. Full and partial successful backup handling is unchanged.

## Restart-only live-mount detection (2026-11-05)

Backend sends a self-contained restart detector, distinct from the publication
probe. Restart confirms fastdisk only when both runtime-home `sessions` and
`workspace` are exact live mountpoints matching their storage directories.
Directory/source checks and symlink rejection remain in place. No live mounts
means `skip_no_fastdisk`, even with stale ready/seeded/request markers or aliased
paths; markers alone never confirm fastdisk. Partial mounts, source mismatches,
unreadable mount tables and execution failures remain failed detections and use
the non-fastdisk skip policy below.

Publication/restore keeps the existing strict detector, including pending-state
and stale-marker safeguards. Positive restart confirmations already persisted
for the same operation remain authoritative across redelivery. Non-fastdisk
and failed initial detections return immediately without helper `start`/`status`,
without reading the blocking switch and without storing a backup-failure warning.
There is no second container-script mount check for those targets. Inventory,
binding/ownership checks and mutation fences remain in place. The container-owned
helper is unchanged and runs only after positive fastdisk confirmation. This
change does not repair mounts or backup failures. No shared orchestration,
frontend, API or container script changes.

## Confirmed-fastdisk policy (2026-10-30)

Backend sends the embedded restart detector. Non-fastdisk/unknown/failed initial
detection skips backup; with blocking explicitly enabled, a positively
confirmed fastdisk target requires
a valid complete or partial backup receipt within 1500 seconds. Failure,
missing capability or unconfirmed result blocks replacement; a validated
`partial_success` permits restart with a distinct warning, even when blocking is enabled. Ordinary durable
restarts persist positive confirmations across redelivery.

Published restart policy and correlated failure logs live in the existing
`restart_backup.py`, contributed by
`AicodingProvisioningStrategy`. It owns the guard, terminal failure/replay handling,
exact-operation failure and status/ext CAS for operation-only progress.
Shared publish orchestration stays in its original `execute_restart` method and
calls declared strategy hooks for admission, execution and progress. No second common execution function or workflow callback is
introduced. The scope owns entry checks and failure handling: an empty result
permits execution; a populated result returns an existing/handled failure. Coding
fills the result only after required failure persistence succeeds. Default scopes
perform no I/O, add no engine metadata and never suppress exceptions. The generic
operation runner retains its adopt-before-issue logic and has no backup exception or coding-state knowledge.
Only the coding strategy catches `RestartBackupBlocked`; transient/post-submit
failures retain the existing crash-resume behavior. No shared service is patched
or reconfigured per request.

## Partial backup is restart success (2026-11-02)

A durably published `partial_success` is a terminal successful backup for restart
policy, not a complete copy of all source data. It permits restart with either
value of `fastdisk_fail_block`. The strategy validates the nested partial receipt:
matching operation ID, nonempty string generation ID, `status=partial_success`,
`error_code=source_files_skipped` and `failed_phase=backup_partial`. Optional
`completeness` must be `degraded` when present. A committed success receipt with
`completeness=degraded` is also classified as partial rather than full success.

The validated terminal partial result logs once at WARN with
`event=aicoding_restart_backup status=partial_success reason=partial_receipt_valid
 action=continue_restart data_loss_risk=true`, plus bot ID, full ARCA ID (including
`@` suffix), owner, environment, operation, generation and elapsed time. Raw helper
diagnostics and file paths are not logged. A valid partial result neither emits
`[AICODING_RESTART_FAILED]` nor writes failure fields for the frontend. Full success
keeps its INFO `phase=prepared status=committed` log; actual failures keep the
existing policy. There is no later `phase=verify status=allowed` or duplicate
partial-success log.

Status, boot identity and receipt fields are validated while waiting for the
terminal result. Malformed receipts and changes observed before successful
completion remain failures governed by the existing switch, not partial successes.
After successful completion, no helper `status`/`start` call or backup deadline
check is made by final hooks. Binding/target checks and ownership fences remain
mandatory. This behavior is confined to the aicoding
strategy; no shared flow, frontend or container-script changes are needed.

## Runtime switch (2026-11-01)

Use the existing `ac_common_config` record (no schema or frontend change):

| Field | Value |
| --- | --- |
| `business_code` | `aicoding` |
| `param_code` | `fastdisk_fail_block` |
| `enable` | `"1"` (configuration record is active) |
| `param_value` | JSON boolean `false` (default) to warn and continue, `true` to block |
| `env` | Chosen by the backend environment, not by the request body |

Missing or disabled records, invalid values and configuration read errors retain
blocking `false` (warn and continue). `enable="0"` disables the configuration
record and also restores this non-blocking default. To enable blocking, use both
`enable="1"` and `param_value=true`. Read errors log only the error type, never raw
exception text. Only a confirmed fastdisk attempt reads the setting. Each backup
precondition snapshots it once across all replicas;
changes affect later attempts, not a backup already using its snapshot.
Already-terminal failed tasks remain failed; the switch does not revive them.

The old `fastdisk_fail_open` key is no longer read. Do not invert an existing row's
value under the old name. Insert the new `fastdisk_fail_block` key in the target
environment; existing old-key rows alone leave blocking disabled. No database
configuration is automatically written or migrated by this code change.

Both values still attempt the same helper and retain the fastdisk 1500-second
wait budget. With `false`, backup-only failures use the existing sanitized
`action=continue_restart data_loss_risk=true` warning with bot/ARCA/owner/env
correlation fields; they do not produce the restart-failed marker. A failed
backup is not reported as successful. Binding changes, ownership/CAS failures
and mutation fences still block. Non-coding engines keep their existing
behavior; non-fastdisk/unknown initial detection skips the helper entirely.
Ordinary BaaS, direct ARCA and published/Caller restarts all use the same coding strategy. Ordinary publication
and restore policy are unchanged.

**Disabling blocking accepts data loss:** replacement can interrupt an unfinished
backup or destroy unpersisted data. Backend does not remove a maintenance barrier,
repair a failed helper or guarantee the replacement will become ready.

Operator example (not automatically executed): set `OCB_BASE_URL` to the target
backend origin and `OCB_COOKIE` to your authorized operator login cookie. The
existing `upsert` route inserts a missing record or updates that same key in the
backend's current environment. Use `create` instead to reject an existing key.

```bash
curl --fail-with-body --request POST \
  "${OCB_BASE_URL%/}/api/v1/common-config/upsert" \
  --header 'Content-Type: application/json' \
  --cookie "$OCB_COOKIE" \
  --data-raw '{
    "business_code": "aicoding",
    "business_name": "AICoding",
    "param_code": "fastdisk_fail_block",
    "param_name": "快盘备份失败阻断重启",
    "param_value": false,
    "enable": "1"
  }'
```

Keep `enable="1"` and set `param_value` to `true` to block after backup failure.
The example uses the default `false`, which allows continuation after backup failure.
Confirm the response body's `success` is `true` (HTTP 200 alone is insufficient).
The existing `POST /api/v1/common-config/get` accepts the same `business_code` and
`param_code` for readback; its returned `env` identifies the effective environment.

## Non-fastdisk / unconfirmed skip policy (2026-11-05)

An initial non-fastdisk result or detection failure skips backup entirely: no
helper `start`, no helper `status`, no second mount check and no backup-failure
log or UI warning. Detection failures retain their separate sanitized WARN
`event=aicoding_restart_fastdisk_detect`; successful non-fastdisk detection is
not a failure. Previously persisted positive confirmations for the same operation
are not downgraded by redelivery.

Binding/engine checks, database failures and durable operation fences retain
their existing safety semantics. Inventory lookup failures remain handled by
the existing strategy policy and are not successful backup receipts. Only a
confirmed fastdisk target enters the helper: with blocking disabled, backup-only
failures warn and continue; enabling blocking vetoes replacement. No runtime-script,
shared service, frontend or restore-protocol changes are part of this policy.

## Boundary

This is an aicoding/claude_code provisioning policy, not a generic lifecycle or
command-execution framework. Its backup implementation, coding-specific offloading, compatibility checks,
polling, physical-target selection, receipt verification and logs live in
`restart_backup.py`. The registry resolves the existing provisioning strategy
and dispatches instance preconditions through its declared async contract; it
contains no backup logic or concrete strategy type checks.

Ordinary Bot restart calls `strategy.prepare_restart` immediately BEFORE its
existing lock acquisition, then runs the returned target/binding verifier immediately
AFTER acquiring that same lock. The verifier does not query completed backup results. The strategy never sees, acquires, or releases the
lock: acquire/release/async hand-off ownership stays entirely in the original
caller, backup-only failures warn and continue, while lifecycle failures
retain the caller's existing lock-release behavior. The default strategy
returns no verifier and never probes devices. The synchronous stop/start/update and allocation lock hand-off remain in
BotService. Ordinary HTTP coding restart now admits a durable task before this
precondition; its operation journal and eventual mutation state are owned by the
coding strategy. No restart-lock repository or TTL changes are required.

Async HTTP entrypoints call `BotServiceProtocol.restart_bot_async`. BotService
resolves the strategy and calls its `execute_restart` contract; explicit
`aicoding` / `claude_code` engines with an eligible existing binding enqueue
durable work without changing the current runtime status. Workers
initialize/recover the operation journal and enter PENDING only at mutation. Unbound recovery and existing
activation-in-progress cases retain their original threaded, awaited lifecycle
so that admission does not erase their original FAILED/ACTIVE state. Default engines execute inline. The coding worker retains the original direct-ARCA stop/start flow;
ordinary BaaS uses the coding BaaS orchestrator. The worker
supplies a stable backup operation ID and a CAS mutation fence through the
coding-owned execution context; direct synchronous callers use the same best-effort backup policy.
See `apps/backend/specs/2026-10-08-aicoding-durable-restart/spec.md`. Published restart
and caller upgrade each invoke the coding precondition before replacement.
Ordinary publication, instance creation/reuse and workflow adoption are not
backup operations. Instance consumers call `prepare_restart_async`, which delegates to the same
`prepare_restart` precondition and runs its returned verifier (they hold no
restart lock). Default engines do not probe or offload; coding offloads both
phases to a worker thread. Backup failures warn and continue the original replacement; lifecycle failures propagate.
The registry adapter is shared dispatch, not an independent lifecycle pipeline.

## Existing transports only

- Direct ARCA bindings detect live mounts before their strategy-owned replacement
  flow and invoke the asynchronous helper only for confirmed fastdisk targets,
  reusing the original stop/start primitives. Their physical target comes directly
  from the old binding's `device_props.sandbox_id`, exactly as destruction does; no BaaS
  inventory or sandbox-list query is involved. The existing provider execution
  hook retains ownership of sandbox suffix/tenant translation.
- `BoundArcaRestartExecutor` in `aicoding/restart_arca.py` is a strategy-owned
  pinned `execute(command)` / `verify()` executor. It consumes the same module's
  `ArcaRestartBindings` binding-read port and `ArcaRestartTransport` command
  contract. A composition root supplies the transport from the existing Arca
  provider hook; the shared router gains no new API. The executor has
  no HTTP exposure or default-provider fallback. ACTIVE/PENDING/FAILED/STOPPED
  bindings may still identify a live old sandbox and are eligible for backup.
  Normal `exec_shell_new` keeps its existing ACTIVE/PENDING restriction. The
  executor never modifies binding status and never re-resolves a new target by
  logical device ID. Database failures, changed physical binding targets, and
  released bindings remain blocking, even after a failed backup attempt.
- Missing sandbox identity and unreachable/destroyed runtimes produce sanitized
  best-effort warnings and continue recovery; neither is a successful backup.
  New durable tasks capture `sandbox_id` (including its tenant suffix), in
  addition to the existing binding/device/provider identity. Pre-rollout tasks
  without this field capture the target at preparation and retain the existing
  binding/device/provider checks. Repeated helper calls use the same operation
  ID; backup waits outside the restart lock and verification runs under it.
- Other non-BaaS providers use their existing `exec_shell_new` to detect live
  mounts and back up only confirmed fastdisk targets; unavailable FAILED/STOPPED
  containers warn and continue without execution.
- Coding restart calls the existing `list_devices_by_bot_uuid` client and
  `GET /bots/{uuid}/devices` for both prepare and verification. The endpoint
  returns device groups in descending Bot record ID order (excluding deleted
  records); the existing client selects the first/current group, not historical
  groups. It supplies status and physical provider ID without health probes.
  Empty inventories require terminal-state confirmation as described below;
  malformed inventories and lookup failures warn and continue replacement. No change
  to the shared `get_bot` contract or non-coding restart flow is required.
- BaaS inventory identifies every live physical target. Commands use the
  existing public `post_bots_api` API and existing PaaS command endpoint, pinned
  to that target rather than randomly selecting a replica.
- BaaS-managed ARCA containers still use BaaS physical-target backup.
  The shared DeviceService ACTIVE/PENDING execution restriction is unchanged.
- No `allow_recovery` or `paas_device_id` parameter is added to shared APIs.
  Missing identity and transport errors are not treated as successful backup.
- A non-empty BaaS inventory whose devices are all explicitly `STOPPED`/`RELEASED`
  is a confirmed no-live-target case: no container backup is attempted, and the
  inventory is rechecked immediately before replacement. An empty inventory is
  allowed only after `get_bot` confirms BaaS `FAILED`
  with the matching `bot_uuid`, or `RELEASED` (including the client's existing
  not-found contract), and a second inventory read is still empty. An explicitly mismatched Bot
  UUID is rejected even for RELEASED. If a device appears during confirmation,
  warn and continue without running backup against the newly observed container. The same
  confirmation is repeated by the verifier before replacement. Local Bot
  `FAILED` alone is not sufficient; durable admission may already have changed
  it to `PENDING`. Active/pending/unknown BaaS states, malformed inventories and
  lookup failures degrade backup without blocking restart. Non-empty inventories always retain the
  physical-target backup checks, even for FAILED Bots. This coding-only policy
  also applies to published/caller instance preconditions; shared BaaS APIs,
  the direct ARCA stop/start lifecycle and other engine policies are unchanged.

## Runtime contract

The paired startup scripts install the root-owned helper and exact admin sudo
rule before enabling the canonical sessions/workspace binds. This rollout
assumes pre-rollout containers do not have those binds.

An absent `/opt/agentclaw/bin/restart_backup` is a legacy skip only when both the
new `/opt/agentclaw/restart-backup-v1` installation and the restart barrier are
absent. The older `.fastdisk.ready` marker is not a backup capability marker.
A permission error is not absence. Once installed, the runtime helper checks
actual mounts; `not_mounted` skips backup only under the non-blocking policy.
Current helpers perform a live backup without stopping business processes and
return a durable complete or partial generation/operation receipt. Unconfirmed
backup follows the switch above, rather than counting as success.

Inventory logs include `source=baas_devices`, query start/failure, device count,
status distribution, resolved target count, operation ID and elapsed time.
Logs carry `event=aicoding_restart_backup`, phase, status/reason, Bot/target,
operation, elapsed time and generation. The policy does not log raw output or
exception messages. Poll logs occur on transitions or at most once per minute.

## Restart failure alerts (2026-10-30)

The coding strategy emits WARN logs with `[AICODING_RESTART_FAILED]` and
`event=aicoding_restart_failed` for confirmed-fastdisk backup vetoes, invalid
backup receipts, and blocking preparation errors. Ordinary task and
published-restart exception handlers retain the underlying backup diagnostics
and log before persisting failure, so a failed status write cannot hide the
original backup error. This is diagnostic-only: no queue, retry, publication,
status or replacement decisions are changed.

Each record includes `bot_id`, `owner_id`, `env`, `arca_id`, raw `target_id`, `operation_id`, `phase`,
`reason`, `error_type`, chained `cause_types`, `elapsed_ms`, `helper_status`,
`binding_id`, `publish_id`, and a safe `stack`. Stages distinguish `prepare`,
`verify_target`, `precondition`, `task_failed`, and `published_restart`.
Stack entries contain only filename, line number and function; raw exception
messages, source lines, locals, commands and helper stdout/stderr are excluded.
Unknown/unavailable fields use `-` rather than an invented value.

`arca_id` comes from the captured direct-ARCA `sandbox_id` or a BaaS physical
`provider_device_id` beginning with `ARCA-SANDBOX-`. Both `arca_id` and
`target_id` preserve the complete routing identity, including any `@tenant`
suffix (for example `@8`). Log formatting never changes provider execution
parameters or tenant translation. Exception wrappers
preserve the resolved physical target. Inventory verification errors retain the
previously captured physical targets; when several containers are involved, emit
one record per captured container rather than arbitrarily selecting a replica.
`owner_id` comes from the Bot provisioning context or durable restart state, not
the user triggering restart. `env` uses the existing `get_current_env()` data
environment (`dev`/`pre`/`prod`; gray maps to prod), not the deployment profile or
publish stage. These values do not require extra database/container lookups.
A logical Bot/device ID is never labeled
as an ARCA ID. Failures without a resolved physical target have `arca_id=-` and
must be correlated by Bot/operation; logging performs no extra inventory or
database I/O.

Alert on WARN plus `event=aicoding_restart_failed` (or the bracketed marker).
Multiple phases can log the same failure: group/deduplicate by `bot_id` and
`operation_id`, and use `arca_id`/`phase` for diagnosis. These logs cover this
strategy's backup/precondition and exception failure boundaries, not a new
universal event contract for all deployment/queue failures. Successful non-fastdisk
detection emits no backup warning; failed initial detection uses its separate
WARNING with `action=continue_restart`, without this failure marker.
Lost-ownership `RestartSuperseded` also emits no new failure alarm. Other engines and shared lifecycle orchestration do not use this logger.

## Validation boundary

Policy/entrypoint tests cover legacy helper absence, ACTIVE/PENDING/FAILED/
STOPPED recovery, default engines, unchanged shared command guards, existing
PaaS POST transport, physical pinning, backup failure, lock order and receipt
changes. Linux root/admin, live provider routing and NAS need staging validation
with the paired scripts. No test here restarts a production Bot.

## Runtime wire contract v1

The helper CLI is `restart_backup start|status OPERATION_ID`. Platform exec must
return exit code zero and stdout containing one JSON object. Unknown versions,
unknown states, malformed output and nonzero/missing exit codes invalidate the
backup result, but the best-effort policy allows replacement.
Additional JSON fields are ignored to allow additive v1 changes.

| Field | v1 requirement |
| --- | --- |
| `version` | Integer `1`; incompatible versions must not be silently accepted |
| `operation_id` | The requested operation ID, identical throughout one operation |
| `status` | `legacy`, `not_mounted`, `running`, `committed`, `partial_success`, or `failed` |
| `boot_id` | Nonempty container-boot identity, required except for `legacy` |
| `backup` | Required for `committed` or `partial_success`: matching `operation_id` and nonempty `generation_id`; committed uses nested `status: success`, partial uses the partial receipt contract above |

`legacy` is emitted only by the backend's missing-helper probe when both new
capability markers are absent; it is not a fallback for transport errors.
`not_mounted` means the installed helper found no canonical data mounts.
`running` requires polling; it must not transition to a skip state. `committed`
means a durable successful generation; `partial_success` means a durable generation
with omitted source files. Both allow restart after receipt validation, with
distinct logs. `failed` and unconfirmed results follow the blocking switch.
Once a complete or partial result passes validation, it is final for that backup
attempt and is not queried again before replacement. The existing target/binding
checks and mutation fences still run; they do not start backup work on replacement
targets discovered during verification.

The paired script repository must implement this same v1 contract. Rollout must
install the root-owned helper and admin sudo permission before enabling mounts.
An OCB rollback is not safe for already-mounted runtimes unless a compatible
backup gate remains in place. Never interpret CI mocks as root/admin or NAS
integration evidence.

The current polling interval is 2 seconds and the observation deadline is 1500
seconds. This is not a hard transport timeout: an in-flight exec uses its existing
transport timeout. These constants remain unchanged in this boundary-only patch;
configuration and executor-capacity tuning are separate unresolved review items.

## Direct ARCA rollout and propagation

The coding strategy owns the pinned executor, transport contract,
recovery-state eligibility and blocking target errors in `restart_arca.py`.
The OCB composition root selects the registered Arca provider and supplies its
existing `_exec_shell_new` hook; that hook and `SandboxRuntimeClient` retain
sandbox suffix/tenant translation. No router API, SDK dependency, public HTTP
contract, generic exec permission, queue type or database schema is introduced.
OCB must update its Avernet gitlink to deploy the strategy and transport wiring
together. Corp contract tests assert that exec and destruction select the same
raw sandbox ID and tenant, including configured Coding suffixes.

The helper must already be installed in direct-ARCA images. `start` must return
promptly while container-side work continues; platform exec retains its existing
transport timeout. Pre-rollout images use the existing strict legacy probe.
Before rollout, verify helper ownership/sudo, committed receipts, durable storage
and restore into a replacement sandbox in staging. Unit mocks do not prove
those runtime/storage properties. This change does not install runtime scripts
or introduce a new restore format.

## Coding Arca replacement orchestration

For admitted, bound Arca coding tasks, `AicodingArcaRestart` in
`restart_arca_lifecycle.py` owns backup-before-lock ordering, under-lock target
verification and mutation fencing, service-bot default-image selection, and
stop/start orchestration. Like `AicodingBaasRestart`, it is invoked directly by
the coding worker, not through `BotService.restart_bot`. Shared stop/start,
provider selection and allocation remain existing lifecycle collaborators.

A busy lock causes rescheduling without mutation. A preparation/verification
failure never releases the old sandbox. Before allocation handoff, exceptions
release only the acquired lock token; after successful handoff, the allocation
worker retains lock-release ownership. Ambiguous post-fence failures are observed
without replaying stop/start. Failed release retains the original restart warning.

Non-coding engines, unbound recovery, and admission's activation-in-progress
fallback retain the existing generic lifecycle. Direct synchronous callers also
retain their existing strategy precondition hook. This change does not migrate
all device lifecycle behavior into coding policy or change published/Caller flows.

## Unified backup dependency assembly

`RestartBackupDependencies` in `restart_dependencies.py` is the single backup
dependency passed by BotService and both coding restart executors. Its immutable
fields hold factories for the existing BaaS inventory/command runtime and the
pinned Arca transport. `BotManagementModule` assembles these lazily; OCB keeps
selecting the existing Arca hook in its composition root. Constructing the
envelope does not resolve either provider, perform network I/O or introduce a
BotService/DeviceService dependency cycle. Non-coding strategies and unbound
recovery do not resolve its factories.

The strategy resolves only the selected capability. BaaS resolution failures
still degrade backup inside the existing best-effort boundary; missing Arca
transport still blocks replacement. Binding/database checks stay outside that
boundary. This is dependency consolidation, not a new common provider API: BaaS
continues to use inventory and Arca continues to pin the old binding's sandbox.

The `prepare_instance_restart` published/Caller dispatcher passes its existing
`target_runtime` argument unchanged to the engine hook. Only the coding strategy
wraps it with `RestartBackupDependencies.for_instance`; default/other engines do
not construct coding backup dependencies. No provider-specific backup factory is passed
through BotService or the engine hook anymore. BotService's existing BaaS service
dependency remains for non-backup lifecycle operations; this change does not
remove that shared functionality.

Propagation: deploy the updated DI assembly, BotService, strategy and tests
together. Direct internal constructors that exercise coding backup must supply
the envelope instead of `arca_restart_transport_provider`; the default represents
unavailable backup capabilities, not backup success. HTTP inputs, queue payloads,
provider APIs and backup helper/receipt formats are unchanged.
