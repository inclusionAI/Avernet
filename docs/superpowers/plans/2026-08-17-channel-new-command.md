# BCS Channel `/new` 命令：归档当前会话并开启全新会话

> 状态：已实现（2026-08-17，worktree `bcs-channel-new-command`）
> 设计/分析过程见本文档；实现位于 `src/bcs/crates/services/bcs-channel/src/commands.rs` + `lib.rs` 挂载点。

## Context

Channel（钉钉等 IM）用户的 chat session 一旦创建就**永不自动结束**（timeout_scanner 只覆盖 service_invocation，chat 会话无超时），同一 IM 会话里的消息永远进入同一个 BCS session，上下文无限累积。`/new` 让用户主动开启全新会话。

**需求语义**（与需求方确认）：
1. `/new` 后旧会话**归档**（Running → Completed，历史保留可查），后续消息进入全新会话
2. **所有 channel 统一支持** —— 在 bcs-channel 服务层实现（DingTalk provider 在仓库外）
3. 当前会话有 agent run 在途时**排队等待**：先回复"任务运行中"，run 结束后自动执行切换

## 关键架构事实

- **唯一拦截点**：`BcsChannelService::handle_inbound`（`bcs-channel/src/lib.rs`），所有 channel 文本消息的唯一入口
- **session id 构造**：`new_session_id`（`bcs-service-api/src/core/session.rs`）= `{group_id}:{8位随机hex}`。`/new` 不改变构造规则也不改 group_id —— 下一条消息走既有 `sessions.create` 生成"同 group 前缀 + 新随机后缀"的 id；新 id 天然带来映射覆盖、bot 侧隔离（`session_key = session_id`，bcs-message-flow/group_flow.rs）、历史按旧 id 保留的效果
- **归档原语**：`SessionRepoPort::complete_if_running`（CAS，幂等，保留 bcs_messages 历史）
- **懒 rollover**：映射原地保留作为触发器；`resolve_or_create_chat_session` / `start_state_machine_from_inbound` 在映射指向的 session 非 Running 时自动创建新 session 并 upsert 重指映射，无需 `delete_if_session`
- **Run 终态信号**：
  - chat run → `ChatFinal`（或 `System`+`purpose:Conversation`+`raw_payload.state ∈ {"error","aborted"}`，见 bcs-message-flow/bot_event.rs `channel_event_kind`/`try_channel_outbound`）流经 `try_outbound`
  - state-machine run → `publish_state_machine_terminal` 每次终态必被调用（即使无 IM 通知），**不**经过 try_outbound —— 两个 hook 点都要
- **旧会话迟到回复仍可送达**：`try_outbound` 经 `list_by_bcs_session` + `channel_route_from_session_meta` 兜底，归档不打断在途 run 的回复投递

## 实现

### `commands.rs`（新文件）

- `parse_channel_command`：仅精确匹配 `text.trim() == "/new"`；match 结构便于后续扩展
- `SessionResetTracker`（仿 `InboundDedupGuard` 的 tokio Mutex + FIFO 有界）：
  - `seed_runs`（dispatch 后播种 active_run_ids）、`active_run_ids`（/new 时快照）
  - `begin_pending`（同 conversation 幂等）、`take_pending`（竞态闭合用）、`take_pending_if_stale`（30min bot 假死兜底）、`observe_run_terminal`（终态扣减 waiting_on，清空时返回待执行重置）
  - **快照语义**：/new 之后新起的 run 不延长等待
- `impl BcsChannelService`：
  - `try_execute_channel_command`：解析 → resolve_inbound_context → 分发；位于 `try_consume_human_input` **之前**（HumanInput 卡片不得吞掉 /new）
  - `execute_new_session`：重复 /new 幂等重答 → 无会话答 NOTHING → state-machine <30s 启动窗口答"流程正在启动" → 收集 waiting_on（chat 快照 + SM run 视图）→ 空则立即执行，否则登记 pending 并**重查竞态闭合**后答 QUEUED
  - `execute_session_reset`：持 `chat_session_resolution_lock` 复查映射仍指向旧会话 → `complete_if_running`（output `{"reason":"channel_command_new"}`）→ provider 直接投递确认（仿 `deliver_human_input_event`；失败仅 warn!，归档已持久化；binding 下线则跳过回复）
  - `observe_outbound_terminal` / `observe_state_machine_terminal` / `maybe_execute_stale_reset`（30min 兜底，入站顺带执行，无后台扫描器）
