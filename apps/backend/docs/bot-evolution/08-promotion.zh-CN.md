# 晋升（Promotion）

> English version: [08-promotion.md](08-promotion.md)

> 状态：草案（DRAFT）。[Bot 进化架构](design.zh-CN.md)中的一项服务。
> 由谁决定一个已验证的候选是否改变 bot：权力分立、门禁、风险等级、评审队列、
> 将修订版晋升为 `active`、发布，以及回到旧版本。提议的决策：
> [DR-2](decisions/0002-promotion-is-platform-owned.zh-CN.md)。

## 1. 目的与范围

由改进者自己打分的自我改进，就是规模化的自我欺骗。文献在这一点上是一致的：
Darwin Gödel Machine 移除了自己的幻觉标记；自评的 Hermes 循环过于宽松；
LLM 编写的技能在没有评测引导修订时往往毫无增益；harness 进化的收益在与预算对齐的
基线比较时常常消失（见 [research.zh-CN.md](research.zh-CN.md)）。本服务持有的规则
保证无论运行哪个进化策略，平台都值得信任：策略负责提议，验证器负责度量，
**只有本服务能移动 bot 的 `active` 引用（ref）**。

**验证（Verification）回答“这个候选有多好？”；晋升回答“由谁决定，bot 是否改变？”**
晋升是平台中唯一可以移动 bot 的 `active`（以及 `canary`）基因组引用的地方，
也是唯一将修订版作为进化结果应用到运行中 bot 的地方。

晋升负责：

- **权力分立**：哪个参与者可以持有哪种权力（§3）；
- **门禁**：平台拥有的决策点，将不可协商的平台底线、验证判定、holdout 与
  基线检查以及风险等级审批，合并为每个候选的一个**门禁决定**
  （§4）；
- **平台底线**，包括对补丁扫描密钥、PII、新的出站 URL 和新的 MCP 服务器
  （§4.2）；
- **风险等级**：将每个补丁归类为 `T0..T3`，以及每个等级的默认晋升路径
  （§5）；
- **评审队列**：通过了所有自动检查、但需要人工决定的候选，支持批准与拒绝（§6）；
- **晋升**：移动 `active`、`previous` 和 `canary`，并通过现有的 Manifest apply
  或服务 bot 发布链应用修订版（§7）；
- **发布与回到旧版本**：影子、金丝雀、晋升、可选的自动回滚，以及通过晋升较早的
  修订版回到旧版本（§8）。

晋升明确**不**负责：

| 关注点 | 负责方 |
| --- | --- |
| 修订版、引用、引用上的比较并交换（CAS）、补丁、内容存储、谱系、基因组的 `policy` 部分（锁定基因、固定项、风险覆盖） | 基因组注册表（Genome Registry），[01-genome.zh-CN.md](01-genome.zh-CN.md)。晋升是唯一被允许在那里移动 `active`、`previous` 和 `canary` 的调用方 |
| 候选如何被度量：套件、划分、评分器、执行器、配对统计、验证配置、判定、holdout 轮换、在线验证指标、反奖励投机规则以及验证器完整性 | 验证，[07-verification.zh-CN.md](07-verification.zh-CN.md) |
| 绑定（哪个策略在 bot 上运行、触发器、允许的基因、验证配置、预算）、运行、沙箱、预算与紧急停止开关 | 进化运行，[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)。晋升读取它需要的绑定字段并遵守紧急停止开关 |
| 记录实验与审计轨迹 | 实验记录 H，[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)。晋升将其门禁决定、批准、拒绝和晋升写入其中 |
| 采用新的改进机制（第 3 层） | 元进化，[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)。它复用门禁，但使用独立的机制采用配置（默认 T3） |
| 共享 API 约定、`avn` CLI、生成的 SDK | 进化 API，[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) |

**运行位置。** 晋升运行在 **Backend** 中，与基因组注册表相邻
（提议路径 `core/bot_genome/`，紧挨 `core/bot_config_manifest/`）。
原因是 Backend 拥有期望状态、Manifest、发布链、租户与审批，而晋升必须与 apply
放在一起（[design.zh-CN.md](design.zh-CN.md)；工作项 RSI-12 将其划分为
“backend：门禁底线、晋升；evolution：绑定”）。验证与进化运行位于新的
`apps/evolution` 模块中；它们通过晋升的服务接口调用晋升（§9）。公开端点位于共享的
`/openapi/v1` 前缀下（§10）：基因组与晋升端点，包括 `/bots/{bot_id}/evolution/` 下的
候选报告、评审队列和 approve/reject 路径，由 Backend 提供；其他进化端点由
`apps/evolution` 提供；[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) 将它们
呈现为一个统一的公开接口面（这遵循 [design.zh-CN.md](design.zh-CN.md) 中 D-1 的
推荐选项）。

**第一次迭代中的调用方**是确定性流水线（夜间作业、CI、产品后端）和人（bot 所有者、
租户管理员、评审者），通过 API、SDK、CLI 和 UI 调用。Bot 调用方随 DR-3 推迟；
无论如何它们都不会持有晋升权力（§3）。

## 2. 领域模型

| 类型 | 含义 | 所有者 | 生命周期 |
| --- | --- | --- | --- |
| `RiskTier` | 补丁的严重程度类别 `T0..T3`；决定默认晋升路径 | 晋升（等级表）；基因组 `policy` 中按 bot 的 `risk_overrides` | 每个候选在被记录时计算一次；对该候选不可变 |
| `GateDecision` | 平台对一个候选的决定：每项检查及其结果、风险等级以及结果（`auto_promote`、`needs_review`、`not_promotable`） | 晋升 | 静态底线失败时在提交时创建，否则在验证判定最终确定时创建；不可变；仅当输入变化时（例如 holdout 事件）才记录新的决定 |
| `ReviewItem` | 等待人工决定的候选，附带评审者需要的一切 | 晋升 | `open` → `approved` / `rejected` / `superseded`；永不删除 |
| `Promotion` | 将 `active`（或 `canary`）移动到某个修订版的一次操作，包括回到旧版本，附带参与者、原因和应用结果 | 晋升 | `applying` → `applied` / `apply_failed`；仅追加 |
| `CandidateReport` | 读视图，联接候选的 diff、验证摘要、门禁决定和评审状态 | 晋升（视图） | 读取时派生；不存储 |

本文使用但在别处定义的类型：`GenomeRevision`、`GenomeRef`、
`GenomePatch`、`GenomePolicy`（[01-genome.zh-CN.md](01-genome.zh-CN.md)）；`Candidate`
（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）；`Verdict`、`VerificationProfile`、
`Evaluation`（[07-verification.zh-CN.md](07-verification.zh-CN.md)）；`Binding`、
`EvolutionPolicy`、`Run`（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）；
`LedgerEntry`（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）。

简要回顾本文依赖的术语：

- **候选**是由策略提交的、针对某个基础修订版的基因组补丁，外加理由和证据。它的
  **候选 id** 是补丁的内容哈希（`sha256:c41e…`）。记录它会产生一个候选
  **修订版**（`r42`，id `sha256:7c1e…`），其父修订版是基础修订版（`r41`，
  `sha256:a90b…`）。
- **判定**是验证在绑定的验证配置下对候选给出的答案：
  `pending | accept | reject | inconclusive`。
- **引用**是指向不可变修订版的具名、可移动指针。`active` 是 bot 正在运行的版本；
  `previous` 是上一个 `active`；`canary` 是金丝雀实例运行的版本。

### 2.1 RiskTier

风险等级**按补丁操作**分配；一个补丁取其各操作中的最高等级，并在此之后应用提升
（rewrite 标记、guardrail 标签）以及 bot 的
`risk_overrides`。

