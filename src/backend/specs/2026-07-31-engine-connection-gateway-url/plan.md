# Plan: Connection Endpoint — Gateway URL and Query-Parameter Credential

## Approach

`EngineConnectionService` keeps everything it does today — bot resolution, the
personal-and-unshared gate, the relay-mode provider call, expiry normalisation —
and changes only what a caller receives. The provider still builds a finished
relay URL around the engine path we ask it for; this endpoint re-addresses that
URL onto the gateway, swapping the origin and the `/proxypass/` routing prefix
for `/engine/` and carrying everything past that prefix through verbatim. The
credential moves from a response header into that URL's query string. `SocketInfo.headers` and
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
| 1 | Gateway base URL comes from the `user_config.gateway` block of `application.yaml`, as a `base_url` / `base_url_pre` pair selected by `get_current_env()` — the same shape `bcn`, `ecb` and `baas` already use. Community ships neutral values; the corp overlay is filled separately in the cob repo | `di/config.py`, `di/modules/config_module.py`, `configs/application-community.yaml`, `connection.py` |
| 2 | A short-lived, target-bound credential in a URL query is acceptable, matching what the internal console already does against the same upstream | `connection.py:191-198` |

## Affected Components

- `src/backend/src/agentclaw/community/core/engine_runtime/connection.py` — the
  service composing the published socket. Carries the whole behavioural change.
- `src/backend/src/agentclaw/community/di/config.py` — new frozen
  `GatewayConfig` dataclass alongside `BcnConfig:64-81` and `EcbConfig`.
- `src/backend/src/agentclaw/community/di/modules/config_module.py` — new
  `@singleton @provider` reading `_block("gateway")`, mirroring the `bcn` reader
  at `:148-160` and the `ecb` reader at `:322-327`.
- `src/backend/src/agentclaw/community/configs/application-community.yaml` — the
  neutral `gateway` block, alongside `ecb:69-70`.
- `src/backend/src/agentclaw/community/configs/application-singlebox.yaml` — the
  singlebox placeholder, alongside `agentclawproxy.base_url:122-123`.
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
- `connection.py` (new module constant, near `:48`):
  - `_ENGINE_PREFIX = "/engine"` — the path segment the gateway routes on.

### Gateway address — configuration

Follows the established host-config pattern exactly; no new mechanism.

- `di/config.py` (new, beside `BcnConfig:64-81`):
  ```python
  @dataclass(frozen=True)
  class GatewayConfig:
      """Public gateway host for the tenant-facing connection socket.

      ``base_url`` is the prod gateway and ``base_url_pre`` overrides it when
      env == 'pre'. Neutral empty defaults — the community build embeds no
      gateway host; an empty value makes the connection endpoint report that
      this deployment has no gateway rather than publish an unopenable URL.
      """
      base_url: str = ""
      base_url_pre: str = ""
  ```
  Named `base_url` rather than `ws_base_url` to match the convention. It is
  stored as `https://…` and rewritten to `wss://` at use, which is exactly what
  `_ws_base:312` does today. It must be a **bare origin** — a path component
  would push `/engine` off the root the gateway's rewrite is anchored at, so one
  is refused rather than published.
- `di/modules/config_module.py` — new provider reading `_block("gateway")` with
  the dataclass defaults, identical in shape to the `ecb` reader at `:322-327`.
- **The pre/prod selection happens in DI, not in the service.** `di/config.py`
  also defines `GatewayEndpoint(base_url)` — the one host that applies — and
  `config_module.gateway_endpoint` resolves it from `GatewayConfig` using
  `get_current_env()`, the same way `http_client_module.py:60` does. Selecting a
  deployment is composition-root work, and `AGENTS.md:72-73` puts raw
  environment access in configuration loading, bootstrap, composition roots or
  tests — not in a core service.
- `EngineConnectionService.__init__:120-133` — takes `gateway: GatewayEndpoint`
  via the existing `@inject`, and reads no environment itself. DI binds the
  service to itself (`engine_runtime_module.py:33-35`), so no wiring change is
  needed.

### YAML values

- `application-community.yaml`, under `user_config:` beside `ecb:69-70`:
  ```yaml
  gateway:
    base_url: ""
    base_url_pre: ""
  ```
  Neutral empty, matching every other host block in the community build. The
  endpoint then reports "no gateway in this deployment" — the same outcome
  community gets today, where `proxy_base_url()` raises.
- `application-singlebox.yaml`, beside `agentclawproxy:122-123`:
  ```yaml
  gateway:
    base_url: "http://127.0.0.1:9999"
  ```
  Mirrors the existing singlebox `agentclawproxy` placeholder. Mostly moot in
  practice — singlebox rewrites loopback BaaS connections to `local`
  (`di/modules/infrastructure/singlebox/devices.py:91-116`), which takes the
  direct branch and never reads this.
