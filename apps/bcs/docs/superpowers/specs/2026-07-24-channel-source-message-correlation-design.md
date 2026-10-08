# Channel 源消息关联设计

- 日期：2026-07-24
- 状态：已完成
- 范围：BCS channel 入站消息与 bot outbound event 的关联

## 背景

Channel provider 收到 IM 消息时知道原始消息 ID，但 bot 后续通过独立的
`run_id` 返回 `chat.delta`、`chat.final` 或终止事件。部分 provider 需要在
首个可见回复成功后更新原消息上的临时状态，因此 outbound delivery 必须能
精确定位启动当前 run 的源 IM 消息。

只按 conversation ID 关联不安全：同一会话内的并发请求可能互相覆盖，导致
provider 更新错误的源消息。

## 决策

1. Channel ingress 在继续使用源 IM 消息 ID 作为 `idempotency_key` 的同时，
   显式写入 `WebSendCommand.source_im_message_id`；普通 WebSocket 请求保持
   `None`，避免把任意幂等键误当作 IM 消息 ID。
2. Message flow 为每个实际 `Send` delivery 创建 `run_id` 后，在调用 bot
   delivery 前缓存 `run_id -> source_im_message_id`，避免快速响应先于 delivery
   返回。
3. Bot event 转换为 channel `OutboundMessage` 时按 `run_id` 读取源消息 ID。
4. Channel service 将该可选字段继续透传给 `ChannelOutboundEvent`，由具体
   delivery adapter 决定是否使用。
5. Bot delivery 未成功时立即清理映射；run 进入 final、error 或 aborted 后沿用
   MessageTracker 的 terminal cleanup 清理。

## Contract 传播范围

- Service API：
  - `WebSendCommand.source_im_message_id: Option<String>`
  - `OutboundMessage.source_im_message_id: Option<String>`
  - `ChannelOutboundEvent.source_im_message_id: Option<String>`
- 消费方：`bcs-channel` 和所有 `ChannelDeliveryPort` 实现。
- 生产方：`bcs-message-flow` 的 bot event outbound hook。
- 兼容性：这些字段都是进程内、可选的 additive contract；非 channel 来源或无法
  关联的旧路径继续传 `None`，不改变 HTTP、WebSocket 或持久化格式。
- 部署与迁移：不需要配置、数据库迁移或协议版本切换。

## 验收标准

- 同一会话的不同 run 可以分别携带各自的源消息 ID。
- 第一个 bot event 可以拿到在 delivery 返回前写入的源消息 ID。
- Channel service 不修改该字段并将其传给 delivery adapter。
- Delivery 失败或 run 结束后，映射不会继续保留。
- 非 channel 消息的 outbound 行为保持不变。
