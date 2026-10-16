# Genome

> 中文版：[01-genome.zh-CN.md](01-genome.zh-CN.md)

> Status: DRAFT. Component in the [bot evolution architecture](design.md).
> This doc covers the Bot Genome: immutable, versioned revisions of a bot's
> definition, the refs that point at them, the patches that change them, and
> the Genome Registry that stores and serves them.

Accepted decision: [DR-1](../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md).
Builds on the Bot Config Manifest (`apps/backend/docs/bot-config-manifest/`).

## 1. Purpose and scope

A **Bot Genome** is the evolvable, declarative definition of one bot: persona
files, skills, curated memory, resources, tools, engine config, and a
non-evolvable `policy` section. A **Genome Revision** is one recorded,
immutable version of it. The Genome is the unit of evolution: every strategy
reads a revision and proposes a change to it, and every change that reaches a
running bot through the genome is a revision being applied.

In one sentence: a Genome Revision is **a complete, pinned Manifest, plus
curated memory, plus a parent pointer and a locked policy, changed only
through patches**.

The **Genome Registry** is the component that implements this. It owns:

- Genome Revisions (content, parent, provenance, status);
- named **refs** (`active`, `previous`, `canary`, `draft`, `candidate/<run>/<n>`)
  and their compare-and-swap updates;
- **Genome Patches**: their schema, applying them to a base revision, and
  the structural checks run when one is recorded (it applies cleanly to its
  base and stays inside the policy, §6.2). Whether a change is *good* is not
  checked here: that is [Verification](07-verification.md);
- the **genome policy** (locked genes, mutable genes, pins, risk overrides)
  and its structural enforcement when a patch is recorded;
- content addressing: file bytes referenced by digest in the existing
  manifest content store;
- **compiling** a revision into a pinned Manifest document, which is what
  apply and eval bots consume. Curated memory travels in the document's new
  `memory` category; at apply, the engine adapter writes those items into
  the engine's own memory format (for OpenClaw, its memory files). This
  hand-off is the *memory projection*; see §7;
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
| Physical projection of a revision onto an engine workspace, runtime memory | Engine adapter (`engine/adapter/`); Backend must not know engine paths, per the repository rule in `AGENTS.md` ("Physical layout ownership belongs to Engine Runtime and its versioned layout contract"; ADR 0014 is one application of it) |

**Placement.** Backend, as a new core package `core/bot_genome/` next to
`core/bot_config_manifest/`, because Backend owns desired state and the
Manifest already lives there. Content reuses the manifest content store
(`core/bot_config_manifest/content/service.py`). Promotion sits beside it in
Backend because promotion must sit beside apply.

**Layering.** The genome sits on top of the Manifest. The Manifest is not
revision-aware: it gains only format features and one fact of its own, the
digest of the document each apply applied (§8). Which revision a digest
belongs to is known only to the Genome Registry. A direct Manifest edit
bypasses the genome and is detected as drift (§5.5).

**Useful on its own.** The Genome Registry is phase P1: versioned bots,
attributable apply, and going back to any earlier revision are useful
independently of any evolution strategy.

## 2. Domain model

