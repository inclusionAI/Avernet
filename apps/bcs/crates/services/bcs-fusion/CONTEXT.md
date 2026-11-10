# bcs-fusion Context

## Provides

- Fuse v2 Card search adaptation and event-triggered Agent/Group publication,
  with a coalesced identity queue and bounded retries. Agent Cards carry both
  committed visibility labels, including private values; public normal Groups
  carry the bot visibility label. Bootstrap supplies the control-plane repo and
  environment for current-state reads. No startup or master-acquisition scan.

- Local `FusionCoreService` implementation for loading bot context files and producing a fallback fusion response.
- BCSFuse-backed `FusionCoreService` implementation using the transport-only external client.
- BCSFuse-backed `WorkerProfileCoreService` implementation and worker sync helpers.
- Recall-scope translation for worker recommendation: the Core passes a typed
  `WorkerRecommendationScope`; this crate maps `LegacyDefault` to no outbound
  `filters` (provider default preserved) and `AllVisibilities` to the controlled
  `filters.availability = [public, protected, private]`. The fusion layer never
  decides viewer authorization; it only translates the scope the Core chose.
- Bot context file loading for `IDENTITY.md`, `SOUL.md`, `RULES.md`, and `MEMORY.md`.
- Optional LLM-client abstraction used by the local fusion engine.

## Consumes

- Bot context base directory supplied by bootstrap or by a higher-level adapter.
- `bcs-service-api` fusion and worker-profile contracts.
- `bcs-fuse-client` HTTP wrapper for remote BCSFuse calls.
- Filesystem reads for bot-owned context documents.

## Allowed dependencies

- `service-api/*`
- Serialization, async trait, logging, and error helper crates
- Filesystem access scoped to the configured bot context directory

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- Config file or env discovery
- Group-management policy that belongs in `services/bcs-group`

## Configuration

- Bootstrap supplies the bot context base directory and any future LLM collaborator explicitly.
- This crate must not select between bcsfuse, local fusion, or other providers; provider selection belongs to the composition root.

## Runtime ownership

The crate owns local context loading and local fallback fusion behavior. It does not own group membership, transport sessions, or provider selection.

## Parking status

Promoted from `mix/` in Phase 3. `LocalFusionService` is the offline fallback; `FuseBackedFusionService` owns BCSFuse-backed business behavior.

## Tests

- `cargo test --package bcs-fusion --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-fusion --all-targets --manifest-path src/bcs/Cargo.toml`
