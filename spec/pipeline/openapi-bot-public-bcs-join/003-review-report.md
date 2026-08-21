---
agent: tc-code-reviewer
status: completed
created: 2026-08-20T21:57:27+08:00
iteration: 5
---

# 代码评审报告

## 评审范围

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- 分支: `feat/openapi-bot-public-catalog`
- Base: `origin/dev_refactory_collaboration@efa0b7da330d8f928b364de2b84a9012f9bbcedc`
- Committed HEAD: `8de5e1d25bc5c593bde1e2c301edf15c623169ee`
- 本轮修复增量: 仅 `src/backend/tests/community/core/bot_public/test_bot_public_service.py` 新增 3 个行为测试；无生产代码改动。
- `.superpowers/` 未修改、未暂存、未删除；未 commit、push 或修改 PR。

## 结论先行

**结论: PASS**

iteration 4 的唯一阻塞项已关闭：独立复跑 focused suite 为 `157/157`，按项目
`report_check.py` 同口径计算的当前工作树变更可执行行覆盖率为 `103/103 = 100.00%`，达到
`>= 90%`。新增测试均有直接业务断言，不是无断言覆盖率填充，也没有扩大功能或修改生产设计。

远端 ACI 对当前仍含 tracked uncommitted diff 的最终 head 仍为 **PENDING**；本报告的 PASS 是代码
复审结论，不得替代最终远端 ACI 三项门禁。

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
|---|---|---|
| 正确性 | PASS | 三个新增测试真实驱动 Catalog 生产方法，覆盖 unexpected metadata exception、Catalog 脱敏和 blank Backend address 过滤，并断言对外行为。 |
| 安全性 | PASS | 无生产代码变化；上轮 caller/tenant、exact address、fail-closed、allowlist、ORM 参数化结论不变。新增日志测试还断言私有上游错误细节不进入日志。 |
| 性能 | PASS | 本轮只有 focused unit tests，无运行时代码或查询行为变化。 |
| 代码风格 | PASS | 新增测试结构沿用现有 fixture/`MagicMock` 风格；无跳过、xfail、`no cover`、阈值调整或无关辅助抽象。 |
| 测试覆盖 | PASS | `157/157` 通过；current-tree change-line coverage `103/103 = 100.00%`，原 12 条未覆盖变更行现均命中。 |
| ACI 覆盖率门禁 | PENDING | 本地 case 与 change-line 预检通过；项目全量总行覆盖率及当前最终 committed head 的远端 ACI job 尚不可得。 |
| 静态检查 | PASS | `ruff check test_bot_public_service.py` 通过；`git diff --check origin/dev_refactory_collaboration` 通过；未新增 unused import/variable 或 whitespace 告警。 |

### 本轮三项行为断言核验

| 测试 | 结论 | 独立核验 |
|---|---|---|
| `test_catalog_search_fails_closed_on_unexpected_metadata_error` | PASS | 令 metadata port 抛带唯一私密 sentinel 的 `RuntimeError`；断言转为无详情 `BotCatalogSearchUnavailableError`，日志含 request ID、候选数、固定 `invalid_metadata` 类别，且不含 sentinel。真实命中原缺口 `1135,1136,1142`。 |
| `test_catalog_search_redacts_sensitive_fields_and_malformed_ext` | PASS | 同时构造 malformed JSON ext 与字符串 ext；断言 `device_id=None`、malformed ext 变 `{}`、passport token/`iam_token` 变 `None`，非敏感字段保留。真实命中原缺口 `1152,1155-1158,1161,1164`。 |
| `test_catalog_search_filters_blank_backend_addresses` | PASS | 同时提供 blank `bot_id`、blank `entity_id` 和 valid address；断言 port 只收到 valid exact address，结果也只含 valid Bot。真实命中原缺口 `1183,1185`。 |

