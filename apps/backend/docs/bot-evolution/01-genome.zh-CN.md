# 基因组

> English version: [01-genome.md](01-genome.md)

> 状态：草案（DRAFT）。[Bot 进化架构](design.zh-CN.md)中的一个组件。
> 本文档介绍 Bot 基因组（Bot Genome）：Bot 定义的不可变、带版本的修订版，
> 指向这些修订版的引用（ref），用于变更它们的补丁，以及存储并提供这些内容的
> 基因组注册表（Genome Registry）。

提议决策：[DR-1](decisions/0001-bot-genome-is-the-unit-of-evolution.zh-CN.md)。
基于 Bot Config Manifest（`apps/backend/docs/bot-config-manifest/`）构建。

## 1. 目的与范围

**Bot 基因组**是一个 Bot 的可进化、声明式定义：人设文件、技能、精选记忆、
资源、工具、引擎配置，以及一个不可进化的 `policy` 部分。**基因组修订版**
（Genome Revision）是它的一个已记录的、不可变的版本。基因组是进化的单位：
每个进化策略都读取一个修订版并提出对它的变更，而每一个到达运行中 Bot 的变更
都是对某个修订版的应用。

一句话概括：基因组修订版是**一份完整、已固定版本的 Manifest，加上精选记忆，
再加上谱系和锁定的策略配置，且只能通过补丁变更**。

**基因组注册表**是实现这一点的组件。它负责：

- 基因组修订版（内容、谱系、来源、状态）；
- 具名**引用**（`active`、`previous`、`canary`、`draft`、`candidate/<run>/<n>`）
  及其比较并交换（compare-and-swap）更新；
- **基因组补丁**（Genome Patch）：其模式、校验，以及对基础修订版的应用；
- **基因组策略配置**（锁定基因、可变基因、固定项、风险覆盖），以及在记录补丁时
  对其进行的结构性强制执行；
- 内容寻址：文件字节通过摘要在现有 manifest 内容存储中被引用；
- 将修订版**编译**为一份已固定版本的 Manifest 文档加上一个记忆投影，供应用
  和评估 Bot 使用；
- 任意两个修订版之间的差异。

它明确**不**负责：

| 不在此处负责 | 负责方 |
| --- | --- |
| 判断候选是否良好（套件、评分器、判定） | [07-verification.zh-CN.md](07-verification.zh-CN.md) |
| 判断修订版是否可以成为 `active`、风险等级审批、评审队列、发布，以及移动 `active` 的晋升端点 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 运行进化策略、绑定、每个绑定的 `allowed_genes`、预算 | [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) |
| 进化策略侧对修订版的视图（`ctx.parent`、`ctx.workspace`、`ctx.candidates`） | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| 携带修订版 id 的片段（episode）与反馈 | [02-experience.zh-CN.md](02-experience.zh-CN.md) |
| 修订版、引用移动、门禁决策的审计日志；实验记录的归档视图；感知谱系的父代选择器 | [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) |
| 改进机制（进化策略）修订版，复用本文的修订版/引用/补丁机制 | [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) |
| 修订版在引擎工作区上的物理投影、运行时记忆 | 引擎适配器（ADR 0014/0017：Backend 不得知晓引擎路径） |

**放置位置。** 放在 Backend 中，作为新的核心包 `core/bot_genome/`，与
`core/bot_config_manifest/` 并列，因为 Backend 负责期望状态，且 Manifest
已经位于此处。内容复用 manifest 内容存储
（`core/bot_config_manifest/content/service.py`）。晋升同样位于 Backend 中、
与其相邻，因为晋升必须与应用放在一起。

**可独立使用。** 基因组注册表属于 P1 阶段：带版本的 Bot、可归因的应用，以及
回到任意更早修订版，这些能力独立于任何进化策略都有价值。

## 2. 领域模型

| 类型 | 含义 | 负责方 | 生命周期 |
| --- | --- | --- | --- |
| `GenomeRevision` | Bot 定义的一个不可变版本：`spec` + `policy`，加上平台写入的元数据（`revision`） | 基因组注册表 | 只记录一次（来自补丁或 Manifest 文档），从不编辑；只有其 `status` 和注解会变化；从不删除 |
| `GenomeSpec` | 修订版的可进化内容：人设、技能、记忆、资源、工具、引擎配置、脚本 | 基因组注册表（由所有者编写，由进化策略提议） | 修订版的一部分；参与哈希 |
| `GenomePolicy` | 不可进化的部分：锁定基因、可变基因、固定项、风险覆盖 | Bot 所有者 / 租户管理员 | 修订版的一部分；参与哈希；由平台原样向后复制；任何补丁都不得触及 |
| `RevisionMetadata` | Id、每个 Bot 的 `seq`、谱系、父代、来源、状态、证据与评估链接、注解 | 基因组注册表（平台写入） | 在记录时写入；状态/注解之后更新；不参与哈希 |
| `Provenance` | 谁或什么创建了修订版（某个人、某次进化策略运行、以后可能是某个 Bot），以及依据哪些证据 | 基因组注册表 | 在记录时写入一次 |
| `GenomeRef` | 指向某个修订版的具名、可移动指针（`active`、`previous`、`canary`、`draft`、`candidate/<run>/<n>`） | 基因组注册表；每个引用恰好有一种移动者 | 通过比较并交换移动；每次移动都是一个被审计的事件 |
| `GenomePatch` | 针对具名基础修订版的、带类型的逐项变更；进化策略唯一可提交的内容 | 由进化策略（通过[进化运行](06-evolution-run.zh-CN.md)）或人提交；由注册表校验 | 以内容寻址方式存储；其摘要即候选 id |
| `PatchOp` | 补丁中的一项（`file.edit`、`skill.add`、`memory.add`……） | 补丁的一部分 | 每个操作映射到一个风险等级 |
| `MemoryItem` | 一条精选记忆事实/规则/教训，带键 | 基因组（精选记忆） | 由补丁操作添加、更新和退役 |
| `CompiledManifest` | 编译为已固定版本的 Manifest 文档加记忆投影的修订版 | 基因组注册表（派生而来，不作为事实存储） | 按需为应用和评估 Bot 生成 |

