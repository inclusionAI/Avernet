# Bounded Swarm execution and Stage conversations

Owner: context_collab L5 execution semantics. The inbound contract is the
existing [`TaskCommissionServiceProtocol`](../products/task_commission/service_api.py),
re-exported by [`api/task/task_commission_service.py`](../../../../api/task/task_commission_service.py).
The [product facade](../products/task_commission/service.py) delegates to the
internal [`SwarmExecutionControl`](swarm_execution_contracts.py) capability.
[Execution operations](operations/swarm/service.py), [reads](swarm_execution_reads.py),
[revision construction](swarm_replanning.py) and
[HTTP delivery](../../../../adapters/http/task/context_collab_swarm_execution.py)
remain separate responsibilities. No independent Swarm Service API is published.
This does not change immutable ProductProfile publication or add a scheduler.

## Service API and HTTP

The HTTP base path is:

```text
/api/v1/collaboration/task/context_collab/{collet_id}/method-bindings/{binding_id}/swarm
```

| HTTP | Service method | Meaning |
| --- | --- | --- |
| `POST /step` | `step_swarm` | Plan/dispatch at most one new Stage; never drain the internal queue. |
| `GET /stages` | `get_swarm_stages` | Current graph, Stage inputs/responsibilities/outputs/status, sessions and execution history. |
| `POST /interactions` | `interact_swarm` | Human message or clarification reply to an exact Stage/session. |

These methods address an exact Collet/Swarm binding, regardless of its product
profile ID. Commission publish/detail/Work methods keep their existing product
checks; those filters must not narrow the Swarm routes to `task_commission`.
In-process consumers migrate to the task-commission Protocol and these three
method names. The former independent Swarm Service API/re-export is removed;
HTTP paths, request/result shapes and authorization semantics are unchanged.

All routes use authenticated human identity, the existing `Envelope` and domain
error mapping: stale versions/replay conflicts → 409; missing Stage/binding →
404; invalid commands/structural authorization failures → 422. Execution and
planning failures propagate; they are not translated to successful completion.
Blocking core calls run off the HTTP event loop, preserving request context.

A step request contains:

```json
{"request_id": "next-stage-001", "expected_version": 12}
```

`expected_version` fences the Collet revision, not a Stage-local counter.
`request_id` is nonempty, at most 128 characters, and unique across mutation
kinds in this binding. Same ID/command/actor replays the recorded result after
current authorization; changed shape or actor conflicts. Incomplete requests
must be explicitly retried with their original command before another control
mutation. A worker may still ask a question synchronously during a step.

A step starts created root Work through existing G1/G4, computes and applies one
planning decision and dispatches no more than one Stage. An in-flight allocation
returns its Stage without dispatching it again. Returning from a worker's
`execute` is not completion: only accepted worker evidence completes child Work
and Stage. Completion reconciliation may consume a step without a new dispatch.
Root/product acceptance remains separate.

The result includes `request_id`, `stage_ref`, `plan_ref`, `status`, `version`,
`event_ref`, `allocation_accepted`, `dispatched` and `dispatch_state`.
`dispatch_state=returned` means dispatch returned without error, not that Work
succeeded. An accepted allocation with no dispatch-return journal is
`unknown`/`RECONCILIATION_REQUIRED`; retry does not repeat a possibly delivered
external side effect. Inspect worker evidence or replan rather than inventing a
successful dispatch. `none` means this step admitted no new allocation.

## Graph and session projection

`stages` returns all current nodes and explicit control edges, including
unexecuted nodes. A configured DAG is previewed as `UNPLANNED` before the first
step; preview sessions cannot yet accept messages. Each executed/planned Stage
exposes:

- identity, independent child `task_ref`/`task_status`, name, responsibility/objective;
- declared input/output ports, actual immutable Artifact bindings and refs;
- dependency refs, configured node/Agent candidates, actual executor refs and allocations;
- G4 `state` and read `status`; `WAITING_FOR_INPUT` is a projection, not a new machine state;
- `session_ref`, chronological Stage interaction events and inherited feedback;
- `reused_from_stage_ref` when a revision reuses accepted predecessor evidence.

`plans` and `archived_stages` retain prior revisions. Pending, recipient-addressed
clarification events appear in `notifications`. The principal/exact designated
executor can inspect the task-wide graph; another allocated Agent can inspect
only its own live Stage sessions, not another Stage's conversation/history.
G1 Method authorization remains a replaceable hook. No resource access is granted
by reading Artifact references; providers still validate actual read authority.

A session is **an Event-backed Stage conversation**, identified by
`swarm:{binding_id}:{stage_id}`. It is not an Engine/ExpertChat conversation ID.
The existing Event table carries messages; there is no new session table or an
unverified claim that real Agent runtime sessions have been connected.

## Human feedback and replanning

```json
{
  "request_id": "feedback-001",
  "expected_version": 19,
  "stage_ref": "wfs_example_0",
  "session_ref": "swarm:mb_example:wfs_example_0",
  "kind": "message",
  "content": "Please account for the revised delivery constraints."
}
```

