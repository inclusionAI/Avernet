# bcs-routing Context

## Provides

- Message routing service implementation for BCS.
- Transport-agnostic routing policy and delivery target selection.
- Application-facing orchestration for structured route decisions.

## Consumes

- `bcs-service-api` contract traits and DTOs.
- Pure utility crates for IDs, logging, and routing calculations.

## Allowed dependencies

- `service-api/*`
- Utility crates such as `uuid`, `serde`, and `tracing`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- `external-clients/*` crates not listed in `Allowed dependencies` above

## Configuration

- Bootstrap injects collaborators and policy knobs explicitly.
- This crate must not select delivery adapters or inspect env directly.

## Runtime ownership

The crate owns routing policy and route-decision semantics. It does not own transport sessions, request auth, or plugin selection.

## Tests

- `cargo test --package bcs-routing --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-routing --all-targets --manifest-path src/bcs/Cargo.toml`
