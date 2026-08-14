# `agentclaw.community.core.grt_chat`

GRT-chat domain — orchestrator for the GRT (geo-replication-test?) chat scenario.

## Context Boundary

```yaml
purpose: "GRT-chat domain — orchestrator for the GRT chat scenario."
provides:
  - "GRT chat services"
consumes:
  - "BotRepository"
  - "DeviceRepository"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.log
  - agentclaw.community.plugin_api.sandbox_runtime
```

### Change impact

Scenario-specific; changes here are unlikely to ripple outside GRT-chat flows.
