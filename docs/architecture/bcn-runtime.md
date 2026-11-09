# BCN runtime contracts v1

The BCN runtime is an additive, opt-in execution path. Existing sessions and the
legacy execution loop are unchanged. Production admission remains disabled until
explicitly wired and validated by a deployment. These contracts do not claim
production durability, recovery latency, or capacity.

## Layers and consumers

| Crate | Layer | Contract and consumers |
| --- | --- | --- |
| `bcn-runtime-model` | contracts | Neutral serializable data used by runners, applications and repository implementations |
| `bcn-runtime-api` | service-api | Inbound application services, pure core reducer, outbound business ports |
| `bcn-runtime-storage-api` | plugin-api | Atomic partition storage and liveness; called by repository implementations |
| `bcn-storage-memory` | plugins | Volatile implementation used only by local/test composition roots |

Delivery adapters call `application::GatewayService`. Applications orchestrate
`core::SessionRunner` and outbound ports. Repository implementations map business
records to `AtomicStore`; reducers never depend on the infrastructure API.
Concrete implementation selection belongs in bootstrap/tests. No protocol SDK,
HTTP client, database client, endpoint or secret belongs in the model/core API.

The public model modules are `identity`, `task`, `runner`, `connector`, `storage`
and `error`. All durable ID types carry `Scope { environment, tenant, namespace }`.
Caller authentication is separate from message fields. IDs confer no access;
every gateway operation authorizes the authenticated principal, target and the
resolved session/task through registered `AccessPolicy` hooks.

## Application and reducer lifecycle

`GatewayService` exposes submit/get/list/subscribe/cancel, each with
`CallerContext`. Submission returns only after admission receipt, inbox and wake
are persisted. Repeated identity with different content is a conflict. Reads
resolve authoritative saved results, checkpoint state, then admission receipts.
Subscription begins with an authoritative snapshot and its cursor; a terminal
task is queried rather than newly subscribed. Expired cursors fall back to a
full snapshot, without a gap during concurrent event production.

Worker and Connector runtimes expose `run(CancellationReceiver)`,
`drain(deadline_ms)` and `status()`. Cancellation is a watch receiver: true or
sender closure requests shutdown. Drain stops new work and leaves unfinished
work recoverable; deadlines are absolute server-clock milliseconds.

`SessionRunner` exposes kind/validate/initialize/reduce. `SessionSpec` freezes the
source `bcs_domain::collaboration::StateMachineDefinition`, resolved bindings and
policy at admission. `RunnerState` is a schema-versioned, normalized, serializable
checkpoint, never a legacy executor. The reducer is deterministic: no I/O, clock
reads, random identity generation or mutation of supplied state. Inputs carry
identity and time; derived identities must be deterministic.

A `Transition` contains next state, effect intents, hard events and timer changes.
State, consumed input position, effects, hard events and timer changes commit in
one transaction under the current ownership epoch and checkpoint revision.
`TimerChange` explicitly supports both Upsert and Cancel. Effects execute only
after commit. Recovery loads committed state rather than reinitializing it.

The workflow subset is BotTask, HumanInput, acyclic sequence/parallel joins and
persisted Judge selection. Unsupported loops, nested group execution, direct
tool actions, sub-state-machines, notification, guards and output contracts must
be rejected before admission. Bindings and workflow semantics cannot change
mid-session. Unselected branches are skipped; human replies correlate an
interaction identity. Judge choices and node results persist before successors
start. Direct contexts carry across serial tasks; an unknown direct execution
blocks that context. Group node attempts use isolated downstream contexts.

## Connector contract

`port::AgentConnector` is an object-safe Send + Sync async trait. It exposes
capabilities, send_message, send_streaming_message, get_task, subscribe_to_task,
and cancel_task. Owned neutral requests and boxed Send streams avoid SDK types.
Optional operations default to `ConnectorError::Unsupported` without I/O.
Implementations bind a frozen target and reject mismatched target/binding version
before I/O; capabilities are target-specific and never inferred from a protocol
name. Cancellation distinguishes unsupported, single-task and exclusive-context
scope. Input continuation, streaming, query and subscription are independent.

`ExecutionHandle` contains target and remote IDs plus binding version, never
credentials. `FrozenTargetBinding` contains only an optional opaque credential
reference. `CredentialResolver` resolves an ephemeral non-serializable,
Debug-redacted credential at the external boundary.

