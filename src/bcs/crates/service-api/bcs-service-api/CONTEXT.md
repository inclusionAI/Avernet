# bcs-service-api Context

## Provides

- Application, core, and port trait contracts for BCS.
- Shared contract-level DTOs, error types, and service container types.
- Default `Noop*` implementations used to keep contract boundaries explicit in tests and local wiring.
- Current-session state-machine permission/start contracts and the outbound
  result-publisher port used to return a completed one-shot result to chat.
- V1 `AuthenticatedCaller` contract types that preserve User, Bot, App, and
  AccessKey context without retaining transport metadata or credentials.
- The state-machine run repository contract includes an atomic
  `create_run_if_session_idle` operation for one-shot session launch
  serialization; production stores must override its compatibility default
  with backend-level locking.

## Consumes

- `bcs-protocol` types only where protocol reuse is intentional at the contract boundary.
- Async trait, serialization, logging, error, and transport-neutral time types.

## Allowed dependencies

- `bcs-protocol` wire contract crate, currently located at `service-api/bcs-protocol`
- Contract-only support crates such as `async-trait`, `serde`, `time`, `tokio`, and `thiserror`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugin-api/*` and `plugins/*`
- `external-clients/*`

## Configuration

- This crate does not read env or runtime config directly.
- Any policy or config knobs must arrive as typed inputs from bootstrap or owning services.

## Runtime ownership

The crate owns contract semantics and fail-closed default behavior. Its V1
authenticated identity types own no JWT, HTTP, Gateway signing, credential, or
Actor-selection semantics, and the crate does not own concrete runtime behavior.

## Tests

- `cargo test --package bcs-service-api --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-service-api --all-targets --manifest-path src/bcs/Cargo.toml`
