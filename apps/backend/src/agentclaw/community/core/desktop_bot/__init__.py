"""Desktop Bot 模块 — 桌面版 Bot 生命周期管理。

与 ``core/service_bot/`` 不同，桌面 Bot 不需要 publish 状态机，
直接通过 BAAS API 进行创建、重启、删除操作，并同步写入本地数据库。
"""