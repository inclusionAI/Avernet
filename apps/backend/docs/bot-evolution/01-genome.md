# Genome

> 中文版：[01-genome.zh-CN.md](01-genome.zh-CN.md)

> Status: DRAFT. Component in the [bot evolution architecture](design.md).
> This doc covers the Bot Genome: immutable, versioned revisions of a bot's
> definition, the refs that point at them, the patches that change them, and
> the Genome Registry that stores and serves them.

Proposed decision: [DR-1](decisions/0001-bot-genome-is-the-unit-of-evolution.md).
Builds on the Bot Config Manifest (`apps/backend/docs/bot-config-manifest/`).

## 1. Purpose and scope

A **Bot Genome** is the evolvable, declarative definition of one bot: persona
files, skills, curated memory, resources, tools, engine config, and a
non-evolvable `policy` section. A **Genome Revision** is one recorded,
immutable version of it. The Genome is the unit of evolution: every strategy
reads a revision and proposes a change to it, and every change that reaches a
running bot is a revision being applied.

In one sentence: a Genome Revision is **a complete, pinned Manifest, plus
curated memory, plus lineage and a locked policy, changed only through
patches**.

The **Genome Registry** is the component that implements this. It owns:

- Genome Revisions (content, lineage, provenance, status);
- named **refs** (`active`, `previous`, `canary`, `draft`, `candidate/<run>/<n>`)
  and their compare-and-swap updates;
- **Genome Patches**: their schema, validation, and application to a base
  revision;
- the **genome policy** (locked genes, mutable genes, pins, risk overrides)
  and its structural enforcement when a patch is recorded;
- content addressing: file bytes referenced by digest in the existing
  manifest content store;
- **compiling** a revision into a pinned Manifest document plus a memory
  projection, which is what apply and eval bots consume;
- diffs between any two revisions.

It explicitly does **not** own:

| Not owned here | Owner |
| --- | --- |
| Deciding whether a candidate is good (suites, graders, verdicts) | [07-verification.md](07-verification.md) |
| Deciding whether a revision may become `active`, risk-tier approvals, the review queue, rollout, and the promotion endpoint that moves `active` | [08-promotion.md](08-promotion.md) |
| Running strategies, bindings, `allowed_genes` per binding, budgets | [06-evolution-run.md](06-evolution-run.md) |
| The strategy-side view of a revision (`ctx.parent`, `ctx.workspace`, `ctx.candidates`) | [03-strategy.md](03-strategy.md) |
| Episodes and feedback that carry a revision id | [02-experience.md](02-experience.md) |
| The audit log of revisions, ref moves, gate decisions; the archive view of the ledger; lineage-aware parent selectors | [05-experiment-ledger.md](05-experiment-ledger.md) |
| Mechanism (strategy) revisions, which reuse this revision/ref/patch machinery | [10-meta-evolution.md](10-meta-evolution.md) |
| Physical projection of a revision onto an engine workspace, runtime memory | Engine adapter (ADR 0014/0017: Backend must not know engine paths) |

**Placement.** Backend, as a new core package `core/bot_genome/` next to
`core/bot_config_manifest/`, because Backend owns desired state and the
Manifest already lives there. Content reuses the manifest content store
(`core/bot_config_manifest/content/service.py`). Promotion sits beside it in
Backend because promotion must sit beside apply.

**Useful on its own.** The Genome Registry is phase P1: versioned bots,
attributable apply, and going back to any earlier revision are useful
independently of any evolution strategy.

## 2. Domain model

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `GenomeRevision` | One immutable version of a bot's definition: `spec` + `policy`, plus platform-written metadata (`revision`) | Genome Registry | Recorded once (from a patch or a Manifest document), never edited; only its `status` and annotations change; never deleted |
| `GenomeSpec` | The evolvable content of a revision: persona, skills, memory, resources, tools, engine config, script | Genome Registry (authored by owners, proposed by strategies) | Part of a revision; hashed |
| `GenomePolicy` | The non-evolvable section: locked genes, mutable genes, pins, risk overrides | Bot owner / tenant admin | Part of a revision; hashed; copied forward verbatim by the platform; no patch may touch it |
| `RevisionMetadata` | Id, per-bot `seq`, lineage, parents, provenance, status, evidence and evaluation links, annotations | Genome Registry (platform-written) | Written at record time; status/annotations updated later; not hashed |
| `Provenance` | Who or what created a revision (a person, a strategy run, later a bot) and from which evidence | Genome Registry | Written once at record time |
| `GenomeRef` | A named, movable pointer to a revision (`active`, `previous`, `canary`, `draft`, `candidate/<run>/<n>`) | Genome Registry; each ref has exactly one kind of mover | Moved by compare-and-swap; every move is an audited event |
| `GenomePatch` | A typed, itemized change against a named base revision; the only thing a strategy may submit | Submitted by strategies (via [Evolution Run](06-evolution-run.md)) or humans; validated by the Registry | Stored content-addressed; its digest is the candidate id |
| `PatchOp` | One item of a patch (`file.edit`, `skill.add`, `memory.add`, …) | Part of a patch | Each op maps to a risk tier |
| `MemoryItem` | One curated memory fact/rule/lesson, keyed | Genome (curated memory) | Added, updated, and retired by patch ops |
| `CompiledManifest` | A revision compiled into a pinned Manifest document plus a memory projection | Genome Registry (derived, not stored as truth) | Produced on demand for apply and eval bots |

### 2.1 Genes

A **gene** is a named, addressable part of `spec` that the policy and
bindings can refer to: a category (`persona`, `skills`, `memory`,
`resources`, `tools.mcp`, `tools.cli_tools`, `engine_config`, `script`), a
key inside one (`engine_config.reasoning_effort`), or one item
(`skills.refund-policy`). The gene path grammar is fixed by RSI-02.

### 2.2 GenomeRevision

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

RevisionId = str          # "sha256:<hex64>" over canonical({spec, policy})
Digest = str              # "sha256:<hex64>" address of bytes in the content store

RevisionStatus = Literal["draft", "candidate", "accepted", "rejected", "promoted", "archived"]

@dataclass(frozen=True)
class Provenance:
    kind: Literal["user", "strategy_run", "manifest_put", "bot"]   # "bot" postponed with DR-3
    actor: str                       # user id, or strategy "id@version"
    run_id: str | None = None        # set only when kind == "strategy_run"
    step: str | None = None          # the strategy's own step name, e.g. "propose"

@dataclass
class RevisionMetadata:
    id: RevisionId
    seq: int                         # per-bot, human-readable ("r42"); NOT an identity
    bot_id: str
    lineage_id: str                  # stable across descendants; a fork starts a new lineage
    parents: list[RevisionId]        # [] only for a bot's first revision; >1 for merge/crossover
    created_by: Provenance
    created_at: datetime
    status: RevisionStatus
    patch_from_parent: Digest | None # digest of the stored GenomePatch; None when recorded from a document
    evidence: list[str]              # "episode:ep_91", "finding:f_12"
    evaluations: list[str]           # links into Verification, never embedded scores
    annotations: dict[str, str] = field(default_factory=dict)   # review notes, experiment tags

@dataclass(frozen=True)
class GenomeRevision:
    genome_schema: int               # 1
    revision: RevisionMetadata       # platform-written, excluded from the hash
    spec: "GenomeSpec"
    policy: "GenomePolicy"
```

```python
@dataclass(frozen=True)
class PersonaFile:
    type: str                        # "SOUL.md", "AGENTS.md", "RULES.md", "LESSONS.md", ...
    digest: Digest

@dataclass(frozen=True)
class SkillOrigin:
    kind: Literal["center", "local", "git", "oss"]
    version: str | None = None       # pinned Center version when kind == "center"

