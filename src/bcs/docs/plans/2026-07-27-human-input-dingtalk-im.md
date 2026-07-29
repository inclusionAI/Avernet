# HumanInput 钉钉 IM 渠道实施计划

- 日期：2026-07-27
- 状态：实施中（公开 BCS 核心链路与内部钉钉 Provider 已完成）
- 范围：结构化协同 HumanInput 的钉钉通知、回复路由与跨 session 排队

## 1. 背景与目标

现有 HumanInput 已支持 Workbench，并能在已有 IM session 上进行有限的
best-effort 通知和回复。但现状依赖 conversation-session mapping 和“恰好一个
pending HumanInput”的启发式判断，无法覆盖以下场景：

- Workbench 或 API 发起的 run 尚未建立 IM conversation mapping；
- 同一钉钉机器人、用户和会话下有多个并行 session 等待 HumanInput；
- HumanInput 通知与普通钉钉机器人对话使用相同的 System/streaming 语义；
- 节点需要指定固定 Human actor，而不是由任意 Present Human 回复；
- 单聊通知需要优先使用流式卡片，失败后再降级 OTO。

本期目标：

1. HumanInput 节点声明 `fixed_group` 或 `direct_assignee`，同一节点只能选择一种；
2. 复用已有 `ChannelBinding` 和钉钉机器人，不重复建立机器人绑定对象；
3. 在 collaboration YAML 中声明唯一固定钉钉群，不在 YAML 中填写
   `channel_binding_id`；
4. 使用持久化 HumanInput request 精确关联 session、run 和 node；
5. 用户回复不携带短码；
6. 同一回复范围只激活一个请求，其余请求排队并让用户轻量感知；
7. HumanInput 与普通机器人对话共享 transport，但不共享事件语义和流式状态；
8. 用户在钉钉中能通过明确标题、正文标签和交互提示区分 HumanInput 请求、
   普通机器人对话以及结构化协同完成/失败结果。

本期的“并行”指多个 session 同时等待 HumanInput。现有 definition 对同一 DAG
内可能并行进入 Running 的多个 HumanInput 节点的限制保持不变。

## 2. 已锁定的产品决策

### 2.1 节点通知模式

- `fixed_group`：发送到当前 collaboration YAML 声明的唯一固定钉钉群；
- `direct_assignee`：发送给节点 assignee 对应的钉钉用户；
- 两种模式都必须配置一个固定 BCS Human actor；
- 一个节点只能选择一种模式，不同时向群和个人广播。

### 2.2 YAML 固定群与机器人解析

`ChannelBinding` 继续作为 BCS group 与钉钉机器人账号的唯一绑定。取消独立
`HumanInputChannelRoute` 设计，也不扩展 ChannelBinding 保存物理钉钉群。

每份 collaboration YAML 最多声明一个 HumanInput 固定群：

```yaml
runtime:
  kind: state_machine
  state_machine:
    human_input_channel:
      channel_type: dingtalk
      fixed_group:
        conversation_type: group
        conversation_id: cid_xxx

    nodes:
      review:
        kind: human_input
        display_name: 人工审核
        assignee:
          type: runtime_actor
          actor: human_1001
        notification:
          mode: fixed_group
        instruction: 请审核上游结果
        node_timeout_ms: 86400000
        transitions:
          complete:
            targets: []
```

YAML 不填写 `channel_binding_id`、robotCode 或机器人凭证。definition 绑定到具体
BCS group 时，根据 `group_id + channel_type` 查询 Active ChannelBinding：

1. 没有匹配 binding：拒绝绑定或启动；
2. 恰好一个 binding：解析并保存 binding id 与 robot account；
3. 多于一个 binding：拒绝为 ambiguous，不选择最新或任意一个。

纯 `/collaboration/definitions/validate` 没有 group 上下文，只做 YAML schema 和
静态语义校验。将 definition 配置到已有 group 时做 ChannelBinding 校验；每次
创建 HumanInputRequest 时再次确认解析出的 binding 仍为 Active，并把 binding
与 YAML conversation 快照到 request。

