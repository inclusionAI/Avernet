# bcn-runtime-api

Application/core Service APIs and outbound business ports.

See [BCN runtime contracts v1](../../../../../docs/architecture/bcn-runtime.md)
for lifecycle, limits, compatibility, isolation and composition boundaries.

Run focused tests with `cargo test -p bcn-runtime-api` from `apps/bcs`.

## Repository recovery contract correction (pre-release v1)

`TaskQueryStore` reads admission-owned metadata and snapshots, including accepted
nonterminal tasks. Result adapters delegate task queries rather than invent
principal/session/target metadata from a ResultIntent. `SessionStore::load_spec`
and `scan_active` discover admitted sessions before first activation;
`deactivate` removes wake only under current ownership and idle-state checks.

`CommandBus::publish` now requires current OwnershipFence and CommittedEffectRef,
returning the immutable committed Command. Callers derive stable IDs through
`effect_command_id`; no caller-provided effect body is publication authority.
Receive is cursor-bearing and bounded; consumers advance even empty eligible
pages and cycle at end. Connector commands must have a persisted dispatch before
ACK. `DispatchStore::claim` takes a committed CommandId; read/scan_active expose
DispatchContext (command/session/task/node) alongside exact immutable operation,
so ACK does not erase restart routing. Terminal records reject later mutations;
exact terminal receipt retries remain idempotent.

`ConnectorProjection` replaces the reset's caller-constructed TaskView. It is
node-scoped output with expected task cursor, correlated through DispatchContext.
The store aggregates peers and allocates task-global epochs independently of
per-dispatch fences. Delta's supplied cursor is an expected current cursor.
Atomic projection/snapshot/Reset publication eliminates partial reset state.

InboxPort is a trusted internal capability. Only Worker may forward the actual
ResultStore return as ResultSaved, after a matching immutable intent was committed.
The store verifies full payload/session association and atomically confirms the
saved receipt with inbox/wake; protocol clients cannot submit arbitrary inputs.
These signature/record changes require corresponding new-runtime consumer
updates and explicit offline migration/discard of pre-release records missing
required context. The legacy runtime is unchanged. See the runtime-store README
for persistence semantics, bounds, cost accounting and production limitations.

## Application lifecycle additions

The new application consumes `RuntimeLiveness` (RuntimeResult) through the
store-layer `LivenessAdapter`; infrastructure NodeLivenessPort remains separate.
CommandBus.receive requires CommandConsumerKind and filters before leasing, so
Worker result/judge and Connector send/cancel consumers cannot steal work.
InboxPort.find_input is a trusted bounded dedup lookup retained after reclamation;
Gateway authorizes the original task before using it. TaskEventStore.read_projection
resolves a dispatch's own canonical node and rejects superseded commands.
SessionStore.begin_recovery/recovery_status persist poison metadata independently
of checkpoint decoding; only a progressing checkpoint commit resets it.

Default runtime drain calls start/poll a drain without blocking to the deadline;
run polls them with an injected clock and shutdown channel. Application tick/step
entrypoints live on the implementations for deterministic scheduling. No
constructor selects a concrete backend/transport. Consumers of the unreleased v1
receive signature must pass the explicit kind. There are no other repository
implementations in this workspace; production implementations must implement the
new methods before composition. This does not change legacy runtime contracts.

Connector publication callers must retain the entire operation (ID, expected
cursor, payload) across ambiguous failures. RuntimeStore checks publication
dedup before returning `Conflict("task projection cursor changed")`; this
specific response permits rebuilding against a fresh cursor. Generic conflicts
and storage errors do not prove the original publication was uncommitted and
must first retry the identical operation.

Task listing returns TaskPage with an exact pre-pagination total_size. Gateway
calls every AccessPolicy.authorize_list before querying rows or counts, then
retains per-task List checks. The collection grant covers all tasks owned by the
caller within the target/optional session and their count; policy implementations
must keep item visibility consistent. The default hook delegates target List
authorization and rejects targetless queries. Supporting targetless collection
listing requires an explicit hook implementation, not a first-page inference.

GatewayService.submit_with_receipt adds reply-aware SubmitOutcome while preserving
submit. Its reply_interaction_id is the actual ID frozen in durable acceptance,
not inferred again from current questions. The default implementation rejects
reply requests before submit unless the implementation provides receipt support;
ordinary submission remains compatible. DefaultGateway uses existing authorization
and payload/idempotency validation, then one bounded Inbox.find_input point read.
No new persistence row, historical scan or process-local reply cache is introduced.

## Admission ownership and diagnostics

AdmissionStore.admit atomically establishes/checks the authenticated session owner
from AdmissionRequest.caller. Existing receipt replay remains compatible; ownerless
legacy headers deny fresh admission but permit reads/checkpoint recovery. No
public DTO field was added. This is a strengthened repository behavior contract,
so alternate AdmissionStore implementations must apply the same CAS requirement.

RuntimeObserverPort is an additive optional outbound port, with synchronous
session_quarantined(SessionId, SessionRecovery) notification. Implementations must
be bounded and return promptly without external I/O. DefaultWorker.with_observer
and bootstrap build_with_observer opt into diagnostics; existing constructors and
build remain compatible. Metadata-only notification occurs after the durable
quarantine transition/new fence and can be lost on a process crash before the
callback. Existing quarantine containment is unchanged; delivery is not guaranteed.
