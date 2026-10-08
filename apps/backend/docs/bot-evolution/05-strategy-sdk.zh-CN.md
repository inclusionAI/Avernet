# 进化策略 SDK——让进化可插拔

> English version: [05-strategy-sdk.md](05-strategy-sdk.md)

> 状态：DRAFT（讨论稿）。说明一个团队如何在不修改平台代码的前提下，将一种
> 进化方法（当前是 ClawEvolve，以后还有其他方法）接入平台。

## 1. 原则

1. **一个端口。** 平台只认识一个策略接口，它只有一个方法 `run(ctx)`。
   ClawEvolve、其他团队的优化器以及平台自己的组合策略，都实现同一个端口。
2. **只有一扇门通向外部。** 策略只能通过交给它的上下文接触平台。它不能晋升、
   不能读取封存的测试、不能触碰线上 Bot，也不能读取平台存储。因此无论策略是
   什么，隔离与预算都在同一处强制执行。（**预算**是在 Bot 的绑定中设置的每次
   运行的支出上限：以美元计的模型开销、挂钟时间以及评估 rollout 次数。每一次
   模型调用和评估都通过 `ctx.budget` 计费，预算耗尽时运行即停止。）
3. **策略提议；平台决定。** 策略提交候选。记录、验证、门禁和晋升始终归平台
   所有（DR-2）。
4. **关于代码的事实被注册；关于 Bot 的选择被配置。** 有些信息对一个策略版本
   而言是成立的，无论哪个 Bot 使用它。例如，「ClawEvolve 2.0 驱动 OpenClaw
   智能体并读取对话历史」。这类信息在该版本注册时记录一次（§3）。另一些信息是
   针对某一个 Bot 的决定。例如，「bot_123 每晚运行 ClawEvolve，可以修改人设和
   技能，预算为 20 美元」。这类信息存放在该 Bot 的绑定中（§5），所有者可以随时
   修改，而无需触碰策略。
5. **概念少，且只定义一次。** 下文每个术语只有一个定义；没有字段重复表达另一个
   字段（例如，引擎兼容性由 `needs` 推导，而不单独声明）。

![策略端口与上下文](images/strategy-port.zh-CN.svg)

## 2. 领域模型

| 概念 | 是什么 | 所有者 | 何时变化 |
| --- | --- | --- | --- |
| **策略（Strategy）** | 实现 `run(ctx)` 的代码，加上它的注册记录（§3） | 策略作者 | 新的策略版本 |
| **能力（Capability）** | 策略可使用的上下文中一个具名、带版本的部分，来自平台所有的能力目录（§4） | 平台 | 平台契约变更 |
| **绑定（Binding）** | Bot 进化策略配置（evolution policy）中的一个条目：用哪个策略、何时运行、可以改什么、如何验证、预算、参数（§5） | Bot 所有者 / 租户管理员 | 任何时候 |
| **运行（Run）** | 一个绑定的一次执行，策略版本、参数、父版本和预算在开始时冻结；提交时返回的运行 id 是它唯一的句柄（§7） | 平台 | — |
| **StrategyContext** | 运行通向平台的唯一一扇门：始终授予的部分，加上策略所需的能力（§6） | 平台 | — |
| **候选 → 判定** | 策略提交的一个基因组补丁，以及平台对它的验证结果（§6） | 策略 → 平台 | — |

## 3. 策略：代码加注册记录

接口只有一个方法：

```python
class EvolutionStrategy(Protocol):
    async def run(self, ctx: StrategyContext) -> RunSummary: ...
```

