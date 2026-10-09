# 元进化

> English version: [10-meta-evolution.md](10-meta-evolution.md)

> 状态：草稿（DRAFT）。属于[Bot 进化架构](design.zh-CN.md)中的一个服务。
> 第 3 级：改进改进机制本身，用当前机制验证候选机制，以及任何自动化循环都不得越过的边界。

> **后续（第 3 级），不在第一次迭代中。** 第一次迭代覆盖第 1–2 级：Bot
> 得到改进，且每次变更都经过验证。本文档提前确定第 3 级的设计，使第 2 级的契约
> （进化策略版本、运行、候选、实验记录 H）的形态不会在日后阻碍第 3 级；§4
> 精确列出第 2 级必须事先提供的内容。第一次迭代中唯一构建的第 3 级基础工作是
> **在 H 中记录每一次实验**（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)），
> 而第 2 级本来就需要它来实现归档和审计追踪。此处其余全部内容，包括 §13
> 中的每个端点，均为**提议**，且未排入第一次迭代。

请先阅读 [07-verification.zh-CN.md](07-verification.zh-CN.md)：递归的可信程度
取决于其验证器的可信程度。

## 1. 目的与范围

元进化（meta-evolution）使平台成为*递归*自我改进，而不是重复的单次改进。第 2
级用一个固定的改进机制来改进 Bot。第 3 级把这个机制本身也视为可以被改进的对象：
它从所有第 2 级实验的记录中学习，提出更好的机制，验证新机制产生的*经过验证的*
改进优于当前机制，然后才让它运行后续的第 2 级轮次。

**负责（提议）：**

- 作为可进化工件的**机制**：机制修订版、机制补丁，以及每个家族的机制引用（ref）
  （`active`、`previous`、`canary`、`candidate/*`）——与 Bot 基因组相同的
  修订版/引用/补丁模型。
- **第 3 级循环**：触发、运行元策略、对机制补丁进行静态检查、采纳，以及移交给
  第 2 级运行。
- **机制验证**：改进问题、比较协议（在留出的改进问题上比较 M′ 与 M1），以及
  回放筛选。
- **机制指标**：从 H 推导出的机制适应度。
- **递归的边界**：验证器边界、深度限制和采纳规则。

**不负责：**

| 关注点 | 负责方 |
| --- | --- |
| 实验记录 H 本身：其模式、记录、保留、导出和审计 | [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) |
| 进化策略注册记录、能力目录、进化策略端口 `run(ctx)`、`StrategyContext` | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| 具体的默认进化策略（ClawEvolve），其机制是第 3 级首先要改进的机制 | [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md) |
| 执行一次运行：绑定、租约、预算、紧急停止开关、沙箱、作业协议 | [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) |
| Bot 验证（S′ 与 S1 对比）：套件、划分、评分器、比较器、判定、验证器完整性 | [07-verification.zh-CN.md](07-verification.zh-CN.md) |
| 权力分立、门禁、风险等级、Bot 基因组的晋升 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 共享 API 约定、幂等键、SDK、`avn` CLI | [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) |
| 基因组修订版，以及第 3 级复用的修订版/引用/补丁机制 | [01-genome.zh-CN.md](01-genome.zh-CN.md) |

**运行位置。** 在新模块 `apps/evolution` 中，与 Strategy Registry、进化运行、
验证和实验记录并列（模块放置位置是待定决策 D-1，参见 [design.zh-CN.md](design.zh-CN.md)）。
元循环不需要任何 Backend 代码：机制是进化策略版本，而不是 Bot 的期望状态。

## 2. 领域模型

### 2.1 三个层级

平台围绕三个层级组织。每一级都包裹着前一级。

| 层级 | 循环 | 变化的内容 | 验证方式 | 位置 |
| --- | --- | --- | --- | --- |
| **1. 智能体** | 任务 → Bot S1 ⇄ 环境 → 结果 | 没有持久变化；Bot 完成当前任务 | 任务结果 | 产生**经验**（[02-experience.zh-CN.md](02-experience.zh-CN.md)） |
| **2. 单系统改进** | S1 + 任务反馈 → 机制 **M1** 提出变更 → 候选 S′ 在环境中运行 → **验证并接受** → S2 | **系统**（Bot 基因组）。后续任务使用 S2 | **Bot 验证**（S′ 与 S1 对比） | 在 Bot 基因组上执行的一次进化策略运行（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)、[07-verification.zh-CN.md](07-verification.zh-CN.md)、[08-promotion.zh-CN.md](08-promotion.zh-CN.md)） |
| **3. 递归自我改进** | 实验记录 **H** → 改进 M1 → **验证并采纳** M2 → M2 运行后续的第 2 级轮次 | **改进机制**本身。M2 运行下一轮 | **机制验证**（M2 与 M1 对比） | 本文档 |

![第 3 级：改进改进机制](images/recursion.zh-CN.svg)

没有任何层级保证一定带来改进。每次变更都经过验证，被拒绝的候选是正常且会被记录的
结果。该结果是 H 的证据。

### 2.2 类型

| 类型 | 是什么 | 负责方 | 生命周期 |
| --- | --- | --- | --- |
| `MechanismRevision` | 某个进化策略家族机制的一个不可变、内容寻址的版本：进化策略版本加上决定其行为的一切（§3） | Strategy Registry（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）；第 3 级的补充在此处 | `draft → candidate → accepted \| rejected → promoted → archived`（与基因组修订版状态一致） |
| `MechanismRef` | 指向某个进化策略家族机制修订版的具名、可移动指针：`active`、`previous`、`canary`、`candidate/<run>/<n>` | Strategy Registry；只能由机制晋升移动（`candidate/*` 由编排器移动） | 通过比较并交换（compare-and-swap）移动 |
| `MechanismPatch` | 对机制修订版的类型化、逐项列出的变更；元策略唯一可以提交的内容 | 元策略（提出）→ 平台（记录） | 一经记录即不可变；其候选 id 为其内容哈希 |
| 元策略 | 一个普通的进化策略（`StrategyRegistration`），其目标是机制而非 Bot 基因组 | 进化策略作者 | 与任何进化策略一样注册和版本化 |
| 元运行 | 针对一个进化策略家族执行的元策略 `Run` | 进化运行（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)） | 运行状态：`queued \| running \| completed \| failed \| cancelled \| budget_exhausted` |
| `ImprovementProblem` | 一个冻结的、可回放的第 2 级任务：起始基因组、经验快照、可见与隐藏套件、固定预算 | 机制验证（本文档），从 H 冻结 | 冻结后不可变；其验证器版本被取代时退役 |
| `MechanismVerificationProfile` | 版本化的、由人负责的机制比较策略：问题划分、种子、显著性、容差、基线规则 | 验证器（人） | 只能通过经评审的变更修改；版本化 |
| `MechanismVerification` | 在留出问题上将候选机制与基线机制进行的一次比较；是一个结果携带 `Verdict` 的 `Operation` | 机制验证 | 操作状态：`queued \| running \| succeeded \| failed \| cancelled` |
| `ReplayScreening` | 一种低成本预筛选：将已存储的候选通过新的提交过滤器或验证配置回放，以已标注结果衡量精确率/召回率 | 机制验证 | 操作；绝不作为采纳证据 |
| `MechanismMetrics` | 按 Bot 分段和验证器版本计算的机制修订版适应度，从 H 推导 | 从实验记录推导（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）；在此定义 | 随 H 增长重新计算 |
| `LedgerEntry` | 一条第 2 级（或第 3 级）实验记录 | [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) | 仅追加 |
| `GateDecision`、`RiskTier`、`ReviewItem`、`Promotion` | 共享的晋升类型，在此配合机制门禁配置使用 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) | 同该文档 |

