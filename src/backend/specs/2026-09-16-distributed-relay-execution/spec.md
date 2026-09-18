# Distributed Relay Execution

## Problem

The current dynamic-task path is centralized: after every execution callback,
the task engine owned by the original owner bot computes the next gap, creates
children, selects executors, and dispatches them. This is correct for existing
tasks, but it prevents a completing executor (a bot, a group reporter, or a
BBS claimant) from continuing the task with the context it just acquired.

## Goal

Add an opt-in distributed relay execution mode. In this mode, each completed
execution turn hands the right to plan and dispatch the next turn to the
authenticated executor that completed the current node. The relay continues
through single bots, cooperative groups, and BBS claimants until the root
acceptance closes or the configured iteration limit is reached.

## Non-goal / compatibility promise

- Existing tasks created while the operations relay switch is off remain on the
  current centralized engine path, with identical APIs, callback semantics,
  state transitions, and scheduling.
- Static workflow, YAML/group-creation, static-plan, and direct BBS task types
  retain their existing specialized centralized adapters even while the relay
  switch is on; the relay switch applies to skill-driven dynamic tasks.
- This does not replace TaskGraphService as the graph SSOT, nor allow a bot to
  write graph state directly.
- This does not introduce a second task graph, a client-side scheduler, or an
  unbounded delegation loop.
- The task-loop skill remains the policy/prompt package. It invokes only the
  approved TaskService HTTP operations; backend owns identity verification,
  state validation, persistence, and runner dispatch.

## User-visible behaviour

1. A user creates a task exactly as today. The recognition skill still emits a
   clarification/confirmation card and the platform calls the existing execute
   API.
2. Execution mode is not shown in the recognition/clarification card and is
   not selected by the user. An operations-owned task setting selects
   `centralized` (default) or `relay`; TaskService resolves that setting when a
   task is created and stamps the resulting value into
   `execution_config.orchestration_mode` and the graph blackboard.
3. In relay mode, `TaskService.execute` persists task metadata and initializes
   the shared graph/context blackboard only. It does not plan or use Runner to
   redeliver the root to the owner bot: the platform injects the new task handle
   into that already-active owner-bot conversation, which directly runs the
   existing execution and acceptance skill segments.
4. After a non-terminal result, the authenticated holder loads task-loop's
   planning segment over the trusted blackboard context and produces a local
   `PlanResult`; planning itself makes no query API call. It reports that result
   through the unified callback endpoint, which creates the planned next nodes
   in the blackboard. The holder constructs a search `query` from the next
   node's goal/gap/instruction and calls generic TaskService search. Search
   returns only current candidate metadata. The Skill decides single Bot,
   collaboration group, or MISS, reports its `SearchResult` through the same
   callback endpoint, and finally calls generic TaskService dispatch. Backend
   validates each task-graph stage and invokes the normal Runner only for an
   already-planned and already-selected node.
5. A cooperative group has exactly one continuation holder: its authenticated
   reporter/master/manager. Other members may contribute output but cannot
   continue the graph.
6. If relay search has no candidate, its `SEARCH_RESULT` reports the MISS and
   requested BBS escalation through the unified callback endpoint. Backend
   applies the existing BBS publication/escalation rules. A BBS claimant
   receives a relay turn only after its normal CAS claim succeeds. After that
   claimant completes a BBS node, it follows the same executor-owned
   planning/search/dispatch loop as a single bot or group reporter; relay mode
   never returns that turn to a centralized root-node `PLANNING` cycle.
7. Every planning and execution-selection trajectory is stored on the relevant
   node's `run_info`: `progress_reason` explains why a next task or executor
   was chosen, and `failure_reason` explains a planning, search, delivery, or
   execution failure.
8. Completion, cancellation, BBS exhaustion, plan/dispatch failure, expired
   relay turn, and recovery are observable in the dashboard/action log. A
   stale, duplicated, or non-holder continuation is rejected without changing
   the graph.

## Required invariants

- `orchestration_mode` is a closed, validated internally stamped config value:
  `centralized` (default) or `relay`. A task caller cannot use the recognition
  card or execute request to override the operations-owned setting.
- A task has at most one live relay turn. It is bound to task id, triggering
  node, actor identity, and an expiry, and is protected by the task graph's
  optimistic version/CAS write; it is single-use once a commit succeeds.
- Only the executor identity proved by the callback/result channel may acquire
  the next turn. BBS uses its existing root-owner CAS identity; group execution
  uses only its configured reporter/master/manager identity.
- A skill's `PlanResult` and `SearchResult` are proposals, not graph writes.
  They are reported only through authenticated `callback/report` events. The
  backend checks parent/child topology, node-id uniqueness, state readiness,
  authorized mode, graph version, limits, actor lease, and required reasons
  before using TaskGraphService and TaskRunner. Search does not create a
  catalog snapshot and `SEARCH_RESULT` does not carry `catalog_id`; `event_id`
  provides callback idempotency.
- Relay planning creates exactly one next node per turn. This is a serial baton;
  centralized planning retains its existing multi-child behaviour.
- A state-advancing plan/selection report must provide a non-empty
  `progress_reason`; an unplannable, MISS, delivery-failed, or execution-failed
  result must provide a non-empty `failure_reason`.
- A completed callback in centralized mode, including a BBS result, follows the
  old `on_report` and root-node `PLANNING` flow; no relay lease, route, or
  feature-flag decision may alter it.
- In relay mode, no BBS completion may call the centralized root-gap collection
  path or mutate the root to `PLANNING` merely to continue work. Its holder
  receives a normal relay turn and submits the next plan/search decision.
- A recovering process can safely resume an uncommitted relay turn after expiry
  using the existing graph/recovery lease semantics; it must not duplicate a
  committed next-node dispatch.

## Acceptance criteria

- A default-mode dynamic task follows the current owner-driven planning and
  dispatch call path; regression tests prove no relay lease is created.
- A relay task persists its root without initial Runner delivery, then the
  already-active Bot A executes it from the injected task handle, plans/selects
  Bot B after a non-terminal result, and the backend dispatches Bot B through
  the normal runner.
- A relay cooperative-group result accepts continuation only from its reporter
  and rejects an ordinary member.
- Concurrent, replayed, expired, or wrong-actor continuation submissions have
  exactly one winner and leave no partial children or double runner dispatch.
- Relay MISS reaches the existing BBS escalation path; a valid BBS claimant can
  continue once, while a non-claimant cannot.
- Root success, `MAX_LOOP`/plan limit exhaustion, callback failure, and restart
  recovery converge to the documented terminal/dashboard state.
- Service API, HTTP schema, authorization, persistence, and conformance tests
  document and validate the additive contract.
- Relay plan, search-selection, BBS-MISS, and execution-failure paths expose
  their required `run_info.progress_reason` / `run_info.failure_reason` values
  in the task dashboard.

## Open-source boundary

Real bot execution, group runtime, BBS delivery, and skill loading remain
adapter concerns. The community backend provides the transport-agnostic relay
contract, state-machine enforcement, persistence seams, and singlebox doubles;
no private endpoint, credential, or engine filesystem assumption is added.
