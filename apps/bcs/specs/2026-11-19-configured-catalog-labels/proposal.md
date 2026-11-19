## Why

Agent and Group discovery Cards need a governed, flat label map without mutating the remote AgentCard document or weakening existing visibility policy. A configurable dictionary and per-key multiplicity must make the same validation authoritative across public management and downstream transports.

## What Changes

- Add `catalog_labels` as platform management metadata, stored once in `bcs_bots` and once in `bcs_group_agent_cards`; old SQL NULL values read as `{}`.
- Add a startup-loaded, environment-independent `catalog-labels.toml` dictionary with required `allow_multiple` on every key. Initial `lifecycle` values: `draft`, `trial`, `running`, `offline`; `allow_multiple = false`.
- Add Agent registration/update/readback and Group create/PATCH/detail support. Reject a null label object; remove keys with null/empty-list values; validate all supplied keys and values before any write.
- Distinguish Group PATCH omission (preserve) from Agent explicit-update omission (replace with empty). Refresh/background operations preserve labels.
- **BREAKING semantic caveat for label-bearing resources:** a legacy Agent update omitting the new field clears labels after they have been populated. Do not claim unconditional old-writer compatibility.
- Publish complete system plus custom labels to the catalog. Do not implement downstream incremental-label deletion or alter search APIs.
- Deliver public implementation first; downstream hosts integrate a published, validated public revision afterwards. Implementation evidence is tracked separately in validation.md; no release is implied.

## Capabilities

### New Capabilities

- `configured-catalog-label-policy`: typed label structure, configuration dictionary, per-key multiplicity, strict normalization and validation.
- `card-catalog-label-management`: transactional Agent and Group storage, operation-specific omission semantics, readback and refresh/concurrency behavior.
- `full-catalog-label-publication`: complete authoritative label snapshots and compatibility-aware rollout requirements.

### Modified Capabilities

None represented by an existing OpenSpec capability in this worktree. Existing REST, storage and catalog contracts still require corresponding updates during implementation; the entries above are delta capability groupings, not assertions that current behavior is already implemented.

## Impact

Public BCS service APIs, A2A and Group applications, HTTP adapters, Bot/Group stores including memory implementations, database migrations, configuration/bootstrap, Fuse publication and conformance tests. No enterprise transport code, no Java client implementation, no Group Triple API and no Fuse server changes belong in this change.

Status: local implementation tested; external acceptance pending, NOT_PUBLISHED, NOT_DEPLOYED, 2026-11-19. Baseline: public `origin/dev` at `8fc2e5f7693fc5caca97e5b5591148885d38ff10`. Implement and validate this change before the paired downstream-host change `add-configured-catalog-labels`.
