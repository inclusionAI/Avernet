# Bot 进化平台（RSI）—— 设计集

> English version: [README.md](README.md)

> 状态：**DRAFT——高层设计，待评审。** 尚未基于本设计编写任何代码。标记为
> *proposed* 的决策，需要在依赖它们的工作项启动前获得负责人签字确认。

## 这是什么

Avernet 的 bot 现在已经可以手工改进（编辑人设文件、上传 skill、应用 Bot
Config Manifest），对于本地 OpenClaw bot，还可以通过 `apps/evolverun/` 中的
AgentEvolve / ClawEvolve 管线改进。本设计集把这些能力变成一个**平台**：一种与
进化策略无关的方式，可以对任意 Avernet bot 运行递归自改进（recursive
self-improvement，RSI）循环，其中：

1. **进化的单元**是一个有版本、不可变的 bot 制品——*Bot 基因组*（Bot
   Genome）——构建在现有的 Bot Config Manifest 之上；
2. bot **如何**进化是一个**可插拔的进化策略**：一个 `run(ctx)` 端口，其他团队
   用 SDK 实现并注册，而无需修改平台。每个 bot 通过绑定选择自己的进化策略；
   ClawEvolve 成为第一个、也是默认的进化策略；
3. 循环可以由**确定性代码**（API / SDK）驱动，由**运维人员或 CI**（CLI）
   驱动，也可以由**一个 bot** 驱动（同一个 CLI，作用域限定为 bot 主体），三者
   共用同一份契约；
4. **验证归平台所有**：任何进化策略、任何 bot 都不能直接写入线上 bot。一切
   都经过 候选 → 验证 → 门禁 → 晋升，并带有谱系，而回退就是再次晋升一个更早的
   修订版；
5. **改进机制本身也会改进**（真正的 RSI）：实验历史 H 被用来提出更好的
   改进机制 M2，只有在验证其能比 M1 产生更好的、经过验证的改进之后才会被
   采用。验证器本身保持固定，由人负责。

**第一轮迭代：** 第 1–2 层（改进 bot，并带验证）。第 3 层（第 5 项）的设计
确保没有任何东西阻碍它，但它会在之后进行。

## 阅读顺序

这些文件是同一份设计按主题拆分而成，应按以下顺序一起阅读。它们不是各自独立的
工作会话：后续会话对应 [10-work-items.zh-CN.md](10-work-items.zh-CN.md) 中的
工作项 RSI-01…RSI-24，每个工作项都注明了需要先阅读哪些文档。

| # | 文档 | 回答的问题 |
| --- | --- | --- |
| 1 | [01-design.zh-CN.md](01-design.zh-CN.md) | 架构：三个层次、组件、循环、归属、模块放置、分期 |
| 2 | [02-genome.zh-CN.md](02-genome.zh-CN.md) | 一个被进化的 bot *是*什么：Bot 基因组，以及它如何扩展 Manifest |
| 3 | [03-verification.zh-CN.md](03-verification.zh-CN.md) | 如何判断一次变更是好的：Bot 验证（S′ 对比 S）与机制验证（M′ 对比 M），以及已有哪些评测代码 |
| 4 | [04-recursion.zh-CN.md](04-recursion.zh-CN.md) | 第 3 层：基于实验记录 H 安全地改进改进机制 |
| 5 | [05-strategy-sdk.zh-CN.md](05-strategy-sdk.zh-CN.md) | 进化如何做到可插拔：唯一的策略端口、能力目录、按 bot 的绑定、运行时 |
| 6 | [06-interfaces.zh-CN.md](06-interfaces.zh-CN.md) | API、SDK 与 CLI 的区别，以及 bot 如何驱动进化 |
| 7 | [07-default-strategy.zh-CN.md](07-default-strategy.zh-CN.md) | ClawEvolve 及其他现有管线如何作为默认策略接入 |
| 8 | [08-governance.zh-CN.md](08-governance.zh-CN.md) | 门禁、风险等级、反奖励投机、沙箱、发布、预算 |
| 9 | [09-research.zh-CN.md](09-research.zh-CN.md) | 这些决策所依据的业界调研与代码库证据 |
| 10 | [10-work-items.zh-CN.md](10-work-items.zh-CN.md) | 供后续会话使用的编号工作项及其依赖关系 |

