# BCS V1 API Auth Plugin Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按独立 `[api.auth]` 配置装配 V1 Gateway/OAuth 认证来源，并兑现浏览器绑定、共享会话并发安全、数据库错误传播和实际来源 CSRF 四项审阅要求。

**Architecture:** 复用 V1 verifier 注入边界，在 delivery 中保留实际成功来源，业务层仍只接收 `AuthenticatedCaller`。配置和具体插件在 bootstrap 装配；严格会话查询/原子更新从 Repository 开始落实。将现有 OAuth 用例从 HTTP adapter 中移到 application facade，新旧 HTTP 入口共用安全生命周期，但不共用配置选择。

**Tech Stack:** Rust workspace（当前 edition 2024、rust-version 1.91）、Axum、Tokio、Serde/TOML、现有 OAuthProvider/Auth Plugin API、SQLite/MySQL DbPlugin、现有 JWT/Secret 服务。

**Spec:** [2026-09-18-v1-api-auth-plugin-chain-design.md](../specs/2026-09-18-v1-api-auth-plugin-chain-design.md)，包括 2026-09-18 四项审阅整改。

## Global Constraints

- 日期：2026-09-18；状态：实施计划待执行，所有任务尚未完成；本文件不代表代码或测试已经通过。
- 执行前读根 AGENTS.md、BCS AGENTS.md/CLAUDE.md、相关 CONTEXT.md 和架构宪法；不得运行 cargo fmt 或全局 formatter。
- “新链严格按配置顺序执行，不使用旧 `AuthPlugin::priority()`”。
- “第一个成功之后不再验证低优先级来源”；已匹配凭据失败不回退，Origin 拒绝也不回退。
- “`AuthenticatedCaller` 仍是业务身份，不增加来源或 Cookie 字段”。来源属于 delivery 契约。
- “缺少整个 `[api.auth]`”保留 Gateway-only 业务入口和旧 provider 选择；旧 OAuth 安全原语必须同步修复。
- “未经修复的新旧二进制不能混合写入同一存储”。不要把中间任务完成的代码部署成可接流量版本。
- 本计划执行 spec 的推荐范围：gateway + alipay/github/google/wechat；不实现 Bot-token/Local-mock 的新链注册，不实现内部 cookie/agentpass。产品若改成独立会话存储或扩充插件范围，先修订 spec，再执行受影响任务。
- 采用共享身份表/单活跃会话的推荐方案，允许历史会话失效并重登；保留新旧 Cookie 覆盖的已声明可用性限制。
- 不增加动态插件加载、热更新或 roles/scopes 聚合；不引入内部 SDK、私有 endpoint 或明文密钥。
- 每个新增或修改源文件不得超过 1,000 行；不得扩大 allowlist。先拆分必改大文件，再写功能，拆分只改组织结构不改行为。
- 每个任务执行 RED → GREEN → 回归 → review；文内 Rust 片段是最小测试/实现切片，不是可替代全部验收的伪成功实现。
- 路径除专门标注外均相对于 `src/bcs/`；Cargo/BCS 脚本在该目录执行，根级 Singlebox 命令另注明 cwd。

---

## 0. 执行前确认与依赖顺序

这是同一个认证边界的纵向改造，不拆成彼此独立上线的配置、存储和 OAuth 三套功能。提交可以细分，发布必须经过 Task 15 的共享写入方门槛。

```text
1 文件边界准备
 ├─ 2 严格会话契约 → 3 内存实现 → 4 SQL/迁移 → 5 JWT/端口桥接
 ├─ 6 配置/schema registry
 └─ 7 delivery 结果/CSRF
5 → 8 OAuth 严格查询/生命周期
6+8 → 9 浏览器绑定 state / application 契约
9 → 10 application facade
7+10 → 11 新旧 HTTP 同步迁移
6+7+8+10+11 → 12 public bootstrap 装配/下游扩展
12 → 13 DB 故障与并发 HTTP 集成
12+13 → 14 契约文档/配置样例/架构门禁
14 → 15 发布演练、全套验证与交付
```

所有任务顺序执行即可；此计划不授权自动启动子代理。若选择并行执行，只能在接口提交后拆分不重叠写集，bootstrap/public exports 仍由同一执行者整合。

执行前记录 `git status --short` 和 HEAD，保留用户未提交修改。spec 当前可能尚未纳入 Git，提交计划或后续代码时只显式添加本任务路径，不使用 `git add .`。

## 1. 文件结构与职责图

### 必须先处理的大文件（本次静态盘点）

| 原文件 | 当前行数 | Task 1 的拆分目标 |
| --- | ---: | --- |
| `crates/bootstrap/bcs/src/server.rs` | 6755 | server 门面、state、分段 bootstrap、router/lifecycle、协议入口和独立测试模块 |
| `crates/bootstrap/bcs/src/config.rs` | 3296 | 主 BcsConfig、分组配置 DTO、defaults、load/validate、分组测试 |
| `crates/bootstrap/bcs/src/migrations.rs` | 3169 | runner、DDL 分组、版本 registry、repair、测试 |
| `crates/adapters/http/bcs-http/src/oauth/mod.rs` | 1114 | routes/state、原 facade、独立测试；Task 11 删除原 facade 的业务实现 |
| `crates/service-api/bcs-config-api/src/lib.rs` | 985 | 新字段放 api_auth.rs，旧 Auth 配置搬到 auth.rs 保留 re-export |

`config_loader.rs` 当前 1045 行，本计划通过已有加载后的验证入口接入 registry，不修改其深合并/环境展开逻辑；如执行中必须改此文件，先拆出其测试保持原导出再改。不得借此重构无关配置行为。

### 新增文件的唯一责任

| 路径 | 责任 / 所属任务 |
| --- | --- |
| `crates/service-api/bcs-service-api/src/port/repo/auth_session.rs` | 严格会话 Repository、scope、snapshot 和错误 / 2 |
| `crates/services/bcs-user-identity/src/session_memory.rs`、`session_db.rs` | 同一身份记录上的会话操作，不建第二套账号 / 3、4 |
| `crates/plugin-api/bcs-auth-api/src/session.rs` | auth 侧严格身份会话契约，不依赖 service-api / 5 |
| `crates/services/bcs-jwt/src/oauth_session.rs` | 新 session JWT claims/sign/verify，不扩大旧泛用 Claims 的 Optional 字段 / 5 |
| `crates/service-api/bcs-config-api/src/api_auth.rs` | 公共配置结构及未知 key 初步分类 / 6 |
| `crates/bootstrap/bcs/src/api_auth_registry.rs` | schema/factory 注册、两阶段发现/装配 / 6、12 |
| `crates/adapters/http/bcs-api-http/src/v1/common/authentication.rs`、`csrf.rs`、`composite_principal.rs` | 实际来源结果、Origin 检查、有序执行 / 7 |
| `crates/plugins/bcs-auth-oauth/src/strict.rs`、`session_lifecycle.rs` | 严格会话验证、issue/rotate/revoke；不创建具体 DB / 8 |
| `crates/plugin-api/bcs-auth-api/src/login_state.rs`、`crates/plugins/bcs-auth-oauth/src/login_state.rs` | pending state/batch 契约与进程内原子实现 / 9 |
| `crates/service-api/bcs-service-api/src/port/oauth.rs` | application 消费的 provider/session/pending-login 业务端口 / 9、10 |
| `crates/application/v1/bcs-app-auth/` | 新旧共享的 AuthService 用例编排 / 10 |
| `crates/bootstrap/bcs/src/api_auth_ports.rs`、`api_auth_wiring.rs` | Service port↔Plugin API 类型桥接、具体装配，不复制用例策略 / 10、12 |
| `crates/bootstrap/bcs/tests/api_auth_http.rs`、`api_auth_races.rs`、`api_auth_config.rs` | 跨真实 store/bridge/router 的消费者验证 / 12、13 |
| `api-contracts/v1/authentication/contract.md` | 来源、Cookie、state、会话和发布契约 / 14 |

