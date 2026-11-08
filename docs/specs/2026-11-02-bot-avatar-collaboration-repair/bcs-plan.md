# BCS Bot Avatar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 BCS 独立保存 Bot 头像，通过现有注册/资料更新入口接收，并在 V1 Group/Session 中返回，提供不会创建缺失 Bot 的资料更新行为。

**Architecture:** `bcs_bots.avatar_url` 是头像副本的唯一 SQL 存储；repository 提供 existing-only 资料写入和轻量批量头像查询，core 转发契约，application 负责组装参与者。头像不进入 capabilities、成员持久化快照或 legacy/WS DTO。Provider 正常注册后使用同一 existing-only 写入能力，手动修复的调用者只能使用 admin 资料更新。

**Tech Stack:** Rust workspace、async-trait、serde/serde_json、Tokio、现有 DB Plugin、SQLite/MySQL、Cargo/nextest；不新增依赖。

**Spec:** [design.md](design.md)，尤其 §4–5、§8–11。Backend 独立计划见 [backend-plan.md](backend-plan.md)，本计划不修改 Backend。

## Global Constraints

- “BCS 对应 Bot 不存在时返回明确错误，不自动注册、不创建、不恢复已删除 Bot。”修复使用的更新路径必须满足此约束，正常注册路径仍可创建。
- `bcs_bots.avatar_url TEXT NULL`，不建索引、不存入 JSON，不改已提交 migration。
- 未提供头像保持原值；`""` 清空；字符串设置；显式 JSON null/非字符串拒绝。
- 只有 V1 Group/Session 投影增加头像；无头像输出 null，Human 输出 null。
- list 返回全部轻量参与者，不静默截断；单次 repo 头像批次最多 256 个不同 Bot ID。
- 新列表 `participants[].name` 原样读取本 Group/Session 成员 `bot_name` 快照（可为 null），不新增名称查询；既有 driver/peer 与详情名称补齐行为不变。
- 不新增重试、补偿、扫描、头像缓存、跨服务事务或外部 URL 探测。
- delivery → application → core → repo → DB Plugin；不让 application 直接访问 SQL/store。
- 不运行 `cargo fmt` 或全局格式化；每个新增/修改源码文件 ≤1,000 行，不扩大尺寸豁免。
- 以下命令从仓库根运行；所有复选框是未来执行步骤，不是已通过证据。计划基线为 `a9b62a1f2`。

---

## 0. 执行准备与文件地图

- [ ] 读取根 `AGENTS.md`、`CONTEXT-MAP.md`、`apps/bcs/AGENTS.md`、`apps/bcs/CLAUDE.md`，design 引用的四份 arch 文档，以及下列 crate 的 `CONTEXT.md`：`bcs-bot-store`、`bcs-bot`、`bcs-app-group`、`bcs-app-session`、`bcs-service-api`。
- [ ] 检查工作区，保留用户改动；若使用独立 worktree，执行时按 using-git-worktrees 技能检查已有工作区后再决定，不在本计划编写阶段创建。

```bash
git status --short --branch
git merge-base --is-ancestor a9b62a1f2 HEAD
bash devops/hook/install_git_hooks.sh
export AVATAR_BCS_BASE="$(git rev-parse HEAD)"
ct() { cargo test --manifest-path apps/bcs/Cargo.toml "$@"; }
```

- [ ] 保存最近基线回归；依赖安装/编译环境失败不是有效 RED：

```bash
ct -p bcs-bot-store --test conformance_bot_repo
ct -p bcs-bot --lib
ct -p bcs-app-group --test driver_metadata
ct -p bcs-app-session --test v1_session_service
```

主要新增文件均有确定责任：

| 路径（前缀 `apps/bcs/`） | 责任 |
| --- | --- |
| `migrations/sqlite/040_bot_avatar.sql`、`migrations/mysql/040_bot_avatar.sql` | 新列；基线两方言最大版本均为 039 |
| `crates/service-api/bcs-service-api/src/types/bot_profile.rs` | AvatarChange、ExistingProfileUpdate、写结果、查询 map |
| `crates/contracts/bcs-protocol/src/http/avatar.rs` | 区分 omitted/string/null 的 wire 字段 |
| `crates/services/bcs-bot-store/src/persistent_repo/profile.rs` | SQL existing-only 写与批量读 |
| `crates/services/bcs-bot-store/src/memory_profile.rs` | memory/file 等价语义，不另建缓存 |
| `crates/application/v1/bcs-app-group/src/avatar.rs` | Group 当前结果集批量补齐 |
| `crates/application/v1/bcs-app-session/src/avatar.rs` | Session 当前结果集批量补齐 |
| `crates/bootstrap/bcs/tests/bot_avatar_migration.rs` | 新库、升级和重复执行验证 |
| `crates/services/bcs-bot-store/tests/conformance_bot_profile.rs` | 两种 store 的同一行为测试 |
| `crates/services/bcs-bot-store/tests/bot_avatar_queries.rs` | query count、失败与隔离 |
| `crates/adapters/http/bcs-http/tests/avatar_write_contract.rs` | 正常注册/admin 输入兼容 |

