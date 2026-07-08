# `agentclaw.community.core.service_bot`

Service-bot domain — bot publication facade, BAAS service binding, sub-resource management.

## Context Boundary

```yaml
purpose: "Service-bot domain — bot publication facade, BAAS service binding, sub-resource management."
provides:
  - "BotPublishService"
  - "BaasService"
  - "ServiceBot SQLAlchemy models"
consumes:
  - "BotManagement"
  - "DeviceService + repo"
  - "SystemConfig"
  - "PassportPlugin"
internal_dependencies:
  - agentclaw.community.core.common_config
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.channel    # per-stage engine_overrides (DingTalk channels) reader at verify/online promotion
  - agentclaw.community.core.config_compose
  - agentclaw.community.core.devices
  - agentclaw.community.core.quality.services
  - agentclaw.community.core.system_config
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.kernel.bot_config
  - agentclaw.community.kernel.device_dto    # neutral OutBound / ResourceSpecification DTOs (B6)
  - agentclaw.community.log
  - agentclaw.community.plugin_api.approval_workflow           # antprocess approval workflow for publish approval
  - agentclaw.community.core.devices.services.device_filesystem    # teclaw build-time file promotion (TeclawFilePromotion)
  - agentclaw.community.plugin_api.engine_ext_client
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.object_storage           # teclaw promotion stages files to OSS
  - agentclaw.community.plugin_api.outbound_rules           # BaasService delegates egress-rule construction (B6)
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.secret_resolver       # BaasService 透传给 outbound rule 构造,singlebox/prod 双实现
  - agentclaw.community.plugins.prod.baas_service    # Back-compat ``BaasService`` alias (PEP 562 lazy import); see arch test exception
  - agentclaw.community.api.publish_approval        # API Protocol for publish approval service
  - agentclaw.community.utils
  - agentclaw.community.utils.env_utils
```

### Change impact

Publication path is the user-visible go-live for bots — bugs here block customers from publishing.
