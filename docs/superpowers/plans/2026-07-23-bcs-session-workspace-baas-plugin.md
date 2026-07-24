# bcs-storage-baas 插件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现独立的 `bcs-storage-baas` crate（`StoragePlugin` trait 的 baas 后端实现，对接 baas「Session File Sharing API v1.1」），并在落地前置中给 BCS 框架加 backend-agnostic 装配（`StoragePluginFactory`），使 baas 可经配置装配而其余 BCS 代码无感。

**Architecture:** 三块。① BCS 仓库前置：`bcs-storage-api` 增 `StoragePluginFactory` trait + `StorageBackendConfig`；`config.rs`/`server.rs` 改用 factory 装配、对后端清单无感；`bcs-session-file` 的 `SessionFileServiceConfig` 加 `share_link_ttl` 并修 `download_route` 的 TTL bug。② 独立的 `bcs-storage-baas` crate（**独立 Cargo workspace**，仅依赖 `bcs-storage-api`）实现 `StoragePlugin`：prepare/complete/abort 直传与同步完成、presign_get 走同步 share-link、delete 按 transfer_id，全部对接 baas HTTP，object_handle 仅存 transfer_id。③ wiring：在 BCS 组合根加 baas factory arm（引入 crate 即装配）。

**Tech Stack:** Rust 2021、async-trait、reqwest（HTTP 客户端）、serde/serde_json、thiserror、tokio、wiremock（测试桩）、baas Session File Sharing API v1.1。

## Global Constraints

- 对接的是 baas「Session File Sharing API v1.1」（语雀 `klg0lpglzmwr8t3g`，权威）。统一响应体 `{"code":0,"message":"success","data":{...}}`，错误 `{"detail":{"error":<CODE>,"message":...}}`。
- **`transfer_id` 是与 baas 对接的唯一凭证**：complete / abort / delete / share-link 全部用它。`object_handle` 仅持久化 `transfer_id`（Pending 多 `type`/`expires_at`，Ready 瘦到仅 `transfer_id`），**不存 `oss_key`**（删除按 transfer_id，不按 OSS key）。
- 基础路径 `/api/v1/sessions/{tenant}/{session_id}/files`，`session_id` 用 BCS 的 session_id，`tenant` 进配置。**session_id 含冒号等非 path-safe 字符，拼接 URL 时必须 percent-encode（`:`→`%3A`）**。`transfer_id` 也需编码（虽为 hex 不含特殊字符，仍统一编码以防未来变更）。
- 下载/分享**统一同步**走 `POST .../transfers/{transfer_id}/share-link`（baas 无独立 `download_url`）；`expire_seconds` 60–604800，BCS 统一用 `share_link_ttl`（默认 3600）。`presign_get` 不缓存 share_url，每次重签。
- 上传字节不经 BCS：`supports_presign_put = true`，prepare 返 `Direct{...}` 客户端直传 OSS，`stream_upload` 返 `Unsupported` 不被调用。
- complete **同步**进 `DONE`，不轮询（session 场景无设备）。
- baas 错误码 → `StorageError`：`TRANSFER_NOT_FOUND`/`SOURCE_TRANSFER_NOT_FOUND`→`NotFound`；`SOURCE_TRANSFER_NOT_READY`/`TRANSFER_STATE_CONFLICT`/`TRANSFER_NOT_TERMINAL`/`OSS_OBJECT_NOT_FOUND`(409)/`INVALID_TRANSITION`(422)→`Conflict`；`NOT_IMPLEMENTED`→`Unsupported("baas")`；`INTERNAL_ERROR`/其他→`Backend`；`DELETE` 已 `DELETED` 重复调→`Ok`。
- baas crate **独立于 BCS 仓库**，仅依赖 `bcs-storage-api` trait crate；它有其自己的 Cargo.toml/workspace。
- 契约测试用 wiremock 桩 baas HTTP 协议，**不**复用 `bcs-storage-api::contract::assert_storage_plugin_conforms`（该共享用例假设 ProxyViaBcs 的 stream_upload+get_stream 语义，对 presign 后端不适用）。baas crate 写自己的 wiremock 往返用例。
- 仓库约定（AGENTS.md / src/bcs/CLAUDE.md）：不跑 cargo fmt；不引入 `T | None` 可空类型除非 `None` 是有意状态；时间戳 unix 秒 u64；UTF-8 字符串不得按字节切片。

## 参考文件（实现者必读）

- 设计文档：`docs/superpowers/specs/2026-07-20-bcs-session-workspace-design-baas-plugin.md`（权威，含 factory 改造、错误映射表、object_handle 形态、完整流程图）
- API trait 契约：`docs/superpowers/specs/2026-07-20-bcs-session-workspace-api.md` §3
- baas API 文档（v1.1）：语雀 `https://yuque.antfin.com/securitytec/otbct4/klg0lpglzmwr8t3g`
- 现有 trait 定义：`src/bcs/crates/plugin-api/bcs-storage-api/src/lib.rs`
- 现有 local 后端（镜像结构参考）：`src/bcs/crates/plugins/bcs-storage-local/`
- 现有装配：`src/bcs/crates/bootstrap/bcs/src/server.rs:159` `build_session_files_service`

## File Structure

**BCS 仓库前置改动（Phase 1）：**
- 新建 `src/bcs/crates/plugin-api/bcs-storage-api/src/factory.rs` — `StoragePluginFactory` trait + `StorageBackendConfig` + `StoragePluginError`
- 改 `src/bcs/crates/plugin-api/bcs-storage-api/src/lib.rs` — `pub mod factory;` + 导出
- 新建 `src/bcs/crates/plugins/bcs-storage-local/src/factory.rs` — `LocalStoragePluginFactory`（包现有 `LocalStoragePlugin::new`）
- 改 `src/bcs/crates/plugins/bcs-storage-local/src/lib.rs` — `pub mod factory;` + 导出
- 改 `src/bcs/crates/bootstrap/bcs/src/config.rs` `SessionFilesConfig` — 加 `share_link_ttl`、`backend: toml::Table`，`data_dir` 移入 `backend`
- 改 `src/bcs/crates/services/bcs-session-file/src/service.rs` — `SessionFileServiceConfig` 加 `share_link_ttl`；`download_route` TTL bug 修复
- 改 `src/bcs/crates/bootstrap/bcs/src/server.rs` `build_session_files_service` — 用 factory 装配、传 `share_link_ttl`、local factory 从 `backend[data_dir]` 读

**独立 baas crate（Phase 2，独立 workspace）：**
- 新 crate 目录 `bcs-storage-baas/`（独立仓库/workspace，与 BCS 仓库同级或独立）
  - `Cargo.toml`、`src/lib.rs`、`src/client.rs`（baas HTTP 客户端）、`src/plugin.rs`（`BaasStoragePlugin`）、`src/handle.rs`（backend_handle serde 类型）、`src/error.rs`（baas 错误码 → StorageError）、`src/factory.rs`（`BaasStoragePluginFactory`）
  - `src/config.rs`（`BaasConfig`：endpoint/tenant/share_link_ttl/health_probe_path/凭证/超时）
  - `tests/baas_wiremock.rs`（wiremock 桩 baas 的 UPLOAD/SINGLE/MULTIPART/share-link/delete/abort/错误 往返）

**Wiring（Phase 3）：**
- 改 `src/bcs/Cargo.toml` + BCS 仓库根 Cargo — 引入 `bcs-storage-baas` crate 依赖
- 改 `src/bcs/crates/bootstrap/bcs/src/server.rs` — factory match 加 `"baas" => Arc::new(BaasStoragePluginFactory)` arm
- 改 BCS 文档/示例配置 — baas 配置块示例

---

## Phase 1：BCS 仓库前置改造（backend-agnostic 装配 + TTL bug）

### Task 1: `bcs-storage-api` 增 factory trait + 配置容器

**Files:**
- Create: `src/bcs/crates/plugin-api/bcs-storage-api/src/factory.rs`
- Modify: `src/bcs/crates/plugin-api/bcs-storage-api/src/lib.rs`
- Test: `src/bcs/crates/plugin-api/bcs-storage-api/src/factory.rs`（内联 `#[cfg(test)]`）

**Interfaces:**
- Consumes: 现有 `StoragePlugin`（trait）、`StorageError`
- Produces: `pub trait StoragePluginFactory`、`pub struct StorageBackendConfig`、`pub enum StoragePluginError`，均从 `bcs_storage_api` 顶层导出

- [ ] **Step 1: 写 factory.rs 的失败测试（StorageBackendConfig 构造 + StoragePluginError Display）**

Create `src/bcs/crates/plugin-api/bcs-storage-api/src/factory.rs`，先只放测试与类型骨架，骨架用 `todo!()`：

```rust
//! Backend-agnostic storage plugin assembly. Each backend crate ships a
//! `StoragePluginFactory` that parses its own backend-specific config keys;
//! the composition root selects one by `storage_backend` and is otherwise
//! ignorant of the backend roster. See
//! `docs/superpowers/specs/2026-07-20-bcs-session-workspace-design-baas-plugin.md`
//! §「落地前置改造」.

use std::sync::Arc;

use async_trait::async_trait;
use serde_json::Map;

use crate::StoragePlugin;

/// Backend-agnostic assembly input: values every backend needs + an opaque
/// pass-through container for backend-specific keys (`endpoint`/`tenant` for
/// baas, `data_dir` for local, …). Factories read their own keys from
/// `backend` and self-validate.
#[derive(Debug, Clone)]
pub struct StorageBackendConfig {
    pub env: String,
    pub max_file_size: u64,
    pub multipart_threshold: u64,
    pub share_link_ttl: u64,
    pub bcs_base_url: String,
    pub bots_base_dir: String,
    /// Backend-specific keys, passed through verbatim from TOML
    /// `[session_files.backend]` (or top-level `[session_files]` leftovers).
    pub backend: Map<String, serde_json::Value>,
}

/// Why a factory failed to build its plugin. Carries a reason for BCS logs;
/// must NOT leak to clients.
#[derive(Debug, thiserror::Error)]
pub enum StoragePluginError {
    #[error("storage backend config error: {0}")]
    Build(String),
}

/// Each storage backend crate implements this: turn the backend-agnostic
/// `StorageBackendConfig` into a concrete `StoragePlugin`. `backend_name`
/// is what the composition root matches against `session_files.storage_backend`.
#[async_trait]
pub trait StoragePluginFactory: Send + Sync {
    fn backend_name(&self) -> &'static str;
    async fn build(&self, cfg: &StorageBackendConfig)
        -> Result<Arc<dyn StoragePlugin>, StoragePluginError>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn backend_config_carries_backend_map() {
        let cfg = StorageBackendConfig {
            env: "prod".into(),
            max_file_size: 1024,
            multipart_threshold: 100,
            share_link_ttl: 3600,
            bcs_base_url: "http://bcs".into(),
            bots_base_dir: "/data/bots".into(),
            backend: {
                let mut m = Map::new();
                m.insert("endpoint".into(), json!("http://baas:8080"));
                m
            },
        };
        assert_eq!(cfg.backend["endpoint"], json!("http://baas:8080"));
        assert_eq!(cfg.share_link_ttl, 3600);
    }

    #[test]
    fn plugin_error_build_message() {
        let e = StoragePluginError::Build("missing endpoint".into());
        assert_eq!(e.to_string(), "storage backend config error: missing endpoint");
    }
}
```

- [ ] **Step 2: 在 lib.rs 导出 factory 模块**

Modify `src/bcs/crates/plugin-api/bcs-storage-api/src/lib.rs`，在 `pub mod contract;` 与 `pub mod fake;` 旁加：

```rust
pub mod factory;
```

并在文件末尾（`byte_stream_from_bytes` 之后）不需要其他改动（类型走 `factory::` 路径引用）。

- [ ] **Step 3: 编译 + 跑测试**

Run: `cargo test -p bcs-storage-api`
Expected: `factory::tests::backend_config_carries_backend_map` 与 `factory::tests::plugin_error_build_message` PASS。

- [ ] **Step 4: Commit**

```bash
git add src/bcs/crates/plugin-api/bcs-storage-api/src/factory.rs src/bcs/crates/plugin-api/bcs-storage-api/src/lib.rs
git commit -m "feat(bcs-storage-api): add StoragePluginFactory + StorageBackendConfig"
```

---

### Task 2: `bcs-storage-local` 加 `LocalStoragePluginFactory`

**Files:**
- Create: `src/bcs/crates/plugins/bcs-storage-local/src/factory.rs`
- Modify: `src/bcs/crates/plugins/bcs-storage-local/src/lib.rs`、`Cargo.toml`

**Interfaces:**
- Consumes:`StoragePluginFactory`、`StorageBackendConfig`、`StoragePluginError`（from `bcs-storage-api`）；现有 `LocalStoragePlugin::new`、`LocalStorageConfig`
- Produces: `LocalStoragePluginFactory`（`backend_name() == "local"`），`build` 从 `cfg.backend["data_dir"]` 读路径，缺省回退 `{bots_base_dir}/session-files`；`max_object_size` 取 `cfg.max_file_size`

- [ ] **Step 1: 写失败测试**

Create `src/bcs/crates/plugins/bcs-storage-local/src/factory.rs`：

```rust
//! `StoragePluginFactory` for the local filesystem backend. Wraps the existing
//! `LocalStoragePlugin::new`; reads `data_dir` from `StorageBackendConfig.backend`
//! (falling back to `{bots_base_dir}/session-files`).

use std::sync::Arc;

use async_trait::async_trait;
use serde_json::Value;

use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginError, StoragePluginFactory};
use bcs_storage_api::StoragePlugin;

use crate::{LocalStorageConfig, LocalStoragePlugin};

pub struct LocalStoragePluginFactory;

fn data_dir(cfg: &StorageBackendConfig) -> std::path::PathBuf {
    match cfg.backend.get("data_dir") {
        Some(Value::String(s)) if !s.is_empty() => std::path::PathBuf::from(s),
        _ => std::path::PathBuf::from(format!("{}/session-files", cfg.bots_base_dir)),
    }
}

#[async_trait]
impl StoragePluginFactory for LocalStoragePluginFactory {
    fn backend_name(&self) -> &'static str { "local" }

    async fn build(&self, cfg: &StorageBackendConfig)
        -> Result<Arc<dyn StoragePlugin>, StoragePluginError>
    {
        let data_dir = data_dir(cfg);
        // async-safe dir creation (matches LocalStoragePlugin internals which use tokio::fs).
        tokio::fs::create_dir_all(&data_dir)
            .await
            .map_err(|e| StoragePluginError::Build(format!("create data_dir {}: {e}", data_dir.display())))?;
        Ok(Arc::new(LocalStoragePlugin::new(LocalStorageConfig {
            data_dir,
            max_object_size: cfg.max_file_size,
        })))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Map};

    fn cfg(backend: Map<String, Value>) -> StorageBackendConfig {
        StorageBackendConfig {
            env: "test".into(), max_file_size: 1024, multipart_threshold: 100,
            share_link_ttl: 3600, bcs_base_url: "http://bcs".into(),
            bots_base_dir: tempfile::tempdir().unwrap().keep().to_string_lossy().into_owned(),
            backend,
        }
    }

    #[tokio::test]
    async fn builds_from_data_dir_key() {
        let dir = tempfile::tempdir().unwrap();
        let mut m = Map::new();
        m.insert("data_dir".into(), json!(dir.path().to_string_lossy().to_string()));
        let p = LocalStoragePluginFactory.build(&cfg(m)).await.unwrap();
        assert_eq!(p.capabilities().supports_presign_put, false);
    }

    #[tokio::test]
    async fn falls_back_to_bots_base_dir_session_files() {
        let p = LocalStoragePluginFactory.build(&cfg(Map::new())).await.unwrap();
        assert_eq!(p.backend_name(), "local");
        // data_dir 前缀应为 {bots_base_dir}/session-files
        assert!(p.health_check().await.unwrap().ok);
    }
}
```

