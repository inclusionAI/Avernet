"""任务主动发现模块 — 读取已发现的任务数据，创建 engine session 并投递通知。

完整流程:
1. 定时调度读取已发现的任务 (mock 数据)
2. 为每个待确认任务创建 engine session（获得 session_id）
3. session 创建成功后通过 NotifySenderPlugin 投递通知（任务详情，不含 session 链接）
4. 用户在前端确认后，由执行框架处理

session_url 不在 discover 阶段构建 — 用户 bot 没有单独的 session_url。

触发方式:
A. 自动 — backend 的 TaskDiscoveryLifecycle 在 startup 后启动定时调度
B. 手动 — HTTP POST /api/public/task-discovery/discover
C. CLI  — scripts/task_discovery.sh discover → curl backend API
"""