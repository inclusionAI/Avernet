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
  - "LocalSkillDeleteService"
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
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skill_center    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skills_pool    # Skills Pool repository contracts consumed by this module
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

Local Skill replacement stages and verifies a complete package in a hidden
implementation directory, then publishes it to the stable layout-owned
``skills-local/<skill-name>`` locator before switching existing Skill metadata.
For a Skills Pool layout, ``skills-local`` is the Pool local root resolved by
``SkillServiceFactory``. Hidden ``.replacement-*`` and ``.rollback-*``
directories are never database authority and are removed synchronously before
the request succeeds. A failed replacement restores the old package and
metadata before returning an error; no cleanup state is persisted in the
database.

Public Local Skill deletion copies and verifies package bytes in a unique
Bot-scoped quarantine. Its one transaction rechecks active
custom SkillSet references, removes the default-set exclusion, all SkillSet
associations, and the Skill row. Bot-scoped SkillSet activation takes the same edit lease, so it
cannot publish a stale association while deletion is in flight. If the
transaction fails, the package is restored from quarantine before the request
fails. A post-commit purge failure returns an error and never recreates the
deleted Skill.