- **Corp values are out of scope for this repo.** The pre and prod gateway hosts
  are set in the cob repo's overlay by the bots owner, exactly as the `bcn` and
  `baas` hosts already are.
- `connection.py:191-198` — `token` extraction is unchanged
  (`info.ws_token or info.token or ""`; the ordering comment at `:187-190` stays,
  it is still the reason). Drop the `headers` dict; pass `token` into
  `_socket_url`; construct `SocketInfo(kind="chat", url=…)`.
- `connection.py:270-286` `_socket_url` — rewritten. Order of decisions:
  1. `type == "local"` → `ws://{target}{socket_path}`, **no credential in the
     URL**. A local device is reached directly and the gateway cannot route to
     it, and there is no relay URL to re-address. Composed, so target and path
     are escaped here.
  2. otherwise → `_readdress_onto_gateway`: `urlsplit` the provider's relay URL,
     require the `/proxypass/` prefix, then publish
     `{gateway_ws_base}{_ENGINE_PREFIX}/{tail}` where `tail` is everything past
     that prefix, verbatim. The provider's query is preserved and the credential
     appended to it with `&`, not assigned over it.
  3. a provider URL that is missing, unparseable, not the `/proxypass/` shape,
     carries a fragment, or is not UTF-8 encodable → `EngineUpstreamError`. A
     guard on the Risk 1 decision, not a supported path.
- `connection.py:288-313` `_ws_base` — replaced by `_gateway_ws_base()`: select
  `base_url_pre` / `base_url` by env, `rstrip("/")`, apply the existing
  `https→wss` / `http→ws` rewrite (`:312`), raise `EngineUpstreamError` when the
  selected value is empty. The `sandbox_client` dependency and its
  `SandboxRuntimeUnavailableError` handling (`:303-311`) go with it — the engine
  proxy's base URL is no longer what this endpoint publishes.
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

### Encoding

- **The provider's path is never re-encoded.** It arrives already encoded by the
  provider; encoding it a second time is its own bug, and it is the reason the
  target is not touched either — the target reaches us inside that path. It is
  only *checked*: `urlsplit` has already cut query and fragment away, so the tail
  cannot end the path early whatever it holds.
- **Credential is percent-encoded** with `quote(token, safe="")`. A JWT is
  base64url plus `.`, all query-safe, so this is defensive rather than required —
  but the value is provider-supplied and this endpoint should not assume its
  alphabet.
- **The local branch still escapes** its composed target and path, since there is
  no provider URL to inherit encoding from.

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
  **Decided 2026-07-31:** a tenant-facing personal bot cannot land on that
  platform, so the shape is out of scope. `_socket_url` composes from `target` +
  `chat_path` and ignores the provider's own URL.

  **Mitigation — enforce the assumption rather than hope for it.** When the
  provider *does* return a URL whose path is not the `/proxypass/` shape, raise
  `EngineUpstreamError` naming it. The guard should never fire given the decision
  above; the point is that if the assumption is ever wrong, it surfaces as a
  named server-side error instead of a tenant reporting a socket that will not
  open. Publishing the engine proxy's host as a fallback is not an option — that
  is the exact thing the spec exists to stop.

- **Risk 2 — an empty credential would publish an unopenable URL.** Today a
  missing token simply omits `headers` (`:192`). Appending
  `?x-proxypass-token=` with no value would fail the handshake with nothing to
  diagnose from.
  **Mitigation:** append the query parameter only when a credential exists,
  matching today's conditional exactly. The URL stays valid and the failure
  surfaces at the upstream as it does today.

- **Risk 3 — a deployment whose `gateway` block is empty.** Every published
  socket would be unopenable.
  **Mitigation:** `EngineUpstreamError` naming the config block and the selected
  environment, mirroring the existing `"no proxy gateway in this deployment"`
  (`:309`). Already mapped to a public status via `ENVELOPE_ERRORS`, so no
  adapter change. Neutral-empty is also the *correct* community behaviour: it
  reproduces exactly what community does today, where `proxy_base_url()` raises
  (`plugins/community/sandbox_client.py:46-47`).

- **Risk 3b — pre pointed at the prod gateway, or vice versa.** The `_pre`
  suffix pattern exists precisely because this has bitten before: `bcn` carries
  a comment that a pre provider token sent to the prod host is rejected
  (`http_client_module.py:58-60`).
  **Mitigation:** use the same `get_current_env() == "pre"` selection rather
  than inventing a second convention, so the gateway pair behaves like every
  other host pair in the build.

- **Risk 4 — the gateway route does not exist yet.** This change publishes an
  address that assumes it.
  **Mitigation:** see Rollout — the `gateway` block is the switch, and until an
  overlay fills it the endpoint fails loudly rather than publishing a wrong
  address.

