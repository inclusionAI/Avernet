# Personal DWS hosting

Durable state plus explicitly constructed authorization/intake services. This
module registers no routes, startup tasks or engine capabilities. Importing it
cannot start DWS or send a message. Explicit composition is available in
`di/dws_hosting.py`; phase-7 owner management and opt-in startup are assembled
outside core in `di/dws_management.py` and `di/dws_lifecycle.py`.

## Context Boundary

```yaml
purpose: Personal-message hosting orchestration boundary and state conformance tests.
provides:
  - HostingAuthorization
  - DwsIntake
  - HostingExecutor
  - HostingDeliveryWorker
  - HostingDeliveryRouter
  - HostingRuntimeService
  - HostingRuntime
  - HostingManagementService
consumes:
  - engine.community.plugin_api.dws_hosting.HostingStateStore
  - engine.community.plugin_api.dws_connection.DwsConnection
  - engine.community.plugin_api.dws_connection.HostingRecoveryStore
  - engine.community.plugin_api.dws_execution.HostingEngine
  - engine.community.plugin_api.dws_execution.HostingExecutionStore
  - engine.community.plugin_api.dws_delivery.HostingOutbox
  - engine.community.plugin_api.dws_delivery.HostingSender
  - engine.community.plugin_api.dws_runtime.HostingRuntimeStore
  - engine.community.plugin_api.dws_management.HostingManagementStore
internal_dependencies:
  - engine.community.kernel
  - engine.community.plugin_api.dws_hosting
  - engine.community.plugin_api.dws_connection
  - engine.community.plugin_api.dws_execution
  - engine.community.plugin_api.dws_delivery
  - engine.community.plugin_api.dws_runtime
  - engine.community.plugin_api.dws_management
  - engine.community.core.dws_hosting
```

### Change impact

The additive `HostingStateStore` Plugin API is implemented by
`plugins/dws_hosting/state.py`. Shared immutable values live in
`kernel/dws_hosting.py`, not in core. Tests may instantiate the concrete SQLite
implementation; orchestration depends only on the Protocols. No existing
chat, session, BCN or engine plugin behavior changes.

## Contract

- The caller supplies a trusted binding identity, normalized direct text, and
  UTC epoch milliseconds. This is not an authenticated HTTP API. Event account,
  private-chat scope, owner authorization and time-of-day schedules belong to
  the caller and must be enforced before production wiring.
- A new binding is disabled. Confirming a nonempty rule is necessary, but not
  sufficient for product activation. Owner checks and account verification are
  implemented in the library; authenticated personal-Bot routing and consent UI
  remain pending. Rebinding the same ID to a different identity fails.
- Inbox insertion and batch assignment commit together. Deduplication uses
  `(binding_id, conversation_id, message_id)`. Duplicate delivery cannot change
  content or extend the batch deadline. Opaque IDs are case-sensitive.
- A conversation has a persistent random session ID. Bindings carry Bot,
  environment, organization, account and agent identity. There is no global
  restriction preventing the same account from authorizing multiple Bots.
- Defaults: 4 seconds quiet, 15 seconds maximum from first local receipt, at
  most 20 messages or 16,000 characters per batch. Policy is constructor-injected
  and must remain fixed while batches are pending. Deadlines survive restart.
- Messages from the account itself, outside the active interval, in the future,
  or over the configured single-message character bound are persisted but not
  queued. The exact activation millisecond is excluded conservatively. A future
  coordinator must surface unsuitable input; this slice does not notify users.
- Claiming freezes the latest confirmed rule and sorted messages in one
  transaction. Later rule changes affect the next claim, not an in-flight turn.
- Each conversation allows one `running` or `unknown` turn; other conversations
  remain independent. Restarts never automatically repeat unknown executions.
  `completed` means execution is confirmed finished, **not** reply delivered;
  it releases the execution slot. Unknown executions are inspected every 30 seconds;
  only an exact run ID with confirmed termination releases the slot to manual handling.
  Missing/expired evidence stays unknown; inspection never reruns or resends a turn.
- Disable/takeover increments an epoch and cancels unclaimed batches. Revoked
  unsent executions are detached, retaining their audit record; only unresolved
  affected DWS sessions rotate, so old results cannot contaminate resumed work.
  Already-started uncertain sends retain their barrier. Re-enable
  cannot revive those batches or authorize old in-flight replies. In-flight work
  continues blocking the conversation until termination is confirmed. `is_current`
  is a diagnostic gate, **not** an atomic send lease. Delivery uses the separate
  transactional outbox gate described below.

