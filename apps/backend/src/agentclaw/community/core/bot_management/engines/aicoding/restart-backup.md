# Coding-only best-effort restart backup

## Policy and boundary

For aicoding/claude_code, restart attempts a final backup but **backup failure
is not a veto on the original restart**. This deliberately prioritizes recovery
of an unavailable Bot over preservation of its most recent unbacked-up data.

The implementation lives in `restart_backup.py`, shared by ordinary coding
restarts, published-instance restarts and Caller recovery. Default/non-coding
strategies remain no-ops. Direct ARCA bindings skip the backup protocol and
retain their original stop/start lifecycle and binding verification. Other
non-BaaS binding providers also skip backup while retaining binding verification. A BaaS
binding backed by an ARCA sandbox is still a BaaS binding and attempts backup.

Only Backend changes are required. The existing helper's `start|status` v1
contract, BaaS command API, container startup scripts and frontend/status API
are unchanged. Historical containers need not be upgraded before restarting.

## Flow

1. Read the original binding and enforce durable restart ownership as before.
2. Query BaaS devices; pin backup commands to observed physical IDs.
3. Call the helper through `/paas/devices/{physical_id}/commands`.
4. Observe its result, then recheck inventory and any available receipt. Ordinary Bot
   restarts do this under the existing restart lock; published-instance and
   Caller paths do not hold that lock.
5. Continue the original destroy/create or stop/start flow, even when backup
   I/O, result validation or the observation budget failed.

A failure on one replica does not prevent backup attempts on remaining replicas.
Unknown inventory never triggers random target selection or a backup on a newly
appeared replica during final verification.

## Failures that continue restart

- Empty/malformed/missing backup inventory, or inventory lookup failure.
- Unreachable old container, missing helper, permissions or execution errors.
- Helper `failed`, invalid output, unknown status/version, or missing receipt.
- Backup observation timeout, or receipt/status/generation verification failure.

An absent helper with no new installation/barrier marker remains `legacy`;
a helper-confirmed absence of canonical mounts remains `not_mounted`. Other
errors are **not** mislabeled as either of these or as successful backup.
They produce a sanitized WARNING with `status=degraded`,
`action=continue_restart`, `data_loss_risk=true`, phase, operation ID and target.
Raw commands, stdout/stderr and exception messages are never logged by this
policy. The shared BaaS client is unchanged and may still log raw response
bodies; transport-wide log redaction is a separate follow-up, not a guarantee
of this patch. Existing lower-level validators still reject invalid receipts; the
coding policy catches their failures at the backup-only boundary.

## Guards that remain mandatory

- Original authorization, lifecycle admission and restart lock checks.
- Durable operation ownership and CAS mutation fencing.
- Bot binding or engine changes and failed binding/database reads or writes.
Backup-only physical inventory or boot-identity changes are warnings, not
restart vetoes. Changed inventories stop receipt probing against those targets;
they do not prevent the original lifecycle from submitting its restart.

Unknown inventory is not proof of a changed identity. If inventory cannot be
confirmed, backup degrades and the original lifecycle guards remain responsible
for replacement. Database reads/writes and mutation fences are outside the
best-effort catch boundary. Task cancellation/process termination is not
converted into successful preparation.

## Timeouts and legacy runtime limitations

The **runtime archive execution budget remains 900 seconds (15 minutes)**.
The Backend observation budget remains 1500 seconds (25 minutes), covering
service termination (up to 330 seconds), archive execution and transport costs.
Ordinary durable restarts share one observation budget across queue time,
redeliveries and physical targets; published/Caller paths retain their existing
per-target budget. BaaS receipt rechecks and the durable mutation fence remain
immediately before provider submission, after request preparation.
Polling remains every 2 seconds. These are not new runtime settings: this patch
does not install, patch, cancel or upgrade scripts in existing containers.
In-flight command calls use their existing transport timeout.

On timeout Backend stops waiting and continues replacement. It does **not**
claim that the old helper has stopped: historical helpers have no guaranteed
cancel/recovery API. Destruction may interrupt a still-running backup. Runtime
failure recovery remains whatever the installed helper supports; it is not a
new prerequisite for restarting.

Backup warnings do not fail the restart task or set Bot/start status to FAILED.
The actual original lifecycle determines success/failure and `/status` polling
continues unchanged. Warning visibility in this minimal patch is operational
logs, not a new frontend notification/response field.

## Compatibility and risk

This is an intentional relaxation of the previous mandatory backup policy.
Data newer than the last successful durable generation may be lost. A later
BaaS replacement failure can leave an old container with stopped services;
this patch cannot guarantee rollback on historical helpers. It also does not
change BaaS's handling of destroy failures or clean up orphan containers.
Reverting this Backend policy restores backup fail-closed behavior, not lost
runtime data. Never represent a degraded attempt as a durable backup receipt.

## Validation

Tests cover backup/transport/parse/timeouts, unavailable inventory, per-replica
attempts, late verification failures, target/binding fencing, legacy/direct
ARCA and explicit non-coding no-ops, ordinary durable tasks and instance callers.
Live ARCA, NAS archive consistency and fault-injected destruction still require
staging validation. No test here restarts a production Bot.

## Runtime wire contract v1

The existing helper accepts `start|status OPERATION_ID`. A valid response has
exit code zero and one JSON object in stdout with `version: 1`, the matching
`operation_id`, and status `legacy`, `not_mounted`, `running`, `committed`, or
`failed`. Except for `legacy`, `boot_id` must be a nonempty string. A committed
receipt requires `backup.status: success`, a matching `backup.operation_id`, and
a nonempty `backup.generation_id`. Invalid responses degrade backup, not identity
diagnostics: a changed valid boot identity is logged as a degraded backup, even
when that response arrives after the observation deadline. Restart continues.

If initial inventory is unavailable, verification retains the first later valid
inventory as its comparison baseline, without starting backup on those targets.
A subsequent observed target change ends backup receipt verification but does
not block provider submission. An unavailable
query never erases an already established baseline.

## Policy amendment (2026-10-12)

Backup has no veto over the existing restart. This supersedes the earlier
physical-target/boot-change blocking policy and its review conclusions. All
backup-only failures, including positively observed physical identity changes,
produce warnings and return control to the original restart. Lifecycle binding,
engine/operation ownership, database writes and mutation fences remain outside
this downgrade boundary; cancellation still propagates. This does not guarantee
preservation of current data or prevent a platform-side replacement during the
backup interval. Shared BaaS/DeviceService code is unchanged.
