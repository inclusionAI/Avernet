---
agent: tc-code-reviewer
status: completed
created: 2026-09-04T17:12:00+08:00
iteration: 2
base: github/REL20260904@3c3aeaf109638ea5e1d917a31dd0fd38d971e5ac
head: abb5fa5608e10d3e648005d938f3eef35bb3ce2a
---

# Code Review 报告：Caller 实例本人重启权限

## 结论

**PASS（本地代码审查）**。远端 GitHub PR/CI 在 PR 创建前仍为 PENDING。

## 审查结果

- 管理员旧逻辑保留：`is_super_admin=True` 时直接委托原 `get_caller_connection()`，不要求实例预先存在。
- 普通用户必须满足 `operator_id == user_id`、当前环境精确实例存在、`ext.bot_uuid` 为有效非空字符串。
- 跨用户、无实例、无有效 `bot_uuid` 均在生命周期、发布、BaaS 和连接调用前拒绝。
- 普通用户不能通过该接口首次创建 Caller 实例。
- Router 不访问 Repository；角色事实由可信认证身份和既有 `super_admin()` 得出，领域准入由 Service 集中执行。
- 未引入 public/collaborator 权限；路由继续使用 POST；未修改 schema、Repository 或 BaaS lifecycle。
- Service API 由 owning core Protocol 单一定义，API 层直接 re-export；Protocol/Concrete conformance 已纳入架构门禁。
- Endpoint 目录不使用 mock/patch；Router 日志测试位于允许隔离依赖的 API 测试层。
- request/success/denied/failed 日志不记录 connection、完整 ext、异常消息、Authorization、Cookie、token、secret 或 credential。

## 验证证据

- 相关功能与架构测试：`403 passed, 0 failed`。
- REL 基线 Backend CI：`casePassRate 100.00% (17127/17127)`。
- 总行覆盖率：`88.55%`，门槛 `>=75%`。
- 变更行覆盖率：`100.00% (44/44)`，门槛 `>=80%`。
- `git diff --check`：PASS。
- Acceptance/live Singlebox：未启动，不能作为真实 BaaS 容器验证。

## 范围检查

`github/REL20260904..HEAD` 仅包含本任务 1 个功能提交。未发现无关重构、格式化噪声或对 BaaS/Relay/WebSocket/Cron 的越界修改。
