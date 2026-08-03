# Track C — Engine (runtime) surface: what `/openapi/v1` wraps and what it doesn't

**English** | [简体中文](engine-surface.zh-CN.md)

_Reference companion to [`README.md`](README.md). The README is the living
status board; this file is the stable ruling: **every engine endpoint, and
whether the public API wraps it.** Read the README first for who owns what and
where the effort stands._

---

## Why Track C exists

Tracks A and B assume the public API's data lives in **backend tables**. The
bot's *runtime* doesn't. Sessions, chat, approvals, models —
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

**Track C wraps the engine's client-facing HTTP behind `/openapi/v1/bots/<component>/{bot_id}/…`
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
| **C3** | **WebSocket** → **do not wrap.** | The public API returns one complete socket URL, credential included; the caller opens the socket itself. Relaying frames would republish the engine's internal frame format as a public contract. |
| **C4** | **AICoding-only** → **out of scope.** | Product-specific surface, not part of the tenant contract. |

The authoritative list for C1 is the proxypass prefix list in
`src/frontend/src/requestConfig.ts:189-205` — the exact set of path prefixes the
frontend rewrites to the engine.

---

## The public surface — 16 endpoints

All bot-scoped under `/openapi/v1/bots/<component>/{bot_id}/…`, all returning
the `Envelope[T]` / `Page[T]` shapes from `openapi_v1/contracts.py`.

> **Every path begins with the literal `/openapi/v1/bots/` prefix.** The tables
> below abbreviate it as `…` for width only. This is a hard invariant, not a
> style: it keeps Track C consistent with the existing categories, and **the
> gateway forwards to agentclaw on that prefix**, so a route mounted anywhere
> else is unreachable in production. A test asserts it.
>
> **The component's name comes before `{bot_id}`.** These five shipped as
> `/openapi/v1/bots/{bot_id}/<component>/…` and were normalized on 2026-08-03 —
> see the **Addressing rule** in [`README.md`](README.md). The tables below use
> the current addresses.

### sessions (7) — engine `/api/sessions`

> **Personal bots only.** All seven routes answer `501 "Not supported for this
> bot type"` on a `service` bot, checked before any device call. The engine
> accepts `user_id`, logs it, and **drops it** — `sessions_list()` has no
> `user_id` parameter (`plugins/openclaw/_session.py:125-132`), so the device
> returns every session it holds. On a personal bot that set is the owner's; on
> a service bot, whose device serves many callers, it is everyone's. Filtering
> on the `user:<id>` suffix the session key already carries is the right fix but
> belongs in the **engine** — a backend filter over an unfiltered device response
> is bypassable. Widening to both bot types later breaks no contract.
> _Decided 2026-07-30._

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/sessions/{bot_id}` | `GET /api/sessions` | `agent_id`, `session_key`, paged → `Envelope[SessionPage]` |
| POST | `…/sessions/{bot_id}` | `POST /api/sessions` | `201 Envelope[Session]` |
| GET | `…/sessions/{bot_id}/{session_id}` | `GET /api/sessions/{session_id}` | `Envelope[Session]` |
| DELETE | `…/sessions/{bot_id}/{session_id}` | `DELETE /api/sessions/{session_id}` | `Envelope[Deleted]` |
| GET | `…/sessions/{bot_id}/{session_id}/messages` | `GET …/messages` | paged → `Envelope[MessagePage]` |
| DELETE | `…/sessions/{bot_id}/{session_id}/messages` | `DELETE …/messages` | clear history → `Envelope[Deleted]` |
| PATCH | `…/sessions/{bot_id}/{session_id}` | `POST …/{session_id}/update` | **divergence:** partial update is `PATCH` on the resource publicly, not a `/update` sub-path. Body is `title`/`model` only — see below |

Two things about this group are not the shape a reader would assume:

- **`total` is a lower bound, not a count.** Both paged routes answer with
  `SessionPage` / `MessagePage` — `Page` subclasses whose `total` says *at least
  this many exist*, exact once you reach a page shorter than `page_size`. The
  engine reports no count for either collection, and the only way to compute one
  is to read every record: for sessions that fans out a `chat.history` call per
  session. A number that is honest about being a bound beats one that is wrong.
  These are the only two list endpoints on the API that do not report an exact
  total; every other category reads a database we own.
- **The two paged routes do not paginate the same way, and one of them does not
  paginate at all.** The session list paginates a materialised list, so
  `offset`/`limit` mean what they say and are forwarded. The message history
  **tail-limits**: `limit` selects the *newest* N messages — that is what both
  bundled providers do (`items[-limit:]`) and all the `chat.history` RPC they
  mirror offers — and the adapter then applies `offset` to that tail. The two
  cancel: growing `limit` to cover the offset moves the tail's start back by
  exactly the offset, so with 100 messages and `page_size=20` pages 1 and 2 both
  returned messages 79–98, and the newest message was spent as the lookahead and
  never shown. A page-sized limit instead leaves every page past the first
  empty. So the history route sends **no offset** and asks for the newest
  `offset + page_size + 1`, cutting the page out of that tail itself.
- **History pages run newest-first**, chronological within a page. That is a
  consequence of the above, not a preference: reaching the oldest page directly
  would mean fetching the entire history, and there is no count to size that
  request from. It also makes `MessagePage.total` *exact* whenever the tail
  comes back short, since a short tail is the whole history. _Decided
  2026-07-30._
- **`PATCH` accepts `title` and `model` only.** A working directory was offered
  and withdrawn: of the two bundled engines one applies it and the other
  discards it without saying so, which would make the same request succeed and
  do nothing depending on which engine the bot runs. A caller still sending it
  gets a 422 rather than a silent no-op. _Decided 2026-07-30._

### engine, read-only (3) — engine `/api/engine`

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/engine/{bot_id}/status` | `GET /api/engine/status` | process / transition phase / connection count |
| GET | `…/engine/{bot_id}/capabilities` | `GET /api/engine/capabilities` | **the most important endpoint in Track C** — see *Capabilities* below |
| GET | `…/engine/{bot_id}/available` | `GET /api/engine/list` | **divergence:** `list` is a verb path; public uses a noun. Registered engines + active flag + version |