### 2.3 `MechanismRevision`

机制修订版是一个被当作 Bot 基因组修订版完全同等对待的进化策略版本：不可变、内容
寻址、带有父版本和来源信息，只能通过类型化补丁修改。其 id 是机制内容（注册记录、
智能体定义摘要、默认参数）的 RFC 8785 规范形式的 SHA-256；人类可读的 `version`
扮演的角色相当于基因组中每个 Bot 的 `seq`。

```python
from dataclasses import dataclass
from typing import Literal

MechanismStatus = Literal["draft", "candidate", "accepted", "rejected", "promoted", "archived"]

@dataclass(frozen=True)
class MechanismRevision:
    id: str                          # "sha256:…" over canonical mechanism content
    family: str                      # strategy id, e.g. "clawevolve/bot-evolution"
    version: str                     # human handle, e.g. "2.0.0"; not an identity
    target_kind: Literal["bot_genome"]  # what this mechanism improves (level 2)
    registration: dict               # the StrategyRegistration record (03-strategy.md)
    agent_definitions: dict[str, str]   # definition name → content digest
    default_params: dict             # thresholds, round limits, per-step models and budgets
    parents: list[str]               # mechanism revision ids
    created_by: dict                 # {"kind": "human" | "meta_run", ...}
    patch_from_parent: str | None    # digest of the MechanismPatch; None for human-registered versions
    status: MechanismStatus
    verifier_version: str | None     # verifier version of the evidence that adopted it; None until verified
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "sha256:5b0e…",
  "family": "clawevolve/bot-evolution",
  "version": "2.0.1",
  "target_kind": "bot_genome",                       // the loop is generic over what it improves
  "agent_definitions": {
    "clawevolve-tune": "sha256:91aa…",               // tune prompt changed: new digest
    "clawevolve-review": "sha256:0c7d…"
  },
  "default_params": {
    "max_rounds": 3,
    "submission_filter": {"min_train_gain_pct": 2, "max_regressed_ratio_pct": 10}
  },
  "parents": ["sha256:3f12…"],                       // 2.0.0
  "created_by": {"kind": "meta_run", "run_id": "run_m12", "actor": "platform/meta-evolve@0.1.0"},
  "patch_from_parent": "sha256:d27f…",
  "status": "candidate",
  "verifier_version": null
}
```

机制本身（一个修订版固定了什么）在 §3 中描述。

### 2.4 `MechanismPatch`

```python
@dataclass(frozen=True)
class MechanismOp:
    op: Literal["param.set", "prompt.edit", "operator.add", "flow.edit", "plugin.replace"]
    target: str                      # param path, agent definition file, operator library, or flow step
    value: object | None = None      # for param.set / plugin.replace
    edits: list[dict] | None = None  # for prompt.edit / operator.add / flow.edit (same edit kinds as genome file.edit)

@dataclass(frozen=True)
class MechanismPatch:
    patch_schema: int
    family: str
    base: str                        # mechanism revision id; must equal the parent (compare-and-swap on record)
    ops: list[MechanismOp]
    rationale: str
    evidence: list[str]              # ledger entry ids the proposal is based on
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch_schema": 1,
  "family": "clawevolve/bot-evolution",
  "base": "sha256:3f12…",
  "ops": [
    {"op": "param.set", "target": "submission_filter.min_train_gain_pct", "value": 2},
    {"op": "prompt.edit", "target": "agents/clawevolve-tune/SKILL.md",
     "edits": [{"kind": "replace_section", "heading": "## Persona edits", "content": "Prefer one itemized change per finding; …"}]}
  ],
  "rationale": "Persona rewrites from the tune step were rejected in 61% of experiments on support bots",
  "evidence": ["ledger:le_5512", "ledger:le_5530", "ledger:le_5601"]
}
```

机制补丁的候选 id 是其内容哈希（例如 `sha256:d27f…`），与基因组补丁完全相同，
因此重新提交会返回相同的 id。人工注册的进化策略版本没有补丁；当它被提交采纳时，
其候选 id 就是其机制修订版 id。

### 2.5 `ImprovementProblem`

一个冻结的、可回放的第 2 级任务。问题由 H 构建：真实的历史实验，连同其输入一起
冻结。也可以添加合成问题，例如故意降级且已知修复方法的基因组。

```python
@dataclass(frozen=True)
class ImprovementProblem:
    id: str
    bot_genome_revision: str         # starting system S
    experience_snapshot: str         # episodes / feedback the mechanism may see
    visible_suites: list[str]        # visible to the mechanism under normal level-2 rules
    hidden_suites: list[str]         # used only to score the outcome
    budget: dict                     # identical for every mechanism compared
    segment: dict                    # engine and bot type, for stratification
    source: Literal["ledger", "synthetic"]
    source_entry: str | None         # ledger entry it was frozen from; None for synthetic problems
    split: Literal["mechanism_train", "mechanism_holdout"]
    verifier_version: str
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "prob_204",
  "bot_genome_revision": "sha256:a90b…",          // starting system S (r41 of a support bot)
  "experience_snapshot": "snapshot:exp_77",       // episodes / feedback the mechanism may see
  "visible_suites": ["train", "validation"],      // visible to the mechanism under normal rules
  "hidden_suites": ["holdout", "regression", "safety"], // used only to score the outcome
  "budget": {"max_usd": 20, "max_rollouts": 400}, // identical for every mechanism compared
  "segment": {"engine": "openclaw", "bot_type": "support"},
  "source": "ledger",
  "source_entry": "ledger:le_5512",
  "split": "mechanism_holdout",
  "verifier_version": "verifier@4"
}
```

### 2.6 `MechanismVerificationProfile`

```python
@dataclass(frozen=True)
class MechanismVerificationProfile:
    id: str                          # e.g. "mechanism-default@1"
    seeds_per_problem: int
    min_holdout_problems: int
    significance: str                # e.g. "0.05"; strings, no floats in hashed content
    min_yield_gain_pct: int          # lower confidence bound of the yield gain must clear this
    max_regression_rate_increase_pct: int
    max_false_acceptance_increase_pct: int
    cost_tolerance_pct: int
    require_budget_matched_baseline: bool
    canary_share_pct: int            # 0 = no mechanism-level canary
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "mechanism-default@1",
  "seeds_per_problem": 3,
  "min_holdout_problems": 30,
  "significance": "0.05",
  "min_yield_gain_pct": 1,
  "max_regression_rate_increase_pct": 0,
  "max_false_acceptance_increase_pct": 0,
  "cost_tolerance_pct": 10,
  "require_budget_matched_baseline": true,
  "canary_share_pct": 0
}
```

### 2.7 `MechanismVerification` 及其判定

