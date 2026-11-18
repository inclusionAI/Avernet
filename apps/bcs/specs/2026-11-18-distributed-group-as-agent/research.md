# 设计依据与现状核对

- 日期：2026-11-18。
- 目标基线：`816e1f849e`，本次开始时工作区无修改。
- 用途：支持 [spec.md](spec.md) 的范围、并行边界和合同增量；不作为运行时完成或部署证据。

## 1. 来源与版本

| 来源 | 本次读取范围与用途 |
| --- | --- |
| 《BCN 分布式 Worker 高可用方案 v2.9（TBase 存运行态，OB 存结果）》 | 重新通过语雀 CLI 读取；正文 2026-11-13 07:32:57 UTC，页面元数据更新于 2026-11-17；重点采用 §15.0、§15.3、§15.3.1、§15.6 |
| 《BCN v2.9 Worker 设计：执行流程、阶段与 TBase 操作》 | 重新通过语雀 CLI 读取；正文 2026-11-13 08:59:12 UTC，页面元数据更新于 2026-11-17；采用纯 reduce、先提交后动作、会话封印、OB 先保存结果等原则 |
| [Group A2A 规范](../2026-11-05-group-as-agent/spec.md)及同目录 overview、讨论、schema | 采用对外路由、Task/context、busy、输入输出、Artifact、归属、取消和清理要求；首期缩小为 StateMachine |
| [分布式合同说明](../2026-11-15-distributed-contracts/spec.md)及 [任务依赖](../2026-11-15-distributed-contracts/tasks.md) | 识别已有声明和待实施角色，不把计划当实际实现 |
| [Repository 合同](../../crates/service-api/bcs-service-api/src/port/repo/distributed/README.md) | 采用提交证明、opaque cursor、完整快照恢复、历史动作身份、调度 token 和 unknown 语义 |

语雀源地址和原始导出保留在仓库外。公共 spec 自包含所需设计决策，不要求开发者登录私有服务，不引入私有 endpoints、SDK、密钥或运行态。

语雀总方案 §1～§14 保留旧 Function/POC 内容；17 项 POC 用例不是当前 Rust、新快照协议或生产 HA 的验证。Worker 仍有 Reset、旧字段与“事务即可去掉补偿”的历史片段。新规范不复制这些假设；当前 repo 合同明确没有持久 Reset，并要求内容证明、裁剪覆盖和部分结果恢复。

## 2. 当前代码证据

| 能力 | 证据 | 边界 |
| --- | --- | --- |
| 领域模型 | [bcs-dist-model CONTEXT](../../crates/contracts/bcs-dist-model/CONTEXT.md)、[SessionSpec](../../crates/contracts/bcs-dist-model/src/session.rs) | 仅 FsmGroup；workflow、participants、limits 可冻结；没有完整 Group A2A 配置 |
| Gateway application | [DistGatewayService](../../crates/service-api/bcs-service-api/src/application/dist_gateway.rs) | 有 submit/get/list/subscribe/cancel 声明；缺少部分群范围和完整语义请求表达 |
| Gateway Core | [DistGatewayCoreService](../../crates/service-api/bcs-service-api/src/core/dist_gateway.rs) | 有 admit/restore/follow 等声明；不是已装配服务 |
| FSM Runner | [DistRunnerCoreService](../../crates/service-api/bcs-service-api/src/core/dist_runner.rs) | init/reduce 纯函数接口；实现搜索只找到 Script 测试替身 |
| 群定义目录 | [DistGroupDirectoryPort](../../crates/service-api/bcs-service-api/src/port/dist_agent.rs) | 首轮 resolve、按精确 revision/digest load；不能直接充当有副作用的 Card 读取 |
| Inbox 受理 | [StoreInboxProducer](../../crates/services/bcs-dist-store/src/inbox/producer.rs) | 实际实现固定写序、创建一次、激活与去重；不提供 Group busy 占用协议 |
| Listener | [read_listener](../../crates/services/bcs-dist-store/src/inbox/listener.rs) | 占位错误 need_contract_not_implemented，受理 fallback 为无人监听；此处未完成 quarantine 判断 |
| 存储接口 | [bcs-store-api](../../crates/plugin-api/bcs-store-api/README.md) | 普通/版本 KV、JSON、ZSet、Stream；公共实现搜索仅找到测试 Model 与 Recorder |
| Key/version | [KeyLayout](../../crates/services/bcs-dist-store/src/layout.rs)、[version](../../crates/services/bcs-dist-store/src/version.rs) | 无 hash tag；Sid 前缀带 shard；不得把语雀示例当部署物理合同 |
| Task 与结果 | [task.rs](../../crates/contracts/bcs-dist-model/src/task.rs)、[ResultStorePort](../../crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs) | 通用 Payload/FinalTaskRecord；尚无本期 typed 公共产物和独立 Artifact 持久化实现 |
| 旧会话 | [Session](../../crates/contracts/bcs-domain/src/session.rs) | 旧 ID 为 group_id:hex，与分布式 KeyLayout 不兼容；需要明确新路径 canonical Session 责任 |
| 旧执行 | [Runtime](../../crates/services/bcs-collaboration-runtime/src/runtime.rs)、[Judge](../../crates/services/bcs-collaboration-runtime/src/runtime_judge.rs)、[completion](../../crates/services/bcs-collaboration-runtime/src/runtime/completion.rs) | 直接使用 repo、delivery、Judge；不能直接作为纯 Runner；旧 final output 有 fallback，不能据此猜测本期最终节点 |
| 测试框架 | [distributed_harness](../../crates/test-support/bcs-test-support/tests/distributed_harness.rs)、[StoreInboxProducer tests](../../crates/services/bcs-dist-store/tests/admission.rs) | 验证接口断言/局部受理，不是三角色可运行集成 |

