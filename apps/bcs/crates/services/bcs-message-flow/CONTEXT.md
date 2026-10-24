# bcs-message-flow Context

System admission shares Group runtime preparation and Bot/session lanes. A weak
composition-bound SystemMessageQueueService lets the dispatcher persist its whole
event before Driver dispatch. Public/Skip copies share a source; personalized
initialization remains private. Explicit required_context survives TTL/count
limits and must fit fully in the shared history byte budget. Joint Group/System
disable retains context for the next Send, using exact scoped drain admission;
it does not turn queued initialization into a native inject call.

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
Queued Worker TaskDispatch Provider abort uses the Provider's Bot/Session scope only when the
original Provider route still resolves, the active-run index contains exactly
the target run, and the managed lane contains no other active Send. Otherwise
abort remains unconfirmed; the scheduler must not claim that the Worker stopped.


One-shot StateMachine chat results enter MessageFlowService with immutable
producer facts. Group queue admission owns the message/delivery transaction;
legacy delivery persists the same StateMachine/Public string, Run and original
time before sending. Conflict, rejection, empty admission and legacy failure
propagate before publication acknowledgement. Queue payload reconstruction reads
these strings both as Send sources and bound Inject context. Fully managed
replays reuse durable deliveries; mixed replays fail as ambiguous because legacy
recipients have no durable delivery ledger. Runtime retry policy is unchanged.

## Provides

The shared human-mention notify helper owns the per-Group external-notify
policy decision. Callers (group_send, web_send, bot-event relay) invoke it only
after the message is persisted; the helper performs one uncached authoritative
`GroupCoreService::read_human_notify_policy` read per candidate notification —
it never caches the mode and never trusts request-time copies. The read is
fail-closed: an unavailable or failed policy read logs and skips the external
notification without failing the message flow, while mode/driver comparison is
synchronous and injects no extra await after admission.

IM queue hints are best-effort and appear only after ten seconds of continued
Queued state. The Channel `/cacel` command (with `/cancel` compatibility alias)
cancels the authenticated Human's newest still-unsent IM message in the current
Session; it never turns a queue cancellation into an abort of running work.
`/abort` continues through the canonical Bot/session chat-abort path.

The queue runtime treats a complete `BotDeliveryResult { delivered: false }`
as terminal Failed, releases the Bot/session lane and never retries it. Only an
explicit DeliveryNotSent result can consume the safe-retry budget. Transport
errors without a complete downstream result remain Unknown, and late callbacks
cannot reopen the terminal delivery.

Bot offline state is not a durable waiting policy. Each scheduler/master epoch
allows a bounded startup grace for WebSocket Bots to reconnect; an otherwise
eligible queued head that remains offline after the grace fails before send and
releases its bound context. A disconnect reported by the WebSocket delivery
adapter after send-start is also an explicit terminal rejection, not Unknown.

LiveDeliveryPolicy reconciles newer durable versions under the management snapshot
write lock. Bootstrap invokes this every five seconds on the master to observe
late old-master commits; failed reads retain the existing snapshot and return an
error. Equal/older versions do not replace current state.

- Inline v1 and reference v2 coordination consumption through an injected resolver port.

Queued messages and run-reply display companions share the existing persisted
visibility classifier. Read-only delivery queries reuse the configured Chat
history window for recorded join anchors (one bounded repository resolution per batch). ManagerWorker and
StateMachine reads use their existing owner/audience rules without a Chat window.
Missing legacy join anchors stay unrestricted by sequence; cancellation and manual
resolution retain the strict join boundary. No View Actor override is added.
Queue status reads and targeted frontend events also enforce Human participant scope/audience; queueing does not bypass visibility.

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
interpretation of rewritten snapshots. Managed task completion uses the same
canonical reconstruction; direct A2A-specific completion is unchanged.

ManagerWorker TaskDispatch, TaskResult and TaskMessage share the existing queue
runtime under flow Task. Admission persists canonical text/attachments before
accepting work. Immutable task intent in the delivery binds task_id to independent
Worker/result run identities; TaskStore is a versioned recoverable projection.
Worker terminal CAS and Manager result admission commit atomically, even if the
Manager queue is full or authorization later changes. Result delivery performs
current Session/role checks before I/O and never re-executes the Worker.
The full run_reply body and the response-mode task_result_text are distinct;
public history receives only the display companion, not the internal summary.
Queued work does not start the legacy TaskStore TTL. Scoped completion refreshes
durable task state; group-wide completion also checks its running Sessions.
Running Worker TaskDispatch has a deadline five minutes before the configured
Provider chat-run timeout (2h55 at the default three-hour setting), measured
from Send start rather than queue admission. The run correlation remains valid
until the Provider deadline so the earlier timeout can abort it. A confirmed
abort atomically admits one timed-out TaskResult for the Manager. If stopping
cannot be confirmed, the Worker remains in CancelUnknown and an atomic durable
System Send tells the Manager the task reference, Worker and uncertainty; the
Manager should verify the old run before reassigning. The legacy task ledger
TTL uses the same 2h55 default but does not itself issue an abort.
Task-only disable drains old work and results. Retained System context can promote
a legacy Task Send to a scoped queue carrier; it never falls back to native inject.
Task lifecycle event projections remain post-commit (no new outbox); their errors
propagate, but a process crash can leave these projections missing. Delivery and
canonical result persistence, not those projections, determine task completion.
See `src/bcs/specs/2026-09-16-task-message-queue/spec.md` and conformance_queued_task.

