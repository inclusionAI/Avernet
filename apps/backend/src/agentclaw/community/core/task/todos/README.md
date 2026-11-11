# Todo workbench

This context owns the user-facing Todo product lifecycle, Space/Bot eligibility,
acceptance decisions, and the projection of B collaboration events into the
public Todo API. Context assembly, event sequencing, Bot execution, and Human
event persistence are owned by the collaboration service behind
`TodoCollaborationPlugin`.

## Context Boundary

```yaml
purpose: Own the user-facing Todo lifecycle, Space/Bot eligibility, acceptance decisions, and B-event projection.
provides:
  - TodoServiceProtocol
  - TodoService
  - TodoRecord
  - TodoPage
  - TodoEventRecord
consumes:
  - TodoRepositoryProtocol
  - SpaceAccessServiceProtocol
  - SpaceMemberServiceProtocol
  - BotRepository
  - TodoCollaborationPlugin
internal_dependencies:
  - agentclaw.community.core.errors
  - agentclaw.community.core.task.todos.errors
  - agentclaw.community.core.task.todos.models
  - agentclaw.community.core.repository.protocols
```

### Change impact

Changes affect the public Todo CRUD, Run, event, interaction, and acceptance
surfaces. A does not write an interaction table; Human interaction and
acceptance records are collaboration events appended through B and projected
through A.
