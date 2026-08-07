> **历史归档 / 非实现基线**：本文记录早期讨论方案，可能包含已废弃的 `role`、`edge_id` 下发、`permission_profiles[]` / `rules_grants[]` 拆分、`extra_rules` 等设计。实现请以 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-final-design.md` 和 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-implementation-spec.md` 为准。

# BCS × A2A 协作鉴权设计

日期：2026-07-31

## 0. 一句话结论

BCS 不再自定义一套散的 bot 协作协议，而是把 bot 间协作统一放到 A2A 协议中：

```text
A2A 负责：AgentCard、skills 发现、message/task/artifact/status、协议交互
BCS 负责：RoleDef、EdgeGrant、TaskCtx、AuthSnapshot、originator、participants、审计
BCS A2A Extension 负责：把 BCS runtime 鉴权上下文放进 A2A message
```

核心原则：

```text
A2A 是协作协议外壳。
BCS 是协作鉴权权威。
Bot 本地插件是鉴权执行点。
```

---

## 1. 总体主线

完整流程分成两个阶段。

### 1.1 task 前：发现与授权

```text
BCS 管理 RoleDef
  ↓
BCS 生成 AgentCardSnapshot
  ↓
Bot 本地 A2A Server 返回 AgentCardSnapshot
  ↓
caller bot 发现 target bot 的 skills
  ↓
caller bot 申请某个 skill/role
  ↓
申请经 BCS A2A Gateway 发给 target bot
  ↓
target bot/plugin 通知 owner/user 审批，或执行预设白名单策略
  ↓
审批结果写回 BCS
  ↓
BCS 生成 EdgeGrant
  ↓
BCS 生成并下发 AuthSnapshot 给 target bot 本地 auth plugin
```

MVP 约束：

```text
权限必须在 task 开始前申请好。
runtime 中不动态申请新权限。
```

### 1.2 task 中：A2A runtime 协作

```text
human 发起任务
  ↓
BCS 创建 TaskCtx
  ↓
caller bot 通过 A2A message 调 target bot
  ↓
所有 bot-to-bot 调用经过 BCS A2A Gateway
  ↓
Gateway 认证 caller、检查 TaskCtx/participants、读取 originator、匹配 EdgeGrant
  ↓
Gateway 在 A2A BCS extension 中写入并盖章 runtime context
  ↓
target bot 收到 A2A request
  ↓
本地 auth plugin 作为 middleware 先执行
  ↓
AuthSnapshot + runtime context 激活 EffectivePermission
  ↓
通过后进入业务 handler
  ↓
返回 A2A artifact/status
  ↓
BCS audit 用 trace_id/dispatch_id 串起完整链路
```


---

## 1.3 A2A 协议骨架：我们到底用 A2A 的哪些东西

本设计不是“借 A2A 名字”，而是直接按 A2A 对象和操作组织 BCS 协作。

A2A 原生主对象：

```text
AgentCard  # agent 自描述：我是谁、我有哪些 skills、怎么认证、支持什么协议
AgentSkill # AgentCard.skills[] 里的单个能力描述
Message    # 一次输入/指令/交互消息
Task       # 一次协作任务的 A2A 状态容器
Part       # Message/Artifact 里的内容片段：text/raw/url/data
Artifact   # task 输出结果
TaskStatus # task 当前状态
```

A2A 原生主操作：

```text
Get AgentCard              # 发现 agent 能力
GetExtendedAgentCard       # 认证后获取扩展卡片；MVP 暂不依赖
SendMessage                # 发消息，启动或继续 task
SendStreamingMessage       # 发消息并通过 stream 获取更新
GetTask                    # 查询 task 状态
CancelTask                 # 取消 task
SubscribeToTask            # 订阅 task 更新
Push Notification          # 长任务完成后回调通知
```

MVP 使用方式：

```text
AgentCard/skills     -> 发布 target bot 可申请 RoleDef
SendMessage          -> 权限申请、审批交互、runtime bot-to-bot 调用
Task/status          -> 表示申请流程或业务协作流程的状态
Artifact             -> 表示 target bot 输出结果
metadata/extensions  -> 承载 BCS extension 上下文
securitySchemes      -> 认证 BCS Gateway，不表达 EdgeGrant
```

---

