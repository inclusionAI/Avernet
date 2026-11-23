# Swarm Stage execution and human interactions

## Record

- Owner: Backend context_collab L5; baseline `2d4c15db`.
- Status: implemented and verified locally; see [delivery](delivery.md). Not a deployment claim.
- Authority: [L5 contracts](../../src/agentclaw/community/core/task/context_collab/l5_methods/README.md), [architecture](../../../../docs/arch/arch.rules.md).
- No applicable existing ADR changes; immutable ProductProfile publication remains unchanged.

## Scope and decisions

Expose step, stages and interaction resource families through a transport-free
Service API and thin authenticated HTTP delivery. A step plans/dispatches at
most ONE Stage, including when a worker reports synchronously. In-flight workers
return without redispatch. Completion reconciliation is allowed without a new
Stage. Idempotency keys and Collet revisions are required for mutations.

A Stage conversation is an Event-backed logical session, not an invented binding
to an Engine/ExpertChat session. Messages carry exact Stage/session references.
Agent clarification requests are accepted only from the live allocation's actual
Agent; root principal/executor can reply. Pending requests block completion and
further planning. Replies are authoritative input in the Stage session event
feed; workers poll the session via the stages API. Notifications are persisted,
recipient-addressed clarification events exposed to the owner in stages/events;
external IM/push transport is outside this change.

Interaction planner is an injected outbound contract. The conservative public
implementation ignores control-plane changes (with an explicit reason); a host
can bind an Agent planner. Client text cannot directly authorize replanning.
Planner proposals are validated/fenced before acceptance. Modes are ignore,
replan_all, replan_from. Replanning preserves the pinned DAG; it re-evaluates
execution, not authoring or publication. Dynamic planners receive a new plan
request on the next step. Managed revisions keep all nodes and reuse only
SUCCEEDED unaffected Tasks/manifests. Target and descendants get fresh Tasks;
unfinished nodes also get fresh Tasks. Previous live allocations are revoked;
late reports cannot submit or accept child Work. Old history/artifacts remain.

MethodStore is still single-process. Coordination is serialized per tenant/environment/Collet using its
reentrant guard, including reports. SQL events and Method state are separate
failure domains: accepted decisions are journaled before applying; retries
reconcile them rather than invoking the planner again. No distributed lock,
durable Method recovery, lease renewal or remote worker cancellation is claimed.

## Acceptance

- Consecutive steps, synchronous/asynchronous workers, replay/concurrency/stale
  revisions; one new dispatch maximum and no duplicate dispatch.
- Full Stage/edge/input/output/Agent/session/history projection with read auth.
- Ignore/full/partial planner decisions, immutable history and late-report fence.
- Clarification request/notification/reply/wait/resume and spoof/replay protection.
- Real DI/SQLite tests, HTTP envelopes, boundary checks and affected regressions.

## Resulting contract and propagation

The durable [execution contract](../../src/agentclaw/community/core/task/context_collab/l5_methods/SWARM-EXECUTION.md)
owns route/request/result examples, worker integration, pending interaction
journals and explicit limitations. New API, HTTP, DI and MethodStore contracts
are additive to existing operations; Stage context refs and planning interaction
facts are additive. External MethodStore implementations need the local guard
and atomic execution-revision CAS. No SQL migration/new table is required.