新建 group 时，ChannelBinding 只有在 group 存在后才能创建。因此 IM-enabled
collaboration 必须按以下顺序编排：

```text
创建 BCS group（不启动初始 run）
  -> 创建钉钉 ChannelBinding
  -> 提交/绑定 collaboration YAML
  -> 校验唯一 Active ChannelBinding
  -> 启动 State Machine run
```

如果建群接口要继续支持 inline YAML 和 auto-start，则同一个 application
orchestration 必须在 group 创建后、run 启动前完成 ChannelBinding 创建和上述
校验。

除 binding 存在性外，还必须通过已认证的群内确认或等价 Provider 校验证明该
机器人能够向 YAML 中的 conversation_id 发消息，不能仅信任 YAML 提交者填写的
物理群 ID。

openConversationId 只用于确定物理 IM 会话和校验消息来源，不直接映射到某个
state-machine session。

### 2.3 指定用户单聊 YAML

只使用 `direct_assignee` 的 YAML 不配置 fixed_group conversation。顶层
`human_input_channel.channel_type` 用于从当前 BCS group 解析唯一 Active
ChannelBinding，节点 assignee 用于解析钉钉用户：

```yaml
runtime:
  kind: state_machine
  state_machine:
    human_input_channel:
      channel_type: dingtalk

    nodes:
      review:
        kind: human_input
        display_name: 人工审核
        assignee:
          type: runtime_actor
          actor: human_1001
        notification:
          mode: direct_assignee
        instruction: 请审核上游结果
        node_timeout_ms: 86400000
        transitions:
          complete:
            targets: []
```

运行时解析顺序：

1. 按当前 `group_id + channel_type` 查找唯一 Active ChannelBinding；
2. 使用 request 中的 `assignee_actor_id`，在该 binding 的可信 IM participant
   mapping 中反查唯一钉钉 user id；
3. 使用 binding 的机器人向该 user id 创建单聊 Streaming Card；
4. 将 binding id、robot account 和 user id 快照到 HumanInputRequest。

YAML 不填写钉钉 user id。静态 definition validation 只校验 assignee 与 mode；
将 definition 绑定到具体 group 时校验 ChannelBinding，启动或创建
HumanInputRequest 时校验 assignee 的钉钉身份映射。映射缺失或不唯一时明确
报错，不从 actor id 字符串猜测 user id。

同一 YAML 可以同时存在 `fixed_group` 和 `direct_assignee` 节点：顶层
fixed_group 只被 `fixed_group` 节点使用，`direct_assignee` 节点始终根据自己的
assignee 解析目标用户；每个节点仍只能选择一种 mode。

### 2.4 无短码回复

用户不需要在回复中携带 request 短码。服务端根据当前回复范围内唯一 active
request 路由自然语言：

- 固定群回复范围：
  `binding_id + openConversationId + assignee_actor_id`
- 单聊回复范围：
  `binding_id + dingtalk_user_id + assignee_actor_id`

同一回复范围有多个请求时只激活一个，其余排队。不同 binding、不同群或不同
assignee 的回复范围可以并行。

## 3. 合同与持久化变更

### 3.1 State Machine definition

- HumanInput 允许使用现有 `StateMachineAssignee::RuntimeActor`；
- YAML raw validator 支持 `assignee.actor`；
- HumanInput validator 要求 assignee 为 RuntimeActor；
- 增加通知模式枚举 `fixed_group | direct_assignee`；
- 缺少模式、同时配置多个模式或配置 BotBinding assignee 均验证失败；
- `StateMachineDefinition` 增加可选的 `human_input_channel`；
- 任意节点选择 IM notification 时必须配置 `human_input_channel.channel_type`；
- 一份 YAML 最多声明一个 `fixed_group`；
- 存在 `fixed_group` 节点时必须配置合法 group conversation；
- 只有 `direct_assignee` 节点时，fixed_group 可以省略；
- `direct_assignee` 节点不允许配置节点级 conversation 或 IM user id；
- YAML 中禁止出现 `channel_binding_id`、robotCode 或机器人凭证；
- 保持现有 Workbench HumanInput contract 兼容。

