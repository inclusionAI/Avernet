# Todo workbench

This context owns the user-facing Todo product lifecycle, Space/Bot eligibility,
automatic dispatch, acceptance decisions, and the public event/interaction view.
Todo state is persisted by A as `pending`, `in_progress`,
`pending_acceptance`, or `completed`; it is not recomputed during reads from B.
The unfiltered workbench list is paged in its visible group order—`pending`,
`in_progress`, `pending_acceptance`, then `completed`—and remains oldest-first
inside each group. A status-filtered list is oldest-first within that state.
This keeps dispatchable work on the first “全部” page without changing the
scheduler's independent oldest-first status scans.

Creating a Todo only persists `pending`. `TodoScheduler` periodically scans A-owned rows while holding a distributed lease. It processes `pending` first, then `pending_acceptance`, then `in_progress`:

1. `pending`: create, bind, and run the B Context, then advance A to `in_progress`.
2. `pending_acceptance`: repair an acceptance decision that was durably appended in B but whose A-side projection failed.
3. `in_progress`: refresh the B runtime; if durable Human work is waiting behind a completed Context, resume the original Bot session.

A confirmed runtime `COMPLETED` result or the active Bot execution's authenticated
`POST /api/v1/collaboration/tasks/todos/{todo_id}/complete` call appends
`task.completed` and makes the Context `done`. The existing scheduler remains
the only owner of completing the active execution credential and advancing the
Todo to `pending_acceptance`; the completion endpoint does not mutate A-owned
Todo state directly. Runtime failure states stay visible in B and leave the
Todo `in_progress` for inspection or recovery.

Acceptance is event-first. The client supplies a stable `decision_id`; A first finds or appends one immutable Human `task.accepted` or `task.rejected` event in B, then persists the same decision in A. Each acceptance round permits exactly one decision: reusing the same ID and payload is idempotent within that round, while a second ID or a changed verdict, content, actor, Todo, or targets conflicts. Repair considers the first valid decision appended after the latest `task.completed`; an older rejection can never be replayed against a later completed round. This ordering lets the scheduler repair B-success/A-failure without inventing or replacing a decision.

- Approval appends `task.accepted`, advances the Todo from `pending_acceptance` to `completed`, leaves the completed Context unchanged, and does not start another Bot run.
- Rejection requires full Human rework content and at least one stable event/artifact target. It appends `task.rejected`, advances the Todo from `pending_acceptance` to `in_progress`, and initially leaves the Context `done`. On the next scheduler refresh, that rejection is recognized as unstarted Human work; B resumes the previous executor session, appends `task.started`, changes the Context to `in_progress`, and delivers the rework instruction to the original Bot. A later completed run returns the Todo to `pending_acceptance`.

Target IDs are durable references. `floor` is presentation metadata captured from the interaction view and is not used as a domain identity. The event retains the full Human instruction and selected targets so the interaction timeline can reconstruct the rejection even after a refresh or retry.

A and B run in the same process in the production composition root, so the adapter calls `ContextCollabServiceProtocol` directly rather than making an internal HTTP self-call. The creator remains the Human principal while Bot runtime addressing uses the Bot record owner. The default ContextCollab status projection keeps its existing northbound field set; the Todo in-process adapter explicitly requests event payloads for its richer interaction projection. ContextCollab execution is a Task-core executor, not a deployment Plugin. New Todo runs reuse Task's existing `OpenApiBotPort` and owner-granted BaaS `allowed-bots` authorization without modifying `TaskExecutor` or introducing a shared Task/Todo abstraction. Optional BCS ports are retained only to resume and inspect historical group-backed Todo Contexts.

`TodoCollaborationPort` is an internal Task/Todo consumer boundary, not a globally replaceable Plugin API. `InProcessTodoCollaborationAdapter` is the production composition and delegates to the same-process `ContextCollabServiceProtocol`; it is fixed by `TaskTodoBindings` rather than selected by deployment Profile. The deterministic fake lives under Todo tests and is not shipped or registered as a local plugin.

The Todo Events endpoint stays compact and event-centric: it returns normalized event metadata and concise primary content, but not the complete event payload. Every successfully persisted product artifact appends one durable `artifact.reported` event, so this view can say what was produced in chronological order. Text uses a whitespace-normalized 20-character preview; file, link, and code events use `artifact_name` as the concise display title. File rows fall back to the sanitized file name when a legacy caller omits `artifact_name`. `task.completed` remains a separate run-completion notice and never substitutes for the artifact events.

The Interactions endpoint is actor/content-centric and richer: it includes the Human objective in `task.created`, Human messages and rejection instructions, selected rejection floors from the full payload, and `artifact.reported` payloads. The client resolves each artifact event by its stable `artifact_id`, displays complete text and complete file/link/code details at the event's exact position, and uses the reporting Bot as the actor. The Artifacts endpoint remains the complete persisted inventory and content source.

New single-Bot Todo delivery calls the BaaS Open API directly with the exact
`bot_id:owner_id` identity. It does not create a synthetic one-Bot BCS group and
does not add the Todo creator as a group observer. Eligibility is determined by
the live Bot record, selected Space scope, and a usable owner identity. Bot
catalog `visibility` and `user_visibility` are not Todo eligibility gates;
delivery authorization comes from the owner's secbaas `allowed-bots` grant.