@dataclass(frozen=True)
class SkillEntry:
    name: str
    origin: SkillOrigin
    digest: Digest | None = None     # None for Center skills: they resolve through Skill Center by version

@dataclass(frozen=True)
class MemorySpec:
    mode: Literal["seed", "merge", "replace"]
    items_digest: Digest             # the itemized memory set (not a raw MEMORY.md)

@dataclass(frozen=True)
class GenomeSpec:
    persona: list[PersonaFile]
    skills: list[SkillEntry]
    memory: MemorySpec | None        # None until RSI-05 lands (see §7)
    resources: list["ResourceEntry"]          # {path, digest}
    tools: "ToolsSpec"                        # {mcp: [{server_code, config_digest}], cli_tools: [{name, digest, version}]}
    engine_config: dict[str, str]             # allowlisted keys; string values (no floats in hashed content)
    script: "ScriptEntry | None"              # {digest}; locked by default
```

Every category is present in every revision. An empty list means "none";
it never means "leave as it was" (totality, §4.3).

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The normative schema is work item RSI-02.
{
  "genome_schema": 1,
  "revision": {                                   // computed / platform-written, not authored
    "id": "sha256:7c1e…",                         // hash of canonical({spec, policy}): content address
    "seq": 42,                                    // per-bot sequence number for humans ("r42"); not an identity
    "bot_id": "bot_123",
    "lineage_id": "lin_support_agent",            // stable across forks; a fork starts a new lineage
    "parents": ["sha256:a90b…"],                  // r41; more than one parent allowed (crossover/merge)
    "created_by": {"kind": "strategy_run", "run_id": "run_7f3", "step": "propose",
                   "actor": "clawevolve/bot-evolution@2.0.0"},
    "created_at": "2026-10-08T03:12:00Z",
    "status": "candidate",                        // draft | candidate | accepted | rejected | promoted | archived
    "patch_from_parent": "sha256:c41e…",          // digest of the stored Genome Patch = candidate id
    "evidence": ["episode:ep_91", "episode:ep_97", "finding:f_12"],
    "evaluations": ["eval:ev_301", "eval:ev_302"],// links into Verification, not embedded scores
    "annotations": {"experiment": "escalation-wording"}
  },
  "spec": {                                       // the evolvable content (authored / proposed)
    "persona": [                                  // == manifest.identity, but pinned
      {"type": "SOUL.md", "digest": "sha256:3f9a…"},
      {"type": "AGENTS.md", "digest": "sha256:81bd…"}
    ],
    "skills": [
      {"name": "refund-policy", "origin": {"kind": "center", "version": "center://refund-policy@v7"}},  // pinned Center version, not copied
      {"name": "quality-check", "digest": "sha256:0c55…", "origin": {"kind": "local"}}                  // bot-owned: stored, referenced by digest
    ],
    "memory": {                                   // new category, see §7
      "mode": "seed",                             // seed | merge | replace
      "items_digest": "sha256:9e20…"              // itemized memory set
    },
    "resources": [{"path": "data/kb/", "digest": "sha256:d4a7…"}],
    "tools": {
      "mcp": [{"server_code": "mcp.x.meet", "config_digest": "sha256:52c8…"}],
      "cli_tools": [{"name": "shopctl", "digest": "sha256:a613…", "version": "2.3.0"}]
    },
    "engine_config": {                            // allowlisted keys only; no floats in hashed content
      "model": "provider/model-x",
      "reasoning_effort": "medium"
    },
    "script": {"digest": "sha256:e1f0…"}          // carried, but LOCKED for evolution by default
  },
  "policy": {                                     // NOT evolvable: copied forward verbatim by the platform
    "locked_genes": ["script", "tools.mcp", "policy"],
    "mutable_genes": ["persona", "skills", "memory", "resources", "engine_config.reasoning_effort"],
    "pins": ["skills.refund-policy"],             // write-protected items (Hermes-style pinning)
    "risk_overrides": {}
  }
}
```

### 2.3 GenomePolicy

The policy says what automated actors may change on this bot. It is owned by
the bot owner or tenant admin, never by a strategy, and the platform rejects
any patch that touches it.

```python
@dataclass(frozen=True)
class GenomePolicy:
    locked_genes: list[str]          # never changed by a patch; default includes "script", "tools.mcp", "policy"
    mutable_genes: list[str]         # the only genes a patch may touch
    pins: list[str]                  # individual write-protected items, e.g. "skills.refund-policy"
    risk_overrides: dict[str, str]   # gene path -> risk tier ("T0".."T3"); may only RAISE a tier (proposed)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "locked_genes": ["script", "tools.mcp", "tools.cli_tools", "policy"],
  "mutable_genes": ["persona", "skills", "memory", "resources", "engine_config.reasoning_effort"],
  "pins": ["skills.refund-policy", "persona.RULES.md"],
  "risk_overrides": {"persona.SOUL.md": "T2"}     // owner raises the default tier for this file
}
```

How the policy is used:

- **At patch record time** (§6.2): ops on locked genes, ops outside
  `mutable_genes`, and ops on pinned items are rejected.
- **At binding configuration time**: a binding's `allowed_genes` must stay
  within the bot's policy (locked genes stay locked); a mismatch is rejected
  before any paid run starts. Bindings are defined in
  [06-evolution-run.md](06-evolution-run.md).
- **At candidate submission**: Promotion's static floor (`FloorCheck`)
  re-checks "no locked gene or pinned item touched" before any verification
  is spent; see [08-promotion.md](08-promotion.md).

This is where "tool and permission changes are human-only" is enforced
**structurally** rather than by convention: tool changes live in locked
genes by default, and the policy itself is outside what a patch can reach.
Changing the policy is an owner action that records a new revision (§4.4).

### 2.4 GenomeRef

```python
RefName = str   # "active" | "previous" | "canary" | "draft" | "candidate/<run>/<n>"

@dataclass(frozen=True)
class GenomeRef:
    name: RefName
    revision: RevisionId
    seq: int                         # seq of the revision, for display ("r42")
    moved_at: datetime
    moved_by: Provenance
    reason: str | None = None        # required for active/previous/canary moves

@dataclass(frozen=True)
class RefUpdate:
    name: RefName
    revision: RevisionId
    expected_revision: RevisionId | None   # CAS: None only when the ref must not exist yet
    reason: str | None = None
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "name": "active",
  "revision": "sha256:a90b…",
  "seq": 41,
  "moved_at": "2026-10-01T09:30:00Z",
  "moved_by": {"kind": "user", "actor": "user_owner_1"},
  "reason": "approved after review of run_6c2"
}
```

### 2.5 GenomePatch and PatchOp

