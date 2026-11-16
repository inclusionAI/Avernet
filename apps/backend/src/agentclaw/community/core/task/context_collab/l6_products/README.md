# L6 product contracts

Owner: Backend `core/task/context_collab`. This layer defines versioned product
composition; it is not another Task engine, identity registry or permission
system. It owns internal, transport-free domain contracts and composition
Service APIs, not a new HTTP/ACCS operation family or product-specific wire protocol.

## Provides and consumes

| Contract | Owner/source | Meaning |
| --- | --- | --- |
| ProductProfileCreateCommand / create_product_profile | [operations/](operations/) | Typed, pure creation operation: validate and return an immutable definition without installation, instances or runtime writes. |
| ProductInstantiateCommand / ProductInstantiationServiceProtocol | [operations/contracts.py](operations/contracts.py), [implementation](operations/product_instantiation.py) | Explicit pinned selection on existing untouched L4 work, then invoke L5 initialization. |
| CollaborationCreateCommand / ContextCollabServiceProtocol | [service_api.py](service_api.py), [implementation](operations/collaboration_creation.py) | Legacy combined Method/product creation extension over L4; root API is an identity reexport. |
| ProductCollaborationPolicy | [operations/collaboration_policy.py](operations/collaboration_policy.py) | Product-aware adapter to L4-owned policy ports; interprets selected L6 evidence without owning Work or query execution. |
| ProductMethodBindingRecovery | [operations/method_bootstrap.py](operations/method_bootstrap.py) | Translates pinned product/identity facts into L5 initialization/recovery inputs through L5-owned operations and ports. |
| ProductProfileDefinition | [contracts.py](contracts.py) | Versioned composition, scenario roles, optional domain/workflow mappings, Task contract selection, compatibility and G1–G4 implementation requirements. |
| MethodCompositionTemplate | [contracts.py](contracts.py) | Required/optional Method versions and instantiation scope. Current runtime supports one explicit Collet-scoped top-level binding, including the existing composite binding representation. |
| ScenarioRole | [binding.py](binding.py) | Maps owner/assignee to actual L4 Participant and RoleBinding facts. No L3 grant is created. |
| DomainProfile | [contracts.py](contracts.py) | Maps product vocabulary to L3 Resource and L4 Collet/Task/Message/Artifact authority, not duplicate product-owned entities. |
| TaskLifecycleContract / TaskAction | [task_contracts.py](task_contracts.py), [contracts.py](contracts.py) | Reusable, exactly versioned Task lifecycle/action contract, independent of product orchestration. References the existing G4 machine; never executes a Bot plan. |
| WorkflowProfile | [contracts.py](contracts.py) | Optional product orchestration contract. Existing profiles own their action mappings and reference a versioned G4 machine over L4 Task, or explicitly declare a nontransition event stream. Not required merely to run an autonomous Bot. |
| OpenAdmissionPolicy / OpenPublicationProfile | [publication.py](publication.py) | Shared participation/role/capacity constraints, ADMITTED delivery gate and partial-failure behavior. Concrete products own specialized admission strategies and step order; L6 does not execute them. |
| ProductInstance | [instance.py](instance.py) | Durable selection/evidence references: product and selected Task/workflow/domain versions, actual top-level MethodBinding ID, selected Method versions, RoleBinding references, governance requirements and negotiation result. No independent lifecycle state. |
| Compatibility selection | `negotiate(profile, offer)` | Exact required profile/Method/ACCS/layer/extension/governance match. Reports missing required and omitted optional capabilities separately. Never silently chooses a newer version or weaker policy. |

`profiles.py` retains the wire ID enums for compatibility; IDs alone are not
profile definitions. L6 supplies the immutable `ProductCatalog` mechanism, not
an installed/default global catalog. Product teams author concrete definitions in
`products/`; the composition root explicitly installs them in
[`di/modules/context_collab_products.py`](../../../../di/modules/context_collab_products.py).
`TaskModule` provides one catalog per injector and injects that same instance into
repositories, generic/product services, L6 Method bootstrap/recovery and the bounded
Open adapter. Pure selection, instance and G4 helpers require this catalog
explicitly; none fall back to global/default product installation. An empty or
incomplete installation rejects unknown profiles rather than silently recovering
one from another catalog. Historical definitions must remain installed for their
instances to load and execute.

