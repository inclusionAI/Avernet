"""task_discovery HTTP adapter — 公开路由 (无需认证)。

参考 ``cron_noauth_router`` 的模式：提供手动触发端点，
让 CLI 或外部调度器通过 HTTP API 触发任务发现/执行。
"""