```python
EditKind = Literal["replace_section", "insert_after"]   # full set fixed by RSI-02

@dataclass(frozen=True)
class TextEdit:
    kind: EditKind
    content: str
    heading: str | None = None       # for replace_section
    anchor: str | None = None        # for insert_after

@dataclass(frozen=True)
class FileEdit:                      # op "file.edit": persona md, SKILL.md, resource text files
    target: str                      # "persona/SOUL.md", "skills/refund-policy/SKILL.md"
    edits: list[TextEdit]

@dataclass(frozen=True)
class SkillAdd:                      # op "skill.add"
    name: str
    files: dict[str, "str | DigestRef"]   # small text inline; larger files uploaded first, referenced by digest

@dataclass(frozen=True)
class SkillUpdate:                   # op "skill.update"
    name: str
    file_ops: list["UnifiedDiff"]    # {kind: "unified_diff", path, diff}

@dataclass(frozen=True)
class MemoryItem:
    key: str
    text: str
    tags: list[str]
    source: list[str]                # evidence ids, e.g. "episode:ep_91"

@dataclass(frozen=True)
class MemoryAdd:                     # op "memory.add"
    item: MemoryItem

@dataclass(frozen=True)
class MemoryUpdate:                  # op "memory.update" (proposed): replaces text, tags, source of an existing key
    item: MemoryItem

@dataclass(frozen=True)
class MemoryRetire:                  # op "memory.retire"
    key: str

@dataclass(frozen=True)
class EngineConfigSet:               # op "engine_config.set"; allowlisted keys only
    key: str
    value: str

PatchOp = FileEdit | SkillAdd | SkillUpdate | MemoryAdd | MemoryUpdate | MemoryRetire | EngineConfigSet

@dataclass(frozen=True)
class GenomePatch:
    patch_schema: int                # 1
    base: RevisionId                 # must equal the parent; CAS on record
    ops: list[PatchOp]
    rationale: str                   # required; shown to reviewers
    evidence: list[str]              # required for non-trivial ops
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch_schema": 1,
  "base": "sha256:a90b…",                               // r41; must equal the parent
  "ops": [
    {"op": "file.edit", "target": "persona/SOUL.md",     // text files: persona md, SKILL.md, resources
     "edits": [
       {"kind": "replace_section", "heading": "## Escalation", "content": "Escalate to a human when a refund exceeds the policy limit…"},
       {"kind": "insert_after", "anchor": "## Tone", "content": "Confirm the order id before quoting amounts."}
     ]},
    {"op": "skill.add", "name": "invoice-lookup",
     "files": {"SKILL.md": "---\nname: invoice-lookup\n---\nLook up an invoice by order id…",   // small text: inline
               "scripts/lookup.py": {"digest": "sha256:6b1d…"}}},                              // larger file: uploaded first
    {"op": "skill.update", "name": "refund-policy",
     "file_ops": [{"kind": "unified_diff", "path": "SKILL.md", "diff": "@@ -12,3 +12,4 @@ partial refunds…"}]},
    {"op": "memory.add",                                  // itemized memory, see §7
     "item": {"key": "customer-tier-rules", "text": "Gold customers get free return shipping.", "tags": ["billing"], "source": ["episode:ep_91"]}},
    {"op": "memory.update",                               // proposed op, see §6.1
     "item": {"key": "returns-window", "text": "Returns are accepted within 30 days.", "tags": ["returns"], "source": ["episode:ep_97"]}},
    {"op": "memory.retire", "key": "old-shipping-sla"},
    {"op": "engine_config.set", "key": "reasoning_effort", "value": "high"}
  ],
  "rationale": "Partial-refund requests were escalated too late in 6 of 9 failing episodes.",
  "evidence": ["finding:f_12", "episode:ep_91"]
}
```

This example shows every op kind at once. Against the §2.2 policy it would
be rejected, because `skills.refund-policy` is pinned there (§6.2); a real
patch only touches mutable, unpinned genes.

### 2.6 CompiledManifest

```python
@dataclass(frozen=True)
class CompiledManifest:
    revision_id: RevisionId
    document: dict                   # a Manifest schema-v1-compatible document; every category present;
                                     # file entries point at the content store by digest (§8 item 6)
    document_digest: Digest          # recorded on the apply report
    memory_projection: "MemoryProjection | None"   # items + mode, handed to the engine (§7)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "revision_id": "sha256:7c1e…",
  "document_digest": "sha256:f27a…",
  "document": {
    "schema_version": 1,
    "manifest": {
      "identity": [{"type": "SOUL.md", "source": {"content_store": "sha256:3f9a…"}}],
      "skills": [{"name": "refund-policy", "center_version": "v7"},
                 {"name": "quality-check", "source": {"content_store": "sha256:0c55…"}}],
      "resources": [{"path": "data/kb/", "source": {"content_store": "sha256:d4a7…"}}],
      "mcp": [],
      "cli_tools": [{"name": "shopctl", "source": {"content_store": "sha256:a613…"}}]
    }
  },
  "memory_projection": {"mode": "seed", "items_digest": "sha256:9e20…"}
}
```

The exact Manifest field names in `document` follow Manifest schema v1 and
the content-store source added in §8; the shape above is illustrative.

## 3. Why the Manifest is not enough

The Manifest was built so that users can manage a bot's assets themselves:
they keep skills, persona files, resources, MCP and CLI tools in their own
sources and declare them in one document that the platform applies. It is the
right **foundation**: engine-neutral, declarative, converges on every engine
including teclaw, applies only through existing core services, and already
has a capability matrix and append-only apply reports. Reusing it means
evolution gets delivery to every engine for free.

But as the artifact of an evolving bot it is missing what evolution needs
(codebase evidence in [research.md](research.md), section "Bot Config
Manifest": one mutable row `ac_bot_config_manifest`,
`repository/models.py:64`; no revision, hash, parent, or ETag,
`services/config_manifest_service.py:141-184`; apply re-reads the current
document, `services/config_manifest_apply_service.py:846-875`; unknown keys
rejected, `schema/validator.py:58`; service-bot rollback one step back only,
`core/service_bot/services/publish_rollback_mixin.py:38-80`):

| Gap in Manifest v1 | Why evolution needs it | Addressed by |
| --- | --- | --- |
| One mutable row per bot, no revision history, no content hash | Lineage, archive, reproducibility, going back to any point | Revisions (§4) |
| No parent pointer | Lineage tree; descendant-aware selection | `parents`, `lineage_id` |
| No optimistic concurrency control. HTTP offers it through an `ETag` (a version tag the server returns with a document) and an `If-Match` request header (the client sends back the tag it read; the server rejects the write with `412 Precondition Failed` if the document changed since). `PUT /config-manifest` has neither | A strategy and a human editing at the same time silently overwrite each other; whoever writes last wins without knowing | Compare-and-swap on refs (§5) |
| Apply re-reads the current document; the report does not say which bytes were applied | Cannot attribute behaviour or experience to a version | Apply reports record `revision_id` and document digest (§8) |
| Sources are moving refs (git branch, OSS key) re-resolved on each apply | A genome must be immutable: every source pinned to commit SHA / digest | Pinned content (§4.2) |
| Whole-document PUT, category-atomic replace | Strategies must submit small, reviewable, itemized patches | Genome Patch (§6) |
| `MEMORY.md` / `IDENTITY.md` reserved and rejected | Learned memory is half of what self-improvement produces | Curated memory (§7) |
| `engine_config` rejected in v1 | Model / temperature / reasoning budget are legitimate tunables | Allowlisted `engine_config` (§8) |
| A category left out of the document means "leave it untouched" | A revision must fully determine the bot; otherwise two applies of the same revision can produce different bots depending on prior state | Totality (§4.3) |
| No place for metadata: unknown keys are rejected | Evolution needs **provenance** (who or what produced a version, from which evidence, with which patch) and **annotations** (free-form labels such as review notes or experiment tags) | Kept on the revision record, not inside the Manifest document, so the Manifest schema does not change for this |
| Personal bots have no way back to an earlier configuration; service bots have their own publish rollback (one step back) | Going back must work for every bot and to any earlier revision | Going back = promoting an earlier revision (§5.3) |

Rejected alternatives (from DR-1):

- **A separate evolution artifact (e.g. a ClawEvolve Pack) delivered by its
  own path.** Rejected: two sources of truth, and Packs are OpenClaw-specific
  workspace snapshots that cannot reach teclaw.
- **Keep Manifest v1 unchanged and version outside it.** Rejected: apply
  would still re-read a mutable document, so behaviour could not be
  attributed to a version.
- **Git repository per bot as source of truth.** Deferred; see §9.2.

## 4. Revisions

### 4.1 What a revision adds to the Manifest

