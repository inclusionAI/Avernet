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
  - RuntimeContainerLocator
  - RuntimeContainerRequest
  - ResolvedRuntimeContainers
consumes:
  - BotRepository
  - BotPublishRepositoryProtocol
  - DeviceBindingRepository
  - ExpertChatInstanceRepository
internal_dependencies:
  - agentclaw.community.log
  - agentclaw.community.core.engine_runtime.stage
  - agentclaw.community.core.devices
  - agentclaw.community.core.bot_management.engines
  - agentclaw.community.core.repository
  - agentclaw.community.core.runtime_binding
  - agentclaw.community.core.service_bot
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

`RuntimeContainerLocator` resolves an already authorized `bot_id/entity_id` to
the canonical owner and an existing binding. Personal Bots use their draft
binding; service Bots require a stage and use its service binding when
`f_user_id` is absent. Friend chats on Caller Bots use the existing Expert Chat
instance only when the registered engine strategy requires an independent
instance. Shared engines retain their stage binding. This service does not
authenticate the friend selector or authorize credential writes.

`resolve_binding` performs no physical-device query. `resolve_targets` returns
all ACTIVE devices from the current ACTIVE BaaS Bot record; an explicit UUID
must match exactly once. `resolve` combines both operations. Results retain the
provider so callers can distinguish Teclaw from ARCA. Consumers requiring one
target must reject ambiguity; enumeration does not authorize broadcasting
credentials. IAM/JIT selects friend versus service runtimes with `f_user_id`
and no longer consumes `caller_runtime_target`; unrelated session APIs retain
their explicit selector. Caller refresh and JIT failures remain isolated.
