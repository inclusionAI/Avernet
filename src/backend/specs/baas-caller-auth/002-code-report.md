---
agent: tc-code
status: completed
created: 2026-09-16T15:46:14.855209+08:00
iteration: 1
---

# 编码报告

## Worktree 信息
- 路径: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/feat-baas-caller-auth-rel20260917
- 分支: feat/baas-caller-auth-rel20260917

## 实现

仅 BaaS Caller 入口调用 require_baas_caller，以配置副本 issuer=baas、verify_audience=False 复用 decode_principal_token，丢弃 claims。删除该路径中间件租户特例和孤立 helper；所有 DI/仓储继承服务端默认租户。Service API 移除 app_id/tenant 参数，保留 Bot、已有实例、有效 bot_uuid 和原连接生命周期。不改共享验证器默认配置。现有密钥继续由 SecretNamesConfig.gateway_principal_signing_key 提供。

README 与 docs/baas-caller-signing-contract.md 已同步。framework/live acceptance helper 均使用 baas JWT；覆盖无 principals 与带无效 principals/foreign tenant，不降低阈值。

## 测试执行结果

- TDD：最小 baas JWT 在旧实现返回401，确认RED；修改后通过。
- 定向 pytest：251 passed，19 个现有依赖/弱测试key warning。日志 /tmp/baas-final-targeted.log。
- 范围：API/expert_chat/test_app_caller_connection.py；core/expert_chat/services/test_expert_chat_instance_service.py；adapters/http/org/test_org_user.py；core/gateway_principal；adapters/http/openapi_v1/test_principal_seam.py；adapters/http/test_avernet_tenant_middleware.py；endpoints/test_expert_chat_caller_connection.py。
- 真实签名 ASGI：缺/任意 principals/app_id/tenant/aud、缺/非法JWT、错误key/HS512/none/tamper、issuer、iat/exp/nbf、无key、业务失败与参数验证；默认DI租户及上下文恢复。
- core：Bot不存在、无实例、非法ext/UUID、force_upgrade true/false，不创建首次实例。
- org/OpenAPI 保持 baas issuer 拒绝；已有gateway/bcs/audience/principal回归通过。
- ruff --preview --select F401,F841,E203,E265：变更行0问题；整个已有core测试文件16个存量F841，全部在未修改行。未为本次任务清理无关测试。
- git diff --check：通过。
- pytest endpoint 文件仅包含已登记用例；完整 registry/core 门禁及真实 live acceptance 由 main 后续执行，不能把本报告当作 live PASS。

## 外部系统边界日志

认证 request/denied 保留路由、参数、请求ID、HTTP状态、耗时和异常类别。业务 request/success/denied/failed 保留默认tenant、目标ID、状态、耗时与递归脱敏响应。不记录JWT claims、JWT、密钥、异常原文或异常链。真实caplog覆盖成功、失败、嵌套凭据与URL query；注入含敏感值的decoder异常也不泄露。

## Git Diff 摘要

```text
 .../community/adapters/http/expert_chat/router.py  |  14 ++-
 .../community/adapters/http/middleware.py          |  11 +--
 .../community/adapters/http/org/dependencies.py    |  31 +++----
 .../agentclaw/community/core/expert_chat/README.md |  11 ++-
 .../expert_chat_instance_service_protocol.py       |  11 +--
 .../services/expert_chat_instance_service.py       |  11 +--
 .../expert_chat/test_caller_connection_api.py      |  17 ++--
 .../http/openapi_v1/test_principal_seam.py         |   7 ++
 .../community/adapters/http/org/test_org_user.py   |   9 ++
 .../api/expert_chat/test_app_caller_connection.py  | 100 +++++++++++++--------
 .../services/test_expert_chat_instance_service.py  |  27 +++---
 .../test_expert_chat_caller_connection.py          |  30 +++++--
 12 files changed, 157 insertions(+), 122 deletions(-)
```
