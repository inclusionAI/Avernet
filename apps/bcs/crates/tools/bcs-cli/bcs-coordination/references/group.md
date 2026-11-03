# BCS 群聊协同命令

创建和管理多 Bot 群聊协作，包括群组生命周期管理、成员管理和消息响应规则。

## 命令列表

| 命令                 | 必需参数                | 说明                         |
| -------------------- | ----------------------- | ---------------------------- |
| `create-group`       | `--driver` 或 `--manager`，以及 `--participants` | 直接创建群组 |
| `get-group`          | `--group`               | 获取群组信息                 |
| `list-groups`        | 无                      | 列出当前认证用户或 Bot 正式参与的群组 |
| `add-member`         | `--group`, `--bot-uuid` | 不推荐维护群级成员；向会话加人使用 `session add-member` |
| `group-status`       | `--group`, `--status`   | 更新群组状态（仅协调者）     |
| `terminate-group`    | `--group`               | 终止群组会话（仅 driver）    |

> **相关 reference**：
> - 拉群前建议先用 `discover --collaborate-bot` 查看可协作的 Bot，`discover` 命令详见 [bot.md](bot.md)
> - 拉群时 Protected Bot 需要先建立好友关系，好友管理和可见性规则详见 [access-control.md](access-control.md)
> - 如果只需要向单个 Bot 获取信息，优先使用 1:1 `chat` 而非拉群，`chat` 命令详见 [bot.md](bot.md)
> - 群聊中需要融合多方视角做协调决策时，使用 `fuse` 命令，详见 [fuse.md](fuse.md)
> - 给当前或指定会话增加协作者，使用 `session add-member`，详见 [session.md](session.md)；不推荐维护群级成员，两者是不同作用域。

---

## create-group - 直接创建群组

需要多人协作时，使用 `create-group` 直接建群。参与者未知时先用 `discover --collaborate-bot` 查找，选择返回的真实 Bot UUID；不要猜测参与者。已有明确建群授权即可执行，目标或参与者不明确时先与用户沟通。`--context` 只包含获准向群成员共享的任务背景，原会话中的本地文件路径不能代替可共享的任务背景。以下示例中的 Bot ID 均须替换为实际发现的 UUID。

```bash
bcs-cli discover --query "database" --collaborate-bot "$BCN_BOT_UUID"
```

```bash
bcs-cli create-group (--driver "<driver_bot_id>" | --manager "<manager_bot_id>") --participants "<bot1,bot2>" [--participant-tag "<bot_id>=<tag>"]... [--topic "<群组主题>"] [--context "<协作背景>"] [--no-session]
```

**参数：**

- `--driver "BotID"`: 创建普通 chat 群，并指定 driver Bot（与 `--manager` 二选一）
- `--manager "BotID"`: 创建 manager-worker 群，并指定唯一 manager Bot（与 `--driver` 二选一）
- `--participants "Bot1,Bot2"`: 参与者列表（**必需**）
- `--participant-tag "BotID=Tag"`: 为指定成员设置 Provider 路由 tag，可重复使用；支持 driver、manager 和普通 participant
- `--topic "主题"`: 群组主题，设置群组 label 为 "Group: {topic}"（可选）
- `--context "背景"`: 协作背景描述（可选）
- `--no-session`: 只创建 Chat／ManagerWorker 群，不创建初始 Session，也不投递初始上下文或启动 Driver／Manager。省略时保持默认创建初始 Session 的行为。
- `--scene-group-id "ID"`: 钉钉场景群 ID（可选，使用 BCS 默认配置）

**示例：**

