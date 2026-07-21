# BCS 会话工作区 — baas 存储插件（设计）

**日期：** 2026-07-20
**状态：** 评审稿
**作者：** zhangwu.zh
**范围：** 基于 `StoragePlugin` trait（定义于 `2026-07-20-bcs-session-workspace-api.md` §3 与
`2026-07-20-bcs-session-workspace-design.md`）与 baas 文件传输 HTTP API，实现 `bcs-storage-baas`
插件。**该插件 crate 独立于当前 BCS 仓库**（不在 `src/bcs/crates/plugins/` 下），只需依赖
`bcs-storage-api` trait crate 即可被 BCS 在组装根按配置装配。

## 目标

为 BCS 会话工作区提供一个 baas 后端存储插件：BCS 仍是会话维度的权威（成员校验、元数据、列表），
baas 仅作为字节承载体（文件落 OSS，留存模式，不做设备投递）。baas 是 `supports_presign_put=true`
后端：**上传字节不经 BCS** —— BCS 在 prepare 向 baas 拿真 OSS 直传 URL 转交客户端，客户端直接 PUT
到 OSS；下载经预签名 `share_url` 302 直连 OSS，字节不经 BCS。落实 baas 的流量隔离原则。

## 参考资料

- 框架与 trait 契约：`2026-07-20-bcs-session-workspace-design.md`、`2026-07-20-bcs-session-workspace-api.md`（§3 `StoragePlugin`、§3.2 baas 后端表）
- baas「第四通道」设计（内部背景参考，外部贡献者可忽略；核心接口映射已内嵌本文档与 API 文档 §3.2）：
  https://yuque.antfin.com/securitytec/otbct4/xqtzt0qamaruiofc
- baas 文件上传/下载 API 参考（同上，内部背景参考）：
  https://yuque.antfin.com/securitytec/otbct4/gp53l55mme92fbke

## baas 模型概述

baas 是 **bot 中心化、异步 ticket 模型**的文件传输服务。base URL 形如
`http://{baas-host}:8890/api/v1/bots/{tenant}/{bot_uuid}/files`，所有操作以 `transfer_id` 为锚。

BCS 上传全部走 baas **留存模式（retention mode）**：`POST /upload-url` **不带 `device_path`**，
文件仅存 OSS 供会话共享，不做设备投递（`device_path` 提供 → 投递模式，文件送达设备，不适用本场景）。
留存模式下 ticket 状态机：`CREATED → UPLOADING → UPLOAD_COMPLETED → DONE`（跳过 `PULLING` 拉取阶段，无设备投递）。

baas 不施加 BCS 会话语义 —— BCS 才是会话维度权威，BCS 自身 DB 为列表权威来源（`GET /staging` 列表
不使用，那是 baas 的 bot 命名空间扁平列表，与会话作用域不对应）。

## 插件 crate

- crate 名：`bcs-storage-baas`（**独立于 BCS 仓库**，仅依赖 `bcs-storage-api` trait crate）。
- 装配：BCS 组合根（`crates/bootstrap/bcs/server.rs`）在 `storage_backend = "baas"` 时构造
  `BaasStoragePlugin` 并以 `Arc<dyn StoragePlugin>` 注入 `SessionFileService`。
- `backend_name()` 返回 `"baas"`；`capabilities()` 返回 `supports_presign_put = true`（**上传字节不经 BCS**，
  客户端直传 OSS）+ `supports_presign_download = true`（`max_object_size` 在插件 `async fn new()`
  构造期 probe 一次并固化，不在 `capabilities()` 内做 IO；BCS `max_file_size` 控制对外 `max_size`）。

## `StoragePlugin` 方法 → baas HTTP 映射

baas 统一响应体 `{"code": 0, "data": {...}}`（错误 `code ≠ 0`，`detail` 含 `error`/`message`）。
下表给出每个 trait 方法的 baas 调用与行为。**单片与分段均在 v1 实现**：baas 据 `file_size` 与
`MULTIPART_THRESHOLD`（默认 100 MB）自动分流 `type:"SINGLE"` / `type:"MULTIPART"`，插件须两者都支持。

