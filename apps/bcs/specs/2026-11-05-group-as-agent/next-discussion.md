# BCN Group A2A 下次讨论

日期：2026-11-12 · 最近更新：2026-11-12 · 代码核对基线：`origin/dev` · `9a61798cb7` · [HTML 阅读版](next-discussion.html)

这次要明确三个问题：**A2A 如何对应现有群能力，上游具体怎样调用，BCS 要补哪些实现。** 群仍对外表现为一个 Agent；上游只处理消息、context、Task 和结果。

本轮讨论采用下面的范围。需要拍板的建议集中在最后一节，方便逐项记录结论。

| 范围 | 本次处理 |
| --- | --- |
| 已有主从群、自定义协作群的 A2A 调用 | 确定接口、过程消息、多轮输入、取消、并发规则和调用来源 |
| BCN 单 Agent、BCN Group | 对照两类 BCN 入口；单 Agent 的标准入站接口仍为设想 |
| 认证机制、凭据及相关传入字段 | 留待联合设计；本期确定基于现有调用主体的资源归属校验 |
| Task 等待补充输入与恢复 | 下一期；本期接入无需补充输入即可执行的群流程 |
| 同 context 的 FIFO 排队 | 下一期待定；本期采用忙碌拒绝 |
| Artifact | 独立 artifacts 表持久化，支持非文本 Part 的表达；本期公开文本和 JSON |
| 来源与用户字段 | caller/user 为暂定名，内部结构及凭据传递方式待联合设计 |
| 自由聊天群 | 后续讨论 Message-only 与 LLM 总结为 Artifact 两种模式 |
| 自然语言建群 | 暂不考虑，移出本方案规划 |

本文与[方案评审稿](design-review.html)及[当前 spec](spec.md)同步。重点讨论协议对照、上游接入和模块分工；待定合同与参数集中在 §7。

## 1. A2A 概念与现有群的对应

