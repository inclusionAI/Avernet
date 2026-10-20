# 工作项

> English version: [work-items.md](work-items.md)

> 状态：DRAFT（讨论稿）。每个工作项的规模按一次后续会话来划定：该会话可将其按
> SDD（spec → plan → tasks → implement）推进完成；对于仅涉及设计的工作项，则
> 推进到一份经过评审的契约文档。每项开始时先阅读*先读*下列出的文档。实现
> spec 放在所属模块的 specs 目录下（`apps/backend/specs/YYYY-MM-DD-<topic>/`、
> `engine/adapter/specs/…`）；设计层面的补充则扩展本目录。

## 依赖关系图

![工作项依赖关系图](images/work-items-deps.zh-CN.svg)

通往第一个有用成果的关键路径：**RSI-01 → RSI-02 → RSI-03 → RSI-04**
（可以回到任意更早修订版的版本化 Bot）。通往「ClawEvolve 运行在平台上」的关键路径：
再加上 RSI-06 → RSI-08 → RSI-09/10/11/12 → RSI-13。第 3 层
（RSI-23 → RSI-24）不在第一轮迭代范围内，第一轮迭代聚焦于第 2 层；
RSI-21 仍从一开始就记录实验。
RSI-11 → RSI-22 可独立于其余部分，为服务型 Bot 提供自动化的验证门禁。

---

## P0 — 契约与决策

### RSI-01 接受或修订三项决策草案
- **状态**：已完成（2026-10-16）。DR-1 →
  [ADR 0019](../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.zh-CN.md)，DR-2 →
  [ADR 0020](../../../../docs/adr/0020-promotion-is-platform-owned.zh-CN.md)，D-1 方案 A →
  [ADR 0021](../../../../docs/adr/0021-evolution-control-plane-in-apps-evolution.zh-CN.md)；负责人均为 @totalfrank 并已签字确认，
  包括引擎负责人对 DR-1 记忆相关后果的签字认可。DR-3 仍推迟。
