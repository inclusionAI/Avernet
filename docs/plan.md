# BCN 分布式 Worker 高可用方案 v2.7：TBase 存运行中的状态，OB 存结果
> v2.7（2026-11-05）：新增第 12 章「流量模型与容量评估」（单 Agent 和群，入口请求 10 / 100 / 10000 / 100000 四档，含集群规模带来的周期性负载）；新增验证项 V8、V9 和 §10 的待决策项。设计正文未改，评估发现的调整建议见 12.8，待决策。修订记录见文末「附录」。
>
> 本版沿用《BCN 分布式 Worker 高可用方案 — 设计目标》，合并了此后的结论：A2A 1.0、inbox 统一使用 TBase Stream、运行中的状态放 TBase、OB 只存结果、下游 Agent 按能力分级。
表中的数字是建议值，规模确认后需要重新核算。
>

## 0. 范围与前提
| 项 | 结论 |
| --- | --- |
| 协议 | 对外和对下游都用 A2A 1.0（`SendMessage` / `SendStreamingMessage` / `GetTask` / `ListTasks` / `CancelTask` / `SubscribeToTask`）。一期只支持 1.0（见 11.1），下游只有 A2A 出站；BCN 协议 bot（WS 长连接入站）路径整体后置 |
| 场景 | S1 单 Agent 调用（BCN 负责转发和 Task 状态机）；S2 群 Session（BCN 负责编排） |
| 不在范围内 | 管理面（Bot 注册、群创建）、Group as a Bot、BCN 老协议（SSE/callback）、跨地域多活、BCN 协议 bot 的 WS 入站路径（一期后置） |
| 关键目标（待验收） | Worker / Gateway / Connector 故障时，感知加恢复 3~10 秒（P50 ≤ 5 秒，P99 ≤ 10 秒）；计划内切换 ≤ 1 秒；已经回复确认的消息 RPO = 0；TBase 单个节点故障时最多 30 秒降级（可以接受）。以上均为目标：V1 / V2 验证以及压测和故障演练通过前，不宣称 RPO = 0 和恢复时延已达标。边缘层（Gateway、Connector）的目标是服务端保证，对端（调用方、下游）何时重连不计入，见 6.1 |
| 中间件 | OB：结果；TBase（Stream + JSON Store）：运行中的状态和全部消息通道；SOFAMQ：推送通知和死信队列。不引入 LiteTopic |


## 1. 相对 v1 的变化
| 项 | v1 | v2 |
| --- | --- | --- |
| 心跳、分片所有权、epoch | OB | TBase（带 TTL 的 key + CAS） |
| 检查点、inbox offset、进行中的 dispatch | OB 事务 | TBase 会话 JSON 文档，单 key 原子更新 |
| 栅栏 | OB `owner_epoch` | 会话文档里的 `owner_epoch`，和状态写入是同一次原子操作 |
| OB 的角色 | 每条消息都要提交 | 只存结果：Task 创建和终态、群每轮记录、审计（群 Session 休眠存档后置，见 5.6） |
| Connector 故障的发现 | OB 心跳 + 接管 dispatch | 会话所有者 Worker 负责监督自己的 dispatch |
| 自我隔离 | 需要 | 不再需要：栅栏和数据在同一份文档上，旧 Worker 的提交必然失败 |
| 懒激活 | 需要 | 改为接管时立即激活：状态在 TBase，批量读取很快 |
| Connector | 单独模块 | 逻辑上单独，部署上可以和 Gateway 合并为一个边缘进程 |


## 2. 总体架构
```plain
                      调用方 (A2A 1.0)
                            │
                       VIP（健康检查、摘流）
                            │
        ┌───────────────────▼────────────────────────────┐
        │  边缘进程 ×N（早期合并部署，后期可以拆开）            │
        │  ┌──────────────┐          ┌───────────────────┐ │
        │  │ A2A Gateway  │          │  Agent Connector  │ │
        │  │ 入口方向       │          │  出口方向          │ │──A2A──► A2A Agent
        │  └──┬───────▲───┘          └──▲─────┬───────┬──┘ │
        └─────┼───────┼─────────────────┼─────┼───────┼────┘
     inbox+wake│  读 evt│           读 cmd │写 evt │写 inbox+wake
   ┌───────────▼───────┴─────────────────┴─────▼───────▼──────────────┐
   │ TBase：运行中的状态                                                   │
   │  node:* / shard:*（心跳、所有权）   sess:{sN}:<sid>（会话 JSON 文档）    │
   │  disp:{sN}:<did>（dispatch 运行时信息）                                │
   │  inbox / wake / evt / cmd（Stream）  active:{sN}（分片内活跃会话集合）    │
   └───────────▲──────────────────────────────────────────────────────┘
               │ 读写文档、读 inbox、写 evt 和 cmd
   ┌───────────┴───────────────┐        ┌──────────────────────────────┐
   │   Session Worker ×N       │──────► │ OB：结果                       │
   │ 分片所有者 · 会话 Actor    │ 关键时刻 │ Task / 群消息记录 / 审计 / 存档  │
   │ 编排 · 监督 dispatch       │ 同步写入 └──────────────────────────────┘
   └───────────┬───────────────┘
               └──► SOFAMQ ──► Push Service ──webhook──► 调用方
```

| 组件 | 职责 | 状态 | 扩容方式 |
| --- | --- | --- | --- |
| A2A Gateway | 终结调用方连接；生成 taskId 和 contextId；写入 inbox；从事件流推送 SSE；处理 `SubscribeToTask` 和 `Last-Event-ID` 续传；`GetTask` / `ListTasks`；接收下游 Agent 的推送通知 | 无状态 | 直接加机器 |
| Session Worker | 按分片持有会话；以会话为单位串行处理 inbox；运行转发状态机和群编排器；提交会话文档；监督自己的 dispatch | 只有可重建的内存状态 | 按分片再平衡 |
| Agent Connector | 作为 A2A 客户端调用下游（一期没有入站连接，不挂在 VIP 后面）；把 token 增量写入事件流，把完成结果写入 inbox | 到下游的连接；dispatch 运行时信息在 TBase | 直接加机器 |
| Push Service | 投递 A2A 推送通知：签名、出口代理防 SSRF、退避重试、死信队列 | 无状态 | 直接加机器 |


三个核心原则：

1. 连接和计算分离：Worker 不持有任何长连接。Worker 崩溃时 token 流不中断，只有状态推进会延迟。
2. 每个 key 只有一个写入者（Stream 除外），CAS 只用来防"旧所有者"：会话文档只由所有者 Worker 写；dispatch 运行时信息只由持有它的 Connector 写；心跳只由节点自己写。
3. 对外始终返回 Task：保证调用方总能用标准的 `GetTask` / `SubscribeToTask` 恢复。

### 为什么 Connector 是独立模块，而不是 Worker 直接调用 Agent
|  | 由 Gateway 调用 | 由 Worker 直接调用 | 独立的 Connector 模块 |
| --- | --- | --- | --- |
| 群 Session（S2） | 群编排产生的下发不对应任何一个调用方请求，Gateway 没有合适的生命周期来承载 | 可以 | 可以 |
| 生命周期 | Gateway 的连接跟随调用方，调用方断线时连接就结束了，但下游调用应该继续执行 | 跟随分片所有权 | 每个 dispatch 有独立的所有权 |
| BCN 协议 bot 的 WS 长连接（一期后置，但这是 Connector 独立的长期理由） | 可以持有 | 一个 bot 会参与很多分片上的会话，分片分布在不同 Worker 上，要么 bot 连接所有 Worker，要么 Worker 之间互相转发 | bot 只连一个 Connector，按注册表路由 |
| Worker 崩溃的影响 | 不适用 | 持有的下游 token 流全部中断，并引发重连风暴 | token 流不受影响，只有状态推进延迟 |
| 扩容依据 | 调用方连接数 | 计算量和连接数混在一起 | 按下游连接数扩容，和 Worker 分开 |
| 网络跳数 | 少 | 最少 | 多一跳（经过 TBase Stream，约 1 毫秒量级） |


由 Worker 直接调用唯一的优势是少一跳，但会破坏"Worker 崩溃时 token 流不中断"这个特性，也解决不了 bot 连接归属问题（BCN 协议 bot 路径后置，但这是长期理由）。所以逻辑上单独模块，部署上早期可以和 Gateway 合并为一个边缘进程；两者只通过 Stream 交互，拆分时不需要改代码。

### 核心机制总览
方案靠下面 9 个机制达到目标。一个容易被问到的点先说清：方案里有心跳，没有所有权租约（见本节末尾）。

| # | 机制 | 解决什么 | 原理 | 详细章节 |
| --- | --- | --- | --- | --- |
| 1 | 心跳 | 发现 Worker / Connector 故障 | 每秒续约一次 `node` 和 `node2` 两个 key，TTL 3 秒，两个都消失才判定死亡；节点自己连续续约满一个 TTL 后，才有资格判定别人死亡 | 4.1、5.1 |
| 2 | 分片加确定性继任者 | 接管不经过中心调度，支持水平扩容 | 会话按 hash 分到 1024 个分片；每个分片的继任者顺序由 rendezvous hash 预先算出，排第一的存活节点直接 CAS 认领 | 5.1 |
| 3 | epoch 栅栏 | 同一个会话不会有两个 Worker 同时推进 | 会话文档里的 `owner_epoch`，每次提交都带条件；新所有者接管时先写入更大的 epoch，旧所有者的提交随即失败，它自己放弃 | 5.1、5.2 |
| 4 | 会话文档原子提交 | 接管后能从上次状态继续 | 状态、输入位点、进行中的下发等，一次原子更新写进 TBase 的 JSON 文档，条件是 epoch 匹配 | 4.1、5.2 |
| 5 | inbox 加提交点加位点 | 已确认的消息不丢，且可重放 | inbox 和 wake 都写成功才回复调用方；`inbox_offset` 随状态一起提交；`wake_offset` 按低水位推进，处理途中崩溃就从旧位点重读 | 5.2、5.3 |
| 6 | 确定性 ID、事件去重、幂等键 | 重试和重放不会重复生效 | sessionId、taskId 由请求内容推导，客户端重试落到同一个 Task；各类事件按各自的身份去重；下发带幂等键 | 3.2、5.2 |
| 7 | 提交后补发 | 提交之后、副作用之前崩溃不丢 | `cmd_published`、`pending_publish` 记在同一次提交里；监督循环对"指令没人领"和"下游无进展"补投 | 5.2、5.4 |
| 8 | 连接与计算分离，加事件流 | Worker 挂了 token 流不断，调用方可续传 | Gateway、Connector 持有连接，Worker 只做计算；token 直接走 Stream；续传用快照加 `Last-Event-ID` | 2、5.3、6.1 |
| 9 | 下游调用的预写、分级和兜底 | 下游不可控时仍可恢复 | 调用前先 `SET NX` 写 `disp`；按下游能力分 A/B/C/M 级恢复；无法确定时按 `onAmbiguous`：重试，或标记 UNKNOWN | 5.4、5.5 |


目标和机制的对应：

| 目标 | 主要靠 |
| --- | --- |
| 3~10 秒故障切换 | 1 心跳（约 3 到 3.5 秒发现）+ 2 继任者认领，Worker 崩溃合计约 5 到 6 秒 |
| 已确认的消息不丢 | 5 提交点和位点 + 4 原子提交 |
| 不重复、不并发 | 3 epoch 栅栏 + 6 去重和幂等键 |
| 水平扩容 | 2 分片加再平衡 + 8 连接与计算分离 |
| 下游不可控 | 9 预写、分级和兜底 + 7 补发 |
| 边缘层故障 | 8 事件流续传 + `conn_epoch` 过滤 + VIP 摘除（见 6.1） |


心跳与租约：心跳 key 的 TTL 本质上是节点的存活租约，只用来发现故障，由 TBase 服务端判定过期，不比较跨机器的时间。所有权没有"到期即失效"的租约：分片所有权记在 `shard:<i>`（owner 加 epoch），会话所有权由会话文档里的 `owner_epoch` 保证。正确性只依赖 epoch 栅栏：旧所有者即使还没发现自己已经失效，它的提交也会因为 epoch 不对而失败，所以不需要依赖时钟，也不需要自我隔离。时钟和 TTL 只影响多久能发现故障，不影响会不会出现两个写入者。

这些机制依赖、但还没验证的 TBase 能力：

| 机制 | 依赖 | 验证项 | 不满足时 |
| --- | --- | --- | --- |
| 1、2 | TTL key、CAS | 基础能力 | — |
| 3、4 | JSON 条件原子更新（条件成立时原子修改多个路径） | V2 | 会话文档退回 OB |
| 5 | Stream 写入成功后单节点故障不丢 | V1 | 会话文档退回 OB，Gateway 同时写一份到 OB 对账 |
| 8 | 同 slot 多 key 的 `XREAD BLOCK` | V3 | Gateway 退化为批量轮询 |


机制 3、4、5 是最关键的前提：V1、V2 验证前，RPO = 0 和 3~10 秒只能算目标。

## 3. 会话模型与 ID
| 概念 | 定义 |
| --- | --- |
| 会话（处理和所有权的单位） | S1：A2A `contextId`；S2：群 Session ID |
| 分片 | `shard = hash(sessionId) % 1024`。TBase key 统一带 hash tag `{sN}`，同一分片的 key 落在同一个 slot |


