# bcs-ws Context

## Provides

- WebSocket delivery adapter for BCS.
- Bot runtime entry under `src/bot/`.
- Workbench/Web entry under `src/web/`.
- Session-bound Workbench connect delivery that delegates current Session
  authorization to the V1 group-session connection service; legacy
  user-bound `/ws` connect remains on the Workbench session service.
- Focused `/openapi/v1/collaboration/messages/ws` Upgrade boundary that verifies
  the query credential before switching protocols and binds its immutable
  tenant/User/Group/Session scope into the existing Workbench connection loop.
- Shared connection-state helpers under `src/shared/`.
- Implementations of `BotDeliveryPort` and `FrontendDeliveryPort`.

## Consumes

- `bcs-service-api` traits and DTOs.
- `bcs-protocol` wire frames.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

- `service-api/*`
- WebSocket framework crates such as `axum`, `tokio`, and `futures`

## Forbidden dependencies

- `bootstrap/bcs`
- `services/*` concrete crates
- Legacy DingTalk runtime modules
- Auxiliary Ding logger crate
- generic `channel/` abstractions

## Configuration

- Bootstrap injects delivery services, auth mode, connection-related limits,
  and the shared group-session connection service used by token verification
  and connect-time authorization.
- The adapter must not select concrete routing or message-flow implementations at runtime.

## Runtime ownership

The adapter owns WebSocket streams, `mpsc::Sender`, connection registries,
frontend envelope stamping, and disconnect cleanup. It does not own routing or
message lifecycle business decisions.

## Tests

- `cargo test --package bcs-ws --manifest-path src/bcs/Cargo.toml`
- `cargo check --workspace --all-targets --manifest-path src/bcs/Cargo.toml`
