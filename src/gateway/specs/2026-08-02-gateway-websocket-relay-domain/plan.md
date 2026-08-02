# Plan: Gateway WebSocket Relay

## Approach

Generalise the domain map; do not special-case a prefix.

1. **A domain declares its planes and its rewrite (core).** `Domain` gains
   `protocols` (default `{http}`, so every existing domain is untouched) and an
   optional `PathRewrite`. `DomainMap` is otherwise unchanged — `engine` resolves
   through the same leading-segment lookup as `bots`, so there is no second
   routing concept, no second config block, and no second "is it configured"
   state.

2. **Each plane refuses what it does not serve (adapters).** `forward_request`
   answers `404 no route for path` for a domain that does not declare `http`;
   the socket entrypoint does the same for one that does not declare
   `websocket`. Both planes read the same map, so without this, adding a socket
   domain would silently open an HTTP route into its upstream.

3. **The socket entrypoint names no domain.** `_relay_ws.py` is the counterpart
   of `_forward.py`: it resolves, authenticates, dials, and pumps. The
   composition root mounts it once per socket domain, building the path from
   `base_path` and the domain name, so which prefix it answers is configuration.

4. **The backend publishes inside the namespace.** `_ENGINE_PREFIX` becomes
   `/openapi/v1/engine`, which is what lets `engine` be an ordinary domain.

### Which path is used where

Two paths, deliberately:

- **Decoded** (`request.url.path` / `websocket.url.path`) for domain resolution
  and route security, on both planes. One request must not route or authorise
  differently depending on which entrypoint serves it.
- **Raw** (`scope["raw_path"]`) for what is relayed on the socket plane, so a
  percent-encoded routing target reaches the upstream exactly as its author
  wrote it. The rewrite substitutes an ASCII prefix, which is safe on either.

The HTTP plane keeps using the decoded path for forwarding, as it always has.
That is unchanged behaviour, and it is why the protocol declaration matters:
`%2e%2e` decodes to `..`, which httpx collapses when building the upstream URL,
so an auth-exempt HTTP domain would have been a path-traversal into its
upstream. Socket domains simply do not serve that plane.

### Order of operations in the socket endpoint

The upstream is dialled **before** the client is accepted. A gateway that
accepts first and then discovers it cannot reach the upstream has to tell the
client over a socket it just opened; dialling first turns every pre-connection
failure into a refused handshake, and lets the upstream's negotiated subprotocol
be echoed back exactly rather than guessed.

Pre-accept refusals close with distinct application codes (4404 no route, 4401
unauthenticated, 4500 internal, 4502 upstream unavailable). A real client sees an
HTTP 403 for all of them — a close code is not transmitted on a handshake that
never completed — so they exist for our own logs and tests, not as a
client-facing contract.

## Affected Components

New:

- `adapters/web/_relay_ws.py` — the socket entrypoint and the bidirectional pump.
- `adapters/web/_ws_forwarder.py` — the `websockets`-backed outbound transport.
- `spi/ws_forwarder/{_models,_protocols,__init__}.py` — the duplex contract.

Changed:

- `core/forwarding/_domains.py` — `Domain.protocols`, `Domain.rewrite`,
  `PathRewrite`, `DomainMap.websocket_domains()`, `Server` scheme validation and
  its per-plane origins,
  and the parse-time validation for all three.
- `core/forwarding/{__init__,_orchestration}.py` — exports; `Forwarding` gains
  `ws_forwarder`.
- `adapters/web/_forward.py` — resolve via `domain_for`, refuse non-HTTP
  domains, apply the domain's rewrite, share `_bundle` (retyped to
  `HTTPConnection`).
- `adapters/web/app.py` — mount one socket route per socket domain.
- `adapters/web/__init__.py` — re-export `WebsocketsForwarder`.
- `bootstrap/{_container,_forwarding}.py` — construct and inject the WebSocket
  forwarder.
- `configs/application.yaml` — the `engine` domain, the `engine_proxy` server
  and its var, and the `/openapi/v1/engine/**` route-security exemption.
- `pyproject.toml`, `uv.lock` — `websockets`.

Backend:

- `core/engine_runtime/connection.py` — `_ENGINE_PREFIX`, and the two docstrings
  whose reasoning depended on root anchoring.
- `adapters/http/openapi_v1/engine_runtime/connection/schemas.py` — the two
  published examples.
- `docs/openapi-v1/engine-surface.md` — the JSON example and the two prose
  paragraphs.
- `specs/2026-07-31-engine-connection-gateway-url/spec.md` — the amended
  acceptance criterion and Resolved Question 4.
- the two `test_connection.py` suites.

## Data Model Changes

None. No tables, no migrations. The gateway holds no state for a socket beyond
the two tasks relaying it.

## API / Interface Changes

### Config

```yaml
domains:
  bots:
    server: backend          # protocols unset → [http]; no rewrite → verbatim
  engine:
    server: engine_proxy
    protocols: [websocket]
    rewrite:
      from: /openapi/v1/engine
      to: /proxypass
```

Both keys are optional and their defaults reproduce today's behaviour exactly.

### `Server`