在 `src/bcs/crates/plugins/bcs-storage-local/src/lib.rs` 顶部（`pub use bcs_storage_api::...` 区域）加：

```rust
pub mod factory;
pub use factory::LocalStoragePluginFactory;
```

`Cargo.toml` 加 dev-dependency（factory 测试用 tempfile）—— 检查 `[dev-dependencies]` 是否已含 `tempfile`；如已有则跳过：

```toml
[dev-dependencies]
tempfile = { workspace = true }
serde_json = { workspace = true }
```

- [ ] **Step 2: 编译 + 跑测试**

Run: `cargo test -p bcs-storage-local`
Expected: `factory::tests::*` PASS（含回退用例）。

- [ ] **Step 3: Commit**

```bash
git add src/bcs/crates/plugins/bcs-storage-local/src/factory.rs src/bcs/crates/plugins/bcs-storage-local/src/lib.rs src/bcs/crates/plugins/bcs-storage-local/Cargo.toml
git commit -m "feat(bcs-storage-local): add LocalStoragePluginFactory"
```

---

### Task 3: `SessionFilesConfig` 加 `share_link_ttl` + `backend` 透传表（config.rs）

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/config.rs`（`SessionFilesConfig` + 默认值函数 + 既有 `data_dir` 处理）
- Test: `src/bcs/crates/bootstrap/bcs/src/config.rs` 既有测试模块 / 新增内联

**Interfaces:**
- Consumes: —
- Produces: `SessionFilesConfig { storage_backend, multipart_threshold, max_file_size, share_link_ttl, share, backend: toml::Table }`；`data_dir` 从具名字段移除（改由 local factory 从 `backend["data_dir"]` 读，Task 2 已支持）
- **重要**：`data_dir` 具名字段移除是 breaking config 变更，但 `data_dir` 此功能刚上线无生产数据，无迁移成本。旧 TOML 顶层 `[session_files] data_dir = "..."` 会被 `backend` table 之外的未知键捕获吗？—— 不会，`#[serde(deny_unknown_fields)]` 未设，但具名字段移除后 `data_dir` 不再被解析。需让 server.rs 在装配时把顶层遗留的 `data_dir` 兜底塞进 `backend`，或直接要求配置迁移。**本 plan 选择：config 不再认识 `data_dir` 具名字段；server.rs 不做遗留迁移（功能未上线）。如需兼容，由部署侧把 `data_dir` 改写到 `[session_files.backend]` 下。**

- [ ] **Step 1: 读现状 SessionFilesConfig + 默认值函数，确认改动锚点**

Run: `sed -n '/pub struct SessionFilesConfig/,/^}/p' src/bcs/crates/bootstrap/bcs/src/config.rs`
确认现有字段：`storage_backend`、`multipart_threshold`、`max_file_size`、`data_dir: Option<String>`、`share: SessionFilesShareConfig`。

- [ ] **Step 2: 写失败测试（解析含 share_link_ttl + backend table 的 TOML）**

在 `config.rs` 的 `#[cfg(test)] mod tests` 里新增（若该模块不存在则新建 `#[cfg(test)] mod tests` 在文件尾）：

```rust
    #[test]
    fn session_files_config_parses_share_link_ttl_and_backend() {
        let toml_str = r#"
storage_backend = "baas"
multipart_threshold = 104857600
max_file_size = 5368709120
share_link_ttl = 3600

[share]
token_secret = "s3cret"
default_ttl_seconds = 86400

[backend]
endpoint = "http://baas:8080"
tenant = "teamclaw"
"#;
        let cfg: SessionFilesConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(cfg.storage_backend, "baas");
        assert_eq!(cfg.share_link_ttl, 3600);
        assert_eq!(cfg.backend["endpoint"], toml::Value::String("http://baas:8080".into()));
        assert_eq!(cfg.backend["tenant"], toml::Value::String("teamclaw".into()));
    }
```

（若 `toml` 不在 config 的 `[dev-dependencies]`，加 `toml = { workspace = true }`。）

- [ ] **Step 3: 跑测试确认失败**

Run: `cargo test -p bcs session_files_config_parses_share_link_ttl_and_backend 2>&1 | tail -5`
Expected: 编译错误 `no field share_link_ttl / backend`。

- [ ] **Step 4: 改 SessionFilesConfig 定义**

把 `SessionFilesConfig` 改为：

```rust
pub struct SessionFilesConfig {
    #[serde(default = "default_session_files_storage_backend")]
    pub storage_backend: String,
    #[serde(default = "default_session_files_multipart_threshold")]
    pub multipart_threshold: u64,
    #[serde(default = "default_session_files_max_file_size")]
    pub max_file_size: u64,
    /// In-session + share download share-link TTL (baas expire_seconds), seconds.
    #[serde(default = "default_session_files_share_link_ttl")]
    pub share_link_ttl: u64,
    #[serde(default)]
    pub share: SessionFilesShareConfig,
    /// Backend-specific config pass-through (local: data_dir; baas: endpoint/tenant/...).
    #[serde(default)]
    pub backend: toml::Table,
}

fn default_session_files_share_link_ttl() -> u64 { 3600 }
```

**删除** 原 `pub data_dir: Option<String>` 字段及其文档注释。

- [ ] **Step 5: 跑测试确认通过 + 全量编译确认无遗漏引用**

Run: `cargo test -p bcs session_files_config_parses_share_link_ttl_and_backend`
Expected: PASS。
Run: `cargo build -p bcs 2>&1 | tail -20`
Expected: 若报 `config.session_files.data_dir` 引用错误（在 server.rs:173-178），记下 —— 这是 Task 4 要改的装配处，**本 task 不处理**。本 task 只确保 config.rs 自身编译 + 测试过。

如果因 server.rs 引用 `data_dir` 导致 `cargo build -p bcs` 失败，临时在 server.rs 那处加 `let _ = &config.session_files.backend;` 占位以通过编译，Task 4 再正式改装配。占位代码：

```rust
    // TEMP placeholder — replaced in Task 4
    let data_dir: std::path::PathBuf =
        config.session_files.backend.get("data_dir")
            .and_then(|v| v.as_str().map(String::from))
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| format!("{}/session-files", config.bots_base_dir.display()).into());
    let _ = std::fs::create_dir_all(&data_dir);
    let storage: Arc<dyn StoragePlugin> = Arc::new(LocalStoragePlugin::new(LocalStorageConfig {
        data_dir,
        max_object_size: config.session_files.max_file_size,
    }));
```

（保留旧装配以过编译；Task 4 整体替换为 factory。）

- [ ] **Step 6: Commit**

```bash
git add src/bcs/crates/bootstrap/bcs/src/config.rs src/bcs/crates/bootstrap/bcs/src/server.rs
git commit -m "feat(bcs-config): SessionFilesConfig gains share_link_ttl + backend pass-through"
```

---

### Task 4: `SessionFileServiceConfig` 加 `share_link_ttl` + 修 `download_route` TTL bug

**Files:**
- Modify: `src/bcs/crates/services/bcs-session-file/src/service.rs`
- Test: 现有 `download_route_*` 测试 + 新增一个断言 `share_link_ttl` 被用的测试

**Interfaces:**
- Consumes: —
- Produces: `SessionFileServiceConfig` 新增 `pub share_link_ttl: u64` 字段；`download_route` 不再用 `unwrap_or(300)`，改用 `self.cfg.share_link_ttl`
- **当前 bug**：`download_route`（service.rs:545）`ttl_secs.unwrap_or(300)` 会让 baas presign URL 5 分钟过期；本 task 修复为 `unwrap_or(self.cfg.share_link_ttl)`（默认 3600）

- [ ] **Step 1: 读现状 download_route + SessionFileServiceConfig + build_svc 测试 helper**

Run: `sed -n '520,557p' src/bcs/crates/services/bcs-session-file/src/service.rs`（download_route）
Run: `grep -n 'share_default_ttl\|share_link_ttl\|SessionFileServiceConfig {' src/bcs/crates/services/bcs-session-file/src/service.rs`
确认所有 `SessionFileServiceConfig { ... }` 构造点（含 build_svc 测试 helper 与 server.rs）—— 都要加 `share_link_ttl` 字段。

- [ ] **Step 2: 写失败测试（download_route 用 share_link_ttl 而非 300）**

在 service.rs 测试模块新增（`build_svc` helper 旁，需先在 Step 4 给 helper 加字段；此处先写测试主体）：

```rust
    #[tokio::test]
    async fn download_route_uses_share_link_ttl_when_no_query_ttl() {
        // presign-capable backend so download_route goes through presign_get.
        let (s, storage, repo) = build_svc(presign_caps());
        // share_link_ttl in build_svc is set to 7777 (asserted below via FakeStoragePlugin recording ttl).
        let r1 = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r1.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r1.file.file_id).await.unwrap();

        // download_route(..., None) should pass share_link_ttl (7777) to presign_get.
        let (_row, route) = s.download_route("g1:abcd1234", &r1.file.file_id, None).await.unwrap();
        let ticket = route.presign.expect("presign backend returns a ticket");
        // FakeStoragePlugin.presign_get encodes ttl in ticket.download_url as fake://<ttl>.
        assert!(ticket.download_url.contains("7777"),
            "expected share_link_ttl(7777) propagated to presign_get, got {}", ticket.download_url);
    }
```

> **实现者注**：`FakeStoragePlugin::presign_get` 现状返回 `PresignGetTicket { download_url: format!("fake://{}", handle.key), expires_at: ttl_secs }`。本测试需断言传给 presign_get 的 ttl。两种做法：(a) 改 `FakeStoragePlugin` 把 ttl 编进 download_url（侵入 trait crate）；(b) 断言 `ticket.expires_at == 7777`（更干净，FakeStoragePlugin 已把 ttl 赋给 expires_at）。**用 (b)**，把测试断言改为 `assert_eq!(ticket.expires_at, 7777)`，删 download_url 断言。先读 `fake.rs` 确认：

Run: `sed -n '/async fn presign_get/,/}/p' src/bcs/crates/plugin-api/bcs-storage-api/src/fake.rs`
确认 `presign_get` 返回 `expires_at: ttl_secs`。若确认，测试断言用 `assert_eq!(ticket.expires_at, 7777);`。

- [ ] **Step 3: 跑测试确认失败**

Run: `cargo test -p bcs-session-file download_route_uses_share_link_ttl_when_no_query_ttl`
Expected: 编译失败（`SessionFileServiceConfig` 无 `share_link_ttl` 字段）或断言失败（expires_at==300）。

- [ ] **Step 4: 改 SessionFileServiceConfig + download_route + build_svc helper**

(a) `SessionFileServiceConfig` 加字段：

```rust
pub struct SessionFileServiceConfig {
    // ... 现有字段 ...
    pub share_link_ttl: u64,   // 新增：in-session + share download share-link TTL
}
```

(b) `download_route`（原 `let ttl = ttl_secs.unwrap_or(300);`）改为：

```rust
            let ttl = ttl_secs.unwrap_or(self.cfg.share_link_ttl);
```

(c) 所有 `SessionFileServiceConfig { ... }` 构造点加 `share_link_ttl:`：
- `build_svc` 测试 helper（service.rs）：`share_link_ttl: 7777,`
- server.rs `build_session_files_service`：`share_link_ttl: config.session_files.share_link_ttl,`（Task 1/3 已让 config 有此字段）
- 其余构造点（grep 出来的）全部补 `share_link_ttl: 3600,`（或合理默认）—— 先 grep 列全：

Run: `grep -rn "SessionFileServiceConfig {" src/bcs/crates/ ` —— 对每处补字段。

- [ ] **Step 5: 跑测试确认通过**

Run: `cargo test -p bcs-session-file`
Expected: 全绿，含 `download_route_uses_share_link_ttl_when_no_query_ttl`（expires_at==7777）。

- [ ] **Step 6: 全量编译确认**

Run: `cargo build -p bcs`
Expected: 干净（server.rs 因 Task 3 占位仍用旧 LocalStoragePlugin，Task 5 改 factory；本 task 不动 server.rs 装配）。

- [ ] **Step 7: Commit**

```bash
git add src/bcs/crates/services/bcs-session-file/src/service.rs src/bcs/crates/bootstrap/bcs/src/server.rs
git commit -m "fix(bcs-session-file): download_route uses share_link_ttl (default 3600) not 300"
```

---

### Task 4.1: 分享下载路径忽略 `?ttl`，统一用 share_link_ttl