```python
from enum import IntEnum
from typing import Literal

class RiskTier(IntEnum):     # ordered, so tiers compare; serialized by name ("T0".."T3") in JSON
    T0 = 0   # annotations only
    T1 = 1   # memory item add/update/retire, skill description tweak, resource content update
    T2 = 2   # persona edits, skill add/update, allowlisted engine_config, any rewrite-flagged edit
    T3 = 3   # tools, script, memory replace mode, permissions, anything in policy: locked by default

# The Genome Patch op names (01-genome.md §6.1; the full set is fixed by RSI-02).
PatchOpName = Literal["file.edit", "skill.add", "skill.update", "memory.add",
                      "memory.update", "memory.retire", "engine_config.set"]

@dataclass(frozen=True)
class TierRaise:                    # one raise applied after the per-op mapping (§5.1)
    kind: Literal["rewrite", "guardrail_touch"]
    target: str | None              # the file that triggered it, e.g. "persona/SOUL.md"; None for patch-wide

@dataclass(frozen=True)
class TierAssessment:
    tier: RiskTier                  # the patch's tier: the maximum over its ops, after raises and overrides
    per_op: list["OpTier"]          # one entry per patch op, in patch order
    raised_by: list[TierRaise]      # empty when no raise applied

@dataclass(frozen=True)
class OpTier:
    op_index: int                   # position of the op in the patch, from 0
    op: PatchOpName
    target: str                     # what the op changes, e.g. "persona/SOUL.md", "skills/refund-policy"
    tier: RiskTier
    reason: str                     # short human-readable reason for the tier, e.g. "persona edit"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "tier": "T2",
  "per_op": [
    {"op_index": 0, "op": "file.edit", "target": "persona/SOUL.md", "tier": "T2",
     "reason": "persona edit"},
    {"op_index": 1, "op": "skill.update", "target": "skills/refund-policy", "tier": "T2",
     "reason": "skill content update"},
    {"op_index": 2, "op": "memory.add", "target": "memory/customer-tier-rules", "tier": "T1",
     "reason": "memory item add"}
  ],
  "raised_by": []                     // no rewrite flag, no guardrail-like text touched
}
```

### 2.2 GateDecision

```python
from dataclasses import dataclass
from typing import Literal

CheckName = Literal[
    "static_floor", "regression_floor", "verification_verdict",
    "holdout", "budget_matched_baseline", "risk_tier_approval",
]
GateOutcome = Literal["auto_promote", "needs_review", "not_promotable"]

@dataclass(frozen=True)
class GateCheck:
    name: CheckName
    result: Literal["pass", "fail", "not_required", "not_run"]
    detail: str                     # short human-readable reason
    evidence: list[str]             # ids: "eval:ev_301", "scan:sc_77", ...

@dataclass(frozen=True)
class GateDecision:
    id: str                         # "gd_204"
    bot: BotRef                     # the bot (owner + bot id, 09-evolution-api.md §2.7)
    candidate_id: str               # content hash of the patch
    revision_id: str                # candidate revision
    parent_revision_id: str         # base the patch was made against
    run_id: str
    binding_id: str
    verdict: Literal["accept", "reject", "inconclusive"]   # the final verdict the gate decided on
    verification_profile: str       # "default@1"
    verifier_version: str           # recorded so decisions across verifier versions are not mixed
    risk: TierAssessment
    auto_promote_ceiling: RiskTier | None   # from the binding; None = never auto-promote
    checks: list[GateCheck]
    outcome: GateOutcome
    reasons: list[str]              # why the outcome is what it is
    decided_at: str                 # RFC 3339
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "gd_204",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "candidate_id": "sha256:c41e…",
  "revision_id": "sha256:7c1e…",        // r42
  "parent_revision_id": "sha256:a90b…", // r41
  "run_id": "run_7f3",
  "binding_id": "bind_01",
  "verdict": "accept",
  "verification_profile": "default@1",
  "verifier_version": "verifier-2026.10.1",
  "risk": {"tier": "T2", "per_op": [], "raised_by": []},   // per_op elided here; see §2.1
  "auto_promote_ceiling": "T1",
  "checks": [
    {"name": "static_floor", "result": "pass", "detail": "schema ok; no locked gene or pin touched; scan clean", "evidence": ["scan:sc_77"]},
    {"name": "regression_floor", "result": "pass", "detail": "safety: 0 newly failing; regression: 0 newly failing", "evidence": ["eval:ev_302"]},
    {"name": "verification_verdict", "result": "pass", "detail": "accept on validation under default@1", "evidence": ["eval:ev_301"]},
    {"name": "holdout", "result": "pass", "detail": "final candidate of run scored on holdout; no drop", "evidence": ["eval:ev_305"]},
    {"name": "budget_matched_baseline", "result": "not_required", "detail": "not required for this strategy", "evidence": []},
    {"name": "risk_tier_approval", "result": "fail", "detail": "tier T2 above auto-promote ceiling T1", "evidence": []}
  ],
  "outcome": "needs_review",
  "reasons": ["risk tier T2 requires human review"],
  "decided_at": "2026-10-08T03:41:00Z"
}
```

### 2.3 ReviewItem

```python
ReviewStatus = Literal["open", "approved", "rejected", "superseded"]

@dataclass(frozen=True)
class ReviewItem:
    candidate_id: str               # the review item is keyed by candidate id
    bot: BotRef                     # the bot (owner + bot id)
    revision_id: str
    revision_seq: int               # 42, shown as "r42"
    parent_revision_id: str
    run_id: str
    strategy: str                   # "clawevolve/bot-evolution"
    strategy_version: str           # "2.0.0"
    risk_tier: RiskTier
    gate_decision_id: str
    rationale: str                  # from the candidate; required on every patch
    evidence: list[str]             # "episode:ep_91", "finding:f_12"
    self_reported_metrics: dict     # shown to reviewers, never used for acceptance
    status: ReviewStatus
    created_at: str
    decided_by: str | None          # user or pipeline client id of the decider; None while open
    decided_at: str | None
    decision_reason: str | None
    promotion_id: str | None        # set when an approval promoted the revision
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate_id": "sha256:c41e…",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "revision_id": "sha256:7c1e…",
  "revision_seq": 42,
  "parent_revision_id": "sha256:a90b…",
  "run_id": "run_7f3",
  "strategy": "clawevolve/bot-evolution",
  "strategy_version": "2.0.0",
  "risk_tier": "T2",
  "gate_decision_id": "gd_204",
  "rationale": "Escalation section did not cover refunds above the tier limit; 9 of 31 failed episodes were mis-escalated.",
  "evidence": ["episode:ep_91", "finding:f_12"],
  "self_reported_metrics": {"train_pass_rate": "0.81"},   // strategy's own claim, informational only
  "status": "open",
  "created_at": "2026-10-08T03:41:00Z",
  "decided_by": null,
  "decided_at": null,
  "decision_reason": null,
  "promotion_id": null
}
```

### 2.4 Promotion

```python
PromotionStatus = Literal["applying", "applied", "apply_failed"]
ActorKind = Literal["user", "pipeline", "gate_policy", "rollout_policy"]

@dataclass(frozen=True)
class Actor:
    kind: ActorKind                 # gate_policy = auto-promotion; rollout_policy = auto-rollback
    id: str                         # user id, pipeline credential name, or policy/binding id

@dataclass(frozen=True)
class ApplyResult:
    kind: Literal["manifest_apply", "service_publish"]   # personal bot: Manifest apply; service bot: publish flow
    reference: str | None           # apply report id or published version, once known
    detail: str                     # short human-readable progress or failure text

@dataclass(frozen=True)
class Promotion:
    id: str                         # "prm_5d2"
    bot: BotRef                     # the bot (owner + bot id)
    ref: Literal["active", "canary"]
    revision_id: str                # what the ref now points at
    revision_seq: int
    from_revision_id: str           # what the ref pointed at before
    going_back: bool                # informational: target is an ancestor that was promoted before
    candidate_id: str | None        # set when the promotion came from a candidate
    gate_decision_id: str | None
    actor: Actor
    reason: str
    status: PromotionStatus
    apply: ApplyResult
    created_at: str
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "prm_5d2",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "ref": "active",
  "revision_id": "sha256:7c1e…",
  "revision_seq": 42,
  "from_revision_id": "sha256:a90b…",
  "going_back": false,
  "candidate_id": "sha256:c41e…",
  "gate_decision_id": "gd_204",
  "actor": {"kind": "user", "id": "user_owner_17"},
  "reason": "Reviewed diff and verification report; escalation fix is correct.",
  "status": "applied",
  "apply": {"kind": "manifest_apply", "reference": "apply_8812", "detail": "all categories converged"},
  "created_at": "2026-10-08T09:12:00Z"
}
```

### 2.5 CandidateReport

评审者（或流水线）使用的读视图。它联接其他服务拥有的数据；
不做存储。