The outbound `SwarmInteractionPlanner.decide` receives the authoritative plan,
Stages, selected session, content and interaction Event ref. Its proposal is one
of `ignore`, `replan_all`, or `replan_from` with an exact current Stage ID. The
client cannot submit the decision as authority. Invalid/stale proposals do not
change execution. The conservative public default retains the message and
explicitly ignores replanning; a host installs an Agent-backed implementation
through DI. There is no keyword-based hidden control language.

Replanning creates an execution revision, not a new ProductProfile version:

- Managed workflows retain the complete pinned DAG, node contracts and Agent
  candidate lists. Replanning cannot reauthor a published DAG.
- `replan_all` gives every node a fresh Stage/child Task. `replan_from` invalidates
  the selected node and all descendants, including previously succeeded nodes.
- Only SUCCEEDED unaffected Tasks/manifests are reused. Unfinished independent
  branches also receive fresh execution identities; no in-flight side effect is
  copied into another revision as accepted evidence.
- Dynamic mode retains the current graph and re-enters its configured scheduling
  planner with fresh Stages and interaction facts; replacing graph structure is
  not part of this control API.
- The old plan is terminated when its machine permits; completed history is
  preserved. Old nonterminal executable Stages are cancelled and live allocations
  revoked. Old pending/succeeded/failed rows remain historical facts.
- Old child Tasks and artifacts are retained, not overwritten. There is no
  automatic remote cancellation or synthetic child `task.cancelled`/success.
  A late report from a revoked execution is rejected before writing child Work.
- Fresh Stages carry `context_event_refs` in their Work contract. Worker session
  reads expose that feedback; normal planning requests also include interaction
  facts. Dispatch still randomly chooses one eligible configured Agent per node.

The complete new plan and reused/fresh Stage set are validated before accepting
the decision. The accepted decision is journaled before the atomic MethodStore
revision commit. Retrying an applied-journal failure reuses the accepted decision
and revision; it does not ask the planner again. Direct candidate submission
cannot forge `execution_revision` metadata.

## Agent clarification and human reply

A trusted worker calls **`TaskCommissionServiceProtocol.interact_swarm`** with
`kind=clarification_request`, its authenticated Agent `ActorContext`, current
revision, Stage/session and nonempty question. Only the exact live allocated
Agent (type and ID), current participation, active binding and unexpired lease
are accepted. Human HTTP cannot manufacture an Agent identity or a worker
question; machine HTTP authentication/ingress remains host-owned.

The request Event contains the principal recipient and is a durable user
notification available in `stages.notifications` and the authorized Event feed.
This public implementation uses polling/Event delivery, not external IM/push.
Only one question per Stage may be pending. Completion reports, direct Stage
completion and further planning are blocked while it is pending.

The human replies using `POST /interactions`:

```json
{
  "request_id": "answer-001",
  "expected_version": 23,
  "stage_ref": "wfs_example_0",
  "session_ref": "swarm:mb_example:wfs_example_0",
  "kind": "clarification_reply",
  "clarification_ref": "evt_question_example",
  "content": "Confirmed. Use the current published requirements."
}
```

The principal/executor may confirm or supply additional information. The exact
pending question/session must match; duplicate replies with a new request ID
are rejected. A reply clears the waiting projection but does not complete Work
or invoke `execute` again. The existing worker polls its session, consumes the
reply and continues, then reports normally. Hosts must implement that cooperative
worker behavior. Lease renewal/timeout policy is not added by this API.

## Consistency, propagation and limits

No new database table is required: SQL Event journaling is reused, while plans,
Stages, allocations and per-axis/Collet reentrant coordination guards remain in
the existing in-process MethodStore. Atomic MethodStore revision CAS and SQL
Event/Task CAS are separate failure domains, not a distributed transaction.
Workers must return promptly from dispatch and must not join a separate-thread
report while the orchestration guard is held. Reports share the same guard.

There is no distributed scheduler, multi-replica exactly-once execution, durable
Method recovery, automatic external retry/cancellation, real chat-session
binding, or external notification delivery. SQL history with missing managed
Method state remains fail-closed. An outstanding accepted decision that conflicts
with an out-of-band Method mutation is rejected, not silently overwritten.

Propagation: existing task-commission Service API/re-export, Swarm router and
DI; internal L5 execution-control capability; additive Stage context refs,
planning interaction facts and reserved coordination Event kinds; MethodStore
implementations must supply the guard and atomic revision commit. HTTP clients
can opt in without changing old routes. Worker hosts must bind the evidence and
execution capabilities already required by managed workflows, plus an optional
interaction planner. The reserved coordination Events use workspace visibility;
they are L5 coordination messages, not product Task control actions or a bypass
of the product's root lifecycle/role contract. Generic append rejects them.

Verification lives in
[core conformance](../../../../../../../tests/community/core/task/context_collab/test_swarm_execution_api.py),
[HTTP conformance](../../../../../../../tests/community/endpoints/test_swarm_execution_endpoints.py),
and the existing architecture/managed-workflow suites. See the
[delivery record](../../../../../../../specs/2026-11-23-swarm-execution-interactions/delivery.md)
for actual results and remaining gaps.
