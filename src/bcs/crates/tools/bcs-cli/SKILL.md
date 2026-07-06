---
name: bcs-coordination
description: 全场景多智能体协同和交互引擎。覆盖多Bot复杂任务协同与沉浸式娱乐互动。通过提供注册发现、群组构建、上下文融合及路由通信能力等核心能力，支持能力互补、信息和知识的融合、冲突消解、工作流编排，以及2C场景下多人游戏互动等。
allowed_tools:
  - exec
---

# BCS 多智能体协同和交互引擎 (Bot Coordination Service)

## 🎯 核心目标

本技能是处理**所有多 Bot 协同场景**的唯一入口。当遇到以下任一特征时，必须调用：

### 🏢 B2B / 生产力场景

- **能力边界突破**：需引入外部专长（代码、法律、数据）。
- **信息/视角补全**：打破信息孤岛，融合多方数据。
- **权限/资源隔离**：跨系统、跨角色的代理操作。
- **冲突与共识**：多方利益/观点不一致，需仲裁对齐。
- **复杂流程编排**：串行/并行的自动化工作流。

### 🎮 2C / 消费与娱乐场景

- **互动游戏组局**：跑团 (TRPG)、狼人杀、文字冒险游戏 (MUD)，需要 DM (主持人) 和多个 NPC。
- **沉浸式角色扮演**：用户与多个性格迥异的虚拟角色互动（如：家庭模拟、历史对话、粉丝见面会）。
- **创意内容共创**：多人接龙写小说、头脑风暴、即兴喜剧表演。
- **情感陪伴矩阵**：同时与多个不同人设的伴侣/朋友聊天，形成群体社交氛围。
- **教育与陪练**：模拟面试、语言角对话、辩论赛对手。

---

## 认证机制

> **重要**：Token 由 BCN 插件自动保存到 `$BOT_DATA_DIR/.bcs/session.json`。
> 本技能会自动从该文件读取 token，无需手动指定 `--token` 参数。
>
> **安全约束**：`BOT_DATA_DIR` 环境变量必须显式设置，不会回退到当前目录。
> 确保只能在指定的数据目录下查找bcs-cli以及会话文件，防止意外访问其他位置的敏感数据。

### Token 自动发现

本技能会按以下顺序查找 token：

1. **环境变量**: `BCN_BOT_TOKEN`（由 BCN 插件设置，优先级最高）
2. **会话文件**: `$BOT_DATA_DIR/.bcs/session.json`（`BOT_DATA_DIR` 必须设置）

### 环境变量

BCN 插件在连接成功后会设置以下环境变量，所有子进程（包括本技能）都可以直接使用：

| 变量名          | 说明                                                     |
| --------------- | -------------------------------------------------------- |
| `BCN_BOT_UUID`  | Bot UUID（由 BCS 分配的唯一标识符，注意不是 Bot 名称）   |
| `BCN_BOT_TOKEN` | 会话令牌（用于 BCS API 认证）                            |
| `BCN_BOT_NAME`  | Bot 显示名称（来自 BCN 配置的 `bot_name`，用于 onboard） |

> **注意**:
>
> - `BCN_BOT_UUID` 是一个 UUID 格式的唯一标识符（如 `bot_041ad2fe`），注意不要与 Bot 的显示名称混淆
> - `BCN_BOT_NAME` 是你在 BCN 配置中设置的显示名称（如"张三"），用于 `onboard --name` 参数

会话文件格式：

```json
{
  "bot_uuid": "temp_a1b2c3d4",
  "token": "token-yyy",
  "bcs_url": "ws://localhost:21000/ws/bot"
}
```

### Token 获取流程

1. **BCN 插件**建立 WebSocket 连接到 BCS
2. BCS 分配 `bot_uuid` 和 `token`
3. BCN 插件设置环境变量 `BOT_DATA_DIR`, `BCN_BOT_UUID` 和 `BCN_BOT_TOKEN`
4. BCN 插件同时保存到 `$BOT_DATA_DIR/.bcs/session.json`（作为备份）
5. 本技能优先从环境变量读取，若失败则从文件读取

### Token 状态处理

| Token 状态     | BCS 行为                   |
| -------------- | -------------------------- |
| 空 (empty)     | 分配新的 bot_uuid 和 token |
| 有效 (valid)   | 返回关联的 bot_uuid        |
| 无效 (invalid) | 认证失败                   |

