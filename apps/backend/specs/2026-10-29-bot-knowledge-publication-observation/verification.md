# Verification: 服务 Bot 知识库分版本尽力记录

## Current Delivery Verification
- Current GitHub Release base: `ed61d43cbf0da75b04de2d872aa4eccdc0ddc8c7` (`REL20261029`).
- Tested source/test head after rebase and review follow-up: `1e276db8d13520127e53f112e901283ad3af8c0d`.
- Follow-up changes are limited to HTTP README boundary/response semantics and permanent Protocol/concrete registration in `test_service_api_conformance.py`; no feature behavior was changed.
- Focused conformance/architecture/DI/boot/HTTP checks: **237 passed**, 0 failed before rebase.
- Fresh full Backend CI after rebase: **27909 passed, 40 skipped, 2 subtests passed, 0 failed**, 439.67 seconds. Existing JUnit gate: 100.00% (27951/27951); line coverage: **90.71%**; changed-line coverage: **99.19%** (123/124). All existing thresholds passed unchanged.
- Independent incremental review confirmed both previous gaps are addressed and found no new P2+; range-diff shows the original eight feature commits remain patch-equivalent, with only upstream surrounding context changing in the follow-up registry patch.
- Source/test files remain identical to the tested head after this documentation-only verification update. Push, remote CI and deployment are separate evidence, not implied by local CI.

```bash
PATH="$PWD/apps/backend/.venv/bin:$PATH" \
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=8 \
  bash apps/backend/scripts/ci_test.sh \
  --base ed61d43cbf0da75b04de2d872aa4eccdc0ddc8c7 \
  --head 1e276db8d13520127e53f112e901283ad3af8c0d
```

### Release Integration Limitation
The new Release supports resolving an existing personal space for a legacy Bot
whose persisted `space_id` is null. The observation still uses the persisted
space ID, so such a legacy service Bot cannot be captured and returns the
unrecorded result instead; native publication remains unaffected. This is an
explicit missing-observation limitation, not a history migration in this PR.

## Initial Verification Scope
- Base: `2508dcabaa5f533321325e0549b724cd916650d9` (`REL20261029`).
- Tested code/test head: `223090213aff10bf90d9885daa674e6f7bdf1858`.
- Worktree: `feat/bot-knowledge-stage-best-effort`；原 PR #3177 未修改。
- 正常组装：现有 DatabasePlugin/common-config Repository、普通 Service API、既有构建任务及新增只读路由。没有生产 DDL、配置切换、合并或部署。

## Focused Validation
| 验证 | 结果 |
| --- | --- |
| 版本记录最初红/绿 | 缺少实现时导入失败；实现后 41 passed |
| 辅助表缺失回归 | 使用真实 SQLite DROP 辅助表，记录返回 False、查询 unavailable；通用配置 get 的历史兼容语义不变；42 passed |
| 构建任务 + 正常 DI | 48 passed；provider 构造、记录异常均不改变原生阶段结果，真实构建失败仍 Fail |
| 当前选择 + 任务 + DI + 模块/API 架构 | 171 passed（Group A 时点） |
| 注册 HTTP + 受控流程 | 11 passed；实际 DI/HTTP/DB、原生 Repository CAS 与 rollback_flip、现有 GET/PUT、mutation witness、路由唯一性 |
| 新架构 guards + 模块/API 架构 | 15 passed |
| 最终定向功能集合 | 172 passed；包含 1/1000/10000 禁用绑定、100/101 启用边界、个人 Bot 回归 |
| 完整门禁发现问题后的 boot/architecture/tasks 回归 | 79 passed |
| 查询数量/上限/个人 Bot 单独验证 | 6 passed |
| Backend local SAST block rules | passed，现有脚本 + flake8 fallback；未运行公司远端 SAST |
| diff whitespace / 源文件行数 | passed；7 个修改/新增生产 Python 文件均小于 1000 行 |

## Initial Full Backend CI

在代码与测试 head `223090213aff10bf90d9885daa674e6f7bdf1858` 完成第二轮完整运行：

