# D. 结果查询、保留与 Card 发布（T14–T16）

执行入口：[plan.md](../plan.md)；共享接口：[interfaces.md](interfaces.md)。这三项可用已完成的 Repo＋受控执行观察单独验证，不以 HTTP handler 测试替代其持久化合同。

## T14：Caller 隔离、一致快照和有界列表

**依赖：** T07、T11。**产物：** GetTask/ListTasks/SubscriptionStart/events，按 Caller 过滤且无 N+1。

**Files:**
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/src/sql/queries.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/src/queries.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/tests/queries.rs`、`apps/bcs/crates/services/bcs-group-a2a-store/tests/query_budget.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/tests/queries.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a/src/cursor.rs`。

**Interfaces:** Implements GroupA2aService.get/list/subscription/events 及 Repo 查询；CursorPayload={caller/group/deployment/filter_hash,last_status_at_ms,last_task_id}，`encode_cursor(payload,key)->String`、`decode_cursor(token,key,query)->CallResult<CursorPayload>`，key 由 bootstrap Secret 注入，不读 env。

- [ ] **1. RED 私有结果。** fixture 在同群创建 app-a/app-b 的已完成 Task，TaskQuery 的 key.scope.caller 替换为另一个主体：

```rust
let w = world().await;
let a = w.completed_call("app-a", "a1").await;
w.completed_call("app-b", "b1").await;
let mut stolen = a.key.clone();
stolen.scope.caller = caller("app-b");
assert!(matches!(w.app.get(TaskQuery { key: stolen, history_length: None }).await,
    Err(CallError::TaskNotFound)));
let page = w.app.list(list_query("app-a", 50)).await.unwrap();
assert_eq!(page.total_size, 1);
assert_eq!(page.tasks.len(), 1);
```

新增 `completed_call` 使用真实 terminal transaction；`list_query(caller,limit)` 填接口清单默认 filters、include_artifacts=false、history_length=Some(0)。
- [ ] **2. RED。** `cargo test -p bcs-group-a2a-store --test queries --test query_budget && cargo test -p bcs-app-group-a2a --test queries`。
- [ ] **3. 实现一致读取。** get 的 Task、Artifacts、bounded history 在同读快照；list 先 Caller+Group+filters SQL 查询轻量 key＋size，按 `(status_timestamp DESC, task_id DESC)`，同快照 COUNT 精确 total，按≤8MiB前缀后批量加载。未到页预算但读取失败返回错误，不装作空页。

```sql
SELECT task_id, status_timestamp_ms, artifact_bytes, history_bytes
FROM bcs_group_a2a_tasks
WHERE deployment=? AND scope_hash=? AND result_deleted_at_ms IS NULL
  AND caller_issuer=? AND caller_subject=? AND group_id=?

ORDER BY status_timestamp_ms DESC, task_id DESC
LIMIT ?;
```

再按筛选生成 context/status/timestamp>= 条件；不能把可见性留给内存过滤。history 使用窗口分区 limit 或等价 batch plan，禁止每个 Task 各查询一次。
- [ ] **4. 游标与高水位。** 对游标做 HMAC-SHA256 完整性、绑定 query scope/filter；认证始终重新执行。snapshot 一次读 Task＋public highwater，subscribe 若已终态拒绝；拿到快照后只读 `sequence>highwater`。并发状态更新跨页不保证全局快照，按 spec 不增加长持有事务。
- [ ] **5. 失败／成本测试。** cursor tamper／跨 Caller／改变筛选、historyLength 0/omit/100、timestamp 等于边界、includeArtifacts 默认 false、100×256KiB 结果只取预算内前缀、totalSize不变。SQL recorder 证明 get 至多3个查询，list 基本页+count+batch产物+batch历史，不随 Task 数线性新增查询。
- [ ] **6. GREEN。** 重跑第 2 步；真实MySQL在T20使用“少量活结果＋至少100万历史墓碑”的合成fixture，EXPLAIN/EXPLAIN ANALYZE（版本支持时）证明scope_hash和result_deleted_at_ms前缀的范围访问；记录page与COUNT rows examined、p95、返回数。若扫描墓碑则该任务不能通过，修索引或拆活结果索引表，而非只声明有索引。
- [ ] **7. 提交。** `feat(bcs): query private A2A tasks with bounded consistent pagination`。

## T15：结果逻辑删除、墓碑和 context 背景过期

**依赖：** T11、T13–14。**产物：** 安全清理任务，可重启重试，不使相同 ID 再执行。

**Files:**
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/src/sql/retention.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/src/retention.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/tests/retention.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/tests/retention.rs`。

