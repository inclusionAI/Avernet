# A. 基础合同、迁移与调用方认证（T01–T05）

执行入口：[plan.md](../plan.md)。每个测试片段使用 [接口清单](interfaces.md) 中的同名类型；新增模块的 mod、Cargo member/dependency、CONTEXT 和 conformance 注册与首次行为一起提交。每项 Step 内按一个断言／一个函数／一次测试推进，不批量写完才测试。

## T01：纯模型、状态转换与严格配置

**依赖：** 无。**产物：** 可独立编译／测试的 domain、Service API 与配置，不创建没有实现的生产空 crate。

**Files:**
- Create: `apps/bcs/crates/contracts/bcs-domain/src/group_a2a/mod.rs`、`apps/bcs/crates/contracts/bcs-domain/src/group_a2a/identity.rs`、`apps/bcs/crates/contracts/bcs-domain/src/group_a2a/input.rs`、`apps/bcs/crates/contracts/bcs-domain/src/group_a2a/state.rs`、`apps/bcs/crates/contracts/bcs-domain/src/group_a2a/output.rs`、`apps/bcs/crates/contracts/bcs-domain/src/group_a2a/configuration.rs`、`apps/bcs/crates/contracts/bcs-domain/src/group_a2a/error.rs`。
- Create: `apps/bcs/crates/service-api/bcs-config-api/src/group_a2a.rs`。
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/application/group_a2a.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/application/a2a_auth.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/core/group_invocation.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/group_execution.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/a2a_credentials.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/group_invocation.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/a2a_token.rs`。
- Modify: `apps/bcs/crates/contracts/bcs-domain/src/lib.rs`。
- Modify: `apps/bcs/crates/service-api/bcs-config-api/src/lib.rs`。
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/mod.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/core/mod.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/mod.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/mod.rs`。
- Create: `apps/bcs/crates/contracts/bcs-domain/tests/group_a2a_state.rs`。
- Create: `apps/bcs/crates/service-api/bcs-config-api/tests/group_a2a_config.rs`。
- Create: `apps/bcs/crates/test-support/bcs-test-support/src/contract/group_a2a/mod.rs`、`apps/bcs/crates/test-support/bcs-test-support/src/contract/group_a2a/fixtures.rs`。

**Interfaces:** Produces 接口清单 §1–2 的 DTO／trait、`transition`、`validate_a2a_config`；Consumes 现有 GroupStrategy、Participant、secrecy 0.8。不把 CallError 转成 HTTP 类型。

- [ ] **1. 写 RED 状态与配置测试。** 每个非法边一条 table-driven assertion，先固定已确认的取消竞争与空链拒绝：

```rust
#[test]
fn completion_intent_excludes_cancel() {
    assert!(matches!(transition(Phase::Completing, LifecycleEvent::CancelIntent),
        Err(CallError::NotCancelable)));
    assert_eq!(transition(Phase::Running, LifecycleEvent::CancelIntent).unwrap(),
        Phase::Cancelling);
}
#[test]
fn a2a_cannot_inherit_anonymous_auth() {
    let mut cfg = GroupA2aConfig::default();
    cfg.enabled = true;
    cfg.auth.chain.clear();
    assert_eq!(validate_a2a_config(&cfg), Err(ConfigError::MissingAuth));
    cfg.auth.chain = vec!["none".into()];
    assert_eq!(validate_a2a_config(&cfg), Err(ConfigError::UnsupportedAuth));
}
```

- [ ] **2. 运行 RED。** `cargo test -p bcs-domain --test group_a2a_state && cargo test -p bcs-config-api --test group_a2a_config`。先缺类型／函数而编译失败；加最小声明后须看到非法状态／配置断言失败，再写实现。
- [ ] **3. 定义接口清单类型，逐条实现状态转换。** 不以“任意非终态都能转终态”代替意图仲裁：

```rust
match (phase, event) {
    (Phase::Running, LifecycleEvent::CompleteIntent) => Ok(Phase::Completing),
    (Phase::Admitted | Phase::Starting | Phase::Running, LifecycleEvent::CancelIntent)
        => Ok(Phase::Cancelling),
    (Phase::Completing, LifecycleEvent::CancelIntent) => Err(CallError::NotCancelable),
    (Phase::Completing, LifecycleEvent::CommitSuccess) => Ok(Phase::Terminal),
    (Phase::Cancelling, LifecycleEvent::CommitCanceled) => Ok(Phase::Terminal),
    (Phase::Failing, LifecycleEvent::CommitFailure) => Ok(Phase::Terminal),
    _ => Err(CallError::Conflict),
}
```