Concretely, a revision adds exactly these things on top of the Manifest:

1. **Revision identity and lineage**: a content-hash id plus a readable
   per-bot sequence number, parent(s), provenance, status, and named refs
   moved by compare-and-swap (§5).
2. **A non-evolvable `policy` section** (§2.3).
3. **Pinned content**: every source resolved to a commit SHA or content
   digest, so the same revision always yields the same bytes (§4.2).
4. **Totality**: every category is present in every revision (§4.3).
5. **Patch-only change for automated actors**: strategies submit typed,
   itemized patches against a named base revision (§6). Humans can still
   record a revision from a whole document.
6. **One new content category, curated memory** (§7), which needs a new
   engine contract.

Deliberately **not** added:

- **Evaluation scores.** They live in [Verification](07-verification.md) and
  are only linked (`evaluations`); the archive view of the ledger in
  [05-experiment-ledger.md](05-experiment-ledger.md) joins them. The genome
  stays a pure definition.
- **`engine_config` as a genome-only addition.** The Manifest already has the
  category; enabling an allowlisted subset is a Manifest change evolution
  benefits from (§8).

### 4.2 Digests and pinning

- **Digests, not content.** A `digest` (`sha256:…`) is the *address* of a
  file's bytes, not the bytes. The bytes are stored once in the existing
  manifest content store and fetched by digest when needed (§9.1). Two
  revisions that share a file share its bytes, so storage is deduplicated
  and diffs are cheap.
- **Pinned, not floating.** When a revision is recorded from a Manifest that
  uses `sources` with moving refs, the platform resolves them and stores the
  bytes. A floating Manifest is an *input*; a revision is a *fact*.
- **Center skills are pinned, not copied.** Skill Center already stores
  immutable, governed versions (ADR 0010). A revision records the Center
  version; only bot-owned skills and git/OSS-sourced content are stored in
  the content store. A `center://` version in a revision is a pinned
  reference to a governed, published version, not evidence of activation.

### 4.3 Totality

Recording a revision from a partial Manifest document fills every omitted
category from the parent revision (or from the bot's current state for the
bot's first revision), so the stored revision is complete. Compiling a
revision for apply always emits every category. `[]` means "none". Unlike a
Manifest document, a revision never leaves a category to prior state, so
two applies of the same revision always produce the same bot.

### 4.4 Identity, sequence numbers, and status

- **Id.** `id = "sha256:" + hex(SHA-256(RFC8785({spec, policy})))`. The
  `revision` metadata is excluded, so identical content always has the same
  id, whoever produced it. Recording content that already exists returns the
  existing revision; no duplicate is created and no new `seq` is assigned.
- **`seq`.** A per-bot counter assigned when a new id is first recorded, for
  humans (`r41`, `r42`). It is never an identity and never used in hashes.
- **Lineage.** `parents` points at the base revision(s). `lineage_id` is
  stable across descendants; a fork starts a new lineage. More than one
  parent is allowed for merge or crossover strategies.
- **Status** is platform-written metadata, outside the hash:

| Status | Meaning (proposed) | Set by |
| --- | --- | --- |
| `draft` | Recorded from an owner's manual edit (UI/API, `PUT /config-manifest`) | Genome Registry |
| `candidate` | Recorded from a patch submitted during a run, not yet decided | Genome Registry |
| `accepted` | The gate accepted it (it may wait for review or rollout) | [Promotion](08-promotion.md) |
| `rejected` | Verification or the gate rejected it; kept in the archive | [Promotion](08-promotion.md) |
| `promoted` | Has been made `active` at least once | [Promotion](08-promotion.md) |
| `archived` | Retired from consideration; still never deleted | Promotion / owner |

Rejected and retired revisions are never deleted; the archive keeps every
candidate.

- **Policy changes.** Because `policy` is part of the hashed content, an
  owner changing the policy records a new revision (provenance `user`) with
  the same `spec`. It is a human-only change (T3 in
  [08-promotion.md](08-promotion.md)).

### 4.5 `script` and `engine_config`

- **`script` is locked by default.** Imperative startup scripts are the most
  dangerous evolvable surface and are already rejected on teclaw. They remain
  part of the genome so a revision is complete, but are locked unless the
  owner unlocks them.
- **`engine_config`** carries only allowlisted keys, with string values in
  hashed content (for example `"temperature": "0.2"`, see §9.3).

## 5. Refs

### 5.1 Ref kinds

Refs are named, movable pointers to immutable revisions, git-style:

| Ref | Meaning | Who moves it |
| --- | --- | --- |
| `active` | What the bot runs | [Promotion](08-promotion.md) only |
| `previous` | Last `active`, kept for quick "go back" | Promotion only |
| `canary` | Revision on canary instances | Promotion only |
| `draft` | Owner's in-progress manual edit | Owner (UI/API) |
| `candidate/<run>/<n>` | The n-th candidate of a run | [Evolution Run](06-evolution-run.md) |
| `inbox/<id>` | Bot-submitted draft patches (fast loop) | Postponed: bot callers are a later, separate design |

### 5.2 Compare-and-swap

Every ref update carries `expected_revision`: the revision the caller
believes the ref points at now. The Registry moves the ref only if that is
still true; otherwise it returns `409 Conflict` with the current revision,
and the caller re-reads and decides. This closes the concurrent-edit gap: a
strategy and a human editing at the same time can no longer silently
overwrite each other. Patches add a second CAS: a patch's `base` must equal
the revision it is applied to (§6.2).

Every ref move is an append-only event (ref, from, to, actor, reason, time).
The audit log and its read model are in
[05-experiment-ledger.md](05-experiment-ledger.md).

### 5.3 Going back is promotion

Within the evolution platform, "rolling back" to `r41` means **promoting
`r41` again**. There is no separate rollback path. Because ids are content
hashes, no new revision is created: the `active` ref moves back to `r41`,
`previous` moves to the revision that was active, and the ref log records the
move with actor and reason. To the rest of the platform this is just "apply
this revision", exactly like any forward promotion:

- **Personal bot:** the revision is applied through Manifest apply.
- **Service bot:** the revision goes out as the **next published version**
  through the existing draft → verify → publish flow, with `revision_id`
  stored on the publish record.

The existing service-bot rollback feature is left as it is. This design
neither replaces nor extends it. The promotion endpoint
(`POST /bots/{bot}/genome/promotions`) and the rollout rules are specified in
[08-promotion.md](08-promotion.md); the Genome Registry provides the CAS ref
move and the compile step it uses.

### 5.4 Relationship to today's Manifest row

In P1 the existing `PUT /config-manifest` keeps working and becomes sugar
for "record a revision from this document and move `draft` (and, for
backward compatibility, `active`) to it, then apply". Existing clients see no
change; the row becomes a projection of `active`. The existing
`/config-manifest` endpoints remain and become views over the Registry.

## 6. Genome Patch

### 6.1 Why patches

A Genome Patch is the only thing a strategy may submit. It is itemized and
typed, following ACE's finding that delta updates avoid context collapse
(whole rewrites gradually erase accumulated detail). Whole-document
replacement by automated actors is not supported. Humans can submit a patch
too (for example `avn genome patch apply`), or record a whole document.

Ops shown in the sources (the full op set is fixed by RSI-02):

| Op | Changes | Notes |
| --- | --- | --- |
| `file.edit` | A text file: persona md, `SKILL.md`, resource text files | Structured edits: `replace_section` (by heading), `insert_after` (by anchor) |
| `skill.add` | A new bot-owned skill | Small text inline; larger files uploaded first with `PUT …/genome/content`, referenced by digest |
| `skill.update` | Files of an existing bot-owned skill | `unified_diff` file ops |
| `memory.add` | Adds a curated memory item | Item carries `key`, `text`, `tags`, `source` evidence |
| `memory.update` *(proposed)* | Replaces the text, tags, and source of an existing item, by key | Needed by memory consolidation ([04-default-strategies.md](04-default-strategies.md)); the earlier version stays in history |
| `memory.retire` | Retires an item by key | Retired items are not deleted from history |
| `engine_config.set` | One allowlisted engine config key | String value |