## 1.4 BCS 模型到 A2A 的映射总表

| BCS 模型 | A2A 承载 | 说明 |
| --- | --- | --- |
| Bot / target bot | A2A Agent Server | bot 本地作为 A2A server 暴露能力和接收消息 |
| Bot profile | AgentCard 基础字段 | name、description、provider、version 等 |
| RoleDef[] | AgentCard.skills[] | MVP 中每个 `skill.id == RoleDef.id`；一个 bot 可以有多个 roles/skills |
| RoleDef.rules[] / permissions[] | AgentSkill.metadata.bcs.permissions[] | 一个 role 有多条 rule；直接暴露该 role 对应权限集的对外表示 |
| AgentCardSnapshot | bot 本地返回的 AgentCard | BCS 生成并签名/带版本，下发给 bot |
| EdgeGrant[] | 不直接放 A2A 原生对象 | caller -> target 可以有多条 grant；审批通过后由 BCS 写库，进入 AuthSnapshot |
| AuthSnapshot | 不走 A2A message | BCS control plane/plugin sync 下发给 target auth plugin |
| SessionContext.id | A2A `contextId` | 会话上下文 ID |
| TaskCtx.task_id | A2A message.taskId / Task.id | MVP 中一个 session 一个 BCS task；bot 间协作沿用这个 taskId |
| TaskCtx.current_originator | Message.metadata.bcs.current_originator_id | caller 可给 hint，Gateway 以 TaskCtx 为准盖章 |
| originator_epoch | Message.metadata.bcs.originator_epoch | 防旧 originator 上下文复用 |
| participants | Message.metadata.bcs.participants_hint | 只作为 caller 提交的非权威 hint；BCS 以 TaskCtx.participants 为准检查/覆盖/驳回 |
| caller_actor_id | Message.metadata.bcs.caller_actor_id | Gateway 认证后写入 |
| EffectivePermission | 不放 A2A | target auth plugin 用 AuthSnapshot + runtime context 激活 |
| dispatch_id / trace_id | Message.metadata.bcs.dispatch_id / trace_id | A2A 对象与 BCS audit 关联 |
| runtime 拒绝 | TaskStatus.state = TASK_STATE_REJECTED | BCS error 放 metadata/error details |
| 业务结果 | Artifact | 结果不要塞回 Message |

---

## 1.5 A2A AgentCardSnapshot 示例

Bot 本地返回的是 BCS 生成的 AgentCardSnapshot。

```json
{
  "name": "Calendar Bot",
  "description": "Manage calendar availability and meeting scheduling.",
  "version": "2026.07.31-1",
  "supportedInterfaces": [
    {
      "url": "https://calendar-bot.local/a2a/v1",
      "protocolBinding": "HTTP+JSON",
      "protocolVersion": "1.0"
    }
  ],
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extensions": [
      {
        "uri": "https://avernet.ai/a2a/extensions/bcs-cooperation-auth/v1",
        "description": "BCS cooperation authorization runtime context.",
        "required": true
      }
    ]
  },
  "securitySchemes": {
    "bcs_gateway_bearer": {
      "httpAuthSecurityScheme": {
        "scheme": "Bearer",
        "bearerFormat": "BCS-Gateway-JWT",
        "description": "Only BCS A2A Gateway may call this bot endpoint."
      }
    }
  },
  "securityRequirements": [
    { "schemes": { "bcs_gateway_bearer": { "list": [] } } }
  ],
  "defaultInputModes": ["application/json", "text/plain"],
  "defaultOutputModes": ["application/json", "text/plain"],
  "skills": [
    {
      "id": "meeting_scheduler",
      "name": "Meeting Scheduler",
      "description": "Schedule meetings for the current originator.",
      "tags": ["calendar", "meeting"],
      "inputModes": ["application/json", "text/plain"],
      "outputModes": ["application/json"],
      "metadata": {
        "bcs": {
          "role_def_id": "meeting_scheduler",
          "approval_required": true,
          "permissions": [
            {
              "capability": "calendar.read_availability",
              "scope": {
                "calendar_owner": "current_originator",
                "time_range": "next_30_days"
              },
              "decision": "allow"
            },
            {
              "capability": "calendar.create_event",
              "scope": {
                "calendar_owner": "current_originator"
              },
              "decision": "allow"
            }
          ]
        }
      }
    }
  ],
  "signatures": [
    {
      "protected": "<bcs-jws-protected-header>",
      "signature": "<bcs-signature>"
    }
  ],
  "metadata": {
    "bcs": {
      "agent_card_snapshot_version": 17,
      "agent_card_snapshot_digest": "sha256:..."
    }
  }
}
```

