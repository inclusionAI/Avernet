# bcs-bot Context

## Provides

ProviderCore validates optional slugs and returns a credential-free
ProviderBasicInfo projection for public discovery, including disabled Providers.
ProviderManagement consumes this through its core contract. Additive slug-aware
registration/update methods preserve old core callers; writes keep existing
Provider admin and Human owner checks. Corrupt discovery config remains an error.

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

- Bootstrap injects `UplinkConfig` into the streaming connection use case.
  After authentication, explicit V3 requests may select allowed built-in MCP
  profiles. This is deployment-wide authorization, not per-Bot provisioning.
  Only the negotiated kind is process-local; reconnect/disconnect clears stale
  selection. Provider-bound coordination resolution remains Provider-owned.

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
Both flows derive ordinary AgentPass Bot `agent_code` from `provider_bot_ref`.
Subbot mode preserves the original parent in `agent_code` and composes the stored
ref with `bot_slug`, in either connection mode, using the loaded Provider config
and existing atomic Bot write. This does not add a repository read or a separate metadata update.
BotCore, ProviderCore and ProviderManagement synchronize gateway webhook/deletion
changes through the injected projection. Delivery switching validates affiliation
before owner-edge writes. Bot control-plane views hydrate upstream affiliation from
Bot metadata without treating it as HTTP delivery or adding gateway-only privileges.

Provider coordination policy also follows Bot-owned affiliation for plugin Bots.
After authentication, the runtime connect use case matches an explicit V3 MCP
client kind against that policy; only unaffiliated Bots use deployment-wide
uplink grants. Deleted ownership, missing/disabled Providers and malformed policy
fail closed. Reconnect reads current policy rather than persisting an entitlement.
Policy resolution is bounded: plugin ownership uses one indexed metadata read and
one Provider lookup (the Provider store's existing cache applies); legacy unbound
or Gateway paths may additionally read the binding projection. There are no scans,
batch fan-outs, new caches or writes. Message flow retains its per-run surface cache.

## AgentPass subbot identity

New Providers may use `agentpass-subbots`. Both registration paths normalize
parent plus Bot slug, create independent Bot credentials and reject occupied
references. Ordinary updates retain the persisted parent. BotCore resolves
parent/token plus optional Actor against current environment-scoped metadata;
subbots require Actor for parent credentials, while Static Tokens identify their
Bot directly. Provider events and runtime connection use cases enforce the same
identity before mutation. See `docs/agentpass-subbots.md` for the full contract.
