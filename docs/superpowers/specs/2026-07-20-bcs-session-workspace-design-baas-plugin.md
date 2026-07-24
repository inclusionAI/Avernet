# BCS 会话工作区 — baas 存储插件（设计）

**日期：** 2026-07-20（2026-07-23 按语雀 Session File Sharing API v1.1 重写）
**状态：** 评审稿
**作者：** zhangwu.zh
**范围：** 基于 `StoragePlugin` trait（定义于 `2026-07-20-bcs-session-workspace-api.md` §3 与
`2026-07-20-bcs-session-workspace-design.md`）与 baas「Session File Sharing API」，实现
`bcs-storage-baas` 插件。**该插件 crate 独立于当前 BCS 仓库**（不在 `src/bcs/crates/plugins/` 下），
只需依赖 `bcs-storage-api` trait crate 即可被 BCS 在组装根按配置装配。

## 目标

为 BCS 会话工作区提供一个 baas 后端存储插件：BCS 仍是会话维度的权威（成员校验、元数据、列表），
baas 仅作为字节承载体（文件落 OSS，session 维度留存，无设备投递）。baas 是 `supports_presign_put=true`
后端：**上传字节不经 BCS** —— BCS 在 prepare 向 baas 拿真 OSS 直传 URL 转交客户端，客户端直接 PUT
到 OSS；下载/分享经 `POST /share-link`（**同步**返 OSS 预签名 GET URL）后 302 直连 OSS。
落实 baas 的流量隔离原则。

## 参考资料

- 框架与 trait 契约：`2026-07-20-bcs-session-workspace-design.md`、`2026-07-20-bcs-session-workspace-api.md`（§3 `StoragePlugin`）
- baas Session File Sharing API（v1.1，权威）：
  https://yuque.antfin.com/securitytec/otbct4/klg0lpglzmwr8t3g

## baas 模型概述（v1.1）

baas Session File Sharing 是 **session 维度、同步 share-link 下载、上传异步 ticket** 的文件 API。
所有操作以 **`transfer_id` 为唯一凭证**（寻址 complete / 取消 / 删除 / share-link / 状态查询）。

- **基础路径**：`/api/v1/sessions/{tenant}/{session_id}/files`（**session 维度**，session_id 用 BCS
  的 session_id，tenant 由配置提供）。BCS `session_id` 形如 `bcs_grp_<uuid>:<8hex>`
  （例 `bcs_grp_312be2fa-c3a2-4f7c-b67a-485b6119af65:cdf28232`），**含冒号**、路径段不安全；
  插件拼接 `{base}` 时**必须对 `session_id` 做 percent-encoding**（`:`→`%3A`，以及任何 `/`/空格/
  非 URL-safe 字符），避免冒号被路由器视为分隔或破坏路径结构。子路径在其下：`.../upload-url`、`.../upload-url/{transfer_id}/complete`、
  `.../upload-url/{transfer_id}`、`.../transfers/{transfer_id}`、`.../transfers/{transfer_id}/share-link`。
- **上传模型**：异步 ticket，`CREATED → UPLOADING → DONE`（`/complete` 直接进 DONE，无设备推送阶段）；
  可转入终态 `FAILED`/`CANCELLED`；`DONE`/`FAILED`/`CANCELLED → DELETED`。
- **下载/分享模型**：**同步**。无单独 `download_url` 字段 —— 获取文件字节一律经
  `POST /files/transfers/{transfer_id}/share-link` 立即拿到 OSS 预签名 GET URL（`share_url`），同一
  `transfer_id` 可多次调用、每次独立时效。`expire_seconds`（60–604800）由调用方传入；BCS 对会话内下载
  与分享下载统一用同一 TTL（默认 3600，见「下载路径分流」），不区分短/长。
- **无 `direction`、无设备**：session 场景所有文件均来自本地上传，OSS 即终态存储。

baas 不施加 BCS 会话业务语义 —— BCS 才是会话维度权威（成员校验、列表权威取自 BCS DB）；会话归属
边界由 baas 路径里的 `{tenant}/{session_id}` 承载（session_id 用 BCS 的），对象隔离在路径层成立。

## 插件 crate

- crate 名：`bcs-storage-baas`（**独立于 BCS 仓库**，仅依赖 `bcs-storage-api` trait crate）。
- 装配：BCS 组合根（`crates/bootstrap/bcs/server.rs`）在 `storage_backend = "baas"` 时构造
  `BaasStoragePlugin` 并以 `Arc<dyn StoragePlugin>` 注入 `SessionFileService`。
- `backend_name()` 返回 `"baas"`；`capabilities()` 返回 `supports_presign_put = true`（**上传字节不经 BCS**，
  客户端直传 OSS）、`supports_presign_download = true`（下载经 share-link 302）。`max_object_size`
  在插件 `async fn new()` 构造期 probe 一次并固化，不在 `capabilities()` 内做 IO；BCS `max_file_size`
  控制对外 `max_size`。

