# BCS 会话工作区（Session Workspace）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 BCS 中为每个 session 提供共享文件区：bot/human 可上传/下载/列出/删除/分享文件，对外暴露 HTTP + CLI，对内提供可插拔 `StoragePlugin` trait，v1 落地框架 + `bcs-storage-local` 本地后端 + 分享链接 + bcs-coordination skill 更新。

**Architecture:** 镜像 `DbPlugin`/`CachePlugin` 模式：trait crate `bcs-storage-api`（含类型 + 错误 + `FakeStoragePlugin` + 契约测试），本地后端 `bcs-storage-local`。领域类型 + 分享 token 落在 `bcs-domain`；repo 端口 `SessionFileRepoPort` 与应用 trait `SessionFileService` 落在 `bcs-service-api`；实现分别为 `bcs-session-file-store`（memory+mysql，通过 `DbPlugin`/`DbStatement`）与 `bcs-session-file`（service 核心含能力路由/鉴权/分享/Pending sweep）。HTTP 适配 `routes/session_files.rs`（axum 0.8 `{sid}` 路径、302 直连 / `Body::from_stream` 流式、静态段优先）；CLI 扩展 `SessionCommands::File` 子命令与 `BcsClient` 三阶段上传（跨主机剥离 `Authorization` + 回归测试）。baas 插件是独立外部 crate，**不在本计划范围**。

**Tech Stack:** Rust 1.91, tokio, axum 0.8 (`{sid}` path params), clap 4 derive, mysql_async via `bcs-db-api`, async-trait, thiserror 2, anyhow, serde, `ulid` 1, `hmac`/`sha2`/`base64` (分享 token), `tokio-util` 0.7 (`ReaderStream`), `reqwest` 0.12 (`stream` feature), `tempfile` 3 (测试).

## Global Constraints

逐字取自 spec（`2026-07-20-bcs-session-workspace-api.md` / `-design.md`）：

- 所有路径以 `/sessions/{sid}/...` 为会话作用域前缀；axum 0.8 路径参数语法 `{sid}`，**静态段 `/files/capabilities` 必须优先于参数段 `/files/{file_id}`**，启动期加测试验证不被当成 `file_id="capabilities"`。
- `FileStatus = "Pending"|"Ready"|"Deleting"|"Failed"`（serde rename_all = `"PascalCase"`）。v1 同步删除不置 `Deleting`。
- `sha256` 恒为 `Option<String>`，v1 一律 `None`（占位字段，后端可在 `complete_upload` 返回）。
- `file_id` = **ULID**（26 字符 Crockford base32，全局唯一），`prepare_upload` 时由 BCS 生成；token payload 仅 `{ v, file_id, exp }`，**不含 `session_id`**。
- `capabilities()` 必须**廉价、同步、无 IO**，返回构造期预计算值；后端 probe 仅在插件 `async fn new()` 构造期执行。`max_size = min(BCS max_file_size, 后端 max_object_size)` 在 bootstrap 静态化，运行时不调用 `capabilities().max_object_size`。
- 分段阈值 `multipart_threshold = 104857600`（100 MB）：`size <` 单片、`size ≥` 自动 multipart；100 MB **不是**硬截断，仅超 `max_size` 才 `413`。`max_file_size = 5368709120`（5 GB）。`part_number` 为 `u16`（1–65535），`part_count ≤ 65535` 否则 prepare `413`。`method`/`expires_at` 在 multipart prepare 响应最外层，`parts[]` 仅 `{ part_number, upload_url }`。
- 上传字节路由由 `supports_presign_put` 驱动：true（baas/OSS）→ `prepare_upload` 返回 `client_target: Direct{...}`，客户端直传后端、字节不经 BCS、`stream_upload` 不调用；false（local）→ `client_target: ProxyViaBcs`，BCS 用 `PUT .../content` 代理 + `stream_upload`。
- 下载由 `supports_presign_download` 驱动：true → `GET .../content` 302 到 `presign_get`；false → `get_stream` 流式返回 body。
- `object_handle` = `UploadHandle`/`StorageHandle` 序列化字符串，**仅持久化于 BCS DB，不透出客户端**；presign 后端**不持久化**短命 per-part 直传 URL（保持行小）。
- `DELETE` 在 **BCS 元数据层幂等**：行存在按 `status` 走 `delete`(Ready)/`abort_upload`(Pending/Failed) 删行后 204；行已不存在直接 204 不探测后端。`Failed` 按 `Pending` 处理走 `abort_upload`。
- 分享 token 用**独立密钥** `[session_files.share] token_secret`（**不复用 invite**），HMAC-SHA256 + base64url-no-pad，对标 invite/register；未配时启动告警 + 随机 32B（重启失效），生产必须显式配置。`default_ttl_seconds = 86400`，范围 60–604800。
- 删除权限 = 上传者（human 上传者；上传者 bot 则需拥有该 bot）**或** 会话创建者 / 该 group 的 driver bot，镜像 `delete_session`。普通成员可 upload/download/list/share-but-not-others。
- 迁移文件 `006_session_files.sql`（mysql）+ SQLite parity in `crates/bootstrap/bcs/src/migrations.rs`；表名 `bcs_session_files`，标准 `id`/`gmt_create`/`gmt_modified`/`env` 四元组 + utf8mb4，唯一索引 `(env, session_id, file_id)`，索引 `(env, session_id)`。
- 仓库路径均相对 `src/bcs/`。版本/依赖用 workspace `Cargo.toml` 已有项（`async-trait` 0.1、`thiserror` 2、`serde` 1、`serde_json` 1、`anyhow` 1、`tokio` 1、`bytes` 1、`futures` 0.3、`tokio-util` 0.7、`axum` 0.8、`reqwest` 0.12、`fastrand` 2、`hmac` 0.12、`sha2` 0.10、`base64` 0.22、`tempfile` 3），**新增 workspace dep：`ulid = "1"`**。
- AGENTS.md：Rust 不引入 `T | None` 风格的可空类型除非 `None` 是契约中的有意状态（`sha256: Option<String>` 即有意状态）。

---

## File Structure

相对 `src/bcs/`：

| 文件 | 职责 | 动作 |
|---|---|---|
| `crates/contracts/bcs-domain/src/actor.rs` | 新增 `ActorRef { actor_kind, actor_id }` | Modify |
| `crates/contracts/bcs-domain/src/session_file.rs` | `FileStatus`、`SessionFile` 领域类型 | Create |
| `crates/contracts/bcs-domain/src/share.rs` | `ShareTokenPayload` + encode/decode_and_verify | Create |
| `crates/contracts/bcs-domain/src/lib.rs` | re-export | Modify |
| `crates/contracts/bcs-domain/Cargo.toml` | + `ulid` workspace dep | Modify |
| `Cargo.toml`（workspace） | + members `bcs-storage-api`/`bcs-storage-local`/`bcs-session-file`/`bcs-session-file-store`、+ workspace dep `ulid`、+ workspace deps 4 条 path | Modify |
| `migrations/mysql/006_session_files.sql` | 表 | Create |
| `crates/bootstrap/bcs/src/migrations.rs` | SQLite parity（`bcs_session_files` CREATE TABLE + ensure） | Modify |
| `crates/plugin-api/bcs-storage-api/` | trait + 类型 + 错误 + `ByteStream` + 契约测试 + `FakeStoragePlugin` | Create |
| `crates/plugins/bcs-storage-local/` | 本地后端实现 + 契约测试 | Create |
| `crates/service-api/bcs-service-api/src/port/repo/session_file.rs` | `SessionFileRepoPort` 端口 | Create |
| `crates/service-api/bcs-service-api/src/port/repo/mod.rs` | re-export | Modify |
| `crates/service-api/bcs-service-api/src/application/session_files.rs` | `SessionFileService` 应用 trait + `SessionFileUseCaseError` | Create |
| `crates/service-api/bcs-service-api/src/application/mod.rs` | re-export | Modify |
| `crates/services/bcs-session-file-store/` | memory + mysql repo 实现 + conformance 测试 | Create |
| `crates/services/bcs-session-file/` | `SessionFileServiceImpl`（路由/鉴权/分享/sweep） | Create |
| `crates/service-api/bcs-services-container/src/services.rs` | + `session_files: Arc<dyn SessionFileService>` 字段 + builder + build() | Modify |
| `crates/adapters/http/bcs-http/src/routes/session_files.rs` | HTTP handler | Create |
| `crates/adapters/http/bcs-http/src/routes/mod.rs` | + `pub mod session_files;` | Modify |
| `crates/adapters/http/bcs-http/src/router.rs` | 注册路由 | Modify |
| `crates/adapters/http/bcs-http/src/state.rs` | + `with_session_files`/`with_storage_plugin`/share secret setter（如经 Services 则可免） | Modify（按需） |
| `crates/tools/bcs-cli/src/main.rs` | `SessionFileCommands` + `File` variant + dispatch | Modify |
| `crates/tools/bcs-cli/src/client.rs` | `BcsClient` 三阶段/下载/列表/删除/分享/能力 + 跨主机 redirect 策略 | Modify |
| `crates/tools/bcs-cli/bcs-coordination/references/session-file.md` | 新 reference | Create |
| `crates/tools/bcs-cli/bcs-coordination/SKILL.md` | 场景表 + 选择树 + 注意事项 | Modify |
| `crates/tools/bcs-cli/bcs-coordination/references/session.md` | 指向 session-file.md | Modify |
| `crates/bootstrap/bcs/src/config.rs` | `SessionFilesConfig` + `SessionFilesShareConfig` | Modify |
| `crates/bootstrap/bcs/src/server.rs`（+ `http_adapter.rs`） | 装配 storage plugin / service / repo / share secret fallback / `delete_session` 钩子 | Modify |
| `configs/bcs-config-example.toml`、`configs/bcs-config-local.toml` | `[session_files]` 配置块 | Modify |

---

## Task 1: 领域类型与分享 token（`bcs-domain`）

引入 `ActorRef`、`FileStatus`、`SessionFile`、`ShareTokenPayload` 的编码/解码。分享 token 复刻 invite/register 方案但用独立密钥、payload 仅 `{v, file_id, exp}`。`file_id` 用 ULID。

**Files:**
- Modify: `crates/contracts/bcs-domain/Cargo.toml`（+ `ulid = { workspace = true }`）
- Modify: `crates/contracts/bcs-domain/src/actor.rs`（+ `ActorRef`）
- Create: `crates/contracts/bcs-domain/src/session_file.rs`
- Create: `crates/contracts/bcs-domain/src/share.rs`
- Modify: `crates/contracts/bcs-domain/src/lib.rs`（re-export）
- Modify: `Cargo.toml`（workspace `[workspace.dependencies]` + `ulid = "1"`）

**Interfaces:**
- Produces（供后续所有任务依赖）：
  - `bcs_domain::ActorRef { actor_kind: ActorKind, actor_id: String }`（`#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]`）
  - `bcs_domain::FileStatus`（enum，serde `rename_all = "PascalCase"`，variants `Pending|Ready|Deleting|Failed`，`#[default] Pending`）
  - `bcs_domain::SessionFile`（领域结构，含 `file_id: String`、`session_id: String`、`file_name: String`、`mime_type: String`、`size: u64`、`sha256: Option<String>`、`owner: ActorRef`、`storage_backend: String`、`object_handle: String`、`status: FileStatus`、`created_at: u64`、`updated_at: u64`）
  - `bcs_domain::ShareTokenPayload { v: u8, file_id: String, exp: u64 }`、`bcs_domain::ShareTokenError`、`bcs_domain::share_token_encode(&ShareTokenPayload, &[u8]) -> String`、`bcs_domain::share_token_decode_and_verify(&str, &[u8]) -> Result<ShareTokenPayload, ShareTokenError>`
  - `bcs_domain::new_file_id() -> String`（`ulid::Ulid::new().to_string()`）

- [ ] **Step 1: 加 workspace dep `ulid`**

`src/bcs/Cargo.toml` 的 `[workspace.dependencies]` 段内（紧挨 `base64 = "0.22"` 之后）加一行：

```toml
ulid       = "1"
```

`crates/contracts/bcs-domain/Cargo.toml` 的 `[dependencies]` 段加：

```toml
ulid        = { workspace = true }
```

- [ ] **Step 2: 写失败测试 — 分享 token 往返与失败分支**

Create `crates/contracts/bcs-domain/src/share.rs` 先只放空占位会被测试驱动填充；但 TDD 顺序是先写测试。把测试放进 `share.rs` 末尾的 `#[cfg(test)] mod tests`：

```rust
// crates/contracts/bcs-domain/src/share.rs
use serde::{Deserialize, Serialize};

const HMAC_LEN: usize = 32;
const CURRENT_VERSION: u8 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ShareTokenPayload {
    pub v: u8,
    pub file_id: String,
    pub exp: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ShareTokenError {
    #[error("invalid share token encoding")]
    InvalidEncoding,
    #[error("invalid share token signature")]
    InvalidSignature,
    #[error("share link has expired")]
    Expired,
    #[error("unsupported share token version")]
    UnsupportedVersion,
    #[error("malformed share token payload: {0}")]
    MalformedPayload(String),
}

// share_token_encode / share_token_decode_and_verify / share_token_decode_and_verify_no_expiry 将在 Step 4 实现。

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn future_exp() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
            + 3600
    }

    #[test]
    fn roundtrip_preserves_payload() {
        let secret = b"share-secret-0123456789abcdef";
        let p = ShareTokenPayload { v: 1, file_id: "01HZXABCDEFGHJKMNPQRSTVWXY".to_string(), exp: future_exp() };
        let token = share_token_encode(&p, secret);
        let decoded = share_token_decode_and_verify(&token, secret).unwrap();
        assert_eq!(decoded, p);
    }

    #[test]
    fn wrong_secret_rejected() {
        let p = ShareTokenPayload { v: 1, file_id: "01HZX".to_string(), exp: future_exp() };
        let token = share_token_encode(&p, b"secret-a");
        assert_eq!(
            share_token_decode_and_verify(&token, b"secret-b"),
            Err(ShareTokenError::InvalidSignature)
        );
    }

    #[test]
    fn expired_rejected() {
        let p = ShareTokenPayload { v: 1, file_id: "01HZX".to_string(), exp: 1 };
        let token = share_token_encode(&p, b"secret");
        assert_eq!(
            share_token_decode_and_verify(&token, b"secret"),
            Err(ShareTokenError::Expired)
        );
    }

    #[test]
    fn unsupported_version_rejected() {
        let p = ShareTokenPayload { v: 99, file_id: "01HZX".to_string(), exp: future_exp() };
        let token = share_token_encode(&p, b"secret");
        assert_eq!(
            share_token_decode_and_verify(&token, b"secret"),
            Err(ShareTokenError::UnsupportedVersion)
        );
    }

    #[test]
    fn malformed_encoding_rejected() {
        assert_eq!(
            share_token_decode_and_verify("!!!not-base64!!!", b"secret"),
            Err(ShareTokenError::InvalidEncoding)
        );
    }
}
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cargo test -p bcs-domain share::tests 2>&1 | tail -20`
Expected: 编译失败（`share_token_encode` / `share_token_decode_and_verify` 未定义）。

- [ ] **Step 4: 实现 share_token_encode / share_token_decode_and_verify**

Replace the placeholder comment in `share.rs` with（复刻 `invite.rs` 编码方案：`base64url_no_pad(serde_json(payload) || HMAC-SHA256(payload))`，末 32 字节为 MAC）：

```rust
use base64::Engine;
use hmac::{Hmac, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

pub fn share_token_encode(payload: &ShareTokenPayload, secret: &[u8]) -> String {
    let payload_bytes =
        serde_json::to_vec(payload).expect("ShareTokenPayload is always serializable");
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(&payload_bytes);
    let signature = mac.finalize().into_bytes();

    let mut combined = payload_bytes;
    combined.extend_from_slice(&signature);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&combined)
}

pub fn share_token_decode_and_verify(
    token: &str,
    secret: &[u8],
) -> Result<ShareTokenPayload, ShareTokenError> {
    let payload = share_token_decode_and_verify_no_expiry(token, secret)?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if payload.exp < now {
        return Err(ShareTokenError::Expired);
    }
    Ok(payload)
}

fn share_token_decode_and_verify_no_expiry(
    token: &str,
    secret: &[u8],
) -> Result<ShareTokenPayload, ShareTokenError> {
    use base64::Engine;
    let combined = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(token)
        .map_err(|_| ShareTokenError::InvalidEncoding)?;
    if combined.len() < HMAC_LEN {
        return Err(ShareTokenError::InvalidEncoding);
    }
    let (payload_bytes, signature) = combined.split_at(combined.len() - HMAC_LEN);

    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(payload_bytes);
    mac.verify_slice(signature)
        .map_err(|_| ShareTokenError::InvalidSignature)?;

    let payload: ShareTokenPayload =
        serde_json::from_slice(payload_bytes).map_err(|e| ShareTokenError::MalformedPayload(e.to_string()))?;
    if payload.v != CURRENT_VERSION {
        return Err(ShareTokenError::UnsupportedVersion);
    }
    Ok(payload)
}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cargo test -p bcs-domain share::tests 2>&1 | tail -20`
Expected: 5 passed.

- [ ] **Step 6: 加 `ActorRef`**

`crates/contracts/bcs-domain/src/actor.rs` 现有 `ActorKind` enum（`Bot`/`Human`，serde `rename_all="lowercase"`）。在其后追加：

```rust
/// A discriminated reference to an actor (bot or human). Used where a single
/// value must carry both *who* (`actor_id`) and *what kind* (`actor_kind`),
/// e.g. `SessionFile.owner`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ActorRef {
    pub actor_kind: ActorKind,
    pub actor_id: String,
}
```

- [ ] **Step 7: Create `session_file.rs`**

```rust
//! Session workspace file domain types.

use serde::{Deserialize, Serialize};

use crate::actor::ActorRef;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "PascalCase")]
pub enum FileStatus {
    #[default]
    Pending,
    Ready,
    Deleting,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionFile {
    pub file_id: String,
    pub session_id: String,
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,
    /// v1 always None (integrity not verified); backends may populate later.
    pub sha256: Option<String>,
    pub owner: ActorRef,
    pub storage_backend: String,
    /// Serialized `UploadHandle` (Pending) or `StorageHandle` (Ready).
    /// Opaque to clients; never returned over the wire.
    pub object_handle: String,
    pub status: FileStatus,
    pub created_at: u64,
    pub updated_at: u64,
}

/// Allocate a globally-unique file id (ULID, 26-char Crockford base32).
pub fn new_file_id() -> String {
    ulid::Ulid::new().to_string()
}
```

- [ ] **Step 8: re-export**

`crates/contracts/bcs-domain/src/lib.rs`：在现有 `pub use actor::{ActorKind, ...};` 行追加 `ActorRef`；并在 invite/register re-export 旁加 share。

> **执行期前置 grep**：`actor.rs` 现有 re-export 列表的实际符号未知 —— 先 `grep -n "pub use actor" crates/contracts/bcs-domain/src/lib.rs` 取真实列表，**在该列表后追加 `ActorRef`**，不要臆造 `ActorStatus`/`EnsureHumanResult`/`EnsureOwnerEdgesResult`/`RelationEdge` 等未必存在的符号（若它们不存在会导致编译失败）。

```rust
// 形如（以 grep 结果为准；仅新增 ActorRef 与 session_file、share 两行）：
pub use actor::{ActorKind, ActorRef /* 其余按现有列表保留 */};
pub use session_file::{FileStatus, SessionFile, new_file_id};
// 函数在 share.rs 已命名为 share_token_encode / share_token_decode_and_verify，直接 re-export，无需 `as` 别名：
pub use share::{ShareTokenError, ShareTokenPayload, share_token_decode_and_verify, share_token_encode};
```

- [ ] **Step 9: 编译 + 全量 domain 测试**

Run: `cargo test -p bcs-domain 2>&1 | tail -20`
Expected: PASS（含 share 5 用例 + 现有用例不回归）。

- [ ] **Step 10: Commit**

```bash
git add src/bcs/Cargo.toml src/bcs/crates/contracts/bcs-domain
git commit -m "feat(bcs-domain): add SessionFile, FileStatus, ActorRef, share token"
```

---

## Task 2: 迁移 006 — `bcs_session_files` 表

MySQL 迁移 + SQLite parity（BCS 自带 SQLite 实现，DDL 在 `crates/bootstrap/bcs/src/migrations.rs`）。镜像 §3.7/§3.8 约定（`id`/`gmt_create`/`gmt_modified`/`env` 四元组 + utf8mb4 + `uk_`/`idx_` 命名）。

**Files:**
- Create: `migrations/mysql/006_session_files.sql`
- Modify: `crates/bootstrap/bcs/src/migrations.rs`（SQLite `bcs_session_files` CREATE TABLE + ensure 函数 + 注册到 apply 顺序）

**Interfaces:** 无（schema only）。后续 Task 4/5 的 mysql.rs 依赖此表列名（必须与 SQL 完全一致）。

- [ ] **Step 1: 写 MySQL 迁移**

Create `migrations/mysql/006_session_files.sql`：

