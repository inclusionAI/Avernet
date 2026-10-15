---
agent: tc-engine-regression
status: completed
created: 2026-10-15T12:21:00+08:00
iteration: 1
---

# Backend 本地独立回归报告

结论：**本地回归 PASS，ACI 兼容覆盖率预检 PASS**。远端 ACI 尚未观察，不能据此写成远端 PASS。

## 固定环境与范围

- Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/bcn-auto-start-restart-rel20261015`。
- 源码仓库：GitHub `inclusionAI/Avernet`。
- Base：`1ce85adc687991db91ca610a9dfaf16bb5d82382`，`origin/REL20261015`。
- Head：`ac821fa20e3c927c543bbfdabc2f600a80f774ce`。
- Branch：`rebase/bcn-auto-start-restart-on-REL20261015`。
- Python：worktree `apps/backend/.venv/bin/python`，3.12；pytest 4 workers。
- 既有链路：Bot lifecycle → BCN provider registration → device allocation/BaaS restart。
- 允许变更：start、两条BaaS restart准备接线、既有注册helper与其BCN边界日志、相关测试。
- 禁止扩张：create资格策略、Engine/Relay运行实现、BCS协议、发布实例生命周期和真实环境部署。
- 本次纯Backend改动；按适用范围运行全部Backend community测试，不启动无关Engine/Relay，不调用真实BCN/BaaS/ARCA。

## 运行与结果

在 `apps/backend` 执行：

```bash
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=4 bash scripts/ci_test.sh --base 1ce85adc687991db91ca610a9dfaf16bb5d82382 --head ac821fa20e3c927c543bbfdabc2f600a80f774ce
```

完整pytest执行 **340.82秒**，结果：**22270 passed、39 skipped、0 failed、0 errors**，2738 warnings。selected总量22309；实际执行用例成功率22270/22270=100%。skipped未作为实际执行通过用例计数。

原wrapper最后调用宿主PATH中的`python`执行`report_check.py`，因该`python`为Python2在函数类型注解处产生`SyntaxError`，wrapper退出1。这是本地检查解释器选择问题，不是测试或业务失败。未修改脚本、业务、测试或阈值，使用同次生成的JUnit和Coverage XML，以项目Python3.12显式执行完整检查，保留既有75%总行门槛，并将变更行门槛从runner的80%提高至tc-main要求的90%：

```bash
apps/backend/.venv/bin/python singlebox/ci/report_check.py \
  --junit apps/backend/pytest_report/TEST-junit.xml \
  --coverage apps/backend/pytest_report/TEST-cov.xml \
  --source-root apps/backend/src \
  --min-case-pass-rate 100 --min-line-coverage 75 \
  --base 1ce85adc687991db91ca610a9dfaf16bb5d82382 \
  --head ac821fa20e3c927c543bbfdabc2f600a80f774ce \
  --min-change-line-coverage 90
```

显式检查 **exit 0**：

| 指标 | 实测 | 门槛 | 结果 |
|---|---|---|---|
| 实际执行case成功率 | 22270/22270 = 100%；selected 22309，skipped 39，failed/errors 0 | 100% | PASS |
| 仓库report_check case口径 | 22309/22309 = 100%，该脚本未从分母扣skip | 100% | PASS；与实际执行口径分别列出 |
| 总行覆盖率 | 113569/126726 = 89.62% | 仓库75%，tc-main最低70% | PASS |
| 变更行覆盖率 | 14/14 = 100% | 90% | PASS |

本地未覆盖变更可执行行：**无**。没有通过排除文件、降低阈值或新增无断言测试规避覆盖率。

## 变更文件覆盖率

| 源码文件 | 变更行覆盖 | 文件整体覆盖（如实列出） |
|---|---|---|
| `apps/backend/src/agentclaw/community/core/bot_management/engines/aicoding/restart_request.py` | 2/2 = 100% | 72/81 = 88.89% |
| `apps/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py` | 7/7 = 100% | 262/305 = 85.90% |
| `apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py` | 5/5 = 100% | 1654/2041 = 81.04% |

整体旧文件覆盖率与本次变更行覆盖率不能混用：本次变更行100%，上述大型既有文件整体不足90%，未宣称整体文件均>90%。

## 行为及边界日志验证

全量community测试包含本次自动入网矩阵、create兼容、BaaS原地/coding prepare、BCN失败与回填失败隔离、环境skip、409幂等及模式序列化等测试。原有生命周期/重启幂等/架构边界测试亦包含在同次执行。

`test_registration_boundary_logs_business_fields_without_credentials` 对成功、409、500参数化，已在本次全量中通过。断言请求与成功/失败事件、业务ref/name/summary、HTTP状态与`elapsed_ms`存在；测试认证凭据和响应token不出日志。

- 事件：`[BcnService.register_provider_bot] event=request / event=success / event=failure`。
- 请求关键字段：provider/ref、path/method、name/summary/owners及connection_mode。
- 响应关键字段：status、elapsed_ms、bot_uuid、name/summary、idempotent；认证token仅记录是否存在。
- 失败关键字段：status、elapsed_ms、error_type、固定安全reason；不记录原始异常/headers/body凭据。
- 使用既有logger，不增加全局日志基础设施。

## 证据与边界

- 全量输出：`/tmp/bcn-auto-start-restart-backend-ci.log`。
- JUnit：`apps/backend/pytest_report/TEST-junit.xml`。
- Coverage：`apps/backend/pytest_report/TEST-cov.xml`。
- 本报告仅修改报告文件；未修改源码、测试、Git分支或提交。
- 远端ACI：**PENDING / 未观察**；由主agent创建PR并核验真实head jobs后更新独立PR/ACI报告。
- 真实预发/生产Bot入网及插件连通性：**未运行**，无指定部署目标；不由本地隔离测试推断线上已生效。