### 3.1 三层身份
| 身份 | 回答的问题 | 来源 | 用途 |
| --- | --- | --- | --- |
| `message_id` / `event_id` | 这一条输入处理过没有 | 调用方的 messageId；Connector 回报的结果或完成用 dispatch_id（终态只发生一次）；中断类事件用交互 ID（见下）；下游推送先映射到 dispatch | 只用于判断单条输入是否重复 |
| `task_id` | 这是哪个任务（可以接收多条输入） | 创建任务时确定性推导 | 路由、查询、判断是否重复创建 |
| `session_id` / `context_id` | 这是哪个会话（承载多个任务） | 调用方提供，或首次受理时确定性推导 | 分片、所有权 |


交互 ID：同一个 dispatch 可以先后多次进入 input-required（先问"请选择商品"，回答后继续执行，再问"请确认数量"）。两次事件的 dispatch_id 和类型相同，但是两次不同的交互，不能按 dispatch_id + 事件类型去重。所以：

+ 完成结果：继续按 dispatch 终态去重。
+ 人工等待等可以重复发生的关键事件（input-required、auth-required）：用交互 ID 区分本次事件，同一事件重投沿用原编号。交互 ID 取上游稳定的标识：A2A 下游用 `status.message.messageId`（`SubscribeToTask` 快照里是同一个），下游没填时用 `下游 taskId + status.timestamp`。BCN 协议 bot 的交互 ID 随该路径后置。
+ 普通 token 增量不做精确去重，也不新增通用事件归档系统。

### 3.2 身份的确定性推导
Gateway 不随机生成 ID，也不需要额外的请求映射表：

判断顺序（从上到下，命中即停）：

1. 带 taskId（对已有任务的后续输入，例如对 input-required 的人工回答）：从 taskId 解出 sessionId，路由到原会话，不用 messageId 推导新会话。同时带了 contextId 时，必须与原会话一致，不一致则拒绝（A2A 1.0 §3.4.3 要求：只带 taskId 时服务端推导 contextId，contextId 与 taskId 不一致时必须拒绝）。解不出 sessionId，或会话里没有该 Task，返回 `TaskNotFoundError`。
2. 不带 taskId、带 contextId（已有会话里的新任务）：`sessionId = contextId`，`taskId = 编码(sessionId, hash(messageId))`。
3. 都不带（新会话里的新任务）：`sessionId = 编码(hash(租户, 调用方, 目标 Agent 或群, messageId))`，`taskId = 编码(sessionId, hash(messageId))`。

taskId 是调用方传来的，可能被伪造成指向别人的会话。Gateway 解码只用于路由，校验在 Worker：该 Task 必须存在于这个会话，并且属于当前调用方的租户和权限，否则按 `TaskNotFoundError` 处理，不泄露该 Task 是否存在。

接口约定：同一个 messageId 必须对应相同的内容。P0 不做拦截：Task 上记录创建消息的摘要，之后的重复创建请求摘要不同时只告警，按首次请求处理。

确定性推导的好处：

+ 调用方重试幂等，包括首次请求没带 contextId 的情况：任何一台 Gateway 处理同一个重试请求，都会落到同一个 Session、同一个 Task。即使原来那台 Gateway 已经崩溃，新 Gateway 也能返回同一个 Task 并接上它的事件流。
+ 不用查表就能路由：从 taskId 直接解出 sessionId 和分片。
+ 能立即返回 Task：A2A 要求 `SendStreamingMessage` 流的第一个事件必须是 Task。Gateway 写入 inbox 成功后马上返回 `Task{id, state=SUBMITTED}`，不需要等 Worker 处理，Worker 正在故障切换时也一样。

## 4. 数据划分
### 4.1 TBase：运行中的状态
同一会话的 key 都带 hash tag `{sN}`（N = 分片号），落在同一个 slot。

| Key | 类型 | 写入方 | 内容 |
| --- | --- | --- | --- |
| `node:<id>` 和 `node2:<id>` | KV + TTL 3 秒 | 节点自己 | 心跳。写两个 key、分布在不同 slot，两个都消失才判定节点死亡，避免 TBase 单个节点故障造成误判 |
| `shard:<i>` | KV（CAS） | 认领者、所有者 Worker | `{owner, epoch, wake_offset}`。`wake_offset` 是本分片 wake 的消费位置（低水位，见 5.2），更新时带 epoch 条件 |
| `active:{sN}` | Set | 所有者 Worker | 本分片需要恢复的会话 ID（可恢复清单）：有进行中的 Task、dispatch 或待发布事件的会话才在清单里，登记和移出规则见 5.2 |
| `sess:{sN}:<sid>` | JSON | 所有者 Worker | 会话文档，结构见下 |
| `disp:{sN}:<did>` | JSON | 持有该 dispatch 的 Connector | `{owner_connector, conn_epoch, downstream_task_id, status, progress_seq}`。`status` 取 SENT / ACCEPTED / ENDED，见 4.3.5；`progress_seq` 由 Connector 递增，供看门狗使用，见 6.1 |
| `inbox:{sN}:<sid>` | Stream | Gateway、Connector、推送接收端 | 输入消息 |
| `wake:{sN}` | Stream | 同上 | 有新消息的会话 ID。按 `XTRIM MINID wake_offset` 截断，不用 MAXLEN，避免截掉未处理的唤醒 |
| `evt:{sN}:<sid>` | Stream | Worker（硬事件）、Connector（token 增量和 reset） | 推给调用方的事件。Connector 写的增量和 reset 都带 `(dispatch_id, conn_epoch)`，Gateway 据此过滤迟到的旧增量，见 5.3 |
| `cmd:a2a:<k>` | Stream | Worker | 下发指令，类型有 dispatch、resume、probe、cancel，Connector 池通过消费组领取 |


大的消息、文件、artifact 先写入 OB 或对象存储，stream 里只放引用。

会话文档结构：

```json
{
  "schema": 2,
  "owner_epoch": 42,
  "kind": "relay | group",
  "status": "ACTIVE | QUARANTINED",
  "inbox_offset": "1730700000000-3",
  "event_seq": 87,
  "takeover_streak": 0,
  "tasks": {
    "<taskId>": { "state": "WORKING", "caller": "...", "push_cfg": "...", "step": 5,
                  "create_digest": "...", "seen_msgs": ["<messageId>", "..."] }
  },
  "dispatches": {
    "<did>": { "target": "...", "protocol": "a2a|bcn", "idem_key": "...", "level": "A",
               "status": "PENDING|INFLIGHT|DONE|FAILED|UNKNOWN", "deadline": 1730700030000,
               "cmd_published": true, "cancel_requested": false, "cancel_published": false,
               "seen_interactions": ["<interactionId>"] }
  },
  "recent_done": { "<taskId>": 1730700000000 },
  "pending_publish": [ { "task_id": "...", "event_seq": 88, "event": { } } ],
  "deadlines": {},
  "state": {}
}
```

+ `tasks` 只保留进行中的 Task；`dispatches` 是下发意图（outbox），运行时信息在 `disp:*` 中；`seen_interactions` 是该 dispatch 已处理过的中断类事件的交互 ID，dispatch 结束时清除。
+ `create_digest` 是创建消息的摘要；`seen_msgs` 是该 Task 已处理过的调用方 messageId，任务结束时随 Task 一起清除。
+ `recent_done` 保留刚结束的 Task 标记（1 小时），用于快速判断"重复创建"和"终态任务不能再接收消息"。
+ `pending_publish` 是已提交、但还没确认写入 evt 的关键事件，见 5.2。
+ `status` 取 `ACTIVE` 或 `QUARANTINED`（毒会话被隔离），见 4.3.5。
+ `state` 是转发状态机或群编排器的状态：轮次、成员、等待中的回复等。
+ 每次提交都是对这份文档的一次原子更新，条件是 `owner_epoch == 我的 epoch`，同时修改多个路径：offset、tasks、dispatches、state、event_seq。

### 4.2 OB：结果
| 表 | 写入时机 | 同步还是异步 | 用途 |
| --- | --- | --- | --- |
| `bcn_task` | 创建时插入；进入终态时更新 | 创建异步；终态同步 | `GetTask`（已结束的任务）、`ListTasks` |
| `bcn_group_message` | 群 Session 每一轮结束时 | 同步 | 群消息记录，长期保存 |
| `bcn_event` | 硬事件产生后 | 异步批量 | 审计和问题回放 |
| `bcn_session_archive` | 一期不启用（后置） | — | 休眠存档：从 TBase 移出，下次有消息时再恢复。一期空闲会话只释放内存 Actor，见 5.6 |
| `bcn_agent_capability` | 管理面维护 | — | 下游 Agent 的能力等级，只读 |


必须写 OB 的数据，要满足"长期保存、需要查询、不能丢"中的至少一条：

+ 长期保存：Task 终态、artifact、历史消息；群消息记录。调用方可能几天后才用 `GetTask` 查结果，群 Session 可能持续几个月。
+ 需要多条件查询：`ListTasks` 要按 `context_id`、状态、更新时间过滤，倒序，游标分页，按权限过滤，需要二级索引。
+ 审计和回放：硬事件日志。
+ 不能接受丢失：运行中的状态丢了可以按失败处理，已经完成的结果丢了无法交代。

必须同步写 OB 的时刻：Task 进入终态（写成功后才把终态事件发给调用方，保证调用方一旦看到"已完成"，之后用 `GetTask` 一定查得到）；群 Session 每一轮结束（每轮只写一次，频率低）。其余写入都是异步的。检查点和 inbox offset 不写 OB。

### 4.3 状态、事件与读写时机（清单）
4.1、4.2 定义了 key 和表，本节汇总方案里所有的状态和事件：作用、读写时机、存放位置。原则：运行中的状态和全部消息在 TBase，OB 只存结果，而且只在三个时刻写——Task 创建、Task 终态、群每轮结束。

#### 4.3.1 状态（TBase）
| 状态 | key | 作用 | 谁写、何时写 | 谁读、何时读 |
| --- | --- | --- | --- | --- |
| 节点心跳 | `node:<id>` 和 `node2:<id>`，TTL 3 秒，两个 key 落在不同 slot | 判断 Worker / Connector 是否存活 | 节点自己，每秒续约一次 | Worker 每 500 毫秒批量检查分片所有者；监督循环每秒检查 Connector |
| 分片所有权 | `shard:<i>` = `{owner, epoch, wake_offset}` | 谁处理这个分片；wake 消费到哪里 | 认领者 CAS 写入（epoch +1）；所有者按低水位推进 `wake_offset`（带 epoch 条件） | 所有 Worker 发现孤儿分片；新所有者接管时读 `wake_offset` |
| 活跃会话清单 | `active:{sN}`（Set） | 接管时知道哪些会话需要恢复 | 所有者 Worker，每批处理结束时登记或移出（5.2） | 接管时 `SMEMBERS`；每 10 秒兜底扫描 |
| 会话文档 | `sess:{sN}:<sid>`（JSON） | 一个会话的全部运行态，字段见 4.3.2 | 只有所有者 Worker，每批消息处理后原子提交一次，条件是 `owner_epoch` | 所有者激活会话时；Gateway 的 `GetTask`（进行中）和 `SubscribeToTask` 快照 |
| dispatch 运行时 | `disp:{sN}:<did>`（JSON）= `{owner_connector, conn_epoch, downstream_task_id, status, progress_seq}` | 一次下游调用由谁执行、执行到哪、有没有进展 | 只有持有它的 Connector：领取时 `SET NX`；接管时 CAS 加 epoch；拿到下游 taskId 时写；执行中至少每 5 秒递增 `progress_seq` | Worker 监督循环每秒检查（有没有、所有者是否存活、进展是否停滞）；Gateway 建流时读一次 `conn_epoch`；新 Connector 接管时读 |
| 限流计数 | 按租户和下游 Agent 的计数器 | 准入控制 | Gateway，每次受理请求 | Gateway，受理前 |


#### 4.3.2 会话文档的字段
这些字段都由所有者 Worker 在处理循环第 5 步的同一次原子提交里写入。

| 字段 | 作用 | 清理 |
| --- | --- | --- |
| `owner_epoch` | 栅栏：每次提交都带条件，被接管后旧所有者的提交失败 | 随接管递增 |
| `status` | `ACTIVE` 或 `QUARANTINED` | 隔离后由人工解除 |
| `inbox_offset` | inbox 处理到哪里，重放的起点 | 持续推进 |
| `event_seq` | 硬事件序号，Gateway 按 `(taskId, event_seq)` 去重 | 持续递增 |
| `takeover_streak` | 连续接管后没提交成功的次数，达到 K 就隔离 | 一次提交成功就清零 |
| `tasks` | 进行中的 Task：状态、调用方、推送配置、当前步骤、创建消息摘要（`create_digest`）、已处理的 messageId（`seen_msgs`） | Task 结束时移除 |
| `dispatches` | 下发意图：目标、协议、`idem_key`、能力等级、状态、截止时间、`cmd_published`、`cancel_requested`、`cancel_published`、已处理的交互 ID（`seen_interactions`） | dispatch 结束时清除 |
| `recent_done` | 刚结束的 Task 标记，用于判断"重复创建"和"终态不能再收消息" | 保留 1 小时 |
| `pending_publish` | 已提交但还没确认写入 evt 的关键事件（终态、input-required、auth-required） | 写入 evt 后清除，接管时补发 |
| `deadlines` | 会话超时、dispatch 截止时间 | 到期或结束时 |
| `state` | 转发状态机或群编排器的状态：轮次、成员、等待中的回复 | 随会话 |


