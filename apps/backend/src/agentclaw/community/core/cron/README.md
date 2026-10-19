# `agentclaw.community.core.cron`

Cron / scheduled-job relay — relays scheduled actions to bots via device service.

## Context Boundary

```yaml
purpose: "Cron / scheduled-job relay — relays scheduled actions to bots via device service."
provides:
  - "CronRelay service"
  - "CronAdminServiceProtocol (privileged list and disable only)"
consumes:
  - "BotService"
  - "DeviceService"
  - "RuntimeBindingResolutionService"
  - "CollaboratorService"
internal_dependencies:
  - agentclaw.community.core.runtime_binding
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.access
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.aicoding.protocols    # hosted workspace ensure contract used by the 7x24 auto-setup service
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

### Single-container targeting

POST /api/cron accepts runtime_stage (draft/verify/online), device_uuid (runtime
instance UUID), and operator_id (creator and
Caller identity). The authenticated operator (internal authenticated_user_id) remains separate from
the requested operator_id/Caller identity;
delegation requires existing Bot ADMIN/OWNER permission. Explicit Caller targets
only support online and must reference an existing Caller binding. Device
membership is validated before forwarding once. No provisioning or fallback
occurs. Omitted creation target fields preserve the legacy draft behavior.

Detail, update, delete, run, runs and running accept the same target fields.
Explicitly selected published instances permit complete edits and deletion.
Legacy untargeted published management retains its existing broadcast restrictions.
Creation/detail results expose runtime_stage, device_uuid and operator_id
for subsequent management. Targets are container-local and are not migrated when
a container is replaced. CronTargetedOperationsMixin records request, resolved
binding, transport result/failure and response events without connection credentials.
