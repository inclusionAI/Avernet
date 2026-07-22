# BCS 会话工作区 — 共享文件框架（设计）

**日期：** 2026-07-20
**状态：** 评审稿
**作者：** zhangwu.zh
**范围：** v1 — 框架（`StoragePlugin` trait + HTTP/CLI 接口 + `SessionFileService`）+ 本地文件系统
后端（`bcs-storage-local`）；分段（multipart）上传在 v1 即支持（baas 默认 100MB 阈值自动分流，
超 100MB 必须能传，否则 baas 后端不可用）。baas 后端实现见独立文档
`2026-07-20-bcs-session-workspace-design-baas-plugin.md`（该插件 crate 不在当前仓库，基于本文档
定义的 trait 接口与 baas HTTP API 实现）。

## 目标

为每个 BCS 会话提供共享文件区（"session workspace"），会话中的 bot 和 human 都可以
在其中上传、下载、列出和删除文件。BCS 拥有该框架，对外提供接口（HTTP + CLI），
对内提供可插拔的存储接口（`StoragePlugin` trait），使不同的文件存储后端（本地文件系统 /
baas / OSS / NAS / 三方服务）只需实现该 trait 即可接入。

本文档覆盖：框架本身、对外 HTTP/CLI 接口、`StoragePlugin` trait 契约、本地文件系统后端实现。
baas 后端的实现细节见 `2026-07-20-bcs-session-workspace-design-baas-plugin.md`。

## 参考资料

- BCS 架构与分层：`src/bcs/CLAUDE.md`、`src/bcs/AGENTS.md`
- 需对标的现有插件模式：`bcs-db-api`（`DbPlugin`）、`bcs-cache-api`（`CachePlugin`）
- 对外 HTTP/CLI 与 `StoragePlugin` trait 的完整契约：`2026-07-20-bcs-session-workspace-api.md`
- baas 后端实现：`2026-07-20-bcs-session-workspace-design-baas-plugin.md`

## 非目标（v1）

- OSS 与 NAS 后端（trait 已支持，v1 不实现）
- baas 后端在当前仓库内的实现（见独立 baas plugin 设计文档，crate 独立于本仓库）
- 文档约定的孤儿/TTL 清理钩子以外的内置清理器
- 目录/层级语义（key 是扁平的，list 仅按前缀过滤）

> 注：分段（multipart）上传**在 v1 即支持**（不是非目标）。单片上限 100 MB 是**分段阈值**，
> 超过即自动走 multipart，由 `StoragePlugin` 在后端组装（baas 用 baas MULTIPART、本地按序拼接段
> 文件）。v1 的 `max_file_size` 不再是 100 MB 硬截断，而是按后端可处理上限配置（baas 无硬上限、
> 本地取保守上限），100 MB 仅是单片/分段的分界。

## 关键决策（brainstorming 阶段已锁定）

| 决策项 | 选择 |
|---|---|
| 字节流路径 | **上传 capability-hybrid：** 客户端始终 `POST /files`(prepare)→PUT 到 BCS 返回的 `upload_url`→`POST /complete`，协议统一；但 `upload_url` 指向随后端能力而异 —— presign 后端（baas/OSS，`supports_presign_put=true`）返回后端真直传 URL，客户端 PUT **直连后端、字节不经 BCS**；本地后端（`supports_presign_put=false`）返回 BCS 代理 `PUT .../content`，字节经 BCS → `stream_upload`。下载侧同理：预签名后端 302 直连签名 URL（字节不经 BCS），本地后端流式返回。 |
| 删除权限 | **上传者 + 会话创建者/驱动 bot。** 文件上传者（human 上传者、上传该文件的 bot、或拥有该 bot 的 human），或会话创建者 / 该 group 的 driver bot 可删除。其余通过会话成员校验的人可上传/下载/列出。 |
| v1 范围 | **框架 + 本地文件系统后端（本仓库内）。** baas 后端作为独立插件 crate 设计于配套文档，后续接入。OSS/NAS 延后（通过 trait 即可接入）。 |
| 文件生命周期 | **仅在会话删除时自动清理。** 会话完成不删除文件。v1 单片/分段阈值 ~100 MB，超限自动 multipart。 |

## 架构与 crate 布局

新增代码完全遵循现有 `DbPlugin` / `CachePlugin` 模式（trait crate 在
`crates/plugin-api/` 下，后端 crate 在 `crates/plugins/` 下）。**以下路径均相对 `src/bcs/` 目录**
（仓库实际 layout 为 `src/bcs/crates/...`），不要建到仓库根目录：

