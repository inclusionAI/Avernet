# 默认进化策略

> English version: [04-default-strategies.md](04-default-strategies.md)

> 状态：草案（DRAFT）。属于 [Bot 进化架构](design.zh-CN.md) 中的一个组件。
> 本文描述平台默认提供的进化策略——ClawEvolve
> （`clawevolve/bot-evolution`）、记忆整合
> （`platform/consolidate-memory`）以及参考策略
> `platform/manual-patch`——以及 `apps/evolverun/` 中现有的流水线如何
> 演变为这些策略。

## 1. 目的与范围

**进化策略**（strategy，文中可简称“策略”）是实现唯一策略端口 `run(ctx)` 的代码，
它通过提交候选（Candidate）来提议对 Bot 的修改；随后由平台对其进行验证、门禁和晋升。
该端口、能力目录（capability catalog）、注册记录（registration record）以及策略 SDK 定义于
[03-strategy.zh-CN.md](03-strategy.zh-CN.md)。本文讨论的是平台随附提供的该端口的三个具体
**实现**：

| 策略 | 示例中的版本 | 角色 | 阶段 / 工作项 |
| --- | --- | --- | --- |
| `platform/manual-patch` | `1.0.0` | 最简单的参考策略：提交其 params 中给出的补丁。以确定性检查端到端地跑通整个循环 | P2，RSI-08 |
| `clawevolve/bot-evolution` | `2.0.0` | **主要默认策略。** 将现有的 ClawEvolve Bot 进化（诊断 → 规划 → tune/review 轮次 → bench）作为黑盒策略 | P3，RSI-13 |
| `platform/consolidate-memory` | `1.0.0` | 第二个、刻意不同的默认策略：将反馈和片段（episode）整合为精选的记忆条目（“dream” 作业）。以一组不同的能力证明可插拔性（R19 需要两个示例） | P5，RSI-15 |

本文负责：

- 每个默认进化策略的注册记录、params schema 以及 `run(ctx)` 行为；
- 从 ClawEvolve 现有各部分到平台模型的映射、去除其与 OpenClaw 的耦合，
  以及绞杀者（strangler）式迁移计划；
- 平台复用的 ClawEvolve 与 ClawBench 资产清单（这些行从验证清单
  移到了此处），以及 `apps/evolverun/` 中其他自我改进流水线的清单；
- 每个策略由策略自身持有的进度记录（平台没有检查点 API，因此每个策略
  自行持久化其进度）。

本文**不**负责：

| 主题 | 负责文档 |
| --- | --- |
| 端口、`StrategyContext`、能力目录、`Candidate`/`Verdict` 语义、一致性测试套件（conformance kit）、Strategy Registry | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| 绑定、运行、租约与重新派发、预算、作业协议定义、沙箱 | [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) |
| 片段、反馈、OpenClaw 的会话导出提供方（从 ClawEvolve 中移出） | [02-experience.zh-CN.md](02-experience.zh-CN.md) |
| 套件、划分、评分器（包括抽取出的 `platform/clawbench` 评分器）、判定策略、验证配置 | [07-verification.zh-CN.md](07-verification.zh-CN.md) |
| 门禁、风险等级、评审队列、晋升 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 基因组修订版、基因组补丁操作、记忆基因 | [01-genome.zh-CN.md](01-genome.zh-CN.md) |
| 实验记录 H（记录每次运行与提交的位置） | [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) |
| ClawEvolve 的改进机制组件（提示词、算子库、门禁阈值）作为第 3 层进化的对象 | [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) |

**它们在哪里运行。**

| 策略 | 代码位置 | 运行时（`runtime.kind`） |
| --- | --- | --- |
| `clawevolve/bot-evolution` | `apps/evolverun/clawweb-skills/clawevolve-skills/`（Python 阶段 skill，仅依赖标准库）加上一个轻量的策略入口；策略作者 = ClawEvolve 团队（“策略实现：策略作者，包括负责默认策略的 `apps/evolverun`”） | `job_worker`（容器镜像）。长期宿主为待定决策 DS-1（原 D-2） |
| `platform/consolidate-memory` | 由平台随 `apps/evolution` 提供 *（提议的位置）* | `in_process` *（提议）* |
| `platform/manual-patch` | 由平台随 `apps/evolution` 提供 | `in_process` *（提议）*；它是 RSI-08 在 singlebox 本地配置中使用的策略 |

三者都是普通的策略：它们的注册、绑定、运行和验证方式与其他团队的策略
完全相同。“默认”仅意味着平台随附提供它们并为其推荐绑定；它们不会获得
任何额外权限。

## 2. 领域模型

| 类型 | 含义 | 所有者 | 生命周期 |
| --- | --- | --- | --- |
| `StrategyRegistration`（每个默认策略一个） | 每个默认进化策略版本的注册记录（§4.2、§7.2、§11） | 策略作者；由 Strategy Registry 存储（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)） | 每个策略版本一条新记录 |
| `ManualPatchParams` | `platform/manual-patch` 的 params：要提交的基因组补丁 | 启动运行的调用方 | 运行开始时冻结 |
| `ClawEvolveParams` | `clawevolve/bot-evolution` 的 params：时间窗口、轮数、轮询间隔 | Bot 所有者（绑定） | 运行开始时冻结 |
| `Finding` | 从真实会话中诊断出的一个问题（好/坏案例、根因、关联片段） | ClawEvolve | 在运行的首次尝试中由诊断步骤创建；保存在 ClawEvolve 的状态中 |
| `TrainCaseProposal` | ClawEvolve 的规划步骤通过 `ctx.evaluate.add_train_cases` 向平台提议的 ClawBench 用例 | 由 ClawEvolve 提议；由验证服务分配划分，并在此后拥有该用例 | 添加后在套件注册表中进行版本管理 |
| `ClawEvolveState` | ClawEvolve 自身的每次运行进度：发现、当前基线、轮次号、待定候选、历史 | ClawEvolve，保存在**其自己的存储**中，以运行 id 为键（现有的 `ce_tasks` / `ce_steps` 可以承担） | 首次尝试时创建；每一步之后更新；重新派发时重新加载 |
| `ConsolidateMemoryParams` | `platform/consolidate-memory` 的 params | Bot 所有者（绑定） | 运行开始时冻结 |
| `LessonCluster` | 一组相关反馈条目，被归纳为一条候选经验教训 | consolidate-memory | 每次运行 |
| `ConsolidationPlan` | 一次 consolidate-memory 运行决定提交的记忆操作，在提交前持久化，以便重新派发时重新提交相同的补丁 | consolidate-memory，保存在其自己的存储中，以运行 id 为键 | 每次运行创建一次 |
| `RunSummary` | `run(ctx)` 的返回值；每个策略用其自己的计数器填充 | 策略；结构见 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) | 每次运行一次 |

此处使用但在别处定义的平台类型：`StrategyContext`、
`Candidate`、`Verdict`、`Operation`、`Capability`、`AgentDefinition`
（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）；`Episode`、`Feedback`
（[02-experience.zh-CN.md](02-experience.zh-CN.md)）；`GenomePatch`、`GenomeRevision`
（[01-genome.zh-CN.md](01-genome.zh-CN.md)）；`Binding`、`Run`、`Budget`
（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。

### 2.1 `ManualPatchParams`

```python
@dataclass(frozen=True)
class ManualPatchParams:
    patch: GenomePatch            # base must equal the run's parent revision
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch": {
    "patch_schema": 1,
    "base": "sha256:a90b…",                       // r41, the binding's parent ("active")
    "ops": [
      {"op": "file.edit", "target": "skills/refund-policy/SKILL.md",
       "edits": [{"kind": "replace_section", "heading": "## When to use",
                  "content": "Use for full and partial refunds of paid orders."}]}
    ],
    "rationale": "Partial refunds are allowed by policy; the skill did not trigger on them",
    "evidence": ["episode:ep_91"]
  }
}
```

### 2.2 `ClawEvolveParams`

```python
@dataclass(frozen=True)
class ClawEvolveParams:
    window_days: int = 7          # how far back diagnose reads sessions
    max_rounds: int = 3           # tune/review/bench rounds per run
    poll_s: int = 60              # interval between verdict lookups
    max_sessions: int = 500       # proposed: cap on episodes read (sessions() default limit)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"window_days": 7, "max_rounds": 3, "poll_s": 60, "max_sessions": 500}
```

`window_days` 和 `max_rounds` 来自源材料中的绑定示例，
`poll_s` 来自 ClawEvolve 的代码草图；`max_sessions` 是
本文提议的。

### 2.3 `Finding`

发现（finding）是 ClawEvolve 的诊断步骤（`clawevolve-diagnose`，一个 LLM
会话评判器）从真实会话中挖掘出的内容：一个好案例或坏案例、其可能的根因，
以及体现它的片段。发现是规划步骤（bench 用例）和 tune 提示词的输入。

```python
@dataclass(frozen=True)
class Finding:
    finding_id: str
    kind: Literal["bad_case", "good_case"]
    summary: str
    root_cause: str
    episodes: list[str]           # episode ids, evidence for the candidate
    severity: Literal["low", "medium", "high"]
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "finding_id": "f_12",
  "kind": "bad_case",
  "summary": "Bot refuses partial refunds although the policy allows them",
  "root_cause": "skill refund-policy does not trigger on partial refunds",
  "episodes": ["ep_91", "ep_97"],
  "severity": "high"
}
```

### 2.4 `TrainCaseProposal`

ClawBench 用例是带有 YAML front matter 的 Markdown 文件（`lib_tasks.py`）。
用例格式保持字节兼容（[07-verification.zh-CN.md](07-verification.zh-CN.md)）；
通过 JSON API 传输时，用例作为 JSON 信封中的一个字符串。

