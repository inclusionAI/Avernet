# Context Collab L6 Product Contracts

- Date: 2026-11-13
- Status: Implemented; verification recorded below. Not merged, host-integrated
  or deployed by this task.
- Owner: Backend `core/task/context_collab`.
- Durable contract: [L6 product contracts](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md).
- Architecture authority: [constitution](../../../../docs/arch/arch.rules.md) and
  [Context Boundaries](../../../../docs/arch/context-boundary-format.md).

## Problem and baseline

L6 previously supplied product/workflow wire IDs, while definitions, roles and
workflow rules were implicit across individual services. Persisted contexts did
not identify selected profile versions or record a ProductInstance. Generic
append could bypass commission-specific sequence/executor validation and the
BBS Message-parent rule.

This increment supersedes only the enum-only/no-profile-catalog trimming in
[2026-11-12 L6 product forms §6](../2026-11-12-context-collab-l6-product-forms/spec.md).
It installs an immutable code-owned catalog, **not** a mutable registry or profile
database. Existing topology and lower-layer state authority remain unchanged.
The older evidence is retained rather than rewritten.

## Decisions and scope

1. Define ProductProfileDefinition, MethodCompositionTemplate, ScenarioRole,
   DomainProfile, WorkflowProfile, compatibility/governance requirements and
   ProductInstance as transport-free contracts. These complement, not replace,
   the existing wire enums.
2. Pin product/workflow/domain and required Method versions. Required capabilities
   must match exactly; missing required capabilities reject, omitted optional
   capabilities are explicit. Current selection is local installed-capability
   matching, never an invented remote Agent negotiation.
3. Materialize ScenarioRole as actual L4 Participant/RoleBinding facts. Share
   canonical Collet-qualified RoleBinding references with the L5 top-level
   MethodBinding and ProductInstance. L6 grants no L3 Resource permissions.
4. Store ProductInstance beside ContextSpec in the existing JSON column, within
   the existing SQL creation transaction. No new tables/columns or schema
   migration. MethodStore projection remains a subsequent, non-atomic write;
   failures propagate and may require recovery.
5. Both generic append and product Work use shared G4 validation. Only commission
   opts into strict control sequencing and designated-executor submission. G4
   machine transitions remain the sole source of valid source/target states.
6. Exact workflow version lookup replaces latest/name-only lookup for products.
   Existing generic/unprofiled name-only and no-transition behavior remains.
7. Installed definitions preserve this checkout's current topology: team_todo =
   Single; discussion/help products = Open + Group; task_commission = Open.
   This is not a universal topology claim about public communities or executors.
8. Existing API Protocol re-exports are retained. Their two missing exact
   dependencies are added to the API Context Boundary; no implementation import
   permission or architecture waiver is added.

## Contract and compatibility impact

- HTTP creation fields and the six creation-event payload keys remain unchanged:
  objective, resource_refs, method_kind, executor_ref, product_kind, workflow_kind.
  No request version selector or new endpoint is introduced.
- Product/workflow/method mismatches reject before persistence (422 through the
  existing HTTP error mapping). Callers that previously supplied unrelated
  labels must choose a compatible composition.
- New product instances pin v1 and explicit roles. G1 compares identity type and
  ID for these roles. Legacy unpinned rows preserve the old structural ID-only
  matching; no backfill silently changes their authority.
- A known legacy unversioned profile means v1, not latest. Legacy JSON shape and
  absence of ProductInstance remain supported. Invalid historical combinations
  are not silently upgraded/repaired when used for product execution.
- Pinned missing/corrupt selections or role/version/evidence mismatches fail
  closed. Historical reads/recovery validate stored facts without negotiating
  against current installed capabilities.
- Commission generic writes cannot bypass executor submission, sequence,
  terminal, public visibility, Task-control primitive or progress constraints.
  `percent` is restricted to progress events, not arbitrary Message payloads.
- BBS public-discussion replies with a parent must target Message events, not the
  initial Task skeleton; root Message parent may be null. Generic writes now obey
  the same existing product rule. Repository parent existence/scope checks remain.
