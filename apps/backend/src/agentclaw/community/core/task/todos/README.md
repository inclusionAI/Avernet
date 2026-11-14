# Todo workbench

This context owns the user-facing Todo product lifecycle, Space/Bot eligibility,
automatic dispatch, acceptance decisions, and the public event/interaction view.
Todo state is persisted by A as `pending`, `in_progress`,
`pending_acceptance`, or `completed`; it is not recomputed during reads from B.

Creating a Todo only persists `pending`. `TodoScheduler` periodically scans A-owned rows while holding a distributed lease. It processes `pending` first, then `pending_acceptance`, then `in_progress`:

1. `pending`: create, bind, and run the B Context, then advance A to `in_progress`.
2. `pending_acceptance`: repair an acceptance decision that was durably appended in B but whose A-side projection failed.
3. `in_progress`: refresh the B runtime; if durable Human work is waiting behind a completed Context, resume the original Bot session.

Only a confirmed runtime `COMPLETED` result makes the Context `done` and advances the Todo to `pending_acceptance`. Runtime failure states stay visible in B and leave the Todo `in_progress` for inspection or recovery.

Acceptance is event-first. The client supplies a stable `decision_id`; A first finds or appends one immutable Human `task.accepted` or `task.rejected` event in B, then persists the same decision in A. Each acceptance round permits exactly one decision: reusing the same ID and payload is idempotent within that round, while a second ID or a changed verdict, content, actor, Todo, or targets conflicts. Repair considers the first valid decision appended after the latest `task.completed`; an older rejection can never be replayed against a later completed round. This ordering lets the scheduler repair B-success/A-failure without inventing or replacing a decision.

- Approval appends `task.accepted`, advances the Todo from `pending_acceptance` to `completed`, leaves the completed Context unchanged, and does not start another Bot run.
- Rejection requires full Human rework content and at least one stable event/artifact target. It appends `task.rejected`, advances the Todo from `pending_acceptance` to `in_progress`, and initially leaves the Context `done`. On the next scheduler refresh, that rejection is recognized as unstarted Human work; B resumes the previous executor session, appends `task.started`, changes the Context to `in_progress`, and delivers the rework instruction to the original Bot. A later completed run returns the Todo to `pending_acceptance`.

Target IDs are durable references. `floor` is presentation metadata captured from the interaction view and is not used as a domain identity. The event retains the full Human instruction and selected targets so the interaction timeline can reconstruct the rejection even after a refresh or retry.

A and B run in the same process in the production composition root, so the adapter calls `ContextCollabServiceProtocol` directly rather than making an internal HTTP self-call. The creator remains the Human principal while Bot runtime addressing uses the Bot record owner. The default ContextCollab status projection keeps its existing northbound field set; the Todo in-process adapter explicitly requests event payloads for its richer interaction projection.

The Todo Events endpoint stays compact and event-centric: it returns normalized event metadata, concise primary content, and artifact links, but not the complete event payload. The Interactions endpoint is actor/content-centric and richer: it includes the Human objective in `task.created`, Human messages and rejection instructions, selected rejection floors from the full payload, and Bot `task.completed` messages. Text artifacts are inlined into the corresponding Bot completion message; file, link, and code artifacts remain separately renderable.

OpenAPI delivery also sends `sender_options.from` as the creator so the executor session is attributable to that principal. The public `/run` operation is a compensation/operator entry point; the ordinary frontend neither edits Todos nor exposes manual Run.

While a Todo is `pending_acceptance`, posting a normal Human interaction atomically claims A as `pending_acceptance -> in_progress`, then append-only records `user_interaction.posted` in B. Rejection uses the same resume mechanism after its B-first acceptance event and A-side state projection. Status refresh keeps A `in_progress` while B is still `done` and the latest Human-work event has not yet been followed by `task.started`. The scheduler then resumes B from that event and reuses the previous executor session.

Resume retries are idempotent. Once B is `in_progress` or a later `task.started` exists, no additional resume is dispatched; the community runtime uses the triggering event ID as the executor message ID. A delivery failure leaves the claimed Todo `in_progress` and fails closed rather than reopening the interactive state during uncertain delivery.


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
  - TodoArtifactServiceProtocol
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