```python
@dataclass(frozen=True)
class TrainCaseProposal:
    case_key: str                 # strategy-chosen, stable per run: "<run>/<finding>/<n>"
    format: str                   # proposed: "clawbench-md/1"
    content: str                  # the Markdown case, unchanged
    derived_from: list[str]       # finding / episode ids
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "case_key": "run_7f3/f_12/1",
  "format": "clawbench-md/1",
  "content": "---\nid: partial_refund_01\ngrading_type: hybrid\n---\n## Prompt\nCan I get a refund for half of my order A17?\n## Expected\n…",
  "derived_from": ["finding:f_12", "episode:ep_91"]
}
```

### 2.5 `ClawEvolveState`

策略自身的进度记录。平台从不读取它，也没有针对它的 API；其结构由 ClawEvolve
自行决定。之所以在此展示，是因为正是它让 ClawEvolve 能够在崩溃和重新派发后
继续运行（§4.4）。

```python
@dataclass
class RoundRecord:
    round: int
    candidate: str | None         # candidate id submitted in this round, if any
    train_score_pct: int
    verdict: str | None           # pending | accept | reject | inconclusive

@dataclass
class ClawEvolveState:
    run_id: str
    findings: list[Finding]
    base: str                     # revision the next round edits (parent, then last accepted)
    next_round: int
    best_train_pct: int           # best train score so far; ClawEvolve's own submission filter
    pending: str | None           # candidate id whose verdict is still being looked up
    history: list[RoundRecord]
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "run_id": "run_7f3",
  "findings": [{"finding_id": "f_12", "kind": "bad_case", "summary": "Bot refuses partial refunds",
                "root_cause": "skill refund-policy trigger", "episodes": ["ep_91"], "severity": "high"}],
  "base": "sha256:a90b…",
  "next_round": 1,
  "best_train_pct": 78,
  "pending": "sha256:c41e…",
  "history": [{"round": 0, "candidate": "sha256:c41e…", "train_score_pct": 78, "verdict": "pending"}]
}
```

### 2.6 `ConsolidateMemoryParams`、`LessonCluster`、`ConsolidationPlan`

```python
@dataclass(frozen=True)
class ConsolidateMemoryParams:
    window_days: int = 7          # feedback considered
    min_feedback: int = 5         # below this, the run submits nothing
    max_new_items: int = 10       # cap on memory.add ops per run
    retire_unused_days: int = 30  # memory items with no supporting use for this long are retired

@dataclass(frozen=True)
class LessonCluster:
    lesson_key: str               # becomes the memory item key
    text: str
    tags: list[str]
    sources: list[str]            # feedback / episode ids
    action: Literal["add", "update", "retire"]

@dataclass(frozen=True)
class ConsolidationPlan:
    run_id: str
    base: str
    ops: list[dict]               # Genome Patch ops (memory.add / memory.update / memory.retire)
    rationale: str
    evidence: list[str]
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// A ConsolidationPlan as persisted before submission
{
  "run_id": "run_8c2",
  "base": "sha256:a90b…",
  "ops": [
    {"op": "memory.add",
     "item": {"key": "partial-refunds-allowed", "text": "Partial refunds are allowed for paid orders.",
              "tags": ["billing"], "source": ["feedback:fb_301", "episode:ep_91"]}},
    {"op": "memory.retire", "key": "old-shipping-sla"}
  ],
  "rationale": "3 corrections in 7 days state partial refunds are allowed; old-shipping-sla unused for 45 days",
  "evidence": ["feedback:fb_301", "feedback:fb_305", "feedback:fb_322"]
}
```

## 3. 现状

仓库中所有的自我改进代码都位于 `apps/evolverun/` 下；仓库其他地方没有
等价的实现。

| 流水线 | 位置 | 作用 | 定位 |
| --- | --- | --- | --- |
| **ClawEvolve Bot/skill 进化** | `apps/evolverun/clawweb`（TS 控制面）+ `clawweb-skills/clawevolve-skills`（Python 阶段 skill） | 会话诊断 → 规划 + ClawBench 用例 → tune/review 轮次 → bench → 接受（`test > baseline`）→ 打包 | **主要默认进化策略**（§4） |
| **ClawEvolve skill 加固** | 同上，`skill_hardening` 流程 | 对单个 skill 进行单阶段加固 | 第二个基于 ClawEvolve 的默认策略（skill 范围），§9 |
| **工作流运行修复** | ClawWeb `routes/evolve*.ts`、`run-analysis/*` | 失败运行的证据 → 诊断、经验教训、建议 → `suggestion_apply` 编辑工作流 YAML | 一个独立的策略；目标是工作流而非基因组——接入的第 2 阶段（§9） |
| **ClawInsight 改进** | `modules/clawinsight` | 监控 → 改进项 → `plan-source/v2` → 规划+优化 | ClawEvolve 绑定的事件触发器，外加通过 `experience.feedback` 输入的 `plan-source/v2`（§9） |
| **TaskGuard 运行时修复** | `apps/evolverun/taskguard` | 运行内的守护/修复/重试 | 运行时韧性，*不是*进化；其运行证据输入经验存储（Experience Store） |
| **Evolvetrace** | `apps/evolverun/evolvetrace` | 运行可观测性；进化标签页是 mock | 以后可能成为实验记录的归档视图和谱系的 UI |

ClawEvolve 已经具备大部分合适的接缝：带有 JSON Schema 的阶段契约目录、
`preprocess | postprocess | replace` 扩展、有版本的自定义阶段 skill、
claim/report 步骤协议、与生产者无关的 `plan-source/v2` 交接、
带评审防火墙的训练/验证分离，以及离线门禁校准。接入工作主要是**将这些接缝
重新指向平台契约**，而不是重写。

### 3.1 代码库证据

文件引用（来自调研清单，[research.zh-CN.md](research.zh-CN.md)）：

- **ClawEvolve 控制面**（TS）：`clawweb/public/modules/clawevolve/server/`
  ——`services/evolve/evolution-flow.ts`（三个封闭的流程键：
  `bot_evolution`、`skill_evolution`、`skill_hardening`），
  `stage-catalog.ts` + `resources/evolve/official-stage-catalog.json`
  （阶段 JSON Schema；`preprocess|postprocess|replace`），
  `task-registry.ts`、`routes/evolve.ts`、`routes/internal/evolve.ts`
  （claim/report 步骤协议），`services/evolve/skill-application.ts` +
  `contracts/bot-skill-gateway.ts`（经人工批准、带 CAS 的 skill 替换），
  `create-module.ts:60-200`（宿主 DI 选项）。
- **阶段 skill**（Python，标准库）：`clawweb-skills/clawevolve-skills/`——
  diagnose（`acquisition/discovery.py:41-96`、`sessions.py`、
  声明了 `session-export/v1` 的 `service_export.py`、
  带 80/20 训练/验证划分的 `integration/plan_source.py:25-97`），
  plan、tune、review、bench（`clawbench-base/scripts/lib_grading.py:51-108`）、
  pack、deploy。优化循环位于
  `clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py`
  （顺序 `:9491-9513`；接受规则 `:6256-6378` = 测试分数严格
  大于基线；可通过环境变量调节的门禁 `:1890-1905`；硬编码的工作区
  `:221`，`FIXED_WORKSPACE = /home/admin/.openclaw/workspace`；tune 通过
  `openclaw agent --local` `:7938`）。
- **Tune 直接编辑线上工作区**；被拒绝的轮次会恢复轮次前的打包。
  这是最需要去除的一项行为。
- **plan-source/v2**（`clawinsight/server/services/evolve/plan-source-contract.ts:6-40`）
  是与生产者无关的发现交接格式。
- **工作流运行修复**：运行证据摄取、分析运行、建议、
  经验教训；分析处理器（“ClawMind”）不在仓库中；批量
  分析器和经验教训过期只在测试中被实例化。
- **ClawInsight**：改进项 → plan-source → 规划+优化；管理员
  评审；3 次验证成功后的规则信任进化。
- **TaskGuard**：运行内的守护/修复/重试；没有持久化学习。
- **Evolvetrace**：可观测性；进化标签页是 mock
  （`src/components/workflow-workspace/evolution-mock.ts`）。

## 4. ClawEvolve 作为主要默认进化策略

### 4.1 黑盒

ClawEvolve 通过唯一的策略端口以**黑盒**方式接入：它保留自己的内部实现
（诊断逻辑、tune 和 review 智能体、提示词、变异算子库、轮次循环），只有
其边界迁移到 `StrategyContext`。它是黑盒层级的参考案例，而黑盒层级是
第一次迭代中唯一的层级（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。

ClawEvolve 的变化一览：

1. 它通过 `ctx.experience.sessions()` 读取会话，而不是读取
   OpenClaw 会话目录。
2. 它编辑由 `ctx.workspace.materialise()` 得到的**沙箱**，从不编辑线上
   工作区，并将 `ws.to_patch()` 作为候选提交。
3. 它通过 `ctx.agents.start()` 以长时操作的形式运行 tune 和 review 智能体，
   并通过 `ctx.evaluate.start_train()` 运行训练 bench。
4. 其 `test > baseline` 接受规则变为对其提交内容的**内部过滤器**；
   真正的接受由平台判定决定。
5. 它不再打包、恢复或部署：它从不更改线上 Bot。
6. 它以运行 id 为键持久化自己的轮次状态，因此被重新派发的运行可以
   继续执行（§4.4）。

