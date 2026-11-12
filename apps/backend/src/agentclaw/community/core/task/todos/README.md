# Todo workbench

This context owns the user-facing Todo product lifecycle, Space/Bot eligibility,
automatic dispatch, acceptance decisions, and the public event/interaction view.
Todo state is persisted by A as `pending`, `in_progress`,
`pending_acceptance`, or `completed`; it is not recomputed during reads from B.

Creating a Todo only persists `pending`. `TodoScheduler` scans pending rows,
creates and runs the B Context through `TodoCollaborationPlugin`, binds the
Context id, and advances the Todo to `in_progress`. The same scheduler refreshes
running Contexts: only a confirmed runtime `COMPLETED` result makes the Context
`done` and advances the Todo to `pending_acceptance`. Runtime failure states stay
visible in B and leave the Todo `in_progress` for inspection or recovery.
Human approval advances the Todo to `completed`; rejection keeps it in
`pending_acceptance`.

A and B run in the same process in the production composition root, so the
adapter calls `ContextCollabServiceProtocol` directly rather than making an
internal HTTP self-call. The creator remains the Human principal while Bot
runtime addressing uses the Bot record owner. The public `/run` operation is a
compensation/operator entry point; the ordinary frontend neither edits Todos
nor exposes manual Run.

## Context Boundary

```yaml
purpose: Own persisted Todo lifecycle, automatic Context dispatch, runtime refresh, interactions, and acceptance.
provides:
  - TodoServiceProtocol
  - TodoService
  - TodoExecutionServiceProtocol
  - TodoExecutionService
  - TodoScheduler
  - TodoRecord
  - TodoPage
  - TodoEventRecord
consumes:
  - TodoRepositoryProtocol
  - SpaceAccessServiceProtocol
  - BotRepository
  - CachePlugin
  - TodoCollaborationPlugin
internal_dependencies:
  - agentclaw.community.core.errors
  - agentclaw.community.core.repository.protocols
  - agentclaw.community.core.spaces
  - agentclaw.community.core.task.todos
  - agentclaw.community.di.todo_config
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.plugin_api
```

### Change impact

Changes affect Todo create/read, automatic scheduling, the compensation Run
endpoint, event and interaction projection, and acceptance. Repository state
transitions are compare-and-set operations so duplicate schedulers or operator
retries cannot move a Todo backwards. Context creation and Todo persistence are
not one cross-store transaction; dispatch therefore resumes from an already
bound Context when retrying. A does not own an interaction table: Human
interaction events and executor progress remain in B, while Human acceptance
and the four product states remain in A.