注意：上面示例只展开了一个 skill。真实 AgentCardSnapshot 中 `skills` 是数组，可以同时包含：

```text
skills[] = [calendar_reader, meeting_scheduler, calendar_writer, ...]
```

每个 skill 里面的 `metadata.bcs.permissions` 也是数组。

这里最重要的映射：

```text
skills[0].id = meeting_scheduler
= RoleDef.id
= 权限申请中的 skill_id
= 审批通过后的 EdgeGrant.role_def_id
```

---

## 1.6 A2A 权限申请 Message 示例

权限申请也走 A2A `SendMessage`，但语义上发给 target bot。

```json
{
  "message": {
    "messageId": "msg_perm_req_001",
    "role": "ROLE_USER",
    "parts": [
      {
        "data": {
          "type": "bcs.permission_request",
          "target_bot_id": "calendar_bot",
          "skill_id": "meeting_scheduler",
          "requested_scope": {
            "calendar_owner": "current_originator",
            "time_range": "next_30_days"
          },
          "reason": "Email bot needs to schedule meetings for the user."
        }
      }
    ],
    "extensions": [
      "https://avernet.ai/a2a/extensions/bcs-cooperation-auth/v1"
    ],
    "metadata": {
      "bcs": {
        "request_kind": "permission_request",
        "caller_actor_id_hint": "email_bot"
      }
    }
  }
}
```

Gateway 处理：

```text
1. 认证 caller，得到真实 caller_actor_id=email_bot
2. 校验 target_bot_id / skill_id 是否存在
3. 记录申请并转发给 target bot plugin
4. target bot/plugin 通知 owner/user 审批或执行白名单策略
5. 审批结果写回 BCS
6. BCS 生成 EdgeGrant(email_bot -> calendar_bot, role_def_id=meeting_scheduler)
7. BCS 更新 AuthSnapshot
```

---

## 1.7 A2A runtime SendMessage 示例

先明确 A2A 原生形态：

```text
SendMessageRequest 里放的是 message，不是完整 Task 对象。
Task 通常是 target agent 处理 message 后返回的状态对象。
message.taskId 只用于继续一个已经存在的 A2A Task。
第一次给某个 target bot 发起新工作时，通常不带 message.taskId，只带 contextId。
```

所以 BCS 的权威 `TaskCtx.task_id` 不应强行等同于 A2A `Task.id`。

MVP 映射：

```text
A2A contextId = BCS SessionContext.id
A2A message.taskId = BCS TaskCtx.task_id
```

也就是说：一个 session 的 BCS task 先确定好；bot A 找 bot B 办事时，沿用这个 taskId，不由 bot B 另起一个新的协作 task。

caller bot 调 target bot 时，也用 A2A `SendMessage`。

caller 原始请求可以带 hint，但不权威：

```json
{
  "message": {
    "messageId": "msg_runtime_001",
    "contextId": "session_1",
    "taskId": "task_1",
    "role": "ROLE_USER",
    "parts": [
      {
        "data": {
          "type": "calendar.schedule",
          "attendees": ["bob@example.com"],
          "duration_minutes": 30
        }
      }
    ],
    "extensions": [
      "https://avernet.ai/a2a/extensions/bcs-cooperation-auth/v1"
    ],
    "metadata": {
      "bcs": {
        "target_bot_id": "calendar_bot",
        "current_originator_id": "human_alice",
        "originator_epoch": 3,
        "participants_hint": ["email_bot"]
      }
    }
  }
}
```

Gateway 转发给 target bot 前，必须重写/盖章 BCS context：

