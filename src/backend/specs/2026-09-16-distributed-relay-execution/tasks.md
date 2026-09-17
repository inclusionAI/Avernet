# Implementation Tasks: Distributed Relay Execution

## Group 1 — Contracts and mode selection

- [x] Add the operations-owned `relay_execution` task setting, default it off,
  and stamp each new task as `centralized` or `relay` inside TaskService. The
  execute caller and task confirmation card cannot choose the mode.
- [x] Extend the existing TaskService protocols with typed relay-event report,
  generic candidate search, generic dispatch, and relay-aware BBS claim. No
  plan API, relay-prefixed API, arbitrary graph-write API, or BBS-publish API
  was added.
- [x] Add `progress_reason` and `failure_reason` to RuntimeInfo, patches,
  persistence serializers, callback/update adapters, and dashboard DTOs.
- [x] Add request and HTTP contract tests for unified callback/report, generic
  search/dispatch, and scoped relay BBS claim.

## Group 2 — Relay authority and persistence

- [x] Implement a graph-backed relay coordinator. The task graph's existing
  optimistic version/CAS persistence is the authority, so no parallel relay
  database or second task graph is introduced.
- [x] Persist only bounded SHA-256 token digests, holder, origin node, status,
  and expiry in graph extend props; never persist the bearer token plaintext.
- [x] Enforce one live turn per task, holder/token/expiry validation, single
  consumption, retry-compatible token digests, bounded event IDs, and recovery
  of an expired turn by idempotently retrying the originating execution event.
- [x] Reopen a consumed turn if Runner delivery fails, while rolling the target
  node back to PENDING and recording `failure_reason`.

## Group 3 — Core relay orchestration

- [x] Make relay execute persist/bootstrap only the shared blackboard and root
  RUNNING metadata. It never invokes the central planner or Runner for the root.
- [x] Accept authenticated `EXECUTION_RESULT`, require trajectory reasons,
  persist output, and grant the reporting executor the next relay turn.
- [x] Accept skill-produced `PLAN_RESULT`, require exactly one next node for the
  serial baton, enforce MAX_LOOP/HUNG, and close the complete ancestor chain
  when the root gap is satisfied.
- [x] Return an existing-discovery candidate catalog without deciding; validate
  `SEARCH_RESULT` candidate membership and persist single/group/BBS decisions.
- [x] Validate the persisted decision and current turn before reusing the
  existing Runner and group-formation paths for dispatch.
- [x] Enforce holder rules for a single Bot, cooperative-group manager/reporter,
  normalized composite Bot identities, and BBS claimant.
- [x] Keep relay BBS on the same callback/report continuation. MISS publishes
  the already-planned node to BBS, scoped claim starts that node, and completion
  grants the claimant a turn without calling centralized BBS root replanning.

## Group 4 — Runtime, platform, and skill integration

- [x] Inject relay task/node identity, callback/search/dispatch contract,
  persisted task instruction, objective, acceptances, upstream outputs, and the
  global blackboard into single-Bot and cooperative-group Runner prompts.
- [x] Force skill report for relay nodes even when the centralized polling
  switch is disabled; leave centralized prompt/poller behaviour unchanged.
- [x] Return `orchestration_mode=relay` and `root_node_id` from execute and make
  the existing frontend execute hook inject the root task into the active main
  Bot conversation. The confirmation card remains unchanged.
- [x] Add task-loop's relay-only routing segment and full protocol reference for
  execution report, global-gap planning, candidate decision, dispatch, MISS to
  BBS, reasons, idempotency, and stop conditions.
- [x] Validate the modified task-loop skill package.

## Group 5 — Verification

- [x] Cover three-baton completion and ancestor convergence.
- [x] Cover default centralized mode creating no relay turn.
- [x] Cover forged/stale turn rejection, duplicate execution report tokens,
  expiry recovery, and failed Runner dispatch retry.
- [x] Cover single-child baton enforcement, required trajectory reasons, and
  composite main-Bot identity.
- [x] Cover cooperative-group manager-only continuation.
- [x] Cover MISS, competing BBS claims, claimant continuation, and unchanged
  root status during relay BBS execution.
- [x] Run focused HTTP/core/Runner tests and the broad backend task suite.
- [x] Implement the frontend root-injection seam in its writable source repository,
  `/Users/shangjian.msj/Github/teamclaw`, rather than editing Avernet's read-only
  `src/frontend-nextgen` export. TeamClaw focused/full Jest, typecheck, lint, and
  Open Core export/build gates pass.
- [x] Align the BBS runner test doubles with the current public-visibility
  roster contract so the complete backend task suite is green.
- [~] OceanBase/live singlebox deployment validation was not available in this
  local implementation run.
