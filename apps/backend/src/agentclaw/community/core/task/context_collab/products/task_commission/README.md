# Task commission product

Owner: Backend `core/task/context_collab/products/task_commission`.
Versioned task-commission templates consume [L6 contracts](../../l6_products/README.md),
not an L6 implementation or a second Task engine. DI explicitly installs their
[definitions](profile.py). Both installed commission versions use the reusable
standard Task contract and its exact `task_commission_goal@1` G4 machine;
historical explicit-workflow definitions retain their pinned references.

| Profile | Method composition | Admission and execution |
| --- | --- | --- |
| `task_commission@1` | Open | Preserved publisher-mediated designated-assignee admission. |
| `task_commission@2` | Open + Single | Bot self-application → owner-policy scoped admission → explicit selection of the admitted participant → L4 Work. |

Existing instances are never migrated. `PublishTaskCommand.product_profile_ref`
selects an exact installed definition at creation; omission selects the installed
`task_commission@1` definition. This
service command does not add a dynamic product-management API or change the
existing public user creation DTO.

## Contracts and authority

- [Service contract](service_api.py): publish, list/detail, Bot opportunity
  Query/Apply, review of an existing L5 application, six Work actions and
  `report_execution` for inbound Bot reports.
  The API layer re-exports only `TaskCommissionServiceProtocol`.
- [Service](service.py): exact @2 selection publishes an opportunity without
  applying for the Bot. The DI adapter reviews the Bot's application and calls
  Single Select before returning a receipt/dispatch. @1 keeps its original
  publisher-mediated admission flow; no existing instance is upgraded.
- The selected [standard Task contract](../../l6_products/task_contracts.py),
  or a historical explicit workflow when pinned: executor actions require
  `selected-agent`.
  Shared [G4 validation](../../g4_contracts_consistency/product_contract.py)
  enforces it for **both product and generic L4 writes**. Submission retains
  its executor-only rule. G1 authorization, machine guards and repository CAS
  remain prerequisites, not alternative permission systems.
- The designated executor is the typed `executor_ref`/materialized L4 role,
  never the publisher, Bot owner, `agent_code` or the MethodBinding ID.
  Exact type and opaque ID must match. Historical unversioned v1 records use
  the typed executor fallback, not a fabricated RoleBinding.

Making the existing assignee `report_progress` responsibility enforceable is a
**task_commission@1 authorization bug fix**, not a topology change or automatic version
migration. Previously accepted owner-reported progress is now rejected. Existing
stored events are not rewritten. Owner review/cancellation remains governed by
G1 and machine guards; start/progress/submit require the executor. The Bot ingress
permits only those three executor actions.

## Method-specific product policy

