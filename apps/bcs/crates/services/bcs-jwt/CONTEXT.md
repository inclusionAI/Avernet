# bcs-jwt Context

## Provides

- HS256 signing and verification for existing OAuth/session JWTs.
- A separately typed and keyed group-session Workbench connection JWT
  implementation of `GroupSessionTokenPort`.
- The trusted team-manager service credential verifier
  (`TeamManagerCredentialVerifierPort` port implementation,
  plan Task 13, spec §6.1): pure HS256 with a DEDICATED
  `team_manager_sync` purpose — register/session/group-session tokens
  never verify as team-manager credentials, and the verified result is
  only the `VerifiedTeamManagerService` (service id, env, allow scopes);
  the raw credential and signing key never enter logs, audit rows, or
  persisted commands.

## Consumes

- `bcs-service-api` group-session token port types and the
  `TeamManagerCredentialVerifierPort` port +
  `VerifiedTeamManagerService` types.
- Injected signing key material from Bootstrap.

## Allowed dependencies

- Contract and cryptographic support crates.

## Forbidden dependencies

- Delivery adapters and HTTP/WebSocket framework types.
- Bootstrap configuration or direct environment access.

## Runtime ownership

This crate owns compact JWT encoding, signature verification, claim-shape and
time validation. It does not authorize access to a stored session. For
team-manager credentials it owns purpose isolation, scope-claim decoding,
and fail-closed rejection; business scope enforcement stays in the
application layer and the authority store.

## Tests

- `cargo test --package bcs-jwt --manifest-path src/bcs/Cargo.toml`
- `tests/conformance_team_manager_credential.rs` registers
  `TeamManagerJwtVerifier` against the shared
  `team_manager_credential_verifier_port_contract_tests` harness (R25); the
  exhaustive negative lattice stays in `tests/team_manager_credential.rs`.