```sql
-- Session workspace files: per-session shared file metadata. The byte payload
-- lives in a StoragePlugin backend (local fs / baas / OSS); BCS DB is the only
-- authoritative source for list/metadata. object_handle is the serialized
-- UploadHandle (Pending) / StorageHandle (Ready) — opaque, never exposed to
-- clients. sha256 is NULL in v1 (integrity stub).
CREATE TABLE IF NOT EXISTS `bcs_session_files` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `file_id` varchar(32) NOT NULL,
  `session_id` varchar(64) NOT NULL,
  `owner_actor_kind` varchar(16) NOT NULL,
  `owner_actor_id` varchar(256) NOT NULL,
  `file_name` varchar(512) NOT NULL,
  `mime_type` varchar(256) NOT NULL,
  `size` bigint(20) unsigned NOT NULL,
  `sha256` char(64) DEFAULT NULL,
  `storage_backend` varchar(32) NOT NULL,
  `object_handle` text NOT NULL,
  `status` varchar(16) NOT NULL,
  `created_at` bigint(20) unsigned NOT NULL,
  `updated_at` bigint(20) unsigned NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_session_file` (`env`, `session_id`, `file_id`),
  KEY `idx_session_files_session` (`env`, `session_id`, `created_at`)
) DEFAULT CHARSET = utf8mb4;
```

- [ ] **Step 2: SQLite parity**

`crates/bootstrap/bcs/src/migrations.rs` 现有每个表有 `CREATE TABLE IF NOT EXISTS` 字符串 + ensure 函数（参见 `bcs_session_participants` 定义与 `ensure_*` / `sqlite_table_columns` 模式）。新增 SQLite 版本（类型映射：`bigint(20) unsigned`→`INTEGER`、`varchar(N)`→`TEXT`、`datetime`→`TEXT`、`AUTO_INCREMENT` 为主键 `INTEGER PRIMARY KEY AUTOINCREMENT`）。在 `migrations.rs` 内：

  1. 加一个常量 `SQLITE_CREATE_BCS_SESSION_FILES`（CREATE TABLE IF NOT EXISTS bcs_session_files (...)），列与 MySQL 对齐（SQLite 用 `INTEGER PRIMARY KEY AUTOINCREMENT`，时间戳列用 `TEXT DEFAULT (strftime('%s','now'))` 或对齐现有 `bcs_session_participants` 的 SQLite 写法 —— **实现时先读现有 SQLite table 定义照抄时间戳写法以保持一致**）。
  2. 在 apply 顺序函数（现有 `apply_migrations` / version 应用入口，参照 `migrations.rs` 内对 004/005 的注册点）注册 version `6` 对应此表创建。
  3. 加 `ensure_bcs_session_files(...)`（若已存在则跳过），风格镜像现有 `ensure_*`。

SQLite 版表 DDL（实现时核对现有 SQLite 时间戳写法后落字）：

```sql
CREATE TABLE IF NOT EXISTS bcs_session_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  env TEXT NOT NULL,
  file_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  owner_actor_kind TEXT NOT NULL,
  owner_actor_id TEXT NOT NULL,
  file_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT,
  storage_backend TEXT NOT NULL,
  object_handle TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_session_file ON bcs_session_files (env, session_id, file_id);
CREATE INDEX IF NOT EXISTS idx_session_files_session ON bcs_session_files (env, session_id, created_at);
```

> **实现注意**：先 `grep -n "bcs_session_participants" crates/bootstrap/bcs/src/migrations.rs` 读现有 SQLite 时间戳列与 ensure 函数体例，**照抄**其写法（`CURRENT_TIMESTAMP` vs `strftime`、`ON UPDATE` 模拟方式），不要自创。SQLite 无 `ON UPDATE` —— 与现有表保持同一约定（如有用触发器或应用层维护 `gmt_modified`，照搬）。

- [ ] **Step 3: 编译 bootstrap**

Run: `cargo build -p bcs 2>&1 | tail -20`
Expected: 编译通过。

- [ ] **Step 4: 迁移自检（MySQL/SQLite 各一）**

Run（SQLite，in-process）：`cargo test -p bcs migrations -- --nocapture 2>&1 | tail -30`（若现有迁移有 in-process SQLite conformance 测试，确认 `bcs_session_files` 表存在）。
Run（MySQL，按 `migrations/README.md`：需 `bcs-admin db migrate --check-db --apply`）—— 若无本地 MySQL，跳过 apply 仅做 SQL 语法 lint：`cargo test -p bcs-storage-local 2>&1 | tail -5`（占位，待 Task 7）。此处只确认 SQLite parity 编译通过 + 迁移注册无冲突。

- [ ] **Step 5: Commit**

```bash
git add src/bcs/migrations/mysql/006_session_files.sql src/bcs/crates/bootstrap/bcs/src/migrations.rs
git commit -m "feat(bcs): migration 006 bcs_session_files (mysql + sqlite parity)"
```

---

## Task 3: `bcs-storage-api` trait crate + `FakeStoragePlugin` + 契约测试

镜像 `bcs-db-api`/`bcs-cache-api` crate 布局（minimal Cargo.toml，trait + 错误 + 类型在同一 `lib.rs` 风格），但依 spec §3 引入完整辅助类型、`ByteStream`、`FakeStoragePlugin`（**注意**：这是 spec 明确要求的有意偏离 —— 现有 db/cache trait crate 内不放 fake，但本 spec 要求 `bcs-storage-api` 内置 `FakeStoragePlugin` 以供 service/HTTP 层测试复用，覆盖含 multipart 在内的所有路径）。

**Files:**
- Create: `crates/plugin-api/bcs-storage-api/Cargo.toml`
- Create: `crates/plugin-api/bcs-storage-api/src/lib.rs`（trait + 类型 + 错误）
- Create: `crates/plugin-api/bcs-storage-api/src/fake.rs`（`FakeStoragePlugin`）
- Create: `crates/plugin-api/bcs-storage-api/src/contract.rs`（契约测试套件，`pub async fn assert_storage_plugin_conforms(plugin: Arc<dyn StoragePlugin>, caps_expected: StorageCapabilities)`，由各后端 crate 在 `#[tokio::test]` 内 `.await`）
- Modify: `Cargo.toml`（workspace members + `bcs-storage-api = { path = ... }` workspace dep）

**Interfaces:**
- Produces（全部 `pub`，被 Task 5/6/7/8 依赖）：
  - `StoragePlugin` trait（签名见 Step 1，逐字对齐 spec §3）
  - `StorageCapabilities`、`UploadPrepareRequest`、`PreparedUpload`、`ClientUploadTarget`、`UploadPartUrl`、`UploadMode`、`UploadHandle`、`StorageHandle`、`PresignGetTicket`、`StorageObjectMeta`、`StorageHealth`、`ByteStream`/`ByteStreamTrait`
  - `StorageError { InvalidInput(String), NotFound, Conflict(String), Unsupported(&'static str), Backend(anyhow::Error) }`
  - `FakeStoragePlugin`（in-memory，`FakeStoragePlugin::new(caps)`）
  - `contract::assert_storage_plugin_conforms(plugin, caps_expected)`
- Consumes: Task 1 的 `bcs_domain`（不直接 —— 本 crate 不依赖 bcs-domain 以保持 trait crate minimal；`UploadHandle.backend_handle: serde_json::Value` 承载后端特定信息）。

> **决策**：`bcs-storage-api` **不**依赖 `bcs-domain`（保持 trait crate 最小，对齐 db/cache-api）。`key` 派生由 service 层（Task 6）用 `session_id`/`file_id` 拼出字符串后透传给 trait。

- [ ] **Step 1: 写 Cargo.toml + trait/类型定义**

Create `crates/plugin-api/bcs-storage-api/Cargo.toml`：

```toml
[package]
name         = "bcs-storage-api"
version.workspace      = true
edition.workspace      = true
license.workspace      = true
repository.workspace   = true
rust-version.workspace = true

[lints]
workspace = true

[dependencies]
async-trait = { workspace = true }
thiserror   = { workspace = true }
anyhow      = { workspace = true }
bytes       = { workspace = true }
futures     = { workspace = true }
serde       = { workspace = true, features = ["derive"] }
serde_json  = { workspace = true }

[dev-dependencies]
tokio = { workspace = true, features = ["macros", "rt-multi-thread"] }
```

Create `crates/plugin-api/bcs-storage-api/src/lib.rs`，逐字落 spec §3 的 trait 与辅助类型（含 serde derive 与 `UploadMode` 的 `rename_all = "lowercase"`）：

```rust
//! Pluggable storage backend trait for the BCS session workspace.
//!
//! Mirrors the `DbPlugin` / `CachePlugin` pattern: concrete backends implement
//! `StoragePlugin`; `SessionFileService` depends only on the trait. A
//! `FakeStoragePlugin` (in-memory) lives in this crate for service/HTTP test
//! reuse — covering single + multipart paths.

pub mod contract;
pub mod fake;

use async_trait::async_trait;
use bytes::Bytes;
use futures::Stream;
use serde::{Deserialize, Serialize};

pub type ByteStream = Box<dyn ByteStreamTrait + Send + Unpin>;

pub trait ByteStreamTrait: Stream<Item = Result<Bytes, std::io::Error>> + Send + Unpin {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct StorageCapabilities {
    pub supports_presign_put: bool,
    pub supports_presign_download: bool,
    pub supports_stream_put: bool,
    pub supports_stream_get: bool,
    pub max_object_size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UploadPrepareRequest {
    pub key: String,
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,
    pub ttl_secs: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PreparedUpload {
    pub handle: UploadHandle,
    pub client_target: ClientUploadTarget,
    pub expires_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ClientUploadTarget {
    /// presign_put backend: direct backend URL(s); bytes bypass BCS.
    Direct {
        mode: UploadMode,
        url: Option<String>,                 // Some for Single
        parts: Option<Vec<UploadPartUrl>>,   // Some for Multipart
        part_size: Option<u64>,
        part_count: Option<u32>,
    },
    /// non-presign backend (local): BCS serves its own `PUT .../content` proxy.
    ProxyViaBcs,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum UploadMode {
    Single,
    Multipart,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UploadPartUrl {
    pub part_number: u16,
    pub url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UploadHandle {
    pub backend: &'static str,
    pub key: String,
    pub backend_handle: serde_json::Value,
    pub expires_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StorageHandle {
    pub backend: &'static str,
    pub key: String,
    pub backend_handle: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PresignGetTicket {
    pub download_url: String,
    pub expires_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StorageObjectMeta {
    pub key: String,
    pub size: u64,
    pub sha256: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StorageHealth {
    pub ok: bool,
    pub detail: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum StorageError {
    #[error("invalid input: {0}")]
    InvalidInput(String),
    #[error("object not found")]
    NotFound,
    #[error("state conflict: {0}")]
    Conflict(String),
    #[error("unsupported by backend {0}")]
    Unsupported(&'static str),
    #[error("backend error")]
    Backend(#[from] anyhow::Error),
}

#[async_trait]
pub trait StoragePlugin: Send + Sync + 'static {
    fn backend_name(&self) -> &'static str;
    /// Cheap, sync, no IO. Returns a value precomputed at construction.
    fn capabilities(&self) -> StorageCapabilities;

    async fn prepare_upload(&self, req: UploadPrepareRequest) -> Result<PreparedUpload, StorageError>;
    async fn stream_upload(
        &self,
        handle: &UploadHandle,
        part_number: Option<u16>,
        body: ByteStream,
    ) -> Result<(), StorageError>;
    async fn complete_upload(&self, handle: &UploadHandle) -> Result<StorageObjectMeta, StorageError>;
    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError>;

    async fn get_stream(&self, handle: &StorageHandle) -> Result<ByteStream, StorageError>;
    async fn presign_get(
        &self,
        handle: &StorageHandle,
        ttl_secs: u64,
    ) -> Result<PresignGetTicket, StorageError>;
    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError>;

    async fn health_check(&self) -> Result<StorageHealth, StorageError>;
}
```

- [ ] **Step 2: 写 `FakeStoragePlugin` 测试（失败）**

Create `crates/plugin-api/bcs-storage-api/src/fake.rs` 内置 `#[cfg(test)] mod tests` 覆盖：三阶段单片往返、分段往返、abort 后 NotFound、delete 幂等 + 删后 NotFound、delete 已不存在对象 Ok、`capabilities()` 同步无 IO。

```rust
//! In-memory `StoragePlugin` for tests. Covers single + multipart paths.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bytes::Bytes;
use futures::StreamExt;

use crate::{
    ByteStream, ByteStreamTrait, ClientUploadTarget, PreparedUpload, PresignGetTicket,
    StorageCapabilities, StorageError, StorageHandle, StorageHealth, StorageObjectMeta,
    StoragePlugin, UploadHandle, UploadMode, UploadPrepareRequest,
};

#[derive(Clone, Default)]
pub struct FakeStoragePlugin {
    caps: StorageCapabilities,
    objects: Arc<Mutex<HashMap<String, Bytes>>>, // key -> final bytes
    staging: Arc<Mutex<HashMap<String, HashMap<Option<u16>, Bytes>>>>, // key -> {part_number: bytes}
}

impl FakeStoragePlugin {
    pub fn new(caps: StorageCapabilities) -> Self {
        Self { caps, ..Default::default() }
    }
}

// Stream adapter wrapping a Vec<Bytes> for ByteStream.
struct VecStream { chunks: std::vec::IntoIter<Bytes> }
impl futures::Stream for VecStream {
    type Item = Result<Bytes, std::io::Error>;
    fn poll_next(mut self: std::pin::Pin<&mut Self>, cx: &mut std::task::Context<'_>)
        -> std::task::Poll<Option<Self::Item>> {
        match self.chunks.next() {
            Some(b) => std::task::Poll::Ready(Some(Ok(b))),
            None => std::task::Poll::Ready(None),
        }
    }
}
impl ByteStreamTrait for VecStream {}

fn make_stream(b: Bytes) -> ByteStream {
    Box::new(VecStream { chunks: vec![b].into_iter() })
}

#[async_trait]
impl StoragePlugin for FakeStoragePlugin {
    fn backend_name(&self) -> &'static str { "fake" }
    fn capabilities(&self) -> StorageCapabilities { self.caps }

    async fn prepare_upload(&self, req: UploadPrepareRequest) -> Result<PreparedUpload, StorageError> {
        let handle = UploadHandle {
            backend: "fake",
            key: req.key.clone(),
            backend_handle: serde_json::json!({ "size": req.size }),
            expires_at: req.ttl_secs,
        };
        // Fake is always a proxy backend (stream_upload receives bytes),
        // regardless of caps — keeps the fake usable for both routing paths.
        Ok(PreparedUpload {
            handle,
            client_target: ClientUploadTarget::ProxyViaBcs,
            expires_at: req.ttl_secs,
        })
    }

    async fn stream_upload(
        &self,
        handle: &UploadHandle,
        part_number: Option<u16>,
        mut body: ByteStream,
    ) -> Result<(), StorageError> {
        let mut buf = Vec::new();
        while let Some(chunk) = body.next().await {
            buf.extend_from_slice(&chunk?);
        }
        self.staging
            .lock().unwrap()
            .entry(handle.key.clone())
            .or_default()
            .insert(part_number, Bytes::from(buf));
        Ok(())
    }

    async fn complete_upload(&self, handle: &UploadHandle) -> Result<StorageObjectMeta, StorageError> {
        let mut parts = self.staging.lock().unwrap().remove(&handle.key)
            .ok_or_else(|| StorageError::Conflict("no staged bytes".into()))?;
        let size: u64 = parts.values().map(|b| b.len() as u64).sum();
        let mut combined = Vec::with_capacity(size as usize);
        let mut keys: Vec<Option<u16>> = parts.keys().cloned().collect();
        keys.sort();
        for k in keys { combined.extend_from_slice(parts.remove(&k).unwrap()); }
        let bytes = Bytes::from(combined);
        self.objects.lock().unwrap().insert(handle.key.clone(), bytes.clone());
        Ok(StorageObjectMeta { key: handle.key.clone(), size, sha256: None })
    }

    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError> {
        self.staging.lock().unwrap().remove(&handle.key);
        self.objects.lock().unwrap().remove(&handle.key);
        Ok(())
    }

    async fn get_stream(&self, handle: &StorageHandle) -> Result<ByteStream, StorageError> {
        let bytes = self.objects.lock().unwrap().get(&handle.key).cloned()
            .ok_or(StorageError::NotFound)?;
        Ok(make_stream(bytes))
    }

    async fn presign_get(&self, handle: &StorageHandle, ttl_secs: u64) -> Result<PresignGetTicket, StorageError> {
        Ok(PresignGetTicket {
            download_url: format!("fake://{}", handle.key),
            expires_at: ttl_secs,
        })
    }

    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError> {
        self.objects.lock().unwrap().remove(&handle.key); // idempotent
        Ok(())
    }

    async fn health_check(&self) -> Result<StorageHealth, StorageError> {
        Ok(StorageHealth { ok: true, detail: None })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::UploadPrepareRequest;

    fn caps() -> StorageCapabilities {
        StorageCapabilities {
            supports_presign_put: false, supports_presign_download: false,
            supports_stream_put: true, supports_stream_get: true,
            max_object_size: 1024 * 1024 * 1024,
        }
    }
    fn req(key: &str, size: u64) -> UploadPrepareRequest {
        UploadPrepareRequest { key: key.to_string(), file_name: "f".into(), mime_type: "application/octet-stream".into(), size, ttl_secs: 300 }
    }

    #[tokio::test]
    async fn single_roundtrip() {
        let p = FakeStoragePlugin::new(caps());
        let prep = p.prepare_upload(req("k1", 3)).await.unwrap();
        let payload = Bytes::from_static(b"abc");
        p.stream_upload(&prep.handle, None, make_stream(payload.clone())).await.unwrap();
        let meta = p.complete_upload(&prep.handle).await.unwrap();
        assert_eq!(meta.size, 3);
        let h = StorageHandle { backend: "fake", key: "k1".into(), backend_handle: serde_json::Value::Null };
        let mut s = p.get_stream(&h).await.unwrap();
        let mut got = Vec::new();
        while let Some(c) = s.next().await { got.extend_from_slice(&c.unwrap()); }
        assert_eq!(got, payload.as_ref());
    }

    #[tokio::test]
    async fn multipart_roundtrip() {
        let p = FakeStoragePlugin::new(caps());
        let prep = p.prepare_upload(req("k2", 6)).await.unwrap();
        p.stream_upload(&prep.handle, Some(1), make_stream(Bytes::from_static(b"aaa"))).await.unwrap();
        p.stream_upload(&prep.handle, Some(2), make_stream(Bytes::from_static(b"bbb"))).await.unwrap();
        let meta = p.complete_upload(&prep.handle).await.unwrap();
        assert_eq!(meta.size, 6);
        let h = StorageHandle { backend: "fake", key: "k2".into(), backend_handle: serde_json::Value::Null };
        let mut s = p.get_stream(&h).await.unwrap();
        let mut got = Vec::new();
        while let Some(c) = s.next().await { got.extend_from_slice(&c.unwrap()); }
        assert_eq!(got, b"aaabbb");
    }

    #[tokio::test]
    async fn abort_makes_object_not_found() {
        let p = FakeStoragePlugin::new(caps());
        let prep = p.prepare_upload(req("k3", 3)).await.unwrap();
        p.stream_upload(&prep.handle, None, make_stream(Bytes::from_static(b"abc"))).await.unwrap();
        p.abort_upload(&prep.handle).await.unwrap();
        let h = StorageHandle { backend: "fake", key: "k3".into(), backend_handle: serde_json::Value::Null };
        assert!(matches!(p.get_stream(&h).await, Err(StorageError::NotFound)));
    }

    #[tokio::test]
    async fn delete_is_idempotent_and_makes_not_found() {
        let p = FakeStoragePlugin::new(caps());
        let prep = p.prepare_upload(req("k4", 3)).await.unwrap();
        p.stream_upload(&prep.handle, None, make_stream(Bytes::from_static(b"abc"))).await.unwrap();
        p.complete_upload(&prep.handle).await.unwrap();
        let h = StorageHandle { backend: "fake", key: "k4".into(), backend_handle: serde_json::Value::Null };
        p.delete(&h).await.unwrap();
        p.delete(&h).await.unwrap(); // idempotent Ok
        assert!(matches!(p.get_stream(&h).await, Err(StorageError::NotFound)));
    }

    #[tokio::test]
    async fn delete_missing_object_ok() {
        let p = FakeStoragePlugin::new(caps());
        let h = StorageHandle { backend: "fake", key: "never".into(), backend_handle: serde_json::Value::Null };
        assert!(p.delete(&h).await.is_ok());
    }

    #[test]
    fn capabilities_is_sync_and_cheap() {
        let p = FakeStoragePlugin::new(caps());
        // Calling capabilities() off an async context proves no IO is needed.
        let c = p.capabilities();
        assert_eq!(c, caps());
    }
}
```

- [ ] **Step 3: 运行测试确认失败（crate 未知）**

先注册 crate。`src/bcs/Cargo.toml`：在 `[workspace] members` 表加 `"crates/plugin-api/bcs-storage-api",`；在 `[workspace.dependencies]` 加 `bcs-storage-api = { path = "crates/plugin-api/bcs-storage-api" }`。

Run: `cargo test -p bcs-storage-api 2>&1 | tail -30`
Expected: 初次可能因 `contract.rs` 缺失报错 —— 先在 Step 4 创建 `contract.rs`，再跑通。

- [ ] **Step 4: 写契约测试套件 `contract.rs`**

