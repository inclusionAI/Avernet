# Ordinary coding restart: strategy-owned execution

Base: private OCB `REL20261009` / `39357e3e9b`, Avernet
`4b57661dcbb85c78a2892326c6ddc934dee3ac7a`.

## Problem

The coding worker marked mutation as started immediately after backup, before
local submission preparation. A preparation failure therefore entered ambiguous
submission recovery and waited even though no remote request was made. Admission
and the reused old restart function also both wrote PENDING. The exact database
reason for the observed update returning None has not been established.

## Ownership / shared-path boundary

All newly introduced restart policy lives in `engines/aicoding/`:

- `restart_task.py`: synchronous admission/preflight, durable dispatch, failure
  persistence and observation; only coding/BaaS takes the separate executor.
- `restart_request.py`: coding-owned configuration/request preparation, reusing
  existing template, BCN, image policy and BaaS payload-building helpers.
- `restart_baas.py`: backup -> short restart lock -> prepare -> durable provider
  polling intent -> final receipt verification -> submit -> persist publish id;
  also owns the final fence and explicit-rejection classification.
- Existing `restart_backup.py` and `restart_state.py`: runtime backup protocol,
  generation/identity verification, timing, operation journal and public errors.

There are no added hooks in the shared restart function or BaaS client. Compared
with the base, BotService has only `lifecycle=self` added to the existing strategy
collaborators. RestartServices carries that instance instead of a new preflight
callback. Default strategies ignore it and still invoke the original restart.
`engines/provisioning.py`, the OCB BaaS client and its API protocol are restored to
the base. The shared `restart_preparation.py` extraction is removed.

The coding adapter reads the injected collaborators; it never monkey-patches a
shared object, intercepts repository calls, or installs per-request callbacks in
the BaaS client. Request preparation is deliberately separate from the old
restart wrapper. Its private helper dependencies are centralized in this adapter
and covered by payload-parity tests to detect future shared-helper drift.

BaaS server, frontend, `/status`, schema and Repository APIs are unchanged.
Published/Caller backup entrypoints and direct-provider restart remain on their
existing paths; they do not enter the new ordinary coding/BaaS executor.

## Execution

1. The coding strategy validates admission, saves requested template changes and
   runs its preflight synchronously. This builds the complete request but does
   not submit it, start backup, enqueue provider polling or change lifecycle
   status. An existing provider intent joins existing work rather than creating
   another coding task.
2. Synchronous admission claims the operation journal with the existing ext CAS,
   writes Bot PENDING, and only then enqueues the coding task. The worker never
   initializes the journal or PENDING. An immediate wake during enqueue cannot
   race the HTTP status write, nor can HTTP reset a fast worker's later status.
   Concurrent requests join an already-enqueued operation; while its submitter
   is still initializing, they report submission in progress without claiming
   that a task has been accepted. A terminal queue row can be explicitly retried.
   Initialization/enqueue exceptions propagate and finalize the owned operation
   as FAILED using existing startup error fields. There is deliberately no new
   process-crash recovery for the PENDING-before-enqueue window and no new ready
   marker or queue protocol. New journals correlate by operation_id rather than
   task_id, which is not available before enqueue. Existing journal formats remain
   readable; workers only repair an incomplete FAILED status write, never PENDING.
3. The worker verifies operation/target ownership and runs backup, outside the
   short restart lock. One 1500-second budget includes queue time and redelivery.
4. With the original restart lock, it verifies the receipt and rebuilds the
   current request. Credentials/configuration payloads remain process-local;
   they are never copied into the persistent task queue. Service-draft default
   image selection continues to use the existing helper.
5. It persists the original provider poll task and binding intent. It does NOT
   write Bot PENDING again. It verifies the binding/physical target and backup
   receipt, fences mutation, then directly calls the existing public
   `post_bots_api` with the prepared update payload. No call to shared
   `restart_bot`, `_restart_bot_baas` or `upgrade_bot` is needed on this path.
6. A known pre-submission failure terminates the coding task; explicit HTTP
   400/401/403/404/422 rejection clears its own provider intent and records a
   sanitized failure. Connection loss, timeout, HTTP 409 and server errors after
   fencing remain ambiguous and are observed, never automatically reissued.
7. The existing provider poller and coding observer finish the operation.

Failure uses Bot FAILED and ext.start_status/start_message. Other engines retain
ALL original writes and rollback behavior, including an already-PENDING Bot.
A missing helper is accepted only for confirmed legacy absence without upgrade
residue; helper-confirmed not_mounted skips backup. Probe failure never means
unmounted. Noncoding engines do not enter the probe or task.

The coding observer budget is 2100 seconds (backup 1500 + provider observation
600). Queue retention is 86400 seconds, not a business deadline. Exec transport
calls retain their own timeout: the budget prevents authorization after expiry;
it cannot forcibly kill an in-flight exec or runtime worker. Old in-flight task
handlers remain registered; no online state is rewritten by deployment.

## Validation and limits

Tests cover task/SQLite persistence, exactly one PENDING write, synchronous
configuration errors, backup and post-backup failures, explicit HTTP rejection,
ambiguous results, stale target/receipt, no shared lifecycle/client invocation,
request-builder parity and unchanged other-engine writes/rollback. Published,
Caller and service API/protocol-ordering regressions are included.

No live deployment, online Bot recovery, or full Singlebox E2E is performed.
The existing main BotService exceeds the source-size guideline; this patch only
passes a collaborator and does not refactor that unrelated large file. New
strategy modules remain under 1000 lines and no CI gate is weakened.
