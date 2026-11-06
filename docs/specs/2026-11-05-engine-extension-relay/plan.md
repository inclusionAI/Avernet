# Engine Extension Relay — Technical Plan

- **Spec:** [spec.md](spec.md) · **Tasks:** [tasks.md](tasks.md)
- **Status:** Draft (for review)

## 1. Decision: the relay lives in the backend, not the Gateway or the engine adapter

| Option | Verdict | Why |
| --- | --- | --- |
| **A. Gateway domain per Extension Server** | Rejected | The Gateway has no data on Bot ownership, collaborators, App grants, or `ac_bots.active_engine`. It would have to route on a path segment the caller controls, such as `/ext/hermes/**`, which lets a caller pick the engine and bypass Bot authorization. Bot authorization belongs to the backend (`EngineRuntimeRelay.resolve_bot` → `require_bot_operator`). |
| **B. Backend relay** | **Chosen** | The backend already owns everything needed to make the decision: verifying the principal (`core/gateway_principal/verifier.py`), Bot resolution, masked 404s, App admission (`admission.py`), and the active engine. It serves **both** surfaces from one core service. The Gateway forwards `/openapi/v1/bots/**` to the backend today, so no new Gateway domain is needed. |
| **C. Engine adapter relay** (generalising `/data/*` `DataProxyService`) | Rejected for this scope | The engine adapter runs on each Bot's device and is reached through BaaS proxypass. Making it the relay would put every Extension Server's credentials and egress on every device. It also adds a hop, and the adapter verifies no Avernet principal today. `DataProxyService` stays the device-local, engine-internal proxy, and the two do not overlap. |

The relay path is:

```
Open:      client ─▶ Gateway (authn, sign X-Avernet-Principal aud=backend)
                       ─▶ Backend /openapi/v1/bots/{bot_id}/engine/ext/{path}
Internal:  workbench ─▶ Backend /api/bots/{bot_id}/engine/ext/{path}  (AuthPlugin session)
                       │
                       ▼
           EngineExtensionRelay (core)
             1. resolve the Bot and authorize the caller  (reuse EngineRuntimeRelay.resolve_bot)
             2. engine = Bot.active_engine (normalize_engine_type)
             3. server = registry.for_engine(engine)       (404 if none or disabled)
             4. op = manifest.match(method, path)          (404 if undeclared)
             5. admit the caller type / scope for op       (403)
             6. limits: rate, concurrency, breaker         (429 / 503)
             7. assertion = signer.sign(claims, aud=server.id)
             8. transport.forward(...)  stream back          (502 / 504)
             9. write the audit record
                       │
                       ▼
           Extension Server (engine owner)  — verifies the assertion via JWKS
```

## 2. API surface

