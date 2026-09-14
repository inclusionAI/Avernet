---
agent: tc-code
status: completed
created: 2026-09-14
iteration: 1
---

# 编码报告

## Worktree
- 路径: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/feat-app-caller-connection-rel20260915
- 分支: feat/app-caller-connection-rel20260915
- Base: GitHub inclusionAI/Avernet REL20260915 @ 5e41bc0d42720b68c1b18c2b3ae5a320c99d33f2
- 本agent不commit/push，交父agent执行rebase及PR。

## 既有链路与边界
- 既有ordinary Principal verifier → exact app grant → current caller chat access → existing caller instance lifecycle。
- 允许新增点: org应用依赖、精确路径tenant middleware、原router平放endpoint、原instance service应用入口及其owning Protocol、context README和测试。
- 禁止触碰点: Gateway、BaaS、Engine、Relay、数据库schema及原用户接口语义；均未修改。

## 文件变更
| 文件（相对src/backend） | 用途 |
|---|---|
| src/agentclaw/community/adapters/http/org/dependencies.py | require_app_caller及缓存ordinary tenant seam；畸形Principal错误仅记录异常类型避免Pydantic input_value泄漏 |
| src/agentclaw/community/adapters/http/middleware.py | 仅新精确路径在DI前建立可信tenant并自动恢复 |
| src/agentclaw/community/adapters/http/expert_chat/router.py | 平放应用endpoint、原ApiResponse映射、结构化安全诊断 |
| src/agentclaw/community/core/expert_chat/services/expert_chat_instance_service.py | tenant一致→精确grant→当前owner/public/MEMBER→已有实例非admin生命周期 |
| src/agentclaw/community/core/expert_chat/expert_chat_instance_service_protocol.py | owning Service API方法及前置条件、返回和错误契约 |
| src/agentclaw/community/core/expert_chat/README.md | 声明grant/collaborator public API依赖 |
| tests/community/api/expert_chat/test_app_caller_connection.py | 实签JWT ASGI、DI时tenant、验签仅一次、租户恢复、错误/参数与日志 |
| tests/community/core/expert_chat/services/test_expert_chat_instance_service.py | 新构造依赖fixture，应用精确授权/撤权/首次实例拒绝 |
| tests/community/core/bot_app_grant/test_grant_service.py | real SQLite grant与service跨tuple/tenant拒绝和撤销即时生效集成 |

## 验证
- 新worktree独立 `uv sync --project src/backend --group dev`，使用本worktree `src/backend/.venv/bin/python`。
- TDD红灯：新ASGI endpoint 404；service方法不存在；畸形JWT input_value日志泄漏；嵌套URL凭据与binary日志泄漏。对应最小实现后均通过。
- focused+回归+架构合并运行: **353 passed**（18个存量Pydantic/Starlette deprecation warnings），日志 `/tmp/app-caller-final-focused.log`。
- 命令: `.venv/bin/python -m pytest tests/community/api/expert_chat/test_app_caller_connection.py tests/community/core/expert_chat/services/test_expert_chat_instance_service.py tests/community/core/bot_app_grant/test_grant_service.py tests/community/api/expert_chat/test_router.py tests/community/adapters/http/org/test_org_user.py tests/community/adapters/http/test_avernet_tenant_middleware.py tests/community/architecture/test_service_api_conformance.py tests/community/architecture/test_module_boundaries.py tests/community/architecture/test_http_adapter_layer_is_http_only.py tests/community/architecture/test_no_fastapi_in_core.py --no-cov -q`（cwd src/backend）。
- `ruff check` 修改源文件与新增API/grant测试 `--select F,E9`: PASS；`git diff --check`: PASS。
- full backend regression及远端coverage/CI由独立regression/父agent提供，本报告不把未运行门禁写PASS。

## 外部边界日志
- 新事件: expert_chat.app_caller_connection.authentication_request/request/success/denied/failed；service事件 expert_chat.application_authorized。
- 字段: system、direction、operation、method、route、request_id、可信tenant/app_id、bot_id/owner_id/user_id/force_upgrade、status/error_code、duration_ms、拒绝reason及异常类型，安全完整业务response。
- 脱敏: token/Authorization/Cookie/password/secret/key/credential/session等键大小写不敏感递归处理，URL userinfo移除，敏感query值（包括嵌套URL）脱敏，binary仅类型长度。固定错误消息，不打印原异常消息或Principal。
- 测试真实caplog断言成功、失败及畸形JWT凭据不落日志。error_logging的现有私有summarizer截断普通字段、没有URL脱敏且公开异常logger输出原exc，不能满足此接口的完整非敏感response契约；因此使用局部redactor，不修改通用helper。

## 契约与兼容性
- 不要求Cookie/AuthenticatedUser；APP+USER仍走APP授权。可信app_id并非BaaS自报id。
- 普通HTTP维持verify_audience=False；签名/issuer/exp/主体结构及各Principal外层tenant一致性仍按现有verifier。内嵌app.tenant与外层tenant的对照不属于现有校验，本次没有扩大规则。
- 未授权和首次创建拒绝先于instance连接读取及生命周期；已有效实例允许原force_upgrade和need_poll语义。
- 授权失败HTTP 200 ApiResponse error_code=403；认证失败HTTP 401；非法参数422；非权限业务失败5999。
