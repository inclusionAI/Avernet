---
agent: tc-code-reviewer
status: completed
created: 2026-10-20
spec_iteration: 2
conclusion: PASS
aci_evidence: PENDING
---

# Bot ext CAS Unicode 独立代码评审

## 结论

**Code Review PASS。未发现需要修复的正确性、隔离或敏感日志问题。ACI evidence PENDING；本报告不代表远端 ACI/CI 已通过。**

依据为当前 iteration 2 spec、实际未提交 diff 和新测试全文，不采用 iteration 1 的 writer 一致化建议。当前生产代码改动仅在 BotRepository.compare_and_set_ext 内（8 行新增、3 行删除）；insert、普通 update、设备 writer、Draft 实现均未修改。

## 范围与审查证据

工作树：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/fix-bot-ext-cas-unicode-20261020`。

已读根 AGENTS.md、相关架构边界、spec iteration 2、既有 Bot/Draft repository 与 fixture；检查 working-tree diff 及未跟踪测试。`/tmp/CODE_REVIEW.md` 在本轮检查时不存在，按普通项目要求评审。生产实现、测试内容及下列行号均以本轮工作树为准。

| 项目 | 结论与依据 |
| --- | --- |
| 最小范围 | `bot.py:640-652` 仅改变 CAS expected/new 编码与单条 IN 条件；没有新增 service、抽象、网络调用、表结构或 writer 统一 |
| 中文与历史两编码 | expected 使用 Unicode 和旧默认转义两种 Python dumps 结果，均作为 SQLAlchemy 绑定值；新值固定 Unicode。兼容常见历史格式，不宣称任意空格/键序/自定义 JSON 等值 |
| 真实并发 | UPDATE 仍同时检查整个 ext 快照。IN 只是同一期望对象的两种表示，不接受内容变化后的快照；两编码测试均验证更新后 stale 期望返回 None 且新内容不被覆盖 |
| NULL | `expected_ext=None` 仍走 SQL IS NULL，未被字符串 null 或 IN 分支替代；NULL 成功与后续非 NULL 拒绝有真实数据库断言 |
| 权限及环境范围 | bot_id、owner_id、is_delete=0、self._env() 完整保留，IN 在同一 query.filter 链中按 AND 附加，不存在 OR 逸出身份范围 |
| 写入与异常 | 单 UPDATE + 原有事务提交；gmt_modified 保持原行为，未增加重试、无条件更新或异常吞没。提交后重新读取仍是既有行为，不承诺返回值为事务快照 |
| 日志脱敏 | `bot.py:660-664` 仅记录事件、bot_id、行数、NULL 标记、编码名称；不记录 owner、ext、expected、SQL 参数、启动原因或认证信息。affected_rows=0 没有被误称一定是真并发 |
| Draft | 新测试执行既有 Draft CAS 并断言中文对象正常更新；Draft/ConfigArtifactOffloader 实现未修改 |
| 测试有效性 | 新测试直接 ORM seed 两种真实 TEXT，并检查 raw TEXT 与业务对象；没有只 mock dumps 或 SQL。日志测试包含秘密哨兵，断言成功/未匹配结果及哨兵不出日志 |
| 文件规模 | 修改生产文件 bot.py 恰 1000 行；新增 test_bot_ext_cas_unicode.py 为 146 行，均未超过上限。未扩充原有超长测试文件 |
| 基本质量 | git diff --check 通过；新增 import 均用于测试/fixture，没有新增未使用局部变量。正式 SAST 由主编排执行，不在此冒称已通过 |

未发现具有明确用户影响的 actionable finding。没有为扩大设计而要求原始 TEXT CAS 重构、JSON 语义比较、全库 writer 统一、数据迁移或数据库排序规则改造。

## 独立运行结果

解释器：`/Users/helloworld/Desktop/codes/teamclaw/ocb/apps/backend/.venv/bin/python`。从本 worktree 的 apps/backend 运行，设置 `DEPLOY_PROFILE=test PYTHONPATH=src:.`；源码来自本 worktree，依赖复用现有解释器。

1. `python -m pytest tests/community/repository/bot/test_bot_ext_cas_unicode.py tests/community/repository/bot/test_bot_repository_unified.py -q`：**63 passed、0 failed、21 warnings，1.66 秒**。warnings 为现有依赖 Pydantic 弃用提示。
2. 用内存 pytest plugin 在正常 conftest 初始化后运行新增模块，并在 sessionfinish 调用现有真实 SQLite fixture 做五项独立补充断言：纯 ASCII 成功后 stale 拒绝、错误 owner、其他 env、已删除行、expected None 对非 NULL。**五项全部通过**；后三种隔离及 NULL 场景均验证原始 ext TEXT 未变化。附带新模块 **7 passed、0 failed**（与第一项重叠，不能累计成 70 个独立用例）。
3. `git diff --check`：通过。复查当前 diff 仍仅修改 CAS 方法，新测试仍为 146 行。

补充脚本最初绕过 pytest conftest 直接导入 fixture，触发既有模块循环导入，未执行断言；随后使用上述 pytest plugin 正常初始化后完成检查。该初次脚本失败不作为本补丁产品缺陷，也不隐去其运行边界。补充检查只在内存定义，未修改生产源码或新增测试文件。

## ACI 与未运行边界

| 指标 | 本 reviewer 证据 |
| --- | --- |
| casePassRate | 定向运行 63/63 = 100%；不是全 backend/远端 PR 用例分母 |
| lineCoverage | PENDING，等待独立 regression 的真实 coverage/JUnit 与正式门禁报告 |
| changeLineCoverage | PENDING，等待提交后的准确 dev base/head 与项目 ACI 兼容检查 |

本轮尚未读取到最终 002-code-report.md；独立回归、SAST 和提交/PR 尚由其他 agent 推进。本 reviewer 未运行全 backend、MySQL/OceanBase 集成验证、发布或生产回调，未生成远端 PR/ACI 证据。SQLite 结果不代表生产数据库排序规则和实际事故已验证。

交付前仍需主编排取得并核对正式 SAST、全量回归、lineCoverage>=70%、changeLineCoverage>=90% 与 casePassRate=100% 等实际门禁证据，按真实结果更新后续报告；若任一真实指标失败，应修复并重新审查。当前代码审查 PASS 允许继续既定验证流程，不解除这些门禁。
