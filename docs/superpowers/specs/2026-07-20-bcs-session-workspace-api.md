# BCS 会话工作区 — API 参考

**日期：** 2026-07-20
**配套设计文档：** `2026-07-20-bcs-session-workspace-design.md`

本文档定义 BCS 会话工作区的三类接口：

1. **对外 HTTP API** —— 面向 bot/human 客户端
2. **对外 CLI API** —— `bcs session file ...`
3. **对内插件 API** —— 存储后端需实现的 `StoragePlugin` trait

## 通用约定

- 所有路径都以 `/sessions/{sid}/...` 为会话作用域前缀。
- 鉴权：bot 使用 `Authorization: Bearer <bot-token>`；human 使用 cookie/OAuth
  （通过现有 `resolve_group_chat_caller` 解析，与现有会话路由一致）。
- 所有 upload/download/list/delete 都要求 caller 是会话参与者（或拥有作为参与者的 bot）。
  delete 另外允许会话创建者 / group driver bot。
- 所有 JSON 请求/响应 body 均为 `Content-Type: application/json`；上传字节端点
  `PUT .../content` 接收原始字节 body（非 JSON）。
- 时间戳为 Unix epoch 秒（u64），对齐现有 BCS 领域类型。
- 大小单位为字节。
- `ActorRef = { "actor_kind": "Bot"|"Human", "actor_id": "<string>" }`。
- `object_handle` 为后端特定的不透明字符串（`UploadHandle`/`StorageHandle` 的序列化形式），
  **仅持久化于 BCS DB 行内部，不透出给客户端**；客户端所有操作以 `file_id` 寻址。
- 字段可空性：`sha256` 为可选 —— **v1 不做内容完整性校验，`sha256` 恒为 `null`（占位字段）**；
  未来后端可在 `complete_upload` 返回 sha256 时透出。客户端必须按可空解析，不得将 `sha256` 视为必填。

### `FileStatus` 状态机

`FileStatus = "Pending"|"Ready"|"Deleting"|"Failed"`。

| 状态 | 产生操作 | 暴露给客户端 | v1 是否出现 | 含义 |
|---|---|---|---|---|
| `Pending` | `POST /files`（prepare） | 是 | 是 | 已 prepare，等 `PUT .../content` + `complete` |
| `Ready` | `POST /complete` 成功 | 是 | 是 | 已可下载/删除 |
| `Failed` | `complete`/上传后端失败 | 是 | 是 | 上传/后端失败；客户端可 `DELETE` 清理后重传 |
| `Deleting` | （异步批量删除标记） | 否（内部） | **否**（v1 同步删除不置此态） | 仅未来「先 mark Deleting，后台批量 sweep」场景使用；v1 `DELETE` 同步完成后端删除+删行，直接 204 |

客户端通过 `GET /.../{file_id}` 与 `GET /.../files`（list）可见 `Pending`/`Ready`/`Failed`，
v1 不会看到 `Deleting`（删除接口同步返回 204 或 502）。

### `SessionFile` 资源（多数接口返回）

```json
{
  "file_id": "01HZX...",
  "session_id": "g1:a1b2c3d4",
  "file_name": "report.pdf",
  "mime_type": "application/pdf",
  "size": 1048576,
  "sha256": null,
  "owner": { "actor_kind": "Human", "actor_id": "human_327325" },
  "storage_backend": "baas",
  "status": "Ready",
  "created_at": 1721462400,
  "updated_at": 1721462405
}
```

`sha256` 可空（见上）。`storage_backend` 对应 `StoragePlugin::backend_name()`（`"local"` / `"baas"` / …）。

### 错误响应体（对标现有 `session_error_to_response`）

```json
{ "error": "<ERROR_CODE>", "message": "<可读说明>" }
```

HTTP 状态码跟随错误码（见各端点说明）。通用错误码：

| code | http | 含义 |
|---|---|---|
| `UNAUTHORIZED` | 401 | caller 未解析 |
| `FORBIDDEN` | 403 | 非会话参与者，或无删除/取消权限 |
| `SESSION_NOT_FOUND` | 404 | `sid` 不存在 |
| `FILE_NOT_FOUND` | 404 | 当前会话下 `file_id` 不存在 |
| `INVALID_TRANSITION` | 409 | 生命周期**转换动作**（upload/complete/abort/delete）在当前状态下不合法：对非 `Pending` 文件重复 PUT、对已 complete/cancel 的文件再 complete、`complete` 时字节尚未上传等 |
| `PAYLOAD_TOO_LARGE` | 413 | 超过 `max_size`（= min(BCS `max_file_size` 配置, 后端 `max_object_size`)，bootstrap 阶段静态化）。注意 100MB 仅是单片/分段阈值，超 100MB 走分段而非 413；仅超 `max_size` 才 413 |
| `INVALID_STATE` | 422 | 资源状态不满足**读取/下载**前提：如对非 `Ready` 文件执行 `GET .../content` |
| `UNSUPPORTED_BACKEND` | 501 | 后端不支持所请求能力（如本地后端的 `presign_get` 下载预签名） |
| `STORAGE_BACKEND` | 502 | 后端操作失败（BCS 对存储后端为网关角色，统一 502；不泄漏后端内部信息） |

---

# 1. 对外 HTTP API

Base：`http://{bcs-host}/sessions/{sid}/files`

## 1.1 查询能力 — `GET /sessions/{sid}/files/capabilities`

可选。客户端可据此预判**上传/下载**字节是否直连后端（不经 BCS）。

**响应 200：**
```json
{
  "storage": "baas",
  "presign_upload": true,
  "presign_download": true,
  "max_size": 104857600
}
```

