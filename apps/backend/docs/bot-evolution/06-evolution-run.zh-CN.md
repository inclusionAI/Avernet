# 进化运行

> English version: [06-evolution-run.md](06-evolution-run.md)

> 状态：草案（DRAFT）。[Bot 进化架构](design.zh-CN.md)中的一个服务。
> 说明 Bot 的进化策略配置如何转化为运行，以及平台如何执行一次运行：
> 触发器、幂等提交、租约作业与重新派发、长时操作、运行时与作业协议、
> 沙箱，以及预算与紧急停止开关。

## 1. 目的与范围

进化运行（Evolution Run）服务（早期草稿中的组件 C4，即 *Run Orchestrator*）
是平台中针对 Bot 执行进化策略的部分。**进化策略**（Strategy）是带版本的代码，
只有一个方法 `run(ctx)`，用于提议对 Bot 的变更（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。
一次**运行**（run）是一个策略针对一个 Bot 的一次执行。本服务是运行的持久化状态
机：它在触发器要求时发起运行，冻结运行的输入，向策略交付一个恰好包含其被授予
能力的上下文，使运行在崩溃后依然存活，把每一笔成本都计入预算，并记录策略提交
的内容。

它负责：

| 负责 | 概要 | 章节 |
| --- | --- | --- |
| 进化策略配置与绑定 | 按 Bot 配置：运行哪些策略、何时运行、从哪个父版本开始、可以改变什么、验证有多严格、预算与参数是什么 | [§3](#3-进化策略配置与绑定) |
| 触发器 | 创建运行的定时、手动和事件触发器 | [§4](#4-触发器) |
| 运行生命周期 | `queued → running → completed \| failed \| cancelled \| budget_exhausted`；启动、运行期间、结束、取消 | [§5](#5-运行生命周期) |
| 幂等的运行提交 | 提交返回运行 id；使用相同幂等键的重复提交返回相同的 id | [§6](#6-幂等提交与按-id-查询状态) |
| 租约与重新派发 | 运行的每次尝试都是一个租约作业；已死亡 worker 的运行会作为一个新作业、以相同的运行 id 再次派发 | [§7](#7-租约重新派发与崩溃恢复) |
| 长时操作（平台侧） | 智能体会话和训练评估是带 id 的持久化操作，由平台执行 | [§8](#8-长时操作平台侧) |
| 运行时与作业协议 | 进程内策略，以及通过 HTTP 访问 `ctx` 的 job-worker 容器 | [§9](#9-运行时与作业协议) |
| 沙箱 | 策略只在沙箱物化副本上工作，自身没有凭证、出网能力或模型密钥 | [§10](#10-沙箱) |
| 预算与紧急停止开关 | 由平台强制执行的单次运行预算；Bot 级和租户级上限；按策略、按 Bot 和全局的紧急停止开关 | [§11](#11-预算与紧急停止开关) |

它**不**负责：

| 不在此负责 | 负责方 |
| --- | --- |
| 基因组修订版、引用（ref）、补丁、内容存储。本服务通过 Genome API 创建候选修订版并移动 `candidate/<run>/<n>` 引用 | [01-genome.zh-CN.md](01-genome.zh-CN.md) |
| 片段（episode）与反馈，以及 `experience.*` 背后的会话导出提供方 | [02-experience.zh-CN.md](02-experience.zh-CN.md) |
| 策略端口、策略所见的 `StrategyContext`、能力目录、注册记录、智能体定义、Strategy Registry、策略 SDK 与一致性测试套件 | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| 作为策略的 ClawEvolve 与 `platform/consolidate-memory` | [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md) |
| 实验记录 H，记录运行、候选、成本与所用模型 | [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) |
| 套件、评分器、验证配置、判定（`verify(candidate, profile)`） | [07-verification.zh-CN.md](07-verification.zh-CN.md) |
| 门禁、风险等级、评审队列、晋升与回到旧版本、补丁扫描 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 共享 API 约定（错误信封、ETag、幂等键规则）、SDK、`avn` CLI | [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) |
| Level 3（改进机制本身） | [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) |

策略设计中的两条原则塑造了本服务：

- **唯一出口。** 策略只能通过其上下文访问平台。因此，无论策略是什么，隔离和
  预算都在这里、在同一处强制执行。
- **策略提议；平台决定。** 运行提交候选。记录、验证、门禁和晋升始终由平台负责
  （[DR-2](decisions/0002-promotion-is-platform-owned.zh-CN.md)）。运行永远不能
  移动 Bot 的 `active` 引用。

**位置。** 本服务位于新模块 `apps/evolution`（待定决策 D-1 的推荐方案 A，见
[design.zh-CN.md](design.zh-CN.md)），与 Strategy Registry、Experience Store、Verification
Service 和 Experiment Ledger 并列。运行是长时、大量调用 LLM、突发性的工作，
应当独立于 Backend 的请求服务进行扩缩容和故障隔离。本服务泛化了 ClawEvolve 的
`ce_tasks` / `ce_steps` 表和 claim-report 端点（ClawWeb 控制面中的
`routes/internal/evolve.ts`），并在默认进化策略中取代它们
（[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)）。对于 singlebox，它有
一个本地配置：SQLite 存储和进程内策略（[work-items.zh-CN.md](work-items.zh-CN.md) 中的工作项
RSI-08）。

## 2. 领域模型

| 类型 | 含义 | 归属 | 生命周期 |
| --- | --- | --- | --- |
| `EvolutionPolicy` | 一个 Bot 的绑定列表 | Bot 所有者 / 租户管理员（存储在此） | 通过 `PUT …/policy` 整体替换，以 ETag 标识版本 |
| `Binding` | 策略配置中的一项：策略版本、触发器、父版本、允许的基因、验证配置、预算、参数，以及（提议）自动晋升上限和发布 | Bot 所有者 / 租户管理员 | 随策略配置创建、修改或删除；每次变更都会针对 Bot 进行检查 |
| `Trigger` | 使绑定创建运行的原因：定时、仅手动，或平台事件 | 绑定的一部分 | 同绑定 |
| `Budget` | 单次运行的花费上限：美元、挂钟时间、rollout 次数、token | 绑定的一部分；在运行上冻结 | 运行开始时冻结；已花费的数额永不重置 |
| `Run` | 绑定的一次执行，策略版本、参数、父版本和预算在开始时冻结；运行 id 是它唯一的句柄 | 平台（本服务） | `queued → running → completed \| failed \| cancelled \| budget_exhausted`；永不删除 |
| `Job` | 运行的一次派发尝试，由 worker 在租约下执行：持有者、租约到期时间、尝试次数、fencing token | 平台（本服务） | 每次尝试一个；重新派发会为同一运行创建新作业；其尝试结束时关闭 |
| `Operation` | 由运行发起、由平台持久化并执行的长时能力调用（智能体会话、训练评估） | 平台（本服务） | `queued → running → succeeded \| failed \| cancelled`；运行结束时取消未完成的操作 |
| `Workspace` | 为某次运行以某个键创建的修订版沙箱物化副本 | 平台（本服务） | 按 `(run, key)` 幂等创建；运行结束后丢弃 |
| `RunSummary` | `run(ctx)` 正常结束时返回的内容 | 策略 | 存储在运行上 |
| `Candidate`、`Verdict` | 运行提交的基因组补丁，以及平台对它的验证结果 | 定义于 [03-strategy.zh-CN.md](03-strategy.zh-CN.md)；判定由 [07-verification.zh-CN.md](07-verification.zh-CN.md) 产生 | 候选 id = 补丁的内容哈希；即使运行失败也会保留 |
| `StrategyContext` | 运行通往平台的唯一出口 | 定义于 [03-strategy.zh-CN.md](03-strategy.zh-CN.md)；由本服务构建 | 每次尝试构建一次 |

### 2.1 Budget

**预算**是单次运行的花费上限。每一次模型调用、智能体会话和评估都计入预算，
任一维度耗尽时运行即停止。

**rollout** 是针对一个 Bot 版本执行一个评估用例的一次执行。例如，针对沙箱候选运行一次
测试用例“部分退款”就是一次 rollout；用 3 个种子运行它（重复 3 次，以平均掉模型的
随机性）是 3 次 rollout；为了比较而同时针对父版本和候选运行它，两者都计数。策略启动的
训练评估按 rollout 计数；对已提交候选的验证不计入运行的预算（§11.1）。

```python
@dataclass(frozen=True)
class Budget:
    max_usd: float                      # US dollars the run may spend in total: model calls, agent
                                        # sessions, and train evaluations. At the limit the next charged
                                        # call fails and the run ends as budget_exhausted
    max_wall_clock_s: int               # seconds from the run's first start to its end, including time
                                        # spent waiting and between attempts (§7.3); at the limit the run ends
    max_rollouts: int | None = None     # how many evaluation rollouts (see above) the run may use;
                                        # None = no limit on this dimension
    max_tokens: int | None = None       # model tokens (input + output, all calls) the run may use;
                                        # None = no limit on this dimension

@dataclass(frozen=True)
class BudgetUsage:                      # what has been spent so far, in the same units as Budget
    usd: float                          # US dollars charged so far
    wall_clock_s: int                   # seconds since the run first started
    rollouts: int                       # evaluation rollouts charged so far
    tokens: int                         # model tokens charged so far
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  // Limits: at most $20, 2 hours, and 400 rollouts (for example 20 train cases x 2 versions x 10 rounds);
  // no token limit because max_tokens is omitted.
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200, "max_rollouts": 400},
  // Spent so far: $6.42, about 30 minutes, 96 rollouts, 412,300 tokens.
  "budget_used": {"usd": 6.42, "wall_clock_s": 1830, "rollouts": 96, "tokens": 412300}
}
```

### 2.2 Trigger 与 Binding

```python
@dataclass(frozen=True)
class ScheduleTrigger:
    schedule: str                       # cron expression, evaluated in UTC

@dataclass(frozen=True)
class ManualTrigger:
    manual: Literal[True]               # no automatic firing; runs only when submitted

# Platform events a binding can be triggered by (proposed list, from §4). A new
# event is added by a reviewed platform change, together with its producer.
EventName = Literal[
    "failure_rate_alert",               # the bot's failure rate crossed its alert threshold
    "clawinsight_improvement_item",     # ClawInsight recorded an improvement item for the bot
    "feedback_threshold_reached",       # N new feedback items arrived since the last run (memory consolidation)
]

@dataclass(frozen=True)
class EventTrigger:
    event: EventName                    # fire a run when this platform event arrives for the bot

Trigger = ScheduleTrigger | ManualTrigger | EventTrigger

@dataclass(frozen=True)
class Rollout:                          # proposed; read by Promotion (08-promotion.md). How a promoted
                                        # revision reaches a multi-instance bot; not the evaluation
                                        # "rollouts" counted in Budget
    canary_share: str                   # share of instances on `canary`, decimal string, e.g. "0.1"
    auto_rollback: bool                 # owner-enabled auto-rollback rule

@dataclass(frozen=True)
class Binding:
    id: str                             # chosen by the owner, unique within the bot, e.g. "bind_01"
    strategy: str                       # "<strategy id>@<version>", a registered, conformant version
    trigger: Trigger
    parent: "SelectorName"              # which revision runs start from (05-experiment-ledger.md §7);
                                        # only "active" is accepted in the first iteration
    allowed_genes: list[str]            # what this strategy may change on THIS bot; within the bot's policy
    verification_profile: str           # e.g. "default@1"; owners may pick a stricter one, never a looser one
    budget: Budget
    params: dict                        # validated against the registration's params_schema, if any, and by the strategy
    auto_promote_ceiling: Literal["T0", "T1", "T2"] | None = "T1"   # proposed; None = never auto-promote
    rollout: Rollout | None = None      # proposed; multi-instance bots only; None = promote `active` directly
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "bind_01",
  "strategy": "clawevolve/bot-evolution@2.0.0",
  "trigger": {"schedule": "0 2 * * *"},             // or {"manual": true}, {"event": "failure_rate_alert"}
  "parent": "active",
  "allowed_genes": ["persona", "skills"],
  "verification_profile": "default@1",
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60},
  "auto_promote_ceiling": "T1",                      // proposed: "T0" | "T1" | "T2" | null (never auto)
  "rollout": {"canary_share": "0.1", "auto_rollback": false}   // proposed: multi-instance bots only
}
```

### 2.3 EvolutionPolicy

每个 Bot 恰好有**一个**进化策略配置，该配置是一个绑定列表。每个绑定把一个策略挂接到
Bot 上，并带有自己的触发器、允许的基因、验证配置、预算和参数。多个绑定意味着多个策略
（或以不同设置运行的同一策略）按不同的时间表改进 Bot 的不同方面：例如，`bind_01` 每晚
对 `persona` 和 `skills` 运行 ClawEvolve，`bind_02` 每周对 `memory` 运行
`platform/consolidate-memory`，如下面的示例所示。绑定彼此独立运行：每个绑定同一时间
最多只有一个活动运行（§4），但不同绑定的运行可以重叠。当两个绑定的候选基于同一个父
版本构建，且其中一个先被晋升时，另一个获批时 `active` 已经移动；此时除非评审者显式
覆盖，晋升会以 `409 stale_parent` 拒绝第二个候选
（[08-promotion.zh-CN.md](08-promotion.zh-CN.md) 的 §6.2）。

```python
@dataclass(frozen=True)
class EvolutionPolicy:
    bot: BotRef                         # the bot this policy belongs to (owner + bot id, 09-evolution-api.md §2.7)
    bindings: list[Binding]             # one entry per strategy attached to the bot
    etag: str                           # changes on every successful PUT; used with If-Match
    updated_at: datetime
    updated_by: str                     # user id of whoever wrote this version
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Evolution policy of bot_123 (OpenClaw support bot)
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "bindings": [
    {
      "id": "bind_01",
      "strategy": "clawevolve/bot-evolution@2.0.0",
      "trigger": {"schedule": "0 2 * * *"},
      "parent": "active",
      "allowed_genes": ["persona", "skills"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
      "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60}
    },
    {
      "id": "bind_02",
      "strategy": "platform/consolidate-memory@1.0.0",
      "trigger": {"schedule": "0 4 * * 0"},
      "parent": "active",
      "allowed_genes": ["memory"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 5, "max_wall_clock_s": 1800},
      "params": {}
    }
  ],
  "etag": "\"pol-v12\"",
  "updated_at": "2026-10-06T08:00:00Z",
  "updated_by": "user_owner_5"
}
```

Bot 级上限和 Bot 级冻结开关（§11）被提议作为该文档的附加字段；
见 [§17](#17-待定决策)。

### 2.4 Run

**运行**是为一个 Bot 执行一个绑定的一次执行：平台在启动时冻结的父修订版上、在绑定的
预算下，启动一次该绑定的策略。它是这次执行的持久业务记录（冻结了什么、花费了什么、
提交了什么、如何结束），也是**调用方使用的唯一句柄**：调用方通过运行 id 启动它、查询
它、列出其候选并取消它。运行永远不会消失，也永远不会以新 id 重新开始，即使其 worker
崩溃也是如此。

```python
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled", "budget_exhausted"]
# Why a run ended, when the status alone does not say (proposed; extended only by a reviewed change).
EndReason = Literal[
    "worker_failed",                    # the strategy reported a non-retryable failure
    "max_attempts",                     # re-dispatched max_attempts times without finishing (§7.2)
    "cancelled_by_owner",               # a caller cancelled it (POST …/runs/{run}:cancel)
    "kill_switch",                      # a strategy, bot, or global kill switch stopped it (§11.3)
    "consecutive_rejections",           # the escalation rule stopped it (§11.2)
]

@dataclass(frozen=True)
class TriggerRecord:
    kind: Literal["schedule", "manual", "event"]
    fire_time: datetime | None          # set for schedule triggers
    event_id: str | None                # set for event triggers
    requested_by: str | None            # set for manual submissions: the caller's user or pipeline client id

@dataclass
class Run:
    id: str                             # "run_7f3"; globally unique; the only handle
    bot: BotRef                         # the bot it runs against (owner + bot id)
    binding_id: str
    idempotency_key: str                # (owner_id, bot_id, key) -> run id
    trigger: TriggerRecord
    # frozen at submission
    strategy: str                       # "clawevolve/bot-evolution"
    strategy_version: str               # "2.0.0"
    parent_ref: "SelectorName"          # the binding's `parent` at submission, e.g. "active"
    parent_revision: str                # resolved revision id, e.g. "sha256:a90b…"
    allowed_genes: list[str]
    verification_profile: str
    params: dict
    budget: Budget
    granted: list[str]                  # capabilities in the context, always-granted ones included
    agent_definitions: dict[str, str]   # definition name -> digest, recorded for attribution
    # changing
    status: RunStatus
    attempt: int                        # 1 on first dispatch, +1 on each re-dispatch
    max_attempts: int
    budget_used: BudgetUsage
    candidate_count: int
    created_at: datetime
    started_at: datetime | None         # first transition to running
    ended_at: datetime | None
    end_reason: EndReason | None        # None while running, and for completed or budget_exhausted runs
    summary: dict | None                # RunSummary from a completed run
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "run_7f3",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "binding_id": "bind_01",
  "idempotency_key": "bind_01/2026-10-08T02:00:00Z",
  "trigger": {"kind": "schedule", "fire_time": "2026-10-08T02:00:00Z", "event_id": null, "requested_by": null},
  "strategy": "clawevolve/bot-evolution",
  "strategy_version": "2.0.0",
  "parent_ref": "active",
  "parent_revision": "sha256:a90b…",                 // r41
  "allowed_genes": ["persona", "skills"],
  "verification_profile": "default@1",
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60},
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "granted": ["parent", "workspace", "operations", "budget", "log", "artifacts", "cancelled",
              "candidates@1", "models@1", "experience.sessions@1", "agents@1", "evaluate.train@1"],
  "agent_definitions": {"clawevolve-tune": "sha256:5d0e…", "clawevolve-review": "sha256:8b17…"},
  "status": "running",
  "attempt": 2,                                       // re-dispatched once after a worker crash
  "max_attempts": 3,
  "budget_used": {"usd": 6.42, "wall_clock_s": 1830, "rollouts": 96, "tokens": 412300},
  "candidate_count": 1,
  "created_at": "2026-10-08T02:00:03Z",
  "started_at": "2026-10-08T02:00:09Z",
  "ended_at": null,
  "end_reason": null,
  "summary": null
}
```

### 2.5 Job

**作业**（job）是运行的一次派发尝试：worker 认领（claim）并在**租约**（lease）下
执行的单元，租约是 worker 必须持续续期的限时认领。一个运行每次尝试对应一个作业：当
worker 崩溃且其租约到期时，平台把该运行作为一个新作业（尝试 `n + 1`）重新派发，对应的
仍是**同一个**运行。**fencing token** 是随每次租约签发的值；携带旧 token 的调用会被
拒绝，因此失去租约的 worker 无法干扰新的持有者。调用方从不看到作业；worker 只看到作业。

| | 运行 | 作业 |
| --- | --- | --- |
| 代表什么 | 为一个 Bot 执行一个绑定的一次执行：业务记录（冻结输入、花费、候选、结果） | 在 worker 上、在租约下执行该运行的一次尝试 |
| 生命周期 | 从提交到终止状态；永久保留 | 从派发到该尝试完成、失败或租约到期 |
| 数量 | 每次提交（每个幂等键）一个 | 运行的每次尝试一个：1 个，崩溃后更多 |
| 谁能看到 | 调用方（API、SDK、CLI、UI）通过运行 id；策略通过 `ctx.run_id` | 仅 worker 和作业协议，通过作业 id |

```python
@dataclass
class Job:
    id: str                             # "job_7f3_2" (run_7f3, attempt 2); one job per attempt of the run
    run_id: str                         # the run this attempt executes
    attempt: int                        # which attempt of the run this job is (1, 2, …)
    worker_id: str | None               # current holder; None while queued
    lease_expires_at: datetime | None
    fencing_token: str | None           # new value on every claim
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "job_7f3_2",
  "run_id": "run_7f3",
  "attempt": 2,
  "worker_id": "worker-clawevolve-02",
  "lease_expires_at": "2026-10-08T02:31:10Z",
  "fencing_token": "ft_7f3_2_b81c"
}
```

### 2.6 Operation

**操作**（operation）是一种其工作可能比一次短请求持续更久的能力调用：智能体会话
（`agents.start`）或训练评估（`evaluate.start_train`）。启动它会立即返回一个
操作 id；其状态按 id 查询。

```python
OperationStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

@dataclass
class Operation:
    id: str                             # "op_19a"
    run_id: str
    kind: Literal["agent_session", "train_evaluation"]   # same values as Operation.kind in 03-strategy.md
    idempotency_key: str                # "<run>/<own step>", e.g. "run_7f3/round-1/tune"
    status: OperationStatus
    cost: BudgetUsage                   # charged to the run's budget as it accrues
    created_at: datetime
    finished_at: datetime | None
    result: dict | None                 # AgentResult or TrainResult once succeeded
    error: dict | None                  # set when failed
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "op_19a",
  "run_id": "run_7f3",
  "kind": "agent_session",
  "idempotency_key": "run_7f3/round-1/tune",
  "status": "succeeded",
  "cost": {"usd": 1.12, "wall_clock_s": 640, "rollouts": 0, "tokens": 88100},
  "created_at": "2026-10-08T02:12:40Z",
  "finished_at": "2026-10-08T02:23:20Z",
  "result": {
    "definition": "clawevolve-tune",
    "exit_status": "completed",
    "transcript_artifact": "art_tune_r1",
    "changed_files": ["persona/SOUL.md", "skills/refund-policy/SKILL.md"]
  },
  "error": null
}
```

### 2.7 Workspace

**工作区**（workspace）是基因组修订版的沙箱物化副本：修订版的文件被铺开供智能体
编辑，与线上 Bot 隔离。物化按 `(run, key)` 幂等，因此重新派发的运行会拿回同一个
沙箱，包括智能体操作已经做出的编辑。

```python
@dataclass(frozen=True)
class Workspace:
    id: str                             # "ws_7f3_r1"
    run_id: str
    key: str                            # "run_7f3/round-1"
    revision: str                       # revision it was materialised from
    created_at: datetime
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"id": "ws_7f3_r1", "run_id": "run_7f3", "key": "run_7f3/round-1",
 "revision": "sha256:a90b…", "created_at": "2026-10-08T02:12:31Z"}
```

### 2.8 RunSummary

```python
@dataclass(frozen=True)
class RunSummary:
    rounds: int | None = None           # strategy-defined; None if the strategy has no rounds
    notes: str = ""
    extra: dict = field(default_factory=dict)   # strategy-specific, shown to owners
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"rounds": 3, "notes": "two candidates submitted; round 2 accepted", "extra": {"findings": 5}}
```

## 3. 进化策略配置与绑定

Bot 的**进化策略配置**（evolution policy）是一个**绑定**（binding）列表。不同的
Bot 使用不同的策略，一个 Bot 也可以使用多个策略（例如每晚对 persona 和 skills
运行 ClawEvolve，每周对 memory 运行记忆整合）；多个绑定的含义见 §2.3。

关于策略的信息按其对谁成立来划分：

- **关于代码的事实需要注册。** “ClawEvolve 2.0.0 驱动 OpenClaw 智能体并读取
  对话历史”无论哪个 Bot 使用它都成立，因此只在策略的注册记录中记录一次
  （[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。
- **关于 Bot 的选择需要配置。** “bot_123 每晚运行 ClawEvolve，可以修改 persona
  和 skills，预算为 20 美元”是针对单个 Bot 的决定，因此它位于该 Bot 的绑定中，
  由 Bot 所有者负责，并且可以随时修改而无需触碰策略。

触发器、父版本选择、允许的基因和验证严格程度都是由 Bot 所有者负责的绑定字段，
它们不是策略代码。

### 3.1 绑定字段

| 字段 | 含义 | 规则 |
| --- | --- | --- |
| `id` | 绑定在 Bot 内的稳定 id | 由所有者选择；在 Bot 内唯一；运行会引用它 |
| `strategy` | 已注册策略版本的 `<id>@<version>` | 必须已注册并通过一致性测试套件（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）；未被紧急停止开关禁用 |
| `trigger` | 何时创建运行（[§4](#4-触发器)） | 定时、手动、事件三者之一 |
| `parent` | 运行从哪个修订版开始 | 第一次迭代中为 `active`。其他选择器（latest-best、Pareto 前沿、MAP-Elites 生态位、clade 得分）是之后查询实验记录的可选项（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)） |
| `allowed_genes` | 该策略在此 Bot 上可以修改什么 | 必须保持在 Bot 基因组的 `policy` 之内：锁定基因保持锁定（[01-genome.zh-CN.md](01-genome.zh-CN.md)） |
| `verification_profile` | 由哪个验证配置评判候选 | 所有者可以选择比平台默认更严格的配置，但不能更宽松（[07-verification.zh-CN.md](07-verification.zh-CN.md)） |
| `budget` | 单次运行上限 | 必须落在 Bot 级和租户级上限之内（[§11](#11-预算与紧急停止开关)） |
| `params` | 策略参数 | 若注册中存在 `params_schema` 则据其校验（检查 8，§3.2），并由策略自行校验 |
| `auto_promote_ceiling` *（提议）* | 晋升无需人工即可晋升的最高风险等级 | `T0`、`T1`（默认）、`T2` 或 `null`（始终评审）；永不为 `T3`。由晋升读取（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)） |
| `rollout` *（提议）* | 金丝雀比例和自动回滚规则 | 仅限多实例 Bot；`null` 表示直接晋升 `active`。由晋升读取（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)） |

### 3.2 绑定检查

写入策略配置时，每个新增或修改的绑定都会针对 Bot 进行检查。不匹配会在配置时
被拒绝，而不是在一次付费运行的中途：

1. 策略版本已注册、符合一致性要求且未被禁用。
2. 策略 `needs` 中的每个能力在该 Bot 的引擎上都有提供方（例如，会话导出为
   OpenClaw 提供 `experience.sessions`）。
3. 如果策略声明了 `agents@1`，则该 Bot 的引擎位于其智能体定义的 `engine` 值
   之中。没有单独的 `supports_engines` 列表；引擎兼容性由 `needs` 推导得出。
4. `allowed_genes` 在 Bot 基因组的 `policy` 之内：不含锁定基因，不含固定
   （pinned）项。
5. `verification_profile` 存在，且不比平台默认更宽松。
6. `budget` 落在 Bot 级和租户级上限之内。
7. `trigger` 格式正确：有效的 cron 表达式，或一个 `EventName`
   （§2.2）。
8. 如果策略的注册记录中有 `params_schema`（提议，
   [03-strategy.zh-CN.md](03-strategy.zh-CN.md)），则 `params` 需通过其校验。

提交运行时会再次执行相同的检查，因为自写入策略配置以来，Bot（其引擎、其基因组
`policy`）或策略（紧急停止开关）可能已经变化。ClawEvolve 在
`singlebox/bot-runtime.ts` 中的 `active_engine='openclaw'` 和
`bot_type='personal'` 过滤条件变为检查 2。

## 4. 触发器

**触发器**（trigger）是使绑定创建运行的原因。每次触发都是一次普通的幂等运行提交
（[§6](#6-幂等提交与按-id-查询状态)），只是由平台而非调用方发起。

| 触发器 | JSON | 触发时机 | 幂等键 |
| --- | --- | --- | --- |
| 定时 | `{"schedule": "0 2 * * *"}` | cron 表达式匹配时（UTC） | `<binding_id>/<scheduled_fire_time>`，例如 `bind_01/2026-10-08T02:00:00Z` |
| 手动 | `{"manual": true}` | 从不自动触发；仅当调用方提交运行时 | 由调用方选择 |
| 事件 | `{"event": "failure_rate_alert"}` | 该 Bot 收到同名平台事件时 | `<binding_id>/<event_id>`（提议） |

默认进化策略中事件触发器的例子：失败率告警（`failure_rate_alert`）、ClawInsight
改进项（`clawinsight_improvement_item`，ClawEvolve 绑定的事件触发器），以及用于记忆
整合的“N 条新反馈”（`feedback_threshold_reached`）
（[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)）。这三个是提议的
`EventName` 取值（§2.2）。

规则：

- **一次触发可以安全重复。** 调度器可能对同一定时时段触发两次（例如在其自身
  重启之后）；键 `<binding_id>/<scheduled_fire_time>` 使第二次触发返回已有的
  运行。
- **任何绑定也都可以手动提交。** `{"manual": true}` 仅表示不会自动触发（提议）。
- **错过的触发会被跳过，而不是补跑**（提议）。如果某个时段经过时服务、Bot 或
  策略处于暂停状态，该时段被记录为已跳过，下一个时段正常触发。
- **每个绑定只有一个活跃运行**（提议）。如果触发器触发时该绑定的上一次运行仍处于
  `queued` 或 `running`，此次触发被记录为已跳过。在有活跃运行期间，同一绑定的
  手动提交会以 `409` 拒绝。见 [§17](#17-待定决策)。

快速循环路径（被进化的 Bot 请求对自身运行）随 Bot 调用方一起推迟（DR-3，
[decisions/0003](decisions/0003-bot-principal-for-evolution-surface.zh-CN.md)）。

## 5. 运行生命周期

```text
queued → running → completed | failed | cancelled | budget_exhausted
```

没有“等待”状态。正在等待判定或操作的策略处于 `running`，这段时间计入其挂钟
时间。

| 状态 | 含义 |
| --- | --- |
| `queued` | 已带冻结输入记录；等待 worker 认领（首次，或租约丢失之后） |
| `running` | 某个 worker 持有租约，`run(ctx)` 正在执行 |
| `completed` | `run(ctx)` 返回了 `RunSummary` |
| `failed` | 策略报告了不可重试的失败，或达到了 `max_attempts` |
| `cancelled` | 调用方、紧急停止开关或升级规则终止了它 |
| `budget_exhausted` | 某个预算维度耗尽 |

### 5.1 启动

提交时，本服务：

1. 再次执行绑定检查（[§3.2](#32-绑定检查)）和紧急停止开关检查
   （[§11.3](#113-紧急停止开关)）。
2. **冻结**策略版本、参数、父版本、允许的基因、验证配置和预算。父引用
   （`active`）此时被解析为修订版 id，因此运行期间的晋升不会改变运行的起点。
3. 记录该策略版本的智能体定义摘要（digest），使结果可以归因到实际使用的提示词。
4. 针对 Bot 级和租户级上限**预留**预算。
5. 将运行连同其第一个作业记录为 `queued`，并返回运行 id。

每次派发（认领）时，本服务构建恰好包含被授予能力的 `StrategyContext`：始终授予
的部分（`parent`、`workspace`、`operations`、`budget`、`log`、`artifacts`、
`cancelled`），始终授予的能力 `candidates@1` 和 `models@1`，以及策略 `needs`
中声明的能力。访问其他任何内容在进程内会抛出 `CapabilityNotGranted`，通过作业
协议则返回 `403`。上下文还携带 `run_id`、`params` 和 `attempt`。

### 5.2 运行期间

- 每一次模型调用、智能体会话和评估都计入预算（§11.1）。
- 策略提交的每个候选都在 `candidates.submit` 返回之前完成校验和记录（§5.4）。
- 平台保证没有任何隐藏内容到达策略：没有 holdout、回归或安全用例，验证结果只以
  聚合形式提供。这由上下文和作业协议返回的内容强制保证，而不是靠提示词。

### 5.3 结束

- **正常结束。** `run(ctx)` 返回 `RunSummary`（`POST
  …/jobs/{id}/complete`）；运行为 `completed`。
- **失败。** 策略抛出异常或调用 `POST …/jobs/{id}/fail`。可重试的失败会像租约
  丢失一样将运行重新排队（§7）；不可重试的失败，或最后一次允许的尝试，以
  `failed` 结束运行。
- **取消。** 调用方调用 `POST …/runs/{run}:cancel`，或紧急停止开关、升级规则
  触发。上下文的 `cancelled` 令牌被置位；策略应当迅速停止（一致性测试套件会
  测试这一点）。宽限期过后租约被吊销。运行以 `cancelled` 结束。
- **预算。** 任一预算维度耗尽时，下一次计费调用抛出 `BudgetExhausted`；运行以
  `budget_exhausted` 结束。

在所有情况下：

- 在失败、取消或预算停止之前做出的提交**会被保留并且仍会验证**。
- **未完成的操作会被取消**（§8）。
- 每一次提交，无论被接受还是被拒绝，都连同策略版本记录在实验记录 H 中
  （[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）。

### 5.4 运行提交的候选

`ctx.candidates.submit(candidate)`（作业协议 `POST
…/runs/{run}/candidates`）是一个短调用。在它返回之前，本服务：

1. 依据补丁 schema 校验补丁，检查其 `base` 是该 Bot 中从本运行可达的修订版，并
   检查每个 op 都在运行冻结的 `allowed_genes` 之内。违规返回 `422`。
2. 计算**候选 id**：补丁的内容哈希。重复提交同一补丁（包括重新派发后的提交）
   返回相同的 id，不会创建任何新内容。
3. 在 Genome Registry 中记录候选修订版，并将引用 `candidate/<run>/<n>` 指向它
   （[01-genome.zh-CN.md](01-genome.zh-CN.md)）。
4. 在验证产生任何花费之前，对已记录的候选调用晋升的静态底线检查（`FloorCheck`，
   通过 `PromotionService.check_floor`，[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。
   底线检查失败会使判定为 `reject`，不运行任何套件；结果存储在 `GateDecision`
   和实验记录中。
5. 将候选持久化到运行和实验记录中，然后返回 id。

如果底线检查通过，验证随后异步进行：本服务以运行冻结的验证配置调用
`verify(candidate, profile)`（[07-verification.zh-CN.md](07-verification.zh-CN.md)），并把
判定交给门禁（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。策略按候选 id 查询判定
（`ctx.candidates.verdict(id)`）；没有回调，也没有阻塞调用。它只能看到状态
（`pending | accept | reject | inconclusive`）和验证聚合结果，永远看不到逐用例的
隐藏数据。提交什么由策略自己选择；候选是否被接受由平台决定。

## 6. 幂等提交与按 id 查询状态

启动一次运行（触发器触发，或调用方通过 API）会返回一个**运行 id**。提交是幂等的，
并由平台保证：

- 提交方发送一个**幂等键**（idempotency key）：由提交方选择的字符串，对同一逻辑
  请求的每次重试都相同，对不同请求则不同。
- 平台存储 `(owner_id, bot_id, key) → run id`。使用相同键的重复提交返回相同的运行 id，不启动
  任何新内容，无论第一次运行现在处于什么状态。
- 此后运行 id 是唯一的句柄。调用方通过它查询状态、候选和判定。不需要回调通道；
  CLI 的 `--wait` 只是重复查询。

键的来源：

| 提交方 | 键 | 示例 |
| --- | --- | --- |
| 流水线 / CI | 在首次尝试前创建一次的 UUID，或一个确定性名称 | `nightly-bot_123-2026-10-08` |
| 平台触发器 | `<binding_id>/<scheduled_fire_time>` | `bind_01/2026-10-08T02:00:00Z` |
| 策略（操作、工作区） | `<run_id>/<own step>` | `run_7f3/round-2/tune` |

键中绝不能包含在重试之间会变化的内容，例如发送时的时间戳。以不同的请求体复用
同一个键会以 `409` 拒绝（提议；共享规则见
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）。

同样的模式也适用于更下一层：`ctx.candidates.submit` 返回候选 id（补丁的内容
哈希），判定按该 id 查询；操作启动接受幂等键并返回操作 id（§8）。

## 7. 租约、重新派发与崩溃恢复

运行必须在其进程死亡后存活：worker 崩溃、硬件故障或重启。工作在平台和策略之间
划分：

| 关注点 | 负责方 | 方式 |
| --- | --- | --- |
| 运行记录、其冻结输入、已花费预算和已提交候选 | 平台 | 在运行提交或 `candidates.submit` 返回之前持久化 |
| 发现运行的进程已死亡 | 平台 | 运行的每次尝试都是一个**租约作业**。worker（或进程内宿主）续期租约；租约到期时，运行回到 `queued`，并作为一个新作业、以相同的运行 id 和 `ctx.attempt + 1` 再次派发。fencing token 拒绝来自旧持有者的调用。达到 `max_attempts` 后运行以 `failed` 结束 |
| 已启动的智能体会话和训练评估 | 平台 | 它们是操作（§8）：由平台持久化并执行，独立于策略的进程。它们在重新派发期间继续运行；策略通过以相同幂等键重复启动来重新关联 |
| 策略自身的进度（轮次、搜索状态、历史） | 策略 | 策略把所需的一切持久化到**自己的存储**中，以运行 id 为键，并在重新派发时重新加载后继续。平台**没有检查点 API**，也从不读取这些状态；其结构因策略而异 |

### 7.1 租约

- worker 认领一个作业（`POST /evolution/v1/jobs:claim`），并收到运行的冻结
  输入、其 `attempt` 和一个新的 **fencing token**。
- worker 通过 `POST …/jobs/{id}/heartbeat` 续期租约。提议默认值：租约 60 秒，
  每 20 秒一次心跳。
- 进程内运行时以同样的方式从其宿主循环续期租约，因此两种运行时的恢复方式相同。
- 每个运行范围内的作业协议调用都携带 fencing token。携带过期 token 的调用返回
  `409`；旧持有者必须停止。

### 7.2 重新派发

租约到期时，本服务：

1. 递增 `attempt` 并将运行设回 `queued`（若仍有剩余尝试次数；否则以 `failed`
   结束，`end_reason: "max_attempts"`）。提议的默认 `max_attempts`：3。
2. 保持操作继续运行、工作区原样保留、候选保持已记录。
3. 在下一次认领时签发新的 fencing token，使旧 token 失效。

重新派发的策略按运行 id 重新加载自己的状态，以相同的键重新物化其工作区（拿回
相同的沙箱），以相同的幂等键重复操作启动（拿回相同的操作，无论是否已完成），
并重新提交任何它不确定的候选（拿回相同的候选 id）。一致性测试套件正是测试这一点：
在运行中途杀死策略并再次派发同一个运行 id，既不会产生重复的候选或操作，也不会
超出预算（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。

### 7.3 跨尝试的预算

重新派发的运行使用相同的冻结输入和相同的预算：早期尝试花掉的部分仍算已花费。
挂钟时间从运行首次开始时计算，包括尝试之间处于 `queued` 的时间（提议），因此
崩溃循环无法使运行超出 `max_wall_clock_s`。

## 8. 长时操作（平台侧）

有些能力调用所做的工作需要数分钟甚至更久：智能体会话（`agents.start`）或跨越
多次 rollout 的训练评估（`evaluate.start_train`）。在工作进行期间，它们从不保持
请求打开。策略如何使用它们见 [03-strategy.zh-CN.md](03-strategy.zh-CN.md)；本节说明平台
保证什么。

- **启动立即返回 id。** 启动调用记录一个操作，把工作交给平台，并返回其操作 id。
  通过作业协议时为带 id 的 `202`。
- **状态按 id 查询。** `ctx.operations.get(op_id)` 返回状态（`queued`、
  `running`、`succeeded`、`failed` 或 `cancelled`），成功后还返回结果。每次查询
  都是一个短请求。SDK 辅助函数 `ctx.operations.wait(op_id)` 重复进行短查询；它
  从不保持某个请求打开。
- **启动是幂等的。** 它接受一个幂等键。平台存储 `(run, key) → operation id`；
  以相同键重复启动会返回同一个操作（无论是否已完成），而不是重新启动并再次为该
  工作付费。
- **操作属于平台和运行。** 它们由平台持久化并执行，独立于策略的进程，因此策略
  崩溃不会使它们停止。其成本在产生时计入运行的预算。运行结束时，其未完成的操作
  会被取消。
- **工作区遵循同样的规则。** `ctx.workspace.materialise(revision,
  key)` 按键幂等，因此重新派发的运行会拿回同一个沙箱，包括智能体操作已经做出的
  编辑。

总是很快的调用保持为普通的请求与响应：读取经验、添加训练用例、
`candidates.submit`、`candidates.verdict`、`models.complete`（受 `max_tokens`
限制）以及预算调用。每个能力的目录契约会说明其哪些调用是操作；任何其工作可能比
一次短请求持续更久的调用都必须是操作。

**智能体操作。** `agents.start(definition, workspace=…, prompt=…,
idempotency_key=…, timeout_s=…)` 让引擎的 `agents@1` 提供方按摘要把指定的智能体
定义加载到工作区旁边的沙箱中。定义对智能体只读；只有工作区可写。策略未注册的
定义会被拒绝。操作成功时，其结果包含会话记录（transcript）和退出状态；智能体修改
的文件留在工作区中，直到策略把它们转成补丁。定义从何而来以及如何校验见
[03-strategy.zh-CN.md](03-strategy.zh-CN.md)。

**训练评估操作。** `evaluate.start_train(workspace,
idempotency_key=…)` 在**仅训练划分**上运行 Verification Service，给出分数和点评
（[07-verification.zh-CN.md](07-verification.zh-CN.md)）。验证、holdout、回归和安全划分保持
隐藏。

## 9. 运行时与作业协议

策略版本的注册记录会指明其运行时：

| `runtime.kind` | 运行方式 | `ctx` 如何到达它 |
| --- | --- | --- |
| `in_process` | 由 `apps/evolution` 组合根加载的 Python 包，通过配置选择（R5/R14） | 直接的 Python 对象 |
| `job_worker` | 容器镜像（任意语言） | **作业协议**（Job Protocol）：每个 `ctx` 调用对应一个 HTTP 端点 |

两种运行时看到相同的运行生命周期、租约、幂等性和预算规则；一致性测试套件针对策略
SDK 中提供的两种 `StrategyContext` 实现（进程内和作业协议）运行。

### 9.1 作业协议

作业协议是本服务与 job-worker 策略之间的**内部** Plugin API（线上形式）。其端点
位于 `/evolution/v1` 下，而不是公共的 `/openapi/v1` 下。每个端点都在
[§14.2](#142-内部-api作业协议) 中列出并附有示例。

| 端点 | `ctx` 调用 |
| --- | --- |
| `POST /evolution/v1/jobs:claim` | （worker 循环）认领作业：`{worker_id, strategy_ids[]}` → `{job_id, run_id, attempt, params, parent, budget, granted, fencing_token}` |
| `POST /evolution/v1/jobs/{id}/heartbeat` | 续期租约；租约过期会使运行作为新作业重新排队 |
| `GET /evolution/v1/runs/{run}/parent` | `ctx.parent` |
| `GET /evolution/v1/runs/{run}/content/{digest}` | 父版本 / 工作区的文件字节 |
| `GET /evolution/v1/runs/{run}/experience/sessions` | `ctx.experience.sessions`（若已授予） |
| `GET /evolution/v1/runs/{run}/experience/feedback` | `ctx.experience.feedback`（若已授予） |
| `POST /evolution/v1/runs/{run}/workspaces` | `ctx.workspace.materialise {revision, key} → {workspace_id}`（幂等） |
| `GET /evolution/v1/runs/{run}/workspaces/{workspace}/files` | 列出工作区的文件 → `[{path, digest}]`（文件字节通过 `content/{digest}` 获取） |
| `PUT /evolution/v1/runs/{run}/workspaces/{workspace}/files/{path}` | `ws.write(path, bytes) → {digest}` |
| `POST /evolution/v1/runs/{run}/workspaces/{workspace}:patch` | `ws.to_patch() → {patch}`（相对工作区 base 的基因组补丁） |
| `POST /evolution/v1/runs/{run}/log` | `ctx.log`：结构化日志行 |
| `PUT /evolution/v1/runs/{run}/artifacts/{name}` | `ctx.artifacts`：上传一个制品（字节） |
| `POST /evolution/v1/runs/{run}/agents:start` | `ctx.agents.start → 202 {operation_id}`（若已授予；幂等） |
| `POST /evolution/v1/runs/{run}/evaluations:train` | `ctx.evaluate.start_train → 202 {operation_id}`（若已授予；幂等） |
| `POST /evolution/v1/runs/{run}/evaluations/cases` | `ctx.evaluate.add_train_cases`（若已授予） |
| `GET /evolution/v1/runs/{run}/operations/{id}` | `ctx.operations.get → {status, result?}` |
| `POST /evolution/v1/runs/{run}/operations/{id}:cancel` | `ctx.operations.cancel` |
| `POST /evolution/v1/runs/{run}/models:complete` | `ctx.models.complete → completion` |
| `POST /evolution/v1/runs/{run}/candidates` | `ctx.candidates.submit → {candidate_id}`（幂等） |
| `GET /evolution/v1/runs/{run}/candidates/{id}` | `ctx.candidates.verdict → {status, aggregates}` |
| `POST /evolution/v1/runs/{run}/budget:charge` | `ctx.budget.charge` |
| `POST /evolution/v1/jobs/{id}/complete` | `run(ctx)` 返回了 `RunSummary` |
| `POST /evolution/v1/jobs/{id}/fail` | `run(ctx)` 失败：`{reason, retryable}` |

适用于每个端点的规则：

- **每个请求都迅速返回。** 可能比一次短请求持续更久的工作是操作：其启动返回带操作
  id 的 `202`，worker 按 id 查询其状态。在智能体会话或评估运行期间，不会保持任何
  请求打开。
- **JSON 与 JSON Schema。** 所有载荷都是 JSON（`content/{digest}` 的原始字节
  除外），每个都有已发布的 JSON Schema。
- **没有 Bot 凭证。** worker 不会获得任何访问 Bot 的凭证。
- **`403`**：调用未被授予能力的端点。
- **`409`**：携带过期 fencing token 的调用。
- **fencing token 的传递方式**（提议）：认领之后的每次调用都发送请求头
  `Evolution-Fencing-Token: <token>`。
- **取消通过心跳到达 worker**（提议）：心跳响应携带 `cancelled: true`，SDK 将其
  转换为 `ctx.cancelled`。

worker 是平台运行的容器。作为 worker 的 Bot（“runner bot”）随 Bot 调用方一起
推迟（DR-3，
[decisions/0003](decisions/0003-bot-principal-for-evolution-surface.zh-CN.md)）。

## 10. 沙箱

策略是基于不可信数据提议变更的不可信代码。无论策略是什么，本服务都是强制执行
隔离的地方。

- **只使用沙箱物化副本。** 策略和评估器只在沙箱物化副本上工作：来自
  `ctx.workspace.materialise` 的临时工作区，或 Verification Service 通过
  `eval_env` 使用的评估 Bot（[07-verification.zh-CN.md](07-verification.zh-CN.md)）。没有
  生产凭证，也无法访问线上表型（正在运行的 Bot）。策略从不修改线上 Bot，因此
  ClawEvolve 的 baseline-pack / restore / pack / deploy 步骤从流程中移除。
- **恰好是被授予的能力。** 每个策略声明它 `needs` 的目录能力及其运行时隔离
  （R13）；其上下文不授予任何其他东西。
- **没有出网、没有模型密钥、没有自己的智能体运行时。** 策略自身没有网络出口或
  模型密钥。模型调用经由 `ctx.models`，智能体会话经由 `ctx.agents`，二者都计入
  运行的预算并被记录。这是有意为之的限制：策略只能使用能力目录所提供的东西。其
  自身的计算（解析、搜索、排序）不受限制。新的需求在出现第二个需要它的策略时通过
  新增目录条目来满足，而不是为某一个策略开例外。
- **智能体定义只读。** 智能体操作的定义按摘要加载到工作区旁边，并且只读；只有
  工作区可写。
- **隐藏数据保持隐藏。** holdout、回归和安全用例永远不会到达策略；验证只以聚合
  形式暴露。由上下文和作业协议强制保证，而不是靠提示词。
- **经验是不可信输入。** 片段文本是提示词注入和记忆投毒的载体。策略必须把它当作
  数据处理。由其派生的补丁在可以晋升之前会被扫描密钥、PII 和新 URL
  （[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。被进化 Bot 向收件箱提交的速率限制随
  Bot 调用方一起推迟。
- **job-worker 隔离**（提议）：job-worker 容器运行时除通往作业协议端点外没有任何
  网络路由，不挂载任何密钥，根文件系统只读（其临时目录除外）。策略自身的进度存储
  是它唯一被允许访问的存储；如何访问尚未确定（ER-10，[§17](#17-待定决策)）。

## 11. 预算与紧急停止开关

预算由本服务强制执行，而不是由策略。

### 11.1 单次运行预算

- **维度：** 美元、挂钟时间、评估 rollout 次数和 token（`Budget`，§2.1）。用量在
  运行上报告（`budget_used`）。
- **计量：** 平台对其服务的每次调用计费：`models.complete`（token 和美元，并记录
  所用模型）、智能体操作和训练评估（rollout 次数和美元）。`ctx.budget.charge`
  记录策略报告的一笔显式费用（提议语义：经由平台产生、但平台自身无法计量的成本；
  只能增加花费，永不退还）。
- **预留：** 提交时针对 Bot 级和租户级上限预留全部预算，结束时释放（未花费部分）。
- **耗尽：** 某个维度耗尽时，下一次计费调用以 `BudgetExhausted` 失败（作业协议：
  `402`，`error.code:
  "budget_exhausted"`，提议），未完成的操作被取消，运行以 `budget_exhausted`
  结束。已提交候选的验证不计入该运行，并且仍会进行。
- **等待也计入。** 策略查询判定或操作所花的时间计入 `max_wall_clock_s`。

### 11.2 Bot 级与租户级上限

- 按 Bot 和按租户的每日、每月花费上限。预算无法放入剩余上限的运行在提交时被拒绝
  （`409`，`error.code: "ceiling_exceeded"`，提议），触发器的触发则记录为已跳过。
- 每个 Bot 每天的最大晋升次数由晋升强制执行（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。
- **升级：** 一次运行中连续 N 个候选被拒绝（提议默认 N = 5）会停止该运行并通知
  所有者。运行以 `cancelled` 结束，`end_reason: "consecutive_rejections"`（提议）。

上限在哪里配置尚未确定（[§17](#17-待定决策)）；提议在进化策略配置上增加 `limits`
字段用于 Bot 级上限，在租户设置中配置租户级上限。

### 11.3 紧急停止开关

| 开关 | 效果 | 谁 |
| --- | --- | --- |
| **按策略**（全局禁用） | 该策略版本（或某策略的所有版本）不能再被绑定、提交或认领；其正在进行的运行被取消 | 平台运维人员；记录在 Strategy Registry 的注册上（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)），在此强制执行 |
| **按 Bot**（冻结进化，保留 `active`） | 该 Bot 的触发器停止触发，提交被拒绝（`409`，`error.code: "evolution_frozen"`），正在进行的运行被取消。Bot 继续运行其 `active` 修订版 | Bot 所有者、租户管理员 |
| **全局**（暂停编排器） | 不服务任何认领，不触发任何触发器，不提交任何运行；正在进行的运行保留其记录，并在暂停结束后（通过重新派发）恢复 | 平台运维人员 |

冻结或暂停状态从不移动任何基因组引用。已提交候选的待定判定仍会完成；它们的晋升
遵循门禁（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。

## 12. 存储

提议位于 `apps/evolution` 中（singlebox 本地配置中使用 SQLite，其他环境使用
服务端数据库）：

| 表 | 键 | 内容 |
| --- | --- | --- |
| `evolution_policy` | `(owner_id, bot_id)` | 绑定文档及其 ETag |
| `evolution_run` | `run_id` | `Run` 记录：冻结输入、状态、尝试次数、用量 |
| `evolution_run_key` | `(owner_id, bot_id, idempotency_key)` | `run_id`；使提交幂等 |
| `evolution_job` | `job_id` | 租约持有者、到期时间、fencing token、尝试次数 |
| `evolution_operation` | `operation_id`，唯一 `(run_id, idempotency_key)` | `Operation` 记录 |
| `evolution_workspace` | `workspace_id`，唯一 `(run_id, key)` | `Workspace` 记录；沙箱文件存放在执行器中 |
| `evolution_run_candidate` | `(run_id, candidate_id)` | 序号 `n`（用于 `candidate/<run>/<n>`）、判定状态缓存 |
| `evolution_budget_charge` | 仅追加 | 每一笔费用：运行、来源（模型调用、操作、显式）、金额 |
| `evolution_trigger_firing` | `(binding_id, fire_key)` | 已触发 / 已跳过的触发及原因 |

响应所依赖的每一次写入（运行提交、候选提交、操作启动、工作区物化）都在响应返回
之前提交；写入失败返回错误，绝不静默地返回成功。

## 13. 服务接口

下面的 Python Protocol 是平台其他部分（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)
中的 API 层、触发器调度器、作业协议适配器、进程内宿主）所调用的接口。它们与传输
无关：§14 中的 HTTP 端点是基于它们的交付适配器。

```python
class EvolutionPolicyService(Protocol):
    async def get_policy(self, bot: BotRef) -> EvolutionPolicy:
        """Return the bot's policy; an empty bindings list if none was written."""

    async def put_policy(self, bot: BotRef, bindings: list[Binding], *,
                         expected_etag: str | None, actor: str) -> EvolutionPolicy:
        """Replace the bindings after running the binding checks (§3.2) on every
        new or changed binding. expected_etag None means "create; fail if one exists".
        Raises BindingCheckFailed (all violations listed) or PreconditionFailed."""


class RunService(Protocol):
    async def submit(self, bot: BotRef, binding_id: str, *, idempotency_key: str,
                     trigger: TriggerRecord, params: dict | None = None,
                     budget: Budget | None = None) -> str:
        """Create a run of the binding and return its run id, or return the existing
        run id for (owner_id, bot_id, idempotency_key). params are merged over the binding's params;
        budget, when given, must not exceed the binding's budget (None: use the binding's).
        Freezes inputs, reserves the budget, records the run as queued.
        Raises BindingCheckFailed, EvolutionFrozen, CeilingExceeded, BindingBusy,
        IdempotencyKeyReused."""

    async def get(self, bot: BotRef, run_id: str) -> Run: ...

    async def list(self, bot: BotRef, *, status: list[RunStatus] | None = None,
                   binding_id: str | None = None, page: int = 1,
                   page_size: int = 20) -> Page[Run]: ...

    async def cancel(self, bot: BotRef, run_id: str, *, reason: str, actor: str) -> Run:
        """Set the run's cancellation token; cancel its unfinished operations;
        end it as cancelled. Idempotent on an already cancelled run; raises
        RunAlreadyEnded for completed/failed/budget_exhausted runs."""

    async def candidates(self, bot: BotRef, run_id: str) -> list[RunCandidate]:
        """Candidates the run submitted, in order, with verdict status."""


class JobService(Protocol):
    """The dispatch side, used by the Job Protocol adapter and the in-process host."""

    async def claim(self, worker_id: str, strategy_ids: list[str]) -> ClaimedJob | None:
        """Lease one queued job of the given strategies; None if there is none.
        Increments nothing: attempt was set when the run was (re-)queued."""

    async def heartbeat(self, job_id: str, fencing_token: str) -> LeaseState:
        """Renew the lease; returns new expiry and whether the run was cancelled.
        Raises StaleFencingToken."""

    async def complete(self, job_id: str, fencing_token: str, summary: RunSummary) -> None: ...

    async def fail(self, job_id: str, fencing_token: str, *, reason: str,
                   retryable: bool) -> None:
        """Retryable with attempts left: re-queue (attempt + 1). Otherwise end as failed."""

    async def expire_leases(self, now: datetime) -> list[str]:
        """Called by the service's own timer: for every job whose lease has expired,
        re-queue its run (a new job, attempt + 1) or fail the run. Returns the affected run ids."""


class OperationService(Protocol):
    async def start(self, run_id: str, kind: Literal["agent_session", "train_evaluation"], *,
                    idempotency_key: str, request: dict) -> str:
        """Record the operation and hand it to its executor; return the operation id.
        Returns the existing id for (run_id, idempotency_key)."""

    async def get(self, run_id: str, operation_id: str) -> Operation: ...

    async def cancel(self, run_id: str, operation_id: str) -> Operation: ...

    async def cancel_unfinished(self, run_id: str) -> int:
        """Called when a run ends. Returns how many were cancelled."""


class BudgetService(Protocol):
    async def reserve(self, bot: BotRef, tenant: str, budget: Budget) -> None:
        """Raises CeilingExceeded."""

    async def charge(self, run_id: str, usage: BudgetUsage, *,
                     source: Literal["model_call", "operation", "explicit"]) -> BudgetUsage:
        """Add to the run's spend and return what remains. Raises BudgetExhausted
        once any dimension is used up (the run is then ended by the caller)."""

    async def remaining(self, run_id: str) -> BudgetUsage: ...


class KillSwitches(Protocol):
    async def is_strategy_disabled(self, strategy: str, version: str) -> bool: ...
    async def is_bot_frozen(self, bot: BotRef) -> bool: ...
    async def is_paused(self) -> bool: ...
```

本服务依赖的端口（由各自的负责文档定义）：

| 端口 | 用途 | 负责方 |
| --- | --- | --- |
| Genome Registry（从 `{base, patch}` 创建修订版、移动 `candidate/<run>/<n>`、解析 `active`、读取内容） | 冻结父版本；记录候选；物化工作区 | [01-genome.zh-CN.md](01-genome.zh-CN.md) |
| 经验查询 | 提供 `experience.sessions` / `experience.feedback` | [02-experience.zh-CN.md](02-experience.zh-CN.md) |
| Strategy Registry（注册记录、一致性状态、智能体定义摘要、各引擎的能力提供方） | 绑定检查；构建上下文；加载智能体定义 | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| 实验记录追加 | 记录运行、提交、判定、成本、所用模型 | [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) |
| `verify(candidate, profile)`、训练评估 | 判定；`evaluate.train` 操作 | [07-verification.zh-CN.md](07-verification.zh-CN.md) |
| 静态底线（`FloorCheck`，经由 `PromotionService.check_floor`） | 在提交时、验证之前拒绝候选 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 门禁接入 | 把判定交给门禁和评审队列 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |

## 14. API

### 14.1 公共 API

公共端点位于前缀 `/openapi/v1` 下；下文路径相对于该前缀。它们遵循
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) 中的共享约定（错误信封、可变资源上的
ETag、创建类 POST 上的 `Idempotency-Key` 请求头）。调用方是流水线和 CI、UI
后端，以及通过 `avn` CLI 操作的人。调用方的认证与授权不在本设计文档集中规定；
Bot 调用方被推迟（DR-3）。下文响应展示的是标准信封中的 `data` 载荷；信封、错误、
分页和幂等性见 [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。

### GET /bots/{bot_id}/evolution/policy

读取 Bot 的进化策略配置（其绑定）。由 UI 后端、`avn evolve policy get` 和流水线
调用。

请求：

```http
GET /openapi/v1/bots/bot_123/evolution/policy
```

响应 `200`（请求头 `ETag: "pol-v12"`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "bindings": [
    {
      "id": "bind_01",
      "strategy": "clawevolve/bot-evolution@2.0.0",
      "trigger": {"schedule": "0 2 * * *"},
      "parent": "active",
      "allowed_genes": ["persona", "skills"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
      "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60}
    }
  ],
  "etag": "\"pol-v12\"",
  "updated_at": "2026-10-06T08:00:00Z",
  "updated_by": "user_owner_5"
}
```

错误：`404` 未知 Bot。

### PUT /bots/{bot_id}/evolution/policy

替换 Bot 的绑定。对每个新增或修改的绑定执行绑定检查（§3.2），任一检查失败则拒绝
整个文档。由 Bot 所有者或租户管理员通过 UI 或 `avn evolve policy set` 调用。

请求（请求头 `If-Match: "pol-v12"`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bindings": [
    {
      "id": "bind_01",
      "strategy": "clawevolve/bot-evolution@2.0.0",
      "trigger": {"schedule": "0 2 * * *"},
      "parent": "active",
      "allowed_genes": ["persona", "skills"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
      "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60}
    },
    {
      "id": "bind_02",
      "strategy": "platform/consolidate-memory@1.0.0",
      "trigger": {"schedule": "0 4 * * 0"},
      "parent": "active",
      "allowed_genes": ["memory"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 5, "max_wall_clock_s": 1800},
      "params": {}
    }
  ]
}
```

响应 `200`（请求头 `ETag: "pol-v13"`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "bindings": [
    {"id": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0", "trigger": {"schedule": "0 2 * * *"},
     "parent": "active", "allowed_genes": ["persona", "skills"], "verification_profile": "default@1",
     "budget": {"max_usd": 20, "max_wall_clock_s": 7200}, "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60}},
    {"id": "bind_02", "strategy": "platform/consolidate-memory@1.0.0", "trigger": {"schedule": "0 4 * * 0"},
     "parent": "active", "allowed_genes": ["memory"], "verification_profile": "default@1",
     "budget": {"max_usd": 5, "max_wall_clock_s": 1800}, "params": {}}
  ],
  "etag": "\"pol-v13\"",
  "updated_at": "2026-10-08T09:30:00Z",
  "updated_by": "user_owner_5"
}
```

错误示例 `422`（绑定检查失败）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "error": {
    "code": "binding_check_failed",
    "message": "binding bind_01 is not valid for bot_123",
    "details": [
      {"binding": "bind_01", "check": "allowed_genes", "detail": "gene 'tools.mcp' is locked by the bot's genome policy"},
      {"binding": "bind_01", "check": "engine", "detail": "agents@1 definitions target engine 'openclaw'; bot engine is 'hermes'"}
    ]
  }
}
```

其他错误：`412` ETag 不匹配（其他人修改了策略配置）；`404` 未知 Bot 或未知策略
版本。

### POST /bots/{bot_id}/evolution/runs

提交 Bot 某个绑定的一次运行。请求体为 `{binding, params?, budget?}`。幂等：
`Idempotency-Key` 请求头为必填；以相同键重复提交返回相同的运行 id，且不启动任何
东西。由流水线和 CI、UI 后端以及 `avn evolve run start` 调用。触发器在内部使用
同一个服务操作（§4）。

请求（请求头 `Idempotency-Key: nightly-bot_123-2026-10-08`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "binding": "bind_01",
  "params": {"max_rounds": 2},                 // optional; merged over the binding's params
  "budget": {"max_usd": 10, "max_wall_clock_s": 3600}   // optional; must not exceed the binding's budget
}
```

响应 `202`（以相同键重复提交时也是如此）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "queued"}
```

错误：`404` 未知绑定；`409`，`error.code` 为以下之一：`evolution_frozen`、
`strategy_disabled`、`binding_busy`（该绑定的某次运行仍处于活跃状态）、
`ceiling_exceeded`、`idempotency_key_reused`（相同的键，不同的请求体）；`422`
`binding_check_failed`（自写入策略配置以来 Bot 或策略已变化），或 `budget` 超出
绑定的预算。

### GET /bots/{bot_id}/evolution/runs

列出 Bot 的运行，最新的在前。过滤条件：`status`、`binding`、`since`；以及
`page` 和 `page_size`。由 UI 后端和 `avn evolve run status` 调用。

请求：

```http
GET /openapi/v1/bots/bot_123/evolution/runs?status=running,queued&binding=bind_01&page=1&page_size=20
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {"id": "run_7f3", "binding_id": "bind_01", "strategy": "clawevolve/bot-evolution",
     "strategy_version": "2.0.0", "status": "running", "attempt": 2,
     "created_at": "2026-10-08T02:00:03Z",
     "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
     "budget_used": {"usd": 6.42, "wall_clock_s": 1830, "rollouts": 96, "tokens": 412300},
     "candidate_count": 1}
  ]
}
```

错误：`404` 未知 Bot；`422` 未知的过滤值。

### GET /bots/{bot_id}/evolution/runs/{run}

按 id 读取一次运行：状态、冻结输入、尝试次数、已用预算、摘要。这就是每个调用方
反复执行直到状态为终态的状态查询。由流水线、UI 后端和 `avn evolve run status
--wait` 调用。

请求：

```http
GET /openapi/v1/bots/bot_123/evolution/runs/run_7f3
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "run_7f3",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "binding_id": "bind_01",
  "idempotency_key": "bind_01/2026-10-08T02:00:00Z",
  "trigger": {"kind": "schedule", "fire_time": "2026-10-08T02:00:00Z", "event_id": null, "requested_by": null},
  "strategy": "clawevolve/bot-evolution",
  "strategy_version": "2.0.0",
  "parent_ref": "active",
  "parent_revision": "sha256:a90b…",
  "allowed_genes": ["persona", "skills"],
  "verification_profile": "default@1",
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60},
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "granted": ["parent", "workspace", "operations", "budget", "log", "artifacts", "cancelled",
              "candidates@1", "models@1", "experience.sessions@1", "agents@1", "evaluate.train@1"],
  "agent_definitions": {"clawevolve-tune": "sha256:5d0e…", "clawevolve-review": "sha256:8b17…"},
  "status": "completed",
  "attempt": 2,
  "max_attempts": 3,
  "budget_used": {"usd": 14.80, "wall_clock_s": 5210, "rollouts": 288, "tokens": 1203400},
  "candidate_count": 2,
  "created_at": "2026-10-08T02:00:03Z",
  "started_at": "2026-10-08T02:00:09Z",
  "ended_at": "2026-10-08T03:26:59Z",
  "end_reason": null,
  "summary": {"rounds": 3, "notes": "two candidates submitted; round 2 accepted", "extra": {"findings": 5}}
}
```

错误：`404` 未知运行，或属于另一个 Bot 的运行。

### POST /bots/{bot_id}/evolution/runs/{run}:cancel

取消一次运行。置位运行的取消令牌，取消其未完成的操作，并以 `cancelled` 结束运行。
已提交的候选会被保留并且仍会验证。对已取消的运行是幂等的。由 Bot 所有者（UI、
`avn evolve run cancel`）和流水线调用。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "persona freeze during product launch"}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"id": "run_7f3", "status": "cancelled", "end_reason": "cancelled_by_owner",
 "ended_at": "2026-10-08T02:40:12Z", "candidate_count": 1}
```

错误：`404` 未知运行；对于处于 `completed`、`failed` 或 `budget_exhausted` 的
运行返回 `409` `run_already_ended`。

### GET /bots/{bot_id}/evolution/runs/{run}/candidates

按提交顺序列出一次运行提交的候选及其判定状态。单个候选的完整报告（diff、验证、
门禁决定）是 [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中的
`GET /bots/{bot_id}/evolution/candidates/{candidate}`。由 UI 后端、流水线和
`avn evolve run report` 调用。

请求：

```http
GET /openapi/v1/bots/bot_123/evolution/runs/run_7f3/candidates
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "items": [
    {"candidate_id": "sha256:c41e…", "n": 1, "ref": "candidate/run_7f3/1",
     "revision": "sha256:7c1e…",                    // r42, parent r41; not the candidate id (hash of the patch)
     "base": "sha256:a90b…",
     "submitted_at": "2026-10-08T02:31:55Z",
     "rationale": "Fixes trigger misses on partial refunds",
     "verdict": {"status": "accept"}},
    {"candidate_id": "sha256:e07d…", "n": 2, "ref": "candidate/run_7f3/2",
     "revision": "sha256:9d40…",                    // r43, parent r42
     "base": "sha256:7c1e…",
     "submitted_at": "2026-10-08T03:10:20Z",
     "rationale": "Tightens escalation wording",
     "verdict": {"status": "reject"}}
  ]
}
```

错误：`404` 未知运行。

### 14.2 内部 API（作业协议）

供 job-worker 策略使用的内部端点，位于 `/evolution/v1` 下（而非
`/openapi/v1`）。它们不属于公共 API，也不经由网关暴露。认领之后，每次调用都发送
请求头 `Evolution-Fencing-Token`（提议名称）。通用错误：`403` 能力未授予；`409`
fencing token 过期；计费调用上的 `402` `budget_exhausted`（提议）；`404` 未知的
运行、作业、操作或本运行的候选。

### POST /evolution/v1/jobs:claim

租约一个属于给定策略 id 的已排队作业。由 worker 的主循环调用。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"worker_id": "worker-clawevolve-02", "strategy_ids": ["clawevolve/bot-evolution"]}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "job_id": "job_7f3_2",
  "run_id": "run_7f3",
  "attempt": 2,
  "strategy": "clawevolve/bot-evolution@2.0.0",
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60},
  "parent": "sha256:a90b…",
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "budget_used": {"usd": 3.10, "wall_clock_s": 912, "rollouts": 48, "tokens": 201000},
  "granted": ["parent", "workspace", "operations", "budget", "log", "artifacts", "cancelled",
              "candidates@1", "models@1", "experience.sessions@1", "agents@1", "evaluate.train@1"],
  "fencing_token": "ft_7f3_2_b81c",
  "lease_expires_at": "2026-10-08T02:16:10Z"
}
```

该作业之后的每次调用都在 `Evolution-Fencing-Token` 请求头中发送
`fencing_token`。

当这些策略没有已排队的作业时，响应 `204`（无响应体）。错误：全局紧急停止开关
开启期间返回 `409` `orchestrator_paused`（提议；worker 将其视同 `204`，稍后
重试）。

### POST /evolution/v1/jobs/{id}/heartbeat

续期租约。租约过期会使运行作为新作业重新排队（§7）。响应还会告诉 worker 运行是否已被取消
（提议）。

请求（请求头 `Evolution-Fencing-Token: ft_7f3_2_b81c`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"lease_expires_at": "2026-10-08T02:17:10Z", "cancelled": false}
```

错误：`409` fencing token 过期（租约已丢失；worker 必须停止此运行）。

### GET /evolution/v1/runs/{run}/parent

`ctx.parent`：运行的冻结父修订版，只读：spec、按摘要的文件、谱系。

请求：

```http
GET /evolution/v1/runs/run_7f3/parent
Evolution-Fencing-Token: ft_7f3_2_b81c
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "sha256:a90b…",
  "seq": 41,
  "parents": ["sha256:77f2…"],
  "spec": {
    "persona": {"files": [{"path": "persona/SOUL.md", "digest": "sha256:1a2b…"}]},
    "skills": [{"name": "refund-policy", "files": [{"path": "skills/refund-policy/SKILL.md", "digest": "sha256:3c4d…"}]}],
    "memory": {"items": []}
  },
  "policy": {"locked_genes": ["script", "tools.mcp", "policy"], "pins": []}
}
```

基因组的结构本身定义于 [01-genome.zh-CN.md](01-genome.zh-CN.md)。

### GET /evolution/v1/runs/{run}/content/{digest}

按内容摘要获取父版本或工作区的文件字节。返回原始字节，而不是 JSON。

请求：

```http
GET /evolution/v1/runs/run_7f3/content/sha256:1a2b…
Evolution-Fencing-Token: ft_7f3_2_b81c
```

响应 `200`：

```http
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8
Digest: sha256:1a2b…

# Soul
You are the support assistant for …
```

错误：`404` 摘要无法从本运行的父版本或工作区到达。

### GET /evolution/v1/runs/{run}/experience/sessions

`ctx.experience.sessions`（仅当授予了 `experience.sessions@1` 时）：Bot 过去的
对话，以规范化、已脱敏的片段形式提供。查询参数：`days`、`limit`（默认 500）、
`revision`。片段格式定义于 [02-experience.zh-CN.md](02-experience.zh-CN.md)。

请求：

```http
GET /evolution/v1/runs/run_7f3/experience/sessions?days=7&limit=2
Evolution-Fencing-Token: ft_7f3_2_b81c
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "episodes": [
    {
      "episode_id": "ep_91",
      "revision_id": "sha256:a90b…",
      "started_at": "2026-10-07T09:12:00Z",
      "turns": [
        {"role": "user", "text": "Can I get a refund for half of my order?"},
        {"role": "assistant", "text": "…", "tool_calls": [{"name": "order_lookup", "args": {"id": "A17"}}]},
        {"role": "tool", "name": "order_lookup", "result": "…"}
      ],
      "outcome": {"status": "user_corrected", "feedback": "partial refunds are allowed"},
      "redactions": ["email", "phone"]
    }
  ]
}
```

错误：`403` `capability_not_granted`。

### GET /evolution/v1/runs/{run}/experience/feedback

`ctx.experience.feedback`（仅当授予了 `experience.feedback@1` 时）：Bot 的评分、
纠正、结果和发现。查询参数：`days`、`limit`。反馈格式定义于
[02-experience.zh-CN.md](02-experience.zh-CN.md)。

请求：

```http
GET /evolution/v1/runs/run_2c8/experience/feedback?days=7&limit=100
Evolution-Fencing-Token: ft_2c8_1_04aa
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "feedback": [
    {"feedback_id": "fb_301", "episode_id": "ep_91", "revision_id": "sha256:a90b…",
     "kind": "correction", "text": "partial refunds are allowed within 30 days",
     "created_at": "2026-10-07T09:20:00Z"},
    {"feedback_id": "fb_302", "episode_id": "ep_95", "revision_id": "sha256:a90b…",
     "kind": "rating", "score": 0.25, "created_at": "2026-10-07T14:02:00Z"}
  ]
}
```

错误：`403` `capability_not_granted`。

### POST /evolution/v1/runs/{run}/workspaces

`ctx.workspace.materialise(revision, key)`：把一个修订版物化为沙箱工作区。按
`(run, key)` 幂等：重复调用返回同一个工作区，包括其中已做出的编辑。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:a90b…", "key": "run_7f3/round-1"}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_7f3_r1", "revision": "sha256:a90b…", "key": "run_7f3/round-1", "created": false}
```

`created` 在某个键的首次调用时为 `true`，重复调用时为 `false`（提议）。错误：
`422` 修订版无法从本运行到达（既不是父版本，也不是本运行的候选）；`409` 同一个键
被用于不同的修订版。

### GET /evolution/v1/runs/{run}/workspaces/{workspace}/files

列出工作区的文件及其内容摘要（与 `content/{digest}` 一起支撑 `ws.read`，并让
worker 看到智能体修改了什么）。

请求：

```http
GET /evolution/v1/runs/run_7f3/workspaces/ws_7f3_r1/files
Evolution-Fencing-Token: ft_7f3_2_b81c
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "files": [
    {"path": "persona/SOUL.md", "digest": "sha256:1a2b…"},
    {"path": "skills/refund-policy/SKILL.md", "digest": "sha256:5e6f…"}
  ]
}
```

错误：`404` 本运行中不存在该工作区。

### PUT /evolution/v1/runs/{run}/workspaces/{workspace}/files/{path}

`ws.write(path, content)`：写入沙箱副本中的一个文件（请求体为原始字节）。`{path}`
作为单个路径段进行百分号编码。只有工作区发生变化；不会触及线上 Bot。

请求：

```http
PUT /evolution/v1/runs/run_7f3/workspaces/ws_7f3_r1/files/skills%2Frefund-policy%2FSKILL.md
Evolution-Fencing-Token: ft_7f3_2_b81c
Content-Type: application/octet-stream

---
name: refund-policy
---
Use for full and partial refunds of paid orders.
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"digest": "sha256:7a8b…"}
```

错误：`404` 未知工作区；`413` 超出 Manifest 文件大小限制。

### POST /evolution/v1/runs/{run}/workspaces/{workspace}:patch

`ws.to_patch()`：工作区相对于其物化来源修订版的逐项基因组补丁。这是一个快速调用；
它不提交任何东西（提交是 `POST …/candidates`）。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch": {
    "patch_schema": 1,
    "base": "sha256:a90b…",
    "ops": [{"op": "skill.update", "name": "refund-policy",
             "file_ops": [{"kind": "unified_diff", "path": "SKILL.md", "diff": "@@ -3,1 +3,1 @@ …"}]}],
    "rationale": "",
    "evidence": []
  }
}
```

策略在提交前填写 `rationale` 和 `evidence`。错误：`404` 未知工作区。

### POST /evolution/v1/runs/{run}/log

`ctx.log`：向运行日志追加结构化日志行。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"lines": [{"at": "2026-10-08T02:23:05Z", "level": "info", "msg": "train", "fields": {"score": 0.82}}]}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"accepted": 1}
```

### PUT /evolution/v1/runs/{run}/artifacts/{name}

`ctx.artifacts`：上传运行的一个制品（原始字节），例如策略希望评审者看到的报告。
再次写入同一名称会替换它。

请求：

```http
PUT /evolution/v1/runs/run_7f3/artifacts/round-1-report.md
Evolution-Fencing-Token: ft_7f3_2_b81c
Content-Type: text/markdown

# Round 1
Train 0.82 vs parent 0.61 …
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"artifact_id": "art_7f3_r1", "digest": "sha256:c3d4…"}
```

错误：`413` 超出制品大小限制（提议：Manifest 文件大小限制）。

### POST /evolution/v1/runs/{run}/agents:start

`ctx.agents.start`（仅当授予了 `agents@1` 时）：在一个工作区中以操作的形式启动
策略已注册的某个智能体定义。按 `idempotency_key` 幂等。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "definition": "clawevolve-tune",
  "workspace_id": "ws_7f3_r1",
  "prompt": "Findings: partial-refund trigger misses (5 episodes). Edit the refund-policy skill …",
  "idempotency_key": "run_7f3/round-1/tune",
  "timeout_s": 1800
}
```

响应 `202`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_19a", "status": "queued"}
```

错误：`403` `capability_not_granted`；`422` `unknown_definition`（未在此策略版本
中注册）或未知工作区；`402` `budget_exhausted`。

### POST /evolution/v1/runs/{run}/evaluations:train

`ctx.evaluate.start_train`（仅当授予了 `evaluate.train@1` 时）：以操作的形式在
**仅训练划分**上评估一个工作区，给出分数和点评。按 `idempotency_key` 幂等。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_7f3_r1", "idempotency_key": "run_7f3/round-1/train", "seeds": 3}
```

响应 `202`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_1b2", "status": "queued"}
```

错误：`403` `capability_not_granted`；`422` 未知工作区；`402`
`budget_exhausted`。

### POST /evolution/v1/runs/{run}/evaluations/cases

`ctx.evaluate.add_train_cases`（仅当授予了 `evaluate.train@1` 时）：添加策略从
经验中派生的可回放用例。划分由平台分配；策略无法选择。用例格式定义于
[07-verification.zh-CN.md](07-verification.zh-CN.md)。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "cases": [
    {"title": "Partial refund request",
     "prompt": "Can I get a refund for half of my order A17?",
     "expected": "Explains that partial refunds are allowed within 30 days and starts one",
     "evidence": ["episode:ep_91"]}
  ]
}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"accepted": [{"case_id": "case_5e1", "split": "train"}]}
```

部分用例可能被分配到隐藏划分；这些用例只以计数形式报告（提议：
`{"accepted": […], "withheld": 1}`），绝不报告 id。错误：`403`
`capability_not_granted`；`422` 用例校验失败。

### GET /evolution/v1/runs/{run}/operations/{id}

`ctx.operations.get`：按 id 查询操作的状态；成功后还返回其结果。

请求：

```http
GET /evolution/v1/runs/run_7f3/operations/op_1b2
Evolution-Fencing-Token: ft_7f3_2_b81c
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "op_1b2",
  "kind": "train_evaluation",
  "status": "succeeded",
  "cost": {"usd": 2.05, "wall_clock_s": 410, "rollouts": 48, "tokens": 0},
  "result": {
    "split": "train",
    "score": 0.82,
    "cases": [
      {"case_id": "case_5e1", "score": 1.0, "critique": "Correctly allowed the partial refund."},
      {"case_id": "case_5e2", "score": 0.5, "critique": "Did not confirm the order id before refunding."}
    ]
  },
  "error": null
}
```

运行期间，同一调用返回 `"status": "running"` 和 `"result": null`。

### POST /evolution/v1/runs/{run}/operations/{id}:cancel

`ctx.operations.cancel`：取消本运行的一个操作。幂等；已完成的操作原样返回。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"id": "op_19a", "status": "cancelled"}
```

### POST /evolution/v1/runs/{run}/models:complete

`ctx.models.complete`（始终授予）：一次模型调用，输入提示词，输出文本，经由平台
路由、计入预算并记录所用模型。`model` 是平台模型列表中的名称；省略时使用平台
默认模型。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "messages": [
    {"role": "system", "content": "You rewrite skill descriptions to be precise."},
    {"role": "user", "content": "Rewrite: 'Handles refunds.'"}
  ],
  "max_tokens": 512
}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "text": "Use when a user asks for a full or partial refund of an order …",
  "model": "platform-default",
  "usage": {"input_tokens": 38, "output_tokens": 41},
  "cost_usd": 0.0009
}
```

错误：`402` `budget_exhausted`；`422` 未知模型名，或 `max_tokens` 超出平台限制。

### POST /evolution/v1/runs/{run}/candidates

`ctx.candidates.submit`（始终授予）：提交一个候选（相对某个 base 修订版的基因组
补丁、理由、证据 id、可选的自报指标）。立即返回候选 id；不等待验证。幂等：id 是
补丁的内容哈希。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch": {"patch_schema": 1, "base": "sha256:a90b…",
            "ops": [{"op": "file.edit", "target": "skills/refund-policy/SKILL.md",
                     "edits": [{"kind": "replace_section", "heading": "## When to use", "content": "…"}]}]},
  "rationale": "Best on 14/20 train cases; fixes trigger misses on partial refunds",
  "evidence": ["episode:ep_91", "eval:train_77"],
  "self_metrics": {"train_score_pct": 82}
}
```

响应 `200`（重复提交时响应体相同）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"candidate_id": "sha256:c41e…", "ref": "candidate/run_7f3/1", "verdict": {"status": "pending"}}
```

错误：`422` `patch_invalid`（schema）、`base_mismatch` 或 `gene_not_allowed`（op
超出运行的 `allowed_genes`、涉及锁定基因或固定项）。

### GET /evolution/v1/runs/{run}/candidates/{id}

`ctx.candidates.verdict`：按 id 查询候选的判定。只提供验证聚合结果，绝不提供逐
用例的隐藏数据。

请求：

```http
GET /evolution/v1/runs/run_7f3/candidates/sha256:c41e…
Evolution-Fencing-Token: ft_7f3_2_b81c
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate": "sha256:c41e…",
  "status": "accept",
  "revision": "sha256:7c1e…",
  "aggregates": {"validation": {"cases": 30, "seeds": 3, "mean_delta": 0.061, "ci": [0.028, 0.094]}},
  "reasons": ["validation_improved"]
}
```

验证仍在进行时，`status` 为 `pending`，`aggregates` 为 `{}`。这是
[07-verification.zh-CN.md](07-verification.zh-CN.md) 中的 `StrategyVerdictView`，判定模型由
该文档负责。

### POST /evolution/v1/runs/{run}/budget:charge

`ctx.budget.charge`：记录一笔显式费用（提议语义见 §11.1）；返回剩余额度。平台
自身计量的调用会自动计费，无需在此调用。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"usage": {"usd": 0.40, "rollouts": 0, "tokens": 0}, "note": "replay of 4 cached rollouts"}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"remaining": {"usd": 13.18, "wall_clock_s": 5370, "rollouts": 304, "tokens": null}}
```

