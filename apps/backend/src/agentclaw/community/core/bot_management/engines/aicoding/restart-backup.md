# Coding-only restart backup

## Confirmed-fastdisk policy (2026-10-30)

Backend sends the embedded publication detector. Unknown/failed initial detection
uses the best-effort policy below; a positively confirmed fastdisk target requires
a successful, complete backup receipt within 300 seconds. Failure, partial backup,
missing capability or unconfirmed result blocks replacement. Ordinary durable
restarts persist positive confirmations across redelivery.

Published restart policy lives in `restart_publish.py`, contributed by
`AicodingProvisioningStrategy`. It owns the guard, terminal failure/replay handling,
exact-operation failure and status/ext CAS for the existing frontend fields.
Shared publish orchestration only calls the declared `execute_published_restart`
and `acquire_restart_workflow` strategy hooks. Default implementations delegate
without extra I/O or engine metadata. The generic operation runner retains its
adopt-before-issue logic and has no backup exception or coding-state knowledge.
Only the coding strategy catches `RestartBackupBlocked`; transient/post-submit
failures retain the existing crash-resume behavior. No shared service is patched
or reconfigured per request.

## Non-fastdisk / unconfirmed best-effort policy (2026-10-12)

Backup is attempted, but backup-only failures log sanitized warnings and do not
block restart. This includes inventory, command, timeout, receipt and runtime boot-identity
failures. Changes to the captured Arca binding/sandbox target remain blocking. Warnings carry `action=continue_restart` and
`data_loss_risk=true`; they are not successful backup receipts. Binding/engine
checks, database failures and durable operation fences remain blocking.
Restart can lose data newer than the last durable backup; a timeout does not
cancel the old helper, and replacement may interrupt it. No runtime-script,
shared service, frontend or restore-protocol changes are part of this policy.

## Boundary

This is an aicoding/claude_code provisioning policy, not a generic lifecycle or
command-execution framework. Its backup implementation, coding-specific offloading, compatibility checks,
polling, physical-target selection, receipt verification and logs live in
`restart_backup.py`. The registry resolves the existing provisioning strategy
and dispatches instance preconditions through its declared async contract; it
contains no backup logic or concrete strategy type checks.

Ordinary Bot restart calls `strategy.prepare_restart` immediately BEFORE its
existing lock acquisition, then runs the returned verifier immediately AFTER
acquiring that same lock. The strategy never sees, acquires, or releases the
lock: acquire/release/async hand-off ownership stays entirely in the original
caller, backup-only failures warn and continue, while lifecycle failures
retain the caller's existing lock-release behavior. The default strategy
returns no verifier and never probes devices. The synchronous stop/start/update and allocation lock hand-off remain in
BotService. Ordinary HTTP coding restart now admits a durable task before this
precondition; its persisted pending state and operation journal are owned by the
coding strategy. No restart-lock repository or TTL changes are required.

Async HTTP entrypoints call `BotServiceProtocol.restart_bot_async`. BotService
resolves the strategy and calls its `execute_restart` contract; explicit
`aicoding` / `claude_code` engines with an eligible existing binding enqueue
persist PENDING synchronously before enqueueing durable work. Workers
initialize/recover the operation journal, not initial PENDING. Unbound recovery and existing
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

- Direct ARCA bindings now attempt the same asynchronous helper protocol before
  their strategy-owned replacement flow, reusing the original stop/start primitives. Their physical target comes directly from the
  old binding's `device_props.sandbox_id`, exactly as destruction does; no BaaS
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
- Other non-BaaS providers retain their existing `exec_shell_new` backup attempts;
  unavailable FAILED/STOPPED containers warn and continue without execution.
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
actual mounts; `not_mounted` skips backup. Mounted runtimes must stop writers and
return a committed generation/operation receipt for backup to count as successful.
Unconfirmed backup still allows restart, with a data-loss warning.

Inventory logs include `source=baas_devices`, query start/failure, device count,
status distribution, resolved target count, operation ID and elapsed time.
Logs carry `event=aicoding_restart_backup`, phase, status/reason, Bot/target,
operation, elapsed time and generation. The policy does not log raw output or
exception messages. Poll logs occur on transitions or at most once per minute.

## Restart failure alerts (2026-10-30)

The coding strategy emits ERROR logs with `[AICODING_RESTART_FAILED]` and
`event=aicoding_restart_failed` for confirmed-fastdisk backup vetoes, receipt
verification failures, and blocking preparation errors. Ordinary task and
published-restart exception handlers retain the underlying backup diagnostics
and log before persisting failure, so a failed status write cannot hide the
original backup error. This is diagnostic-only: no queue, retry, publication,
status or replacement decisions are changed.

Each record includes `bot_id`, `owner_id`, `env`, `arca_id`, raw `target_id`, `operation_id`, `phase`,
`reason`, `error_type`, chained `cause_types`, `elapsed_ms`, `helper_status`,
`binding_id`, `publish_id`, and a safe `stack`. Stages distinguish `prepare`,
`verify`, `verify_target`, `precondition`, `task_failed`, and `published_restart`.
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

Alert on ERROR plus `event=aicoding_restart_failed` (or the bracketed marker).
Multiple phases can log the same failure: group/deduplicate by `bot_id` and
`operation_id`, and use `arca_id`/`phase` for diagnosis. These logs cover this
strategy's backup/precondition and exception failure boundaries, not a new
universal event contract for all deployment/queue failures. Non-fastdisk or
failed initial detection retains WARNING with `action=continue_restart`, without
this failure marker. Lost-ownership `RestartSuperseded` also emits no new failure
alarm. Other engines and shared lifecycle orchestration do not use this logger.

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
| `status` | `legacy`, `not_mounted`, `running`, `committed`, or `failed` |
| `boot_id` | Nonempty container-boot identity, required except for `legacy` |
| `backup` | Required for `committed`: object with `status: success`, matching `operation_id`, and nonempty `generation_id` |

`legacy` is emitted only by the backend's missing-helper probe when both new
capability markers are absent; it is not a fallback for transport errors.
`not_mounted` means the installed helper found no canonical data mounts.
`running` requires polling; it must not transition to a skip state. `committed`
means writers have been stopped and the backup generation is durable. `failed`
degrades backup but allows replacement. Before replacement, the status, boot
identity and committed generation are checked again; changes warn and continue.
Changed physical inventories stop receipt probing against those targets.

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
