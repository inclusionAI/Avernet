# Bot run completion and timeout callbacks

## Scope and ownership

The bot-run Worker owns queue dispatch, execution watchers and completion notification.
The executor chain (including `ResultGuardExecutor`) owns persistence of the result in
`baas_bot_run`; the queue repository owns `baas_bot_run_queue`. A cancellation request
is not evidence that the result has reached a terminal status.

This describes the community implementation with the timeout callback race fix.
Source and tests at the target revision are authoritative; this document does not
establish which revision is deployed.

## Ordered path

1. Claim a queue entry subject to the bot's global concurrency cap. The historical
   QPM configuration controls concurrent RUNNING entries, not requests per minute.
   The deadline remains queue creation time plus `meta.timeout`, including queue wait.
2. Execute with a guardian for heartbeat/deadline and an abort-signal poller. A local
   deadline only requests task cancellation; it does not mark the queue DONE or notify.
3. The executor persists COMPLETED, FAILED, TIME_OUT or ABORTED. `ResultGuardExecutor`
   handles cancellation and preserves a result that has already reached a terminal state.
4. On executor exit, remove the local cancellation handle, cancel and await both
   watchers **before** finalization or callback. A slow callback must not be cancelled
   by the execution deadline.
5. Check that the result is terminal, attempt owner-fenced queue completion, then
   invoke the configured callback from the Worker's common success/exception finalizer.
   The HTTP callback independently refuses PENDING/RUNNING results.

## Failure and recovery boundaries

- A session-busy requeue returns the entry to PENDING without completion or notification.
- If result persistence fails or the result remains nonterminal, local finalization
  leaves the queue recoverable and sends no callback. Persistence exceptions propagate.
- The global timeout scan still handles expired PENDING entries and RUNNING entries
  with stale owner heartbeats. It must persist a terminal result before ending the
  queue entry and notifying; fresh-heartbeat RUNNING entries remain the owner's job.
- Queue completion and HTTP delivery are not one transaction. Queue writes and callbacks
  retain their existing best-effort handling. HTTP retries, process loss, stale-owner
  races and failed queue writes are **not** distributed exactly-once delivery; receivers
  must remain idempotent by `run_id`. This fix removes the competing local guardian
  callback, not every possible duplicate delivery mechanism.

## Contracts and evidence

- [Internal executor/callback protocols](../../src/secbaas/community/core/service/bot_run/_internal_protocols.py)
- [Worker and timeout scan](../../src/secbaas/community/core/service/bot_run/_worker.py)
- [Result guard](../../src/secbaas/community/core/service/bot_run/_executor.py)
- [HTTP callback](../../src/secbaas/community/core/service/callback/_http_callback.py)
- [Deterministic race and failure regressions](../../tests/unit/core/service/bot_run/test_worker_timeout_callback.py)
- [Timeout and recovery tests](../../tests/unit/core/service/bot_run/test_worker_timeout.py)
- [Abort tests](../../tests/unit/core/service/bot_run/test_worker_abort.py)