```
crates/contracts/bcs-domain/src/session_file.rs        # SessionFile 领域类型
crates/contracts/bcs-domain/src/share.rs               # ShareTokenPayload + share_token_encode/decode_and_verify（对标 invite/register）
crates/contracts/bcs-protocol/src/session_file.rs      # wire DTO（HTTP 请求/响应）

crates/plugin-api/bcs-storage-api/                     # StoragePlugin trait + 错误 + 类型 + 契约测试 + FakeStoragePlugin
crates/plugins/bcs-storage-local/                      # 本地文件系统后端（开发 / 单节点）
# crates/plugins/bcs-storage-baas/                     # baas 后端 —— 见 design-baas-plugin.md，crate 独立于本仓库

crates/service-api/src/application/session_files.rs    # SessionFileService 用例
crates/service-api/src/port/repo/session_file.rs       # SessionFileRepo 端口（元数据）

crates/services/bcs-session-file/                      # SessionFileService 核心实现
crates/services/bcs-session-file-store/                # SessionFileRepo 的 MySQL + memory 实现
crates/services/bcs-session/ (扩展)                    # 删除会话时的清理钩子

crates/adapters/http/bcs-http/src/routes/session_files.rs   # HTTP handler
crates/tools/bcs-cli/src/main.rs + client.rs           # CLI 子命令

migrations/mysql/006_session_files.sql                 # bcs_session_files 表
```

分层与 `CLAUDE.md` 一致：`contracts` -> `service-api`(application/core/port)
-> `services`(+`*-store`) -> `adapters`，插件位于 `core` 之下。
`SessionFileService` 持有 `Arc<dyn SessionFileRepo>`、`Arc<dyn StoragePlugin>`，
以及对现有 session 查找 service 的引用。组装根（`crates/bootstrap/bcs/server.rs`）按
配置装配选定的存储插件（v1 默认 `local`；`baas` 装配见 baas plugin 文档）。

## 领域与数据模型

### `SessionFile`（领域类型，`bcs-domain`）

```rust
pub struct SessionFile {
    pub file_id: String,          // 全局唯一 ULID（见「file_id 生成与唯一性」节），URL-safe、不透明
    pub session_id: String,
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,
    pub sha256: Option<String>,
    pub owner: ActorRef,          // { actor_kind: Bot|Human, actor_id }
    pub storage_backend: String,  // "local" | "baas" | ...
    pub object_handle: String,    // backend-specific: local path | baas transfer_id / OSS key
    pub status: FileStatus,       // Pending | Ready | Deleting | Failed
    pub created_at: u64,
    pub updated_at: u64,
}
```

`ActorRef` 复用 `bcs-domain::group` 中已有的 `ActorKind::{Bot, Human}` + `actor_id`。

### `bcs_session_files` 表（迁移 `006`）

字段：`env`、`file_id`（PK）、`session_id`、`owner_actor_kind`、`owner_actor_id`、
`file_name`、`mime_type`、`size BIGINT`、`sha256 CHAR(64)`、`storage_backend`、
`object_handle`、`status`、`created_at`、`gmt_create`、`gmt_modified`。
索引：`(env, session_id)`，唯一索引 `(env, session_id, file_id)`。
遵循现有 `env` + `gmt_create/gmt_modified` + utf8mb4 约定。

### `file_id` 生成与唯一性

BCS 为每个文件分配一个**全局唯一**的 `file_id`，作为客户端寻址与 share token payload 的唯一键。
`file_id` 采用 **ULID**（与现有 `01HZX...` 示例一致：URL-safe、不透明、时间有序、26 字符 Crockford base32），
由 BCS 在 `prepare_upload` 时生成，写入 `bcs_session_files.file_id`（PK）与 `(env, session_id, file_id)` 唯一索引。

**不采用 `session_id + filename hash` 派生 `file_id`**，原因：
- 本框架允许同一会话存在多个同名文件（§1.6：唯一索引在 `(env, session_id, file_id)`，不在 `file_name`），
  纯 `session_id + filename hash` 会在同名文件上冲突，破坏唯一性；
- share token payload 仅含 `{ file_id, exp, version }`（见「分享链接」），`file_id` 必须能唯一寻址一行，
  可派生/可预测的 id 会削弱这一保证。

会话/文件名归属信息体现在 DB 行与 object key（`session-files/{env}/{session_id}/{file_id}/{file_name}`，
见「key 派生约定」），而非 `file_id` 本身——`file_id` 对客户端不透明，仅用于寻址。