现有 `bcs-auth-alipay/github/google/wechat` 仍只实现 OAuthProvider；不复制成四套 JWT verifier。插件参数 schema 可在 bootstrap provider descriptor 中使用现有 provider config 类型，但不得放入 HTTP handler。

## 2. 任务之间的固定接口词汇

这些名称在下面任务中保持一致；新增类型只有在对应任务完成时才存在。

### Repository 侧（Task 2，service-api）

```rust
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthSessionScope {
    pub user_id: String,
    pub provider: String,
    pub env: String,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthSessionVersion {
    pub session_id: String,
    pub revision: u64,
    pub token_hash: String,
}
#[derive(Clone, Debug)]
pub struct AuthSessionSnapshot {
    pub scope: AuthSessionScope,
    pub version: AuthSessionVersion,
    pub expires_at: u64,
    pub username: String,
    pub display_name: Option<String>,
    pub avatar: Option<String>,
}
#[derive(Clone, Debug)]
pub struct InstallAuthSession {
    pub scope: AuthSessionScope,
    pub expected_revision: u64,
    pub next: AuthSessionVersion,
    pub expires_at: u64,
}
#[derive(Clone, Debug)]
pub struct RotateAuthSession {
    pub scope: AuthSessionScope,
    pub expected: AuthSessionVersion,
    pub next: AuthSessionVersion,
    pub expires_at: u64,
    pub now: u64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AuthSessionWrite { Applied, Conflict }
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AuthSessionRevoke { Revoked, NotCurrent }
#[derive(Debug, thiserror::Error)]
pub enum AuthSessionStoreError {
    #[error("identity store unavailable")]
    Unavailable,
    #[error("invalid persisted identity")]
    CorruptRecord,
}
```

`AuthSessionRepoPort` 方法：

```rust
async fn read_session_revision(&self, scope: &AuthSessionScope)
    -> Result<u64, AuthSessionStoreError>;
async fn get_session_by_hash(&self, scope: &AuthSessionScope, hash: &str, now: u64)
    -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError>;
async fn install_login_session(&self, command: InstallAuthSession)
    -> Result<AuthSessionWrite, AuthSessionStoreError>;
async fn rotate_session(&self, command: RotateAuthSession)
    -> Result<AuthSessionWrite, AuthSessionStoreError>;
async fn revoke_session(&self, scope: &AuthSessionScope, session_id: &str)
    -> Result<AuthSessionRevoke, AuthSessionStoreError>;
```

scope 来自可信配置 + 已验证 JWT/身份，不由未验证 JWT 决定 env。版本必须 `checked_add(1)`；撤销也增加版本，hash 清空后不可通过查询。新登录在读取 revision 后预签 JWT，再 CAS 安装；并发登录冲突返回 409，不自动调用无条件写入；重启新的浏览器登录批次是明确重试方式。refresh 冲突按 spec 返回 401。

### Plugin API 侧（Task 5）

`AuthSessionIdentityPort` 在 bcs-auth-api 中声明与上述五个方法相同的参数/结果语义；类型名称对应为 `SessionScope`、`SessionVersion`、`SessionSnapshot`、`InstallSession`、`RotateSession`、`SessionWrite`、`SessionRevoke`、`SessionStoreError`。增加 `ensure_identity(provider: &str, external_user_id: &str, name: Option<&str>, avatar: Option<&str>, env: &str) -> Result<String, SessionStoreError>` 以在新登录时创建/确认身份，不能调用吞错误的 lookup。bootstrap `RepoAuthSessionIdentityPort` 逐字段转换到 Repository，不让两个契约 crate 互相依赖。

### Delivery 侧（Task 7）

```rust
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CredentialKind { GatewayPrincipalHeader, OAuthSessionCookie, SsoCookie, ExplicitToken }
#[derive(Clone, Debug)]
pub struct AuthenticationContext { pub source: String, pub credential_kind: CredentialKind }
#[derive(Clone, Debug)]
pub struct VerifiedRequestIdentity {
    pub caller: AuthenticatedCaller,
    pub authentication_context: AuthenticationContext,
}
```

`PrincipalVerifier::verify(&HeaderMap) -> Result<VerifiedRequestIdentity, PrincipalVerificationError>`；错误为 Missing / Invalid / Forbidden / Unavailable / Internal，均可 Clone。新增默认方法 `verify_in_attempt(&HeaderMap, &mut VerificationAttempt)`，默认委托 verify，OAuth wrapper 覆写它。`VerificationAttempt` 只含 `oauth_session: Option<Result<VerifiedRequestIdentity, PrincipalVerificationError>>`，由每次 Composite::verify 创建，缓存同一请求的严格 OAuth 结果，不存 token、不跨请求复用。`CompositePrincipalVerifier::new(Vec<Arc<dyn PrincipalVerifier>>)` 按传入顺序运行，只有 Missing 继续。`TrustedBrowserOrigins::new(Vec<String>) -> Result<Self, String>` 与 `validate(method, origin, &AuthenticationContext) -> Result<(), PrincipalVerificationError>` 在 HTTP adapter 实现。

## Task 1: 拆分必改大文件，保留路径导出和行为

**Files:** Modify 上述大文件；Create：

- `crates/bootstrap/bcs/src/server/{state.rs,defaults.rs,router.rs,runtime.rs,protocol.rs,secrets.rs,gateway_trust.rs,session_files.rs,channels.rs,collaboration.rs,construction.rs,construction_memory.rs,construction_storage.rs,construction_finish.rs,tests_auth.rs,tests_runtime.rs,tests_collaboration.rs}`。
- `crates/bootstrap/bcs/src/config/{types.rs,defaults.rs,loading.rs,validation.rs,tests_auth.rs,tests_core.rs,tests_paths.rs}`。
- `crates/bootstrap/bcs/src/migrations/{baseline_identity.rs,baseline_collaboration.rs,baseline_delivery.rs,registry.rs,repairs.rs,report.rs,tests_core.rs,tests_upgrade.rs,tests_eventing.rs}`。
- `crates/adapters/http/bcs-http/src/oauth/{legacy_facade.rs,tests.rs}`；`crates/service-api/bcs-config-api/src/auth.rs`。

**Interfaces:** 保持 `bcs::BcsConfig`、`bcs::BcsServer`、`bcs::resolve_config_secrets`、`bcs::migrations::*`、`bcs_http::oauth::OAuthRouteState` 的原签名/re-export。大型构造函数按已存在阶段提取命名 helper，逐步保持原初始化次序，不引入新模式开关。

- [ ] **1. 记录基线测试和待移动函数**：运行 `cargo test -p bcs --lib`、`cargo test -p bcs-http --lib oauth`、`cargo test -p bcs-config-api`，保存失败/缺依赖信息，不以已有失败为借口删除测试。
- [ ] **2. 写行为不变的回归检查**：`crates/bootstrap/bcs/tests/bootstrap_split.rs`，锁定公开导出及 schema 目标版本，以下测试在拆分前后都必须通过：

```rust
#[test]
fn public_config_and_schema_exports_survive_split() {
    let config = bcs::BcsConfig::default();
    assert_eq!(config.gateway_principal.audience, "bcs");
    assert_eq!(bcs::migrations::sqlite_target_version(), 27);
}
```