- Other products retain unmatched-event and snapshotless-row semantics. This is
  intentionally not a blanket strict-state-machine change.
- Migration is new-instances-only. Keep old definitions/machines; no live upgrade,
  automatic downgrade or automatic compensation executor is provided.

## Acceptance criteria and evidence

- [Definition tests](../../tests/community/core/task/context_collab/test_l6_contracts.py):
  immutable installed definitions, exact versions, required/optional capability
  outcomes, code-owned custom definitions, role mapping and version rejection.
- [Runtime tests](../../tests/community/core/task/context_collab/test_l6_runtime_contract.py):
  real SQLite + Open Method adapter; versioned persistence and RoleBinding refs;
  generic/product bypass rejection; recovery without renegotiation; corrupt
  selection rejection; legacy/unprofiled compatibility.
- [Method delivery tests](../../tests/community/endpoints/test_context_collab_methods_endpoints.py):
  compatible creation, mismatch 422, valid top-level Open commission, existing
  lifecycle/Group/Swarm/Open operations and conflict mapping.
- Product-layer whitelist and backend architecture suites remain enabled.

### Initial contract increment verification record

Intermediate runs: core/repository baseline regression **871 passed**; first
new-contract run **44 passed**. A broader run found **6 failed / 1357 passed**:
five endpoint fixtures used incompatible product labels; one architecture gate
found existing API Protocol imports missing from its declaration. The imports
were confirmed present in HEAD; only their exact contract declarations were
added. These are diagnostic results, not a passing final gate.

Final rerun (2026-11-13): **1368 passed, 43 warnings, 81.15 seconds**.
No failures remain in the executed scope:

```sh
cd apps/backend
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/core/task/context_collab \
  tests/community/repository/task/context_collab \
  tests/community/endpoints/test_context_collab_endpoints.py \
  tests/community/endpoints/test_openapi_context_collab_endpoints.py \
  tests/community/endpoints/test_context_collab_methods_endpoints.py \
  tests/community/architecture -q --disable-warnings
```

The equivalent final invocation used repository-root paths. Changed-file Ruff
checks and `git diff --check` pass; all 23 changed Python source/test files are
below 1,000 lines and all relative links in changed Markdown resolve. No full
Backend suite, production database, external Agent/A2A integration or deployment
validation was performed. The final scope includes exact-machine regression
coverage for both pinned and unversioned legacy commission Work operations.

## Remaining gaps / non-goals

- No complete ACCS conformance or deployment claim. Local governance IDs identify
  current structural implementations, not external policy integrations.
- No remote negotiation/L1 bridge or credentials to resume another Agent session;
  cross-Agent delivery remains declared as L1/A2A, not implemented here.
- No general workflow engine, nested composition executor, automatic saga,
  general Decision orchestration, full Outcome/Metric/evaluation evidence or live
  migration. ProductInstance contains references, not a second lifecycle.
- The in-memory MethodStore still loses lifecycle changes on restart; top-level
  recovery cannot make those changes durable. SQL + Method projection is not one
  transaction. A persisted reference does not guarantee an active runtime binding.
- Runtime installation of a new product requires explicit code/composition/test
  changes; constructing a custom catalog is not dynamic registration.

Product summary lists retain their pre-existing tolerant ContextSpec-only read
path. A listed summary is not evidence of a valid ProductInstance; strict detail
and operation loads can still reject that row. This increment does not turn the
listing API into a conformance audit.


## Follow-up: declaration-driven task_commission consumption (2026-11-13)

### Intent and propagation

The initial increment pinned product composition but left commission admission,
action mappings and dispatch prerequisites scattered across its consumers. This
follow-up makes the existing v1 semantics explicit in a dedicated L6 definition;
it does not change Open into Single or introduce another state machine.

- `l6_products/task_commission.py` owns the complete definition and six Work
  action mappings. `publication.py` declares the supported publisher-mediated
  Open admission sequence, participation/capacity, ADMITTED dispatch gate and
  propagate-without-rollback policy. Catalog checks link these roles to required
  typed subjects and reject incomplete publication definitions.
