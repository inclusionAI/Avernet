---
agent: tc-engine-regression
status: completed
created: 2026-10-20
iteration: 2
---

# 独立 Backend 回归报告

## 范围和环境
- 本需求只涉及 Avernet backend 的服务 Bot CLI 发布物名单开关；不涉及 engine adapter/relay，不启动或修改累积 Engine 用例，也不部署或修改实际名单。
- Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-cli-artifact-allowlist`。
- macOS arm64，Python 3.12.13；复用已存在 runtime-ownership worktree venv，只设置当前 worktree 的 PYTHONPATH，没有 uv sync 或更改 venv。
- Base：`a558e89670dcc8cc67f4082fbfc9d4e718e9c3cd`；Head：当前未提交工作树，包括两个 untracked 新生产 Python 文件。后续 commit/rebase 后须以同报告运行仓库兼容检查以固定实际 base/head。

## 执行命令

```bash
cd apps/backend
PYTHONPATH="$PWD/src:$PWD" DEPLOY_PROFILE=test COVERAGE_CORE=sysmon   /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-runtime-ownership/apps/backend/.venv/bin/python   -m pytest tests/community -q -n4 --dist loadfile   --cov=agentclaw.community --cov-report=xml:pytest_report/TEST-cov.xml   --junitxml=pytest_report/TEST-junit.xml
```

## 结果

| 指标 | 证据 | 结论 |
|---|---|---|
| 用例总数 | 24,081 | 包含 39 skipped |
| 实际执行通过率 | 24,042 / 24,042 = 100% | PASS |
| passed / failed / errors / skipped | 24,042 / 0 / 0 / 39 | PASS |
| 总行覆盖率 | 120,830 / 134,361 = 89.93%，阈值 70% | PASS |
| 变更行覆盖率 | 50 / 50 = 100%，阈值 90% | PASS |
| pytest 墙钟时间 | 276.51 秒 | 第二轮完整执行 |
| 项目兼容 casePassRate | (total - failures - errors) / total = 100% | 明确单列 skipped，未声称它们被执行 |

覆盖率取真实 pytest-cov XML。增量使用 `git diff HEAD --unified=0` 加所有新增生产文件的 `git diff --no-index /dev/null`，复用 `singlebox/ci/report_check.py` 的 `find_coverage_hits` 和 `check_change_coverage`，未覆盖变更行为空。未降低阈值、添加 ignore 或修改覆盖率配置。

| 文件（community 下） | covered / changed executable | 未覆盖变更行 |
|---|---|---|
| `adapters/http/access/router.py` | 6/6 | 无 |
| `core/service_bot/services/agentclaw_artifact.py` | 6/6 | 无 |
| `core/service_bot/services/bot_build_service.py` | 4/4 | 无 |
| `core/service_bot/services/local_bin_artifact.py` | 3/3 | 无 |
| `core/user_list/service.py` | 4/4 | 无 |
| `core/user_list/user_list_service_protocol.py` | 0/0 | 无 |
| `di/modules/service_bot_module.py` | 1/1 | 无 |
| `core/service_bot/services/runtime_artifact_policy.py` | 24/24 | 无 |
| `core/user_list/server_managed.py` | 2/2 | 无 |

## 首轮失败及复核
- 首轮：24,041 passed、1 failed、39 skipped，308.62 秒；唯一失败是本次新增 DI 测试 `tests/community/di/test_runtime_artifact_allowlist_wiring.py::test_build_provider_wires_repository_and_database_scope`，SQLite `no such table: ac_entity_user_list`。
- 首轮开始收集时编码 agent 尚在完成该 fixture 的建表隔离修复；执行 traceback 的 INSERT 与磁盘新函数的建表行不一致，符合已收集旧函数与更新后 linecache 的现象。不能将首轮标为 PASS 或视为生产基线失败。
- 当前磁盘版本隔离重跑 1 passed；随后完整重新收集执行 24,042 passed、39 skipped、0 failed，证实当前交付版本通过。回归 agent 没有修改源代码或测试。
- 首轮 XML、日志及增量证据已保留在 `pytest_report/first-run/`；第二轮覆盖当前主报告。

## 行为与排查日志证据
- 名单：默认关闭、源 owner 精确匹配、全量 `*`、env/type 隔离、全量短路查询、缺失 owner、两条 DB 异常路径 fail closed。
- 构建：固定一次决策、必需 DI、真实 repository/SQLite、migration 默认关闭、两种 CLI 目录关闭时无源遍历、重试清理旧快照、保留其他 Home、清理失败传播。
- 访问控制：通用 correct 端点对 server-managed type 返回 403，旧名单类型行为保持。
- `service_bot.runtime_artifacts.request/success/failure` 日志包含 bot_id、entity_id、env、type、enabled、match、elapsed_ms；DB failure 只记录异常类型。行为测试断言注入的 `private-token-must-not-log` 原文未落日志。
- 本改动是内部 repository 查询与本地构建边界，未新增外部 HTTP 系统；不将无外部出站操作宣称为已测试的外部系统请求。

## 证据文件与交付状态
- `apps/backend/pytest_report/full-regression.log`
- `apps/backend/pytest_report/TEST-junit.xml`
- `apps/backend/pytest_report/TEST-cov.xml`
- `apps/backend/pytest_report/regression-evidence.json`
- `apps/backend/pytest_report/working-tree-source.diff`
- `apps/backend/pytest_report/di-isolated.log`
- `git diff --check` PASS。
- 父 agent 另执行项目 block 规则 SAST：23 个 Python 文件 PASS，日志 `/tmp/service-bot-cli-artifact-sast.log`；此项为父 agent 提供的证据。
- 本地结论：**PRECHECK PASS（工作树）**。固定 commit base/head 的仓库兼容命令由父 agent 接续执行。
- 远端 CI：**PENDING**；此报告不将本地 PASS 等同远端通过。