- [ ] **3. 每次只移动一个责任块**：state/extension、trust/secrets、router、测试、各构造阶段；config 分离 DTO/default/load/tests；migrations 保持 DDL 顺序与 checksum 字节不变。DDL 按责任搬成 Rust `&[&str]` 常量组，runner 用按原顺序 chain 的 iterator 逐条执行；不能把多个 SQL statement 合成一次 execute，也不能改既有 checksum。
- [ ] **4. 更新 module/re-export 最小接线**，例如 `pub use state::{BcsServerState, BcsServerExtensions};`；根据 rust privacy 将父模块被 sibling 使用的内部成员设为 `pub(super)`，不公开到外部 API。
- [ ] **5. 回归并检查行数**：运行步骤 1、`cargo test -p bcs --test bootstrap_split`、`git diff --check`；检查每个新增/修改源码均 ≤1000 行。若某构造阶段仍超限，继续按依赖构建阶段拆分，不按任意行号截断函数。
- [ ] **6. 提交组织结构变更**：`refactor(bcs): isolate auth wiring and migration boundaries`，只显式 stage 本任务路径。

## Task 2: 定义严格会话 Repository 和 conformance 边界

**Files:** Create `crates/service-api/bcs-service-api/src/port/repo/auth_session.rs`；Modify 对应 `port/repo/mod.rs`、service-api CONTEXT；Create `crates/test-support/bcs-test-support/src/contract/repo/auth_session.rs` 并在 `contract/repo/mod.rs` re-export。

**Interfaces:** 产生上文 `AuthSessionRepoPort` 和数据类型。独立于旧 UserIdentityRepoPort；实现对象可以同时实现两个 trait，但禁止 strict 默认方法调用旧 Option 方法。

- [ ] **1. 写失败测试**：新 `crates/service-api/bcs-service-api/tests/auth_session_contract.rs` 检查 trait object 安全、scope equality 和错误类别；集中 harness 固定名 `auth_session_repo_port_contract_tests(repo: &dyn AuthSessionRepoPort, scope: AuthSessionScope)`。

```rust
#[test]
fn environment_is_part_of_session_scope() {
    use bcs_service_api::port::repo::auth_session::AuthSessionScope;
    let a = AuthSessionScope { user_id: "u".into(), provider: "github".into(), env: "dev".into() };
    let b = AuthSessionScope { env: "prod".into(), ..a.clone() };
    assert_ne!(a, b);
}
```

- [ ] **2. RED**：`cargo test -p bcs-service-api --test auth_session_contract`，预期新 module/type 尚不存在。
- [ ] **3. 添加上文完整类型/trait**，错误不带 SQL、endpoint 或 Secret；把读 revision、无记录、持久化错误和写冲突分开，仓库缺少 identity row 不是自动创建成功。
- [ ] **4. 编写 harness 的操作序列**：revision=0；安装 A；查询 A；旋转 B；查询 A=None；撤销同 session_id；查询 B=None；重复撤销 NotCurrent。每次断言返回 enum，不能仅 `.is_ok()`。
- [ ] **5. GREEN**：步骤 2 和 `cargo check -p bcs-test-support`；harness 实际实现调用从 Task 3 开始。
- [ ] **6. 提交**：`feat(bcs): define strict atomic auth session repository`。

## Task 3: 实现内存会话操作和确定性竞争测试

**Files:** Create `crates/services/bcs-user-identity/src/session_memory.rs`、`tests/conformance_auth_session_memory.rs`；Modify `src/memory.rs`、`src/lib.rs`；Extend Task 2 harness。

**Interfaces:** `MemoryUserIdentityRepo` 同时实现旧 UserIdentityRepoPort 与新 AuthSessionRepoPort；共享同一 identity 记录，不建第二张用户表。trait 实现可以委托新模块，但一次操作必须持有同一锁。

- [ ] **1. 写失败 consumer 测试**：先用旧 `ensure_identity` 生成真实 scope，再调用新 harness；另添加以下竞争断言序列：

```rust
// same_sid 是 A.session_id；B 是 revision+1、同 sid 的下一 token。
assert_eq!(repo.rotate_session(to_b.clone()).await.unwrap(), AuthSessionWrite::Applied);
assert_eq!(repo.revoke_session(&scope, &same_sid).await.unwrap(), AuthSessionRevoke::Revoked);
assert!(repo.get_session_by_hash(&scope, &to_b.next.token_hash, to_b.now).await.unwrap().is_none());
assert_eq!(repo.rotate_session(to_b).await.unwrap(), AuthSessionWrite::Conflict);
```

`to_b` 是上文 RotateAuthSession，scope 由测试 ensure_identity 获得；测试显式构造 A/B 哈希、时间与版本，不调用尚未实现的 JWT。

- [ ] **2. RED**：`cargo test -p bcs-user-identity --test conformance_auth_session_memory`，预期 trait 未实现。
- [ ] **3. 最小实现**：条件匹配 scope/sid/revision/hash/expiry；只在匹配时一次写入新值。撤销按 sid 清当前 hash、expiry 并加 revision；hash 已空重复撤销为 NotCurrent。使用 checked_add，溢出 CorruptRecord。
- [ ] **4. 补齐新登录 C 与旧 logout、双 refresh、过期和跨 env 测试**；用 `tokio::sync::Barrier` 启动竞争，断言两个 refresh 的 Applied 数量为 1。
- [ ] **5. GREEN**：步骤 2、`cargo test -p bcs-user-identity --lib`；不放宽旧身份字段契约。
- [ ] **6. 提交**：`feat(bcs): implement atomic in-memory session rotation`。

## Task 4: SQL 条件操作、迁移和真实 store 故障分类

**Files:** Create `crates/services/bcs-user-identity/src/session_db.rs`、`tests/conformance_auth_session_sqlite.rs`、`tests/auth_session_db_errors.rs`；Modify `src/lib.rs` 和 migration registry（Task 1 后的小文件）；Create `migrations/sqlite/028_auth_session_version.sql`、`migrations/mysql/027_auth_session_version.sql`；Modify `migrations/README.md`。

**Interfaces:** DbUserIdentityStore 实现 Task 2 trait；使用已有 DbPlugin::execute/transaction 的 affected_rows，不增加私有数据库接口。开始执行时若迁移编号已被其他提交占用，顺延到最新空闲版本并更新本任务测试，不覆盖已有 checksum。

- [ ] **1. 写失败测试**：SQLite 真实插件建旧 schema，运行新 migration；确认旧 token 无效、新字段存在、重复执行 migration 不再次清会话。调用 Task 2 harness；query 成功零行必须 Ok(None)，故障必须 Err(Unavailable)。
- [ ] **2. RED**：`cargo test -p bcs-user-identity --test conformance_auth_session_sqlite`、`cargo test -p bcs-user-identity --test auth_session_db_errors`。
- [ ] **3. 添加增量 DDL 和严格 SQL**：新增 nullable `session_id`、非空 `session_revision`（默认 0）、用于无歧义比较的 `session_expires_at` Unix 秒列；迁移时将历史 token/expiry 清空。scope 仍为已有 user_id/auth_source/env。

```sql
UPDATE bcs_user_identities
SET token = ?, token_expire_at = ?, session_expires_at = ?,
    session_revision = session_revision + 1
WHERE user_id = ? AND auth_source = ? AND env = ?
  AND session_id = ? AND session_revision = ? AND token = ?
  AND session_expires_at > ?;
```

