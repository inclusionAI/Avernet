# BCS 消息拥塞控制设计

- **日期：** 2026-09-04
- **修订日期：** 2026-09-07
- **状态：** 待评审（Draft for Review）
- **范围：** 单实例 BCS 的消息准入、Bot 限流、有序投递、上下文暂存、后端状态/取消能力与 IM 提示；Workbench UI 后置

## 决策摘要

- 本版只面向一个 BCS 进程、一个调度循环；允许多个异步发送任务，不允许两个调度进程同时运行。
- 纳入队列的逻辑消息只在 `bcs_messages` 中保存一次；每个受管“消息 × 目标 Bot”对应一条
  `bcs_message_deliveries`，各目标独立排队、失败和取消。
- 投递类型复用现有 `DeliveryType::Send/Inject`，数据库和 API 使用 `send/inject`。
- 同一个 Bot/session 的受管 delivery 在可信终态前最多有一个 active `send`；关闭类型的
  原路径流量不在该限制内，不能宣称混合流量下 Bot 的全部请求都被串行化。
- `inject` 先存住，按消息因果顺序绑定到下一条 `send`，随一次 `chat.send` 发送；
  不依赖引擎 `chat.inject`。
- 受管消息在写入正文时同步将附件写入 `bcs_messages.content.attachments`，与 delivery 同事务提交；
  实际发送统一从消息表读取附件，不依赖入站请求内存。IM 同样保存入站附件及其已有 URL，
  首版不处理排队期间的 URL 过期、刷新或文件转存。
- 首版不实现 context 数量/字节限制或全局积压上限，不新增相应统计字段；inject 总积压治理、
  上下文压缩及摘要后续单独评审，不在首版静默截断或压缩消息。
- MySQL/OceanBase 是生产事实源，SQLite 用于本地单实例；不引入外部 MQ 或 Redis。
- 调度归属和计数使用可重建的进程内状态；不引入 Worker lease、续租、分布式 fencing 或领导者选举。
- 首版新增的核心业务表只有 delivery 表；不引入 outbox 表、通知扫描或重放任务，
  也不新增发送 attempt 表、Abort attempt 表或持久化 lane 表。
- delivery 是投递事实源；首版提供后端快照查询、取消和提交后尽力发布的状态事件，
  并实现 IM 简化提示。Workbench 排队 UI 后续独立实现，不属于首版交付或验收。
  外部 IM 提示允许漏发。ChatRun 查询需与 delivery 对齐，业务完成回调单独确认恢复边界。
- 只有确认未发出的投递才能安全自动重试；逻辑 run ID 保持不变，每次发送调用使用新 request ID。
  Unknown 不自动重发，已终止后的重新执行必须使用新 run ID。
- 排队取消直接进入 `cancelled`；active 取消复用现有 `BotDeliveryPort::abort`。
  取消请求走普通后台异步任务，不经过消息 FIFO，不新增独立 Abort Worker 框架。
- 单目标拥塞只拒绝该目标，不回滚其他目标。排队和取消状态可通过后端接口查询，
  IM 按简化策略提示，不以 Workbench 已接入完整状态展示作为首版前提。
- 默认关闭；Bot 的 `off/enforce` 与全局业务类型开关共同决定新请求是否入队，首版不做
  shadow `observe`、优先级调度或复杂熔断。
- Direct A2A 和 state-machine 当前尚未接入 `bcs_messages`，对应类型保持关闭并沿用原路径；
  消息持久化/事务整合另行完成后，可分别开启或关闭队列，不阻塞其他已就绪类型上线。
- 业务类型开关使用 `flow_kind`，不与 `kind=send/inject` 混用。已经入队的 delivery 始终由
  调度器管理，关开关不删除积压，也不将已有请求改为直接发送。
- 本次仅通过 Draft PR 提交设计文档供评审，不实现运行时代码；方案评审后再单独推进实现。

## 评审重点

- 当前部署是否确实满足单进程约束，启动和升级能否保证没有调度进程重叠。
- 是否接受“终态前不释放 lane”和“Unknown 暂停等待确认”的安全/可用性取舍。
- message、delivery、ChatRun 的边界，以及单消息多目标的关系是否清楚。
- 单调度循环、数据库事务和状态条件更新，是否足以处理发送、取消与回包的异步竞态。
- 不保存完整 attempt 历史，是否满足首版排障要求。
- `inject` 的因果绑定、发送前撤销后的解绑、可能已发送后的禁止重放是否符合群聊语义。
- 附件是否在入队时完整落库、发送时统一读库；是否接受首版保存 IM 原附件 URL、
  但不保证链接在排队后仍有效，以及附件 URL 的存储和对外展示边界。
- 现有 `chat.abort` 的 scope、Provider `PENDING` 限制和取消确认规则是否完整保留。
- 后端状态/取消契约、多 Bot 聚合和 IM 简化提示及通知失败策略是否合理；
  Workbench UI 明确后置，是否仍混入了前端实现或页面验收要求。
- 是否接受首版无 outbox、外部 IM 状态提示可能漏发；ChatRun 查询校准及业务回调的
  既有恢复/人工处理边界是否明确。
- Provider bypass header TODO 是否阻塞对应 Bot 的生产启用。
- 按业务类型灰度时，是否接受限流/顺序只约束受管请求；关闭类型共享 Bot/Session 时的
  上下文边界和 Provider scope Abort 风险是否已明确。
- Direct A2A/state-machine 未就绪时拒绝启用、类型停用前 drain，以及已入队请求的所有权是否明确。

## 1. 问题与目标

当前 BCS 缺少统一的消息流控制入口：繁忙 Bot 可能同时接收过多请求，同一会话的请求可能
并行或乱序，群聊上下文依赖引擎 inject 行为，用户也无法区分“正在排队”和“没有收到消息”。

首版完成以下能力：

1. 对已启用业务类型，按目标 Bot 共享最大受管并发、发送速率和待发送 send 数量上限。
2. 同一个 Bot/session 的受管消息严格按序执行，不同 session 之间可以并行。
3. `inject` 在 BCS 持久化，在下一条合适的 `send` 中统一交给 Bot。
4. 排队状态查询、用户主动撤销发送，以及已启动运行的取消。
5. BCS 重启后恢复未发送队列，不盲目重复执行可能已经发出的请求。
6. 为已接入类型提供明确的等待、失败和取消状态查询及事件契约，供 CLI/后续 Workbench 接入；
   实现 IM 简化状态提示，不承诺故障或重启后的补发。首版不实现 Workbench 排队 UI。
   Direct A2A/state-machine 接入后复用同一套后端能力。

本版不做通用分布式任务平台。实现重点是“一张持久化投递表 + 一个进程内调度循环”，
并保留数据库与外部 Bot 之间不可省略的发送边界。

## 2. 设计决策

### 2.1 单实例前提

“单实例”指同一份生产队列只能有一个 BCS 调度进程，不只是部署在同一台机器：

- 一个进程中有一个负责状态决策的调度循环。
- 网络发送、Abort 和通知可以并发执行，但只把结果返回调度循环，不直接决定下一条消息何时发送。
- 服务管理器使用先停旧进程、确认退出、再启动新进程的升级方式；首版不支持重叠式滚动升级。
- 同机部署使用配置指定路径上的操作系统独占文件锁，随进程退出释放；锁获取失败则启动失败。
  该锁不解决跨机器竞争，不能把它当作分布式锁。
- 新进程恢复数据库状态完成前，不接受新的调度准入，也不发送新请求。
- 如果未来需要多副本或跨机接管，必须重新评审调度所有权和 fencing，不能直接增加副本数。

这个前提使 Worker ID、lease token、租约续期和持久化 claim 不再是首版必需项。

### 2.2 message、delivery 与 ChatRun

`bcs_messages` 保存 canonical 消息正文、发送者、会话和消息顺序，不保存队列状态。

`bcs_message_deliveries` 保存目标 Bot 的工作流：投递类型、状态、上下文绑定、运行身份、
最近一次发送结果和取消意图。它是已接入“业务类型 × 目标 Bot”请求的投递事实源。

ChatRun 保留现有客户端异步运行、流式结果、long-poll、TTL 和 owner 权限职责，通过
`message_id/delivery_id` 关联投递。取消和终态由 delivery 驱动，不能只将 ChatRun
标记为 Cancelled 而让 Bot 继续执行。非终态 delivery 仍存在时，关联恢复信息不能被 TTL 清理。
以上关联与校准仅适用于已经入队的 ChatRun。当前未接入的 Direct A2A 保留现有 ChatRun
行为，不为开启群聊队列而强制迁移，也不伪造缺失的 message_id/delivery_id。
ChatRun 的执行状态不依赖一次可能丢失的投影通知；查询和 long-poll 返回前需按关联 delivery
校准，具体版本和恢复规则见 13.3。回复内容从已落库的 canonical 回复及既有历史关联读取。

现有 `BotRunContextPort` 继续供 run 查询和 Abort 使用，但已接入调度器的 run context
必须可由 delivery 重建；它是索引，不再独立决定 lane 是否释放。
关闭类型的原路径 run context 仍由既有实现维护。scope 查询需要合并两类已知 active run，
按 canonical run ID 去重，并明确每个 run 的状态由哪条路径负责。

### 2.3 投递类型

| 类型 | BCS 行为 | 下游调用 |
| --- | --- | --- |
| `send` | 入队，等待容量和顺序条件，启动一次运行 | 一次 `chat.send`，包含绑定的上下文 |
| `inject` | 暂存，绑定到后续 `send`，不创建独立 run | 不独立调用；随承载它的 `chat.send` 发送 |

沿用 `bcs_domain::DeliveryType::Send/Inject`，不新建 Invoke/Observe 等同义类型。
被绑定的 `inject` 不改成 `send`，也不创建第二条 delivery。引擎 `chat.inject`
协议本身不在本文中重新定义。

### 2.4 投递保证与 run ID

网络超时不代表 Bot 没收到消息。例如 Bot 已经开始执行，但 ACK 丢失；此时重发可能执行
两次，换一个 run ID 也只能区分回包，不能消除重复执行。

| 场景 | 首版处理 | ID 规则 |
| --- | --- | --- |
| 确认未发出且允许重试 | 回到 queued，延迟后再投递 | 原 delivery/run ID/幂等键不变，request ID 更新 |
| 可能已经接收，结果不明 | unknown，保留 lane 和容量，不自动重发 | 不通过复用或更换 ID 绕过 Unknown |
| 运行已终止，用户要求重新执行 | 新请求重新准入 | 新 message、delivery、run ID 和幂等键 |

稳定的 `run_id` 和名为 `idempotency_key` 的字段不等于下游已经支持幂等。
首版不依赖第三方去重来保证调度正确性，不承诺端到端 exactly-once。
新请求仍必须遵守原 lane 的顺序，不能绕过尚未收敛的旧运行。

### 2.5 首版保留与移除

| 保留 | 移除或后置 |
| --- | --- |
| 持久化 delivery、发送前落库、条件更新 | Worker 租约、分布式 fencing、领导者选举 |
| 单进程内 lane/容量索引，启动时重建 | 独立 lane 表、Bot 高频计数表、跨节点连接路由 |
| 最近一次 request ID、发送结果和取消上下文 | 独立发送/Abort attempt 表及完整 attempt 审计 |
| 简单公平轮转、最小发送间隔 | 优先级、aging、可突发 GCRA、多级熔断 |
| 取消后台任务、现有 abort port | 独立 Abort Control Worker 服务及任务框架 |
| 提交后尽力通知、查询恢复展示、现有来源信息 | outbox 表、通知扫描重放、可靠异步回调框架 |
| 启动恢复、截止时间扫描、明确告警 | 自动隔离引擎、自动接管运行、无证据 force release |

状态的 `state_version` 仍保留，用于异步竞态和客户端去重；它不是 Worker 所有权凭证。

### 2.6 按业务类型开启队列

使用本设计中的业务来源字段 `flow_kind` 作为开关维度：

| flow_kind | 业务范围 | 首版接入要求 |
| --- | --- | --- |
| `group` | 群聊输入和群聊回复转发 | 首批候选，核对消息落库与准入事务后显式开启 |
| `direct_a2a` | Direct A2A 对话 | 当前未接入 bcs_messages，默认关闭；迁移完成后单独验收/开启 |
| `task` | task 流程中真正发给 Bot 的业务消息 | 核对 canonical 消息与完成回调；未就绪则关闭 |
| `system` | 需要 Bot 处理或接收上下文的系统业务消息 | 核对 canonical 消息与准入；不包括内部控制事件 |
| `state_machine` | state-machine 产生的 Bot 业务输入 | 当前未接入 bcs_messages，默认关闭；接入完成后单独验收/开启 |