迁移编号不是占位符：本基线使用 040；若执行分支已有 040，先更新本计划与新文件名为各自下一空闲版本，不能覆盖任何既有版本。其他新增文件在各 task 中列出。

## Task 1: 为本次变更拆出必要的超长文件责任

**Files:**
- Modify: `apps/bcs/crates/services/bcs-bot/src/application/onboarding.rs`
- Create: `apps/bcs/crates/services/bcs-bot/src/application/onboarding_tests.rs`
- Modify: `apps/bcs/crates/services/bcs-bot-store/src/persistent_repo/mod.rs`
- Create: `apps/bcs/crates/services/bcs-bot-store/src/persistent_repo/registry_storage.rs`
- Modify: `apps/bcs/crates/services/bcs-bot-store/src/persistent_repo/bot_repo_port.rs`
- Create: `apps/bcs/crates/services/bcs-bot-store/src/persistent_repo/runtime_ops.rs`, `apps/bcs/crates/services/bcs-bot-store/src/persistent_repo/actor_ops.rs`
- Modify: `apps/bcs/crates/services/bcs-bot-store/src/memory_bot_repo_port.rs`, `apps/bcs/crates/services/bcs-bot-store/src/memory.rs`
- Create: `apps/bcs/crates/services/bcs-bot-store/src/memory_runtime_ops.rs`, `apps/bcs/crates/services/bcs-bot-store/src/memory_identity_ops.rs`
- Modify: `apps/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-session/src/projections.rs`, `apps/bcs/crates/application/v1/bcs-app-session/src/tests.rs`

**Interfaces:** Consumes/Produces 均为现有公开 trait 和构造器，签名、序列化、权限、锁顺序不变。新 helper 是同一 store 的私有 inherent 方法，不能在多个文件中重复实现同一 trait。

- [ ] **RED：执行目标文件尺寸断言，确认现有超限。**

```bash
python - <<'PY'
from pathlib import Path
paths = [
'apps/bcs/crates/services/bcs-bot/src/application/onboarding.rs',
'apps/bcs/crates/services/bcs-bot-store/src/persistent_repo/mod.rs',
'apps/bcs/crates/services/bcs-bot-store/src/persistent_repo/bot_repo_port.rs',
'apps/bcs/crates/services/bcs-bot-store/src/memory_bot_repo_port.rs',
'apps/bcs/crates/application/v1/bcs-app-session/src/lib.rs',
]
assert all(len(Path(p).read_text().splitlines()) <= 1000 for p in paths)
PY
```

预期 AssertionError。先记录 §0 回归结果，再开始机械移动。

- [ ] 移动 onboarding 的 `#[cfg(test)] mod tests` 到子文件；若 tests 文件仍 >1,000 行，按名称合并/owner binding 两组拆成 `onboarding_tests/names.rs`、`onboarding_tests/ownership.rs`，父文件仅保留共用 doubles/fixture。使用 `#[path] mod`，不删除测试。
- [ ] 将 persistent `save_to_db`、`try_load_from_db`、`decode_registry_row`、`try_load_many_from_db`、`load_from_db` 移至 `registry_storage.rs` 的同类型 inherent impl；保留必要的 `pub(super)`，不改 SQL。
- [ ] 将 persistent trait 实现的 `connect_or_promote_streaming` 至 `resolve_pending_request` 方法体提取为 `*_impl` 到 `runtime_ops.rs`；将 `ensure_human_actor`、`list_legacy_bots_for_owner`、`update_human_name` 方法体提取到 `actor_ops.rs`。原 trait impl 保留显式委托，例如：

```rust
async fn update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
    self.update_human_name_impl(staff_no, new_name).await
}
```

- [ ] 对 memory 同样提取 `connect_or_promote_streaming` 至 `resolve_pending_request` 为 `memory_runtime_ops.rs`，`register`、`register_with_owner_and_token`、`save_token`、`find_bot_by_token` 为 `memory_identity_ops.rs`；用原签名的 `*_impl` 委托，所有锁、文件写入和错误返回原样移动。
- [ ] 将 Session `project_participant`、`project_status`、`map_status_to_domain`、`project_summary` 移至 `projections.rs`；inline tests 移至 `tests.rs`。模块用 `use super::*` 访问必要内部项，production helper 用 `pub(super)`。
- [ ] **GREEN：重跑 §0 四条命令、尺寸断言及 `cargo check --manifest-path apps/bcs/Cargo.toml --workspace --all-targets`。** 预期全通过；若发现新增 helper 文件超限，按同责任的连接/请求或身份/凭证继续拆，不引入 include! 拼接 trait。
- [ ] Commit：`git add` 本 task 明确列出的改动后 `git commit -m "refactor(bcs): isolate bot persistence and session projections"`。不得顺带格式化其他文件。

## Task 2: 增量迁移及完整方言验证

**Files:**
- Create: 两个 `040_bot_avatar.sql`、`apps/bcs/crates/bootstrap/bcs/tests/bot_avatar_migration.rs`
- Modify: `apps/bcs/crates/bootstrap/bcs/src/migrations.rs`
- Modify: `apps/bcs/crates/tools/bcs-admin/src/migrate_mysql_chain_tests.rs`
- Modify: `apps/bcs/crates/bootstrap/bcs/tests/provider_slug_migration.rs` 及检索到的精确版本计数断言

