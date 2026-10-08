# 调研：业界调研与代码库证据

> English version: [09-research.md](09-research.md)

> 收集于 2026-10-08。标记为 **[V]** 的业界条目已在本次会话中对照抓取的页面或
> 搜索结果核实；**[K]** 为以标准 arXiv id 引用、未重新抓取的知名论文；**[U]**
> 无法核实，未经检查不得依赖。

## 1. 业界调研

### 1.1 外部优化器（离线进化 prompt / 程序）

| 系统 | 机制 | 对我们的启示 |
| --- | --- | --- |
| DSPy MIPROv2 [K] — arxiv.org/abs/2406.11695 | 针对某个指标，在 instruction × demo 上做贝叶斯搜索 | 把可调文本声明为带指标的参数；优化器可替换 |
| GEPA [V] — arxiv.org/abs/2507.19457, dspy.ai/api/optimizers/GEPA | 基于 trace 与*文本*反馈的反思式变异；在逐实例得分上维护 Pareto 前沿；据报告以少得多的 rollout 胜过 GRPO | 最适合进化 SKILL.md / 人设文本；评分器必须同时返回分数**和**评语；保留 Pareto 归档 |
| TextGrad [K] — arxiv.org/abs/2406.07496 | 在由文本变量构成的图中传播文本「梯度」 | 在人设 → skill → 工具描述之间做功劳分配 |
| OPRO [K] — arxiv.org/abs/2309.03409 | 以 LLM 作为优化器，基于排序后的（prompt, score）历史 | 简单的基线提议器 |
| Promptbreeder [K] — arxiv.org/abs/2309.16797 | 种群进化；变异 prompt 本身也会进化 | 提议器本身也可以进化（元层次）—— 后续再做 |
| Trace / OptoPrime [K] — arxiv.org/abs/2406.16218 | 执行 trace 图；可更新任意可训练节点 | trace 是反馈的单位 |
| Meta-Harness [V] — arxiv.org/abs/2603.28052 | 编码 agent 在文件系统上借助完整原始历史搜索 harness 代码 | 给提议器提供谱系 + 原始 trace，而非摘要 |
| StarHarness [V, abstract] — arxiv.org/abs/2608.24804 | 在 prompt、工具、skill、MCP、subagent 上做分层搜索 | 其搜索空间 ≈ 我们的基因组 |
| Rethinking harness-evolution evaluation [V] — arxiv.org/abs/2607.12227 | 自动 harness 进化往往无法胜过预算匹配的测试时扩展；在留出数据上泛化很差 | **强制要求**：留出集评估与预算匹配基线 |

### 1.2 Agent 自进化

| 系统 | 启示 |
| --- | --- |
| Reflexion [K] 2303.11366 | 失败后进行语言化自我反思 → 情景记忆（快循环） |
| ExpeL [K] 2308.10144 | 带 ADD/EDIT/UPVOTE/DOWNVOTE 的洞察列表 —— 带效用值的条目化记忆 |
| Agent Workflow Memory [K] 2409.07429 | 从轨迹中归纳可复用的工作流（≈ skill 草稿） |
| Dynamic Cheatsheet [K] 2504.07952 | 测试时的策展记忆 |
| ACE [V] 2510.04618 | Generator / Reflector / Curator；**条目化增量更新**避免了简短偏置与上下文坍缩 |
| Voyager [K] 2305.16291 | skill 库只接纳经过验证的 skill |
| SkillWeaver [V] 2504.07079 | 提议 → 合成 → 打磨 skill；skill 可从强 Bot 迁移到弱 Bot |
| Trace2Skill [V, abstract] 2603.25158 | 轨迹经验 → 通过并行补丁合并生成 SKILL.md |
| SkillAxe [V, abstract] 2606.10546 | LLM 编写的 skill ≈ 无增益；评估引导的精炼可以找回增益 |
| SKILL.md mining for CUAs [V, abstract] 2606.20363 | 挖掘出的 skill 可读但效果弱 —— 门禁比提取更重要 |
| ADAS [K] 2408.08435 | 元 agent + 设计归档 |
| Gödel Agent [K] 2410.04444 | 自指式运行时修改 —— 脆弱 |
| SICA [V] 2504.15228 | Agent 编辑自己的代码；归档；最优者成为元 agent |
| Darwin Gödel Machine [V] 2505.22954 | 归档树 + 偏向新颖性的父代选择；没有归档时增益坍缩；**观察到奖励投机（reward hacking）** |
| Huxley-Gödel Machine [V] 2510.21614 | 得分 ≠ 后代的可改进性；按分支（clade）元生产力选择 → 选择器必须可插拔且感知谱系 |
| AlphaEvolve [K] deepmind blog / 2506.13131 | LLM diff + 自动化评估器 + MAP-Elites/岛屿数据库 |
| Self-evolving agents survey [V] 2508.07407 | 框架：输入、agent 系统、环境、优化器 |

### 1.3 产品实践

