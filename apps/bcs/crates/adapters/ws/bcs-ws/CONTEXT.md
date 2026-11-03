# bcs-ws Context

## Provides

- WebSocket delivery adapter for BCS.
- Bot runtime entry under `src/bot/`.
- Workbench/Web entry under `src/web/`.
- Session-bound Workbench connect delivery that delegates current Session
  authorization to the V1 group-session connection service; legacy
  user-bound `/ws` connect remains on the Workbench session service.
- Focused `/openapi/v1/collaboration/messages/ws` Upgrade boundary that verifies
  the query credential before switching protocols and binds its immutable
  tenant/User/Group/Session scope into the existing Workbench connection loop.
- Shared connection-state helpers under `src/shared/`.
- Implementations of `BotDeliveryPort` and `FrontendDeliveryPort`.
- Task control RPCs accept explicit Session IDs and preserve V2 session-scoped
  Group IDs. See [task RPC Session scope](docs/task-rpc-session-scope.md).
- Opaque, per-connection identities and pinned send/abort operations. Selection
  checks the original identity under the registry lock and never redirects to
  a replacement connection. Tokens are not connection identities.
- Protected delivery (bot-authority spec §8.2 — 已准入输出不追溯):
  workbench connects may explicitly select a view (`view_actor_id`:
  Human self default, another Human rejected, a Bot id is a selection
  parameter re-authorized by the application). Delivery authorization
  is decided ONCE per business operation per recipient connection at
  the operation's accept point, via the narrow `OutputAdmissionPort`
  (the application resolves each candidate's delivery view against
  the strict Group/Session snapshots in ONE batch; the adapter
  freezes it as a read-only in-process permit keyed by connection
  instance). Frames carry trusted producer metadata
  (`operation_id` + `run_id` + `terminal`) and are checked at the
  ENQUEUE boundary against the frozen permit — never per frame at
  the writer, never a retroactive revocation: a mid-operation
  transfer, manager-edge removal, membership or scope change leaves
  the admitted stream flowing; NEW operations, new subscriptions,
  reconnects and replacements re-admit against current facts without
  inheriting any earlier permit. Independent resource events
  (member/system/metadata) are authorized per event against current
  facts and skipped fail-closed on a read error without touching any
  stream. Run terminal ≠ operation complete: the application
  registers the operation's complete Send-run set before any
  downstream I/O; a terminal closes only its own stream, and the
  operation turns Draining once EVERY registered stream is terminal
  (duplicate and pre-registration terminals count once). The
  producer's visibility facts are likewise frozen by the message
  flow from the admission-time Group/Session snapshot before the
  first callback, so a role or membership change between accept and
  first frame never reclassifies admitted output. Terminal frames
  are typed and order-preserving: a full slow-consumer queue takes
  ONE shared bounded backpressure budget, and a terminal still lost
  retires the saturated connection (socket closes so the client
  recovers via reconnect) — never a silent live stream without its
  final. Permits distinguish prepared vs accepted: unused
  preparations expire on a short prepare-timeout while an accepted
  operation is NEVER reclaimed by age (a legal queue wait has no
  bound) — only explicit end-states reclaim it: every registered
  stream terminal, every recipient connection closed, or the
  application releasing output-less streams after transport failure
  (their operation binding is dropped with the tombstone). Committed
  managed work finishes without the sender: canonical run ids are
  pre-assigned and the FULL managed ∪ direct stream set is
  registered BEFORE the durable admission commits, so a worker
  never runs an unbound admitted row and a cancelled sender cannot
  leave the operation behind as a prepared-only permit. Releases
  are exact: commit failure, duplicates and admission-time
  rejections release only their fresh identities; a `delivered=false`
  explicit rejection releases only that run; a transport-uncertain
  Err releases nothing; an aborted send releases only the
  never-started runs — already-delivered siblings keep their
  operation until their own terminal. The Session
  last-Run fallback serves Internal sinks only — public Workbench
  output never borrows another Run's permit lineage. Permits live
  in process
  memory (no Redis, no cross-process replay): restarts, leadership
  changes and reconnects re-admit against current facts.

## Consumes

- `bcs-service-api` traits and DTOs.
- `bcs-protocol` wire frames.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

- `service-api/*`
- WebSocket framework crates such as `axum`, `tokio`, and `futures`

## Forbidden dependencies

- `bootstrap/bcs`
- `services/*` concrete crates
- Legacy DingTalk runtime modules
- Auxiliary Ding logger crate
- generic `channel/` abstractions

## Configuration

- Bootstrap injects delivery services, auth mode, connection-related limits,
  and the shared group-session connection service used by token verification
  and connect-time authorization.
- The adapter must not select concrete routing or message-flow implementations at runtime.

## Runtime ownership

