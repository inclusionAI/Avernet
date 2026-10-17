---
agent: tc-code-reviewer
status: completed
created: 2026-10-15T15:10:00+08:00
iteration: 1
---

# 代码评审报告

## 评审范围
- Worktree: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/bcn-auto-start-restart-rel20261015
- 分支: feat/bcn-auto-start-restart-rel20261015
- Base / Head: 1ce85adc687991db91ca610a9dfaf16bb5d82382 / ac821fa20e3c927c543bbfdabc2f600a80f774ce
- 源码与测试: 3 个生产文件、4 个测试文件；另有任务 spec/报告。
- 已读取 Review Spec、002 编码报告、AGENTS.md、架构规则、review memory 与项目 OPTIMIZE.md。无 /tmp/CODE_REVIEW.md。
- 既有链路: start → 注册 helper → 分配；replacement restart → start；同步 BaaS restart 与 coding prepare 各自复用 helper。
- 允许新增点: 上述三路径取消旧资格与 DRM 限制、既有注册 HTTP 边界日志。
- 禁止触碰点: create 资格、能力规则、权限/状态/锁、durable intent、teclaw/desktop 禁止操作、发布实例重启、Relay/Engine/BCS。

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
| --- | --- | --- |
| 正确性 | PASS | start 与两条 BaaS 路径均传 respect_drm=False；replacement 经真实 start 注册一次；仅 openclaw+personal 为 plugin，其余省略 connection_mode |
| 安全性 | PASS | owner 仍来自 owner_id/既有 caller fallback；无新增鉴权放宽；HTTP 请求日志不含 headers，成功投影业务字段并只记 token 存在布尔 |
| 性能 | PASS | 每次实际生命周期执行增加一次既有幂等注册，移除 start 已无消费者的模板读取；无新增循环/N+1 |
| 代码风格 | PASS | 无新抽象/模块/模型；内部 keyword-only 参数有兼容默认值；create-only helper 注释已修正；既有超长 bot_service 净减少行数 |
| 测试覆盖 | PASS（局部行为） | 独立执行 BCN+start 69/69 passed；编码聚焦 549/549 passed（002证据）；全量及变更覆盖以独立003b实测为准 |
| ACI 覆盖率门禁 | PENDING | 实际远端 job 尚未提供；不得以局部文件 Cover 或所有局部用例通过代替 ACI PASS |
| 静态检查 | PASS | 独立 ruff --select F,E203,E265 --preview 三生产文件通过；committed base..head git diff --check通过；002另核验四测试文件静态检查 |
| 外部系统边界日志 | PASS | request/success/failure 有系统、操作、POST路由、provider/ref、状态、耗时；请求完整业务载荷，成功业务投影，失败安全 reason；200/409/500 caplog验证 admin/runtime token 不泄漏 |

### Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
| --- | --- | --- | --- |
| R-01 | 方法边界、输入输出、副作用 | PASS | 仅既有 helper 和三个生命周期位置；没有设备/BaaS协议、模型或发布流程扩张 |
| R-02 | 模型默认值/序列化兼容 | PASS | respect_drm默认True，connection_mode=None仍不序列化；ext仍仅回填bcn_registered=True |
| R-03 | start/restart无模板/能力/旧DRM/已注册阻断 | PASS | 三路径删除_should调用并显式绕过DRM；九组start和双BaaS矩阵断言实际注册 |
| R-04 | create资格及DRM保持 | PASS | _provision_created_bot仍_should门控且不传respect_drm；原create资格/DRM用例纳入549例回归 |
| R-05 | 状态/权限/锁/durable intent/禁止操作保持 | PASS | restart主编排无改动；两BaaS durable intent早返在注册前，测试断言注册与upgrade均不发生 |
| R-06 | 覆盖率与远端三项指标 | PENDING | 本地窄覆盖率只定位范围，独立全量003b与远端实际ACI指标分别记录 |

### ACI 覆盖率证据

- Base / Head: 1ce85adc687991db91ca610a9dfaf16bb5d82382 / ac821fa20e3c927c543bbfdabc2f600a80f774ce。
- 独立局部用例: 69/69 (100%), skipped=0, failed=0；17个既有Pydantic弃用警告。
- 局部诊断覆盖率: BcnService 113/305=37.05%；BotService 354/2041=17.34%；合计467/2346=19.91%。本次只跑BCN/start两个测试文件，未覆盖其余生命周期和大量无关方法，不能作为全量ACI分母或PASS依据。
- 局部产物: /tmp/bcn-review-coverage.xml、/tmp/bcn-review-coverage.log。
- 本地全量/变更覆盖: PENDING，独立regression agent运行中；待003b提供实际分子/分母及未覆盖变更行。
- 远端 ACI job: PENDING；casePassRate（>=100%）、lineCoverage（>=70%）、changeLineCoverage（>=90%）均尚无远端分子/分母。
- 不使用pragma、排除生产代码、无断言测试或降低阈值。

### 独立执行验证

```bash
COVERAGE_FILE=/tmp/bcn-review.coverage apps/backend/.venv/bin/python -m pytest apps/backend/tests/community/core/bot_management/services/test_bcn_service_register_provider.py apps/backend/tests/community/core/bot_management/services/test_bot_service_stop_start.py --cov=agentclaw.community.core.bot_management.services.bcn_service --cov=agentclaw.community.core.bot_management.services.bot_service --cov-report=term-missing --cov-report=xml:/tmp/bcn-review-coverage.xml -q
apps/backend/.venv/bin/python -m ruff check --select F,E203,E265 --preview apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py apps/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py apps/backend/src/agentclaw/community/core/bot_management/engines/aicoding/restart_request.py
git diff --check 1ce85adc6...ac821fa20
```

### 具体问题列表
无必须修复项。资格 helper 遗留的 create/start 描述已在 ac821fa20 更正，已核对。

## 整体结论

**结论: PASS（代码审查与已执行局部验证）；ACI evidence PENDING。**

本结论不代表全量测试或远端门禁通过。进入 PR 后必须保留真实全量和 ACI 检查；任一实际门禁失败需回到编码修复。未指定部署目标，本报告不证明线上注册成功。
