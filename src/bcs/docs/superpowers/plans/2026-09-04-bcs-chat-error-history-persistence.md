# BCS 错误消息历史持久化方案

> 状态：2026-09-14 核心实现及聚焦验证完成；全链路与完整门禁尚未全部通过（见 12.1）
>
> 日期：2026-09-11（根据 dev 最新消息拥塞控制改动更新）
>
> 前端修订：2026-09-18，基于独立 TeamClaw 新前端 worktree。下文 TeamClaw 路径均相对 TeamClaw 仓库根目录；Avernet 原 `src/frontend` 不再是本方案的前端实施落点。
>
> 范围：BCS 自由聊天与 manager-worker 会话历史
>
> 建议评审人：BCS MessageFlow、WebSocket 适配层、消息持久化、事件订阅和前端负责人

## 1. 背景与结论

实施分支：BCS 为 `codex/bcs-chat-error-history`，已 rebase 到
`origin/dev@28f9c6f23f`；TeamClaw 为独立 worktree 分支
`codex/chat-error-history`，已 rebase 到活跃迭代
`sprint_teamclaw_S090011826218_20260806@69c7b0b7`。不修改原 TeamClaw 工作目录。

BCS 当前会把下游返回的终态 `chat/error` 实时推送给 Workbench，但不会把错误消息本身写入 `bcs_messages`。因此用户当下能够看到报错，刷新页面或重新连接后，该报错就会消失，持久化历史与用户实际看到的会话不一致。

建议修复这一行为：将带有 group/session 上下文、且面向用户展示的终态错误，保存为独立的 `chat_error` 消息；历史查询时把它还原为 assistant 错误消息；同时继续禁止它参与正常的 Bot 路由和广播。

持久化内容只包含规范化后的用户可见文案与必要的关联字段，不保存完整下游 payload、堆栈、凭证或传输层细节。

最新 dev 已引入受管消息 delivery、`run_reply` 规范化回复和终态 CAS 事务。因此本方案不再假设可以通过独立写入一条消息完成修复；需要扩展现有终态提交契约，使部分回复、`chat_error` 投影和 delivery 终态在同一事务中提交。是否需要新增索引或迁移，以最终实现的幂等约束为准，不能预先承诺“无需迁移”。

## 2. 行为契约

本次修改遵循以下契约：

1. 带有 group 和 session 上下文的终态 `chat/error`，属于需要持久化的用户可见会话事实。
2. 如果错误发生前已经产生部分 assistant 文本，部分文本与错误分别持久化，不能把错误伪装成正常回复的一部分。
3. `chat_error` 不等同于成功的 `chat/final`，不得广播或转发给其他 Bot。manager-worker Task error 仅允许一个 `leg=result` 的 manager Send delivery；它发送的是任务结果语义投影，不把 `chat_error` 历史行作为 Bot 会话输入。
4. human、integration 和 admin 类型的历史查询可以读取该错误；Bot 历史查询在响应转换前过滤 `chat_error` 投影，以保持“终态错误不发送给其他 Bot”的现有运行时规则。
5. 没有 group 上下文的直接 A2A run 不写入 `bcs_messages`，其终态错误继续由 direct-chat run 状态或存储负责。
6. 状态机 node/run 失败继续由 collaboration-runtime 的持久化机制负责，本次修改不重复写入 `bcs_messages`。
7. `chat/aborted` 不转换为 `chat_error`；被取消 run 的部分文本仍按现有逻辑落库。如果后续需要持久化取消通知，应单独设计契约。

## 3. 当前行为与代码链路

### 3.1 正常终态

对于正常的 group 回复，`BcsMessageFlow::handle_bot_event` 先发布实时事件，随后由 `relay_final_chat_event` 调用 `persist_final_chat`。最终产生的 `chat` 记录会在页面刷新后通过 `MessageService` 返回。

### 3.2 错误终态

对于 `ChatEventState::Error`，同一个入口当前只会：

1. 把错误事件实时推送给前端；
2. 持久化已经缓冲的部分 chat 文本；
3. 清理 run；
4. 不持久化错误展示文案本身。

现有契约测试 `bot_error_terminal_with_display_message_only_publishes_frontend` 明确断言消息仓库为空。该断言编码了本方案要修改的旧行为，因此需要替换。

### 3.3 错误接入路径

以下终态错误来源已经进入 `MessageFlowService`：

- BCN/OpenClaw WebSocket 的 `chat.event`，且 `state=error`；
- Provider 2.0 SSE 的 `chat/error`；
- Provider callback-streaming 的 `chat/error`；
- 规范化为 `chat.event` 的旧版 Provider 终态回调。

