# Plan: Gateway `/engine` Route

## Approach

Three new seams, each landing in the layer the gateway's architecture already
assigns it, plus configuration.

1. **Routing (core, transport-agnostic).** `EngineRoute` holds the engine-proxy
   `Server` and turns a request path into an upstream WebSocket URL by swapping
   `/engine` for `/proxypass` and carrying the rest through unchanged. It sits
   beside `DomainMap` in `core/forwarding/`, but it is deliberately **not** a
   domain: `DomainMap` routes the segment after `base_path: /openapi/v1`, and
   this prefix is anchored at the root. Anchoring is load-bearing — the backend
   refuses a gateway `base_url` with a path component precisely so `/engine`
   cannot be pushed off the root (`_gateway_ws_base` in the backend's
   `core/engine_runtime/connection.py`) — so the gateway must not make the
   anchor configurable either.

2. **The upstream socket (SPI + web adapter).** A `WebSocketForwarder` SPI
   mirroring the existing `Forwarder`: an async context manager yielding a duplex
   `WebSocketUpstream`, implemented by `WebsocketsForwarder` in `adapters/web/`.

   It is deliberately **not** under `plugins/`. That directory means an
   edition-swappable implementation of a plugin contract — every other entry has
   a real or documented second flavour (`database/sqlite`,
   `secret_resolver/community`, `schema_catalog/file`, `runner/bare`) — and there
   is no corp variant of "dial a WebSocket". A selector there would have been a
   config knob with one legal value, which Rule 8 calls role ambiguity and
   AGENTS.md calls speculative configurability. `adapters/web` is also where
   Rule 7 puts a socket library by construction: the rule bans transport
   frameworks in *core*, which makes the transport layer their home.

   The SPI stays regardless: it is what lets the composition root hand the web
   adapter a typed collaborator and lets tests relay against a stub instead of a
   live socket.

3. **The relay (web adapter).** A Starlette WebSocket endpoint at
   `/engine/{full_path:path}` that resolves the route, authenticates, dials the
   upstream, accepts the client, and then pumps frames in both directions until
   one side closes. It reads its collaborators off `app.state`, as
   `forward_request` does, so the adapter still imports neither core nor plugins.

The HTTP path is untouched. `forward_request` keeps its catch-all, `/engine`
stays outside every configured domain, and an HTTP request there keeps answering
`404 no route for path`.

### Order of operations in the endpoint

The upstream is dialled **before** the client is accepted. A gateway that
accepts first and then discovers it cannot reach the upstream has to tell the
client over a socket it just opened; dialling first turns every pre-connection
failure into a refused handshake, and lets the upstream's negotiated subprotocol
be echoed back to the client exactly rather than guessed.

Pre-accept refusals close with distinct application codes (4404 no route, 4401
unauthenticated, 4502 upstream unavailable). A real client sees an HTTP 403 for
all three — a WebSocket close code is not transmitted on a handshake that never
completed — so the codes exist for the gateway's own logs and tests. They are
not a client-facing contract.

## Affected Components

New:

- `src/gateway/src/gateway/community/core/forwarding/_engine.py` — `EngineRoute`,
  the prefix constants, and the config reader.
- `src/gateway/src/gateway/community/spi/ws_forwarder/{_models,_protocols,__init__}.py`
  — the duplex forwarding contract.
- `src/gateway/src/gateway/community/adapters/web/_ws_forwarder.py` — the
  `websockets`-backed outbound transport.
- `src/gateway/src/gateway/community/adapters/web/_engine_ws.py` — the endpoint
  and the bidirectional pump.

Changed:

- `core/forwarding/__init__.py` — re-export `EngineRoute`.
- `core/forwarding/_orchestration.py` — `Forwarding` gains `ws_forwarder` and
  `engine_route`, so the composed subsystem still hands the adapter everything
  it needs through one object.
- `bootstrap/_forwarding.py` — build the engine route from the same
  `user_config.upstreams` section the domain map is built from.
- `bootstrap/_container.py` — construct and inject the WebSocket forwarder
  directly (no plugin selector).
- `adapters/web/__init__.py` — re-export `WebsocketsForwarder` so the
  composition root reaches it without an absolute private-module import.
- `adapters/web/app.py` — register the WebSocket route and publish the new
  `app.state` entries.
- `adapters/web/_forward.py` — `_bundle` retyped to `HTTPConnection` so the
  WebSocket endpoint builds its credential bundle the same way. No behaviour
  change.
- `configs/application.yaml` — the `/engine/**` route-security exemption and
  the documented (commented) `engine` upstream block.
- `pyproject.toml`, `uv.lock` — `websockets`.

## Data Model Changes

None. No tables, no migrations. The gateway holds no state for a socket beyond
the two tasks relaying it.

## API / Interface Changes

### New served route