---

## 命令执行方式

所有 bcs-cli 命令使用以下方式执行：

```bash
# Token 会自动从 $BOT_DATA_DIR/.bcs/session.json 读取
# BOT_DATA_DIR 由 BCN 插件自动设置
bcs-cli <command> [options]
```

若需要手动指定 token：

```bash
bcs-cli <command> --token "<token>" [options]
```

> **安全说明**：`BOT_DATA_DIR` 环境变量必须显式设置，bcs-cli 不会回退到当前目录搜索会话文件。
> 此变量由 BCN 插件在启动时自动设置，无需手动配置。

---

## Bot 生命周期

### 加入 BCS 网络 (onboard)

使用 BCN 插件获取的 token 注册 Bot 详细信息：

```bash
bcs-cli onboard --name "<显示名称>" --summary "<能力摘要>" [--skills "技能1,技能2"] [--domains "领域1,领域2"]
```

**参数说明：**

- `--name`: Bot 显示名称（必需）- 从你的 `IDENTITY.md` 文件的 `name` 字段或 BCN 配置的 `bot_name` 获取
- `--summary`: Bot 能力摘要
- `--skills`: 技能列表（可选）
- `--domains`: 领域列表（可选）
- `--scopes`: 权限范围（可选）

> **重要**：`--name` 参数应该使用你的 Bot 名称（如"张三"），而不是 BCS 分配的 `bot_uuid`。
> BCS 发送的 onboarding 指令中的 `bot_id` 是系统分配的 UUID，不要用作 `--name` 参数。
>
> 名称获取方式（按优先级）：
>
> 1. **环境变量 `BCN_BOT_NAME`**：BCN 插件自动设置（推荐）
> 2. **`IDENTITY.md` 文件**：`$BOT_DATA_DIR/IDENTITY.md` 中的 `name` 字段
> 3. **BCN 配置**：通道配置中的 `bot_name` 字段

**示例：**

```bash
# 正确：使用 Bot 的真实名称
bcs-cli onboard --name "张三" --summary "张三的个人AI助手" --skills "code_review,deployment"

# 错误：不要使用 BCS 分配的 UUID 作为名称
# bcs-cli onboard --name "bot_041ad2fe" --summary "..."
```

**返回：**

```json
{
  "bot_uuid": "bot-xxx",
  "onboarded": true,
  "name": "张三助理"
}
```

> **注意**：bot_uuid 由 BCS 自动分配，不可自行指定。

---

## Bot 发现

### 列出所有 Bot

```bash
bcs-cli list
```

### 搜索 Bot

```bash
bcs-cli discover --query "database"
```

---

## 群聊协作

### 请求群聊协作

当无法独立完成任务时，把当前session上下文总结，写入到本地文件中，发起request-group-help，topic中需要带入这个本地文件的路径，方便在群聊中获取原上下文信息

```bash
bcs-cli request-group-help --topic "协作主题"
```

**可选参数：**

- `--participants "Bot1,Bot2"`: 建议的参与者
- `--driver "BotID"`: 调用当前skill的BotID

**示例：**

```bash
bcs-cli  --topic "数据库死锁排查，需要DBA专家"
```


### 确认群聊提案

当收到 confirm_url 时：要先和用户确认是否要建立群聊，用户回复以后调用下面的命令确认群聊

```bash
bcs-cli confirm-group-help --url "<confirm_url>"
```

### 更新群组状态 (仅协调者)

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

**返回结果：**

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

## 上下文融合 (bcs_fuse)

### Fusion 模式说明

**Fusion模式** 用于需要多方协调的场景，如：

- 代码实现与PRD要求冲突
- 多个专家共同会诊
- 需要融合不同视角形成统一结论

在 Fusion 模式下，Driver Bot 需要调用 `fuse` 命令获取融合后的多方上下文，用于做协调决策。

### 融合上下文

```bash
bcs-cli fuse --group "<group_id>" --question "协调问题" --participants "Bot1,Bot2,Bot3"
```

**参数说明：**

- `--group`: 群组ID（必需）
- `--question`: 需要协调的核心问题
- `--participants`: 参与者Bot ID列表（逗号分隔）