### 4.2 注册记录

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {
    "experience.sessions@1": {},
    "agents@1": {"definitions": {                  // shipped with the strategy, uploaded at registration
      "clawevolve-tune":   {"engine": "openclaw", "path": "agents/clawevolve-tune"},
      "clawevolve-review": {"engine": "openclaw", "path": "agents/clawevolve-review"}
    }},
    "evaluate.train@1": {}
  },
  "params_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "window_days":  {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
      "max_rounds":   {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
      "poll_s":       {"type": "integer", "minimum": 10, "default": 60},
      "max_sessions": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500}
    }
  }
}
```

- `candidates@1` 和 `models@1` 始终被授予，无需声明。
- `params_schema`（注册记录中一个提议的可选字段，
  [03-strategy.zh-CN.md](03-strategy.zh-CN.md)）使绑定检查能够在写入策略配置时
  拒绝错误的 params（§11，参数 schema）。
- 智能体定义即现有的 `clawevolve-tune` 和
  `clawevolve-review` skill（`SKILL.md`、`agents/`、`references/`）。目前
  它们被安装在 ClawEvolve 所驱动的 OpenClaw 运行时中，因此只需给出名称
  即可。在平台上，它们**随策略一起提供**，在注册时由 `openclaw` 的
  `agents@1` 提供方上传并校验，以内容寻址方式存储，并在每次调用时按摘要
  加载到沙箱中（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。因此，修改
  tune 提示词意味着注册一个新的策略版本。
- 引擎兼容性是推导得出的：这些定义中唯一的 `engine`
  是 `openclaw`，因此在添加其他引擎的定义之前，绑定检查只接受将此策略
  用于 OpenClaw Bot（§5）。

### 4.3 ClawEvolve 各部分的映射

| ClawEvolve 部分 | 在新模型中 | 需要的改动 |
| --- | --- | --- |
| `acquisition/discovery.py`、`sessions.py`、`service_export.py` | OpenClaw 的 `experience.sessions` 提供方（平台侧，[02-experience.zh-CN.md](02-experience.zh-CN.md)） | 移到引擎会话导出契约之后；规范化为 `Episode`；标记基因组修订版 |
| `clawevolve-diagnose` | 在策略内部 | 通过 `ctx.experience.sessions()` 读取片段，而不是读磁盘 |
| `clawevolve-plan`（bench 用例） | 在策略内部 | 通过 `ctx.evaluate.add_train_cases()` 添加用例；由平台分配划分（去除其自身的 80/20 划分权） |
| `clawevolve-tune` + `clawevolve-review` | 在策略内部 | **编辑由 `ctx.workspace.materialise()` 得到的沙箱，而不是线上工作区**；通过 `ctx.agents.start()` 以按 id 查询的长时操作运行智能体；提交 `ws.to_patch()` |
| 训练 bench 运行（`bench-full-opt`） | `ctx.evaluate.start_train()`（按 id 查询的操作） | ClawBench 评分以 `platform/clawbench` 的形式移入验证服务 |
| `action_accept`（`test > baseline`）+ 建议性门禁 | 决定提交内容的内部过滤器 | 接受由绑定的验证配置下的平台判定决定；ClawEvolve 的门禁阈值可作为更严格配置的初始值 |
| baseline-pack / restore / pack / deploy | —（移除） | 不再需要：策略从不更改线上 Bot |
| `ce_tasks` / `ce_steps` / claim-report | —（被替换）作为编排；作为存储复用 | 平台运行编排 + 作业协议取代派发。这些表可以作为 ClawEvolve 以运行 id 为键的自有进度存储（§4.4） |
| `EvolutionFlow` 注册表（3 个封闭键） | 已注册的策略 | `bot_evolution`、`skill_evolution`、`skill_hardening` 成为三个已注册的策略（或一个带 params 的策略；待定决策，§14） |
| 阶段扩展 + 上传的阶段 skill | 策略版本，或以后的组合层级 | 替换某个阶段即成为一个新的策略版本，或在步骤类型存在后成为组合层级中的一步 |
| `skill-decision` 人工批准 + `BotSkillGateway.replaceLocalSkill`（CAS） | 平台评审队列 + 晋升（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)） | 人工批准推广到所有 T2 补丁 |
| 从 tune/review 提示词中去除验证 id（评审防火墙） | 结构性保证：策略从不接收验证用例，只接收判定聚合结果 | 无需去除；上下文中本就不包含它们 |

### 4.4 轮次循环

`clawevolve/bot-evolution` 的一次运行：

1. **仅首次尝试——诊断与规划。** 读取父修订版在 `window_days` 内的片段；
   挖掘发现；通过 `ctx.evaluate.add_train_cases` 提议训练用例（由平台分配划分；
   holdout、回归和安全划分保持隐藏）。保存状态。
2. **每一轮**（最多 `max_rounds` 轮），使用键 `<run_id>/round-<n>`：
   1. 以该键从当前 `base` 物化一个沙箱；
   2. 启动 tune 智能体（`<key>/tune`），按 id 等待其完成；
   3. 启动 review 智能体（`<key>/review`），按 id 等待其完成；
   4. 启动对该沙箱的训练评估（`<key>/train`），等待其完成；
   5. **提交过滤器**（ClawEvolve 自己的启发式规则，`action_accept` 的
      后继）：仅当训练分数超过迄今最佳时才提交 `ws.to_patch()`；保存待定
      候选 id；
   6. 每隔 `poll_s` 按候选 id 查询判定，直到它不再是
      `pending`；若为 `accept`，下一轮基于被接受的修订版进行；
      保存状态。
3. 返回一个 `RunSummary`，包含已运行的轮次和已提交的候选。

**为什么过滤器使用训练分数。** 目前 `action_accept` 比较的是
*test*（验证）分数。在平台上，ClawEvolve 不再能看到逐用例的验证结果——
只能看到判定聚合结果——因此它自己的过滤器只能使用训练划分；验证比较发生在
平台判定中，而平台判定才是真正的接受。ClawEvolve 的 `full_opt_gate` 和
`candidate_opt_gate` 被重新表达为判定策略规则，并在验证器中变为阻断性的
（[07-verification.zh-CN.md](07-verification.zh-CN.md)）；它们不再
在策略内部运行。

**等待判定。** 运行在查询判定期间保持 `running` 状态，这段时间计入绑定的
`max_wall_clock_s`。没有回调，也没有等待状态。

**崩溃与重新派发。** 对于租约已过期的运行，平台以相同的运行 id 和
`ctx.attempt + 1` 重新派发。ClawEvolve 从其存储中重新加载
`ClawEvolveState`（现有的 `ce_tasks` / `ce_steps` 可以承担），
并且由于每个工作区和操作的键都由运行 id 和轮次推导而来，重复调用
`materialise`、`agents.start` 和 `evaluate.start_train` 会返回相同的沙箱
和相同的操作——包括已经完成的智能体编辑——而无需再次付费。崩溃后重新提交的
候选会得到相同的候选 id，因为该 id 是补丁的内容哈希。

**ClawEvolve 使用的幂等键。** 遵循共享规则
`<run_id>/<own step>`：

| 调用 | 键 |
| --- | --- |
| `workspace.materialise` | `run_7f3/round-2` |
| `agents.start("clawevolve-tune")` | `run_7f3/round-2/tune` |
| `agents.start("clawevolve-review")` | `run_7f3/round-2/review` |
| `evaluate.start_train` | `run_7f3/round-2/train` |

绝不使用发送时的时间戳：键在各次重试之间必须完全相同。

### 4.5 平台复用的 ClawEvolve 评估资产

Bot 质量评估清单中的这些行属于 ClawEvolve 和
ClawBench 资产。它们要么成为本策略的一部分，要么成为验证器从中抽取的
构建模块；复用的验证器一侧在
[07-verification.zh-CN.md](07-verification.zh-CN.md) 中规定。

| 组件 | 位置 | 作用 | 状态 | 复用为 |
| --- | --- | --- | --- | --- |
| **ClawBench runner** | `apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-base/scripts/` | Markdown + YAML 用例（`lib_tasks.py`）。评分器：`lib_grading.py` 中的 `automated`（由用例提供的 `grade(transcript, workspace)`）、`llm_judge`（评分细则）、`hybrid`（加权）。脚本化的多轮用户（`interactions`）。`--runs N` 均值/标准差 | **在用**，仅限 OpenClaw（在复制的工作区上运行 `openclaw agent --local`） | 平台验证器（`platform/clawbench`）的**用例格式 + 评分器** |
| **ClawWeb Bench store** | `apps/evolverun/clawweb/public/shared/server/schema.ts`（`cm_bench_domains/templates/template_versions/runs/task_results/artifacts`）、`routes/bench.ts` | 有版本的套件（domain = 套件）、带 `source_hash` 的已发布模板、运行及逐用例结果（含明细与对话记录） | **在用**（OSS 版本中也有） | **套件注册表**和**验证结果**的数据模型初始来源 |
| **ClawEvolve 划分** | `clawevolve-plan/clawevolve_plan/bench/split.py`、`clawevolve_bench_plan_run.py:185-211`、`clawevolve_optimize_run.py:1140-1330` | 训练/测试域、按会话分组的防泄漏划分、从 tune/review 提示词中去除验证 id、用例和评分器冻结 | **在用** | **划分分配 + 可见性规则**，现由平台负责 |
| **ClawEvolve 门禁** | `clawevolve_optimize_run.py`：`action_accept` `:6256`、`candidate_static_gate` `:2666`、`candidate_opt_gate` `:5675-5958`、`full_opt_gate` `:5862-5947`、`replicate-validation` `:6380-6520`、评估标识 `:3604-3745` | 当且仅当测试分数 > 基线时接受。回归预算、受保护信号以及成对的带种子复现都存在，但属于**建议性或不可达** | 接受规则在用；其余处于休眠 | 验证器中的**比较器 + 判定策略**构建模块（纯函数）；`action_accept` 保留为策略自己更宽松的过滤器 |
| **门禁校准 / 回放** | `clawevolve-skills/scripts/calibrate_evolution_gates.py`、`replay_candidate_gate.py` | 由带标注的历史决策 + 对抗场景组成的黄金语料。报告门禁的精确率/召回率。在已存储的轮次上离线回放门禁 | 在用的工具 | **验证器校准**以及针对验证配置和提交过滤器变更的**机制验证**的初始来源（[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)） |
| **诊断 → 规划** | `clawevolve-diagnose/clawevolve_diagnose/judge/*`、`clawevolve-plan/bench/case_contract.py`、`template_builder.py` | LLM 会话评判器从真实会话中挖掘好/坏案例，并将其转为 bench 用例 | 在用 | 在本策略内部（发现、训练用例），以及在验证器一侧基于生产故障的**回归套件扩充** |

这些资产的迁移（验证器迁移中与 ClawEvolve/ClawBench 相关的步骤）：

1. 将 `lib_grading` + 用例解析抽取为 `platform/clawbench` 评分器
   插件，保持 Markdown 用例格式字节兼容。
2. 将套件存储迁移到套件注册表，并保持 ClawWeb Bench 可读
   （或将其变为一个视图）。
3. 将 ClawEvolve 的 `full_opt_gate` 和 `candidate_opt_gate` 重新表达为
   判定策略规则，并在默认配置下使其成为**阻断性**的。保留 `action_accept`
   作为策略自身（更宽松）的策略，叠加在其上。
4. 将门禁校准（`calibrate_evolution_gates.py`）扩展到评判器
   校准，并按计划定期运行。

ClawBench 是否是*唯一的*平台默认评分器，还是多个评分器之一，属于
待定决策 DS-2（§14）。

## 5. 将 ClawEvolve 与 OpenClaw 解耦

代码中发现的耦合点，以及各自的替代方案：

| 耦合 | 替代为 |
| --- | --- |
| 硬编码的 `/home/admin/.openclaw/workspace` 和 `~/.openclaw/agents/*/sessions` | 编辑使用 `ctx.workspace`（物化的基因组）；会话使用 `ctx.experience.sessions()` |
| 使用 `openclaw agent --local --agent …` 运行 tune/review/judge/bench | `ctx.agents.start()`（`agents` 能力；按 id 查询的操作），先实现 OpenClaw 提供方；bench 执行移到验证服务中的评估 Bot |
| OpenClaw md 约定（SOUL/AGENTS/TOOLS、`skills/skills-local`、`config/mcporter.json`） | 基因组基因（`persona`、`skills`、`tools.mcp`）；由引擎投影负责路径 |
| `singlebox/bot-runtime.ts` 中的 `active_engine='openclaw'`、`bot_type='personal'` 过滤条件 | 绑定检查：Bot 的引擎必须为 `needs` 中的所有内容提供提供方，并且属于 `agents@1` 定义的 `engine` 值之一 |
| 直接通过 SQLite 读取 Backend 表（`ac_bots`、…） | Genome Registry / Backend API |
| `local-execution.ts` 中的 `OPENCLAW_*` 环境变量 | 仅在配置加载中使用（原始环境访问属于 config/bootstrap） |

这样，OpenClaw 之外的引擎只需要能力提供方（会话导出、
智能体运行器）和引擎投影支持——外加一个新策略版本中针对该引擎的 ClawEvolve
智能体定义——而不需要 fork ClawEvolve。

## 6. 迁移计划（绞杀者模式，不搞一次性切换）

1. **影子记录（行为不变）。** ClawEvolve 继续像现在一样运行，
   但每个被接受的轮次还会通过 Genome API 记录一个基因组修订版（来自其
   打包结果）。用真实输出验证基因组模型。
2. **黑盒适配器。** 将 ClawEvolve 注册为一个 job-worker 策略，
   其 `run(ctx)` 调用现有的 skill 脚本，路径指向一个已物化的沙箱，
   并提交得到的补丁。由平台进行编排、验证和晋升。AgentEvolve UI 将平台
   运行与旧任务并列展示。（源计划称之为
   `clawevolve/bot-evolution@1`；本文将其理解为 1.x 版本线，并将下面的
   原生策略称为 `2.0.0`。）
3. **原生策略。** skill 直接通过策略 SDK 进行读写；
   从流程中移除 pack/restore；旧任务类型被
   弃用。这就是 §4.2 中注册的 `clawevolve/bot-evolution@2.0.0`。
4. **第二个和第三个默认策略。** `skill_hardening`、ClawInsight 触发器，
   以及工作流运行修复（待工作流可以表示为基因组基因或其自身的制品之后——
   待定决策 DS-3）。

每一步都可以独立发布且可回退。工作项 RSI-13
涵盖第 1–3 步，其完成标准是：`clawevolve/bot-evolution` 通过平台、
在不触碰线上工作区的情况下，在其自身的 bench 上取得与旧版 AgentEvolve
相同或更好的结果。

## 7. `platform/consolidate-memory`

### 7.1 为什么需要第二个默认策略

只有通过第二个、刻意不同的策略，抽象才能得到验证
（R19：有两个示例之后再抽象）。记忆整合在端口必须处理的每个维度上都与
ClawEvolve 不同：不同的基因（`memory`，而非 persona 和 skills）、不同的触发方式
（每周或在 N 条新反馈之后，而非每晚）、不同的输入（以反馈为主，连同其引用的
片段，而非对所有会话进行诊断）、没有智能体也没有训练评估，以及每次运行
只提交一次，而非多轮循环。

它借鉴了业界实践中的后台整合器（见
[research.zh-CN.md](research.zh-CN.md)）：

| 参考 | 我们采纳的内容 |
| --- | --- |
| Anthropic Dreams | 从会话写出一个**新**版本，输入保持不变，采纳或丢弃 → 运行提议一个补丁；由晋升决定 |
| OpenClaw Dreaming | 需主动开启、每晚运行、经打分后晋升到记忆 → 绑定需主动开启；只有通过门禁的条目才会进入记忆 |
| Hermes Curator | 归档，从不删除；置顶条目写保护；试运行 → 条目被退役（保留在历史中），置顶由平台底线保障 |
| ACE Curator | 逐条的增量更新可避免简短化偏差和上下文坍缩 → 每个记忆条目一个操作，从不整体重写一大块内容 |
| Letta sleep-time agents | 执行任务的智能体不编辑记忆；由后台进程编辑 → 主体 Bot 从不写自己的记忆 |

### 7.2 注册记录

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "platform/consolidate-memory",
  "version": "1.0.0",
  "runtime": {"kind": "in_process", "entrypoint": "avernet_evolution.strategies.consolidate_memory:ConsolidateMemoryStrategy"},
  "needs": {
    "experience.feedback@1": {},                 // ratings, corrections, outcomes, findings
    "experience.sessions@1": {}                  // episodes cited by feedback (RSI-15); models@1 is always granted
  },
  "params_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "window_days":        {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
      "min_feedback":       {"type": "integer", "minimum": 1, "default": 5},
      "max_new_items":      {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
      "retire_unused_days": {"type": "integer", "minimum": 7, "default": 30}
    }
  }
}
```