| `StoragePlugin` 方法 | baas HTTP | 行为与说明 |
|---|---|---|
| `capabilities()` | — | `supports_presign_put = true`（**上传字节不经 BCS**，客户端直传 OSS）、`supports_presign_download = true` |
| `prepare_upload` | `POST /upload-url`（**不带 `device_path`**；带 `filename`、`file_size`、`expire_seconds`、BCS 派生的 `oss_key` 作为对象 key） | baas 据 `file_size` 与 `MULTIPART_THRESHOLD`（默认 100MB）分流：`<` 返 `type:"SINGLE"` + 单个 OSS 直传 `upload_url`；`≥` 返 `type:"MULTIPART"` + `upload_session_id`/`part_size`/`parts:[{part_number, upload_url}]`。插件把 **OSS 直传 URL 放进 `PreparedUpload.client_target = Direct{...}` 交给 BCS**（BCS 原样返给客户端，客户端直传 OSS），并把 `transfer_id`/`type`/`oss_key` 等**定位信息**放进 `UploadHandle` 持久化到 `object_handle`（**不持久化短命的 per-part OSS 直传 URL**，保持行小）。`oss_key` 由 BCS 派生为 `file-transfers/{tenant}/{session_id}/{file_id}/{file_name}`，会话隔离在 key 派生层成立，不依赖 baas 自定 key。 |
| `stream_upload` | — | **不被调用**（`supports_presign_put=true`，客户端直传 OSS，字节不经 BCS）。实现可返 `Unsupported` 或留 no-op。 |
| `complete_upload` | `POST /upload-url/{transfer_id}/complete`（空 body）→ 轮询 `GET /transfers/{transfer_id}` 直到 `status == "DONE"` | 留存模式直接跳到 `DONE`（无 pull）。SINGLE/MULTIPART 统一空 body：MULTIPART 下 baas 自行 `list_parts` 校验组装，客户端/BCS 都无需收集 ETag。轮询间隔与上限超时由配置控制，**超时 → `StorageError::Backend`（502），不无限挂起**。返回 `StorageObjectMeta`（`size`，可选 `sha256` —— baas 通常不返回，留 `Option`）。 |
| `abort_upload` | `DELETE /upload-url/{transfer_id}` | ticket 转 `CANCELLED` 终态、OSS multipart 会话 abort（若进行中），幂等。 |
| `presign_get` | `POST /transfers/{transfer_id}/share-link`（`expire_seconds`） | 仅 `status == "DONE"` 可调用；返回 `share_url`（OSS 预签名 GET URL），作为 BCS `GET .../content` 的 302 目标。若 ticket 非 `DONE`，baas 返 `INVALID_TRANSITION` → `StorageError::Conflict`。 |
| `get_stream`（回退） | `share-link` → `GET share_url` → 流式返回 | `supports_presign_download = true` 时下载走 302，一般不用此方法。 |
| `delete`（`Ready` 文件） | `DELETE /staging?key={oss_key}` | 仅对终态 ticket（`DONE`/`FAILED`/`CANCELLED`）有效；BCS 保证调用前文件 `Ready`（`DONE`）。`404 OSS_OBJECT_NOT_FOUND` 映射为 `Ok`（幂等）。 |
| `health_check` | baas base_url 可达性探测（`HEAD/GET` base 或配置的 `health_probe_path`） | **不依赖任何真实 transfer_id**，避免污染/依赖生产数据。仅探测 base_url 可达 + 鉴权可用，不探测真实对象。 |

### `object_handle` 字段定义

`SessionFile.object_handle` 是一个 **JSON 字符串**，存于 `bcs_session_files.object_handle` 列
（`TEXT`）。它是 `UploadHandle`（`Pending` 时）或 `StorageHandle`（`Ready` 后）经
`serde_json::to_string` 序列化的结果；每次跨 HTTP 请求用时由 `serde_json::from_str` 重建。
结构是后端特定的（`backend_handle: serde_json::Value`），baas 的定义如下。注意它对客户端不透明
（§1.9 分享元信息会省略该字段）。

顶层的 `UploadHandle` / `StorageHandle`（trait 契约，与后端无关的 envelope）：

