# Bridge Plugin 已消费事件回收 Spec

> 日期：2026-10-19
> 状态：本地实现完成并已执行公共回归；存在已复现的宿主测试限制，尚未发布
> 源码基准：`25e8027156d807335e24dca7b8d4c413427113e5`
> 范围：`bcs-bridge-core` 的 Plugin 事件保留与投递生命周期
> 验证记录：[Validation](../plans/2026-10-19-bridge-plugin-retention-reclamation-validation.md)
> 执行计划：[Execute Plan](../plans/2026-10-19-bridge-plugin-retention-reclamation-execute-plan.md)

## 1. 决策摘要

**Plugin 模式在现有发送游标提交后回收已消费的事件前缀，使 16384 条 / 32 MiB
限制衡量待投递积压，而不是整个 run 的累计产出。Gateway 保持全量重放。**

- 按用户追加确认，将事件数上限由 8192 翻倍至 16384；字节上限维持 32 MiB，
  不把 thinking 排除出统计。共享常量也提高 Gateway 的数量上限，但不改变其全量保留语义。
- 不在缓冲满时无条件删除最旧事件，不删除尚需投递的数据。
- 不改变事件内容、序号、顺序、V2/V3 wire shape 和幂等请求行为。
- 不把本地 WebSocket 写入成功称为服务端 ACK，也不新增 exactly-once 保证。
- 首期不引入磁盘事件存储、delta 合并、批量发送或新的配置项。

追加决策（2026-10-19）：只翻倍事件数，保留字节预算作为内存保护。以下故障统计
和原始红灯测试保留旧的 8192 边界；规范中的当前上限为 16384。

## 2. 问题与已知证据

### 2.1 修复前行为

`run.rs::push_frame()` 将引擎事件追加到 run buffer。Plugin 的
`flush_events()` 通过 `next_after(cursor)` 读取并发送，成功后只推进游标。
运行期间的已发送记录仍留在 buffer，通常到终态投递完成后才统一释放。

因此，`buffer.len() >= 8192` 会把下一条事件替换为 `buffer_overflow`
终止错误并取消 run，即使绝大多数历史事件已发送且字节预算远未用完。

### 2.2 脱敏现场统计

同一会话的两次 Plugin 超限记录如下；不提交会话标识、正文、思考内容、
工具参数、凭据或原始运行日志。

| 样本 | thinking 增量 | 文本增量 | 工具事件 | 错误前保留记录 | 错误前预算字节 |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 6924 | 424 | 844 | 8192 | 5,533,357 |
| B | 7830 | 316 | 46 | 8192 | 3,996,384 |

两次转换后 trace 都包含连续的 seq 1～8193，最后一条为终止错误。
样本 A 的 thinking 增量合计约 71 KB，只有 delta，没有重复累计全文。

**证据边界：旧 overflow 日志与 converted trace 未记录发送 cursor，无法精确
还原这两次故障瞬间的未投递数量。8192 是累计保留数，不能解释为 8192 条积压。
本方案依据已有投递契约设计，不以“故障时积压已经证明很小”为前提。**

### 2.3 与之前 thinking 字节优化的区别

先前 Codex 路径将 `agent_thinking(delta, accumulated_text)` 改为
`agent_thinking(delta, None)`，去掉每条事件中的累计全文副本，实际减少了
重复内容，而非取消统计。thinking 仍占内存、仍计入条数和字节预算。

本方案处理的是另一维度：**每条事件投递完成后还保留多久**。

## 3. 目标与非目标

### 3.1 必须达到

- **FR-1**：Plugin 在待投递积压未超限时，累计事件数超过 16384 不得触发
  retention 保护；thinking、chat、tool 等类型均使用同一计费规则。
