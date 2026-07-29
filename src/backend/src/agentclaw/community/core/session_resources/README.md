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
  - TokenVault
internal_dependencies:
  - agentclaw.community.api
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.session_resources
  - agentclaw.community.core.task_queue
  - agentclaw.community.log
  - agentclaw.community.plugin_api
```

### Change impact

Changes affect file-upload control-plane APIs, BaaS transfer calls, Engine
materialization dispatch, and the point at which Chat resources become ready.
Every state transition must preserve owner, Bot, and session isolation.