**🔴 P2-E**。`download_route` 默认 TTL 已修（Task 4），但 **分享下载** 现状 `shared_file_content`(bcs-http) 把 `q.ttl` 透传 → `download_file_by_id` → `download_route(..., q.ttl)`。若前端传 `?ttl=10`，分享下载的 OSS `share_url` 就 10 秒过期，与 baas spec/design「`?ttl` 对前端隐藏、统一 share_link_ttl」的意图冲突。本 task 让分享下载路径 **不传 q.ttl**，固定用 `share_link_ttl`。

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/session_files.rs`（`shared_file_content`）
- Test: `src/bcs/crates/adapters/http/bcs-http/src/routes/session_files.rs` 测试模块

**Interfaces:**
- Consumes:`download_file_by_id(&state, sid, fid, ttl)`（现有 helper，会话内路径仍透传 q.ttl——会话内 q.ttl 是否仍生效？设计对前端隐藏所有 ?ttl，但**会话内** q.ttl 接受但忽略 vs **分享** q.ttl 接受但忽略是两件事。**本 plan 统一**：两条 download 路径都忽略 q.ttl、统一 share_link_ttl。但 Task 4 改的是 `download_route` 默认（None → share_link_ttl）；若传入 Some(ttl) 仍会覆盖。故需让两 handler **不传** q.ttl（传 None），q.ttl 字段保留为「接受但忽略」兼容）
- Produces:`shared_file_content` 与 `download_content` 调 `download_file_by_id(&state, sid, fid, None)`（传 None，忽略 q.ttl）；`DownloadQuery.ttl` 字段保留解析（兼容旧请求，不报错）但值被丢弃

- [ ] **Step 1: 读 shared_file_content + download_content 现状**

Run: `grep -n "pub async fn shared_file_content\|pub async fn download_content\|download_file_by_id" src/bcs/crates/adapters/http/bcs-http/src/routes/session_files.rs`

确认两 handler 都把 `q.ttl` 透传给 `download_file_by_id`。

- [ ] **Step 2: 写失败测试（分享下载忽略 q.ttl，传 None）**

HTTP 层测试较重（需起 router）。**简化**：本 task 不写独立 HTTP 测试，而是改 `download_file_by_id` 让其 ttl 参数语义清晰 —— 自动化验证靠现有 `shared_file_content_*` HTTP 测试仍绿（断言行为不变 + 302 正常）。若要断言"q.ttl 被忽略"，需注入 fake storage 记录传入 ttl —— 与 Task 4 的 service 测试方式一致。**本 plan 选轻量**：改代码 + 既有 HTTP 测试不回归即视为通过；q.ttl 忽略的正确性由 Task 4 的 service 层 `download_route(..., None) → share_link_ttl` 测试间接覆盖（handler 传 None 时 service 用 share_link_ttl）。

故 Step 2 跳过新测试，直接改代码（Step 3），以既有测试无回归验证。

- [ ] **Step 3: 改两个 download handler 传 None**

`shared_file_content`：

```rust
pub async fn shared_file_content(
    State(state): State<HttpAppState>,
    Query(q): Query<DownloadQuery>,
) -> Response {
    let Some(token) = q.token else {
        return unauthorized();
    };
    match state.services.session_files.share_consume(&token).await {
        Ok(r) => {
            let sid_owned = r.file.session_id.clone();
            let fid = r.file.file_id.clone();
            // TTL hidden from the frontend: always None → download_route uses
            // share_link_ttl (统一 3600). q.ttl is accepted-but-ignored (P2-E).
            download_file_by_id(&state, &sid_owned, &fid, None).await
        }
        Err(_) => share_consume_err_to_response(),
    }
}
```

`download_content`（会话内，同样忽略 q.ttl）：

```rust
pub async fn download_content(
    State(state): State<HttpAppState>,
    Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    Query(_q): Query<DownloadQuery>,   // q.ttl accepted-but-ignored
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return forbidden_not_participant();
    }
    download_file_by_id(&state, &sid, &file_id, None).await
}
```

- [ ] **Step 4: 跑 bcs-http 测试 + 全量编译**

Run: `cargo build -p bcs-http && cargo test -p bcs-http`
Expected: 全绿（既有 shared-file-content / download-content HTTP 测试仍过；它们不依赖 q.ttl）。若某既有测试显式传 `?ttl=` 并断言行为——核对该断言是否仍成立（应仍 302/200 不变）。

- [ ] **Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-http/src/routes/session_files.rs
git commit -m "fix(bcs-http): download/share content ignores ?ttl (hidden from frontend, uses share_link_ttl)"
```

---

### Task 5: `server.rs` 用 factory 装配 storage（对后端清单无感）

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs` `build_session_files_service`
- Test: 既有 `bcs --lib` bootstrap/wiring 测试 + server tests

**Interfaces:**
- Consumes:`StoragePluginFactory`、`StorageBackendConfig`（Task 1）、`LocalStoragePluginFactory`（Task 2）、`SessionFilesConfig.backend`（Task 3）、`SessionFileServiceConfig.share_link_ttl`（Task 4）
- Produces: `build_session_files_service` 按 `storage_backend` 选 factory、`build()` 得 plugin，注入 service；`data_dir` 不再在 server.rs 显式读

- [ ] **Step 1: 读 build_session_files_service 全貌**

Run: `sed -n '159,230p' src/bcs/crates/bootstrap/bcs/src/server.rs`
确认：`data_dir` 解析（173-180）、`storage` Arc 构造（182-185）、`file_repo` 分支（187-193）、`bcs_base_url`（211-214）、`SessionFileServiceConfig { ... }` 构造（216+）。

- [ ] **Step 2: 改 build_session_files_service 的 storage 装配段**

把原 `data_dir` 解析 + `Arc::new(LocalStoragePlugin::new(...))` 整段（173-185）替换为 factory 装配。引入：

```rust
    use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginFactory};
    use bcs_storage_local::LocalStoragePluginFactory;
```

替换装配段：

```rust
    // Backend-agnostic storage assembly: select a factory by storage_backend,
    // build the plugin from the backend pass-through table. server.rs is
    // otherwise ignorant of the backend roster (adding OSS/NAS later is one
    // factory arm here + its crate). See design-baas-plugin §「落地前置改造」.
    let factory: Arc<dyn StoragePluginFactory> = match config.session_files.storage_backend.as_str() {
        "local" => Arc::new(LocalStoragePluginFactory),
        other => {
            return Arc::new(dummy_baas_not_wired_yet());  // baas wired in Phase 3 (Task 11)
        }
    };
```

> **实现者注**：本 task 时 baas crate 尚未引入，`"baas"` arm 用一个明确的 "not wired" 处理（启动期 panic 或返 error），避免误装配。Phase 3 Task 11 把它换成真 `BaasStoragePluginFactory`。具体：用 `panic!("storage_backend='{}' requires crate bcs-storage-baas, not yet linked in this build", other)`（在构建期/启动期失败而非静默）。但 `build_session_files_service` 返回 `Arc<dyn ...>` 不能直接 panic 返回——用 `std::process::abort` 或把函数签名加 Result。**最简做法**：保持签名，对未知 backend panic：

```rust
        other => panic!(
            "storage_backend='{}': require crate bcs-storage-baas (not linked in this build) \
             or a known backend; aborting bootstrap",
            other
        ),
```

把这条 `other => panic!(...)` 写进去（Task 11 替换为 `"baas" => ...` arm）。

然后构造 `StorageBackendConfig` 并 build：

```rust
    let backend_cfg = StorageBackendConfig {
        env: env.clone(),
        max_file_size: config.session_files.max_file_size,
        multipart_threshold: config.session_files.multipart_threshold,
        share_link_ttl: config.session_files.share_link_ttl,
        bcs_base_url: bcs_base_url.clone(),   // bcs_base_url 在下方 211-214 定义；需把其上移到此处之前
        bots_base_dir: config.bots_base_dir.display().to_string(),
        backend: toml_table_to_json_map(&config.session_files.backend),
    };
    let storage: Arc<dyn StoragePlugin> = factory.build(&backend_cfg).await
        .expect("storage backend build failed at bootstrap");
```

注 `bcs_base_url` 原在 211-214 定义，需**上移**到 factory 装配之前。`toml_table_to_json_map` 是个本文件内 helper（新增）：

```rust
/// Convert a `toml::Table` (config pass-through) into a `serde_json::Map`.
fn toml_table_to_json_map(table: &toml::Table) -> serde_json::Map<String, serde_json::Value> {
    let mut out = serde_json::Map::new();
    for (k, v) in table {
        out.insert(k.clone(), toml_value_to_json(v));
    }
    out
}

fn toml_value_to_json(v: &toml::Value) -> serde_json::Value {
    match v {
        toml::Value::String(s) => serde_json::Value::String(s.clone()),
        toml::Value::Integer(i) => serde_json::Value::Number((*i).into()),
        toml::Value::Float(f) => serde_json::json!(f),
        toml::Value::Boolean(b) => serde_json::Value::Bool(*b),
        toml::Value::Table(t) => serde_json::Value::Object(toml_table_to_json_map(t)),
        toml::Value::Array(a) => serde_json::Value::Array(a.iter().map(toml_value_to_json).collect()),
        toml::Value::Datetime(d) => serde_json::Value::String(d.to_string()),
    }
}
```

**删除** 原 `use bcs_storage_local::{LocalStorageConfig, LocalStoragePlugin};`（改用 factory）；保留 `use bcs_storage_api::StoragePlugin;`。
**删除** Task 3 的临时占位（若有）。

- [ ] **Step 3: 确保 SessionFileServiceConfig 构造补了 share_link_ttl**

确认 216+ 的 `SessionFileServiceConfig { ... }` 含 `share_link_ttl: config.session_files.share_link_ttl,`（Task 4 已做，此处核对）。

- [ ] **Step 4: 跑 bcs lib 测试 + 全量编译**

Run: `cargo build -p bcs`
Expected: 干净。
Run: `cargo test -p bcs --lib`
Expected: 全绿（含现有 migration/wiring 测试）。注意测试里若有构造 `BcsConfig` 默认 + `storage_backend="local"` 的用例，会走 `LocalStoragePluginFactory`（从 `backend` table 读 data_dir 或回退 bots_base_dir）—— 确认测试 fixture 的 `bots_base_dir` 可写。

若 server tests 因 `data_dir` 具名字段移除而失败（fixture 仍设旧 `data_dir`），改 fixture 把 `data_dir` 放 `[session_files.backend] data_dir` 下或依赖回退。

- [ ] **Step 5: Commit**

```bash
git add src/bcs/crates/bootstrap/bcs/src/server.rs
git commit -m "refactor(bcs-bootstrap): assemble storage via StoragePluginFactory (backend-agnostic)"
```

**Phase 1 完成检查点**：`cargo build --workspace` 干净；`cargo test -p bcs-storage-api -p bcs-storage-local -p bcs-session-file -p bcs --lib` 全绿；config 支持 `[session_files] share_link_ttl` + `[session_files.backend]`；local 后端经 factory 装配保持行为。

---

## Phase 2：独立 baas crate 实现

> **crate 位置**：独立 Cargo workspace（不在 `src/bcs/crates/`）。建议放在 BCS 仓库同级或独立仓库；本 plan 以 `bcs-storage-baas/` 为 crate 根（其自身 `Cargo.toml` + 可有独立 Cargo.lock）。依赖 `bcs-storage-api`（path 或 git 依赖——本 plan 用 path 依赖指向 `../Avernet/src/bcs/crates/plugin-api/bcs-storage-api`，落地时按实际发布方式调整）。
>
> **测试驱动**：baas crate 是 `reqwest` → baas HTTP 的适配层，核心正确性靠 wiremock 桩 baas 协议验证。每个 trait 方法一个往返测试 + 错误映射表驱动测试。

### Task 6: baas crate 骨架（Cargo.toml + lib.rs + BaasConfig + health_check）

**Files:**
- Create: `bcs-storage-baas/Cargo.toml`
- Create: `bcs-storage-baas/src/lib.rs`
- Create: `bcs-storage-baas/src/config.rs`
- Create: `bcs-storage-baas/src/error.rs`
- Test: `bcs-storage-baas/src/lib.rs`（health_check wiremock）

**Interfaces:**
- Consumes:`StoragePlugin`、`StorageError`、`StorageHandle`、`UploadHandle` 等（from `bcs-storage-api`）；reqwest
- Produces: `BaasStoragePlugin`（实现 `StoragePlugin`，本 task 只实现 `backend_name`/`capabilities`/`health_check`）、`BaasConfig`、`BaasStoragePluginFactory`

- [ ] **Step 1: Cargo.toml**

Create `bcs-storage-baas/Cargo.toml`：

```toml
[package]
name        = "bcs-storage-baas"
version     = "0.1.0"
edition     = "2021"
license     = "MIT"
publish     = false

[dependencies]
async-trait    = "0.1"
anyhow         = "1"
bcs-storage-api = { path = "../Avernet/src/bcs/crates/plugin-api/bcs-storage-api" }  # 调整为实际相对路径
reqwest        = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }
serde          = { version = "1", features = ["derive"] }
serde_json     = "1"
thiserror      = "2"
tokio          = { version = "1", features = ["rt-multi-thread"] }
tracing        = "0.1"
percent-encoding = "2"

[dev-dependencies]
tokio    = { version = "1", features = ["macros", "rt-multi-thread"] }
wiremock = "0.6"
```

- [ ] **Step 2: BaasConfig + 默认值**

Create `bcs-storage-baas/src/config.rs`：

```rust
//! baas plugin config. Originates from `StorageBackendConfig.backend` keys:
//! endpoint (host only), tenant, share_link_ttl, health_probe_path, auth
//! header bearers, timeouts. The factory parses & validates these.

use std::time::Duration;

#[derive(Debug, Clone)]
pub struct BaasConfig {
    /// baas host only, e.g. "http://baas.xxx:8080" (no API path).
    pub endpoint: String,
    /// Tenant segment in baas session path.
    pub tenant: String,
    /// Seconds for share-link expire_seconds (in-session + share download).
    pub share_link_ttl: u64,
    /// Optional health path relative to endpoint ("", "/health"...).
    pub health_probe_path: String,
    /// Auth header(s) to attach to every baas request (plugin-held, never leaked to clients).
    pub auth_headers: Vec<(String, String)>,
    pub http_timeout: Duration,
}

impl Default for BaasConfig {
    fn default() -> Self {
        Self {
            endpoint: String::new(),
            tenant: String::new(),
            share_link_ttl: 3600,
            health_probe_path: String::new(),
            auth_headers: Vec::new(),
            http_timeout: Duration::from_secs(30),
        }
    }
}
```

- [ ] **Step 3: 错误映射 module**

Create `bcs-storage-baas/src/error.rs`：

```rust
//! baas v1.1 error code -> `StorageError`. baas error body:
//! `{"detail":{"error":"<CODE>","message":"...", ...optional}}`.

use bcs_storage_api::StorageError;

/// Map a baas error code (from response `detail.error`) + the HTTP status
/// to a `StorageError`. See design-baas-plugin §「错误映射」.
pub fn map_baas_error(code: &str, status: u16, detail_msg: &str) -> StorageError {
    match code {
        "TRANSFER_NOT_FOUND" | "SOURCE_TRANSFER_NOT_FOUND" => StorageError::NotFound,
        "SOURCE_TRANSFER_NOT_READY"
        | "TRANSFER_STATE_CONFLICT"
        | "TRANSFER_NOT_TERMINAL"
        | "OSS_OBJECT_NOT_FOUND"
        | "INVALID_TRANSITION" => StorageError::Conflict(format!("{code}: {detail_msg}")),
        "NOT_IMPLEMENTED" => StorageError::Unsupported("baas"),
        _ => StorageError::Backend(anyhow::anyhow!(
            "baas error {status} {code}: {detail_msg}"
        )),
    }
}