`null` 表示该维度没有限制。错误：`402` `budget_exhausted`；`422` 金额为负。

### POST /evolution/v1/jobs/{id}/complete

`run(ctx)` 已返回。以 `completed` 结束运行，并附带其 `RunSummary`。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"summary": {"rounds": 3, "notes": "two candidates submitted; round 2 accepted", "extra": {"findings": 5}}}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "completed"}
```

错误：`409` fencing token 过期，或运行已结束（例如在此期间被取消；此时响应携带
运行的最终状态）。

### POST /evolution/v1/jobs/{id}/fail

`run(ctx)` 失败。仍有剩余尝试次数的可重试失败会以相同的运行 id 将运行重新排队；
否则运行以 `failed` 结束。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "own progress store unreachable", "retryable": true}
```

响应 `200`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "queued", "attempt": 3}
```

错误：`409` fencing token 过期或运行已结束。

## 15. 示例

### 15.1 流水线启动一次运行并跟踪它

使用生成的客户端 SDK（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）：

```python
import time
from avernet_evolution import Client

c = Client.from_env()
key = "nightly-bot_123-2026-10-08"                     # deterministic: same key on every retry
run_id = c.runs.start(bot_id="bot_123", binding="bind_01",   # caller owns the bot: no entity_id
                      budget={"max_usd": 10, "max_wall_clock_s": 3600},
                      idempotency_key=key)              # safe to repeat: returns the same run_id

