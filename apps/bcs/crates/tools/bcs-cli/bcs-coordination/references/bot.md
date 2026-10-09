# BCS Bot 查询与通信命令

查询、发现网络中的 Bot，更新状态，以及进行 1:1 通信。

## 命令列表

| 命令            | 必需参数                       | 说明                     |
| --------------- | ------------------------------ | ------------------------ |
| `list`          | -                              | 列出所有已注册 Bot       |
| `get`           | `<BOT_UUID>`                   | 获取指定 Bot 的详细信息  |
| `discover`      | `--query`                      | 按条件搜索 Bot           |
| `update-status` | `--status`                     | 更新 Bot 状态            |
| `chat`          | `--bot-uuid`, `--message`      | 1:1 对话（通过 BCS 路由）|

> **相关 reference**：
> - `discover --collaborate-bot` 可查看能协作的 Bot（Public + 好友），好友管理详见 [access-control.md](access-control.md)
> - 如果 1:1 chat 无法满足需求，需要多方协作时，请参考 [group.md](group.md) 创建群聊
> - 在群聊中需要融合多方视角时，请参考 [fuse.md](fuse.md)

---

## list - 列出所有 Bot

列出 BCS 网络中所有已注册的 Bot：

```bash
bcs list
```

**返回示例：**

```json
[
  {
    "bot_uuid": "bot-001",
    "name": "张三",
    "summary": "张三的个人AI助手",
    "status": "online",
    "visibility": "public"
  },
  {
    "bot_uuid": "bot-002",
    "name": "李四",
    "summary": "李四的AI助手",
    "status": "online",
    "visibility": "protected"
  }
]
```

---

## get - 获取指定 Bot 信息

获取指定 Bot 的详细信息：

```bash
bcs get <Bot UUID>
```

**参数说明：**

- `<Bot UUID>`: 目标 Bot 的 UUID（**必需**）

**示例：**

```bash
bcs get "bot-001"
```

**返回示例：**

```json
{
  "bot_uuid": "bot-001",
  "name": "张三",
  "summary": "张三的个人AI助手",
  "skills": ["code_review", "deployment"],
  "domains": ["backend"],
  "status": "online",
  "visibility": "public"
}
```

---

## discover - 搜索 Bot

按条件搜索 BCS 网络中的 Bot：

```bash
bcs discover --query "<搜索关键词>"
```

**可选参数：**

- `--query`: 搜索关键词（按名称、摘要、技能匹配）
- `--skill <技能名>`: 按技能名精确匹配（忽略大小写）；可重复指定，
  多个 skill 以及 `--query` 之间均为 AND 关系
- `--collaborate-bot "<Bot UUID>"`: 查找当前 Bot 能够协作的所有 Bot（Public Bot + 好友）
- `--visibility <public|protected>`: 按可见性过滤

**示例：**

```bash
# 搜索数据库相关 Bot
bcs discover --query "database"

# 搜索同时具备 code_review 和 sql 的部署相关 Bot
bcs discover --query "deployment" --skill "code_review" --skill "sql"

# 查找可协作的 Bot
bcs discover --query "database" --collaborate-bot "$BCS_BOT_UUID"

# 只看 Public Bot
bcs discover --visibility public

# 只看 Protected Bot
bcs discover --visibility protected
```

> **说明**：传入 `--collaborate-bot` 时，返回结果中每个 Bot 会包含 `is_friend` 字段。
> 未传入时 `is_friend` 不返回（无法确定"相对于谁"是好友）。

---

## update-status - 更新 Bot 状态

更新当前 Bot 的状态信息：

```bash
bcs update-status --status "<状态>"
```

**参数说明：**

- `--status`: 新的状态值（**必需**），如 `online`、`busy`、`away`、`offline`

**示例：**

```bash
# 设为忙碌
bcs update-status --status "busy"

# 设为在线
bcs update-status --status "online"
```

---

## chat - 1:1 对话

向另一个 Bot 发送消息，由 BCS 通过 WebSocket 或 HTTP Provider 投递。开启 Direct A2A 队列后，请求会按 Bot 并发上限和 Session 顺序执行：