Bot WebSocket 的 `ResponseFrame { ok: false }` 当前不会进入该链路。WebSocket 适配层会直接构造只发送到 run channel 的错误事件并注销 run。对于能够解析出有效 chat run 上下文的响应，需要把这条旁路统一转换为 MessageFlow 的应用层命令。

### 3.4 历史查询路径

新的 Chat 会话和 manager-worker 会话统一通过 `MessageRepoPort` 查询历史。本方案不再覆盖已废弃的 legacy Bot-history fallback；历史回放、分页和权限行为均以新的 MessageRepoPort 查询链路为唯一准入。

## 4. 目标

- 页面刷新或重新连接后，用户仍能看到此前出现的错误。
- 明确区分成功的 assistant 回复与失败的 run。
- 如果错误前已经输出部分 assistant 文本，刷新后仍保持“部分文本 → 终态错误”的顺序。
- 所有受支持的下游错误接入路径表现一致。
- 保持现有受众和路由边界，不触发新的 Bot 广播或 Bot 回复。
- 同一个终态 run 的持久化可重试且具备幂等性。
- 兼容最新受管 delivery 的原子提交、状态版本 CAS 和持久化退避；回滚后已有 `chat_error` 仍可读取。

## 5. 非目标

- 回填部署前已经发生的错误。
- 把直接 A2A 错误写入 group history。
- 修改状态机失败历史。
- 把 `chat/aborted` 持久化为错误。
- 保存原始 Provider/engine payload、堆栈、请求体、端点信息或凭证。
- 新增公开的顶层 `GroupMessage` role 或 message-type 枚举。
- 新增 `chat.failed`、`run.failed` 等公开事件类型。

## 6. 持久化模型

增加一个共享的内部存储类型常量：

```rust
pub const CHAT_ERROR_MESSAGE_TYPE: &str = "chat_error";
```

建议写入以下 `bcs_messages` 字段：

| 字段 | 值 |
| --- | --- |
| `group_id` | 解析后的 BCS group ID |
| `session_id` | 解析后的 BCS session ID |
| `sender_id` | 产生或上报错误的 Bot |
| `sender_type` | `bot` |
| `message_type` | `chat_error` |
| `content` | 只包含规范化用户可见文案的 JSON 字符串 |
| `client_msg_id` | 确定性的幂等键 `chat-error:<run_id>` |
| `owner_bot_id` | 与该 Bot 正常输出一致的可见性计算结果 |
| `run_id` | BCS 规范 run ID |
| `created_at` | BCS 接收时间 |

`content` 继续使用 JSON 字符串，不新增对象 schema。对于受管 delivery，错误投影是主记录，不能作为 `run_reply` 的 display companion：`run_reply` 仍只保存规范化成功回复全文。本次显式扩展 admission 的 companion 契约，允许“前序部分 `chat` + 主 `chat_error`”一起提交，不生成错误 run_reply。旧版读取端即使不认识 `chat_error`，也能通过未知类型 fallback 将其显示为 assistant 文本，只是没有错误样式。

首版修复不要求把 `errorKind` 和 `errorCode` 写入持久化记录。它们可以继续用于实时诊断和日志；`run_id` 作为持久化关联键。如果产品后续确实需要长期保留错误分类，应通过显式、版本化的 metadata 契约增加，不能通过保存完整 payload 实现。

### 6.1 `message.created` 事件语义

`chat_error` 是终态失败在 Workbench 历史中的投影，不是正常路由的聊天消息。首版实现不能隐式地为它发送普通 `message.created` webhook。

当前持久化 helper 把 session 解析和 `message.created` 事件生成都绑定在 `message_type == "chat"` 条件上。实现时应拆开这两个判断：

- `chat` 和 `chat_error` 都必须具备真实的 session 上下文；
- 只有事件目录明确覆盖的 message type 才生成 `message.created`；
- 本方案暂不把 `chat_error` 加入该事件目录。

如果评审人认为外部订阅者也应该收到终态失败，应另行定义事件目录和契约，不能让本次修复通过普通聊天消息的副作用完成通知。

## 7. 用户可见文案规范化

在应用层增加唯一的错误展示文案规范化 helper，并让实时推送和持久化共用其输出。

文案选择顺序：

1. 非空的 `message.content[].text`；
2. 非空的 `errorMessage`；
3. 兼容旧字段的非空 `error_message`；
4. 稳定、面向用户的通用失败提示。诊断关联使用独立 run_id 字段，不把内部标识拼进正文。

