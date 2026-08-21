---
agent: tc-code-reviewer
status: completed
created: 2026-08-19T00:14:41+08:00
iteration: 2
---

# 代码评审报告

## 评审范围

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- 分支: `feat/openapi-bot-public-catalog`
- Base: `87185a97b989d67133a997cd69ce8435fab0f47a`
- Head: PENDING（当前 HEAD 仍等于 base，实现位于未提交 working-tree diff）
- 改动文件数: 17 个 Task 1 代码、测试与文档文件（不计 pipeline 产物和忽略的缓存文件）
- 复审范围: `.superpowers/sdd/openapi-bot-public-catalog/task-1-fix-1-brief.md` 的全部 REQUIRED corrections

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
|------|------|------|
| 正确性 | PASS | Discovery service 现在输出 repository 的权威 `bot_type`；adapter 使用 `record["bot_type"]` 而不是 fallback，缺失/非法值被固定 502 包络收敛。两条 catalog operation 的身份分支均有行为断言。 |
| 安全性 | PASS | `401001` 只由显式闭集 `APP_ONLY_SUBCODE_REFUSED` 中的 search/discover 触发；旧 `REFUSED` 路由仍使用 `MissingPrincipalError` / `401000`，且保留与无凭证字节级等价的回归断言。新 DTO 仍为显式 allowlist。 |
| 性能 | PASS | 修复未增加额外 I/O、N+1 查询或超出线性投影的处理。 |
| 代码风格 | PASS | 修改保持薄 adapter / service boundary；当前 diff 未见新的越界依赖或无关重构。 |
| 测试覆盖 | PASS | `002-code-report.md` 记录 fix 的 focused GREEN `74/74` 以及 bounded regression `155/155`；新测试覆盖 service 权威 `bot_type`、pre-fix 缺失形状、日志脱敏、旧路由兼容和两条 operation 的身份矩阵。本轮遵照 scoped re-review 要求未重跑广泛测试。 |
| ACI 覆盖率门禁 | PENDING | 未提交改动没有可用的 base..head 变更行分母，也未提供远程 ACI job；定向 pytest 成功不替代 ACI。 |
| 静态检查 | PASS | `002-code-report.md` 记录 changed Task-1 files 的 ruff 通过；本轮重新执行 `git diff --check` 通过，静态审阅未发现新的未使用 import/变量或孤儿符号。 |

### ACI 覆盖率证据

- Base / Head: `87185a97b989d67133a997cd69ce8435fab0f47a` / PENDING
- 用例: 开发阶段报告记录 `155/155` passed；ACI `casePassRate` 分子/分母 PENDING，threshold = 100%
- 总行覆盖率: PENDING；threshold >= 70%
- 变更行覆盖率: PENDING；threshold >= 90%
- 未覆盖变更行: PENDING，需形成实际 head 后基于 base..head 计算
- 远程 ACI job: PENDING

### Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
|------|--------|------|------|
| R-01 | 两条路径、挂载顺序、查询约束与 runtime filter | PASS | search/discover 仍在 generic bots router 前挂载，参数约束和 `{"runtime_state": [value]}` 翻译未因修复回退。 |
| R-02 | verified user id、user-plus-app 及 pure-app 拒绝 | PASS | forged user、user-plus-app、pure-app 均对 search/discover 参数化覆盖；pure-app 两路均断言 `401001`。 |
| R-03 | 权威 `bot_type`、allowlist DTO 与敏感字段隔离 | PASS | service 将 `bot["bot_type"]` 添加到 discovery detail，adapter 不再推测类型；缺失形状测试断言固定 502。 |
| R-04 | 固定失败响应与低敏诊断 | 已由用户于 2026-08-19 调整 | 保留 catalog adapter 的固定失败响应与低敏诊断；用户明确要求不改共享 `BotDiscoverService` 的日志格式，因此该 service 的原有日志与对应脱敏断言均已恢复/移除。 |
| R-05 | 动态 OpenAPI/schema 文档正确 | PASS | `USER_SCOPED_403` 显式发布 `403001`，动态遍历生成 schema 的测试锁定该 runtime subcode。 |
| R-06 | 架构约束及 legacy endpoint 兼容 | PASS | `401001` 被限制在 catalog 闭集；旧 `REFUSED` 路由的 `401000` 与 no-credential 防枚举契约保持不变。 |

### 具体问题列表

无剩余可执行代码问题。

## 整体结论

**结论: PASS**

### 后续门禁

1. 形成实际 head 后，由 `tc-engine-regression` / 远程 ACI 基于正确 base..head 验证 `casePassRate=100%`、`lineCoverage>=70%`、`changeLineCoverage>=90%`。
