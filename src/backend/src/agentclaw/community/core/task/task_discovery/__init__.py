"""任务主动发现模块 — 读取已发现的任务数据，经 BaaS Open API 创建 engine session + 投递通知。

完整流程:
1. APScheduler BackgroundScheduler 定时调度（线程级，非 asyncio）
2. 按 (bot_id, owner_id, dt) 读取待确认任务
3. 通过 OpenApiBotSessionInitiator → BaaS Open API POST /openapi/v1/messages
   一步完成 session 创建与发现提示消息注入（2026-09-15: 原 CronRelay +
   WebSocket chat.send 注入链已废除，实现自 corp 列下沉统一）
4. session 创建后通过 NotifyMessagesProvider 投递通知（发现摘要 + session 链接）
5. 用户在 session 中确认后，由执行框架处理

触发方式:
A. 自动 — TaskDiscoveryScheduler 在 startup 后按 cron 表达式定时触发
B. 手动 — HTTP POST /api/v1/collaboration/tasks/discovery/discover
C. CLI  — scripts/task_discovery.sh discover → curl backend API
"""