## `StoragePlugin` 方法 → baas HTTP 映射

baas 统一响应体 `{"code": 0, "message": "success", "data": {...}}`（错误 `code ≠ 0`，`detail` 含
`error`/`message`，见「错误映射」）。下表路径前缀 `{base}`=`{endpoint}/api/v1/sessions/{tenant}/{session_id}/files`。

| `StoragePlugin` 方法 | baas HTTP | 行为与说明 |
|---|---|---|
| `capabilities()` | — | `supports_presign_put = true`（**上传字节不经 BCS**）、`supports_presign_download = true`（下载 share-link 302） |
| `prepare_upload` | `POST {base}/upload-url`（body：`filename`（必填）、`file_size`、`expire_seconds`、`part_size?`、`operator`=`BCS caller`、`staging_subdir?`=`null`） | baas 据 `file_size` 与 `MULTIPART_THRESHOLD`（100 MiB）分流：`<` 返 `type:"SINGLE"` + 顶层 `upload_url`+`expires_at`；`≥` 返 `type:"MULTIPART"` + `upload_session_id`/`part_size`/`part_count`/`parts:[{part_number, upload_url, expires_at}]`（顶层 `upload_url`/`expires_at` 为 `null`）。插件把 **OSS 直传 URL 放进 `PreparedUpload.client_target = Direct{...}` 交给 BCS**（BCS 原样返客户端），并把 `transfer_id` 等**定位信息**放进 `UploadHandle` 持久化到 `object_handle`（**不持久化短命的 per-part OSS 直传 URL**，保持行小）。 |
| `stream_upload` | — | **不被调用**（`supports_presign_put=true`，客户端直传 OSS）。实现返 `Unsupported`。 |
| `complete_upload` | `POST {base}/upload-url/{transfer_id}/complete`（空 body） | SINGLE 验证 OSS 对象存在 / MULTIPART 合并分片后 ticket 直接进 `DONE`（无需轮询设备）。插件可对 `DONE` 返 `StorageObjectMeta`（`size`；`sha256` baas 不返，留 `None`）。幂等：重复 complete 返当前 `DONE` 视为成功。 |
| `abort_upload`（Pending/Failed） | `DELETE {base}/upload-url/{transfer_id}` | 取消进行中上传；MULTIPART 同时中止 OSS 分片会话；ticket → `CANCELLED`。幂等。 |
| `presign_get`（下载/分享，会话内 & 分享共用） | `POST {base}/transfers/{transfer_id}/share-link`（body：`expire_seconds`、`operator`=`BCS caller`、`show?`） | **同步**返 `share_url`（OSS 预签名 GET URL，**带 `expire_seconds` 有效期** 60–604800）。仅 `status == DONE` 可调，否则 baas 返 `SOURCE_TRANSFER_NOT_READY`(409)/`INVALID_TRANSITION`(422)。插件把 `share_url` 包成 `PresignGetTicket { download_url: share_url, expires_at }` 返给 BCS（`expires_at` 由响应 ISO 8601 `expires_at` 解析为 unix 秒）。**同一 transfer_id 可多次调用，每次独立时效** —— 插件**不缓存 share_url**，每次 `presign_get` 都重新 `POST /share-link`（BCS 对会话内与分享下载统一传 `ttl_secs=share_link_ttl`（默认 3600），不区分）。 |
| `get_stream`（回退） | `share-link` → `GET share_url` → 流式返回 | `supports_presign_download = true` 时下载走 302，一般不用此方法。 |
| `delete`（`Ready` 文件） | `DELETE {base}/transfers/{transfer_id}` | **按 `transfer_id` 删除**（删 ticket + 关联 OSS staging 对象）。仅终态（`DONE`/`FAILED`/`CANCELLED`/`DELETED`）可删；`DONE` Ready 文件满足。已 `DELETED` 重复删返幂等成功 → 视为 `Ok`。 |
| `health_check` | baas `{endpoint}` 可达性探测（`HEAD/GET` endpoint 或配置的 `health_probe_path`） | **不依赖任何真实 `transfer_id`**，避免污染/依赖生产数据。仅探测服务可达 + 鉴权可用，不探测真实对象。 |

> **token 唯一凭证化**：与 baas 对接的**唯一**凭证统一为 `transfer_id` —— complete / abort / delete /
> share-link / 状态查询全部用它。`object_handle` **不再需要 `oss_key`**（删除按 transfer_id，不按 OSS key），
> 也不解析最终 OSS key。这大幅简化 handle 形态（见下）。

### `object_handle` 字段定义

`SessionFile.object_handle` 是一个 **JSON 字符串**（`UploadHandle`(Pending) / `StorageHandle`(Ready) 序
列化）。后端特定部分（`backend_handle: serde_json::Value`）对客户端不透明（§1.9 分享响应会省略该字段）。

