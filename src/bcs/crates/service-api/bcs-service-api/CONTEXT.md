# bcs-service-api Context

## Provides

MessageFlowService exposes a Human-scoped latest-queued cancellation command for
IM adapters. It selects only the caller's newest canonical IM source in the
requested Session and cancels only still-unsent Send deliveries; active work
remains the separate scoped `chat.abort` contract.

BotDeliveryResult distinguishes a complete downstream rejection
(`delivered=false`) from an uncertain transport error (`Err`). A rejection is
terminal and is never retryable by itself; safe retry still requires the
explicit DeliveryNotSent contract. HTTP Provider non-success responses and
decoded `ok=false` acknowledgements use the rejection result, while missing or
incomplete responses remain errors for Unknown handling.

TaskDispatchOutcome/TaskMessageOutcome may return `queued`: the canonical source
and target delivery have committed, but no Bot delivery result exists yet.
Managed tasks require an explicit canonical running Session. Task completion
includes durable queued/uncertain task work; an active Manager result may complete
its own Session, while other queued return legs still block closure. Worker final,
error and abort settle the assignment and admit a separate Manager result together.
No new Plugin API or database schema is required. Task lifecycle event projections
are not part of this atomic guarantee; no durable notification outbox is added.

SystemMessageQueueService atomically admits one producer event before any direct
delivery; admitted recipients expose delivery_id separately from delivered.
Group/Session initialization can return queued instead of running. The managed
and repository admit_batch contracts commit one Session's canonical sources and
targets together. Required initialization survives ordinary count/TTL limits,
and a scoped non-expired context lookup authorizes a carrier after policy disable.
Consumers are the System dispatcher, session/group launch and shared queue runtime;
Memory/SQLite conformance covers persistence, and queued System tests cover the
dispatcher-to-Send boundary without native inject.

- Terminal Run cleanup has an independent bounded Run-ID page. Each write
  rechecks terminal state, retires unfinished dispatch/Chat work and clears
  Node phase/lease without deleting payloads, outcomes or fencing tokens.
  Partial writes are resumable; cleanup performs no external IO.
- HumanInput recovery reuses saved request text/target/deadline. The Channel
  repository CAS from NotificationPending to Notifying grants one external
  invocation; Notifying, including legacy zero-attempt rows, is never resent.
  Runtime supplies a read-only Run/Session activation/node/deadline preflight;
  this is not a cross-Store transaction or an external exactly-once guarantee.
  Existing requests return covered execution node IDs so the active-Run
  scanner only reconstructs missing ready events from the immutable snapshot.

- Missing startup facts have a non-renewable 90-second preparation grace from
  saved Run creation. An active creator is protected during that interval;
  expiry permits conditional revocation, not a claim that its process died.
  The Run failure CAS and typed startup-failure fact share a small transaction
  only on this failure path. Restored facts or a terminal Run make stale CAS
  fail. Snapshot-less Session completion requires that matching typed fact and
  original activation; it neither recompiles a definition nor fabricates IM.
  Missing Bot dispatch payloads use the saved Node timeout/retry policy, or
  saved start + 90 seconds and FailRun when timeout is disabled (legacy absent
  start uses Run creation). The Node CAS rechecks absent payload, attempt and
  no artifact or Provider run ID; it never resends that attempt. A saved Provider
  run ID is positive acceptance evidence even without a checkpoint, so it keeps
  waiting under the original Node timeout policy. Legacy/mixed writers still require
  pre-FO drain. No normal-path writes, migration, new worker or cross-Store
  transaction is added.

- Terminal IM checkpoints freeze the original Run/Session activation, destination
  set, rendered text and deadline before Session completion. Per-recipient
  progress and a cleanup marker use short owner/token leases and JSON CAS;
  every send and acknowledgement requires the same Completed Service activation.
  Preflight is read-only and may retry while unsent; Sending/unknown is never
  resent because Channel delivery has no uniform idempotency guarantee. Partial
  delivery remains explicit and does not alter the terminal Run outcome. A
  separate bounded checkpoint page also covers already completed Sessions.
  Memory/SQLite/MySQL share repository contracts; Channel ports separate pure
  preparation, preflight, external delivery and existing terminal cleanup.