channel/HTTP/WebSocket 是入口渠道，不另设业务类型；按其真实业务归入上述类型。
`flow_kind` 由服务端业务入口确定并传递，不能由客户端任意指定，也不能在共用的群聊/发送
辅助函数中丢失原类型，导致 Direct A2A 或 state-machine 被误判成 group。
同一业务类型的 send 和 inject 一起受开关控制，首版不增加二者各自的开关。

新请求的队列选择规则为：

~~~text
queue_managed = (bot.mode == enforce) AND flow_enabled[flow_kind]
~~~

类型开关全局生效，Bot 开关仍用于逐 Bot 灰度；首版不再增加每 Bot 的类型覆盖矩阵。
全部类型默认 false，未配置项也按 false。开启前必须验证该类型的 canonical 消息、同事务
delivery 创建、附件落库与发送读库、事件关联和取消接入已就绪；能力未就绪时拒绝启用，
不静默退回直发。附件 URL 续期/转存不属于首版就绪条件。
就绪是代码/契约验收结果，不增加一个可由用户强行置真的 ready 配置项。

关闭类型的请求保持原消息/ChatRun/发送路径，不要求先写 bcs_messages，不创建占位消息或
无 source_message_id 的 delivery，也不显示排队。后续接入这两个类型不属于本次实现前置条件。

混合流量期间，下文的 active 数、max_running、发送速率、队列上限和 FIFO 均指受管 delivery：

- 关闭类型的直发请求不计入这些额度，Bot 实际总并发可能超过 max_running。
- 不同已开启类型共享原 Bot/session lane 和 Bot 容量，不能把 flow_kind 加到 lane key
  或为各类型复制一份 max_running，从而绕过总体受管限额。
- 关闭类型的消息/context 不自动进入本队列的 inject 绑定，也不保证与队列消息的相对顺序。
  如要求同一会话所有输入有序，必须接入所有会影响该会话的路径，或隔离其 Bot/Session。
- scope Abort 仍按真实作用域处理，不能因为类型关闭就漏掉原路径 active run；精确 delivery
  cancel 的 Provider 限制见 11.5。

## 3. 消息与投递基数

已同时开启业务类型和 Bot 开关的目标使用如下模型：

~~~text
bcs_messages: M1
  ├─ D1 -> Bot A -> send
  ├─ D2 -> Bot B -> inject
  └─ D3 -> Bot C -> send
~~~

- M1 只有一份正文。D1/D2/D3 是独立投递工作流，不是三份重复消息。
- A、C 各有自己的运行身份、等待顺序和执行结果；B 只有上下文绑定，没有独立 run。
- A、B、C 的回复分别成为新的 canonical 消息。
- 每个“消息 × 目标 Bot”最多一条 delivery。路由结果冲突时 `send` 优先于 `inject`。
- 如果给不同目标的业务正文确实不同，应创建不同逻辑消息；delivery 投影不能隐藏正文差异。
- 取消某个 delivery 不删除原消息，不影响其他目标。取消整条消息的投递也按目标返回结果。
- Recall/delete 只对尚未发送的 delivery 直接取消；已发送请求仍需要真实终止确认。

灰度期间，类型关闭或 Bot 为 off 的目标保持原路径，不回填历史 delivery；上述完整关系只
适用于已经入队的消息。未接入的 Direct A2A/state-machine 可以仍然没有 canonical 消息记录。
同一消息同时包含已接入和未接入目标时，API 必须标明未跟踪目标，不能把它显示成已完成。

## 4. 中间件与架构

### 4.1 数据库和进程内工具

- 生产沿用 MySQL/OceanBase；本地使用 SQLite，测试另有 Memory repository。
- 数据库保存受管消息、delivery 及其取消/运行恢复信息，不保存状态通知任务。
- 进程内使用有界异步 channel 传递调度命令/结果，并使用定时器轮询兜底。
- 不引入 Kafka、RabbitMQ、Redis Stream 或 Redis 锁。
- 不另起调度服务；调度循环和异步发送任务运行在现有 BCS 进程中。

有界 channel 只是唤醒和进程内通信，不是消息存储。只有数据库提交成功，才返回已接收。
入口繁忙而尚未持久化时返回明确临时错误，不把消息留在内存中假装已入队。

### 4.2 最小组件划分

~~~text
消息入口 / 用户取消 / Bot 事件
             ↓
    调度应用服务（单状态循环）
       ↙                  ↘
delivery repository     异步网络任务
       ↓                  ↓
       数据库        BotDeliveryPort
                          ↓
                   WebSocket / Provider
~~~

应用服务负责准入、调度、取消和结果协调；Core 负责纯状态规则，repository 负责事务。
HTTP/WS adapter 只做协议转换，不能绕过应用服务直接修改队列。具体组件由 Bootstrap 装配。

异步网络任务不拥有调度策略。它执行一次现有 port 调用，将带 request ID 的结果送回循环。
单个网络任务等待 Bot/Provider 时，调度循环仍能处理其他 session、取消和终态事件。
已有 `BotDeliveryPort::abort` 继续负责 transport 协议，不新增另一套 Abort 实现。

首版可以在现有 message-flow 内组织调度模块；是否拆 crate 按现有依赖边界决定，不作为功能前置。

### 4.3 内存索引与写入顺序

内存只维护可从数据库重建的数据：

- Bot 的 active 数、queued 数，以及每个 session 的 active delivery。
- 各 lane 的队首候选、简单轮转位置。
- 本进程正在准备、发送或取消的任务句柄。
- run ID/下游 alias 到 delivery 的反向索引。
- Bot 下一次允许发送时间。

lane key 为 `(env, target_bot_id, canonical_session_id)`，只是索引键，不新增 lane 实体或表。
所有影响调度的写操作都进入同一个循环：先提交数据库，再更新内存，再唤醒后继任务。
事务失败或提交结果无法确认时，暂停新发送并重新读取数据库；不能仅凭内存回滚猜测结果。

同一循环串行执行短状态事务，不在事务内等待网络、Bot final、附件下载或外部鉴权。
可能较慢的准备工作先在有界任务中完成，回到循环后再次检查状态和当前安全权限。

## 5. 投递状态机

### 5.1 send 状态

~~~text
queued -> dispatching -> running -> completed | failed

dispatching -> queued                （仅确认未发出且允许重试）
dispatching | running -> unknown
unknown -> running | completed | failed

queued -> failed | cancelled | expired | rejected_capacity

dispatching | running | unknown -> cancelling
cancelling -> cancelled | completed | failed | cancel_unknown
cancel_unknown -> cancelled | completed | failed
~~~

图中 `unknown -> running` 只接受能关联原运行的可信接收证据，不会触发重新发送。
可信 final/error/aborted 可以先于 ACK 到达，直接结束对应非终态；例如 dispatching 也能直接
completed。调度器不要求所有中间阶段按顺序观察到。

| 状态 | 含义 | 占用 active 容量/lane |
| --- | --- | --- |
| `queued` | 已落库，等待调度；包含离线、限流和安全重试退避 | 不占 active 容量；仍按队首顺序阻止后继越过 |
| `dispatching` | 发送前记录已提交，网络请求可能已经发出 | 是 |
| `running` | 可信 ACK、delta 或运行事件证明下游已接收 | 是 |
| `unknown` | 无法确认原请求是否接收或仍在运行 | 是 |
| `cancelling` | 已持久化取消意图，等待发送协调和终止确认 | 是 |
| `cancel_unknown` | 取消结果无法确认，仍可能运行 | 是 |
| `completed` | 已可信完成 | 否 |
| `failed` | 明确失败或已确认隔离结束 | 否 |
| `cancelled` | 发送前已撤销，或发送后已确认取消 | 否 |
| `expired` | 尚未发送的请求超过队列 TTL | 否 |
| `rejected_capacity` | 准入时该目标 Bot 的 send 排队数量已达上限 | 否 |

取消中的请求若被证明根本没发出，可以直接 cancelled；不需要下游 Abort。
业务 terminal 不回退，迟到结果不得把已取消或已完成的 delivery 重新打开。

首版不保存 `claimed`、`submitted`、`retry_wait` 状态：

- 准备/选择在内存中管理，未提交 send-start 前，数据库仍是 queued。
- 写出但未确认接收保持 dispatching，可通过 `submitted_at_ms` 表达观测进度。
- 安全重试回到 queued，使用 `available_at_ms` 和 `wait_reason=retry_backoff`。
- 不引入 dead-letter 队列；超过安全重试次数而未发出的请求明确 failed。
- 发送前准备发现明确且不可重试的错误时，queued 可直接 failed，不必先进入 dispatching。

### 5.2 inject 状态

~~~text
pending_context -> bound -> consumed
pending_context -> cancelled | expired
bound -> pending_context   （承载 send 被证明未发出且永久结束）
~~~

`inject` 不占 Bot active 容量，也不参与 send 的队列位置或 max_queued 计数。
首版不设独立的 context 数量/字节上限，不因 Bot 的 send 队列已满而拒绝 inject。
已可能发送的 bound context 不再解绑给下一条 send。其承载 send 终止时统一 consumed。

### 5.3 等待原因与终态竞争

queued 的等待原因使用有限集合：
`prior_message_running | bot_capacity | rate_limited | bot_offline | retry_backoff | paused`。

单进程仍然有取消、ACK、final 和发送 future 的异步竞争。统一使用
`delivery_id + expected_state_version + allowed_status` 做条件迁移；发送结果另校验
`request_id`。受影响行数不符合预期时读取最新状态，不能无条件覆盖。

匹配原运行的可信 terminal 优先于迟到的 transport timeout/Submitted。
lane/容量只在第一次成功终态事务后释放一次；失败的条件更新不触发重复释放。

## 6. 持久化数据模型

### 6.1 bcs_messages

沿用现有消息模型，并保证发送者作用域内的 client message ID 去重和 session sequence 有序。
推荐数据库约束：

~~~text
UNIQUE(env, sender_id, client_msg_id)
UNIQUE(env, session_id, session_seq)
~~~

实际列名和身份作用域按现有 message contract 对齐，不改变 Direct A2A 的消息迁移范围。
重复 client ID 返回原消息和已有目标结果，不再次路由入队。不同 sender 不共享幂等作用域。

附件持久化规则：

- 对所有已开启类型的受管消息，入站携带的合法附件列表必须写入 `content.attachments`，
  保留原顺序；正文仍放在既有 `content.text`，mentions 等已有内容字段不得被覆盖丢失。
  无附件的纯文本消息继续兼容现有 string content，不强制改写全部历史数据。
- 复用既有 Attachment 字段：attachment_id、type、file_name，以及入站已有的 mime_type、
  size、sha256、url、expires_at。可选字段保持原有可选语义，不新增附件字节统计要求。
  这是可供发送读取的附件数据，不是完整 HTTP 请求、transport frame 或文件二进制。
- IM 与其他入口使用同一存储位置，先保留入站附件及实际发送所需的已有 URL；
  不要求先转存文件，也不新增来源解析、URL 刷新或续期机制。持久化的是附件描述和链接，
  并不意味着外部文件已被 BCS 备份或链接将一直有效。
- 附件只随 canonical 消息保存一份；delivery 使用 source_message_id 引用消息，不复制附件
  列表或 URL。inject 同样保存在其原始消息中，绑定 send 时只记录引用关系。
- 入队成功意味着正文、附件与目标 delivery 已在同一事务中提交。不能先提交纯文本消息/
  delivery，再异步补附件；附件写入失败必须回滚准入，不得返回已排队。
- 幂等重入读取原消息及附件，不用后来的入站请求覆盖原附件快照；历史缺失附件不凭空补造。
  本版不做历史消息附件补录，也不把关闭类型的历史请求重新入队。

当前 `Attachment::stable_metadata()` 会去掉 url/expires_at，不能直接当作本版队列附件的
完整持久化结果。实现时需明确区分发送所需的持久化映射与历史/事件的对外展示投影，
对应 contract 和兼容测试见第 13 节；本设计不宣称当前代码已经支持完整附件读库发送。

