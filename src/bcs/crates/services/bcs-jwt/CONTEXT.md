# bcs-jwt Context

## Provides

- HS256 signing and verification for existing OAuth/session JWTs.
- A separately typed and keyed group-session Workbench connection JWT
  implementation of `GroupSessionTokenPort`.

## Consumes

- `bcs-service-api` group-session token port types.
- Injected signing key material from Bootstrap.

## Allowed dependencies

- Contract and cryptographic support crates.

## Forbidden dependencies

- Delivery adapters and HTTP/WebSocket framework types.
- Bootstrap configuration or direct environment access.

## Runtime ownership

This crate owns compact JWT encoding, signature verification, claim-shape and
time validation. It does not authorize access to a stored session.

## Tests

- `cargo test --package bcs-jwt --manifest-path src/bcs/Cargo.toml`
