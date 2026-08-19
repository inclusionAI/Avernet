"""任务主动发现模块 — 读取已发现的任务数据，创建 engine session 通知用户确认。

完整流程:
1. 定时调度读取已发现的任务 (mock 数据)
2. 为每个待确认任务创建 engine session + session_url
3. 用户在 engine session 中确认后，调用 engine 执行任务

触发方式:
A. 自动 — backend 的 TaskDiscoveryLifecycle 在 startup 后启动定时调度
B. 手动 — HTTP POST /openapi/v1/collaboration/tasks/discovery/discover
C. CLI  — scripts/task_discovery.sh discover → curl backend API
"""