```json
{
  "message": {
    "messageId": "msg_runtime_001",
    "contextId": "session_1",
    "taskId": "task_1",
    "role": "ROLE_USER",
    "parts": [
      {
        "data": {
          "type": "calendar.schedule",
          "attendees": ["bob@example.com"],
          "duration_minutes": 30
        }
      }
    ],
    "extensions": [
      "https://avernet.ai/a2a/extensions/bcs-cooperation-auth/v1"
    ],
    "metadata": {
      "bcs": {
        "caller_actor_id": "email_bot",
        "target_bot_id": "calendar_bot",
        "current_originator_id": "human_alice",
        "originator_epoch": 3,
        "participants_hint": ["email_bot"],
        "task_ctx_version": 7,
        "min_auth_snapshot_version": 12,
        "trace_id": "trace_abc",
        "dispatch_id": "dispatch_001",
        "gateway_attestation": "<bcs-gateway-signature-or-token>"
      }
    }
  }
}
```

注意：

```text
metadata.bcs.current_originator_id 可以由 caller 提交为 hint。
但 target bot 只信 Gateway 转发后盖章的值。
```

---

## 1.8 A2A runtime 返回示例

业务通过后，target bot 返回 A2A Task/Artifact。

```json
{
  "task": {
    "id": "task_1",
    "contextId": "session_1",
    "status": {
      "state": "TASK_STATE_COMPLETED"
    },
    "artifacts": [
      {
        "artifactId": "artifact_calendar_event_001",
        "name": "created_calendar_event",
        "parts": [
          {
            "data": {
              "event_id": "evt_123",
              "start": "2026-08-03T16:00:00-07:00",
              "end": "2026-08-03T16:30:00-07:00"
            }
          }
        ],
        "metadata": {
          "bcs": {
            "trace_id": "trace_abc",
            "dispatch_id": "dispatch_001"
          }
        }
      }
    ],
    "metadata": {
      "bcs": {
        "trace_id": "trace_abc",
        "dispatch_id": "dispatch_001"
      }
    }
  }
}
```

权限不足时：

```json
{
  "task": {
    "id": "task_1",
    "contextId": "session_1",
    "status": {
      "state": "TASK_STATE_REJECTED",
      "message": {
        "messageId": "msg_denied_001",
        "role": "ROLE_AGENT",
        "parts": [
          { "text": "BCS authorization denied." }
        ],
        "metadata": {
          "bcs": {
            "error_code": "BCS_DENIED",
            "trace_id": "trace_abc",
            "dispatch_id": "dispatch_001"
          }
        }
      }
    }
  }
}
```

---

## 1.9 A2A 操作到 BCS 流程映射

| A2A 操作 | BCS 场景 | BCS 处理 |
| --- | --- | --- |
| Get AgentCard | 发现 target bot 可申请 roles | bot 返回 BCS 下发的 AgentCardSnapshot |
| SendMessage + `type=bcs.permission_request` | task 前权限申请 | Gateway 路由到 target bot/plugin，owner/user 审批 |
| SendMessage + `type=bcs.permission_approval_result` | 审批结果回写 | BCS 校验后创建/拒绝 EdgeGrant |
| SendMessage | runtime bot-to-bot 调用 | Gateway admission + BCS context 盖章 + target 本地鉴权 |
| SendStreamingMessage | runtime 流式调用 | 同 SendMessage，但返回 stream updates |
| GetTask | 查询申请/业务任务状态 | A2A task 状态；安全事实查 BCS audit |
| CancelTask | 取消申请/业务任务 | 更新 A2A task；必要时记录 BCS audit |
| SubscribeToTask | 订阅申请/业务进展 | A2A stream；BCS trace_id 关联 |
| Artifact | 业务输出 | artifact metadata 只放 trace/dispatch 关联，不放权限事实 |


## 2. 组件分工

### 2.1 BCS Control Plane

负责权威配置和快照：

```text
RoleDef 管理
EdgeGrant 管理
BotCapabilityRegistry 管理
AgentCardSnapshot 生成
AuthSnapshot 生成
快照版本/签名/失效
审计落库
```

### 2.2 BCS A2A Gateway

负责 runtime 可信路由和盖章：

```text
认证 caller bot
路由 A2A message
维护 TaskCtx
维护 participants
读取 current_originator
检查 EdgeGrant
写入 BCS A2A extension runtime context
生成 trace_id / dispatch_id
```

MVP 规定：

```text
所有 bot-to-bot runtime 调用必须经过 BCS A2A Gateway。
不允许 bot 绕过 Gateway 直接调用另一个 bot 的 A2A endpoint。
```

