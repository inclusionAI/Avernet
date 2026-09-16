# Task 消息接入 BCS 队列

- 日期：2026-09-16
- 状态：本地实现，待评审和预发验收
- 所属模块：BCS / bcs-message-flow
- 范围：ManagerWorker 主从群的任务消息；不包含 StateMachine 和 Direct A2A 迁移
- 架构依据：[架构约束](../../../../docs/arch/arch.rules.md)、[消息流边界](../../crates/services/bcs-message-flow/CONTEXT.md)、[Service API 边界](../../crates/service-api/bcs-service-api/CONTEXT.md)。本变更延续现有消息队列架构，无新增系统级 ADR。

## 1. 问题与边界

开启 Group/System 队列后，Worker 的初始化上下文可能已经保存为 pending_context。
旧 task.dispatch 直接调用下行接口，不经过队列，因此无法绑定这些上下文；Worker
实际收到任务正文，却没有收到群角色和协作规则。只放开 Task 配置校验不能解决这个问题。

本次将以下三条下行接入同一套 Send 调度：

| 入口 | 来源 | 目标 | 持久化 task leg |
| --- | --- | --- | --- |
| TaskDispatch | Manager 指派任务 | Worker | dispatch |
| TaskResult | Worker final/error/aborted | 原 Manager | result |
| TaskMessage | Worker 主动进度/协作消息 | Manager | message |

任务消息复用 Bot/session FIFO、Bot 并发限制、发送间隔、队列容量、上下文绑定与截断、
取消、Unknown 和关闭后排空。不新建 Task 专用调度器或任务数据库表。

## 2. 开关与兼容性

业务策略仍存放在 bcs_message_delivery_policy，通过现有 Human 管理接口进行版本 CAS 更新。
新增 ready 的是既有 `flow_enabled.task` 字段，默认 false；Bot 仍需 mode=enforce。

- group/system 继续要求同时开关；task 独立开关。
- 要验证“所有主从群消息不依赖 native inject”，应同时开启 group、system、task，
  并将 Manager 和 Worker 的 Bot 策略设为 enforce。
- task=false 且目标 lane 没有旧队列 Send、没有待消费上下文时，保持旧直发链路。
- task=false 但同 Bot/session 有未结束 Send 时，返回 queue_draining，不能越过旧消息。
- 只有 pending_context 时，下一次 Task Send 可作为排空载体进入队列。这个兼容例外
  只消费对应 lane 的上下文，不把它转换成 native inject。
- 已入队任务在关闭后继续运行；其 Worker 结果继续走队列，即使 Manager 的新准入已关闭。
- 旧直发任务运行中开启 task：Worker 返回时可将结果单独入队，不重发 Worker 任务。
- 不再支持通过旧 TOML 业务配置打开 Task；部署应使用 DB 策略接口。

开启后的任务必须提供明确、存在且 Running 的 canonical bcs_session_id，并属于指定群。
不自动新建或猜测“最新会话”。StateMachine task 入口保持原路径，不被本开关接管。

回滚时先关闭 Task 并排空所有 Task delivery，再降级代码；旧版本不能解释新增的 task
semantic projection，不应在仍有 Task 积压时直接回退二进制。

## 3. 消息、任务与运行身份

- bcs_messages 是正文和附件的事实源，入队前写入 content.text、content.attachments。
- 每条目标消息对应一条 bcs_message_deliveries，flow_kind=task、kind=send。
- task_id 是逻辑任务身份；Worker run_id、Manager result run_id 各自独立。
- 每次网络尝试的 request_id 继续由公共队列生成，与 task_id/run_id 区分。
- transport 的 ACK/Provider alias 沿用现有 run 身份关联机制。

delivery.semantic_projection_json 复用现有 version=1 的群快照，新增 task 对象：

| 字段 | 含义 |
| --- | --- |
| leg | dispatch / result / message |
| task_id | 派发和结果关联的逻辑任务 ID；主动消息使用独立消息关联 ID |
| manager | 原任务 Manager Bot ID |
| worker | Worker Bot ID |
| worker_name | 发送者展示名称快照 |
| response_mode | full / after_last_tool_call |

