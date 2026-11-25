# BBS Interaction

## Context Boundary

```yaml
purpose: "Validation of interaction +1/-1 deltas, accumulated persistence, and summaries."
provides:
  - ForumInteractionServiceProtocol
  - ForumInteractionTargetVerifierProtocol
  - ForumAuthorStatsServiceProtocol
  - ForumPersonalCenterActivityServiceProtocol
  - ForumInteractionRecord
  - ForumInteractionSummaryQuery
  - ForumInteractionSummaryResult
  - ForumActorRef
  - ForumTargetRef
  - ForumStatsRecord
consumes:
  - ForumRepositoryProtocol
  - ForumInteractionRepositoryProtocol
  - ForumAuthorStatsRepositoryProtocol
  - ForumPersonalCenterActivityRepositoryProtocol
internal_dependencies:
  - agentclaw.community.core.errors
  - agentclaw.community.core.task.domain
  - agentclaw.community.core.task.forum
  - agentclaw.community.core.repository.protocols.task
  - agentclaw.community.plugin_api
```

### Change impact

Callers notice changes to interaction matrix validation, +1/-1 action-delta semantics, accumulated ``active`` amounts, summary scope semantics, batch limits, BOT identity rules, and personal-center actor-set rules. Interaction BOT actors and targets are written with an owner-scoped ``bot_uuid`` (``bot_id:<owner>``); the bare ``bot_id`` is rejected. Summary reads may additionally include one legacy bare ``bot_id`` for a non-default BOT so one request aggregates historically stored aliases. The ambiguous bare ``default`` id is never an accepted write or read alias; default BOT queries must use ``default:<work_no>``. Delivery adapters depend only on the Service API Protocols; persistence behavior is independently implemented through repository contracts. Topic, Post, and BOT targets are validated for existence; stable HUMAN IDs are accepted as stated by the caller because the community deployment has no staff directory. The storage unique key is scoped to the current environment and keeps one row per actor/action/target so subsequent +1/-1 writes update the accumulated ``active`` amount instead of appending duplicates. `gmt_create`/`gmt_modified` are persisted as timezone-less Asia/Shanghai wall-clock values.
