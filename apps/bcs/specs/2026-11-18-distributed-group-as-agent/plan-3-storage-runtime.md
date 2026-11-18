# Group Invocation Storage and Runtime Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Group 请求独占受理、可靠许可调度、结果事务、终态发布与停止隔离提供可验证的 provider 和 Worker 桥接。

**Architecture:** GroupInvocationRepo 拥有请求/轮次控制，SessionStore checkpoint 拥有业务决定；两者以明确身份和提交证明连接。许可+待调度义务原子保存，调度用 token 失效修复跨记录中断，最终结果与 Artifact 先归档再发布和释放。

**Tech Stack:** bcs-dist-store、现有分布式 ports、bcs-store-api、DbPlugin/SQLite 持久参考、Tokio 测试与故障 barrier。

---

前置 D01～D06。S01 可先独立开发，S04 在 S01 表结构完成后开发；S02 需要基础调度/Inbox provider 并补精确 Start 封口，S03/S05 需要真实 Worker，S06 需要前面状态齐备。基础依赖详细验收见 S00；不能默认原分布式计划已经执行。

## S00：基础分布式依赖交付清单（集成前必需）

基础项目实现细节继续由 [原分布式计划](../2026-11-15-distributed-contracts/tasks.md) 管理；以下是本期必须消费的确切交付物和命令。执行人将其实现 commit 和实际命令结果登记到本目录 validation.md，缺少任一项不勾选对应集成门槛。

| 依赖 | Files / 原任务 | 需要证明 |
| --- | --- | --- |
| Session/ownership/Inbox provider | `apps/bcs/crates/services/bcs-dist-store/src/{memory,reference}/`；原 S2/S3 | seal 拒绝旧 owner，resolve_commit 不重复业务，逐输入前缀确认不越过其他 session |
| 调度 provider | `apps/bcs/crates/services/bcs-dist-store/src/{scheduling,consumer}.rs`；原 S2/W2 | observe/settle owner+token 原子检查，有界公平 due scan；S02 继续补 reschedule |
| TaskFeed provider | `apps/bcs/crates/services/bcs-dist-store/src/task_feed/`；原 S2/S3 的 repo provider，供 W4 与新 G03 消费 | snapshot/event 原子覆盖、部分 append 恢复、Gap、旧任务归档后再裁剪；不另建旧 G3 Gateway |
| DispatchBoard provider | `apps/bcs/crates/services/bcs-dist-store/src/dispatch/`；原 S2/C1 | current holder、稳定控制、unknown 不作为 finish、可恢复 sched |
| Worker 引擎 | `apps/bcs/crates/services/bcs-dist-worker/src/{ownership,reader,processor,committer,effects}.rs`；原 W1/W2/W4～W7，消费新 R01～R03 替代本期 W3 | 先提交后效果、旧 batch 未完成不覆盖、接管有义务 session |
| Connector 和 A2A 适配 | `apps/bcs/crates/services/bcs-dist-connector/src/`、`apps/bcs/crates/external-clients/bcs-a2a-http/src/dist_client.rs`；原 C1～C6 | 预写可能发送、区分 NotSent/Rejected/Unknown、query/resubscribe、远端取消确认 |

基础角色的行为序列应是：

```text
Worker: claim -> seal/load -> replay required effects -> read committed prefix
        -> reduce -> guarded commit/resolve -> effects -> confirm prefix -> settle
Connector: claim did -> persist possible-send -> network -> persist remote evidence
           -> normalize -> admit critical input or append stream -> supervise
```

基线命令为 `cargo test -p bcs-dist-store`、`cargo test -p bcs-dist-worker`、`cargo test -p bcs-dist-connector`、`cargo test -p bcs-a2a-http`，必须含真实 provider 测试而非只跑 Script harness。公共持久参考可以使用 SQLite 实现 repo 的明确事务；不要求为此先实现另一套生产低层 KV/JSON 插件。生产宿主 provider 单独跑同一 contract suite。

