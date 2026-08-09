# `agentclaw.community.core.desktop_bot`

Desktop-bot domain — variant of bot lifecycle for DeskOS-bound bots (BAAS device class).

## Context Boundary

```yaml
purpose: "Desktop-bot domain — variant of bot lifecycle for DeskOS-bound bots (BAAS device class)."
provides:
  - "DesktopBotService"
consumes:
  - "BotRepository"
  - "DeviceService"
  - "BaasService"
  - "SkillCenter factories"
  - "PassportPlugin"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.errors
  - agentclaw.community.core.mcp
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.plugin_api.cache
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
```

### Change impact

DesktopBot lifecycle is a parallel track to standard bot; refactors that unify the two need to touch this domain carefully.
