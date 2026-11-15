# Task commission product

Owner: Backend `core/task/context_collab/products/task_commission`.
Versioned task-commission templates consume [L6 contracts](../../l6_products/README.md),
not an L6 implementation or a second Task engine. DI explicitly installs their
[definitions](profile.py); both use the `task_commission_goal@1` Work machine.

| Profile | Method composition | Admission and dispatch |
| --- | --- | --- |
| `task_commission@1` | Open | Publisher-mediated SINGLE_CONTRIBUTION during publication; no ongoing participant binding. |
| `task_commission@2` | Open | Designated Bot queries/applies itself; owner-authorized automatic review creates a task-scoped Participant binding before dispatch. |

Existing instances are never migrated. `PublishTaskCommand.product_profile_ref`
selects an exact installed definition at creation; omission preserves v1. This
service command does not add a dynamic product-management API or change the
existing public user creation DTO.

## Contracts and authority

- [Service contract](service_api.py): publish, list/detail, Bot opportunity
  Query/Apply, review of an existing L5 application, six Work actions and
  `report_execution` for inbound Bot reports.
  The API layer re-exports only `TaskCommissionServiceProtocol`.
- [Service](service.py): v1 preserves publisher-mediated admission before body
  publication/dispatch. v2 publishes an opportunity without applying for the Bot;
  dispatch waits for the Bot's own application to reach ADMITTED.
- [Workflow definition](profile.py): progress requires `selected-agent`.
  Shared [G4 validation](../../g4_contracts_consistency/product_contract.py)
  enforces it for **both product and generic L4 writes**. Submission retains
  its executor-only rule. G1 authorization, machine guards and repository CAS
  remain prerequisites, not alternative permission systems.
- The designated executor is the typed `executor_ref`/materialized L4 role,
  never the publisher, Bot owner, `agent_code` or the MethodBinding ID.
  Exact type and opaque ID must match. Historical unversioned v1 records use
  the typed executor fallback, not a fabricated RoleBinding.

Making the existing assignee `report_progress` responsibility enforceable is a
**v1 authorization bug fix**, not a topology change or automatic version
migration. Previously accepted owner-reported progress is now rejected. Existing
stored events are not rewritten. Internal owner-controlled start/review/cancel
behavior is unchanged; the Bot ingress permits only executor start/progress/submit.

## v2 complete journey and participation boundary

1. Product staff author `build_self_service_commission_profile()` and deploy it
   through [DI installation](../../../../../di/modules/context_collab_products.py).
   “Online” means an immutable code-owned definition installed into the actual
   application catalog, not a mutable registry or a mocked registration call.
2. Business staff create a task with that exact profile and designate the Bot.
   SQL stores the goal/body/executor; L5 creates only an **Open** MethodBinding,
   with no Group roster, Group member or Single selection. A selected-agent
   RoleBinding is designation, not admission.
3. The designated, authenticated Bot calls **L5 Open's `query_opportunities`**
   for a known Collet/MethodBinding, then **L5 Open's `submit_application`** as
   itself. The Method service owns both operations; the Bot is their caller,
   not the implementation owner. The wire action name remains `apply`.
   Product-wide discovery is also available through the existing signed **L4
   Query** wire operation: product policy selects exact-profile, designated,
   created tasks, delegates active-window/capacity checks to L5 Open and filters
   opportunity scope/policy/type. Neither query requires prior participation
   or writes application, participant binding or delivery records.
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
5. After ADMITTED, dispatch provides task details and the starting revision.
   Bot start/progress/submit use L4 Work. The shared G1 adapter requires active
   scoped admission for these v2 events through **both product and generic L4**
   writes. Admission to task A cannot authorize task B; Open revocation blocks
   subsequent execution. Structural designation alone cannot start work.
6. Bot progress records plan, code, tests and PR-proposal steps. Successful
   execution submits artifact references and moves to `reviewing`; even 100%
   progress is not completion or acceptance. Only explicit publisher `accept`
   moves the task to `done`. Failed execution must not auto-submit.

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

This corrects the v2 definition's accidental Group composition in the current
code-owned catalog; v1 and unrelated Open + Group products are unchanged. It is
not an automatic migration of any previously stored v2 instance. Previously
created v2 `Open + Group` contexts are incompatible with the corrected profile;
no in-place conversion or historic admission rewrite is provided. Recreate test
instances rather than relabeling their Group member records. MethodStore remains
in-memory, with no new durable L4 participant-management API or SQL table.