Whether Genome Patch ops map onto JSON Patch (RFC 6902) is decided in RSI-02.

### 6.2 Rules enforced when recording a patch

The Registry enforces these when recording a candidate or draft from a patch
(a structural subset of the gate's static floor; the full floor, including
secret/PII/URL scanning, is in [08-promotion.md](08-promotion.md)):

- **Base must match.** `base` must equal the parent; ops must apply cleanly
  or the patch is rejected. There is no fuzzy merge in v1.
- **Policy.** Ops on locked genes, on genes outside `mutable_genes`, or on
  pinned items are rejected. Any op reaching `policy` is rejected.
- **Binding scope.** When recorded for a run, ops must also stay within the
  run's `allowed_genes` (passed by [Evolution Run](06-evolution-run.md)).
- **Rewrite detection.** A `file.edit` that changes more than a configurable
  fraction of a file (default 40%) is flagged `rewrite` and raises the risk
  tier.
- **Limits.** Patch size, file count, and file size limits mirror Manifest
  limits.
- **Rationale and evidence.** `rationale` is required; `evidence` is
  required for non-trivial ops.

### 6.3 Risk tier per op

Each op maps to a **risk tier**; a patch takes the maximum over its ops.
What each tier means for promotion (auto vs human review, owner ceilings) is
defined in [08-promotion.md](08-promotion.md). The default mapping is carried
in the patch schema as data (work item RSI-02: "every patch op has a defined
risk tier"); the table in 08-promotion.md is the same mapping and must stay
identical to this one:

| Op | Target | Tier |
| --- | --- | --- |
| annotation change on the revision record | — | T0 |
| `memory.add`, `memory.update`, `memory.retire` (modes `seed`, `merge`) | `memory` | T1 |
| `skill.update` touching only the skill description | `skills/<name>` | T1 |
| `file.edit` | `resources/...` | T1 |
| `file.edit` | `persona/*` (SOUL, AGENTS, RULES) | T2 |
| `skill.add`, `skill.update` (content) | `skills/<name>` | T2 |
| `engine_config.set` (allowlisted key) | `engine_config.<key>` | T2 |
| any op on `tools.mcp`, `tools.cli_tools`, `script`; memory `replace` mode; permission changes | locked genes | T3 |
| any op on `policy` | `policy` | rejected by the floor (never promotable) |

Raises applied after this mapping (a `rewrite`-flagged edit and edits that
touch guardrail-like text are at least T2) are explained in
[08-promotion.md](08-promotion.md).

`policy.risk_overrides` lets an owner raise a gene's tier (proposed: it may
never lower it).

### 6.4 Composition

Patches compose: a run with several accepted iterations can be squashed into
one patch for review, while the archive keeps each step.

### 6.5 Patch identity and candidates

The stored patch is content-addressed. Its digest is the **candidate id**
used by strategies and by Promotion (`sha256:c41e…`): a retried submission of
the same patch returns the same id and creates nothing new. The resulting
revision has its own id (hash of `{spec, policy}`) and records the patch in
`patch_from_parent`. Keeping both identities is open decision G-9. See [03-strategy.md](03-strategy.md) for the
strategy-side `ctx.candidates.submit`.

## 7. Memory

Today `MEMORY.md` and `IDENTITY.md` are reserved: apply never writes or
deletes them, because the engine's runtime writes them. Self-improvement
needs part of memory to be evolvable, so memory is split:

| Layer | Owner | In genome? | Evolution |
| --- | --- | --- | --- |
| **Episodic / runtime memory**: what the bot writes as it works | Engine runtime | No | Captured into [Experience](02-experience.md) as evidence |
| **Curated memory (seed)**: facts, rules, lessons the bot should start with | Genome | **Yes**, itemized | Proposed by consolidation ("dream") strategies, gated, promoted |

Projection onto the engine is engine-owned (ADR 0014/0017: Backend must not
know engine paths). Proposed **engine memory projection contract** (work item
RSI-05), a Plugin API implemented by the engine adapter:

```python
class EngineMemoryProjection(Protocol):
    """Engine-owned. Backend never learns where or how memory is stored."""

    def export_memory(self, bot_id: str) -> list[MemoryItem]:
        """Read the engine's runtime memory into normalized items, for consolidation strategies."""

    def project_memory(self, bot_id: str, items: list[MemoryItem],
                       mode: Literal["seed", "merge", "replace"]) -> None:
        """Materialise curated items into the engine's layout.
        seed: only if absent. merge: curated items upserted by key, runtime items untouched.
        replace: the curated set becomes the memory (T3, human approval)."""
```

- Engines declare support per mode in the capability matrix; teclaw support
  goes through the `BotConfigArtifact` contract like other categories.
- This amends the "reserved files are never touched" rule, so it is a stated
  consequence of DR-1 and must be confirmed with the engine owners.
- **Interim:** until RSI-05 lands, memory evolution is limited to a
  platform-managed file (for example `LESSONS.md`) declared as a persona
  file. This needs no engine change; `spec.memory` stays empty until then.

## 8. Manifest v2 changes

Kept backward compatible with schema v1 documents:

1. Revision table + refs + CAS (P1).
2. Apply reports record `revision_id` and the compiled document digest (P1).
3. Pinned resolution recorded on the revision (P1); revisions are total even
   when recorded from a partial document.
4. `engine_config` category enabled for an allowlist of keys (P3/P4).
5. `memory` category with modes (P5, depends on RSI-05).
6. A **content-store source** for apply: an entry may point at a digest in
   the platform's own content store instead of an external source. Apply
   already reads that store after every fetch, so this only skips the fetch
   (P1).
7. The manifest content store accepts **produced** (non-fetched) content
   with patch provenance (P2).

Provenance and annotations need no Manifest change: they live on the
revision record. For service bots, the publish record references the
revision id (RSI-04).

## 9. Storage

### 9.1 Content: reuse the manifest content store

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

Genome revisions reference content in this same store by digest; there is no
second copy.

How content is fetched from a digest:

- **Inside Backend,** `ManifestContentService.read(digest)` returns the bytes
  and re-verifies the hash on read.
- **Outside Backend** (UI, CLI, strategies, evaluators), the Registry API
  serves `GET /bots/{bot}/genome/content/{digest}`. It is access-checked
  against the bot, so a digest is not a capability by itself. New content is
  uploaded with `PUT /bots/{bot}/genome/content`, which returns its digest.
- **At apply,** compiling a revision emits Manifest entries that point at the
  content store by digest (§8 item 6). Apply hands those bytes to the
  materialisers exactly as it does with fetched content today.
- **Center skills** have no digest in the revision; they resolve through
  Skill Center by their pinned version.

Three extensions are needed:

1. **A write path for produced content.** Today every store event is a
   *fetch* (`source_url` is required). Content created by a strategy, such as
   an edited `SKILL.md` or a new memory item, was never fetched. It needs a
   store call whose provenance is the producing patch and run.
2. **Retention for the archive.** The archive keeps every candidate. Most
   blobs are small text and deduplicate, but manifest resources may be
   100–200 MiB each. Unconditional retention is acceptable for v1. Any later
   sweep may only remove blobs that no revision references, and must never
   remove a blob reachable from a `promoted` revision.
3. **Readers outside Backend.** Strategies and evaluators in
   `apps/evolution` read content through the Registry API (or a materialised
   sandbox), never from the blob directory directly.

