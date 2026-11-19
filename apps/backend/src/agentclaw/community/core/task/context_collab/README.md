# Context collaboration: authority, telemetry and read projections

Owner: Backend `core/task/context_collab`. This is the ACCS collaboration path,
separate from the older goal-driven TaskGraph framework in the parent directory.
It implements bounded local contracts, not full ACCS conformance.

## Context Boundary

- **Provides:** transport-free generic collaboration services, L4 primitives,
  L5 Method services, L6 product composition, product services and shared G1–G4
  enforcement. The full L4 `ContextServiceProtocol`, commands and results
  are exposed by `l4_context_primitives/context_service_protocol.py`; narrow
  storage/policy ports live beside it in `service_ports.py`. `l6_products/service_api.py`
  extends L4 with legacy combined creation; root `service_api.py` only reexports
  the canonical L4/L6 types. Method contracts live in
  `l5_methods/operations/contracts.py`; [L5 Method policies](l5_methods/README.md)
  own Open publication/admission values, and product contracts live in
  `products/*/service_api.py`. [G3 acquisition](g3_observe_evaluate_evolve/README.md)
  exposes `TelemetryServiceProtocol` for generic Observation, MetricDefinition/
  MetricSample and Outcome, separate from Work. Root API imports remain compatible.
- **Consumes:** injected `DatabasePlugin`, product catalog, MethodStore,
  authorization and delivery ports. DI chooses concrete implementations;
  HTTP adapters translate requests but do not own Task transitions.
- **Dependencies:** L4 primitive models and narrow write operations use L2/L3
  references and domain errors, not L5/L6 composition types.
  `l4_context_primitives/context_service.py` owns create, Work, append
  and authorized G3 read projections through L4-owned ports. The
  `l6_products/operations/collaboration_policy.py` adapter interprets L6
  constraints for those ports without owning Work resolution, persistence or query
  orchestration.
  Only L6/product composition paths resolve product selection and invoke L5
  initialization; `l6_products/operations/collaboration_creation.py` adds legacy
  combined creation to L4. Primitive SQL models, Task storage and the actual
  Collet/Task/Event repository live in `l4_context_primitives/persistence/`.
  Product metadata persistence and rich compatibility records live in
  `l6_products/persistence/`. `services/` and the old `persistence/repository.py`,
  `models.py` and `task_storage.py` paths contain identity reexports only.
  L6 composes lower-layer contracts; concrete business templates stay in
  `products/`. G1–G4 cross-cut these layers.

## Unified Swarm planning boundary

[`swarm_planning.py`](swarm_planning.py) owns the collaboration-domain outbound
`SwarmPlanningService` protocol and decision values, outside L5. It uses Method
value contracts, so it is not a lower-level `plugin_api` interface. Product
configuration explicitly selects a named implementation; DI supplies it. Each
`plan` call reads current Stage/participation facts and selects both next Stage
and executor, or proposes completion. Implementations cannot grant authority or
declare execution profiles; L5 validates and commits decisions. The coding mock
lives only in tests.

## Operation ownership

Operation implementation follows the owner of the affected authority, not the
HTTP route or a shared `services/` folder. This is a local implementation layout
for ACCS semantic boundaries, not an ACCS-mandated Python directory tree.

```text
g3_observe_evaluate_evolve/
  telemetry.py / metrics.py # protocol values, not L4 commands
  service_api.py / service.py # generic G1-gated acquisition/query contracts/body
  persistence.py           # independent append-only telemetry SQL
  observation.py           # existing Task/Event read projections
l4_context_primitives/
  context_service_protocol.py # full ContextServiceProtocol, commands/results
  service_ports.py            # narrow read/write and G1/G4 policy contracts
  context_service.py          # create/start/append_event/get_status/get_events
  operations/
    contracts.py           # L4 commands and primitive storage ports
    collet_lifecycle.py     # create/activate/suspend/resume/complete/archive
    work.py                # G4 Task resolution, Task fence and atomic root/child append
  persistence/
    repository.py          # actual L4 creation/read/append SQL and Task/Collet CAS
    models.py              # canonical Collet/Task/Event ORM models (one model set)
    task_storage.py        # Task identity/evidence checks and legacy import
    records.py             # lower records/repository contract and read decoder port
    creation.py / row_codec.py # primitive construction and row conversion
l5_methods/
  method_policies.py        # MethodPolicy / OpenMethodPolicy / SwarmMethodPolicy; L6 selects these values
  publication.py            # Open admission/publication and Bot self-application contracts
  operations/               # layer-owned Method operations
    contracts.py            # Method commands, results and public protocols
    initialization.py       # top-level Method/roster from resolved Method facts
    lifecycle.py            # binding lifecycle, inspect, compose and detach
    single.py / group.py     # separate operations; single_group.py preserves combined API
    open/                   # opportunity query/application/admission operations
    swarm/                  # execution profile/plan/stage + worker report operations
l6_products/
  service_api.py            # legacy combined creation command/extended Protocol
  task_contracts.py         # standard_task@1: fixed Task contract, not a Bot plan
  operations/
    collaboration_policy.py # product-aware adapter for L4-owned G1/G4 policy ports
    contracts.py            # Profile authoring, new/existing-Collet Instantiate commands/Protocol
    product_profile.py      # create_product_profile: validate/return, no installation
    product_instantiation.py # one L6 new-Collet creation/binding or existing-Collet selection
    collaboration_creation.py # legacy combined Method/product creation over L4
    method_bootstrap.py     # L6 selection/identities -> L5 initialization/recovery
    method_lifecycle.py     # legacy product-aware constructor; operations inherited from L5
  persistence/
    repository.py          # product selection/listing and legacy composition adapter
    records.py             # rich product records and same-read ProductRecordDecoder
services/                   # old import paths only; no operation implementations
products/
  task_commission/           # concrete product profile and business orchestration
  bbs_public_discussion/
```

