# StateMachine 异常终态结果

## 问题与范围

Chat 中的协作运行失败后，主持人末节点可能永远不会执行。平台提供唯一、私有的异常结果并记录公开状态事件；主持人根据服务端事实和游戏阶段决定恢复。成功路径、HumanInput 契约、工作流 YAML 和调度器不变。非 Chat 保留既有会话收尾与 IM 路径。

## 原子边界

失败、明确取消和启动事实缺失收敛共用终态命令。单个存储事务执行 run 状态 CAS、终结时间及内部诊断、未完成派发作废、结果 checkpoint、可投递范围内的 canonical 消息和唯一 Send、公开事件。节点完成 ID 的投影由 CAS 核对；冲突时重新投影，最多尝试三次。任一步写入失败整个事务回滚；CAS 输者读取已存在终态。启动缺失保留原始事实检查与准备宽限。内存实现同样暂存并共同提交。

系统消息的 message ID 和 client_msg_id 均为 `run-result:{run_id}`。只生成目标为原始 `created_by` 的一条 Send；消息 audience 定向该 Bot。投递由现有调度器在事务提交后执行。首次准备与发送前分别检查 Chat 会话、群及目标成员；发送前还检查现有 outbound policy。排队满时仍保存 `rejected_capacity`，不保证激活。这里的唯一性只指业务记录，不保证网络传送、模型执行或外部副作用恰好一次。

相对普通 TaskResult，复用消息序号、容量和 Send 队列，但目标是 run 创建者，无需失败 worker 仍存在。内部范围失效导致的清理中止不投递，事件 `scope_invalid=true`、原因 null。明确的用户取消为 `user_aborted`，不自动恢复。

## 消息与事件

消息固定头 `[State Machine Run Result]`，后接 JSON：`schema_version: 1`、`run_id`、`workflow_name`、`status`、`failed_node_id`、`attempt`、`reason_category`、`completed_node_ids`。节点 attempt 是平台原始编号；没有对应节点时为 null。名称来自原始快照，缺失时为 `definition_id@version`。完成节点仅包含稳定排序的 ID。字段不包含 input、instruction、artifact、投票、玩家词、堆栈或原始错误。节点失败原因与失败事实一起持久化：`node_failed`、`human_timeout`；启动及发布路径分别使用 `startup_failed`、`publication_failed`。

公开 `state_machine.run.failed` / `state_machine.run.aborted` 事件包含同一安全摘要、完成时间、持续时间和范围失效标识；事件不包含私有消息正文或产物。事件 schema 和 catalog 是契约。Bot 使用受认证保护的 `/state-machine-runs/{run_id}/result` 查询最小事实：仅原创建 Bot、仍属有效会话时可访问。响应不返回 input、节点产物和诊断，仅提供受限的 32 位十六进制 client reference 用于确认失联提交。

## 主持人

RUN_RESULT 独立于节点任务、回灌和 worker 消息。主持人先执行 `status --failed-run`，脚本核实服务端终态、会话、创建者和当前阶段；完成发言后等待投票时直接开投，旧轮次或旧阶段结果不重试。按 session、游戏轮次、regular/PK 和 speak/vote 独立持久化重试次数，最多两次；重复通知幂等，第三次停止。人工兜底调用相同验证与限额。

提交前持久化不确定状态和随机 client reference；响应丢失后只有服务端事实匹配该引用才能确认，否则停止。节点写入必须匹配轮次、阶段及尝试，旧结果不能念稿或覆盖。一次激活提交必须是最后一个工具调用。

## 数据访问

异常终态按 run 读取完成节点 ID，不读取产物；SQL CAS 附带完成节点集合一致性条件。投递侧沿用既有每 Bot 容量计数、会话序号、待绑定上下文查询和消息/事件写入；新事务增加 run CAS、派发作废、结果 checkpoint、可选启动 checkpoint 及事件。无数据库事务跨网络。成本随该 run 节点数量和原有待绑定上下文数量变化；尚未完成大规模负载证明。

## WebSocket 断连的快速失败检测

已投递或正在投递的 Bot 节点，其 WebSocket 连接断开时，适配器在释放旧连接占用之前通知运行时。运行时按 Bot 分页查找该断连时刻前开始、仍在运行且尚无结果的节点，核对不可变派发快照中的 WebSocket 目标、Bot、run、node、attempt、请求和启动时间，再以条件更新保存失败事实，进入原有异常终态事务。未发送的派发、HTTP Provider、已有产物、其他 Bot、旧 attempt 和新连接之后启动的工作不由此路径失败。领导权切换导致的服务端关闭不作为 Worker 故障。

连接断开无法区分进程退出与暂时性网络故障；对符合条件的在途任务，这一策略选择立即失败并允许主持人恢复，即便原进程稍后重新连接。迟到回调受到原有 attempt 和终态约束，但不能保证外部副作用恰好一次。数据库失败会传播并记录，既有恢复扫描继续提供兜底；该路径没有实现额外的持久断连事件队列。

数据库开销：每次断连至少执行一次按 `(env, assignee_bot_id, status)` 索引筛选的查询，按 `(run_id, node_id)` 游标每页最多 64 条；每个候选额外读取派发，符合条件者再读取 run、执行节点失败事务和已有终态事务。实际工作量与该 Bot 的并发在途节点数成正比，写事务不跨网络 I/O；对极高并发断连未做负载压测。处理期间旧连接占用不释放，以防新连接与旧断连混淆；长时间数据库故障可能延迟重连。