顶层 envelope（trait 契约、后端无关）：

```jsonc
// UploadHandle (Pending 期间持久化的完整 object_handle)
{
  "backend": "baas",
  "key": "session-files/prod/{sid}/{file_id}/{file_name}",   // BCS 派生的 DIAG/local 派生用 key；baas 不用它寻址
  "backend_handle": { /* 见下，baas 特定，仅定位信息 */ },
  "expires_at": 1721466000                                    // 上传链接/句柄过期时间（unix 秒）
}
// StorageHandle (Ready 后持久的 object_handle，瘦身后)
{
  "backend": "baas",
  "key": "session-files/prod/{sid}/{file_id}/{file_name}",
  "backend_handle": { /* 见下，仅 transfer_id */ }
}
```

`backend_handle`（baas 特定）**只存 `transfer_id` 这一个定位凭证**（+ Pending 期上传链接过期时间）。
2 种形态（Pending + Ready）：

```jsonc
// backend_handle: baas, Pending（单片或分段共用；type 仅 DIAG，complete/abort 不需要）
{
  "transfer_id": "a1b2c3d4...",    // baas ticket id —— 唯一凭证：complete / abort / share-link / delete 全部用它
  "type": "SINGLE",                // "SINGLE" | "MULTIPART"，仅 DIAG 记录分流结果（baas 端自存，BCS 调用时不需要）
  "expires_at": 1721466000         // 上传 URL 过期时间（≤ envelope.expires_at）
}
```

```jsonc
// backend_handle: baas, Ready（complete 后瘦身：仅 transfer_id）
{
  "transfer_id": "a1b2c3d4..."
}
```

> **关键**：baas 签发的 OSS 直传 URL（单片 `upload_url` / 分段 `parts[].upload_url`）**不进 `object_handle`** ——
> 它们只在 prepare 的 HTTP 响应里经 `PreparedUpload.client_target` 返给客户端、由客户端直接 PUT 到 OSS
> （`supports_presign_put=true`，字节不经 BCS，`stream_upload` 不调用）。因此即便分段 512 part，
> `object_handle` 仍只有几十字节（不含 N 条长签名 URL）。complete/abort/delete/share-link 只需 `transfer_id`。
>
> **无 `oss_key`**：删除按 `transfer_id` 调 `DELETE .../transfers/{transfer_id}`，不需要 OSS 对象 key；
> 故**不需要**在 complete 时从 baas 响应捕获最终 OSS key（v1.1 查询响应也不再返回 `deleted_key` 字段）。

字段用途速查：

| 字段 | 谁写入 | 谁读取 | 用途 |
|---|---|---|---|
| `backend` / `key` | BCS envelope | BCS / plugin | 路由到正确 plugin；`key` 仅用于 DIAG / local 派生路径，baas 不用它寻址 OSS |
| `transfer_id` | `prepare_upload`（baas 返回） | `complete_upload`/`abort_upload`/`presign_get`/`delete` | baas ticket 唯一凭证：complete、DELETE /upload-url(abort)、share-link、DELETE /transfers(delete) |
| `type` | `prepare_upload` | DIAG | 记录 SINGLE vs MULTIPART 分流结果（BCS 调 baas 时不传，仅日志）|
| `expires_at` | `prepare_upload` | BCS | 上传 URL 过期时间；过期后 sweep 转 `Failed` |

`complete_upload` 成功后，BCS 把 `object_handle` 从 `UploadHandle`（Pending）替换为 `StorageHandle`
（Ready：瘦掉 `type`/`expires_at`，**仅保留 `transfer_id`**）。`presign_get`（share-link）/`delete`
都用 `StorageHandle.backend_handle.transfer_id` 寻址。

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
>    ticket 调用会返 `TRANSFER_STATE_CONFLICT`(409) —— 插件将其当 `Ok`（abort 已终态 = 已取消/已完成，
>    幂等成功）。这与通用错误映射表（TRANSFER_STATE_CONFLICT→Conflict）不同，是 abort 路径的特判。
>    delete 路径的幂等宽容则按 `TRANSFER_NOT_FOUND`(404) → Ok（已删）。两者各自特判，非通用规则。

## client →（BCS）→ baas/OSS 上传完整流程

以大文件（`file_size >= MULTIPART_THRESHOLD`=100 MiB，分段）为例。单片流程是它的子集（无 `parts`、
`type:"SINGLE"`）。baas 是 `supports_presign_put=true` 后端：**上传字节不经 BCS**。