契约套件以 **`pub async fn assert_storage_plugin_conforms(plugin, caps)`** 暴露，每个后端 crate（Task 7 local）在自己的 `#[tokio::test]` 内 `.await` 它。它复用 spec §3 契约列表。因 trait 方法对 `ByteStream` 入参，套件用 fake-assist bytes 构造流。**采用 async 签名（而非内部 `tokio::runtime::Runtime::new().block_on`）的原因**：`contract.rs` 是 `pub`（非 `#[cfg(test)]`）模块，若内部调用 `tokio::runtime` 则需 `tokio` 进入正式 `[dependencies]`；改 async 后 `tokio` 只留在 `[dev-dependencies]`，crate 保持轻。完整实现（对 presign 与 proxy 两类后端分支断言）：

```rust
//! Shared contract suite for any `StoragePlugin`. Each backend crate calls
//! `assert_storage_plugin_conforms` from its own integration test.

use std::sync::Arc;

use bytes::Bytes;
use futures::StreamExt;

use crate::{
    ByteStream, ByteStreamTrait, ClientUploadTarget, StorageCapabilities, StorageError,
    StorageHandle, StoragePlugin, UploadPrepareRequest,
};

struct VecStream(std::vec::IntoIter<Bytes>);
impl futures::Stream for VecStream {
    type Item = Result<Bytes, std::io::Error>;
    fn poll_next(mut self: std::pin::Pin<&mut Self>, _cx: &mut std::task::Context<'_>)
        -> std::task::Poll<Option<Self::Item>> {
        std::task::Poll::Ready(self.0.next().map(Ok))
    }
}
impl ByteStreamTrait for VecStream {}
fn stream_of(b: Bytes) -> ByteStream { Box::new(VecStream(vec![b].into_iter())) }

pub async fn assert_storage_plugin_conforms(plugin: Arc<dyn StoragePlugin>, expected_caps: StorageCapabilities) {
    assert_eq!(plugin.capabilities(), expected_caps);
    assert!(!plugin.backend_name().is_empty());

        let key = format!("contract-{}", line!());
        let req = UploadPrepareRequest {
            key: key.clone(), file_name: "f".into(), mime_type: "application/octet-stream".into(),
            size: 5, ttl_secs: 300,
        };
        let prep = plugin.prepare_upload(req).await.unwrap();

    let payload = Bytes::from_static(b"hello");
    match &prep.client_target {
            ClientUploadTarget::ProxyViaBcs => {
                plugin.stream_upload(&prep.handle, None, stream_of(payload.clone())).await.unwrap();
            }
        ClientUploadTarget::Direct { .. } => {
            // presign_put backend: bytes bypass BCS; emulate by staging directly.
            plugin.stream_upload(&prep.handle, None, stream_of(payload.clone())).await.unwrap();
        }
    }
    let meta = plugin.complete_upload(&prep.handle).await.unwrap();
    assert_eq!(meta.size, payload.len() as u64);

    let h = StorageHandle { backend: prep.handle.backend, key: key.clone(), backend_handle: serde_json::Value::Null };
    let mut s = plugin.get_stream(&h).await.unwrap();
    let mut got = Vec::new();
    while let Some(c) = s.next().await { got.extend_from_slice(&c.unwrap()); }
    assert_eq!(got, payload.as_ref());

    // delete idempotent + makes NotFound
    plugin.delete(&h).await.unwrap();
    plugin.delete(&h).await.unwrap();
    assert!(matches!(plugin.get_stream(&h).await, Err(StorageError::NotFound)));
}
```

> 说明：契约套件对 presign/proxy 两条 byte 路径都走 `stream_upload` 注入字节（fake/contract 层不区分；后端真实直传路径由该后端 crate 自己的 integration 测试覆盖，如 local 是 proxy 不需直传）。`presign_get` 的 302 链路由 HTTP 层（Task 8）与后端 crate 测试覆盖，不在通用契约内强制。

- [ ] **Step 5: 暴露 `byte_stream_from_bytes` 公开 helper**

`fake.rs` 现有私有 `make_stream(b: Bytes) -> ByteStream`。将其提取为 `lib.rs` 顶层 `pub fn byte_stream_from_bytes(b: bytes::Bytes) -> ByteStream`，供 HTTP `upload_bytes` handler（Task 8 Step 5）把 `axum::body::Bytes` 包成 `ByteStream` 喂给 service。在 `lib.rs` 加：

```rust
/// Wrap a single `Bytes` chunk as a `ByteStream` for proxy upload ingestion.
pub fn byte_stream_from_bytes(b: bytes::Bytes) -> ByteStream {
    struct OneShot(std::option::IntoIter<bytes::Bytes>);
    impl futures::Stream for OneShot {
        type Item = Result<bytes::Bytes, std::io::Error>;
        fn poll_next(mut self: std::pin::Pin<&mut Self>, _cx: &mut std::task::Context<'_>)
            -> std::task::Poll<Option<Self::Item>> {
            std::task::Poll::Ready(self.0.next().map(Ok))
        }
    }
    impl ByteStreamTrait for OneShot {}
    Box::new(OneShot(vec![b].into_iter()))
}
```

> `fake.rs` 的私有 `make_stream` 改为复用本函数（直接调用 `crate::byte_stream_from_bytes`），避免重复实现 `VecStream`/`OneShot`。

- [ ] **Step 6: 运行全部 storage-api 测试**

Run: `cargo test -p bcs-storage-api 2>&1 | tail -30`
Expected: 6 fake 用例 PASS + 契约套件编译通过 + `byte_stream_from_bytes` 编译通过。

- [ ] **Step 7: Commit**

```bash
git add src/bcs/Cargo.toml src/bcs/crates/plugin-api/bcs-storage-api
git commit -m "feat(bcs-storage-api): StoragePlugin trait, types, FakeStoragePlugin, contract suite"
```

---

## Task 4: Repo 端口 + `SessionFileService` 应用 trait（`bcs-service-api`）

镜像 `SessionRepoPort`（`port/repo/session.rs`）与 `SessionManagementService`（`application/session.rs`）。

**Files:**
- Create: `crates/service-api/bcs-service-api/src/port/repo/session_file.rs`
- Modify: `crates/service-api/bcs-service-api/src/port/repo/mod.rs`（re-export）
- Create: `crates/service-api/bcs-service-api/src/application/session_files.rs`
- Modify: `crates/service-api/bcs-service-api/src/application/mod.rs`（re-export）

**Interfaces:**
- Produces：
  - `SessionFileRepoPort`（trait，签名见 Step 1）
  - `NewSessionFileParams`、`SessionFileListParams`、`SessionFileListPage`（`(items, truncated, next_marker)`）
  - `SessionFileService`（应用 trait，签名见 Step 3）
  - `SessionFileUseCaseError`（`NotFound`/`Forbidden`/`Conflict`/`InvalidInput`/`InvalidState`/`Backend`/`Internal(ServiceError)`，对齐 spec 错误码表）
- Consumes: Task 1 的 `bcs_domain::{SessionFile, FileStatus, ActorRef, ShareTokenPayload, ShareTokenError, new_file_id, share_token_encode, share_token_decode_and_verify}`；Task 3 的 `bcs-storage-api::{StoragePlugin, PreparedUpload, UploadHandle, StorageHandle, StorageObjectMeta, PresignGetTicket, StorageCapabilities}`。

- [ ] **Step 1: 写 `SessionFileRepoPort`**

Create `crates/service-api/bcs-service-api/src/port/repo/session_file.rs`：

```rust
//! Repository port for session file metadata. BCS DB is the sole authoritative
//! source for list/metadata (never the storage backend).

use async_trait::async_trait;

use bcs_domain::{ActorRef, FileStatus, SessionFile};

use crate::ServiceResult;

#[derive(Debug, Clone)]
pub struct NewSessionFileParams {
    pub file_id: String,
    pub session_id: String,
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,
    pub owner: ActorRef,
    pub storage_backend: String,
    pub object_handle: String, // serialized UploadHandle
    pub expires_at: u64,
}

#[derive(Debug, Clone, Default)]
pub struct SessionFileListParams {
    pub prefix: Option<String>,
    pub limit: u32,
    pub marker: Option<String>, // opaque cursor (created_at,file_id)
}

#[derive(Debug, Clone)]
pub struct SessionFileListPage {
    pub items: Vec<SessionFile>,
    pub truncated: bool,
    pub next_marker: Option<String>,
}

#[async_trait]
pub trait SessionFileRepoPort: Send + Sync {
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile>;
    async fn get(&self, session_id: &str, file_id: &str) -> ServiceResult<Option<SessionFile>>;
    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: FileStatus,
        size: u64,
    ) -> ServiceResult<Option<SessionFile>>;
    async fn update_status(
        &self,
        session_id: &str,
        file_id: &str,
        status: FileStatus,
    ) -> ServiceResult<Option<SessionFile>>;
    async fn delete(&self, session_id: &str, file_id: &str) -> ServiceResult<bool>;
    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> ServiceResult<SessionFileListPage>;
    /// Rows that are Pending and past their expires_at (for the Pending sweep).
    async fn list_expired_pending(&self, now: u64, limit: u32) -> ServiceResult<Vec<SessionFile>>;
    async fn delete_all_for_session(&self, session_id: &str) -> ServiceResult<Vec<SessionFile>>;
}
```

`src/port/repo/mod.rs` 加 `pub mod session_file;` 与 `pub use session_file::{NewSessionFileParams, SessionFileListPage, SessionFileListParams, SessionFileRepoPort};`。

- [ ] **Step 2: 写应用层错误类型 + 命令/结果类型**

Create `crates/service-api/bcs-service-api/src/application/session_files.rs` 先放错误与 DTO。

> **执行期前置（必做）**：本文件引用 `bcs_storage_api::{ByteStream, PresignGetTicket, ...}`，需先把 `bcs-storage-api` 依赖加入本 crate 的 `Cargo.toml`（正式 `[dependencies]`）—— 见 Step 4 的依赖修改。**先做 Step 4 的 Cargo.toml 依赖加入，再写本文件**，否则中途 `cargo check` 会因 crate 未解析失败。

```rust
//! Session file workspace application service trait.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use bcs_domain::{ActorRef, SessionFile, ShareTokenError};
// NOTE: 不要写 `use bcs_service_api_internal_exports::*;`（该 crate 不存在）。按编译器提示
// 显式 import 真实符号；本文件需要 `crate::port::repo::{...}` 与 `crate::{ServiceError, ServiceResult}`
// 以及 `bcs_storage_api::{ByteStream, PresignGetTicket}`（后者在 Step 4 依赖加入后可用）。
use crate::port::repo::{NewSessionFileParams, SessionFileListPage, SessionFileListParams};
use crate::{ServiceError, ServiceResult};

#[derive(Debug, thiserror::Error)]
pub enum SessionFileUseCaseError {
    #[error("not found: {0}")]
    NotFound(String),
    #[error("forbidden: {0}")]
    Forbidden(String),
    #[error("invalid input: {0}")]
    InvalidInput(String),
    #[error("payload too large: {0}")]
    PayloadTooLarge(String),
    #[error("invalid transition: {0}")]
    Conflict(String),
    #[error("invalid state: {0}")]
    InvalidState(String),
    #[error("storage backend error")]
    Backend,
    #[error("internal error: {0}")]
    Internal(#[from] ServiceError),
}

impl From<ShareTokenError> for SessionFileUseCaseError {
    fn from(e: ShareTokenError) -> Self {
        use bcs_domain::ShareTokenError::*;
        match e {
            InvalidEncoding | InvalidSignature | UnsupportedVersion | MalformedPayload(_) => {
                SessionFileUseCaseError::InvalidInput(format!("share token: {e}"))
            }
            Expired => SessionFileUseCaseError::InvalidState("share token expired".into()),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrepareUploadCommand {
    pub session_id: String,
    pub file_name: String,
    pub size: u64,
    pub mime_type: String,
    pub caller: ActorRef,
    // NOTE: prepare/upload/list/download are participant-gated (HTTP `ensure_session_member`),
    // NOT owner-gated — no `caller_identities` here. `owner` is recorded from `caller`.
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrepareUploadResult {
    pub file: SessionFile,
    pub client_target_json: serde_json::Value, // wire: §1.2.a / §1.2.b 响应体（去掉 file_id 重复由 handler 拼）
    pub expires_at: u64,
}

/// Mutate (delete/share) authz is done ENTIRELY in the service, fed by values
/// the HTTP layer pre-resolves (caller_identities + session_creator + driver_bot).
/// HTTP fetches `session.created_by` (session_repo via session_management) and
/// `group.driver_bot` (group_management) before constructing this command.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeleteFileCommand {
    pub session_id: String,
    pub file_id: String,
    pub caller: ActorRef,
    pub caller_identities: Vec<String>,        // [caller.actor_id] + owned bot_uuids (HTTP `caller_identities()`)
    pub session_creator: Option<String>,       // session.created_by
    pub driver_bot: Option<String>,            // group.driver_bot
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShareMintCommand {
    pub session_id: String,
    pub file_id: String,
    pub caller: ActorRef,
    pub ttl_seconds: Option<u64>,
    pub caller_identities: Vec<String>,
    pub session_creator: Option<String>,
    pub driver_bot: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShareMintResult {
    pub share_url: String,
    pub share_token: String,
    pub expires_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShareConsumeResult {
    pub file: SessionFile,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadRoute {
    /// presign backend: Some(presigned url + expires_at) -> HTTP 302.
    /// local backend: None -> HTTP streams via get_stream.
    pub presign: Option<bcs_storage_api::PresignGetTicket>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilitiesView {
    pub storage: String,
    pub presign_upload: bool,
    pub presign_download: bool,
    pub max_size: u64,
}
```

> 注意：`use bcs_service_api_internal_exports::*;` 是占位提示——实际不要写这行；按编译器提示 import 真实符号。`bcs-storage-api` 需作为 `bcs-service-api` 的依赖加入（见 Step 4）。

- [ ] **Step 3: 写 `SessionFileService` trait**

在同一文件追加：

```rust
#[async_trait]
pub trait SessionFileService: Send + Sync {
    async fn capabilities(&self) -> CapabilitiesView;

    async fn prepare_upload(
        &self,
        cmd: PrepareUploadCommand,
    ) -> Result<PrepareUploadResult, SessionFileUseCaseError>;

    /// Proxy-backend byte ingestion (local). Presign backends never call this.
    async fn stream_upload(
        &self,
        session_id: &str,
        file_id: &str,
        part_number: Option<u16>,
        body: bcs_storage_api::ByteStream,
        content_length: u64,
    ) -> Result<(), SessionFileUseCaseError>;

    async fn complete_upload(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> Result<SessionFile, SessionFileUseCaseError>;

    async fn delete_file(
        &self,
        cmd: DeleteFileCommand,
    ) -> Result<(), SessionFileUseCaseError>;

    async fn get(&self, session_id: &str, file_id: &str) -> Result<SessionFile, SessionFileUseCaseError>;

    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> Result<SessionFileListPage, SessionFileUseCaseError>;

    /// Returns the download route for a Ready file.
    async fn download_route(
        &self,
        session_id: &str,
        file_id: &str,
        ttl_secs: Option<u64>,
    ) -> Result<(SessionFile, DownloadRoute), SessionFileUseCaseError>;

    async fn share_mint(
        &self,
        cmd: ShareMintCommand,
    ) -> Result<ShareMintResult, SessionFileUseCaseError>;

    /// Verify share token (no session auth), return the file (must be Ready).
    async fn share_consume(
        &self,
        session_id: &str,
        token: &str,
    ) -> Result<ShareConsumeResult, SessionFileUseCaseError>;

    /// Return a streaming body for a Ready file (local / fallback).
    async fn get_stream(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> Result<(SessionFile, bcs_storage_api::ByteStream), SessionFileUseCaseError>;

    /// Sweep Pending rows past expires_at -> Failed + abort_upload. Called by a timer (Task 11).
    async fn sweep_expired_pending(&self) -> Result<u64, SessionFileUseCaseError>;

    /// Best-effort cleanup of all files in a session (called by delete_session hook).
    async fn delete_all_for_session(&self, session_id: &str) -> Result<u64, SessionFileUseCaseError>;
}
```

`src/application/mod.rs` 加 `pub mod session_files;` 与 `pub use session_files::*;`（或精确 re-export trait 名）。

- [ ] **Step 4: 加依赖 + 编译**

`crates/service-api/bcs-service-api/Cargo.toml` 的 `[dependencies]` 加 `bcs-storage-api = { workspace = true }`（service-api 已有 `bcs-domain`、`async-trait`、`thiserror`、`serde`、`serde_json`）。

Run: `cargo build -p bcs-service-api 2>&1 | tail -30`
Expected: 编译通过（trait/类型定义不依赖未实现符号）。修正 Step 2 中占位 import。

- [ ] **Step 5: Commit**

```bash
git add src/bcs/Cargo.toml src/bcs/crates/service-api/bcs-service-api
git commit -m "feat(bcs-service-api): SessionFileRepoPort + SessionFileService trait + DTOs"
```

---

## Task 5: `bcs-session-file-store`（memory + mysql）

镜像 `bcs-session-store`：`lib.rs` + `memory.rs` + `mysql.rs` + `tests/conformance.rs`。store crate **通过 `bcs-db-api::{DbPlugin, DbStatement, DbValue, DbRow, DbSqlFlavor}` 访问数据**，不直接用 `mysql_async`。

**Files:**
- Create: `crates/services/bcs-session-file-store/Cargo.toml`
- Create: `crates/services/bcs-session-file-store/src/lib.rs`
- Create: `crates/services/bcs-session-file-store/src/memory.rs`
- Create: `crates/services/bcs-session-file-store/src/mysql.rs`
- Create: `crates/services/bcs-session-file-store/tests/conformance.rs`
- Modify: `Cargo.toml`（workspace members + `bcs-session-file-store` workspace dep）

**Interfaces:**
- Produces：`MemorySessionFileRepo`、`MySqlSessionFileStore`（均 `impl SessionFileRepoPort`）
- Consumes: Task 1 (`bcs_domain`)、Task 4 (`SessionFileRepoPort` in `bcs-service-api`)、`bcs-db-api`

- [ ] **Step 1: Cargo.toml**

```toml
[package]
name = "bcs-session-file-store"
description = "Session file metadata stores for BCS"
version.workspace      = true
edition.workspace      = true
license.workspace      = true
repository.workspace   = true
rust-version.workspace = true

[dependencies]
async-trait    = { workspace = true }
bcs-db-api     = { workspace = true }
bcs-domain     = { workspace = true }
bcs-service-api = { workspace = true }
serde_json     = { workspace = true }
tokio          = { workspace = true }
tracing        = { workspace = true }

[dev-dependencies]
bcs-test-support = { workspace = true }
tokio-test       = "0.4"
sha2             = { workspace = true }
hex              = { workspace = true }

[lints]
workspace = true
```

- [ ] **Step 2: 写 memory 实现 + 单测（失败先行）**

`src/lib.rs`：

```rust
//! Session file repository implementations: memory + mysql.

pub mod memory;
pub mod mysql;

pub use memory::MemorySessionFileRepo;
pub use mysql::MySqlSessionFileStore;
```

`src/memory.rs`：`Arc<RwLock<HashMap<(String,String), SessionFile>>>`（key = `(session_id, file_id)`），`#[derive(Default)]`，实现 `SessionFileRepoPort` 全方法。`list` 按 `created_at` 升序 + `prefix` 前缀 + `limit` + marker 游标 `(created_at,file_id)`。`list_expired_pending` 过滤 `status==Pending && expires_at < now`。`delete_all_for_session` 返回被删全行。

完整 memory 实现（核心方法示例，其余方法为直接 HashMap 操作）：

