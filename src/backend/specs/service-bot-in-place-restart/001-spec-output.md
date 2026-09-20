# 服务 Bot 原地重启

最新补充：用户已确认以/home/admin下的运行目录成功标记放行，不再限定容器实例；具体语义、兼容性与本轮测试以`004-container-success-marker.md`为准。

## 已确认需求与编码计划

用户已确认本方案并要求实现。沿用当前 Avernet worktree 和 daas 脚本工作区，保留已有 publish-ignore 改动。本轮只本地实现和验证，不部署、不重启远端 Bot、不提交 PR。

1. 在现有 publish router 平放 `POST /api/service-bot/publish/{publish_id}/restart-in-place`，复用原 restart 的认证、协作者权限、服务和响应语义。
2. 增加默认 false 的 `in_place` 参数，沿 restart service → durable task → execute_restart → build/BaaS → deploy context → managed composer 透传；历史任务缺省 false。新入口固定 true，不新增 request body、stage 或 version 参数。
3. 原有 recreate/fallback 分支照常执行且保留该标志，不另加禁止重建或运行目录检查。
4. 启动命令仅开启时添加固定参数 `--in_place_restart true`。daas `bootstrapping/start_service.sh` 解析并在 bootstrap reexec 时保留。
5. 开启标志时跳过整个 `service_bot_transition.sh` 调用，避免制品 cp/chown/chmod；继续执行现有配置初始化、`set_read_only_paths.sh` 和启动流程。不保证完全不写配置或不执行任何 chmod。

## 既有链路、允许新增点与禁止触碰点

- 既有发布单重启、任务轮询、状态机和 fallback 保持原语义。verify/online 由发布单解析；不扩展草稿态。
- 只允许新增 route、布尔参数接线、命令 flag、Shell 分支及对应日志/文档/行为测试。
- 不改独立 BaaS restart API，不改 `docker/agent/start_service.sh`，不改 tar/cp 实现、ignore 规则或只读权限策略，不新增目录/NAS 校验。
- 新路由复用既有鉴权。日志记录 publish_id、stage、in_place、结果和耗时，不打印 token、headers 或配置凭据。

## Review Spec

- 新入口具有旧入口相同权限，不绕开服务层。
- 缺省参数/旧任务仍走原路径；任务重试和 recreate 不丢标志。
- 命令仅添加固定布尔标志，无用户输入拼接。
- bootstrap reexec 保留 true/false；原地模式不调用迁移脚本；普通模式保持成功/失败语义。
- `set_read_only_paths.sh` 无行为变化；无新目录检查和禁止重建检查。
- 日志可区分模式，成功/失败有可观察证据；测试断言行为而非仅源码字符串。

## QA / 测试计划

- TDD：新路由、任务 payload/handler、升级及 recreate 接线、composer true/default 参数。
- Shell：实执行抽取的解析/reexec/迁移区块，使用临时目录和 stub，验证跳过迁移、普通迁移、失败退出和只读步骤保留；禁止启动真实 Bot。
- 运行受影响测试、后端全量测试、静态检查、bash -n 和 git diff --check。
- 独立 reviewer 和 regression 审查本轮差异；远端 ACI 尚未运行，不能宣称通过。

## Ship Spec

用户审核代码后另行批准交付。Backend 和 daas 脚本须配套发布；旧脚本不认识新 flag，不能把只发布 Backend 视为已生效。旧入口继续使用完整发布重启链路。
