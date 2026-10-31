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
Subbot mode parses the last colon in the client-composed `provider_bot_ref`,
validates both parts and preserves the parent in `agent_code` and the full ref
unchanged, in either connection mode, using the loaded Provider config
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

## Provider collaboration scope

Provider collaboration scope is resolved from live Provider metadata by BotRegistryCoreService.collaboration_owner. Same-owner authorization uses persisted created_by; missing affiliated Providers and malformed policies fail closed. Provider admin commands expose optional scope, and cannot relax same_owner. See specs/2026-10-13-provider-collaboration-scope/spec.md.

Legacy Provider registration preflights affiliation before merging Bot capabilities or credentials, for both plugin and gateway modes. This catches existing public memberships without modifying the Bot; storage independently revalidates under its policy/row locks.

## A2A Card core

`A2aBotCore` implements `A2aBotCoreService` using active Gateway affiliation and
the `A2aBotRepoPort` CAS boundary. It does not perform HTTP access, parse SDK
types or authenticate Provider management requests. Those use cases live in
`crates/application/bcs-app-a2a-bot`. Bootstrap injects the collaborators.

The Bot query facade receives the same Core as a read collaborator. Detail,
discovery and search responses expose an optional full `agent_card`, preserving
skills/descriptions and unknown fields without rewriting legacy capabilities.
Existing BCS visibility, ownership, organization and friendship gates run first.
Core batches Card reads; discovery predicates identify nonmatching A2A Bots
without loading their full payload or falling back to legacy capabilities.
Cards are read from persistence, so refresh needs no duplicated query cache.
Query/discovery orchestration is in application/bot_query.rs and bot_cards.rs.
Refresh failure preserves the last validated Card for new sends and cancellation.
New delivery interfaces derive from current egress/gRPC policy, without a saved
route projection; availability uses the same pure selector.
Affiliation, disabled binding/Provider/downlink and credential checks remain
unchanged. Cancellation skips new extension negotiation and the outbound adapter
uses the already-frozen Run endpoint/Task. No new database read or cache is added.
Delivery targets project typed extension declarations with their required flag
and full params. Core does not select an experimental draft/business profile;
that interpretation belongs to the outbound protocol adapter. Invalid stored
declarations fail rather than silently dropping negotiation data.


ProviderRegistrationCore supports explicit validated A2A registration. It requires
current Provider management authority (not self-service alone), Gateway mode,
enabled downlink/credential and matching Card identities. No webhook default is
required; the repository commits Card and existing ownership lifecycle together.


`A2aProviderRegistrationCore` implements the Provider-admin A2A admission Core.
It consumes ProviderCoreService and the existing BotProviderRepoPort atomic
ownership/Card create; no network or SDK code is added to Core. It repeats
management/downlink authorization after application Card fetch, normalizes the
Provider identity, preserves a delegated owner and audits the actual Provider.
Duplicates, tombstones and concurrent collisions fail without credential replay.
The transaction is the existing composite; no migration or second partial Bot
write is introduced. Admission adds bounded per-request reads, no registry scan.
