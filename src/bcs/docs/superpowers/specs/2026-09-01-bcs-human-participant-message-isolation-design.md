# Human participant 主消息视图投影设计

**状态：Approved**

## 1. 背景

BCS 群聊既用于工作协作，也用于桌游等“裁判 + 玩家”场景。工作协作中的
Human 需要看到完整群消息；桌游玩家则不应在群聊主消息流中直接看到其他
worker 或状态机节点的内部回复。

本设计引入 `message_view_scope=full|participant`，把差异限定为 Human tab
中的**主消息视图投影**。它不是安全隔离或状态机资源权限模型。

## 2. 核心原则

1. `full` 是默认值，行为必须与本次改造前完全一致，包括 WebSocket、历史消息、
   状态机消息和副屏展示；不得因为引入 scope 新增、删除、改序或改写消息。
2. `participant` 只影响群聊主消息流：WebSocket 实时消息与 group/session message
   历史接口必须使用同一套投影规则。
3. 状态机 graph、node output、pending HumanInput、HumanInput response 等接口不读取
   `message_view_scope`，继续沿用各自已有的成员、受理人和运行权限规则。
4. 本能力是产品视图降噪，不承诺机密性。Human 可通过有权限的状态机副屏查看流程
   信息；真正的秘密信息不能仅依赖本 scope 保护。
5. bot owner 以 bot 视角查看不在本次范围内。

## 3. Scope 数据模型

```text
message_view_scope = full | participant
```

- 缺省和旧数据均按 `full` 处理，保证向后兼容；API 中的非法值应在边界拒绝。
- scope 绑定 Human participant，Bot participant 不使用该字段。
- 群 participant 保存默认 scope；session participant 保存该 session 的有效 scope。
- 创建 session 时，Human 的 session scope 默认继承群 scope，也允许创建者在下拉框中
  显式选择。
- Human 加入群或 session 时可以选择自己的 scope。该选择只决定自己的消息视图，
  不提升群管理、运行控制或状态机操作权限，因此允许自助选择。
- Group/Session 管理者可以查看并修改成员 scope，用于判断哪些成员适合作为游戏玩家。

## 4. 主消息视图投影

### 4.1 `full`

不执行任何 participant 投影，完全走改造前的消息读取与投递路径。

### 4.2 `participant`

主消息流保留：

- Human 自己发送的普通群消息；
- manager bot 面向群公开发送的普通消息；
- manager 发出的流程说明、投票结果和回合结果等公开消息；
- 用于打开状态机副屏的 panel 控制消息；
- 明确指向该 Human 的 HumanInput 提示或结果（如果该消息原本进入主消息流）；
- 与协作语义无关、原本可见的普通系统消息。

主消息流过滤：

- ManagerWorker 主从协作中其他 worker 的回复；
- StateMachine 中其他节点的回复、节点产物和内部执行事件；
- 明确指向其他 actor 的消息。

过滤依据使用写入或投递时附带的结构化可见性元数据，不在读取端解析自然语言正文。

推荐的消息元数据：

```text
visibility_domain = public | manager_worker | state_machine
audience_kind = public | full_only | directed
audience_actor_ids = [...]
```

投影规则：

| viewer scope | public | full_only | directed(viewer) | directed(other) |
| --- | --- | --- | --- | --- |
| `full` | 显示 | 显示 | 显示 | 显示 |
| `participant` | 显示 | 隐藏 | 显示 | 隐藏 |

`visibility_domain` 用于来源审计和测试；最终显示由 audience 决定。普通 manager 公告必须
标记为 `public`，不能因为来源是状态机或 manager 就一律隐藏。

## 5. 实时与历史一致性

### WebSocket

建立 Human tab 连接时解析当前 session participant 的有效 scope，并把 actor id、scope
绑定到连接。每次 fan-out 前按连接执行投影，不能只在前端隐藏。

只有客户端显式传入 `view_actor_id` 时才绑定 participant view。旧客户端省略该字段时，
必须沿用改造前的 full 连接授权、participant 列表和未投影投递逻辑；不得从登录 Human
推断 view actor，也不得要求该 Human 本身已作为 participant 加入群或 session。

scope 更新后，应使旧连接失效或要求重连，避免旧连接继续使用缓存 scope。

### Group/Session message history

历史接口解析同一个 actor id 与有效 scope，并在分页语义确定后返回该 viewer 的投影。
实时与历史必须复用同一判定函数及消息元数据。

对于运行时快照补齐的 HumanInput 展示，只能用于 `participant` 投影，不能改变 `full`
原有消息集合。补齐消息需要稳定 identity，防止与持久化消息重复。

## 6. 状态机接口与副屏

