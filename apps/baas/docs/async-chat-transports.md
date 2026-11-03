# Async chat transport isolation

This is an internal capability contract (consumer → transport), not a new
external Service API or registered Plugin API. Engine wire protocols, system
config `a2a_mode`, pool request methods, reuse rules and interaction persistence
are unchanged. See [ADR-0016](adr/0016-chat-transport-state-ownership.md).

## Responsibilities and dependency direction

Source paths in the table are relative to
`src/secbaas/community/core/service/bot_run/`, except bootstrap.

| File | Owns | Must not own |
| --- | --- | --- |
| `_async_chat_client.py` | Sessions, concurrency/queueing, send/stream orchestration, retry/backoff policy | Wire clients, concrete selection, fallback tables or auth policy |
| `_async_chat_events.py` | Event handling, stream translation, trace restoration, interaction polling/persistence | Concrete transports, fallback release or protocol routing |
| `_async_chat_client_transport.py` | `ChatTransport`, callbacks, factory signature and mode validation | Implementations, selection or mutable session state |
| `_async_chat_transport_base.py` | Main connection lifecycle, callback registration, operation forwarding, credential snapshot | Conversation state, retry/backoff or A2A policy |
| `_async_chat_client_ws.py` | WS construction and unchanged WS signatures | A2A imports, probing, fallback or HTTP token minting |
| `_async_chat_client_a2a.py` | A2A probing, grey fallback, secondary WS lifecycle, run ownership, HTTP interaction auth/routing | Facade references or access to its state/helpers |
| `bootstrap/_chat_transport.py`, `bootstrap/_core_services.py` | Concrete selection, lazy imports and pool factory injection | Conversation policy |
| `_bot_websocket_client.py` / `_bot_a2a_client.py` | Their respective wire protocols | Facade session/concurrency policy |
| `_chat_errors.py` | Shared rejection/disconnection exceptions | Either wire implementation |

```text
bootstrap ── injects factory ──> AsyncChatClientPool
    │                                  │ injects selected ChatTransport
    └── selects WS / A2A         AsyncChatClient + AsyncChatEvents
                                       │ operations
                                  ChatTransport
                                   /       \
                                  WS       A2A
                                   ^        │
                                   └────────┘ explicit compatibility fallback
```

There is no `owner` constructor argument, reverse bootstrap import or raw-client
`*_target()` API. The facade depends only on the contract. Implementations
receive narrow callbacks, never the facade, `SessionState` or its mutable tables.
A2A may use the stable WS constructor for compatibility; WS cannot depend on
A2A. `off` must work without importing either A2A module.

## Operation and callback contract

- `send`, `inject` and `abort` receive the existing chat arguments and return
  the raw engine response. The facade maps `ok=False` to `ChatRequestError` for
  send/inject. Transport run ownership is cleared on an explicit send rejection
  (`ok=False` or `ChatRequestError`), never on uncertain ACK timeout/cancellation.
- `resolve_interaction` receives the session key and resolution. The transport
  chooses the wire connection and supplies only that protocol's arguments; it
  returns the existing `EngineInteractionResolveExchange` for facade persistence.
  `can_resolve_interaction(session_key)` reports that session's uplink availability;
  a main reconnect must not suppress dispatch on a healthy fallback connection.
- `is_connected`, `transport_name`, `uri`, `has_active_sessions()` and
  `tracks_session()` expose diagnostics and pool-relevant occupancy, not clients.
- `refresh_outbound_auth(headers)` preserves the latest nonempty credential
  snapshot for the next handshake. Only an active A2A leg additionally refreshes
  per-request HTTP auth. The transport copies the caller's header dictionary.
- `bind_events(ChatTransportCallbacks(...))` is required before connecting and
  snapshots the event registry. Rebinding a live main connection fails clearly.
  `events` maps the existing event names to handlers; callbacks retain their
  existing payload/dispatch signature, including wildcard event logging.
- `on_disconnect(event, payload)` notifies a current main connection failure;
  the facade terminates unfinished waiters and signals its retry monitor.