本次检索生产实现与 bootstrap/adapter 装配，未找到完整 DistGateway、Worker、Connector、TaskFeed、SessionStore、ResultStore 链路。不能由接口存在推断分布式服务已运行。

## 3. 保留与新增决定

保留旧 Group 规范的 HTTP+JSON 路由、文本/对象输入、同 context busy、完整请求去重、独立 Artifact、公开内容约束和停止未知隔离。

本期新增提议：

1. A2A adapter 由 BCS 分布式 Gateway 装配，与 apps/gateway 无关。
2. 复用现有 Gateway/Runner 边界，在 D0 扩展合同；不再建一套并行任务执行系统。
3. 新入口采用分布式 canonical Sid；不隐式兼容旧 SQL Session ID。
4. Group 请求预留独立于 Inbox admit；增加独占轮次、持久执行许可与恢复协议。
5. 最终 Task/Artifact 同事务归档，TaskFeed 与占用释放按持久化阶段补做；不假设跨存储事务。
6. TaskFeed 为新路径公共视图来源；不同时消费旧 bcs_events 生成第二份独立公开状态。

这些是草案提议，未改变现有 Rust/public contract。D0 通过前，只能按现状实现可独立的研究/夹具；不能声称增量合同已冻结。

## 4. 验证记录

本次为文档变更，只执行静态核对和文档检查：

- 重新读取两篇语雀正文，区分正文日期与页面元数据日期。
- 对照当前 Service API、领域模型、Inbox 实现、旧 Runtime、Group 草案和分布式开发计划。
- 新增文档及索引的相对文件链接、代码围栏、表格列数、导航和文件规模检查通过；本次新文档没有跨文件标题锚点。新增文件的尾随空白检查通过，`git diff --check` 通过。
- 自审请求身份、跨记录受理许可、终态裁决、跨存储发布、慢消费者和恢复边界。

同一对话的先前代码核对曾执行：`bcs-dist-store`、`bcs-dist-model`、`bcs-store-api` 共 38 项测试及 distributed_harness 34 项测试通过，属于该次核对的证据。本次文档产出未重新运行 Cargo 测试，不将旧结果充当新增合同或实现验证。

尚未验证：新 GroupInvocationRepo、typed 请求/产物、真实 FSM Runner、角色装配、完整 A2A 客户端联调、持久 provider、跨进程故障、容量、RPO/RTO 和部署。后续结果应分开追加，不覆盖此记录。

## 5. 2026-11-18 评审修订

本轮仅修订本目录的设计文档，保留上面的历史核对记录；未修改 Rust 声明、实现、迁移或装配。

