# Coding restart with minimal shared-lifecycle changes

Date: 2026-10-08. Base: OCB 0974a4af1c / Avernet 94bae68a6.
Scope: ordinary aicoding/claude_code restart; published restart and Caller retain
prepare_restart_async. No frontend, schema, status-route or device-callback changes.

## Shared boundary

- BotService.restart_bot_async retains its original implementation location and
  only passes a RestartServices dependency bundle to execute_restart.
- The default strategy consumes that keyword without forwarding it to restart;
  other engines retain their previous behavior.
- DI registers the coding task handler and supplies its dependencies.
- restart_bot, stop/start and the provider restart implementation match baseline.
- No RestartDispatchMixin, restart_lifecycle_snapshot, restart_handoff or status
  projection hooks remain. No concrete coding rules are added to shared flows.
- Repository implementation and Protocol match baseline; no ext_cas.py extraction.

## Admission and scope exceptions

The strategy validates ordinary bound Bot input and joins an existing live task
before creating a new one. Queue dedup uses tenant/owner/bot. Payload freezes the
engine, old binding/device/provider, operation ID, timestamp and expected PENDING status.
Template values use their existing protected storage, not queue payloads.

The original ext-only CAS saves the operation journal and clears old start_*
errors. Synchronous admission writes PENDING before enqueue without replacing ext;
workers initialize/recover the journal but never write initial PENDING. Queue
dedup and task_id fencing remain unchanged. Only admission runs in the request
thread; backup and platform mutation run in a worker.
The original status endpoints read get_bot and existing error fields unchanged.
Browser polling remains 3 seconds / 5 minutes; timeout does not cancel the task.

Unbound Bots retain the original threaded, awaited restart callback: there is no
bound old container to back up, and original FAILED-without-binding provider
recovery must not be bypassed by prematurely setting PENDING. A PENDING binding
with a non-PENDING Bot also retains the original activation-in-progress guard.
These cases do not create this coding restart task. Thus durable async admission
is explicitly scoped to eligible bound ordinary coding Bots, not every request.

## Execution and observation entirely inside aicoding

The worker sets a coding-owned ContextVar. Direct ARCA uses the original
restart_bot stop/start flow. Ordinary BaaS uses AicodingBaasRestart: best-effort
backup, existing restart lock, request preparation, final target/receipt recheck,
then the CAS transition BACKING_UP -> RESTARTING immediately before provider POST.
No lock ownership or TTL behavior is changed. A verifier callable proves that
the precondition was installed, not that a backup was committed.

Before execution, the worker records old provider request/publish IDs. The BaaS
orchestrator persists request ID, workflow baseline and provider recovery work
before submission. After return or an ambiguous post-fence exception, the worker
captures the new provider intent; old intent is not proof of this restart.
Pre-submission failures terminate without claiming backup success. An ambiguous
post-fence response is observed, never retried as a fresh mutation. For stop/start
the worker records the replacement binding or waits for allocation; a failed
stop/start call is not replayed.

WAITING_READY observation requires the matching BaaS publish to succeed and the
existing provider finalization markers/readiness, or readiness of a replacement
allocation binding. The journal does not override the public status endpoint.
The frontend continues to use the original stored-runtime readiness semantics.

## Failure and recovery limits

- Amendment (2026-10-11): backup is best effort. Unavailable/invalid inventory,
  backup command failures, failed receipts and timeouts log sanitized warnings
  and continue the original restart. These failures alone do not mark the Bot
  FAILED.
- Amendment (2026-10-12): backup-observed physical target/boot changes only warn
  and continue restart. Lifecycle binding/engine/operation changes still block. Database,
  durable mutation-fence and worker-level failures retain their original error
  handling, including FAILED journal/start status where applicable.
- Amendment (2026-10-19): direct ARCA bindings now participate in the same helper
  protocol; BaaS-managed containers retain BaaS inventory and command transport.
  Arca target checks, recovery-state eligibility and blocking errors live in
  `engines/aicoding/restart_arca.py`; the device router supplies only fixed-target
  command transport, without coding-specific backup policy. Changes to the
  captured Arca binding/sandbox remain blocking, unlike backup receipt failures.
- Restart may destroy the old container without a confirmed backup, losing data
  not previously persisted. A backend timeout does not cancel the historical
  helper. If replacement fails, previously stopped workloads are not guaranteed
  to recover; continued availability is not promised. No script upgrade,
  frontend or /status change is required for this policy.
- Target checks reject already-observed engine/binding changes. No lifecycle-field
  database CAS exists in the restored Repository interface.
- Ext and status use separate writes. Exceptions propagate; repeat admission or
  worker ensure can repair incomplete FAILED status and missing journals. Failure
  before a task is accepted leaves no new journal. Enqueue errors propagate
  without rolling back PENDING; a later request can retry, although the Bot can
  remain PENDING until then. Repair after permanent queue termination is not
  guaranteed.
- Re-reading operation/target before status update does not eliminate races with
  other writers between the read and UPDATE. No unconditional full-ext rewrite
  is used because it could erase the CAS-owned mutation fence.
- Process loss after RESTARTING but before strategy observation cannot reliably
  correlate completion. Reclaimed deliveries wait; they never rerun destruction
  or creation. Business timeout (35 minutes) reports failure/manual inspection. A
  live worker may still persist observation; existing BaaS recovery remains active.
- The queue deadline is 24 hours. Ordinary restart shares one 1500-second backup
  budget across queue delay, redeliveries and physical targets, followed by a
  600-second provider observation allowance. Published/Caller paths retain their
  existing per-target backup budget.
- If the queue terminates without handler cleanup, /status does not synthesize an
  error from queue state; frontend timeout and operational inspection remain needed.
- Existing runtime callbacks are unchanged. This change does not claim to isolate
  historical startup reports or every concurrent external lifecycle operation.

## Deployment and validation

Register the new handler on every claiming worker before relying on its task type.
Drain/reconcile outstanding jobs before rollback; never clear a mutation fence and
blindly rerun a potentially completed platform operation.

Tests cover bound admission, unchanged other-engine behavior, unbound fallback,
activation guard, SQLite queue dedup/ext CAS/status writes, partial-write recovery,
backup failures, real BotService BaaS/ARCA paths with fake platforms, provider
observation, old-intent rejection, lost responses and process-loss non-replay.
Published/Caller, endpoints, repository and architecture regressions also run.
No production containers or preproduction/browser E2E have been exercised.
