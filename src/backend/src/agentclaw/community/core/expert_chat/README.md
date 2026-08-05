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
  - "SkillSync guard"
  - "CollaboratorService (chat 权限校验)"
internal_dependencies:
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.caller_identity
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
