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

TC conversation-file automatic knowledge ingestion is retired. Upload completion,
status polling, and the materialization callback (including the formal OpenAPI
session-file endpoints) only execute the normal Session Resource flow. None of
these routes resolves or invokes `TcResourceReadyObserverProtocol`.

The coordinator and publisher contracts remain for compatibility, but no
production session-file path schedules them. Do not reconnect them to READY
observations. ECB rejects the old resource-ready notice with
`410 TC_RESOURCE_READY_DISABLED` and no longer runs its automatic inbox consumers.

Normal attachment upload, materialization, content access, and reference remain
unchanged. Explicit knowledge uploads/imports, existing knowledge, historical
inbox records, and the internal trusted context API are not removed. Existing
shared token/context wiring must remain available to its other consumers.

The internal resource-context response preserves the uploader as `user_id` and
resolves the Bot `owner_id` from the resource's persisted `binding_id`. The binding
must exist, identify the same `bot_uuid`, and contain a non-empty `entity_id`.
Its current lifecycle status is not part of this historical ownership lookup.
