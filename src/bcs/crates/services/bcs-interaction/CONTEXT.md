# bcs-interaction Context

## Provides

- `InteractionService` implementation for Provider 2.0 HITL request, resolve,
  replay, invalidation, and terminal cleanup orchestration.
- Process-local `InteractionStorePort` implementation for the first delivery.

## Consumes

- `bcs-service-api` application contracts and outbound ports.
- `CanResolveInteraction` for real-time session authorization.
- `InteractionProviderPort` and `InteractionFrontendPort` for protocol-neutral
  outbound delivery.

## Allowed dependencies

- `service-api/*`
- Pure utility crates such as `serde_json`, `sha2`, `tokio`, and `tracing`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete Provider, WebSocket, database, or cache implementations
- Environment and global configuration access

## Configuration

- Bootstrap injects the store, authorization policy, Provider port, Frontend
  port, and terminal retention duration.
- This crate does not choose transports or persistence implementations.

## Runtime ownership

The service owns interaction lifecycle, validation, idempotency, and retryability
policy. Delivery adapters own SSE, WebSocket, and HTTP wire formats. The initial
store is process-local and may be replaced through `InteractionStorePort`.

## Tests

- `cargo test --package bcs-interaction --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-interaction --all-targets --manifest-path src/bcs/Cargo.toml`
