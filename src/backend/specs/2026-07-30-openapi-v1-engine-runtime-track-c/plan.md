# Plan: Public API — Engine Runtime Surface (Track C)

## Approach

One new core module, `core/engine_runtime/`, owns the single thing all six
groups do: resolve the caller's bot (owner-scoped), resolve its device, forward
one HTTP call to the engine adapter, and normalise the engine's envelope into a
typed result. Six thin routers under
`adapters/http/openapi_v1/engine_runtime/` each map their group's payload
shapes and nothing else — the same division `openapi_v1/routines/router.py`
already uses against `CronRelayService`.

The relay is built on the two pieces that already carry cron to the engine in
production: `DeviceContextResolver` (the repo's single provider-resolution
point) and `DeviceAdapterTransport`. Track C generalises that path rather than
adding a second one.

The `connection` endpoint is the one handler that does not forward: it composes
a socket map from the bot's active engine, the engine's declared capabilities,
and the existing device connection service.

### Assumptions carried in from the spec's open questions

The five open questions were answered by the bots owner as "proceed with the
recommendations". They are load-bearing, so they are restated here as
assumptions — overturning any of them changes this plan:

| # | Assumption | Where it lands |
|---|---|---|
| 1 | Add an optional `warning` to `Envelope` | `contracts.py` — a shared Track B contract change |
| 2 | Unreachable device → immediate retryable `409`; **no auto-wake** | `EngineDeviceNotReadyError` → `ENVELOPE_ERRORS` |
| 3 | Connection expiry mirrors the internal 120-minute WS token TTL; no caller override | `connection/router.py` |
| 4 | Single owner; board row stays unassigned | docs only |
| 5 | Owner-only; no collaborator access | relay resolves the bot via owner-scoped lookup |

## Affected Components

- `src/backend/src/agentclaw/community/core/engine_runtime/` **(new)** — the
  relay: bot resolution, device resolution, forwarding, envelope normalisation,
  typed errors.
- `src/backend/src/agentclaw/community/api/engine_runtime_service.py` **(new)** —
  the Service API Protocol the routers Inject
  (`adapters/http` may not Inject a concrete `core/` service — enforced by
  `tests/community/architecture/test_http_adapter_layer_is_http_only.py`).
- `src/backend/src/agentclaw/community/di/modules/engine_runtime_module.py`
  **(new)** — Protocol → concrete singleton alias.
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/engine_runtime/`
  **(new)** — six routers + schemas.
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/contracts.py` —
  add `Envelope.warning`; add `501`/`504` to `ERROR_RESPONSES`.
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py` —
  add the four new error mappings to `ENVELOPE_ERRORS`.
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py` —
  register the new groups in `_SUBGROUPS`.
- **Read-only dependencies, not modified:**
  `core/devices/services/device_context_resolver.py`,
  `plugin_api/device_adapter_transport.py`,
  `core/bot_management/services/bot_service.py`,
  `core/devices/services/device_service.py`.
- **`src/engine` is not modified.** Track C is a pure consumer of the engine's
  existing HTTP surface.

## Data Model Changes

**None.** No new tables, no new columns, no migration, and nothing for the
README's out-of-band DDL section. Track C stores nothing; every read is a
pass-through to the device.

