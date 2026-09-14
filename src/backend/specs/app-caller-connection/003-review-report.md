---
agent: tc-code-reviewer
status: completed
created: 2026-09-14T08:06:12.926185+00:00
iteration: 3
---
# 代码评审报告

## 评审范围
- Worktree: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/feat-app-caller-connection-rel20260915
- 分支: feat/app-caller-connection-rel20260915
- Base: GitHub inclusionAI/Avernet REL20260915 @ 5e41bc0d42720b68c1b18c2b3ae5a320c99d33f2
- Head: 798ffdef6822db74f574e02f30d4d298644cf96a；已复核提交统计与审查快照一致。ACI 证据由主编排/003b 回归报告补充。
- 变更范围: 6 个生产/契约/README 文件、3 个测试文件及任务文档。读取当前完整生产 diff、相关既有鉴权/租户/实例/grant 链路及 Review Spec。
- 既有链路: ordinary Principal verification → tenant scope → service → existing-instance guard → caller lifecycle。新增点为 APP 身份约束、精确 grant 与当前用户访问检查、原 router 平放入口；无 Engine/Relay/BaaS/Gateway/schema 改动。

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
|---|---|---|
| 正确性 | PASS | 参数、Protocol 与实现一致；owner/public/member 授权失败关闭；旧授权和生命周期方法未修改。 |
| 安全性 | PASS | APP 身份来自验签结果；tenant 先于 DI；grant 与当前访问权先于实例读取；无用户/管理员回退或首次创建授权。未发现高可信新增可利用安全漏洞。 |
| 性能 | PASS | 请求缓存验签一次；固定次数授权查询，无新增 N+1。 |
| 代码风格 | PASS | 原 router 平放，领域授权位于 service，通过 owning core Protocol 跨域；README 已声明依赖。 |
| 测试覆盖 | PASS | 独立相关测试 107/107，架构/契约 153/153；最后补入真实 SQLite grant 与 DI tenant 后独立 16/16。上述测试存在重叠，不累加。覆盖率分子分母另由 regression 提供，未用文件 Cover 替代 ACI。 |
| ACI 覆盖率门禁 | PENDING | 当前尚无已提交 head 的独立 JUnit/coverage ACI 报告；本报告不宣称 ACI PASS。 |
| 静态检查 | PASS | 5 个生产 Python 文件 ruff F401/F841/E701/E702/E703/E714 实测通过，git diff --check 通过。 |
| 外部系统边界日志 | PASS | authentication_request/request/success/denied/failed 事件含业务目标、状态、耗时，拒绝保留稳定 reason；复杂嵌套响应、URL userinfo/query、bytes 与异常内容有不泄密断言。复用原 lifecycle，不新建出站协议。 |

### ACI 覆盖率证据

- Base / Head: 5e41bc0d42720b68c1b18c2b3ae5a320c99d33f2 / 798ffdef6822db74f574e02f30d4d298644cf96a。
- ACI casePassRate: PENDING；passed/total、skipped、failed 尚无最终 ACI artifact；threshold 100%。
- ACI lineCoverage: PENDING；covered/total 待独立 regression；threshold >=70%。
- ACI changeLineCoverage: PENDING；changed-covered/changed-total 待 base/head diff 映射；threshold >=90%。
- 未覆盖变更行: 待 regression artifact，不能从相关测试全通过推导无缺口。
- 远端 ACI job: PENDING，未创建 PR/job 时不得标 PASS。

### Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
|---|---|---|---|
| R-01 | 层次与最小修改 | PASS | 原 router 平放；service 负责 grant/current access；无 transport 导入 core；153 架构/契约测试通过。 |
| R-02 | 应用身份与 ordinary audience | PASS | APP/APP+USER 可调用；USER-only、缺失/伪造/过期/issuer/结构无效 401；真实签名 BaaS audience 通过，验签配置一次且 verify_audience=False。 |
| R-03 | 精确 grant/实时访问/已有实例 | PASS | SQLite integration 验证四字段更换、tenant 更换及 revoke；只有首次合法调用读取实例，生命周期 await_count=1。缺实例/无有效 bot_uuid 拒绝。 |
| R-04 | 租户隔离与缓存 | PASS | ASGI middleware 先于依赖构造建立可信 tenant；provider 中断言 acme；完成/错误后 reset，下一匿名请求 401；验签一次。 |
| R-05 | 静态告警与测试质量 | PASS | ruff 和 diff-check 通过；测试断言 HTTP 状态、传参、side-effect 次数及拒绝顺序，无降低阈值或 coverage 排除。 |
| R-06 | 局部覆盖及远端证据 | PENDING | 独立 coverage 尚在 regression 阶段，需记录真实分子分母；远端 job 单独观察。 |

