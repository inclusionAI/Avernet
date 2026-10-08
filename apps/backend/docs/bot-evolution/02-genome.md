# Bot Genome — the form of an evolved bot

> 中文版：[02-genome.zh-CN.md](02-genome.zh-CN.md)

> Status: DRAFT. Builds on the Bot Config Manifest
> (`apps/backend/docs/bot-config-manifest/`). Proposed decision:
> [DR-1](decisions/0001-bot-genome-is-the-unit-of-evolution.md).

## 1. Why not just use the Manifest as-is?

The Manifest was built for teclaw onboarding and is the right **foundation**:
engine-neutral, declarative, converges on every engine including teclaw,
applies only through existing core services, and already has a capability
matrix and append-only apply reports. Reusing it means evolution gets
delivery to every engine for free.

But as the artifact of an evolving bot it is missing the things evolution
needs (codebase evidence in [09-research.md §2.1](09-research.md#21-bot-config-manifest)):

| Gap in Manifest v1 | Why evolution needs it |
| --- | --- |
| One mutable row per bot, no revision history, no content hash | Lineage, archive, reproducibility, rollback to any point |
| No parent pointer | Lineage tree; descendant-aware selection |
| No If-Match / ETag | A strategy and a human editing concurrently silently overwrite each other |
| Apply re-reads the current document; report does not say which bytes were applied | Cannot attribute behaviour or experience to a version |
| Sources are moving refs (git branch, oss key) re-resolved each apply | A genome must be immutable: every source pinned to commit SHA / digest |
| Whole-document PUT, category-atomic replace | Proposers must emit small, reviewable, itemized patches |
| `MEMORY.md` / `IDENTITY.md` reserved and rejected | Learned memory is half of what self-improvement produces |
| `engine_config` rejected in v1 | Model / temperature / reasoning budget are legitimate tunables |
| A category left out of the document means "leave it untouched" | A revision must fully determine the bot; otherwise two applies of the same revision can produce different bots depending on prior state |
| Unknown keys rejected; no metadata | Need provenance and annotations |
| Rollback limited to one step and service bots only | Need rollback to any accepted revision for every bot |

## 2. Shape

In one sentence: a Genome Revision is **a complete, pinned Manifest, plus
curated memory, plus lineage and a locked policy, changed only through
patches**. Concretely, it adds exactly these things on top of the Manifest:

1. **Revision identity and lineage**: a content-hash id plus a readable
   per-bot sequence number, parent(s), provenance (who or what created it,
   from which evidence), status, and named refs moved by compare-and-swap
   (§3).
2. **A non-evolvable `policy` section**: locked genes, mutable genes, pinned
   items, risk overrides (below).
3. **Pinned content**: every source resolved to a commit SHA or content
   digest, so the same revision always yields the same bytes (§7).
4. **Totality**: every category is present in every revision; `[]` means
   "none". Unlike a Manifest document, a revision never leaves a category to
   prior state.
5. **Patch-only change for automated actors**: strategies and bots submit
   typed, itemized patches against a named base revision (§4). Humans can
   still record a revision from a whole document.
6. **One new content category, curated memory** (§5), which needs a new
   engine contract.

Deliberately *not* added: evaluation scores (they live in the verification
layer and are only linked), and `engine_config` (the Manifest already has the
category; enabling an allowlisted subset is a Manifest change evolution
benefits from, not a genome-only addition).

```yaml
# Illustrative — the normative schema is work item RSI-02.
genome_schema: 1
revision:                        # computed / platform-written, not authored
  id: sha256:7c1e…               # hash of canonical(spec) — content address
  seq: 42                        # per-bot sequence number for humans ("r42"); not an identity
  bot_id: bot_123
  lineage_id: lin_support_agent  # stable across forks; a fork starts a new lineage
  parents: [sha256:a90b…]        # >1 parent allowed (crossover/merge)
  created_by: {kind: strategy_run, run_id: run_88, step: propose, actor: clawevolve/bot-evolution@2}
  created_at: 2026-10-08T03:12:00Z
  status: candidate              # draft | candidate | accepted | rejected | promoted | archived
  patch_from_parent: blob:sha256:…  # the Genome Patch that produced it
  evidence: [episode:ep_91, episode:ep_97, finding:f_12]
  evaluations: [eval:ev_301, eval:ev_302]   # links into C5, not embedded scores

spec:                            # the evolvable content (authored / proposed)
  persona:                       # == manifest.identity, but pinned
    - {type: SOUL.md, blob: sha256:…}
    - {type: AGENTS.md, blob: sha256:…}
  skills:
    - {name: refund-policy, origin: {kind: center, version: "center://…@v7"}}   # pinned Center version, not copied
    - {name: quality-check, blob: sha256:…, origin: {kind: local}}              # bot-owned: stored as a blob
  memory:                        # NEW — see §5
    mode: seed                   # seed | replace | merge
    items_blob: sha256:…         # itemized memory set (not a raw MEMORY.md)
  resources:
    - {path: data/kb/, blob: sha256:…}
  tools:
    mcp: [{server_code: mcp.x.meet, config_blob: sha256:…}]
    cli_tools: [{name: shopctl, blob: sha256:…, version: 2.3.0}]
  engine_config:                 # NEW in evolution scope — allowlisted keys only
    model: provider/model-x
    reasoning_effort: medium
  script: {blob: sha256:…}       # carried, but LOCKED for evolution by default

policy:                          # NOT evolvable — copied forward verbatim by the platform
  locked_genes: [script, tools.mcp, policy]
  mutable_genes: [persona, skills, memory, resources, engine_config.reasoning_effort]
  pins: [skills.refund-policy]   # write-protected items (Hermes-style pinning)
  risk_overrides: {}
```

Design points:

- **Content-addressed blobs.** File content is referenced by digest and
  stored in the existing manifest content store (§7). Two revisions that
  share a file share its blob. Diffs are cheap.
- **Pinned, not floating.** When a revision is recorded from a Manifest that
  uses `sources` with moving refs, the platform resolves them and stores the
  blobs. A floating Manifest is an *input*; a revision is a *fact*.
- **Total, not partial.** Recording a revision from a partial Manifest
  document fills every omitted category from the parent revision (or from the
  bot's current state for the first revision), so the stored revision is
  complete. Compiling a revision for apply always emits every category.
- **Center skills are pinned, not copied.** Skill Center already stores
  immutable, governed versions (ADR 0010). A revision records the Center
  version; only bot-owned skills and git/OSS-sourced content are stored as
  blobs.
- **`spec` vs `policy`.** `policy` is owned by the bot owner / platform, never
  by a strategy. The platform floor rejects any patch touching it. This is
  where "tool and permission changes are human-only" is enforced
  structurally rather than by convention.
- **`script` locked by default.** Imperative startup scripts are the most
  dangerous evolvable surface and are already rejected on teclaw. They remain
  part of the genome so a revision is complete, but are locked unless the
  owner unlocks them.
- **Metadata references, never embeds, evaluation.** Scores live in C5 and
  are joined in the Archive read model; the genome stays a pure definition.

## 3. Refs

Named, movable pointers to immutable revisions, git-style:

| Ref | Meaning | Who moves it |
| --- | --- | --- |
| `active` | What the bot runs | Promotion only |
| `previous` | Last `active` (convenience for one-click rollback) | Promotion only |
| `canary` | Revision on canary instances | Promotion only |
| `draft` | Owner's in-progress manual edit | Owner (UI/API) |
| `candidate/<run>/<n>` | Candidates in a run | Orchestrator |
| `inbox/<id>` | Bot-submitted draft patches (fast loop) | Bot principal |

Ref updates are compare-and-swap (`expected_revision`), closing the
concurrent-edit gap.

**Relationship to today's Manifest row.** In P1 the existing
`PUT /config-manifest` keeps working and becomes sugar for "record a revision
from this document and move `draft` (and, for backward compatibility,
`active`) to it, then apply". Existing clients see no change; the row becomes
a projection of `active`.

**Relationship to service-bot publish.** A publish version already freezes a
`BotConfigArtifact`. Promotion for a service bot = record the revision, run
draft → verify → publish with the artifact compiled from that revision, and
store `revision_id` on the publish record. Rollback eligibility rules then
generalise: any `promoted` revision can be re-promoted.

## 4. Genome Patch

The only output a Proposer may produce. Itemized and typed, following ACE's
finding that delta updates avoid context collapse.

```yaml
patch_schema: 1
base: sha256:a90b…                       # must equal the parent; CAS on record
ops:
  - op: file.edit                        # text files: persona md, SKILL.md, resources
    target: persona/SOUL.md
    edits:
      - {kind: replace_section, heading: "## Escalation", content: "…"}
      - {kind: insert_after, anchor: "## Tone", content: "…"}
  - op: skill.add
    name: invoice-lookup
    files: {SKILL.md: "…", scripts/lookup.py: blob:sha256:…}
  - op: skill.update
    name: refund-policy
    file_ops: [{kind: unified_diff, path: SKILL.md, diff: "@@ …"}]
  - op: memory.add                       # itemized memory, see §5
    item: {key: "customer-tier-rules", text: "…", tags: [billing], source: [episode:ep_91]}
  - op: memory.retire
    key: "old-shipping-sla"
  - op: engine_config.set
    key: reasoning_effort
    value: high
rationale: "…"                           # required; shown to reviewers
evidence: [finding:f_12]                 # required for non-trivial ops
```

Rules enforced by the platform when recording a candidate:

- Base must match; ops apply cleanly or the patch is rejected (no fuzzy
  merge in v1).
- Ops on locked genes or pinned items are rejected.
- A `file.edit` that changes more than a configurable fraction of a file
  (default 40%) is flagged `rewrite` and raises the risk tier.
- Each op maps to a **risk tier** ([08-governance.md §3](08-governance.md#3-risk-tiers)).
- Patch size, file count, and blob size limits mirror Manifest limits.

Patches compose: a run with several accepted iterations can be squashed into
one patch for review, while the archive keeps each step.

## 5. Memory — the open part

Today `MEMORY.md` and `IDENTITY.md` are reserved: apply never writes or
deletes them, because the engine's runtime writes them. Self-improvement
needs some of memory to be evolvable, so this has to be split:

| Layer | Owner | In genome? | Evolution |
| --- | --- | --- | --- |
| **Episodic / runtime memory** — what the bot writes as it works | Engine runtime | No | Captured into Experience Store as evidence |
| **Curated memory (seed)** — facts, rules, lessons the bot should start with | Genome | **Yes**, itemized | Proposed by consolidation strategies ("dream"), gated, promoted |

Projection onto the engine is engine-owned (ADR 0014/0017: Backend must not
know engine paths). Proposed **Engine memory projection contract**
(work item RSI-05):

- `export_memory(bot) -> items[]` — engine reads its runtime memory into
  normalized items (for consolidation strategies to read).
- `project_memory(bot, items, mode)` — engine materialises curated items into
  its layout. Modes: `seed` (only if absent), `merge` (curated items
  upserted by key, runtime items untouched), `replace` (curated set becomes
  the memory; requires T3 approval).
- Engines declare support per mode in the capability matrix; teclaw support
  goes through the BotConfigArtifact contract like other categories.

This requires amending the "reserved files are never touched" rule — hence
it is called out in DR-1 as a consequence and must be confirmed with the
engine owners. **Until RSI-05 lands, memory evolution is limited to a
platform-managed file (e.g. `LESSONS.md`) declared as a persona file** — a
safe interim that needs no engine change.

## 6. Manifest v2 changes this implies

Kept backward compatible with schema v1 documents.

1. Revision table + refs + CAS (P1).
2. Apply reports record `revision_id` and compiled document digest (P1).
3. Pinned resolution recorded on revision (P1); revisions are total even
   when recorded from a partial document.
4. `metadata` / `annotations` top-level key, ignored by apply (P1).
5. `engine_config` category enabled for an allowlist of keys (P3/P4).
6. `memory` category with modes (P5, depends on RSI-05).
7. Any-revision rollback for personal and service bots (P1).
8. Manifest content store accepts produced (non-fetched) content with patch
   provenance (P2).

## 7. Storage

### 7.1 Content: reuse the manifest content store

The manifest apply pipeline already keeps the platform's own durable copy of
everything it fetches (`core/bot_config_manifest/content/service.py`):

- **Bytes** live in a content-addressed blob directory,
  `<root>/blobs/<hex[:2]>/<hex64>`, written once and atomically and
  hash-verified on read. The root is
  `user_config.bot_config_manifest.content_store_dir` (default
  `./data/manifest_content`; deployments point it at a shared volume).
- **Provenance** lives in `ac_manifest_content`, an append-only log of store
  events (bot, source URL, credential *name*, apply). The table holds no
  bytes.
- **Retention** in v1 is unconditional: no delete, no sweep, no TTL.

Genome revisions reference blobs in this same store; there is no second
copy. Three extensions are needed:

1. **A write path for produced content.** Today every store event is a
   *fetch* (`source_url` is required). Content created by a proposer, such
   as an edited `SKILL.md` or a new memory item, was never fetched. It needs
   a store call whose provenance is the producing patch and run.
2. **Retention for the archive.** The archive keeps every candidate. Most
   blobs are small text and deduplicate, but manifest resources may be
   100–200 MiB each. Unconditional retention is acceptable for v1. Any later
   sweep may only remove blobs that no revision references, and must never
   remove a blob reachable from a `promoted` revision.
3. **Readers outside Backend.** Proposers and evaluators in
   `apps/evolution` read content through the Genome Registry API (or a
   materialised sandbox), never from the blob directory directly.

Open question: how a directory resource (`path: data/kb/` from git) is
stored today, as one archive blob or one blob per file. It decides how
fine-grained `file.edit` patches on resources can be, and must be confirmed
before RSI-02 fixes the patch schema.

### 7.2 Revisions: database, not git (open decision D-6)

| Option | For | Against |
| --- | --- | --- |
| **DB revisions + existing content-addressed store** (recommended) | Tenancy, ACL, queries over lineage and status, reuses the manifest content store, fits Backend patterns | Need our own diff/merge tooling |
| Git repository per bot | Free history, diff, blame; proposers already speak git | Multi-tenant hosting, ACL, querying across bots, GC — a new infrastructure dependency |

Recommendation: DB-native, plus a **git export** (`avn genome export
--format git`) so humans and coding-agent proposers can work on a familiar
filesystem history (the Meta-Harness lesson) without git being the source of
truth.

## 8. Multi-bot and teams (future)

A genome is per bot. BCS owns relationships and routing, so a future
**team genome** (roles, routing hints, shared skills) would be a separate
artifact owned by BCS that references member genome revisions. Out of scope
for v1, but nothing here prevents it: lineage ids and refs are generic.