```python
@dataclass(frozen=True)
class ProblemOutcome:
    problem: str
    seed: int
    mechanism: str                   # mechanism revision id
    level2_run: str                  # the sandboxed level-2 run
    accepted_candidate: str | None   # what bot verification would have promoted; None if nothing passed
    hidden_gain_pct: int             # verified gain on the hidden suites (0 if nothing accepted)
    regressed: bool                  # accepted candidate regressed on hidden regression/safety
    cost_usd_cents: int

@dataclass(frozen=True)
class MechanismVerification:
    id: str
    family: str
    candidate: str                   # mechanism revision id under test (M′)
    baseline: str                    # usually the family's active revision (M1)
    profile: str
    verifier_version: str
    operation: str                   # operation id; status by id
    verdict: Literal["pending", "accept", "reject", "inconclusive"]
    comparison: dict | None          # paired statistics per mechanism metric, once finished
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "mv_31",
  "family": "clawevolve/bot-evolution",
  "candidate": "sha256:5b0e…",
  "baseline": "sha256:3f12…",
  "profile": "mechanism-default@1",
  "verifier_version": "verifier@4",
  "operation": "op_19a",
  "verdict": "accept",
  "comparison": {
    "problems": 34, "seeds": 3,
    "yield_gain_pct": {"mean": "3.1", "ci_lower": "1.4", "ci_upper": "4.8"},   // paired per-problem differences
    "regression_rate_pct": {"baseline": "2.9", "candidate": "2.0"},
    "false_acceptance_rate_pct": {"baseline": "5.9", "candidate": "5.9"},
    "cost_per_accepted_usd": {"baseline": "6.40", "candidate": "6.75"},
    "budget_matched_baseline": {"yield_gain_pct": {"mean": "2.2", "ci_lower": "0.6"}}
  }
}
```

### 2.8 `MechanismMetrics`

机制的适应度，按机制修订版、按 Bot 分段、按验证器版本计算（指标从不跨验证器版本
比较，§10）。本文档负责定义；实验记录
（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）将其存储为使用相同
字段名的派生视图。

| 指标 | 定义 |
| --- | --- |
| **经验证的改进收益** | 已晋升的 S2 相对 S1 在留出套件和回归套件上经验证的平均增益，按单位成本计 |
| **接受率** | 已提交候选中被验证接受的比例 |
| **误接受率** | 晋升后被回滚（回到旧版本）或在线上出现回归的比例 |
| **回归率** | 在安全套件和回归套件上出现回归的候选比例 |
| **每项被接受改进的成本** | 总花费除以被接受的候选数 |
| **后代生产力** | 被接受候选*之后*的谱系持续改进的程度。Huxley-Gödel Machine 表明，它比单一分数更能预测长期进展 |

```python
@dataclass(frozen=True)
class MechanismMetrics:
    mechanism: str
    segment: dict
    verifier_version: str
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

负面结果是这些指标的一等输入：被拒绝的候选、回归、回到旧版本以及浪费的预算都
记录在 H 中。正是这一点让元策略能够学到“算子 X 在类型 Y 的 Bot 上屡屡失败”，
就像 ClawEvolve 的算子库所期望的那样。

## 3. 机制是什么

一个机制 M 是一个**进化策略版本**（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）
加上决定其行为的一切：

| 机制组件 | ClawEvolve 中的现有示例 |
| --- | --- |
| 轮次结构（循环限制、停止规则） | `bot_evolution` 阶段序列、`maxRounds` |
| 提议提示词和算子库 | `_build_tune_prompt`、`references/mutation_operator_library.json` |
| 分析启发式 | 诊断批大小、好/坏比例、根因聚类 |
| 提交过滤器（什么值得提交） | `test > baseline`、`FULL_OPT_MAX_REGRESSED_RATIO`、配对胜率阈值 |
| 默认绑定参数 | 父版本选择、窗口、每次运行的预算 |
| 每个步骤的模型选择和预算 | tune/review/judge 模型、轮次预算 |

ClawEvolve 已经在第 3 级上手工改进其机制：`scripts/calibrate_evolution_gates.py`
和 `scripts/replay_candidate_gate.py` 回放历史候选以调整接受阈值，而 tune 提示词
携带了进化历史。平台让这个循环变得显式、经过验证且可插拔。ClawEvolve 本身如何
成为一个进化策略，见 [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)。

**机制基因组。** 同一套注册表、引用、归档和晋升模型同时服务于两种工件。循环对其
所改进的对象是通用的：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"target_kind": "bot_genome"}   // or "mechanism"
```

智能体定义（提示词、技能、工具配置）已经随进化策略版本一同发布，在注册时以内容
寻址方式存储，并在每次运行中按摘要记录（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。
因此，修改过的 tune 提示词已经是一个带有新摘要的新进化策略版本，H 中的结果也已经
可以归因到所使用的确切提示词。第 3 级在此之上增加了谱系、引用和补丁。

**机制补丁可以改变什么（提议）。** 来源列出了五种变更：编辑流程、替换插件版本、
编辑提示词、修改参数、添加算子。它们对应 §2.4 的补丁操作：

| 操作 | 变更内容 | 示例 |
| --- | --- | --- |
| `param.set` | 机制的某个默认参数或阈值 | `submission_filter.min_train_gain_pct: 2` |
| `prompt.edit` | 智能体定义内的某个文件（与基因组 `file.edit` 的编辑类型相同） | 重写 tune 提示词中关于人设编辑的部分 |
| `operator.add` | 进化策略算子库中的一个条目 | 添加一个“拆分过大技能”算子 |
| `flow.edit` | 步骤顺序或停止规则（前提是进化策略将其作为数据暴露） | 在训练评估之前运行 review |
| `plugin.replace` | 一个固定的步骤或插件版本（对于组合式进化策略，是步骤版本） | `team-x/root-cause-analyzer@1` → `@2` |

对于黑盒进化策略，只有该进化策略以数据形式暴露的内容（参数、智能体定义、算子文件、
流程描述）可以被元策略修补。对进化策略自身代码的修改是人工编写的新版本，同样要经过
机制验证（§8）。这一边界是待定决策 O-1。

**机制引用。** 引用按进化策略家族划定作用域。`clawevolve/bot-evolution` 拥有
`active`、`previous`、`canary` 和 `candidate/*` 引用，Bot 和租户通过其进化策略配置
跟随或固定这些引用：

| 引用 | 含义 | 由谁移动 |
| --- | --- | --- |
| `active` | 跟随该引用的绑定所发起的新第 2 级运行使用的机制 | 仅机制晋升 |
| `previous` | 上一个 `active`，保留用于快速回到旧版本 | 仅机制晋升 |
| `canary` | 机制金丝雀期间部分第 2 级运行使用的机制 | 仅机制晋升 |
| `candidate/<run>/<n>` | 某次元运行的候选机制 | 编排器 |

回到旧版本与基因组相同：再次晋升一个较早的机制修订版。没有单独的回滚路径。

## 4. 第 2 级必须事先提供的内容

第 3 级不在第一次迭代中，但只有在第 2 级契约已经携带正确字段时它才能工作。以下是
这些挂钩；它们都不会在第 2 级自身需求之外增加第一次迭代的范围。

| 第 2 级契约 | 对第 3 级的要求 | 位置 |
| --- | --- | --- |
| 运行冻结其进化策略版本 | 每次运行记录确切的机制修订版（版本加智能体定义摘要）；进行中的运行用其启动时的机制完成 | [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) |
| 进化策略版本不可变且内容寻址 | 智能体定义在注册时按摘要存储；修改过的提示词即新版本 | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| 实验记录 H 从第一天起记录每一次实验，包括被拒绝的候选和后续线上结果 | 每条记录都包含机制修订版、证据、补丁、判定、成本、采纳情况、线上结果和验证器版本 | [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) |
| 每个判定都记录验证器版本 | 使机制指标只在同一验证器版本内比较 | [07-verification.zh-CN.md](07-verification.zh-CN.md) |
| 经验可以作为冻结快照寻址 | 使改进问题对每个被比较的机制回放相同的片段（提议的要求） | [02-experience.zh-CN.md](02-experience.zh-CN.md) |
| 修订版/引用/补丁机制对 `target_kind` 通用 | 使机制复用它，而非第二套实现 | [01-genome.zh-CN.md](01-genome.zh-CN.md) |
| 绑定指明进化策略家族和版本 | 使绑定日后可以跟随家族的 `active` 机制引用，而非固定版本（提议的绑定形式，待定决策 O-2） | [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) |
| 权力分立明确提及第 3 级 | 修改验证器的权力只属于人；采纳机制的权力属于机制门禁 + 人工批准 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |

## 5. 第 3 级循环

骨架与第 2 级相同。目标是机制，评估者是机制验证。

1. **触发**：定时、H 中积累了足够多的新实验、某项机制指标下降（例如接受率下降），
   或手动触发。
2. **选择父机制**：通常是某个进化策略家族的 `active` M1。
3. **分析 H**：在失败或浪费的实验中寻找模式（收益低的算子、放过回归的阈值、消耗
   预算却无效果的步骤）。
4. **元提议**：一个**元策略**（本身也是进化策略，例如获得 H 文件系统导出的编码
   智能体，即 Meta-Harness 模式）产出一个**机制补丁**：调整阈值、重写 tune 提示词
   中关于人设编辑的部分、添加算子、更换评估器组合、重新排列步骤。
5. **静态检查**：补丁不得触及**验证器边界**（§10），必须保持在声明的能力范围内，
   并且必须通过一致性检查（§7）。
6. **机制验证**：§8。
7. **采纳或拒绝**：M2 只有通过门禁（默认需人工批准，§10）才能成为该进化策略家族的
   `active` 机制。
8. **移交**：跟随该家族 `active` 引用的 Bot 的后续第 2 级运行使用 M2。固定版本的
   Bot 保留 M1。已在进行中的运行用其启动时的机制完成，因为运行会冻结其机制版本。

```text
H ──► meta-run (meta-strategy) ──► mechanism patch ──► static checks ──► mechanism verification
                                                                                 │
                       pinned bindings keep M1                                   ▼
level-2 runs ◄── hand over to M2 ◄── promote family `active` ◄── human approval (T3) ◄── verdict accept
     │
     └──► new experiments recorded in H
```

### 5.1 元策略

