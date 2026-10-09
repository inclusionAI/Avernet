# 默认进化策略——接入现有管线

> English version: [07-default-strategy.md](07-default-strategy.md)

> 状态：DRAFT（讨论稿）。说明 ClawEvolve 以及 `apps/evolverun/` 中其他自改进代
> 码如何成为平台默认的、可替换的进化策略。
> 证据与文件引用：[09-research.zh-CN.md §2.2](09-research.zh-CN.md#22-现有自改进管线)。

## 1. 现状

| 管线 | 位置 | 功能 | 定位 |
| --- | --- | --- | --- |
| **ClawEvolve bot/skill 进化** | `apps/evolverun/clawweb`（TS 控制面）+ `clawweb-skills/clawevolve-skills`（Python 阶段 skill） | 会话诊断 → 计划 + ClawBench 用例 → tune/review 轮次 → bench → 接纳（`test > baseline`）→ 打包 | **主默认进化策略** |
| **ClawEvolve skill 加固** | 同上，`skill_hardening` 流程 | 对单个 skill 的单阶段加固 | 第二个默认进化策略（skill 范围） |
| **Workflow-run 修复** | ClawWeb `routes/evolve*.ts`、`run-analysis/*` | 失败运行证据 → 诊断、教训、建议 → `suggestion_apply` 编辑 workflow YAML | 一个独立的进化策略；目标是 workflow 而非基因组——第 2 阶段 |
| **ClawInsight 改进** | `modules/clawinsight` | 监控 → 改进条目 → `plan-source/v2` → plan+optimize | ClawEvolve 绑定的一个事件触发，外加通过 `experience.feedback` 输入的 `plan-source/v2` |
| **TaskGuard 运行时修复** | `apps/evolverun/taskguard` | 运行中的守护/修复/重试 | 属于运行时韧性，*不是*进化；其运行证据流入经验库 |
| **Evolvetrace** | `apps/evolverun/evolvetrace` | 运行可观测性；进化标签页是 mock | 将来可能作为归档/谱系 UI |

好消息是：ClawEvolve 已经具备大部分正确的接缝——带 JSON Schema 的阶段契约目
录、`preprocess | postprocess | replace` 扩展、带版本的自定义阶段 skill、
claim/report 步骤协议、与生产者无关的 `plan-source/v2` 交接、带评审防火墙的训练
集/验证集分离，以及离线门禁校准。接入工作主要是**将这些接缝重新指向平台契约**，
而不是重写。

## 2. ClawEvolve 作为黑盒策略

ClawEvolve 通过唯一的策略端口（[05-strategy-sdk.zh-CN.md](05-strategy-sdk.zh-CN.md)）
以**黑盒**方式接入：它保留自己的内部实现（诊断逻辑、tune 与 review 智能体、
提示词、变异算子库、轮次循环），只有它的边界部分迁移到 `StrategyContext` 上。
代码草图见 [05-strategy-sdk.zh-CN.md §10](05-strategy-sdk.zh-CN.md#10-示例)。

注册记录：`needs` = `experience.sessions@1`、
带有定义 `clawevolve-tune` 和 `clawevolve-review` 的 `agents@1`
（引擎为 `openclaw`，随策略一起交付和上传，见
[05-strategy-sdk.zh-CN.md §4.2](05-strategy-sdk.zh-CN.md#42-智能体定义从何而来)）、
`evaluate.train@1`。

| ClawEvolve 组件 | 在新模型中 | 所需变更 |
| --- | --- | --- |
| `acquisition/discovery.py`、`sessions.py`、`service_export.py` | OpenClaw 的 `experience.sessions` 提供方（平台侧） | 移到引擎 session-export 契约之后；归一化为 `Episode`；打上基因组修订版标签 |
| `clawevolve-diagnose` | 位于策略内部 | 通过 `ctx.experience.sessions()` 而非磁盘读取片段 |
| `clawevolve-plan`（bench 用例） | 位于策略内部 | 通过 `ctx.evaluate.add_train_cases()` 添加用例；由平台分配划分（移除其自有的 80/20 划分权） |
| `clawevolve-tune` + `clawevolve-review` | 位于策略内部 | **编辑由 `ctx.workspace.materialise()` 得到的沙箱，而不是线上工作区**；通过 `ctx.agents.start()` 以按 id 查询的长时操作运行智能体；提交 `ws.to_patch()` |
| 训练集 bench 运行（`bench-full-opt`） | `ctx.evaluate.start_train()`（一个按 id 查询的操作） | ClawBench 评分作为 `platform/clawbench` 迁入验证服务 |
| `action_accept`（`test > baseline`）+ 建议性门禁 | 决定提交什么的内部过滤 | 接受与否变为平台在绑定的验证配置下给出的判定；ClawEvolve 的门禁阈值可以作为更严格验证配置的初始值 |
| baseline-pack / restore / pack / deploy | —（移除） | 不再需要：策略从不修改线上 Bot |
| `ce_tasks` / `ce_steps` / claim-report | —（替换） | 平台 Run Orchestrator + 作业协议 |
| `EvolutionFlow` 注册表（3 个封闭 key） | 已注册的策略 | `bot_evolution`、`skill_evolution`、`skill_hardening` 成为三个已注册的进化策略（或一个带参数的策略） |
| 阶段扩展 + 上传的阶段 skill | 进化策略版本，或以后的组合层 | 替换某个阶段成为一个新的策略版本；一旦步骤类型存在，也可以是一个组合层步骤 |
| `skill-decision` 人工审批 + `BotSkillGateway.replaceLocalSkill`（CAS） | 平台评审队列 + 晋升 | 人工审批推广到所有 T2 补丁 |

## 3. 与 OpenClaw 解耦

发现的耦合点及其消除方式：

| 耦合点 | 替换为 |
| --- | --- |
| 硬编码的 `/home/admin/.openclaw/workspace` 和 `~/.openclaw/agents/*/sessions` | 编辑使用 `ctx.workspace`（物化的基因组）；会话使用 `ctx.experience.sessions()` |
| 用 `openclaw agent --local --agent …` 运行 tune/review/judge/bench | `ctx.agents.start()`（`agents` 能力；一个按 id 查询的操作），先提供 OpenClaw 提供方；bench 执行迁移到验证服务中的评测 Bot |
| OpenClaw 的 md 约定（SOUL/AGENTS/TOOLS、`skills/skills-local`、`config/mcporter.json`） | 基因组的基因（`persona`、`skills`、`tools.mcp`）；路径由引擎投影负责 |
| `singlebox/bot-runtime.ts` 中的 `active_engine='openclaw'`、`bot_type='personal'` 过滤 | 绑定检查：Bot 的引擎必须为 `needs` 中的每一项提供提供方 |
| 直接 SQLite 读取 Backend 表（`ac_bots`，…） | Genome Registry / Backend API |
| `local-execution.ts` 中的 `OPENCLAW_*` 环境变量 | 仅在配置加载中使用（R：原始环境变量访问只在 config/bootstrap 中） |

此后，OpenClaw 之外的引擎只需提供能力提供方（会话导出、智能体执行器）和引擎
投影支持，而无需 fork ClawEvolve。

## 4. 迁移计划（绞杀者模式，不做一次性切换）

1. **影子记录（不改变行为）。** ClawEvolve 照常运行，但每个被接纳的轮次还会通过
   Genome API 记录一个基因组修订版（来自其打包产物）。以真实产出验证基因组模型。
2. **黑盒适配器。** 将 `clawevolve/bot-evolution@1` 注册为一个作业 worker 策略，
   其 `run(ctx)` 调用现有 skill 脚本，路径指向一个物化的沙箱，并提交得到的补丁。
   由平台编排、验证和晋升。AgentEvolve UI 将平台运行与遗留任务并列展示。
3. **原生进化策略。** skill 直接通过策略 SDK 读写；从流程中移除 pack/restore；遗
   留任务类型被弃用。
4. **第二、第三个默认策略。** `skill_hardening`、ClawInsight 触发器、workflow-run
   修复（待 workflow 能表示为一个基因组基因或其自身的制品之后——见下文待决事项
   D-4）。

每一步都可独立交付且可回退。

## 5. 第二个非 ClawEvolve 默认策略：记忆整合

为证明可插拔性（R19 需要两个例子），平台应尽早发布一个刻意不同的进化策略：
**`platform/consolidate-memory`**，参照 Anthropic Dreams / OpenClaw Dreaming /
Hermes Curator 建模。

- **注册：** `needs` = 仅 `experience.feedback@1`（来自收件箱的观察、评分、
  纠正）。
- **绑定：** 每周定时，或在出现 N 条新观察后由事件触发；
  `allowed_genes: ["memory"]`。
- **`run(ctx)`：** 将近期的观察聚类为候选教训，并提交一个由逐项
  `memory.add/update/retire` 操作组成的补丁（N 天未使用的 skill 会被归档，从不
  删除）。
- **验证：** 使用一个包含回归集和矛盾检查的验证配置；T1 记忆条目自动晋升，任何
  影响人设的内容进入评审。

它覆盖了不同的基因、不同的触发器以及快循环收件箱，这正是该抽象必须能处理的情况。

## 6. 待决事项

- **D-2：** 将 TS ClawWeb 控制面长期保留为默认进化策略执行器的*宿主*，还是把执行
  器移植到 Python 策略 SDK？建议：skill 保持 Python（它们已经是仅依赖标准库的
  Python），作为作业 worker 运行；在第 3 步之后退役 TS 编排代码。
- **D-3：** ClawBench 是平台的默认评分器，还是众多评分器之一？建议：作为平台默认
  （它已支持自动化、评分细则评审和混合评分），同时保持评分器接口开放。
- **D-4：** Workflow YAML（TaskGuard）作为一个基因组基因，还是一个独立制品？建
  议：作为独立制品，采用相同的修订版/引用模型；在接入 workflow-run 修复时再定。
- **D-5：** 外部的「ClawMind」`analyze` 处理器不在本仓库中。在接入 workflow-run
  修复之前，必须引入或重新定义其契约。处于休眠状态的 `SingleRunAnalyzer` /
  `BatchRunAnalyzer` / `LessonExpireScheduler` 代码应当要么接入进化策略，要么删除。