`token_expire_at` 用 flavor 对应 FROM_UNIXTIME / datetime 表达式，参数不是字符串拼接。Rotate.next.sid 必须等于 expected.sid，next.revision 必须为 expected+1。install 用同样 scope+expected_revision CAS 写入新 sid；revoke 的条件只有 scope+sid+非空 hash，不匹配旧 revision/hash。0 行为 Conflict/NotCurrent，不能当成功或 DB 故障。

- [ ] **4. 故障注入**：测试实现 `DbPlugin` decorator，query/execute/transaction 分别在指定 operation 返回 `DbError::Backend("injected".into())`，其余调用委托真实 SQLite；逐项断言分类，不 `.ok()`。Db row 类型错误映射 CorruptRecord。中央 harness 由 SQLite 测试调用。
- [ ] **5. GREEN**：步骤 2、`cargo test -p bcs --lib migrations`、`cargo test -p bcs-user-identity`。补新 migration 后 Task 1 target version 测试改为 28；真实 MySQL 双连接竞争保留到 Task 13，不能以 SQLite 替代报告通过。
- [ ] **6. 提交**：`feat(bcs): persist versioned sessions with conditional writes`。

## Task 5: 严格 Auth port、JWT 会话实例和 bootstrap bridge

**Files:** Create `crates/plugin-api/bcs-auth-api/src/session.rs`、`crates/services/bcs-jwt/src/oauth_session.rs`、`crates/services/bcs-jwt/tests/oauth_session.rs`、`crates/bootstrap/bcs/src/identity_session_wiring.rs`；Modify 对应 lib/re-exports、现有 `identity_wiring.rs`；Create test-support `contract/plugin/auth_session_identity.rs`。

**Interfaces:** 产生上文 AuthSessionIdentityPort；`OAuthSessionClaims` 包含 sub/src/env/session_id/revision/iat/exp/name。`OAuthSessionJwt::new(secret: &str)`、`sign(&OAuthSessionClaims) -> Result<String, JwtError>`、`verify_at(token: &str, now: u64) -> Result<OAuthSessionClaims, JwtError>`、`verify_for_revoke(token: &str) -> Result<OAuthSessionClaims, JwtError>`；后者只跳过 exp，不跳过签名、claim 完整性。`RepoAuthSessionIdentityPort::new(repo: Arc<dyn AuthSessionRepoPort>, identities: Arc<dyn UserIdentityRepoPort>)` 只做契约翻译。

- [ ] **1. 写失败测试**：在 JWT test 显式构造新 claims；同一 iat/exp 时 revision 改变必须改变 JWT，缺 sid/revision/env 的旧 token 被新 verifier 拒绝。

```rust
let jwt = OAuthSessionJwt::new("test-signing-material");
let a = OAuthSessionClaims { sub: "u".into(), src: "github".into(), env: "local".into(),
    session_id: "sid-a".into(), revision: 1, iat: 100, exp: 200, name: None };
let b = OAuthSessionClaims { revision: 2, ..a.clone() };
assert_ne!(jwt.sign(&a).unwrap(), jwt.sign(&b).unwrap());
assert!(jwt.verify_at(&jwt.sign(&a).unwrap(), 200).is_err());
```

- [ ] **2. RED**：`cargo test -p bcs-jwt --test oauth_session`。
- [ ] **3. 实现**：复用现有 JWT HMAC/验签工具，不改变 group-session JWT；新类型 required 字段不扩成 Optional。新增严格 bridge 逐字段转换 repo Result，旧 identity display bridge 原样保留；ensure_identity 错误传播到 strict port。
- [ ] **4. 添加 bridge conformance**：DB decorator 注入错误必须得到 SessionStoreError::Unavailable；跨 env/sid/revision 拒绝；未知 hash 仍 None。中央 harness 名 `auth_session_identity_port_contract_tests`，调用它的测试放 `crates/bootstrap/bcs/tests/conformance_auth_session_identity.rs`。
- [ ] **5. GREEN**：步骤 2、`cargo test -p bcs --test conformance_auth_session_identity`、`cargo test -p bcs-auth-api`。
- [ ] **6. 提交**：`feat(bcs): add strict session identity bridge and JWT claims`。

## Task 6: api.auth schema 与开放的两阶段 registry

**Files:** Create config-api `src/api_auth.rs`、`tests/api_auth.rs`；Create bootstrap `src/api_auth_registry.rs`、`tests/api_auth_config.rs`；Modify Task 1 后 `config.rs`、`config/types.rs`、`config/validation.rs`、bootstrap lib.rs、config-api exports。

**Interfaces:** `ApiConfig { auth: Option<ApiAuthConfig> }`（缺省 auth 有明确兼容语义）；ApiAuthConfig 包含 chain、OAuth 公共字段、trusted_browser_origins 和经结构化解析的 plugin tables。`ApiAuthSourceRegistration { name, capabilities, validate, build }`：`ApiAuthCapabilities { uses_browser_cookie: bool, is_oauth_provider: bool }`；validate(&serde_json::Value) -> Result<(), String>；Task 6 注册原型只包含 name/capabilities/validate，Task 12 原地添加 build 指针和构造上下文，不提前引用缺失类型。`validate_api_auth(config, registrations) -> Result<(), String>` 在最终 deep-merge/env expansion 后调用。

- [ ] **1. 写失败配置测试**：保持下列用户要求的 TOML 形状，未知来源必须在 registry 阶段报错，而非被 BcsConfig 先忽略：

```rust
let value: toml::Value = toml::from_str(r#"
[api.auth]
chain = ["gateway", "cookie"]
[api.auth.cookie]
client_id = "test-client"
"#).unwrap();
assert_eq!(value["api"]["auth"]["chain"].as_array().unwrap().len(), 2);
```

完整测试向 registry 注入名为 cookie 的只校验 descriptor，证明下游名称可被发现；未注册版本拒绝同一输入。该 descriptor 不作为默认 production 注册。

- [ ] **2. RED**：`cargo test -p bcs-config-api --test api_auth`、`cargo test -p bcs --test api_auth_config`。
- [ ] **3. 实现公共/插件分层解析**：reserved fields 严格识别；剩余项必须是已注册插件表；各表 deny_unknown_fields；重复来源/注册、空 chain、非法名称、启用 OAuth 缺公共参数报错。合法但未启用表只验证结构，不获取 Secret。只包含 Cookie 型来源时也要求 Origin，不能绑定 OAuth 名称。
- [ ] **4. 添加最终 overlay 测试**：base gateway，dev 替换 chain；无新段进入兼容；显式空表不是兼容；旧 Auth.chain/priority 不改变。secret supplier 的调用计数未启用时为 0。
- [ ] **5. GREEN**：步骤 2、`bash scripts/ci/check-config-validation.sh`；按 Task 1 的拆分同步该脚本 CONFIG_SCOPE，增加新路径而非删除既有检查。
- [ ] **6. 提交**：`feat(bcs): validate independent API auth configuration`。

## Task 7: Delivery 实际来源、有序验证与 CSRF

**Files:** Create `v1/common/authentication.rs`、`csrf.rs`、`composite_principal.rs`、`tests/api_auth_boundary.rs`（位于 bcs-api-http）；Modify `common/principal.rs/mod.rs/state.rs/error.rs`、Gateway verifier、group_session_connection.rs、现有所有 PrincipalVerifier test doubles。先将超限的 `tests/{session_routes,friendship_routes,bot_routes,group_routes}.rs` 按 fixtures 与测试职责拆到对应 `tests/support/<route>_fixtures.rs` / 路由测试子模块，每个源码 ≤1000 行；不删除断言、不加 allowlist。