### 3.2 ChannelBinding 解析

不修改 ChannelBinding domain 或存储 schema。复用现有按 target 查询能力，在
definition 绑定到 group 和 HumanInputRequest 创建时执行：

- `target = BindingTarget::Group { group_id }`；
- `channel_type = YAML human_input_channel.channel_type`；
- `status = Active`；
- 查询结果必须恰好为一个。

解析结果属于 group runtime 配置与 request snapshot，不写回 ChannelBinding。
普通机器人 binding 行为保持不变；系统不得从最近 conversation 或任意
ConversationSessionMap 猜测要使用的机器人。

`direct_assignee` 还需要扩展现有 IM participant repo 的反向查询能力：

```text
find_by_actor(channel_type, account_ref, actor_id)
  -> Vec<ImParticipantMap>
```

当前主查询方向 `im_user_id -> actor_id` 保持不变；Store 为
`channel_type + account_ref + actor_id` 增加查询索引。application 层要求反查
结果恰好一个，零个返回 identity missing，多个返回 identity ambiguous。

### 3.3 HumanInputRequest

新增 `bcs_human_input_requests` 持久化对象，至少保存：

```text
request_id
session_id
run_id
node_id
binding_id
notification_mode
reply_scope_key
active_slot_key
assignee_actor_id
im_conversation_id
im_conversation_type
im_user_id
deadline_ms
status
provider_message_ref
delivery_attempts
last_delivery_error
created_at
activated_at
responded_at
```

状态机：

```text
queued
  -> notifying
  -> active
  -> responded | expired | cancelled

notifying
  -> delivery_failed
```

`active_slot_key` 只在 `notifying/active` 状态有值，并具有唯一约束；queued 和
terminal request 为 NULL。这样数据库负责保证同一回复范围至多一个占用者。

HumanInputRequest 是状态机节点与 IM 交互的短生命周期关联，不是新的机器人绑定。

### 3.4 Channel outbound intent

在 `ChannelOutboundEvent` 增加与 `kind` 正交的 typed outbound purpose。
`kind` 继续表达 `ChatDelta/ChatFinal/System` 等流式帧生命周期，`purpose` 表达
用户正在看到的业务消息类型，至少区分：

- 普通 `Conversation`；
- `HumanInputRequest`；
- `HumanInputQueueSummary`；
- `HumanInputAck`；
- `StateMachineCompleted`；
- `StateMachineFailed`。

HumanInput purpose 携带 request/node correlation，State Machine terminal purpose
携带 state-machine run correlation。普通 assistant `ChatFinal` 默认仍是
`Conversation`，不能因为正文包含“完成”等字样就自动识别为
`StateMachineCompleted`。

BCS core/channel service 不再要求 Provider 解析任意 `raw_payload.type` 来判断
HumanInput 或状态机终态。Provider 只负责把 typed purpose 翻译为钉钉协议和
用户可见样式；`raw_payload.type` 只保留为兼容或诊断信息，不能作为投递分支的
权威依据。

State Machine runtime 在 run 成功或失败时发布一次且仅一次 terminal outbound
event。若现有终态输出路径已经发送结果，应将该次发布标记为对应 terminal
purpose，而不是额外再发一份重复结果。

投递结果需要返回 provider message/card reference，供 request 审计和诊断使用。

## 4. 请求创建与排队

HumanInput 节点激活后：

1. 从 definition 取得 notification mode 和固定 assignee；
2. 根据 state-machine group 和 YAML channel_type 查找唯一 Active ChannelBinding；
3. `fixed_group` 读取 YAML 的 fixed_group conversation；
4. `direct_assignee` 使用 assignee actor，通过该 binding 下的可信 participant
   mapping 反查唯一钉钉 user id；
