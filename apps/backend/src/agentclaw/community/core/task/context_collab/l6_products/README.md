# L6 product contracts

Owner: Backend `core/task/context_collab`. This layer defines versioned product
composition; it is not another Task engine, identity registry or permission
system. These are internal, transport-free domain contracts, not a new Service
API or product-specific wire protocol.

## Provides and consumes

| Contract | Owner/source | Meaning |
| --- | --- | --- |
| ProductProfileDefinition | [contracts.py](contracts.py) | Versioned composition, scenario roles, domain/workflow mappings, compatibility and G1–G4 implementation requirements. |
| MethodCompositionTemplate | [contracts.py](contracts.py) | Required/optional Method versions and instantiation scope. Current runtime supports one explicit Collet-scoped top-level binding, including the existing composite binding representation. |
| ScenarioRole | [binding.py](binding.py) | Maps owner/assignee to actual L4 Participant and RoleBinding facts. No L3 grant is created. |
| DomainProfile | [contracts.py](contracts.py) | Maps product vocabulary to L3 Resource and L4 Collet/Task/Message/Artifact authority, not duplicate product-owned entities. |
| WorkflowProfile | [contracts.py](contracts.py) | References a versioned G4 machine over L4 Task state, or explicitly declares a nontransition event stream. Includes completion states and compensating control events. |
| OpenAdmissionPolicy / OpenPublicationProfile | [publication.py](publication.py) | Shared participation/role/capacity constraints, ADMITTED delivery gate and partial-failure behavior. Concrete products own specialized admission strategies and step order; L6 does not execute them. |
| ProductInstance | [instance.py](instance.py) | Durable selection/evidence references: product/workflow/domain versions, actual top-level MethodBinding ID, selected Method versions, RoleBinding references, governance requirements and negotiation result. No independent lifecycle state. |
| Compatibility selection | `negotiate(profile, offer)` | Exact required profile/Method/ACCS/layer/extension/governance match. Reports missing required and omitted optional capabilities separately. Never silently chooses a newer version or weaker policy. |

`profiles.py` retains the wire ID enums for compatibility; IDs alone are not
profile definitions. L6 supplies the immutable `ProductCatalog` mechanism, not
an installed/default global catalog. Product teams author concrete definitions in
`products/`; the composition root explicitly installs them in
[`di/modules/context_collab_products.py`](../../../../di/modules/context_collab_products.py).
`TaskModule` provides one catalog per injector and injects that same instance into
repositories, generic/product services, Method lifecycle/recovery and the bounded
Open adapter. Pure selection, instance and G4 helpers require this catalog
explicitly; none fall back to global/default product installation. An empty or
incomplete installation rejects unknown profiles rather than silently recovering
one from another catalog. Historical definitions must remain installed for their
instances to load and execute.

Installing a profile requires explicit code/composition changes and tests, not an
enum change. Constructing a separate catalog does **not** dynamically register a
runtime product. Tests may assemble isolated catalogs. There is no mutable
runtime registry, catalog database or registration API.

L6 pure modules consume L2–L5 value contracts and the installed L5 profile
catalog. They do not import concrete product definitions/services, DI,
repositories, HTTP or remote clients. Application services and the existing
repository compose the contracts.
Shared G4 enforcement lives in
[`g4_contracts_consistency/product_contract.py`](../g4_contracts_consistency/product_contract.py).

## Installed v1 definitions

| Product ID | Workflow ID | Existing Method topology | Executor |
| --- | --- | --- | --- |
| team_todo | team_todo_execution | Single | Required |
| public_discussion | forum_public_discussion | Open + Group | Optional |
| bbs_public_discussion | bbs_public_discussion | Open + Group | Optional |
| bbs_help_request | bbs_help_request_goal | Open + Group | Optional |
| task_commission | task_commission_goal | Open | Required |

This table records **this checkout's existing implementations**. It does not
infer that every public discussion is Group, or that a designated executor
implies Single. Changing an installed profile's topology or lower-layer meaning
requires a new version; preserve old definitions for existing instances.

## task_commission consumes its selected definition

[`products/task_commission/profile.py`](../products/task_commission/profile.py)
owns the complete installed `task_commission@1` definition, rather than placing
business templates in L6 or flags in a generic profile factory. Its specialized
publisher-mediated `DesignatedOpenAdmission` belongs in
[`products/task_commission/publication.py`](../products/task_commission/publication.py).
The other four existing compatibility definitions live in
[`products/legacy_profiles.py`](../products/legacy_profiles.py), not L6. Neither
module registers itself; only DI chooses the installation.

The product service and its bounded Open adapter consume the injected definition:

- Creation explicitly selects `TASK_COMMISSION_PROFILE_REF`, reads its Method
  composition and Workflow ref, and calls `prepare_product_spec` with that exact
  selection. Explicit selection is creation-only: the input must have no pinned
  versions or materialized role facts. Generic creation still selects v1; neither
  path selects latest or migrates an existing instance.
- The publication contract maps `assignee` to the required typed executor and
  `owner` to the required principal. Catalog construction delegates role validation
  to the product's specialized policy. L6 only requires the referenced roles to
  exist and be required; it does not impose commission's executor/principal
  source mapping on all Open products. The implemented strategy is
  `publisher_mediated`: the publisher remains the
  actor and accountable principal throughout; this does not impersonate the Bot
  or claim that it autonomously applied. G1 still authorizes every L5 operation.
- The DI adapter consumes the exact selected publication policy:
  `publish_opportunity → submit_application → screen → admit`, with
  `SINGLE_CONTRIBUTION` and capacity 1 in v1. The product passes the actual
  projected top-level MethodBinding ID, not a publisher/executor identity.
  Unsupported strategies, step sequences and delivery policies fail closed;
  the bounded adapter also rejects a generic policy that is not this product's
  designated-executor strategy. There is no arbitrary workflow DSL executor.
