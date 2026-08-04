# bcs Context

## Provides

- BCS process entrypoint and composition root.
- Config loading, logging bootstrap, runtime assembly, and adapter registration.
- Concrete selection of services, plugins, and external clients from validated config.
- Composition of the V1 Bot, Group, Session/Message, and Invitation/Friendship
  application facades from the same runtime stores and core services used by
  legacy HTTP, then direct mounting of the versioned collaboration Router.
- Fail-closed resolution of the dedicated group-session WebSocket signing key,
  composition of one session-connection application service from the shared
  V1 Session facade, and mounting of its focused token issuance and WebSocket
  Upgrade Routers.
- Composition adapter that publishes a completed one-shot state-machine result
  through the message-flow service under the initiating Bot identity.

## Consumes

- `bcs-config-api` config DTOs and bootstrap-owned config loading helpers.
- `adapters/*`, `services/*`, `plugin-api/*`, `plugins/*`, and `external-clients/*` crates.
- `bcs-api-http` and its application-only V1 contract boundary.
- Process env, config files, and CLI flags.

## Allowed dependencies

- `service-api/*`
- `adapters/*`
- `services/*`
- `plugin-api/*`
- `plugins/*`
- `external-clients/*`
- Runtime/framework crates needed for startup and shutdown

## Forbidden dependencies

- New business rules duplicated from `services/*`
- Request-time protocol handling beyond adapter registration
- Contract definitions that belong in `service-api/*` or `plugin-api/*`
- Test-only fixtures in production wiring

## Configuration

- This crate owns config file discovery, env parsing, and CLI/bootstrap flags.
- Only this crate selects local, test, or remote concrete implementations.
- Production resolves `bcn-group-session-ws-jwt` through `EnvSecretAccess`
  using `BCS_SECRET_BCN_GROUP_SESSION_WS_JWT`; missing or empty material aborts
  Router construction and is never replaced by another JWT secret.

## Runtime ownership

The crate owns process lifecycle, adapter mounting, and startup/shutdown wiring.
It selects concrete V1 application facades and injects their Gateway Principal
verifier, but does not own request-time business policy.

## Tests

- `cargo test --package bcs --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs --all-targets --manifest-path src/bcs/Cargo.toml`
