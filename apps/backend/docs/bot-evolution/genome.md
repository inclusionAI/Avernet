# Bot Genome — the form of an evolved bot

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
needs (codebase evidence in [research.md §2.1](research.md#21-bot-config-manifest)):

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
| Unknown keys rejected; no metadata | Need provenance and annotations |
| Rollback limited to one step and service bots only | Need rollback to any accepted revision for every bot |

## 2. Shape

A Genome Revision = **pinned Manifest document + memory seed + metadata**,
recorded immutably and identified by the hash of its canonical form.

```yaml
# Illustrative — the normative schema is work item RSI-02.
genome_schema: 1
revision:                        # computed / platform-written, not authored
  id: sha256:7c1e…               # hash of canonical(spec) — content address
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
    - {name: refund-policy, blob: sha256:…, origin: {kind: center, ref: "center://…@v7"}}
    - {name: quality-check, blob: sha256:…, origin: {kind: local}}
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

- **Content-addressed blobs.** All file content lives in the existing
  content-addressed store (`ac_manifest_content`). Two revisions that share a
  skill share its blob. Diffs are cheap.
- **Pinned, not floating.** When a revision is recorded from a Manifest that
  uses `sources` with moving refs, the platform resolves them and stores the
  blobs. A floating Manifest is an *input*; a revision is a *fact*.
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
- Each op maps to a **risk tier** ([governance.md §3](governance.md#3-risk-tiers)).
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
3. Pinned resolution recorded on revision (P1).
4. `metadata` / `annotations` top-level key, ignored by apply (P1).
5. `engine_config` category enabled for an allowlist of keys (P3/P4).
6. `memory` category with modes (P5, depends on RSI-05).
7. Any-revision rollback for personal and service bots (P1).

## 7. Storage choice (open decision D-6)

| Option | For | Against |
| --- | --- | --- |
| **DB revisions + existing content-addressed store** (recommended) | Tenancy, ACL, queries over lineage and status, reuses `ac_manifest_content`, fits Backend patterns | Need our own diff/merge tooling |
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