附件 URL 可能包含临时访问凭证，按私有消息数据控制访问，不写入日志或队列状态通知，
不直接将存储 JSON 广播给全部参与者。不额外收集或持久化登录 token、Cookie、Provider header；
已有 URL 按附件字段保存，不拆出其中参数作为新的凭据字段。本规则不放宽第 17.3 节的 header 限制。

### 6.2 bcs_message_deliveries

首版字段按职责分组，不保存重复正文、附件列表或完整 transport frame：

| 分组 | 字段及语义 |
| --- | --- |
| 身份 | delivery_id、env、source_message_id、target_bot_id、session_id、group_id、source_session_seq |
| 路由语义 | kind=send/inject、flow_kind、必要的版本化 semantic_projection_json |
| 工作流 | status、state_version、wait_reason、available_at_ms、expire_at_ms、created_at_ms、updated_at_ms |
| 运行关联 | canonical run_id、idempotency_key、必要的 ChatRun/completion reference |
| 最近一次发送 | attempt_no、request_id、send_started_at_ms、submitted_at_ms、accepted_at_ms、last_send_result |
| 下游定位 | transport_owner_kind、provider_id/provider_bot_ref、downstream_session_key、下游 run alias 集合 |
| 执行恢复 | run_deadline_at_ms、terminal_at_ms、发送时的 connection identity |
| 取消 | cancel_requested_at_ms/by/reason、abort_request_id、abort_started_at_ms、cancel_deadline_at_ms、last_abort_result |
| 上下文 | bound_to_delivery_id；仅 inject 使用 |
| 诊断 | last_error_code、脱敏摘要、必要的未发出判定依据 |

语义约束：

- flow_kind 固定为服务端确定的 group/direct_a2a/task/system/state_machine；准入后不可改写。
  开关决定是否创建新 delivery，不作为读取、恢复或结算既有 delivery 的过滤条件。
- send 的 canonical run ID/幂等键在本次运行内稳定，inject 的运行字段为 NULL。
- attempt_no 仅记录累计发送次数；request_id 是最近一次调用身份，不是 lease token。
- 新的安全重试可以覆盖“最近一次发送”字段，不保存完整 attempt 历史；旧结果根据旧 request ID 丢弃。
- 原始 transport owner、下游 session key 和已确认 alias 不能随当前 Bot 配置变化而改写。
- connection identity 只用于避免向不明的新连接发送旧 Abort，不是跨节点所有权/fencing。
- alias 集合是已有协议返回的实际运行标识，不创建新 run；从该集合重建内存反向索引。
  不同 delivery 的 alias 冲突应暂停相关投递并告警，不覆盖旧映射。
- semantic projection 只保存目标级路由意图、mention 处理、固定参与者语义等必要内容，
  不复制 canonical 正文或附件；目标附件可见范围沿用原 delivery 语义，不新增通用
  interceptor artifact 管理框架。

唯一键/索引至少覆盖：

~~~text
UNIQUE(env, source_message_id, target_bot_id)
UNIQUE(env, run_id)
UNIQUE(env, idempotency_key)
UNIQUE(env, request_id)

INDEX(env, status, available_at_ms)
INDEX(env, target_bot_id, session_id, source_session_seq)
INDEX(env, bound_to_delivery_id)
INDEX(env, source_message_id)
~~~

NULL 的唯一键行为必须在实际数据库实现中验证。下游 alias 先存有界集合并重建内存索引，
首版不新增 run-alias 映射表；如果现有协议需要无界或多代 alias，应作为实现前的兼容性问题处理。

不再创建 `bcs_message_delivery_attempts`、`bcs_message_abort_attempts`、
`bcs_message_abort_attempt_deliveries`、`bcs_bot_session_delivery_lanes`、
`bcs_bot_delivery_state`。Bot 低频策略优先放在统一配置和既有 Bot 配置扩展中。

### 6.3 无 outbox 的后续处理与来源信息

首版不创建 outbox 表，不持久化通知任务、通知发送标记或补发游标，也不增加通知扫描/重放任务。
单实例仍可能在“数据库已提交、通知尚未发送”之间崩溃；本版通过明确可靠性边界接受这个窗口，
不通过内存队列宣称通知已经可靠保存。

| 后续动作 | 首版处理与故障边界 |
| --- | --- |
| delivery 状态查询/事件 | 提交后尽力发布 WebSocket 事件；HTTP 提供权威快照，Workbench 查询恢复展示后续实现 |
| 外部 IM 排队/异常状态提示 | 尽力发送，允许失败或重启时漏发，不承诺补发 |
| ChatRun 执行状态 | delivery 为事实源；正常路径直接更新投影，查询/启动时校准，不依赖通知送达 |
| 推进 task/state-machine 等业务的完成回调 | 复用现有业务恢复机制；没有恢复能力时明确需要人工核实/恢复，不盲目重放 |

通知失败不回滚已提交的消息，不阻塞 lane。ChatRun 校准不是重新执行 Bot，也不是重新发出
业务完成回调。驱动流程下一步的回调不能按“可丢 UI 提示”处理，具体边界见 10.5。

Bot 回复正文、目标 delivery 和 inject 绑定仍在终态事务中一起写入，不改为异步补写；
不另建完整 terminal inbox。正常 Bot 回复的外部渠道投递沿用现有消息交付机制，其可靠性
不因“状态提示尽力发送”而被重新定义。

消息原始 channel binding、conversation、source IM message 等引用继续优先沿用现有来源数据；
不足时只补最小来源元数据。来源信息决定“向哪里回复/提示”，不因删除 outbox 而删除。
不存 token/Provider header，不将来源信息复制到每个目标 delivery。
IM 附件按第 6.1 节直接进入 content.attachments，不为首版增加外部文件恢复/转存所需的来源模型。

### 6.4 事务与清理

Repository 必须提供真正的原子事务：消息与目标 delivery 准入、send-start、取消、
terminal 与其 canonical 回复、目标准入及 context 变更，在各自操作内共同提交或共同回滚。
状态通知与这些事务分离，只在提交成功后发送；不能因通知失败重新执行事务或重发 Bot 请求。

状态条件更新失败时整个相关事务回滚，不允许“UPDATE 影响 0 行但后续释放容量/插入回复成功”。
若现有 DB port 无法表达这个保证，只做实现这些事务必需的最小 contract 扩展及 conformance
测试，不建设多步骤 claim/fencing 通用框架。

只要存在非终态 delivery 或未消费 context，相关消息与运行定位信息就不能物理清理。
终态记录保留时间由现有历史/审计策略决定；新运行不回收旧 run ID，迟到事件不得按 session
兜底绑定到当前运行。

## 7. 准入事务

只有业务类型开关开启、目标 Bot 为 enforce 的 send/inject 才进入统一准入；其他请求沿用
既有路径。统一的是已纳入队列的业务消息，不要求所有系统事件或尚未迁移的类型先写消息表。
已经接入的入口复用现有 canonical message 模型及写入逻辑，不再创建第二份消息。
接入需要统一新消息与 delivery 的提交边界：不能先独立提交新消息，再异步补入 delivery，
否则后续消息可能抢先发送。Direct A2A/state-machine 的消息接入另行完成，完成前本版只保持
其开关关闭；未来启用时必须验证这个事务边界，而不只是“表里已经有消息”。
已存在的消息只用于幂等重入/读取原投递；用户重发历史消息时创建新的逻辑消息。

流程：

1. 沿用现有鉴权、消息大小限制和路由；服务端确定 flow_kind、目标及 send/inject 类型。
2. 按类型开关与 Bot 开关决定目标路径。对受管目标，将准入命令送入有界调度入口；未接收
   的命令返回明确临时错误。类型/Bot 切换中的受影响新请求返回临时错误，不跳过 drain 直发。
3. 调度循环检查 client ID 是否重复，并准备固定的目标语义投影及附件持久化数据。
4. 在同一事务中写入/关联 canonical message 和 session sequence；新消息的正文、mentions
   及完整合法附件列表一并写入 content，其中附件位于 content.attachments，不能只保存正文。
5. 对 send 目标检查该 Bot 的 max_queued；inject 不执行此检查，不增加 context 或全局积压检查。
6. 可接收的 send 插入 queued；inject 插入 pending_context；send 队列已满的目标插入 rejected_capacity。
7. 对本条准入成功的 send，原子绑定本 lane 中尚未绑定且序号更小的有效 inject，见第 9 节。
8. 提交包含附件的消息、目标 delivery 和 context 绑定事务；任一写入失败整次回滚。
9. 更新内存索引，返回 message ID、逐目标 delivery 结果，再唤醒调度；提交后尽力发布状态通知，
   不等待通知成功才返回已接收。

受管路径如果因缺少 canonical 数据、事务能力或运行关联而失败，必须返回明确错误；
不能在已经请求开启队列的情况下 silently fallback 到原发送路径。原路径也不能为方便而
调用 admission 后再自行发送，导致同一请求同时进入两条路径。
存在旧 delivery 的幂等重入按已保存归属处理，不能因当前类型开关关闭就将其再次直发。

Bot B 满了不回滚 A、C；但数据库故障导致事务失败时，整次准入失败，不能部分声称已持久化。
只有全部目标都被拒绝时才返回 all-rejected，仍保留 canonical message 和拒绝记录；
若有已接收的 inject 目标，按逐目标结果展示，不能把整条消息误报为全部拒绝。

Bot queue cap 统计尚未发送的 queued send；active 数独立限制。准备中的请求数据库仍 queued，
仍计入 cap。inject 不挤占 send queue cap，继续沿用既有单条消息校验和本方案的过期/撤销规则。
本版不保证 inject 总积压量或整个数据库的队列规模有界。

## 8. 调度与限流

### 8.1 一个调度循环

调度循环由新消息、Bot 上线、网络结果、终态、取消和定时 tick 唤醒。
它只做短事务和状态决策，不等待完整 Bot 运行。

每次从 Bot 的非空 session 队列中简单轮转，每个 lane 只考虑最小 source_session_seq
的非终态 send。后面的消息不能越过退避中的队首。

满足以下条件才开始准备发送：

- 该 delivery 已经通过队列准入，所属范围没有被暂停或安全撤销；类型开关只用于新准入，
  drain 中的旧 delivery 仍按原调度策略处理，不能因关开关直接消失。
- Bot 可连接，且该 lane 没有 active send。
- Bot active 数小于 max_running。
- 已满足最小发送间隔和 available_at_ms，消息未过期。
- 本进程的网络/准备任务并发未达到全局上限。

同一 delivery 在内存中最多一个准备任务。准备完成回到调度循环后重新检查所有条件：
它可能已经被取消、过期、撤权，或者容量已被其他 session 占用。
只有 queued -> dispatching 的数据库事务成功后才占用容量并授权一次网络调用。

### 8.2 简单限流

首版只实现：

- `max_running`：每个 Bot 的最大 active send 数。
- `min_send_interval_ms`：同一 Bot 两次 send-start 授权之间的最小间隔。
  这是 BCS 本地调度速率，不承诺经过网络缓冲后 Bot 实际接收时刻仍严格等间隔。
- `max_queued`：每个 Bot 的未发送 send 数量上限，不包含 inject。

首版不设置 context 数量/字节预算或全局积压上限，不新增字节统计字段、预算计数或配套配置。
既有单条消息大小校验和下游协议的载荷限制继续生效，但不作为新的积压配额；组合载荷无法
发送时按第 9、10 节明确失败，不自动裁剪。inject 总积压治理及压缩后置，见第 17.2 节。
有界调度入口和准备/发送任务并发限制仍保留，它们限制进程内即时工作量，不是持久化积压上限。

不支持额外 burst、优先级或 aging。不同 Bot/session 使用轮转避免单个繁忙会话持续抢占。

每次授权发送都会推进 Bot 的 next_send_at；确认未发出也不返还该次速率额度。
ACK 不释放 active 容量，只有 terminal 或确认未发出回到 queued 才释放。
Unknown 和取消未确认状态继续计入 active，因此可能占满 Bot 全局容量，这是明确的可用性代价。

速率计时使用进程内单调时钟；重启后保守等待一个 min_send_interval 再允许首发。
不持久化 GCRA/TAT，不承诺跨重启保留突发额度。

### 8.3 离线、退避和过期

- 尚未发送时 Bot 离线：保持 queued，reason=bot_offline，不创建发送调用、不占 active 容量。
- queued 到期：原子变为 expired；不能把已发送请求的 queue TTL 当作执行已结束。
- 确认未发出的瞬时失败：回到 queued，设置固定的有界退避时间和次数上限，不增加 retry_wait 状态。
- running 超时走取消/Unknown 处理，不能变成 queued 自动重跑。
- 人工 pause 只停止新 dispatch；查询、terminal ingestion 和 Abort 继续工作。