声明 `experience.sessions@1` 意味着会向所有者表明该策略会
读取对话历史（[02-experience.zh-CN.md](02-experience.zh-CN.md)）。

进程内策略的 `entrypoint` 字段是提议的；规范性的
`runtime` 结构见 [03-strategy.zh-CN.md](03-strategy.zh-CN.md)。

### 7.3 绑定

- **触发器：** 每周定时，或在 N 条新反馈之后触发事件。
- **`allowed_genes: ["memory"]`。**
- **验证配置：** 包含回归套件和
  **矛盾检查**（新条目不得与 persona、置顶条目或彼此相矛盾）的配置。
  源材料提议了这一配置；下文示例中的名称 `memory@1` 仅作示意，其定义属于
  [07-verification.zh-CN.md](07-verification.zh-CN.md)。
- **预算：** 较小（源材料示例使用 `max_usd: 5`、
  `max_wall_clock_s: 1800`）。

### 7.4 `run(ctx)`

1. 通过 `ctx.experience.feedback()` 读取 `window_days` 内的反馈。若少于
   `min_feedback`，则不提交直接返回。通过 `ctx.experience.sessions()` 读取同一
   时间窗口内的片段，并保留反馈所引用的那些，作为每条经验教训的
   支撑证据。
2. 加载父修订版当前的记忆条目（`ctx.parent` →
   `spec.memory.items_digest` → 按摘要获取内容）。
3. 通过 `ctx.models.complete()`（普通模型调用：无工具、无步骤）将反馈聚类为
   候选经验教训，并对每条经验教训作出决定：
   `memory.add`、`memory.update`（已有的键）或 `memory.retire`
   （被反馈否定的条目，或在 `retire_unused_days` 内未被使用的条目）。
   条目被退役，从不删除；置顶条目保持不动。
4. 在其自己以运行 id 为键的存储中**持久化 `ConsolidationPlan`**，
   然后提交**一个**由逐条记忆操作组成的补丁。被重新派发的运行会
   找到已保存的计划并重新提交相同的补丁，因此得到相同的
   候选 id，而不是因新的模型输出产生第二个不同的候选。
5. 返回。验证和晋升无需该运行即可继续。

**晋升结果。** 记忆条目的 add/update/retire 属于风险等级 T1，因此在
所有者策略下，门禁通过后会自动晋升；任何影响
persona 的内容都会进入评审（T2）。该策略从不使用记忆的 `replace` 模式
（T3）。

**引擎记忆投影契约落地之前的过渡方案（RSI-05）。**
在引擎能够投影精选记忆条目之前，记忆进化仅限于
一个由平台管理的 persona 文件（例如 `LESSONS.md`）。在该
过渡期内，策略对该文件发出 `file.edit` 操作，而不是
`memory.*` 操作。这些属于 persona 编辑，因此属于 T2 并进入评审，
且绑定必须允许它们（见待定决策 DS-5，§14）。该策略
使用反馈和片段（RSI-15）；由主体 Bot 记录的观察
随 Bot 调用方一起推迟（DR-3）。

## 8. `platform/manual-patch`（参考策略）

尽可能小的策略。它的存在使平台循环——
编排、候选记录、验证、门禁、晋升、回到旧版本——
可以在接入任何真实策略之前构建和测试，同时也让
一致性测试套件有一个可以通过的最简示例。

- **注册：** 不声明任何能力（它只使用始终授予的
  `candidates@1` 和 `parent`）。
- **参数：** 要提交的基因组补丁（`ManualPatchParams`）。
- **`run(ctx)`：** 检查 `patch.base` 是否等于 `ctx.parent.id`（否则以
  不可重试的原因使运行失败），将补丁作为候选提交，然后返回。它不等待
  判定：无论运行是否仍在进行，提交的内容都会被验证。