Task ledger notifications are explicitly human-only; they create no Bot
delivery. Their projection failures are logged locally and never invalidate
admission, dispatch, or terminal results, nor produce Manager notifications.
Task admission, lifecycle event, and canonical result persistence errors retain
their existing error contracts; only this auxiliary human notice is best-effort.
Confirmed Failed/Cancelled assignment transitions without a Worker
callback create the same stable TaskResult in the terminal CAS transaction.
Unknown/CancelUnknown and result-delivery failures never recursively create
TaskResults. Result preparation retains current Session/group authorization.
Task intent optionally persists assignment_intent_id and a UTF-8-safe summary;
historical intents without these fields fall back to the internal task reference.
Confirmed cancellation is a separate ledger category, not a failure. Existing
abnormal callbacks use a hidden run_reply with task_result_text; human error
history remains a separate, idempotent post-commit projection. No new outbox,
MCP tool, database table or automatic retry policy is introduced.

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
Dynamic enforce requires durable storage and healthy scheduler supervision, not a host file lock. Bootstrap supplies a single elected-master runtime epoch per DB. Policy refresh on takeover uses the same snapshot write lock as management updates; configuration requests route to the master, without periodic policy broadcasts. Demotion cancels local scheduler futures, preserving uncertain send-start records for takeover recovery. Scheduler supervision failure still closes admission and policy activation.

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

## Direct A2A queue

Async Direct A2A uses Session registry claims, canonical private messages and the
shared managed-delivery runtime. The internal A2aChat preparation/projection branch
uses the same external and downstream session ID. Managed events never enter Group
reply routing. ChatRun final content is checkpointed before delivery settlement;
queries and bounded recovery reconcile state. Disablement drains existing lane work.
See `apps/bcs/specs/2026-09-20-direct-a2a-message-queue/spec.md` for storage costs,
policy readiness, cancellation uncertainty and the v3 HTTP/CLI contract.

## Provider collaboration scope

Direct A2A and group caller authorization enforce Provider collaboration scope before legacy public/friend/organization exemptions; the authenticated caller and all group participants are checked.

## Caller association ownership

Caller coordination reuses Managed delivery parent/invocation metadata and the
existing task/run terminal CAS. No independent child repository or result ledger
consumer remains; Caller wiring no longer initializes a separate repository schema.
~~Historical migrations and their compatibility schema initialization are unchanged.~~
(2026-10-20 勘误:The a2a_child table family migrations were later dropped entirely in
7ea603b8c; current numbered tails are sqlite 032 `session_registry` and mysql
032 `session_speaking_leases`.)
Accepted-dispatch lineage and per-session/turn invocation allowances are bounded
owner-local memory only: 1024 production entries, at most 16 invocation keys per
turn, depth capped at two. Reserve before ordinary admission; failed/unknown
admissions keep the allowance. Existing coordination intent/tool-call dedup owns
replay. Lost identity refuses inheritance; it never blocks existing Worker results.
This is not a cross-owner durable budget or a parent-terminal/admission transaction.
Each coordination assignment uses one scoped Managed parent lookup and existing
run context; no extra child INSERT transaction or child lookup at completion.
Memory locks are never held across await/network I/O.

Protected Caller dispatch runs in the bounded runtime task set with the existing
I/O timeout, never inline on the scheduler. In-flight Caller tasks reserve Bot
capacity conservatively (a persisted active row can also be counted while its
future is pending). Deadline/cancel control aborts the local future; shutdown
aborts and drains it before recovery. Timeout before StartSend fails preparation;
after StartSend it is Unknown, never proof of non-delivery or permission to resend.
This bounds local futures, not remote execution. Queue TTL does not declare a
possibly executing dispatch stopped. Stage DEBUG logs carry dispatch IDs only,
not identity proofs or credentials.

No new queue scan or per-tick per-task lookup is added. Completion/timeout uses
one scoped delivery lookup and at most one lifecycle CAS; existing bounded control
(32 rows) and expiry (100 rows) batches remain unchanged. Capacity reservations
are bounded by max_tasks and use process-local bookkeeping, not another cache.

## Caller identity boundary

Caller dispatch consumes `bcs-caller-identity-api::CallerIdentityPlugin`. Concrete JWT/private adapters are injected by composition roots, never selected by orchestration.

### Caller credential profiles (Plugin API v1.1)

Dispatch authorization (`not_after_ms`, caller context expiry, parent/run/attempt
ledger checks and container isolation) is independent of identity credential TTL.
ExecutionBound plugins retain bounded run-attesting proofs. IdentityRelay plugins
return upstream identity credentials with their actual expiry/reuse semantics;
they do not attest run/scope/purpose/isolation. Protected send carries both windows
separately. Cancellation terminates BCS execution, not an upstream identity JWT.
The material ledger still fences parent run/attempt; plugins never select INITIAL
as recovery from a failed parent relay. Unsupported purpose-specific issuance
fails closed. This distinction adds no database access or credential cache.