- **27650 passed、40 skipped、2 subtests passed，0 failed**，308.49 秒。
- JUnit case pass rate **100.00%**（27692/27692，按既有 report_check 计算）。
- 全量行覆盖率 **90.65%**，门槛 75%。
- 变更行覆盖率 **99.19%**（123/124），门槛 80%。
- 原有 Backend CI/report gate **passed**；没有降低任何门槛。
- 初次验证后的 `6aec60cb3` 仅更新 SDD/验证文档；后续 review 修复与 Release rebase 的验证以本文 Current Delivery Verification 为准。

```bash
# Run from repository root with the backend virtualenv on PATH.
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=8 \
  bash apps/backend/scripts/ci_test.sh \
  --base 2508dcabaa5f533321325e0549b724cd916650d9 --head 223090213
```

原始报告保存在本 worktree 的 `apps/backend/pytest_report/TEST-junit.xml`、
`TEST-cov.xml`；它们是本地生成物，不提交进仓库。

## Cost Evidence
- 在 1 / 1000 / 10000 条额外禁用绑定下，TEAM Owner 采集均为 5 SELECT + 1 独立 upsert；按版本读取均为 5 SELECT、0 写入。
- 生产采集 SQL 含 tenant/env/source PK/space/enabled 条件及 LIMIT 101；SQLite 查询计划命中覆盖索引 `idx_bk_enabled`。
- 100 个启用 ID 可记录；第 101 个导致辅助记录失败，不产生部分记录，不影响原生任务。
- 已完成构建阶段的重放不再次解析记录 provider，也不新增辅助写入。
- 以上是本地 SQLite 查询数/索引证据，不是 OceanBase 实测执行计划或延迟保证。

## Review and Repairs
- Group A：Standards 无确认缺陷；Spec 发现缺表被误判为未记录的 P2，严格读取修复并经独立增量复审；Performance PASS WITH RISKS。
- Group B：Standards、Spec 均无新的确认缺陷；Performance PASS WITH RISKS。运行时成本常数、有界，不新增调度或逐 ID I/O。
- 首轮完整 CI：27642 passed、40 skipped、2 failed。两个新增失败是新路由未加入 boot 精确集合，以及 Core 类型引用 API 导出层违反架构规则。已最小修正；前者在干净 base 中单独通过，不将其伪称为基线失败。
- 修复只调整 boot 期望集合和 Core Protocol 引用，不修改构建/发布状态机或事务。最终增量 Standards / Spec 均无新的确认缺陷；Spec 评审者独立运行新增 6 个回归和 86 个 boot/task/DI/architecture 用例通过。最终 delta 只有类型引用修正及测试/文档，不再次触发新的 Performance 评审。

## Acceptance Mapping
- AC-01：现有 GET/PUT 注册用例、个人 Bot/当前选择与 CAS 回归。
- AC-02/03：独立采集、版本读取、当前草稿变更不改旧版本；上线只读无复制。
- AC-04：响应仅含观察状态，不给原生发布状态赋值；失败/非 BUILDING 不补写。
- AC-05：空数组、缺失/无效及存储失败分开；无草稿 fallback。
- AC-06：存储/provider 故障注入、原生任务结果、真实构建失败保留。
- AC-07：源 PK、业务 ID、租户、环境、空间、当前 Owner/Editor 及历史 Owner 测试。
- AC-08：历史未记录与原生 rollback_flip 选择旧版本；当前勾选不被恢复。
- AC-09：同一版本重建覆盖；保存失败保留原采集时间，仅称 last_recorded。
- AC-10：没有改变现有 AGENTS.md、MCP 授权、原生文件/发布/恢复实现。

## Remaining Boundaries
- 未新增前端接入，因此不宣称页面已展示分阶段开关。
- 未运行完整 Singlebox 或真实设备 AGENTS.md 验收；保留既有 bot_knowledge pending 和全部门禁阈值原样。
- 未验证真实预发/生产、OceanBase、内部集成包、生产 profile 启动或实际 DB 故障延迟。
- 可复核的 DatabasePlugin/公有插件与当前内部适配器代码没有提供本功能独立短截止时间；真实驱动/代理配置未知。
- 异常隔离不等于时间隔离，数据库等待仍可能占用原有构建 worker；整个底层数据库故障仍会影响原生流程。
- common-config 操作员保留其原有管理权限；记录不是防篡改审计或授权依据。
- 没有自动回填、补偿或恢复草稿勾选；同一发布单重建失败可保留带时间的上次记录。