- `storage`：存储后端名称，对应 `StoragePlugin::backend_name()`（`"local"`/`"baas"`/…）。
- `presign_upload=true`：prepare 返回的 `upload_url` 是后端**真直传 URL**（如 OSS），客户端 PUT 直连后端、字节不经 BCS（baas/OSS）。需要客户端网络可达 OSS。
- `presign_upload=false`（local）：`upload_url` 是 BCS 代理 `PUT .../content`，字节经 BCS。
- `presign_download=true`：`GET .../content` 会 302 跳转到后端签名 URL，字节不经 BCS。
- `presign_download=false`（local）：`GET .../content` 由 BCS 流式返回 body。
- `max_size` = min(BCS `max_file_size` 配置, 后端 `capabilities().max_object_size`)，在 bootstrap
  阶段计算并注入 `SessionFileService`，运行时不再动态调用 `capabilities()`。**`capabilities()` 必须廉价、同步、无 IO**，返回构造期预计算的值；baas 任何 probe（`max_object_size` 等）在插件 `async fn new()` 构造时完成，不得在 `capabilities()` 内做阻塞 IO。
  注意 100MB 仅是单片/分段阈值（`multipart_threshold`），超 100MB 走分段而非拒绝；仅超 `max_size` 才 `413`。

## 1.2 发起上传（prepare） — `POST /sessions/{sid}/files`

三阶段上传第一步。创建 `Pending` 的 `SessionFile` 行，返回 BCS 自有的上传链接。

**请求：** `application/json`
```json
{
  "file_name": "model.bin",
  "size": 524288000,
  "mime_type": "application/octet-stream"
}
```

`size` 是权威值，后端据此在单片与分段间自动分流（阈值由后端 `capabilities().max_object_size`
与分段阈值决定，对标 baas 的 `MULTIPART_THRESHOLD`，默认 100 MB）。客户端无需选择模式。
`mode` wire 值为小写字符串 `"single"`/`"multipart"`（Rust 枚举 `UploadMode::{Single, Multipart}` 经 serde rename 为小写）；客户端按字符串匹配，不得按枚举名大小写解析。

### 1.2.a 单片上传（`size` < 分段阈值）

**响应 201：**
```json
{
  "file_id": "01HZX...",
  "mode": "single",
  "upload_url": "http://{bcs-host}/sessions/g1:a1b2c3d4/files/01HZX.../content",
  "method": "PUT",
  "expires_at": 1721466000
}
```

客户端一次 PUT 全部字节到 `upload_url`，再调 `complete`。

### 1.2.b 分段上传（`size` ≥ 分段阈值，**v1 即支持**）

> `size ≥ multipart_threshold`（默认 100 MB，对标 baas `MULTIPART_THRESHOLD`）时自动走分段，
> v1 即实现（baas 默认 100MB 阈值自动分流，超 100MB 必须可传）。

**响应 201：**
```json
{
  "file_id": "01HZX...",
  "mode": "multipart",
  "method": "PUT",
  "part_size": 10485760,
  "part_count": 50,
  "expires_at": 1721466000,
  "parts": [
    { "part_number": 1, "upload_url": "..." },
    { "part_number": 2, "upload_url": "..." }
  ]
}
```

`part_count = ceil(size / part_size)`；一次 prepare 返回所有分片的 `upload_url`（`method`/`expires_at`
在最外层，各 part 共用），可并行上传。`upload_url` 指向随后端能力：presign 后端（baas/OSS）为后端真
直传 URL（客户端直传后端、字节不经 BCS）；local 后端为 BCS 代理 `PUT .../content?part={n}`。客户端
PUT 各分片后调一次 `complete`，由 `StoragePlugin::complete_upload` 在后端组装（baas/OSS：`list_parts`/
组装；local：按 `part_number` 顺序拼接段文件），客户端无需收集 ETag。**分段上传须所有分片 PUT 完成后再 `complete`；缺段或累计 size ≠ prepare `size` 时 `complete` 返 `409 INVALID_TRANSITION`（字节尚未上传完全），local 在 `complete_upload` 校验各段存在与累计大小。**`part_number` 为 `u16`（1–65535）；后端 `part_size` 选择须保证 `part_count ≤ 65535`，否则 prepare 返 `413 PAYLOAD_TOO_LARGE`。中途可 `DELETE` 取消
（后端 abort 分段会话）。

### 通用约定

`upload_url`（单片）与 `parts[].upload_url`（分段）始终由 BCS prepare 返回，但其**指向**随后端能力而异，
客户端协议不变（始终 PUT 到该 URL 再 `complete`）：
- **presign 上传后端**（baas/OSS，`supports_presign_put=true`）：URL 是后端签发的**真直传 URL**
  （如 `https://oss.../?Sig=...`），客户端 PUT **直连后端、字节不经 BCS**。客户端跨主机 PUT 时**必须**剥离
  `Authorization`（OSS 预签名 URL 自带签名）；这不是「靠默认」的软约束——`bcs-cli` 须显式配置
  reqwest `RedirectPolicy`/请求构建，在直传目标 host 与 BCS 不同时不发送 Bearer，并有回归测试断言跨主机请求不含 `Authorization`。
- **代理后端**（local，`supports_presign_put=false`）：URL 是 BCS 自有的 `PUT .../content`，字节经 BCS。