**注册**一个策略版本，就是告诉平台它的存在：它的代码在哪里运行（`runtime`），
以及它需要哪些能力（`needs`）。只有已注册且通过一致性测试套件（§11）的版本才能
绑定到 Bot。注册会产生下面的记录，存入策略注册表（Strategy Registry，C3）。它是
数据而不是方法，因为平台需要在不运行策略代码的情况下读取它（例如，在作业 worker
容器尚不存在时）：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {                                       // capabilities from the catalog (§4), with arguments
    "experience.sessions@1": {},
    "agents@1": {"engines": ["openclaw"]},         // also implies which bots it can run on
    "evaluate.train@1": {}
  }
}
```

不存在单独的 `supports_engines`：绑定检查从 `needs` 推导引擎兼容性（§5）。

## 4. 能力目录

平台拥有一个小型、封闭、带版本的能力目录。每个条目对应 `StrategyContext` 的
一个部分，并且是一份契约：方法签名、数据模式、语义和一致性测试（R25）。策略只能
声明目录中的名称。每个条目都有**按引擎划分的提供方**（例如，引擎适配器的会话
导出为 OpenClaw 提供 `experience.sessions`），绑定检查正是据此得知一个 Bot 能
提供什么。

| 能力 | 上下文部分 | 提供什么 | 备注 |
| --- | --- | --- | --- |
| *（始终授予）* | `parent`、`workspace`、`submit`、`budget`、`log`、`artifacts`、`cancelled` | 读取父修订版；将修订版物化到沙箱，并将差异转回补丁；提交候选；预算、日志、产物、取消 | 无需声明 |
| `experience.sessions@1` | `ctx.experience.sessions()` | Bot 过去的对话，归一化为片段（episode），并经过过滤 | 读取对话历史；向所有者展示 |
| `experience.feedback@1` | `ctx.experience.feedback()` | 收件箱中的评分、纠正、结果以及被测 Bot 的观察 | |
| `agents@1` `{engines}` | `ctx.agents.run(engine, …)` | 在沙箱工作区中运行引擎智能体 | 引擎列表必须包含该 Bot 的引擎 |
| `evaluate.train@1` | `ctx.evaluate.train(…)`、`ctx.evaluate.add_train_cases(…)` | 仅在**训练集**上进行平台评估，返回分数与评语；添加训练用例 | 验证集、封存集、回归集和安全集保持隐藏 |

**`@1` 的含义。** `@` 后面的数字是*能力契约*（其方法和数据结构）的版本，而不是
策略的版本。策略会声明它是针对哪个契约版本编写的。如果平台之后以不兼容的方式修改
`experience.sessions`（例如采用不同的片段（episode）格式），它会发布
`experience.sessions@2`，并继续向针对 `@1` 注册的策略提供 `@1`。新增条目或版本
是一项经评审的平台变更。

### 4.1 能力是什么样子的

每个能力都是上下文上一个小型、带类型的 API。两个例子：

```python
# experience.sessions@1
async def sessions(self, *, days: int, limit: int = 500,
                   revision: str | None = None) -> list[Episode]: ...
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// One Episode returned by ctx.experience.sessions(days=7)
{
  "episode_id": "ep_91",
  "revision_id": "sha256:a90b…",            // the genome revision the bot was running
  "started_at": "2026-10-07T09:12:00Z",
  "turns": [
    {"role": "user", "text": "Can I get a refund for half of my order?"},
    {"role": "assistant", "text": "…", "tool_calls": [{"name": "order_lookup", "args": {"id": "A17"}}]},
    {"role": "tool", "name": "order_lookup", "result": "…"}
  ],
  "outcome": {"status": "user_corrected", "feedback": "partial refunds are allowed"},
  "redactions": ["email", "phone"]           // personal data removed before the strategy sees it
}
```

```python
# agents@1 — registered as {"agents@1": {"engines": ["openclaw"]}}
async def run(self, engine: str, *, agent: str, workspace: Workspace,
              prompt: str, timeout_s: int = 1800) -> AgentResult: ...