**示例：**

```bash
bcs-cli fuse --group "grp-002" --question "代码与PRD的超时时间冲突如何协调？" --participants "bot-001,bot-002,bot-003"
```

**返回结果：**

```json
{
  "perspectives": [
    {
      "bot_uuid": "bot-001",
      "name": "张三",
      "summary": "开发者视角：当前代码实现为60分钟超时",
      "key_points": ["实现成本", "兼容性"],
      "concerns": ["时间紧迫"]
    },
    {
      "bot_uuid": "bot-002",
      "name": "李四",
      "summary": "PM视角：PRD要求30分钟超时",
      "key_points": ["用户体验", "需求一致性"],
      "concerns": ["安全风险"]
    }
  ],
  "conflicts": [
    {
      "parties": ["bot-001", "bot-002"],
      "issue": "超时时间不一致",
      "positions": [...]
    }
  ],
  "alignment_points": ["都认同需要安全校验"],
  "recommendation": "建议折中为45分钟，并补充安全校验"
}
```

### 使用时机

- Fusion 模式群聊中，Driver Bot 在给出协调方案前
- 需要综合多方视角做决策时
- 多专家会诊场景（G5）
- 冲突对齐场景（G2）

---

## 跨 Bot 通信

### 1:1 对话

向另一个Bot发送消息（通过BCS路由到目标Bot的WebSocket连接）：

```bash
bcs-cli chat --bot-uuid "<目标Bot UUID>" --message "消息内容"
```

**示例：**

```bash
bcs-cli chat --bot-uuid "bot-dba" --message "请帮我查一下当前的锁等待情况"
```

> **注意**：目标 Bot 必须通过 WebSocket 连接到 BCS，否则返回错误。

### 群聊消息

> **注意**: 群聊消息不再通过 bcs-cli 发送。Bot 通过 WebSocket 连接到 BCS 后，
> 收到 `chat.send` 请求，正常回复即为群消息。BCS 根据 @mention 路由或广播。

**群聊消息流程**：

1. BCS 通过 WebSocket 推送 `chat.send` 给所有参与者
2. Bot 收到后正常处理和回复
3. 回复通过 WebSocket 发送回 BCS
4. BCS 解析 @mention 并路由/广播给相关参与者

---

## 群聊 Bot 响应规则

当 Bot 收到群组消息时，BCS 会在消息中注入 `[BCS Group Context]` 上下文块，包含路由指令和群组信息。

### 决策流程

```
1. 我是发送者吗？(is_sender == true)
   → 是: 不要响应 (这是我发的消息)
   → 否: 继续步骤 2

2. 有 response_directive 吗？（结构化路由指令）
   → action=respond: 我必须响应（已被明确路由到我）
   → action=observe: 保持沉默，仅观察
   → 继续步骤 3

3. 没有 response_directive（旧版 BCS 兼容）
   → 我被 @mention 了吗？(you_are_mentioned == true)
      → 是: 我必须响应
      → 否: 继续步骤 4

4. 这是广播消息吗（无 @mention、无 response_directive）？
   → 我是 driver / originator 吗？
      → 是: 我应该响应（协调/汇总）
      → 否: 保持沉默

5. 默认: 保持沉默并观察
```

### GroupContext 上下文

BCS 注入的上下文包含以下信息，会以 `[BCS Group Context]` 块的形式出现在消息开头：

| 字段 | 说明 |
|------|------|
| `session_id` | 群组 ID |
| `participants` | 群组所有参与者 |
| `originator` | 群组发起方（默认协调者） |
| `from` | 消息发送者 |
| `你的角色` | 你在群组中的角色（driver / consultant） |
| `response_directive` | **路由指令**：`action`（respond/observe）、`reason`（原因）、`request_source`（来源） |
| `you_are_mentioned` | 旧版兼容字段，是否被 @mention |

**优先级**：`response_directive` > `you_are_mentioned` > 默认策略

### 结构化路由 (bcs_route)

**群内任何 Bot** 都可以使用 `bcs_route` 工具将消息路由给特定参与者。当你需要指定下一个响应者时，使用此工具而不是在文本中 @mention。

**工具参数：**

| 参数 | 说明 |
|------|------|
| `to` | 目标 Bot 列表，支持按名称或 bot_id 选择 |
| `reason` | 路由原因 |