该 helper 必须满足：

- 只在判断空值时去除首尾空白，不随意修改正文；
- 不得把整个事件 payload 序列化为 fallback 文案；
- 不得把堆栈、传输对象或请求对象复制到持久化文案；
- 如果下游 frame 没有可展示消息，应补全 `message.role=assistant`、`message.content[].text` 和 `errorMessage`，确保实时视图与历史回放显示相同内容；
- 如果引入展示长度限制，截断必须保证 UTF-8 安全。

现有下游协议已经把 `errorMessage` 定义为 `chat/error` 的展示 fallback。本方案只是把这一用户可见语义进一步持久化。

## 8. 处理流程与顺序

最新 dev 的受管 delivery 路径优先于下述旧的直接 MessageFlow 路径：先完成受管终态的数据库事务，再执行实时通知、channel delivery 和 run 清理。队列关闭或未纳管的 Bot 才走直接路径；关闭准入不会把已有 pending work 静默旁路到 legacy delivery。

### 8.1 非任务 group run

对于非受管的非任务 `chat/error`：

1. 解析并规范化用户可见错误文案；
2. 按现有逻辑把已缓冲的部分文本写为 `chat`；
3. 使用确定性的 `client_msg_id` 写入 `chat_error`；
4. 发布规范化后的实时错误事件；
5. 使用现有安全的通用 channel 渲染逻辑完成 channel delivery；
6. 标记或移除 active send context，并注销 frontend run；
7. 清理该 run 的 message tracker；
8. 通知 terminal observer。

持久化顺序必须为：

```text
chat（部分 assistant 文本） -> chat_error（终态展示文案）
```

错误分支必须直接返回，不能继续进入 `relay_final_chat_event`，以免产生 Bot 广播或增加正常 final delivery 结果。

### 8.2 Manager-worker 任务 run

保留 `handle_task_bot_event` 中现有的校验与交付边界：只有仍处于 dispatched 状态的任务所分配的 worker 才能完成任务，并且任务结果必须先成功送达 manager，再提交历史记录。

对于校验通过的任务终态错误：

- worker 已缓冲的部分文本仍保存为 `chat`，并保持现有 owner；
- 已有的 manager 可见 task-result 历史行改为 `chat_error`，不额外增加第二条错误记录；
- task-result 文案使用规范化错误文本，不能退化为 `[no response]`；
- 任务分支处理前不得先执行非任务错误持久化，避免重复写入。

这样可以保留现有 manager 通知语义，同时保证刷新后能识别该结果是失败，而不是正常回复。

### 8.3 WebSocket 请求拒绝

对于属于 active chat run 的 Bot WebSocket `ResponseFrame { ok: false }`：

1. 通过 `BotRunContextPort` 解析规范 Bot/group/session 上下文；
2. 校验返回响应的连接是否属于预期 Bot；
3. 把响应错误转换为标准应用层 `BotEventCommand`，设置 `ChatEventState::Error`、`errorMessage` 和 assistant 展示消息；
4. 调用 `MessageFlowService::handle_bot_event`；
5. 删除适配层针对该场景的直接 run-channel 发送和注销逻辑。

`FrontendDeliveryPort` 已经负责 session/group 投递和 run fallback，因此适配层不能再重复发送同一个合成错误。

如果响应不属于带 group/session 上下文的 chat run，则保留现有 one-shot/direct-response 处理，不虚构 group history 记录。

### 8.4 持久化失败

持久化必须在终态 run/tracker 清理前完成。对于受管 delivery，错误投影必须和 `Failed` 状态、已缓冲回复在同一个 delivery transaction 中提交；不能先提交 delivery 终态、再单独补写 `chat_error`。写入失败时：

- 通过现有 service error 路径返回存储错误；
- 不得报告终态接收成功；
- 不得注销 run 或永久标记其已经终止；
- 必须保留或恢复重试所需的部分文本缓冲；
- 使用相同 run 重试时继续采用 `client_msg_id=chat-error:<run_id>`，返回已有记录，不能追加重复终态错误。

受管路径复用最新 dev 的 delivery event lock、状态版本 CAS 和 storage-only backoff；重试只重试数据库操作，不重放网络发送。`client_msg_id=chat-error:<run_id>` 仍是业务幂等键，但必须由事务内的唯一性约束或等价的数据库冲突处理兜底，不能仅依赖“先查后写”。本方案不承诺跨进程崩溃场景的 exactly-once；若最终实现需要新增唯一索引，应同步补充 MySQL/OceanBase 与 SQLite 迁移及回滚说明。