| 产品 | 模式 | 启示 |
| --- | --- | --- |
| Anthropic Agent Skills [V] — anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills | 渐进式披露；开放的 SKILL.md 格式；带评估的 skill-creator | 我们的 skill 已采用这种格式；描述对触发很重要 |
| Anthropic memory tool [V] — platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool | 客户端文件记忆；路径穿越防护、大小上限 | 校验记忆路径；条目化 |
| Claude Managed Agents memory + Dreams [V] — platform.claude.com/docs/en/managed-agents/{memory,dreams} | 每次记忆变更都是一个不可变版本；Dreams 从会话写出一个**新**存储，输入保持不变，可采纳或丢弃 | 「提议新版本、门禁、晋升」的参考 |
| Claude Code memory [V via 3rd party; details U] | 人工编写的 CLAUDE.md + 自动记忆索引 | 拆分策展记忆与运行时记忆 |
| OpenAI trace grading, prompt optimizer, self-evolving cookbook [V via search] | trace 评分器、基于数据集的优化器、基线 → 评分器 → 优化器 → 重新部署 | 优化后的 prompt 可能在特定输入上退化 —— 需要回归集。**OpenAI Evals 平台将于 2026-11-30 关停** —— 不要依赖它 |
| LangMem / LangSmith [V] | 程序性记忆 = 基于轨迹 + 反馈的 prompt 优化；数据集/评估器/标注队列 | 多 prompt 优化器会选择改*哪个*组件 |
| Letta sleep-time agents [V] | 执行任务的 agent 没有记忆编辑工具；由后台 agent 编辑记忆 | 把执行与学习分离（我们的双速循环） |
| Mem0 [V via secondary] | 由 LLM 决定 ADD/UPDATE/DELETE/NOOP | 完全由 LLM 做门禁是一个弱点 |
| Hermes Agent (Nous) [V via doc snippets; triggers U] | Agent 自行编写 skill；Curator 只归档、从不删除；钉住的 skill 写保护；dry-run | 钉住、归档而非删除、dry-run —— 已采纳 |
| OpenClaw Dreaming [V] — docs.openclaw.ai/concepts/dreaming | 需主动开启的夜间 light/REM/deep 阶段；只以加权评分晋升到 MEMORY.md | 默认关闭、按评分晋升；人类可读的日记不是晋升来源 |

### 1.4 接口模式

| 模式 | 示例 | 我们的用法 |
| --- | --- | --- |
| A. 外部优化器 API/SDK | DSPy, TextGrad, LangMem, OpenAI optimizer, AlphaEvolve | 进化策略运行（慢循环） |
| B. Agent 可调用的自编辑工具 | Memory tool, Claude Code auto memory, Hermes `skill_manage`, Voyager | 被改进 Bot 的 CLI —— **只写入收件箱** |
| C. 后台整合器 | Letta sleep-time, Dreams, OpenClaw Dreaming, Hermes Curator, ACE Curator | `platform/consolidate-memory` 进化策略 |
| D. 自指式代码修改 | SICA, DGM, Gödel Agent | 不在 Bot 范围内；未来可能用于「进化策略进化进化策略」 |

共识：B 写入暂存层，A/C 提议版本，由平台所属的门禁执行晋升。这正是本设计。

### 1.5 未核实 / 已标记

Hermes 的 skill 创建触发计数；Hermes curator 是否会触碰内置 skill；Harvey
「~6x」的 Dreams 数据；Claude Code「AutoDream」；自动记忆的版本与大小限制；
OpenAI cookbook ↔ GEPA 的关联；ShinkaEvolve；LangMem 版本。上文中 2026 年的
论文仅从摘要/片段得知。

## 2. 代码库证据

### 2.1 Bot Config Manifest

文档：`apps/backend/docs/bot-config-manifest/`（zh-CN；`manifest-schema.zh-CN.md`
优先于 `design.zh-CN.md`）。代码：`apps/backend/src/agentclaw/community/core/bot_config_manifest/`。

- 顶层键 `schema_version`（仅为 1）、`sources`、`manifest`、`script`；
  未知键会被拒绝（`schema/validator.py:58`）。
- 类别：`mcp`、`resources`、`skills`、`identity`、`engine_config`
  （v1 中 PUT 时拒绝）、`cli_tools`（`capabilities.py:77-89`）。
- `MEMORY.md`、`IDENTITY.md` 为保留项：会被拒绝，从不写入或删除。
- 存储：每个 Bot 一行可变记录，`ac_bot_config_manifest`
  （`repository/models.py:64`）；没有修订版、哈希、父版本或 ETag
  （`services/config_manifest_service.py:141-184`）。
- apply 报告只追加（`repository/apply_models.py:90`），但 apply 会重新读取
  当前文档；报告记录的是解析后的 git SHA，而不是所应用的文档
  （`services/config_manifest_apply_service.py:846-875`）。
- 内容寻址的 blob 来源记录：`content/models.py:94`（`ac_manifest_content`）。
- 收敛：按类别替换，类别级原子性；apply 顺序与触发条件见 `apply/order.py`、
  `apply/triggers.py`；PUT 即 apply（与设计文档不同）。
