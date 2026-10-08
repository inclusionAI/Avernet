# 默认进化策略——接入现有管线

> English version: [default-strategy.md](default-strategy.md)

> 状态：DRAFT（讨论稿）。说明 ClawEvolve 以及 `apps/evolverun/` 中其他自改进代
> 码如何成为平台默认的、可替换的进化策略。
> 证据与文件引用：[research.zh-CN.md §2.2](research.zh-CN.md#22-现有自改进管线)。

## 1. 现状

| 管线 | 位置 | 功能 | 定位 |
| --- | --- | --- | --- |
| **ClawEvolve bot/skill 进化** | `apps/evolverun/clawweb`（TS 控制面）+ `clawweb-skills/clawevolve-skills`（Python 阶段 skill） | 会话诊断 → 计划 + ClawBench 用例 → tune/review 轮次 → bench → 接纳（`test > baseline`）→ 打包 | **主默认进化策略** |
| **ClawEvolve skill 加固** | 同上，`skill_hardening` 流程 | 对单个 skill 的单阶段加固 | 第二个默认进化策略（skill 范围） |
| **Workflow-run 修复** | ClawWeb `routes/evolve*.ts`、`run-analysis/*` | 失败运行证据 → 诊断、教训、建议 → `suggestion_apply` 编辑 workflow YAML | 一对 Analyzer + Proposer；目标是 workflow 而非基因组——第 2 阶段 |
| **ClawInsight 改进** | `modules/clawinsight` | 监控 → 改进条目 → `plan-source/v2` → plan+optimize | 一个为主进化策略供料的 **Trigger** + **Analyzer** |
| **TaskGuard 运行时修复** | `apps/evolverun/taskguard` | 运行中的守护/修复/重试 | 属于运行时韧性，*不是*进化；其运行证据是一个 **ExperienceSource** |
| **Evolvetrace** | `apps/evolverun/evolvetrace` | 运行可观测性；进化标签页是 mock | 将来可能作为归档/谱系 UI |

好消息是：ClawEvolve 已经具备大部分正确的接缝——带 JSON Schema 的阶段契约目
录、`preprocess | postprocess | replace` 扩展、带版本的自定义阶段 skill、
claim/report 步骤协议、与生产者无关的 `plan-source/v2` 交接、带评审防火墙的训练
集/验证集分离，以及离线门禁校准。接入工作主要是**将这些接缝重新指向平台契约**，
而不是重写。

## 2. 将 ClawEvolve 映射到插件种类

| ClawEvolve 组件 | 插件种类 | 所需变更 |
| --- | --- | --- |
| `acquisition/discovery.py`、`sessions.py`、`service_export.py` | **ExperienceSource**（OpenClaw） | 移到引擎 session-export 契约之后；归一化为 `Episode`；打上基因组修订版标签 |
| `clawevolve-diagnose`（评审模型、聚类、改写为可回放查询） | **Analyzer** | 从 C2 而非磁盘读取片段；输出 `plan-source/v2`（这已是它的输出） |
| `clawevolve-plan`（bench 模板、目标、spec） | **SuiteBuilder**（+ 产出策略私有 spec） | 将用例输出到 C5；由平台分配划分（移除其自有的 80/20 划分权） |
| `clawevolve-tune` | **Proposer** | **编辑沙箱工作区，而不是线上工作区**；通过 `GenomeWorkspace.to_patch()` 返回 `GenomePatch`，取代 `diff.patch` + `changed_files.txt` |
| `clawevolve-review`（防火墙、假设） | Proposer 的一部分（双 agent 提议器） | 保留防火墙；它可直接映射到作业协议的输入过滤 |
| `clawbench-base` 评分 | **Evaluator**（平台提供的默认实现） | 包装为 `platform/clawbench`；执行器通过 `eval_env` 在评测 Bot 中运行，取代 `openclaw agent --local` |
| `action_accept` + 可由环境变量调节的建议性门禁 | **AcceptancePolicy** `clawevolve/acceptance` | 将阈值从环境变量提升为策略参数；在其上叠加平台底线 |
| baseline-pack / restore / pack / deploy | —（移除） | 由基因组修订版、引用（ref）和平台晋升取代 |
| `ce_tasks` / `ce_steps` / claim-report | —（替换） | 平台 Run Orchestrator + 作业协议 |
| `EvolutionFlow` 注册表（3 个封闭 key） | 策略清单 | `bot_evolution`、`skill_evolution`、`skill_hardening` 成为三个已注册的进化策略 |
| 阶段扩展 + 上传的阶段 skill | 复用插件的进化策略版本 | `replace` = 替换某一步的插件；pre/post = 额外步骤 |
| `skill-decision` 人工审批 + `BotSkillGateway.replaceLocalSkill`（CAS） | 平台评审队列 + 晋升 | 人工审批推广到所有 T2 补丁 |

由此得到的默认进化策略即
[strategy-sdk.zh-CN.md §4](strategy-sdk.zh-CN.md#4-策略清单) 中的清单示例。

## 3. 与 OpenClaw 解耦

发现的耦合点及其消除方式：

| 耦合点 | 替换为 |
| --- | --- |
| 硬编码的 `/home/admin/.openclaw/workspace` 和 `~/.openclaw/agents/*/sessions` | 编辑使用 `GenomeWorkspace`（物化的基因组）；会话使用 ExperienceSource |
| 用 `openclaw agent --local --agent …` 运行 tune/review/judge/bench | 插件 SDK 内的 **AgentRunner** 抽象，先提供 OpenClaw 实现；bench 执行迁移到评测 Bot |
| OpenClaw 的 md 约定（SOUL/AGENTS/TOOLS、`skills/skills-local`、`config/mcporter.json`） | 基因组的基因（`persona`、`skills`、`tools.mcp`）；路径由引擎投影负责 |
| `singlebox/bot-runtime.ts` 中的 `active_engine='openclaw'`、`bot_type='personal'` 过滤 | 进化策略的 `applies_to.engines`，根据引擎能力校验 |
| 直接 SQLite 读取 Backend 表（`ac_bots`，…） | Genome Registry / Backend API |
| `local-execution.ts` 中的 `OPENCLAW_*` 环境变量 | 仅在配置加载中使用（R：原始环境变量访问只在 config/bootstrap 中） |

此后，OpenClaw 之外的引擎只需提供一个 ExperienceSource 和引擎投影支持，而无需
fork ClawEvolve。

## 4. 迁移计划（绞杀者模式，不做一次性切换）

1. **影子记录（不改变行为）。** ClawEvolve 照常运行，但每个被接纳的轮次还会通过
   Genome API 记录一个基因组修订版（来自其打包产物）。以真实产出验证基因组模型。
2. **适配器进化策略。** 注册 `clawevolve/bot-evolution@1`，其各步骤是轻薄的作业
   worker，调用现有 skill 脚本，路径指向一个 `GenomeWorkspace`。由平台编排；由平
   台晋升。AgentEvolve UI 将平台运行与遗留任务并列展示。
3. **原生进化策略。** skill 直接通过插件 SDK 读写；从流程中移除 pack/restore；遗
   留任务类型被弃用。
4. **第二、第三个默认策略。** `skill_hardening`、ClawInsight 触发器、workflow-run
   修复（待 workflow 能表示为一个基因组基因或其自身的制品之后——见下文待决事项
   D-4）。

每一步都可独立交付且可回退。

## 5. 第二个非 ClawEvolve 默认策略：记忆整合

为证明可插拔性（R19 需要两个例子），平台应尽早发布一个刻意不同的进化策略：
**`platform/consolidate-memory`**，参照 Anthropic Dreams / OpenClaw Dreaming /
Hermes Curator 建模。

- 触发器：定时 + 空闲；或收件箱中出现 N 条新观察。
- 分析器：将近期的观察和片段聚类为候选教训。
- 提议器：逐项的 `memory.add/update/retire` 操作，以及针对 N 天未使用的 skill 的
  `skill.update` → 归档（从不删除）。
- 评估器：仅用回归集（记忆变更不应造成回归）+ 一项矛盾检查。
- 接纳策略：无回归；T1 条目自动晋升，影响人设的条目进入评审。

它覆盖了不同的基因、不同的触发器以及快循环收件箱，这正是该抽象必须能处理的情况。

## 6. 待决事项

- **D-2：** 将 TS ClawWeb 控制面长期保留为默认进化策略执行器的*宿主*，还是把执行
  器移植到 Python 插件 SDK？建议：skill 保持 Python（它们已经是仅依赖标准库的
  Python），作为作业 worker 运行；在第 3 步之后退役 TS 编排代码。
- **D-3：** ClawBench 是平台的默认 Evaluator，还是众多评估器之一？建议：作为平台
  默认（它已支持自动化、评分细则评审和混合评分），同时保持 Evaluator 种类开放。
- **D-4：** Workflow YAML（TaskGuard）作为一个基因组基因，还是一个独立制品？建
  议：作为独立制品，采用相同的修订版/引用模型；在接入 workflow-run 修复时再定。
- **D-5：** 外部的「ClawMind」`analyze` 处理器不在本仓库中。在接入 workflow-run
  修复之前，必须引入或重新定义其契约。处于休眠状态的 `SingleRunAnalyzer` /
  `BatchRunAnalyzer` / `LessonExpireScheduler` 代码应当要么作为插件接入，要么删除。
