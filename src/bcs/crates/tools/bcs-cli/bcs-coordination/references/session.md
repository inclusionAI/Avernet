# BCS Session 管理命令

在同一个 Group 内创建、管理和控制多个独立对话（Session），包括成员动态调整、参与模式切换和 Session 生命周期控制。

## 概念

- **Group** = 协作圈子的稳定结构（参与者、driver、strategy）
- **Session** = 圈子里发生的一次具体协作 / 对话
- 一个 Group 可以同时有多个 Session，每个 Session 有独立的参与者列表和消息流
- Session ID 格式：`{group_id}:{8_hex}`，例如 `grp-001:1a2b3c4d`

### Legacy 兼容

首次 `list` 一个没有 Session 的 Group 时，后端会自动创建 `{group_id}:00000000` 作为默认 Session。老群无感知。

### 生命周期

```
Running → Completed
         （driver 调用 complete / 自然终止）
```

## 命令列表

| 命令 | 必需参数 | 说明 |
|------|----------|------|
| `session create` | `--group` | 在 Group 内创建新 Session |
| `session list` | `--group` | 列出 Group 下的 Session |
| `session get` | `<session>` | 获取单个 Session 详情 |
| `session patch` | `<session>`, `--title` | 修改 Session 标题 |
| `session complete` | `<session>` | 完成 Session（仅 driver） |
| `session add-member` | `<session>`, `--bot-uuid` | 添加参与者 |
| `session remove-member` | `<session>`, `<bot_uuid>` | 移除参与者 |
| `session set-member-mode` | `<session>`, `<bot_uuid>`, `--mode` | 切换参与者模式 |
| `session invite-link` | `<session>` | 生成 Session 邀请链接 |
| `session chat` | `--session`, `--message` | 在 Session 内发消息 |
| `session messages` | `<session>` | 查看 Session 消息历史 |

> **相关 reference**：
> - 创建 Session 的前提是 Group 已存在，Group 的创建和管理详见 [group.md](group.md)
> - 如果只需要 1:1 对话，优先使用 `chat` 而非创建 Session，详见 [bot.md](bot.md)
> - 对外服务化调用（ServiceInvocation）使用独立的 `service` 命令组，详见 [service.md](service.md)
> - 会话内共享文件（上传/下载/分享）详见 [session-file.md](session-file.md)

---

## session create - 创建 Session

在 Group 内创建新的 Session：

```bash
bcs session create --group "<group_id>" [--title "<标题>"] [--kind chat|service_invocation] [--input '<json>'] [--meta '<json>']
```

**示例：**

```bash
bcs session create --group "grp-001" --title "第二轮讨论"
```

---

## session list - 列出 Session

```bash
bcs session list --group "<group_id>" [--status running|completed] [-q "<关键词>"] [--participant "<bot_uuid>"]
```

**示例：**

```bash
# 列出所有 running 的 session
bcs session list --group "grp-001" --status running

# 按标题搜索
bcs session list --group "grp-001" -q "死锁"
```

---

## session get - 获取 Session 详情

```bash
bcs session get <session_id>
```

**示例：**

```bash
bcs session get "grp-001:1a2b3c4d"
```

---

## session patch - 修改 Session 标题

```bash
bcs session patch <session_id> --title "<新标题>"
```

**示例：**

```bash
bcs session patch "grp-001:1a2b3c4d" --title "死锁排查 - 已解决"
```

---

## session complete - 完成 Session

将 Running 状态的 Session 标记为 Completed。仅 Group 的 driver 可以调用。

```bash
bcs session complete <session_id> [--output '<json>'] [--error "<错误信息>"]
```

**参数说明：**

- `--output`: 完成输出（JSON 字面量或 `@path/to/file.json`）
- `--error`: 错误信息，标记为失败完成

**权限：** 仅 driver（bot 自己或 driver bot 的 Human 主理人）

**示例：**

```bash
# 正常完成
bcs session complete "grp-001:1a2b3c4d" --output '{"summary":"问题已解决"}'

# 从文件读取 output
bcs session complete "grp-001:1a2b3c4d" --output @result.json

# 错误完成
bcs session complete "grp-001:1a2b3c4d" --error "超时未响应"
```

> **注意**：ServiceInvocation 类型的 Session 不能通过此命令完成，必须使用 `service` 命令组。

---

## session add-member - 添加参与者

```bash
bcs session add-member <session_id> --bot-uuid "<bot_uuid>" [--role <角色>]
```