`WebSocket /engine/{rest}` → `WebSocket {engine_proxy}/proxypass/{rest}`, query
preserved.

Nothing is added to the published OpenAPI document. The served document is
generated from each domain's description; this route has no domain, and a
WebSocket has no OpenAPI representation to add.

### `Forwarding`

```python
Forwarding(domain_map, forwarder, catalog, ws_forwarder, engine_route, ...)
```

`engine_route` is `EngineRoute | None`. `None` is a real contract state — this
deployment fronts no engine proxy — and the endpoint answers accordingly. It is
required rather than defaulted so every construction site states which it is.

### `WebSocketForwarder` SPI

```python
class WebSocketForwarder(Protocol):
    def connect(
        self, request: WebSocketForwardRequest
    ) -> AbstractAsyncContextManager[WebSocketUpstream]: ...


class WebSocketUpstream(Protocol):
    @property
    def subprotocol(self) -> str: ...  # "" when none was negotiated
    async def send(self, message: str | bytes) -> None: ...
    async def receive(self) -> str | bytes: ...  # raises WebSocketClosedError
    async def close(self, code: int, reason: str) -> None: ...
```

`subprotocol` is `str` and not `str | None`: "no subprotocol" is the empty
string, so no caller has to branch on a sentinel.

## Key Files & Functions

### `core/forwarding/_engine.py`

- `ENGINE_PREFIX = "/engine"`, `PROXYPASS_PREFIX = "/proxypass"` — constants, not
  configuration. Both halves are a fixed contract with the backend, which spells
  the same two strings as constants of its own.
- `EngineRoute.upstream_url(path, query) -> str | None` — `None` when *path* is
  not under the prefix. The tail is taken by slicing, never by parsing or
  re-joining segments, so a percent-encoded target such as `ARCA_x%40host%3A0`
  reaches the upstream exactly as the provider wrote it.
- `build_engine_route(raw, variables) -> EngineRoute | None` — reads the
  `engine.server` key out of the same `upstreams` mapping the domain map reads,
  resolving it against the same `servers` block. Absent → `None`. An unknown
  server name, or a base URL whose scheme has no WebSocket equivalent, raises at
  startup rather than at the first handshake.
- Scheme mapping `{http: ws, https: wss, ws: ws, wss: wss}` mirrors the backend's
  `_WS_SCHEMES`, so a deployment may spell the upstream either way.

### `adapters/web/_engine_ws.py`

`forward_websocket(websocket)`:

1. `engine_route` off `app.state`; absent → refuse 4404.
2. Raw path and query from the ASGI scope. **`scope["raw_path"]` is preferred
   over `websocket.url.path`**, because the latter is percent-*decoded*: relaying
   it would re-encode the target and the engine path, and the contract is that
   everything past the prefix travels verbatim. The decoded path is the fallback
   for servers that do not set `raw_path`.
3. `upstream_url(path, query)`; `None` → refuse 4404.
4. `authenticator.authenticate("GET", path, bundle)` — a handshake is a `GET`,
   and the route-security table is consulted for this path like any other.
   `AuthError` → refuse 4401.
5. Handshake headers: the client's, minus hop-by-hop, minus the WebSocket
   handshake headers the client library sets itself
   (`sec-websocket-key`/`-version`/`-extensions`/`-protocol`), minus `host` and
   any caller-supplied `X-Avernet-Principal`. When authentication produced
   identities they are signed onto the upstream handshake exactly as the HTTP
   path does; under the shipped exemption the set is empty and no header is
   added.
6. `ws_forwarder.connect(...)`; failure → refuse 4502.
7. `websocket.accept(subprotocol=...)` echoing what the upstream negotiated.
8. Two tasks, `client → upstream` and `upstream → client`, run until the first
   finishes; the other is cancelled and the close is propagated.

Close-code translation: 1005 (no status) and 1006 (abnormal) cannot be sent in a
close frame, so they become 1000 and 1011 respectively; reasons are truncated to
the 123-byte limit. Everything else crosses unchanged.

### `adapters/web/_ws_forwarder.py`

- `max_size=None` — the gateway is transparent, and the library's 1 MiB default
  would close a large chat frame with 1009 as though the peer had misbehaved.
- `open_timeout` bounds the **handshake** only. There is deliberately **no read
  timeout**: the credential is checked once at the handshake and the socket
  outlives its expiry by design, so an idle deadline would kill healthy
  connections. Liveness is left to the protocol's own ping/pong, which does not
  penalise a quiet-but-alive peer.

## Dependencies

`websockets>=14` becomes a runtime dependency of `gateway-community`. It is
needed twice over: uvicorn refuses an Upgrade outright unless a WebSocket
implementation is installed, and it is the client the plugin dials with. It is
pure Python, is what `uvicorn[standard]` already pulls in, and ships type
information.