## Conformance Tests

`tests/test_state_contract.py` exercises real SQLite, including concurrency,
transaction rollback, deduplication, batching boundaries, restart recovery,
rule snapshots, isolation and incompatible storage rejection. Every alternative
state implementation must satisfy these behavioral tests.

`test_cli_contract.py` uses real subprocesses with an offline DWS fixture;
`test_connection_wire.py` pins v1.0.62 projections. `test_intake.py` exercises
authorization, supervision and failure behavior through ports with real SQLite.
`test_recovery_contract.py` covers schema migration, restart and stale-attempt fencing.

## Connection and Recovery

- Device authorization returns a short-lived challenge only to the trusted
  owner. Account/organization must match the provisioned binding. It does not
  enable hosting. DWS alone stores/refreshes tokens in its private config directory.
- Only `user_im_message_receive_o2o_all` is subscribed. Notifications persist
  conversation/message IDs as hints, not untyped text. Complete history supplies
  canonical text; owner and AI-originated messages cannot trigger reply loops.
- Startup, consumer rotation/restart and periodic polling reconcile history.
  DWS handles its own socket reconnect; Adapter also checks bus generation and
  dropped-message counters before/after reconciliation.
- Fixed windows (default 5 minutes), 30-second overlap, durable message-ID
  deduplication and known-conversation rescans supplement recent-DM discovery.
  Old hints rewind the scan beyond the overlap. Hints remain until their exact
  IDs appear in history, including unsupported message types.
- Pending hints block only their own conversation; targeted live hydration runs
  independently of periodic discovery. Startup/incomplete recovery blocks all
  turn claims. Partial pages,
  disconnects, identity changes and stale attempts cannot release the barrier.
  Transient failures back off; invalid output/storage faults stop safely.
- Transient history/discovery failures keep the event consumer alive while
  reconciliation retries with bounded backoff. The first failure fences claims
  immediately and the failed interval is never skipped. Actual connection
  loss still rotates the consumer and requires reconciliation again.
- Only bounded, complete history advances a cursor. This is not an exactly-once
  delivery guarantee: history retention/visibility must be qualified. Permanently
  unavailable hinted messages and oversized windows currently stay blocked and
  require operational handling, not silent skipping.

The opt-in runtime starts intake and its recovery barrier before any turn
worker. A database-wide process lease spans all workers and cancellation joins
pending SQLite calls before releasing ownership. Local locking is not deployment fencing for two
containers with copied state. Engine execution is explicit, not started by intake;
delivery is explicit too, not started by intake or ordinary chat.

Rollout qualification distinguishes an explicit denial from `RolloutUnavailable`.
Transport errors, unavailable services and malformed responses pause new work and
sends without changing owner consent. A failed check does not extend the 30-second
positive cache; retries are throttled for 5 seconds. Recovery resumes only after a
fresh positive check. Explicit denial still disables hosting durably, and neither
recovery nor a later grant can undo a manual disable. This contract affects only
DWS runtime eligibility; management-command verification still fails closed.

## Execution and Results

- A trusted worker passes a claimed turn to `HostingExecutor`. Its immutable
  rule snapshot goes in `extraSystemPrompt`; normalized current-batch facts go
  in `message`. The dedicated `agent:<agent>:dws:<session>` key is stable per
  bound conversation and distinct from interactive chat. No fabricated delivery
  receipts, relationship information or copied chat history are added.
- SQLite reserves a stable run/RPC ID before network I/O. A duplicate worker
  cannot dispatch it again. Intake barriers, disable/takeover and binding epochs
  are checked before reservation; controls are checked again before resolving.
  Rule edits do not invalidate the frozen rule version.
- The OpenClaw plugin uses a dedicated signed connection and `agent` with
  `deliver=false`. It waits past `accepted` for the same RPC/run's terminal
  `result.payloads`; cached completed results are valid. It never uses a chat
  event, streaming text or the last history message as this turn's final result.
- Raw results commit before parsing. Restart can finish parsing a saved result.
  Disconnect, timeout or `in_flight` stays unknown and blocks that conversation;
  `agent.wait` is status-only and cannot recover reply text. A wait timeout without
  `endedAt` is not completion. Even after the gateway loses its in-memory cache,
  this worker never reruns the original agent operation.