/// Delete (DELETE .../transfers/{transfer_id}) idempotent success: a 409
/// TRANSFER_NOT_TERMINAL is a real conflict; a 404 / already-DELETED is Ok.
pub fn is_delete_idempotent_ok(code: Option<&str>) -> bool {
    matches!(code, Some("TRANSFER_NOT_FOUND")) // transfer gone already
}
```

- [ ] **Step 4: lib.rs 骨架 + BaasStoragePlugin（仅 backend_name/capabilities/health_check）**

Create `bcs-storage-baas/src/lib.rs`：

```rust
//! `bcs-storage-baas`: `StoragePlugin` impl for the baas Session File Sharing
//! API v1.1. Uploads bypass BCS (client PUTs to OSS direct URLs), complete is
//! sync-to-DONE, downloads go through sync `POST /share-link`, delete is by
//! transfer_id. See design-baas-plugin spec.

pub mod config;
pub mod error;

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_storage_api::{
    ByteStream, PresignGetTicket, PreparedUpload, StorageCapabilities, StorageError, StorageHandle,
    StorageHealth, StorageObjectMeta, StoragePlugin, UploadHandle, UploadPrepareRequest,
};

use crate::config::BaasConfig;

pub struct BaasStoragePlugin {
    cfg: BaasConfig,
    caps: StorageCapabilities,
    http: reqwest::Client,
}

impl BaasStoragePlugin {
    pub fn new(cfg: BaasConfig, max_object_size: u64) -> Self {
        let caps = StorageCapabilities {
            supports_presign_put: true,
            supports_presign_download: true,
            supports_stream_put: true,
            supports_stream_get: true,
            max_object_size,
        };
        let mut builder = reqwest::Client::builder().timeout(cfg.http_timeout);
        // Attach auth headers via a default-headers middleware substitute: store on cfg, applied per-request in client layer.
        let http = builder.build().expect("reqwest client build");
        Self { cfg, caps, http }
    }

    /// Build the baas base path: {endpoint}/api/v1/sessions/{tenant}/{session_id percent-encoded}/files.
    /// transfer_id and session_id are percent-encoded to be safe path segments.
    fn base_for_session(&self, session_id: &str) -> String {
        let sid = percent_encode_path(session_id);
        format!(
            "{}/api/v1/sessions/{}/{}",
            self.cfg.endpoint.trim_end_matches('/'),
            percent_encode_path(&self.cfg.tenant),
            sid
        )
    }

    fn auth(&self, req: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        let mut r = req;
        for (k, v) in &self.cfg.auth_headers {
            r = r.header(k, v);
        }
        r
    }
}

/// percent-encode a single path segment (session_id / tenant / transfer_id).
/// baas session_id may contain ':' (e.g. bcs_grp_<uuid>:<hex>) which is not
/// path-segment-safe — must encode ':' (and `/` etc.). `_`/`-` are safe and
/// kept verbatim (NON_ALPHANUMERIC would encode `_`→`%5F`, breaking the
/// readable `bcs_grp_...` form — so use a block-set that encodes only
/// separators/unreserved-unsafe chars, NOT a permissive allow-set).
///
/// Encoding set: start from RFC 3986 path-segment chars and **add `:` to the
/// encode-set** (a path sub-delim `:` is legal per RFC but we choose to encode
/// it so baas routers never treat it as a delimiter). Concretely use
/// `percent_encoding::AsciiSet` extended from `NON_ALPHANUMERIC`'s complement:
/// encode `:` `/` `?` `#` `[` `]` `@` `!` `$` `&` `'` `(` `)` `*` `+` `,` `;`
/// `=` `%` and space; **keep** `A-Za-z0-9 - _ . ~`.
fn percent_encode_path(s: &str) -> String {
    use percent_encoding::{utf8_percent_encode, AsciiSet, CONTROLS};
    // 0x3A = ':', 0x2F = '/', 0x3F = '?', 0x23 = '#', 0x5B/5D = '[',']', 0x40 = '@',
    // sub-delims: 0x21 '!', 0x24 '$', 0x26 '&', 0x27 ''', 0x28/29 '(',')', 0x2A '*',
    // 0x2B '+', 0x2C ',', 0x3B ';', 0x3D '=', 0x25 '%', 0x20 space.
    const ENCODE_SET: &AsciiSet = &CONTROLS
        .add(b':').add(b'/').add(b'?').add(b'#').add(b'[').add(b']').add(b'@')
        .add(b'!').add(b'$').add(b'&').add(b'\'').add(b'(').add(b')').add(b'*')
        .add(b'+').add(b',').add(b';').add(b'=').add(b'%').add(b' ');
    utf8_percent_encode(s, ENCODE_SET).to_string()
}

#[cfg(test)]
mod tests {
    use super::percent_encode_path;

    #[test]
    fn colon_encoded_underscore_kept() {
        // session_id 形如 bcs_grp_abc:cdf28232：':' 编码、'_' 保留（可读 bcs_grp_...）。
        assert_eq!(percent_encode_path("bcs_grp_abc:cdf28232"), "bcs_grp_abc%3Acdf28232");
    }
    #[test]
    fn slash_encoded() {
        // session_id 理论上不应含 '/'，但若含则编码（防路径穿越/段断裂）。
        assert_eq!(percent_encode_path("a/b"), "a%2Fb");
    }
    #[test]
    fn alphanumeric_dash_tilde_kept() {
        assert_eq!(percent_encode_path("A1-_~"), "A1-_~");
    }
}

#[async_trait]
impl StoragePlugin for BaasStoragePlugin {
    fn backend_name(&self) -> &'static str { "baas" }

    fn capabilities(&self) -> StorageCapabilities { self.caps }

    async fn prepare_upload(&self, _req: UploadPrepareRequest) -> Result<PreparedUpload, StorageError> {
        unimplemented!("Task 7")
    }
    async fn stream_upload(&self, _h: &UploadHandle, _p: Option<u16>, _b: ByteStream) -> Result<(), StorageError> {
        Err(StorageError::Unsupported("baas")) // presign_put backend: never called by BCS
    }
    async fn complete_upload(&self, _h: &UploadHandle) -> Result<StorageObjectMeta, StorageError> {
        unimplemented!("Task 8")
    }
    async fn abort_upload(&self, _h: &UploadHandle) -> Result<(), StorageError> {
        unimplemented!("Task 8")
    }
    async fn get_stream(&self, _h: &StorageHandle) -> Result<ByteStream, StorageError> {
        Err(StorageError::Unsupported("baas")) // presign_download backend: 302 path used instead
    }
    async fn presign_get(&self, _h: &StorageHandle, _ttl: u64) -> Result<PresignGetTicket, StorageError> {
        unimplemented!("Task 9")
    }
    async fn delete(&self, _h: &StorageHandle) -> Result<(), StorageError> {
        unimplemented!("Task 10")
    }

    async fn health_check(&self) -> Result<StorageHealth, StorageError> {
        // Probe endpoint (or health_probe_path) without any real transfer_id.
        // Accept 2xx as ok; 401/404/405 means "reachable". 5xx/conn-error = not ok.
        let url = if self.cfg.health_probe_path.is_empty() {
            self.cfg.endpoint.clone()
        } else {
            format!("{}{}", self.cfg.endpoint.trim_end_matches('/'), self.cfg.health_probe_path)
        };
        let resp = self.auth(self.http.get(&url)).send().await;
        match resp {
            Ok(r) => {
                let s = r.status().as_u16();
                let ok = s < 500; // 2xx/3xx/4xx all imply "service responding"
                Ok(StorageHealth { ok, detail: if ok { None } else { Some(format!("baas health HTTP {s}")) } })
            }
            Err(e) => Ok(StorageHealth { ok: false, detail: Some(format!("baas unreachable: {e}")) }),
        }
    }
}

// placeholder so the crate compiles before Task 11's factory
pub mod factory {
    // filled in Task 11
}
```

- [ ] **Step 5: 写 health_check wiremock 测试**

Create `bcs-storage-baas/tests/health.rs`：

```rust
use bcs_storage_api::{StorageHealth, StoragePlugin};
use bcs_storage_baas::{config::BaasConfig, BaasStoragePlugin};
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(server_url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(
        BaasConfig { endpoint: server_url, tenant: "t".into(), ..Default::default() },
        5_000_000_000,
    )
}

#[tokio::test]
async fn health_check_ok_when_2xx() {
    let server = MockServer::start().await;
    Mock::given(method("GET")).and(path("/"))
        .respond_with(ResponseTemplate::new(200))
        .mount(&server).await;
    let h: StorageHealth = plugin(server.uri()).health_check().await.unwrap();
    assert!(h.ok);
}

#[tokio::test]
async fn health_check_ok_on_404_405() {
    let server = MockServer::start().await;
    Mock::given(method("GET")).and(path("/"))
        .respond_with(ResponseTemplate::new(404))
        .mount(&server).await;
    let h = plugin(server.uri()).health_check().await.unwrap();
    assert!(h.ok, "404 means reachable");
}

#[tokio::test]
async fn health_check_not_ok_on_500() {
    let server = MockServer::start().await;
    Mock::given(method("GET")).and(path("/"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&server).await;
    let h = plugin(server.uri()).health_check().await.unwrap();
    assert!(!h.ok);
}

#[tokio::test]
async fn health_check_not_ok_when_unreachable() {
    // unreachable port
    let h = plugin("http://127.0.0.1:1".into()).health_check().await.unwrap();
    assert!(!h.ok);
}
```

- [ ] **Step 6: 跑测试**

Run (在 baas crate 目录): `cargo test --test health`
Expected: 4 个 health_check 测试全过。

- [ ] **Step 7: Commit**

```bash
git add bcs-storage-baas/
git commit -m "feat(bcs-storage-baas): crate skeleton + BaasConfig + health_check"
```

> **注**：因 baas crate 独立 workspace，commit 在其自身仓库。若本期 plan 在 BCS 仓库一并管理（如 bcs-storage-baas 作为 BCS 仓库 subdirectory 但独立 Cargo），按实际仓库布局调整 git add 路径。下同。

---

### Task 7: prepare_upload（upload-url，SINGLE + MULTIPART）

**Files:**
- Modify: `bcs-storage-baas/src/lib.rs`（实现 prepare_upload + backend_handle serde 类型）
- Test: `bcs-storage-baas/tests/prepare.rs`

**Interfaces:**
- Consumes:`UploadPrepareRequest { key, file_name, mime_type, size, ttl_secs }`、`BaasConfig`、baas `POST .../upload-url`
- Produces:`PreparedUpload { handle: UploadHandle{ backend:"baas", key, backend_handle: {transfer_id, type, expires_at}, expires_at }, client_target: Direct{...}, expires_at }`
- **关键**：`UploadHandle.backend_handle` **不存 upload_url / parts URL**（那些进 client_target），仅存 `{transfer_id, type, expires_at}`（type 为 DIAG）。`size < 100MiB` → SINGLE（Direct{url}）；`≥` → MULTIPART（Direct{parts}, 顶层 url=null）。
- baas prepare body：`{filename, file_size, expire_seconds=ttyl_secs, operator, staging_subdir:null}`（无 device_path）

- [ ] **Step 1: 在 lib.rs 加 backend_handle serde 类型（Pending 形态）**

在 lib.rs 加（或新建 `src/handle.rs` 并 `pub mod handle;`）：

```rust
use serde::{Deserialize, Serialize};

/// backend_handle persisted while Pending: only the durable locator.
/// type is DIAG-only (baas doesn't need it on subsequent calls).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BaasPendingHandle {
    pub transfer_id: String,
    #[serde(rename = "type")]
    pub transfer_type: String, // "SINGLE" | "MULTIPART"
    pub expires_at: u64,
}

/// backend_handle after complete (Ready): slimmed to transfer_id only.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BaasReadyHandle {
    pub transfer_id: String,
}
```

- [ ] **Step 2: 写 prepare_upload 测试（SINGLE）**

Create `bcs-storage-baas/tests/prepare.rs`：

```rust
use bcs_storage_api::{ClientUploadTarget, StoragePlugin, UploadMode, UploadPrepareRequest};
use bcs_storage_baas::{config::BaasConfig, BaasStoragePlugin};
use serde_json::json;
use wiremock::matchers::{body_partial_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(
        BaasConfig { endpoint: url, tenant: "teamclaw".into(), ..Default::default() },
        5_000_000_000,
    )
}
fn req(size: u64) -> UploadPrepareRequest {
    UploadPrepareRequest { key: "session-files/prod/sid/fid/f".into(), file_name: "f".into(),
        mime_type: "application/octet-stream".into(), size, ttl_secs: 300 }
}

#[tokio::test]
async fn prepare_single_returns_direct_url_and_transfer_id() {
    let server = MockServer::start().await;
    // session_id "sid" encoded; path = /api/v1/sessions/teamclaw/sid/files/upload-url
    Mock::given(method("POST"))
        .and(path("/api/v1/sessions/teamclaw/sid/files/upload-url"))
        .and(body_partial_json(json!({"filename":"f","file_size":5,"staging_subdir":null})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"message":"success","data":{
                "upload_url":"https://oss/...?sig=X","transfer_id":"t-single",
                "http_method":"PUT","expires_at":"2026-07-23T12:00:00Z","type":"SINGLE"
            }
        })))
        .mount(&server).await;

    let p = plugin(server.uri());
    let r = p.prepare_upload(req(5)).await.unwrap();
    assert_eq!(r.handle.backend, "baas");
    // backend_handle only durable locator (no upload_url persisted)
    assert_eq!(r.handle.backend_handle["transfer_id"], "t-single");
    assert_eq!(r.handle.backend_handle["type"], "SINGLE");
    assert!(r.handle.backend_handle.get("upload_url").is_none(), "must NOT persist upload_url");
    match r.client_target {
        ClientUploadTarget::Direct { mode: UploadMode::Single, url, .. } => {
            assert_eq!(url.as_deref(), Some("https://oss/...?sig=X"));
        }
        other => panic!("expected Direct Single, got {other:?}"),
    }
}

#[tokio::test]
async fn prepare_multipart_returns_parts_and_null_top_url() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/sessions/teamclaw/sid/files/upload-url"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"message":"success","data":{
                "upload_url":null,"transfer_id":"t-multi","http_method":"PUT","expires_at":null,"type":"MULTIPART",
                "upload_session_id":"OSS-1","part_size":10485760,"part_count":2,
                "parts":[{"part_number":1,"upload_url":"https://oss/p1","http_method":"PUT","expires_at":"x"},
                         {"part_number":2,"upload_url":"https://oss/p2","http_method":"PUT","expires_at":"x"}]
            }
        })))
        .mount(&server).await;

    let p = plugin(server.uri());
    let r = p.prepare_upload(req(20*1024*1024)).await.unwrap();
    assert_eq!(r.handle.backend_handle["transfer_id"], "t-multi");
    assert_eq!(r.handle.backend_handle["type"], "MULTIPART");
    match r.client_target {
        ClientUploadTarget::Direct { mode: UploadMode::Multipart, url, parts, part_size, part_count } => {
            assert!(url.is_none());
            assert_eq!(part_count, Some(2));
            assert_eq!(part_size, Some(10485760));
            let parts = parts.unwrap();
            assert_eq!(parts.len(), 2);
            assert_eq!(parts[0].part_number, 1);
            assert_eq!(parts[0].url, "https://oss/p1");
        }
        other => panic!("expected Direct Multipart, got {other:?}"),
    }
    // per-part URLs NOT persisted in backend_handle
    assert!(r.handle.backend_handle.get("parts").is_none());
    assert!(r.handle.backend_handle.get("upload_session_id").is_none());
}