### 2.3 target bot 本地 A2A Server

负责 A2A 服务能力：

```text
返回 BCS 下发的 AgentCardSnapshot
接收 A2A message
验证请求来自可信 BCS Gateway
调用本地 auth plugin
执行业务能力
返回 artifact/status
```

### 2.4 target bot 本地 auth plugin

负责本地鉴权执行：

```text
持有 AuthSnapshot
读取 BCS runtime context
激活 EffectivePermission
在业务 handler 前 fail-closed
```

---

## 3. AgentCardSnapshot：BCS RoleDef 如何进入 A2A

### 3.0 数组关系必须明确

一个 bot 不是只有一个 role，一个 role 也不是只有一条 rule。

MVP 基本基数：

```text
Bot
  has many RoleDef[]

RoleDef
  has many rules[] / permissions[]

AgentCardSnapshot
  has many skills[]

AgentCardSnapshot.skills[]
  one-to-one projects RoleDef[]

AgentSkill.metadata.bcs.permissions[]
  projects RoleDef.rules[] 的对外表示

EdgeGrant[]
  caller -> target 可以有多条 active grants
```

所以文档中凡是表达多个角色/规则时，应写成：

```text
RoleDef[]
rules[]
permissions[]
skills[]
EdgeGrant[]
```

不要写成像单个 role/rule，否则会误解为一个 bot 只能有一个角色。

### 3.1 RoleDef 是源头

MVP 定义：

```text
RoleDef 是 canonical source。
AgentCard.skills[] 是 RoleDef[] 的 A2A projection。
```

也就是说：

```text
BCS RoleDef[]
  ↓ 渲染
AgentCardSnapshot.skills[]
  ↓ 下发
bot 本地 A2A Server 返回
```

Bot runtime 不能自己伪造、增加、删除 `AgentCard.skills`。

---

### 3.2 skill.id 直接等于 RoleDef.id

MVP 不再增加中间层。

```text
AgentCard.skills[].id == RoleDef.id
```

不要设计：

```text
AgentSkill -> 多个 RoleDef
GrantTemplate / PermissionPackage
```

这些会让模型绕回去并变复杂。

---

### 3.3 AgentCard.skills[] 直接暴露 roles[] 和 permissions[]

MVP 为了简单，不做二次 `bcs.role.describe` 查询。

AgentCardSnapshot 中直接暴露多个 skills[]；每个 skill 对应一个 RoleDef，并带该 RoleDef 的 permissions[]：

```text
skills[]
  - id                  # RoleDef.id
    name                # RoleDef display name
    description
    approval_required
    permissions[]       # RoleDef.rules[] 的对外表示
```

示例：

```yaml
skills:
  - id: meeting_scheduler        # 等于 RoleDef.id
    name: Meeting Scheduler
    description: 帮用户安排会议
    metadata:
      bcs:
        role_def_id: meeting_scheduler
        approval_required: true
        permissions:
          - capability: calendar.read_availability
            scope:
              calendar_owner: current_originator
              time_range: next_30_days
          - capability: calendar.create_event
            scope:
              calendar_owner: current_originator
```

含义：

```text
caller 申请 meeting_scheduler 这个 A2A skill
= 申请 target bot 的 meeting_scheduler RoleDef
```

审批通过后：

```text
EdgeGrant.role_def_id = meeting_scheduler
```

---

### 3.4 A2A message.role 不等于 BCS RoleDef

必须显式区分：

```text
A2A message.role 是消息角色，例如 user/agent。
BCS RoleDef 是授权角色/权限集。
```

BCS RoleDef 只出现在：

```text
AgentCard.skills[].id
permission request.skill_id
EdgeGrant.role_def_id
AuthSnapshot.RoleDef
```

不允许用 A2A `message.role` 表达 BCS 权限角色。

---

## 4. AgentCardSnapshot 与 AuthSnapshot

两者独立，不能合并。

### 4.1 AgentCardSnapshot

用途：A2A 发现/发布。

内容来自：

```text
RoleDef[] + BotProfile + security declaration + BCS extension declaration
```

给 caller 看：

```text
target bot 有哪些可申请 skill/role
每个 role 对应什么 permissions[]/rules[]
怎么申请
```

