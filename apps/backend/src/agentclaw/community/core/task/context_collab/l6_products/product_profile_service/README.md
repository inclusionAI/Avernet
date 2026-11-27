# Product configuration and durable rollouts

Owner: Backend `context_collab` L6. The [Service API](contracts.py) has exactly
five async methods, implemented by [ProductProfileService](service.py). Canonical
[ProductProfile](../product_profile.py) contains only `id/name/composition/stages`.
`composition` records exact L5 Method contracts/policies; public creation takes
only `name` and strict [YAML](stages_yaml.py) `stages`, installing the product-owned
interactive MultiRollout NEST Swarm composition. YAML is the sole, static workflow
DAG input; no planning Agent chooses or changes that graph. Required Methods are
selected, optional Methods are validated and retained but never implicitly activated.

## Context Boundary

```yaml
purpose: Authorized immutable product configuration and durable iteration orchestration.
provides:
  - ProductProfileServiceProtocol
  - ProductProfileService
  - InteractiveResult
  - StageRolloutDetails
  - ProductProfileRepository
  - default_composition
consumes:
  - ProductRuntimeAuthorization
  - RolloutExecutionPort
  - RolloutInteractionPort
  - StageSessionMessagesPort
  - MultiRolloutController
internal_dependencies:
  - agentclaw.community.core.errors
  - agentclaw.community.log
  - agentclaw.community.core.task.context_collab
  - agentclaw.community.core.task.task_runner.client
```

### Change impact

HTTP delivery injects the API reexport, not a concrete service. DI binds scoped
SQL persistence and fail-closed external capabilities. L5 owns allocation,
run observation, progress acceptance and rollout transitions; it never imports
L6. L4 values do not import L5/L6. Changes affect the product HTTP schemas,
worker/report consumers, runtime/message adapters, receipts and SQL snapshots.
Legacy v1 shapes have explicit names and cannot mutate v2 runtime tables.

### Caller scope and invocation identity

The five methods do not accept `context` or `request_id`. HTTP delivery uses a
shared authenticated dependency to bind the task-local G1
[`actor_context_scope`](../../g1_secure_govern/actor_context.py); library callers
must establish the same trusted scope around calls. An unbound caller is denied,
never treated as the stored owner. The scope is restored on success and error,
including nested/concurrent callers; singleton services do not retain an actor.
Tenant/environment keep their existing trusted scope and cannot be supplied in
the request body. The actor carrier is transport-free and does not authenticate
unverified claims itself.

Creates generate internal receipt identifiers. Repeating a create, even with
identical content, creates a new Profile/Collet; there is no content-based
deduplication. Interventions instead require a caller-generated `interactive_uuid`:

```python
await service.interactive(collet_rollout_id, interactive_uuid, interactive_context)
```

The UUID is an opaque, nonblank string bounded to 128 UTF-8 bytes. Keep it stable
for every retry of the same intervention; use a new UUID for a new intervention.
Within the trusted tenant/environment and actor, the same UUID with identical
rollout ID and feedback returns the persisted original `InteractiveResult`, even
after a successor rollout or later state change. Reusing it for different content
or a different rollout conflicts. Current authorization always precedes replay.
The result, state changes, successor identities and cancellation/advance intents
commit atomically; repeated calls cannot add another committed side effect.
Receipts survive service/process reconstruction. For ordinary serial profiles,
concurrent duplicates may compute
a proposal more than once, but the proposal capability must not mutate durable
state or dispatch/cancel runs itself.

`interactive` no longer accepts `expected_revision`: it captures the loaded
revision internally and uses SQL CAS. A competing distinct write still conflicts,
not silently reapplies a stale proposal. A duplicate losing CAS re-reads its
committed receipt before returning; it does not rerun the proposal. New UUIDs
cannot control a superseded rollout. `limit` remains bounded. This signature
change is breaking: remove old `context`/`request_id` and interactive
`expected_revision` fields; HTTP interaction bodies contain only `interactive_uuid`
and `interactive_context` (unknown body fields are rejected). No SQL migration
is required for that signature change; existing receipts and independent Bot progress replay semantics are
unchanged.

