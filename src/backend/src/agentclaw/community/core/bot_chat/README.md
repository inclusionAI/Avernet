# `agentclaw.community.core.bot_chat`

Single-user bot chat domain — errors, schemas, service entry points for the per-user chat session flow.

## Context Boundary

```yaml
purpose: "Single-user bot chat domain — errors, schemas, service entry points for the per-user chat session flow."
provides:
  - "BotChatService"
  - "Errors + request/response schemas"
consumes:
  - "(low fanout — mostly self-contained today)"
internal_dependencies:
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.di.config
  - agentclaw.community.log
  - agentclaw.community.plugin_api.database
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils.env_utils
```

### Change impact

Local to the bot-chat flow. Changes here affect the single-user chat path; group chat lives separately.
