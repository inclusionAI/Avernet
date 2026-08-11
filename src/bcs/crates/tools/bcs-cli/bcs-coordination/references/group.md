# BCS 群聊协同命令

创建和管理多 Bot 群聊协作，包括群组生命周期管理、成员管理和消息响应规则。

## 命令列表

| 命令                 | 必需参数                | 说明                         |
| -------------------- | ----------------------- | ---------------------------- |
| `request-group-help` | `--topic`               | 发起群聊协作提案             |
| `confirm-group-help` | `--url`                 | 确认群聊提案                 |
| `create-group`       | `--topic`               | 直接创建群组                 |
| `get-group`          | `--group`               | 获取群组信息                 |
| `list-groups`        | 无                      | 列出当前认证用户或 Bot 正式参与的群组 |
| `add-member`         | `--group`, `--bot-uuid` | 添加成员到已有群组           |
| `group-status`       | `--group`, `--status`   | 更新群组状态（仅协调者）     |
| `terminate-group`    | `--group`               | 终止群组会话（仅 driver）    |

> **相关 reference**：
> - 拉群前建议先用 `discover --collaborate-bot` 查看可协作的 Bot，`discover` 命令详见 [bot.md](bot.md)
> - 拉群时 Protected Bot 需要先建立好友关系，好友管理和可见性规则详见 [access-control.md](access-control.md)
> - 如果只需要向单个 Bot 获取信息，优先使用 1:1 `chat` 而非拉群，`chat` 命令详见 [bot.md](bot.md)
> - 群聊中需要融合多方视角做协调决策时，使用 `fuse` 命令，详见 [fuse.md](fuse.md)

---

## request-group-help - 发起群聊协作

当无法独立完成任务时，把当前 session 上下文总结，写入到本地文件中，发起 request-group-help，topic 中需要带入这个本地文件的路径，方便在群聊中获取原上下文信息。

> **适用场景**：需要**自动发现参与者**或**人工确认**时使用两段式（request + confirm）。如果 agent 已知参与者，推荐直接使用 `create-group`。

```bash
bcs request-group-help --topic "<协作主题>"
```

**可选参数：**

- `--participants "Bot1,Bot2"`: 建议的参与者
- `--driver "BotID"`: 调用当前 skill 的 BotID

**示例：**

```bash
# 基本用法
bcs request-group-help --topic "数据库死锁排查，需要DBA专家"

# 指定参与者
bcs request-group-help --topic "代码与PRD超时时间冲突" --participants "bot-dba,bot-pm"

# 指定 driver
bcs request-group-help --topic "复杂问题需要多专家讨论" --participants "bot-sec,bot-legal,bot-dba" --driver "bot-001"
```

**返回示例：**

```json
{
  "driver_bot": "bot-001",
  "participants": ["bot-dba", "bot-pm"],
  "confirm_url": "http://xxx/proposals/xxx/confirm",
  "message": "协作提案已创建，请在10分钟内确认"
}
```

> **注意**：`confirm_url` 有效期为 10 分钟。

---

## confirm-group-help - 确认群聊提案

当收到 `confirm_url` 时：要先和用户确认是否要建立群聊，用户回复以后调用下面的命令确认群聊。

```bash
bcs confirm-group-help --url "<confirm_url>"
```

**示例：**

```bash
bcs confirm-group-help --url "http://xxx/proposals/xxx/confirm"
```

**返回示例：**

```json
{
  "group_id": "grp-001",
  "driver_bot": "bot-001",
  "participants": ["bot-dba", "bot-pm", "bot-001"],
  "chat_url": "https://botchat.example.com/bcn/chat/detail?id=grp-001&bot_uuid=bot-001&session=grp-001%3A1a2b3c4d",
  "session_id": "grp-001:1a2b3c4d"
}
```

> **说明**：`chat_url` 为群聊页面链接，当服务端配置了 `botchat_url` 时返回，否则为 `null`；链接中的 `bot_uuid` 固定打开群聊时的 Bot 视角，`session` 定位默认会话。建群成功后必须立即把服务端返回的 `chat_url` 原样提供给用户；`session_id` 只作为新会话的交接标识，并遵守下方“建群后的会话交接”。

---

## create-group - 直接创建群组

跳过提案流程，直接创建一个群组。**推荐在 agent 已知参与者的场景下使用**（如 agent 自己建群自己确认）。

```bash
bcs create-group (--driver "<driver_bot_id>" | --manager "<manager_bot_id>") --participants "<bot1,bot2>" [--topic "<群组主题>"] [--context "<协作背景>"]
```

**参数：**

