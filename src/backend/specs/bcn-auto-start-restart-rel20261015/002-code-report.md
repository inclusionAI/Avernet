---
agent: tc-code
status: completed
created: 2026-10-15T15:00:00+08:00
iteration: 1
---

# 编码报告

## Worktree 信息
- 路径: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/bcn-auto-start-restart-rel20261015
- 分支: 根 agent 管理的本任务隔离分支，基于 inclusionAI/Avernet REL20261015@1ce85adc6
- Git commit / rebase / push / PR 由根 agent 负责，本编码 agent 未执行 Git 写操作。

## 既有链路与边界
- 既有链路: start → BCN helper → 异步分配；replacement restart → stop/start；同步 BaaS restart 与 coding BaaS prepare 各自执行 BCN helper。
- 允许新增点: 三条生命周期路径取消资格过滤；共享 helper 通过 respect_drm 参数区分 create；仅既有注册 HTTP 边界补日志。
- 禁止触碰点: create 资格判定、能力解析器、重启锁、durable intent、teclaw/desktop 禁止操作、发布实例重启、引擎与 Relay、BCS/BCN 协议。

## 改动文件列表
| 文件路径 | 改动类型 | 说明 |
| --- | --- | --- |
| apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py | 修改 | start/同步 BaaS restart 每次尝试注册，显式 respect_drm=False；create 保留旧默认 True；删除 start 资格门控移除后不再使用的模板读取 |
| apps/backend/src/agentclaw/community/core/bot_management/engines/aicoding/restart_request.py | 修改 | coding prepare 同样取消资格门控并绕过旧 DRM，保留 durable intent 早返 |
| apps/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py | 修改 | register_provider_bot 的 request/success/failure 事件、状态与耗时、响应业务字段投影 |
| apps/backend/tests/community/core/bot_management/services/test_bot_service_stop_start.py | 修改 | 更新旧限制断言，新增九组引擎/类型矩阵、禁用能力与 DRM、已有成功标记、真实 replacement restart → start 链路 |
| apps/backend/tests/community/core/bot_management/services/test_bot_service_restart_baas_envs.py | 修改 | 同步/coding 两种路径 × plugin/下行矩阵、失败继续、durable intent 不重复注册 |
| apps/backend/tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py | 修改 | applicationCoding 原先不注册的旧断言改为应注册 |
| apps/backend/tests/community/core/bot_management/services/test_bcn_service_register_provider.py | 修改 | 成功/409/500 日志字段、耗时与认证值不落日志 |

## 测试执行结果
- RED: 原实现运行更新后的 start/restart 矩阵，20 failed / 65 passed；失败为新增资格与 DRM 绕过要求未执行注册。独立边界日志 RED 为 3 failed，缺 request/success/failure 事件。
- GREEN: 最终聚焦回归 **549 passed, 17 warnings, 1.78s**；warnings 为现存 Pydantic 配置废弃提示。
- 命令: `apps/backend/.venv/bin/pytest apps/backend/tests/community/core/bot_management/services/test_bot_service_stop_start.py apps/backend/tests/community/core/bot_management/services/test_bot_service_restart_baas_envs.py apps/backend/tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py apps/backend/tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py apps/backend/tests/community/core/bot_management/services/test_bcn_service_register_provider.py apps/backend/tests/community/core/bot_management/aicoding -q`
- create 原有完整测试文件参与回归，资格与 DRM 不变。
- 静态检查: 三个生产文件及四个修改测试文件 `ruff check --select F,E203,E265 --preview`，All checks passed；`git diff --check` 无输出通过。
- 全量 Backend CI、独立变更覆盖率与代码审查由根 agent 后续安排，当前不宣称远端 CI 或覆盖率 PASS。
- 生产文件行数: bot_service.py 6329（既有超长文件，未扩张拆分范围）；bcn_service.py 834；restart_request.py 268。未新增生产文件。

## 外部系统边界日志
- `event=request`: system=BCN、direction=outbound、operation、POST path、provider_id、provider_bot_ref、connection_mode、name/summary/owners 完整业务 payload。
- `event=success`: 同一关联 ref、HTTP status、elapsed_ms、bot_uuid、name/summary/owners、idempotent_replay、runtime_token_present；409 也统一成功事件，保留幂等语义。
- `event=failure`: 同一 ref/status/elapsed_ms/error_type，安全 reason 分别为 http_status_error / request_timeout / unexpected_error。上游未定义可信错误业务字段，不输出可能携带凭据的原始 body/message；原 BcnServiceError 返回语义保留。
- 无凭据的环境仍由既有 BcnService 配置判断并 skipped；不回填成功标记。
- 认证头、admin/runtime token 不进日志；不引入通用递归脱敏或全局过滤器。
- 验证: 新增 200/409/500 三例真实 caplog 断言业务字段、状态、耗时可见，原始 admin/runtime token 不可见；旧注册 HTTP 载荷、环境 skip、timeout/unexpected exception 用例参与回归。

## Git Diff 摘要
7 files changed, 238 insertions(+), 154 deletions(-)（不含 spec/报告产物）。