实现选择：非受管 SQL append 使用 env/group/session/sender/run 的 SHA-256 生成
64 字符 message_id，沿用数据库现有主键；冲突或提交响应丢失后读取该主键确认结果，
不能读取到已提交记录时仍返回写入错误。Memory 仓储在写锁内执行同一 run 唯一性规则。
受管路径仍由原 delivery 的 CAS 事务裁决，因此本次没有新增 schema migration。
任务结果交付成功后保留进程内标记，历史写入重试不重发 manager task result；该标记不跨重启。

## 9. 历史投影与访问范围

`bcs-message::persisted_to_group_message` 将 `chat_error` 映射到现有公开 `GroupMessage` 结构：

```json
{
  "message_type": "bot",
  "role": "assistant",
  "content": "<规范化错误文案>",
  "run_id": "<BCS 规范 run ID>",
  "metadata": {
    "terminal_state": "error",
    "is_error": true
  }
}
```

公开的 `message_type` 继续使用 `bot`，无需扩展枚举。V1 契约中的 `metadata` 已经是可扩展对象，需要补充这两个字段的文档，避免消费者根据文案猜测失败状态。

历史查询行为：

- human、integration 和 admin 调用方可以收到 `chat_error`；
- Bot 调用方在持久化查询后、响应转换前过滤该投影；
- 新的 MessageRepoPort group/session 历史查询要按时间或 session sequence 合并该记录；
- 存在过滤记录时，分页 cursor 仍应基于持久化页计算并保持单调；
- 数据库/API 保留部分回复与错误的独立记录；TeamClaw 将同一 session、Bot、run 的记录聚合展示，终态保持失败。

### 9.1 TeamClaw 当前接入链路与缺口

- 历史链路：`services/backendApi/collaboration/sessionController.ts::listSessionMessages` → `services/workspace/groupChatHistoryPaginator.ts` → `groupMessageMapper.ts::mapGroupHistoryMessages` → `groupChatProvider.ts` 的 hydration → `pages/Workspace/hooks/useManifestHistoryLoader.ts` / `useGroupChat.ts` → `components/GroupChatPane/GroupChatMessageList.tsx` / `GroupChatBubble.tsx`（页面内路径均从 `src/` 起算）。只消费新的 session messages API，不接入 legacy Bot history。
- `SessionMessageData` 当前未显式声明 metadata；Mapper 当前把非 pending 消息设为 `history`，仅 pending 设为 `streaming`，没有终态错误映射。
- Mapper 已按 `run_id + sender` 合并同页消息，支持其他 Bot/run 穿插；但分页逐页映射，Hook 再按消息 ID 前置去重，尚不能据此保证跨页同 run 聚合。
- `isToolResult` 把 `metadata.is_error` 也作为工具消息信号。若直接传入本方案的 `is_error: true`，独立错误可能被当作无 tool ID 的工具结果，导致正文不进入 blocks。这是本次必须覆盖的回归点。
- `GroupChatBubble` 给 SDK `Bubble` 传 blocks 和 isStreaming，并未传消息 status；`GroupChatRunStatus` 只处理已终止/输出中。仅设置 `status='error'` 不足以保证失败样式。
- 实时链路委托 `@tc-chat/adapters` 的 GroupChatProvider。当前依赖源码的错误回调产生 `status='error'` 和错误文本块；必须验证它与已有部分回复的合并，不能假定错误回调天然保留完整 blocks。

### 9.2 TeamClaw 目标行为

1. 在 API DTO 中显式声明终态 metadata。Service Mapper 首先判断 `metadata.terminal_state === 'error'`，转换为 `ChatMessage.status='error'`，然后才处理 tool/pending。保留现有契约中的 `is_error`，但它不能单独决定 run 失败；工具错误仍是工具块，不提升为整次运行失败。正常消息没有 terminal_state 时保持现有行为。
2. 统一聚合键为 `(session_id, sender/Bot ID, canonical run_id)`，不使用时间窗口、不要求相邻。缺少 run ID 时按原始消息 ID 独立展示，不猜测归属。聚合后的内容顺序为已有文本/工具块 → 错误文案，显示为一条失败的 Bot 消息；错误正文只出现一次，不能替换已经输出的部分回复。
3. Service 层保留原始记录 ID 用于去重，并统一处理首屏、跨页、实时/历史合并；Hook 只编排加载与 `setMessages`，不新增 DTO 规则。分页游标基于原始响应页或接口提供的游标计算，不能从聚合后的气泡数、ID 或时间反推。分页边界重复记录不重复追加正文，旧 pending 不能把终态 error 改回 streaming。

   实施补充：现有 V1 `before` 为排他毫秒时间游标，部分 chat 与 error 在同事务中可具有相同时间戳。向前翻页以 `before=边界毫秒+1` 重读边界，再按原始记录 ID 去重；满页且不前进时将 limit 从 50 扩至 V1 上限 100。若 100 条仍在同毫秒，明确报错并保留游标与可重试状态，不静默宣告历史结束。超过此上限的完整遍历需要后续复合游标契约，本次不声称已消除该既有接口限制。
