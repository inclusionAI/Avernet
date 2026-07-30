# Track C — Engine (runtime) surface: what `/openapi/v1` wraps and what it doesn't

**English** | [简体中文](engine-surface.zh-CN.md)

_Reference companion to [`README.md`](README.md). The README is the living
status board; this file is the stable ruling: **every engine endpoint, and
whether the public API wraps it.** Read the README first for who owns what and
where the effort stands._

---

## Why Track C exists

Tracks A and B assume the public API's data lives in **backend tables**. The
bot's *runtime* doesn't. Sessions, chat, approvals, models, node inventory —
these live on the bot's device, served by the **engine adapter** (`src/engine`,
port `20003`), and today the client reaches them **directly**:

1. Frontend calls `GET /api/v1/devices/bots/{bot_id}/connection`
   (`adapters/http/devices/router.py:476`) → `{type, target, token, engine_type,
   url, available}`.
2. Frontend rewrites matching request paths to `/proxypass/{target}{path}` with
   an `X-PROXYPASS-TOKEN` header, or `http://{target}{path}` in local mode
   (`src/frontend/src/requestConfig.ts:150-260`).

That is fine for the internal TeamClaw frontend and wrong for an external
tenant. It publishes proxypass topology and a raw device token, and it makes the
**engine** — which was never designed as a public contract — the surface an
integrator codes against.

**Track C wraps the engine's client-facing HTTP behind `/openapi/v1/bots/{bot_id}/…`
and replaces the connection hand-off with one sanitised socket-info endpoint.**

Two properties make this cheaper than Tracks A and B:

- **No Track A stage, no DDL.** Every engine call is keyed by `bot_id`, and bots
  are tenant-isolated already (Stage 1, PR #456). Track C inherits that the same
  way `identity` does. It adds **no tables**.
- **The transport already exists.** `DeviceContextResolver` (the repo's single
  provider-resolution point) → `DeviceAdapterTransport.invoke()` /
  `.stream()` (`plugin_api/device_adapter_transport.py`). `CronRelayService`
  has run this path in production for `/api/cron` since before this effort
  started, and `openapi_v1/routines/router.py:29` already imports
  `CronRelayServiceProtocol`. **Routines is Track C's worked precedent** — read
  it before writing a handler.

---

## The scope rule (this is the whole decision)

The engine serves **89 HTTP routes + 6 WebSocket endpoints across 25 routers**.
Track C does not wrap most of them. Four rules decide each one:

| # | Rule | Consequence |
|---|---|---|
| **C1** | **Frontend → engine directly over HTTP** → **wrap it.** | These are the endpoints with no backend representation today. A public caller has no other way to reach them, so the public API must provide one. |
| **C2** | **Frontend → backend → engine** → **out of scope.** | The backend already owns a public-facing contract for these; wrapping the engine route again would create a second, divergent path to the same behavior. `/api/cron` is the clearest case — it is already the `routines` category. |
| **C3** | **WebSocket** → **do not wrap.** | The public API returns the socket URL and the headers to send; the caller opens the socket itself. Relaying frames would republish the engine's internal frame format as a public contract. |
| **C4** | **AICoding-only** → **out of scope.** | Product-specific surface, not part of the tenant contract. |

The authoritative list for C1 is the proxypass prefix list in
`src/frontend/src/requestConfig.ts:189-205` — the exact set of path prefixes the
frontend rewrites to the engine.

---

## The public surface — 17 endpoints

All bot-scoped under `/openapi/v1/bots/{bot_id}/…`, all returning the
`Envelope[T]` / `Page[T]` shapes from `openapi_v1/contracts.py`.

### sessions (7) — engine `/api/sessions`

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/sessions` | `GET /api/sessions` | `agent_id`, `session_key`, paged → `Envelope[Page[Session]]` |
| POST | `…/sessions` | `POST /api/sessions` | `201 Envelope[Session]` |
| GET | `…/sessions/{session_id}` | `GET /api/sessions/{session_id}` | `Envelope[Session]` |
| DELETE | `…/sessions/{session_id}` | `DELETE /api/sessions/{session_id}` | `Envelope[Deleted]` |
| GET | `…/sessions/{session_id}/messages` | `GET …/messages` | paged → `Envelope[Page[Message]]` |
| DELETE | `…/sessions/{session_id}/messages` | `DELETE …/messages` | clear history → `Envelope[Deleted]` |
| PATCH | `…/sessions/{session_id}` | `POST …/{session_id}/update` | **divergence:** partial update is `PATCH` on the resource publicly, not a `/update` sub-path |

### engine, read-only (3) — engine `/api/engine`

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/engine/status` | `GET /api/engine/status` | process / transition phase / connection count |
| GET | `…/engine/capabilities` | `GET /api/engine/capabilities` | **the most important endpoint in Track C** — see *Capabilities* below |
| GET | `…/engine/available` | `GET /api/engine/list` | **divergence:** `list` is a verb path; public uses a noun. Registered engines + active flag + version |

> **`POST /api/engine/switch` and `POST /api/engine/restart` are deliberately
> NOT wrapped.** PR #494 made `engine` immutable on `PUT /openapi/v1/bots/{bot_id}`
> (`extra="forbid"` → 422); wrapping `switch` would be a back door around that
> ruling. And `POST /openapi/v1/bots/{bot_id}/restart` already re-provisions the
> device, so wrapping `restart` would give one bot two restart verbs with
> different blast radii. _Decided 2026-07-30._

### models (2) — engine `/api/models`

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/models` | `GET /api/models` | `Envelope[Page[Model]]` |
| GET | `…/models/{model_id}` | `GET /api/models/{model_id:path}` | **model ids contain slashes** (`openai/gpt-5.3`). The engine uses a `:path` converter; the public route must settle URL-encoding vs. a `:path` converter and document it |

### approvals (3) — engine `/api/approvals`

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/approvals/mode` | `POST /api/approvals/mode/get` | **divergence:** a read is `GET` with `session_key` as a query param, not a `POST` |
| PUT | `…/approvals/mode` | `POST /api/approvals/mode/set` | body `{session_key, mode}` |
| GET | `…/approvals/modes` | `GET /api/approvals/modes` | static enum; note this is the one engine route with **no** capability gate |

### nodes (1) — engine `/api/nodes`

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/nodes` | `GET /api/nodes` | `status`, `platform`, paged → `Envelope[Page[Node]]` |

### connection (1) — NEW, no engine counterpart

`GET /openapi/v1/bots/{bot_id}/connection` → `Envelope[Connection]`

The public replacement for `get_device_connection`. Returns ready-to-use
sockets, never proxypass topology:

```jsonc
{
  "engine": "openclaw",
  "expires_at": "2026-07-30T12:34:56Z",
  "sockets": {
    "chat": {
      "url": "wss://<host>/proxypass/<target>/api/openclaw/ws",
      "headers": { "x-proxypass-token": "<scoped token>" }
    }
  }
}
```

Rules this endpoint must hold:

- **Only sockets the bot's active engine actually serves appear.** Absent key =
  unsupported. `terminal` appears only when the engine declares
  `WEB_SHELL_OPEN`; `chat` resolves to `/api/openclaw/ws` or
  `/api/claude_code/ws` depending on the active engine (the engine also serves a
  generic `/api/{engine}/ws`, `api/app.py:310`).
- **The URL is opaque and complete.** Callers concatenate nothing. `target`,
  `type` and the bare `token` are *not* fields — they are what we are trying to
  stop publishing.
- **`expires_at` is mandatory** so a caller knows to re-fetch rather than
  silently failing on an expired token (the internal WS token TTL is 120 min —
  `core/grt_chat/services/grt_chat_service.py:25`).
- The socket set is **capability-derived**, so this endpoint and
  `…/engine/capabilities` must never disagree.

---

## Full engine inventory and the ruling on each

| Engine router | Prefix | HTTP | WS | Ruling | Why |
|---|---|---|---|---|---|
| `api/session` | `/api/sessions` | 7 | — | ✅ **C1 — wrap** | in the frontend proxypass list |
| `api/engine` | `/api/engine` | 5 | — | ✅ **C1 — wrap 3 of 5** | `switch`/`restart` excluded, see above |
| `api/models` | `/api/models` | 2 | — | ✅ **C1 — wrap** | in the proxypass list |
| `api/approvals` | `/api/approvals` | 3 | — | ✅ **C1 — wrap** | in the proxypass list |
| `api/node` | `/api/nodes` | 1 | — | ✅ **C1 — wrap** | in the proxypass list |
| `api/cron` | `/api/cron` | 10 | — | ⛔ **C2** | **already the `routines` category** — backend `/api/cron` → `CronRelayService` → engine. Explicitly commented out of the frontend proxypass list (`requestConfig.ts:195`) |
| `api/file` | `/api/file` | 5 | — | ⛔ **C2** | backend calls `/api/file/{read,upload,list,remove,rmtree}` server-side; frontend never proxypasses it |
| `api/skills` | `/api/skills` | 10 | — | ⛔ **C2** | backend `skills_pool` / `skill_center` drive layout, symlink and bindpath ops. Internal filesystem mechanics, no tenant-facing contract |
| `api/mcp` | `/api/mcp` | 10 | — | ⛔ **C2** | backend pushes MCP config to devices; the public `mcp` category (marketplace + caller config) is the tenant contract |
| `api/resource_materialization` | `/api/resource-materializations` | 2 | — | ⛔ **C2** | backend `session_resources` calls it server-side |
| `api/bot` | `/api/bot` | 1 | — | ⛔ **C2** | `POST /config` — backend `bot_public` sync path |
| `api/bash` | `/api/bash` | 1 | — | ⛔ **C2** | backend exposes `POST /api/v1/devices/exec_shell`; arbitrary shell on a tenant device is not a v1 public contract regardless |
| `api/work_item` | `/api/work-items` | 3 | — | ⛔ **C2** | backend-mediated |
| `api/session_favorites` | `/api/session-favorites` | 3 | — | 🟡 **deferred** | the engine's own doc calls it frontend-direct via the engine proxy, but it is **absent from the proxypass prefix list** and has zero references in `src/frontend`. Likely corp-frontend-only. Additive later; excluded from v1. _Decided 2026-07-30._ |
| `api/routers/openclaw_http` | `/api/openclaw` | 3 | — | 🟡 **deferred** | `test-connection` / `disconnect` / `config`. Listed in the proxypass array as `'api/openclaw'` — **no leading slash**, so `url.startsWith()` never matches `/api/openclaw/...` and the entry is dead as written (`requestConfig.ts:191`). Also openclaw-specific gateway debug tooling. _Decided 2026-07-30._ |
| `api/default_config` | `/api/openclaw` | 1 | — | 🟡 **deferred** | same dead prefix entry |
| `api/zero_check` | `/api/openclaw/zero-check` | 2 | — | 🟡 **deferred** | same dead prefix entry |
| `api/web_shell` | — | 2 | 1 | ⛔ **C3 / not v1** | `GET /terminal`, `/terminal/health`, `WS /ws/terminal`. The socket is reachable through `…/connection` when the engine declares `WEB_SHELL_OPEN`; the two HTTP routes are the shell's own bootstrap |
| `api/routers/ws` | — | — | 1 | 🔌 **C3 — connection info** | `/api/openclaw/ws` |
| `api/routers/claude_code_ws` | `/api/claude_code` | — | 1 | 🔌 **C3 — connection info** | `/api/claude_code/ws` |
| `openclaw/router` | `/api/openclaw` | — | 1 | 🔌 **C3** | `/client` — gateway-side socket, not a tenant socket |
| `api/app` (module-level) | — | 6 | 2 | ⛔ / 🔌 | `/health`, `/readiness`, `/config`, `/test-connection`, `/disconnect`, `/api/evaluation/report` are ops surface. `WS /ws` and `WS /api/{engine}/ws` are the generic chat sockets → `…/connection` |
| `api/aicoding_sessions` | `/api/aicoding/sessions` | 10 | — | ⛔ **C4** | aicoding-only |
| `api/aicoding/skill_router` | `/api/aicoding` | 1 | — | ⛔ **C4** | aicoding-only |
| `api/aicoding/data_proxy_router` | `/data` | 1 | — | ⛔ **C4** | harness-data reverse proxy |

Two prefixes in the frontend's proxypass list — **`/api/teclaw`** and
**`/api/notify`** — have **no router in the OSS engine**. They belong to
corp/teclaw builds. Nothing to wrap; if they ever land in this repo they are C1
candidates and this table must be revisited.

---

## Contract mechanics every Track C handler inherits

Track C reuses the Track B primitives (`responses.py`, `contracts.py`,
`principal.py`, `errors.py` — see the README's *Track B — the reusable
primitives*). These five are **new to Track C** and should be built once, not
per group.

### 1. Two envelopes have to become one

The engine returns `ApiResponse{success, data, message, warning, total}`
(`src/engine/.../api/response.py`); the public API returns
`Envelope[T]` / `Page[T]`. One mapping helper, not seven:

- `data` → `Envelope.data`, with a per-group shape mapper.
- `total` → `Page.total`.
- `success: false` → raise, don't pass through — a `200` carrying
  `success: false` must never reach a public caller.
- **`warning` has no home in the public contract yet.** It is the
  capability-*limited* signal ("this engine can only list the current session"),
  and dropping it silently degrades the answer. **Recommendation: add an optional
  `warning` field to `Envelope`.** This is the one change Track C makes to a
  shared Track B contract, so it needs the bots owner's sign-off.

### 2. Capabilities are the public contract's escape hatch

Every engine handler calls `check_capability()` (`src/engine/.../api/caps.py`):
unsupported → **501** (with the engine's declared `fallback` string), limited →
a `warning` in the body. The supported set differs per engine (`openclaw`,
`claude_code`, `aicoding`, `teclaw`, `hermes`), so **the same public path
answers differently for two of a tenant's own bots.**

- `CapabilityNotSupportedError` / the transport's 501 need an `ENVELOPE_ERRORS`
  entry with a fixed public message pointing the caller at
  `…/engine/capabilities`.
- That is why `…/engine/capabilities` is in the v1 surface and not deferred: it
  is how a caller discovers, ahead of time, which of the other 16 endpoints its
  bot will actually answer.

### 3. `user_id` must come from the principal, never the caller

Several engine routes take `user_id` as a **query parameter**
(`GET /api/sessions`, `POST /api/approvals/mode/get`, `/api/session-favorites`).
On the public surface these must be filled from `caller_owner_id(principal)` and
**rejected if present in the request** (`extra="forbid"` on bodies, explicit
omission from the query model). A caller-supplied `user_id` forwarded verbatim
is a cross-caller read inside the same tenant — the isolation guard cannot catch
it, because the engine has no tenant axis at all.

The same applies to the `engine=` override on `/api/sessions` and
`/api/models`: **the bot's active engine is authoritative.** Do not expose it.

### 4. Device readiness is a public error, not a 500

A cold, dormant or restarting device makes every engine call fail at the
transport. Reuse `core/bot_management/readiness.py` (extracted in #494) rather
than inventing a second policy, and settle **one** behavior for all 17
endpoints: masked `409 device not ready` vs. auto-wake-then-retry. Whatever is
chosen, `GET /openapi/v1/bots/{bot_id}/status` stays the endpoint that tells a
caller *why*.

### 5. Errors from the transport

`DeviceAdapterTransport` raises `DeviceAdapterEndpointNotFoundError` (404 from
the device — an endpoint this runtime doesn't serve),
`DeviceAdapterHTTPStatusError` (any other non-2xx) and
`DeviceAdapterTimeoutError`. All three need `ENVELOPE_ERRORS` entries. Per the
README's Track B gotcha, **map the base class last** — `ENVELOPE_ERRORS` returns
on the first `isinstance` match in insertion order.

---

## Isolation: what Track C does and does not need

- **No Track A stage.** No new tables; nothing to add `avernet_tenant` to.
- **No DDL.** Nothing for the out-of-band schema section in the README.
- **Isolation comes entirely from the `bot_id` lookup.** Every handler resolves
  the bot through the bot service scoped by `caller_owner_id(principal)` *before*
  touching the device. A foreign or cross-tenant `bot_id` must be a **masked
  404**, byte-identical to "no such bot" — the Track A guard on `BotModel`
  delivers this for free, exactly as it does for `identity`.
- **The device is reached only through the resolved bot.** No handler may accept
  a `binding_id`, `device_uuid`, or `target` from the caller.

---

## Routing note

The new groups sit at `/openapi/v1/bots/{bot_id}/{sessions,engine,models,approvals,nodes,connection}`
— one segment **below** the `{bot_id}` wildcard, so they do not need the
literal-subgroups-first ordering that `_SUBGROUPS` enforces in
`openapi_v1/__init__.py:32-40`. They must still be registered so that
`/openapi/v1/bots/mcp` (the literal marketplace group) keeps resolving ahead of
`/openapi/v1/bots/{bot_id}`.

Watch one near-collision: bot-scoped MCP would be
`/openapi/v1/bots/{bot_id}/mcp/...`, which is a **different resource** from the
existing marketplace group at `/openapi/v1/bots/mcp/...`. Track C does not add
it (rule C2), but the two must never be merged by a later reader.

---

## Decisions taken

- **2026-07-30 — Scope rule adopted (C1–C4).** Wrap engine HTTP the frontend
  reaches directly; leave backend-mediated engine calls to the backend contract
  that already fronts them; return connection info for sockets instead of
  relaying them; exclude aicoding.
- **2026-07-30 — v1 surface fixed at 17 endpoints**: sessions 7, engine 3,
  models 2, approvals 3, nodes 1, connection 1.
- **2026-07-30 — `engine/switch` and `engine/restart` excluded**, to preserve
  #494's engine-immutability ruling and avoid two restart verbs.
- **2026-07-30 — `session-favorites` and the `/api/openclaw` HTTP trio
  (+ default-config, zero-check) deferred**, not cancelled. Both are additive:
  adding them later breaks no published contract.
- **2026-07-30 — chat stays a WebSocket.** No `POST /chat`, no SSE. The public
  API hands back a URL and headers; the caller owns the socket.

## Open questions for the SDD

1. **`warning` on `Envelope`** — add the optional field (recommended), send a
   response header, or drop the signal? Touches a shared Track B contract.
2. **Readiness behavior** — masked `409` or auto-wake-then-retry, uniformly
   across all 17.
3. **`model_id` with slashes** — `:path` converter or mandatory URL-encoding.
4. **Pagination** — the engine takes `limit`/`offset`; the public `Page` shape
   must map cleanly, including for `/api/models` and `/api/nodes`, which return
   flat lists with no `total`.
5. **Timeouts** — `DeviceAdapterTransport.invoke()` takes a per-call timeout; the
   public surface needs one documented deadline per group.