#### 4.3.3 消息和事件（TBase Stream）
| Stream | 事件类型 | 作用 | 谁写、何时写 | 谁读、何时读 | 清理 |
| --- | --- | --- | --- | --- | --- |
| `inbox:{sN}:<sid>` | 创建请求；调用方后续消息（人工回答）；取消；下游结果或完成；中断类事件（input-required、auth-required）；下游推送 | 会话的全部输入，去重规则见 5.2 | Gateway：返回 Task 之前；Connector：下游结果到达时 | 所有者 Worker，从 `inbox_offset` 往后 `XRANGE` | 提交后 `XTRIM MINID`；会话结束后 `EXPIRE` |
| `wake:{sN}` | 会话 ID | 告诉分片所有者"这个会话有新消息" | 写完 inbox 之后紧接着写 | 所有者 Worker `XREAD BLOCK`，从 `wake_offset` 开始 | 按 `wake_offset` 截断 |
| `evt:{sN}:<sid>` | 硬事件：状态变化、完整 artifact、终态 | 推给调用方，也是续传的来源 | Worker：提交会话文档之后（处理循环第 6 步） | Gateway `XREAD BLOCK`，推 SSE，按 `latest_epoch` 过滤 | `MAXLEN` 加会话结束后 `EXPIRE` |
|  | 软事件：token 增量（带 `dispatch_id`、`conn_epoch`） | 实时输出 | Connector，每 50 毫秒合并一次，直接写，不经过 Worker | 同上 | 同上 |
|  | reset：整体替换的快照（带新 epoch） | 接管后覆盖旧 Connector 写的内容 | 新 Connector 接管时，在写增量之前 | 同上 | 同上 |
| `cmd:a2a:<k>` | dispatch：新下发 | 让 Connector 调用下游 | Worker：提交之后；监督循环按 `cmd_published` 重投 | Connector 池通过消费组领取，领取成功或 `SET NX` 失败后 `XACK` | `MAXLEN` |
|  | resume：Connector 挂了后续接 | 让新 Connector 接管 dispatch | Worker 监督循环 | 同上 | 同上 |
|  | probe：看门狗探测 | 用 `GetTask` 查下游状态 | Worker 监督循环 | 同上 | 同上 |
|  | cancel：取消传播 | 向下游发 `CancelTask` | Worker 处理取消后；按 `cancel_published` 重投 | 任一 Connector | 同上 |


推送通知（P1）走 SOFAMQ：Worker 执行副作用时发消息，Push Service 投递、重试、进死信队列。它不在 TBase 也不在 OB。

#### 4.3.4 结果（OB）
| 表 | 作用 | 写入时机 | 读取时机 |
| --- | --- | --- | --- |
| `bcn_task` | Task 的长期记录，供 `GetTask`（已结束的）和 `ListTasks` | 创建时异步插入；终态同步更新，先于对外发布终态事件 | `GetTask` 查不到会话文档时；`ListTasks`；去重时查"这个 Task 是否已存在" |
| `bcn_group_message` | 群消息记录，长期保存 | 每一轮结束时同步写，先于提交会话文档（幂等键 `turn_id`） | 群编排器恢复和重放时 |
| `bcn_event` | 硬事件日志，审计和回放 | 异步批量 | 排查问题时 |
| `bcn_session_archive` | 群 Session 休眠存档 | 一期不启用 | — |
| `bcn_agent_capability` | 下游 Agent 的能力等级 | 管理面维护 | Worker / Connector 决定恢复策略时，只读 |


OB 写入规则：幂等键不存在才写，写完读回；读回的和本次计算不一致就以读回的为准（见 5.2）。

#### 4.3.5 状态枚举与转移
Task 状态（A2A 1.0）：

| 状态 | 含义 | 记录位置 | 谁推进 |
| --- | --- | --- | --- |
| SUBMITTED | Gateway 已受理 | 只存在于返回给调用方的响应里，TBase 和 OB 都还没有记录（见 10 的第 3 条） | Gateway |
| WORKING | 处理中 | 会话文档的 `tasks` | Worker |
| INPUT_REQUIRED、AUTH_REQUIRED | 等待调用方输入 | 会话文档，同时写入 `pending_publish`，再发布到 evt | Worker |
| COMPLETED、FAILED、CANCELED、REJECTED | 终态 | 先写 OB `bcn_task`，再提交会话文档（从 `tasks` 移除），最后发布到 evt | Worker |


中间状态只在 TBase（和 evt、异步的 `bcn_event`），只有创建和终态进 OB。所以 `ListTasks` 里进行中的任务是最终一致的，已结束的任务是强一致的。

`dispatches.status`（会话文档里，生命周期，只有 Worker 写）：

| 状态 | 含义 | 转移条件 |
| --- | --- | --- |
| PENDING | 下发意图已提交，cmd 还没发出（`cmd_published=false`） | 初始 |
| INFLIGHT | cmd 已发出，等待或正在执行 | 处理循环第 6 步标记 `cmd_published` 时由 PENDING 转入 |
| DONE | 成功终态已处理 | 处理 Connector 回报的成功结果 |
| FAILED | 失败终态 | 失败结果、重试耗尽或超过截止时间 |
| UNKNOWN | 下游结果不明，且不能安全重发 | 模糊窗口内崩溃，且 `onAmbiguous=fail-explicit` |


`disp.status`（TBase `disp:*` 里，执行进度，只有 Connector 写）：

| 状态 | 含义 | 谁在什么时候用 |
| --- | --- | --- |
| SENT | Connector 领取指令时由 `SET NX` 写入，同时是调用下游之前的预写记录。有 disp 记录但没有 `downstream_task_id` 就是模糊窗口 | 恢复分级（5.5） |
| ACCEPTED | 已拿到下游 taskId 并写回，从此可以用 `SubscribeToTask` / `GetTask` 找回 | 看门狗只对 ACCEPTED 之后的 dispatch 计时；优雅退出等 dispatch 进入 ACCEPTED |
| ENDED | 终态结果已写入 inbox，disp 随后设置过期时间 | Worker 据此知道 Connector 已交付 |


只回复 Message 的下游（M 级）没有 taskId，所以没有 ACCEPTED，直接从 SENT 到 ENDED。新 Connector 接管时，ACCEPTED 保持不变；接管时仍是 SENT 的，按 5.5 的能力分级处理。

两套状态分工：Worker 只写 `dispatches.status`，Connector 只写 `disp.status`，互不写对方，避免双写。

会话 `status`：

| 状态 | 含义 | 转移 |
| --- | --- | --- |
| ACTIVE | 正常 | 初始 |
| QUARANTINED | 毒会话被隔离 | 新所有者读到 `takeover_streak` 达到 K 时，先 CAS 置为 QUARANTINED，再把进行中的 Task 判为失败（写入 OB）并告警；之后发给该会话的消息直接返回错误；由人工运维操作解除 |


#### 4.3.6 一条消息的读写时序
| 步骤 | 动作 | 存储 |
| --- | --- | --- |
| ① Gateway | 限流计数；推导 ID；`XADD inbox`，再 `XADD wake`，两者都成功才返回 `Task{SUBMITTED}` | TBase |
| ② Worker | `XREAD BLOCK wake`（从 `wake_offset` 开始），交给会话的处理队列 | TBase |
| ③ Worker | `XRANGE inbox`；读会话文档；去重（先看 `tasks` 和 `recent_done`，都没有再查 OB `bcn_task`） | TBase，必要时读 OB |
| ④ Worker | 运行状态机 | 内存 |
| ⑤ Worker | 如果是 Task 终态或群一轮结束：幂等写 OB，写完读回 | OB（同步） |
| ⑥ Worker | 原子提交会话文档，条件 `owner_epoch` | TBase |
| ⑦ Worker | `XADD evt`（硬事件）；`XADD cmd`；标记 `cmd_published`；清除 `pending_publish`；`XTRIM inbox`；推送发 SOFAMQ（P1）；硬事件异步写 `bcn_event` | TBase、OB（异步）、SOFAMQ |
| ⑧ Worker | 本批处理结束：更新 `active`（登记或移出），然后 `wake_offset` 才能推进过这些 wake | TBase |
| ⑨ Connector | 领取 cmd → 确认会话文档里有这个 dispatch → `SET disp NX`（SENT）→ 调用下游 → 写 `disp`（taskId、`progress_seq`）→ 写增量到 evt；结束时写 inbox 和 wake，disp 置 ENDED | TBase |
| ⑩ Gateway | `XREAD BLOCK evt`，过滤后推 SSE；`GetTask` 先查会话文档，再查 OB | TBase、OB |


#### 4.3.7 几条贯穿的规则
+ 单写者与栅栏：会话文档只有所有者 Worker 写，提交带 `owner_epoch` 条件；`disp` 只有持有它的 Connector 写；心跳只有节点自己写。Stream 允许多方写入。
+ 提交点：消息"已确认"= inbox 和 wake 都写成功；Task 终态"对外可见"= OB 已写；步骤"已完成"= 会话文档已提交。
+ 副作用幂等：evt 按 `(taskId, event_seq)` 去重；cmd 按 `idem_key` 和 dispatch ID 去重；推送按 `(taskId, event_seq)` 去重。
+ 去重按事件类型：创建请求按 Task 是否已存在，后续消息按 `seen_msgs`，取消按业务状态，结果按 dispatch 终态，中断类事件按交互 ID（见 5.2）。
+ 清理：inbox 和 wake 按位点 `XTRIM MINID`；evt 用 `MAXLEN` 加会话结束后 `EXPIRE`；`disp` 在 ENDED 后设置过期时间；`recent_done` 保留 1 小时；`seen_msgs` 和 `seen_interactions` 随 Task / dispatch 结束清除。
+ active 的登记、移出和 wake 的低水位：见 5.2。

## 5. 核心机制
### 5.1 节点心跳与分片所有权
1. 心跳：每秒续约一次 `node:` 和 `node2:` 两个 key（TTL 3 秒）。过期由 TBase 服务端判定，没有时钟偏差问题。
2. 发现死亡节点：每个 Worker 每 500 毫秒用 `MGET` 批量检查各分片所有者的两个心跳 key。
3. 认领：按 rendezvous hash（最高随机权重哈希）算出继任者顺序，排第一的存活节点立即 CAS 认领 `shard:<i>`（epoch +1），排在后面的延迟后兜底。全程不依赖中心调度。
4. 抖动保护：节点自己连续续约成功满一个 TTL 之后，才有资格判定其他节点死亡。TBase 刚恢复时大家的心跳都会短暂缺失，这条规则防止全员互相认领。
5. 接管时的会话清单：`active:{sN}` 中的会话，加上 `XRANGE wake:{sN} (wake_offset +` 读出的会话，两者合并后一起激活。否则"Gateway 已确认、但旧所有者还没读到 wake"的新会话会被漏掉（新会话还不在 active 中，兜底扫描也覆盖不到它）。
6. 不需要自我隔离：旧所有者即使还活着，只要新所有者 CAS 更新了会话文档的 `owner_epoch`，旧所有者对该会话的下一次提交就会失败，它随即放弃这个会话。Stream 读取没有副作用，旧所有者读到了也无害。
7. 计划内切换：节点标记 draining，完成当前批次后主动把 `shard:<i>` 交给继任者，≤ 1 秒。

### 5.2 会话处理循环
```plain
分片所有者 Worker：
  接管分片时：SMEMBERS active:{sN} ∪ XRANGE wake:{sN} (wake_offset +
              → 用 pipeline 批量读取会话文档 → 逐个 CAS 写入 owner_epoch → 重建定时器
              → 重新执行 cmd_published=false 的下发 → 重新发布 pending_publish 中的事件
  日常：XREAD BLOCK wake:{sN}（从 wake_offset 开始）→ 会话 sid 有新消息
              → 交给会话 sid 的处理队列（每个会话单线程串行，不同会话并行）
              → 该会话这一批处理完成后（含下面第 7 步的 active 登记或移出），wake_offset 才能推进过对应的 wake（低水位，批量推进）
    1. XRANGE inbox:{sN}:sid (inbox_offset +  COUNT 100
    2. 按事件类型去重（见下表）
    3. 执行状态机 → 新状态、硬事件 E[]、新的下发意图 D[]、需要写入 OB 的结果 R[]
    4. 先写 OB：R[] 不为空（Task 终态 / 群一轮结束）时，幂等写入 OB
    5. 提交：原子更新会话文档（条件 owner_epoch）：state、offset、tasks、dispatches、event_seq，
       以及本批次的关键事件写入 pending_publish
       提交失败 → 所有权已被接管 → 放弃该会话
    6. 执行副作用：XADD 硬事件到 evt → XADD D[] 到 cmd:* → 标记 cmd_published
       → 清除已写入 evt 的 pending_publish（可以在下一次提交时顺带清除）
       → SOFAMQ 推送通知（P1）→ XTRIM inbox MINID
    7. 本批处理结束后更新 active：会话还有进行中的 Task、dispatch 或非空的 pending_publish → SADD；
       否则 SREM，再重新读一次 inbox，有新消息就 SADD 回来继续处理
```

第 2 步按事件类型去重。"taskId 已存在就跳过"只适用于创建请求，不适用于后续事件：