- `on_sessions_failed(keys, message, source)` reports failure of exactly the
  supplied registered keys. The facade alone changes its unfinished states;
  completed/unknown sessions and unrelated waiters are untouched. A fallback
  failure does not trigger main reconnect.

## Lifecycle and routing invariants

- Event/disconnect handlers are registered **before** any main/fallback
  handshake. Failed or cancelled handshakes dispose of their candidates.
- `connect()` establishes the main connection and returns server information.
  `reconnect()` replaces only main, retaining callbacks and secondary runs.
  `close()` disposes of all connections and transport ownership. Intentional
  disposal does not emit a main-disconnect or scoped-failure callback.
- `off` uses WS; `grey` probes A2A and selects WS on `A2aUnavailableError`;
  `on` propagates it. This branch retains fail-fast behavior for `on` if no HTTP
  base can be derived (dev's legacy implementation fell back for non-WS input).
  Valid WS endpoints need no URL/config migration.
- A2A alone owns the attachment fallback connection, lock and per-session
  ownership. Existing fallback runs remain routable for abort/interaction even
  if grey main reconnect selects WS. Full close also attempts fallback cleanup
  if closing main fails.
- Real terminal frames from the **current fallback** release ownership before
  forwarding to the facade, including when no waiter exists. Inject terminals,
  primary events and retired fallback frames cannot release it. Exact keys take
  priority over the existing contains/optional case-insensitive matching.
- ACK return, waiter timeout/cancellation and early stream close do not imply
  engine termination; fallback ownership survives them. Fallback disconnect
  clears its ownership and reports only those keys as an unknown run outcome.
- Background A2A interaction dispatch re-mints using its injected token minter;
  mint failure logs and uses the last-known token. WS main/fallback never mints,
  refreshes HTTP auth or receives A2A's extra `session_key` resolve argument.
- Dev's SSE keepalive, upstream error and request auth implementations stay in
  `_bot_a2a_client.py`, unchanged by this refactor, as is the WS wire client.

## Internal constructor migration

Constructors are explicit: the pool requires `transport_factory`; the facade
requires an already selected `transport`. No implicit fallback imports bootstrap
from core. Production bootstrap injects `build_chat_transport` as a callable
(`providers.Object`), not a prebuilt connection:

```python
# Assembly layer only; normal consumers continue using pool.get(...).
pool = AsyncChatClientPool(transport_factory=build_chat_transport)
transport = build_chat_transport(uri=uri, mode="off", headers=headers)
client = AsyncChatClient(transport=transport, max_retries=1)
```

The pool's existing per-call token minter is passed to new transports; reuse
refreshes credentials without replacing the original minter context. Direct
internal/enterprise constructor users must adopt injection; pool `get(...)`
callers and wire/API/config consumers need no migration.

## Validation and change impact

A2A auth/probing/ownership changes should touch only its implementation/tests;
WS wire changes should touch only WS implementation/tests. Contract or shared
callback changes require both regressions. This seam does not replace the
BaaS SPI/plugin architecture (ADRs 0001/0003/0015); legacy wire paths are retained
rather than mixing a package relocation into this refactor.

Tests prove both sides independently: `test_async_chat_facade_contract.py`
exercises the facade with a strict fake capability; transport isolation tests
bind callbacks with no facade. Lifecycle/ownership regressions cover handshake
ordering, cancellation, scoped failures, reconnect vs close and auth isolation.
AST boundaries prevent owner access, raw targets, concrete facade dependencies
and selection outside bootstrap. A fresh-interpreter check enforces WS without
A2A imports or HTTP minting.

From `apps/baas/`:

```bash
.venv/bin/pytest -q tests/unit/core/service/bot_run \
  tests/community/service/bot_run/test_bot_a2a*.py \
  tests/community/service/bot_run/test_async_chat_a2a_integration.py \
  tests/architecture
```

The community suite uses real loopback TCP for streaming, errors, cancellation
and HITL; it needs permission to bind local ports. Buffered ASGI transport is
not an equivalent streaming-timing test. Unit/architecture suites require no
external services. The full Singlebox product gate remains a separate check.
