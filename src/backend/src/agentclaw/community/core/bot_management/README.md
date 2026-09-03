# `agentclaw.community.core.bot_management`

Bot lifecycle, creation policy, repository, engine resolution, render-screen models, and skill-set switching.

## Context Boundary

```yaml
purpose: "Bot lifecycle, creation policy, repository, engine resolution, render-screen models, and skill-set switching."
provides:
  - "BotService"
  - "BotCreateContext"
  - "BotCreateDeploymentMode"
  - "PreparedBotCreate"
  - "BotRepository protocol + impl"
  - "EngineResolver"
  - "DataInitService"
  - "RenderScreenService"
  - "TeclawProvisionService"
  - "TeclawPublishTaskLifecycle"
  - "CreateBotForOthersService"
  - "DefaultBotPassportRepairService"
  - "BotQuotaService and BotQuotaScope"
consumes:
  - "DeviceAccessor"
  - "PassportPlugin"
  - "CallerIdentityRepositoryProtocol"
  - "AuthRelationshipPlugin"
  - "DeviceService"
  - "ResourceService"
  - "BotPublishService"
  - "SkillCenter factories"
  - "PolicyService"
  - "TaskQueueService"
  - "HandlerRegistry"
  - "CommonConfigService"
  - "BotSpaceAccessProtocol (implemented by the Spaces context)"
  - "SpaceAccessServiceProtocol"
  - "CachePlugin quota lock"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.identity    # MCP execution identity carried into the restart Passport refresh
  - agentclaw.community.core.repository.protocols.platform    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skill_center    # repository contracts consumed by this module
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_app_grant.protocols    # sweep contract: deletion withdraws the bot's app authorizations
  - agentclaw.community.core.bot_startup_script.protocols    # sweep contract: deletion removes the bot's stored startup script
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.config
  - agentclaw.community.core.config_compose
  - agentclaw.community.core.common_config
  - agentclaw.community.core.cron.services.aicoding.cron_auto_setup
  - agentclaw.community.core.desktop_bot
  - agentclaw.community.core.mcp
  - agentclaw.community.core.devices
  - agentclaw.community.core.events
  - agentclaw.community.core.resources
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.bot_management.bot_space    # narrow cross-context Space membership contract
  - agentclaw.community.core.spaces.errors    # typed Space membership failures propagated by Bot Space assignment
  - agentclaw.community.core.spaces.models    # SpaceRecord/SpaceType used by Bot Space assignment
  - agentclaw.community.core.spaces.protocols    # Space lookup used by quota configuration
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.di.modules
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.drm
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.secret_resolver
  - agentclaw.community.plugin_api.auth_relationship
  - agentclaw.community.plugin_api.cache
  - agentclaw.community.utils
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
  - agentclaw.community.utils.secret_utils
  - agentclaw.community.core.access.policy_service_protocol  # Service API Protocol consumed by this module
```

### Change impact

Highest-fanout domain — 12+ other core domains import it. BotService signature changes ripple widely. Repository protocol changes break the corresponding plugin_impl repos.
