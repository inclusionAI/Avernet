# apply 引擎只接收一种类型化期望状态；漂移靠观察而非追踪

> English version: [0022-apply-engine-desired-state-and-observed-drift.md](0022-apply-engine-desired-state-and-observed-drift.md)

状态：accepted（2026-10-23；RSI-04 之前的重构，在 PR #3083 上与 issue 负责人共同决定）。负责人：@totalfrank，已签字确认。

## 决策

**一种输入。** Backend 的 apply 引擎（`apps/backend/src/agentclaw/community/core/apply_engine/`）
是一个独立组件，其唯一输入是类型化的**期望状态**（`DesiredState`）：每个构造一个
`Declaration`——`script`、`identity`、`resources`、`skills`、`mcp`、`engine_config`、
`cli_tools`——要么未声明（`None`：apply 不动 Bot 在该构造上的状态），要么以一组
类型化条目声明，空元组即声明「没有」，引擎通过删除现有内容来收敛到它。每个条目
以摘要（`ContentRef`）指向内容存储中已有的字节，或 Skill Center 的版本，或共享
仓库的路径。引擎从不看到 Manifest 文档，也从不拉取。

**两个编译器，互不转换。** 产生期望状态的是*编译器*，apply 运行按 apply 携带的状态
引用查找：`StateRef("manifest")` → Manifest 编译器（`core/bot_config_manifest/compiler/`，
负责校验、拉取、解包、归档），`StateRef("genome", promotion_id)` → 基因组编译器
（`core/bot_genome/compile.py`，在晋升仍然有效时读取已存储的修订版）。修订版不转换
成 Manifest 文档，Manifest 文档也不转换成修订版。契约里的 `compiled.yaml` 基准被一个
等价性测试取代：把每个示例用例按两条路各编译一次并比较状态
（`api-contracts/genome/v1/manifest-v1-compatibility.md`）。

**漂移靠观察。** Bot 是否持有其 `active` 修订版，靠读取 Bot 得出——
`GenomeRegistry.drift` 像 `capture` 那样捕获其状态，计算该状态在 `active` 的策略配置下
会记录成的修订版 id 并比较——从不靠记录投递了什么。apply 报告不携带文档摘要；
晋升行不携带已编译文档、摘要或 apply id；`applied_revision` 与 `by_apply_reference`
已删除。已漂移的 Bot 对自动晋升回答的 409 给出 `active` 与观察到的修订版 id。

**资源根目录。** Bot 的 workspace 根目录就是资源根目录，与资源 Open API 的既有定义一致
（路径一律相对 workspace，不传路径即为根）。声明了资源的 apply 先清空根目录下所有
未受保护的条目，再写入声明的内容，于是上一份文档声明而这一份未声明的路径、以及 Bot
自己写在那里的文件都会被移除（#2162）。受保护条目是一份集中维护的清单
`core/workspace/protected_entries.py`——身份文件、`skills/`、`skills-pool/` 与 `skills-local/`、引擎的
`state/`、`conf/`、`<engine>_conf/`、以及点文件——它们归其他 construct 或引擎所有；
Open API 的只读策略、清空操作和两个编译器读的都是这份清单，两个编译器都会拒绝命名了
其中条目的资源路径。

[ADR 0019](0019-bot-genome-is-the-unit-of-evolution.zh-CN.md) 与
[ADR 0020](0020-promotion-is-platform-owned.zh-CN.md) 中描述已编译文档摘要及其映射回
修订版的部分，由本决策取代；它们关于修订版是什么、谁拥有晋升的决策仍然有效。

设计：[`apps/backend/specs/2026-10-23-apply-engine-desired-state/`](../../apps/backend/specs/2026-10-23-apply-engine-desired-state/)
以及 [`apps/backend/docs/bot-evolution/01-genome.zh-CN.md` §2.7、§5.5](../../apps/backend/docs/bot-evolution/01-genome.zh-CN.md)。

## 后果

- RSI-04 缩减为绑定：一个以 `StateRef("genome", promotion_id)` 启动 apply 运行的
  `RevisionApplier`、能解析 Center 版本与仓库路径的物化器、服务型 Bot 发布路径、
  singlebox。
- 新的期望状态来源（模板、未来的格式）是一个新编译器加一个新状态引用，从不是
  新的物化器或对引擎的改动。
- 每当有调用方询问，漂移的代价是一次捕获（读一次 Bot）；什么都不写。状态读不出来
  的 Bot 也无法说它是同步的（`CaptureRefused` 会向上传播）。
- 声明了 `resources` 的 apply 会替换 workspace 下所有未受保护的条目，包括 Bot 自己写进去的文件；受保护清单就是文档化的边界，清单上的条目一概不碰。
  根目录是文档化的所有权边界，边界之外一概不碰。
- 基因组的晋升表在功能上线前改变了形状（直接在已提交的 DDL 中删除列，而不是新增
  一个迁移）。

## 备选方案

- **把修订版编译成 Manifest 文档再应用。** 否决：该文档需要不属于 Manifest 语法的
  来源写法、需要一个摘要穿过 apply 报告和晋升行、还需要从 apply id 到晋升的关联——
  同一个 Bot 自己就能回答的事实被记录了三遍。
- **通过记录投递的摘要来追踪漂移。** 否决：记录说的是发送了什么，而不是 Bot 持有
  什么；设备上的手工编辑、部分成功的 apply、从别处恢复的 Bot，都会让记录出错而
  Bot 无人观察。
- **为资源建立逐文件的所有权账本。** 否决：根目录是 schema 已经声明的更简单的
  边界，而账本必须与每一条写路径（包括控制台上传）保持同步。