上面是关键分支；再分别加 Start、FailIntent 的测试和实现，保证 Starting／Completing／Cancelling 发生失败能进入 Failing，但 Terminal 不可逆。配置逐个复制 spec §12 默认值，positive 值和上限逐项验证，enabled=false 时不强求凭据依赖。
- [ ] **4. 增加表驱动边界。** 检查所有 7 个 Phase×7 个事件组合、deadline 加法溢出、未识别配置键、零／负／超大 limits、PresentedToken 无 serde／redacted Debug；在 fixture 中构造 caller/scope/request，禁止真实 token。
- [ ] **5. GREEN 与 API 回归。** 重跑第 2 步；`cargo test -p bcs-service-api && cargo check -p bcs-config-api -p bcs-domain`。Expected PASS，不要求所有后续 trait 已有具体实现。更新各 CONTEXT 的新增合同，说明尚未挂载入口。
- [ ] **6. 提交。** 按 Files 逐项 stage，`git diff --cached --check`；主题 `feat(bcs): define group A2A lifecycle and configuration contracts`。

## T02：一次性交付完整 schema 和迁移测试

**依赖：** T01。**产物：** 可从空库和当前版本升级的双方言 schema；后续任务只实现操作，不回填本迁移。

**Files:**
- Create: `apps/bcs/crates/services/bcs-db-migrations/src/migrations/group_a2a.rs`。
- Create: `apps/bcs/crates/services/bcs-db-migrations/src/migrations/group_a2a_tests.rs`。
- Modify: `apps/bcs/crates/services/bcs-db-migrations/src/lib.rs`。
- Modify: `apps/bcs/crates/services/bcs-db-migrations/src/migrations/tests.rs`。
- Modify: `apps/bcs/crates/tools/bcs-admin/src/migrate.rs`（注册新测试模块；MySQL目录读取沿用现有实现）。
- Create: `apps/bcs/crates/tools/bcs-admin/src/migrate_group_a2a_tests.rs`。
- Modify: `apps/bcs/migrations/README.md`。
- Create: `apps/bcs/migrations/mysql/` 下按下面算法分配的唯一 `*_group_a2a.sql`；基线最新 046，因此若未变化输出精确为 `047_group_a2a.sql`。

**Interfaces:** Consumes spec §9 的表及 T01 DTO；Produces schema 支持 Repo 原子操作、`sqlite_target_version()` 新 target、冻结的迁移 checksum。

- [ ] **1. 写 RED 升级断言。** 在现有 migrations test support 上增加 `group_a2a_upgrade_keeps_legacy_groups_closed`，插入旧群后应用新迁移，断言 support_a2a=0、旧群内容不变、reservation unique key 区分 case。测试版本断言使用 `sqlite_target_version()`，不写 `Some(44)`。

```rust
assert_eq!(row.get_i64("support_a2a").unwrap().unwrap(), 0);
assert_eq!(row.get_string("label").unwrap().unwrap(), "legacy");
assert_eq!(applied_version, sqlite_target_version());
assert!(insert_same_reservation_twice(&db).await.is_err());
```

此片段的 `row` 是 DbPlugin.query 返回的 DbRow；`insert_same_reservation_twice` 使用真实 INSERT、相同全键执行两次，不返回预设错误。它们在新增测试文件定义。
- [ ] **2. 运行 RED。** `cargo test -p bcs-db-migrations group_a2a`，Expected missing table/column；先确保筛选确实执行新增测试，不接受 0 tests。
- [ ] **3. 分配编号，再写 schema。** 仓库根执行以下只计算编号的脚本，在实施日志记下结果；此时不得修改任何旧迁移：

```python
from pathlib import Path
import re
root = Path('apps/bcs')
mysql = max(int(p.name.split('_', 1)[0]) for p in (root/'migrations/mysql').glob('[0-9]*_*.sql')) + 1
source = (root/'crates/services/bcs-db-migrations/src/lib.rs').read_text()
sqlite = max(map(int, re.findall(r'version:\s*(\d+)', source))) + 1
print(root / f'migrations/mysql/{mysql:03d}_group_a2a.sql')
print('SQLite version:', sqlite)
```

