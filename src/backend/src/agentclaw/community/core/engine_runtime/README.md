# `agentclaw.community.core.engine_runtime`

Engine-runtime relay — forwards one public `/openapi/v1/bots/{bot_id}/…`
request to that bot's engine adapter and normalises the answer.

## Context Boundary

```yaml
purpose: "Engine-runtime relay — resolves the caller's bot, forwards one HTTP call to its engine adapter, and normalises the engine envelope."
provides:
  - "EngineRuntimeRelay — owner-scoped bot resolution + device forward + envelope normalisation"
  - "EngineResult / ConnectionResult / SocketInfo value objects"
  - "Engine-runtime domain errors (no HTTP status; the adapter maps them)"
consumes:
  - "BotService — owner-scoped bot lookup; the isolation seam"
  - "DeviceContextResolver — bot -> DeviceContext (the repo's single provider-resolution point)"
  - "DeviceAdapterTransport — the one system boundary (HTTP to the bot's engine adapter)"
  - "DeviceService — connection info for the socket-composing endpoint"
internal_dependencies:
  - agentclaw.community.core.bot_management.services.bot_service
  - agentclaw.community.core.devices.services.device_context
  - agentclaw.community.core.devices.services.device_context_resolver
  - agentclaw.community.core.devices.services.device_service
  - agentclaw.community.log
  - agentclaw.community.plugin_api.device_adapter_transport
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

Adding a group means adding a router, not a relay method: `call()` is a generic
forward on purpose.