## Creation, queries and revision metadata

- `create_product_profile(name, stages)` creates the [default
  composition](configuration.py) internally and validates only the structural
  Method/YAML configuration. Runtime execution, evidence, adaptation, isolation
  and notification capabilities are not required until a Collet is created.
  Callers cannot supply or override a composition; the stored/returned
  ProductProfile still includes it.
- `create_product_collet` validates that the persisted Profile can be executed
  with the installed runtime and evidence capabilities, then commits the Collet,
  independent first rollout/stage IDs, actual MethodBinding/relations, owner,
  frozen policy/graph, receipt and initial advance intent together. Network IO is
  never inside that transaction.
- `query_collet_rollout` is read-only. Its list result carries technical `revision`,
  `current_rollout_id` and `stopped` attributes; these are not domain fields.
  HTTP returns those alongside `rollouts` in its Envelope data. `all_rollout=false`
  follows the persisted pointer, not list order.
- `interactive` authorizes and checks the UUID receipt before rejecting a new
  intervention on a superseded rollout. It invokes a trusted proposal capability
  on a detached copy and commits against the internally loaded revision.
  RE_DO/OPTIMIZE invalidate
  targets and successors and create fresh IDs. Accepted unaffected evidence can
  be reused explicitly, but not its session ID. SHUTDOWN persists a stop fact
  and cancellation intents; it does not manufacture another business status.
- `query_stage_rollout_details` loads and authorizes the stage before reading its
  stored real session/provider/Bot/owner/runtime route. An unbound session is
  explicit; provider errors never become empty messages or SQL Event fallback.
  Limits are 1–200; ordering is provider-defined and completeness is unknown.

Inline POST bodies are bounded to 2 MiB before JSON parsing (including streamed
requests); individual domain content remains bounded to 256 KiB.
Creation paths emit operation-scoped INFO diagnostics for start, validation,
authorization, MultiRollout setup, and persistence. Failures log the failed step,
exception type/message, and stack; raw stages/content are not logged, only bounded
byte sizes.
HTTP base: `/api/v1/collaboration/task/products`. Routes are `POST /profiles`,
`POST /collets`, `GET /collets/{id}/rollouts`, `POST /rollouts/{id}/interactive`,
and `GET /stages/{id}/messages`. Auth context and tenant/environment are not body
fields. Inline configuration and feedback bodies are redacted in error logs.

The [internal context-collab adapter](../../../../../adapters/http/task/context_collab/README.md)
also exposes `POST /api/v1/collaboration/task/context_collab/product_profiles`
and `POST /api/v1/collaboration/task/context_collab/collets`. They reuse the same
creation handlers, `name`/`stages` and `profile_id`/`content` schemas, authenticated
actor scope and body limits. The original `/products` routes remain supported;
these aliases do not change service bindings, storage or worker scheduling.

### Directed Method relation endpoints

`MethodRelationTemplate(source_method, target_method, relation)` describes a
source → target edge, not a generic parent/child relationship. `relation` owns
its meaning: for `NEST`, the source Method contains the target Method. Other
relations do not acquire containment semantics from their endpoint names.

Stored/returned composition objects and the standalone value codec use
`source_method` and `target_method`, not `parent_method`/`child_method`. Profile
creation no longer accepts a composition at all; HTTP rejects even a correctly
encoded caller composition as an extra field. The value codec still rejects
old, mixed and unknown fields. Persisted v2 Profile
reads alone translate the exact old tagged relation shape before strict decoding;
new writes use the new names, and reads never rewrite stored history. Ambiguous,
partial or unknown legacy shapes are rejected. No SQL DDL change is needed for
this endpoint rename. Existing L5 `parent_binding`/`child_binding` edge fields,
edge identities, direction and runtime snapshots are unchanged. Retained v1
storage also preserves its canonical bytes and digests; see [its storage
contract](../PROFILE-STORAGE.md).

Deploy compatible v2 readers before enabling new composition writes; old readers
cannot consume the new endpoint keys. Rollback requires stopping new writers and
retaining a compatible reader, not rewriting immutable configurations.

## Runtime and authority