- Creation reads the selected Profile's Method/Workflow and explicitly pins the
  exact selected version before persistence. Explicit selection rejects already
  pinned/materialized inputs; it is not an instance migration API.
- Product-to-adapter `AssigneeAdmissionCommand` and product-to-delivery
  `DispatchedTaskInfo` now require `product_profile_ref` and actual
  `method_binding_ref`. All repository callers/implementations/tests were updated.
  There is no HTTP/public creation or Work-command schema change.
- The bounded DI adapter consumes the exact selected publication policy and
  retains the publisher ActorContext. It verifies publisher accountability and
  top-level binding identity before side effects. Adapter and product both reject
  a non-ADMITTED receipt; body append and dispatch do not run in that case.
- Work loads exact historical action mappings. Shared G4 checks declared control
  events/primitives; existing G1, G4 machine guards and repository CAS remain.
- Existing mock L1 delivery remains a lowered L4 notification, now carrying
  selected product version and actual MethodBinding refs. No remote session
  authority or delivery-to-completion inference is added.

### Compatibility and limits

This extracts current semantics from an uncommitted implementation, not an upgrade
of a released profile. Future topology/behavior changes still require a new
version. Other products intentionally retain no automatic Open publication use
case. The initial increment's storage/atomicity/evidence limits remain; no
arbitrary template engine, rollback executor or live remote integration is added.
The earlier “no L1 bridge” scope statement refers to no newly implemented remote
transport: this follow-up enriches an already-existing local mock delivery bridge.

Reusable contract and navigation updated in the
[L6 README](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md).

### Follow-up verification

The first focused run found one direct test construction missing the new required
refs (**1 failed, 72 passed**); the consumer was updated rather than weakening the
contract. The subsequent product/definition/SQLite runtime suite passed:
**119 passed, 17 warnings, 2.41 seconds**. This evidence is separate from the
initial increment's 1368-test checkpoint above.

Full affected regression (same command/scope as the initial checkpoint above):
**1388 passed, 43 warnings, 86.98 seconds**. Includes product, generic entrypoint,
repository, HTTP/OpenAPI/method endpoints and architecture suites. No failures
remain in the executed regression scope. Changed-file Ruff check, line limits,
relative Markdown links and `git diff --check` pass. No full Backend suite,
production DB or live remote Agent/A2A test was run. No commit, merge, host
integration or deployment is claimed.


## Follow-up: product-owned definitions and DI installation (2026-11-13)

### Ownership and dependency corrections

This follow-up supersedes the **file ownership/installation** of the preceding
checkpoint, not the v1 business semantics or its recorded verification:

- Moved the concrete commission definition from `l6_products/task_commission.py`
  to `products/task_commission/profile.py`; moved `DesignatedOpenAdmission` and
  its fixed publisher-mediated step sequence to that product's `publication.py`.
- L6 keeps shared contracts, exact immutable `ProductCatalog`, generic Open
  participation/role/capacity validation, delivery gate and instance helpers.
  Concrete policy validation is dispatched to the product-owned strategy; the
  generic policy does not hardcode commission's typed subject sources.
- Moved the other four installed compatibility definitions out of the generic
  catalog into `products/legacy_profiles.py`, preserving topology and v1 IDs.
- `di/modules/context_collab_products.py` explicitly selects definitions;
  `TaskModule` binds one catalog per injector. Required constructor/helper
  injection propagates the **same** catalog through product/generic creation,
  repository creation/read, L5 projection/recovery and shared G4 checks. No
  default catalog, hidden fallback or product self-registration remains in L6.
- `actor_has_role` now checks actual L4 facts only, with no catalog selection.
  Shared G4 retains the typed executor check for unversioned legacy commission
  rows; the existing legacy G1 compatibility boundary is unchanged.
- Architecture tests scan L6 for product/DI reverse imports (including relative
  import probes), alongside the existing unchanged product import whitelist.
  SQLite tests exercise an injected v2-only catalog, restart/recovery without
  renegotiation, exact selected domain/roles and shared G4 using v2 instead of
  installed v1 defaults. Empty installation rejects both creation entrypoints.
  DI tests assert shared catalog identity and per-injector isolation.
