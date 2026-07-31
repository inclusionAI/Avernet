# Plan: Connection Endpoint — Gateway URL and Query-Parameter Credential

## Approach

`EngineConnectionService` keeps everything it does today — bot resolution, the
personal-and-unshared gate, the relay-mode provider call, expiry normalisation —
and changes only the two lines that decide what a caller receives. The published
URL is composed against a new deployment-supplied gateway base under an `engine`
prefix instead of the engine proxy's `/proxypass/`, and the credential moves from
a response header into that URL's query string. `SocketInfo.headers` and
`Socket.headers` are deleted rather than left empty, so the credential has
exactly one home.

Nothing outside `core/engine_runtime/` and its public adapter changes. In
particular the BaaS relay URL builder is untouched: the internal console consumes
the same builder, so editing it would move the console's socket too.

### Assumptions carried in from the spec's open questions

The spec was approved without the two open questions being answered separately,
so its own recommendations are carried forward. They are load-bearing —
overturning either changes this plan:

| # | Assumption | Where it lands |
|---|---|---|
| 1 | Gateway base URL comes from a deployment env var, with an explicit named failure when unset, following `data_proxy_service.py:231-255` | `connection.py` — new module constant + resolver |
| 2 | A short-lived, target-bound credential in a URL query is acceptable, matching what the internal console already does against the same upstream | `connection.py:191-198` |

## Affected Components

- `src/backend/src/agentclaw/community/core/engine_runtime/connection.py` — the
  service composing the published socket. Carries the whole behavioural change.
- `src/backend/src/agentclaw/community/core/engine_runtime/models.py` — the
  transport-agnostic value objects. `SocketInfo` loses `headers`;
  `ConnectionResult`'s `expires_at` docstring is retightened.
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/engine_runtime/connection/schemas.py`
  — the published contract. `Socket` loses `headers`; examples and the
  `expires_at` description change.
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/engine_runtime/connection/router.py:71`
  — stops passing `headers` through.
- `src/backend/tests/community/core/engine_runtime/test_connection.py` — the
  URL-shape and credential-placement tests.

**Explicitly unchanged** (spec guardrail): `src/baas/**`,
`core/grt_chat/services/grt_chat_service.py:131-132`,
`core/devices/services/device_service.py:1781`,
`core/devices/services/baas_conn_info.py:172`, and `src/frontend/**`. All keep
addressing the engine proxy exactly as today.

## Data Model Changes

None. No tables, no columns, no migrations. `DeviceConnectionInfo`
(`core/devices/models.py:114-165`) is read exactly as it is read today —
`target`, `type`, `ws_token`/`token`, `ws_expires_at`/`expires_at`, `available`.

## API / Interface Changes

### Published response — `Socket.headers` removed

Before:

```json
{ "kind": "chat",
  "url": "wss://agentclawproxy-pre.example.com/proxypass/ARCA_x@0:20003/api/openclaw/ws",
  "headers": { "x-proxypass-token": "eyJ…" } }
```

After:

```json
{ "kind": "chat",
  "url": "wss://gateway.example.com/engine/ARCA_x@0:20003/api/openclaw/ws?x-proxypass-token=eyJ…" }
```

`kind` and `url` are unchanged in meaning. `Connection.engine`,
`Connection.expires_at` and `Connection.sockets` are structurally unchanged.

**Removing `headers` is not a breaking change today.**
`/openapi/v1/bots/{bot_id}/connection` is absent from the published
`src/gateway/configs/schemas/bots.openapi.json` (32 paths, connection not among
them), so the compatibility gate has no prior field to miss. After the endpoint
is published, the same removal would trip `property-removed`
(`src/gateway/.../forwarding/_compat.py:169`) and need `--allow-breaking`. This
is the cheap moment to do it.

### `expires_at` description — semantics retightened

The current wording (`schemas.py:53-57`) says "when every URL and credential here
stops working… request this endpoint again before then", which reads as though a
live socket dies at that instant. It does not: the credential is checked once, at
the handshake. New wording states that it bounds *opening* a socket, that an
open socket survives expiry, that a caller fetches a fresh credential before
connecting or reconnecting, and that a caller must not poll on a timer to keep a
live socket alive. Mirrored in `models.py:107-109`.

### Internal signature change

`_socket_url(self, info, socket_path)` gains the credential, because the URL now
carries it: `_socket_url(self, info, socket_path, token)`. Private to the class;
no external caller.

## Key Files & Functions

- `connection.py:51` — replace `_PROXY_TOKEN_HEADER` with
  `_PROXY_TOKEN_PARAM = "x-proxypass-token"` (same name, now a query key).
- `connection.py` (new module constants, near `:48`):
  - `GATEWAY_URL_ENV = "ENGINE_GATEWAY_URL"` — deployment-supplied gateway base.
  - `_ENGINE_PREFIX = "/engine"` — the path segment the gateway routes on.
  - a dev/local default, mirroring `data_proxy_service.py:65`, gated on
    `SERVER_ENV in {dev, local}` exactly as that precedent does.