Commission's Open publication/admission policy is declared in
`profile.composition.policies` as `OpenMethodPolicy(publication=OpenPublicationProfile(...))`.
It is L6 product configuration scoped to the required Open Method, not a universal
`ProductProfile.publication` field or a new L5 contract. The product service and
DI adapters resolve `composition.require_policy("Open").publication`; creation
and catalog installation reject duplicate, untyped or unbound Method policies.
Other Methods do not need an Open policy or a null placeholder. See the
[L6 authoring example and migration](../../l6_products/README.md#create-a-product-profile).
Moving policy into `composition.policies` is a Python configuration relocation,
not a change to the L5 Open contract. The explicit @2 product definition adds
Single selection without rewriting @1 or introducing a new HTTP/ACCS operation.

## Autonomous products without domain/workflow boilerplate

New authored definitions may omit both `domain` and `workflow`. `domain=None`
uses standard vocabulary; `workflow=None` selects the fixed `standard_task@1`
lifecycle/action contract, not an unrestricted Task or a Bot execution workflow.
The Bot still chooses its own plan/tools; the product only constrains observable
start, progress, submission, review, rejection and cancellation.

The [L6 authoring example](../../l6_products/README.md#create-a-product-profile)
shows the omitted fields. Keep the required executor role, composition,
governance and truthful compatibility declaration. Generic creation supplies
`workflow_kind=None` for such a definition; product publication derives this
value. Work uses the same G1/G4 checks, artifact requirement, owner acceptance and
Task/Collet revision fences as the explicit workflow path. Optional profiles do
not remove the installed admission prerequisite.

The default publish selector remains `task_commission@1` for compatibility.
The installed `OPEN_SINGLE_COMMISSION_PROFILE` is the explicit @2 successor;
choose it with `PublishTaskCommand.product_profile_ref` or L6 Instantiate.
It omits domain/workflow, pins `standard_task@1` and requires Open@0.2 and
Single@0.2. Do not change @1's topology or reinterpret historical records.

Independent binding IDs are persisted with the selected @2 ProductInstance.
Recovery fills missing bindings/edges after partial writes and validates requested
IDs against this exact selection; it preserves suspended/terminated bindings and
detached edges. SQL selection and MethodStore are not atomic together. Unknown
IDs never cause new projections. Previously generated experimental composite @2
snapshots are not silently migrated and fail strict reference validation; @1 and
legacy Open + Group keep their historical topology.
See [topology contracts and failure regressions](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_method_topology.py).

## ProductInstance and the Open + Single journey

The [end-to-end test](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py)
shows one commission: create only its Collet, instantiate its ProductInstance
separately, then create/start root Work only at `l4_context_operate.start`. Multi-instance reuse/isolation is outside this
journey's scope. For this product, each
independent commission has one instance, one Collet and one independent root
Task (potentially with L4 subtasks). This is not a universal protocol 1:1 rule.
Before start, each instance persists `root_task_ref=None`; after start the query
projection resolves the root from L4 without rewriting selection. It pins exact
Profile/Method/Task-contract versions, binding/role refs, governance, negotiation and creation evidence.
It does not copy runtime Open/Single objects or the Task status. Historical
instances without an explicit root ref resolve it from L4 without rewriting
storage. See the [L6 identity contract](../../l6_products/README.md#productinstance-identity-and-cardinality).

1. Product staff author `OPEN_SINGLE_COMMISSION_PROFILE` via the pure L6
   `create_product_profile` operation and explicitly install @2 through [DI installation](../../../../../di/modules/context_collab_products.py).
   “Online” means an immutable code-owned definition installed into the actual
   application catalog, not a mutable registry or a mocked registration call.
2. Business staff first create only the L4 context with
   `ColletCreateCommand(initiator_ref, title=..., description=...)`: no Space,
   objective, executor, Task or Event is supplied/created. They then call
   `ProductInstantiationServiceProtocol.instantiate(ProductInstantiateCommand(
   collet_id, profile_ref, expected_version, participant_refs=(bot,),
   resource_refs=input_resources), context)`.
   The sole initial participant defaults to the scalar assignee role source;
   multiple initial Agents require explicit executor designation. Every supplied
   Agent must satisfy the exact Profile's participant scope (`OPEN` or allowlist).
   The journey's allowlist has two Agents, including the working Bot; only that
   Bot is initially bound. Eligibility is not admission. One SQL transaction
   installs ProductInstance, Collet-scoped Participants/RoleBindings and initial
   ResourceBindings. The journey supplies versioned requirements-document and
   repository `ResourceRef`s; `collet.participants_bound.payload.resource_refs`
   persists them in that same transaction, and the L4 pure parser reconstructs
   the bindings immediately. Empty inputs still write `resource_refs: []`.
   These are instance inputs, not Profile fields, uploaded contents or Grants.
   A failed SQL write rolls back selection and all bindings together.
   The binding Event advances Collet from revision 1 to 2. Task still
   does not exist. Existing combined publishers retain their compatibility flow.
   One L6 Instantiate creates two ACTIVE MethodBindings: **Open@0.2**
   (`open_binding_id`) and **Single@0.2** (`single_binding_id`), plus the explicit
   **Single --support--> Open** MethodCompositionEdge declared in the Profile.
   No opportunity, application, Group roster or SingleSelection exists yet.
   A selected-agent RoleBinding is designation, not admission or selection.
3. The designated, authenticated Bot calls **L5 Open's `query_opportunities`**
   with only its `actor_context` to discover available opportunities in the
   current tenant/environment, then **L5 Open's `submit_application`** as itself
   for the returned opportunity's Collet/MethodBinding. The Method service owns
   both operations; the Bot is their caller, not the implementation owner. The wire action name remains `apply`.
   Product-wide discovery is also available through the existing signed **L4
   Query** wire operation: L5 Open checks active bindings, windows and capacity;
   product policy then filters exact-profile, designated, created tasks and each
   opportunity's binding/scope/policy/type. Neither query requires prior
   participation or writes application, participant binding or delivery records.
4. HTTP Apply rechecks availability and submits with the Bot as applicant and
   its **signed Bot owner** as accountable principal (not necessarily the publisher).
   Its `review_task_application` continuation reviews the existing L5 application
   without submitting again. Review validates the stored SUBMITTED application
   against the exact task/profile/opportunity, designated Bot, trusted accountable
   principal and selected-agent roles before making any admission writes.
   The publisher authorized the bounded capacity-1 rule when opening the task.
   The adapter executes `screen → admit` under that owner's `product_policy`
   actor. The in-process journey case instead shows both L5 calls explicitly
   with the authorized policy actor, without using the review wrapper.
   The opportunity uses `COLLET_PARTICIPANT`, not `GROUP_MEMBER` or
   `SINGLE_CONTRIBUTION`. Open admission creates a real immutable L4
   `ScopedParticipantBinding` and references it from the AdmissionDecision;
   its scope is exactly this task's Collet. Binding validity requires the
   admitting application to remain ADMITTED and the Open MethodBinding ACTIVE.
   Revocation retains evidence but immediately removes execution eligibility.
   These records are not Space membership, resource grants or session access.
5. After ADMITTED, an explicit L5 `Single.select` chooses the scoped participant
   created by that admission under `single_binding_id`, carrying the Open
   application/decision as evidence. Opportunities, applications, decisions and
   participant bindings remain under `open_binding_id`; admission does not move them.
   Admission alone is tested to reject Work.start. The product Apply adapter
   executes this same selection under the publisher's policy actor; Open itself
   does not call Single. Execution checks both valid scoped admission and an
   ACTIVE, in-window SingleSelection for the same participant/scope, selected by
   the publisher. Both independent MethodBindings and the declared composition
   must be ACTIVE. Dispatch and executor reports carry `single_binding_id`, not
   the opportunity's `open_binding_id`. Suspending either binding independently
   denies execution without changing its peer or the pinned ProductInstance. Release, mismatched evidence, wrong scope/participant, an
   unauthorized selector or expired selection cannot authorize execution.
   After Select, the journey calls the injected L4
   `ContextServiceProtocol.start` with `WorkStartCommand(expected_version,
   note, objective=..., completion_conditions=...)` and the Bot's authenticated
   `ActorContext`, not product `start_task`. G1/G4 validate an uncommitted root
   Task draft using actual initial bindings. One SQL transaction then creates
   and starts Work with `task.created` / `task.started` evidence: Task revision 2,
   Collet revision 4 absent intervening Events. Rejection or failed persistence
   leaves no Task or partial Event; start does not dispatch or create an Activity. The Bot then
   reports steps, metrics and its result directly to the injected generic G3
   service. Submission alone uses the existing product/A2A Work ingress. The generic
   `ContextCollabService.append_event` is not a Work API; it is a generic event
   append entrypoint.
   The shared G1 adapter enforces these prerequisites for @2 execution events
   through **both product and generic L4** writes. It also rechecks the
   authenticated actual Agent against the pinned Profile participant scope;
   claimed event actors cannot bypass the scope. Admission to task A cannot
   authorize task B; Open revocation blocks subsequent execution. Structural
   designation alone cannot start work.
6. Bot execution records plan, code, tests and PR-proposal **step details** as
   G3 `Observation` values. A registered, explicitly versioned `MetricDefinition`
   declares percent/HIGHER_IS_BETTER/LAST/Task semantics; `MetricSample` carries
   the actual 20/50/80/100 measurements. The final `Outcome` records SUCCEEDED,
   artifact/evidence references and the final sample; it is not an acceptance
   verdict. All three records leave L4 state/events/revisions unchanged. The
   [generic G3 contract](../../g3_observe_evaluate_evolve/README.md) documents
   scope, persistence and nonblocking acquisition failures; there is no
   execution-progress-specific API. After successful business execution, signed
   HTTP Work.submit submits artifact references and moves to `reviewing`.
   Only explicit publisher `accept` moves the task to `done`. Failed execution
   must not auto-submit. Existing HTTP progress remains available for older
   callers but is not used for this journey's step/metric reporting.

The product still designates one eligible Bot and uses a capacity-1 Open
opportunity. Open + Single now separates admission from execution selection;
it does not implement unrestricted discovery/admission of multiple competing
Bots, candidate ranking or executor reassignment.

The guard is deliberately bounded to task execution, not a replacement for all
G1/Governance policy: other Open + Group products retain their existing public
Message contribution rules; pure Open commission does not acquire those rules. The new Bot discovery projection reveals only designated
opportunities, not task history or other applicants. This is not full ACCS
conformance or a general automatic-admission engine.

### Pure-Open participant contract and compatibility

The [L4 scoped identity value](../../l4_context_primitives/participants.py),
[L5 admission contract](../../l5_methods/operations/contracts.py) and
[MethodStore](../../persistence/method_store.py) define this bounded local path.
`COLLET_PARTICIPANT` opportunity and admission scopes must both equal the owning
Collet ID; nested/arbitrary scopes are rejected. The immutable binding records
an Agent identity reference and requested role references. It does not mutate
the creation-time designation or introduce a second participant lifecycle.
Consumers must correlate the binding with the exact admission decision,
ADMITTED application, actor and ACTIVE Open MethodBinding; a stored binding
reference alone is not an authorization credential.

The installed `task_commission@1` definition remains pure Open and
publisher-mediated; it does not acquire Single semantics. Historical
self-application fixtures remain supported with their declared policy, while
@2 composes scoped Open participation with Single selection. Neither path
creates a Group roster/member. No in-place conversion or historic admission
rewrite is provided. MethodStore remains in-memory, with no new durable L4
participant-management API or SQL table.

## Calling L5 Open directly

The [journey case](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py)
resolves the real `OpenMethodOpsServiceProtocol` from its test injector.
Publication uses the publisher actor; query uses the Bot actor. The fixture
injects the same L5 service into `SimulatedWorkingBot`, whose
`query_opportunities_by_skill` helper uses its own actor context to discover opportunities.
Its `submit_application_by_skill` helper accepts a declared Product Profile role
name, such as `requested_role="assignee"`, and derives the canonical L4 role-binding
reference from that profile and the discovered opportunity's Collet scope, without
reading repository records. It builds `OpenApplicationCommand` from its own UUID,
owner and actor context and invokes L5 `submit_application` itself. L5 still
receives `requested_role_refs`, not bare product role names; requesting a role
does not grant it or bypass admission. The journey calls
`working_bot.query_opportunities_by_skill()` and
`working_bot.submit_application_by_skill(...)`, without supplying an applicant
or actor on the Bot's behalf. These are test-runtime wrappers, not installed
Skills. Screening and admission use the publisher's authorized policy actor:

```python
open_method = injector.get(OpenMethodOpsServiceProtocol)
published = open_method.publish_opportunity(
    OpenOpportunityCommand(
        collet_id=task_id,
        actor_context=owner_context,
        participation_type=ParticipationType.COLLET_PARTICIPANT,
        capacity=1,
        admission_policy_refs=[
            "task_commission.designated_bot_self_application/1"
        ],
    )
)
assert published.accepted and published.state == "OPEN"
# The simulated Bot owns the context built from the fixture's trusted identity.
# Production ingress must establish these identity facts from verified authentication.
bot_context = working_bot.actor_context
[opportunity] = working_bot.query_opportunities_by_skill()
submitted = working_bot.submit_application_by_skill(
    opportunity=opportunity,
    capability_evidence_refs=["evidence://calculator-coding"],
    requested_role="assignee",
)
assert submitted.state == "SUBMITTED"
policy_context = ActorContext(
    record.collet.initiator_ref, source="product_policy"
)
[policy_ref] = opportunity.admission_policy_refs
screened = open_method.screen(
    command=OpenScreenCommand(
        application_ref=submitted.object_id,
        context=policy_context,
        collect_id=task_id,
        reason=policy_ref,
    ),
)
assert screened.state == "SCREENING"
decision_id = f"dec_{uuid4().hex[:12]}"
admitted = open_method.admit(
    command=OpenAdmitCommand(
        application_ref=submitted.object_id,
        context=policy_context,
        collect_id=task_id,
        decision_id=decision_id,
        reason=policy_ref,
    ),
)
assert admitted.state == "ADMITTED"
# Admission is participation, not Single selection or permission to start Work.
open_binding_id, single_binding_id = instance.method_binding_refs
decision = store.get_decision(axes, decision_id, open_binding_id, task_id)
[participant_ref] = decision.resulting_binding_refs
single_method = injector.get(GroupMethodOpsServiceProtocol)
selected = single_method.select(
    task_id,
    single_binding_id,
    SingleSelectCommand(
        agent_participant_ref=participant_ref,
        scope_ref=task_id,
        selection_evidence_refs=[submitted.object_id, decision_id],
    ),
    policy_context,
)
assert selected.accepted and selected.state == "ACTIVE"
```

`task_id` is the product's Collet ID. `instance.method_binding_refs` pins the
independent Open and Single binding IDs in the Profile's required-Method order.
Screen and admit each accept only their command, with required `application_ref`,
trusted `context` and `collect_id` (the Collet ID). L5 resolves the application's
unique owning binding inside that Collet and the current tenant/environment;
callers no longer pass a MethodBinding ID. Admission has no `admitted_scope_ref`
input: the persisted decision and scoped participation use `collect_id`.
G1 reviewer authorization, ACTIVE/Open guards, G4 transitions and decision-key
idempotency still apply. `OpenDecisionOpCommand` remains for reject/withdraw/revoke.

Publication derives the persisted `scope_ref` from `collet_id` and records the
server's current UTC time as `opens_at`; callers supply neither field. Same-ID
replay preserves that first timestamp and current state rather than reopening
the window. Optional `closes_at` must be later than the first opening time.
`selected_agent_role_refs` are canonical references to the task's existing
selected-agent RoleBindings, built with L4 `role_binding_ref`; they do not
create participation. The case resolves them from the actual creation record.
This is an **in-process discovery call** across ACTIVE Open bindings in the
current tenant/environment, not independent JWT verification. The isolated
journey has one available opportunity; real callers may receive zero or many.
L5 availability does not establish designation or product eligibility: the
product discovery adapter still filters by pinned profile, exact Collet/binding,
Bot designation and task state. The test constructs its trusted actor context;
production delivery must establish it from verified authentication and inject
its service dependency. Cross-Agent callers still use L1/A2A.

The [L5 contract](../../l5_methods/operations/contracts.py) returns immutable
`OpenOpportunity` values from an ACTIVE Open binding. It applies OPEN state,
`opens_at <= now <= closes_at` (no upper bound if absent) and free-capacity
checks. SUBMITTED, SCREENING and ADMITTED applications occupy slots; terminal
applications do not. Missing bindings fail as not found; inactive/non-Open
bindings and unauthorized actors are rejected. Results are a read-time snapshot,
not a reservation, product eligibility decision, automatic application or
participation grant. Submit/admit still perform their own checks.

The case shows the real L5 calls in sequence: PublishOpportunity → Query → SubmitApplication →
Screen → Admit → Single Select. Submission returns SUBMITTED, not an admission receipt; it does
not implicitly screen, admit or start Work. SCREENING still has no participant
binding or decision and cannot authorize Work. Admission creates the scoped
participant binding and decision; Single then explicitly selects that scoped
participant with correlated evidence. Only after selection does the case invoke
`l4_context_operate.start(collet_id, WorkStartCommand(expected_version, note,
objective=OBJECTIVE, completion_conditions=(...)), bot_context)`.
This is the L4 Work.start API, not product `start_task`, a Bot method or a caller
constructing a generic event. Its actor/event fields are chosen by L4 and its
receipt reports the committed Task status and Collet revision. Open itself does
not start Work. The case declares `BotSelfApplicationAdmission` with
`COLLET_PARTICIPANT` locally, matching its Bot self-application sequence and
activating G1's admission prerequisite; it does not change the global publisher-
mediated template. A pre-admission start is rejected without SQL writes.
Screening/admission use the publisher-authorized `product_policy` actor,
not the applicant Bot. This case makes those lifecycle steps visible; it does
not exercise the product review wrapper for this admission.

The production `review_task_application` continuation remains on both
`TaskCommissionServiceProtocol` and `TaskMethodOpsPort`; HTTP Apply uses it
before dispatch. Separate signed HTTP Apply cases exercise the real adapter,
and review regressions cover rejected mismatches/replays. The v1 adapter rejects
this continuation. There is no new HTTP operation or automatic submission hook.

Product review cannot repeat opportunity discovery: the stored SUBMITTED
application already occupies the capacity-1 slot. It validates that application's facts;
L5 checked window/capacity at submission. Mismatches and repeat review are
rejected without a second application, admission or Work start. Review/write or
execution failures still propagate, without automatic retry or compensation.

The journey checks that admission alone cannot authorize Work. The
[Open + Single regressions](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_open_single.py)
exercise signed product-wide HTTP Query/Apply, real selection before dispatch,
released/invalid selection denial and immutable ProductInstance references. Product filtering
continues to hide unrelated Bots' tasks; L5 itself does not import product
profiles, SQL discovery or designation rules. `SimulatedWorkingBot.query_opportunities_by_skill`
and `submit_application_by_skill` are only test-runtime callers of the injected L5
capability, not new domain operations, admission policies or HTTP endpoints.
The case checks the persisted SUBMITTED application's applicant and submitting
actor against the Bot UUID, and its accountable principal against the Bot owner.
[Bot-runtime regressions](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_bot_runtime.py)
check the discovery caller identity, unchanged empty/available results, declared
role-to-binding conversion in the opportunity scope, rejection of undeclared
roles and propagated query/submission failures. Separate regression cases send
the actual signed HTTP Apply envelope to preserve ingress coverage.

## Bot ingress and authentication

```text
Bot HTTP tool / A2A client
  → POST /openapi/v1/task-commission/a2a (Gateway)
  → Bot token registry authentication + signed X-Avernet-Principal
  → POST /a2a/task-commission (Backend, explicit Gateway path rewrite)
  → verified Bot tenant + ActorContext(bot, exact bot_uuid, source=a2a)
  → Query/Apply or report_execution → shared G1/G4 and L5 admission as applicable
  → A2A Message containing AccsOperationResult
```

The Bot supplies its **registered Bot credential** via `X-Avernet-Bot-Token`
(the gateway default dedicated header), not a self-created principal JWT.
The gateway resolves the token and signs its authoritative Bot UUID, owner and
tenant. Backend independently verifies signature, issuer, audience and expiry,
requires exactly one Bot principal and rejects extra user/app identities.
The existing boot-time shared signing-key configuration must be provisioned on
both services; missing key fails closed. Neither body identity nor SSO nor
`X-Bot-Id`/`X-Staff-Id` provides a fallback. Ordinary public user/app APIs have
**not** been opened to Bot-only callers.

Publish `assignee_agent_id` using the **exact gateway registry `bot_uuid`**,
including any owner qualifier, for example `bot_writer_001:u_bot_owner`.
A bare engine agent code or UUID with the suffix removed does not match and is
rejected; no fuzzy lookup or silent identity migration is performed.
The request's `caller` must equal the signed Bot; its
`accountable_principal_ref` must equal that Bot's signed owner. That owner can
differ from the task publisher. Unlike outbound publisher-attributed dispatch,
this inbound operation is the Bot acting as itself, not delegated publisher
impersonation. A binding/profile reference is correlation, never a credential.

## Wire contract and example

The [HTTP adapter](../../../../../adapters/http/task/commission_a2a.py) accepts a
bounded **A2A 0.3.0 JSON-RPC `message/send`** request, with exactly one DataPart
containing an ACCS 0.2 operation. `role=user` means the sending A2A client, not
necessarily a human. No streaming, notifications, remote A2A Task/session
continuation, metadata/configuration options or arbitrary A2A methods are
supported. This is not the engine adapter's A2A 1.0 wire format.

The installed profile supports these bounded operations on the same route (common
signed identity, protocol-version and unsupported-extension checks still apply):

| ACCS tuple: layer / family / action / mode | Target and context | Result data |
| --- | --- | --- |
| L4 / query / opportunities / QUERY | ProductProfile `task_commission`, version `1`; no Collet or Method context | Minimal available opportunity projections |
| L5 / open / apply / COMMAND | OpenOpportunity target plus matching Collet and MethodBinding context; profile `task_commission@1` | Admission receipt with scope and resulting participant binding refs |

Both require `{}` payload and null `expected_revision`. The first response
DataPart is always `AccsOperationResult`; successful Query/Apply adds a second
DataPart containing `opportunities` / `admission`. Work retains its existing
single-part response and mandatory revision fence. Query/Open deadlines are
checked at ingress; Work additionally rechecks before SQL commit. None provides
a durable idempotency/replay contract.

| ACCS Work action | Product Work action | Payload | Caller |
| --- | --- | --- | --- |
| `start` | `start_task` | optional `note` | designated Bot |
| `progress` | `report_progress` | optional `note`, integer `percent` 0–100, `progress`, `error_msg`, `reason`, `task_id` | designated Bot |
| `submit` | `submit_task` | optional `note`, nonblank `artifacts` references; machine requires evidence | designated Bot |

Progress details are additive: old `percent`/`note` reports still work. Omitted
(or null) detail fields are not written to the event; an explicit empty string
is retained, for example `error_msg=""` when no error occurred.

| Progress field | Meaning |
| --- | --- |
| `progress` | String describing the current execution progress, separate from numerical `percent` |
| `error_msg` | String describing an execution error; reporting it does not automatically fail, complete or submit the Task |
| `reason` | String containing key evidence, such as a generated artifact reference or test result |
| `task_id` | Actual L4 root `Task.task_id`, **not** the Collet ID or a remote A2A task ID; if supplied, it must match the targeted Collet's Task |

These four fields are accepted only by `progress`. The three descriptive fields
are bounded to 16,000 characters each at HTTP ingress; `task_id` is bounded to
1–256 characters. The shared G4 product contract also checks string types and
Task identity for in-process/product and generic L4 appends. The report target
and concurrency fence remain the Collet and its `expected_revision`; the new
payload Task ID is correlation, not authorization or a routing override. Details
are persisted in `task.progress.updated` event payloads and can be read through
L4 `get_events`; they do not add database columns or change lifecycle rules.

`accept`, `reject`, `cancel` and arbitrary payload fields are refused on this
endpoint. A progress report is an L4 Task control event, not a Message side
channel. No artifact file upload is performed by this route. Work accepts
canonical
positive product-version strings, not just `"1"`; the service still requires the
exact persisted ProductInstance version and MethodBinding. This is not dynamic
installation or permission to switch a running task to another product version.

Example progress request (fake identities; replace all task/context references,
revision, request IDs and timestamp with the actual current values):

```json
{
  "jsonrpc": "2.0",
  "id": "rpc-progress-1",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "role": "user",
      "messageId": "msg-progress-1",
      "parts": [{
        "kind": "data",
        "data": {
          "operation_id": "op-progress-1",
          "layer": "L4",
          "operation_family": "work",
          "action": "progress",
          "mode": "COMMAND",
          "target_ref": {
            "object_type": "Collet", "object_id": "collet_demo",
            "version": null, "issuer": "avernet"
          },
          "expected_revision": 3,
          "input_refs": [],
          "payload": {
            "percent": 50,
            "note": "初稿完成",
            "progress": "已完成方案和编码，正在运行测试",
            "error_msg": "",
            "reason": "artifact://draft 已生成",
            "task_id": "task_demo"
          },
          "context": {
            "request_id": "req-progress-1",
            "idempotency_key": null,
            "caller": {
              "identity_type": "AGENT",
              "identity_id": "bot_writer_001:u_bot_owner", "issuer": "avernet"
            },
            "accountable_principal_ref": {
              "identity_type": "PRINCIPAL", "identity_id": "u_bot_owner",
              "issuer": "avernet"
            },
            "delegation_chain_refs": [],
            "space_refs": [],
            "collet_ref": {"context_type": "COLLET", "context_id": "collet_demo"},
            "method_binding_ref": {
              "object_type": "MethodBinding", "object_id": "mb_top_collet_demo",
              "version": null, "issuer": "avernet"
            },
            "product_profile_ref": {
              "object_type": "ProductProfile", "object_id": "task_commission",
              "version": "1", "issuer": "avernet"
            },
            "trace_id": "trace-demo",
            "correlation_id": null,
            "causation_id": null,
            "requested_at": "2026-11-13T10:00:00+08:00",
            "deadline": null,
            "protocol_versions": {"accs": "0.2", "a2a": "0.3.0"},
            "extensions": {}
          }
        }
      }]
    }
  }
}
```

The initial expected revision comes from dispatch `target_ref.version`.
After each successful report, use the new Collet revision from
`result.parts[0].data.result_refs[0].version` for the next report.
In the inbound COMMAND, `target_ref.version` stays null; concurrency is expressed
by the mandatory integer `expected_revision`. The service checks the task's
pinned product profile and canonical top-level MethodBinding reference; it does
not treat that reference alone as a credential or lease. The installed profile
also verifies active scoped Open admission before execution.

## Errors and delivery limits

- Missing/invalid principal: HTTP 401. Oversized body: HTTP 413 (64 KiB limit).
- Invalid JSON/RPC/method/params: JSON-RPC error. Domain failures: HTTP 200 A2A
  Message with `AccsOperationResult.status=REJECTED`, never transport success
  interpreted as a successful operation.
- Stale revision: CONFLICT with **no second event**. Terminal/out-of-order
  events, mismatched identities/context/profile and expired deadlines fail
  before persistence. Unexpected storage errors produce FAILED/INTERNAL,
  without disclosing exception details or claiming a successful write.
- No durable replay receipt or idempotency-key contract exists here. Replaying
  after an uncertain response can conflict; **do not increment and blindly retry**.
  Reconcile authoritative state first. Opportunity discovery is not a full
  task-state/revision reconciliation endpoint; a refreshed authorized handoff
  is still needed after an execution conflict. Discovery does not solve that gap.
- [Outbound dispatch](dispatch.py) still uses local `MockA2aDelivery`; this change
  makes inbound HTTP reachable but does not install a live outbound transport,
  automatic Bot runtime reporting tool or session-injection capability.
- MethodStore remains in-memory; publication crosses SQL/MethodStore/dispatch
  without a distributed transaction. Open screen/admit or dispatch failure may
  leave a submitted/admitted intermediate state; the error propagates, with no
  automatic retry/compensation or restart durability. SQL uses signed-tenant
  scope, while current L5 `current_axes()` retains single-tenant MethodStore
  semantics; this is not proof of full L5 multitenancy. Artifact URIs remain
  references rather than uploaded files.

## Verification and change impact

[Pure-Open participant tests](../../../../../../../../tests/community/core/task/context_collab/test_open_collet_participation.py)
cover scoped binding creation/replay, scope rejection, storage isolation and
write failures. [L5 query contract tests](../../../../../../../../tests/community/core/task/context_collab/test_open_query.py)
cover nonmutation, authentication, binding isolation/lifecycle, opportunity
states, application windows and per-opportunity capacity. Product consumer
cases also assert actual delegation to L5 while retaining product-policy filters.

[Ingress tests](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_a2a_ingress.py)
exercise real HTTP, real signature verification, Task DI, SQLite and failed
writes. They reject owner/other-Bot/typed-identity/generic-L4 bypass, forged
claims, cross-tenant input, stale replay and invalid state.
[Endpoint framework cases](../../../../../../../../tests/community/endpoints/test_task_commission_a2a_endpoints.py)
also boot the real application/service graph; the
[product journey](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py)
deploys `task_commission@2` (Open + Single, omitting domain/workflow) through
the real catalog seam. Its main case creates one Collet, separately instantiates
one ProductInstance with initial participant/role and input resource bindings,
then creates/starts root Task at L4 start after admission/selection. It checks
that ResourceBindings exist before any Task, and that the original input
references survive unchanged in the Event stream through acceptance. The product
service handles owner commands.
The authored Profile allowlists two Agents, including the working Bot; only that
Bot is initially bound. On the same instance, real L4 Work.start/G1 rejects an
out-of-scope Agent and an eligible but unbound candidate without writes, then
allows the admitted/selected working Bot to start through L4 Work, record
G3 telemetry and submit through signed HTTP.
It directly invokes L5 Open Query/SubmitApplication as the Bot and Screen/Admit
as the policy actor, then explicitly calls Single Select before Work. Separate
regression cases cover the product review/dispatch continuation; this journey
does not test multi-instance reuse or isolation.
The simulated Bot directly calls the injected L5 Open and G3 services with its
own trusted ActorContext, then submits through signed A2A HTTP; regressions also
exercise signed HTTP Query/Apply and compatibility progress. Review regressions reject mismatched task, binding,
opportunity, profile, application, caller, accountable principal, requested roles
and repeated review without additional writes/delivery. The test checks Query nonmutation, delayed dispatch,
applicant/accountable identities and a real task-scoped Participant binding,
with no Group roster or members. It
writes plan/code/test/PR-proposal files, executes three local code checks,
records step Observations, metric samples and an Outcome before auto-submitting
only after successful return; no telemetry acquisition advances Work. Pure-Open
regressions cover task isolation, revocation and identity mismatches across the
product/generic execution boundary, including a partially failed admission where
binding/decision evidence exists but the application is not ADMITTED. The L5
participant suite checks scope,
idempotency, storage isolation and propagated write failures. Open handles participation/admission;
result submission is L4 Work. PR creation is a local proposal, not a remote PR,
live Bot/LLM, gateway or artifact-upload test.

[Optional-profile runtime tests](../../../../../../../../tests/community/core/task/context_collab/test_optional_product_profiles.py)
add both-mode no-write rejection, exact machine version, Task/Collet fences,
metadata corruption and recovery checks. The successful local fixtures explicitly
declare L2–L5 compatibility only. Source L1 requirements remain unchanged and
are rejected by the local catalog, which does not advertise L1 support; neither
mock delivery nor signed local HTTP establishes live L1 interoperability.

The journey fixture temporarily wraps selected real calls to print
`[commission-flow][module/directory]` entries, returns and resulting statuses.
It covers L6 profile preparation, pure L4 creation, explicit L6 instantiation,
L5 query/admission, mock outbound delivery,
authenticated HTTP ingress, G1/G4 checks and atomic Collet/Task/Event writes.
These wrappers preserve real execution and are removed after each test; they
add no production stdout and do not dump credentials or request bodies.

Run from `apps/backend/`:

```sh
# Follow the successful user journey live (-s disables pytest stdout capture).
DEPLOY_PROFILE=test .venv/bin/python -m pytest -s -q \
  tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py::test_end_to_end_commission_flow

DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/core/task/context_collab/products/test_task_commission_a2a_ingress.py \
  tests/community/core/task/context_collab/test_l6_runtime_contract.py -q
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/endpoints/test_endpoint_runner.py -k 'a2a/task-commission' -q
```

Changing the wire, opaque Bot identity, version fences or role rule affects Bot
clients and generic L4 writers; keep DTOs, gateway routing/security, product
contract and both entrypoint tests aligned. Verification here is local HTTP/SQL
and gateway routing/auth policy evidence, not deployed multi-service/live-Bot
interoperability.

## Task authority and compatibility

The product's public `collet_id` / command `collab_context_id` still identifies
its bounded collaboration context. The repository links that Collet to an
independent **root L4 Task** with a different ID, its own revision, completion
conditions, designated executor and event provenance. G4 and the product's
Work pipeline use Task, not CurrentSnapshot or MethodBinding state.

The body Message advances only the Collet revision; Bot Work start, compatibility
HTTP progress, submit and publisher acceptance also advance the Task revision.
G3 telemetry advances neither revision. Bot requests continue
to carry the public Collet `expected_revision`; the product resolves and fences
the current Task revision internally in the same SQL transaction. Public wire
fields and `task_commission@1` remain unchanged. A 100-percent progress report
is not acceptance: submission yields `reviewing`; only publisher acceptance
yields `done`. G3 counts one completed Task, not the number of progress events.

See the [collaboration boundary](../../README.md) for projection semantics,
read-only legacy decoding and the required [Task storage migration](../../sql/2026_11_13_context_collab_task.sql).
Do not deploy new runtime code against the old schema or mix it with old
snapshot writers. The Task storage cutover itself does not change v1 Open admission topology or
turn result submission into an L5 Open operation; self-application admission
is the explicit `task_commission@1` behavior described above. Outbound delivery and MethodStore limits
listed above still apply.

## Layer-owned execution path

Product actions resolve the pinned standard Task contract or explicit
WorkflowProfile, event mapping, role/sequence contract and Bot report context in
this module. Both this product and generic
append then call L4 `operations/work.py`: preparation applies G4 and produces the
exact Task fence; commit atomically persists through the repository. Report
deadlines remain between preparation and commit. L5 Open operations now live in
`l5_methods/operations/open/`; Open still handles admission, not result submission.
The journey's local trace wraps the actual L4 preparation/commit and G4 resolver,
so console directory labels follow the moved implementations rather than a
hardcoded execution narrative. Existing Work actions/reports remain unchanged; the installed profile selects
the self-application admission topology explicitly. See the [operation map](../../README.md#operation-ownership).
