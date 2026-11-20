# B. 群开放与原子受理（T06–T08）

执行入口：[plan.md](../plan.md)；签名与 fixture：[interfaces.md](interfaces.md)。这三项最终必须以真实 SQLite 证明原子性，不能用 MockGroup+MockSession 的成功返回拼成“事务”。

## T06：`supportA2A`、持续资格和固定执行配置

**依赖：** T01–02。**产物：** 管理面可读写开关且始终保持资格不变量；尚不挂载调用入口。

**Files:**
- Modify: `apps/bcs/crates/contracts/bcs-domain/src/group.rs`。
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/core/group.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/group.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/types/mod.rs`。
- Create: `apps/bcs/crates/services/bcs-group/src/core/a2a_eligibility.rs`。
- Modify: `apps/bcs/crates/services/bcs-group/src/core/group_core.rs`。
- Create: `apps/bcs/crates/application/v1/bcs-app-group/src/a2a.rs`。
- Modify: `apps/bcs/crates/application/v1/bcs-app-group/src/service.rs`、`apps/bcs/crates/application/v1/bcs-app-group/src/create.rs`、`apps/bcs/crates/application/v1/bcs-app-group/src/projections.rs`、`apps/bcs/crates/application/v1/bcs-app-group/src/lib.rs`。
- Create: `apps/bcs/crates/services/bcs-group-store/src/a2a_mutation.rs`。
- Modify: `apps/bcs/crates/services/bcs-group-store/src/store_mapping.rs`、`apps/bcs/crates/services/bcs-group-store/src/store_writes.rs`、`apps/bcs/crates/services/bcs-group-store/src/store_eventful.rs`、`apps/bcs/crates/services/bcs-group-store/src/memory_mutation.rs`、`apps/bcs/crates/services/bcs-group-store/src/card_snapshot.rs`、`apps/bcs/crates/services/bcs-group-store/src/lib.rs`。
- Modify: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`。
- Modify: `apps/bcs/crates/adapters/http/bcs-http/src/routes/groups/dto.rs`、`apps/bcs/crates/adapters/http/bcs-http/src/routes/groups/translation.rs`、`apps/bcs/crates/adapters/http/bcs-http/src/routes/groups/projections.rs`。
- Create: `apps/bcs/crates/services/bcs-group/tests/a2a_eligibility.rs`、`apps/bcs/crates/services/bcs-group/tests/a2a_mutation_races.rs`。
- Create: `apps/bcs/crates/application/v1/bcs-app-group/tests/a2a_settings.rs`。

- Modify: `apps/bcs/crates/contracts/bcs-domain/src/collaboration.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/application/collaboration_runtime.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/collaboration.rs`。
- Modify: `apps/bcs/crates/services/bcs-collaboration-store/src/sql_binding.rs`、`apps/bcs/crates/services/bcs-collaboration-store/src/memory_binding.rs`、`apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/service.rs`。
- Modify: `apps/bcs/api-contracts/v1/openapi/groups.yaml`、`apps/bcs/api-contracts/v1/domain-models.yaml`。
- Create: `apps/bcs/crates/services/bcs-collaboration-store/tests/a2a_output_binding.rs`。

**Interfaces:** Consumes `validate_eligibility`、Group 的 support_a2a／a2a_revision；Produces `GroupCardSnapshot` 新属性及 group runtime binding 的 a2aOutput（OutputSelector 列表），使用既有 Group mutation 管理授权。新 `A2aGroupMutationGuard` 是共享 core 校验，不是 HTTP-only filter。

- [ ] **1. 写纯 RED。** 新测试 `human_actor_blocks_enable_even_when_legacy_kind_is_empty`：

```rust
let mut group = eligible_group();
let mut human = Participant::human("human-fixture", ParticipantRole::Observer);
human.mode = Some(ParticipantMode::Absent);
group.participants.push(human);
assert_eq!(validate_eligibility(&group, &manager_snapshot(&group)),
    Err(EligibilityError::HumanMember));
```

