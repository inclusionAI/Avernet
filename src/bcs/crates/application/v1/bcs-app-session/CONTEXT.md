# bcs-app-session Context

## Provides

- `SessionServiceImpl`, the transport-agnostic BCN V1 Session facade.
- StateMachine/ordinary-history merging uses the shared domain identity and
  newest-first projection. Durable messages take precedence across legacy IDs;
  workflow metadata does not group distinct outputs into a single chat round.
- `GroupSessionConnectionServiceImpl`, which authorizes session access before
  issuing a session-scoped Workbench WebSocket token, verifies that token into
  an immutable connection binding, and revalidates the exact bound Session at
  connect time through the V1 Session facade.
- `SessionFileApplicationServiceImpl`, which selects a Human or owned Bot
  actor, authorizes Session membership and file mutations, enriches legacy
  file commands, and orchestrates best-effort completion notifications.

## Consumes

- `bcs-service-api` application, Core, repository-port, and connection-token
  port contracts.
- The existing transport-neutral Session File lifecycle service plus injected
  Session, Group, Bot Registry, and System Message service interfaces.
- Pure utility crates for asynchronous traits and JSON values.

## Allowed dependencies

- `service-api/*`
- Pure domain history identity/projection helpers in `contracts/bcs-domain`
- Utility crates such as `async-trait` and `serde_json`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*` in production code
- Direct environment or transport access

## Configuration

- Bootstrap injects `state_machine_history.persistence_enabled` and
  `message_history.state_machine_cutoff_timestamp` into both history entry points.
  Enabled Sessions with original `created_at >= cutoff` read frozen content from
  MessageRepo only; older Sessions and disabled persistence use the runtime path.
  The cutoff defaults to 0. Session/Group authorization still applies; selected
  message reads never fall back or backfill. One-shot StateMachine history uses
  this same cutoff; ordinary mixed chat keeps its separate cutoff policy.
  Configuration changes require restart; the removed `read_source` is rejected.

- The composition root injects Session and connection-token service
  implementations.
- This crate must not select implementations or inspect environment variables.

## Runtime ownership

This crate owns V1 Session and Session File authorization and orchestration.
For files, it enforces membership and the owner-or-owning-Human mutation rule
before storage mutation, and sends completion notification only after a
successful lifecycle transition. Delivery adapters may translate HTTP and
WebSocket requests into these contracts but must not reimplement session/file
authorization or token policy.

## Tests

- `cargo test --package bcs-app-session --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-app-session --all-targets --manifest-path src/bcs/Cargo.toml`