### 4.2 AuthSnapshot

用途：本地 runtime 鉴权。

内容来自：

```text
RoleDef[] + EdgeGrant[] + PlatformGuard + BotCapabilityRegistry
```

给 target bot 本地 auth plugin 用：

```text
根据 caller + target + current_originator + task_id 激活 EffectivePermission
```

### 4.3 为什么独立

生命周期不同：

```text
EdgeGrant 审批/撤销
=> AuthSnapshot 变化
=> AgentCardSnapshot 不一定变化

bot 展示信息变化
=> AgentCardSnapshot 变化
=> AuthSnapshot 不应该变化

RoleDef 权限语义变化
=> AgentCardSnapshot 和 AuthSnapshot 都变化
```

---

## 5. A2A securitySchemes 怎么结合 BCS

A2A `securitySchemes` 只解决连接认证：

```text
target bot 如何确认请求来自可信 BCS Gateway
```

它不表达：

```text
caller 是否能调用 target
originator 是谁
participants 是否可信
EdgeGrant 是否匹配
RoleDef 是否授权
```

示例：

```yaml
securitySchemes:
  bcs_gateway_bearer:
    type: http
    scheme: bearer

securityRequirements:
  - bcs_gateway_bearer: []
```

MVP 信任模型：

```text
target bot 只认证 BCS A2A Gateway。
原始 caller bot 身份由 Gateway 认证后写入 BCS extension。
```

---

## 6. BCS A2A Extension

### 6.1 extension 的定位

BCS A2A Extension 不传完整权限集。

它只定义：

```text
BCS runtime 鉴权上下文字段放在哪里
字段叫什么
谁能写
谁能信
如何校验
```

权限事实仍在：

```text
BCS Control Plane
AuthSnapshot
EdgeGrant
RoleDef
```

---

### 6.2 runtime 最小字段

A2A 原生字段：

```text
contextId = BCS SessionContext.id
taskId    = BCS TaskCtx.task_id
messageId = A2A message id
parts     = A2A payload
```

BCS extension 字段：

```yaml
metadata:
  bcs:
    caller_actor_id: bot_email
    target_bot_id: bot_calendar
    current_originator_id: human_alice
    originator_epoch: 3
    task_ctx_version: 7
    min_auth_snapshot_version: 12
    trace_id: trace_123
    dispatch_id: dispatch_456
```

字段说明：

```text
caller_actor_id
# Gateway 认证出的真实 caller，用于匹配 EdgeGrant(caller -> target)

target_bot_id
# 本次目标 bot，防止错投/转发后误用

current_originator_id
# TaskCtx 当前 human originator，用于 originator_policy 和 scope 激活

originator_epoch
# originator 版本，防止旧上下文被复用

task_ctx_version
# target 看到的是哪个 TaskCtx 版本

min_auth_snapshot_version
# target 执行前至少要持有的 AuthSnapshot 版本；不足则刷新，刷新失败 deny

trace_id
# 多跳协作链路追踪 ID

dispatch_id
# 本次 caller -> target 调用审计 ID
```

---

### 6.3 runtime message 不传这些

不传权威权限事实：

```text
完整 AuthSnapshot
完整 EdgeGrant
完整 RoleDef
完整 participants
caller 自报 permission result
```

---

## 7. 权限申请/审批如何走 A2A

### 7.1 申请对象

申请对象是：

```text
skill_id
```

但 MVP 中：

```text
skill_id == RoleDef.id
```

申请通过后：

```text
EdgeGrant(caller -> target, role_def_id = skill_id)
```

---

### 7.2 申请发给谁

权限申请语义上发给 target bot。

不是 BCS Gateway 自己替用户决定。

链路：

```text
caller bot
  -> BCS A2A Gateway
  -> target bot A2A endpoint
  -> target bot plugin / frontend approval
  -> BCS control plane
```

分工：

```text
BCS Gateway：认证、路由、记录申请、基础校验、审计
target bot/plugin：申请接收方、审批入口、用户策略执行点
target owner/user：最终授权决策者
BCS control plane：写 EdgeGrant、生成快照、下发 AuthSnapshot
```

---

### 7.3 task 前置授权

MVP 只支持 task 前已经授权好。

```text
runtime 中不动态弹权限申请。
```