```

`engines` 列出该策略可以驱动的引擎。使用任何其他引擎的调用都会被拒绝，绑定到
引擎不在列表中的 Bot 也会被拒绝（§5）。例如，ClawEvolve 的调优步骤会调用
`ctx.agents.run("openclaw", agent="clawevolve-tune", workspace=ws, prompt=…)`。
平台在沙箱 `ws` 内启动该智能体，而不是在线上 Bot 上，并返回它的对话记录和退出
状态。它修改的文件留在 `ws` 中，直到策略把它们转成补丁。

## 5. 绑定：Bot 使用哪些策略

Bot 的进化策略配置（evolution policy）是一个绑定列表。不同 Bot 使用不同的策略，
一个 Bot 也可以使用多个策略：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Evolution policy of bot_123 (OpenClaw support bot)
{
  "bindings": [
    {
      "strategy": "clawevolve/bot-evolution@2.0.0",
      "trigger": {"schedule": "0 2 * * *"},           // or {"manual": true}, {"event": "failure_rate_alert"}
      "parent": "active",                             // which revision runs start from
      "allowed_genes": ["persona", "skills"],         // what this strategy may change on THIS bot
      "verification_profile": "default@1",            // owners may pick a stricter one, never a looser one
      "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
      "params": {"window_days": 7, "max_rounds": 3}   // validated by the strategy
    },
    {
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

绑定被创建或修改时，平台会对照该 Bot 进行检查。`needs` 中的每个能力都必须有
面向该 Bot 引擎的提供方；`agents@1` 必须列出该 Bot 的引擎；`allowed_genes` 必须
保持在该 Bot 的 `policy` 范围内（锁定的基因保持锁定）。不匹配会在配置时被拒绝，
而不是在一次付费运行进行到一半时才暴露。

触发、父版本选择、允许的基因和验证严格程度都是绑定字段，归 Bot 所有者所有。
它们不是策略代码。

## 6. StrategyContext、候选与判定

```python
class StrategyContext(Protocol):
    run_id: str; params: dict                       # frozen at run start
    parent: GenomeRevisionView                      # read-only: spec, files by digest, lineage
    workspace: WorkspaceFactory                     # materialise(revision) → sandbox dir; ws.to_patch()
    budget: BudgetMeter                             # remaining(); charge(); raises BudgetExhausted
    log: RunLog; artifacts: ArtifactSink; cancelled: CancellationToken
    attempt: int                                    # 1 on first dispatch, +1 on each re-dispatch (§7)
    async def submit(self, c: Candidate) -> str: ...      # returns the candidate id
    async def verdict(self, candidate_id: str) -> Verdict: ...  # status lookup by id

    # present only if declared in `needs`; otherwise access raises CapabilityNotGranted
    experience: ExperienceQuery                     # experience.sessions@1 / experience.feedback@1
    agents: AgentRunner                             # agents@1
    evaluate: TrainEvaluator                        # evaluate.train@1
