# E. HTTP／SSE、装配与交付验收（T17–T20）

执行入口：[plan.md](../plan.md)；类型及调用方向：[interfaces.md](interfaces.md)。直到 T19 全依赖具备，生产入口仍关闭。禁止为能启动把缺失依赖换成成功 noop。

## T17：标准 HTTP+JSON、认证边界和机器可读合同

**依赖：** T04、T08、T11–12、T14、T16。**产物：** 注入真实应用的完整路由，标准响应和控制面 envelope 不混淆。

**Files:**
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/mod.rs`、`apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/dto.rs`、`apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/auth.rs`、`apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/errors.rs`、`apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/send.rs`、`apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/tasks.rs`。
- Modify: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/mod.rs`、`apps/bcs/crates/adapters/http/bcs-api-http/src/v1/common/state.rs`。
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/tests/group_a2a_routes.rs`、`apps/bcs/crates/adapters/http/bcs-api-http/tests/support/group_a2a.rs`。
- Create: `apps/bcs/api-contracts/v1/internal/group-a2a.yaml`。
- Modify: `apps/bcs/api-contracts/v1/internal.yaml`、`apps/bcs/api-contracts/v1/openapi/groups.yaml`。
- Modify: `apps/bcs/scripts/validate_openapi_contract.py`。
- Create: `apps/bcs/tests/openapi/test_group_a2a_contract.py`。
- Create: `docs/specs/a2a/extensions/call-metadata/v1.md`、`docs/specs/a2a/extensions/call-metadata/v1.schema.json`。

**Interfaces:** Consumes GroupA2aService、A2aAuthenticationService、GroupA2aCardService；Produces seven operation handlers、strict request normalization、error map。ApiState 用明确 Optional 代表整个新能力未启用，但开启时必须完整注入，不能逐方法静默 fallback。

- [ ] **1. RED HTTP 测试。** `http_world()` 组装 Axum router＋T03/T07 真 SQLite 服务；`post_send(token:Option<&str>,body:Value)->Response` 用 oneshot 请求真实 router；synthetic token 是 fixture 管理面签发的合法用途：

```rust
let w = http_world().await;
let denied = w.post_send(None, send_body("m1")).await;
assert_eq!(denied.status(), StatusCode::UNAUTHORIZED);
assert_eq!(w.start_count(), 0);
let accepted = w.post_send(Some(w.token()), send_body("m1")).await;
assert_eq!(accepted.status(), StatusCode::OK);
let json = response_json(accepted).await;
assert!(json.get("task").is_some());
assert!(json.get("data").is_none());
```

`send_body(id)` 构造 examples §3 的请求（无真实凭据）；`response_json` 读取测试 Response body，失败直接测试失败。
- [ ] **2. RED。** `cargo test -p bcs-api-http --test group_a2a_routes`；先确保 expected 401/standard body 失败，而不是依赖装配 panic。
- [ ] **3. 实现传输验证。** body 1MiB 解析前限制；strict JSON visitor 拒绝重复键、非法 Unicode／数；parts oneof/≤64、object data≤1、role／messageId；完整 canonical request 给 Core，Authorization 不入 body。非支持 mime/version/extensions 提前拒绝，无 reservation。
- [ ] **4. 路由／错误／权限。** action suffix 精确拆解一次；cancel/subscribe 空 body `{}` 用 path ID，body ID 冲突400；unknown suffix404。旧 `/agents/**` Provider 管理认证／envelope 不变。所有七操作及 unsupported capability endpoint 认证；task 操作不因开关关闭失去访问。ErrorInfo 对应 spec §11，401带 challenge，503不冒充401。

```rust
match error {
    CallError::TaskNotFound => error_info(404, "NOT_FOUND", "TASK_NOT_FOUND", "a2a-protocol.org"),
    CallError::ContextBusy => error_info(409, "ABORTED", "CONTEXT_BUSY", "inclusionai.avernet.bcs"),
    CallError::NotCancelable => error_info(400, "FAILED_PRECONDITION", "TASK_NOT_CANCELABLE", "a2a-protocol.org"),
    CallError::StorageUnavailable => error_info(503, "UNAVAILABLE", "STORAGE_UNAVAILABLE", "inclusionai.avernet.bcs"),
    other => map_remaining_call_error(other),
}
```

`error_info` 和 `map_remaining_call_error` 定义在 errors.rs，后者对接口清单 CallError **穷尽 match**，不得 `_ => 500` 吞掉规范错误；每个 variant 对应一条 table test。
- [ ] **5. 更新机器合同。** YAML 增加标准裸 A2A 例外标记，validator 仅对确切 path+method+protocol 放行；其他 internal endpoint 仍需 envelope。发布 includeProgress schema，仅 boolean，Card `$ref` 可机器获取；缺 activation header400且不占ID。正式输出选择 a2aOutput 在 Group/runtime 管理合同声明，不从 A2A 请求传任意 workflow。
- [ ] **6. GREEN。** `cargo test -p bcs-api-http --test group_a2a_routes --test a2a_routes --test boundary_contract`；仓库根 `uv run --with pytest --with pyyaml --with jsonschema pytest apps/bcs/tests/openapi/test_group_a2a_contract.py -q`；`uv run --with pyyaml python apps/bcs/scripts/validate_openapi_contract.py --root apps/bcs/api-contracts/v1`。生成 schema fixture 验证 examples，不仅 JSON parse。
- [ ] **7. 提交。** `feat(bcs): expose authenticated group A2A HTTP contracts`。

## T18：等待、SSE 高水位与独立认证复验

**依赖：** T13、T17。**产物：** 默认阻塞 send、stream/subscribe、持续输出时的 token 复验、有界缓冲。

**Files:**
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/sse.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/src/wait.rs`。
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/tests/group_a2a_streaming.rs`。
- Create: `apps/bcs/crates/application/bcs-app-group-a2a/tests/wait.rs`。
- Modify: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/send.rs`、`apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/group/tasks.rs`。

**Interfaces:** Implements TaskWaitService 和 A2aChangeNotifier 消费；StreamResponse={task|statusUpdate|artifactUpdate}；in-flight stream 持 runtime-only AuthenticatedA2aCaller，按独立 timer 复验 credential，不保存原文 token。

- [ ] **1. RED：snapshot 到 subscribe 完成竞争。** HTTP fixture `subscribe_with_barrier(key)` 在取得初始高水位后等待 test Barrier；`finish` 通过真实 terminal transaction，`collect_sse` 解析有限流：

```rust
let w = http_world().await;
let task = w.running_call("m1").await;
let stream = w.subscribe_with_barrier(&task.key).await;
w.finish(&task.key, "done").await;
w.release_subscription_barrier();
let frames = collect_sse(stream).await;
assert!(frames.first().unwrap().get("task").is_some());
assert!(frames.iter().any(is_completed_status));
assert_eq!(frames.iter().filter(|f| f.get("artifactUpdate").is_some()).count(), 1);
```

`is_completed_status(&Value)->bool` 精确比较 statusUpdate.status.state，非字符串 contains；flush/ordering 用真实 Axum stream body 测试。
- [ ] **2. RED。** `cargo test -p bcs-api-http --test group_a2a_streaming && cargo test -p bcs-app-group-a2a --test wait`。
- [ ] **3. 实现读循环。** snapshot Task+highwater 后从持久化 events(after) 消费；通知只是唤醒。成功最终产物→终态，终态关闭；新 subscribe 已终态400；重复 stream 命中终态发一帧Task后关闭。无 Last-Event-ID 逐事件重放承诺。
- [ ] **4. 独立 timer 与限制。** 不用“收到业务消息则重置所有 timer”的循环；select 分别保留 heartbeat、credential_check、credential_expiry、execution wait deadlines：

```text
select:
  new event/notification → read committed page → bounded send
  heartbeat tick → SSE comment only
  auth_check tick (15s) → revalidate; failure closes transport
  credential expiry → close transport
  client disconnect → release connection permit, never cancel Task
```

持续 progress 也每15秒复验；慢客户端最多128条/1MiB，溢出关闭。Caller32／instance512连接预算，RAII 释放；请求正文和业务deadline不受心跳影响。auth dependency失败关闭流但不改Task。
- [ ] **5. 等待测试与错误。** fake clock推进60s，默认 send返回504且 Task继续；及时终态返回200，returnImmediately=true不等待；断线／过期／撤销保持后台执行；没有“网关504→换messageId重发”。includeProgress=false仍有状态/最终产物/心跳。
- [ ] **6. GREEN。** 重跑第2步；同时测试活跃流不断事件时撤销、连接数超限429和关闭后 permit回收；真实网关缓冲与idle timeout仅T20集成验证，不能拿单元test代替。
- [ ] **7. 提交。** `feat(bcs): stream committed A2A results with bounded authenticated waits`。

## T19：公共装配、托管资源旁路保护与安全启动

**依赖：** T05、T09–18 全部。**产物：** 新能力显式依赖注入，disabled不挂载、enabled缺依赖启动失败，旧入口无法绕过生命周期。

**Files:**
- Create: `apps/bcs/crates/bootstrap/bcs/src/group_a2a/mod.rs`、`apps/bcs/crates/bootstrap/bcs/src/group_a2a/execution.rs`、`apps/bcs/crates/bootstrap/bcs/src/group_a2a/stores.rs`、`apps/bcs/crates/bootstrap/bcs/src/group_a2a/lifecycle.rs`。
- Modify: `apps/bcs/crates/bootstrap/bcs/src/group_a2a/auth.rs`。
- Modify: `apps/bcs/crates/bootstrap/bcs/src/config/root.rs`、`apps/bcs/crates/bootstrap/bcs/src/config/validation.rs`、`apps/bcs/crates/bootstrap/bcs/src/http_adapter.rs`、`apps/bcs/crates/bootstrap/bcs/src/server.rs`、`apps/bcs/crates/bootstrap/bcs/src/server/lifecycle_wiring.rs`。
- Modify: `apps/bcs/crates/bootstrap/bcs/Cargo.toml`。
- Create: `apps/bcs/crates/services/bcs-session/src/a2a_guard.rs`。
- Modify: `apps/bcs/crates/services/bcs-session/src/lib.rs`、`apps/bcs/crates/services/bcs-session/src/application/sessionmanagementserviceimpl.rs`、`apps/bcs/crates/services/bcs-session/src/application/sessionmanagementserviceimpl_2.rs`。
- Modify: `apps/bcs/crates/services/bcs-group/src/core/group_core.rs`（delete/terminate保护）。
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/lib.rs`（托管Session聊天入口保护，逻辑放专属a2a_group模块）。
- Modify: `apps/bcs/crates/application/v1/bcs-app-session/src/participant.rs`、`apps/bcs/crates/application/v1/bcs-app-session/src/lib.rs`。
- Modify: `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/rerun.rs`。
- Create: `apps/bcs/crates/bootstrap/bcs/tests/integration_group_a2a_wiring.rs`、`apps/bcs/crates/bootstrap/bcs/tests/integration_group_a2a_bypass.rs`。
- Modify: `apps/bcs/configs/bcs-config-example.toml`。

**Interfaces:** Produces `GroupA2aComponents {service,auth,card,wait,lifecycle}`（Arc相应traits）；`build_group_a2a(config,db,existing_services)->Result<Option<GroupA2aComponents>,StartupError>`。Bootstrap execution bridge选strategy调用 T09/T10 application方法，无业务判断复制。管理 CLI 通过同一 public构造获取依赖，不自己挑另外一组 DB／配置。

- [ ] **1. RED 装配。** 新测试 fixture `boot(config)` 调公共构造，router测试按实际组件挂载：

```rust
let mut cfg = valid_test_config();
cfg.a2a.enabled = false;
assert!(!boot(cfg.clone()).await.unwrap().has_group_a2a_routes());
cfg.a2a.enabled = true;
cfg.a2a.auth.chain.clear();
assert!(boot(cfg).await.is_err());
```

boot返回 TestServer 包装实际 app与lifecycle，has_group_a2a_routes 通过HTTP路径响应而非固定bool判断。valid_test_config包含 SQLite、正式测试client token而非local mock。
- [ ] **2. RED。** `cargo test -p bcs --test integration_group_a2a_wiring --test integration_group_a2a_bypass`。
- [ ] **3. 分阶段接线。** config validate→migrations/schema readiness→token store/plugin/bridge→group repo/core/app→执行端口→wait/card→恢复/retention/publication lifecycle→HTTP。所有实例共享同逻辑 DB，claim带 fencing；从未启用的部署禁用时不创建worker，启用时缺存储／执行端口拒绝启动。已有任务的部署不能以全局disabled替代drain；先关各群开关并完成收尾，保留查询／恢复到安全停机。Card发布还受入口就绪状态约束，不能入口未挂载却注册可调用地址。
- [ ] **4. 封堵旧旁路。** A2A托管Session在 Session application／Core入口拒绝普通reactivate、聊天、成员增删、原生rerun；管理delete先验证无活动／隔离且清理顺序受控。不能只改legacy `/services` handler，测试直接Service API同样拒绝。可信execution关联回调仍能工作，不用客户端可伪造flag绕过guard。
- [ ] **5. 启停与配置测试。** 关闭supportA2A先禁止新受理，历史query/cancel仍通；shutdown停止claim/dispatch，保存未知状态，不伪造失败完成；重启恢复。global disabled路由不存在；旧Auth chain、Provider admin、Group管理权限回归。bootstrap server.rs已经989行，新代码放group_a2a模块，必须检查≤1000。
- [ ] **6. GREEN。** 重跑第2步；`cargo check -p bcs --all-targets && cargo test -p bcs-app-session && cargo test -p bcs-api-http --test a2a_routes`。主程序与嵌入构造都覆盖；没有host checkout则记录未验证，不能写host已集成。
- [ ] **7. 提交。** `feat(bcs): assemble group A2A services and protect managed execution boundaries`。

## T20：端到端、真实 MySQL、预算与交付记录

**依赖：** T01–19。**产物：** 可复现的发布证据、更新的当前合同与完整退出门槛，不自动部署。

**Files:**
- Create: `apps/bcs/crates/bootstrap/bcs/tests/integration_group_a2a.rs`、`apps/bcs/crates/bootstrap/bcs/tests/integration_group_a2a_mysql.rs`。
- Create: `apps/bcs/scripts/e2e-test/test_group_a2a.py`。
- Create: `apps/bcs/tests/openapi/test_group_a2a_examples.py`。
- Modify: `apps/bcs/README.md`、`apps/bcs/docs/bcsfuse-catalog.md`、`apps/bcs/api-contracts/README.md`。
- Modify: `apps/bcs/crates/service-api/bcs-service-api/CONTEXT.md`、`apps/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`、`apps/bcs/crates/services/bcs-message-flow/CONTEXT.md`。
- Create: `apps/bcs/specs/2026-11-16-group-as-agent/delivery.md`（仅记录实际执行结果）。

**Interfaces:** Consumes全部已交付合同；Produces AC-01–34各有测试名、运行命令、结果、实际源revision与未验证边界的交付记录。

- [ ] **1. 先写跨层 RED。** 一个入口贯穿“管理员签发→开启群→send→manager/SM→Artifact→关闭→历史查询→取消另一个任务”。不能直接写Task表替代执行；HTTP client只使用examples中的标准字段。用例至少包含Agent client和未注册Bot的应用client。

```python
async def test_disable_blocks_new_round_but_preserves_owned_result(world):
    token = await world.issue_client_token("application-a")
    group = await world.create_public_manager_group(supportA2A=True)
    first = await world.send(group, token, message_id="m1")
    await world.bot_complete(first, "done")
    await world.patch_group(group, supportA2A=False)
    assert (await world.get_task(group, token, first.id))["status"]["state"] == "TASK_STATE_COMPLETED"
    rejected = await world.send_raw(group, token, message_id="m2", context_id=first.context_id)
    assert rejected.status_code == 404
```

`world` fixture在新e2e文件定义：管理动作使用测试管理员身份，调用动作使用签发token；bot_complete通过 Rule Bot／fake Provider协议，不接数据库。配置、port、SQLite位置全部放临时目录，HTTP stub bound loopback；测试token不入快照／日志。
- [ ] **2. RED与单机。** `cargo test -p bcs --test integration_group_a2a`；仓库根 `uv run --with pytest --with pytest-asyncio --with httpx pytest apps/bcs/scripts/e2e-test/test_group_a2a.py -q`。一次失败必须可定位具体合同，不加自动重试掩盖flaky。
- [ ] **3. 实现遗漏的跨层适配与样例校验。** 对照spec AC逐条建立测试映射，覆盖shutdown/restart、旧Run晚回调、wrongcaller、旧目录DELETE、bounded buffers／counts、BodyError。JSON/SSE样例用正式DTO／JSON Schema反序列化，不只parse；错误reason、securityRequirements、list totalSize、subscribe语义对固定A2A版本。
- [ ] **4. 真实MySQL。** 独立可丢弃测试库，`BCS_TEST_MYSQL_URL`从本地秘密环境注入，不写入文件／命令回显；`cargo test -p bcs --test integration_group_a2a_mysql -- --ignored --test-threads=1`。覆盖空库迁移、旧库升级、唯一collation、事务rollback、group enable/add-Human、双新call争context、cancel/complete、expiry/admission竞争。捕获EXPLAIN与query/row counts，不以SQLite代替。
- [ ] **5. 预算和负载。** 建1/50/100页、16×256KiB产物边界、256未终结关联、16 caller/128 deployment执行容量、32/512连接等case，断言上限、退避、连接池不被长事务占用。大规模计数／持久化墓碑增长测试用合成数据；性能结果记录机器、数据量、并发、p95和失败率，不把小测试推成生产保证。
- [ ] **6. GREEN 与完整门槛。** 从仓库根运行 `bash apps/bcs/scripts/ci_test.sh --fast-fail`；再跑真实OpenAPI合同、已有A2A出站／Group管理／Session／Provider回归和CI要求的覆盖率门槛。读取 `docs/agents/validation.md` 后按正常push gate执行，不能设置跳过开关。基线失败单独定位记录，不能全归本改动或忽略。
- [ ] **7. 知识和运维交接。** 更新目录发布、接口、CONTEXT、CLI说明；记录扩展URI已发布状态。提供开启前旧Card核对、drain后关闭/回退、停止证据人工核实步骤；不回滚新表／墓碑。host源不在仓库时交付所需构造接口清单，标明未实际集成。
- [ ] **8. 提交。** `test(bcs): verify group A2A lifecycle across SQLite MySQL and HTTP`。delivery记录每项真实结果；实现／本地提交／推送／合并／host／部署六种状态分开，不自动把下一状态写成完成。

## 最终停止条件

只有所有强制测试和实际依赖都验证后，才能把实现完成写入delivery。若MySQL／host／网关不可用，保留对应阻塞或缺口并交给用户，不以“计划已写完”或“Rust通过”替代。用户未授权部署时，停在可审阅的分支／PR，不操作任何生产Group、凭据或目录。
