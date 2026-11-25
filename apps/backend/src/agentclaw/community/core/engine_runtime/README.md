# `agentclaw.community.core.engine_runtime`

Engine-runtime relay — forwards one public `/openapi/v1/bots/<component>/{bot_id}/…`
request to that bot's engine adapter and normalises the answer.

## Context Boundary

```yaml
purpose: "Engine-runtime relay — resolves the caller's bot, forwards one HTTP call to its engine adapter, and normalises the engine envelope."
provides:
  - "EngineRuntimeRelay — bot resolution with operator adjudication + stage-aware device forward + envelope normalisation"
  - "EngineConnectionService — composes WebSocket connections and provider-issued A2A faces"
  - "gate — who may operate a bot, and which bot types/stages the operator surfaces serve"
  - "stage — which runtime a stage names (draft workspace vs published bindings)"
  - "EngineResult / ConnectionResult / SocketInfo value objects"
  - "SessionKeyCodec / SessionKeyCodecRegistry — the engine-specific wire form of a session id (assembled by EngineRuntimeModule)"
  - "Engine-runtime domain errors (no HTTP status; the adapter maps them)"
  - "DesktopConnectionServiceProtocol — owner-affined desktop WebSocket discovery"
  - "SessionSourceListingProtocol — bounded caller-relative session windows"
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

### Opt-in session source windows

`SessionSourceListingService` consumes the existing relay after the operator
gate has resolved `BotFacts`. Owners and explicit collaborators can request
`source=mine|others`; caller identity always comes from the authenticated actor,
not the Bot's owner locator. Omitted source preserves the existing single-read
page-number behavior. For `mine`, verified OpenClaw and AICoding/Claude Code
implementations receive `user_id=authenticated caller` and
`source=all_but_others`, regardless of Owner/collaborator role. One native-filtered
offset window is read, preserving the existing Owner filter's treatment of
unknown keys (including some channel sessions). No second creator filter is applied.
Other engines' `mine`, and all `others` queries, consume the unfiltered
offset/limit list and use the fallback scanner below. Upstream errors propagate;
they do not trigger an unfiltered retry masquerading as a successful native filter.

Creator parsing supports the historical `user:<id>:...`, `...:user:<id>` and
`...:direct:<id>` keys, case-insensitive with numeric leading-zero normalization.
The engine's `user_id` field denotes the reader and is not creator evidence.
Unknown and synthetic task identities stay in `mine` for owner-style compatibility
and do not enter `others`. This is a visibility convenience, not an authorization
rule or a strict audit of human-created sessions.

The scanner performs at most two reads of at most `page_size+1` candidates each,
with a three-second await/transport budget. Cursors resume the consumed **raw**
offset for fallback scans, or the filtered offset for native mine, with no
server-side data or permission cache. Strategy is part of the cursor scope.
They bind the caller, resolved Bot/owner, engine, stage, instance and filters;
they are not secret or authorization tokens. Every continuation is reauthorized.
Concurrent insertions/deletions can shift offsets; this is not a snapshot export.
Never infer exhaustion from a short/empty filtered window. Follow `has_more` and
`next_cursor`; `scan_limited` says the bounded scan stopped early. Continuing
beyond 2000 candidates returns an explicit error, not a false final page.
The cumulative returned-match `total` is a lower bound, not the engine total.

No cross-instance aggregation, new friend privileges, writes or message-history
permissions are introduced. `f_user_id` cannot be combined with source filtering.
Provider connection work already running in a blocking thread may finish after
an await timeout; the per-request read bound also limits that outstanding work.
Public request/response details live in [the Engine surface guide](../../../../../docs/openapi-v1/engine-surface.md).

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

### Credentialed A2A proxy faces

The provider owns the browser-reachable base URL. BaaS `ws-info` may declare
`a2a_auth=proxypass`: the A2A face uses its existing `token` and `expires_at`,
with no second device selection or HTTP-info lookup. The device service first
applies the authenticated caller's existing gray gate, then accepts this face
only for non-desktop OpenClaw with a matching HTTPS proxy target and live token.
Invalid/unsupported faces are withheld; the WS result remains unchanged.

After the existing operator check, `/connection` adds `headers` only to the
`kind=a2a` socket. The browser applies them exclusively to the card and `/rpc`
under that base, preserving per-request IAM. It refreshes before credential
expiry, rejects redirects and changed targets, and never automatically retries
a send. No Backend card/RPC forwarding route is added. Existing direct A2A
without headers and existing WS sockets retain their contracts.

The HTTP proxy must allow `x-iam-token` on the A2A RPC preflight without
changing its origin or token validation. A2A SSE responses carry
`X-Accel-Buffering: no` so reverse proxies do not batch streaming output.
