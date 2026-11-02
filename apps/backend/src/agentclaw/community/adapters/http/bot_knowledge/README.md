# Knowledge selection HTTP

## Context Boundary

```yaml
purpose: Adapt authenticated current-selection requests.
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
Existing current-selection GET/PUT and AGENTS.md scheduling remain unchanged.
Stage-aware management and conversation reads reuse `/api/knowledge/bots/...`
and its existing authorization policies; no standalone publication-selection
HTTP URL is exposed by this adapter. Publication snapshot data is internal
auxiliary state, never runtime authorization or a native publish prerequisite.