## Calling L5 Open directly

The [journey case](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py)
resolves the real `OpenMethodOpsServiceProtocol` from its test injector. Query
and application use the Bot actor; screening and admission use the publisher's
authorized policy actor:

```python
open_method = injector.get(OpenMethodOpsServiceProtocol)
[opportunity] = open_method.query_opportunities(
    collab_context_id=task_id,
    method_binding_id=binding_id,
    context=bot_context,
)
submitted = open_method.submit_application(
    collab_context_id=task_id,
    method_binding_id=opportunity.method_binding_id,
    opportunity_ref=opportunity.opportunity_id,
    command=OpenApplicationCommand(
        applicant_agent_ref=working_bot.bot_id,
        accountable_principal_ref=working_bot.owner_id,
        requested_role_refs=selected_agent_role_refs,
    ),
    context=bot_context,
)
assert submitted.state == "SUBMITTED"
policy_context = ActorContext(
    record.context_spec.principal_ref, source="product_policy"
)
[policy_ref] = opportunity.admission_policy_refs
screened = open_method.screen(
    collab_context_id=task_id,
    method_binding_id=binding_id,
    application_ref=submitted.object_id,
    command=OpenDecisionOpCommand(reason=policy_ref),
    context=policy_context,
)
assert screened.state == "SCREENING"
decision_id = f"dec_{uuid4().hex[:12]}"
admitted = open_method.admit(
    collab_context_id=task_id,
    method_binding_id=binding_id,
    application_ref=submitted.object_id,
    command=OpenAdmitCommand(
        admitted_scope_ref=task_id,
        decision_id=decision_id,
        reason=policy_ref,
    ),
    context=policy_context,
)
assert admitted.state == "ADMITTED"
```

`task_id` is the product's Collet ID; `binding_id` is its actual L5 MethodBinding.
`selected_agent_role_refs` are canonical references to the task's existing
selected-agent RoleBindings, built with L4 `role_binding_ref`; they do not
create participation. The case resolves them from the actual creation record.
This is a **known-binding, in-process call**, not product-wide/global discovery
or independent JWT verification. The test constructs its trusted actor context;
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

The case shows the real L5 calls in sequence: Query → SubmitApplication →
Screen → Admit. Submission returns SUBMITTED, not an admission receipt; it does
not implicitly screen, admit or dispatch. SCREENING still has no participant
binding or decision and cannot authorize Work. Admission creates the scoped
participant binding and decision; the case then explicitly dispatches through
`TaskDispatchPort` after checking admission. Open itself does not dispatch or
start Work. Screening/admission use the publisher-authorized `product_policy`
actor, not the applicant Bot. This case makes those lifecycle steps visible;
it does not exercise the product review wrapper for this admission.

The production `review_task_application` continuation remains on both
`TaskCommissionServiceProtocol` and `TaskMethodOpsPort`; HTTP Apply uses it
before dispatch. Separate signed HTTP Apply cases exercise the real adapter,
and review regressions cover rejected mismatches/replays. The v1 adapter rejects
this continuation. There is no new HTTP operation or automatic submission hook.

Product review cannot repeat opportunity discovery: the stored SUBMITTED
application already occupies the capacity-1 slot. It validates that application's facts;
L5 checked window/capacity at submission. Mismatches and repeat review are
rejected without a second application, admission or dispatch. Review/write or
dispatch failures still propagate, without automatic retry or compensation.

The case checks that repeated reads do not mutate participation, neither
submission nor screening can authorize Work, and admission consumes capacity.
It also exercises signed product-wide HTTP Query
and checks that its minimal projection matches the L5 result. Product filtering
continues to hide unrelated Bots' tasks; L5 itself does not import product
profiles, SQL discovery or designation rules. `SimulatedWorkingBot` has neither
a built-in `query_opportunities` method nor an `apply` method. Separate regression
cases send the actual signed HTTP Apply envelope to preserve ingress coverage.

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

v2 additionally supports these bounded operations on the same route (common
signed identity, protocol-version and unsupported-extension checks still apply):

| ACCS tuple: layer / family / action / mode | Target and context | Result data |
| --- | --- | --- |
| L4 / query / opportunities / QUERY | ProductProfile `task_commission`, version `2`; no Collet or Method context | Minimal available opportunity projections |
| L5 / open / apply / COMMAND | OpenOpportunity target plus matching Collet and MethodBinding context; profile `task_commission@2` | Admission receipt with scope and resulting participant binding refs |

