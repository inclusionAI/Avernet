# 构建任务排除

既有链路：provider build plan → BotBuildService._migrate_bot_instance / restore_draft → rsync。
允许新增点：3个引擎provider默认排除项、取消AICoding任务extra include、契约说明和行为测试。禁止触碰：runtime调度器、通用传输、数据库。

修改OpenClaw/ClaudeCode/AICoding build plans，排除根cron，后两者排除旧sessions任务与运行记录。原构建delete-excluded清理复用制品残留，restore_draft不使用delete-excluded，保留目标任务。配置变化复用既有构建阶段/rsync日志，不引入无业务意义日志。

验证：10 focused测试通过（含6个真实GNU rsync构建/恢复用例）；235 workspace/build近邻测试通过。未启动线上容器或创建真实定时任务。
