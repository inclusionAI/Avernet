# `agentclaw.community.core.mcp`

MCP (Model Context Protocol) domain — config, auth, and sync for MCP servers bound to bots/devices.

## Context Boundary

```yaml
purpose: "MCP (Model Context Protocol) domain — config, auth, and sync for MCP servers bound to bots/devices."
provides:
  - "MCPConfigService"
  - "MCPAuthService"
  - "MCPSyncService"
  - "MCPMarketService"
consumes:
  - "BotRepository"
  - "DeviceMCPSyncPlugin"
  - "DeviceAccessor"
  - "MCPAuthPlugin"
  - "MCPCenterPlugin"
  - "PassportPlugin"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.config
  - agentclaw.community.core.devices
  - agentclaw.community.core.workspace
  - agentclaw.community.di.modules
  - agentclaw.community.log
  - agentclaw.community.plugin_api.device_mcp_sync
  - agentclaw.community.plugin_api.device_sync
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.mcp_auth
  - agentclaw.community.plugin_api.mcp_center
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.utils.env_utils
```

### Change impact

MCP config changes propagate to running engines via the sync plugins; misconfiguration here yields broken tool access on bots without a clear error.