要求客户端能直传 OSS 的前提：客户端网络可达 OSS（baas「第四通道」前提）；仅能连 BCS 的客户端用 local 后端。
所有上传链接在 `expires_at` 前有效；超时未上传可用 `DELETE` 取消后重新发起。
`expires_at` = BCS 给出的上传链接/句柄过期时间（取 `ttl_secs` 配置与后端签发 URL 过期时间的更早者）；
超时未 `complete` 的 `Pending` 文件由后台 sweep 转为 `Failed` 并 `abort_upload` 清理后端。**v1 包含一个最小的 `Pending` 超时 sweep**（粗粒度定时器：扫描 `Pending` 且 `expires_at` 已过期的行 → 转 `Failed` + `abort_upload`），作为「兜底」；后端孤儿对象（无对应元数据行的对象）逐项对账 sweep 延后至 v1 之后。`Failed` 后再 `complete` 收到 `INVALID_TRANSITION`（409），客户端可 `DELETE` 清理重传。

**错误：** 超 `max_size` 前置校验拒返 `413 PAYLOAD_TOO_LARGE`（`max_size` = min(`max_file_size`,
后端硬上限)，**不是** 100MB 截断 —— 100MB 仅是单片/分段阈值，超 100MB 走分段而非拒绝）；非参与者返 `403 FORBIDDEN`。

## 1.3 上传字节 — `PUT /sessions/{sid}/files/{file_id}/content`

三阶段第二步，**仅代理后端（local，`supports_presign_put=false`）使用**：客户端把原始字节 PUT 到
1.2 返回的 `upload_url`（即本端点，BCS 自有）。BCS 流式接收并交给 `StoragePlugin::stream_upload`：
本地直接落盘到临时段文件。单片上传时 `part_number = None`；分段上传时 URL 带 `?part={n}`，BCS 解析后
以 `Some(n)` 传入 `stream_upload`，各分片独立落盘，最后由 `complete_upload` 在本地按序拼接组装。

**presign 上传后端（baas/OSS，`supports_presign_put=true`）不走此端点**：1.2 返回的 `upload_url`
已是后端真直传 URL（如 OSS），客户端直接 PUT 到后端、字节不经 BCS，本端点对这类上传不被调用。BCS
在 `complete` 时凭 `object_handle` 里的 `transfer_id` 等定位信息调用后端完成。

与下载端点 `GET /sessions/{sid}/files/{file_id}/content` 同路径、不同方法。

**请求：** 原始字节 body；`Content-Length` 为文件大小；`Content-Type` 可选（按 prepare 的 mime）。

**响应 202：**
```json
{ "file_id": "01HZX...", "status": "Pending" }
```

文件仍为 `Pending`，待 `complete` 转 `Ready`。重复 PUT 同一 `file_id`：`409 INVALID_TRANSITION`。

**错误：** `404 FILE_NOT_FOUND`；`409 INVALID_TRANSITION`（文件非 `Pending`）；
`413 PAYLOAD_TOO_LARGE`（超 `max_size` 或 `Content-Length` 与 prepare 的 `size` 不符）；
`502 STORAGE_BACKEND`（后端接收失败，不泄漏后端内部信息）。

## 1.4 完成上传 — `POST /sessions/{sid}/files/{file_id}/complete`

三阶段第三步。
- 本地：finalize（fsync + 原子改名到终态 key），校验 size（v1 不校验 sha256，见通用约定）；
- baas：调 baas `complete` + 轮询到 `DONE`，取回 `StorageObjectMeta`。

**请求 body：** 空 `{}`。

**响应 200：** `SessionFile`，`status: "Ready"`。

**错误：** `404 FILE_NOT_FOUND`；`409 INVALID_TRANSITION`（已 complete/cancel，或字节尚未上传）；
`502 STORAGE_BACKEND`（后端无法确认对象）。

## 1.5 删除文件 / 取消上传 — `DELETE /sessions/{sid}/files/{file_id}`

单一入口，按 `file_id` 当前 `status` 自动分流三种语义：

### 1.5.a 删除文件（`status: Ready`）

会话工作区里一个已完成上传的文件，从后端存储和 BCS 元数据中移除。

**后端行为：** 调 `StoragePlugin::delete`（`StorageHandle` 由 `object_handle` 重建）
- 本地：unlink 终态文件（幂等，文件已不存在返 `Ok`）；
- baas：`DELETE /staging?key={oss key}`（仅终态 ticket 有效；BCS 保证调用前文件 `Ready`=`DONE`，
  baas `404 OSS_OBJECT_NOT_FOUND` 映射为 `Ok`，幂等）。

后端删除成功后删除 `bcs_session_files` 元数据行（先后端再行，避免幽灵行；若后端成功而行删除
失败，留下待对账的孤儿对象，由解耦 sweep 收敛）。

### 1.5.b 取消上传（`status: Pending` 或 `Failed`）

一个尚未 `complete`（`Pending`）或上传/后端已失败（`Failed`）的文件，中止并清理。`Failed` 本质
是尚未完成上传，**按 `Pending` 处理**走 `abort_upload` 清理后端 staging/临时文件再删行——不走
`delete`/`DELETE /staging`，否则 baas 可能找不到 staging 对象。

**后端行为：** 调 `StoragePlugin::abort_upload`（`UploadHandle` 由 `object_handle` 重建）
- 本地：unlink 临时 `.part` 段文件（单片）/ 所有分片段文件（分段），幂等；
- baas：`DELETE /upload-url/{transfer_id}`，ticket 转 `CANCELLED` 终态（幂等）。

随后删除 `bcs_session_files` 元数据行。

### 权限

删除与取消权限一致：**上传者**（human 上传者；若上传者是 bot 则需拥有该 bot）**或** 会话创建者 /
该 group 的 driver bot。镜像现有 `delete_session` 规则。会话普通成员可上传/下载/列出，但不可
删除他人文件。

### 响应与错误（BCS 元数据层幂等）

**响应 204：** 无 body。`DELETE` 在 **BCS 元数据层幂等**：对已删除/已取消的 `file_id` 重复调用
也返回 204，不报 404。

