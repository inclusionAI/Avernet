# Async chat transport isolation

This is an internal refactor of the existing chat client, not a new Service API
or Plugin API. The engine wire protocols, system config `a2a_mode`, pool reuse
rules and interaction persistence contracts remain unchanged. The pool maps
that config to the facade's construction-only `transport_mode` argument.
Existing callers of the private constructor must use `transport_mode` rather
than `a2a_mode`; the exported pool API is unchanged.

## Responsibilities and dependency direction

All source paths below are relative to
`src/secbaas/community/core/service/bot_run/`.

| File | Owns | Must not own |
| --- | --- | --- |
| `_async_chat_client.py` | Session registration, concurrency/queueing, send/stream orchestration, reconnection loop | Concrete client construction, capability probing, fallback tables, HTTP token refresh policy |
| `_async_chat_events.py` | Transport-neutral callbacks, stream translation, trace restoration, interaction polling/persistence | Concrete transport dependencies or transport-specific routing/authentication |
| `_async_chat_client_transport.py` | Internal strategy contract, mode validation and lazy selection | Wire implementation or shared A2A fallback state |
| `_async_chat_client_ws.py` | WS construction and unchanged WS request/interaction signature | A2A imports, probing, fallback ownership or HTTP token minting |
| `_async_chat_client_a2a.py` | A2A probing, grey selection, attachment fallback ownership, A2A interaction auth/session arguments | Changes to the WS wire protocol |
| `_bot_websocket_client.py` / `_bot_a2a_client.py` | Their respective wire protocols | Facade session/concurrency policy |
| `_chat_errors.py` | Shared request-rejection exception | Either wire implementation |

```text
AsyncChatClient + AsyncChatEvents
              |
       transport contract/factory
          /             \
         WS             A2A
          ^              |
          +--------------+
          explicit compatibility fallback only
```

`off` lazily selects WS without importing either A2A module. A2A may call the
WS constructor for its deliberate compatibility fallback; WS cannot depend on
A2A. Shared orchestration must not inspect a low-level client's private handler
registry or the A2A strategy's ownership state. `ChatRequestError` remains
importable from `_bot_websocket_client` and the package for existing consumers.

## Lifecycle and routing invariants

- Strategies bind the facade's declarative event bridge **before** awaiting a
  primary or fallback handshake. Reconnect uses the same selected strategy and
  the same ordering. Frames arriving during `connect()` must not be dropped.
- `off` uses WS; `grey` probes A2A and falls back to WS on
  `A2aUnavailableError`; `on` propagates that error. This branch also keeps its
  explicit fail-fast behavior for `on` when no HTTP base can be derived from
  the endpoint (unlike dev's legacy WS fallback for non-WS input). No URL or
  config migration is needed for valid WS endpoints.
- Only the A2A strategy owns the secondary attachment WS connection, its lock,
  frame source marker, and per-session run ownership. Ownership survives a
  grey reconnect selecting a WS main connection until the original fallback
  run ends. Send/abort/interaction routing follows that ownership.
- Fallback ownership is released only by the owning connection's real terminal
  frames, not inject terminals or primary A2A frames. Exact session matches
  take precedence over substring matching; matching otherwise retains the
  existing case/contains rules. Disconnect terminates only fallback-owned runs.
- Pool auth refresh preserves the latest nonempty handshake credential for
  either transport's next connection. Only an active A2A client additionally
  updates its per-request auth. Background A2A interaction dispatch re-mints
  through the injected factory; failure logs and uses the last-known token.
  WS main/fallback dispatch never mints, refreshes HTTP auth, or receives the
  A2A-only `session_key` resolve argument.
- Latest dev's A2A SSE keepalive, raw upstream error reporting and request auth
  implementation stay in `_bot_a2a_client.py`, unchanged by this refactor.

## Validation and change impact

Changing the strategy contract affects both implementations and the facade.
Changing A2A probing, HTTP auth or attachment ownership should touch only A2A
implementation/tests; WS wire changes should touch only WS implementation/tests.
Shared callback changes require both regression suites. This seam is not a
replacement for the existing BaaS SPI/plugin architecture (ADRs 0001/0003/0015).

The tests have been split by lifecycle, callbacks, streaming, interactions,
authentication and A2A fallback ownership so transport-only work need not edit
the previous multi-thousand-line shared test files. Existing cases are retained.

From `apps/baas/`:

```bash
# Shared orchestration, both strategies, WS wire behavior and isolation contracts
.venv/bin/pytest -q tests/unit/core/service/bot_run/test_async_chat*.py \
  tests/unit/core/service/bot_run/test_bot_websocket_client.py

# Real TCP A2A streaming, errors, IAM, cancellation, HITL and fallback conformance
.venv/bin/pytest -q tests/community/service/bot_run/test_bot_a2a*.py \
  tests/community/service/bot_run/test_async_chat_a2a_integration.py

# Includes import/policy boundaries and a clean-interpreter WS-without-A2A check
.venv/bin/pytest -q tests/architecture
```

The TCP suite needs permission to bind local loopback ports; replacing it with
buffered ASGI transport would not verify streaming timing. The isolation check
must use a fresh interpreter so pre-imported A2A modules cannot hide a regression.
