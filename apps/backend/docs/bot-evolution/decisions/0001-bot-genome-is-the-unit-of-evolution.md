# Bot Genome is the unit of evolution, built on the Bot Config Manifest

> 中文版：[0001-bot-genome-is-the-unit-of-evolution.zh-CN.md](0001-bot-genome-is-the-unit-of-evolution.zh-CN.md)

Status: proposed (draft decision record; promote to `docs/adr/` on acceptance).

## Decision

Self-improvement in Avernet evolves one artifact: the **Bot Genome**, an
immutable, content-addressed revision of a bot's evolvable definition
(persona files, skills, curated memory, resources, tools, engine config, plus
a non-evolvable `policy` section). The genome sits on top of the Bot Config
Manifest: a revision compiles to a pinned Manifest document (every source
resolved to a commit SHA or digest) and is applied through the existing
Manifest / publish pipeline. No second delivery path is introduced.

In one sentence, a revision is a complete, pinned Manifest, plus curated
memory, plus lineage and a locked policy, changed only through patches.
Revisions are **total**: every category is present, so a revision fully
determines the bot regardless of prior state. File content is referenced by
digest and stored in the existing manifest content store; Skill Center skills
are referenced by pinned Center version rather than copied.

Revisions are stored as RFC 8785 canonical JSON and identified by the
SHA-256 of `{spec, policy}`. The Bot Config Manifest stays YAML as today;
everything new introduced here is JSON only.

Revisions carry parent pointers, a readable per-bot sequence number,
provenance (who or what created them, from which evidence), and status. Named refs (`active`, `previous`, `canary`,
`draft`, `candidate/*`, `inbox/*`) point at revisions and move by
compare-and-swap.

The Manifest stays revision-unaware. It gains only format features (a
content-store source, `engine_config` applied for an allowlist of keys, a
`memory` category, a write path for produced content) and one fact of its
own: each apply report, and each service-bot publish record, records the
digest of the document it applied. The Genome Registry maps those digests to
revisions. The existing `/config-manifest` endpoints are unchanged; a
direct Manifest edit bypasses the genome, records no revision, and is
detected as **drift** (the last applied digest differs from what `active`
compiles to). Evolution pauses for a drifted bot until the owner
reconciles.

Strategies and bots change a genome only by submitting a typed, itemized
**Genome Patch** against a named base revision. Whole-document replacement by
automated actors is not supported.

Curated memory becomes part of the genome. Runtime/episodic memory stays
engine-owned. This requires an engine memory projection contract and
amends the current rule that `MEMORY.md` and `IDENTITY.md` are never touched
by apply: the engine, not Backend, decides how curated items are projected.

Design: [`../01-genome.md`](../01-genome.md).

## Consequences

- Versioned bots, attributable experience, and going back to any earlier
  revision become available independently of any evolution strategy. Going
  back is promoting an earlier revision again, applied like any promotion.
- Manifest storage stays one row per bot; revisions and refs live in the
  Genome Registry. Apply reports record the digest of the applied document,
  and the Registry maps it to a revision.
- A direct Manifest edit is detected as drift: runs do not start, and
  every promotion except the owner's own is refused, until the owner
  records the bot's current Manifest as a revision and promotes it, or
  promotes `active` again.
- Engine owners must agree to the memory projection contract before memory
  is evolvable; until then curated lessons use a platform-managed persona file.
- For service bots, promoting a revision publishes it as the next version
  through the existing publish flow, and the publish record records the
  digest of the published document. The existing service-bot rollback
  feature is unchanged.

## Alternatives

- **A separate evolution artifact (e.g. ClawEvolve Pack) delivered by its own
  path.** Rejected: two sources of truth, and Packs are OpenClaw-specific
  workspace snapshots that cannot reach teclaw.
- **Git repository per bot as source of truth.** Deferred: offers history for
  free but adds multi-tenant infrastructure; a git export is provided instead.
- **Version outside the Manifest and leave the Manifest entirely
  unchanged.** Rejected: without a content-store source and the applied
  document digest, apply would still re-fetch moving sources and could not
  say which document it applied, so behaviour could not be attributed to a
  version. The genome does sit on top of the Manifest, but the Manifest
  gains those format features.
