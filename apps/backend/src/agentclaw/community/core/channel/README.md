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
  - agentclaw.community.core.repository.protocols.chat    # repository contracts consumed by this module
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.di.modules
  - agentclaw.community.log
  - agentclaw.community.core.devices.services.device_filesystem
  - agentclaw.community.plugin_api.device_sync_dispatcher
```

### Change impact

Channel changes affect group_chat and bot_chat consumers; signature changes propagate via di/modules/skill_center_module.

Service Bot channel rows retain their immutable entity in `identity_id` after
Owner handoff. Legacy list/create parameters may name the current Owner; the
service normalizes these to the entity for transferable service Bots. Shared
defaults and personal/default Bot channel identities retain their legacy rules.
Stage generation and existing-channel synchronization resolve the Bot before
calling Owner-scoped workspace APIs. Those calls use its current Owner, while
channel collection uses its stable entity. The Owner-only HTTP config endpoint
checks current ownership independently; an entity locator grants no authority.
