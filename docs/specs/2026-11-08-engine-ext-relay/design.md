# Engine Extension Relay API — Design

> Status: **Draft** · Date: 2026-11-08 · Scope: `engine/adapter` (primary), `apps/gateway`, frontends, external engine owners
> 中文版：[design.zh-CN.md](./design.zh-CN.md)

## 1. Background

The engine adapter (`engine/adapter`, port 20003) exposes one HTTP/WS API
surface for every engine it hosts — OpenClaw, AICoding, Claude Code, Hermes,
and future engines. Most of that surface is **common**: sessions, chat, MCP,
skills, cron, approvals, files, nodes, models. Each engine implements the
common surface through Plugin Protocols under `core/*`, declares what it
supports through `EngineCapabilities`, and the web layer gates every handler
with `check_capability(...)` (`api/caps.py`), answering 501 for unsupported
capabilities.

Some APIs, however, only make sense for **one** engine. Today they end up in
the adapter in one of three ways:

| Today's pattern | Example | Problem |
| --- | --- | --- |
| Engine-specific router in the adapter | `api/aicoding/*`, `api/aicoding_sessions/*`, `/api/openclaw/{config,test-connection,disconnect}` | Adapter grows a module per engine; every change needs an adapter release; the 1,000-line limit and architecture rules are under constant pressure |
| Engine-specific forwarding plugin | `/data/*` → `DataProxyService` (AICoding only, gated by `DATA_PROXY_FORWARD`) | Correct idea, but bespoke: one Protocol, one capability, one router per upstream |
| WS method passthrough | `RelayService.forward_request` for unknown WS methods (OpenClaw gateway methods such as `sessions.list`) | WS-only, tied to the engine's own upstream process, not to an independently owned service |

We want a **single, generic relay** for engine-specific APIs: the engine owner
runs their own server somewhere; the adapter only authenticates, checks, and
forwards.

## 2. Goals and non-goals

### Goals

1. One stable URL namespace for all engine-specific HTTP APIs:
   `/api/ext/{engine}/...`.
2. **Configuration-driven** registration of an engine's extension upstream —
   adding or changing an engine-specific API requires no adapter code change
   and no adapter release.
3. Safe by default: authenticated, allow-listed, size/time bounded,
   credentials never leaked upstream, no open proxy.
4. Transparent: method, path, query, body, status and response body pass
   through verbatim; streaming (SSE / chunked) responses are supported.
5. Discoverable: the frontend can ask which extension APIs an engine offers.
6. Fits the Microkernel Architecture Constitution
   (`docs/arch/arch.rules.md`): Protocol + real implementation +
   `Noop` / `Mock`, conformance tests under
   `tests/contracts/`, no scattered `if engine == ...`.

### Non-goals

- Replacing the common API. Anything two or more engines need belongs in the
  common surface (see §10, graduation).
- Replacing `RelayService` (WS method passthrough to an engine's own upstream
  process). That stays; this design covers independently owned HTTP services.
- Business logic, response reshaping, caching, or aggregation in the relay.
  The relay is a pipe.
- Hosting / deploying the owner's server. Owners run it; we only reach it.
- WebSocket relay in phase 1 (designed in §8, delivered in phase 2).

## 3. Terminology

| Term | Meaning |
| --- | --- |
| **Extension API** | An API that only one engine supports. |
| **Extension upstream** | The owner-operated HTTP server that implements an engine's Extension APIs. |
| **Relay** | The adapter component that forwards `/api/ext/{engine}/...` to the extension upstream. |
| **Route** | One allow-listed `(method, path pattern)` entry in an upstream's config. |
| **Manifest** | The upstream's self-description of its Extension APIs (§6.3). |

## 4. Architecture

```
Frontend / Backend / CLI
        │  HTTPS (user / bot / access-key credentials)
        ▼
   Gateway (apps/gateway)
        │  existing route to the bot's adapter instance,
        │  signed X-Avernet-Principal attached
        ▼
   Engine adapter :20003
   ├── /api/sessions, /api/chat, /api/mcp, ...      ← common API (unchanged)
   └── /api/ext/{engine}/{path}                     ← NEW: Extension Relay
            │ 0. verify principal (401 before any lookup)
            │ 1. engine == active engine?
            │ 2. upstream configured?        (ExtRelayRegistry)
            │ 3. route allow-listed?
            │ 4. build outbound headers
            │ 5. forward + stream back       (ExtRelayService)
            ▼
   Extension upstream (owner-operated, anywhere reachable)
     e.g. https://aicoding-ext.example.com/v1/...
```

Placement rationale:

- **In the adapter, not the gateway.** The adapter already owns "which engine
  is this bot running" (`EngineManager`) and capability gating. The gateway
  stays engine-agnostic and keeps doing auth + routing to the bot's adapter.
