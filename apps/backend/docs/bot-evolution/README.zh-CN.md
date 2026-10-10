# Bot 进化平台（RSI）—— 设计文档集

> English version: [README.md](README.md)

> 状态：**草案（DRAFT）—— 设计，待评审。** 尚未基于本设计编写任何代码。
> 标记为*提议*（proposed）的决策，需要在依赖它们的工作项启动前获得负责人
> 签字确认。

## 这是什么

Avernet 的 bot 现在已经可以手工改进（编辑人设文件、上传 skill、应用 Bot
Config Manifest），对于本地 OpenClaw bot，还可以通过 `apps/evolverun/` 中的
AgentEvolve / ClawEvolve 管线改进。本设计文档集把这些能力变成一个**平台**：
一种与进化策略无关的方式，可以对任意 Avernet bot 运行自改进循环，其中：

1. **进化的单元**是一个有版本、不可变的 bot 制品——*Bot 基因组*（Bot
   Genome），构建在现有的 Bot Config Manifest 之上；
2. bot **如何**进化是一个**可插拔的进化策略**：一个 `run(ctx)` 端口，其他团队
   用 SDK 实现并注册，而无需修改平台。每个 bot 通过绑定（Binding）选择自己的
   进化策略；ClawEvolve 成为第一个默认进化策略；
3. 循环通过**同一份契约**驱动，契约有三个接入面：REST API、SDK，以及面向人和
   CI 的 CLI（bot 作为调用方的场景已推迟）；
4. **验证归平台所有**：任何进化策略都不能写入线上 bot。一切都经过
   候选 → 验证 → 门禁 → 晋升，并带有谱系；回到旧版本就是晋升一个更早的
   修订版；
5. **改进机制本身也可以改进**（第 3 层）：实验历史被用来提出更好的改进机制，
   只有在验证它能产生更好的、经过验证的改进之后才会被采用。验证器
   （Verifier）本身保持固定，由人负责。

**第一轮迭代：** 第 1–2 层（改进 bot，并带验证）。第 3 层（第 5 项）的设计
确保没有任何东西阻碍它，但它会在之后进行。

## 阅读顺序

从 [design.zh-CN.md](design.zh-CN.md) 开始：它解释问题、目标和架构，并概述
治理规则。随后各编号文档各自覆盖一个部分：先是**组件**（存在且有归属的东西），
然后是**服务**（运行的东西）。每份文档结构相同：领域模型、服务接口、带示例的
API，以及代码示例。

这些文件是同一份设计，而不是各自独立的工作会话。后续会话对应
[work-items.zh-CN.md](work-items.zh-CN.md) 中的工作项 RSI-01…RSI-24，每个
工作项都注明了需要先阅读哪些文档。

| 文档 | 类型 | 回答的问题 |
| --- | --- | --- |
| [design.zh-CN.md](design.zh-CN.md) | 概览 | 问题、目标、三个层级、架构、保障（治理摘要）、放置、分阶段、风险 |
| [01-genome.zh-CN.md](01-genome.zh-CN.md) | 组件 | 一个可进化的 bot *是*什么：修订版、引用（ref）、补丁、记忆、存储、Genome Registry API |
| [02-experience.zh-CN.md](02-experience.zh-CN.md) | 组件 | 进化策略从什么中学习：片段（episode）、反馈、会话导出、数据处理 |
| [03-strategy.zh-CN.md](03-strategy.zh-CN.md) | 组件 | 进化如何做到可插拔：策略端口、能力目录、上下文、智能体定义、SDK、一致性 |
| [04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md) | 组件 | ClawEvolve 及其他首批进化策略，以及它们复用的现有代码 |
| [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) | 组件 | 每次实验的记录：谱系、归档、审计 |
| [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) | 服务 | 绑定、运行、租约、长时操作、作业协议、沙箱、预算 |
| [07-verification.zh-CN.md](07-verification.zh-CN.md) | 服务 | 如何判断一次变更是好的：套件、评分器、配对运行、验证配置、判定 |
| [08-promotion.zh-CN.md](08-promotion.zh-CN.md) | 服务 | 由谁决定：权力分立、门禁、风险等级、评审、晋升、发布、回到旧版本 |
| [09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md) | 服务 | 公共访问层：API 约定、端点索引、SDK、`avn` CLI |
| [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) | 服务（之后） | 第 3 层：改进改进机制、机制验证、边界 |
| [research.zh-CN.md](research.zh-CN.md) | 附录 | 业界调研与代码库证据 |
| [work-items.zh-CN.md](work-items.zh-CN.md) | 附录 | 供后续会话使用的工作项及其依赖关系 |