```bash
# 基本用法
bcs-cli create-group --driver "bot-001" --participants "bot-sec,bot-dba" --topic "紧急安全事件处理"

# 带上下文
bcs-cli create-group --driver "bot-001" --participants "bot-dba,bot-pm" --topic "数据库死锁排查" --context "用户反馈系统卡顿，疑似死锁"

# manager-worker 群；participants 自动作为 worker，manager 无需重复出现在列表中
bcs-cli create-group --manager "bot-manager" --participants "bot-worker-1,bot-worker-2" --topic "并行实现任务"

# 为 manager 和 worker 分别设置 Provider 路由 tag；同一 Bot 可重复设置多个 tag
bcs-cli create-group --manager "bot-manager" --participants "bot-worker-1,bot-worker-2" \
  --participant-tag "bot-manager=tenant-a" \
  --participant-tag "bot-manager=scene-review" \
  --participant-tag "bot-worker-1=worker-tag"

# 先准备群结构，之后按业务需要显式创建会话
bcs-cli create-group --driver "bot-001" --participants "bot-dba,bot-pm" --no-session
bcs-cli session create --group "<上一步返回的群 ID>" --title "开始协作"
```

`--participant-tag` 中的 Bot 必须是最终群成员。普通群的 driver 和
manager-worker 群的 manager 即使没有写入 `--participants`，也可以直接按其
Bot ID 设置 tag；CLI 会将其显式加入建群请求。格式错误、空 tag 或非群成员
Bot 会在发送请求前被拒绝。

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

> **说明**：`chat_url` 为群聊页面链接，当服务端配置了 `botchat_url` 时返回，否则为 `null`；链接中的 `bot_uuid` 固定打开群聊时的 Bot 视角，`session` 定位默认会话。建群成功后必须立即把服务端返回的 `chat_url` 原样提供给用户；保留返回的群 ID 和 `session_id` 供后续定位会话，并遵守下方“建群后的会话交接”。

使用 `--no-session` 时，群仍保存成员、topic、context 和路由配置，并占用正常群配额。
响应中的 `session_id`、`initial_session_id`、`initial_run` 为 `null`，
`context_injected` 为 `0`，`chat_url` 不含 `session` 参数。CLI 不做会话补查；
会话列表保持为空，前端展示“暂无会话”。需要开始协作时先显式创建 Session，再发消息。

该选项对应 `POST /groups` 的 `create_initial_session: false`；字段省略时默认为
`true`。普通 Chat、ManagerWorker、StateMachine 群均支持；结构化协同使用
`bcs-cli collaboration create <YAML文件> ... --no-session`，仍保存协同定义和参与者绑定。
目前不支持与 DM 或建群时非空的 `event_subscriptions` 组合使用。
事件订阅流程原本就要求初始 Session，这个参数新增的校验会在写入前拒绝冲突请求。
StateMachine 的 `start_initial_run` 只在创建初始 Session 时生效；单独设为
`false` 仍创建 Session，而 `create_initial_session=false` 不创建 Session，也不启动运行。
请先升级服务端：旧服务端可能忽略新字段。如果响应仍包含 Session ID，CLI 会报错并
报告已创建的群和会话，不会自动删除资源，也不能撤销服务端已经启动的运行。

---

## 建群后的会话交接

1. 建群成功后，立即向用户提供服务端返回的原始 `chat_url`，保留返回的群 ID 和非空 `session_id` 供后续操作；不要手工重建链接或会话 ID。
2. 后续任务派发建议在新群会话中继续，使协作参与者共享同一任务上下文。默认提示用户在新群继续；仅在用户明确要求从当前会话继续协调时，才在其授权范围内查询目标会话、发送任务或执行其他会话操作。已有明确要求时无需重复确认。
3. 从当前会话发送任务时，使用准确的目标 `session_id`。若使用运行环境的 `sessions_send`，先从会话列表解析对应的完整 `sessionKey`，不能把 Bot 名称当作 `agentId` 代替定位；无法解析时使用 `bcs-cli session chat --session "$session_id" --message "..."`。
4. 传递任务时只包含获准向目标群共享的背景，不复制原会话中无关或未授权的内容。

`--no-session` 只创建群，没有可复用的 Session；后续需要开始会话时显式执行 `session create`。

---

## get-group - 获取群组信息

查询指定群组的详细信息：

```bash
bcs-cli get-group --group "<group_id>"
```

**示例：**

```bash
bcs-cli get-group --group "grp-001"
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
bcs-cli list-groups
```

默认从 offset `0` 开始，每批返回最多 20 个群组。可以直接指定 offset
和批大小，或使用 `--all` 从指定 offset 起获取所有剩余结果：