> **`POST /api/engine/switch` and `POST /api/engine/restart` are deliberately
> NOT wrapped.** PR #494 made `engine` immutable on `PUT /openapi/v1/bots/{bot_id}`
> (`extra="forbid"` → 422); wrapping `switch` would be a back door around that
> ruling. And `POST /openapi/v1/bots/{bot_id}/restart` already re-provisions the
> device, so wrapping `restart` would give one bot two restart verbs with
> different blast radii. _Decided 2026-07-30._

### models (2) — engine `/api/models`

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/models/{bot_id}` | `GET /api/models` | `Envelope[Page[Model]]` |
| GET | `…/models/{bot_id}/{model_id}` | `GET /api/models/{model_id:path}` | **model ids contain slashes** (`openai/gpt-5.3`). The engine uses a `:path` converter; the public route must settle URL-encoding vs. a `:path` converter and document it |

### approvals (3) — engine `/api/approvals`

| Method | Public path | Engine route | Notes |
|---|---|---|---|
| GET | `…/approvals/{bot_id}/mode` | `POST /api/approvals/mode/get` | **divergence:** a read is `GET` with `session_key` as a query param, not a `POST` |
| PUT | `…/approvals/{bot_id}/mode` | `POST /api/approvals/mode/set` | body `{session_key, mode}`; a refusal (`data.ok=false` under an outer success) is a 502, not a 200 — see below |
| GET | `…/approvals/{bot_id}/modes` | `GET /api/approvals/modes` | static enum; note this is the one engine route with **no** capability gate |

### connection (1) — NEW, no engine counterpart

`GET /openapi/v1/bots/connection/{bot_id}` → `Envelope[Connection]`

The public replacement for `get_device_connection`. Returns ready-to-use
sockets, never proxypass topology:

```jsonc
{
  "engine": "openclaw",
  "expires_at": "2026-07-30T12:34:56Z",
  "sockets": [
    {
      "kind": "chat",
      "url": "wss://<gateway>/openapi/v1/engine/<target>/api/openclaw/ws?x-proxypass-token=<scoped token>"
    }
  ]
}
```

`sockets` is a **list whose `kind` is an enum** (`chat` in v1), not an
object keyed by kind. An enum-keyed object generates as `additionalProperties`
plus `propertyNames`, which most client generators drop or flatten to an untyped
map — the enum would then be documentation only. A list of records generates a
real typed enum everywhere and extends cleanly to a third socket.

Rules this endpoint must hold:

- **`chat` is the only socket in v1.** It resolves to `/api/openclaw/ws` or
  `/api/claude_code/ws` depending on the active engine, falling back to the
  generic `/api/{engine}/ws` (`api/app.py:310`) so a newly-added engine stays
  reachable. **No `terminal` socket** — it was implemented and then removed:
  `spec.md` excludes "arbitrary command execution and interactive shell on a
  tenant's device … at any scope" from v1, and this reference previously
  contradicted it. `SocketKind` stays an enum over a list so a
  second socket is additive. _Corrected 2026-07-30._
- **The URL is opaque and complete, credential included.** Callers concatenate
  nothing. `target`, `type` and a bare `token` are *not* fields — handing over
  the pieces to build an address is what this endpoint exists to stop. The
  credential rides in the URL's query string rather than a companion header
  because the consumer is a browser: `new WebSocket(url, protocols)` accepts no
  headers, so a URL is the only place it can travel. The internal console opens
  this same socket the same way. _Corrected 2026-07-31._
- **The address is the gateway's, not the hop behind it.** The published origin
  comes from the `gateway` config block (`base_url` / `base_url_pre`, selected
  by env), under an `/openapi/v1/engine/{target}{path}` prefix the gateway
  rewrites onto that hop. A deployment that fronts no gateway — the community
  build's normal state — is a named upstream error, not a 500 and not a
  published address nothing serves. The prefix sits inside the published API
  namespace, not at the host root: `engine` is an ordinary gateway domain,
  resolved by the same leading-segment lookup as `bots`, so the socket lives in
  the same surface as the endpoint that hands it out. _Corrected 2026-08-02._
- **The provider's URL is re-addressed, not rebuilt.** The device connection is
  still requested in **`ws_conn_mode="relay"`**, and the provider still builds a
  finished URL around the engine path it is given — that path passthrough is why
  a `claude_code` bot is not handed openclaw's default and rejected with 4001 on
  connect. Exactly two things then change: the origin becomes the gateway's, and
  the hop's `/proxypass/` prefix becomes `/openapi/v1/engine/`. Everything past that prefix
  — target, engine path, any query the provider set — is carried through as the
  provider wrote it, so this endpoint holds no opinion about a URL grammar it
  does not own and cannot silently drop a part it did not anticipate. A provider
  URL of a shape the `/engine` prefix cannot express — BaaS's LOCAL platform
  answers `/wsrelay/{session_id}` — is refused rather than published, so a wrong
  assumption surfaces server-side instead of as a socket that will not open.
  _Corrected 2026-07-31._
- **`expires_at` is mandatory** so a caller knows to re-fetch rather than
  silently failing on an expired token. It is **the issuer's own value** wherever
  the issuer states one: the BaaS path documents that it *ignores* the requested
  TTL and decides server-side, so a locally computed expiry there describes a
  token that does not exist. `DeviceConnectionInfo.expires_at` carries the stated
  value, and it is filled only on the paths where it really describes the token
  being returned — the local path normally hands back an HTTP token whose expiry
  BaaS does not state, and fills the field only when it falls back to the WS
  token. Where nothing is stated, the field falls back to the requested TTL
  (120 min, mirroring `core/grt_chat/services/grt_chat_service.py:25`): a bound
  of the right order beats omitting a mandatory field. Provider values are
  normalised to UTC ISO 8601 so one shape reaches the wire either way.
  _Corrected 2026-07-30._
- **The published credential is the WebSocket one.** `DeviceConnectionInfo` now
  carries a `ws_token`/`ws_expires_at` pair alongside `token`/`expires_at`,
  because on the local provider's healthy path they are two different tokens:
  the address is built from ws-info's `target` while `token` is http-info's, so
  publishing `token` there pairs a WebSocket URL with an HTTP credential. The
  local path fills the pair from ws-info; the BaaS path leaves it empty because
  its `token` already *is* the ws token. Empty therefore means "`token` is it",
  and every reader does `ws_token or token` — which is also what makes the
  expiry above describe the credential actually handed out. _Added 2026-07-30._
- The socket set is **capability-derived**, so this endpoint and
  `…/engine/{bot_id}/capabilities` must never disagree.

---

## Full engine inventory and the ruling on each

| Engine router | Prefix | HTTP | WS | Ruling | Why |
|---|---|---|---|---|---|
| `api/session` | `/api/sessions` | 7 | — | ✅ **C1 — wrap** | in the frontend proxypass list |
| `api/engine` | `/api/engine` | 5 | — | ✅ **C1 — wrap 3 of 5** | `switch`/`restart` excluded, see above |
| `api/models` | `/api/models` | 2 | — | ✅ **C1 — wrap** | in the proxypass list |
| `api/approvals` | `/api/approvals` | 3 | — | ✅ **C1 — wrap** | in the proxypass list |
| `api/node` | `/api/nodes` | 1 | — | ⛔ **dropped 2026-07-30** | in the proxypass list, so C1 would wrap it — but the product does not need node inventory on the public surface. Additive later. |
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
| `api/web_shell` | — | 2 | 1 | ⛔ **C3 / not v1** | `GET /terminal`, `/terminal/health`, `WS /ws/terminal`. Not reachable through `…/connection/{bot_id}` either — the terminal socket was implemented and then removed (see the connection entry above); the two HTTP routes are the shell's own bootstrap |
| `api/routers/ws` | — | — | 1 | 🔌 **C3 — connection info** | `/api/openclaw/ws` |
| `api/routers/claude_code_ws` | `/api/claude_code` | — | 1 | 🔌 **C3 — connection info** | `/api/claude_code/ws` |
| `openclaw/router` | `/api/openclaw` | — | 1 | 🔌 **C3** | `/client` — gateway-side socket, not a tenant socket |
| `api/app` (module-level) | — | 6 | 2 | ⛔ / 🔌 | `/health`, `/readiness`, `/config`, `/test-connection`, `/disconnect`, `/api/evaluation/report` are ops surface. `WS /ws` and `WS /api/{engine}/ws` are the generic chat sockets → `…/connection/{bot_id}` |
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
- **The engine's `warning` is dropped, deliberately.** It is the
  capability-*limited* signal, but the strings are internal engineering prose
  and not always English, and rule C2 keeps all but one limited capability off
  this surface — only `SESSION_CREATE` on `claude_code` can reach it, and that
  caveat describes how the session key is established rather than a degraded
  result. It is **logged server-side** and goes no further. `Envelope` is
  unchanged; `…/engine/{bot_id}/capabilities` is where a caller discovers limitations.
  _Decided 2026-07-30._

### 2. Capabilities are the public contract's escape hatch

Every engine handler calls `check_capability()` (`src/engine/.../api/caps.py`):
unsupported → **501** (with the engine's declared `fallback` string), limited →
a `warning` in the body. The supported set differs per engine (`openclaw`,
`claude_code`, `aicoding`, `teclaw`, `hermes`), so **the same public path
answers differently for two of a tenant's own bots.**

- `CapabilityNotSupportedError` / the transport's 501 need an `ENVELOPE_ERRORS`
  entry with a fixed public message pointing the caller at
  `…/engine/{bot_id}/capabilities`.
- That is why `…/engine/{bot_id}/capabilities` is in the v1 surface and not deferred: it
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
than inventing a second policy, and settle **one** behavior for all 16
endpoints: masked `409 device not ready` vs. auto-wake-then-retry. Whatever is
chosen, `GET /openapi/v1/bots/{bot_id}/status` stays the endpoint that tells a
caller *why*.

### 5. Closed value sets are enums; open ones stay strings

This is a public, generated contract, so a value set that is genuinely closed
should reach a client generator as a real enum rather than a bare `str`. Four
qualify — `SocketKind` (`chat` in v1, ours), `ApprovalMode`
(`approve` | `on-miss` | `never`), `MessageRole` (a `Literal` at
`src/engine/.../core/session/models.py:46`), and `EngineName` (reuse the bots
category's, don't redefine).

Equally important, these **stay strings** because the source is open and a
fabricated enum would break on the first new value:
`Session.permission_mode` / `.runtime` / `.model`, the `process` and
`transition` dicts in engine status, and **capability names** — the engine's
`Capability` enum is closed but explicitly versioned as "adding new entries is
safe", so a strict enum on a *response* field would turn an additive engine
release into a public 500.

Two traps worth knowing before you write the enums:

- **Approval mode is not one vocabulary — it is two, and neither is enforced.**
  `core/approval/models.py:15-19` names the canonical trio **`always` /
  `on-miss` / `never`**, then declares a six-value `Literal` whose extras are
  aliases (`approve`≈`always`, `on_miss`≈`on-miss`, `off`≈`never`).
  `GET /api/approvals/modes` advertises a *third* combination —
  **`approve` / `on-miss` / `never`** (`approvals/router.py:104-125`). Nothing
  canonicalises: `plugins/openclaw/_approval.py:57` forwards the string verbatim
  upstream, and `core/adapters/openclaw/approval.py:76` echoes back the
  **requested** mode rather than the committed one, contradicting its own
  docstring. The local stub returns `"auto"`
  (`local/openclaw/plugin_impl.py:93`) — a seventh value outside the `Literal`.
  The backend keeps an independent copy of both lists
  (`adapters/http/approvals/router.py:117,175-185`).
  **So: enum on the request** (the advertised three — publish one spelling),
  **`str` on the response** (a strict response enum would 500 against the local
  and singlebox stubs).
- **Enums need per-member docs to be worth anything.** OpenAPI has no native
  slot for them, so use `json_schema_extra={"x-enum-descriptions": {...}}` plus
  prose in the field description, and subclass `str, Enum` so the schema emits
  `type: string` + `enum`.

**Capability matrix for the wrapped groups**, from
`engines/openclaw/engine.py:55-134` and `engines/claude_code/engine.py:45-105`:

| Group | openclaw | claude_code |
|---|---|---|
| sessions (list/get/delete/messages/update) | ✅ | ✅ |
| sessions **create** | ✅ | ⚠️ limited — returns a real warning string |
| approvals get/set | ✅ | ❌ 501, no `fallback` declared |
| models | ✅ | ✅ |
| engine status/capabilities/available | ✅ ungated | ✅ ungated |

`claude_code`'s **limited** `SESSION_CREATE` is the only limited capability
this surface can reach at all — the other four (`MCP_START`, `MCP_STOP`,
`MCP_TOOLS_CALL`, `SKILLS_EXECUTE`) sit on routes rule C2 excludes. That is why
the caveat is logged rather than carried in the response.

One deliberate divergence: `GET /api/approvals/modes` is the only engine route
with **no** capability gate (`approvals/router.py:104`), so on a `claude_code`
bot it advertises three modes while get and set both 501. The public
`…/approvals/{bot_id}/modes` gates on `APPROVAL_SET`, so every mode it lists is one the
write endpoint accepts. The write capability rather than the read: the engine
defines `APPROVAL_GET` and `APPROVAL_SET` independently and gates one route on
each, so on an engine declaring only the read, keying this route off the read
would advertise three selectable modes while every `PUT` answers 501.
(`Capability.APPROVAL_LIST` exists at `core/engine/capability.py:84` but **no
engine declares it and no route checks it** — dead.)

### 6. Errors from the transport

`DeviceAdapterTransport` raises `DeviceAdapterEndpointNotFoundError` (404 from
the device — an endpoint this runtime doesn't serve),
`DeviceAdapterHTTPStatusError` (any other non-2xx) and
`DeviceAdapterTimeoutError`. All three need `ENVELOPE_ERRORS` entries. Per the
README's Track B gotcha, **map the base class last** — `ENVELOPE_ERRORS` returns
on the first `isinstance` match in insertion order.

Two engine failures do **not** arrive as transport errors and have to be read
out of an otherwise-successful payload:

- `exec.approvals.set` reports the *call* and the *change* separately. A refused
  mode change comes back as an outer success whose `data.ok` is `false`, so the
  PUT checks the flag and answers 502 rather than echoing the requested mode as
  though it had been applied. The check is `is False`, not falsy: the matching
  read carries no `ok` at all.
- `GET /engine/status` on an adapter that reports failure in-band returns a raw
  `{"success": false}` body, rejected before the payload is passed through.

_Added 2026-07-30._

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

The groups sit at `/openapi/v1/bots/{sessions,engine,models,approvals,connection}/{bot_id}`
— each behind its **own literal** segment, so none of them can shadow another
and their order relative to each other is free. They must still be registered
before the bots group so that the components serving a single-segment collection
root (`resources`, `routines`) keep resolving ahead of
`/openapi/v1/bots/{bot_id}`.

_Superseded note (pre-2026-08-03): these groups used to sit one segment below
the `{bot_id}` wildcard, which is what made their mount order free. It is free
for a different reason now — they are literal-prefixed — and the near-collision
this section used to warn about is gone with it: bot-scoped MCP is now
`/openapi/v1/bots/mcp/{bot_id}/...`, which nests **under** the marketplace
group's own literal rather than competing with it from the other side. Track C
does not add it (rule C2)._

---

## Decisions taken

- **2026-07-30 — Scope rule adopted (C1–C4).** Wrap engine HTTP the frontend
  reaches directly; leave backend-mediated engine calls to the backend contract
  that already fronts them; return connection info for sockets instead of
  relaying them; exclude aicoding.
- **2026-07-30 — v1 surface fixed at 16 endpoints**: sessions 7, engine 3,
  models 2, approvals 3, connection 1. **Nodes dropped** — the frontend does
  proxypass `/api/nodes`, so rule C1 would wrap it, but the product does not
  need node inventory on the public surface. Additive later.
- **2026-07-30 — `engine/switch` and `engine/restart` excluded**, to preserve
  #494's engine-immutability ruling and avoid two restart verbs.
- **2026-07-30 — `session-favorites` and the `/api/openclaw` HTTP trio
  (+ default-config, zero-check) deferred**, not cancelled. Both are additive:
  adding them later breaks no published contract.
- **2026-07-30 — the sessions group and the connection endpoint serve
  `personal` bots only**; `service` gets `501`. `BotType` is
  `Literal["personal", "service"]` and PR #494 already lets an external tenant
  create either, so this is live, not hypothetical. The other three groups
  serve both types.

  Connection was added to this ruling during review, and for the sessions
  group's reason rather than one of its own. The socket it publishes is not
  chat-scoped however it is labelled: the engine's WebSocket server advertises
  `sessions.list`, `sessions.patch`, `sessions.delete`, `sessions.reset` and
  the `exec.approvals` methods in its `hello`, grants `operator.admin`, and
  forwards unhandled methods to the active engine's relay plugin. Publishing
  that for a `service` bot would return over a socket exactly the data the
  sessions group answers `501` to withhold — a closed front door beside an open
  window. Scoping the token itself would take an engine-side change; the gate
  is the part this surface owns.
- **2026-07-30 — chat stays a WebSocket.** No `POST /chat`, no SSE. The public
  API hands back a URL and headers; the caller owns the socket.
- **2026-07-31 — a `service` bot resolves through its *published* runtime
  binding**, not through `ac_bots.binding_id`. Raised in review against the
  three groups above that do serve both bot types. That column holds the
  pre-publication draft — on the BaaS path it is the owner's own personal
  device, and the binding publishing produces is not on that column at all
  (`BaasConnInfoBuilder._resolve_bot` documents the same split) — so the by-bot
  entry point sends a published bot's engine, model and approval calls to the
  wrong device, or reports "not ready" once the draft binding is released while
  the published bot is healthy. The live binding is the publish record's
  `ext.binding.online`, selected with the shared `select_stage_bind_id` and
  reached through `resolve_for_binding_invoke`. There is **no fallback to the
  draft**: a bot with no published runtime is "not ready", the same answer an
  unprovisioned personal bot gets, because serving the draft is the defect this
  replaces.

  **That lookup is keyed on the `ac_bots` primary key, not on `bot_id`.** Review
  caught this in the round after the one above, and it is the sharper half of
  the ruling. `bot_id` is *not* unique across owners — the column carries no
  unique constraint, and `create_bot_for_others` gives every user a bot called
  `default` — so a lookup by `(bot_id, env)` selects whichever owner published
  most recently and can forward one caller's request to another owner's running
  device. Resolving the bot owner-scoped first does not constrain a second query
  that never mentions the row it authorised, so the primary key of that row is
  threaded through `BotFacts` and used as the key. Filtering by `owner_id`
  instead would also close the hole, but re-introduces the false negative
  `get_latest_success_by_source_bot_id` documents — an org bot whose record was
  created under a different staff id. The primary key has neither problem.
- **2026-07-31 — device resolution never runs on the event loop.** It is
  synchronous and its provider leg is blocking network I/O — a BaaS-backed bot
  resolves through `BaasService.get_ws_info`, a sync `httpx` call with a
  30-second timeout — so one slow provider lookup would park the worker's loop
  and stall every unrelated request. The relay and the connection endpoint both
  run it in a worker thread, as `CronRelayService` already does.

## Open questions for the SDD

1. **`warning` on `Envelope`** — add the optional field (recommended), send a
   response header, or drop the signal? Touches a shared Track B contract.
2. **Readiness behavior** — masked `409` or auto-wake-then-retry, uniformly
   across all 17.
3. **`model_id` with slashes** — `:path` converter or mandatory URL-encoding.
4. **Pagination** — the engine takes `limit`/`offset`; the public `Page` shape
   must map cleanly, including for `/api/models`, which returns a flat list
   with no `total`.
5. **Timeouts** — `DeviceAdapterTransport.invoke()` takes a per-call timeout; the
   public surface needs one documented deadline per group.
