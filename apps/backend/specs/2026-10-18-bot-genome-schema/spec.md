# Bot Genome schema and patch format (RSI-02)

> Deliverables: the contract in
> [`apps/backend/api-contracts/genome/v1/`](../../api-contracts/genome/v1/README.md)
> (schemas, risk tiers, vectors, examples, Manifest v1 compatibility) and the
> contract test `tests/community/contracts/test_bot_genome_schema.py`.

## Summary

Fix the normative, machine-checkable format of the Bot Genome: what a Genome
Revision contains, how its identity is computed, what a Genome Patch may
change and at which risk tier, and how a Bot Config Manifest v1 document maps
to a revision and back. Every later piece of bot evolution (the Genome
Registry, promotion, strategies, verification) builds on this format, so it
has to be complete, unambiguous, and agreed before any of them is built.

## Motivation

Work item RSI-02 in `apps/backend/docs/bot-evolution/work-items.md` is the
next step on the critical path RSI-01 → RSI-02 → RSI-03 → RSI-04. RSI-01
accepted the decisions this builds on: ADR 0019 (the Bot Genome, built on the
Manifest, is the unit of evolution), ADR 0020 (promotion is platform-owned)
and ADR 0021 (control plane in `apps/evolution`).

The draft data model in `01-genome.md` §2 cannot yet hold every Manifest v1
document without changing the bot it describes (MCP settings had no defined
place), leaves the op set and gene path grammar open (G-2, G-3), names an
open directory-storage question (G-1), and has no rule for computing a
revision's identity that two implementations would agree on. RSI-03 cannot
start until these are fixed.

## User Stories

- As a **Backend engineer building the Genome Registry (RSI-03)**, I want one
  normative schema for revisions, patches, and gene paths, so that I implement
  validation and hashing once and correctly.
- As a **strategy author**, I want a patch format that can express any edit I
  make to a sandbox copy of a bot, so that my strategy's work can always be
  submitted.
- As a **bot owner**, I want the policy defaults and the risk tier of every
  change to be defined in advance, so that I know what automated actors may
  change on my bot and which changes wait for my approval.
- As a **reviewer of the bot-evolution design**, I want every Manifest v1
  field accounted for, so that recording a bot's Manifest as a revision never
  silently changes the bot.
- As an **engineer in `apps/evolution`**, I want the schemas as versioned
  contract files, so that I can validate patches without importing Backend
  code.

## Acceptance Criteria

Revision format:
- [ ] A Genome Revision has a published schema covering `spec`, `policy`, and
      the platform-written `revision` metadata; every example revision in the
      deliverable validates against it.