- Chat result publication checkpoints freeze the original command, timestamp and
  deadline in the existing delivery table. Only Pending may claim and start IO;
  owner/token/expiry and active-Run fencing protect acknowledgements. Delivered
  resumes Run completion without sending again; Failed resumes original failure.
  Delivering is never replayed: history deduplication does not cover message-flow
  Bot routing. Uncertainty fails at the original deadline and may have produced a
  visible result. Persistence errors propagate. Memory, SQLite and MySQL share
  conformance tests; the active-Run scanner uses the same foreground path. No new
  public state, migration, whole-workflow transaction or worker is introduced.

- `StateMachineLoopInstrumentationHook` observes committed entry/result transitions
  and typed v2 compiler rejection through closed metric enums. It cannot receive
  Run/Node/Bot IDs, raw outcomes or artifact payloads. The synchronous hook must
  not block or fail execution. Completed-node replay does not recount; a crash
  after commit can lose an observation. Runtime emits signals, bootstrap selects
  the Prometheus implementation, and test-support provides noop/conformance
  consumers. Existing HTTP/Event contracts and persistence are unchanged.

- State Machine Run views expose an optional `node_execution_metadata` map
  covering every Loop body execution node in start/get/rerun responses. Node
  queries and graph nodes use the same `execution` object; graph result edges
  preserve their real outcome and carry the saved `loop_route`. Graph definitions
  retain authoring graph_mode and add execution_graph_mode and
  execution_plan_compiler_version.
  Runtime reads the validated immutable plan, without parsing IDs, recompiling
  v2 authoring or consulting current limits. Read-only queries are independent
  of the execution switch. V1 omits these fields; legacy Run/Node rows without
  snapshots retain their existing raw read path. Present v2 snapshots with a
  missing, corrupt or unsupported plan fail instead of returning partial metadata.
  Both HTTP adapters, public Session creation, OpenAPI and panel DTOs preserve
  the projection. Graph node `outcome` is the saved actual outcome; consumers
  compare it with edge.outcome instead of inferring selection from target state.

- Public Node started/completed/retry_scheduled Events carry the same optional
  execution object from the saved plan. Existing Event CAS/transactions remain
  unchanged. Eventing full and metadata_only projections preserve this metadata.
  V2 Completed-node output history saves metadata.state_machine.execution with
  a stable message key before successor progression. Write failures propagate;
  the existing active-Run scanner can repeat the local history write. Bot output
  stays FullOnly, Human output is directed to its saved responder. History batch
  reads return the stored metadata verbatim; legacy v1/pre-persistence output
  retains the snapshot projection. This is not Chat final-result publication
  or terminal-Run cleanup, which remain separate FO work.

- Bot dispatch checkpoints freeze the rendered request, delivery identity, target
  reference and original deadline before external IO. URLs, credentials and
  forwarding headers are excluded. Normal dispatch and recovery share a short
  owner/token lease and a durable Pending-to-Delivering send marker. Only Pending
  requests may be sent; Delivering is never replayed without a transport guarantee.
  ACK and ambiguous-dispatch expiry fence the active Node/attempt and checkpoint
  together in a small local transaction. Rejected delivery preserves the existing
  fatal policy; expiry saves the original retry/fail_run decision. If Node timeout
  is disabled, the saved Provider deadline bounds ambiguity only. Missing payloads
  converge only at the saved timeout/preparation deadline, without rebuilding
  today's request or inferring an unsent state.

- Opening checkpoints save immutable rendered text/component and Run identity
  before startup. Pending/Running recovery persists that original history and
  marks its barrier before dispatching unstarted initial nodes. A fixed message
  primary key and a checkpoint CAS suffice for this local write; there is no
  external-delivery lease or frontend acknowledgement. Missing startup facts
  wait for the saved preparation grace, then fail through a conditional Run
  update and typed failure fact, without rerendering today's Group.