- **模块**：arch
- **目标**：由所有者决定 DR-1（基因组 = 进化单位，构建于 Manifest 之上）和
  DR-2（晋升由平台所有）。DR-3（进化接口面使用 Bot 主体）已推迟，直到 Bot 与
  平台的通信方式确定为止。同时决定
  [design.zh-CN.md](design.zh-CN.md#6-归属与模块放置) 中的待定决策 D-1
  （控制面模块放置）。
- **先读**：[design.zh-CN.md](design.zh-CN.md)、[01-genome.zh-CN.md](01-genome.zh-CN.md)、
  [design.zh-CN.md](design.zh-CN.md#5-保障治理摘要)、[decisions/0003-bot-principal-for-evolution-surface.zh-CN.md](decisions/0003-bot-principal-for-evolution-surface.zh-CN.md)。
- **交付物**：DR-1 和 DR-2 被接受（以下一个可用编号晋升到 `docs/adr/`）或被修订；
  记录 D-1。
- **完成标准**：每份 ADR 都有所有者，且引擎所有者已明确签字认可 DR-1 中关于
  记忆的后果。

### RSI-02 基因组 schema 与补丁格式
- **状态**：已完成（2026-10-18）。契约：
  [`apps/backend/api-contracts/genome/v1/`](../../api-contracts/genome/v1/README.md)；规格：
  `apps/backend/specs/2026-10-18-bot-genome-schema/`。负责人及引擎评审签字：
  @totalfrank。已关闭 G-1、G-2、G-3、G-7、G-10。已开启的后续事项：#2858
  （`engine_config`）、#2859（生效的 MCP 配置）、#2860（MCP 请求头凭证）、#2862
  （`script: null`）；通过 RSI-04 依赖 #2162（资源删除）。
- **模块**：backend（契约）、engine（评审）
- **目标**：为基因组修订版（Genome Revision）（`spec`、`policy`、`revision`
  元数据）和基因组补丁（Genome Patch）（操作、风险等级映射、重写阈值）制定
  规范性 JSON Schema，制定内容哈希的规范化规则（RFC 8785 规范 JSON，见
  [01-genome.zh-CN.md](01-genome.zh-CN.md)），以及与 Manifest schema v1 之间
  的双向映射。
- **先读**：[01-genome.zh-CN.md](01-genome.zh-CN.md)；`manifest-schema.zh-CN.md`；
  `schema/validator.py`。
- **交付物**：`apps/backend/specs/<date>-bot-genome-schema/`，包含 schema 文件、
  示例，以及与 Manifest v1 的兼容性对照表。
- **完成标准**：每个 Manifest v1 示例都能往返转换为修订版；每种补丁操作都有
  明确的风险等级；评审者就策略配置默认值（`mutable_genes`、`pins`）达成一致。

### RSI-06 进化策略端口、能力目录与作业协议
- **模块**：evolution（新增）、arch
- **目标**：定义策略端口（`run(ctx)`）、`StrategyContext`、
  候选 / 判定（提交返回候选 id；判定按 id 查询）、注册记录 schema、首个能力
  目录（始终授予：`candidates@1`、`models@1`；需声明：`experience.sessions@1`、
  `experience.feedback@1`、`agents@1`、`evaluation.train@1`）及其按引擎的提供方契约（对 `agents@1` 而言，包括按引擎的
  智能体定义契约以及定义的上传与加载，见 03-strategy.md）、绑定（bindings）
  schema 与绑定检查、运行生命周期与失败语义（R11）：幂等的运行提交并
  返回运行 id、崩溃后以相同运行 id 重新派发的带租约作业、由策略自行负责的进度
  持久化（平台不提供检查点 API）；智能体会话和训练评估的长时操作（启动返回操作
  id，按键幂等；状态按 id 查询；工作运行期间不挂起任何请求）；隔离（R13）；以及
  每个 `ctx` 调用在作业协议（Job Protocol）中的映射。
- **先读**：[03-strategy.zh-CN.md](03-strategy.zh-CN.md)、
  [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)；ClawEvolve
  `official-stage-catalog.json`、`routes/internal/evolve.ts`；
  `docs/arch/protocol-contract-tests.md`。
- **交付物**：契约文档 + JSON Schema 文件。
- **完成标准**：ClawEvolve 的轮次循环可以完全基于上下文编写，而无需绕过它
  （用 04-default-strategies.md 中的 ClawEvolve 草图核对），并且一个非 ClawEvolve 的进化策略（记忆整合）能以
  一组不同的能力适配。

### RSI-07 进化 API 契约
- **模块**：evolution、backend、gateway
- **目标**：Genome 与 Evolution 资源的 OpenAPI 文档（异步模式、幂等性、ETag、与
  OpenAPI v1 一致的错误信封）、gateway 路由，以及针对这些约定的共享一致性测试。
- **先读**：[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)；
  `apps/backend/docs/openapi-v1/README.md`；
  `apps/gateway/configs/schemas/README.md`。
- **交付物**：OpenAPI 草案（`bots.openapi.json` 的新增内容、
  `evolution.openapi.json`）以及 gateway 域条目。
- **完成标准**：09-evolution-api.md §13 中的每个调用方流程都能在纸面上用所列
  端点走通。

## P1 — 版本化 Bot（独立于 RSI 也有价值）

### RSI-03 Backend 中的基因组注册表
- **模块**：backend
- **目标**：修订版表、带 CAS 的引用（ref）、补丁应用/校验、diff、将钉住的源
  解析进内容存储、谱系查询。提供晋升 API：移动 `active` 并调用 Manifest apply
  （个人 Bot），或把修订版作为下一个发布版本发布（服务型 Bot）。已应用摘要 →
  修订版的映射，以及漂移检查（[01-genome.zh-CN.md](01-genome.zh-CN.md) §5.5）。
- **依赖**：RSI-02。
- **先读**：[01-genome.zh-CN.md](01-genome.zh-CN.md)；`core/bot_config_manifest/`。
- **完成标准**：可从 manifest、从补丁创建修订版；可对任意两个修订版做 diff；
  移动引用时能检测冲突；一致性测试 + 单元测试；singlebox 验收故事「编辑 →
  修订版 → apply → 通过晋升一个更早的修订版回到两个修订版之前」。

### RSI-04 已编译修订版的直接投递（第二阶段）
- **模块**：backend
- **目标**：通过现有的 apply 引擎把已编译的修订版直接投递到 Bot 运行时，中间
  不再经过 Manifest 文档。新组件接收已编译的修订版（每个类别都存在；文件条目
  以内容存储摘要表示，Center 技能以固定版本表示，共享技能仓库的技能以路径
  表示），直接喂给 apply 引擎的各物化器：从内容存储读取字节，解析 Center 与
  仓库技能；它们接入 RSI-03 留下的 `RevisionApplier` 接缝。Manifest 语法不新增
  任何源类型。apply 报告记录它所应用的已编译文档的摘要
  （[01-genome.zh-CN.md](01-genome.zh-CN.md) §8 第 5 项）；对服务型 Bot，发布
  记录记录所发布文档的摘要。已编译的修订版还必须能删除修订版中省略的内容，
  因此本项包含两个完整性修复：资源删除
  （[#2162](https://github.com/inclusionAI/Avernet/issues/2162)）和
  `script: null`（[#2862](https://github.com/inclusionAI/Avernet/issues/2862)）。
  仍属于 Manifest、随各自条目落地的工作：`engine_config`（待定决策 G-4，
  [#2858](https://github.com/inclusionAI/Avernet/issues/2858)）和 `memory`
  类别（在 RSI-05 之后）。Manifest 中没有修订版表、引用或 `revision_id`；
  `/config-manifest` 端点保持不变。现有的服务型 Bot 回滚功能不做改变。
- **依赖**：RSI-03（接缝与已编译文档）、#2162、#2862。
- **完成标准**：所有现有 manifest 测试无需修改即通过；在 singlebox 中晋升一个
  修订版即可把它投递到个人 Bot，无需任何拉取、也不写 Manifest，apply 报告携带
  已编译文档的摘要且 `GenomeRegistry.applied_revision` 能映射回修订版；通过
  晋升更早的修订版回退两个版本能恢复 Bot，包括被删除的资源和脚本。

## P2 — 进化核心

### RSI-08 进化服务骨架
- **模块**：evolution（新的 `apps/evolution`，依据 D-1）
- **目标**：遵循 Backend DI/插件约定的服务脚手架；带预算、幂等运行提交（幂等键 →
  运行 id）以及租约到期后重新派发的作业租约的进化运行编排器状态机；
  进化策略注册表；作业协议端点；带绑定检查与触发的绑定；面向
  singlebox 的本地 profile（SQLite、进程内进化策略）；一个参考进化策略
  `platform/manual-patch`（提交一个提供的补丁；用确定性检查验证），用于端到端
  演练整个循环。
- **依赖**：RSI-06、RSI-07、RSI-03。
- **完成标准**：singlebox 故事：用参考进化策略启动运行 → 记录候选 → 门禁 →
  晋升 → Bot 更新 → 回到上一个修订版；用相同幂等键重复启动会返回相同的运行 id；
  在运行中途杀掉 worker 会导致以相同运行 id 重新派发，重新派发的运行会重新接上
  正在运行的操作，而不是再次启动它们。

### RSI-09 进化策略 SDK 与一致性测试套件
- **模块**：evolution
- **目标**：Python 策略 SDK：`EvolutionStrategy` 基类、类型化模型、进程内与
  作业协议两种 `StrategyContext` 实现、`WorkspaceFactory`（物化 / `to_patch`）、
  带 OpenClaw 提供方的 `AgentRunner`、带模拟平台的本地 harness（能够杀掉并
  重新派发一次运行，以测试策略自身的恢复）、作为测试辅助的策略一致性测试
  套件。注册通过 `POST /evolution/strategies` 端点完成，不属于该包。
- **依赖**：RSI-06、RSI-08。
- **完成标准**：由平台团队之外的人仅凭策略 SDK 文档编写一个示例第三方进化策略
  （例如 OPRO 风格的人设优化器），并通过一致性测试。

### RSI-10 经验库与会话导出 v2
- **模块**：evolution + engine adapter
- **目标**：带修订版 id 标签的片段（episode）/反馈/评估 trace 模型；摄入管线；
  引擎 `session-export/v2` 契约（泛化 ClawEvolve 的 `session-export/v1`），先做
  OpenClaw 实现；保留期与脱敏。
- **依赖**：RSI-08；引擎所有者。
- **完成标准**：可按修订版查询来自 OpenClaw Bot 的片段；ClawEvolve diagnose
  可以从经验库读取。

## P3/P4 — 评估、治理与默认进化策略

### RSI-11 验证服务（Bot 验证）
- **模块**：evolution、backend（`eval_publish`、`eval_env`）
- **目标**：实现 [07-verification.zh-CN.md](07-verification.zh-CN.md)：
  以 ClawWeb Bench 模型和 ClawBench 用例格式为种子的套件注册表；由平台分配的
  划分，包括密封的封存集与必过的回归集/安全集；从 `lib_grading` 中抽取的
  `platform/clawbench` 评分器；本地沙箱与已部署沙箱（eval env）执行器；带重复
  种子与置信区间的配对比较器；评审模型集成；将 ClawEvolve 的 `full_opt_gate` /
  `candidate_opt_gate` 变为阻断式的判定策略；验证配置。
- **先读**：[07-verification.zh-CN.md](07-verification.zh-CN.md)（§2 列出了可复用的
  现有代码）；[08-promotion.zh-CN.md](08-promotion.zh-CN.md)。
- **完成标准**：候选在沙箱中完成验证，带配对基线与按划分的判定；可证明策略
  作业输入不包含封存集、回归集和安全集；同一批用例文件无需修改即可在 ClawBench
  和平台评分器下运行。

### RSI-12 门禁、评审队列与晋升
- **模块**：backend（门禁底线、晋升）、evolution（绑定）
- **目标**：平台底线检查、风险等级分配、基于绑定验证配置下验证判定的门禁、带
  diff + 验证报告的评审队列、审批、Bot 的绑定（包括自动晋升上限）、审计事件、
  紧急停止开关。
- **完成标准**：T1 在自动晋升上限内自动晋升；T2 等待审批；T3 在锁定时被拒绝；所有
  决定均有审计。

### RSI-13 将 ClawEvolve 接入为默认进化策略
- **模块**：evolverun、evolution
- **目标**：执行
  [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)
  中的绞杀者步骤 1–3：影子记录修订版 → 黑盒适配器进化策略 → 原生进化策略。
  Tune 在由 `ctx.workspace` 得到的沙箱上工作；它的接纳规则变为内部提交过滤，
  而接受与否转由平台验证决定；从流程中移除 pack/restore；ClawEvolve 将其轮次
  状态持久化到自己的存储中，以运行 id 为键（今天的 `ce_tasks` /
  `ce_steps` 即可承担），使重新派发的运行能够继续；决定 DS-1 和 DS-2。
- **依赖**：RSI-09、RSI-10、RSI-11、RSI-12。
- **完成标准**：`clawevolve/bot-evolution` 通过平台、在不触碰线上工作区的
  情况下，在其自身 bench 上取得与旧版 AgentEvolve 相同或更好的结果。

### RSI-16 发布：影子、金丝雀与自动回滚
- **模块**：backend、baas
- **目标**：为多实例 Bot 提供 `canary` 引用；在线指标对比；可选的自动回滚；
  映射到服务型 Bot 的 verify 阶段。
- **依赖**：RSI-12。

### RSI-21 实验记录 H
- **模块**：evolution
- **目标**：按 [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) 中的
  schema 记录每一次第 2 层实验（包括被拒绝的候选及其后续的线上结果）；派生的
  机制指标；面向策略的文件系统导出；验证器版本标记。
- **依赖**：RSI-08。在第一轮迭代中，它是第 2 层的归档与审计轨迹；为第 3 层
  派生改进机制指标可以推迟，但记录应尽早开始，因为第 3 层的效果取决于它所学习
  的历史。

### RSI-22 服务型 Bot 发布与 Quality Task 上的验证门禁
- **模块**：backend
- **目标**：在服务型 Bot 的 `VALIDATING → ONLINE_PUB` 状态转换上，使用验证服务
  提供可选的自动化验证门禁；让 Quality Task 指向一个仓库内的评分器插件（与外部
  MASA 评分器并存），使开源构建拥有可用的 Bot 质量检查。
- **依赖**：RSI-11。
- **完成标准**：若服务型 Bot 的 verify 阶段候选未通过某个必过用例，则在没有
  显式覆盖的情况下无法发布，且该覆盖操作会被审计。

### RSI-23 机制验证与离线重放（之后，第 3 层）
- **模块**：evolution
- **目标**：(a) 基于 H 的离线重放工具，用于验证配置与提交过滤的变更，泛化
  `calibrate_evolution_gates.py` / `replay_candidate_gate.py`；
  (b) 从 H 冻结得到的改进问题基准，以及
  [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) 中的机制验证协议，
  首先用于**人工编写**的进化策略变更。
- **依赖**：RSI-11、RSI-13、RSI-21。
- **完成标准**：对 ClawEvolve tune prompt 的一次修改，通过在留出问题上将已验证
  的改进收益与当前机制对比，被接受或拒绝。

### RSI-24 自动化元策略（之后，第 3 层）
- **模块**：evolution
- **目标**：一个读取 H 并提议机制补丁（阈值、prompt、算子、步骤顺序）的元进化
  策略，只能经由 RSI-23 与人工批准才被采纳；由静态检查强制执行
  [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) 中的边界。
- **依赖**：RSI-23。

### RSI-20 运行、评审队列与谱系的 UI
- **模块**：frontend-nextgen（或过渡期使用 AgentEvolve）
- **目标**：运行列表/详情、候选报告（diff + 按划分的评估）、评审操作、基因组
  谱系树、回到旧版本。
- **依赖**：RSI-08、RSI-12。

## P5 — Bot 驱动的进化

### RSI-14 Bot 主体 scope 与 Bot skill *（已推迟）*
- **状态**：随 DR-3 已推迟，直到 Bot 与平台的通信方式确定为止。不要领取。
- **模块**：gateway、backend、evolution（遵循 bcs-cli 约定）
- **目标**：实现 DR-3：Bot 主体仅能访问进化端点，scope 单独设计（早先的 scope
  草图已放弃），并通过授权 hook 强制执行；
  基于公共 API 的面向 Bot 的工具，通过 Manifest `cli_tools` 交付；被改进 Bot 与
  执行者 Bot 的 `SKILL.md`；带速率限制的收件箱与观察端点。
- **完成标准**：singlebox 中的 Bot 通过该工具记录一条观察并提交一个收件箱补丁；
  试图晋升或触碰其他 Bot 的操作被拒绝并审计；像 `bcs-cli` 一样设置叶子命令覆盖率
  门禁。

### RSI-05 引擎记忆投影契约
- **模块**：engine adapter（所有者）、backend（使用方）
- **目标**：`export_memory` / `project_memory(mode)` 契约、能力矩阵条目、
  OpenClaw 实现、teclaw artifact 字段提案；相应修订保留文件规则。
- **依赖**：RSI-02、DR-1 已被接受。
- **过渡方案**：完成之前，记忆进化使用一个由平台管理的人设文件，即一个现有的
  身份文件类型，例如 `KNOWLEDGE.md`（`LESSONS.md` 不是 Manifest 的身份文件类型；
  见 [01-genome.zh-CN.md](01-genome.zh-CN.md) §7）。

### RSI-15 记忆整合进化策略
- **模块**：evolution
- **目标**：按
  [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)
  实现 `platform/consolidate-memory` —— 第二个非 ClawEvolve 的默认进化策略，
  用以证明可插拔性（R19）。
- **依赖**：RSI-05（或过渡方案）、RSI-13。由 Bot 记录的观察需等待 RSI-14
  （已推迟）；在此之前，该进化策略使用反馈和片段。

## P6 — 开放式探索

### RSI-17 归档选择器
- 将逐用例 Pareto（GEPA）、MAP-Elites 生态位、分支元生产力（HGM）实现为绑定
  `parent` 字段的选项；为进化策略导出归档读模型。

### RSI-18 跨 Bot skill 迁移
- 已晋升的 skill 在现有治理（ADR 0010）下提供给 Skill Center；在采纳前针对每个
  使用方 Bot 重新评估。

### RSI-19 训练数据导出
- 对 `(input, revision, output, scores, critiques)` 以及被接受/被拒绝的配对做
  脱敏导出，仅限租户主动选择加入。

## 横切的待定决策（在此跟踪）

每份文档也会以文档前缀列出各自的待定决策：`G-` 基因组、`X-` 经验、`S-` 进化策略、
`DS-` 默认进化策略、`L-` 实验记录、`ER-` 进化运行、`V-` 验证、`P-` 晋升、`A-` API、
`O-` 元进化。

| ID | 决策 | 位置 |
| --- | --- | --- |
| D-1 | 控制面模块放置 | 已决定：`apps/evolution`（[ADR 0021](../../../../docs/adr/0021-evolution-control-plane-in-apps-evolution.zh-CN.md)） |
| DS-1（原 D-2） | 默认进化策略执行器的长期宿主（TS 还是 Python） | 04-default-strategies.md → RSI-13 |
| DS-2（原 D-3） | 将 ClawBench 作为平台默认评分器 | 04-default-strategies.md → RSI-11 |
| DS-3（原 D-4） | Workflow YAML 作为基因还是独立 artifact | 04-default-strategies.md |
| DS-4（原 D-5） | ClawMind 分析器契约；休眠的分析器要么接入要么移除 | 04-default-strategies.md |
| D-6 | 基因组存储：DB + 内容存储（推荐）还是每个 Bot 一个 git 仓库 | 01-genome.md；RSI-03 |
| A-n | 进化 API 的待定决策（事件投递；Bot 运行请求已推迟） | 09-evolution-api.md |
