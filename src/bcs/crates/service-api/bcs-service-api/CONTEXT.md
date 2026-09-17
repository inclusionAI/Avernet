# bcs-service-api Context

## Provides

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
