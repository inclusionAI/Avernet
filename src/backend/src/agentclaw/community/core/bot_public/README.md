# `agentclaw.community.core.bot_public`

Bot publication / discoverability domain — public bot listing, friend/relationship state, publish approvals.

## Context Boundary

```yaml
purpose: "Bot publication / discoverability domain — public bot listing, friend/relationship state, publish approvals."
provides:
  - "BotPublicService"
  - "BotDiscoverService"
  - "Bot-public SQLAlchemy models"
consumes:
  - "BotManagement repo + service"
  - "AntProcess plugins (auth_relationship, bot_publish_approval, antprocess)"
  - "PassportPlugin"
  - "SkillCenter factories"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.operator_context
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.log
  - agentclaw.community.plugin_api.approval_workflow
  - agentclaw.community.plugin_api.auth_relationship
  - agentclaw.community.plugin_api.bot_publish_approval
  - agentclaw.community.plugin_api.device_sync
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.utils.env_utils
```

### Change impact

Owns visible-to-other-users state; bugs here can leak unpublished bots or block legitimate publication. The approval callback path crosses with core/antprocess.