- Judge input persistence records an explicit Node judging phase and immutable
  artifact/responder. Normal Bot/Human responses and recovery share a short Node
  lease. FinishJudge fences active Run, Running Node, attempt, owner, monotonic
  token and lease deadline, then commits terminal state, saved failure action,
  existing Judge audit and optional public completion Event in one local
  transaction. Memory/MySQL/SQLite implement the same repo contract. Legacy
  artifact-only rows are not claimable; delivery correlation never uses this
  lease token. Completed Judges are not called again; a remote result lost before
  local commit may be reevaluated with the same saved input and attempt.

- Experimental completed-node progression recovery returns bounded cursor pages
  with explicit per-Run failures. The Run repository separately pages Pending
  and Running rows by exclusive run_id under environment/status filters; the
  runtime merges them under one bounded cursor. Memory and SQL share
  pagination and Pending/Ready/RetryScheduled skip-CAS conformance tests.
  Recovery requires immutable snapshots, retains uncertain Running attempts,
  and resumes Chat finalization from immutable publication facts. It does not
  infer creator death from absent payloads: bounded preparation expiry and
  repository conditions decide whether missing-fact convergence may commit.
  Terminal IM uses its own checkpoint recovery page, independent of active Runs.
  Already committed RetryScheduled attempts use
  their saved identity; no extra attempt is created by progression itself.

- Failed-attempt recovery uses a repo-only `StateMachineFailureAction` saved
  alongside Failed/error/completed_at by one Running-attempt CAS. Retry scheduling
  clears this action; a saved FailRun cannot be retried. Missing/unknown legacy
  decisions fail closed, without guessing from error text or current configuration.
  Memory and SQL share failure/retry/eventful conformance tests. SQL eventful CAS
  races may surface the existing Conflict error and are retried by a later scan.

- A separate terminal-Run recovery page scans Running ServiceInvocation Sessions
  by exclusive Session id, then completes only the Run's saved activation from
  its immutable snapshot and persisted terminal result. Session application and
  repository contracts provide an activation CAS; Memory/SQL implementations
  and the runtime-cleanup decorator must preserve it. Normal State Machine
  completion, failure, and cancellation use the same CAS, propagate write errors,
  and dispatch callbacks only from the returned activation snapshot. Missing
  activation metadata is never inferred from the current Session. Completed/Failed
  Run recovery prepares the immutable terminal IM intent before completing the
  Session, then shares the foreground delivery path. Chat publication uses its
  separate checkpoint; no HTTP endpoint or new callback payload is introduced.

- Pending HumanInput views and HumanInputReadyEvent optionally carry the shared
  LoopContext. A Loop entry includes all four fields, with explicit null only
  for the first iteration's previous_result; ordinary nodes omit the context.
  Channel direct-assignee notifications show the complete prior result in a
  separate section. Shared-group notifications keep iteration information and
  point to Workbench without exposing private output/outcome/result identities.
  The structured context is never redacted into a false first-iteration null.
  Rendered text is saved in the existing HumanInputRequest before delivery;
  replay and queue activation use the original text/destination/deadline.
  Replayed Active/terminal requests do not send again. Notifying replay keeps
  the original interaction stream key; provider retry/exhaustion semantics stay
  unchanged. Queue activation propagates persistence errors instead of treating
  them as delivery failures. This does not add a recovery scanner or guarantee
  exactly-once external delivery across ambiguous multi-instance failures.

- State Machine snapshots return authoring, resolved bindings and an optional
  complete execution-plan envelope. Saving all snapshot fields is one immutable
  write; rerun copies the same envelope in its existing creation transaction.
  V1 omits the envelope; v2 requires plan, hash and compiler version together.

- Definition validation preview optionally projects a compiled acyclic graph
  with shared node execution metadata and result-edge loop routes, preserving
  the authoring graph mode. V2 preview reports VALIDATION_ONLY_FEATURE when
  the runtime's execution capability is disabled. Explicit deployment opt-in
  enables both validation and persisted-plan execution; v1 omits the added fields.