| inbox 事件 | 如何判断重复 |
| --- | --- |
| 创建请求（不带 taskId） | 推导出的 taskId 已在 `tasks`、`recent_done` 或 OB `bcn_task` 中 → 重复创建，跳过；摘要与 `create_digest` 不同时告警 |
| 调用方的后续消息（带 taskId，例如人工回答） | 按 3.2 先从 taskId 定位会话；messageId 在该 Task 的 `seen_msgs` 中 → 跳过；否则校验任务状态：终态返回 `UnsupportedOperationError`，找不到返回 `TaskNotFoundError` |
| 取消 | 按业务状态：已经取消或已结束 → 不做任何操作 |
| Connector 回报的结果或完成 | 按 dispatch 终态去重：已是 DONE / FAILED → 丢弃 |
| Connector 回报的中断类事件（input-required、auth-required） | 按交互 ID 去重：ID 在该 dispatch 的 `seen_interactions` 中 → 重复，丢弃；否则是新的一次交互，登记 ID 后处理。不能用 dispatch_id + 事件类型，同一个 dispatch 可以先后多次要求输入 |
| 下游推送 | 先映射到 dispatch，再按 dispatch 状态处理 |


为什么先写 OB，再提交文档：两个存储之间没有事务。

+ 第 4 步写 OB 之后、第 5 步提交之前崩溃，新所有者会从旧 offset 重新处理这批消息。
+ 重新处理到第 4 步时，用幂等键（taskId，或 group_id + turn_id）发现 OB 里已经有记录，就直接采用这份记录、不重新计算。这样即使编排时有不确定的选择（例如 LLM 决定下一个发言者），重放的结果也和第一次一致。
+ 终态不可变，重复写入没有影响。
+ 实现约束：OB 写入使用"已存在就不覆盖"，写完读回。读回的记录和本次计算结果不同时，丢弃本次结果，按读回的记录重建状态和后续动作，不能继续使用本次的临时计算结果。测试要覆盖：在"写完 OB、还没提交会话文档"时注入崩溃，且编排逻辑带不确定的选择，验证重放结果和第一次一致。

最终结果待发布（`pending_publish`）：会话文档已经提交了"Task 完成"和新的 inbox 位置，但完成事件还没写入 evt 时 Worker 崩溃，新所有者从新位置继续处理，不会再产生这个事件，调用方的实时流会一直挂到超时。所以：

+ 只有"流必须以它收尾，或调用方要据此行动"的事件放入 `pending_publish`：终态，以及 input-required、auth-required 等中断态。它和 Task 状态在同一次提交中写入。
+ 写入 evt 成功后清除；晚一点清除没有影响，因为 Gateway 按 `(taskId, event_seq)` 去重。
+ 接管时把 `pending_publish` 中的事件全部重新发布。
+ token 增量、审计、中间状态通知不放进来，不做通用 outbox。

副作用只在提交成功后执行，旧所有者的提交会失败，所以它不会产生副作用。新所有者重新执行第 6 步时可能产生重复，各接收方都能去重：evt 按 `(taskId, event_seq)`；cmd 按 idem_key；推送通知按 `(taskId, event_seq)`。

wake 的恢复规则：

+ Gateway 只有 inbox 和 wake 都写成功才回复确认。写完 inbox、没写 wake 就崩溃时，调用方没收到确认，不算已确认消息丢失，不需要跨存储事务。是否自动重试取决于上游客户端或 SDK，不作为默认行为；客户端实现重试时，沿用原 messageId 重试，由 3.2 的确定性推导保证落到同一个 Session 和 Task。
+ 已确认的唤醒不能丢：wake 的消费位置 `wake_offset` 记录在分片记录里，只能按低水位推进，即所有已读到的 wake 对应的会话处理都完成之后才能推进（和消费者处理完才提交位点是同一个做法）。处理完成指会话这一批已处理，并已按下面的规则登记到 `active:{sN}` 或完成移出复查。处理途中崩溃，接管者从 `wake_offset` 往后重新读取，重复处理由 `inbox_offset` 和去重规则兜住（见 5.1）。
+ 兜底扫描：Worker 每 10 秒检查一次 `active:{sN}` 中每个会话的 inbox 最新 ID，与文档里的 offset 比较，落后就处理。它覆盖已登记的会话，新会话靠 `wake_offset` 保证。

active 的登记和移出（每批处理结束时，由该会话的处理队列执行）：

+ 登记：会话还有进行中的 Task、进行中的 dispatch，或非空的 `pending_publish` → `SADD`（幂等）。等待人工输入的 Task 也在 `tasks` 里，所以它所在的会话不会被移出，超时定时器能在接管时重建。
+ 移出：三者都为空 → 先 `SREM`，再重新读一次 inbox（从 `inbox_offset` 往后）；有新消息就 `SADD` 回来继续处理。先移出再复查：复查时已经到达的消息会被看到；复查之后才到的消息，它的 wake 一定晚于这次 `SREM`，Worker 读到后会重新登记。
+ 移出后会话文档保留，下一条消息的 wake 会把它重新登记。所以接管时只需要恢复 `active` 里的会话，以及 `wake_offset` 之后未处理的会话，不用加载空闲会话。
+ 为什么 `wake_offset` 要用低水位：如果在 `SADD` 之后就推进 `wake_offset`，而移出发生在崩溃前，这条 wake 已被视为处理完，会话却不在 `active` 里，消息会一直无人处理。低水位保证处理没结束就不会推进。

### 5.3 Gateway：调用方请求、流式输出与续传
| 操作 | 处理 |
| --- | --- |
| 所有请求 | 先检查 `A2A-Version`（header 或请求参数）：不是 1.0 就返回 `VersionNotSupportedError`，不进入后面的流程。没带版本的请求按规范视为 0.3，同样返回该错误 |
| `SendStreamingMessage` | 按 3.2 确定 sessionId 和 taskId → `XADD inbox` + `XADD wake` → 两者都写成功后才返回 `Task{SUBMITTED}`（RPO = 0 目标的提交点，以 V1 验证为前提）→ 接着 `XREAD BLOCK evt`，从写入前记下的位置开始 |
| `SendMessage` + `return_immediately: true` | 同上，写入成功后返回 `Task{SUBMITTED}`，不再读事件流 |
| `SendMessage`（默认，阻塞） | 同样先写 inbox 和 wake；之后 Gateway 读 evt，等到 Task 进入终态或中断态（INPUT_REQUIRED、AUTH_REQUIRED），再返回带最新状态和 artifact 的 Task（从会话文档或 OB 取）。等待期间 Gateway 崩溃：客户端用原 messageId 重试，按 3.2 落到同一个 Task，已到终态或中断态就直接返回，否则继续等。最长等待时间可配置，超时返回可重试错误，这与规范字面要求（等到终态或中断态才返回）有出入，需要在接口文档中说明；长任务建议调用方用流式或 `return_immediately` |
| `GetTask` | 先查会话文档（进行中的任务），查不到再查 OB `bcn_task`（已结束的任务） |
| `ListTasks` | 查 OB。已结束的任务是强一致的；进行中的任务状态是最终一致的，因为创建记录是异步写入的，需要在接口文档中说明 |
| `SubscribeToTask` | 第一个事件发快照：会话文档中的状态 + 从 evt 汇总当前步骤已累积的增量；之后推送实时事件。任务已结束则返回 OB 中的最终 Task。建流时读一次 `disp:` 的 `conn_epoch`，作为过滤用的 `latest_epoch` 初值（见下） |
| `Last-Event-ID`（BCN 扩展） | 该 ID 还在 evt 中就精确补发，否则退化为快照 |
| `CancelTask` | 写入 inbox，由 Worker 处理 |


事件分两类：

+ 软事件：token 增量，由 Connector 每 50 毫秒合并一次写入 evt，对应 A2A 的 `artifact_update`（`append=true`）。每条增量带 `(dispatch_id, conn_epoch)`。
+ 硬事件：状态变化、完整 artifact，由 Worker 提交后写入 evt。

顺序保证：Connector 先写完最后一个增量，再把"完成"写入 inbox；Worker 提交后才写硬事件。所以在事件流里，"完成"一定排在该步骤所有增量之后。SSE 的 `id:` 字段填 stream ID。

过滤旧 Connector 迟到的增量：只会在旧 Connector 其实没死（网络分区或假活）、新 Connector 已经接管时出现，此时两个 Connector 会同时往同一条流写。Gateway 为每条调用方的流、每个 dispatch 在内存里记一个 `latest_epoch`，读 evt 时按下表处理。过滤放在 Gateway，因为增量不经过 Worker，Gateway 是唯一按顺序看到它们并决定是否转发的地方；放在 Connector 端则每条增量都要先读一次 `disp:`，代价太大。

| 读到的事件 | 处理 |
| --- | --- |
| reset（`append=false` 的整体替换快照，带 epoch N） | 转发，并把 `latest_epoch` 更新为 N |
| 增量，epoch 不小于 `latest_epoch` | 转发 |
| 增量，epoch 小于 `latest_epoch` | 丢弃（旧 Connector 迟到的输出），并计数 |
| 没带 epoch 的事件（旧版本写的） | 转发，保证新旧版本兼容 |
| 状态变化、final 等硬事件 | 不过滤，它们来自 Worker |


reset 之前写入的旧 epoch 增量不用过滤，它们会被 reset 整体替换。完成结果不需要用 epoch 处理：Worker 按 dispatch 终态去重，而且同一个下游任务的 final 内容相同。

A2A 标准的 `SubscribeToTask` 只提供"快照 + 继续"，没有事件 ID 和断点语义。BCN 的 `Last-Event-ID` 精确续传是超出标准的增强，需要在 AgentCard 的 `extensions` 里声明。

### 5.4 下发与监督（会话所有者 Worker 负责监督）
```plain
Worker 提交下发意图（会话文档中的 dispatches）
  → XADD cmd:a2a:<k>（Connector 池通过消费组领取）

Connector 领取指令：
  先确认会话文档里存在该 dispatch（防止执行旧所有者的残留指令）
  → SET disp:{sN}:<did> NX，写入 status=SENT（或者 CAS 接管）；NX 失败说明已有其他 Connector 领取 → XACK 后丢弃
  → NX 成功后才调用下游（因此"没有 disp 记录"就意味着下游一定没被调用过；有 disp 记录但没有 downstream_task_id，就是模糊窗口）
  → 拿到下游 taskId 后写入 disp:（status=ACCEPTED）
  → artifact_update 合并后带 (dispatch_id, conn_epoch) XADD evt，并至少每 5 秒递增一次 disp 的 progress_seq
  → 终态 XADD inbox + wake，disp 置为 ENDED（之后设置过期时间）
```

A2A 下游的调用方式：

+ 需要 token 流：`SendStreamingMessage`，第一个事件必须是 Task，带 taskId。
+ 不需要 token 流：`SendMessage` + `return_immediately: true`。
+ 禁止使用默认阻塞的 `SendMessage`：它要等到任务进入终态或中断态才返回，整个执行过程都处于模糊窗口。
+ 每次请求带 `A2A-Version: 1.0`；下游返回 `VersionNotSupportedError` 的 Agent 视为不可用，dispatch 判为 FAILED 并写明原因。一期不做 0.3 适配。
+ `messageId` 设为 idem_key；Agent 支持推送通知时，同时在请求里配置推送，回调地址指向 Gateway 的推送接收端。

taskId 只能由下游服务端生成（A2A 1.0 不支持调用方为新任务指定 taskId），所以"发出请求"到"拿到 taskId 并写回"之间一定存在模糊窗口。

Worker 每秒检查一次自己的进行中 dispatch：

+ 指令已发出但迟迟无人领取：`cmd_published=true`，超过 3 秒仍没有 `disp:` 记录（例如 Connector 刚从消费组取到指令就崩溃）→ 用同一个 dispatch ID 重新投递。这种重投没有模糊窗口，因为没有 disp 记录说明下游没被调用过；原来那条指令后来即使被领到，也会因 NX 失败被丢弃。恢复不依赖消费组的未确认消息接管（`XAUTOCLAIM`）。
+ 持有该 dispatch 的 Connector 已经死亡（两个心跳 key 都不存在）：
    - 重新发一条 `cmd:a2a`，标记为 `resume`。新 Connector CAS 接管 `disp:`（`conn_epoch` +1），读取其中的 `downstream_task_id`，按下表能力等级恢复；取到下游快照后，先往 evt 写一条 reset（`append=false` 整体替换，带新 epoch），再继续写增量。
+ 进展停滞：dispatch 处于 ACCEPTED 之后、且所属 Task 处于 WORKING 时，`progress_seq` 超过 N 秒没有变化 → 看门狗处理，见 6.1。
+ 超过截止时间：按策略重试、换 Agent，或判为失败。

由 Worker 监督的原因：Worker 本来就持有这些 dispatch 的截止时间。这样 Connector 不需要维护"自己名下有哪些 dispatch"的索引，也不需要 Connector 之间互相接管。检测耗时是心跳 3 秒 + 检查周期 1 秒，之后续接下游，总共约 6 秒以内。

### 5.5 下游 A2A Agent 的能力分级
能力通过 AgentCard 的 `capabilities.extensions` 声明，第三方 Agent 改不了 AgentCard 时由管理员在 Bot 注册信息里配置：

```json
"extensions": [{
  "uri": "https://bcn.antgroup.com/ext/reliability/v1",
  "params": {
    "idempotentMessageId": true,
    "dedupWindowSec": 3600,
    "taskQueryable": true,
    "resubscribable": true,
    "sideEffects": "none | idempotent | non-idempotent"
  }
}]
```