协议对照基于 [A2A 1.0.1 规范](https://a2a-protocol.org/v1.0.1/specification/)，线上版本字段使用 `1.0`。**“已有能力”列出当前 BCS 可复用的实现，“需要补齐”列出新增或扩展的工作；“性质”区分标准内的实现选择、支持子集和协议扩展。** 支持子集仍使用标准协议；协议扩展则新增 BCN 定义的字段或业务语义。上游接入表在 §2 分列标记。

| A2A 概念或字段 | 群内对应 | 已有能力 | 需要补齐 | 性质 |
| --- | --- | --- | --- | --- |
| Agent、AgentCard | 一个可重复调用的 Group | Group Card 模板及其存储 | 发布 Group A2A Card，填入调用地址和实际支持能力 | Card 是标准；Group 映射和发布 URL 是实现选择 |
| `contextId` | 直接使用 Session.id | Session ID、创建与重新激活、按 ID 查询 | 复用 Session 查询与目标群归属校验；不新增 context ID 或映射表 | 标准字段；复用 Session ID 是实现选择 |
| Task、`taskId` | 一次独立业务执行 | Session activation、StateMachine Run | 独立 A2A Task 记录，关联本轮 activation 或 Run，保存每轮结果 | 标准对象；本期以一轮独立执行建一个 Task |
| `message.messageId` | 一条上游输入 | 群消息记录；尚无 Group A2A 消息受理记录 | 持久化受理与去重；匹配有效受理记录时返回原 Task | 标准字段；去重是标准内的可选实现，对外沿用 MAY |
| `message.role`、`parts` | 输入消息的方向与内容 | 群消息内容、来源和投递通路 | 解码 A2A Message，将 `ROLE_USER` 输入及 parts 转为本轮群消息 | 标准字段；本期输入支持子集见 §2 |
| `Task.status` | 执行状态的对外投影 | Session、Run、节点、投递各自的状态 | 汇总本轮执行状态，生成并持久化 A2A Task 状态 | 标准状态；投影规则由 BCS 实现 |
| `TaskStatusUpdateEvent.status.message` | 分工、阶段进展、阶段总结 | `bcs_events` 中的消息、任务派发及节点事件 | 复用事件来源，关联到本轮 A2A Task，按 includeProgress 转为标准状态事件 | 事件标准；includeProgress 是扩展字段 |
| `Artifact` | 本轮最终结果 | manager 的 task.complete 与 summary；状态机 `final_output` 与 Run.output | 完成信号绑定本轮 Task/activation，Artifact 写入独立 artifacts 表，与成功终态同事务提交，保留非文本 Part | 对象标准；结果选择规则是实现选择 |
| CancelTask | 停止本轮执行 | Run abort、投递取消能力 | A2A 取消入口，覆盖整轮工作；确认停止后再提交 canceled 并释放占用 | 操作标准；底层取消由 BCS 实现 |
| `configuration.returnImmediately`、`historyLength` | 返回时机、返回历史数量 | Session 消息历史；尚无 Group A2A 返回配置 | 实现等待或立即返回 Task，以及按条数读取 Task history | 标准；二者都不是过程消息开关 |
| `metadata` | 附加调用信息 | Session 保存调用方关联信息 | 定义 includeProgress；外部 Agent / 上游用户来源字段的命名、结构与凭据传递方式待定 | 容器标准；这些键及其语义是扩展 |
| 同 context 只运行一个 Task | 同 Session 的执行占用 | Running Session 禁止再次激活；Run 创建已有 Session 空闲检查 | A2A Task 原子受理和占用，覆盖启动、取消、收尾阶段；隔离迟到事件 | 支持子集：本期不并行、不排队；CONTEXT_BUSY 为扩展错误原因 |

“最后输出”按执行轮次确定。主从群已有 manager 的 `task.complete` 和待完成工作检查，建议以该调用确认的 `summary` 作为最终汇总；普通阶段总结继续作为过程消息。需要补齐本轮关联和原子终态提交，见 §4。状态机按配置的最终输出节点取值。

### Task 处理方式与 BCN 的选择

**本期主从群和自定义协作群采用 Task-generating 模式：受理的业务执行统一返回 Task，每次独立执行对应一个 Task，多轮通过同一个 context 关联。** ✅ 表示本期方案选择；最后一列区分已有基础和需要补齐的实现。不同场景下的选择可以组合。

| 场景 | A2A 规则与可选处理方式 | BCN 本期选择 | 已有基础 / 待补齐 |
| --- | --- | --- | --- |
| 返回 Message 还是 Task | ① **Message-only**：只返回 Message<br>② **Task-generating**：统一返回 Task<br>③ **Hybrid**：按交互返回 Message 或 Task。[响应模式](https://a2a-protocol.org/v1.0.1/topics/life-of-a-task/#agent-response-message-or-task) | ✅ **② Task-generating**。即使很快完成，也返回 completed Task；请求校验失败仍返回错误。 | 待补齐：标准入站、Task 对象及持久化。 |
| 多轮消息怎样划分 Task | 未终结 Task 可包含多条 Message；独立后续工作也可在同 context 新建 Task。[多轮规则](https://a2a-protocol.org/v1.0.1/specification/#343-multi-turn-conversation-patterns) | ✅ **每次独立执行建一个 Task**，绑定一次 Session activation 或一个 Run；同 Task 补充输入按下面两行处理。 | 已有：activation / Run。<br>待补齐：A2A Task 关联和每轮结果快照。 |
| 向 working Task 发新消息，带 taskId | 服务端可选择①接收补充消息，或②不支持运行中追加；协议不要求消息排队。[异步处理](https://a2a-protocol.org/v1.0.1/specification/#333-asynchronous-processing) | ✅ **② 不支持追加**：返回 UnsupportedOperationError，不受理、不排队。 | 待补齐：入站状态校验及标准错误映射。 |
| Task 等待补充输入 | 可用 input_required 请求输入，再以同 taskId 继续；是否提供这种交互取决于服务能力。[多轮规则](https://a2a-protocol.org/v1.0.1/specification/#343-multi-turn-conversation-patterns) | **下一期**：Task 等待输入与继续执行整体后移；本期接入无需补充输入的群执行路径。 | 已有：群内 HumanInput。<br>下一期补 A2A 等待状态、输入校验和恢复入口。 |
| Task 到达终态后继续改目标 | **固定规则**：completed / failed / canceled / rejected 的 Task 不能重启；继续工作使用新 Task。[终态规则](https://a2a-protocol.org/v1.0.1/topics/life-of-a-task/#task-immutability) | ✅ **同 context 新 Task**，省略旧 taskId；自定义协作创建带新 input 的新 Run。 | 已有：Run input。<br>待补齐：已完成 Session 再激活与新轮输入通路。 |
| context 有执行中 Task，又收到不带 taskId 的请求 | 协议允许同 context 关联多个 Task。调度可设计为①并行，②串行排队，或③忙碌拒绝；FIFO 顺序和 busy 错误由服务定义。[并行后续工作](https://a2a-protocol.org/v1.0.1/topics/life-of-a-task/#parallel-follow-ups) | ✅ **③ 忙碌拒绝**：返回 409 / CONTEXT_BUSY，不创建新 Task。② FIFO 排队列为下一期待定。 | 已有：Session 空闲检查。<br>待补齐：A2A 原子占用与忙碌错误映射。 |
| SendMessage 何时返回 | ① returnImmediately=false（默认）：**等待执行完成后同步返回 Task**；执行失败、取消、拒绝，或需要补充输入／认证时也会返回<br>② true：**立即返回 Task**，后续查询或订阅执行结果。[返回配置](https://a2a-protocol.org/v1.0.1/specification/#322-sendmessageconfiguration) | ✅ **① + ② 都支持**，由上游通过标准字段选择。等待响应与 Task 是否独立执行是两个维度。 | 已有：Group Service 异步受理和 Session 查询。<br>待补齐：标准 Task 封装，以及新增**服务端等待完成或中断后再返回 Task**的能力。 |
| 怎样获取后续状态与结果 | ① GetTask 轮询<br>② 流式发送 / 订阅<br>③ Push Notification；②③按 Card 声明能力。[更新方式](https://a2a-protocol.org/v1.0.1/specification/#35-task-update-delivery-mechanisms) | ✅ **① + ②**。本期 streaming=true、pushNotifications=false。 | 已有：bcs_events 内部事实。<br>待补齐：将事实转换并保存为 A2A Task；轮询读取快照，SSE 推送标准状态与产物事件，详见 §4。 |
| 相同 messageId 重试 | SendMessage 幂等为可选能力（MAY）：服务端可利用 messageId 检测重复消息。[幂等规则](https://a2a-protocol.org/v1.0.1/specification/#331-idempotency) | ✅ **选择去重**：对外沿用标准 MAY；内部持久化预留 ID、完整请求指纹并复用原 Task。已有 message-dedup 扩展的适用边界见 §2。 | 待补齐：原子去重、ID 预留生命周期及结果清理后的处理。 |
| 请求取消 Task | CancelTask 请求服务端尝试取消；可能成功，也可能因阶段不支持等原因返回 TaskNotCancelableError。[取消规则](https://a2a-protocol.org/v1.0.1/specification/#315-cancel-task) | ✅ **提供取消能力**。收尾期限内等待停止确认，确认后转 canceled；超时仍未知则 failed 并隔离 context；终态再次取消按标准报错。 | 已有：Run abort、投递取消。<br>待补齐：整轮取消传播、有界收尾和恢复。 |

### BCN 两类入口的对照

| 入口 | 当前状态 | 上游怎样定位目标 | 会话与执行由谁负责 |
| --- | --- | --- | --- |
| BCN 单 Agent | 已有 Bot 调用、Card 管理和 A2A 出站；标准 A2A 入站为设想 | `/api/v1/collaboration/a2a/agents/{agent_id}`，agent_id 使用现有 bot_id | 拟由 BCN 包装单 Bot 执行 |
| BCN Group | 标准 A2A 入站为本次方案 | `/api/v1/collaboration/a2a/groups/{group_id}` | BCN Session、MessageFlow、StateMachine Runtime |

BCN 单 Agent 与 Group 可以使用同一种 Card、Message、Task 格式。定位目标只需要 URL 中的 ID；请求体仍需标准消息字段。上游不传 manager、成员列表、工作流定义或 provider 参数。单 Agent 的实现排期单独确定，本次落实 Group 路径。

## 2. 我们提供什么接口

Group 拟使用基地址 `/api/v1/collaboration/a2a/groups/{group_id}`，Card 发布在该地址下的 `/agent-card.json`。Card 中 `supportedInterfaces` 给出真实调用地址、`HTTP+JSON` binding 和版本 `1.0`；客户端读取 Card 后选择接口。

最新 dev 的 [#3900 路由变更](https://github.com/inclusionAI/Avernet/blob/06e9187178adb83cf2c7216641515bca52b8f21a/apps/bcs/crates/adapters/http/bcs-http/src/router.rs#L71-L84) 已为现有 A2A 管理和调用路由增加 `/api/v1/collaboration/a2a/...` 前缀，并保留 `/a2a/...` 兼容入口。本方案新增的 Group/单 Agent 标准入站采用版本化路径，示例同步使用该路径；新增入口无需再建立旧前缀别名。

| 路由用途 | `/api/v1/collaboration` 之后的路径 | 状态与共存方式 |
| --- | --- | --- |
| 外部 Agent 注册与 Card 管理 | `/a2a/agents`、`/a2a/agents/{bot_id}/agent-card`、`agent-card:refresh` | 已有；管理登记的外部 Card，保留当前处理器和权限 |
| 命名 Bot 调用及运行查询 | `/a2a/providers/{provider_name}/bots/{provider_bot_ref}/chat-async`、`/a2a/chat/runs/{run_id}` 及 `/cancel` | 已有；沿用 BCN 调用合同与 A2A 出站 |
| BCN 单 Agent 标准入站 | `/a2a/agents/{bot_id}/agent-card.json`、`/message:send`、`/tasks/...` | 设想；新建精确子路由。agent-card.json 发布 BCN 包装后的 Card，与已有 agent-card 管理路径分开 |
| BCN Group 标准入站 | `/a2a/groups/{group_id}/agent-card.json`、`/message:send`、`/tasks/...` | 本次新增；以下接口均相对此 Group 基地址 |

| 接口 | 请求要点 | 返回 | 来源 |
| --- | --- | --- | --- |
| `GET /agent-card.json` | URL 中的 group_id | AgentCard | 标准 Card 格式，BCN 发布路径 |
| `POST /message:send` | SendMessageRequest | `{ "task": … }` | 标准操作；Group 固定采用 Task 响应 |
| `POST /message:stream` | 同上 | SSE，先 Task，再状态与产物事件 | 标准 |
| `GET /tasks/{task_id}` | 可选 `historyLength` | Task | 标准 |
| `GET /tasks` | 可选 `contextId`、分页条件 | ListTasksResponse | 标准 |
| `POST /tasks/{task_id}:subscribe` | 定位已有非终态 Task | SSE，先当前 Task，再后续事件；已终态返回 UnsupportedOperationError，改用 GetTask | 标准 |
| `POST /tasks/{task_id}:cancel` | 定位已有 Task | 更新后的 Task | 标准 |

上表是对外 URL 合同。当前依赖 axum 0.8.9 / matchit 0.8.4，动态参数后不能直接拼接 `:cancel` 等后缀。适配层统一注册 `/tasks/{task_action}`，GET 将捕获值作为完整 taskId，POST 从末尾解析 `:cancel` 或 `:subscribe`；注册方式见 §5。上游继续调用上表中的标准 URL。见 [matchit 参数约束](https://docs.rs/matchit/0.8.4/matchit/#parameters)。

HTTP JSON 请求使用 `Content-Type: application/a2a+json` 和 `A2A-Version: 1.0`；流式响应使用 `text/event-stream`。具体凭据及认证接入方式另行讨论；资源归属边界如下。

### 调用主体与资源归属

已有 [Group Service 查询](../../crates/adapters/http/bcs-http/src/routes/services.rs) 校验目标群及 `Session.caller_principal`。A2A 入口复用现有认证层解析出的主体，在创建 Session 时保存 caller_principal；来源字段暂称 caller/user，命名、结构和凭据传递方式待定；自报来源只用于业务追踪。contextId 继续复用 `{group_id}:{8位hex}` 的 Session ID，访问权限由主体和资源归属决定。

本期只接受 **A2A 入口创建的 ServiceInvocation Session** 作为 context：由首个 A2A Task 的受理记录确认来源，保留到 Session 删除。续聊、GetTask、ListTasks、Subscribe、Cancel 都校验目标群、调用主体和 A2A 来源；列表先按这些条件过滤再分页，不能只在返回单条记录时校验。未知、跨群、不可访问的 Task 返回 TaskNotFoundError；对应 context 返回统一的 `404 / NOT_FOUND`、reason=`CONTEXT_NOT_FOUND`，不自动创建替代 context。

### 上游必须知道什么

**A2A 标准**列标记标准字段和标准允许的实现选择；**支持子集**列标记 BCN 本期的输入或交互限制，因此同一行可同时勾选这两列。**协议扩展**列标记 BCN 新增的字段或业务语义，扩展行只判断新增部分。

| 字段或行为 | 上游要求 | A2A 标准 | 支持子集 | 协议扩展 |
| --- | --- | :---: | :---: | :---: |
| `message.messageId` | 由消息创建方生成，每条新消息使用唯一 ID | ✅ | — | — |
| 相同消息重试 | 重试保留 messageId 和执行内容；幂等按标准 MAY，BCN 实现选择去重，详见下文 | ✅ | — | — |
| `message.role`、`message.parts` | 客户端发消息使用 `ROLE_USER`，内容放在 parts | ✅ | — | — |
| 本期输入范围 | 使用文本和至多一个对象 data Part；Card 声明媒体类型，详细结构约束见下文 | ✅ | ✅ | — |
| `message.contextId` | 携带服务端返回的 contextId，可关联同一上下文中的后续交互 | ✅ | — | — |
| `message.taskId` | 指向已有 Task；创建新 Task 时省略；终态 Task 不接受新消息 | ✅ | — | — |
| 向 working Task 追加消息 | 本期不支持；按 §3 返回 UnsupportedOperationError | ✅ | ✅ | — |
| Task 等待补充输入与恢复 | 下一期；本期接入无需补充输入即可执行的群流程 | ✅ | ✅ | — |
| `configuration.returnImmediately` | 省略或 false：等终态或中断态后同步返回 Task；true：立即返回 Task，后续查询或订阅。BCN 的候选 HTTP 等待上限为 60 秒，超过后会收到下行所述的 504 | ✅ | — | — |
| 同步发送的等待上限 | BCN 额外约定：达到单次等待上限返回 504 / SEND_WAIT_TIMEOUT，Task 继续执行。预计较长的群任务，建议显式使用 true 或 stream | — | — | ✅ |
| `configuration.historyLength` | 限制返回的历史消息数量，不控制过程消息的生成 | ✅ | — | — |
| `configuration.acceptedOutputModes` | 按 Card 选择可接收的输出媒体类型；不相交时在受理前报 ContentTypeNotSupportedError | ✅ | — | — |
| `metadata.includeProgress` | 可选，建议默认 false；需要业务过程消息时设 true | — | — | ✅ |
| 上游 Agent 来源（暂称 caller） | 字段名、结构和必填性待定，表示上游 Agent | — | — | ✅ |
| 上游用户来源（暂称 user） | 字段名、结构和凭据传递方式待定 | — | — | ✅ |
| 扩展发现与激活 | 读取 Card 的 capabilities.extensions；使用本扩展已定义的字段时，必须以 A2A-Extensions 请求头激活对应扩展 | ✅ | — | — |
| 同 context 的并发能力 | 本期只运行一个 Task，不并行、不排队；忙时不受理新执行，前一轮结束后重试 | ✅ | ✅ | — |
| BCN 错误原因 | 使用标准错误结构；CONTEXT_BUSY、MESSAGE_ID_CONFLICT、CONTEXT_SOURCE_CONFLICT 等业务 reason 见受理规则 | — | — | ✅ |

支持 HTTP+JSON 的标准客户端可不使用扩展完成调用。上游 Agent 和背后用户的表达方式待来源合同定稿；自动调用需要允许无背后用户。

### 错误与恢复方式

标准错误按 [A2A §5.4 映射](https://a2a-protocol.org/v1.0.1/specification/#54-error-code-mappings) 返回：TaskNotFoundError 为 `404 / NOT_FOUND`；TaskNotCancelableError、UnsupportedOperationError 为 `400 / FAILED_PRECONDITION`；ContentTypeNotSupportedError 为 `400 / INVALID_ARGUMENT`。以下集中定义 BCN reason；HTTP 状态与响应中的 error.code 一致，error.status 使用对应名称，reason 放在 ErrorInfo 中。

| BCN reason | HTTP / error.status | 触发条件与上游处理 |
| --- | --- | --- |
| CONTEXT_BUSY | 409 / ABORTED | context 有执行中任务；查询原 Task，待结束后重试未受理请求 |
| CONTEXT_SOURCE_CONFLICT | 400 / FAILED_PRECONDITION | 候选规则：更换或补绑定 context 来源；与来源合同一起确认 |
| CONTEXT_UNAVAILABLE | 400 / FAILED_PRECONDITION | 旧执行停止结果未知，context 已隔离；等待恢复处置，不自动重发业务消息 |
| CONTEXT_NOT_FOUND | 404 / NOT_FOUND | context 未知、跨群、不可访问或非 A2A 来源；核对目标和持有的 ID |
| MESSAGE_ID_CONFLICT | 400 / INVALID_ARGUMENT | 同范围同 ID 的完整请求指纹不同；核对原请求，不能以换 ID 的方式自动补执行 |
| MESSAGE_RESULT_EXPIRED | 404 / NOT_FOUND | 原 ID 仍有预留，结果已清理；不重新执行该消息 |
| CALL_METADATA_NOT_ACTIVATED | 400 / INVALID_ARGUMENT | 已知来源/过程字段存在但未激活扩展；补头后重发，原请求未受理 |
| SEND_WAIT_TIMEOUT | 504 / DEADLINE_EXCEEDED | BCN 单次同步等待到期；若取得 taskId，则查询或订阅原 Task |

SEND_WAIT_TIMEOUT 是 BCN 定义的 HTTP 等待错误，A2A 的标准错误表没有定义它；业务 reason 与单次等待时限属于表中的额外约定。网关自行返回的 504 可能没有上述 JSON 结构，上游按结果未知处理。请求体超限使用 HTTP 413，在解析和受理前拒绝，不创建 Task。

### A2A 输入格式与 BCN 接收范围

标准 Message 的 `parts` 是内容数组；每个 Part 在 `text`、`data`、`raw`、`url` 中选择一种，还可带 mediaType 等信息。`data` 支持任意 JSON 值，包括对象、数组和标量。见 [Part 定义](https://a2a-protocol.org/v1.0.1/specification/#416-part)。

| Part 内容 | 标准表达 | BCN 本期接收范围 |
| --- | --- | --- |
| 文本 | `{"text":"比较两家供应商"}` | 接收，按原顺序保留 |
| 结构化数据 | `{"data":{"budget":500000},"mediaType":"application/json"}`；data 也可为数组、字符串、数值等 | 接收至多一个对象 data，映射到本轮 input.data；对象形状和数量限制属于 BCN 业务合同 |
| 文件 | raw 表示 Base64 字节，url 表示文件地址 | 本期不接收文件输入 |

**BCN 使用标准 Message / Part 格式，本期接收的是标准输入形式的子集，与 A2A 协议兼容。** Card 的 `defaultInputModes` 声明 `text/plain`、`application/json`，具体 skill 可用 `inputModes` 覆盖。媒体类型声明表达“能接收什么类型”；对象形状、数量及文件支持范围写入接入文档，Card 的 documentationUrl 指向该文档。data 为数组或标量、或提交多个 data Part 时，按业务输入校验返回 `400 / INVALID_ARGUMENT`；未支持的媒体类型返回 `ContentTypeNotSupportedError`。详见 [Card 和 Skill 定义](https://a2a-protocol.org/v1.0.1/specification/#441-agentcard)。

主从群将文本按顺序放入本轮 manager 输入，对象 data 保存到 Task 原始输入并以独立 JSON 数据块注入 manager 的本轮上下文；manager 分配任务时选择相关数据传给 Worker。自定义协作把同一对象映射到 Run.input.data，由节点输入绑定读取。两条路径都保留原始结构，不只保存一段文本摘要。

Card 的 `defaultOutputModes` 按群实际产物声明：主从群本期为 `text/plain`；可输出结构化结果的自定义协作声明 `application/json`，需要文本结果时由输出节点显式提供。请求的 `acceptedOutputModes` 与 Card/skill 输出能力求交集，省略时采用服务默认；无交集在受理前返回 ContentTypeNotSupportedError，受理后按选定类型输出 Artifact 和可选过程消息。执行中产物不满足已选类型时 Task 失败并说明原因，不临时输出未协商的媒体类型。

### 相同消息重试，A2A 标准保证什么

A2A 对 SendMessage 的幂等性使用 **MAY**：服务端可以利用 messageId 识别重复消息，但协议不强制去重。**BCN 对外保持这个可选能力语义，实现上选择去重。** 去重属于标准内的实现选择，不增加高于标准的必然去重承诺。见 [规范 §3.3.1 Idempotency](https://a2a-protocol.org/v1.0.1/specification/#331-idempotency)。

已有 [message-dedup/v1 合同](../../../../docs/specs/a2a/extensions/message-dedup/v1.md) 是 **协商后生效的 Bot endpoint 可选扩展**，强于标准 MAY。Group 本期不声明或激活该 URI；对外保持上面的标准语义。需要让 BCS 出站客户端安全自动重试时，必须先确认 Group 的扩展适用范围与完整实现，再决定是否发布，不能仅凭内部做了去重就启用客户端重试。

| 比较项 | 已有 message-dedup/v1 | Group 本期设计 |
| --- | --- | --- |
| 适用范围与协商 | 新建 Task；认证 Bot endpoint 内，双方协商 URI | 标准 Group 入站；本期不发布该扩展，去重为内部实现目标 |
| 去重范围 | 认证 Bot endpoint | tenant（有则使用）＋认证 caller_principal＋group_id＋messageId；排除可伪造的 metadata.caller |
| 请求指纹 | context、parts、configuration、metadata 等完整语义请求；协商保持一致 | 同样保存完整语义请求和扩展激活集合，包含 returnImmediately、historyLength、acceptedOutputModes、includeProgress；只允许 send/stream 传输方式变化 |
| Task 结果清理 | ID 预留随 Bot store 保留；清理或过期不能重新执行 | ID 预留随 Group 调用存储保留；结果清理后保留最小墓碑，重试返回已清理错误，不重新执行 |
| 服务重启 | 未完成 Task 置 failed，不自动补执行 | 恢复已有执行关联、占用和取消进度；启动结果未知时核实，不再次派发。此项需在未来扩展评审中明确 |
| BCS 客户端自动重试 | 仅已声明且协商后，按保存的原请求重发 | 未协商时遵守既有客户端规则，报告结果未知，不自动重发；已知 taskId 则查询或订阅 |

内部受理需原子保存 ID 预留和初始 Task，再启动执行。同范围同 ID、同指纹复用原 Task；不同指纹返回 `400 / INVALID_ARGUMENT`、reason=`MESSAGE_ID_CONFLICT`。Task 结果已清理则返回 `404 / NOT_FOUND`、reason=`MESSAGE_RESULT_EXPIRED`；ID 预留继续阻止再次执行。存储失败返回错误。这里“复用原 Task”指同一次任务，实际响应仍遵循原请求的 returnImmediately；不会为了重试而改变执行或等待配置。

例如首轮响应丢失而 taskId 未知时，未协商去重扩展的 BCS 客户端报告结果未知；不会换 messageId 悄悄发起替代执行。已拿到 taskId 后，直接 GetTask；Task 尚未终结时也可 Subscribe。

### 同步等待、网关超时与断线

returnImmediately=false 的成功响应只在终态或中断态返回，不能因等待超时而成功返回 working Task。BCN 候选方案将单次 HTTP 等待设为 60 秒；达到上限时结束本次等待，返回 `504 / DEADLINE_EXCEEDED`、reason=`SEND_WAIT_TIMEOUT`，已受理且可见时在错误 details 中给出 taskId/contextId。默认阻塞的标准客户端调用超过该时长的群任务会收到这个错误；长任务接入示例使用 returnImmediately=true 或 stream。网关先超时或客户端断线时可能拿不到这些 ID；业务执行继续，查询和重试按前述规则处理。

单次等待上限、网关超时和 **Task 执行期限** 分开设置：部署时网关超时应大于服务端等待上限并预留响应时间；预计执行较久的上游选择 returnImmediately=true 或 stream。执行期限及取消收尾期限见 §4，具体时长在 §7 确认。该等待上限属于传输错误边界，不改变 [标准返回条件](https://a2a-protocol.org/v1.0.1/specification/#322-sendmessageconfiguration)。

### metadata 的候选结构

```json
{
  "includeProgress": true
}
```

对象放在 **SendMessageRequest 顶层的 metadata**。includeProgress 可选，默认 false，在本轮受理时固定。

**待定：** 来源和用户字段暂称 caller/user，最终名称、内部结构、必填性与凭据传递方式（metadata、HTTP 头或其他载体）需与认证机制联合设计。此前的 caller: {id, name?}、user: {id} 是讨论候选，当前 Schema 只定义 includeProgress。

来源绑定的候选规则是：首轮保存，后续省略则继承；更换或补绑定返回 400 / FAILED_PRECONDITION、CONTEXT_SOURCE_CONFLICT。该规则与来源合同一起确认。资源归属和去重范围继续用已解析的 caller_principal，不使用自报来源作为认证键。

扩展拥有的键由最终 Schema 明确；其他扩展键可以共存，同名字段冲突在发布组合时解决。

### 上游如何发现 metadata 字段

**Agent Card 在 `capabilities.extensions` 声明可选扩展，`required: false`；使用这些字段的请求必须激活扩展。** 普通标准调用可以省略这些字段。标准提供 uri、description、required、params 这些声明字段；includeProgress 由 BCN 定义；caller/user 的最终结构和 Schema 待定。扩展规范与结构说明见 [A2A Extensions](https://a2a-protocol.org/v1.0.1/topics/extensions/#extension-specification)。

| 发现内容 | 放在哪里 | 约定归属 |
| --- | --- | --- |
| 接入说明、业务输入限制 | Card.documentationUrl 指向接入文档 | Card 字段为标准，文档内容由 BCN 提供 |
| 扩展标识和用途 | capabilities.extensions 中的 uri、description、required | 标准扩展声明；URI 标识 BCN 的版本化合同 |
| 已确定字段的 Schema；来源字段定稿后补齐 | 建议放在扩展 params.metadataSchema；也可由扩展文档链接独立 Schema | params 是标准容器；metadataSchema 这个键及其位置由 BCN 扩展约定 |
| 本次请求是否使用扩展 | A2A-Extensions 请求头列出 Card 中的 URI | 标准激活机制；字段仍放在 SendMessageRequest 顶层 metadata |

上游读取 Card 后，按扩展 URI 理解合同，并用 Schema 校验字段。Schema 能描述结构，字段来源和多轮继承规则仍由扩展文档解释。使用这些字段时携带激活头，响应头确认已激活的 URI。**出现已定义的扩展字段（当前为 includeProgress）却未激活对应扩展时，在创建 Session/Task 前返回 `400 / INVALID_ARGUMENT`、reason=`CALL_METADATA_NOT_ACTIVATED`，提示补齐请求头后重发。** 这属于 BCN 对已知扩展字段的输入校验；可选扩展不使用 ExtensionSupportRequiredError。见 [扩展激活](https://a2a-protocol.org/v1.0.1/topics/extensions/#extension-activation)。

未提供扩展字段时允许普通标准调用，includeProgress 默认为 false；来源继承与补绑定行为按最终来源合同处理。漏激活头在受理前拒绝，不创建 context。

<details>
<summary>示例：Agent Card 中的扩展和字段 Schema</summary>

以下为 Card 的相关片段，发布时将示例域名替换为可长期维护的文档和扩展地址：

```json
{
  "documentationUrl": "https://bcn.example.com/docs/a2a/group",
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain"],
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extensions": [{
      "uri": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md",
      "description": "在 SendMessageRequest.metadata 中提供调用来源和过程消息开关。",
      "required": false,
      "params": {
        "metadataSchema": {
          "$ref": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.schema.json"
        }
      }
    }]
  }
}
```

使用这些字段的请求增加：

```http
A2A-Extensions: https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md
```

当前 [Schema 草稿](call-metadata.schema.json) 只定义 includeProgress。正式扩展规范与 Schema 采用 GitHub 仓库地址，扩展名和文件名为候选，来源字段定稿后再发布。Card.uri 与激活头一致；Schema.$id 与 Schema 自身引用一致。自动获取 JSON 应使用仓库原始内容地址或明确的仓库解析方式。metadataSchema 是 BCN 的发现约定。

这里的输出能力片段对应主从群；自定义协作应按实际结果节点调整 defaultOutputModes。

</details>

<details>
<summary>示例一：新会话，只用标准字段</summary>

```http
POST /api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send
Content-Type: application/a2a+json
A2A-Version: 1.0
```

```json
{
  "message": {
    "messageId": "msg-001",
    "role": "ROLE_USER",
    "parts": [{"text": "比较两家供应商，预算为 80 万。"}]
  },
  "configuration": {"returnImmediately": true}
}
```

受理后返回：

```json
{
  "task": {
    "id": "task-001",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "status": {"state": "TASK_STATE_SUBMITTED"}
  }
}
```

</details>

## 3. 运行中的 context 如何接收消息

### A2A 标准如何处理

**A2A 的 working 是 Task 状态；context 用于关联消息和任务，协议没有定义 context 的 busy 状态。** 同一 context 可以关联并行 Task，参见官方的 [Parallel Follow-ups](https://a2a-protocol.org/v1.0.1/topics/life-of-a-task/#parallel-follow-ups)。

| 请求方式 | A2A 标准语义 |
| --- | --- |
| 带 contextId，不带 taskId | 可以在已有上下文中发起新 Task；不要求先结束该 context 中其他 Task |
| 带 taskId，指向未终结 Task | 明确向该 Task 发送后续消息；能否在 working 阶段接收，由服务端能力决定 |
| 带 taskId，指向终态 Task | 该 Task 不再接受新消息；后续工作使用新 Task |

标准没有统一要求“有 working Task 就拒绝新请求”，也没有规定必须排队或自动并入当前 Task。contextId 和 taskId 的用法见 [规范 §3.4](https://a2a-protocol.org/v1.0.1/specification/#34-multi-turn-interactions)，未终结 Task 是否接收补充消息见 [§3.3.3](https://a2a-protocol.org/v1.0.1/specification/#333-asynchronous-processing)。

### BCN 本期采用忙碌拒绝

**同一 context 最多一轮执行。本期选择③忙碌拒绝：有活动 Task 时，新的不带 taskId 请求返回 HTTP 409 / CONTEXT_BUSY。** 请求未受理，不创建新 Task，也不进入队列。上游查询或订阅 activeTaskId，待前一轮结束后重试。② FIFO 排队列入下一期待定，评估要点见 §6。

占用从原子受理时取得，覆盖启动中、working、取消及收尾未确认的整个生命周期。只检查 `status == working` 会留下并发窗口；旧执行确认结束并收尾后再释放占用。重试先查有效受理记录，匹配时复用原 Task，再按返回配置响应。

### 向同一个 Task 追加消息，会不会排队

这里的 append 指用新 messageId、相同 taskId 再调用 SendMessage。A2A 没有专门的“消息追加队列”操作，也不保证追加消息会排队。

本期对 working Task 的新消息仍返回 UnsupportedOperationError，不受理、不排队。如果以后支持，需要在同一 Task 内持久化待处理消息，明确 manager 或状态机何时消费；已受理的消息没有处理完之前，Task 不能完成。若完成先提交，新消息就按终态拒绝；若消息先受理，完成判定必须包含它。简单地把消息存入群历史，会造成“已返回完成，但追加要求尚未执行”的歧义。

Task 的 input_required 与补充输入恢复整体放到下一期；本期不新增 A2A 补充输入入口。

### 受理规则

| 请求情形 | 本期处理 |
| --- | --- |
| 无 contextId、无 taskId | 创建 Session 和 Task；原子保存受理记录 |
| 有 contextId、无 taskId，context 空闲 | 复用 Session，创建下一轮 Task |
| 有 contextId、无 taskId，context 被占用 | 返回 409 / CONTEXT_BUSY，不受理、不创建新 Task |
| context 因旧执行停止结果未知而隔离 | 返回 400 / CONTEXT_UNAVAILABLE，附可见的原 taskId 与恢复提示，不开始下一轮 |
| 相同 messageId、相同执行请求重试 | 优先查重，匹配有效受理记录时复用原 Task；不因为 context 忙而拒绝已识别的重试 |
| 相同 messageId、不同执行内容 | 与有效受理记录冲突时返回 MESSAGE_ID_CONFLICT |
| 原 ID 仍有预留，但 Task 结果已清理 | 返回 MESSAGE_RESULT_EXPIRED，保留预留记录，不再次执行 |
| 未知、跨群或不可访问的 taskId | 返回 TaskNotFoundError，不受理 |
| 未知、跨群、不可访问或非 A2A 创建的 contextId | 返回 404 / CONTEXT_NOT_FOUND，不创建替代 Session |
| 已知 metadata 扩展字段存在，但未激活扩展 | 返回 400 / CALL_METADATA_NOT_ACTIVATED；补齐头后可重发，尚未创建 Session/Task |
| context 来源绑定冲突（合同待定） | 候选为 400 / CONTEXT_SOURCE_CONFLICT，与来源合同一起确认 |
| 带 taskId 指向 working Task | 本期返回 UnsupportedOperationError，保持原执行 |
| 带 taskId 指向终态 Task | 新消息返回 UnsupportedOperationError；原消息重试匹配有效受理记录时返回原 Task |
| 同时带 contextId 与 taskId | 先校验归属一致；不一致返回参数错误 |

处理顺序为：验证调用主体、协议和输入能力 → 验证目标及资源归属 → 原子查重 → 校验来源绑定和 Task/context 状态 → 新受理或复用原 Task。BUSY、参数错误及来源冲突均不预留新 messageId。错误使用标准结构，表中的大写业务 reason 由 BCN 定义。本期的多轮指前一轮结束后，在同 context 开始下一次独立执行；Task 进行中的补充输入与恢复列入下一期。

<details>
<summary>示例二：上一轮结束后改目标；运行中发送的错误</summary>

```json
{
  "message": {
    "messageId": "msg-002",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "role": "ROLE_USER",
    "parts": [{"text": "预算改为 50 万，重新比较并给出建议。"}]
  },
  "configuration": {"returnImmediately": true}
}
```

context 空闲时，受理并返回新的 task-002，contextId 保持不变：

```json
{
  "task": {
    "id": "task-002",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "status": {"state": "TASK_STATE_SUBMITTED"}
  }
}
```

若 task-001 仍在执行，同一请求返回 HTTP 409：

```json
{
  "error": {
    "code": 409,
    "status": "ABORTED",
    "message": "当前会话仍有任务执行中，请在任务结束后重试。",
    "details": [{
      "@type": "type.googleapis.com/google.rpc.ErrorInfo",
      "reason": "CONTEXT_BUSY",
      "domain": "bcn.example.com",
      "metadata": {"contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41", "activeTaskId": "task-001"}
    }]
  }
}
```

`CONTEXT_BUSY` 为 BCN 业务原因，domain 由部署服务确定。上游可据 activeTaskId 查询或订阅；忙碌响应不创建 task-002，待旧执行结束后再提交原请求。

</details>

## 4. 执行能力与终态如何补齐

### 本轮完成信号与状态映射

**已有能力：** [handle_task_complete](../../crates/services/bcs-message-flow/src/task_flow.rs) 只允许 manager 发起 `task.complete`，支持 completed/error，并检查待回复工作和未完成回送；[complete_session_with_summary](../../crates/services/bcs-message-flow/src/task_flow/support.rs) 把 summary 保存为 Session.output。Session 另有按 activation 完成的能力。主从群已经有显式完成入口。

**待补齐：** A2A 执行中的 task.complete 必须精确指向本轮 Session、activation 和关联 Task，复用现有入口和按 activation 完成能力；禁止回退到“该群最新 Running Session”。manager 提交的 summary 作为最终汇总，必须满足下文的对外内容规则。普通聊天最后一条消息、Worker 的单项完成或运行一段时间无消息，都不触发 A2A COMPLETED。

接受完成信号时，原子关闭本轮新派发，确认本轮工作和结果回送已经完成，并固定最终结果及事件消费边界。先向 task.complete 请求返回受理确认，再等待 manager 当前执行结束；完成本轮清理及终态提交后释放 context 占用，允许下一轮开始。manager 退出或清理超时同样进入有界收尾流程。

| 群执行事实 | A2A Task 状态 | 新增映射与提交条件 |
| --- | --- | --- |
| 输入校验通过且已原子受理，尚未开始 | SUBMITTED | 持久化 Task 与占用后返回 |
| 本轮 manager 或 Run 正在执行 | WORKING | 关联明确的 activation/Run |
| manager 成功提交最终 summary；或 Run 成功且取得指定最终节点输出 | COMPLETED | 冻结本轮派发，完成事件投影和结果快照；状态与 Artifact 原子提交 |
| manager task.complete(error)、Run Failed、执行超时、启动失败或输出类型错误 | FAILED | 保存可展示的失败原因；仍需终止关联工作并处理占用 |
| 用户请求取消，关联工作已停止且收尾完成 | CANCELED | 与完成/失败提交互斥，迟到输出不能修改终态 |
| 运行中意外进入 HumanInput | 终止该轮后 FAILED | 本期没有 A2A 输入恢复能力，按下面的有界收尾处理 |

上表使用标准状态名缩写，线上值带 `TASK_STATE_` 前缀。本期没有独立的业务拒绝入口，不主动生成 REJECTED：受理前校验失败返回协议错误，受理后启动或执行失败映射 FAILED。REJECTED 仍是标准终态，协议校验及终态取消/订阅规则继续覆盖它。

### manager 未提交完成信号时

新增本轮空闲检查：仅在本轮已无运行中或排队的 manager/Worker 调用、待回送结果、定时重试和停止结果未知的工作时开始计时。建议连续空闲 60 秒仍未收到 task.complete，向 manager 发一次提交最终汇总的提醒；提醒后再给 120 秒完成宽限。若收到有效完成信号则正常完成；若 manager 派发了实际新工作，则退出本次空闲检查，待工作结束后重新检查。

每个 Task 最多提醒一次，提醒受理标记和截止时间持久化。提醒后仍无完成信号，或再次空闲超过宽限，触发 FAILED 收尾，对外原因使用“执行未提交最终结果”；正在运行的提醒调用先走同一取消通路。新工作仍受整轮执行期限约束。SSE 心跳、普通过程文本和日志不重置这些计时；Worker 正常执行但没有过程消息时，不因“消息静默”提前失败。具体时长列入 §7 确认。

### 执行期限与异常收尾

**已有基础：** 底层 delivery 有运行超时和取消状态，Runtime 有 abort；本期新增整轮 Task 的执行期限、收尾期限及持久化恢复。发布 Group Card 前校验流程能力：本期拒绝含 HumanInput 的静态流程；动态路径意外进入等待输入时触发失败收尾，不长期保持 working。

| 情形 | Task 与执行的处理 | context 的处理 |
| --- | --- | --- |
| 超过执行期限、Worker 失联超出运行预算、意外等待输入 | 停止派发并发出取消，在收尾期限内确认；正常收尾后提交 FAILED | 停止确认和结果保存后释放占用 |
| 用户取消且在收尾期限内确认停止 | 提交 CANCELED | 释放占用，可开始下一轮 |
| 收尾期限已过，底层停止结果仍未知 | 提交 FAILED，明确说明停止结果未知；关闭该代执行的派发/事件接纳并记录待核实工作 | 隔离 context，续聊返回 400 / FAILED_PRECONDITION、reason=CONTEXT_UNAVAILABLE；后台有界重试并告警，确认旧工作停止后解除隔离 |
| 进程重启 | 读取绝对截止时间、执行代次和已派发关联，不重置预算；核实已有执行，不重复启动 | 恢复占用或隔离状态；旧代次结果无法进入新一轮 |

隔离阶段 Task 已有可查询终态和具体原因。恢复流程必须有负责人和处置入口；只有取得停止证据，或已验证执行端隔离能阻止旧工作继续生效，才可解除占用。仅让锁过期无法证明 Worker 停止，因此不采用盲目强制释放。支持有界停止或隔离的执行链路是开放 Group A2A 的验收条件。

### 过程消息采用通用的 includeProgress

建议使用 `metadata.includeProgress`，默认 false。客户端可以将它展示为“显示执行过程”。名称与单 Agent、Group 都适用，不要求客户端知道 manager 和 Worker。

| 设置 | 返回内容 | 接入行为 |
| --- | --- | --- |
| false 或省略 | Task 状态、错误和最终 Artifact | 仍可查询或订阅执行状态 |
| true | 上述内容，加上分工、阶段进展、阶段总结 | SSE 通过 statusUpdate.status.message 传递；已保存的过程消息可从 Task history 获取 |

过程消息是已发生的业务动作和可展示的阶段结果。主从群从 manager 消息及派发事件生成；自定义协作可映射节点阶段事件。BCS 需要识别“过程”与“最终结果”，不把每条群内消息直接转发。最终结果保存到独立 artifacts 表，再生成标准 Artifact。

### 对外消息与失败原因的范围

includeProgress 只控制公开进展是否出现；公开内容由同一投影逻辑生成，先筛选再写入 Task.history、status.message 和对外事件流。GetTask 与 SSE 使用同一份公开数据。

| 内容来源 | 对外处理 |
| --- | --- |
| 派发、节点启动/完成等运行事实 | 使用“开始核对报价”“已完成信息整理”等业务阶段模板；可携带经过配置允许的阶段名和完成数量 |
| manager 阶段总结与最终 summary、流程结果节点 | 仅接收明确标为对外输出且符合发布字段约束的内容；普通内部群聊不直接转发。最终 Artifact 也按该边界生成 |
| FAILED、取消异常和协议错误 | 输出稳定的业务原因和下一步建议，如“执行超时”“执行未提交最终结果”；详细诊断保留在内部日志，并通过关联 ID 排查 |
| 成员 Bot ID/姓名、内部路由和地址、系统提示、模型思考、凭据、工具原始返回与堆栈 | 默认只保留在内部；业务结果确需引用工具数据时，通过已定义的对外结果字段投影 |

内容约束按字段和确定的事件模板实现，不依赖模型自行保证脱敏。无法确认适合公开的过程内容不生成对应进展；错误仍返回概括原因，最终产物无法通过发布校验时按执行失败处理。发布配置及样例验收由 MessageFlow、Runtime 和调用应用服务共同维护。

开关作用于本轮 Task，在首次受理时固定；续聊可重新选择。所有订阅者看到同一份事件流。`historyLength` 只裁剪返回的历史条数；`returnImmediately` 只决定发送请求何时返回。同步发送要在响应中查看已保留过程，实时查看则使用 stream 或 subscribe。

### 与 bcs_events 结合

`includeProgress` 是调用方选择“是否展示过程”的开关，`bcs_events` 提供生成这些过程消息的内部事实。优先复用现有事件体系：已有 [事件目录](../../api-contracts/events/v1/catalog.yaml)、持久化事件、唯一 event_id 及 stream.sequence 顺序；新增的是按本轮 Task 归集、消费去重和转换为 A2A 输出的能力。

- `task.assigned`、`task.completed` 可形成“已分配工作”“某项工作已完成”的进展消息。
- `message.created` 中可展示的 manager 阶段总结可转换为 statusUpdate.status.message。
- `state_machine.node.started/completed/retry_scheduled` 可形成流程进展；Run 的最终输出仍按完成条件生成 Artifact。

**bcs_events 提供群执行中发生的事实；轮询和 SSE 是上游获取 A2A Task 状态与结果的两种方式。** 本期新增的转换逻辑把内部事实和执行状态整理成同一份 A2A Task 记录，再供两个出口使用：

| 层次 | 处理内容 | 上游看到什么 |
| --- | --- | --- |
| 群执行与 bcs_events（已有） | 节点启动、任务完成、manager 消息等业务事实 | 由 BCN 内部消费 |
| A2A 状态与结果保存（待补齐） | 关联本轮 Task，更新状态和 history，将 Artifact 写入独立 artifacts 表；处理重复和迟到事件 | 一份可查询、可恢复的 Task 快照 |
| GetTask 轮询（待补齐） | 每次读取已保存的 Task 当前快照 | 请求时的最新状态与结果；查询本身不推进执行，也不要求客户端消费内部事件 |
| message:stream / subscribe（待补齐） | 先返回 Task 快照，再按顺序发送已保存的状态与产物变化 | 标准 TaskStatusUpdateEvent / TaskArtifactUpdateEvent |

例如某节点完成后，转换逻辑保存对应 Task 进展。轮询客户端下一次 GetTask 读到更新，已连接的 SSE 客户端收到对应标准事件。两种出口的 Task 状态和结果必须一致；bcs_events 需要经过转换才能成为 A2A 流。includeProgress=false 时省略可选的过程文本，任务状态、错误和最终结果仍照常返回。

现有事件里的 task_id 表示 BCS 内部任务，需通过本轮执行关联找到 A2A taskId；不能直接替换字段名。sequence 只在各自 stream 内有序，聚合多个 Worker/Run 事件时，还要为本 A2A Task 保存确定的输出顺序和消费位置，并按 event_id 去重。这些是新增投影工作，不必再复制一套原始业务事件表。

现有 Eventing 提供 Webhook 投递；A2A 的 SSE 适配及内部消费查询仍需补齐。启用投影时同步启用所需事件记录，沿用业务变更与事件同事务落库的要求，并在事件保留期内完成消费；已承诺保留的 Task 结果及公开历史独立保存。任务完成仍由运行事实判定。相关职责见 [Eventing](../../crates/services/bcs-eventing/CONTEXT.md) 和 [Event Store](../../crates/services/bcs-event-store/CONTEXT.md)。

终态提交前，先关闭本轮派发并固定各源 stream 的完成边界，将该边界内应公开的过程事件投影完，再在同一数据库事务写入 artifacts 表和成功终态。SSE 按“过程事件 → 最终产物 → 终态”发送并关闭；终态后的迟到回调只进入内部追踪，不新增公开事件或覆盖产物。失败收尾无法等待失联源时，以已持久化的边界封存该轮，不无限等待未产生的事件。GetTask 始终读取同一已提交快照。

订阅已终态 Task 按 [SubscribeToTask](https://a2a-protocol.org/v1.0.1/specification/#316-subscribe-to-task) 返回 UnsupportedOperationError，客户端改用 GetTask；“已连接的订阅收到终态后关闭”和“新订阅终态 Task 被拒绝”分别处理。

### SSE 心跳与网关保活

message:stream 和 subscribe 在无业务事件时，每 15 秒发送一次 SSE 注释心跳 `: keep-alive\n\n`，通过 axum [KeepAlive](https://docs.rs/axum/0.8.9/axum/response/sse/struct.KeepAlive.html) 实现。心跳不生成 A2A 事件、不写入 Task history、不受 includeProgress 控制，也不延长执行或完成宽限；终态发送后连同流一起停止。

HTTP 适配层及时 flush，部署关闭该路径的代理缓冲，并将网关空闲超时设为大于心跳间隔且留有余量。网关的总连接时限仍独立生效，应覆盖任务执行及收尾预算，或允许客户端在断开后用已知 taskId 重新订阅。重连先获取当前 Task 快照；已终态则改用 GetTask，不重新发送业务消息。心跳不承诺断线期间事件逐条重放。

<details>
<summary>示例三：启用公开过程消息</summary>

以下请求使用 call-metadata 可选扩展，必须携带对应激活头：

```http
POST /api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:stream
Content-Type: application/a2a+json
A2A-Version: 1.0
A2A-Extensions: https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md
```

```json
{
  "message": {
    "messageId": "msg-progress-001",
    "role": "ROLE_USER",
    "parts": [
      {"text": "比较两家供应商。"},
      {"data": {"budget": 800000, "currency": "CNY"}, "mediaType": "application/json"}
    ]
  },
  "metadata": {
    "includeProgress": true
  }
}
```

SSE 顺序示例：

```text
data: {"task":{"id":"task-003","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:8c4e9b17","status":{"state":"TASK_STATE_SUBMITTED"}}}

data: {"statusUpdate":{"taskId":"task-003","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:8c4e9b17","status":{"state":"TASK_STATE_WORKING","message":{"messageId":"progress-001","role":"ROLE_AGENT","parts":[{"text":"已开始核对报价和交付周期。"}]}}}}

data: {"artifactUpdate":{"taskId":"task-003","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:8c4e9b17","artifact":{"artifactId":"result-003","parts":[{"text":"建议选择供应商 B，总价 72 万，交付周期 6 周。"}]},"lastChunk":true}}

data: {"statusUpdate":{"taskId":"task-003","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:8c4e9b17","status":{"state":"TASK_STATE_COMPLETED"}}}
```

</details>

### 取消要覆盖状态机和实际节点执行

现有 [cancel 实现](../../crates/services/bcs-collaboration-runtime/src/runtime/completion.rs) 已将 Run 标记为 Aborted、处理未活动派发并收尾服务 Session。需要补齐的是：取消意图如何传到已经启动的节点和 Bot，怎样确认它们停止，以及确认前怎样保持 Session 占用。

建议取消按以下顺序处理，执行失败触发的收尾复用同一停止通路：

1. 持久化本 Task 的取消意图，与完成提交互斥；停止生成新的派发。
2. 主从群覆盖本轮 manager、Worker 和结果回送；状态机覆盖活动节点、等待节点、定时重试及已派发 Bot 工作。
3. 对已启动工作发出取消，追踪确认；在收尾期限内保留占用，超时仍未知则按前述 FAILED 与隔离流程处理。
4. 确认本轮全部工作停止并完成收尾后，写入 `TASK_STATE_CANCELED`，释放占用。迟到输出不能改变终态或进入下一轮。

CancelTask 返回更新后的 Task。仍在停止时保留非终态，通过 status.message 给出进度；上游继续查询或订阅。断开 send 或 SSE 连接只结束本次等待或订阅。取消尚在处理中时，重复请求复用同一取消意图；已 completed/failed/canceled/rejected 则返回 TaskNotCancelableError，已清理或不可访问则返回 TaskNotFoundError。取消幂等表示重复调用不会重复产生取消副作用，响应可随状态变化。见 [CancelTask](https://a2a-protocol.org/v1.0.1/specification/#315-cancel-task) 与 [幂等规则](https://a2a-protocol.org/v1.0.1/specification/#331-idempotency)。完成与取消谁先成功提交，由同一原子状态转换决定。

<details>
<summary>示例四：取消请求与两阶段结果</summary>

```http
POST /api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-003:cancel
Content-Type: application/a2a+json
A2A-Version: 1.0
```

```json
{"id": "task-003"}
```

若已受理但节点尚未确认停止，直接返回 Task：

```json
{
  "id": "task-003",
  "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:8c4e9b17",
  "status": {
    "state": "TASK_STATE_WORKING",
    "message": {
      "messageId": "cancel-progress-003",
      "role": "ROLE_AGENT",
      "parts": [{"text": "正在停止执行，等待运行节点确认。"}]
    }
  }
}
```

确认停止后查询同一 Task：

```json
{
  "id": "task-003",
  "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:8c4e9b17",
  "status": {"state": "TASK_STATE_CANCELED"}
}
```

</details>

### 状态机多轮需要新输入和新目标

现有 [rerun HTTP 接口](../../crates/adapters/http/bcs-http/src/routes/collaboration_runs.rs) 明确禁止请求体；[rerun 实现](../../crates/services/bcs-collaboration-runtime/src/runtime/rerun.rs) 只接受 Failed Run，沿用旧 input 和定义快照，将节点重置后从入口启动。它服务于失败重试。

多轮调用需要另一种行为：同一 Session 中，带新输入从入口创建新 Run。已有 [StartStateMachineRunCommand](../../crates/service-api/bcs-service-api/src/application/collaboration_runtime.rs) 带 input，可作为入口基础；需要补齐已完成 Session 的重新激活、本轮目标传递和 A2A Task 关联。

**建议保留失败 rerun 语义，多轮改目标走新 Run。** 上游继续调用 message:send，无须调用状态机接口，也无需增加必填 goal 字段。若决定扩展 rerun 路由，还需允许已完成 Run 发起新轮次，并区分“原输入失败重试”和“新目标调用”的语义；本轮建议沿用新 Run 入口。

| 输入信息 | 来源与用途 |
| --- | --- |
| 最初目标 | 首轮输入，作为会话背景保存 |
| 本轮请求与 data | 当前 Message.parts，作为本轮执行的直接输入 |
| 上轮结果与关联 Task | 从该 context 上一轮已保存结果读取，支持补充要求和比较 |
| 上游 Agent / 用户来源（字段待定） | 本轮来源快照，进入 manager 和节点可见的上下文；继承规则随来源合同定稿 |

“预算改为 50 万”是本轮目标调整，由执行 Agent 结合背景理解。适配层只传递确定的输入，不自行理解并重写业务目标。流程节点的输入绑定和提示中都必须能看到这条新要求，不能继续只读取群创建时的固定目标。每一轮保存独立输入和输出，旧 Run 保持终态。

这里的目标输入也与 opening_message 区分：现有 opening_message 用于开场内容和面板展示；其 override 当前仅支持特定 one-shot 路径。改多轮目标应落到 Run.input 和节点输入合同。

## 5. 实现工作与模块分工

### 模块职责

| 模块 | 本期工作 | 输出合同 |
| --- | --- | --- |
| HTTP delivery adapter / bcs-http | 版本化路由；`/tasks/{task_action}` 捕获与动作后缀解析；SSE 心跳、请求体和等待上限、统一错误映射；媒体协商及扩展激活 | A2A HTTP 接口与协议一致性测试 |
| service-api application 与 Group 调用应用服务 | 资源归属、A2A Session 来源、幂等受理、原子占用、期限与终态提交；公开消息投影规则 | 与传输无关的调用、查询、订阅、取消应用合同 |
| Session / bcs-session | 复用 ServiceInvocation；重新激活、activation 关联及收尾；受管 Session 删除也遵守停止确认 | Task 到 Session/activation 的关联 |
| MessageFlow / bcs-message-flow | 来源和 data 注入；task.complete 精确关联；本轮空闲检测与一次完成提醒；公开汇总及取消确认 | 本轮进展、执行停止事实与最终结果 |
| Collaboration Runtime | 新输入启动新 Run；HumanInput 能力检查；停止派发、取消确认和超时隔离；最终输出 | Run 输入、输出、取消与恢复合同 |
| Eventing / Event Store 与调用应用服务 | 复用 bcs_events 及事务写入；补按本轮执行消费、去重、公开消息投影和消费位置恢复 | 业务事件到 A2A Task 进展的转换合同 |
| 存储实现与持久化 port | Task、ID 预留/墓碑、原始请求、占用/隔离、完成提醒和截止时间、事件边界；按确认的保留策略清理 | 原子受理、终态保存、清理和重启恢复 |
| 上游接入方 | 按 Card 调用，保存 IDs，正确区分重试与下一轮，处理 busy、查询和取消 | 标准 A2A 客户端加可选 metadata |

HTTP 层负责协议转换；执行策略放在应用服务和现有运行模块。A2A DTO 保留在适配层，跨模块应用合同使用执行语义。

axum 内部将 `get(get_task)` 与 `post(dispatch_task_action)` 合并注册在同一个 `/tasks/{task_action}` 路径，避免 GET/POST 使用不同参数名造成重叠注册冲突。GET 不拆分捕获值；POST 只匹配末尾的 `:cancel` / `:subscribe`，要求去掉后缀后的 taskId 非空，再做原有归属校验。未知动作或缺失后缀返回 HTTP 404，非法 ID 或请求体 id 与路径不一致返回 400；不再重复 URL 解码。路由契约测试覆盖两类动作、GET、空 ID、未知后缀和请求体不一致，确认标准 URL 可直接访问。

### 需要新增或扩展的存储

以下是必须持久化的记录及约束，物理表名在实现评审时与已有存储合并确定。

contextId 直接使用 session_id；不新增 context 表或 ID 映射表。

| 记录 | 最少保存内容 | 关键约束 |
| --- | --- | --- |
| A2A Task / 调用记录 | task_id、group_id、session_id、activation、完整原请求、来源快照、includeProgress、输出类型、状态、执行/收尾/完成宽限截止时间、一次提醒标记与取消意图 | 结果在独立 artifacts 表保存，下一轮不覆盖；成功终态与产物同事务提交；首轮来源标记保留至 Session 删除 |
| Artifact / artifacts 表 | task_id、artifact_id、context_id、name、description、parts、metadata、extensions、position、created_at | (task_id, artifact_id) 唯一；Part 保序并支持 text/data/raw/url；GetTask 与 SSE 共用已提交产物 |
| 消息受理与 ID 预留 | tenant、caller_principal、group_id、messageId、完整指纹、激活扩展集合、对应 task_id、结果清理标记 | 同范围原子唯一；预留随调用存储保留，Task 过期仍不再次执行；外部 caller.id 不充当认证键 |
| context 活动占用 | session_id、当前执行 task_id、执行代次/状态版本、隔离原因与恢复进度 | 一个 context 最多一份占用；受理时原子取得；Task 终态与底层停止分别确认 |
| 本轮执行关联 | task_id 到 Run、节点、delivery、manager/Worker 工作的关联及停止进度 | 重启后可继续追踪，迟到事件按轮次隔离 |
| 对外消息历史 | 原始输入、最终答复及启用后保留的过程消息、顺序 | 可用于 Task.history；按 historyLength 返回；内部群聊天继续保留在原有存储 |
| A2A 事件消费位置 | task_id、相关源 stream、消费 sequence、源 event_id、输出顺序及终态完成边界 | 复用 bcs_events；终态前完成投影，终态后不再追加公开事件 |

请求指纹使用规范化 JSON，覆盖完整语义请求及扩展激活集合，包括 configuration 和全部 metadata，includeProgress 也在其中。新请求与重试都按同一规则处理字段默认值和对象键序；服务端生成的 contextId 不反写到原请求指纹。重试保持原请求配置；查询时可单独调整 GetTask 的 historyLength。

现有 Run.input、Run.output 和 Session 能力继续复用。本期采用忙碌拒绝，不增加排队字段和调度；重启同时恢复非终态 Task 的占用和已终态 Task 的隔离记录，按持久化期限推进核实与告警。

结果保留与 ID 预留分别处理。建议终态 Task 的完整结果、输入历史和公开事件保留 7 天，自终态提交起算，读取不续期；未终态或仍处于隔离恢复中的记录暂缓清理。实际清理后，按原 messageId 重试返回 MESSAGE_RESULT_EXPIRED，直接 GetTask 返回 TaskNotFoundError。墓碑只保留去重范围、messageId、指纹、taskId 和清理标记，随目标 Group 的调用存储保留，不使用会允许 ID 重新执行的短 TTL。恢复和来源校验所需的最小记录按各自生命周期保留。具体期限与容量预算在 §7 确认。

**Artifact 已确定单独持久化。** text/data 内联保存；raw/url 使用存储插件的对象引用、大小和摘要，为非文本场景保留表达。本期 Group 公开文本与 JSON，文件能力后续开放。成功终态与 Artifact 在同一数据库事务提交；FAILED/CANCELED 可以没有产物。大对象先写入存储，数据库事务提交引用；未引用对象可清理。结果清理同时回收无引用的托管对象，失败可重试。表字段和生命周期见 [spec §8](spec.md#8-artifact-与其他持久化记录)。

### 建议交付顺序和验收

1. **先定外部合同。** 版本化路径、媒体类型、资源归属、metadata 激活、去重扩展边界、Task/context 规则和标准错误。
2. **打通受理与两类执行。** 新会话、终态后续聊、manager 完成信号、状态机新输入和独立结果持久化。
3. **补齐过程与有界收尾。** SSE/查询一致，执行期限、取消及隔离恢复可验证；重启和迟到事件不会混入下一轮。

| 验收场景 | 要观察到的结果 |
| --- | --- |
| 使用标准字段完成两轮调用 | 同一 context、两个 Task；旧结果可查询 |
| 同范围同请求重复受理；原 Task 结果清理；指纹改变 | 复用原 Task；有预留而结果已清理时报 MESSAGE_RESULT_EXPIRED；含 configuration/includeProgress 的变化报冲突；均不重复派发 |
| BCS 出站客户端首轮响应丢失且未协商 message-dedup | 报告未知，不自动重发；已有 taskId 时查询 |
| 两请求并发争用 context | 最多一项取得执行权，其余返回 CONTEXT_BUSY 且不创建新 Task |
| includeProgress 分别为 true / false | 过程消息按开关出现；状态、错误和最终结果始终可获取 |
| 同一 bcs_events 事件重复消费、消费进程重启 | 公开历史不重复，继续按本 Task 的输出顺序恢复 |
| 来源字段定稿后的跨轮调用 | 根据最终 Schema 验证上下文传递、继承与冲突 |
| 状态机下一轮把预算改为 50 万 | 新 Run 的节点消费新目标，不复用旧轮次产物替代执行 |
| 主从群和状态机各自在节点执行中取消 | 停止确认前 context 忙；确认后 canceled；允许开启下一轮 |
| Worker 失联、意外 HumanInput、取消收尾超时或重启 | 到期进入失败收尾或隔离，不无限 working；已持久化预算不重置；旧执行不能污染新一轮 |
| task.complete 指向旧 activation；manager 未提交最终汇总 | 旧完成信号拒绝；普通消息不触发 completed；正确完成信号与结果快照一同提交 |
| 终态与过程事件竞争；终态后订阅或再次取消 | 过程事件先于最终结果和终态，之后无公开迟到事件；分别报 UnsupportedOperationError / TaskNotCancelableError |
| 猜测 ID、跨调用方访问、非 A2A Session 续聊 | 查询/列表/订阅/取消/续聊都受归属约束，列表分页结果不泄漏其他主体数据 |
| 带 includeProgress 但漏激活头；来源绑定规则定稿后发生冲突 | 前者未建 Session/Task，补头可重发；后者按最终来源合同验收 |
| acceptedOutputModes 不相交；主从群接收对象 data | 前者受理前拒绝；后者保留原对象并进入 manager 上下文 |
| 同步等待超时、网关断线 | 不成功返回 working；业务执行继续；独立执行期限仍生效 |
| 标准 cancel/subscribe URL 经 axum 注册后调用 | 路由可启动且动作解析正确；未知后缀、空 ID 和路径/请求体不一致得到约定错误 |
| manager 已无待执行工作但不提交完成；或 Worker 正常长时间运行 | 前者只提醒一次并在宽限到期后失败收尾，重启不重复提醒；后者不因缺少过程消息误失败 |
| 内部事件含成员身份、工具原始返回或堆栈 | Task history、状态消息、错误、Artifact 和 SSE 都遵守同一对外字段约束 |
| 无业务事件的长 SSE 经过网关 | 15 秒注释心跳及时到达；不延长 Task 期限；终态后停止，断线可按 taskId 恢复查询 |
| 结果保留期到达、请求体超限 | 结果实际清理后原 ID 不再次执行；超限请求在受理前返回 413 |

## 6. 下一期与后续讨论

### Task 补充输入与恢复

下一期设计 input_required 的进入条件、等待提示、同 taskId 补充消息、输入校验和恢复执行，并与已有 HumanInput 的职责对齐。本期接入无需补充输入即可完成的群执行路径，群内已有 HumanInput 保持现有功能。

### 同 context 的 FIFO 排队（待定）

排队新 Task 在协议上可行：当前 Task working，后续 Task 保持 submitted，仍可满足同 context 只执行一轮。下一期先确认连续提交需求，再决定是否实现。主要成本是长任务阻塞后续请求，以及队列恢复和取消：

- 原子保存 Task、messageId 去重记录和受理顺序，重试返回同一排队项，重启保持顺序。
- 排队输入先独立保存，轮到时才进入 Session，并读取前序执行的真实结果，避免影响当前任务。
- 队首原子取得执行权；前序停止和收尾未确认时，后续保持等待。取消排队项与启动竞争要互斥。
- 明确容量、等待超时、前序失败后的推进规则，并验证重启与取消行为。

这解决同 context 的多个独立 Task 调度；同 Task append 另需输入消费与完成判定的设计。

### 自由聊天群接入（后续讨论）

初步考虑群级设置二选一：返回标准 Message（无 Task、无 Artifact），或按约定条件结束后由 LLM 总结，作为 Task 的 Artifact 持久化。一次回复何时结束、总结的触发和输入范围、响应模式如何说明都待讨论。本期不增加设置字段，详见 [spec §12](spec.md#12-后续自由聊天群与文件能力)。

### 自然语言建群

暂不考虑，因复杂度和风险较高，移出接口、助手 Agent、MCP Server 和 Fuse 深度探索的规划。

## 7. 本期安排与待确认项

| 事项 | 本期安排 | 影响 |
| --- | --- | --- |
| 入口路径与已有管理路由 | 新入站使用 `/api/v1/collaboration/a2a/...`；agent-card.json 与已有 agent-card 管理路由分别处理 | Card 的 supportedInterfaces 和上游示例同步指向真实入口 |
| 过程消息的字段与默认值 | `metadata.includeProgress`，默认 false，本轮固定 | 客户端无需知道群结构；不增加 configuration 非标准字段 |
| working context 收到无 taskId 新消息 | **已选③忙碌拒绝**，返回 409 / CONTEXT_BUSY | 本期补原子占用；② FIFO 排队列下一期待定 |
| Task 等待补充输入与恢复 | **下一期** | 本期接入无需补充输入的群执行路径 |
| 状态机改目标的方式 | 新 Task、新 Run，传入本轮 input；保留失败 rerun | 上游沿用标准消息接口；Runtime 补多轮输入通路 |
| 主从群的完成信号与最终结果 | 复用 manager task.complete，以 summary 为最终汇总；强绑定本轮 Task/activation，完成投影后提交 COMPLETED | MessageFlow、Session、事件投影共同实现；明确 manager Skill 的完成动作 |
| 取消完成的判定 | 整轮工作停止并收尾后写 canceled、释放占用 | Runtime 和 MessageFlow 都需参与 |
| 等待、执行与收尾期限 | **待确认建议值**：单次 HTTP 等待 60 秒、Task 执行 30 分钟、取消收尾 60 秒；评估默认阻塞调用的完成率后确定等待上限 | 文档和 Card.documentationUrl 明示等待上限及 504；长任务建议 true/stream；停止未知则 FAILED 并隔离 |
| manager 完成提醒 | **待确认建议值**：无待执行工作持续 60 秒，提醒一次，再给 120 秒完成宽限；恢复实际工作则退出空闲检查 | 持久化提醒与计时；提醒本身不延长整轮预算，避免等满 30 分钟才发现遗漏完成 |
| Task 结果与墓碑保留 | **待确认建议**：终态结果保留 7 天，读取不续期；未终态/隔离恢复中暂缓清理；最小墓碑随 Group 调用存储保留 | 确认容量和清理任务；实际清理后才返回 MESSAGE_RESULT_EXPIRED，ID 不重新执行 |
| 请求体大小上限 | **待确认建议值**：JSON 请求体 1 MiB，包含 message、configuration 和 metadata；网关与适配层统一限制 | 超限在解析和受理前返回 HTTP 413；接入文档列明上限 |
| 正式扩展 URI 与 Schema 地址 | **已定方向**：采用 GitHub 仓库中的版本化规范与 Schema；文件名和来源字段定稿后发布 | Card.uri 与激活头一致；Schema.$id 与 Schema 引用一致，机器获取使用原始 JSON |
| 对外内容与 SSE 保活 | 阶段模板和对外结果字段控制公开范围；FAILED 用业务原因；无业务事件时 15 秒注释心跳 | 验收内部信息不外泄、代理不缓冲；确认网关空闲/总连接时限及隔离恢复负责人 |
| 去重与既有扩展的关系 | 本期保持标准 MAY，内部做持久化去重；不发布 Bot endpoint 的 message-dedup/v1；若要求 BCS 自动重试，另行确认 Group 范围与重启语义后再启用扩展 | 指纹含全部 configuration/metadata；预留 ID 不随 Task 结果清理；客户端不擅自重发未知执行 |
| 资源归属边界 | 复用 caller_principal；本期仅接受 A2A 创建的 ServiceInvocation Session，所有 Task 操作及续聊校验归属 | 本期落实隔离；具体凭据、认证链和 Card 安全声明仍留待联合设计 |
| caller/user 的命名、结构与凭据传递 | **待定**：字段名称、内部结构、必填性及凭据所在位置，与认证机制联合设计 | Schema 定稿后同步示例；自报来源不作认证键 |
| 扩展字段的发现方式 | Card 声明 required=false 的扩展；使用字段时必须以 A2A-Extensions 激活，漏头在受理前报错；params.metadataSchema 指向仓库中的 Schema | 保留不使用扩展字段的标准调用；本扩展拥有的键由最终 Schema 指定，同名冲突在发布时解决 |
| 来源在多轮中的处理 | **候选规则**：首轮绑定、省略继承；更换或补绑定报 CONTEXT_SOURCE_CONFLICT | 与来源字段合同一起确认 |
| Artifact 独立存储 | **已定**：artifacts 表，保留非文本 Part；物理列与大对象引用方式实现评审确定 | 产物与成功终态同事务，清理包含托管对象 |
| 自由聊天群 | **后续讨论**：Message-only 或 LLM 总结为 Artifact | 结束判定、产物定义与设置方式待定 |
| 自然语言建群 | **暂不考虑** | 移出本方案规划 |

当前内容已同步 [spec](spec.md)、[overview](overview.md) 和[完整示例](examples.md)。后续确认待定项时，继续同步这些引用文档及 Schema。