- `connection.py:191-198` — `token` extraction is unchanged
  (`info.ws_token or info.token or ""`; the ordering comment at `:187-190` stays,
  it is still the reason). Drop the `headers` dict; pass `token` into
  `_socket_url`; construct `SocketInfo(kind="chat", url=…)`.
- `connection.py:270-286` `_socket_url` — rewritten. Order of decisions:
  1. `type == "local"` → `ws://{target}{socket_path}`, **no credential in the
     URL**. A local device is reached directly and the gateway cannot route to
     it. Preserves `test_local_devices_are_reached_directly:241`.
  2. otherwise → `{gateway_ws_base}{_ENGINE_PREFIX}/{target}{socket_path}` plus
     `?{_PROXY_TOKEN_PARAM}={quote(token)}` when a credential exists.
  3. a provider URL that is not the proxypass shape → see Risk 1.
- `connection.py:288-313` `_ws_base` — replaced by `_gateway_ws_base()`: read the
  env var, `rstrip("/")`, apply the existing `https→wss` / `http→ws` rewrite
  (`:312`), raise `EngineUpstreamError` when unset. The `sandbox_client`
  dependency and its `SandboxRuntimeUnavailableError` handling
  (`:303-311`) are removed from this path — the engine proxy's base URL is no
  longer what this endpoint publishes.
- `connection.py:9-11` — module docstring says "Nothing in `ConnectionResult`
  exposes a target, a connection type, or a bare token." Still true of the
  *fields*, but the URL now visibly carries both. Reword so it is not read as a
  guarantee the URL no longer keeps.
- `models.py:86-96` — drop `headers` from `SocketInfo`; drop `field` import if it
  becomes unused (`ConnectionResult.sockets` still uses it).
- `schemas.py:28-30` — drop `Socket.headers`; `:15-19` and `:42-47` — examples
  become gateway URLs carrying the query parameter; `:53-57` — new `expires_at`
  text.
- `router.py:71` — `Socket(kind=s.kind, url=s.url)`.

### Credential and target encoding

- **Target is not percent-encoded.** It carries `@` and `:` (e.g.
  `ARCA_x@0:20003`); both are legal `pchar` in a path segment, and production
  carries them unencoded today.
- **Credential is percent-encoded** with `quote(token, safe="")`. A JWT is
  base64url plus `.`, all query-safe, so this is defensive rather than required —
  but the value is provider-supplied and this endpoint should not assume its
  alphabet.

## Dependencies

None new. `urllib.parse.quote` is stdlib. No package, no version bump, no new
internal service call — the provider call count is unchanged (still exactly one,
pinned by `test_the_provider_is_asked_exactly_once:419`).

The endpoint gains a runtime dependency on the gateway serving `/engine`, which
is a different workstream's deliverable. See Rollout.

## Risks & Mitigations

- **Risk 1 — a provider URL that is not the proxypass shape.** `_socket_url`
  today passes any `ws://`/`wss://` provider URL through verbatim
  (`:282-284`), and that path is live: the BaaS LOCAL platform's relay branch
  returns `wss://{host}/wsrelay/{session_id}` and explicitly ignores the `path`
  argument (`src/baas/.../plugins/sandbox/desktop/_real.py:326-347`).
  `test_a_relayed_url_is_not_appended_to:149-154` exercises exactly that shape.
  It cannot be rebuilt from `target` + `path`, and the agreed gateway rewrite
  (`/engine/{rest}` → `/proxypass/{rest}`) cannot express it.
  **Mitigation:** raise `EngineUpstreamError` naming the unroutable shape, rather
  than publishing the engine proxy's host to a tenant — which is the exact thing
  the spec exists to stop. **This needs the user's confirmation** that
  tenant-facing personal bots cannot land on the LOCAL/desktop BaaS platform; if
  they can, this is a regression for those bots and the gateway thread needs a
  second prefix for the relay shape. Recorded as an open decision below.

- **Risk 2 — an empty credential would publish an unopenable URL.** Today a
  missing token simply omits `headers` (`:192`). Appending
  `?x-proxypass-token=` with no value would fail the handshake with nothing to
  diagnose from.
  **Mitigation:** append the query parameter only when a credential exists,
  matching today's conditional exactly. The URL stays valid and the failure
  surfaces at the upstream as it does today.

- **Risk 3 — a deployment that has not set the gateway URL.** Every published
  socket would be unopenable.
  **Mitigation:** `EngineUpstreamError` with a message naming the env var,
  mirroring the existing `"no proxy gateway in this deployment"` (`:309`) and the
  precedent's failure text (`data_proxy_service.py:248-255`). Already mapped to
  a public status via `ENVELOPE_ERRORS`, so no adapter change.

- **Risk 4 — the gateway route does not exist yet.** This change publishes an
  address that assumes it.
  **Mitigation:** see Rollout — the env var is the switch, and until a deployment
  sets it the endpoint fails loudly rather than publishing a wrong address.