这里的“不限制”是有意设计，不属于 participant 消息投影泄漏：状态机接口是副屏和流程操作
的数据面，而不是群聊主消息流。Human 需要通过这些接口及时读取已经完成的前置节点、当前
HumanInput、投票状态和 manager 公布后的结果；如果接口也按主消息流裁剪，Human 只能等到
轮到自己的节点或下一轮上下文注入后才获得信息，无法满足桌游的实时参与体验。

因此隔离边界按接口职责划分：

- WebSocket 群聊事件与 group/session message history 属于主消息流，必须执行 participant 投影；
- 状态机 run、graph、node、artifact、HumanInput 等接口不执行 participant 投影，只沿用原有
  session membership、assignee 和 manager 控制权限；
- 这意味着有原有状态机读取权限的 Human 可以在副屏中看到节点信息。`message_view_scope`
  是展示降噪设置，不应被描述或依赖为保密权限。

以下接口不因 `message_view_scope=participant` 返回 403/409，也不裁剪为 HumanInput-only：

- run graph / run detail；
- node output / artifact；
- pending HumanInput；
- HumanInput response；
- 原有 rerun、cancel 等控制接口。

它们继续执行改造前已有的权限判断，例如 session membership、HumanInput assignee、
manager 控制权限。`participant` 不是扩大权限：原来无权调用的接口仍然无权调用。

新版副屏可以使用这些现有接口按游戏需要展示：

- 当前回合中已完成的前置发言；
- 当前 HumanInput；
- 投票提交与结果；
- manager 公布后的回合信息。

本次主消息投影改造不新增“受限副屏模式”，也不把状态机接口变成严格信息隔离边界。
用于打开副屏的 panel 消息属于公开控制消息；participant 必须能实时收到并在刷新后恢复，
副屏内部再通过上述状态机接口读取数据。

## 7. API 变更

需要在以下结构中读写并回显 `message_view_scope`：

- 群成员加入、邀请和成员列表；
- session 创建、加入、成员更新和成员列表；
- Human tab WebSocket 建连上下文；
- group/session message history viewer 上下文。

API 默认值为 `full`。不应引入 `participant_view_unsupported` 之类由 session 模式、
状态机运行状态或内部版本触发的错误；participant scope 可以在任意 session 中保存，
是否产生过滤由实际消息元数据决定。

## 8. 数据库变更

需要持久化：

1. 群 participant 的默认 `message_view_scope`；
2. session participant JSON 中的有效 `message_view_scope`；
3. 消息的 `visibility_domain`、`audience_kind`、`audience_actor_ids_json`。

如保留 `message_visibility_version`，它只能作为分类完整度、迁移或可观测性标记，不能
成为状态机执行、状态机查询、Human 加入或 scope 更新的权限门禁。

迁移要求：

- 新字段具有兼容默认值；
- 旧 participant 默认 `full`；
- 旧消息默认按原行为向 `full` 展示；
- 不要求回填旧状态机消息以启用严格隔离，因为本能力不是安全边界。

## 9. 前端交互

- Human 加入群/session 时保留 scope 选择。
- 新建 session 不增加阻断式弹窗；使用创建按钮旁的三角下拉菜单选择 Human scope。
- 默认项为“完整视角（full）”。
- 成员设置中显示 `full`/`participant`，管理员可修改。
- `full` 不启用任何新副屏参数或 HumanInput-only 渲染分支。

## 10. 验收标准

### 兼容性

- 未传 scope 的旧调用得到 `full`。
- 未传 `view_actor_id` 的旧 Workbench 连接保持原授权与未投影消息行为。
- `full` 的 WS 帧、历史消息集合、排序和副屏行为与基线一致。
- 旧群、旧 session 可以继续创建运行、加入和查询状态机。

### participant 主消息流

- 实时隐藏 ManagerWorker 其他 worker 回复与 StateMachine 其他节点回复。
- 刷新后历史结果与实时一致。
- 自己的普通聊天消息刷新后仍可见。
- manager 的公开公告与投票结果可见。

### 状态机接口

- participant Human 在满足原有权限时可读取 graph/node output、查询 pending HumanInput
  并提交 response。
- scope 不阻止一次性状态机创建或运行。
- 原有 assignee、成员和 manager 权限测试继续通过。

### 管理与连接

- 群主/管理员能查看成员 scope，并据此选择游戏玩家。
- scope 更新后旧 WS 连接不能继续沿用旧投影。
- 创建 session 的下拉选择不改变默认的一键创建习惯。

## 11. 非目标

- 不提供针对恶意 Human 的机密性保证。
- 不隔离 bot owner 的 bot 视角。
- 不重新设计状态机资源权限。
- 不在本次改造中定义新版游戏副屏的完整产品交互。