**BCS 始终以本表为文件列表的权威来源** —— 永不从后端拉取。这样无论后端如何
（baas 列表是按 bot 命名空间、扁平、异步的），list/metadata 都保持快速、统一、可过滤。

## 对内可插拔接口：`StoragePlugin`

详细 API（含全部辅助类型、错误、契约测试、`UploadHandle`/`StorageHandle` 形态）见配套文档
`2026-07-20-bcs-session-workspace-api.md` §3。上传侧为统一的三阶段 trait（与 BCS 三阶段 HTTP
流程一一对应），下载侧保留 `get_stream` / `presign_get`：

```rust
#[async_trait]
pub trait StoragePlugin: Send + Sync + 'static {
    fn backend_name(&self) -> &'static str;
    fn capabilities(&self) -> StorageCapabilities;   // { supports_presign_put, supports_presign_download, ... }

    // --- 上传：BCS 三阶段，plugin 决定具体实现 -----------------------------
    // prepare 返回 PreparedUpload{ handle, client_target, expires_at }：client_target
    // 告诉 BCS 该把哪个 URL 给客户端（presign 后端直传 URL / local 走 BCS 代理），
    // handle 持久化到 object_handle 供 complete/abort 重建。完整契约见 api.md §3。
    async fn prepare_upload(&self, req: UploadPrepareRequest) -> Result<PreparedUpload, StorageError>;
    // part_number: 单片恒 None；分段传对应编号（1-based）。v1 即支持分段。
    async fn stream_upload(&self, handle: &UploadHandle, part_number: Option<u16>, body: ByteStream) -> Result<(), StorageError>;
    async fn complete_upload(&self, handle: &UploadHandle) -> Result<StorageObjectMeta, StorageError>;
    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError>;

    // --- 下载 ---------------------------------------------------------------
    async fn get_stream(&self, handle: &StorageHandle) -> Result<ByteStream, StorageError>;
    async fn presign_get(&self, handle: &StorageHandle, ttl_secs: u64) -> Result<PresignGetTicket, StorageError>;
    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError>;

    async fn health_check(&self) -> Result<StorageHealth, StorageError>;
}
```

`UploadHandle` 是后端特定的、可序列化句柄，由 BCS 在 `prepare_upload` 后持久化到
`SessionFile.object_handle` 列，供跨 HTTP 请求的 `stream_upload`/`complete_upload`/
`abort_upload`/`delete` 重建使用（HTTP 无状态跨请求）。`StorageError { InvalidInput, NotFound,
Conflict, Unsupported, Backend }` 对标 `DbError`；lint 禁止泄漏后端细节。

`capabilities()` 必须廉价、同步、无 IO，返回构造期预计算值（baas 等 probe 仅在插件 `async fn new()` 构造期执行，不在 `capabilities()` 内做阻塞 IO）；其返回值同时驱动上传与下载的字节路由：

- **上传** `supports_presign_put`：true（baas/OSS）→ `prepare_upload` 返回后端真直传 URL，客户端
  PUT 直连后端、字节不经 BCS，`stream_upload` 不被调用；false（local）→ BCS 用 `PUT .../content`
  代理 + `stream_upload` 接收字节。
- **下载** `supports_presign_download`：true → BCS 对 `GET .../content` 返 302 到 `presign_get`
  签名 URL（字节不经 BCS）；false → BCS 用 `get_stream` 流式返回 body。

客户端上传/下载协议在两种能力下完全一致（始终 PUT/GET BCS 返回的 URL），只是该 URL 指向后端还是
BCS、字节是否经 BCS，客户端不感知。

**本地后端（`bcs-storage-local`）实现要点：**

- `supports_presign_put = false`、`supports_presign_download = false`；`max_object_size` 取**配置项**（不以动态磁盘剩余空间
  作为静态 capability，剩余空间在 `stream_upload` 中实际校验）。上传字节经 BCS 代理（`stream_upload`）。
- `prepare_upload`：返回 `client_target = ProxyViaBcs`（BCS 据此把 `PUT {bcs_base}/.../content` 作为
  客户端 `upload_url`）。单片开一个临时文件（`UploadHandle.backend_handle` 含
  `{ temp_path, final_path }`）；**分段**（size ≥ 100 MB）`backend_handle` 含
  `{ final_path, parts: [{ part_number, temp_path }] }`。`temp_path` 必须包含 `file_id`（key 中
  已含）并附加随机后缀，分段时各段路径含 `part_number`，保证多客户端/多 worker 同 key 并发不冲突
  （单片 `{data_dir}/{key}.{rand}.part` / 分段 `{data_dir}/{key}.p{part_number}.{rand}.part`）。