不在 delivery 中复制正文、附件 URL 或凭证。不增加数据库列或迁移。
消息继续使用 ManagerWorker 可见性分类，任务派发对 Worker 定向可见，Worker 结果
保持既有 FullOnly 分类；不扩大 Human 消息可见范围。

## 4. 准入与发送

1. 原入口先校验当前群、Session、Manager/Worker 角色和任务拦截规则。
2. 命中 Task 策略或合法上下文排空规则后，原子保存 canonical message 和 Send delivery。
3. 返回 status=queued、空 bot_deliveries；这表示持久接收，不表示 Bot 已收到。
4. TaskStore 记录 Queued 投影，不在此启动旧的五分钟 TaskStore TTL。
5. 公共 Worker 选中任务时，从 canonical message 读取正文/附件，绑定对应 Bot/session
   中更早的 Inject，包括 System GroupContext，并按现有限制生成发送内容。
6. 发送前再次检查群/Session 状态、参与者角色、静音、Bot 可用性和下行拦截规则。
7. send-start 提交后注册 run/task 关联，实际使用 chat.send。保留 TaskDispatch、TaskResult、
   TaskMessage 的 delivery_kind，并携带 task_id；被指派的目标明确标记为 mentioned。

排队 TTL 和执行 deadline 分离：前者从入队开始，后者在实际 send-start 时由公共运行时设置。
同一 Worker/session 必须等前一次运行进入可信终态才启动下一次任务。
Manager 派发工具应接收 queued 后结束当前轮，不能同步占据 Manager lane 等待自己的结果
Send；结果会在 Manager lane 可用后独立启动下一轮。

## 5. Worker 终态与结果事务

只有可信 chat/chat.event 的 final、error、aborted 才能结算任务；agent/tool 的终止标记
不能冒充整个任务终态。

Worker 终态事务同时完成：

- 通过 delivery state_version CAS 结算 Worker Send，消费已发送上下文、释放 Worker lane；
- 保存内部 run_reply 全文和必要的可见 chat 片段；
- 给 Manager 准入独立 TaskResult Send，并分配新的 Session 顺序号。

Manager 队列满时提交 rejected_capacity 结果 delivery，Worker 仍然完成；结果正文保留，
不因为回传拥塞重新执行 Worker。重复/并发 final 通过事件锁、CAS 和稳定结果消息键避免重复。
DB 写失败传播并使用既有 storage-only retry，不重放网络发送。

群关闭或 Manager 被移除不阻止已运行 Worker 的终态保存；Manager 结果真正发送前重新授权，
无权限则失败，不唤醒已关闭会话。TaskResult 与 Worker 是否成功分开表达：error/abort 的结果
带失败/取消标记，不能仅把部分回复当成成功结果。

### 完整正文与任务响应窗口

- run_reply.content.text：按现有运行级归一化规则重建的完整文本，不包含工具输出正文。
- run_reply.content.task_result_text：按 response_mode 选择的实际 Manager 回传文本。
- run_reply.content.task_state：completed / failed / cancelled。
- full 使用完整回复；after_last_tool_call 只使用最后一次工具调用后的文本。
- 最后一次工具调用后没有文本时，回传 [no response]，不回退为工具前分析文本。
- 原有全文/当前段快照/增量 final 的启发式归一化限制不变，不承诺任意引擎歧义均可无损还原。
- public history 继续过滤内部 run_reply，不会展示两份全文；直接按 message_id 读取仍可取得全文。

## 6. 重启、取消与完成检查

TaskStore 是可重建的内存投影，不是第二套持久任务状态机。
发送、回调和 task.complete 检查根据 durable delivery 恢复任务关联；更新按 state_version
防止旧通知覆盖新终态。after_last_tool_call 的恢复按 Session 分页读取相同 Bot/run 的 chat/tool
记录，重建响应窗口。尚未持久化的内存 delta 仍不具备崩溃恢复保证。