### 9.2 Revisions: database, not git (open decision D-6)

| Option | For | Against |
| --- | --- | --- |
| **DB revisions + existing content-addressed store** (recommended) | Tenancy, ACL, queries over lineage and status, reuses the manifest content store, fits Backend patterns | Need our own diff/merge tooling |
| Git repository per bot | Free history, diff, blame; coding agents already speak git | Multi-tenant hosting, ACL, querying across bots, GC: a new infrastructure dependency |

Recommendation: DB-native, plus a **git export** (`avn genome export
--format git`) so humans and coding-agent strategies can work on a familiar
filesystem history (the Meta-Harness lesson) without git being the source
of truth.

Proposed tables (names illustrative, fixed by RSI-03): `ac_genome_revision`
(id, bot, seq, lineage, parents, provenance, status, canonical JSON of
`{spec, policy}`), `ac_genome_ref` (bot, name, revision, version for CAS),
`ac_genome_ref_event` (append-only ref log), `ac_genome_patch` (digest,
base, canonical JSON).

### 9.3 Serialization: canonical JSON

Every platform-owned record in this design is stored and exchanged as JSON:
genome revisions, Genome Patches, mechanism revisions, job inputs and
outputs, verdicts, and Experiment Ledger entries.

- **Canonical form.** A record is serialized with the JSON Canonicalization
  Scheme (RFC 8785) before hashing. The revision `id` is the SHA-256 of the
  canonical form of `{spec, policy}`; `revision` metadata is excluded.
- **No floats in hashed content.** RFC 8785 formats numbers as IEEE doubles,
  so hashed content uses integers, strings, and enums only (for example
  `max_wall_clock_s: 7200`, `temperature: "0.2"`).
- **Validation.** Every record type has a JSON Schema (RSI-02, RSI-06), the
  same mechanism `BotConfigArtifact` already uses (`artifact.schema.json`)
  and the one OpenAPI is built on.
- **JSON only for everything new**, both when authored and when stored.
- **The Bot Config Manifest stays YAML.** It is an existing contract and is
  not changed. When a revision is recorded from a Manifest document, the YAML
  is parsed once, omitted categories are filled, sources are pinned, and
  canonical JSON is stored. The original YAML is kept as a provenance record,
  so its comments are not lost, but it is never the hashed or exchanged form.

Why not YAML as the stored form: it has no canonical serialization (the same
data can be written many ways, so it cannot be hashed directly); its implicit
typing differs between YAML 1.1 and 1.2 and between libraries (`no`, `on`,
`1.10`, dates); and every plugin, in any language, must read exactly the
same values. Patch and diff tooling (JSON Patch, RFC 6902; JSON Merge Patch,
RFC 7396) is also defined over JSON.

## 10. Beyond one bot

- **Teams (future).** A genome is per bot. BCS owns relationships and
  routing, so a future **team genome** (roles, routing hints, shared skills)
  would be a separate artifact owned by BCS that references member genome
  revisions. Out of scope for v1, but nothing here prevents it: lineage ids
  and refs are generic.
- **Mechanisms (level 3, later).** The Registry is designed to be generic
  over `target_kind` (`bot_genome` now, `mechanism` later): strategy versions
  can be stored with the same revision / ref / patch machinery, with refs
  scoped per strategy family. See [10-meta-evolution.md](10-meta-evolution.md).
- **Cross-bot skill transfer (P6)** goes through Skill Center governance
  (ADR 0010) and is re-evaluated per consuming bot.

## 11. Service interface

The Genome Registry is a Backend core service, transport-agnostic, with a
`GenomeRegistry` Protocol. Callers in Backend (Promotion, the
`/config-manifest` compatibility layer) use it in process; callers in
`apps/evolution` (Evolution Run, Verification) use it through the API in §12.

```python
from typing import Protocol, Literal

class GenomeRegistry(Protocol):
    # --- revisions -------------------------------------------------------
    def get_revision(self, bot_id: str, rev: RevisionId | str) -> GenomeRevision:
        """Accepts a revision id, "r<seq>", or a ref name. Raises NotFound."""

    def list_revisions(self, bot_id: str, *, status: RevisionStatus | None = None,
                       parent: RevisionId | None = None, lineage_id: str | None = None,
                       page: int = 1, page_size: int = 20) -> "Page[RevisionMetadata]":
        """Metadata only, newest seq first."""

    def record_patch(self, bot_id: str, patch: GenomePatch, *, created_by: Provenance,
                     status: Literal["draft", "candidate"],
                     allowed_genes: list[str] | None = None) -> GenomeRevision:
        """Validate (§6.2), store the patch content-addressed, apply it to `patch.base`,
        and record the resulting revision. Idempotent: the same patch returns the same
        revision. `allowed_genes` is None for owner patches (policy only), a list for runs.
        Raises BaseMismatch, PatchRejected(reasons), LimitExceeded."""

    def record_manifest(self, bot_id: str, manifest_yaml: str, *, created_by: Provenance,
                        base: RevisionId | None) -> GenomeRevision:
        """Parse a Manifest document, fill omitted categories from `base` (or current state),
        pin every source, store canonical JSON, keep the YAML as provenance. Status `draft`."""

    def set_status(self, bot_id: str, rev: RevisionId, status: RevisionStatus,
                   *, actor: Provenance, reason: str) -> None:
        """Platform-internal (Promotion). Status is metadata; content never changes."""

    def diff(self, bot_id: str, rev: RevisionId, against: RevisionId) -> "GenomeDiff":
        """Per-category, per-item differences; text files as unified diffs."""

    # --- refs ------------------------------------------------------------
    def list_refs(self, bot_id: str) -> list[GenomeRef]: ...

    def move_ref(self, bot_id: str, update: RefUpdate, *, actor: Provenance) -> GenomeRef:
        """Compare-and-swap. Raises RefConflict(current) if the ref moved.
        Only the designated mover may move each ref kind (§5.1); `active`, `previous`,
        `canary` are moved only by Promotion."""

    # --- content ---------------------------------------------------------
    def read_content(self, bot_id: str, digest: Digest) -> bytes:
        """Access-checked against the bot; the digest must be reachable from one of its revisions
        or uploads. Hash re-verified on read."""

    def put_content(self, bot_id: str, data: bytes, *, provenance: Provenance) -> Digest:
        """Produced content (§9.1 extension 1). Idempotent by digest."""

    # --- apply / materialisation -----------------------------------------
    def compile(self, bot_id: str, rev: RevisionId) -> CompiledManifest:
        """Pinned, total Manifest document plus memory projection. Used by Promotion (apply,
        service-bot publish) and Verification (eval bot materialisation)."""
```

```python
@dataclass(frozen=True)
class GenomeDiff:
    revision: RevisionId
    against: RevisionId
    changes: list["GeneChange"]      # {gene, kind: added|removed|modified, diff?: str}
    risk_tier: str                   # max tier over the changes, by the §6.3 mapping
    rewrite_flags: list[str]         # files flagged `rewrite`
```

Errors are typed and map onto HTTP statuses in §12: `NotFound` → 404,
`RefConflict` / `BaseMismatch` → 409, `PatchRejected` → 422,
`LimitExceeded` → 413, `MoverNotAllowed` → 403.

## 12. API

Public API prefix: `/openapi/v1`. Paths below are relative to it. All
request and response bodies are JSON except content bytes. POSTs accept an
`Idempotency-Key` header (rules in [09-evolution-api.md](09-evolution-api.md)).
Responses below show the `data` payload of the standard envelope; see
[09-evolution-api.md](09-evolution-api.md) for the envelope, errors,
pagination, and idempotency.
Callers in the first iteration are humans (UI, `avn` CLI), pipelines, and
platform services; bot callers are postponed to a later, separate design.

