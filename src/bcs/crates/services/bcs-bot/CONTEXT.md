# bcs-bot Context

## Provides

- `BotCore::find_agent_registration` exposes the repository's authoritative,
  fallible Agent registration projection without runtime hydration or writes.
  The V1 self facade calls it only after trusted Agent identity verification.

- Authenticated provider coordination callbacks share reference claims with stream intake.
- Bot service implementations for BCS, including the independent Bot
  control-plane Core.
- Bot onboarding, discovery, status, connectivity, and binding metadata behavior.
- Application-facing orchestration around registry reads and writes.

## Consumes

- `bcs-service-api` contract traits and DTOs.
- `plugin-api/*` contracts when persistence or cache support is needed.
- Pure utility crates for IDs, logging, and serialization.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

- `service-api/*`
- `plugin-api/*`
- Utility crates such as `uuid`, `serde`, and `tracing`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- `external-clients/*` crates not listed in `Allowed dependencies` above

## Configuration

- Bootstrap injects stores, collaborators, and policy knobs explicitly.
- This crate must not choose concrete plugins or inspect env directly.

## Runtime ownership

The crate owns registry business rules, status/connectivity semantics, and Bot
control-plane persistence orchestration such as Provider hydration. It does not
own socket runtime state or transport handling.

Provider event ingestion authenticates the Provider/Bot binding before asking
the message-flow application contract to reconcile a durable managed run. Late
terminal events can survive an expired run cache; this service does not own the
delivery state machine or resume nonterminal streams from durable metadata.

## Tests

- `cargo test --package bcs-bot --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-bot --all-targets --manifest-path src/bcs/Cargo.toml`

BotCore resolves binding webhook overrides before the optional Provider default. Gateway registration validates an effective endpoint before writes; address-only PATCH validates Provider ownership and preserves capabilities. Explicit receiver failures do not select a fallback endpoint.

ProviderRegistrationCore consumes typed owner/self-service policy and BotProviderRepoPort.
It reauthorizes token-scoped registration, validates mode/endpoint before creation,
and reads the injected ProviderCredentialRepoPort
for gateway readiness: the downlink credential must exist, be enabled and have a
nonblank secret. Credential read failures propagate; issuance and upstream never
read or require that credential. Bot creation and gateway projection are atomic in SQL;
Human/owner-edge writes follow and failures propagate, without journal-based resume.
Duplicate Provider/ref returns Conflict; distinct refs may share a valid register token.
Upstream memberships never create delivery bindings or MOCK credentials through this
flow. Legacy Provider-admin plugin registration retains its MOCK/preserved-token rules.
Both flows derive AgentPass Bot `agent_code` from `provider_bot_ref` in either
connection mode, using the already loaded Provider config and the existing atomic
Bot write. This does not add a repository read or a separate metadata update.
BotCore, ProviderCore and ProviderManagement synchronize gateway webhook/deletion
changes through the injected projection. Delivery switching validates affiliation
before owner-edge writes. Bot control-plane views hydrate upstream affiliation from
Bot metadata without treating it as HTTP delivery or adding gateway-only privileges.