#[tokio::test]
async fn prepare_session_id_with_colon_is_percent_encoded() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/sessions/teamclaw/bcs_grp_abc%3Acdf28232/files/upload-url"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "code":0,"data":{"upload_url":"https://oss","transfer_id":"t","http_method":"PUT","expires_at":"x","type":"SINGLE"}
        })))
        .mount(&server).await;
    let p = plugin(server.uri());
    let mut r = req(5);
    r.key = "session-files/prod/bcs_grp_abc:cdf28232/fid/f".into(); // key 含 sid（含冒号）
    // session_id_from_key 取第 3 段 "bcs_grp_abc:cdf28232"；percent_encode_path 用 block-set：
    //   ':'→"%3A"，但 '_''a-z' 等保留 → "bcs_grp_abc%3Acdf28232"（与 mock path 一致）。
    let res = p.prepare_upload(r).await.unwrap();
    // 仅断言请求路径正确（已在 mock path 匹配）；若不匹配此 mock 会 404 失败。
    assert_eq!(res.handle.backend_handle["transfer_id"], "t");
}
```

> **Session_id 来源问题（实现者重大注意）**：`UploadPrepareRequest` 只有 `key`（形如 `session-files/{env}/{session_id}/{file_id}/{file_name}`），**没有独立 session_id 字段**。baas 路径需要 session_id。两种解法：
> (A) 从 `key` 解析第 3 段作 session_id（脆弱，key 格式约定）。
> (B) 改 `StoragePlugin` trait，给 `prepare_upload` 传 session_id（侵入 trait + 所有后端）。
> 设计文档假设 baas 拿得到 session_id。**本 plan 采用 (A)**：在 lib.rs 加 `fn session_id_from_key(key: &str) -> &str`，按 `session-files/{env}/{session_id}/...` 取第 3 段。`complete_upload`/`delete`/`presign_get` 也从 handle.key 同法取。
>
> 这是个**设计与 trait 的缝隙**——落地时若觉得 parser 脆弱，可在此处记录技术债并提议给 trait 加 session_id 入参（不在本 plan 范围）。先实现 (A) 让功能跑通。

> **🔴 P2-D operator 是常量（trait 缝隙，实现决策）**：baas prepare/share-link body 的 `operator` 设计要求 = BCS caller（human/bot id，供 baas 审计）。但 `StoragePlugin` trait 的 `prepare_upload`/`presign_get` 签名**没有 caller 参数**，plugin 是装配期单例、拿不到每次请求的真实 caller。两个选项：
> (A) `operator` 写死常量 `"bcs"`（本 plan 选此 —— 放弃 baas 侧按上传者审计的粒度，仅能区分"BCS 服务发起"）。
> (B) 改 trait 给 prepare/presign 加 caller 入参（波及 local/fake/所有后端，且 caller 透传需穿过 service 层，不在本期）。
> 本 plan everywhere 用 `"operator": "bcs"`。**落地者须在 baas design 文档「身份/租户」节把 `operator` 描述从"= BCS caller"改为"= 常量 \"bcs\"（trait 不传 caller；按上传者审计见 tech-debt）"**，Task 13 的实现注记一并补上此条。
> 这是个**设计与 trait 的缝隙**——落地时若觉得 parser 脆弱，可在此处记录技术债并提议给 trait 加 session_id 入参（不在本 plan 范围）。先实现 (A) 让功能跑通。

- [ ] **Step 3: 实现 prepare_upload**

在 lib.rs 实现 `prepare_upload`（替换 `unimplemented!`）：

```rust
    async fn prepare_upload(&self, req: UploadPrepareRequest) -> Result<PreparedUpload, StorageError> {
        let session_id = session_id_from_key(&req.key);
        let base = self.base_for_session(session_id);
        let body = serde_json::json!({
            "filename": req.file_name,
            "file_size": req.size,
            "expire_seconds": req.ttl_secs,
            "staging_subdir": serde_json::Value::Null,
            "operator": "bcs",  // ⬇ P2-D 见下注记
        });
        let resp = self.auth(self.http.post(format!("{base}/upload-url")).json(&body)).send().await
            .map_err(|e| StorageError::Backend(e.into()))?;
        let data = baas_data(resp).await?;  // helper: assert code==0, return data
        let transfer_id = data["transfer_id"].as_str().ok_or_else(|| bad("missing transfer_id"))?.to_string();
        let type_str = data["type"].as_str().unwrap_or("SINGLE").to_string();
        let expires_at = parse_iso_to_unix(data["expires_at"].as_str().unwrap_or(""))
            .unwrap_or(req.ttl_secs);

        let (client_target, handle) = if type_str == "MULTIPART" {
            let part_size = data["part_size"].as_u64().unwrap_or(0);
            let part_count = data["part_count"].as_u32().unwrap_or(0);
            let parts: Vec<UploadPartUrl> = data["parts"].as_array().iter().flatten().map(|p| UploadPartUrl {
                part_number: p["part_number"].as_u64().unwrap_or(0) as u16,
                url: p["upload_url"].as_str().unwrap_or("").to_string(),
            }).collect();
            let ct = ClientUploadTarget::Direct {
                mode: UploadMode::Multipart, url: None, parts: Some(parts),
                part_size: Some(part_size), part_count: Some(part_count),
            };
            (ct, pending_handle(transfer_id, "MULTIPART", expires_at))
        } else {
            let url = data["upload_url"].as_str().unwrap_or("").to_string();
            let ct = ClientUploadTarget::Direct {
                mode: UploadMode::Single, url: Some(url), parts: None, part_size: None, part_count: None,
            };
            (ct, pending_handle(transfer_id, "SINGLE", expires_at))
        };
        Ok(PreparedUpload {
            handle: UploadHandle { backend: "baas".into(), key: req.key.clone(), backend_handle: handle, expires_at },
            client_target, expires_at,
        })
    }
```

加 helpers（lib.rs 内）：

```rust
fn session_id_from_key(key: &str) -> &str {
    // key = "session-files/{env}/{session_id}/{file_id}/{file_name}"
    let mut it = key.split('/');
    it.next(); // session-files
    it.next(); // env
    it.next().unwrap_or("") // session_id
}

fn pending_handle(transfer_id: String, ty: &str, expires_at: u64) -> serde_json::Value {
    serde_json::to_value(BaasPendingHandle {
        transfer_id, transfer_type: ty.into(), expires_at,
    }).expect("BaasPendingHandle serializable")
}

fn bad(msg: &str) -> StorageError { StorageError::Backend(anyhow::anyhow!("baas bad response: {msg}")) }

/// Parse an HTTP response, assert `code == 0`, return the `data` object.
async fn baas_data(resp: reqwest::Response) -> Result<serde_json::Value, StorageError> {
    let status = resp.status();
    if !status.is_success() {
        let body: serde_json::Value = resp.json().await.map_err(|e| StorageError::Backend(e.into()))?;
        let detail = &body["detail"];
        let code = detail["error"].as_str().unwrap_or("");
        let msg = detail["message"].as_str().unwrap_or("");
        // DELETE-idempotent short-circuit handled by callers; here surface the mapped error.
        return Err(crate::error::map_baas_error(code, status.as_u16(), msg));
    }
    let body: serde_json::Value = resp.json().await.map_err(|e| StorageError::Backend(e.into()))?;
    if body["code"].as_i64() != Some(0) {
        return Err(bad(&format!("non-zero code: {}", body)));
    }
    Ok(body["data"].clone())
}

/// ISO 8601 -> unix seconds. Minimal parse: handle 'Z' and offset forms via splitting on 'T'/'+'/'-'.
fn parse_iso_to_unix(s: &str) -> Option<u64> {
    if s.is_empty() { return None; }
    // 用 chrono 更稳，但本 crate 未引 chrono。此处用简单方案：留给 Task 实现时按需引 chrono。
    // 暂返回 None（调用方回退到 ttl_secs）。
    None
}
```

> **实现者注**：`baas_data` 里 `null::<serde_json::Value>()` 是错的写法 —— Step 3 的 body 里 `"staging_subdir": null::<serde_json::Value>()` 改为 `"staging_subdir": serde_json::Value::Null`。`parse_iso_to_unix` 临时返 None，Task 9（presign_get 需 expires_at）时引入 chrono 正式解析。

- [ ] **Step 4: 跑测试 + 修编译**

Run: `cargo test --test prepare`
按编译错误修正（`UploadPartUrl`/`UploadMode` 导入 from bcs_storage_api、`null` 写法等）。Expected: 3 个 prepare 测试过。

- [ ] **Step 5: Commit**

```bash
git add bcs-storage-baas/
git commit -m "feat(bcs-storage-baas): prepare_upload (SINGLE + MULTIPART, transfer_id handle)"
```

---

### Task 8: complete_upload + abort_upload

**Files:**
- Modify: `bcs-storage-baas/src/lib.rs`
- Test: `bcs-storage-baas/tests/complete_abort.rs`

**Interfaces:**
- Consumes:`UploadHandle.backend_handle`（Pending 形态：含 transfer_id/type/expires_at）、`session_id_from_key(handle.key)`
- Produces:`complete_upload → StorageObjectMeta { key, size, sha256: None }`（sync DONE）；`abort_upload → ()`
- complete: `POST {base}/upload-url/{transfer_id}/complete`（空 body）→ data `{transfer_id, status:"DONE"}`
- abort: `DELETE {base}/upload-url/{transfer_id}` → data `{transfer_id, status:"CANCELLED"}`
- **complete 不持久化新的 backend_handle**（service 层负责把 Pending→Ready handle 瘦身，本插件只返 meta；但 baas 上传 handle 里有 type/expires_at，complete 后 service 拿到的仍是 Pending handle——**注意**：`complete_upload` 的返回 `StorageObjectMeta` 不含 handle，service 层 `complete_upload`(service.rs) 把 `UploadHandle` 转 `StorageHandle` 时是直接重用 backend_handle。**这意味着 Ready 的 backend_handle 还是 Pending 形态（含 type/expires_at）**。设计文档说 Ready 瘦身到仅 transfer_id —— 但 service 层 `complete_upload` 调完 plugin 后自己构造 `StorageHandle { backend_handle: upload_handle.backend_handle }`（原样透传），**不做瘦身**。
- **🔴 P1-A 冲突（落地阻断）**：baas `complete_upload` 返 `StorageObjectMeta { size: 0, .. }`（baas complete 响应不含 size），但 service 层 `bcs-session-file/src/service.rs` `complete_upload` 有防御校验：
  ```rust
  if meta.size != row.size && upload_handle.backend_handle.get("parts").is_none() {
      return Err(SessionFileUseCaseError::Conflict(...));
  }
  ```
  baas single 上传的 `backend_handle` 无 `parts` 键（pending_handle 只设 transfer_id/type/expires_at）→ `meta.size(0) != row.size` 必然触发 → **所有 baas 单分片上传 complete 都被拒为 Conflict**。trait `complete_upload` 不传 `expected_size`，plugin 拿不到 prepare size，无法返对。**解法（本 plan 选此）**：在 service 层放宽——后端 `backend == "baas"`（presign_put 后端）时跳过该 size 校验（baas complete 已由 OSS 验证对象存在、且 presign 后端不返 size，校验无意义）。落地为 **Task 8.5**（紧随 Task 8）。备选（改 trait 给 complete 传 expected_size）波及 local/fake，不在本期。

> **缝隙**：设计文档说 Ready handle 瘦身到仅 transfer_id，但 service 层 `complete_upload`(service.rs:~520) 不做瘦身（透传 backend_handle）。这是设计 vs 实现的又一处差异。两个选项：
> (a) baas `complete_upload` 返回 meta 时，service 不改 handle —— 则 Ready handle 仍含 type/expires_at（无害，只是稍大）。
> (b) 让 baas plugin 在 complete **内部**没法瘦身 service 的 handle（trait 不返回 handle）。
> **本 plan 选 (a)**：接受 Ready handle 含 type/expires_at（冗余但无害），不强行瘦身。设计文档的"瘦身"改为"首选瘦身，但因 trait 不返 handle，service 透传，baas Ready handle 仍含 DIAG 字段；可接受"。若要干净瘦身，需改 trait 让 complete 返回新 handle —— 不在本 plan 范围。
>
> 落地者：在 baas design 文档相应处加一条「实现注记」说明此点（Ready handle 不瘦身，因 trait 限制），保持设计文档与实现一致。

> **🔴 P1-A 缝隙注记**：baas complete 不返 size + service 防 size-mismatch → 见 **Task 8.5** 在 service 层放宽（presign_put 后端跳过 size 校验）。Task 8 的 `complete_returns_meta_sync_done` 测试断言 `meta.size == 0` 不变；单测在 baas crate 内不会触发 service 层校验，但**集成路径（service→baas）需 Task 8.5 才不卡住**。

- [ ] **Step 1: 写测试**

Create `bcs-storage-baas/tests/complete_abort.rs`：

```rust
use bcs_storage_api::{StoragePlugin, UploadHandle};
use bcs_storage_baas::{config::BaasConfig, BaasPendingHandle, BaasStoragePlugin};
use serde_json::json;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(BaasConfig { endpoint: url, tenant: "t".into(), ..Default::default() }, 5_000_000_000)
}
fn pending_handle(key: &str, tid: &str) -> UploadHandle {
    UploadHandle {
        backend: "baas".into(), key: key.into(),
        backend_handle: serde_json::to_value(BaasPendingHandle {
            transfer_id: tid.into(), transfer_type: "SINGLE".into(), expires_at: 3600,
        }).unwrap(),
        expires_at: 3600,
    }
}

#[tokio::test]
async fn complete_returns_meta_sync_done() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/upload-url/tid/complete"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{"transfer_id":"tid","status":"DONE"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    // key session-files/prod/sid/... → session_id "sid"
    let h = pending_handle("session-files/prod/sid/fid/f", "tid");
    let meta = p.complete_upload(&h).await.unwrap();
    assert_eq!(meta.size, 0); // baas complete 响应不含 size，meta.size=0（service 层用 prepare size 覆盖）
}