- `stream_upload`：单片写入唯一 temp 文件；分段（`part_number=Some(n)`）写入对应 `parts[n].temp_path`，
  校验累计 size ≤ prepare size。v1 即支持分段。
- `complete_upload`：单片 fsync + 原子改名到 `final_path`；分段按 `part_number` 顺序拼接各段到
  `final_path`（逐段 fsync，最后原子改名保证可见性），返回 `StorageObjectMeta`。
- `abort_upload`：unlink temp（单片）/ 所有分片段（分段），幂等。
- `get_stream`：打开终态文件流式返回；`presign_get` 返回 `StorageError::Unsupported`；
  `delete`：unlink 终态文件（幂等）。
- key 映射到 `$BCS_DATA_DIR/session-files/...`（或配置的 `data_dir`）下，扁平，无层级语义。
- 用于开发、测试和单节点部署。

baas 后端 `UploadHandle` 形态、方法→baas HTTP 映射、错误映射等见
`design-baas-plugin.md`。

## 对外 HTTP API

在 `router.rs` 中注册的会话级路由，复用 `State<HttpAppState>`、
`resolve_group_chat_caller` 和现有会话成员校验。完整请求/响应契约见配套 API 文档。

**上传走 BCS 三阶段（prepare → PUT → complete）+ 取消端点，客户端协议统一**；`upload_url` 指向随后端
`supports_presign_put` 而异，客户端不感知、不需要知道后端是什么：

1. `POST /sessions/{sid}/files`（JSON `{file_name, size, mime_type}`）→ 创建 `Pending` 的
   `SessionFile` 行，返回 `upload_url` + `file_id`。`upload_url`：presign 后端（baas/OSS）为后端**真直传
   URL**；本地后端为 BCS 自有的 `PUT .../content`（字节经 BCS）。
2. 客户端 PUT 字节到该 `upload_url`：presign 后端**直连后端、字节不经 BCS**；本地后端经 BCS →
   `StoragePlugin::stream_upload` 落盘。
3. `POST /sessions/{sid}/files/{file_id}/complete` 完成（本地 finalize；baas 调 complete + 轮询到
   `DONE`）。任意时刻可用 `DELETE /sessions/{sid}/files/{file_id}` 取消（`Pending`/`Failed` 时为
   abort，`Ready` 时为删除对象 + 行）。

对外上传语义统一为三阶段 + 取消；`presign_put` 的 direct/proxy 字节路径之分对客户端不可见（始终 PUT
BCS 返回的 URL）。下游 `StoragePlugin` 由 `prepare_upload` 返回 `PreparedUpload.client_target` 告诉 BCS
该把哪个 URL 给客户端、字节是否经 BCS（presign 后端 `stream_upload` 不被调用）。
下载侧同理（预签名后端 302 直连签名 URL / 本地流式返回 body）。

