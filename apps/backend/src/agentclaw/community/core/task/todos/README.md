# Todo workbench

This context owns the user-facing Todo product lifecycle, Space/Bot eligibility,
acceptance decisions, and the projection of B collaboration events into the
public Todo API. Context assembly, event sequencing, Bot execution, and Human
event persistence are owned by the collaboration service behind
`TodoCollaborationPlugin`. In the production composition root, A and B run
in the same process and the adapter calls `ContextCollabServiceProtocol`
directly; no internal HTTP self-call is used. The Todo creator remains the
Human principal, while Bot runtime addressing uses the Bot record's owner.

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
  - BotRepository
  - TodoCollaborationPlugin
internal_dependencies:
  - agentclaw.community.core.errors
  - agentclaw.community.core.task.todos.errors
  - agentclaw.community.core.task.todos.models
  - agentclaw.community.core.repository.protocols
```

### Change impact

Changes affect the public Todo create/read, Run, event, interaction, and
acceptance surfaces. Todo editing is intentionally unsupported. A does not
write an interaction table: Human interaction is appended through B, while
Human acceptance is persisted only by A. Except for A-owned ``completed``,
the public Todo status is projected from the bound B Context at read time.
Non-terminal B states remain ``in_progress``; terminal ``done`` or ``cancelled``
states enter A-owned ``pending_acceptance`` rather than mutating B during Human
acceptance.

The in-process adapter translates known ContextCollab domain rejections and
runtime startup failures into ``TodoCollaborationError`` so the Todo delivery
surface returns its stable collaboration-unavailable response. Todo-owned
validation and lifecycle conflicts remain Todo Core errors, including the
``409`` returned when a non-draft Todo is run.
