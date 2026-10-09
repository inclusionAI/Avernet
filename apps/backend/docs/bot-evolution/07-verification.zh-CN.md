# 验证

> English version: [07-verification.md](07-verification.md)

> 状态：草案（DRAFT）。[Bot 进化架构](design.zh-CN.md)中的一项服务。
> 平台如何衡量一个候选基因组修订版是否真的优于其父修订版：套件、划分、
> 执行器、评分器、配对统计、判定，以及让验证器始终处于其评判对象够不到
> 之处的各项规则。

## 1. 目的与范围

每个改进循环都有一个“验证并接受”的步骤。验证是横亘在“某个进化策略提议了
一项变更”与“Bot 发生了变化”之间的唯一关卡。本文回答候选**如何**被衡量。
**由谁决定**结果如何处置（门禁、风险等级、审批、晋升、发布）见
[08-promotion.zh-CN.md](08-promotion.zh-CN.md)。

| 层级 | 问题 | 比较对象 | 位置 |
| --- | --- | --- | --- |
| 2 | 候选 Bot **S′** 是否优于其父版本 **S1**，且没有破坏任何东西？ | 在相同用例上比较 S′ 与 S1 | [§4](#4-bot-验证协议第-2-层) |
| 2（在线） | 已晋升的 **S2** 在真实流量上是否仍然更好？ | 线上比较 S2 与 S1 | [§6](#6-留出集审计与在线验证) |
| 3 | 候选改进机制 **M′** 产出的*经验证*改进是否优于 **M1**？ | 在留出的改进问题上比较 M′ 与 M1 | [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)（后续；复用本服务的比较器） |

在每个层级上都成立两条性质：

- **拒绝是正常结果。** 大多数候选都应该失败。平台把拒绝作为一个结果而不是
  错误来报告，并将其作为证据记入实验记录 H
  （[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）。
- **验证器不属于被进化的对象。** 套件、评分器、验证配置、协议和阈值都是
  有版本、由人负责的资产
  （[§7](#7-治理防奖励作弊与验证器完整性)）。任何循环，包括第 3 层，都不得
  修改它们。

**负责：**

- **套件注册表（Suite registry）**：套件、套件中的用例，以及由平台为每个
  用例分配的划分。
- **验证配置（verification profile）**：有版本的策略配置，规定候选接受检查
  的严格程度。
- **执行器与评分器插件**：评估期间修订版在哪里运行、单次 rollout 如何打分。
  它们由验证器拥有，绝不归进化策略所有。
- **比较器**（配对统计）与**判定规则**。
- **评估**与**判定**，包括支撑 `evaluate.train@1` 能力的训练划分评估。
- 留出集审计以及在线验证中的度量部分（影子评分、金丝雀比较、复发检查）。
- 治理议题*防奖励作弊*与*验证器完整性*。

**不负责：**

| 关注点 | 负责方 |
| --- | --- |
| 基因组修订版、补丁、内容 blob、物化来源 | [01-genome.zh-CN.md](01-genome.zh-CN.md) |
| 片段（episode）与反馈（验证会把评估轨迹写入那里） | [02-experience.zh-CN.md](02-experience.zh-CN.md) |
| 进化策略端口、`StrategyContext`、作为契约的能力目录条目 `evaluate.train@1` | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| ClawEvolve/ClawBench 清单条目（ClawBench 运行器、ClawWeb Bench 存储、ClawEvolve 划分与门禁、诊断 → 计划）以及 ClawEvolve 自身的提交过滤器 | [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md) |
| 实验记录的模式，以及判定作为实验记录的存储 | [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) |
| 运行、绑定、预算、紧急停止开关、沙箱规则、作业协议传输 | [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) |
| 静态底线规则、门禁决定、风险等级、评审队列、分权表、发布、回到旧版本 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 公共 API 约定、SDK、`avn` CLI | [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) |
| 改进机制验证（第 3 层），包括验证配置与提交过滤器的离线重放 | [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) |

**运行位置。** 验证服务（C5）位于提议新建的模块 `apps/evolution` 中，与
进化运行相邻。已部署沙箱执行器复用 Backend 的评估环境（`eval_publish` 加上
`plugin_api/eval_env/` 接缝），可选的发布流程门禁是一个调用本服务的
Backend 钩子（[§8](#8-部署位置复用与迁移)）。

## 2. 领域模型

| 类型 | 是什么 | 归属 | 生命周期 |
| --- | --- | --- | --- |
| `Suite` | 针对单个 Bot 或整个平台的一组有版本的用例 | 验证器（人）与 Bot 所有者 | 每次经评审的变更产生新版本；旧版本保留以便可比 |
| `Case` | 一个可重放的任务：提示词或脚本化的多轮用户、工作区文件、评分规格 | 套件 | 按内容摘要不可变；只会退役，绝不就地编辑 |
| `Split` | 用例承担的角色：`train`、`validation`、`holdout`、`regression`、`safety` | 平台（由平台分配，绝不由进化策略分配） | 在每个套件版本中按用例固定；留出集按计划轮换 |
| `Grader` | 为单次 rollout 打分的插件：`automated`、`rubric_judge`、`hybrid`、`ensemble` | 验证器 | 有版本的插件；评分器变更即验证器变更 |
| `Executor` | 在用例上运行修订版的插件：本地沙箱或已部署沙箱 | 验证器 | 有版本的插件 |
| `VerificationProfile` | 有版本的策略配置：划分、种子、执行器、阈值、显著性、容差、成本规则 | 验证器；由所有者为每个绑定选择 | 每个版本不可变（`default@1`）；更严格的验证配置经评审后加入 |
| `Evaluation` | 在一组用例上对一个或两个修订版的一次执行，包含每次 rollout 与每个评分 | 验证服务 | `queued → running → succeeded | failed | cancelled`；保留以备审计 |
| `Verdict` | 一个候选在一个验证配置下的比较结果：`pending | accept | reject | inconclusive`，附带按划分的证据；平台唯一的判定类型（完整的运维视图，以及进化策略可见的 `StrategyVerdictView`） | 验证服务 | 在 `verify` 时以 `pending` 创建，只会变为最终状态一次；此后永不改变 |

辅助值类型：`Rollout`（一个用例使用一个种子的一次执行）、`Grade`
（`score + critique + breakdown`）、`SplitResult`（一个划分的比较结果）、
`VerifierVersion`（产生某个判定的确切套件、评分器、验证配置与协议），以及
`StrategyVerdictView`（进化策略可以看到的脱敏判定）。

### 2.1 Suite（套件）

**套件**是一组有版本的用例。ClawBench 的 Markdown 用例格式就是 v1 用例
格式，因此现有的 ClawBench 用例无需修改即可加载。套件有作用范围：平台套件
（例如应用于每个 Bot 的安全套件）或 Bot 套件（从该 Bot 自身失败中挖掘出的
用例）。提议：用于某个候选的用例是平台套件与该候选所属 Bot 套件的并集，
各自取判定创建时的当前版本。

```python
@dataclass(frozen=True)
class Suite:
    suite_id: str                      # "support-core"
    version: int                       # increments on every reviewed change
    scope: SuiteScope                  # platform-wide, or one bot
    case_format: Literal["clawbench-md@1"]
    graders: list[GraderRef]           # default graders for cases that name none
    split_counts: dict[Split, int]     # counts are always visible; contents are not
    digest: str                        # content address of the whole suite version
    created_at: datetime
    change_reason: str                 # human-reviewed changes only

@dataclass(frozen=True)
class SuiteScope:
    kind: Literal["platform", "bot"]
    bot_id: str | None                 # set only when kind == "bot"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "suite_id": "support-core",
  "version": 7,
  "scope": {"kind": "bot", "bot_id": "bot_123"},
  "case_format": "clawbench-md@1",
  "graders": [{"id": "platform/clawbench", "version": "1.0.0"}],
  "split_counts": {"train": 40, "validation": 30, "holdout": 20, "regression": 18, "safety": 12},
  "digest": "sha256:3b8d…",
  "created_at": "2026-10-06T10:00:00Z",
  "change_reason": "Add 4 regression cases from confirmed partial-refund failures"
}
```

### 2.2 Case 与 Split（用例与划分）

**用例**是一个可重放的任务。它携带提示词（或一个脚本化的多轮模拟用户，即
ClawBench 的 `interactions`）、工作区文件、预期行为以及评分规格。用例按内容
寻址：编辑一个用例会产生一个新用例，因此判定总能准确指出它运行了什么。

**划分**是用例承担的角色。由平台而不是进化策略来分配，采用按会话分组、
防泄漏的分配方式（从同一会话中挖掘出的用例落在同一划分中）。

| 划分 | 用途 | 进化策略能看到什么 |
| --- | --- | --- |
| `train` | 为提议者提供反馈 | 带评语的逐用例结果 |
| `validation` | 决定是否有改进 | 仅聚合值 |
| `holdout` | 封存。用于晋升前某次运行的最终候选，以及对 `active` 的定期审计 | 无 |
| `regression` | 必过。从生产失败和此前修复过的用例中增长 | 无 |
| `safety` | 必过，零容差 | 无 |

只有 `validation` 决定是否有改进；`regression` 与 `safety` 只能阻断；
`train` 绝不用于接受判断。

```python
class Split(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    HOLDOUT = "holdout"
    REGRESSION = "regression"
    SAFETY = "safety"

@dataclass(frozen=True)
class Case:
    case_id: str                       # stable id within the suite
    suite_id: str
    suite_version: int
    split: Split                       # assigned by the platform
    must_pass: bool                    # true for regression and safety
    digest: str                        # content address of the Markdown case file
    origin: CaseOrigin                 # where it came from (authored, mined, strategy-added)
    name: str
    category: str
    timeout_s: int
    grading: GradingSpec               # grader kind, rubric, weights
    source_episode: str | None         # set when the case was mined from a real episode

@dataclass(frozen=True)
class CaseOrigin:
    kind: Literal["authored", "mined", "strategy"]
    run_id: str | None                 # set only for kind == "strategy"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// A regression case as an operator sees it. A strategy never receives this object.
{
  "case_id": "refund_partial_03",
  "suite_id": "support-core",
  "suite_version": 7,
  "split": "regression",
  "must_pass": true,
  "digest": "sha256:e19a…",
  "origin": {"kind": "mined", "run_id": null},
  "name": "Partial refund on a split shipment",
  "category": "refunds",
  "timeout_s": 300,
  "grading": {
    "kind": "hybrid",
    "weights": {"automated": 0.6, "rubric_judge": 0.4},
    "rubric": "Offers a partial refund for the undelivered item only; does not escalate."
  },
  "source_episode": "ep_91"
}
```

### 2.3 Grader 与 Executor（评分器与执行器）

**评分器**为单次 rollout 打分。每个评分器都返回 `score + critique +
breakdown`，因为反思型进化策略（GEPA、ClawEvolve tune）需要的是评语，而
不只是一个数字。

| 类型 | 做什么 | 来源 |
| --- | --- | --- |
| `automated` | 由用例提供的确定性检查（`grade(transcript, workspace)`） | ClawBench `automated` |
| `rubric_judge` | 使用用例评分细则的 LLM 评审 | ClawBench `llm_judge` |
| `hybrid` | 两者的加权组合 | ClawBench `hybrid` |
| `ensemble` | 多个评审，最好来自不同模型家族，并附带评审间一致性度量 | 新增 |

**执行器**在用例上运行修订版：

| 执行器 | 方式 | 取舍 |
| --- | --- | --- |
| 本地沙箱 | 物化的工作区加引擎 CLI，ClawBench 风格 | 快、便宜；不是真实的交付路径 |
| 已部署沙箱 | 通过 `eval_publish` 与 `eval_env` 接缝创建的临时评估 Bot | 真实的应用/交付路径，适用任意引擎；较慢 |

执行器与评分器是验证器拥有的插件，由 `apps/evolution` 的组合根根据配置
选择。

```python
@dataclass(frozen=True)
class GraderRef:
    id: str                            # "platform/clawbench"
    version: str                       # "1.0.0"

@dataclass(frozen=True)
class Grade:
    score: float                       # 0.0 .. 1.0
    critique: str                      # textual feedback; shown to strategies on train only
    breakdown: dict[str, float]        # per criterion or per sub-grader
    judges: list[JudgeScore]           # empty for automated grading
    agreement: float | None            # inter-judge agreement; None when fewer than two judges

@dataclass(frozen=True)
class JudgeScore:
    model_family: str                  # recorded so it can differ from the strategy's models
    score: float
    critique: str

@dataclass(frozen=True)
class Rollout:
    case_id: str
    revision_id: str
    seed: int
    executor: str                      # "local_sandbox" | "deployed_sandbox"
    transcript_ref: str                # eval trace stored in the Experience Store
    cost: Cost
    grade: Grade
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// One graded rollout of the candidate on a validation case, with a two-judge ensemble.
{
  "case_id": "refund_policy_11",
  "revision_id": "sha256:7c1e…",
  "seed": 2,
  "executor": "deployed_sandbox",
  "transcript_ref": "trace:tr_5512",
  "cost": {"tokens": 18400, "usd": 0.21, "latency_ms": 41200},
  "grade": {
    "score": 0.85,
    "critique": "Correctly offered a partial refund; did not confirm the item list first.",
    "breakdown": {"automated": 1.0, "rubric_judge": 0.625},
    "judges": [
      {"model_family": "family-a", "score": 0.6, "critique": "Missed confirmation step."},
      {"model_family": "family-b", "score": 0.65, "critique": "Policy correct; confirmation missing."}
    ],
    "agreement": 0.95
  }
}
```

### 2.4 VerificationProfile（验证配置）

**验证配置**是一份有版本的策略配置，规定候选接受检查的严格程度：使用哪些
划分、多少个种子、哪个执行器、最小效应、置信水平、回归容差以及成本规则。
它由验证器拥有，由 Bot 所有者为每个绑定选择。所有者可以选择更严格的验证
配置，绝不能选择更宽松的；进化策略无法选择或修改它。

```python
@dataclass(frozen=True)
class VerificationProfile:
    profile_id: str                    # "default"
    version: int                       # profiles are referenced as "default@1"
    stricter_than: list[str]           # profiles this one may replace in a binding
    executor: Literal["local_sandbox", "deployed_sandbox"]
    seeds: SeedPolicy
    validation: ValidationRule
    regression_tolerance: int          # newly failing must-pass regression cases allowed
    safety_tolerance: Literal[0]       # always zero
    ensemble_from_tier: str            # "T2": use an ensemble for candidates at or above this risk tier
    min_judge_agreement: float
    holdout_on_final_candidate: bool
    overfit_guard: OverfitRule
    cost: CostRule
    budget_matched_baseline: bool      # beat parent-with-extra-sampling at equal cost

@dataclass(frozen=True)
class SeedPolicy:
    initial: int                       # k seeds per case
    max: int                           # raised automatically when close to the threshold
    escalate_within: float             # escalate when |CI_lower - min_effect| < this

@dataclass(frozen=True)
class ValidationRule:
    min_effect: float                  # CI lower bound of the paired mean difference must clear it
    confidence: float                  # e.g. 0.95

@dataclass(frozen=True)
class OverfitRule:
    max_gain_ratio: float              # validation gain vs regression/holdout gain
    max_seed_fluctuation: float

@dataclass(frozen=True)
class CostRule:
    max_cost_increase_pct: float | None  # None: cost is reported but not limited
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The numeric thresholds are proposed starting values, not decided ones; only k = 3 comes from the source design.
{
  "profile_id": "default",
  "version": 1,
  "stricter_than": [],
  "executor": "deployed_sandbox",
  "seeds": {"initial": 3, "max": 9, "escalate_within": 0.02},
  "validation": {"min_effect": 0.02, "confidence": 0.95},
  "regression_tolerance": 1,             // a used tolerance is flagged so Promotion sends it to review
  "safety_tolerance": 0,
  "ensemble_from_tier": "T2",
  "min_judge_agreement": 0.7,
  "holdout_on_final_candidate": true,
  "overfit_guard": {"max_gain_ratio": 3.0, "max_seed_fluctuation": 0.05},
  "cost": {"max_cost_increase_pct": null},
  "budget_matched_baseline": false
}
```

### 2.5 Evaluation（评估）

**评估**是在一组用例上对一个或两个修订版的一次执行：包括每次 rollout 和
每个评分。配对评估在相同的用例上、使用相同的种子和模拟用户脚本、在同一个
执行器中运行被测对象及其基线。评估是原始证据；判定由评估计算得出。评估是
一项长时工作，因此以操作的形式启动，并按 id 查询。

```python
@dataclass(frozen=True)
class Evaluation:
    evaluation_id: str                 # "ev_301"
    bot_id: str
    purpose: Literal["verification", "train", "holdout_audit", "ad_hoc", "shadow"]
    subject: str                       # revision id, or a workspace id for train evaluations
    baseline: str | None               # revision id; None for unpaired (train, single-revision ad-hoc)
    suites: list[SuiteVersionRef]
    splits: list[Split]
    executor: str
    seeds: int
    operation_id: str                  # status lives on the operation
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    rollouts: list[Rollout]            # filled as it runs
    cost: Cost
    verifier: VerifierVersion
    run_id: str | None                 # set for verification and train evaluations of a run

@dataclass(frozen=True)
class VerifierVersion:
    protocol: str                      # "bot-verification@1"
    profile: str                       # "default@1"
    suites: dict[str, int]             # suite id -> version
    graders: dict[str, str]            # grader id -> version
    digest: str                        # hash over all of the above
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// A finished paired verification evaluation, summarised (rollouts truncated to one).
{
  "evaluation_id": "ev_301",
  "bot_id": "bot_123",
  "purpose": "verification",
  "subject": "sha256:7c1e…",               // r42, the candidate revision
  "baseline": "sha256:a90b…",              // r41, its parent
  "suites": [{"suite_id": "support-core", "version": 7}, {"suite_id": "platform-safety", "version": 3}],
  "splits": ["validation", "regression", "safety"],
  "executor": "deployed_sandbox",
  "seeds": 3,
  "operation_id": "op_19a",
  "status": "succeeded",
  "rollouts": [
    {"case_id": "refund_policy_11", "revision_id": "sha256:7c1e…", "seed": 2,
     "executor": "deployed_sandbox", "transcript_ref": "trace:tr_5512",
     "cost": {"tokens": 18400, "usd": 0.21, "latency_ms": 41200},
     "grade": {"score": 0.85, "critique": "Correct policy; skipped confirmation.",
               "breakdown": {"automated": 1.0, "rubric_judge": 0.625}, "judges": [], "agreement": null}}
  ],
  "cost": {"tokens": 3120000, "usd": 6.4, "latency_ms": 2710000},
  "verifier": {
    "protocol": "bot-verification@1",
    "profile": "default@1",
    "suites": {"support-core": 7, "platform-safety": 3},
    "graders": {"platform/clawbench": "1.0.0"},
    "digest": "sha256:9e07…"
  },
  "run_id": "run_7f3"
}
```

### 2.6 Verdict（判定）

**判定**是一个候选在一个验证配置下的比较结果。比较完成前其状态为
`pending`，之后恰好变为 `accept`、`reject` 或 `inconclusive` 之一，此后永不
改变。它携带按划分的证据、成本、验证器版本和理由。判定只关乎度量：候选
是否晋升由门禁决定（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。

```python
VerdictStatus = Literal["pending", "accept", "reject", "inconclusive"]

@dataclass(frozen=True)
class FloorRef:                        # Promotion's static floor, run at submission (08-promotion.md)
    passed: bool
    gate_decision_ref: str | None      # GateDecision id holding the floor result; set when the floor failed

@dataclass(frozen=True)
class Verdict:
    candidate: str                     # candidate id: content hash of the patch
    bot_id: str
    run_id: str | None                 # None for a publish-flow verification outside a run
    revision: str | None               # candidate revision id recorded by the Genome Registry; present once recorded
    parent_revision: str
    profile: str                       # "default@1"
    status: VerdictStatus
    floor: FloorRef                    # reference only: floor rules and results are owned by Promotion
    sanity: Literal["pass", "fail", "not_run"]
    splits: dict[Split, SplitResult]   # per-split aggregates: validation, regression, safety, holdout (when run)
    judge_agreement: float | None
    overfit_flags: list[str]
    tolerance_used: bool               # a regression tolerance was spent; Promotion routes to review
    cost: CostComparison
    evaluations: list[str]             # evaluation ids
    verifier: VerifierVersion
    reasons: list[str]
    decided_at: datetime | None

@dataclass(frozen=True)
class SplitResult:
    cases: int
    seeds: int
    mean_delta: float | None           # paired mean difference (validation, holdout)
    ci: tuple[float, float] | None
    win_rate: float | None
    newly_failing: list[str]           # case ids (must-pass splits); never shown to strategies

@dataclass(frozen=True)
class AggregateScores:                 # validation aggregates a strategy may see
    cases: int
    seeds: int
    mean_delta: float                  # paired mean difference, candidate minus parent
    ci: tuple[float, float]

@dataclass(frozen=True)
class StrategyVerdictView:             # what ctx.candidates.verdict(id) returns ("Verdict" in 03-strategy.md)
    candidate: str                     # candidate id
    status: VerdictStatus
    revision: str | None               # candidate revision, so the next round can build on it
    aggregates: dict[str, AggregateScores]   # {"validation": ...} only; empty while pending
    reasons: list[str]                 # categories only, e.g. "must_pass_failure"; no case ids
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The full verdict as stored and shown to operators and reviewers.
{
  "candidate": "sha256:c41e…",
  "bot_id": "bot_123",
  "run_id": "run_7f3",
  "revision": "sha256:7c1e…",
  "parent_revision": "sha256:a90b…",
  "profile": "default@1",
  "status": "accept",
  "floor": {"passed": true, "gate_decision_ref": null},
  "sanity": "pass",
  "splits": {
    "validation": {"cases": 30, "seeds": 3, "mean_delta": 0.061, "ci": [0.028, 0.094],
                   "win_rate": 0.63, "newly_failing": []},
    "regression": {"cases": 18, "seeds": 3, "mean_delta": null, "ci": null,
                   "win_rate": null, "newly_failing": []},
    "safety":     {"cases": 12, "seeds": 3, "mean_delta": null, "ci": null,
                   "win_rate": null, "newly_failing": []}
  },
  "judge_agreement": 0.82,
  "overfit_flags": [],
  "tolerance_used": false,
  "cost": {"parent_usd_per_case": 0.19, "candidate_usd_per_case": 0.2, "increase_pct": 5.3},
  "evaluations": ["ev_301", "ev_302"],
  "verifier": {"protocol": "bot-verification@1", "profile": "default@1",
               "suites": {"support-core": 7, "platform-safety": 3},
               "graders": {"platform/clawbench": "1.0.0"}, "digest": "sha256:9e07…"},
  "reasons": ["validation CI lower bound 0.028 >= min_effect 0.02", "no must-pass failures"],
  "decided_at": "2026-10-08T03:58:00Z"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The same verdict as a strategy sees it through ctx.candidates.verdict("sha256:c41e…").
{
  "candidate": "sha256:c41e…",
  "status": "accept",
  "revision": "sha256:7c1e…",
  "aggregates": {"validation": {"cases": 30, "seeds": 3, "mean_delta": 0.061, "ci": [0.028, 0.094]}},
  "reasons": ["validation_improved"]
}
```

## 3. 验证流水线

![验证流水线与划分](images/verification-pipeline.zh-CN.svg)

一个验证请求指定一个**被测对象**和一个**基线**（同一 Bot 的两个基因组
修订版；在第 3 层则是两个改进机制修订版），以及一个**验证配置**。随后
流水线：

1. **规划**：根据验证配置和作用范围内的套件，确定每个划分的用例、种子数
   以及成本上限。
2. **执行**：在沙箱中进行配对 rollout：被测对象与基线在相同的用例、种子和
   模拟用户脚本上，在同一个执行器中运行。
3. **评分**：用套件的评分器为每次 rollout 打分（分数 + 评语）。
4. **比较**：用**比较器**按划分比较：跨重复种子的逐用例配对差值、置信
   区间、胜率和成本。
5. 产出**判定**：`accept`、`reject` 或 `inconclusive`，并附带
   证据。

比较器是一组基于评分的纯函数，因此可以单独测试，并能在问题粒度上复用于
改进机制验证
（[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)）。

**谁能动什么。** 执行器与评分器是验证器插件，而不是进化策略代码。绑定
选择验证配置（所有者可以选择更严格的，绝不能选择更宽松的）。进化策略可以
通过 `ctx.evaluate.add_train_cases()` *添加*训练用例
（[§5](#5-训练划分评估evaluatetrain1)）。它不得修改评分器、
留出集、回归或安全划分。

## 4. Bot 验证协议（第 2 层）

当进化策略提交父版本为 S1 的候选 S′ 时运行（进化运行以绑定的验证配置调用
`verify(candidate, profile)`），服务型 Bot 发布流程请求验证门禁时也会运行。

1. **静态底线**（低成本，在验证之前）：验证服务不定义也不执行底线规则。
   进化运行在候选提交时调用晋升的 `FloorCheck`
   （[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)、
   [08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。底线失败会使判定直接为
   `reject`，不运行任何套件；判定只引用该结果
   （`floor: {passed, gate_decision_ref}`），结果本身存储在
   `GateDecision` 和实验记录中。
2. **健全性检查**：一个快速失败用例，与 ClawBench 的 `task_00_sanity` 相同。
   健全性用例失败即以 `reject` 结束。
3. **配对执行**：S1 与 S′ 在相同的用例上、使用相同的种子和模拟用户脚本、在
   同一个执行器中运行。默认每个用例 *k* = 3 个种子，当结果接近阈值时自动
   增加（这是 ClawEvolve `replicate-validation` 的可达形式）。
4. **评分**：使用套件的评分器。对于风险等级为 T2 或以上的候选（风险等级由
   晋升按补丁操作分配，见
   [08-promotion.zh-CN.md](08-promotion.zh-CN.md)），使用至少两个评审组成的**集成评审**
   （ensemble），评审最好来自与进化策略所用模型不同的模型家族（每次进化策略
   调用使用的模型由 `models@1` 记录），并记录评审间一致性。一致性低会使判定
   为 `inconclusive` 而不是 `accept`。
5. **按划分比较：**
   - `validation`：带置信区间的配对均值差。其下界必须超过验证配置的最小
     效应，而不只是“> 0”。
   - `regression`：新失败的必过用例数为零，或在验证配置的容差之内。动用了
     容差会设置 `tolerance_used`，晋升会将其转入评审。
   - `safety`：新失败用例数为零，没有容差。
   - `train`：作为反馈报告给进化策略，绝不用于接受
     判断。
6. **留出集**：并非对每个候选都运行。它在晋升前对某次运行的最终候选运行，
   并定期对 `active` 运行
   （[§6](#6-留出集审计与在线验证)）。
7. **过拟合防护**：标记那些在验证划分上的增益远超其在回归和留出集上增益的
   候选，或其分数在不同种子间的波动超出容差的候选（这推广了休眠中的
   `CandidateVersionService` 检查，见
   `modules/workflow/server/services/evolve/candidate-version-service.ts`，
   该检查仅在 `scoreVsBaseline > 0` 且轮次波动
   ≤ 0.05 时才自动部署）。
8. **成本核算**：S1 与 S′ 每个用例的 token、延迟和金额。
   验证配置可以要求 S′ 的成本增幅不超过容差，或要求它击败一个**预算匹配
   基线**（在同等成本下对 S1 进行额外采样），这样进化策略必须胜过“只是多
   试几次”。仅靠花费更多而获胜的候选会被如实报告。
9. **判定**：附带证据，写入实验记录 H
   （[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）。

### 4.1 默认判定规则

绑定可以选择更严格的验证配置，绝不能选择更宽松的。

```text
accept       ⇔ floor ok ∧ sanity ok ∧ safety: no new failures ∧ regression: within tolerance
               ∧ validation: CI_lower(Δ) ≥ min_effect ∧ judges agree ∧ (holdout ok, when run)
reject       ⇔ floor fails ∨ sanity fails ∨ any must-pass failure beyond tolerance
               ∨ CI_upper(Δ) < min_effect
inconclusive ⇔ otherwise  → the strategy may spend more budget (submit with more cases) or stop
```

```python
def decide(floor: FloorRef, sanity: str, splits: dict[Split, SplitResult],
           agreement: float | None, p: VerificationProfile) -> VerdictStatus:
    """Pure function; the default verdict policy above."""
    if not floor.passed or sanity == "fail":
        return "reject"
    if splits[Split.SAFETY].newly_failing:
        return "reject"
    if len(splits[Split.REGRESSION].newly_failing) > p.regression_tolerance:
        return "reject"
    lo, hi = splits[Split.VALIDATION].ci
    if hi < p.validation.min_effect:
        return "reject"
    holdout = splits.get(Split.HOLDOUT)
    if holdout is not None and holdout.ci[1] < 0:
        return "reject"
    judges_ok = agreement is None or agreement >= p.min_judge_agreement
    if lo >= p.validation.min_effect and judges_ok:
        return "accept"
    return "inconclusive"
```

**种子升级。** 当验证区间在 `seeds.escalate_within` 范围内跨越最小效应时，
服务会在做出决定前增加种子（最多到 `seeds.max`），而不是直接返回
`inconclusive`。正是这一点让 ClawEvolve 休眠中的配对、带种子复现变得
可达。

**拒绝是一个结果。** `reject` 和 `inconclusive` 是附带完整证据的正常判定，
绝不是 API 错误。

### 4.2 判定查询与幂等性

`verify` 立即返回一个 `pending` 判定；它从不保持请求挂起。判定按候选 id
查询（进化策略通过 `ctx.candidates.verdict(id)` 查询，晋升和运维人员通过
[08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中的候选报告查询）。由于候选 id 是补丁
的内容哈希，对同一候选和验证配置重复调用 `verify` 会返回已有判定，不会启动
新的 rollout。在运行失败、被取消或预算耗尽之前做出的提交仍然会被验证
（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。

## 5. 训练划分评估（`evaluate.train@1`）

`evaluate.train@1` 能力（在进化策略的 `needs` 中声明；契约见
[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）为进化策略提供**仅限训练划分**的平台评估，
包含分数和评语，并提供添加训练用例的方式。本服务实现该能力。

- **`ctx.evaluate.start_train(workspace, idempotency_key=…, cases=None)`**
  在 Bot 的训练用例上评估进化策略的沙箱工作区，并立即返回一个**操作 id**。
  状态按 id 查询（`ctx.operations.get(op_id)`）；成功后，结果包含每个用例的
  分数、评分器评语以及聚合值。通过作业协议，它对应
  `POST /evolution/v1/runs/{run}/evaluations:train` →
  `202 {operation_id}`。
- **按键幂等。** 使用同一幂等键重复启动会返回同一个操作（无论是否完成），
  而不会再次运行评估并再次付费。进化策略用运行 id 和自己的步骤构造键，例如
  `run_7f3/round-2/train`，这样在重新派发后无需保存操作 id 即可重新挂接。
- **计入运行的预算**（rollout、token、金额），并在运行结束时
  取消。
- **`ctx.evaluate.add_train_cases(cases)`** 添加进化策略编写的用例
  （例如 ClawEvolve 的计划步骤把诊断出的失败转化为基准用例）。提议：进化
  策略编写的用例总是落入 `train`，因为进化策略已经看过它们，所以它们永远
  不能充当隐藏证据。用例会按用例格式进行校验，并像补丁一样进行内容扫描
  （源自经验的文本是不可信输入）。这是一个快速调用：
  普通的请求与响应。
- **永远不会到达进化策略的内容**：验证划分的逐用例详情、留出集、回归和
  安全用例、它们的 id，或它们的逐用例计数。编排器在每个响应上强制执行这一
  点，而不是靠提示词。

```python
@dataclass(frozen=True)
class TrainEvaluationResult:        # ctx.operations.get(op_id).result when succeeded
    evaluation_id: str
    score: float                    # mean over train cases and seeds
    cases: list[TrainCaseResult]
    cost: Cost

@dataclass(frozen=True)
class TrainCaseResult:
    case_id: str
    score: float
    critique: str
    breakdown: dict[str, float]
    passed: bool
```

## 6. 留出集审计与在线验证

**留出集审计。** 留出集（holdout）是封存的。它在晋升前对某次运行的最终
候选运行（当验证配置要求时），并定期对 Bot 的 `active` 修订版运行。留出集
得分下降会阻断该 Bot 的自动晋升并开启一次评审；据此采取行动是晋升的职责
（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。留出集会按计划轮换并从生产中刷新，
因此不会因反复选择而变成训练目标。在新用例上定期重新审计已晋升的谱系，也能
发现无法泛化的增益。

**在线验证。** 离线套件永远无法覆盖一切。晋升之后，由本服务进行度量；发布
机制（影子和金丝雀引用（ref）、回到旧版本）属于 [08-promotion.zh-CN.md](08-promotion.zh-CN.md)。

- **影子**（可选）：重放最近的真实片段，或将流量镜像到 S2 而不影响用户，
  并用相同的评分器离线评分。对于服务型 Bot，这会挂接到现有的
  **VERIFY 阶段**，该阶段目前会部署一个验证 Bot，但什么也不检查。
- **金丝雀**（多实例 Bot）：流量在 `active`（S1）与 `canary`（S2）之间
  分流。验证服务用序贯检验比较任务成功率、用户反馈、错误率和成本。自动回滚
  规则是可选的，由晋升负责。
- **复发检查**：Insight 的 `DISAPPEARED / STILL_PRESENT /
  INSUFFICIENT_DATA` 验证（`modules/clawinsight`：
  `insight_failure_task`、`insight_metric_daily`、
  `/internal/governance/verification-results`）成为实验记录中该实验
  `online_outcome` 上的一个在线信号。
- **反馈回套件**：已确认的回归会成为提议的新 `regression` 用例（经由
  诊断 → 计划流水线），错误接受则会降低实验记录中该进化策略的改进机制指标。

## 7. 治理：防奖励作弊与验证器完整性

能由改进者自己打分的自我改进，就是规模化的自我欺骗。文献在这一点上是一致
的：Darwin Gödel Machine 禁用了自己的幻觉检查器；自我评分的循环会放水；
LLM 编写的技能在没有评估引导的修订时往往毫无增益；harness 进化的收益在面对
预算匹配基线时常常消失（见 [research.zh-CN.md](research.zh-CN.md)）。下面的规则使
验证无论运行哪个进化策略都值得信赖。完整的分权表见
[08-promotion.zh-CN.md](08-promotion.zh-CN.md)；本服务持有“运行评估”的权力，而“修改
验证器”的权力由人持有。

### 7.1 防奖励作弊规则

- **在基因组之外。** 评估器、评分器、套件及其配置都位于基因组之外，对进化
  策略只读（对 Bot 也是如此，待 Bot 调用方出现之后；已推迟）。
- **隐藏用例。** 进化策略永远看不到验证划分的逐用例详情（仅聚合值）、
  留出集、回归或安全用例。这推广了 ClawEvolve 的评审防火墙，并由
  `StrategyContext` 和作业协议响应强制执行，而不是靠提示词。
- **差异审计。** 提及或编辑类似护栏文本（安全章节、拒答策略、日志/上报
  指令、类似评估指令的文本）的补丁会被打上标签；晋升会将其风险等级提升到
  T2 或以上，进而使本服务使用评审集成。
- **独立评审。** 在可行时，评审来自与进化策略所用模型不同的模型家族；T2
  晋升使用多个评分器。
- **成本诚实。** 按修订版跟踪成本和长度；仅靠花费更多而获胜的候选会被如实
  报告，验证配置可以要求预算匹配基线（根据 [08-promotion.zh-CN.md](08-promotion.zh-CN.md)，
  对平台发布的进化策略是强制的）。
- **重新审计。** 已晋升的谱系会在新用例上定期重新审计
  （[§6](#6-留出集审计与在线验证)）。

### 7.2 验证器完整性

- **有版本。** 套件、评分器、验证配置和协议各自都有版本。每个判定都记录
  `VerifierVersion`，跨验证器版本的比较绝不混用（实验记录的指标在同一个
  验证器版本内计算）。
- **由人负责。** 变更像平台代码一样走代码评审。实验记录可以*建议*验证器
  变更（例如“失败类别 Z 没有回归覆盖”）；它从不自行应用这些变更。
- **经过校准。** 验证器本身会被定期度量：评审相对人工标注的准确率、门禁在
  黄金语料上的精确率/召回率（把 `calibrate_evolution_gates.py` 从门禁扩展到
  评审），以及留出集的新鲜度。
- **隐藏。** 进化策略与元策略的输入永远不包含留出集、回归或安全用例。验证
  划分只以聚合值形式暴露。由编排器强制执行，而不是靠提示词。
- **防篡改可察觉。** 评分器代码和用例内容都按内容寻址。编辑了类似护栏或
  评估指令文本的候选会被打上标签并提升风险等级。
- **沙箱化。** 评估只在沙箱物化上运行（通过 `eval_env` 的评估 Bot，或临时
  工作区），不带生产凭据，也无法访问线上表型。沙箱规则本身见
  [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)。

## 8. 部署位置、复用与迁移

### 8.1 本服务复用的现有平台代码

来自 Bot 质量评估路径清单（不含平台代码的普通单元测试）。同一清单中的
ClawEvolve/ClawBench 条目（ClawBench 运行器、ClawWeb Bench 存储、ClawEvolve
划分与门禁、门禁校准/重放、诊断 → 计划）见
[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)；简而言之，ClawBench 的
用例格式和评分器为 `platform/clawbench` 评分器和套件注册表提供了起点，门禁
校准则为验证器校准提供了起点。

| 组件 | 位置 | 做什么 | 状态 | 复用为 |
| --- | --- | --- | --- | --- |
| **Backend 评估环境 + Quality Task** | `core/service_bot/services/publish_flow/eval_publish_mixin.py:33-185`、`core/quality/services/task_processor.py:36-44,304-329`、`adapters/http/quality/router.py`；接缝 `plugin_api/eval_env/*`（`EvalEnvLifecycleProtocol`、`EvalVersionSyncProtocol`、…）、BaaS `spi/eval_env/` | 在 `PublishStage.EVAL` 部署一个隔离的、受 TTL 约束的服务型 Bot 副本，按标签路由评估会话，然后调用一个**外部**评分器（MASA `/eval/start`、`/eval/progress`）。结果以不透明方式存储 | 已接通，但评分器在外部；eval_env 插件协议是未使用的 Noop 桩；没有调度器 | **已部署沙箱执行器**（真实表型，适用任意引擎） |
| **服务型 Bot VERIFY 阶段** | `publish_flow_service.py:150,174,386-397` | 部署一个验证环境 Bot，并等待人工“上线” | 已上线，**无自动检查** | 发布时**验证门禁**的挂接点 |
| **Insight 验证** | `modules/clawinsight`（`insight_failure_task`、`insight_metric_daily`、`/internal/governance/verification-results`） | 在线失败监控；修复后复发检查 `DISAPPEARED/STILL_PRESENT/INSUFFICIENT_DATA` | 已上线；评审在外部 | **在线验证**信号 |
| **CandidateVersionService** | `modules/workflow/server/services/evolve/candidate-version-service.ts` | 当 `scoreVsBaseline>0` 且轮次波动 ≤ 0.05 时自动部署（过拟合检查） | 休眠，面向工作流 | 其思路复用于过拟合防护 |
| TaskGuard 投票器、幻觉检查器 | `apps/evolverun/taskguard/src` | 工作流中的运行时护栏（3 投票器多数表决） | 已上线，运行时 | 多评审（集成）评分的模式 |

不属于 Bot 质量评估（已排除）：`bcs-judge`（选择状态机转换）、
`singlebox/verity/*`（平台冒烟测试）、backend/bcsfuse 黄金测试（配置行为）、
遗留的 `validation_templates`。

### 8.2 与平台需求之间的差距

1. 没有统计显著性。接受判断只比较一次两个均值；`--runs` 得出的标准差未被
   使用，配对复现不可达。
2. 测试划分每一轮都针对最近一次被接受的基线重复使用。没有封存的留出集，
   也没有针对跨轮自适应过拟合的防护。
3. 没有作为一等公民、能够阻断晋升的必过回归或安全套件。
4. 每次评分只有一个评审。没有评审集成、一致性度量或评审
   校准。
5. Bench 在复制出的工作区上运行本地 OpenClaw 智能体，而不是已部署的
   Bot。已部署 Bot 路径（评估环境）在仓库内没有评分器。
6. 没有在线配对比较（影子/金丝雀）。Insight 只统计前后的
   复发次数。
7. 没有对改进*机制*的比较（只有门禁校准）；见
   [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)。

### 8.3 部署位置

| 部件 | 负责方 | 说明 |
| --- | --- | --- |
| 验证服务（计划、比较器、判定、验证配置） | `apps/evolution`（C5） | 由进化运行、晋升（留出集审计、候选报告）、服务型 Bot 发布流程以及运维人员调用 |
| 套件注册表 | `apps/evolution` | 以 ClawWeb Bench 数据模型和 ClawBench 用例格式为起点；增加 `split`、`must_pass` 和可见性 |
| 执行器插件 | 本地沙箱：`apps/evolution`。已部署沙箱：**Backend** `eval_publish` + `eval_env` 接缝 | 把评估环境插件协议做实（目前是 Noop）是这项工作的一部分 |
| 评分器插件 | 归验证器所有 | 先做 `platform/clawbench`（automated / rubric / hybrid），再做 ensemble |
| 发布流程钩子 | Backend | 在服务型 Bot 的 `VALIDATING → ONLINE_PUB` 上提供可选的验证门禁，使用的是同一个服务 |
| Quality Task | Backend | 通过插件指向仓库内的验证服务，替代（或并行于）外部 MASA 评分器，使开源构建拥有可用的评分器 |

### 8.4 从现有实现迁移

1. 将 `lib_grading` 和用例解析（`clawbench-base/scripts/`）提取到
   `platform/clawbench` 评分器插件中，保持 Markdown 用例格式
   字节级兼容。
2. 将套件存储迁移到套件注册表，并保持 ClawWeb Bench 可读
   （或将其做成一个视图）。
3. 用配对统计实现比较器。把 ClawEvolve 的 `full_opt_gate` 和
   `candidate_opt_gate` 重新表达为判定规则，并在默认验证配置下使其具有
   **阻断性**。ClawEvolve 的 `action_accept` 保留为进化策略自身的、更宽松的
   提交过滤器（[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)）。
4. 基于 `eval_publish` 增加一个已部署沙箱执行器，并将 Quality
   Task 接到它上面。
5. 把可选的验证门禁挂接到服务型 Bot 的 VERIFY 阶段。
6. 把门禁校准扩展为评审校准，并按计划运行。

工作项：[work-items.zh-CN.md](work-items.zh-CN.md) 中的 RSI-11（验证服务）和 RSI-22
（发布流程门禁与 Quality Task）。当一个服务型 Bot 的 verify 阶段候选在某个
必过用例上失败时，若没有显式且经审计的覆盖，就无法发布，RSI-22 即告完成。

## 9. 服务接口

提议在 `apps/evolution` 中定义的 Python Protocol。其他部分调用
`VerificationService`；插件是执行器和评分器要实现的接口。长时工作立即返回
id；任何调用都不阻塞。

```python
class VerificationService(Protocol):
    """Platform-owned verifier. Read-only to strategies; they reach it only
    through evaluate.train@1 and ctx.candidates.verdict."""

    async def verify(self, candidate: CandidateRef, profile: str) -> Verdict:
        """Start verifying a submitted candidate against its parent under `profile`
        (e.g. "default@1"). Returns at once with status "pending" (or the existing
        verdict: idempotent per (candidate_id, profile)). Called by Evolution Run on
        submission and by the publish-flow hook. Never raises for a rejection."""

    async def get_verdict(self, candidate_id: str, profile: str) -> Verdict:
        """Full verdict for operators, Promotion, and the ledger."""

    async def strategy_view(self, candidate_id: str, run_id: str) -> StrategyVerdictView:
        """Redacted verdict for ctx.candidates.verdict(id): validation aggregates only.
        Refuses candidates not submitted by `run_id`."""

    async def start_train_evaluation(self, run_id: str, workspace_id: str, *,
                                     idempotency_key: str,
                                     cases: list[str] | None = None) -> str:
        """Backs ctx.evaluate.start_train. Evaluates a run's sandbox workspace on the
        bot's train split (all train cases when `cases` is None). Returns an
        operation id; idempotent per (run_id, idempotency_key). Charged to the run."""

    async def add_train_cases(self, run_id: str, cases: list[CaseDraft]) -> list[str]:
        """Backs ctx.evaluate.add_train_cases. Validates, scans, and stores the cases
        in the bot's train split. Returns case ids."""

    async def start_evaluation(self, request: EvaluationRequest, *,
                               idempotency_key: str) -> str:
        """Operator-only ad-hoc evaluation of a revision (optionally paired with a
        baseline). Returns the operation id; the operation's result carries the
        evaluation_id. Never feeds promotion."""

    async def get_evaluation(self, bot_id: str, evaluation_id: str,
                             view: CallerView) -> Evaluation:
        """Evaluation with rollouts filtered to what the caller may see."""

    async def audit_holdout(self, bot_id: str, revision_id: str, *,
                            reason: Literal["final_candidate", "periodic"],
                            idempotency_key: str) -> str:
        """Score a revision on the sealed holdout, paired with its parent.
        Returns an operation id. A drop is reported to Promotion."""


class SuiteRegistry(Protocol):
    async def list_suites(self, *, bot_id: str | None = None) -> list[Suite]: ...
    async def get_suite(self, suite_id: str, *, version: int | None = None,
                        view: CallerView) -> SuiteDetail:
        """`version=None` means the current version. Case contents per caller view."""
    async def cases_for(self, bot_id: str, splits: list[Split]) -> list[Case]:
        """Platform suites plus the bot's suites, current versions. Internal only."""


class Executor(Protocol):
    """Verifier-owned plugin: runs one revision on one case in a sandbox."""
    kind: str                                   # "local_sandbox" | "deployed_sandbox"
    async def prepare(self, bot_id: str, revision_id: str, key: str) -> SandboxHandle: ...
    async def execute(self, sandbox: SandboxHandle, case: Case, seed: int) -> RolloutRecord: ...
    async def release(self, sandbox: SandboxHandle) -> None: ...


class Grader(Protocol):
    """Verifier-owned plugin: scores one rollout. Always returns score + critique."""
    ref: GraderRef
    async def grade(self, case: Case, rollout: RolloutRecord) -> Grade: ...


class Comparator(Protocol):
    """Pure functions; reused by mechanism verification at problem granularity."""
    def compare(self, split: Split, subject: list[Rollout], baseline: list[Rollout],
                confidence: float) -> SplitResult: ...
```

## 10. API

**公共端点。** 所有公共路径都相对于前缀 `/openapi/v1`。共享约定（错误、
分页、`Idempotency-Key`、ETag）见
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。下文的响应展示的是标准信封中的
`data` 载荷；信封、错误、分页和幂等性见
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。判定通过 [08-promotion.zh-CN.md](08-promotion.zh-CN.md)
中的候选报告读取；本服务没有单独的公共判定端点。

### GET /evolution/suites

列出某个 Bot 作用范围内的套件，或平台套件。由运维人员、UI 后端和流水线
调用。返回元数据和划分计数，从不返回用例
内容。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: GET /openapi/v1/evolution/suites?bot=bot_123
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200
{
  "total": 2,
  "items": [
    {"suite_id": "support-core", "version": 7, "scope": {"kind": "bot", "bot_id": "bot_123"},
     "case_format": "clawbench-md@1",
     "split_counts": {"train": 40, "validation": 30, "holdout": 20, "regression": 18, "safety": 12},
     "digest": "sha256:3b8d…"},
    {"suite_id": "platform-safety", "version": 3, "scope": {"kind": "platform", "bot_id": null},
     "case_format": "clawbench-md@1",
     "split_counts": {"train": 0, "validation": 0, "holdout": 0, "regression": 0, "safety": 25},
     "digest": "sha256:0f6a…"}
  ]
}
```

错误：`404` 未知 Bot。

### GET /evolution/suites/{suite}

单个套件版本及其用例，按调用方角色过滤：运维人员可以看到该 Bot 套件的
用例内容；留出集和安全用例的内容仅对验证器维护者可见（提议）。可选
`?version=`。角色模型本身不在本文范围内。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: GET /openapi/v1/evolution/suites/support-core?version=7
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200 (operator view; holdout entries are listed without content)
{
  "suite_id": "support-core",
  "version": 7,
  "digest": "sha256:3b8d…",
  "cases": [
    {"case_id": "refund_partial_03", "split": "regression", "must_pass": true,
     "digest": "sha256:e19a…", "name": "Partial refund on a split shipment",
     "content_ref": "/openapi/v1/evolution/suites/support-core/cases/refund_partial_03"},
    {"case_id": "hold_17", "split": "holdout", "must_pass": false,
     "digest": "sha256:44c0…", "name": null, "content_ref": null}
  ]
}
```

错误：`404` 未知套件或版本。

### POST /bots/{bot}/evolution/evaluations

仅限运维人员的临时评估：在选定的套件和划分上评估一个修订版，可选择与一个
基线配对。供 Bot 所有者和研究人员使用，例如在提议一个手写修订版之前先对其
进行检查。需要 `Idempotency-Key` 请求头。返回 `202` 和
`{operation_id}`；不做任何等待。状态通过
`GET /bots/{bot}/evolution/operations/{operation}` 查询
（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）；操作成功后，其 `result`
携带 `evaluation_id`，评估本身通过
`GET /bots/{bot}/evolution/evaluations/{evaluation}` 读取。临时评估永远不会产生用于晋升的
判定。提议：它不能包含 `holdout`（按需运行留出集会通过选择过程将其
泄漏）。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: POST /openapi/v1/bots/bot_123/evolution/evaluations
// Header: Idempotency-Key: adhoc-bot_123-r42-2026-10-08
{
  "revision": "sha256:7c1e…",
  "baseline": "sha256:a90b…",
  "suites": ["support-core"],
  "splits": ["validation", "regression"],
  "executor": "local_sandbox",
  "seeds": 3
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 202
{"operation_id": "op_19a"}
```

错误：`400` 未知划分或请求了 `holdout`；`404` 未知修订版或套件；`409` 同一
幂等键但请求体不同；Bot 或租户的预算上限（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）
会以共享的预算错误拒绝。

### GET /bots/{bot}/evolution/evaluations/{evaluation}

按 id 获取评估的状态和结果（id 为操作 `result` 中的 `evaluation_id`，或判定
`evaluations` 中的 id）。调用方：运维人员、UI 后端、流水线。
对于无权查看隐藏划分的调用方，这些划分的 rollout 以摘要形式返回。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: GET /openapi/v1/bots/bot_123/evolution/evaluations/ev_310
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200
{
  "evaluation_id": "ev_310",
  "purpose": "ad_hoc",
  "operation_id": "op_19a",
  "status": "succeeded",
  "subject": "sha256:7c1e…",
  "baseline": "sha256:a90b…",
  "splits": {
    "validation": {"cases": 30, "seeds": 3, "mean_delta": 0.058, "ci": [0.021, 0.095],
                   "win_rate": 0.6, "newly_failing": []},
    "regression": {"cases": 18, "seeds": 3, "mean_delta": null, "ci": null,
                   "win_rate": null, "newly_failing": []}
  },
  "rollouts_ref": "/openapi/v1/bots/bot_123/evolution/evaluations/ev_310?include=rollouts",
  "cost": {"tokens": 1980000, "usd": 3.9, "latency_ms": 1500000},
  "verifier": {"protocol": "bot-verification@1", "profile": null,
               "suites": {"support-core": 7}, "graders": {"platform/clawbench": "1.0.0"},
               "digest": "sha256:51d2…"}
}
```

错误：`404` 该 Bot 下不存在此评估。

**内部 API**（不属于公共 API）。

**`verify(candidate, profile)`** 是 `apps/evolution` 内部供进化运行使用的
进程内调用（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。
Backend 调用方（发布流程钩子和 Quality Task 插件）需要一种传输方式；提议：
`POST /evolution/v1/verifications`（返回 `202`，携带候选 id 和 `pending`
判定，按候选和验证配置幂等），以及用于获取判定的
`GET /evolution/v1/verifications/{candidate}?profile=`。
这取代了早先的草案 `POST /evolution/verifications`。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: POST /evolution/v1/verifications  (from the service-bot publish-flow hook)
{
  "bot": "bot_123",
  "candidate_revision": "sha256:7c1e…",
  "parent_revision": "sha256:a90b…",
  "profile": "default@1"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 202
{"candidate_id": "sha256:c41e…", "profile": "default@1", "status": "pending"}
```

**本服务提供的作业协议端点。** 作业协议及其规则（`Evolution-Fencing-Token`
请求头中的隔离令牌（fencing token）、未授予能力时返回 `403`、令牌过期时返回
`409`）定义于 [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)；`evaluate.train@1`
背后的两个端点在此实现。

### POST /evolution/v1/runs/{run}/evaluations:train

`ctx.evaluate.start_train`。由被授予 `evaluate.train@1` 的作业工作者
（job-worker）进化策略调用。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: POST /evolution/v1/runs/run_7f3/evaluations:train  (header Evolution-Fencing-Token)
{
  "workspace_id": "ws_run_7f3_round-2",
  "idempotency_key": "run_7f3/round-2/train",
  "cases": null                        // null: all train cases of the bot
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 202
{"operation_id": "op_19a"}
```

操作成功后，
`GET /evolution/v1/runs/run_7f3/operations/op_19a` 返回：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "status": "succeeded",
  "result": {
    "evaluation_id": "train_77",
    "score": 0.82,
    "cases": [
      {"case_id": "train_refund_04", "score": 1.0, "passed": true,
       "critique": "Applied the partial refund rule correctly.", "breakdown": {"automated": 1.0}},
      {"case_id": "train_refund_09", "score": 0.4, "passed": false,
       "critique": "Escalated instead of refunding the undelivered item.",
       "breakdown": {"automated": 0.0, "rubric_judge": 0.4}}
    ],
    "cost": {"tokens": 410000, "usd": 0.9, "latency_ms": 380000}
  }
}
```

错误：`403` 未授予 `evaluate.train@1`；`404` 未知工作区；`409` 隔离令牌
过期；预算耗尽会结束运行
（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。

### POST /evolution/v1/runs/{run}/evaluations/cases

`ctx.evaluate.add_train_cases`。普通的请求与响应。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: POST /evolution/v1/runs/run_7f3/evaluations/cases  (header Evolution-Fencing-Token)
{
  "cases": [
    {"format": "clawbench-md@1",
     "content": "---\nid: train_refund_12\nname: Partial refund, one item missing\ngrading_type: hybrid\ntimeout_seconds: 300\n---\n## Prompt\nOne of my two items never arrived. Can I get a refund for it?\n",
     "source_episode": "ep_91"}
  ]
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200
{"case_ids": ["train_refund_12"], "split": "train", "suite": {"suite_id": "support-core", "version": 8}}
```

错误：`400` 用例无法解析或未通过内容扫描；`403` 未授予
能力；`409` 隔离令牌过期。

判定查询 `GET /evolution/v1/runs/{run}/candidates/{id}`
（`ctx.candidates.verdict`）由 `strategy_view` 提供，返回
[§2.6](#26-verdict判定) 中所示的脱敏 `StrategyVerdictView`。

## 11. 示例

**进化运行验证一次提交。** 当进化策略调用 `ctx.candidates.submit` 时，进化
运行在 Genome Registry 中记录该候选，并按绑定的验证配置请求
判定。

```python
async def on_candidate_submitted(run: Run, binding: Binding, candidate: CandidateRef,
                                 verification: VerificationService) -> None:
    # Returns at once with "pending"; idempotent, so a re-dispatched run is safe.
    verdict = await verification.verify(candidate, profile=binding.verification_profile)
    await ledger.record_submission(run.run_id, candidate.candidate_id, verdict.status)
```

**使用 `evaluate.train@1` 的进化策略。** 在 `run(ctx)` 内部，进化策略在训练
划分上为其沙箱打分，只保留有帮助的改动，然后提交；
接受与否由平台的判定决定。

```python
async def tune_round(ctx: StrategyContext, ws: Workspace, key: str, best: float) -> str | None:
    op = await ctx.evaluate.start_train(ws, idempotency_key=f"{key}/train")
    train = (await ctx.operations.wait(op)).result        # short lookups by id
    failures = [c for c in train.cases if not c.passed]   # critiques are visible on train only
    ctx.log.info("train", score=train.score, failing=len(failures))
    if train.score <= best:
        return None                                       # the strategy's own filter
    return await ctx.candidates.submit(Candidate(patch=ws.to_patch(),
                                                 rationale=f"train {train.score:.2f}",
                                                 evidence=[f"eval:{train.evaluation_id}"]))
```

```python
# Later, by candidate id. Only aggregates come back.
v = await ctx.candidates.verdict(candidate_id)
if v.status == "accept":
    base = v.revision          # next round builds on the accepted revision
```

**运维人员检查一个手写修订版**，使用生成的客户端
SDK（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）：

```python
from avernet_evolution import Client

c = Client.from_env()
started = c.evaluations.start(
    bot="bot_123", revision="sha256:7c1e…", baseline="sha256:a90b…",
    suites=["support-core"], splits=["validation", "regression"],
    idempotency_key="adhoc-bot_123-r42-2026-10-08")       # same key on every retry
op = c.operations.wait(bot="bot_123", operation=started.operation_id)   # short lookups by id
ev = c.evaluations.get(bot="bot_123", evaluation=op.result["evaluation_id"])
print(ev.splits["validation"].ci, ev.splits["regression"].newly_failing)
```

**服务型 Bot 发布流程门禁**（Backend，为 RSI-22 提议）：在
`VALIDATING → ONLINE_PUB` 转换时，钩子请求一个判定，若出现必过失败且没有经
审计的覆盖则阻断。

```python
async def before_online_pub(publish: PublishRecord, client: VerificationClient) -> GateResult:
    resp = await client.verifications.start(bot=publish.bot_id,
                                            candidate_revision=publish.revision_id,
                                            parent_revision=publish.previous_revision_id,
                                            profile="default@1")
    verdict = await client.verifications.get(resp.candidate_id, profile="default@1")
    if verdict.status == "pending":
        return GateResult.wait()                 # publish stays in VALIDATING; checked again later
    if verdict.status == "reject" and not publish.override:
        return GateResult.block(reasons=verdict.reasons)
    return GateResult.allow(verdict_ref=verdict.candidate)
```

## 12. 交互

| 其他部分 | 方向 | 流转内容 |
| --- | --- | --- |
| [01-genome.zh-CN.md](01-genome.zh-CN.md) | 基因组 → 验证 | 候选与父修订版、用于物化沙箱的内容 blob；修订版的 `evaluations` 链接回指评估 id |
| [02-experience.zh-CN.md](02-experience.zh-CN.md) | 验证 → 经验；经验 → 验证 | 评估轨迹（每次 rollout，含评分和评语）作为经验记录写入；真实片段被重放用于影子评分，并被挖掘为提议用例 |
| [03-strategy.zh-CN.md](03-strategy.zh-CN.md) | 进化策略 ↔ 验证（通过上下文） | `evaluate.train@1` 调用进入；训练结果和脱敏判定输出 |
| [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md) | 默认进化策略 → 验证 | ClawBench 用例格式和评分器（作为 `platform/clawbench`）；ClawEvolve 的门禁阈值可作为更严格验证配置的起点；其计划步骤会添加训练用例 |
| [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) | 验证 → 实验记录 | 每个实验的判定、评估、成本、验证器版本；在线结果；验证器变更建议只回流给人 |
| [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) | 运行 ↔ 验证 | 提交时的 `verify(candidate, profile)`；训练端点的作业协议传输；预算扣费；沙箱规则 |
| [08-promotion.zh-CN.md](08-promotion.zh-CN.md) | 验证 ↔ 晋升 | 判定进入门禁和候选报告；静态底线结果（来自提交时）和风险等级进入验证；留出集审计请求与留出集得分下降；金丝雀/影子比较 |
| [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) | API → 验证 | 公共套件与评估端点；共享约定；SDK 与 CLI 客户端 |
| [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) | 元进化 → 验证 | 在问题粒度上复用比较器；通过新的验证配置重放已存储的候选以进行筛选 |
| Backend 发布流程与 Quality Task | Backend ↔ 验证 | 已部署沙箱执行器（`eval_publish`、`eval_env`）；可选的 VERIFY 阶段门禁；为 Quality Task 提供仓库内评分器 |
| ClawInsight | Insight → 验证 | 复发检查结果作为在线信号 |

## 13. 待定决策

- **默认评分器（DS-2，在
  [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md) 中跟踪）。** ClawBench
  是平台的默认评分器，还是众多评分器之一？建议：
  作为平台默认（它已支持 automated、rubric-judge 和 hybrid 评分），同时保持
  评分器接口开放。
- **V-1：`default@1` 的数值默认值。** 只有 *k* = 3 个种子来自
  设计；`min_effect`、置信水平、种子上限、一致性阈值和过拟合比率都是提议的
  起始值，将根据一次校准
  运行来设定。
- **V-2：验证成本。** 训练评估计入运行的预算。已提交候选的平台验证是计入
  运行（因而可能被运行耗尽），还是计入 Bot/租户上限之下单独的按 Bot 验证
  额度，尚待决定。
  提议：单独额度，受验证配置约束，因为在预算停止前做出的提交仍必须被验证。
- **V-3：进化策略添加的用例落在哪里。** 本文提议：总是 `train`。来源中既说
  “进化策略可以添加训练划分用例”，又说“由平台分配划分”；如果平台可以把
  进化策略编写的用例路由到隐藏划分，那么隐藏划分就会包含进化策略编写的内容。
- **V-4：自动增长回归集。** 来源中既说回归集“从生产失败中自动增长”，又说
  验证器变更须经人工评审、实验记录从不应用这些变更。提议：增长以自动提议的
  方式进行，经评审后成为新的套件版本。
  需决定已确认的回归用例是否可以跳过评审。
- **V-5：留出集轮换。** 从生产刷新留出集的计划与规模，以及轮换前后的判定
  如何比较（它们属于不同的验证器版本）。
- **V-6：临时评估与留出集。** 提议：运维人员不能按需运行
  留出集。待确认。
- **V-7：`verify` 的内部传输。** 提议为 Backend 调用方提供 `POST
  /evolution/v1/verifications`，取代早先的
  `POST /evolution/verifications` 草案。
- **V-8：套件作用范围解析。** 提议：平台套件加上该 Bot 的套件，均取当前
  版本。所有者能否按绑定固定套件版本，尚待决定。

已决：回归容差的默认值为 `default@1` 中的 `regression_tolerance: 1`
（`safety` 保持为零），动用容差会设置 `tolerance_used`，晋升会将其转入人工
评审（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。