| 等级 | 能力 | dispatch 处于已发送、未拿到 taskId（模糊窗口）时崩溃 | 已拿到 taskId 之后崩溃 |
| --- | --- | --- | --- |
| A | 按 messageId 幂等，且可查询或可订阅 | 用同一个 messageId 重发 | `SubscribeToTask`（第一个事件是快照）；任务已结束则 `GetTask` |
| B | 不幂等，但可查询、可订阅或支持推送通知 | 先找证据：是否已经收到对应的推送？Agent 接受调用方指定 contextId 时，用 `ListTasks(context_id)` 查询。只有查到的任务能确定对应原请求时（contextId 为这次下发专用、创建时间晚于下发时间，有条件时核对回显的 idem_key）才续接；查不到或不能确定，按 `onAmbiguous` 处理 | 同 A |
| C | 只有流式，不能查询 | 按 `onAmbiguous` 处理 | 按 `onAmbiguous` 处理 |
| M | 只回复 Message（没有 Task、没有 taskId、没有 token 流） | 回复到达之前任何时刻崩溃都按 `onAmbiguous` 处理 | 不适用 |


"查不到"不能证明原请求没有执行：原请求可能还在网络上，查询之后 Agent 才收到并创建任务；Agent 可能先返回、后台再创建任务；`ListTasks` 的索引可能有延迟。所以 B 级和 C 级的区别只在于 B 级有机会找到证据走正常续接，找不到证据时两者处理相同。

`onAmbiguous` 的默认值由 `sideEffects` 决定：`none` / `idempotent` → retry（重发，只采纳第一个到达终态的结果，其余丢弃或取消）；`non-idempotent` 或未声明 → fail-explicit（这一步标记为 UNKNOWN，交给上层决定：S1 返回给调用方；S2 交给群编排策略，可以跳过该成员、请求人工确认或换一个 Agent）。

BCN 协议 bot（WS 入站）路径一期后置，其恢复规则（请求 ID 去重、事件 seq/ack、连接注册表、离线语义）届时另行设计。

BCN 自身保证：步骤完成按 dispatch 终态去重，先到的生效、晚到的丢弃，会话状态和群消息记录不会重复推进业务。流式输出的边界：故障或重发后，中间输出允许重置，由完整的 final 覆盖；保证最终结果正确、可查询，不保证逐个 token 精确恢复，也不保证调用方完全看不到被重置或重复的中间输出。

### 5.6 群 Session（S2）
+ 每一轮结束时：先把这一轮的记录同步写入 OB `bcn_group_message`（幂等键 turn_id），再提交会话文档、推进到下一轮。
+ 会话文档里只保留编排状态，以及最近几轮的摘要或指针；完整的群消息记录在 OB。
+ 空闲处理（一期）：群 Session 空闲超过阈值后，只释放内存中的 Actor 和定时器，保留 TBase 中的会话文档，并按 5.2 的规则移出 `active:`（没有进行中的 Task 和 dispatch 时），有新消息时由 wake 重新登记并加载。存档到 OB 并从 TBase 物理删除（`bcn_session_archive`）、复杂的休眠迁移与清理后置。代价是 TBase 中的会话文档随累计的群 Session 增长，需要在规模评估（见 10）中计入。
+ 不确定的结果只记录、不重算：Agent 的回复、编排时的选择、当前时间，都先作为事件落入 OB，再基于它做决策，重放时直接使用记录下来的结果。
+ 一轮中并行下发给多个 Agent，每个 dispatch 独立记录；所有回复到齐、达到法定数量或超时后，这一轮才结束。
+ 旁观成员的静默投递（BCN 协议的 `chat.inject`）属于 BCN 协议路径，一期后置；一期群成员都是 A2A Agent。
+ 对调用方：一次 A2A 请求对应一个 Task。哪些成员的 token 增量推给调用方由群策略决定；每轮的结果以硬事件或 artifact 的形式输出。
+ 取消：`CancelTask` 写入 inbox，Worker 处理后对每个进行中的 dispatch 发一条 `cancel` 指令（走 `cmd:a2a`，用 `cancel_requested` 和 `cancel_published` 标记，补投机制与下发相同）。任一 Connector 领取后，用 `disp:` 里的 `downstream_task_id` 向下游发 `CancelTask`（A2A 规定取消是幂等的）。dispatch 还没拿到下游 taskId（SENT）时，Worker 记下取消意图，拿到 taskId 后再发 `cancel`；仍拿不到就按截止时间处理。

### 5.7 定时器与推送通知
+ 定时器：截止时间保存在会话文档里（dispatch 的 deadline、会话超时）。Worker 为自己持有的活跃会话维护内存定时器；接管分片时，从会话文档重建，不需要额外的定时存储。
+ BCN 推给调用方：Worker 执行副作用时，如果该 Task 配置了推送，发一条消息到 SOFAMQ。Push Service 负责签名、经出口代理投递、退避重试、失败进死信队列。
+ 下游推给 BCN：Gateway 的推送接收端校验 token → 按 dispatch 找到会话 → 写入 inbox 和 wake（token 增量写入 evt）。

## 6. 故障处理
| 故障 | 如何感知 | 如何恢复 | 调用方的感受 | 时效 |
| --- | --- | --- | --- | --- |
| Worker 崩溃 | 两个心跳 key 3 秒过期，加检测周期 ≤ 0.5 秒 | CAS 认领 → 合并 active 和未处理的 wake → 批量读取会话文档并逐个 CAS → 补发 `pending_publish` 和未发出的下发 → 从 offset 继续处理 | token 流不中断，状态迁移延迟约 5 秒 | 约 5 秒 |
| Worker 假活 | 进度探针（处理队列积压但 offset 不前进）≤ 5 秒 → 主动摘除 | 同上；假活 Worker 恢复后提交失败，自动放弃 | 同上 | 满足 |
| Gateway 崩溃 | 调用方连接立即断开 | 重连任意 Gateway 续传；重试请求时，确定性推导的 sessionId 和 taskId 保证幂等（包括首次没带 contextId 的请求） | 调用方断流，重连后立即可续传（对端重连时间不计入） | 服务端保证，见 6.1 |
| Connector 崩溃（A2A 下游） | 已建 disp：Worker 发现心跳过期，≤ 4 秒；未建 disp：指令 3 秒无人领取 | 已建 disp：重新下发 resume 指令 → 新 Connector 续接下游；未建 disp：同一 dispatch ID 重投 | 增量暂停 ≤ 6 秒，之后补发快照 | 满足（A/B 级） |
| TBase 单个节点故障 | 命令报错 | 等待 TBase 恢复（≤ 30 秒）。只影响落在该节点上的分片；两个心跳 key 避免把节点误判为死亡 | 受影响的会话最多 30 秒内无法接收消息、推流暂停 | 按约定接受，不计入 3~10 秒 |
| OB 故障 | 写入报错 | 只阻塞"需要写结果"的步骤（Task 终态、群一轮结束），这些步骤退避重试；其他消息照常处理 | Task 停在终态之前，暂时不对外发布完成 | 取决于 OB 的 RTO |
| 毒会话 | 会话文档中的 `takeover_streak` 连续 K 次接管都没有成功提交 | 新所有者读到 `takeover_streak` 达到 K 时，先把会话 `status` CAS 置为 `QUARANTINED`，再把进行中的 Task 判为失败（写入 OB）并告警；之后发给该会话的消息直接返回错误，由人工运维操作解除 | 收到明确的失败 | — |


Worker 崩溃的时间预算：感知 3~3.5 秒 + 认领 0.1 秒 + 批量激活 ≤ 1 秒 + 处理积压 ≤ 1 秒，合计约 5~6 秒。

保护措施：

+ 准入控制：Gateway 按租户和 bot 限流（TBase 计数器）；分片积压超过阈值时拒绝新会话。
+ 防恢复风暴：一次接管中，同时激活的会话数有上限；调用方重连的续传请求限速（见 6.1）。

### 6.1 边缘层（Gateway 和 Connector）故障切换
Worker 故障是服务端主动接管；Gateway 和 Connector 故障时连接已经断了，服务端没法替对端重连。所以边缘层的目标是服务端保证，对端（调用方、下游）何时重连不计入 3~10 秒。

| 组件 | 服务端保证 |
| --- | --- |
| Gateway | 故障节点被 VIP 摘除后，任意存活的 Gateway 立即能处理 `GetTask`、`SubscribeToTask` 和用原 messageId 的重试请求（Gateway 无状态） |
| Connector | 下游流续接完成，约 6 秒以内（心跳 3 秒 + 检查周期 1 秒 + 续接） |


第三方 A2A 调用方只享有 Gateway 这一行的保证，不承诺 3~10 秒。

入口 VIP：Gateway 前面是 VIP，它在三个地方起作用。

| 场景 | 作用 |
| --- | --- |
| Gateway 故障 | VIP 对 Gateway 实例做健康检查，连续失败几次后自动摘除。摘除之前，新请求还会打到已经挂掉的节点上失败 |
| Gateway 发布 | 发布前主动摘流，再等存量请求结束 |
| 下游推送回调 | 回调地址用 VIP 域名，不绑具体节点；挂掉的节点被摘除后，推送自然不再打到它 |


Connector 一期没有入站连接，不挂在 VIP 后面；它的"摘流"就是停止从消费组领指令。VIP 的健康检查间隔、失败阈值、主动摘流接口见 V6。

Gateway 故障：

+ 调用方连接立即断开。重连的调用方用 `SubscribeToTask` 或 `Last-Event-ID` 在任意存活 Gateway 上续传；请求受理途中挂掉的，客户端用原 messageId 重试，确定性推导保证落到同一个 Task。
+ 调用方重连的续传请求要限速，并对同一个 Task 的快照做短时缓存，避免一批流同时断开后集中重连、反复构建快照。
+ 下游推送由 VIP 转给任意存活 Gateway，下游的重试在 Gateway 恢复前由下游自己负责；下游放弃投递时，依赖推送做证据的 B 级恢复回落到 `onAmbiguous`。

Connector 故障：

+ 整个 Connector 挂掉：由 Worker 监督发现（见 5.4），新 Connector 续接下游。新旧 Connector 同时写事件流的情况，由 `conn_epoch` 加 Gateway 过滤处理（见 5.3）。
+ Connector 假活（进程卡住、心跳线程还在跳）：由下面的看门狗兜底。

看门狗（dispatch 进展停滞）：

+ 计时器在会话所有者 Worker 里，复用已有的每秒一次的 dispatch 监督循环，不新增线程或服务。数据在 TBase，不依赖 OB。
+ Connector 在 `disp:` 里递增 `progress_seq`：有事件就递增，没有事件也至少每 5 秒一次。
+ Worker 用自己的单调时钟记录"上次看到 `progress_seq` 变化的时间"，超过 N 秒没变就触发处理。不比较跨机器的时间戳，没有时钟偏差问题。不能让 Connector 自己计时，因为假活的 Connector 就是不能自查的那个。
+ 触发后，Worker 让 Connector 池里的另一个 Connector 用 `GetTask` 探测下游：还在跑就重置计时；已终态就补发结果；探测不到就按 resume 流程处理。
+ Task 处于 INPUT_REQUIRED 或 AUTH_REQUIRED 时暂停计时（等待人工输入期间没有事件是正常的），人工回答后重新计时。
+ N 可配置。Worker 换人后内存里的观察时间重置，相当于多给一个 N 秒的宽限，可以接受。
+ 代价是每个活跃 dispatch 每 5 秒一次 TBase 写，规模确认后评估，间隔可以调大。
+ 它只兜底"进程活着、这个 dispatch 卡住"；整个 Connector 挂掉仍由节点心跳覆盖。

优雅退出（发布和缩容）：

| 组件 | 流程 |
| --- | --- |
| Gateway | 先从 VIP 摘流，等宽限期让存量请求和流自然结束，到期仍未结束的流由 Gateway 主动结束，然后退出 |
| Connector | 停止从消费组领新指令，等手上的 dispatch 走到已拿到下游 taskId 或完成，到期没完成的停止心跳并退出，由 Worker 监督按故障流程接管 |
| 合并部署的边缘进程 | 先按 Gateway 流程，再按 Connector 流程 |


宽限期可配置，要和发布系统的强杀超时对齐。强杀等同于崩溃，走故障恢复路径。所以边缘层不承诺"≤ 1 秒无感切换"，承诺的是"优雅退出，尽量不中断；超时走故障恢复"。

合并部署的边缘进程崩溃时，调用方流和下游流同时断。两条恢复路径互相独立，通过事件流汇合，谁先恢复都不影响结果，但恢复时间会叠在同一个窗口里，验收时要单独测一次。

已知限制：

+ 过滤只覆盖 reset 之后到达的旧增量；reset 之前写入的旧增量由 reset 整体替换，调用方最多短暂看到旧文本。
+ 看门狗的 N 和 VIP 的摘除时间还没有确定值，无法给出边缘层精确的时间预算。

## 7. 扩容、发布与可观测
+ 扩容：边缘进程和 Worker 都可以直接加机器；Worker 加入后按 rendezvous hash 逐步接管分片，每次只迁移少量分片并限速。
+ Worker 发布：节点逐个 draining，主动交出分片，每个分片切换 ≤ 1 秒。会话文档带 `schema` 字段，新版本先上线读取能力，旧版本全部下线后再启用新的写入格式，因为会话文档会在新旧版本之间互相接管，这个顺序必须严格遵守。
+ 边缘进程发布：优雅退出，先摘流，再等宽限期，到期仍未结束的按故障路径处理，规则见 6.1。
+ 关键指标：接管次数和耗时（P50 / P99）、误判率、分片积压（inbox 最新 ID 与文档里 offset 的差距）、首 token 延迟、各能力等级下的 UNKNOWN 和重发次数、被隔离的毒会话。
+ 链路追踪：`traceparent` 写入 inbox 消息和 dispatch 记录，故障切换前后的调用链保持连续。

