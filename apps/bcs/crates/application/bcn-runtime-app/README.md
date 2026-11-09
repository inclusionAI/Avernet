# BCN runtime application

This application crate implements `GatewayService`, `WorkerRuntime` and
`ConnectorRuntime` through `DefaultGateway`, `DefaultWorker` and
`DefaultConnector`. Constructors receive service contracts only. Tests wire the
real memory repositories and pure Direct/Structured reducers. No production
bootstrap, HTTP client, database client, environment lookup or legacy-loop
migration is included. Admission must be explicitly enabled by the composition
root; production must leave it disabled until its infrastructure is configured.

Gateway binds authenticated principal, complete scope, target and original
session/task to every operation. Message identity is separate from content
hash; trace IDs affect neither. Retries read current task state. A task-bearing
submit is an original-task reply, with optional `interaction_id` (required when
multiple HumanInput nodes are active). Replies and cancels use durable inbox
dedup records and bypass new-task capacity. A subscription starts from a
canonical snapshot or supplied cursor and rereads canonical pages, including
Reset epochs. It reauthorizes each read and stops after terminal publication.

Worker checkpoints actual reducer transitions and the consumed inbox position
before publishing ordered hard events and committed effect references. It
reloads checkpoint revisions after publication. Result/Judge commands are
filtered before leasing; canonical SavedResult returns enter the trusted inbox
before terminal publication/dependent scheduling. Absolute timers survive
restart. A pure timer preview identifies the affected node; its own persisted
partial projection is frozen into the timer input before reduction. Existing
timer dedup wins across retries. Durable SessionRecovery counts ownership
changes without checkpoint progress, quarantining after three. Activation,
publication and no-op commits cannot erase quarantine.

Connector is independently runnable. It claims before ACK, writes MAY_HAVE_SENT
before network I/O, persists handles before another stream read, and retains an
observed stream item across storage failures. Reset/Delta retains its complete
ID, expected cursor and payload until resolved; ambiguous failures retry exactly,
and only an explicit cursor conflict after dedup lookup permits rebasing.
PREPARED alone permits a send.
Recovery queries/subscribes with the exact durable handle; no handle means an
explicit execution_unknown result and no resend. Each takeover reads its own
node projection and publishes Reset before Delta. Direct responses retain actual
server context; group attempts use isolated downstream context IDs. Cancellation
has its own dispatch and preserves Confirmed/AlreadyTerminal/Unknown evidence.

`tick()` is bounded and returns a TickReport including errors. Worker `step()`
drives one session. Pending external futures are polled, never awaited to
completion by tick. Heartbeat TTL is discovery only; takeover additionally needs
one uninterrupted TTL of successful own heartbeat and explicit Dead observation.
A failed renewal restarts warm-up. `drain(deadline_ms)` is a nonblocking poll:
it stops new ownership/Connector claims, reports remaining work, and preserves
all durable records when the absolute deadline drops in-memory I/O. `run()`
accepts an injected shutdown watch channel and polls drain. Closed watch channels
also request shutdown. The clock is constructor-injected.

## Bounded work and payloads

Defaults remain 1024 shards; heartbeat 1 s/TTL 3 s; ownership inspection 500 ms;
inbox page 100; recovery/external futures 64; dispatch supervision 1 s;
progress polling 5 s/stall 15 s; token flush 200 ms; pending tasks 16; graph
nodes 32/agents 5; poison takeovers 3; drain 30 s. Retry backoff is 1–5 s.
One tick examines at most 64 ownership slots and 64 owned wake pages, recovers
at most 64 sessions, and polls at most 64 external commands/dispatch actors.
Waiting sessions do not reserve selection slots for their lifetime: each tick
selects at most 32 admission repairs and reserves the remaining slots for durable
wake discoveries, rotating wake cursors and
owned-shard offsets, then fills unused slots from retained session hints.
Unselected unfinished sessions remain in durable indexes.
Each selected session also rechecks ownership (up to 64 additional owner reads)
and consumes at most 100 inputs. Scans retain cursors even across empty
eligible command pages. These are functional bounds, not capacity measurements.

Inbound parts and each remote node output are limited to 64 KiB serialized;
frozen spec to 32 KiB. Transition preflight sums spec, state, effects, events and
future SavedResult duplication against 896 KiB (128 KiB reserved below the
1 MiB row ceiling). It estimates command/intent/projection/event mutations with
88 available slots below the 100-mutation transaction ceiling. Aggregate
Connector projection similarly reserves 128 KiB. Existing AtomicStore byte and
batch validation remains authoritative and LimitExceeded maps to CapacityExceeded.

An accepted transition that exceeds the budget uses the pure internal
TaskFailure reduction at the original inbox position. It freezes a single
bounded failure result (up to 8 KiB partial output), cancels known remote work,
blocks uncertain Direct context and preserves queued tasks. It never replaces
an existing immutable result intent or treats a capacity failure as remote stop
proof. Terminal status still waits for ResultSaved. Local streaming/observation overflow becomes ObservationFailed with a UTF-8-safe
bounded prefix. After its receipt is durable, Worker uses the validated input and
canonical dispatch handle to reduce TaskFailure, including supported cancellation
and uncertain Direct context blocking. A handle saved just before Snapshot
observation failed is restored through pure Accepted reduction in that same
checkpoint. Already-confirmed Completed/terminal Snapshot output overflow keeps
remote termination evidence and does not cause uncertain-context blocking.
Failed/FailedWithOutput retain authoritative remote failure semantics. Error text
is bounded.

## Storage cost and validation limits

Repositories share one physical atomic partition per complete Scope. An
unowned inspection is two point reads and one claim transaction; an owned
inspection is one read. A session turn adds a fenced recovery record operation,
spec/checkpoint reads, one bounded inbox scan, then the documented repository
commit/publication operations per consumed input (at most 100). Publication
reloads checkpoint after each input. Observation failure adds one canonical dispatch point read before its failure
checkpoint. Worker effects add one input-dedup point
read, the external result/judge operation and durable inbox append/ACK. Connector
actor polls read its dispatch, task snapshot and own node projection; Delta/Reset
adds the repository's bounded node aggregation scan. Recovery read_projection
uses a dispatch point read and a two-key read. No transaction spans external I/O.
Repeated external failures back off; stalled futures are dropped after 15 s and
the durable command remains available. Detailed per-repository costs are in the
runtime-store README. No production capacity, crash durability or real failover
claim follows from memory tests. Production adapters and bootstrap are separate.