```
1. prepare（BCS ↔ baas 拿直传 URL，转交客户端）
   client ──POST /sessions/{sid}/files {file_name, size:200MiB, mime}──► BCS
   BCS prepare_upload:
     BCS ──POST {endpoint}/api/v1/sessions/{tenant}/{sid}/files/upload-url
              {filename, file_size:200MiB, expire_seconds, operator:<BCS caller>, staging_subdir:null}──► baas
     baas（file_size≥阈值）:
        data: {transfer_id, type:"MULTIPART", upload_session_id, part_size:10MiB, part_count:20,
               upload_url:null, expires_at:null,
               parts:[{1, upload_url:<OSS>, expires_at}, {2, upload_url:<OSS>, expires_at}, ...]}
     BCS 构造 PreparedUpload:
        client_target = Direct{ Multipart, parts:[{1,<OSS>},{2,<OSS>},...], part_size, part_count }
        handle = UploadHandle{backend:"baas", key,
                  backend_handle:{transfer_id, type:"MULTIPART", expires_at}}  // 不含 part URL，不含 oss_key
     BCS 持久化 handle 到 bcs_session_files.object_handle（小 JSON），行置 Pending
   BCS ──201 {file_id, mode:"multipart", method:"PUT", part_size, part_count, expires_at,
              parts:[{part_number:1, upload_url:<OSS>}, ...]}──► client   // 真 OSS URL 原样返回

2. stream（客户端直传 OSS，可并行；BCS 不参与、stream_upload 不调用）
   client ──PUT <OSS part1 URL> ──10MiB──► OSS    （跨主机，客户端剥离 Authorization；OSS 签名 URL 自带 sig）
   OSS ──200──► client
   （… client 把 20 个 part 都直接 PUT 到各自 OSS URL …）

3. complete（BCS ↔ baas 完成 → 直接 DONE）
   client ──POST /.../files/{file_id}/complete {}──► BCS
   BCS complete_upload(handle):   // 仅凭 transfer_id
     BCS ──POST {endpoint}/api/v1/sessions/{tenant}/{sid}/files/upload-url/{transfer_id}/complete {}──► baas
     baas: SINGLE 验证 OSS 对象 / MULTIPART 合并分片 → ticket→DONE（同步，无需轮询设备）
     BCS 把 object_handle 从 UploadHandle 替换为 StorageHandle（瘦到仅 transfer_id），行置 Ready
   BCS ──200 Ready SessionFile──► client

4. download / share（BCS ↔ baas 拿 share_url，302 给客户端；客户端直连 OSS）
   client ──GET /sessions/{sid}/files/{file_id}/content──► BCS   （带鉴权 member 校验）
   BCS download_route → presign_get(handle, ttl=3600):
     BCS ──POST {endpoint}/api/v1/sessions/{tenant}/{sid}/files/transfers/{transfer_id}/share-link
              {expire_seconds:3600, operator:<BCS caller>, show?}──► baas
     baas: data:{share_url:<OSS GET URL>, transfer_id, expires_at(ISO)}
     BCS 302 → share_url   // 客户端跟随，直连 OSS 取字节；不带 Authorization
```

要点：
- **prepare 一次请求拿到所有 part 的真 OSS 直传 URL**（baas `MULTIPART` 响应一次性返 `parts[]`）。
  BCS 原样返客户端，**不存进 `object_handle`**（保持行小）。
- **`object_handle` 只存 `transfer_id`**（+ Pending `type`/`expires_at`）—— 没有短命的 part URL，
  没有 `oss_key`。complete/abort/delete/share-link 全凭 `transfer_id`。
- **字节不经 BCS**：上传 client→OSS；下载 client←OSS（BCS 302 跳 share_url）。
- **下载是同步 share-link**：`presign_get` 直接 `POST /share-link` 立即拿到 `share_url` 返 BCS、302 给客户端，
  无异步 ticket / 无轮询。
- **complete 同步到 DONE**：session 场景 `/complete` 后直接 `DONE`，无需轮询（与 Bot 版「需轮询 `PULLING`」不同）。
- **前提**：客户端网络可达 OSS（baas 流量隔离前提）；仅能连 BCS 的客户端应使用 local（非 presign）后端。

## 下载路径分流

v1.1 删除了独立的 `download_url` —— **会话内下载与分享下载统一走 `POST /share-link`**（同步返 OSS 预签名
GET URL）。两条路径在 **BCS 层差异仅鉴权**，在 **trait/baas 层是同一个 `presign_get(handle, 3600)` 调用**：
BCS 对会话内下载与分享下载**统一用同一 TTL（默认 3600 秒，可配 `share_link_ttl`）**，不区分短/长；分享路径
额外的过期约束仅由 BCS share token 的 `exp` 早于调 `presign_get` 之前校验完成（见下）。

| 路径 | HTTP 入口 | 鉴权 | BCS service 流程 | trait 方法 | baas 调用 | TTL |
|---|---|---|---|---|---|---|
| 会话内下载 | `GET /sessions/{sid}/files/{file_id}/content` | 带鉴权（member 校验） | member 校验 → Ready 检查 → `presign_get(handle, 3600)` → 302 | `presign_get` | `POST .../transfers/{transfer_id}/share-link`（`expire_seconds=3600`） | 3600（统一）|
| 分享下载 | `GET /sessions/shared-file/content?token=...` | 无鉴权（凭 share token） | `share_consume(token)` 校验 token `exp`（过期/无效统一 404）→ 取行重建 handle → `presign_get(handle, 3600)` → 302 | `presign_get` | `POST .../transfers/{transfer_id}/share-link`（`expire_seconds=3600`） | 3600（统一）|