Installing a profile requires explicit code/composition changes and tests, not an
enum change. Constructing a separate catalog does **not** dynamically register a
runtime product. Tests may assemble isolated catalogs. There is no mutable
runtime registry, catalog database or registration API.


## Create a product profile

[`create_product_profile(command)`](operations/product_profile.py) is the L6
library operation for authoring a `ProductProfileDefinition`. Its typed input is
[`ProductProfileCreateCommand`](operations/contracts.py): exact `ref`, Method
composition, scenario roles, governance and compatibility requirements, plus
optional domain, workflow, Task contract selection and Open publication.
`publication=None` intentionally
means there is no automatic Open publication use case.

```python
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    ProductProfileCreateCommand,
    create_product_profile,
)

profile = create_product_profile(
    ProductProfileCreateCommand(
        ref=profile_ref,
        composition=composition,
        scenario_roles=scenario_roles,
        # Autonomous Bot: no domain/workflow boilerplate.
        # Fixed standard_task@1 still governs Task actions and completion.
        governance=governance,
        compatibility=compatibility,
        publication=publication,
    )
)
# Installation is a separate, explicit composition-root step:
catalog = ProductCatalog((profile,))
```

Creation returns the definition itself, not an installation receipt. It preserves
all supplied IDs, versions and product-specific admission strategies; it does not
allocate versions, select latest, switch participation types or execute admission.
[`validation.py`](validation.py) owns the shared structural checks used by both
creation and catalog installation (Method composition/scope, role mappings,
Task/workflow role requirements and publication contracts). Invalid definitions
raise
`ContextCollabValidationError`. Direct dataclass construction remains compatible;
installation still validates it. Reference uniqueness is enforced within each
catalog, not by this stateless creation operation.

Creating a definition performs no I/O, runtime authorization, capability
negotiation, global registration, catalog mutation or Collet/Task/Method creation.
It is not an authenticated HTTP/ACCS write endpoint or a guarantee that the
referenced machine/Method versions are locally installed. Product code supplies
business templates; DI explicitly installs the resulting definitions and later
selection negotiates the installed capabilities. Commission's
[`init_product_profile`](../products/task_commission/profile.py) calls this
operation with its authored command, preserving its selected publication policy.
The [creation tests](../../../../../../../tests/community/core/task/context_collab/test_l6_product_profile_operations.py)
cover successful creation, immutability, exact versions, both admission strategies,
non-installation and shared rejection rules.

L6 pure modules consume L2–L5 value contracts and the installed L5 profile
catalog. They do not import concrete product definitions/services, DI,
repositories, HTTP or remote clients. L6 composition operations consume the
existing repository/MethodStore contracts and invoke lower-layer operations;
concrete product services and DI remain outside L6.
Shared G4 enforcement lives in
[`g4_contracts_consistency/product_contract.py`](../g4_contracts_consistency/product_contract.py).

## Optional domain and product orchestration

- `domain=None` (the default) uses standard L3/L4 vocabulary. No synthetic
  DomainProfile, domain reference or product-owned Task is created. Supply a
  DomainProfile only when the product needs an explicit vocabulary mapping.
- `workflow=None` (the default) means no product-specific orchestration. It does
  **not** remove Task lifecycle, action, authorization or acceptance rules. The
  resolver selects the fixed `standard_task@1` contract, never an installed
  latest version. `task_contract_ref=VersionedProfileRef("standard_task", 1)` is
  the equivalent explicit selection; unavailable exact contracts are rejected
  during authoring and catalog installation.
- [`standard_task@1`](task_contracts.py) defines `start_task`, `report_progress`,
  `submit_task`, `accept_task`, `reject_task` and `cancel_task` mappings. It reuses
  the existing G4 `task_commission_goal@1` machine (the historical name is kept),
  not a copied transition table. A required `selected-agent` role must come
  from `executor_ref`. Executor reporting, public Task controls, artifact guards,
  owner review/cancellation and Collet/Task CAS still apply through both product
  Work and generic append. Progress 100 is not acceptance; submission reaches
  `reviewing`, and owner acceptance reaches `done`.
- An explicit WorkflowProfile preserves its existing action/machine/event-stream
  semantics. Current composition supports **one** Task authority: supplying both
  `workflow` and `task_contract_ref` is rejected rather than merging or weakening
  their rules. General workflow orchestration/DSL execution is not implemented.