- Preview and Run graph optionally expose a `loops` map of StateMachineLoopGraphView.
  These logical descriptors come from validated authoring / immutable Run snapshots.
  Existing flattened nodes/edges remain unchanged; body logical IDs map to opaque
  execution IDs via execution metadata. No new persisted state or execution node kind.
  Consumers: both HTTP adapters, OpenAPI contracts, frontend preview and BCS panel.

- Preview/Run edges optionally expose `display_name`; Loop descriptors optionally
  expose `continue_display_name`. Names come from validated authoring or immutable
  snapshots, never replace actual outcomes, and do not select routes. Missing fields
  remain omitted for existing definitions/plans. Authoring transition names cover
  ordinary/body/break/exhausted edges; Loop continuation names cover the return edge.
  Both HTTP adapters, OpenAPI, frontend preview, panel and Skill preserve this contract.

- Run graph nodes optionally expose `assignee_display_name`, resolved from the
  saved definition's participant display name for a BotBinding assignee. Missing
  or blank names are omitted; consumers fall back to the binding ID. RuntimeActor
  assignments do not acquire participant names. Both HTTP adapters and the panel
  preserve this additive display field; execution bindings and snapshots are unchanged.

DeliveryAdmissionTarget carries a typed per-recipient rejection, committed as an
unsent Failed delivery without aborting other recipients. DeliveryStatusView
adds optional fixed admission_error codes, never arbitrary transport error text.

- `CoordinationIntentPort` with authenticated consumer identity and immutable execution receipts.

MessageRepoPort adds scoped run_chat_segments reconstruction with fail-closed
defaults. AdmitMessageDeliveries accepts an optional visible display companion
for internal run_reply admission; its message/event commit with summary, targets,
and lifecycle CAS. Public history excludes internal summaries before pagination;
canonical ID reads remain unfiltered. Memory/SQL implementations and caller/test
construction propagate this internal contract change together.

Failed Group terminals admit a primary chat_error string projection and an
optional preceding chat display companion, atomically with Failed lifecycle CAS.
The error has no delivery targets or message.created event and never enters
run_reply reconstruction. Bot history filters the projection before conversion.

MessageRepoPort also provides append_message_with_id for a caller-owned stable
logical message id. Memory and SQL return one stored message and one sequence
under concurrent replay; callers must verify original content and identity.
SQL relies on the existing message primary key and rolls back the duplicate
insert's sequence allocation. The old client_msg_id lookup alone is not a
concurrent uniqueness guarantee. Memory/SQLite/MySQL Text and Prepared contracts
cover this path; custom implementations default to an explicit storage error.

Control work_batch ignores the legacy ID cursor and reserves per-action shares
under one total limit; timeout classes use deadline order. This internal contract
change propagates to the runtime and both SQL/Memory repositories, with shared
conformance coverage. Recovery cursor semantics and external wire APIs are unchanged.

Managed/repository bounded_contexts returns a bounded newest-first metadata
page and complete bound count. Persisted Send selections and Inject's additive
discarded_context terminal propagate to queue preparation, storage, status
consumers and the fixed-column monitoring hook without changing Bot wire APIs.

Managed delivery query contracts distinguish scalar scheduling candidates,
complete active counts, scoped lifecycle reads and queue statistics. The
DeliveryInstrumentation hook observes fixed-name operations and post-commit
events without prescribing a metrics implementation or exposing payload labels.

MessageFlowService exposes Human-only environment-wide delivery-policy read/replace operations; policy values use the leaf bcs-config-api contract, and the delivery repository owns durable version CAS. Application validation rejects non-Human callers independently of HTTP.

- Application, core, and port trait contracts for BCS.
- `GroupCreateCommand` requires an explicit `create_initial_session` boolean.
  Existing callers use true; false supports non-provisional normal groups, including StateMachine,
  creation without initial Session writes or bootstrap delivery. It is not a
  persistent prohibition on subsequent Session creation.