#[tokio::test]
async fn abort_deletes_upload_url_transfer() {
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/upload-url/tid"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{"transfer_id":"tid","status":"CANCELLED"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let h = pending_handle("session-files/prod/sid/fid/f", "tid");
    p.abort_upload(&h).await.unwrap();
}

#[tokio::test]
async fn complete_oss_object_not_found_maps_to_conflict() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/upload-url/tid/complete"))
        .respond_with(ResponseTemplate::new(409).set_body_json(json!({"detail":{"error":"OSS_OBJECT_NOT_FOUND","message":"no object"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let h = pending_handle("session-files/prod/sid/fid/f", "tid");
    let err = p.complete_upload(&h).await.unwrap_err();
    assert!(matches!(err, bcs_storage_api::StorageError::Conflict(_)));
}
```

- [ ] **Step 2: 实现 complete_upload + abort_upload**

```rust
    async fn complete_upload(&self, handle: &UploadHandle) -> Result<StorageObjectMeta, StorageError> {
        let pending: BaasPendingHandle = serde_json::from_value(handle.backend_handle.clone())
            .map_err(|e| StorageError::Backend(e.into()))?;
        let session_id = session_id_from_key(&handle.key);
        let base = self.base_for_session(session_id);
        let resp = self.auth(self.http.post(format!("{base}/upload-url/{}/complete",
                    percent_encode_path(&pending.transfer_id))).body(serde_json::json!({}).to_string()))
            .send().await.map_err(|e| StorageError::Backend(e.into()))?;
        let _data = baas_data(resp).await?; // sync DONE; data has status
        Ok(StorageObjectMeta { key: handle.key.clone(), size: 0, sha256: None })
    }

    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError> {
        let pending: BaasPendingHandle = serde_json::from_value(handle.backend_handle.clone())
            .map_err(|e| StorageError::Backend(e.into()))?;
        let session_id = session_id_from_key(&handle.key);
        let base = self.base_for_session(session_id);
        let resp = self.auth(self.http.delete(format!("{base}/upload-url/{}",
                    percent_encode_path(&pending.transfer_id)))).send().await
            .map_err(|e| StorageError::Backend(e.into()))?;
        // CANCELLED (or already terminal) — treat 2xx + TRANSFER_STATE_CONFLICT as Ok.
        let _ = baas_data_or_conflict_ok(resp).await?;
        Ok(())
    }
```

加 helper `baas_data_or_conflict_ok`（abort 幂等：409 TRANSFER_STATE_CONFLICT 也算 Ok，因 ticket 可能已终态）：

```rust
async fn baas_data_or_conflict_ok(resp: reqwest::Response) -> Result<serde_json::Value, StorageError> {
    let status = resp.status();
    if status.is_success() {
        return Ok(serde_json::Value::Null); // abort 幂等成功不取 data
    }
    let body: serde_json::Value = resp.json().await.map_err(|e| StorageError::Backend(e.into()))?;
    let code = body["detail"]["error"].as_str().unwrap_or("");
    if code == "TRANSFER_STATE_CONFLICT" {
        return Ok(serde_json::Value::Null); // already terminal — idempotent ok
    }
    Err(crate::error::map_baas_error(code, status.as_u16(), body["detail"]["message"].as_str().unwrap_or("")))
}
```

- [ ] **Step 3: 跑测试**

Run: `cargo test --test complete_abort`
Expected: 3 个过。

- [ ] **Step 4: Commit**

```bash
git add bcs-storage-baas/
git commit -m "feat(bcs-storage-baas): complete_upload (sync DONE) + abort_upload (idempotent)"
```

---

### Task 8.5: service 层放宽单分片 size 校验（baas complete 不返 size）

**🔴 P1-A 落地阻断修复**。baas `complete_upload` 返 `size: 0`（baas complete 响应无 size），但 `bcs-session-file/src/service.rs` `complete_upload` 的防御校验 `if meta.size != row.size && backend_handle.get("parts").is_none() → Conflict` 会拒掉所有 baas 单分片上传（baas single 的 backend_handle 无 `parts` 键）。trait 不传 `expected_size` 给 complete，plugin 无法返对 → 在 **service 层**放宽：presign_put 后端（baas/OSS，`backend == "baas"` 或更通用 `supports_presign_put`）跳过该 size 校验（OSS 已验证对象存在，size 校验无意义；多分片仍由后端 list_parts 保证）。

**Files:**
- Modify: `src/bcs/crates/services/bcs-session-file/src/service.rs`（`complete_upload` 的 size 校验块）
- Test: 新增 service 单测 `complete_baas_single_skips_size_check`（用 FakeStoragePlugin 返 size:0 + backend_handle 无 parts，但 backend="baas"/presign_put，断言不 Conflict；或直接断言校验路径跳过）

**Interfaces:**
- Consumes:`upload_handle.backend`（已有，判后端名）；`upload_handle.backend_handle`（判 parts 已有）
- Produces:`complete_upload` 的 size 校验增加后端守卫：`backend_handle.get("parts").is_none() && !is_presign_put_backend(upload_handle.backend)` 才校验 size
- **判定方式**：用 `upload_handle.backend == "baas"`（具体名）还是 `self.caps.supports_presign_put`？`self.caps` 是 service 持有的后端能力（`SessionFileServiceConfig` 有 `caps` 或经 storage.capabilities()）。**本 plan 用 `self.caps.supports_presign_put`**（后端无关判据，OSS future 后端同样受益）。确认 service 是否持 `caps`：

Run: `grep -n "supports_presign_put\|self.caps\|caps:" src/bcs/crates/services/bcs-session-file/src/service.rs | head`
若 service 持有 `caps: StorageCapabilities`（应在 `SessionFileServiceImpl` 里），用它；否则用 `upload_handle.backend == "baas"` 兜底（更窄但稳）。**优先 `self.caps.supports_presign_put`**。

- [ ] **Step 1: 写失败测试（service 层）**

在 `src/bcs/crates/services/bcs-session-file/src/service.rs` 测试模块加。需 FakeStoragePlugin 能返 size:0 + 无 parts 的 backend_handle、且 capability `supports_presign_put=true`。`FakeStoragePlugin` 现状 `complete_upload` 返的 meta size 是实际写入字节数（不为 0），无法直接模拟 baas 的"不返 size"。**故此测试用一个最小 fake presign backend**：在测试模块内定义 `PresignFake`（实现 StoragePlugin，`complete_upload` 返 `StorageObjectMeta { size: 0, .. }`、`supports_presign_put=true`），inject 到 service，prepare→stream→complete，断言 complete **不**返 Conflict、行转 Ready：

```rust
    #[tokio::test]
    async fn complete_presign_backend_single_skips_size_mismatch_check() {
        // presign_put backend whose complete_upload returns size=0 (no size in response),
        // single-part (backend_handle has no "parts"). service must NOT reject this as
        // Conflict (P1-A: baas complete response carries no size).
        let storage: Arc<dyn StoragePlugin> = Arc::new(PresignSizelessComplete::default());
        let (svc, repo) = build_svc_with_storage(storage);
        let r = svc.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        svc.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5).await.unwrap();
        let ready = svc.complete_upload("g1:abcd1234", &r.file.file_id).await.unwrap();
        assert_eq!(ready.status, FileStatus::Ready); // not rejected as Conflict
    }
```

`PresignSizelessComplete`（测试模块内）：

```rust
    /// Minimal presign_put backend whose complete_upload returns size=0 and a
    /// parts-less backend_handle (mirrors baas single). Used to assert service
    /// skips the size-mismatch defense for presign_put backends.
    #[derive(Default)]
    struct PresignSizelessComplete { staging: Arc<tokio::sync::Mutex<bytes::Bytes>> }
    #[async_trait]
    impl StoragePlugin for PresignSizelessComplete {
        fn backend_name(&self) -> &'static str { "sizeless" }
        fn capabilities(&self) -> StorageCapabilities {
            StorageCapabilities { supports_presign_put: true, supports_presign_download: false,
                supports_stream_put: true, supports_stream_get: true, max_object_size: u64::MAX }
        }
        async fn prepare_upload(&self, req: UploadPrepareRequest) -> Result<PreparedUpload, StorageError> {
            Ok(PreparedUpload {
                handle: UploadHandle { backend: "sizeless".into(), key: req.key.clone(),
                    backend_handle: serde_json::json!({ "transfer_id": "t", "type": "SINGLE" }), // 无 parts
                    expires_at: req.ttl_secs },
                client_target: ClientUploadTarget::ProxyViaBcs, // 测试用 stream_upload 注入
                expires_at: req.ttl_secs })
        }
        async fn stream_upload(&self, h: &UploadHandle, _p: Option<u16>, mut b: ByteStream) -> Result<(), StorageError> {
            use futures::StreamExt;
            let mut v = Vec::new();
            while let Some(c) = b.next().await { v.extend_from_slice(&c.unwrap()); }
            *self.staging.lock().await = bytes::Bytes::from(v);
            Ok(())
        }
        async fn complete_upload(&self, _h: &UploadHandle) -> Result<StorageObjectMeta, StorageError> {
            Ok(StorageObjectMeta { key: "k".into(), size: 0, sha256: None }) // size 0 like baas
        }
        async fn abort_upload(&self, _: &UploadHandle) -> Result<(), StorageError> { Ok(()) }
        async fn get_stream(&self, _: &StorageHandle) -> Result<ByteStream, StorageError> {
            unimplemented!()
        }
        async fn presign_get(&self, _: &StorageHandle, t: u64) -> Result<PresignGetTicket, StorageError> {
            Ok(PresignGetTicket { download_url: "x".into(), expires_at: t })
        }
        async fn delete(&self, _: &StorageHandle) -> Result<(), StorageError> { Ok(()) }
        async fn health_check(&self) -> Result<StorageHealth, StorageError> {
            Ok(StorageHealth { ok: true, detail: None })
        }
    }
```

> **实现者注**：`build_svc_with_storage` 在 Task (C3 的) delete-failure 测试里已存在（`Cargo` 测试helper，inject 自定义 storage）；复用它。若不存在则参考现有 `build_svc` 加一个 storage 参数版。

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test -p bcs-session-file complete_presign_backend_single_skips_size_mismatch_check`
Expected: 失败 —— `complete_upload` 返 `Conflict("completed size 0 != prepared size 5")`（现状 size 校验触发）。

- [ ] **Step 3: 改 service complete_upload 的 size 校验，加 supports_presign_put 守卫**

`src/bcs/crates/services/bcs-session-file/src/service.rs` `complete_upload`，把：

```rust
        if meta.size != row.size && upload_handle.backend_handle.get("parts").is_none() {
            return Err(SessionFileUseCaseError::Conflict(format!(
                "completed size {} != prepared size {}",
                meta.size, row.size,
            )));
        }
```

改为：

```rust
        // Defensive size check for non-presign single uploads (local): the backend
        // returns the actual written size, which must match what we prepared.
        // Presign_put backends (baas/OSS) do NOT return size on complete (OSS object
        // existence is verified server-side, not byte-counted for the client); skip
        // the check for them to avoid spurious Conflict (P1-A).
        if !self.caps.supports_presign_put
            && meta.size != row.size
            && upload_handle.backend_handle.get("parts").is_none()
        {
            return Err(SessionFileUseCaseError::Conflict(format!(
                "completed size {} != prepared size {}",
                meta.size, row.size,
            )));
        }
```

若 service 无 `self.caps`（grep 确认），改用 `upload_handle.backend != "baas"`（更窄）；并加注释说明 presign_put 后端不返 size。**优先 `self.caps.supports_presign_put`**。

- [ ] **Step 4: 跑测试确认通过 + 既有 complete 测试无回归**

Run: `cargo test -p bcs-session-file`
Expected: 全绿，含 `complete_presign_backend_single_skips_size_mismatch_check` 且既有 local single size-mismatch 测试（若有 `stream_rejects_size_mismatch_*` 之类）仍红/绿按预期——**核对**：local 后端 `supports_presign_put=false`，校验仍生效，既有 local size-mismatch 行为不变。

- [ ] **Step 5: Commit**

```bash
git add src/bcs/crates/services/bcs-session-file/src/service.rs
git commit -m "fix(bcs-session-file): skip complete size check for presign_put backends (baas returns no size)"
```

---

### Task 9: presign_get（同步 share-link）+ ISO8601 解析

**Files:**
- Modify: `bcs-storage-baas/src/lib.rs`、`Cargo.toml`（加 chrono）
- Test: `bcs-storage-baas/tests/presign.rs`

**Interfaces:**
- Consumes:`StorageHandle.backend_handle`（含 transfer_id）、`cfg.share_link_ttl`、`session_id_from_key`
- Produces:`PresignGetTicket { download_url: share_url, expires_at: <unix secs from ISO8601> }`
- `POST {base}/transfers/{transfer_id}/share-link` body `{expire_seconds: ttl, operator}` → data `{share_url, transfer_id, expires_at(ISO8601)}`
- 仅 DONE 可调；非 DONE → SOURCE_TRANSFER_NOT_READY(409)→Conflict
- **不缓存**：每次调用都 POST

- [ ] **Step 1: 加 chrono 依赖**

`Cargo.toml` `[dependencies]` 加：`chrono = { version = "0.4", default-features = false, features = ["std", "parsing"] }`

- [ ] **Step 2: 写测试**

Create `bcs-storage-baas/tests/presign.rs`：

```rust
use bcs_storage_api::{PresignGetTicket, StorageHandle, StoragePlugin};
use bcs_storage_baas::{config::BaasConfig, BaasReadyHandle, BaasStoragePlugin};
use serde_json::json;
use wiremock::matchers::{body_partial_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(BaasConfig { endpoint: url, tenant: "t".into(), share_link_ttl: 3600, ..Default::default() }, 5_000_000_000)
}
fn ready_handle(key: &str, tid: &str) -> StorageHandle {
    StorageHandle {
        backend: "baas".into(), key: key.into(),
        backend_handle: serde_json::to_value(BaasReadyHandle { transfer_id: tid.into() }).unwrap(),
    }
}

#[tokio::test]
async fn presign_get_returns_share_url_with_iso_expires_at() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/transfers/tid/share-link"))
        .and(body_partial_json(json!({"expire_seconds":3600})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{
            "share_url":"https://oss/get?sig=Y","transfer_id":"tid","expires_at":"2026-07-23T12:00:00Z"
        }})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let ticket: PresignGetTicket = p.presign_get(&ready_handle("session-files/prod/sid/fid/f", "tid"), 3600).await.unwrap();
    assert_eq!(ticket.download_url, "https://oss/get?sig=Y");
    assert!(ticket.expires_at > 0, "ISO8601 parsed to unix secs");
}

#[tokio::test]
async fn presign_get_not_ready_maps_to_conflict() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/transfers/tid/share-link"))
        .respond_with(ResponseTemplate::new(409).set_body_json(json!({"detail":{"error":"SOURCE_TRANSFER_NOT_READY","message":"UPLOADING","transfer_id":"tid","current_status":"UPLOADING"}})))
        .mount(&server).await;
    let p = plugin(server.uri());
    let err = p.presign_get(&ready_handle("session-files/prod/sid/fid/f", "tid"), 3600).await.unwrap_err();
    assert!(matches!(err, bcs_storage_api::StorageError::Conflict(_)));
}

#[tokio::test]
async fn presign_get_does_not_cache_each_call_re_posts() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path("/api/v1/sessions/t/sid/files/transfers/tid/share-link"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{"share_url":"https://oss/x","transfer_id":"tid","expires_at":"2026-07-23T12:00:00Z"}})))
        .expect(2) // 两次调用都 POST — 不缓存
        .mount(&server).await;
    let p = plugin(server.uri());
    let h = ready_handle("session-files/prod/sid/fid/f", "tid");
    p.presign_get(&h, 3600).await.unwrap();
    p.presign_get(&h, 3600).await.unwrap();
}
```

- [ ] **Step 3: 实现 presign_get + ISO8601 解析**

```rust
    async fn presign_get(&self, handle: &StorageHandle, ttl_secs: u64) -> Result<PresignGetTicket, StorageError> {
        let ready: BaasReadyHandle = serde_json::from_value(handle.backend_handle.clone())
            .or_else(|_| serde_json::from_value::<BaasPendingHandle>(handle.backend_handle.clone())  // 兼容 service 透传的 Pending 形态 handle
                        .map(|p| BaasReadyHandle { transfer_id: p.transfer_id }))
            .map_err(|e| StorageError::Backend(e.into()))?;
        let session_id = session_id_from_key(&handle.key);
        let base = self.base_for_session(session_id);
        let body = serde_json::json!({ "expire_seconds": ttl_secs, "operator": "bcs" });
        let resp = self.auth(self.http.post(format!("{base}/transfers/{}/share-link",
                    percent_encode_path(&ready.transfer_id))).json(&body)).send().await
            .map_err(|e| StorageError::Backend(e.into()))?;
        let data = baas_data(resp).await?;
        let share_url = data["share_url"].as_str().ok_or_else(|| bad("missing share_url"))?.to_string();
        let expires_at = parse_iso_to_unix(data["expires_at"].as_str().unwrap_or("")).unwrap_or(ttl_secs);
        Ok(PresignGetTicket { download_url: share_url, expires_at })
    }
```

正式实现 `parse_iso_to_unix`（用 chrono）：

```rust
fn parse_iso_to_unix(s: &str) -> Option<u64> {
    use chrono::DateTime;
    if s.is_empty() { return None; }
    // 支持带 Z 或 offset 的 ISO8601
    let dt = DateTime::parse_from_rfc3339(s).ok()?;
    Some(dt.timestamp().max(0) as u64)
}
```

> **实现者注**：`presign_get` 兼容 Pending/Ready 两种 backend_handle（因 Task 8 注记：service 透传的 handle 可能仍含 type/expires_at）。落 `BaasPendingHandle`→`BaasReadyHandle` 的 fallback 转换。这一段是必要的鲁棒性，别删。

- [ ] **Step 4: 跑测试**

Run: `cargo test --test presign`
Expected: 3 个过。

- [ ] **Step 5: 跑全 crate 测试确认无回归**

Run: `cargo test`
Expected: 全绿（health/prepare/complete_abort/presign）。

- [ ] **Step 6: Commit**

```bash
git add bcs-storage-baas/
git commit -m "feat(bcs-storage-baas): presign_get via sync share-link (no cache, ISO8601 expires_at)"
```

---

### Task 10: delete（按 transfer_id，幂等）

**Files:**
- Modify: `bcs-storage-baas/src/lib.rs`
- Test: `bcs-storage-baas/tests/delete.rs`

**Interfaces:**
- Consumes:`StorageHandle.backend_handle`（transfer_id）、`session_id_from_key`
- `DELETE {base}/transfers/{transfer_id}` → data `{transfer_id, previous_status, new_status:"DELETED"}`
- 已 DELETED 重复删（404 TRANSFER_NOT_FOUND 或 2xx 均算 Ok）；TRANSFER_NOT_TERMINAL(409) → Conflict

- [ ] **Step 1: 写测试**

Create `bcs-storage-baas/tests/delete.rs`：

```rust
use bcs_storage_api::{StorageError, StorageHandle, StoragePlugin};
use bcs_storage_baas::{config::BaasConfig, BaasReadyHandle, BaasStoragePlugin};
use serde_json::json;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(BaasConfig { endpoint: url, tenant: "t".into(), ..Default::default() }, 5_000_000_000)
}
fn h(key: &str, tid: &str) -> StorageHandle {
    StorageHandle { backend: "baas".into(), key: key.into(),
        backend_handle: serde_json::to_value(BaasReadyHandle { transfer_id: tid.into() }).unwrap() }
}

#[tokio::test]
async fn delete_returns_ok_on_deleted() {
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/transfers/tid"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code":0,"data":{"transfer_id":"tid","previous_status":"DONE","new_status":"DELETED"}})))
        .mount(&server).await;
    plugin(server.uri()).delete(&h("session-files/prod/sid/f/f", "tid")).await.unwrap();
}

#[tokio::test]
async fn delete_idempotent_on_transfer_not_found() {
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/transfers/tid"))
        .respond_with(ResponseTemplate::new(404).set_body_json(json!({"detail":{"error":"TRANSFER_NOT_FOUND","message":"gone"}})))
        .mount(&server).await;
    plugin(server.uri()).delete(&h("session-files/prod/sid/f/f", "tid")).await.unwrap(); // 404 -> Ok 幂等
}

#[tokio::test]
async fn delete_not_terminal_maps_conflict() {
    let server = MockServer::start().await;
    Mock::given(method("DELETE")).and(path("/api/v1/sessions/t/sid/files/transfers/tid"))
        .respond_with(ResponseTemplate::new(409).set_body_json(json!({"detail":{"error":"TRANSFER_NOT_TERMINAL","message":"UPLOADING"}})))
        .mount(&server).await;
    let err = plugin(server.uri()).delete(&h("session-files/prod/sid/f/f", "tid")).await.unwrap_err();
    assert!(matches!(err, StorageError::Conflict(_)));
}
```

- [ ] **Step 2: 实现 delete**

```rust
    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError> {
        let ready: BaasReadyHandle = serde_json::from_value(handle.backend_handle.clone())
            .or_else(|_| serde_json::from_value::<BaasPendingHandle>(handle.backend_handle.clone())
                        .map(|p| BaasReadyHandle { transfer_id: p.transfer_id }))
            .map_err(|e| StorageError::Backend(e.into()))?;
        let session_id = session_id_from_key(&handle.key);
        let base = self.base_for_session(session_id);
        let resp = self.auth(self.http.delete(format!("{base}/transfers/{}",
                    percent_encode_path(&ready.transfer_id)))).send().await
            .map_err(|e| StorageError::Backend(e.into()))?;
        if resp.status().is_success() {
            return Ok(());
        }
        let status = resp.status();
        let body: serde_json::Value = resp.json().await.map_err(|e| StorageError::Backend(e.into()))?;
        let code = body["detail"]["error"].as_str().unwrap_or("");
        if crate::error::is_delete_idempotent_ok(Some(code)) {
            return Ok(());
        }
        Err(crate::error::map_baas_error(code, status.as_u16(), body["detail"]["message"].as_str().unwrap_or("")))
    }
```

- [ ] **Step 3: 跑测试**

Run: `cargo test --test delete`
Expected: 3 个过。

- [ ] **Step 4: 表驱动错误映射测试**

Create `bcs-storage-baas/tests/error_map.rs`：

```rust
use bcs_storage_api::StorageError;
use bcs_storage_baas::error::map_baas_error;

#[test]
fn error_code_map_table() {
    let cases: &[(&str, u16, &str, &str)] = &[
        ("TRANSFER_NOT_FOUND", 404, "m", "NotFound"),
        ("SOURCE_TRANSFER_NOT_FOUND", 404, "m", "NotFound"),
        ("SOURCE_TRANSFER_NOT_READY", 409, "m", "Conflict"),
        ("TRANSFER_STATE_CONFLICT", 409, "m", "Conflict"),
        ("TRANSFER_NOT_TERMINAL", 409, "m", "Conflict"),
        ("OSS_OBJECT_NOT_FOUND", 409, "m", "Conflict"),
        ("INVALID_TRANSITION", 422, "m", "Conflict"),
        ("NOT_IMPLEMENTED", 501, "m", "Unsupported"),
        ("INTERNAL_ERROR", 500, "m", "Backend"),
        ("UNKNOWN_NEW_CODE", 500, "m", "Backend"),
    ];
    for (code, status, msg, expect) in cases {
        let e = map_baas_error(code, *status, msg);
        let got = match e {
            StorageError::NotFound => "NotFound",
            StorageError::Conflict(_) => "Conflict",
            StorageError::Unsupported(_) => "Unsupported",
            StorageError::Backend(_) => "Backend",
            StorageError::InvalidInput(_) => "InvalidInput",
        };
        assert_eq!(got, *expect, "code {code} expected {expect} got {got}");
    }
}
```

注：`map_baas_error` 与 `is_delete_idempotent_ok` 需在 lib.rs 顶端 `pub use error::{map_baas_error, is_delete_idempotent_ok};` 或 `pub mod error;`（已 pub mod）。确认 error 模块函数为 `pub`。

- [ ] **Step 5: 跑错误映射测试 + 全 crate**

Run: `cargo test --test error_map && cargo test`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add bcs-storage-baas/
git commit -m "feat(bcs-storage-baas): delete by transfer_id (idempotent) + error map tests"
```

---

### Task 11: `BaasStoragePluginFactory`

**Files:**
- Modify: `bcs-storage-baas/src/factory.rs`、`src/lib.rs`
- Test: `bcs-storage-baas/tests/factory.rs`

**Interfaces:**
- Consumes:`StoragePluginFactory`、`StorageBackendConfig`（from bcs-storage-api）；`BaasConfig`、`BaasStoragePlugin`
- Produces:`BaasStoragePluginFactory`（`backend_name() == "baas"`），`build` 从 `cfg.backend` 取 `endpoint`/`tenant`/`share_link_ttl`/`health_probe_path`/auth headers，缺失 `endpoint`/`tenant` 返 `StoragePluginError::Build`
- 注：factory 的 `max_object_size` 要么取 `cfg.max_file_size`（简化、与 local 一致），要么构造期 probe baas —— **本 plan 简化为取 `cfg.max_file_size`**（baas 无硬上限、`max_size = min(max_file_size, max_object_size)`，两者相等即可；省一次 probe 网络调用，符合 capabilities 廉价无 IO 原则）

- [ ] **Step 1: 写测试**

Create `bcs-storage-baas/tests/factory.rs`：

```rust
use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginError, StoragePluginFactory};
use bcs_storage_baas::BaasStoragePluginFactory;
use serde_json::{json, Map};

fn cfg(backend: Map<String, serde_json::Value>) -> StorageBackendConfig {
    StorageBackendConfig {
        env: "prod".into(), max_file_size: 5_000_000_000, multipart_threshold: 100,
        share_link_ttl: 3600, bcs_base_url: "http://bcs".into(), bots_base_dir: "/x".into(),
        backend,
    }
}

#[tokio::test]
async fn builds_from_endpoint_tenant() {
    let mut m = Map::new();
    m.insert("endpoint".into(), json!("http://baas:8080"));
    m.insert("tenant".into(), json!("teamclaw"));
    let p = BaasStoragePluginFactory.build(&cfg(m)).await.unwrap();
    assert_eq!(p.backend_name(), "baas");
    assert_eq!(p.capabilities().supports_presign_put, true);
    assert_eq!(p.capabilities().max_object_size, 5_000_000_000);
}

#[tokio::test]
async fn errors_when_endpoint_missing() {
    let mut m = Map::new();
    m.insert("tenant".into(), json!("teamclaw"));
    let err = BaasStoragePluginFactory.build(&cfg(m)).await.unwrap_err();
    assert!(matches!(err, StoragePluginError::Build(_)));
}

#[tokio::test]
async fn errors_when_tenant_missing() {
    let mut m = Map::new();
    m.insert("endpoint".into(), json!("http://baas:8080"));
    let err = BaasStoragePluginFactory.build(&cfg(m)).await.unwrap_err();
    assert!(matches!(err, StoragePluginError::Build(_)));
}
```

- [ ] **Step 2: 实现 factory**

Create `bcs-storage-baas/src/factory.rs`（替换 lib.rs 里的占位 `pub mod factory;`）：

```rust
//! `StoragePluginFactory` for baas: parse endpoint/tenant/... from
//! `StorageBackendConfig.backend`, build `BaasStoragePlugin`.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginError, StoragePluginFactory};
use bcs_storage_api::StoragePlugin;
use serde_json::Value;

use crate::{config::BaasConfig, BaasStoragePlugin};

pub struct BaasStoragePluginFactory;

fn get_str(backend: &serde_json::Map<String, Value>, key: &str) -> Result<String, StoragePluginError> {
    backend.get(key)
        .and_then(|v| v.as_str().map(String::from))
        .filter(|s| !s.is_empty())
        .ok_or_else(|| StoragePluginError::Build(format!("baas: missing required config '{}'", key)))
}

#[async_trait]
impl StoragePluginFactory for BaasStoragePluginFactory {
    fn backend_name(&self) -> &'static str { "baas" }

    async fn build(&self, cfg: &StorageBackendConfig)
        -> Result<Arc<dyn StoragePlugin>, StoragePluginError>
    {
        let endpoint = get_str(&cfg.backend, "endpoint")?;
        let tenant = get_str(&cfg.backend, "tenant")?;
        let health_probe_path = cfg.backend.get("health_probe_path")
            .and_then(|v| v.as_str().map(String::from)).unwrap_or_default();
        // auth headers: read a `headers` sub-table ([session_files.backend.headers] key=val)
        let mut auth_headers = Vec::new();
        if let Some(Value::Object(h)) = cfg.backend.get("headers") {
            for (k, v) in h {
                if let Some(s) = v.as_str() {
                    auth_headers.push((k.clone(), s.to_string()));
                }
            }
        }
        let baas_cfg = BaasConfig {
            endpoint, tenant,
            share_link_ttl: cfg.share_link_ttl,
            health_probe_path,
            auth_headers,
            http_timeout: Duration::from_secs(30),
        };
        // max_object_size = cfg.max_file_size (baas 无硬上限；capabilities 廉价无 IO，不做 probe)
        Ok(Arc::new(BaasStoragePlugin::new(baas_cfg, cfg.max_file_size)))
    }
}
```

lib.rs 顶端确认 `pub mod factory;` + 导出：`pub use factory::BaasStoragePluginFactory;`
`config.rs` 的 `BaasConfig` 已有 `share_link_ttl` 字段。`BaasStoragePlugin::new` 签名 `(BaasConfig, u64)` 已在 Task 6 定。

- [ ] **Step 3: 跑测试 + 全 crate**

Run: `cargo test`
Expected: 全绿（含 factory 3 个）。

- [ ] **Step 4: Commit**

```bash
git add bcs-storage-baas/
git commit -m "feat(bcs-storage-baas): BaasStoragePluginFactory (validate endpoint/tenant)"
```

**Phase 2 完成检查点**：baas crate `cargo test` 全绿（health/prepare/complete_abort/presign/delete/error_map/factory）；`cargo build` 干净；trait 全方法实现。

---

## Phase 3：Wiring（把 baas crate 接入 BCS 组合根）

### Task 12: BCS 仓库引入 baas crate 依赖 + server.rs factory arm

**Files:**
- Modify: `src/bcs/Cargo.toml`（workspace members? baas crate 是独立的，不在 BCS workspace；用 path 依赖引入）
- Modify: `src/bcs/crates/bootstrap/bcs/Cargo.toml`（依赖 bcs-storage-baas）
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`（baas factory arm + 替换 Task 5 的 panic 占位）
- Test: `cargo build -p bcs` 编译通过 + 既有 server tests 绿

**Interfaces:**
- Consumes:`BaasStoragePluginFactory`（from bcs-storage-baas）
- 期望：装配 `storage_backend = "baas"` 时构造 `BaasStoragePlugin`；其他未知 backend panic

- [ ] **Step 1: 加 baas crate 依赖**

`src/bcs/crates/bootstrap/bcs/Cargo.toml` `[dependencies]` 加（path 按实际 baas crate 位置调）：

```toml
bcs-storage-baas = { path = "../../../../bcs-storage-baas" }  # 调整为实际相对路径
```

> **实现者注**：baas crate 独立 workspace 不在本 repo——path 依赖指向 repo 外。落地时若 baas crate 也纳入 BCS 仓库（作为子目录但独立 Cargo），则 path 是 crate 内相对路径，且需把它加入 BCS workspace `[workspace] members`? **不**——它有独立 Cargo.toml，不是 BCS workspace member，仅作为 path dep 被 bootstrap 引用。确认 `src/bcs/Cargo.toml` 的 `[workspace]` 不含 baas（否则 workspace 冲突）。

- [ ] **Step 2: server.rs baas factory arm**

`build_session_files_service` 的 match（Task 5 留的 `other => panic!(...)`）替换为：

```rust
    use bcs_storage_baas::BaasStoragePluginFactory;
    let factory: Arc<dyn StoragePluginFactory> = match config.session_files.storage_backend.as_str() {
        "local" => Arc::new(LocalStoragePluginFactory),
        "baas" => Arc::new(BaasStoragePluginFactory),
        other => panic!("unknown storage_backend '{other}'"),
    };
```

`StoragePluginFactory` 已在 Task 5 import。

- [ ] **Step 3: 编译 + 测试**

Run: `cargo build -p bcs`
Expected: 干净。若 baas crate path 依赖找不到，修路径。
Run: `cargo test -p bcs --lib`
Expected: 全绿（既有 wiring 测试——`storage_backend="local"` 默认仍走 local factory；若有 baas 装配测试需 baas 后端桩，本 plan 不加，留 e2e）。

- [ ] **Step 4: Commit**

```bash
git add src/bcs/crates/bootstrap/bcs/Cargo.toml src/bcs/crates/bootstrap/bcs/src/server.rs
git commit -m "feat(bcs-bootstrap): wire baas storage backend via BaasStoragePluginFactory"
```

---

### Task 13: 文档与示例配置（baas 配置块）

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-bcs-session-workspace-design-baas-plugin.md`（加「实现注记」：Ready handle 不瘦身 / session_id 从 key 解析 / operator 常量 / abort TRANSFER_STATE_CONFLICT→Ok / complete 不返 size 致 service 放宽）
- Modify: BCS 部署配置示例（若有，如 `config.example.toml` 或 README）—— 加 baas 配置块示例
- Test: 无（文档）

- [ ] **Step 1: 在 baas design 文档加「实现注记」节**

在 `docs/superpowers/specs/2026-07-20-bcs-session-workspace-design-baas-plugin.md` 「object_handle 字段定义」末尾加：

```markdown
> **实现注记（trait 缝隙，落地决策）**：
> 1. **Ready handle 不刻意瘦身**：设计上文写"Ready 瘦身到仅 transfer_id"，但 `StoragePlugin::complete_upload`
>    返回 `StorageObjectMeta`（不含 handle），service 层 `complete_upload`(bcs-session-file) 调完 plugin 后
>    原样透传 Pending 的 `backend_handle` 作 Ready handle。故 baas Ready 行的 `backend_handle` 实际仍含
>    `type`/`expires_at`（DIAG，无害）。`presign_get`/`delete` 解析时兼容 Pending/Ready 两种形态。
>    若要干净瘦身，需改 trait 让 `complete_upload` 返回新 handle —— 不在本期范围。
> 2. **session_id 从 key 解析**：`UploadPrepareRequest` 无独立 session_id 字段，baas 路径需 session_id。
>    插件按 `session-files/{env}/{session_id}/{file_id}/{file_name}` 从 `key` 第 3 段取 session_id。
>    若未来 key 格式变更或想消除脆弱性，可给 trait 方法加 session_id 入参（跨所有后端的 trait 改动）。
> 3. **operator 是常量 "bcs"**：baas prepare/share-link body 的 `operator` 设计要求 = BCS caller（human/bot id），
>    但 `StoragePlugin` trait 的 `prepare_upload`/`presign_get` 无 caller 参数，plugin 单例拿不到真实 caller。
>    落地用常量 `"bcs"`（仅能区分"BCS 服务发起"，放弃按上传者审计粒度）。按上传者审计需 trait 加 caller 入参，不在本期。
> 4. **complete 不返 size + service 放宽 size 校验**：baas `complete_upload` 返 `StorageObjectMeta { size: 0 }`
>    （baas complete 响应无 size）。service 层 `complete_upload` 有防御校验
>    `if meta.size != row.size && backend_handle 无 parts → Conflict`，会对 baas 单分片上传误拒。
>    已在 service 层放宽：`supports_presign_put` 后端跳过该 size 校验（OSS 已验证对象存在，size 校验无意义）。
>    见 plan Task 8.5。
> 5. **abort 对 TRANSFER_STATE_CONFLICT 当 Ok（幂等）**：abort 的 `DELETE /upload-url/{transfer_id}` 对已终态
>    ticket 调用会返 `TRANSFER_STATE_CONFLICT`(409) —— 插件将其当 `Ok`（abort 已终态 = 已取消/已 完成，
>    幂等成功）。这与通用错误映射表（TRANSFER_STATE_CONFLICT→Conflict）不同，是 abort 路径的特判。
>    delete 路径的幂等宽容则按 `TRANSFER_NOT_FOUND`(404) → Ok（已删）。两者各自特判，非通用规则。
```
>    若要干净瘦身，需改 trait 让 `complete_upload` 返回新 handle —— 不在本期范围。
> 2. **session_id 从 key 解析**：`UploadPrepareRequest` 无独立 session_id 字段，baas 路径需 session_id。
>    插件按 `session-files/{env}/{session_id}/{file_id}/{file_name}` 从 `key` 第 3 段取 session_id。
>    若未来 key 格式变更或想消除脆弱性，可给 trait 方法加 session_id 入参（跨所有后端的 trait 改动）。
```

- [ ] **Step 2: 加示例配置块**

在 BCS 部署文档/示例（找 `config.example.toml` 或 README；若无则在 design 文档「配置」节已含示例，确认它反映 `[session_files.backend]` 形态——design 文档 Task 已是 `[session_files]` 顶层 endpoint/tenant + `[session_files.backend]`。核对 design 的配置块与 config.rs(Task 3) 一致：
- design 写 `endpoint`/`tenant` 在 `[session_files]` 顶层 → 但 Task 3 把后端专属字段放 `[session_files.backend]` table。
- **修正**：design 配置块要么把 endpoint/tenant 移到 `[session_files.backend]`，要么 config.rs 兼容顶层 + backend table 两种来源。

**本 plan 选**：后端专属字段**全部**在 `[session_files.backend]`（与 Task 3 一致，backend-agnostic 透传）。design 的配置示例改为：

```toml
[session_files]
storage_backend = "baas"
multipart_threshold = 104857600
max_file_size = 5368709120
share_link_ttl = 3600

[session_files.backend]
endpoint = "http://baas.xxx.xxx:8080"
tenant = "teamclaw"
health_probe_path = ""
# [session_files.backend.headers]
# X-Baas-Token = "..."
```

在 design 文档更新该配置块（删顶层 endpoint/tenant 注释那版，改成 `[session_files.backend]`）。`share_link_ttl` 是顶层（Task 3 加的具名字段）。

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-bcs-session-workspace-design-baas-plugin.md
git commit -m "docs(baas-plugin): record trait-gap impl notes; config uses [session_files.backend]"
```

---

### Task 14: 全量验证 + e2e 烟测（baas 后端桩）

**Files:** 无（验证）

- [ ] **Step 1: BCS 全量构建 + 测试**

Run: `cargo build --workspace`（BCS repo）→ 干净。
Run: `cargo test --workspace`（BCS repo）→ 全绿。
Run: `cargo test`（baas crate）→ 全绿。

- [ ] **Step 2: baas 后端装配烟测（本地起 baas wiremock + BCS）**

可选但推荐：写一个 bcs e2e 脚本，起一个 wiremock 桩 baas（按 v1.1 响应），起 BCS 配 `storage_backend="baas"` 指向桩，跑一次 prepare→PUT→complete→download（302 到桩 share_url）→delete 往返，断言全绿。若 e2e 基建方便则加；否则记为「baas 真实 e2e 待 baas 服务可用后补」。

Run（示例，若有 e2e 脚手架）: `./scripts/test.sh baas_session_files` 或等价。

- [ ] **Step 3: 确认前置 Phase 1 改动无回归**

核:
- local 后端经 factory 装配仍正常（不依赖 baas）。
- `download_route` 默认 TTL 现为 `share_link_ttl`（3600），既有 download 测试断言更新（Task 4 已处理）。
- config `data_dir` 从 `[session_files.backend]` 读，既有用 `data_dir` 的测试 fixture 已迁（Task 5 Step 4）。

- [ ] **Step 4: 最终 Commit（若有 e2e 脚本）**

```bash
git add <e2e script if any>
git commit -m "test(bcs): baas session-file e2e smoke (wiremock stub)"
```

---

## Self-Review 记录（写完后跑的检查）

**1. Spec 覆盖**：对照 design-baas-plugin 各节——
- 「baas 模型概述 / 路径」→ Task 6 `base_for_session` + percent_encode + encode 测试覆盖 ✅
- 「prepare_upload 映射」→ Task 7 ✅
- 「complete/abort」→ Task 8 ✅
- 「presign_get share-link」→ Task 9 ✅
- 「delete transfer_id」→ Task 10 ✅
- 「错误映射表」→ Task 10 error_map 测试 ✅
- 「身份/租户 endpoint=host + tenant config」→ Task 11 factory + config ✅
- 「孤儿清理」→ 实现为 baas 无 staging 枚举，BCS 不做孤儿 sweep（现状 `delete_all_for_session` 按行 transfer_id 删，正常路径覆盖）—— **无需新代码**，但需确认 `delete_all_for_session`(service.rs) 调 `delete`(plugin) 用的是 Ready handle 含 transfer_id。✅（service 层 delete_all 对 Ready 行调 storage.delete(StorageHandle {backend_handle})）
- 「配置 endpoint=host only+tenant」→ Task 11/13 ✅
- 「落地前置 factory」→ Task 1/2/3/4/5 ✅
- 「wiring」→ Task 12 ✅
- health_check → Task 6 ✅
- object_handle 形态 → Task 7（pending）+ Task 9/10（兼容 ready）✅

**2. 占位符扫描**：无 TBD/TODO（除 `unimplemented!` 是有意的 task-stub，每个都被后续 task 实现）。`parse_iso_to_unix` Task 7 临时 None、Task 9 正式实现 —— 已标注，非遗留。

**3. 类型一致性**：`BaasPendingHandle`/`BaasReadyHandle` 各 task 引用一致；`session_id_from_key` 签名一致；`StorageBackendConfig` 字段（Task 1 定义 `share_link_ttl`/`backend`/`bots_base_dir`/`env`/`max_file_size`/`multipart_threshold`/`bcs_base_url`）在 Task 2/5/11 使用处一致；`StoragePluginError::Build` 一致。`BaasStoragePlugin::new(BaasConfig, u64)` Task 6 定义、Task 11 调用一致。

**4. 已知服务/缺口（已在 plan 显式标注，非计划遗漏）**：
- 🔴 P1-A baas complete 返 size:0 被 service size 校验拒 → **Task 8.5** 在 service 层为 presign_put 后端跳过 size 校验（含单测 `complete_presign_backend_single_skips_size_mismatch_check`）
- 🔴 P1-B session_id percent-encode 策略与 mock 一致性 → Task 6 `percent_encode_path` 用 block-set（编码 `:` 保留 `_`）+ 内联单测 + Task 7 colon 测试 mock 对齐
- Ready handle 不瘦身（trait 限制）→ Task 13 注记
- session_id 从 key 解析（trait 缺 session_id 入参）→ Task 7 + Task 13 注记
- operator 是常量 "bcs"（trait 缺 caller 入参，P2-D）→ Task 7 注记 + Task 13 注记（放弃按上传者审计）
- 🔴 P2-E 分享/会话内下载忽略 q.ttl → **Task 4.1** 改 `shared_file_content`/`download_content` 传 None（统一 share_link_ttl）
- P2-C factory build 用 tokio::fs（非阻塞）→ Task 2 已用 `tokio::fs::create_dir_all`
- P3-G abort 对 TRANSFER_STATE_CONFLICT 当 Ok（特判，非通用映射）→ Task 8 实现 + Task 13 注记 5
- 422/409 归并到 409（baas INVALID_TRANSITION 422 → Conflict → BCS INVALID_TRANSITION 409）→ 错误映射表已含，自洽
- health_check 401/404/405 接受为可达 —— review 时标记的潜在 SPOF 监控弱点，plan 按设计实现（接受 4xx = 可达），落地后若要凭 401 告警凭证失效，再迭代