**Interfaces:** 上文 VerifiedRequestIdentity/AuthenticationContext/CredentialKind/VerificationAttempt、CompositePrincipalVerifier、TrustedBrowserOrigins；PrincipalVerifier 签名增量破坏只限 delivery 消费方，更新全部调用者后提交。

- [ ] **1. 写失败测试**：最小的纯 Origin 测试不依赖业务 facade：

```rust
let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()]).unwrap();
let cookie = AuthenticationContext { source: "github".into(), credential_kind: CredentialKind::OAuthSessionCookie };
let gateway = AuthenticationContext { source: "gateway".into(), credential_kind: CredentialKind::GatewayPrincipalHeader };
assert!(origins.validate(&axum::http::Method::POST, None, &cookie).is_err());
assert!(origins.validate(&axum::http::Method::POST, None, &gateway).is_ok());
```

- [ ] **2. RED**：`cargo test -p bcs-api-http --test api_auth_boundary`。
- [ ] **3. 实现三态以外的错误传播**：Missing 才继续；Invalid→401、Forbidden→403、Unavailable→503、Internal→500。Gateway 成功 provenance 固定为 gateway/header；config 顺序不调用 priority。成功后 middleware 固定 metadata，CSRF 通过才调用下游；Origin 拒绝不重跑 chain。
- [ ] **4. 用计数 fake verifier 增加全矩阵**：Gateway 成功+坏 cookie，Gateway 失败+好 cookie，OAuth 成功+好 Gateway+缺 Origin，来源返回 Missing；断言后续 verifier/业务服务未调用。来源名由注册 wrapper 固定，不能由请求头设置。
- [ ] **5. GREEN**：步骤 2、`cargo test -p bcs-api-http`、`cargo check -p bcs --all-targets`。此时生产仍只装配 Gateway，验证旧入口无变化。
- [ ] **6. 提交**：`feat(bcs): preserve authentication provenance for CSRF checks`。

## Task 8: OAuth 严格验证和原子生命周期引擎

**Files:** Create bcs-auth-oauth `src/strict.rs`、`session_lifecycle.rs`、`tests/strict_session.rs`、`tests/session_lifecycle.rs`；Modify lib.rs/Cargo.toml；Extend bcs-auth-api session 契约和中央 harness。

**Interfaces:** `OAuthSessionEngine::new(jwt: OAuthSessionJwt, identities: Arc<dyn AuthSessionIdentityPort>, env: String, idle_secs: u64)`；`verify(token, now) -> Result<SessionSnapshot, SessionAuthError>`、`install(scope: SessionScope, display_name: Option<String>, now: u64) -> Result<IssuedSession, SessionAuthError>`、`refresh(token: &str, now: u64) -> Result<IssuedSession, SessionAuthError>`、`revoke(token: &str) -> Result<SessionRevoke, SessionAuthError>`。`IssuedSession { token: String, expires_at: u64 }`；SessionAuthError=Invalid/Forbidden/Unavailable/Internal/Conflict。Origin 和 provider allowlist 不在引擎猜测，分别由 HTTP 与来源适配器处理。

- [ ] **1. 写失败测试**：在新 `crates/plugins/bcs-auth-oauth/tests/support/session_identity.rs` 定义 TestSessionIdentity：用 Mutex 保存严格 port 的 scope/version/hash/expiry，记录操作并可注入错误，实现 Task 5 全部方法；将它注入 engine，不让插件测试反向依赖 bootstrap。真实 store/bridge 消费方覆盖由 Task 13 完成，使用相同 now 连续刷新；断言旧 JWT 查无绑定、新 JWT 有新 revision、revoke(old) 后新 JWT 也失效。
- [ ] **2. RED**：`cargo test -p bcs-auth-oauth --test strict_session`、`cargo test -p bcs-auth-oauth --test session_lifecycle`。
- [ ] **3. 最小算法**：

```text
refresh(token, now):
  claims = jwt.verify_at(token, now)
  reject claims.env != injected env
  snapshot = strict_port.get_session_by_hash(scope, sha256(token), now)
  require claims.sid/revision == snapshot.sid/revision
  next_revision = checked_add(snapshot.revision, 1)
  sign next JWT with same sid, next_revision and injected env
  CAS rotate(expected snapshot, next hash/expiry)
  Conflict -> Invalid; storage failure -> Unavailable
  return IssuedSession only after Applied
```

install 使用随机新 sid、read revision、签名、CAS 安装；409 不做隐式覆盖重试。revoke 验签并核对 scope/environment，不要求旧 hash 仍活跃，按 sid 原子撤销所有刷新后继。

- [ ] **4. 补齐无效签名/无 env/无 sid、DB 不可用、expiry 与 revision 溢出测试**；不复用旧 verify_oauth_session 中“错误返回 None”的实现作为严格接口。
- [ ] **5. GREEN**：步骤 2、`cargo test -p bcs-auth-api -p bcs-jwt -p bcs-auth-oauth`。
- [ ] **6. 提交**：`feat(bcs): enforce strict OAuth session lifecycle`。

## Task 9: 浏览器绑定 state 与必需 application 输入输出

**Files:** Create bcs-auth-api `src/login_state.rs`；Create bcs-auth-oauth `src/login_state.rs`、`tests/conformance_pending_oauth_login.rs`；Modify service-api `application/v1/auth.rs`；Create `port/oauth.rs`；Add centralized pending login harness。

**Interfaces:**

- Plugin contract：`PendingOAuthLoginStore::issue_batch(providers: &[String], callback_base: &str, flow: &str, now: u64) -> Result<LoginBatch, LoginStateError>`；`consume(state, browser_nonce, provider, exact_callback, flow, now) -> Result<(), LoginStateError>`。
- `LoginBatch { browser_nonce: String, expires_at: u64, provider_states: Vec<(String,String)> }`；LoginStateError=Invalid/Unavailable。实现 `MemoryPendingOAuthLoginStore`，同一锁完成匹配和整个批次消费，nonce hash 常量时间比较。
- Service types（本任务先增量添加，不改变活跃 AuthService）：`BrowserLoginBinding { nonce: String }`；`BuildLoginUrls` 保留 callback_base_url；`BoundOAuthLogin` 暂时与旧 CompleteOAuthLogin 并存，保留其 provider/code/auth_code/state/callback_base_url 字段并新增 required browser_binding；`BrowserCookieChange` 为 `SetLoginChallenge { nonce, expires_at }`、`ClearLoginChallenge`、`SetSession { token, expires_at }`、`ClearSession`。
- `AuthFlowReply<T> { result: Result<T, ApplicationError>, cookie_changes: Vec<BrowserCookieChange> }` 保证成功/失败均能带清理指令；不把原始 HTTP headers/response 类型带入 service contract。

- [ ] **1. 写跨浏览器失败测试**：

```rust
let store = MemoryPendingOAuthLoginStore::new();
let batch = store.issue_batch(&["github".into()], "https://bcs.example.com/openapi/v1/auth/callback", "v1", 100).await.unwrap();
let state = &batch.provider_states[0].1;
assert!(store.consume(state, "browser-b", "github", "https://bcs.example.com/openapi/v1/auth/callback/github", "v1", 101).await.is_err());
assert!(store.consume(state, &batch.browser_nonce, "github", "https://bcs.example.com/openapi/v1/auth/callback/github", "v1", 101).await.is_ok());
```