The direct runtime returns a real `run_id` and `session_id`, both of which are
persisted in `task.started`. Later Human interaction or rejection reuses the same
session by sending the BaaS message with `metadata.session_id`; each resumed
round receives and persists a new `run_id`. The triggering event ID is the
message idempotency key. Historical Contexts that already store
`group_id + session_id` continue to use the legacy BCS resume/status path, but
new Todo execution never creates a group. No new table or Context schema is
introduced. The public `/run` operation remains a compensation/operator entry
point; the ordinary frontend neither edits Todos nor exposes manual Run.

While a Todo is `pending_acceptance`, posting a normal Human interaction atomically claims A as `pending_acceptance -> in_progress`, then append-only records `user_interaction.posted` in B. Rejection uses the same resume mechanism after its B-first acceptance event and A-side state projection. Status refresh keeps A `in_progress` while B is still `done` and the latest Human-work event has not yet been followed by `task.started`. The scheduler then resumes B from that event and reuses the previous executor session.

Resume retries are idempotent. Once B is `in_progress` or a later `task.started` exists, no additional resume is dispatched; the ContextCollab executor uses the triggering event ID as the BCS session-chat idempotency key. A delivery failure leaves the claimed Todo `in_progress` and fails closed rather than reopening the interactive state during uncertain delivery.

## Bot execution and artifact reporting contract

Each dispatched or resumed Bot run receives only its dynamic execution context
in the message body: `todo_id`, `todo_execution_id`, the current-generation
`todo_execution_token`, the environment-specific Backend URL, and the current
objective. Runtime metadata repeats stable routing identifiers for observability but deliberately excludes the execution token. Bot behavior must not depend on metadata being propagated by a transport. Fixed
report/upload/complete paths, request formats, ordering, and failure handling
belong to the `todo-runner` Skill and are not duplicated in the runtime message.

Every dispatched or resumed run explicitly loads the `todo-runner` Skill from
the selected Bot's physical runtime engine before doing business work. Current
sources are:

- OpenClaw: `/home/admin/.openclaw/workspace/skills/skills-repo/infra/todo-runner/SKILL.md`
- Claude Code: `/home/admin/.claude_code/workspace/skills/skills-repo/infra/todo-runner/SKILL.md`
- AgentCoding: `/home/admin/.aicoding/workspace/skills/skills-repo/infra/todo-runner/SKILL.md`
- Hermes: `/home/admin/.harness/workspace/skills/skills-repo/infra/todo-runner/SKILL.md`

The Skill stays in the shared `infra` repository:
it is not installed into a Bot Default SkillSet and no link is created at the
`workspace/skills` top level. Direct loading is repeated for every
`[todo-execute]` and `[todo-resume]`, so a missing or unreadable source fails the
run instead of silently executing without the Todo protocol. The Skill derives
its fixed report/upload/complete URLs from `backend_url` and `todo_id` and owns
the stable execution and artifact-reporting procedure.

The identities form one hierarchy rather than three unrelated caller inputs:

```text
Todo / collab_context_id
└── todo_execution_id
    └── todo_execution_token
```

`collab_context_id` is the Todo's B-owned collaboration Context. A
`todo_execution_id` identifies one execution generation within that Context,
and its token authorizes artifact reporting and completion only for that
generation. The Bot therefore reports `todo_execution_id` plus the token; it
does not report or choose `collab_context_id`. The Backend resolves the Context
from the persisted execution and rejects executions that do not belong to the
Todo in the URL.

Issuing a replacement execution invalidates the previous generation. Marking
the Context complete also completes its active execution, so the token cannot
be reused after the run reaches acceptance. The public request field is
`todo_execution_id`; the old `execution_id` spelling is accepted only as a
temporary compatibility alias. A legacy `collab_context_id` request field may
be parsed during that window but is ignored and never used for authorization or
artifact persistence.

After at least one artifact is reported successfully, the Bot calls the
generation-scoped Complete endpoint with `todo_execution_id` and the same
`X-Todo-Execution-Token`. The endpoint appends one idempotent `task.completed`
event for that execution. It intentionally leaves the execution credential and
Todo row unchanged; the existing scheduler observes the resulting Context
`done` state, invalidates the active credential, and projects the Todo from
`in_progress` to `pending_acceptance`.

Artifact persistence and Context event storage are separate stores. The service
therefore persists the artifact first and appends `artifact.reported` only after
that succeeds. The event payload keeps `artifact_id`, `todo_id`,
`todo_execution_id`, and `run_id` as separate fields, plus artifact kind, name,
and concise summary. Retrying the same report reuses the content-deduplicated
artifact row, checks for an existing event with that `artifact_id`, and appends
only a missing event. Event append failure is returned to the caller so a retry
can heal the partial write; failed artifact persistence never creates a false
event.


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
  - TodoCollaborationPort
  - InProcessTodoCollaborationAdapter
consumes:
  - TodoRepositoryProtocol
  - SpaceAccessServiceProtocol
  - BotRepository
  - CachePlugin
  - TodoCollaborationPort
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