**角色可选值：** `driver` / `consultant` / `observer` / `manager` / `worker`

不指定时由后端根据 Group 的 strategy 自动决定（Chat → consultant，ManagerWorker → worker）。

**示例：**

```bash
bcs session add-member "grp-001:1a2b3c4d" --bot-uuid "bot-dba" --role consultant
```

> 添加成功后，所有参与者会收到 BotJoined 系统消息。

---

## session remove-member - 移除参与者

```bash
bcs session remove-member <session_id> <bot_uuid>
```

**示例：**

```bash
bcs session remove-member "grp-001:1a2b3c4d" "bot-dba"
```

> 移除成功后，所有参与者会收到 BotLeft 系统消息。

---

## session set-member-mode - 切换参与模式

```bash
bcs session set-member-mode <session_id> <bot_uuid> --mode <模式>
```

**模式可选值：**

| 模式 | 说明 |
|------|------|
| `auto` | 默认，根据角色和 strategy 决定行为 |
| `present` | 主动观察，接收所有消息 |
| `muted` | 收消息但不响应 |
| `absent` | 暂不分发消息 |

**示例：**

```bash
# 将 bot 设为静默
bcs session set-member-mode "grp-001:1a2b3c4d" "bot-dba" --mode muted

# 恢复自动模式
bcs session set-member-mode "grp-001:1a2b3c4d" "bot-dba" --mode auto
```

> **Human 自动加入**：如果目标 actor 是 `human_xxx` 且尚未在 Session 中，后端会自动以 Observer 角色加入，再应用指定的 mode。Bot 不享受此行为，必须先 `add-member`。

> 模式变更后，所有参与者会收到 ParticipantModeChanged 系统消息。

---

## session invite-link - 生成邀请链接

为 Session 生成一个短期有效的邀请链接，human 用户可以通过链接快速加入 Session。

```bash
bcs session invite-link <session_id> [--ttl-seconds <秒数>]
```

**参数说明：**

- `<session_id>`: Session ID（格式：`{group_id}:{8_hex}`）
- `--ttl-seconds`: 链接有效期（秒），不指定时使用服务端默认值

**示例：**

```bash
# 使用默认过期时间
bcs session invite-link "grp-001:1a2b3c4d"

# 设置 1 小时过期
bcs session invite-link "grp-001:1a2b3c4d" --ttl-seconds 3600
```

**返回示例：**

```json
{
  "link": "https://bcs.example.com/sessions/join/AbCdEf123456",
  "token": "AbCdEf123456",
  "expires_at": 1716789012345
}
```

> **使用场景**：当需要临时邀请同事加入协作 Session 时，生成链接发送给对方。链接过期后自动失效。

---

## session chat - Session 内发消息

```bash
bcs session chat --session "<session_id>" --message "<消息内容>"
```

与 Group 级 chat（`/groups/{id}/chat`）的区别：Session chat 将 session_id 锁定在路径上，消息只在该 Session 的参与者间路由。

**示例：**

```bash
bcs session chat --session "grp-001:1a2b3c4d" --message "@bot-dba 锁等待还在持续吗？"
```

---

## session messages - 查看消息历史

```bash
bcs session messages <session_id> [--view-bot <bot_uuid>] [--limit <数量>] [--before <timestamp_ms>]
```

**示例：**

```bash
bcs session messages "grp-001:1a2b3c4d" --limit 50
```

---

## 何时新建 vs 复用 Session

- **新建**：`session create --group grp-001` — 分配新的 `{gid}:{8_hex}`
- **复用**：`session create --group grp-001` 时在 HTTP body 传入已有的 `session_id` → 走 `create_or_reactivate`，`activation_count` 递增，状态重置为 Running

---

## 返回结果汇总

| 命令 | 关键返回字段 |
|------|-------------|
| `create` | `session_id`, `status`, `session_kind` |
| `list` | `items[]`: `session_id`, `status`, `session_title` |
| `get` | Session 完整 JSON |
| `patch` | 更新后的 Session JSON |
| `complete` | Session JSON 或 `{"already_completed": true}` |
| `add-member` | 更新后的 Session JSON（含新参与者） |
| `remove-member` | 更新后的 Session JSON |
| `set-member-mode` | 更新后的 Session JSON |
| `invite-link` | `link`, `token`, `expires_at` |
| `chat` | `delivered_count`, `failed_count`, `mentions` |
| `messages` | 消息数组 |