实施要点：
- **trait 仅一个 `presign_get(handle, ttl)` → POST share-link**（v1.1 统一了下载为 share-link；`ttl`
  由 BCS 传入，插件透传为 `expire_seconds`，baas 限制 60–604800，3600 在范围内）。**不新增 trait 方法、
  不新增 `download_route_shared`** —— 会话内与分享路径在 service 层都最终调 `presign_get(handle, 3600)`，
  仅鉴权前置不同。
- **service 层鉴权差异**（不依赖 trait）：
  - 会话内：HTTP handler member 校验 → `download_route(sid, fid)` → `presign_get(handle, 3600)` → 302。
  - 分享：`share_consume(token)`（已实现：`share_token_decode_and_verify` 校验 `exp`，任何失败模式统一 `404 NOT_FOUND`、关闭 oracle）
    → 取出 `SessionFile` 行 → 以行内 `object_handle` 重建 `StorageHandle` → `presign_get(handle, 3600)` → 302。
- **统一 TTL=3600**：会话内与分享**同一** `share_link_ttl`（默认 3600，可配），不设短/长二分。分享链接的
  有效期由 BCS share token 的 `exp`（生成时按 `ttl_seconds`，范围 60–604800）决定，与 `share_url` 的
  `expire_seconds`（恒 3600）独立 —— 持有人凭未过期 token 重调下载端点即换新 `share_url`（插件不缓存）。
- **`?ttl` 对前端隐藏**：BCS 不把 ttl 暴露为前端契约；`?ttl` 入参保留为「接受但忽略」以兼容，TTL 完全由
  BCS 内部（`share_link_ttl`）+ 后端决定。
- **`presign_get` 不缓存 share_url**：每次调用都重新 `POST /share-link`（同一 transfer_id 可多次请求、
  各自独立时效）。
- **安全**：share_url 带 `expire_seconds`(3600) 有效期 + BCS share token 的 `exp` 双层约束；无鉴权分享下载
  不返回会话内信息（meta 经 `to_shared_dto` 剥离 `session_id`/`object_handle`，失败统一 404 不泄漏 oracle）。
- local/fake 后端 `supports_presign_download=false`：`presign_get` 可返 `Unsupported`；本地文件不适合
  跨会话无鉴权分享（分享为 presign 后端能力）。

> **契约级影响**：**无**。`StoragePlugin` trait 不增方法（单 `presign_get` 已覆盖两路）；
`SessionFileService` trait 亦不增 `download_route_shared` —— 分享下载复用现有 `share_consume`（token 校验）
+ `presign_get`（重新签 share-link）组合，不引入新方法。`download_route`/`share_consume` 为现有方法。
> `?ttl` 对 BCS 内部调用处：会话内 `download_route` 与分享 `presign_get` 均传统一 `share_link_ttl`（默认 3600），
> 不再读 `q.ttl`。

## 错误映射

baas v1.1 错误体格式：`{"detail": {"error": "<CODE>", "message": "...", ...可选字段}}`。baas 错误码 →
`StorageError`（trait 方法不泄漏 baas 内部信息到客户端，仅用于 BCS 内部日志/重试决策）：

- baas `TRANSFER_NOT_FOUND`（404）→ `StorageError::NotFound`
- baas `SOURCE_TRANSFER_NOT_FOUND`（404，share-link 引用 transfer 不存在）→ `StorageError::NotFound`
- baas `SOURCE_TRANSFER_NOT_READY`（409，share-link 时 transfer 非 DONE，响应含 `current_status`）→
  `StorageError::Conflict`（文件未就绪；BCS 保证调用前文件 `Ready`/`DONE`，此错理论不触发）
- baas `TRANSFER_STATE_CONFLICT`（409，非法状态转换如重复 complete）→ `StorageError::Conflict`
- baas `TRANSFER_NOT_TERMINAL`（409，delete 时 ticket 非终态）→ `StorageError::Conflict`
- baas `OSS_OBJECT_NOT_FOUND`（**409**，complete 时 OSS staging 路径无对象 = 上传未完成）→
  `StorageError::Conflict`（上传未真正落地，complete 失败）
- baas `INVALID_TRANSITION`（422，如对非 DONE 的 ticket 请求 share-link）→ `StorageError::Conflict`
- baas `NOT_IMPLEMENTED`（501）→ `StorageError::Unsupported("baas")`
- baas `INTERNAL_ERROR`（500）/其他 → `StorageError::Backend`
- `delete`（`DELETE .../transfers/{transfer_id}`）对已 `DELETED` 重复调用返幂等成功 → `Ok`。