## 8. 待验证事项
| # | 对象 | 内容 | 重要程度 | 不满足时的影响 |
| --- | --- | --- | --- | --- |
| V1 | TBase | 单节点故障时 RPO 是否为 0（"高保"的具体保证）。会话文档和 inbox 都在 TBase，这一项决定进行中的会话会不会丢 | 关键 | 会话文档退回 OB（即 v1 设计）；Gateway 同时把消息写一份到 OB 用于对账 |
| V2 | TBase JSON Store | 条件原子更新：能否在"某个路径的值等于期望值"的前提下，原子地修改多个路径？单个文档的大小和更新延迟 | 关键 | 同上 |
| V3 | TBase | `shard:` 的 CAS 语义；同 slot 多 key 的 `XREAD BLOCK`；阻塞连接规模；`XTRIM MINID`；`XREADGROUP` / `XACK`；故障切换期间阻塞读的表现 | 中 | Gateway 退化为每 50 毫秒批量轮询；加客户端超时重连；`cmd:a2a` 改为按 key 分配给固定 Connector |
| V4 | OB | 现在每个 Task 只有一到两次写入、每轮群讨论一次写入，压力很小；确认 RTO | 低 | — |
| V5 | BCN 协议 | 一期后置：bot 端 seq/ack 重放和幂等键去重（BCN 协议 bot 的 WS 路径整体后置） | — | — |
| V6 | VIP | 健康检查间隔和失败次数、是否有主动摘流接口、摘流后存量连接的处理 | 中 | 摘除时间不确定，无法计入 Gateway 切换预算 |
| V7 | A2A 调用方 | 主流 A2A 客户端 SDK 对 `artifact_update` 中 `append=false`（同 artifactId）的处理是否为整体替换。规范只写了"append 为 true 时追加到同 ID 的 artifact"，没有明说 false 的含义 | 中 | 故障后补发的 reset 快照可能被当作追加，输出重复，需要评估备选做法 |
| V8 | TBase | 集群级上限：总 ops/s 与带宽；单个 slot 的持续 ops/s（决定逻辑分片数）；`XREAD BLOCK` 的并发连接数；Stream 单条 `XADD` 的大小和写入延迟；单 key 持续写入的上限（限流计数、wake）。容量评估（12）里的"单分片 3 万 ops/s"是假设 | 高 | 第 12 章的分片数、逻辑分片数、token 合并间隔都要重算 |
| V9 | OB | 租户 TPS 上限；同步写（终态、群每轮）P99 延迟；主键点查 QPS；批量插入的吞吐 | 中 | 入口请求超过 1 万/s 时，OB 的批量写和读缓存要提前设计 |


如果 V1 或 V2 不满足，只需把会话文档退回 OB，其余部分（Stream、监督、下发分级）不受影响。

## 9. 分期落地
| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P0-a | 边缘进程（Gateway + Connector 合并部署）+ Worker + TBase 协调和会话文档 + OB 结果表；S1 + A2A 下游（A/B 级）；Worker、Gateway、Connector 的故障切换 | 单台 Worker 满载时崩溃，P99 ≤ 10 秒；token 流不中断；已确认消息零丢失 |
| P0-b | S2 群编排、群 Session 空闲时释放内存 Actor（持久状态保留）、毒会话隔离、计划内切换 | 群 Session 在多次故障切换后结果一致；发布期间不中断 |
| P1 | 推送通知、C/M 级策略、能力一致性探测 | 推送通知零丢失 |
| 后置 | BCN 协议 bot 路径：WS 入站连接、连接注册表、离线语义、seq/ack 扩展 | 另行设计 |
| P2 | 故障演练常态化、自动扩缩容、拆分 Gateway 和 Connector 的部署、容量基线 | — |


### 最小验收用例
验收的核心是：消息能被重新发现、已保存的待办能继续执行、最终结果能补发。"通过"指设计处理被接受，持久性和恢复时延仍以实测为准。

| # | 用例 | 预期 |
| --- | --- | --- |
| 1 | 首次请求（没带 contextId）跨 Gateway 重试 | 落到同一个 Session、同一个 Task，不重复创建 |
| 2 | wake 处理途中换 Worker | 新所有者从 `wake_offset` 重新读到未处理的唤醒，新会话不被漏掉 |
| 3 | 提交后尚未发布完成事件时宕机 | 接管者从 `pending_publish` 补发最终结果，调用方的流正常收尾 |
| 4 | Connector 领取 cmd 后、登记 disp 前宕机 | 3 秒后同一 dispatch ID 补投，下游只被调用一次 |
| 5 | 非幂等下游结果不明 | 不盲目重发，该步骤进入 UNKNOWN |
| 6 | OB 已写入、会话文档未提交时宕机 | 重放采用 OB 已保存的结果，状态和后续动作与第一次一致 |
| 7 | 对 input-required 的人工回复只带 taskId、不带 contextId | 路由到原会话，不推导新会话；同时带了不一致的 contextId 时被拒绝 |
| 8 | 同一个 dispatch 先后两次 input-required | 两次都被处理；同一次 input-required 重投时只处理一次 |
| 9 | Gateway 崩溃，VIP 摘除后 | 在存活 Gateway 上用原 messageId 重试和 `SubscribeToTask` 立即可用，结果与崩溃前一致 |
| 10 | Connector 假活：`progress_seq` 停止变化超过 N 秒 | 看门狗触发探测并续接，不依赖节点心跳 |
| 11 | 新旧 Connector 同时写增量 | Gateway 只转发 reset 之后 epoch 不小于 `latest_epoch` 的增量，丢弃的有计数 |
| 12 | 合并部署的边缘进程崩溃 | 调用方流和下游流同时断，两条恢复路径互不影响，最终结果正确 |
| 13 | 边缘进程优雅退出 | 宽限期内进行中的请求正常完成；超时未完成的走故障恢复，不丢结果 |
| 14 | 会话转为空闲、移出 `active` 的同时收到新消息，Worker 崩溃 | 新所有者从 `wake_offset` 重新读到唤醒，新消息不丢 |
| 15 | 取消时 dispatch 还没拿到下游 taskId | 记下取消意图，拿到 taskId 后发出 `cancel`，Task 最终为 CANCELED |
| 16 | 毒会话：连续 K 次接管都没能提交 | 会话被置为 QUARANTINED，Task 失败，之后的消息返回错误 |
| 17 | 阻塞模式的 `SendMessage` 中途 Gateway 崩溃，客户端用原 messageId 重试 | 返回同一个 Task 的当前状态；已到终态或中断态则直接返回 |
| 18 | 请求没带 `A2A-Version`，或带 0.3 | 返回 `VersionNotSupportedError`，不创建任何状态 |


## 10. 仍需确认
1. 规模目标：并发会话数、下游并发流数、峰值 token 吞吐、群 Session 通常持续多久。这些影响分片数、会话文档大小、V3 的阻塞连接规模。
2. token 走快路径的代价：token 增量属于软事件，一期允许故障后中间输出重置，由完整 final 覆盖；保证最终结果正确、可查询、不重复推进业务，精确恢复每个 token、无重复的增量续传后置。如果业务要求每个 token 都精确可回放，就要让增量也经过 Worker 并落库，首 token 延迟会增加。
3. Task 刚受理（SUBMITTED）到 Worker 首次提交之间（Worker 故障切换时可能长达约 5 秒），`GetTask` 和 `SubscribeToTask` 在会话文档和 OB 里都查不到这个 Task，会返回 `TaskNotFoundError`，而 Gateway 刚刚才把它返回给调用方。可选方案：Gateway 写 inbox 时同时 `SET NX` 一个带过期时间的 Task 标记，查不到时据此返回 SUBMITTED；或者查不到时回退读 inbox。会在热路径上多一次写入，待决定。
4. 对外暴露哪些协议绑定（JSON-RPC、gRPC、REST）：规范要求一个 Agent 声明的多种绑定功能等价，每多一种绑定，Gateway 就要多实现一套等价的语义，待决定。
5. 容量评估（12）产生的待决策项：token 合并间隔与 evt 条目大小、逻辑分片数、Worker 互检和 Connector 存活检查的方式、`progress_seq` 与监督读的频率、限流计数方式、创建时的 OB 去重读、evt 保留策略、群的流转发策略、群一轮结果合批提交，共 10 项，见 12.8。

## 11. A2A 1.0 依赖清单
本章汇总方案对 A2A 协议的依赖，依据 A2A 1.0 规范（`specification.md`、`a2a.proto`）。"规范强度"一列按规范用词标注：MUST 是协议义务，MAY 是可选能力。

### 11.1 一期范围
| 项 | 结论 |
| --- | --- |
| 协议版本 | 只支持 1.0。对外：请求带 `A2A-Version: 1.0`（header 或请求参数）才处理；没带（规范规定按 0.3 处理）或带其他版本，返回 `VersionNotSupportedError`。对下游：调用时带 `A2A-Version: 1.0`；返回 `VersionNotSupportedError` 的 Agent 视为不可用，dispatch 判为失败并写明原因。0.3 适配不做 |
| 下游 Agent 的版本 | 注册时需确认 AgentCard 声明支持 1.0，由管理面负责，不在本方案范围 |
| 协议绑定 | 待定（JSON-RPC、gRPC、REST 的哪几种），见 10。规范要求同一个 Agent 声明的多种绑定功能等价 |


### 11.2 作为服务端（调用方到 BCN）
| 依赖的 A2A 规定 | 规范强度 | 用在哪里 |
| --- | --- | --- |
| 回复可以是 Task 或 Message | MAY | 我们选择始终返回 Task，调用方才能用 `GetTask` 和 `SubscribeToTask` 恢复（§2 原则、§5.3） |
| 流式请求的第一个事件必须是 Task | MUST | Gateway 写完 inbox 和 wake 后立即返回 `Task{SUBMITTED}`（§5.3） |
| taskId 由服务端生成 | MUST | 按请求内容确定性推导 taskId，保证重试幂等（§3.2） |
| 只带 taskId 时服务端推导 contextId；两者不一致必须拒绝 | MUST | §3.2 的判断顺序 |
| `SubscribeToTask` 的第一个事件是当前完整 Task；终态任务不能订阅 | MUST | 续传先发快照再接实时事件；终态改用 `GetTask`（§5.3） |
| 终态是 COMPLETED、FAILED、CANCELED、REJECTED，中断态是 INPUT_REQUIRED、AUTH_REQUIRED；终态任务不再接收消息 | MUST | 先写 OB 再发布终态；人工回答沿用同一个 Task（§4.3.5、§5.2） |
| 事件按生成顺序投递；同一个 Task 允许多条并发流，各自收到相同事件 | MUST | 事件流用 Stream，多个 Gateway 可以各自读取（§4.3.3、§5.3） |
| `ListTasks` 按状态时间倒序、游标分页、按调用方权限过滤 | MUST | 查 OB，按租户过滤（§4.2、§5.3） |
| 默认的 `SendMessage` 是阻塞的，要等到终态或中断态才返回；`return_immediately: true` 才立即返回 | MUST | §5.3 |
| 调用方带 `A2A-Version`，没带按 0.3 处理；不支持的版本返回 `VersionNotSupportedError` | MUST | §5.3、11.1 |
| 推送通知至少尝试投递一次，调用方用 2xx 确认 | MUST | P1 的 Push Service（§5.7） |


### 11.3 作为客户端（BCN 到下游 A2A Agent）
只有前几条是规范保证的，后面都是可选能力，所以才需要能力分级。

| 依赖 | 规范强度 | 不成立时 |
| --- | --- | --- |
| 流式请求的第一个事件是 Task（带 taskId）或 Message | MUST | 是 Message 就没有 taskId，进入 M 级，回复到达前任何时刻崩溃都按 `onAmbiguous`（§5.5） |
| taskId 由下游生成，调用方不能指定 | MUST | 这是模糊窗口无法消除的原因，只能缩小（§5.4） |
| `return_immediately: true` 时任务一创建就返回 | MUST | — |
| 取消是幂等的 | 规范写明 | 重复发 `CancelTask` 安全，但任务被清理后可能返回 TaskNotFound（§5.6） |
| 对 input-required 的回答沿用同一个 taskId 和 contextId | 规范写明 | —（§5.2 后续消息） |
| 按 messageId 去重 | MAY，不保证 | 靠 AgentCard 扩展声明，没声明就按最保守处理（§5.5） |
| 接受调用方指定 contextId、`ListTasks` 能按 contextId 查 | MAY | B 级的证据拿不到，回落到 `onAmbiguous`（§5.5） |
| `SubscribeToTask`、推送通知 | 取决于 AgentCard 声明的能力 | 不支持就只能用 `GetTask` 轮询（§5.4） |
| 下游投递推送失败后可以放弃 | MAY | 依赖推送做证据的恢复回落到 `onAmbiguous`（§5.5） |


### 11.4 超出标准的部分
| 内容 | 说明 | 位置 |
| --- | --- | --- |
| `Last-Event-ID` 精确续传 | A2A 没有事件 ID 和断点语义，只有"快照加继续"。这是 BCN 扩展，要在 AgentCard 里声明 | §5.3 |
| 可靠性扩展（`idempotentMessageId`、`sideEffects` 等） | 用 `capabilities.extensions` 声明下游能力，第三方改不了就由管理员配置 | §5.5 |
| 同一 messageId 重试返回同一个 Task | 规范只写"发送 MAY 幂等"，没要求调用方重试时沿用 messageId，这是我们的约定 | §3.2 |
| 状态消息的持久保存 | 规范明确说重连后可能收不到状态消息，我们存了，比标准强 | §5.3 |
| `ListTasks` 进行中的任务是最终一致 | 规范没规定一致性，需要在接口文档中写明 | §5.3 |
| 阻塞模式 `SendMessage` 的最长等待时间 | 规范要求等到终态或中断态，我们加了可配置的上限 | §5.3 |


