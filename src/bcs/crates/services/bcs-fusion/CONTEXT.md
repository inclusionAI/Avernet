# bcs-fusion Context

## Provides

- Local `FusionCoreService` implementation for loading bot context files and producing a fallback fusion response.
- BCSFuse-backed `FusionCoreService` implementation using the transport-only external client.
- BCSFuse-backed `WorkerProfileService` implementation and worker sync helpers.
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

The crate owns local context loading and local fallback fusion behavior. It does not own group membership, proposal confirmation, transport sessions, or provider selection.

## Parking status

Promoted from `mix/` in Phase 3. `LocalFusionService` is the offline fallback; `FuseBackedFusionService` owns BCSFuse-backed business behavior.

## Tests

- `cargo test --package bcs-fusion --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-fusion --all-targets --manifest-path src/bcs/Cargo.toml`