```rust
use std::collections::HashMap;
use std::sync::{Arc, RwLock};

use async_trait::async_trait;
use bcs_domain::{FileStatus, SessionFile};
use bcs_service_api::port::repo::{
    NewSessionFileParams, SessionFileListPage, SessionFileListParams, SessionFileRepoPort,
};
use bcs_service_api::ServiceResult;

#[derive(Default)]
pub struct MemorySessionFileRepo {
    rows: Arc<RwLock<HashMap<(String, String), SessionFile>>>,
}

impl MemorySessionFileRepo {
    pub fn new() -> Self { Self::default() }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[async_trait]
impl SessionFileRepoPort for MemorySessionFileRepo {
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile> {
        let now = now_secs();
        let row = SessionFile {
            file_id: params.file_id.clone(),
            session_id: params.session_id.clone(),
            file_name: params.file_name,
            mime_type: params.mime_type,
            size: params.size,
            sha256: None,
            owner: params.owner,
            storage_backend: params.storage_backend,
            object_handle: params.object_handle,
            status: FileStatus::Pending,
            created_at: now,
            updated_at: now,
        };
        let key = (params.session_id.clone(), params.file_id.clone());
        let mut rows = self.rows.write().unwrap();
        if rows.contains_key(&key) {
            return Err(bcs_service_api::ServiceError::Internal(anyhow::anyhow!(
                "duplicate session file {} {}", params.session_id, params.file_id
            )));
        }
        rows.insert(key, row.clone());
        Ok(row)
    }

    async fn get(&self, session_id: &str, file_id: &str) -> ServiceResult<Option<SessionFile>> {
        Ok(self.rows.read().unwrap().get(&(session_id.to_string(), file_id.to_string())).cloned())
    }

    async fn update_object_handle_and_status(
        &self, session_id: &str, file_id: &str, object_handle: &str,
        status: FileStatus, size: u64,
    ) -> ServiceResult<Option<SessionFile>> {
        let mut rows = self.rows.write().unwrap();
        let row = rows.get_mut(&(session_id.to_string(), file_id.to_string()));
        if let Some(r) = row {
            r.object_handle = object_handle.to_string();
            r.status = status;
            r.size = size;
            r.updated_at = now_secs();
            Ok(Some(r.clone()))
        } else { Ok(None) }
    }

    async fn update_status(&self, session_id: &str, file_id: &str, status: FileStatus) -> ServiceResult<Option<SessionFile>> {
        let mut rows = self.rows.write().unwrap();
        let row = rows.get_mut(&(session_id.to_string(), file_id.to_string()));
        if let Some(r) = row { r.status = status; r.updated_at = now_secs(); Ok(Some(r.clone())) } else { Ok(None) }
    }

    async fn delete(&self, session_id: &str, file_id: &str) -> ServiceResult<bool> {
        Ok(self.rows.write().unwrap().remove(&(session_id.to_string(), file_id.to_string())).is_some())
    }

    async fn list(&self, session_id: &str, params: SessionFileListParams) -> ServiceResult<SessionFileListPage> {
        let rows = self.rows.read().unwrap();
        let mut items: Vec<SessionFile> = rows.values()
            .filter(|r| r.session_id == session_id)
            .filter(|r| params.prefix.as_deref().map_or(true, |p| r.file_name.starts_with(p)))
            .cloned().collect();
        items.sort_by(|a, b| a.created_at.cmp(&b.created_at).then(a.file_id.cmp(&b.file_id)));
        // marker = "<created_at>:<file_id>"; resume strictly after marker
        if let Some(m) = &params.marker {
            let (mc, mf) = m.split_once(':').unwrap_or(("0", ""));
            let mc: u64 = mc.parse().unwrap_or(0);
            items = items.into_iter().skip_while(|r| !(r.created_at > mc || (r.created_at == mc && r.file_id > mf))).collect();
            // skip_while leaves the first matching item at front; skip it too (it is the marker's own row)
            if items.first().map_or(false, |r| r.created_at == mc && r.file_id == mf) {
                items.remove(0);
            }
        }
        let limit = if params.limit == 0 { 100 } else { params.limit.min(1000) } as usize;
        let truncated = items.len() > limit;
        if truncated { items.truncate(limit); }
        let next_marker = if truncated {
            items.last().map(|r| format!("{}:{}", r.created_at, r.file_id))
        } else { None };
        Ok(SessionFileListPage { items, truncated, next_marker })
    }

    async fn list_expired_pending(&self, now: u64, limit: u32) -> ServiceResult<Vec<SessionFile>> {
        let rows = self.rows.read().unwrap();
        let mut out: Vec<SessionFile> = rows.values()
            .filter(|r| r.status == FileStatus::Pending)
            .filter_map(|r| {
                // expires_at 隐于 object_handle envelope（与 mysql 的
                // JSON_EXTRACT(object_handle,'$.expires_at') < ? 对齐，避免 memory/mysql 语义分叉）。
                let v: serde_json::Value = serde_json::from_str(&r.object_handle).ok()?;
                let exp = v.get("expires_at")?.as_u64()?;
                (exp < now).then(|| r.clone())
            })
            .collect();
        out.truncate(limit as usize);
        Ok(out)
    }

    async fn delete_all_for_session(&self, session_id: &str) -> ServiceResult<Vec<SessionFile>> {
        let mut rows = self.rows.write().unwrap();
        let keys: Vec<(String,String)> = rows.keys().filter(|(s, _)| s == session_id).cloned().collect();
        let removed = keys.into_iter().filter_map(|k| rows.remove(&k)).collect();
        Ok(removed)
    }
}
```

> **memory/mysql `list_expired_pending` 语义对齐**：`SessionFile` 域类型不单独含 `expires_at`（它隐于 `object_handle` envelope 的 `UploadHandle.expires_at`）。memory 与 mysql 都从 `object_handle` 解析 `expires_at`（memory 用 serde_json、mysql 用 `JSON_EXTRACT(...,'$.expires_at')`），谓词一致 `exp < now`——两套实现行为对齐，conformance 测试可同断言。

写 conformance 测试 `tests/conformance.rs`，对 `MemorySessionFileRepo` 跑 insert→get→list→update→delete 全往返 + marker 分页 +过期 pending 列表：

```rust
use bcs_domain::{ActorKind, ActorRef};
use bcs_service_api::port::repo::{
    NewSessionFileParams, SessionFileListParams, SessionFileRepoPort,
};
use bcs_session_file_store::MemorySessionFileRepo;

fn params(id: &str, sess: &str, created_offset: u64) -> NewSessionFileParams {
    NewSessionFileParams {
        file_id: id.into(), session_id: sess.into(), file_name: format!("f-{id}"),
        mime_type: "text/plain".into(), size: 10,
        owner: ActorRef { actor_kind: ActorKind::Human, actor_id: "human_1".into() },
        storage_backend: "local".into(),
        object_handle: serde_json::json!({ "expires_at": 1000u64 + created_offset }).to_string(),
        expires_at: 1000 + created_offset,
    }
}

#[tokio::test]
async fn insert_get_list_update_delete() {
    let repo = MemorySessionFileRepo::new();
    let r = repo.insert(params("f1", "s1", 1)).await.unwrap();
    assert_eq!(repo.get("s1", "f1").await.unwrap().unwrap().file_id, "f1");
    let page = repo.list("s1", SessionFileListParams { prefix: None, limit: 100, marker: None }).await.unwrap();
    assert_eq!(page.items.len(), 1);
    assert!(!page.truncated);
    let updated = repo.update_object_handle_and_status("s1", "f1", "{\"expires_at\":1}", bcs_domain::FileStatus::Ready, 10).await.unwrap().unwrap();
    assert_eq!(updated.status, bcs_domain::FileStatus::Ready);
    assert!(repo.delete("s1", "f1").await.unwrap());
    assert!(repo.get("s1", "f1").await.unwrap().is_none());
}

#[tokio::test]
async fn expired_pending_filtered() {
    let repo = MemorySessionFileRepo::new();
    repo.insert(params("f2", "s2", 5)).await.unwrap(); // expires_at 1005
    let expired = repo.list_expired_pending(2000, 10).await.unwrap();
    assert_eq!(expired.len(), 1);
    let none = repo.list_expired_pending(500, 10).await.unwrap();
    assert!(none.is_empty());
}
```

- [ ] **Step 3: 跑 memory conformance**

Run: `cargo test -p bcs-session-file-store 2>&1 | tail -30`
Expected: 2 用例 PASS。

- [ ] **Step 4: 写 mysql 实现**

`src/mysql.rs`：镜像 `bcs-session-store/src/mysql.rs` 的 `MySqlSessionStore { db: Arc<dyn DbPlugin>, env: String, flavor: DbSqlFlavor }` + `new(db, env)` + `sqlite(db, env)`。SQL 常量用 `?` 占位 + `DbValue::from(...)` 绑定；`gmt_create`/`gmt_modified` 由 DB 默认值维护；`created_at`/`updated_at` 由应用层传 unix 秒。`list` 的 marker 解析 `(created_at, file_id)`，用 `(created_at > ? OR (created_at = ? AND file_id > ?))` 续读。`list_expired_pending` 用 `JSON_EXTRACT(object_handle, '$.expires_at') < ?`（MySQL/OceanBase）—— SQLite 用 `json_extract`。`flavor.on_conflict_*` 不需要（无 upsert）。`is_duplicate_key()` 检测 insert 冲突（理论不应发生，ULID 全局唯一）。

INSERT/SELECT 示例（完整列出核心两方法，其余方法照此映射）：

```rust
use async_trait::async_trait;
use bcs_db_api::{db_get_column, db_get_column_opt, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::{ActorKind, ActorRef, FileStatus, SessionFile};
use bcs_service_api::port::repo::{
    NewSessionFileParams, SessionFileListPage, SessionFileListParams, SessionFileRepoPort,
};
use bcs_service_api::ServiceResult;
use std::sync::Arc;

#[derive(Clone)]
pub struct MySqlSessionFileStore {
    db: Arc<dyn DbPlugin>,
    env: String,
    flavor: DbSqlFlavor,
}

impl MySqlSessionFileStore {
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self { db, env, flavor: DbSqlFlavor::Mysql }
    }
    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self { db, env, flavor: DbSqlFlavor::Sqlite }
    }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn row_to_session(env: &str, r: &DbRow) -> ServiceResult<SessionFile> {
    let actor_kind = match db_get_column::<String>(r, "owner_actor_kind")?.as_str() {
        "Bot" => ActorKind::Bot,
        _ => ActorKind::Human,
    };
    Ok(SessionFile {
        file_id: db_get_column(r, "file_id")?,
        session_id: db_get_column(r, "session_id")?,
        file_name: db_get_column(r, "file_name")?,
        mime_type: db_get_column(r, "mime_type")?,
        size: db_get_column::<u64>(r, "size")?,
        sha256: db_get_column_opt(r, "sha256"),
        owner: ActorRef { actor_kind, actor_id: db_get_column(r, "owner_actor_id")? },
        storage_backend: db_get_column(r, "storage_backend")?,
        object_handle: db_get_column(r, "object_handle")?,
        status: serde_json::from_value(serde_json::Value::String(db_get_column::<String>(r, "status")?))
            .unwrap_or(FileStatus::Pending),
        created_at: db_get_column(r, "created_at")?,
        updated_at: db_get_column(r, "updated_at")?,
    })
}

#[async_trait]
impl SessionFileRepoPort for MySqlSessionFileStore {
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile> {
        // `created_at`/`updated_at` are the business creation time (unix secs) used for
        // list ordering — NOT `expires_at`. `expires_at` lives inside `object_handle` JSON.
        let now = now_secs();
        let stmt = DbStatement::new(format!(
            "INSERT INTO bcs_session_files \
             (env, file_id, session_id, owner_actor_kind, owner_actor_id, file_name, mime_type, \
              size, storage_backend, object_handle, status, created_at, updated_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)",
        ))
        .bind(DbValue::from(self.env.clone()))
        .bind(DbValue::from(params.file_id.clone()))
        .bind(DbValue::from(params.session_id.clone()))
        .bind(DbValue::from(match params.owner.actor_kind { ActorKind::Bot => "Bot", ActorKind::Human => "Human" }.to_string()))
        .bind(DbValue::from(params.owner.actor_id.clone()))
        .bind(DbValue::from(params.file_name.clone()))
        .bind(DbValue::from(params.mime_type.clone()))
        .bind(DbValue::from(params.size))
        .bind(DbValue::from(params.storage_backend.clone()))
        .bind(DbValue::from(params.object_handle.clone()))
        .bind(DbValue::from(now))
        .bind(DbValue::from(now));
        self.db.execute(stmt).await.map_err(bcs_service_api::ServiceError::from)?;
        self.get(&params.session_id, &params.file_id).await?
            .ok_or_else(|| bcs_service_api::ServiceError::Internal(anyhow::anyhow!("insert did not return row")))
    }

    async fn get(&self, session_id: &str, file_id: &str) -> ServiceResult<Option<SessionFile>> {
        let stmt = DbStatement::new(
            "SELECT file_id, session_id, file_name, mime_type, size, sha256, storage_backend, \
             object_handle, status, created_at, updated_at FROM bcs_session_files \
             WHERE env = ? AND session_id = ? AND file_id = ? LIMIT 1",
        )
        .bind(DbValue::from(self.env.clone()))
        .bind(DbValue::from(session_id.to_string()))
        .bind(DbValue::from(file_id.to_string()));
        let rows = self.db.query(stmt).await.map_err(bcs_service_api::ServiceError::from)?;
        Ok(rows.into_iter().next().map(|r| row_to_session(&self.env, &r)).transpose()?)
    }
    // update_object_handle_and_status / update_status / delete / list / list_expired_pending /
    // delete_all_for_session 按 INSERT/SELECT 同款 DbStatement 风格实现（见下要点）。
}
```

其余方法要点（实现者按此落字，bind 数量与 SQL 一致）：
- `update_object_handle_and_status`：`UPDATE bcs_session_files SET object_handle=?, status=?, size=?, updated_at=? WHERE env=? AND session_id=? AND file_id=?` 后 SELECT 回读。
- `update_status`：`UPDATE ... SET status=?, updated_at=? WHERE env=? AND session_id=? AND file_id=?` 后回读。
- `delete`：`DELETE FROM bcs_session_files WHERE env=? AND session_id=? AND file_id=?`，返回 affected>0。
- `list`：`SELECT ... WHERE env=? AND session_id=? [AND file_name LIKE ?] AND (created_at > ? OR (created_at = ? AND file_id > ?)) ORDER BY created_at, file_id LIMIT ?+1`，多读 1 行判断 truncated；`prefix` 转 `prefix%` 用 `LIKE`。limit clamp 100/1000。
- `list_expired_pending`：`SELECT ... WHERE env=? AND status='Pending' AND JSON_EXTRACT(object_handle,'$.expires_at') < ? LIMIT ?`（flavor 名 `json_extract` vs `JSON_EXTRACT` —— 用 `flavor` 判别或统一小写 `json_extract`，MySQL 大小写不敏感均可）。
- `delete_all_for_session`：先 `SELECT ... WHERE env=? AND session_id=?` 收集，再 `DELETE ... WHERE env=? AND session_id=?`，返回被删行。

> **`DbStatement`/`DbValue`/`db_get_column` API 细节**：实现时先 `grep -n "pub fn new\|pub fn bind\|impl DbStatement\|pub fn query\|pub fn execute" crates/plugin-api/bcs-db-api/src/lib.rs` 与 `crates/services/bcs-session-store/src/mysql.rs` 抄真实签名（`DbStatement::new(...).bind(...)` 链式或 builder 风格以现有为准），不要臆造方法名。

- [ ] **Step 5: 注册 crate + 编译**

`src/bcs/Cargo.toml`：members 加 `"crates/services/bcs-session-file-store",`；workspace deps 加 `bcs-session-file-store = { path = "crates/services/bcs-session-file-store" }`。

Run: `cargo build -p bcs-session-file-store 2>&1 | tail -30`
Expected: 编译通过。

- [ ] **Step 6: mysql integration（可选，需本地 DB）**

若仓库现有 mysql integration 测试基建（`bcs-session-store/tests/conformance_session_repo.rs` 用 `bcs-test-support`），照其加 `tests/conformance_mysql.rs` 用 `#[ignore]` 标注、CI/本地按现有方式跑；若无本地 MySQL 则仅保 memory conformance，mysql 留 `#[ignore]` 集成测试。Run memory conformance 确认不回归。

- [ ] **Step 7: Commit**

```bash
git add src/bcs/Cargo.toml src/bcs/crates/services/bcs-session-file-store
git commit -m "feat(bcs-session-file-store): memory + mysql SessionFileRepoPort impls"
```

---

## Task 6: `bcs-session-file` service 实现

`SessionFileServiceImpl` 持有 `Arc<dyn SessionFileRepoPort>`、`Arc<dyn StoragePlugin>`、`Arc<dyn SessionRepoPort>`（用于会话存在校验 + 创建者/driver 鉴权）、share 配置（secret/ttl/base_url）、`max_size`、`multipart_threshold`、`bcs_base_url`（用于 local `upload_url` 派生）。实现能力路由、鉴权、三阶段、删除分流、分享 token、Pending sweep、`delete_all_for_session`。

**Files:**
- Create: `crates/services/bcs-session-file/Cargo.toml`
- Create: `crates/services/bcs-session-file/src/lib.rs`
- Create: `crates/services/bcs-session-file/src/service.rs`（`SessionFileServiceImpl`）
- Create: `crates/services/bcs-session-file/src/authz.rs`（上传者/创建者/driver 判定）
- Modify: `Cargo.toml`（members + `bcs-session-file` workspace dep）

**Interfaces:**
- Produces：`SessionFileServiceImpl` + `SessionFileServiceConfig { storage: Arc<dyn StoragePlugin>, repo: Arc<dyn SessionFileRepoPort>, session_repo: Arc<dyn SessionRepoPort>, env: String, max_size: u64, multipart_threshold: u64, bcs_base_url: String, share_secret: Vec<u8>, share_default_ttl: u64, share_base_url: Option<String> }`、构造 `SessionFileServiceImpl::new(cfg) -> Self`。`env` 用于 object key 派生（`derive_key(env, ...)`），须与 DB 行 `env` 列（`MySqlSessionFileStore.env`）一致，故由 bootstrap 注入与 repo 同一 env。
- Consumes: Task 1/3/4/5

- [ ] **Step 1: Cargo.toml**

```toml
[package]
name = "bcs-session-file"
description = "Session file workspace application service for BCS"
version.workspace      = true
edition.workspace      = true
license.workspace      = true
repository.workspace   = true
rust-version.workspace = true

[dependencies]
async-trait     = { workspace = true }
bcs-domain      = { workspace = true }
bcs-service-api = { workspace = true }
bcs-storage-api = { workspace = true }
bcs-session-file-store = { workspace = true }
serde           = { workspace = true, features = ["derive"] }
serde_json      = { workspace = true }
tracing         = { workspace = true }
anyhow          = { workspace = true }

[dev-dependencies]
bcs-test-support = { workspace = true }
tokio            = { workspace = true, features = ["macros", "rt-multi-thread"] }

[lints]
workspace = true
```

> `SessionFileService` trait 在 `bcs-service-api`（Task 4）；本 crate 依赖 `bcs-service-api` 拿到 trait 与 port；`bcs-session-file` 的 `bcs-session-file-store` 依赖用于构造 memory repo 在测试里注入。生产 mysql repo 由 bootstrap 注入（不在此 crate 直接依赖 mysql store 也可；但为测试便利，dev 依赖即可）。

- [ ] **Step 2: 写 key 派生 + authz helper**

`src/authz.rs`：

```rust
use bcs_domain::ActorRef;

/// key = session-files/{env}/{session_id}/{file_id}/{file_name}
/// `env` 取自 `SessionFileServiceConfig.env`（见 Step 5），与 DB 行 `env` 列一致。
pub fn derive_key(env: &str, session_id: &str, file_id: &str, file_name: &str) -> String {
    format!("session-files/{env}/{session_id}/{file_id}/{file_name}")
}

/// Test whether `caller` may mutate (delete/share) a file owned by `owner`.
///
/// 鉴权分工：service 层做纯判断（caller 是否含 owner / session creator / driver bot 身份）；
/// "human 拥有 driver bot" 需要 bot registry（HTTP 层已有 `list_bots_by_creator`）。为避免
/// service 依赖 registry，HTTP handler（Task 8）先把 `caller_identities` 解析好传入 ——
/// `caller_identities = [caller.actor_id] + 其拥有的 bot_uuid 列表`，并预先解析 `session_creator`
/// 与 `driver_bot`（均按 actor_id 比对）。service 不再持有 `SessionRepoPort` 用于鉴权（故本 helper
/// 不再依赖 `SessionRepoPort`，纯同步函数）。
pub fn can_mutate(
    caller_identities: &[String],
    owner: &ActorRef,
    session_creator: Option<&str>,
    driver_bot: Option<&str>,
) -> bool {
    if caller_identities.iter().any(|id| id == &owner.actor_id) { return true; }
    if let Some(c) = session_creator { if caller_identities.iter().any(|id| id == c) { return true; } }
    if let Some(d) = driver_bot { if caller_identities.iter().any(|id| id == d) { return true; } }
    false
}
```

> **配套 DTO（已在 Task 4 Step 2 落地）**：`DeleteFileCommand` 与 `ShareMintCommand` 同时带 `caller_identities: Vec<String>`、`session_creator: Option<String>`、`driver_bot: Option<String>` 三字段。**mutate 鉴权完全在 service 层做**（可单测、不依赖 HTTP）：service 调 `can_mutate(cmd.caller_identities, &row.owner, cmd.session_creator.as_deref(), cmd.driver_bot.as_deref())`。HTTP 层（Task 8）职责仅为：① 用 `caller_identities(state, caller)` helper 收集身份；② 取 `session.info().created_by` 与 `group.driver_bot` 填入 command 的 `session_creator`/`driver_bot`。这样 service 不需 `group_repo`（仅用 command 携带值），HTTP 不做鉴权判断只做数据装配。`PrepareUploadCommand` 不带 `caller_identities`（prepare/upload 仅需 participant 校验，由 HTTP `ensure_session_member` 把关）。

- [ ] **Step 3: 写 `prepare_upload` 测试（失败）**

