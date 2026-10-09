# bcs-app-bot Context

## Provides

- `BotSelfServiceImpl` authenticates an Agent identity token through the injected
  `AgentIdentityPort`, then asks the registry Core for persisted registration.
  Invalid/non-Agent identities, verifier unavailability, ambiguous registration,
  and storage failure stay distinct application errors. Missing registration is
  a successful read with no Bot, and token refresh does not change identity.

- `BotServiceImpl`, the transport-agnostic implementation of the BCN V1 Bot
  control-plane Service API.
- Human-Principal authorization, Bot/Human projections, candidate visibility,
  reachability computation, and owner-scoped updates.
- `BotManagerServiceImpl` (plan Task 13, spec §6): the Human-only manager
  list/grant/revoke use cases. Every use case asks the centralized
  `BotAuthorityHook` first, then mutates only through
  `BotAuthorityCoreService` with `AuditActor::Human`; the lane's owner-target
  conflicts surface the fixed `owner_role_requires_transfer` code.
- `TeamManagerSyncServiceImpl` (plan Task 13, spec §6.1): the trusted
  team-manager sync facade. It verifies the platform service credential
  through the injected `TeamManagerCredentialVerifierPort` (the verified
  identity + scopes are the only accepted actor shape; the raw credential is
  never logged, audited, or persisted), re-validates the command's scopes,
  and delegates to the Core's one-transaction sync lane. The internal
  single-member repairs derive one full-snapshot reconcile under the same
  lane.

## Consumes

- `bcs-service-api` application contracts, Core service contracts, and shared
  transport-agnostic contract types.
- The non-repository `AgentIdentityPort`, implemented by deployment adapters.
- Pure utility crates for asynchronous traits and standard collections.

## Allowed dependencies

- `service-api/*`
- Utility crates such as `async-trait`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- Store or Legacy service implementations outside tests
- Repository ports in production code; persistence orchestration belongs behind
  a Core service contract

## Configuration

- The composition root injects the environment and Core service
  implementations.
- This crate must not select implementations or inspect environment variables.

## Runtime ownership

This crate owns the V1 Bot control-plane use-case facade, mounted behind the
trusted Human Principal verification boundary. The separate internal self facade
requires Agent identity verification itself and supports Agents before registration.

## Tests

- `cargo test --package bcs-app-bot --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-app-bot --all-targets --manifest-path src/bcs/Cargo.toml`
