# Backend 回归测试报告

## 范围和环境
- 本次仅新增 Backend 应用 Caller connection；不涉及 Engine、Relay 或部署，未启动无关服务。
- Worktree: feat-app-caller-connection-rel20260915
- Python 3.12.13，pytest / coverage.py 7.14.3，4 个 xdist workers；已核实导入路径属于当前 worktree。
- Base: `5e41bc0d42720b68c1b18c2b3ae5a320c99d33f2`
- 第一轮 Head: `798ffdef6822db74f574e02f30d4d298644cf96a`
- 时间: 2026-09-14 16:06:33 +08:00；耗时 195.64 秒。

## 第一轮结果：FAIL

执行：在 src/backend 运行 `BACKEND_CI_PYTEST_WORKERS=4 uv run bash scripts/ci_test.sh --base 5e41bc0d42720b68c1b18c2b3ae5a320c99d33f2 --head 798ffdef6822db74f574e02f30d4d298644cf96a`。

| 指标 | 结果 |
|---|---|
| 用例 | total=18640, passed=18578, failed=2, errors=0, skipped=60 |
| 总行覆盖率 | 98031/110095 = 89.04%，满足仓库75%和工作流70%阈值 |
| 变更行覆盖率 | NOT RUN：先修复测试失败，再以稳定新 HEAD 执行独立90%门禁 |
| 远端 ACI | NOT RUN：本地回归不能代表远端 job |

失败项：
1. `tests/community/framework/test_coverage_gate.py::test_endpoint_coverage_against_baseline`：新路径 `POST /api/v1/expert-chats/app-caller-connection` 未注册 framework 的 happy/error 用例；需要补真实行为用例，不得添加 baseline 豁免。
2. `tests/community/architecture/test_architecture_compliance.py::test_core_layer_does_not_import_api`：`core/expert_chat/services/expert_chat_instance_service.py:36,37` 直接导入 `community.api.bot_app_grant_service` 和 `community.api.collaborator_service`，违背该门禁。需保持既有架构规则修正依赖。

证据：本地 `/tmp/app-caller-backend-ci.log`（失败说明在37329和37348行）、`src/backend/pytest_report/TEST-junit.xml`、`src/backend/pytest_report/TEST-cov.xml`。生成日志及 XML 不提交。

## 外部边界日志
第一轮 suite 已执行新增 HTTP 真实签名 JWT 测试，覆盖 request/success/denied/failed 事件、返回 token 保持且日志隐藏、嵌套 Authorization/privateKey/session、URL token/userinfo、二进制与异常消息脱敏；APP/混合APP+USER 无 Cookie、缺失/伪造/过期/issuer错误/USER-only拒绝，可信tenant注入/重置与 verifier 单次调用。整体门禁失败，因此暂不作最终回归 PASS 声明。

## 下一步
实现 agent 修复上述两项，父 agent 提供稳定新 commit 后重新运行完整 CI 与独立90%变更行覆盖率门禁。

## 第二轮结果：PRECHECK PASS
- 稳定 Head: `bfd6812c7537fa1dcd6ed16aa80f1f11124c26fb`；Base 不变。
- 时间: 2026-09-14 16:12:17 +08:00；耗时 166.41 秒。
- 第一轮两项失败已修复：新增 endpoint framework happy/error 行为用例；core 改为从所属领域导入 Protocol。完整 suite 已实际重跑，两项均通过。
- 执行命令同第一轮，`--head` 替换为第二轮 SHA。
- 另外执行：`uv run python ../../scripts/ci/report_check.py --junit pytest_report/TEST-junit.xml --coverage pytest_report/TEST-cov.xml --source-root src --base 5e41bc0d42720b68c1b18c2b3ae5a320c99d33f2 --head bfd6812c7537fa1dcd6ed16aa80f1f11124c26fb --min-case-pass-rate 100 --min-line-coverage 75 --min-change-line-coverage 90`。exit=0。

| 指标 | 第二轮准确结果 | 判定 |
|---|---|---|
| 实际用例 | total=18644, passed=18584, skipped=60, failed=0, errors=0；实际执行通过率18584/18584=100% | PASS |
| 仓库casePassRate | 18644/18644=100%；仓库解析器将60个skipped计入此分子，不能称18644个实际通过 | PASS（仓库口径） |
| 总行覆盖率 | 98029/110095=89.04%，仓库阈值75%，工作流阈值70% | PASS |
| 变更行覆盖率 | 85/87=97.70%，独立阈值90% | PASS |
| 远端ACI/CI | 未在本回归中查询实际PR/job | PENDING，交父agent独立核验 |

未覆盖的变更执行行：`adapters/http/expert_chat/router.py:608,609`，畸形 URL 的 ValueError 脱敏兜底；合法嵌套URL、userinfo、token查询参数和其他敏感字段脱敏路径已覆盖。没有降低阈值、添加ignore或覆盖率排除。

第二轮证据：`/tmp/app-caller-backend-ci-round2.log`；`src/backend/pytest_report/TEST-junit.xml`、`TEST-cov.xml`。测试期间业务代码及测试保持稳定，仅报告文档有并发变更。

## 最终日志和功能验收范围
- 事件：`expert_chat.app_caller_connection.authentication_request/request/success/denied/failed`。
- 字段：system、direction、operation、method、route、request_id（存在时）、可信 app_id/tenant、bot_id/owner_id/user_id/force_upgrade、status/duration_ms；response 包含递归脱敏后的非敏感返回数据；失败有异常类型和安全固定消息。
- 已运行断言：HTTP真实签名验证、无Cookie APP/混合主体成功、USER-only及不合法JWT拒绝、错误租户拒绝、每请求一次签名验证及tenant reset；grant精确绑定/撤销、owner/public/collaborator当前权限、实例边界、force_upgrade参数、原接口回归；请求/成功/拒绝/失败事件和非敏感结果字段保留、原Principal/返回token/嵌套认证材料/URL凭据/异常敏感文本不落日志。
- 无新增出站调用，复用原连接生命周期；本轮无Engine模型级、ARCA或生产验证要求，均未执行，不将其表示为已通过。

**最终结论：本地 Backend 回归及覆盖率 PRECHECK PASS；远端 ACI/CI 需实际 job 证据。**