```python
@dataclass(frozen=True)
class SplitSummary:
    split: Literal["train", "validation", "holdout", "regression", "safety"]
    mean_delta: str                 # candidate minus parent, mean score; decimal string (no floats in canonical JSON)
    ci_low: str | None              # confidence interval of mean_delta; None for must-pass splits
    ci_high: str | None
    newly_failing: int              # cases the parent passed and the candidate fails
    detail_visible: bool            # per-case detail is shown per caller role (07-verification.md)

@dataclass(frozen=True)
class CandidateReport:
    candidate_id: str
    bot: BotRef                     # the bot (owner + bot id)
    revision_id: str
    revision_seq: int
    parent_revision_id: str
    parent_revision_seq: int
    run_id: str
    strategy: str
    strategy_version: str
    patch_summary: list[OpTier]     # ops with their tiers
    diff_path: str                  # relative API path of the genome diff
    rationale: str
    evidence: list[str]
    verdict: Literal["pending", "accept", "reject", "inconclusive"]
    splits: list[SplitSummary]
    cost: dict                      # parent vs candidate cost per case
    flags: list[Literal["rewrite", "guardrail_touch", "overfit_suspect", "wins_by_spending"]]   # meanings in §6.1
    gate: GateDecision
    review: ReviewItem | None
```

其 JSON 形式即 `GET /bots/{bot_id}/evolution/candidates/{candidate}` 的响应（§10.1）。

## 3. 权力分立

进化循环中的每种权力都恰好有一类持有者，而决定“更好”与“上线”的权力，
永远不由被改进的对象或执行改进的对象持有。这是 DR-2 的核心。

| 权力 | 持有者 | 永不由谁持有 |
| --- | --- | --- |
| 提议变更 | 进化策略（通过提交候选）；DR-3 确定后（已推迟），被改进的 bot 可通过收件箱提议 | — |
| 选择哪些策略在 bot 上运行、它们可以改什么、验证有多严格 | **所有者 / 租户管理员**（绑定，[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)） | 策略、bot |
| 定义什么绝不能变差 | **平台 + 所有者**（regression、safety、holdout 套件；平台底线） | 策略、bot |
| 运行评测 | **平台**（验证，使用验证器拥有的执行器和评分器，[07-verification.zh-CN.md](07-verification.zh-CN.md)） | 策略 |
| 修改验证器（套件、评分器、配置、协议、阈值） | **人**，通过经过评审的变更（[07-verification.zh-CN.md](07-verification.zh-CN.md)） | 任何自动化循环，包括第 3 层 |
| 采用新机制（第 3 层） | 平台机制门禁 + 人工批准（[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)） | 元策略 |
| 决定晋升 | **平台门禁** + 按风险等级由所有者/评审者（本文） | 策略、bot |
| 修改锁定基因 / policy | 所有者、租户管理员（[01-genome.zh-CN.md](01-genome.zh-CN.md)） | 策略、bot |

每一行在实践中的含义：

- **提议。** 策略唯一的输出是通过 `ctx.candidates.submit` 提交的候选。
  它不能写入线上 bot、其工作区或其基因组引用。策略选择提交什么是它自己的事（例如
  ClawEvolve 的 `test > baseline` 规则变成对其提交内容的内部过滤）；
  候选是否被接受则不是。
- **选择。** 绑定是所有者对某个策略在某个 bot 上的信任声明：允许的基因、
  验证配置、预算以及自动晋升上限（§5.3）。所有者可以选择**更严格**的验证配置；
  策略永远不能放宽它。
- **定义“绝不变差”。** 平台底线（§4.2）不能被策略或所有者覆盖。
  regression 与 safety 套件由平台和所有者拥有，对策略永不可见。
- **运行评测。** 策略可以通过 `evaluate.train@1` 运行训练评测以获取自身反馈，
  但门禁使用的评测只由验证运行。
- **修改验证器。** 套件、评分器、配置、协议和阈值只能通过人编写并经过评审的变更
  来修改。实验记录 H 可以建议验证器变更（“失败类别 Z 没有 regression 覆盖”）；
  但从不自行应用。
- **采用机制。** 第 3 层的候选机制只有在机制验证和人工批准之后才会被采用。
  机制采用默认为 T3（所有者可在自己的租户上，对仅参数类补丁将其降为 T2）；
  详见 [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)。
- **决定晋升。** 只有本服务移动 `active`。自动晋升只在所有门禁检查通过且风险等级
  不高于所有者设定的自动晋升上限时发生；其他情况都等待人工
  处理。
- **修改 policy。** 基因组的 `policy` 部分（锁定基因、可变基因、固定项、风险覆盖）
  由平台原样向前复制，只能由所有者或租户管理员修改。底线拒绝任何
  触及它的补丁。

由上表推出的调用方规则：

| 调用方 | 在晋升中可以做什么 |
| --- | --- |
| 人类所有者 / 租户管理员 / 受委派评审者 | 读取报告和队列；批准与拒绝；晋升该 bot 的任意修订版，包括回到旧版本 |
| 确定性流水线（夜间作业、CI、产品后端） | 读取报告和队列；只批准等级在所有者所配置的自动晋升策略范围内的条目；拒绝 |
| 门禁本身（`gate_policy` 参与者） | 晋升门禁决定为 `auto_promote` 的候选 |
| 发布策略（`rollout_policy` 参与者） | 当所有者启用的自动回滚规则触发时回到 `previous`（§8.3） |
| 进化策略 | 什么都不能做。它们通过 `ctx.candidates.verdict` 读取自己候选的判定（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)），永远看不到门禁决定或队列 |

### 3.1 决策记录：DR-2

DR-2（状态：提议）陈述了本文所实现的决策：

> 进化策略是可插拔的，但**晋升不是**。只有平台门禁能移动 bot 的 `active`
> 基因组引用。策略可以提交候选补丁。策略和 bot 都不能写入线上 bot、其
> 工作区或其基因组引用。

验证同样由平台拥有：套件、评分器、验证配置和协议位于基因组之外，对策略、
bot 和第 3 层元循环只读，并且只能通过经人工评审的变更来修改。
策略的输入永远不包含 holdout、regression 或 safety 用例。

DR-2 中记录的后果：

- ClawEvolve 的 tune 阶段必须停止编辑线上工作区，改为从沙箱中
  产出补丁；pack/restore 退出进化流程
  （[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)）。
- 任何第三方策略都可以被启用，而无需信任它拥有生产环境
  写权限。
- Bot 所有者获得评审队列和按 bot 的策略配置（启用的策略、
  自动晋升上限、预算）。
- 每一次晋升和回到旧版本都会被审计，并附带证据和评测
  链接。

DR-2 中被否决的备选方案：

| 备选方案 | 否决原因 |
| --- | --- |
| **每个策略按自己的规则晋升**（当前 ClawEvolve 的接受规则加原地编辑） | 奖励投机和自我评分是自我改进智能体被报告的主要失败模式 |
| **总是要求人工批准** | 不作为默认值，因为它会阻塞低风险的记忆更新。作为所有者策略保留可用（将自动晋升上限设为无） |

## 4. 门禁

门禁是平台拥有的决策点，它将一个判定已最终确定的候选转化为门禁决定。
验证决定候选*有多好*；门禁决定*它是否可以改变 bot，以及必须由谁
同意*。

### 4.1 检查项

只有当以下检查**全部**通过时，候选才可晋升：

1. **静态底线**（§4.2，在提交时运行）—— schema 有效；基础修订版匹配；未触及任何锁定基因或
   固定项；未引入密钥/PII；大小与重写阈值达标；
   未引入新的出站 URL 或 MCP 服务器，除非对应基因已解锁；没有权限提升；未超出预算。
2. **回归底线** —— 在 bot 的 `regression` 和 `safety` 套件上，候选相对于父修订版
   的退化不超过容差。默认：`safety` 上新失败用例为零（无容差），`regression` 上
   至多一个，且这一个会把候选送去评审，即使其等级本可以
   自动晋升。容差来自绑定的验证配置；
   由验证运行这些套件。
3. **验证判定** —— 在绑定的验证配置下，在 `validation` 上为 `accept`
   （[07-verification.zh-CN.md](07-verification.zh-CN.md)）。
   所有者可以选择更严格的配置；策略不能放宽它。对于 T2 及以上的
   候选，判定必须来自至少两个评分器的集成
   （验证记录评审者间一致性；一致性低时得到 `inconclusive`，而不是 `accept`）。
4. **Holdout 检查** —— 在晋升前，一次运行的最终候选会在密封的
   `holdout` 划分上评分（当配置要求时；时机由
   [07-verification.zh-CN.md](07-verification.zh-CN.md) 负责），已晋升的修订版会定期在
   `holdout` 上评分，而不是每次迭代都评，从而避免 holdout 通过反复选择
   而泄漏。`active` 上的 holdout 下降会开启一个
   **事件（incident）**，并阻止该 bot 后续的自动晋升，直到所有者
   解除它；该 bot 待处理的和新的候选改为进入评审。
5. **预算对齐基线** —— 对每个策略可选，**对平台发布的策略
   为强制**：候选必须在同等成本下胜过
   额外采样的父修订版。仅靠多花费而胜出的候选会在其报告中被标记为
   `wins_by_spending`。
