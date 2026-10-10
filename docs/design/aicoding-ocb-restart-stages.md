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
Published/Caller backup entrypoints and direct-provider restart do not enter
the ordinary coding/BaaS executor. The Caller-specific asynchronous extension
is documented below.

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

## Caller asynchronous restart (dev follow-up)

Caller is **not** an ordinary Bot and does not write the source Bot's PENDING.
Its independent `ac_expert_chat_instance` row uses the existing `init / success /
failed` states and `ext.error.message` error contract. No schema, Repository,
BaaS, frontend or `/status` changes are required.

- Only aicoding/claude_code existing Caller upgrades opt in. First creation,
  successful reuse and other engines retain the old path.
- Admission writes `init` once, before enqueueing `aicoding.caller.restart`.
  The payload contains IDs/engine/time only, never IAM tokens or template secrets.
- `engines/aicoding/caller_restart.py` owns admission, locks, operation phases,
  task execution and errors. It reuses the existing restart-lock table with a
  tenant/owner/Bot/**Caller**-scoped namespaced key. HTTP never waits on this lock:
  a busy lock returns the existing pending result without another upgrade.
- The worker invokes the original `_upgrade_container`: original configuration,
  backup precondition, then upgrade. The aicoding `prepare_restart_async` override validates operation
  ownership/target/deadline and records submission after the backup verifier
  returns. The original upgrade signature/body has no new callback. A typed,
  invocation-local execution context is installed/reset by the coding worker;
  strategy dispatch pins that policy while passing the freshly resolved Bot
  context, so changing the engine cannot bypass the submission checks. BaaS still only manages devices.
- After the new publish ID is persisted, the original polling/identity/connection
  logic resumes. It is serialized with admission, so a stale polling write cannot
  overwrite another operation. Identity exchange uses the deployed publish ID,
  not a newer owner publication. Tokens remain confined to the incoming request.
- A worker also observes deployment failures without a frontend. Backup,
  preparation, submission and deployment failures persist `failed` and the old
  error field. Ordinary polling stops; only an explicit restart can retry.
- A delivered task never repeats an upgrade after entering EXECUTING/SUBMITTING.
  A lost worker is conservatively observed until the business deadline rather
  than automatically replayed. A stale lock is token-fenced and expires after
  the business budget; a late worker must revalidate ownership before submission.
- Backup retains the 1500-second budget. Queue waiting is deducted for Caller;
  business timeout remains 2100 seconds. The timeout is enforced on worker
  delivery/pre-submit checks, not by cancelling an already accepted BaaS job.
- The accepted historical write-before-enqueue crash window is unchanged; no
  completion marker or crash-window recovery was added. Normal write/enqueue
  exceptions are surfaced and persisted as failure.

Shared changes are limited to a default engine-policy dispatch, typed internal
lifecycle ports/dependency passthrough, invocation-scoped strategy dispatch, and DI
registration. The original Caller body is extracted without changing its logic.
`ExpertChatInstanceServiceProtocol` documents the existing response shape's
asynchronous semantics. Published-service and ordinary tasks remain distinct.

Tests cover inline default-engine compatibility, no-backup upgrade, admission
ordering, immediate queue wake, real SQLite concurrent requests, worker replay,
backup/submission/deployment/identity failures, timeout, token exclusion, tenant
context, original identity exchange and DI/lifecycle discovery. Runtime validation
against a deployed environment remains to be performed.

Local validation (2026-10-09): **3007 passed** across bot-management,
service-bot services, expert-chat, Caller/lock repositories, Service API
conformance, protocol ordering, lifecycle discovery and module-boundary tests.
Ruff, the new strategy's local SAST block scan and `git diff --check` passed.

Boundary convergence validation (2026-10-09): **3014 passed** in the same
regression suites. The additional tests cover context cleanup on success/failure,
concurrent default-engine dispatch, engine changes inside the reused upgrade,
the unchanged shared upgrade signature, and real not_mounted parsing/verification
before submission. Ruff, the local blocking flake8 rules, source-size checks and
`git diff --check` passed. No deployed-runtime validation was performed.