- A Bot's internal plan, tool loop, coding steps or execution attempts do not
  require WorkflowProfile. Use one when the product needs declared product-level
  coordination or different event/Task semantics, not to describe how a single
  Bot reasons. The standard Task contract does not schedule those steps.

ProductInstance persists absent `domain_ref` / `workflow_ref` as null and pins
`task_contract_ref=standard_task@1` for the default path. Legacy workflow-owned
instances retain their old JSON shape (no new Task-contract field). Strict
loading checks the exact selection against the installed definition; missing or
mismatched references fail closed. Historical reads and Method recovery do not
renegotiate capabilities or choose a newer contract. These references use the
existing `creation_facts` JSON storage; no new table or backfill is needed.

Do not mutate an installed product version to remove its old references. Author
and explicitly install a **new product version** for new instances; preserve old
definitions and machines. The default DI installation remains
`task_commission@1` with its original domain/workflow. The autonomous `@2` in the
journey test is a test-local installation, not a production catalog upgrade.

## Installed definitions

| Product ref | Workflow ref | Existing Method topology | Executor |
| --- | --- | --- | --- |
| team_todo@1 | team_todo_execution@1 | Single | Required |
| public_discussion@1 | forum_public_discussion@1 | Open + Group | Optional |
| bbs_public_discussion@1 | bbs_public_discussion@1 | Open + Group | Optional |
| bbs_help_request@1 | bbs_help_request_goal@1 | Open + Group | Optional |
| task_commission@1 | task_commission_goal@1 | Open | Required |


This table records **this checkout's existing implementations**. It does not
infer that every public discussion is Group, or that a designated executor
implies Single. Changing an installed profile's topology or lower-layer meaning
requires a new version; preserve old definitions for existing instances.

## task_commission consumes its selected definition

[`products/task_commission/profile.py`](../products/task_commission/profile.py)
owns the installed `task_commission@1` definition, rather than placing business
templates in L6 or a generic profile factory. The specialized
`DesignatedOpenAdmission` compatibility policy and
`BotSelfApplicationAdmission` policy belong in
[`products/task_commission/publication.py`](../products/task_commission/publication.py).
The other four existing compatibility definitions live in
[`products/legacy_profiles.py`](../products/legacy_profiles.py), not L6. Neither
module registers itself; only DI chooses the installation.

The product service and its bounded Open adapter consume the injected definition:

- `PublishTaskCommand.product_profile_ref` selects an exact installed definition
  (default `task_commission@1`). Creation reads that definition's Method
  composition and optional Workflow ref, then calls `prepare_product_profile`
  with the exact selection. This selector is creation-only. Generic creation
  without an exact profile ref still selects v1; neither path selects latest or
  migrates an existing instance.
- The publication contract maps `assignee` to the required typed executor and
  `owner` to the required principal. Catalog construction delegates role validation
  to the product's specialized policy. L6 only requires the referenced roles to
  exist and be required; it does not impose commission's executor/principal
  source mapping on all Open products. The installed
  `designated_bot_self_application` strategy uses the authenticated Bot as
  applicant, its signed owner as accountable principal, and publisher-authorized
  policy execution for automatic review. The separately assembled
  `publisher_mediated` strategy is retained only for compatibility tests. G1
  still authorizes L5 operations.
- The DI adapter consumes the exact selected publication policy:
  `publish_opportunity → submit_application → screen → admit`, with
  `SINGLE_CONTRIBUTION` and capacity 1 in v1. The product passes the actual
  projected top-level MethodBinding ID, not a publisher/executor identity.
  Unsupported strategies, step sequences and delivery policies fail closed;
  the bounded adapter also rejects a generic policy that is not this product's
  designated-executor strategy. The installed profile publication only opens the
  opportunity; the Bot's later Query/Apply runs admission with
  `COLLET_PARTICIPANT`, capacity 1, and verifies the resulting task-scoped
  ParticipantBinding. There is no arbitrary workflow DSL executor.
- Dispatch requires the L5 receipt to be `ADMITTED`. The installed profile
  publishes the body/opportunity before Bot application, with no early dispatch;
  the compatibility publisher-mediated path dispatches after admission. Failure
  propagates without rollback of already-created Collet/Method objects. No
  automatic retry, compensation or atomic cross-store publication/admission is
  implied.
- Six Work entrypoints resolve action/event/primitive/payload mappings from the
  instance's exact standard Task contract or explicit WorkflowProfile. G4 checks
  declared control-event mappings,
  then the referenced G4 machine remains the only transition authority. L6
  contains no copied state transition table.