## 9. inject 到 send 的绑定

### 9.1 因果顺序

~~~text
M1 inject(B), M2 send(B), M3 inject(B), M4 send(B)
绑定结果：M1 -> M2，M3 -> M4
~~~

在 send 准入事务中，绑定同一个 Bot/session 下满足以下条件的 inject：

- status=pending_context，未被其他 send 绑定。
- source_session_seq 小于本条 send。
- 没有被取消、recall、删除或过期。

M3 即使在 M2 尚未物理发送时到达，也不能加入 M2。绑定时只保存消息引用；真正发送时
按 sequence 读取内容，生成一条包含历史上下文和当前请求的 `chat.send`。
不能在 materialization 时临时捞取“截至现在的所有上下文”。

为维持原消息顺序，send 准入时的绑定不得越过更早的、尚未完成准入的消息；所有入口必须
复用同一 session sequence 和准入排序规则，不能先提交后序 send 再补写前序 inject。
这里的入口只指已开启类型的受管消息。关闭类型沿用旧 inject 行为时，其上下文可能已直接
到达引擎，不回填为 pending_context；类型切换后也不得把它作为新 context 再发送一次。

### 9.2 消费、取消和限制

- 安全自动重试保留同一组绑定，不把 context 又交给下一条 send。
- 承载 send 被证明未发出且永久取消、过期或失败：有效 inject 解绑为 pending_context；
  已过期/撤销的 context 结束为相应终态，不重新入队。
- 解绑后如果已有更晚的 queued send，在同一状态事务中将有效 context 绑定给最早且尚未
  首次 send-start 的合适后继；没有后继则保持 pending_context。
  不能只等待下一次新准入，导致已经排队的下一条 send 漏掉这些上下文。
  后继绑定变化时递增其 state_version；旧版本的准备结果丢弃并重新物化，不能发送旧载荷。
- 承载 send 已可能发出：context 留在 bound，不向其他 send 重放；承载 send 终态时 consumed。
- active run 期间新到的 inject 留给下一条 send，不调用引擎 inject。
- bound context 的独立 TTL 不在运行中拆开已发送载荷；是否能进入载荷在绑定和首次发送前检查。
- 被用户撤销的 bound inject，在承载 send 尚未首次 send-start 时可原子移除绑定并更新投影。
  一旦开始过发送，不支持“从 Bot 上下文中撤回一部分”；返回已可能发送，不承诺下游撤销。
- 已开始投递的 context 集合不因安全重试重新选取；若必须撤销，应先取消整个承载 send。
- 不对 inject 总数或总字节数做配额拒绝。既有单条消息校验照常执行；若绑定后组合载荷
  超过下游已知限制，明确报告承载 send 失败，不静默截断、丢弃或自动压缩上下文。
  发送前已确定的不可重试准备失败不能一直留在 queued 或自动重试；有效 context 按上述
  “确认未发出且永久失败”规则保留和解绑，原始消息不删除。

首版不自动总结或压缩 context，不引入跨 session 上下文重排。
绑定 inject 的附件从其原始消息的 content.attachments 读取，按消息 sequence 和消息内附件
顺序组合。保留原 inject 对该目标的附件可见性规则，不能因承载请求最终使用 chat.send，
就将原本不允许该目标访问的文件一并发送。首版保留已有附件 URL，不实现排队期间的刷新。

## 10. 发送、ACK 与终态处理

### 10.1 发送前落库

准备任务通过 source_message_id 读取 bcs_messages，正文与附件均使用已持久化的数据；
附件读取 content.attachments，绑定 inject 则读取其各自原始消息，再应用固定路由语义及
目标附件可见性规则，构造协议载荷。首次发送、排队后发送、安全重试和重启恢复统一走这条
读库路径，不再使用原 cmd.attachments、channel 入站对象或请求内存补齐附件。

对于本来就没有附件的消息，字段缺省表示无附件；已有附件字段解析失败或消息读取失败，
必须明确返回准备错误，不能通过过滤坏条目、使用空列表或只发正文来伪装成功。
本版按第 6.1 节取出保存的 URL 参与发送，不新增针对排队时长的 URL 有效性预检、自动重签、
刷新或转存，也不复用历史展示中“附件补全失败仍返回无 URL”的容错逻辑。
下游实际报告的附件错误仍按既有可信错误处理；不因附件错误绕过发送结果判定或自动重跑 Bot。

沿用既有校验及下游已知的载荷限制，不为此新增持久化 context 字节统计。如果准备阶段已经
确定组合载荷过大等不可重试错误，回到调度循环校验状态/版本后直接提交 failed，按第 9.2 节
处理 context；不创建本次发送调用，也不提交 send-start。只有下游才知道的限制仍按其可信
错误处理，不能把已经尝试发送的请求当作“确认未发出”。
准备结果回到调度循环后：

1. 再次确认 delivery 仍 queued、未取消/过期，目标仍可访问，lane/容量/速率允许。
2. 创建新 request_id，递增 attempt_no，固化当前 transport owner、原始 session key 和
   已知的 downstream run ID；下游协议规定可预先确定的 alias 也在此时记录。
3. 在同一事务中将 queued 改为 dispatching，记录 send_started_at 和运行 deadline。
   最近发送结果先保守标为 unknown；事务提交本身不是网络写出证明。
4. 提交成功后更新内存 active/lane 索引，并启动一次网络调用。
5. 网络任务把结果送回调度循环，携带原 delivery_id/request_id。

只有完成第 3 步才能调用下游。因此“queued + 无待执行旧网络任务”是可安全恢复的未发送状态。
已经提交 dispatching、但还没来得及真正发送就崩溃，也保守进入 Unknown；首版接受这种少量
误保守情况，不增加发送日志协调协议。

### 10.2 最小传输结果

应用层不能继续只依赖 delivered 布尔值，至少需要区分：

~~~text
DefinitelyNotSent { retryable, reason }
Submitted { downstream_ref }
Accepted { downstream_ref }
Unknown { reason }
~~~

- DefinitelyNotSent：能够证明未交给下游且没有残留发送任务；按第 11 节决定重试、失败或取消。
- Submitted：交给本地连接队列或 Provider，但不能证明 Bot 接收；保留 dispatching，记录 submitted_at。
- Accepted：协议规定的可信接收证据；进入 running，仍等待 terminal。
- Unknown：发送可能发生，但无法确认结果；进入 unknown。
- Bot 业务 error 或可信终态拒绝走 terminal，不作为发送自动重试依据。

HTTP 2xx 的意义取决于对应 Provider contract，不能一概当作 Bot 已运行。
adapter/SDK 不得在调度器不知情时自动重发 `chat.send`。
这些结果属于 application/port 契约，HTTP 状态码解释和 WS frame 转换留在 adapter。

### 10.3 回包关联和快终态

- transport result 通过 request_id 关联最近一次发送，旧 request 的迟到结果不得覆盖新 request。
- Bot 事件通过 canonical run ID 或已确认 alias、Bot、session 和原 transport owner 关联。
  不允许仅凭 session 将未知 run 绑定到“当前正在执行的消息”。
- 可信 final/error/aborted 可能先于发送 future 返回；先提交的 terminal 生效，后续结果 no-op。
- 同一 run 的重复终态只结算一次。错误 Bot、scope 或 alias 冲突事件不能修改 delivery。
- 回包先按 run ID 判断是否存在受管 delivery，有则交给调度器，无则沿用已知原路径 run 的
  处理；不能按“当前 flow_enabled 值”分流，否则关开关后旧队列 run 的终态会丢失。
- 若协议只在 ACK 中给出 alias，而事件先到，使用现有 adapter 的有界短暂缓冲等待关联；
  可重试的 Provider callback 在关联/持久化前不能 ACK。无法恢复的 WS 事件丢失按 Unknown
  处理并告警，不假装具备 durable terminal inbox 的恢复能力。
- 启用前必须测试真实 transport 的 run 关联路径，不能把未知 alias 直接丢弃后宣称运行完成。

进程重启后从 delivery 重建已知 run 索引。新连接不自动证明旧 run 仍存在；只有协议明确的
连续性证据或可信原运行事件，才能继续关联旧执行。

### 10.4 终态事务

应用层先准备 Bot 回复的规范化内容和纯路由结果，再由调度循环提交短事务：

1. 验证原 run、目标、当前非终态，使用 state_version 条件更新为 completed/failed/cancelled。
2. 根据是否可能已发送，消费或释放绑定 inject。
3. 如有正常 Bot 回复，同时写入唯一的 canonical 回复消息，并复用准入规则创建其目标 delivery。
   回复以原 run/输出身份去重，不因重复 final 再生成一条消息。
4. 提交终态、context、canonical 回复与目标 delivery 事务。
5. 提交后释放内存 lane/容量，唤醒后继消息；可重试的入站 Bot/Provider callback 此后才 ACK，
   不等待 WebSocket/IM 状态提示或业务完成回调成功。
6. 直接更新 ChatRun 投影，尽力发送状态提示，并沿用现有路径调用业务完成回调；这些动作
   不进入新增的持久化任务队列，失败边界分别按 10.5、12 和 13.3 处理。

Bot 回复的 sequence、目标 delivery 和 inject 绑定需要一起准入，不能先插入回复，
等后序消息已经开始执行后再异步补入更小 sequence 的 delivery。

如果回复路由准备明确失败，仍记录可信 terminal 和可展示的 canonical 回复，记录/通知路由失败，
不回滚 Bot 已完成这个事实，也不自动补入过期顺序位置的投递。用户可将其作为新消息重新转发。
数据库提交失败则不发布成功、不释放 lane，等待 callback 重试或恢复处理。

ChatRun 更新或状态通知失败不改变已经提交的 delivery terminal；查询可从 delivery 和已落库
回复恢复事实，不补发遗失的状态通知，也不通过重新处理 Bot final 来重做后续副作用。
`aborted` 不生成新的 Bot 运行请求；已有 partial 内容按现有 history 规则保存，不能再次触发
普通 final fan-out。HITL 未完成交互沿用现有终止失效规则。

### 10.5 业务完成回调的边界

“告诉用户已完成”和“驱动任务执行下一步”不是同一种动作。首版不增加可靠回调框架，
但接入 task/state-machine 等流程前必须逐项确认：

- 既有业务状态能否读取 delivery 的已提交终态，识别尚未处理的完成结果。
- 如果已有恢复机制，沿用其幂等键、状态条件和恢复入口；不另做终态 delivery 全量回调重放。
- 如果尚无恢复机制，本版明确允许崩溃后需要人工核实/恢复，不能宣称工作流会自动继续。
  人工处理前先核实下一步是否已执行，不能简单重复有副作用的调用。
- callback 超时或失败不撤销 delivery 的真实终态，不把原 Bot 请求重新入队；业务自身的
  “待处理/待恢复”状态与 delivery completed 分开表达。

涉及权限或安全的规则不能降为尽力通知。例如已结束 run 的 HITL 请求仍须按权威运行状态
拒绝过期操作，不依赖恰好成功的前端通知或异步失效回调。
本版不承诺一次提交后所有业务后续动作都已完成；需要可靠自动推进的流程必须先有已验证的
恢复能力，或后续单独评审 outbox/回调机制。

## 11. 重试、Unknown、恢复与取消

### 11.1 安全重试

首版只自动重试“同一次运行尚未发出去”的瞬时失败，条件必须同时成立：

- adapter 明确返回 DefinitelyNotSent 且允许重试。
- 该次异步发送调用已经结束，没有仍可能继续发送的队列项或任务。
- 所有此前尝试也都按此规则确认未发出，没有 Accepted、Submitted、Bot 事件或 Unknown 证据。
- 未被用户取消，未超过固定退避的重试次数/时间上限和消息 TTL。

典型例子是发送前发现连接失效。没有响应、响应字节数为 0、写入后断连和 ACK timeout
都不是未发送证明。Bot 已离线且尚未调度时直接 queued，不制造无意义的 attempt。

~~~text
delivery D1 / run R1 / idempotency key K1
  request Q1 / attempt 1 -> definitely_not_sent
  queued（retry_backoff）
  request Q2 / attempt 2 -> 等待 R1 的结果
~~~

重试保留 D1/R1/K1 和 context 集合，更新最近一次 request ID/结果字段。
不需要持久化每个 Worker 的身份，也不需要靠 lease token 判断这个过程。

