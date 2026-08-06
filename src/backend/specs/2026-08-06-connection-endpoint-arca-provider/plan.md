# Plan: Connection Endpoint — Serving Bots on the ARCA Provider

## Approach

`EngineConnectionService._socket_url` currently recognises two provider shapes
and treats everything else as the second one:

1. `type == "local"` → compose `ws://{target}{path}` directly.
2. everything else → `_readdress_onto_gateway`, which **requires** a finished
   `/proxypass/…` URL in `info.url`.

The ARCA provider is neither. It returns `type="proxy"`, a bare routing target, a
signed credential, and `url=""`, so it lands in branch 2 and is refused.

Add a third branch: a provider whose connection kind says "bare routing target"
gets its URL composed here, from `target` + the engine's socket path + `token`.

The composition is not new code so much as a second entry point into code that
already exists. `_readdress_onto_gateway` today does two separable things: it
*extracts* `tail` and `query` from the provider's URL, then *builds* the gateway
URL from `tail`, `query` and `token`. Only the extraction is BaaS-specific. Split
the build out as `_gateway_url(tail, query, token)`, and the new branch becomes:
produce a `tail` from the target and path, pass an empty query, call the same
builder. Byte-identical output by construction, not by two implementations
agreeing.

### Why no ARCA-side or configuration change is needed

The corp provider gives us both facts we need, and the gateway already routes
what we would build:

- `ArcaDeviceService._compose_device_conn_info` calls
  `sandbox_client.build_proxy_connection(sandbox_id=…, ttl_seconds=…)`, which
  returns a `ProxyConnection(target, token)` — `kernel/device_dto.py:155`. The
  target is the runtime's own routing string; the token is a JWT signed over
  exactly that string.
- The gateway's `bots-messages-ws` domain (`src/gateway/configs/application.yaml:276`)
  matches `/openapi/v1/bots/messages/ws/**`, rewrites that prefix to `/proxypass`,
  and forwards to the `engine_proxy` server — the same proxy the target is for.

So the endpoint holds everything required. The one thing it must *not* do is
reach for `sandbox_client.proxy_base_url()`: `_readdress_onto_gateway` discards
the provider's origin and substitutes the gateway's, so a proxy base URL would be
fetched only to be thrown away.

## Worked trace — every value, every step

One bot throughout: `bot_id=b_01k2f9`, owner `staff-9931`, `active_engine=openclaw`,
`bot_type=personal`, unshared. Gateway configured as
`https://gateway.example.com`.

### Step 0 — what `build` resolves before touching a device

| name | value | source |
| --- | --- | --- |
| `engine` | `openclaw` | `bot["active_engine"]` |
| `binding_id` | `4471` | `binding_repository.get_active_by_bot_and_owner("b_01k2f9", "staff-9931")` |
| `chat_path` | `/api/openclaw/ws` | `_chat_path("openclaw")` → `_CHAT_WS_PATHS` |
| `operator` | `OperatorContext(staff_id="staff-9931", …)` | the authenticated principal |

For a `claude_code` bot `chat_path` is `/api/claude_code/ws`; for an engine with
no dedicated entry, `/api/{engine}/ws`.

### Step 1 — what the provider returns

`_get_connection` calls
`device_service.get_device_connection(binding_id=4471, operator=…, ttl=7200,
ws_conn_mode="relay", path="/api/openclaw/ws")`. The router dispatches on
`binding.device_provider`.

**`device_provider="baas"`** → `BaasDeviceService.get_device_connection`
(`baas_device_service.py:751`), which calls BaaS `GET /api/v1/bots/{bot_uuid}/ws-info`
and returns:

```python
DeviceConnectionInfo(
    type="baas",
    target="ARCA_ARCA-SANDBOX-abc123@0:20003",
    token="eyJhbGciOiJIUzI1NiIs…",
    engine_type="openclaw",
    url="wss://agentclawproxy-prod.example.com/proxypass/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws",
    expires_at="2026-08-06T12:02:00Z",
    baas_base_url="http://baas.example.com:8890",
    bot_uuid="b_01k2f9",
    tenant="default",
    engine_port=20003,
    available=True,
)
```

`url` is populated **only** because `ws_conn_mode == "relay"`
(`baas_device_service.py:818`); `path` is what BaaS concatenated onto the target
with no separator of its own.