while (run := c.runs.get(bot_id="bot_123", run_id=run_id)).status in ("queued", "running"):
    time.sleep(30)                                      # short lookups by id; no request held open

print(run.status, run.budget_used.usd)
for cand in c.runs.candidates(bot_id="bot_123", run_id=run_id):
    print(cand.candidate_id, cand.verdict.status)       # approval is Promotion's job (08-promotion.md)
```

使用 CLI 实现同样的操作：

```bash
avn evolve run start --bot bot_123 --binding bind_01 \
  --idempotency-key nightly-bot_123-2026-10-08 --max-usd 10 --output json
avn evolve run status --bot bot_123 run_7f3 --wait --output json
```

### 15.2 所有者添加一个绑定

```python
policy = c.policy.get(bot_id="bot_123")
bindings = policy.bindings + [Binding(
    id="bind_02", strategy="platform/consolidate-memory@1.0.0",
    trigger={"schedule": "0 4 * * 0"}, parent="active", allowed_genes=["memory"],
    verification_profile="default@1",
    budget={"max_usd": 5, "max_wall_clock_s": 1800}, params={})]
try:
    c.policy.put(bot_id="bot_123", bindings=bindings, if_match=policy.etag)
except BindingCheckFailed as e:                          # 422: fix the binding, nothing was written
    for d in e.details:
        print(d.binding, d.check, d.detail)