- Only a single full JSON decision is accepted: strict version, rule version,
  action and action-specific fields, with no model-controlled recipients. Whole
  code fences and empty payloads are tolerated; malformed/progress prose, multiple
  decisions, media and error output are not guessed or silently discarded.
- Confirmed-ended failures become durable manual records; this does **not** mean
  an owner notification was sent. Unknown runs remain blocked. `ignore` releases
  the slot; `reply`/`escalate` stay blocked awaiting the durable outbox.
- The optional `HostingFormatRepair` port handles exactly one plain-text candidate.
  Deployment wires OpenClaw `agent` with `modelRun=true`, `promptMode=none`,
  `deliver=false` and a separate deterministic formatting session/run. Tools are
  disabled by the engine. One bounded inference rechecks the original rules/facts;
  any reply must preserve the candidate text exactly. Schema/version/scope checks
  still apply. Unsupported gateways, errors, ambiguity and timeouts stay manual.
  The original tool-enabled turn is never rerun. Formatting sessions can persist;
  automatic temporary-session cleanup is not claimed.
- Every `reply`, including a recovered decision, must pass `HostingEngine.review_reply`
  (a single payload may contain up to eight identical complete JSON objects;
  exact duplicates are reduced to one, while conflicts or trailing prose fail).
  before it is resolved. This separate, tool-free raw-model call sees the frozen
  rules, current input, candidate and the frozen real-dialogue snapshot when present.
  Legacy executions without snapshots retain the four-exchange fallback.
  It excludes tools, unconfirmed sends, owner notices and other conversations;
  history is untrusted context, never authority. No persistent agent history is read.
  It checks relevance to the current batch; a greeting cannot justify an unrelated
  task. Input deduplicates current-batch messages by message ID, not text; no
  dialogue-count reduction or model change. Review returns `verdict`, an enumerated
  `reason_code`, and a single-line `reason` (1..240 characters, no private message
  quotes, credentials or reasoning trace). The validated diagnosis is persisted
  atomically with the outcome in private SQLite `executions.review_json`; it is
  excluded from execution serialization, management projections and owner notices.
  `approve` keeps the candidate unchanged; `skip_owner_replied` resolves to ignore
  only with a later complete owner message as supporting evidence. No reply or
  owner notice is queued for that skip. `reject` with `outside_rule` also resolves
  to ignore: a confirmed scope exclusion is not a processing failure. Identity or
  relationship uncertainty is never treated as scope exclusion. Other rejections, uncertainty,
  invalid output, unsupported mode or a 60-second timeout becomes manual.
  No original agent replay or replacement reply is attempted. This adds one model
  call and may conservatively reject context-dependent replies; it reduces risk,
  not a guarantee of semantic correctness. Review sessions may persist.

- Model-generated `escalate` decisions also pass this isolated scope review before
  creating an owner notice. In-scope requests for missing personal facts may
  escalate; out-of-scope requests must not. `ignore` needs no further model call.
  This retains natural-language rule interpretation, not a regex/person whitelist
  or a new pre-generation rule-router. Generation may still produce an out-of-scope
  candidate; the independent review can veto both reply and escalation.
- Legacy verdict-only responses remain accepted with `reason_code=unspecified`;
  a legacy reject still requires manual handling, never an invented scope reason.
  Existing historical records retain a null review. Schema v7 adds the nullable
  review column without modifying settings, enabled state, rule versions or outbox.
  Older workers reject v7; restore a pre-upgrade database backup before downgrade.

### Real dialogue and incremental session sync

- The opt-in DWS executor reads canonical history for this account/conversation,
  bounded to 24 hours and 40 messages, 2000 characters each (truncation marked).
  Owner manual replies are context only; future peer batches are not answered early.
  History read/auth/scope failures become manual handling, never blind generation.
- Confirmed peer deliveries from the local outbox supplement canonical history;
  AI-client messages remain excluded by the intake parser to prevent reply loops.
  A different hosting Bot's AI replies are not currently imported as context.
- The dispatch transaction freezes the snapshot, delta and attempted sync keys
  inside `invocation_json`, with no backend or database schema migration. Each
  session receives unseen messages only, plus changed delivery statuses linked
  to original incoming IDs. Its own earlier candidate body is not injected again.