元策略是同一端口（`run(ctx)`，[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）上的
普通进化策略，其目标是机制。它与第 2 级进化策略有三点不同（提议）：

- 它的运行以**进化策略家族**而非 Bot 为目标：元运行的父版本是一个机制修订版，
  `ctx.parent` 是该修订版的视图。
- 它通过一个**第 3 级能力**读取 H，提议名称为 `ledger.read@1`
  （在 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 的能力目录中列为后续、第 3 级；
  第一次迭代中不授予），
  该能力提供限于可见数据的实验记录文件系统导出（Meta-Harness 发现原始历史优于
  摘要）。它从不包含隐藏套件的用例。
- 它通过 `ctx.candidates.submit` 提交**机制补丁**。候选的判定来自机制验证，而非
  Bot 验证。

元策略可以以其他进化策略家族为目标，但绝不能以自身为目标（§10）。

### 5.2 元运行

元运行是一个 `Run`，具有与第 2 级运行相同的生命周期、租约、预算、幂等性和崩溃恢复
（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。第 3 级使用独立的预算，
与任何 Bot 的预算分开，因为每次机制比较都要运行许多第 2 级运行（§8.3）。紧急停止
开关照常适用：禁用一个元策略即在所有地方禁用它，全局开关会连同其他一切一起暂停
元运行。

## 6. 机制候选与采纳

机制候选只有通过专用的**机制门禁配置**才能成为 `active`
（[08-promotion.zh-CN.md](08-promotion.zh-CN.md) 负责门禁；这是它的第 3 级配置）：

1. **静态底线**：模式有效；`base` 等于父版本；补丁不触及验证器边界（§10）；生成的
   注册记录仍然只声明目录中的能力；智能体定义对每个声明的引擎仍然有效。
2. **一致性**：候选机制像任何新进化策略版本一样通过进化策略一致性测试套件
   （[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。
3. 在该家族的机制验证配置下，**机制验证判定**为 `accept`（§8）。
4. **默认风险等级 T3**：始终需要人工批准，并附上比较报告。所有者可以针对其自己租户上
   仅涉及参数的补丁（阈值、预算）将其降为 T2（对于共享家族，“自己的租户”含义为
   待定决策 O-3）。
5. **可选的机制金丝雀**（§8.4），然后晋升该家族的 `active` 引用。

每一步都是 H 中经审计的事件（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）：
候选、验证、评审和晋升。

## 7. 机制补丁的静态检查

在提交时、在验证花费任何资金之前运行：

| 检查 | 拒绝 |
| --- | --- |
| 验证器边界 | 任何目标为套件、划分、评分器、验证配置、机制验证配置、门禁底线、显著性阈值或元验证协议的操作 |
| 策略与安全包络 | 任何触及预算、紧急停止开关、风险等级表、锁定基因或 Bot 基因组 `policy` 部分的操作 |
| 自引用 | 来自元运行、且其家族等于该元策略自身家族的补丁 |
| 能力 | 生成的注册记录的 `needs` 增加了能力，或指明了目录之外的能力 |
| 隐藏数据引用 | 引用隐藏套件 id 或用例内容的提示词编辑（与 Bot 补丁中针对类护栏文本的差异审计相同） |
| 模式与基准 | 无效操作，或 `base` 不等于父版本（比较并交换） |

## 8. 机制验证

问题不是“S′ 是否优于 S1”，而是“**在两者都未针对其调优过的问题上，M2 产生的经验证
改进是否优于 M1？**”

### 8.1 用验证的术语表述

机制验证使用与 Bot 验证（[07-verification.zh-CN.md](07-verification.zh-CN.md)）相同的
验证器机制，只是上升了一级：

- **对象**是一个机制修订版。一个**用例**是一个*改进问题*（冻结的 Bot 基因组 +
  经验快照 + 套件 + 预算，§2.5）。
- 一次**执行**是在沙箱中进行的完整第 2 级运行，其输出本身由 Bot 验证进行验证。
- **分数**是在问题的隐藏套件上经验证的改进收益，以及成本、回归率和误接受率。
- **比较器**是同一套配对统计机制，粒度为问题级。

### 8.2 协议

1. 将改进问题划分为 **mechanism-train**（元策略可以看到结果）和
   **mechanism-holdout**（仅在此处使用），按引擎和 Bot 类型分层。留出问题应来自
   元策略从未见过的 Bot。
2. 在相同的留出问题上，以**相同预算**和多个种子运行 M1 和 M′。每次运行都是在沙箱
   评估 Bot 中进行的完整第 2 级运行，因此其输出由 Bot 验证以与生产环境完全相同的
   方式验证。任何真实 Bot 的引用都不会移动：第 2 级运行在沙箱副本上工作，其被接受
   的候选只被评分，从不被晋升。
3. 在问题的隐藏套件上为每个结果评分。用配对统计（逐问题配对差值和置信区间，而非
   原始平均值）比较 §2.8 中的机制指标。
4. **仅当**收益显著提升、回归率和误接受率不上升、且成本保持在容差范围内时，**才
   采纳 M′**。可选地要求 M′ 击败一个**预算匹配基线**，即给予 M′ 额外花费的 M1。
5. **机制级金丝雀**（可选）：离线采纳之后，让 M2 在一部分真实第 2 级运行中与 M1
   并行运行，并在完全移交之前比较线上收益。

判定策略（提议；阈值来自配置）：

```text
accept       ⇔ static floor ok ∧ conformance ok
               ∧ CI_lower(Δ yield) ≥ min_yield_gain
               ∧ Δ regression rate ≤ max_regression_rate_increase
               ∧ Δ false-acceptance rate ≤ max_false_acceptance_increase
               ∧ Δ cost within cost_tolerance
               ∧ (beats budget-matched baseline, when required)
reject       ⇔ any floor failure ∨ CI_upper(Δ yield) < min_yield_gain ∨ regression or false-acceptance rate rises
inconclusive ⇔ otherwise  → more problems or seeds, or stop
```

### 8.3 成本与代理指标

这代价高昂，因为每次比较都要运行许多第 2 级运行。因此第 3 级很少运行，且使用独立的
预算。

允许使用低成本代理指标对候选进行*预筛选*。一个例子是将已存储的候选通过新的提交
过滤器或验证配置回放，并以已标注结果衡量精确率/召回率，正如
`calibrate_evolution_gates.py` 和 `replay_candidate_gate.py` 已经做的那样；它们
成为筛选工具（`ReplayScreening`）。**代理指标绝不允许用于采纳。** 筛选不等于采纳。

注意回放可以筛选的两种变更之间的区别：

- 对进化策略**提交过滤器**的修改是机制变更。它通过回放进行筛选，且只能通过 §8.2
  采纳。
- 对**验证配置**的修改是验证器变更。回放筛选为编写它的人提供参考；它只能通过经
  评审的人工变更采纳（§10），绝不由元循环采纳。

### 8.4 机制金丝雀

可选，按配置决定（`canary_share_pct`）。离线接受并批准之后，该家族的 `canary`
引用指向 M2，跟随该引用的绑定的真实第 2 级运行中有该比例从 `canary` 而非 `active`
启动。在 `active` 移动之前，从 H 比较线上收益。固定版本的 Bot 从不参与金丝雀。

## 9. 改进问题

- **由 H 构建。** 一个问题从一次真实的历史实验冻结而来：其起始基因组修订版、机制
  可以看到的经验（作为快照）、套件和预算。冻结不会复制任何已经内容寻址的内容；它
  只记录 id。
- 可以添加**合成问题**：故意降级且已知修复方法的基因组。
- **划分并分层**为 `mechanism_train` 和 `mechanism_holdout`，按引擎和 Bot 类型分层。
  留出问题来自元策略从未见过的 Bot。
- **标注验证器版本。** 当套件发生变化时，在旧验证器版本下冻结的问题不会与新问题
  混用。
- **进化策略的回归测试。** 即使没有任何自动化元策略，这个基准也很有价值：对进化
  策略的人工修改（例如新的 ClawEvolve tune 提示词）可以通过在留出问题上与当前机制
  比较经验证的改进收益来决定接受或拒绝。

## 10. 边界：递归不得触及的内容

递归需要一个固定的锚点。没有它，系统就可以通过削弱自己的评判者来“改进”，这正是
Darwin Gödel Machine 的失败案例（它禁用了自己的幻觉检查器）。

| 任何自动化循环都绝不修改 | 原因 |
| --- | --- |
| **验证器**：套件、划分、评分器、平台门禁底线、元验证协议、显著性阈值 | 它定义了“更好”。如果循环可以编辑它，增益就失去意义 |
| 锁定基因和 Bot 基因组的 `policy` 部分 | 所有者权限 |
| 预算、紧急停止开关、风险等级表 | 安全包络 |
| 元策略修改自身或其自身验证器的能力 | 防止无界自引用 |

规则：

- **深度上限为 2。** M 改进 S；元策略改进 M。元策略可以以其他进化策略家族为目标，
  但绝不能以自身为目标。它自身的变更由人编写，或者在人工批准下，由*另一个*元策略
  经过相同的机制验证协议完成。
- **机制采纳默认为风险等级 T3**：始终需要人工批准，并附上比较报告。所有者可以针对
  其自己租户上仅涉及参数的补丁（阈值、预算）将其降为 T2。
- **验证器只通过人工编写、经评审的变更进化。** 它可以从 H *获得信息*（例如“回归
  套件漏掉了失败类别 Z”），但这样的发现是给人的建议，绝不是自动编辑。
- **验证器变更会破坏可比性。** 当套件发生变化时，H 中的实验会标注验证器版本，
  机制指标只在同一验证器版本内比较。
- **隐藏数据在上一级同样保持隐藏。** 元策略的输入从不包含留出、回归或安全用例，
  也不包含 mechanism-holdout 问题的结果。这由编排器强制执行，而不是靠提示词。

这些边界通过结构强制执行：通过 §7 的静态检查，通过让验证器位于任何循环可修补的工件
之外，以及通过 [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中的权力分立（修改验证器
的权力只属于人；采纳机制的权力只属于机制门禁加人工批准）。

## 11. 分阶段

第 3 级依赖于已填充的 H 和可信的第 2 级验证器。在这些具备之前构建它只会优化噪声。

1. **从第一天起记录 H**（第一次迭代，与第 2 级工作一起）。它成本低廉，是下面所有
   内容的前提。
2. **离线回放**（P4）：将 `calibrate_evolution_gates.py` /
   `replay_candidate_gate.py` 移植为基于 H 的回放工具，用于验证配置和提交过滤器的
   变更。由人采纳。
3. **改进问题基准**（P5）：从 H 冻结问题，并对人工编写的机制变更运行机制验证协议。
   仅此一项就很有价值：它是进化策略的回归测试（[work-items.zh-CN.md](work-items.zh-CN.md)
   中的工作项 RSI-23）。
4. **自动化元策略**（P6）：让元策略提出机制补丁，只能通过 §8 和人工批准采纳，并由
   静态检查强制执行 §10 的边界（RSI-24）。

## 12. 服务接口

以下所有接口均为提议，且仅属于第 3 级。

```python
from typing import Protocol

class MechanismRegistry(Protocol):
    """Mechanism revisions and refs per strategy family. Implemented by the Strategy
    Registry (03-strategy.md) on the shared revision/ref/patch machinery."""

    async def get_revision(self, family: str, revision: str) -> MechanismRevision: ...
    async def list_revisions(self, family: str, *, status: str | None = None) -> list[MechanismRevision]: ...
    async def refs(self, family: str) -> dict[str, str]:
        """Ref name → mechanism revision id."""
    async def record_candidate(self, patch: MechanismPatch, *, run_id: str) -> str:
        """Static checks (§7), then record a candidate revision. Idempotent:
        returns the patch's content hash as candidate id."""
    async def promote(self, family: str, revision: str, *, expected_active: str,
                      reason: str, actor: str) -> Promotion:
        """Move `active` (and `previous`) by compare-and-swap. Only the mechanism
        gate calls this; going back is promoting an earlier revision."""
    def resolve(self, family: str, ref_or_version: str) -> MechanismRevision:
        """Resolve a binding's `@active` / `@canary` / `@2.0.0` at run start;
        the run freezes the result."""


class ImprovementProblems(Protocol):
    """Frozen improvement problems built from H."""

    async def freeze(self, *, source_entry: str, split: str, budget: dict,
                     idempotency_key: str) -> ImprovementProblem: ...
    async def get(self, problem: str) -> ImprovementProblem: ...
    async def list(self, *, split: str | None = None, segment: dict | None = None,
                   verifier_version: str | None = None) -> list[ImprovementProblem]: ...


class MechanismVerifier(Protocol):
    """Mechanism verification (§8). Verifier-owned, read-only to every loop."""

    async def start_verification(self, *, family: str, candidate: str, baseline: str,
                                 profile: str, idempotency_key: str) -> str:
        """Start M′ vs M1 on mechanism-holdout problems. Returns an operation id at once."""
    async def get_verification(self, verification: str) -> MechanismVerification: ...
    async def start_replay(self, *, family: str, candidate: str, idempotency_key: str) -> str:
        """Replay screening (§8.3). Returns an operation id. Never adoption evidence."""
    async def get_replay(self, replay: str) -> dict: ...


class MechanismMetricsReader(Protocol):
    """Derived from the Experiment Ledger (05-experiment-ledger.md)."""

    async def metrics(self, family: str, *, mechanism: str | None = None,
                      segment: dict | None = None,
                      verifier_version: str) -> list[MechanismMetrics]:
        """verifier_version is required: metrics are never mixed across verifier versions."""


class MechanismGate(Protocol):
    """Level-3 gate profile (08-promotion.md holds the gate)."""

    async def decide(self, family: str, candidate: str) -> GateDecision: ...
    async def approve(self, family: str, candidate: str, *, reason: str, actor: str) -> Promotion: ...
    async def reject(self, family: str, candidate: str, *, reason: str, actor: str) -> GateDecision: ...


class LedgerRead(Protocol):
    """Proposed level-3 capability `ledger.read@1` on StrategyContext, granted only to
    meta-strategies that declare it."""

    async def export(self, *, family: str, since: str | None = None) -> "Workspace":
        """Materialise a filesystem export of visible ledger data into a sandbox
        workspace. Never contains hidden cases or mechanism-holdout outcomes."""
```

## 13. API

所有端点均为**提议、第 3 级、不在第一次迭代中**。它们遵循
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) 的共享约定：前缀
`/openapi/v1`（下文路径均相对于它）、JSON 请求体、每个创建资源的 POST 都带
`Idempotency-Key` 请求头、可变资源使用 ETag、长时工作作为操作处理（`202` + 操作 id，
按 id 查询状态，不保持请求连接）。下文的响应展示的是标准信封中的 `data` 负载；
信封、错误、分页和幂等性参见 [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。

`{id}` 是进化策略家族 id，例如 `clawevolve/bot-evolution`，在路径段中进行百分号编码
（`clawevolve%2Fbot-evolution`），与 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 中
Strategy Registry 端点的做法相同。

调用方：人工操作员和研究人员（UI、`avn`），以及在进化策略变更时运行改进问题基准的
流水线/CI。Bot 调用方随 DR-3 推迟，不是此接口面的调用方。

### GET /evolution/strategies/{id}/mechanism/revisions

列出某个家族的机制修订版及其谱系和状态。由 UI 和 `avn` 调用。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism/revisions?status=candidate
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {"id": "sha256:5b0e…", "version": "2.0.1", "parents": ["sha256:3f12…"],
     "status": "candidate", "created_by": {"kind": "meta_run", "run_id": "run_m12"}}
  ]
}
```

错误：`404` 未知家族。

### GET /evolution/strategies/{id}/mechanism/revisions/{rev}

返回一个机制修订版（§2.3）。`{rev}` 是修订版 id 或版本号。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism/revisions/2.0.1
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "sha256:5b0e…", "family": "clawevolve/bot-evolution", "version": "2.0.1",
  "target_kind": "bot_genome",
  "agent_definitions": {"clawevolve-tune": "sha256:91aa…", "clawevolve-review": "sha256:0c7d…"},
  "default_params": {"max_rounds": 3, "submission_filter": {"min_train_gain_pct": 2, "max_regressed_ratio_pct": 10}},
  "parents": ["sha256:3f12…"], "patch_from_parent": "sha256:d27f…",
  "status": "candidate", "verifier_version": null
}
```

错误：`404` 未知家族或修订版。

### GET /evolution/strategies/{id}/mechanism/refs

返回该家族的机制引用。响应携带 `ETag`。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism/refs
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "active": "sha256:3f12…",
  "previous": "sha256:2e80…",
  "canary": null,
  "candidate/run_m12/1": "sha256:5b0e…"
}
```

### POST /evolution/strategies/{id}/mechanism/promotions

将该家族的 `active` 引用移动到某个机制修订版；回到旧版本即晋升一个较早的修订版。
由机制门禁在批准后调用，或由操作员调用以回到旧版本。对 `expected_active` 执行比较
并交换。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism/promotions
// Idempotency-Key: promote-clawevolve-2.0.0-after-incident-1182
{
  "revision": "sha256:3f12…",          // going back to 2.0.0
  "expected_active": "sha256:5b0e…",
  "reason": "False-acceptance rate rose on the mechanism canary"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "promotion_id": "mp_07",
  "family": "clawevolve/bot-evolution",
  "active": "sha256:3f12…",
  "previous": "sha256:5b0e…",
  "actor": "user:owner_42",
  "at": "2026-10-09T08:00:00Z"
}
```