```jsonc
// UploadHandle (Pending 期间持久化的完整 object_handle)
{
  "backend": "baas",
  "key": "session-files/prod/{sid}/{file_id}/{file_name}",   // BCS 派生的终态 key
  "backend_handle": { /* 见下，baas 特定 */ },
  "expires_at": 1721466000                                    // 上传链接/句柄过期时间（unix 秒）
}
// StorageHandle (Ready 后持久的 object_handle，瘦身后)
{
  "backend": "baas",
  "key": "session-files/prod/{sid}/{file_id}/{file_name}",
  "backend_handle": { /* 见下，baas 特定，去掉过期 OSS 直传 URL */ }
}
```

`backend_handle`（baas 特定）持久化**只存定位信息**，不存短命的 OSS 直传 URL（那些经
`PreparedUpload.client_target` 返给客户端，不落 DB）。2 种形态（Pending + Ready）：

```jsonc
// backend_handle: baas, Pending（单片或分段共用；type 区分）
{
  "transfer_id": "a1b2c3d4...",                               // baas ticket id，complete/abort/share-link 寻址用
  "type": "SINGLE",                                           // 或 "MULTIPART"，baas 分流结果
  "upload_session_id": "oss-session-xxxxx",                   // 仅 MULTIPART：OSS multipart 会话 id（abort/DIAG 用）
  "oss_key": "file-transfers/.../model.bin",                  // OSS 对象 key，delete(DELETE /staging) 寻址用
  "expires_at": 1721466000                                    // 上传 URL 过期时间（≤ envelope.expires_at）
}
```

```jsonc
// backend_handle: baas, Ready（complete 后瘦身）
{
  "transfer_id": "a1b2c3d4...",
  "type": "SINGLE",          // 或 "MULTIPART"，保留原始分流记录
  "oss_key": "file-transfers/.../model.bin"
}
```

> **关键**：baas 签发的 OSS 直传 URL（单片 `upload_url` / 分段 `parts[].upload_url`）**不进
> `object_handle`** —— 它们只在 prepare 的 HTTP 响应里返给客户端、由客户端直接 PUT 到 OSS
> （`supports_presign_put=true`，字节不经 BCS，`stream_upload` 不调用）。因此即便分段 512 part，
> `object_handle` 仍只有几百字节（不含 N 条长签名 URL）。complete/abort 只需 `transfer_id`，
> 不依赖那些 URL；delete 只需 `oss_key`。

字段用途速查：

| 字段 | 谁写入 | 谁读取 | 用途 |
|---|---|---|---|
| `backend` / `key` | BCS envelope | BCS / plugin | 路由到正确 plugin；`key` 用于 DIAG / local 派生路径 |
| `transfer_id` | `prepare_upload`（baas 返回） | `complete_upload`/`abort_upload`/`presign_get` | baas ticket 寻址：complete、DELETE /upload-url、share-link |
| `type` | `prepare_upload` | complete/abort/DIAG | 记录 SINGLE vs MULTIPART 分流结果 |
| `upload_session_id` | `prepare_upload`（仅 MULTIPART） | abort/DIAG | OSS multipart 会话管理 |
| `oss_key` | `prepare_upload`（**BCS 派生**，含 `session_id`/`file_id`，通过 `POST /upload-url` 请求传给 baas 指定对象 key） | `delete` | `DELETE /staging?key={oss_key}` 删除 OSS 对象 |
| `expires_at` | `prepare_upload` | BCS | prepare 返回的 `expires_at` 来源；过期后 sweep 转 `Failed` |

`complete_upload` 成功后，BCS 把 `object_handle` 从 `UploadHandle`（Pending）替换为 `StorageHandle`
（Ready，瘦掉 `upload_session_id`/`expires_at`，保留 `transfer_id`/`type`/`oss_key`）。`presign_get`/
`delete` 用 `StorageHandle` 里的 `transfer_id`（`share-link`）/`oss_key`（`DELETE /staging`）寻址。

> **命名澄清**：客户端在 HTTP §1.2 看到的 `upload_url` / `parts[].upload_url` 是 **baas 签发的真 OSS
> 直传 URL**（prepare 原样返回，`supports_presign_put=true`，客户端直传 OSS、不经 BCS）。与 local 后端
> 那种「BCS 代理 `PUT .../content` URL」区分：两种后端客户端协议一致（PUT 到 BCS 给的 URL），只是 URL
> 指向后端还是 BCS。