## S01：GroupInvocation 内存模型及持久参考 provider

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-store/src/group_invocation/{mod,model,record,memory,sql,reservation,rounds,recovery,cancellation}.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-store/tests/{group_invocation_memory,group_invocation_sqlite,group_invocation_failures}.rs`、`tests/group_common/mod.rs`。
- Modify：`apps/bcs/crates/services/bcs-dist-store/src/lib.rs`、`Cargo.toml`；Create：`apps/bcs/crates/services/bcs-dist-store/CONTEXT.md`（基线不存在）。
- Create（基线预期编号）：`apps/bcs/migrations/mysql/048_distributed_group_call.sql`、`apps/bcs/migrations/sqlite/046_distributed_group_call.sql`，一次定义请求/轮次/准备拒绝证据、S02 Start 封口及副本位置目录、最终归档/Artifact/history、blob manifest/pin/GC 状态表；迁移编号以实施时最大版本为准。
- Modify：`apps/bcs/crates/services/bcs-db-migrations/src/lib.rs`；Create：`src/migrations/group_invocation.rs`、`src/migrations/group_invocation_tests.rs`（相对该 crate）。
- Modify：`apps/bcs/migrations/README.md`。

- [ ] **1. 实施前串行分配迁移版本，然后 parameterized conformance 同时装配 Memory 与 file-backed SQLite。** 当前 MySQL 最大 047、SQLite 最大 045；若新版本已被其他任务占用，使用实施时最大版本的下一个数字，并在记录列出真实文件。每方言同一次交付只创建一个迁移，历史 baseline/schema/body 不变。S01 的迁移同时创建 S04 使用的结果和 Artifact 表，避免 SQL provider 先于表结构实施。

`tests/group_common` 提供 `invocation_world(kind)`、reopen、clock、barrier、fault recorder；真实 DbPlugin 为 SqlitePlugin::new_file。同消息并发预留一个 winner，两个消息抢同 context 只有一轮，旧 generation release 失败。

```rust
#[tokio::test]
async fn activation_records_pending_work_atomically() {
    let world = group_common::sqlite_invocation_world().await;
    let round = world.reserve_and_claim("msg-1").await;
    world.activate_after_durable_inbox(&round).await.unwrap();
    let saved = world.reopen().await.read_request("msg-1").await;
    assert!(saved.accepted());
    assert!(saved.pending_start_dispatch());
}
```

- [ ] **1a. 覆盖 Inbox Quarantined/ContentConflict 和 activate 响应丢失。** `abandon_round(expected_generation)` 与 `activate_round` 只允许一个成功；Abandoned 记录携带 DropStartPending，context 可释放；若已 Accepted 则返回 AlreadyAccepted，不能删除或伪造终态。

- [ ] **1b. 增加 Blob lifecycle conformance。** manifest 在 put 前创建；put/归档响应丢失、archive 失败、pin 保留、引用移除、精确 generation delete 和 delete 响应丢失重试都跑 Memory + SQLite；不依赖对象存储枚举。


- [ ] **2. RED：** `cargo test -p bcs-dist-store --test group_invocation_memory --test group_invocation_sqlite --test group_invocation_failures`。
- [ ] **3. 实现同一状态转换规则的 memory 和 SQL 映射。** memory 用单个受限临界区模拟原子存储，不把 mutex 当跨进程保证；SQL 在 request/round 表唯一键、条件更新和事务内返回 winner。

```text
request table UNIQUE (tenant, principal, group, message_id)
current_round table UNIQUE (tenant, principal, group, sid)
final_archive table UNIQUE (tenant, principal, group, tid)
artifact table UNIQUE (task_id, artifact_id) and UNIQUE (task_id, position)
history table UNIQUE (task_id, history_id/position)
reserve -> existing basis winner or new Preparing
refresh_preparation -> CAS unclaimed preparation revision + idle context generation/background
claim -> CAS preparation revision + matching context basis + no active round + source check
activate -> CAS exact request/round + Inbox evidence;
            write Accepted and PendingStart obligation in ONE request mutation