4. 沿用 `beginHistoryHydration` → 加载/映射 → `hydrateRun` → `enterLiveMode` 的生命周期；刷新、重连、视角切换必须保留错误状态和归属。真实终态错误与连接/历史请求失败分别处理：后两者继续走连接或加载错误提示，不制造会话错误记录。
5. 复用 `GroupChatBubble`、`MessageSenderMeta`、现有复制操作和 blocks 渲染；在 `GroupChatRunStatus` 增加可见的“回复失败”状态，使用 `text-destructive` 等项目语义 token，不新增 UI 依赖。错误文案作为聚合消息的末尾文本块保留；不能只弹一次性 toast。error/aborted 消息明确关闭光标、输出中和停止操作，不能被全局 `isRequesting` 的最后一条消息兜底重新标为流式；其他 Bot 正在输出时不受影响。
6. 本次不新增自动重试、换模型重试或消息重发按钮。错误回放属于 Open Core；遵循 TeamClaw 分层和 shadcn/Tailwind 规范，不引入旧前端代码或内部专属依赖。

## 10. 实施任务

### 任务 1：定义并记录持久化契约

涉及文件：

- 修改：`crates/contracts/bcs-domain/src/message.rs`
- 修改：`docs/bcs-provider-2.0-sse-protocol.md`
- 修改：`api-contracts/v1/domain-models.yaml`

- [ ] 增加并导出 `CHAT_ERROR_MESSAGE_TYPE`。
- [ ] 记录哪些带 group 上下文的 `chat/error` 需要持久化。
- [ ] 记录 `GroupMessage.metadata.terminal_state` 和 `is_error`。
- [ ] 明确 direct A2A、状态机和 `chat/aborted` 不在本次修改范围内。

### 任务 2：在 MessageFlow 中规范化并持久化终态错误

涉及文件：

- 修改：`crates/services/bcs-message-flow/src/bot_event.rs`
- 按需修改：`crates/services/bcs-message-flow/src/group_flow.rs`
- 按需修改：`crates/services/bcs-message-flow/src/message_tracker.rs`
- 测试：`crates/services/bcs-message-flow/tests/contract_bot_event.rs`

- [ ] 增加唯一的用户可见错误文案规范化 helper。
- [ ] 增加 `persist_chat_error` helper，复用现有 owner 计算并使用确定性 client message ID。
- [ ] 扩展受管 delivery 的 admission/terminal transaction，使前序 `chat`、独立 `chat_error` 和 `Failed` 状态一次提交；`run_reply` 继续只保存规范化成功回复全文。
- [ ] 在 `try_persist_group_message` 中拆开 session scope 解析与普通 `message.created` 事件生成。
- [ ] 调整错误终态顺序：先持久化，再清理；实时投递与持久化使用相同的规范化 payload。
- [ ] 将部分 chat 文本保存为独立的前序记录。
- [ ] 部分文本持久化失败时保留或恢复 pending chat 状态。
- [ ] 修改任务终态分支，避免生成重复错误行。
- [ ] 非任务错误的 `bot_deliveries` 必须保持为空。
- [ ] 复用 delivery event lock、状态版本 CAS 和 storage-only backoff；不得因数据库重试重复网络投递。

### 任务 3：将 WebSocket 请求拒绝统一接入 MessageFlow

涉及文件：

- 修改：`crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs`
- 测试：最接近的 `bcs-ws` dispatcher/frame 契约测试模块

- [ ] 合成终态错误前解析 active run scope。
- [ ] 将带上下文的 chat rejection 转换为 `BotEventCommand`。
- [ ] 删除已规范化场景中的直接 run-channel 投递。
- [ ] 没有 group scope 时保留现有 direct/one-shot fallback。
- [ ] 区分 scope 解析失败与确实无 scope：解析失败记录 warning 并保留错误，不得伪造 group history。
- [ ] 验证前端事件和 run 注销均不会重复发生。