## client →（BCS）→ baas/OSS 上传完整流程

以大文件（`size ≥ MULTIPART_THRESHOLD`，分段）留存模式为例。单片流程是它的子集（无 `parts`、
`type:"SINGLE"`）。baas 是 `supports_presign_put=true` 后端：**上传字节不经 BCS**——BCS 只在 prepare
时向 baas 拿真 OSS 直传 URL 转交客户端，客户端直接 PUT 到 OSS；BCS 在 complete 时凭 `transfer_id`
调 baas 完成。baas 服务实例不碰字节、BCS 不落盘不缓冲字节。

```
1. prepare（BCS ↔ baas 拿直传 URL，转交客户端；客户端此时还没传字节）
   client ──POST /sessions/{sid}/files {file_name, size:5GB, mime}──► BCS
   BCS prepare_upload:
     BCS ──POST {base_url}/upload-url {filename, file_size:5GB, expire_seconds, 无device_path}──► baas
     baas（留存模式，file_size≥阈值）:
        data: {transfer_id, type:"MULTIPART", upload_session_id, part_size:10MB, part_count:512,
               parts:[{1, upload_url:<OSS_URL_1>}, {2, upload_url:<OSS_URL_2>}, ...]}
     BCS 构造 PreparedUpload:
        client_target = Direct{ Multipart, parts:[{1,<OSS_URL_1>},{2,<OSS_URL_2>},...], part_size, part_count }
        handle = UploadHandle{backend:"baas", key,
                  backend_handle:{transfer_id, type:"MULTIPART", upload_session_id, oss_key, expires_at}}  // 不含 part URL
     BCS 持久化 handle 到 bcs_session_files.object_handle（小 JSON），行置 Pending
   BCS ──201 {file_id, mode:"multipart", method:"PUT", part_size, part_count, expires_at,
              parts:[{part_number:1, upload_url:<OSS_URL_1>}, ...]}──► client   // 真 OSS URL，原样返回；method/expires_at 在最外层（见 api.md §1.2.b）

2. stream（客户端直传 OSS，可并行；BCS 不参与、stream_upload 不调用）
   client ──PUT <OSS_URL_3>  ──10MB bytes──► OSS    （跨主机，客户端剥离 Authorization；OSS 签名 URL 自带 sig）
   OSS ──200──► client
   （… client 把 512 个 part 都直接 PUT 到各自 OSS_URL_n …）

3. complete（BCS ↔ baas 完成组装）
   client ──POST /.../files/{file_id}/complete {}──► BCS
   BCS complete_upload(handle):   // 仅凭 transfer_id，不需要 part URL/ETag
     BCS ──POST {base_url}/upload-url/{transfer_id}/complete {}──► baas
     baas: OSS list_parts + 组装 multipart（客户端/BCS 都不收集 ETag），ticket→UPLOAD_COMPLETED
     BCS 轮询 GET /transfers/{transfer_id}：留存模式直接 DONE（无 pull）
     BCS 把 object_handle 从 UploadHandle 替换为 StorageHandle（瘦掉 upload_session_id/expires_at），行置 Ready
   BCS ──200 Ready SessionFile──► client
```

要点：
- **prepare 一次请求拿到所有 part 的真 OSS 直传 URL**（baas `MULTIPART` 响应一次性返 `parts[]`，见
  baas API doc §1.1）。BCS 原样返客户端，**不存进 `object_handle`**（保持行小，即使在 multipart 512 part）。
- **字节不经 BCS**：client→OSS 直连，一跳。BCS 不开 `PUT .../content` 接收该上传、不调 `stream_upload`、
  不缓冲字节。这是相对"BCS 代理转发"模型的根本变化（流量隔离 + BCS 无带宽压力 + object_handle 恒小）。
- **`part_number` 仍是客户端→OSS 真直传 URL 的索引**：客户端按 prepare 响应里的 `parts[{n, upload_url}]`
  把第 n 段 PUT 到对应 `OSS_URL_n`。complete 不需要 per-part 信息（baas 自行 list_parts 组装）。
- **baas 状态机细节封装在插件内**：`CREATED → UPLOADING → UPLOAD_COMPLETED → DONE` 的轮询、`list_parts`
  组装都在 `complete_upload` 内完成，对 `SessionFileService`/客户端不可见。
