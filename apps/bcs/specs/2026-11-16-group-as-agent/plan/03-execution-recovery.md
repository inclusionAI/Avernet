# C. 精确执行、完成与故障恢复（T09–T13）

执行入口：[plan.md](../plan.md)。这些任务必须测试真实 MessageFlow／Runtime 用例，fake 只放到 Bot／Provider 网络边界；不靠修改 Task 状态来模拟“执行已结束”。接口：[interfaces.md](interfaces.md)。

## T09：ManagerWorker activation、完成 ack 与退出分离

**依赖：** T08。**产物：** manager 实际执行关联、受信完成候选与整轮关联账本。

**Files:**
- Create: `apps/bcs/crates/services/bcs-message-flow/src/a2a_group.rs`、`apps/bcs/crates/services/bcs-message-flow/src/a2a_group/start.rs`、`apps/bcs/crates/services/bcs-message-flow/src/a2a_group/completion.rs`、`apps/bcs/crates/services/bcs-message-flow/src/a2a_group/associations.rs`。
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/lib.rs`、`apps/bcs/crates/services/bcs-message-flow/src/task_flow.rs`、`apps/bcs/crates/services/bcs-message-flow/src/task_flow/support.rs`。
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/message_flow.rs`。
- Create: `apps/bcs/crates/services/bcs-message-flow/tests/a2a_group_completion.rs`、`apps/bcs/crates/services/bcs-message-flow/tests/support/a2a_group.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/src/sql/associations.rs`。

**Interfaces:** 在现有 MessageFlowService 新增 transport-neutral `start_group_a2a(claim)->CallResult<ExecutionObservation>`、`observe_group_a2a(claim)`、`stop_group_a2a(claim)`；签名与 GroupExecutionPort 相同，T12 完成 stop 语义。CompletionCandidate 来自可信 execution_id→Session/activation/manager 关联，不从模型自报字段独立构造。

- [ ] **1. 写 RED：ack 不等于退出。** 专属 fixture `manager_world()` 使用真实 MessageFlow、SQLite Session/Task/association store；`complete_tool(&TaskKey,summary)` 走真实工具完成入口；`finish_manager(&TaskKey)` 只向 fake Bot 边界发送对应 run terminal：

```rust
let w = manager_world().await;
let task = w.start_call("m1").await;
let ack = w.complete_tool(&task.key, "最终建议").await.unwrap();
assert!(ack.accepted);
assert_eq!(w.task(&task.key).await.state, TaskState::Working);
assert!(w.context_occupied(&task.context_id).await);
w.finish_manager(&task.key).await;
assert!(matches!(w.observe(&task.key).await, ExecutionObservation::Complete(_, _)));
```

其他新增 fixture 方法：start_call 包装真实 send+drain，task 调真实 get，context_occupied 读取 repo，observe 调真实 MessageFlow observe。ack 用新增 `CompletionAck {accepted:bool}`，不伪造 TaskCompleted。
- [ ] **2. RED。** `cargo test -p bcs-message-flow --test a2a_group_completion`，Expected 现有 complete_if_running 提前结束或关联缺失。
- [ ] **3. 注入执行上下文。** 将 initial/current/previousTask envelope 作为一份受控输入进入 manager，data 独立 JSON 块；不传 token。分配 execution_id 后才开始根投递；Worker 派发前记录同 Task/generation 的 work_id，超 256 个未终结关联拒绝并失败收尾。
- [ ] **4. 改完成拦截点。** A2A 托管 Session 走 `RecordIntent::Complete`，检查 manager、activation、pending Worker／回送，再闭合新派发并 ack；不得调用 legacy latest_running_service_session 或立即完成 Session。非 A2A 分支逐字保留原合同。

```text
trusted parent/run association → manager + generation check
  → pending ledger check → CAS Completing + frozen summary
  → ack tool → await manager terminal observation
  → CompletionCandidate (not yet public COMPLETED)
```

- [ ] **5. 追加失败与空闲测试。** 旧 activation、模型伪造 ID、Worker pending、回送 unknown、重复相同 summary、冲突 summary；完成期间新派发拒绝；fake clock 验证 60 秒一次提醒＋累计 120 秒真实空闲宽限，活跃 Worker 无消息不超时。提醒也是关联工作，不重置绝对 deadline。
- [ ] **6. GREEN。** `cargo test -p bcs-message-flow --test a2a_group_completion && cargo test -p bcs-message-flow`；永久 BaaS result-without-name fixture 保持原样、不为关联新增必填 name。
- [ ] **7. 提交。** `feat(bcs): fence manager completion to the active A2A invocation`。

## T10：StateMachine 新轮输入、幂等 Run 与明确输出

