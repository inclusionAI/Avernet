---
agent: tc-code-reviewer
status: completed
created: 2026-09-21T00:25:00+08:00
iteration: 2
---

# 代码评审报告

## 评审范围

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`
- 分支: `feat/service-bot-build-ignore-db`
- Base: `github/dev`，`7d39e392b99d8bf8351b029c3128d97d1c230411`
- Head: 尚未提交的完整 working tree，包含新增文件；没有用空的 `base...HEAD` 代替实际改动。
- 范围：DB/API、构建三条复制路径、producer 快照透传、DI/schema/契约、对应测试与文档。无 Engine/DaaS/旧实例接口行为修改。

## 整体结论

**结论: PASS；远端 ACI evidence PENDING。**

发现的入站日志遗漏 path/status/elapsed、schema E302、发布单快照仅有 mock 证据三项已在本轮修复并复验。第一轮全量回归的 8 个失败在第二轮通过测试修复清零，未降低门禁或更改 runtime；第二轮全量和独立 90% 变更行门禁通过。最终提交需复核临时树对应关系，远端门禁尚待执行。

## 固定检查维度

| 维度 | 结论 | 说明 |
| --- | --- | --- |
| 正确性 | PASS | DB 单次快照、成功 producer 透传；主根锚定、额外根前缀转换及整根跳过；include 在探测/fallback 前过滤；required 配置拒绝 |
| 安全性 | PASS | 可信 actor 管理权限；Bot repository 环境/租户约束；新模型读写 tenant guard；SQLAlchemy 参数化；字面相对路径与 argv，不拼接 shell。未发现高置信新增可利用漏洞 |
| 性能 | PASS | 每次 build 一次规则读取；配置唯一索引和有限 CAS；没有目录树 Python 递归扫描或逐文件数据库查询 |
| 架构/最小改动 | PASS | 平放现有 router，service 持有业务策略，repository 持有 CAS，DI 组装；协议/conformance/context 文档补齐；历史 restore 不读当前规则 |
| 测试覆盖 | PASS | 第二轮全量 19533 passed / 0 failed；43 skipped 明确保留；reviewer 对修复用例另跑 14 passed |
| ACI 覆盖率门禁 | 本地 PASS / 远端 PENDING | 第二轮临时树 case/total/changed 三项达标；最终提交和远端 job 尚未生成 |
| 静态检查 | PASS | reviewer 实跑 Ruff `--preview --select F,E302,E305,E203,E231,W291,W293`，10 个相关生产文件无告警；`git diff --check` 通过 |
| 外部边界日志 | PASS | request/response/failure 含关联 ID、操作、规范化路径、状态/耗时；拒绝路径不原样输出，SQL 异常仅类别；snapshot/transfer 绑定 revision/version；成功/失败及敏感错误不泄露有断言 |

## 独立测试与覆盖率证据

Reviewer 执行新增 repository、API router、service、build、真实 endpoints 五个测试文件：**51 passed，failed=0，skipped=0**。另行执行真实 endpoint（含发布单 ext 持久化）与 Service API/oversized 架构门禁：**160 passed**；二者有重叠，不叠加成独立用例数。

局部 coverage 使用独立 `/tmp/db-ignore-review.coverage`，避免污染 regression 的覆盖数据：

| 新模块 | 已覆盖/总行 | 未覆盖 |
| --- | --- | --- |
| repository/implementations/build_ignore.py | 50/51 | 118：连续 CAS 冲突耗尽 |
| services/build_ignore_rules.py | 13/13 | 无 |
| services/build_ignore_service.py | 67/69 | 142–143：大列表日志摘要 |
| kernel/build_ignore.py | 29/29 | 无 |
| 合计 | 159/162（98.15%） | 3 行 |

以上是局部定位证据；未覆盖分支不影响本次局部阈值，但可补明确行为测试。禁止据此宣称全量或变更行 ACI 通过。

### 独立全量回归第一轮（已修复）

Regression agent 实测：19574 collected，19523 passed，8 failed，43 skipped，239.24 秒。临时树 `c5d14e8e9285f41c394c055ee0062b16ae6ace56` 对比上述 base：

- casePassRate：19523/19531（排除 skipped，约 99.959%），未达 100%。
- lineCoverage：101650/113986 = 89.18%，达到 70%。
- changeLineCoverage：254/257 = 98.83%，达到 90%；3 条未覆盖行同上。
- 整体门禁 FAIL。临时树不是最终 PR 提交，不代替远端状态。

第一轮问题（第二轮全部关闭）：

1. `tests/community/endpoints/test_build_ignore.py` 新增发布 ext 测试导入 Mock，违反 endpoint 测试架构禁令；将该编排集成用例移至合适的 core/services 测试位置或用合规真实依赖，保留行为断言。
2. GET `/ops/build-ignore` 缺框架登记的 error scenario，补实际失败用例。
3. `test_bot_build_service_openclaw_stage_configs.py` 6 个旧 fixture 未传新增必填 repository/environment，补注入并重跑原测试，不削弱断言。

### 第二轮复审与本地门禁

- 测试修复：发布 ext 测试移至 core/services/publish_flow，显式注册真实 app/world fixture；原 HTTP endpoints 未使用 Mock；新增 GET missing_bot 框架 error scenario；旧 stage-config fixture 注入空 repository 和 env，未删除原行为断言。
- Reviewer 独立执行修改的 snapshot persistence、stage configs、HTTP endpoints：14 passed。
- 三个修复测试文件 Ruff `--preview` 静态复验 PASS；fixture 参数精确 `F811` 注释用于 pytest 注入，不是生产孤儿代码或覆盖率豁免。
- 独立 regression 临时树：`95949e0032cdbfabfc0f92713b2100110a5411f0`，Base：`7d39e392b99d8bf8351b029c3128d97d1c230411`。
- 用例：19533/19533 = 100%，failed=0，skipped=43，19576 collected，227.23 秒。
- 总行覆盖率：101652/113986 = 89.18%，超过项目本地 75% 与评审 70% 阈值。
- 变更行覆盖率：254/257 = 98.83%，超过 90%；独立 `report_check.py` 90% gate exit 0。
- 3 条未覆盖行同局部表；本轮 runtime 与第一轮一致。详见 `003b-regression-report.md`。

### 最终 ACI

- Base / Head：上述 base / 最终提交待生成。
- casePassRate：分子/分母 PENDING；要求 100%。
- lineCoverage：分子/分母 PENDING；要求 ≥70%。
- changeLineCoverage：分子/分母 PENDING；要求 ≥90%。
- 远端 ACI job：PENDING。
- 后续以 `003b-regression-report.md` 和最终 base/head 对应远端 job 补齐；临时树覆盖结果须与最终源文件对应。

## Review Spec 检查项

| 编号 | 检查项 | 结论 | 证据 |
| --- | --- | --- | --- |
| R-01 | 无 child router/core adapter 反依赖；DI/schema 完整 | PASS | 新端点平放；实际 HTTP→DI→SQLite 回查；Service API conformance 通过 |
| R-02 | 原子修改、唯一键、并发不丢更新 | PASS | tenant+length-prefixed hash 唯一键；revision CAS；真实 SQLite 首写并发及已有行混合增删；末条删除保留空行 |
| R-03 | 精确子树、多源映射、必需配置保护 | PASS | 实际临时文件树 rsync 验证 workspace/bin 不影响 binary 或嵌套同名路径；额外根/整根、stale 清理、source 不变；MCP/阶段配置拒绝 |
| R-04 | 实际快照进入发布单 ext | PASS | producer 成功/失败分支；真实 BuildStageRunner→PublishExtState→BotPublishService→SQLite 回查 BUILT 和快照，同时保留 existing_setting/binding |
| R-05 | 无无关模块改动及静态新增问题 | PASS | 未改 Engine/DaaS/连接/重启；新规则 helper 独立；现有 oversized 模块门禁通过，无新增 allowlist |
| R-06 | 日志/异常脱敏有断言 | PASS | 对实际格式化日志检查 request/response 字段；secret-bearing DB/HTTP 错误不回显；失败不降级为空规则 |
| R-07 | 模型/方法职责与测试门禁 | PASS/PENDING | 职责、局部行为及第二轮全量本地门禁通过；最终远端 ACI 仍 PENDING |

## 非阻塞建议与交接

1. 可补 CAS 耗尽、大列表日志摘要两类行为断言，覆盖尚缺的 3 条新模块执行行。
2. 发布前先执行增量 DDL；旧容器规则不自动导入，新接口管理下一次构建规则。
3. 继续独立全 Backend 回归、最终 rebase/head 覆盖和远端 ACI；不得将本地代码 PASS 解释为允许跳过这些门禁。
