# Embedded execution deadlines and owned flow timeout hotfix

## Problem and scope

An embedded runtime call can remain pending beyond `timeoutSeconds`. Passing a
native timeout or AbortSignal does not establish a caller-owned completion
bound. Separately, a reaper that uses its current configuration to fail shared
running rows can apply a different timeout than the one selected at launch.

This patch bounds embedded attempts, propagates per-invocation cancellation,
and restricts timeout cleanup to known in-process executions. It does not
identify the original runtime hang trigger, implement distributed execution
leases, deploy a new runtime, or repair historical production records.

## Contracts

### Controller to executor (Plugin API)

The optional fifth argument to `executeNode` is an `ExecutorDispatchContext`.
Its `abortSignal` is the execution's current flow signal. The OpenClaw dispatch
must prefer it over the signal captured when the dispatch was constructed,
including embedded fallbacks. Existing four-argument consumers remain valid.
A caller's cancellation must not cancel sibling flows sharing a session.
Already-dispatched notifications, tool requests and asynchronous callback side
effects cannot be recalled by invalidating subsequent callbacks.

A fanout child controller may propagate its cancellation reason before it can
publish a terminal result. The fanout runtime must reconcile those cancellation
errors through durable observation: read an existing terminal outcome first,
otherwise report the established unknown-outcome error. A cancelled signal is
not a child-termination acknowledgement. Ordinary execution, persistence and CAS
errors must still propagate rather than being reclassified solely because a
signal was also cancelled.

`ExecutorResult.retryable === false` forbids automatic node retry. Omission
preserves the existing retry policy. The controller must also not retry after
its flow signal is aborted. This is a safety constraint, not a new retry knob.

### Embedded attempt lifecycle

1. Establish a deadline at executor entry using the existing `timeoutSeconds`
   value (default 300 seconds), including preparation and semaphore waiting.
2. Use an attempt-local AbortController linked to the parent signal.
3. At deadline or parent cancellation, invalidate outward progress, agent-event
   and final-output callbacks before requesting native cancellation.
4. Allow up to five seconds for the underlying operation to settle. The caller
   returns failure even if the underlying promise never settles.
5. Include already-started asynchronous guarded callbacks in settlement; an
   unawaited callback is still live work. If cancellation is unconfirmed, return
   `retryable: false`; parent cancellation
   is always non-retryable. A timed-out operation that has actually settled can
   use the existing failure retry policy. Settlement is not a guarantee of
   external side-effect rollback or business idempotency. A native success also
   waits for already-started guarded callbacks under the original deadline.
6. Discard late results/callbacks; consume late rejections. Remove deadline
   timers and parent listeners on exit. JavaScript timers do not preempt a
   blocked event loop or kill external processes.

The LLM semaphore removes cancelled waiters and must not launch their requests
later. A long-held permit warning must not release capacity while the original
operation is still alive. An unresponsive operation can retain a permit;
operator inspection is safer than silently exceeding the concurrency limit.

### Owned flow cleanup (Service API)

Initial asynchronous startup uses the same registered launch path as continuation
and retains deferred stack detachment to avoid agent-runtime re-entry.
Timeout candidates must come from known local execution handles with a trusted
launch policy. Reaper configuration or a newly deployed workflow must not
reinterpret an existing execution's deadline. An explicit zero disables the
flow timeout. Concurrent sweeps must not duplicate timeout handling. Reads and completion
writes must use the execution's original repository, even when global bindings
change while a CAS is pending. A failed status read or an unconfirmed CAS must
not publish a terminal completion or notification.

Runs without a trusted local handle or original timeout policy are not presumed
to be abandoned. They are not automatically failed by this watchdog. This is a
conservative compatibility change: historical/restarted runs can require manual
verification. In-process ownership is not a distributed lease or fencing token.
The patch does not secure independent old reapers or unrelated cleanup actors.

## Validation

Behavioral tests cover never-settling execution, cooperative cancellation,
late results/events, cancelled semaphore waiters, per-invocation signal
precedence, initial asynchronous launch, frozen timeout selection, unknown
ownership, non-running records and repeated sweeps. Existing architecture and
callback tests must continue passing. Full production acceptance requires the
actual deployed build, control-plane routing and old reaper actors to be checked.

## Rollout and rollback

- Verify the deployed TaskGuard/OpenClaw build and the target API environment.
- Canary all actors capable of timeout cleanup; a patched process cannot fence
  an unpatched process that still writes completion records.
- Observe timeout reason, cancellation confirmation, queued/active LLM permits,
  known-policy skips and running backlog before expanding rollout.
- Do not retry an affected business run until its earlier execution has stopped
  and its side effects have been checked. Preserve original failure evidence.
- Reverting this patch restores the old timeout/ownership risks; rollback alone
  is not evidence of safe business execution.

## Existing source-size debt

The touched controller, index, types and embedded executor already exceed
the repository's 1,000-line source limit on the target baseline. New helpers
and tests are kept below the limit. This document does not approve an exception or modify a CI
allowlist; maintainer review is still required for the existing violation. A full
decomposition is deliberately not mixed into an incident patch or represented
as completed; this existing gate/debt requires a
separate owned cleanup before a strict file-size gate can be claimed to pass.