### 2.1 基因

**基因**（gene）是 `spec` 中一个具名、可寻址的部分，策略配置和绑定可以引用它：
可以是一个类别（`persona`、`skills`、`memory`、`resources`、`tools.mcp`、
`tools.cli_tools`、`engine_config`、`script`），类别中的一个键
（`engine_config.reasoning_effort`），或一个条目（`skills.refund-policy`）。
基因路径语法由 RSI-02 确定。

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

每个修订版中都包含所有类别。空列表表示“无”；它从不表示“保持原样”
（完整性，§4.3）。

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

策略配置规定了自动化参与者可以在该 Bot 上变更什么。它由 Bot 所有者或租户管理员
负责，从不由进化策略负责，平台会拒绝任何触及它的补丁。

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

策略配置的使用方式：

- **在记录补丁时**（§6.2）：针对锁定基因的操作、`mutable_genes` 之外的操作，
  以及针对固定条目的操作都会被拒绝。
- **在配置绑定时**：绑定的 `allowed_genes` 必须保持在该 Bot 的策略配置范围内
  （锁定基因保持锁定）；不匹配会在任何付费运行开始前被拒绝。绑定的定义见
  [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)。
- **在提交候选时**：晋升的静态底线检查（`FloorCheck`）会在花费任何验证成本之前
  再次检查“未触及任何锁定基因或固定条目”；见 [08-promotion.zh-CN.md](08-promotion.zh-CN.md)。

正是在这里，“工具和权限变更只能由人完成”得以**结构性地**而非靠约定来强制执行：
工具变更默认位于锁定基因中，而策略配置本身处于补丁可触及的范围之外。
更改策略配置是一项所有者操作，会记录一个新的修订版（§4.4）。

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

### 2.5 GenomePatch 与 PatchOp

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

此示例一次性展示了所有操作类型。按照 §2.2 的策略配置，它会被拒绝，因为
`skills.refund-policy` 在那里被固定（§6.2）；真实的补丁只会触及可变且未固定的基因。

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

`document` 中确切的 Manifest 字段名遵循 Manifest schema v1 以及 §8 中新增的
内容存储来源；上面的结构仅为示意。

## 3. 为什么 Manifest 还不够

Manifest 的构建初衷是让用户能够自行管理 Bot 的资产：他们将技能、人设文件、资源、
MCP 和 CLI 工具保存在自己的来源中，并在一份由平台应用的文档中声明它们。它是正确的
**基础**：与引擎无关、声明式、在包括 teclaw 在内的每个引擎上收敛、只通过现有核心
服务应用，并且已经拥有能力矩阵和仅追加的应用报告。复用它意味着进化可以免费获得
向每个引擎交付的能力。

但作为一个不断进化的 Bot 的产物，它缺少进化所需的东西
（代码库证据见 [research.zh-CN.md](research.zh-CN.md) 的“Bot Config
Manifest”一节：每个 Bot 一行可变记录 `ac_bot_config_manifest`，
`repository/models.py:64`；没有修订版、哈希、父代或 ETag，
`services/config_manifest_service.py:141-184`；应用时重新读取当前文档，
`services/config_manifest_apply_service.py:846-875`；未知键被拒绝，
`schema/validator.py:58`；服务 Bot 只能回滚一步，
`core/service_bot/services/publish_rollback_mixin.py:38-80`）：

| Manifest v1 中的缺口 | 进化为何需要它 | 解决方式 |
| --- | --- | --- |
| 每个 Bot 一行可变记录，没有修订历史，没有内容哈希 | 谱系、归档、可复现性、回到任意时间点 | 修订版（§4） |
| 没有父代指针 | 谱系树；感知后代的选择 | `parents`、`lineage_id` |
| 没有乐观并发控制。HTTP 通过 `ETag`（服务器随文档返回的版本标签）和 `If-Match` 请求头（客户端回传其读取到的标签；若文档自那以后已变更，服务器以 `412 Precondition Failed` 拒绝写入）提供这一能力。`PUT /config-manifest` 两者都没有 | 进化策略与人同时编辑时会静默地互相覆盖；最后写入者在不知情的情况下胜出 | 引用上的比较并交换（§5） |
| 应用时重新读取当前文档；报告不说明应用的是哪些字节 | 无法将行为或经验归因到某个版本 | 应用报告记录 `revision_id` 和文档摘要（§8） |
| 来源是可移动的引用（git 分支、OSS 键），每次应用都重新解析 | 基因组必须不可变：每个来源都固定到提交 SHA / 摘要 | 固定版本的内容（§4.2） |
| 整份文档 PUT，按类别原子替换 | 进化策略必须提交小型、可评审、逐项的补丁 | 基因组补丁（§6） |
| `MEMORY.md` / `IDENTITY.md` 被保留并拒绝 | 习得的记忆是自我改进产出的一半 | 精选记忆（§7） |
| v1 中拒绝 `engine_config` | 模型 / 温度 / 推理预算是合理的可调参数 | 白名单化的 `engine_config`（§8） |
| 文档中省略的类别表示“保持不变” | 修订版必须完全确定 Bot；否则同一修订版的两次应用可能因先前状态不同而产生不同的 Bot | 完整性（§4.3） |
| 没有存放元数据的位置：未知键会被拒绝 | 进化需要**来源**（谁或什么产生了某个版本、依据哪些证据、使用哪个补丁）和**注解**（自由格式的标签，例如评审备注或实验标签） | 保存在修订版记录上，而不是 Manifest 文档内，因此 Manifest 模式无需为此改变 |
| 个人 Bot 无法回到更早的配置；服务 Bot 有自己的发布回滚（只能回退一步） | 回到旧版本必须对每个 Bot 都可用，并且可以回到任意更早的修订版 | 回到旧版本 = 晋升一个更早的修订版（§5.3） |