Only PREPARED proves unsent. The connector atomically commits MAY_HAVE_SENT under
its dispatch fence before network I/O. It saves a remote handle immediately and
uses query/subscription after uncertainty. A finite dedup window does not alone
authorize replay; the capability must cover the operation's entire possible
lifetime or enforce an immutable expiry. Otherwise expose execution_unknown.
Durable dispatch uses `DispatchOperation::Send { request }` or
`DispatchOperation::Cancel { dispatch_id, binding, request }`. Each send,
continuation and cancel owns distinct stable dispatch/operation identities;
cancel persists its exact target handle, scope flag and frozen binding. Claim
and takeover return the same immutable operation. Every operation independently
advances PREPARED → MAY_HAVE_SENT before its network call.
`DispatchReceipt::Agent` retains agent events; `DispatchReceipt::Cancellation`
retains the typed Confirmed / AlreadyTerminal / Unknown outcome and affected-task
evidence. Receipt type must match operation type. Cancellation inbox input also
retains dispatch ID, operation ID and the identical typed outcome; uncertain
cancellation must never be translated into confirmed Aborted.

Errors distinguish NotSent, MayHaveBeenAccepted, RemoteFailed, Unsupported and
OwnershipLost. EOF without a terminal event cannot become success. Cancellation
intent is not cancellation confirmation.

An immutable result intent is fenced and persisted before `ResultStore` is
called. That store returns the authoritative saved result; only then may a
reducer publish terminal completion or advance dependent nodes. PendingResult
is nonterminal. Result writes never overwrite an existing authoritative result.
New connector epochs persist complete event projection before ordered Reset;
old epochs cannot overwrite snapshots or append new events. Hard results require
a fenced receipt and atomic inbox append.

Worker hard facts use `TaskEventStore::publish_hard_event(&OwnershipFence,
CommittedEventRef { session_id, event_id })`, independently of Connector deltas.
A committed HumanInput event can publish before any dispatch exists. The store
validates current session ownership, incarnation, epoch and scope, then loads
the immutable committed event payload rather than trusting a caller-provided
body. It atomically appends that event and saves its publication receipt before
acknowledging/clearing pending state. An unknown or uncommitted event fails
closed; a stale owner receives OwnershipLost without publishing or clearing it.
A current owner can retry by event identity and receive the same cursor. Hard
events use the authoritative task-stream cursor; ownership epochs do not become
Connector projection epochs. Connector `append` accepts only Delta, rejecting
Domain and Reset; `publish_reset` retains its own Connector epoch checks.

This pre-release contract correction replaces the send-only
`DispatchRecord.request` with tagged `operation`, and the untyped agent-only
receipt with `DispatchReceipt`. New serialization/consumer tests pin these
shapes before concrete stores are introduced; legacy runtime formats are
unchanged.

## Infrastructure Plugin API

`AtomicStore` is linearizable within one explicit `(scope, partition)`:

- `read_many` returns values/revisions in requested order, including duplicates
  and missing entries. Empty reads are valid.
- `transact` validates the entire request, checks all absence/revision conditions
  against one pre-state, then applies all mutations or none. Duplicate mutation
  keys are invalid. Duplicate conditions are allowed and all must hold. Empty
  and condition-only batches are valid. Mutations may be unconditional, but
  business ownership/checkpoint writes must supply all required fences.
- Every mutation, including deletion of an absent key, receives a monotonically
  increasing revision. Revisions never repeat after deletion/recreation within
  a store lifetime. A persistent backend must preserve this property across
  restarts. Exhaustion rejects the whole transaction before any mutation.
- `scan_page` is ordered by key, with an exclusive cursor bound to scope,
  partition and prefix. It returns at most the requested limit and examines at
  most limit + 1 matching rows. Pages are not a cross-request snapshot; callers
  handle concurrent writes through durable cursors and idempotency. Prefix may
  be empty. A foreign cursor fails closed.
- `server_time_ms` supplies backend-authoritative time; the memory implementation
  uses an injected `ServerClock`.

Scope and partition components are 1–64 ASCII letters, digits, underscore or
hyphen. Keys use 1–256 ASCII letters, digits or `_-/.:`; prefix may be empty.
Isolation is structural, never string concatenation of unescaped tenant names.

| Request bound | v1 maximum |
| --- | --- |
| read keys | 100 |
| transaction conditions / mutations | 100 / 100 |
| individual value | 1 MiB |
| total condition-key + mutation-key + value bytes | 8 MiB |
| scan page size | 1–100 |