**Interfaces:** Implements Repo.expire/purge；`GroupA2aRetention::run_batch()->CallResult<usize>`，clock／limits 注入；不添加读取续期。最小 reservation 原文键／fingerprint 不随结果 TTL 清理。

- [ ] **1. RED 墓碑。**

```rust
let w = world().await;
let task = w.completed_call("app-a", "m1").await;
w.advance(7 * 24 * 60 * 60 * 1000 + 1);
w.fail_at(FaultPoint::AfterLogicalExpiry);
let _ = w.retention_batch().await;
assert!(matches!(w.app.get(TaskQuery { key: task.key, history_length: None }).await,
    Err(CallError::TaskNotFound)));
assert!(matches!(w.app.send(request("app-a", "m1")).await,
    Err(CallError::ResultExpired)));
assert_eq!(w.start_count(), 1);
```

`retention_batch` 调真实 expiry/purge；completed_call 内调用 drain 保证 start_count=1，不由 helper 写固定计数。
- [ ] **2. RED。** `cargo test -p bcs-group-a2a-store --test retention && cargo test -p bcs-app-group-a2a --test retention`。
- [ ] **3. 实现可见性切换。** 同事务标记 Task.result_deleted_at＋reservation.result_expired，保留恢复依赖和去重记录。Get/list/events 都排除逻辑删除；physical purge 按 row_limit 删除大字段／events/artifacts，失败重试，不回滚逻辑删除。

```text
due candidates ≤100 → lock Task + context dependencies
→ active/isolated/background-in-use? skip
→ mark task result_deleted + reservation expired → commit
→ bounded physical purge → retain minimal ownership + ID tombstone
```

- [ ] **4. 续聊竞争与清理范围。** next round admit 与 expire 串行：受理先取得前轮快照则 pin/copy 背景，expiry 不破坏该轮；expire 先提交则 ContextHistoryExpired。context 到期清除 initial input／execution snapshot 大字段并保留来源／Caller，不能历史结果删了就创建普通 Session 来继续。
- [ ] **5. RED/GREEN 故障。** logical commit 前故障仍可读取、之后故障不可读取；重启接着 purge；隔离和未终态不清理；读不续期；Group 删除不删除 ID 墓碑；累计预留增长指标可见。对 archived completion candidate、Session.input/output 的重复大内容也统一清理，避免只删 Artifact 但正文永久留在其他表。
- [ ] **6. GREEN。** 重跑第 2 步，`cargo test -p bcs-app-group-a2a --test recovery --test admission`；query recorder 验证每批最多100 key、逐 Task 分片有上限、无全表全行加载。
- [ ] **7. 提交。** `feat(bcs): expire A2A results without reopening deduplication identities`。

## T16：Card 同源投影、持久化同步意图与旧目录核对

**依赖：** T04、T06–08。**产物：** 开关+生命周期状态即资格真源；Fuse旧LOCAL Group Card明确替换为HTTP+JSON，只有一个Group writer；公开最小Card可被真实Fetcher读取，Bot catalog行为不变。

**Files:**
- Create: `apps/bcs/crates/services/bcs-group-a2a/src/card.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/src/publication.rs`。
- Create: `apps/bcs/crates/services/bcs-group-a2a-store/src/sql/publication.rs`。
- Modify: `apps/bcs/crates/services/bcs-group-store/src/card_snapshot.rs`、`apps/bcs/crates/services/bcs-group-store/src/card_template.rs`。
- Modify: `apps/bcs/crates/services/bcs-fusion/src/core/card_sync.rs`。
- Create: `apps/bcs/crates/tools/bcs-admin/src/a2a_catalog.rs`。
- Modify: `apps/bcs/crates/tools/bcs-admin/src/main.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/tests/publication.rs`。
- Modify: `apps/bcs/docs/bcsfuse-catalog.md`。

**Interfaces:** Produces GroupA2aCardService／GroupA2aPublicationRepo；Consumes credential-free CardProjection、现有 Fuse 写端口。新增 `GroupA2aCatalogPort::put(group_id,card)/delete(group_id)` 作为 application outbound port，Bootstrap 包装现有 Fuse client；Core 不导入 HTTP client。

