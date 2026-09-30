# bcs-secret-local Context

## Provides

- `InMemorySecretAccess` — explicit map of secret name → user/value, used by tests.
- `EnvSecretAccess`      — env-var snapshot lookup (`${prefix}<NAME>`).
- `NoopSecretAccess`     — always returns `SecretAccessError::Unavailable`, default safety net.

All three implement `bcs_service_api::port::secret::SecretAccessPort`.

## Consumes

- `bcs-service-api`

## Allowed dependencies

- `bcs-service-api`, `serde`, `serde_json`, `tokio` (sync + fs), `tracing`.

## Forbidden dependencies

- Any production backend client. The whole point of this crate is that
  open-source / unit-test builds compile without those.

## Tests

- `cargo test --package bcs-secret-local --manifest-path src/bcs/Cargo.toml`
