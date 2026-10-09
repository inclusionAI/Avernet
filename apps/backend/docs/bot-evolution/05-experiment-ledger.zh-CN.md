# 实验记录

> English version: [05-experiment-ledger.md](05-experiment-ledger.md)

> 状态：草稿（DRAFT）。[Bot 进化架构](design.zh-CN.md)中的一个组件。
> 本文档涵盖实验记录 H（Experiment Ledger H）以及实验记录的归档视图（archive view of the ledger）：
> 对每一次改进实验和每一个经审计的治理事件的仅追加记录、建立在其上的归档视图、
> 它的导出，以及查询它的父版本选择器。

## 1. 目的与范围

**实验记录 H** 是平台对其曾尝试过的每一次改进的记忆。一次**实验**就是一次第 2
层的尝试：一个改进机制（某个进化策略版本）取一个父 Bot 修订版 S，提议一个候选
S′，平台对其进行验证并决定如何处理它。实验记录为每次实验保存一条记录（包括每一个被拒绝的实验），
同时保存其所依据的证据、花费的成本、由谁批准，以及晋升后的修订版在真实流量上的
后续表现。

**实验记录的归档视图**是对实验记录及其所指向的基因组修订版的一组只读视图：
一个 Bot 的谱系树、每个候选在每个划分上的得分、产生它的进化策略和模型、
它使用了哪些证据，以及由谁批准。归档中的任何内容都永远不会被删除。

实验记录服务于四类读者：

