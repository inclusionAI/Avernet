# Todo workbench

This context owns the user-facing Todo product lifecycle, Space/Bot eligibility,
automatic dispatch, template stages, and the public event/interaction view. Todo
state is persisted by A as the six product states `not_started`, `in_progress`,
`interrupted`, `pending_action`, `completed`, or `cancelled`; it is not
recomputed during reads from B. The compatibility names `pending` and
`pending_acceptance` map to `not_started` and `pending_action` respectively;
they are not additional product states.

The unfiltered workbench list follows the visible group order
`not_started`, `in_progress`, `pending_action`, `interrupted`, `completed`, then
`cancelled`, and remains oldest-first inside each group. A status-filtered list
is oldest-first within that state. `TodoScheduler` scans `not_started` and
`in_progress` rows for dispatch/refresh while preserving the product state
projection. Template Todos with `pending_action` are never sent through the
legacy acceptance-reconciliation path.

A template Todo is pinned to an immutable published template version. Its
ordered stage instances are the product workflow:

- A Bot stage runs in `in_progress`; the Bot reports its artifacts through
  `/report` and signals execution completion through `/complete`. The complete
  request may carry a human-readable `summary` and Skill-defined structured
  `result`.
- `/complete` advances the current stage only after its configured hard artifact
  gates pass. A next Bot stage keeps the Todo in `in_progress`; a next Human
  stage enters `pending_action` with `planned_human_stage`; the last completed
  stage enters `completed`. Stage advancement is an internal Backend projection
  from the execution binding, not a separate Bot-facing stage outcome API.
- Runtime or domain logic may project a blocked condition into `pending_action`
  with `blocked`, preserving the reason and requested action. It does not
  advance the stage.
- `failed` is an execution-attempt result, not a Todo product state. Runtime
  recovery may retry it while the Todo remains `in_progress`; after recovery is
  exhausted, the runtime/domain integration may project `interrupted`, which is
  explicitly resumable.
- A Human stage is completed only by its bound Human or a Space administrator.
  A planned Human stage cannot be bypassed by the generic resume operation.
- Explicit cancel enters `cancelled`; completed and cancelled Todos cannot be
  resumed.

`not_started` is only the never-dispatched initial state. Once the first stage
has started, a Bot-to-Bot transition never regresses the Todo to `not_started`.
The detailed execution policy—retry count, Bot replacement, knowledge lookup,
Skill behavior, and notification delivery—is runtime/platform behavior rather
than template configuration. The template only declares stages, purpose,
participants, responsible owner, and deterministic hard artifact gates.

The legacy non-template Todo API remains compatible for existing clients. Its
`pending_acceptance` flow is not the template lifecycle: new product flows
should model human review as an explicit Human stage rather than relying on an
implicit acceptance step. Legacy acceptance events and routes remain documented
below only for compatibility.

A and B run in the same process in the production composition root, so the adapter calls `ContextCollabServiceProtocol` directly rather than making an internal HTTP self-call. The creator remains the Human principal while Bot runtime addressing uses the Bot record owner. The default ContextCollab status projection keeps its existing northbound field set; the Todo in-process adapter explicitly requests event payloads for its richer interaction projection. ContextCollab execution is a Task-core executor, not a deployment Plugin: it directly reuses Task's `BcsClientPort`, `BcsCreateGroupRequest`, `BcsBotTokenProvider`, and existing BCS DI adapter without modifying `TaskExecutor` or introducing a shared Task/Todo abstraction.

`TodoCollaborationPort` is an internal Task/Todo consumer boundary, not a globally replaceable Plugin API. `InProcessTodoCollaborationAdapter` is the production composition and delegates to the same-process `ContextCollabServiceProtocol`; it is fixed by `TaskTodoBindings` rather than selected by deployment Profile. The deterministic fake lives under Todo tests and is not shipped or registered as a local plugin.

