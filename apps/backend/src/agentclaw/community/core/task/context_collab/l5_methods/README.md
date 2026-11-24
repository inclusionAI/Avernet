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
  - SwarmMethodPolicy
  - SwarmWorkflowPolicy
  - SwarmWorkflowPolicyReader
  - SwarmArtifactEvidenceValidator
  - AdmissionRole
  - OpenAdmissionPolicy
  - OpenPublicationProfile
  - BotSelfApplicationAdmission
  - OpenMethodOpsServiceProtocol
  - SwarmMethodOpsServiceProtocol
  - stage_role_candidates
  - SwarmDriver
  - SwarmExecutionControl
  - SwarmExecutionOperations
consumes:
  - MethodStoreProtocol
  - CollaborationReader
  - EventWriter
  - ContextServiceProtocol
  - TopLevelBindingRecovery
  - SwarmPlanningService
  - StageExecutionService
  - SwarmInteractionPlanner
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

The [bounded execution controls](SWARM-EXECUTION.md) are an internal L5 capability
assembled in the existing Swarm operations module. The existing task-commission
Service API exposes the three caller-facing methods; L5 has no dependency on
that product implementation and does not publish another execution Service API.

## Base methods and product compositions

`MethodKind` contains exactly four base methods: `Single`, `Group`, `Swarm`
and `Open`. `Open + Group`, `Open + Single` and `Open + Swarm` are product
composition wire expressions, not enum members. `parse_method_kinds` handles
these existing selection/legacy binding strings separately; `MethodKind.from_wire`
and composition-edge endpoints accept only base methods. G1 allows the supported
product compositions at creation, but parsing still rejects undeclared expressions,
including reversed `Swarm + Open`.

## Method policy ownership

- [method_policies.py](method_policies.py) defines `MethodPolicy` and
  `OpenMethodPolicy`, `SwarmMethodPolicy` and `SwarmWorkflowPolicy`. L6 co-locates each policy with its
  exact L5 Profile in `MethodTemplate(profile, policy)`; the policy must match
  that Method. Only required declarations enable runtime policy lookup; optional
  declarations do not implicitly activate capabilities. L6 defines an authoring
  envelope, not a second Method Profile or policy type.
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
- `required_capabilities` is an immutable, keyword-only tuple of capability
  declaration refs needed by the recruiting product. It defaults to empty to
  preserve existing policies; non-string, blank or duplicate refs are rejected.
  This field declares matching input, not an automatic evaluator or permission
  grant. The product reviewer must explicitly consume it. The local Open + Swarm
  reviewer below accepts an application when any exact declared capability ref
  matches the needs; empty needs or declarations produce no match. It does not
  require every Bot to cover every Swarm stage.
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

## Open + Swarm recruitment journey

The
[combined journey](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end_open_swarm.py)
authors a local Profile with independent Open/Swarm bindings and an explicit
Swarm --support--> Open edge; it does not change installed product defaults.

The Profile uses an OPEN participant scope and general `OpenAdmissionPolicy`,
not the capacity-1 `BotSelfApplicationAdmission`. Seven local Bots discover and
apply; the accountable owner calls the test-local
[capability reviewer](../../../../../../../tests/community/core/task/context_collab/products/open_admission_support.py)
with the pinned `OpenAdmissionPolicy` and each submitted application. The Profile
explicitly lists five `required_capabilities`. The Bots submit capability refs
as self-declarations through the existing `capability_evidence_refs`; the reviewer
uses exact set intersection, not applicant IDs or preassigned roles. Five Bots
match, one declares unrelated copywriting ability and one declares nothing.
The case checks that all seven applications reach the reviewer; separate
[reviewer regressions](../../../../../../../tests/community/core/task/context_collab/products/test_open_capability_admission.py)
check configuration changes, different Bot identities and empty/unmatched input.
These local declarations are not remotely verified evidence or proof of actual
capability. The owner still explicitly calls L5 `screen` and `admit`/`reject`;
review alone grants no participation. Capacity covers all seven simultaneous
applications, not just the five eventual admissions. Admission creates correlated
scoped Participant/Decision evidence, not a placeholder Swarm allocation.

