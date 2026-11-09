# Session Resources

## Context Boundary

```yaml
purpose: Own session-scoped uploaded-resource state, authorization, and materialization transitions.
provides:
  - SessionResourceService
  - SessionResourceRecord
  - SessionResourceRepositoryProtocol
consumes:
  - DatabasePlugin
  - HttpClient
  - TaskQueueService
  - DeviceContextResolver
  - DeviceAdapterTransport
  - RuntimeBindingResolutionService
  - BotRepository
  - TokenVault
internal_dependencies:
  - agentclaw.community.api
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.session_resources
  - agentclaw.community.core.runtime_binding
  - agentclaw.community.core.engine_runtime
  - agentclaw.community.core.resources
  - agentclaw.community.core.repository.protocols.bot
  - agentclaw.community.core.task_queue
  - agentclaw.community.log
  - agentclaw.community.plugin_api
```

### Change impact

Changes affect file-upload control-plane APIs, BaaS transfer calls, Engine
materialization dispatch, and the point at which Chat resources become ready.
Every state transition must preserve owner, Bot, and session isolation.

EngineSessionFilesService relays OpenAPI ready-file list/content/delete directly
to the resolved runtime's manifest, including files published by local Chat tools.
It does not create or require upload-control-plane records. Upload/status APIs
and their callback state machine remain owned by SessionResourceService.