Both require `{}` payload and null `expected_revision`. The first response
DataPart is always `AccsOperationResult`; successful Query/Apply adds a second
DataPart containing `opportunities` / `admission`. Work retains its existing
single-part response and mandatory revision fence. Query/Open deadlines are
checked at ingress; Work additionally rechecks before SQL commit. None provides
a durable idempotency/replay contract.

| ACCS Work action | Product Work action | Payload | Caller |
| --- | --- | --- | --- |
| `start` | `start_task` | optional `note` | designated Bot |
| `progress` | `report_progress` | optional `note`, integer `percent` 0–100 | designated Bot |
| `submit` | `submit_task` | optional `note`, nonblank `artifacts` references; machine requires evidence | designated Bot |

`accept`, `reject`, `cancel` and arbitrary payload fields are refused on this
endpoint. A progress report is an L4 Task control event, not a Message side
channel. No artifact file upload is performed by this route.

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
          "payload": {"percent": 50, "note": "初稿完成"},
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
not treat that reference alone as a credential or lease. v2 additionally
verifies active scoped Open admission before execution.

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
  Reconcile authoritative state first. v2 opportunity discovery is not a full
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
deploys an authored v2 profile through the real catalog seam and uses the product
service for owner commands. The case directly invokes L5 Open query and application
submission under a trusted Bot actor context, then explicitly continues through
the real product review/dispatch path. The simulated Bot exercises product
discovery and execution reporting through signed A2A HTTP; regression cases also
exercise signed HTTP Apply. Review regressions reject mismatched task, binding,
opportunity, profile, application, caller, accountable principal, requested roles
and repeated review without additional writes/delivery. The test checks Query nonmutation, delayed dispatch,
applicant/accountable identities and a real task-scoped Participant binding,
with no Group roster or members. It
writes plan/code/test/PR-proposal files, executes three local code checks,
reports each step and auto-submits only after successful return. Pure-Open
regressions cover task isolation, revocation and identity mismatches across the
product/generic execution boundary, including a partially failed admission where
binding/decision evidence exists but the application is not ADMITTED. The L5
participant suite checks scope,
idempotency, storage isolation and propagated write failures. Open handles participation/admission;
result submission is L4 Work. PR creation is a local proposal, not a remote PR,
live Bot/LLM, gateway or artifact-upload test.

The journey fixture temporarily wraps selected real calls to print
`[commission-flow][module/directory]` entries, returns and resulting statuses.
It covers L6 profile preparation, L5 query/admission, mock outbound delivery,
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

The body Message advances only the Collet revision; Bot start/progress/submit
and publisher acceptance also advance the Task revision. Bot requests continue
to carry the public Collet `expected_revision`; the product resolves and fences
the current Task revision internally in the same SQL transaction. Public wire
fields and `task_commission@1` remain unchanged. A 100-percent progress report
is not acceptance: submission yields `reviewing`; only publisher acceptance
yields `done`. G3 counts one completed Task, not the number of progress events.

See the [collaboration boundary](../../README.md) for projection semantics,
read-only legacy decoding and the required [Task storage migration](../../sql/2026_11_13_context_collab_task.sql).
Do not deploy new runtime code against the old schema or mix it with old
snapshot writers. The Task storage cutover itself does not change v1 Open admission topology or
turn result submission into an L5 Open operation; v2 admission is the explicit
new profile described above. Outbound delivery and MethodStore limits
listed above still apply.

## Layer-owned execution path

Product actions resolve the pinned WorkflowProfile, event mapping, role/sequence
contract and Bot report context in this module. Both this product and generic
append then call L4 `operations/work.py`: preparation applies G4 and produces the
exact Task fence; commit atomically persists through the repository. Report
deadlines remain between preparation and commit. L5 Open operations now live in
`l5_methods/operations/open/`; Open still handles admission, not result submission.
The journey's local trace wraps the actual L4 preparation/commit and G4 resolver,
so console directory labels follow the moved implementations rather than a
hardcoded execution narrative. Existing Work actions/reports and v1 profile semantics remain unchanged;
v2 selects the new admission topology explicitly. See the [operation map](../../README.md#operation-ownership).