- **FR-2**：只有按当前传输契约已完成消费的连续前缀可以回收。
- **FR-3**：失败发送的记录必须保留；普通重连延续既有 cursor，不重启引擎。
- **FR-4**：序号跨回收和队列清空仍单调递增；不改写已经生成的事件。
- **FR-5**：未消费积压仍受新的条数上限和原有字节预算保护，终止错误仍可被保留。
- **FR-6**：Gateway 同 ID 重试从头重放、多个读取者及终态 grace 行为不退化。
- **FR-7**：run fingerprint、会话关联、terminal 与 tombstone 不随正文回收丢失。
- **FR-8**：诊断能够区分累计产出、消费游标和当前保留量。

### 3.2 首期明确不做

- 不承诺无限离线、无限积压、任意生产速率下任务永不终止。
- 不新增服务端接收/落库 ACK、进程崩溃恢复或端到端 exactly-once。
- 不修改 Gateway 的全量重放契约，其累计 retention 上限问题仍是独立范围。
- 不合并/删除 delta，不修改 thinking 文本、工具事件、交互事件或终态内容。
- 不改变每 run 每 tick 一条事件的现有发送调度；除已确认的事件数翻倍外，
  不调整字节、并发常量或新增运行开关。
- 不迁移数据库，不修改会话持久化、Provider 启动或引擎运行时实现。

## 4. 契约与安全边界

### 4.1 Plugin 与 Gateway 的保留策略

| 模式 | 读取者 | 重试/重连起点 | 保留策略 |
| --- | --- | --- | --- |
| Plugin | 每个 pending run 的单一逻辑消费游标 | 当前 `PendingRun.cursor` 之后 | 可回收已提交前缀 |
| Gateway | 每个 SSE stream 的独立游标 | 同 ID 重试从头读取 | 完整历史保留至既有终态 grace 清理 |

依据现有测试：

- `plugin_websocket::reconnect_delivers_offline_final_without_restarting_engine`
  要求重连交付离线终态、重复请求不重放已消费事件、不再次启动引擎。
- `e2e_webhook::duplicate_send_reattaches_with_replay` 要求 Gateway 重试
  先重放已有 seq，再跟随新事件，不得删除其所需历史。

Plugin 的 pending run 与 route 跨 socket 重连保留；活跃 pending route 不按
普通路由 TTL 淘汰。实现必须维持该约束，不能重连时把 cursor 重置为 0。

### 4.2 “消费完成”的精确定义

| 处理结果 | 提交 cursor | 回收记录 |
| --- | --- | --- |
| `encode -> Some(frame)` 且本地 `send` 成功 | 是 | 是 |
| `encode -> None`，编码器按既有契约过滤该事件 | 是 | 是 |
| send 返回错误、超时或未完成 | 否 | 否 |
| 编码失败 | 不作为原事件投递成功提交 | 沿用错误上报、取消和终态清理路径 |

接口使用 `commit_delivery_through` 或等价名称，不命名为服务端 `ack`。

本地 send 成功不保证 BCS 已接收或持久化；send 失败也可能发生在对端已收到
之后。现有机制本身存在这种不确定性，前缀回收既不消除也不扩大为新的保证。
测试应验证“未完成消费的记录不会被本地丢弃”，不能声称网络层 exactly-once。

### 4.3 对外兼容性

- HTTP/WS 报文、V2 per-Bot wire seq、V3 outer seq 与 payload seq 相等规则不变。
- 保留 `errorKind=buffer_overflow` 与 `stopReason=buffer_overflow`，仅增强诊断。
- 同 ID 同 body 仍走幂等复用，不同 body 仍冲突；不会因 buffer 为空重新执行。
- 不给没有 `name`/`toolName` 的既有工具结果补名字，也不改变其身份关联条件。
- Rust crate 的内部公开类型若调整，需在同一变更中更新所有仓库内调用者及测试；
  在变更说明中列明源码兼容影响，不把“wire 不变”误称为所有 API 不变。

## 5. Buffer 设计

### 5.1 数据结构

提取 `core/src/run_buffer.rs`，避免继续扩大已经超过 1000 行的 `run.rs`。
建议结构如下，字段命名可按实现微调，约束不可放宽：

