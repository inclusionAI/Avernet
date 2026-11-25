# StateMachine Group as Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让已发布的 FSM 群在同一 A2A context 内执行多轮独立 Task；并发新请求先到先得，结果可按 Task 查询，节点历史隔离。

**Architecture:** 复用分布式 Gateway、Worker、Connector、TaskFeed。Gateway 负责协议和访问控制；Worker 是受理和持久提交的责任方；纯 Runner 决定节点动作与整轮结果；Connector 只报告下游事实。只新增群调用需要的合同，不让旧 SQL Session 或 HTTP handler 成为第二个执行权来源。

**Tech Stack:** BCS Rust workspace、Axum HTTP+JSON/SSE、`bcs-store-api` 的持久 CAS/stream、BCS DB plugin（SQLite/MySQL）、Cargo conformance、Singlebox。

**Spec:** [FSM 分布式讨论稿](spec.md)；[历史计划 #4190](https://github.com/inclusionAI/Avernet/blob/97a873ccdd33d1dc8d4b804b39a69ea0e7483863/apps/bcs/specs/2026-11-18-distributed-group-as-agent/plan.md)。本文是**供评审的实施计划**。已确认的语义以本地 spec 为准；下列新文件、签名、拆包和决策门槛是实施建议，不是现有 API 或已通过的测试。

## Global Constraints

- 同一 `contextId` 复用分布式 `Sid`；每轮新 `Tid`、新 Run，不续跑上一轮。新请求竞争以持久原子占用为准，输家 `409 / CONTEXT_BUSY`，无 Task、无自动排队，之后同一 messageId 可重发。已受理请求重试先复用原 Task。
- 最终节点 `final_output` 是唯一成功产物来源。提交权威 `Completed` 时 `GetTask(taskId)` 必须已可读且下一轮即可受理；**不能等待 SSE 或异步解锁**。不采用 #4190 的“先公开终态再释放”作为客户端语义。
- 失败/取消必须等所有在途工作停止；停止未知、超时、缺记录和 Worker 重启均不授权开下一轮。不能承诺不支持去重的 Bot 恰好执行一次。
- 历史按授权范围、Session、冻结工作流中的逻辑节点隔离，不按 Bot 聚合。失败/取消轮中真实且持久化的交互仍保留，并标注轮次结果；不是 Artifact。新版工作流或新绑定用新 context。Artifact 过期与节点历史裁剪分别表示。
- 对外执行仅 FSM。排除 ManagerWorker、自由聊天、文件输入、轮内追问和旧 Session 迁移。保留原 JSON-RPC 对旧资源的行为；新增严格来源禁止跨入口读写，包括不带 Sid 的首次身份派生。默认不挂载新服务；真实 provider 与跨进程验收通过才谈上线。

## Review Focus

- 两个 Gateway 的不同请求竞争，输家在前轮结束后用同一 ID 重发仍能成功（T2）。
- 两个逻辑节点绑定同一 Bot，即使该 Bot 自带对话记忆也不能串读（T3、T6、T9）。
- SQL 归档已写、权威完成未提交；或已提交、SSE 未发布。两种窗口不能呈现相同的可见性（T4、T7）。
- 分支失败时兄弟节点或超时重试的旧 attempt 还在远端执行；租约交接不能清除这项义务（T5、T7）。
- 旧 context 的成员换绑或发布被撤销，归档、游标和持续订阅均不能绕过作用域检查（T5、T8）。

---

## 基线、依赖和决策门槛

2026-11-24 复核基线：`origin/dev` 取 `040a6267c40e866a09dcd79a9bea639503b6bca8`（相对上一轮 `82a657e78`，`apps/bcs` 无变化；文档分支共同基点 `df916df94`），`bcs-service-api` 为 7.1.0。Gateway、Inbox producer、Worker 协调与 [Connector #4133](https://github.com/inclusionAI/Avernet/pull/4133) 均已合入。Connector 的[交付记录](../2026-11-17-connector-runtime/validation.md)区分了库级行为、未实现的恢复合同与生产装配；T6 直接扩展现有实现，不再等该 PR。[#4245](https://github.com/inclusionAI/Avernet/pull/4245)（Worker 受理/processor/归档）仍在审，下文 `[#4245]` 路径按 `c7187edcc9` 核对，不当作 dev 已有文件。[#4237](https://github.com/inclusionAI/Avernet/pull/4237) 仍是 Runner 设计。T7/T9 需要真实依赖，不能以库测试代替群调用闭环。

**联合版本门槛（阻塞 T6/T7 集成 GREEN）：** #4245 的依赖范围包括 model、Service API、Connector 的 `report.rs`、Connector store、Inbox、Worker 与协调 provider，不只是 processor。以包含该固定版本合同及配套实现的联合构建为基础；实际合入版本不同须重验并记录 commit/能力矩阵。特别是结果报告采用其 `ConnectorAdmission` / `admit_connector` 合同，保留 attempt 身份、claim fence 和稳定输入身份；不能把 dev 的通用 `admit` 直接接到新版 Inbox，也不能伪造远端 event ID。未完成配套对齐时只允许隔离开发，禁止宣称 T6/T7 已集成。

| 决策点 | 已选设计方向与尚待确认的参数 | 最晚冻结点；不通过时怎么办 |
| --- | --- | --- |
| 权威写者、受理/完成的原子边界 | 由 Worker 的 `ckpt:<sid>` 精确版本 CAS 裁决受理和终态，不另加 Session 锁；#4245 的 input 文档只恢复原请求，不能裁决不同输入争用。 | **T1，前置合同必须先明确；按范围调整可在门禁复核期间继续功能开发。** 记录实际 key/表、CAS/事务范围、栅栏及丢响应读回证据；不能用内存锁或分布式多键事务假设代替。 |
| 重试等价性、线协议与查询预算 | 严格入口用 `(tenant, principal, group, messageId)`；完整规范化输入、显式 context、有效配置和扩展入指纹。建议采用 #3979 HTTP+JSON 与 #4190 的有界读取；不改变 JSON-RPC。 | T1/D0 冻结重试样例、分页与历史投影预算及其内部事件表示；T8 冻结等待时长、错误和扩展并验证预算。未冻结不发布 Card。 |
| 首批工作流及 Bot 恢复能力 | 建议 BotTask 无环图、唯一最终节点，串行/扇出汇合；排除 Judge、Loop、HumanInput。送达未知时仅允许有持久去重保证且仍在其有效窗口内按原身份重发；只有查询能力不允许重发。 | T5/T6：决定拒绝发布该类 Bot 还是允许挂起并核实。Judge 如必需，应另立有恢复证据的扩展，不插进本批 reducer。 |
| 历史、Artifact、请求墓碑及 Blob 保留 | 分开保存期限；固定历史读边界、裁剪标识；在引用解除前不能回收。#4190 的 8 MiB 是候选的**公开响应/单帧**预算，不是所有 Worker 的正文配额。 | T3/T4 定裁剪与过期合同；T9 前定期限、容量、未知停止告警、核实与人工恢复依据。无依据不解锁；无保留政策不启用自动删除。 |
| 群开放、认证和撤销后的访问 | 只从可信认证链取 scope；显式发布；Card 只声明已实现能力。当前授权与冻结的工作流版本分别检查。 | T1/D0 冻结、T8 验证 Card、send、历史读取、订阅和 cancel 的权限矩阵（含既有订阅撤权）。没有明确授权就不开放对应入口。 |

## 交付范围与执行授权（2026-11-25 调整）

按用户决定，将 **Group as an Agent 功能实现**与 **存储 provider 实现/认证**分开交付。本计划负责 T1～T8、W，以及 T9 的配置接缝、验收程序和功能装配；不负责开发或认证具体协调/DB provider。P 的实现、部署配置、存储服务重启/HA、时钟与保留保障、容量认证由独立 provider 工作流交付。内部实现不得复制进公开仓库。

- **现在允许继续功能实现**：复用 `7c4d97a648` 的修复与验证，从 T2～T5/W 的功能缺口核对推进到 T6/T7，再到 T8/T9 装配；先落实每个任务实际消费的前置合同，不要求等 P 认证。D0 门禁复核并行推进；这项开发授权不表示 D0 已冻结或 Stage 2 已完成。
- **功能完成**：真实业务实现、集中合同、故障注入、受影响测试与适用代码门禁通过。协调存储可注入合同级测试后端，SQLite 可验证 SQL 行为；测试报告必须标明后端，不将业务组件替身当作已经实现的功能。
- **真实后端验收**：同一业务实现对已选 provider 跑合同和集成场景；真实 MySQL 升级/repo 验收仍需完成，环境缺失时列为待验收，不阻塞不依赖该环境的编码，也不称 MySQL 已通过。
- **部署放行**：D0 复核、具体后端认证、真实跨进程故障矩阵和安全/容量门槛全部满足，另行授权。缺能力仍启动失败，禁止在运行时以 Model 或内存实现自动降级。
- 本节及下方阶段表优先于任务中旧的 P 阻塞措辞：这些阻塞改指对应的真实后端验收/部署，而非功能开发。实际协议矛盾、未知停止、无权威证明等正确性问题仍阻塞依赖它的功能，不得借拆分范围延期掩盖。

## 依赖合同（实现与验收分轨）

**P：跨进程持久协调 provider（独立交付；阻塞真实后端验收和部署，不阻塞本计划功能开发）。** 交付责任在部署侧存储 provider，不在 HTTP 或 SQL 归档实现。当前基线的 Document/Stream 实现是测试模型或包装器；SQLite/MySQL 能保存归档不证明其已满足协调合同。接入方须交付可由两个独立进程连接同一隔离 namespace 的具体插件和启动配置，列出版本、能力（document exact-version CAS、`PersistentJsonMerge`、Stream append/读回及 `StreamGroups`、KV、ZSet、membership 与 #4245 的 `RetainedLease`；完整 `AtomicJson` 仅在实际消费者需要其操作时要求，不能把 DocumentCas/PersistentJsonMerge 等同于完整 AtomicJson）及保留策略。`StrictLease` 或到期删键不等价于 `RetainedLease`：终止记录须按固定版本合同保留，使 membership 能区分 `Dead` 与缺记录的 `Unknown`；仍有 Dispatch/接管义务引用时不得清掉死亡证明。验证 holder 终止、provider 重启后仍读到 `Terminated` 并判定 `Dead`，未知/缺失记录不得被当成可接管证明。用 Worker controller 与 Connector claim 并发更新同一 Dispatch：CAS 冲突可重试，双方已确认字段不得相互覆盖；验证 consumer-group pending 消息的 reclaim、ACK 后不再交付及服务重启读回。缺任一必需能力时联合角色启动应失败。对该实现执行 `bcs-store-api` 对应合同、`bcs-dist-store` 受理/协调 harness；同一套实例验证旧 owner 提交被拒、丢响应按身份读回、持久服务重启不丢已确认状态。T9 的 `validation.md` 必须记录运行命令、provider 版本和故障结果；缺交付就标 blocked，不用每进程内存或仅 SQL 归档补位。

**W：不可变工作流修订目录（本计划交付，T5 校验后、T7/T9 真实重启前）。**

- [ ] 新增 `crates/services/bcs-dist-store/src/workflow_revision.rs`、双方言新迁移及 `tests/conformance_workflow_revision.rs`；通过 DB plugin 原子保存不可变 `(scope, group, revision, digest)` 的定义、编译版本、绑定清单和当前发布指针。同一 revision 异内容拒绝；先有可验证对象再让 `resolve` 返回它，不能原地覆盖。
- [ ] 实现 `DistGroupDirectoryPort::{resolve,load_workflow}` 及发布写侧，Session 固定引用、活动/可恢复 context 的修订不回收；加载校验 scope 和 digest，缺失、旧格式或损坏时明确报错。测试 `publish_v2_restart_loads_pinned_v1`：发布 v1 → 创建 S → 发布 v2 → 清空进程缓存重启；S 继续用 v1 原绑定，新 context 用 v2；删掉/篡改 v1 不得回退 v2。
- [ ] RED/GREEN：`cargo test -p bcs-dist-store --test conformance_workflow_revision`，按 R25 调真实目录 contract harness，并跑双方言迁移；W 未交付不装配 fixture 作为群目录。

## 分阶段执行与放行门槛

Stage 表达交付顺序，T1～T9 保留为下文的实现任务编号；两者不重复计算完成度。当前状态为 **D0 冻结结论仍待复核，Stage 2 已有实现；按本次范围调整允许继续后续功能开发，阶段完成与真实后端验收分别记录**。已有模型、正向原型和测试继续复用，不从零重写，也不把候选接口视作冻结合同。每次推进在 [validation.md](validation.md) 记录具体版本、命令、通过/失败/跳过项及证据层级。

| Stage | 目标与任务映射 | 进入条件 | 退出条件 |
| --- | --- | --- | --- |
| **1：完成 T1，验证并冻结 D0** | S1.1～S1.5，见下文；对应 T1 及合同决策 | 当前即可继续 | 五项全部完成，冻结记录可追溯，无未裁决的协议缺口 |
| **2：基础持久化与纯执行** | T2、T3、T4、T5、W；不含 P 开发 | 所消费的 T1 合同明确；D0 复核可并行 | 业务实现、集中合同、可运行迁移与 W 修订恢复通过；真实 MySQL/P 验收另列，不冒充通过 |
| **3：下游执行与 Worker 闭环** | T6、T7 | 所依赖的 Stage 2 功能合同可用，配套版本可构建；不等待 P 认证 | 真实业务组件的完整结果交付、停止屏障、隔离核实、独立 Feed 和跨轮恢复测试通过；后端类型明确，真实 provider 故障验收另列 |
| **4：公开协议与访问边界** | T8 | 所需 Stage 3 业务链可用 | HTTP/Card/Get/List/SSE/cancel 与授权负测、旧 RPC 回归通过；未验收部署保持关闭 |
| **5：装配与验收交接** | T9 的配置、验收程序与功能装配；消费独立 P 交付 | Stage 2～4 功能合同可用 | 功能装配与可复用验收程序交付；真实 Singlebox 跨进程/后端重启/容量结果单独记账，缺 P 标待验收；部署另行授权 |

**并行规则：** D0 剩余门禁复核与功能实现并行；T3/T5 可并行，T2/T4 对齐联合合同，W 复用 T5 校验。T6/T7 只消费已明确且有测试的前置合同；遇到协议缺口先修合同及测试。T8 纯协议测试可提前开展，完整功能集成等待所需业务链。P 独立推进，缺 P 不阻塞编码，但对应真实验收和部署始终保持 blocked。

### Stage 1：五项冻结工作包

本阶段完成的是**可实施的合同和可执行的正向协议证据**，不是提前实现全部 T2～T9。模型/模拟接收端可以验证算法和故障切面，但必须指出与真实合同的映射；不能用测试中的布尔值替代未实现的外部效果。完整生产 provider 和跨进程部署验收留在后续阶段；会改变协议可行性的能力缺失不能推迟。

#### S1.1 请求、受理与证明生命周期

**归属：** T1；后续由 T2/T4/T7 实现生产存储。
**主要位置：** `group_round.rs` 模型及 repo、集中 `group_round_repo_contract_tests`、`bcs-dist-store/tests/group_round_support/{reservation,lifecycle}.rs` 与对应测试。

- [ ] 明确原请求、准备/废弃记录、Accepted、终态证明、冷证明与过期墓碑的物理 key、唯一写者和 CAS/转存边界；保持 checkpoint 为唯一执行权来源。
- [ ] 在现有原型上完成“受理 → 完成 → 冷转存 → 读回确认 → checkpoint 压缩 → owner 交接 → 原请求重试 → 结果过期”的测试；未提交不可公开，过期不能重跑，旧写不能复活。
- [ ] 补齐预留未决、同键竞争、失败释放、无活跃 owner 激活以及 Accepted 后无 hint 的自动发现；不手工注入已知 Sid 代替发现。回收前先证明旧提交已被栅栏阻断，不能直接删键或仅靠 TTL。
- [ ] 对目录、请求记录、历史证明和单记录字节量给出有界方案；覆盖容量满时拒绝、恢复及回收，不把原型固定 16 项的上限作为生产持续运行证明。

**退出证据：** 生命周期与全部写入窗口的 RED→GREEN，跨轮/接管/压缩后原请求仍恢复同一 Tid；以证据证明安全回收，或明确拒绝回收的容量限制。

#### S1.2 公共列表快照与游标

**归属：** T1；后续由 T4/T8 实现生产索引与协议。
**主要位置：** `group_round_support/listing.rs`、列表合同/集中测试、`contract-decisions.md`。

- [ ] 完善双重采集或等价覆盖屏障，证明包含查询开始前已提交的所有 Task；两个 Sid 并发提交与索引延迟时，不能返回遗漏数据的 total。
- [ ] 用不可变 cut 实现可执行的分页原型：固定排序 `(status_updated_at DESC, tid ASC)`、筛选、total、行投影与页大小；后页不能重新读取实时状态。
- [ ] 游标绑定 scope/group、筛选、cut、末键和有效期；覆盖跨主体、篡改、撤权、过期、版本清理，以及热转冷/新增/状态更新与翻页交错。
- [ ] 给出快照保留、到期拒绝、最大扫描/批量读取成本；依赖不足时有界报错，不无限等索引追平。

**退出证据：** 跨页不重复、不漏项、total 不变；权限或版本失效必须拒绝续页；模型测试和真实索引待实现部分分别说明。

#### S1.3 完整编码预算与失败收尾

**归属：** T1；后续由 T4/T6/T7/T8 落实各边界。
**主要位置：** `RoundBudgetProfile`、test-support 的有界读取参考、投影/Feed 编码夹具和停止证据测试。

- [ ] 确定默认/非默认投影、历史、分页、入站、内部事件/批与公开响应的 profile；候选值只有通过实际编码验证才冻结。
- [ ] 同一组有序 Artifact/Parts 贯穿入站、内部 Feed 和公开 Get/SSE 编码，包含外壳、JSON 转义、历史、摘要引用和解引用；覆盖 B−1/B/B+1，不只验证裸字节读取。
- [ ] 大产物引用在发布/重放完成前保持 pin，覆盖内容损坏、引用缺失和恢复；内部事件能写不等于公开帧能完整还原。
- [ ] 超大中间结果产生有界业务失败，保留独立核验的停止事实；有兄弟 attempt 未停止时仍不得提交整轮终态。补齐已有 `oversized_intermediate_result_is_bounded_failure` 的完整屏障场景。

**退出证据：** 成功提交的产物可以完整读回；超限不截断为成功、不走存储损坏隔离，也不因结果读取失败伪造停止。

#### S1.4 联合版本与合同映射

**归属：** T1 接口对齐；生产 Connector/Worker 改造仍属 T6/T7。
**主要位置：** model、Service API、集中 harness、锁定依赖的 Connector report / Inbox / Worker / TaskFeed 合同；候选变更记录在合同决策中。

- [ ] 固定可复现的联合版本或逐项最小适配清单，列出原型到真实合同的字段、调用和能力映射；不能只锁 Worker 而混用不兼容 Connector/Inbox。
- [ ] 验证 `ConnectorAdmission/admit_connector`、typed `GroupStart`、终态/停止证据及两种 Feed 发布许可的消费者兼容性；未知事件 ID 不得靠伪造补齐。
- [ ] 通过合同级夹具证明所选 CAS/所有权/原子范围可表达原型，不假设多键事务；对需要修改的正式合同更新集中测试和兼容性分析。
- [ ] 明确剩余实际装配验证交给哪个 Stage/T 任务；若映射本身不成立，回到 S1.1～S1.3 修订，不能登记成后续普通实现事项后放行。

**退出证据：** 版本/能力/消费者矩阵与可运行的合同兼容测试，无未解决的接口冲突。此门槛不要求全部真实 provider 和跨进程部署已经完成。

#### S1.5 门禁、审查与 D0 冻结记录

**归属：** T1 最终交付；消费 S1.1～S1.4 的证据。

- [ ] 执行全部 T1 测试、受影响模块套件、`cargo check --workspace --all-targets`、API/R25 等适用门禁；确认执行了预期测试，不能把零测试、SKIP 或包装脚本 exit 0 算通过。
- [ ] 修复或替换旧 API 检查器的不兼容调用，形成真实基线/工作树公共 API 差异及版本传播分析；不在脏工作区执行强制 checkout。接口导出本身不能替代差异门禁。
- [ ] 完成最终审查和对应修复验证，逐项核对 S1.1～S1.4；全量测试失败、中止或未跑部分必须列出原因与影响，不能以受影响套件通过冒充全量通过。必需冻结检查 blocked 时保持 D0 未冻结。
- [ ] 资源受限时保持 `-j 1` / 单测试线程、日志落盘和资源监测；触及保护阈值停止全量构建。共享缓存清理需明确授权，不通过盲目重跑消耗剩余磁盘。
- [ ] 在 `contract-decisions.md` 记录冻结版本、参数、权限/撤权矩阵、协议与测试映射，在 `validation.md` 记录实际命令/结果/限制；只有所有必需项通过、重要审查问题闭环后，才将 D0 改为已冻结并放行 Stage 2。

**提交与部署边界：** 阶段完成不自动授权 git commit、push、合并或部署；保留既有用户授权边界。D0 冻结是设计实施门槛，不是上线批准。

### 后续 Stage 的具体交付

- **Stage 2（T2/T3/T4/T5/W）：** 将 Stage 1 协议落到真实 repo、双方言持久化、冻结历史输入、工作流修订及纯 Runner。P 独立交付；缺 P 不阻塞本阶段功能完成，但 T2 provider GREEN 与实际后端验收单独保持 blocked。不可把原型 fixture 当作 W 或生产存储。
- **Stage 3（T6/T7）：** 接通真实下游、Connector/Inbox/Runner、结果归档、统一停止屏障、核实队列、Supervisor、独立 Feed 及目录回收。执行实际 A/B 双轮和各故障窗口；同时验证 Stage 1 的合同没有被实现弱化。
- **Stage 4（T8）：** 将已验证能力映射到公开 HTTP+JSON/Card/SSE，完成来源隔离、当前授权、快照分页与旧 RPC 回归。仅在全部相应能力已实现时声明 Card，不因阶段编号扩大公开范围。
- **Stage 5（T9）：** 使用真实 P/W 和独立进程验证死亡接管、provider 重启、停止未知、目录持续容量与 Feed 持续故障；记录未开放能力和告警/恢复方案。任何上线动作仍需单独授权。

下文保留任务级文件、接口与测试细节；执行时同时遵守所属 Stage 的门槛。S1 工作包补全 T1 的冻结责任，不删除后续 T 任务的生产验收，也不要求同一测试结果重复执行来凑阶段完成度。

## 文件与实施顺序

路径均相对 `apps/bcs/`；Cargo 命令在该目录执行。除标明的现有文件外，`新增` 文件和所列 `--test` 目标是**计划产物**。各任务先创建测试并运行得 RED，不能把“零项测试”当通过；每次 GREEN 后形成单独可审查提交，不混入全局格式化。

| 任务 | 文件职责 | 依赖 |
| --- | --- | --- |
| T1 共享合同与夹具 | `crates/contracts/bcs-dist-model/src/group_round.rs`、Service API 分布式 repo 与集中 conformance | 先做 |
| T2 受理、许可、调度 | `bcs-dist-store` 的群占用与 Inbox，`bcs-dist-worker` 的 canonical admission | T1 + #4245 接口对齐 |
| T3 节点历史 | 节点历史 repo、DB provider、冻结读取及裁剪 | T1 |
| T4 历史产物 | 扩展 #4245 archive，按 Task 保存/读取、过期、Blob 引用和 GC | T1 + 归档合同 |
| T5 工作流与纯执行 | 共享冻结能力校验、确定性 FSM reducer | T1；可用 fixture |
| T6 下游执行证据 | Connector 的节点会话隔离、送达未知、停止事实 | T1/T3/T5；对齐 #4245 联合版本的 Connector/Inbox 合同 |
| T7 Worker 完成桥接 | processor、持久 outbox、按权威 cut 读取和接管 | T2～T6 + #4245 |
| T8 Gateway | application、Card、HTTP+JSON、查询及 SSE | T1 后可先用替身；真实读写依赖 T2/T4/T7 |
| T9 装配与故障验收 | bootstrap 配置、真实角色、Singlebox 跨进程 | T1～T8 + P/W 的交付证据 |

### T1：冻结跨进程合同，不先写 HTTP handler

**执行状态（2026-11-25）：D0 原冻结结论暂停，修复与门禁重新核定中。** 已有 wire、正向协议原型、候选 repo/Runner 接口及审查修复证据见 [T1 验证记录](validation.md)。候选接口已进入工作树不代表已冻结；剩余工作按 S1.1～S1.5 收口。旧 `resolve_commit` 的反例只是基线能力探查，不是必须改变旧 API 的冻结门槛。模型通过不等于真实 provider/联合版本验收通过；后续功能开发按“交付范围与执行授权”推进，不据此恢复 D0 冻结。

**文件：** 新增 `crates/contracts/bcs-dist-model/src/group_round.rs`、`crates/service-api/bcs-service-api/src/port/repo/distributed/group_round.rs`、`crates/test-support/bcs-test-support/src/contract/distributed/group_round.rs`、`crates/contracts/bcs-dist-model/tests/group_round_wire.rs`；修改各模块导出、`src/core/dist_runner.rs`（Service API）、相应 Cargo.toml 和 `Cargo.lock`。

**接口草案（Stage 1 收口）：** `RoundKey { scope: TaskScope, group: GroupId, sid: Sid, tid: Tid, generation: NonZeroU64 }`（当前候选实现显式拒绝零代次）；`RoundInput { key, ordered_parts, workflow_ref, history_cut, execution_deadline }`；`Admission::{New, Replay, Busy, Conflict, Expired}`（New/Replay 带原身份和返回配置）；`TerminalProof` 绑定 RoundKey、受保护提交身份与结果/收尾证据。协议 DTO 不进模型；任何已有状态不能靠补默认 scope 或缺省 generation 自动升级。

- [ ] 写 `frozen_identity_survives_reload` 和 `old_shape_cannot_guess_owner`，断言 `assert_eq!(reload(&input)?, input)`，旧格式缺 scope/generation 必须显式拒读。集中 harness 命名按 `<trait_snake>_contract_tests`。
- [ ] RED：`cargo test -p bcs-dist-model --test group_round_wire`；预期缺类型或 wire 断言失败。
- [ ] 按 [D0 合同决策](contract-decisions.md) 做 A/B 双轮原型：首次创建 checkpoint、无 owner 受理、旧 owner 迟到写；在 `Accepted` 后未发 hint、归档后未提交、`Completed` 后未首次 append 三处停机，分别断言可执行的 Tid、Get 可见性和下一轮能否实际派发。列出无 hint 的发现路径及请求跨轮读回证据；无法证明则阻塞 T2/T7，不另加 Gateway 锁。重试与远端证据向量可并行。通过评审后再同步模型、Service API 与消费者。
- [ ] 用两个 Sid 的并发写者验证列表 cut：A 的终态在首页查询前已提交，故意延迟其索引写入并推进另一 Sid。只有能证明覆盖 A 才可返回页面及 total，否则有界等待后报依赖错误；不接受仅用最近更新时间证明追平。D0 在 `contract-decisions.md` 同时冻结 Accepted/唤醒/列表 cut 的因果关系、权限矩阵和撤权点、分页快照期限及默认/非默认投影预算；T8 验证这些值，不延后到装配再决定。T4/T7 用同一产物验证公开预算和 #4245 `task_feed/{storage,append}.rs` 的内部编码上限（128 KiB/事件、256 KiB/批）：超过内联预算用带 digest 的归档引用，pin 至补发完成；引用和批外壳也必须通过校验，否则不提交成功。T8 按同一 digest 还原公开帧。群详情投影不能替代 W 或公共 Task 索引。
- [ ] 同时冻结 Connector → Inbox → Worker → Runner 的入站预算：#4245 `bcs-dist-model/src/input.rs` 的 `PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES` 为 8 MiB，`processor/prepare.rs` 会在 reduce 前解引用，不能用小 Feed 引用绕过此限额。T6 对下游响应从 IO 起限长，完整结果编码连同事件外壳须满足冻结的入站预算；T7 校验引用与实际解码大小。超过限额先独立持久化经身份核验的停止事实，再用有界、稳定身份的超限失败事件收尾，不把未读完的响应当停止证明，不让业务超限落入 `store_invalid`/损坏隔离。`oversized_intermediate_result_is_bounded_failure` 覆盖 9 MiB 中间产物、几 KB 候选最终产物和入站 B−1/B/B+1；大对象不得先完整载入内存，超限不得继续执行依赖该结果的节点，未停兄弟仍阻止终态。同步变更模型、Connector report、Inbox 校验、Worker 解引用及集中合同，不全局放宽通用输入上限。
- [ ] 冻结 Session `source`、分域的首次 Sid/Tid 派生及 T8 的跨入口拒绝。T1/T2 增加 `replay_before_current_capabilities`：当前授权 → 按稳定请求键查原记录 → 用首次保存的规范化依据判等/恢复；仅确定未命中才按当前目录验证新请求，预留竞争失败重读赢家。A 丢回包后改默认值或下线能力，重试仍取 A；撤销访问则不能读 A。`Accepted` 同时授权执行和公开读取，预写数据没有此资格。
- [ ] GREEN：重跑 RED 命令；`cargo check --workspace --all-targets`；`bash scripts/ci/check-public-api.sh` 并单独检查 `bcs-dist-model` 的版本/序列化兼容性；门禁缺工具或跳过不能记作通过。
- [ ] 提交 `feat(bcs): define recoverable FSM group round contracts`。

### T2：并发先到先得，BUSY 不产生可执行任务

**文件：** 新增 `crates/services/bcs-dist-store/src/group_admission.rs`、`crates/services/bcs-dist-store/tests/conformance_group_round.rs`；扩展 `crates/services/bcs-dist-store/src/inbox/producer.rs`、`crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs` 与 `crates/services/bcs-dist-worker/src/admission.rs` [#4245]。**接口：** T1 的 `GroupRoundRepo::admit(&GroupStartRequest) -> Result<Admission, StoreError>`（异步）；恢复取得原 `RoundKey`，不重分配 Tid。Gateway 只提交可信 scope 与稳定意图。

- [ ] 写 `two_gateways_one_round`：`assert_eq!((new_count, busy_count, executable_starts), (1, 1, 1))`；原胜者重试取原 Tid；前轮完成后输家按原 messageId 新受理。跨群、跨主体和客户端指定未知 Sid 不创建 Session。
- [ ] RED：`cargo test -p bcs-dist-store --test conformance_group_round`，先观察并发或恢复断言失败。
- [ ] 实现 T1 批准的受理协议。先准备原请求，再通过 `ckpt:<sid>` 的 owner fence 和版本 CAS 同时登记许可与调度义务，最后补 Inbox/schedule。逐步注入宕机与丢回包；旧准备、迟到 append、重复 Start 均须核对原代次，争用输家不得自动补跑。记录重复物理输入与唯一业务轮次，不要求流中只有一条记录。
- [ ] `prepared_snapshot_is_not_a_public_task`：在 producer 已写初始快照和列表索引、尚未授予许可处停机。T2 为 T8 提供按 accepted cut 的读过滤；猜中 Tid 的 Get/subscribe/cancel 与未受理资源一致，List/total 不含它。覆盖 abandon 后重启仍不可见、成功恢复只显现一次，以及 REST 忙 Session 遭旧 RPC 提交不产生任何物理写入。
- [ ] GREEN：重跑该目标及 `cargo test -p bcs-dist-store --test admission`；对实际 provider 执行同一 harness，验证逐次故障后不会留下永久忙或未通知的胜者。
- [ ] 提交 `feat(bcs): admit one recoverable FSM round per context`。

### T3：持久化节点自己的历史，而不是 Session 群聊历史

**文件：** 新增 `crates/service-api/bcs-service-api/src/port/repo/distributed/node_history.rs`、`crates/services/bcs-dist-store/src/node_history.rs`、`crates/services/bcs-dist-store/tests/conformance_node_history.rs`；在 `migrations/{sqlite,mysql}/` 各加下一可用编号的 `fsm_node_history.sql`，在 `crates/services/bcs-db-migrations/src/lib.rs` 对应现行机制登记。**接口：** `NodeHistoryKey(scope, group, sid, workflow_ref, node_id)`；`append_once(key, interaction_id, entry)`，`read_at(key, cut, budget) -> HistoryPage { entries, truncated }`；两个方法为 repo 异步读写，失败返回 `StoreError`。`cut` 取受理时已提交的历史边界，取 cut 与保护该历史版本必须同一一致性操作。新增 `freeze_node_input(round, node, cut, budget) -> PinnedHistory`，持久记录选中条目/内容的不可变引用、有序 digest 和裁剪标志，后续派发只加载已验证的冻结版本；物化完成前保留版本 pin，终态且无可重放派发后才释放输入 pin。

- [ ] 写 `same_bot_two_nodes_remain_private`：研究和评审都绑定 Bot X，分别写入私有标记；`assert!(!review_history.contains(research_marker))`；新 Sid 为空。另测失败/取消轮中真实交互带所属轮次状态，不伪装成功 Artifact；重试相同交互只记一次。
- [ ] RED：`cargo test -p bcs-dist-store --test conformance_node_history`，旧的共享历史或按 Bot 键实现必须失败。
- [ ] 保存实际发送给节点的输入和它的实际响应，区分未送达/未知；用稳定交互身份去重，轮次终态通过可验证引用或一致索引关联。显式上游绑定作为**本轮输入**，不开放跨节点历史遍历。按固定 cut 批量加载有界历史；裁剪后 `truncated=true` 而非新历史。接管后不得读入同轮重试刚写的记录。
- [ ] `trim_during_takeover_replays_exact_input`：选定两条历史，在派发提交前崩溃；并发执行 retention 和接管。比较字节、顺序、digest、`truncated` 均不变，后续新轮可见裁剪。冻结阶段异常不能退回用同一 cut 裸查。
- [ ] GREEN：运行该目标、`cargo test -p bcs-db-migrations` 及 SQLite/真实 MySQL 的迁移升级和 repo harness。归档/历史迁移在首次提交前协调编号，不修改历史文件。
- [ ] 提交 `feat(bcs): isolate persisted interaction history by FSM node`。

### T4：按 Task 归档最终产物，不让未提交的结果提前可见

**文件：** `crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs`、`crates/services/bcs-dist-store/src/archive/sql.rs` [#4245]；新增 `crates/services/bcs-dist-store/tests/group_artifact_archive.rs`、两种方言的 `fsm_task_artifacts.sql` 增量迁移，复用原归档表/DB plugin。**接口：** 扩展 `ResultStorePort::save_final` 的版本化 `FinalTaskRecord`：完整公开历史、稳定 result digest、保序 Artifact/Parts 和 `RoundKey`；产物唯一键 `(tid, artifact_id)`。归档成功不自动获得公开资格。

- [ ] 写 `archive_all_parts_or_none`、`old_tid_not_overwritten`：第二个产物写失败后不出现半份归档；同内容重放得到相同 ID/顺序，异内容冲突；A、B 两轮的文本/JSON 按各自 Tid 查询。
- [ ] RED：`cargo test -p bcs-dist-store --test group_artifact_archive`；先验证无事务或按 Session 更新会失败。
- [ ] 用同一事务写最终记录、产物和不可变 Blob 引用；按 scope/group/Tid 读，列表先分页再批量取产物；准备中的归档只供恢复。实现独立的过期标记和幂等回收：pin 在写对象前登记，未决/隔离或仍被引用不删；对象读取从 IO 开始限长，不能先全量解码。区分 Artifact 已过期、未授权、历史裁剪。授权校验先于披露过期。
- [ ] 和 T8 实现 D0 冻结的公开投影编码/大小校验合同（wire 类型仍在 adapter，由宿主注入纯校验器）。T4/T7 在提交成功前限长编码完整默认 GetTask 及必需状态/Artifact SSE 帧，包含外壳、转义、history 和所有 Part；保存预算版本、投影摘要和已校验的大小。`blob_fits_but_wire_overflows`：裸 Blob 小于 B，最终 JSON 为 B−1/B/B+1，前两者可读；B+1 只能进入有界失败收尾，绝不公开 Completed。
- [ ] 给 T7 的 `TerminalProof` 加按 Task 的持久读/幂等转存：作用域与 `Tid/generation/commit_id/status/result_ref/digest` 都一致才确认。未提交归档无证明；压缩前须确认等价冷证明可读。`compacted_proof_survives_hot_expiry`：A 归档/提交，B 推进、压缩、清热快照并重启，仍可查 A；对照仅归档未提交的 A′ 始终不可见为成功。清理时先保证 expired 墓碑可读再删除证明和结果，不能出现可重新执行的间隙。
- [ ] GREEN：同一目标覆盖 Blob put 后、事务前宕机，删除回复丢失，活动引用保留；运行与 T3 相同的双方言升级验证。限额采用 D0 冻结的 profile，测试用显式小预算覆盖 `B-1/B/B+1`，不把测试值作为默认配置。
- [ ] 提交 `feat(bcs): archive immutable FSM results per task`。

### T5：共享能力校验与确定性 FSM Runner

**文件：** 新增 `crates/services/bcs-dist-runner-fsm/{Cargo.toml,src/lib.rs,src/validator.rs,src/reducer.rs,tests/fsm_round.rs}`；共享 validator 的 trait 属于 `crates/service-api/bcs-service-api/src/core/dist_runner.rs`，供群目录和 Runner 复用。参考 `crates/services/bcs-collaboration-runtime/src/definition.rs` 的最终节点规则，不嵌入旧 runtime 的网络/数据库调用。**接口：** `validate(frozen: &SessionSpec, workflow: &StateMachineDefinition, supported: &FsmCapabilities) -> Result<ValidatedWorkflow, RunnerError>`；复用 `DistRunnerCoreService::{init,reduce}`，T1 的 typed Start、固定 `now` 和已解析节点输入进入 `EvaluationContext`。

- [ ] 写 `fork_join_uses_designated_final`：交换两分支的回包顺序，最终产物仍取显式 `final_output`；相同状态和有序输入重放满足 `assert_eq!(decision1, decision2)`。缺失唯一输出、不支持节点拒绝发布且不创建 Task。旧 context 重放仍用原 workflow/binding，新 context 才用新版。
- [ ] RED：`cargo test -p bcs-dist-runner-fsm --test fsm_round`。先注册最小测试目标，再观察上述行为失败。
- [ ] 实现冻结校验、串行/扇出汇合/有界重试、稳定节点 attempt 身份与输出模式选择。纯归约只产出派发/取消/完成意图；并行 A 失败而 B 在途时不输出整轮终态，旧 attempt 未核实时不能借新 attempt 释放占用。
- [ ] `unexpected_interaction_enters_stop_barrier`：BotTask 运行时返回 `INPUT_REQUIRED` 或 `AUTH_REQUIRED`，T6 报告原始状态，严格来源的 reducer 立即冻结失败原因、停止派发并请求取消，不进入通用等待输入路径；仍有在途调用时不产出终态，全部停稳后只提交一次失败。
- [ ] 新增 `crates/services/bcs-dist-runner-fsm/tests/conformance_dist_runner.rs`，以具体生产 Runner 类型调用 `dist_runner_core_service_contract_tests`。GREEN 同时运行 `cargo test -p bcs-dist-runner-fsm --test conformance_dist_runner` 与 `bash scripts/ci/check-r25-conformance.sh`；功能用例和共享 validator 的合同各自保留。
- [ ] GREEN：重跑目标，追加失败/取消竞态和含 Judge/HumanInput 的拒绝测试。将共享 trait 的真实实现接入 R25，而非在 Gateway 重写节点白名单。
- [ ] 提交 `feat(bcs): reduce frozen FSM workflows into recoverable actions`。

### T6：下游会话也按节点隔离，送达未知不盲发

**文件：** 复用 `crates/service-api/bcs-service-api/src/core/dist_connector.rs`、`crates/services/bcs-dist-connector/src/{rules.rs,send.rs,call.rs}` 和 `crates/services/bcs-dist-connector-store/` 的派发认领与命令流。修改 `crates/service-api/bcs-service-api/src/port/dist_agent.rs` 及 `crates/external-clients/bcs-a2a-http/src/dist_agent/mod.rs` 的 resolver 和调用点：严格来源必须传可信 `CallAuthority { round, node, attempt, binding, operation, remote_ref }`（首次 start 尚无远端引用），操作区分 start/resend/continue/query/subscribe/cancel；用当前授权解析并使用同一获准目标，不用客户端 metadata 或仅含 `AgentBinding` 的回调代替。新增 `crates/services/bcs-dist-connector/tests/group_node_delivery.rs`。**接口：** 沿用 `DistAgentClientPort::{start,query,cancel}`；`StableSendRequest` 携带节点身份、原 attempt 及该节点的冻结历史输入。将可用停止事实映射到 Worker 输入，而非由 Connector 决定群是否完成。

- [ ] 写 `downstream_accept_then_drop_reply` 和 `same_bot_distinct_node_conversations`：可控 Bot 接单后丢回复；不可查且未协商持久去重时 `assert_eq!(send_attempts, 1)`，不假报停止；同 Bot 两节点不能复用导致串读的远端会话。明确 BCS 提供的历史和远端续聊谁负责上下文，禁止两份历史重复拼接。
- [ ] RED：创建新目标后运行 `cargo test -p bcs-dist-connector --test group_node_delivery`，让缺失的节点隔离、派发前复核或未决上报断言失败；已有通用重发/查询用例不是此处的 RED。
- [ ] 在已合入的 `uncertain_start`（`Resend` / `AskAgent` / `Unresolved`）与明确未送达信号上验证四类证据，不重写发送器。已拒绝的重复发送不能反证原调用未送达；查询暂时 NotFound 不证明远端已停止。`src/delayed.rs` 的 D2（未决上报）、D3（死 holder 的重投）和 D8（drain 交接）仍是延迟合同：D2 接到持久监督义务。ACK 后死 holder 的恢复由 T7/T9 强制装配 #4245 Worker Supervisor 完成，不依赖 Connector 自身 D3；D8 主动 drain 可单独保持关闭。
- [ ] 在首次网络 I/O 前持久化 `SendProof`（目标去重域、保证截止时间、操作、请求 ID、规范化正文及发送配置摘要）。在 `bcs-dist-model/src/session.rs` 的 `DownstreamCapabilities` 能力合同及 `rules.rs` 中使严格来源不能仅凭 `idempotent_start/input` 重发。`takeover_rechecks_dedupe_window_and_domain`：窗口内同域同语义且授权仍有效才可依赖去重重发；过期、时间不确定、同 credential ref 解析到异域、有效配置改变均不增加发送次数。`shared_binding_distinct_grants`：研究节点被撤销新数据委托、评审节点仍能按独立授权查询/取消自己的原 Task，不能交换节点或操作。
- [ ] 在 `call.rs` 流消费、`send.rs` query 及 adapter cancel 映射之前，核对首次 accepted 所绑定的目标域、远端 task/context、当前操作与本地 did/attempt。`wrong_remote_ref_is_not_stop_evidence` 分别送入另一任务的查询结果、终态流帧、取消回执；不保存为 A 的结果或停止事实，T7 仍拒绝释放 A。
- [ ] `normal_stream_final_requires_complete_result` 与 `lost_artifact_tail_recovers_by_get`：T5 发布校验要求每个产出节点能按已确认身份取回完整终态快照（含全部有序 Artifact/Parts）。修改 `bcs-a2a-http/src/dist_agent/events.rs`、Connector `report.rs`/`send.rs`/`call.rs`：正常流的 `Completed` 状态仅提供经身份核验的停止事实，不能立即落成产物 `Final` 或结束结果恢复。正常完成和断流接管统一从完整 GetTask 校验清单、顺序、内容摘要及 T1 入站预算，持久固定结果后才首次 `admit_connector(Final)`；不把部分 Delta 与快照拼接，也不依赖后续 Final 覆盖首次内容。分别测试不断流的 Delta → 无产物 Completed、半段 Delta 后杀 Connector、完整结果固定后报告回包丢失；真实 Inbox 中只出现同身份同摘要的完整 Final，Runner 收到全部 Part。仅支持 Delta 或只回状态的目标不发布；已发布目标违约时不接受空/残缺产物，恢复期限耗尽报告有界结果不完整失败，并保留已证实的停止事实按统一屏障收尾。
- [ ] T6 对按请求查询能力分 profile 验收：当前真实 A2A adapter 的 `ByRequest` 返回 `Unsupported`，不得把 GetTask 或假 port 的能力当作生产支持。需要该恢复能力的目标须交付经协商的 request-id 查询扩展及 adapter（实现在 `bcs-a2a-http`，不在 Worker 拼 URL），绑定目标域、原操作/请求身份和当前 query 授权，并记录协议/adapter 版本。用真实 HTTP adapter 加可控扩展服务验证索引迟到后找到原 Task，再读取并校验停止证据；NotFound 仍保持未知。标准 A2A profile 无该扩展且启动回包丢失时，应保留核实义务和告警，始终不重发、不构造 Task ID、不伪造停止；T9 分别记录两种 profile，缺扩展交付就标对应恢复验收 blocked。
- [ ] 回归已有 Connector：`cargo test -p bcs-dist-connector --test rules --test recovery --test control`。T9 装配真实 resolver、membership 与持久 provider，不以 `PendingMembership` 或测试 resolver 验收发布。
- [ ] `revocation_between_enqueue_and_dispatch`：发出/重发/continue 前重新核实当前成员资格、委托、目标凭据权限；未获授权不发新输入/历史。区分普通换绑和撤权，查询/取消已派发任务仅在有单独控制权限时可用。`remote_global_memory_ignores_conversation_id`：让测试 Bot 在两个不同 conversation 下共享全局记忆，能力检查必须拒绝；仅经目标部署验证的隔离记忆域或禁用共享记忆的模式可发布。T5/T8 使用同一能力证据，按 adapter/配置版本失效；T9 在实际下游执行标记负测。
- [ ] GREEN：重跑该目标。分别记录逻辑轮次、发送尝试和下游实际执行次数，不用其中一项冒充另外两项。
- [ ] 提交 `feat(bcs): correlate FSM node delivery and stop evidence`。

### T7：Worker 提交权威终态并立即允许下一轮

**文件：** 接入 [#4245] 的 `crates/services/bcs-dist-worker/src/processor/decision.rs`、同目录的 `transitions.rs` 和 `effects.rs`；新增 `crates/services/bcs-dist-worker/tests/group_round_recovery.rs`；扩展 `crates/services/bcs-dist-gateway-store/src/task_reader.rs`。**接口：** `commit_terminal(round: &RoundKey, proof: TerminalProof) -> Result<CommitSeq, StoreError>` 接入现有受 owner/session fence 保护的提交与 outbox；公开 get/list/subscribe 必须按相同权威 cut 决定能否展示归档，不能仅凭归档行存在或旧 snapshot 仍为 Working 裁决。

- [ ] 写 `archive_visible_only_at_commit_and_next_round_not_waiting_for_sse`：先暂停在归档后、提交前，A 不可见为成功，B 忙；放行提交但暂停 SSE，`assert_eq!(get(a).artifacts, expected)` 且 B 立即获准；恢复旧 SSE 不改 B 的占用。两个窗口各杀一次 Worker，A 的 Run 不重跑。
- [ ] RED：`cargo test -p bcs-dist-worker --test group_round_recovery`，确认旧的异步释放或 archive fallback 不满足可见性。
- [ ] 从 T3 的 `PinnedHistory` 核验并装入本节点已冻结的输入，执行 T5 的决定、T6 的派发，用 T4 固定归档；在同一权威版本下提交 FSM 终态和可接下一轮的事实，迟到旧写须带精确代次、被栅栏拒绝。读路径能从提交证明找到原归档，即使热快照滞后。SSE 是跟随者，不是放行条件。失败/取消只接受所有未决 attempt 已停止的证明；丢失通知、租约到期不算证明。
- [ ] 验证联合版本中的 Worker、Connector 和 Store 使用同一 `did`/dispatch 文档、命令分区及 `ConnectorAdmission` / `admit_connector` 合同，停止证明取自该 attempt 的持久事实；不另建 Worker 侧“已停止”账本。同步检查 `bcs-dist-connector/src/report.rs`、Service API Inbox port 与 `bcs-dist-store/src/inbox/{producer,validation}.rs`，不能只替换 Worker。`get_task_final_reaches_real_runner`：真实 Connector 通过 GetTask 获取无 `remote_event_id` 的完整终态，经真实 Inbox 交给 Runner；再注入报告丢响应和重复查询，结果只应用一次，异内容冲突不被吞掉，不伪造远端事件 ID。重复取消、换 holder 和迟到回调不能提前释放 context。
- [ ] 把 Runner Finish 和 [prepare.rs](https://github.com/inclusionAI/Avernet/blob/c7187edcc99354f017f2e1a9bfcb3d6e88c15b96/apps/bcs/crates/services/bcs-dist-worker/src/processor/prepare.rs) 的所有独立入口接到同一停止屏障。包括 `TaskDeadlineExceeded/TaskDeadlineOutOfRange`、`TaskStepLimitExceeded`、`CanceledByUser`、`OutputQuotaExceeded`、容量拒绝和风险/隔离；同时修改 `processor/lifecycle.rs` 的 `finalize_independently` 及恢复回放。未受理容量拒绝不可变成公开 Task；已受理而没有 dispatch 的分支也须从持久记录证明为空。其余先冻结意图、停止新派发、登记取消，再由统一提交检查全部 attempt 的停止证明；可疑 checkpoint 不得凭猜测生成空集合。`deadline_or_step_limit_with_live_sibling` 对 deadline/步数上限分别断言：未停无终态、无释放，停止确认后仅一个终态。取消与正常完成竞态同理。
- [ ] 修改 [drive.rs](https://github.com/inclusionAI/Avernet/blob/c7187edcc99354f017f2e1a9bfcb3d6e88c15b96/apps/bcs/crates/services/bcs-dist-worker/src/processor/drive.rs) 和 `lifecycle.rs::retire_published` 对 FSM 来源的调度条件：从阻塞 session 的 outbox 中成对拆出 `AppendEvents` 与 `PublishSnapshot` 的持久发布义务，与权威提交同切面登记；两者均不得留作轮次退役或下一轮派发的同步前置条件。独立有界 publisher 按稳定 ID 顺序重试，已有事项不能因 A 退役或 checkpoint 压缩被清掉。`feed_append_failure_does_not_starve_next_round`：共享 feed 连续拒写（非客户端断开），A 可查、B **实际派发首节点**；接管后恢复 feed，检查 A 产物先于终态，重放无重复公开事件。业务派发/停止证明/归档等必要效果不得一并跳过。
- [ ] 改 [#4245] `bcs-dist-store/src/evidence.rs`、`task_feed/{append,publish,terminal}.rs`：仅对严格群来源的 `AppendEvents` / `PublishSnapshot` 新增跨 checkpoint 可验证的成对首次写许可，分别绑定提交身份、Sid/generation/Tid、effect ID、事件 digest/range/前驱，以及对应快照 digest/source_commit/snapshot_event_id。publisher 先按 `previous == completed_head` append，再 publish 该 Task 的快照；两步完成分别持久读回，不以 append 成功代替快照完成。A/B 业务调度不等 publisher，其他 effect 仍用原 outbox 授权。Task 级权威终态证明依 T4 转存，不依赖旧 `terminal.rs` 等待 feed 才可查；发布义务的完成/裁剪则须证明 append 和 publish 均已完成或快照已被同 Task 的合法更新覆盖，不能因 B 换 checkpoint 视为过时。所有引用 pin 保留到对应义务完成，之后才能清许可。测试 A 首次 append 从未成功且 B 已派发，再在 A 首次 append 成功、尚未 publish 处宕机；接管后凭原许可完成快照和后续事件，无重复、无错 Tid、无无授权发布，A 始终可查且 B 不受阻。
- [ ] 改 [#4245] `bcs-dist-store/src/dispatch/{journal,mod,scan}.rs` 及对应合同：按 shard 分批回收已终结且证明已转存的 did 目录项；回收前确认没有 pending claim、命令、未决监督或可重放 effect，保留拒绝迟到写与同 did 重建的 fence/水位。同步将基于可收缩数组的 `cut/next` 游标改为稳定单调目录键及每轮上界，删除不改变其他项的位置，新增项在后续轮次可发现；当前扫描 work 引用项须保持 pin 或可验证墓碑，接管不依赖失效下标。旧游标格式显式版本化，升级须在受保护切面重新建立扫描边界且允许幂等重扫，不猜测下标等价。`dispatch_directory_reuses_capacity` 固定同 shard、每轮两个新 did，累计超过默认 1,024 项仍能派发；`reclaim_before_scan_cursor_survives_takeover` 在扫描暂停于旧 cut=100/next=80 时回收前 30 项并重启，断言无 rollback、未终结项不漏扫并可接回。记录最大批量/存储调用数，不全量重扫历史；未知项未删、旧通知不复活调用，不能靠提高上限或 TTL 通过。
- [ ] T1/T6/T7 在权威 Dispatch 与 repo 合同新增核实交接状态和独立 claim epoch，修改 Connector `src/{engine,call,send}.rs`、Connector store `src/queue.rs` 及 Worker Supervisor。短期查询耗尽时，以受当前 claim fence 保护的 CAS 同时登记核实模式、attempt/原引用或稳定查询键、核实方式、下次时间/退避和待调度义务，并使普通 claim 失效；确认或读回交接成功后才退出 actor。核实队列只是可修复索引，不成为第二个所有权源；CAS 后索引尚未写的宕机由 Supervisor 扫权威状态补齐。普通 claim、重复 hint 及本实例无 actor 的重建路径均拒绝核实模式；Supervisor 将其路由到核实队列，不再发普通执行 hint。核实者按独立 epoch 有界领取，即使原 Connector 仍活着也可完成交接；仅允许获准的 query/cancel 与持久报告，不允许 start/resend/continue，旧 epoch 的写入被拒。用独立并发/容量低频复查，重启恢复、迟到通知提前唤醒；无能力或权限则保留并告警。
- [ ] `verification_handoff_fences_live_connector`：原 Connector 不死，未知 actor 已退出后重复投递旧 hint，由另一核实者认领；普通槽不被重新占用，旧 holder 写被拒。交接 CAS 前、后未入索引、领取后分别宕机，义务不丢且无双写。`late_evidence_after_short_retries` 使用 T6 的真实查询扩展 profile：多个未知调用释放普通槽、另一 context 首派发后重启，再让索引可查，系统主动找到原 Task 并核实停止，不直接注入证明、无重复 start。另跑标准 A2A `ByRequest=Unsupported` 对照：保持隔离且其他 context 可执行，不把安全未知算作已恢复。
- [ ] 异常公开状态与 T8 联测：超过停止核实期限时保存独立 `Quarantined`，不越过终态门槛；A 的 Get/同请求重试为原 Task `WORKING/STOP_UNCONFIRMED`，新的或重连订阅发送此快照后关闭（非终态），活动订阅同样有界退出，不能无期限依赖失败的 feed；新请求 `400 CONTEXT_UNAVAILABLE`。重启与持续未知重复这些断言，再注入真实停止证明完成收尾。
- [ ] GREEN：重跑该目标，覆盖同 Sid 多轮、失败历史、Unknown/崩溃、旧 owner 迟到和归档冲突。关闭轮次后有界压缩 checkpoint，保留拒绝旧事件的 watermark、已验证的按 Tid 冷证明和所有未完成义务；连续多轮的 checkpoint 不随已归档历史线性增长。
- [ ] 提交 `feat(bcs): commit FSM terminal state independently of SSE delivery`。

### T8：新增群 HTTP+JSON、Card、查询和订阅，不改旧绑定

**文件：** `crates/application/bcs-app-dist-gateway/src/{lib.rs,submit.rs,wait.rs}`、`crates/adapters/http/bcs-dist-gateway-http/src/{lib.rs,card.rs}`；新增同一 adapter 的 `src/group_rest.rs`、`tests/group_rest.rs`。在 `crates/services/bcs-dist-gateway-store/src/{page.rs,task_reader.rs}` 实现按群查询的索引读，不把当前页长度当作总数。**接口：** 扩展 `DistGatewayService` 的群调用入口，显式传可信 caller、GroupId 和规范化请求；读取 Card 调独立只读 application 边界。协议和 SSE 帧不进入 Runner/store。

- [ ] 写 `rest_contract_preserves_rpc`：`POST /api/v1/collaboration/a2a/groups/{group_id}/message:send` 并发败者 HTTP 409；阻塞 send 等待超限 504 而 Task 继续；断开 stream 不取消。终态 get 含最终节点 Artifact。用假认证链证明跨群/主体和恶意自报 metadata 不能读取任务；已有 JSON-RPC 套件保持通过。
- [ ] RED：`cargo test -p bcs-dist-gateway-http --test group_rest`，预期缺路由或返回码不符。
- [ ] 实现 `agent-card.json`、`message:send`、`message:stream`、`GET tasks/{id}`、`GET tasks` 和 `/tasks/{id}:subscribe` / `:cancel`。按 #3979 在同一动态 POST 路径分派动作；先查群/主体归属再返回资源。Get/List/SSE 使用相同公开投影；保留有序 Part 属性。List 返回过滤后实际总数/有效页大小，批量加载可选产物；订阅恢复到已授权快照，不承诺逐事件无损。校验显式发布、权限撤销行为、输入/输出模式、扩展和等待预算，未获批能力不在 Card 宣告。
- [ ] `rpc_cannot_write_rest_context_and_vice_versa`：在 `submit.rs`、cancel 和共享读入口校验持久的创建来源，不以 caller/group 相同代替来源校验；隔离新来源不改变旧资源行为。测试无 context 同 messageId 从两个入口建会话、跨入口追加/取消/查询/订阅、REST 准备后宕机的记录不进公开列表或 total，验证 Inbox 无新增写。Task 终态、隔离状态和 Accepted 可见性均从权威记录恢复而非裸快照。
- [ ] `list_pages_share_cut_across_hot_to_archive`：T1/T4 共建仅含已受理资源的版本化公共索引，首次查询取得有界的一致读版本，不能为异步索引滞后伪造当前 cut；按 Tid 唯一。total、排序键 `(status_updated_at DESC, tid ASC)`、筛选与行投影都读同版本。新游标签名并绑定 scope/group、筛选、cut、末键、期限；后页重验权限，过期/版本不可读则明确拒绝，不回退到实时查询。页间热转冷、并发新增和状态更新后，跨页无重复/漏项、total 不变；游标篡改、跨主体、撤权、过期均不可继续读。保留投影版本/引用覆盖有效游标期或显式使游标失效；记录有界扫描、索引计数和页内批量 IO 成本。
- [ ] GREEN：重跑新增目标及 `cargo test -p bcs-dist-gateway-http --test a2a`；检查终态与订阅竞态、削权后的旧游标、过期读取、超大列表/单帧、取消响应丢失后的幂等恢复。未决定的权限不能以匿名回退通过测试。
- [ ] 提交 `feat(bcs): expose published FSM groups over A2A HTTP and SSE`。

### T9：装配、注入故障、记录可上线范围

**文件：** `crates/bootstrap/bcs/src/server/server_run.rs`、新增 `crates/bootstrap/bcs/src/server/group_a2a_wiring.rs`、`crates/bootstrap/bcs/tests/group_a2a_companion.rs`。在 `specs/2026-11-20-fsm-group-a2a-discussion/validation.md` 记录实际角色、provider、命令、失败/跳过项。**接口：** composition root 注入真实群目录、认证、Worker、Runner、Connector、结果与节点历史 provider；不向 handler 暴露这些 repo。

- [ ] 写 `published_two_node_group_survives_takeover`：研究和评审各跑两轮，重复请求、忙时重发、失败/取消仍占用、跨轮历史隔离、Artifact 按旧 Tid 查询；关闭入口时确认既有路由不受影响。测试下游可控，核心角色不允许 Script 替身。
- [ ] RED：`cargo test -p bcs --test group_a2a_companion`；未挂载/无真实角色时功能场景不得通过。
- [ ] 先做显式 opt-in 的同进程装配，再用 Singlebox companion 的独立进程及共享持久 provider 执行 T2/T6/T7 的崩溃矩阵，覆盖停 Gateway、Worker、Connector、provider 重启；不得将多个进程各自的内存模型当作共享存储。
- [ ] 强制装配 [#4245] `bcs-dist-worker/src/supervisor/visit.rs`：由持有 shard lease 的 Worker 运行 Supervisor，按 T7 的稳定游标扫描权威 Dispatch、检查真实 membership；普通执行模式向目标 pool 重发 hint，核实模式仅修复/唤醒独立核实队列，不能重发普通执行 hint；owner 交接后接续扫描。`acked_connector_dies_without_hint`：远端 Task ID 已持久且队列已 ACK 后杀 Connector，不人工补 hint；接任者在原 deadline 前接回同一远端 Task，远端只执行一次。禁用 Supervisor 的对照组不能通过。单独记录隔离核实队列、TaskFeed 首次补发及 Dispatch 目录跨容量的跨进程结果；D8 不是本验收前提。
- [ ] GREEN：重跑 companion、受影响 crate、双方言升级、`cargo check --workspace --all-targets` 和两项 API/R25 门禁。记录受理/结果未知路径最大存储调用数、分页与恢复扫描有界性、故障退避和 retention/GC 负担；生产 provider 缺失、停止证明未签字、权限矩阵不全时保留关闭状态，不以本地 GREEN 替代上线验收。
- [ ] 提交 `test(bcs): verify FSM group invocation across process recovery`。

## 执行边界

本文不批准 Runtime PR、协议默认值上线、数据迁移执行或部署。任务中的未勾选命令和断言是**待完成验收**；已执行的阶段性证据以 `validation.md` 为准。本次重组只修改计划，不新增运行时验证结果。先核对所消费合同和功能前置条件，再按 Stage 2～5 推进；D0 门禁复核与独立 P 认证并行，未通过不宣称冻结或部署就绪。允许的并行仅限阶段表所列范围。T2/T4 须先与在审分支协调，T7 和真实 E2E 不绕过 P/W 或联合版本依赖。