- [ ] Every entry in `spec` is a stable **key** (the field that names it:
      persona file type, skill name, resource path, MCP server code) plus a
      **value**: a digest when the entry has stored bytes, inline fields when
      it has none (MCP config, a Center skill's pinned version).
- [ ] File content is always referenced by digest, including content a
      Manifest declared inline.
- [ ] A directory (a skill, or a directory resource) is referenced by the
      digest of its `acm-tree-v1` encoding; the encoding's rules, including
      what it does not preserve, are stated (closes G-1).
- [ ] The revision id is defined as SHA-256 over the RFC 8785 canonical form
      of `{spec, policy}`, with no floats anywhere in hashed content, and the
      rule is backed by published test vectors that a second implementation
      can check itself against.

Policy:
- [ ] The policy has exactly `mutable_genes`, `pins`, and `risk_overrides`;
      anything not listed in `mutable_genes` is off limits, and `policy`
      itself is never reachable by a patch.
- [ ] Default policy: mutable `persona`, `skills`, `memory`, `resources`;
      pinned `persona/SAFETY.md`; `script` and `tools/mcp` not mutable
      (closes G-3).
- [ ] `risk_overrides` may only raise a tier (closes G-7).

Gene paths:
- [ ] One slash-separated gene path grammar is defined and used by policy
      entries, binding `allowed_genes`, patch targets, and diff output;
      prefix matching works on whole segments only, so a rule for skill `pdf`
      never covers skill `pdf.tools`.

Patch format:
- [ ] A Genome Patch has a published schema; the op set is `file.edit` (kinds
      `replace_section`, `insert_after`, `diff`), `skill.add`, `skill.update`,
      `skill.remove`, `persona.add`, `persona.remove`, `resource.add`,
      `resource.remove`, `memory.add`, `memory.update`, `memory.retire`,
      `memory.set_mode` (closes G-2, G-10).
- [ ] Any change to a text file can be expressed as a patch (the `diff` kind
      guarantees it), so turning a sandbox's edits into a patch never fails
      for lack of an op.
- [ ] MCP and script changes cannot be expressed as a patch; they change only
      when an owner records a whole Manifest document.
- [ ] Every op has a defined default risk tier, carried as data next to the
      schema together with the rewrite threshold (40% of a file's lines); the
      tier tables in `01-genome.md` and `08-promotion.md` are identical to
      that data, and a test fails if they drift.

Manifest mapping:
- [ ] A compatibility table lists every Manifest v1 field with one outcome:
      maps to a revision field, resolved at record time, kept only in the
      provenance copy of the original document, refused, or ignored.
- [ ] "Round-trip" is defined as: recording a Manifest document M as a
      revision and compiling that revision to M′ gives a document that
      applies to the same bot as M did when it was recorded.
- [ ] The complete example document and every example fragment in
      `docs/bot-config-manifest/examples.zh-CN.md` are worked through
      M → revision → M′ and satisfy that definition on paper, with the stated
      exceptions (cli_tools ignored, engine_config refused).
- [ ] Recording rules are stated for placeholders (resolved once, with the
      recording bot's values), the first revision's capture from the bot's
      current state, and the platform-written AGENTS.md reference block
      (removed on capture, with the reason documented).

Documentation and tracking:
- [ ] `01-genome.md`, `03-strategy.md`, `06-evolution-run.md`,
      `08-promotion.md` and their `zh-CN` versions match the new format.
- [ ] `work-items.md` (and `zh-CN`) marks RSI-02 done and adds to RSI-04: a
      Center-skill source in the Manifest, and a dependency on resource
      removal (#2162).
- [ ] Known limitations are documented with their tracking issues: effective
      MCP config is not fully pinned (#2859); MCP header values are stored as
      written (#2860); `engine_config` is refused until #2858.

## In Scope

- Schemas for Genome Revision, Genome Patch, and gene paths.
- The risk-tier mapping and rewrite threshold as data.
- Canonicalisation and identity rules with test vectors.
- The Manifest v1 ↔ revision compatibility table and worked examples.
- A contract test that validates every example against the schemas, checks
  the identity test vectors, and checks the tier tables against the data.
- Design-doc updates listed above.

## Out of Scope

- Any runtime code: the Genome Registry, patch application, the Manifest →
  revision mapper, and compile are RSI-03.
- Manifest changes (content-store source, Center-skill source, applied
  document digest, resource removal) are RSI-04 and #2162.
- CLI tools: not part of genome v1; they return with acli.
- `engine_config`: refused until #2858 defines keys and types.
- Curated memory projection onto engines (RSI-05); the `memory` category's
  shape is fixed here, its delivery is not.
- Pinning the effective MCP configuration (#2859) and credential references
  for MCP headers (#2860).
- Per-file storage of directories (the cost of re-storing a whole tree on a
  one-file edit is accepted for v1 and recorded under G-1).

## Open Questions

- None blocking. Remaining open decisions in `01-genome.md` §15 that this work
  item does not close (D-6 storage, G-4 engine_config keys, G-5 memory
  projection, G-6 lineage of identical content, G-8 retention sweep, G-9
  candidate id vs revision id) stay open and keep their current owners.