- Both adapter and product check the L5 receipt is `ADMITTED` before dispatch.
  The product also checks this before appending the body Message. Failure
  propagates without rollback of already-created Collet/Method objects. No
  automatic retry, compensation or atomic cross-store publication is implied.
- Six Work entrypoints resolve action/event/primitive/payload mappings from the
  instance's exact WorkflowProfile. G4 checks declared control-event mappings,
  then the referenced G4 machine remains the only transition authority. L6
  contains no copied state transition table.
- The existing lowered **L4/message/publish EVENT** delivery carries selected
  ProductProfile ID/version and actual MethodBinding ID in OperationContext.
  This is contextual correlation, not an L6 command, access credential or proof
  of remote acceptance. Tests exercise the local mock bridge, not live A2A.

The internal `AssigneeAdmissionCommand` and `DispatchedTaskInfo` require those
two refs; public creation and Work command fields are unchanged. Other products
may intentionally have no automatic Open publication contract. Such definitions
cannot be consumed by this publication use case.

## Instantiation and persistence

1. Generic creation validates the existing wire/method structure and G1 policy.
   Product services construct their declared product composition and also use G1.
2. `prepare_product_spec` verifies the product/workflow/Method tuple and required
   role subjects, negotiates against **local installed** capabilities, pins
   product/workflow versions, and materializes L4 Participant/RoleBinding facts.
3. The existing repository atomically writes Collet, initial Event, snapshot and
   ProductInstance in its existing transaction. The instance occupies the
   reserved `product_instance` member of the existing `context_spec` JSON column;
   the L4 ContextSpec decoder does not depend on L6 or own that instance.
4. Existing L5 projection creates an explicit top-level MethodBinding. Its
   `profile_versions` come from the pinned product definition; its
   `role_binding_refs` identify the same L4 facts as ProductInstance. A role ref
   is a local, Collet-qualified `(role, participant)` reference, not an independent identity or
   resource permission authority.

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

- `task_commission@1` selects strict Work semantics: only declared Task control
  events from an allowed source state; designated typed executor only for
  progress and submit; public events; `percent` is an integer in `[0,100]` and belongs only
  to progress reporting, never a Message-based side channel. Existing G4 guards
  check artifacts and owner acceptance/cancellation. Terminal/out-of-order
  controls fail before writing.
- `bbs_public_discussion@1` requires public messages; a non-null reply parent
  must be a Message, never the `task.created` skeleton. Missing/cross-context
  parents remain the repository's responsibility. Root body/replies may have
  null parent. This existing product rule now applies to generic writes too.
- Other v1 workflows preserve their established unmatched-event/no-transition
  and snapshotless-row behavior; strictness is not silently applied to every
  product. A valid ordinary Message never advances Task state.
- Completion and compensating actions reuse existing G4 transitions
  (`task.accepted`, `task.rejected`, `task.cancelled`). No second transition table
  or automatic saga/rollback executor is introduced.
- A generic stale request is rejected as a version conflict before source-state
  checks. Repository CAS still fences a concurrent writer after the read.

## Compatibility, read behavior and migration

- Public creation fields and six-field creation payload remain unchanged. New
  instances select v1; this change adds no request version selector or HTTP
  endpoint. A product/workflow/Method mismatch is now rejected before persistence,
  rather than accepted as unrelated labels.
- New product rows pin product/workflow versions and store explicit role facts.
  G1 compares identity **type and ID** through these facts. Legacy rows retain
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
- Product summary listing retains its existing tolerant ContextSpec-only decode;
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
uses the actual `TaskModule` / `TaskPersistenceModule` assembly and installed
product catalog. Infrastructure wiring stays in its fixture; the test body
receives only `TaskCommissionServiceProtocol`, supplies business commands and
asserts product responses, list/detail views and the exposed activity history.
It does not inspect ProductInstance/MethodStore, parse delivery envelopes or
query SQL.

The journey is owner publication and discovery, simulated assignee start,
progress and submission, then owner review and acceptance. The harness passes
the published task ID to the simulated Bot; it does not claim that the Bot
received an actual notification. Real product/repository services, the Open
adapter and G1/G4 execute behind the API, using isolated SQLite and the default
local `MockA2aDelivery`.

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

Both suites provide local service-level integration evidence, not HTTP
authentication, live remote A2A, autonomous Bot/LLM execution, artifact upload,
production-database behavior or durable MethodStore restart evidence. Artifact
URIs are recorded references; the tests do not create or transfer files.

Verification owners:

- [Commission declaration/adapter tests](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_l6.py).
- [L6 dependency/installation gates](../../../../../../../tests/community/core/task/context_collab/test_l6_layering.py).
- [DI singleton wiring](../../../../../../../tests/community/core/task/context_collab/test_service_real_repository.py).
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
adds HTTP authentication evidence beyond the two service-only suites above;
outbound delivery is still mock, not live remote execution.

`WorkflowAction.required_role` is an optional additional L4 role restriction;
`None` adds no role restriction and does not bypass G1 or machine guards. The
catalog rejects references to undeclared scenario roles. Shared G4 applies a
declared role rule to both product and generic L4 writes. Commission progress
requires `selected-agent`; typed legacy executor matching remains available for
unversioned rows. This fixes enforcement of the existing v1 assignee
responsibility, without changing topology, upgrading profiles or rewriting old
events. Publishers can still review results, but cannot report Bot progress.

The earlier no-new-HTTP statement describes the initial L6 contract increment,
not this follow-up. No transport or concrete product implementation has been
added to L6. The new transport adapter injects the product API Protocol.