### 已在审查过程中修复的问题

1. 权限拒绝事件原先丢失 service 的稳定 reason；已补 reason 和 exception_type。
2. 普通 Principal 校验原异常字符串包含 Pydantic input_value；已改为安全异常类型日志，真实恶意 payload 测试不泄漏。
3. 递归响应日志原先未处理 URL query 内嵌 URL 和二进制；已补处理及行为断言。
4. 租户负向测试原先混淆嵌套 app.tenant 与 principal.tenant；按现有 verifier 的跨 principal tenant 契约修正，没有修改已有验签语义。

本地局部日志 helper 的必要性已核实：既有 error_logging helper 属 private，会截断正常字段且不清理 URL 凭据；直接复用不满足本接口完整非敏感响应日志要求。保持在新增入口内部，没有扩大通用日志行为。

## 整体结论

**结论: PASS**（源码审查与已执行独立测试；不等于 ACI/CI PASS）

无剩余必须修复源码问题。下一步：提交稳定 head，完成独立 regression 的三项 ACI 指标核验，再由主编排执行 rebase/push/PR 与真实远端门禁；任意指标失败均返回修复，pending 不可视为通过。本任务无部署或合并授权流程。


## 全量门禁修复复审（iteration 2）

- 审查范围：798ffdef 后本次修复 working-tree diff；此前全量回归暴露 core→api 导入门禁和 endpoint 场景登记两项失败。先前源码 PASS 不代表这些未运行门禁已通过。
- owning core Protocol 导入修复：api 原文件仅转导出同一 Protocol，调整不会更换 DI key 或服务行为；README/Spec 同步真实架构约束，没有 waiver 或门禁弱化。
- 新框架 endpoint 测试使用真实 app、DI、grant/instance 仓储及受信 JWT；新增成功和无 Principal 401 场景，成功断言 need_poll=False。framework fixtures 已在测试前后 reset verifier，未引入跨测试密钥状态污染。
- 独立运行 endpoint、architecture_compliance、coverage_gate、module_boundaries：**38 passed**，18 条存量 deprecation warnings。
- 复审结论：**PASS**。本轮不改变身份、grant、租户或生命周期授权语义，无新增必须修复问题。
- 旧 head 覆盖率诊断（父编排提供）：变更行 85/87=97.70%，总行 89.04%；这不是最终 head 门禁结果，全量新 head case/coverage 与远端 CI 仍需 003b/008 证据。旧轮存在 2 个失败用例，不能写全量 PASS。


## Singlebox 接口故事复审（iteration 3）

- 审查范围仅 `scripts/ci/singlebox_coverage_modules.yaml` 与 `tests/community/acceptance/expert_chat/test_caller_connection_api.py` 本次增量；没有业务源码更改。
- manifest 只新增真实 APP endpoint 到 router denominator；expert_chat Core/Router 阈值均保持原值，没有排除生产代码或下调覆盖率。
- 拒绝故事真实签名 APP JWT、不发送用户身份，覆盖无精确 grant、无已有实例、私有 Bot 无当前访问、public 仍不能首次创建、grant 撤销。已有实例但缺发布产物的 5999 明确断言 success=False，仅用于生命周期错误场景，未标作成功。
- 正向故事通过原管理员 API 先 provision，再种入精确 owner/app/bot/user grant；APP 请求前清空 Cookie，并断言实际请求中无 Cookie/x-user-id。显式断言 success=True/error_code=0、复用同一 instance ID/bot_uuid；非轮询分支断言连接 bot_uuid 与 ws_url。撤销后必须 403。
- 测试 JWT 使用 singlebox 既有测试签名配置，密钥不属于生产凭据；新故事通过真实 HTTP 与真实仓储执行，无 mock 业务方法，无降低阈值。
- 独立静态验证：ruff F/E9 PASS；git diff --check PASS；acceptance 文件 **16 个测试收集成功**。这不是 live 执行成功；未并发操作 implementation 正在使用的 Singlebox 实例。
- 复审结论：**PASS（源码与测试设计）**。Singlebox 修复后的实际运行、module coverage 分子分母和最终远端 job 仍以最新 head 的回归报告为准，尚未取得结果时保持 PENDING。