**`device_provider="arca"`** → the corp `ArcaDeviceService`. Both `ws_conn_mode`
and `path` are ignored — `path` never even reaches it, because the base
`get_device_connection` does not forward it to `_compose_device_conn_info`
(`device_service.py:1031`). It returns:

```python
DeviceConnectionInfo(
    type="proxy",
    target="ARCA_ARCA-SANDBOX-abc123@0:20003",
    token="eyJhbGciOiJIUzI1NiIs…",
    engine_type="openclaw",
    url="",              # every other field at its default
    expires_at="",
    available=True,
)
```

The target's `@0` comes from the `@alt`-suffixed sandbox id, which
`build_proxy_connection` uses verbatim (`plugin_api/sandbox_runtime.py:94-98`).
A device with no alt suffix yields `ARCA_ARCA-SANDBOX-abc123:20003` — same
grammar, one fewer component. **The endpoint must not parse either form.**

**`device_provider="local"`** (test/singlebox composition roots):

```python
DeviceConnectionInfo(type="local", target="127.0.0.1:20003", token="", …)
```

### Step 2 — `token` selection, unchanged

`connection.py:246` — `ws_token or token or ""`:

| provider | `ws_token` | `token` | selected |
| --- | --- | --- | --- |
| baas | `""` | `eyJhbGciOiJIUzI1NiIs…` | `eyJhbGciOiJIUzI1NiIs…` |
| arca | `""` | `eyJhbGciOiJIUzI1NiIs…` | `eyJhbGciOiJIUzI1NiIs…` |
| local | ws-info's token | http-info's token | ws-info's |

### Step 3 — `target` selection, unchanged

`_socket_url` reads `ws_target or target or ""` and refuses an empty result with
`"device connection carries no routing target"`. ARCA sets `ws_target=""`, so the
value used is `ARCA_ARCA-SANDBOX-abc123@0:20003`.

### Step 4 — the branch (this is the change)

| `info.type` | branch | tail fed to the builder |
| --- | --- | --- |
| `local` | compose direct, no gateway, no credential | — |
| `proxy` | **new** — compose onto gateway | `ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws` |
| `baas`, `desktop` | re-address the provider's URL | `ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws` |
| anything else, `url` empty | **new** — named error | — |
| anything else, `url` present | re-address, or refuse the shape | — |

The two tails are identical, which is the whole point. On the BaaS path the tail
is what remains after cutting `/proxypass/` off `parts.path`; on the ARCA path it
is `quote(target) + quote(socket_path)`.

### Step 5 — the shared builder

`_gateway_url(tail="ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws", query="", token="eyJ…")`:

| sub-step | value |
| --- | --- |
| credential percent-encoded | `eyJhbGciOiJIUzI1NiIs…` (a JWT is already URL-safe; a `+` or `/` in another credential would become `%2B` / `%2F`) |
| query after appending | `x-proxypass-token=eyJhbGciOiJIUzI1NiIs…` |
| `_gateway_ws_base()` | `wss://gateway.example.com` |
| `+ _ENGINE_PREFIX` | `wss://gateway.example.com/openapi/v1/bots/messages/ws` |
| `+ "/" + tail` | `wss://gateway.example.com/openapi/v1/bots/messages/ws/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws` |
| `+ "?" + query` | **final** (below) |

```
wss://gateway.example.com/openapi/v1/bots/messages/ws/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws?x-proxypass-token=eyJhbGciOiJIUzI1NiIs…
```

Where a provider's URL carried a query of its own, it is preserved and the
credential appended with `&` — unchanged behaviour, and the reason `query` is a
parameter of the builder rather than something it invents.

### Step 6 — what the gateway does with it

| hop | value |
| --- | --- |
| tenant's browser opens | `wss://gateway.example.com/openapi/v1/bots/messages/ws/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws?x-proxypass-token=eyJ…` |
| gateway rewrites prefix | `/proxypass/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws?x-proxypass-token=eyJ…` |
| forwarded to `engine_proxy` | proxy validates the JWT's target claim against `ARCA_ARCA-SANDBOX-abc123@0:20003` |
| proxy routes to | the sandbox's engine adapter on `:20003`, path `/api/openclaw/ws` |

The claim check is why the target must survive byte-for-byte: `arca_utils._get_proxypass_token`
records that a token target disagreeing with the URL target is rejected by the
proxy.

