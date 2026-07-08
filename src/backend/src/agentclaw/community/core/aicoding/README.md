# `agentclaw.community.core.aicoding`

AICoding workspace orchestration — provisions per-user code-execution workspaces.

## Context Boundary

```yaml
purpose: "AICoding workspace orchestration — provisions per-user code-execution workspaces; aggregates external workflow catalog for the frontend; resolves bot containers and forwards aixharness ↔ harness-data /data/* traffic."
provides:
  - "WorkspaceService"
  - "WorkflowCatalogService"
  - "DataProxyService"
consumes:
  - "BotService"
  - "DeviceService"
  - "WorkspacePathFactory"
  - "CachePlugin"
  - "AntCodeConfig"
  - "DeviceAccessor"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.workspace
  - agentclaw.community.di.config
  - agentclaw.corp.di.config_corp
  - agentclaw.community.log
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.cache
  - agentclaw.community.plugin_api.sandbox_runtime
  - agentclaw.community.utils.arca_utils
```

### Change impact

Misconfiguration here breaks AICoding bot creation end-to-end. Touches device + bot management, so refactors require cross-domain awareness.
