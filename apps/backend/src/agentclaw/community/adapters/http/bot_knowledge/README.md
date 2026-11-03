# Current knowledge selection HTTP

## Context Boundary

```yaml
purpose: Adapt authenticated requests for current application knowledge selection.
provides:
  - router
consumes:
  - BotKnowledgeServiceProtocol
internal_dependencies:
  - agentclaw.community.adapters.http.auth
  - agentclaw.community.api
  - agentclaw.community.di
  - agentclaw.community.kernel
  - agentclaw.community.utils
```

### Change impact
No grant writes and no service-Bot publication/version inputs.
