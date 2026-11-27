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

The Bot team product surface is a compatibility facade over the existing Todo
template model. `TodoBotTeamServiceProtocol` and `TodoBotTeamService` expose
Bot-team names, linear Bot nodes, immutable published revisions, and archive
lifecycle while mapping them to the existing Template/Version/Stage services and
storage. A node that requires approval expands to an adjacent Human stage bound
to the `todo_owner` participant. The public BotTeam facade does not rename or
remove the legacy Template API, add BotTeam-specific tables or columns, or
backfill historical rows. Product-facing `bot_team_id` and `revision_id` map to
the existing `template_id` and `template_version_id` fields; those physical names
are intentionally retained and are not exposed by the BotTeam facade.

`TodoProductCreationServiceProtocol` and `TodoProductCreationService` provide the
shared Bot/BotTeam creation seam. A single-Bot Todo delegates to the legacy
creation service. A BotTeam Todo pins the selected published revision, expands its
stages, and stores the responsible Human as the `todo_owner` participant in the
existing `participant_bindings_json`. Reads project that binding as
`owner_id/owner_name`; historical rows without it fall back to `created_by` in
memory only. There is no owner backfill, naming-driven DDL, or duplicate BotTeam
storage.

`TodoProductProgressServiceProtocol` and `TodoProductProgressService` project the user-facing progress view. The persisted Todo stage snapshot is the product skeleton: it owns stage identity, order, Bot, and acceptance criteria. The Product runtime rollout and its session messages only add runtime facts such as status, retry, blocker, current step, or sub-steps; they do not rename, reorder, or introduce user-visible stages. A runtime stage that cannot be matched to the Todo snapshot or read through the protocol remains explicitly unavailable/unknown rather than being guessed from source text.

`ac_todo_product_interaction` is the local authoritative record for Product-Todo
Human dialogs. Each row is created under the owning Todo row lock and only one
row in `sending`, `delivery_unknown`, or `cancel_requested` may exist per Todo
(single-flight). The local interaction ID is also the Product protocol
`request_id`; it does not use a Context event ID, execution ID, or frontend-supplied
ID. Before calling `interactive()` the service queries the Collet's runtime
metadata and persists `collet_rollout_id` plus `expected_revision`. Protocol
timeouts become `delivery_unknown`, which keeps the send lock; stale revisions
and explicit runtime failures become `failed`. Only a protocol `SHUTDOWN` result
projects the stop target and Todo as cancelled. Stop requests use the deterministic
ID `{interaction_id}:shutdown:v1`, so repeated cancel clicks are idempotent.
Legacy Context Todos continue to use the original interaction event flow and never
write the Product interaction table; Product Todos never post
`user_interaction.posted`.

`TodoNotificationPublisherProtocol` is the Todo-owned notification boundary. Its
adapter maps bounded Todo events to the existing WorkOrder/Inbox infrastructure
and uses `delivery_key` (hashed as the inbox group ID) for idempotency. The
recipient is only the explicit Human recipient or the Todo owner; a missing
recipient is recorded as skipped. Inbox failures do not roll back a persisted Todo
fact. The current implementation observes product runtime facts while serving
Progress, so it relies on the stable delivery key to keep Inbox idempotent. It does
not yet persist a notification-delivery ledger or run a compensation scheduler;
failed/delivery-unknown retries remain documented follow-up work.

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

A and B run in the same process in the production composition root, so the adapter calls `ContextCollabServiceProtocol` directly rather than making an internal HTTP self-call. The creator remains the Human principal while Bot runtime addressing uses the Bot record owner. The default ContextCollab status projection keeps its existing northbound field set; the Todo in-process adapter obtains payloads through the authorized L4 `get_events` API for its richer interaction projection, not a status-query payload flag. ContextCollab execution is a Task-core executor, not a deployment Plugin. New Todo runs reuse Task's existing `OpenApiBotPort` and owner-granted BaaS `allowed-bots` authorization without modifying `TaskExecutor` or introducing a shared Task/Todo abstraction. Optional BCS ports are retained only to resume and inspect historical group-backed Todo Contexts.

`TodoContextRuntime` owns run/resume/refresh orchestration in the Todo consumer.
It reads the independent L4 Task and L6 creation facts, then records runtime
handles through canonical L4 event append and version fences. Combined creation
uses L6 `create_collaboration`; `executor_owner_id` is persisted as optional L6
runtime-addressing metadata, not as Task accountability or a resource Grant.
The legacy `team_todo_execution` machine treats `done` as a completed execution
round that Human feedback may resume; Todo acceptance remains product-owned.

`TodoCollaborationPort` is an internal Task/Todo consumer boundary, not a globally replaceable Plugin API. `InProcessTodoCollaborationAdapter` is the production composition and delegates to the same-process `ContextCollabServiceProtocol`; it is fixed by `TaskTodoBindings` rather than selected by deployment Profile. The deterministic fake lives under Todo tests and is not shipped or registered as a local plugin.

The Todo Events endpoint stays compact and event-centric: it returns normalized event metadata and concise primary content, but not the complete event payload. Every successfully persisted product artifact appends one durable `artifact.reported` event, so this view can say what was produced in chronological order. Text uses a whitespace-normalized 20-character preview; file, link, and code events use `artifact_name` as the concise display title. File rows fall back to the sanitized file name when a legacy caller omits `artifact_name`. `task.completed` remains a separate run-completion notice and never substitutes for the artifact events.