- [ ] **1. RED：关闭后重启恢复 DELETE。** `catalog_world()` 注入真实 repo／application＋记录型 HTTP stub；`publish_once` 执行一批 worker，`last_operation` 读取 stub 实际请求，`clear_memory_queue` 清内存而不删数据库：

```rust
let w = catalog_world().await;
w.set_support_a2a(true).await.unwrap();
w.publish_once().await.unwrap();
assert_eq!(w.last_operation().await, "PUT");
w.set_support_a2a(false).await.unwrap();
w.clear_memory_queue();
let restarted = w.restart().await;
restarted.publish_once().await.unwrap();
assert_eq!(restarted.last_operation().await, "DELETE");
```

- [ ] **2. RED。** `cargo test -p bcs-app-group-a2a --test publication`；补 HTTP Card 与 catalog 投影字段相等（catalog 仅加 groupId/labels，不改变 endpoint/security）。
- [ ] **3. 实现统一 projection。** 只从persisted validated Group snapshot生成独立可调用投影；public+enabled+record_status.active+status∈{Active,Completed}+supported。旧模板local://、LOCAL、required discovery-card/v1不复制；不以修改旧模板来混合两套协议。false→None→DELETE；读取错误→重试，而非“缺群”→DELETE。展示字段显式允许后映射，安全声明来自部署profile；HTTP最小Card公开读application/json，Fuse用同一HTTP+JSON投影。原生群列表/本地discovery不变；原Fuse未开启Group撤下是有意兼容变化，依赖LOCAL的消费者未迁移则阻止切换。
- [ ] **4. 唯一writer接管及持久化claim／ack。** 旧FuseCardSyncLifecycle::publish中Resource::Group不再直接PUT/DELETE；旧CardChangeQueue的Group只唤醒持久化worker，Bot/Agent分支保留。启动切换先停旧Group领取/重试，drain在途并确认旧实例退出，再启用新writer和目录核对；不会简单merge新worker与旧writer共存。没有远端revision fence时超时旧请求保持uncertain、不确认撤销，结算后最终DELETE核对。 mutation 同事务 upsert publication desired revision；worker 按 group serial、读取最新状态后调用 network；ack 条件必须匹配 revision/lease，旧PUT不能覆盖新revision的本地成功标记；对远端无fence的任意迟到网络写不承诺即时不可见，只承诺本地拒绝且结算后核对收敛。timeout 保存 uncertain 后重读／重试，不宣称远端强一致。

```sql
UPDATE bcs_group_a2a_publications
SET confirmed_revision=?, lease_until_ms=NULL
WHERE deployment=? AND group_id=? AND desired_revision=? AND lease_token=?;
```

- [ ] **5. 旧目录一次性核对。** CLI `a2a catalog reconcile --batch-size 100` 保存高水位／进度，读取 legacy Group 与已知发布墓碑，安排当前 PUT/DELETE，不只扫描 enabled 群（否则无法撤销默认 false 的旧 Card）。不增加周期全库扫描，不修改 Bot publication flow。缺接收端清单能力时对所有本地 Group key 发幂等 DELETE，已删除本地 Group 需使用已有目录资源清单／迁移导出作为显式输入，记录覆盖边界，不能假定已全部撤销。
- [ ] **6. GREEN。** `cargo test -p bcs-app-group-a2a --test publication && cargo test -p bcs-group-store --test group_card_snapshots --test group_card_templates && cargo test -p bcs-fusion`。HTTP stub 测 500／timeout／ack false／旧请求迟到；migration命令中断后游标恢复；竞争用例模拟旧PUT晚于DELETE落地，确认uncertain不成功、最终DELETE重试后消失、旧worker不能新领取。不运行真实远端注册。
- [ ] 本任务验证已生成的调用投影能被真实Fetcher校验（本地public JSON stub，不伪称已具备T17的生产router）；真实Group router与HttpA2aCardFetcher的端到端测试在T17交付 `bootstrap/bcs/tests/integration_group_a2a_card_fetch.rs`，不制造T16→T17→T16依赖环。
- [ ] **7. 提交。** `feat(bcs): publish only enabled A2A groups and recover catalog withdrawals`。