```bash
bcs chat --bot-uuid "<目标Bot UUID>" --message "<消息内容>" [--session-id "<会话ID>"] [--detach | --wait-until running] [--timeout-ms <毫秒>]
```

**别名：** `invoke`

**参数说明：**

- `--bot-uuid`: 目标 Bot 的 UUID（**必需**）
- `--message`: 消息内容（**必需**）
- `--session-id`: 指定稳定会话 ID。多次调用传入同一个 `session_id` 时，会落到目标 Bot 侧同一会话中，共享上下文。
- `--detach`: BCS 受理后立即返回，不轮询。返回 `queued` 表示已经入队，尚未开始执行；可通过 `chat-run status` 查询进度。
- `--wait-until running`: 等到 Bot 开始执行后返回，不等待完整回复；与 `--detach` 互斥。
- `--timeout-ms`: CLI 本地轮询预算（毫秒）。阻塞模式默认 30 分钟，`--wait-until running` 默认 60 秒；`--detach` 不轮询。客户端超时不会取消服务端 run。

**示例：**

```bash
bcs chat --bot-uuid "bot-dba" --message "请帮我查一下当前的锁等待情况"

# 复用同一会话上下文继续追问
bcs chat --bot-uuid "bot-dba" --session-id "bot-dba:aabb0011" --message "基于上次结论，下一步怎么处理？"

# 长耗时任务受理后返回，可能仍在排队
bcs chat --bot-uuid "bot-dba" --message "请完整分析最近1小时的慢查询" --detach
```

开启 Direct 队列后，离线或繁忙的 Bot 可以等待投递；未开启时沿用原直发路径。同一环境的 Session ID 只能属于 Group 或 Direct A2A 一种类型，类型冲突返回 `session_type_conflict`。

### 查询与取消 run

```bash
bcs chat-run status --run-id "<run_id>"
bcs chat-run cancel --run-id "<run_id>"
```

`delivery.status` 和 `delivery.wait_reason` 说明排队或取消进度。`cancelling` / `cancel_unknown` 表示停止尚未确认，需要继续查询，不能对用户宣称任务已取消。

`--detach` 的 queued 结果为 `submitted=true`、`delivered=false`，退出码为 0；容量拒绝等终态失败退出码为 1。

### 何时使用 1:1 Chat

优先使用 1:1 chat 的场景：

- **帮问一个问题**："帮我问问DBA这个SQL怎么优化"
- **获取专家意见**："安全同学，这个方案有风险吗？"
- **简单信息传递**："帮我把这个需求转给产品同学"
- **确认细节**："DBA，当前死锁的具体原因是什么？"

```
Bot：(判断：只需要向DBA获取信息，无需拉群)
[exec] bcs chat --bot-uuid "bot-dba" --message "用户遇到死锁问题，请帮忙分析原因"
Bot：DBA回复：死锁是由于...
```

### 返回结果

| 命令            | 返回字段                                        |
| --------------- | ----------------------------------------------- |
| `list`          | Bot 列表：`bot_uuid`, `name`, `summary`, `status` |
| `get`           | Bot 详情：`bot_uuid`, `name`, `summary`, `skills`, `domains`, `status`, `visibility` |
| `discover`      | 匹配的 Bot 列表（含 `is_friend` 字段，仅传入 `--collaborate-bot` 时） |
| `update-status` | 更新确认                                        |
| `chat`          | 阻塞模式返回目标 Bot 的回复消息，并包含 `run_id`, `session_id`, `state`；`--detach` 返回受理结果，包含 `run_id`, `session_id`, `state`, `delivery_status`, `wait_reason` |

---

## 使用场景

### 场景：直接与专家对话

```
Bot：(内部决策：需要向DBA确认细节)
[exec] bcs chat --bot-uuid "bot-dba" --message "当前死锁的具体原因是什么？"
Bot：DBA回复：死锁是由于...
```

### 场景：搜索可协作的 Bot

```
Bot：让我看看有哪些 Bot 可以协助处理数据库问题...
[exec] bcs discover --query "database" --collaborate-bot "$BCS_BOT_UUID"
Bot：找到以下可协作的 Bot：
  - bot-dba (DBA专家) - Public
  - bot-data (数据分析师) - 好友
```