决策草案（状态为 `proposed`）。讨论期间它们存放在这里；一旦被接受，每一份
都会以下一个可用的 ADR 编号晋升到 `docs/adr/`：

- [DR-1 —— Bot 基因组是进化的单元](decisions/0001-bot-genome-is-the-unit-of-evolution.zh-CN.md)
- [DR-2 —— 晋升归平台所有](decisions/0002-promotion-is-platform-owned.zh-CN.md)
- [DR-3 —— 进化接口面上的 bot 主体](decisions/0003-bot-principal-for-evolution-surface.zh-CN.md)（**已推迟**：先确定 bot 如何与平台通信）

## 术语表（在本设计稳定之前仅在本设计内有效）

| 术语 | 含义 |
| --- | --- |
| **Bot 基因组（Bot Genome）** | 单个 bot 可进化的声明式定义：人设文件、skill、记忆种子、资源、工具、引擎配置。一经记录即不可变；以内容哈希标识。 |
| **基因组修订版（Genome Revision）** | 基因组的一个已记录版本，带有父指针、来源记录和状态。 |
| **基因组补丁（Genome Patch）** | 从一个修订版到另一个修订版的、有类型的、逐项列出的变更。这是进化策略唯一可以提交的东西。 |
| **表型（Phenotype）** | 应用某个修订版后产生的运行中的 bot（引擎 + 工作区）。只被观测，从不被进化直接编辑。 |
| **经验（Experience）** | 从表型中收集的、规范化后的片段（episode）（会话 / 轨迹）、反馈和评估轨迹。 |
| **进化策略（Strategy）** | 只有一个方法 `run(ctx)` 的有版本代码，为一个 bot 提议候选；注册时附带它 `needs` 的目录能力。 |
| **能力（Capability）** | 策略上下文中归平台所有、带版本的一个部分（例如 `experience.sessions@1`），按引擎划分提供方。 |
| **绑定（Binding）** | bot 进化策略配置（evolution policy）中的一个条目：用哪个进化策略、触发、父版本、允许的基因、验证配置、预算、参数。 |
| **进化运行（Evolution Run）** | 针对一个 bot 对某个绑定的一次执行，所有内容在开始时冻结；它提交候选并接收判定。 |
| **候选（Candidate）** | 在一次运行中提出、尚未晋升的基因组修订版。 |
| **门禁（Gate）** | 归平台所有的决策点，使用绑定验证配置下的验证判定*加上*不可协商的平台检查，来接受或拒绝一个候选。 |
| **晋升（Promotion）** | 将 bot 的 `active` 引用（ref）移动到某个修订版，并通过现有的 Manifest / 发布链路应用它。 |
| **归档 / 实验记录 H（Archive / Experiment Ledger (H)）** | 曾经运行过的每一次改进实验（改进机制、父版本、候选、证据、判定、成本、线上结果）。不删除任何内容；它是第 2 层的选择池，也是第 3 层的证据基础。 |
| **改进机制（M）（Mechanism (M)）** | 改进机制：一个进化策略版本，加上它的 prompt、算子、参数和模型。像基因组一样有版本，因此它本身也可以被改进（第 3 层）。 |
| **Bot 验证（Bot verification）** | 在平台所有的用例集上，判定候选 bot S′ 是否优于其父版本 S。 |
| **机制验证（Mechanism verification）** | 在封存的改进问题上，判定候选改进机制 M′ 是否比 M 产生更好的*经过验证的*改进。 |
| **验证器（Verifier）** | 用例集、评分器、门禁底线和验证协议。由人负责；永远不会被任何自动化循环修改。 |

## 本设计集的非目标

- 权重训练 / 微调。经验库保持可用于训练的状态（见
  [01-design.zh-CN.md §9](01-design.zh-CN.md#9-通往权重训练的桥梁)），但平台进化的是
  harness，而不是模型。
- 进化团队拓扑和 BCS 路由。v1 中基因组按 bot 划分；团队级基因组列为未来
  工作。
- 本阶段不替换 AgentEvolve UI。