### 任务 4：通过 MessageRepoPort 历史路径回放错误

涉及文件：

- 修改：`crates/services/bcs-message/src/lib.rs`
- 新增或修改测试：`crates/services/bcs-message/src/lib.rs`

- [ ] 将 `chat_error` 转换为 assistant 内容及稳定的错误 metadata。
- [ ] 对 Bot 调用方排除该投影。
- [ ] 在新存储的 group 和 session 历史中包含该记录。
- [ ] 保持 MessageRepoPort 查询的排序、owner 过滤、limit 和 cursor 行为。
- [ ] 确认当前读取端可以通过未知类型 fallback 显示字符串内容。

### 任务 5：在独立 TeamClaw 前端恢复错误状态和展示

以下路径相对 TeamClaw 根目录，不是 Avernet `src/frontend`：

- DTO/转换：`src/services/backendApi/collaboration/sessionController.ts`、`src/services/workspace/groupMessageMapper.ts`。
- 分页/回填：`src/services/workspace/groupChatHistoryPaginator.ts`、`groupChatProvider.ts`，以及 `src/pages/Workspace/hooks/useGroupChat.ts`、`groupChatHistoryUtils.ts`、`useManifestHistoryLoader.ts` 的相关集成点；新增合并规则放 Service，不放 Hook。
- 展示：`src/pages/Workspace/components/GroupChatPane/GroupChatBubble.tsx`、`GroupChatRunStatus.tsx`、必要时 `GroupChatMessageList.tsx`。
- 测试仅放 TeamClaw `test/**`，扩展既有 `test/services/workspace/groupMessageMapper.test.ts`、`groupChatProvider.test.ts`、`test/pages/Workspace/hooks/useGroupChat.test.ts` 及 GroupChatPane 组件测试；新增分页器和跨页合并聚焦测试。

- [ ] TeamClaw Spec 标记 Open Core，并按其仓库流程在 approved 后实现业务代码。
- [ ] 补 DTO metadata，先识别终态错误再识别工具，映射为 SDK 已有的 `error` 状态，保留错误正文和 Bot/run 关联。
- [ ] 完成 Service 层跨页合并/记录去重，避免普通块或 pending 覆盖失败状态；保持原始页游标和滚动位置。
- [ ] 验证实时 error 与历史 hydration 合流不重复、不吞部分文本；如问题位于 SDK，应在 `@tc-chat/adapters` 源码仓库提交聚焦修复并更新依赖，不编辑 node_modules 或复制整套 parser。当前本地依赖指向相邻 `Teamclaw-Chat-Packages/packages/adapters`，交付时须提供可复现的依赖版本。
- [ ] 增加持久的“回复失败”状态，关闭该 run 的流式/停止 UI；保留其他并发 Bot 的运行状态和现有复制操作。
- [ ] 验证刷新、切回会话、重连和视角切换后 Bot 名称、时间戳、run ID、部分文本和错误文案均正确。

### 任务 6：补充契约与端到端覆盖

涉及文件：

- 修改 `crates/adapters/http/bcs-http/tests/` 下相关 HTTP/session 契约测试。
- 如果现有 group refresh 用户故事能够覆盖该路径，则修改相关 BCS E2E。

- [ ] 证明错误接收后，session messages 接口可以返回该错误。
- [ ] 证明 Bot 身份的历史调用方收不到 Workbench 专用错误投影。
- [ ] 证明 human 查看者只能看到现有 owner 过滤允许的错误。
- [ ] 证明终态提交后，刷新或重连不依赖进程内 pending tracker。
- [ ] 证明受管 delivery 的错误投影与失败状态原子提交；模拟提交结果不明、CAS 冲突、数据库短暂故障和队列 drain。

## 11. 测试矩阵