Bot error、确认 abort 或其他 terminal 后不自动重跑。用户重新发送创建新的消息、delivery、
run ID 和幂等键，重新获得队列位置；不重新打开原终态，不重放已经可能消费的 inject。

### 11.2 进程启动与重启恢复

服务取得本机单实例锁后，先进入恢复阶段：

1. 禁止新准入和新 dispatch；恢复期间查询可返回已有快照及恢复标记。
2. 扫描全部非终态 delivery，加载其 Bot 策略、队列、context、原始 transport 和 run 索引，
   不按当前 flow_enabled/Bot 开关过滤；既有 delivery 就是曾被准入的事实。
3. queued 保持 queued；运行期间的内存准备任务随旧进程退出，不会再发出。
4. dispatching/running 先改为 unknown，保留原接收时间/发送证据；
   cancelling 或已发出 Abort 但未确认的记录改为 cancel_unknown。
5. 重建 active 数和 lane。unknown/cancel_unknown 仍占用；若发现同一 enforced lane 多个
   active，则暂停该 Bot 并告警，不挑一个丢掉。
6. 按 13.3 校准已关联的未终态 ChatRun，包括其 delivery 已经终态而投影尚未更新的情况；
   不自动重放业务完成回调，也不补建历史通知或 IM 排队提示定时器。
7. 只有状态和索引一致后才开放新准入及可安全 lane 的调度。

若当前配置已关闭某类型/Bot，但数据库仍有其积压，恢复后继续保留调度归属，并阻止对应
新请求直接绕过。按 14.3 继续 drain 或等待人工处理；不自动删除、迁移或重新发送旧记录。

新进程不续租旧 Worker，不领取旧 running work 重新发送。
Bot/Provider 在 BCS 重启后仍可能继续执行，所以“BCS 已重启”不能当成“Bot 已停止”。

单实例故障期间服务不可用，首版不承诺无损自动接管或透明继续流式响应。
queued 数据可恢复；外部执行结果可能需要迟到事件、已有状态查询能力或人工核实才能收敛。

### 11.3 Unknown、断连和运行截止时间

- 已发送请求遇到断连/超时：保留 lane/容量，展示结果不明。
- 只有原运行的可信 ACK/事件，才能把 unknown 恢复为 running 或终态。
- run deadline 到达：持久化取消意图，再通过后台任务尝试现有 Abort。
- Abort 无法确认：cancel_unknown，不自动释放，也不无限循环 Abort。
- 同 Bot 其他 lane 在剩余 active 容量内可继续；如果容量已被 Unknown 占满，则整个 Bot 等待。
- 终态记录或已验证停止旧 runtime 的证据可以用于人工恢复；操作需鉴权、记录操作者、
  原因和证据引用，并清理原运行关联后再释放。
- 本地暂停、修改数据库标志、重连或 timeout 都不构成旧执行已终止的证据。
  首版不提供“忽略风险直接 force release”按钮，也不自动重启/隔离外部 Bot。

已验证 runtime 隔离只说明旧执行不能继续影响该 session，不会撤销它此前已经产生的副作用。
无法取得证据时保持暂停，并明确告诉用户需要运维处理；这是首版已接受的边界。

### 11.4 排队取消和 active 取消

| 取消时的状态 | 行为 |
| --- | --- |
| queued，包括退避或正在内存准备 | 条件更新为 cancelled；不调用 chat.abort；准备结果返回时丢弃 |
| dispatching | 记录取消意图，进入 cancelling；等待本进程 send 调用收敛 |
| running / unknown | 进入 cancelling，按原 transport/run 尝试 Abort |
| cancelling | 合并正在进行的取消，不并发发出相同作用域的 Abort |
| cancel_unknown | 返回结果不明，保留 lane；不自动反复发送 |
| terminal | 已取消幂等返回；已完成/失败返回 too_late |

queued 取消事务同时处理 context 解绑；提交后更新排队计数、对齐 ChatRun 并尽力发布状态提示。
取消前已经开始网络调用的请求不能直接 cancelled。消息删除/recall 同样遵循这条规则。

取消权限沿用现有消息/session 权限：作者、被授权的管理员和 Direct A2A run owner，
不能因为知道 delivery ID 就拥有取消权；channel 身份必须映射回原始准入身份。

### 11.5 现有 chat.abort 兼容

协议核对基线仍为 2026-09-04 的 `origin/dev`（`6ecb426302`），权威协议文档为
`src/bcs/docs/chat-abort-protocol.md`，scoped Abort 由 `92d607ddc1` 引入。
这不是对当前远端最新提交的重新声明；实现前应核对届时 contract 及兼容测试。

保留以下语义：

- Workbench `chat.abort(group_id, session_id, bot_id)` 只选择 scope 内 active run，
  不取消 queued send，不删除 pending inject。
- legacy `{group_id, run_id}` 只用于解析 scope，不能把 scope Abort 收窄成单 run 取消。
- SessionBound 从 token 解析 scope，继续检查 subscription、scope mismatch 和当前 Human 权限；
  目标 Bot 可以为清理 stale run 而处于 Absent。
- WebSocket 对每个 active run 使用原始 session key 和精确 downstream run ID。
- Provider 对 Bot/Session 发一条 scope Abort，并按实际返回的 run ID 逐个确认。
- 成功、partial failure、not-supported 和 `run_ids` 兼容 alias 沿用当前 wire contract。
  timeout/disconnect 不是 Plugin 不支持 Abort 的证据。

类型关闭不改变 Workbench scope Abort 的选择范围。应用层合并 delivery 的受管运行和
既有 run context 中的原路径运行；WebSocket 按 run 调用，Provider 按 scope 合并一次调用，
结果分别交回对应的状态管理路径。原路径运行不会因为参加 Abort 就被补建 delivery。

“受管 lane 只有一个 active”不再足以推出“Provider scope 只有一个 run”：关闭的 Direct A2A
或 state-machine 可能仍从原路径进入同一 scope。
精确 delivery cancel 只有在能证明作用域独占且取消期间不会有其他原路径请求混入时，
才可复用 Provider scope Abort，例如已验证的 Session 隔离；一次查询中恰好只有一条 run
不能证明这一点。无法证明时返回 `exact_abort_not_supported`，不发送 scope Abort、不宣称
已接受精确取消，也不连带终止其他类型运行。
运行 deadline 触发的自动取消遵守同样限制：无法安全取消时保留占用并告警，不能扩大取消范围。
用户明确调用 scope `chat.abort` 仍按既有语义终止该 scope 的 active run，界面说明可能包含
关闭队列类型的原路径运行。未接入请求沿用原有取消 API，不新增其 delivery 精确取消 API。

### 11.6 取消后台任务如何执行

取消不需要独立任务表或 Abort Control Worker 框架：

1. 调度循环先在 delivery 持久化 cancel intent。
2. 如当前 send future 尚未结束，先等待其结果；不并发让 Provider Abort 跑在 send 之前。
3. send 证明未发出则直接 cancelled；否则保留原 run，准备现有 abort port 调用。
4. 在网络调用前记录 abort_request_id、abort_started_at 和 cancel deadline。
5. 启动一个有界的后台异步任务调用 `BotDeliveryPort::abort`，结果返回调度循环。
6. 只有经过 run/scope 校验的终止证据才能结束原 delivery。

这些任务不占普通 send 的 FIFO 或速率额度，使用单独的小型并发上限，避免被满额运行请求饿死。
同一 scope 的取消任务通过进程内 in-flight 集合去重；新进程不会继承该集合，但会读取
abort_started_at，避免将可能已发出的 Abort 自动重发。

取消意图已存、但 abort_started_at 为空：原 send 状态协调清楚后可以继续首次 Abort。
abort_started_at 已存而结果不明：恢复为 cancel_unknown，不能假定调用没发生。
请求方断开不会抹掉取消意图；首版不保证自动恢复丢失的取消确认。

Provider 当前只取消 RUNNING，不取消 PENDING。send future 已返回不一定代表 Provider 已进入
RUNNING；即使经过 send/abort 顺序屏障，空 Abort 结果仍不能证明已停止。
若之后得到可信接收/运行事件且仍有 cancel intent，可发起新的取消调用；这不是对丢失确认的盲重试。

### 11.7 Abort 结果收敛

- 匹配的 `aborted_run_ids` 或可信 `chat.event state=aborted`：通过统一 terminal 事务 cancelled。
- final/error 先到：保留 completed/failed，取消返回 too_late。
- 空 ID 列表、Provider `410 run_terminated`、普通 transport ACK：不能独立证明取消成功；
  本地无 terminal 时继续等待，截止后 cancel_unknown。
- 未知方法规范化为 `chat_abort_not_supported`，保持非终态和明确原因；
  不能误报 cancelled，未知或不支持能力的 Bot 不允许开启 enforce。
- scope 外 ID、错误 Bot、旧 run 的迟到 Abort response：不得结算当前运行。
- scope 部分成功时独立提交已确认终态，不回滚成功项，保留现有 partial-failure response。
- deadline 到达不自动变为 cancelled；重复终态不重复释放容量。

Direct A2A 后续接入并创建 delivery 后，queued 取消可以投影为 ChatRun Cancelled；active 必须等确认。
取消结果不明时 ChatRun 保持非终态并暴露状态，不只注销响应 channel。
当前 direct_a2a 关闭时保持既有路径；本版不提前实施其消息迁移或把已有 ChatRun 强制关联 delivery。

### 11.8 停机与数据库故障

优雅停机先停止新准入和 dispatch，在有界 drain 时间内继续接收终态和已有取消结果。
到期仍有 active 时保留数据库状态，由下次启动按 Unknown 恢复，不假装全部完成。

数据库不可用或提交结果不明时，停止新发送及未持久化的 Abort。
已经运行的外部 Bot 不会因此停止；恢复后重新读取事实，不能仅凭进程内结果释放 lane。
不可重试 WS terminal 在故障期间可能丢失，明确转入结果核实流程；不承诺数据库停机期间无损接收。

## 12. 后端状态能力与 IM 提示

首版交付后端状态查询、状态事件、取消能力和 IM 简化提示；不实现 Workbench 排队状态组件、
动画、输入框交互或页面轮询。后端兼容现有 Workbench 消息发送与 chat.abort，前端新功能
后续单独实现。本节的状态语义供后端契约和未来展示复用，不要求当前 Workbench 消费新字段。

### 12.1 后端对外状态语义

后端提供状态、等待原因和作用范围，不要求用户理解准备任务、request ID 或进程内部实现。
下表是状态含义和参考文案，不是首版 Workbench UI 实现清单，也不是 IM 逐状态通知清单：

| 持久化状态 | 对外含义 / 参考文案 |
| --- | --- |
| queued | 已排队，并附等待原因 |
| dispatching | 正在发送；未确认 Bot 处理 |
| running | Bot 正在处理 |
| pending_context / bound | 已保存为上下文，不单独等待回复 |
| consumed | 上下文已归入该次处理，不再重复投递；不承诺模型实际采用 |
| unknown | 处理状态暂时无法确认，后续请求已暂停 |
| cancelling | 正在停止 |
| cancel_unknown | 停止结果暂时无法确认，后续请求已暂停 |
| completed / failed / cancelled / expired / rejected_capacity | 已完成 / 失败 / 已取消 / 已过期 / 队列已满 |

后端状态须能区分暂停范围：默认是该 Bot/session；若 active 容量被不确定请求占满，才说明
整个 Bot 的受管请求需要等待。IM 提示沿用这个范围，不能扩大成整个群或所有 Bot 都暂停。

### 12.2 后端队列位置和多 Bot 聚合

队列位置按读取时的同 lane 前序非终态 send 数计算，包含 active，不包含 inject。
`ahead=0` 仍可能在等 Bot 全局容量或速率，后端不能因此把状态变成 running；离线/Unknown
保留实际等待原因，不计算虚假 ETA。位置提供有界的查询结果，超过范围可供后续前端显示
`100+`；不暴露前序发送者身份或正文，也不批量更新所有后继 delivery。

后续 Workbench 可基于逐目标状态展示，例如（首版不实现该 UI）：

~~~text
A 正在处理 · B 已排队（同一会话前面有 2 个请求） · C 已保存为上下文
~~~

后端按逐目标快照派生消息摘要，供查询和 IM 聚合使用，不另存一套聚合生命周期。
只以 send 判断是否等待回复，inject 单独返回其状态：