- Shared contract-level DTOs, error types, and service container types.
- Default `Noop*` implementations used to keep contract boundaries explicit in tests and local wiring.
- Current-session state-machine permission/start contracts and the outbound
  result-publisher port used to return a completed one-shot result to chat.
- V1 `AuthenticatedCaller` contract types that preserve User, Bot, App, and
  AccessKey context without retaining transport metadata or credentials.
- Session-scoped Workbench connection-token use cases, exact-session connect
  reauthorization contracts, and the outbound token signing/verification port.
- Channel conversation mapping lookup by BCS session id, with optional channel-type filtering.
- An optional `ChannelSenderIdentity` on Human WebSend commands. The Channel
  service may populate it only for an opted-in Bot binding after commands and
  HumanInput replies have been consumed; other application entry points leave
  it absent.
- Transport-neutral Session launch commands that receive adapter-normalized
  Human or Bot identity while keeping credential parsing and protocol response
  projection outside the application boundary.
- The state-machine run repository contract includes an atomic
  `create_run_if_session_idle` operation for one-shot session launch
  serialization; production stores must override its compatibility default
  with backend-level locking.

## Consumes

- `bcs-protocol` types only where protocol reuse is intentional at the contract boundary.
- Async trait, serialization, logging, error, and transport-neutral time types.

## Allowed dependencies