`manager_snapshot(&Group)->ExecutionSnapshot` 在 shared fixture 定义：拷贝当前 participants，strategy=ManagerWorker，definition=None，outputs=[]（manager summary 为内置输出），hash 由规范化配置算；不通过 kind 判定人类。
- [ ] **2. RED。** `cargo test -p bcs-group --test a2a_eligibility`；加 `a2a_settings` 测试 supportA2A JSON、false 默认、PATCH 缺失和 null 区分。不要用 Option<bool> 的默认反序列化把 null 误当缺失，使用现有 patch presence 模式或专属字段解析器。
- [ ] **3. 实现纯资格与版本变更。** normal/public/active、支持 strategy、actor_kind 全 Bot；manager 角色有效；StateMachine 编译后检查 HumanInput 和结果选择；输出类型只 text/json。Group.status只允许Active/Completed，Closed/Error/Inactive拒绝；生命周期转到不允许状态必须增加a2a_revision并写DELETE意图，不因guard而阻止关闭群。现有 Provider same_owner 约束仍先验证，不能为了 public 开启跳过。group revision 在成功改变资格／Card 配置时 checked_add。
- [ ] **4. 先闭合输出配置，再覆盖真实写路径。** GroupRuntimeBinding增加a2a_output和a2a_binding_revision，Sql/Memory读写a2a_output_json；现有create/detail/PATCH和ConfigureGroupRuntimeCommand同步加入a2aOutput（省略保持、null拒绝、[]清空仅关闭时允许），本任务同交付OpenAPI。StateMachine定义、node_id/format、成功分支覆盖先编译校验，Group + binding + validated snapshot + revision + publication意图由同一store-owned组合事务提交，不再分开GroupCore.mutate与Runtime.configure两次commit。已有configure/upsert/delete入口同样经过guard。测试必须走生产管理入口写入→Store读回→开启→真实snapshot.outputs，不能只fixture构造。 `GroupCore::mutate/upsert/add_participant/update_visibility` 都调用共享 guard；SQL 在锁住 group revision 后读成员／binding 并保存，Memory 用一把共享 mutation lock。持久化 validated execution snapshot 包含 a2aOutput、无Human证明对应的group/binding revision；所有相关写都会使其更新或失效，不能永久缓存一个eligible=true。WS／invite 必须通过这些受保护用例；对照调用点逐个加回归。context/opening_message/routing_policy（规范化默认值）纳入执行快照和config_hash，Group.context不是纯展示描述；label-only不改执行hash。Card snapshot同时带Group.status。提前编译流程后持久化 config_hash，事务校验 binding revision 未变，不能锁内调用外部服务。

```text
load version → compile candidate → transaction lock Group
  → verify expected group+binding versions
  → validate effective visibility/support/members/config
  → persist Group + RuntimeBinding.a2a_output/revision + validated snapshot + publication intent
  → commit → invalidate caches → wake publisher
```

- [ ] **5. 写竞争 RED/GREEN。** 两个真实 DB 连接、Barrier 同时 enable 与 add Human；恰好一个成功，最终不出现 enabled+Human。private→public+enable 同事务可成功；enable 与定义删除竞争失败闭合；改context/opening/routing后旧context续聊返回ContextConfigurationChanged，改label可继续。Closed/Error/Inactive关闭调用与发布，Completed仍合格。无效 mutation 不增加版本、不写同步意图。新文件 `a2a_mutation_races.rs` 的 barrier 在读取后／事务前插入测试 hook，不靠 sleep。
- [ ] **6. GREEN。** `cargo test -p bcs-group --test a2a_eligibility --test a2a_mutation_races && cargo test -p bcs-app-group --test a2a_settings && cargo test -p bcs-group-store && cargo test -p bcs-collaboration-store --test a2a_output_binding`。更新 Group CONTEXT，record_status 和会话完成不能混用。
- [ ] **7. 提交。** 主题 `feat(bcs): gate group A2A publication behind validated supportA2A`。

## T07：组合 Store 的原子受理、去重和容量

**依赖：** T02、T06。**产物：** Memory／SQLite／MySQL 的 GroupInvocationRepo.admit，测试可证明全部写一起提交。首次创建 bcs-group-a2a-store 不是只有接口的空实现。

**Files:**
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/Cargo.toml`、`apps/bcs/crates/services/bcs-group-a2a-store/CONTEXT.md`、`apps/bcs/crates/services/bcs-group-a2a-store/src/lib.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/src/memory.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/src/schema.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/src/sql/mod.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/src/sql/admission.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/tests/admission.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/tests/admission_mysql.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/tests/support/mod.rs`。
- Create: `apps/bcs/crates/test-support/bcs-test-support/src/contract/repo/group_invocation.rs`。
- Modify: `apps/bcs/crates/test-support/bcs-test-support/src/contract/repo/mod.rs`。
- Create: `apps/bcs/crates/services/bcs-session-store/src/a2a_transaction.rs`。
- Modify: `apps/bcs/crates/services/bcs-session-store/src/lib.rs`、`apps/bcs/crates/services/bcs-session-store/src/mysql.rs`、`apps/bcs/crates/services/bcs-session-store/src/memory.rs`。
- Modify: `apps/bcs/Cargo.toml`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/src/memory/group.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/src/memory/session.rs`。

**Interfaces:** Implements GroupInvocationRepo.admit/get 基本快照；Consumes AdmitCall。Produces `SqlGroupInvocationRepo::new(db,StoreScope {deployment,env})` 与 Memory 组合实现；后续方法按所属任务增量声明与实现，同时扩展共享harness；不能留下Unsupported默认方法规避R25。