- [ ] **2. RED**：`cargo test -p bcs-auth-oauth --test conformance_pending_oauth_login`。
- [ ] **3. 实现 CSPRNG batch/state**：nonce 不等于 state，记录仅保存 nonce hash、callback、provider、flow、TTL=300；两个 provider state 共享同一批次，任意一个成功消费时整个批次失效。错误浏览器不消费 A 的记录；store 写故障不返回有效登录 URL。
- [ ] **4. 添加同批次并发 consume、expiry、flow/provider/callback 错误测试**；新 BoundOAuthLogin test doubles 必须提供真实浏览器 binding，不能放 `Option` 或默认空值绕过；旧 CompleteOAuthLogin 与活跃 AuthService 的原子签名切换在 Task 11 做，避免 Task 9 单独提交无法编译。
- [ ] **5. GREEN**：步骤 2、`cargo test -p bcs-service-api`。本期先提供 memory 实现，生产要求单实例或已验证固定路由；多实例无固定路由部署在 Task 15 前不得启用 OAuth，不用无 state fallback 补可用性。
- [ ] **6. 提交**：`feat(bcs): bind OAuth login state to initiating browser`。

## Task 10: 抽出 transport-neutral AuthService 用例编排

**Files:** Create `crates/application/v1/bcs-app-auth/{Cargo.toml,CONTEXT.md,src/lib.rs,src/login.rs,src/session.rs,tests/login_flow.rs,tests/session_flow.rs}`；Modify root Cargo.toml/workspace dependencies、service-api auth.rs/port/oauth.rs；Create bootstrap `api_auth_ports.rs`。

**Interfaces:** 本任务新增 `SecureAuthService`（service-api/application/v1/auth.rs）并由 `AuthApplicationService` 实现；旧 AuthService 暂不改变。新 trait 在 Task 11 原子取代旧入口，随后稳定名称仍为 AuthService。消费下面 application ports（类型均在 service-api/port/oauth.rs，与 plugin 同义类型逐字段桥接）：

- `OAuthProviderPort::names() -> Vec<String>`、`auth_url(provider,state,redirect_uri) -> Result<String, ApplicationError>`、`exchange_user(provider,code,redirect_uri) -> Result<ExternalLoginIdentity, ApplicationError>`。
- `ExternalLoginIdentity { provider, external_user_id: String, name: Option<String>, avatar: Option<String> }`。
- `OAuthSessionPort::install_identity(identity: ExternalLoginIdentity, now: u64) -> Result<BrowserSession, ApplicationError>`、`refresh(token: &str, now: u64) -> Result<BrowserSession, ApplicationError>`、`revoke(token: &str) -> Result<(), ApplicationError>`；`BrowserSession { token: String, expires_at: u64 }` 对应 IssuedSession，revoke 的 Revoked/NotCurrent 均为应用幂等成功。
- `PendingOAuthLoginPort` 与 Task 9 的 issue_batch/consume 同语义，使用 service-api 自己的 `PendingLoginBatch { browser_nonce: String, expires_at: u64, provider_states: Vec<(String,String)> }`，错误映射为 ApplicationError，避免 service-api 依赖 plugin-api。
- SecureAuthService 的 login_urls/complete_login/refresh_session/logout 返回 `AuthFlowReply<T>`（T 分别为原 provider URL list、仅 location 的 redirect、无 cookie 字段的 renewal/logout 数据）；complete_login 输入为 BoundOAuthLogin；current_user 输入为 `AuthenticatedUserQuery { caller: AuthenticatedCaller, provider_label: String, avatar: Option<String> }`，只做已认证 Human 投影，不二次执行另一条 chain。provider_label/avatar 由可信 adapter 提供，仅为显示数据，不能当权限；不传 delivery AuthenticationContext 到业务层。

- [ ] **1. 写失败用例测试**：fake PendingOAuthLoginPort 记录 consume，fake OAuthProviderPort 记录 exchange；错误浏览器时 exchange=0、install=0；exchange 失败时 result=Err 且 cookie_changes 恰有 ClearLoginChallenge。
- [ ] **2. RED**：`cargo test -p bcs-app-auth --test login_flow`（创建最小 workspace 成员后运行，失败必须来自缺功能，不把缺 manifest 当业务 RED）。
- [ ] **3. 按以下顺序实现编排**（新增 crate 依赖 service-api、async-trait、time，仅使用配置注入和 ports，不依赖 adapters/concrete plugins；在 Cargo.toml 和 CONTEXT.md 声明该边界）：

```text
complete_login(command):
  validate enabled provider + required code
  pending.consume(state, browser_binding, provider, callback, configured flow)
  on binding failure: reply(error, no cookie changes)
  exchange_user -> install_identity (both error-propagating)
  reply always clears challenge after successful consume
  success additionally sets session + configured same-origin redirect
```

login_urls 保持 provider chain 子序列顺序；current_user 仅 Human；refresh/revoke 调用会话 port；为新 trait 定义 `PresentedSession { token: String }`，refresh 输入该必需结构，logout 输入 `Option<PresentedSession>`（无 Cookie 是幂等退出的有效边界）。旧 Headers 参数只由 Task 11 delivery 转换，application 不自行解析 cookie；错误分类 Unavailable→application Unavailable。secret、provider 选择、Origin 检查不放 application。

- [ ] **4. Bootstrap bridges**：provider bridge 包装已注入 `Arc<dyn OAuthProvider>`，仅完成 exchange_code/get_user_info 转换；session bridge 调用 Task 8 engine 并 ensure_identity；pending bridge 调 Task 9 store。bridge 不选择实现、不查 env、不写 ad hoc SQL。
- [ ] **5. GREEN**：步骤 2、`cargo test -p bcs-app-auth --test session_flow`、`cargo check -p bcs --all-targets`、`bash scripts/ci/check-import-rules.sh`。新 app crate 增加 Context/central conformance harness；所有 ports 均有 fake 记录调用。
- [ ] **6. 提交**：`refactor(bcs): share secure OAuth application orchestration`。

## Task 11: 新旧 HTTP 登录/刷新/退出一起迁移

**Files:** Modify `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/auth.rs`、common state/error；Modify `crates/service-api/bcs-service-api/src/port/repo/user_identity.rs`、`crates/plugin-api/bcs-auth-api/src/port.rs`、identity store/test doubles 中旧 update_token；Modify bcs-http `oauth/mod.rs`、`legacy_facade.rs`、routes/tests；Create bcs-api-http `tests/oauth_browser_binding.rs`；Modify bootstrap `tests/integration_oauth_routes.rs`。

**Interfaces:** 原子替换活跃 AuthService 的方法/返回值为 Task 10 SecureAuthService，删除临时 trait 名并让 BoundOAuthLogin 成为 CompleteOAuthLogin 的最终形状；同步全部消费者/test doubles，在同一个可编译提交完成，不能保留生产双入口。仅由 adapter 解析临时 Cookie、request method/Origin；调用升级后 AuthService；消费 AuthFlowReply 的 CookieChange 生成多个 Set-Cookie。旧 OAuthRouteState 改为持有 injected AuthService/旧 identity reader，不再实现另一份 callback/refresh/logout 业务逻辑。

- [ ] **1. 写失败 HTTP 测试**：两个独立 cookie jar；A GET URL 收 challenge，B 带合法 state/code 但无 A Cookie 请求 callback，断言失败且未兑换；A 正常 callback 断言响应同时包含 session 设置和 challenge 清理，JSON 不包含 nonce。
- [ ] **2. RED**：`cargo test -p bcs-api-http --test oauth_browser_binding`、`cargo test -p bcs --test integration_oauth_routes`。
- [ ] **3. 编码 Cookie/响应**：

```rust
// append 而非 insert，不能将两个 Set-Cookie 合并成逗号分隔值。
response.headers_mut().append(axum::http::header::SET_COOKIE, session_header);
response.headers_mut().append(axum::http::header::SET_COOKIE, clear_challenge_header);
response.headers_mut().insert(axum::http::header::CACHE_CONTROL,
    axum::http::HeaderValue::from_static("no-store"));
```

