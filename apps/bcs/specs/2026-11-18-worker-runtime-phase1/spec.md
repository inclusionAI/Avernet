---
status: proposed
date: 2026-11-18
updated: 2026-11-19
owner: BCS distributed Worker
---

# BCN Worker 第一批通用运行时 SPEC v1

2026-11-19 integration amendment: user-authorized changes in sections 3.3, 4.1
and the acceptance matrix remove output-body byte quotas and integrate Gateway
admission and independent output formatting. See the [current integration evidence](../2026-11-19-gateway-worker-admission/verification.md). Original proposal status
and pinned history below are not current implementation acceptance claims.

> Membership amendment (2026-11-20):
> [RetainedLease](../2026-11-20-retained-membership-lease/spec.md) replaces the
> earlier StrictLease/time/permanent-tombstone requirements. Earlier verification
> below records the implementation tested then; it is not evidence for the new provider.

## 1. 状态、目标与基线

本稿定义 Worker 的通用执行与恢复闭环，供实现前评审。本次仅提交 SPEC 和导航，不修改 Rust 契约、运行时、插件、配置或数据库迁移；文档合入不代表功能实现、测试通过或部署完成。

目标是：输入可靠受理后，能够被发现、去重、计算、提交、执行副作用、发布输出并归档结果；Worker 重启、通知丢失或所有权交接后，仍能从持久状态继续推进。实际群协作规则由后续 Runner 实现，本批不能用生产占位 Runner 冒充真实群功能。

