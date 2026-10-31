# A2A outbound adapter

This delivery adapter owns conversion between external A2A events and BCS
outputs. Application policy, Bot ownership and persistence remain behind
service-api ports. SDK and HTTP types must remain inside this boundary.

The standard gRPC binding (`grpc_client`) uses the same pinned SDK models and
protobuf conversion library. Composition supplies a policy-checked channel and
trusted MOSN routing metadata; this low-level binding does not discover a target,
select endpoints, retry submissions, or cancel Tasks when a stream is dropped.
Its five operations preserve task/event models and bounded typed ErrorInfo while
excluding peer diagnostics. Bootstrap optionally injects validated `GrpcMeshConfig` into Card selection and
delivery. Only explicitly configured Card interfaces can select gRPC; the channel
dials a literal loopback MOSN endpoint and never falls back to direct dialing or
REST. Frozen route fingerprints partition contexts and reject retargeting old
Tasks. HTTP scopes omit that field to preserve existing context-key hashes.

The injected application `RunBudgetService` owns pure-policy invocation and
managed/cache deadline orchestration. This adapter passes canonical scope and
the expected deadline, then persists only its A2A correlation and validated
pending message before exposing interaction cards or issuing a continuation.
It does not call a Core contract via an import or facade re-export. Bootstrap
supplies the application implementation; no implementation selection occurs here.

Initial stream, async continuation, Task query/cancel and subscription share
bounded REST error parsing so ProtoJSON typed ErrorInfo survives SDK binding
gaps. A complete initial 400/401/403/404/405/406/415/422 rejection is Failed with
safe a2a_protocol error fields, not Unknown or retryable. 408/409/429, 5xx,
timeouts and incomplete responses remain uncertain; only negotiated identical
submission retry is permitted. Query/cancel/continuation failures retain safe
HTTP status and recognized reasons in request errors without inventing a Task
terminal. Arbitrary peer diagnostics/metadata/unknown details are never forwarded.
The initial rejection emits one terminal through normal ingestion and returns
an explicit rejection; later scheduler observations cannot revive that terminal.
It adds no retries, token-path database writes or queue scans.

Repeated exact cancellation of a durably Canceled Run reuses that confirmed
outcome after scope checks and performs no RPC. Completed/Failed work returns no
aborted Run. A typed TASK_NOT_CANCELABLE response triggers one exact GetTask,
within the same cancellation deadline, to distinguish a terminal race from an
unconfirmed cancellation. Completion is never relabeled Canceled and cancellation
does not synthesize Final without the driver's authoritative text calibration.

An unspecified Task state queries the exact Task before cancellation. A queried
known state resumes normal projection with the already-ingested prefix committed,
so calibration cannot replay text. At most three such state queries are allowed
per live projection/Run; repeated unspecified frames cannot trigger unbounded
network or credential-store reads. Normal text tokens add none. This is separate
from the bounded disconnect/subscription recovery budget and has no DB cursor.

Disconnect recovery allows three consecutive failed recovery rounds, within the
Run deadline. Only a validated, successfully ingested event from the resumed
stream resets the count; HTTP subscribe success or GetTask success alone does
not reset it. Repeated healthy disconnections therefore do not consume a single
lifetime quota. Confirmed cleanup terminals also mark the local transport Terminal
only after their error terminal is accepted by event ingestion.

Every lane-releasing Final/Error/Aborted is preceded by a successful terminal
correlation CAS, including standalone Message completion. Ingestion may release
the managed lane before its callback returns; the next Send must already see
the old correlation as terminal. A failed CAS publishes no terminal.

After that commit, a failed terminal notification gets at most three ingestion
attempts, 250ms apart, with identical Run/scope/payload. This relies on managed
ingestion's durable terminal guard; it does not replay text, repeat the CAS,
resubmit a remote request or cancel a known terminal Task. Exhaustion preserves
the confirmed remote terminal, attempts the existing scoped tracking-lost
transition for the managed delivery, propagates failure and does not mark the
local transport Terminal. The phase-one queue remains conservative when local
publication cannot be confirmed. There is no restart outbox/replay claim.

The normal terminal path retains its existing one correlation CAS and one
ingestion call; no token-path database work is added. Failure adds at most two
calls to the existing terminal ingestion pipeline and one tracking-lost
transition/invalidation. Each pipeline call retains its own storage retry policy;
this does not bound those internal retries or claim one call equals one SQL.
No additional correlation writes, queue scans or remote RPCs occur on notification
retry. The queued public HTTP story and SQL-backed delivery conformance tests
cover the handoff ordering and persistence/ingestion failures.

Phase one requires a single BCS instance and an enabled, serialized Bot/Session
queue. The active-scope read plus Run insert is not a cross-instance atomic
admission primitive; direct parallel sends are outside that deployment contract.
Unknown outcomes keep the scope blocked without deadline-based auto-expiry.
Restart does not automatically recreate stream owners. Existing exact cancellation
can confirm a known Task's terminal outcome; unknown Task IDs or unconfirmed
outcomes have no dedicated manual-unlock endpoint in phase one.