- **验证：** 仅确定性检查（自动评分器，不使用
  评判器），因此 singlebox 故事可以离线复现。示例中的配置名称
  `deterministic@1` 仅作示意。
- **幂等性：** 无需状态。相同的 params 产生相同的补丁，因此
  被重新派发的运行会重新提交并得到相同的候选 id。

RSI-08 的 singlebox 故事使用了它：以该策略启动一次运行 →
记录候选 → 门禁 → 晋升 → Bot 更新 → 通过晋升较早的修订版回到旧版本；
以相同的幂等键重复启动会返回相同的运行 id。

## 9. `apps/evolverun/` 中的其他流水线

| 流水线 | 计划 |
| --- | --- |
| `skill_evolution` 流程 | 与 Bot 进化相同的阶段，范围限定为一个目标 skill。要么作为单独注册的策略，要么作为 `clawevolve/bot-evolution` 的一个参数（DS-6，§14）。迁移第 4 步 |
| `skill_hardening` 流程 | 对单个 skill 的单一加固阶段。注册为策略，限定 skill 范围（`allowed_genes: ["skills"]`）。迁移第 4 步 |
| ClawInsight 改进 | 不是策略：而是 ClawEvolve 绑定的**事件触发器**（例如 `{"event": "failure_rate_alert"}`），其 `plan-source/v2` 发现通过 `experience.feedback` 成为输入。使用该输入需要 ClawEvolve 声明 `experience.feedback@1`，即一个新的策略版本 |
| 工作流运行修复 | 一个以工作流 YAML 而非基因组为目标的独立策略。等待 DS-3（工作流存放在哪里）和 DS-4（缺失的分析器契约） |
| TaskGuard | 运行内的运行时韧性，不是进化。其运行证据作为反馈输入经验存储（[02-experience.zh-CN.md](02-experience.zh-CN.md)） |
| Evolvetrace | 可观测性。以后可能成为实验记录的归档视图和谱系的 UI |

## 10. 服务接口

### 10.1 策略类

三者都实现 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 中的端口：

```python
class EvolutionStrategy(Protocol):
    async def run(self, ctx: StrategyContext) -> RunSummary: ...
```

每个策略自己的存储由其宿主注入（job-worker 入口，
或对进程内策略而言的 `apps/evolution` 组合根）。
它是策略的存储，而不是平台 API。

```python
class RunStateStore(Protocol[S]):
    """Strategy-owned progress, keyed by run id. The platform never reads it."""
    async def load(self, run_id: str) -> S | None: ...
    async def save(self, run_id: str, state: S) -> None: ...   # raises on write failure; never silently drops


class ManualPatchStrategy(EvolutionStrategy):
    """platform/manual-patch@1.0.0 — submits params.patch as one candidate.

    Needs no declared capability. Fails the run (retryable=False) if the
    patch base is not the run's parent revision.
    """
    async def run(self, ctx: StrategyContext) -> RunSummary: ...


class ClawEvolveStrategy(EvolutionStrategy):
    """clawevolve/bot-evolution@2.0.0 — diagnose, plan, then tune/review/train rounds.

    Needs experience.sessions@1, agents@1 {clawevolve-tune, clawevolve-review},
    evaluate.train@1. Keeps ClawEvolveState in `store`; all workspace and
    operation keys are derived from the run id and round, so a re-dispatch
    re-attaches instead of repeating paid work.
    """
    def __init__(self, store: RunStateStore[ClawEvolveState]) -> None: ...
    async def run(self, ctx: StrategyContext) -> RunSummary: ...

    # Internals kept from today's skills (not part of any platform contract):
    #   diagnose(episodes) -> list[Finding]                 clawevolve-diagnose
    #   plan_bench(findings) -> list[TrainCaseProposal]     clawevolve-plan
    #   build_tune_prompt(findings, history) -> str         clawevolve-tune (_build_tune_prompt)
    #   build_review_prompt(findings, train) -> str         clawevolve-review


class ConsolidateMemoryStrategy(EvolutionStrategy):
    """platform/consolidate-memory@1.0.0 — one itemized memory patch per run.

    Needs experience.feedback@1 and experience.sessions@1 (models@1 is always granted). Persists the
    ConsolidationPlan before submitting so a re-dispatch resubmits the same
    patch.
    """
    def __init__(self, store: RunStateStore[ConsolidationPlan]) -> None: ...
    async def run(self, ctx: StrategyContext) -> RunSummary: ...
```

### 10.2 每个策略的返回内容

`RunSummary` 定义于 [03-strategy.zh-CN.md](03-strategy.zh-CN.md)；各
默认进化策略填充的字段：

| 策略 | 摘要内容 |
| --- | --- |
| `platform/manual-patch` | `candidates: [<id>]` |
| `clawevolve/bot-evolution` | `rounds`、`candidates`（每轮的 id）、`findings` 数量 |
| `platform/consolidate-memory` | `candidates`（零个或一个 id）、按类型统计的 `ops` 数量，或一个 `skipped` 原因（例如 “not enough feedback”） |

### 10.3 各策略的能力调用

| 调用 | manual-patch | ClawEvolve | consolidate-memory |
| --- | --- | --- | --- |
| `ctx.parent` | 读取 id | 读取 id | 读取 id 和记忆条目 |
| `ctx.experience.sessions` | — | 是 | 是（反馈引用的片段） |
| `ctx.experience.feedback` | — | —（以后用于 ClawInsight 输入） | 是 |
| `ctx.evaluate.add_train_cases` | — | 首次尝试 | — |
| `ctx.workspace.materialise` / `ws.to_patch` | — | 每轮 | — |
| `ctx.agents.start`（操作） | — | tune、review | — |
| `ctx.evaluate.start_train`（操作） | — | 每轮 | — |
| `ctx.operations.get` / `wait` | — | 是 | — |
| `ctx.models.complete` | — | 可选 | 是 |
| `ctx.candidates.submit` | 一次 | 每轮，经过过滤 | 至多一次 |
| `ctx.candidates.verdict` | — | 是，在轮次之间 | — |

## 11. API

公开端点位于前缀 `/openapi/v1` 之下；下文的标题均相对于该前缀。
内部作业协议位于 `/evolution/v1` 之下，并在本节末尾单独
列出。所有内容均为 JSON。下文的响应
展示的是标准信封中的 `data` 负载；信封、错误、
分页和幂等性见 [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)。本文不定义新的
端点：它展示默认进化策略如何使用以下文档中定义的端点：
[03-strategy.zh-CN.md](03-strategy.zh-CN.md)（注册）、
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)（策略配置、运行、作业协议）以及
[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)（约定、幂等键）。

**注册（公开）。**

### `POST /evolution/strategies`

注册一个默认进化策略的版本。由策略的
发布方调用（`avn strategy publish`；对于平台提供的策略，则由
平台的发布流水线调用）。智能体定义随
请求一起上传，并由引擎的 `agents@1` 提供方校验。规范性
定义见 [03-strategy.zh-CN.md](03-strategy.zh-CN.md)。

请求（ClawEvolve，同 §4.2；consolidate-memory 的记录见 §7.2，
manual-patch 的记录见下文）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {
    "experience.sessions@1": {},
    "agents@1": {"definitions": {
      "clawevolve-tune":   {"engine": "openclaw", "path": "agents/clawevolve-tune"},
      "clawevolve-review": {"engine": "openclaw", "path": "agents/clawevolve-review"}
    }},
    "evaluate.train@1": {}
  },
  "params_schema": {"type": "object", "additionalProperties": false, "properties": {"window_days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 7}}}   // abridged; full schema in §4.2
}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "needs": {
    "experience.sessions@1": {},
    "agents@1": {"definitions": {
      "clawevolve-tune":   {"engine": "openclaw", "digest": "sha256:5d1f…"},   // stored content-addressed
      "clawevolve-review": {"engine": "openclaw", "digest": "sha256:9e07…"}
    }},
    "evaluate.train@1": {}
  },
  "conformance": "pending"                        // must pass the kit before binding outside development
}
```

请求（manual-patch）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "platform/manual-patch",
  "version": "1.0.0",
  "runtime": {"kind": "in_process", "entrypoint": "avernet_evolution.strategies.manual_patch:ManualPatchStrategy"},
  "needs": {},
  "params_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {"patch": {"$ref": "genome-patch.schema.json"}}
  }
}
```

主要错误：`needs` 中的某项不在目录中时返回 `400`；
智能体定义校验失败或指定了没有
`agents@1` 提供方的引擎时返回 `422`；该版本已存在但内容不同时
返回 `409`。

### `GET /evolution/strategies/{id}/versions/{version}`

读取一条注册记录。由 UI、绑定检查和
运维人员调用。

请求：`GET /openapi/v1/evolution/strategies/platform%2Fconsolidate-memory/versions/1.0.0`

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "platform/consolidate-memory",
  "version": "1.0.0",
  "runtime": {"kind": "in_process", "entrypoint": "avernet_evolution.strategies.consolidate_memory:ConsolidateMemoryStrategy"},
  "needs": {"experience.feedback@1": {}, "experience.sessions@1": {}},
  "conformance": "passed"
}
```

主要错误：id 或版本未知时返回 `404`。

### 参数 schema

每个策略在运行开始时校验其 params（params 由
策略校验）。每个默认进化策略还会在其注册记录中将下面的 schema 声明为
`params_schema`（§4.2、§7.2 以及上文的
manual-patch 记录；这是一个提议的可选字段，定义于
[03-strategy.zh-CN.md](03-strategy.zh-CN.md)），因此绑定检查会在
写入策略配置时拒绝错误的 params（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。

`platform/manual-patch`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "patch": {"$ref": "genome-patch.schema.json"}   // the Genome Patch schema (01-genome.md)
  }
}
```

`patch` 在 schema 中不是 `required`：绑定（`bind_03`）携带
空的 params，每次运行在运行提交的 `params` 中提供补丁。
没有补丁的运行会在启动时以不可重试的原因失败。