| 问题与证据 | 规范修订 | 待实现验证 |
| --- | --- | --- |
| [InboxSchedulingPort](../../crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs) 仅明确 admission 使调度 token 失效；独立许可写入不能自动继承这个保证 | spec §6、§7.3 补充许可与待调度义务同记录原子保存、未许可输入消费屏障、有界重查、重新调度使 token 失效及 Start 消费证明确认；§11 补充扫描与访问成本 | §13 覆盖旧 settle 两种交错、通知丢失、许可保存后崩溃、等待中接管及确认响应丢失 |
| 原 §7.2 在查询请求身份前按群能力校验，无法保证响应丢失后按冻结配置恢复原请求 | spec §7.1、§7.2 先做当前权限与基础校验，再按保存的规范化版本/默认值查重；仅新请求协商能力，并处理并发预留获胜记录 | §13 覆盖文本改为 JSON 后原样重试、默认值/扩展变化、预留竞争、内容冲突、结果过期及权限撤销 |

这些仍是 D0 的待落地合同，不表示现有 provider 已具备可靠重新调度或请求版本恢复能力。本轮未运行 Cargo、provider 故障注入或跨进程测试；新增验收场景尚待实现后执行。

本轮实际验证：两份文档共 36 个相对文件链接、代码围栏闭合、表格列数、尾随空白和文件末尾换行检查通过；`git diff --check` 通过。新目录仍未跟踪，已直接检查文件内容，未仅依赖 Git diff。手工复核了 §6/§7 的协议、§11 成本约束和 §13 验收场景的一致性。


## 6. 2026-11-18 阻塞审阅修订

本轮审阅指出 D0 不能冻结，原因与修订如下：

| 阻塞点 | 本轮文档处理 |
| --- | --- |
| Pre-Accepted Quarantined/ContentConflict 或 activate 永久失败可能永久占用 | 增加 `abandon_round`、`confirm_start_discarded`、Abandoned/DropStartPending、expected generation 互斥、Worker progress-only 丢弃证明及恢复错误映射 |
| Runner Start/Finish 仍是 Payload | D01/D04 冻结 `GroupRoundInput`、`PublicTaskResult`、绝对 deadline、固定 Artifact ID/顺序/媒体模式/digest |
| Gateway/Runner 双份 workflow 白名单 | 增加 C01 shared pure validator；G01/R01 消费同一 digest/compiler version |
| cancel 仍返回 AdmissionReceipt | D02/G03 迁移 `CancelTaskResult { task, cancel_request_id }` |
| Blob 只有 put/get | D06/S01/S04 增加 manifest、pin/reference、精确 generation delete、file-backed provider、GC conformance 和 wiring |
| R25/API gate 与编译范围不足 | D05 采用 trait_snake harness + `conformance_*.rs`；D02 升 `bcs-service-api` 7.0.0 并传播；D0/I04 跑 workspace/all-targets、R25 和 public-api gate |
| SQL sid 唯一约束误用于最终归档 | S04 分开 request/current-round/archive/artifact/history 约束，并测试同 Sid 多轮归档 |
| 长会话 checkpoint 线性增长 | spec/plan 增加 S07 逐轮压缩、closed generation/迟到事件 fencing、500 轮成本测试 |
| 受管删除无合同 | 本期明确禁止 distributed Session 删除；旧入口副作用前返回 `DISTRIBUTED_CONTEXT_DELETE_UNSUPPORTED` |
| claim Lost、S02 Worker 依赖、文档状态不一致 | G02 增加有限 generation 重试；Worker recovery 移到 S03；spec/plan 状态统一为审阅修订、D0 未冻结 |

本轮没有运行 Rust、provider、R25 或 API gate；这些是后续 D0 GREEN 和集成门槛，文档静态检查不能替代它们。

## 7. 本次阻塞审阅后的静态验证

当轮同步修订 `spec.md`、`plan.md`、`plan-0-contracts.md`、`plan-1-gateway.md`、`plan-2-runner.md`、`plan-3-storage-runtime.md`、`plan-4-integration.md` 和本研究记录；未修改 Rust、迁移、角色装配或实现。当时 spec SHA-256 为 `ed4f338174b0b7a1b6b89f559c2e26778b8c407e74b8c653ff3d2356f9f54d97`；后续完整复审修订见 §8，当前执行 hash 以 plan.md §1 为准。

静态检查通过：9 个文档文件、93 个相对链接、30 个表格、代码围栏、任务 ID 与依赖引用、尾随空白、文件末尾换行及 `git diff --check`。任务集合包含 D01～D06、C01、G01～G06、R01～R04、S00～S07、I01～I04；C01/R25 是合同和 gate 标识，不是遗漏的任务编号。