**依赖：** T08；与 T09 的 Service API 修改顺序整合。**产物：** 新 Task 创建新 Run，重试同 execution_id 不再创建 Run，成功／失败候选不提前完成 Session。

**Files:**
- Create: `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/a2a.rs`。
- Modify: `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/start.rs`、`apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/completion.rs`、`apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/rerun.rs`、`apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/service.rs`。
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/collaboration_runtime.rs`。
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/collaboration.rs`。
- Create: `apps/bcs/crates/services/bcs-collaboration-store/src/a2a_run.rs`。
- Create: `apps/bcs/crates/services/bcs-collaboration-runtime/tests/a2a_rounds.rs`、`apps/bcs/crates/services/bcs-collaboration-runtime/tests/fixtures/a2a_group.yaml`。

**Interfaces:** Runtime 新 `start_group_a2a/observe_group_a2a/stop_group_a2a`，对应 GroupExecutionPort；existing Run.create 必须接受已保留的 execution_id/idempotency binding。输出按 `OutputSelector {key,node_id,format}` 解析，有序生成 Artifact。

- [ ] **1. 写 RED 输入／Run 唯一性。** `state_machine_world()` 使用真实 runtime 和两节点纯 BotTask YAML，fake Bot 回显收到的 Run.input；新增 `captured_input(execution_id)->Value`、`run_count(execution_id)->usize` 从真实调用／DB读取：

```rust
let w = state_machine_world().await;
let first = w.start_call_with_data("m1", json!({"budget": 800000})).await;
let claim = w.claim(&first.key).await;
w.runtime_start(claim.clone()).await.unwrap();
w.runtime_start(claim.clone()).await.unwrap();
assert_eq!(w.run_count(&claim.execution_id).await, 1);
assert_eq!(w.captured_input(&claim.execution_id)["data"]["budget"], 800000);
```

`claim` 从真实已提交意图读取；runtime_start 调新增 runtime application 方法，不能私自创建 Run。
- [ ] **2. RED。** `cargo test -p bcs-collaboration-runtime --test a2a_rounds`，检查 failure 不是缺 YAML／配置，而是原实现不满足幂等／输入。
- [ ] **3. 实现输入与冻结。** 按 spec `input.a2a.initialMessage/currentMessage/previousTask`、text/data 生成本轮值；固定 definition 及 bindings，不运行时按可变别名重新解析。用同库唯一 execution_id→Run 保留绑定，重复 start 查询已有，start 不可知返回 Unknown。
- [ ] **4. 输出收集和完成分支。** 按配置 node_id 查本轮节点输出，校验所有允许成功分支都有结果，按 key/position 排序；不是遍历最后一个。A2A 分支回传 CompletionCandidate，Session 完成交给 T11；legacy runtime 完成不变。

```rust
for (position, selector) in snapshot.outputs.iter().enumerate() {
    let result = results.get(&selector.node_id).ok_or(CallError::OutputInvalid)?;
    artifacts.push(project_selected_output(selector, result, position as u32)?);
}
```

`project_selected_output(&OutputSelector,&Value,u32)->CallResult<OutputArtifact>` 在新 a2a.rs 定义，Text 必须 string、Data 保持 Value，不把全部内部结果放进 Artifact。
- [ ] **5. 加第二轮及拒绝测试。** 先用 T11 前的受控终态 fixture 完成第一轮，再受理第二轮：不同 Run、相同 context、initial 不变、current budget 改为500000、previous 指第一轮；FAILED previous 不当成功。静态 HumanInput 开启被拒，动态意外等待按 FailIntent；原生 rerun 对托管 Run 拒绝；不伪造 Human Caller。
- [ ] **6. GREEN。** `cargo test -p bcs-collaboration-runtime --test a2a_rounds && cargo test -p bcs-collaboration-runtime --test runtime_progression && cargo test -p bcs-collaboration-store`。
- [ ] **7. 提交。** `feat(bcs): bind state machine A2A rounds to immutable execution inputs`。

## T11：公开投影与原子终态／Artifact

**依赖：** T07、T09–10。**产物：** 真实执行候选到可查询成功结果的单事务提交；重复 source event 与晚回调安全。

**Files:**
- Create: `apps/bcs/crates/services/bcs-group-a2a/src/completion.rs`、`apps/bcs/crates/services/bcs-group-a2a/src/projection.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/src/sql/terminal.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/src/sql/events.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/tests/terminal.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/tests/projection.rs`。
- Modify: `apps/bcs/crates/services/bcs-session-store/src/a2a_transaction.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/tests/completion.rs`。
- Modify: `apps/bcs/crates/services/bcs-event-store/src/lib.rs`（只 export 所需 transaction fragment）。
- Create: `apps/bcs/crates/services/bcs-event-store/src/group_a2a.rs`。