reject_unclaimed -> atomically revoke exact preparation revision and unbind the request key
                     recovery can only finish noncritical rejection cleanup
                     later retry uses a non-reusable revision; never delete accepted/tombstone
```

Abandoned 状态和 DropStartPending 同次持久写；abandon/activate 只准一个成功，expected generation 不匹配返回 Lost。恢复扫描覆盖 Preparing/SlotHeld/InboxDurable、Accepted.pending、Abandoned.pending_seal/pending_drop 和拒绝清理项，按状态只暴露合法恢复动作。ClosedAbsent 经 confirm_start_absent 验证封口/不存在证明，无需 Worker 即退出主动索引；ClosedPresent 的 confirm_start_discarded 校验封口 drain 边界及 progress-only CommitReceipt，消费全部同身份副本后退出。两者都保留持久拒绝墓碑，不制造假输入证明。

prepared request 保存 raw semantics、basis/defaults/version、frozen ref、sid/tid/deadlines，以及 preparation revision 和 generation 绑定的背景。refresh_preparation 仅在未 claim 且当前空闲 generation/背景仍匹配时原子替换派生输入并推进 revision，不能更改客户端指纹、稳定 ID 或 deadline。claim/reject/refresh 对同 revision 互斥；拒绝证据在删除并重建同请求键后仍阻止旧写者，不能重用 revision 造成 ABA。pending/recovery 索引不得成为仅内存队列；SQLite 同事务更新索引字段，生产 provider 给出等价恢复协议。Confirmed Start 前，不因重调度成功清除 pending。

context 控制行保存 initial_goal/最近已释放成功轮引用；read_context_basis 读取同一行/cut，成功 release 与推进成功指针及清空 slot 同事务，失败/取消只清空 slot。不要从“已能 GetTask 读取终态、但尚未 release”的 archive 提前推断下一轮背景；背景引用的 pin 由 D06/S04 生命周期合同保护。

生产 GroupInvocation 实现只依赖 repo/plugin-api。若当前 bcs-store-api 无法原子满足 claim 涉及的多记录条件，不能逐 Key 普通写伪装一致；使用可证明的单文档/CAS 设计或事务 provider，并先提交布局/访问成本评审。新低层能力必须有接口与 conformance，不能私调 TBase。

- [ ] **4. GREEN：** 新增 `tests/conformance_group_invocation.rs` 调用 `group_invocation_repo_port_contract_tests`，故障注入覆盖写前失败、写后丢响应、并发 reservation winner basis、直接 Busy/Lost→Busy 共用拒绝、拒绝后扫描不执行和旧 revision 不影响重试。验证 A 旧背景准备、B 成功释放、A 条件刷新及 refresh/claim 响应丢失；同时覆盖 abandon/activate 两种顺序、ClosedAbsent 确认、ClosedPresent 错误边界拒绝、Quarantined/ContentConflict 和 reopen 后准备恢复。每种 scenario 显式断言 Task/Input 未重复；运行 `cargo test -p bcs-db-migrations`，完整 SQLite 迁移新建/升级通过；MySQL 全链验证在 S04 真实环境继续完成。提交 `feat(bcs): persist exclusive group invocation rounds`。

## S02：reschedule、Start 封口与公平恢复

本任务修改调度/Inbox provider 和恢复记录，不创建 Worker recovery 文件；Worker 的实际重查/接管消费全部放在 S03。无输入封口确认不依赖 Worker，也不要求存在 SessionSpec。

**Files**

- Create/Modify：`apps/bcs/crates/services/bcs-dist-store/src/scheduling.rs`、`src/group_invocation/recovery.rs`。
- Create/Modify：`apps/bcs/crates/services/bcs-dist-store/src/inbox/{producer,closure}.rs` 及 S00 memory/persistent-reference 的 Inbox 映射；实现 D03 seal_abandoned_start，不改非 Group 输入的语义。
- Create：`apps/bcs/crates/services/bcs-dist-store/tests/{group_reschedule,conformance_group_scheduling}.rs`，后者调用 `inbox_scheduling_port_contract_tests`。
- Create：`apps/bcs/crates/services/bcs-dist-store/tests/{group_start_closure,conformance_group_start_closure}.rs`，后者调用扩展后的 `inbox_producer_port_contract_tests`。


- [ ] **1. 写三个交错测试。** 旧 settle 先成功后 reschedule 重新登记；reschedule 先成功后旧 settle 必须 NeedsRecheck；due 已是立即执行时仍使旧 token 失效。禁止只比较 due 分数。

```text
case A: observe(token=7) -> settle(None) -> activate -> reschedule
        assert due contains sid