按 [存储布局](schema.md) 创建 spec §9 所有表，同时加入 token/client、execution associations、public source cursors、capacity counters、publication intents、output binding 快照和 Session 托管标记所需列，避免每个后续任务各补一次 migration。JSON 仅放有界 payload，过滤字段独立列；scope／ID binary comparison。Group 和 Session 的已有列在新 migration ALTER，禁止更新 baseline。
- [ ] **4. 逐表补约束测试。** admission 唯一键、artifact position、token digest、source event 去重；due/retention 查询索引；revision CHECK／溢出；MySQL 大索引用固定 hash＋完整键碰撞比较，不截短 client/messageId。数据库类测试用回滚或隔离测试数据库，不连接生产。
- [ ] **5. GREEN 与真实方言。** `cargo test -p bcs-db-migrations group_a2a && cargo test -p bcs-admin group_a2a`；`cargo run -p bcs-admin -- db migrate --dialect mysql --check-files`。真实 MySQL 测试读取 `BCS_TEST_MYSQL_URL`，无配置标记未运行，不能静态 check 冒充通过；T20 是合并前强制门槛。
- [ ] **6. 提交。** 主题 `feat(bcs): persist group A2A calls credentials and publication intents`。本提交之后冻结编号、内容、checksum；后续发现 schema 缺口只能新增迁移并报告原因。

## T03：client／token 的真实存储与轮换

**依赖：** T02。**产物：** Memory／SQLite／MySQL 的 A2aTokenRepo，可单独验收，不涉及 Group 访问范围。

**Files:**
- Create: `apps/bcs/crates/services/bcs-a2a-token-store/Cargo.toml`、`apps/bcs/crates/services/bcs-a2a-token-store/src/lib.rs`、`apps/bcs/crates/services/bcs-a2a-token-store/src/memory.rs`、`apps/bcs/crates/services/bcs-a2a-token-store/src/sql.rs`、`apps/bcs/crates/services/bcs-a2a-token-store/CONTEXT.md`。
- Create: `apps/bcs/crates/services/bcs-a2a-token-store/tests/conformance.rs`、`apps/bcs/crates/services/bcs-a2a-token-store/tests/conformance_mysql.rs`、`apps/bcs/crates/services/bcs-a2a-token-store/tests/support/mod.rs`。
- Create: `apps/bcs/crates/test-support/bcs-test-support/src/contract/repo/a2a_token.rs`。
- Modify: `apps/bcs/Cargo.toml`。
- Modify: `apps/bcs/crates/test-support/bcs-test-support/src/contract/repo/mod.rs`。

**Interfaces:** Implements A2aTokenRepo（接口清单 §2.5）；Consumes DbPlugin，不访问 BotRepo；Produces `SqlA2aTokenStore::new(db,logical_db,deployment)` 和 `MemoryA2aTokenStore::default()`，实现选择仅在测试／bootstrap。

- [ ] **1. 写 RED 轮换及撤销测试。** 实现接口清单 issue_fixture、test_token_store，真实 DB 写入两个同 client token：

```rust
#[tokio::test]
async fn rotation_keeps_caller_and_revocation_is_immediate() {
    let store = test_token_store().await;
    let old = issue_fixture(&store, "app-a", "fixture-old", now(), 1000).await;
    issue_fixture(&store, "app-a", "fixture-new", now(), 2000).await;
    let a = store.lookup(sha256("fixture-old"), now()).await.unwrap().unwrap();
    let b = store.lookup(sha256("fixture-new"), now()).await.unwrap().unwrap();
    assert_eq!(a.caller, b.caller);
    store.revoke(&old, now()).await.unwrap();
    assert!(store.lookup(sha256("fixture-old"), now()).await.unwrap().is_none());
    assert!(store.lookup(sha256("fixture-new"), now()).await.unwrap().is_some());
}
```

测试 helper `sha256(&str)->[u8;32]` 调 `Sha256::digest`；issue_fixture 对已存在且一致 client 不重复 create，其余失败必须上抛。
- [ ] **2. RED。** `cargo test -p bcs-a2a-token-store --test conformance rotation_keeps_caller`，最初 missing crate 后注册最小 crate，确认 lookup 行为断言失败。
- [ ] **3. 实现精确查询。** 一次 join 取 token+client，条件必须同时包含 deployment、purpose、expires_at>now、未撤销、client.enabled；current(credential_id) 用同条件复验。list 只投影 TokenMetadata，不 SELECT digest。revoke／disable 幂等但 DB 错误返回 Unavailable。