- Sync keys mean attempted dispatch, not proven consumption or delivery. Unknown
  executions are never retried; they keep the existing end-confirmation barrier.
  On restart/resume the frozen invocation is reused, not refreshed from live history.
  Facts whose consumption is uncertain remain available in the independent review
  snapshot; no automatic reset/replay of the long-lived agent session is attempted.
- Generation uses the delta; review uses the frozen snapshot. Latest rules remain
  authoritative. Disable/re-enable changes sending permission, not the reality of
  prior dialogue. History is not enqueued and cannot revive old incoming work.
- The extra bounded history read currently happens per turn. Latency optimization
  is separate; no cache may bypass account validation or hide missing history.

`test_execution.py` covers SQLite execution conformance and crash/control races;
`test_hosting_gateway.py` uses an offline real WebSocket server for signed
handshake, ACK/final correlation, cancellation and transport faults.
`test_reply_review.py` covers reviewer veto, unchanged-text approval, isolation,
failure/cancellation and disabling hosting during review.

Using the existing agent preserves skills/MCP. Neither `deliver=false` nor prompt
instructions prevent tools from sending messages or causing other side effects.
This is not a tool-permission sandbox, and production activation must explicitly
accept/manage that risk. The hosting worker never resets, aborts or rewrites an
interactive session; it does not modify the shared client or other engines.

## Delivery and Compensation

- Enqueue from a persisted decision, never arbitrary model-selected recipients.
  One immutable delivery per turn stores the body and stable idempotency key.
  Peer replies have no added body prefix and retain DWS's native AI badge.
  Replies target the source conversation;
  owner notices require a separate trusted owner-notification sender.
- `HostingDeliveryWorker.run_once` leases one send or receipt query. The store
  checks enabled/takeover epochs and intake recovery before claiming and before
  committing `sending`. It intentionally does not check the latest rule version.
  Revocation after `sending` is in-flight; no remote cancellation is promised.
- Authorization preflight is read-only. Transient preflight failure gets bounded
  persisted backoff; auth rejection/exhaustion becomes a manual record. Once a
  write starts, timeout, cancellation, malformed output or process failure is
  **unknown**, not proof of failed delivery and never a trigger for resend.
- A queryable remote task ID triggers bounded, read-only status queries. Only a confirmed
  message reference with the correct task/conversation releases the execution
  slot. Unconfirmed outcomes remain blocked; no history-last-message guessing.
  Known failures become durable manual records and release the slot.
- Expired read/preflight leases can be reclaimed; expired writes cannot. Claim
  tokens fence old workers. Storage errors propagate; an unpersisted receipt
  cannot cause automatic resend. This is not an exactly-once remote guarantee.
- The DWS sender supports replies only and forces the native AI tag.
  `HostingDeliveryRouter` delegates owner notices to a separately injected sender;
  the internal implementation uses the owner's configured notification MCP.
  There is no automatic fallback to the contact or to DWS self-messaging.
- An owner-notice sender with no receipt-query capability may explicitly return
  `accepted` with a task ID and `queryable=False`. This persists terminal
  `accepted`, releases the conversation and stops polling/retries, without claiming
  delivery. The same receipt for a peer reply remains unknown and blocked.
  Uncertain MCP writes also remain unknown; absence of a query is not permission
  to resend. Global runtime registration remains pending; owner acknowledgement
  while the runtime is stopped can hand unresolved work to manual takeover.

`test_delivery.py` covers real SQLite workers, concurrency, restart, control
races and storage faults. `test_sender.py` uses real offline CLI subprocesses
for argv, AI markers, accepted/confirmed receipts, deadlines and cleanup. Live
delivery qualification remains pending. `test_runtime.py` and `test_runtime_store.py`
cover restart barriers, durable operator acknowledgements, capacity/retention,
process ownership and fault propagation. `test_formatting.py` verifies lossless
delimiter repair, refusing missing fields, duplicate decisions or changed versions.

See `engine/adapter/specs/dws-personal-hosting.md` for implementation progress.

### Review and intake diagnostics

Verbose single-line review explanations are truncated to 240 characters at the
model-result boundary. Verdict, reason code, extra fields, control characters and
JSON ambiguity remain strictly validated; truncation never upgrades a rejection.
Unavailable reviews log only the failure stage, not model content or exceptions.
Intake logs distinguish live events, live history reads and periodic history scans
using a hashed message reference and receipt delay, without names or message text.
Periodic scans record actual ingestion time, not scan start. The 30-second fallback
scan is unchanged; missing live events can still incur that polling delay.
