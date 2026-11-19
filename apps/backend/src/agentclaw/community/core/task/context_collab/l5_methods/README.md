# L5 Method contracts and operations

Owner: Backend `core/task/context_collab/l5_methods`. L5 composes L4 primitives
into Methods and owns their profiles, bindings, composition, publication and
admission constraints. [L6](../l6_products/README.md) selects these contracts to
compose products; L5 does not import L6 or concrete product definitions.
This is the local implementation boundary, not a new ACCS wire operation.

## Context Boundary

```yaml
purpose: Method contracts and operations over L4 primitives, independent of product composition.
provides:
  - MethodProfile
  - MethodBinding
  - MethodPolicy
  - OpenMethodPolicy
  - AdmissionRole
  - OpenAdmissionPolicy
  - OpenPublicationProfile
  - BotSelfApplicationAdmission
  - OpenMethodOpsServiceProtocol
  - SwarmMethodOpsServiceProtocol
  - stage_role_candidates
  - SwarmController
consumes:
  - MethodStoreProtocol
  - CollaborationReader
  - ContextServiceProtocol
  - TopLevelBindingRecovery
  - SwarmPlanningService
  - StageExecutionService
internal_dependencies:
  - agentclaw.community.core.task.context_collab.errors
  - agentclaw.community.core.task.context_collab.l2_agent_network
  - agentclaw.community.core.task.context_collab.l4_context_primitives
  - agentclaw.community.core.task.context_collab.l5_methods
  - agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context
  - agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization
  - agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report
  - agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines
  - agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine
  - agentclaw.community.core.task.context_collab.persistence.method_store
  - agentclaw.community.core.task.context_collab.swarm_planning
  - agentclaw.community.utils
```

### Change impact

Method contracts affect generic Method services, L6 composition/validation,
product definitions, product services and DI authorization/admission adapters.
Preserve pinned versions and runtime authority; changing Python ownership alone
does not migrate ProductInstances, alter stored facts or authorize participation.

## Open policy ownership

- [method_policies.py](method_policies.py) defines `MethodPolicy` and
  `OpenMethodPolicy`. L6 may select a policy in `composition.policies` only for a
  required Method; it does not define another Method policy type.
- [publication.py](publication.py) defines `OpenAdmissionPolicy`,
  `OpenPublicationProfile` and `BotSelfApplicationAdmission`. Shared constraints
  require named, required role mappings, positive capacity and a typed
  participation outcome. Publication permits delivery only after `ADMITTED` and
  preserves `propagate_without_rollback` failure behavior.
- `OpenAdmissionPolicy` also declares who executes the admission decision and
  which bounded rule selector describes the judgement:
  `decision_actor`, `decision_agent_ref` and `decision_rule`. The
  `accountable_role` / `product_policy` defaults preserve existing owner
  behavior. `configured_agent` selects the exact `decision_agent_ref`; it is
  the admission decision identity, not an initial task executor or role
  binding. `capability_evidence` is the currently supported configured rule.
  These declarations do not bypass L5 G1/G4 execution: product composition must
  authorize and orchestrate the configured Agent's explicit `screen`/`admit`
  calls.
- `AdmissionRole` is L5's read-only structural view of role name, requiredness
  and participant source. L6 `ScenarioRole` satisfies it without an upward
  import, including in type-checking-only annotations.
- `BotSelfApplicationAdmission` retains designated Bot self-application,
  `COLLET_PARTICIPANT`, capacity 1, an executor applicant and accountable owner.
  These values do not themselves submit, screen, admit, select or start Work.
  Product services and DI still orchestrate those explicit operations.
- The commission-specific `DesignatedOpenAdmission` compatibility strategy stays
  in [the product](../products/task_commission/publication.py). Other products
  can select L5 policies without importing commission templates or services.

The former `l6_products.method_policies`, `l6_products.publication` and commission
`BotSelfApplicationAdmission` imports remain identity reexports. Internal
consumers import the canonical L5 definitions directly. Existing constructor
fields, validation, strategy IDs, installed versions, wire shapes and persistence
remain unchanged.

## Verification

- [Policy regressions](../../../../../../../tests/community/core/task/context_collab/test_l5_open_method_policies.py)
  cover standalone structural role validation, immutable policy values,
  self-application bounds, dispatch gating and legacy import identity.
- [L5 architecture gate](../../../../../../../tests/community/architecture/test_context_collab_l5_dependencies.py)
  rejects L6/product dependencies, including relative and type-checking imports.
- [L6 profile creation tests](../../../../../../../tests/community/core/task/context_collab/test_l6_product_profile_operations.py)
  cover policy selection, role mappings and shared creation/catalog validation.

## Swarm stage handoff and role matching

Swarm's Stage DAG, not a second product workflow stage variable, owns internal
execution order. `start_plan`, successful `complete_stage`, and `resume_plan`
project dependency satisfaction through the registered G4 `swarm_stage` machine:
only PENDING stages whose predecessors have all SUCCEEDED become READY. A
paused plan defers this projection until resume; failed predecessors do not
satisfy dependencies. Projection is scoped to the current plan, binding and
Collet. It does not automatically complete the plan or the L4 Task.