`session_header`/`clear_challenge_header` 由本任务 cookie encoder 将 Task 9 enum 转成 HeaderValue；生产 challenge 固定 `__Host-bcs_oauth_login`、Path=/、HttpOnly、Secure、SameSite=Lax、Max-Age=300。local HTTP 用独立名称，不能错误使用 __Host 前缀。

- [ ] **4. 同步旧入口**：旧 URL/callback 使用 legacy flow namespace 与相同安全 state/session port；refresh/logout 显式检查 Cookie Origin，不能借 Gateway identity 跳过。新入口使用 api.auth.trusted_browser_origins；兼容/旧入口由 bootstrap 将现有 cors.allowed_origins 的有限精确集合注入（排除 wildcard/null，未配置则拒绝 Cookie unsafe 请求），不根据 Host/Referer 猜测允许来源；该兼容安全收紧写进文档。保持旧非 OAuth `/auth/user` fallback 与旧响应 shape；不保留旧 unconditional update_token。无 OAuth 时 V1 `/user` 仍能通过新 verifier 返回 Human；URL/callback/refresh/logout 返回 auth_not_configured。
- [ ] **5. GREEN**：步骤 2、`cargo test -p bcs-http --lib oauth`、`cargo test -p bcs-api-http`；扫描所有生产 update_token 调用并迁移，测试 helper 改调用 safe install 而非旧后门，移除两个旧端口的 update_token 方法并更新全部实现/调用者；原 identity conformance 的会话用例由新 session harness 接替，不丢弃测试覆盖。
- [ ] **6. 提交**：`fix(bcs): secure both legacy and V1 OAuth entrypoints`。

## Task 12: 完成 public 来源装配和下游 factory 接入

**Files:** Create bootstrap `api_auth_wiring.rs`、`api_auth_provider_configs.rs`；Modify Task 6 registry、Task 1 server/router/construction、bootstrap lib.rs、identity_wiring；Create `tests/api_auth_registration.rs`；Modify bcs-api-http state、standalone session-token wiring。

**Interfaces:** `ApiAuthBuildContext { name: String, options: serde_json::Value, secret_access: Arc<dyn SecretAccessPort>, sessions: Arc<dyn AuthSessionIdentityPort>, oauth: Option<Arc<OAuthSourceContext>> }`；`OAuthSourceContext { engine: Arc<OAuthSessionEngine>, allowed_providers: Vec<String> }` 由 bootstrap 在 OAuth 已启用时构造，非 OAuth 来源不需要它；`ApiAuthSourceRegistration::build` 类型为 `fn(ApiAuthBuildContext) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>>`。Gateway 可直接包装已有 verifier；OAuth sources 包装共享 engine。`BuiltApiAuth { verifier: Arc<dyn PrincipalVerifier>, auth_service: Option<Arc<dyn AuthService>>, origins: TrustedBrowserOrigins }`；无 OAuth 的 /user 由 delivery projection 提供，不因 auth_service=None 404。

- [ ] **1. 写失败装配测试**：注册额外名 `test_external_cookie` 的 fake descriptor；显式新配置仅选择它时证明 build 被调用、旧 inventory 不自动加入，未知/重复注册拒绝；secret_calls 只包含启用实例。
- [ ] **2. RED**：`cargo test -p bcs --test api_auth_registration`、`cargo test -p bcs --test api_auth_config`。
- [ ] **3. 实现初始化顺序**：发现 descriptor→合并后的 schema 验证→解析所选 Secret→构造 identity/session/provider clients→构造 application facade→原子发布可用路由。没有 `[api.auth]` 时走兼容选择，但使用已修复的共享 OAuth 原语。
- [ ] **4. OAuth 来源分派**：每请求一个已验证 Cookie 结果；循环到首个 OAuth source 才验证签名+repo+allowlist，验证 provider 不在 allowlist→403，不匹配当前来源→Missing。缓存仅 request-local，可在 Composite 的 invocation context 保存，不能全局 cache 成功用户。

```text
gateway present + valid -> return gateway; do not inspect OAuth cookie
first OAuth position + cookie present -> strict verify once
verified provider disabled -> Forbidden
verified provider differs from current source -> Missing
verified provider matches -> return caller + OAuthSessionCookie provenance
```

Composite 在每次 verify 创建 Task 7 的 VerificationAttempt，依次调用 verify_in_attempt；OAuth wrapper 仅在缓存缺省时调用共享 engine 并验证 allowlist，然后匹配当前 provider。外部 verifier 使用默认方法即可；不得在进程共享对象中保存上个请求的身份。

- [ ] **5. 统一注入**：主 V1 ApiState 与 group_session_connection_router 使用同一 Arc；所有生产/测试构造路径同步（Default、memory、storage）。未启用 gateway 时不读取其 Secret；其他 group-session token 签名依赖独立保持。provider 私钥和 client secret 通过 SecretPort 解析，错误只写逻辑 field path。
- [ ] **6. GREEN**：步骤 2、`cargo test -p bcs --lib api_auth`、`cargo check -p bcs --all-targets`、`cargo test -p bcs-api-http`；提交 `feat(bcs): assemble configurable V1 authentication sources`。

## Task 13: DB 到 HTTP 故障传播和跨入口竞争验收

**Files:** Create bootstrap `tests/api_auth_http.rs`、`api_auth_races.rs`、`tests/support/auth_db.rs`；Create `crates/services/bcs-user-identity/tests/conformance_auth_session_mysql.rs`；Extend SQL conformance 与实际 MySQL 环境测试；Modify application error tests。

**Interfaces:** 使用 Task 4 DbPlugin decorator、真实 DbUserIdentityStore、Task 5 bridge、Task 8 engine、Task 10 facade 和真正 Router；不得在高层直接 fake 一个 503 作为本任务证据。新增 application Unavailable 分支在全部 HTTP match 中穷尽处理。

- [ ] **1. 写 RED 测试**：数据库 query 故障走真实 `/openapi/v1/collaboration/groups` protected GET，期望 503；改为查询成功无记录期望 401；查询 malformed row 期望 500。业务服务 fake 计数均为 0。
- [ ] **2. 运行**：`cargo test -p bcs --test api_auth_http`，记录状态或调用计数不符，定位最底层吞错位置。
- [ ] **3. 增加可控时序（不依赖 sleep）**：在 DbPlugin decorator 的指定读写点放 `Barrier`，请求组合包含 legacy-refresh/V1-logout 与 V1-refresh/legacy-logout；如下断言固定安全结果：

```text
refresh pauses after reading A
logout(A) commits and returns success
resume refresh -> CAS Conflict, no Set-Cookie
request with A -> 401
request with candidate B -> 401
```

另测 rotate 先提交但 response 暂停→logout(A)→迟到 B cookie；新登录 C 与旧 logout；两次 refresh 同时读取 A；两个独立 DB store 实例竞争同一 SQLite DB。

- [ ] **4. 修补真实 error/affected_rows 路径**：不通过放宽预期或 mock 替换消除失败。实际 MySQL 两连接重复执行同一 harness；测试使用 bcs-db-mysql 已有 MysqlDbPlugin/AsyncMysqlDbManager，专用隔离测试库配置仅从测试边界读取，凭据不入仓库。该 live 测试加明确 ignore 标签，执行 `cargo test -p bcs-user-identity --test conformance_auth_session_mysql -- --ignored --nocapture`；未提供测试库则明确失败/未运行，不能 return 后显示绿色。
- [ ] **5. GREEN**：`cargo test -p bcs --test api_auth_http --test api_auth_races`、`cargo test -p bcs-user-identity`、`cargo test -p bcs-api-http --test api_auth_boundary`。
- [ ] **6. 提交**：`test(bcs): cover shared-session races and DB auth failures`。