错误：`409` `expected_active` 已过期；`403` 调用方无权晋升机制；`422` 该修订版没有
已接受的机制验证（仅针对向前晋升；回到之前晋升过的修订版不需要新的验证）。

### GET /evolution/mechanism-candidates/{candidate}

机制候选的采纳报告：补丁、静态检查、机制验证比较结果和门禁决定。由评审者调用
（UI、`avn evolve mechanism review show`）。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/mechanism-candidates/sha256:d27f…
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate": "sha256:d27f…",
  "family": "clawevolve/bot-evolution",
  "revision": "sha256:5b0e…",
  "base": "sha256:3f12…",
  "patch_ops": 2,
  "static_checks": {"verifier_boundary": "ok", "capabilities": "ok", "self_reference": "ok", "conformance": "ok"},
  "verification": {"id": "mv_31", "verdict": "accept", "yield_gain_ci_lower_pct": "1.4"},
  "gate": {"decision": "needs_review", "risk_tier": "T3", "reasons": ["mechanism adoption is T3 by default"]}
}
```

错误：`404` 未知候选。

### GET /evolution/mechanism-review-queue

等待人工批准的机制候选。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/mechanism-review-queue?family=clawevolve%2Fbot-evolution
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {"candidate": "sha256:d27f…", "family": "clawevolve/bot-evolution", "risk_tier": "T3",
     "verification": "mv_31", "verdict": "accept", "queued_at": "2026-10-09T06:40:00Z"}
  ]
}
```

### POST /evolution/mechanism-candidates/{candidate}:approve

人工批准。如果配置中没有机制金丝雀，批准会晋升该家族的 `active` 引用；否则会先
移动 `canary`。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/mechanism-candidates/sha256:d27f…:approve
// Idempotency-Key: approve-sha256-d27f
{"reason": "Yield gain holds on all four segments; cost within tolerance"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"candidate": "sha256:d27f…", "status": "promoted", "promotion_id": "mp_06", "active": "sha256:5b0e…"}
```

错误：`409` 判定不是 `accept`，或自验证开始以来 `active` 已经移动（需针对新的
`active` 重新验证）；`403` 不是机制批准者。

### POST /evolution/mechanism-candidates/{candidate}:reject

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/mechanism-candidates/sha256:d27f…:reject
// Idempotency-Key: reject-sha256-d27f
{"reason": "Gain concentrated in one segment; resubmit with stratified evidence"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"candidate": "sha256:d27f…", "status": "rejected"}
```

