---
status: proposed
date: 2026-11-18
owner: BCS distributed Worker
---

# BCN Worker 第一批通用运行时 SPEC v1

## 1. 状态、目标与基线

本稿定义 Worker 的通用执行与恢复闭环，供实现前评审。本次仅提交 SPEC 和导航，不修改 Rust 契约、运行时、插件、配置或数据库迁移；文档合入不代表功能实现、测试通过或部署完成。

目标是：输入可靠受理后，能够被发现、去重、计算、提交、执行副作用、发布输出并归档结果；Worker 重启、通知丢失或所有权交接后，仍能从持久状态继续推进。实际群协作规则由后续 Runner 实现，本批不能用生产占位 Runner 冒充真实群功能。

代码基线为 Avernet `7358ad11c314c7c221f1f66630f0aaec89e92997`。协调依赖 [PR #4126](https://github.com/inclusionAI/Avernet/pull/4126)，设计对照版本为 [`220c99949bfa56eb76221ffe7078f352d84d1e8b`](https://github.com/inclusionAI/Avernet/blob/220c99949bfa56eb76221ffe7078f352d84d1e8b/apps/bcs/specs/2026-11-17-worker-coordination/spec.md)。该 PR 仍是提案，不能视为已实现的前置能力；后续依赖版本变化须重新核对契约。

相关契约和导航：

- [分布式契约 SPEC](../2026-11-15-distributed-contracts/spec.md)及[任务划分](../2026-11-15-distributed-contracts/tasks.md)。
- [业务存储端口](../../crates/service-api/bcs-service-api/src/port/repo/distributed/README.md)、[SessionStore](../../crates/service-api/bcs-service-api/src/port/repo/distributed/session.rs)、[Inbox 与调度](../../crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs)。
- [Runner](../../crates/service-api/bcs-service-api/src/core/dist_runner.rs)、[输出与任务模型](../../crates/contracts/bcs-dist-model/src/task.rs)、[TaskFeed](../../crates/service-api/bcs-service-api/src/port/repo/distributed/task_feed.rs)。
- [DispatchBoard](../../crates/service-api/bcs-service-api/src/port/repo/distributed/dispatch.rs)、[Result 与 Blob](../../crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs)。
- [存储能力](../../crates/plugin-api/bcs-store-api/src/common.rs)、[JSON](../../crates/plugin-api/bcs-store-api/src/json.rs)、[Stream](../../crates/plugin-api/bcs-store-api/src/stream.rs)。
- [架构约束](../../../../docs/arch/arch.rules.md)、[协议测试规则](../../../../docs/arch/protocol-contract-tests.md)及 [BCS ADR 导航](../../docs/adr/README.md)。本稿不修改已有 ADR 或以提案覆盖当前 Rust 定义。

基线已有接口声明和部分 Inbox producer，但尚无本稿的完整 Worker。现有 producer 的监听读取仍为占位逻辑；受理记录仅含摘要及 sid/tid，且在 Inbox append 后登记 active，不能直接作为本稿的规范输入选择和独立修复协议。

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
| 存储插件 | 新增受限的 `PersistentJsonCas` 能力及独立接口，提供持久文档读取、同版本路径投影、原子创建、精确版本条件下的 Replace／Patch |
| Inbox provider | 补齐持久受理意图、规范内容与位置、受理修复、监听状态和调度；保持生产、消费、调度端口分离 |
| SessionStore | 增加精确 owner＋业务 head 保护的隔离状态、风险尝试结果及条件解除隔离；控制写不推进 `commit_seq` |
| Runner／输出 | `Show` 增加稳定 artifact 身份和明确更新操作；输出事件、展示草稿及恢复读取共享确定性投影 |
| Worker 控制面 | 保留 `snapshot/drain(grace)`，通过 application facade 暴露受权隔离查询和条件恢复，不新增业务提交入口 |

### 3.1 持久 JSON CAS 子集

`PersistentJsonCas` 支持不存在时原子创建，以及读取到的精确文档版本下的一次更新；首次创建版本为 1，每次成功更新推进版本，失败不修改内容或版本，耗尽失败而不回绕。存在性和版本条件必须与写入原子核对，不能以先 GET 再普通覆盖实现。

逻辑 Replace 必须删除旧值中不再存在的字段；Patch 沿用现有 JSON 指针和有序编辑语义，真实 null 与字段缺失可区分，多处编辑全部成功或全部失败。元数据与业务 JSON 隔离，业务 `rev`、`_store` 等名字不被禁用。适配器可以使用可逆私有编码，但不能要求所有后端都存字符串。

该子集不提供 TTL、删除、`Any`、`Newer` 或删除重建后的历史承诺。现有 `AtomicJson` 完整契约保持不变；现有消费者必须通过接口与测试变更显式改用子集，不得只放宽构造检查。原子创建与 EQ 更新分别验收，已有文档的 EQ 测试不能证明 create-if-absent 可用。

### 3.2 输出投影

展示单元以 `(node, artifact_id, class)` 为稳定身份；`artifact_id` 由纯计算确定，不依赖随机数或重试次数。公共操作为：

- `Upsert`：用完整 Payload 新建或替换该单元，保留已存在单元的排列位置。
- `AppendText`：向文本单元追加字符串；不存在时从空文本创建，已有非文本单元则拒绝该 Decision。任意 JSON 不做隐式 MERGE。

完整展示状态为按首次出现顺序排列的单元集合，使用版本化公共编码；`TaskContent.snapshot` 可以保存其 Inline 值或不可变 Ref。`OutputBody::Artifact` 携带同一操作。事件折叠、checkpoint 草稿和 restore 复用同一纯投影实现，不能在测试 Runner 内藏一套自定义快照语义。

Thinking 只产生临时事件，不进入完整展示快照和最终归档，也不能驱动策略。`SetStatus` 只接受非终态；Completed／Failed／Canceled／Rejected 必须通过 `FinishTask` 和第 6 节归档路径。先验证整个 Decision，再构建候选；不合法动作不能部分提交或执行。

## 4. 可靠受理、消费与调度

### 4.1 持久受理与规范输入

关键输入采用下列协议，不假定跨 Key 事务：

1. 校验作用域、sid/tid 关系、格式和大小，重算规范业务摘要。关键 Agent 事件的业务身份与摘要排除 holder epoch、收包时间和投递次数；同一上游关键事件跨 holder 重放保持身份。
2. 向按 shard 可重放的持久受理日志追加修复意图，保存完整请求或已确认持久化的不可变引用。此后进程消失，修复器仍能独立完成受理。
3. 原子创建／CAS 选定作用域内同一 `InputId` 的规范 sid、tid 和业务内容。相同内容协助完成，冲突内容返回 `ContentConflict`，不得交给 Runner。
4. 补齐不可变 spec、create-once 的初始 task 和 Inbox。append 结果未知或并发重试可能产生物理副本；再用 CAS 固定一条规范 `InboxPos`。规范内容一旦选定不得被另一个请求替换。
5. 更新权威调度文档完成持久激活，记录受理完成后返回 Accepted。Duplicate 也必须确认或修复此前步骤；通知成功不是受理成功的前提。

不同请求不能覆盖已有冻结 spec 或推进后的 task。规范记录未完成时，消费者在该 Session 的未决位置前停止，并触发修复；不能先执行 loser 内容，再向请求方返回冲突。非规范物理副本只推进读取覆盖，不重复驱动 Runner。

受理日志的扫描位置与连续完成水位分离。每轮固定上界、有限分页前进；未完成项由持久记录保留并重试，完成水位不跨缺口。单会话长期 Pending 不能阻止扫描其他会话；永久拒绝有持久结果后才能确认对应意图。首批不 GC 该日志和必要去重证据。

中间 Agent 事件继续使用 `append_stream`，以 `(did, holder_epoch, seq)` 包装身份并由 checkpoint 序列去重。其 Session 已有持久发现记录；周期修复还要检查未消费 Inbox，以发现 append 后、激活前崩溃的输入。成功返回仍要求完成持久激活。关键终态不走这套代次过滤，不能仅因 holder 旧而丢弃有效关键回流。

### 4.2 权威调度与索引修复

每 Session 的独立持久调度文档保存 scheduling owner fence、激活版本、next_check 和监听状态。每次新受理／中间批次激活都通过 EQ 更新并推进激活版本，即使 due 已经是立即执行。`active` ZSet 仅为加速索引，不承担互斥和删除授权，因此不要求底层提供 `VersionedZSet`。

Worker 先 `observe(owner)` 安装单调 owner fence，再进行最后一次输入及义务检查。`settle` 原子核对精确 owner 和观察 token；不匹配返回 `NeedsRecheck` 或 `Fenced`，不得只刷新 token 重放旧决定。更新 shard owner 或封印 checkpoint 不等于已安装 scheduling fence。

受理日志同时提供会话发现来源。周期扫描核对权威 next_check、未消费 Inbox 和未完成义务，修复索引／通知遗漏；先前 due 为 None 的会话也不能永久不再检查。扫描按固定本轮上界和有界页轮转，不能反复读取最热的第一页。接管枚举包含未来 deadline，不只包含当前到期项。

监听标记只用于减少提示，不作为正确性前提。空闲退出和缓存淘汰必须停止／撤销本地 watch，再按 observe／检查／settle 协议处理持久义务；reader 失败必须被监督并从已提交 consumed 重建。

### 4.3 执行轮次

固定流程为：加载／封印 → 补做前批必要 effects → 有界读取并去重 → 解析不可变引用 → 固定本轮输入及时间 → 纯计算 → checkpoint CAS → effects → 确认消费及重新调度。

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

| 情况 | 处理 |
| --- | --- |
| checkpoint 可解释、此前必要 effects 可安全完成、终结候选可验证 | 进入 Quarantined；Worker 独立终结器将尚未冻结终态的 Running 任务终结为 Failed、Queued 任务终结为 Rejected，复用两阶段归档，不再调用坏 Runner |
| checkpoint／格式无法解释、证据损坏、必要 effect 永久冲突或无法安全确认 | 进入 Blocked；停止业务推进，保留全部义务和证据，不伪造 Failed |

已进入 AwaitingResult 的任务必须保留第一次 FinishTask 提交冻结的终态、完整结果及来源身份，继续原两阶段归档；即使归档已成功、第二次提交尚未完成，也不能改写为 Failed。已进入 Done 的任务只补齐原有必要 effects，不修改终态或不可变结果。无法确认冻结内容，或原归档存在无法安全解决的冲突时进入 Blocked。

隔离写入核对精确 owner 和业务 head，只修改控制状态，不能覆盖最新 checkpoint 或清除 outbox。无法解析业务状态时仍须保存隔离原因，并依靠独立 dispatch 监督表继续处理远端未知义务。

已观察到隔离时拒绝新业务受理；并发中已经选定规范内容的输入必须保留，由隔离收尾处理，不能静默丢弃。关键 Agent 回流可继续持久保存；隔离不会授权新业务运行。

解除隔离要求受权控制请求、预期 owner／业务 head、隔离身份与固定 request ID；重试幂等，状态已变化则拒绝。它不清除 outbox、远端义务或损坏证据；问题尚未修复时不得自动恢复。首批不提供批量修复控制台或自动修复策略。

## 8. 协调、资源与兼容性

注册、严格不可复活租约、分片认领、主动交接和 Session 封印依赖 #4126。存储 CAS 版本不使用 `epoch × 10^9 + commit_seq`；封印、业务提交与恢复控制共用文档版本，但控制写不推进业务序号。正常已缓存版本的完整 checkpoint 提交不固定增加一次预读；冲突或未知结果的读取单独计费。

交接当前轮次包括在途提交、必要 effects、acquire 和 settle 的收尾，不能只等待 reducer 返回。接收方安装新 scheduling fence 后恢复。checkpoint 首次初始化与缺失异常处理沿用 #4126 的方案 A，本稿不额外引入初始化标记，也不声称能从单次缺失识别所有误删。

第一批必须有以下保护：

- 每会话串行；全局 processor、effects、并发 IO、watch、缓存、批次和扫描均设置显式上限，启动时校验相互约束。
- 队列和缓存同时限制条数与字节；存储写入预算计入编码膨胀。超大单条输入在受理前拒绝，不以无限缓存或无界重试处理。
- 存储连接和阻塞读取使用有界预算。不能假设多 Session Stream 在同 slot；不支持跨分区多流读取时采用有界单流轮转，不静默丢弃请求中的流。
- 慢会话、失败 effect、Blob 读取和修复缺口有公平重试与退避；不能占满全部槽位或让后续会话饥饿。
- 缓存淘汰不丢未确认 candidate；无法安全丢弃的状态继续占用受限槽位并对上游背压，从已提交 consumed 恢复读取。
- 基础观测包括受理修复滞后、提交未知、outbox 年龄、归档等待、监督积压、隔离／Blocked 数、reader 重启及资源占用；诊断不记录消息正文或凭据。

访问成本必须按实际 provider 往返计量：关键受理至少包含意图 append、规范记录条件写、创建补齐、Inbox append、规范位置确认及激活；只有创建场景才创建 spec/task。发现扫描每页限制为 P 个意图／会话，去重后按预算核对权威状态和 Inbox；重复扫描历史的成本随保留量增长。effects、引用解析、归档、冲突确认另行计数。pipeline 不扩大原子范围，单元测试不证明固定恢复延迟或生产吞吐。

运行时保持 opt-in。新输出动作和持久内容采用显式格式版本；旧 `Show` 不能默认解释为 AppendText。新协议使用独立 namespace，不与旧 GT 写入方混写。现有存量若需迁移，须另行授权并停写、核对版本和证据；本批不承诺透明在线迁移。

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
| 修复公平性 | 同会话未决位置阻塞其后续消费，但不阻塞同 shard 其他会话；扫描重启不永久越过缺口 |
| 调度 | 同 due 新激活使旧 token 失效；旧 settle 不删除新义务；通知与索引同时丢失仍恢复，未来 deadline 被接管 |
| 去重与提交 | 旧 holder 关键终态有效；中间事件按代次／序列过滤；重复物理输入只推进 consumed；未知提交不启动 effects |
| 协调竞争 | 提交／封印／控制写交错保留最新业务头；接收方 observe 后旧 settle 被拒；交接不等待整个远端任务 |
| Effects | 半批重放、旧 Worker 迟到、同 ID 内容冲突、append 成功而 receipt 丢失；ID 错误不冒充已完成 |
| 输出恢复 | Upsert／AppendText 一致折叠、Thinking 排除、初始空快照、并发裁剪及 Gap；恢复得到同一 cut 的完整内容 |
| 归档 | 两次提交及归档之间各断点、保存未知与重复写、终态发布失败；下一任务不提前启动 |
| 控制监督 | 人工回复重试、取消与完成竞争、超时身份稳定、Unknown 不当 Dead；任务结束后继续监督未知远端义务 |
| 隔离 | 无需坏 Runner 收尾；归档成功、第二次提交前进入隔离仍保留原冻结终态与不可变结果身份；坏 outbox 不被跳过；Blocked 不伪造 Failed；受权恢复不清证据 |
| JSON provider | 原子创建唯一胜者、EQ 冲突、真实 null／删除／嵌套收缩／数组多编辑、元数据同名、版本耗尽、失败无部分修改 |
| 资源 | 热会话公平性、缓存淘汰、连接耗尽、超大内容、reader 重启、长期存储故障；工作与内存有界 |
| 组合 | 两个以上 Worker 加确定性测试 Runner，结合真实持久 provider 注入故障；不得只用内存模型替代 |

### 9.3 完成判定与本次交付

验证分为公共确定性契约测试、真实 provider conformance、多 Worker 组合故障测试。严格租约、原子创建、单文档 CAS 和存储故障切换持久性分别记录证据；不能相互推导。具体 provider 的实测版本、命令、配置和限制在其宿主记录。

公共模型通过而某个要求交付的 provider 未通过，只能报告公共部分完成。能力不足时拒绝启用是安全行为，不是该组合交付完成。第一批不承诺长期容量、固定恢复时延或特定高吞吐目标。

本次交付仅为 `proposed` 文档与索引。文档验证检查相对链接、接口引用、提案／现状区分、与 #4126 的一致性及公开信息边界；不因提交 SPEC 而运行真实存储实验、迁移或部署。文档送审、实现、运行验证和部署分别报告，不以本节的未来测试矩阵充当已执行证据。