- **行存在**：按 `status` 走 `delete`/`abort_upload`（后端对象已不存在时插件返 `Ok`），删行后返 204。
- **行已不存在**：**直接返 204，不探测后端** —— 因为 `object_handle` 随 DB 行一同消失，无 handle
  无法重建 `StorageHandle`/`UploadHandle`，也无法调用后端 `delete`/`abort_upload`。此时若后端仍有
  残留对象，由 orphan sweep（设计文档「孤儿对账」）收敛。

即：本接口不引入 tombstone/软删除表，重复删除在 BCS 元数据层幂等（返 204），后端残留由 sweep
兜底；不在「行已删除」后做无法实现的假探测。

**错误：**
- `403 FORBIDDEN` —— 非上传者且非会话创建者/driver；
- `502 STORAGE_BACKEND` —— 后端删除/取消失败（不泄漏后端内部信息，可重试）；
- （行存在/已不存在均不返回 `404 FILE_NOT_FOUND`：存在则 204，不存在的 `file_id` 也 204。）

## 1.6 列文件 — `GET /sessions/{sid}/files`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prefix` | string | 否 | 文件名前缀过滤 |
| `limit` | int | 否 | 默认 100，最大 1000 |
| `marker` | string | 否 | 不透明分页游标，取自上次的 `next_marker` |

**响应 200：**
```json
{
  "items": [ { /* SessionFile */ }, { /* SessionFile */ } ],
  "truncated": false,
  "next_marker": null
}
```

- 默认按 `created_at` **升序**返回（保证分页稳定、不漏页不重页）；`marker` 为不透明游标，
  客户端原样回传，不得解析。
- `prefix` 对 `file_name` 做**大小写敏感**的前缀过滤。
- 列表权威来源为 BCS 自身 DB，从不取自后端。
- 同一会话**允许存在多个同名文件**：`file_id` 是唯一标识（DB 唯一索引在 `(env, session_id, file_id)`，
  不在 `file_name`）。客户端不得按 `file_name` 去重。

## 1.7 查询文件元数据 — `GET /sessions/{sid}/files/{file_id}`

**响应 200：** `SessionFile`。

**错误：** `404 FILE_NOT_FOUND`。

## 1.8 下载字节 — `GET /sessions/{sid}/files/{file_id}/content`

- 预签名后端（`supports_presign_download=true`）：**302 跳转** 到短期有效的预签名 `download_url`。字节在客户端 <-> 后端直接流转。
- 本地后端（`supports_presign_download=false`）：流式返回 body，含 `Content-Type`、`Content-Length`、
  `Content-Disposition: attachment; filename="..."`。

可选查询 `?ttl=<秒>`（默认按后端；baas `share-link` 60–604800）控制预签名 URL 有效期。

**重定向行为说明**：GET 的 302/307 会被标准 HTTP 客户端自动跟随（reqwest 默认跟随、`curl -L`、
浏览器原生 GET），客户端最终从后端（OSS）取字节，对客户端透明、无需适配。重定向目标是跨主机
（OSS 而非 BCS），规范客户端**必须**在跨主机重定向时**剥离 `Authorization` 头**——这不靠「reqwest 默认如此」承担，`bcs-cli` 须显式配置 `RedirectPolicy` 在跨 host 跳转时不转发 sensitive 头，并有回归测试断言跳转到 OSS host 时请求不含 Bearer。
Bearer token 不泄漏给 OSS；OSS 预签名 URL 自带 query 签名，自洽。v1 客户端为 bot/CLI，302 完全够用；
若后续 Web UI 经浏览器 `fetch` 直连遇到 CORS，可对那类客户端让 BCS 走中继回退（流式返回 body）。

**错误：** `404 FILE_NOT_FOUND`；文件非 `Ready` 返 `422`。

> 文件的删除与上传取消见 1.5。

## 1.9 分享文件（share link）

为 `Ready` 的文件生成一个**不校验会话权限**的分享链接：拿到链接者凭 token 即可下载/查元信息，
过期失效。token 为无状态签名（HMAC-SHA256，对标 invite/register token 方案，但**使用独立密钥**
`[session_files.share] token_secret`，不复用 invite 密钥）。**token payload = `{ file_id, exp, version }`**
（**不含 `session_id`**——`file_id` 全局唯一，消费端凭 `file_id` 查行取得 `session_id` 与路径 `{sid}` 比对）。无状态意味着**不可撤销**（未过期前一直
有效）；撤销方式 = 删除文件（删除后 token 验证时查 DB 行失败 → `404`）或等自然过期。无 DB 表、
无活跃分享列表。

### 1.9.a 生成分享 — `POST /sessions/{sid}/files/{file_id}/share`

**权限：** 与 `DELETE` 一致 —— 上传者（human 上传者；若上传者是 bot 则需拥有该 bot）或会话创建者 /
该 group 的 driver bot。普通成员不可生成他人文件的分享。

**请求 body：** `application/json`，可空
```json
{ "ttl_seconds": 86400 }
```
`ttl_seconds` 可选，默认 `[session_files.share] default_ttl_seconds`（86400 = 24h），范围 60–604800
（1 分钟至 7 天）。

**响应 201：**
```json
{
  "share_url": "http://{bcs-host}/sessions/g1:a1b2c3d4/shared-file/content?token=eyJ...",
  "share_token": "eyJ...",
  "expires_at": 1721466000
}
```

`share_url` 指向 BCS 自有的 `GET .../shared-file/content?token=` 端点（见 1.9.c），可直接分发/点击。
文件需 `Ready`，否则 `422 INVALID_STATE`。

