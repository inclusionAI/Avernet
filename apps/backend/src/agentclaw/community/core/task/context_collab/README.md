# Context collaboration: authority and read projections

Owner: Backend `core/task/context_collab`. This is the ACCS collaboration path,
separate from the older goal-driven TaskGraph framework in the parent directory.
It implements bounded local contracts, not full ACCS conformance.

## Context Boundary

- **Provides:** transport-free generic collaboration services, L4 primitives,
  L5 Method services, L6 product composition, product services and shared G1–G4
  enforcement. Generic/application contracts remain in `service_api.py`; L4 write contracts
  live in `l4_context_primitives/operations/contracts.py`, Method contracts in
  `l5_methods/operations/contracts.py`, and product contracts in
  `products/*/service_api.py`. Root API imports remain compatible.
- **Consumes:** injected `DatabasePlugin`, product catalog, MethodStore,
  authorization and delivery ports. DI chooses concrete implementations;
  HTTP adapters translate requests but do not own Task transitions.
- **Dependencies:** L4 primitive models and narrow write operations use L2/L3
  references and domain errors, not L5/L6 composition types or read projections.
  The northbound `l4_context_primitives/context_collab_service.py` orchestrates
  these operations with G1–G4, L6 profile validation and L5 Method bootstrap;
  its placement does not make composition metadata part of the Collet model.
  L6 composes lower-layer contracts; concrete business templates stay in
  `products/`. G1–G4 cross-cut these layers.

## Operation ownership

Operation implementation follows the owner of the affected authority, not the
HTTP route or a shared `services/` folder. This is a local implementation layout
for ACCS semantic boundaries, not an ACCS-mandated Python directory tree.

```text
l4_context_primitives/
  context_collab_service.py  # northbound create/append/query orchestration
  operations/
    contracts.py           # EventAppendCommand, storage ports and append intent
    collet_lifecycle.py     # create/activate/suspend/resume/complete/archive
    work.py                # G4 Task resolution, Task fence and atomic append
l5_methods/operations/
  contracts.py           # Method commands, results and public protocols
  initialization.py      # top-level Method/roster from resolved Method facts
  lifecycle.py           # binding lifecycle, inspect, compose and detach
  single.py / group.py   # separate operations; single_group.py preserves combined API
  open/                  # opportunity query/application/admission operations
  swarm/                 # execution profile/plan/stage operations
services/
  method_bootstrap.py       # L6 creation contract -> L5 initialization/recovery
products/
  task_commission/           # concrete product profile and business orchestration
  bbs_public_discussion/
```

- L4/L5 operations consume narrow `CollaborationReader`/`EventWriter` ports, not L6's
  ContextSpec or the richer application repository contract. Persistence exposes
  creation principal/executor identity facts for governance, preserving existing
  ID-only Method/Collet rules even for partial legacy rows. These are **creation
  identities**, not reassignment or a replacement for typed Task executor checks
  on Bot reporting. Task remains Work authority.
- Application/product entrypoints retain event-schema checks, G1 authorization,
  product contracts, pinned workflow selection and their existing error order.
  They pass only the selected machine ID/version and explicit event intent into
  L4 `prepare_event_append`. It produces a TaskUpdate and exact Task/Collet
  fences; `PreparedEventAppend.commit` uses the same repository transaction for
  generic and commission Work. Preparation does not write; Bot deadlines are
  still checked after G4 and before commit. Ordinary Messages cannot acquire a
  TaskUpdate. These internal operations do not expose an unauthenticated API.
- `TaskModule` injects the L5 lifecycle implementation and an application-owned
  `TopLevelBindingRecovery` adapter. Only that adapter resolves product catalog
  versions during recovery. Initialization is idempotent and keeps existing
  terminated bindings; missing binding recovery retains the existing in-memory
  restart limitation (no durable method history is fabricated).