All bounds are checked before touching state. One API call is one bounded
backend operation, not an N+1 loop. Memory reads are O(keys), transactions are
O(conditions + mutations) under one lock, scans use a tree range and at most
limit + 1 matching rows. A maximal read/scan may return 100 MiB; callers should
choose lower limits for large records. No lock spans external network I/O.

`NodeLivenessPort` registers process incarnation, renews heartbeats, inspects
bounded batches, pages membership and marks draining. Alive/Dead/Unknown are
explicit. Storage failures cannot be interpreted as Dead. Heartbeat TTL is a
failure detector, never an ownership fence; `OwnershipPort` epochs are the
correctness mechanism.

`conformance` exports reusable async consumer scenarios accepting
`Arc<dyn AtomicStore>`. Supply an empty isolated backend per case. The same
suite must run against every real backend; the memory tests prove only local
semantics. Memory construction rejects `RuntimeProfile::Production` and has no
automatic production fallback. It has no resources to drain beyond owned memory.
Its isolation tier is trusted in-process code with memory/clock access only;
there is no network, filesystem persistence, environment access or credential
resolution. The profile constructor and dependency boundaries enforce that tier.

## Defaults and compatibility

Defaults are 1024 fixed shards, heartbeat 1 s / TTL 3 s, ownership inspection
500 ms, inbox batch 100, recovery concurrency 64, dispatch supervision 1 s,
progress 5 s / stall 15 s, token batching 200 ms, 16 pending tasks per session,
32 graph nodes / 5 agents, quarantine after 3 unsuccessful takeovers and 30 s
drain. Configuration and capacity validation remain composition-root work.

These are new v1 contracts and do not change legacy APIs. Future changes must
update consumer compatibility tests and these docs before reducers, repositories,
applications or adapters depend on new fields. Checkpoint schema upgrades must
be explicitly supported before new writers are enabled. No production adapter
or runtime entrypoint is implemented by the contract crates.

## Reducer implementation and outbox correlation

`services/bcn-session-runner` implements the pure core contract. It normalizes
an attached Judge into an internal `@judge:<source-id>` node after the source
result-save barrier. Source IDs starting with `@` are reserved; synthetic Judge
nodes do not count against the 32 source-node limit or 5 distinct bound agents.
Judge selection is persisted as a separate immutable node result before
selected successors can run; its output never replaces original Bot output.
Task/node/attempt/purpose identities use length-delimited SHA-256 (64 lowercase
hex characters) so arbitrary source names cannot overflow storage keys.

Every `EffectIntent::SendAgent` carries frozen `task_id` and `node_id` in addition
to its request. Every `EffectIntent::CancelAgent` carries frozen `task_id`,
`node_id`, an independent `dispatch_id`, `binding` and request. Direct effects
use `node_id: null`; structured effects use their normalized node ID. Workers
must preserve these correlations in outbox/receipt routing, rather than infer
them from the current task (which may have advanced by delivery/recovery time).
Cancellation operation IDs derive per task/node/attempt; caller CancelCommand
identity is a separate application deduplication concern. Cancellation inputs
must match both that operation and its independent cancel dispatch identity.

This is a pre-release correction of the new v1 effect shape. Request-only
send/cancel outbox payloads cannot supply durable correlation and fail decoding
rather than guess it from mutable session state. They require offline discard
or explicit migration before enabling new writers; the runner does not migrate
them. Current effect/checkpoint serialization round trips are pinned by tests.
Existing legacy runtime formats and in-flight sessions are unaffected.

Reducers ignore Delta; Connector's projection and terminal Snapshot must carry
complete/partial output. `ResultIntent` remains immutable and is created only
at terminal evidence, never used for token accumulation. A failed terminal
Snapshot saves its partial output, and no successor starts before authoritative
ResultSaved. Structured tasks save their final task result after all required
node results; successful task output is the terminal node's output. HumanReply
checks tenant scope and exact interaction in core; Gateway retains principal/
target authorization. Concrete production admission remains disabled.

`QueuedTask.terminal_intent` is an additive `#[serde(default)] Option<ResultIntent>`.
Old queued-task checkpoint payloads decode with None. Queue cancellation freezes
a Cancelled intent and emits SaveResult without dispatch. The item remains in
the bounded queue until matching ResultSaved, then publishes Cancelled and is
removed. Duplicate cancellation is idempotent, and scheduling skips these items.
Unknown Direct execution freezes Failed/ContextBlocked intents for already
queued tasks through the same barrier; newly submitted tasks fail immediately.