case B: observe(token=7) -> activate -> reschedule(version=8) -> settle(token=7,None)
        assert NeedsRecheck
case C: due=now; observe(token=7) -> reschedule(due=now) -> settle(token=7,None)
        assert NeedsRecheck
```

- [ ] **2. RED：** `cargo test -p bcs-dist-store --test group_reschedule`。
- [ ] **3. 在一个存储原子操作中校验 round、更新 token、提前 due。** 复用 D03 合同，不通过重新调用 admit(Duplicate) 间接实现。对 Accepted.pending 以 request identity 重做；通知丢失/进程崩溃不清义务。

- [ ] **3a. 实现精确 Start 的持久封口与副本位置目录。** 用 barrier 分别让 append 或 seal 先到线性化点。seal 先完成时旧 append 必须拒绝；append 先完成时 ClosedPresent 固定 drain 边界，涵盖全部同身份副本。ClosedAbsent 仅由已封口且确无输入的 provider 事实生成；查无 session 时只写独立封口证据，不创建 spec/task/checkpoint。持久参考实现将该身份的 append gate、位置证据和封口记录纳入同一事务/原子协议；其他 backend 需证明等价栅栏，不能用普通读后写或请求端预检查冒充。封口证据通过 repo/plugin API 保存，所需新增低层能力先回 D03 合同审查。

```text
seal -> ClosedAbsent -> confirm_start_absent -> remove active recovery item, retain tombstones
append copies -> seal -> ClosedPresent(drain_through) -> durable Drop scheduling
seal response lost -> resolve/retry original identity, never infer absence from timeout
old writer after seal -> typed StartClosed, no physical append and no activation
```

恢复扫描 page_size 默认 64（配置限制最大值），失败指数退避并设置上限，按稳定 keyset/游标推进；扫描不能每轮只看头部。遇到持续失败会话不反复立即 reschedule 使系统热循环。

- [ ] **4. GREEN：** 加 129 条 pending、前 64 条持续失败，后续条目仍处理；返回 response lost 后可重复 reschedule 并最终由 Start proof 关闭义务。运行 `cargo test -p bcs-dist-store --test group_reschedule --test group_start_closure --test conformance_group_start_closure`，覆盖无 spec/Start、两种 append/seal 顺序、多副本、封口/确认响应丢失及 reopen 后旧写者被拒。无输入完成项不再被恢复扫描/重调度。提交 `feat(bcs): fence abandoned starts and recover group scheduling`。

## S03：Worker 未许可 Start 屏障与消费证明确认

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-worker/src/group_call/{mod,start_gate,proofs,recovery}.rs`、`tests/{group_start_gate,group_start_recovery,group_start_discard}.rs`。
- Modify：`apps/bcs/crates/services/bcs-dist-worker/src/{reader,processor,committer,scheduler}.rs`。
- Modify：`apps/bcs/crates/services/bcs-dist-store/src/{consumer.rs,group_invocation/recovery.rs,inbox/listener.rs}`，用真实 listener/owner/quarantine 合同替换占位。