### Step 7 — `expires_at`

| provider | reported | published |
| --- | --- | --- |
| baas | `2026-08-06T12:02:00Z` | `2026-08-06T12:02:00+00:00` (normalised) |
| arca | *(empty)* | `now + 7200s`, computed |

Unchanged code. Worth recording that the computed fallback is *exact* on the ARCA
path rather than a guess: `_get_connection` passes `ttl=CONNECTION_TTL_SECONDS`
(7200), and the corp provider signs for precisely that (`resolved_ttl`, capped at
7 days). On the BaaS path the TTL is advisory, which is why the provider's own
value is preferred.

## Affected Components

| component | change |
| --- | --- |
| `core/engine_runtime/connection.py` | the only production file that changes |
| `core/devices/**` | none — the ARCA provider is corp-side and untouched |
| `adapters/http/openapi_v1/**` | none — same response model, same error mapping |
| `di/**` | none — no new dependency is injected |
| `src/gateway/**` | none — the route already exists |

## Key Files & Functions

`src/backend/src/agentclaw/community/core/engine_runtime/connection.py`

| location | change |
| --- | --- |
| module constants, near `_PROXYPASS_PREFIX` (`:84`) | **add** `_PROXY_TARGET_TYPES` |
| `_socket_url` (`:321`) | **modify** — read `type` once, add the proxy-target branch and the unrecognised-shape guard |
| `_compose_onto_gateway` | **add** — target + path → `_gateway_url` |
| `_gateway_url` | **add** — extracted from the tail of `_readdress_onto_gateway` |
| `_readdress_onto_gateway` (`:360`) | **modify** — its last five lines become a call to `_gateway_url`; extraction and guards unchanged |
| `_get_connection` (`:285`) | **comment only** — the note claiming an unset `ws_conn_mode` is what makes a provider return a bare target is wrong for ARCA, which returns one either way, and drops `path` besides |

`src/backend/tests/community/core/engine_runtime/test_connection.py` — new cases
alongside the existing `_Devices(type="local", …)` ones.

## API / Interface Changes

None. `ConnectionResult`, `SocketInfo`, the `Connection`/`Socket` response models,
and the `EngineUpstreamError → 502 "Engine service error"` mapping
(`responses.py:260`) are all unchanged. A previously-failing request starts
succeeding; no field is added, removed, or retyped.

## Implementation Detail

### Naming the shape

```python
#: Connection kinds whose provider returns a *bare* proxypass routing target and
#: a signed proxypass credential, leaving the URL for the caller to assemble. The
#: corp ``ArcaDeviceService`` answers ``"proxy"``; ``"arca"`` is accepted beside
#: it because that is the provider key the same device carries everywhere else
#: (``ARCA_DEVICE_PROVIDER``), and a provider spelling one where the other is
#: meant should not be the difference between a socket and a 502.
#:
#: Assembling here rather than in the provider is the platform's normal case, not
#: a special case: ``get_device_connection_v2`` (``device_service.py:1776``) and
#: the console frontend (``connectionStore.ts:177``) each build
#: ``/proxypass/{target}{path}`` for themselves from the same two values.
_PROXY_TARGET_TYPES = frozenset({"proxy", "arca"})
```

### The branch

```python
conn_type = str(getattr(info, "type", "") or "")

if conn_type == "local":
    ...unchanged...

if conn_type in _PROXY_TARGET_TYPES:
    return self._compose_onto_gateway(target, socket_path, token)

return self._readdress_onto_gateway(info, token)
```

`_readdress_onto_gateway` gains one guard at its top, before the URL is parsed, so
that an unrecognised kind carrying no URL is named as such instead of borrowing
the wrong-shape message:

```python
if not url:
    raise EngineUpstreamError(
        f"device connection of kind {conn_type!r} carries no relay url and is "
        f"not a kind this endpoint can compose one for"
    )
```

The kind is safe to name here: `@envelope_errors` publishes the fixed string
`"Engine service error"` and the exception's own message reaches logs only.

### Encoding

| part | `safe` | why |
| --- | --- | --- |
| routing target | `@:[]` | the target is an *authority-like* segment: `ARCA_…@0:20003` must keep `@` and `:`, and the local branch's `[::1]` must keep its brackets. Same set the local branch already uses. |
| socket path | `/` | separators survive; anything else is escaped so it cannot end the path early |
| credential | `""` (nothing safe) | a credential is opaque; every reserved character is encoded |