三项均调用真实 `search_catalog_public_bots_by_keyword`，并对异常、日志、port 入参或返回数据作明确断言；
没有直接调用仅为刷行的内部 helper，没有 `skip`/`xfail`/`pragma: no cover`，也没有与本功能无关的测试。

## 测试与覆盖率证据

- Focused pytest:
  - `test_bot_public_service.py`
  - `test_bot_catalog_metadata_service.py`
  - `test_bot_public_router.py`
  - `test_bot_repository_unified.py`
  - `test_sync_bot_config_uses_resolver.py`
- 用例: `157/157 (100.00%)`，skipped=0，failed=0。
- 局部覆盖率: `1057/2907 = 36.36%`。该分母包含历史大文件，只用于定位缺口，不能替代项目全量 ACI 总行覆盖率。
- 当前工作树变更可执行行覆盖率: `103/103 = 100.00%`，threshold `>= 90%`，PASS。
- 未覆盖变更可执行行: 无。

### ACI 覆盖率证据

- Base / Current tree: `efa0b7da330d8f928b364de2b84a9012f9bbcedc` / `8de5e1d25bc5c593bde1e2c301edf15c623169ee + tracked uncommitted diff`。
- 用例: 本地 focused `157/157 (100.00%)`；远端 ACI casePassRate `PENDING`，threshold = 100%。
- 总行覆盖率: 远端/项目全量 `PENDING`，threshold `>= 70%`；局部 `1057/2907` 不作为该门禁结论。
- 变更行覆盖率: 本地当前工作树 `103/103 (100.00%)`，threshold `>= 90%`，PASS。
- 远端 ACI job: `PENDING`。当前工作树尚未形成最终 committed head，不得沿用较早 head 的远端 PASS。

## 最小性与回归核验

- 本轮仅在已有 Catalog service 测试类中新增 3 个与 iteration-4 REJECT 一一对应的行为测试。
- 未新增通用 fixture、测试框架、生产 helper、配置、依赖或 CI 排除规则。
- 生产文件 blob 与上轮评审一致：
  - `bot_public_service.py`: `83e0b7bf4a53cf1adedeacf202a7d0a64747c50b`
  - repository `bot.py`: `1c1dc32f46bc83663838e3dd639378dba26f30ac`
  - `bot_public_module.py`: `17cc60b01b7cdfd0f610161de7c90a83c20585e9`
- iteration 4 已通过的最小性结论保持不变：legacy sanitizer、原 paginated query 行为、DI 空行和
  base 既有 `pytest` F401 均保持恢复状态；没有要求顺手清理 base 告警。

## Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
|---|---|---|---|
| R-01 | unexpected metadata exception 必须 fail closed 且不泄露详情 | PASS | 异常类型、空公开错误详情、固定低敏日志与 sentinel 不泄露均有断言。 |
| R-02 | Catalog sanitizer 行为有直接断言 | PASS | malformed ext、device、passport token、IAM token 和非敏感字段保留均覆盖。 |
| R-03 | blank Backend address 不得进入 port 或结果 | PASS | blank bot/entity 两个分支均命中，port 入参和最终结果同时断言。 |
| R-04 | 测试修复保持最小 | PASS | 只加 3 个目标测试；生产、协议、DI、schema、文档均无本轮变化。 |
| R-05 | changeLineCoverage `>= 90%` | PASS | `103/103 = 100.00%`，原 12 行缺口全部关闭。 |
| R-06 | 远端 ACI 不得误报 | PASS | 明确标记当前最终 committed head 的远端 ACI 为 PENDING。 |

## 具体问题列表

无。

## 整体结论

**结论: PASS**

### 必须修复项

无。

### 下一步

继续 `tc-engine-regression`，并在当前 tracked diff 提交形成最终 head 后重新执行远端 ACI；只有
`casePassRate >= 100%`、`lineCoverage >= 70%`、`changeLineCoverage >= 90%` 三项门禁均通过后才可部署。