- teclaw 交付：整个 `BotConfigArtifact` 快照
  （`kernel/bot_config/artifact.py`），引擎契约 A1–A5 见
  `engine-convergence-contract.zh-CN.md`；`ownership` 标志由
  `teclaw_platform_managed` 门控。
- 服务型 Bot 的发布/回滚：带冻结 artifact 的版本化发布记录；回滚只能退回一步
  （`core/service_bot/services/publish_rollback_mixin.py:38-80`）。
- ADR 0018：Manifest apply 是一次性命令，而非控制器；Skills 与 MCP 保持各自
  独立的失败边界。

### 2.2 现有自改进管线

全部位于 `apps/evolverun/` 下；仓库其他位置没有对等实现。

- **ClawEvolve 控制面**（TS）：`clawweb/public/modules/clawevolve/server/`
  —— `services/evolve/evolution-flow.ts`（三个封闭的 flow key）、
  `stage-catalog.ts` + `resources/evolve/official-stage-catalog.json`
  （阶段 JSON Schema；`preprocess|postprocess|replace`）、
  `task-registry.ts`、`routes/evolve.ts`、`routes/internal/evolve.ts`
  （claim/report 步骤协议）、`services/evolve/skill-application.ts` +
  `contracts/bot-skill-gateway.ts`（经人工批准、带 CAS 的 skill 替换）、
  `create-module.ts:60-200`（宿主 DI 选项）。
- **阶段 skill**（Python，仅标准库）：`clawweb-skills/clawevolve-skills/` ——
  diagnose（`acquisition/discovery.py:41-96`、`sessions.py`、
  `service_export.py`、`integration/plan_source.py:25-97`，含 80/20 划分）、
  plan、tune、review、bench（`clawbench-base/scripts/lib_grading.py:51-108`）、
  pack、deploy。优化循环位于
  `clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py`
  （顺序 `:9491-9513`；接纳规则 `:6256-6378` = 测试得分严格高于基线；可通过
  环境变量调节的门禁 `:1890-1905`；硬编码工作区 `:221`；通过
  `openclaw agent --local` 执行 tune `:7938`）。
- **Tune 直接编辑线上工作区**；被拒绝的轮次会恢复到该轮之前的打包快照。
- **plan-source/v2**（`clawinsight/server/services/evolve/plan-source-contract.ts:6-40`）
  是与生产者无关的发现结果交接格式。
- **Workflow-run 自愈**：运行证据摄入、分析运行、建议、经验教训；分析器
  handler（「ClawMind」）不在仓库中；批量分析器与经验过期仅在测试中实例化。
- **ClawInsight**：改进项 → plan-source → plan+optimize；管理员评审；在 3 次
  验证成功后进化规则信任度。
- **TaskGuard**：运行内的守护/修复/重试；没有持久化学习。
- **Evolvetrace**：可观测性；进化标签页是 mock
  （`src/components/workflow-workspace/evolution-mock.ts`）。

### 2.3 现有 Bot 质量评估代码

完整清单、复用计划与缺口见
[03-verification.zh-CN.md §2](03-verification.zh-CN.md#2-代码库中已有的部分)。
概要：ClawBench（用例格式，自动化 / 评分细则 / 混合评分器）加上 ClawWeb Bench
存储，是唯一可用的 Bot 评分器，并且仅限 OpenClaw 本地。后端 eval env +
Quality Task 会部署隔离的服务型 Bot 副本，但把评分委托给外部服务。服务型 Bot
的 VERIFY 阶段不运行任何自动化检查。ClawEvolve 的回归、复现与校准门禁虽然存在，
但只是建议性的，或在规范轮次中无法触达。

### 2.4 塑造本设计的架构约束

- `docs/arch/arch.rules.md`：R1 契约；R3 Service API 与 Plugin API 区分；
  R5/R14 由配置在组合根中选择实现；R7 传输无关的核心；R11 插件生命周期声明；
  R12 通过 hook 认证；**R13 按插件类型声明隔离等级与能力**；R16 传播分析；
  R19 两个实例之后再抽象；R20 单机优先、本地模式可离线测试；R25 一致性测试。
- 后端插件模式：`plugin_api/*` Protocol、`plugins/local` +
  `plugins/community`、`@plugin_impl` 注册表；组合根
  `di/container.py`、`di/profile_modules.py`；一致性测试套件位于
  `apps/backend/tests/community/contracts/`。相关的现有接缝：
  `plugin_api/eval_env/`（EvalEnvLifecycle、VersionSync、…）。
- OpenAPI v1 拒绝 `bot` 主体（有意移除了 bot→owner 回退）—— 因此有 DR-3。
- `bcs-cli` 是面向 Bot、带 SKILL.md 与会话文件认证的 CLI 先例。
- 引擎拥有物理布局（ADR 0014、0017）；后端不得添加引擎路径。
