# 独立代码评审：service-bot-cron-target-routing

- Reviewer: daas_code（未参与Avernet业务编码或build plan修改）
- Worktree: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-cron-target
- 本地代码结论: PASS。远端ACI证据: PENDING，不能当作远端门禁通过。

## 架构和行为

已核验Cron既有router平放endpoint；binding/实例选择在领域mixin且复用RuntimeBindingResolutionService与DeviceContextResolver。Creator不替代Operator管理权限，跨Caller委托要求ADMIN；Caller stage非online明确拒绝，无ready实例不回退。多实例device需membership验证且转发一次；targeted管理走同一binding，列表查询每个运行实例。新增caller_binding标志保持Caller固定解析路径，避免使用普通发布态invoke上下文。底层engine不接收HTTP路由控制字段。

构建根规则/cron/和Claude Code/AICoding的两个sessions任务文件根排除合理，AICoding extra include已取消；独立真实GNU rsync测试验证旧制品重试清除、工作区同名cron目录保留，以及草稿恢复保留实例任务。

## 初轮问题与复评

初轮REJECT：[P2] get_running_crons固定success=True会将新增targeted下游失败变为成功空列表。已修复router.py236-238，保留success/message/error_code；真实endpoint runner的targeted_running_failure场景断言adapter不可用时success=False/error_code=503/message保留。其他ApiResponse映射同样保留下游error_code。

日志复评：入站、目标解析、出站、响应和失败事件齐全；请求与响应投影已知engine任务业务字段，notify按真实契约保留enabled/user_ids，不dump认证扩展。error_code可检索；未知适配器异常用安全摘要，业务CronRelayError保留业务原因；目标尚未解析时只记本地失败而不假记outbound事件。测试覆盖notify/header凭据不落日志、sessionKey明文、失败事件和无出站解析失败。

API层新增实际endpoint runner场景覆盖routing/stage/alias校验、Creator委托/权限、running以及下游失败映射；领域测试覆盖一次转发、绑定membership、Caller身份、无fallback和DI。

## 独立复跑证据

PATH=/opt/homebrew/bin:$PATH PYTHONPATH=apps/backend/src /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/design-teclaw-caller-session-token-20261010/apps/backend/.venv/bin/python -m pytest apps/backend/tests/community/core/cron apps/backend/tests/community/endpoints/test_cron_target_routing.py apps/backend/tests/community/endpoints/test_endpoint_runner.py apps/backend/tests/community/core/workspace/test_engine_sandbox_build_plan.py apps/backend/tests/community/core/service_bot/services/test_bot_build_runtime_cron.py -q --disable-warnings --tb=short

结果：1682 passed / 37 warnings in 137.36s；范围含完整实际HTTP endpoint runner、Cron领域、新API合同、engine build plan和真实rsync制品/草稿恢复用例。git diff --check通过。该结果是本地测试证据，不是容器部署或远端ACI证据。

## ACI独立门禁

PR当前尚未提供实际head对应远端job；casePassRate/lineCoverage/changeLineCoverage分子分母暂无远端证据，全部PENDING。主agent在创建PR后独立核验100% / >=70% / >=90%门禁，不能把本地1682PASS换算为远端ACI PASS。


## 全量首次失败修复补充

初轮全量发现3项失败，主agent授权最小测试维护，未修改业务行为或降低门禁。
- endpoint失败seed原本直接AsyncMock替换service transport，违反真实HTTP测试框架；改为既有DeviceAdapterTransport插件set_response，在外部系统边界返回503，仍执行真实router/领域/DI。
- 直接unwrap handler的非object body单测移至core/cron/services/test_cron_targeted_operations.py，完整保留原400与禁止service调用断言；endpoint文件保持真实框架场景。
- IAM provider构造单测移除已不存在的collaborators参数，保留runtime_bindings、session_validator等真实依赖。
- Claude Code默认排除精确计数31→33，对应本任务新增两个sessions任务文件排除；不放宽断言。

独立复跑no-mock scanner/IAM provider/default exclude计数/实际HTTP targeted_running_failure/非object body单测：51 passed / 1514 deselected。ruff F/E9通过，git diff --check通过。首轮全量状态由主agent记录20582 passed / 3 failed / 43 skipped，修复后全量待主agent重跑，不能将本地51项等同于全量或远端ACI通过。
