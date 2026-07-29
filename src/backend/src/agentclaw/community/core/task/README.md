# `agentclaw.community.core.task`

Goal-driven task execution loop — the system carries a user goal through
intake → plan → execute → validate → deliver, with the TaskScheduler doing
deepresearch dynamic orchestration (route / decompose / dispatch / reroute /
escalate) and owner-bot SKILLs doing per-node state judgement + acceptance,
reporting back as events that TaskService folds into state.

Design source of truth:
`ocb/src/backend/docs/specs/2026-07-28-goal-driven-task-execution/`
(spec.md / plan.md / tasks.md).

## Context Boundary

```yaml
purpose: "Goal-driven task execution loop: aggregate root, state machine, events, persistence seam + Scheduler orchestration and owner-bot SKILL verification回投."
provides:
  - "Task aggregate root (spec + execution_graph two faces)"
  - "Task/Node state machine (legal-transition guard)"
  - "Domain events (single-writer seq watermark; reported vs system-driven)"
  - "TaskRepo / TaskEventRepo persistence Protocols"
  - "TaskService (unified authority: query + intake + on_event fold + claim_node)"
  - "TaskScheduler编排 Ports (BotDiscoverPort / DecomposerPort / TaskDriverPort)"
  - "ExecutionPort (effects)"
ownership:
  - "api/task/protocols.py — 5 business Protocols (Protocol-only layer)"
  - "core/task/domain/ — pure models, state machine, events, repository Protocols"
  - "adapters/http/task/ — FastAPI router + pydantic schemas"
  - "plugins/community/task/ — Noop impls (Phase 0); real impls Phase 2-6"
  - "di/modules/infrastructure/community/task.py — Noop→Protocol binding"
consumes:
  - "agentclaw.community.api.task (Protocols)"
  - "agentclaw.community.plugin_api (infrastructure Plugin base, not inherited by task Protocols)"
internal_dependencies: []
forbidden_imports:
  - "core/task/domain and api/task MUST NOT import plugins / adapters / di"
  - "No 蚂蚁中间件 prod adapter (ZDAS/ZCache/Mist/ARCA/Buservice/DingTalk) in Avernet"
  - "No skill/算法代码 (8 SKILL prompts + LLM understand/judge) in Avernet"
```

### Change impact

- **State machine** (`domain/state_machine.py`) is the single authority for
  legal transitions; TaskService `_apply_event` consults it before any state
  write. Adding a new status or edge is a breaking change for every event fold.
- **Event kinds** (`domain/events.py`) are the wire contract for owner-bot
  SKILL 回投 and Scheduler编排. New kinds must declare `reported` classification
  via `is_reported_kind` or the guard misroutes them.
- **Protocols** in `api/task/protocols.py` are the seam between the four layers
  and the plugin implementations; signature changes ripple to every impl and
  the HTTP router. The unified `TaskService` MUST NOT be split back into
  Query/Intake services (spec/plan 定稿).
- **Avernet 红线**: this module ships to the open-source community build, so it
  must never import AntGroup middleware adapters or skill/algorithm code. Those
  live only in the corp `ocb` repo (Phase 6 bcsfuse adapter).

### Implementation phases (from tasks.md)

| Phase | Scope | Status |
| --- | --- | --- |
| 0.1 | Domain models (enums + dataclasses) | ✅ |
| 0.2 | State machine transitions + guard | ✅ |
| 0.3 | Domain events + seq invariant | ✅ |
| 0.4 | TaskRepo / TaskEventRepo Protocols | ✅ |
| 0.5 | 5 api Protocols (unified TaskService + 4 Ports) | ✅ |
| 0.6 | HTTP schemas + router 骨架 | ✅ |
| 0.7 | Community Noop impls | ✅ |
| 0.8 | DI module registration | ✅ |
| 0.9 | Architecture guard + Context Boundary | ✅ |
| 0.10 | End-to-end smoke | ✅ |
| 1 | Persistence (2 Repo + 3 tables ORM) | pending |
| 2 | TaskService (event fold authority) | pending |
| 3 | TaskScheduler (route/decompose/dispatch/reroute/tick) | pending |
| 4 | Execution loop + 4 Port impl + Case A/D | pending |
| 5 | BBS + Case B/C/E | pending |
| 6 | corp bcsfuse adapter (ocb repo) | pending |