![Genome domain model: a bot's revisions, their spec and policy, patches, refs, and the compiled manifest](images/genome-model.svg)

How the types relate:

- A bot has many **revisions**. Each revision holds one `spec` (the
  evolvable content) and one `policy` (the owner's rules), plus metadata the
  platform writes.
- Every revision except the bot's first has exactly one **parent**. A
  **patch**, a list of ops, is what turns the parent into the new revision.
  An owner can also record a revision from a whole Manifest document, with
  no patch.
- **Refs** are names (`active`, `draft`, …) that point at revisions.
- **Compiling** a revision gives the `CompiledManifest` that apply and eval
  bots consume.
- File content is never stored inline: every file in `spec` is a **digest**,
  the address of its bytes in the content store.

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `GenomeRevision` | One immutable version of a bot's definition: `spec` + `policy`, plus platform-written metadata (`revision`) | Genome Registry | Recorded once (from a patch or a Manifest document), never edited; only its `status` and annotations change; never deleted |
| `GenomeSpec` | The evolvable content of a revision: persona, skills, memory, resources, tools, engine config, script | Genome Registry (authored by owners, proposed by strategies) | Part of a revision; hashed |
| `GenomePolicy` | The non-evolvable section: locked genes, mutable genes, pins, risk overrides | Bot owner / tenant admin | Part of a revision; hashed; copied forward verbatim by the platform; no patch may touch it |
| `RevisionMetadata` | Id, per-bot `seq`, parent, provenance, status, evidence and evaluation ids, annotations | Genome Registry (platform-written) | Written at record time; status/annotations updated later; not hashed |
| `Provenance` | Who or what created a revision (a person, a strategy run, later a bot) | Genome Registry | Written once at record time |
| `GenomeRef` | A named, movable pointer to a revision (`active`, `previous`, `canary`, `draft`, `candidate/<run>/<n>`) | Genome Registry; each ref has exactly one kind of mover | Moved by compare-and-swap; every move is an audited event |
| `GenomePatch` | A typed, itemized change against a named base revision; the only thing a strategy may submit | Submitted by strategies (via [Evolution Run](06-evolution-run.md)) or humans; checked by the Registry | Stored content-addressed; its digest is the candidate id |
| `PatchOp` | One item of a patch (`file.edit`, `skill.add`, `memory.add`, …) | Part of a patch | Each op maps to a risk tier |
| `MemoryItem` | One curated memory fact/rule/lesson, keyed | Genome (curated memory) | Added, updated, and retired by patch ops |
| `CompiledManifest` | A revision rendered as a plain Manifest document (pinned, every category present), plus the revision id and the document's digest for bookkeeping | Genome Registry (derived, not stored as truth) | Produced on demand for apply and eval bots |

### 2.1 Genes

A **gene** is a named, addressable part of `spec` that the policy and
bindings can refer to: a category (`persona`, `skills`, `memory`,
`resources`, `tools.mcp`, `tools.cli_tools`, `engine_config`, `script`), a
key inside one (`engine_config.reasoning_effort`), or one item
(`skills.refund-policy`). The gene path grammar is fixed by RSI-02.

### 2.2 GenomeSpec

Shared aliases first; every type below uses them.

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

RevisionId = str   # identity of a revision: "sha256:" + 64 hex chars, the hash of canonical({spec, policy}) (§4.4).
                   # Example: "sha256:7c1e…" (shortened here and below)
Digest = str       # address of stored bytes: "sha256:" + 64 hex chars of the bytes themselves.
                   # Example: "sha256:3f9a…" is the address of one SOUL.md file;
                   # GET …/genome/content/sha256:3f9a… returns "# Soul\nYou are a patient support agent…" (§9.1).
                   # One store and one endpoint serve every digest below (persona file, skill, memory items,
                   # resource, MCP config, CLI tool, script); the field that holds a digest says what the bytes are.
# RiskTier (T0 < T1 < T2 < T3) is defined once in 08-promotion.md §5.
# BotRef {owner_id, bot_id}: the bot's full identity (a bot_id alone is not unique
# across users); defined once in 09-evolution-api.md §2.6.
```

Persona files and skills:

```python
class PersonaFileType(StrEnum):
    """The persona files a bot may have: VALID_IDENTITY_FILES in core/services/identity.py,
    minus MEMORY.md and IDENTITY.md (reserved, §7), plus the proposed platform-managed
    LESSONS.md (§7 interim)."""
    SOUL = "SOUL.md"
    AGENTS = "AGENTS.md"
    RULES = "RULES.md"
    OKR = "OKR.md"
    SAFETY = "SAFETY.md"
    OUTPUT = "OUTPUT.md"
    USER = "USER.md"
    TOOLS = "TOOLS.md"
    HEARTBEAT = "HEARTBEAT.md"
    BOOTSTRAP = "BOOTSTRAP.md"
    KNOWLEDGE = "KNOWLEDGE.md"
    CLAUDE = "CLAUDE.md"
    GREETING = "GREETING.md"
    README = "README.md"
    LESSONS = "LESSONS.md"

@dataclass(frozen=True)
class PersonaFile:
    type: PersonaFileType            # which persona file, e.g. PersonaFileType.SOUL ("SOUL.md")
    digest: Digest                   # where its bytes are, e.g. "sha256:3f9a…"

class SkillOriginKind(StrEnum):
    CENTER = "center"                # a governed skill from Skill Center: pinned by version, not copied
    LOCAL = "local"                  # a bot-owned skill (uploaded, or created by a patch): stored by digest
    GIT = "git"                      # fetched from a git source when the revision was recorded: stored by digest
    OSS = "oss"                      # fetched from object storage when the revision was recorded: stored by digest

@dataclass(frozen=True)
class SkillOrigin:
    kind: SkillOriginKind
    version: str | None = None       # CENTER only: the pinned version, e.g. "center://refund-policy@v7"

@dataclass(frozen=True)
class SkillEntry:
    name: str                        # the skill's name, e.g. "refund-policy"
    origin: SkillOrigin
    digest: Digest | None = None     # where the skill's stored files are, e.g. "sha256:0c55…" for the bot-owned
                                     # "quality-check" skill; None for CENTER skills, which Skill Center resolves by version
```

Curated memory:

```python
class EvidenceKind(StrEnum):
    """The Experience records a change can cite (02-experience.md)."""
    EPISODE = "episode"              # one recorded session or trajectory of the bot, id "ep_…"
    FEEDBACK = "feedback"            # one feedback record, id "fb_…": a rating, a correction, or a producer's
                                     # finding such as a ClawInsight improvement item
    EVAL = "eval"                    # one evaluation rollout trace, id "et_…"

EvidenceRef = str  # "<EvidenceKind>:<id>", e.g. "episode:ep_91", "feedback:fb_311". Each one names exactly one
                   # Experience record, which GET …/experience/episodes/ep_91 (or …/feedback/fb_311) returns.
                   # It is an id to look up, not a URL.

@dataclass(frozen=True)
class MemoryItem:
    key: str                         # stable name of the item, e.g. "returns-window"; updates and retires address it
    text: str                        # the fact, rule, or lesson itself, e.g. "Returns are accepted within 30 days."
    tags: list[str]                  # free-form topic labels, e.g. ["returns"]
    source: list[EvidenceRef]        # what the item was learned from, e.g. ["episode:ep_97"]

class MemoryMode(StrEnum):
    """How apply writes the curated items into the engine's memory (§7). Besides curated items, the
    engine's memory holds what the bot wrote itself while running ("runtime items")."""
    SEED = "seed"                    # write each item only if the engine has no item with that key yet;
                                     # anything already there, including runtime items, is kept as it is
    MERGE = "merge"                  # write every item, overwriting an existing item with the same key;
                                     # runtime items with other keys are kept
    REPLACE = "replace"              # the engine's memory becomes exactly the curated items; runtime items are
                                     # dropped (T3: needs human approval)

@dataclass(frozen=True)
class MemorySpec:
    mode: MemoryMode
    items_digest: Digest             # where the stored list of MemoryItem is, e.g. "sha256:9e20…"
```

The stored list that `items_digest` points at, for a support bot:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
[
  {"key": "returns-window",                       // stable name; memory.update / memory.retire address it
   "text": "Returns are accepted within 30 days of delivery.",
   "tags": ["returns"],
   "source": ["episode:ep_97", "feedback:fb_204"]},   // the sessions and feedback it was learned from
  {"key": "customer-tier-rules",
   "text": "Gold customers get free return shipping.",
   "tags": ["billing", "returns"],
   "source": ["episode:ep_91"]}
]
```

An example of the modes. The curated set has `returns-window` = "Returns
are accepted within 30 days." While running, the bot wrote its own
`returns-window` = "Returns are accepted within 14 days." and a new item
`vip-contact`. On the next apply:

| Mode | `returns-window` afterwards | `vip-contact` afterwards |
| --- | --- | --- |
| `seed` | "14 days" (the key existed, so the bot's own item stays) | kept |
| `merge` | "30 days" (the curated item overwrites the key) | kept |
| `replace` | "30 days" | removed |

Resources, tools, and the whole spec:

```python
@dataclass(frozen=True)
class ResourceEntry:
    path: str                        # where the resource appears in the bot's workspace, e.g. "data/kb/"
    digest: Digest                   # where its stored bytes are (how a directory is stored: open decision G-1)

@dataclass(frozen=True)
class McpServerEntry:
    server_code: str                 # the MCP server's registered code, e.g. "mcp.x.meet"
    digest: Digest                   # where this bot's stored config for that server is

@dataclass(frozen=True)
class CliToolEntry:
    name: str                        # e.g. "shopctl"
    digest: Digest                   # where the tool's stored package is
    version: str                     # the tool's own version, e.g. "2.3.0"

@dataclass(frozen=True)
class ToolsSpec:
    mcp: list[McpServerEntry]
    cli_tools: list[CliToolEntry]

@dataclass(frozen=True)
class ScriptEntry:
    digest: Digest                   # where the bot's startup script is; locked by default (§4.5)

@dataclass(frozen=True)
class GenomeSpec:
    persona: list[PersonaFile]
    skills: list[SkillEntry]
    memory: MemorySpec | None        # None until RSI-05 lands (§7)
    resources: list[ResourceEntry]
    tools: ToolsSpec
    engine_config: dict[str, str]    # allowlisted key -> value, e.g. {"reasoning_effort": "medium"};
                                     # values are strings because hashed content has no floats (§9.3)
    script: ScriptEntry | None       # None when the bot has no startup script
```

Every category is present in every revision. An empty list means "none";
it never means "leave as it was" (totality, §4.3).

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The normative schema is work item RSI-02.
{
  "persona": [                                    // == manifest.identity, but pinned
    {"type": "SOUL.md", "digest": "sha256:3f9a…"},
    {"type": "AGENTS.md", "digest": "sha256:81bd…"}
  ],
  "skills": [
    {"name": "refund-policy", "origin": {"kind": "center", "version": "center://refund-policy@v7"}},  // pinned Center version, not copied
    {"name": "quality-check", "digest": "sha256:0c55…", "origin": {"kind": "local"}}                  // bot-owned: stored, referenced by digest
  ],
  "memory": {                                     // new category, see §7
    "mode": "seed",                               // seed | merge | replace (MemoryMode)
    "items_digest": "sha256:9e20…"                // the stored list of memory items
  },
  "resources": [{"path": "data/kb/", "digest": "sha256:d4a7…"}],
  "tools": {
    "mcp": [{"server_code": "mcp.x.meet", "digest": "sha256:52c8…"}],
    "cli_tools": [{"name": "shopctl", "digest": "sha256:a613…", "version": "2.3.0"}]
  },
  "engine_config": {                              // allowlisted keys only; no floats in hashed content
    "model": "provider/model-x",
    "reasoning_effort": "medium"
  },
  "script": {"digest": "sha256:e1f0…"}            // carried, but LOCKED for evolution by default
}
```

### 2.3 GenomePolicy

The policy says what automated actors may change on this bot. It is owned by
the bot owner or tenant admin, never by a strategy, and the platform rejects
any patch that touches it.

**Why a policy.** The policy holds the owner's rules that bound evolution of
*this* bot: which genes are locked, which are mutable, which items are
pinned, and which genes get a higher risk tier. It lives inside the revision
and is part of its hash, so every revision records the rules it was made and
verified under; looking at an old revision tells you what automated actors
were allowed to change at that time. Strategies can read it (to avoid
proposing changes that would be rejected), but no patch can change it: only
the owner can, through a normal edit that records a new revision (§4.4). An
automated actor therefore cannot loosen its own limits. Example: on a
customer-facing support bot, the owner pins `persona.RULES.md` (the refund
and escalation rules legal signed off on) and keeps `tools.mcp` and
`tools.cli_tools` locked, so a nightly strategy may improve tone in
`SOUL.md` and add skills, but can never rewrite the rules or give the bot a
new tool.

```python
@dataclass(frozen=True)
class GenomePolicy:
    locked_genes: list[str]          # gene paths (§2.1) no patch may change; default includes "script", "tools.mcp", "policy"
    mutable_genes: list[str]         # gene paths (§2.1) a patch may touch; everything else is off limits
    pins: list[str]                  # single items inside a mutable gene that stay write-protected, e.g. "skills.refund-policy"
    risk_overrides: dict[str, RiskTier]   # gene path -> tier to use instead of the default; may only RAISE a tier (proposed)
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
- **At binding configuration time**: a **binding** attaches one evolution
  strategy to the bot: which strategy, when it runs, which genes it may
  change (`allowed_genes`), and its budget. A bot has a list of them (its
  bindings), e.g. ClawEvolve nightly on persona and skills. A
  binding's `allowed_genes` must stay within the bot's policy (locked genes
  stay locked); a mismatch is rejected before any paid run starts. Bindings
  are defined in [06-evolution-run.md](06-evolution-run.md).
- **At candidate submission**: Promotion's static floor (`FloorCheck`)
  re-checks "no locked gene or pinned item touched" before any verification
  is spent; see [08-promotion.md](08-promotion.md).

This is where "tool and permission changes are human-only" is enforced
**structurally** rather than by convention: tool changes live in locked
genes by default, and the policy itself is outside what a patch can reach.
Changing the policy is an owner action that records a new revision (§4.4).

### 2.4 GenomeRevision

```python
class ProvenanceKind(StrEnum):
    USER = "user"                    # a person edited the bot through the genome API (UI or API);
                                     # a direct PUT /config-manifest records no revision (§5.5)
    STRATEGY_RUN = "strategy_run"    # a strategy submitted it as a candidate during an Evolution Run
    BOT = "bot"                      # postponed with DR-3

@dataclass(frozen=True)
class Provenance:
    kind: ProvenanceKind
    actor: str                       # who: a user id ("user_owner_1"), or a strategy "id@version"
                                     # ("clawevolve/bot-evolution@2.0.0")
    run_id: str | None = None        # STRATEGY_RUN only: the Evolution Run that produced it, e.g. "run_7f3"
    step: str | None = None          # STRATEGY_RUN only: the strategy's own label for the step, e.g. "propose"

EvaluationId = str  # id of one Verification evaluation, e.g. "ev_301";
                    # GET …/evolution/evaluations/ev_301 returns it with its scores (07-verification.md)

class RevisionStatus(StrEnum):
    """Meaning of each status and who sets it: §4.4."""
    DRAFT = "draft"
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    ARCHIVED = "archived"

@dataclass
class RevisionMetadata:
    id: RevisionId                   # the revision's identity: hash of {spec, policy} (§4.4)
    seq: int                         # per-bot counter for people: the bot's 1st recorded revision is 1 ("r1"), the
                                     # 42nd is 42 ("r42"). UIs show it, and paths accept it
                                     # (GET …/revisions/r42), but it is not an identity: other records store `id`
    bot: BotRef                      # the bot this revision belongs to (owner + bot id)
    parent: RevisionId | None        # the revision this one was made from: the patch's `base`, or the `base` given
                                     # when a whole document was recorded; None only for the bot's first revision
    created_by: Provenance
    created_at: datetime
    status: RevisionStatus
    patch_digest: Digest | None      # digest of the stored GenomePatch that turned `parent` into this revision
                                     # (= the candidate id, §6.5); None when the revision was recorded from a whole
                                     # Manifest document instead of a patch (§4.1)
    evidence: list[EvidenceRef]      # the Experience records that motivated the change, e.g.
                                     # ["episode:ep_91", "feedback:fb_311"]; [] for most manual edits
    evaluations: list[EvaluationId]  # the Verification evaluations run on this revision, e.g. ["ev_301", "ev_302"];
                                     # ids only: scores stay in Verification
    annotations: dict[str, str] = field(default_factory=dict)
                                     # free-form labels people or the platform add after recording, e.g.
                                     # {"experiment": "escalation-wording", "review_note": "checked by legal"};
                                     # not hashed, so they change without creating a new revision

@dataclass(frozen=True)
class GenomeRevision:
    genome_schema: int               # version of this record's format, so a reader knows how to parse it: 1 today;
                                     # a breaking change to the shape of spec or policy bumps it (RSI-02)
    revision: RevisionMetadata       # platform-written, excluded from the hash
    spec: GenomeSpec
    policy: GenomePolicy
```

A **run id** (`run_7f3`) is the id of one Evolution Run: one execution of
one binding for one bot. Evolution Run assigns it when the run is
submitted. It is globally unique and opaque. It is not a counter per
strategy or per bot ([06-evolution-run.md](06-evolution-run.md)).

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// `spec` and `policy` are shortened; full examples are in §2.2 and §2.3.
{
  "genome_schema": 1,                             // format version of this record
  "revision": {                                   // computed / platform-written, not authored
    "id": "sha256:7c1e…",                         // hash of canonical({spec, policy}): the identity
    "seq": 42,                                    // the bot's 42nd revision, shown as "r42"; not an identity
    "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},   // owner + bot id; a bot id alone is not unique
    "parent": "sha256:a90b…",                     // r41, the patch's base
    "created_by": {"kind": "strategy_run", "run_id": "run_7f3", "step": "propose",
                   "actor": "clawevolve/bot-evolution@2.0.0"},
    "created_at": "2026-10-08T03:12:00Z",
    "status": "candidate",
    "patch_digest": "sha256:c41e…",               // the stored Genome Patch that produced it = candidate id
    "evidence": ["episode:ep_91", "episode:ep_97", "feedback:fb_311"],
    "evaluations": ["ev_301", "ev_302"],          // Verification evaluation ids, not embedded scores
    "annotations": {"experiment": "escalation-wording"}
  },
  "spec": {"persona": [{"type": "SOUL.md", "digest": "sha256:3f9a…"}], "skills": [], "memory": null,
           "resources": [], "tools": {"mcp": [], "cli_tools": []},
           "engine_config": {"reasoning_effort": "medium"}, "script": null},
  "policy": {"locked_genes": ["script", "tools.mcp", "tools.cli_tools", "policy"],
             "mutable_genes": ["persona", "skills", "memory"], "pins": [], "risk_overrides": {}}
}
```

### 2.5 GenomeRef

```python
class FixedRefName(StrEnum):
    """The refs every bot can have, at most one of each (§5.1, §5.2)."""
    ACTIVE = "active"
    PREVIOUS = "previous"
    CANARY = "canary"
    DRAFT = "draft"

@dataclass(frozen=True)
class CandidateRefName:
    """The ref of one submitted candidate; serialized as "candidate/<run_id>/<n>" (§5.2)."""
    run_id: str                      # the Evolution Run that submitted it, e.g. "run_7f3"
    n: int                           # 1 for the run's first candidate, 2 for the second, …

RefName = FixedRefName | CandidateRefName   # on the wire: "active" or "candidate/run_7f3/1"; any other
                                            # string fails to parse, so no free-form names exist

@dataclass(frozen=True)
class GenomeRef:
    name: RefName
    revision: RevisionId             # the revision it points at now
    moved_at: datetime
    moved_by: Provenance
    reason: str | None = None        # required for active/previous/canary moves

@dataclass(frozen=True)
class RefUpdate:
    name: RefName
    revision: RevisionId             # where the ref should point
    expected_revision: RevisionId | None   # compare-and-swap: where the caller believes it points now;
                                           # None only when the ref must not exist yet (§5.3)
    reason: str | None = None
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "name": "active",
  "revision": "sha256:a90b…",
  "moved_at": "2026-10-01T09:30:00Z",
  "moved_by": {"kind": "user", "actor": "user_owner_1"},
  "reason": "approved after review of run_6c2"
}
```

### 2.6 GenomePatch and PatchOp

```python
class EditKind(StrEnum):             # full set fixed by RSI-02
    REPLACE_SECTION = "replace_section"   # replace everything under a Markdown heading; the heading line stays
    INSERT_AFTER = "insert_after"         # insert text directly below one exact line

@dataclass(frozen=True)
class TextEdit:
    kind: EditKind
    content: str                     # the new text to write
    heading: str | None = None       # REPLACE_SECTION only: the heading line, e.g. "## Escalation"
    anchor: str | None = None        # INSERT_AFTER only: the line to insert below, e.g. "## Tone"

@dataclass(frozen=True)
class DigestRef:
    digest: Digest                   # a file uploaded first with PUT …/genome/content

@dataclass(frozen=True)
class FileDiff:
    path: str                        # file inside the skill, e.g. "SKILL.md"
    diff: str                        # the change to that file in unified diff format (what `git diff` prints)

@dataclass(frozen=True)
class FileEdit:
    """Op "file.edit": structured edits to one text file (persona md, SKILL.md, resource text)."""
    target: str                      # the file, e.g. "persona/SOUL.md", "skills/refund-policy/SKILL.md"
    edits: list[TextEdit]

@dataclass(frozen=True)
class SkillAdd:
    """Op "skill.add": a new bot-owned skill."""
    name: str                        # the new skill's name, e.g. "invoice-lookup"
    files: dict[str, str | DigestRef]   # path in the skill -> small text inline, or a larger file uploaded first

@dataclass(frozen=True)
class SkillUpdate:
    """Op "skill.update": changes files of an existing bot-owned skill."""
    name: str                        # the skill, e.g. "refund-policy"
    file_diffs: list[FileDiff]

@dataclass(frozen=True)
class MemoryAdd:
    """Op "memory.add": a new curated memory item."""
    item: MemoryItem

@dataclass(frozen=True)
class MemoryUpdate:
    """Op "memory.update" (proposed): replaces the text, tags, and source of an existing key."""
    item: MemoryItem

@dataclass(frozen=True)
class MemoryRetire:
    """Op "memory.retire": retires an item; it stays in history."""
    key: str                         # the item to retire

@dataclass(frozen=True)
class EngineConfigSet:
    """Op "engine_config.set": one allowlisted engine config key."""
    key: str                         # e.g. "reasoning_effort" (allowlist: open decision G-4)
    value: str                       # always a string, e.g. "high" (no floats in hashed content)

PatchOp = FileEdit | SkillAdd | SkillUpdate | MemoryAdd | MemoryUpdate | MemoryRetire | EngineConfigSet

@dataclass(frozen=True)
class GenomePatch:
    patch_schema: int                # schema version of the patch format; 1
    base: RevisionId                 # the revision the patch was written against; becomes the new revision's parent
    ops: list[PatchOp]
    rationale: str                   # required; shown to reviewers
    evidence: list[EvidenceRef]      # required for non-trivial ops
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch_schema": 1,
  "base": "sha256:a90b…",                               // r41; becomes the parent
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
     "file_diffs": [{"path": "SKILL.md", "diff": "@@ -12,3 +12,4 @@ partial refunds…"}]},
    {"op": "memory.add",                                  // itemized memory, see §7
     "item": {"key": "customer-tier-rules", "text": "Gold customers get free return shipping.", "tags": ["billing"], "source": ["episode:ep_91"]}},
    {"op": "memory.update",                               // proposed op, see §6.1
     "item": {"key": "returns-window", "text": "Returns are accepted within 30 days.", "tags": ["returns"], "source": ["episode:ep_97"]}},
    {"op": "memory.retire", "key": "old-shipping-sla"},
    {"op": "engine_config.set", "key": "reasoning_effort", "value": "high"}
  ],
  "rationale": "Partial-refund requests were escalated too late in 6 of 9 failing episodes.",
  "evidence": ["feedback:fb_311", "episode:ep_91"]
}
```

This example shows every op kind at once. Against the §2.3 policy it would
be rejected, because `skills.refund-policy` is pinned there (§6.2); a real
patch only touches mutable, unpinned genes.

How the two `file.edit` kinds change a file, using the two edits in the
example above:

```text
Before (persona/SOUL.md)          After
------------------------          -----
## Tone                           ## Tone
Be friendly and brief.            Confirm the order id before quoting amounts.   <- insert_after "## Tone":
                                  Be friendly and brief.                            new line directly below the anchor