被否决的替代方案（来自 DR-1）：

- **一个通过独立路径交付的单独进化产物（例如 ClawEvolve Pack）。** 否决原因：
  两个事实来源，而且 Pack 是 OpenClaw 专属的工作区快照，无法触达 teclaw。
- **保持 Manifest v1 不变，在其外部做版本管理。** 否决原因：应用仍会重新读取一份
  可变文档，因此行为无法归因到某个版本。
- **每个 Bot 一个 Git 仓库作为事实来源。** 暂缓；见 §9.2。

## 4. 修订版

### 4.1 修订版在 Manifest 之上增加了什么

具体来说，修订版在 Manifest 之上恰好增加了以下内容：

1. **修订版标识与谱系**：一个内容哈希 id 加上一个可读的每 Bot 序号、父代、来源、
   状态，以及通过比较并交换移动的具名引用（§5）。
2. **一个不可进化的 `policy` 部分**（§2.3）。
3. **固定版本的内容**：每个来源都解析为提交 SHA 或内容摘要，因此同一修订版总是
   产生相同的字节（§4.2）。
4. **完整性**：每个修订版中都包含所有类别（§4.3）。
5. **自动化参与者只能通过补丁变更**：进化策略针对具名基础修订版提交带类型的
   逐项补丁（§6）。人仍然可以从整份文档记录修订版。
6. **一个新的内容类别：精选记忆**（§7），这需要新的引擎契约。

刻意**不**增加的内容：

- **评估分数。** 它们存放在[验证](07-verification.zh-CN.md)中，仅以链接方式
  关联（`evaluations`）；[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)
  中实验记录的归档视图会将它们关联起来。基因组保持为纯粹的定义。
- **作为基因组专属新增项的 `engine_config`。** Manifest 已经有这个类别；启用一个
  白名单子集是一项对进化有益的 Manifest 变更（§8）。

### 4.2 摘要与固定版本

- **摘要，而非内容。** `digest`（`sha256:…`）是文件字节的*地址*，而不是字节本身。
  字节在现有 manifest 内容存储中只存储一次，需要时按摘要获取（§9.1）。共享同一
  文件的两个修订版共享其字节，因此存储被去重，差异计算也很廉价。
- **固定，而非浮动。** 当从一份使用带可移动引用的 `sources` 的 Manifest 记录修订版时，
  平台会解析它们并存储字节。浮动的 Manifest 是一种*输入*；修订版是一项*事实*。
- **Center 技能被固定，而不是被复制。** Skill Center 已经存储了不可变、受治理的版本
  （ADR 0010）。修订版记录 Center 版本；只有 Bot 自有技能和来自 git/OSS 的内容
  才存入内容存储。修订版中的 `center://` 版本是对一个受治理、已发布版本的固定
  引用，而不是已激活的证据。

### 4.3 完整性

从一份不完整的 Manifest 文档记录修订版时，所有被省略的类别都会从父修订版
（对于 Bot 的第一个修订版，则从 Bot 的当前状态）中补齐，因此存储的修订版是完整的。
为应用而编译修订版时，总会输出每个类别。`[]` 表示“无”。与 Manifest 文档不同，
修订版从不把某个类别留给先前状态，因此同一修订版的两次应用总是产生相同的 Bot。

### 4.4 标识、序号与状态

- **Id。** `id = "sha256:" + hex(SHA-256(RFC8785({spec, policy})))`。`revision`
  元数据被排除在外，因此相同的内容总是具有相同的 id，无论由谁产生。记录已存在的
  内容会返回已有的修订版；不会创建重复项，也不会分配新的 `seq`。
- **`seq`。** 一个每 Bot 计数器，在新的 id 首次被记录时分配，供人阅读
  （`r41`、`r42`）。它从不作为标识，也从不用于哈希。
- **谱系。** `parents` 指向基础修订版。`lineage_id` 在后代之间保持稳定；分叉会开启
  新的谱系。合并或交叉类进化策略允许有多个父代。
- **状态**是平台写入的元数据，位于哈希之外：

| 状态 | 含义（提议） | 设置者 |
| --- | --- | --- |
| `draft` | 由所有者的手动编辑记录（UI/API、`PUT /config-manifest`） | 基因组注册表 |
| `candidate` | 由运行期间提交的补丁记录，尚未决定 | 基因组注册表 |
| `accepted` | 门禁已接受它（它可能还在等待评审或发布） | [晋升](08-promotion.zh-CN.md) |
| `rejected` | 验证或门禁拒绝了它；保留在归档中 | [晋升](08-promotion.zh-CN.md) |
| `promoted` | 至少被设为 `active` 一次 | [晋升](08-promotion.zh-CN.md) |
| `archived` | 不再被考虑；仍然从不删除 | 晋升 / 所有者 |

