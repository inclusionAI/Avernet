# bcn-session-runner

Pure `SessionRunner` core implementations: `DirectA2aRunner` serializes tasks in
one downstream context, and `StructuredGroupRunner` executes the admitted v1
acyclic workflow subset. Concrete selection belongs in bootstrap/tests. This
crate has no network, storage, clock, environment or random-number access and
is not wired into production admission or the legacy execution loop.

## Context boundary

Provides `DirectA2aRunner`, `StructuredGroupRunner` and reusable admission
validation. Consumes only `bcn-runtime-api::core::SessionRunner`, neutral
`bcn-runtime-model` / `bcs-domain` contracts and SHA-256 for bounded deterministic
identities. Supplied state is cloned; the caller must atomically commit each
transition with consumed input and ownership fence before executing effects.
Work is bounded by 32 source nodes, 5 distinct bound agents and 16 queued tasks;
normal reducer operations perform zero database/network round trips. Graph
validation and progression operate on these bounded in-memory maps.

## Admission and normalization

Validation accepts BotTask, HumanInput, sequence, parallel fork/join and LLM
Judge outcome transitions. It rejects unsupported graph modes, version 2/Loop,
GroupChat, ToolAction, SubStateMachine, notifications, guards, output contracts,
actions, retries, variable/event/schema semantics and extensions. There must be
one entry and one terminal; all nodes must lie on entry-to-terminal paths.
HumanInput requires a positive explicit timeout and no assignee/final_output.
Policies can lower but cannot exceed the MVP graph/agent/queue limits.

A Judge attached to source node `x` becomes the synthetic node `@judge:x` after
`x`; source IDs beginning with `@` are reserved. Synthetic nodes do not consume
the source-node or distinct-agent limits. Bot output is saved before invoking
Judge; the selected branch is frozen in a result intent and saved before any
branch work starts. Judge results contain no business output and are excluded
from downstream Bot inputs. Skipping follows potential reachability and joins
wait for every predecessor to be completed or skipped, with at least one
selected incoming edge. The canonical definition is reused; the old concrete
executor is never imported.

## Reduction and recovery

Task/node/attempt/purpose are length-delimited and SHA-256 encoded to fixed
64-character lowercase hex IDs; durable IDs also carry environment/tenant/
namespace. Time is supplied by input. Repeating a reduction or reducing a
serialized/restored state produces the same transition. Duplicate submissions
for the active or queued task are no-ops; the Gateway admission receipt owns
request-content deduplication. Stale task, interaction, dispatch, cancellation
operation or cancelled-attempt timer inputs do not restart work.

Node terminal output first becomes an immutable `ResultIntent`; the runner
emits SaveResult and waits in PendingResult. Only an exactly matching
ResultSaved unlocks successors or publishes terminal status. Structured tasks
also save a task-level result after node results: success exposes the terminal
node output; failure/cancellation retains saved business outputs. Failed nodes
stop successor admission and outstanding branches receive cancellation intent.
A result-intent write failure is retried by the effect executor; it must not be
reinterpreted as permission to run successors.

Delta is a Connector/EventHub projection concern and is ignored by the reducer.
Terminal Snapshot output is authoritative, including partial output on Failed
or Cancelled. AgentEvent::Failed alone carries no partial output; Connector must
emit a terminal Snapshot when partial output exists. Intents never serve as
mutable token accumulators.

Human replies validate caller scope, a nonempty authenticated principal,
message scope/task/session and the exact current interaction. Gateway access
policy remains responsible for authenticating/authorizing caller and target;
a runner checkpoint does not contain a principal owner. Agent continuation
reuses the same remote task/context handle with a new dispatch/operation/attempt.
For a subsequent independent Direct task only the remote context is reused.
ExecutionUnknown or CancellationUnknown blocks later tasks on that Direct
context, including tasks already queued; there is no automatic context reset.

Send and cancel effects carry immutable task/node correlation. Cancellation
uses its own per-node stable operation/dispatch and frozen binding, distinct
from caller command deduplication and from send identity. Confirmation must
match those identities and cover the handle's remote task. Unknown cancellation
fails explicitly; AlreadyTerminal awaits the authoritative terminal event.
Without a handle yet, cancellation waits for accepted/terminal evidence and
never certifies that remote work stopped. Unsupported cancellation emits no
network effect and remains pending until terminal evidence or deadline.
Timeout requests downstream cancellation when possible but saves failure, never
claims a remote abort. Node deadlines are cancelled when result save is
acknowledged; continuation cancels prior-attempt deadlines before scheduling
its new execution deadline.

## Verification

`cargo test --manifest-path apps/bcs/Cargo.toml -p bcn-session-runner`

Tests cover deterministic reduction/recovery, result barriers, continuation,
context blocking, bounds, sequence/fork/join/skip, persisted Judge selection,
partial failed snapshots, human correlation, cancellation and stale timers.
These pure unit tests establish no production persistence/failover/capacity
claim. No in-flight legacy session migration is provided.

Queued tasks carry an optional, serde-defaulted `terminal_intent`. Cancelling a
queued task freezes a Cancelled result and emits SaveResult without dispatch;
only matching ResultSaved removes it and publishes cancellation. Repeat cancel
is a no-op, and unsaved terminal items continue consuming queue capacity. The
scheduler skips them while retaining their immutable result intent. When Direct
execution becomes unknown, already queued tasks similarly save Failed results
with ContextBlocked; new submissions return ContextBlocked immediately. Prior
checkpoints without the queued field decode as `None`.

The normalized unique terminal is derived from validated source final_output:
Bot terminals must explicitly set final_output, final_output cannot have outgoing
transitions, and every node must reach the one terminal. Canonical terminal
HumanInput is also supported. No source-selected final output is discarded.

Validated ObservationFailed inputs return their error to the application; they
never freeze a node as authoritative remote termination. Worker derives the
bounded TaskFailure transition from that durable input/checkpoint (and canonical
dispatch handle if needed), then commits at the original input position. Reducer
execution remains pure and late/superseded dispatch inputs remain no-ops.