5. 创建 HumanInputRequest，并快照 binding、目的地、assignee 和 deadline；
6. 尝试占用 `active_slot_key`：
   - 成功：进入 `notifying` 并发送通知；
   - 已被占用：保持 `queued`。

队列选择顺序：

1. deadline 较早的优先；
2. deadline 相同或为空时按 created_at FIFO；
3. 已 active 的请求不因新请求 deadline 更早而抢占；
4. 排队不延长 Runtime 已确定的 node deadline。

当前请求进入 `responded/expired/cancelled/delivery_failed` 后：

1. 原子清理 active slot；
2. 选择下一条仍有效的 queued request；
3. CAS 为 `notifying` 并占用 active slot；
4. 通知成功后转为 `active`；
5. 通知失败按投递失败策略处理并继续提升后续请求。

Runtime timeout、run cancellation 和 node terminal transition 必须同步关闭对应
request。queued request 如果在激活前已超时，直接标记 expired，不再通知。

## 5. 用户排队感知

用户需要感知待办压力，但不需要理解 request_id 或内部排队机制：

- 只有 active request 发送完整、可回复通知；
- queued request 不逐条发送可回复通知；
- active 通知展示“另有 N 项等待处理”及最早截止时间；
- active 期间新增排队请求时，发送可合并的非操作性摘要，不逐条刷屏；
- 当前请求完成、过期或取消后，下一项发送新的完整通知，并明确“现在可回复”；
- queued request 未激活即超时时，摘要显示待办数量变化和超时数量；
- Workbench 继续展示全部 pending/queued HumanInput 和 IM 投递状态。

同一回复范围在 active 期间，指定用户的下一条合法消息优先作为 HumanInput：

- 固定群要求来自 YAML 快照的 openConversationId、指定用户并 `@机器人`；
- 单聊要求来自相同 robotCode 和指定钉钉用户；
- 其他用户、其他群或未满足群聊 @ 条件的消息不消费 HumanInput；
- 没有 active request 时继续走原普通机器人对话。

由于 V1 不使用短码、reply-to 或卡片 action，用户自然语言始终关联到“发送时
当前 active 的请求”。系统不得在多个 pending request 中猜测最新或最相关的
session。若未来要求用户同时处理同一回复范围内的多个请求，应新增隐藏
request_id 的互动卡片提交，而不是放宽该规则。

## 6. 钉钉投递

### 6.1 固定群

- 使用隐式解析的唯一 binding robotCode 和 YAML fixed_group openConversationId；
- 使用现有群消息 API 发送一次性 Markdown；
- 内容包含节点标题、instruction、必要的共享/脱敏上下文、deadline 和排队摘要；
- 不进入普通对话按 run_id 聚合的 streaming-card state；
- 不发送仅授权用户可见的私密 artifact 到共享群。

### 6.2 指定用户单聊

优先使用现有 streaming-card create/update 能力向
`IM_ROBOT.<dingtalk_user_id>` 投递：

- 每个请求使用 `human-input-<request_id>` 形式的唯一流标识，Provider 生成
  `bcs-human-input-<request_id>` outTrackId；
- 一次写入完整内容并立即 finalize；
- streaming state 按 request_id 隔离，不能复用 state-machine run_id；
- 对创建和 finalize 做有界重试；
- Streaming 最终失败后，使用现有 OTO Markdown API 降级；
- Streaming 与 OTO 都失败时记录 `delivery_failed`，Workbench 保持可处理，
  并提升队列中的下一项，避免单个不可达用户请求阻塞全部 IM 待办。

由于同一回复范围只会通知一个 active request，OTO 降级消息仍可使用自然语言
回复，不需要短码。

### 6.3 普通对话隔离

- HumanInput request/summary/ack 不复用普通 ChatDelta/ChatFinal 的流状态；
- HumanInput System 事件不能追加到 `bcs-{run_id}` 卡片；
- State Machine terminal result 不能追加到
  `human-input-<request_id>` 卡片或覆盖 HumanInput 通知；