**Interfaces:** Consumes 当前 migration runner；Produces `avatar_url TEXT NULL`，旧行 NULL、重复执行不改历史。

- [ ] **RED：新增完整链路迁移测试，核心断言如下。**

```rust
use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn avatar_column_is_nullable_and_history_is_stable() {
    let db = LocalSqliteDbPlugin::new().unwrap();
    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
    let columns = db.query(DbStatement::new("PRAGMA table_info(bcs_bots)"))
        .await.unwrap();
    let avatar = columns.iter().find(|r|
        r.get("name") == Some(&DbValue::String("avatar_url".into()))).unwrap();
    assert_eq!(avatar.get("notnull"), Some(&DbValue::I64(0)));
    let statement = || DbStatement::new(
        "SELECT version,name,checksum FROM bcs_schema_migrations ORDER BY version");
    let before = db.query(statement()).await.unwrap();
    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
    assert_eq!(db.query(statement()).await.unwrap(), before);
}
```

`DbValue::I64` 是当前 DB Plugin 的有符号整数 variant；保留 nullable 断言。

- [ ] 运行 `ct -p bcs --test bot_avatar_migration`。预期找不到 avatar 列而失败。
- [ ] 两个 migration 写入相同 SQL，并在 SQLite version list 与 runner 增加 040：

```sql
ALTER TABLE bcs_bots ADD COLUMN avatar_url TEXT NULL;
```

- [ ] 增加“运行到旧版本、插入旧行、升级后旧行名称/描述/凭证/Owner 未变且头像 NULL”的测试。沿用 runner 现有单版本测试方式，禁止复制或修改历史生产 DDL作为新 schema。
- [ ] 更新精确历史数量断言为新版本，并将 avatar 列检查加入 `full_mysql_migration_chain_applies_and_preserves_history` 既有真实 MySQL链路；静态 check-files 不能替代此测试。
- [ ] **GREEN：**

```bash
ct -p bcs --test bot_avatar_migration --test provider_slug_migration
ct -p bcs-admin mysql_syntax_revisions_preserve_exact_archives_and_schema
: "${BCS_TEST_MYSQL_URL:?Set an empty disposable local MySQL database URL}"
ct -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored
```

最后一条必须实际运行；无可用 MySQL 时记录未验证，不能标记 task 完成。只用可丢弃数据库，不输出凭证。
- [ ] Commit：`feat(bcs): persist bot avatars in a dedicated column`，只暂存本 task 新迁移、runner 和测试。

## Task 3: Store existing-only 资料更新与头像读取

**Files:**
- Create: `crates/service-api/bcs-service-api/src/types/bot_profile.rs`
- Modify: 同 crate `src/types/mod.rs`、`src/port/repo/bot.rs`
- Create: `crates/services/bcs-bot-store/src/persistent_repo/profile.rs`、`src/memory_profile.rs`
- Modify: 同 store `src/persistent_repo/mod.rs`、`src/persistent_repo/bot_repo_port.rs`、`src/memory.rs`、`src/memory_bot_repo_port.rs`
- Create: 同 store `tests/conformance_bot_profile.rs`、`tests/support/bot_profile_fixture.rs`、`tests/support/bot_profile_contract.rs`
- Modify: `apps/bcs/crates/tools/bcs-admin/src/migrate_mysql_chain_tests.rs`（挂接同一 store 行为合同）

以上路径均以 `apps/bcs/` 开头。

**Interfaces:** 新值放在 `types::bot_profile`，不加到 legacy 可序列化 `BotCapabilities`：

```rust
use std::collections::HashMap;
use crate::BotCapabilities;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum AvatarChange { #[default] Keep, Clear, Set(String) }
#[derive(Debug, Clone)]
pub struct ExistingProfileUpdate {
    pub capabilities: BotCapabilities,
    pub avatar: AvatarChange,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProfileWrite { Updated, Missing }
pub type BotAvatarMap = HashMap<String, Option<String>>;
```

在 `BotRepoPort` 增加：

```rust
async fn update_profile_existing(
    &self, bot_id: &str, update: ExistingProfileUpdate,
) -> ServiceResult<ProfileWrite>;
async fn update_avatar_existing(
    &self, bot_id: &str, avatar: AvatarChange,
) -> ServiceResult<ProfileWrite>;
async fn read_avatar_batch(&self, bot_ids: &[String]) -> ServiceResult<BotAvatarMap>;
```

老 test double 需要默认实现时，默认显式返回“未配置”错误，不能默认成功；真实 store 必须实现。`read_avatar_batch` 输入去重后 ≤256，支持 tombstone 展示、排除 Human、沿用所在 repository 的当前环境/租户隔离。

- [ ] **RED：** 在 `tests/support/bot_profile_contract.rs` 编写共用函数，`conformance_bot_profile.rs` 引入后让 Memory 和真实 SQLite 各运行一次：

