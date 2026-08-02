# bcs-app-bot Context

## Provides

- `BotServiceImpl`, the transport-agnostic implementation of the BCN V1 Bot
  control-plane Service API.
- Human-Principal authorization, Bot/Human projections, candidate visibility,
  reachability computation, and owner-scoped updates.

## Consumes

- `bcs-service-api` application, core, and outbound-port contracts.
- Pure utility crates for asynchronous traits and standard collections.

## Allowed dependencies

- `service-api/*`
- Utility crates such as `async-trait`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- Store or Legacy service implementations outside tests

## Configuration

- The composition root injects the environment and all contract
  implementations.
- This crate must not select implementations or inspect environment variables.

## Runtime ownership

This crate owns the V1 Bot control-plane use-case facade. The HTTP adapter may
mount it only behind the trusted Human Principal verification boundary.

## Tests

- `cargo test --package bcs-app-bot --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-app-bot --all-targets --manifest-path src/bcs/Cargo.toml`