- HumanInput 投递失败不得触发新的 bot chat 或新的 state-machine run；
- Provider 不负责 HumanInput 权限、排队或状态机推进。

### 6.4 用户可见消息类型

钉钉 Provider 必须根据 typed purpose 渲染消息，而不是让用户仅凭自然语言正文
猜测消息类型：

| Purpose | 用户可见标题/首行 | 交互语义 |
| --- | --- | --- |
| `Conversation` | 保持现有机器人名称和对话样式 | 普通机器人对话 |
| `HumanInputRequest` | `【待你处理】<节点名称>` | 展示 instruction、截止时间、排队摘要，并明确提示当前可直接回复 |
| `HumanInputQueueSummary` | `【待办队列更新】` | 只展示等待数量和最近截止时间，明确提示暂不需要回复 |
| `HumanInputAck` | `【输入已接收】<节点名称>` | 确认本次输入已被对应节点接收，不继续占用回复 slot |
| `StateMachineCompleted` | `【协同已完成】<流程名称>` | 展示最终结果摘要和详情入口，只读，不提示回复 |
| `StateMachineFailed` | `【协同执行失败】<流程名称>` | 展示脱敏错误摘要和详情入口，只读，不提示回复 |

固定群 Markdown 同时在 API title 和正文第一行写入类型标签，避免客户端折叠
title 后失去区分。单聊 Streaming Card 在卡片头部展示相同类型标签，并在模板
支持时使用不同的状态色；文本标签是强制项，不能只依赖颜色。Streaming 降级
到 OTO Markdown 后必须保留相同标题、首行标签和回复提示。

HumanInputRequest 使用独立 `human-input-<request_id>` 流状态；
StateMachineCompleted/Failed 使用 state-machine run 的独立终态消息，不复用
HumanInput card。视觉标签只帮助用户理解，不参与入站路由、权限校验或 request
选择；用户自行发送相同文案也不能改变消息类型。

## 7. 入站路由

钉钉回调处理顺序：

```text
robotCode
  -> ChannelBinding
conversationId / senderStaffId
  -> reply_scope_key
senderStaffId
  -> trusted Human actor
active HumanInputRequest
  -> session_id + run_id + node_id
  -> respond_human_node
```

具体规则：

1. 强制验证钉钉回调身份；
2. robotCode 必须匹配 active binding；
3. 固定群 conversationId 必须等于 HumanInputRequest 快照的 YAML destination；
4. senderStaffId 必须映射为 request 快照中的 assignee；
5. 根据 reply_scope_key 查询唯一 active request；
6. 调用统一 HumanInput application service，不直接修改 runtime store；
7. 使用 message id 去重，并以节点完成 CAS 保证首次响应生效；
8. 成功后关闭 request、释放 slot、推进队列并发送 ack；
9. 未命中 active request 时才进入既有普通机器人对话路径。

`ConversationSessionMap` 继续服务普通 IM conversation 与 BCS session 的关系，
不能用于在多个 HumanInput session 之间猜测目标。

回复者不能因为发送消息而被自动加入 session 或获得 HumanInput 权限。Runtime
鉴权使用 request 创建时冻结的 assignee 和可信 Channel identity。

## 8. 失败与可观测性

- YAML destination 缺失、ChannelBinding 缺失或不唯一、assignee identity
  缺失、Streaming/OTO 失败分别使用可识别的错误码；
- 日志携带 request_id、binding_id、session_id、run_id、node_id、reply_scope_key
  和 provider message ref，但不记录完整 Human 文本；
- 指标覆盖 queued/active 数量、排队时长、通知成功率、Streaming→OTO 降级率、
  timeout-before-activation 和非法回复拒绝数；
- delivery failure 不回滚已 Running 的 HumanInput node，也不直接让 run 失败；
- Workbench 显示 request 的排队、投递失败和 deadline 状态，作为 IM 不可达时的
  处理入口。

## 9. 实施顺序

1. 扩展 definition、assignee 与 notification mode contract，补齐兼容测试；
2. 增加 YAML `human_input_channel`、group-context binding resolution、Human actor
   反向 IM identity 查询和建群时序；