## Escalation                     ## Escalation                                  <- replace_section "## Escalation":
Escalate when the customer        Escalate to a human when a refund exceeds        heading kept, everything under it
asks for a manager.               the policy limit…                                 up to the next heading of the same
Always apologise first.                                                             or higher level is replaced
## Closing                        ## Closing
```

`skill.update` instead carries one unified diff per changed file: the
standard text diff format that `git diff` and `diff -u` print, with `@@`
hunk headers, `-` for removed lines and `+` for added lines.

### 2.7 CompiledManifest

```python
@dataclass(frozen=True)
class CompiledManifest:
    revision_id: RevisionId          # bookkeeping: which revision this was compiled from (not inside the document)
    document: dict                   # a plain Manifest document, exactly what apply consumes (shape below)
    document_digest: Digest          # digest of that document; apply reports record it (§8 item 5), and the
                                     # Registry maps it back to `revision_id`
```

`document` is just a Manifest document. Compiling adds nothing to the
Manifest format beyond the Manifest changes listed in §8: every category is
present, file entries point at the content store by digest (§8 item 1), and
curated memory is the Manifest's new `memory` category (§8 item 3). The two
other fields are bookkeeping kept next to the document, not sent to apply.
When Promotion applies the document, the apply report records its digest
(§8 item 5), and the Registry records that this revision compiled to it, so
the digest maps back to the revision (§5.5).

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
      "cli_tools": [{"name": "shopctl", "source": {"content_store": "sha256:a613…"}}],
      "memory": {"mode": "seed", "items": {"source": {"content_store": "sha256:9e20…"}}}   // new category, §8 item 3
    }
  }
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
| No parent pointer | Lineage tree; descendant-aware selection | `parent` |
| No optimistic concurrency control. HTTP offers it through an `ETag` (a version tag the server returns with a document) and an `If-Match` request header (the client sends back the tag it read; the server rejects the write with `412 Precondition Failed` if the document changed since). `PUT /config-manifest` has neither | A strategy and a human editing at the same time silently overwrite each other; whoever writes last wins without knowing | Compare-and-swap on refs (§5) |
| Apply re-reads the current document; the report does not say which bytes were applied | Cannot attribute behaviour or experience to a version | Apply reports record the applied document digest; the Registry maps it to a revision (§8) |
| Sources are moving refs (git branch, OSS key) re-resolved on each apply | A genome must be immutable: every source pinned to commit SHA / digest | Pinned content (§4.2) |
| Whole-document PUT, category-atomic replace | Strategies must submit small, reviewable, itemized patches | Genome Patch (§6) |
| `MEMORY.md` / `IDENTITY.md` reserved and rejected | Learned memory is half of what self-improvement produces | Curated memory (§7) |
| `engine_config` is validated by the schema, but nothing applies it (moved out of the first wave; no materialiser) | Model / temperature / reasoning budget are legitimate tunables | A materialiser for an allowlist of `engine_config` keys (§8) |
| A category left out of the document means "leave it untouched" | A revision must fully determine the bot; otherwise two applies of the same revision can produce different bots depending on prior state | Totality (§4.3) |
| No place for metadata: unknown keys are rejected | Evolution needs **provenance** (who or what produced a version, from which evidence, with which patch) and **annotations** (free-form labels such as review notes or experiment tags) | Kept on the revision record, not inside the Manifest document, so the Manifest schema does not change for this |
| Personal bots have no way back to an earlier configuration; service bots have their own publish rollback (one step back) | Going back must work for every bot and to any earlier revision | Going back = promoting an earlier revision (§5.4) |

Rejected alternatives (from DR-1):

- **A separate evolution artifact (e.g. a ClawEvolve Pack) delivered by its
  own path.** Rejected: two sources of truth, and Packs are OpenClaw-specific
  workspace snapshots that cannot reach teclaw.
- **Version outside the Manifest and leave the Manifest entirely
  unchanged.** Rejected: without a content-store source and the applied
  document digest on the apply report, apply would still re-fetch moving
  sources and could not say which document it applied, so behaviour could
  not be attributed to a version. The genome does sit on top of the
  Manifest, but the Manifest gains those format features (§8).
- **Git repository per bot as source of truth.** Deferred; see §9.2.

## 4. Revisions

### 4.1 What a revision adds to the Manifest

Concretely, a revision adds exactly these things on top of the Manifest:

1. **Revision identity and lineage**: a content-hash id plus a readable
   per-bot sequence number, a parent, provenance, status, and named refs
   moved by compare-and-swap (§5).
2. **A non-evolvable `policy` section**: the owner's rules that bound
   evolution of this bot, recorded with every revision and changeable only by
   the owner (why: §2.3).
3. **Pinned content**: every source resolved to a commit SHA or content
   digest, so the same revision always yields the same bytes (§4.2).
4. **Totality**: every category is present in every revision (§4.3).
5. **Patch-only change for automated actors**: strategies submit typed,
   itemized patches against a named base revision (§6). Humans can still
   record a revision from a whole **Manifest document** (the full YAML
   document the Bot Config Manifest API accepts) instead of a patch, through
   the genome API (`POST …/genome/revisions` with `{manifest}`, §12). Such a
   revision has no `patch_digest`. A document sent directly to
   `PUT /config-manifest` records no revision (§5.5).
6. **One new content category, curated memory** (§7), which needs a new
   engine contract.

Deliberately **not** added:

- **Evaluation scores.** They live in [Verification](07-verification.md) and
  are only linked (`evaluations`); the archive view of the ledger in
  [05-experiment-ledger.md](05-experiment-ledger.md) joins them. The genome
  stays a pure definition.
- **`engine_config` as a genome-only addition.** The Manifest schema already
  has the category; applying an allowlisted subset is a Manifest change
  evolution benefits from (§8).

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
  humans: the bot's first recorded revision is `r1`, the next new one `r2`,
  and so on. Example: a strategy submits a candidate whose content is new,
  so it becomes `r42`; a retry of the same submission returns `r42` again
  instead of creating `r43`. It is never an identity and never used in
  hashes: two bots both have an `r42`, and other records store the `id`.
  `seq` is the order of recording, not ancestry: `r42`'s parent is
  whatever its base was, which can be `r39` (for example, a run that
  started from `r39` while it was `active`), while `r41`'s parent is `r38`.
- **Parent.** Every revision except the bot's first has exactly one
  `parent`: the base the patch was written against, or the `base` given
  when a whole document was recorded. Following `parent` links gives a bot's
  history as a tree (two candidates made from `r41` are siblings).
- **Status** is platform-written metadata, outside the hash:

| Status | Meaning (proposed) | Set by |
| --- | --- | --- |
| `draft` | Recorded from an owner's manual edit through the genome API (UI or API) | Genome Registry |
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

Refs are names, not identity. Every revision has its own identity, the
content-hash id (plus the per-bot `seq`, e.g. `r42`), whether or not any ref
points at it. A revision can have no ref, or several.

### 5.2 Each ref in detail

The running example: a support bot runs `r41`. A nightly run `run_7f3`
proposes two candidates, and the owner is also editing the bot by hand.

**`active`: what the bot runs.** Every apply (personal bot) and every
publish (service bot) made through the genome uses the revision `active`
points at (a direct Manifest edit is drift, §5.5), so it is the one
ref every bot has once it has been applied. Only Promotion moves it: when a
revision is promoted, whether newer or older ([08-promotion.md](08-promotion.md)).
*Example:* the gate accepts `r42` and the owner approves it; Promotion moves
`active` from `r41` to `r42` and applies `r42`.

**`previous`: one step back.** The revision `active` pointed at before its
last move. Promotion moves it in the same step as `active`, so going back
one step needs no lookup: promote `previous`. *Example:* after the move
above, `previous` = `r41`. If `r42` misbehaves, promoting `previous` makes
`active` = `r41` again, and `previous` becomes `r42`.

**`canary`: the trial revision on part of the fleet.** Only for
multi-instance (service) bots whose binding uses a canary rollout. A share
of instances runs `canary` while the rest run `active`, and Verification
compares the two online. Promotion sets it when the trial starts. When the
trial succeeds, `active` moves to the same revision; when it is stopped,
the canary instances run `active` again ([08-promotion.md](08-promotion.md)).
*Example:* `canary` = `r42` on 10% of instances for a day, then `active`
moves to `r42`.

**`draft`: the owner's work in progress.** Each manual edit through the
genome API (UI or API) records a revision and moves `draft` to it. Nothing runs `draft` until the
owner promotes it, so an owner can save half-done work. *Example:* the owner
edits `SOUL.md` twice; that records `r43` and `r44`, `draft` = `r44`, and
`active` stays `r42` until the owner promotes `r44`.

**`candidate/<run_id>/<n>`: one candidate of one run.** The `n`-th
candidate (counting from 1, in submission order) that the Evolution Run
`run_id` submitted. Evolution Run creates it when the strategy submits a
candidate ([06-evolution-run.md](06-evolution-run.md)). It is never moved
afterwards, so the name keeps working after the gate decides and can be used
in the UI, API paths, and ledger links. *Example:* `run_7f3` submits two patches;
they are recorded as `r42` and `r45`, and named `candidate/run_7f3/1` and
`candidate/run_7f3/2`. `GET …/genome/revisions/candidate/run_7f3/2` returns `r45`. A
revision made by an owner's manual edit gets no candidate ref.

### 5.3 Compare-and-swap

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

### 5.4 Going back is promotion

Within the evolution platform, "rolling back" to `r41` means **promoting
`r41` again**. There is no separate rollback path. Because ids are content
hashes, no new revision is created: the `active` ref moves back to `r41`,
`previous` moves to the revision that was active, and the ref log records the
move with actor and reason. To the rest of the platform this is just "apply
this revision", exactly like any forward promotion:

- **Personal bot:** the revision is applied through Manifest apply.
- **Service bot:** the revision goes out as the **next published version**
  through the existing draft → verify → publish flow. The publish record
  records the digest of the published document, which the Registry maps to
  the revision.

The existing service-bot rollback feature is left as it is. This design
neither replaces nor extends it. The promotion endpoint
(`POST /bots/{bot_id}/genome/promotions`) and the rollout rules are specified in
[08-promotion.md](08-promotion.md); the Genome Registry provides the CAS ref
move and the compile step it uses.

### 5.5 Direct Manifest edits and drift

The Manifest is not revision-aware, and the existing `/config-manifest`
endpoints keep working exactly as today. A direct `PUT /config-manifest`
bypasses the genome: it records no revision and moves no ref. Owners who
want history edit through the genome API instead: record a revision (which
moves `draft`) and promote it. The genome API can also record a revision
from a whole Manifest document (`POST …/genome/revisions` with `{manifest}`,
§12); that is a genome operation, not a Manifest one.

**How the Registry knows what a bot runs.** Every apply report (personal
bot) and every publish record (service bot) records the digest of the
Manifest document it applied (§8 item 5). When Promotion applies a compiled
revision, the Registry records that the revision compiled to that digest
(`CompiledManifest.document_digest`, §2.7). Given a bot's last applied
digest, the Registry knows which revision the bot runs.

**Drift.** A bot has *drifted* when its last applied document digest (from
the latest apply report, or the publish record of the version that is
serving) differs from the digest `active` compiles to. It usually follows a
direct Manifest edit. Drift is detected, not prevented. The Registry checks
for it at run start and at promotion:

- **Run start.** A run does not start on a drifted bot: Evolution Run ends
  it with end reason `out_of_band_change`
  ([06-evolution-run.md](06-evolution-run.md)).
- **Promotion.** Promotions by the gate, by review-queue approvals, and by
  an auto-rollback rule are refused with `409 drifted`
  ([08-promotion.md](08-promotion.md)). An owner promotion is allowed,
  because it is how the owner reconciles.

The owner reconciles in one of two ways:

1. **Keep the direct edit.** Record the bot's current Manifest as a
   revision and promote it.
2. **Discard the direct edit.** Promote `active` again, which re-applies it
   and overwrites the direct edit.

Either way the new apply records the digest the new `active` compiles to,
and the drift ends. Experience recorded while the bot was drifted stays
unattributed ([02-experience.md](02-experience.md) §3).

*Example:* `bot_123` runs `r42`, which compiles to `sha256:f27a…`.

1. On Oct 9 the owner edits `SOUL.md` and sends the document straight to
   `PUT /config-manifest`. Apply succeeds; its report records
   `sha256:b7e3…`, which no revision compiles to. The bot has drifted.
2. On Oct 10 at 02:00 the nightly binding fires. The Registry reports
   drift, so `run_8a1` is ended with `out_of_band_change` before any
   budget is reserved.
3. The owner records the current Manifest as a revision
   (`POST …/genome/revisions` with `{manifest}`, base `r42`), which becomes
   `r46`, and promotes `r46`. Its apply report records the digest `r46`
   compiles to, which is what `active` now compiles to. The next nightly run
   starts from `r46`.

## 6. Genome Patch

### 6.1 Why patches

A Genome Patch is the only thing a strategy may submit. It is itemized and
typed, following ACE's finding that delta updates avoid context collapse
(whole rewrites gradually erase accumulated detail). Whole-document
replacement by automated actors is not supported. Humans can submit a patch
too (through `POST …/genome/revisions`, §12), or record a whole document.

Ops shown in the sources (the full op set is fixed by RSI-02):

| Op | Changes | Notes |
| --- | --- | --- |
| `file.edit` | A text file: persona md, `SKILL.md`, resource text files | Structured edits: `replace_section` (by heading), `insert_after` (by anchor) |
| `skill.add` | A new bot-owned skill | Small text inline; larger files uploaded first with `PUT …/genome/content`, referenced by digest |
| `skill.update` | Files of an existing bot-owned skill | One unified diff (the `git diff` format) per changed file |
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
`patch_digest`. Keeping both identities is open decision G-9. See [03-strategy.md](03-strategy.md) for the
strategy-side `ctx.candidates.submit`.

## 7. Memory

Today `MEMORY.md` and `IDENTITY.md` are reserved: apply never writes or
deletes them, because the engine's runtime writes them. Self-improvement
needs part of memory to be evolvable, so memory is split:

| Layer | Owner | In genome? | Evolution |
| --- | --- | --- | --- |
| **Episodic / runtime memory**: what the bot writes as it works | Engine runtime | No | Captured into [Experience](02-experience.md) as evidence |
| **Curated memory (seed)**: facts, rules, lessons the bot should start with | Genome | **Yes**, itemized | Proposed by consolidation ("dream") strategies, gated, promoted |

**Why curated memory is its own category, not a persona file.** The
Manifest's lack of a memory file is only part of the reason. Three
things would stay true even if the Manifest accepted `MEMORY.md`:

- **The bot writes its memory while it runs.** A file in the genome would
  be overwritten by the bot between applies, and each apply would erase
  what the bot learned. Items plus a mode (§2.2) say exactly what apply
  writes and what it leaves alone.
- **The format belongs to the engine.** Each engine stores memory its own
  way. Backend must not know engine paths (below), so the genome holds
  engine-neutral items and the engine adapter writes them.
- **Changes are per fact.** A strategy adds, updates, or retires one item,
  with the evidence it came from (`source`). Each change is a small,
  reviewable op with its own risk tier (§6.3). An edit to one large file
  would be neither.

**How curated memory reaches the engine.** It travels like every other
category: compiled into the Manifest document's `memory` category (§8 item
3) and applied by Manifest apply, which hands the items and the mode to the
engine adapter. Projection onto the engine is engine-owned: Backend must not
know engine paths, per the repository rule in `AGENTS.md` ("Physical layout ownership belongs to Engine Runtime and its versioned layout contract"; ADR 0014 is one application of it). Proposed **engine memory projection contract** (work item
RSI-05), a Plugin API implemented by the engine adapter:

```python
class EngineMemoryProjection(Protocol):
    """Engine-owned. Backend never learns where or how memory is stored."""

    def export_memory(self, bot: BotRef) -> list[MemoryItem]:
        """Read the engine's runtime memory into normalized items, for consolidation strategies."""

    def project_memory(self, bot: BotRef, items: list[MemoryItem],
                       mode: MemoryMode) -> None:
        """Materialise curated items into the engine's layout. The modes and an
        example are in §2.2: seed writes only keys the engine does not have yet,
        merge overwrites by key, replace makes the curated set the whole memory."""