### 1.9.b 查询分享文件元信息 — `GET /sessions/{sid}/shared-file?token={token}`

**权限：无** —— 仅校验 token 签名 + 过期 + `{sid}` 与文件 `session_id` 一致，**跳过会话成员鉴权**。

```json
{
  "file_id": "01HZX...",
  "session_id": "g1:a1b2c3d4",
  "file_name": "report.pdf",
  "mime_type": "application/pdf",
  "size": 1048576,
  "sha256": null,
  "owner": { "actor_kind": "Human", "actor_id": "human_327325" },
  "storage_backend": "baas",
  "status": "Ready",
  "created_at": 1721462400,
  "updated_at": 1721462405
}
```

返回裁剪后的 `SessionFile`（`object_handle` 为内部句柄，不透出任何客户端——分享响应与普通响应均不含）。`{sid}`
此处仅作路径命名空间与一致性校验（token 解出的 `file_id` 查行后，若行 `session_id` ≠ 路径 `{sid}`
返 `404`），不参与鉴权。

### 1.9.c 下载分享文件字节 — `GET /sessions/{sid}/shared-file/content?token={token}`

**权限：无**（同 1.9.b 的校验，跳过成员鉴权）。字节路由复用 1.8：
- 预签名后端：**302 跳转** 到 `StoragePlugin::presign_get` 签名 URL，**有效期取 token 过期与后端
  预签名 TTL 的更早者**（确保分享链接过期后该 URL 亦不可用）。跨主机重定向**必须剥离 `Authorization`
  头（同 §1.8：`bcs-cli` 显式配置 `RedirectPolicy`，不依赖默认）。
- 本地后端：流式返回 body，含 `Content-Type`/`Content-Length`/`Content-Disposition: attachment;
  filename="..."`。

### 错误（1.9.b / 1.9.c 共用）

- `401` —— token 签名无效 / 版本不支持（`InvalidSignature`/`UnsupportedVersion`）；
- `410 GONE` —— token 已过期（`Expired`，与 invite 的 `Expired → 410` 一致）；
- `404 FILE_NOT_FOUND` —— 文件已删/`{sid}` 与文件 `session_id` 不一致/`file_id` 不存在；
- `422 INVALID_STATE` —— 文件非 `Ready`（如中途转 `Failed`，理论上 `Ready` 才能生成分享）；
- `502 STORAGE_BACKEND` —— 后端取字节失败。
- 1.9.a 另有：`403 FORBIDDEN`（非上传者/创建者/driver）、`404 FILE_NOT_FOUND`、`422 INVALID_STATE`。

---

# 2. 对外 CLI API

`bcs-cli`（clap-derive），扩展 `SessionCommands`。token/URL 解析遵循现有 `bcs` CLI 约定
（`--token`/env/`session.json`，`--url`/env/`127.0.0.1:21000`）。默认 JSON 输出；
`--no-json` 为人读。

```
bcs session file <SUBCOMMAND>
```

子命令：

### 2.1 `upload` —— 上传（自动封装三阶段）
```
bcs session file upload \
  --session <sid> \
  --path <local-file> \
  [--name <override-filename>] \
  [--mime <mime-type>] \
  [--token <t>] [--url <bcs-url>] [--no-json]
```
内部自动串起三阶段：`POST /files`（prepare）→ PUT 字节到 `upload_url` → `POST /complete`，
客户端只需指定本地文件路径。打印最终 `SessionFile`（`Ready`）。上传中失败会尝试 `DELETE`
取消再退出。上传是 CLI 唯一的上传入口，不单独暴露 `upload-url`/`complete` 低阶命令，保持
接口简单；需要分阶段控制的脚本可直接走 HTTP 三阶段接口。

### 2.2 `list` —— 列出工作区文件
```
bcs session file list \
  --session <sid> \
  [--prefix <p>] [--limit <n>] [--marker <m>] \
  [--token <t>] [--url <bcs-url>]
```
打印 `{items, truncated, next_marker}`（`--no-json` 下为表格）。

### 2.3 `download` —— 下载文件字节
```
bcs session file download \
  --session <sid> \
  --file-id <id> \
  [--out <local-path>] \
  [--ttl <seconds>] \
  [--token <t>] [--url <bcs-url>]
```
跟随预签名跳转并流式写到 `--out`（默认当前目录，文件名取自元数据 `file_name`）。打印保存路径。

### 2.4 `delete` —— 删除/取消
```
bcs session file delete --session <sid> --file-id <id> [--token <t>] [--url <bcs-url>]
```
按服务端文件状态生效：`Pending`/`Failed` 时取消上传，`Ready` 时删除文件（与 HTTP §1.5 分流一致）。成功时打印空/确认信息。

### 2.5 `share` —— 生成分享链接
```
bcs session file share --session <sid> --file-id <id> [--ttl <seconds>] [--token <t>] [--url <bcs-url>]
```
调 `POST .../files/{file_id}/share`，打印 `{share_url, share_token, expires_at}`。分享链接是可直接
分发/点击的裸 URL，下载无需 CLI 子命令（浏览器/curl/`GET .../shared-file/content?token=`）。

### 2.6 `capabilities` —— 查询后端能力
```
bcs session file capabilities --session <sid> [--token <t>] [--url <bcs-url>]
```
打印 `{storage, presign_upload, presign_download, max_size}`，可选用以预判上传/下载字节是否直连后端。

---

# 3. 对内插件 API —— `StoragePlugin`

crate：`bcs-storage-api`（位于 `crates/plugin-api/`，相对 `src/bcs/`）。后端实现该 trait；
`SessionFileService` 只调用该 trait，不依赖具体后端。crate 同时提供
**`FakeStoragePlugin`**（in-memory 实现），覆盖三阶段上传 / 下载 / 删除 / 幂等 / 分段路径，供
service 单测与 HTTP 层测试注入复用（对标 `DbPlugin`/`CachePlugin` 的 fake 模式）。

