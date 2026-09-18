# Technical Plan: Distributed Relay Execution

## Design decision

Add `orchestration_mode` to the task execution configuration with a
compatibility-safe default of `centralized`. The value is selected by an
operations-owned task setting at task creation and then stamped into the task;
it is not a user-facing recognition-card choice or a caller-selected execute
parameter. Centralized tasks keep their current
`ExecutionEngine.on_report -> _on_pass_collect/_on_fail_collect` behaviour.

Relay distributes *skill-driven decision and continuation authority* to the
most recently completed, authenticated executor, while retaining *state
authority* in backend services:

```text
executor result -> callback identity verification -> relay-turn CAS grant
  -> holder runs task-loop planning from the shared blackboard
  -> callback/report(PLAN_RESULT) writes planned nodes to blackboard
  -> calls pure candidate search with a Skill-built query
  -> callback/report(SEARCH_RESULT) writes Skill selection to target node
  -> dispatches that already-configured node -> normal Runner dispatch
```

Relay bootstrap is intentionally not a Runner dispatch. `TaskService.execute`
only creates the task metadata/graph blackboard; the platform injects the task
handle and `[task-execute]` context into the owner bot's already-active
conversation. TaskService bootstraps the root as RUNNING; that bot executes it
directly and becomes the first holder after reporting its result.

## Contract surfaces

### Service API

Extend the existing `TaskServiceProtocol`; do not introduce a relay-specific
service facade. The generic Task Service interface gains:

1. `search_task_candidates(query=...)` — searches candidates from the query
   supplied by the Skill and returns only the current catalog-backed candidate
   metadata. It does not read or update the task graph.
2. `report_task_event(...)` — the service target behind the unified
   callback adapter. It accepts typed `PLAN_RESULT`, `SEARCH_RESULT`, and
   `EXECUTION_RESULT` reports and performs their constrained graph writes.
3. `dispatch_task(request)` — takes only an already planned and selected node,
   validates its persisted state and holder turn, then starts the existing
   runner outside the graph lock.

Planning remains a local skill operation, so there is deliberately no plan API.
There is deliberately no `search-result` or `bbs/publish` endpoint:
`SEARCH_RESULT` is reported through callback and a validated `outcome=MISS`
invokes the existing BBS publication path internally.
Relay holders reach these capabilities through typed HTTP DTOs and explicit
TaskService arguments. Relay turn validation is
an internal authorization concern of these methods, not a separate public
service surface. Use dedicated value-object request/response contracts rather
than loose dicts. All names, optionality, and async signatures require Service
API conformance tests.

### HTTP adapter

Add authenticated internal task endpoints below
`/api/v1/collaboration/tasks/`; they are general task-domain capabilities, not
relay-prefixed APIs:

| Operation | Route | Caller supplies | Backend returns / does |
| --- | --- | --- | --- |
| Search candidates | `POST /search` | `{query}` built by the Skill | current catalog-backed candidate metadata only |
| Dispatch | `POST /dispatch` | task/node, relay turn, idempotency key | validates persisted plan/selection, then starts existing Runner |

Extend the existing `POST /callback/report` task-loop envelope with
`event_type`, `relay_turn`, `event_id`, `progress_reason`, `failure_reason`,
and a typed payload. Its new event types are `PLAN_RESULT`, `SEARCH_RESULT`,
and `EXECUTION_RESULT`. The normal response is extended only for an eligible
relay holder: it returns the opaque `relay_turn`, origin node, and expiry. The
trusted blackboard is injected into the next Runner prompt rather than returned
as caller-controlled graph data. It remains backward-compatible for centralized tasks and
non-holders. Explicit abandonment is not part of the first public skill
contract; lease expiry and recovery are the safe fallback.

Do not expose graph mutation endpoints that accept arbitrary nodes. In
particular, skill code must not receive access to `update_task_node_info`,
`attach_bbs_node`, or generic TaskGraphService methods. `bbs/claim` remains a
specialized competitive command and is extended to identify the BBS node being
claimed in relay mode; `bbs/attach` and `bbs/result` remain only for centralized
compatibility. Relay BBS execution result uses `callback/report` like all other
executors. Map domain conflicts to 409 and stale/expired turns to a distinct,
documented error code.

The normal callback response can include a `relay_turn` descriptor only for the
authenticated continuation holder. Its existing body and centralized behaviour
remain compatible. HMAC callback authentication and BBS claim identity must be
reused; no caller-supplied `bot_id` is proof of identity.

### Persistence

Persist a relay-turn record (or atomically versioned fields on `task_info`) with
task id, triggering node id, holder identity/type, opaque token digest, graph
version, status, expiry, and audit timestamps. Prefer a separate append/audit
record when it avoids overloading task graph state. Claim/grant and consume are
conditional writes, compatible with SQLite and OceanBase. Never persist an
opaque bearer token in plaintext.

Extend `RuntimeInfo` and `TaskNodePatch` with nullable `progress_reason` and
`failure_reason`. They are persisted as part of node `run_info` and serialized
by the dashboard DTO. `PLAN_RESULT` writes its planning rationale on the
current node; `SEARCH_RESULT` writes selection or MISS rationale on the target
planned node; execution and Runner delivery failures write `failure_reason` on
their execution node. They are not merely action-log payloads.

## Core flow

1. `TaskService.execute` validates and stores mode. Default remains
   `centralized`; relay persists only the graph blackboard and returns a task
   handle for platform injection into the existing owner-bot conversation.
2. TaskService bootstraps the relay root as RUNNING and the platform injects the
   task handle into the current owner conversation. The owner bot directly runs
   task-loop execution/acceptance. No initial Runner
   delivery or root planning occurs in relay mode.