- [ ] **1. batch = 合法前缀 + 未许可 Start + 后续事件。** 断言前缀可提交，consumed 不越过 Start，broker ack/trim 不覆盖它，Runner 没收到 Start；读取许可错误与明确未许可同样保守但区分诊断。

```text
pending batch -> allow prefix only
unlicensed Start -> persist bounded next_check; never settle(None)
abandoned ClosedPresent Start + exact DiscardStartProof -> progress-only commit through sealed drain
  -> confirm_start_discarded(closure proof, commit proof)
abandoned ClosedAbsent -> recovery confirms absence without Runner/spec/checkpoint
wrong discard digest/generation -> reject/quarantine, never discard another input
takeover -> enumerate waiting session; reread start and license
accepted Start -> commit runner state + exact start identity + consumed prefix
commit receipt -> confirm_start_consumed(request, round, receipt)
confirmation response lost -> replay confirmation with same proof
```

- [ ] **2. RED：** `cargo test -p bcs-dist-worker --test group_start_gate --test group_start_recovery --test group_start_discard`。
- [ ] **3. 实现 D03 的 provider-owned prefix 边界，不对 opaque position 排序。** 等许可的义务持久可枚举，不只保留本机 reader buffer。许可读取采用有界退避；恢复器在 activate 保存后、reschedule 前崩溃，仍能扫描 Accepted.pending。未许可 Start 的丢弃仅由 Worker gate 处理，不调用 Runner；DiscardStartProof 与原 round/input/digest 匹配，progress-only 消费不创建 dispatch。ClosedPresent 的 Drop 确认还必须覆盖全部封口副本边界，不能消费第一份后提前清除。ClosedAbsent 在恢复器验证 Inbox 证明后关闭，不进入 Worker Start 路径；封口结果未知则退避。旧 abandon replay 不释放新 generation。Start-consumed proof 必须来自受保护 checkpoint，核实 scope/group/sid/tid/request/generation；随机字符串和无 Start 的 CommitReceipt 不接受，不与独立的 Start-absent proof 混用。

Listener 读取从统一 SessionStore/存储投影拿已封印 owner、listen epoch、独立 quarantine；读失败不误当“允许执行”。不能继续保留原占位 always-error 却宣布可靠 idle-exit 完成。

- [ ] **4. GREEN：** 覆盖旧 owner、旧 generation、错误 checkpoint proof、部分前缀、共享分区连续 ack、许可确认丢响应、等待中接管与 Trim 竞态；提交 `feat(bcs): fence unlicensed group starts at consumption`。