```rust
use bcs_service_api::{BotRepoPort, BotCapabilities};
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};

pub async fn avatar_contract(repo: &dyn BotRepoPort) {
    assert_eq!(repo.update_avatar_existing("missing", AvatarChange::Clear)
        .await.unwrap(), ProfileWrite::Missing);
    assert!(repo.create_registration_if_absent("avatar-bot".into(),
        BotCapabilities::default(), "owner", "test-runtime-token")
        .await.unwrap());
    let ids = vec!["avatar-bot".to_string()];
    for (change, expected) in [
        (AvatarChange::Set("https://example.com/avatar.png".into()),
         Some("https://example.com/avatar.png".to_string())),
        (AvatarChange::Keep, Some("https://example.com/avatar.png".to_string())),
        (AvatarChange::Clear, None),
    ] {
        assert_eq!(repo.update_avatar_existing("avatar-bot", change)
            .await.unwrap(), ProfileWrite::Updated);
        assert_eq!(repo.read_avatar_batch(&ids).await.unwrap()["avatar-bot"], expected);
    }
}
```

测试 fixture 使用真实 migration 链，不调用 `tests/common/bot_provider.rs::sqlite()`（它只建立部分测试 schema）。在两个新 root test 文件分别挂载下面的模块；`tests/support/bot_profile_fixture.rs` 的 `sqlite_repo()` 用 `super::bootstrap_migrations` 初始化 DB，并返回 `(Arc<LocalSqliteDbPlugin>, PersistentBotRepo)`。memory 使用 tempfile 和 `MemoryBotRepo::with_base_dir`。

```rust
#[path = "../../../bootstrap/bcs/src/migrations.rs"]
mod bootstrap_migrations;
#[path = "support/bot_profile_fixture.rs"]
mod fixture;
#[path = "support/bot_profile_contract.rs"]
mod profile_contract;
use profile_contract::avatar_contract;
```

```rust
pub async fn sqlite_repo() -> (std::sync::Arc<bcs_db_local::LocalSqliteDbPlugin>, bcs_bot_store::PersistentBotRepo) {
    let db = std::sync::Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    super::bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let repo = bcs_bot_store::PersistentBotRepo::with_sql_flavor(
        db.clone(), bcs_db_api::DbSqlFlavor::Sqlite);
    (db, repo)
}
```

- [ ] 将共用合同挂入两个真实测试，然后运行 `ct -p bcs-bot-store --test conformance_bot_profile`。预期缺少新类型/方法的编译失败；依赖缺失不计 RED。

```rust
#[tokio::test]
async fn memory_avatar_contract() {
    let dir = tempfile::tempdir().unwrap();
    let repo = bcs_bot_store::MemoryBotRepo::with_base_dir(dir.path().into());
    avatar_contract(&repo).await;
}
#[tokio::test]
async fn sqlite_avatar_contract() {
    let (_db, repo) = fixture::sqlite_repo().await;
    avatar_contract(&repo).await;
}
```
- [ ] SQL 更新用 `UPDATE ... WHERE bot_uuid=? AND env=? AND COALESCE(is_deleted,0)=0`，不 INSERT、不 upsert。更新资料与头像在同一 DB 事务/语句边界，复用既有 capabilities 到列的映射，Keep 不出现在 SET 子句中。
- [ ] 处理 MySQL unchanged update 的 affected_rows=0：在同一短事务下先锁定/确认目标活记录，或采用 DB 插件支持的行匹配确认，不能把“已是目标值”错判 Missing；读取或提交失败继续返回错误。
- [ ] 头像批量读只选择 ID、avatar_url 及必要隔离字段，以参数化 IN 查询；SQL 方言共用 DB Plugin，空输入不查询，输入超限返回 InvalidOperation。不改全局 `env` 规则，不新增裸环境读取。
- [ ] Memory 将 avatar 放入同一 Bot 持久化记录的独立字段；文件快照可用 `StoredBotProfile` 包装 capabilities 并兼容旧文件缺字段。**不要**把它加进 BotCapabilities，也不要另建头像 cache。先完成可失败的文件写入，再发布内存值；普通存储写入保留此字段。
- [ ] 对资料补齐、重复写、软删除、并发删除、存储错误增加断言：Missing 目标没有新增行；profile 更新失败名称/头像都不成功；已有 token/owner 不变；memory 重启仍有头像，旧文件无头像可读。
- [ ] **GREEN：** `ct -p bcs-bot-store --test conformance_bot_profile --test conformance_bot_repo --test registration_create --test memory_agent_restart`。在 `migrate_mysql_chain_tests.rs` 挂接共用合同，并用完成完整迁移的 DB 创建 MySQL repo；运行 §2 ignored MySQL 测试，不能只测 SQLite。

```rust
#[path = "../../../services/bcs-bot-store/tests/support/bot_profile_contract.rs"]
mod avatar_store_contract;
```

```rust
let repo = bcs_bot_store::PersistentBotRepo::with_sql_flavor(
    db.clone(), bcs_db_api::DbSqlFlavor::Mysql);
avatar_store_contract::avatar_contract(&repo).await;
```
- [ ] Commit：`feat(bcs): add existing-only bot profile persistence`。

