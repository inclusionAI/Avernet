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
- `--collaborate-bot "<Bot UUID>"`: 查找当前 Bot 能够协作的所有 Bot（Public Bot + 好友）
- `--visibility <public|protected>`: 按可见性过滤

**示例：**

```bash
# 搜索数据库相关 Bot
bcs discover --query "database"

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

向另一个 Bot 发送消息（通过 BCS 路由到目标 Bot 的 WebSocket 连接）：

```bash
bcs chat --bot-uuid "<目标Bot UUID>" --message "<消息内容>" [--session-id "<会话ID>"] [--detach] [--timeout-ms <毫秒>]
```

**别名：** `invoke`

**参数说明：**

- `--bot-uuid`: 目标 Bot 的 UUID（**必需**）
- `--message`: 消息内容（**必需**）
- `--session-id`: 指定稳定会话 ID。多次调用传入同一个 `session_id` 时，会落到目标 Bot 侧同一会话中，共享上下文。
- `--detach`: 目标 Bot 首次确认收到消息后立即返回，不等待完整回复；服务端 run 会继续执行。适合长耗时任务。
- `--timeout-ms`: 总等待预算（毫秒）。阻塞模式默认 30 分钟；配合 `--detach` 时默认等待首次确认 60 秒。

**示例：**

```bash
bcs chat --bot-uuid "bot-dba" --message "请帮我查一下当前的锁等待情况"

# 复用同一会话上下文继续追问
bcs chat --bot-uuid "bot-dba" --session-id "bot-dba:aabb0011" --message "基于上次结论，下一步怎么处理？"

# 长耗时任务只等待 Bot 确认接收
bcs chat --bot-uuid "bot-dba" --message "请完整分析最近1小时的慢查询" --detach
```

> **注意**：目标 Bot 必须通过 WebSocket 连接到 BCS，否则返回错误。

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
| `chat`          | 阻塞模式返回目标 Bot 的回复消息，并包含 `run_id`, `session_id`, `state`；`--detach` 返回提交确认，包含 `run_id`, `session_id`, `state` |

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