```rust
use async_trait::async_trait;

#[async_trait]
pub trait StoragePlugin: Send + Sync + 'static {
    // Stable backend name ("local", "baas", "oss", ...). Stored on each
    // SessionFile.storage_backend for auditability.
    fn backend_name(&self) -> &'static str;

    // Advertised capabilities. Drive both upload and download byte routing.
    fn capabilities(&self) -> StorageCapabilities;

    // --- upload: BCS three-stage (prepare -> [stream] -> complete) ---
    // Byte path is capability-driven:
    //   supports_presign_put = true  (baas/OSS): prepare returns a direct
    //     backend upload URL; the client PUTs bytes directly to the backend;
    //     stream_upload is NOT called by BCS (bytes bypass BCS).
    //   supports_presign_put = false (local): BCS synthesizes its own
    //     PUT .../content proxy URL; client PUTs bytes to BCS; BCS feeds them
    //     via stream_upload. complete_upload finalizes either way.
    // The UploadHandle returned by prepare is serialized into
    // SessionFile.object_handle and reconstructed for complete/abort
    // (HTTP is stateless across requests). For presign_put backends the
    // per-part direct URLs are returned to the client in the prepare HTTP
    // response only — NOT persisted in object_handle (keeps the row small;
    // see § UploadHandle.backend_handle shapes).

    /// Reserve a staging upload. Local: open temp file(s). Baas/OSS: call
    /// backend presign to get a direct upload URL (single) or per-part URLs
    /// (multipart) + a transfer id. Returns the client-facing upload target
    /// (direct URL for presign backends; None for local, where BCS synthesizes
    /// its proxy URL) and the persistable handle.
    async fn prepare_upload(
        &self,
        req: UploadPrepareRequest,
    ) -> Result<PreparedUpload, StorageError>;

    /// Feed client bytes into staging as a stream. Called ONLY by
    /// non-presign backends (local) where bytes arrive at BCS's `PUT .../content`
    /// proxy endpoint. Presign backends (baas/OSS) never invoke this — the
    /// client PUTs directly to the backend URL issued in prepare_upload.
    ///
    /// `part_number`: 单片上传传 `None`；分段上传传对应分片编号（1-based）。v1 即支持分段。
    async fn stream_upload(
        &self,
        handle: &UploadHandle,
        part_number: Option<u16>,
        body: ByteStream,
    ) -> Result<(), StorageError>;

    /// Finalize the upload. Local: fsync + atomic rename / concat parts. Baas:
    /// POST /upload-url/{id}/complete then poll /transfers until DONE.
    async fn complete_upload(
        &self,
        handle: &UploadHandle,
    ) -> Result<StorageObjectMeta, StorageError>;

    /// Cancel an in-progress (Pending) upload. Local: unlink temp (single) or
    /// all per-part temp segments (multipart). Baas: DELETE /upload-url/{id}.
    /// Idempotent.
    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError>;

    // --- download / delete (operate on a finalized Ready object) -----------

    /// Streaming read of a finalized object. Used when supports_presign_download
    /// is false, or as a fallback.
    async fn get_stream(
        &self,
        handle: &StorageHandle,
    ) -> Result<ByteStream, StorageError>;

    /// Issue a short-lived presigned download URL. Used when
    /// supports_presign_download is true (BCS GET .../content returns 302 to it).
    async fn presign_get(
        &self,
        handle: &StorageHandle,
        ttl_secs: u64,
    ) -> Result<PresignGetTicket, StorageError>;

    /// Permanently remove a finalized object. Idempotent: missing object is Ok.
    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError>;

    async fn health_check(&self) -> Result<StorageHealth, StorageError>;
}
```

### 辅助类型