```rust
struct RetainedRecord {
    event: RunEvent,
    accounted_bytes: usize,
}

struct RunEventBuffer {
    records: VecDeque<RetainedRecord>,
    retained_bytes: usize,
    released_through: u64,
    last_emitted_seq: u64,
}
```

保留策略在 run 创建、引擎启动前固定。核心 buffer 管理序号范围、预算和回收；
传输层判定一次消费是否完成，不能自行修改队列字段或取消预算保护。

### 5.2 追加与序号

- seq 是 run 级标识，不能用 `records.len() + 1` 生成。
- 正常追加保留完整 typed event、raw 与元数据，不改变序列化/编码语义。
- 入队时计算并保存 `accounted_bytes`，回收使用保存值，不再次序列化。
- 每次入队、出队、清空及其计费变化都在同一把短时 mutex 内完成。
- 超限时沿用“将当前事件替换为一个终止错误”的行为；终止错误使用该序号，
  保留独立的额外一条终止记录余量。错误记录也必须记录自身计费值，不能出现
  入队未计费、出队却扣费的下溢；该余量不可递归制造多个错误。

### 5.3 前缀回收

`commit_delivery_through(seq)` 的调用约束：

1. 仅用于 Plugin 的消费前缀；Gateway 禁止调用该回收策略。
2. 提交的是已逐条完成处理的连续序列，不能跳过失败记录或提交未来序号。
3. 同一 cursor 重复提交幂等，落后的提交不倒退、不再次扣减；未来/非法跳跃
   显式拒绝，不能以 `saturating_sub` 隐藏计费或游标错误。
4. 使用 `pop_front()` 回收对应记录，扣减精确保存值，更新 `released_through`。
5. 队列清空不清除 `last_emitted_seq`；下一条接续原序号。
6. 正常终态记录仍按顺序完成投递后回收；run 的 terminal 标志由原有 driver
   cleanup 完成后发布，不因队列暂时为空提前发布。

当前一次只发送一个记录，可要求新提交恰为上一已提交序号的后继。
若未来引入批处理，需要显式证明整段均已完成，不能放宽为任意大于旧值的序号。

### 5.4 读取

- Plugin `next_after(cursor)` 应利用保留区间和队列索引访问，避免每次从头线性扫描。
- 若 cursor 落在已回收区间，应返回明确的内部游标错误，不能静默跳到队首。
- Gateway 保持 snapshot/subscribe 的原子性和从头重放能力；不得套用 Plugin
  的回收下界，也不得因为某个 SSE 读取者消费完就回收历史。
- 不持锁跨 `.await`；网络发送只持有当前事件的必要副本。

### 5.5 内存语义

回收会丢弃 buffer 对事件及其正文的所有权，并释放对应 payload 分配；暂时被
发送器、broadcast 或其他现有有界队列持有的副本可能继续存活。
`VecDeque` 容量也不会因每次 pop 自动缩小，不能承诺 RSS 立即等比例下降。

- 保持容量增长受队列预算约束；不在每个事件回收时 `shrink_to_fit()`。
- run 最终释放时可按原有策略收缩容器。
- retained bytes 是计费值，不是准确 RSS；压测同时观察保留计数与实际峰值内存。

## 6. 投递与生命周期接线

### 6.1 成功路径

```text
读取 cursor 之后的一个事件（不出队）
  → encode
  → 有 frame：等待 send 成功；无 frame：确认既有过滤语义
  → 同步提交消费进度：pending.cursor、route.cursor、buffer 前缀
  → 继续当前发送调度
```

成功后的本地提交区间不包含 `.await`。提交前验证游标一致性，避免部分字段已更新
而另外部分因可预期校验错误失败；所有 buffer 变更通过受控接口完成。

生产者与消费者可能并发。保护依据同一锁下的实际保留量判断，不承诺无限瞬时
突发可被吸收。终态、取消、发送失败、回收交错均必须有确定性测试。

### 6.2 错误与关闭路径

