# Product configuration and durable rollouts

Owner: Backend `context_collab` L6. The [Service API](contracts.py) has exactly
five async methods, implemented by [ProductProfileService](service.py). Canonical
[ProductProfile](../product_profile.py) contains only `id/name/composition/stages`.
`composition` selects exact L5 Method contracts/policies; strict [YAML](stages_yaml.py)
is the sole stage DAG input. Required Methods are selected, optional Methods are
validated and retained but never implicitly activated.

## Context Boundary

```yaml
purpose: Authorized immutable product configuration and durable iteration orchestration.
provides:
  - ProductProfileServiceProtocol
  - ProductProfileService
  - InteractiveResult
  - StageRolloutDetails
  - ProductProfileRepository
consumes:
  - ProductProfilePublicationAuthorizer
  - ProductRuntimeAuthorization
  - RolloutExecutionPort
  - RolloutInteractionPort
  - StageSessionMessagesPort
internal_dependencies:
  - agentclaw.community.core.errors
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

## Creation, queries and revision metadata

- `create_product_profile` checks the existing independent publication authority,
  validates the Method composition and YAML jointly, requires installed execution
  and evidence contracts, and commits the complete immutable configuration.
- `create_product_collet` commits the Collet, independent first rollout/stage IDs,
  actual MethodBinding/relations, owner, frozen policy/graph, receipt and initial
  advance intent together. Network IO is never inside that transaction.
- `query_collet_rollout` is read-only. Its list result carries technical `revision`,
  `current_rollout_id` and `stopped` attributes; these are not domain fields.
  HTTP returns those alongside `rollouts` in its Envelope data. `all_rollout=false`
  follows the persisted pointer, not list order.
- `interactive` checks exact current rollout/revision before invoking a trusted
  proposal capability on a detached copy. Replays are checked after current G1
  authorization and before stale-version rejection. RE_DO/OPTIMIZE invalidate
  targets and successors and create fresh IDs. Accepted unaffected evidence can
  be reused explicitly, but not its session ID. SHUTDOWN persists a stop fact
  and cancellation intents; it does not manufacture another business status.
- `query_stage_rollout_details` loads and authorizes the stage before reading its
  stored real session/provider/Bot/owner/runtime route. An unbound session is
  explicit; provider errors never become empty messages or SQL Event fallback.
  Limits are 1–200; ordering is provider-defined and completeness is unknown.

Inline POST bodies are bounded to 2 MiB before JSON parsing (including streamed
requests); individual domain content remains bounded to 256 KiB.
HTTP base: `/api/v1/collaboration/task/products`. Routes are `POST /profiles`,
`POST /collets`, `GET /collets/{id}/rollouts`, `POST /rollouts/{id}/interactive`,
and `GET /stages/{id}/messages`. Auth context and tenant/environment are not body
fields. Inline configuration and feedback bodies are redacted in error logs.

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
Runtime failures stop scheduling. Accepted facts, state and receipt commit under
one aggregate CAS, competing with interactive/stop. Separate owner/root Task
acceptance remains separate: `overall_acceptance=owner` leaves a fully accepted
rollout's Collet RUN_FINISHED; it does not fabricate a root Work transition.

The [OpenAPI runtime adapter](../../l5_methods/openapi_rollout_execution.py) and
[OpenAPI/BCS message adapters](session_messages.py) use existing Bot/session ports.
Identity resolution, participation/resource grants and evidence validation remain
injected governed capabilities. Public defaults deny execution/verification and
use a conservative no-op proposal; missing capabilities cannot produce a runnable
profile. A host must supply the real capabilities and schedule the worker.

## Storage, recovery and compatibility

Apply the [v2 DDL](../../sql/2026_11_26_product_runtime_v2.sql). Five tables hold
complete configuration, explicit integer identity mappings, a versioned runtime
snapshot, operation receipts and durable intents. The runtime snapshot contains
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