如果 runtime 权限不足：

```text
返回 A2A REJECTED + BCS extension error
```

不用 `AUTH_REQUIRED`，因为 MVP 不支持当前任务中继续授权后恢复。

---

## 8. TaskCtx 如何映射 A2A

### 8.1 contextId / taskId

MVP 直接把 BCS 的 session/task 放进 A2A message：

```text
A2A contextId = BCS SessionContext.id
A2A message.taskId = BCS TaskCtx.task_id
```

含义：

```text
一个 session 当前只有一个 active TaskCtx。
human 发起任务时，BCS 先创建 TaskCtx.task_id。
之后 bot A 找 bot B 协作时，沿用这个 taskId。
不是 bot B 重新创建一个新的协作 task。
```

关系：

```text
TaskCtx.session_id = SessionContext.id
SessionContext.active_task_id = TaskCtx.task_id
```

A2A `SendMessageRequest` 仍然是发送 `message`，不是发送完整 Task 对象；但这个 message 里携带的 `taskId` 就是 BCS 已创建的 TaskCtx.task_id。target bot 可以在响应中返回同一个 `Task.id = taskId`。

---

### 8.2 TaskCtx 权威状态

A2A message 携带 `contextId` / `taskId`；其中 `taskId` 就是 BCS 已创建的 TaskCtx.task_id。

但 TaskCtx 权威状态只在 BCS：

```text
current_originator
originator_epoch
participants
task_ctx_version
status
```

BCS 不信 bot 自报的 TaskCtx 内容。

Gateway 根据 `taskId` 查权威 TaskCtx 后再盖章 runtime context。

---

## 9. originator 语义

### 9.1 current_originator

MVP 使用：

```text
current_originator
```

不是 kickoff 固定 originator。

规则：

```text
human 消息更新 current_originator，并递增 originator_epoch
bot 消息不更新 originator
bot 不能自己决定 originator
```

---

### 9.2 A2A 中如何携带

caller bot 可以在 A2A message 中提交 originator 相关字段，但它只是 hint：

```text
current_originator_id
originator_epoch
```

Gateway 必须检查：

```text
缺失：Gateway 补齐
一致：Gateway 盖章转发
不一致：MVP 直接 reject，并记录审计
```

target bot 只信 Gateway 盖章后的 originator 字段。

---

## 10. participants 语义

participants 定义：

```text
BCS 已经把该 task 的可信上下文交付过的 bot 集合。
```

用途：

```text
防止 bot 拿别人的 task_id 冒用别人的 current_originator。
```

MVP 规则：

```text
participants 是 BCS TaskCtx 中的权威状态。
A2A runtime message 可以带 participants_hint，但它不是权威 participants。
BCS Gateway 必须以 TaskCtx.participants 为准检查、覆盖或驳回。
```

Gateway admission：

```text
1. 认证 caller_actor_id
2. 根据 taskId 查 TaskCtx
3. 检查 caller_actor_id 是否在 TaskCtx.participants
4. 不在则 reject
5. 通过 EdgeGrant 检查后，把 target_bot_id 加入 participants
```

A2A 中的承载位置：

```text
Message.metadata.bcs.participants_hint
```

原因：participants_hint 描述的是“本次 message 的 caller 对 task 参与者的理解”，属于单次 dispatch 的运行时上下文，不是 A2A Task 的权威状态。

Gateway 处理规则：

```text
缺失：允许，Gateway 可补齐或不转发该字段
一致：允许，Gateway 盖章后转发
不一致：MVP 建议 reject；也可覆盖重写，但必须 audit
```

不要把权威 participants 写入 `Task.metadata`。`Task.metadata` 可以放展示/trace 信息，但不能作为鉴权依据。

---

## 11. runtime 本地鉴权流程

Target bot 收到 A2A request 后，必须先走本地 auth plugin。

```text
A2A request received
  ↓
验证请求来自可信 BCS Gateway
  ↓
读取 BCS extension runtime context
  ↓
检查 AuthSnapshot version >= min_auth_snapshot_version
  ↓
用 AuthSnapshot 激活 EffectivePermission
  ↓
通过后进入业务 handler
  ↓
失败则 REJECTED
```

EffectivePermission 输入：