## Task 4: 批量读取 Core 契约与查询预算

**Files:**
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/core/registry.rs`
- Modify: `apps/bcs/crates/services/bcs-bot/src/core/bot_core.rs`
- Create: `apps/bcs/crates/services/bcs-bot/src/core/bot_profile.rs`
- Create: `apps/bcs/crates/services/bcs-bot-store/tests/bot_avatar_queries.rs`
- Create: `apps/bcs/crates/services/bcs-bot/tests/bot_profile.rs`

**Interfaces:** `BotRegistryCoreService` 增加 `update_profile_existing`、`update_avatar_existing`（同 repo 签名）及 `avatar_urls(&self, bot_ids: &[String]) -> ServiceResult<BotAvatarMap>`。与 repo 不同，core 可接受整页 IDs，负责去重和 chunk；它不负责授权哪些资源可见。

- [ ] **RED：** 在 `apps/bcs/crates/services/bcs-bot/tests/bot_profile.rs` 复制改造 `apps/bcs/crates/services/bcs-bot-store/tests/conformance_agent_registration.rs` 中的 `CountReads` DB wrapper（具备 query/execute/transaction/health_check）。源文件的 `fixture()` 返回 `(Arc<LocalSqliteDbPlugin>, Arc<CountReads>, PersistentBotRepo)`，可复用该装配方式取得 count 并用 repo 构建 Core；源 fixture 的局部 schema 不照搬，仍使用 Task 3 的完整 migration 链。通过真实 DB 记录查询数与读取行数。构建 257 个不同已注册 Bot，输入再重复第一项；断言：

```rust
let ids = (0..257).map(|i| format!("avatar-{i}")).collect::<Vec<_>>();
let mut duplicated = ids.clone();
duplicated.push(ids[0].clone());
let before = count.queries.load(std::sync::atomic::Ordering::SeqCst);
let values = core.avatar_urls(&duplicated).await.unwrap();
assert_eq!(values.len(), 257);
assert_eq!(count.queries.load(std::sync::atomic::Ordering::SeqCst) - before, 2);
```

`core = BotCore::with_repo(Arc::new(PersistentBotRepo::with_sql_flavor(count.clone(), DbSqlFlavor::Sqlite)))`；seed 使用 Task 3 的真实 schema fixture 和注册方法。CountReads 的写方法转发 inner DB，不禁止 seed。Core 聚合测试放在 bcs-bot，不能让 bcs-bot-store 反向依赖 bcs-bot；store 的 `bot_avatar_queries.rs` 只测试单批 `read_avatar_batch`（256=1查询，257拒绝）。在 bcs-bot 测试中复用 fixture 的准确挂载为：

```rust
#[path = "../../../bootstrap/bcs/src/migrations.rs"]
mod bootstrap_migrations;
#[path = "../../bcs-bot-store/tests/support/bot_profile_fixture.rs"]
mod fixture;
```

- [ ] 运行 `ct -p bcs-bot --test bot_profile`，预期新 Core API 不存在；store 单批断言运行 `ct -p bcs-bot-store --test bot_avatar_queries`。
- [ ] Core 方法在新 `bot_profile.rs` inherent impl 中实现，trait impl 只委托，保持 `bot_core.rs` <1,000 行：

```rust
let unique = bot_ids.iter().cloned().collect::<std::collections::BTreeSet<_>>();
let ids = unique.into_iter().collect::<Vec<_>>();
let mut output = BotAvatarMap::new();
for batch in ids.chunks(256) {
    output.extend(self.repo.read_avatar_batch(batch).await?);
}
Ok(output)
```

- [ ] 增加空输入=0查询、1/256/257、不同环境 ID、Human、tombstone、DB error、无敏感列投影的测试。确认仅当前结果页 IDs 被提交，没有全表读取。
- [ ] **GREEN：** `ct -p bcs-bot-store --test bot_avatar_queries` 与 `ct -p bcs-bot --test bot_profile`；记录最大批次和查询数，不把 existing name-backfill 的查询算成头像回归。
- [ ] Commit：`feat(bcs): batch participant avatar reads through core`。

## Task 5: 原始注册与 admin 更新入口接收头像

**Files:**
- Create: `apps/bcs/crates/contracts/bcs-protocol/src/http/avatar.rs`
- Modify: 同 protocol `src/http/mod.rs`、`src/http/onboard.rs`、`src/http/provider.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/bot_onboarding.rs`、`src/application/provider.rs`
- Modify: `apps/bcs/crates/adapters/http/bcs-http/src/routes/onboard.rs`、`src/routes/providers.rs`
- Modify: `apps/bcs/crates/services/bcs-bot/src/application/onboarding.rs`、`src/application/provider.rs`
- Create: `apps/bcs/crates/adapters/http/bcs-http/tests/avatar_write_contract.rs`

**Interfaces:** wire `AvatarField(Option<String>)` 的 None 只表示字段省略，presence 的反序列化只接受 String。delivery 将它转换成 Task 3 的 `AvatarChange`。只扩展 `AdminBotOnboardCommand`、`RegisterProviderBotCommand`，不为了头像改所有内部 Provider 注册协议。

- [ ] **RED：** protocol 测试断言 omitted/empty/value/null/number；用最小 wrapper 独立验证：

```rust
#[derive(serde::Deserialize)]
struct Input {
    #[serde(default)]
    avatar_url: AvatarField,
}
assert!(serde_json::from_str::<Input>(r#"{"avatar_url":null}"#).is_err());
assert!(serde_json::from_str::<Input>(r#"{"avatar_url":7}"#).is_err());
assert_eq!(serde_json::from_str::<Input>("{}").unwrap().avatar_url.0, None);
```

- [ ] 运行 `ct -p bcs-protocol avatar`，预期缺少 AvatarField。
- [ ] 写最小 wire 解析器；声明 serde Serialize 以保持已有 request DTO 的 derive 可编译，字段 omitted 时 `skip_serializing_if`：

```rust
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize)]
#[serde(transparent)]
pub struct AvatarField(pub Option<String>);
impl AvatarField { pub fn is_omitted(&self) -> bool { self.0.is_none() } }
impl<'de> serde::Deserialize<'de> for AvatarField {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        String::deserialize(d).map(|s| Self(Some(s)))
    }
}
```

导入 `serde::Deserialize`；`Some("") → Clear`，其他 Some → Set，None → Keep。转换在 adapter/application 边界，不让 service-api 依赖 bcs-protocol。

- [ ] admin 保留原有 merge_name/merge_summary、权限和响应。原 `save_capabilities` 分支改用 `update_profile_existing`，Missing 映射原 `onboarded=false`；不得提前写头像再用 upsert 保存 capabilities。
- [ ] Provider application 在现有注册及 ownership 初始化成功后、返回成功前调用 `update_avatar_existing`；Keep 不新增写入。显式头像必须成功持久化才返回成功；重复注册也应用显式字段，不发新凭证。初次注册后头像写失败可留下已注册 Bot，但必须返回错误，不回滚 Bot 或重试——这是既有非原子注册链路的失败边界。
- [ ] `providers.rs` 接近上限，若新增后超过 1,000，将该注册 handler 原样移入 `routes/providers/register.rs`，父路由 re-export；不要修改 `provider_core.rs` 的 1,671 行旧实现来传递仅 application 使用的字段。
- [ ] 新 route tests 从 `bcs-http/tests/route_contract.rs` 复用当前 fake-service/router 构建方式，分别断言 command.avatar、旧响应无 avatar、缺失不注册，以及失败返回非成功；不要往原大测试文件继续加测试。
- [ ] **GREEN：** `ct -p bcs-protocol avatar`、`ct -p bcs-http --test avatar_write_contract`、`ct -p bcs --test integration_onboarding`、`ct -p bcs-bot --lib`；新增 missing-after-precheck 回归须断言 DB 行数不增加。
- [ ] Commit：`feat(bcs): accept avatars on existing bot sync endpoints`。

## Task 6: V1 Group 头像与列表摘要

**Files:**
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-group/src/avatar.rs`
- Modify: 同 application `src/lib.rs`、`src/projections.rs`、`src/service.rs`
- Create: 同 application `tests/v1_group_service/avatars.rs`
- Modify: 同 application `tests/v1_group_service.rs`（只挂子测试模块，941 行基线需守上限）
- Modify: `apps/bcs/api-contracts/v1/domain-models.yaml`、`apps/bcs/api-contracts/v1/openapi/groups.yaml`

**Interfaces:** V1 新 `ParticipantSummary`，Group `Actor`/`Participant` 增加 `avatar_url: Option<String>`，正常/DM summary 均增加 `participants: Vec<ParticipantSummary>`。新增字段不 skip None/空 Vec。内部方法：`apply_group_avatars(groups: &mut [GroupSummary], avatars: &BotAvatarMap)` 和 detail 等价纯投影，不额外查库。

- [ ] **RED：** 在 `avatars.rs` 使用父 suite 的 `Fixture::new()`、已存在 create-group helpers，新增 `group_avatars_follow_current_profile`。向 fixture.bots 注册两个 Bot，使用 Task 4 写头像后创建普通 Group/DM；调用真实 `list_groups/get`，序列化结果断言：

```rust
fn assert_participant_shape(value: &serde_json::Value) {
    let row = &value["participants"][0];
    assert!(row.get("actor_id").is_some());
    assert!(row.get("actor_kind").is_some());
    assert!(row.get("name").is_some());
    assert!(row.get("avatar_url").is_some());
    assert!(row.get("message_view_scope").is_none());
}
```

`assert_participant_shape` 仅用于 list 摘要；detail 保留完整控制字段。接着清空头像，再次 get 应返回 null，不重新创建 Group。

- [ ] 在同一子测试文件增加 `group_list_names_are_snapshots`：分别设置成员 `bot_name=Some("snapshot-name")`、`None`，而 registry 始终有不同的 `"current-name"` 和可用头像。对普通 Group、DM 和公开列表的新 `participants` 调用以下断言，确保返回快照或 null；并单独保留既有 `driver_bot_name` / `peer_actor.name` 补齐断言：

```rust
fn assert_group_list_name_snapshot(
    value: &serde_json::Value, actor: &str, snapshot: Option<&str>,
) {
    let row = value["participants"].as_array().unwrap().iter()
        .find(|item| item["actor_id"] == actor).unwrap();
    let expected = snapshot.map(serde_json::Value::from)
        .unwrap_or(serde_json::Value::Null);
    assert_eq!(row["name"], expected);
}
```

- [ ] 运行 `ct -p bcs-app-group --test v1_group_service avatars`。预期字段缺失断言失败。
- [ ] 添加模型与现有构造器字段：

```rust
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ParticipantSummary {
    pub actor_id: String,
    pub actor_kind: ActorKind,
    pub name: Option<String>,
    pub avatar_url: Option<String>,
}
```

list 先取最终页→从原成员记录提取 `participants` 的身份与 `bot_name` 快照→收集全部 Bot IDs→一次调用 `registry.avatar_urls()`→对这一页投影。`participants[].name` 直接使用该快照，缺失时为 null；不能从 avatar map、registry 当前资料或补齐后的成员副本取名称。当前 `src/projections.rs::project_summary()` 已调用 `backfill_bot_names()`，保留该调用服务既有 driver/peer 名称，但新数组的名称必须在它之前提取。不能在逐 Group 循环内部各查一次头像，也不能为新数组增加任何名称查询。detail 在现有授权后取本 Group map，保留详情名称补齐。
- [ ] 为 DM `peer_actor` 赋相同 map 的头像；公开 Group 同一摘要也填充，但保留原公开可见性规则，尤其不能读出被现有成员过滤排除的 ID。
- [ ] 为 create/update/participant 共用模型统一填充；字段初始 null 不代表完成，要验证返回已有头像。registry double 分别记录 `avatar_urls` 与原有名称查询入口；用相同成员/driver/peer fixture 比较变更前后的名称读取次数，断言新摘要没有增加名称读取，重复 Bot 仍只进入去重后的头像批次。不要将本来用于 driver/peer 的名称查询错误断言为零。
- [ ] 更新 OpenAPI required/nullable 定义，列出 Human、missing、tombstone、DM、公开群、session-only 可读群、不授权 caller、DB read failure 的测试。
- [ ] **GREEN：** `ct -p bcs-app-group --test v1_group_service --test driver_metadata --test session_group_detail --test v1_group_participant`。所有原排序、count、driver 断言保持。
- [ ] Commit：`feat(bcs): expose participant avatars in v1 group views`。

## Task 7: V1 Session 头像与会话独有参与者

**Files:**
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-session/src/avatar.rs`
- Modify: 同 application `src/lib.rs`、`src/projections.rs`、`src/authorization.rs`、`src/participant.rs`
- Create: 同 application `tests/v1_session_service/avatars.rs`
- Modify: 同 application `tests/v1_session_service.rs`（仅挂子模块）
- Modify: `apps/bcs/api-contracts/v1/domain-models.yaml`、`apps/bcs/api-contracts/v1/openapi/sessions.yaml`

**Interfaces:** `SessionSummary.participants: Vec<ParticipantSummary>` 复用 Task 6 类型；`SessionParticipant.avatar_url: Option<String>`。`project_summary` 与 `project_participant` 接受已查询的 avatar map，不自己进行 I/O。

- [ ] **RED：** 使用父 suite `Fixture::new()` 和 `setup_manager_worker_session(&fixture)` 建 session，将仅存在于 session 的 Bot 加入。使用 Task 4 Core 写入各 Bot 不同 URL；真实 list/get 必须包含新增 Bot 的头像，不包含只在 Group 而已离开 Session 的成员。

```rust
fn assert_session_avatar(value: &serde_json::Value, actor: &str, expected: &str) {
    let row = value["participants"].as_array().unwrap().iter()
        .find(|item| item["actor_id"] == actor).unwrap();
    assert_eq!(row["avatar_url"], expected);
}
```

此函数调用于已注册 Bot 有头像场景；空头像测试直接断言 `is_null()`。
- [ ] 增加 `session_list_names_are_snapshots`：Session 成员名称快照分别为 `Some("session-snapshot")` 和 `None`，registry 名称不同且有头像；调用真实 list 后验证下列断言。名称缺失时也能返回头像，但不得额外调用名称补齐；详情原有名称补齐另行保持：

```rust
fn assert_session_list_name_snapshot(
    value: &serde_json::Value, actor: &str, snapshot: Option<&str>,
) {
    let row = value["participants"].as_array().unwrap().iter()
        .find(|item| item["actor_id"] == actor).unwrap();
    let expected = snapshot.map(serde_json::Value::from)
        .unwrap_or(serde_json::Value::Null);
    assert_eq!(row["name"], expected);
}
```
- [ ] 运行 `ct -p bcs-app-session --test v1_session_service avatars`，预期缺字段失败。
- [ ] 在 list 最终分页后收集 Session 自身成员 ID，调用一次 avatar_urls；新摘要 `name` 直接 clone 当前 Session 成员记录的 `bot_name`（含 None），不为 list 调用 `backfill_participant_names` 或额外 registry 名称读取。detail/成员 mutation 返回时使用相同头像填充 helper，但保留各自原有名称补齐。不要沿用 Group 名称或成员快照，也不要根据 `needs_bot_name_backfill` 决定是否查询头像。
- [ ] 测试同一页共享 Bot、修改/清空后旧会话、Human、missing/tombstone、scope 拒绝、查询异常、创建/更新/成员操作复用响应。分开计数既有授权读取、名称读取与 avatar_urls，断言为新 list 摘要增加的名称读取为零，唯一新增 DB 读为 `ceil(U / 256)` 以内的头像批次。用失败 registry 断言不返回一页全 null 的成功响应。
- [ ] 更新 Session OpenAPI，保留排序、分页、collected、消息权限、state-machine 字段。
- [ ] **GREEN：** `ct -p bcs-app-session --test v1_session_service` 和 `ct -p bcs-app-session --lib`。
- [ ] Commit：`feat(bcs): expose participant avatars in v1 session views`。

## Task 8: HTTP 兼容、完整门禁与交付证据

**Files:**
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/tests/avatar_response_contract.rs`
- Modify: `apps/bcs/crates/services/bcs-bot-store/CONTEXT.md`、`apps/bcs/crates/application/v1/bcs-app-group/CONTEXT.md`、`apps/bcs/crates/application/v1/bcs-app-session/CONTEXT.md`
- Create: `docs/specs/2026-11-02-bot-avatar-collaboration-repair/bcs-validation.md`

**Interfaces:** 完整实现本设计 BCS 侧输入与输出，可由模拟 Backend 请求独立验证；不依赖 Backend repair 路由上线。

- [ ] **RED：** 编写 HTTP 序列化回归，通过新旧 route fixture 分别取得 JSON；断言 V1 participant 有 avatar_url、legacy participant 与 WS 帧没有该字段。同一 Bot 不可通过把 RegisteredBot 加 serde 字段“顺便”扩展旧接口。

```rust
fn assert_legacy_participants_unchanged(value: &serde_json::Value) {
    for participant in value["participants"].as_array().unwrap() {
        assert!(participant.get("avatar_url").is_none());
    }
}
```

- [ ] 运行 `ct -p bcs-api-http --test avatar_response_contract`，补齐 HTTP/application 映射缺口，重复直到 GREEN；同时运行 `ct -p bcs-http --test avatar_write_contract`。
- [ ] 运行全部相关 crate 与 workspace 构建；保存命令、退出码、断言数量及失败原因：

```bash
cargo check --manifest-path apps/bcs/Cargo.toml --workspace --all-targets
ct -p bcs-protocol -p bcs-bot-store -p bcs-bot -p bcs-app-group -p bcs-app-session -p bcs-http -p bcs-api-http
bash apps/bcs/scripts/ci_test.sh --fast-fail --base "$AVATAR_BCS_BASE" --head HEAD
bash singlebox/ci/singlebox_coverage.sh
python singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

verifier 必须验证同一次 runner 生成的 reports 目录。完整 coverage 要在本模块验收测试已注册到既有 suite 后运行，不排除生产 denominator。

- [ ] 在独立真实 BCS 栈上：正常注册→admin 更新头像→V1 group/session 读回→清空→再次读回；不存在 Bot 的 admin 更新不得增加行数。记录真实 MySQL 链路结果；环境不可用写明未验证，不宣称完成。
- [ ] 检查每个新增/修改源码行数、`git diff --check`、历史 migration 未变；所有 fixture/model 构造点由 `--all-targets` 覆盖，不允许遗漏编译目标。
- [ ] 在 `bcs-validation.md` 写当前提交、实测结果、查询成本、schema 版本和已知限制，并向 Backend 交付：字段规则、existing-only missing 行为、已部署版本、测试请求。
- [ ] Commit：`test(bcs): verify avatar contracts and migration compatibility`。不推送、不创建 PR，除非用户另行要求。

## 自检与覆盖映射

| Design 要求 | Task |
| --- | --- |
| 独立列、历史迁移冻结、MySQL | 2–3、8 |
| 省略/清空/null、旧写保持、存储错误 | 3、5 |
| missing 与删除竞态不创建 | 3、5、8 |
| V1 list/get、DM、共用响应、legacy 不变 | 6–8 |
| 去重、256 批次、查询成本、隔离 | 4、6–7 |
| 列表名称来自成员快照/null，不新增名称查询，保留既有名称补齐 | 6–7 |
| 内存/文件、缓存保留、重启 | 3 |
| 无新重试/后台扫描/跨模块改动 | Global Constraints、3、5 |
| 两模块独立验收与部署交接 | 8 |

计划编写时只进行路径、文档与接口一致性自检，没有执行上述测试或实施任何代码。

## 计划编写验证

已核对 design 覆盖、跨任务类型/方法名、现有文件与新增文件归属、migration 基线和真实测试入口；本地相对链接、代码围栏、Python/JSON/Bash 示例语法和空白检查通过。未运行 Rust 编译、数据库迁移或实施测试；不能把文档示例当作已验证实现。
