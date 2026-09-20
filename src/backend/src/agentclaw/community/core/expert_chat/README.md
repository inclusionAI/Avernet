# `agentclaw.community.core.expert_chat`

Expert-chat domain — bot-to-bot chat session orchestration for the expert workflow.

## Context Boundary

```yaml
purpose: "Expert-chat domain — bot-to-bot chat session orchestration for the expert workflow."
provides:
  - "ExpertChatService"
  - "Expert-chat SQLAlchemy models"
consumes:
  - "BotRepository"
  - "DeviceService + DeviceContextResolver"
  - "ServiceBot repo + BaasService"
  - "CommonConfigService (legacy ARCA image policy resolution)"
  - "SkillSync guard"
  - "CollaboratorService (chat 权限校验)"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.chat    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.caller_identity
  - agentclaw.community.core.common_config
  - agentclaw.community.core.devices
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.skill_center
  - agentclaw.community.log
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils
```

### Change impact

Schema changes require SQLite migration. Cross-domain dependencies make refactors risky — start with read-only impact analysis.

### Application Caller connection policy

The BaaS-authenticated `POST /api/v1/expert-chats/app-caller-connection`
may access an existing Caller in the server default tenant, including another
user's instance on a private Bot. Its HS256 JWT requires `iss=baas`, `iat/exp`
and validates optional `nbf` with the existing 5-second leeway. It ignores
`principals`, `app_id`, `tenant` and `aud` and never uses them for tenant routing.
It reuses the configured `SecretNamesConfig.gateway_principal_signing_key`
value, with no separate BaaS key or deployment setting. BaaS must securely load
the same key; issuer separates protocols, not holders of the shared key. This endpoint does not require an application grant, ownership, or current
collaborator membership. The Bot must exist in the tenant, and the Caller must
already have an instance with a nonempty bot UUID. No administrator privilege or
first-time provisioning is granted. User-authenticated endpoints retain their
existing access policies.