`clawevolve/bot-evolution`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "window_days":  {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
    "max_rounds":   {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
    "poll_s":       {"type": "integer", "minimum": 10, "default": 60},
    "max_sessions": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500}
  }
}
```

`platform/consolidate-memory`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "window_days":        {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
    "min_feedback":       {"type": "integer", "minimum": 1, "default": 5},
    "max_new_items":      {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    "retire_unused_days": {"type": "integer", "minimum": 7, "default": 30}
  }
}
```

schema 使用 JSON Schema draft 2020-12。各项限制（minimum、maximum）为
提议值。

**绑定并启动默认进化策略（公开）。**

### `PUT /bots/{bot}/evolution/policy`

设置 Bot 的绑定列表。由 Bot 所有者或租户管理员调用
（UI、`avn evolve policy set`）。定义于
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)；此处展示为默认进化策略推荐的
绑定。

请求：

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
      "params": {"window_days": 7, "max_rounds": 3}
    },
    {
      "id": "bind_02",
      "strategy": "platform/consolidate-memory@1.0.0",
      "trigger": {"schedule": "0 4 * * 0"},
      "parent": "active",
      "allowed_genes": ["memory"],
      "verification_profile": "memory@1",
      "budget": {"max_usd": 5, "max_wall_clock_s": 1800},
      "params": {}
    },
    {
      "id": "bind_03",
      "strategy": "platform/manual-patch@1.0.0",
      "trigger": {"manual": true},
      "parent": "active",
      "allowed_genes": ["persona", "skills", "memory", "resources"],
      "verification_profile": "deterministic@1",
      "budget": {"max_usd": 0, "max_wall_clock_s": 600},
      "params": {}
    }
  ]
}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"bot": "bot_123", "bindings": ["bind_01", "bind_02", "bind_03"], "etag": "W/\"policy-7\""}
```

主要错误（绑定检查，发生在配置时，而不是在付费运行
进行到一半时）：Bot 的引擎没有 `needs` 中某个能力的提供方时返回 `422`
（例如在非 OpenClaw Bot 上使用 ClawEvolve，
因为其智能体定义仅适用于 OpenClaw）；`allowed_genes` 超出 Bot 基因组 `policy`
时返回 `422`（锁定基因保持
锁定）；`If-Match` 过期时返回 `412`。

### `POST /bots/{bot}/evolution/runs`

启动一次运行。定义于 [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)。
manual-patch 参考策略总是以这种方式启动，补丁放在
`params` 中。

请求（带请求头 `Idempotency-Key: 3f6c2a9e-8d1b-4c7e-9a0f-2b5d6e7f8a91`）；
请求体为 `{binding, params?, budget?}`，此处运行 `bind_03`：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "binding": "bind_03",
  "params": {
    "patch": {
      "patch_schema": 1,
      "base": "sha256:a90b…",
      "ops": [{"op": "file.edit", "target": "skills/refund-policy/SKILL.md",
               "edits": [{"kind": "replace_section", "heading": "## When to use",
                          "content": "Use for full and partial refunds of paid orders."}]}],
      "rationale": "Partial refunds are allowed by policy",
      "evidence": ["episode:ep_91"]
    }
  }
}
```

响应（`202`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "queued"}
```

主要错误：使用相同 `Idempotency-Key` 的重复请求返回相同的
`run_id`（不是错误）；params 未通过注册记录的
`params_schema` 时返回 `422`；补丁 base 不是父修订版的运行会在启动时
以不可重试的原因失败。

### 内部：默认进化策略发起的作业协议调用

`clawevolve/bot-evolution` 作为 job worker 运行，并发起以下 HTTP 调用。
进程内策略以对 `ctx` 的直接 Python 方法调用发起相同的调用，
负载也相同。端点列表和语义定义于
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)；每个请求都会
及时返回，可能超出短请求时长的工作是一个操作（`202` +
操作 id），未被授予的能力对应的端点返回
`403`，携带过期 fencing token 的调用返回 `409`。claim 之后，
每个调用都在 `Evolution-Fencing-Token` 请求头中携带 fencing token，
具体定义见该文档。

### `POST /evolution/v1/jobs:claim`

ClawEvolve worker 为其策略 id 领取一个作业。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"worker_id": "clawevolve-worker-3", "strategy_ids": ["clawevolve/bot-evolution"]}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "job_id": "job_7f3",
  "run_id": "run_7f3",
  "attempt": 1,
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60, "max_sessions": 500},
  "parent": "sha256:a90b…",
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "granted": ["candidates@1", "models@1", "experience.sessions@1", "agents@1", "evaluate.train@1"],
  "fencing_token": "ft_000231"
}
```

主要错误：没有可用作业时返回 `204`（无内容），此为提议。

### `POST /evolution/v1/jobs/{id}/heartbeat`

在 `run(ctx)` 工作期间续租。租约过期会以相同的运行 id 和
`attempt + 1` 将作业重新入队。

请求：`POST /evolution/v1/jobs/job_7f3/heartbeat`，带请求头
`Evolution-Fencing-Token: ft_000231`，请求体 `{}`。

响应（`cancelled: true` 是取消到达 worker 的方式；
SDK 将其转换为 `ctx.cancelled`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"lease_expires_at": "2026-10-09T02:06:00Z", "cancelled": false}
```

主要错误：租约丢失后返回 `409`（worker 必须停止）。

### `GET /evolution/v1/runs/{run}/parent`

`ctx.parent`。三个策略都会使用。

请求：`GET /evolution/v1/runs/run_7f3/parent`

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "sha256:a90b…",
  "seq": 41,
  "spec": {
    "persona": [{"type": "SOUL.md", "digest": "sha256:11aa…"}],
    "skills": [{"name": "refund-policy", "digest": "sha256:22bb…", "origin": {"kind": "local"}}],
    "memory": {"mode": "merge", "items_digest": "sha256:33cc…"}
  },
  "lineage": ["sha256:a90b…", "sha256:8f20…"]
}
```

### `GET /evolution/v1/runs/{run}/content/{digest}`

父修订版或工作区的文件字节。consolidate-memory 通过它读取
记忆条目集合；ClawEvolve 黑盒适配器通过它读取文件，以便
为旧脚本布置文件。

请求：`GET /evolution/v1/runs/run_8c2/content/sha256:33cc…`

响应（条目集合返回 `application/json`；其他文件以
原始字节返回）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"items": [{"key": "old-shipping-sla", "text": "Standard shipping takes 5 days.", "tags": ["shipping"],
            "source": ["episode:ep_12"]}]}
```

### `GET /evolution/v1/runs/{run}/experience/sessions`

`ctx.experience.sessions(days, limit, revision)`。ClawEvolve 诊断的
输入。返回经过脱敏和规范化的片段（[02-experience.zh-CN.md](02-experience.zh-CN.md)）。

请求：`GET /evolution/v1/runs/run_7f3/experience/sessions?days=7&limit=500`

响应：

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

主要错误：未授予 `experience.sessions@1` 时返回 `403`。

### `GET /evolution/v1/runs/{run}/experience/feedback`

`ctx.experience.feedback(days, kinds, limit)`。consolidate-memory 的主要输入。
`Feedback` 结构的规范定义见 [02-experience.zh-CN.md](02-experience.zh-CN.md)；
此示例仅作示意。

请求：`GET /evolution/v1/runs/run_8c2/experience/feedback?days=7`

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "feedback": [
    {"feedback_id": "fb_301", "kind": "correction", "episode_id": "ep_91",
     "revision_id": "sha256:a90b…", "text": "partial refunds are allowed",
     "created_at": "2026-10-07T09:15:00Z"},
    {"feedback_id": "fb_305", "kind": "rating", "episode_id": "ep_97",
     "revision_id": "sha256:a90b…", "rating": -1, "created_at": "2026-10-07T14:02:00Z"}
  ]
}
```

主要错误：未授予 `experience.feedback@1` 时返回 `403`（ClawEvolve 2.0.0
即属此情况）。

### `POST /evolution/v1/runs/{run}/evaluations/cases`

`ctx.evaluate.add_train_cases`。ClawEvolve 的规划步骤提议用例；
平台分配划分。*提议的解读：*响应只揭示被分配到 `train` 的
用例；平台放入隐藏划分的用例只计数，
不标识。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "cases": [
    {"case_key": "run_7f3/f_12/1", "format": "clawbench-md/1",
     "content": "---\nid: partial_refund_01\ngrading_type: hybrid\n---\n## Prompt\nCan I get a refund for half of my order A17?\n",
     "derived_from": ["finding:f_12", "episode:ep_91"]}
  ]
}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"received": 12, "train_case_ids": ["case_501", "case_503", "case_507"], "duplicates": 0}
```

主要错误：用例无法解析为 ClawBench 用例时返回 `422`；
未授予 `evaluate.train@1` 时返回 `403`。

### `POST /evolution/v1/runs/{run}/workspaces`

`ctx.workspace.materialise(revision, key)`。按键幂等：
被重新派发的运行会拿回同一个沙箱，其中包含已经完成的智能体编辑。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:a90b…", "key": "run_7f3/round-2"}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_42", "revision": "sha256:a90b…", "created": false}   // false: an existing sandbox was returned
```

### `POST /evolution/v1/runs/{run}/agents:start`

`ctx.agents.start(definition, workspace, prompt, idempotency_key)`。在沙箱内
以长时操作的形式启动 tune 或 review 智能体。
定义按摘要加载，只读；只有工作区
可写。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "definition": "clawevolve-tune",
  "workspace_id": "ws_42",
  "prompt": "Findings: f_12 partial refunds refused. History: round 1 rejected (regression on case_88). Edit skills/refund-policy …",
  "idempotency_key": "run_7f3/round-2/tune",
  "timeout_s": 1800
}
```

响应（`202`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_19a", "status": "queued"}
```

主要错误：定义不是该策略注册的定义，或未授予
`agents@1` 时返回 `403`；使用相同键的重复请求返回 `202` 以及
相同的 `operation_id`。

### `POST /evolution/v1/runs/{run}/evaluations:train`