```

### 15.3 触发器调度器触发一个绑定

在服务内部，一次定时触发就是一次幂等提交：

```python
async def fire_schedule(binding: Binding, bot: BotRef, slot: datetime) -> None:
    if await kill.is_paused() or await kill.is_bot_frozen(bot):
        await firings.record_skipped(binding.id, slot, reason="paused_or_frozen")
        return
    key = f"{binding.id}/{slot.isoformat().replace('+00:00', 'Z')}"   # bind_01/2026-10-08T02:00:00Z
    try:
        run_id = await runs.submit(bot, binding.id, idempotency_key=key,
                                   trigger=TriggerRecord(kind="schedule", fire_time=slot,
                                                         event_id=None, requested_by=None))
        await firings.record_fired(binding.id, slot, run_id)
    except (BindingBusy, CeilingExceeded, BindingCheckFailed, EvolutionFrozen) as e:
        await firings.record_skipped(binding.id, slot, reason=type(e).__name__)
```

### 15.4 基于作业协议的最小 job worker

任何语言编写的 job worker 都只需要 HTTP。下面的示意代码使用不带 SDK 的 Python，
以展示线上协议；策略 SDK 的作业协议版 `StrategyContext` 封装的正是这些调用。

```python
import os, threading, time
import httpx

base = os.environ["EVOLUTION_JOB_ENDPOINT"]             # injected by the worker's bootstrap
http = httpx.Client(base_url=base, timeout=30)