The existing Bot response tracing boundary explicitly supplies its trace ID as
log-correlation data to bcs-observability. Callback handling restores the outer
scope on completion or cancellation; log correlation owns no span handles.

The adapter owns WebSocket streams, `mpsc::Sender`, connection registries,
frontend envelope stamping, and disconnect cleanup. It does not own routing or
message lifecycle business decisions.

Legacy Bot events with a registered Group Run restore an omitted Session from
the accepted Run context before the unregistered-event default-Session fallback.
Group-only Runs retain their original Group-as-session identity; real Session
ids remain exact. Explicit Session or Bot/Group mismatches are rejected, never
repaired by borrowing the accepted scope. This reuses the existing one Run
context read per legacy frame (Memory or configured cache); it adds no DB,
authority or extra cache lookup. Unregistered legacy events, direct A2A session
keys and the V3 protocol keep their existing normalization rules.

Successful send ACKs are passed to the message-flow application before alias
projection. The application owns durable delivery/run identity correlation;
the adapter does not update delivery tables.

Rejected responses with an authenticated active Group run use its canonical
scope to invoke MessageFlow chat/error. Scope lookup failures retain the run and
propagate an error; absent scope keeps direct handling. State-machine correlation
and pending abort/one-shot responses retain their existing handling.

## Tests

- `cargo test --package bcs-ws --manifest-path src/bcs/Cargo.toml`
- `cargo check --workspace --all-targets --manifest-path src/bcs/Cargo.toml`

## Leadership change close contract

Bootstrap drives transport-only `ConnectionEpoch` gates on the Bot and Workbench
registries. Every handler subscribes before processing frames, including sockets
which never register and session-bound Workbench sockets. Cancellation stops new
frame dispatch and independently interrupts the outbound writer, even if its
business queue is full. The writer attempts WebSocket Close **1012** with reason
`leadership_lost`, bounded to one second. Normal disconnect/subscription cleanup
still runs; an already executing application call finishes before handler cleanup
(no unsafe cancellation of a partly completed registration).

This is a retryable transport interruption, not `bot.kicked`, token expiry or
credential revocation. Clients retain credentials and use their existing
reconnect/backoff policy through the master-routing ingress. Promotion creates a
fresh epoch and never revives cancelled sockets. There is no cross-replica
forwarding or replay guarantee for in-flight messages. API/frame DTOs are unchanged.

Legacy ingress rejects registered terminal/expired Runs using its existing
context read, without an additional authorization lookup. Missing output lineage
also fails closed in MessageFlow, including cleaned aliases. Legacy agent/error
normalizes to the same chat error command as V3, so settlement, task failure,
terminal backpressure and per-stream permit cleanup share one lifecycle. No
extra chat final is required; running sibling streams retain their permits.

Unknown or cleaned nonmanaged Run output is also fenced at MessageFlow's event
entry, not only at frontend publication. A completed accepted alias whose active
context was removed cannot re-enter persistence/relay through the legacy scope
fallback. Direct A2A alias projection uses the composition-shared tracked context
and never creates a second independent output identity for an alias.

## Service Session policy retirement

Service Sessions retain creation, authorization, completion, completion events,
activation fencing and reactivation. The Group `service_spec` policy, Service
Session timeout scanner, external completion callbacks and callback recovery are
retired. Reactivation requires a Completed Service Session; it has no callback
status prerequisite. State Machine Node deadlines, progression/IM recovery,
Provider callbacks and Event subscriptions retain their existing contracts.
Legacy Group creation accepts and discards `service_spec` for Backend compatibility, and the legacy settings
endpoint returns 410. Service invocation needs no Group policy marker. Existing
Group policy and Session callback columns, indexes and values remain unchanged;
runtime stores no longer read or write those fields. This retirement adds no
schema migration. Stop old binaries to prevent legacy scanners and callbacks
from continuing to run. See `apps/bcs/specs/2026-10-27-retire-service-session-policies/spec.md`.

Speaking-lease acquire/preempt/release/current reauthorize the authenticated
Human through Workbench chat authorization on every command before touching the
lease store. An owned Bot does not stand in for Human participation. Absent
participants fail the same sender gate as chat.send. Session-bound credentials
cannot address another Group; the bound Session supplies authorization context.
The existing group-keyed lease wire/storage contract is unchanged. Authorization
adds one group-service read per command (existing group cache applies); scoped
Session authorization also reads that Session. No new membership cache is added.
For a non-member, the reused authorization also checks owned Bots: up to the
Group's Bot participant count registry reads (memory hits normally, database
fallback on misses), before refusing a non-participating Human sender. A scoped
Session fallback can read the Session twice. These are existing authorization
costs now incurred by lease commands; no batching/cache rewrite is included here.
The focused tests use memory stores and do not establish database load capacity.