- **Risk 5 — losing the `sandbox_client` dependency changes construction.**
  `EngineConnectionService.__init__` (`:120-133`) takes it via `@inject`.
  **Mitigation:** the constructor argument stays (removing it touches DI wiring
  and the conformance test that pins the signature); only `_ws_base`'s use of it
  goes. Revisit as cleanup once the endpoint ships, not in this change.

## Alternatives Considered

- **Rewrite the provider's URL instead of composing our own** — take the
  BaaS-returned `wss://{proxy}/proxypass/{target}{path}`, swap host and prefix.
  Rejected: it makes this endpoint depend on parsing a shape BaaS is free to
  change, and it does not solve Risk 1 anyway (the `wsrelay` shape has no
  `/proxypass/` to swap). Composing from `target` + `chat_path` — both of which
  we already hold — has no parsing step.
- **Change the BaaS relay builder so it emits the gateway URL directly** —
  fewer moving parts on our side. Rejected: the internal console consumes the
  same builder, so this would drag the console's socket onto the gateway. Also
  impossible here — the builder that produces the real production URL is not in
  this repository (the open-source ARCA plugins return `ws://localhost:{port}`,
  `src/baas/.../arca/local_proc/_sandbox_plugin.py:474-479`).
- **Keep `headers` populated alongside the query parameter** — both client
  styles work without branching. Rejected by the user: each transport carries the
  credential the way that transport natively supports, and publishing it twice
  leaves a caller guessing which one the socket honours.
- **Leave `headers` in the schema but always empty** — avoids a contract edit.
  Rejected: it locks a dead field in permanently, since removing it after publish
  trips the compatibility gate.
- **Reuse `sandbox_client.proxy_base_url()` and repoint it at the gateway** — no
  new configuration. Rejected: it is shared with every other engine-proxy caller,
  so repointing it moves all of them, violating the spec's scope guardrail.

## Rollout

- **No feature flag.** `ENGINE_GATEWAY_URL` is the switch: a deployment that has
  not set it cannot serve this endpoint, and says so by name.
- **Ordering.** The gateway's `/engine` route must exist before any deployment
  sets the variable. Until then this endpoint is the only consumer, and it is
  not yet reachable — the public surface answers unauthenticated until the
  caller-authentication workstream lands, so no tenant is holding a URL from it.
- **Backwards compatibility.** None owed. The endpoint is not in the published
  gateway schema and has no integrators. The internal console is untouched by
  construction, so there is nothing to migrate.
- **Reverting** is a code revert plus unsetting the variable; nothing persists.

## Test Strategy

All in `tests/community/core/engine_runtime/test_connection.py`, extending the
existing stubs (`_Devices:67`, `_Sandbox:83`, `_svc:103`). Unit only — the
service takes injected fakes and makes no network call.

**Rewritten:**
- `:157` and `:179` — credential assertions move from `headers[...]` to the URL's
  query string. The `ws_token`-before-`token` precedence they pin is the point
  and must survive verbatim.
- `:227` `test_proxy_url_is_composed_and_scheme_swapped` — expects the gateway
  host, the `/engine` prefix, and the credential in the query.
- `:212` and `:283` — the "no gateway configured" failures now come from the
  unset env var, not from `sandbox_client`.
- `:149` and `:235` — the verbatim-passthrough tests, per whichever way Risk 1 is
  decided.

**New:**
- A local device publishes `ws://{target}{path}` with **no** credential in the
  URL and does not consult the gateway variable.
- A provider issuing no credential publishes a URL with **no** query string —
  not a trailing `?x-proxypass-token=`.
- `SocketInfo` and `Socket` have no `headers` attribute (guards against a
  reintroduction that would put the credential in two places).
- A credential containing a character needing escaping is percent-encoded in the
  query.
- The target's `@` and `:` survive **unencoded** in the path segment.
- An unset `ENGINE_GATEWAY_URL` outside dev/local raises `EngineUpstreamError`,
  naming the variable.

**Unchanged and must still pass:** the bot-type and sharing gates (`:331-413`),
`test_the_provider_is_asked_exactly_once:419`, the expiry tests (`:288-316`),
`test_result_carries_no_target_type_or_bare_token:322`, and
`test_chat_path_follows_the_engine:256`.

**Not covered here:** that the gateway actually routes `/engine` to the upstream,
and that its WebSocket block carries the upgrade headers, `proxy_buffering off`,
a read timeout above the engine's 30-second heartbeat
(`src/engine/.../transport/ws_server.py:409-419`), and a location ordering ahead
of any catch-all. Those belong to the gateway workstream; this endpoint only
publishes an address that assumes them.

## Open Decision

**Risk 1 needs an answer before implementation.** If a tenant-facing personal bot
can be served by the BaaS LOCAL/desktop platform — the one returning
`wss://{host}/wsrelay/{session_id}` — then erroring on that shape is a
regression, and the gateway needs a second prefix for it. If those bots cannot
reach this endpoint, erroring is correct and cheap. Defaulting to **error**
pending confirmation, because the alternative silently publishes the internal
proxy host to a tenant.