- `bcs-protocol` wire contract crate, currently located at `service-api/bcs-protocol`
- Contract-only support crates such as `async-trait`, `serde`, `time`, `tokio`, and `thiserror`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugin-api/*` and `plugins/*`
- `external-clients/*`

## Configuration

- This crate does not read env or runtime config directly.
- Any policy or config knobs must arrive as typed inputs from bootstrap or owning services.

## Runtime ownership

The crate owns contract semantics and fail-closed default behavior. Its V1
authenticated identity types own no JWT, HTTP, Gateway signing, credential, or
Actor-selection semantics, and the crate does not own concrete runtime behavior.

## Tests

- `cargo test --package bcs-service-api --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-service-api --all-targets --manifest-path src/bcs/Cargo.toml`

## Friend connection list contract

V1 FriendConnectionService accepts optional target_type (Human/Bot) and required
one-based page/page_size (1..100). The HTTP adapter defaults these to 1/20.
The invitation facade authenticates the actor, validates pagination and translates
page/page_size to offset/limit. ConnectService::list_friends_paginated accepts
FriendListQuery and returns FriendEntriesPage; EdgeGrantRepoPort exposes the same
query with FriendIdsPage. The store owns active-default-profile matching, exact
Human-prefix filtering, deduplication, case-sensitive UTF-8 ordering, count and
pagination. A single SQL read returns the page and total together, including the
total for an empty page. Query/decoding errors propagate through ServiceResult;
the facade checks the conversion of the u64 count into the V1 u32 response.
Human-to-Bot authorization remains one directed grant; listing from either side
does not grant reverse access.

Propagation: the invitation facade, DbConnectService, DbEdgeGrantStore, Noop
implementations and recording test doubles implement the required paged method.
Legacy list_friends consumers (legacy HTTP routes and Bot search) keep their
unpaginated interface and behavior; there is no default full-list fallback for
the paged interface. No HTTP/Plugin API, configuration or schema change is needed
for the pushdown. V1 consumers still default to 1/20 and must page for all friends.

Storage portability: SQLite and MySQL use store-owned SQL flavor expressions for
binary identity and literal human_ prefix matching (not LIKE wildcard matching).
MySQL uses the existing CTE-capable deployment baseline. Existing edge indexes
(from_id, env, status) and (to_id, env, status) support the two candidate scans;
profiles join by primary ID. COUNT/UNION still process the matching set in the
DB, and deep OFFSET pages are not constant-time. Validate production plans with
EXPLAIN against representative data before adding workload-specific indexes.

Validation: store conformance tests exercise mixed actors, single Human grants,
double Bot grants, default-profile eligibility, exact prefixes, ordering, empty
pages, bounds and errors. Facade tests prohibit full-list calls and verify paged
results/errors/count conversion. HTTP contract and legacy route/search tests
cover compatibility. MySQL SQL construction tests do not substitute for a live
MySQL conformance run.

## Human mention notification metadata (0.2.0)

The outbound `HumanMentionNotifyPort` DTO carries optional `group_name` and
`session_name` display metadata alongside the existing authoritative IDs. The
message-flow application supplies the Group label and resolves the Session title
only for a real notification, off the main send path. Missing/read-failed sessions
retain the notification's ID without a title; a Session from a different Group
must not contribute a title. Group-level messages have an empty Session ID and no
Session name. The names do not affect routing, recipients or authorization.

This source-contract addition affects message-flow producers, no-op/test fixtures,
and the bootstrap adapter. All struct literals need the two fields; external
consumers must rebuild against 0.2.0. Bootstrap maps into the separate
`bcs-human-notify-api` schema. No persistent schema or HTTP/WS API changes are
required.

Provider management supports optional shared endpoints and saved Bot overrides. BotWebhookChange represents unchanged/inherit/set independently of HTTP; resolved BotDeliveryTarget still requires a concrete URL. Repository endpoint updates return persisted bindings or errors.

OpenAPI RegisterService adds optional Provider selection at issuance and mode/ref/
webhook at redemption. Optional metadata is omitted for legacy v1 responses.
ProviderRegistrationCoreService owns scoped authorization and strict creation;
BotProviderRepoPort replaces journal reservation/completion with Bot-owned Provider
identity, cross-mode uniqueness and gateway-only dual writes. Duplicate scoped refs
conflict without credential replay; different refs may share a valid register token.
Shared receipt DTOs live in types (no repo-to-core dependency). Core and repository
contracts have shared conformance harnesses, including Memory/SQLite implementations.
No Plugin API changes. Token v2 purpose and mode claims cannot be widened during redemption.

ProviderBotCoreService also owns authorized deletion by Bot Provider/ref metadata
for both modes. ProviderManagement invokes this core operation before its legacy
binding fallback and then performs channel cleanup; it never accesses the metadata
repository directly. A missing metadata identity returns None, but storage and
authorization errors propagate and must not trigger the fallback.

BotRegistryCoreService/BotRepoPort add fail-closed `create_registration_if_absent`
for this flow. It is atomic, preserves existing active/deleted identities and
never uses upsert semantics; existing registration methods remain unchanged.
Memory and persistent stores implement it, with race, tombstone and failure tests.

StateMachine history canonical lookup resolves physical IDs and stored legacy client
keys within one environment/Session before audience or pagination filters. SQL
uses chunks of at most 200 keys with two bounded result queries per chunk; unknown
implementations return an error, not an incomplete authorized history page.

MessageRepoPort also provides `list_state_machine_history`: an env/group/Session
scoped, audience-filtered page of persisted StateMachine entries, bounded to
1..1000 plus lookahead. Full/Bot excludes Human prompts; Participant sees public
and explicitly directed rows only. Unsupported adapters fail rather than
returning an empty successful page. The runtime history facade selects this
port in messages mode before accessing any workflow repository. Authorization
and ordinary-chat history policies remain at their existing boundaries.

`resolve_history_window_start` resolves the ordinary Chat window at a fixed
physical sequence anchor. New durable projections do not consume positions;
legacy rows and missing ordinary positions do. Store/query failures propagate,
and audience/owner filtering remains independent. No schema changes or window
initialization API is required.

StateMachineRunRepoPort batches at most 32 Run IDs to find missing terminal
history-repair confirmations. Memory and SQL stores retain a completion marker
only after opening/publication rows are repaired successfully. Later cleanup
sweeps, including after restart, skip those source/message reads. Node-history
Pending recovery and network delivery acknowledgements remain independent.