- **Risk 5 — dropping `sandbox_client` from the constructor.**
  `EngineConnectionService.__init__` (`:120-133`) takes it via `@inject`, and it
  becomes dead once `_ws_base` goes; leaving it would be an unused dependency a
  reviewer has to ask about.
  **Mitigation:** remove it. DI binds the service to itself with `@inject`
  (`engine_runtime_module.py:33-35`), so the injector simply stops supplying it —
  no wiring edit. `test_service_api_conformance:54` pins the *Protocol* against
  the class (`build`'s signature), not `__init__`, so it is unaffected. The only
  other construction site is the `_svc` helper at `test_connection.py:103-109`,
  which is being edited anyway.

## Alternatives Considered

- **Compose the URL from `target` + `chat_path` instead of re-addressing the
  provider's** — no parsing of a shape BaaS owns. **Chosen first, then reversed
  on 2026-07-31 at the bots owner's call.** Composing asserts our own grammar for
  a URL the provider owns: it assumes the relay URL is exactly
  `{origin}/proxypass/{target}` with our path appended and nothing else, so a
  query the provider sets, an extra path segment, or its own encoding of the
  target are all dropped silently. Worse, dropping a provider query and then
  appending `?x-proxypass-token=` yields a socket missing what the provider
  needed, with nothing pointing at why. The "no parsing step" argument also did
  not survive contact with the code — the relay-shape guard already parses that
  URL with `urlsplit`, so composing paid the parsing cost *and* kept the
  assumption.
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
- **Read the gateway URL from an environment variable**, following
  `data_proxy_service.py:231-255`. Rejected by the user: host configuration in
  this service belongs in `application.yaml`, where every other upstream host
  already lives, and where the corp overlay can set pre and prod independently.
  The env-var precedent is real but is the outlier, not the convention.
- **A nested `gateway.host.{dev,pre,prod}` map**, as BaaS uses
  (`src/baas/configs/application.yaml:146-152`). Rejected: the backend's own
  convention is the flat `base_url` / `base_url_pre` pair, and matching the
  neighbouring blocks matters more than matching another service.

## Rollout

- **No feature flag.** The `user_config.gateway` block is the switch: a
  deployment whose value is empty cannot serve this endpoint, and says so.
- **Two-repo rollout.** This repo ships the neutral community values and the
  reader. The pre and prod gateway hosts land separately in the cob repo's
  overlay, owned by the bots owner — the same split `bcn`, `baas` and `ecb`
  already follow. Merging here changes nothing in corp until that overlay lands.
- **Ordering.** The gateway's `/engine` route must exist before the overlay is
  filled. Until then this endpoint is the only consumer, and it is not yet
  reachable — the public surface answers unauthenticated until the
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
- `:212` and `:283` — the "no gateway configured" failures now come from an empty
  `gateway` block, not from `sandbox_client`. The `_svc` helper (`:103-109`) and
  the `_Sandbox` stub (`:83`) lose `sandbox` and gain a `GatewayConfig`.
- `:149` `test_a_relayed_url_is_not_appended_to` and `:235`
  `test_a_provider_supplied_ws_url_is_used_verbatim` — both pin the
  verbatim-passthrough behaviour this change removes. `:235`'s URL
  (`wss://relay.example/route/xyz`) and `:149`'s (`…/wsrelay/s1/…`) become the
  guard's inputs: they now assert `EngineUpstreamError` rather than a published
  URL. Rename both, since what they pin is now the opposite.

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
- An empty `gateway` block raises `EngineUpstreamError` naming the config, and
  does so *before* any credential is embedded in a URL.
- `base_url_pre` is selected when `get_current_env()` is `pre`, and `base_url`
  otherwise — the pre/prod pair is not silently collapsed to one host.

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

## Decisions Taken

| Decision | Answer | Recorded |
|---|---|---|
| Gateway address source | `user_config.gateway` block in `application.yaml`, `base_url` / `base_url_pre` selected by `get_current_env()`; corp values land in the cob overlay | 2026-07-31 |
| Credential placement | Query parameter on the socket URL only; `headers` removed from the contract. HTTP keeps the header and gains no query form | 2026-07-31 |
| `wsrelay` provider URLs (Risk 1) | Out of scope — tenant-facing personal bots cannot reach that platform. Refused with a named error rather than published | 2026-07-31 |
| How the published URL is built | **Re-address** the provider's relay URL — swap origin and routing prefix, carry everything past the prefix through verbatim — rather than compose it from parts | 2026-07-31 (reversed) |
| `sandbox_client` on the constructor | Removed, not kept — dead once `_ws_base` goes, and DI fills `__init__` by `@inject` | 2026-07-31 |

No open decisions remain. Ready for `tasks`.