```sql
SELECT t.credential_id, t.client_id, t.expires_at_ms
FROM bcs_a2a_tokens t JOIN bcs_a2a_clients c
  ON c.deployment=t.deployment AND c.client_id=t.client_id
WHERE t.deployment=? AND t.digest=? AND t.purpose='a2a_call'
  AND t.expires_at_ms>? AND t.revoked_at_ms IS NULL AND c.enabled=1
LIMIT 1
```

- [ ] **4. 扩展共享 conformance。** 到期边界等于 now 拒绝、跨部署摘要无结果、disable_client 不再可认证、ID 不复用、写失败不返回凭据、数据库故障≠missing。SQL recorder 断言 lookup 恰好一次 query，不扫描所有 Bot。
- [ ] **5. GREEN。** `cargo test -p bcs-a2a-token-store`；MySQL runner 同 suite，`cargo test -p bcs-a2a-token-store --test conformance_mysql -- --ignored` 必须显式提供测试连接，缺配置报清晰前置条件错误。
- [ ] **6. 提交。** 主题 `feat(bcs): store scoped A2A client credentials with revocation`。

## T04：独立 A2A 认证 profile 与 BCS Token 插件

**依赖：** T01、T03。**产物：** 可注入的 plugin→port→application 认证链，拒绝匿名与无效凭据降级，不改旧登录链。

**Files:**
- Create: `apps/bcs/crates/plugin-api/bcs-auth-api/src/a2a.rs`。
- Modify: `apps/bcs/crates/plugin-api/bcs-auth-api/src/lib.rs`。
- Create: `apps/bcs/crates/plugins/bcs-auth-bcs-token/Cargo.toml`、`apps/bcs/crates/plugins/bcs-auth-bcs-token/src/lib.rs`、`apps/bcs/crates/plugins/bcs-auth-bcs-token/src/verify.rs`、`apps/bcs/crates/plugins/bcs-auth-bcs-token/CONTEXT.md`、`apps/bcs/crates/plugins/bcs-auth-bcs-token/tests/auth_chain.rs`。
- Create: `apps/bcs/crates/application/bcs-app-a2a-auth/Cargo.toml`、`apps/bcs/crates/application/bcs-app-a2a-auth/src/lib.rs`、`apps/bcs/crates/application/bcs-app-a2a-auth/src/authenticate.rs`、`apps/bcs/crates/application/bcs-app-a2a-auth/CONTEXT.md`、`apps/bcs/crates/application/bcs-app-a2a-auth/tests/authentication.rs`。
- Create: `apps/bcs/crates/bootstrap/bcs/src/group_a2a/auth.rs`，只写 bridge，T19 接线。
- Modify: `apps/bcs/Cargo.toml`。

**Interfaces:** Produces A2aAuthPlugin／A2aAuthChain 与 A2aAuthenticationService；Consumes plugin-owned CredentialLookup（通过 bootstrap 适配 T03），application 只持 A2aCredentialVerifierPort 和 A2aClock。

- [ ] **1. 写 RED 的链拒绝与调用证明。** 新增测试 fixture `RejectPlugin`、`RecordingSuccessPlugin`，后者持 AtomicUsize；插件返回值严格四分类：

```rust
let hit = Arc::new(AtomicUsize::new(0));
let chain = A2aAuthChain::new(vec![
    Box::new(RejectPlugin), Box::new(RecordingSuccessPlugin(hit.clone()))
]);
assert!(matches!(chain.verify(&fixture_token(), now()).await,
    Err(PluginAuthError::Rejected)));
assert_eq!(hit.load(Ordering::SeqCst), 0);
```

fixture_token() 返回测试 PresentedCredential，不在 assertion Debug 展开秘密。再写真实 bcs_token 插件对 SQLite lookup 的成功／撤销测试，证明 lookup 被调用。
- [ ] **2. RED。** `cargo test -p bcs-auth-bcs-token --test auth_chain && cargo test -p bcs-app-a2a-auth --test authentication`。
- [ ] **3. 实现链。** 只 NotApplicable 才继续，Rejected/Unavailable 立即结束；全部不匹配为 Rejected。plugin 把秘密 hash 后精确 lookup，不接受 JWT 外形就绕过用途；application 包 tokio timeout，错误不带原始 token。bridge 映射稳定 issuer+subject，不用 digest 充当 caller。