[SwarmStagesPolicy](../../l5_methods/method_policies.py) selects execution/evidence
capabilities and explicitly chooses `stages` or `owner` overall acceptance. It
contains no graph. Legacy `SwarmWorkflowPolicy.workflow` is rejected on the new
creation path rather than silently overriding YAML. The explicit [migration
preparer](migration.py) checks candidates, dependencies and reviewed condition
mappings before extracting the legacy graph; it does not authorize migration.

[RolloutEngine](../../l5_methods/rollout_engine.py) runs at most one serial step.
A host schedules [RolloutIntentWorker.run_once](../../l5_methods/rollout_worker.py)
in the intended tenant/environment. The public app does **not** silently start a
new background scheduler. The same engine's `report(ProgressEvent, ReportContext)`
is the independent ingress; it is not a sixth ProductProfileService method.
The separate [RolloutProgressServiceProtocol](../../l5_methods/rollout_progress_protocol.py)
is DI-bound to that engine. Its [HTTP ingress](../../../../../adapters/http/task/product_progress.py)
is `POST /api/v1/collaboration/task/product-progress`, authenticated using the
existing signed, single-Bot principal verifier. The signed tenant scopes SQL;
actor and accountable owner come only from verified claims. The body carries
`event/request_id/attempt/expected_revision`; claims about attempts/revisions are
checked against persisted facts, not trusted as authority. There is no human
SSO fallback. A valid signature alone does not grant acceptance rights: the
installed evidence capability must authorize that verifier. The old commission
A2A report route remains v1-only; it cannot write v2 state.

Candidate authorization receives persisted Method/owner facts: configured
eligibility alone grants nothing. The driver commits the actual allocation,
attempt/lease and unknown-delivery fence before dispatch. A dispatch return only
binds a real session; real runtime observation is needed for RUNNING/RUN_FINISHED.
Unknown delivery is not blindly retried. Late dispatch after stop/replan is
recorded as a cancellation reconciliation intent. Pending/claimed/unknown and
requested-but-unconfirmed cancellation remain distinguishable durable facts.

Progress checks current rollout, attempt, lease, revision and trusted validator.
Success covers exactly all frozen conditions. Failure facts can have no artifacts.
Confirmed runtime failures stop the failed candidate; interactive MultiRollout
waits for human feedback before an Agent can propose a successor. Autonomous
MultiRollout still terminates on runtime failure instead of treating it as
automatic quality feedback. Unknown dispatch is never a confirmed failure.
Accepted facts, state and receipt commit under one aggregate CAS, competing
with interactive/stop. Separate owner/root Task
acceptance remains separate: `overall_acceptance=owner` leaves a fully accepted
rollout's Collet RUN_FINISHED; it does not fabricate a root Work transition.

The [OpenAPI runtime adapter](../../l5_methods/openapi_rollout_execution.py) and
[OpenAPI/BCS message adapters](session_messages.py) use existing Bot/session ports.
Identity resolution, participation/resource grants and evidence validation remain
injected governed capabilities. Public defaults deny execution/verification and
use a conservative no-op proposal; missing capabilities cannot produce a runnable
profile. A host must supply the real capabilities and schedule the worker.

## Storage, recovery and compatibility

> Partial implementation, no runtime cutover: the [storage foundation](../persistence/RELATIONAL-STORAGE.md)
> now provides five-table models, strict codecs, business creation/read UoW and isolated SQL tests.
> The [relational-storage design](../../../../../../../../specs/2026-11-27-context-collab-relational-product-storage/spec.md) proposes using five `ac_context_collab_*` business tables without separate control/identity/receipt/Outbox/slot tables, with Collet content/status and retained Profile/current-rollout associations but no runtime_collet_id/revision/execution_state columns; it records row-lock and feedback-event replay plans plus unresolved full-runtime recovery and protocol compatibility gaps. Execution/progress/feedback migration is not implemented and this does not replace the current runtime storage contract below.

