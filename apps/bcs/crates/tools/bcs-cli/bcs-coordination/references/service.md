# BCS 对外服务化调用命令

将 Group 当作服务对外暴露，通过 Service Key 鉴权，支持输入/输出投递和 callback 通知。

## 概念

- **Service Group**：配置了 `service_spec` 的 Group，可以作为服务被外部调用
- **ServiceInvocation Session**：一次服务调用对应一个 Session，有独立的 input/output/callback 生命周期
- 与 Chat Session 的区别：ServiceInvocation 不走 chat 消息流，是一次性的 input → 处理 → output 投递

### 鉴权

- 使用 `X-BCS-Service-Key` 请求头（原始 key，服务端 SHA256 校验）
- 优先级：`--service-key` 参数 > `BCS_SERVICE_KEY` 环境变量
- 服务端 key registry 为空时（本地/dev）：anonymous 模式，无鉴权
- **caller_principal 隔离**：同一 Group 下不同 key 互不可见对方的 Session

## 命令列表

| 命令 | 必需参数 | 说明 |
|------|----------|------|
| `service invoke` | `--group` | 发起服务调用 |
| `service status` | `<sid>` | 查询 Session 状态（单次） |
| `service wait` | `<sid>` | 阻塞等待 Session 完成 |

---

## service invoke - 发起服务调用

```bash
bcs service invoke --group "<group_id>" [--input '<json>'] [--meta '<json>'] [--title "<标题>"] [--session-id "<复用id>"] [--caller-id "<调用方id>"] [--detach] [--timeout-ms <ms>]
```

**参数说明：**

- `--input`: 输入 payload（JSON 字面量或 `@path/to/file.json`）
- `--meta`: 元数据（JSON 字面量或 `@path/to/file.json`）
- `--session-id`: 复用已有 Session（reactivate）
- `--caller-id`: 调用方标识（记录在 Session 上）
- `--detach`: 拿到 202 + session_id 后立即返回，不等待完成
- `--timeout-ms`: 阻塞等待的超时时间（默认 30 分钟，最大 24 小时）

**前提**：目标 Group 必须配置了 `service_spec`，否则返回 400。

**示例：**

```bash
# 阻塞等待完成
bcs service invoke --group "svc-group-001" --input '{"query":"分析日志"}' --title "日志分析任务"

# 从文件读取 input，不等待
bcs service invoke --group "svc-group-001" --input @request.json --detach

# 复用已有 session
bcs service invoke --group "svc-group-001" --session-id "svc-group-001:aabb0011" --input '{"followup":true}'
```

### 并发限制

当 Group 配置了 `max_concurrency` 且当前 Running 的 ServiceInvocation Session 数已达上限时，服务端返回 **429 Too Many Requests**：

```json
{
  "error": "max_concurrency_exceeded",
  "max": 3,
  "current_running": 3,
  "retry_after_seconds": 10
}
```

---

## service status - 查询状态

单次查询 ServiceInvocation Session 的当前状态：

```bash
bcs service status <session_id> [--group "<group_id>"]
```

Session ID 格式为 `{group_id}:{8_hex}`，`--group` 不传时从 ID 中自动解析。

**示例：**

```bash
bcs service status "svc-group-001:aabb0011"
```

---

## service wait - 等待完成

阻塞等待 ServiceInvocation Session 完成（或超时）：

```bash
bcs service wait <session_id> [--group "<group_id>"] [--timeout-ms <ms>]
```

**示例：**

```bash
bcs service wait "svc-group-001:aabb0011" --timeout-ms 60000
```

---

## 返回结果汇总

| 命令 | 关键返回字段 |
|------|-------------|
| `invoke` | `session_id`, `status`, `input`, `output`, `reused` |
| `status` | `session_id`, `status`, `output`, `error_message` |
| `wait` | 与 `status` 相同，等到终态才返回 |

---

## 使用场景

### 场景：发起服务调用并等待结果

```
用户：帮我调用日志分析服务
Bot：正在发起服务调用...
[exec] bcs service invoke --group "svc-log-analyzer" --input '{"query":"最近1小时错误日志"}'
Bot：分析完成，结果：...
```

### 场景：异步提交后轮询

```
Bot：提交异步任务...
[exec] bcs service invoke --group "svc-batch" --input @batch.json --detach
Bot：任务已提交，session_id: svc-batch:aabb0011
[exec] bcs service wait "svc-batch:aabb0011" --timeout-ms 300000
Bot：任务完成，输出：...
```