原始代码对照基线为 Avernet `7358ad11c314c7c221f1f66630f0aaec89e92997`。协调依赖 [PR #4126](https://github.com/inclusionAI/Avernet/pull/4126)，设计对照版本为 [`220c99949bfa56eb76221ffe7078f352d84d1e8b`](https://github.com/inclusionAI/Avernet/blob/220c99949bfa56eb76221ffe7078f352d84d1e8b/apps/bcs/specs/2026-11-17-worker-coordination/spec.md)。#4126 的文档已合入，固定版本的状态仍为 proposed；文档合入本身不证明前置能力已实现或通过验收。2026-11-19 修订分支 rebase 到 `dev` 的 `3a925fd474`，保留上述历史对照与设计 pin；后续实现须对实际集成版本重新核对，不能把历史缺口当成当前代码结论。

相关契约和导航：

- [实现计划与进度](implementation-plan.md)：开发验证单独记录，不将 SPEC 送审等同于功能交付。
- [分布式契约 SPEC](../2026-11-15-distributed-contracts/spec.md)及[任务划分](../2026-11-15-distributed-contracts/tasks.md)。
- [业务存储端口](../../crates/service-api/bcs-service-api/src/port/repo/distributed/README.md)、[SessionStore](../../crates/service-api/bcs-service-api/src/port/repo/distributed/session.rs)、[Inbox 与调度](../../crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs)。
- [Runner](../../crates/service-api/bcs-service-api/src/core/dist_runner.rs)、[输出与任务模型](../../crates/contracts/bcs-dist-model/src/task.rs)、[TaskFeed](../../crates/service-api/bcs-service-api/src/port/repo/distributed/task_feed.rs)。
- [DispatchBoard](../../crates/service-api/bcs-service-api/src/port/repo/distributed/dispatch.rs)、[Result 与 Blob](../../crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs)。
- [存储能力](../../crates/plugin-api/bcs-store-api/src/common.rs)、[JSON](../../crates/plugin-api/bcs-store-api/src/json.rs)、[Stream](../../crates/plugin-api/bcs-store-api/src/stream.rs)。
- [架构约束](../../../../docs/arch/arch.rules.md)、[协议测试规则](../../../../docs/arch/protocol-contract-tests.md)及 [BCS ADR 导航](../../docs/adr/README.md)。本稿不修改已有 ADR 或以提案覆盖当前 Rust 定义。

原始代码基线已有接口声明和部分 Inbox producer，但尚无本稿的完整 Worker。该基线 producer 的监听读取为占位逻辑；受理记录仅含摘要及 sid/tid，且在 Inbox append 后登记 active，不能直接作为本稿的规范输入选择和独立修复协议。这是静态对照，不是已复现的隔离运行故障。

## 2. 分批方案与边界

选择分三批交付：先通用 Worker，再真实群 Runner，最后长期运行治理。一次实现全部内容会混合执行协议、群业务和容量治理的评审；只做正常路径则无法兑现可靠受理与故障恢复。本批因此保留恢复和资源安全底线，但不展开长期优化。

| 第一批包含 | 不在第一批 |
| --- | --- |
| 持久受理、修复、输入消费、去重、调度和 checkpoint 提交 | 真正的群协作 FSM Runner、群节点执行策略和业务汇总 |
| outbox、DispatchBoard、输出事件、完整快照和结果归档 | Gateway／Connector 完整业务引擎及真实远端链路 |
| 人工回复、取消、超时、远端监督和隔离 | 保留策略、证据 GC、孤儿 Blob 清理和容量调优 |
| 协调集成、基础限流、背压、缓存和诊断 | 健康 owner 间自动再均衡及生产吞吐承诺 |
| 公共存储实现、确定性测试 Runner 和 provider conformance | 将测试 Runner 注册为生产默认实现 |

公共代码依赖业务端口；服务层不导入厂商 SDK、传输适配器或具体插件。provider 的命令映射、私有验证材料及企业组合由宿主仓库拥有，不复制到公共 SPEC。企业组合仍是整体第一批的验收组成部分，公共测试通过不等于所有组合已交付。

## 3. 拟议接口与模型变化

以下为后续实现需要的显式契约变化；本 PR 不改这些声明。

| 边界 | 变化 |
| --- | --- |
| 存储插件 | 扩展已有独立 `DocumentCas`／`DocumentStorePlugin` 子集，提供持久文档读取、同版本路径投影、原子创建、精确版本条件下的 Replace／Patch |
| Inbox provider | 补齐持久受理意图、规范内容与位置、受理代次及在途登记、持久受理结论、受理修复、监听状态和调度；明确 Quarantined／OutcomeUnknown 及重试语义，保持生产、消费、调度端口分离 |
| SessionStore | 增加精确 owner＋业务 head 保护的隔离状态、风险尝试结果及条件解除隔离；控制写不推进 `commit_seq` |
| Runner／输出 | `Show` 增加稳定 artifact 身份和明确更新操作；输出事件、展示草稿及恢复读取共享确定性投影；Worker 冻结内部物理输出配置、初始化用量和展示版本，维护快照进度及独立的任务终结状态转换；不设输出正文大小配额 |
| Worker 控制面 | 保留 `snapshot/drain(grace)`，通过 application facade 暴露受权隔离查询和条件恢复，不新增业务提交入口 |

### 3.1 持久 JSON CAS 子集

本节原提案名 `PersistentJsonCas` 在实际集成基线上对应已有的 [`DocumentCas`／`DocumentStorePlugin`](../../crates/plugin-api/bcs-store-api/src/document.rs)，不另建等价能力。支持不存在时原子创建，以及读取到的精确文档版本下的一次更新；首次创建版本为 1，每次成功更新推进版本，失败不修改内容或版本，耗尽失败而不回绕。存在性和版本条件必须与写入原子核对，不能以先 GET 再普通覆盖实现。

路径读取可从一次一致文档读取中投影；Patch 可先在内存中完成全部编辑，再以原 expected version 做一次原子 Replace，期间并发更新必须使该次写入拒绝。此实现最多一次读取和一次 EQ 写入，无隐藏重试，不降低失败原子性；读取和写入仍计完整文档字节，不能宣称为原生局部更新。配置 provider 可提供通过相同 conformance 的原生实现。

逻辑 Replace 必须删除旧值中不再存在的字段；Patch 沿用现有 JSON 指针和有序编辑语义，真实 null 与字段缺失可区分，多处编辑全部成功或全部失败。元数据与业务 JSON 隔离，业务 `rev`、`_store` 等名字不被禁用。适配器可以使用可逆私有编码，但不能要求所有后端都存字符串。

该子集不提供 TTL、删除、`Any`、`Newer` 或删除重建后的历史承诺。现有 `AtomicJson` 完整契约保持不变；现有消费者必须通过接口与测试变更显式改用子集，不得只放宽构造检查。原子创建与 EQ 更新分别验收，已有文档的 EQ 测试不能证明 create-if-absent 可用。

### 3.2 输出投影

展示单元以 `(node, artifact_id, class)` 为稳定身份；`artifact_id` 由纯计算确定，不依赖随机数或重试次数。公共操作为：

- `Upsert`：用完整 Payload 新建或替换该单元，保留已存在单元的排列位置。
- `AppendText`：向文本单元追加字符串；不存在时从空文本创建，已有非文本单元则拒绝该 Decision。任意 JSON 不做隐式 MERGE。

完整展示状态为按首次出现顺序排列的单元集合，使用版本化公共编码；`TaskContent.snapshot` 可以保存其 Inline 值或不可变 Ref。`OutputBody::Artifact` 携带同一操作。事件折叠、checkpoint 草稿和 restore 复用同一纯投影实现，不能在测试 Runner 内藏一套自定义快照语义。

Thinking 只产生临时事件，不进入完整展示快照和最终归档，也不能驱动策略。`SetStatus` 只接受非终态；Completed／Failed／Canceled／Rejected 必须通过 `FinishTask` 和第 6 节归档路径。先验证整个 Decision，再构建候选；不合法动作不能部分提交或执行。

### 3.3 Output persistence and phase-one policy (2026-11-19 integration amendment)

This phase has no byte-size quota for one output unit, cumulative task output,
or the complete display snapshot. The former 1 MiB/8 MiB quotas are removed.
Output-limit policy and OSS/object-store integration are deferred. Existing
Result/Blob storage is retained. OutputUsage is internal diagnostic accounting:
committed Upsert values and AppendText increments count once; replay does not
charge again and numeric overflow saturates rather than rejecting task output.

Worker admission owns initialization and the internal display version. Gateway
supplies neither output policy nor accounting. The following physical working
parameters remain internal and do not cap total task output:

| Parameter | Rule |
| --- | --- |
| Inline threshold | 64 KiB; larger snapshot/event/result values are immutable references |
| Checkpoint/outbox frame | 1 MiB including provider wrapper; outbox is a 256 KiB subset |
| Finalization reserve | At least 64 KiB each for checkpoint and outbox control/terminal work |

Action count, artifact count, queued tasks, dispatches, identities and reference
metadata retain their structural bounds. A Decision accepts at most one SetStatus.
Startup must validate maximum legal candidate/finalization shapes against the
provider envelope and SessionStorePort::checkpoint_capacity(). Unsupported physical
profiles fail startup; no Gateway change is needed to choose internal parameters.

Reference creation is Worker/provider work. Deterministic content and digests use
stable immutable Blob identities; put_immutable must be confirmed before a
checkpoint references the object. Unknown writes are confirmed with the same
identity. View drafts, snapshots and archive share a reference for identical
content. Large Upsert/AppendText event bodies and final results are externalized
before outbox encoding too. Readers resolve and verify references and apply the
same projection. Blob IO stays outside the pure Runner. Orphan cleanup is deferred.

Structural-limit failures still reject the entire uncommitted Decision and may
finalize only that task as OutputQuotaExceeded through section 6, preserving prior
confirmed output and committed cancellation obligations. Unit/cumulative/snapshot
body size alone no longer triggers this failure. Uninterpretable checkpoints and
unbounded Runner state follow section 7.2. No partial Decision or silent truncation
is allowed. Actual backend failures must not be reported as success.

Full-snapshot Blob writes can still have quadratic cumulative byte cost. The
publication cadence in section 5.1 remains; no unlimited-capacity or linear-cost
claim is made. Immutable snapshot chunking, retention policy and measured
production capacity remain later work.

## 4. 可靠受理、消费与调度

### 4.1 持久受理与规范输入

Integration amendment: Gateway submits a trusted scope and stable business command
through TaskAdmissionCoreService. WorkerTaskAdmission generates initial task state,
creation time and the versioned empty display. Same scoped input and caller content
reuse the selected original request even after clock/directory changes. Changed
body/target still conflicts. InboxProducerPort::lookup returns canonical content,
not acceptance proof: admit must confirm or repair durable activation. Concurrent
first requests use the existing atomic canonical selection and recover the winner
only after comparing caller intent. Accepted/Duplicate map to one success receipt
for Gateway; success is admission, not task completion.


关键输入采用下列协议，不假定跨 Key 事务。新业务指新 Task 的 Start；关键 Agent 回流、既有任务的控制义务和已有 InputId 重试须按其既有身份处理，不能统一按新业务拒绝。新业务 gate／封闭代次的拒绝条件仅适用于 Start；既有控制与关键回流使用保留登记配额继续受理，其身份、内容校验及持久激活要求不变。

1. 校验作用域、sid/tid 关系、格式和大小，重算规范业务摘要，先查同一 InputId 的既有结论。关键 Agent 事件的业务身份与摘要排除 holder epoch、收包时间和投递次数；同一上游关键事件跨 holder 重放保持身份。读取受理代次的预检查仅用于提前选择拒绝路径，不授予写入或执行资格，也不能跳过确定拒绝所需的持久结论。
2. 向按 shard 可重放的持久受理日志追加修复意图，保存完整请求或已确认持久化的不可变引用、观察到的受理代次。此时只产生修复责任，尚未选定规范内容或授予创建 task 的资格。
3. 原子创建／CAS 选定作用域内同一 `InputId` 的规范 sid、tid、业务内容、受理代次及稳定登记身份。相同内容协助完成该规范操作（包括其原代次），冲突内容返回 `ContentConflict`，不得交给 Runner。再以调度文档的 EQ 登记有界在途槽位，绑定该规范操作及其修复意图；仅 Normal 且代次匹配时允许登记新业务。并发同 ID 登记复用同一槽位，不建立第二个受理决定。登记之前不得创建 Submitted task 或追加业务输入；日志／规范记录成功而登记失败仍由修复器收敛。
4. 补齐不可变 spec、create-once 的初始 task 和 Inbox。append 结果未知或并发重试可能产生物理副本；再用 CAS 固定一条规范 `InboxPos`。规范内容一旦选定不得被另一个请求替换。
5. 在同一次调度文档 EQ 中核对槽位、受理代次与 gate，写入该 InputId 的 Accepted 决定并完成持久激活。这是新业务受理与关闭 gate 的先后判定点。结论先复制并确认到规范受理记录，才可移除槽位中的决定；不可把仅一次激活计数递增当作某 InputId 已受理的证明。Duplicate 也必须确认或修复此前步骤；通知成功不是受理成功的前提。

不同请求不能覆盖已有冻结 spec 或推进后的 task。规范记录未完成、或尚无可确认的 Accepted 决定时，消费者在该 Session 的未决位置前停止并触发修复；仅凭 Inbox 存在不得调用 Runner。非规范副本及已确定拒绝的输入只在相应修复／收尾义务已持久登记后推进读取覆盖，不重复驱动 Runner。

持久受理结论与任务终态分离：

| 可确认的事实 | 调用方结果与后续责任 |
| --- | --- |
| 激活 EQ 已写 Accepted，之后发生隔离 | 首次确认返回 Accepted，同内容重试返回 Duplicate 和原 receipt；任务由 §7.2 收尾，受理历史不改写为拒绝 |
| gate 已关闭或原代次已封闭，尚无 Accepted 决定 | 生产者或修复器幂等保存该 InputId 的隔离拒绝结论后返回 Quarantined；同内容重试仍为 Quarantined。若已创建 task，登记 Rejected 收尾义务，走 §6，不能永久停在 Submitted |
| 任一步持久写入／确认结果不明，或激活重试预算耗尽 | 返回可重试的 StoreError::OutcomeUnknown，保留原身份及修复责任；既不返回 Accepted，也不返回确定的 NotApplied |
| 同 InputId 的规范业务内容不同 | ContentConflict；不能改写原决定、task 或执行内容 |

关闭 gate 与激活写发生竞争时，结果以同一调度文档的决定为准；未知的激活必须先查槽位或其已持久复制的结论，不能因为现在隔离就推断此前未受理。规范操作一旦绑定旧代次，即使尚未登记，也不能在恢复后换成新代次重入；须留下持久拒绝结论，新业务重提使用新 InputId。只有日志、尚无规范记录的并发意图仍是未决竞争者，统一收敛到首次规范选择的内容与代次；某次旧日志 append 不单独冻结 InputId 的代次，也不证明已受理／已拒绝。向调用方返回确定拒绝前必须先固定规范拒绝结论，此后重试不得变成 Accepted；任何拒绝写也不能覆盖已有 Accepted 证据。

受理日志的扫描位置与连续完成水位分离。每轮固定上界、有限分页前进；未完成项由持久记录保留并重试，完成水位不跨缺口。单会话长期 Pending 不能阻止扫描其他会话；永久拒绝有持久结果且相关收尾义务可独立恢复后才能确认意图。首批不 GC 该日志和必要去重证据。在受支持 namespace、证据持久保留且无回滚的前提下，InputId 去重没有自动到期窗口；未来回收必须另定窗口外拒绝重建或等价去重证据，不能默认过期后允许重做。

中间 Agent 事件继续使用 `append_stream`，以 `(did, holder_epoch, seq)` 包装身份并由 checkpoint 序列去重。其 Session 已有持久发现记录；周期修复还要检查未消费 Inbox，以发现 append 后、激活前崩溃的输入。成功返回仍要求完成持久激活。关键终态不走这套代次过滤，不能仅因 holder 旧而丢弃有效关键回流。

### 4.2 权威调度与索引修复

每 Session 的独立持久调度文档保存 scheduling owner fence、激活版本、next_check、监听状态及 §4.1 的受理 gate、单调代次和有界在途槽位。每次新受理／中间批次激活都通过 EQ 更新并推进激活版本，即使 due 已经是立即执行。槽位结论复制后可移除工作集条目，但持久日志及规范结论不删除。`active` ZSet 仅为加速索引，不承担互斥和删除授权，因此不要求底层提供 `VersionedZSet`。

Worker 先 `observe(owner)` 安装单调 owner fence，再进行最后一次输入及义务检查。`settle` 原子核对精确 owner 和观察 token；不匹配返回 `NeedsRecheck` 或 `Fenced`，不得只刷新 token 重放旧决定。更新 shard owner 或封印 checkpoint 不等于已安装 scheduling fence。

第一批保留单文档 EQ；独立计数器不能替代 owner、token 与结算的原子校验。每轮冲突重试及用时有上限（§8.2），冲突后重读 owner、token、gate、槽位和待处理义务，重新判断业务决定；超预算退避并公平重排。Inbox 已写但激活未完成时保留修复义务并返回 OutcomeUnknown；扫描是未知结果的恢复路径，不能成为降低成功语义的静默降级。`append_stream` 由既有持久会话发现记录及未消费 Inbox 承担这一责任。

受理修复负责关闭每个意图，会话发现负责找到可能仍有输入／义务的 Session；首批二者可共享同一持久日志作为发现来源，但游标、工作预算和完成条件不同。受理完成水位不是会话发现的裁剪授权。按 shard 的日志是逻辑范围，未规定一个 shard 对应单个物理 Key；provider 须记录实际分段／分区布局、热点及扫描成本。周期扫描核对权威 next_check、未消费 Inbox 和未完成义务，修复索引／通知遗漏；先前 due 为 None 的会话也不能永久不再检查。扫描按固定本轮上界和有界页轮转，不能反复读取最热的第一页。接管枚举包含未来 deadline，不只包含当前到期项。

监听标记只用于减少提示，不作为正确性前提。空闲退出和缓存淘汰必须停止／撤销本地 watch，再按 observe／检查／settle 协议处理持久义务；reader 失败必须被监督并从已提交 consumed 重建。

### 4.3 执行轮次

固定流程为：加载／封印 → 补做前批必要 effects → 有界读取并去重 → 解析不可变引用 → 固定本轮输入及时间 → 纯计算与额度校验 → 确认候选所需 Blob 持久化 → checkpoint CAS → effects → 确认消费及重新调度。

- 同 Session 串行，不同 Session 有界并行；一个会话失败不能占满全部执行槽位。
- `Payload::Ref` 在 reduce 前解析、校验摘要并冻结本轮值。暂态读取失败退避；缺失或摘要冲突不得用空值代替。Runner 无 IO、时钟读取或随机数。
- 一次 candidate 原子包含状态、consumed、dispatch 序列、deadline、展示草稿和固定 outbox；计算过程中不修改已确认状态。
- 只有重复物理记录时仍可提交消费进度。`InboxPos` 保持 opaque；业务层不比较后端游标字符串，不持久化 `DeliveryReceipt`。
- 只以已确认 `CommitReceipt` 授权 ack／trim。共享物理分区只确认连续已提交前缀，不能跨过其他 Session 的未提交输入。

提交未知保留同一 commit ID、摘要和候选，先 resolve 或重试该身份；不得换 ID、执行未确认 effects 或开始下一提交。存储版本相等／更大不证明提交成功。确认前批必要 effects 已完成后，下一提交才能替换 outbox。

## 5. Effects、输出恢复与 DispatchBoard

### 5.1 可重放副作用

所有 effects 使用原 source_commit、effect ID 和内容摘要；接管不重新编号。相同 ID 不同内容是冲突，不是幂等成功。旧 owner 的迟到副作用也按原提交证明和幂等协议处理，不能仅依赖当前进程停止来保证安全。

输出先有序 append，再 publish 完整快照，最后才允许覆盖范围内的裁剪。部分 append 从固定事件 ID 继续；ID 不递增错误必须回读核对内容，或验证持久完成证书，不能直接视为 AlreadyApplied。快照状态、内容和覆盖水位一次发布，不允许回退。

并非每次业务提交都发布快照：状态或 interaction 改变、以及 §6 第二次提交的终态必须发布；纯展示增量自上次发布累计达到 **128 条事件、64 KiB 编码增量或 1 秒**任一条件即安排发布。阈值是初始配置，随任务冻结；Thinking 不计入展示增量且不推动快照内容。计数、已发布覆盖和下一次发布时间随 checkpoint 持久保存；最后一段增量之后没有新输入时，持久 deadline 仍唤醒 Worker 发出快照。时间由运行时注入，重放原提交的 effects 不重新决策。1 秒是健康且预算充足时的调度目标，不是存储故障下的交付保证。

未到阈值的提交仍及时 append 事件；restore 通过旧快照加后续事件得到完整视图。不得将未发布的 checkpoint 草稿当成 TaskFeed 已发布覆盖，或先裁剪再补快照。状态快照覆盖它之前已批准、已确认 append 的输出；终态必须覆盖最终完整展示内容，不能越过未发布的必要输出。快照节流不改变前批必要 effects 完成后才能覆盖 outbox 的规则。

dispatch 创建成功包含持久复查登记；已有记录重试也须修复该登记。采用先持久发现意图、后创建／更新 dispatch 的可修复协议；每个 dispatch 内的 revision、due 和义务为权威状态，sched 只是索引。通知失败不推翻已完成的持久写入，Supervisor 能独立补发。取消及继续输入的幂等证据不得随队列出队而丢失。

### 5.2 保留边界与读取一致性

不从基础 Stream 能力推导 `StreamRecovery`。公共 provider 通过单调逻辑保留边界和受控裁剪满足 TaskFeed：

1. 先确认快照、较早任务归档及相应 append 完成证明覆盖待删除范围。
2. CAS 推进持久逻辑保留边界，再执行 native stream 前缀裁剪。失败可保留更多物理数据，不能扩大已授权范围。
3. restore／follow 在取数前后读取逻辑边界，按固定输出上界取数；覆盖失效时返回 Gap 或重新 restore。即使 native stream 仍有旧数据，也不能忽略逻辑边界。

append 成功但完成证明尚未写入时，相关事件不得裁剪，保证未知结果仍能按固定 ID 回读核对。首批保留边界和必要完成证据，禁止旁路 trim、TTL 或重建日志。底层插件不因此宣称实现了原子 `StreamRecovery` 能力。

restore 必须覆盖初始快照为空的情况，将同一 cut 的快照与后续事件折叠成完整视图。follow 在交付时检查 Gap，不只订阅前检查；列表读取保持作用域隔离和有界分页。

## 6. 结果归档与任务收尾

沿用两次业务提交，不允许 `SetStatus` 绕过归档：

1. `FinishTask` 的第一次提交写入 AwaitingResult 和 SaveResult，固定完整结果、展示快照、终态及归档身份。
2. 确认不可变归档成功后，第二次提交写入结果引用、任务终态及终态输出 effects。
3. 终态事件／快照发布确认后，才启动下一排队任务；终态任务的内存状态可在持久证据完整后释放。

归档不可用时停留在可恢复的 AwaitingResult，不能提前发布 Completed。保存回包丢失时以同身份核对完整内容和引用；同身份不同内容必须冲突。保存成功但第二次提交前宕机，重放恢复同一引用。

公共 ResultStore 与 BlobStore 默认复用 `DbPlugin` 实现不可变持久化，由组合根选择 DB provider。结果按作用域和 tid 唯一，Blob 按身份与摘要保护；不可变对象写入成功但业务提交失败形成的孤儿首批保留，不做提前 GC。后续实现采用新增迁移，不改写已发布迁移。

## 7. 控制输入、监督与隔离

### 7.1 控制与远端不确定性

人工回复按 tid 和 interaction 匹配；取消、deadline 及监督合成输入采用稳定身份与统一去重规则。deadline 的发生身份随其持久登记固定，重试不产生新的超时事件。迟到控制输入不得改写已归档终态。

Worker 只写 dispatch 意图，不直接调用 Agent，也不把远端恢复算法复制进 Worker：

| 状态 | Worker 行为 |
| --- | --- |
| 未领取／未发送 | 保留监督并补发领取提示 |
| 发送结果未知 | 保留义务，交 Connector 按能力恢复；不盲目重新发送或伪造远端失败 |
| 已接受／等待人工输入 | 按 deadline、继续输入和取消义务复查 |
| 取消未确认 | 继续监督；CancelIntent 不等于远端 Canceled |
| 已有确定终态 | 补齐稳定关键回流，收敛已经满足的义务 |
| holder 存活 Unknown | 退避，不按 Dead 接管 |

本地任务结束不删除仍未知的远端发送或取消义务。dispatch 的重调度核对 revision，旧扫描不能覆盖新控制输入。关键回流在任务终态之后仍可用于监督收敛，但不能二次驱动已结束任务。

### 7.2 隔离状态与收尾

新增持久隔离控制状态，区分正常、Quarantined 和 Blocked，并记录原因、业务 head、固定操作 ID 及收尾进度。风险重放前幂等记录 attempt，结果按成功、暂态失败、确定性失败记录；同一次尝试重试不重复计数。正常交接、存储暂态重试和纯 consumed 推进不能被误认为毒性失败或有效业务恢复。

正常执行与接管恢复共用错误分类，不能仅在 takeover 时检测：

| 触发 | 重试与处置 |
| --- | --- |
| 固定状态、输入与上下文下的非法 Decision／确定性执行失败 | **首次确认即停止重跑**（同一失败候选额外重试 0 次）；checkpoint 和独立收尾候选可验证则 Quarantined，否则 Blocked |
| CorruptState、UnsupportedFormat，或 InvalidWorkflow 导致无法安全解释任务／状态 | 首次确认即 Blocked，不机械转换为 Failed；错误码本身不授权终结 |
| §3.3 的结构性输出限制违反 | 仅该任务 Failed，不计入隔离失败次数；单条、累计或完整展示的正文字节数不触发此分支 |
| 暂态存储、连接或 Blob 读取失败 | 按预算退避；结果未知先确认，不计入毒性失败，不能用次数把 Unknown 变成确定失败 |

`takeover_streak` 只是原始恢复计数，不能代替上述分类，也不是机械隔离阈值。失败证据绑定业务 head、输入／上下文摘要及 attempt；同一次 attempt 重试计数一次。进程崩溃而没有确定失败证据时保留未决 attempt，由监督继续确认，不能把接管次数冒充 Runner 已确定失败次数。

| 情况 | 处理 |
| --- | --- |
| checkpoint 可解释、此前必要 effects 可安全完成、终结候选可验证 | 进入 Quarantined；Worker 独立终结器将尚未冻结终态的 Running 任务终结为 Failed、Queued 任务终结为 Rejected，复用两阶段归档，不再调用坏 Runner |
| checkpoint／格式无法解释、证据损坏、必要 effect 永久冲突或无法安全确认 | 进入 Blocked；停止业务推进，保留全部义务和证据，不伪造 Failed |

已进入 AwaitingResult 的任务必须保留第一次 FinishTask 提交冻结的终态、完整结果及来源身份，继续原两阶段归档；即使归档已成功、第二次提交尚未完成，也不能改写为 Failed。已进入 Done 的任务只补齐原有必要 effects，不修改终态或不可变结果。无法确认冻结内容，或原归档存在无法安全解决的冲突时进入 Blocked。

隔离写入核对精确 owner 和业务 head，只修改控制状态，不能覆盖最新 checkpoint 或清除 outbox。无法解析业务状态时仍须保存隔离原因，并依靠独立 dispatch 监督表继续处理远端未知义务。

隔离收口是可恢复协议，不是一次最终扫描：

1. 先按 owner／业务 head 写入隔离操作并暂停普通业务提交，再在权威调度文档以 owner fence、预期受理代次和该操作 ID 执行 EQ，关闭新业务 gate、封闭该代次，并固定当时的在途槽位集合。关门确认之后，捕获并持久保存受理日志各相关分区的上界和扫描进度；provider 必须保证此 cut 覆盖关门前已完成的日志 append，读取未知则继续确认，不宣称收口。暂停至关门之间仍可能成功的 Accepted 必须纳入收尾；隔离控制写成功但 gate 尚未关闭时不得宣称受理已封闭。接管从操作进度继续关门，不要求跨 Key 原子写。
2. 终结器逐项处理封闭代次的登记，生产者／修复器可协助同一幂等操作。已写 Accepted 的保持原结论；未写 Accepted 的固定为隔离拒绝。每个登记都先选定／核对规范内容及结论，补齐其可恢复状态；未进入 checkpoint 的新任务也必须成为独立终结器的输入，按 Rejected 经 §6 归档。已 Running 的按上表处理，冻结任务保持原结果。
3. 在删除工作集槽位、确认该项收尾之前，必须确认对应 task 已终态发布；必要时终结器先 create-once 初始 task 再收尾，使暂停的旧生产者后续 create-once 不会制造新的 Submitted。规范拒绝结论和最终 task 保留；迟到 append 只能形成不再执行的物理副本。不能仅从一次“不存在 task”的读取推断无需收尾。每轮有界分页及提交，不把全部任务塞进一个 checkpoint。
4. 在固定日志 cut 内分页核对该 Session 的规范受理记录，覆盖已经释放槽位的 Accepted 及尚未登记的规范操作；完成证明包含扫描覆盖、未决清单及其收敛结果。只有封闭集合中的全部在途修复意图、该代次已 Accepted 但未消费的新任务、checkpoint 中需收尾的任务都已有持久结论及必要终态发布证明，才能标记本次隔离收尾完成。未决／损坏项保留进度并报告 Blocked，不能跳过。cut 之后才出现的日志意图没有旧代次登记资格；绑定旧代次的规范操作只能拒绝，尚未规范选择的竞争者按 §4.1 收敛，不能扩充已封闭集合或绕过 gate。

因此，预检查后暂停不构成受理资格；规范选择后暂停有日志可修复，Inbox 已写未激活则必有可发现登记。关闭 gate 与激活共享同一 EQ 判定点，解除隔离推进代次，已经规范绑定旧代次或已确定拒绝的操作永不恢复为新业务。关键 Agent 回流和既有控制义务使用独立预算继续持久保存／激活，由终结器或监督消费，不调用坏 Runner，也不授权新 Task。

对独立终结器将结束的任务，每个未结束 dispatch 都生成稳定身份的 `RequestCancel` effect（绑定终结操作、tid 和 did）。effect 必须先进入受 owner／业务 head 保护的 checkpoint 与 outbox，再携带原 `EffectSource` 调用 `DispatchControlPort::request_cancel`；不绕过提交直接取消。同一 effect 重放不重复创建取消意图。Runner 已先取消同一 dispatch 时，后续终结器用不同请求 ID 确认同一待监督义务，保留最初的取消身份和远端观察结果。相同已确认请求 ID 携带不同内容仍是冲突；已确定终态则用持久证据收敛。dispatch 集合过大时，保存遍历进度并分批提交取消义务；本地终态前须确认全部必要取消意图已持久登记，但不等待远端取消完成。已提交意图结果未知继续按 §7.1 监督，不把 CancelIntent 当成远端 Canceled；Blocked 且无法验证提交／归属时不伪造新取消授权，既有义务继续保留。

解除隔离要求受权控制请求、预期 owner／业务 head、隔离身份、已完成的收口证明与固定 request ID；重试幂等，状态已变化则拒绝。恢复步骤持久记录，gate 的开启核对同一隔离身份、owner fence 和封闭代次，并推进至新代次，不能复用旧代次；checkpoint 控制与 gate 均确认恢复前，普通 Worker 不推进新业务。并发的新隔离使旧恢复条件失效，不得只刷新版本重开。它不清除 outbox、远端义务或损坏证据；问题尚未修复时不得自动恢复。首批不提供批量修复控制台或自动修复策略。

## 8. 协调、资源与兼容性

注册、严格不可复活租约、分片认领、主动交接和 Session 封印依赖 #4126。存储 CAS 版本不使用 `epoch × 10^9 + commit_seq`；封印、业务提交与恢复控制共用文档版本，但控制写不推进业务序号。正常已缓存版本的完整 checkpoint 提交不固定增加一次预读；冲突或未知结果的读取单独计费。

交接当前轮次包括在途提交、必要 effects、acquire 和 settle 的收尾，不能只等待 reducer 返回。接收方安装新 scheduling fence 后恢复。checkpoint 首次初始化与缺失异常处理沿用固定版本的 [#4126 §3.3 Session 接管和提交](https://github.com/inclusionAI/Avernet/blob/220c99949bfa56eb76221ffe7078f352d84d1e8b/apps/bcs/specs/2026-11-17-worker-coordination/spec.md#33-session-接管和提交)：防护记录不删除、不过期、不淘汰、不回滚；没有历史存在凭据时才允许首次 create-if-absent，已知存在后发现缺失按数据异常处理，不能退回创建路径。本稿不额外引入 checkpoint 初始化标记，也不声称能从单次缺失识别所有误删；依赖版本变化时重新核对该章节。

### 8.1 资源底线

第一批必须有以下保护：

- 每会话串行；全局 processor、effects、并发 IO、watch、缓存、批次和扫描均设置显式上限，启动时校验相互约束。
- 队列和缓存同时限制条数与字节；存储写入预算计入编码膨胀。超大单条输入在受理前拒绝，不以无限缓存或无界重试处理。
- 存储连接和阻塞读取使用有界预算。不能假设多 Session Stream 在同 slot；不支持跨分区多流读取时采用有界单流轮转，不静默丢弃请求中的流。
- 慢会话、失败 effect、Blob 读取和修复缺口有公平重试与退避；不能占满全部槽位或让后续会话饥饿。
- 缓存淘汰不丢未确认 candidate；无法安全丢弃的状态继续占用受限槽位并对上游背压，从已提交 consumed 恢复读取。
- 基础观测包括受理修复滞后、提交未知、outbox 年龄、归档等待、监督积压、隔离／Blocked 数、reader 重启及资源占用；诊断不记录消息正文或凭据。

### 8.2 冲突、扫描与容量预算

以下为可配置的初始安全 profile，实施验收须记录实际配置和 provider 测量；没有测量不能把它称为可支持容量。所有时间预算使用本地单调时钟；业务 deadline 仍使用注入时间。

| 项目 | 初始预算／规则 |
| --- | --- |
| 单轮 EQ 冲突 | 最多 8 次写尝试或 500ms，先到即停止；单次 IO 也有剩余时间超时。已超时写保留原条件与身份进行确认，不刷新版本强写 |
| 冲突退避 | 指数退避，初始 5ms、上限 200ms，允许运行时抖动；跨轮最多退避 5 秒，回公平队列尾部，不能占用 processor／连接等待 |
| 会话在途新业务登记 | 最多 64 项且编码不超过 64 KiB；登记满时先修复旧项，拒绝新增 work，持久意图已有写入则按 OutcomeUnknown 保留修复责任 |
| 每次扫描工作 | 每页最多 128 项且 1 MiB；每个 shard 一次调度最多 4 页或 100ms，然后保存可恢复游标并让出；单项也有 IO 超时 |
| 每 Worker 扫描额度 | 最多 1,000 个日志项／秒及 200 个会话核对／秒，突发最多一页；全局扫描 IO 并发最多 8，且不超过存储连接池的四分之一 |
| 扫描公平性 | 按 shard、修复／发现／dispatch 监督轮转，每类保留至少四分之一扫描配额，其余共享；未知项重排而非阻塞整页；进程重启仍能完成历史全轮扫描 |
| 发现目标及告警 | 已声明容量和健康存储下，从遗漏激活到重新发现目标不超过 60 秒；最老未决修复年龄／完整发现轮次年龄达目标的 50% 告警，达到目标即暂停新业务受理 |

修复 backlog、保留日志项数／字节、历史 Session 数、单 shard 热点和实际最大物理 Key／分段大小均须有部署容量上限。新增意图前预留预算；无法确认预算、达到上限、池占用持续超限或发现滞后达到上述阈值时，对新业务背压，保留已有修复、结果、关键回流、取消和监督的保留配额。准入并发不能把“检查余额后写入”当成严格限额，provider 需提供有界预留或等价并发预算。磁盘／连接完全故障时保留未知义务并退避，不声称仍能满足 60 秒。

可支持容量由实际保留历史决定，不只由活跃会话数决定。设一轮需遍历 L 个日志项、核对 S 个会话，实际分配速率为 rL、rS，其无故障最短耗时也至少为 `max(L/rL, S/rS)`，重复项、provider 往返和争用另计；低于 60 秒的估算不是达标证明。每个启用组合须交付容量记录：最大 L／S／存储字节、输入与激活速率、热点分布、池大小、实际全轮耗时及最大遗漏发现延迟，并在接近边界时验证背压、重启扫描和关键回流仍可推进。未验证的容量配置不得进入生产启用清单，超出已声明范围不得继续宣称发现目标成立。

访问成本必须按实际 provider 往返计量：关键受理至少包含意图 append、规范记录条件写、在途登记 EQ、创建补齐、Inbox append、规范位置确认、决定／激活 EQ、结论复制及槽位关闭；只有创建场景才创建 spec/task。持久写回包未知时的确认读取、重试次数、EQ 冲突率和延迟单独记录。每页去重后按预算核对权威状态和 Inbox；历史扫描成本随保留量增长。effects、引用解析、完整快照 Blob、归档及隔离分批收尾另计读写条数和字节。pipeline 不扩大原子范围，单元测试不证明固定恢复延迟或生产吞吐。

### 8.3 兼容性与保留

运行时保持 opt-in。新输出动作和持久内容采用显式格式版本；旧 `Show` 不能默认解释为 AppendText。新协议使用独立 namespace，不与旧 GT 写入方混写。现有存量若需迁移，须另行授权并停写、核对版本和证据；本批不承诺透明在线迁移。

实施兼容性说明：#4145 协调原型的 checkpoint 同样使用数字版本 1，但 `runner_state` 是无 task 归属的 `{format_version,value}`；本批采用 `null | {tid,state}` 及 task 生命周期状态。原型 namespace 不支持直接复用，不能因为数字版本相同而推断布局兼容，也不自动推断 tid 或丢弃旧数据。普通读取先核版本头，并把已知原型布局报告为 `UnsupportedFormat { format: 1 }`，不改写或 seal 该 checkpoint；新启用仍须使用全新 namespace。旧证据保留和离线迁移另行授权。

owner、spec、checkpoint、受理及 effect 证据在首批不自动过期、淘汰或回收。持续运行的历史扫描成本和容量上限必须披露；这不是第三批保留治理已完成的证明。

## 9. 实施切片与验收

### 9.1 三个评审切片

| 切片 | 交付 | 退出条件 |
| --- | --- | --- |
| 1A 消费与提交 | 契约变化、受理修复、权威调度、reader、processor、committer 和持久 JSON CAS 适配 | 崩溃与竞争下不丢逻辑输入、不重复业务推进 |
| 1B 副作用与结果 | effects、DispatchBoard、TaskFeed、公共输出投影、Result／Blob provider | 重放收敛、完整恢复、归档后才发布终态 |
| 1C 控制与恢复 | 人工回复、取消、超时、监督、隔离、协调集成及资源底线 | 多 Worker 故障和交接矩阵通过 |

这些是同一批次的评审切片，不是三个独立可上线版本。测试 Runner 仅生成公共契约动作，使用显式测试装配，不注册生产 fallback。真实群 Runner 和完整 Gateway／Connector 引擎留给第二批。

### 9.2 必须通过的测试

| 类别 | 场景与预期 |
| --- | --- |
| 受理 | 每步成功后宕机／回包丢失；同 ID 同／不同内容竞争；无需调用方重试仍可修复，冲突内容永不 reduce |
| 受理与隔离交错 | 预检查后暂停、规范选择后暂停、Inbox 已写未激活、隔离扫描与修复并发；覆盖激活 EQ 成功丢回包及结论复制断点。每个已创建 task 最终有终态或明确 Blocked 证据；Accepted 不改写、拒绝输入不执行，同 ID 重试结论稳定 |
| 隔离收口与恢复 | 关门后才登记的旧规范操作、日志成功而规范选择前崩溃及同 ID 跨代次竞争、旧生产者在收尾后迟到 create／append、隔离／恢复中途换 owner；日志 cut 覆盖已释放槽位的 Accepted，旧规范代次不因重开而获准，不能在收口后制造永久 Submitted |
| 修复公平性 | 同会话未决位置阻塞其后续消费，但不阻塞同 shard 其他会话；扫描重启不永久越过缺口 |
| 调度 | 同 due 新激活使旧 token 失效；旧 settle 不删除新义务；通知与索引同时丢失仍恢复，未来 deadline 被接管 |
| 调度争用 | 同会话多个生产者与 observe／settle／关门／新激活交错；超出 8 次或时间预算时公平重排，已写 Inbox 返回 OutcomeUnknown 且无需调用方重试可恢复；记录真实 provider 往返、冲突及尾延迟 |
| 去重与提交 | 旧 holder 关键终态有效；中间事件按代次／序列过滤；重复物理输入只推进 consumed；未知提交不启动 effects |
| 协调竞争 | 提交／封印／控制写交错保留最新业务头；接收方 observe 后旧 settle 被拒；交接不等待整个远端任务 |
| Effects | 半批重放、旧 Worker 迟到、同 ID 内容冲突、append 成功而 receipt 丢失；ID 错误不冒充已完成 |
| 输出恢复 | Upsert／AppendText 一致折叠、Thinking 排除、初始空快照、并发裁剪及 Gap；恢复得到同一 cut 的完整内容 |
| 输出引用与结构边界 | 超过旧单条、累计和快照大小上限的 Upsert／AppendText／归档可经 Ref 成功，不撑爆 AppendEvents outbox；覆盖 Blob 成功但 checkpoint 未提交／结果未知、编码膨胀、元数据与 artifact 数量边界；结构限制失败保留已确认内容、不提交部分 Decision，下一任务重新 init |
| 快照节奏 | 状态／interaction／终态必发，纯增量触发条数／字节／时间阈值；最后增量后无输入、重启、慢存储和并发 trim 下仍可恢复完整 cut，未发布草稿不授权裁剪；记录完整 Blob 累计写量而不宣称 O(n) |
| 归档 | 两次提交及归档之间各断点、保存未知与重复写、终态发布失败；下一任务不提前启动 |
| 控制监督 | 人工回复重试、取消与完成竞争、超时身份稳定、Unknown 不当 Dead；任务结束后继续监督未知远端义务 |
| 隔离 | 正常路径首次确定性非法 Decision 停止重跑；无法解释状态进入 Blocked，暂态失败不累计毒性；无需坏 Runner 收尾；归档成功、第二次提交前隔离仍保留冻结结果；坏 outbox 不被跳过；受权恢复不清证据 |
| 隔离取消 | 终结器先提交 RequestCancel 再调用端口；取消 effect 部分完成、重放、失权及同 ID 冲突；本地终态后远端仍 Unknown 继续监督，不等待远端取消完成，不伪造 Canceled 或 Blocked 下的新提交 |
| JSON provider | 原子创建唯一胜者、EQ 冲突、真实 null／删除／嵌套收缩／数组多编辑、元数据同名、版本耗尽、失败无部分修改 |
| 资源 | 热会话公平性、缓存淘汰、连接耗尽、超大内容、reader 重启、长期存储故障；工作与内存有界 |
| 历史容量 | 接近已声明日志项数／字节及会话数上限、持续追加新历史、扫描重启与长期缺口；分页／并发／速率均有界，告警和背压生效且保留修复与控制配额；实测健康范围内最大遗漏发现延迟，故障期间不承诺时效 |
| 组合 | 两个以上 Worker 加确定性测试 Runner，结合真实持久 provider 注入故障；不得只用内存模型替代 |

### 9.3 完成判定与本次交付

验证分为公共确定性契约测试、真实 provider conformance、多 Worker 组合故障测试。严格租约、原子创建、单文档 CAS 和存储故障切换持久性分别记录证据；不能相互推导。具体 provider 的实测版本、命令、配置和限制在其宿主记录。

公共模型通过而某个要求交付的 provider 未通过，只能报告公共部分完成。能力不足时拒绝启用是安全行为，不是该组合交付完成。第一批不承诺长期容量、固定恢复时延或特定高吞吐目标。

本次交付仅为 `proposed` 文档与索引。文档验证检查相对链接、接口引用、提案／现状区分、与 #4126 的一致性及公开信息边界；不因提交 SPEC 而运行真实存储实验、迁移或部署。文档送审、实现、运行验证和部署分别报告，不以本节的未来测试矩阵充当已执行证据。

### 9.4 2026-11-19 评审修订

本轮按 [PR #4144](https://github.com/inclusionAI/Avernet/pull/4144) 已对齐的评审意见修订，状态仍为 proposed；未修改已采用的契约或运行时。

本轮已检查 16 个相对链接、导航链路、固定依赖章节和公开信息边界，并完成独立静态审阅；修正了日志 cut 覆盖、规范选择前的跨代次竞争语义和大事件引用化三处问题。未执行运行时或真实 provider 测试；上述初始预算仍须实施验收，不能视为测量结果。提案尚未成为运行事实，因此不把这些拟议行为提升为已采用契约或运维知识。

| 评审主题 | 本稿决定与位置 |
| --- | --- |
| 并发受理与隔离 | §4.1／§7.2：激活 EQ 判定、持久在途登记与结论、封闭代次收口及未消费任务终结；预检查和最终扫描不能代替协议 |
| 发现与历史成本 | §4.2／§8.2：分开修复与发现职责、明确容量记录／扫描预算／告警背压；首批不引入 GC／TTL，也不将完成水位当成裁剪授权 |
| 调度 EQ 争用 | §4.2／§8.2：保留单文档原子校验，限定重试、退避及未知结果责任；未经等价证明不拆 owner 与激活计数 |
| 输出规模与快照 | §3.3／§5.1：无输出正文大小配额；保留内部物理阈值、先持久化 Blob、发布节奏和任务级结构限制失败；不强制小快照外置，分片和后续限制策略另评估 |
| 隔离触发与取消 | §7.2：首次确定性失败分类、Blocked 边界、已提交 RequestCancel 及终态后监督；不以接管计数代替失败证据 |
| 依赖定位 | §8：固定版本 §3.3 章节链接及 checkpoint 缺失前提；更正 #4126 文档合入状态，保留历史代码对照 |

### 9.5 本轮验收边界与实现核对

逐条核对以 #4144 的 `8811efbc9751004747451d00c5ea1e6a30a6b224` 原稿为基线，
[核对矩阵](spec-audit.md) 区分原始要求、实现采用、确定性测试、真实存储证据和剩余项。
本地压力/容量测试按用户最新要求排除；DEV 耗时仅诊断，不作最终容量或性能结论。
未认证的容量配置仍不进入生产启用清单。存储 HA/节点切换按用户确认的平台前提记录，
本轮不另行执行节点故障切换；OB 实例验收继续 defer。
Singlebox 使用用户指定的内存 Store mock 装配真实 Worker 进程，Result/Blob 复用 SQLite；
进程内接管不证明 Store 跨进程持久性，真实存储功能/故障验证单独走 PROXY。