- **complete 同步轮询的伸缩性风险（v1 已知）**：`complete_upload` 在请求路径内同步轮询 baas 直到 `DONE`，大文件 multipart 的 `list_parts` + 组装可能耗时数秒~数十秒，长占 HTTP 连接与 worker。v1 单节点可接受；轮询有配置上限超时（超时 → `StorageError::Backend`/502，不无限挂起）。后续可改为异步 complete（返回 202 + 客户端轮询文件 `status`）。
- **abort**：任意时刻 client `DELETE` → BCS `abort_upload` 调 `DELETE /upload-url/{transfer_id}`，
  baas ticket 转 `CANCELLED`、OSS multipart 会话 abort（若进行中），随后删元数据行。
- **前提**：客户端网络可达 OSS（baas「第四通道」前提）；仅能连 BCS 的客户端应使用 local（非 presign）后端。

## 错误映射

baas 错误码 → `StorageError`（trait 方法不泄漏 baas 内部信息到客户端，仅用于 BCS 内部日志/重试决策）：

- baas `TRANSFER_NOT_FOUND` → `StorageError::NotFound`
- baas `TRANSFER_STATE_CONFLICT` / `NOT_TERMINAL_STATE` / `DIRECTORY_NOT_EMPTY` → `StorageError::Conflict`
- baas `INVALID_TRANSITION` → `StorageError::Conflict`（如对非 `DONE` 的 ticket 请求 `share-link`）
- baas `NOT_IMPLEMENTED` → `StorageError::Unsupported("baas")`
- baas `OSS_OBJECT_NOT_FOUND`（`DELETE /staging` 时）→ 映射为 `Ok`（幂等语义）
- 其他 `code != 0` → `StorageError::Backend`

`StorageError` 再由 BCS HTTP 层映射为对外错误码（如 `Conflict→INVALID_TRANSITION(409)`、
`Backend→STORAGE_BACKEND(502)`），详见 `api.md` 通用错误码表。

## 身份 / 租户

baas base URL 含 `{tenant}/{bot_uuid}`。BCS 在存储插件配置中存储配置好的 `tenant` + `bot_uuid`
凭证。v1 使用**一个配置好的 baas service bot**（所有会话的上传/下载共享该 bot 命名空间下的
staging）；按会话/按上传者分配 baas bot 身份为后续扩展（需要 BCS 持有每个会话内 bot 的 baas
凭证，本期不做）。

**v1 已知风险（SPOF）**：所有会话的文件操作共享单一 baas service bot，该 bot 被吊销/限流/不可达即全部会话文件上传下载失败。v1 缓解：监控该 bot 配额与 baas 可达性（`health_check` + 告警），配置冗余/配额留余；按会话/按上传者分配 baas bot 身份延后（需 BCS 持有每个会话内 bot 的 baas 凭证）。

baas 凭证（鉴权头/token）由 `bcs-storage-baas` 插件内部持有，不暴露给 BCS 上层或客户端。客户端在
prepare 收到的 `upload_url` / `parts[].upload_url` **就是 baas 签发的真 OSS 直传 URL**（`supports_presign_put=true`，
预设签名、自鉴权、不含 baas service-bot 凭证），客户端直接 PUT 到该 URL、字节不经 BCS。baas 的服务凭证
始终留在插件内，不嵌进这些 URL。下载时 BCS 302 跳转的 `share_url` 同样是 OSS 预签名 GET URL（自鉴权、
不含 baas 凭证）。即：客户端会接触 OSS 预签名 URL，但**永远接触不到 baas 的鉴权凭证**。

**会话隔离性**：v1 所有会话共享同一个 service bot 的 baas staging，但 `oss_key` 由 BCS 派生、
含 `session_id` + `file_id`（如 `file-transfers/.../{session_id}/{file_id}/{file_name}`），不同会话
的对象路径天然隔离；baas 的 `GET /staging` 列表**不在 BCS 使用**，BCS 列表权威来自自身 DB（按
`session_id` 过滤）。因此即使共享同一 service bot，会话间的文件可见性仍由 BCS 在成员校验 + DB
列表层保证，不依赖 baas 提供会话隔离。

