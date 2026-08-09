# `agentclaw.community.core.engine_runtime`

Engine-runtime relay — forwards one public `/openapi/v1/bots/<component>/{bot_id}/…`
request to that bot's engine adapter and normalises the answer.

## Context Boundary

```yaml
purpose: "Engine-runtime relay — resolves the caller's bot, forwards one HTTP call to its engine adapter, and normalises the engine envelope."
provides:
  - "EngineRuntimeRelay — bot resolution with operator adjudication + stage-aware device forward + envelope normalisation"
  - "EngineConnectionService — composes a bot's usable WebSocket connections"
  - "gate — who may operate a bot, and which bot types/stages the operator surfaces serve"
  - "stage — which runtime a stage names (draft workspace vs published bindings)"
  - "EngineResult / ConnectionResult / SocketInfo value objects"
  - "Engine-runtime domain errors (no HTTP status; the adapter maps them)"
consumes:
  - "BotService — owner-scoped bot lookup; the isolation seam"
  - "DeviceContextResolver — bot -> DeviceContext (the repo's single provider-resolution point)"
  - "DeviceAdapterTransport — the one system boundary (HTTP to the bot's engine adapter)"
  - "DeviceService — connection info for the socket-composing endpoint"
  - "DeviceBindingRepository — the active binding id, without building conn info"
  - "BotPublishRepository — a service bot's published stage bindings (ext.binding.{verify,online})"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.bot_collaborator.models
  - agentclaw.community.core.bot_collaborator.repository.protocol
  - agentclaw.community.core.bot_management.services.bot_service
  - agentclaw.community.core.devices.errors
  - agentclaw.community.core.devices.repository.protocol
  - agentclaw.community.core.devices.services.device_context
  - agentclaw.community.core.devices.services.device_context_resolver
  - agentclaw.community.core.devices.services.device_service
  - agentclaw.community.core.service_bot.repository.bot_publish_repository
  - agentclaw.community.core.service_bot.repository.models
  - agentclaw.community.core.service_bot.types
  - agentclaw.community.log
  - agentclaw.community.core.devices.models
  - agentclaw.community.di.config
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.utils.env_utils
```

### Change impact

This module is the only place Track C crosses into the device. Two properties
are load-bearing and must survive any refactor:

1. **Bot resolution is owner-scoped and happens before the forward.** The
   engine has no tenant axis — once a request lands on a device, nothing
   constrains it — so the relay is the last point at which isolation can be
   enforced. A handler that forwards before resolving, or that passes a
   caller-supplied `user_id`, defeats it silently and no downstream guard will
   notice.
2. **The engine's `success: false` never becomes a public success.** The engine
   can report failure inside an HTTP 200; `_normalise` raises on that.
3. **A `service` bot resolves through its published runtime binding, never
   `ac_bots.binding_id`.** That column holds the pre-publication draft — on the
   BaaS path, the owner's own device — so the by-bot entry point sends a
   published bot's traffic to the wrong box. The live binding is the publish
   record's `ext.binding.online`, and that record **must be looked up by the
   `ac_bots` primary key**: `bot_id` is not unique across owners, so a lookup
   keyed on it can return another owner's runtime and forward the caller there.
   Owner-scoped bot resolution does not constrain a query that never names the
   row it authorised. Device resolution is also blocking network I/O and must
   stay off the event loop; `call()` runs it in a worker thread.

Adding a group means adding a router, not a relay method: `call()` is a generic
forward on purpose.