- 全部是 inject：context_only。
- 任一 unknown/cancel_unknown：attention_required，并保留其他目标的成功/等待状态。
- 任一 cancelling：cancel_requested；不能覆盖其他目标已经完成的事实。
- 仍有 dispatching/running 或部分已完成、部分排队：in_progress。
- 所有 send 都未发送：pending。
- 全部 send 终态：全成功 completed；有成功也有非成功 partial_failure；
  全取消 cancelled；其余 failed，附逐目标原因。
- 有因 Bot off 或业务类型关闭而未跟踪的目标：明确标为 untracked，不能据此推断整个消息已完成。

用户状态通知不写入 canonical chat，不路由给 Bot，不进入 inject context。

### 12.3 后端状态传递与查询恢复

admission response 返回初始快照；WebSocket 事件在事务提交后尽力发布，允许丢失。
HTTP 单条/批量查询提供最新权威快照，调用方不依赖事件必达，刷新、重连或事件丢失后都可
查询恢复；首版实现并验证这个后端能力，不实现 Workbench 页面的轮询任务。
admission response 丢失时，后端支持在原发送者作用域内按 client message ID 查询原准入结果；
同一 client ID 幂等重入不重复创建消息或运行，不能要求调用方生成新 ID 才能查明是否接收。
每条 delivery 使用自己的 state_version，事件和快照均携带 delivery ID/version，便于后续
客户端处理乱序/重复。消息摘要按当前快照派生，首版不增加独立 aggregate_version 生成器。

不持久化状态通知；event ID 可由 delivery ID/state_version 构成，重复事件仍按版本去重。
进程恢复后返回最新快照，不重放历史状态变化。位置属于即时提示，不因为前序
消息完成而给所有后续消息递增 version；位置与状态分别处理，状态只接受更高版本。

### 12.4 外部 IM 简化提示（首版实现）

由 BCS 的渠道通知逻辑生成提示并通过现有渠道能力发送，不要求改造 IM 客户端。
从原消息来源定位通知目标，不能把队列提示广播到所有群或所有 Bot：

- 需要回复的 send 超过约 2 秒仍 queued，发送一次“已收到，Bot 正忙，正在排队”。
- send 准入时目标已经离线，立即提示“消息已保留，Bot 恢复后处理”。
- 不逐条通知 dispatching/running 或排名变化；正常完成只发送正常回复。
- pending_context/bound/consumed 不单独发提示；全部目标都是 inject 时，不提示正在等待回复。
- send 最终失败/取消在本进程内合并为目标聚合后的异常提示，避免逐目标刷屏。
- unknown/cancel_unknown 提供必要的状态不明提示，说明受影响的 Bot/session；
  不冒充失败或已取消，不因状态持续不变而重复发送。
- 同一消息多个 Bot 的排队/异常提示尽量聚合，保留各目标差异，不把单目标等待说成全部失败。
- 已支持编辑卡片的渠道可复用既有卡片；其他渠道不持续发送进度，不新增 IM 客户端 UI 工程。
- 失败通知不得包含其他参与者消息、Provider 凭据或内部错误堆栈。

首版只为本次进程运行期间的新准入/状态变化创建内存定时器和发送任务，以
`message_id + notification_kind` 在进程内去重；状态提示不写数据库，也不建立补发队列。
启动恢复不为历史 queued/terminal 记录补建提示任务。发送前重新检查当前状态，避免任务已完成
却还发送过时的排队提示。

进程崩溃、发送失败或无幂等能力渠道的响应丢失，都可能导致提示漏发；只对可观测失败记录
日志/指标，不盲重发，也不承诺跨重启“最多一次”或通知 exactly-once。数据库快照仍可查询。
通知失败不回滚用户消息、不阻塞 Bot lane；正常 Bot 回复继续走既有消息交付路径。

### 12.5 Workbench 前端后续接入参考（本版不实现）

以下仅保留为后续独立前端工作的参考，不进入本版实施步骤、前端代码变更或验收门槛：

- 将第 12.1 节状态映射为 UI；queued 不显示 Bot typing，dispatching 显示发送进度，
  running 才显示处理动画。queued 可延迟约 300 ms 展示以避免闪烁，离线/失败/取消及时展示。
- 排队不禁用输入框；按 client message ID 合并 echo 和 admission response。响应丢失时
  显示“正在确认是否接收”并查询原结果，不使用新的 client message ID 盲重发。
- 等待非终态 delivery 时对当前可见/待处理消息进行低频、有界的批量查询；刷新/重连也查询。
  事件丢失不等于连接断开，不能仅靠重连触发恢复。查询失败采用有界退避，不扫描全部历史。
- 按 delivery ID/state_version 合并乱序事件和快照，展示逐 Bot 状态及聚合摘要，接入取消按钮。

后续前端任务自行实现并验收这些交互；本版不宣称现有 Workbench 已经具备排队展示能力。

## 13. 公共 API 与协议变更

### 13.1 应用接口边界

不再引入 claim/lease/Abort task 管理接口。按现有 application/core/port 分层增加最小能力：

~~~text
admit_message(command) -> message + per-target delivery outcomes
get_message_deliveries(message_id)
query_session_deliveries(session_id, message_ids)
cancel_delivery(delivery_id)
cancel_message_deliveries(message_id)

内部调度：
start_dispatch(delivery_id, expected_version)
record_send_result(delivery_id, request_id, result)
handle_run_event(run_identity, event)
handle_abort_result(delivery_id, abort_request_id, result)
restore_from_store()
~~~

可以组织在一个调度应用服务中，不必为每个动作新建一套 Service 类。
Core 定义状态决策，应用层调用业务 port；repository 封装事务，adapter 不直接调用 repository。

AdmissionOutcome 包含 canonical message ID、client ID、目标 Bot、delivery ID、kind、
状态/version、run ID、wait reason 和容量拒绝原因；inject 不返回独立 run ID。
同时返回服务端确定的 flow_kind。入口响应可增加可选的 `queue_managed` 及
`queue_disabled_reason=flow_disabled|bot_off`，说明为何当前请求未进入队列。
既有消息/run 的 queue_managed 按实际 delivery 关联返回，不因当前开关变化而改成 false。
未接入目标明确返回 untracked，不伪造 delivery 生命周期；尚未有 canonical 消息的原路径
继续返回原有 run/业务 ID，不伪造 message_id/delivery_id，也不能要求它调用基于 message ID
的 delivery 查询。开启配置但能力未就绪是启用失败，不是正常的 flow_disabled。

### 13.2 后端 HTTP 查询与状态事件

本节是首版后端交付，不包含 Workbench 组件、状态管理或事件订阅代码。保持现有客户端兼容，
由后续 Workbench 功能独立接入这些接口和事件；IM 使用第 12.4 节的服务端通知流程。

既有 send response 只增加可选的 message/delivery summary 字段，不删除旧字段。
新增 `message.delivery.updated` 事件，仅发送给声明 `message.delivery.v1` 的客户端。
发送者本人也要收到状态事件，即使普通 message echo 排除了其连接。

~~~json
{
  "type": "event",
  "event": "message.delivery.updated",
  "payload": {
    "schema_version": 1,
    "event_id": "evt_1",
    "message_id": "m_1",
    "session_id": "s_1",
    "delivery": {
      "delivery_id": "d_1",
      "target_bot_id": "bot_b",
      "kind": "send",
      "flow_kind": "group",
      "status": "queued",
      "state_version": 1,
      "wait_reason": "bot_capacity"
    }
  }
}
~~~

HTTP 计划增加：

~~~text
GET  /openapi/v1/collaboration/messages/{message_id}/deliveries
POST /openapi/v1/collaboration/sessions/{session_id}/message-deliveries/query
POST /messages/{message_id}/deliveries/{delivery_id}/cancel
POST /messages/{message_id}/deliveries/cancel
~~~

路径按现有 route 分组落地，不重构无关 API。批量查询最多 100 个 message ID，逐项检查权限。
history DTO 不强加必填 delivery 字段，兼容旧客户端。
按 client message ID 查询原准入结果优先复用既有消息查询入口；不足时只补最小查询能力，
保持发送者身份作用域和权限检查，不要求前端先实现才能测试后端契约。

全拒绝可返回 429，仍携带已落库的 message/delivery ID；部分成功返回逐目标结果。
queued 取消可同步返回 cancelled；active 取消返回当前 cancelling 快照，以 delivery ID
查询后续结果，不新增独立 durable cancel operation API。
message cancel 是逐目标动作，允许部分成功，不通过数据库回滚假装撤回已经发送的 Abort。
inject 的取消按第 9 节处理：pending_context 可直接取消，bound 要检查承载 send 是否仍允许撤回，
consumed 不承诺撤销 Bot 已收到的上下文。

### 13.3 chat.abort 与 ChatRun/CLI

Workbench `chat.abort` 继续使用已有 scope 请求和同步 response timeout。
对受管运行，请求进入应用服务后持久化取消意图，等待后台调用结果；原路径运行继续按现有
机制处理，最终合并成功/partial-failure，不能只返回当前开关开启类型的结果。
超时不等于已取消，scope 无 active 时保持既有空结果语义。
旧客户端仍能调用现有 Abort，不要求先支持新 delivery status capability。

以下 ChatRun 校准规则只适用于已存在 delivery 关联的运行；当前未接入 Direct A2A 保留原 contract。
后续 direct_a2a 类型开启后，ChatRun 增加可选 delivery 关联/摘要。取消、重要排队状态和终态
在正常路径直接更新投影；状态变化幂等推进版本，位置变化不单独推进 ChatRun 版本。
不要求 ChatRun 写入与 delivery 跨存储原子提交，但不能只依赖一次提交后的内存调用：

- 查询/long-poll 返回前，按 delivery 的最新执行状态和已落库回复校准投影。仅在投影实际变化
  时条件更新 ChatRun 并递增版本；重复查询不反复递增。校准失败返回临时错误，不返回已知过时的状态。
- long-poll 必须有界复查 delivery，不能只等待可能丢失的进程内唤醒。
- 启动恢复检查已关联的未终态 ChatRun，读取其 delivery 的当前状态；必须包含 delivery 已终态、
  ChatRun 仍 Pending/Running 的情况，不能仅遍历非终态 delivery。
- 校准只恢复状态、结果引用及客户端版本，不重新发送 Bot 请求，不重做业务完成回调或 IM 提示。

如果 ChatRun 自身 TTL/deadline 会提前结束或注销 active run，必须在接入时改为委托调度取消，
不能保留绕过 delivery 的本地“假终态”。

CLI detach 在持久化准入成功后返回；TTY 进度输出到 stderr，JSON stdout 仍只输出约定的一个结果。
Direct A2A/state-machine 的消息持久化整合和队列启用另行交付；当前只提供关闭类型的兼容分支
与启用校验，不以这两类迁移完成作为群聊队列的前置条件。

### 13.4 附件持久化与发送读取契约

- 消息准入契约明确：入站附件写入 content.attachments，与正文和 delivery 同事务提交；
  已开启类型的 HTTP/WS/channel 等子入口不能在转换过程中丢弃附件。
- message repository/store 需无损往返保存附件字段，发送应用服务直接读取存储消息，
  不从历史展示 DTO 获取可能被裁剪或重新签名的附件，也不依赖入站请求继续存活。
- 当前只保存 stable_metadata 的映射和相关测试需要按第 6.1 节更新；历史/消息事件等
  对外读取保持既有权限与安全投影，不能因存储增加 URL 就直接扩大 URL 的暴露范围。
- 不新增 delivery 附件副本、附件表或 URL 管理服务，不改变 Bot 附件 wire 字段；
  关闭类型保持原路径，无附件的旧 string/object content 继续可读。

所有变更需要对应 application/port contract、WS/Provider 协议兼容说明和 conformance tests。
不能凭空要求 Bot 返回 attempt_no，也不将 HTTP/WS DTO 下沉到 core。

## 14. 配置与灰度

### 14.1 Bot 开关与业务类型开关

保留 Bot 级 `mode=off|enforce`，新增全局业务类型布尔开关 `message_delivery.flow_enabled`。
新请求只有同时满足 Bot 为 enforce、对应类型为 true 才入队；首版不增加 Bot × 类型的 override
矩阵，不做 shadow observe。全部类型的默认值都是 false。

示例仅表示“group 已完成接入、其他类型尚未启用”；目标 Bot 仍需单独设置 enforce：

~~~toml
[message_delivery.flow_enabled]
group = true
direct_a2a = false
task = false
system = false
state_machine = false
~~~