Apply the [v2 DDL](../../sql/2026_11_26_product_runtime_v2.sql). Five base tables hold
complete configuration, explicit integer identity mappings, a versioned runtime
snapshot, operation receipts and durable intents; MultiRollout adds one tenant-wide
candidate admission table. The runtime snapshot contains
independent Collet/Rollout/Stage records, parent/current relationships, frozen
Method facts, attempts, leases, sessions and evidence. It is the **single** v2
execution authority, not a view assembled from process-local Plan/Stage dicts.
One CAS row intentionally bounds the first serial implementation; snapshots are
limited to 12 MiB and fail explicitly before MEDIUMTEXT overflow. No unbounded
history/performance claim is made. Stored graphs/Method prerequisites are checked
against the exact immutable Profile when reconstructing a runtime.

V1 Python consumers use `LegacyProductConfiguration`, `LegacyProductProfileKey`,
`LegacyCollet` and `LegacyArtifact`; there is no Python `ProductProfileDefinition`
alias or two canonical Artifact shapes. V1 storage retains its original JSON
discriminator, string IDs, wire primitives and Task authority. Its pure authoring
operation is compatibility-only, not the new create API. Compatibility owner:
Backend context_collab; review/removal deadline: **2026-12-31**. Removal requires
migrating remaining legacy product facades and their stored configurations.

Cutover must stop v1 writers and drain in-flight process-local Methods first.
Only a complete trusted snapshot may be imported for continuation. SQL child
Tasks alone, lost admissions/leases or unknown sessions fail closed. A proposed
string-ID/version → integer mapping must retain the exact source digest and
repository-allocated target ID; IDs are never parsed/hashes/list positions.
The migration preparer validates mappings but deliberately does not bulk-import
unrepresentable roles, governance, artifacts or runtime history. Those require
an explicit operator-reviewed adapter; existing history is retained, not erased.
New v2 state must never be sent back through a v1 writer. Rollback requires stopping
v2 writes and validating readers, not overwriting newer facts with a v1 snapshot.

## Verification and boundaries

The [focused suites](../../../../../../../../tests/community/core/task/context_collab/product_profile_service/)
use real isolated SQLite transactions and real execution/message adapter code,
with recording external Protocol doubles. They cover models/YAML, governance,
atomic rollback, receipts, CAS, late delivery, shutdown, successor history and
exact provider/session routing. They are **not** evidence of a live Bot/provider,
a target MySQL deployment, enterprise integration or remote cancellation.
The change's actual commands/results and remaining migration work belong in its
[delivery report](../../../../../../../../specs/2026-11-26-context-collab-protocol-optimization/delivery.md).


## MultiRollout NEST Swarm (default creation)

The [L5 runtime contract](../../l5_methods/MULTI-ROLLOUT.md) owns execution,
state, bounded strategies, isolation, unknown outcomes and recovery. New public
Profiles always select exactly `MultiRollout@1.0 NEST Swarm@0.2` through the
[product-owned default](configuration.py). The five-method Service API is otherwise
unchanged; existing persisted serial, parallel or autonomous Profiles are not
rewritten or silently reconfigured.

```python
# Profile creation only needs an authenticated actor; governed host capabilities
# are required when a Collet is created:
profile = await service.create_product_profile("coding", stages_yaml)
collet = await service.create_product_collet(profile.id, "Implement the feature")
```

The HTTP `POST /api/v1/collaboration/task/products/profiles` body likewise has
only `name` and `stages`. Supplying the removed `composition` field is a 422 error.
The response still contains the generated composition for inspection. This is a
breaking creation-contract change: Python callers must remove the positional or
keyword composition argument; HTTP clients must omit it rather than send null.
No DDL or stored-Profile migration is needed. Roll back clients and API versions
together; do not rewrite immutable configurations to restore an old request body.

### Static Swarm plan and human-gated iteration

- `SwarmStagesPolicy` executes the frozen YAML dependency graph as a static
  workflow. It does **not** use dynamic `SwarmMethodPolicy` planning or embed a
  second, independently writable legacy `SwarmWorkflowPolicy.workflow` graph.
  Stage candidates, dependencies and acceptance conditions come only from YAML.
- One candidate starts at a time. Confirmed execution failure (`RUN_FAILED`) or
  trusted acceptance failure (`ACCEPTANCE_FAILED`) enters `WAITING_FEEDBACK` and
  emits one idempotent human notification. Repeated worker ticks cannot start
  another rollout or invoke an adaptation Agent while waiting.