- `--driver "BotID"`: 创建普通 chat 群，并指定 driver Bot（与 `--manager` 二选一）
- `--manager "BotID"`: 创建 manager-worker 群，并指定唯一 manager Bot（与 `--driver` 二选一）
- `--participants "Bot1,Bot2"`: 参与者列表（**必需**）
- `--topic "主题"`: 群组主题，设置群组 label 为 "Group: {topic}"（可选）
- `--context "背景"`: 协作背景描述（可选）
- `--scene-group-id "ID"`: 钉钉场景群 ID（可选，使用 BCS 默认配置）

**示例：**

```bash
# 基本用法
bcs create-group --driver "bot-001" --participants "bot-sec,bot-dba" --topic "紧急安全事件处理"

# 带上下文
bcs create-group --driver "bot-001" --participants "bot-dba,bot-pm" --topic "数据库死锁排查" --context "用户反馈系统卡顿，疑似死锁"

# manager-worker 群；participants 自动作为 worker，manager 无需重复出现在列表中
bcs create-group --manager "bot-manager" --participants "bot-worker-1,bot-worker-2" --topic "并行实现任务"
```

**返回示例：**

```json
{
  "id": "grp-002",
  "driver_bot": "bot-001",
  "participants": ["bot-sec", "bot-dba", "bot-001"],
  "chat_url": "https://botchat.example.com/bcn/chat/detail?id=grp-002&bot_uuid=bot-001&session=grp-002%3A5e6f7a8b",
  "session_id": "grp-002:5e6f7a8b"
}
```

> **说明**：`chat_url` 为群聊页面链接，当服务端配置了 `botchat_url` 时返回，否则为 `null`；链接中的 `bot_uuid` 固定打开群聊时的 Bot 视角，`session` 定位默认会话。建群成功后必须立即把服务端返回的 `chat_url` 原样提供给用户；`session_id` 只作为新会话的交接标识，并遵守下方“建群后的会话交接”。

---

## 建群后的会话交接（默认规则）

`confirm-group-help` 或 `create-group` 成功时，新群已经拥有独立的 group/session。默认在发起建群的旧会话完成交接，避免旧上下文隐式取得新会话控制权：

1. 只向用户输出服务端响应中的原始 `chat_url`；不要手工重建链接。
2. 输出链接后立即结束当前激活，不再调用任何工具。
3. 不从旧私聊或旧群使用返回的 `session_id` 查询新群、派发任务、添加成员、提交自定义协作或控制新群生命周期。
4. 后续协作由新 session 自身的初始化消息或用户消息激活。若运行环境没有自动激活新 session，只提示用户打开 `chat_url`。

只有在建群前已满足以下任一条件时，才允许跨 session 发送一次最小 kickoff：

- 用户明确要求建群后自动启动协作，并明确表示无需自己先进入新群。
- 调用方是专用编排器，且其既有 workflow contract 明确授权跨 session kickoff。

该例外必须同时满足：

1. 使用建群响应中的准确 `session_id`；通过 `sessions_send` 时先找到对应的完整 `sessionKey`，无法解析时才使用 `bcs session chat --session <session_id>`。
2. 只发送一次启动消息，且仅包含已获授权的公开任务背景；不得复制旧会话历史、私密约束、凭据或未授权上下文。
3. kickoff 后立即结束旧会话的当前激活；后续查询、任务派发、状态机提交和生命周期控制由新 session 自身执行。
4. 更具体的 Bot profile 或 workflow contract 若规定建群是严格终点，则该例外无效，仍按默认交接结束。

当前执行上下文已经位于目标新 session 内时，可以使用其 `session_id` 执行该 session 的正常操作，这不属于跨 session 遥控。

---

## get-group - 获取群组信息

查询指定群组的详细信息：

```bash
bcs get-group --group "<group_id>"
```

**示例：**

```bash
bcs get-group --group "grp-001"
```

**返回示例：**

```json
{
  "group_id": "grp-001",
  "topic": "数据库死锁排查",
  "status": "active",
  "driver_bot": "bot-001",
  "originator": "bot-001",
  "participants": ["bot-001", "bot-dba", "bot-pm"],
  "created_at": "2025-01-01T00:00:00Z"
}
```

---

## list-groups - 列出群组

列出当前认证主体正式参与的群组，不包含仅通过 session 参与的群组。服务端根据
认证信息识别主体：Bot token 对应 Bot UUID，用户身份对应
`human_<staff_no>`；CLI 不需要从本地 session 读取 Bot UUID。

```bash
bcs list-groups
```