- queued 取消/过期：直接结束 Send，释放或重绑上下文，不调用 Bot abort。
- 已发送取消：复用 cancelling/cancel_unknown，可信 aborted 回调完成终态及结果事务。
- unknown/cancel_unknown：仍占用原 Bot/session lane，不能通过 TaskStore TTL 假装结束。
- 人工恢复复用现有接口；不会凭空伪造 Worker 输出。
- 未发送即失败/取消/过期：以 delivery 状态和 ledger 表达，不生成虚假的 Bot 回复。
- task.complete 先恢复本 Session 的持久任务，排队任务也算 pending；排队或不确定的结果/
  主动消息也会阻止提前完成。正在处理该结果的 Manager 可以在自身运行中完成 Session，
  不要求先等待自己的 final，否则会形成循环等待。
- group-wide complete 另外检查该群 Running Sessions，防止重启后空内存 ledger 提前关群。

Provider 精确 abort 的已有能力限制不变；本次不以 scope-wide abort 冒充安全的单 run abort。

## 7. 事件与通知保证

task.assigned/task.completed 沿用现有事件投影；排队 assigned 标记 status=queued。
这些事件在队列事务之后写入，写入错误返回失败，不静默返回成功；但进程在两个提交之间崩溃
可能缺失事件投影。本次不增加 outbox、不承诺事件或 IM 提示恰好一次。

真正决定任务是否完成的是 delivery 终态和 canonical result。状态通知继续复用公共队列
能力；Workbench 专用 task 排队 UI 不在本次范围。调用方收到不明确错误应先查询状态，
不能盲目重派任务；任务受理不提供跨新 task_id 的业务去重。

## 8. 验收与传播范围

变更涉及配置契约、MessageFlow Service API 的 queued 语义及实现；WS/HTTP/CLI 继续透传
status 字符串，无新增公共接口。DB 插件/表结构无需变更。独立监控日志自动包含 flow_kind=task。

测试入口：

- `conformance_queued_task`：Memory 和真实迁移 SQLite 的派发→发送→final→Manager 结果、
  双向初始化上下文、重复 final、附件、历史去重、重启恢复、响应窗口、关闭排空、容量拒绝、
  取消/过期、错误回传、关闭群授权、旧任务切换、Task 关闭时的上下文载体。
- `bcs-config-api`：Task 可单独打开、Bot override、调度监督要求、其他未 ready 类型仍拒绝。
- `bcs-message-flow` 全量测试：旧群聊、task、A2A、队列状态机/调度/abort/恢复兼容性。
- `bcs-message-store` 全量测试：Memory/SQLite 准入、终态回复事务、CAS、上下文、容量与 SQL 查询。

预发还需实测：同 Worker FIFO、多 Worker fan-in 到同 Manager、Provider 字段和角色标签、
Manager 忙时结果排队、主节点切换、取消和 Unknown 人工处置。OceanBase 和真实 Provider
未由本地测试替代。本轮未部署；提交和 PR 的验证状态以对应 PR 记录为准。

### 本地验证记录

- `cargo test -p bcs-message-flow -p bcs-config-api -p bcs-message-store -p bcs-service-api -p bcs-test-support`：773 项通过，1 个既有 doctest 忽略。
- 新增 Task 专项用例 18 个；同一测试入口还运行了复用的 25 个 SQLite migration 测试，合计 43 项通过。
- `cargo test -p bcs-provider-http --test provider_transport_contract task_kinds`：2 项通过，覆盖 Task 三种下行的 SSE 与 JSON callback fallback。
- `cargo test -p bcs-ws --test frame_compat task`：6 项通过；11 项原有 coordination echo 测试按仓库配置忽略，相关消息流测试由 bcs-message-flow 执行。
- `cargo check -p bcs --all-targets`：通过。
- 修改和新增文件的空白检查通过；所有修改/新增 Rust 源文件均低于 1,000 行。为满足此约束，将原有超长 bot_event/task_flow 文件按职责拆分，未做全局格式化。
- `scripts/ci/arch-check.sh` 未通过：DEP 检查脚本变量展开异常、既有 import/trait 命名及 contract harness 登记问题、全仓 conformance discovery 失败。Task 新测试已接入共享 contract harness；本次未修改门禁，也不声明全仓架构检查通过。

上述 Cargo 命令均在 `src/bcs` workspace 执行；真实 OceanBase、预发和完整 Singlebox E2E 尚未执行。