| 场景 | 预期持久化结果 | 预期实时或路由结果 |
| --- | --- | --- |
| 仅错误终态，包含 `message` | 一条 `chat_error` | 一次前端错误；不转发给 Bot |
| 仅错误终态，只包含 `errorMessage` | 一条规范化 `chat_error` | 实时和刷新后的文案一致 |
| 错误不含展示文案 | 一条安全的通用 `chat_error` | 实时和刷新后使用相同通用文案 |
| 两个 delta 后报错 | 一条部分 `chat`，随后一条 `chat_error` | 先展示部分文本，再展示错误 |
| tool result、后续文本、再报错 | 保留已有 tool/chat 行，最后一条 `chat_error` | 顺序保持不变 |
| 重复终态回调 | 只有一条终态错误 | 不重复渲染终态错误 |
| 持久化失败 | 不执行成功终态清理，可以重试 | 接收接口返回失败 |
| DB 失败后重试 | 复用已有部分记录，只写一条错误 | 最终只渲染一次错误 |
| WS `ResponseFrame ok=false` | 与 `chat/error` 相同 | 适配层与 MessageFlow 不重复投递 |
| Manager 错误 | 按现有 manager 规则决定公开范围 | 不产生 peer broadcast 副作用 |
| Worker task 错误 | 保持现有 worker/manager 可见性 | manager 只收到一次任务失败 |
| Bot 历史调用方 | 过滤错误投影 | Bot 上下文不变化 |
| Human 刷新页面 | 返回错误投影 | 错误继续可见 |
| TeamClaw 仅错误 + is_error | 正文和 terminal_state 均返回 | 不误判为工具，不丢正文，status=error |
| 部分回复与错误跨页/夹有其他 Bot | 原始记录保持独立有序 | 同 session/Bot/run 合并一次；其他 Bot 不合并 |
| 历史 hydration 与实时错误重叠 | 同一终态事实 | 不重复、不吞部分文本，旧 pending 不覆盖 error |
| 工具失败但 run 正常完成 | 保持原有工具记录 | 仅工具块失败，不误标 run 失败 |
| 一个 Bot 失败、另一个仍输出 | 各自 run 独立 | 失败 Bot 无光标/输出中/停止按钮，另一个继续流式 |
| Direct A2A 错误 | 不写 `bcs_messages` | direct run 状态包含错误 |
| 状态机失败 | 不增加 `chat_error` | 保持 runtime 自有失败逻辑 |
| `chat/aborted` | 不写 `chat_error`，只保存部分文本 | 保持现有 abort 行为 |

## 12. 验证命令

先执行聚焦测试：

```bash
cd src/bcs
cargo test -p bcs-message-flow --test contract_bot_event
cargo test -p bcs-message
cargo test -p bcs-ws
cargo test -p bcs-http
```

修改 metadata 文档后执行相关 OpenAPI/契约检查：

```bash
cd src/bcs
python3 -m pytest tests/openapi/test_session_v1_contract.py
```

前端验证在独立 TeamClaw 仓库执行，不运行旧 Avernet 前端测试代替验收：

```bash
cd /Users/ray/ant/projects/teamclaw-chat-error-history
npm run typecheck
npm run lint
npm test -- --runInBand test/services/workspace/groupMessageMapper.test.ts test/services/workspace/groupChatProvider.test.ts test/pages/Workspace/hooks/useGroupChat.test.ts test/pages/Workspace/components/GroupChatPane
npm run ci
```

新增分页/合并测试也必须运行；`npm run ci` 不替代 Jest 测试。按 TeamClaw 门禁运行全量测试、Open Core source/dependency 检查。实现后浏览器验收真实 session messages 与实时错误链路：首屏、刷新、跨页、重连、视角切换和多 Bot 并发；检查失败状态、正文不重复、部分文本保留及无残留流式动画。不要为本次修复安装新的 UI 库；SDK 本地依赖不可用时如实记录未验证项，不用私有包替代公开交付要求。

合并前执行仓库 pre-push 工作流选中的 BCS module gate。如果修改影响 BCS user-story E2E，应运行统一的 singlebox coverage 入口，不能单独搭建临时测试栈替代。

### 12.1 实际实施与验证记录（2026-09-14）

