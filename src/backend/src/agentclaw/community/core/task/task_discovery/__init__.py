"""任务主动发现模块 — 读取已发现的任务数据，创建 engine session + WebSocket 注入消息 + 投递通知。

完整流程:
1. APScheduler BackgroundScheduler 定时调度（线程级，非 asyncio）
2. 按 (bot_id, owner_id, dt) 读取待确认任务 (mock 数据)
3. 通过 CronRelayService.forward_request 创建 engine session
4. WebSocket chat.send 注入发现提示消息（bot 主动呈现发现任务）
5. session 创建后通过 NotifyMessagesProvider 投递通知（发现摘要 + session 链接）
6. 用户在 session 中确认后，由执行框架处理

engine 侧零改动 — 复用现有 WebSocket 端点 + chat.send 处理器。

触发方式:
A. 自动 — TaskDiscoveryScheduler 在 startup 后按 cron 表达式定时触发
B. 手动 — HTTP POST /api/v1/collaboration/tasks/discovery/discover
C. CLI  — scripts/task_discovery.sh discover → curl backend API
"""