- [ ] **1. 写 RED 回滚与并发。** world 在此任务组装真实 Store＋迁移；`admit(request)` 测试 helper 构造 AdmitCall，使用确定 dummy fingerprint（T08 才验证完整算法）：

```rust
#[tokio::test]
async fn admission_failure_leaves_no_session_or_reservation() {
    let w = world().await;
    w.fail_at(FaultPoint::BeforeAdmissionCommit);
    assert!(w.admit(request("app-a", "m1")).await.is_err());
    assert_eq!(w.pending_records().await, (0, 0, 0));
    assert_eq!(w.session_count().await, 0);
}
```

新增 `TestWorld::session_count()->usize` 精确统计本测试 group 的真实 Session 行；故障 hook 位于事务 executor 最后一步之前。
- [ ] **2. RED。** `cargo test -p bcs-group-a2a-store --test admission admission_failure`。同键 16 个并发请求额外断言全部返回同一 Task/context，DB 仅一份 reservation；错误不得吞成 empty。
- [ ] **3. 实现组合事务。** repo持有已由bootstrap选定的DbPlugin与不可变StoreScope，**没有logical_db路由参数**；claim/expire/purge/publication强制固定deployment，Group/Session fragment固定env；把 Session 创建/reactivation 的 Store-owned fragment 注入组合计划。获取 group→capacity→context 锁顺序固定，查 reservation 已存在则只读旧 Task，忽略当前 supportA2A 是否关闭；仅新调用检查eligibility revision；按新请求accepted_output_modes与该事务中的执行快照实际能力求交集，保存selected_output_modes_json，无交集ContentTypeNotSupported且整个事务无预留/占用。重复原Task不因当前输出配置改变重算模式。

```sql
-- Illustrative CAS step: affected_rows must equal 1 before later writes.
UPDATE bcs_group_a2a_contexts
SET active_task_id=?, generation=generation+1, state='occupied'
WHERE deployment=? AND session_id=? AND generation=? AND state='idle'
  AND active_task_id IS NULL;
```

同事务包含 Session、Task、预留、容量、自身启动意图；任何唯一冲突回滚后重新按完整去重键读取并比较指纹，不能把所有 unique violation 都当重复。context busy 不留预留；容量超额 429；隔离不释放容量。before/after commit fault 分开表示。
- [ ] **4. 加字节／方言和关系测试。** messageId `A` 与 `a` 不同；不同 caller/group 不共享预留；group disable 与 admit 并发依据提交顺序；context belongs-to-group/source/Caller 验证；前轮清理竞争。完整键 digest 碰撞注入时返回冲突而不是复用他人 Task。
- [ ] **5. 统一 Memory 锁域。** Memory Group／Session／Invocation 必须共用 `MemoryA2aUnitOfWork` 的权威状态与锁；不能三个无关 Arc<Mutex>。它放在组合 Store，提供GroupRepoPort／SessionRepoPort的memory适配器，与InvocationRepo共享权威状态；通过composition注入，现有Group/Session Store不能反向依赖该具体Store。非A2A原Memory构造不变；执行中不得复制一份群快照长期当真。测试同套 conformance 跑 Memory 与 SQL。
- [ ] **6. GREEN。** `cargo test -p bcs-group-a2a-store --test admission && cargo test -p bcs-session-store`；真实 MySQL 同测试，断言新受理没有全量 Session list／N+1。存储 recorder 记录 statement 数与 rows，事务内部不是“一个 repo 调用=一个 query”。
- [ ] **7. 提交。** 主题 `feat(bcs): atomically admit scoped group A2A executions`。

## T08：应用受理、JCS 去重和持久化启动意图

**依赖：** T01、T07。**产物：** 可调用 GroupA2aService.send＋一批 worker，执行端是 RecordingExecution，不启动实际 Bot。

**Files:**
- Create: `apps/bcs/crates/services/bcs-group-a2a/Cargo.toml`、`apps/bcs/crates/services/bcs-group-a2a/CONTEXT.md`、`apps/bcs/crates/services/bcs-group-a2a/src/lib.rs`、`apps/bcs/crates/services/bcs-group-a2a/src/admission.rs`、`apps/bcs/crates/services/bcs-group-a2a/src/fingerprint.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/Cargo.toml`、`apps/bcs/crates/application/bcs-app-group-a2a/CONTEXT.md`、`apps/bcs/crates/application/bcs-app-group-a2a/src/lib.rs`、`apps/bcs/crates/application/bcs-app-group-a2a/src/send.rs`、`apps/bcs/crates/application/bcs-app-group-a2a/src/worker.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/tests/admission.rs`、`apps/bcs/crates/application/bcs-app-group-a2a/tests/worker.rs`、`apps/bcs/crates/application/bcs-app-group-a2a/tests/support/mod.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a/tests/fingerprint.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/src/sql/recovery.rs`（本任务实现 claim/start 标记；T13 扩展恢复）。
- Modify: `apps/bcs/Cargo.toml`。