def heartbeat_loop(job_id: str, token: str, stop: threading.Event, cancelled: threading.Event):
    while not stop.wait(20):
        r = http.post(f"/evolution/v1/jobs/{job_id}/heartbeat", json={},
                      headers={"Evolution-Fencing-Token": token})
        if r.status_code == 409:                        # lease lost: someone else holds the run now
            cancelled.set(); return
        if r.json()["cancelled"]:
            cancelled.set()

while True:
    r = http.post("/evolution/v1/jobs:claim",
                  json={"worker_id": "worker-opt-01", "strategy_ids": ["team-x/prompt-opt"]})
    if r.status_code == 204:
        time.sleep(10); continue
    job = r.json()
    h = {"Evolution-Fencing-Token": job["fencing_token"]}
    run = f"/evolution/v1/runs/{job['run_id']}"
    stop, cancelled = threading.Event(), threading.Event()
    threading.Thread(target=heartbeat_loop, args=(job["job_id"], job["fencing_token"], stop, cancelled)).start()
    try:
        ws = http.post(f"{run}/workspaces", headers=h,
                       json={"revision": job["parent"], "key": f"{job['run_id']}/main"}).json()
        op = http.post(f"{run}/evaluations:train", headers=h,
                       json={"workspace_id": ws["workspace_id"],
                             "idempotency_key": f"{job['run_id']}/baseline-train"}).json()
        while (s := http.get(f"{run}/operations/{op['operation_id']}", headers=h).json())["status"] \
                in ("queued", "running"):
            if cancelled.is_set(): break
            time.sleep(15)
        patch = optimize(s["result"], http, run, h)     # its own search; model calls via models:complete
        cand = http.post(f"{run}/candidates", headers=h, json={"patch": patch,
                         "rationale": "best of 12 prompt variants on train", "evidence": []}).json()
        http.post(f"/evolution/v1/jobs/{job['job_id']}/complete", headers=h,
                  json={"summary": {"rounds": 1, "notes": cand["candidate_id"], "extra": {}}})
    except Exception as e:
        http.post(f"/evolution/v1/jobs/{job['job_id']}/fail", headers=h,
                  json={"reason": str(e)[:500], "retryable": True})
    finally:
        stop.set()