- 当前 direct_a2a、state_machine 未接入 bcs_messages，必须保持 false。
- group/task/system 也不是默认自动开启，必须先验证本类型的全部业务子入口。
- 后续 direct_a2a 接入完成，可只把 direct_a2a 改为 true，不要求 state_machine 同时开启；
  反向亦然。任一类型可以独立停用，已有积压按 14.3 处理。
- 配置未知类型/拼写错误要报错，不归到 system；省略已知项按 false。
- 类型由服务端真实业务来源确定。客户端不得通过传入另一 flow_kind 绕过队列或触发尚未就绪类型。

启用必须同时满足：canonical message/session 可用、正文/附件与 delivery 原子准入、
发送可从消息表恢复附件字段、run 关联与取消/ChatRun（若适用）可对接、相关 contract tests
通过。IM URL 续期、可重新获取外部文件或提前转存不作为首版启用条件。
代码/部署尚不支持的类型被设置为 true 时，启动或配置更新明确返回 `queue_flow_not_ready`，
不静默忽略，不改走直发。
这些错误码是本方案拟新增的配置/应用错误，不宣称已有同名实现。

其余最小配置继续保留：

- max_running、min_send_interval_ms、max_queued：按 Bot 在所有已开启类型间共享。
- queue TTL、安全重试次数及固定退避。
- send/ACK timeout、run deadline、cancel deadline。
- 调度 tick、全局准备/发送任务上限、独立的小型 Abort 并发上限。
- 单实例锁路径、优雅停机 drain timeout。

所有启用值必须完整解析且校验合法；默认数值在实现压测后确认。
不增加 lease TTL、heartbeat、claim batch、priority、GCRA burst、circuit 或多节点配置。

### 14.2 按类型逐步启用

类型开关与 Bot mode 都是新请求准入策略，不是既有 delivery 的生命周期开关。
可以先开启 group 并选择少量 enforce Bot，而 Direct A2A/state-machine 继续使用原路径；
必须明确这不是全 Bot 流量限流，相关风险见 2.6 和 11.5。

每次启用一个类型，流程如下：

1. 验证该类型的持久化/事务/运行关联接入已完成，不等待其他关闭类型迁移。
2. 明确本次受影响的“类型 × enforce Bot”范围；短暂停止该范围的新请求，返回切换中的临时错误。
3. 等待该范围原路径在途请求结束或取得可信终止证据，确认不会再有旧路径调用晚到后发出。
4. 设置切换边界；旧消息、已有 ChatRun 和已直接 inject 的上下文不回填为新队列工作。
5. 应用类型开关，恢复该范围新请求，观察排队、限流、取消和重启。
6. 其他类型开关不联动；增加 enforce Bot 时，对该 Bot 当前开启类型执行相同切换检查。

如果其他关闭类型继续与受管消息共享 Bot/Session，不承诺它们之间有序，也不能凭空将
其 active 数纳入队列限额。部署若要求所有流量有序/限流，应先接入相关类型或隔离作用域，
不能仅将配置开关设为 true 来代替接入实现。

未知/不支持 Abort 的目标不能进入受管准入；首版优先用受控部署版本和现有 Provider 配置确认，
不为此强制增加 capability 协商协议。将来新增 capability 必须更新正式 contract。
能力降级时暂停相关新队列发送，保留已准入运行的查询和终态处理。
全量类型迁移完成前，保留明确的原路径分支；不能提前移除 Direct A2A/state-machine 所需路径。

### 14.3 类型停用、暂停与回退

紧急 pause 只停止新的受管 dispatch，不关闭 terminal、查询或 Abort。
降低 max_running 不主动终止已有受管运行，等占用降到新上限以内。

停用一个类型不能让其新直发请求越过旧队列；使用先 drain、后应用关闭配置的流程：

1. 暂停受影响“类型 × Bot”的新外部发起请求，其他类型继续工作；不要立即让该范围的新请求直发。
   已准入运行的回包、终态和正常回复目标准入仍按原受管路径完成；由此产生的积压也要处理完，
   不能为结束 drain 而丢掉回复或将它改为原路径发送。
2. 保持原调度所有权，处理或显式取消该范围的 queued、active 和未消费 inject。
   Unknown/cancel_unknown 必须先取得终止证据，不能因停用类型直接释放。
3. bound inject 可能被其他已开启类型的 send 携带；须等待其消费，或在允许的发送前边界
   处理绑定，不能因关闭来源类型就从已发送载荷中删除。其他类型可继续执行，不强制清空它们。
4. 该范围没有未完成 delivery/context 和旧路径在途任务后，应用 false，再允许后续新请求走原路径。
5. 关闭整个 Bot mode 时，对该 Bot 的全部受管类型执行同样流程。

直接热更新为 false 但仍有积压时，拒绝该次配置切换并保留原生效策略，给出待 drain 范围。
首版不新增持久化切换任务平台；可以由运维暂停新请求、等待 drain 后再次应用配置。
重启时即使文件配置已经 false，也必须恢复数据库中的既有 delivery，并阻止该范围的新直发，
直到完成同样的排空流程。不能按当前开关过滤掉旧队列记录。

对于本来就未启用且无积压的 Direct A2A/state-machine，关闭不需要 drain，立即保持原路径。
停用/再启用不重发历史消息、不更换既有 run ID，也不自动把原路径请求补入队列。

全局升级仍必须先停旧进程再启动新进程。数据库中尚有受管积压时，不能部署会忽略 delivery
表的旧代码；关类型开关不是这种代码回退的安全替代。

## 15. 可观测性与运维

首版只要求支撑问题定位的指标：

- 按 Bot 的受管 queued send/active send 数，最老排队时长，容量拒绝数；增加 flow_kind 分组
  和当前类型开关状态。原路径流量单独标识，不把受管 active 数展示为 Bot 的实际总运行数。
- 发送开始间隔、排队耗时、运行耗时。
- unknown/cancel_unknown 数、停留时间和受阻 lane 数。
- 安全重试次数、数据库失败、重复/无法关联事件、状态提示发送失败或结果不明。
- ChatRun 投影校准失败、业务完成回调的可观测失败和需要人工处理的流程。
- Abort 成功、超时、空结果和不支持次数。

日志关联使用 message_id、delivery_id、run_id、request_id、attempt_no、Bot/session，
以及 flow_kind、是否受管和 Abort request ID。原路径尚无 message/delivery ID 时使用其现有
业务标识，不伪造 ID。无需 Worker ID/lease token；日志不得记录完整 prompt、header 明文或凭据。

最小运维能力：

- 查询消息及目标状态、最近发送结果、等待原因。
- 暂停/恢复新调度，取消排队请求，调用现有 scope Abort。
- 查看“业务类型 × Bot”的生效开关、未就绪原因和停用前尚需 drain 的 delivery/context。
- 展示 Unknown 原因和需要人工核实的原运行定位信息。
- 有可靠终止/隔离证据时，执行受鉴权和审计的恢复；无证据时拒绝释放。
- 查询启动恢复和 ChatRun 校准失败；根据既有业务状态与 delivery 核实未推进流程。
  调度状态不一致时暂停并重建，不在线自动“猜测修复”计数。

Unknown、可观测的通知发送不明和 ChatRun 校准失败需要告警；进程在通知发送前崩溃时，
首版不能逐条识别漏发提示，也不承诺自动把所有异常恢复成成功。
完整 attempt 检索、跨节点看板、自动熔断、自动隔离与容量自动调参都后置。

## 16. 测试与验收计划

### 16.1 实施切片

1. 固化现有 send/inject 路由、服务端 flow_kind 分类、run 关联和 chat.abort 兼容测试。
2. 增加 delivery schema 和 repository 原子事务，覆盖 Memory/SQLite/MySQL；同步补齐
   content.attachments 的写入与读取，不增加 outbox 或附件表。
3. 实现单调度循环、队列限额、最小发送间隔、lane FIFO 和 context 绑定。
4. 接入从消息表读取正文/附件的一次性发送任务、最近 request 关联、terminal/ChatRun 查询校准，
   确认各业务回调恢复边界。
5. 接入排队取消、既有 Abort 后台任务和启动恢复。
6. 增加类型开关及就绪校验，接入首批就绪类型的全部子入口、后端状态查询/事件/取消能力和
   IM 简化提示，保持既有 CLI/Workbench 接口兼容；不实现 Workbench 新 UI。
   Direct A2A/state-machine 保持关闭并验证原路径兼容。
7. 在单实例部署约束下按类型和 Bot 灰度，验证重启、回退和人工恢复。
8. 后续 Direct A2A/state-machine 完成消息及事务整合后，分别运行接入验收再开启；
   这一步不属于首版群聊队列的交付前置条件。

### 16.2 数据与队列测试

- 同一 client message ID 并发提交只产生一条 canonical 消息及唯一目标 delivery。
- A/B/C fan-out 一份正文，各目标独立；B 满了不回滚 A/C。
- send/inject 沿用现有 enum；只有 send 创建 run。
- 同 Bot/session FIFO，不同 session 可以在 max_running 内并行。
- 多个开启类型共享同一 Bot/session 的顺序和 Bot 容量，不按 flow_kind 另起 lane 或复制限额。
- 单轮转下持续繁忙 session 不饿死其他 session；无优先级插队。
- 最小发送间隔、max_running、max_queued 和 TTL 边界。
- Bot 的 send 队列已满时只拒绝该目标 send，inject 仍可暂存，不计入 max_queued。
- 单 delivery 准备任务去重；慢附件准备不阻塞取消或其他 session。
- 准入、send-start、取消、终态任意事务失败时完整回滚，不释放错误的内存容量。
- DB 提交结果不明时暂停并读回，不能启动未确认授权的网络任务。
- Workbench/IM 等已开启入口的附件均落在 content.attachments；正文、mentions 和附件字段
  往返保存完整，合法列表的顺序不变，无附件旧格式继续可读。
- 附件写入失败时消息/delivery 同时回滚；不存在“已排队成功但稍后才补附件”的窗口。
- 入队后销毁或修改原请求对象，首次发送、延迟发送和重启恢复仍从数据库还原相同附件字段；
  IM 的已有 URL/expires_at 按入站值保留，不以重签、刷新或转存作为测试通过条件。
- 消息读取或附件解析失败不能只发送正文；多 Bot 共享一份附件数据，但各目标可见范围不扩大。
- 幂等重入不覆盖原附件；安全重试不重新取用原请求中的附件。
- 保存的附件 URL 不出现在队列状态通知或日志中；历史/事件对外投影不额外暴露 URL。

### 16.3 inject 测试

- M1 inject/M2 send/M3 inject/M4 send 的绑定只为 M1->M2、M3->M4。
- 晚到 context 不进入已经排队的前一条 send。
- inject 对引擎 chat.inject 的调用次数为 0；每次 send 请求携带固定 context。
- 安全重试不重新选择 context；未发出的永久取消解绑有效 context。
- 承载 send 取消时，已排队但尚未开始的最早后继获得重新绑定的 context；无后继时继续暂存。
- 可能已发出的 context 不给后续 send 重放。
- recall/删除/过期/取消的 context 不在允许撤回阶段进入 payload，已可能发送后不承诺撤回。
- 不引入 context 数量/字节配额检查；既有单条消息大小校验继续生效。
- 组合载荷超过下游已知限制时，准备失败直接结束承载 send，不启动发送或自动重试；
  原始消息和有效 context 保留，解绑/重新绑定遵循同一因果规则，不静默截断或压缩。
- inject 的附件随原始消息落库，绑定、解绑和重启后均从原消息读取；合并进 send 不改变
  原目标的附件可见范围，不因最终 wire 方法是 chat.send 而扩大文件访问权限。

### 16.4 异步竞态与崩溃测试

- queued 取消与准备完成竞争：取消成功后下游调用次数为 0。
- send-start 后取消：不会直接显示 cancelled，也不会重新发送。
- final 先于 ACK/future 返回，迟到结果不能覆盖终态。
- 旧 request ID、错误 Bot、旧 run/alias 不改变新运行；重复 terminal 只释放一次。
- 确认未发出才重试；换 request ID、保留 run ID，不依赖 fake Bot 帮忙去重。
- ACK 丢失时下游仍只收到一次调用，进入 Unknown，不通过换 run ID 重发。
- queued 阶段崩溃可以恢复；send-start 后任意点崩溃都不能盲重发。
- 重启恢复完成前不 dispatch；Unknown 占用容量重建正确。
- 双进程争用本机锁时第二个启动失败；升级测试确认旧进程已退出。
- 数据库故障不提前 ACK Provider terminal；WS 无法恢复的事件丢失明确进入人工核实。
- terminal、回复消息及回复目标准入不会让后序 send 越过尚未插入的前序 delivery。
- 在终态事务提交后、ChatRun 更新或通知发送前崩溃：回复与目标 delivery 仍已落库，
  重启不重复生成 Bot 回复/新运行，不扫描补发历史通知。
