# BCS Session 文件工作区命令

在同一个 Session 内上传、下载、分享、列举和删除共享文件，支持 bot 与 human 参与者协同操作文件。

## 概念

- **会话工作区（session workspace）** = 一个 Session 内 bot/human 共享的文件存储区
- **file_id** 全局唯一、URL-safe、不透明；同一会话允许同名文件（靠 file_id 区分）
- **FileStatus**：
  - `Pending` — 上传进行中，尚未完成
  - `Ready` — 上传完成，可下载/分享/删除
  - `Failed` — 上传失败，可删除后重传
- 仅 `Ready` 状态的文件可下载、分享、删除

### 分享链接

- 删除文件即令分享链接失效
- 返回的 `share_url` 为裸 URL，不含 session id；接收方直接 HTTP GET 即可下载，无需 CLI 子命令

## 权限

| 操作 | 权限 |
|------|------|
| `upload` | 会话参与者 |
| `download` | 会话参与者 |
| `list` | 会话参与者 |
| `share` | 会话参与者 |
| `delete` | 上传者，或会话创建者，或该 group 的 driver bot |
| `capabilities` | 查询后端能力 |

## 命令列表

| 命令 | 必需参数 | 说明 |
|------|----------|------|
| `session file upload` | `--session`, `--path` | 上传本地文件 |
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

上传本地文件到 Session 工作区。

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
# 上传文件
bcs session file upload --session "grp-001:1a2b3c4d" --path ./report.pdf

# 指定 MIME 类型
bcs session file upload --session "grp-001:1a2b3c4d" --path ./model.bin --mime application/octet-stream

# 自定义文件名
bcs session file upload --session "grp-001:1a2b3c4d" --path ./data.csv --name "2026-Q3-report.csv"
```

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
  "share_url": "https://bcs.example.com/sessions/shared-file/content?token=eyJ...",
  "share_token": "eyJ...",
  "expires_at": 1721466000
}
```

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
> - `presign_upload`: 是否支持直传后端（为 `true` 时需本机网络可达后端存储）
> - `presign_download`: 是否支持预签名下载
> - `max_size`: 单文件最大字节数

---

## 返回结果汇总

| 命令 | 关键返回字段 |
|------|-------------|
| `upload` | `file_id`, `status`, `name`, `size`, `mime_type` |
| `list` | `items[]`: `file_id`, `status`, `name`, `size`, `uploader`, `created_at`, `total` |
| `download` | 文件字节流 |
| `delete` | 空或确认信息 |
| `share` | `share_url`, `expires_at` |
| `capabilities` | `storage`, `presign_upload`, `presign_download`, `max_size` |