```

- Engines declare support per mode in the capability matrix; teclaw support
  goes through the `BotConfigArtifact` contract like other categories.
- This amends the "reserved files are never touched" rule. It is a stated
  consequence of DR-1 (the Bot Genome, built on the Manifest, is the unit
  of evolution, so curated memory must be part of it), and must be
  confirmed with the engine owners.
- **Interim:** until RSI-05 lands, memory evolution is limited to a
  platform-managed file (for example `LESSONS.md`) declared as a persona
  file. This needs no engine change; `spec.memory` stays empty until then.

## 8. Manifest changes

The Manifest stays revision-unaware (§1, §5.5). It gains format features and
one fact of its own, all backward compatible with schema v1 documents. This
is the complete list:

1. **Content-store source.** An entry may point at a digest in the
   platform's own manifest content store instead of an external source.
   Apply reads the bytes from the store and skips the fetch. A compiled
   revision always uses it, so a compiled document is fully reproducible
   (P1).
2. **`engine_config` applies.** The category already exists in the
   Manifest schema (`schema/validator.py` validates an
   `engine_config.config` mapping), but nothing applies it today: it was
   moved out of the first wave and has no materialiser. The change is a
   materialiser for an allowlist of keys (open decision G-4; P3/P4).
3. **`memory` category**: curated items plus a mode, handed by apply to the
   engine adapter (`EngineMemoryProjection`, §7; P5, depends on RSI-05).
4. **Produced-content write path.** The manifest content store accepts
   **produced** (non-fetched) content, such as content created by a patch,
   with patch provenance (§9.1; P2).
5. **Applied-document digest on the apply report.** When apply finishes,
   the apply report records the hash of the exact Manifest document it
   applied (for example `sha256:f27a…`). For service bots, the publish
   record records the digest of the published document. This is a Manifest
   fact with no revision concept; the Genome Registry maps digests to
   revisions (§5.5) (P1).

Revisions, refs, compare-and-swap, pinning, provenance, and annotations need
no Manifest change: they live in the Genome Registry, on top of the
Manifest.

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
- **Outside Backend** (UI, pipelines, strategies, evaluators), the Registry API
  serves `GET /bots/{bot_id}/genome/content/{digest}`. It is access-checked
  against the bot, so a digest is not a capability by itself. New content is
  uploaded with `PUT /bots/{bot_id}/genome/content`, which returns its digest.
- **At apply,** compiling a revision emits Manifest entries that point at the
  content store by digest (§8 item 1). Apply hands those bytes to the
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

Recommendation: DB-native, plus a **git export** of the revision history (proposed; not
yet an endpoint in §12) so humans and coding-agent strategies can work on a familiar
filesystem history (the Meta-Harness lesson) without git being the source
of truth.

Proposed tables (names illustrative, fixed by RSI-03): `ac_genome_revision`
(id, owner_id, bot_id, seq, parent, provenance, status, canonical
JSON of `{spec, policy}`), `ac_genome_ref` (owner_id, bot_id, name,
revision, version for CAS),
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
  revisions. Out of scope for v1, but nothing here prevents it: revision ids
  and refs are generic.
- **Mechanisms (level 3, later).** The Registry is designed to be generic
  over `target_kind` (`bot_genome` now, `mechanism` later): strategy versions
  can be stored with the same revision / ref / patch machinery, with refs
  scoped per strategy family. See [10-meta-evolution.md](10-meta-evolution.md).
- **Cross-bot skill transfer (P6)** goes through Skill Center governance
  (ADR 0010) and is re-evaluated per consuming bot.

## 11. Service interface

The Genome Registry is a Backend core service, transport-agnostic, with a
`GenomeRegistry` Protocol. Callers in Backend (Promotion) use it in
process; callers in
`apps/evolution` (Evolution Run, Verification) use it through the API in §12.

```python
from typing import Protocol

class GeneChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"

@dataclass(frozen=True)
class GeneChange:
    gene: str                        # gene path (§2.1), e.g. "persona.SOUL.md"
    kind: GeneChangeKind
    diff: str | None = None          # unified diff for modified text files; None otherwise

@dataclass(frozen=True)
class GenomeDiff:
    revision: RevisionId             # the newer side
    against: RevisionId              # the side it is compared with
    changes: list[GeneChange]
    risk_tier: RiskTier              # highest tier over the changes, by the §6.3 mapping
    rewrite_flags: list[str]         # paths of files flagged `rewrite` (§6.2)

# Page[T] {total, items}: the shared pagination shape, 09-evolution-api.md.

class GenomeRegistry(Protocol):
    # --- revisions -------------------------------------------------------
    def get_revision(self, bot: BotRef, rev: RevisionId | str) -> GenomeRevision:
        """Accepts a revision id, "r<seq>", or a ref name. Raises NotFound."""

    def list_revisions(self, bot: BotRef, *, status: RevisionStatus | None = None,
                       parent: RevisionId | None = None,
                       page: int = 1, page_size: int = 20) -> Page[RevisionMetadata]:
        """Metadata only, newest seq first."""

    def record_patch(self, bot: BotRef, patch: GenomePatch, *, created_by: Provenance,
                     status: RevisionStatus,          # DRAFT (owner) or CANDIDATE (run) only
                     allowed_genes: list[str] | None = None) -> GenomeRevision:
        """Validate (§6.2), store the patch content-addressed, apply it to `patch.base`,
        and record the resulting revision. Idempotent: the same patch returns the same
        revision. `allowed_genes` is None for owner patches (policy only), a list for runs.
        Raises BaseMismatch, PatchRejected(reasons), LimitExceeded."""

    def record_manifest(self, bot: BotRef, manifest_yaml: str, *, created_by: Provenance,
                        base: RevisionId | None) -> GenomeRevision:
        """Parse a Manifest document, fill omitted categories from `base` (or current state),
        pin every source, store canonical JSON, keep the YAML as provenance. Status `draft`."""

    def set_status(self, bot: BotRef, rev: RevisionId, status: RevisionStatus,
                   *, actor: Provenance, reason: str) -> None:
        """Platform-internal (Promotion). Status is metadata; content never changes."""

    def diff(self, bot: BotRef, rev: RevisionId, against: RevisionId) -> GenomeDiff:
        """Per-category, per-item differences; text files as unified diffs."""

    # --- refs ------------------------------------------------------------
    def list_refs(self, bot: BotRef) -> list[GenomeRef]: ...

    def move_ref(self, bot: BotRef, update: RefUpdate, *, actor: Provenance) -> GenomeRef:
        """Compare-and-swap. Raises RefConflict(current) if the ref moved.
        Only the designated mover may move each ref kind (§5.1); `active`, `previous`,
        `canary` are moved only by Promotion."""

    # --- content ---------------------------------------------------------
    def read_content(self, bot: BotRef, digest: Digest) -> bytes:
        """Access-checked against the bot; the digest must be reachable from one of its revisions
        or uploads. Hash re-verified on read."""

    def put_content(self, bot: BotRef, data: bytes, *, provenance: Provenance) -> Digest:
        """Produced content (§9.1 extension 1). Idempotent by digest."""

    # --- apply / materialisation -----------------------------------------
    def compile(self, bot: BotRef, rev: RevisionId) -> CompiledManifest:
        """Pinned, total Manifest document (curated memory in its `memory` category). Used by Promotion (apply,
        service-bot publish) and Verification (eval bot materialisation)."""

    def applied_revision(self, bot: BotRef) -> RevisionId | None:
        """The revision the bot runs: its last applied document digest (Manifest apply report or service-bot
        publish record) mapped back through the digests recorded at promotion. None when that digest maps to
        no revision. The bot is drifted when this differs from `active` (§5.5); Evolution Run checks it at run
        start and Promotion before promoting."""
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
Callers in the first iteration are humans through the workbench UI,
pipelines and CI over HTTP, and platform services; bot callers are
postponed to a later, separate design.

