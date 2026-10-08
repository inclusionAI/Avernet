# Bot 基因组——进化后 bot 的形态

> English version: [02-genome.md](02-genome.md)

> 状态：DRAFT（讨论稿）。建立在 Bot Config Manifest
> （`apps/backend/docs/bot-config-manifest/`）之上。拟议决策：
> [DR-1](decisions/0001-bot-genome-is-the-unit-of-evolution.zh-CN.md)。

## 1. 为什么不直接沿用 Manifest？

Manifest（Bot 配置清单）是为 teclaw 接入而构建的，它是正确的**基础**：
引擎中立、声明式、在包括 teclaw 在内的所有引擎上收敛，只通过现有核心服务
apply，并且已经具备能力矩阵和仅追加（append-only）的 apply 报告。复用它意味着
进化可以免费获得面向所有引擎的交付能力。

但作为一个进化中 bot 的产物，它缺少进化所需的东西（代码库证据见
[09-research.md §2.1](09-research.zh-CN.md#21-bot-config-manifest)）：

| Manifest v1 的缺口 | 进化为什么需要它 |
| --- | --- |
| 每个 bot 一行可变记录，没有修订历史，没有内容哈希 | 谱系、归档、可复现、回滚到任意时间点 |
| 没有父指针 | 谱系树；感知后代的选择 |
| 没有 If-Match / ETag | 进化策略与人工并发编辑时会静默地相互覆盖 |
| apply 重新读取当前文档；报告不说明 apply 的是哪些字节 | 无法把行为或经验归因到某个版本 |
| 源是移动引用（git 分支、oss key），每次 apply 都重新解析 | 基因组必须不可变：每个源都钉住到 commit SHA / digest |
| 整文档 PUT，按类别原子替换 | 提议器必须产出小而可审查、逐项列出的补丁 |
| `MEMORY.md` / `IDENTITY.md` 被保留且被拒绝 | 习得的记忆是自改进产出的一半 |
| v1 拒绝 `engine_config` | 模型 / temperature / 推理预算都是合理的可调参数 |
| 文档中省略某个类别意味着「保持不动」 | 一个修订版必须完整决定 bot；否则同一修订版 apply 两次，可能因先前状态不同而得到不同的 bot |
| 拒绝未知键；没有元数据 | 需要来源记录和注解 |
| 回滚仅限一步且仅限 service bot | 需要对每个 bot 回滚到任意已接纳的修订版 |

## 2. 结构

一句话概括：基因组修订版（Genome Revision）是**一份完整且钉住的 Manifest，加上
策展记忆，加上谱系与锁定的 policy，并且只能通过补丁变更**。具体而言，它在
Manifest 之上恰好增加了以下几项：

1. **修订版标识与谱系**：内容哈希 id 加上便于阅读的按 bot 递增序号、父版本、
   来源记录（由谁或什么、基于哪些证据创建）、状态，以及通过比较并交换（CAS）
   移动的具名引用（§3）。
2. **不可进化的 `policy` 段**：锁定基因、可变基因、钉住条目、风险覆盖（见下文）。
3. **钉住的内容**：每个源都解析到 commit SHA 或内容 digest，因此同一修订版总是
   得到相同的字节（§7）。
4. **完整性（totality）**：每个修订版都包含所有类别；`[]` 表示「无」。与
   Manifest 文档不同，修订版从不把某个类别交给先前状态决定。
5. **自动化执行者只能通过补丁变更**：进化策略和 Bot 针对一个具名的基准修订版
   提交带类型、逐项列出的补丁（§4）。人工仍可从整份文档记录修订版。
6. **一个新的内容类别：策展记忆**（§5），它需要新的引擎契约。

刻意*没有*增加的：评估分数（存放在验证层，修订版只做链接），以及
`engine_config`（Manifest 本身已有该类别；为一个白名单子集放开它是进化会受益的
Manifest 变更，而不是基因组独有的新增）。

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

设计要点：

- **内容寻址的 blob。** 文件内容按 digest 引用，存放在现有的 Manifest 内容存储
  中（§7）。共享某个文件的两个修订版共享同一个 blob。diff 成本很低。
- **钉住，而非浮动。** 当从一份使用移动引用 `sources` 的 Manifest 记录修订版时，
  平台会解析这些引用并存储 blob。浮动的 Manifest 是*输入*；修订版是*事实*。
- **完整，而非部分。** 从一份部分 Manifest 文档记录修订版时，所有被省略的类别
  都从父修订版（首个修订版则从 bot 的当前状态）补全，因此存储下来的修订版是
  完整的。为 apply 编译修订版时总是输出所有类别。
- **Center skill 钉住版本，而不复制。** Skill Center 已经存储了不可变、受治理的
  版本（ADR 0010）。修订版记录 Center 版本；只有 bot 自有的 skill 以及来自
  git/OSS 的内容才以 blob 形式存储。
- **`spec` 与 `policy`。** `policy` 归 bot 所有者 / 平台所有，永远不归进化策略
  所有。平台底线会拒绝任何触及它的补丁。「工具和权限变更只能由人完成」正是在
  这里通过结构而非约定来强制执行的。
- **`script` 默认锁定。** 命令式启动脚本是最危险的可进化面，并且在 teclaw 上
  已经被拒绝。它们仍然是基因组的一部分，以保证修订版完整，但除非所有者解锁，
  否则处于锁定状态。
- **元数据引用评估，而从不内嵌评估。** 分数存放在 C5 中，在归档读模型中进行
  关联；基因组保持为纯粹的定义。

## 3. 引用（ref）

指向不可变修订版的具名、可移动指针，类似 git：

| 引用 | 含义 | 谁来移动它 |
| --- | --- | --- |
| `active` | bot 当前运行的版本 | 仅晋升 |
| `previous` | 上一个 `active`（便于一键回滚） | 仅晋升 |
| `canary` | 金丝雀实例上的修订版 | 仅晋升 |
| `draft` | 所有者进行中的手动编辑 | 所有者（UI/API） |
| `candidate/<run>/<n>` | 一次运行中的候选 | 编排器 |
| `inbox/<id>` | bot 提交的补丁草稿（快循环） | Bot principal |

引用更新采用比较并交换（compare-and-swap，`expected_revision`），从而弥补
并发编辑的缺口。

**与当前 Manifest 行的关系。** 在 P1 中，现有的 `PUT /config-manifest` 继续
可用，并成为如下操作的语法糖：「从这份文档记录一个修订版，将 `draft`（以及为了
向后兼容的 `active`）移动到该修订版，然后 apply」。现有客户端感知不到任何变化；
该行变成 `active` 的一个投影。

**与 service bot 发布的关系。** 一个发布版本已经会冻结一个
`BotConfigArtifact`。对 service bot 而言，晋升 = 记录修订版，以从该修订版编译
出的 artifact 运行 draft → verify → publish，并在发布记录上存储 `revision_id`。
回滚资格规则随之泛化：任何 `promoted` 修订版都可以被重新晋升。

## 4. 基因组补丁

提议器唯一允许产出的输出。逐项列出且带类型，遵循 ACE 的发现：增量更新可以避免
上下文坍缩。

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

平台在记录候选时强制执行的规则：

- base 必须匹配；各 op 必须能干净地应用，否则补丁被拒绝（v1 不做模糊合并）。
- 针对锁定基因或钉住条目的 op 会被拒绝。
- 改动超过文件可配置比例（默认 40%）的 `file.edit` 会被标记为 `rewrite`，
  并提升风险等级。
- 每个 op 都映射到一个**风险等级**（[08-governance.md §3](08-governance.zh-CN.md#3-风险等级)）。
- 补丁大小、文件数量和 blob 大小限制与 Manifest 的限制保持一致。

补丁可以组合：一次包含多个已接纳迭代的运行可以压缩（squash）为一个补丁供审查，
而归档保留每一步。

## 5. 记忆——开放的部分

目前 `MEMORY.md` 和 `IDENTITY.md` 是保留文件：apply 从不写入或删除它们，因为
引擎运行时会写入它们。自改进需要记忆中的一部分是可进化的，因此必须将其拆分：

| 层 | 所有者 | 是否在基因组中？ | 进化方式 |
| --- | --- | --- | --- |
| **情景 / 运行时记忆**——bot 在工作中写下的内容 | 引擎运行时 | 否 | 作为证据被采集进经验库 |
| **策展记忆（种子）**——bot 启动时就应具备的事实、规则、经验教训 | 基因组 | **是**，逐项列出 | 由整合类进化策略（"dream"）提议，经门禁后晋升 |

向引擎的投影由引擎负责（ADR 0014/0017：Backend 不得知道引擎路径）。拟议的
**引擎记忆投影契约**（工作项 RSI-05）：

- `export_memory(bot) -> items[]`——引擎将其运行时记忆读出为规范化条目
  （供整合类进化策略读取）。
- `project_memory(bot, items, mode)`——引擎将策展条目物化到自己的布局中。
  模式：`seed`（仅在不存在时）、`merge`（按 key upsert 策展条目，运行时条目
  保持不动）、`replace`（策展集合成为全部记忆；需要 T3 审批）。
- 引擎在能力矩阵中按模式声明支持情况；teclaw 的支持与其他类别一样，经由
  BotConfigArtifact 契约实现。

这需要修订「保留文件永远不被触碰」这条规则——因此它在 DR-1 中作为一项后果被
明确指出，并且必须与引擎所有者确认。**在 RSI-05 落地之前，记忆进化仅限于一个
以 persona 文件形式声明的平台托管文件（例如 `LESSONS.md`）**——这是一个无需
改动引擎的安全过渡方案。

## 6. 由此引出的 Manifest v2 变更

与 schema v1 文档保持向后兼容。

1. 修订版表 + 引用 + CAS（P1）。
2. apply 报告记录 `revision_id` 和编译后文档的 digest（P1）。
3. 在修订版上记录钉住后的解析结果（P1）；即使从部分文档记录，修订版也是完整的。
4. `metadata` / `annotations` 顶层键，apply 时忽略（P1）。
5. 为一个键白名单启用 `engine_config` 类别（P3/P4）。
6. 带模式的 `memory` 类别（P5，依赖 RSI-05）。
7. 个人 bot 和 service bot 均支持回滚到任意修订版（P1）。
8. Manifest 内容存储接受带补丁来源记录的「产出」（非拉取）内容（P2）。

## 7. 存储

### 7.1 内容：复用 Manifest 内容存储

Manifest apply 管线已经为它拉取的所有内容保存了平台自己的持久副本
（`core/bot_config_manifest/content/service.py`）：

- **字节**存放在按内容寻址的 blob 目录 `<root>/blobs/<hex[:2]>/<hex64>` 中，
  只写一次、原子写入，读取时校验哈希。根目录为
  `user_config.bot_config_manifest.content_store_dir`（默认
  `./data/manifest_content`；部署时指向共享卷）。
- **来源记录**存放在 `ac_manifest_content` 中，这是一张只追加的存储事件日志
  （bot、源 URL、凭证*名称*、apply）。该表不存字节。
- **保留策略**在 v1 中是无条件的：不删除、不清扫、没有 TTL。

基因组修订版引用的就是这个存储中的 blob，不存在第二份副本。需要三项扩展：

1. **产出内容的写入路径。** 目前每个存储事件都是一次*拉取*（`source_url` 必填）。
   由提议器创建的内容（例如编辑后的 `SKILL.md` 或新的记忆条目）从未被拉取过，
   需要一个以产出它的补丁和运行作为来源记录的存储调用。
2. **归档的保留策略。** 归档会保留每一个候选。大多数 blob 是小文本且可去重，
   但 Manifest 资源单个可达 100–200 MiB。v1 采用无条件保留是可以接受的。之后的
   任何清扫只能删除没有任何修订版引用的 blob，并且绝不能删除从某个 `promoted`
   修订版可达的 blob。
3. **Backend 之外的读取方。** `apps/evolution` 中的提议器和评估器通过基因组仓库
   API（或已物化的沙箱）读取内容，绝不直接读取 blob 目录。

待确认问题：目录类资源（来自 git 的 `path: data/kb/`）目前是如何存储的——整体
一个归档 blob，还是每个文件一个 blob。这决定了针对资源的 `file.edit` 补丁能做
到多细粒度，必须在 RSI-02 确定补丁 schema 之前确认。

### 7.2 修订版：用数据库，而不是 git（待决事项 D-6）

| 选项 | 优点 | 缺点 |
| --- | --- | --- |
| **DB 修订版 + 现有内容寻址存储**（推荐） | 租户隔离、ACL、可对谱系和状态进行查询，复用 Manifest 内容存储，符合 Backend 的既有模式 | 需要自己的 diff/merge 工具 |
| 每个 bot 一个 Git 仓库 | 免费获得历史、diff、blame；提议器本就会使用 git | 多租户托管、ACL、跨 bot 查询、GC——一项新的基础设施依赖 |

建议：DB 原生，外加一个 **git 导出**（`avn genome export
--format git`），让人类和编码 agent 类提议器能在熟悉的文件系统历史上工作
（Meta-Harness 的经验），而无需让 git 成为真相来源。

## 8. 多 bot 与团队（未来）

基因组是按 bot 划分的。BCS 负责关系与路由，因此未来的**团队基因组**（角色、
路由提示、共享 skill）将是一个由 BCS 拥有、引用各成员基因组修订版的独立产物。
不在 v1 范围内，但这里的设计不会阻碍它：谱系 id 和引用都是通用的。