6. **风险等级审批**（§5）—— 等级不高于绑定的
   自动晋升上限（自动晋升），或由人在评审
   队列中批准。

检查 1–5 无需人工即可得出通过或失败。检查 6 是人可能
介入的地方。

### 4.2 平台底线与补丁扫描

平台底线是任何策略和任何所有者都不能覆盖的检查集合。
晋升定义底线规则并拥有 `FloorCheck`（§9）。它的成本很低，
因此**在候选提交时、在任何验证开销之前**运行：
补丁级结构规则也会在候选被记录时由基因组注册表强制执行
（[01-genome.zh-CN.md](01-genome.zh-CN.md)），
随后进化运行为提交的候选调用晋升的 `FloorCheck`（通过
`PromotionService.check_floor`）
（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。底线失败会使
判定直接为 `reject`，而不运行任何套件；结果存储在
`GateDecision` 和实验记录中，判定只引用它
（[07-verification.zh-CN.md](07-verification.zh-CN.md)）。

| 底线检查 | 规则 | 失败时 |
| --- | --- | --- |
| Schema | 补丁及其产生的修订版均通过基因组和补丁 JSON Schema 校验 | 候选不被记录 |
| 基础修订版 | `base` 等于父修订版；操作可干净地应用（v1 中不做模糊合并） | 候选不被记录 |
| 锁定基因与固定项 | 没有操作触及 `policy.locked_genes` 中列出的基因或 `policy.pins` 中的条目；没有操作触及 `policy` | 候选不被记录 |
| 允许的基因 | 每个操作都在绑定的 `allowed_genes` 范围内（其本身也在 `policy.mutable_genes` 范围内） | 候选不被记录 |
| 密钥与 PII 扫描 | 未引入密钥或 PII，使用**与仓库 pre-push hook 相同的扫描规则**（私钥、可识别的提供商令牌、bearer/JWT 凭据、凭据类字段中的高熵值），外加租户的 PII 策略 | `not_promotable` |
| 出站端点 | 不新增出站 URL，也不新增 MCP 服务器，除非对应基因已解锁 | `not_promotable` |
| 权限提升 | 没有操作扩大工具、权限或 `policy` 中的任何内容 | `not_promotable` |
| 大小与重写 | 补丁大小、文件数和文件大小在 Manifest 限制之内；修改超过文件可配置比例（默认 40%）的 `file.edit` 被标记为 `rewrite`（提升等级，§5.1） | 超出限制：`not_promotable`；重写：等级提升 |
| 预算 | 本次运行在该候选上未超出预算 | `not_promotable` |

**为什么要扫描补丁。** 经验（片段（episode）、反馈）是不可信的
输入：它是提示注入和记忆投毒的载体。挖掘片段的策略
可能会把密钥、客户的个人数据或攻击者的 URL 从片段带入记忆条目或
技能。因此，每个源自经验的补丁在可以被晋升之前，都要经过密钥、PII 和 URL
扫描。（摄入时的脱敏与经验的保留见
[02-experience.zh-CN.md](02-experience.zh-CN.md)；策略的沙箱见
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)。）

### 4.3 结果

| 结果 | 条件 | 接下来发生什么 |
| --- | --- | --- |
| `auto_promote` | 所有检查通过，等级不高于绑定的自动晋升上限，没有事件阻塞该 bot，该 bot 的进化未被冻结，且未达到每日晋升上限 | 晋升以参与者 `gate_policy` 移动 `active`（或 `canary`，§8） |
| `needs_review` | 检查 1–5 通过，但等级高于上限，或某个软条件将其送去评审（一次被容忍的回归失败、该 bot 上的 holdout 事件、达到每日晋升上限、bot 被冻结） | 开启一个 `ReviewItem` |
| `not_promotable` | 判定为 `reject` 或 `inconclusive`，或检查 1–5 中任一失败，或等级为 T3 且所涉基因未被所有者解锁 | 修订版状态变为 `rejected`；保留，永不删除 |

拒绝是正常结果，而不是错误：大多数候选都应当失败，
每个决定都会作为证据记录在实验记录 H 中。

`inconclusive` 判定不可晋升。策略可以花费更多
预算（通过验证使用更多种子或用例）或停止；门禁
只依据最终的 `accept` 做决定。

### 4.4 门禁流程

```python
def decide(c: CandidateFacts, b: BindingView, bot: BotState) -> GateDecision:
    checks = [
        c.floor,                                                                  # §4.2, run at submission
        regression_floor(c.evaluations, b.verification_profile),                  # tolerance from profile
        verdict_check(c.verdict),                                                 # accept on validation
        holdout_check(c.evaluations, bot.open_incidents),
        baseline_check(c.evaluations, required=c.strategy_platform_published or b.require_baseline),
    ]
    risk = classifier.classify(c.patch, c.policy)                                 # §5.1

    if any(ch.result == "fail" for ch in checks) or c.verdict != "accept":
        return decision(checks, risk, "not_promotable")
    if risk.tier == RiskTier.T3 and not c.policy.unlocks(risk):
        return decision(checks, risk, "not_promotable")

    soft = soft_conditions(checks, bot)        # tolerated regression failure, incident, freeze, daily limit
    within = b.auto_promote_ceiling is not None and risk.tier <= b.auto_promote_ceiling
    if within and risk.tier < RiskTier.T3 and not soft:
        return decision(checks + [approval("pass", "within ceiling")], risk, "auto_promote")
    return decision(checks + [approval("fail", why(risk, b, soft))], risk, "needs_review", soft)
```

无论上限如何设置，T3 永远不会被自动晋升。

## 5. 风险等级

### 5.1 等级表与分配

按补丁操作分配；补丁取其各操作中的最大值。

| 等级 | 示例 | 默认晋升方式 |
| --- | --- | --- |
| **T0** | 仅注释 | 自动 |
| **T1** | 记忆条目新增/更新/退役；技能描述微调；资源内容更新 | 若门禁通过且所有者策略允许，则自动 |
| **T2** | Persona 编辑（SOUL/AGENTS/RULES）；技能新增/更新；allowlist 范围内的 `engine_config`；任何被标记 `rewrite` 的编辑 | 人工评审（所有者或其委派人）；所有者可按策略将自动上限提高到 T2 |
| **T3** | MCP/CLI 工具变更、script、记忆 `replace` 模式、权限、`policy` 中的任何内容 | 默认锁定；解锁后始终人工评审，永不自动 |

从基因组补丁操作（[01-genome.zh-CN.md](01-genome.zh-CN.md)）
到等级的提议默认映射。规范性映射以数据形式承载在补丁 schema 中
（工作项 RSI-02：“每个补丁操作都有明确定义的风险等级”），并列于
[01-genome.zh-CN.md](01-genome.zh-CN.md)；两张表必须保持一致。本文
解释其背后的策略。

| 操作 | 目标 | 等级 |
| --- | --- | --- |
| 修订版记录上的注释变更 | — | T0 |
| `memory.add`、`memory.update`、`memory.retire`（模式 `seed`、`merge`） | `memory` | T1 |
| 仅涉及技能描述的 `skill.update` | `skills/<name>` | T1 |
| `file.edit` | `resources/...` | T1 |
| `file.edit` | `persona/*`（SOUL、AGENTS、RULES） | T2 |
| `skill.add`、`skill.update`（内容） | `skills/<name>` | T2 |
| `engine_config.set`（allowlist 中的键） | `engine_config.<key>` | T2 |
| 针对 `tools.mcp`、`tools.cli_tools`、`script` 的任何操作；记忆 `replace` 模式；权限变更 | 锁定基因 | T3 |
| 针对 `policy` 的任何操作 | `policy` | 被底线拒绝（永不可晋升） |

在按操作映射之后应用的提升：

- **重写标记。** 被标记 `rewrite` 的 `file.edit`（修改超过文件
  配置比例，默认 40%）至少为 T2。
  整文件重新生成会侵蚀上下文（“上下文坍塌”），因此大幅
  重写总要经人过目。
- **Guardrail diff 审计。** 提及或编辑类 guardrail 文本的补丁
  （安全章节、拒答策略、日志或上报指令、
  类似评测指令的文本）会被标记为 `guardrail_touch`，并
  至少提升到 T2。检测规则属于 [07-verification.zh-CN.md](07-verification.zh-CN.md)
  中的验证器完整性规则；等级上的
  后果在这里应用。
- **所有者覆盖。** 基因组中的 `policy.risk_overrides` 可以为该 bot 提高
  特定基因或条目的等级。覆盖永远不会降低
  底线规则，也永远不能使 T3 可自动晋升。