- **Per bot instance.** Each adapter runs one active engine. A request for
  `/api/ext/hermes/...` on an adapter running `openclaw` is rejected (§7), so a
  client can never reach another engine's upstream through the wrong bot.

### 4.1 Components (adapter)

Follows the adapter's existing layering (`plugin_api/` Protocols →
`plugins/` impls, re-exported through `core/`, wired by DI modules), the same
shape as `auth_gate`:

```
src/engine/community/
├── plugin_api/ext_relay/
│   ├── protocol.py      # ExtRelayService, ExtRelayRegistry Protocols + errors
│   └── models.py        # UpstreamConfig, RouteRule, RelayRequest, RelayResult
├── core/ext_relay/      # re-exports for the web layer
├── plugins/ext_relay/
│   ├── httpx_impl.py    # HTTP forwarding via a pooled httpx.AsyncClient
│   ├── yaml_registry.py # registry built from ext_relay config (§5)
│   ├── noop_impl.py     # NoopExtRelayService: nothing configured → 501
│   └── mock_impl.py     # in-memory fake for tests
├── di/modules/          # binds impls from config; no `if engine == ...` in code
├── api/ext_relay/
│   └── router.py        # /api/ext/{engine}/{path:path}; HTTP concerns only
└── tests/contracts/test_ext_relay_contract.py
```

```python
class ExtRelayRegistry(Protocol):
    def get(self, engine: str) -> UpstreamConfig | None: ...
    def engines(self) -> list[str]: ...

class ExtRelayService(Protocol):
    async def forward(self, req: RelayRequest) -> RelayResult | StreamingRelayResult: ...
    async def manifest(self, engine: str) -> dict: ...
```

Errors are semantic (`RouteNotAllowed`, `UpstreamNotConfigured`,
`UpstreamUnreachable`, `UpstreamTimeout`, `PayloadTooLarge`) and mapped to
HTTP status in the router, following the existing `DataProxyError` pattern.

## 5. Configuration

Upstreams are registered in the adapter config (`application-*.yaml`), keyed by
engine name as registered in `DEFAULT_REGISTRY`. Example:

```yaml
ext_relay:
  enabled: true
  defaults:
    connect_timeout_s: 3
    read_timeout_s: 30
    max_request_body_mb: 10
    max_concurrency: 64
  upstreams:
    aicoding:
      base_url: https://aicoding-ext.example.com/v1
      auth:
        type: bearer                    # none | bearer | hmac | mtls
        secret_ref: env:AICODING_EXT_TOKEN   # never inline; env:/file:/secret-store ref
      forward_principal: true           # forward signed principal (§7.3)
      routes:                           # allow-list; empty = deny all
        - { methods: [GET],        path: "/workspaces/**" }
        - { methods: [POST],       path: "/workspaces/*/index" }
        - { methods: [GET],        path: "/events", stream: true, read_timeout_s: 600 }
      manifest_path: /.well-known/avernet-ext.json
    hermes:
      base_url: https://hermes-ext.example.com
      auth: { type: none }
      routes:
        - { methods: [GET, POST], path: "/memory/**" }
```

Rules:

- `base_url` must be `https` in prod; `http` only for loopback / local profile.
- No credentials in config values — only references.
- Route paths are glob patterns over the **relative** path (`*` = one
  segment, `**` = any depth). Paths are normalized before matching; `..`,
  encoded slashes, and double slashes are rejected.
- An engine with no `upstreams` entry has no Extension APIs → 501.
- Config is loaded at startup; hot reload is a phase-2 item.

## 6. API contract

### 6.1 Relay endpoint

```
{METHOD} /api/ext/{engine}/{path:path}?{query}
```

| Part | Behaviour |
| --- | --- |
| `METHOD` | `GET, POST, PUT, PATCH, DELETE` (and `HEAD`). `OPTIONS` is answered by the adapter's CORS middleware and never forwarded. |
| `engine` | Must equal the adapter's active engine. |
| `path` | Appended to `base_url` after normalization and allow-list match. |
| `query` | Forwarded verbatim. |
| body | Streamed through verbatim, bounded by `max_request_body_mb`. Exception: `hmac` upstreams buffer the body (same bound) to compute the signature (§7.3). |
| response | Upstream status, headers (filtered, §7.4) and body returned verbatim; `text/event-stream` and chunked bodies are streamed. |

Example:

```
GET /api/ext/aicoding/workspaces/ws_123/tree?depth=2
→ GET https://aicoding-ext.example.com/v1/workspaces/ws_123/tree?depth=2
```

### 6.2 Response envelope

- **Upstream responses are passed through unchanged** — the relay does not wrap
  them. Owners are *encouraged* to use the platform envelope
  `{code, message, data, request_id}` so clients handle errors uniformly.
- **Relay-generated errors** always use the standard envelope:

