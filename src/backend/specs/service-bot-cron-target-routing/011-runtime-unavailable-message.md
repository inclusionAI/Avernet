# Cron运行环境不可用提示优化

截图中的预发创建在生产发布后失败，内部EngineStageNotLiveError被通用异常映射为业务500并直接展示英文。

方案：在单目标Cron解析边界捕获该具体异常，按verify/online提供中文操作指引，转为CronRelayError(409)，保留Bot、stage、原始异常的warning诊断日志。不解析英文异常字符串，不修改共享EngineRuntime错误、不改变发布生命周期或自动创建实例。预发提示：预发运行环境当前不可用，请先发布到预发，待实例就绪后重试。生产提示同理。

测试：模拟真实运行态领域异常，核对409及精确文案、原始异常链、诊断日志和transport未调用；回归Cron服务与HTTP端点。

验证：新增verify/online两条回归先失败（领域异常透传），修复后通过。Cron core及HTTP端点合计237 passed；ruff F/E9通过，git diff --check通过。未部署。