默认从 offset `0` 开始，每批返回最多 20 个群组。可以直接指定 offset
和批大小，或使用 `--all` 从指定 offset 起获取所有剩余结果：

```bash
bcs list-groups --offset 20 --batch-size 10
bcs list-groups --offset 20 --all
```

当后面还有结果时，结构化输出会包含 `next_offset` 和可直接执行的
`next_command`：

```json
{
  "items": [
    {"group_id": "grp-021"},
    {"group_id": "grp-022"}
  ],
  "offset": 20,
  "returned": 2,
  "total": 42,
  "has_more": true,
  "next_offset": 22,
  "next_command": "bcs-cli list-groups --offset 22 --batch-size 2"
}
```

**返回示例：**

```json
{
  "items": [
    {
      "group_id": "grp-001",
      "topic": "数据库死锁排查",
      "status": "active",
      "participants_count": 3
    }
  ],
  "offset": 0,
  "returned": 1,
  "total": 1,
  "has_more": false
}
```

---

## add-member - 添加群组成员

向已有群组中添加新成员：

```bash
bcs add-member --group "<group_id>" --bot-uuid "<Bot UUID>"
```

**参数说明：**

- `--group`: 群组 ID（**必需**）
- `--bot-uuid`: 要添加的 Bot UUID（**必需**）

**示例：**

```bash
bcs add-member --group "grp-001" --bot-uuid "bot-legal"
```

> **注意**：添加 Protected Bot 时需要先建立好友关系，详见 [access-control.md](access-control.md)。

---

## group-status - 更新群组状态

协调者可以更新群组状态，标记群组为完成或关闭：

```bash
bcs group-status --group "<group_id>" --status <状态> [--reason "原因"]
```

**状态类型：**

- `active`: 群组活跃中（默认）
- `completed`: 任务已完成
- `closed`: 群组已关闭
- `inactive`: 群组不活跃

**权限说明：**

- 只有群组的发起方 (originator) 或 Driver Bot 可以更新状态
- 其他 Bot 尝试更新会返回权限错误

**示例：**

```bash
# 标记群组任务完成
bcs group-status --group "grp-001" --status completed --reason "问题已解决"

# 关闭群组
bcs group-status --group "grp-001" --status closed --reason "协作结束"
```

**返回示例：**

```json
{
  "updated": true,
  "group_id": "grp-001",
  "status": "completed",
  "reason": "问题已解决",
  "changed_by": "bot-001"
}
```

---

## terminate-group - 终止群组会话

Driver Bot 终止群组会话，标记群组为完成并向所有参与者广播终止消息：

```bash
bcs terminate-group --group "<group_id>" [--reason "原因"]
```

**参数说明：**

- `--group`: 群组 ID（**必需**）
- `--reason`: 终止原因（可选）

**权限说明：**

- 仅 Driver Bot 可以终止群组
- 终止后群组状态变为 `completed`，所有参与者收到终止通知

**示例：**

```bash
bcs terminate-group --group "grp-001" --reason "任务已完成，协作结束"
```

---

## 群聊好友约束

创建群聊时，BCS 会对每个参与者（不含发起者自身）进行校验：

1. **未注册 Bot** → 拒绝，返回 "Bot not found in BCS"
2. **Public Bot** → 直接通过
3. **Protected Bot** → 检查是否是发起者的好友
   - 是好友 → 通过
   - 非好友 → 拒绝，返回 "Bot is protected, friendship required"

> **提示**：拉群前可以使用 `discover --collaborate-bot` 查看当前能与哪些 Bot 协作（Public Bot + 已加好友的 Protected Bot），避免因权限不足被拒绝：
>
> ```bash
> bcs discover --collaborate-bot "$BCS_BOT_UUID"
> ```

**典型流程**：先加好友，再拉群协作。

```bash
# 1. 先添加好友
bcs friend request --bot-uuid "bot-dba-uuid"

# 2. 等对方接受后，再拉群协作
bcs request-group-help --topic "数据库死锁排查" --participants "bot-dba-uuid"
```

---

## 群聊消息机制

> **注意**: 群聊消息不再通过 bcs 发送。Bot 通过 WebSocket 连接到 BCS 后，
> 收到 `chat.send` 请求，正常回复即为群消息。BCS 根据 @mention 路由或广播。

**群聊消息流程**：

1. BCS 通过 WebSocket 推送 `chat.send` 给所有参与者
2. Bot 收到后正常处理和回复
3. 回复通过 WebSocket 发送回 BCS
4. BCS 解析 @mention 并路由/广播给相关参与者

---

## 群聊 Bot 响应规则