One standard for every upstream, enforced in `__post_init__` rather than at the
point of use: a `base_url` carries a scheme from `{http, https, ws, wss}`, and a
value without one fails the boot with the server named. `http_base_url` and
`websocket_base_url` re-spell it per plane, since the scheme encodes the origin
and TLS-or-not, not which planes the upstream serves.

This also fixes a latent bug: the shipped `backend.sample.com` /
`baas.sample.com` samples produced a relative URL with an empty host when
concatenated with the request path.

### `Domain`

`serves_http` / `serves_websocket` are predicates rather than a
`serves(protocol)` taking a string, because the delivery adapters are the
callers and may not import core — a shared constant would have to be duplicated
across that boundary, where it could drift. The socket entrypoint reaches the origin
through `domain.server.websocket_base_url`, which is attribute access at
runtime rather than an import, so the layer rule still holds.

### `Forwarding`

```python
Forwarding(domain_map, forwarder, catalog, ws_forwarder, ...)
```

### Published socket address

`wss://{gateway}/openapi/v1/engine/{target}{path}?…` — moved from the host root.
See the spec's Resolved Question 2 and the backend spec's Resolved Question 4.

## Dependencies

`websockets>=14` becomes a runtime dependency of `gateway-community`. Needed
twice over: uvicorn refuses an Upgrade outright unless a WebSocket
implementation is installed, and it is the client the transport dials with.

## Risks & Mitigations

- **Risk 1 — a socket domain silently becoming an HTTP proxy.** Both planes read
  one map. **Mitigation:** the protocol declaration, enforced at both
  entrypoints, with tests covering the plain 404 and the `%2e%2e` traversal that
  would otherwise reach the upstream.
- **Risk 2 — a re-encoded path.** `URL.path` is percent-decoded; relaying it
  would rewrite the routing target. **Mitigation:** the socket plane relays
  `raw_path`; a test pins that `%2F` survives.
- **Risk 3 — an idle deadline killing healthy sockets.** The exact failure the
  backend's contract warns about. **Mitigation:** no read timeout anywhere —
  only the handshake is bounded; a test asserts the dial passes no receive
  deadline.
- **Risk 4 — a rewrite that can never fire.** A `from` anchored off the domain
  would silently no-op. **Mitigation:** refused at startup, named.
- **Risk 5 — regressing the HTTP plane.** It now takes a different code path
  (`domain_for` rather than `resolve`). **Mitigation:** the existing forwarding
  suite is unchanged and still passes; a test pins that a plain domain still has
  no rewrite and forwards verbatim.
- **Risk 6 — unbounded concurrent sockets.** Real and unaddressed here; see the
  spec's Out of Scope. The first wall is file descriptors (two per relayed
  socket), which is deployment configuration, not code.

## Alternatives Considered

- **Nginx in front** — see the spec's Resolved Question 1.
- **Keeping the prefix at the host root** — the first implementation. Rejected:
  it forced a parallel routing concept for one route. See Resolved Question 2.
- **A `websocket` flag rather than a protocol list** — rejected: a domain that
  serves both planes is expressible, and a list says which without a second
  boolean.
- **Rewriting on the HTTP plane too** — available by configuration, but the
  engine domain declares `[websocket]`, so no HTTP path is opened. Keeping the
  HTTP plane's verbatim default intact matters more than symmetry here.
- **Extending the HTTP `Forwarder` SPI with a socket method** — rejected:
  request/response and duplex-until-closed are different lifecycles.

## Rollout

- **No feature flag.** The `engine` domain is the switch; a deployment that
  fronts no engine proxy removes it and the path 404s like any unknown domain.
- **Ordering.** The two repos must agree on the prefix. Both halves are in this
  change, and the endpoint is unpublished, so nothing external is holding the
  old address.
- **Backwards compatibility.** No existing path, response, or published document
  changes. `protocols` and `rewrite` default to today's behaviour.
- **Reverting** is a code revert. No state, no migration.

## Test Strategy

- **Unit — `tests/test_domain_map.py`.** Protocol defaults, socket-only and
  both-planes domains, `websocket_domains()`, unknown protocol; verbatim default,
  prefix substitution, no re-encoding, off-anchor and half-specified rewrites;
  socket origin for all four schemes, unusable scheme at startup, and that an
  HTTP domain needs no scheme (the shipped samples are bare hosts).
- **Contract — `tests/contracts/spi/test_ws_forwarder.py`.** The transport
  against a real `websockets` server: text and binary, verbatim path and query,
  forwarded headers, subprotocol negotiation, close-code propagation,
  unreachable upstream, no read deadline.
- **Integration — `tests/integration/test_relay_ws_route.py`.** Duplex exchange,
  the rewrite as the upstream saw it, `%2F` surviving, header stripping,
  principal signing, subprotocol echo, close propagation both ways, refusals for
  unconfigured / auth failure / unreachable upstream, HTTP on a socket-only
  domain 404ing, and the `%2e%2e` traversal never reaching the upstream.
- **Config — `tests/test_route_security.py`, `tests/test_domain_map.py`.** The
  shipped table exempts `/openapi/v1/engine/**`; the shipped upstreams load.
- **Live smoke** (not committed): real uvicorn, real `websockets` upstream, real
  client — the only thing that proves uvicorn accepts the Upgrade.
- **Backend.** Both `test_connection.py` suites, on the new prefix.