### POST /evolution/strategies/{id}/meta-runs

启动一次元运行：针对该家族运行一个元策略。幂等；返回 `202` 及运行 id。元运行是
进化运行（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）的一个 `Run`；
通过下面的端点按 id 查询状态。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/meta-runs
// Idempotency-Key: meta-clawevolve-2026-10-09
{
  "meta_strategy": "platform/meta-evolve@0.1.0",
  "parent": "active",
  "budget": {"max_usd": 200, "max_wall_clock_s": 86400},
  "params": {"ledger_window_days": 90}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_m12", "status": "queued"}
```

错误：`422` 元策略自身的家族等于 `{id}`（自引用，§10）；`403` 调用方无权启动元
运行；`409` 幂等键被以不同请求体重用。

### GET /evolution/strategies/{id}/meta-runs/{run}

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/meta-runs/run_m12
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "run_id": "run_m12",
  "status": "completed",
  "meta_strategy": "platform/meta-evolve@0.1.0",
  "parent": "sha256:3f12…",
  "budget": {"max_usd": 200, "spent_usd": "143.20"},
  "candidates": ["sha256:d27f…"]
}
```

### GET /evolution/strategies/{id}/mechanism-metrics

针对单个验证器版本，按修订版和分段给出的机制指标。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism-metrics?verifier_version=verifier@4&engine=openclaw
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "verifier_version": "verifier@4",
  "items": [
    {"mechanism": "sha256:3f12…", "segment": {"engine": "openclaw", "bot_type": "support"},
     "experiments": 412, "verified_yield_pct_per_usd": "0.31", "acceptance_rate_pct": "18.2",
     "false_acceptance_rate_pct": "5.9", "regression_rate_pct": "2.9",
     "cost_per_accepted_usd": "6.40", "descendant_productivity": "0.44"}
  ]
}
```

错误：`400` 缺少 `verifier_version`（指标从不跨验证器版本混合）。

### POST /evolution/improvement-problems

从一条实验记录条目冻结一个改进问题。仅限操作员。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/improvement-problems
// Idempotency-Key: freeze-le_5512
{
  "source_entry": "ledger:le_5512",
  "split": "mechanism_holdout",
  "budget": {"max_usd": 20, "max_rollouts": 400}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"id": "prob_204", "split": "mechanism_holdout", "verifier_version": "verifier@4"}
```

错误：`422` 该条目的经验已不再保留（无法生成快照），或其验证器版本已被取代。

### GET /evolution/improvement-problems

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/improvement-problems?split=mechanism_holdout&engine=openclaw&verifier_version=verifier@4&page=1&page_size=2
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 41,
  "items": [
    {"id": "prob_204", "segment": {"engine": "openclaw", "bot_type": "support"}, "source": "ledger"},
    {"id": "prob_219", "segment": {"engine": "openclaw", "bot_type": "sales"}, "source": "synthetic"}
  ]
}
```

对于元策略调用方，mechanism-holdout 问题只按 id 和分段列出；其结果从不向元策略
暴露。

### GET /evolution/improvement-problems/{problem}

返回一个问题（§2.5）。错误：`404`。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/improvement-problems/prob_204
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "prob_204", "bot_genome_revision": "sha256:a90b…", "experience_snapshot": "snapshot:exp_77",
  "visible_suites": ["train", "validation"], "hidden_suites": ["holdout", "regression", "safety"],
  "budget": {"max_usd": 20, "max_rollouts": 400},
  "segment": {"engine": "openclaw", "bot_type": "support"},
  "source": "ledger", "source_entry": "ledger:le_5512",
  "split": "mechanism_holdout", "verifier_version": "verifier@4"
}
```

### POST /evolution/mechanism-verifications

启动候选与基线之间的机制验证。返回 `202` 及操作 id。对于元运行候选，由机制门禁
自动调用；对于人工编写的进化策略版本，由操作员或 CI 调用。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/mechanism-verifications
// Idempotency-Key: mv-clawevolve-2.0.1-vs-2.0.0
{
  "family": "clawevolve/bot-evolution",
  "candidate": "sha256:5b0e…",
  "baseline": "active",
  "profile": "mechanism-default@1"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"verification_id": "mv_31", "operation_id": "op_19a", "status": "queued"}
```

错误：`422` 该家族各分段的 mechanism-holdout 问题少于 `min_holdout_problems`；
`409` 幂等键被以不同请求体重用。

### GET /evolution/mechanism-verifications/{verification}

状态，以及完成后的比较结果（§2.7）。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/mechanism-verifications/mv_31
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "mv_31", "operation": {"id": "op_19a", "status": "succeeded"},
  "candidate": "sha256:5b0e…", "baseline": "sha256:3f12…",
  "profile": "mechanism-default@1", "verifier_version": "verifier@4",
  "verdict": "accept",
  "comparison": {"problems": 34, "seeds": 3,
                 "yield_gain_pct": {"mean": "3.1", "ci_lower": "1.4", "ci_upper": "4.8"}}
}
```

### POST /evolution/replays

在 H 中已存储的候选上，启动对候选提交过滤器（或者，对于编写验证器变更的人，候选
验证配置）的回放筛选。返回 `202` 及操作 id。绝不作为采纳证据。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/replays
// Idempotency-Key: replay-filter-min-gain-2
{
  "family": "clawevolve/bot-evolution",
  "candidate": "sha256:5b0e…",
  "corpus": {"since": "2026-07-01T00:00:00Z", "labelled_only": true}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"replay_id": "rp_08", "operation_id": "op_1b2", "status": "queued"}
```

### GET /evolution/replays/{replay}

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/replays/rp_08
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "rp_08", "operation": {"id": "op_1b2", "status": "succeeded"},
  "replayed_candidates": 1840,
  "baseline": {"precision_pct": "71.0", "recall_pct": "64.5"},
  "candidate": {"precision_pct": "78.3", "recall_pct": "61.0"},
  "note": "Screening only; adoption requires mechanism verification"
}
```

### 内部：POST /evolution/v1/runs/{run}/ledger:export

内部 API，不在 `/openapi/v1` 下。元策略工作进程原样使用
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 的作业协议（认领、心跳、
工作区、智能体、模型、操作、候选、预算）。为提议的 `ledger.read@1` 能力新增一个
端点：

```text
POST /evolution/v1/runs/{run}/ledger:export    ctx.ledger.export → {workspace_id}   (if granted; idempotent per key)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /evolution/v1/runs/run_m12/ledger:export
{"family": "clawevolve/bot-evolution", "since": "2026-07-01T00:00:00Z", "key": "run_m12/ledger"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_4410", "entries": 412, "redacted": ["hidden_suite_cases", "mechanism_holdout_outcomes"]}
```

对于元运行，`POST /evolution/v1/runs/{run}/candidates` 接受 `MechanismPatch` 请求体
并返回 `{candidate_id}`（补丁内容哈希）；`GET
/evolution/v1/runs/{run}/candidates/{id}` 返回仅含汇总数据的机制判定。未授予：`403`；
围栏令牌过期：`409`。

### 内部：verify_mechanism(candidate, baseline, profile)

