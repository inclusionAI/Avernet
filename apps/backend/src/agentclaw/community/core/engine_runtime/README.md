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
  - "SessionKeyCodec / SessionKeyCodecRegistry — the engine-specific wire form of a session id (assembled by EngineRuntimeModule)"
  - "Engine-runtime domain errors (no HTTP status; the adapter maps them)"
  - "DesktopConnectionServiceProtocol — owner-affined desktop WebSocket discovery"
consumes:
  - "RuntimeBotLookup — lightweight scoped legacy/entity address resolution; operator authorization remains in gate"
  - "DeviceContextResolver — bot -> DeviceContext (the repo's single provider-resolution point)"
  - "DeviceAdapterTransport — the one system boundary (HTTP to the bot's engine adapter)"
  - "DeviceService — connection info for the socket-composing endpoint"
  - "DeviceBindingRepository — the active binding id, without building conn info"
  - "BotPublishRepository — a service bot's published stage bindings (ext.binding.{verify,online})"
internal_dependencies:
  - agentclaw.community.core.bot_management.addressing
  - agentclaw.community.core.bot_management.errors
  - agentclaw.community.core.service_bot.services.baas_service
  - agentclaw.community.core.service_bot.baas_service_errors
  - agentclaw.community.core.bot_management.engines    # engine-type spelling normalisation
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.bot_collaborator.models
  - agentclaw.community.core.bot_collaborator.protocols
  - agentclaw.community.core.bot_management.services.bot_service
  - agentclaw.community.core.devices.errors
  - agentclaw.community.core.devices.services.device_context
  - agentclaw.community.core.devices.services.device_context_resolver
  - agentclaw.community.core.devices.services.device_service
  - agentclaw.community.core.devices.services.a2a_issuance_gray    # 用户级 A2A 灰度门：desktop 分支不经设备服务，由本模块就地判定（连接签发治理）
  - agentclaw.community.core.service_bot.repository.models
  - agentclaw.community.core.service_bot.types
  - agentclaw.community.log
  - agentclaw.community.core.devices.models
  - agentclaw.community.di.config
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.utils.env_utils
  - agentclaw.community.utils.runtime_list_timing
```

### Change impact

This module is the only place Track C crosses into the device. Two properties
are load-bearing and must survive any refactor:

1. **Unique scoped Bot resolution and current-Owner authorization happen before forwarding.** The
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

### Runtime read budget

The relay and connection composer are injected with `RuntimeBotLookup`, not
the management-page `BotService`. It uses the same `BotAddressResolver` and
repository scope/ambiguity rules, but returns a copy of the already-resolved
row without a second owner lookup, draft binding enrichment or template read.
It does not cache rows or permission results across requests. The existing
operator check, published-stage binding selection, device provider and public
response contracts remain unchanged. IAM and management-page queries retain
their existing services. See `specs/2026-11-06-runtime-read-budget/spec.md`.

### Desktop connection contract

Desktop Bots support draft only. After owner/operator authorization, the active
binding is resolved through BaaS get_ws_info using device_affinity=owner_id and
the engine-specific WebSocket path. The default desktop_connection.mode is
direct: only an explicit loopback ws port and the expected path are accepted.
The optional relay value uses existing gateway routing. Unknown configuration
keys and unsupported modes fail validation. The additive transport_mode field
appears only in desktop responses; cloud and friend responses retain their shape.
HTTP runtime discovery and legacy endpoints retain their existing behavior.
Consumer contract tests are test_desktop_connection_contract.py and
test_desktop_workflows.py; Mock BaaS results do not constitute device acceptance.