`ctx.evaluate.start_train(workspace, idempotency_key)`。取代
ClawEvolve 自己的训练 bench（`bench-full-opt`）。仅限训练划分。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_42", "idempotency_key": "run_7f3/round-2/train"}
```

响应（`202`）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_19b", "status": "queued"}
```

### `GET /evolution/v1/runs/{run}/operations/{id}`

`ctx.operations.get(op_id)`；SDK 的 `operations.wait` 会重复这个简短的
查询。状态值：`queued | running | succeeded | failed | cancelled`。

请求：`GET /evolution/v1/runs/run_7f3/operations/op_19b`

响应（一次成功的训练评估；分数和点评是
反思型策略所需的反馈）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "op_19b",
  "status": "succeeded",
  "result": {
    "score": 0.82,
    "cases": [
      {"case_id": "case_501", "score": 1.0, "critique": "Offered a partial refund correctly"},
      {"case_id": "case_503", "score": 0.4, "critique": "Did not ask for the order id before refunding"}
    ],
    "cost": {"usd": "1.20", "rollouts": 36}
  }
}
```

成功的智能体操作则返回一个 `AgentResult`，例如
`{"exit_status": "ok", "transcript_artifact": "art_77"}`；它修改的文件
保留在工作区中，直到策略将其转为补丁。

### `POST /evolution/v1/runs/{run}/models:complete`

`ctx.models.complete(messages, model, max_tokens)`。consolidate-memory 用它
将反馈聚类为经验教训。费用计入预算；所用模型
记录在实验记录中。省略 `model` 时，使用平台默认
模型。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "messages": [
    {"role": "system", "content": "Group the feedback into lessons. Answer as JSON: [{key, text, tags, sources, action}]."},
    {"role": "user", "content": "fb_301 correction: partial refunds are allowed\nfb_305 rating -1 on ep_97\n…"}
  ],
  "max_tokens": 2048
}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "text": "[{\"key\": \"partial-refunds-allowed\", \"text\": \"Partial refunds are allowed for paid orders.\", \"tags\": [\"billing\"], \"sources\": [\"fb_301\", \"fb_305\"], \"action\": \"add\"}]",
  "model": "platform-default",
  "usage": {"input_tokens": 912, "output_tokens": 64},
  "charged_usd": "0.01"
}
```

主要错误：运行预算耗尽时调用被拒绝，
SDK 抛出 `BudgetExhausted`（状态码定义于
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。

### `POST /evolution/v1/runs/{run}/candidates`

`ctx.candidates.submit(candidate)`。幂等：候选 id 是
补丁的内容哈希，因此重新提交会返回相同的 id。

请求（来自 ClawEvolve 第 2 轮）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch": {"patch_schema": 1, "base": "sha256:a90b…",
            "ops": [{"op": "skill.update", "name": "refund-policy",
                     "file_ops": [{"kind": "unified_diff", "path": "SKILL.md", "diff": "@@ -3,1 +3,1 @@ …"}]},
                    {"op": "file.edit", "target": "persona/SOUL.md",
                     "edits": [{"kind": "replace_section", "heading": "## Escalation", "content": "…"}]}]},
  "rationale": "Round 2: train 82% vs best 78%; fixes trigger misses on partial refunds",
  "evidence": ["finding:f_12", "episode:ep_91"],
  "self_metrics": {"train_score_pct": 82}       // shown to reviewers, never used for acceptance
}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"candidate_id": "sha256:c41e…", "created": true}
```

主要错误：补丁未通过 schema 校验、其 base 不是
当前修订版，或某个操作触及绑定 `allowed_genes` 之外的基因或
锁定基因时，返回 `422`。

### `GET /evolution/v1/runs/{run}/candidates/{id}`

`ctx.candidates.verdict(candidate_id)`。状态为 `pending | accept | reject |
inconclusive`，并附带**仅限聚合结果**的验证数据（即
[07-verification.zh-CN.md](07-verification.zh-CN.md) 中的 `StrategyVerdictView`）。ClawEvolve 读取
`revision`，以便在被接受的候选之上构建下一轮。

请求：`GET /evolution/v1/runs/run_7f3/candidates/sha256:c41e…`

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate": "sha256:c41e…",
  "status": "accept",
  "revision": "sha256:d7a2…",                   // r42, the candidate revision recorded by the Genome Registry
  "aggregates": {"validation": {"cases": 30, "seeds": 3, "mean_delta": 0.061, "ci": [0.028, 0.094]}},
  "reasons": ["validation_improved"]
}
```

### `POST /evolution/v1/jobs/{id}/complete`

以策略的 `RunSummary` 成功结束作业。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"rounds": 3, "candidates": ["sha256:c41e…", "sha256:e903…"], "findings": 4}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "completed"}
```

### `POST /evolution/v1/jobs/{id}/fail`

以失败结束作业。可重试的失败可以在相同的运行 id 下被重新派发
（不超过运行的尝试次数上限）；不可重试的失败会将运行
结束为 `failed`。manual-patch 在补丁 base 不是
父修订版时使用它。

请求：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "diagnose found no usable sessions in window", "retryable": false}
```

响应：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "failed"}
```

## 12. 示例

### 12.1 `platform/manual-patch`

```python
class ManualPatchStrategy(EvolutionStrategy):
    async def run(self, ctx: StrategyContext) -> RunSummary:
        params = ManualPatchParams.parse(ctx.params)          # validates against the params schema
        if params.patch.base != ctx.parent.id:
            raise NonRetryableRunError("patch base is not the run's parent revision")
        candidate_id = await ctx.candidates.submit(Candidate(
            patch=params.patch,
            rationale=params.patch.rationale,
            evidence=params.patch.evidence,
        ))                                                    # same patch → same id after a re-dispatch
        ctx.log.info("submitted", candidate=candidate_id)
        return RunSummary(candidates=[candidate_id])
```

端到端使用它的流水线（客户端 SDK，[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)）：

```python
from avernet_evolution import Client

c = Client.from_env()
run_id = c.runs.start(bot="bot_123", binding="bind_03",            # binding runs platform/manual-patch@1.0.0
                      params={"patch": patch}, idempotency_key="fix-refund-trigger-2026-10-09")
run = c.runs.get(run_id)                       # repeat until terminal
cand = run.candidates()[0]
report = cand.report()                         # diff + verification + gate decision (08-promotion.md)
```

### 12.2 `clawevolve/bot-evolution`（原生策略）

其内部实现保持不变（诊断逻辑、tune 提示词、变异算子库、
轮次循环）；只有边界迁移到上下文。与早先的
草图相比，此版本还会运行 review 智能体并填充运行摘要。

```python
class ClawEvolveStrategy(EvolutionStrategy):
    def __init__(self, store: RunStateStore[ClawEvolveState]) -> None:
        self.store = store                                            # its own storage, not the platform's

    async def run(self, ctx: StrategyContext) -> RunSummary:
        p = ClawEvolveParams(**ctx.params)
        state = await self.store.load(ctx.run_id)
        if state is None:                                             # first attempt
            episodes = await ctx.experience.sessions(days=p.window_days, limit=p.max_sessions)
            findings = diagnose(episodes)                             # clawevolve-diagnose, unchanged logic
            await ctx.evaluate.add_train_cases(plan_bench(findings))  # platform assigns splits
            state = ClawEvolveState(run_id=ctx.run_id, findings=findings, base=ctx.parent.id,
                                    next_round=0, best_train_pct=0, pending=None, history=[])
            await self.store.save(ctx.run_id, state)

        while state.next_round < p.max_rounds:
            if ctx.cancelled.is_set():
                break
            key = f"{ctx.run_id}/round-{state.next_round}"           # same keys after a re-dispatch
            if state.pending is None:
                ws = await ctx.workspace.materialise(state.base, key=key)   # sandbox, not the live bot
                tune = await ctx.agents.start("clawevolve-tune", workspace=ws,
                                              prompt=build_tune_prompt(state.findings, state.history),
                                              idempotency_key=f"{key}/tune")
                await ctx.operations.wait(tune)                       # short status lookups by id
                train_op = await ctx.evaluate.start_train(ws, idempotency_key=f"{key}/train")
                train = (await ctx.operations.wait(train_op)).result  # replaces its own bench step
                review = await ctx.agents.start("clawevolve-review", workspace=ws,
                                                prompt=build_review_prompt(state.findings, train),
                                                idempotency_key=f"{key}/review")
                await ctx.operations.wait(review)
                pct = round(train.score * 100)                        # TrainResult.score is 0..1
                if pct > state.best_train_pct:                        # its own submission filter
                    state.pending = await ctx.candidates.submit(Candidate(
                        patch=ws.to_patch(),
                        rationale=f"Round {state.next_round}: train {pct}%",
                        evidence=[f"finding:{f.finding_id}" for f in state.findings],
                        self_metrics={"train_score_pct": pct}))
                    state.best_train_pct = pct
                    state.history.append(RoundRecord(state.next_round, state.pending,
                                                     pct, "pending"))
                    await self.store.save(ctx.run_id, state)          # survives a crash from here on
            if state.pending is not None:
                verdict = await ctx.candidates.verdict(state.pending) # the platform decides
                if verdict.status == "pending":
                    await asyncio.sleep(p.poll_s)                     # counts against max_wall_clock_s
                    continue
                if verdict.status == "accept":
                    state.base = verdict.revision                     # next round builds on it
                state.history[-1].verdict = verdict.status
                state.pending = None
            state.next_round += 1
            await self.store.save(ctx.run_id, state)

        return RunSummary(rounds=state.next_round, findings=len(state.findings),
                          candidates=[r.candidate for r in state.history if r.candidate])