`src/service.rs` 测试驱动。先写测试：local 后端（ProxyViaBcs）单片 + 分段，断言 repo 行 Pending、`client_target_json` 形态、`upload_url` 指向 BCS、`expires_at` 在最外层。

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{ActorKind, ActorRef};
    use bcs_service_api::port::repo::{SessionFileListParams, SessionRepoPort};
    use bcs_session_file_store::MemorySessionFileRepo;
    use bcs_storage_api::{ClientUploadTarget, StorageCapabilities, StoragePlugin};

    fn local_caps() -> StorageCapabilities {
        StorageCapabilities { supports_presign_put: false, supports_presign_download: false,
            supports_stream_put: true, supports_stream_get: true, max_object_size: 5_000_000_000 }
    }

    async fn svc() -> (SessionFileServiceImpl, Arc<dyn StoragePlugin>) {
        let storage: Arc<dyn StoragePlugin> = Arc::new(FakeStoragePlugin::new(local_caps()));
        let repo: Arc<dyn SessionFileRepoPort> = Arc::new(MemorySessionFileRepo::new());
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(/* 测试用 fake，见 bcs-test-support 或本 crate内建简易 stub */);
        let cfg = SessionFileServiceConfig {
            storage: storage.clone(), repo: repo.clone(), session_repo,
            env: "test".into(), // 须与 MemorySessionFileRepo 的 env 一致（memory repo 当前不按 env 过滤，测试仍应填占位以对齐契约）
            max_size: 5_000_000_000, multipart_threshold: 100*1024*1024,
            bcs_base_url: "http://bcs:21000".into(),
            share_secret: b"k".to_vec(), share_default_ttl: 3600, share_base_url: None,
        };
        (SessionFileServiceImpl::new(cfg), storage)
    }

    #[tokio::test]
    async fn prepare_single_returns_proxy_url_and_pending_row() {
        let (s, _) = svc().await;
        let r = s.prepare_upload(PrepareUploadCommand {
            session_id: "g1:abcd1234".into(), file_name: "x.txt".into(), size: 100,
            mime_type: "text/plain".into(),
            caller: ActorRef { actor_kind: ActorKind::Human, actor_id: "human_1".into() },
        }).await.unwrap();
        assert!(r.file.object_handle.contains("\"ProxyViaBcs\"") || r.client_target_json.get("mode").is_none());
        assert_eq!(r.client_target_json["mode"], "single");
        assert!(r.client_target_json["upload_url"].as_str().unwrap().starts_with("http://bcs:21000/sessions/g1:abcd1234/files/"));
        assert!(r.client_target_json["expires_at"].is_u64());
    }
}
```

> **fake `SessionRepoPort`**：`bcs-service-api` 的 `SessionRepoPort` 方法很多；测试需一个实现。优先用 `bcs-test-support` 已有的 fake（`grep -rn "SessionRepoPort" crates/test-support`）；若无，则在 `bcs-session-file` 测试模块内写一个最小 stub（仅实现 `get`/`belongs_to_group`，其余默认占位）。实现者先 grep 确认。

- [ ] **Step 4: 运行确认失败**

Run: `cargo test -p bcs-session-file 2>&1 | tail -30`
Expected: 编译失败（`SessionFileServiceImpl`/`FakeStoragePlugin` import 未定义）。

- [ ] **Step 5: 实现 `SessionFileServiceImpl`**

`src/lib.rs`：`pub mod authz; pub mod service; pub use service::{SessionFileServiceConfig, SessionFileServiceImpl};`。

`src/service.rs` 完整实现要点（逐方法，落字时保留签名与 Task 4 trait 一致）：

```rust
use std::sync::Arc;
use async_trait::async_trait;
use bcs_domain::{self, ActorRef, FileStatus, SessionFile, new_file_id, share_token_decode_and_verify, share_token_encode, ShareTokenPayload};
use bcs_service_api::application::session_files::*;
use bcs_service_api::port::repo::{NewSessionFileParams, SessionFileListParams, SessionFileRepoPort};
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_storage_api::{ByteStream, ClientUploadTarget, PreparedUpload, StorageCapabilities, StorageError, StorageHandle, UploadHandle, UploadMode, UploadPrepareRequest};
use crate::authz::{can_mutate, derive_key};

pub struct SessionFileServiceConfig {
    pub storage: Arc<dyn bcs_storage_api::StoragePlugin>,
    pub repo: Arc<dyn SessionFileRepoPort>,
    pub session_repo: Arc<dyn SessionRepoPort>,
    pub env: String, // 与 repo 的 env 一致；用于 object key 派生（derive_key(env, ...)）
    pub max_size: u64,
    pub multipart_threshold: u64,
    pub bcs_base_url: String,
    pub share_secret: Vec<u8>,
    pub share_default_ttl: u64,
    pub share_base_url: Option<String>,
}

pub struct SessionFileServiceImpl {
    cfg: SessionFileServiceConfig,
    caps: StorageCapabilities,
}

impl SessionFileServiceImpl {
    pub fn new(cfg: SessionFileServiceConfig) -> Self {
        // capabilities() precomputed at construction (cheap, no IO) — spec mandate.
        let caps = cfg.storage.capabilities();
        Self { cfg, caps }
    }

    fn max_size(&self) -> u64 { self.cfg.max_size.min(self.caps.max_object_size) }

    fn bcs_proxy_upload_url(&self, sid: &str, file_id: &str) -> String {
        format!("{}/sessions/{}/files/{}/content", self.cfg.bcs_base_url, urlencoding::encode(sid), file_id)
    }
    fn bcs_proxy_upload_url_part(&self, sid: &str, file_id: &str, part: u16) -> String {
        format!("{}?part={}", self.bcs_proxy_upload_url(sid, file_id), part)
    }
}
```

/// Fixed part size for the local-proxy (ProxyViaBcs) multipart branch.
/// Shared by `prepare_upload` (part_count guard) and `wire_client_target` (synthesis).
const PROXY_PART_SIZE: u64 = 10 * 1024 * 1024;
```

`prepare_upload`：
```rust
#[async_trait]
impl SessionFileService for SessionFileServiceImpl {
    async fn capabilities(&self) -> CapabilitiesView {
        CapabilitiesView {
            storage: self.cfg.storage.backend_name().to_string(),
            presign_upload: self.caps.supports_presign_put,
            presign_download: self.caps.supports_presign_download,
            max_size: self.max_size(),
        }
    }

    async fn prepare_upload(&self, cmd: PrepareUploadCommand) -> Result<PrepareUploadResult, SessionFileUseCaseError> {
        if cmd.size > self.max_size() {
            return Err(SessionFileUseCaseError::PayloadTooLarge(format!("size {} exceeds max {}", cmd.size, self.max_size())));
        }
        // part_count guard: part_number is u16, so local-proxy multipart (part_size 10MB)
        // must not exceed 65535 parts. Presign backends return their own part_count in
        // client_target, so this only bounds the proxy branch — but enforce here uniformly
        // to avoid `as u16` overflow panic in wire_client_target.
        // part_count ≤ 65535 guard (part_number is u16). Uses the mod-level PROXY_PART_SIZE
        // so the bound stays consistent with wire_client_target's synthesis.
        let part_count_needed = cmd.size.div_ceil(PROXY_PART_SIZE);
        if part_count_needed > 65535 {
            return Err(SessionFileUseCaseError::PayloadTooLarge(format!(
                "size {} would produce {} parts, max 65535", cmd.size, part_count_needed
            )));
        }
        // member check: session must exist; caller must be participant (HTTP layer already did member
        // check; service re-validates session existence).
        let _sess = self.cfg.session_repo.get(&cmd.session_id).await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("session {}", cmd.session_id)))?;

        let file_id = new_file_id();
        let key = derive_key(&self.cfg.env, &cmd.session_id, &file_id, &cmd.file_name);
        let req = UploadPrepareRequest {
            key: key.clone(), file_name: cmd.file_name.clone(), mime_type: cmd.mime_type.clone(),
            size: cmd.size, ttl_secs: 300,
        };
        let prepared: PreparedUpload = self.cfg.storage.prepare_upload(req).await
            .map_err(map_storage_err)?;
        let handle_json = serde_json::to_string(&prepared.handle).unwrap();
        let row = self.cfg.repo.insert(NewSessionFileParams {
            file_id: file_id.clone(), session_id: cmd.session_id.clone(),
            file_name: cmd.file_name.clone(), mime_type: cmd.mime_type.clone(), size: cmd.size,
            owner: cmd.caller.clone(), storage_backend: self.cfg.storage.backend_name().to_string(),
            object_handle: handle_json, expires_at: prepared.expires_at,
        }).await.map_err(SessionFileUseCaseError::Internal)?;

        let client_target_json = self.wire_client_target(&cmd.session_id, &file_id, cmd.size, &prepared);
        Ok(PrepareUploadResult { file: row, client_target_json, expires_at: prepared.expires_at })
    }
}
```

`wire_client_target(&self, sid, file_id, size, prepared)`（把 `PreparedUpload.client_target` 翻译成 §1.2.a/§1.2.b 响应 JSON；**对 local `ProxyViaBcs` 据 `size` 与 `multipart_threshold` 自决单片/分段并合成 BCS 代理 URL**）。签名带 `size: u64`（取自 `cmd.size`，避免从 `object_handle` 反解 size）：

```rust
impl SessionFileServiceImpl {
    fn wire_client_target(&self, sid: &str, file_id: &str, size: u64, prepared: &PreparedUpload) -> serde_json::Value {
        match &prepared.client_target {
            ClientUploadTarget::Direct { mode, url, parts, part_size, part_count } => {
                match mode {
                    UploadMode::Single => serde_json::json!({
                        "mode": "single",
                        "upload_url": url.clone().unwrap_or_default(),
                        "method": "PUT",
                        "expires_at": prepared.expires_at,
                    }),
                    UploadMode::Multipart => serde_json::json!({
                        "mode": "multipart",
                        "method": "PUT",
                        "part_size": part_size.unwrap_or(0),
                        "part_count": part_count.unwrap_or(0),
                        "expires_at": prepared.expires_at,
                        "parts": parts.clone().unwrap_or_default().iter().map(|p| serde_json::json!({"part_number": p.part_number, "upload_url": p.url})).collect::<Vec<_>>(),
                    }),
                }
            }
            ClientUploadTarget::ProxyViaBcs => {
                // local proxy: no direct URL. Service decides single vs multipart by
                // size vs multipart_threshold, synthesizing BCS proxy URLs.
                // part_size 取 mod 级 PROXY_PART_SIZE（与 prepare_upload 的上限校验共用同一值）。
                let part_size: u64 = PROXY_PART_SIZE;
                if size >= self.cfg.multipart_threshold {
                    // part_count ≤ 65535 已在 prepare_upload 前置校验保证（PayloadTooLarge → 413），
                    // 此处 `as u16` 安全；仍加 debug_assert 作防御性不变量。
                    let part_count: u32 = ((size + part_size - 1) / part_size) as u32;
                    debug_assert!(part_count <= 65535, "part_count overflow — prepare should have rejected");
                    let parts: Vec<_> = (1..=part_count as u16).map(|n| serde_json::json!({
                        "part_number": n, "upload_url": self.bcs_proxy_upload_url_part(sid, file_id, n)
                    })).collect();
                    serde_json::json!({
                        "mode": "multipart", "method": "PUT",
                        "part_size": part_size, "part_count": part_count,
                        "expires_at": prepared.expires_at, "parts": parts,
                    })
                } else {
                    serde_json::json!({
                        "mode": "single",
                        "upload_url": self.bcs_proxy_upload_url(sid, file_id),
                        "method": "PUT",
                        "expires_at": prepared.expires_at,
                    })
                }
            }
        }
    }
}
```

> **part_count 上限前置校验已落地**：`prepare_upload` 在生成 client_target 前校验 `size > max_size` → `PayloadTooLarge`（handler 映射 413），并校验 `ceil(size / PROXY_PART_SIZE) > 65535` → 同样 `PayloadTooLarge`。故 `wire_client_target` 代理分支的 `as u16` 安全。`PROXY_PART_SIZE` 与 `wire_client_target` 内的 `part_size` 必须一致——建议提成 mod 级 `const PROXY_PART_SIZE: u64 = 10 * 1024 * 1024;` 两处复用，避免字面量漂移。

`stream_upload` / `complete_upload` / `delete_file` / `download_route` / `share_mint` / `share_consume` / `get_stream` / `sweep_expired_pending` / `delete_all_for_session` — 实现要点：

- `stream_upload`：get 行确认 `Pending` 否则 `Conflict`；`content_length > max_size` 或与 prepare size 不符 → `InvalidInput`(413 由 handler映射)；重建 `UploadHandle`（`serde_json::from_str(&row.object_handle)`）；调 `storage.stream_upload(&handle, part_number, body)`；`map_storage_err`。
- `complete_upload`：行需 `Pending` 否则 `Conflict`；重建 handle；`meta = storage.complete_upload(&handle).await`；`meta.size != row.size` 或（local multipart）缺段 → `Conflict`（local 后端在 `complete_upload` 内校验并返 `StorageError::Conflict`，service 透传）；`object_handle` 替换为 `StorageHandle` 序列化（瘦身后）；`repo.update_object_handle_and_status(..., Ready, meta.size)`。
- `delete_file`：行不存在 → `Ok(())`（元数据层幂等，不报 NotFound）；行存在：`if !can_mutate(&cmd.caller_identities, &row.owner, cmd.session_creator.as_deref(), cmd.driver_bot.as_deref()) { return Err(Forbidden) }`；`status==Ready` → `storage.delete(&storage_handle)`；`Pending/Failed` → `storage.abort_upload(&upload_handle)`；后端成功后 `repo.delete(...)`；后端失败 → `Backend`（502，可重试，**不删行**保留对象由 sweep）。`StorageError::NotFound` from backend → 当 `Ok`（幂等）。
- `download_route`：行需 `Ready` 否则 `InvalidState`；`presign_download` → `storage.presign_get(&handle, ttl)` → `DownloadRoute{ presign: Some }`；否则 `None`（HTTP 走 `get_stream`）。
- `share_mint`：行需 `Ready` 否则 `InvalidState`；`if !can_mutate(&cmd.caller_identities, &row.owner, cmd.session_creator.as_deref(), cmd.driver_bot.as_deref()) { return Err(Forbidden) }`；`exp = now + cmd.ttl_seconds.unwrap_or(share_default_ttl).clamp(60,604800)`；`token = share_token_encode(&ShareTokenPayload{v:1,file_id:cmd.file_id.clone(),exp}, &secret)`；`base = share_base_url.clone().unwrap_or_else(|| bcs_base_url.clone())`；`share_url = format!("{}/sessions/{}/shared-file/content?token={}", base, urlencoding::encode(&cmd.session_id), token)`；返回 `ShareMintResult{ share_url, share_token: token, expires_at: exp }`。
- `share_consume`：`payload = share_token_decode_and_verify(token, &secret)?`（err 映射 401/410）；`row = repo.get(sid, payload.file_id)`；行不存在或 `row.session_id != sid` → `NotFound`（404）；行需 `Ready` 否则 `InvalidState`；返回 `ShareConsumeResult{ file: row }`（**不要调 `row.redacted()`**——`SessionFile` 无此方法；`object_handle` 不透出由 HTTP `to_dto` 序列化时剥离，service 返回完整 row 即可）。
- `get_stream`：行 `Ready`；重建 `StorageHandle`；`storage.get_stream(&handle)`。
- `sweep_expired_pending`：`repo.list_expired_pending(now, 100)`；逐行 `storage.abort_upload`（错误记日志不中断）+ `repo.update_status(..., Failed)`；返回处理数。
- `delete_all_for_session`：`repo.delete_all_for_session(sid)` 返回行列表；逐行 `storage.delete(&storage_handle)`（错误记日志留孤儿对象，不中断，按 spec "部分失败语义"）；返回处理数。

`map_storage_err`：

```rust
fn map_storage_err(e: StorageError) -> SessionFileUseCaseError {
    match e {
        StorageError::NotFound => SessionFileUseCaseError::NotFound("object".into()),
        StorageError::Conflict(m) => SessionFileUseCaseError::Conflict(m),
        StorageError::InvalidInput(m) => SessionFileUseCaseError::InvalidInput(m),
        StorageError::Unsupported(_) => SessionFileUseCaseError::Backend,
        StorageError::Backend(_) => SessionFileUseCaseError::Backend,
    }
}
```

- [ ] **Step 6: 完整服务单测**

补齐 service 单测覆盖：prepare single/multipart、stream+complete 往返、delete 分流（Ready/Pending/Failed/幂等 204）、download_route（local→None）、share mint+consume（成功/过期/篡改/sid 不一致）、sweep、delete_all_for_session。用 `FakeStoragePlugin` + `MemorySessionFileRepo` + fake `SessionRepoPort`。

Run: `cargo test -p bcs-session-file 2>&1 | tail -40`
Expected: 全 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/bcs/Cargo.toml src/bcs/crates/services/bcs-session-file
git commit -m "feat(bcs-session-file): SessionFileService impl (routing/authz/share/sweep)"
```

---

## Task 7: `bcs-storage-local` 本地文件系统后端

实现 `StoragePlugin`（`supports_presign_put=false`、`supports_presign_download=false`），通过 `bcs-storage-api` 契约测试。

**Files:**
- Create: `crates/plugins/bcs-storage-local/Cargo.toml`
- Create: `crates/plugins/bcs-storage-local/src/lib.rs`
- Create: `crates/plugins/bcs-storage-local/tests/contract.rs`（调 `assert_storage_plugin_conforms`）
- Modify: `Cargo.toml`（members + `bcs-storage-local` workspace dep）

**Interfaces:**
- Produces：`LocalStoragePlugin` + `LocalStorageConfig { data_dir: PathBuf, max_object_size: u64 }` + `LocalStoragePlugin::new(cfg) -> Self`（`max_object_size` 在构造期固化进 capabilities）
- Consumes: Task 3 `bcs-storage-api`

- [ ] **Step 1: Cargo.toml**

```toml
[package]
name = "bcs-storage-local"
version.workspace      = true
edition.workspace      = true
license.workspace      = true
repository.workspace   = true
rust-version.workspace = true

[dependencies]
async-trait = { workspace = true }
anyhow      = { workspace = true }
bcs-storage-api = { workspace = true }
bytes       = { workspace = true }
futures     = { workspace = true }
serde       = { workspace = true, features = ["derive"] }
serde_json  = { workspace = true }
tokio       = { workspace = true, features = ["fs", "io-util"] }
tokio-util  = { workspace = true, features = ["io"] }
tracing     = { workspace = true }
fastrand    = { workspace = true }

[dev-dependencies]
tempfile = { workspace = true }
tokio    = { workspace = true, features = ["macros", "rt-multi-thread"] }

[lints]
workspace = true
```

- [ ] **Step 2: 写实现**

`src/lib.rs`：路径策略按 spec §3.1 —— 单片 temp `{data_dir}/{key}.{rand}.part`、终态 `{data_dir}/{key}`；分段 temp `{data_dir}/{key}.p{part_number}.{rand}.part`。`prepare_upload` 返回 `ProxyViaBcs`，`backend_handle` 单片 `{temp_path, final_path, size}`、分段 `{final_path, parts:[{part_number,temp_path}], size, part_size}`。`stream_upload` 校验磁盘空间 + 累计 size ≤ prepare size，写 temp。`complete_upload` 单片 fsync+rename；分段按序 concat+fsync+rename，校验各段存在与累计 size == prepare size 否则 `Conflict`；返回 `StorageObjectMeta`。`abort_upload` unlink temp(s)。`get_stream` 打开 final，`ReaderStream`。`presign_get` 返 `Unsupported`。`delete` unlink final（幂等 Ok）。`health_check` 探 `data_dir` 可写。

核心（单片 + 分段 complete、路径派生）：

```rust
use std::path::{Path, PathBuf};
use std::sync::Arc;
use async_trait::async_trait;
use bytes::Bytes;
use bcs_storage_api::*;
use tokio::io::AsyncWriteExt;
use tokio_util::io::ReaderStream;

pub struct LocalStorageConfig { pub data_dir: PathBuf, pub max_object_size: u64 }

pub struct LocalStoragePlugin {
    data_dir: PathBuf,
    caps: StorageCapabilities,
}

impl LocalStoragePlugin {
    pub fn new(cfg: LocalStorageConfig) -> Self {
        let caps = StorageCapabilities {
            supports_presign_put: false, supports_presign_download: false,
            supports_stream_put: true, supports_stream_get: true,
            max_object_size: cfg.max_object_size,
        };
        Self { data_dir: cfg.data_dir, caps }
    }
    fn final_path(&self, key: &str) -> PathBuf { self.data_dir.join(key) }
    fn rand_suffix() -> String { (0..8).map(|_| fastrand::alphanumeric()).collect() }
    fn temp_path(&self, key: &str, part: Option<u16>) -> PathBuf {
        match part {
            None => self.data_dir.join(format!("{key}.{}.part", Self::rand_suffix())),
            Some(n) => self.data_dir.join(format!("{key}.p{n}.{}.part", Self::rand_suffix())),
        }
    }
}