```text
AuthSnapshot
caller_actor_id
target_bot_id
current_originator_id
originator_epoch
task_id
task_ctx_version
```

业务 handler 不应自己决定是否调用鉴权。

鉴权必须是前置 middleware。

---

## 12. 审计如何结合 A2A

A2A 对象保持原生语义：

```text
Task
Message
Artifact
status
```

BCS 审计记录安全事实。

关联字段：

```text
trace_id    # 多跳协作链路
 dispatch_id # 单次 caller -> target 调用
```

BCS audit log 记录：

```yaml
dispatch_id: dispatch_456
trace_id: trace_123
a2a_context_id: session_abc
a2a_task_id: task_xyz
a2a_message_id: msg_001
caller_actor_id: bot_email
target_bot_id: bot_calendar
current_originator_id: human_alice
originator_epoch: 3
matched_edge_grant_ids:
  - edge_789
auth_snapshot_version: 12
effective_permission_view_id: epv_333
decision: allow
result_status: completed
artifact_ids:
  - artifact_999
```

原则：

```text
A2A 负责协议对象和协作状态。
BCS audit 负责安全事实和鉴权链路。
```

---

## 13. 端到端示例

### 13.1 前置授权

```text
1. calendar bot owner 在 BCS 创建 RoleDef: meeting_scheduler
2. BCS 生成 AgentCardSnapshot，其中 skills 包含 meeting_scheduler 及 permissions
3. BCS 下发 AgentCardSnapshot 给 calendar bot
4. email bot 读取 calendar bot 的 AgentCard
5. email bot 看到 skill: meeting_scheduler
6. email bot 发起权限申请：skill_id = meeting_scheduler
7. 申请经 BCS Gateway 到 calendar bot plugin
8. calendar bot plugin 通知 owner 审批
9. owner 同意
10. 审批结果写回 BCS
11. BCS 创建 EdgeGrant(email_bot -> calendar_bot, role_def_id=meeting_scheduler)
12. BCS 生成并下发新的 AuthSnapshot 给 calendar bot
```

### 13.2 runtime 协作

```text
1. human_alice 在 session_1 发起任务 task_1
2. BCS 创建 TaskCtx(task_1, current_originator=human_alice)
3. email_bot 是 kickoff target，加入 participants
4. email_bot 需要 calendar_bot 安排会议
5. email_bot 发 A2A message，带 contextId=session_1, taskId=task_1
6. BCS Gateway 认证 email_bot
7. Gateway 检查 email_bot ∈ TaskCtx.participants
8. Gateway 读取 current_originator=human_alice
9. Gateway 检查 EdgeGrant(email_bot -> calendar_bot)
10. Gateway 写入 BCS extension runtime context
11. Gateway 转发给 calendar_bot
12. calendar_bot 验证请求来自 Gateway
13. auth plugin 用 AuthSnapshot 激活 EffectivePermission
14. 通过后业务 handler 查询/创建日历事件
15. calendar_bot 返回 A2A artifact/status
16. BCS audit 记录 dispatch_id / trace_id / 权限事实
```

---

## 14. MVP 不做

MVP 明确不做：

```text
runtime 中动态申请权限
用 A2A message.role 表达 BCS RoleDef
把完整 AuthSnapshot 放进 A2A message
把完整 participants 放进 A2A message
让 bot 自己自由声明 AgentCard.skills
AgentSkill -> 多个 RoleDef 的复杂映射
GrantTemplate / PermissionPackage 新模型
绕过 BCS Gateway 的 bot-to-bot 直连调用
```

---

## 15. 最终设计口径

```text
AgentCardSnapshot 解决：别人如何发现我有哪些可申请 roles[]/skills[]，以及每个 role 的 permissions[]。
权限申请 A2A 流程解决：caller 如何申请 target 的某个 role。
EdgeGrant 解决：caller -> target 是否被 owner/user 授权。
AuthSnapshot 解决：target bot 本地如何拿到可执行的权限事实。
TaskCtx 解决：本次任务里 caller/originator/participants 的可信上下文。
BCS A2A Extension 解决：runtime A2A message 如何携带 BCS 盖章的上下文。
本地 auth plugin 解决：业务 handler 前如何强制激活 EffectivePermission。
BCS audit 解决：整个 A2A 协作链路如何可追溯。
```