**Interfaces:** Implements Core.complete、Repo.intent/commit_terminal/project/snapshot/events；Consumes CompletionCandidate、WorkEvidence；Produces 同一 TaskView 与稳定 public sequence，不发送未提交 Artifact。

- [ ] **1. RED：失败事务不露部分成功。** `w.candidate(task)` 用真实已停止观察生成 summary candidate；`w.complete(task)` 调 Core.complete：

```rust
let w = world().await;
let task = w.running_stopped_call("m1").await;
w.fail_at(FaultPoint::BeforeTerminalCommit);
assert!(w.complete(&task.key, "summary").await.is_err());
let view = w.repo.get(TaskQuery { key: task.key.clone(), history_length: None }).await.unwrap();
assert_ne!(view.state, TaskState::Completed);
assert!(view.artifacts.is_empty());
assert!(w.context_occupied(&task.context_id).await);
```

running_stopped_call 通过 RecordingExecution 的真实关联记录返回 terminal observation，不直接设置 Task.Completed。
- [ ] **2. RED。** `cargo test -p bcs-group-a2a-store --test terminal --test projection`；增加事务中前 N 步逐步 fault，确认 Session 状态也不提前 Completed。
- [ ] **3. 收敛完成边界。** 关闭派发，保存 per-stream 高水位，投影到边界；source cursor 与 public event 同事务。阶段消息只固定模板，includeProgress=false 不写可选进度；内部工具／成员／凭据字段拒绝公开。已确认结果来自 allowlisted selector，不靠正则假装内容绝对安全。
- [ ] **4. 组合终态提交。** lock Task revision/generation＋context＋Session activation；插入完整 artifacts、Artifact public events、terminal event，Task terminal、Session complete、清占用及容量同事务。rowcount 错误整个 rollback；DB outage 不发布 success。

```text
check completing + generation + stopped evidence + projection boundary
→ insert artifacts in configured order
→ append artifact events then terminal event
→ CAS Task terminal + Session activation complete
→ context idle + release execution capacity → commit → notify readers
```

- [ ] **5. 补竞争／大小测试。** 相同候选重复提交稳定 ID、旧代次拒绝、late source 无新公开事件；16 个 Artifact／256KiB 边界，超限 OutputInvalid；terminal event 不受 history budget 挤占；JSON 数组输出类型保持。完成事务后响应丢失重读同样结果。
- [ ] **6. GREEN。** `cargo test -p bcs-group-a2a-store --test terminal --test projection && cargo test -p bcs-app-group-a2a --test completion`；再运行 T09/T10 的完整两轮真实完成测试，移除只能用于早期阶段的手工 terminal fixture。
- [ ] **7. 提交。** `feat(bcs): commit A2A artifacts public events and terminal state atomically`。

## T12：整轮取消、停止证明与隔离

**依赖：** T09–11。**产物：** CancelTask 可接受但不提前宣称停止，涵盖 manager／Worker／节点／回送／重试。

**Files:**
- Create: `apps/bcs/crates/services/bcs-group-a2a/src/cancel.rs`。
- Create: `apps/bcs/crates/services/bcs-message-flow/src/a2a_group/stop.rs`。
- Modify: `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/a2a.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/src/sql/intents.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/tests/cancellation.rs`。
- Create: `apps/bcs/crates/services/bcs-collaboration-runtime/tests/a2a_stop.rs`。

**Interfaces:** Implements Core.cancel、GroupExecutionPort.stop、Repo.record_observation；Stopped/Unknown 不能互换。增加 `TestWorld::is_isolated(context_id)->bool`、`capacity_used()->u32` 从真实 DB 读取。

- [ ] **1. RED：未知停止不能放开。**

```rust
let w = world().await;
let task = w.running_call("m1").await;
w.set_stop_unknown(true);
w.app.cancel(task.key.clone()).await.unwrap();
w.drain().await.unwrap();
assert_eq!(w.task(&task.key).await.state, TaskState::Working);
w.advance(60_001);
w.drain().await.unwrap();
assert_eq!(w.task(&task.key).await.state, TaskState::Failed);
assert!(w.is_isolated(&task.context_id).await);
assert_eq!(w.capacity_used().await, 1);
```

