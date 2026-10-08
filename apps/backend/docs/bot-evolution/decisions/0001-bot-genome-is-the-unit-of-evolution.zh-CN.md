# Bot 基因组是进化的单元，构建在 Bot Config Manifest 之上

> English version: [0001-bot-genome-is-the-unit-of-evolution.md](0001-bot-genome-is-the-unit-of-evolution.md)

状态：proposed（决策草案；接受后晋升到 `docs/adr/`）。

## 决策

Avernet 中的自改进只进化一种制品：**Bot 基因组**（Bot Genome），即 bot 可进化
定义的一个不可变、按内容寻址的修订版（人设文件、skill、策展记忆、资源、工具、
引擎配置，外加一个不可进化的 `policy` 部分）。它就是带有历史的 Bot Config
Manifest：一个修订版会编译成一份钉住的 Manifest（Bot 配置清单）文档（每个来源
都解析到一个 commit SHA 或摘要），并通过现有的 Manifest / 发布管线应用。不引入
第二条交付路径。

一句话概括：修订版是一份完整且钉住的 Manifest，加上策展记忆，加上谱系与锁定的
policy，并且只能通过补丁变更。修订版是**完整的**：包含所有类别，因此无论先前
状态如何，一个修订版都能完整决定 bot。文件内容按摘要引用，存放在现有的
Manifest 内容存储中；Skill Center 的 skill 以钉住的 Center 版本引用，而不复制。

修订版带有父指针、便于阅读的按 bot 递增序号、来源记录（由谁或什么创建、依据
哪些证据）和状态。具名引用
（ref）（`active`、`previous`、`canary`、`draft`、`candidate/*`、`inbox/*`）
指向修订版，并通过 compare-and-swap 移动。现有的 `/config-manifest` 端点保留，
成为注册表之上的视图。

进化策略和 bot 只能通过针对某个具名基础修订版提交一个有类型的、逐项列出的
**基因组补丁**（Genome Patch）来修改基因组。不支持自动化主体整体替换文档。

策展记忆成为基因组的一部分。运行时 / 情景记忆仍归引擎所有。这需要一份引擎
记忆投影契约，并修订当前「apply 从不触碰 `MEMORY.md` 和 `IDENTITY.md`」的规则：
由引擎而不是 Backend 决定策展条目如何投影。

设计：[`../02-genome.zh-CN.md`](../02-genome.zh-CN.md)。

## 影响

- 版本化的 bot、可归因的经验、回滚到任意已晋升修订版，这些能力都可以独立于
  任何进化策略而获得。
- Manifest 存储从每个 bot 一条可变记录，变为修订版 + 引用；apply 报告会记录
  所应用的修订版。
- 在记忆可被进化之前，引擎所有者必须同意记忆投影契约；在此之前，策展的经验
  教训使用一个平台管理的人设文件。
- 服务型 bot 的发布记录引用修订版 id；回滚资格从只能回退一步推广到任意已晋升
  的修订版。

## 备选方案

- **独立的进化制品（例如 ClawEvolve Pack），通过它自己的路径交付。** 已否决：
  会出现两个真相来源，而且 Pack 是 OpenClaw 专属的工作区快照，无法到达 teclaw。
- **每个 bot 一个 Git 仓库作为真相来源。** 暂缓：历史记录是免费获得的，但会
  增加多租户基础设施；改为提供 git 导出。
- **保持 Manifest v1 不变，在其外部做版本管理。** 已否决：apply 仍会重新读取
  一份可变文档，因此行为无法归因到某个版本。