The Todo Events endpoint stays compact and event-centric: it returns normalized event metadata and concise primary content, but not the complete event payload. Every successfully persisted product artifact appends one durable `artifact.reported` event, so this view can say what was produced in chronological order. Text uses a whitespace-normalized 20-character preview; file, link, and code events use `artifact_name` as the concise display title. File rows fall back to the sanitized file name when a legacy caller omits `artifact_name`. `task.completed` remains a separate run-completion notice and never substitutes for the artifact events.

The Interactions endpoint is actor/content-centric and richer: it includes the Human objective in `task.created`, Human messages and rejection instructions, selected rejection floors from the full payload, and `artifact.reported` payloads. The client resolves each artifact event by its stable `artifact_id`, displays complete text and complete file/link/code details at the event's exact position, and uses the reporting Bot as the actor. The Artifacts endpoint remains the complete persisted inventory and content source.

Todo delivery uses the same `singlebot_2_group` shape as Task: BCS creates one
real `manager_worker` group whose only Bot participant is the selected executor
with role `manager`; the Todo creator joins as a non-speaking Human `observer`.
The selected Bot is both `driver_bot` and `master_bot`. No second Bot and no
`default` Bot is added. Because this is Bot collaboration, an executor is
eligible only when the authoritative BCS agent visibility is exactly `public`;
legacy Human-facing `ac_bots.public` does not grant eligibility. The authoritative
catalog lookup uses the exact BCS Bot UUID as an explicit Bot viewer, so the
agent-facing `visibility` rule is evaluated instead of the Human-facing
`user_visibility` rule. `protected`, `private`, missing or unknown visibility,
and unavailable catalog metadata all fail closed. The rule is checked when
creating a Todo, before its initial dispatch, and before resuming a Human
interaction or rejection. Historical Todos remain readable if their Bot later
becomes non-public, but no new or resumed run is delivered until that exact
Bot identity is public again. Final delivery uses `inject_observers`, and later Human interaction resumes the same
BCS group session rather than creating another group. The public `/run`
operation is a compensation/operator entry point; the ordinary frontend
neither edits Todos nor exposes manual Run.

While a Todo is `pending_acceptance`, posting a normal Human interaction atomically claims A as `pending_acceptance -> in_progress`, then append-only records `user_interaction.posted` in B. Rejection uses the same resume mechanism after its B-first acceptance event and A-side state projection. Status refresh keeps A `in_progress` while B is still `done` and the latest Human-work event has not yet been followed by `task.started`. The scheduler then resumes B from that event and reuses the previous executor session.

Resume retries are idempotent. Once B is `in_progress` or a later `task.started` exists, no additional resume is dispatched; the ContextCollab executor uses the triggering event ID as the BCS session-chat idempotency key. A delivery failure leaves the claimed Todo `in_progress` and fails closed rather than reopening the interactive state during uncertain delivery.

## Bot execution and artifact reporting contract

Each dispatched or resumed Bot run receives only its dynamic execution context
in the message body: `todo_id`, `todo_execution_id`, the current-generation
`todo_execution_token`, the environment-specific Backend URL, and the current
objective. Runtime metadata may repeat these values for observability, but Bot
behavior must not depend on metadata being propagated by a transport. Fixed
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
event for that execution and may persist the caller's `summary` and structured
`result` as completion content. It intentionally leaves the execution credential
and Todo row unchanged; the existing scheduler observes the resulting Context
`done` state, invalidates the active credential, and projects the Todo from
`in_progress` to `pending_acceptance` for legacy Todos. For template Todos, the
Backend uses the execution's stage binding to advance the current stage after the
completion event is durable. There is no separate Bot-facing stage outcome
endpoint.

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
  - BotCatalogMetadataServiceProtocol
  - CachePlugin
  - TodoCollaborationPort
  - TodoArtifactServiceProtocol
internal_dependencies:
  - agentclaw.community.core.bot_public.catalog_metadata
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
and the six product states remain in A; runtime attempt states remain in B.
