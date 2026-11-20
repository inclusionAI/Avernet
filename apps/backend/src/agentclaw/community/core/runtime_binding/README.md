# `agentclaw.community.core.runtime_binding`

Read-only resolution of a trusted Bot request to an existing `binding_id`.

## Context Boundary

```yaml
purpose: "Resolve Bot, owner, actor, stage, and an explicit runtime target to one existing binding id."
provides:
  - RuntimeBindingRequest
  - RuntimeBindingSource
  - RuntimeBindingTarget
  - ResolvedRuntimeBinding
  - RuntimeBindingResolutionService
consumes:
  - BotRepository
  - BotPublishRepositoryProtocol
  - DeviceBindingRepository
  - ExpertChatInstanceRepository
internal_dependencies:
  - agentclaw.community.log
  - agentclaw.community.core.engine_runtime.stage
  - agentclaw.community.core.bot_management.engines
```

`RuntimeBindingTarget.CALLER_SERVICE` explicitly resolves a service Bot's draft,
verify, or online runtime even when the Bot also has a caller instance.
`RuntimeBindingTarget.CALLER_INSTANCE` resolves the authenticated user's active
Expert Chat instance. `AUTO` selects service stage bindings for Teclaw, including
Bots with call_type=caller; other Caller service Bots select the authenticated
user's independent instance. Explicit `CALLER_INSTANCE` selection is unchanged.

It does not select a device, retain session affinity, mutate state, or call the
Session Resource state machine. The OpenAPI adapter resolves once for upload
creation and passes the resulting `binding_id` to that existing state machine.

`resolve_session_file(request, session_user_id=...)` is the file-specific entry
point. Without an authorized friend subject, service Bots use the requested
stage even when they have `call_type=caller`. With a subject, the provisioning
strategy decides whether Caller chat uses an independent instance or a shared
stage runtime (including Teclaw). Independent instances are looked up for the
subject, not the reading actor, and unavailable instances never fall back to a
service binding. Personal Bots retain their draft binding. This does not change
the general `resolve()` / `AUTO` contract used by other consumers.