## S04：最终归档、Blob 生命周期与批量读取

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-store/src/group_results/{mod,memory,sql,parts,history,query}.rs`、`src/blob_lifecycle/{mod,memory,sql}.rs`。
- Create：`apps/bcs/crates/external-clients/bcs-dist-blob-local/{Cargo.toml,CONTEXT.md,src/lib.rs,src/file_store.rs}`、`tests/conformance_blob_store.rs`，实现 BlobStorePort 的 file-backed provider。
- Create：`apps/bcs/crates/services/bcs-dist-store/tests/{blob_lifecycle,conformance_blob_lifecycle,conformance_group_results}.rs`。
- Modify：`apps/bcs/Cargo.toml` 和 Cargo.lock，注册本地 Blob adapter；对象网络/文件 IO 不进入 store/Core。
- Modify：`apps/bcs/crates/services/bcs-dist-store/tests/group_common/mod.rs`，增加 sqlite_result_world 和 two_artifact_result 等结果 fixture。
- Read/consume：S01 迁移创建的结果及 blob manifest/pin 表；本任务实现 provider，不假设 S01 已交付其行为。
- Create：`apps/bcs/crates/services/bcs-dist-store/tests/{group_results,group_results_sqlite,group_results_mysql}.rs`。

- [ ] **1. 核对 S01 的表结构和完整迁移结果。** 本任务依赖 S01 schema/fixture；如果结果结构发生变化，先做合同兼容评审，并新增 additive migration，不修改 S01 已提交/被数据库记录的版本。
- [ ] **2. 写完整归档/冲突/事务失败测试。** 一个 Task 两个 Artifact（text 和 JSON array），顺序、ID、metadata 原样；每个 Part 另有不同的 filename/mediaType/公开 metadata，不能和 Artifact metadata 混合，归档/get/list 的 round-trip 保留。在第二 Artifact 插入失败时 archive 和所有产物均不可见。

```rust
#[tokio::test]
async fn failed_artifact_write_leaves_no_terminal() {
    let world = group_common::sqlite_result_world().await;
    let result = world.two_artifact_result();
    world.fail_second_artifact_insert();
    assert!(world.save(result).await.is_err());
    assert!(world.get_final().await.is_none());
    assert_eq!(world.artifact_count().await, 0);
}
```

- [ ] **2a. 增加 Blob conformance 的真实故障用例。** manifest 在 put 前写；put 后事务前崩溃可扫描发现，archive 失败保持 pin；exact generation/digest 重删幂等，引用与 GC CAS 竞争不误删，旧 put 在途未知不得仅靠 TTL 回收。Memory/SQLite 生命周期 repo 与 file-backed Blob 适配均实际执行。
- [ ] **3. RED：** `cargo test -p bcs-dist-store --test group_results --test group_results_sqlite --test blob_lifecycle`、`cargo test -p bcs-dist-blob-local --test conformance_blob_store`。
- [ ] **4. 实现 DbPlugin::transaction，不使用 execute_batch 冒充事务。** 最终 Task、Artifact 集合、history/边界、digest 与结果引用同事务。相同 source/content 返回相同引用，不同内容冲突。对象采用 prepare_manifest/pin → put_immutable → archive+reference 同事务；归档响应丢失查询原 digest/reference。GC 只能 CAS Deleting 且无 pending/live pin/ref，使用 delete_immutable 精确 generation；不能仅有 put/get 而把删除留给未定义脚本。checkpoint 内容先 durable pin，提交 Unknown 保留，确认未提交/压缩完成才凭证明释放。文件 provider 仅使用配置的仓库外根目录、不跟随越界路径；宿主 provider 实现相同端口。

| 表 | 唯一约束 | 说明 |
| --- | --- | --- |
| group_call_requests | (tenant_id, principal_id, group_id, message_id) | 原请求去重；不包含 sid |
| group_call_round_slots | (tenant_id, principal_id, group_id, sid) | 仅当前轮次占用，不是 archive |
| group_call_archives | (tenant_id, principal_id, group_id, tid) | 同 sid 多轮可以同时保留，sid 只有普通查询索引 |
| group_call_artifacts | (archive_id, artifact_id) 和 (archive_id, position) | archive_id 外键关联完整 scope/group/tid |
| group_call_history | (archive_id, message_id) 和 (archive_id, position) | 稳定身份和显示顺序分别唯一 |
| group_call_blob_manifests | (blob_id, generation) | digest/owner/状态和 pending pins 可恢复 |
| group_call_blob_references | (blob_id, generation, owner_kind, owner_id) | archive 引用同事务，checkpoint pin 依提交证明 |

同一个 sid 的两轮 `tid1/tid2` SQL 测试必须断言两个 archive 和各自产物都保留，不得将 slots 的 sid UNIQUE 复制到 archives。

按 `(scope,group,status,status_updated_at,tid)` 支持分页索引。D02 的 context/status/statusTimestampAfter 条件在分页前执行；排序使用状态更新时间及 tid 倒序，不能以归档/Artifact 更新时间替代。TaskReader 的专用 TaskListPage 返回页、next、有效 page_size 和 total_size；从同一逻辑索引 cut 对完整授权/筛选集合聚合计数，热任务及归档以完整 scoped Tid 去重，不能只数 SQL archive 或把当前页长度当总量。跨存储实现由 TaskReader provider 提供一致的逻辑索引/版本读取，不让 adapter 拼数，也不持有 DB 锁跨网络。historyLength=0 不返回历史，正数有界读取最近 N 条；不修改归档内容。includeArtifacts 默认/显式 false 时不访问 Artifact 表或产物对象，true 时按当前页 task IDs 批量加载，不在应用层逐任务调用。用 SQL/对象调用记录验证投影和聚合次数/扫描行数，并测空页/末页、hot/archive 重叠、同时间分页、时间边界及群过滤与时间筛选组合。MySQL identity 字段按仓库大小写敏感规则定义，避免 messageId 大小写碰撞；SQLite 用完全一致比较。

- [ ] **5. GREEN：** 全迁移链在 SQLite 文件新建/升级均通过；真实 MySQL 配置由现有测试基础读取，未配置时显式未验证，不把 skip 当 pass。运行 `cargo test -p bcs-dist-store --test conformance_blob_lifecycle --test conformance_group_results`、`cargo test -p bcs-dist-blob-local --test conformance_blob_store`、`cargo test -p bcs-db-migrations`、`cargo test -p bcs-dist-store --test group_results_mysql -- --ignored --nocapture`（创建测试时将真实环境用例标为 ignored，部署门槛必须实际执行）。提交 `feat(bcs): archive group artifacts with final task state`。

## S05：完成裁决、归档后发布与取消/隔离桥接

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-worker/src/group_call/{finalization,cancellation,publication}.rs`。
- Modify：`apps/bcs/crates/services/bcs-dist-worker/src/{processor,effects}.rs`、`apps/bcs/crates/services/bcs-dist-store/src/group_invocation/rounds.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-worker/tests/{group_finalization,group_cancellation}.rs`。

