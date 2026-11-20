# Configured catalog labels — phase 1: Avernet

Status: **LOCAL_TESTED; external acceptance gates pending; NOT_PUBLISHED; NOT_DEPLOYED**. Recorded 2026-11-19.

This directory is the review/delivery copy intended for version control (currently uncommitted) of OpenSpec change `add-configured-catalog-labels`. The repository ignores `openspec/`; its root change directory is a local CLI working copy. Do not lose this app-owned copy during future commit preparation. Capability artifacts map from local `specs/<capability>/spec.md` to `requirements/<capability>/spec.md` here, avoiding nested `specs` ignore rules. Maintain the same artifact content in both copies before validation; do not treat the local mirror as an independent contract.

## Reading order

1. [Proposal](proposal.md): scope, ownership and compatibility caveats.
2. [Design and rollout](design.md): field/config/storage/transport decisions, migration sequence, old/new matrix and rollback.
3. Capability requirements:
- [card-catalog-label-management](requirements/card-catalog-label-management/spec.md)
- [configured-catalog-label-policy](requirements/configured-catalog-label-policy/spec.md)
- [full-catalog-label-publication](requirements/full-catalog-label-publication/spec.md)
4. [Ordered tasks](tasks.md): implementation progress and separately gated release/deployment tasks.
5. [Validation record](validation.md): actual local verification and explicitly separated runtime/deployment gaps.

## Accepted decisions

- Name: `catalog_labels`, a flat string-to-string-list management metadata object, separate from raw Card documents.
- Agent and Group support; persistence owned by `bcs_bots` and `bcs_group_agent_cards`.
- Reject outer `null`; known per-key null/empty arrays remove the key; a supplied object replaces the whole collection.
- Group PATCH and Agent otherwise-valid explicit-update omission preserve; registration/create omission initializes empty.
- URL/manual/background refresh and unrelated internal maintenance preserve persisted labels.
- Config declares allowed values AND whether each key allows multiple distinct values. Initial `lifecycle`: `draft`, `trial`, `running`, `offline`; `allow_multiple = false`.
- No business dictionary literals in production code; system visibility labels remain system-owned.
- Every catalog publication emits complete current custom plus system labels; downstream incremental processing is outside this work.
- No Group Triple/Java API. Existing Agent Triple/Java structural typing is part of phase 2 and later client handoff.

## Implementation and release order

1. **Avernet:** public policy/config, owning schema migrations, Agent/Group HTTP and transactional storage, complete catalog payload and tests.
2. **OCB:** verified available public gitlink, typed Agent Triple and production wrapper/assembly, independent product policy packaging, integration tests.
3. **Runtime/client release:** expand schema → provision config → upgrade all servers → verify non-empty persisted readback → enable later client writers. Public source publication is not embedded-host deployment.

Old requests handled by the new server preserve existing labels when the field is omitted. New Triple clients can have fields silently ignored by old servers. Neither compatibility risk is solved by additive Proto tags alone; consult the explicit release gates before enabling writers. Rollback retains columns/data/history and disables new writers first.

## Baseline and ownership

- Inspected Avernet source: `8fc2e5f7693fc5caca97e5b5591148885d38ff10` on fetched `origin/dev`; this is a proposal baseline, not a future feature commit.
- Owning application: `bcs`. This directory does not promote unimplemented requirements into current production contracts.
- Companion change: same change ID in the other repository. Public requirements stay in Avernet; OCB documents only its integration/transport differences.
- Current-spec navigation: [application spec index](../README.md).