```

- **候选** = 针对某个基础修订版的基因组补丁、理由、证据 id，以及可选的自报指标
  （向评审者展示，绝不用于接受判断）。
- `submit` 记录候选并立即返回它的**候选 id**；它不等待验证。它是幂等的：候选
  id 是补丁的内容哈希，因此重试提交（包括重新派发后重复的提交，§7）会返回相同
  的 id，不会产生重复。
- **判定** = 按候选 id 查询的结果：状态为 `pending`、`accept`、`reject` 或
  `inconclusive`，只附带验证**汇总值**，绝不包含逐用例的隐藏数据。id 是唯一的
  句柄；没有回调，也没有阻塞调用。
- ClawEvolve 这类多轮策略会在上一个被接受的候选之上构建下一轮，它会按 id 查询
  判定，直到状态不再是 `pending`。在此期间运行保持 `running`，这段时间计入绑定
  的 `max_wall_clock_s`。
- 提交什么由策略自己选择（由它的启发式决定什么值得提交）。候选是否被接受由平台
  选择：先按绑定的验证配置进行验证，再经过门禁和风险等级
  （[08-governance.zh-CN.md §2](08-governance.zh-CN.md#2-门禁)）。

## 7. 运行生命周期

```text
queued → running → completed | failed | cancelled | budget_exhausted
```

- **开始：** 编排器冻结策略版本、参数、父版本和预算；以恰好被授予的能力构建
  上下文；并预留预算。
- **进行中：** 每次模型调用和评估都计入预算。
- **结束：** 在失败、取消或预算耗尽之前做出的提交会被保留，并仍然接受验证。
  每个提交，无论被接受还是被拒绝，都会连同策略版本一起记录到实验记录 H 中。

### 7.1 按 id 提交与查询状态

启动一次运行（触发条件满足，或调用方通过 Evolution API，
[06-interfaces.zh-CN.md](06-interfaces.zh-CN.md)）会返回一个**运行 id**。提交
是幂等的，并由平台保证：调用方发送一个幂等键，使用相同键的重复提交会返回同一个
运行 id，而不会启动第二次运行。此后，运行 id 是唯一的句柄：调用方通过它查询
状态、提交和判定。下一层同样如此：策略的 `ctx.submit` 返回一个候选 id，判定
通过该 id 查询（§6）。

### 7.2 崩溃与重启

工作在平台和策略之间划分：

| 关注点 | 所有者 | 方式 |
| --- | --- | --- |
| 运行记录、其冻结的输入、已花费的预算和已提交的候选 | 平台 | 在 `submit` 或运行提交返回之前持久化 |
| 发现运行的进程已死亡 | 平台 | 每次运行都是一个**带租约的作业**。worker（或进程内宿主）续租；租约到期时（进程崩溃、硬件故障、重启），作业回到 `queued`，并以相同的运行 id 和 `ctx.attempt + 1` 重新派发。fencing token 会拒绝旧持有者的调用。超过 `max_attempts` 后，运行以 `failed` 结束 |
| 策略自身的进度（轮次编号、搜索状态、历史） | 策略 | 策略把所需的任何内容持久化到**自己的存储**中，以运行 id 为键，并在重新派发时重新加载并继续。平台不提供检查点 API，也从不读取这部分状态；它的形态因策略而异 |

重新派发的运行使用相同的冻结输入和相同的预算：之前各次尝试花掉的预算不会退回。
由于 `submit` 是幂等的，策略在崩溃前已提交过的候选如果再次提交，会得到相同的
候选 id。

## 8. 同一端口上的两个层级

| 层级 | 团队编写什么 | 何时使用 |
| --- | --- | --- |
| **黑盒** | 一个完整策略：`run(ctx)`，并向平台注册（§3） | 已有自带内循环的引擎（ClawEvolve、GEPA 风格优化器、编码智能体循环）。这是默认的接入方式，也是第一轮迭代中唯一的层级 |
| **组合** *（之后）* | 一个接入 `platform/composed` 的小步骤 | 复用现有策略的大部分，只替换其中一块 |

组合层是为那些拥有更好的*某一块*、而不是完整策略的团队准备的。`platform/composed`
本身就是平台提供的一个普通策略。它的参数列出一系列小步骤，并依次调用每个步骤。
例如，假设某个团队有一种更好的方法来找出失败的根因，但没有自己的调优循环。它不必
编写完整的策略，只需编写一个「分析」步骤。然后，一个绑定会以如下参数运行
`platform/composed`：
`{"steps": ["team-x/root-cause-analyzer@1", "clawevolve/tune@2"]}`，从而复用
ClawEvolve 的调优。对编排器而言，这只是另一个策略。

步骤类型（分析、提议等）只有在第二个团队确实需要替换某一块时才会定义（R19：有
两个例子后再抽象）。在那之前，ClawEvolve 和其他策略都以黑盒方式接入。

## 9. 运行时

| `runtime.kind` | 如何运行 | `ctx` 如何到达 |
| --- | --- | --- |
| `in_process` | 由 `apps/evolution` 组合根加载的 Python 包，通过配置选择（R5/R14） | 直接的 Python 对象 |
| `job_worker` | 容器镜像（任意语言） | 下面的作业协议：每个 `ctx` 调用对应一个 HTTP 端点 |

### 作业协议

```text
POST /evolution/v1/jobs:claim                       {worker_id, strategy_ids[]} → job {run_id, attempt, params, parent, budget, granted, fencing_token}
POST /evolution/v1/jobs/{id}/heartbeat              lease renewal; an expired lease re-queues the job (§7.2)
GET  /evolution/v1/runs/{run}/parent                ctx.parent
GET  /evolution/v1/runs/{run}/content/{digest}      file bytes of the parent / workspace
GET  /evolution/v1/runs/{run}/experience/sessions   ctx.experience.sessions   (if granted)
GET  /evolution/v1/runs/{run}/experience/feedback   ctx.experience.feedback   (if granted)
POST /evolution/v1/runs/{run}/agents:run            ctx.agents.run            (if granted)
POST /evolution/v1/runs/{run}/evaluations:train     ctx.evaluate.train        (if granted)
POST /evolution/v1/runs/{run}/candidates            ctx.submit → {candidate_id}   (idempotent)
GET  /evolution/v1/runs/{run}/candidates/{id}       ctx.verdict → {status, aggregates}
POST /evolution/v1/runs/{run}/budget:charge         ctx.budget.charge
POST /evolution/v1/jobs/{id}/complete | /fail       RunSummary | {reason, retryable}
```

所有载荷都是带 JSON Schema 的 JSON。worker 不会获得任何访问 Bot 的凭证；未被
授予的能力所对应的端点返回 `403`；携带过期 fencing token 的调用返回 `409`。

worker 是由平台运行的容器。以 Bot 充当 worker（「执行者 Bot」）随 DR-3 一起已推迟
（[decisions/0003](decisions/0003-bot-principal-for-evolution-surface.zh-CN.md)）。

## 10. 示例

**ClawEvolve 作为黑盒策略。** 它的内部保持不变：诊断逻辑、调优提示词、变异算子
库、轮次循环。只有边界部分迁移到上下文上。

```python
class ClawEvolveStrategy(EvolutionStrategy):
    async def run(self, ctx):
        state = await self.store.load(ctx.run_id)                        # its own storage, not the platform's
        if state is None:                                                # first attempt
            findings = diagnose(await ctx.experience.sessions(days=ctx.params["window_days"]))
            await ctx.evaluate.add_train_cases(plan_bench(findings))      # platform assigns splits
            state = State(findings=findings, base=ctx.parent.id, next_round=0)
            await self.store.save(ctx.run_id, state)
        while state.next_round < ctx.params["max_rounds"]:
            if state.pending is None:
                ws = await ctx.workspace.materialise(state.base)         # sandbox, not the live bot
                await ctx.agents.run("openclaw", agent="clawevolve-tune", workspace=ws,
                                     prompt=build_tune_prompt(state.findings, state.history))
                train = await ctx.evaluate.train(ws)                     # replaces its own bench step
                if train.score > state.best_train:                       # its own heuristic
                    state.pending = await ctx.submit(Candidate(patch=ws.to_patch(), rationale=...,
                                                               evidence=state.findings.ids))
                    await self.store.save(ctx.run_id, state)             # survives a crash from here on
            if state.pending is not None:
                verdict = await ctx.verdict(state.pending)               # the platform decides
                if verdict.status == "pending":
                    await asyncio.sleep(ctx.params["poll_s"]); continue
                if verdict.status == "accept":
                    state.base = verdict.revision                        # next round builds on it
                state.pending = None
            state.next_round += 1
            await self.store.save(ctx.run_id, state)
        return RunSummary(rounds=state.next_round)