- 回复经 **provider 直接投递**而非 try_outbound：无会话场景（/new 为首条消息）没有可供路由的 session；`kind: System`、`purpose: HumanInputAck`、`raw_payload: {"type":"channel.command","command":"new"}`、`source_im_message_id` 指向 /new 的 msg_id

### `lib.rs` 挂载点（8 处，无重构）

1. `mod commands;`；2. struct + 构造函数增加 `session_reset_tracker` / `chat_session_resolution_lock`（签名不变）；
3. `handle_inbound` 在 actor 解析后、`try_consume_human_input` 前插入命令拦截（dedup 失败忘记机制自然覆盖命令失败）；
4. context 解析后插入 `maybe_execute_stale_reset`；
5. `resolve_or_create_chat_session` + `conversations.upsert` 包入 `chat_session_resolution_lock`（关闭"并发消息同见 Completed 双双建会话"竞态；dispatch 在锁外）；
6. dispatch 成功后 `seed_runs`；
7. `try_outbound` 在 `source_is_channel` 守卫后调 `observe_outbound_terminal`（所有提前 return 之前）；
8. `publish_state_machine_terminal` 首句调 `observe_state_machine_terminal`。

### 回复文案

| 场景 | 文案 |
|---|---|
| 重置完成（即时/deferred） | `已开启全新会话，此前的聊天记录已归档。` |
| 已排队（含重复 /new 幂等回复） | `当前任务仍在运行中，任务结束后将自动开启新会话。` |
| 无会话可重置 | `当前没有进行中的会话，直接发送消息即可开始新会话。` |
| state-machine 启动窗口 | `流程正在启动，请稍后再试。`（复用现有文案） |

### 明确的取舍 / 边界

- 群聊 /new 仍需 @bot（沿用既有丢弃逻辑）；per-sender scope 只重置发送者自己的会话；conversation-shared scope 任何成员可重置共享会话
- pending 与 tracker 为进程内存：重启丢失 → 重启后 /new 立即执行（迟到回复仍可送达），v1 不做 DB 持久化
- /new 排队期间用户新消息继续进入旧会话（不阻塞）
- 不清理旧会话的 HumanInputRequest 行（按既有 expire/fail 逻辑自然收场）
- 并发 /new 的回复组合不确定（赢家 DONE；输家看到 Completed 答 NOTHING），不变量：恰好各答一条、只归档一次
- 集成风险（上线前验证）：无会话场景的确认回复 `bcs_session_id` 传 `""`，需确认仓库外 DingTalk provider 容忍

## 测试

- `commands.rs` 12 个单测：解析器（精确匹配/空白容忍/拒绝变体）；tracker（seed→drain、未知 run no-op、重复终态幂等、多 run 等待、重复 pending 拒绝、stale 阈值、FIFO 驱逐、take_pending）
- `lib.rs` 12 个集成测试：无会话 / 空闲即时重置+dedup 重放+rollover / 排队+ChatFinal / error 终态 / per-sender 隔离 / shared 群 / state-machine 三态（空闲立即、Running 排队+终态执行、启动窗口）/ HumanInput 不吞 /new / 并发幂等+恰好一个新会话 / 30min stale 兜底
- harness 扩展：`RecordingMessageFlow.active_run_ids` 可配置；`TestHarness::new_with_clock` 支持可变时钟

## 验证

- `cargo test --package bcs-channel`：83 通过（59 基线 + 24 新增）
- 并发测试重复 5 次稳定
- `cargo test --workspace` 回归
- 钉钉联调（内部 provider 环境）待进行：单聊/群聊 @bot 全流程 + 空 `bcs_session_id` 回复兼容性