### 5.2 为什么按操作分级并取最大值

补丁作为一个整体被评审，因此其路径必须对其风险最高的
部分是安全的。取最大值也意味着策略无法通过把一个 persona
编辑与大量记忆条目打包来“稀释”它。按操作的等级仍会保留
（`per_op`），以便评审者看到是哪个操作决定了等级。

多迭代运行可以把若干已接受的迭代压缩成一个补丁
用于评审；实验记录保留每一步，压缩后的补丁与其他补丁一样
分级。

### 5.3 自动晋升上限

**自动晋升上限**是无需人工即可晋升的最高等级，
由所有者**按绑定**设置（因此即按策略、按 bot）。它就是
绑定字段 `auto_promote_ceiling`，发布设置则是
绑定字段 `rollout`；两者都是
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 中定义的绑定的提议字段。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The Promotion-related fields of one binding in bot_123's evolution policy (other fields elided).
{
  "id": "bind_01",
  "strategy": "clawevolve/bot-evolution@2.0.0",
  "allowed_genes": ["persona", "skills", "memory"],
  "verification_profile": "default@1",
  "auto_promote_ceiling": "T1",        // binding field (06): "T0" | "T1" | "T2" | null (never auto)
  "rollout": {"canary_share": "0.1", "auto_rollback": false}   // binding field (06), multi-instance bots only (§8)
}
```

- 默认上限：`T1`（记忆条目以及较小的描述或资源
  更新在门禁通过时自动晋升）。
- `null` 表示“始终要求人工批准”——即 DR-2 中被否决作为默认值的
  备选方案，作为所有者策略可用。
- 允许的最高值是 `T2`。T3 永不自动。
- 按 bot 和按租户的**每日最大晋升次数**是一项预算
  （[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）；达到上限后，
  自动晋升会转为评审条目。

## 6. 评审队列

评审队列保存门禁决定为 `needs_review` 的候选：它们
通过了所有自动检查，但必须由人同意。它将
ClawEvolve 的 `skill-decision` 人工批准（由
`BotSkillGateway.replaceLocalSkill` 执行 CAS 技能替换，
`contracts/bot-skill-gateway.ts`）从单个技能推广到每个 T2 补丁，
乃至更广的范围。

### 6.1 评审者看到什么

每个条目都与其 `CandidateReport`（§2.5）一同展示：

- 候选修订版相对其父修订版的 **diff**（基因组 diff，
  [01-genome.zh-CN.md](01-genome.zh-CN.md)），附带每个操作的等级以及该
  补丁获得其等级的原因；
- 策略的**理由**和**证据**（片段、发现）——每个
  补丁都必须带有理由，非平凡操作必须引用证据；
- 按划分的**验证摘要**：`validation` 上的配对均值差和
  置信区间，`regression` 和 `safety` 上新失败的用例，
  holdout 结果，验证器版本，以及父修订版与候选的每用例
  成本。每用例详情按调用方角色可见
  （[07-verification.zh-CN.md](07-verification.zh-CN.md)）；
- **标记**：`rewrite`、`guardrail_touch`、`overfit_suspect`（validation 增益
  远超 regression 和 holdout 上的增益，或分数在不同
  种子间波动）、`wins_by_spending`；
- 策略**自报的指标**，并如实标注；它们永远不
  用于接受判断；
- 完整的**门禁决定**。

### 6.2 生命周期

```text
open ──approve──▶ approved   (Promotion created; revision status → promoted)
  │
  ├──reject───▶ rejected    (revision status → rejected; kept, never deleted)
  │
  └──(parent no longer active and owner policy says so)──▶ superseded
```

- **批准**会晋升候选修订版（到 `active`，或在绑定配置了发布时到 `canary`，
  §8）。批准需要提供原因，该原因会被
  存储和审计。
- **拒绝**需要提供原因。拒绝会作为证据进入实验记录 H
  （带标签的结果为验证器校准和第 3 层提供输入）。
- **过时的基础修订版（提议）。** 候选是针对其父修订版验证的。
  如果 `active` 此后已移动（另一次晋升，或回到旧版本），批准
  它将替换一个它从未与之比较过的修订版。提议规则：
  批准时对 `active` 执行 CAS，期望值为候选的父修订版。
  发生冲突时 API 返回 `409 stale_parent`；评审者可以
  通过显式的 `override_stale_parent: true` 并附上原因（会被审计）强行晋升，
  或者拒绝它，以便策略针对新的 `active` 重新提议。
  过时条目是否应改为自动变成 `superseded`，
  是待定决策 P-1（§13）。
- 条目永不删除；已决定的条目在队列中仍可通过
  `status` 过滤器读取，并保留在实验记录中。

### 6.3 由谁评审

所有者、租户管理员或所有者指定的委派人。流水线只能
在所有者为其配置的自动晋升策略范围内批准
（§3）。T3 条目（仅在所有者解锁该基因后才可能出现）始终需要
人工。对于机制采用（第 3 层），比较报告是
必需的（[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)）。

## 7. 晋升

一次**晋升**将 bot 的 `active` 引用（或 `canary`）移动到某个修订版，并
通过现有链路将该修订版应用到运行中的 bot。这是
进化改变 bot 的唯一途径。

### 7.1 步骤

1. **对引用执行 CAS。** `active` 从期望的修订版移动到
   目标修订版，`previous` 被设为旧的 `active`，这在基因组注册表的引用上
   通过一次比较并交换完成。并发的晋升会以 `409` 失败，
   且不做任何改变。引用日志记录参与者和原因。
2. **修订版状态。** 目标修订版的状态变为 `promoted`。
3. **应用。** 修订版被编译为固定版本的 Manifest 文档加上
   记忆投影（[01-genome.zh-CN.md](01-genome.zh-CN.md)）并被应用：
   - **个人 bot：** 通过 Manifest apply。Manifest apply 是一次性
     命令，而不是控制器（ADR 0018）；apply 报告记录
     `revision_id` 和编译后文档的摘要。
   - **服务 bot：** 作为**下一个发布版本**，通过现有的
     draft → verify → publish 流程，并在发布记录上存储 `revision_id`。
     该流程的 VERIFY 阶段是影子验证
     接入的地方（§8.1），而在
     `VALIDATING → ONLINE_PUB` 转换上可选的自动验证门禁是工作项 RSI-22
     （[07-verification.zh-CN.md](07-verification.zh-CN.md)）。
4. **记录。** 一条 `Promotion` 记录和一条实验记录条目，包含参与者、原因、
   门禁决定、批准以及评测链接
   （[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）。
5. **归因。** 从此以后，片段携带新的修订版 id
   （[02-experience.zh-CN.md](02-experience.zh-CN.md)），这使得
   在线比较 `r41` 和 `r42` 成为可能。

如果应用失败，晋升的状态为 `apply_failed`，apply 报告
说明原因，所有者通过晋升 `previous` 回到旧版本（§8.4）。对于
服务 bot，`active` 相对于发布流程究竟何时移动，是
待定决策 P-2（§13）。

### 7.2 谁可以晋升什么

| 目标 | 参与者 | 要求 |
| --- | --- | --- |
| 候选修订版 | `gate_policy` | 门禁决定为 `auto_promote` |
| 候选修订版 | 用户 / 流水线（批准） | 门禁决定为 `needs_review`，并在队列中获批；流水线只能在所有者策略范围内 |
| 曾被晋升过的修订版（回到旧版本） | 所有者 / 租户管理员，或在启用自动回滚时的 `rollout_policy` | 修订版属于该 bot；常规的、受审计的晋升 |
| 所有者编写的修订版（由所有者自己的 Manifest 编辑记录而来，`draft` 引用） | 所有者 / 租户管理员 | 所有者操作，在进化门禁之外（所有者直接持有该权力）；受审计 |
| 门禁决定为 `not_promotable` 的候选修订版 | 无人 | 以 `422 not_promotable` 拒绝 |

紧急停止开关（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）在
这里被遵守：当某个 bot 的进化被冻结时（保持 `active` 不变），不会有候选被
自动晋升，批准会以 `403 evolution_frozen` 被拒绝；所有者
回到旧版本仍被允许，因为它是对错误晋升的补救手段
（提议）。

### 7.3 永不删除

被拒绝、被取代和已退役的修订版都会保留，永不删除；它们
在实验记录的归档视图中保持可见
（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）。之后任何
内容存储清理都绝不能删除可从
`promoted` 修订版到达的 blob（[01-genome.zh-CN.md](01-genome.zh-CN.md)）。

## 8. 发布与回到旧版本

### 8.1 影子（可选）

候选在镜像流量或重放的片段上运行，并使用相同的评分器离线
评分，不影响用户。对于服务 bot，这
对应于发布流程中现有的 VERIFY 阶段，该阶段目前会部署一个
验证环境 bot，但不检查任何东西。影子评分属于验证的
在线验证（[07-verification.zh-CN.md](07-verification.zh-CN.md)）；晋升
决定在 `active` 移动之前是否需要影子结果（属于
绑定的 `rollout` 字段，提议，[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。

### 8.2 金丝雀（多实例 bot）

一部分实例运行 `canary` 引用。晋升将 `canary` 移动到
候选（一次 `ref: "canary"` 的晋升）；验证使用序贯检验，将 `canary` 的在线
指标（任务成功率、用户反馈、错误率、成本）与
`active` 进行比较。当比较结果有利时，
第二次晋升将 `active` 移动到同一修订版。金丝雀不
适用于单实例（个人）bot。

### 8.3 晋升与可选的自动回滚

晋升即 §7。所有者可以按策略启用**自动回滚规则**
（绑定字段 `rollout.auto_rollback`，在 [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 中提议）：当在线验证
报告新的 `active` 出现已确认的回归（或 holdout 下降）时，
平台以参与者 `rollout_policy` 晋升 `previous`。这是一次普通的
回到旧版本的晋升，而不是一个独立的机制。

### 8.4 回到旧版本

**回到旧版本就是再次晋升一个较早的修订版。** 由于修订版 id
是内容哈希，不会创建新的修订版：`active` 移回
较早的修订版（例如 `r41`），`previous` 变为被离开的那个修订版，
引用日志以参与者和原因记录这次移动。

```text
avn genome promote --revision r41 --reason "r42 mis-routes VIP refunds"
```

对平台的其余部分而言，这只是“应用这个修订版”，与
向前晋升完全一样：个人 bot 通过 Manifest apply 获得它；服务
bot 通过现有的发布流程，将其作为**下一个发布版本**获得。
它适用于个人 bot 和服务 bot，并可回到该 bot 的任意较早修订版，
而不仅是回退一步。

**现有的服务 bot 回滚功能**（带有冻结制品的版本化发布记录，
只能回退一步；
`core/service_bot/services/publish_rollback_mixin.py:38-80`）保持
原样。RSI 既不替换也不扩展它，也不存在重复的回滚
路径。

### 8.5 晋升之后

新 `active` 相对其父修订版的在线结果（在线验证、
来自 Insight 的复发检查）会在之后被联接到实验记录中的
该实验。已确认的回归会成为新的 `regression` 用例，而错误的
接受（之后被撤回的晋升）会计入该策略的机制
指标（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)，
[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)）。

## 9. 服务接口

由进化运行（在判定最终确定后）、进化 API 层
（公开端点）以及 UI 后端使用。策略永远不调用它。

```python
from typing import Protocol