- The existing lowered **L4/message/publish EVENT** delivery carries selected
  ProductProfile ID/version and actual MethodBinding ID in OperationContext.
  This is contextual correlation, not an L6 command, access credential or proof
  of remote acceptance. Tests exercise the local mock bridge, not live A2A.

The internal `AssigneeAdmissionCommand` and `DispatchedTaskInfo` require those
two refs; existing public user creation and Work wire fields remain unchanged.
The bounded signed-Bot route also handles Query/Apply for the installed profile;
see the
[commission wire contract](../products/task_commission/README.md#wire-contract-and-example).
Other products
may intentionally have no automatic Open publication contract. Such definitions
cannot be consumed by this publication use case.

## Instantiation and persistence

### Persistence ownership

[`persistence/repository.py`](persistence/repository.py) owns product selection,
product listing and the rich legacy composition adapter;
[`persistence/records.py`](persistence/records.py) owns its request/results and
`ProductRecordDecoder`. The actual primitive SQL repository, canonical ORM
models, Task/evidence checks and lower-only creation are in
[`L4 persistence`](../l4_context_primitives/persistence/).
The L6 adapter delegates primitive SQL operations to L4 while preserving legacy
rich results and atomic combined creation. The old shared
`context_collab/persistence/repository.py` path reexports this L6 adapter only;
it is not the L4 injection contract.

The composition root separately binds an exact L4 repository instance to the
L4-owned repository Protocol. It supplies `ProductRecordDecoder` at the storage
read seam so L6/G1/G4 can inspect pinned metadata from the same SQL snapshot,
without a second lookup or an upper-layer import in L4. Primitive creation does
not invoke that decoder or query a catalog. A standalone L4 repository instead
uses the lower-only decoder with no product dependency. The existing metadata
column/table names and tenant/env isolation remain unchanged.

### Explicit installation on an existing Collet

Pure L4 `create_collet(ColletCreateCommand, context)` does not select a Profile,
Method or bindings. Its input contains only space, responsible principal,
objective and zero/one/many designated executor references. To apply a product,
call the separate L6-owned
[`ProductInstantiationServiceProtocol`](operations/contracts.py):

```python
instance = product_instantiation.instantiate(
    ProductInstantiateCommand(
        collet_id=collet.collet_id,
        profile_ref=profile.ref,
        expected_version=collet.version,
    ),
    actor_context,
)
```

[`operations/product_instantiation.py`](operations/product_instantiation.py)
authorizes only the Collet initiator or responsible principal and resolves the
exact installed version; it never selects latest. The repository CAS installs
immutable selection on an untouched Collet/root Task (both revision 1), rejecting
stale, repeated or conflicting selections. It validates the selection against
the existing objective, typed executor and creation evidence. The original Task
and Event remain unchanged, as do their revisions and the Collet event head.
The L6 operation then invokes L5 initialization for the pinned Method and role references.

**Current product limit:** ScenarioRole's `executor_ref` source is scalar.
This installation path rejects multiple designated executors rather than dropping
all but the first; required-role rules may also reject unassigned Tasks. These
are upper-layer product constraints, not restrictions on L4 creation. Supporting
multi-subject product roles requires a separate contract evolution.

SQL selection and MethodStore writes are separate transactions. A SQL error
rolls back selection and no binding is created. A MethodStore error propagates;
SQL selection may already be committed, and the existing top-level recovery
adapter can rebuild that binding. Retrying installation cannot replace selection.
A method-independent Collet cannot be healed into an invented default Method.
[SQLite tests](../../../../../../../tests/community/repository/task/context_collab/test_product_instantiation.py)
cover these boundaries and the [commission journey](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py)
uses the injected operation before calling Open.

### Existing combined application/product flows

The existing `create_collaboration(CollaborationCreateCommand, context)` HTTP
facade is owned by [L6](operations/collaboration_creation.py); concrete product
publishers stay in `products/`. These upper-layer combined creation flows are not
the pure L4 command:

1. Generic creation validates the existing wire/method structure and G1 policy.
   Product services construct their declared product composition and also use G1.
2. `prepare_product_profile` verifies the product/optional-workflow/Method tuple
   and required role subjects, and negotiates against **local installed**
   capabilities. Creation facts pin the exact product version; the installed
   immutable definition selects domain/workflow or standard Task contract refs.
   A workflow-free definition requires `workflow_kind=None`; a mismatched
   explicit value is rejected, not ignored.
3. The repository atomically writes Collet, an independent root L4 Task,
   initial Event and ProductInstance metadata. Task has its own ID/revision;
   `current_snapshot` is a derived G3 response, not persisted Work authority.
   The instance occupies the reserved `product_instance` member of the existing
   `creation_facts` JSON column. L6 owns selection decoding; L4 Collet/Task do not
   depend on that decoder or own product configuration.
4. Existing L5 projection creates an explicit top-level MethodBinding. Its
   `profile_versions` come from the pinned product definition; its
   `role_binding_refs` identify the same declared roles as ProductInstance.
   Runtime role checks resolve their typed subjects from L4 Task. A role ref
   is a local, Collet-qualified `(role, participant)` reference, not an independent
   identity or resource permission authority. The installed task_commission@1
   profile uses pure Open;
   designation does not create participation. Only later COLLET_PARTICIPANT
   admission establishes a scoped Participant binding, validated alongside the
   application/decision and Method lifecycle. No Group roster or members are
   initialized for that product. Other Open + Group products retain their
   existing initial roster behavior.

**Transaction limit:** L5 projection remains a subsequent MethodStore write,
not part of the SQL transaction. A failed projection propagates as an error;
it can leave an incomplete instance requiring the existing top-level recovery
path. The current in-memory MethodStore also cannot preserve lifecycle changes
across restart. Persisting ProductInstance references does not solve that L5
storage limitation or make every intermediate runtime reference active.

## Advance, complete and compensate

Both generic `ContextCollabService.append_event` and product Work operations use
the same G4 product validation, followed by existing G4 transition/guard
resolution and repository CAS. G1 remains a separate prerequisite; L6 does not
authorize an operation merely because a role/template mentions it.

- Both standard Task contracts and the commission's explicit workflow select
  strict Work semantics: only declared Task control
  events from an allowed source state; designated typed executor only for
  progress and submit; public events; `percent` is an integer in `[0,100]` and belongs only
  to progress reporting, never a Message-based side channel. Existing G4 guards
  check artifacts and owner acceptance/cancellation. Terminal/out-of-order
  controls fail before writing. The installed profile additionally installs an admission prerequisite
  through shared G1: product and generic L4 start/progress/submit require the
  designated Bot's active, task-scoped Open/Group binding. Designation alone or
  admission to another task cannot authorize execution; revocation blocks it.
- `bbs_public_discussion@1` requires public messages; a non-null reply parent
  must be a Message, never the `task.created` skeleton. Missing/cross-context
  parents remain the repository's responsibility. Root body/replies may have
  null parent. This existing product rule now applies to generic writes too.
- Other v1 workflows preserve their established unmatched-event/no-transition
  behavior; strictness is not silently applied to every product. An ordinary
  Message never advances Task state/version. A genuinely partial legacy row
  without a Task can receive Messages, but cannot advance Work. A new row with
  a missing referenced Task is corrupt and rejected, not treated as partial.
- Completion and compensating actions reuse existing G4 transitions
  (`task.accepted`, `task.rejected`, `task.cancelled`). No second transition table
  or automatic saga/rollback executor is introduced.
- A generic stale request is rejected as a version conflict before source-state
  checks. Repository CAS still fences a concurrent writer after the read.

## Compatibility, read behavior and migration

- Public user creation fields and six-field creation payload remain unchanged.
  Generic creation and omitted product-service selection use the installed
  `task_commission@1` profile. The product service can explicitly select that
  exact definition; no dynamic registration API or new route is added. A product/workflow/Method mismatch is rejected before persistence, rather
  than accepted as unrelated labels.
- New product rows pin the product version and selected Task/workflow/domain
  references. Role checks compare identity **type and ID** against L4 Task;
  designation does not create an admitted participant. Legacy rows retain
  their old structural ID-based G1 matching; no backfill silently changes their
  identity semantics.
- Unversioned legacy rows retain their original JSON shape and remain readable
  without fabricated persisted negotiation or ProductInstance. When executing a
  known legacy product, its unversioned definition means **v1**, never latest;
  incompatible historical combinations are not silently repaired.
- Pinned rows with missing/mismatched instance, role, version or creation-evidence
  references fail closed. Reading a historical selection never renegotiates
  against today's installed capabilities. Top-level recovery reuses pinned
  Method versions and role refs rather than substituting installed latest.
- Product summary listing retains its existing tolerant creation-facts decode;
  it is a discovery projection, not evidence of validated ProductInstance or
  creation-event references. Detail reads and operation loads use the strict
  instance validation above. A listed row may consequently fail strict loading.
- G4 workflow lookup for product operations uses an exact `(ID, version)` key.
  Name-only lookup remains for unprofiled lower-layer callers.
- Migration policy is `new_instances_only`: existing instances are not upgraded
  in place. Retain historical definitions/machines. There is no live migration
  operation, implicit downgrade, or data backfill in this change.

## Scope limits and conformance evidence

The current selection mode is `local_exact_match`, **not** cross-Agent
negotiation. `L1/A2A` is the declared cross-Agent delivery boundary, not evidence
that this change implements remote delivery, continuation credentials, or access
to another Agent's session. No A2A session or identity privilege is fabricated.

Governance IDs identify the existing structural G1, optimistic-version G2,
event-projection G3 and shared product G4 implementations. They are not claims
of complete external policy integration. ProductInstance links the goal through
its Collet and initial Event; full Outcome/Metric/Evidence generation and
cross-layer evaluation correlation are not implemented here. Nested template
execution, general Decision orchestration, remote negotiation, live migration,
automatic compensation and persistent Method lifecycle recovery remain outside
this minimum contract increment. Do not describe it as full ACCS conformance.

## Local commission product acceptance

The [commission user-journey test](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py)
uses the actual `TaskModule` / `TaskPersistenceModule`, isolated SQLite and the
real catalog seam to deploy both an explicit-domain/workflow `task_commission@1`
and an autonomous `task_commission@2` with neither field supplied. Product
staff define/deploy; business staff create/designate; the case directly invokes L5 Open's
`query_opportunities` with the Bot actor and explicit context/binding, rather
than a built-in working-Bot method. Product-wide signed HTTP Query is checked
separately against that result. The main journey explicitly calls L5
SubmitApplication → Screen → Admit with the Bot/policy actors; separate
regressions exercise signed HTTP Apply. Admission establishes the real
task-scoped Participant binding before execution, with no Group roster/member.
The Bot then reports
plan/code/test/PR-proposal steps, submits on successful return, and the owner
explicitly accepts. Product observations use service responses/history; focused
MethodStore assertions verify nonmutation and actual scoped admission.

The journey's compatibility offer explicitly declares only local L2–L5 evidence.
The authored source's L1 requirement is preserved: the local catalog does not
advertise L1, and a required L1 still rejects negotiation. Mock HTTP/outbound
transport is not a reason to fabricate an interoperability capability.

Negative cases check unrelated Bots, forged contexts, repeated Query, duplicate
Apply, task isolation, revocation through both Work entrypoints and signed-tenant
SQL discovery. Execution failure must not auto-submit in either the installed self-application
path or the compatibility path. The
harness signs a local principal; it does not verify gateway forwarding or actual
outbound receipt. Outbound still uses local `MockA2aDelivery`. Open handles
admission; progress/submission remain L4 Work, never Open operations.

The separate [protocol/persistence integration suite](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_protocol_integration.py)
retains pinned ProductInstance/role checks, the single Open binding,
publisher-mediated ADMITTED-before-dispatch checks, A2A envelope/bridge
correlation and independent repository/SQL reads. Its observing delivery mock
also verifies that Work actions do not republish or redispatch. Companion cases
cover rejection/resubmission, cancellation, unchanged instance/admission facts
and rejected operations without writes. These are implementation-integration
checks, not assertions required in the product user journey.

Run from `apps/backend/`:

```sh
# Product user journey only.
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py -q

# Internal protocol/persistence integration.
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/core/task/context_collab/products/test_task_commission_protocol_integration.py -q
```

The journey adds local HTTP/signature verification for simulated Bot reports;
the protocol suite remains service-level integration. Neither verifies live
remote A2A, autonomous Bot/LLM execution, artifact upload, production-database
behavior or durable MethodStore restart. The journey creates temporary plan/code/test/PR-proposal files and runs three
local code checks; their URIs are references, not transferred/uploaded artifacts
or proof of a real remote PR. L5 MethodStore still uses its existing single-tenant
axes; SQL signed-tenant tests do not establish full MethodStore multitenancy.

Verification owners:

- [Commission declaration/adapter tests](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_l6.py).
- [L6 dependency/installation gates](../../../../../../../tests/community/core/task/context_collab/test_l6_layering.py): no reverse imports from concrete products, DI or legacy compatibility entrypoints.
- [L6 service placement/compatibility tests](../../../../../../../tests/community/core/task/context_collab/test_l6_service_placement.py): product orchestration ownership, canonical type identity and inherited L5 operations.
- [DI singleton wiring](../../../../../../../tests/community/core/task/context_collab/test_service_real_repository.py).
- [Optional-profile runtime tests](../../../../../../../tests/community/core/task/context_collab/test_optional_product_profiles.py): both contract modes, action/role/acceptance fences, exact machine selection, JSON validation and recovery without renegotiation.
- [HTTP profile-version translation tests](../../../../../../../tests/community/adapters/http/task/test_commission_profile_versions.py): canonical positive versions, malformed selectors and oversized integer validation.
- [Definition/compatibility tests](../../../../../../../tests/community/core/task/context_collab/test_l6_contracts.py).
- [SQLite runtime/bypass/selection tests](../../../../../../../tests/community/core/task/context_collab/test_l6_runtime_contract.py).
- Existing `tests/community/core/task/context_collab/products/` tests enforce
  product behavior and the product import whitelist; architecture tests enforce
  transport/framework/dependency boundaries.
- Change intent and compatibility impact:
  [2026-11-13 L6 product contracts spec](../../../../../../../specs/2026-11-13-context-collab-l6-product-contracts/spec.md).

## Bot execution reporting follow-up (2026-11-13)

The [commission product boundary](../products/task_commission/README.md) now owns
the bounded signed-Bot A2A ingress and its wire/example/operational limits. It
adds broader HTTP authentication evidence alongside the simulated Bot journey;
outbound delivery is still mock, not live remote execution.

`WorkflowAction.required_role` is an optional additional L4 role restriction;
`None` adds no role restriction and does not bypass G1 or machine guards. The
catalog rejects references to undeclared scenario roles. Shared G4 applies a
declared role rule to both product and generic L4 writes. Commission progress
requires `selected-agent`; typed legacy executor matching remains available for
unversioned rows. This fixes enforcement of the existing v1 assignee
responsibility, without changing topology, upgrading profiles or rewriting old
events. Publishers can still review results, but cannot report Bot progress.

The initial L6 contract increment added no HTTP endpoint; this follow-up uses
the existing bounded signed-Bot route. No transport or concrete product implementation has been
added to L6. The new transport adapter injects the product API Protocol.

## Independent Task authority follow-up (2026-11-13)

The [collaboration boundary](../README.md) owns the Task/Collet distinction,
read-projection semantics and [database cutover](../sql/2026_11_13_context_collab_task.sql).
Plain creation facts hold the selected L6 references; CurrentSnapshot and
ProgressSnapshot are G3 views. G4 consumes persisted Task, and generic/product Work uses typed
Task/Collet version fences. Existing product profile versions, creation payloads
and public product IDs are unchanged. The existing `new_instances_only` policy
concerns product-profile changes; it does not remove the prerequisite storage
migration or permit mixed snapshot/Task-authoritative writers.

## Layer-owned operation boundary

Definition and Service API modules remain free of runtime/persistence and
transport dependencies. They may consume G1's `ActorContext` value contract,
not its authorizer implementation. Runtime composition and policy adapters
belong in `operations/`, separate from these definitions.

L6 owns product instantiation, the legacy combined creation entrypoint and
product-aware policy/bootstrap/recovery adapters. It composes by invoking L5
operations, never by reimplementing Method rules or Work transitions.
[`operations/method_bootstrap.py`](operations/method_bootstrap.py) translates
creation identities and pinned catalog versions into L5 `ResolvedMethodSpec`.
L5 owns binding/roster creation and consumes an injected recovery port; it never
imports L6 to heal a binding. The L6
[legacy lifecycle constructor](operations/method_lifecycle.py) only assembles that
adapter for old `product_catalog=` callers; its Method operations are inherited
from L5. New DI injects the L5 implementation and recovery port separately.

L6 product Task/workflow selection supplies a machine ID/version to L4 Work,
not a ProductProfile object. `services/` and root APIs only reexport layer-owned
implementations/contracts, and L6 must not import them back. See the
[operation ownership map](../README.md#operation-ownership) for ports, compatibility
imports and preserved restart/transaction limitations.