- Only after an authorized human submits feedback using `interactive(...)` does
  `stage-improvement-agent@1` receive the failed snapshot and feedback. Its typed
  proposal is adjudicated by the controller: `RE_DO` reruns unchanged, `OPTIMIZE`
  creates a bounded immutable plan patch, `SHUTDOWN` stops, and `DO_NOTHING`
  keeps waiting without extending the original deadline. Neither feedback alone
  nor a failure automatically authorizes a retry. Successful Swarm acceptance
  completes the controller without calling a planning/adaptation Agent.
- Default limits: four total rollouts, 3,600 seconds overall, 400 stage dispatches,
  eight strategy calls and two no-progress decisions; each human-feedback wait
  is bounded to 600 seconds and the overall absolute deadline. Every successor
  reruns the workflow with fresh candidate/session/workspace identities.

These defaults require installed exact capabilities: `openapi-bot/v1`,
`trusted-evidence/v1`, `stage-improvement-agent@1`, `per-candidate-workspace@1`
and idempotent feedback notifications. They identify host contracts, not import
paths, URLs or bundled production Agents. `ProductProfileRuntimeModule` still
binds unavailable external ports by default; creation fails closed before writing
anything when the host has not installed the required capabilities. Host adapters
must implement these exact references. No Agent/provider integration is fabricated.

### Query and interaction contract

Only MultiRollout profiles (including all newly created profiles) receive
`multi_rollout_control` in the HTTP Envelope alongside existing
rollouts/revision/current_rollout_id/stopped. The published
HTTP response schema describes the extension. It includes controller phase/outcome,
focus/winner, all actual candidate IDs, plan versions/digests, body Bindings,
trusted scores, hard budget usage, reservations/peak, absolute waiting deadline,
strategy-call dispositions/durations and cancel requested/unknown/confirmed facts.
No raw feedback or credentials are in this projection. Legacy HTTP JSON omits the
extension; `all_rollout=false` returns the focus only, not all active candidates.

Interactive UUID/actor authorization and its signature are unchanged. MultiRollout
adds a durable in-flight operation fence: same or competing adaptive feedback
cannot call two Agents at once. Committed results replay before focus/terminal
checks, with fresh authorization. New UUIDs cannot alter a terminal run. Parallel
interaction permits only SHUTDOWN; adaptive adjustments require WAITING_FEEDBACK.
Trusted SHUTDOWN can fence an in-flight selection/adaptation. Ordinary serial
profiles retain their previous proposal/CAS behavior.

Signed progress retains its separate request_id/attempt/**aggregate**
expected_revision. A valid non-focus candidate may report; concurrent sibling
reports can CAS-conflict normally. Follow the original-request replay and then
confirmed-stale/new-technical-ID retry procedure in the L5 guide.

### Storage, scheduling and deployment

New installations use the current [bootstrap DDL](../../sql/2026_11_26_product_runtime_v2.sql).
Existing v2 installations must stop writers/workers and apply the additive
[MultiRollout upgrade](../../sql/2026_11_27_multi_rollout.sql) **once** before running
new code: outbox logical-key/claim lease/fence columns/indexes and a tenant-wide
candidate admission table are required. No separate tables per plan/decision.
Existing JSON snapshots and receipts are not rewritten. This is not a claim that
the MySQL script has been executed against a production installation.

The host schedules both worker `run_once` and `check_deadlines`. Claimed leases
recover with fencing; unknown dispatch and unconfirmed cancellation retain quota.
Without provider query/idempotency reconciliation, an unknown attempt is not
redelivered. A late route remains cancellation work. A positive cancellation
acknowledgement or actual terminal provider observation settles external occupancy;
it does not manufacture a cancelled business event. No implicit timer/thread is
started by profile creation, and a configured Agent alone is not a running worker.

Rollback must first stop/drain/reconcile MultiRollout executions and preserve a
reader for `product-runtime/multi-rollout@1`; never point an old writer at this new
snapshot or delete quota rows for unknown external runs.
