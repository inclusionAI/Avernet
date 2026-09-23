# Coding-only restart backup

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
caller, so a prepare failure leaves no lock to clean up and a verify failure
flows through the caller's existing `finally` release. The default strategy
returns no verifier and never probes devices. Existing duplicate handling,
stop/start/update, status transitions and allocation lock hand-off remain
untouched. No lock repository or TTL changes are required.

Async HTTP entrypoints call `BotServiceProtocol.restart_bot_async`. BotService
resolves the strategy and calls its `execute_restart` contract; coding offloads
the original synchronous restart method, while default engines execute inline.
The original synchronous Service API is unchanged. Published restart
and caller upgrade each invoke the coding precondition before replacement.
Ordinary publication, instance creation/reuse and workflow adoption are not
backup operations. Instance consumers call `prepare_restart_async`, which delegates to the same
`prepare_restart` precondition and runs its returned verifier (they hold no
restart lock). Default engines do not probe or offload; coding offloads both
phases to a worker thread. Failure propagates before the original replacement.
The registry adapter is shared dispatch, not an independent lifecycle pipeline.

## Existing transports only

- Active/Pending ARCA bindings use the original `exec_shell_new` API unchanged.
- BaaS inventory resolves the current record via `GET /bots/{uuid}`, then reads
  `GET /bots/{id}/detail-by-id` using the backend client’s `include_devices=True`.
  The default UUID detail response does not populate devices. No health probe is
  needed, and historical UUID records must not be flattened together. Both prepare
  and verification use this lookup; missing/malformed inventory blocks replacement.
- BaaS inventory identifies every live physical target. Commands use the
  existing public `post_bots_api` API and existing PaaS command endpoint, pinned
  to that target rather than randomly selecting a replica.
- Recovering FAILED/STOPPED ARCA bindings use their stored physical sandbox ID
  with the same PaaS command endpoint. This is engine-owned recovery handling;
  the shared DeviceService ACTIVE/PENDING execution restriction is unchanged.
- No `allow_recovery` or `paas_device_id` parameter is added to shared APIs.
  Missing identity and transport errors are not treated as successful backup.
- A non-empty BaaS inventory whose devices are all explicitly `STOPPED`/`RELEASED`
  is a confirmed no-live-target case: no container backup is attempted, and the
  inventory is rechecked immediately before replacement. An actually empty or
  malformed inventory remains fail-closed.

## Runtime contract

The paired startup scripts install the root-owned helper and exact admin sudo
rule before enabling the canonical sessions/workspace binds. This rollout
assumes pre-rollout containers do not have those binds.

An absent `/opt/agentclaw/bin/restart_backup` is a legacy skip only when both the
new `/opt/agentclaw/restart-backup-v1` installation and the restart barrier are
absent. The older `.fastdisk.ready` marker is not a backup capability marker.
A permission error is not absence. Once installed, the runtime helper checks
actual mounts; `not_mounted` skips backup. Mounted runtimes must stop writers and
return a committed generation/operation receipt before replacement is allowed.

Logs carry `event=aicoding_restart_backup`, phase, status/reason, Bot/target,
operation, elapsed time and generation. The policy does not log raw output or
exception messages. Poll logs occur on transitions or at most once per minute.

## Validation boundary

Policy/entrypoint tests cover legacy helper absence, ACTIVE/PENDING/FAILED/
STOPPED recovery, default engines, unchanged shared command guards, existing
PaaS POST transport, physical pinning, backup failure, lock order and receipt
changes. Linux root/admin, live provider routing and NAS need staging validation
with the paired scripts. No test here restarts a production Bot.

## Runtime wire contract v1

The helper CLI is `restart_backup start|status OPERATION_ID`. Platform exec must
return exit code zero and stdout containing one JSON object. Unknown versions,
unknown states, malformed output and nonzero/missing exit codes block replacement.
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
blocks replacement. Before replacement, the status, boot identity and committed
generation are checked again; a changed identity/receipt blocks replacement.

The paired script repository must implement this same v1 contract. Rollout must
install the root-owned helper and admin sudo permission before enabling mounts.
An OCB rollback is not safe for already-mounted runtimes unless a compatible
backup gate remains in place. Never interpret CI mocks as root/admin or NAS
integration evidence.

The current polling interval is 2 seconds and the observation deadline is 1500
seconds. This is not a hard transport timeout: an in-flight exec uses its existing
transport timeout. These constants remain unchanged in this boundary-only patch;
configuration and executor-capacity tuning are separate unresolved review items.
