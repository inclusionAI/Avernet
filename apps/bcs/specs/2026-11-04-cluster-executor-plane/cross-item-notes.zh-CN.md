# 集群执行器平面 —— 给兄弟项的备注

[English](cross-item-notes.md) · 属于[集群执行器平面设计](design.zh-CN.md)

记录在此以免遗漏;这些都不由第 1 项实现。

**第 2 项(连接与转发)**

- 把每个 Bot 事件中继给会话的**当前**所有者,逐事件查找(提示 + `NOT_OWNER`),
  绝不在整个运行期间缓存。
- socket 的排空状态;一个由 Bot 插件支持的"请重连"控制帧(类似 HTTP/2 的 `GOAWAY`)。
- 集群范围的 Bot 可达性("X 连在哪里?"),取代今天进程本地的 `is_available`。
- 为"Bot 重连中"增加一个投递等待状态(有界等待),取代今天在启动宽限期之后直接
  失败的行为。
- 当 Bot 重连时,唤醒那些有排队投递的所有者(经对等通道),把轮询时延挡在 TTFT
  之外。
- 注册表可以放在 Redis 中(软状态)。
- 各插件在重连时的行为不同:DeepSeek Harness 插件在断开期间缓冲事件(最多 1,000 个
  事件 / 16 MiB),重连后刷出;在 OpenClaw 插件中没有找到等价的缓冲(未做端到端
  验证)。
- 跟踪 issue [inclusionAI/Avernet#3506](https://github.com/inclusionAI/Avernet/issues/3506)
  (提醒所有者)已被 D6 的转发取代([设计 §9](design.zh-CN.md#9-把一轮请求路由到其所有者d6))。

**第 3 项(执行正确性)**

- `bcs_message_deliveries` 已经具备文档 03 派发记录的大部分内容(`run_id`、
  `idempotency_key`、`attempt_no`、每次尝试的 `request_id`、`may_have_been_sent`、
  一个绝不自动重发的 `unknown` 状态、CAS 版本)。请明确决定是把它扩展到状态机流程,
  还是新建 `bcs_dispatch_records`,以免出现两本含义重叠的账。
- 用流式或 `return_immediately = true` 派发有副作用的步骤,以便立即记录 task id;
  要求有副作用的步骤必须以 Task 应答(Message 应答之后没有任何可查询的东西)。
- 可考虑令 A2A `messageId` = 确定性的 `idem_key`,这样按 `messageId` 去重的普通
  Agent(规范中的 MAY)能免费受益。
- 派发记录必须记录应答类型;已记录的 Message 即为终态。
- 去重保留窗口的公式中包含"租约 TTL";在 D5 下它就是认领阈值 —— 应从同一份配置
  推算。
- 所有派发记录的写都使用围栏构造器。

**第 4 项(BCN A2A profile)**

- 明确规定:关闭连接不会取消 task。
- 要求 Agent 把进度流式写入 task 的 artifacts,使继任者重新订阅时不会留下 token
  空档。
- task 与已见键集合(seen-set)的保留下限。

**第 5 项(面向 PA 的接口)**

- BCN 对 `SendMessage` 一律以 Task 应答;调用方使用 `return_immediately = true` 或
  流式(阻塞调用会在整个运行期间保持连接打开)。
- 要求 PA **在重试时复用 `messageId`**;在共享存储中按
  `(principal, session_id, messageId)` 原子去重。没有稳定的客户端键,这一跳就不可能
  做到恰好一次,而下游去重也帮不上忙(重复的一轮会成为带新键的新运行)。
- 把花钱的步骤设计成对服务器签发的提案的批准("批准 P-123"),这天然是幂等的。
- 定义一轮请求在运行进行中到达时的含义(作为该运行的输入、排队,还是以"忙"拒绝)。
- 传输协议(gRPC、JSON-RPC、REST、SOFA):一套与协议无关的去重实现;薄适配层负责
  提取键与调用方身份、对规范化后的请求做哈希以检测冲突,并把结果映射为各协议原生的
  错误。框架层的重试(gRPC hedging、SOFA failover)使并发与跨节点的重复成为常态。
  SOFA 是需要我们自行定义的自定义绑定。
- "恰好一次"的措辞:*每一跳都是至少一次投递;对经 profile 验证的 Agent,在去重保留
  窗口内副作用实际上只发生一次;无法确定的结果会被上报,绝不猜测。*
