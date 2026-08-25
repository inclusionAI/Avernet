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
  - agentclaw.community.core.engine_runtime.stage
```

`RuntimeBindingTarget.CALLER_SERVICE` explicitly resolves a service Bot's draft,
verify, or online runtime even when the Bot also has a caller instance.
`RuntimeBindingTarget.CALLER_INSTANCE` resolves the authenticated user's active
Expert Chat instance. `AUTO` preserves the existing Session Files behavior.

It does not select a device, retain session affinity, mutate state, or call the
Session Resource state machine. The OpenAPI adapter resolves once for upload
creation and passes the resulting `binding_id` to that existing state machine.