```

（顺序说明：在此草图中，review 在训练评估之后运行，
结合训练点评评审已编辑的沙箱。在现有流程中，review 智能体可能会
进一步编辑沙箱；如果是这样，训练评估必须在 review 之后
运行。确切的阶段顺序仍由 ClawEvolve 决定。）

### 12.3 ClawEvolve 黑盒适配器（迁移第 2 步）

在 skill 移植之前，适配器在沙箱的本地副本上调用现有脚本。
仅为草图：

```python
class ClawEvolveAdapter(EvolutionStrategy):
    async def run(self, ctx: StrategyContext) -> RunSummary:
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/adapter")
        local = await ws.checkout(tmp_dir(ctx.run_id))               # files by digest via content/{digest}
        await export_sessions_for_legacy(ctx, local.sessions_dir)    # ctx.experience.sessions → session-export/v1 files
        run_legacy_optimize(workspace=local.root, sessions=local.sessions_dir,
                            no_pack=True, no_deploy=True)            # existing skill scripts, paths re-pointed
        await ws.sync_from(local)                                    # write edits back into the sandbox
        cid = await ctx.candidates.submit(Candidate(patch=ws.to_patch(), rationale="legacy adapter run"))
        return RunSummary(candidates=[cid])
```

`checkout`、`sync_from` 和 `export_sessions_for_legacy` 是适配器的
辅助函数，不是 SDK 契约；在这一步中，旧脚本仍会在 worker 内调用 `openclaw agent
--local`，这正是第 3 步用 `ctx.agents.start` 替换它们的原因。

### 12.4 `platform/consolidate-memory`

```python
class ConsolidateMemoryStrategy(EvolutionStrategy):
    def __init__(self, store: RunStateStore[ConsolidationPlan]) -> None:
        self.store = store

    async def run(self, ctx: StrategyContext) -> RunSummary:
        p = ConsolidateMemoryParams(**ctx.params)
        plan = await self.store.load(ctx.run_id)                     # set by an earlier attempt?
        if plan is None:
            feedback = await ctx.experience.feedback(days=p.window_days)
            if len(feedback) < p.min_feedback:
                return RunSummary(candidates=[], skipped="not enough feedback")
            cited = {f.episode_id for f in feedback if f.episode_id}
            episodes = [e for e in await ctx.experience.sessions(days=p.window_days)
                        if e.episode_id in cited]                    # supporting evidence per lesson
            current = await load_memory_items(ctx.parent)            # items_digest → content by digest
            lessons = await cluster_lessons(ctx.models, feedback, episodes, current)   # models.complete, JSON answer parsed
            ops = to_memory_ops(lessons, current,
                                max_new=p.max_new_items,
                                retire_unused_days=p.retire_unused_days,
                                pins=ctx.parent.policy.pins)         # pinned items are never touched
            if not ops:
                return RunSummary(candidates=[], skipped="no new lessons")
            plan = ConsolidationPlan(run_id=ctx.run_id, base=ctx.parent.id, ops=ops,
                                     rationale=summarize(lessons),
                                     evidence=[s for l in lessons for s in l.sources])
            await self.store.save(ctx.run_id, plan)                  # before submit: re-dispatch resubmits this
        cid = await ctx.candidates.submit(Candidate(
            patch=GenomePatch(patch_schema=1, base=plan.base, ops=plan.ops,
                              rationale=plan.rationale, evidence=plan.evidence),
            rationale=plan.rationale, evidence=plan.evidence))
        return RunSummary(candidates=[cid], ops=Counter(op["op"] for op in plan.ops))
```

### 12.5 一次每晚的 ClawEvolve 运行，端到端

1. 绑定 `bind_01` 于 02:00 在 `bot_123` 上触发。平台以
   幂等键 `bind_01/2026-10-09T02:00:00Z` 提交运行，得到
   `run_7f3`；策略版本 `2.0.0`、params、预算（`max_usd: 20`）以及
   父修订版（`active` = `r41`，`sha256:a90b…`）被冻结。
2. ClawEvolve worker 领取该作业（§11，作业协议），被授予的
   能力为 `experience.sessions@1`、`agents@1`、`evaluate.train@1`。
3. 诊断读取 `r41` 最近 7 天的片段，发现 `f_12`，并
   提议 12 个训练用例；平台将其中一部分保持隐藏。
4. 第 0 轮：tune 智能体编辑沙箱 `ws_42`，训练得分 78%，
   策略提交 `sha256:c41e…`。
5. 验证（`platform/clawbench` 评分器，与 `r41` 成对比较，并包含
   回归和安全套件）返回 `accept`；门禁判定为 T2
   （persona + skill），因此该候选进入评审队列
   （[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。候选修订版 `r42`
   成为第 1 轮的基线。
6. worker 在第 1 轮中被终止。租约过期；该运行以
   `attempt = 2` 再次派发。ClawEvolve 重新加载其状态，
   以相同的键重复 `materialise` 和 `agents.start`，并拿回
   `ws_43` 以及已在运行中的 tune 操作。
7. 三轮之后作业完成。所有者批准 `r42`；
   晋升将 `active` 移到 `r42`。每一次提交，无论是否被接受，都
   记录在实验记录中，并附带策略版本和智能体定义
   摘要（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)）。

## 13. 交互

| 其他组件 / 服务 | 方向 | 流转内容 |
| --- | --- | --- |
| Strategy Registry（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)） | 默认策略 → 注册表 | 注册记录、ClawEvolve 智能体定义（上传后按摘要存储）、一致性测试结果 |
| 进化运行（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)） | 双向 | 作业与租约、冻结的 params/父修订版/预算、每一次 `ctx` 调用（ClawEvolve 使用作业协议，平台策略在进程内调用）、`RunSummary` |
| 经验（[02-experience.zh-CN.md](02-experience.zh-CN.md)） | 经验 → 默认策略 | 片段（ClawEvolve）、反馈及其引用的片段（consolidate-memory）；ClawEvolve 的会话导出代码作为 OpenClaw 提供方迁移到那里 |
| 验证（[07-verification.zh-CN.md](07-verification.zh-CN.md)） | 双向 | 训练用例和训练评估（来自 ClawEvolve）；判定聚合结果（发给所有策略）；从 ClawEvolve 中抽取的 ClawBench 评分器、Bench store 模型、划分规则和门禁函数 |
| 晋升（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)） | 候选 → 门禁 | 候选进入门禁和评审队列；ClawEvolve 的 `skill-decision` 批准由评审队列取代 |
| 基因组（[01-genome.zh-CN.md](01-genome.zh-CN.md)） | 双向 | 父修订版和按摘要获取的内容（输入）；基因组补丁（输出）；迁移第 1 步中影子记录的修订版 |
| 实验记录（[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)） | 运行 → 实验记录 | 每次提交，附带策略版本、智能体定义摘要、所用模型、成本 |
| 元进化（[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)） | 以后 | ClawEvolve 的提示词、算子库和提交过滤器阈值是第一个要进化的机制；门禁校准/回放工具为机制验证提供初始基础 |
| 进化 API（[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)） | 调用方 → 默认策略 | 启动 manual-patch 运行；绑定默认进化策略；为 ClawEvolve 执行 `avn strategy publish` |
| 引擎适配器（OpenClaw） | 为默认策略提供提供方 | `experience.sessions` 提供方（会话导出）、`agents@1` 提供方（在沙箱中运行 ClawEvolve 的智能体）、供 consolidate-memory 使用的记忆投影（RSI-05） |
| ClawWeb / AgentEvolve UI | 过渡期 | 迁移期间（第 2 步）将平台运行与旧任务并列展示；在 DS-1 决定之前可能承载默认策略运行器 |
| ClawInsight | → ClawEvolve 绑定 | 事件触发器；`plan-source/v2` 发现作为反馈输入（以后的版本） |

## 14. 待定决策

- **DS-1（原 D-2）：默认策略运行器的长期宿主。** 是保留 TS
  ClawWeb 控制面作为默认策略运行器的长期*宿主*，
  还是将运行器移植到 Python 策略 SDK？建议：保持
  skill 使用 Python（已经是仅依赖标准库的 Python），将其作为 job worker 运行；
  在迁移第 3 步之后退役 TS 编排代码。在 RSI-13 中决定。
- **DS-2（原 D-3）：ClawBench 作为平台默认评分器。** ClawBench 是
  平台的默认评分器，还是多个评分器之一？建议：
  作为平台默认（它已支持自动、评分细则评判和混合
  评分），同时保持评分器接口开放。在 RSI-11 中决定。
- **DS-3（原 D-4）：工作流 YAML（TaskGuard）作为基因组基因，还是独立
  制品？** 建议：作为独立制品，采用相同的修订版/引用（ref）
  模型；在接入工作流运行修复时再决定。
- **DS-4（原 D-5）：ClawMind 分析器。** 外部的 “ClawMind” `analyze` 处理器
  不在本仓库中。在接入工作流运行修复之前，必须引入或重新定义其契约。
  处于休眠状态的 `SingleRunAnalyzer` /
  `BatchRunAnalyzer` / `LessonExpireScheduler` 代码应当要么接入
  策略，要么移除。
- **DS-5：过渡期记忆绑定。** 在 RSI-05 之前，consolidate-memory
  写入一个 persona 文件（`LESSONS.md`），但其绑定只允许
  `memory`。可选方案：为过渡期绑定允许 `persona`（比所需范围
  更宽），或者如果基因组策略配置支持条目级目标，则让 `allowed_genes` 指定单个条目（例如
  `persona/LESSONS.md`）。
  建议：如果 [01-genome.zh-CN.md](01-genome.zh-CN.md) 能够表达条目级目标，
  则采用条目级目标。
- **DS-6：一个 ClawEvolve 策略还是三个。** `bot_evolution`、
  `skill_evolution` 和 `skill_hardening` 是作为三个已注册的策略，
  还是作为一个带流程参数的策略？源材料对两者都未作定论。
  建议：`skill_hardening` 作为独立策略（阶段不同、
  `allowed_genes` 不同）；`skill_evolution` 作为
  `clawevolve/bot-evolution` 的一个参数（阶段相同、目标更窄）。

已解决：params schema 在每条注册记录中声明为 `params_schema`，并由绑定检查
进行校验（§11，参数 schema；
[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）；consolidate-memory 同时声明了
`experience.feedback@1` 和 `experience.sessions@1`（§7.2）。