- 已实现受管错误的部分文本、`chat_error` 与 Failed CAS 原子提交；故障注入覆盖提交前失败、提交后响应失败、调用方中断和重复终态。非受管错误使用作用域摘要作为既有数据库主键并以唯一约束兜底；本次无需 schema 迁移。manager-result 保持先交付再写历史，进程内重试不重复交付已成功结果。
- BCS：`cargo test -p bcs-message-flow -p bcs-message-store -p bcs-message -p bcs-ws -p bcs-http -p bcs-api-http --quiet` 通过；后续新增故障/并发测试再次通过对应包测试。WS 中 14 条既有 ignored 测试未运行。session V1 OpenAPI 聚焦测试 16 条通过。
- OpenAPI/事件契约全量检查为 75 通过、5 失败；已在未修改的 HEAD 快照中复现完全相同的 5 个失败（operation inventory、claim security 等），未以修改基线断言方式消除它们。
- 架构检查为 5 通过、5 跳过、5 失败，涉及检查脚本 Bash 兼容性、既有 import/trait 命名及 conformance 发现；未完整重放其基线，不能宣称架构门禁通过。
- TeamClaw rebase 到最新迭代后：18 个聚焦 Jest suite、120 条测试通过，覆盖 Mapper、Provider、分页器、终态投影、Hook 和 GroupChatPane；typecheck、lint、静态 guardrails 通过。保留迭代已发布 SDK，未修改 node_modules 或依赖声明。
- 按 Bigfish 技能完成受控浏览器验证并检查截图：真实前端组件展示部分回复、一次错误正文与“回复失败”，刷新保持一致，其他 Bot 可继续流式输出。临时页面和脚本已清理；该检查不替代真实后端 E2E。
- 前端全量 Jest 两次运行出现持续高 CPU、无 suite 结果，首次超过 9 分钟后终止；聚焦测试正常，未确定全量停滞原因。Open Core 源码导出检查通过，但其干净依赖安装遇到 ECONNRESET，构建未完成。完整 `npm run ci` 尚未完成。
- 尚未执行真实 MySQL/OceanBase、统一 singlebox coverage，以及真实 BaaS→BCS→TeamClaw 的刷新/重连/视角切换联合验收；SQLite 并发测试不替代这些环境验证。

## 13. 兼容性、安全与回滚

### 13.1 兼容性

- 数据库：无 DDL 变更。
- 接入协议：不变，继续接收现有 `chat/error` 字段。
- 公开历史接口：只增加 metadata，现有顶层枚举不变。
- 事件订阅：不增加事件类型，也不为 `chat_error` 隐式生成普通 `message.created`。
- 旧版前后端读取：将字符串作为普通 assistant 文本显示；即使缺少错误样式，错误内容仍然可见。

### 13.2 安全与隐私

- 只持久化原本就准备在 Workbench 展示的规范化文本。
- 不得持久化完整事件 payload 或序列化后的错误对象。
- 不增加包含完整错误正文的日志；只记录 run/group/session、安全的 error code、持久化结果和 message ID。
- 复用现有 owner 与 caller 过滤规则，不能意外公开 worker 私有失败。
- 对 Bot 历史过滤 UI 专用错误投影，避免 prompt/context 漂移。

### 13.3 回滚

回滚 writer 后只会停止生成新的 `chat_error`。已有记录仍然是合法的 `bcs_messages` 数据；当前未知类型 fallback 可以显示其中的字符串，因此不需要清理迁移。

## 14. 可观测性

在终态错误持久化边界增加结构化成功或失败日志，包含：

- `group_id`；
- `session_id`；
- `run_id`；
- `bot_id`；
- 成功时的 `message_id`；
- 失败时安全的 `error_code` 和存储错误分类。

不得记录完整下游错误 payload。现有 MessageFlow 与 delivery 指标应继续区分 frontend delivery 和 Bot delivery；错误持久化成功不能增加“Bot 正常回复成功”的计数。

## 15. CR 检查清单

请评审人明确确认以下事项：

- [ ] `chat_error` 是合适的内部持久化类型，且不会被当作正常成功 chat。
- [ ] 错误投影面向 human，并会从 Bot 历史中被过滤。
- [ ] direct A2A 和状态机不在修改范围内是合理的。
- [ ] `chat/aborted` 保持在本次范围之外。
- [ ] 首版有意不发送 `message.created`。
- [ ] 持久化字符串不包含原始 payload、堆栈或凭证。
- [ ] 任务和非任务分支不会重复写入同一个错误。
- [ ] 只有持久化及投递达到预期终态边界后才执行清理。
- [ ] MessageRepoPort session history 能回放新写入的错误。
- [ ] 已覆盖失败重试和确定性 `client_msg_id`。
- [ ] 不需要数据库迁移或架构豁免。
- [ ] 若幂等唯一性需要新增索引，已补齐 MySQL/OceanBase 与 SQLite migration；否则已证明等价的事务冲突兜底。

## 16. 建议交付方式

BCS 与 TeamClaw 为独立仓库，分别交付聚焦的修复 PR，互相链接本方案和联调结果；若涉及 SDK 修复，另外关联 SDK 变更及依赖版本。BCS PR 建议标题为：

```text
fix(bcs): persist user-visible chat errors in session history
```

PR 描述应明确说明这是一次有意的行为契约变更，列出 MessageFlow、WebSocket 接入、MessageStore 历史投影、Workbench 和事件订阅等受影响方，并附上第 12 节中的实际验证结果。
