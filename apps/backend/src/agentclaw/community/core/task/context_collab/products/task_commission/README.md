# Task commission product

Owner: Backend `core/task/context_collab/products/task_commission`.
`task_commission@1` composes **Open**, a designated Bot executor and the
`task_commission_goal@1` machine. This is a business template consuming
[L6 contracts](../../l6_products/README.md), not an L6 implementation or a second
Task engine. DI installs its [definition](profile.py) explicitly.

## Contracts and authority

- [Service contract](service_api.py): publish, list/detail, six Work actions and
  `report_execution(TaskExecutionReportCommand, ActorContext)` for inbound Bot
  reports. The API layer re-exports only `TaskCommissionServiceProtocol`.
- [Service](service.py): publisher-mediated Open admission must reach ADMITTED
  before body publication and dispatch. It does not impersonate a Bot applicant.
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

## Bot ingress and authentication

```text
Bot HTTP tool / A2A client
  → POST /openapi/v1/task-commission/a2a (Gateway)
  → Bot token registry authentication + signed X-Avernet-Principal
  → POST /a2a/task-commission (Backend, explicit Gateway path rewrite)
  → verified Bot tenant + ActorContext(bot, exact bot_uuid, source=a2a)
  → report_execution → G1 → shared G4 → machine guards → repository CAS
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

| ACCS action | Product Work action | Payload | Caller |
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
not treat that reference as an active L5 lease.

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
  Reconcile authoritative state first. There is currently no Bot-authenticated
  read/reconciliation endpoint; a refreshed authorized handoff is needed after
  a conflict. This route does not widen query permissions to solve that gap.
- [Outbound dispatch](dispatch.py) still uses local `MockA2aDelivery`; this change
  makes inbound HTTP reachable but does not install a live outbound transport,
  automatic Bot runtime reporting tool or session-injection capability.
- MethodStore remains in-memory; publication crosses SQL/MethodStore/dispatch
  without a distributed transaction. No restart durability or automatic saga
  is implied. Artifact URIs remain references rather than uploaded files.

## Verification and change impact

[Ingress tests](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_a2a_ingress.py)
exercise real HTTP, real signature verification, Task DI, SQLite and failed
writes. They reject owner/other-Bot/typed-identity/generic-L4 bypass, forged
claims, cross-tenant input, stale replay and invalid state.
[Endpoint framework cases](../../../../../../../../tests/community/endpoints/test_task_commission_a2a_endpoints.py)
also boot the real application/service graph; the
[product journey](../../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py)
uses the product service for owner actions and a simulated working Bot through
signed A2A HTTP for start/progress/submit. The Bot reports during local file
generation and auto-submits only after successful execution; a failure case
checks that no result is submitted. Open handles participation/admission; result
submission is L4 Work. This is not a live Bot, gateway or artifact-upload test.

The journey fixture temporarily wraps selected real calls to print
`[commission-flow][module/directory]` entries, returns and resulting statuses.
It covers L6 profile preparation, L5 admission, mock outbound delivery,
authenticated HTTP ingress, G1/G4 checks and repository snapshot writes.
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
