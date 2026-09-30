# `agentclaw.community.core.mcp`

MCP (Model Context Protocol) domain — config, auth, and sync for MCP servers bound to bots/devices.

## Context Boundary

```yaml
purpose: "MCP (Model Context Protocol) domain — config, auth, and sync for MCP servers bound to bots/devices."
provides:
  - "EffectiveMCPStateReaderProtocol"
  - "MCPConfigService"
  - "MCPScopedConfigService"
  - "MCPAuthService"
  - "MCPSyncService"
  - "MCPMarketService"
  # Request-agnostic config flow extracted for both API surfaces (internal
  # /api/mcp and public /openapi/v1/bots/mcp).
  - "UnifiedConfig"
  - "read_unified_config"
  - "write_unified_config"
  - "HeaderGroup"
  - "ScopedMCPConfig"
  - "read_scoped_config"
  - "write_scoped_config"
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
  - "select_mcp_endpoint"
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
  - "SecretResolver"
  - "PassportPlugin"
  - "CallerIdentityRepositoryProtocol"
internal_dependencies:
  - agentclaw.community.core.digital_employee.contracts
  - agentclaw.community.core.bot_config_surface    # BotConfigCoords, the shared config-category address type
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.identity # Caller identity overrides used for Passport scope sync
  - agentclaw.community.core.default_capabilities
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.config
  - agentclaw.community.core.devices
  - agentclaw.community.core.workspace
  - agentclaw.community.di.modules
  - agentclaw.community.log
  - agentclaw.community.plugin_api.device_mcp_sync
  - agentclaw.community.plugin_api.device_sync_dispatcher
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.mcp_auth
  - agentclaw.community.plugin_api.mcp_center
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.secret_resolver
  - agentclaw.community.di.config
  - agentclaw.community.utils.env_utils
```

### Change impact

MCP config changes resolve per Bot: explicit `ac_bot_mcp_config` fields override
the owner's `ac_user_mcp_config`, then Center/default values fill the remainder.
For normal Center endpoints, Header maps merge by case-insensitive name: the
Bot's explicitly named values win while other user-default names remain
inherited. A Bot `headers: {}` no longer blocks user Headers. A product
config-groups save atomically replaces user/Bot Header maps without changing
installation or unrelated connection fields; projection after commit is
best-effort. See ADR 0015 for the deliberate compatibility change.
They propagate to running engines through `DeviceSync`; the same resolver is used
for restart/whole-artifact composition. Contract changes therefore affect Manifest
apply, user-config fan-out, every DeviceSync implementation, and OCB's ARCA adapter.

User-default fan-out is best-effort: the persistent `ac_user_mcp_config` row is
the desired state, and only Bots whose effective MCP state contains the changed
server are projected. Unrelated Bots receive neither a device probe nor an
outcome. A selected Bot's resolution, probe, dispatch, or delivery failure is
returned in `sync_results` and does not roll the row back; `sync_summary`
reports the affected/synced/offline/failed counts. When a selected Bot lacks the
MCP at runtime, the result is `RUNTIME_DRIFT` and the Bot is fully reconciled.

A custom Bot URL does not inherit static user/default/managed credentials in the
server entry. Container-wide mcporter `headerPolicies` remain host-matched runtime
policy, however; this core module neither emits nor disables them per server.