```rust
pub struct StorageCapabilities {
    pub supports_presign_put: bool,     // upload: true → client PUTs direct to backend (baas/OSS); false → BCS proxy (local)
    pub supports_presign_download: bool, // download: true → GET .../content 302s; false → BCS streams
    pub supports_stream_put: bool,       // v1 所有后端为 true；保留以备未来不支持流式上传的后端
    pub supports_stream_get: bool,       // v1 所有后端为 true；保留以备未来不支持流式下载的后端
    pub max_object_size: u64,            // backend hard limit; BCS also enforces its own max_file_size
}

pub struct UploadPrepareRequest {
    pub key: String,                // BCS-derived final key, e.g. session-files/{env}/{sid}/{file_id}/{file_name}
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,                  // authoritative; backends also enforce max_object_size / multipart threshold
    pub ttl_secs: u64,              // staging / upload_url lifetime
}

/// Result of prepare_upload. `client_target` is what BCS hands to the client
/// as the `upload_url`/`parts[].upload_url` (§ 1.2): a direct backend URL for
/// presign_put backends (bytes bypass BCS), or None for local where BCS
/// synthesizes its own `PUT .../content` proxy URL. `handle` is what BCS
/// persists to object_handle and reconstructs for complete/abort (it must NOT
/// carry ephemeral per-part direct URLs that the client already received —
/// keep the persisted row small).
pub struct PreparedUpload {
    pub handle: UploadHandle,
    pub client_target: ClientUploadTarget,
    pub expires_at: u64,
}

pub enum ClientUploadTarget {
    /// presign_put backend (baas/OSS): the direct backend URL(s) the client
    /// PUTs to. BCS passes these to the client as-is; bytes never touch BCS.
    Direct {
        mode: UploadMode,                       // Single | Multipart
        // single: one URL; multipart: per-part URLs (1-based)
        url: Option<String>,                    // Some for Single
        parts: Option<Vec<UploadPartUrl>>,      // Some for Multipart
        part_size: Option<u64>,
        part_count: Option<u32>,
    },
    /// non-presign backend (local): no direct URL; BCS serves its own
    /// `PUT .../content` endpoint and relays bytes via stream_upload.
    /// (BCS synthesizes the proxy URL itself — the plugin doesn't know BCS's host.)
    ProxyViaBcs,
}

pub struct UploadPartUrl { pub part_number: u16, pub url: String }

pub enum UploadMode { Single, Multipart }

/// Backend-specific, serializable handle persisted as SessionFile.object_handle.
/// Reconstructed for stream_upload (local only) / complete_upload / abort_upload / delete.
/// For presign_put backends this carries ONLY the durable locator (transfer_id,
/// oss_key, type) — NOT the per-part OSS direct URLs (those go to the client via
/// PreparedUpload.client_target and are not persisted).
pub struct UploadHandle {
    pub backend: &'static str,
    pub key: String,
    // local (single):    { temp_path, final_path }
    // local (multipart): { final_path, parts: [{ part_number, temp_path }] }
    // baas  (single):    { transfer_id, type:"SINGLE",  oss_key, expires_at }
    // baas  (multipart): { transfer_id, type:"MULTIPART", upload_session_id, oss_key, expires_at }
    pub backend_handle: serde_json::Value,
    pub expires_at: u64,
}

/// Handle for a finalized (Ready) object — derived from UploadHandle after
/// complete_upload, or reconstructed from SessionFile.object_handle.
pub struct StorageHandle {
    pub backend: &'static str,
    pub key: String,
    pub backend_handle: serde_json::Value,
}

pub struct PresignGetTicket {
    pub download_url: String,
    pub expires_at: u64,
}

pub struct StorageObjectMeta {
    pub key: String,
    pub size: u64,
    pub sha256: Option<String>,      // backends that can verify integrity return it
}

pub struct StorageHealth {
    pub ok: bool,
    pub detail: Option<String>,
}

// ByteStream is an async byte stream over bytes::Bytes, matching the streaming
// types already used in bcs-http/bcs-ws. This alias is the finalized contract
// type — plugin impls use it for stream_upload input and get_stream output.
pub type ByteStream = Box<dyn ByteStreamTrait + Send + Unpin>;

pub trait ByteStreamTrait: futures_core::Stream<Item = Result<bytes::Bytes, std::io::Error>> {}
```

### 错误类型

对标 `DbError` 的后端无关形态；lint 禁止在 message 中泄漏后端内部信息。

```rust
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
```

### 契约测试（位于 `bcs-storage-api`，所有后端共用）

每个 `StoragePlugin` 实现须通过同一套用例（对标 `bcs-test-support`）：
- `prepare_upload` 返回 `PreparedUpload`；若 `supports_presign_put`，`client_target` 为 `Direct`
  （BCS 把该 URL 交给客户端、客户端直传后端、字节不经 BCS）；若非，`client_target` 为 `ProxyViaBcs`
  （BCS 用 `stream_upload` 接收字节）。
- 非-presign 后端（local）：`prepare_upload` -> `stream_upload(handle, None, 已知字节)` ->
  `complete_upload` 得 `Ready`，`StorageObjectMeta.size` 一致。
- presign 后端（baas/OSS）：`prepare_upload` -> （客户端直接 PUT 到 `client_target` URL，不经 BCS）->
  `complete_upload` 得 `Ready`；往返字节一致。`stream_upload` 不被调用（调用应返 `Unsupported` 或不被断言）。
- 分段契约（v1）：单 part（local）用 `stream_upload(handle, Some(n), part_bytes)` 多次；
  presign 后端用 `client_target = Direct{ Multipart, parts[] }` 客户端逐 part 直传；后端 `complete_upload`
  组装，往返原始字节（与单片同套测试，仅 size ≥ 阈值）。
- `complete_upload` 后 `get_stream`（或 `supports_presign_download` 时 `presign_get` + GET）
  能往返原始字节
- `abort_upload`（在 `complete_upload` 之前）使对象不存在，后续 `delete`/`get_stream` 返回
  `NotFound`
- `delete` 幂等，且使后续 `get_stream` 返回 `NotFound`
- `delete` 对**已不存在对象**（如先 `abort_upload` 或重复 `delete`）返回 `Ok(())`，作为独立用例
  与上一条并列
- `capabilities()` 的 `backend_name` 一致且非空
- `health_check` 对真实（或 stub）后端返回 ok
- **`capabilities()` 契约**：同步、廉价、无 IO，返回构造期预计算值；后端任何 probe 在插件 `async fn new()` 构造时完成，不在 `capabilities()` 内做阻塞 IO。除 bootstrap 阶段外，`SessionFileService` 不应在请求
  路径上调用 `capabilities()`；`capabilities` 应作为构造时注入的静态值（`max_size` 等在 bootstrap
  固化）。契约测试断言一次 prepare/complete 请求不触发二次 `capabilities()` 调用，且 `capabilities()` 本身不执行 IO（baas 实现的 probe 仅在构造期发生）。

### key 派生约定

`StoragePlugin` 以 key 寻址。BCS 为每个文件派生 `key`，按会话/后端隔离避免冲突，例如
`session-files/{env}/{session_id}/{file_id}/{file_name}`。后端不应假设 key 之外存在层级。
`SessionFile` 行上存储的 `object_handle` 是 `UploadHandle`（上传中）或 `StorageHandle`
（`Ready` 后）的序列化形式，BCS 将其重建后传给 `stream_upload`/`complete_upload`/
`abort_upload`/`delete`/`get_stream`/`presign_get`。

