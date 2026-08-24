# `agentclaw.community.core.runtime_binding`

Read-only resolution of a trusted Bot request to an existing `binding_id`.

## Context Boundary

```yaml
purpose: "Resolve Bot, owner, actor, and stage to one existing binding id."
provides:
  - RuntimeBindingRequest
  - RuntimeBindingSource
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

It does not select a device, retain session affinity, mutate state, or call the
Session Resource state machine. The OpenAPI adapter resolves once for upload
creation and passes the resulting `binding_id` to that existing state machine.