- [ ] **2. RED。** `cargo test -p bcs-app-group-a2a --test cancellation && cargo test -p bcs-collaboration-runtime --test a2a_stop`。
- [ ] **3. 意图先落库再 stop。** CAS phase→Cancelling，固定一次 settle deadline、禁止新 dispatch；调用 stop 必须精确 execution/work IDs。队列项若尚未越 send barrier，可原子 NotStarted；运行中依据实际终态确认；scope abort 可能覆盖旁人时不发，保持 Unknown。
- [ ] **4. 覆盖全部关联。** manager、Worker、return leg、SM node、scheduled retry 均登记并确认；Run.Aborted 仅是内部停止请求记录，不作为 evidence。停止已证实 CommitCanceled，超时 CommitFailure(stopped=false)＋isolated；failed/canceled 不插部分 Artifact。
- [ ] **5. 竞争和幂等测试。** complete intent 与 cancel 用 barrier 竞争最多一个成功；completing cancel→NotCancelable；重复 pending cancel 不重置 deadline；terminal cancel 拒绝；late callback 不改终态。取消一个 Task 不影响其他 Session 的同 Bot 工作。
- [ ] **6. GREEN。** 重跑第 2 步；`cargo test -p bcs-message-flow --test a2a_group_completion && cargo test -p bcs-group-a2a-store --test terminal`。查询停止状态的 fake 与真实生产 Provider/WS wire 适配分别测，不能仅测 fake AlwaysStopped。
- [ ] **7. 提交。** `feat(bcs): cancel complete A2A rounds with confirmed stop isolation`。

## T13：重启恢复、owner fencing 与受控解封

**依赖：** T08、T11–12。**产物：** 新进程恢复已保存意图和预算、无盲目重新派发；管理员可核实但不能 force unlock。

**Files:**
- Create: `apps/bcs/crates/services/bcs-group-a2a/src/recovery.rs`。
- Modify: `apps/bcs/crates/services/bcs-group-a2a-store/src/sql/recovery.rs`。
- Modify: `apps/bcs/crates/application/bcs-app-group-a2a/src/worker.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/src/admin.rs`、`apps/bcs/crates/application/bcs-app-group-a2a/tests/recovery.rs`。
- Create: `apps/bcs/crates/tools/bcs-admin/src/a2a_recovery.rs`、`apps/bcs/crates/tools/bcs-admin/tests/a2a_recovery.rs`。
- Modify: `apps/bcs/crates/tools/bcs-admin/src/main.rs`。

**Interfaces:** Implements Core.reconcile、Repo.claim_due/release_isolation、GroupA2aAdminService；Consumes GroupExecutionPort.observe 与 persisted evidence；CLI `a2a execution inspect|reconcile|release` 的 release 必须引用服务端可验证 evidence_ids，不提供 `--force`。

- [ ] **1. 写重启 RED。** restart 真正关闭旧组件并用原 SQLite 文件创建新连接，不复制内存 map：

```rust
let w = world().await;
w.fail_at(FaultPoint::AfterDispatchBeforeAck);
let task = w.app.send(request("app-a", "m1")).await.unwrap().task;
let _ = w.drain().await;
let restarted = w.restart().await;
restarted.drain().await.unwrap();
assert_eq!(restarted.total_recorded_dispatches(&task.key).await, 1);
assert!(restarted.task_is_tracked_or_isolated(&task.key).await);
```

total_recorded_dispatches 由共享 fake provider 的接收账本计数，跨应用重启保留，不能依据本进程 start_count；task_is_tracked_or_isolated 检查 Repo 状态与 execution associations，无布尔常量兜底。
- [ ] **2. RED。** `cargo test -p bcs-app-group-a2a --test recovery`，逐一覆盖接口清单 FaultPoint。
- [ ] **3. 实现恢复判定。** NotStarted 且从持久化证据能证明未越 barrier 才 start；Running 继续 observe；Complete/Failed 收尾；Unknown 达到预算转隔离。绝对 deadline 不重算 now+timeout。新 owner 的 claim revision/token fencing 所有写；远端不支持 fencing 时不宣称可以安全重发。
- [ ] **4. 有界扫描和退避。** 每批≤100、轻量 key pagination；每次失败记录类别、next_check_at、attempt，指数退避建议 1s→60s、10 次转人工核实。轮次不变且有业务进展才重置连续失败计数，不延长 Task 期限。无任务扫描有退避，不占连接等待网络。
- [ ] **5. 解封测试。** 未覆盖所有 work evidence 拒绝；证据齐全 atomically Session 精确完成＋context idle＋容量释放，原 Task 保持 FAILED。旧 callback/owner 重放不解封。管理员 CLI 审计操作者和 evidence ID，不打印 Task 原始正文／凭据。
- [ ] **6. GREEN。** `cargo test -p bcs-app-group-a2a --test recovery --test cancellation && cargo test -p bcs-admin --test a2a_recovery`。增加 query-count 断言每轮只 claim 一批、持续故障不 busy loop；旧 service_spec scanners 不被恢复。
- [ ] **7. 提交。** `feat(bcs): recover fenced A2A executions and safely release isolation`。
