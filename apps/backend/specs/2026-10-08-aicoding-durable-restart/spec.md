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
engine, old binding/device/provider, operation ID, timestamp and original status.
Template values use their existing protected storage, not queue payloads.

The original ext-only CAS saves the operation journal and clears old start_*
errors. Existing update_by_owner writes PENDING without replacing ext. Only
admission runs in the request thread; backup and platform mutation run in a worker.
The original status endpoints read get_bot and existing error fields unchanged.
Browser polling remains 3 seconds / 5 minutes; timeout does not cancel the task.

Unbound Bots retain the original threaded, awaited restart callback: there is no
bound old container to back up, and original FAILED-without-binding provider
recovery must not be bypassed by prematurely setting PENDING. A PENDING binding
with a non-PENDING Bot also retains the original activation-in-progress guard.
These cases do not create this coding restart task. Thus durable async admission
is explicitly scoped to eligible bound ordinary coding Bots, not every request.

## Execution and observation entirely inside aicoding

The worker sets a coding-owned ContextVar and invokes the unchanged restart_bot.
The already-existing prepare_restart hook uses a stable backup operation ID and
runs receipt/identity checks before a CAS transition BACKING_UP -> RESTARTING
under the existing restart lock. No lock ownership or TTL behavior is changed.

Before calling restart_bot, the worker records old provider request/publish IDs.
After the call returns, or after a caught ambiguous BaaS exception, it reads the
existing binding props to capture new provider intent. It does not inject calls
into the provider mutation. The original BaaS implementation already persists
request ID, workflow baseline and its own recovery task before platform mutation.
Only changed intent is adopted; an old request/publish is not proof of this restart.
For successful stop/start returns the strategy records the replacement binding
(or waits for its allocation); a failed stop/start call is not replayed.

WAITING_READY observation requires the matching BaaS publish to succeed and the
existing provider finalization markers/readiness, or readiness of a replacement
allocation binding. The journal does not override the public status endpoint.
The frontend continues to use the original stored-runtime readiness semantics.

## Failure and recovery limits

- Backup failure saves phase=FAILED, ext.start_status=FAILED and a sanitized
  ext.start_message through original ext CAS; status-only update_by_owner writes
  Bot.status=FAILED. The old container is not destroyed by this restart, but its
  workload may have stopped during backup; continued availability is not promised.
- Target checks reject already-observed engine/binding changes. No lifecycle-field
  database CAS exists in the restored Repository interface.
- Ext and status use separate writes. Exceptions propagate; repeat admission or
  worker ensure can repair incomplete status. A crash can leave inconsistency,
  and repair after permanent queue termination is not guaranteed.
- Re-reading operation/target before status update does not eliminate races with
  other writers between the read and UPDATE. No unconditional full-ext rewrite
  is used because it could erase the CAS-owned mutation fence.
- Process loss after RESTARTING but before strategy observation cannot reliably
  correlate completion. Reclaimed deliveries wait; they never rerun destruction
  or creation. Business timeout (2 hours) reports failure/manual inspection. A
  live worker may still persist observation; existing BaaS recovery remains active.
- The queue deadline is 24 hours. Each target backup budget remains 1500 seconds.
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