> 客户端直传后端（presign）要求客户端网络可达后端（baas「第四通道」前提）；仅能连 BCS 的客户端用
> local 后端。跨主机 PUT 时客户端剥离 `Authorization`，后端预签名 URL 自带签名。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/sessions/{sid}/files` | **发起上传**（JSON），返回 `upload_url`（presign 后端为后端真直传 URL，本地为 BCS 代理） + `file_id`，行置 `Pending`。 |
| `PUT`  | `/sessions/{sid}/files/{file_id}/content` | **上传字节**（仅本地后端经 BCS；presign 后端不走此端点，客户端直传后端）。与下载 `GET .../content` 同路径不同方法。 |
| `POST` | `/sessions/{sid}/files/{file_id}/complete` | **完成上传**（后端 finalize / complete + 轮询）。 |
| `GET`  | `/sessions/{sid}/files` | **列文件**（分页：prefix、limit、marker）。 |
| `GET`  | `/sessions/{sid}/files/capabilities` | **后端能力**（`{storage, presign_upload, presign_download, max_size}`），可选，供客户端预判上传/下载字节是否直连后端。 |
| `GET`  | `/sessions/{sid}/files/{file_id}` | **文件元数据**（`SessionFile`）。 |
| `GET`  | `/sessions/{sid}/files/{file_id}/content` | **下载字节** —— 预签名后端 302 跳转到签名 URL；本地后端流式返回 body。 |
| `DELETE` | `/sessions/{sid}/files/{file_id}` | **取消上传**（`Pending`/`Failed`）或 **删除文件**（`Ready`）。 |
| `POST` | `/sessions/{sid}/files/{file_id}/share` | **生成分享链接**（需 `Ready`），返回 `share_url` + `share_token` + `expires_at`。 |
| `GET`  | `/sessions/{sid}/shared-file?token={token}` | **分享文件元信息**（无鉴权，仅校验 token + 过期 + `sid` 一致）。 |
| `GET`  | `/sessions/{sid}/shared-file/content?token={token}` | **分享下载字节**（无鉴权，字节路由同 `GET .../content`）。 |

> 路由注册：`/files/capabilities`（静态段）与 `/files/{file_id}`（参数段）同层，`router.rs` 须确保静态段优先匹配（axum matchit 默认静态优先），并在启动期加测试验证 `GET /files/capabilities` 不被当成 `file_id="capabilities"`。`PUT`/`GET` 同路径 `.../content` 靠方法区分。

> 分享链接详情见 API 文档 §1.9。

v1 **分段阈值 ~100 MB**：`size < 100 MB` 走单片（`mode: "single"`），**`size ≥ 100 MB` 自动走
分段**（`mode: "multipart"`，prepare 一次返回所有分片 `upload_url`，由 `complete_upload` 在后端
组装：baas 用 baas MULTIPART `list_parts` + 组装、本地按 `part_number` 顺序拼接段文件）。v1 即
支持分段（baas 默认 100MB 阈值自动分流，超 100MB 必须可传）。`max_file_size` 不再是 100MB 硬截断，
而是按后端可处理上限配置（详见配置）；超 `max_size` 才 `413`。响应形态见 API 文档 §1.2.a/§1.2.b。

## 对外 CLI API

扩展 `SessionCommands`（clap-derive）。完整参数见配套 API 文档。**上传只暴露 `upload` 一个
子命令**，内部自动串联三阶段（`POST /files` prepare → PUT 字节 → `POST /complete`），对用户
保持单入口、单参数（只需本地文件路径）；需要分阶段控制的脚本可直接走 HTTP 三阶段接口。

```
bcs session file upload       --session <sid> --path <local> [--mime] [--name]   # 三阶段，唯一上传入口
bcs session file list         --session <sid> [--prefix] [--limit] [--marker]
bcs session file download     --session <sid> --file-id <> [--out <path>] [--ttl <seconds>]
bcs session file delete       --session <sid> --file-id <>                        # 删除 / 取消
bcs session file share        --session <sid> --file-id <> [--ttl <seconds>]      # 生成分享链接
bcs session file capabilities --session <sid>
```

`BcsClient` 新增三阶段上传的 `prepare`/`put-stream`/`complete` 助手方法（仅 `upload` 子命令内部
使用），字节流式 PUT。默认 JSON 输出；`--no-json` 为人读，对齐现有 CLI 约定。

## 鉴权与权限

- **Caller 解析：** 每个 route handler 复用 `resolve_group_chat_caller`
  -> `GroupChatCaller::Bot{bot_uuid}` 或 `GroupChatCaller::Human{...}`
  -> 构造 `ActorRef`。
- **成员校验：** human 的 `actor_id` 是会话参与者，或拥有作为参与者的 bot，即通过
  （复用 `human_has_group_access` 风格逻辑）。bot 通过自身 token + 会话参与者身份通过。
  upload/download/list 均需成员校验。
- **删除：** `owner == caller`（human 上传者；若上传者是 bot 则需拥有该 bot），或 caller
  是会话创建者 / 拥有该 group 的 driver bot。对标现有 `delete_session` 规则。
- 不在 BCS 现有能力之外新增 RBAC 层。

## 生命周期与清理

- **上传：** `prepare_upload` 创建 `Pending` 行（`object_handle` = `UploadHandle` 序列化）；
  `stream_upload` 接收字节；`complete_upload` 后置 `Ready`（`object_handle` 转 `StorageHandle`）。
- **`expires_at` / Pending 超时：** prepare 返回的 `expires_at` = BCS 给出的上传链接/句柄过期时间
  （取 `ttl_secs` 配置与后端签发的 URL 过期时间的**更早者**）。客户端须在该时间前完成 PUT+complete；
  **超时未 `complete` 的 `Pending` 文件由后台 sweep 转为 `Failed` 并调用 `abort_upload` 清理后端**。
  转为 `Failed` 后客户端若再 `complete` 收到 `INVALID_TRANSITION`（409）。**v1 包含一个最小的 `Pending` 超时 sweep**（粗粒度定时器：扫描 `Pending` 且 `expires_at` 已过期的行 → 转 `Failed` + `abort_upload`），作为兜底；前端按 `Failed` 可 `DELETE` 清理重传。
- **删除/取消：** `Pending` 与 `Failed` 都走 `abort_upload`、`Ready` 走 `delete`（`Failed` 本质尚未
  完成上传，按 `Pending` 处理，清理后端 staging/临时段再删行，不走 `delete`/`DELETE /staging`
  以免后端找不到 staging 对象）。统一先删后端对象，再删元数据行。先后端再行（已删行对应的孤儿
  对象比「幽灵行」更安全，由 sweep 对账两者）。HTTP `DELETE` 端点在 **BCS 元数据层幂等**：
  行存在则按 `status` 走 `delete`/`abort_upload`（后端对象已不存在时插件返 `Ok`）删行后返 204；
  **行已不存在时直接返 204，不探测后端** —— `object_handle` 随 DB 行一同消失，无 handle 无法重建
  `StorageHandle`/`UploadHandle`，无法调用后端 `delete`/`abort_upload`（不做无法实现的假探测）；
  此时若后端仍有残留对象，由 orphan sweep 收敛。不引入 tombstone/软删除表。
- **删除会话钩子：** `delete_session` 执行时调用
  `SessionFileService::delete_all_for_session(sid)` -> 遍历文件 ->
  逐个 `StoragePlugin::delete` -> 删行。会话**完成**不触发此钩子；完成会话的文件仍可下载。
  **部分失败语义：单个文件后端删除失败不中断会话删除**——记录失败行（保留元数据 + 留孤儿对象）交孤儿对象对账 sweep，继续删其余文件，会话删除仍成功返回；避免 N 次顺序后端调用阻塞或半删回滚。
  v1 先同步逐个删除保正确；后续若会话文件量大可改为「先 mark `Deleting`，后台批量 + 异步 sweep」，
  与 `Deleting` 作为内部瞬态的定义一致（非 v1 阻塞）。
- **`Pending` 超时 sweep（v1）：** 粗粒度定时器扫描 `Pending` 且 `expires_at` 已过期的元数据行，转 `Failed` + `abort_upload`，防止 abandoned 会话的 staging/临时段长期泄漏。
- **孤儿对象对账（v1 之后）：** 解耦定时器清理后端孤儿对象（无对应元数据行的对象，如 `delete_session` 钩子中后端删除失败留下的残留），与 `Pending` 超时 sweep 解耦。

## 测试

- **契约测试 + in-memory fake**（`bcs-storage-api`，对标 `bcs-test-support`）：除每个
  `StoragePlugin` 实现跑同一套契约用例外，crate 内**同步提供一个 `FakeStoragePlugin`**（in-memory
  实现，覆盖三阶段上传 / 下载 / 删除 / 幂等 / 分段路径）。现有 `DbPlugin`/`CachePlugin` 都通过
  fake 注入测试，`StoragePlugin` 同此模式 —— 各层（service 单测、HTTP 层）复用同一 fake，避免
  重复手写 mock、避免遗漏 multipart 路径。本地文件系统须通过同一契约测试。
- **单元测试**（`SessionFileService`）：能力路由、鉴权决策（上传者 vs 创建者）、生命周期钩子。
- **memory store**：对标现有 `*-store/src/memory.rs` 实现 `SessionFileRepo::memory`，
  用于无 MySQL 的快速 HTTP 层测试。
- **HTTP 层测试：** 三阶段上传（prepare/stream/complete）、`abort`/删除分流（含 `Failed`→abort
  分支）、list 分页、预签名下载 302 跳转流、delete 403/204 + 幂等 204、share 链接生成与无鉴权下载
  （过期 → 410、删文件 → 404、`sid` 不一致 → 404）、分段缺段 `complete` → 409、`/files/capabilities` 与 `/files/{file_id}` 路由不冲突。
- **跨主机凭证隔离回归测试：** 预签名后端下断言 `GET .../content` 的 302 目标与 `upload_url` 直传目标 host ≠ BCS 时，`bcs-cli`/BCS HTTP 客户端发出的请求不含 `Authorization` Bearer（显式 `RedirectPolicy` 剥离，不依赖默认）。
- baas 后端的契约/integration 测试见 `design-baas-plugin.md`。

## 分享链接（share link）

为 `Ready` 的文件生成**不校验会话权限**的分享链接：拿到链接者凭 token 即可下载/查元信息，过期失效。
完整 HTTP/CLI 契约见 API 文档 §1.9 / §2.5。

### token 与签名

- 无状态签名 token，复用 BCS 现有 invite/register 的 **HMAC-SHA256(JSON payload) + base64url-no-pad**
  方案，但**使用独立密钥** `[session_files.share] token_secret`（**不复用 invite 密钥**），与
  invite/register 互相不可伪造。
- 新增 `crates/contracts/bcs-domain/src/share.rs`（对标 `invite.rs`/`register.rs`，复制+改名）：
  ```rust
  /// share token payload —— 仅 { version(v), file_id, exp }；不含 session_id
  /// （file_id 全局唯一，消费端凭 file_id 查行取得 session_id 与路径 {sid} 比对）
  #[derive(Serialize, Deserialize)]
  pub struct ShareTokenPayload { pub v: u8, pub file_id: String, pub exp: u64 }
  pub fn share_token_encode(&ShareTokenPayload, secret: &[u8]) -> String;
  pub fn share_token_decode_and_verify(token: &str, secret: &[u8])
      -> Result<ShareTokenPayload, ShareTokenError>;  // 验签 + 过期 + 版本
  ```
  经 `bcs-service-api` re-export（与 invite/register 同款）。

### 架构与鉴权模型

- **mint（`POST .../files/{file_id}/share`）**:权限同 `DELETE`（上传者/创建者/driver），文件需
  `Ready`（否则 `422`）。构造 `{v:1, file_id, exp}` → encode → 拼成
  `{share_base_url或请求host}/sessions/{sid}/shared-file/content?token={token}` 返回。无 DB 写入
  （纯无状态），无活跃分享列表。
- **消费（`GET .../shared-file` / `.../shared-file/content?token=`）**:权限 = **无**。流程：
  `share_token_decode_and_verify` → 解出 `file_id` → 查 `bcs_session_files` 行 → 若行 `session_id` ≠
  路径 `{sid}` 返 `404`（路径混淆保护）→ 跳过会话成员鉴权 → `Ready` 校验 →
  元信息候选端返回（`object_handle` 不透出客户端，参见 API 文档通用约定）；下载端复用 `GET .../content` 字节路由
  （预签名后端 302 到 `StoragePlugin::presign_get`，**预签名 URL 有效期取 token 过期与后端 TTL 的
  更早者**；本地后端 `get_stream` 流式返回）。
- 不新增 `StoragePlugin` 方法、不新增 DB 表，分享功能纯 token + routing。
- `{sid}` 在消费端仅作路径命名空间与一致性校验，**不参与鉴权**，且分享消费者无需知道也不依赖 `sid`
  （`share_url` 自带 `sid` 与 `token`）。

### 生命周期与安全

- **不可撤销**：无状态 token 在自然过期前一直有效；撤销方式 = 删除文件（删后 token 验证查行失败 →
  `404`）。无 tombstone/分享表（已在 brainstorming 阶段明确接受此权衡：分享链接的生命周期短、场景
  受控，不引入额外 schema）。
- **secret 与重启**：`token_secret` 未配时启动告警 + 随机 32B 密钥（进程重启即失效，旧 share token
  全部无法验证），与 invite 一致；生产**必须**显式配置固定密钥。
- 预签名后端（baas）下，分享下载经 BCS 302 到 OSS 预签名 URL，token 验签在 BCS 内完成，OSS 不参与
  轻鉴权 —— 即分享权限边界完全由 BCS 的 token 签名 + 过期决定，与 baas `share-link` 无直接耦合
  （baas `presign_get` 仅提供短期字节 URL，BCS 把它的 TTL 收敛到 ≤ token 过期）。

## bcs-coordination skill 更新

`bcs-cli` 内置的 `bcs-coordination` skill（`crates/tools/bcs-cli/bcs-coordination/`）是 bot/agent
使用 BCS 的操作入口（`SKILL.md` + `references/<场景>.md`）。会话工作区新增文件能力后，须同步更新该
skill，使 bot 能用 CLI 上传/下载/分享/列举会话文件，与现有 `session` reference（`references/session.md`）
能力并列。

### 新增 reference：`references/session-file.md`

对标 `session.md` 的体例（概念 → 命令清单表 → 每命令示例 → 返回结果汇总），覆盖 CLI §2 的全部子命令：

- **概念**：会话工作区（session workspace）= 一个 Session 内 bot/human 共享的文件区；`file_id` 唯一、
  允许同名；`FileStatus`（`Pending`/`Ready`/`Failed`）；`Ready` 才可下载/分享/删除。
- **权限**：会话参与者可 upload/download/list/share；删除/取消限上传者或会话创建者/driver bot。
- **命令清单表**（与 CLI §2 一致）：`session file upload` / `list` / `download` / `delete` / `share` /
  `capabilities`，每条标必需参数与说明。
- **`session file upload`**：三阶段一次性封装（`POST /files` prepare → PUT `upload_url` → `POST /complete`）；
  说明 `upload_url` 指向随后端能力（presign 后端直传后端、字节不经 BCS；local 经 BCS），失败会尝试 `DELETE`
  取消。给小文件与大文件（≥100MB 自动 multipart，串行 PUT 各 `parts[].upload_url`（v1；可并行优化为后续））示例。
- **`session file download`**：跟随预签名 302 自动落到后端（baas/OSS）或 BCS 流式（local），流式写 `--out`。
- **`session file share`**：生成分享链接返回 `share_url`（可直接分发，不校验会话权限，过期失效）；
  说明分享下载是裸 URL，无 CLI 子命令。
- **`session file capabilities`**：打印 `{storage, presign_upload, presign_download, max_size}`，
  供脚本预判字节是否直连后端、是否需要客户端可达 OSS。
- **返回结果汇总表**。

要点对齐 spec：100MB 是单片/分段阈值（非硬截断），`expires_at`/`method` 在 multipart prepare 响应最外层，
分享 token 用独立密钥 `[session_files.share] token_secret`（不复用 invite）。

### `SKILL.md` 改动点

- **场景指南表**（`## 场景指南` 下表格）加一行：
  `| session-file | 会话工作区文件上传/下载/分享/列/删 | references/session-file.md |`