### GET /bots/{bot_id}/genome/revisions

List revision metadata, filtered. Called by UI,
pipelines, the Ledger read model.

```http
GET /openapi/v1/bots/bot_123/genome/revisions?status=candidate&parent=sha256:a90b…&page=1&page_size=2
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {"id": "sha256:7c1e…", "seq": 42, "parent": "sha256:a90b…", "status": "candidate",
     "created_by": {"kind": "strategy_run", "run_id": "run_7f3", "step": "propose", "actor": "clawevolve/bot-evolution@2.0.0"},
     "created_at": "2026-10-08T03:12:00Z", "patch_digest": "sha256:c41e…"}
  ]
}
```

Errors: 404 unknown bot; 400 invalid filter.

### POST /bots/{bot_id}/genome/revisions

Record a revision from a patch (`{base, patch}`) or from a Manifest document
(`{manifest}`). Called by Evolution Run (when a strategy calls
`ctx.candidates.submit`; strategies never call this directly), and by owners
through the UI or the API. Returns the revision; recording
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
  "parent": "sha256:a90b…",
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

### GET /bots/{bot_id}/genome/revisions/{rev}

Full revision (metadata, `spec`, `policy`). `{rev}` is an id, `r<seq>`, or a
ref name. Called by UI, pipelines, Evolution Run (to build
`ctx.parent`), Verification.

