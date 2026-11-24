# ADR-0016: Chat transports own connections, not conversation state

## Status

accepted

## Date

2026-11-03

## Applies to

all

## Context

The WS/A2A split isolated protocol policies but passed `AsyncChatClient` back
into each transport as an owner. Transports consequently read the facade's
private connection and session state, and shared event handlers called back
into A2A to release fallback ownership. File isolation alone did not constrain
changes to this boundary.

## Decision

- The internal `ChatTransport` contract exposes operations and lifecycle,
  never raw wire clients or protocol-specific argument dictionaries.
- Each transport owns its main connection, credentials and secondary
  connections. A2A alone owns probing, fallback routing and run ownership.
  Per-session interaction availability belongs here too; main disconnection
  must not suppress dispatch on a still-healthy fallback connection.
- `AsyncChatClient` owns conversation state, concurrency, waiters, streaming,
  interaction persistence and retry/backoff policy. Transport implementations
  cannot access the facade, its session table or its terminal-state helpers.
- Explicit callbacks forward events, main disconnection and failure of a
  specified set of sessions. The facade alone applies failures to its state.
  Callbacks are bound before any handshake, including fallback and reconnect.
- Fallback terminal processing happens at the source before forwarding events;
  it does not depend on whether a conversation waiter still exists.
- Main reconnect replaces only the main wire client. Full close also disposes
  of fallback connections and ownership. ACK, waiter timeout and waiter
  cancellation do not imply that a background engine run has ended.
- Bootstrap selects implementations through a lazily importing factory injected
  into the connection pool. The facade receives an already selected transport.
  No reverse bootstrap import or default service location is permitted.

This is an internal capability contract, not a new external Service API or
registered SPI plugin. Existing wire protocols, `a2a_mode` configuration and
pool request methods remain unchanged. Direct constructors migrate to explicit
transport/factory injection; there is no hidden legacy-constructor fallback.
The existing WS/A2A wire modules remain at their legacy paths; this decision
must not expand that placement into new external infrastructure capabilities.

## Consequences

**Positive:** A2A routing/auth changes do not require shared orchestration or WS
changes. Facade tests can use a fake transport; transport tests need no facade.
Architecture tests enforce imports, state isolation and lazy WS-only startup.

**Negative:** Direct internal constructors and their tests require explicit
assembly. Both implementations must conform to the operation/callback contract.

**Risks:** Reconnect and close are distinct lifecycles; conflating them would
lose fallback runs. Source-aware ownership, handshake ordering, scoped failure,
auth isolation and background-run cancellation need regression coverage.

## Evidence

- community: `src/secbaas/community/core/service/bot_run/_async_chat_client_transport.py`
- community: `src/secbaas/community/core/service/bot_run/_async_chat_client.py`
- community: `src/secbaas/community/core/service/bot_run/_async_chat_client_a2a.py`
- community: `src/secbaas/community/bootstrap/_core_services.py`
- community: `tests/unit/core/service/bot_run/test_async_chat_transport_contract.py`
- community: `tests/architecture/test_async_chat_transport_boundaries.py`
- community: `docs/async-chat-transports.md`
