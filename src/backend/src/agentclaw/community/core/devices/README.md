# `agentclaw.community.core.devices`

Device / engine domain — engine binding, health probing, readiness, sandbox provisioning, OSS↔NAS migration.

## Context Boundary

```yaml
purpose: "Device / engine domain — engine binding, health probing, readiness, sandbox provisioning, OSS↔NAS migration."
provides:
  - "DeviceService"
  - "DeviceBindingRepository protocol"
  - "Engine health & readiness services"
  - "Device error types"
  - "Device event types"
consumes:
  - "BotManagement repo + service"
  - "EventBus"
  - "StoragePath"
  - "SystemConfig"
  - "BaasService"
  - "PassportPlugin"
  - "DeviceAccessor (models only)"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.config
  - agentclaw.community.core.common_config
  - agentclaw.community.core.config_compose    # teclaw_paths — namespaces/mappers used by the device-fs dispatcher (B6)
  - agentclaw.community.core.events
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.services    # {identity,resource,config}_addressing mapper builders for dispatch_addressed (B6)
  - agentclaw.community.core.storage
  - agentclaw.community.core.system_config
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.utils
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.corp.di    # corp-only device services (ARCA) — relocate to corp/ in B11 T3.4
  - agentclaw.community.kernel.lifecycle    # BaaS publish task lifecycle registers task_queue handlers
  - agentclaw.community.kernel.device_dto    # neutral CommandResult / ResourceSpecification / OutBound DTOs (B6)
  - agentclaw.community.log
  - agentclaw.community.core.devices.services.device_filesystem    # DeviceFileSystem — return type of the core device-fs dispatcher (B6)
  - agentclaw.community.plugin_api.device_sync    # DeviceSyncPlugin — return type of the core device-sync dispatcher (B6)
  - agentclaw.community.plugin_api.devices    # DeviceAccessor — injected into DeviceFilesystemDispatcher (B6)
  - agentclaw.community.plugin_api.drm    # DRM dynamic-config reader plugin (B6)
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.outbound_rules    # OutboundRuleProvider (kernel rule) injected into ArcaDeviceService (B6)
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.sandbox_runtime    # SandboxRuntimeClient — ARCA SDK I/O seam (B6)
  - agentclaw.community.plugin_api.secret_resolver    # build_outbound_operation_rule 接 SecretResolver, 收口 Mist→layotto 硬依赖
  - agentclaw.community.utils
  - agentclaw.community.utils.env_utils
```

### Change impact

Owns the engine/device binding lifecycle. Schema changes (binding table, engine config dir) require migration. Health-probe contract is consumed by Prom-style monitoring outside the repo.