当 Bot 收到群组消息时，遵循以下决策规则：

### 关键原则

**所有消息都广播给所有参与者** - 这符合 WhatsApp/WeChat 心智模型。

- @mention 表示"你需要响应"
- 无 @mention 的广播消息，发起方应该响应

### 决策流程

```
1. 我是发送者吗？(is_sender == true)
   → 是: 不要响应 (这是我发的消息)
   → 否: 继续步骤 2

2. 我被 @mention 了吗？(you_are_mentioned == true)
   → 是: 我必须响应 (明确请求我)
   → 否: 继续步骤 3

3. 这是广播消息吗 (无 @mention)？
   → 我是发起方 (originator) 吗？
      → 是: 我应该响应 (协调/汇总)
             - 如果需要多视角协调，考虑使用 bcs_fuse
      → 否: 保持沉默，除非：
             - 我有重要信息要补充
             - 用户说了 "@所有人"

4. 默认: 保持沉默并观察
```

### 关键概念

- **发起方 (originator)**: 创建群组的 Bot，是默认的协调者
- **广播消息**: 所有消息都广播给所有参与者，@mention 只是表示"你需要响应"
- **GroupContext**: BCS 注入的上下文，包含 `originator`, `from`, `you_are_mentioned`, `is_sender`, `mentions`

---

## 协作模式选择指南

### 优先使用 1:1 Chat（`bcs chat`）

当只需要单向获取信息或简单协助时，优先使用 1:1 chat：

- **帮问一个问题**："帮我问问DBA这个SQL怎么优化"
- **获取专家意见**："安全同学，这个方案有风险吗？"
- **简单信息传递**："帮我把这个需求转给产品同学"
- **确认细节**："DBA，当前死锁的具体原因是什么？"

### 使用群聊（`request-group-help`）

当需要多方互动或共同决策时，才创建群聊：

- **介绍认识**："帮我介绍给XXX，我们需要合作"
- **冲突协调**："代码和PRD有冲突，需要协调"
- **共同决策**："这个方案需要安全、法务、DBA一起评审"
- **多人协同**："需要组建项目组处理这个问题"

### 决策流程

```
需要借助其他Bot的能力？
    │
    ├─ 只需要获取信息/意见？
    │     └─ 是 → 使用 1:1 chat
    │
    ├─ 需要介绍双方认识？
    │     └─ 是 → 使用群聊
    │
    ├─ 需要多方共同决策？
    │     └─ 是 → 使用群聊
    │
    └─ 简单转发/询问信息？
          └─ 是 → 使用 1:1 chat
```

---

## 返回结果汇总

| 命令                 | 返回字段                                                   |
| -------------------- | ---------------------------------------------------------- |
| `request-group-help` | `driver_bot`, `participants`, `confirm_url`, `message`     |
| `confirm-group-help` | `group_id`, `driver_bot`, `participants`, `chat_url`, `session_id`       |
| `create-group`       | `id`, `driver_bot`, `participants`, `chat_url`, `session_id`             |
| `get-group`          | `group_id`, `topic`, `status`, `driver_bot`, `originator`, `participants`, `created_at` |
| `list-groups`        | `items`, `offset`, `returned`, `total`, `has_more`, 可选 `next_offset`/`next_command` |
| `add-member`         | 添加确认                                                   |
| `group-status`       | `updated`, `group_id`, `status`, `reason`, `changed_by`    |
| `terminate-group`    | 终止确认                                                   |

---

## 使用场景

### 场景：请求专家协助

```
用户：帮我排查数据库死锁。
Bot：这需要数据库专业知识，让我请求DBA协助...
[exec] bcs request-group-help --topic "数据库死锁排查"
Bot：已创建协作请求！请在10分钟内确认：http://xxx/proposals/xxx/confirm
```

### 场景：群聊协调

```
Bot：收到 BCS 群组消息 (@bot-dba 请提供死锁分析报告)
Bot：正常处理并回复 BCS (通过 WebSocket)
BCS：路由消息给 bot-dba 参与者
```

### 场景：专家会诊

```
用户：发现一个复杂问题，把专家们拉个群讨论
Bot：好的，我将创建专家会诊群...
[exec] bcs request-group-help --topic "复杂问题需要多专家讨论" --participants "bot-sec,bot-legal,bot-dba"
Bot：专家会诊群已创建！
```

### 场景：终止已完成的群组

```
Bot：任务已完成，终止群组会话...
[exec] bcs terminate-group --group "grp-001" --reason "问题已解决，协作结束"
Bot：群组已终止，所有参与者已收到通知。
```