```json
{ "code": 502001, "message": "extension upstream unreachable", "data": null, "request_id": "<trace id>" }
```

Clients distinguish relay errors from upstream errors with the response header
`X-Avernet-Relay-Error: <reason>` (present only on relay-generated errors).

### 6.3 Discovery

```
GET /api/ext/{engine}/_manifest
```

Returns the upstream's manifest (fetched from `manifest_path`, cached for
60 s), intersected with the configured allow-list so it never advertises a
route the relay would reject:

```json
{
  "engine": "aicoding",
  "version": "2026.11.1",
  "apis": [
    { "id": "workspace.tree", "method": "GET", "path": "/workspaces/{id}/tree",
      "summary": "List workspace files", "stream": false },
    { "id": "events", "method": "GET", "path": "/events", "stream": true }
  ],
  "openapi": "/openapi.json"
}
```

`_`-prefixed first segments are reserved for the relay itself; upstream routes
may not start with `_`.

Additionally, `GET /api/engine/capabilities` reports the new capability
`ext.relay` (`Capability.EXT_RELAY`) when the active engine has an upstream
configured, so the frontend can decide whether to call `_manifest` at all.

### 6.4 Error mapping

| Situation | Status | `X-Avernet-Relay-Error` |
| --- | --- | --- |
| `engine` not registered | 404 | `unknown_engine` |
| `engine` ≠ active engine | 409 | `engine_mismatch` |
| No upstream configured for engine | 501 | `not_configured` |
| Path not allow-listed / reserved / malformed | 404 | `route_not_allowed` |
| Method not allowed for path | 405 | `method_not_allowed` |
| Missing / invalid principal | 401 | `unauthenticated` |
| Request body over limit | 413 | `payload_too_large` |
| Concurrency limit hit | 429 | `overloaded` |
| Connect error / DNS / TLS | 502 | `upstream_unreachable` |
| Connect or read timeout | 504 | `upstream_timeout` |
| Upstream answered (any status) | upstream's | — (absent) |

Not-allow-listed paths return 404 (not 403) so the relay does not reveal which
routes exist. Checks run in the pipeline order of §4: principal verification
comes first, so an unauthenticated caller always gets 401 and cannot probe
the active engine, the upstream configuration, or the allow-list.

## 7. Security

### 7.1 Inbound authentication

The relay accepts only requests that carry a valid signed
`X-Avernet-Principal` minted by the gateway. No principal → 401. The relay
never trusts a bare identity header.

Note: the adapter's HTTP routes do not verify the gateway principal today (the
adapter is reached only through the gateway / bot sandbox network). Because the
relay is the first adapter route that sends traffic *out* to third-party
servers, it must not rely on network placement alone; adding principal
verification to the adapter is a phase-1 dependency (see §11 Q2).

### 7.2 Engine binding

`engine` in the path must equal `EngineManager.engine`. This prevents using a
bot's adapter as a hop to a different engine's upstream and keeps one bot's
traffic on its own engine's owner.

### 7.3 Outbound identity

Two independent things are sent upstream:

1. **Service credential** (`auth.type`): proves *the relay* is calling. `bearer`
   → `Authorization: Bearer <secret>`; `hmac` → signature over method, path,
   timestamp and body hash; `mtls` → client certificate. Because the body
   hash must be known before headers are sent, `hmac` upstreams buffer the
   request body (bounded by `max_request_body_mb`) instead of streaming it;
   responses still stream.
2. **End-user principal** (`forward_principal: true`): the relay forwards a
   principal token scoped to the upstream audience (`aud = ext:{engine}`) in
   `X-Avernet-Principal`, plus `X-Avernet-Bot-Id` and `X-Avernet-Engine`.
   Upstreams verify it with the platform's public verification key / JWKS.
   Re-signing for a dedicated audience (rather than forwarding the inbound
   token) means a leaked upstream token cannot be replayed against the
   adapter or other services — see §11 Q2.

### 7.4 Header policy

| Direction | Dropped | Added |
| --- | --- | --- |
| Request → upstream | hop-by-hop (`Connection`, `Keep-Alive`, `TE`, `Trailer`, `Transfer-Encoding`, `Upgrade`, `Proxy-*`), `Host`, `Authorization`, `Cookie`, inbound `X-Avernet-*` | `X-Forwarded-For/Proto/Host`, `traceparent`, `X-Request-Id`, service credential, outbound principal |
| Response → client | hop-by-hop, `Set-Cookie`, `Server`, upstream CORS headers | `X-Request-Id`; adapter CORS middleware applies its own policy |

### 7.5 SSRF and abuse controls

- Target host comes **only** from config `base_url`; the client controls the
  path, never the host. Redirects are **not** followed (3xx passed through
  to the client).