All three go through `_quote_or_reject`, so a lone surrogate in a provider value
is named rather than becoming a 500 at serialisation.

The re-addressed BaaS path is **not** re-encoded — the provider already encoded
it. Only the composed path encodes, because it is building from raw values. This
asymmetry is deliberate and already present for the local branch.

## Risks & Mitigations

| risk | mitigation |
| --- | --- |
| The corp provider's `type` string is `"proxy"` today; a corp-side rename would silently revert ARCA to a 502. | The set holds both plausible spellings, and the unrecognised-kind error names the value it saw, so the log line says exactly which string to add. |
| Percent-encoding the target could break the proxy's claim check. | `safe="@:[]"` leaves every character of the real grammar untouched; a test asserts the target segment survives verbatim. |
| The gateway might not front the same proxy that serves ARCA targets. | It does — `application.yaml:276` forwards to `engine_proxy`, and the BaaS path already publishes `ARCA_…` targets through this same route in production. |
| Composing a URL for a stopped sandbox. | Pre-existing and out of scope: ARCA does not report liveness, and `available` defaults to `True`. The handshake fails, as it does for every other ARCA caller today. |
| The refactor changes the BaaS output. | The builder is called with exactly the values the old inline code used; a test asserts the two branches agree byte-for-byte for the same inputs. |

## Alternatives Considered

**Make the ARCA provider fill `url` in relay mode.** Rejected — see spec Resolved
Question 1. Corp-side, and it would need `path` threaded through
`_compose_device_conn_info`, whose signature does not carry it, to build a URL
whose origin this endpoint discards anyway.

**Infer the shape from `url == ""`.** Rejected — see spec Resolved Question 2. It
would compose a plausible-looking URL for a provider that failed to fill a field
it was supposed to fill.

**Inject `SandboxRuntimeClient` and build `{proxy_base}/proxypass/{target}{path}`,
then re-address it.** Rejected: a new dependency on this service, a network-facing
config lookup, and an origin computed only to be replaced one line later.

**Normalise `"proxy"` to `"baas"` at the router.** Rejected: the router's job is
dispatch, and the two kinds genuinely differ in what they return. Flattening them
would leave `url` empty on a kind that promises one.

## Test Strategy

All in `tests/community/core/engine_runtime/test_connection.py`, reusing the
existing `_Devices` fake, which already takes `type`/`target`/`token`.

**New:**
1. `type="proxy"` publishes `wss://gw.example/openapi/v1/bots/messages/ws/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws?x-proxypass-token=…`.
2. The published URL never names the engine proxy and never contains `/proxypass/`.
3. `@` and `:` survive in the target segment; a bracketed IPv6-style target keeps its brackets.
4. The engine's path is used — `claude_code` yields `/api/claude_code/ws`.
5. A `proxy` device with no token publishes no query string.
6. The credential is percent-encoded.
7. Composed and re-addressed branches agree byte-for-byte for the same target, path and token.
8. An unrecognised kind with `url=""` raises the new named error, and the message does not carry the credential.
9. `expires_at` for a `proxy` device falls back to `now + CONNECTION_TTL_SECONDS`.
10. A `proxy` device with an empty target still raises `"carries no routing target"`.

**Unchanged and must stay green:** the whole existing file, in particular the
local-branch cases, the `/wsrelay/` wrong-shape refusal, the fragment refusal, the
provider-query-preserved case, and the gateway-base validation cases.

## Rollout

Single commit on `claude/backend-openapi-connection-f7j47u`, cut from and merged
into `REL20260806`. No migration, no config, no feature flag: the change converts
a hard failure into a success on one provider and is a no-op on the others. Nothing
to roll back beyond reverting the commit.

## Decisions Taken

1. Compose at the point of publication, not in the provider.
2. Branch on the declared connection kind, not on an absent URL.
3. One builder shared by both gateway branches, so they cannot drift.
4. Accept both `"proxy"` and `"arca"` as the bare-target kind.
5. Name the unrecognised kind in the error; the HTTP body stays the fixed
   `"Engine service error"`.
6. Leave `ws_conn_mode="relay"` and `path=…` on the provider call, even though
   ARCA ignores both — they are correct for BaaS, and removing them would break
   it.