3. 增加 HumanInputRequest repo/store、active-slot 唯一约束和队列 application service；
4. 增加正交的 typed outbound purpose，将 Human-ready 改为 HumanInput intent，
   并让 runtime 发布唯一的 StateMachine terminal intent；
5. 修改 Channel inbound，在普通对话之前查询 active request 并调用统一响应服务；
6. 在内部钉钉 Provider 实现 purpose 对应的用户可见样式、固定群 Markdown、
   单聊 Streaming→OTO 及独立流状态；
7. 补充 Workbench 队列/投递状态和“先绑定 Channel、再启动 run”的入口；
8. 加入指标、日志、迁移验证和端到端测试；
9. 实施前先将 `bcs-internal` 使用的 public BCS checkout 对齐到其 gitlink 版本。

## 9.1 当前实施进度

截至 2026-07-27 已完成：

- definition/YAML 合同、RuntimeActor assignee 与两种 notification mode；
- `group_id + channel_type` 唯一 Active ChannelBinding 的绑定/启动前校验；
- IM participant actor 反向查询；
- HumanInputRequest memory/SQLite/MySQL 存储、active slot 与跨 session 排队；
- 无短码的 fixed-group/direct-assignee 精确回复路由与权限校验；
- HumanInput request、queue summary、ack、协同成功/失败的 typed purpose；
- run 终态消息、失败 request 关闭及下一请求提升；
- 钉钉固定群一次性 Markdown、单聊 Streaming Card 立即 finalize 并在失败时
  降级 OTO；
- provider message/card reference 回写 request；
- `ocb_2` 独立 worktree 与聚焦 Provider 回归测试。

仍需在合入前完成：

- Workbench 的 request 队列/投递状态展示；
- 指标和完整错误码体系；
- 真实钉钉群 destination 的已认证可达性确认；
- public BCS 提交后更新 `ocb_2` 的 `ocb-public` gitlink，并执行跨仓端到端验收。

## 10. 测试与验收

### Definition 与 contract

- HumanInput 只接受 RuntimeActor assignee；
- 节点恰好配置一种 notification mode；
- direct-only YAML 只要求 channel_type，不要求 fixed_group conversation；
- direct_assignee 根据节点 actor 和已解析 binding 找到唯一钉钉 user id；
- direct_assignee 的身份映射缺失或不唯一时拒绝启动或投递；
- fixed_group 节点要求 YAML 顶层存在唯一合法 fixed_group destination；
- YAML 不接受 channel_binding_id、robotCode 或凭证；
- 旧 definition 和现有 ChannelBinding 可正常读取；
- Channel Plugin API 的新 typed purpose 有 conformance tests；
- HumanInputRequest、StateMachineCompleted 和 StateMachineFailed 具有不同
  purpose，普通 ChatFinal 保持 Conversation。

### 排队与持久化

- 同一回复范围的多个跨-session request 只有一个 active；
- 不同 binding、群、用户或 assignee 的 request 可并行；
- deadline 优先、FIFO 次序正确且不发生抢占；
- active 完成、超时、取消和投递失败后原子提升下一项；
- 并发实例争抢 active slot 时只有一个 winner；
- queued request 在激活前超时后不会发送通知。

### 投递

- group-context 中零个或多个 Active DingTalk binding 时拒绝启动；
- 固定群只向 YAML 配置的 openConversationId 发送 Markdown；
- 单聊 Streaming 使用 request_id 隔离的 outTrackId 并 finalize；
- Streaming 失败后降级 OTO；
- HumanInput 通知、摘要和 ack 不污染普通 run_id streaming state；
- HumanInput、协同完成和协同失败消息具有不同的钉钉标题、正文首行和回复提示；
- 固定群 Markdown、单聊 Streaming 和 OTO fallback 均保留用户可见类型标签；
- 同为 System/ChatFinal kind 的事件仍按 purpose 分别渲染，不读取正文或
  raw_payload 猜测；