```http
GET /openapi/v1/bots/bot_123/genome/revisions/r41
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "genome_schema": 1,
  "revision": {"id": "sha256:a90b…", "seq": 41, "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
               "parent": "sha256:5d02…", "status": "promoted", "created_at": "2026-09-30T02:40:00Z",
               "created_by": {"kind": "user", "actor": "user_owner_1"}, "patch_digest": null,
               "evidence": [], "evaluations": ["ev_288"], "annotations": {}},
  "spec": {"persona": [{"type": "SOUL.md", "digest": "sha256:2b77…"}], "skills": [], "memory": null,
           "resources": [], "tools": {"mcp": [], "cli_tools": []},
           "engine_config": {"reasoning_effort": "medium"}, "script": null},
  "policy": {"locked_genes": ["script", "tools.mcp", "policy"], "mutable_genes": ["persona", "skills", "memory"],
             "pins": [], "risk_overrides": {}}
}
```

Errors: 404.

### GET /bots/{bot_id}/genome/revisions/{rev}/diff?against=

Diff two revisions. Called by the review UI and the candidate report in
[08-promotion.md](08-promotion.md), and pipelines.

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

### GET /bots/{bot_id}/genome/refs

List refs. Called by UI, pipelines, Evolution Run (resolving a
binding's `parent`), Promotion.

```http
GET /openapi/v1/bots/bot_123/genome/refs
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "refs": [
    {"name": "active", "revision": "sha256:a90b…", "moved_at": "2026-10-01T09:30:00Z",
     "moved_by": {"kind": "user", "actor": "user_owner_1"}, "reason": "approved after review of run_6c2"},
    {"name": "previous", "revision": "sha256:5d02…", "moved_at": "2026-10-01T09:30:00Z",
     "moved_by": {"kind": "user", "actor": "user_owner_1"}, "reason": "approved after review of run_6c2"},
    {"name": "candidate/run_7f3/1", "revision": "sha256:7c1e…", "moved_at": "2026-10-08T03:12:00Z",
     "moved_by": {"kind": "strategy_run", "actor": "clawevolve/bot-evolution@2.0.0", "run_id": "run_7f3"}, "reason": null}
  ]
}
```

### PUT /bots/{bot_id}/genome/refs/draft

Move the owner's `draft` ref, with compare-and-swap. Called by the owner via
the UI or the API. (`active`, `previous`, `canary` are not movable here; they move only
through `POST /bots/{bot_id}/genome/promotions` in
[08-promotion.md](08-promotion.md).)

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request
{"revision": "sha256:7c1e…", "expected_revision": "sha256:a90b…"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200
{"name": "draft", "revision": "sha256:7c1e…", "moved_at": "2026-10-08T10:02:00Z",
 "moved_by": {"kind": "user", "actor": "user_owner_1"}, "reason": null}
```

Errors: 409 `ref_conflict` with the current revision in the body; 404 unknown revision.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 409
{"error": "ref_conflict", "ref": "draft", "expected_revision": "sha256:a90b…", "current_revision": "sha256:3e61…"}
```

### GET /bots/{bot_id}/genome/content/{digest}

Fetch bytes by digest. Access-checked against the bot. Called by UI,
pipelines, strategies' workspace materialisation (through
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

### PUT /bots/{bot_id}/genome/content

Upload produced content; returns its digest. Used before submitting a patch
that references a large file by digest. Called by Evolution Run (on behalf of
strategies) and by owners through the UI or the API.

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

`POST /bots/{bot_id}/genome/promotions` moves `active` (including going back to
an earlier revision). It lives under the genome path but is specified in
[08-promotion.md](08-promotion.md).

### Internal

- In-process `GenomeRegistry` (§11) for Promotion in Backend.
- Engine memory projection contract (§7), a Plugin API implemented by engine
  adapters (RSI-05).

## 13. Examples

### 13.1 An owner edits a bot and goes back

The owner (or a pipeline acting for the owner) uses the public endpoints of
§12, in this order:

1. `GET /openapi/v1/bots/bot_123/genome/revisions/active` returns `r41`
   (`sha256:a90b…`).
2. `POST /openapi/v1/bots/bot_123/genome/revisions` with
   `Idempotency-Key: owner-edit-2026-10-08-01` and the body below records the
   edit as a `draft` revision and returns its id.
3. `PUT /openapi/v1/bots/bot_123/genome/refs/draft` with
   `{"revision": "<the new id>", "expected_revision": "sha256:a90b…"}` moves
   `draft`. A `409 ref_conflict` means someone else moved `draft` first; its
   body names the current revision, and the owner re-reads and decides.
4. `GET /openapi/v1/bots/bot_123/genome/revisions/<the new id>/diff?against=r41`
   shows the change.
5. Going back later is promoting the earlier revision:
   `POST /openapi/v1/bots/bot_123/genome/promotions` with an `Idempotency-Key`
   and `{"revision": "r41", "expected_revision": "<what active points at now>", "reason": "r42 increased escalations"}`
   (specified in [08-promotion.md](08-promotion.md)).

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request body of step 2
{
  "base": "sha256:a90b…",
  "status": "draft",
  "patch": {
    "patch_schema": 1,
    "base": "sha256:a90b…",
    "ops": [{"op": "file.edit", "target": "persona/SOUL.md",
             "edits": [{"kind": "insert_after", "anchor": "## Tone",
                        "content": "Confirm the order id before quoting amounts."}]}],
    "rationale": "Owner edit after a customer complaint.",
    "evidence": []
  }
}
```

### 13.2 Evolution Run records a candidate (platform side)

```python
# Illustrative only: inside apps/evolution, handling ctx.candidates.submit for run_7f3
async def submit_candidate(run: Run, candidate: Candidate) -> str:
    rev = await genome_api.record_revision(
        bot_id=run.bot.bot_id, entity_id=run.bot.owner_id,   # the run's bot: id in the path, owner as entity_id
        base=candidate.patch["base"], status="candidate",
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
bot = BotRef(owner_id="user_owner_5", bot_id="bot_123")
compiled = registry.compile(bot, "sha256:7c1e…")
registry.move_ref(bot, RefUpdate(name="previous", revision=current_active,
                  expected_revision=current_previous, reason=reason), actor=actor)
registry.move_ref(bot, RefUpdate(name="active", revision=compiled.revision_id,
                  expected_revision=current_active, reason=reason), actor=actor)
apply_service.apply(bot=bot, document=compiled.document)
# The apply report records the digest of the applied document (compiled.document_digest, §8 item 5);
# the Manifest knows nothing about r42. Promotion keeps the revision <-> digest mapping on its own
# record and in the Registry (§5.5).
```

The ordering, failure handling, and service-bot publish path are owned by
[08-promotion.md](08-promotion.md).

## 14. Interactions

| Other component / service | Direction | What flows |
| --- | --- | --- |
| Bot Config Manifest (existing) | Genome → Manifest | Revisions recorded from Manifest documents through the genome API; compiled revisions applied through Manifest apply; apply reports carry the applied document digest, which the Registry maps to revisions; direct Manifest edits bypass the genome and are detected as drift (§5.5) |
| Manifest content store (existing) | Genome → store | Bytes by digest; new write path for produced content |
| Skill Center (existing) | Genome → Center | Pinned Center versions resolved at compile/apply |
| [Experience](02-experience.md) | Experience → Genome (by id) | Every episode and feedback record carries the revision id that produced it, attributed through the Registry's digest → revision mapping; `null` while the bot is drifted |
| [Strategy](03-strategy.md) | via Evolution Run | Strategies read the parent revision (`ctx.parent`), materialise and diff workspaces, submit patches as candidates |
| [Experiment Ledger](05-experiment-ledger.md) | Genome → Ledger | Revision records, ref-move events, lineage for the archive view of the ledger and audit |
| [Evolution Run](06-evolution-run.md) | Run → Genome | Drift check at run start (§5.5); resolve binding `parent`; record candidates with `allowed_genes`; upload produced content; binding checks against `policy` |
| [Verification](07-verification.md) | Verification → Genome | Compile/materialise parent and candidate into eval bots; link evaluations to revisions |
| [Promotion](08-promotion.md) | Promotion → Genome | Move `active`/`previous`/`canary` by CAS; set status; compile and apply; record which document digest the applied revision compiled to; refuse on drift (§5.5) |
| [Evolution API](09-evolution-api.md) | callers → Genome | Public genome endpoints |
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
| G-5 | Engine memory projection contract (the engine owner signed off on amending the reserved-file rule when accepting DR-1) | RSI-05; DR-1 consequence; interim `LESSONS.md` |
| G-6 | Lineage when a patch produces content identical to an existing revision (same id, different parent path) | Proposed: return the existing revision, record the extra derivation as a Ledger event |
| G-7 | Whether `risk_overrides` may lower a tier | Proposed: raise only |
| G-8 | Blob retention sweep for the archive | v1 unconditional; any sweep must keep blobs reachable from any revision, never those of `promoted` revisions |
| G-9 | Candidate id vs revision id | Proposed: keep both identities. The candidate id is the content hash of the stored patch (§6.5); the recorded candidate revision id is the content hash of `{spec, policy}` (§4.4). Both appear in the verdict ([07-verification.md](07-verification.md)) and in ledger entries. Alternative: use only the revision id as the handle |
| G-10 | `memory.update` op | Proposed in §6.1 for memory consolidation; tier T1 like `memory.add` / `memory.retire`. Alternative: express an update as retire + add |

Related: DR-1 was accepted in RSI-01 ([ADR 0019](../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md)), unblocking RSI-02 (schema and patch
format), RSI-03 (Registry in Backend), and RSI-04 (Manifest changes for the
genome); see [work-items.md](work-items.md).
