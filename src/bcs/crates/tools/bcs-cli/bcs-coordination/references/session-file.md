# BCS Session 文件工作区命令

在同一个 Session 内上传、下载、分享、列举和删除共享文件，支持 bot 与 human 参与者协同操作文件。

## 概念

- **会话工作区（session workspace）** = 一个 Session 内 bot/human 共享的文件存储区
- **file_id** 全局唯一、URL-safe、不透明；同一会话允许同名文件（靠 file_id 区分）
- **FileStatus**：
  - `Pending` — 已 prepare，等待上传完成
  - `Ready` — 上传完成，可下载/分享/删除
  - `Failed` — 上传失败，可删除后重传
- 仅 `Ready` 状态的文件可下载、分享、删除

### 三阶段上传

```
prepare → PUT → complete
```

`upload` 命令自动封装三阶段：
1. `prepare` 创建 file_id 并返回 `upload_url`
2. `PUT` 将文件字节写入 `upload_url`（presign 后端直传 OSS，local 后端经 BCS）
3. `complete` 标记上传完成，状态变为 Ready

失败时 CLI 会尝试 `DELETE` 取消未完成的上传。

### Multipart 上传

- **100MB 是单片/分段阈值（非硬截断）**：超过 100MB 自动走 multipart 串行 PUT（v1；可并行优化为后续）
- multipart 响应的最外层包含 `expires_at` 和 `method` 字段

### 分享链接

- 分享链接使用独立密钥签发，过期前不可撤销
- 删除文件即令分享链接失效
- 返回的 `share_url` 为裸 URL，下载无需 CLI 子命令（直接 HTTP GET 即可）

## 权限

| 操作 | 权限 |
|------|------|
| `upload` | 会话参与者 |
| `download` | 会话参与者 |
| `list` | 会话参与者 |
| `share` | 会话参与者 |
| `delete` | 上传者，或会话创建者，或该 group 的 driver bot |
| `capabilities` | 会话参与者 |

## 命令列表

| 命令 | 必需参数 | 说明 |
|------|----------|------|
| `session file upload` | `--session`, `--path` | 上传本地文件（自动三阶段） |
| `session file list` | `--session` | 列出工作区文件 |
| `session file download` | `--session`, `--file-id` | 下载文件字节 |
| `session file delete` | `--session`, `--file-id` | 删除文件 / 取消上传 |
| `session file share` | `--session`, `--file-id` | 生成分享链接 |
| `session file capabilities` | `--session` | 查询后端能力 |

> **相关 reference**：
> - Session 的创建和管理详见 [session.md](session.md)
> - 文件工作区依附于 Session，需先有 Session 才能操作文件

---

## session file upload - 上传

上传本地文件到 Session 工作区，自动封装三阶段（prepare → PUT → complete）。

```bash
bcs session file upload --session "<session_id>" --path <本地路径> [--name <文件名>] [--mime <MIME 类型>]
```

**参数说明：**

- `--session`: Session ID（格式：`{group_id}:{8_hex}`）
- `--path`: 本地文件路径
- `--name`: 自定义文件名（不指定时使用路径中的文件名）
- `--mime`: MIME 类型（不指定时从扩展名推测）

**示例：**

```bash
# 上传小文件（<100MB，单片上传）
bcs session file upload --session "grp-001:1a2b3c4d" --path ./report.pdf

# 上传大文件（≥100MB，自动 multipart 串行 PUT（v1；可并行优化为后续））
bcs session file upload --session "grp-001:1a2b3c4d" --path ./model.bin --mime application/octet-stream

# 自定义文件名
bcs session file upload --session "grp-001:1a2b3c4d" --path ./data.csv --name "2026-Q3-report.csv"
```

> **注意**：presign 后端（baas/OSS）要求本机/进程网络可达 OSS；仅能连 BCS 的环境应使用 local 后端。跨主机 PUT 到后端 OSS URL 时 Bearer 不应发送（OSS 预签名 URL 自鉴权），`bcs` CLI 已自动处理。

---

## session file list - 列出文件

```bash
bcs session file list --session "<session_id>" [--prefix <前缀>] [--status <状态>] [--limit <数量>] [--offset <偏移>]
```

**示例：**

```bash
# 列出所有文件
bcs session file list --session "grp-001:1a2b3c4d"

# 按前缀筛选
bcs session file list --session "grp-001:1a2b3c4d" --prefix "report"

# 按状态筛选
bcs session file list --session "grp-001:1a2b3c4d" --status "Ready"

# 分页
bcs session file list --session "grp-001:1a2b3c4d" --limit 20 --offset 0
```

---

## session file download - 下载

```bash
bcs session file download --session "<session_id>" --file-id <file_id> [--out <输出路径>] [--ttl <秒数>]
```

**示例：**

```bash
# 下载到默认文件名
bcs session file download --session "grp-001:1a2b3c4d" --file-id "f1a2b3c4"

# 指定输出路径
bcs session file download --session "grp-001:1a2b3c4d" --file-id "f1a2b3c4" --out ./downloaded.pdf

# 设置下载链接有效期
bcs session file download --session "grp-001:1a2b3c4d" --file-id "f1a2b3c4" --ttl 3600
```

> 下载时 CLI 自动跟随 presigned 302 重定向获取文件字节。

---

## session file delete - 删除

```bash
bcs session file delete --session "<session_id>" --file-id <file_id>
```

**示例：**

```bash
bcs session file delete --session "grp-001:1a2b3c4d" --file-id "f1a2b3c4"
```

> 可删除已完成的文件或取消进行中的上传。删除后对应的分享链接立即失效。

---

## session file share - 生成分享链接

```bash
bcs session file share --session "<session_id>" --file-id <file_id> [--ttl <秒数>]
```

**示例：**

```bash
# 使用默认有效期
bcs session file share --session "grp-001:1a2b3c4d" --file-id "f1a2b3c4"

# 设置 24 小时过期
bcs session file share --session "grp-001:1a2b3c4d" --file-id "f1a2b3c4" --ttl 86400
```

**返回示例：**

```json
{
  "share_url": "https://bcs.example.com/files/shared/AbCdEf123456",
  "expires_at": 1716789012345
}
```

> 返回的 `share_url` 为裸 URL，接收方直接 HTTP GET 即可下载，无需 CLI 子命令。分享链接使用独立密钥签发，有效期到期后自动失效；删除源文件也会令分享链接失效。

---

## session file capabilities - 查询能力

```bash
bcs session file capabilities --session "<session_id>"
```

**示例：**

```bash
bcs session file capabilities --session "grp-001:1a2b3c4d"
```

**返回示例：**

```json
{
  "storage": "local",
  "presign_upload": false,
  "presign_download": false,
  "max_size": 5368709120
}
```

> 返回字段含义：
> - `storage`: 后端存储类型（`local` / `baas` / `oss`）
> - `presign_upload`: 是否支持直传后端（为 `true` 时需本机网络可达 OSS）
> - `presign_download`: 是否支持预签名下载
> - `max_size`: 单文件最大字节数

---

## 返回结果汇总

| 命令 | 关键返回字段 |
|------|-------------|
| `upload` | `file_id`, `status`, `name`, `size`, `mime_type` |
| `list` | `items[]`: `file_id`, `status`, `name`, `size`, `uploader`, `created_at`, `total` |
| `download` | 文件字节流（或跟随 302 重定向） |
| `delete` | 空或确认信息 |
| `share` | `share_url`, `expires_at` |
| `capabilities` | `storage`, `presign_upload`, `presign_download`, `max_size` |