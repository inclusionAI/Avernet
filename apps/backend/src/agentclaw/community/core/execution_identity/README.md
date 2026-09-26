# Execution Identity

## Context Boundary

```yaml
purpose: Persist and activate the principal identity used by a Bot's AgentPass credentials.
provides:
  - ExecutionIdentityServiceProtocol
  - RuntimePassportTokenUpdaterProtocol
  - ExecutionIdentityBinding
  - BotExecutionIdentityBindingModel
consumes:
  - BotRepository
  - ExecutionIdentityRepositoryProtocol
  - PassportPlugin
  - RuntimePassportTokenUpdaterProtocol
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.log
  - agentclaw.community.plugin_api
  - agentclaw.community.utils
```

### Change impact

This module owns only AgentPass execution identity. It does not own Bot
management authorization, `ac_bots.owner_id`, or any filesystem, container,
skill, MCP, BaaS, BCN, or Proxy resource address. A missing ACTIVE binding is
defined as the legacy state and resolves to the Bot's unchanged owner ID.
