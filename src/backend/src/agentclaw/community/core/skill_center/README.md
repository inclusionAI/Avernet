# `agentclaw.community.core.skill_center`

Skill Center domain — skill set switching, market sync, repository sync, skill auth, propagation logging.

## Context Boundary

```yaml
purpose: "Skill Center domain — skill set switching, market sync, repository sync, skill auth, propagation logging."
provides:
  - "SkillSetService"
  - "SkillSetActivator"
  - "SkillSetSwitcher"
  - "MarketSyncService"
  - "GitSyncService"
  - "SkillAuthService"
  - "CurrentRuntimeLayoutProbeService"
  - "LocalSkillQueryService"
  - "LocalSkillUploadService"
  - "LocalSkillStateService"
  - "LocalSkillCleanupWorkModel"
consumes:
  - "BotRepository"
  - "BotCollabLogRepositoryProtocol"
  - "CollaboratorServiceProtocol"
  - "Events"
  - "core/mcp services"
  - "CachePlugin"
  - "DeviceAccessor"
  - "DeviceContextResolver"
  - "DeviceAdapterTransport"
  - "MCPCenterPlugin"
  - "ObjectStoragePlugin"
  - "SecretResolver"
  - "SkillCenterClient"
  - "SkillRepoSyncPlugin"
  - "WorkspacePathFactory"
  - "LocalSkillCleanupRepository"
internal_dependencies:
  - agentclaw.community.core.access
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.config
  - agentclaw.community.core.config_compose
  - agentclaw.community.core.devices
  - agentclaw.community.core.events
  - agentclaw.community.core.mcp
  - agentclaw.community.core.models
  - agentclaw.community.core.skills_pool
  - agentclaw.community.core.workspace
  - agentclaw.community.di.modules
  - agentclaw.community.di.runtime_mode
  - agentclaw.community.kernel
  - agentclaw.community.log
  - agentclaw.community.plugin_api.cache
  - agentclaw.community.plugin_api.local_skill_cleanup
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.mcp_center
  - agentclaw.community.plugin_api.object_storage
  - agentclaw.community.plugin_api.secret_resolver
  - agentclaw.community.plugin_api.skill_center_client
  - agentclaw.community.plugin_api.skill_repo_sync
  - agentclaw.community.plugin_api.skill_scanner
  - agentclaw.community.utils
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
```

### Change impact

Skill-set switching is the highest-throughput flow in production. Changes here can break every chat session in flight. Coordinate with the propagation log schema before changing repository protocols.

Local Skill replacement stages a complete package before switching the existing
Skill metadata.  Failed post-switch obsolete-byte deletion is recorded through
`LocalSkillCleanupRepository` in the exact deployment-wide Bot scope
`(env, owner_id, bot_id)`.  The next serialized Local Skill mutation retries
pending work; it marks successful work `cleaned` and retains failures with an
attempt count and a stable operator-safe error.  A task that follows a failed
Active rollback retains its staged bytes and restores the old runtime mapping
before it attempts byte cleanup.  Apply
`sql/2026_08_04_local_skill_cleanup_work.sql` before deploying this behavior.
