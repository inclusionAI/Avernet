# Tasks: Gateway `/engine` Route

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

> **Out of this feature's scope:** the published URL itself (backend-owned and
> unchanged), HTTP under `/engine`, and the corp overlay values for the
> engine-proxy upstream.

## Task 1: Root-anchored engine route resolver `[x]`
- **Goal:** Turn `/engine/{rest}` plus a query into the upstream WebSocket URL `{engine_proxy}/proxypass/{rest}?{query}`, verbatim.
- **Files:** `src/gateway/src/gateway/community/core/forwarding/_engine.py` (new), `core/forwarding/__init__.py`
- **Done when:**
  - [x] `EngineRoute.upstream_url(path, query)` returns the rewritten URL, or `None` when the path is not under `/engine/`.
  - [x] The tail is sliced, never re-encoded or re-joined; a percent-encoded target survives byte for byte.
  - [x] `build_engine_route(raw, variables)` reads `upstreams.engine.server` against the same `servers` block, returns `None` when absent, and raises on an unknown server or a base URL with no WebSocket-mappable scheme.
  - [x] `http/https/ws/wss` all map to a `ws`/`wss` upstream; a trailing slash on the base URL does not double up.
  - [x] No transport framework imported (Rule 7).
- **Depends on:** —

## Task 2: WebSocketForwarder SPI `[x]`
- **Goal:** A duplex forwarding contract the adapter can use without knowing which client library dials the upstream.
- **Files:** `src/gateway/src/gateway/community/spi/ws_forwarder/{_models,_protocols,__init__}.py` (new)
- **Done when:**
  - [x] `WebSocketForwarder.connect(request) -> AbstractAsyncContextManager[WebSocketUpstream]`.
  - [x] `WebSocketUpstream` exposes `subprotocol` (`str`, `""` when none), `send`, `receive`, `close`.
  - [x] `WebSocketForwardRequest` carries url, headers, and requested subprotocols; `WebSocketClosedError` carries code and reason so no caller imports the client library.
  - [x] `__all__` declared; no imports from core, adapters, plugins, or bootstrap.
- **Depends on:** —

## Task 3: `websockets` outbound transport `[x]`
- **Goal:** The one implementation of the SPI, in the layer that speaks protocols.
- **Files:** `src/gateway/src/gateway/community/adapters/web/_ws_forwarder.py` (new), `adapters/web/__init__.py`, `pyproject.toml`, `uv.lock`
- **Done when:**
  - [x] Dials with `max_size=None` (transparent to frame size) and a bounded handshake timeout.
  - [x] **No read deadline** on receive — the socket outlives its one-time credential by design.
  - [x] `ConnectionClosed` is translated to the SPI's `WebSocketClosedError`, code and reason preserved.
  - [x] `websockets` added as a runtime dependency and locked.
  - [x] Lives in `adapters/web/`, not `plugins/`: there is no edition-specific flavour, and a socket library belongs in the transport layer (Rule 7 bans it from core, Rule 8 forbids `plugins/` doubling as a home for non-swappable implementations).
- **Depends on:** Task 2

## Task 4: WebSocket endpoint and bidirectional pump `[x]`
- **Goal:** Serve the handshake, authenticate it, dial the upstream, and relay frames until one side closes.
- **Files:** `src/gateway/src/gateway/community/adapters/web/_engine_ws.py` (new), `adapters/web/_forward.py`
- **Done when:**
  - [x] The upstream is dialled **before** the client is accepted; every pre-connection failure refuses the handshake.
  - [x] `scope["raw_path"]` is used so the relayed path is not percent-decoded and re-encoded.
  - [x] Route security is consulted for the path (`GET`), and resolved identities are signed onto the upstream handshake as on the HTTP path.
  - [x] Hop-by-hop, `host`, inbound `X-Avernet-Principal`, and client handshake headers are stripped before forwarding.
  - [x] Text and binary frames relay in both directions; close code and reason propagate, with 1005/1006 translated to sendable codes.
  - [x] The surviving pump task is cancelled and awaited before the upstream context manager exits.
  - [x] `_bundle` retyped to `HTTPConnection` and shared; no behaviour change on the HTTP path.
- **Depends on:** Tasks 1–3

## Task 5: Composition and configuration `[x]`
- **Goal:** Wire the route and the plugin through the composition root, and declare the prefix's upstream and identity requirement in configuration.
- **Files:** `core/forwarding/_orchestration.py`, `bootstrap/_forwarding.py`, `bootstrap/_container.py`, `adapters/web/{__init__,app}.py`, `configs/application.yaml`
- **Done when:**
  - [x] `Forwarding` carries `ws_forwarder` and `engine_route`; `app.state` publishes both.
  - [x] The forwarder is constructed in the composition root, with **no** config selector — one implementation, so a knob would have a single legal value.
  - [x] `route_security` declares `"/engine/**": {}` — no identity required, stated rather than implied.
  - [x] The `upstreams.engine` block is documented in the shipped config and left unset, so the community build refuses the route; the comment records that any L7 hop in front must pass Upgrade through with no read timeout.
  - [x] `application.yaml` still loads and every existing forwarded path behaves as before.
- **Depends on:** Tasks 1–4

## Task 6: Tests `[x]`
- **Goal:** Cover the rewrite, the SPI flavour, the endpoint, and the shipped configuration.
- **Files:** `tests/test_engine_route.py` (new), `tests/contracts/spi/test_ws_forwarder.py` (new), `tests/integration/test_engine_ws_route.py` (new), `tests/test_route_security.py`
- **Done when:**
  - [x] Unit: prefix match/non-match, `/proxypass` swap, query preservation, verbatim encoding, scheme mapping, config errors, absent block.
  - [x] Contract: the forwarder against a real `websockets` server — text, binary, subprotocol, headers, close propagation, no read deadline.
  - [x] Integration: duplex exchange, upstream path and query as the upstream saw them, and refusal for no-route, off-prefix, auth failure, and unreachable upstream; HTTP under `/engine` still 404s.
  - [x] Config: the shipped table resolves `/engine/**` to an empty requirement and leaves the version base requiring a user.
  - [x] `ruff`, `mypy`, and `basedpyright` clean for the new code.
- **Depends on:** Tasks 1–5