- 网络失败：不提交当前序号，保持待投递记录和 cursor，走现有重连逻辑。
- 编码失败：仍先尝试报告已有的 bounded encoding error，再按现有语义取消 run。
  不把错误替代帧成功发送等同于原事件成功投递；失败路径的丢弃属于明确终止清理。
- 普通积压超限：保留终止错误、取消当前 run；不得无声覆盖工具或交互记录。
- 显式 shutdown/kicked/fatal：沿用已有取消、等待 cleanup 和释放策略，不把强制
  关闭期间的清理描述为正常完整投递。
- 正常完成：terminal 已发布且队列无剩余后，调用现有 `release_delivery()`，
  解除 delivery pin、开始 tombstone grace；不得在活跃 run 队列为空时提前执行。
- fingerprint、run/session 关联及 tombstone 保持原有独立生命周期。

### 6.3 消除 Plugin 的无用 Gateway 订阅

当前 runtime 创建 Gateway stream 后由 Plugin 丢弃。回收上线前，应将交付对象
显式区分为 Gateway stream 与 Plugin handle，避免产生不参与消费契约的历史读取者。

推荐用显式 enum 表达交付类型，而不是通过可空 stream 隐藏不合法状态：

```rust
enum RunDelivery {
    Gateway { stream: RunStream },
    Plugin,
}
// RuntimeReply 的 Run 分支携带 handle + delivery。
```

具体 Rust API 在实施时与调用点一起调整。Gateway 必须先同步订阅再启动引擎，
包括同 ID 重试分支；Plugin 不创建该 stream。检查 webhook、QwenWork 和直接
调用 runtime 的测试，确保模式不匹配显式报错而不是 unwrap/panic 或静默空流。

## 7. 资源成本与可观测性

### 7.1 成本

- 每条正常 Plugin 事件：一次入队、一次成功消费提交、一次出队；队列操作均摊 O(1)。
- 计费序列化只在入队时执行，回收不重新编码、不扫描全量历史。
- 新增数据库读写、网络 RPC、持久化迁移均为 **0**。
- 条数上限为 16384（原来 8192）；字节 32 MiB、额外终止记录余量保持不变。
- 现有每 Bot 最多 16 个 pending run 继续生效；多 Bot 和多 run 的预算会累加，
  本修复不引入进程级全局内存配额，也不声称 32 MiB 是全进程上限。
- 未修改的发送调度仍可能造成真实背压；正常链路也不等同于任意速率下无积压。

### 7.2 诊断

在 overflow 与 run 完成摘要中记录以下非敏感字段，避免逐 token INFO 日志：

- `retention_mode`：Plugin 待投递 / Gateway 全量重放；
- `last_emitted_seq`、`delivery_cursor`（Plugin）；
- `retained_events`、`retained_bytes`、本次候选事件大小；
- `limit_reason`：events / bytes / both；
- 已回收累计条数和字节数、保留高水位（如成本可控）。

产生序号、消费游标和保留量采用同一 buffer 快照中的值；明确统计是在终止错误
追加之前还是之后。若另记生产尝试数，注明超限候选被错误替换的口径。

不记录正文、thinking 内容、工具参数、凭据；不把 run ID 作为无界指标标签。
不增加新的指标基础设施依赖，优先使用已有 tracing。

## 8. 验收矩阵