```

**另一个团队用另一种语言编写的优化器。** 一个 TypeScript 编写的 GEPA 风格提示词
优化器以 `runtime.kind: "job_worker"` 和
`needs: {"evaluate.train@1": {}, "experience.feedback@1": {}}` 注册。它认领作业
并调用作业协议。它永远不需要知道修订版如何存储、验证如何进行、服务 Bot 如何发布。
它提交的一个候选：

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

## 11. SDK 与一致性测试

| 包 | 面向 | 内容 |
| --- | --- | --- |
| `avernet-evolution`（Python）、`@avernet/evolution`（TS） | 调用方：流水线、CI、UI 后端 | 为 Evolution API（绑定、运行、判定）生成的客户端 |
| `avernet-evolution-strategy`（先 Python，后 TS） | 策略作者 | `EvolutionStrategy` 基类、类型化模型、进程内与作业协议两种 `StrategyContext`、`WorkspaceFactory`（物化 / `to_patch`）、`AgentRunner`（先支持 OpenClaw）、带模拟平台的本地测试工具（`avn strategy dev`），以及一致性测试套件 |

一致性测试在端口两侧都要运行：

- **策略测试套件**（由作者运行；在某个版本可以在开发环境之外被绑定之前，也由
  C3 运行）：候选必须通过补丁模式和本次运行 `allowed_genes` 的校验；策略只使用
  被授予的能力；它会在取消和 `BudgetExhausted` 时停止；重复提交同一候选是幂等的；在运行中途杀掉策略并再次派发
  同一运行 id，既不会产生重复候选，也不会超出预算。
- **能力提供方**（由平台和引擎适配器运行）：每个目录条目针对每个引擎提供方都有
  一个契约测试，遵循 `docs/arch/protocol-contract-tests.md`。

## 12. 端口足够通用的证据

| 策略 | 形态 | 如何适配端口 |
| --- | --- | --- |
| ClawEvolve（`apps/evolverun`） | 多轮 调优 → 基准测试 → 评审 | 黑盒；`agents`、`experience.sessions`、`evaluate.train`；在轮次之间按候选 id 查询判定 |
| `platform/consolidate-memory`（[07-default-strategy.zh-CN.md §5](07-default-strategy.zh-CN.md#5-第二个非-clawevolve-默认策略记忆整合)） | 定时将观察整合为记忆条目 | 黑盒；仅 `experience.feedback`；每次运行一个提交 |
| GEPA / OPRO 风格优化器 | 带反思式变异的种群搜索 | 黑盒；用 `evaluate.train` 作为适应度；提交最佳候选 |
| 编码智能体策略（Meta-Harness 风格） | 智能体在完整历史下编辑文件 | 黑盒；`workspace` + `agents` |