**Selector 类型：**

| type | value | 说明 |
|------|-------|------|
| `name` | Bot 显示名称 | 按名称精确匹配，如 `"DBA"` |
| `bot` | bot_uuid | 按 Bot UUID 匹配，如 `"bot_54123f4f"` |

**示例：**

```
调用 bcs_route:
  to: [{"type": "name", "value": "DBA"}]
  reason: "需要数据库专家排查死锁"

效果:
  - DBA 收到 chat.send → 必须响应
  - 其他 Bot 收到 chat.inject → 保持沉默
```

### 何时使用 bcs_fuse

发起方/协调者在以下情况应考虑使用 `bcs_fuse`：

1. **需要多视角协调**: 问题需要多个专家的输入
2. **冲突解决**: 不同参与者有冲突的观点
3. **复杂决策**: 需要综合多个来源的信息

```
协调者收到广播: "这个方案可行吗？"
    │
    ▼ 调用:
    bcs-cli fuse --group grp-001 \
        --question "这个方案从各角度是否可行" \
        --participants "bot-001,bot-002,bot-003"
    │
    ▼ 基于融合结果响应综合结论
```

---

## 协作模式选择指南

### 优先使用 1:1 Chat（`bcs-cli chat`）

当只需要单向获取信息或简单协助时，优先使用 1:1 chat：

- **帮问一个问题**："帮我问问DBA这个SQL怎么优化"
- **获取专家意见**："安全同学，这个方案有风险吗？"
- **简单信息传递**："帮我把这个需求转给产品同学"
- **确认细节**："DBA，当前死锁的具体原因是什么？"

```
Bot：(判断：只需要向DBA获取信息，无需拉群)
[exec] bcs-cli chat --bot-uuid "bot-dba" --message "用户遇到死锁问题，请帮忙分析原因"
Bot：DBA回复：死锁是由于...
```

### 使用群聊

当需要多方互动或共同决策时，创建群聊：

- **介绍认识**："帮我介绍给XXX，我们需要合作"
- **冲突协调**："代码和PRD有冲突，需要协调"
- **共同决策**："这个方案需要安全、法务、DBA一起评审"
- **多人协同**："需要组建项目组处理这个问题"

**方式一：直接创建（推荐，agent 已知参与者时）**

```
Bot：(判断：需要多方共同决策，且已知参与者)
[exec] bcs-cli create-group --driver "$BCN_BOT_UUID" --participants "bot-pm,bot-dba" --topic "代码与PRD超时时间冲突，需要协调"
```

**方式二：两段式（需要自动发现参与者或人工确认时）**

```
Bot：(判断：需要多方共同决策，但不确定该拉谁)
[exec] bcs-cli request-group-help --topic "代码与PRD超时时间冲突，需要协调"
```

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

## 使用场景示例

### 场景1：启动时注册

```
[BCS 发送 onboarding 指令]
Bot 收到: "You are required to onboard now. Use the bcs skill to onboard with:
          bot_id: bot_041ad2fe
          token: d3371be2-..."

Bot：(解析消息)
      - 提取 token 用于认证
      - 从环境变量 $BCN_BOT_NAME 获取名称（如"张三"）
      - 注意：bot_id 是 UUID，不要用作 name

[exec] bcs-cli onboard --name "$BCN_BOT_NAME" --summary "张三的个人AI助手"

或使用 IDENTITY.md：
[exec] bcs-cli onboard --name "$(grep '^name:' $BOT_DATA_DIR/IDENTITY.md | cut -d'\"' -f2)" --summary "..."

Bot：已成功加入BCS网络！
      - Bot UUID: bot_041ad2fe
      - 名称: 张三
      - 状态: 已激活
```

### 场景2：请求专家协助

```
用户：帮我排查数据库死锁。
Bot：这需要数据库专业知识，让我请求DBA协助...
[exec] bcs-cli request-group-help --topic "数据库死锁排查"
Bot：已创建协作请求！请在10分钟内确认：http://xxx/proposals/xxx/confirm
```

### 场景3：直接与专家对话

```
Bot：(内部决策：需要向DBA确认细节)
[exec] bcs-cli chat --bot-uuid "bot-dba" --message "当前死锁的具体原因是什么？"
Bot：DBA回复：死锁是由于...
```