被拒绝和已退役的修订版从不删除；归档保留每一个候选。

- **策略配置变更。** 由于 `policy` 是参与哈希的内容的一部分，所有者更改策略配置
  会记录一个新的修订版（来源为 `user`），其 `spec` 不变。这是一项只能由人完成的
  变更（[08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中的 T3）。

### 4.5 `script` 与 `engine_config`

- **`script` 默认锁定。** 命令式启动脚本是最危险的可进化面，并且已经在 teclaw 上
  被拒绝。它们仍是基因组的一部分，以保证修订版完整，但除非所有者解锁，否则保持锁定。
- **`engine_config`** 只携带白名单中的键，在参与哈希的内容中使用字符串值
  （例如 `"temperature": "0.2"`，见 §9.3）。

## 5. 引用

### 5.1 引用类型

引用是指向不可变修订版的具名、可移动指针，类似 git：

| 引用 | 含义 | 由谁移动 |
| --- | --- | --- |
| `active` | Bot 正在运行的内容 | 仅限[晋升](08-promotion.zh-CN.md) |
| `previous` | 上一个 `active`，保留用于快速“回到旧版本” | 仅限晋升 |
| `canary` | 金丝雀实例上的修订版 | 仅限晋升 |
| `draft` | 所有者正在进行的手动编辑 | 所有者（UI/API） |
| `candidate/<run>/<n>` | 某次运行的第 n 个候选 | [进化运行](06-evolution-run.zh-CN.md) |
| `inbox/<id>` | Bot 提交的草稿补丁（快速循环） | 暂缓：Bot 调用方是之后单独的设计 |

### 5.2 比较并交换

每次引用更新都携带 `expected_revision`：调用方认为该引用当前指向的修订版。
只有在这一点仍然成立时，注册表才会移动该引用；否则返回 `409 Conflict` 及当前
修订版，由调用方重新读取并做出决定。这弥补了并发编辑的缺口：进化策略与人同时编辑
时，不再会静默地互相覆盖。补丁增加了第二个 CAS：补丁的 `base` 必须等于它所应用到的
修订版（§6.2）。

每次引用移动都是一个仅追加的事件（引用、源、目标、参与者、原因、时间）。
审计日志及其读模型见 [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)。

### 5.3 回到旧版本就是晋升

在进化平台内，“回滚”到 `r41` 意味着**再次晋升 `r41`**。没有单独的回滚路径。
由于 id 是内容哈希，不会创建新的修订版：`active` 引用移回 `r41`，`previous` 移到
原先处于 active 的修订版，引用日志记录这次移动及其参与者和原因。对平台的其余部分
而言，这只是“应用这个修订版”，与任何向前的晋升完全相同：

- **个人 Bot：** 修订版通过 Manifest 应用进行应用。
- **服务 Bot：** 修订版通过现有的 草稿 → 验证 → 发布 流程，作为**下一个发布版本**
  发出，并在发布记录上存储 `revision_id`。

现有的服务 Bot 回滚功能保持不变。本设计既不替代也不扩展它。晋升端点
（`POST /bots/{bot}/genome/promotions`）和发布规则在
[08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中定义；基因组注册表提供它所使用的
CAS 引用移动和编译步骤。

### 5.4 与当前 Manifest 记录行的关系

在 P1 中，现有的 `PUT /config-manifest` 继续可用，并成为以下操作的语法糖：
“从这份文档记录一个修订版，将 `draft`（以及为了向后兼容，`active`）移到它，然后
应用”。现有客户端看不到任何变化；该记录行变成 `active` 的一个投影。现有的
`/config-manifest` 端点保留，并成为注册表之上的视图。

## 6. 基因组补丁

### 6.1 为什么使用补丁

基因组补丁是进化策略唯一可以提交的东西。它是逐项且带类型的，这遵循了 ACE 的发现：
增量更新可以避免上下文坍缩（整体重写会逐渐抹去累积的细节）。不支持自动化参与者进行
整份文档替换。人也可以提交补丁（例如 `avn genome patch apply`），或记录一份完整文档。

来源中展示的操作（完整操作集由 RSI-02 确定）：

| 操作 | 变更内容 | 说明 |
| --- | --- | --- |
| `file.edit` | 一个文本文件：人设 md、`SKILL.md`、资源文本文件 | 结构化编辑：`replace_section`（按标题）、`insert_after`（按锚点） |
| `skill.add` | 一个新的 Bot 自有技能 | 小型文本内联；较大的文件先通过 `PUT …/genome/content` 上传，再按摘要引用 |
| `skill.update` | 一个现有 Bot 自有技能的文件 | `unified_diff` 文件操作 |
| `memory.add` | 添加一条精选记忆条目 | 条目携带 `key`、`text`、`tags`、`source` 证据 |
| `memory.update` *（提议）* | 按键替换一条现有条目的文本、标签和来源 | 记忆整合需要它（[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)）；旧版本保留在历史中 |
| `memory.retire` | 按键退役一条条目 | 已退役的条目不会从历史中删除 |
| `engine_config.set` | 一个白名单中的引擎配置键 | 字符串值 |

基因组补丁操作是否映射到 JSON Patch（RFC 6902）由 RSI-02 决定。

### 6.2 记录补丁时强制执行的规则

注册表在从补丁记录候选或草稿时强制执行以下规则（这是门禁静态底线的一个结构性子集；
完整的底线，包括密钥/PII/URL 扫描，见 [08-promotion.zh-CN.md](08-promotion.zh-CN.md)）：

- **基础必须匹配。** `base` 必须等于父代；操作必须能干净地应用，否则补丁被拒绝。
  v1 中没有模糊合并。
- **策略配置。** 针对锁定基因、`mutable_genes` 之外的基因或固定条目的操作会被拒绝。
  任何触及 `policy` 的操作都会被拒绝。
- **绑定范围。** 为某次运行记录时，操作还必须保持在该运行的 `allowed_genes` 范围内
  （由[进化运行](06-evolution-run.zh-CN.md)传入）。
- **重写检测。** 变更超过文件可配置比例（默认 40%）的 `file.edit` 会被标记为
  `rewrite`，并提升风险等级。
- **限制。** 补丁大小、文件数量和文件大小限制与 Manifest 的限制一致。
- **理由与证据。** `rationale` 是必需的；对于非平凡的操作，`evidence` 是必需的。

### 6.3 每个操作的风险等级

每个操作映射到一个**风险等级**；补丁取其所有操作中的最高等级。
每个等级对晋升意味着什么（自动还是人工评审、所有者上限）在
[08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中定义。默认映射以数据形式携带在补丁
模式中（工作项 RSI-02：“每个补丁操作都有明确的风险等级”）；08-promotion.zh-CN.md
中的表格是同一个映射，必须与此处保持一致：

| 操作 | 目标 | 等级 |
| --- | --- | --- |
| 修订版记录上的注解变更 | — | T0 |
| `memory.add`、`memory.update`、`memory.retire`（模式 `seed`、`merge`） | `memory` | T1 |
| 仅触及技能描述的 `skill.update` | `skills/<name>` | T1 |
| `file.edit` | `resources/...` | T1 |
| `file.edit` | `persona/*`（SOUL、AGENTS、RULES） | T2 |
| `skill.add`、`skill.update`（内容） | `skills/<name>` | T2 |
| `engine_config.set`（白名单中的键） | `engine_config.<key>` | T2 |
| 针对 `tools.mcp`、`tools.cli_tools`、`script` 的任何操作；记忆 `replace` 模式；权限变更 | 锁定基因 | T3 |
| 针对 `policy` 的任何操作 | `policy` | 被底线拒绝（永远不可晋升） |

在此映射之后应用的等级提升（被标记为 `rewrite` 的编辑，以及触及类护栏文本的编辑，
至少为 T2）在 [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中说明。

`policy.risk_overrides` 允许所有者提升某个基因的等级（提议：它永远不能降低等级）。

### 6.4 组合

补丁可以组合：一次包含多个已接受迭代的运行可以被压缩为一个补丁供评审，
同时归档保留每一步。

### 6.5 补丁标识与候选

存储的补丁以内容寻址。其摘要即进化策略和晋升所使用的**候选 id**
（`sha256:c41e…`）：重试提交同一补丁会返回相同的 id，且不会创建任何新内容。
由此产生的修订版有自己的 id（`{spec, policy}` 的哈希），并在 `patch_from_parent`
中记录该补丁。同时保留这两种标识是待定决策 G-9。进化策略侧的
`ctx.candidates.submit` 见 [03-strategy.zh-CN.md](03-strategy.zh-CN.md)。

## 7. 记忆

目前 `MEMORY.md` 和 `IDENTITY.md` 是保留文件：应用从不写入或删除它们，因为由引擎
运行时写入它们。自我改进需要让一部分记忆可进化，因此记忆被拆分：

| 层 | 负责方 | 是否在基因组中？ | 进化方式 |
| --- | --- | --- | --- |
| **情景 / 运行时记忆**：Bot 在工作时写下的内容 | 引擎运行时 | 否 | 作为证据被采集到[经验](02-experience.zh-CN.md)中 |
| **精选记忆（种子）**：Bot 启动时应具备的事实、规则、教训 | 基因组 | **是**，逐项存储 | 由整合（“dream”）类进化策略提议，经门禁检查后晋升 |

到引擎上的投影由引擎负责（ADR 0014/0017：Backend 不得知晓引擎路径）。提议的
**引擎记忆投影契约**（工作项 RSI-05），一个由引擎适配器实现的 Plugin API：

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

- 引擎在能力矩阵中按模式声明支持情况；teclaw 的支持与其他类别一样，通过
  `BotConfigArtifact` 契约实现。
- 这修改了“保留文件从不被触碰”的规则，因此它是 DR-1 的一项明确后果，必须与引擎
  负责人确认。
- **过渡方案：** 在 RSI-05 落地之前，记忆进化仅限于一个由平台管理、并声明为人设
  文件的文件（例如 `LESSONS.md`）。这无需引擎变更；在此之前 `spec.memory` 保持为空。

## 8. Manifest v2 变更

与 schema v1 文档保持向后兼容：

1. 修订版表 + 引用 + CAS（P1）。
2. 应用报告记录 `revision_id` 和编译后文档的摘要（P1）。
3. 固定版本的解析结果记录在修订版上（P1）；即使从不完整的文档记录，修订版也是完整的。
4. 为白名单中的键启用 `engine_config` 类别（P3/P4）。
5. 带模式的 `memory` 类别（P5，依赖 RSI-05）。
6. 用于应用的**内容存储来源**：条目可以指向平台自身内容存储中的摘要，而不是外部
   来源。应用在每次获取之后本来就会读取该存储，因此这只是跳过了获取步骤
   （P1）。
7. manifest 内容存储接受带补丁来源的**产出型**（非获取得到的）内容（P2）。

来源和注解无需修改 Manifest：它们存放在修订版记录上。对于服务 Bot，发布记录引用
修订版 id（RSI-04）。

## 9. 存储

### 9.1 内容：复用 manifest 内容存储

manifest 应用流水线已经为其获取的所有内容保留了平台自己的持久副本
（`core/bot_config_manifest/content/service.py`）：

- **字节**存放在一个内容寻址的 blob 目录中，
  `<root>/blobs/<hex[:2]>/<hex64>`，只原子地写入一次，并在读取时校验哈希。根目录为
  `user_config.bot_config_manifest.content_store_dir`（默认
  `./data/manifest_content`；部署时将其指向共享卷）。
- **来源**存放在 `ac_manifest_content` 中，这是一个仅追加的存储事件日志（Bot、
  来源 URL、凭据*名称*、应用）。该表不保存字节。
- **保留策略**在 v1 中是无条件的：不删除、不清扫、无 TTL。

基因组修订版通过摘要引用同一存储中的内容；不存在第二份副本。

如何根据摘要获取内容：

- **在 Backend 内部，** `ManifestContentService.read(digest)` 返回字节，并在读取时
  重新校验哈希。
- **在 Backend 外部**（UI、CLI、进化策略、评估器），注册表 API 提供
  `GET /bots/{bot}/genome/content/{digest}`。它会针对 Bot 进行访问检查，因此摘要
  本身并不是一种访问凭证。新内容通过 `PUT /bots/{bot}/genome/content` 上传，该接口
  返回其摘要。
- **在应用时，** 编译修订版会输出按摘要指向内容存储的 Manifest 条目（§8 第 6 项）。
  应用将这些字节交给物化器，方式与目前处理获取到的内容完全相同。
- **Center 技能**在修订版中没有摘要；它们通过其固定版本经由 Skill Center 解析。

需要三项扩展：

1. **产出型内容的写入路径。** 目前每个存储事件都是一次*获取*（`source_url` 是必需的）。
   由进化策略创建的内容，例如编辑过的 `SKILL.md` 或新的记忆条目，从未被获取过。
   它需要一个存储调用，其来源为产出它的补丁和运行。
2. **归档的保留策略。** 归档保留每一个候选。大多数 blob 是小型文本且可去重，但
   manifest 资源每个可能有 100–200 MiB。v1 可以接受无条件保留。之后的任何清扫
   都只能删除没有任何修订版引用的 blob，并且绝不能删除可从 `promoted` 修订版
   到达的 blob。
3. **Backend 外部的读取方。** `apps/evolution` 中的进化策略和评估器通过注册表 API
   （或一个已物化的沙箱）读取内容，从不直接读取 blob 目录。

### 9.2 修订版：数据库，而非 git（待定决策 D-6）

| 选项 | 优点 | 缺点 |
| --- | --- | --- |
| **数据库修订版 + 现有的内容寻址存储**（推荐） | 租户隔离、ACL、可对谱系和状态进行查询、复用 manifest 内容存储、符合 Backend 的模式 | 需要自己的 diff/merge 工具 |
| 每个 Bot 一个 Git 仓库 | 免费获得历史、diff、blame；编码智能体本就熟悉 git | 多租户托管、ACL、跨 Bot 查询、GC：一项新的基础设施依赖 |

建议：原生数据库方案，外加 **git 导出**（`avn genome export
--format git`），使人和编码智能体类进化策略可以在熟悉的文件系统历史上工作
（Meta-Harness 的经验），而无需让 git 成为事实来源。

提议的表（名称仅为示意，由 RSI-03 确定）：`ac_genome_revision`
（id、bot、seq、谱系、父代、来源、状态、`{spec, policy}` 的规范 JSON），
`ac_genome_ref`（bot、名称、修订版、用于 CAS 的版本），
`ac_genome_ref_event`（仅追加的引用日志），`ac_genome_patch`（摘要、基础、规范 JSON）。

### 9.3 序列化：规范 JSON

本设计中每一种由平台拥有的记录都以 JSON 形式存储和交换：基因组修订版、基因组补丁、
机制修订版、作业输入和输出、判定，以及实验记录条目。

- **规范形式。** 记录在哈希之前使用 JSON 规范化方案（RFC 8785）进行序列化。
  修订版 `id` 是 `{spec, policy}` 规范形式的 SHA-256；`revision` 元数据被排除在外。
- **参与哈希的内容中不使用浮点数。** RFC 8785 将数字格式化为 IEEE 双精度数，因此
  参与哈希的内容只使用整数、字符串和枚举（例如 `max_wall_clock_s: 7200`、
  `temperature: "0.2"`）。
- **校验。** 每种记录类型都有一个 JSON Schema（RSI-02、RSI-06），这与
  `BotConfigArtifact` 已在使用的机制（`artifact.schema.json`）相同，也是 OpenAPI
  所基于的机制。
- **所有新内容都只使用 JSON**，无论是编写时还是存储时。
- **Bot Config Manifest 仍使用 YAML。** 它是一个现有契约，不做改变。从 Manifest
  文档记录修订版时，YAML 只被解析一次，省略的类别被补齐，来源被固定，然后存储规范
  JSON。原始 YAML 作为来源记录保留，因此其中的注释不会丢失，但它从不作为参与哈希
  或用于交换的形式。

为什么不使用 YAML 作为存储形式：它没有规范序列化（同样的数据可以有多种写法，因此
无法直接哈希）；其隐式类型在 YAML 1.1 与 1.2 之间以及不同库之间存在差异（`no`、`on`、
`1.10`、日期）；而每个插件，无论使用何种语言，都必须读取到完全相同的值。补丁和 diff
工具（JSON Patch，RFC 6902；JSON Merge Patch，RFC 7396）也是基于 JSON 定义的。

## 10. 超越单个 Bot

- **团队（未来）。** 基因组以 Bot 为单位。BCS 负责关系和路由，因此未来的
  **团队基因组**（角色、路由提示、共享技能）将是一个由 BCS 负责、引用成员基因组
  修订版的单独产物。不在 v1 范围内，但这里没有任何东西阻碍它：谱系 id 和引用是通用的。
- **机制（第 3 层，之后）。** 注册表被设计为对 `target_kind` 通用（现在是
  `bot_genome`，之后是 `mechanism`）：进化策略的版本可以使用同一套
  修订版 / 引用 / 补丁机制存储，引用按进化策略族划分作用域。
  见 [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)。
- **跨 Bot 技能迁移（P6）** 经由 Skill Center 治理（ADR 0010），并针对每个使用它的
  Bot 重新评估。

## 11. 服务接口

基因组注册表是一个 Backend 核心服务，与传输无关，提供 `GenomeRegistry` Protocol。
Backend 中的调用方（晋升、`/config-manifest` 兼容层）在进程内使用它；
`apps/evolution` 中的调用方（进化运行、验证）通过 §12 中的 API 使用它。

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

错误是带类型的，并映射到 §12 中的 HTTP 状态码：`NotFound` → 404，
`RefConflict` / `BaseMismatch` → 409，`PatchRejected` → 422，
`LimitExceeded` → 413，`MoverNotAllowed` → 403。

## 12. API

公共 API 前缀：`/openapi/v1`。以下路径均相对于它。除内容字节外，所有请求和响应体
都是 JSON。POST 接受 `Idempotency-Key` 请求头（规则见
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）。以下响应展示的是标准信封中的
`data` 负载；信封、错误、分页和幂等性见
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。
第一轮迭代中的调用方是人（UI、`avn` CLI）、流水线和平台服务；Bot 调用方推迟到之后
单独的设计中。

### GET /bots/{bot}/genome/revisions

列出经过筛选的修订版元数据。调用方：UI、CLI（`avn genome log`）、流水线、
实验记录读模型。

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

错误：404 未知 Bot；400 无效筛选条件。

### POST /bots/{bot}/genome/revisions

从补丁（`{base, patch}`）或 Manifest 文档（`{manifest}`）记录修订版。调用方：
进化运行（当进化策略调用 `ctx.candidates.submit` 时；进化策略从不直接调用此接口），
以及通过 UI/CLI（`avn genome patch apply`）操作的所有者。返回该修订版；记录已存在的
内容时，以 `200` 返回已有修订版。

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

错误：409 `base_mismatch`（base 不是当前父代，或操作无法干净地应用）；
422 `patch_rejected` 并附带原因（`locked_gene`、`pinned_item`、`gene_not_allowed`、
`missing_rationale`、`schema`）；413 `limit_exceeded`。

### GET /bots/{bot}/genome/revisions/{rev}

完整修订版（元数据、`spec`、`policy`）。`{rev}` 可以是 id、`r<seq>` 或引用名。
调用方：UI、CLI（`avn genome show`）、进化运行（用于构建 `ctx.parent`）、验证。

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

错误：404。

### GET /bots/{bot}/genome/revisions/{rev}/diff?against=

比较两个修订版的差异。调用方：评审 UI 和 [08-promotion.zh-CN.md](08-promotion.zh-CN.md)
中的候选报告、CLI（`avn genome diff`）。

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

错误：404 任一修订版不存在；422 修订版属于不同的 Bot。

### GET /bots/{bot}/genome/refs

列出引用。调用方：UI、CLI（`avn genome refs`）、进化运行（解析绑定的 `parent`）、
晋升。

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

以比较并交换方式移动所有者的 `draft` 引用。由所有者通过 UI/CLI 调用。
（`active`、`previous`、`canary` 不能在此移动；它们只能通过
[08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中的 `POST /bots/{bot}/genome/promotions`
移动。）

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

错误：409 `ref_conflict`，响应体中包含当前修订版（CLI 退出码 4）；
404 未知修订版。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 409
{"error": "ref_conflict", "ref": "draft", "expected_revision": "sha256:a90b…", "current_revision": "sha256:3e61…"}
```

### GET /bots/{bot}/genome/content/{digest}

按摘要获取字节。针对 Bot 进行访问检查。调用方：UI、CLI
（`avn genome content get`）、进化策略的工作区物化（通过进化运行）、验证。

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

错误：404 未知摘要，或该摘要无法从此 Bot 到达。

### PUT /bots/{bot}/genome/content

上传产出型内容；返回其摘要。在提交按摘要引用大文件的补丁之前使用。调用方：
进化运行（代表进化策略）、CLI（`avn genome content put`）。

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

错误：413 超过 Manifest 文件大小限制。

### 由其他组件负责的相关端点

`POST /bots/{bot}/genome/promotions` 移动 `active`（包括回到更早的修订版）。
它位于基因组路径下，但在 [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中定义。

### 内部接口

- 进程内的 `GenomeRegistry`（§11），供 Backend 中的晋升和 `/config-manifest`
  兼容层使用。
- 引擎记忆投影契约（§7），一个由引擎适配器实现的 Plugin API（RSI-05）。

## 13. 示例

### 13.1 所有者编辑 Bot 并回到旧版本

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

通过 CLI 完成同样的操作：

```text
avn genome show bot_123 --rev active --output json
avn genome patch apply bot_123 --base r41 --file edit.json --dry-run
avn genome diff bot_123 r42 --against r41
avn genome refs bot_123
avn genome promote bot_123 --revision r41 --reason "r42 increased escalations"
avn genome export bot_123 --format git ./bot_123-history
```

### 13.2 进化运行记录一个候选（平台侧）

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

进化策略本身看不到这些：它调用 `ctx.candidates.submit(...)` 并取回候选 id
（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。

### 13.3 晋升应用一个修订版（Backend，进程内）

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

顺序、失败处理和服务 Bot 发布路径由 [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 负责。

## 14. 交互

| 其他组件 / 服务 | 方向 | 传递内容 |
| --- | --- | --- |
| Bot Config Manifest（现有） | 双向 | 从 Manifest 文档记录修订版；编译后的修订版通过 Manifest 应用进行应用；`/config-manifest` 成为 `active` 之上的视图；应用报告记录 `revision_id` |
| Manifest 内容存储（现有） | 基因组 → 存储 | 按摘要的字节；面向产出型内容的新写入路径 |
| Skill Center（现有） | 基因组 → Center | 在编译/应用时解析固定的 Center 版本 |
| [经验](02-experience.zh-CN.md) | 经验 → 基因组（按 id） | 每个片段和反馈记录都携带产生它的修订版 id |
| [进化策略](03-strategy.zh-CN.md) | 经由进化运行 | 进化策略读取父修订版（`ctx.parent`），物化工作区并计算差异，将补丁作为候选提交 |
| [实验记录](05-experiment-ledger.zh-CN.md) | 基因组 → 实验记录 | 修订版记录、引用移动事件、供实验记录归档视图和审计使用的谱系 |
| [进化运行](06-evolution-run.zh-CN.md) | 运行 → 基因组 | 解析绑定的 `parent`；以 `allowed_genes` 记录候选；上传产出型内容；针对 `policy` 检查绑定 |
| [验证](07-verification.zh-CN.md) | 验证 → 基因组 | 将父代和候选编译/物化为评估 Bot；将评估链接到修订版 |
| [晋升](08-promotion.zh-CN.md) | 晋升 → 基因组 | 通过 CAS 移动 `active`/`previous`/`canary`；设置状态；编译并应用；在发布记录上记录 `revision_id` |
| [进化 API](09-evolution-api.zh-CN.md) | 客户端 → 基因组 | 公共基因组端点、SDK、`avn genome` |
| [元进化](10-meta-evolution.zh-CN.md) | 之后 | 用于机制修订版的同一套修订版/引用/补丁机制 |
| 引擎适配器 | 基因组 → 引擎 | 记忆投影（`project_memory`、`export_memory`）；物理布局 |
| BCS | 未来 | 团队基因组将引用成员修订版 |

## 15. 待定决策

| ID | 决策 | 说明 |
| --- | --- | --- |
| D-6 | 基因组存储：数据库修订版 + 内容存储（推荐）与每个 Bot 一个 git 仓库 | §9.2；RSI-03 |
| G-1 | 目录资源（来自 git 的 `path: data/kb/`）目前如何存储：一个归档 blob 还是每个文件一个 blob | 决定针对资源的 `file.edit` 补丁能有多细粒度；必须在 RSI-02 确定补丁模式之前确认 |
| G-2 | 完整的补丁操作集，以及操作是否映射到 JSON Patch（RFC 6902） | RSI-02 |
| G-3 | 锁定基因的默认值 | RSI-02 “完成标准”：评审者就锁定基因的默认值达成一致 |
| G-4 | `engine_config` 键白名单 | P3/P4 |
| G-5 | 引擎记忆投影契约，以及引擎负责人对修改保留文件规则的签字确认 | RSI-05；DR-1 的后果；过渡方案 `LESSONS.md` |
| G-6 | 当补丁产生与已有修订版完全相同的内容时的谱系（相同 id、不同的父代路径） | 提议：返回已有修订版，并将额外的派生关系记录为一个实验记录事件 |
| G-7 | `risk_overrides` 是否可以降低等级 | 提议：只能提升 |
| G-8 | 归档的 blob 保留清扫 | v1 无条件保留；任何清扫都必须保留可从任意修订版到达的 blob，绝不能删除 `promoted` 修订版的 blob |
| G-9 | 候选 id 与修订版 id | 提议：保留两种标识。候选 id 是存储的补丁的内容哈希（§6.5）；记录的候选修订版 id 是 `{spec, policy}` 的内容哈希（§4.4）。两者都出现在判定（[07-verification.zh-CN.md](07-verification.zh-CN.md)）和实验记录条目中。替代方案：只使用修订版 id 作为句柄 |
| G-10 | `memory.update` 操作 | 在 §6.1 中为记忆整合而提议；等级与 `memory.add` / `memory.retire` 相同，为 T1。替代方案：将更新表示为 retire + add |

相关：在 RSI-02（模式与补丁格式）、RSI-03（Backend 中的注册表）和 RSI-04
（Manifest v2 兼容层）开始之前，DR-1 必须被接受（RSI-01）；见
[work-items.zh-CN.md](work-items.zh-CN.md)。