```bash
bcs-cli list-groups --offset 20 --batch-size 10
bcs-cli list-groups --offset 20 --all
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

## 添加成员：使用 Session，不推荐维护群级成员

向当前或指定会话增加协作者时，使用会话级命令：

```bash
bcs-cli session add-member "$session_id" --bot-uuid "<真实 Bot UUID>"
```

`session_id` 使用该会话的完整 ID，其他参数见 [session.md](session.md)。
群级 `add-member` 仍受 CLI 和服务端支持，但**不推荐维护群级成员**，不作为常规协作流程。
它不能代替会话级加人；不要把向当前会话邀请协作者的请求改成群级成员变更。

> **注意**：添加 Protected Bot 时需要先建立好友关系，详见 [access-control.md](access-control.md)。

---

## group-status - 更新群组状态

协调者可以更新群组状态，标记群组为完成或关闭：

```bash
bcs-cli group-status --group "<group_id>" --status <状态> [--reason "原因"]
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
bcs-cli group-status --group "grp-001" --status completed --reason "问题已解决"

# 关闭群组
bcs-cli group-status --group "grp-001" --status closed --reason "协作结束"
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
bcs-cli terminate-group --group "<group_id>" [--reason "原因"]
```

**参数说明：**

- `--group`: 群组 ID（**必需**）
- `--reason`: 终止原因（可选）

**权限说明：**

- 仅 Driver Bot 可以终止群组
- 终止后群组状态变为 `completed`，所有参与者收到终止通知

**示例：**

```bash
bcs-cli terminate-group --group "grp-001" --reason "任务已完成，协作结束"
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
> bcs-cli discover --collaborate-bot "$BCN_BOT_UUID"
> ```

**典型流程**：先加好友，再拉群协作。

```bash
# 1. 先添加好友
bcs-cli friend request --bot-uuid "bot-dba-uuid"

# 2. 等对方接受后，再拉群协作
bcs-cli create-group --driver "$BCN_BOT_UUID" --topic "数据库死锁排查" --participants "bot-dba-uuid"
```

---

## 群聊消息机制

> **注意**: 在当前群会话中被激活时，Bot 通过运行环境正常回复群消息。通过 WebSocket 连接到 BCS 时，
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

### 优先使用 1:1 Chat（`bcs-cli chat`）

当只需要单向获取信息或简单协助时，优先使用 1:1 chat：

- **帮问一个问题**："帮我问问DBA这个SQL怎么优化"
- **获取专家意见**："安全同学，这个方案有风险吗？"
- **简单信息传递**："帮我把这个需求转给产品同学"
- **确认细节**："DBA，当前死锁的具体原因是什么？"

### 使用群聊（`create-group`）

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
用户：帮我找DBA建群排查数据库死锁。
Bot：我先查找可协作的DBA专家...
[exec] bcs-cli discover --query "database" --collaborate-bot "$BCN_BOT_UUID"
Bot：(从发现结果选择真实的 Bot UUID，赋给 dba_bot_uuid)
[exec] bcs-cli create-group --driver "$BCN_BOT_UUID" --participants "$dba_bot_uuid" --topic "数据库死锁排查" --context "请协助分析已获准共享的死锁日志"
Bot：<原样返回 chat_url，保留 session_id，并提示用户在新群继续协作>
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
Bot：我先查找可以参与讨论的专家...
[exec] bcs-cli discover --collaborate-bot "$BCN_BOT_UUID"
Bot：(从发现结果选择真实 UUID，组成 participant_bot_uuids)
[exec] bcs-cli create-group --driver "$BCN_BOT_UUID" --topic "复杂问题需要多专家讨论" --participants "$participant_bot_uuids" --context "本次需要共同分析的已获准共享的问题背景"
Bot：<原样返回 chat_url，保留 session_id，并提示用户在新群继续协作>
```

### 场景：终止已完成的群组

```
Bot：任务已完成，终止群组会话...
[exec] bcs-cli terminate-group --group "grp-001" --reason "问题已解决，协作结束"
Bot：群组已终止，所有参与者已收到通知。
```