内部服务调用。机制门禁对每个元运行候选发起该调用（与
`POST /evolution/mechanism-verifications` 契约相同）。它调度的每次改进问题执行都是
提交给进化运行的沙箱化第 2 级运行，幂等键为
`<verification_id>/<problem>/<mechanism>/<seed>`，因此崩溃和重新派发绝不会为同一次
执行付费两次。

## 14. 示例

### 14.1 通过基准进行的人工编写进化策略变更（首个用途）

第一个有用的第 3 级步骤不需要元策略：ClawEvolve 维护者修改 tune 提示词，注册
`2.0.1`，然后让机制验证来决定。

```python
from avernet_evolution import Client

c = Client.from_env()
family = "clawevolve/bot-evolution"

# 2.0.1 was registered with `avn strategy publish` (03-strategy.md); new tune-prompt digest.
v = c.mechanism_verifications.start(
    family=family, candidate="2.0.1", baseline="active",
    profile="mechanism-default@1",
    idempotency_key="mv-clawevolve-2.0.1-vs-2.0.0",   # same key on every retry
)
result = c.mechanism_verifications.wait(v.verification_id)   # short lookups by id
print(result.verdict, result.comparison["yield_gain_pct"])

if result.verdict == "accept":
    # T3: a human approves from the report; approval promotes `active` (or `canary` first).
    report = c.mechanism_candidates.get(result.candidate)   # human-registered: candidate id = revision id
    c.mechanism_candidates.approve(report.candidate, reason="Benchmark accept; reviewed diff")
```

### 14.2 元策略（草图）

```python
class MetaEvolve(EvolutionStrategy):
    """Coding-agent meta-strategy (Meta-Harness pattern). Registered with
    needs: {"ledger.read@1": {}, "agents@1": {"definitions": {"meta-coder": {...}}}}."""

    async def run(self, ctx):
        state = await self.store.load(ctx.run_id)                     # its own storage
        if state is None:
            history = await ctx.ledger.export(family=ctx.params["family"],
                                              since=ctx.params["since"])   # filesystem export of H
            ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/mech")
            op = await ctx.agents.start(
                "meta-coder", workspace=ws,
                prompt="Find operators and thresholds with low verified yield in ./ledger; "
                       "edit only params, agents/ and operators/.",
                idempotency_key=f"{ctx.run_id}/propose")
            await ctx.operations.wait(op)
            patch = ws.to_mechanism_patch()            # static checks run on submit (§7)
            state = State(candidate=await ctx.candidates.submit(patch))
            await self.store.save(ctx.run_id, state)
        verdict = await ctx.candidates.verdict(state.candidate)   # mechanism verification decides
        return RunSummary(candidates=[state.candidate], last_status=verdict.status)
```

### 14.3 低成本筛选提交过滤器变更

```python
rp = c.replays.start(family=family, candidate="2.0.2",
                     corpus={"since": "2026-07-01T00:00:00Z", "labelled_only": True},
                     idempotency_key="replay-filter-min-gain-2")
screen = c.replays.wait(rp.replay_id)
if screen.candidate["precision_pct"] > screen.baseline["precision_pct"]:
    # Worth paying for a real comparison; screening alone never adopts.
    c.mechanism_verifications.start(family=family, candidate="2.0.2", baseline="active",
                                    profile="mechanism-default@1",
                                    idempotency_key="mv-clawevolve-2.0.2-vs-active")
```

### 14.4 跟随或固定家族的机制（提议的绑定形式）

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// One binding in bot_123's evolution policy (06-evolution-run.md); open decision O-2
{
  "strategy": "clawevolve/bot-evolution@active",   // follow the family's active mechanism; "@2.0.0" pins
  "trigger": {"schedule": "0 2 * * *"},
  "parent": "active",
  "allowed_genes": ["persona", "skills"],
  "verification_profile": "default@1",
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "params": {"window_days": 7}
}
```

### 14.5 回到旧版本

```bash
avn evolve mechanism promote clawevolve/bot-evolution --revision 2.0.0 \
  --reason "False-acceptance rate rose after 2.0.1" --yes
```

## 15. 交互

| 其他部分 | 方向 | 流转内容 |
| --- | --- | --- |
| 实验记录 H（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)） | 实验记录 → 元进化 | 用于指标、问题冻结和 `ledger.read@1` 导出的实验（包括被拒绝的、被回到旧版本的以及线上结果） |
| 实验记录 H | 元进化 → 实验记录 | 元运行候选、机制验证、批准、晋升（经审计） |
| Strategy Registry（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)） | 双向 | 机制修订版和引用存储于此；候选机制的注册与一致性检查；`ledger.read@1` 目录条目 |
| 默认进化策略（[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)） | 元进化 → 进化策略 | ClawEvolve 是第一个机制被改进的家族；其校准/回放脚本为回放筛选提供基础 |
| 进化运行（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)） | 双向 | 元运行作为运行执行；每次改进问题执行都是沙箱化的第 2 级运行；绑定将 `@active` 解析为冻结的机制；预算和紧急停止开关 |
| 验证（[07-verification.zh-CN.md](07-verification.zh-CN.md)） | 验证 → 元进化 | 对每次问题执行的 Bot 验证；验证器版本；在问题粒度上复用的配对统计比较器 |
| 晋升（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)） | 双向 | 机制门禁配置、默认 T3、评审队列条目、权力分立 |
| 经验（[02-experience.zh-CN.md](02-experience.zh-CN.md)） | 经验 → 元进化 | 用于改进问题的冻结经验快照 |
| 基因组（[01-genome.zh-CN.md](01-genome.zh-CN.md)） | 基因组 → 元进化 | 共享的修订版/引用/补丁机制；问题的起始基因组 |
| 进化 API（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)） | 元进化 → API | 在共享约定下的 §13 端点；`avn evolve mechanism …` 命令 |

## 16. 待定决策

| ID | 决策 | 说明 |
| --- | --- | --- |
| O-1 | 元策略在黑盒进化策略中可以修补什么 | 提议：只修补该进化策略以数据形式暴露的内容（参数、智能体定义、算子文件、流程描述）；代码变更仍为人工编写的新版本 |
| O-2 | 绑定如何跟随家族的机制引用 | 提议 `strategy: "<family>@active"`；目前绑定总是固定确切版本。必须在制定绑定契约时确定，以免日后成为破坏性变更 |
| O-3 | 按租户划分的机制引用 | 来源允许所有者“在其自己的租户上”将采纳降为 T2，这意味着共享家族需要租户作用域的引用；尚未设计 |
| O-4 | 跨租户证据 | “来自元策略从未见过的 Bot”的留出问题以及跨 Bot 学习，与禁止跨租户使用经验和学习产物的数据处理默认规则相冲突（[02-experience.zh-CN.md](02-experience.zh-CN.md)）；第 3 级可能需要按租户运行，或需要显式选择加入 |
| O-5 | 自动化机制修订版的版本命名 | 提议：id 为内容哈希；人类可读的 `version` 在候选注册时分配（例如补丁级版本号递增） |
| O-6 | 谁负责第 3 级预算并确定其规模 | 与 Bot 预算分开；按家族或按平台 |
| O-7 | H 能力的名称和形态 | 提议 `ledger.read@1`，返回文件系统导出；必须通过经评审的平台变更加入目录 |
| O-8 | 机制金丝雀分配 | 真实的第 2 级运行如何分配到 `canary` 与 `active`，以及所有者能否选择退出 |
