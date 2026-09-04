---
agent: tc-engine-regression
status: completed
created: 2026-09-04T17:12:00+08:00
iteration: 2
base: github/REL20260904@3c3aeaf109638ea5e1d917a31dd0fd38d971e5ac
head: abb5fa5608e10d3e648005d938f3eef35bb3ce2a
---

# Backend 本地回归报告：Caller 实例本人重启权限

## 结论

**PASS（REL20260904 本地回归与覆盖率门禁）**。

## 最终命令

```bash
cd src/backend
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=auto \
  uv run bash scripts/ci_test.sh \
  --base 3c3aeaf109638ea5e1d917a31dd0fd38d971e5ac \
  --head abb5fa5608e10d3e648005d938f3eef35bb3ce2a
```

## 结果

| 门禁 | 结果 | 证据 |
|---|---:|---|
| Backend coverage pytest | PASS | 17068 passed，59 skipped，0 failed |
| casePassRate | PASS | 100.00%（17127/17127），要求 >=100% |
| lineCoverage | PASS | 88.55%，要求 >=75% |
| changeLineCoverage | PASS | 100.00%（44/44），要求 >=80% |
| Backend CI | PASS | `backend CI gate passed` |
| 相关功能/架构测试 | PASS | 403 passed，0 failed |
| Endpoint no-mock 门禁 | PASS | 已包含在架构测试与全量 CI |
| Module boundary | PASS | 已包含在架构测试与全量 CI |
| Protocol conformance | PASS | 已包含在架构测试与全量 CI |
| git diff whitespace | PASS | `git diff --check` exit 0 |
| Acceptance/live Singlebox | NOT RUN | 未启动 Backend/BaaS live stack；不伪报通过 |

## 行为覆盖

- 管理员可以继续为任意 Caller 创建、复用或升级实例。
- 普通用户仅能管理自己已有且含有效 `bot_uuid` 的精确实例。
- 普通用户无实例、无 `bot_uuid` 或跨用户时，不发生 lifecycle/BaaS 副作用。
- `force_upgrade` 只有在上述授权通过后才进入既有升级流程。
- 四类入站日志已验证，敏感哨兵未落日志。

## 远端状态

GitHub PR 尚未创建时，远端 CI/ACI 为 PENDING。本报告只证明本地 REL 基线门禁通过。