### 11.5 未验证的假设
+ reset 用 `artifact_update` 加 `append=false` 表示整体替换，依赖调用方把同 artifactId 的 false 当作替换，规范没有明说，见 V7。
+ 中断类事件的交互 ID 用 `status.message.messageId`，假设 `SubscribeToTask` 快照里是同一个，规范没保证；下游没填或不稳定时，用"下游 taskId 加 `status.timestamp`"。
+ 下游 AgentCard 声明的能力与实际行为一致。一致性探测放 P1。

### 11.6 最硬的三条依赖
如果下面三条不成立，方案本身就要改，其余的都有兜底：

1. 流式请求的第一个事件带 taskId：决定能不能拿到下游 taskId。
2. `SubscribeToTask` 的第一个事件是当前快照：决定续传设计。
3. 终态和中断态的定义：决定 OB 的写入时机和去重规则。

## 12. 流量模型与容量评估
本章评估方案在不同入口请求量下，对 TBase 和 OB 的 ops / TPS / QPS 和压力。**所有数字都是按设计推算的量级，不是实测**：参数来自本章的假设，需要用现网统计和压测校正（V8、V9）。本章不改变设计正文，发现的调整建议集中在 12.8，待决策。

### 12.1 口径
| 项 | 说明 |
| --- | --- |
| 入口请求 | 每秒新建 Task 的 A2A 请求数 λ，评估四档：10 / 100 / 10000 / 100000 |
| 场景 | S1：单 Agent，一个 Task 一个 dispatch；群：一个 Task 包含 R 轮，每轮 K 个成员并行回复 |
| ops | TBase 的一条命令或一个 key 的读写。pipeline 和 `MGET` 能减少网络往返，不减少 key 级 ops |
| 并发量 | 用 Little 定律：并发 = λ × 时长 |
| 现实量级 | 11-02 的流量评估文档假设峰值约 3.3 Turn/s，生产 Bot 约 5900 个。10 档与它同一量级，100 档约有 30 倍余量，10000 和 100000 是极限压力，用来找拐点 |


### 12.2 假设
| 参数 | 值 | 说明 |
| --- | --- | --- |
| 单次下游调用时长 | 30 秒，其中 20 秒产出 token | 假设 |
| token 合并间隔 | 50 毫秒（设计值），对比 200 毫秒 | 50 毫秒即每流每秒 20 条 evt |
| evt 条目大小 | 250 字节 | 含 dispatch_id、conn_epoch、序号等元数据，假设 |
| evt 保留 | `MAXLEN` 200 条，会话结束后 60 秒 | 设计里的保留策略未定量，假设 |
| 会话文档 | S1 约 3.5 KB，群约 20 KB | 假设 |
| 每个 Gateway 流读取 | 每条 evt 一次 `XREAD` 返回 | 上界；读取方落后时可合并 |
| `progress_seq` 写入 | 流式时每秒 1 次，空闲时每 5 秒 1 次 | 设计原文是"有事件就递增"，这里已按限频算，见 12.8 |
| GetTask | 每个请求 0.3 次，读 OB | 假设 |
| 群 | K = 4，R = 3，每轮约 30 秒，转发策略分两种：仅主发言者（每轮 1 条流）、全部成员（每轮 K 条流） | 假设 |
| 集群规模 | 每个边缘进程承载 1 万并发连接（调用方 SSE 加下游流）；每个 Worker 处理 3000 条消息/秒，监督 1 万条在途 dispatch；至少 3 个 | 由并发量推出 W 和 E，假设 |
| TBase 单分片 | 3 万 ops/s | 取自调研中"单实例几万 QPS"的下限，假设（V8） |


### 12.3 每个请求的成本
S1 单 Agent，TBase 控制面 ops（不含 token）：

| 环节 | ops |
| --- | --- |
| Gateway 受理（限流计数、读 evt 尾、写 inbox、写 wake） | 4 |
| Worker 处理创建消息 | 8.25 |
| Connector 下发与回报 | 9 |
| Worker 处理完成消息 | 8.25 |
| 会话清理 | 3 |
| **合计** | **32.5** |


token 路径（占大头）：每个转发的流，每次合并写 1 次 `XADD` 加 1 次 `XREAD`。20 秒输出，50 毫秒间隔是 400 批，**每个请求 800 ops**；200 毫秒间隔是 100 批，200 ops。所以 TBase 的压力主要由 token 流决定：50 毫秒时是控制面的 24.6 倍，200 毫秒时是 6.2 倍。

群（K = 4，R = 3）：

| 项 | 数值 | 相对 S1 |
| --- | --- | --- |
| 控制面 ops/请求 | 204.2 | 6.3 倍 |
| inbox 消息/请求 | 13（1 条创建加 12 条成员结果） | 6.5 倍 |
| 并发 dispatch | 每请求 12 个、各约 30 秒 | 12 倍 |
| token（仅主发言者转发） | 3 条流，50 毫秒 2400 ops | 3 倍 |
| token（全部成员转发） | 12 条流，50 毫秒 9600 ops | 12 倍 |
| OB 同步写/请求 | 4（每轮 1 次加终态 1 次） | S1 是 1 |


大群敏感性（K = 10，R = 5）：每请求控制面约 786 ops，是 S1 的 24 倍；λ = 100 时并发 dispatch 15 万，W 约 15、E 约 17。

### 12.4 周期性负载和集群规模
周期性负载按两类算，一类跟并发量成正比，一类跟集群规模有关：

| 周期任务 | 公式 | 说明 |
| --- | --- | --- |
| `progress_seq` 写 | 并发 dispatch × 0.73/s | 流式 1/s，空闲 0.2/s，已按限频算 |
| Worker 监督读 `disp:` | 并发 dispatch × 1/s | 设计是每秒一次 |
| 兜底扫描 `active` | 并发会话 × 0.1/s | 设计是每 10 秒一次 |
| 心跳写 | 2 × (W + E)/s | 两个 key，每秒一次，量很小 |
| **Worker 互相检查** | **4 × W²/s** | 设计：每个 Worker 每 500 毫秒检查所有分片所有者的两个心跳 key，随 W 平方增长 |
| **Connector 存活检查** | **2 × W × min(E, 并发/W)/s** | 设计：Worker 每秒检查其 dispatch 所在 Connector 的两个心跳 key，随 W × E 增长 |


每个并发 dispatch 的周期成本约 1.83 ops/s（进度写 0.73、监督读 1.0、扫描 0.1），跟集群规模无关。**真正随集群规模平方增长的是后两项**：S1 的 10 档和 100 档（W = 3、E = 3）只有几十 ops/s，可以忽略；100000 档（W = 300、E = 600）两项各约 36 万 ops/s；群 100000 档（W = 3600、E = 4500）分别达到 5180 万和 3240 万 ops/s。

### 12.5 结果
**S1 单 Agent**

| 入口请求/s | 并发 Task | W / E | TBase ops/s（50ms 原始） | TBase ops/s（200ms 加周期优化） | 所需 TBase 分片（50ms / 200ms） | token 带宽（50ms / 200ms） | TBase 内存（50ms / 200ms） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 300 | 3 / 3 | 8.9k | 2.6k | 1 / 1 | 2.0 MB/s / 0.5 MB/s | 50 MB / 28 MB |
| 100 | 3.0k | 3 / 3 | 88.8k | 25.5k | 3 / 1 | 20 MB/s / 5 MB/s | 501 MB / 276 MB |
| 10000 | 300k | 30 / 60 | 8.9M | 2.5M | 297 / 85 | 2.0 GB/s / 0.5 GB/s | 50 GB / 28 GB |
| 100000 | 3.0M | 300 / 600 | 89.5M | 25.5M | 2983 / 849 | 20 GB/s / 5 GB/s | 501 GB / 276 GB |


**群（K = 4，R = 3，仅主发言者转发流）**

| 入口请求/s | 并发 Task / dispatch | W / E | TBase ops/s（50ms 原始） | TBase ops/s（200ms 加周期优化） | 所需 TBase 分片（50ms / 200ms） | token 带宽（50ms / 200ms） | TBase 内存 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 900 / 3.6k | 3 / 3 | 32.4k | 10.6k | 2 / 1 | 6 MB/s / 1.5 MB/s | 110 MB |
| 100 | 9.0k / 36k | 4 / 5 | 323.8k | 106.0k | 11 / 4 | 60 MB/s / 15 MB/s | 1.1 GB |
| 10000 | 900k / 3.6M | 360 / 450 | 33.2M | 10.6M | 1108 / 354 | 6 GB/s / 1.5 GB/s | 110 GB |
| 100000 | 9.0M / 36M | 3600 / 4500 | 408M | 106M | 13599 / 3533 | 60 GB/s / 15 GB/s | 1.1 TB |


群（全部成员转发流）的 TBase ops/s：10 档约 104k（50ms）/ 29k（200ms），100 档约 1.0M / 286k，10000 档约 105M / 29M，100000 档约 1.1G / 286M。**转发策略是 4 倍的差别。**

周期性负载占比（S1，优化后）：10 档 277 ops/s（占总量约 11%），100 档 2.3k（约 9%），10000 档 224k（约 9%），100000 档 2.2M（约 9%）。优化前的周期项在 100000 档约 620 万，其中互检和 Connector 存活检查两项就占 72 万。

### 12.6 OB
OB 只有结果数据，**没有周期性负载，跟集群规模无关**，只跟入口请求量成正比：

| 入口请求/s | S1 写 TPS（原始 / 批量） | S1 读 QPS | 群写 TPS（原始 / 批量） | 群读 QPS | 群消息和事件行/s |
| --- | --- | --- | --- | --- | --- |
| 10 | 20 / 11 | 23 | 50 / 41 | 53 | 190 |
| 100 | 200 / 113 | 230 | 500 / 413 | 530 | 1.9k |
| 10000 | 20k / 11.3k | 23k | 50k / 41.3k | 53k | 190k |
| 100000 | 200k / 113k | 230k | 500k / 413k | 530k | 1.9M |


计算依据：

| 项 | S1 | 群（R = 3） |
| --- | --- | --- |
| 同步写（在关键路径上） | 1（终态） | 4（每轮 1 次加终态） |
| 异步写 | 1（Task 创建，可批量） | 1（同左） |
| 读 | 2.3：创建时去重主键点查 1、同步写后读回 1、GetTask 0.3 | 5.3：去重 1、同步写读回 R + 1 = 4、GetTask 0.3 |


"批量"指 Task 创建按 20 条合并插入、事件按 50 条合并，终态和群每轮的同步写不合并（合并会增加终态延迟）。

对比：11-02 文档里旧方案每个 Turn 约 14 个 OB 事务，新方案 S1 每个请求 2 次写、群 5 次写，因为中间状态都在 TBase。10 档和 100 档下 OB 压力不大（最多几百 TPS）；到 10000 档，同步写就有 1 万（S1）到 4 万（群）TPS，加上异步写合计约 1.1 万到 4.1 万 TPS（批量后）或 2 万到 5 万 TPS（不批量），需要 OB 团队确认租户上限（V9）；100000 档需要分库分表和读缓存。

### 12.7 热点、突发和内存
**1. 单个 slot 的上限。** key 带 hash tag `{sN}`，同一个分片的所有 key（inbox、会话文档、disp、evt）落在同一个 slot，slot 不能再拆。假设单个 slot 持续不超过 5000 ops/s（一个物理分片放多个 slot 留余量），1024 个逻辑分片支持的入口请求上限约为：

| 场景 | 50ms 原始 | 200ms 加周期优化 |
| --- | --- | --- |
| S1 | 约 5700/s | 约 2 万/s |
| 群（仅主发言者） | 约 1500/s | 约 4800/s |
| 群（全部成员） | 约 490/s | 约 1800/s |


超过上限需要增加逻辑分片数，而分片数写进 key 和 sessionId 的编码，上线后很难改，要在一期定。

**2. 热点 key。**

| key | 压力 | 说明 |
| --- | --- | --- |
| 租户限流计数 | 单租户的 λ ops/s，打在同一个 key 上 | 100000 档就是 10 万 ops/s，会超过单个 key 的能力 |
| `wake:{sN}` | 每分片 2λ/1024，S1 100000 档约 195/s；群约 1.3k/s | 不是问题 |
| 热点群会话 | 同一个群的所有消息串行处理，每条 3 个 TBase 往返，约 3 毫秒，单会话上限约 300 条/秒 | 一个群同时被多个调用方并发调用、或大群一轮结束时 K 条结果同时到达（K = 10 时排队约 30 毫秒）会触及；可用同会话合批提交缓解 |


**3. 故障突发（S1 / 群，单节点故障）。**

| 故障 | 突发 ops | 备注 |
| --- | --- | --- |
| Worker 崩溃 | 10 档约 400 / 2.1k；10000 档约 40k / 17.5k | 接管者批量读文档、写 epoch、读 inbox，目标 1.5 秒内完成，约 1.2 万到 2.7 万 ops/s 集中在这个 Worker 持有的分片上 |
| Connector 崩溃 | 10000 档约 2 万 / 3.2 万 | 同时产生 5000 到 8000 次对下游 Agent 的续接调用，要与"防恢复风暴"的并发上限对齐，并对下游限速 |
| Gateway 崩溃 | 10000 档约 2.5 万 / 1 万 | 调用方重连，每个构建一次快照 |