- delivery 已终态但 ChatRun 仍 Pending/Running：启动/查询校准能收敛，重复查询不重复
  推进版本或调用业务回调；仅丢失 long-poll 唤醒也能通过有界复查取得终态。
- 业务完成回调失败/崩溃按已确认的现有恢复机制或人工流程处理，不盲目重放有副作用的调用。

### 16.5 chat.abort 兼容测试

- scope Abort 合并已知受管/原路径 active，不因类型关闭而漏选；不取消 queued 或 pending inject。
- legacy run_id 只解析 scope；SessionBound token、subscription、Human 权限保持不变。
- WebSocket 使用原 session key/精确 run；Provider 使用 scope，校验实际返回 ID。
- 满 send 容量/暂停 dispatch 不阻塞取消后台任务。
- send/Abort 有序协调；Provider PENDING 未被取消时不误报成功。
- abort_started_at 前后崩溃能区分尚未开始和可能已发出，不盲重发丢失确认的 Abort。
- 空结果、410、timeout、disconnect、不支持均不能提前释放。
- final/error/aborted/Abort response 乱序只产生一次终态；partial success 不回滚。
- 新连接没有 run continuity 证据时，不把旧 Abort 发给不明的新执行。
- Provider 共享 scope 存在关闭类型流量、或无法证明取消期间独占时，精确 delivery cancel
  返回 exact_abort_not_supported；自动 deadline 取消也不扩大为可能误伤的 scope Abort。
- 原路径回包和受管回包按原 run 归属处理；旧 delivery 在关开关后仍能结算终态。
- Direct A2A 当前关闭时沿用原取消路径，不伪造 delivery；后续接入验收必须覆盖 active cancel
  等待真实确认，而不是只修改 ChatRun 本地状态。

### 16.6 后端 API、IM 提示与灰度测试

- admission response、WebSocket event、HTTP snapshot 的 delivery ID/version 和状态语义一致；
  用协议测试调用方验证乱序/重复时可按版本合并，不要求实现浏览器页面。
- 丢弃终态事件且保持连接时，后续 HTTP 查询仍返回最新终态；重启/重连后也能查询最新快照，
  不要求历史事件补发或前端轮询实现。
- 响应丢失后可在原发送者作用域按 client message ID 查询原结果；同 ID 重入不重复调度，
  不能跨身份读取其他用户的准入结果。
- 声明 capability 的发送者连接收到状态事件；旧客户端未声明 capability 不收到新事件，
  既有 Workbench 消息发送和 scope Abort 请求保持兼容。
- ahead=0 不误报 running；多 Bot 聚合、context-only、全拒绝、部分失败和 untracked 返回正确。
- IM 只通知原来源，不刷排名；非幂等发送结果不明时不盲重发，进程内去重不冒充跨重启去重。
- send 超过提示阈值、Bot 离线和必要的失败/取消/状态不明会触发对应简化提示；
  inject-only 不产生等待回复提示，dispatching/running 不逐状态刷屏，多 Bot 结果按消息聚合。
- IM 定时器/发送任务在重启时丢失可以漏提示，不回滚消息、不影响 Bot 调度；已完成的请求
  不再发送过时的排队提示，正常 Bot 回复仍沿用既有交付机制。
- 类型/Bot 启用前先清理受影响原路径在途请求；停用时有积压/Unknown 不能绕过调度。
- 类型开关全部默认 false；目标 Bot off 或类型 false 任一成立时都不新建队列 delivery。
- direct_a2a/state_machine 未就绪而请求开启时明确失败；不会因 Bot 已 enforce 而强制要求
  关闭类型写 bcs_messages，也不会对原路径创建空 message_id 的 delivery。
- 服务端分类贯穿共用发送辅助函数，Direct A2A/state-machine 不被误标 group；客户端不能
  自选 flow_kind 绕过开关。一个类型的 send/inject 不会被拆到不同策略。
- group 开启而 Direct A2A/state-machine 关闭时各走所选路径；原路径流量不虚报为受限流，
  不承诺与受管消息全局 FIFO，旧 inject 不回填重放。
- 停用某类型不联动关闭其他类型；已有跨类型 inject 绑定、查询和取消可继续处理。
- 热更新关闭但有积压时拒绝切换；重启配置已关闭时仍恢复旧 delivery，并阻止新直发越过。
- Provider header 非空时禁止启用，不能默默丢弃。
- 当前开启业务类型的全部子入口都进入统一准入；关闭类型保留原路径。后续分别开启
  Direct A2A/state-machine 的验收独立进行，不要求二者同时迁移或同时启用。

### 16.7 首版验收边界

验收要求是：单实例条件成立时，受管消息可查询和恢复、受管流量按 Bot 共享限流、同 lane 顺序成立、
inject 不依赖引擎、取消结果不误报、未知执行不盲重发。
首版必须验证 Direct A2A/state-machine 关闭和未就绪启用校验，不要求完成其 bcs_messages
迁移。关闭类型的原路径运行不计入队列限额，不对混合流量承诺 Bot 全部请求有序或总并发上限。
后端状态能力通过 API/协议测试验收，事件丢失后仍可查询正确结果；ChatRun 通过权威 delivery
校准，不能因投影通知丢失而永久停留在旧状态。IM 简化提示属于首版实现，但允许漏发，
不承诺补发；业务回调的自动/人工恢复边界必须明确。
Workbench 排队 UI、typing 动画、输入框行为、页面轮询和取消按钮均后续独立实现，
不运行或要求相应的新前端 UI 验收；后端仍需通过现有客户端消息发送/Abort 的兼容测试。
附件验收只保证准入时字段完整落库、发送时从消息表恢复并按原权限投递；IM 同样处理，
不保证附件 URL 在等待或重启后仍有效，也不承诺下游最终一定能下载外部文件。

不以 outbox、通知可靠补发、通用可靠回调、多副本、高可用接管、完整 attempt 审计、shadow observe、
优先级或自动故障修复作为首版验收项；也不要求 context 数量/字节配额、全局积压上限或自动压缩。
max_queued 只约束受管 send，不承诺 inject 总积压量或整体存储规模有界。
实现时运行受影响的单元/仓储/协议兼容和业务测试；本次仅文档变更，执行文档 diff/结构检查即可。

## 17. 假设、非目标与待办项

### 17.1 明确假设

- 只有一个调度进程，部署能阻止进程重叠；MySQL/OceanBase 的多连接不代表支持多调度者。
- Direct A2A 和 state-machine 当前尚未接入 bcs_messages，类型开关默认关闭，后续独立迁移/启用。
- 只有已开启且通过接入验收的业务类型进入调度器；其全部子入口统一准入，关闭类型保留原行为。
- run 事件可鉴权和关联；目标 Abort 支持已由协议测试/部署版本确认。
- 外部引擎和关闭类型的原路径仍可能独立执行，限流/严格顺序仅约束受管 delivery；Provider
  精确取消要求已验证的 scope 独占，不能仅凭受管 lane 的单 active 推断。

### 17.2 后置能力

- Workbench 消息排队 UI、逐 Bot 状态/聚合展示、输入框和 typing 交互、事件订阅/查询恢复及
  取消按钮；已明确由后续独立前端工作实现，复用本版后端接口，不阻塞首版后端与 IM 交付。
- 多实例调度、分布式租约/fencing、领导者选举、跨节点连接路由和滚动接管。
- 完整发送/Abort attempt 表、历史检索和独立任务执行平台。
- Direct A2A/state-machine 的 canonical 消息与准入事务迁移，分别交付，不与首版群聊队列绑定。
- 事务 outbox、状态通知扫描/补发及可靠异步业务回调；按实际可靠性需求另行评审，
  不仅以是否部署多实例作为引入条件。
- 持久化 lane/高频计数表、Redis/MQ、GCRA burst、优先级、aging、自动熔断。
- shadow observe 灰度、自动容量调优、自动 runtime 隔离和无证据释放。
- 已失败/取消运行的自动重跑、重新打开 terminal delivery。
- 下游幂等重放/状态查询协议扩展、跨重启透明流式恢复、不可重试事件的 durable inbox。
- inject 总积压治理、context 数量/字节预算、全局积压保护，以及上下文压缩/摘要策略。
- 附件（包括 IM）URL 的排队期间过期处理、刷新/重新签发、外部文件重新获取及提前转存。
  首版直接持久化入站附件字段并在发送时读回，不将这些后置能力作为队列接入前置条件。
- 精确 ETA。

Workbench 按上述独立前端工作交付；其余扩展能力按实际需求另行评审，不为未来可能多副本
提前保留空框架或必填配置。
inject 积压治理需要结合真实消息量、下游上下文窗口和压缩效果设计，不能仅用条数/字节硬阈值
替代语义处理。后续评审需明确何时压缩、如何保留原始消息及因果关系、摘要如何绑定到 send，
以及如何避免重放已消费的上下文；这些不是首版实现要求。

### 17.3 Provider bypass header TODO

当前仓库 example/local 配置未启用 `provider_http.bypass_headers`；实际部署是否非空仍需核实。
队列延迟和 BCS 重启会让仅存于入站 HTTP 请求中的 header 丢失，现有 Abort 又需要沿用原始
Provider routing header。因此它仍是明确的生产启用门槛，而不是单实例就可以忽略的问题。

首版规则：

- 不持久化 header 明文，不写入 delivery、消息来源元数据或日志。
- 实际 allowlist 非空的 Provider Bot 不允许开启 enforce，返回明确兼容性错误。
- Bot off 或类型关闭的请求可以沿用原路径，但不能宣称拥有本方案的 durable queue/恢复能力。
- 已选择受管路径的请求不允许因 header 缺失临时 bypass 调度器、改写 flow_kind 或切换 transport owner。
- 即使 allowlist 原先为空，运行中配置变为非空也必须阻止新的队列发送，先完成兼容性评审。

后续另行评审：run-scoped AEAD 加密上下文，或明确拒绝需要延迟透传 header 的请求。
不在本次首版顺带实现凭据存储系统。

### 17.4 实现前需要确认的事项

- 生产启动器、本机锁路径和非重叠升级流程的实际配置。
- 每个 Bot 的 max_running、max_queued、发送间隔与超时初值，通过真实 Bot 延迟和数据库压测确认。
- 现有 DB transaction、消息来源和 ChatRun 查询校准是否足够；缺口只做必要扩展，不引入 outbox。
- 各受管入口的附件字段是否完整进入 content.attachments，发送路径和存储/历史投影是否
  按第 6.1、10.1、13.4 节区分；IM 原 URL 的存储访问控制与不处理过期的边界是否明确。
- 每个 flow_kind 的服务端分类与持久化就绪清单；Direct A2A/state-machine 未就绪期间必须关闭，
  task/system 未核实前也不得默认宣称已支持。
- task/state-machine 等业务完成回调是否已有恢复机制；没有时明确人工核实入口、负责角色和
  不支持自动继续的首版限制，不能在上线后才把它解释为“通知允许丢失”。
- 各 transport 的 run alias、旧 run continuity 和可信终态关联是否通过真实 contract 测试。
- 关闭类型是否与受管类型共享 Bot/Session，以及 Provider scope 是否能证明无其他原路径运行。
- 人工恢复可接受的终止/隔离证据来源及负责角色；无证据时保持暂停。

这些是部署/兼容性检查项，不改变已确定的单实例设计。
如果任一检查要求超出单实例能力，应停止对应 Bot 的 enforce 启用，不通过悄悄增加副本、
丢弃 header 或放开重发来绕过。

### 17.5 交付边界

本文件是本次 Draft PR 的唯一预期变更，保留中文和 Draft for Review 状态，目标分支为 dev。
方案的首版实现范围是后端拥塞控制/查询/取消能力与 IM 简化提示，不包含 Workbench UI 实现。
本次仅提交、推送本文档并创建 Draft PR，供同学先评审方案；不合并 PR，不实现运行时代码。
具体 reviewer 后续指定，前端和后端实现均在方案评审后另行交付。