决策记录草案（状态为 `proposed`）。讨论期间它们存放在这里；一旦被接受，每一份
都会以下一个可用的 ADR 编号晋升到 `docs/adr/`：

- [DR-1 —— Bot 基因组是进化的单元](decisions/0001-bot-genome-is-the-unit-of-evolution.zh-CN.md)
- [DR-2 —— 晋升归平台所有](decisions/0002-promotion-is-platform-owned.zh-CN.md)
- [DR-3 —— 进化接入面上的 bot 主体](decisions/0003-bot-principal-for-evolution-surface.zh-CN.md)（**已推迟**：先确定 bot 如何与平台通信）

## 术语表

| 术语 | 含义 |
| --- | --- |
| **Bot 基因组（Bot Genome）** | 单个 bot 可进化的声明式定义：人设文件、skill、记忆、资源、工具、引擎配置。一经记录即不可变；以内容哈希标识。 |
| **基因组修订版（Genome Revision）** | 基因组的一个已记录版本，带有父指针、来源记录和状态。 |
| **基因组补丁（Genome Patch）** | 从一个修订版到另一个修订版的、有类型的、逐项列出的变更。这是进化策略唯一可以提交的东西。 |
| **表型（Phenotype）** | 应用某个修订版后产生的运行中的 bot（引擎 + 工作区）。只被观测，从不被进化编辑。 |
| **经验（Experience）** | 从运行中的 bot 收集的、规范化后的片段（episode）（会话或轨迹）和反馈，每条都标注了产生它的修订版。 |
| **进化策略（Strategy）** | 只有一个方法 `run(ctx)` 的有版本代码，为一个 bot 提议候选；注册时附带它 `needs` 的目录能力。 |
| **能力（Capability）** | 策略上下文中归平台所有、带版本的一个字段（例如 `experience.sessions@1`），按引擎提供各自的提供方。 |
| **绑定（Binding）** | bot 进化策略配置（evolution policy）中的一个条目：用哪个进化策略、触发条件、父版本、允许的基因、验证配置、预算、参数。 |
| **进化运行（Evolution Run）** | 针对一个 bot 对某个绑定的一次执行，其输入在开始时冻结，并以运行 id 标识；它提交候选，并按 id 查询它们的判定。 |
| **作业（Job）** | 一次运行在某个 worker 上、在租约下的一次派发尝试。一次运行的每次尝试对应一个作业（崩溃后的重新派发会为同一运行创建新的作业）。调用方只看到运行；worker 只看到作业。 |
| **Rollout**（评估） | 针对一个 bot 版本执行一个评估用例一次；重复的种子分别计数。运行预算可以限制其数量。 |
| **Bot 身份（Bot identity）** | 一个 bot 由其所有者和 bot id 共同标识（`owner_id`、`bot_id`），因为仅凭 bot id 在不同用户之间并不唯一。路径携带 `{bot_id}`；所有者是查询参数 `entity_id`，默认为调用方，与 OpenAPI v1 一致。 |
| **操作（Operation）** | 在运行内部启动的长时工作（一次智能体会话、一次训练集评估）：启动时返回一个 id，并按该 id 查询其状态。 |
| **候选（Candidate）** | 在一次运行中提出、尚未晋升的基因组修订版。 |
| **判定（Verdict）** | 验证一个候选的结果：`pending`、`accept`、`reject` 或 `inconclusive`。 |
| **门禁（Gate）** | 归平台所有的决策点，使用绑定验证配置下的判定*加上*不可协商的平台检查，来接受或拒绝一个候选。 |
| **晋升（Promotion）** | 将 bot 的 `active` 引用（ref）移动到某个修订版，并通过现有的 Manifest / 发布链路应用它。 |
| **实验记录 H（Experiment Ledger (H)）** | 曾经运行过的每一次改进实验（改进机制、父版本、候选、证据、判定、成本、线上结果）。不删除任何内容；它是第 2 层的选择池、审计轨迹，也是第 3 层的证据基础。 |
| **改进机制（M）（Mechanism (M)）** | 改进机制：一个进化策略版本，加上它的 prompt、智能体定义、参数和模型。有版本，因此它本身也可以被改进（第 3 层）。 |
| **验证器（Verifier）** | 套件、评分器、门禁底线和验证协议。由人负责；永远不会被任何自动化循环修改。 |

## 非目标

- 权重训练或微调。经验保持可用于训练的状态（见
  [design.zh-CN.md](design.zh-CN.md)），但平台进化的是 harness，而不是
  模型。
- 进化团队拓扑和 BCS 路由。v1 中基因组按 bot 划分；团队级基因组属于未来
  工作。
- bot 调用平台（随 DR-3 一并推迟）。
- 本阶段不替换 AgentEvolve UI。