- Path normalization and allow-list matching happen before any outbound call.
- Limits: body size, connect/read timeouts, per-upstream concurrency, response
  streaming (no full buffering of large bodies).
- Optional per-route `rate_limit` (phase 2).

## 8. Streaming and WebSocket

- **Phase 1 (HTTP):** responses with `text/event-stream` or no
  `Content-Length` are streamed chunk-by-chunk (`StreamingResponse`); routes
  marked `stream: true` get their own `read_timeout_s` (idle timeout between
  chunks). Client disconnect cancels the upstream request.
- **Phase 2 (WebSocket):** `WS /api/ext/{engine}/{path}` for routes marked
  `ws: true`. Same auth, binding and allow-list; the relay connects upstream
  and pipes frames both ways without inspecting them, closing both sides
  when either closes. Kept out of phase 1 because the adapter's existing WS
  endpoints carry session/chat semantics that need separate review.

## 9. Observability

- Trace: propagate `traceparent` / `X-Request-Id` (adapter
  `TraceContextMiddleware`).
- Access log per relayed call: engine, route pattern (not raw path), method,
  status, upstream latency, bytes in/out, relay error reason. Never log
  bodies, query values, or credential headers.
- Metrics: `ext_relay_requests_total{engine,route,status}`,
  `ext_relay_upstream_latency_seconds{engine,route}`,
  `ext_relay_inflight{engine}`.
- `/api/engine/status` gains an `ext_relay` block: configured, last manifest
  fetch result, upstream reachable.

## 10. Governance: what belongs in the relay

| Question | Answer |
| --- | --- |
| Does only one engine need it? | Extension API → relay. |
| Do ≥ 2 engines need it, or does the frontend want to use it engine-agnostically? | **Graduate**: define a common Protocol + capability in `core/*`, implement per engine, retire the ext route. |
| Does it need adapter-local state (filesystem, engine process, sandbox)? | Not a relay candidate; it is an engine plugin in the adapter. |
| Does it change Engine / Backend / BCS ownership of entities? | Not allowed through the relay; entity ownership rules still apply. |

Owner obligations: publish a manifest, keep backward compatibility within a
major version (`/v1` in `base_url`), verify the forwarded principal, and own
their upstream's availability. A failing upstream must only degrade that
engine's extension features, never the common API.

## 11. Open questions

1. **Frontend call path.** Do frontends call `/api/ext/...` through the same
   gateway route they use for the adapter today, or do we expose a dedicated
   gateway domain? (Proposed: same route, no gateway change.)
2. **Principal handling in the adapter.** (a) Inbound: the adapter needs the
   gateway's verification key to check `X-Avernet-Principal`. (b) Outbound:
   re-sign for `aud=ext:{engine}` (needs signer access in the adapter, or a
   gateway token-exchange endpoint) vs. forward the inbound token unchanged
   (simpler, weaker isolation). Proposed: verify inbound; re-sign via gateway
   token exchange.
3. **Config source.** Static YAML per deployment (phase 1) vs. a Backend-owned
   registry so owners can self-register upstreams (later).
4. **Secrets.** Whether phase 1 needs a secret-store / mTLS backend beyond
   `env:` and `file:` references; if not, ship httpx + Noop + Mock first.
5. **Migration of existing engine-specific routes.** Move `/data/*`
   (AICoding `DataProxyService`) and `api/aicoding/*` behind
   `/api/ext/aicoding/*` with a deprecation window, or leave them as is.

## 12. Rollout plan

| Phase | Deliverable |
| --- | --- |
| 0 | This design doc reviewed and accepted (EN + 中文). |
| 1 | `plugin_api/ext_relay` Protocols, httpx / `Noop` / `Mock` impls, inbound principal verification, router, `EXT_RELAY` capability, `_manifest`, contract tests, unit tests (allow-list, normalization, header policy, error mapping, streaming). Disabled unless `ext_relay.enabled`. |
| 2 | WebSocket relay, rate limits, config hot reload, secret store / mTLS. |
| 3 | Migrate the first real engine-specific API (candidate: AICoding `/data/*`), with a deprecation window for the old path. |

## 13. Testing strategy

- **Contract tests** (`tests/contracts/test_ext_relay_contract.py`): every
  `ExtRelayService` impl (httpx, Noop, Mock) satisfies the same behaviour.
- **Unit tests**: path normalization and traversal rejection, glob matching,
  method allow-list, header drop/add tables, error → status mapping, body and
  concurrency limits, manifest intersection.
- **Integration tests**: an in-process fake upstream (httpx `MockTransport` /
  local ASGI app) covering pass-through status/body, SSE streaming, client
  disconnect cancellation, timeouts and unreachable upstream.
- **Security tests**: forged `X-Avernet-Principal`, `engine` mismatch,
  encoded traversal (`%2e%2e`, `%2f`), redirect not followed, credentials
  absent from upstream request and logs.
