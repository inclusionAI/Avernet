# `agentclaw.community.core.cron`

Cron / scheduled-job relay — relays scheduled actions to bots via device service.

## Context Boundary

```yaml
purpose: "Cron / scheduled-job relay — relays scheduled actions to bots via device service."
provides:
  - "CronRelay service"
consumes:
  - "BotService"
  - "DeviceService"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.events.bus
  - agentclaw.community.core.events.types
  - agentclaw.community.core.service_bot
  - agentclaw.community.di
  - agentclaw.community.kernel
  - agentclaw.community.log
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.utils.env_utils
```

### Change impact

Misfires here cause silent data updates at the wrong cadence. Schedule-key changes require careful migration.
