"""Default-env-bot (评测沙箱) DI 桥接模块。

评估沙箱功能由 corp 层提供，社区层通过 registry 模式桥接，
使得社区代码不直接引用 corp 模块（B8 规则）。

本包与生产服务 bot 链路完全隔离：
- 生产服务 bot 走 ``service_bot_module.py`` + ``BotBuildService``
- 评测沙箱 bot 走 ``default_env_bot/`` + corp overlay
"""