`StorageError` 再由 BCS HTTP 层映射为对外错误码（如 `Conflict→INVALID_TRANSITION(409)`、
`NotFound→FILE_NOT_FOUND(404)`、`Backend→STORAGE_BACKEND(502)`），详见 `api.md` 通用错误码表。

## 身份 / 租户

baas 基础路径含 `{tenant}/{session_id}`：

- **`session_id`** = **BCS 的 session_id**（prepare/complete/delete/share-link 调用时由 BCS 从文件行直接传入，
  会话归属边界由它承载）。
- **`tenant`** 由 **BCS 配置**提供（`session_files.tenant`，见「配置」），不经 URL 路径之外的渠道传递。
- **baas 鉴权凭证**（service token / 鉴权头）由插件内部持有、请求时携带，不暴露给 BCS 上层或客户端。
  客户端收到的 `upload_url`/`parts[].upload_url` 是 baas 签发的真 OSS 直传 URL（自鉴权、不含 baas 凭证）；
  下载 302 的 `share_url` 同样是 OSS 预签名 GET URL（自鉴权）。客户端接触 OSS 预签名 URL 但**永远接触不到
  baas 鉴权凭证**。
- **`operator`**：prepare / share-link 请求体里 BCS 传 `operator`=`<BCS caller>`（上传者/调用者 human/bot id），
  供 baas 侧审计。

**会话隔离性**：不同会话的对象天然隔离于路径 `{tenant}/{session_id}/files/...`；BCS 列表权威来自自身 DB（按
`session_id` 过滤），不依赖 baas 列表。删除按 `transfer_id` 且必须在该 session 路径下，进一步避免跨会话误删。
SPOF/凭证风险：BCS 用单一 baas 服务凭证访问所有会话文件，凭证被吊销/限流/不可达即全部会话失败。v1 缓解：
监控 baas 可达性与凭证配额（`health_check` + 告警）；按会话/上传者分配 baas 身份延后。

## 孤儿清理

删除统一按 `transfer_id`（`DELETE .../transfers/{transfer_id}`），且 `transfer_id` 持久化在每个文件行的
`object_handle` 里 —— **正常路径总能凭行内 `transfer_id` 清理后端对象**，不再有 v1（按 `oss_key` 删除）时
"元数据丢则无法重建 key" 的问题。

- **正常路径**（行存在）：`delete_all_for_session`/`delete` 按行内 `transfer_id` 调
  `DELETE .../transfers/{transfer_id}`（终端态守卫由 baas 把关，Ready=DONE 满足）。
- **元数据已丢的孤儿**：v1.1 **未提供 staging 列表/扫描接口**（`GET /staging` 已删），BCS 侧无法凭
  `transfer_id` 之外的途径枚举孤儿。故孤儿清理**不在 BCS 侧做**，依赖 baas 侧的留存/过期策略（OSS staging
  对象的 retention/TTL）兜底；如确需主动清理，需 baas 提供按 `session_id`/`tenant` 的枚举接口（v1.1 暂无）。

## 流量隔离

文件字节在客户端与 OSS 之间通过预签名 URL 直接流转，**不经 BCS、不经 baas 服务实例**：
- 上传：客户端 ──PUT──► OSS 直传 URL（baas 签发，BCS 在 prepare 转交）。BCS 不接收字节、不调
  `stream_upload`、不落盘/不缓冲。
- 下载：客户端 ← OSS `share_url`（BCS `POST /share-link` 拿到后 302 跳转）。

这与 baas 流量隔离原则一致，文件大流量不挤占 BCS 业务/聊天通道，BCS 无带宽/CPU 压力。同时因 `object_handle`
只存 `transfer_id`、不持久化短命 OSS 直传 URL，行恒小（即便 multipart 512 part）。

## 配置

`storage_backend = "baas"` 时，BCS bootstrap 读取以下配置块并构造 `BaasStoragePlugin`：

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

URL 拼装规则：`{endpoint}/api/v1/sessions/{tenant}/{session_id_编码后}/files/{sub}` —— 插件用 endpoint（host）+
固定 API path + 配置 `tenant` + 请求级 `session_id`（**percent-encoded，见「baas 模型概述」**）拼接，
部署侧无需在 endpoint 里带 API path。
`staging_subdir` 不进配置、prepare 时不传（默认 `null`）—— 会话隔离由路径 `{tenant}/{session_id}` 承载。
`max_size` 在 bootstrap 阶段计算并注入 `SessionFileService`，运行时不再调用 `capabilities()`。
`health_check` 不对 `endpoint`（files API 根，需鉴权）发 `HEAD` 期待 200 —— 那会因未带凭证或路径
非 health 资源返 401/404/405 而误判失败。`health_probe_path` 留空时探测 `endpoint`，**接受 `2xx`/`401`/`404`/`405`
即算「服务可达」**；若 baas 部署提供独立 health endpoint（如 `/health`），填入该路径并以 `2xx` 为可达判据。
无论哪种，**都不依赖任何真实 `transfer_id`**。

