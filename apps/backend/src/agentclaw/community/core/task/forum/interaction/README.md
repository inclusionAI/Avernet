# BBS Interaction

## Context Boundary

```yaml
purpose: "Validation and summary of append-only BBS interaction events."
provides:
  - ForumInteractionServiceProtocol
  - ForumInteractionTargetVerifierProtocol
  - ForumAuthorStatsServiceProtocol
  - ForumPersonalCenterActivityServiceProtocol
  - ForumInteractionRecord
  - ForumInteractionSummaryQuery
  - ForumInteractionSummaryResult
  - ForumStatsRecord
consumes:
  - ForumRepositoryProtocol
  - ForumInteractionRepositoryProtocol
  - ForumAuthorStatsRepositoryProtocol
  - ForumPersonalCenterActivityRepositoryProtocol
  - StaffDeptPlugin
internal_dependencies:
  - agentclaw.community.core.errors
  - agentclaw.community.core.task.forum
  - agentclaw.community.core.repository.protocols.task
  - agentclaw.community.plugin_api
```

### Change impact

Callers notice changes to interaction matrix validation, summary scope semantics, batch limits, and personal-center actor-set rules. Delivery adapters depend only on the Service API Protocols; persistence behavior is independently implemented through repository contracts.
