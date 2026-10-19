# Bot 基因组是进化的单元，构建在 Bot Config Manifest 之上

> English version: [0019-bot-genome-is-the-unit-of-evolution.md](0019-bot-genome-is-the-unit-of-evolution.md)

状态：accepted（2026-10-16；原为 bot-evolution 决策草案 DR-1）。负责人：@totalfrank，已签字确认。引擎负责人：@totalfrank，已签字认可记忆相关后果（策展记忆进入基因组；保留文件规则在 RSI-05 投影契约落地时修订）。

## 决策

Avernet 中的自改进只进化一种制品：**Bot 基因组**（Bot Genome），即 bot 可进化
定义的一个不可变、按内容寻址的修订版（人设文件、skill、策展记忆、资源、工具、
引擎配置，外加一个不可进化的 `policy` 部分）。基因组构建在 Bot Config
Manifest 之上：一个修订版会编译成一份钉住的 Manifest（Bot 配置清单）文档（每个来源
都解析到一个 commit SHA 或摘要），并通过现有的 Manifest / 发布管线应用。不引入
第二条交付路径。

一句话概括：修订版是一份完整且钉住的 Manifest，加上策展记忆，加上谱系与锁定的
policy，并且只能通过补丁变更。修订版是**完整的**：包含所有类别，因此无论先前
状态如何，一个修订版都能完整决定 bot。文件内容按摘要引用，存放在现有的
Manifest 内容存储中；Skill Center 的 skill 以钉住的 Center 版本引用，而不复制。

修订版以 RFC 8785 规范 JSON 存储，并以 `{spec, policy}` 的 SHA-256 作为标识。
Bot Config Manifest 保持现有的 YAML 形式；这里引入的所有新增内容都只使用 JSON。

修订版带有父指针、便于阅读的按 bot 递增序号、来源记录（由谁或什么创建、依据
哪些证据）和状态。具名引用
（ref）（`active`、`previous`、`canary`、`draft`、`candidate/*`、`inbox/*`）
指向修订版，并通过 compare-and-swap 移动。

Manifest 保持对修订版无感知。它只获得格式特性（一种内容存储来源、对允许列表中的
键应用 `engine_config`、一个 `memory` 类别、一条用于产出内容的写入路径）以及
一项自身的事实：每份 apply 报告以及每条服务型 bot 发布记录，都会记录其所应用
文档的摘要。基因组注册表把这些摘要映射到修订版。现有的 `/config-manifest` 端点
保持不变；直接的 Manifest 编辑会绕过基因组，不记录修订版，并作为**漂移**被
检测到（最近一次应用的摘要与 `active` 编译出的结果不同）。对于发生漂移的 bot，
进化会暂停，直到所有者完成调和。

进化策略和 bot 只能通过针对某个具名基础修订版提交一个有类型的、逐项列出的
**基因组补丁**（Genome Patch）来修改基因组。不支持自动化主体整体替换文档。

策展记忆成为基因组的一部分。运行时 / 情景记忆仍归引擎所有。这需要一份引擎
记忆投影契约，并修订当前「apply 从不触碰 `MEMORY.md` 和 `IDENTITY.md`」的规则：
由引擎而不是 Backend 决定策展条目如何投影。

设计：[`apps/backend/docs/bot-evolution/01-genome.zh-CN.md`](../../apps/backend/docs/bot-evolution/01-genome.zh-CN.md)。

## 影响

- 版本化的 bot、可归因的经验、回退到任意更早的修订版，这些能力都可以独立于
  任何进化策略而获得。回退就是再次晋升一个更早的修订版，并像任何晋升一样被
  apply。
- Manifest 存储保持每个 bot 一条记录；修订版和引用存放在基因组注册表中。apply
  报告记录所应用文档的摘要，由注册表将其映射到修订版。
- 直接的 Manifest 编辑会作为漂移被检测到：运行不会启动，并且除所有者本人的晋升
  之外，所有晋升都会被拒绝，直到所有者把 bot 当前的 Manifest 记录为一个修订版
  并晋升它，或者再次晋升 `active`。
- 在记忆可被进化之前，引擎所有者必须同意记忆投影契约；在此之前，策展的经验
  教训使用一个平台管理的人设文件。
- 对服务型 bot 而言，晋升一个修订版会通过现有的发布流程把它作为下一个版本
  发布，且发布记录会记录所发布文档的摘要。现有的服务型 bot 回滚功能保持
  不变。

## 备选方案

- **独立的进化制品（例如 ClawEvolve Pack），通过它自己的路径交付。** 已否决：
  会出现两个真相来源，而且 Pack 是 OpenClaw 专属的工作区快照，无法到达 teclaw。
- **每个 bot 一个 Git 仓库作为真相来源。** 暂缓：历史记录是免费获得的，但会
  增加多租户基础设施；改为提供 git 导出。
- **在 Manifest 之外做版本管理，并让 Manifest 完全保持不变。** 已否决：没有
  内容存储来源和所应用文档的摘要，apply 仍会重新获取变动中的来源，也无法说明
  它应用了哪份文档，因此行为无法归因到某个版本。基因组确实构建在 Manifest
  之上，但 Manifest 会获得这些格式特性。