## 落地前置改造：backend-agnostic 装配

baas 插件落地前，先对 BCS 框架做一处结构改造：**让 `config.rs`/`server.rs` 对「后端清单」无感**，
新增后端（OSS/NAS/…）时不再每次改这两个文件。机制 = `StoragePluginFactory` trait + 配置透传 table + 组合根一行 arm。
本节定目标契约；plan 生成时统一实施。

### 现状问题

- `config.rs` `SessionFilesConfig` 只列 local 用的字段（`data_dir`）；加 baas 需添 `endpoint`/`tenant`，
  以后每加一个后端都要在此加字段 —— 配置层感知后端清单。
- `server.rs` `build_session_files_service` 硬编码 `LocalStoragePlugin::new(...)`，无 `match storage_backend` 分支；
  加后端要在此改装配代码。

### 目标设计

#### 1. `bcs-storage-api` 加 factory trait + 通用后端配置容器

每个后端 crate 自带一个 `StoragePluginFactory` 实现，负责解析自己的专属配置键。`bcs-storage-api` 只定义 trait
与一个**后端无关的配置容器** `StorageBackendConfig`：

```rust
// bcs-storage-api/src/factory.rs（新增）
use std::sync::Arc;
use async_trait::async_trait;
use serde_json::Map;

/// 后端无关的装配输入：所有后端都用得到的通用值 + 后端专属键值透传容器。
/// 专属配置（local 的 data_dir、baas 的 endpoint/tenant）不在此 struct 里具名，
/// 放在 `backend` Map 里原样透传，由各 factory 自行解析。
pub struct StorageBackendConfig {
    pub env: String,
    pub max_file_size: u64,
    pub multipart_threshold: u64,
    pub share_link_ttl: u64,
    pub bcs_base_url: String,
    /// 后端专属配置原样透传（来自 TOML `[session_files.backend]` 或顶层 `[session_files]` 里
    /// 非 [`SessionFilesConfig`] 已知字段的剩余键）。各 factory 自取所需键、自身校验。
    pub backend: Map<String, serde_json::Value>,
}

/// 每个 storage 后端 crate 实现：把自己的专属配置解析成具体 `StoragePlugin`。
/// `backend_name` 用于组合根按 `storage_backend` 配置值选 factory。
#[async_trait]
pub trait StoragePluginFactory: Send + Sync {
    fn backend_name(&self) -> &'static str;
    async fn build(&self, cfg: &StorageBackendConfig) -> Result<Arc<dyn StoragePlugin>, StoragePluginError>;
}
```

> `StoragePluginError` 复用现有或新增（`Build(String)`，承载各 factory 解析失败原因，不泄漏到客户端）。

#### 2. `config.rs`：`SessionFilesConfig` 加 `share_link_ttl` + `backend` 透传表，**不列后端专属字段**

```rust
pub struct SessionFilesConfig {
    pub storage_backend: String,        // "local" | "baas" | …（组合根按此选 factory）
    pub multipart_threshold: u64,
    pub max_file_size: u64,
    pub share_link_ttl: u64,            // 新增：统一下载/分享 share-link TTL（默认 3600）
    pub share: SessionFilesShareConfig,
    #[serde(default)]
    pub backend: toml::Table,           // 新增：后端专属配置透传（local: data_dir；baas: endpoint/tenant）
    // data_dir 从具名字段移除，改由 LocalStoragePluginFactory 从 backend["data_dir"] 读
}
```
新后端加字段只改它自己的 crate 文档 + `backend` table 填键，`config.rs` 永不动。

#### 3. `server.rs`：装配处只 `match` factory 名，不解析任何后端字段

```rust
let factory: Arc<dyn StoragePluginFactory> = match config.session_files.storage_backend.as_str() {
    "local" => Arc::new(LocalStoragePluginFactory),
    "baas"  => Arc::new(BaasStoragePluginFactory),   // 引入 bcs-storage-baas crate 即加此 arm
    other   => bail!("unknown storage_backend: {other}"),
};
let backend_cfg = StorageBackendConfig {
    env: env.clone(), max_file_size: config.session_files.max_file_size,
    multipart_threshold: config.session_files.multipart_threshold,
    share_link_ttl: config.session_files.share_link_ttl,
    bcs_base_url: bcs_base_url.clone(),
    backend: toml_to_json_map(&config.session_files.backend),
};
let storage = factory.build(&backend_cfg).await?;
```
新后端加进来仍需在此加一行 arm（`Arc::new(XxxFactory)`）—— 这是架构宪法 Rule 14「组合根选择实现」的合规位置，
且 arm 仅一行、不涉及任何配置字段解析。完全零改 server.rs 可用 `inventory`/`linkme` 全局自注册，但引入新依赖、
调试弱，本期不采用。