- L6 [profile creation](l6_products/README.md#create-a-product-profile) is a pure
  `create_product_profile` operation returning `ProductProfileDefinition`.
  L5-owned Method policies are co-located in each `MethodTemplate(profile, policy)`
  declaration (for example, `OpenMethodPolicy(publication=...)`), not top-level Profile fields; see the
  [L6 authoring contract](l6_products/README.md#create-a-product-profile). Creation
  and catalog installation share validation but remain separate; neither profile
  authoring nor role declarations grant runtime participation or create a Collet.
  `domain` is optional vocabulary mapping; omitting `workflow` selects the fixed
  [standard Task contract](l6_products/README.md#optional-domain-and-product-orchestration),
  not an unrestricted lifecycle. Product-specific coordination is optional and
  does not describe an autonomous Bot's internal tool/plan loop.
- L4/L5 operations consume narrow `CollaborationReader`/`EventWriter` ports, not L6's
  product selection metadata or the richer application repository contract.
  Persistence exposes
  creation principal/executor identity facts for governance, preserving existing
  ID-only Method/Collet rules even for partial legacy rows. These are **creation
  identities**, not reassignment or a replacement for typed Task executor checks
  on Bot reporting. Task remains Work authority.
- The full L4 service owns schema checks, G1 policy invocation, early revision
  rejection, G4 contract validation, Work preparation/commit and authorized reads.
  Order remains schema → G1 → revision → additional contracts → G4 Work → SQL CAS.
  The product-aware policy adapter receives the same typed read snapshot as L4,
  validates product evidence and returns only a machine ID/version; it cannot
  commit or return a collaboration service result. Product entrypoints retain
  their existing policy/deadline checks and also pass resolved machine selection
  plus event intent into L4 `prepare_event_append`. It produces a TaskUpdate and exact Task/Collet
  fences; `PreparedEventAppend.commit` uses the same repository transaction for
  generic and commission Work. Preparation does not write; Bot deadlines are
  still checked after G4 and before commit. Ordinary Messages cannot acquire a
  TaskUpdate. These internal operations do not expose an unauthenticated API.
- `TaskModule` injects the L5 lifecycle implementation and the L6-owned
  `ProductMethodBindingRecovery` adapter through L5's `TopLevelBindingRecovery`
  port. Only that adapter resolves product catalog versions during recovery. Initialization is idempotent and keeps existing
  terminated bindings; missing binding recovery retains the existing in-memory
  restart limitation (no durable method history is fabricated).
- Product bootstrap resolves Method facts without interpreting product policy
  in L5. [Installed commission](products/task_commission/README.md#productinstance-and-the-open--single-journey)
  @2 uses **Open + Single**: COLLET_PARTICIPANT admission creates an immutable
  L4 ScopedParticipantBinding, then Single explicitly selects that participant
  for execution. No Group roster/member is created. Its authority requires the
  correlated ADMITTED application/decision, independently ACTIVE Open and Single
  bindings with an explicit Single --support--> Open composition edge, and an
  owner-selected, in-window SingleSelection. DI installs the shared G1 guard,
  so product and generic L4 execution cannot bypass these prerequisites.
  Publisher-mediated @1 remains installed unchanged; selecting @2 is explicit.
  Unrelated Open + Group products retain their structural behavior.
- The locally authored [Open + Swarm journey](l5_methods/README.md#open--swarm-recruitment-journey)
  combines broader Open recruitment with the original five-worker coding Swarm.
  It checks scoped admissions before explicit Driver startup and verifies Stage,
  child Task and final root Task artifacts; it does not install a new product
  default or imply an automatic generic Open-to-Swarm admission guard.
- L5 Open publication is `publish_opportunity(OpenOpportunityCommand(...))`.
  The command requires `collet_id` and `participation_type`, followed by optional
  publication fields; it has no `actor_context`. In-process publication derives
  its G1/audit identity from the persisted Collet's typed `initiator_ref`, even
  before any Task exists. A missing Collet fails without an opportunity write.
  Authenticated entrypoints supply their actual trusted `context` separately as
  a keyword-only service argument; HTTP never substitutes the initiator for its
  caller. G1 checks run before both first publication and idempotent replay.
  Neither `scope_ref` nor `opens_at` is an input: the persisted opportunity scope
  is always `collet_id`, and its opening timestamp is the server's current UTC
  time at first publication. An optional `closes_at` must be later than that
  timestamp. Same-ID, same-shape replay preserves the original opening time,
  audit and current state, even after the window expires; generated timestamps
  are not request-shape fields. `participation_type` uses the existing `ParticipationType`
  enum to declare the admission outcome, not participant count. HTTP accepts
  the same four string values, validates them into the enum before building
  the command, and rejects unknown values with 422. Neither the method
  arguments nor the command contain a binding ID. The service resolves exactly
  one ACTIVE binding declaring Open within that Collet and the current
  tenant/environment, then applies the
  existing G1 authorization, G4 transition, audit and binding-scoped idempotency
  checks. No Open binding is NotFound; no ACTIVE candidate or multiple ACTIVE
  candidates is ValidationError, with no opportunity write. It never picks the
  first/top-level binding or infers binding scope from a participant scope.
  The result supplies `method_binding_id` for subsequent binding-scoped operations.
  The internal HTTP publish route is now
  `POST /api/v1/collaboration/task/context_collab/{collab_context_id}/open/opportunities`;
  the previous `/method-bindings/{binding_id}/open/opportunities` publish route
  is removed. HTTP supplies the separate service context from verified
  authentication, not JSON. The HTTP publish body also omits `actor_context`,
  `scope_ref` and `opens_at` and
  rejects caller-supplied overrides; the Collet comes from the route. Other Open
  routes, except submission and review described below, remain binding-scoped. See the
  [publication regressions](../../../../../../tests/community/core/task/context_collab/test_open_publication.py).
- L5 Open owns `OpenMethodOpsServiceProtocol.query_opportunities(actor_context)`:
  read-only discovery across Collets and MethodBindings in the current
  tenant/environment, without caller-supplied Collet or binding IDs. G1 validates
  the actor before enumeration, including an empty store, and checks each
  candidate binding; denied, inactive and non-Open bindings are omitted. Only
  OPEN opportunities within their application window and below capacity are
  returned. Capacity remains isolated by Collet, binding and opportunity;
  querying neither reserves capacity nor admits an applicant. No candidates
  returns an empty tuple. MethodStore's `list_bindings_in_axes` supplies the
  tenant/environment-bounded candidate snapshot, not a durable global index.
  Product discovery filters the L5 result by authorized task/profile,
  designation and product policy outside L5. The existing signed HTTP
  product-discovery operation remains L4 Query; no new HTTP route or wire tuple
  is introduced. See the [query regressions](../../../../../../tests/community/core/task/context_collab/test_open_query.py)
  and [direct invocation example](products/task_commission/README.md#calling-l5-open-directly).
- L5 Open submission is `submit_application(OpenApplicationCommand(...))` with
  no separate scope, binding or context arguments. The command requires
  `opportunity_ref`, trusted `actor_context` and `applicant_agent_ref`; optional
  `capability_evidence_refs`, artifact/role refs and the idempotency key are
  retained on the application. Evidence is a declaration, not an authorization
  grant. The opportunity must resolve uniquely across bindings in the current
  tenant/environment: missing is NotFound and ambiguous scoped IDs fail validation,
  including duplicates under inactive bindings. The actual owning binding still
  passes Open/ACTIVE/G1 checks; window, capacity and nonterminal-applicant guards
  remain unchanged. This uses the in-memory scoped store, not a new global index.
  The command has no `accountable_principal_ref`: persisted accountability comes
  from trusted G1 provenance. Human proxy submissions use the authenticated human;
  Bot self-submissions require applicant == authenticated Bot UUID and a verified
  owner in `ActorContext.accountable_principal_ref`. That optional typed provenance
  is not needed for discovery or ordinary Work, but missing Bot ownership fails
  submission closed. The signed A2A adapter supplies the owner, which may differ
  from the task publisher. Audit still records the actual submitting actor;
  withdraw still requires the persisted accountable principal. Same-ID replay
  compares resolved accountability and evidence as well as other request facts.
  HTTP submission is now
  `POST /api/v1/collaboration/task/context_collab/open/opportunities/{opportunity_id}/applications`;
  the previous Collet/MethodBinding-scoped submit route is removed rather than
  ignoring its path scope. HTTP rejects caller-supplied actor/accountability
  overrides. See [submission regressions](../../../../../../tests/community/core/task/context_collab/test_open_submission.py)
  and [real HTTP cases](../../../../../../tests/community/endpoints/test_open_submission_cases.py).
- L5 Open review is command-only: `screen(OpenScreenCommand(...))` and
  `admit(OpenAdmitCommand(...))`. Both commands require `application_ref`, trusted
  `context` and `collect_id` (the L4 Collet ID); neither accepts a binding ID.
  L5 resolves the application's unique owning MethodBinding within that Collet
  and the current tenant/environment. Missing references are NotFound; empty
  identifiers and duplicate application IDs across bindings are ValidationError,
  including duplicates under inactive/non-Open bindings. It never selects the
  first/top-level binding. The resolved binding still passes Open/ACTIVE/G1
  authorization; G4 state transitions, actor/reason audit, propagated write
  failures and decision-key idempotency remain in force. Admission scope is
  derived from `collect_id`, not caller-supplied `admitted_scope_ref`; that field
  remains on persisted `AdmissionDecision` evidence. COLLET_PARTICIPANT also
  requires the opportunity's scope to match its owning Collet. Other admission
  products keep their existing rules (including Swarm target-stage validation).
  HTTP review routes are now
  `POST /api/v1/collaboration/task/context_collab/{collab_context_id}/open/applications/{application_id}/screen`
  and the same path ending in `/admit`. The previous MethodBinding-scoped routes
  are removed. HTTP derives Collet/application from the route and context from
  authentication, rejecting JSON context/scope overrides rather than ignoring
  them. `OpenDecisionOpCommand` remains unchanged for reject/withdraw/revoke.
  See [review regressions](../../../../../../tests/community/core/task/context_collab/test_open_review.py),
  [participation/replay tests](../../../../../../tests/community/core/task/context_collab/test_open_collet_participation.py)
  and [real HTTP cases](../../../../../../tests/community/endpoints/test_open_review_cases.py).
- All `services/*` modules are import-only compatibility forwards, not
  implementations. Product instantiation, product-aware policy, combined creation
  and Method bootstrap/recovery live in L6. The L6 legacy lifecycle constructor
  assembles the same recovery adapter for old `product_catalog=` callers; the
  old `services/method_lifecycle_service.py` path reexports it. New DI uses the L5
  Method implementation directly. Root `method_service_api` and `service_api`
  reexport canonical layer-owned types, preserving object identity and wire fields.

The [functional port tests](../../../../../../tests/community/core/task/context_collab/test_layer_operations.py)
exercise L4/L5 with records that have no product metadata or catalog, shared
Work fences, partial legacy Messages and propagated write failures. The real
DI/SQL/product journey remains the integration evidence. No new operation family
is invented for layers that do not yet have a corresponding use case.

## Creation and explicit composition

[`ContextServiceProtocol`](l4_context_primitives/context_service_protocol.py) is the
public **L4** Service API for `create_collet`, `create_task`, `start`, `append_event`, `get_status`
and `get_events`. `TaskModule` resolves it directly to the singleton
[`ContextService`](l4_context_primitives/context_service.py) in L4,
not to an application facade or a creation-only service. A direct L4 caller uses
one service for creation, Work and reads.

[`service_ports.py`](l4_context_primitives/service_ports.py) defines the storage
and G1/G4 policy extension contracts. The service itself depends only on these
lower/cross-cutting contracts. Typed generic read state binds the selected store
and its policy adapter to the same snapshot: L4 accesses only Collet, Tasks,
Events and a derived G3 display view, while an external adapter can interpret
additional stored metadata. No Profile/Binding types, untyped metadata container,
second lookup or reverse call to an application service enters the L4 service.
The deployment policy preserves commission admission and pinned lifecycle checks;
it is not a permissive substitute. Creation never invokes the event policy or catalog.

`TaskPersistenceModule` binds the L4-owned
[`ContextCollabRepositoryProtocol`](l4_context_primitives/persistence/records.py)
to an exact
[`ContextCollabRepository`](l4_context_primitives/persistence/repository.py)
instance in L4, not the L6 adapter or a subclass carrying product operations.
`TaskModule` uses this binding for both the L4 service and the narrow
`CollaborationReader` consumed by L4/L5 operations.
Product journeys call L6 once; independent primitive consumers may still use L4
creation and existing-Collet installation separately:

```text
L6 ProductInstantiationServiceProtocol.instantiate(ProductInstantiateNewCommand, actor)
  → validate pinned Profile, participants, role sources, capabilities and G1
  → injected L4 ContextServiceProtocol.create_collet (chain below)
  → product installation + L5 initialization (chain below); no Task

L4 ContextServiceProtocol.create_collet
  → L4 ContextService.create_collet (G1 creation authorization)
  → L4 ContextCollabRepository.create_l4_collet
  → one SQL transaction: only Collet (no Tasks or Events)

L6 ProductInstantiationServiceProtocol.instantiate(existing_collet_id, ...)
  → one SQL transaction: ProductInstance + scoped participants/roles + binding Event
  → initialize L5 Method bindings (separate MethodStore transaction); no Task

L4 ContextServiceProtocol.start(existing_collet_id, WorkStartCommand, actor)
  → in-memory root Task draft when missing; validate via G1/G4
  → one SQL transaction: root Task + task.created + task.started + Collet CAS
```

The SQL repository's read decoder port is storage-only: it receives the already
loaded primitive records and serialized `creation_facts` column in the same read,
not a repository/service callback. A standalone lower-only repository uses
`PrimitiveRecordDecoder` without a catalog. The deployed
[`ProductRecordDecoder`](l6_products/persistence/records.py) validates optional
product metadata and derives the richer state for the selected G1/G4 adapter;
L4 does not interpret that metadata. **Creation bypasses the decoder entirely.**
The L6 repository binding is separate and is used only by upper composition/product
consumers; it is not resolved by the L4 service or its repository provider.

[`l6_products/service_api.py`](l6_products/service_api.py) owns the **L6
composition extension** for compatibility: it adds `create_collaboration` to the
L4 Protocol. Root `service_api.ContextCollabServiceProtocol` is an identity
reexport of that extension; primitive commands/results remain identity reexports
from L4. The [L6 combined creation implementation](l6_products/operations/collaboration_creation.py)
inherits L4's use cases, not a second copy. New direct L4 consumers import
from L4 explicitly:

The canonical [`ColletCreateCommand`](l4_context_primitives/operations/contracts.py)
requires only `initiator_ref`; optional keyword-only `title`/`description`
describe the context. It has **no `to_space`, `objective`, `executor_refs`**,
Profile/Method selection or binding input. New contexts have no L3 scope anchor
(`scope=None`). Existing scoped contexts keep their stored anchor. L4 does not
import L5, L6, the application facade or the rich repository. Collet initiator
and audit provenance retain the authenticated actor. For root Work created by
start, the Task principal is the Collet initiator; executors come from actual
initial bindings, not the start caller's identity or a request participant list.

```python
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ColletCreateCommand,
    ContextServiceProtocol,
)

l4_collab_service = injector.get(ContextServiceProtocol)
collet = l4_collab_service.create_collet(
    ColletCreateCommand(
        initiator_ref=owner_ref,
    ),
    actor_context,
)
# Standalone L4 example: no Task/Event exists yet. Bind via L6 before product Work.start.
# New product journeys instead pass ColletCreateCommand into one L6 Instantiate call.
```

Collet creation persists only the context and returns revision 1. It has no Task,
Event, participants or creation facts. Root Task creation follows installation
and required admission/selection in product journeys: Work.start can atomically
create/start it, or explicit `create_task` can create it first without starting.
The latter works before or after Instantiate and inherits actual Collet executor
bindings when present, otherwise leaving Work unassigned. It preserves installed
selection metadata and binding evidence, and is not a prerequisite for
Instantiate. The Swarm journey uses Instantiate → create_task → controller.wake;
the commission journey continues to use Work.start directly. Creation failures
propagate and SQL writes roll back. Objective/executors remain Task authority,
not Collet fields.

Product-specific instantiation belongs to L6, not an L4 side effect:
[`ProductInstantiationServiceProtocol.instantiate`](l6_products/operations/contracts.py)
accepts `ProductInstantiateNewCommand(collet=ColletCreateCommand(initiator_ref,
title=..., description=...), profile_ref=profile.ref, participant_refs=(bot_a,),
resource_refs=(input_resource,))` to create and initialize through one L6 call.
The existing `ProductInstantiateCommand(collet_id, profile_ref, expected_version,
participant_refs=(bot_a,), executor_ref=bot_a, resource_refs=(input_resource,))`
remains available for binding an already-created untouched Collet.
New creation prevalidates Profile/roles/eligibility/capabilities and product G1,
then calls the injected public L4 creation port (including L4 G1 authorization).
It returns ProductInstance with `collet_ref`; it does not invoke admission,
selection or Work.start. Separate L4/SQL/MethodStore commits are preserved.
A post-creation failure exposes the committed identity via
`ProductInstantiationIncompleteError`; inspect and recover that Collet rather
than retrying new creation. See the [new-creation and recovery contract](l6_products/README.md#instantiate-a-product-collaboration).
Initial participants and typed L3 input resource references are supplied by the
product caller, not by L4 creation or the reusable Profile. The exact Profile's
typed [`ParticipantScope`](l6_products/participant_scope.py) declares `OPEN` eligibility
or an Agent allowlist; every supplied Agent must be eligible before any write.
Eligibility is not participation, Open admission, Single selection or a grant.

Instantiation authorizes the initiator/responsible principal and pins the exact
Profile on an untouched Collet. One SQL transaction writes ProductInstance and
actual scoped L4 Participants/RoleBindings plus `collet.participants_bound`.
That Event carries `resource_refs` (including `[]` for no inputs), the authoritative
facts parsed into L4 [ResourceBindings](l4_context_primitives/resource_binding.py).
Selection metadata retains the same references; no resource body, independent
binding store or resource Grant is created. Collet advances from revision 1 to 2;
there is still **no Task**, and the stored instance's `root_task_ref` is `None`.
Its creation evidence references this
binding Event. L5 initialization follows separately. Existing untouched Work is
also supported for compatibility: assignment preserves its creation Event and
advances its Task revision with `task.participants_bound`, which carries the
input resource references rather than rewriting the original creation Event.
A SQL failure rolls back selection and all initial binding evidence together.
Collet itself has no current-event pointer. G1 checks the pinned eligibility
scope against the **authenticated actual Agent** on generic and product execution
paths; eligibility, claimed actors and requested roles do not grant execution.
See [L6 instantiation](l6_products/README.md#instantiation-and-persistence) for
immutable selection, transaction limits and scalar product-role constraints.

The existing HTTP creation flow remains upper-layer composition:
`create_collaboration(CollaborationCreateCommand, context) -> Collet`, implemented
in [`l6_products/operations/collaboration_creation.py`](l6_products/operations/collaboration_creation.py).
It retains Method/product/resource selection inputs and its existing single-
executor wire contract; it is **not** the L4 command. Both HTTP faces retain the
existing creation response fields and derive their receipt from Collet plus the
accepted application command. Later state remains a separate `get_status` query.
SQL and MethodStore are still separate transactions; binding errors propagate.
No new HTTP creation route or multi-executor HTTP input is introduced here.

## L4 Work.start contract

`ContextServiceProtocol.start(collab_context_id, WorkStartCommand, context)`
is the explicit entrypoint on the L4-owned Service API for Work on the
**root Task** of an existing Collet. It uses the same injected L4 service as
creation and status/event queries; no application facade is required. Start does
not call a product service or expose arbitrary event fields. The command requires the caller's positive integer Collet
`expected_version` and optionally a string `note`. With no root Task, it also
requires a real `objective`; optional `completion_conditions` initialize that
Task. The event actor comes from `context.actor`.

```python
started = l4_collab_service.start(
    collet.collet_id,
    WorkStartCommand(
        expected_version=current_collet_revision,  # Re-read after Instantiate.
        note="Start admitted work",
        objective="Deliver work",
        completion_conditions=("Owner accepts deliverable",),
    ),
    bot_context,
)
assert started.accepted and started.current_status == "in_progress"
# Use started.context_version for the next version-fenced operation.
```

The L4-owned pipeline preserves schema validation, G1 authorization
(including scoped admission when required by the selected product), role/action
contracts and the exact pinned G4 machine. With no root Task, it validates an
uncommitted Task draft from the supplied objective, Collet initiator and actual
initial executor bindings. Only after all gates pass does one SQL transaction
create root Work and persist `task.created` (Task revision 1), then `task.started`
(Task revision 2), advancing Collet twice under one CAS. Rejection or any write
failure leaves no Task or partial Event. With existing Work, start preserves its
identity and definition and only applies the permitted transition. Both paths
require an available machine and matching transition; an unmatched start cannot
report success. Generic `append_event` does not create missing Work.
The result is an `EventAcceptedResult` from that committed write; stale/repeated
starts, guard failures and storage errors propagate without a success receipt.
`append_event` retains its generic unmatched-event compatibility semantics.

Starting does not create an Activity, dispatch an Agent, mark execution complete
or accept a deliverable. This bounded root operation is not a Task-tree CRUD API.
ACCS Work also names propose/assign/decompose/complete Task and record Activity;
that vocabulary is not a claim that all of those APIs are implemented here.
[Work.start contract tests](../../../../../../tests/community/core/task/context_collab/test_l4_work_start.py)
exercise both standard Task and explicit Workflow contracts with SQLite,
including distinct Task/Collet fences and SQL rollback.
[Root creation tests](../../../../../../tests/community/repository/task/context_collab/test_work_start_creation.py)
add taskless installation, first-start atomicity, G1/G4 no-write denial,
missing objective, repeated/stale starts and SQL failure/CAS rollback. The
[commission journey](../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py)
uses the injected L4 service for creation, Work and queries after Open admission; no new
HTTP route or wire operation is added. Existing product `start_task`/Bot-report adapters remain
compatible and share the same L4 preparation/commit machinery.

## State ownership

| Object | Authority and current implementation |
| --- | --- |
| [Collet](l4_context_primitives/collet.py) | Bounded context identity, initiator, governance scope, lifecycle vocabulary and context revision (no current-event pointer), with optional title/description, overall objective/acceptance criteria, typed L4 resources (`resources`), output artifacts (`artifacts`) and audit metadata for new records. This is a local representation, not an exhaustive ACCS schema. The SQL reader still seeds `ACTIVE`; the separate Collet lifecycle path is not unified by this change. |
| [Task](l4_context_primitives/task.py) | Independent L4 work-item ID, Collet/parent links, objective, completion conditions, responsible principal, designated executor set, typed L4 output artifacts (`artifacts`), status, Task revision and event provenance. Not an A2A Task/execution attempt. |
| [ScopedParticipantBinding](l4_context_primitives/participants.py) | Immutable task-scoped identity evidence created by Open COLLET_PARTICIPANT admission; validity derives from its admission application/decision and Method lifecycle, not an independent Group membership machine. |
| [MethodProfile / MethodProfileRef](l5_methods/method_profile.py) | L5 versioned Method contract and its compact exact-version reference. L6 composition templates use the contract; negotiation and ProductInstance retain references rather than duplicate definitions. |
| [MethodBinding](l5_methods/method_binding.py) | L5 Method identity, scope and runtime facts. Neither creation facts nor ProductInstance substitutes for Method state. |
| [Creation facts](l6_products/creation_facts.py) | Plain creation input and pinned L6 selection references, not a Collet/Task aggregate or mutable Work state. |
| [ProductInstance](l6_products/instance.py) | Pinned composition and selected Task/workflow/domain references; absent optional profiles stay null. No second work-item state machine. |
| [CurrentSnapshot / ProgressSnapshot](g3_observe_evaluate_evolve/views.py) | Read/response projections. CurrentSnapshot derives from Task; G3 computes authorized query progress. Neither is an ordinary runtime persistence or transition input. Explicit old-row decoding is the only snapshot-authority compatibility boundary. |
| [EventRecord](l4_context_primitives/event_envelope.py) | Append-only accepted facts. New Work events record `task_ref` and `task_version_after` separately from the Collet revision. Message events omit both. |

[G4 Task transition resolution](g4_contracts_consistency/workflow_resolution.py) consumes
Task, not CurrentSnapshot. Generic and product entrypoints share G4 validation
and the selected versioned machine. The repository receives a typed
`TaskUpdate(task_ref, expected_version, status)` and fences its exact Task
identity/revision plus the Collet revision in one transaction. Ordinary Messages
cannot carry a TaskUpdate. L5's top-level termination gate reads root Task state,
not the response projection.

### Collet representation and audit

Collet retains `collet_id`, `initiator_ref`, `state`, `scope` and `version`.
It has no `current_event_id`: G3 derives the current event from the last accepted
Event in the complete stream, then applies G1 visibility (no fallback to an older
visible event). The deprecated SQL column is retained for schema compatibility
only; new domain code neither reads nor writes it. The legacy HTTP combined-create
receipt resolves its immutable `task.created` evidence through `get_events` at
the returned creation revision; concurrent appends cannot change that receipt.
`scope: SpaceRef | None` is an
optional legacy L3 governance anchor, **not** the union of accessible Spaces or a
Resource access grant. `version` is the aggregate revision used for CAS; the read-only `revision`
property is an alias, not another persisted counter.

New creations also expose `title`, `description`, `objective`, `acceptance_criteria`, `created_at`,
`created_by`, `updated_at`, `updated_by` and a derived `etag`. The immutable
[ColletMetadata](l4_context_primitives/collet_metadata.py) value object groups
description/audit internally; `Collet.to_dict()` serializes these fields
at the top level, not under a second aggregate. There is no `schema_version`
property or wire field. The SQL decoder alone accepts the retired
`schema_version="1.1"` marker in stored metadata, without rewriting on read;
the next accepted write stores metadata without it. Other legacy marker values
remain invalid. Canonical ETags no longer include that field, so tags produced
before its removal must be refreshed; existing `version`-based CAS is unchanged.
Audit timestamps are timezone-aware UTC values, normalized without losing
microseconds. The authenticated creation actor supplies creation/update
identity; existing initiator-versus-responsible-Task-principal semantics remain
unchanged. Callers can supply optional nonblank title/description through
`ColletCreateCommand`, but not server-owned audit fields. Descriptions do not
replace Task objectives or completion conditions. Metadata also carries the
collaboration-level `objective` and `acceptance_criteria`, both `str` with empty
string defaults for absent legacy values; explicit null or non-string values
are invalid. Both fields serialize at the Collet top level and participate in
its ETag, so older tags must be refreshed after adding these fields. This is a
metadata-only addition: creation commands, Work initialization, Task fields and
acceptance/transition logic are unchanged; no automatic propagation is added.

Every accepted SQL event append updates `updated_at`/`updated_by` in the same
Collet CAS transaction as its revision. Creation audit remains fixed.
ETag is a quoted SHA-256 tag of the canonical Collet representation excluding the
tag itself; it is derived, not separately persisted. It does not add an HTTP
`If-Match` API or replace the existing expected-version fence. Reads do not
advance Collet audit; explicit Task creation and L6 participant binding do. The
separate in-memory lifecycle service still does not persist lifecycle state/events/audit.

Legacy metadata-less representations remain readable with `metadata=None`; absent
audit/description values are unknown, not synthesized from SQL row
timestamps or deployment time. The removed event-pointer field is not accepted
in the domain wire shape, and appends do not invent missing creation history. Complete current metadata is
required when any metadata field is supplied; partial/invalid metadata, unknown
fields and mismatched supplied ETags fail validation.

## Persistence and compatibility

The [L4 repository](l4_context_primitives/persistence/repository.py) and
[canonical ORM models](l4_context_primitives/persistence/models.py) store three
distinct records. The [L6 persistence adapter](l6_products/persistence/repository.py)
owns ProductInstance selection/listing and legacy combined-creation records;
it delegates primitive SQL operations to L4. Old shared persistence paths are
compatibility imports, not the implementation locations:

```text
Collet.root_task_ref ──→ Task.task_id
      │                      │
      └── Collet revision    └── independent Task revision
                   ↘        ↙
               append-only Work Event
```

- Pure L4 creation atomically writes Collet, root Task and creation Event.
  Application composition may include pinned ProductInstance metadata; explicit
  later L6 instantiation installs it separately without rewriting creation evidence. New rows leave `current_snapshot`
  NULL. Root IDs differ from Collet IDs, even when both revisions start at 1.
- `ac_context_collab_collet.collet_metadata` stores L4 schema/description/audit
  JSON separately from `creation_facts`, Task JSON and shared SQL row timestamps.
  NULL is reserved for unknown legacy metadata; it is not automatically
  backfilled. L6 may update composition facts on the same row without changing
  the Collet representation, so `gmt_modified` is not Collet's audit authority.
- Collet `artifacts` is a tuple of the same L4 Artifact values, defaulting to
  empty. Non-empty values use Artifact's nested JSON representation and are
  included in the Collet ETag; empty values are omitted to preserve existing
  representations and ETags. Missing fields decode as empty. This is model-only:
  no storage mapping, runtime reporting or Task-to-Collet aggregation is added.
- Collet `resources` is a tuple of L4
  [ResourceBinding](l4_context_primitives/resource_binding.py) values, each carrying
  a typed L3 `ResourceRef`; these are not the grant-snapshot bindings nested in
  Artifact. It defaults to empty, including when the wire field is missing.
  Non-empty resources serialize as a list of binding objects and participate in
  the Collet ETag; empty resources are omitted to preserve existing shapes and
  ETags. This is model-only: no SQL mapping or automatic propagation from L6
  inputs or initial binding events is added. Existing event-based binding facts
  remain unchanged, and a resource entry does not grant access.
- Task `artifacts` is an immutable tuple of existing L4
  [Artifact](l4_context_primitives/artifact.py) values, serialized as a JSON list
  of objects using Artifact's ResourceBinding/ResourceRef contract, not strings
  or resource bodies. It defaults to empty, including for older records without
  this field. This is a model-only declaration: Work/report handling,
  Stage output propagation and root Task aggregation do not populate it yet.
- New Task JSON stores `executor_refs` as a list of typed references. The explicit
  legacy decoder accepts the old nullable `executor_ref`; mixed singular/plural
  storage is rejected. The singular read property returns the sole executor only,
  never a first-executor fallback for a multi-executor Task. Multiple-executor
  response views expose `executor_refs`; zero/one views preserve their old shape.
- Work atomically updates Task, appends Event and advances Collet. Message
  advances only the Collet/event head. A successful SQL update must affect the
  expected row; conflicts and write failures roll back all three records.
- Stored Task IDs, versions, parent links and event provenance are checked.
  Missing roots, orphaned Task rows, cross-context parents, parent cycles and
  mismatched evidence fail closed, without falling back to an old snapshot.
- Existing product APIs still use **Collet IDs** as their task identifiers.
  The root Task link is internal; this change does not add a new Task CRUD,
  decomposition, reassignment or recursive-completion API. Parent links model
  recursion, but do not automatically complete parents or execute child Tasks.
- The existing EventEnvelope adapter continues to export a **Collet aggregate**
  envelope. Local Task subject/version columns are not included. That adapter
  is not a lossless Task-state replication format or a remote Task import API.

### Database cutover

Apply [the Task migration](sql/2026_11_13_context_collab_task.sql) **after**
[the baseline schema](sql/2026_11_11_context_collab.sql), before starting the new
runtime. It adds the root link, Event subject/revision and independent Task
storage. The script targets OceanBase MySQL mode and is applied once by the
normal deployment process; SQLite tests create ORM metadata, not this DDL.

Existing deployments without `collet_metadata` must additionally apply the
[additive metadata migration](sql/2026_11_17_context_collab_collet_metadata.sql)
after their Task/creation-facts cutover and **before** deploying the new runtime.
No legacy audit backfill is performed. Fresh installations using the current
baseline already have this column and must skip that ALTER. The historical
[creation-facts migration](sql/2026_11_16_context_collab_creation_facts.sql) is
only for deployments still storing `context_spec`, not the current baseline.

Stop old snapshot writers during cutover: mixed old/new writers and old-runtime
rollback after Task-authoritative writes are not supported. Preserve historical
profile definitions. This storage cutover is not a product-profile upgrade.
Production DDL execution must be verified separately.

[Legacy decoding](persistence/task_storage.py) is explicit:

1. A row with no root ref and no Task rows can decode its old snapshot into a
   deterministic `task_legacy_*` root. Version 1 is an **import baseline**, not a
   fabricated historic Task revision. `legacy_context_version` records the
   source Collet revision; missing historic creation/update evidence remains
   explicitly absent. Historic display counters are ignored.
2. GET performs no backfill. The next accepted append materializes the import
   and clears the legacy snapshot in the same transaction. Message preserves
   Task version 1; a Work update advances it. Historical Events/profile selections
   are not rewritten, and later updates retain the original import baseline.
3. A genuinely partial old row with neither root nor snapshot has no Task.
   It can receive ordinary Messages but cannot advance Work. This is distinct
   from a corrupted new row with a missing referenced Task, which is rejected.

## Execution telemetry

[G3 acquisition](g3_observe_evaluate_evolve/README.md) records step details as
Observation, percentages as versioned MetricDefinition + MetricSample, and
execution results as Outcome. The injected service applies G1 access and writes
independent telemetry; it never advances L4 Events, Tasks or Collet revisions.
Outcome SUCCEEDED is not submission/acceptance. The commission journey uses
this direct library service, then separately submits Work through signed HTTP.
Existing HTTP progress actions remain compatible; the older read projection
below does not automatically consume these new samples. Apply the
[additive telemetry DDL](sql/2026_11_17_context_collab_telemetry.sql) before using
acquisition; SQLite contract tests do not establish deployed DDL readiness.

## Query progress

Library callers query final Task state through the L4 service's `get_status`
and read accepted artifact references from `get_events(..., context=actor)`.
The L6 compatibility extension inherits these same L4 queries, not a second
implementation.
The latter returns L4 `EventRecord` facts in acceptance order after the same G1
visibility filtering as status queries; `task.submitted` payloads carry the
submitted `artifacts`. Both queries are read-only, do not fetch artifact contents
or change Collet/Task revisions, and do not add query routes to the HTTP API.
They are separate reads, not a cross-call snapshot guarantee.

[G3 observation](g3_observe_evaluate_evolve/observation.py) counts work items,
not historic control events:

- `completed_count`: Tasks in `done`; `active_count`: `in_progress` or
  `reviewing`; `blocked_count`: `blocked`. `created` is pending.
- Percent is an equal-weight mean over visible, noncancelled Tasks: completed
  Tasks contribute 100; other Tasks contribute their last visible, evidenced
  reported percent, or 0. This is an observation, **not** a completion verdict.
  A report of 100 does not accept a result or complete a Task.
- The current default includes each stored Task, including parents if present;
  it is not a weighted tree/leaf-only aggregation policy. Current public product
  mutation paths address one root Task per Collet.
- G1 filters Events first. Tasks whose latest state evidence is hidden are
  omitted from current-state projections; progress evidence must also be visible.
  Explicit legacy import baselines can lack historical state evidence.
- `trunk_events` filters only the returned event list. It does not change Task
  counters/state. Per-event `completed/active/pending` means historical position,
  not completed/active/pending work items. GET never advances either revision.

## Verification and change impact

- [Generic G3 acquisition contracts and evidence](g3_observe_evaluate_evolve/README.md): step/metric/result separation, G1 access, immutable definitions, independent SQL, failure handling and compatibility.

- [Collet metadata contracts](../../../../../../tests/community/core/task/context_collab/test_collet_metadata.py): schema/revision separation, UTC precision, description/audit validation, canonical ETag and legacy decoding.
- [Collet metadata SQL tests](../../../../../../tests/community/repository/task/context_collab/test_collet_metadata.py): authenticated creation audit, separate storage, append/read behavior, CAS/rollback and unknown/corrupt legacy metadata.
- [Standalone L4 repository tests](../../../../../../tests/community/repository/task/context_collab/test_l4_repository.py): actual lower-only create/read/append SQL, decoder isolation, distinct principals, CAS, read-only queries and rollback.
- [Real L4 repository wiring](../../../../../../tests/community/repository/task/context_collab/test_l4_repository_wiring.py): exact L4 implementation, singleton sharing with primitive readers and creation without resolving product storage or accessing the catalog.
- [Pure L4 SQL creation tests](../../../../../../tests/community/repository/task/context_collab/test_collet_creation.py): unassigned creation, rejection of removed executor inputs, later multi-executor binding, authorization, storage compatibility and rollback.
- [Explicit product installation tests](../../../../../../tests/community/repository/task/context_collab/test_product_instantiation.py): pinned selection, preserved creation evidence, actual initial bindings, revision-fenced installation and surfaced write failures.
- [Participant eligibility and G1 tests](../../../../../../tests/community/repository/task/context_collab/test_participant_scope.py): OPEN/allowlist contracts, all initial Agents checked before writes, binding rollback, pinned scope and authenticated execution through both entrypoints.
- [Full L4 DI/contract tests](../../../../../../tests/community/di/test_l4_collab_service.py): direct protocol/implementation binding, complete API conformance, preserved G1 policy and no application-facade resolution.
- [Lower-only L4 service tests](../../../../../../tests/community/core/task/context_collab/test_l4_collab_operations.py): Work/schema/policy/CAS ordering, authorized queries, visibility/trunk behavior and propagated failures, without L5/L6 read facts.
- [L4 dependency gate](../../../../../../tests/community/architecture/test_context_collab_l4_dependencies.py): no L5/L6/application imports from L4.
- [Compatibility-entrypoint gate](../../../../../../tests/community/architecture/test_context_collab_compatibility_imports.py): `services/` and root `service_api.py` contain only imports, never implementations.
- [L6 placement/compatibility tests](../../../../../../tests/community/core/task/context_collab/test_l6_service_placement.py) and [L6 dependency gate](../../../../../../tests/community/core/task/context_collab/test_l6_layering.py): canonical L6 ownership, legacy identity reexports and no reverse dependency on `services/`, root APIs, concrete products or DI.

- [Task contract tests](../../../../../../tests/community/core/task/context_collab/test_task.py).
- [SQLite Task authority/cutover tests](../../../../../../tests/community/repository/task/context_collab/test_task_authority.py): distinct IDs/revisions, G4 uses Task despite a corrupt retired snapshot, repeated progress, read-only/trunk-independent queries, Task fences, atomic rollback, invalid storage and legacy import.
- [L6 composition contract](l6_products/README.md) and [commission product](products/task_commission/README.md) document profile selection and authenticated Bot reporting.
- [Commission journey](../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py) runs real local DI/SQLite/signed HTTP with mocked outbound delivery, not a live remote Bot.

Changing Task state/version, query counters or legacy mapping affects generic
services, G4, Method termination, product services, stored rows and existing
clients interpreting progress. The creation return contract also affects DI,
library consumers, both HTTP adapters and their test doubles; update them
together when changing it. Keep their tests and migration aligned. Source
imports of product selection or snapshots must use their owning boundaries;
Existing HTTP product inputs remain unchanged; new Collet library serialization
includes metadata/ETag while old six-field values remain readable. Pure L4
Task creation evidence contains objective and executor_refs (empty without
prior executor bindings); initial product participation is recorded separately.
The in-memory
MethodStore and SQL/MethodStore/delivery transaction gap remain existing limits;
this change does not add live A2A execution or a recursive workflow engine.

## Swarm execution reports and child Work

The [L5 controller/report contract](l5_methods/README.md#bounded-swarm-coordination)
coordinates a product-selected external unified plan capability. Each Stage
references an independent L4 child Task. The internal L4 append contract accepts
a child `task_creation` only with an existing same-Collet parent and identical
accountable principal, an initial created revision and a G4-prepared start.
It atomically records task.created/task.started and preserves root_task_ref.
Subsequent Work resolves the exact TaskUpdate subject, fences that Task revision
and the Collet revision, and never substitutes a child for root Work.
Worker completion evidence belongs in Task events and Stage output refs; it is
not a G3 telemetry-only observation. Root acceptance remains separate. The
[Swarm integration test](../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end_swarm.py)
checks child Work SQL reload and five phase artifacts.