## 流量隔离

文件字节在客户端与 OSS 之间通过预签名 URL 直接流转，**不经 BCS、不经 baas 服务实例**：
- 上传：客户端 ──PUT──► OSS 直传 URL（baas 签发，BCS 在 prepare 转交）。BCS 不接收字节、不调
  `stream_upload`、不落盘/不缓冲。
- 下载：客户端 ← OSS `share_url`（BCS 302 跳转）。

这与 baas「第四通道」流量隔离原则一致，文件大流量不挤占 BCS 业务/聊天通道，BCS 无带宽/CPU 压力。
同时因 `object_handle` 不持久化短命的 per-part OSS 直传 URL，行恒小（即便 multipart 512 part）。

## 配置

`storage_backend = "baas"` 时，BCS bootstrap 读取以下配置块并构造 `BaasStoragePlugin`：

```toml
[session_files]
storage_backend = "baas"
multipart_threshold = 104857600 # 100 MB；size ≥ 自动 multipart（baas MULTIPART_THRESHOLD），< 单片
max_file_size = 5368709120      # 5 GB v1 默认上限（baas 无硬上限）；超此值返 413。max_size = min(max_file_size, 后端 max_object_size)

[session_files.baas]
base_url = "http://{baas-host}:8890/api/v1/bots/{tenant}/{bot_uuid}/files"
tenant = "<tenant>"             # 必填
bot_uuid = "<service-bot-uuid>" # 必填，v1 用一个配置好的 baas service bot
health_probe_path = ""          # 可选；相对 base_url 的 health endpoint（如 baas 提供 /health 则填 /health）。留空时探测 base_url 本身
# 其余可选：鉴权凭证 / 超时 / complete 轮询间隔与超时 / share-link 默认 ttl（60–604800）
```

`max_size` 在 bootstrap 阶段计算并注入 `SessionFileService`，运行时不再调用 `capabilities()`。
`health_check` 不对 `base_url`（一个鉴权资源路径）发 `HEAD` 期待 200 —— 那会因路径不是合法 baas
资源返 404/405 而误判失败。`health_probe_path` 留空时探测 `base_url`，**接受 `2xx`/`401`/`404`/`405`
即算「服务可达」**（只要网络通、服务在响应）；若 baas 部署提供独立 health endpoint（如 `/health`、
`/status`），填入该路径并以 `2xx` 为可达判据。无论哪种，**都不依赖任何真实 `transfer_id`**。

## 测试

- **契约测试**：`bcs-storage-baas` 须通过 `bcs-storage-api` 的通用契约用例（与 `bcs-storage-local`
  同套），通过对 baas 的 stub/fake server（建议在插件 crate 内提供一个 baas HTTP 协议的 wiremock
  fixture）或 integration tag 测试覆盖：三阶段上传往返、`abort` 幂等、`delete` 幂等、`share-link`
  302 下载往返。
- **轮询与超时**：单元测试 `complete_upload` 的轮询→`DONE` 与超时→`StorageError::Backend` 路径。
- **错误映射**：表驱动测试覆盖 baas 各错误码 → `StorageError` 的映射，含 `OSS_OBJECT_NOT_FOUND→Ok`。
- 因为 crate 独立于 BCS 仓库，其测试在插件 crate 仓库内独立运行；BCS 仓库内对 baas 路径的端到端
  验证依赖 `FakeStoragePlugin`（`bcs-storage-api` 内）注入，不强制真实 baas。
- **分段（multipart, v1）**：契约/集成测试须覆盖 `type:"MULTIPART"` 路径 —— `prepare_upload` 返回
  `PreparedUpload.client_target = Direct{Multipart, parts[]}`（多 part 真 OSS 直传 URL，BCS 原样返客户端、
  **不经 `stream_upload`**）、客户端逐 part 直传 OSS（在测试里即为对 stub OSS 端点的 PUT）、
  `complete_upload` baas `list_parts`+组装后轮询 `DONE`、`abort_upload` abort OSS 会话。与单片同套往返断言。

> 分段上传在 v1 即实现（见上 `prepare_upload`/`complete_upload`/`abort_upload` 行与
> `UploadHandle` multipart 形态）。`part_number: Option<u16>` 已是 trait 签名的一部分。