- **协作模式快速选择树**加一支：在 `session` 分支后补
  `需要群组内共享文件？ → 是 → 使用 session file → 读取 references/session-file.md`。
- **注意事项**补一条：`session file upload` 对 presign 后端（baas/OSS）要求本机/进程网络可达 OSS；
  仅能连 BCS 的环境用 local 后端。跨主机 PUT 时 `bcs` 包装函数已带 `--token`，但 PUT 到后端 OSS URL
  时 Bearer 不应发送（OSS 用预签名 URL 自鉴权）—— 若 `bcs-cli` 内部用 reqwest，默认跨主机剥离
  `Authorization`，无需用户处理；自定义客户端需注意。

### 与 `session.md` 的衔接

`references/session.md` 的命令清单表与"相关 reference"区块加一条指向 `session-file.md`：
会话级文件操作是 `session` 能力的子集（同一 `<session_id>` 作用域），不另立顶层场景。

> 实现时这些 skill 文件改动随 CLI §2 子命令落地一并提交；本节只约定改动范围与内容契约，不展开
> 全文（全文在实现期按 `session.md` 体例撰写）。

## 配置

新增到 bootstrap 配置（`config.rs`），对标现有插件配置块。**`max_size`（=`min(BCS max_file_size,
后端 max_object_size)`）在 bootstrap 阶段计算并注入 `SessionFileService`，运行时不再动态调用
`capabilities().max_object_size`**。**`capabilities()` 必须廉价、同步、无 IO**，返回构造期预计算值；
baas 等后端的 probe 仅在插件 `async fn new()` 构造期执行，不在 `capabilities()` 内做阻塞 IO。除 bootstrap 外 `SessionFileService` 不应在请求路径上调用
`capabilities()`，capabilities 作为构造时注入的静态值：

```toml
[session_files]
storage_backend = "local"       # "local"（v1 默认）| "baas"
multipart_threshold = 104857600 # 100 MB；size ≥ 此值自动 multipart，< 此值单片。对标 baas MULTIPART_THRESHOLD
max_file_size = 5368709120      # 5 GB v1 默认上限；baas 无硬上限、本地取保守上限；超此值返 413
# 对外 capabilities.max_size = min(max_file_size, 后端 capabilities().max_object_size)，bootstrap 阶段静态化
data_dir = "/var/bcs/session-files"   # local only；可为相对路径或由启动脚本解析 $BCS_DATA_DIR
# storage_backend = "baas" 时见 design-baas-plugin.md 的 [session_files.baas] 配置块

[session_files.share]
token_secret = "<share-token-secret>"   # 必填（生产），与 [invite] token_secret 隔离，不复用
default_ttl_seconds = 86400             # 默认 24h；mint 时可按请求覆盖，范围 60–604800
share_base_url = "https://bcs.example.com"  # 可选；share_url 前缀，不配则用请求 host 推导
```