#### 4. local 后端迁移：把 `LocalStoragePlugin::new` 包一层 `LocalStoragePluginFactory`

`bcs-storage-local` 新增 `LocalStoragePluginFactory`，`build` 内从 `cfg.backend["data_dir"]` 取路径（缺省回退
`{bots_base_dir}/session-files`，需把 `bots_base_dir` 也放进 `StorageBackendConfig` 或由 server.rs 在 backend
table 里预填好）。~15 行。

#### 5. baas crate（本设计）实现 `BaasStoragePluginFactory`

`build` 内从 `cfg.backend` 取 `endpoint`/`tenant`/`health_probe_path`/凭证等，缺失则返 `StoragePluginError::Build`。
`max_object_size` 在 `BaasStoragePlugin::new()` 构造期 probe 一次并固化（已有设计）。

### 其它配套小改（与 factory 改造一并落地）

- `SessionFileServiceConfig`（`bcs-session-file/src/service.rs`）加 `share_link_ttl: u64` 字段；
- `download_route` 的 `ttl_secs.unwrap_or(300)` → `ttl_secs.unwrap_or(self.cfg.share_link_ttl)`（**当前 bug 修复**：
  统一 3600，现状 300 会让 OSS URL 5 分钟过期）；
- bootstrap 装配处传 `share_link_ttl`。

### 改动收敛清单

| 文件 | 改动 |
|---|---|
| `bcs-storage-api/src/factory.rs`（新） | `StoragePluginFactory` trait + `StorageBackendConfig` |
| `bcs-storage-local/src/lib.rs` | + `LocalStoragePluginFactory`（包现有 `new`） |
| `bcs-storage-baas`（新 crate） | `BaasStoragePlugin` + `BaasStoragePluginFactory` |
| `config.rs` `SessionFilesConfig` | + `share_link_ttl`/`backend: toml::Table`；`data_dir` 移入 `backend` |
| `server.rs` `build_session_files_service` | match factory arm（local/baas）+ build 调用 |
| `service.rs` `SessionFileServiceConfig` + `download_route` | + `share_link_ttl` 字段；TTL 用 `share_link_ttl` |

之后再加 OSS/NAS 后端：新建 crate 实现 plugin+factory + `server.rs` 加一行 arm，`config.rs` 不动。

## 测试

- **契约测试**：`bcs-storage-baas` 须通过 `bcs-storage-api` 的通用契约用例（与 `bcs-storage-local` 同套），
  通过对 baas 的 stub/fake server（插件 crate 内提供 baas HTTP 协议的 wiremock fixture）覆盖：三阶段上传
  往返（SINGLE + MULTIPART，客户端逐 part 直传 OSS）、`complete` 直达 `DONE`、`abort` 幂等、`delete` 按
  `transfer_id` 幂等、**下载/分享 share-link 往返**（`presign_get`→`share_url`→302，断言不缓存每次重签、
  对非 DONE 返 `Conflict`）。
- **错误映射**：表驱动测试覆盖 baas v1.1 各错误码 → `StorageError`：`TRANSFER_NOT_FOUND`/`SOURCE_TRANSFER_NOT_FOUND→NotFound`、
  `SOURCE_TRANSFER_NOT_READY`/`TRANSFER_STATE_CONFLICT`/`TRANSFER_NOT_TERMINAL`/`OSS_OBJECT_NOT_FOUND`/`INVALID_TRANSITION→Conflict`、
  `NOT_IMPLEMENTED→Unsupported`、`INTERNAL_ERROR→Backend`；`DELETE` 已 `DELETED` 重复调 `Ok`。
- **ISO 时间解析**：`presign_get` 从 baas 响应 ISO 8601 `expires_at` 解析为 unix 秒填 `PresignGetTicket.expires_at`；
  `download_url`(=`share_url`) 透传。
- 因为 crate 独立于 BCS 仓库，其测试在插件 crate 仓库内独立运行；BCS 仓库内对 baas 路径的端到端
  验证依赖 `FakeStoragePlugin`（`bcs-storage-api` 内）注入，不强制真实 baas。
- **分段（multipart, v1）**：`prepare_upload` 返回 `PreparedUpload.client_target = Direct{Multipart, parts[]}`
  （多 part 真 OSS 直传 URL，BCS 原样返客户端、不经 `stream_upload`）、客户端逐 part 直传 OSS、
  `complete_upload` baas 合并分片后直达 `DONE`、`abort_upload` cancel + 中止 OSS 会话。与单片同套往返断言。

> 分段上传在 v1 即实现（见上 `prepare_upload`/`complete_upload`/`abort_upload` 行）。`part_number: Option<u16>`
> 已是 trait 签名的一部分。`object_handle` multipart 形态仅多 `type` DIAG 字段，定位仍只靠 `transfer_id`。