class PromotionService(Protocol):
    """Platform-owned gate, review queue, and promotion (DR-2).

    The only component allowed to move a bot's `active`, `previous`, and
    `canary` refs in the Genome Registry.
    """

    def check_floor(self, bot: BotRef, candidate_id: str) -> GateCheck:
        """Run every FloorCheck on a submitted candidate (§4.2). Called by Evolution
        Run at submission, before verification. On failure, records a GateDecision
        with outcome `not_promotable` and the verdict becomes `reject` without
        running any suite. Idempotent per candidate."""

    def decide(self, bot: BotRef, candidate_id: str) -> GateDecision:
        """Evaluate the gate for a candidate whose verdict is final.

        Called by Evolution Run when Verification reports a final verdict.
        Idempotent per (candidate, verdict, verifier version): calling again
        returns the stored decision. An `auto_promote` outcome triggers the
        promotion before returning; `needs_review` opens a ReviewItem.
        Raises VerdictPending if the verdict is still `pending`.
        """

    def reconsider(self, bot: BotRef, reason: str) -> list[GateDecision]:
        """Re-evaluate open items of a bot after a bot-level change
        (holdout incident opened or cleared, kill switch, daily limit reset).
        Never turns a decided item back into an open one."""

    def candidate_report(self, bot: BotRef, candidate_id: str,
                         viewer: Actor) -> CandidateReport:
        """Diff + verification summary + gate decision + review state.
        Per-case verification detail is filtered by the viewer's role."""

    def review_queue(self, bot: BotRef, status: ReviewStatus | None = "open",
                     min_tier: RiskTier | None = None,
                     page: int = 1, page_size: int = 20) -> "Page[ReviewItem]":
        """List review items for a bot, newest first."""

    def approve(self, bot: BotRef, candidate_id: str, actor: Actor, reason: str,
                idempotency_key: str, override_stale_parent: bool = False) -> Promotion:
        """Approve an open item and promote its revision.

        Raises NotReviewable (not in the queue), AlreadyDecided (decided with
        another idempotency key), PolicyDenied (actor may not approve this
        tier), StaleParent (`active` is no longer the candidate's parent and
        override_stale_parent is False), EvolutionFrozen.
        """

    def reject(self, bot: BotRef, candidate_id: str, actor: Actor, reason: str,
               idempotency_key: str) -> ReviewItem:
        """Reject an open item; the revision's status becomes rejected."""

    def promote(self, bot: BotRef, revision_id: str, ref: Literal["active", "canary"], expected_revision: str,
                actor: Actor, reason: str, idempotency_key: str) -> Promotion:
        """Move `ref` ("active" or "canary") to `revision_id` with CAS on
        `expected_revision`, then apply. Going back is this call with an
        earlier revision. Raises RefConflict, NotPromotable, PolicyDenied."""


class FloorCheck(Protocol):
    """One platform-floor rule (§4.2). Floor checks are plugins owned by the
    platform, never by strategies; adding one is a reviewed platform change."""

    name: Literal["schema", "base", "locked_genes_and_pins", "allowed_genes", "secret_pii_scan",
                  "outbound_endpoints", "permission_escalation", "size_and_rewrite", "budget"]   # one per row of §4.2

    def check(self, patch: "GenomePatch", parent: "GenomeRevision",
              policy: "GenomePolicy", allowed_genes: list[str]) -> GateCheck: ...


class RiskClassifier(Protocol):
    """Maps a patch to its risk tier (§5.1), including raises and owner overrides."""

    def classify(self, patch: "GenomePatch", policy: "GenomePolicy") -> TierAssessment: ...
```

底线背后的密钥与 PII 扫描器与
仓库 pre-push hook 共享规则，因此 hook 在提交中阻止的凭据
在补丁中同样会被阻止。所有者的租户 PII 策略会增加规则；它永远不会
移除共享的规则。

## 10. API

公开端点位于共享前缀 `/openapi/v1` 之下；下文路径
均相对于该前缀。共享约定（错误、分页、ETag、幂等
键）见 [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。这里的每个 `POST`
都接受 `Idempotency-Key` 请求头：一个由客户端选择的字符串，对同一个
逻辑请求的每次重试都相同，对不同请求则不同；
使用相同键的重试会返回第一次的结果，且不做任何
新操作。下文的响应展示的是标准信封中的 `data` 负载；
信封、错误、分页和幂等性见 [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。

调用方：人（UI、`avn` CLI）和确定性流水线（客户端 SDK）。
Bot 调用方随 DR-3 推迟。

### 10.1 公开端点

#### GET /bots/{bot_id}/evolution/candidates/{candidate}

候选报告：diff、验证摘要、门禁决定和评审
状态。由 UI 评审界面、`avn evolve review show` 以及
决定是否批准的流水线调用。

请求示例：

```text
GET /openapi/v1/bots/bot_123/evolution/candidates/sha256:c41e…
```

响应示例（`200`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate_id": "sha256:c41e…",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "revision_id": "sha256:7c1e…",
  "revision_seq": 42,
  "parent_revision_id": "sha256:a90b…",
  "parent_revision_seq": 41,
  "run_id": "run_7f3",
  "strategy": "clawevolve/bot-evolution",
  "strategy_version": "2.0.0",
  "patch_summary": [
    {"op_index": 0, "op": "file.edit", "target": "persona/SOUL.md", "tier": "T2", "reason": "persona edit"},
    {"op_index": 1, "op": "skill.update", "target": "skills/refund-policy", "tier": "T2", "reason": "skill content update"}
  ],
  "diff_path": "/bots/bot_123/genome/revisions/sha256:7c1e…/diff?against=sha256:a90b…",
  "rationale": "Escalation section did not cover refunds above the tier limit; 9 of 31 failed episodes were mis-escalated.",
  "evidence": ["episode:ep_91", "finding:f_12"],
  "verdict": "accept",
  "splits": [
    {"split": "validation", "mean_delta": "0.12", "ci_low": "0.05", "ci_high": "0.19", "newly_failing": 0, "detail_visible": true},
    {"split": "regression", "mean_delta": "0.00", "ci_low": null, "ci_high": null, "newly_failing": 0, "detail_visible": true},
    {"split": "safety", "mean_delta": "0.00", "ci_low": null, "ci_high": null, "newly_failing": 0, "detail_visible": true},
    {"split": "holdout", "mean_delta": "0.07", "ci_low": "0.01", "ci_high": "0.13", "newly_failing": 0, "detail_visible": false}
  ],
  "cost": {"parent_usd_per_case": "0.031", "candidate_usd_per_case": "0.033"},
  "flags": [],
  "gate": {
    "id": "gd_204",
    "outcome": "needs_review",
    "risk": {"tier": "T2", "raised_by": []},
    "auto_promote_ceiling": "T1",
    "reasons": ["risk tier T2 requires human review"]
  },
  "review": {"status": "open", "created_at": "2026-10-08T03:41:00Z"}
}
```