- [ ] **1. 用命名故障点测试 archive 保存后、第二 checkpoint 前崩溃；partial events；snapshot 后 release 前崩溃。** get 可读完整 archive，SSE 仍须排空旧 boundary，再产物/终态。在 before_release_round barrier 保持上一轮占用时，真实下一轮请求必须 Busy 且无新 Task/Input；恢复 release 并观察持久成功证明后，同一 messageId 重试才成功。不得让 wait_terminal 隐式等 release 来掩盖这两个不同边界。
- [ ] **2. RED：** `cargo test -p bcs-dist-worker --test group_finalization --test group_cancellation`。
- [ ] **3. 实现单一 checkpoint 裁决的阶段机，所有副作用来自 outbox。** release 不成为另一条独立 application“看见完成就删锁”路径。

```text
Closing -> SaveResult(source proof, fixed result)
AlreadySaved + matching digest -> retain same ref
confirmed archive -> guarded checkpoint(Publishing, ref)
AppendFinalArtifacts -> AppendTerminal -> PublishCoveringSnapshot
ReleaseRound(exact round, confirmed archive/public coverage/stopped evidence)
```

cancel intent 保存后停止新派发，并追踪所有现存 did/重试/Judge 义务。unknown 不能传给 DispatchQueue.finish；收尾超时先发布 FAILED 并持久隔离，待停止确认后只解除占用/隔离，不改 FAILED。毒会话 quarantine 独立保存，两类原因不能互相清空。

- [ ] **4. GREEN：** Final→Cancel、Cancel→Final 只有一套决定；旧 release 不影响下一轮；OB 不可用不提前发布成功；已连接客户端慢/断开不阻止共享发布完成。提交 `feat(bcs): finalize group tasks through durable publication stages`。