- Product bootstrap resolves Method facts without interpreting product policy
  in L5. [Commission v2](products/task_commission/README.md#v2-complete-journey-and-participation-boundary)
  uses **Open only**: COLLET_PARTICIPANT admission creates an immutable L4
  ScopedParticipantBinding for the exact Collet, with no Group roster/member or
  Single selection. Its authority requires the correlated ADMITTED application,
  decision and ACTIVE Open binding. DI installs the shared G1 admission guard,
  so product and generic L4 execution cannot bypass or reuse a revoked binding.
  v1 and unrelated Open + Group products retain their structural behavior.
- L5 Open owns `OpenMethodOpsServiceProtocol.query_opportunities`: a read-only
  snapshot on an explicitly identified, ACTIVE Open binding. G1 requires an
  authenticated actor, not existing membership. Only OPEN opportunities within
  their application window and below capacity are returned; querying neither
  reserves capacity nor admits an applicant. Product discovery selects its
  authorized task/profile candidates and delegates these Method checks to L5,
  retaining designation and product-policy filtering outside L5. The existing
  signed HTTP product-discovery operation remains L4 Query; no new wire tuple
  or global L5 discovery index is implied. See the [direct invocation example](products/task_commission/README.md#calling-l5-open-directly).
- Old `services/*` operation imports are compatibility forwards, not duplicate
  implementations. The legacy lifecycle constructor additionally assembles the
  same recovery adapter for old `product_catalog=` callers. New DI uses the L5
  implementation directly. Root `method_service_api` and `service_api` reexport
  canonical command classes, preserving object identity and wire fields.

The [functional port tests](../../../../../../tests/community/core/task/context_collab/test_layer_operations.py)
exercise L4/L5 with records that have no ContextSpec or product catalog, shared
Work fences, partial legacy Messages and propagated write failures. The real
DI/SQL/product journey remains the integration evidence. No new operation family
is invented for layers that do not yet have a corresponding use case.

## Creation return contract

`ContextCollabServiceProtocol.create_collet(command, context) -> Collet` returns
exactly the persisted L4 object after initial Task/Event creation and L5 binding
initialization. The implementation lives in
[`l4_context_primitives/context_collab_service.py`](l4_context_primitives/context_collab_service.py)
and DI selects it there; the old `services/context_collab_service.py` is removed.
Library callers use `collet.collet_id`, `collet.version` and
`collet.current_event_id`, not the former creation-result wrapper. Task objective,
executor, creation facts and pinned product metadata keep their existing owners;
returning Collet does not copy them onto the aggregate root. Persistence still
returns its internal `ContextCreatedRecord` for application/product orchestration.

Both HTTP faces retain their existing `collab_context_id`, `context_version`,
`current_snapshot` and `current_event` response fields. The delivery serializer
builds this **creation receipt** from the returned Collet and accepted command,
using the canonical Method spelling and the initial Task/Event contract. It does
not re-read a potentially advanced context or attach HTTP projections to Collet.
Later state and authorized progress remain the separate `get_status` query.
Creation is not idempotent; repository and MethodStore write failures propagate.
The existing SQL/MethodStore transaction gap is not removed by this return change.

## State ownership

| Object | Authority and current implementation |
| --- | --- |
| [Collet](l4_context_primitives/collet.py) | Bounded context identity, initiator, scope, lifecycle vocabulary, event head and context revision. The six-field Python layout is local, not a claim that ACCS mandates exactly six fields. The legacy repository still seeds `ACTIVE`; the separate Collet lifecycle path is not unified by this change. |
| [Task](l4_context_primitives/task.py) | Independent L4 work-item ID, Collet/parent links, objective, completion conditions, responsible principal, designated executor, status, Task revision and event provenance. Not an A2A Task/execution attempt. |
| [ScopedParticipantBinding](l4_context_primitives/participants.py) | Immutable task-scoped identity evidence created by Open COLLET_PARTICIPANT admission; validity derives from its admission application/decision and Method lifecycle, not an independent Group membership machine. |
| [MethodBinding](l5_methods/method_binding.py) | L5 Method identity, scope and runtime facts. Neither ContextSpec nor ProductInstance substitutes for Method state. |
| [ContextSpec](l6_products/context_spec.py) | L6/composition creation input and selected profile/role references. Existing JSON/wire fields are retained for compatibility, not treated as mutable Task state. |
| [ProductInstance](l6_products/instance.py) | Pinned composition and evidence references; no second work-item state machine. |
| [CurrentSnapshot / ProgressSnapshot](g3_observe_evaluate_evolve/views.py) | Read/response projections. CurrentSnapshot derives from Task; G3 computes authorized query progress. Neither is an ordinary runtime persistence or transition input. Explicit old-row decoding is the only snapshot-authority compatibility boundary. |
| [EventRecord](l4_context_primitives/event_envelope.py) | Append-only accepted facts. New Work events record `task_ref` and `task_version_after` separately from the Collet revision. Message events omit both. |

[G4 workflow resolution](g4_contracts_consistency/workflow_resolution.py) consumes
Task, not CurrentSnapshot. Generic and product entrypoints share G4 validation
and the selected versioned machine. The repository receives a typed
`TaskUpdate(task_ref, expected_version, status)` and fences its exact Task
identity/revision plus the Collet revision in one transaction. Ordinary Messages
cannot carry a TaskUpdate. L5's top-level termination gate reads root Task state,
not the response projection.

## Persistence and compatibility

[Repository](persistence/repository.py) and [ORM models](persistence/models.py)
store three distinct records:

```text
Collet.root_task_ref ──→ Task.task_id
      │                      │
      └── Collet revision    └── independent Task revision
                   ↘        ↙
               append-only Work Event
```

- Creation atomically writes Collet, root Task and creation Event (plus pinned
  ProductInstance metadata when selected). New rows leave `current_snapshot`
  NULL. Root IDs differ from Collet IDs, even when both revisions start at 1.
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

## Query progress

Library callers query final Task state through the L4 service's `get_status`
and read accepted artifact references from `get_events(..., context=actor)`.
The latter returns L4 `EventRecord` facts in acceptance order after the same G1
visibility filtering as status queries; `task.submitted` payloads carry the
submitted `artifacts`. Both queries are read-only, do not fetch artifact contents
or change Collet/Task revisions, and do not add fields or routes to the HTTP API.
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

- [Task contract tests](../../../../../../tests/community/core/task/context_collab/test_task.py).
- [SQLite Task authority/cutover tests](../../../../../../tests/community/repository/task/context_collab/test_task_authority.py): distinct IDs/revisions, G4 uses Task despite a corrupt retired snapshot, repeated progress, read-only/trunk-independent queries, Task fences, atomic rollback, invalid storage and legacy import.
- [L6 composition contract](l6_products/README.md) and [commission product](products/task_commission/README.md) document profile selection and authenticated Bot reporting.
- [Commission journey](../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py) runs real local DI/SQLite/signed HTTP with mocked outbound delivery, not a live remote Bot.

Changing Task state/version, query counters or legacy mapping affects generic
services, G4, Method termination, product services, stored rows and existing
clients interpreting progress. The creation return contract also affects DI,
library consumers, both HTTP adapters and their test doubles; update them
together when changing it. Keep their tests and migration aligned. Source
imports of ContextSpec or snapshots must use their new owners; HTTP product
fields and the six-key creation payload remain unchanged. The in-memory
MethodStore and SQL/MethodStore/delivery transaction gap remain existing limits;
this change does not add live A2A execution or a recursive workflow engine.
