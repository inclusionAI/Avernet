# Tasks: Gateway WebSocket Relay

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

> **Out of this feature's scope:** verifying the proxypass credential at the
> gateway (the hop behind it does), concurrency limits and socket metrics
> (measure before bounding), and the corp engine-proxy host (an overlay).

## Task 1: Domains declare protocols and an optional rewrite `[x]`
- **Goal:** Make "which plane serves this domain" and "does its path get rewritten" configuration, so no prefix needs special-casing in code.
- **Files:** `core/forwarding/_domains.py`, `core/forwarding/__init__.py`
- **Done when:**
  - [x] `Domain.protocols` defaults to `{http}`; every existing domain behaves identically.
  - [x] `Domain.serves_http` / `.serves_websocket` are predicates, not a string-keyed lookup — delivery adapters may not import core, so a shared constant would be duplicated across the boundary.
  - [x] `PathRewrite` substitutes one prefix and carries the tail through untouched; no rewrite means verbatim.
  - [x] `rewrite.from` must start at the domain's own prefix — a rule that could never fire is refused at startup, not silently ignored.
  - [x] `Domain.websocket_base_url` is derived at parse time (`http→ws`, `https→wss`, `ws`/`wss` as written); an unusable scheme fails the boot, and an HTTP-only domain needs no scheme.
  - [x] `DomainMap.websocket_domains()` lists socket domains for the composition root.
  - [x] Unknown protocol, non-list protocols, and a half-specified rewrite all raise at startup.
- **Depends on:** —

## Task 2: WebSocketForwarder SPI `[x]`
- **Goal:** A duplex contract the entrypoint can use without knowing which client library dials.
- **Files:** `spi/ws_forwarder/{_models,_protocols,__init__}.py`
- **Done when:**
  - [x] `connect(request) -> AbstractAsyncContextManager[WebSocketUpstream]`.
  - [x] `WebSocketUpstream` exposes `subprotocol` (`str`, `""` when none), `send`, `receive`, `close`.
  - [x] `WebSocketClosedError` carries code and reason, so no caller imports the client library.
- **Depends on:** —

## Task 3: `websockets` outbound transport `[x]`
- **Goal:** The one implementation of the SPI, in the layer that speaks protocols.
- **Files:** `adapters/web/_ws_forwarder.py`, `adapters/web/__init__.py`, `pyproject.toml`, `uv.lock`
- **Done when:**
  - [x] `max_size=None` (transparent to frame size), bounded handshake timeout, `proxy=None` so an ambient `HTTPS_PROXY` cannot re-route a configured upstream.
  - [x] **No read deadline** — the socket outlives its one-time credential by design.
  - [x] `ConnectionClosed` translated to `WebSocketClosedError`, code and reason preserved.
  - [x] In `adapters/web/`, not `plugins/`: no edition-specific flavour exists, and Rule 7 puts a socket library in the transport layer.
- **Depends on:** Task 2

## Task 4: Socket entrypoint and bidirectional pump `[x]`
- **Goal:** Serve the handshake for any socket domain, dial the upstream, relay until one side closes.
- **Files:** `adapters/web/_relay_ws.py`, `adapters/web/_forward.py`
- **Done when:**
  - [x] Resolves and authenticates on the **decoded** path, exactly as the HTTP plane does; relays the **raw** path so the tail travels byte for byte.
  - [x] Refuses a domain that does not declare `websocket`.
  - [x] Dials the upstream **before** accepting the client; every pre-connection failure refuses the handshake.
  - [x] Hop-by-hop, `host`, inbound `X-Avernet-Principal` and client handshake headers stripped; resolved identities signed on as the HTTP path does.
  - [x] Text and binary relay both ways; close code and reason propagate, 1005/1006 translated to sendable codes, reasons truncated to 123 bytes.
  - [x] The surviving pump task is cancelled and awaited before the upstream context manager exits.
  - [x] Names no domain — `relay_route(base_path, domain)` builds the mount path.
- **Depends on:** Tasks 1–3

## Task 5: HTTP plane refuses non-HTTP domains `[x]`
- **Goal:** Adding a socket domain must not open an HTTP route into its upstream.
- **Files:** `adapters/web/_forward.py`
- **Done when:**
  - [x] `forward_request` resolves via `domain_for` and answers `404 no route for path` when the domain does not declare `http` — indistinguishable from an unconfigured domain.
  - [x] The domain's rewrite is applied when one is declared; absent, the path forwards verbatim as before.
  - [x] A `%2e%2e` traversal under a socket-only domain never reaches the upstream.
- **Depends on:** Task 1

## Task 6: Composition and configuration `[x]`
- **Goal:** Wire the transport, mount one socket route per socket domain, and declare the engine domain.
- **Files:** `bootstrap/{_container,_forwarding}.py`, `core/forwarding/_orchestration.py`, `adapters/web/app.py`, `configs/application.yaml`
- **Done when:**
  - [x] `Forwarding` carries `ws_forwarder`; the bespoke `engine_route` and its config block are deleted.
  - [x] The forwarder is constructed in the composition root, with no config selector — one implementation, so a knob would have a single legal value.
  - [x] `app.py` mounts a socket route per `websocket_domains()` entry; no domain is named in code.
  - [x] `application.yaml` ships the `engine` domain **enabled**, with `engine_proxy` under `servers` and a sample var, consistent with every neighbouring domain.
  - [x] `route_security` declares `"/openapi/v1/engine/**": {}`, with a comment recording that it is the first anonymous rule in the namespace.
- **Depends on:** Tasks 1–5

## Task 7: Backend publishes inside the namespace `[x]`
- **Goal:** Move the published prefix so `engine` can be an ordinary gateway domain.
- **Files:** `core/engine_runtime/connection.py`, `adapters/http/openapi_v1/engine_runtime/connection/schemas.py`, `docs/openapi-v1/engine-surface.md`, `specs/2026-07-31-engine-connection-gateway-url/spec.md`, both `test_connection.py`
- **Done when:**
  - [x] `_ENGINE_PREFIX = "/openapi/v1/engine"`.
  - [x] The bare-origin guard survives with its new reason (a base path would double the namespace), not its old one.
  - [x] Both published OpenAPI examples updated.
  - [x] The reference doc's JSON example and both prose paragraphs updated.
  - [x] The superseded acceptance criterion is amended in place **and** a dated Resolved Question 4 records why.
- **Depends on:** Task 1

## Task 8: Tests `[x]`
- **Files:** `tests/test_domain_map.py`, `tests/test_route_security.py`, `tests/contracts/spi/test_ws_forwarder.py`, `tests/integration/test_relay_ws_route.py`
- **Done when:**
  - [x] Unit: protocol defaults, socket-only / both-planes, `websocket_domains()`, unknown protocol; verbatim default, prefix substitution, no re-encoding, off-anchor and half-specified rewrites; socket origin per scheme, unusable scheme, HTTP domain needing none.
  - [x] Contract: the transport against a real `websockets` server.
  - [x] Integration: duplex exchange, rewrite as the upstream saw it, `%2F` surviving, refusals, HTTP-on-socket-domain 404, `%2e%2e` traversal blocked.
  - [x] Config: the shipped table exempts the socket prefix; the shipped upstreams load.
  - [x] Backend: both `test_connection.py` suites pass on the new prefix.
  - [x] `ruff`, `mypy`, `basedpyright` no worse than baseline; live smoke re-run.
- **Depends on:** Tasks 1–7
