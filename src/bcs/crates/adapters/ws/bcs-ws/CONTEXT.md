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
- Opaque, per-connection identities and pinned send/abort operations. Selection
  checks the original identity under the registry lock and never redirects to
  a replacement connection. Tokens are not connection identities.

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

The existing Bot response tracing boundary explicitly supplies its trace ID as
log-correlation data to bcs-observability. Callback handling restores the outer
scope on completion or cancellation; log correlation owns no span handles.

The adapter owns WebSocket streams, `mpsc::Sender`, connection registries,
frontend envelope stamping, and disconnect cleanup. It does not own routing or
message lifecycle business decisions.

Successful send ACKs are passed to the message-flow application before alias
projection. The application owns durable delivery/run identity correlation;
the adapter does not update delivery tables.

Rejected responses with an authenticated active Group run use its canonical
scope to invoke MessageFlow chat/error. Scope lookup failures retain the run and
propagate an error; absent scope keeps direct handling. State-machine correlation
and pending abort/one-shot responses retain their existing handling.

## Tests

- `cargo test --package bcs-ws --manifest-path src/bcs/Cargo.toml`
- `cargo check --workspace --all-targets --manifest-path src/bcs/Cargo.toml`

## Leadership change close contract

Bootstrap drives transport-only `ConnectionEpoch` gates on the Bot and Workbench
registries. Every handler subscribes before processing frames, including sockets
which never register and session-bound Workbench sockets. Cancellation stops new
frame dispatch and independently interrupts the outbound writer, even if its
business queue is full. The writer attempts WebSocket Close **1012** with reason
`leadership_lost`, bounded to one second. Normal disconnect/subscription cleanup
still runs; an already executing application call finishes before handler cleanup
(no unsafe cancellation of a partly completed registration).

This is a retryable transport interruption, not `bot.kicked`, token expiry or
credential revocation. Clients retain credentials and use their existing
reconnect/backoff policy through the master-routing ingress. Promotion creates a
fresh epoch and never revives cancelled sockets. There is no cross-replica
forwarding or replay guarantee for in-flight messages. API/frame DTOs are unchanged.