The Interactions endpoint is actor/content-centric and richer: it includes the Human objective in `task.created`, Human messages and rejection instructions, selected rejection floors from the full payload, and `artifact.reported` payloads. The client resolves each artifact event by its stable `artifact_id`, displays complete text and complete file/link/code details at the event's exact position, and uses the reporting Bot as the actor. The Artifacts endpoint remains the complete persisted inventory and content source.

An interaction may persist `reply_to_event_id` and structured `mentions` in the
same `user_interaction.posted` event payload. The reply ID must identify a
replyable event already visible in the same Context; the UI floor is derived
presentation metadata and is never accepted as identity. Mentions currently
support Bot actors only, and each mentioned Bot must satisfy the same live
Bot/Space eligibility policy as Todo creation. Arbitrary `@text` has no
authority without the structured Bot ID. Persisting a mention does not wake the
mentioned Bot, create another run, or change orchestration: the existing Human
interaction transition and scheduler resume continue to target the Todo's
original executor. No interaction table or schema migration is introduced.

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

## Imported source identity

Imported Todos keep product-owned source metadata in
`ac_todo_source_binding`; manually created Todos have no source binding. Dima
imports use the stable work-item identity rather than editable title or
description content. Within one `avernet_tenant`, runtime `env`, TeamClaw
`space_id`, `source_type`, and `source_item_id`, only one Todo may exist. The
Dima project ID is retained as `source_container_id` metadata but does not
change that identity, so moving a work item between Dima projects cannot create
a duplicate Todo in the same TeamClaw Space. The same source item may be
imported independently into another TeamClaw Space.

`source_metadata_json` stores a bounded, whitelisted JSON snapshot of useful
Dima fields such as `processor_id` and `processor_name`. It is descriptive
source metadata only: it does not participate in duplicate identity, bind the
processor to a Todo template participant, select an Action notification target,
or update automatically after import.

Todo and source binding are inserted in one database transaction. A friendly
pre-check returns the existing Todo ID for ordinary retries, while the database
unique constraint closes concurrent-import races; an import conflict returns
`409` with `data.existing_todo_id`. This binding is an import snapshot only. It
does not introduce Dima status write-back.

Automatic Dima import is configuration and progress persistence, not an
in-process scheduler. Backend stores Human-owned rules, one monotonic
`cursor_updated_at`, and an idempotent checkpoint for each
`(rule, container, item, source_updated_at)` source version. Rule creation, owner
listing, and updates remain Human owner-scoped. Execution is tenant-scoped: any
authenticated caller, including an application-only Bot caller, may list enabled
rules, read rule detail and progress, create a Todo through a rule, and write a
checkpoint. `created_by` is audit and imported-Todo ownership, not an execution
permission. Rule-bound Todo creation takes `space_id`, `bot_id`, and `created_by`
only from the rule; the caller cannot override them. An external Agent scans every
configured Dima container, interprets the natural-language filter, creates
matching Todos, and writes one checkpoint per processed item. It should query Dima with
`source_updated_at >= cursor_updated_at`, use persisted progress to de-duplicate
equal timestamps, and advance the rule cursor only after the full multi-container
scan window is complete. Backend does not call Dima or run a periodic import job
for these rules.

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
purpose: Own persisted Todo lifecycle, imported-source identity, automatic Context dispatch, runtime refresh, interactions, Product dialogs, notifications, and acceptance.
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
  - TodoSourceServiceProtocol
  - TodoSourceService
  - TodoSourceInput
  - TodoAutoImportServiceProtocol
  - TodoAutoImportService
  - TodoBotTeamServiceProtocol
  - TodoBotTeamService
  - TodoProductCreationServiceProtocol
  - TodoProductCreationService
  - TodoProductProgressServiceProtocol
  - TodoProductProgressService
  - TodoProductInteractionServiceProtocol
  - TodoProductInteractionService
  - TodoNotificationPublisherProtocol
  - TodoNotificationPublisher
  - TodoExecutorSelection
  - TodoOwnerInput
  - BotTeamRecord
  - BotTeamRevisionRecord
  - TodoAutoImportRuleRecord
  - TodoAutoImportProgressRecord
  - InProcessTodoCollaborationAdapter
consumes:
  - TodoRepositoryProtocol
  - TodoAutoImportRepositoryProtocol
  - TodoProductInteractionRepositoryProtocol
  - ProductProfileServiceProtocol
  - WorkOrderRepositoryProtocol
  - SpaceAccessServiceProtocol
  - BotRepository
  - CachePlugin
  - TodoCollaborationPort
  - TodoArtifactServiceProtocol
  - TodoSourceClientProtocol
  - MCPAuthPlugin
  - MCPCenterPlugin
  - HttpClient
internal_dependencies:
  - agentclaw.community.core.errors
  - agentclaw.community.core.repository.protocols
  - agentclaw.community.core.spaces
  - agentclaw.community.core.task.context_collab
  - agentclaw.community.core.task.todos
  - agentclaw.community.di.todo_config
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.plugin_api
```

### Change impact

Changes affect Todo create/read, imported-source identity and duplicate
prevention, automatic scheduling, the compensation Run endpoint, event and
interaction projection, Product Human dialogs, notification delivery, and
acceptance. Repository state
transitions are compare-and-set operations so duplicate schedulers or operator
retries cannot move a Todo backwards. Context creation and Todo persistence are
not one cross-store transaction; dispatch therefore resumes from an already
bound Context when retrying. A does not own the legacy Context interaction table: those Human interaction
events and executor progress remain in B. Product Collet Todos do own the local
`ac_todo_product_interaction` dialog facts because they are Product-boundary
human actions and must remain readable when the external runtime changes; legacy
product state and generic runtime attempt states remain in B.