| ID | 场景 | 必须断言 |
| --- | --- | --- |
| AC-01 | Plugin 持续消费并累计产生至少 20,000 个混合事件 | 不因累计量超限；seq 连续；终态可到达；保留量跟随积压 |
| AC-02 | 同一队列多次消费至空再追加 | seq 不重置；字节正确归零并重新累计 |
| AC-03 | 提交重复/落后/未来/跳跃 cursor | 幂等或明确拒绝；不误删、不倒退、不下溢 |
| AC-04 | send 错误、超时、发送 future 未完成即断开 | 不提交当前记录；重连仍从该消费位置读取 |
| AC-05 | send 成功后重连及相同请求重发 | 保持原有已消费前缀不重放语义；引擎启动一次 |
| AC-06 | Plugin 无消费时条数/字节达到真实预算 | 仍有界终止；只保留一个额外终止错误；错误可读取 |
| AC-07 | thinking/chat/tool/interaction/opaque/raw 混合 | 完整字段和顺序不变；thinking 仍计费；原有编码错误路径不变 |
| AC-08 | final/error/abort 与追加、回收交错 | 不丢正常终态；terminal 只在 cleanup 后发布；无死锁 |
| AC-09 | V2/V3、重连重新协商版本、encoder 返回 None | wire seq 规则和过滤语义不变，cursor/回收一致 |
| AC-10 | Gateway 同 ID 重放、多个读取者、broadcast lag | 完整历史和 metadata 不变，无 Plugin 回收 |
| AC-11 | terminal 投递完成、重复 release、tombstone 到期 | 正文释放不重启 run；grace 不被无限续期 |
| AC-12 | 合成脱敏现场形状与多 run 并发 | 相同事件构成可持续消费；一个 run 的回收不影响另一 run |
| AC-13 | Plugin/Gateway runtime 交付类型 | Plugin 不创建 Gateway forwarder；Gateway 启动前已订阅 |
| AC-14 | overflow 日志、内存和访问成本 | 能区分累计量与积压；无敏感数据；无新 DB 调用或 O(N²) 回收 |

长流测试必须明确模拟消费进度，不可在同步循环里一次无消费塞入 20,000 条后
要求成功；那会正确地触发真实积压保护。时间敏感场景使用可控发送器或测试时钟，
不要依赖真实网络何时断开来断言本地 send 成功/失败边界。

## 9. 交付、回滚与后续

### 9.1 首期交付

公共实现及文档属于本 BCS workspace。内部发行层只消费公共版本并验证发布产物，
不复制回收逻辑。保持业务/API 适配与引擎运行时所有权边界，不在旧引擎目录加入行为。

上线前完成验收矩阵、public bridge suites、直接依赖者的编译/测试及受影响的
内部发行验证。无新配置、无 DDL、无数据回填。

回滚恢复旧实现会恢复累计上限问题；已释放的内存事件无法原地重建。因此不做
活跃 run 的热切换，采用计划内 drain/停止后升级或回退，并明确重启可能中断活跃任务。
本方案不为进程重启提供新的事件恢复能力。

### 9.2 明确后续

- 有界批量投递：解决现有单 run 每 20 ms 一条的吞吐限制，独立验证公平性和心跳。
- delta 合并：需在正式分配 seq 前完成，具备明确逻辑文本段边界及最大等待时间。
- Gateway 长任务：条数上限同样提高至 16384，但仍是累计历史上限；完整重放下
  另评估磁盘暂存或协议化重同步，不能直接复用前缀回收。
- 更强可靠性：服务端 ACK/持久化游标与进程恢复需要独立协议设计。

## 10. 源码导航与验证状态

下列路径相对于 `apps/bcs/crates/tools/bcs-bridge/`：

- `core/src/run.rs`：`retained_event_size`、`push_frame`、`RunHandle`、`RunRegistry`、`forward_stream`。
- `core/src/plugin.rs`：`PendingRun`、`Route`、`dispatch`、`flush_events`、`run`。
- `core/src/runtime.rs`：`RuntimeReply`、`handle_chat_send`。
- `core/src/webhook.rs`：Gateway stream 建立与 wire 编码。
- `core/src/encoder.rs`：V2/V3 过滤、编码失败与 seq 约束。
- `core/tests/plugin_websocket.rs`、`core/tests/e2e_webhook.rs`、`core/tests/encoder_contract.rs`。

排查时在上述基准提交运行过 `cargo test -p bcs-bridge-core --lib run::tests::`，
17 passed / 0 failed；这只证明旧行为的测试基线。实现后的测试、资源观测及未完成
发布事项见上方 Validation，不以旧基线代替新实现的验证。