3. Callback adapter authenticates and correlates the executor, then records its
   output/result using TaskGraphService. A terminal root result closes the graph;
   a non-terminal result grants a relay turn instead of central planning and
   returns a relay turn to the holder. The Runner prompt for a dispatched next
   node carries the trusted blackboard context.
4. The holder runs local task-loop planning and reports `PLAN_RESULT` through
   `callback/report`; TaskService validates it and creates exactly one next graph
   node. The holder builds a query from that planned spec and calls `TaskService.search`;
   the search endpoint returns current candidate metadata only. The Skill decides
   HIT_SINGLE, HIT_MULTI_BOTS, or MISS and reports `SEARCH_RESULT` through the
   same callback endpoint. TaskService validates task identity, node state,
   topology, mode, depth, required reasons, and turn before writing the target
   node's `run_mode`, `assignee`, and group information; it does not validate
   candidate membership against a Backend catalog snapshot.
   The holder calls `TaskService.dispatch` only after that write; it validates
   the persisted configured node and delegates delivery to the existing
   TaskRunner/TaskExecutor.
5. A skill reports `SEARCH_RESULT(outcome=MISS)` through
   `callback/report`. BBS publication and claim reuse their existing
   delivery/CAS mechanism; after claim and completion, the claimant receives
   the same relay turn and resumes this local
   planning/search/dispatch cycle.
   Crucially, a relay BBS result does **not** enter the centralized
   `on_bbs_report -> _on_pass_collect -> root PLANNING` continuation path. It
   records the BBS node outcome, grants the claimant's relay turn, and waits for
   that claimant's skill-produced plan/search proposal just like every other
   relay executor.

## State model

Keep node and graph status enums unchanged. Add relay turn state separately:
`GRANTED` and `CONSUMED`; expiry is derived from `expires_at_ms`. It is an execution
coordination record, not a task-node lifecycle state.

The relay continuation boundary is therefore mode-specific: centralized BBS
retains the current root re-evaluation semantics, while relay BBS retains the
root's existing relay state and makes the completed BBS executor the sole next
planner until it consumes or loses its turn. This is a branch in orchestration
policy, not a new root/node status transition.

`TaskGraphService` remains the sole graph mutation gateway. Relay-only service
logic validates a reported plan before using existing graph operations, and a
separate constrained path applies search selection to the planned node. Dispatch consumes
the turn only when its persisted node is successfully accepted for delivery. A
failed dispatch must leave the turn retryable or clearly expired, never leave a
half-written node or duplicate runner delivery.

## Skill and runtime changes

Extend task-loop with a relay execution context marker, a dispatch segment, and
two narrow result output contracts.
The baton holder uses the existing execution and acceptance segments first;
these additions only distinguish its post-result continuation from the current
central-owner continuation:

- `[task-relay-planning]` returns the same local `PlanResult` shape as planning;
  it makes no API call and contains no identity decision.
- `[task-relay-search]` returns the existing four-state search result over the
  supplied catalog.

These markers must outrank user-recognition triggers and cannot trigger
acceptance. The execution prompt tells only the authorized holder to request a
continuation after final result reporting. The skill documents stable task API
paths while runtime injects the backend base URL and authenticated operation
context; it never guesses or hardcodes a deployment endpoint. BBS plaza
claim/delivery lifecycle is not redesigned by this feature, but after a claim
the BBS executor is a normal relay baton holder; it must not hand planning back
to the root/owner engine.

The existing task-loop parts change as follows:

- recognition and its clarification/confirmation card are unchanged. Runtime
  resolves the operations-owned task setting and stamps the mode after user
  confirmation; the user never selects or sees it;
- execution/acceptance receives `orchestration_mode`; the owner directly
  executes the injected root handle, and any holder follows a returned
  `relay_turn` only when the result is non-terminal;
- planning/search replace their "owner bot" assumption with "authorized relay
  holder" under the two relay markers, while retaining their existing JSON
  shapes and their centralized markers unchanged;
- planning reports `PLAN_RESULT` and search reports `SEARCH_RESULT` through
  the unified callback envelope; search expresses BBS escalation as a MISS
  next-action rather than calling a publish endpoint;
- a `task-dispatch` segment invokes generic dispatch only after the plan and
  selection are persisted; it makes no execution-choice decision;
- Runner instruction formatting must inject a skill-recognizable relay handoff
  prompt built from the persisted `TaskNode.task_spec`: task instruction,
  objective, deliverables, acceptance requirements, relevant blackboard input,
  task/node identity, and callback contract. Backend supplies this structured
  handoff context; task-loop owns execution policy and result formatting;
- BBS claim and delivery remain specialized BBS APIs, while a claimed BBS executor
  receives the same relay turn/context as another holder after completion.

## Compatibility and rollout

- Schema migration is additive; legacy rows read as `centralized`.
- Gate relay with an operations-owned task setting that resolves to the
  validated task-level value; it can reject/newly select relay submissions while
  preserving reads.
- Ship protocol/schema support before enabling the operations setting. The
  platform execution hook injects the relay root prompt, but the visible
  recognition/confirmation card schema does not change.
- Dashboard exposes mode and redacted relay-turn status; it never exposes the
  opaque token.
- Rollback disables new relay task creation. Existing relay tasks remain
  recoverable: either finish their active turn or fall back through a documented
  server-side recovery decision, never silently become centralized mid-turn.

## Test strategy

Unit tests cover turn token/identity/version validation, graph atomicity,
continuation-boundary rules, candidate validation, expiration, and centralized
non-regression. Contract tests cover the new Service API and HTTP DTOs.
Repository tests cover concurrent CAS and recovery on SQLite plus the existing
OceanBase validation checklist. Singlebox E2E covers relay single-bot,
cooperative-group reporter, BBS handoff, recovery, and default-mode regression.