## Risks & Mitigations

- **Risk 1 — a re-encoded path.** Starlette's `URL.path` is percent-decoded.
  Relaying it would rewrite the target (`ARCA_x@0:20003`) and any encoded segment
  of the engine path, breaking a socket for reasons invisible in the published
  URL. **Mitigation:** read `scope["raw_path"]` and slice it; a test pins that an
  encoded target survives byte for byte.

- **Risk 2 — an idle-read deadline kills healthy sockets.** The exact failure the
  backend's contract warns about. **Mitigation:** no read timeout anywhere on the
  relay — not in the plugin, not in the pump; only the handshake is bounded. A
  test asserts the plugin does not pass a receive deadline.

- **Risk 3 — the exemption becomes an accident.** If the prefix were served
  without consulting the route-security table, the table would stop describing
  the gateway and nobody could tighten the prefix. **Mitigation:** the endpoint
  authenticates like every other route; the exemption is the explicit empty
  requirement in `application.yaml`, and an overlay that omits it fails closed
  with a refused handshake rather than an open unauthenticated socket.

- **Risk 4 — a leaked upstream connection or a task that outlives its socket.**
  A duplex relay has two tasks and two sockets. **Mitigation:** the upstream is
  held by an async context manager for the whole endpoint, and the pump cancels
  and awaits the surviving task before returning, so the context manager's exit
  is the last thing that runs.

- **Risk 5 — an L7 hop in front of the gateway that does not pass Upgrade.** Out
  of this repository's control. **Mitigation:** documented in the spec's resolved
  question and in the configuration comment, so the requirement travels with the
  route rather than being rediscovered in an incident.

## Alternatives Considered

- **Nginx in front of the gateway.** Rejected — see the spec's resolved question
  1. Cheaper to write, but it splits routing truth away from `application.yaml`,
  leaves `route_security` describing a posture the host does not have, ships an
  artifact this repository has no deployment for, and cannot be covered by the
  gateway's tests.
- **A new domain under `base_path`.** Rejected: the published address is
  root-anchored by construction, and the backend refuses a gateway base URL with
  a path precisely so it stays that way.
- **Making the prefix and the rewrite configurable.** Rejected: both are a fixed
  contract with the backend, which hard-codes the same two strings. A knob here
  would only let a deployment break the contract silently.
- **Extending the existing `Forwarder` SPI with a WebSocket method.** Rejected:
  request/response and duplex-until-closed are different lifecycles, and every
  existing flavour would have to grow a method it cannot implement.
- **Byte-level relaying under the HTTP catch-all.** Not possible: uvicorn
  terminates the WebSocket handshake and hands the application frames, so there
  is no byte stream to relay, and disabling that would mean implementing RFC 6455
  in the gateway.

## Rollout

- **No feature flag.** The `upstreams.engine` block is the switch. Absent — the
  community build's state — the route refuses every handshake, which is exactly
  what a deployment that fronts no engine proxy should do.
- **Ordering.** This is the deliverable the backend's rollout waits on. Once it
  is deployed, an overlay may fill the backend's `user_config.gateway` block and
  this gateway's `upstreams.engine` block; until both are filled, neither side
  publishes or serves anything new.
- **Backwards compatibility.** Nothing owed. No existing path, response, or
  document changes; the route is additive and previously answered 404.
- **Reverting** is a code revert. No state, no migration, no published contract.

## Test Strategy

- **Unit — `tests/test_engine_route.py`.** The rewrite: prefix match and
  non-match, `/engine` with no tail, the `/proxypass` swap, query preservation,
  verbatim percent-encoding, scheme mapping for all four accepted schemes, a
  trailing slash on the base URL, an unknown server name, a base URL with no
  usable scheme, and an absent `engine` block resolving to `None`.
- **Contract — `tests/contracts/spi/test_ws_forwarder.py`.** The community
  plugin against a real `websockets` server: text and binary round-trips, the
  negotiated subprotocol, forwarded handshake headers, close-code propagation,
  and that no receive deadline is imposed.
- **Integration — `tests/integration/test_engine_ws_route.py`.** The endpoint
  wired to a stub upstream through `TestClient`: a full duplex exchange, the
  rewritten upstream path and preserved query as the upstream saw them, refusal
  when no route is configured, refusal on a path outside the prefix, refusal on
  an authentication failure, refusal when the upstream will not connect, and that
  an HTTP request to `/engine/...` still answers `404 no route for path`.
- **Configuration — extends `tests/test_route_security.py`.** The shipped table
  resolves `/engine/**` to an empty requirement, and still resolves a
  version-base path to `user: required`.

Not covered here: that a corp overlay names a reachable engine proxy, and that
whatever L7 hop fronts the gateway in a given deployment passes Upgrade through.
Both are deployment facts about components outside this repository.