- 每个 State Machine run 成功或失败后只产生一条对应 terminal result，且不会
  追加或覆盖 HumanInput card；
- 重试不会创建重复的可回复 active request。

### 入站与权限

- 正确群、正确 assignee 的消息进入对应 session/run/node；
- 错误群、错误用户、未 @ 的群消息和终态 request 均不能推进节点；
- 消息不能通过回复动作动态授予 participant 权限；
- 重复 callback 和并发回复最多完成节点一次；
- 没有 active request 时原普通机器人对话行为保持不变。

### 端到端

- 创建 group、绑定钉钉 Channel、提交 YAML 后才能启动 IM-enabled run；
- Workbench 发起 run，固定群收到通知并完成 HumanInput；
- Workbench 发起 run，指定用户收到 Streaming 单聊并完成 HumanInput；
- 多个并行 session 对同一用户排队，用户看到聚合待办，逐项回复并依次推进；
- 同一用户在不同钉钉群中的 HumanInput 可以并行；
- 第一个请求超时或取消后，下一请求被提升并通知；
- 用户可在钉钉中明确区分“待你处理”和“协同已完成/执行失败”，完成消息不会
  被误认为仍需回复；
- IM 全部投递失败时，Workbench 仍能完成节点。

## 11. 权限控制类安全约束设计与风险识别

>/*数字支付需求安全分析要求*/

### 权限控制类安全约束设计

- 涉及非公开数据 HumanInput 请求、状态机上下文和用户回复，仅限节点指定的
  Human actor 查看并回复其 assignee 指向自己的请求。
- 涉及非公开数据固定群通知，仅允许在请求保存的 binding 和 openConversationId
  对应会话中展示，并仅接受请求指定回复人的输入。
- 涉及非公开数据 YAML 固定群目的地，仅限有权配置当前 BCS group definition
  且有权使用其唯一 ChannelBinding 的用户声明和变更。
- 身份校验必须使用已认证上下文中的用户和会话身份，不得使用消息正文传入的
  用户或会话标识。

### 技术安全风险识别

- **技术安全风险描述：** 仅凭 openConversationId 选择 session，可能将回复写入
  其他并行流程并错误推进节点。**安全设计要求：** 必须通过 active
  HumanInputRequest 定位唯一 session、run 和 node。
- **技术安全风险描述：** 回复时动态将发送者加入状态机参与者，可能使非指定
  群成员获得 HumanInput 权限。**安全设计要求：** 回复权限必须使用 request
  创建时冻结的 assignee 身份。
- **技术安全风险描述：** 依赖用户可见标题、正文标签或关键词识别消息类型，
  可能被普通消息伪造并错误推进节点。**安全设计要求：** 出站渲染使用可信
  typed purpose，入站推进只查询持久化 active HumanInputRequest，任何展示文案
  均不参与鉴权或路由。

**注意** 1、在进行权限安全校验时使用到的用户身份信息（包括用户类型和用户Id）和业务中需要消费当前用户身份信息时，必须从安全的上下文中获取的（用户登录态或其他用户不可控身份组件）2、代码编写中确保所有校验逻辑代码都已完全实现，如果保留todo则该函数示例返回应该默认校验不通过

## 12. 兼容性与非目标

- 现有 Workbench HumanInput API、Judge、artifact 和 DAG progression 语义保持不变；
- 普通 Channel binding 和 ConversationSessionMap 行为保持不变；
- 现有普通 ChatDelta/ChatFinal 默认使用 `Conversation` purpose，用户界面保持
  兼容；
- 本期不依赖钉钉引用回复字段、用户可见短码或互动卡片 action；
- 本期一份 collaboration YAML 最多配置一个固定通知群；
- 本期不在 YAML 中配置 channel_binding_id、robotCode 或机器人凭证；
- 本期不放宽同一 definition 内并行 HumanInput 节点限制；
- 若后续要求同一回复范围并发处理多项，新增隐藏 request_id 的互动卡片提交，
  不使用“最近 session”启发式路由。