Isolation therefore needs no Track A stage: the relay resolves `bot_id` through
`BotService` scoped by `caller_owner_id(principal)`, and `BotModel` is already
guarded (Stage 1, PR #456). A foreign or cross-tenant `bot_id` raises
`BotNotFoundError`, already mapped to a masked `404` in `ENVELOPE_ERRORS`
(`responses.py:118`).

## API / Interface Changes

### New public routes — 17, all under `/openapi/v1/bots/{bot_id}`

| Method | Path | Engine route forwarded to | Response |
|---|---|---|---|
| GET | `/sessions` | `GET /api/sessions` | `Envelope[Page[Session]]` |
| POST | `/sessions` | `POST /api/sessions` | `201 Envelope[Session]` |
| GET | `/sessions/{session_id}` | `GET /api/sessions/{id}` | `Envelope[Session]` |
| DELETE | `/sessions/{session_id}` | `DELETE /api/sessions/{id}` | `Envelope[Deleted]` |
| GET | `/sessions/{session_id}/messages` | `GET …/messages` | `Envelope[Page[Message]]` |
| DELETE | `/sessions/{session_id}/messages` | `DELETE …/messages` | `Envelope[Deleted]` |
| PATCH | `/sessions/{session_id}` | `POST …/{id}/update` | `Envelope[Session]` |
| GET | `/engine/status` | `GET /api/engine/status` | `Envelope[EngineStatus]` |
| GET | `/engine/capabilities` | `GET /api/engine/capabilities` | `Envelope[EngineCapabilities]` |
| GET | `/engine/available` | `GET /api/engine/list` | `Envelope[list[EngineInfo]]` |
| GET | `/models` | `GET /api/models` | `Envelope[Page[Model]]` |
| GET | `/models/{model_id:path}` | `GET /api/models/{id:path}` | `Envelope[Model]` |
| GET | `/approvals/mode` | `POST /api/approvals/mode/get` | `Envelope[ApprovalMode]` |
| PUT | `/approvals/mode` | `POST /api/approvals/mode/set` | `Envelope[ApprovalMode]` |
| GET | `/approvals/modes` | `GET /api/approvals/modes` | `Envelope[list[str]]` |
| GET | `/nodes` | `GET /api/nodes` | `Envelope[Page[Node]]` |
| GET | `/connection` | *(none — composed)* | `Envelope[Connection]` |

Query/body models all carry `extra="forbid"`. `user_id`, `engine`,
`binding_id`, `device_uuid` and `agent_id` are **not** accepted on any route —
`user_id` is filled from the principal, `engine` from the bot's `active_engine`.

### `Envelope` gains an optional field

```python
warning: str = Field(default="", description=
    "Non-empty when the engine served this request with a documented "
    "limitation; the payload may be incomplete.")
```

Additive and defaulted, so every existing category keeps working; they will
simply serialize `"warning": ""`. `ErrorEnvelope` does **not** get it.

### `Connection` payload

```python
class Socket(BaseModel):
    url: str        # complete wss:// URL, opaque; caller concatenates nothing
    headers: dict[str, str]

class Connection(BaseModel):
    engine: str
    expires_at: str          # ISO 8601
    sockets: dict[str, Socket]   # "chat", optionally "terminal"
```

No `target`, no `type`, no bare `token` field.

### New Service API Protocol

`api/engine_runtime_service.py`:

```python
@runtime_checkable
class EngineRuntimeRelayProtocol(Protocol):
    async def call(self, *, bot_id: str, owner_id: str, method: str, path: str,
                   body: dict | None = None, params: dict | None = None,
                   timeout: float | None = None) -> EngineResult: ...
    async def capabilities(self, *, bot_id: str, owner_id: str) -> EngineCapabilitiesResult: ...
    def connection(self, *, bot_id: str, owner_id: str) -> ConnectionResult: ...
```

Must be registered as an `(EngineRuntimeRelayProtocol, EngineRuntimeRelay)` pair
in `tests/community/architecture/test_service_api_conformance.py`.

## Key Files & Functions

**New — core**

- `core/engine_runtime/README.md` — required `## Context Boundary` yaml block;
  copy the shape from `core/cron/README.md:5-25`.
- `core/engine_runtime/models.py` — `EngineResult(data, total, warning)`,
  `ConnectionResult`, `SocketInfo`.
- `core/engine_runtime/errors.py` — `EngineCapabilityUnsupportedError`,
  `EngineDeviceNotReadyError`, `EngineUpstreamError`. Semantic state only, **no
  HTTP status** (Rule 7 / `arch.rules.md:203`); the adapter maps them.
- `core/engine_runtime/relay.py` — `EngineRuntimeRelay`:
  - `_resolve_bot(bot_id, owner_id)` → `BotService.get_bot(bot_id, owner_id)`;
    raises `BotNotFoundError` for a bot that isn't the caller's. **This is the
    isolation seam** and must run before any device work.
  - `_resolve_device(bot_id, owner_id)` →
    `DeviceContextResolver.resolve_for_bot()`
    (`device_context_resolver.py:61`). Wrap `DeviceNotBoundError` /
    `ConnInfoBuildError` → `EngineDeviceNotReadyError`.
  - `call(...)` → `DeviceAdapterTransport.invoke(ctx.conn_info, method, path, …)`
    (`plugin_api/device_adapter_transport.py:59`), then `_normalise`.
  - `_normalise(raw)` — the one place the engine's
    `ApiResponse{success, data, message, warning, total}`
    (`src/engine/.../api/response.py:9`) becomes an `EngineResult`. A `200`
    carrying `success: false` raises `EngineUpstreamError`; it must never reach
    a caller.
  - Map `DeviceAdapterHTTPStatusError.status_code == 501`
    (`device_adapter_transport.py:36`) → `EngineCapabilityUnsupportedError`.
    That is how the engine reports an unsupported capability
    (`src/engine/.../api/caps.py:44`).

**New — adapter**

- `adapters/http/openapi_v1/engine_runtime/__init__.py` — exports the six
  routers.
- `…/engine_runtime/{sessions,engine,models,approvals,nodes,connection}/router.py`
  + `schemas.py`. Each handler: `@envelope_errors`, takes `request: Request`,
  `principal: PrincipalDep`, Injects `EngineRuntimeRelayProtocol`, calls
  `caller_owner_id(principal)`, and maps the payload. Mapping helpers live in
  the router, matching `openapi_v1/routines/router.py:52-95`.

**Modified**

- `openapi_v1/contracts.py:22` — add `Envelope.warning`.
- `openapi_v1/contracts.py:66` — add `501` and `504` to `ERROR_RESPONSES`.
- `openapi_v1/responses.py:113` — add to `ENVELOPE_ERRORS`, **before** the
  `BotServiceError` base entry at `responses.py:171`:
  - `EngineCapabilityUnsupportedError` → `(501, "Not supported by this bot's engine")`
  - `EngineDeviceNotReadyError` → `(409, "Bot device is not ready")`
  - `DeviceAdapterTimeoutError` → `(504, "Engine request timed out")`
  - `EngineUpstreamError` → `(502, "Engine service error")`
  - `DeviceAdapterEndpointNotFoundError` → `(501, "Not supported by this bot's engine")`
  - `DeviceAdapterHTTPStatusError` → `(502, "Engine service error")` — **last of
    this group**, it is the base for the specific transport failures.
- `openapi_v1/__init__.py:33` — add the six routers to `_SUBGROUPS`. They live
  one segment below the `{bot_id}` wildcard so ordering among themselves is
  free, but they must stay above `bots_router` so `/openapi/v1/bots/mcp` keeps
  resolving ahead of `/openapi/v1/bots/{bot_id}`.
- `di/modules/engine_runtime_module.py` (new) + registration in the composition
  root.
- `tests/community/architecture/test_service_api_conformance.py` — register the
  new pair.

## Dependencies

No new packages. No version bumps. Internally: `BotService`,
`DeviceContextResolver`, `DeviceAdapterTransport`, `DeviceService` (connection
only) — all already wired.

Note `plugins/community/device_adapter_transport.py:27` is a **no-op** returning
`{"success": False}`. In the community profile every Track C endpoint will
therefore answer `502`. That is correct (community ships no container runtime)
and matches how cron behaves today, but the tests must not assume a live device.

## Risks & Mitigations

- **Risk: `Page.total` is unknowable for sessions/models/nodes.** The engine's
  list handlers return a flat list and never populate `ApiResponse.total`
  (`src/engine/.../api/session/router.py:132`), but `Page.total` is a required
  `int` (`contracts.py:88`).
  **Mitigation:** follow the routines precedent
  (`openapi_v1/routines/router.py:126-131`) — fetch the engine's list, slice in
  the handler, report an exact `total`. Cap the engine-side request so a bot
  with thousands of sessions cannot force an unbounded fetch, and record the cap
  in the response docs. Revisit if/when the engine reports `total`.

- **Risk: the connection endpoint needs two device calls** (capabilities, then
  connection), doubling its failure surface.
  **Mitigation:** derive the **chat** socket from the bot's `active_engine` — a
  backend fact, no device call — and only consult capabilities for the optional
  `terminal` socket. A capabilities failure then fails the whole endpoint with
  the same `409` as everything else, rather than silently omitting a socket.

- **Risk: `expires_at` may not match the token the provider actually issued.**
  `DeviceConnectionInfo` (`core/devices/models.py:115`) has no expiry field,
  while the BaaS path's `BotWsConnectionInfoResponse`
  (`core/service_bot/services/baas_service.py:130-133`) does.
  **Mitigation:** prefer the provider-reported expiry where it exists; fall back
  to `now + ttl` using the TTL we passed. Do **not** hardcode 120 minutes in the
  handler independently of the TTL requested — a computed expiry that disagrees
  with the real token is worse than none.

- **Risk: `get_device_connection` applies a public-bot / collaborator permission
  model** (`core/devices/services/device_service.py:974-1006`) that is **wider
  than owner-only**, contradicting assumption 5.
  **Mitigation:** the relay resolves the bot owner-scoped *first* and passes the
  resolved owner as the operator, so the wider check can never widen the public
  surface. Do not call the connection service with the raw caller identity.

- **Risk: session ids are not path-safe.** They look like
  `agent:main:session:2d20…:user:165137`, and the frontend URL-safe-base64
  encodes them (`src/engine/docs/session-favorites-api.zh-CN.md:19-31`).
  **Mitigation:** settle one public encoding, document it on every session
  route, and cover both a plain and an encoded id in tests. Do **not** invent a
  second encoding — reuse what the session list returns as `id`.

- **Risk: `Envelope.warning` changes a contract shared with all seven existing
  categories.**
  **Mitigation:** additive with a default, and the public surface answers `401`
  to everything until the auth workstream lands, so there are no external
  clients to break. Assert the new key's presence in the existing
  `tests/.../test_responses.py` rather than letting it drift.

- **Risk: capability answers differ per engine**, so the same public path
  behaves differently across two of a tenant's own bots.
  **Mitigation:** that is why `/engine/capabilities` ships in v1 rather than
  being deferred. Document it as the discovery endpoint in every `501` message.

## Alternatives Considered

- **One relay method per engine group** (`list_sessions`, `get_model`, …) on the
  Protocol, mirroring `CronRelayServiceProtocol`. Rejected: cron's per-verb
  Protocol exists because cron has real relay *logic* (multi-bot fan-out,
  runtime-stage targeting). Track C is a pure forward, so seventeen Protocol
  methods would be seventeen ways to spell `call()`. Rule 19
  (`arch.rules.md:569`) — abstract after two examples, not before.
- **Six independent relays, one per group.** Rejected: the normalisation,
  isolation, and error mapping are identical; six copies would drift, which is
  exactly what #494's "extract, don't copy" gotcha warns about.
- **A generic pass-through** (`/openapi/v1/bots/{bot_id}/engine/{path:path}`).
  Rejected outright: it republishes the engine's entire surface — including the
  71 routes deliberately excluded — as a public contract, and makes the
  scope ruling unenforceable.
- **Auto-wake on an unreachable device.** Rejected per assumption 2: it couples
  every runtime read to device provisioning and turns a fast `409` into a
  multi-second block. `POST /openapi/v1/bots/{bot_id}/restart` already exists
  for the caller who wants that.
- **Putting mapping in `core/`** rather than the routers. Rejected for
  consistency with `openapi_v1/routines/router.py`, though it is a genuine
  tension with Rule 7's thin-adapter requirement. Worth revisiting for all of
  Track B/C at once, not unilaterally here.

## Rollout

- **No feature flag.** The whole public surface answers `401` until the auth
  workstream swaps `require_principal` (`openapi_v1/dependencies.py`), so Track
  C ships dark by construction — the same posture as bots in #494.
- **No migration, no DDL, no deploy ordering constraint.**
- **Backwards compatibility:** the only shared change is the additive
  `Envelope.warning`. Internal `/api` routes are untouched.
- Ship as one PR per the README's per-category convention, or split
  `sessions + engine + connection` (P1) from `approvals + models + nodes`
  (P2/P3) if review size becomes the constraint. The shared relay must land in
  the first PR either way.

## Test Strategy

**Unit**
- `_normalise`: engine envelope → `EngineResult`; `success: false` raises;
  `warning` and `total` carried through.
- Transport-error translation: `501` → `EngineCapabilityUnsupportedError`;
  timeout → `DeviceAdapterTimeoutError`; other non-2xx → `EngineUpstreamError`.
- `ENVELOPE_ERRORS` ordering: assert the specific transport errors resolve
  before `DeviceAdapterHTTPStatusError` and before `BotServiceError`. The
  README's Track B gotcha ("map the base class last") is a real regression risk
  here because this change adds two base/leaf pairs at once.
- Connection composition: socket map per engine; `terminal` present only when
  the capability is declared; no `target`/`type`/`token` key in the payload.

**Endpoint** (per group, using the in-memory transport at
`plugins/local/device_adapter_transport.py` so the relay runs end-to-end)
- Every one of the 17 routes: success shape, `Envelope`/`Page` conformance.
- Each mapped error: `409` not-ready, `501` unsupported, `502` upstream,
  `504` timeout.
- `extra="forbid"` rejection of `user_id`, `engine`, `binding_id`,
  `device_uuid` → `422`.

**Isolation** (against the real Track A guard, mirroring
`tests/.../test_bots_tenant_isolation.py`)
- A `bot_id` owned by another caller → masked `404`, byte-identical to a
  non-existent bot, on **all 17 routes**.
- A cross-tenant `bot_id` → same masked `404`.
- No device call is issued for a bot the caller does not own — assert the
  transport was never invoked, not merely that the status was 404.

**Architecture gates**
- `tests/community/architecture/` after the new cross-module imports; declare
  them in `core/engine_runtime/README.md`'s Context Boundary. The README's
  Stage-1 gotcha notes this gate failed CI twice for exactly this omission.
- `test_service_api_conformance.py` — register the new Protocol pair.
- `test_http_adapter_layer_is_http_only.py` — routers must Inject the Protocol,
  never `EngineRuntimeRelay`.

**Unchanged**
- Full `tests/community` green with the internal suite **unmodified**.