fn box_stream<S>(s: S) -> ByteStream
where S: futures::Stream<Item = Result<Bytes, std::io::Error>> + Send + Unpin + 'static
{
    struct Wrap<S>(S);
    impl<S> futures::Stream for Wrap<S> where S: futures::Stream<Item = Result<Bytes, std::io::Error>> + Unpin {
        type Item = Result<Bytes, std::io::Error>;
        fn poll_next(mut self: std::pin::Pin<&mut Self>, cx: &mut std::task::Context<'_>)
            -> std::task::Poll<Option<Self::Item>> {
            std::pin::Pin::new(&mut self.0).poll_next(cx)
        }
    }
    impl<S> ByteStreamTrait for Wrap<S> where S: futures::Stream<Item = Result<Bytes, std::io::Error>> + Send + Unpin + 'static {}
    Box::new(Wrap(s))
}

// stream_upload: drain body to temp file (per part), enforce cumulative size.
async fn drain_to(path: &Path, mut body: ByteStream, max: u64) -> Result<u64, std::io::Error> {
    if let Some(parent) = path.parent() { tokio::fs::create_dir_all(parent).await.ok(); }
    let mut f = tokio::fs::File::create(path).await?;
    let mut total: u64 = 0;
    use futures::StreamExt;
    while let Some(chunk) = body.next().await {
        let b = chunk?;
        total += b.len() as u64;
        if total > max { return Err(std::io::Error::new(std::io::ErrorKind::InvalidInput, "exceeds size")); }
        f.write_all(&b).await?;
    }
    f.sync_all().await?;
    Ok(total)
}
```

`complete_upload` 分段组装（核心校验语义 —— 缺段/累计不符返 `Conflict`，对应 §1.2 409）：

```rust
async fn complete_upload(&self, handle: &UploadHandle) -> Result<StorageObjectMeta, StorageError> {
    let bh = &handle.backend_handle;
    let size = bh.get("size").and_then(|v| v.as_u64()).ok_or_else(|| StorageError::InvalidInput("missing size".into()))?;
    if let Some(parts) = bh.get("parts").and_then(|v| v.as_array()) {
        // multipart: concat in part_number order, verify cumulative == size
        let final_path = PathBuf::from(bh["final_path"].as_str().ok_or_else(|| StorageError::InvalidInput("missing final_path".into()))?);
        let mut sorted: Vec<(u16, String)> = parts.iter().filter_map(|p| Some((p["part_number"].as_u64()? as u16, p["temp_path"].as_str()?.to_string()))).collect();
        sorted.sort_by_key(|(n, _)| *n);
        let mut out = tokio::fs::File::create(&final_path).await?;
        let mut total: u64 = 0;
        for (_, tmp) in &sorted {
            let mut r = tokio::fs::File::open(tmp).await.map_err(|e| StorageError::Backend(e.into()))?;
            total += tokio::io::copy(&mut r, &mut out).await.map_err(|e| StorageError::Backend(e.into()))?;
        }
        out.sync_all().await.map_err(|e| StorageError::Backend(e.into()))?;
        if total != size { return Err(StorageError::Conflict(format!("size mismatch {} != {}", total, size))); }
        // cleanup temps
        for (_, tmp) in &sorted { let _ = tokio::fs::remove_file(tmp).await; }
        Ok(StorageObjectMeta { key: handle.key.clone(), size: total, sha256: None })
    } else {
        // single
        let tmp = PathBuf::from(bh["temp_path"].as_str().ok_or_else(|| StorageError::InvalidInput("missing temp_path".into()))?);
        let final_path = PathBuf::from(bh["final_path"].as_str().ok_or_else(|| StorageError::InvalidInput("missing final_path".into()))?);
        let actual = tokio::fs::metadata(&tmp).await.map_err(|e| StorageError::Backend(e.into()))?.len();
        if actual != size { return Err(StorageError::Conflict(format!("size mismatch {} != {}", actual, size))); }
        tokio::fs::rename(&tmp, &final_path).await.map_err(|e| StorageError::Backend(e.into()))?;
        Ok(StorageObjectMeta { key: handle.key.clone(), size: actual, sha256: None })
    }
}
```

> `prepare_upload`：单片 `backend_handle = {temp_path, final_path, size}`；分段（service 侧按 threshold 决定 mode，但 local 也需知道分段计划）—— **决定**：local `prepare_upload` 据 `req.size` vs `max_object_size` **不**自决 multipart（multipart_threshold 是 BCS 配置，不在后端）；service 在 `wire_client_target` 里据 `multipart_threshold` 决定 mode 并把 `part_number` 透传给 `stream_upload`。因此 local `prepare_upload` 始终返回 `ProxyViaBcs`，`backend_handle` 含 `{temp_path, final_path, size}`（单片）—— 但分段时 `stream_upload(part_number=Some(n))` 需要一个 part temp 路径。**修正**：local `prepare_upload` 不预生成 parts；`stream_upload` 在 `part_number=Some(n)` 时用 `self.temp_path(key, Some(n))` 现场生成 part temp 路径，并在 `backend_handle` 里维护 `final_path`+`size`+累计 parts（通过 `mut handle`? trait 是 `&UploadHandle` 不可变）。**方案**：part temp 路径由 `key`+`part_number`+随机后缀派生（无需持久化到 handle，因为 `complete_upload` 用 `std::fs::read_dir` 扫 `{key}.p{n}.*.part` 文件按 part_number 重组）。这样 `backend_handle` 单片/分段统一为 `{final_path, size}`，`complete_upload` 分段时扫目录。落字：`stream_upload(Some(n))` → 写 `temp_path(key, Some(n))`；`complete_upload` → 若存在任一 `{key}.p*.part` 走分段分支（扫目录排序），否则单片（`temp_path` 由 `backend_handle.temp_path`，单片保留 temp_path 字段）。

> **简化为一致方案**：`backend_handle` 恒为 `{ final_path: String, size: u64 }`。单片 `stream_upload(None)` 写 `{key}.{rand}.part`；分段 `stream_upload(Some(n))` 写 `{key}.p{n}.{rand}.part`。`complete_upload`：扫 `data_dir` 下 `{key}.*` 文件 —— 若含 `p{n}` 段则按 n 排序 concat 到 `final_path`，否则把单个 `.part` rename。`abort_upload`：扫删 `{key}.*`。`delete`：unlink `final_path`。`get_stream`：open `final_path`。这样无需在 handle 里持久化 per-part 路径，handle 恒小（满足 spec「不持久化短命路径」精神）。**采用此扫目录方案**，`complete_upload` 重写为扫目录版本（实现者按此）。

- [ ] **Step 3: 契约测试**

`tests/contract.rs`：

```rust
use std::sync::Arc;
use bcs_storage_api::{contract::assert_storage_plugin_conforms, StorageCapabilities};
use bcs_storage_local::{LocalStorageConfig, LocalStoragePlugin};

#[tokio::test]
async fn local_conforms() {
    let dir = tempfile::tempdir().unwrap();
    let caps = StorageCapabilities {
        supports_presign_put: false, supports_presign_download: false,
        supports_stream_put: true, supports_stream_get: true, max_object_size: 1024 * 1024,
    };
    let plugin: Arc<dyn bcs_storage_api::StoragePlugin> =
        Arc::new(LocalStoragePlugin::new(LocalStorageConfig { data_dir: dir.path().to_path_buf(), max_object_size: 1024 * 1024 }));
    assert_storage_plugin_conforms(plugin, caps).await;
}
```

- [ ] **Step 4: 注册 + 跑测试**

`src/bcs/Cargo.toml`：members 加 `"crates/plugins/bcs-storage-local",`；workspace deps 加 `bcs-storage-local = { path = "crates/plugins/bcs-storage-local" }`。

Run: `cargo test -p bcs-storage-local 2>&1 | tail -30`
Expected: 契约测试 PASS（含往返 + delete 幂等 + NotFound）。补 local 专属单测：分段缺段 → `Conflict`、`abort` 删 temp、`presign_get` → `Unsupported`、`delete` 不存在对象 `Ok`。

- [ ] **Step 5: Commit**

```bash
git add src/bcs/Cargo.toml src/bcs/crates/plugins/bcs-storage-local
git commit -m "feat(bcs-storage-local): local fs StoragePlugin + contract suite"
```

---

## Task 8: HTTP 适配 `routes/session_files.rs`

axum 0.8 `{sid}` 路径、`Redirect::to` 302、`Body::from_stream` 流式、`/files/capabilities` 静态段优先、错误映射 `{error, message}`、成员校验复用 `resolve_group_chat_caller` + `human_has_group_access`、`caller_identities` 收集（caller.actor_id + 其拥有的 bot_uuid）。

**Files:**
- Create: `crates/adapters/http/bcs-http/src/routes/session_files.rs`
- Modify: `crates/adapters/http/bcs-http/src/routes/mod.rs`（`pub mod session_files;`）
- Modify: `crates/adapters/http/bcs-http/src/router.rs`（注册路由）
- Modify: `crates/adapters/http/bcs-http/src/state.rs`（services 已含 session_files；如经 Services 则仅需确认；分享 secret 经 service 持有，无需 state 改动）—— **若 `Services` bundle 未含 session_files 则在此加 `with_*`**

**Interfaces:**
- Produces：handler 函数 `prepare_upload`/`upload_bytes`/`complete_upload`/`delete_file`/`list_files`/`get_file`/`download_content`/`capabilities`/`share_mint`/`shared_file_meta`/`shared_file_content`
- Consumes: Task 6 `SessionFileService`（经 `state.services.session_files`）、Task 1/4；`resolve_group_chat_caller`/`GroupChatCaller`/`human_has_group_access`

- [ ] **Step 1: DTO + 错误映射**

`session_files.rs` 顶部：请求/响应 DTO（`SessionFileDto` 剥 `object_handle`、`PrepareRequest`、`ListQuery`、`ShareRequest`）+ `fn session_file_to_response(err: &SessionFileUseCaseError) -> Response`（按 spec 错误码表映射：`NotFound→404`、`Forbidden→403`、`InvalidInput→413` 当 size 超限 / 否则 400、`Conflict→409 INVALID_TRANSITION`、`InvalidState→422`、`Backend→502`、`Internal→500`）。返回体 `{"error": "<CODE>", "message": "..."}`。

```rust
use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use bcs_domain::{ActorKind, ActorRef, SessionFile};
use bcs_service_api::application::session_files::{
    CapabilitiesView, DeleteFileCommand, PrepareUploadCommand, SessionFileUseCaseError,
    ShareMintCommand,
};
use crate::routes::group_messages::{resolve_group_chat_caller, GroupChatCaller};
use crate::state::HttpAppState;

#[derive(Serialize)]
struct SessionFileDto {
    file_id: String, session_id: String, file_name: String, mime_type: String,
    size: u64, sha256: Option<String>,
    owner: ActorRefDto, storage_backend: String, status: String,
    created_at: u64, updated_at: u64,
    // NOTE: object_handle intentionally omitted — internal only.
}
#[derive(Serialize)] struct ActorRefDto { actor_kind: String, actor_id: String }

fn to_dto(f: &SessionFile) -> SessionFileDto {
    SessionFileDto {
        file_id: f.file_id.clone(), session_id: f.session_id.clone(), file_name: f.file_name.clone(),
        mime_type: f.mime_type.clone(), size: f.size, sha256: f.sha256.clone(),
        owner: ActorRefDto {
            actor_kind: match f.owner.actor_kind { ActorKind::Bot => "Bot", ActorKind::Human => "Human" }.to_string(),
            actor_id: f.owner.actor_id.clone(),
        },
        storage_backend: f.storage_backend.clone(),
        status: serde_json::to_string(&f.status).unwrap().trim_matches('"').to_string(),
        created_at: f.created_at, updated_at: f.updated_at,
    }
}

fn err_to_response(err: SessionFileUseCaseError) -> Response {
    use SessionFileUseCaseError::*;
    let (code, status) = match &err {
        NotFound(_) => ("FILE_NOT_FOUND", StatusCode::NOT_FOUND),
        Forbidden(_) => ("FORBIDDEN", StatusCode::FORBIDDEN),
        PayloadTooLarge(_) => ("PAYLOAD_TOO_LARGE", StatusCode::PAYLOAD_TOO_LARGE),
        InvalidInput(_) => ("INVALID_INPUT", StatusCode::BAD_REQUEST),
        Conflict(_) => ("INVALID_TRANSITION", StatusCode::CONFLICT),
        InvalidState(_) => ("INVALID_STATE", StatusCode::UNPROCESSABLE_ENTITY),
        Backend => ("STORAGE_BACKEND", StatusCode::BAD_GATEWAY),
        Internal(_) => ("INTERNAL", StatusCode::INTERNAL_SERVER_ERROR),
    };
    (status, Json(json!({ "error": code, "message": err.to_string() }))).into_response()
}

#[derive(Deserialize)] struct PrepareRequest { file_name: String, size: u64, mime_type: String }
#[derive(Deserialize)] struct ListQuery { prefix: Option<String>, limit: Option<u32>, marker: Option<String> }
#[derive(Deserialize)] struct ShareRequest { ttl_seconds: Option<u64> }
#[derive(Deserialize)] struct DownloadQuery { ttl: Option<u64>, token: Option<String> }
```

- [ ] **Step 2: caller → ActorRef + caller_identities helper**

```rust
fn caller_to_actor_ref(caller: &GroupChatCaller) -> ActorRef {
    match caller {
        GroupChatCaller::Bot { bot_uuid } => ActorRef { actor_kind: ActorKind::Bot, actor_id: bot_uuid.clone() },
        GroupChatCaller::Human(h) => ActorRef { actor_kind: ActorKind::Human, actor_id: h.actor_id.clone() },
    }
}
async fn caller_identities(state: &HttpAppState, caller: &GroupChatCaller) -> Vec<String> {
    match caller {
        GroupChatCaller::Bot { bot_uuid } => vec![bot_uuid.clone()],
        GroupChatCaller::Human(h) => {
            let mut ids = vec![h.actor_id.clone()];
            for b in state.services.registry.list_bots_by_creator(&h.staff_no).await {
                ids.push(b.bot_uuid);
            }
            ids
        }
    }
}
```

- [ ] **Step 3: `prepare_upload` handler**

```rust
pub async fn prepare_upload(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap, uri: Uri,
    Json(body): Json<PrepareRequest>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    // member check: reuse human_has_group_access pattern — for brevity, delegate to a helper
    // ensure_session_member(&state, &sid, &caller).await (see Step 4)
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN","message":"not a session participant"}))).into_response();
    }
    let cmd = PrepareUploadCommand {
        session_id: sid.clone(), file_name: body.file_name, size: body.size, mime_type: body.mime_type,
        caller: caller_to_actor_ref(&caller),
    };
    match state.services.session_files.prepare_upload(cmd).await {
        Ok(r) => {
            let mut v = r.client_target_json.clone();
            v["file_id"] = json!(r.file.file_id);
            (StatusCode::CREATED, Json(v)).into_response()
        }
        Err(e) => err_to_response(e),
    }
}
```

- [ ] **Step 4: `ensure_session_member` helper**

```rust
async fn ensure_session_member(state: &HttpAppState, sid: &str, caller: &GroupChatCaller) -> bool {
    // session must exist + caller is participant or owns a participating bot.
    let sess = match state.services.session_management.get(sid).await { Ok(Some(s)) => s, _ => return false };
    let group = match state.services.group_management.get(&sess.group_id).await { Ok(Some(g)) => g, _ => return false };
    match caller {
        GroupChatCaller::Bot { bot_uuid } => group.participants.iter().any(|p| &p.bot_uuid == bot_uuid),
        GroupChatCaller::Human(h) => {
            // reuse sessions::human_has_group_access — make it pub(crate) (see note)
            crate::routes::sessions::human_has_group_access(&state, &group, &h.actor_id, &h.staff_no).await
        }
    }
}
```

> **实现前置**：把 `sessions.rs` 的 `human_has_group_access` 改为 `pub(crate)`（当前是私有 fn），并在 `sessions.rs` 确认 `state.services.group_management` 字段名（实现时 `grep -n "group_management\|registry" state.rs` 对齐真实 Services 字段名 `registry`/`group_management`）。

- [ ] **Step 5: `upload_bytes` (PUT .../content) + `complete_upload`**

```rust
pub async fn upload_bytes(
    State(state): State<HttpAppState>, Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap, uri: Uri,
    body: axum::body::Bytes,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN"}))).into_response();
    }
    let part = uri.query()
        .and_then(|q| q.split('&').find(|p| p.starts_with("part=")))
        .and_then(|p| p[5..].parse().ok());
    let content_length = body.len() as u64;
    let stream = bcs_storage_api::byte_stream_from_bytes(body.into());
    match state.services.session_files.stream_upload(&sid, &file_id, part, stream, content_length).await {
        Ok(()) => (StatusCode::ACCEPTED, Json(json!({"file_id": file_id, "status": "Pending"}))).into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn complete_upload(
    State(state): State<HttpAppState>, Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap, uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN"}))).into_response();
    }
    match state.services.session_files.complete_upload(&sid, &file_id).await {
        Ok(f) => (StatusCode::OK, Json(json!(to_dto(&f)))).into_response(),
        Err(e) => err_to_response(e),
    }
}
```

`byte_stream_from_bytes` 已在 Task 3 Step 5 暴露（`bcs_storage_api::byte_stream_from_bytes`，入参 `bytes::Bytes`；`axum::body::Bytes` 经 `.into()` 转 `bytes::Bytes`）。

- [ ] **Step 6: `delete_file` + `list_files` + `get_file` + `capabilities`**

`delete_file`：`DELETE /sessions/{sid}/files/{file_id}`。鉴权所需的 `session_creator`/`driver_bot` 由本 handler 解析后填入 command；行不存在也 204（service `delete_file` 对不存在行返 `Ok(())`）。

```rust
pub async fn delete_file(
    State(state): State<HttpAppState>, Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap, uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN"}))).into_response();
    }
    // Resolve mutate-authz inputs: session creator (session.created_by) + group driver bot.
    let sess = state.services.session_management.get(&sid).await.ok().flatten();
    let group = sess.as_ref().and_then(|s| state.services.group_management.get(&s.group_id).await.ok().flatten());
    let session_creator = sess.as_ref().and_then(|s| s.created_by.clone());
    let driver_bot = group.as_ref().map(|g| g.driver_bot.clone());
    let cmd = DeleteFileCommand {
        session_id: sid, file_id, caller: caller_to_actor_ref(&caller),
        caller_identities: caller_identities(&state, &caller).await,
        session_creator, driver_bot,
    };
    match state.services.session_files.delete_file(cmd).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(), // 204 even if row absent (idempotent)
        Err(e) => err_to_response(e),
    }
}

pub async fn list_files(
    State(state): State<HttpAppState>, Path(sid): Path<String>,
    headers: HeaderMap, uri: Uri, Query(q): Query<ListQuery>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN"}))).into_response();
    }
    let params = SessionFileListParams {
        prefix: q.prefix, limit: q.limit.unwrap_or(100),
        marker: q.marker,
    };
    match state.services.session_files.list(&sid, params).await {
        Ok(page) => Json(json!({
            "items": page.items.iter().map(to_dto).collect::<Vec<_>>(),
            "truncated": page.truncated, "next_marker": page.next_marker,
        })).into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn get_file(
    State(state): State<HttpAppState>, Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap, uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN"}))).into_response();
    }
    match state.services.session_files.get(&sid, &file_id).await {
        Ok(f) => (StatusCode::OK, Json(json!(to_dto(&f)))).into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn capabilities(
    State(state): State<HttpAppState>, Path(sid): Path<String>,
    headers: HeaderMap, uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN"}))).into_response();
    }
    let c = state.services.session_files.capabilities().await;
    (StatusCode::OK, Json(json!(c))).into_response()
}
```

> `SessionFileListParams`、`ListQuery` import 见 Step 1。`Session` / `Group` 的字段名（`created_by`/`driver_bot`/`group_id`）以 `bcs-service-api` 真实定义为准——实现时 `grep -n "pub created_by\|pub driver_bot\|pub group_id" crates/service-api/bcs-service-api/src/` 核对。

- [ ] **Step 7: `download_content`（302 / 流式）**

```rust
pub async fn download_content(
    State(state): State<HttpAppState>, Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap, uri: Uri, Query(q): Query<DownloadQuery>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN"}))).into_response();
    }
    match state.services.session_files.download_route(&sid, &file_id, q.ttl).await {
        Ok((file, route)) => match route.presign {
            Some(ticket) => Redirect::to(&ticket.download_url).into_response(),
            None => {
                // local: stream via get_stream
                match state.services.session_files.get_stream(&sid, &file_id).await {
                    Ok((_f, stream)) => {
                        let mut h = HeaderMap::new();
                        h.insert("content-type", file.mime_type.parse().unwrap());
                        h.insert("content-length", file.size.to_string().parse().unwrap());
                        h.insert("content-disposition",
                            format!("attachment; filename=\"{}\"", file.file_name).parse().unwrap());
                        (h, Body::from_stream(stream)).into_response()
                    }
                    Err(e) => err_to_response(e),
                }
            }
        },
        Err(e) => err_to_response(e),
    }
}
```

> `Body::from_stream(stream)`：`stream` 是 `ByteStream = Box<dyn ByteStreamTrait + Send + Unpin>`，`ByteStreamTrait: futures::Stream<Item = Result<Bytes, io::Error>>`。axum 0.8 `Body::from_stream` 要求 `S: Stream<Item=Result<I,E>> + Send + 'static`，`I: Into<Bytes>`，`E: Into<BoxError>` —— 满足。

- [ ] **Step 8: `share_mint` + `shared_file_meta` + `shared_file_content`（无鉴权）**

