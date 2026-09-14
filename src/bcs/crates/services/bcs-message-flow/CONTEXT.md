# bcs-message-flow Context

Queued Provider routing snapshots are opt-in non-sensitive headers, bounded and
normalized by the shared config contract. Admission rejects only unsupported
Provider recipients, without retaining rejected values or bypassing the queue.
Send-start and scoped abort retain the original route; empty and named routes
cannot share an ambiguous scope abort. Inject does not overwrite its carrier's
route. Header metadata is excluded from protocol/model and public status views.

Queued group preparation captures versioned routing intent without copying
message text, attachments or credentials into deliveries. It rebuilds protocol
frames from canonical messages, reapplies current session membership and
outbound policy, pins WebSocket connection identity and retains the original
downstream session key. Per-attempt request aliases do not replace engine run
identities. Accepted engine identities are persisted before cache projections.
Exact queued Provider abort remains fail-closed until legacy-inclusive scope
exclusivity is implemented; it must never be emulated by a scope-wide abort.

## Provides

- Inline v1 and reference v2 coordination consumption through an injected resolver port.

Queued messages and run-reply display companions share the existing persisted
visibility classifier. Queue status reads and targeted frontend events also
enforce Human participant scope/audience; queueing does not bypass visibility.

Storage-only retries preserve scheduler work/results across transient DB failures,
with 100 ms–5 s backoff, shutdown interruption and rate-limited warnings. Network
operations and whole event pipelines are never retried. Terminal lookup/history/
CAS retain input and stable reply IDs until persisted; CAS resolves ambiguous
commits without duplicate replies. Composition calls retain_terminal_events on
the shared Arc: 64 bounded terminal tasks survive caller cancellation. In-memory
retention is not a durable inbox; restart/notification guarantees are unchanged.

Group terminal normalization reconstructs completed chat segments through the
scoped repository port, combines the current scoped buffer and final using
versioned prefix heuristics, and separates visible final segments from internal
run_reply canonical bodies. It does not infer tool text or promise lossless
interpretation of rewritten snapshots. Task/A2A-specific completion is unchanged.

The scheduler wraps an empty Bot page once in the same tick without resetting
its budgets. Control work uses fair due-action batches rather than a global ID
cursor; abort slots and transition fencing remain unchanged.

Bounded Inject preparation reads newest metadata and canonical bodies only up
to the configured history budget. It fixes reference/UTF-8-offset selections at
send-start; terminal orchestration distinguishes consumed from discarded_context
without deleting canonical messages or replaying omitted history. Safe retries
retain the selection, and pre-send carrier cancellation releases all bindings.

The single-instance runtime uses purpose-scoped repository reads: cursor-paged
queued Bots and scalar lane heads, complete active counts, identity callbacks,
and bounded expiry/control/recovery batches. Post-commit instrumentation uses
the transport-neutral DeliveryInstrumentation hook, implemented only in bootstrap.
No SQL/cache/Prometheus implementation dependency is introduced into this service.

LiveDeliveryPolicy serializes durable policy CAS and publication; Group admission and runtime read defaults plus partial Bot overrides, preserving drain and send-start version fencing.
Policy management validates trusted Human callers again at the application boundary, grants environment-wide access, and audits actor, versions, field names, timestamps and outcomes without recording credentials or full payloads.
Dynamic enforce requires durable storage and a healthy scheduler, not a host file lock. Deployment guarantees a single instance per DB; scheduler shutdown/failure still closes admission and policy activation.

- `MessageFlowService` implementation for Workbench/Web group send, bot event relay, chat abort, and master-slave task flow.
- `A2aChatService` implementation for direct bot chat and async chat run APIs.
- `MessageDeliveryCoreService` and `MessageDeliverySchedulingCoreService`
  implementations for pure managed-delivery lifecycle and per-Bot scheduling
  decisions. These do not authorize I/O before a repository transaction commits.
- `ManagedMessageDeliveryService` implements atomic lifecycle orchestration and
  causal context reassignment. `DeliveryRuntime` owns a single bounded task loop;
  `ManagedDeliveryPreparationService` supplies application-level reconstruction
  and authorization before the existing typed Bot delivery/abort ports are used.

## Consumes

- `bcs-service-api` service traits and delivery ports.
- `bcs-protocol` wire frames and payload DTOs.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

- `service-api/*`
- Pure utility crates such as `serde_json`, `uuid`, and `tracing`

## Forbidden dependencies

- Composition-root runtime crate
- `adapters/*`
- Concrete `services/bcs-routing`
- `external-clients/*` crates not listed in `Allowed dependencies` above
- websocket runtime sender handles and adapter framework types
- DingTalk runtime crates

## Configuration

- Policy knobs and dependency ports are provided by constructors and bootstrap wiring.
- This crate must not inspect env or choose concrete adapters/plugins directly.

## Runtime ownership

The service owns request-time message lifecycle decisions. It does not own
transport connection state and does not directly send websocket frames.

## Tests

Opt-in DEBUG target `bcs_reply_profile` emits body-free elapsed microseconds for
reply reconstruction, terminal transitions, repository commits and mutation-lock
wait/hold. Mutation guards are keyed by Bot (all sessions share a Bot guard);
Normal admission/state logs are DEBUG; Send terminal summaries remain INFO and
Unknown/CancelUnknown entries WARN. Independent queue monitoring is unchanged.
multi-target terminal admission acquires source and target Bots in lexical order.
Bot guards survive caller cancellation once persistence begins. Repository writes
use session locks plus Bot capacity locks for Send operations. Timing is diagnostic only,
does not alter queue policy, and adds no public
API/config fields. The isolated bootstrap reply load test correlates these events
with final-run spans and reports nested stage timings separately.

- `cargo test --package bcs-message-flow --manifest-path src/bcs/Cargo.toml`
- Structure check: scan this crate for adapter/runtime transport symbols before merging.
