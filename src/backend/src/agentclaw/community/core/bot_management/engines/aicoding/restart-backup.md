# Coding-only restart backup

## Boundary

This is an aicoding/claude_code provisioning policy, not a generic lifecycle or
command-execution framework. Its implementation, async adaptation, compatibility
checks, polling, physical-target selection, receipt verification and logs all
live in `restart_backup.py`. The registry only re-exports the two entrypoints.

Ordinary Bot restart calls `strategy.prepare_restart` once in place of its
existing lock acquisition. The default strategy only invokes the original
acquisition callback; it never probes devices. The coding strategy prepares
before acquiring that same lock, verifies binding and receipt under it, and
releases the acquired lock on verification failure. Existing duplicate handling,
stop/start/update, status transitions and allocation lock hand-off remain in the
original caller. No lock repository or TTL changes are required.

Async HTTP entrypoints invoke the coding-owned adapter; it offloads only coding
restart work. Non-coding operations execute inline as before. Published restart
and caller upgrade each invoke the coding precondition before replacement.
Ordinary publication, instance creation/reuse and workflow adoption are not
backup operations. There is no new generic `execute_restart` strategy method.

## Existing transports only

- Active/Pending ARCA bindings use the original `exec_shell_new` API unchanged.
- BaaS inventory identifies every live physical target. Commands use the
  existing public `post_bots_api` API and existing PaaS command endpoint, pinned
  to that target rather than randomly selecting a replica.
- Recovering FAILED/STOPPED ARCA bindings use their stored physical sandbox ID
  with the same PaaS command endpoint. This is engine-owned recovery handling;
  the shared DeviceService ACTIVE/PENDING execution restriction is unchanged.
- No `allow_recovery` or `paas_device_id` parameter is added to shared APIs.
  Missing identity and transport errors are not treated as successful backup.

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