```

worker 不存储任何操作 id：崩溃之后，重新派发的尝试以相同的键重复相同的
`workspaces` 和 `evaluations:train` 调用，并拿回相同的工作区和操作。

### 15.5 一次端到端的运行（含一次崩溃）

Bot `bot_123`，绑定 `bind_01`（`clawevolve/bot-evolution@2.0.0`），每晚定时。
ClawEvolve 自身的代码见 [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)。

| 时间 | 事件 | 运行状态 |
| --- | --- | --- |
| 02:00:00 | 定时触发；键 `bind_01/2026-10-08T02:00:00Z`；输入被冻结：父版本 `active` = `r41`（`sha256:a90b…`）、`max_rounds: 3`、`max_usd: 20` | `run_7f3` `queued`，尝试 1 |
| 02:00:09 | Worker A 认领 `job_7f3_1`，token `ft_7f3_1_…`；上下文授予 `experience.sessions@1`、`agents@1`、`evaluate.train@1` 以及始终授予的部分 | `running` |
| 02:01 | 策略读取 7 天的片段，添加训练用例，把状态保存到自己的存储中 | `running` |
| 02:12 | 以键 `run_7f3/round-1` 调用 `workspaces`；以键 `run_7f3/round-1/tune` 调用 `agents:start` → `op_19a` | `running` |
| 02:15 | Worker A 的宿主机重启；心跳停止 | `running` |
| 02:16:10 | 租约到期；尝试次数变为 2；token `ft_7f3_1_…` 现已过期 | `queued`，尝试 2 |
| 02:16:30 | Worker B 认领新作业 `job_7f3_2`；策略按运行 id 重新加载状态；以相同的键重复 `workspaces` 和 `agents:start` → 相同的 `ws_7f3_r1`、相同的 `op_19a`，仍在运行 | `running`，尝试 2 |
| 02:23 | `op_19a` 成功；训练评估 `op_1b2`；策略提交候选 `sha256:c41e…` → `candidate/run_7f3/1`（`r42`） | `running` |
| 02:24–02:58 | 在 `default@1` 下验证；策略按 id 查询判定：先是 `pending`，然后是 `accept`；下一轮基于 `r42` 构建 | `running` |
| 03:26:59 | 第 3 轮结束；带摘要调用 `jobs/job_7f3_2/complete` | `completed` |

随后门禁把 `r42`（风险等级 T2：persona + skill）路由到评审队列
（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）；运行本身从不移动 `active`。

## 16. 交互

| 其他部分 | 方向 | 流转内容 |
| --- | --- | --- |
| [01-genome.zh-CN.md](01-genome.zh-CN.md) Genome Registry | 运行 → 基因组 | 将 `active` 解析为冻结的父版本；为 `ctx.parent`、工作区和 `content/{digest}` 读取修订版与内容；从 `{base, patch}` 创建候选修订版；移动 `candidate/<run>/<n>` 引用 |
| [01-genome.zh-CN.md](01-genome.zh-CN.md) | 基因组 → 运行 | 用于绑定检查的 Bot 基因组 `policy`（锁定基因、固定项） |
| [02-experience.zh-CN.md](02-experience.zh-CN.md) 经验 | 运行 → 经验 | `experience.sessions` / `experience.feedback` 背后的查询，已脱敏 |
| [03-strategy.zh-CN.md](03-strategy.zh-CN.md) Strategy Registry | Registry → 运行 | 注册记录、一致性状态、各引擎的能力提供方、按摘要的智能体定义、策略紧急停止开关 |
| [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 策略实现 | 运行 ↔ 策略 | 进程内的 `run(ctx)`，或作业协议；候选、操作、模型调用、预算费用 |
| [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md) | 默认进化策略 → 运行 | 作为 job worker / 进程内策略的 ClawEvolve 和 `platform/consolidate-memory`；作为事件触发器的 ClawInsight 条目 |
| [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) 实验记录 | 运行 → 实验记录 | 带策略版本和智能体定义摘要的运行记录、每一次提交、判定、成本、所用模型 |
| [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) | 实验记录 → 运行 | 以后：`active` 之外的父版本选择器 |
| [07-verification.zh-CN.md](07-verification.zh-CN.md) 验证 | 运行 → 验证 | 以运行冻结的配置调用 `verify(candidate, profile)`；训练评估；策略添加的训练用例 |
| [07-verification.zh-CN.md](07-verification.zh-CN.md) | 验证 → 运行 | 判定（对策略只提供聚合结果）；训练分数和点评 |
| [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 晋升 | 运行 → 晋升 | 提交时的静态底线检查（`FloorCheck`）；供门禁和评审队列使用的已验证候选 |
| [08-promotion.zh-CN.md](08-promotion.zh-CN.md) | 晋升 → 运行 | 没有运行需要等待的内容；运行候选的晋升在运行之后或独立于运行进行 |
| [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) 进化 API 与客户端 | 客户端 → 运行 | 通过 SDK、CLI 和 UI 后端进行策略配置读写、运行提交、状态查询、取消、候选列表 |
| [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) 元进化（以后） | 元进化 → 运行 | 在改进问题上以相等预算运行候选机制 |

## 17. 待定决策

| ID | 决策 | 选项 / 提议 |
| --- | --- | --- |
| D-1 | 控制面的模块位置 | A. 新建 `apps/evolution`（**推荐**）；B. Backend `core/evolution/`；C. ClawWeb 的 TS 控制面。见 [design.zh-CN.md](design.zh-CN.md) |
| ER-1 | 自动晋升上限放在哪里 | 已解决（提议字段）：`auto_promote_ceiling` 和 `rollout` 是绑定字段（§2.2、§3.1），由晋升读取（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)） |
| ER-2 | Bot 级上限和 Bot 级冻结在哪里配置 | 提议：在进化策略配置上增加 `limits`（每日/每月美元）和 `frozen` 字段；租户级上限放在租户设置中 |
| ER-3 | 迭代次数限制 | 治理要求把“迭代次数”列为单次运行限制之一，但平台看不到策略的轮次。提议：统计候选提交次数（`max_candidates`），或去掉该维度 |
| ER-4 | 作业协议缺口：工作区内容 | 已解决：`GET …/workspaces/{workspace}/files`、`PUT …/workspaces/{workspace}/files/{path}` 和 `POST …/workspaces/{workspace}:patch`（§9.1、§14.2）；`ctx.log` 和 `ctx.artifacts` 分别是 `POST …/log` 和 `PUT …/artifacts/{name}` |
| ER-5 | fencing token 的传递方式以及线上的取消 | 提议：请求头 `Evolution-Fencing-Token`；心跳响应中的 `cancelled` 标志 |
| ER-6 | 租约时长与 `max_attempts` | 提议：60 秒租约、20 秒心跳、3 次尝试；可在注册中按策略覆盖 |
| ER-7 | 每个绑定的并发与错过的触发 | 提议：每个绑定一个活跃运行；错过的时段跳过，不补跑 |
| ER-8 | 运行提交请求体 | 已解决：`{binding, params?, budget?}`，因为允许的基因和验证配置来自绑定。仍待定：运维人员是否可以运行未绑定的策略版本（例如用于测试） |
| ER-9 | 配置时的参数校验 | 已解决：注册记录中可选的 `params_schema`（提议，[03-strategy.zh-CN.md](03-strategy.zh-CN.md)），在写入策略配置时作为绑定检查 8 执行（§3.2） |
| ER-10 | 策略自身的进度存储 | 策略把自身进度持久化到自己的存储中（已达成一致）。待定：处于沙箱中的 job worker 如何访问该存储——例如在注册记录中为策略自身存储声明一条出网白名单，或由平台提供一个平台从不解读的、按运行划分的不透明 blob。与 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 中的 S-8 是同一决策 |
| ER-11 | 升级的结果 | 连续 N 次拒绝：以 `cancelled` 结束并带 `end_reason: "consecutive_rejections"`（提议），或以 `failed` 结束；N 的取值 |
| ER-12 | `budget:charge` 的语义 | 鉴于平台调用会被自动计量，策略可以显式计入哪些费用 |