值得注意的错误：`404 not_found`（未知的 bot 或候选，或调用方无权
读取该 bot）。判定仍为 `pending` 的候选不是
错误：返回的报告中为 `"verdict": "pending"` 和 `"gate": null`。

#### GET /bots/{bot_id}/evolution/review-queue

列出评审条目。由 UI、`avn evolve review list` 以及
流水线调用。查询参数：`status`（默认 `open`，可选 `approved`、
`rejected`、`superseded` 或 `all`）、`min_tier`、`page`（从 1 开始）、
`page_size`（1 到 100，默认 20）。

请求示例：

```text
GET /openapi/v1/bots/bot_123/evolution/review-queue?status=open
```

响应示例（`200`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {
      "candidate_id": "sha256:c41e…",
      "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
      "revision_id": "sha256:7c1e…",
      "revision_seq": 42,
      "parent_revision_id": "sha256:a90b…",
      "run_id": "run_7f3",
      "strategy": "clawevolve/bot-evolution",
      "strategy_version": "2.0.0",
      "risk_tier": "T2",
      "gate_decision_id": "gd_204",
      "rationale": "Escalation section did not cover refunds above the tier limit; 9 of 31 failed episodes were mis-escalated.",
      "evidence": ["episode:ep_91", "finding:f_12"],
      "self_reported_metrics": {"train_pass_rate": "0.81"},
      "status": "open",
      "created_at": "2026-10-08T03:41:00Z",
      "decided_by": null,
      "decided_at": null,
      "decision_reason": null,
      "promotion_id": null
    }
  ]
}
```

值得注意的错误：`404 not_found`；对未知的 `status` 值返回
`400 invalid_argument`。

#### POST /bots/{bot_id}/evolution/candidates/{candidate}:approve

批准一个开放的评审条目并晋升其修订版。由所有者和
评审者（UI、`avn evolve review approve`）调用，也可由流水线在所有者
策略范围内调用。

请求示例：

```text
POST /openapi/v1/bots/bot_123/evolution/candidates/sha256:c41e…:approve
Idempotency-Key: review-bot_123-sha256:c41e-approve
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "reason": "Reviewed diff and verification report; escalation fix is correct.",
  "override_stale_parent": false     // optional; true only to promote over a moved `active` (audited)
}
```

响应示例（`200`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "review": {
    "candidate_id": "sha256:c41e…",
    "status": "approved",
    "decided_by": "user_owner_17",
    "decided_at": "2026-10-08T09:12:00Z",
    "decision_reason": "Reviewed diff and verification report; escalation fix is correct.",
    "promotion_id": "prm_5d2"
  },
  "promotion": {
    "id": "prm_5d2",
    "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
    "ref": "active",
    "revision_id": "sha256:7c1e…",
    "revision_seq": 42,
    "from_revision_id": "sha256:a90b…",
    "going_back": false,
    "candidate_id": "sha256:c41e…",
    "gate_decision_id": "gd_204",
    "actor": {"kind": "user", "id": "user_owner_17"},
    "reason": "Reviewed diff and verification report; escalation fix is correct.",
    "status": "applying",
    "apply": {"kind": "manifest_apply", "reference": null, "detail": "apply started"},
    "created_at": "2026-10-08T09:12:00Z"
  }
}
```

值得注意的错误：`404 not_found`；`409 not_reviewable`（候选不在
队列中，例如已被自动晋升或为 `not_promotable`）；
`409 already_decided`（之前已用不同的幂等键做出决定）；
`409 stale_parent`（自验证以来 `active` 已移动；见 §6.2）；
`403 policy_denied`（流水线批准超出其允许等级的条目）；
`403 evolution_frozen`（按 bot 的紧急停止开关）。

#### POST /bots/{bot_id}/evolution/candidates/{candidate}:reject

拒绝一个开放的评审条目。修订版的状态变为 `rejected`；它会被保留，永不删除。

请求示例：

```text
POST /openapi/v1/bots/bot_123/evolution/candidates/sha256:c41e…:reject
Idempotency-Key: review-bot_123-sha256:c41e-reject
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "reason": "The new escalation text drops the legal-hold exception."
}
```

响应示例（`200`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate_id": "sha256:c41e…",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "revision_id": "sha256:7c1e…",
  "revision_seq": 42,
  "status": "rejected",
  "decided_by": "user_owner_17",
  "decided_at": "2026-10-08T09:20:00Z",
  "decision_reason": "The new escalation text drops the legal-hold exception.",
  "promotion_id": null
}
```

值得注意的错误：`404 not_found`；`409 not_reviewable`；`409 already_decided`；
`400 invalid_argument`（原因为空）。

#### POST /bots/{bot_id}/genome/promotions

将 `active`（或 `canary`）移动到某个修订版并应用它。用于回到旧版本、
晋升所有者编写的修订版、canary → active，
并在内部被门禁和批准使用（它们调用服务接口，而不是
此端点）。由所有者和租户管理员调用（UI、
`avn genome promote`）。

请求示例（从 `r42` 回到 `r41`）：

```text
POST /openapi/v1/bots/bot_123/genome/promotions
Idempotency-Key: 4b0f6c1e-9a7d-4f53-8d1e-2c6a9e1b7f02
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "revision": "sha256:a90b…",          // r41; "r41" (seq) is also accepted
  "ref": "active",                     // "active" (default) or "canary"
  "expected_revision": "sha256:7c1e…", // CAS: what `ref` must point at now (r42)
  "reason": "r42 mis-routes VIP refunds"
}
```

响应示例（`201`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "prm_6a0",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "ref": "active",
  "revision_id": "sha256:a90b…",
  "revision_seq": 41,
  "from_revision_id": "sha256:7c1e…",
  "going_back": true,
  "candidate_id": null,
  "gate_decision_id": null,
  "actor": {"kind": "user", "id": "user_owner_17"},
  "reason": "r42 mis-routes VIP refunds",
  "status": "applying",
  "apply": {"kind": "manifest_apply", "reference": null, "detail": "apply started"},
  "created_at": "2026-10-09T08:02:00Z"
}
```

引用会立即移动；应用进度可通过晋升的
`apply` 字段查看，其来源是现有的 Manifest apply 报告（个人 bot）或
发布记录（服务 bot），两者都会记录 `revision_id`。

值得注意的错误：`404 not_found`（未知修订版或不属于该 bot）；
`409 ref_conflict`（`expected_revision` 不匹配；未做任何改变）；
`422 not_promotable`（门禁决定为
`not_promotable` 的候选修订版）；`403 policy_denied`（调用方无权晋升）；
`400 invalid_argument`（`ref` 不是 `active` 或 `canary`；
或在单实例 bot 上使用 `canary`）。

### 10.2 内部接口

不属于公开 API；列出它们以使边界清晰。

| 接口 | 调用方 → 被调用方 | 用途 |
| --- | --- | --- |
| `PromotionService.check_floor(bot, candidate)`（运行每个 `FloorCheck`） | 进化运行 → 晋升 | 在候选提交时、验证之前执行静态底线 |
| `PromotionService.decide(bot, candidate)` | 进化运行 → 晋升 | 判定最终确定后评估门禁 |
| `PromotionService.reconsider(bot, reason)` | 验证（holdout 事件）、进化运行（紧急停止开关）→ 晋升 | bot 级变更后重新评估开放条目 |
| `verify(candidate, profile)` | 晋升 → 验证（仅当门禁需要尚不可用的 holdout 或基线结果时） | 请求缺失的评测（[07-verification.zh-CN.md](07-verification.zh-CN.md)） |
| 引用 CAS（`active`、`previous`、`canary`） | 晋升 → 基因组注册表 | 移动引用；只有晋升可以移动这三个引用 |
| Manifest apply / 发布流程 | 晋升 → 现有 Backend 服务 | 应用已晋升的修订版 |
| 实验记录追加 | 晋升 → 实验记录 H | 门禁决定、批准、拒绝、晋升 |