## Task 14: 配置、HTTP 契约、conformance 与架构门禁

**Files:** Create `api-contracts/v1/authentication/contract.md`；Modify `api-contracts/v1/openapi/auth.yaml`、`shared.yaml`、`openapi.yaml`、`internal.yaml`、Gateway contract、`configs/bcs-config-example.toml`、`configs/bcs-config-local.toml`、`docs/superpowers/specs/2026-08-28-openapi-auth-facade.md`、各已变 crate CONTEXT/README；central harness exports；CI scripts 只增加真实边界/测试覆盖。

**Interfaces:** HTTP envelope 不变；OAuth browser challenge 与 multiple Set-Cookie、新 503/409 错误、source precedence、sid/revision JWT schema、Shared-writer rollout 都为文档契约。

- [ ] **1. 写失败 contract assertions**：OpenAPI auth/url 响应声明 Set-Cookie/no-store；callback 必须说明临时 cookie；refresh 401/503 和 logout 503 均列出。新增测试 `scripts/test_api_auth_contract.py` 使用 unittest 读取 YAML 并断言这些键，不能只 grep 术语。
- [ ] **2. RED**：`python -m unittest discover -s scripts -p test_api_auth_contract.py`；`python scripts/validate_openapi_contract.py`。
- [ ] **3. 更新样例和旧文档冲突**：显式 gateway-only、OAuth-only、mixed 三种示例；local 不隐式启用 Mock，OAuth required 参数齐全；provider table 存在不等于启用。旧 facade 文档移除“未绑定浏览器回退”和 best-effort logout 描述，注明安全升级版本。
- [ ] **4. R25**：为 AuthSessionRepoPort、AuthSessionIdentityPort、PendingOAuthLoginStore、OAuthProviderPort、OAuthSessionPort、PendingOAuthLoginPort、AuthService 增量契约分别提供/更新 `*_contract_tests` 中央 harness；每个 `conformance_*.rs` 都真实调用 harness。不得加 CONFORMANCE_WAIVED 掩盖缺失。
- [ ] **5. GREEN**：步骤 2，`bash scripts/ci/check-config-validation.sh`、`bash scripts/ci/check-import-rules.sh`、`bash scripts/ci/check-r25-conformance.sh`、`bash scripts/ci/arch-check.sh`。因目录拆分更新扫描路径是修正检查，不降低已有规则；独立记录基线故障和缺工具，skip 不算通过。
- [ ] **6. 提交**：`docs(bcs): specify configurable auth and shared-session safety`。

## Task 15: 发布演练、完整验证和交付

**Files:** Create `docs/superpowers/plans/2026-09-18-v1-api-auth-plugin-chain-validation.md`（执行时填写实际证据）；Modify migration README 的 rollout/runbook；必要时更新根 Singlebox BCS stories，不改变覆盖分母或阈值。

**Interfaces:** 不新增运行时功能。交付必须同时具备已修复 shared writers、schema、JWT、config 和 public/内部消费方版本一致性；测试不足则保留明确未完成状态。

- [ ] **1. 运行针对性完整回归**：

```bash
cargo test -p bcs-config-api -p bcs-service-api -p bcs-auth-api -p bcs-jwt
cargo test -p bcs-user-identity -p bcs-auth-oauth -p bcs-app-auth
cargo test -p bcs-auth-alipay -p bcs-auth-github -p bcs-auth-google -p bcs-auth-wechat
cargo test -p bcs-api-http -p bcs-http -p bcs
cargo test --workspace
bash scripts/ci/arch-check.sh
```

- [ ] **2. 做发布安全演练**：阻断/排空所有旧 callback/refresh/logout 写入实例→升级 schema（只失效旧会话，不删除身份/资产）→部署全量修复 writer→启用新配置→新登录。验证旧 sid-less JWT 不可恢复、新浏览器登录成功、刷新/退出原子性。旧新不安全 writer 混跑不是允许的灰度方式。
- [ ] **3. 做回滚演练**：先将 chain 切成 gateway-only、关闭 OAuth 生命周期入口；确认 group-session signing 仍有效。旧二进制回滚必须同步配置和 JWT/schema 兼容策略；不能重新启用旧 unconditional writer。记录恢复新版本后重新登录步骤。
- [ ] **4. 在仓库根跑统一 Singlebox 与 artifacts verifier**：

```bash
cd ../..
bash scripts/ci/singlebox_coverage.sh
python scripts/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir scripts/.dependencies/coverage/singlebox/reports
```

保留 40% runtime line、36% method、100% HTTP endpoint 和 CLI leaf 覆盖要求；不得通过排除新增 auth 路径或只在测试里调用 domain 凑覆盖。若服务/工具缺失，记录确切命令与原因，不填通过。

- [ ] **5. 对每个改动源码检查行数与 diff**：从 `git diff --name-only --diff-filter=ACM <执行前记录的HEAD>` 取路径，并与 `git ls-files --others --exclude-standard` 合并，逐个统计 Rust/Python/TS/JS/SQL 源文件；任何 >1000 先拆分再交付。运行 `git diff --check`。
- [ ] **6. 填写验证文档并准备 PR**：Problem / Solution / Validation / Compatibility and risk / Spec；关联 spec 验收 ID，列出真实 provider smoke、真实 MySQL、OCB 子模块集成是否验证。未运行项不得被 cargo 单测或静态阅读替代。提交 `test(bcs): record API auth rollout validation`。

## 3. Spec 验收覆盖索引

| Spec 标准 | 任务 |
| --- | --- |
| C1–C4 配置兼容与严格校验 | 1、6、11、12、14 |
| A1 Gateway 契约 | 7、12、13 |
| A2–A4 OAuth 多来源及无效 Cookie | 5、8、9、10、11、12 |
| A5 DB→HTTP 503 | 2、4、5、8、13 |
| A6–A7 顺序、无 fallback、身份不合并 | 7、12、13 |
| R1–R2 路由覆盖、public/gate/授权 | 7、11、12、13 |
| O1–O3 登录、会话共存、错误与 Cookie | 8、9、10、11、14 |
| O4–O5 浏览器绑定与批次消费 | 9、10、11 |
| O6–O9 原子竞争、全部 writer、发布 | 2、3、4、5、8、11、13、15 |
| S1–S2 按实际来源 CSRF | 7、11、12、13 |
| 下游 OCB factory 与配置可扩展性 | 6、12、14；内部 SDK 实现在 OCB 独立计划执行 |
| 四项审阅 R1/R2/R3/R4 | 9–11 / 2–5、8、11、13、15 / 2、4、5、13 / 7、12、13 |

## 4. 执行停点与本计划验证记录

- 任一阶段发现必须放宽 CSRF、原子性或错误传播才能兼容旧实现：停止该依赖链，修订 spec，不能自建降级模式。
- 多实例 OAuth 没有共享 pending-state 实现或固定路由保证：不启用该部署的 OAuth；Gateway-only 可独立验证。
- 内部 OCB 缺少 public 子模块/真实 SDK 环境：公开实现与 fake factory 测试可以推进，OCB 集成验收必须标未完成。
- 本轮仅生成计划，未执行 Task 1–15、未运行 Cargo/E2E、未产生运行时代码或迁移。文档自检覆盖路径、TOML/代码围栏、任务依赖和 spec 验收映射。
