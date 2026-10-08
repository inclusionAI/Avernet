# Bot Genome is the unit of evolution, built on the Bot Config Manifest

> 中文版：[0001-bot-genome-is-the-unit-of-evolution.zh-CN.md](0001-bot-genome-is-the-unit-of-evolution.zh-CN.md)

Status: proposed (draft decision record; promote to `docs/adr/` on acceptance).

## Decision

Self-improvement in Avernet evolves one artifact: the **Bot Genome**, an
immutable, content-addressed revision of a bot's evolvable definition
(persona files, skills, curated memory, resources, tools, engine config, plus
a non-evolvable `policy` section). It is the Bot Config Manifest with history:
a revision compiles to a pinned Manifest document (every source resolved to a
commit SHA or digest) and is applied through the existing Manifest / publish
pipeline. No second delivery path is introduced.

In one sentence, a revision is a complete, pinned Manifest, plus curated
memory, plus lineage and a locked policy, changed only through patches.
Revisions are **total**: every category is present, so a revision fully
determines the bot regardless of prior state. File content is referenced by
digest and stored in the existing manifest content store; Skill Center skills
are referenced by pinned Center version rather than copied.

Revisions carry parent pointers, a readable per-bot sequence number,
provenance (who or what created them, from which evidence), and status. Named refs (`active`, `previous`, `canary`,
`draft`, `candidate/*`, `inbox/*`) point at revisions and move by
compare-and-swap. The existing `/config-manifest` endpoints remain and
become views over the registry.

Strategies and bots change a genome only by submitting a typed, itemized
**Genome Patch** against a named base revision. Whole-document replacement by
automated actors is not supported.

Curated memory becomes part of the genome. Runtime/episodic memory stays
engine-owned. This requires an engine memory projection contract and
amends the current rule that `MEMORY.md` and `IDENTITY.md` are never touched
by apply: the engine, not Backend, decides how curated items are projected.

Design: [`../genome.md`](../genome.md).

## Consequences

- Versioned bots, attributable experience, and rollback to any promoted
  revision become available independently of any evolution strategy.
- Manifest storage changes from one mutable row to revisions + refs; apply
  reports record the revision applied.
- Engine owners must agree to the memory projection contract before memory
  is evolvable; until then curated lessons use a platform-managed persona file.
- Service-bot publish records reference a revision id; rollback eligibility
  generalises from one step to any promoted revision.

## Alternatives

- **A separate evolution artifact (e.g. ClawEvolve Pack) delivered by its own
  path.** Rejected: two sources of truth, and Packs are OpenClaw-specific
  workspace snapshots that cannot reach teclaw.
- **Git repository per bot as source of truth.** Deferred: offers history for
  free but adds multi-tenant infrastructure; a git export is provided instead.
- **Keep Manifest v1 unchanged and version outside it.** Rejected: apply would
  still re-read a mutable document, so behaviour could not be attributed to a
  version.