### 2.1 Open surface (`/openapi/v1`, behind the Gateway)

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/openapi/v1/bots/{bot_id}/engine/extensions` | Discovery: the operations this caller can use, from the manifest. |
| `GET POST PUT PATCH DELETE` | `/openapi/v1/bots/{bot_id}/engine/ext/{path:path}` | The relay. `HEAD`/`OPTIONS` are not relayed. CORS preflight is answered by the Gateway. |
| `GET` | `/openapi/v1/engine-extensions/.well-known/jwks.json` | The public key set for assertions. This route needs no authentication. |

- The routes use `route_class=PublicAPIRoute`, so the authorization rule is
  attached when the route is declared.
- In the admission table (`admission_routes.py`) both bot routes are
  `GRANT_CHECKED_OWN_BOT` and the JWKS route is `OPEN`. The route-surface test
  catches any drift between the routes and this table.
- In the Gateway `route_security`, `/openapi/v1/bots/**` already gives
  `user: optional, app: optional`, which covers the relay. Bots (`bot` principal)
  are admitted only if a manifest operation lists `bot`, so the Gateway needs a
  new entry:
  `"/openapi/v1/bots/{bot_id}/engine/ext/**": {user: optional, app: optional, bot: optional}`.
- The JWKS route needs `"GET /openapi/v1/engine-extensions/.well-known/jwks.json": {}`
  and a `bots` domain match. It is configuration only, so no Gateway code
  changes.
- The published OpenAPI document (`bots.openapi.json`, generated from the
  backend) documents the relay route as an opaque passthrough. Its description
  links to the discovery endpoint. Copying per-engine operations into the
  document is phase 2 (§11).

### 2.2 Internal surface (`/api`, first-party)

| Method | Path |
| --- | --- |
| `GET` | `/api/bots/{bot_id}/engine/extensions` |
| `*` | `/api/bots/{bot_id}/engine/ext/{path:path}` |

These routes authenticate through the same `get_request_context`
(`AuthPlugin`) as the other first-party routes. The caller type is always
`user`. They are thin adapters over the same core service, so the two surfaces
cannot drift apart (Rule 7).

## 3. Extension Server registry (configuration-driven, Rule 14)

The registry is part of the backend configuration (the `user_config` section,
loaded in the composition root), keyed by the **canonical engine type** that
`normalize_engine_type` produces:

```yaml
engine_extensions:
  jwks_public_path: /openapi/v1/engine-extensions/.well-known/jwks.json
  servers:
    hermes:                               # canonical engine type
      id: hermes-ext                      # assertion audience; stable, unique
      enabled: true
      base_url: "${hermes_ext_url}"       # https required outside single-box
      manifest:
        source: url                       # url | file
        location: /.well-known/avernet-extension.json   # relative to base_url
        refresh_seconds: 300
      egress:
        allow_private_network: false      # SSRF guard override; operator-only
        client_cert_secret: ""            # optional mTLS (SecretResolver name)
      limits:
        timeout_seconds: 30
        stream_timeout_seconds: 600
        max_request_bytes: 10485760
        max_response_bytes: 52428800      # buffered responses only
        max_concurrency: 64
        rps_per_bot: 20
      breaker: { failure_threshold: 5, window_seconds: 30, cooldown_seconds: 30 }
```

Loading rules (startup **fails closed** on any violation):

- The keys are canonical engine types, and each `id` is unique.
- `base_url` must have an `https` scheme. The single-box profile (`DEPLOY_PROFILE=test`/local)
  also accepts `http://127.0.0.1` and `http://localhost`.
- `base_url` must have no userinfo, query, or fragment.
- Credentials never appear inline. They are only **secret names** that
  `SecretResolver` resolves.

Phase 1 has no database table. Per-tenant registration (Open Question 1)
would add a `RegistryProvider` Protocol backed by the database, with the
configuration as one flavor. The core service depends only on
`EngineExtensionRegistry.for_engine(engine) -> ExtensionServer | None`, so that
change stays local.

## 4. Extension Manifest

The Extension Server serves the manifest at `base_url + location` (or the
operator supplies it as a file). It is an **OpenAPI 3.1** document with one
vendor extension on each operation:

```yaml
openapi: 3.1.0
info: { title: Hermes Extension API, version: 1.4.0 }
x-avernet-extension: { contract: 1, engine: hermes }
paths:
  /jobs/{job_id}:
    get:
      operationId: getJob
      summary: Read a Hermes background job
      x-avernet:
        scope: read                # read | write
        audience: [user, app]      # which caller types may invoke
        stream: false              # true → SSE, uses stream_timeout
```

- **It is the allow-list.** The relay compiles `(method, path template)` into a
  matcher, using the same `{param}` and most-specific-wins semantics as the
  Gateway's `route_security`. Any operation without `x-avernet` is
  **ignored**, so it cannot be called.
- **Refresh.** The relay refreshes the manifest in the background and keeps the
  **last known good** copy, as the Gateway does with its schema catalog.
  - If the manifest cannot be fetched at boot, the server stays registered,
    every relay call returns 503 `engine_extension_manifest_unavailable`, and
    the standard API is unaffected.
  - If a fetched manifest is malformed, `contract` is unknown, or
    `engine` does not match the registry key, the relay rejects it and keeps
    the previous copy.
- **Limits.** The manifest may declare at most 500 operations and be at most
  1 MiB.
- **Not used for request validation.** Request and response bodies are opaque
  to the relay, and the Extension Server validates its own input.

## 5. Extension Assertion (auth to the Extension Server)

### 5.1 Why a new token rather than forwarding `X-Avernet-Principal`

The Gateway principal is **HS256**, signed with a symmetric key that the
Gateway, backend, and BCS share (`principal_signing_key`).

- Anyone who can verify an HS256 token can also forge one. Giving that key to an
  outside party would let it impersonate any user to every Avernet component.
- The Gateway principal is addressed to `aud=backend`. Forwarding it would also
  give the Extension Server a token our own backend accepts, which is a confused
  deputy.

The assertion is therefore a **separate, asymmetrically signed** token, minted
by the backend for one Extension Server and one request.

### 5.2 Format

The header is `X-Avernet-Extension-Assertion: <JWS compact>`, and the JOSE
header is `{alg: ES256, kid: <key id>, typ: "avernet-ext+jwt"}`. The claims are:

| Claim | Value |
| --- | --- |
| `iss` | `avernet` (configurable per deployment, e.g. `https://api.example.com`) |
| `aud` | the Extension Server `id` (e.g. `hermes-ext`) |
| `iat` / `nbf` / `exp` | now / now / now + 60 s (leeway 5 s; mirrors the backend verifier) |
| `jti` | the relay request id (UUIDv7), also sent as `X-Avernet-Request-Id` |
| `sub` | the stable id of the acting principal (user id, App id, or Bot uuid) |
| `sub_type` | `user` \| `app` \| `bot` |
| `act` | for an App acting for a user: `{sub: <app_id>, sub_type: "app"}`, with `sub` set to the user (Open Question 3) |
| `tenant` | the Bot's tenant id |
| `bot` | `{id: <public bot id>, engine: <canonical engine>}` |
| `scope` | the operation's scope (`read` \| `write`) |
| `op` | the manifest `operationId` |
| `htm` / `htu` | the method and the upstream path plus query that were actually forwarded, binding the token to this request (as DPoP does) |

- **No PII beyond ids.** Display names and emails are not included. An
  Extension Server that needs them calls back into the Open API (§8).
- **Device topology is never included** (no device ids or connection info),
  consistent with the `BotFacts` boundary in `engine_runtime/relay.py`.
- **No body hash.** Streaming uploads make one impractical. Body integrity rests
  on TLS. Replay is bounded by `exp` together with `htm`/`htu`, and Extension
  Servers that need strict replay protection can cache `jti` for 65 s.

### 5.3 Keys and rotation

- **`ExtensionAssertionSigner` Protocol** (`plugin_api/engine_extension.py`):
  `sign(claims) -> str` and `public_jwks() -> dict`.
  - `community` flavor: ES256 from a PEM resolved through `SecretResolver`. The
    names are `engine_ext_signing_key_current` and an optional
    `engine_ext_signing_key_next`, each with a configured `kid`.
  - `local` flavor: an ephemeral key generated at boot, for single-box and tests.
  - An enterprise or KMS flavor plugs in through the same Protocol, and asks KMS
    to do the signing.
- **Rotation.** The operator publishes `next` in the JWKS, waits at least the
  JWKS cache time (the response is served with `Cache-Control: max-age=300`),
  switches signing to `next`, and then retires the old key after
  `exp + leeway`. Requests do not fail during the switch.
- **Boot.** In strict profiles a missing signing key stops boot, as for the
  Gateway principal signer. Outside strict profiles a missing key disables the
  relay, and calls return 503 `engine_extension_signing_unavailable`.
- **No fallback key.** The relay never falls back to a default key.

### 5.4 Optional transport-level auth

`egress.client_cert_secret` turns on **mTLS** for engine owners who require it.
It is additive: the assertion is still mandatory. No static bearer token is
configured, because the assertion already proves the request came from
Avernet.

## 6. Forwarding behavior

**Path.** The upstream URL is `base_url.rstrip('/') + '/' + path`, plus the
query string, which is forwarded unchanged. The path is normalized before
matching:

- percent-decode once;
- reject `..`, `.`, empty segments, `%2F`, `%5C`, and `\` with 400;
- re-encode the result.

**Request headers (allow-list).** Only these pass through:

- `content-type`, `content-length`, `accept`, `accept-language`, `accept-encoding`;
- `if-match`, `if-none-match`, `idempotency-key`;
- `x-request-id` (sent upstream as `x-client-request-id`);
- `traceparent`, `tracestate`.

Everything else is dropped, including `cookie`, `authorization`, `x-avernet-*`,
`x-forwarded-*`, and `host`. Avernet then adds:

- `X-Avernet-Extension-Assertion`;
- `X-Avernet-Request-Id`;
- `X-Forwarded-For` (a single hop: the client IP the backend sees);
- `User-Agent: avernet-engine-extension-relay/<version>`.

**Response headers.** Hop-by-hop headers, `set-cookie`, `server`,
`x-powered-by`, and any `access-control-*` are dropped, because CORS belongs
to the Gateway. Avernet adds `X-Avernet-Relay-Source` and
`X-Avernet-Request-Id`.

**Redirects.** The relay never follows redirects (`follow_redirects=False`).
A 3xx response passes through only if its `Location` resolves under
`base_url`, and the relay rewrites that `Location` to the relay path.
Otherwise the relay returns 502 `engine_extension_bad_redirect`.

**Status mapping.**

| Situation | Response |
| --- | --- |
| Upstream 1xx–5xx | Passed through, `Relay-Source: upstream` |
| Connection refused, DNS failure, TLS failure | 502 `engine_extension_unreachable` |
| Timeout (connect, read, or total) | 504 `engine_extension_timeout` |
| Response over `max_response_bytes` | 502 `engine_extension_response_too_large` |
| Breaker open | 503 `engine_extension_circuit_open` + `Retry-After` |
| Rate or concurrency limit | 429 + `Retry-After` |

Errors the relay produces itself use the standard Avernet envelope and carry
`Relay-Source: relay`. The `_normalise` `success:false` rewrite in
`EngineRuntimeRelay` is **not** applied, because the vendor owns its bodies.

**Streaming.** If the upstream sends `content-type: text/event-stream`, or the
operation has `stream: true`, the relay streams the response chunk by chunk
through `StreamingResponse` with no buffering, and the response-size cap does
not apply. The idle-read timeout is 60 s and the total is
`stream_timeout_seconds`. When the client disconnects, the relay cancels the
upstream request, using the same one-shot, closeable stream discipline as the
Gateway Forwarder contract v2.

**Request bodies** are streamed to the upstream. A cumulative counter enforces
`max_request_bytes` and returns 413. The check covers chunked uploads that send
no `content-length`.

**SSRF.** The base URL comes only from operator configuration, never from the
caller.

- At connect time the transport resolves DNS and refuses loopback, RFC 1918,
  link-local (including `169.254.169.254`), CGNAT, and ULA addresses, unless
  `allow_private_network` is set.
- The connection is pinned to the IP that passed the check, so DNS rebinding
  cannot swap the target.

## 7. Limits, resilience, audit, observability

**Limits.**

- **Rate limit.** An in-process token bucket keyed by
  `(server_id, tenant, bot_id)` holds the `rps_per_bot` budget.
  - **Why not the cache plugin.** The existing `plugin_api/cache.py`
    `CachePlugin` has only `get`/`set` and locks. It has no atomic increment,
    and a get-then-set counter would race across replicas.
  - **Effective cap.** Limits are therefore per replica, so the cap is
    `rps_per_bot × replicas`. The operator doc says so.
  - **Later.** A cluster-wide limit would add an atomic `incr_with_ttl` to the
    cache Protocol. That waits until a second consumer needs it (Rule 19).
- **Concurrency.** An `asyncio.Semaphore` per server and per process. The
  effective global cap is `max_concurrency × replicas`.

**Breaker.** A breaker per server and per process counts 5xx responses, 502s,
and 504s within a rolling window. While it is open, calls fail fast with 503.
After the cooldown it lets one probe request through (half-open).

**Audit.** The relay writes one structured `engine_extension.audit` log line per
call, alongside the existing `openapi_v1/access_log.py`. Its fields are:

- request id, surface (`open` or `internal`), caller and caller type, `act`;
- tenant, Bot, engine, server id, `operationId`, method, path template;
- upstream status, relay outcome, latency, bytes in and out.

It never logs bodies, query values, or headers. Audit records are write-only
log events. The relay does not persist them in the database in phase 1, so it
does not add a write path that could fail.

**Observability.**

- **Metrics.** The backend has no metrics Protocol today; `TracerPlugin` only
  installs tracing and exports a trace carrier. Phase 1 therefore emits these
  values as fields of the audit line (outcome, latency, breaker state) and logs
  `manifest_age_seconds` on each refresh. Dashboards build on the logs. Adding a
  metrics Protocol is a separate decision.
- **Tracing.** The relay takes the outbound `traceparent` from
  `TracerPlugin.export_trace_carrier()`. Without an active trace it forwards the
  client's `traceparent`, if one was sent.

## 8. Engine-owner contract (public doc) and callbacks

The new document is `docs/engine-extensions/extension-server-contract.md`. It
is versioned by `x-avernet-extension.contract`, and it covers:

- the manifest format;
- the JWKS URL;
- the verification steps: `alg` is ES256, `kid` is in the JWKS, `iss` and `aud`
  match, `exp`/`nbf` hold with 5 s leeway, `htm` and `htu` equal the received
  method and path;
- the claim semantics;
- the headers it can rely on;
- the error and timeout expectations;
- the limits.

**Callbacks.** An Extension Server that must read Avernet data, such as Bot
details or a user's display name, registers as an ordinary **Application**
and calls the Open API with its own App API key. It is subject to App grants
like any other integrator. The assertion is never accepted as a credential by
Avernet, because `aud` is never `backend`, so the relay cannot be used to
launder privileges.

## 9. Code layout (backend, Rule 8 / 3 / 20 / 25)

```
apps/backend/src/agentclaw/community/
  plugin_api/engine_extension.py          # Protocols: ExtensionAssertionSigner,
                                          #   ExtensionTransport (async, streaming)
  plugins/community/engine_extension/
    signer.py                             # ES256 via SecretResolver
    transport.py                          # httpx.AsyncClient, SSRF-pinned resolver
  plugins/local/engine_extension.py       # ephemeral-key signer; in-memory
                                          #   transport (MockSeam, raises if unstubbed)
  core/engine_extension/
    __init__.py
    config.py                             # registry config model + validation
    registry.py                           # EngineExtensionRegistry (for_engine)
    manifest.py                           # parse, compile matcher, refresh, LKG
    paths.py                              # path normalization + upstream URL build
    headers.py                            # request/response allow-lists
    claims.py                             # assertion claim construction
    limits.py                             # rate limiter, semaphore, breaker
    relay.py                              # EngineExtensionRelay orchestration
    audit.py                              # audit event
    errors.py                             # relay error codes → HTTP mapping
  adapters/http/openapi_v1/engine_extension/
    router.py                             # discovery + relay + JWKS (PublicAPIRoute)
  adapters/http/engine_extension/
    router.py                             # internal /api surface
  di/…                                    # bind Protocols per profile; build registry
apps/backend/tests/community/
  contracts/test_engine_extension_signer.py
  contracts/test_engine_extension_transport.py
  core/engine_extension/test_*.py
  adapters/http/…/test_engine_extension_*.py
  support/reference_extension_server.py   # reference server for E2E + docs
```

- **Bot facts.** `EngineExtensionRelay` depends on `EngineRuntimeRelayProtocol`
  only for `resolve_bot` (authorization plus the masked 404) and reads the
  active engine through `BotFacts`. `BotFacts` gains an `active_engine: str`
  field, which is still a narrow value object, so device topology still cannot
  leak.
- **File size.** Each file stays well under the 1,000-line limit. `relay.py`
  only orchestrates, and the logic lives in the sibling modules.

## 10. Gateway, engine adapter, frontend, singlebox

- **Gateway:** configuration only (§2.1), with two `route_security` entries.
  The existing Gateway rules already do the rest:
  - `_INBOUND_STRIP` removes any forged `x-avernet-principal`;
  - the Gateway treats an `X-Avernet-Extension-Assertion` header sent by a
    caller as an ordinary header, and the backend then drops it through the
    request allow-list.
- **Engine adapter:** no change in phase 1. The adapter adds no `Capability`
  flag: whether extensions are available is a backend registry fact, not
  something the engine reports about itself.
- **Frontend (nextgen):** no change in phase 1. The internal surface is
  available for follow-up UI work. Any engine-specific UI must stay behind
  `getCapabilities()` or `@/extensions` seams (open-core rules).
- **Singlebox:** an optional `ENGINE_EXT_REFERENCE=1` starts the reference
  Extension Server on `127.0.0.1:20090` and registers it for `openclaw`, so the
  relay can be exercised end to end.

## 11. Phase 2 candidates (not in this plan)

1. **WebSocket relay.** Upgrade at the backend, put the assertion in the first
   frame or as a subprotocol token, and re-sign on a timer for long-lived
   sockets.
2. **Gateway doc merge.** The Gateway's schema catalog reads each enabled
   manifest and publishes its operations under
   `/openapi/v1/bots/{bot_id}/engine/ext/...` with `x-avernet-security`.
3. **Tenant-registered Extension Servers.** A database-backed registry with an
   approval workflow (Open Question 1).
4. **Scoped App grants.** Add engine-specific scopes (Open Question 2).
5. **Migrate the existing endpoints.** Move `/api/openclaw/*` and `aicoding`
   `/data/*` behind the relay, one engine at a time.

## 12. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| The relay becomes an open proxy or SSRF vector | Base URL from configuration only, manifest allow-list, path normalization, DNS pinning plus a private-range deny-list, no redirect following |
| Privilege escalation through a vendor server | The assertion is asymmetric, `aud` is the vendor, Avernet never accepts it, and callbacks need their own App credential |
| Cross-tenant data exposure at the vendor | Each assertion names exactly one tenant and Bot, already authorized. The contract doc requires vendors to scope by `tenant` and `bot.id` |
| A slow vendor exhausts backend workers | Async transport, a concurrency cap per server, timeouts, the breaker, and streaming with idle timeouts |
| Manifest outage | Last-known-good copy. A failure degrades only that engine's extensions |
| Per-replica limits are looser than configured | Documented as `× replicas`. Vendors enforce their own limits too. A cluster-wide limiter is a follow-up |
| Key compromise | KMS flavor, a short `exp`, and rotation through `next` without downtime |
