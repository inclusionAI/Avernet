---
agent: tc-code
status: completed
created: 2026-09-09T12:30:00+08:00
iteration: 2
source: migrated-to-avernet-worktree
---

# Avernet 编码报告

## Worktree

- 路径：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/generic-token-exchange-jit`
- 分支：`feat/generic-token-exchange-jit`
- Base：GitHub `inclusionAI/Avernet` `dev` @ `20a02b43c`

## 改动边界

- 新增中立 Token Plugin 四阶段协议、Pipeline 和 Caller-first Orchestrator。
- 既有 Router 仍注入 `CallerIamTokenServiceProtocol`，DI 将 corp 可扩展编排包装在原 Caller service 外层。
- BaaS 新增通用 token header append；保留既有 Caller append 方法与响应契约。
- 对 BaaS URL 中的 `paas_device_id` 复用严格校验，拒绝路径/控制字符。

## 排查日志与脱敏

- Pipeline：match 跳过、执行成功、失败阶段。
- Orchestrator：Caller 异常、上下文构造异常、Pipeline 非预期异常。
- BaaS：append 请求、成功、业务拒绝、HTTP 失败、非法输入。
- token 仅进入 BaaS 请求体，不写日志；测试断言成功和非法输入日志均不包含原 token。

## 本地验证

- 相关 Caller、Router、DI、Pipeline、BaaS 定向回归：455 passed。
- Pipeline/API 变更覆盖率定向检查：99%。
- 相关 Ruff：passed。
- `git diff --check`：passed。