## S06：清理、墓碑、恢复索引与访问成本

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-store/src/group_results/retention.rs`、`src/group_invocation/maintenance.rs`、`src/blob_lifecycle/gc.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-store/tests/{group_retention,group_access_cost}.rs`。
- Modify：`apps/bcs/crates/services/bcs-dist-worker/src/group_call/recovery.rs`。

- [ ] **1. 测试结果实际删除后原 messageId 仍不能重执行；保存旧 basis 的墓碑仍能判定内容冲突。** 未终态/准备未决/隔离中的记录不按 7 天直接删除；本期不删除分布式 Session/spec/checkpoint，只清理已归档公开结果及其无引用 Blob。对象回收失败可重试。
- [ ] **2. RED：** `cargo test -p bcs-dist-store --test group_retention --test group_access_cost`。
- [ ] **3. 实现有界 keyset 维护和批量确认。** retention 根据实际删除提交记录 expired 状态；先到期不等于已清理。archive/content 对象引用有无引用证明后回收，不按单个客户端 cursor 修剪流。

```text
select bounded expired terminal set, exclude unresolved obligations
transaction: remove public result/artifact/history; retain request tombstone+basis
claim_unreferenced(manifest version, no live/pending ref) -> Deleting
delete_immutable(exact generation/digest) -> confirm Deleted; failure retries same generation
keep unresolved manifest/pin records discoverable; do not depend on object-store LIST
maintain accepted/pending index until Start-consumption proof confirmed
retire abandoned active work on ClosedAbsent proof or ClosedPresent + full drain proof
retain closed-start/rejected-preparation tombstones outside active scans
```

固定测试规模：内部维护分页 1/64/128、HTTP 列表页 1/50/100、至少 129 条恢复记录、至少 1000 条历史任务。分别测已完成无输入 abandon 只保留墓碑、不再反复 reschedule，及未解析封口继续退避。page 查询次数不随不可见任务数线性增长，total_size 聚合单独记录扫描行数而不读取全部正文，当前页 Artifact 批量加载；token 路径 OB writes=0。记录真实 SQL/插件调用数、最大读取行数和失败退避次数，不以 repo 方法计数代替 SQL。

- [ ] **4. GREEN 与提交：** 断言后页不饥饿、失败不会无限重复失效 token、停止未知不删证据，提交 `feat(bcs): bound group recovery retention and storage work`。

## S07：Checkpoint 逐轮压缩与迟到事件 fencing

**Files**

- Modify：`apps/bcs/crates/services/bcs-dist-worker/src/{committer,effects,group_call/recovery}.rs`、`apps/bcs/crates/services/bcs-dist-store/src/{group_invocation/rounds,group_results/retention}.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-worker/tests/group_checkpoint_compaction.rs`、`apps/bcs/crates/services/bcs-dist-store/tests/group_checkpoint_compaction.rs`。

- [ ] **1. RED：** 同一 Sid 生成 500 轮（每轮包含 dispatch/retry/effect），断言没有压缩时 checkpoint/加载成本增长，随后实现目标测试。
- [ ] **2. 实现 release 后逐轮压缩。** 保留 initial goal/成功背景引用、active round、closed_round_generation、fencing/effect tombstones；删除已关闭 task/view_drafts/dispatch_seq/deadlines/outbox。迟到输入/effect 先查 round/generation 目录，拒绝旧轮次且不创建新 dispatch；未知证明进入 quarantine。
- [ ] **3. GREEN：** checkpoint 大小、读取和提交工作在 500 轮后不随历史线性增长（固定每轮节点上限，检查 1/100/500 轮的字节/行数/SQL 计数）；checkpoint 仅保留常数大小 closed generation，不把 500 份墓碑留在内存；压缩与接管、旧 release、SaveResult 回放交错时不解锁新轮次；提交 `feat(bcs): compact closed group rounds with late-event fencing`。

本子计划退出条件：真实 memory+SQLite conformance、持久重启、Worker 桥接的受理/完成故障测试通过。生产 TBase/OB provider 的多进程验收独立保留，不由 reference provider 结果代替。