### GET /bots/{bot}/genome/revisions

List revision metadata, filtered. Called by UI, CLI (`avn genome log`),
pipelines, the Ledger read model.

```http
GET /openapi/v1/bots/bot_123/genome/revisions?status=candidate&parent=sha256:a90b…&page=1&page_size=2
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {"id": "sha256:7c1e…", "seq": 42, "parents": ["sha256:a90b…"], "status": "candidate",
     "lineage_id": "lin_support_agent",
     "created_by": {"kind": "strategy_run", "run_id": "run_7f3", "step": "propose", "actor": "clawevolve/bot-evolution@2.0.0"},
     "created_at": "2026-10-08T03:12:00Z", "patch_from_parent": "sha256:c41e…"}
  ]
}
```

Errors: 404 unknown bot; 400 invalid filter.

### POST /bots/{bot}/genome/revisions

Record a revision from a patch (`{base, patch}`) or from a Manifest document
(`{manifest}`). Called by Evolution Run (when a strategy calls
`ctx.candidates.submit`; strategies never call this directly), and by owners
through UI/CLI (`avn genome patch apply`). Returns the revision; recording
content that already exists returns the existing one with `200`.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: from a patch, recorded as a run candidate (internal caller: Evolution Run)
{
  "base": "sha256:a90b…",
  "status": "candidate",
  "run": {"run_id": "run_7f3", "binding_id": "bind_01", "allowed_genes": ["persona", "skills"]},
  "patch": {
    "patch_schema": 1,
    "base": "sha256:a90b…",
    "ops": [{"op": "file.edit", "target": "persona/SOUL.md",
             "edits": [{"kind": "replace_section", "heading": "## Escalation", "content": "Escalate to a human when…"}]}],
    "rationale": "Partial-refund requests were escalated too late.",
    "evidence": ["episode:ep_91"]
  }
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: from a Manifest document (owner edit); YAML carried as a string, stored as provenance
{
  "base": "sha256:a90b…",
  "status": "draft",
  "manifest": {"format": "yaml", "document": "schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n      source: {git: support-bot, path: SOUL.md, ref: main}\n"}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 201 Created
{
  "id": "sha256:7c1e…",
  "seq": 42,
  "status": "candidate",
  "parents": ["sha256:a90b…"],
  "patch_digest": "sha256:c41e…",
  "risk_tier": "T2",
  "flags": [],
  "ref": "candidate/run_7f3/1"
}
```

Errors: 409 `base_mismatch` (base is not the current parent, or ops do not
apply cleanly); 422 `patch_rejected` with reasons (`locked_gene`,
`pinned_item`, `gene_not_allowed`, `missing_rationale`, `schema`); 413
`limit_exceeded`.

### GET /bots/{bot}/genome/revisions/{rev}

Full revision (metadata, `spec`, `policy`). `{rev}` is an id, `r<seq>`, or a
ref name. Called by UI, CLI (`avn genome show`), Evolution Run (to build
`ctx.parent`), Verification.

```http
GET /openapi/v1/bots/bot_123/genome/revisions/r41
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "genome_schema": 1,
  "revision": {"id": "sha256:a90b…", "seq": 41, "bot_id": "bot_123", "lineage_id": "lin_support_agent",
               "parents": ["sha256:5d02…"], "status": "promoted", "created_at": "2026-09-30T02:40:00Z",
               "created_by": {"kind": "user", "actor": "user_owner_1"}, "patch_from_parent": null,
               "evidence": [], "evaluations": ["eval:ev_288"], "annotations": {}},
  "spec": {"persona": [{"type": "SOUL.md", "digest": "sha256:2b77…"}], "skills": [], "memory": null,
           "resources": [], "tools": {"mcp": [], "cli_tools": []},
           "engine_config": {"reasoning_effort": "medium"}, "script": null},
  "policy": {"locked_genes": ["script", "tools.mcp", "policy"], "mutable_genes": ["persona", "skills", "memory"],
             "pins": [], "risk_overrides": {}}
}
```

Errors: 404.

### GET /bots/{bot}/genome/revisions/{rev}/diff?against=

Diff two revisions. Called by the review UI and the candidate report in
[08-promotion.md](08-promotion.md), CLI (`avn genome diff`).

```http
GET /openapi/v1/bots/bot_123/genome/revisions/r42/diff?against=r41
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "revision": "sha256:7c1e…",
  "against": "sha256:a90b…",
  "risk_tier": "T2",
  "rewrite_flags": [],
  "changes": [
    {"gene": "persona.SOUL.md", "kind": "modified",
     "diff": "@@ -14,3 +14,3 @@ ## Escalation\n-Escalate when the customer asks.\n+Escalate to a human when a refund exceeds the policy limit.\n"},
    {"gene": "skills.invoice-lookup", "kind": "added"}
  ]
}
```

Errors: 404 either revision; 422 revisions of different bots.

### GET /bots/{bot}/genome/refs

List refs. Called by UI, CLI (`avn genome refs`), Evolution Run (resolving a
binding's `parent`), Promotion.

```http
GET /openapi/v1/bots/bot_123/genome/refs
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "refs": [
    {"name": "active", "revision": "sha256:a90b…", "seq": 41, "moved_at": "2026-10-01T09:30:00Z",
     "moved_by": {"kind": "user", "actor": "user_owner_1"}, "reason": "approved after review of run_6c2"},
    {"name": "previous", "revision": "sha256:5d02…", "seq": 40, "moved_at": "2026-10-01T09:30:00Z",
     "moved_by": {"kind": "user", "actor": "user_owner_1"}, "reason": "approved after review of run_6c2"},
    {"name": "candidate/run_7f3/1", "revision": "sha256:7c1e…", "seq": 42, "moved_at": "2026-10-08T03:12:00Z",
     "moved_by": {"kind": "strategy_run", "actor": "clawevolve/bot-evolution@2.0.0", "run_id": "run_7f3"}, "reason": null}
  ]
}
```

### PUT /bots/{bot}/genome/refs/draft

Move the owner's `draft` ref, with compare-and-swap. Called by the owner via
UI/CLI. (`active`, `previous`, `canary` are not movable here; they move only
through `POST /bots/{bot}/genome/promotions` in
[08-promotion.md](08-promotion.md).)

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request
{"revision": "sha256:7c1e…", "expected_revision": "sha256:a90b…"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200
{"name": "draft", "revision": "sha256:7c1e…", "seq": 42, "moved_at": "2026-10-08T10:02:00Z",
 "moved_by": {"kind": "user", "actor": "user_owner_1"}, "reason": null}
```

Errors: 409 `ref_conflict` with the current revision in the body (CLI exit
code 4); 404 unknown revision.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 409
{"error": "ref_conflict", "ref": "draft", "expected_revision": "sha256:a90b…", "current_revision": "sha256:3e61…"}
```

### GET /bots/{bot}/genome/content/{digest}

Fetch bytes by digest. Access-checked against the bot. Called by UI, CLI
(`avn genome content get`), strategies' workspace materialisation (through
Evolution Run), Verification.

```http
GET /openapi/v1/bots/bot_123/genome/content/sha256:3f9a…
Accept: application/octet-stream
```

```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream

# Soul
You are the support agent for …
```

Errors: 404 unknown digest or digest not reachable from this bot.

### PUT /bots/{bot}/genome/content

Upload produced content; returns its digest. Used before submitting a patch
that references a large file by digest. Called by Evolution Run (on behalf of
strategies), CLI (`avn genome content put`).

```http
PUT /openapi/v1/bots/bot_123/genome/content
Content-Type: application/octet-stream