```rust
pub async fn share_mint(
    State(state): State<HttpAppState>, Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap, uri: Uri, Json(body): Json<ShareRequest>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c, Err(_) => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response(),
    };
    // member check first (mint requires participant); mutate authz (creator/driver) done in service.
    if !ensure_session_member(&state, &sid, &caller).await {
        return (StatusCode::FORBIDDEN, Json(json!({"error":"FORBIDDEN"}))).into_response();
    }
    let sess = state.services.session_management.get(&sid).await.ok().flatten();
    let group = sess.as_ref().and_then(|s| state.services.group_management.get(&s.group_id).await.ok().flatten());
    let session_creator = sess.as_ref().and_then(|s| s.created_by.clone());
    let driver_bot = group.as_ref().map(|g| g.driver_bot.clone());
    let cmd = ShareMintCommand {
        session_id: sid, file_id, caller: caller_to_actor_ref(&caller),
        ttl_seconds: body.ttl_seconds,
        caller_identities: caller_identities(&state, &caller).await,
        session_creator, driver_bot,
    };
    match state.services.session_files.share_mint(cmd).await {
        Ok(r) => (StatusCode::CREATED, Json(json!({"share_url": r.share_url, "share_token": r.share_token, "expires_at": r.expires_at}))).into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn shared_file_meta(
    State(state): State<HttpAppState>, Path(sid): Path<String>, Query(q): Query<DownloadQuery>,
) -> Response {
    let token = match q.token { Some(t) => t, None => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response() };
    match state.services.session_files.share_consume(&sid, &token).await {
        Ok(r) => (StatusCode::OK, Json(json!(to_dto(&r.file)))).into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn shared_file_content(
    State(state): State<HttpAppState>, Path(sid): Path<String>, Query(q): Query<DownloadQuery>,
) -> Response {
    let token = match q.token { Some(t) => t, None => return (StatusCode::UNAUTHORIZED, Json(json!({"error":"UNAUTHORIZED"}))).into_response() };
    // reuse download_route logic but via share_consume-verified file:
    match state.services.session_files.share_consume(&sid, &token).await {
        Ok(r) => {
            let file = r.file;
            match state.services.session_files.download_route(&file.session_id, &file.file_id, q.ttl).await {
                Ok((_f, route)) => match route.presign {
                    Some(ticket) => Redirect::to(&ticket.download_url).into_response(),
                    None => match state.services.session_files.get_stream(&file.session_id, &file.file_id).await {
                        Ok((_f, stream)) => {
                            let mut h = HeaderMap::new();
                            h.insert("content-type", file.mime_type.parse().unwrap());
                            h.insert("content-disposition", format!("attachment; filename=\"{}\"", file.file_name).parse().unwrap());
                            (h, Body::from_stream(stream)).into_response()
                        }
                        Err(e) => err_to_response(e),
                    }
                },
                Err(e) => err_to_response(e),
            }
        }
        Err(e) => err_to_response(e),
    }
}
```

- [ ] **Step 9: 注册路由（静态段优先）**

`routes/mod.rs` 加 `pub mod session_files;`。`router.rs` `build_api_routes()` 内（在现有 session 路由块后）注册：

```rust
// Session file workspace
.route(
    "/sessions/{sid}/files",
    get(routes::session_files::list_files).post(routes::session_files::prepare_upload),
)
// STATIC segment MUST precede the {file_id} param segment — axum matchit is
// static-first by default, but we add an explicit startup test (Step 11).
.route("/sessions/{sid}/files/capabilities", get(routes::session_files::capabilities))
.route("/sessions/{sid}/files/{file_id}", get(routes::session_files::get_file).delete(routes::session_files::delete_file))
.route("/sessions/{sid}/files/{file_id}/content",
    get(routes::session_files::download_content).put(routes::session_files::upload_bytes))
.route("/sessions/{sid}/files/{file_id}/complete", post(routes::session_files::complete_upload))
.route("/sessions/{sid}/files/{file_id}/share", post(routes::session_files::share_mint))
.route("/sessions/{sid}/shared-file", get(routes::session_files::shared_file_meta))
.route("/sessions/{sid}/shared-file/content", get(routes::session_files::shared_file_content))
```

- [ ] **Step 10: HTTP 层测试（FakeStoragePlugin + memory store）**

`bcs-http` 既有 in-process 测试基建（参考 `client.rs` 内嵌 test server / `routes/sessions.rs` 测试）。新增 `session_files.rs` 内 `#[cfg(test)] mod tests` 覆盖：三阶段 upload→complete→download 往返、`GET /files/capabilities` 不被当 file_id、delete Ready→204 + 重复 204、delete 他人文件→403、share mint→consume（含过期→对应错误、sid 不一致→404）、local download 流式 Content-Disposition。注入 `FakeStoragePlugin` + `MemorySessionFileRepo` 构造 `SessionFileServiceImpl` 进测试 `HttpAppState`。

Run: `cargo test -p bcs-http session_files 2>&1 | tail -40`
Expected: PASS。

- [ ] **Step 11: 静态段优先回归测试**

```rust
#[tokio::test]
async fn capabilities_route_not_shadowed_by_file_id() {
    let app = build_test_app().await;
    let resp = app.get("/sessions/s1/files/capabilities").await;
    assert_eq!(resp.status(), StatusCode::OK); // not 404 FILE_NOT_FOUND
    assert!(resp.json::<serde_json::Value>().await.unwrap().get("storage").is_some());
}
```

- [ ] **Step 12: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-http/src
git commit -m "feat(bcs-http): session file workspace routes (prepare/upload/complete/delete/list/get/download/share)"
```

---

## Task 9: CLI `session file` 子命令 + `BcsClient` 方法 + 跨主机鉴权隔离

扩展 `SessionCommands` 加 `File` 子命令；`BcsClient` 加三阶段上传/下载/列表/删除/分享/能力；reqwest 跨主机 redirect 策略剥离 `Authorization`；直传后端 URL 时不带 Bearer；回归测试断言跨主机请求不含 `Authorization`。

**Files:**
- Modify: `crates/tools/bcs-cli/src/main.rs`（`SessionFileCommands` enum + `File` variant in `SessionCommands` + dispatch）
- Modify: `crates/tools/bcs-cli/src/client.rs`（方法 + redirect policy + auth-free PUT）

**Interfaces:**
- Produces：`SessionFileCommands::{Upload,List,Download,Delete,Share,Capabilities}` clap 变体；`BcsClient::{prepare_session_file, put_session_file_bytes, complete_session_file, list_session_files, delete_session_file, get_session_file, download_session_file, share_session_file, session_file_capabilities}`
- Consumes: Task 8 HTTP 端点

- [ ] **Step 1: `BcsClient` 跨主机 redirect 策略 + auth-free PUT**

`client.rs` 现有 `BcsClient` 构造（`reqwest::Client::builder().timeout(...).build()`）改为带自定义 redirect policy：

```rust
fn build_http_client() -> reqwest::Client {
    // Follow up to 10 redirects, but NEVER forward Authorization to a different host
    // (OSS presigned URLs self-authenticate; Bearer must not leak cross-host).
    let policy = reqwest::redirect::Policy::custom(move |attempt| {
        if attempt.previous().len() >= 10 {
            attempt.error("too many redirects")
        } else if attempt.previous().is_empty() {
            attempt.follow()
        } else {
            // cross-host hop: still follow, but reqwest strips Authorization on
            // cross-host by default; we keep default behavior and assert in tests.
            attempt.follow()
        }
    });
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .redirect(policy)
        .build()
        .expect("reqwest client")
}
```

> **关键**：reqwest **默认**在跨 host 重定向时已剥离 `Authorization`（这是 reqwest 的既定行为）。`Policy::custom` 的 `attempt.follow()` 保留此默认剥离。spec 要求"显式配置 + 回归测试"，本步骤显式构造 policy（不依赖 `Client::default()`）+ Step 5 回归测试。**upload 直传**（PUT 到后端 URL，非 redirect 场景）必须**完全不调 `add_auth`**：见 Step 3。

把现有 4 处 `reqwest::Client::builder().timeout(...).build()`（`client.rs:64,80,100,120,139`）替换为调用 `build_http_client()`。

- [ ] **Step 2: `BcsClient` 三阶段上传方法**

```rust
// POST /sessions/{sid}/files -> prepare JSON (含 upload_url 或 parts)
pub async fn prepare_session_file(&self, sid: &str, file_name: &str, size: u64, mime: &str)
    -> Result<serde_json::Value> {
    let url = format!("{}/sessions/{}/files", self.base_url, urlencoding::encode(sid));
    let body = serde_json::json!({ "file_name": file_name, "size": size, "mime_type": mime });
    let resp = self.add_auth(self.http_client.post(&url).json(&body)).send().await
        .context("prepare session file")?;
    Self::ensure_success(resp, "prepare session file").await
}

// PUT bytes to an upload_url. If the upload_url host != BCS base_url host, do NOT
// attach Authorization (backend presigned URL self-authenticates).
pub async fn put_session_file_bytes(&self, upload_url: &str, bytes: reqwest::Body) -> Result<()> {
    let bcs_host = reqwest::Url::parse(&self.base_url).ok().and_then(|u| u.host_str().map(String::from));
    let target_host = reqwest::Url::parse(upload_url).ok().and_then(|u| u.host_str().map(String::from));
    let cross_host = match (bcs_host.as_deref(), target_host.as_deref()) {
        (Some(a), Some(b)) => a != b,
        _ => false,
    };
    let mut req = self.http_client.put(upload_url).body(bytes);
    if !cross_host {
        req = self.add_auth(req); // same-host (local proxy): keep bearer
    }
    // cross-host: send bare PUT, NO Authorization
    let resp = req.send().await.context("put session file bytes")?;
    Self::ensure_success_status(resp, "put session file bytes").await
}

pub async fn complete_session_file(&self, sid: &str, file_id: &str) -> Result<serde_json::Value> {
    let url = format!("{}/sessions/{}/files/{}/complete", self.base_url, urlencoding::encode(sid), file_id);
    let resp = self.add_auth(self.http_client.post(&url).json(&serde_json::json!({}))).send().await
        .context("complete session file")?;
    Self::ensure_success(resp, "complete session file").await
}
```

> `ensure_success` / `ensure_success_status` **不是现成 helper**——现有 `BcsClient` 方法用内联 `if !response.status().is_success() { return Err(anyhow!(...)) }`。实现时先把它们抽取为 `BcsClient` 上的私有 helper（`async fn ensure_success(resp, label) -> Result<serde_json::Value>` 解析 JSON、`async fn ensure_success_status(resp, label) -> Result<()>` 仅判 status），再用于本任务所有新方法。不要假设它们已存在。

- [ ] **Step 3: `upload` 高阶封装（三阶段自动串）**

```rust
pub async fn upload_session_file(&self, sid: &str, path: &str, name_override: Option<&str>, mime: Option<&str>)
    -> Result<serde_json::Value> {
    let file_name = name_override.map(String::from)
        .unwrap_or_else(|| std::path::Path::new(path).file_name().and_then(|n| n.to_str()).unwrap_or("file").to_string());
    let metadata = tokio::fs::metadata(path).await?;
    let size = metadata.len();
    let mime = mime.unwrap_or("application/octet-stream").to_string();
    let prepared = self.prepare_session_file(sid, &file_name, size, &mime).await?;
    let mode = prepared["mode"].as_str().unwrap_or("single");
    let file_id = prepared["file_id"].as_str().context("missing file_id")?.to_string();
    match mode {
        "single" => {
            let url = prepared["upload_url"].as_str().context("missing upload_url")?.to_string();
            // reqwest::Body does NOT impl From<std::fs::File> — stream an open tokio File:
            let file = tokio::fs::File::open(path).await?;
            self.put_session_file_bytes(&url, reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(file))).await?;
        }
        "multipart" => {
            let part_size = prepared["part_size"].as_u64().context("missing part_size")? as usize;
            let parts = prepared["parts"].as_array().context("missing parts")?;
            // 串行 PUT 各 part（CLI 简单实现，不做并行；并行可为后续优化）
            for (i, p) in parts.iter().enumerate() {
                let url = p["upload_url"].as_str().context("missing part url")?.to_string();
                let mut f = tokio::fs::File::open(path).await?;
                // tokio AsyncSeekExt must be in scope:
                use tokio::io::{AsyncSeekExt as _};
                f.seek(std::io::SeekFrom::Start((i as u64) * part_size as u64)).await?;
                let take = f.take(part_size as u64);
                self.put_session_file_bytes(&url, reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(take))).await?;
            }
        }
        _ => return Err(anyhow!("unknown mode {}", mode)),
    }
    let final_file = self.complete_session_file(sid, &file_id).await
        .or_else(|e| {
            // best-effort cancel on failure
            let _ = self.delete_session_file(sid, &file_id);
            Err(e)
        })?;
    Ok(final_file)
}
```

> `tokio-util` 0.7 `io` feature 需加到 `bcs-cli` dev/dep；`bcs-cli` 现有依赖确认后加 `tokio-util = { workspace = true, features = ["io"] }`。reqwest 已带 `stream` workspace feature；`reqwest::Body::wrap_stream` + `response.bytes_stream()` 均可。

- [ ] **Step 4: 其余 `BcsClient` 方法**

`list_session_files`（GET `/files?prefix=&limit=&marker=`）、`delete_session_file`（DELETE `/files/{id}`）、`get_session_file`（GET `/files/{id}`）、`download_session_file`（GET `/files/{id}/content?ttl=`，跟随 302 流式写 `--out`）、`share_session_file`（POST `/files/{id}/share`）、`session_file_capabilities`（GET `/files/capabilities`）。download 用 `response.bytes_stream()` 写文件：

```rust
pub async fn download_session_file(&self, sid: &str, file_id: &str, out: Option<&str>, ttl: Option<u64>) -> Result<String> {
    let mut url = format!("{}/sessions/{}/files/{}/content", self.base_url, urlencoding::encode(sid), file_id);
    if let Some(t) = ttl { url.push_str(&format!("?ttl={}", t)); }
    let resp = self.add_auth(self.http_client.get(&url)).send().await.context("download session file")?;
    if !resp.status().is_success() {
        let s = resp.status(); let b = resp.text().await.unwrap_or_default();
        return Err(anyhow!("download failed ({}): {}", s, b));
    }
    // resolve out path: from content-disposition or file_id default
    let out_path = out.map(String::from).unwrap_or_else(|| format!("./{}", file_id));
    let mut f = tokio::fs::File::create(&out_path).await?;
    use futures::StreamExt;
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        f.write_all(&chunk?).await?;
    }
    Ok(out_path)
}
```

- [ ] **Step 5: 跨主机 `Authorization` 剥离回归测试**

`client.rs` 内 `#[cfg(test)]`：

```rust
#[tokio::test]
async fn cross_host_put_omits_authorization() {
    use std::sync::{Arc, Mutex};
    let captured = Arc::new(Mutex::new(None::<Option<String>>));
    let captured2 = captured.clone();
    let server = wiremock::MockServer::start().await; // 若无 wiremock，用内嵌 tokio TcpListener 接收一行请求头
    // BCS base = 127.0.0.1:<a>, upload target = 127.0.0.1:<b> (different port => same host diff port;
    // to test cross-HOST, use "localhost" vs "127.0.0.1" — reqwest treats them as different hosts).
    // ...构造 BcsClient base=http://localhost:<a>, upload_url=http://127.0.0.1:<b>/put
    // 断言 captured 收到的请求头不含 "authorization"
    assert!(captured2.lock().unwrap().as_ref().map_or(true, |h| h.is_none()), "Authorization must not be sent cross-host");
}
```

> **wiremock 可用性**：仓库若无 `wiremock` workspace dep，则用内嵌 `tokio::net::TcpListener` 接收原始请求字节、断言不含 `authorization:` 行（参考 `client.rs` 既有内嵌 test server 模式 —— 第二个 agent 报告 `client.rs:2817+` 有 `TcpListener` 测试）。实现者照既有模式写，不引入新 crate。

- [ ] **Step 6: clap `SessionFileCommands` + `File` variant + dispatch**

`main.rs`：`SessionCommands` enum 加 variant：

```rust
/// Manage the session shared file workspace (upload/download/share/list/delete).
File {
    #[command(subcommand)]
    command: SessionFileCommands,
},
```

新增 enum：

```rust
#[derive(Subcommand)]
enum SessionFileCommands {
    /// Upload a local file (auto three-stage: prepare -> PUT -> complete).
    Upload {
        #[arg(short, long)] session: String,
        #[arg(long)] path: String,
        #[arg(long)] name: Option<String>,
        #[arg(long)] mime: Option<String>,
    },
    /// List files in the session workspace.
    List {
        #[arg(short, long)] session: String,
        #[arg(long)] prefix: Option<String>,
        #[arg(long)] limit: Option<u32>,
        #[arg(long)] marker: Option<String>,
    },
    /// Download a file's bytes (follows presigned redirect or streams).
    Download {
        #[arg(short, long)] session: String,
        #[arg(long)] file_id: String,
        #[arg(long)] out: Option<String>,
        #[arg(long)] ttl: Option<u64>,
    },
    /// Delete a file or cancel an in-progress upload.
    Delete {
        #[arg(short, long)] session: String,
        #[arg(long)] file_id: String,
    },
    /// Generate a no-auth share link (valid until ttl expiry).
    Share {
        #[arg(short, long)] session: String,
        #[arg(long)] file_id: String,
        #[arg(long)] ttl: Option<u64>,
    },
    /// Query backend capabilities (storage / presign / max_size).
    Capabilities {
        #[arg(short, long)] session: String,
    },
}
```

dispatch arm（`SessionCommands::File { command }` → match `SessionFileCommands`，每个调 `BcsClient` 对应方法，JSON/`--no-json` 输出对齐现有 session 子命令处理）。token 从 `Session` parent 的 `--token` 或 env/session.json 解析（现有逻辑），传给构造 `BcsClient`。

- [ ] **Step 7: 编译 + CLI 烟测**

Run: `cargo build -p bcs-cli 2>&1 | tail -20`；`cargo run -p bcs-cli -- session file --help 2>&1 | tail -20`
Expected: 编译通过 + 子命令 help 列出 upload/list/download/delete/share/capabilities。

- [ ] **Step 8: Commit**

```bash
git add src/bcs/crates/tools/bcs-cli/src/main.rs src/bcs/crates/tools/bcs-cli/src/client.rs src/bcs/Cargo.toml
git commit -m "feat(bcs-cli): session file subcommands + three-stage upload + cross-host auth isolation"
```

---

## Task 10: bcs-coordination skill 更新

新增 `references/session-file.md`（对标本仓库 `references/session.md` 体例），更新 `SKILL.md`（场景表 + 选择树 + 注意事项），`session.md` 加交叉链接。全部内容契约已在 design.md「bcs-coordination skill 更新」节锁定。

**Files:**
- Create: `crates/tools/bcs-cli/bcs-coordination/references/session-file.md`
- Modify: `crates/tools/bcs-cli/bcs-coordination/SKILL.md`
- Modify: `crates/tools/bcs-cli/bcs-coordination/references/session.md`

**Interfaces:** 无（文档）。

- [ ] **Step 1: 写 `references/session-file.md`**

对齐 `session.md` 体例：标题 → 概述 → 概念（会话工作区 / file_id 唯一允许同名 / FileStatus / Ready 才可下载分享删除）→ 权限（参与者可 upload/download/list/share；删除/取消限上传者或创建者/driver）→ 命令清单表（`session file upload|list|download|delete|share|capabilities`，必需参数 + 说明）→ 每命令示例（upload 小文件 + 大文件 ≥100MB 自动 multipart 并行 PUT；download 跟随 302；share 裸 URL 无 CLI 子命令；capabilities 预判字节是否直连后端/需可达 OSS）→ 返回结果汇总表。要点对齐 spec：100MB 是阈值非硬截断、`expires_at`/`method` 在 multipart 响应最外层、分享用独立密钥。

完整结构模板（实现时照 `session.md` 排版落字，不在此展开全文）：

```
# BCS Session 文件工作区命令
（一段概述：在同一个 Session 内 bot/human 上传/下载/分享/列举/删除共享文件）

## 概念
- 会话工作区（session workspace）= 一个 Session 内 bot/human 共享的文件区
- file_id 全局唯一、URL-safe、不透明；同一会话允许同名文件
- FileStatus：Pending（已 prepare 等上传/完成）/ Ready（可下载分享删除）/ Failed（上传失败可删后重传）
- 仅 Ready 可下载/分享/删除

## 权限
- 会话参与者可 upload / download / list / share
- 删除/取消限上传者，或会话创建者 / 该 group 的 driver bot

## 命令列表
| 命令 | 必需参数 | 说明 |
| session file upload | --session, --path | 上传本地文件（自动三阶段） |
| session file list | --session | 列出工作区文件 |
| session file download | --session, --file-id | 下载文件字节 |
| session file delete | --session, --file-id | 删除文件 / 取消上传 |
| session file share | --session, --file-id | 生成分享链接 |
| session file capabilities | --session | 查询后端能力 |

## session file upload - 上传
（说明三阶段封装；upload_url 指向随后端：presign 后端直传后端字节不经 BCS、local 经 BCS；失败会尝试 DELETE 取消）
小文件示例：
bcs session file upload --session "grp-001:1a2b3c4d" --path ./report.pdf
大文件（≥100MB 自动 multipart）示例：
bcs session file upload --session "grp-001:1a2b3c4d" --path ./model.bin --mime application/octet-stream

## session file list / download / delete / share / capabilities
（各一示例，download 写 --out，share 说明返回 share_url 裸 URL 下载无需 CLI 子命令，capabilities 打印 {storage,presign_upload,presign_download,max_size}）

## 返回结果汇总
| 命令 | 关键返回字段 |
（同 session.md 汇总表风格）

> 100MB 是单片/分段阈值（非硬截断）；超 100MB 自动 multipart。分享链接用独立密钥签发，过期前不可撤销（删文件即失效）。
```

