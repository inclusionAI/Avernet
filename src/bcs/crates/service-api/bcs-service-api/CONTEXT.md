# bcs-service-api Context

## Provides

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
