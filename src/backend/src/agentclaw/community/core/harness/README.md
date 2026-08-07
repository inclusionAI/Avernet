# `agentclaw.community.core.harness`

Harness domain — scan / patch / bot-profile management for the test-harness flows.

## Context Boundary

```yaml
purpose: "Harness domain — scan / patch / bot-profile management for the test-harness flows."
provides:
  - "HarnessService"
  - "Scan/patch repository protocols"
  - "BotProfile model"
consumes:
  - "Identity service"
  - "SkillCenter factories"
  - "WorkspacePathFactory"
  - "MCPCenterPlugin"
internal_dependencies:
  - agentclaw.community.core.services
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.workspace
  - agentclaw.community.di.config
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.mcp_center
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.secret_resolver
  - agentclaw.community.utils.env_utils
  - agentclaw.community.utils.retry  # llm.py aliases its transport helpers here
```

### Change impact

Owns harness state. Patch-record schema is referenced by external tooling; changes need coordination.
