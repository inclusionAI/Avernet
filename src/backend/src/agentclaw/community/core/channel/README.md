# `agentclaw.community.core.channel`

Channel domain — channel objects, channel-scoped bot service wiring, channel-side file ops.

## Context Boundary

```yaml
purpose: "Channel domain — channel objects, channel-scoped bot service wiring, channel-side file ops."
provides:
  - "ChannelService"
  - "Channel repositories"
consumes:
  - "BotService"
  - "DeviceFileSystem"
  - "DeviceSyncDispatcher"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.di.modules
  - agentclaw.community.log
  - agentclaw.community.core.devices.services.device_filesystem
  - agentclaw.community.plugin_api.device_sync
```

### Change impact

Channel changes affect group_chat and bot_chat consumers; signature changes propagate via di/modules/skill_center_module.
