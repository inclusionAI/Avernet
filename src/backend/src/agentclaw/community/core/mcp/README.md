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
  # Request-agnostic config flow extracted for both API surfaces (internal
  # /api/mcp and public /openapi/v1/bots/mcp).
  - "UnifiedConfig"
  - "read_unified_config"
  - "write_unified_config"
  - "list_marketplace_servers"
  - "list_marketplace_tenants"
  # Presentation helpers shared by both surfaces.
  - "mask_api_key"
  - "strip_ext_info"
  - "strip_ext_info_from_list"
  - "is_network_type_visible"
  - "normalize_network_types"
  - "primary_transport_protocol"
  - "ALLOWED_NETWORK_TYPES"
  # Dependency-free domain errors each surface maps.
  - "McpError"
  - "McpServerNotFoundError"
  - "McpHeadersInvalidError"
  - "McpConfigValueError"
  - "McpSyncFailedError"
  - "McpMarketUnavailableError"
consumes:
  - "BotRepository"
  - "DeviceMCPSyncPlugin"
  - "DeviceAccessor"
  - "MCPAuthPlugin"
  - "MCPCenterPlugin"
  - "PassportPlugin"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
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