## 3.1 后端：`bcs-storage-local`（`crates/plugins/bcs-storage-local/`）

- `capabilities`：`supports_presign_put = false`（上传字节经 BCS 代理，走 `stream_upload`）、
  `supports_presign_download = false`，`max_object_size` 取**配置项**（默认等于 BCS
  `max_file_size` 或合理上限），不以磁盘剩余空间作为静态 capability（剩余空间动态变化无法在启动时
  固定）；`stream_upload` 中再实际校验磁盘可用空间，不足返 `StorageError::Backend`。
- key 映射到 `$BCS_DATA_DIR/session-files/...`（或配置的 `data_dir`）下的文件。
- `prepare_upload`：返回 `PreparedUpload{ client_target: ProxyViaBcs, handle }`（BCS 据此把
  `PUT {bcs_base}/.../content` 作为客户端 `upload_url`）。单片 `backend_handle` 含
  `{ temp_path, final_path }`；**分段**（size ≥ 阈值）含 `{ final_path, parts: [{ part_number, temp_path }] }`。
  **`temp_path` 必须包含 `file_id`（key 中已含）并附加随机后缀**，分段时各段路径含 `part_number`，
  保证多客户端/多 worker 同 key 并发不冲突（单片 `{data_dir}/{key}.{rand}.part` /
  分段 `{data_dir}/{key}.p{part_number}.{rand}.part`）。v1 即支持分段。
- `stream_upload`：单片写入唯一 temp 文件；分段（`part_number=Some(n)`）写入对应段文件，校验
  累计 size ≤ prepare size。
- `complete_upload`：单片 fsync + 原子改名到 `final_path`；分段按 `part_number` 顺序拼接各段到
  `final_path`（逐段 fsync，最后原子改名），返回 `StorageObjectMeta`。
- `abort_upload`：unlink temp（单片）/ 所有分片段（分段），幂等。
- `get_stream`：打开终态文件流式返回；`presign_get` 返回 `StorageError::Unsupported`；
  `delete`：unlink 终态文件（幂等）。
- 用于开发、测试和单节点部署。

## 3.2 后端：`bcs-storage-baas`（独立 crate，不在本仓库）

baas 后端实现见 **`2026-07-20-bcs-session-workspace-design-baas-plugin.md`**（该插件 crate
独立于 BCS 仓库，仅依赖 `bcs-storage-api` trait crate，在组装根按 `storage_backend = "baas"`
装配）。这里只保留极简摘要供本契约文档自洽：

- `backend_name` = `"baas"`；`capabilities()`: `supports_presign_put = true`（**上传字节不经 BCS**，客户端直传 OSS）、`supports_presign_download = true`。
- 上传走 baas **留存模式**（`POST /upload-url` 不带 `device_path`）：`prepare_upload` 取 baas 签发的
  OSS 直传 URL + `transfer_id`（单片 `type:"SINGLE"`，分段 `type:"MULTIPART"`，baas 据 `file_size` 与
  `MULTIPART_THRESHOLD` 自动分流），返回 `PreparedUpload{ client_target: Direct{...}, handle }` ——
  BCS 把 `client_target` 里的真 OSS 直传 URL 原样交给客户端（§1.2 的 `upload_url`/`parts[].upload_url`），
  客户端直接 PUT 到 OSS、**字节不经 BCS**，`stream_upload` 不被调用。`complete_upload` 调 baas `complete`
  + 轮询 `DONE`（分段 baas 自行 `list_parts` 组装，客户端/BCS 都不收集 ETag）；`abort_upload` 调
  `DELETE /upload-url/{id}`。`object_handle` 只持久化 `transfer_id`/`type`/`oss_key` 等**定位信息**，
  不持久化短命的 per-part OSS 直传 URL（详见 baas 插件文档）。
- 下载：`presign_get` 调 `POST /transfers/{id}/share-link` 取 `share_url`，BCS `GET .../content` 302 到它。
- `delete`（`Ready`）调 `DELETE /staging?key={oss key}`，`404 OSS_OBJECT_NOT_FOUND` 映射为 `Ok`（幂等）。
- `health_check` 仅探测 baas base_url 可达性，不依赖真实 `transfer_id`。
- 会话隔离：`oss_key` 由 BCS 派生、含 `session_id`/`file_id`，不同会话对象路径天然隔离；BCS 列表
  权威来自自身 DB（不用 `GET /staging`），共享同一 service bot 不影响会话隔离性。
- 客户端直传 OSS 要求客户端能网络可达 OSS（baas「第四通道」前提）；仅能连 BCS 的客户端应使用
  `supports_presign_put=false` 后端（local）。跨主机 PUT 时客户端剥离 `Authorization`，OSS 预签名 URL 自带签名。
- 错误映射、`UploadHandle`/`StorageHandle` 形态、完整上传流程、身份/租户、配置、测试、分段细节均见 baas 插件文档。

## 3.3 未来后端（trait 就绪，v2+）

- `bcs-storage-oss`：`supports_presign_put = true` + `supports_presign_download = true`；`prepare_upload`
  发 OSS 直传 URL（单片）/ OSS multipart init（分段）作为 `client_target: Direct{...}`，客户端直传；
  `complete_upload` 调 OSS complete-multipart；`delete` 删对象；`list` 不使用（BCS 从 DB 列）。
- `bcs-storage-nas`：`supports_presign_download = false`；通过挂载的 NFS 路径流式，对标
  `bcs-storage-local`（temp + rename）。
- 三方服务：实现该 trait；`SessionFileService` 及 HTTP/CLI 表面均无需改动。