```rust
match plugin.verify(credential, now_ms).await {
    PluginVerdict::NotApplicable => continue,
    PluginVerdict::Authenticated(p) => return Ok(p),
    PluginVerdict::Rejected => return Err(PluginAuthError::Rejected),
    PluginVerdict::Unavailable => return Err(PluginAuthError::Unavailable),
}
```

- [ ] **4. 添加 redaction／生命周期测试。** 1 个匹配插件一次成功；无命中拒绝；token 到期、DB 超时、配置空链／none/local 均不放行；关闭释放资源。捕获 DEBUG 输出插入 sentinel，断言秘密、摘要、credential_id 未被记录。
- [ ] **5. GREEN 与旧链回归。** `cargo test -p bcs-auth-api -p bcs-auth-bcs-token -p bcs-app-a2a-auth -p bcs-auth-session`。Expected 旧 AuthPluginChain 优先级语义和配置默认不变，A2A 使用自己的 profile。
- [ ] **6. 提交。** 主题 `feat(bcs): authenticate A2A callers through a strict token plugin`。

## T05：最小管理员凭据 CLI

**依赖：** T03–04。**产物：** 可在公共本地环境签发并撤销通用 client 凭据；不增加公开匿名发行 API。

**Files:**
- Create: `apps/bcs/crates/application/bcs-app-a2a-auth/src/credentials.rs`、`apps/bcs/crates/application/bcs-app-a2a-auth/tests/credentials.rs`。
- Create: `apps/bcs/crates/tools/bcs-admin/src/a2a_credentials.rs`。
- Modify: `apps/bcs/crates/tools/bcs-admin/src/main.rs`。
- Modify: `apps/bcs/crates/tools/bcs-admin/Cargo.toml`。
- Create: `apps/bcs/crates/tools/bcs-admin/tests/a2a_credentials.rs`。

**Interfaces:** Implements A2aCredentialAdminService；CLI 操作 `a2a client create|disable`、`a2a token issue|list|revoke`；Consumes 现有管理员 config loader／本地运维权限，不把调用 token 接成管理身份。

- [ ] **1. RED。** 用测试 Repo＋固定 fake clock 调 service.issue，验证失败无 secret 和不同发行随机：

```rust
let first = admin.issue("app-a", 60_000).await.unwrap();
let second = admin.issue("app-a", 60_000).await.unwrap();
assert_ne!(first.secret.expose(), second.secret.expose());
assert_eq!(first.metadata.client_id, second.metadata.client_id);
assert!(!format!("{:?}", first).contains(first.secret.expose()));
```

`PresentedToken::expose(&self)->&str` 仅签发响应／验证边界使用；IssuedToken 自定义 redacted Debug。测试 admin 构造用真实服务＋T03 SQLite repo，不 mock issue。
- [ ] **2. 运行。** `cargo test -p bcs-app-a2a-auth --test credentials && cargo test -p bcs-admin --test a2a_credentials`，Expected 缺命令／无随机签发行为。
- [ ] **3. 实现随机签发和提交顺序。** 使用 OsRng.fill_bytes([u8;32])，base64url，SHA-256；先 Repo.issue，再返回 IssuedToken；默认 TTL=30d，最大 90d，拒绝零／超限。create client 是显式动作，issue 不隐式创建未知 client。
- [ ] **4. CLI 仅展示一次。** `bcs-admin a2a token issue --client-id app-a` 仅 stdout 输出 token；stderr 和 `--debug` 不泄漏；list 无摘要和秘密；revoke 按 credential-id 不要求把 token 写进 argv。测试 invoke binary＋临时 SQLite 配置并解析输出，删除 tmp fixture 不入库提交。
- [ ] **5. GREEN。** 重跑第 2 步，`cargo run -p bcs-admin -- a2a --help`，用新 token 经 T04 验证，再 revoke 后拒绝；记录这是管理员 CLI 不是公开 token grant。
- [ ] **6. 提交。** 主题 `feat(bcs): issue and revoke A2A client tokens with admin tooling`。