- [ ] **Step 2: 改 `SKILL.md`**

`## 场景指南` 表（在 `session` 行后）加一行：

```
| session-file | 会话工作区文件上传/下载/分享/列/删 | [references/session-file.md](references/session-file.md) |
```

`## 协作模式快速选择` 树（在 `session` 分支后）加一支：

```
    ├─ 需要在群组内共享文件？
    │     └─ 是 → 使用 session file → 读取 references/session-file.md
```

`## 注意事项` 末尾加：

```
8. **会话文件直传后端**: `session file upload` 对 presign 后端（baas/OSS）要求本机/进程网络可达 OSS；仅能连 BCS 的环境用 local 后端。跨主机 PUT 到后端 OSS URL 时 Bearer 不应发送（OSS 预签名 URL 自鉴权），`bcs` CLI 已处理；自定义客户端需注意。
```

> **实现前置**：`SKILL.md` 选择树现状有结构瑕疵（尾部两支缩进异常，见 grounding 报告）。编辑时**保留**现有文本、仅在 `session` 分支后插入新支，不顺手重构既有缩进，缩小 diff。

- [ ] **Step 3: `session.md` 交叉链接**

`references/session.md` 命令清单表后"相关 reference"区块加：

```
> - 会话内共享文件（上传/下载/分享）详见 [session-file.md](session-file.md)
```

- [ ] **Step 4: Commit**

```bash
git add src/bcs/crates/tools/bcs-cli/bcs-coordination
git commit -m "docs(bcs-coordination): add session-file reference + SKILL/index updates"
```

---

## Task 11: bootstrap 装配 + 配置 + delete_session 钩子

在 `config.rs` 加 `SessionFilesConfig`/`SessionFilesShareConfig`；在 `server.rs`（standalone memory 与 mysql 两处）构造 `LocalStoragePlugin`/`SessionFileRepoPort`/`SessionFileServiceImpl`，注入 `Services`；share secret 随机 fallback（对标 invite）；`delete_session` 调 `session_files.delete_all_for_session`；启动 Pending sweep 定时器。配置 toml 加 `[session_files]` 块。

**Files:**
- Modify: `crates/bootstrap/bcs/src/config.rs`（+ `SessionFilesConfig`）
- Modify: `crates/bootstrap/bcs/src/server.rs`（装配两处 + sweep + delete hook）
- Modify: `crates/bootstrap/bcs/src/http_adapter.rs`（或 server.rs，share secret fallback 构造 service）
- Modify: `crates/service-api/bcs-services-container/src/services.rs`（+ `session_files` 字段 + builder + build）
- Modify: `configs/bcs-config-example.toml`、`configs/bcs-config-local.toml`（+ `[session_files]` 块）
- Modify: `crates/services/bcs-session/src/`（`delete_session` 钩子调用 `delete_all_for_session`）—— **或** 在 HTTP `delete_session` handler 调（见 Step 4 决策）

**Interfaces:**
- Produces：`SessionFilesConfig { storage_backend: String, multipart_threshold: u64, max_file_size: u64, data_dir: Option<String>, share: SessionFilesShareConfig }`、`SessionFilesShareConfig { token_secret: Option<String>, default_ttl_seconds: u64, share_base_url: Option<String> }`

- [ ] **Step 1: 配置结构 + toml**

`config.rs`（对标 `InviteConfig`）：

```rust
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SessionFilesConfig {
    #[serde(default = "default_local")] pub storage_backend: String,
    #[serde(default = "default_multipart_threshold")] pub multipart_threshold: u64,
    #[serde(default = "default_max_file_size")] pub max_file_size: u64,
    #[serde(default)] pub data_dir: Option<String>,
    #[serde(default)] pub share: SessionFilesShareConfig,
}
fn default_local() -> String { "local".into() }
fn default_multipart_threshold() -> u64 { 104_857_600 }
fn default_max_file_size() -> u64 { 5_368_709_120 }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SessionFilesShareConfig {
    #[serde(default)] pub token_secret: Option<String>,
    #[serde(default = "default_share_ttl")] pub default_ttl_seconds: u64,
    #[serde(default)] pub share_base_url: Option<String>,
}
fn default_share_ttl() -> u64 { 86400 }
```

在顶层 config 结构里加 `#[serde(default)] pub session_files: SessionFilesConfig`（对标 `invite` 字段位置）。toml 两处加：

```toml
[session_files]
storage_backend = "local"
multipart_threshold = 104857600
max_file_size = 5368709120
data_dir = "/var/bcs/session-files"

[session_files.share]
token_secret = "replace-with-share-token-secret"
default_ttl_seconds = 86400
share_base_url = "https://bcs.example.com"
```

- [ ] **Step 2: `Services` bundle 加 `session_files`**

`bcs-services-container/src/services.rs`：struct 加 `pub session_files: Arc<dyn bcs_service_api::application::session_files::SessionFileService>,`；builder 加 `.session_files(...)` setter；`build()` 加 `session_files: required(self.session_files, "session_files")?`。`bcs-services-container` Cargo.toml 确保依赖 `bcs-service-api`（已有）。

- [ ] **Step 3: 装配（server.rs 两处）**

在 `server.rs` standalone（~line 907 旁）与 mysql-backed（~line 2071 旁）两处，构造 storage plugin + repo + service：

```rust
// storage plugin (local v1)
let data_dir = std::path::PathBuf::from(
    config.session_files.data_dir.clone().unwrap_or_else(|| format!("{}/session-files", data_root)));
let _ = std::fs::create_dir_all(&data_dir);
let storage: Arc<dyn bcs_storage_api::StoragePlugin> = Arc::new(
    bcs_storage_local::LocalStoragePlugin::new(bcs_storage_local::LocalStorageConfig {
        data_dir,
        max_object_size: config.session_files.max_file_size,
    }),
);
// repo (memory in standalone; mysql in mysql-backed mode)
let file_repo: Arc<dyn bcs_service_api::port::repo::SessionFileRepoPort> =
    Arc::new(bcs_session_file_store::MemorySessionFileRepo::new()); // standalone
// (mysql mode): Arc::new(bcs_session_file_store::MySqlSessionFileStore::new(db.clone(), env.clone()))

// share secret fallback (independent of invite)
let share_secret = config.session_files.share.token_secret.as_deref()
    .map(|s| s.as_bytes().to_vec())
    .unwrap_or_else(|| {
        tracing::warn!("session_files.share.token_secret not configured — generating random secret (share tokens will not survive restart)");
        (0..32).map(|_| fastrand::u8(..)).collect()
    });

let session_file_service: Arc<dyn bcs_service_api::application::session_files::SessionFileService> =
    Arc::new(bcs_session_file::SessionFileServiceImpl::new(bcs_session_file::SessionFileServiceConfig {
        storage: storage.clone(),
        repo: file_repo.clone(),
        session_repo: session_repo.clone(), // 变量名以 server.rs 现有为准确认（grep "SessionRepoPort" crates/bootstrap/bcs/src/server.rs）
        env: env.clone(), // 与 file_repo（MySqlSessionFileStore::new(db, env)）同一 env；standalone 与 mysql 两处装配均须填
        max_size: config.session_files.max_file_size,
        multipart_threshold: config.session_files.multipart_threshold,
        bcs_base_url: format!("http://{}", config.bind), // or configured external URL
        share_secret,
        share_default_ttl: config.session_files.share.default_ttl_seconds,
        share_base_url: config.session_files.share.share_base_url.clone(),
    }));

// inject into Services builder:
//   .session_files(session_file_service.clone())
```

> `bcs-storage-local`/`bcs-session-file`/`bcs-session-file-store` 须加入 `bcs`（bootstrap）crate 的 `[dependencies]`（`Cargo.toml` of `crates/bootstrap/bcs`）。

- [ ] **Step 4: `delete_session` 钩子**

**决策**：在 HTTP `delete_session` handler（`routes/sessions.rs`，line ~1757 旁）成功删 session 后，调 `state.services.session_files.delete_all_for_session(&sid).await`（best-effort，错误记日志不影响 session 删除成功响应）—— 对标 spec「部分失败语义」。修改 `delete_session`：在 `state.services.session_management.delete(&sid).await` Ok(true) 分支后加：

```rust
match state.services.session_files.delete_all_for_session(&sid).await {
    Ok(n) => tracing::info!(session_id=%sid, deleted=n, "cleaned up session files"),
    Err(e) => tracing::warn!(error=?e, session_id=%sid, "session file cleanup partial failure (orphan sweep will reconcile)"),
}
```

- [ ] **Step 5: Pending sweep 定时器**

在 `server.rs` 启动后台 task（对标现有 timeout scanner，~line 1118 旁）：

```rust
let sweep_svc = session_file_service.clone();
tokio::spawn(async move {
    let mut interval = tokio::time::interval(std::time::Duration::from_secs(300));
    loop {
        interval.tick().await;
        match sweep_svc.sweep_expired_pending().await {
            Ok(n) if n > 0 => tracing::info!(swept=n, "session file pending sweep"),
            Ok(_) => {}
            Err(e) => tracing::warn!(error=?e, "session file pending sweep error"),
        }
    }
});
```

- [ ] **Step 6: 编译 + 启动集成**

Run: `cargo build -p bcs 2>&1 | tail -30`
Expected: 编译通过（含两处装配点、Services bundle、钩子、sweep）。

- [ ] **Step 7: Commit**

```bash
git add src/bcs/crates/bootstrap/bcs src/bcs/crates/service-api/bcs-services-container src/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs src/bcs/configs
git commit -m "feat(bcs): bootstrap session file workspace (config + wiring + delete hook + pending sweep)"
```

---

## Task 12: 端到端集成验证

用 local 后端跑通完整 HTTP + CLI 往返，覆盖 spec 所有 v1 路径。

**Files:**
- Create: `crates/adapters/http/bcs-http/tests/session_files_e2e.rs`（或在既有 e2e 测试基建内）—— 若仓库无 e2e harness 则以 Task 8 的 in-process 测试为最终门，本任务做手动脚本验证。

- [ ] **Step 1: 启动 standalone bcs**

Run: `cargo run -p bcs -- --config configs/bcs-config-local.toml`（后台）
Expected: 启动日志含 `session_files` 配置加载、`bcs_session_files` 表迁移。

- [ ] **Step 2: CLI upload → download → share → delete 往返**

```bash
SID="grp-001:00000000"
echo "hello workspace" > /tmp/hello.txt
# upload
F=$(bcs session file upload --session "$SID" --path /tmp/hello.txt | jq -r .file_id)
# list
bcs session file list --session "$SID"
# download
bcs session file download --session "$SID" --file-id "$F" --out /tmp/hello.dl
diff /tmp/hello.txt /tmp/hello.dl   # must be empty
# share
SHARE=$(bcs session file share --session "$SID" --file-id "$F" | jq -r .share_url)
curl -sL "$SHARE" -o /tmp/hello.share
diff /tmp/hello.txt /tmp/hello.share
# delete
bcs session file delete --session "$SID" --file-id "$F"   # 204
bcs session file delete --session "$SID" --file-id "$F"   # 204 idempotent
# capabilities
bcs session file capabilities --session "$SID"
```
Expected: 全部成功，diff 为空，二次 delete 不报错，capabilities `storage="local"`、`presign_upload=false`、`presign_download=false`。

- [ ] **Step 3: 大文件 multipart（≥100MB）**

```bash
head -c 150000000 /dev/urandom > /tmp/big.bin
F=$(bcs session file upload --session "$SID" --path /tmp/big.bin | jq -r .file_id)
bcs session file download --session "$SID" --file-id "$F" --out /tmp/big.dl
cmp /tmp/big.bin /tmp/big.dl   # must match
```
Expected: prepare 返回 `mode:"multipart"` + `parts[]`；upload 成功；cmp 一致。

- [ ] **Step 4: 取消 + Failed 分流**

```bash
# prepare but never PUT/complete -> wait sweep (>300s) OR manually trigger:
F=$(curl -s -X POST "$BCS/sessions/$SID/files" -H "Authorization: Bearer $T" -d '{"file_name":"a","size":10,"mime_type":"text/plain"}' | jq -r .file_id)
# delete while Pending -> abort path
bcs session file delete --session "$SID" --file-id "$F"   # 204 via abort_upload
```
Expected: 204，后端 temp 清理（`$data_dir` 无残留 `{key}.*.part`）。

- [ ] **Step 5: 权限 403**

用非上传者/非创建者 participant 的 token `delete` 他人文件 → `403 FORBIDDEN`。

- [ ] **Step 6: 路由静态段优先 + 302 行为**

`GET /sessions/{sid}/files/capabilities` → 200（非 404）。local 后端 download 直接流式（无 302）；记录此为 local 预期。baas 302 由 baas 插件 crate 独立验证（本计划不覆盖）。

- [ ] **Step 7: 全量测试套件回归**

Run: `cargo test -p bcs-domain -p bcs-storage-api -p bcs-storage-local -p bcs-session-file-store -p bcs-session-file -p bcs-http -p bcs-cli 2>&1 | tail -40`
Expected: 全 PASS，无既有用例回归。

- [ ] **Step 8: Commit（如有 e2e 测试文件）**

```bash
git add src/bcs/crates/adapters/http/bcs-http/tests
git commit -m "test(bcs-http): session file workspace e2e (upload/download/share/delete/multipart)"
```

---

## Self-Review

### 1. Spec 覆盖

逐条对照 spec：

| spec 条目 | 覆盖任务 |
|---|---|
| §1.1 capabilities | T8 Step 6/11、T6 `capabilities()` |
| §1.2 prepare（single §1.2.a / multipart §1.2.b，`method`/`expires_at` 最外层） | T6 `wire_client_target`、T8 Step 3、multipart threshold T11 config |
| §1.2 「跨主机剥离 Authorization + 回归测试」 | T9 Step 1/5 |
| §1.3 PUT .../content（仅 local，part param） | T6 `stream_upload`、T8 Step 5 |
| §1.4 complete | T6 `complete_upload`、T8 Step 5 |
| §1.5 DELETE 三语义 + 元数据层幂等（行不存在 204 不探测） | T6 `delete_file`、T8 Step 6 |
| §1.6 list（prefix/limit/marker，created_at 升序，允许同名） | T5 list、T6 list、T8 Step 6 |
| §1.7 get metadata | T8 Step 6 |
| §1.8 download（302/stream + ttl） | T6 `download_route`/`get_stream`、T8 Step 7 |
| §1.9 share（mint/consume，独立密钥，payload 无 session_id，过期 410） | T6 `share_mint`/`share_consume`、T8 Step 8、T1 share.rs、T11 secret fallback |
| §1.9.b/c `object_handle` 不透出 | T8 `SessionFileDto` 剥离 |
| §3 StoragePlugin trait + 全部辅助类型 + ByteStream + FakeStoragePlugin + 契约测试 | T3 |
| §3.1 bcs-storage-local（ProxyViaBcs，temp+rand，multipart concat，presign_get Unsupported，delete 幂等） | T7 |
| §3.2 baas | **不在本计划**（独立 crate，spec design-baas-plugin.md；api 未稳定，按用户指示暂不规划） |
| design.md 迁移 006（mysql + sqlite parity） | T2 |
| design.md `file_id` ULID + 唯一索引 | T1 `new_file_id`、T2 表 |
| design.md `ActorRef`/`ActorKind` | T1 Step 6（引入 `ActorRef`） |
| design.md Pending sweep（v1） | T6 `sweep_expired_pending`、T11 Step 5 |
| design.md delete_session 钩子 + 部分失败语义 | T11 Step 4 |
| design.md orphan sweep（v1 之后） | 非本计划（spec 明确 v1 之后） |
| design.md bcs-coordination skill 更新 | T10 |
| design.md 配置 `[session_files]` + `[session_files.share]` | T11 Step 1 |
| design.md `max_size` bootstrap 静态化 + capabilities 无 IO | T6 `new()` 预计算 caps、T11 注入 max_size |

**gap**：design.md「orphan 对账 sweep」明确 v1 之后 → 正确不含。baas 后端 → 用户明确指示暂不规划。

### 2. Placeholder 扫描

- T5 mysql.rs「其余方法要点」段给了 SQL 形态 + bind 要点而非逐行代码 —— 这是已识别的妥协：因 `DbStatement`/`DbValue`/`db_get_column` 真实 API 需在实现期从 `bcs-session-store/src/mysql.rs` 抄签名（grounding 确认 store crate 不直接用 mysql_async）。**风险可接受**：所有方法 SQL 与字段映射已逐条列出，实现者照 `bcs-session-store/src/mysql.rs` 模板填即可，无未知。如需更严，可在执行期由 subagent 先 `grep` `DbStatement` API 再落字。
- T10 Step 1 session-file.md 用结构模板而非全文 —— design.md 节已锁内容契约，全文体例对齐 `session.md`，属文档撰写非代码契约；可接受。
- T6 多处「按此实现」注释段是可执行的具体算法描述（drain_to、complete 扫目录、sweep、can_mutate），非占位。
- 无 "TBD"/"TODO"/"implement later"/"similar to Task N" 占位。

### 3. 类型一致性

- `SessionFileService` trait 方法签名跨 T4（定义）↔ T6（impl）↔ T8（调用）一致：`prepare_upload(PrepareUploadCommand)`、`stream_upload(&str,&str,Option<u16>,ByteStream,u64)`、`complete_upload(&str,&str)`、`delete_file(DeleteFileCommand)`、`get`/`list`/`download_route`/`share_mint(ShareMintCommand)`/`share_consume(&str,&str)`/`get_stream`/`sweep_expired_pending`/`delete_all_for_session`。
- **mutate 鉴权一致**（review 修正后）：`DeleteFileCommand`/`ShareMintCommand` 在 T4 Step 2 即声明 `caller_identities: Vec<String>` + `session_creator: Option<String>` + `driver_bot: Option<String>`；service（T6 `delete_file`/`share_mint`）调 `can_mutate(&cmd.caller_identities, &row.owner, cmd.session_creator.as_deref(), cmd.driver_bot.as_deref())`；HTTP（T8 `delete_file`/`share_mint` handler）解析 `session.created_by` + `group.driver_bot` 填入 command，不做判断。`PrepareUploadCommand` 不带 `caller_identities`（prepare 仅需 participant 校验）。service 的 `session_repo` 仅供 `prepare_upload` 校验会话存在用，不再用于鉴权（driver_bot 由 HTTP 经 group 注入），故 service 不依赖 `group_repo`。
- `PreparedUpload.client_target` 枚举名 `ClientUploadTarget::{Direct, ProxyViaBcs}` 跨 T3↔T6↔T8 一致；`UploadMode::{Single,Multipart}` serde lowercase 一致。
- `ByteStream = Box<dyn ByteStreamTrait + Send + Unpin>` 跨 T3↔T6↔T8 一致；`byte_stream_from_bytes` 在 T3 Step 5 暴露、T8 Step 5 经 `bcs_storage_api::byte_stream_from_bytes` 调用。
- `SessionFileRepoPort` 方法名跨 T4↔T5↔T6 一致（`insert`/`get`/`update_object_handle_and_status`/`update_status`/`delete`/`list`/`list_expired_pending`/`delete_all_for_session`）。
- `FileStatus` serde PascalCase 跨 T1↔T5(mysql `status` 列存 `"Pending"`/`"Ready"`/`"Failed"`) 一致 —— T5 `row_to_session` 用 `serde_json::from_value(String)` 还原，需确认 `FileStatus` 可从裸字符串反序列化（PascalCase rename 覆盖 `Serialize`/`Deserialize` 两向，OK）。

**修正**：T2 migration `status varchar(16)` 足够容纳 `Pending`/`Ready`/`Deleting`/`Failed`（最长 8），OK。

### 4. 已知执行期前置 grep（实现者首步做）

每个涉及既有 API 的 task 第一步 grep 真实签名，避免臆造：
- `DbStatement`/`DbValue`/`db_get_column`/`DbSqlFlavor` → `crates/plugin-api/bcs-db-api/src/lib.rs` + `crates/services/bcs-session-store/src/mysql.rs`（T5）
- `Services`/builder 字段名、`state.services.*` 真实名 → `crates/service-api/bcs-services-container/src/services.rs` + `state.rs`（T8/T11）
- `human_has_group_access` 可见性、`GroupChatCaller`/`resolve_group_chat_caller` 签名 → `routes/sessions.rs`/`group_messages.rs`（T8）
- SQLite migrations 体例 → `crates/bootstrap/bcs/src/migrations.rs`（T2）
- CLI 现有 BcsClient helper（`add_auth`/`ensure_success`）+ 内嵌 TcpListener test server → `crates/tools/bcs-cli/src/client.rs`（T9）
- `SessionRepoPort` 测试 fake → `crates/test-support`（T6）

---

## Execution Handoff

计划已完成并保存到 `docs/superpowers/plans/2026-07-20-bcs-session-workspace.md`。两种执行方式：

**1. Subagent-Driven（推荐）** — 每个 Task 派新 subagent 执行，Task 间两阶段 review，快速迭代。

**2. Inline Execution** — 在当前会话内用 executing-plans 批量执行，带 checkpoint review。

请选择执行方式。