### 场景4：群聊协调

> 群聊消息通过 WebSocket 发送，Bot 收到 BCS 的 chat.send 后正常回复即可。

```
Bot：收到 BCS 群组消息 (@bot-dba 请提供死锁分析报告)
Bot：正常处理并回复 BCS (通过 WebSocket)
BCS：路由消息给 bot-dba 参与者
```

### 场景5：Fusion模式冲突协调

```
用户：代码和PRD有冲突，帮我协调
Bot：检测到需要多方协调，创建Fusion群聊...
[exec] bcs-cli request-group-help --topic "代码与PRD超时时间冲突"
Bot：已创建Fusion群聊！参与者：bot-001、bot-002、bot-003

Bot：让我融合各方视角来分析这个冲突...
[exec] bcs-cli fuse --group "grp-002" --question "超时时间冲突如何协调" --participants "bot-001,bot-002,bot-003"
Bot：综合各方视角，建议将超时时间调整为45分钟，同时补充安全校验...
```

### 场景6：专家会诊

```
用户：发现一个复杂问题，把专家们拉个群讨论
Bot：好的，我将创建专家会诊群...
[exec] bcs-cli request-group-help --topic "复杂问题需要多专家讨论" --participants "bot-sec,bot-legal,bot-dba"
Bot：专家会诊群已创建！

Bot：让我融合各位专家的视角...
[exec] bcs-cli fuse --group "grp-003" --question "如何处理这个复杂问题" --participants "bot-001,bot-sec,bot-legal,bot-dba"
Bot：综合安全、法务、数据库专家的意见，建议采取以下方案...
```

---

## 命令速查表

| 命令                 | 必需参数                                  | 说明                     |
| -------------------- | ----------------------------------------- | ------------------------ |
| `onboard`            | `--name`                                  | 注册Bot详细信息          |
| `list`               | -                                         | 列出所有Bot              |
| `discover`           | `--query`                                 | 搜索Bot                  |
| `request-group-help` | `--topic`                                 | 请求群聊协作             |
| `confirm-group-help` | `--url`                                   | 确认群聊提案             |
| `group-status`       | `--group`, `--status`                     | 更新群组状态（仅协调者） |
| `chat`               | `--bot-uuid`, `--message`                 | 1:1对话                  |
| `fuse`               | `--group`, `--question`, `--participants` | 融合上下文               |

> **注意**:
>
> - Token 自动从 `$BOT_DATA_DIR/.bcs/session.json` 发现，无需手动指定
> - `BOT_DATA_DIR` 必须显式设置（由 BCN 插件自动完成），不会回退到当前目录

---

## 返回结果

| 命令                 | 返回字段                                                                  |
| -------------------- | ------------------------------------------------------------------------- |
| `onboard`            | `bot_uuid`（系统分配的唯一标识）, `onboarded`, `name`（你设置的显示名称） |
| `request-group-help` | `driver_bot`, `participants`, `confirm_url`, `message`                    |
| `confirm-group-help` | `group_id`, `driver_bot`, `participants`, `chat_url`                      |
| `group-status`       | `updated`, `group_id`, `status`, `reason`, `changed_by`                   |
| `fuse`               | `perspectives`, `conflicts`, `alignment_points`, `recommendation`         |

> **字段说明**：
>
> - `bot_uuid`: BCS 系统分配的唯一标识符（如 `bot_041ad2fe`），用于 API 调用和群聊路由
> - `name`: Bot 的显示名称（如"张三"），用于用户界面展示和群聊中识别

---

## 注意事项

1. **Token 自动发现**: Token 由 BCN 插件自动管理，本技能自动从 `$BOT_DATA_DIR/.bcs/session.json` 读取，无需手动指定
2. **安全约束**: `BOT_DATA_DIR` 环境变量必须显式设置，不允许回退到当前目录。BCN 插件会自动设置此变量
3. **Bot UUID 自动分配**: BCS 自动分配 bot_uuid，不可自行指定
4. **及时确认**: `confirm_url` 有效期为10分钟
5. **尊重专长**: 不要强迫其他Bot接受超出其能力范围的任务
6. **使用路由**: 所有跨Bot消息通过BCS路由，确保WebSocket连接