**Interfaces:** Consumes GroupInvocationRepo、A2aClock、GroupExecutionPort；Produces GroupInvocationCoreService.admit、GroupA2aService.send、`GroupA2aWorker::run_batch()->CallResult<usize>`，每批≤100，不在 Core 发网络。

- [ ] **1. RED：重复 send 不再启动。** world 组装真实 repo/core/app、fake clock 与 RecordingExecution；drain 只执行一批：

```rust
let w = world().await;
let a = w.app.send(request("app-a", "m1")).await.unwrap();
let b = w.app.send(request("app-a", "m1")).await.unwrap();
assert_eq!(a.task.key, b.task.key);
w.drain().await.unwrap();
w.drain().await.unwrap();
assert_eq!(w.start_count(), 1);
w.set_support_a2a(false).await.unwrap();
assert!(w.app.send(request("app-a", "m1")).await.unwrap().duplicate);
assert!(matches!(w.app.send(request("app-a", "m2")).await,
    Err(CallError::GroupUnavailable)));
```

- [ ] **2. RED。** `cargo test -p bcs-app-group-a2a --test admission --test worker`；增加 JCS UTF-16 key 排序、数值序列化、数组保序测试，不接受普通 serde_json map 排序冒充 RFC8785。
- [ ] **3. 实现归一化／指纹。** strict parse 在 T17；此处 CallRequest 必须已验证。按 spec 补默认、扩展排序去重、JCS bytes→SHA256；不含 token/route，包含 configuration/metadata/historyLength。选 JCS 实现前查已有 lock 与官方源码，锁依赖并跑 RFC vectors；如自行封装，只封装确定性算法，不手写不完整浮点格式器。

```rust
let hash = fingerprint(&request)?;
let cmd = AdmitCall {
    request, fingerprint: hash, fingerprint_version: 1,
    now_ms: self.clock.now_ms(), limits: self.limits.clone(),
};
self.repo.admit(cmd).await
```

- [ ] **4. Worker投递前必须通过CAS barrier。** claim只领取租约，先Repo.begin_dispatch(BeginDispatch{fence,now_ms})成功才得到DispatchPermit；Core/Repo共享的negotiate_output_modes纯函数放domain/configuration.rs（不是Store依赖Core实现）；CAS同时校验generation、expected_revision、owner/lease_token/lease有效期、取消/完成意图、barrier未越，affected_rows=1。再port.start(permit)，执行入口的consume_dispatch与根work/queue原子登记；最终queue send-start才发网络。record_observation/record_work/renew均用当前fence并取回新revision；默认lease30s、10s续，续约失败停止新工作。barrier已越且ack未知只能observe/Unknown，不再次start。没有端口/存储不就绪，不能noop成功。

```rust
let permit = repo.begin_dispatch(BeginDispatch { fence, now_ms: clock.now_ms() }).await?;
let active_fence = permit.fence.clone();
let observed = execution.start(permit).await?;
repo.record_observation(RecordObservation {
    fence: active_fence, observation: observed,
    now_ms: clock.now_ms(), next_check_at_ms: clock.now_ms() + 1000,
}).await?;
```

本计划固定：begin_dispatch推进Task到Starting并返回最新revision；**consume_dispatch只更新intent/work/queue，不推进Task revision**，仍校验Task当前revision；lease续约只更新lease_until，不推进Task revision。record_observation/record_work若改变Task版本必须返回最新fence。取消或其他状态转换发生后旧fence写入拒绝，worker重新claim/observe而非重放start。
- [ ] **5. 补 RED/GREEN 边界。** 增加claim后取消→begin_dispatch失败且start_count=0；barrier后崩溃→Unknown；lease过期旧owner不能start/record/renew；取消与queue admission竞争不得产生未登记投递。 对acceptedOutputModes求交集的新请求无交集ContentTypeNotSupported、pending_records不变；合法集合写入Task且重试不重算。修改 returnImmediately／metadata 指纹冲突，缺省与显式默认等价，historyLength 缺省与0不等价；首轮没有 contextId，服务端生成 ID 不反写原 fingerprint。已存在 taskId 的新消息拒绝；请求 body 超限 T17 测，领域 Parts/data/media 校验在这里仍有强类型防线。
- [ ] **6. GREEN。** `cargo test -p bcs-group-a2a --test fingerprint && cargo test -p bcs-app-group-a2a --test admission --test worker && cargo test -p bcs-group-a2a-store`。验证 start_count 和真实 DB，不只核对返回 Task。
- [ ] **7. 提交。** 主题 `feat(bcs): deduplicate group calls and dispatch durable execution intents`。