The product does not preassign these applicants or their worker roles at
Instantiate. It omits scenario-role/governance/compatibility boilerplate and uses
safe resolved L6 defaults. At runtime, published role requirements and actual
admission supply the recruited team's L4 facts through bounded
[L6 pre-Work enrollment](../l6_products/README.md#runtime-open-enrollment-for-swarm).
Open.admit itself still creates scoped participation evidence, not a Swarm
allocation, resource Grant or Task. Initial ProductInstance role refs stay pinned;
current L4 participation includes the append-only enrollment delta.

[`operations/open/participation.py`](operations/open/participation.py) rechecks
the current ACTIVE Open binding, ADMITTED application, correlated decision and
scoped participant before enrolled Swarm planning, role-based allocation and
worker reports. Revocation cannot be bypassed using retained role facts. This
L5-owned validation never imports L6; fixed-team Swarms without enrollment retain
their original behavior. The product-aware L4 policy independently guards root
start/progress/submission. SQL and MethodStore still have no distributed
transaction, and typed AgentRef mapping does not verify remote identity.

After recruitment/enrollment, explicit L4 creation leaves root Work unstarted;
Driver wake performs the real L4/G1/G4 start. The journey verifies every executed
worker against admission evidence, each Stage and child Task's artifacts,
released allocations and completed Swarm. Root submission and owner acceptance
remain separate operations. Planning and worker computation remain local doubles;
the verification worker runs real unittest cases. No live Bot/LLM/PR is exercised.
[Enrollment regressions](../../../../../../../tests/community/core/task/context_collab/products/test_open_swarm_enrollment.py)
cover late-bound identities, authorization/evidence rejection, revocation and
pre-Work/write-failure fences.

## Bounded Swarm coordination

[`SwarmDriver`](swarm_driver.py) owns an in-process loop. The caller
supplies only `collet_id` through DI's `AssistedBuilder[SwarmDriver].build`;
ports are constructor-injected, not obtained from a global injector/registry.
The composition adapter resolves the pinned ProductProfile's explicit
`SwarmMethodPolicy.plan_service_ref` or `SwarmWorkflowPolicy.workflow` on its
required Swarm declaration and the one ACTIVE Swarm binding. An omitted or unknown
capability fails closed. No coding names, L6 imports or filesystem predicates
belong in L5. [Runtime assembly tests](../../../../../../../tests/community/di/modules/test_swarm_runtime.py)
cover startup identity selection; [wake regressions](../../../../../../../tests/community/core/task/context_collab/test_swarm_driver_wake.py)
cover construction, startup, coalescing and failure propagation.

Product Instantiate creates/binds the Collet first; explicit L4 `create_task`
then creates unstarted root Work while retaining the installed selection and
executor bindings. Construction accepts that root Task in CREATED or
IN_PROGRESS and makes no Work transition. The initial `wake()` starts CREATED root Work through the
injected L4 `ContextServiceProtocol.start` (G1/G4 and SQL CAS), preserving its
identity/objective, then enqueues unified planning. Subsequent wakes reconcile
already-started Work without another start; pending wakes coalesce. Start errors
propagate before planning is queued. This explicit driver entrypoint does
not add automatic dispatch to the generic L4 Work.start contract. With the
standard Task contract, the composition adapter supplies the designated root
executor as `SwarmRuntimeConfiguration.start_actor`; `actor` remains the
accountable principal for planning and Method administration. The adapter reads
the pinned Task/workflow start action and actual L4 role facts: it uses the
principal if permitted, otherwise requires exactly one startup role holder.
Missing or ambiguous role holders fail closed, without guessing an executor.
Already-running Work needs no startup identity selection. These trusted internal
contexts do not grant roles or bypass L4/G1/G4; startup is still adjudicated by
`ContextServiceProtocol.start`. The Open + Swarm example has no product workflow
and calls only `wake()`; the workflow-based Swarm example retains principal
startup. Custom runtime resolvers must now supply `start_actor` explicitly;
there is no fallback to the coordinator. No public command or persisted schema
changes are required.

The outbound [`SwarmPlanningService`](../swarm_planning.py)
interface is outside L5. One `plan` call proposes BOTH the next Stage and its
Agent Participant plus I/O contracts; the first decision also supplies the DAG.
Every call receives fresh authoritative Stage states, output refs and L4
participation. There is no separate MATCH interface. A read-only role helper
still serves validation, not an independent decision service. Decisions confer
no authority. The driver fences stale results, validates/submits/adopts the
initial DAG, and checks READY/dependencies/roles before generating the selected
worker's runtime execution profile and allocating. Authoring/instantiation and
external planning never call `declare_execution_profile`.

Each driver Stage has a distinct L4 child Task under root Work. Dispatch
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
in-process, not durable; a remote completion needs a driver wake after its
report. The Driver itself is not a production scheduler or automatic retry engine.
The [bounded execution Service API](SWARM-EXECUTION.md) now adds authenticated
HTTP step/stages/interactions, explicit execution revisions and journaled human
feedback/clarification. It does not add a distributed SQL/MethodStore transaction.
SQL child Work uses CAS; a failure between SQL and Method completion propagates,
and the same report can reconcile already-submitted/accepted child Work only
when its artifacts match the committed Task evidence. The coding journey covers
the successful path, not failure recovery, child CAS conflicts or SQL rollback.
Existing candidate provenance is not an external compute-job lifecycle record.


## Managed Swarm workflow

The immutable [workflow contracts](swarm_workflow.py) and
[compiler](workflow_compilation.py) add a managed DAG mode without changing
root Task workflow semantics or the dynamic policy's required planning ref.
The [product storage guide](../l6_products/PROFILE-STORAGE.md) owns publication,
cutover and configuration details. No L6 or DI dependency is introduced in L5.

`SwarmWorkflowPolicyReader` resolves the pinned, trusted product policy for both
the driver and direct Method callers. Managed candidate submission must match
the complete compiled graph; allocation independently rechecks the configured
Agent whitelist, actual participation/admission, exact execution-profile I/O and
dependency success. One live allocation across the plan is admitted under one
MethodStore lock, together with the frozen named input manifest. Stable IDs are
not a recovery mechanism; existing SQL children with lost Method history block
redispatch. Managed allocation IDs derive from Stage/attempt; exact request
replay (including a concurrent replay) returns the first allocation without
renewing its lease or overwriting frozen inputs. Current G1 authorization still
applies, while a changed target/profile/lease under the same key conflicts.
Duplicate in-flight wakes neither reselect nor redispatch.

Each node declares named required outputs, type/content contracts and cardinality;
inputs bind only named accepted outputs of direct predecessors. Control-only
edges do not copy artifacts. `ArtifactPortBinding` is G4-owned evidence over L4
Artifact references, never transferred resource bodies. Resource revisions must
be exact and provider-guaranteed immutable. Stage output role names are projected
from declarations, not separately configured. The selected Agent's current
resource access is checked before allocation and again at dispatch.

`SwarmArtifactEvidenceValidator` is a narrow read-only port: it owns no dispatch
or Method write capability. Public DI defaults to unavailable/fail-closed for
managed evidence. A host must install its immutable-resource, content-contract
and access adapter. The existing executor `accepts(stage, report)` signature
remains compatible, but cannot alone authorize managed completion. Report
validation occurs before any child Work submission; direct completion rechecks
actual resources, done child Work, actor identities and full versioned manifests.
Manifest and Stage success/allocation releases commit together in MethodStore;
child SQL and external I/O remain separate failure domains. Exact-manifest retry
may reconcile partial SQL reports, while changed ports/revisions are rejected.
The root standard Task payload contract is unchanged.

The planner proposes the next configuration-order READY node and uniformly
chooses one actual eligible configured Agent. Fork/AND join is supported with
serial dispatch; graph layer width remains structural, not execution parallelism.
The driver event budget scales with validated node count (dynamic mode retains
100). Asynchronous completion still needs an explicit host wake. No automatic
retry, reassignment, scheduler, persistent Method recovery or root acceptance is
provided. The [local acceptance test](../../../../../../../tests/community/core/task/context_collab/products/test_swarm_workflow_end_to_end.py)
uses DB publication, real SQLite, G1/G4 and content-addressed temporary resources.


## Swarm execution Service API

[Bounded execution and Stage conversations](SWARM-EXECUTION.md) owns the current
step/stages/interactions contract, authorization, request journaling, execution
revision semantics, clarification notification/reply flow and public-local
integration limits. This supersedes the earlier Driver-only exclusion of explicit
replanning and HTTP delivery, not immutable workflow publication or root Work
acceptance. The execution Service API is exported through the flat `api/` surface;
L5 still imports neither L6 nor a delivery framework.