尚未执行 Cargo、R25、public API、provider conformance、真实 Blob provider、跨进程故障或容量测试。审阅意见要求的 `bcs-service-api` 7.0.0、Cargo.lock propagation 和 PR propagation analysis 只有在 D02 实际修改 public API 时执行，不能在文档阶段宣称通过。

## 8. 2026-11-18 完整复审六项修订

用户授权修复完整复审发现的六项文档问题。本轮修改范围仍为本目录 spec/research/6 份 plan；不修改 Rust、迁移、锁文件、角色装配或旧计划，不执行实施任务。上面的验证记录保留为历史证据。

| 问题与核对依据 | 本轮修订 | 待实施验证 |
| --- | --- | --- |
| G02 的 Lost 分支直接 Busy，但 Preparing 可被恢复器继续 claim | spec §6/§7、D02/G02/S01 统一 reject_unclaimed，拒绝 revision 永不复用 | Busy 后释放并扫描仍无 Task/Input；拒绝写响应丢失、同 messageId 新准备及旧扫描交错 |
| 准备时背景与后续成功 claim 的 generation 未绑定 | D01/D02 保存 PreparationContextBasis，read_context_basis/refresh_preparation 条件读写；release 原子推进成功背景 | A 准备 T0、B 完成 T1、A Lost 后输入必须使用 T1；同 generation 背景更新也校验；deadline/指纹不变 |
| Drop 只接受消费证明，实际没有 Start 时永远无法确认 | D03/S02 增加 seal_abandoned_start 和 producer append 栅栏；ClosedAbsent 独立确认，ClosedPresent 按封口边界消费 | 无 SessionSpec/Start 也能退出主动扫描；append/seal 两种顺序、多副本、确认丢响应与旧写者 |
| TaskPage 无法生成锁定 SDK 的完整 ListTasksResponse | D02/G03/G04/S04 补 page_size/total_size，聚合过滤后的热/归档去重集合 | 空页/末页/跨 scope/组合过滤的页元数据正确，计数不加载正文，记录实际扫描成本 |
| PublicPart 没有 filename/metadata，与沿用的 Part 保存合同不符 | D01 用 PublicPart + PublicPartContent 保留逐 Part 属性，输入指纹/公开策略/归档/编码同步 | 多 Part 和顶层 metadata 互不混淆；允许属性往返，私有属性不泄漏 |
| I02 在终态可见后立即 unwrap 下一轮，而 spec 允许 release 延迟 | S05/I02/I03 加 before_release_round barrier，wait_terminal 与 wait_round_released 分开 | release 前 Busy 且无新输入，确认持久 release 后同 messageId 重试成功 |

协议字段依据是 [Cargo.lock](../../Cargo.lock) 锁定的 a2a-lf 0.4.2 / a2a-pb 0.3.2；本地包源码 `types.rs::ListTasksResponse`、`proto/a2a.proto::ListTasksResponse` 包含 page_size/total_size，proto 将总量定义为分页前数量。Part 属性沿用 [原 Group 草案 §8.3](../2026-11-05-group-as-agent/spec.md#83-parts-与非文本内容)。当前 [InboxProducerPort](../../crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs) 尚无封口方法；新增合同与 provider 能力都待 D0/实现评审，不能从本次文字修改推断已具备跨记录原子性。

方案边界：保留 Lost 的有界重试而不是每次换 generation 都抛弃请求，但必须条件刷新服务器派生输入；保留消费证明而不是用超时清空 Drop，以写入封口提供真实无输入结束路径。新增 producer 能力由 S02 落地，无法实现实际 append 栅栏的 provider 不得开启该路径。未运行 Cargo、R25/public API、provider 故障注入、跨进程或容量测试；文档检查不替代这些门槛。

本轮实际静态验证：直接检查本目录 8 份文档（含未跟踪文件），56 个相对链接（含 4 个标题锚点）、31 个表格、代码围栏、尾随空白、末尾换行、29 个唯一任务 ID，以及六项修订的跨文档覆盖和两轮测试步骤顺序均通过；`git diff --check` 通过。这些覆盖检查针对文档内容，不是运行时行为测试。spec SHA-256 为 `c1229e5fe1cdf11d8b672fa20854ba5cac7c23915755dc26a3d8fe1edcef7020`，已同步 plan.md 的执行基线。未改动本目录之外的既有工作区修改，未提交。