- Method test fixtures now explicitly supply catalogs. Previously oversized
  Group/Open/Swarm tests are split into shared fixture helpers, value/store,
  lifecycle and guard responsibilities; no cases or gates are removed.

No HTTP contract, topology, version migration, new workflow engine or Resource
permission is introduced. `task_commission@1` remains **Open**, not Single or
Open + Single. All earlier storage/atomicity/remote validation limits remain.

### Verification

Core/repository propagation regression: **939 passed, 17 warnings, 6.26 seconds**.
The first added DI test had an incorrect BBS Protocol import path and failed
collection; it was corrected to the existing `products/service_api.py` contract.
The focused ownership/catalog/runtime/DI suite then passed: **64 passed,
17 warnings, 2.11 seconds**. An initial broad invocation referenced a nonexistent
DI test path and collected no tests; the corrected path is included below.

Final affected regression: **1417 passed, 43 warnings, 81.34 seconds**:

```sh
cd apps/backend
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/core/task/context_collab \
  tests/community/repository/task/context_collab \
  tests/community/endpoints/test_context_collab_endpoints.py \
  tests/community/endpoints/test_openapi_context_collab_endpoints.py \
  tests/community/endpoints/test_context_collab_methods_endpoints.py \
  tests/community/architecture \
  tests/community/di/test_task_module.py \
  tests/community/repository/task/test_task_persistence_module.py \
  -q --disable-warnings
```

No failures remain in that executed scope. Changed-file Ruff and
`git diff --check` pass; all 65 changed/new Python files are below 1,000 lines,
and changed Markdown relative links resolve. AST comparison confirms all 121
original Group/Open/Swarm test methods remain after splitting. No full Backend
suite, production database, live A2A, host integration or deployment validation
was performed; no push was made by this follow-up.

## Follow-up — local product acceptance through installed profiles

The commission end-to-end module now obtains `TaskCommissionServiceProtocol`
through the actual `TaskModule` / `TaskPersistenceModule` composition. It no
longer imports another test's fake repository or directly constructs the product
service. Only `DatabasePlugin` (isolated SQLite) and `A2aDeliveryPort` (observing
local mock) are replaced; product installation, repository, Open admission
adapter/services, structural G1 and shared G4 execute normally. No runtime
contract or production implementation changed in this follow-up.

The main case publishes business fields, verifies pinned L6 selection and L4
role facts, verifies one Open binding and real admission, observes ADMITTED and
persisted facts at send time, then simulates the Bot using the notification's
Collet ID through product query/Work APIs. Owner acceptance, full public event
history and an independent repository/SQL read verify completion and an unchanged
ProductInstance. Twelve companion cases exercise rejection and
resubmission, typed-identity/role/sequence/artifact guards without writes, and
cancellation from three states with terminal write rejection. Task cancellation
does not implicitly revoke L5 admission.

Reusable test entry and scope limits are documented in the owning
[L6 README](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md#local-commission-product-acceptance).

### Verification

The revised module initially passed **13 tests**. After the final assertions and
formatting, the affected product/L6/repository/DI regression passed **174 tests,
17 warnings, 3.35 seconds** using:

```sh
cd apps/backend
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/core/task/context_collab/products \
  tests/community/core/task/context_collab/test_l6_contracts.py \
  tests/community/core/task/context_collab/test_l6_layering.py \
  tests/community/core/task/context_collab/test_l6_runtime_contract.py \
  tests/community/core/task/context_collab/test_service_real_repository.py \
  tests/community/di/test_task_module.py \
  tests/community/repository/task/test_task_persistence_module.py \
  -q --disable-warnings
```

Changed-test Ruff and `git diff --check` pass; the changed source file is below
1,000 lines. This is local service-level product integration, not live A2A,
HTTP authentication, autonomous Bot/LLM execution, artifact upload, production
DB or durable MethodStore restart evidence. No full Backend suite, push or
deployment was performed by this follow-up.