因为 Worker 数随并发量增长（每个 Worker 约 1 万条 dispatch），单个 Worker 故障的突发是个与规模基本无关的常数（约 4 万 ops 以内）。

**4. 内存。** 占大头的是 evt 流：S1 每个会话约 55 KB（文档 3.5 KB、inbox 约 2 KB、evt 约 50 KB）里 evt 占 200 条 × 250 字节。另外会话结束后保留 60 秒，也计入内存。群的会话文档约 20 KB。**闲置群的会话文档一期保留在 TBase**（§5.6），这部分与入口请求量无关，只跟累计的群数有关：1 万个群约 200 MB，10 万个约 2 GB，100 万个约 20 GB。

### 12.8 发现与调整建议（待决策）
下面是评估发现的问题和建议，设计正文没有改。

| # | 发现 | 建议 | 效果 | 优先级 |
| --- | --- | --- | --- | --- |
| 1 | token 路径占 TBase 压力的绝大部分，50 毫秒间隔每个 S1 请求 800 ops，是控制面的 24.6 倍；条目 250 字节中大部分是元数据 | 合并间隔改为可配置，默认 200 毫秒或自适应（低速率时放大）；条目精简元数据 | S1 每请求 800 降到 200 ops；带宽按条目大小再降 2 到 4 倍 | 高 |
| 2 | 1024 个逻辑分片限制了规模：以 5000 ops/s/slot 计，S1 约 2 万/s 封顶，群约 1800/s 到 4800/s | 逻辑分片数做成配置项，一期就预留更大的值（如 8192）；分片数写入 key 编码前定下来 | 避免上线后迁移 | 高 |
| 3 | Worker 互检是 O(W²)：W = 300 时约 36 万 ops/s，W = 3600 时 5180 万 | 每个 Worker 只监视"自己是第一继任者"的分片的所有者 | 100000 档 36 万降到约 4.8k | 中（W 超过约 30 才显著） |
| 4 | Connector 存活检查是 O(W × E)：E = 600 时约 36 万 ops/s | 每个 Connector 由 3 个 Worker 监视，发现死亡后写一个 dead 标记；其他 Worker 每秒读一次 dead 集合 | 36 万降到约 3.9k | 中 |
| 5 | 设计里 `progress_seq`"有事件就递增"会使每个 token 批多一次写，把 token 写入翻倍；监督读每秒一次、兜底扫描每 10 秒一次开销也不小 | `progress_seq` 至多每 5 秒写一次；监督读每 2 秒一次；兜底扫描每 30 秒一次 | 每个并发 dispatch 的周期成本 1.83 降到 0.73 ops/s | 中 |
| 6 | 租户限流计数是单 key 热点 | Gateway 本地令牌桶，周期性与 TBase 同步 | 避免 10 万 ops/s 打到一个 key | 中（超过 1 万/s 才需要） |
| 7 | 每个创建请求要查一次 OB 做去重（主键点查，λ QPS），写完同步写还要读回 | 去重读可用 §10 第 3 条的 Task 标记（TBase）替代；读回只在重放路径执行 | OB 读 QPS 减半以上 | 低（10000 档以上才显著） |
| 8 | evt 保留占内存大头（每个会话 50 KB） | `MAXLEN` 200 降到 50，会话结束后保留 60 秒降到 15 秒 | 内存降约 4 到 5 倍 | 中 |
| 9 | 群的转发策略对 TBase 压力有 4 倍差别 | 默认仅主发言者的流转发给调用方，其余成员的输出在结束时合并进 artifact；做成群策略配置 | 群 token 压力降到 1/4 | 高（群场景） |
| 10 | 大群一轮结束时 K 条结果几乎同时到达，逐条提交 | 同一会话 100 毫秒窗口内合批提交 | 提交数从 K × R 降到约 R | 中 |


另外，Worker 和 Connector 故障后的恢复并发要设上限（12.7 第 3 点），这条已经在"防恢复风暴"里，只需要把上限值定下来。

### 12.9 局限
+ 所有参数是假设，尤其是 T_RUN、T_STREAM、条目大小、群的 K 和 R、单分片 3 万 ops/s。
+ 没有算 CPU：Worker 和边缘进程的单进程承载量也是假设，会影响 W、E，进而影响周期项。
+ TBase 的 `XREAD BLOCK` 并发连接数限制没算（V3、V8）：10000 档并发 30 万条流，每条一个阻塞连接不可行，必须按分片合并读取。
+ 10000 和 100000 档在单个 TBase 集群内很可能做不到，需要按"单元（cell）"拆成多个独立集群，这是架构层面的变化，不在本期设计内。
+ 现网统计没有拿到，未校正：S、f、各模式占比、t_run、转发策略。

## 附录：v2.7 修订记录
| # | 内容 | 修订 | 位置 |
| --- | --- | --- | --- |
| 1 | 缺少流量模型 | 新增第 12 章：S1 和群，入口请求 10 / 100 / 10000 / 100000 四档；TBase ops、带宽、内存、所需分片；OB TPS / QPS；周期性负载与集群规模；热点、突发和内存 | §12 |
| 2 | 评估发现的问题 | 10 项调整建议，设计正文未改，待决策 | §12.8、§10 第 5 条 |
| 3 | 容量相关的验证项 | 新增 V8（TBase 集群级上限、单 slot、阻塞连接）和 V9（OB 租户上限、同步写延迟） | §8 |


## 附录：v2.6 修订记录
| # | 内容 | 修订 | 位置 |
| --- | --- | --- | --- |
| 1 | 缺少 A2A 依赖清单 | 新增第 11 章：一期范围、作为服务端和客户端的依赖（含规范强度和用在哪里）、超出标准的部分、未验证的假设、最硬的三条依赖 | §11 |
| 2 | 一期只支持 A2A 1.0 | 没带版本或版本不是 1.0 的请求返回 `VersionNotSupportedError`；调用下游带 `A2A-Version: 1.0`；不做 0.3 适配 | §0、§5.3、§5.4、§11.1 |
| 3 | §5.3 把阻塞和非阻塞的 `SendMessage` 写成一样 | 拆成三行：流式、`return_immediately: true`、默认阻塞；阻塞模式等到终态或中断态再返回，并写明崩溃重试和最长等待时间 | §5.3 |
| 4 | reset 的 `append=false` 语义是假设 | 新增验证项 V7 | §8、§11.5 |
| 5 | 协议绑定未定 | 列为待确认项 | §10、§11.1 |
| 6 | 验收用例 | 增加 2 条：阻塞模式 Gateway 崩溃重试、版本检查 | §9 |


## 附录：v2.5 修订记录
| # | 内容 | 修订 | 位置 |
| --- | --- | --- | --- |
| 1 | 缺少机制总览 | 第 2 章新增「核心机制总览」：9 个机制、目标与机制的对应、依赖的未验证能力 | §2 |
| 2 | "所有权靠租约吗"没有答案 | 说明心跳只用来发现故障，所有权不依赖租约和时钟，正确性靠 epoch 栅栏 | §2 |


## 附录：v2.4 修订记录
| # | 内容 | 修订 | 位置 |
| --- | --- | --- | --- |
| 1 | 状态与事件清单 | 新增 4.3：TBase 状态、会话文档字段、Stream 事件、OB 结果、状态枚举与转移、读写时序、贯穿规则 | §4.3 |
| 2 | active 清单只增不减 | 定义登记条件和移出规则（先 SREM 再复查 inbox）；接管时不加载空闲会话 | §4.1、§5.2、§5.6 |
| 3 | wake_offset 的推进规则 | 移出 active 后，"先 SADD 再推进"会有丢唤醒的窗口，改为低水位：会话这一批处理完成后才推进 | §4.1、§5.2 |
| 4 | dispatch 两套状态字段 | `dispatches.status`（Worker，生命周期）与 `disp.status`（Connector，SENT / ACCEPTED / ENDED）分工，互不写对方；SENT 同时是调用下游前的预写记录 | §4.1、§4.3.5、§5.4 |
| 5 | 取消传播和 cmd 类型 | cmd 类型列全：dispatch、resume、probe、cancel；取消走 `cmd:a2a`，拿不到下游 taskId 时记下取消意图 | §4.1、§4.3.3、§5.6 |
| 6 | 毒会话隔离的持久化 | 会话文档增加 `status`（ACTIVE / QUARANTINED），隔离后的消息返回错误，人工解除 | §4.1、§4.3.5、§6 |
| 7 | 看门狗与等待输入 | Task 处于 INPUT_REQUIRED / AUTH_REQUIRED 时暂停计时 | §5.4、§6.1 |
| 8 | 新发现的待决项 | SUBMITTED 到 Worker 首次提交之间 `GetTask` 查不到记录，列入 §10 | §4.3.5、§10 |
| 9 | 验收用例 | 增加 3 条：空闲移出 active 时收到新消息并崩溃、取消时没有下游 taskId、毒会话 | §9 |


## 附录：v2.3 修订记录
| # | 内容 | 修订 | 位置 |
| --- | --- | --- | --- |
| 1 | 一期下游范围 | 一期下游只有 A2A；BCN 协议 bot 的 WS 入站路径（连接注册表、离线语义、seq/ack、`chat.inject`）整体后置，相关键、验证项 V5 和故障行一并移除 | §0、§2、§4.1、§5.4、§5.5、§5.6、§6、§8、§9 |
| 2 | 边缘层故障切换的目标 | 边缘层目标是服务端保证，对端何时重连不计入；第三方调用方只享有"重连后任意 Gateway 立即可续传" | §0、§6、§6.1 |
| 3 | 入口 VIP | 补充 VIP 的三种用法（故障摘除、发布摘流、回调域名）；新增验证项 V6 | §2、§6.1、§8 |
| 4 | Connector 假活 | 新增看门狗：Connector 递增 `progress_seq`，会话所有者 Worker 用自己的单调时钟判断停滞，另一个 Connector 用 `GetTask` 探测 | §4.1、§5.4、§6.1 |
| 5 | 新旧 Connector 同时写事件流 | 增量和 reset 带 `(dispatch_id, conn_epoch)`；Gateway 按 `latest_epoch` 过滤迟到的旧增量；新 Connector 接管时先写 reset | §4.1、§5.3、§5.4 |
| 6 | 边缘层优雅退出 | Gateway 先摘流、等宽限期；Connector 停止领指令、等 dispatch 进入 ACCEPTED；超时按故障路径处理；不再承诺边缘层"≤ 1 秒无感" | §6.1、§7 |
| 7 | 验收用例 | §9 增加 5 条边缘层用例 | §9 |


## 附录：v2.2 修订记录
| # | 评审问题 | 修订 | 位置 |
| --- | --- | --- | --- |
| 1 | 带 taskId、没带 contextId 时，不能用 messageId 推导新会话（例如对 input-required 的人工回答） | 判断顺序：先看 taskId，从 taskId 解出原会话并校验与 contextId 一致；其次 contextId；最后才推导新会话。Worker 校验 Task 属于该会话和调用方 | §3.2、§5.2、§5.3 |
| 2 | `dispatch_id + 事件类型` 不能作为所有 Connector 事件的去重键，同一 dispatch 可以多次 input-required | 完成结果按 dispatch 终态去重；中断类事件按交互 ID 去重（A2A 用 `status.message.messageId`，BCN bot 用事件 seq），dispatch 记录增加 `seen_interactions` | §3.1、§4.1、§5.2 |
| 3 | 文档收尾项 | 流式输出边界改为"故障后中间输出允许重置，由完整 final 覆盖"；一期群 Session 空闲只释放内存 Actor，休眠存档后置；RPO = 0 和 3~10 秒明确为待验收目标；"调用方没收到确认会重试"改为"客户端实现重试时沿用原 messageId"；最小验收用例补入 §9 | §0、§4.2、§5.2、§5.3、§5.5、§5.6、§9、§10 |


## 附录：v2.1 修订记录
| # | 评审问题 | 修订 | 位置 |
| --- | --- | --- | --- |
| 1 | Task 创建去重与后续事件处理混用；首次请求没带 contextId 时，不同 Gateway 会生成不同 Session 和 Task | 三层身份；没带 contextId 时由请求确定性推导 sessionId；"taskId 已存在就跳过"只用于创建请求，后续事件按事件身份或业务状态去重；不新增通用请求映射表 | §3、§4.1、§5.2 |
| 2 | wake 的恢复规则不清：新会话还不在 active 中，接管时会漏掉已确认的唤醒 | `shard:<i>` 记录 `wake_offset`；会话进入 active 后才推进；接管时合并 active 和未处理的 wake；wake 按 MINID 截断 | §4.1、§5.1、§5.2、§5.3 |
| 3 | 提交后的断点：最终结果未写入 evt；Connector 取到指令后、建 disp 之前崩溃；先写 OB 再提交时的重放 | 会话文档新增 `pending_publish`（只放终态和中断态）；Worker 监督新增"指令 3 秒无人领取就用同一 dispatch ID 重投"；补充"重放必须采用 OB 中已保存的结果"的实现约束；推送通知仍在 P1 | §5.2、§5.4、§6 |
| 4 | B 级恢复"ListTasks 查不到就安全重发"不成立 | 只有能对应原请求时才续接，否则按已有的 `onAmbiguous` 处理 | §5.5 |