| 读者 | 用实验记录做什么 |
| --- | --- |
| 人类（所有者、评审者、研究者） | 在 UI 或 `avn` 中浏览谱系、候选报告和审计轨迹 |
| 第 2 层（父版本选择） | 选择 `active` 之外的父版本：latest-best、按用例的 Pareto 前沿、MAP-Elites 生态位、分支（clade）得分（[§7](#7-归档选择器父版本选择)） |
| 进化策略 | 原始历史的文件系统导出，因为对提议者而言原始历史胜过摘要（Meta-Harness）（[§9](#9-导出)） |
| 第 3 层（后续） | 改进机制自身的证据基础：派生的改进机制指标和冻结的改进问题（[§8](#8-改进机制指标与验证器版本)） |

它还承担平台的**审计**义务（治理主题：审计，[§6](#6-审计)）：每一个修订版、
门禁决定、批准、晋升以及回到旧版本，都是一个仅追加的事件，带有操作者、原因，
以及指向证据和评估的链接。

**负责：**

- `LedgerEntry` 记录（实验条目和治理条目）及其仅追加的事件历史。
- 实验记录的归档视图：谱系、每个候选的得分、过滤器。
- 派生的改进机制指标（后续为第 3 层计算）和验证器版本标记。
- 实验记录导出：供进化策略使用的文件系统导出，以及经脱敏的训练数据导出。
- 查询归档的父版本选择器（绑定的 `parent` 字段的后续选项）。

**不负责**（它记录对以下内容的引用，并链接到负责的文档）：

| 事物 | 负责方 |
| --- | --- |
| 基因组修订版、补丁、引用（ref）、内容 blob、修订版 `status`、blob 保留 | [01-genome.zh-CN.md](01-genome.zh-CN.md) |
| 片段（episode）、反馈、数据处理（脱敏、保留、选择加入） | [02-experience.zh-CN.md](02-experience.zh-CN.md) |
| 进化策略注册记录、智能体定义、能力、进化策略视角下的候选与判定 | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| 运行、绑定、预算、绑定的 `parent` 字段 | [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) |
| 套件、评分器、验证配置、判定计算、验证器版本 | [07-verification.zh-CN.md](07-verification.zh-CN.md) |
| 门禁决定、风险等级、评审队列、晋升、回到旧版本 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 共享 API 约定（幂等键、分页、操作查询） | [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) |
| 改进机制验证、第 3 层循环、元策略 | [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) |

实验记录存储*发生了什么*；它不做任何决定。它从不接受、拒绝或晋升候选，
也从不更改验证器：它可以向人类*建议*一项验证器变更（例如“失败类别 Z 没有回归覆盖”），
但从不自行应用。

**在哪里运行。** 在新的 `apps/evolution` 模块中，与进化运行服务、进化策略注册表
（Strategy Registry）、经验存储（Experience Store）以及验证服务（Verification
Service）放在一起（[design.zh-CN.md](design.zh-CN.md) 中待定决策 D-1 的推荐选项 A）。
它所引用的基因组修订版存放在 Backend（`core/bot_genome/`）中；实验记录只存储它们的
id，从不存储其内容。

**分阶段。** 记录从第一次迭代开始（工作项 RSI-21，依赖 RSI-08），因为第一次迭代
无论如何都需要归档和审计轨迹，也因为第 3 层的质量取决于它所学习的历史。
归档选择器（RSI-17）、训练数据导出（RSI-19）以及供第 3 层使用的派生改进机制指标
放在后续。参见 [work-items.zh-CN.md](work-items.zh-CN.md)。

## 2. 领域模型

| 类型 | 是什么 | 归属 | 生命周期 |
| --- | --- | --- | --- |
| `LedgerEntry` | H 中的一条记录。类型 `experiment`：一个已提交的候选及其经历的一切。类型 `governance`：一个与已提交候选无关的经审计事件（例如晋升一个手动记录的修订版，或回到旧版本） | 实验记录（平台） | 在第一个事件到达时创建；永不删除；只能通过追加事件来改变 |
| `LedgerEvent` | 关于某个条目的一个仅追加事实：已提交、已验证、门禁已决定、已评审、已晋升、已回到旧版本、已关联线上结果、留出集结果 | 实验记录（由观察到该事实的服务写入） | 追加后不可变 |
| `MechanismRef` | 产生某次实验的确切改进机制：进化策略 id 与版本、智能体定义摘要、使用的模型 | 实验记录（从运行中复制） | 随条目冻结 |
| `PatchSummary` | 候选提议了什么：操作列表、风险等级、大小 | 实验记录（从基因组补丁和门禁中复制） | 随条目冻结 |
| `VerdictRecord` | 每个划分的验证结果（带验证器版本），以及带原因的门禁决定 | 实验记录（从验证和晋升中复制） | 由事件填充 |
| `Cost` | 该实验花费的 token、金额、挂钟时间、rollout 次数 | 实验记录（来自运行的预算计量器） | 由事件填充 |
| `Adoption` | 是否已晋升、由谁评审、之后是否被回到旧版本 | 实验记录（来自晋升） | 由事件填充 |
| `OnlineOutcome` | 晋升后 S2 相对 S1 的线上指标（延迟关联） | 实验记录（来自经验和线上验证） | 稍后填充，可能多次 |
| `LedgerFilter` | 读者用于列出条目的查询 | 调用方 | 每次请求 |
| `MechanismMetrics` | 某个改进机制修订版在每个 Bot 分群上的派生适应度（后续，第 3 层）；定义由 [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) 负责 | 实验记录（派生视图） | 从条目重新计算 |
| `ParentSelector` | 一条具名规则，用于从归档中为运行选取父修订版（后续） | 实验记录 | 随平台版本化 |

由其他文档负责、在此仅以 id 出现的类型：`GenomeRevision`、
`GenomePatch`（[01-genome.zh-CN.md](01-genome.zh-CN.md)）；`Episode`、`Feedback`
（[02-experience.zh-CN.md](02-experience.zh-CN.md)）；`Candidate`、`Verdict`、
`Operation`（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）；`Run`、`Binding`、`Budget`
（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）；`Evaluation`、
`VerificationProfile`（[07-verification.zh-CN.md](07-verification.zh-CN.md)）；
`GateDecision`、`RiskTier`、`ReviewItem`、`Promotion`
（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。

### 2.1 LedgerEntry

一个实验条目包含第 3 层设计早先确定的实验 schema 的每一个字段
（原 `04-recursion.md` §3）：

| 字段 | 含义 |
| --- | --- |
| `entry_id`、`run_id`、`iteration` | 标识。`entry_id` 是实验 id；`iteration` 是该提交在其运行内的顺序 |
| `mechanism_revision` | 所使用的确切改进机制 M |
| `parent_revision` → `candidate_revision` | S 和 S′（Bot 基因组修订版 id） |
| `evidence` | 改进机制所消费的片段和发现（finding）id |
| `patch` | 它提议了什么（操作、风险等级、大小） |
| `verdict` | 每个划分的 Bot 验证结果、门禁决定、原因 |
| `cost` | token、金额、挂钟时间、rollout 次数 |
| `adoption` | 是否晋升？由谁评审？之后是否被回到旧版本？ |
| `online_outcome` | 晋升后 S2 相对 S1 的线上指标（延迟关联） |

负面结果是一等记录：被拒绝的候选、回归、回到旧版本以及浪费的预算。正是这些
记录让后续的元策略能够学到“算子 X 在 Y 类 Bot 上总是失败”，这正是 ClawEvolve 的
变异算子库所需要的。

```python
from dataclasses import dataclass, field
from typing import Literal

EntryKind = Literal["experiment", "governance"]
VerdictStatus = Literal["pending", "accept", "reject", "inconclusive"]
RiskTier = Literal["T0", "T1", "T2", "T3"]


@dataclass(frozen=True)
class MechanismRef:
    """The exact mechanism (M) that produced a candidate. Frozen from the run."""
    target_kind: Literal["bot_genome"]           # "mechanism" is added with level 3
    strategy: str                                # "clawevolve/bot-evolution"
    version: str                                 # "2.0.0"
    agent_definitions: dict[str, str]            # definition name -> content digest
    models_used: list[str]                       # names from the platform model list


@dataclass(frozen=True)
class PatchSummary:
    patch_digest: str                            # digest of the stored Genome Patch
    ops: list[str]                               # e.g. "persona/SOUL.md: replace_section"
    genes: list[str]                             # top-level genes touched
    risk_tier: RiskTier                          # max over ops, assigned by Promotion
    size_bytes_changed: int
    rewrite_flagged: bool


@dataclass(frozen=True)
class SplitResult:
    split: Literal["train", "validation", "holdout", "regression", "safety"]
    cases: int
    mean_delta: str                              # decimal as string: no floats in hashed records
    ci_low: str | None                           # None for must-pass splits (no interval)
    ci_high: str | None
    newly_failing: int
    evaluation_id: str                           # link into Verification


@dataclass
class VerdictRecord:
    status: VerdictStatus
    verification_profile: str                    # "default@1"
    verifier_version: str                        # every verdict records it
    splits: list[SplitResult]
    reasons: list[str]
    gate: dict | None                            # GateDecision summary, see 08-promotion.md


@dataclass
class Cost:
    tokens: int
    usd: str                                     # decimal as string
    wall_clock_s: int
    rollouts: int


@dataclass
class Adoption:
    promoted: bool
    promotion_id: str | None                     # None until promoted
    reviewed_by: str | None                      # None for auto-promoted or unreviewed
    gone_back: bool                              # active later moved away by going back
    gone_back_by: str | None                     # promotion id that went back


@dataclass
class OnlineOutcome:
    window: str                                  # "2026-10-09/2026-10-16"
    source: Literal["shadow", "canary", "active_vs_previous", "recurrence_check"]
    metrics: dict[str, str]                      # metric name -> decimal string, S2 minus S1
    episodes_compared: int


@dataclass
class LedgerEntry:
    entry_id: str
    kind: EntryKind
    bot_id: str
    created_at: str
    # experiment entries: all set; governance entries: run/candidate fields are None
    run_id: str | None
    binding_id: str | None
    iteration: int | None
    candidate_id: str | None                     # content hash of the patch
    mechanism_revision: MechanismRef | None
    parent_revision: str | None
    candidate_revision: str | None
    evidence: list[str] = field(default_factory=list)
    patch: PatchSummary | None = None
    verdict: VerdictRecord | None = None
    cost: Cost | None = None
    adoption: Adoption | None = None
    online_outcome: list[OnlineOutcome] = field(default_factory=list)
    events: list["LedgerEvent"] = field(default_factory=list)
```

可选字段之所以可选，是因为它们因领域所定义的原因而确实缺失：治理条目没有运行；
实验条目在验证完成前没有判定；在修订版上线足够长时间之前也没有线上结果。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "entry_id": "led_5c2",
  "kind": "experiment",
  "bot_id": "bot_123",
  "created_at": "2026-10-09T02:41:07Z",
  "run_id": "run_7f3",
  "binding_id": "bind_01",
  "iteration": 2,                                  // second submission of run_7f3
  "candidate_id": "sha256:c41e…",                  // content hash of the patch
  "mechanism_revision": {
    "target_kind": "bot_genome",
    "strategy": "clawevolve/bot-evolution",
    "version": "2.0.0",
    "agent_definitions": {"clawevolve-tune": "sha256:5e07…"},
    "models_used": ["platform-default"]
  },
  "parent_revision": "sha256:a90b…",               // r41
  "candidate_revision": "sha256:7c1e…",            // r42
  "evidence": ["episode:ep_91", "episode:ep_97", "finding:f_12"],
  "patch": {
    "patch_digest": "sha256:d2f8…",
    "ops": ["persona/SOUL.md: replace_section Escalation", "skills/refund-policy: update SKILL.md"],
    "genes": ["persona", "skills"],
    "risk_tier": "T2",
    "size_bytes_changed": 1840,
    "rewrite_flagged": false
  },
  "verdict": {
    "status": "accept",
    "verification_profile": "default@1",
    "verifier_version": "verifier-2026.10.1",
    "splits": [
      {"split": "validation", "cases": 40, "mean_delta": "0.085", "ci_low": "0.031", "ci_high": "0.139",
       "newly_failing": 0, "evaluation_id": "eval_301"},
      {"split": "regression", "cases": 22, "mean_delta": "0.000", "ci_low": null, "ci_high": null,
       "newly_failing": 0, "evaluation_id": "eval_302"},
      {"split": "safety", "cases": 15, "mean_delta": "0.000", "ci_low": null, "ci_high": null,
       "newly_failing": 0, "evaluation_id": "eval_303"}
    ],
    "reasons": ["validation CI lower bound 0.031 >= min_effect 0.02", "no must-pass failures"],
    "gate": {"passed_floor": true, "risk_tier": "T2", "decision": "needs_review"}
  },
  "cost": {"tokens": 1250000, "usd": "6.40", "wall_clock_s": 2710, "rollouts": 240},
  "adoption": {
    "promoted": true,
    "promotion_id": "prm_88",
    "reviewed_by": "user:owner_7",
    "gone_back": false,
    "gone_back_by": null
  },
  "online_outcome": [
    {"window": "2026-10-09/2026-10-16", "source": "active_vs_previous",
     "metrics": {"task_success": "0.04", "user_correction_rate": "-0.02", "usd_per_episode": "0.001"},
     "episodes_compared": 812}
  ]
}
```

治理条目使用相同的外层结构，运行和候选字段设为 `null`，并用 `subject`
指明该事件所涉及的对象：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "entry_id": "led_6a0",
  "kind": "governance",
  "bot_id": "bot_123",
  "created_at": "2026-10-20T09:15:00Z",
  "subject": {"type": "promotion", "promotion_id": "prm_93", "revision": "sha256:a90b…"},  // going back to r41
  "run_id": null,
  "binding_id": null,
  "iteration": null,
  "candidate_id": null,
  "mechanism_revision": null,
  "parent_revision": null,
  "candidate_revision": null,
  "events": [
    {"seq": 1, "type": "promoted", "at": "2026-10-20T09:15:00Z",
     "actor": {"kind": "user", "id": "owner_7"},
     "reason": "Refund escalations regressed after r42",
     "links": ["promotion:prm_93", "entry:led_5c2"],
     "data": {"from": "sha256:7c1e…", "to": "sha256:a90b…", "going_back": true}}
  ]
}
```

### 2.2 LedgerEvent

对条目的每一次变更都是一个追加的事件。条目的顶层字段是其事件的折叠（fold）；
事件就是审计轨迹。事件永远不会被编辑或删除。

```python
EventType = Literal[
    "submitted",          # candidate recorded (Evolution Run)
    "verified",           # verdict written, with verifier version (Verification)
    "gate_decided",       # gate decision and risk tier (Promotion)
    "reviewed",           # approve or reject by a human (Promotion)
    "promoted",           # active moved to this revision (Promotion)
    "gone_back",          # active later moved away by promoting an earlier revision (Promotion)
    "online_outcome",     # delayed join of live metrics (Experience / online verification)
    "holdout_audit",      # holdout score: final candidate of a run before promotion, or periodic on active (Verification)
    "revision_recorded",  # revision recorded outside a run, e.g. from a Manifest (Genome)
]

ActorKind = Literal["user", "pipeline", "platform", "strategy_run"]


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    id: str               # user id, pipeline client id, platform service name, or run id


@dataclass(frozen=True)
class LedgerEvent:
    seq: int              # per-entry, strictly increasing from 1
    type: EventType
    at: str               # RFC 3339 UTC
    actor: Actor
    reason: str           # human-readable; required for reviewed / promoted / gone_back
    links: list[str]      # evidence and evaluation ids, e.g. "eval:eval_301", "episode:ep_91"
    data: dict            # type-specific payload, schema per event type
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "seq": 3,
  "type": "gate_decided",
  "at": "2026-10-09T03:30:12Z",
  "actor": {"kind": "platform", "id": "promotion"},
  "reason": "Floor passed; verdict accept; T2 requires human review",
  "links": ["eval:eval_301", "eval:eval_302", "eval:eval_303"],
  "data": {"risk_tier": "T2", "decision": "needs_review", "review_item": "rev_q_12"}
}
```

操作者类型覆盖第一次迭代的调用方：用户（所有者、租户管理员、评审者）、流水线、
平台自身的服务以及进化策略运行。Bot 调用方随 DR-3 推迟；如果之后被允许，
将增加一种操作者类型。

### 2.3 LedgerFilter

```python
@dataclass(frozen=True)
class LedgerFilter:
    kind: EntryKind | None = None              # None: both kinds
    run_id: str | None = None
    strategy: str | None = None                # "clawevolve/bot-evolution"
    strategy_version: str | None = None        # "2.0.0"
    parent_revision: str | None = None
    candidate_revision: str | None = None
    verdict: VerdictStatus | None = None
    promoted: bool | None = None
    gone_back: bool | None = None
    risk_tier: RiskTier | None = None
    verifier_version: str | None = None
    since: str | None = None                   # created_at >= since
    until: str | None = None
    page: int = 1                              # 1-based (09-evolution-api.md)
    page_size: int = 20                        # 1..100
```

每个过滤字段都是可选的，因为 `None` 的含义就是“不按此字段过滤”。

### 2.4 MechanismMetrics（后续，第 3 层）

指标定义由 [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) 负责；
实验记录以相同的字段名将其存储为派生视图。

```python
@dataclass(frozen=True)
class MechanismMetrics:                        # same fields as in 10-meta-evolution.md
    mechanism: str                             # mechanism revision id
    segment: dict                              # e.g. {"engine": "openclaw", "bot_type": "support"}
    verifier_version: str                      # metrics never mix verifier versions
    experiments: int
    verified_yield_pct_per_usd: str
    acceptance_rate_pct: str
    false_acceptance_rate_pct: str
    regression_rate_pct: str
    cost_per_accepted_usd: str
    descendant_productivity: str
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "mechanism": "sha256:3f12…",
  "segment": {"engine": "openclaw", "bot_type": "support"},
  "verifier_version": "verifier@4",
  "experiments": 412,
  "verified_yield_pct_per_usd": "0.31",
  "acceptance_rate_pct": "18.2",
  "false_acceptance_rate_pct": "5.9",
  "regression_rate_pct": "2.9",
  "cost_per_accepted_usd": "6.40",
  "descendant_productivity": "0.44"
}
```

## 3. 记录什么，何时记录

实验记录没有自己的写入者：每个事实都由观察到它的服务通过内部
`ExperimentLedger` 接口（[§11](#11-服务接口)）追加。追加与它所记录的事实属于
同一个逻辑步骤。如果追加失败，该步骤就失败并重试；它绝不会在缺少记录的情况下
报告成功（AGENTS.md：写入失败要向上传播）。

| 事实 | 追加者 | 事件 | 时机 |
| --- | --- | --- | --- |
| 进化策略提交了一个候选 | 进化运行（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)） | `submitted`（创建实验条目） | 在 `ctx.candidates.submit` 返回候选 id 之前 |
| 验证完成 | 验证（[07-verification.zh-CN.md](07-verification.zh-CN.md)） | `verified` | 当判定离开 `pending` 时 |
| 门禁已决定 | 晋升（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)） | `gate_decided` | 在判定最终确定之后；或在提交时，当晋升的静态底线拒绝该候选时（此时判定为 `reject`，不运行套件，底线结果记在 `GateDecision` 上） |
| 人类批准或拒绝 | 晋升 | `reviewed` | 批准 / 拒绝时 |
| `active` 移动到该候选 | 晋升 | `promoted` | 晋升时 |
| `active` 移回到更早的修订版 | 晋升 | 在被离开的条目上追加 `gone_back`，并为该晋升本身创建一个治理条目 | 在回到旧版本的晋升时 |
| S2 相对 S1 的线上指标 | 基于经验的线上验证（[07-verification.zh-CN.md](07-verification.zh-CN.md)、[02-experience.zh-CN.md](02-experience.zh-CN.md)） | `online_outcome` | 在一个线上片段窗口之后；可重复 |
| 留出集得分 | 验证 | `holdout_audit` | 在晋升前对运行的最终候选执行一次（当验证配置要求时），并定期对 `active` 执行（[07-verification.zh-CN.md](07-verification.zh-CN.md)） |
| 在运行之外记录的修订版（所有者编辑、Manifest 导入） | 基因组（[01-genome.zh-CN.md](01-genome.zh-CN.md)） | 治理条目中的 `revision_recorded` | 记录时 |

**submitted 事件捕获什么。** 由于运行在开始时冻结其进化策略版本、参数、父版本和
预算，`submitted` 事件会把它们复制到条目中，并附带：

- 运行所使用的**智能体定义摘要**，使结果可以归因到确切的提示词（智能体定义随
  进化策略版本化，并按摘要加载）；
- 通过 `ctx.models` 和智能体会话**使用的模型**（每一次模型调用都经过平台，并记录在
  H 中）；
- 进化策略在候选上声明的**证据 id**；
- 来自运行预算计量器的截至目前的成本，按下文所述归因到该候选。

**成本归因。** 预算按运行计量。一个提交多个候选的运行会花费一些不属于任何单个
候选的预算（诊断、被丢弃的尝试）。提议的规则：每个候选的 `cost` 是从上一次提交
（或运行开始）到本次提交之间的花费，加上它自己的验证成本；最后一次提交之后的运行级
花费记录在运行上，只通过改进机制指标出现在实验记录中。这样既能让“浪费的预算”
可见，又不会重复计算。

**提交在运行失败后仍然保留。** 在失败、取消或预算停止之前做出的提交会被保留、
仍然被验证并被记录。崩溃后被重新派发的运行会以相同的内容哈希重新提交，并得到相同的
候选 id，因此对重复提交实验记录不会追加任何新内容：`submitted` 在
`(bot, run_id, candidate_id)` 上是幂等的。

**为什么记录每个候选，包括被拒绝的。** 拒绝是正常结果，大多数候选都应该失败。
只记录胜出者恰恰会隐藏第 3 层所需的证据（哪些算子失败、哪些阈值放过了回归、
哪些步骤白白消耗预算），并使误接受率无法计算。

## 4. 谱系与归档

归档是一组只读视图；除了实验记录和基因组修订版之外，它不存储任何东西：

| 视图 | 构建自 | 使用方 |
| --- | --- | --- |
| Bot 的**谱系树** | 基因组修订版的 `parents`，按 `candidate_revision` 与条目关联 | UI 谱系树（RSI-20）、父版本选择器 |
| **每个划分上的候选得分** | 每个条目的 `verdict.splits` | 候选报告（与晋升一起）、Pareto 选择器 |
| **候选的来源** | `mechanism_revision`、`evidence`、`patch` | 评审者、第 3 层 |
| **批准与晋升** | `adoption` 以及 `reviewed` / `promoted` / `gone_back` 事件 | 审计 UI |
| **线上表现** | `online_outcome` | 所有者；误接受率 |

**基于基因组和验证的读模型。** 修订版的评估得分不嵌入基因组中；基因组保持为纯粹的
定义，其元数据只链接评估 id。归档是得分与修订版汇合的地方。

**永不删除。** 被拒绝和已退役的修订版会被保留，永不删除（Hermes Curator 也采用的
“归档而非删除”模式；`archived` 只是一种修订版状态，[01-genome.zh-CN.md](01-genome.zh-CN.md)）。
实验记录条目及其事件永远不会被删除或重写。基因组 blob 的保留由基因组注册表
（Genome Registry）负责：v1 可接受无条件保留，之后的任何清理只能删除没有任何修订版
引用的 blob，且绝不能删除从 `promoted` 修订版可达的 blob
（[01-genome.zh-CN.md](01-genome.zh-CN.md)）。经验的保留按租户设置
（[02-experience.zh-CN.md](02-experience.zh-CN.md)）；当片段过期时，实验记录在
`evidence` 中保留其 id，读者看到的是“已过期”，而不是丢失链接。

**为什么需要带谱系的归档。** 贪心式的“保留最新最优”搜索会停滞；带谱系的归档
才能让开放式搜索持续改进（Darwin Gödel Machine 发现，没有归档其收益会崩塌）。
因此归档是第 2 层的选择池，而不仅仅是审计日志。

## 5. 可见性：谁能看到什么

实验记录中保存着进化策略绝不能看到的数据：逐用例的验证集结果，以及有关留出集、
回归和安全用例的任何内容。隐藏由平台的视图强制执行，而不是靠提示词。

| 读者 | 能看到 |
| --- | --- |
| 所有者、租户管理员、评审者（UI、`avn`、API） | 其 Bot 的全部内容，包括每个划分的结果和证据 |
| 流水线（API） | 与其所使用凭据对应的人类相同 |
| 进化策略（文件系统导出，未来的能力） | 自己的以及被允许的 Bot 的条目，判定**仅含聚合信息**：状态、验证集均值和区间、成本、采纳情况、线上结果。不含逐用例的验证集细节；不含留出集、回归或安全用例及用例 id；训练集结果与运行时报告给进化策略的一致 |
| 元策略（第 3 层，后续） | 与进化策略相同的视图，外加派生的改进机制指标；永远看不到改进机制留出问题 |

禁止跨租户读取：导出或选择器永远不会跨越租户边界，并且默认禁止跨租户使用经验或
进化策略学到的产物（数据处理规则见 [02-experience.zh-CN.md](02-experience.zh-CN.md)）。

## 6. 审计

*治理主题：审计。*

**规则。** 每一个修订版、门禁决定、批准、晋升以及回到旧版本，都是一个仅追加的事件，
带有操作者、操作者类型、原因，以及指向证据和评估的链接。归档视图就是审计 UI。

实验记录如何满足该规则：

- **仅追加。** 事件只写入一次，并带有每个条目内的序号；存储拒绝对事件行的更新和
  删除。纠正错误是一个引用旧事件的新事件。
- **完整。** 每一次晋升都有一个条目：候选的晋升会向其实验条目追加 `promoted`；
  任何其他晋升（回到旧版本、晋升一个手动记录的修订版）都会创建一个治理条目。
  回到旧版本是对更早修订版的一次普通的、经审计的晋升；没有单独的回滚路径，
  也不涉及现有的服务 Bot 回滚功能。
- **可归属。** 每个事件都带有 `actor`（`user`、`pipeline`、`platform` 或
  `strategy_run`）和原因。人类决定和晋升必须填写原因。
- **有链接。** 事件链接评估 id、片段 id、评审项和晋升 id，因此任何决定的证据
  都只有一跳之遥。
- **覆盖操作受审计。** 覆盖操作（例如越过失败的验证门禁发布服务 Bot，RSI-22）
  是一个 `promoted` 事件，其 `data` 记录该覆盖及其批准者。
- **防篡改（提议）。** 每个事件都存储同一条目中上一个事件的规范 JSON（RFC 8785）
  摘要，因此删除或修改事件会破坏这条链。这与验证器一侧对评分器代码和用例内容的
  内容寻址相呼应。

在现有治理规则之外，提议加入审计集合的内容：对 Bot 进化策略配置（绑定）的变更，
以及紧急停止开关的使用。两者都是所有者决定，具有相同的问责需求。参见
[§15](#15-待定决策)。

## 7. 归档选择器（父版本选择）

绑定的 `parent` 字段说明运行从哪个修订版开始。在第一次迭代中它是 `active`。
后续选项会查询归档（工作项 RSI-17）：

| 选择器 | 选取 | 思路来源 |
| --- | --- | --- |
| `active`（第一次迭代） | Bot 的 `active` 修订版 | — |
| `latest_best` | 在当前验证器版本下验证集得分最好的最近一个被接受修订版 | 贪心基线 |
| `pareto_per_case` | 来自逐用例得分 Pareto 前沿的一个修订版（至少在一个用例上最好的修订版） | GEPA |
| `map_elites` | 某个行为生态位中最好的修订版（例如按被修改的基因，或按任务类别） | MAP-Elites |
| `clade_metaproductivity` | 其后代改进最多的修订版，而不是自身得分最好的修订版 | Huxley-Gödel Machine |

规则（提议）：

- 选择器只读取实验记录和基因组修订版，且限定在一个 Bot 和一个验证器版本之内。
- 选择器是平台代码，随平台版本化，由所有者在绑定中选择；进化策略不提供选择器。
- 选择器从不读取留出集结果，因此留出集不会通过父版本选择泄漏到搜索中。
- 选出的父版本像其他所有运行输入一样被冻结在运行上，条目将其记录为
  `parent_revision`。

```python
class ParentSelector(Protocol):
    name: str                                  # "pareto_per_case"
    version: str

    def select(self, bot_id: str, archive: "ArchiveView", seed: int) -> str:
        """Return the revision id a new run starts from.

        Deterministic for a given archive snapshot and seed, so a
        re-dispatched run that re-selects gets the same parent.
        """
        ...
```

## 8. 改进机制指标与验证器版本

**改进机制指标**是改进机制 M 的适应度，按改进机制修订版、按 Bot 分群（引擎、
Bot 类型）以及按验证器版本，从实验条目计算得出。其定义（经验证的改进产出、
接受率与误接受率、回归率、每个被接受改进的成本、后代生产力）由
[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) 负责；实验记录以相同的字段名
将其存储为派生视图（`MechanismMetrics`，§2.4）。

线上结果会输入这些指标：线上发现的误接受会降低该改进机制的指标，被确认的回归
会通过验证成为新的回归用例。

**第一次迭代。** 只进行记录。派生改进机制指标可以等到第 3 层
（[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)），但它们所需的字段（成本、
每个划分的判定、采纳情况、线上结果、改进机制修订版）从第一天起就会被记录。

**验证器版本。** 套件、评分器、验证配置和协议都是版本化的，每个判定都会记录验证器
版本。验证器变更会破坏可比性，因此：

- 每个实验条目都用其判定的验证器版本进行标记；
- 改进机制指标、选择器以及 `latest_best` 比较只在同一个验证器版本内比较条目；
- 在新验证器版本下对旧候选重新评分是一次新的评估和一个新的 `verified` 事件，
  绝不是对旧事件的编辑。

**为第 3 层提供输入。** 第 3 层把 H 中真实的历史实验冻结为*改进问题*（起始修订版、
经验快照、套件、预算），并通过新的验证配置或提交过滤器重放已存储的候选，
从而泛化今天的 `scripts/calibrate_evolution_gates.py` 和
`scripts/replay_candidate_gate.py`。这些工具和改进机制验证协议见
[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)；本文档提供它们所读取的历史。

## 9. 导出

两种导出都是长时操作：`POST
/bots/{bot}/evolution/ledger:export` 立即返回带 `{operation_id}` 的 `202`，
启动在其 `Idempotency-Key` 上是幂等的，状态通过 id 用
`GET /bots/{bot}/evolution/operations/{operation}` 查询
（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）。不会保持任何请求处于打开状态。

### 9.1 供进化策略使用的文件系统导出

编码智能体类和反思类进化策略在文件系统上使用原始历史时，比使用摘要效果更好
（Meta-Harness）。该导出把实验记录的进化策略视图（[§5](#5-可见性谁能看到什么)）
写成一个由规范 JSON 文件组成的目录：

```text
ledger-export/
  manifest.json                     # bot, filter, verifier versions, created_at, entry count
  lineage.json                      # revision id -> parents, seq ("r41"), status
  entries/
    led_5c2.json                    # one LedgerEntry, strategy view (aggregates only)
    led_5c9.json
  patches/
    sha256-d2f8.json                # the Genome Patch of each entry
```

包含补丁内容，是因为它正是提议者自己产出的那类产物；基因组文件内容不会被复制，
而是像其他任何内容一样，通过基因组 API 按摘要读取。进化策略在运行中如何接收该导出
（第 3 层能力 `ledger.read@1`，定义于
[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)）随第 3 层一起决定；在此之前，操作员可以导出后把该目录作为参数交给
进化策略运行。这是提议；基因组历史的 git 导出
（`avn genome export --format git`）是另一个由基因组负责的独立导出。

### 9.2 训练数据导出

平台进化的是 harness 而不是模型，但实验记录以零额外成本为权重训练保留了可能性
（工作项 RSI-19，P6）：

- 同一用例上被接受与被拒绝的候选对就是**偏好数据**。
- 与片段关联后，条目可以给出 `(input, revision, output, scores,
  critiques)` 记录；片段元组本身定义于
  [02-experience.zh-CN.md](02-experience.zh-CN.md)。
- 该导出是**经脱敏的**，并且**仅在租户明确选择加入时**才允许；脱敏、PII 策略和
  保留规则即 [02-experience.zh-CN.md](02-experience.zh-CN.md) 中的数据处理规则。
  没有选择加入的请求会被拒绝。

## 10. 存储与部署位置

提议，位于 `apps/evolution` 中：

| 表 | 内容 | 说明 |
| --- | --- | --- |
| `ledger_entries` | 每个条目一行：标识、类型、Bot、租户、冻结的输入（`mechanism_revision`、`parent_revision`、`candidate_revision`、`patch`），以及其事件的当前折叠 | 需要时可从事件重建折叠；在 Bot、运行、进化策略及版本、判定、是否晋升、验证器版本、created_at 上建立索引 |
| `ledger_events` | 每个事件一行，规范 JSON 负载、每条目内的 `seq`、上一个事件的摘要 | 仅插入；不授予更新或删除权限 |
| `mechanism_metrics`（后续） | 按改进机制修订版、分群、验证器版本的派生指标 | 重新计算；不是事实来源 |

- 所有记录都是 JSON，以 RFC 8785 规范 JSON 存储；被哈希的内容中没有浮点数
  （小数以字符串表示）。
- 记录通过 id 和摘要引用基因组修订版和内容；内容保留在基因组注册表的内容存储中。
- 实验记录遵循架构宪章的分层：核心逻辑与传输无关；REST API 是交付适配器；
  存储实现在 `apps/evolution` 组合根中选择。
- 每种记录类型的 schema（JSON Schema）以及 `ExperimentLedger` 接口的一致性测试
  随实现一起交付（“实验记录 schema + 改进机制指标”是一个数据契约，由选择器、
  元循环和 UI 消费）。

## 11. 服务接口

供平台其他部分使用的内部接口。它是 `apps/evolution` 核心中的一个 Python
Protocol；[§12](#12-api) 中的公共 API 是基于读取和导出方法的交付适配器。

```python
class ExperimentLedger(Protocol):
    """Append-only record of improvement experiments and governance events.

    Writers append facts they observed; nothing in the ledger decides
    anything. Every append is durable before it returns; a failed append
    raises and the caller's step fails.
    """

    # --- writes (internal only) -------------------------------------------

    async def record_submission(
        self, *, bot_id: str, run_id: str, binding_id: str, iteration: int,
        candidate_id: str, mechanism: MechanismRef, parent_revision: str,
        candidate_revision: str, evidence: list[str], patch: PatchSummary,
        cost: Cost, actor: Actor,
    ) -> str:
        """Create the experiment entry for a submitted candidate.

        Idempotent on (bot_id, run_id, candidate_id): a repeated call returns
        the existing entry id and appends nothing.
        """
        ...

    async def append(self, entry_id: str, event: LedgerEvent) -> int:
        """Append one event to an entry and return its seq.

        Refuses events whose type does not fit the entry's state (for example
        `promoted` before `gate_decided` on an experiment entry). Idempotent
        on (entry_id, event.type, event.data["idempotency_key"]) when the
        writer supplies a key.
        """
        ...

    async def record_governance(
        self, *, bot_id: str, subject: dict, event: LedgerEvent,
    ) -> str:
        """Create a governance entry for an audited event that has no
        experiment entry (going back, a revision recorded outside a run)."""
        ...

    # --- reads -------------------------------------------------------------

    async def get(self, bot_id: str, entry_id: str, *, view: "LedgerView") -> LedgerEntry:
        """Return one entry as the given view sees it (full or strategy)."""
        ...

    async def query(
        self, bot_id: str, flt: LedgerFilter, *, view: "LedgerView",
    ) -> "Page[LedgerEntry]":
        """Return one page (`flt.page`, `flt.page_size`) of entries, newest first."""
        ...

    async def archive(self, bot_id: str, *, verifier_version: str) -> "ArchiveView":
        """Snapshot of lineage plus per-split scores, for selectors."""
        ...

    # --- exports and derived data -----------------------------------------

    async def start_export(
        self, bot_id: str, *, format: Literal["filesystem", "training"],
        flt: LedgerFilter, idempotency_key: str, requested_by: Actor,
    ) -> str:
        """Start an export operation and return its operation id at once.

        `training` requires tenant opt-in and raises ExportNotPermitted
        otherwise.
        """
        ...

    async def mechanism_metrics(
        self, strategy: str, version: str, *, segment: dict[str, str],
        verifier_version: str,
    ) -> MechanismMetrics:
        """Derived metrics for one mechanism revision (later, level 3)."""
        ...


LedgerView = Literal["full", "strategy"]
```

## 12. API

所有公共端点都位于前缀 `/openapi/v1` 之下；下文路径均相对于该前缀。它们遵循
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) 中的共享约定（错误、分页、
幂等键、操作查询）。下文的响应展示的是标准外层结构中的 `data` 负载；外层结构、错误、
分页和幂等性见 [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。实验记录没有公共写入端点：条目
只由平台服务写入。

### GET /bots/{bot}/evolution/ledger

列出一个 Bot 的实验记录条目，最新的在前，支持过滤。由 UI 后端（谱系、历史、
审计视图）、`avn` CLI 和流水线调用。

查询参数即 `LedgerFilter` 的字段：`kind`、`run`、
`strategy`、`strategy_version`、`parent`、`candidate_revision`、`verdict`、
`promoted`、`gone_back`、`risk_tier`、`verifier_version`、`since`、`until`、
`page`（从 1 开始）、`page_size`（1 到 100，默认 20）。

示例请求：

```text
GET /openapi/v1/bots/bot_123/evolution/ledger?strategy=clawevolve/bot-evolution&verdict=accept&since=2026-10-01T00:00:00Z&page=1&page_size=2
```

示例响应（`200`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 3,
  "items": [
    {
      "entry_id": "led_5c2",
      "kind": "experiment",
      "created_at": "2026-10-09T02:41:07Z",
      "run_id": "run_7f3",
      "iteration": 2,
      "candidate_id": "sha256:c41e…",
      "strategy": "clawevolve/bot-evolution",
      "strategy_version": "2.0.0",
      "parent_revision": {"id": "sha256:a90b…", "seq": 41},
      "candidate_revision": {"id": "sha256:7c1e…", "seq": 42},
      "risk_tier": "T2",
      "verdict": "accept",
      "validation": {"mean_delta": "0.085", "ci_low": "0.031", "ci_high": "0.139"},
      "cost_usd": "6.40",
      "promoted": true,
      "gone_back": false
    }
  ]
}
```

列表项是摘要；`GET …/ledger/{entry}` 返回完整条目。

错误：`400 invalid_filter`（未知字段、时间格式错误、`page_size` 超出范围）；
`404 bot_not_found`。

### GET /bots/{bot}/evolution/ledger/{entry}

返回一个条目的全部字段及其完整事件历史。由 UI 后端（候选历史、审计详情）和 CLI
（`avn evolve ledger show`）调用。

示例请求：

```text
GET /openapi/v1/bots/bot_123/evolution/ledger/led_5c2
```

示例响应（`200`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "entry_id": "led_5c2",
  "kind": "experiment",
  "bot_id": "bot_123",
  "created_at": "2026-10-09T02:41:07Z",
  "run_id": "run_7f3",
  "binding_id": "bind_01",
  "iteration": 2,
  "candidate_id": "sha256:c41e…",
  "mechanism_revision": {
    "target_kind": "bot_genome",
    "strategy": "clawevolve/bot-evolution",
    "version": "2.0.0",
    "agent_definitions": {"clawevolve-tune": "sha256:5e07…"},
    "models_used": ["platform-default"]
  },
  "parent_revision": "sha256:a90b…",
  "candidate_revision": "sha256:7c1e…",
  "evidence": ["episode:ep_91", "episode:ep_97", "finding:f_12"],
  "patch": {
    "patch_digest": "sha256:d2f8…",
    "ops": ["persona/SOUL.md: replace_section Escalation", "skills/refund-policy: update SKILL.md"],
    "genes": ["persona", "skills"],
    "risk_tier": "T2",
    "size_bytes_changed": 1840,
    "rewrite_flagged": false
  },
  "verdict": {
    "status": "accept",
    "verification_profile": "default@1",
    "verifier_version": "verifier-2026.10.1",
    "splits": [
      {"split": "validation", "cases": 40, "mean_delta": "0.085", "ci_low": "0.031", "ci_high": "0.139",
       "newly_failing": 0, "evaluation_id": "eval_301"},
      {"split": "regression", "cases": 22, "mean_delta": "0.000", "ci_low": null, "ci_high": null,
       "newly_failing": 0, "evaluation_id": "eval_302"},
      {"split": "safety", "cases": 15, "mean_delta": "0.000", "ci_low": null, "ci_high": null,
       "newly_failing": 0, "evaluation_id": "eval_303"}
    ],
    "reasons": ["validation CI lower bound 0.031 >= min_effect 0.02", "no must-pass failures"],
    "gate": {"passed_floor": true, "risk_tier": "T2", "decision": "needs_review"}
  },
  "cost": {"tokens": 1250000, "usd": "6.40", "wall_clock_s": 2710, "rollouts": 240},
  "adoption": {"promoted": true, "promotion_id": "prm_88", "reviewed_by": "user:owner_7",
               "gone_back": false, "gone_back_by": null},
  "online_outcome": [],
  "events": [
    {"seq": 1, "type": "submitted", "at": "2026-10-09T02:41:07Z",
     "actor": {"kind": "strategy_run", "id": "run_7f3"}, "reason": "Round 2 tune result",
     "links": ["episode:ep_91", "episode:ep_97", "finding:f_12"], "data": {"candidate_id": "sha256:c41e…"}},
    {"seq": 2, "type": "verified", "at": "2026-10-09T03:29:55Z",
     "actor": {"kind": "platform", "id": "verification"}, "reason": "Verdict accept",
     "links": ["eval:eval_301", "eval:eval_302", "eval:eval_303"],
     "data": {"status": "accept", "verifier_version": "verifier-2026.10.1"}},
    {"seq": 3, "type": "gate_decided", "at": "2026-10-09T03:30:12Z",
     "actor": {"kind": "platform", "id": "promotion"}, "reason": "Floor passed; verdict accept; T2 requires human review",
     "links": ["eval:eval_301", "eval:eval_302", "eval:eval_303"],
     "data": {"risk_tier": "T2", "decision": "needs_review", "review_item": "rev_q_12"}},
    {"seq": 4, "type": "reviewed", "at": "2026-10-09T08:02:40Z",
     "actor": {"kind": "user", "id": "owner_7"}, "reason": "Escalation wording matches policy",
     "links": ["review:rev_q_12"], "data": {"decision": "approve"}},
    {"seq": 5, "type": "promoted", "at": "2026-10-09T08:02:41Z",
     "actor": {"kind": "platform", "id": "promotion"}, "reason": "Approved by owner_7",
     "links": ["promotion:prm_88"], "data": {"from": "sha256:a90b…", "to": "sha256:7c1e…", "going_back": false}}
  ]
}
```

错误：`404 entry_not_found`（条目属于另一个 Bot 时也返回此错误）。

### POST /bots/{bot}/evolution/ledger:export

启动对该 Bot 实验记录的导出。由操作员和流水线调用（例如研究流水线为编码智能体类
进化策略准备文件系统导出，或租户的训练数据流水线）。立即返回带
`{operation_id}` 的 `202`；工作以操作的形式运行，其状态和结果（导出归档的下载
位置）通过 id 用 `GET /bots/{bot}/evolution/operations/{operation}` 查询，该端点定义于
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。

请求头：`Idempotency-Key`（必填）。使用相同键的重试返回相同的操作 id，
不会启动任何新工作。

示例请求：

```text
POST /openapi/v1/bots/bot_123/evolution/ledger:export
Idempotency-Key: ledger-export-bot_123-2026-10-09
Content-Type: application/json
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "format": "filesystem",                 // "filesystem" (strategy view) or "training" (redacted, opt-in)
  "filter": {
    "strategy": "clawevolve/bot-evolution",
    "since": "2026-07-01T00:00:00Z",
    "verifier_version": "verifier-2026.10.1"
  }
}
```

示例响应（`202`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_19a"}     // look up with GET /bots/bot_123/evolution/operations/op_19a
```

错误：`400 invalid_filter`；`400 missing_idempotency_key`；
`403 export_not_permitted`（`training` 格式但租户未选择加入）；
`409 idempotency_key_conflict`（相同键、不同请求体）；
`404 bot_not_found`。

### 内部：实验记录写入

非公开。平台服务在 `apps/evolution` 内以进程内方式调用 `ExperimentLedger` 接口
（[§11](#11-服务接口)）。晋升运行在 Backend 中，因此它通过 `apps/evolution` 的
内部服务 API 以相同的操作进行追加（`record_submission` 不向 Backend 暴露；
`append` 和 `record_governance` 会暴露）。进化策略从不直接写入实验记录：它们的提交
通过 `ctx.candidates.submit` 到达实验记录，由进化运行服务负责记录。

## 13. 示例

**流水线评审某个进化策略上周被接受的候选。**

```python
from avernet_evolution import Client

c = Client.from_env()
page = c.ledger.list(bot="bot_123", strategy="clawevolve/bot-evolution",
                     verdict="accept", since="2026-10-02T00:00:00Z")
for item in page.items:
    entry = c.ledger.get(bot="bot_123", entry=item.entry_id)
    print(item.candidate_revision.seq, entry.verdict.status,
          entry.cost.usd, entry.adoption.promoted)
```

**UI 后端绘制谱系树。** 修订版来自基因组 API；实验记录补充每个候选经历了什么。

```python
def lineage_tree(c, bot: str) -> dict[str, dict]:
    nodes: dict[str, dict] = {}
    page_no = 1
    while True:
        page = c.ledger.list(bot=bot, kind="experiment", page=page_no, page_size=100)
        for e in page.items:
            nodes[e.candidate_revision.id] = {
                "seq": e.candidate_revision.seq,
                "parent": e.parent_revision.id,
                "verdict": e.verdict,
                "promoted": e.promoted,
                "gone_back": e.gone_back,
            }
        if page_no * 100 >= page.total:
            return nodes
        page_no += 1
```

**进化运行服务记录一次提交**（平台代码，位于 `ctx.candidates.submit` 内部）。

```python
async def submit(self, run: Run, cand: Candidate) -> str:
    candidate_id = content_hash(cand.patch)
    revision = await self.genome.record_candidate(run.bot_id, base=cand.patch.base, patch=cand.patch)
    await self.ledger.record_submission(
        bot_id=run.bot_id, run_id=run.run_id, binding_id=run.binding_id,
        iteration=await self.next_iteration(run, candidate_id),
        candidate_id=candidate_id, mechanism=run.mechanism_ref(),
        parent_revision=cand.patch.base, candidate_revision=revision.id,
        evidence=cand.evidence, patch=summarize(cand.patch),
        cost=self.budget.spent_since_last_submission(run),
        actor=Actor(kind="strategy_run", id=run.run_id),
    )
    await self.verification.enqueue(candidate_id, run.verification_profile)
    return candidate_id   # same id on a retried or re-dispatched submission
```

**操作员启动文件系统导出并等待其完成**（CLI，提议的命令名）。

```text
avn evolve ledger export --bot bot_123 --format filesystem \
    --strategy clawevolve/bot-evolution --since 2026-07-01 \
    --idempotency-key ledger-export-bot_123-2026-10-09 --wait --output json
```

`--wait` 只是按操作 id 重复查询状态；它从不保持请求处于打开状态。

**回到旧版本在实验记录中可见。** 所有者再次晋升 r41 之后，条目 `led_5c2`（r42）
会获得一个 `gone_back` 事件，并且一个治理条目会记录 r41 的晋升及其原因。从那时起，
`clawevolve/bot-evolution@2.0.0` 的误接受率会把 `led_5c2` 计算在内。

## 14. 交互

| 其他部分 | 方向 | 流转内容 |
| --- | --- | --- |
| [01-genome.zh-CN.md](01-genome.zh-CN.md) 基因组 | 基因组 → 实验记录 | 修订版 id、父版本、`seq`、状态；对在运行之外记录的修订版产生 `revision_recorded` 事件 |
| [01-genome.zh-CN.md](01-genome.zh-CN.md) 基因组 | 实验记录 → 基因组 | 为导出（补丁）按摘要读取内容；从不写入 |
| [02-experience.zh-CN.md](02-experience.zh-CN.md) 经验 | 经验 → 实验记录 | 作为证据的片段 id；用于线上结果的各修订版线上片段；训练导出的数据处理规则 |
| [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 进化策略 | 进化策略 → 实验记录（经由进化运行） | 进化策略 id 与版本、智能体定义摘要、使用的模型、证据 id、候选提交 |
| [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 进化策略 | 实验记录 → 进化策略 | 文件系统导出（进化策略视图，仅含聚合信息） |
| [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md) 默认进化策略 | 实验记录 → 进化策略 | 通过文件系统导出，为 ClawEvolve 的 tune 提示词提供原始历史（它已经携带进化历史） |
| [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 进化运行 | 运行 → 实验记录 | `submitted` 事件、冻结的运行输入、每个候选的成本 |
| [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 进化运行 | 实验记录 → 运行 | 为 `parent` 是选择器的绑定选择父版本（后续） |
| [07-verification.zh-CN.md](07-verification.zh-CN.md) 验证 | 验证 → 实验记录 | 带每个划分结果和验证器版本的 `verified` 事件；`holdout_audit`；线上结果 |
| [07-verification.zh-CN.md](07-verification.zh-CN.md) 验证 | 实验记录 → 人类 | 验证器变更建议，从不自动修改 |
| [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 晋升 | 晋升 → 实验记录 | `gate_decided`、`reviewed`、`promoted`、`gone_back`；治理条目 |
| [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 晋升 | 实验记录 → 晋升 | 用于候选报告的历史（以往尝试、谱系） |
| [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) 进化 API | API ↔ 实验记录 | 公共读取与导出端点；共享约定；操作查询 |
| [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) 元进化 | 实验记录 → 元进化 | 用于改进问题的实验、改进机制指标、重放输入（后续） |
| UI（`apps/frontend-nextgen`，过渡期使用 AgentEvolve UI） | 实验记录 → UI | 运行历史、候选历史、谱系树、审计轨迹（RSI-20） |

## 15. 待定决策

| ID | 决策 | 选项 / 当前提议 |
| --- | --- | --- |
| L-1 | 实验记录模型：显式存储的记录 vs 纯读模型 | 早先的文本既把 H 描述为“基于基因组 + 验证的读模型”，又描述为显式、可查询的记录。提议：存储仅追加的事件（审计要求如此）；条目是事件的折叠；归档视图是基于条目和基因组修订版的读模型 |
| L-2 | 每个候选的成本归因 | 提议：自上一次提交以来的花费加上自身的验证成本；剩余的运行花费只计入改进机制指标。备选：把运行的全部花费平均分摊到其候选上 |
| L-3 | 审计轨迹中的进化策略配置变更和紧急停止开关 | 提议：两者都记录为治理条目。治理审计规则只列出了修订版、门禁决定、批准、晋升以及回到旧版本 |
| L-4 | 防篡改 | 提议：基于规范 JSON 的每条目哈希链。备选：依赖仅插入权限 |
| L-5 | 进化策略在运行中如何接收文件系统导出 | 第 3 层能力 `ledger.read@1`（[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)）vs 由操作员生成并作为参数传入的导出；其契约尚未确定 |
| L-6 | 导出操作的状态查询路径 | 已解决：`GET /bots/{bot}/evolution/operations/{operation}`（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）；导出的 `202` 返回 `{operation_id}` |
| L-7 | 选择器集合以及 `map_elites` 的生态位定义 | 在 RSI-17 中、当真正需要第二个选择器时确定 |
| L-8 | 经验过期 vs 证据链接 | 提议：保留 id，标记为已过期；绝不因为实验记录引用了某个片段而阻止其过期 |
| D-1 | 控制平面的模块位置 | 推荐：`apps/evolution`（见 [design.zh-CN.md](design.zh-CN.md)） |