Text projection tracks each Artifact independently across BCS tool boundaries.
Projection updates must be committed only after event ingestion succeeds.
The module implements Card validation/fetch, SDK event projection and the
production `BotDeliveryPort`. `HttpA2aCardFetcher` implements `A2aCardPort`: bounded
public JSON fetch, no redirects/proxy/credentials, configured DNS/IP policy pinned
per attempt, TLS outside explicitly permitted loopback, and explicit rejection
of unsupported no-cache/no-store declarations. Required extensions are injected
by composition; unsupported capabilities never silently downgrade.

Task/context correlation and credential resolution use service-api repository
ports. Bootstrap injects concrete stores, outbound policy, Run context and a weak
event-ingestion reference. The adapter freezes route and extension negotiation at
submission. It emits trusted local routing fields, never remote routing claims.
Tool observations are negotiated by their versioned URI, decoded on staged state,
and deduplicated by tool-call ID and phase. They preserve Artifact text cursors.
The single-instance background driver owns the selected REST/SSE or gRPC stream, bounded
query/resubscribe recovery and remote Task cancellation; no restart token cursor
or multi-instance lease is claimed. Structured Input exec uses the waiting owner
described below; no ask_user engine capability is claimed.

The pinned Rust SDK subscription binding is overridden with canonical POST and
opaque Task-ID path encoding; SDK request/event types remain the wire authority.

Contract: docs/specs/2026-10-28-bcs-a2a-grpc/plan.md in the repository root.


Task query, cancel and subscribe error decoding accepts nested ProtoJSON
error.details and the pinned SDK's flat details shape. Typed ErrorInfo reasons
preserve protocol distinctions; arbitrary text or HTTP status alone does not
invent a Task error. Error bodies are bounded and excluded from error strings.

`structured_exec` validates the pinned Structured Input exec profile against
the native Java fixture. It preserves the full schema and accepts only the
supported object/decisions enum-or-const subset, rejects unimplemented validation
keywords, checks exact Task/context, request identity, immutable tool argument
bindings, expiry and size limits. ProtoJSON integral doubles are supported.
Replies use standard DataPart metadata and original Task/context. Task metadata
receipts prove acceptance only; tool execution is not inferred. Card validation
checks required Structured Input draft/profile parameters; optional incompatible
profiles are not negotiated. Core passes complete typed declarations to avoid
losing these parameters. Bootstrap advertises this exact profile and wires the
production interaction service, generic budget Core and managed-delivery port.

Each live Run owns a bounded resolution channel (64 entries) and its frozen
target. The ingestion/interaction service references are weak to avoid cycles.
The driver publishes cards only after deadline/context/correlation persistence,
drops the transport stream on input wait, retains its projection and logical slot, and
does no polling or resubscription until resolution/cancel/expiry. Expiry and
unconfirmed acceptance fail closed and cancel the original Task. Every callback
and response is checked against canonical local scope and frozen Task/context.

Partial decisions keep the original wait budget; the final decision restores
execution charging before standard SendMessage(returnImmediately=true). A Task
receipt must confirm the exact request/tool/decision. On success the existing
interaction service closes that decision's card with receipt=accepted before
terminal calibration can invalidate it. This is receipt, not execution success.
After the final receipt, bounded query/subscription recovery resumes the same
projection; transient subscription failure does not resubmit the decision.

Budget/pending snapshots use the existing Run JSON/CAS, with backward-compatible
absent fields on older records and no migration rewrite. Budget transitions
add managed CAS and bounded cache updates at pause/resume, never per token.
One continuation can perform at most three receipt queries (four checks including
its response), followed by the existing three-attempt stream recovery. Pending
updates write correlation once per confirmed human patch, not per token.

A2A association contract: explicit-column run bindings can share one remote Task
within a trusted scope. Repository CAS returns the committed projection and
fences both run and shared Task revisions. Pending input is Task-owned; budgets
and local states remain run-owned. Run-scoped cancellation rejects a shared Task
with another active binding. See docs/specs/a2a/task-bindings-v1.md.


Standalone A2A Message responses preserve an optional nonempty `contextId`.
BCS validates it against the submitted context before publishing text, retains
an existing context when the response omits it, and atomically saves a newly
established context with the Completed run before publishing Final. Taskless
runs keep `remote_task_id` and `task_version` absent. The existing
`request_context_id` column stores this identity; no schema change is needed.
The terminal transaction adds at most one scoped context upsert, with no
per-token writes or additional recovery scan. A failed binding CAS rolls back
the context upsert as well. Contexts remain isolated by the full Bot/Session,
Provider/interface and route scope.
