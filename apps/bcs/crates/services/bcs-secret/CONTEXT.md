# bcs-secret Context

## Provides

- `DefaultSecretService` — implements `bcs_service_api::application::SecretService`
  by delegating to a `SecretAccessPort`. Future audit / rate-limit / redaction
  policy belongs here so adapters never reach for the port directly.

## Consumes

- `bcs_service_api::application::SecretService`
- `bcs_service_api::port::secret::SecretAccessPort`

## Allowed dependencies

- `bcs-service-api`, `async-trait`, `tracing`.

## Forbidden dependencies

- Concrete secret backends — the port keeps those decoupled.

## Tests

- `cargo test --package bcs-secret --manifest-path src/bcs/Cargo.toml`