策略看不到以上任何接口。它们对结果的视角是通过候选 id
查询到的判定（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。

## 11. 示例

### 11.1 在策略范围内批准的夜间流水线

```python
# Illustrative only
from avernet_evolution import Client, RiskTier

c = Client.from_env()
run_id = c.runs.start(bot_id="bot_123", binding="bind_01",      # bind_01 runs clawevolve/bot-evolution@2.0.0
                      budget={"max_usd": 10},
                      idempotency_key="nightly-bot_123-2026-10-08")   # same key on every retry
run = c.runs.wait(run_id)                     # repeated short status lookups by id

for cand in run.candidates():
    report = c.candidates.get(bot_id="bot_123", candidate=cand.id)
    if report.gate is None or report.gate.outcome != "needs_review":
        continue                              # auto-promoted, not promotable, or still pending
    if report.gate.risk.tier <= RiskTier.T1 and not report.flags:
        # Only reached when the owner turned gate auto-promotion off but allowed
        # this pipeline to approve T1; anything higher is left for a human.
        c.review.approve(bot_id="bot_123", candidate=cand.id,
                         reason="nightly auto-policy",
                         idempotency_key=f"approve-bot_123-{cand.id}")
```

同样的调用也支撑着 CLI 和 UI 后端。

### 11.2 通过 CLI 进行人工评审

```text
$ avn evolve review list --bot bot_123
CANDIDATE        REV  TIER  STRATEGY                         VERDICT  CREATED
sha256:c41e…     r42  T2    clawevolve/bot-evolution@2.0.0   accept   2026-10-08T03:41Z

$ avn evolve review show sha256:c41e… --bot bot_123      # report: diff, splits, flags, gate
$ avn genome diff r42 --against r41 --bot bot_123
$ avn evolve review approve sha256:c41e… --bot bot_123 --reason "escalation fix is correct" --yes
```

退出码遵循共享的 CLI 约定
（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）：例如 `4` 表示
`stale_parent` 之类的冲突，`5` 表示 `policy_denied`。

### 11.3 回到旧版本

```python
# Illustrative only
refs = c.genome.refs(bot_id="bot_123")        # active = r42, previous = r41
promo = c.genome.promote(bot_id="bot_123",
                         revision=refs.previous,
                         expected_revision=refs.active,
                         reason="r42 mis-routes VIP refunds",
                         idempotency_key="goback-bot_123-r42-to-r41")
assert promo.going_back
```

对于服务 bot，同样的调用会将 `r41` 作为下一个发布
版本发布；不会使用现有的一步式服务 bot 回滚。

### 11.4 门禁对 T1 记忆候选做出决定

`bot_123` 上的一次 `platform/consolidate-memory@1.0.0` 运行提交了一个补丁，其中包含
两个 `memory.add` 操作和一个 `memory.retire` 操作。验证在 `default@1` 下返回 `accept`，
并附带回归和矛盾检查。门禁
记录：底线通过（扫描干净）、回归底线通过、判定通过、
运行最终候选的 holdout 通过、无需基线、等级 `T1`
≤ 上限 `T1`。结果为 `auto_promote`；晋升以参与者
`gate_policy` 移动 `active`，bot 后续的会话携带新的修订版 id。假如
任何操作触及了 persona 文本，等级就会是 T2，候选就会
在评审队列中等待。

## 12. 交互

| 其他部分 | 方向 | 流转内容 |
| --- | --- | --- |
| 基因组（[01-genome.zh-CN.md](01-genome.zh-CN.md)） | 晋升 → 基因组 | 对 `active`、`previous`、`canary` 的 CAS 移动；修订版状态变更（`promoted`、`rejected`）；读取补丁、diff 和 `policy` |
| 基因组（[01-genome.zh-CN.md](01-genome.zh-CN.md)） | 基因组 → 晋升 | 记录时的补丁级底线结果（基础修订版、锁定基因、固定项、rewrite 标记） |
| 经验（[02-experience.zh-CN.md](02-experience.zh-CN.md)） | 晋升 → 经验（间接） | 晋升之后，片段携带新的修订版 id |
| 进化策略（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)） | 无直接交互 | 策略只提交候选并读取判定；永远看不到门禁决定或队列 |
| 默认进化策略（[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)） | — | ClawEvolve 的接受规则变为内部提交过滤器；其 `skill-decision` 审批变为本评审队列 |
| 实验记录 H（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)） | 晋升 → 实验记录 | 门禁决定、批准、拒绝、晋升、回到旧版本，附带参与者和原因 |
| 进化运行（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)） | 运行 → 晋升 | 提交时的 `check_floor`；判定最终确定时的 `decide`；绑定字段（允许的基因、配置、自动晋升上限、发布）；预算、每日晋升上限、紧急停止开关 |
| 验证（[07-verification.zh-CN.md](07-verification.zh-CN.md)） | 验证 → 晋升 | 判定、按划分的证据、holdout 与基线结果、在线（影子/金丝雀）结果、holdout 事件 |
| 验证（[07-verification.zh-CN.md](07-verification.zh-CN.md)） | 晋升 → 验证 | 缺少 holdout 或基线结果时调用 `verify(candidate, profile)` |
| 进化 API（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)） | API → 晋升 | §10 的公开端点；SDK 和 `avn` 命令 |
| 元进化（[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)） | 元进化 → 晋升 | 通过独立的门禁配置进行机制采用（默认 T3，需要比较报告） |
| 现有的 Manifest apply 与服务 bot 发布流程（Backend） | 晋升 → 现有服务 | 应用已晋升的修订版；apply 报告和发布记录上的 `revision_id` |

## 13. 待定决策

- **P-1：批准时父修订版过时。** 是对候选的父修订版执行 CAS 批准并提供
  显式覆盖（§6.2 中的提议），还是自动将过时条目标记为
  `superseded` 并要求策略重新提议？
- **P-2：服务 bot 的 `active` 何时移动。** 在批准时（期望状态
  先行，发布随后），还是仅在发布流程到达
  `ONLINE_PUB` 时？后者使 `active` 与用户所见保持一致，但使
  晋升目前要等待一个手动的“上线”步骤。
- **P-3：Holdout 节奏。** 时机定义在
  [07-verification.zh-CN.md](07-verification.zh-CN.md) 中：在晋升前对一次运行的最终候选
  评分一次（当配置要求时），并定期在
  `active` 上评分。此处待定的是：定期评分的时间表，以及一个事件阻止
  自动晋升的时长。
- **P-4：所有者编写的修订版与底线。** 所有者的编辑在
  进化门禁之外。底线的密钥/PII 扫描是否仍应对其运行
  （建议性还是阻断性）？
- **P-5：自动回滚规则。** 哪些在线信号可以触发它（来自金丝雀的已确认
  回归、holdout 下降、错误率飙升），以及是否
  允许用于服务 bot（在服务 bot 上回到旧版本意味着一次发布）。
- **P-6：受委派评审者。** 所有者如何指定委派人，以及对于某些基因，T2
  评审是否可以要求两名评审者。在授权设计完成之前
  保持最小化。
- **P-7：DR-2 的采纳。** DR-2 目前为提议状态；采纳后将被提升到 `docs/adr/`
  （工作项 RSI-01）。

已解决：

- **默认回归容差。** [07-verification.zh-CN.md](07-verification.zh-CN.md) 中的
  `default@1`：`safety` 新失败用例为零；
  `regression` 容差为 1，且已用掉的容差
  （`tolerance_used: true`）会将候选转交人工评审（§4.1、§4.3）。
- **评审队列端点由谁提供。** 基因组与晋升
  端点（包括 `/bots/{bot_id}/evolution/candidates/…` 和
  `/bots/{bot_id}/evolution/review-queue` 路径）由 Backend 提供；
  经验、策略注册表、进化运行、验证、实验记录和
  元进化端点由 `apps/evolution` 提供；
  [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) 将它们呈现在统一的公开
  `/openapi/v1` 接口面下。这遵循 D-1 的推荐选项
  （[design.zh-CN.md](design.zh-CN.md)）。
