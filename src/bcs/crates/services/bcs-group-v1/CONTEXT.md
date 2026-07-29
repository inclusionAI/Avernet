# bcs-group-v1 Context

## Provides

- `GroupServiceImpl`, the transport-agnostic implementation of the BCN V1
  Group Service API.
- Principal-aware authorization, V1 projections, and orchestration over the
  existing Group, Session, Friendship, Relation, and Collaboration contracts.

## Consumes

- `bcs-service-api` application, core, and outbound-port contracts.
- Pure utility crates for asynchronous traits and JSON values.

## Allowed dependencies

- `service-api/*`
- Utility crates such as `async-trait` and `serde_json`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- Store or Legacy service implementations outside tests

## Configuration

- A future production composition root must inject all contract
  implementations and a signed Gateway Principal verifier before mounting the
  V1 delivery adapter.
- This crate must not select implementations or inspect environment variables.

## Runtime ownership

This crate owns the V1 Group use-case facade. It is deliberately not linked
into the production `bcs` binary until the signed Principal verifier and V1
router are wired together.

## Tests

- `cargo test --package bcs-group-v1 --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-group-v1 --all-targets --manifest-path src/bcs/Cargo.toml`