[`operations/swarm/matching.py`](operations/swarm/matching.py) exposes a read-only
`stage_role_candidates` helper over actual L4 Participation/RoleBinding facts.
Stage `requirement_refs` using the local `<collet>/role-bindings/<role>/<participant>`
reference format require the same Agent Participant to satisfy every referenced
binding. Unknown/foreign role references fail closed. Other opaque requirements
are not evaluated by this helper. Allocation rechecks these role requirements
and requires an `AGENT` target and an `A2A_AGENT` execution profile that both
reference that exact Participant. Stages without role references retain their
existing allocation behavior.

Matching is not admission, an allocation, or a resource grant. Having a worker
role does not authorize Swarm governance operations: the existing G1
owner/designated-executor checks still apply. External computation and authority
remain separate; worker reports pass evidence validation before the protocol
handler explicitly calls `complete_stage`. There is no artifact predicate engine or external
Agent transport adapter in this increment.

Evidence:
- [Dependency regressions](../../../../../../../tests/community/core/task/context_collab/test_swarm_stage_dependencies.py)
  cover fan-in, plan isolation, paused completion/resume and failed predecessors.
- [Role regressions](../../../../../../../tests/community/core/task/context_collab/test_l6_fixed_agent_roles.py)
  cover scoped assignments, exact local refs and role intersections.
- [Multi-role coding journey](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end_swarm.py)
  exercises real DI/SQLite/L4/G1/G4 through L5 in one numbered end-to-end case:
  explicit product planning, unified Stage/worker decisions, worker reports,
  independent child Work, five different artifacts and separate root acceptance.

## Bounded Swarm coordination

[`SwarmController`](swarm_controller.py) owns an in-process loop. The caller
supplies only `collet_id` through DI's `AssistedBuilder[SwarmController].build`;
ports are constructor-injected, not obtained from a global injector/registry.
The composition adapter resolves the pinned ProductProfile's explicit
`swarm_plan_service_ref` and the one ACTIVE Swarm binding. An omitted or unknown
capability fails closed. No coding names, L6 imports or filesystem predicates
belong in L5.

Product Instantiate creates/binds the Collet first; explicit L4 `create_task`
then creates unstarted root Work while retaining the installed selection and
executor bindings. Construction accepts that root Task in CREATED or
IN_PROGRESS and makes no Work transition. The initial `wake()` starts CREATED root Work through the
injected L4 `ContextServiceProtocol.start` (G1/G4 and SQL CAS), preserving its
identity/objective, then enqueues unified planning. Subsequent wakes reconcile
already-started Work without another start; pending wakes coalesce. Start errors
propagate before planning is queued. This explicit controller entrypoint does
not add automatic dispatch to the generic L4 Work.start contract.

The outbound [`SwarmPlanningService`](../swarm_planning.py)
interface is outside L5. One `plan` call proposes BOTH the next Stage and its
Agent Participant plus I/O contracts; the first decision also supplies the DAG.
Every call receives fresh authoritative Stage states, output refs and L4
participation. There is no separate MATCH interface. A read-only role helper
still serves validation, not an independent decision service. Decisions confer
no authority. The controller fences stale results, validates/submits/adopts the
initial DAG, and checks READY/dependencies/roles before generating the selected
worker's runtime execution profile and allocating. Authoring/instantiation and
external planning never call `declare_execution_profile`.

Each controller Stage has a distinct L4 child Task under root Work. Dispatch
creates/starts it with the selected executor through G4 and atomic L4 SQL
creation/event/CAS. The worker actively calls
[`StageReportProtocol`](../g4_contracts_consistency/execution_report.py), carrying
allocation, Stage and artifact refs. Returning from dispatch alone completes
nothing. [`SwarmStageReportService`](operations/swarm/report.py) validates the
exact allocated Agent (type AND ID), binding, Stage, lease and injected artifact
predicate before accepting Work. It submits the child Task as the worker,
accepts that internal Work under its accountable principal through G4's pinned
Task machine, and invokes `complete_stage` to release allocation, persist
`Stage.output_artifact_refs`, mark SUCCEEDED and project dependency readiness.
Unknown, wrong-worker, duplicate or unacceptable reports do not advance Work.
The child acceptance is internal Stage evidence acceptance, not root/product
acceptance. Artifact-specific predicates remain in the execution capability.

A planner may return `finished=True`, but `complete_plan` independently requires
a nonempty plan/termination contract, all Stages SUCCEEDED with artifacts,
corresponding done child Tasks, and no live allocations. It then transitions
through G4 `swarm.plan.complete` to COMPLETED; no direct store mutation invents
success. `is_completed()` verifies the plan, stages, evidence and child Work.
Root submission/user acceptance remain separate G1/G4 operations. Arbitrary
product termination-condition expressions are not evaluated by a new DSL.

Calls/exceptions are synchronous and propagate. The queue and MethodStore are
in-process, not durable; a remote completion needs a controller wake after its
report. There is no production scheduler, transport adapter, automatic retry or
replanning, persisted Decision API or distributed SQL/MethodStore transaction.
SQL child Work uses CAS; a failure between SQL and Method completion propagates,
and the same report can reconcile already-submitted/accepted child Work only
when its artifacts match the committed Task evidence. The coding journey covers
the successful path, not failure recovery, child CAS conflicts or SQL rollback.
Existing candidate provenance is not an external compute-job lifecycle record.
