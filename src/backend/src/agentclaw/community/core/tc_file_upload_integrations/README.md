# TC File Upload Integrations

## Context Boundary

```yaml
purpose: Resolve authoritative TC resource context and coordinate bounded, best-effort resource-ready publication.
provides:
  - TcResourceContextService
  - TcResourceReadyCoordinator
  - TcResourceReadyObserverProtocol
consumes:
  - DeviceBindingRepository
  - SessionResourceRecord
  - TcResourceReadyPublisherPlugin
internal_dependencies:
  - agentclaw.community.core.bot_management.token_vault
  - agentclaw.community.core.repository.protocols.devices
  - agentclaw.community.core.repository.protocols.platform
  - agentclaw.community.plugin_api.tc_resource_ready
  - agentclaw.community.core.session_resources
  - agentclaw.community.log
```

### Change impact

The existing TC upload and materialization APIs remain the authoritative primary
flow. Every production path that observes a `READY` resource invokes the same
observer: legacy upload completion and status polling, the materialization
callback, and the formal OpenAPI session-file completion and status operations.

The coordinator publishes only `schema_version`, a deterministic `event_id`,
and `res_id`. It does not retain browser-supplied session, group, member, user,
file, transfer, or download data. The receiving OCB/ECB integration resolves
those facts from authoritative storage by `res_id`.

Delivery remains best-effort and non-blocking. In-flight work is capped, and
successful deliveries are deduplicated by a bounded TTL/LRU cache. Scheduling,
overload, and downstream delivery failures are logged without changing the TC
API result. There is no durable outbox, retry worker, or restart-safe guarantee
in this phase.

The internal resource-context response preserves the uploader as `user_id` and
resolves the Bot `owner_id` from the resource's persisted `binding_id`. The binding
must exist, identify the same `bot_uuid`, and contain a non-empty `entity_id`.
Its current lifecycle status is not part of this historical ownership lookup.