import sys
def lookup(order_id): ...
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 201 (200 if the bytes already exist)
{"digest": "sha256:6b1d…", "size": 1834}
```

Errors: 413 over the Manifest file size limit.

### Related endpoint owned elsewhere

`POST /bots/{bot}/genome/promotions` moves `active` (including going back to
an earlier revision). It lives under the genome path but is specified in
[08-promotion.md](08-promotion.md).

### Internal

- In-process `GenomeRegistry` (§11) for Promotion and the `/config-manifest`
  compatibility layer in Backend.
- Engine memory projection contract (§7), a Plugin API implemented by engine
  adapters (RSI-05).

## 13. Examples

### 13.1 An owner edits a bot and goes back

```python
# Illustrative only: generated client SDK (see 09-evolution-api.md)
from avernet_evolution import Client, RefConflict

c = Client.from_env()
active = c.genome.get(bot="bot_123", rev="active")            # r41

patch = {
    "patch_schema": 1, "base": active.revision.id,
    "ops": [{"op": "file.edit", "target": "persona/SOUL.md",
             "edits": [{"kind": "insert_after", "anchor": "## Tone",
                        "content": "Confirm the order id before quoting amounts."}]}],
    "rationale": "Owner edit after a customer complaint.", "evidence": [],
}
rev = c.genome.record(bot="bot_123", base=active.revision.id, patch=patch, status="draft",
                      idempotency_key="owner-edit-2026-10-08-01")
try:
    c.genome.refs.set_draft(bot="bot_123", revision=rev.id, expected_revision=active.revision.id)
except RefConflict as e:
    print("someone else moved draft to", e.current_revision)   # re-read and decide

print(c.genome.diff(bot="bot_123", rev=rev.id, against=active.revision.id).changes)

# Going back later is just promoting the earlier revision (endpoint in 08-promotion.md)
c.genome.promote(bot="bot_123", revision="r41", reason="r42 increased escalations")
```

The same through the CLI:

```text
avn genome show bot_123 --rev active --output json
avn genome patch apply bot_123 --base r41 --file edit.json --dry-run
avn genome diff bot_123 r42 --against r41
avn genome refs bot_123
avn genome promote bot_123 --revision r41 --reason "r42 increased escalations"
avn genome export bot_123 --format git ./bot_123-history
```

### 13.2 Evolution Run records a candidate (platform side)

```python
# Illustrative only: inside apps/evolution, handling ctx.candidates.submit for run_7f3
async def submit_candidate(run: Run, candidate: Candidate) -> str:
    rev = await genome_api.record_revision(
        bot=run.bot_id, base=candidate.patch["base"], status="candidate",
        run={"run_id": run.id, "binding_id": run.binding_id, "allowed_genes": run.allowed_genes},
        patch=candidate.patch,
        idempotency_key=f"{run.id}/candidate/{candidate.patch_digest}",
    )
    # rev.patch_digest == candidate id ("sha256:c41e…"); a retry returns the same revision
    await verification.enqueue(candidate_id=rev.patch_digest, revision=rev.id, profile=run.profile)
    return rev.patch_digest
```

The strategy itself never sees this: it calls `ctx.candidates.submit(...)`
and gets the candidate id back ([03-strategy.md](03-strategy.md)).

### 13.3 Promotion applies a revision (Backend, in process)

```python
# Illustrative only: inside Backend promotion, after the gate accepted r42
compiled = registry.compile("bot_123", "sha256:7c1e…")
registry.move_ref("bot_123", RefUpdate(name="previous", revision=current_active,
                  expected_revision=current_previous, reason=reason), actor=actor)
registry.move_ref("bot_123", RefUpdate(name="active", revision=compiled.revision_id,
                  expected_revision=current_active, reason=reason), actor=actor)
apply_service.apply(bot_id="bot_123", document=compiled.document,
                    revision_id=compiled.revision_id, document_digest=compiled.document_digest)
```

The ordering, failure handling, and service-bot publish path are owned by
[08-promotion.md](08-promotion.md).

## 14. Interactions

| Other component / service | Direction | What flows |
| --- | --- | --- |
| Bot Config Manifest (existing) | both | Revisions recorded from Manifest documents; compiled revisions applied through Manifest apply; `/config-manifest` becomes a view over `active`; apply reports record `revision_id` |
| Manifest content store (existing) | Genome → store | Bytes by digest; new write path for produced content |
| Skill Center (existing) | Genome → Center | Pinned Center versions resolved at compile/apply |
| [Experience](02-experience.md) | Experience → Genome (by id) | Every episode and feedback record carries the revision id that produced it |
| [Strategy](03-strategy.md) | via Evolution Run | Strategies read the parent revision (`ctx.parent`), materialise and diff workspaces, submit patches as candidates |
| [Experiment Ledger](05-experiment-ledger.md) | Genome → Ledger | Revision records, ref-move events, lineage for the archive view of the ledger and audit |
| [Evolution Run](06-evolution-run.md) | Run → Genome | Resolve binding `parent`; record candidates with `allowed_genes`; upload produced content; binding checks against `policy` |
| [Verification](07-verification.md) | Verification → Genome | Compile/materialise parent and candidate into eval bots; link evaluations to revisions |
| [Promotion](08-promotion.md) | Promotion → Genome | Move `active`/`previous`/`canary` by CAS; set status; compile and apply; record `revision_id` on publish records |
| [Evolution API](09-evolution-api.md) | clients → Genome | Public genome endpoints, SDK, `avn genome` |
| [Meta-evolution](10-meta-evolution.md) | later | Same revision/ref/patch machinery for mechanism revisions |
| Engine adapter | Genome → Engine | Memory projection (`project_memory`, `export_memory`); physical layout |
| BCS | future | A team genome would reference member revisions |

## 15. Open decisions

| ID | Decision | Notes |
| --- | --- | --- |
| D-6 | Genome storage: DB revisions + content store (recommended) vs git repository per bot | §9.2; RSI-03 |
| G-1 | How a directory resource (`path: data/kb/` from git) is stored today: one archive blob or one blob per file | Decides how fine-grained `file.edit` patches on resources can be; must be confirmed before RSI-02 fixes the patch schema |
| G-2 | Full patch op set and whether ops map onto JSON Patch (RFC 6902) | RSI-02 |
| G-3 | Locked-gene defaults | RSI-02 "done when": reviewers agree on locked-gene defaults |
| G-4 | `engine_config` key allowlist | P3/P4 |
| G-5 | Engine memory projection contract, and the engine owners' sign-off on amending the reserved-file rule | RSI-05; DR-1 consequence; interim `LESSONS.md` |
| G-6 | Lineage when a patch produces content identical to an existing revision (same id, different parent path) | Proposed: return the existing revision, record the extra derivation as a Ledger event |
| G-7 | Whether `risk_overrides` may lower a tier | Proposed: raise only |
| G-8 | Blob retention sweep for the archive | v1 unconditional; any sweep must keep blobs reachable from any revision, never those of `promoted` revisions |
| G-9 | Candidate id vs revision id | Proposed: keep both identities. The candidate id is the content hash of the stored patch (§6.5); the recorded candidate revision id is the content hash of `{spec, policy}` (§4.4). Both appear in the verdict ([07-verification.md](07-verification.md)) and in ledger entries. Alternative: use only the revision id as the handle |
| G-10 | `memory.update` op | Proposed in §6.1 for memory consolidation; tier T1 like `memory.add` / `memory.retire`. Alternative: express an update as retire + add |

Related: DR-1 must be accepted (RSI-01) before RSI-02 (schema and patch
format), RSI-03 (Registry in Backend), and RSI-04 (Manifest v2 compatibility
layer) start; see [work-items.md](work-items.md).
