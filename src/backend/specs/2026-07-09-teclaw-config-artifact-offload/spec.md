# Teclaw Config-Artifact Offload

## Summary
Teclaw (external, pull-based) bots deploy from a composed configuration artifact
that the platform freezes at build time and stores against the publish record.
That artifact can grow large — large enough to exceed the size limit of the
field it is stored in — which silently breaks the publish. This feature makes
the platform store an oversized artifact durably regardless of size, so every
teclaw publish, upgrade, rollback, and eager provision continues to work no
matter how big the artifact is.

## Motivation
The composed teclaw artifact carries an opaque, engine-owned payload plus a
growing set of staged file references. In practice this payload can be larger
than the storage field that currently holds it. When that happens the write
either fails or truncates, and the publish/deploy flow breaks — the bot cannot
be delivered, upgraded, or rolled back. Today there is no size headroom and no
fallback, so a single large artifact is enough to wedge a bot's release
pipeline. We need the artifact to persist reliably even when it is big, without
changing how publishing behaves for everyone else.

## User Stories
- As a bot owner publishing a teclaw bot, I want my publish to succeed even when
  my bot's composed configuration is large, so that a big artifact never wedges
  my release.
- As a bot owner, I want upgrade, rollback, and re-deploy of a large-artifact
  bot to keep working, so that the whole release lifecycle is unaffected by
  artifact size.
- As a platform operator, I want existing already-published bots (whose
  artifacts were stored the old way) to keep deploying unchanged, so that this
  change requires no data migration and carries no rollback risk.
- As a platform engineer, I want small artifacts to keep behaving exactly as
  they do today, so that the common case pays no new cost.

## Acceptance Criteria
- [ ] A teclaw publish whose composed artifact exceeds the storage field's safe
      limit completes successfully (build → verify → online), and the bot
      receives the complete, correct artifact.
- [ ] The delivered artifact is byte-for-byte the composed one — no truncation,
      no dropped file references, no altered opaque payload.
- [ ] Upgrade, rollback, offline/re-publish, and eager-at-creation provisioning
      all succeed for a large-artifact teclaw bot and deliver the correct
      artifact for the correct stage.
- [ ] Bots whose artifacts were stored inline before this change continue to
      publish, upgrade, and roll back with no migration and no behavior change.
- [ ] Small artifacts (below the safe limit) are stored and delivered exactly as
      they are today, incurring no additional storage round-trip.
- [ ] Per-stage artifact behavior (stage stamping and channel overrides) is
      preserved for large artifacts, identical to the small-artifact path.
- [ ] ARCA / non-teclaw bots are completely unaffected.

## In Scope
- Reliable persistence and retrieval of the teclaw config artifact at any size.
- Preserving all existing publish-lifecycle behavior (build, verify, online,
  canary/release, upgrade, rollback, eval, eager provision) for teclaw bots.
- Backward compatibility with artifacts already stored the old way.

## Out of Scope
- Reducing the artifact's size or changing what the artifact contains.
- Changing the ARCA / mount-based delivery path or any non-teclaw engine.
- A data migration of existing publish records.
- Any change to how the external container consumes the artifact.
- Lifecycle/garbage-collection guarantees for superseded stored artifacts beyond
  keeping storage bounded.

## Open Questions
- None outstanding. Two implementation decisions have already been settled with
  the requester and belong in the plan: (1) offload only when the artifact is
  large enough to risk the field limit, keeping small artifacts inline, with a
  threshold set comfortably below the field's capacity to leave buffer; and
  (2) store each artifact under a deterministic location that is overwritten in
  place, so no explicit cleanup wiring is required.
