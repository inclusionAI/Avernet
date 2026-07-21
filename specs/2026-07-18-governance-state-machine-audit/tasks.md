# Tasks: Governance 状态机实现合理性评估

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> 本期是**评估**(不写新代码),tasks 是"评估查证步骤"。每个 task 产出评估报告 `audit-report.md` 的一节。报告文件与本 tasks 同目录: `specs/2026-07-18-governance-state-machine-audit/audit-report.md`。

## Task 1: 预备 — 创建评估报告骨架
- **Goal:** 建立 `audit-report.md` 骨架(四维度章节 + 问题清单 + 结论),作为后续 task 填充的容器。
- **Files:** `specs/2026-07-18-governance-state-machine-audit/audit-report.md`
- **Done when:**
  - [ ] 报告文件创建,含章节:①数学性质 ②守卫与不变式 ③单一驱动收口 ④测试覆盖完整性 ⑤问题清单 ⑥结论。
  - [ ] 每章节留"待填"占位 + 引用对应 task。
- **Depends on:** —

## Task 2: 维度① — 状态有向图 + 入出度表
- **Goal:** 从 `TICKET_TRANSITIONS` 抽 5×5 邻接矩阵,标注入出度,判定可达/死态/终态/自环。
- **Files:** `domain/ticket.py:76`(TICKET_TRANSITIONS);报告①节
- **Done when:**
  - [ ] 报告①节含 5 态 × 5 态邻接矩阵(ASCII 表)。
  - [ ] 每态入度/出度表。
  - [ ] 判定:可达性(从 OPEN 是否可达每态)、死态(无)、终态(CLOSED/OBSERVED)、自环(无);不对称转换(单向)单列。
  - [ ] **加第 6 态边际成本判断**:基于本期加 OBSERVED 实际工作量,估加第 6 态要动几处 + 给"状态机膨胀阈值"建议。
- **Depends on:** Task 1

## Task 3: 维度② — 各态字段取值表 + 不变式测试覆盖
- **Goal:** 逐态×逐字段填取值表,列隐含不变式,逐条核查测试是否钉死。
- **Files:** `domain/ticket.py`(close/enter_observed/pause/review/accept_feedback docstring + 实现);报告②节
- **Done when:**
  - [ ] 报告②节含 5 态 × 7 字段(active_worker/closed_at/close_reason/cooldown_until/remind_at/mute_until/assignee)取值表。
  - [ ] 隐含不变式逐条列出(如"非 CLOSED 态 closed_at 必 null""OBSERVED 不设 closed_at""ACTIVE 态 active_worker 必非 null")。
  - [ ] 每条不变式标注【有测试钉死】/【靠默契无测试】。
  - [ ] **核心问题判定**:不变式散在 6+ 处方法注释无单一事实源 → 评是否需要 `STATE_INVARIANTS` 集中表,给建议。
- **Depends on:** Task 1

## Task 4: 维度③ — 写点全量表 + 旁路风险评级
- **Goal:** grep 全仓 governance_status 写点,分类经守卫/serde/旁路;评 bulk_close_open 旁路与 open_observed_ticket 收口瑕疵。
- **Files:** `domain/ticket.py`(transition_to + 9 调用方);`repositories/task_record_repo.py:203`(bulk_close_open);`services/lifecycle_service.py:190`(open_observed_ticket);报告③节
- **Done when:**
  - [ ] 报告③节含写点全量表:每处写点 file:line + 分类(经领域守卫 / serde 序列化 / 旁路)。
  - [ ] 确认 `transition_to` 是唯一合法转换入口;9 调用方逐个判是否经领域封装方法。
  - [ ] **bulk_close_open 旁路评估**:SQL WHERE 谓词(open,scheduled)是否等价 TICKET_TRANSITIONS 守卫 / 两套定义漂移风险 / "性能理由"是否成立(Git 看注释历史 + 逻辑判断) / 可选改造(SQL CASE WHEN 等价守卫 / 接受旁路加单测)。
  - [ ] **open_observed_ticket 收口瑕疵评估**:lifecycle_service:190 直接调 transition_to 而非 enter_observed —— 评是否该改用领域封装(注意 enter_observed 带 close_reason,建单不需要,瑕疵是否成立)。
  - [ ] 旁路/瑕疵给风险评级(high/medium/low)。
- **Depends on:** Task 1

## Task 5: 维度④ — 转换分支测试覆盖矩阵 + 缺口
- **Goal:** 合法/非法转换分支 × 测试 覆盖矩阵;driver 每方法分支覆盖;DSL e2e 覆盖的转换链。
- **Files:** `tests/community/core/economy/governance/test_domain_model.py`;`tests/community/core/economy/governance/test_lifecycle_service.py`;DSL `a_private_ocb/tests/governance_e2e/`;报告④节
- **Done when:**
  - [ ] 报告④节含合法分支覆盖矩阵(矩阵每条非空转换 × 是否有测,✅/❌)。
  - [ ] 非法分支覆盖矩阵(每态试图转非法态抛错 × 是否有测)。
  - [ ] driver 每方法分支(not found/非法/成功/幂等)覆盖表。
  - [ ] 已补缺口记录(enter_observed/close_observed_for_removal 本期补的)。
  - [ ] DSL e2e 78 TC 涵盖的端到端转换链单列(lifecycle/feedback/admin/complex_flow 套件覆盖的态流转)。
  - [ ] 真有未覆盖缺口的话,列出来(标【已确认无测试】,但**不在本评估补测试**——另开 task)。
- **Depends on:** Task 1

## Task 6: 汇总 — 问题清单 + 结论
- **Goal:** 合并四维度发现,去重排序,给结论。
- **Files:** 报告⑤⑥节
- **Done when:**
  - [ ] 报告⑤节:问题清单按 high/medium/low 排序,每条【现象+file:line+风险+建议】。
  - [ ] 区分"真缺陷"vs"可接受取舍但有漂移风险"(后者标 low)。
  - [ ] 报告⑥节:结论一句话(状态机是否合理 + 是否建议近期重构)。
  - [ ] 结论显式标注每项建议与"加固 feature 不碰状态机"红线的冲突 + "不重构"选项的代价。
  - [ ] 若建议改造,列优先级最高的 1-2 项,注明"另开 spec"。
- **Depends on:** Task 2, 3, 4, 5

---

## Groups

> 评估按维度独立查证,四维度可并行(都只读),但报告骨架先立(Task 1),汇总最后(Task 6)。

- **Group A — 报告骨架:** Task 1
  - Theme: 建 audit-report.md 容器,后续 task 填充。
- **Group B — 四维度查证:** Tasks 2, 3, 4, 5
  - Theme: 数学性质 / 守卫不变式 / 单一驱动 / 测试覆盖,各产报告一节(可并行)。
- **Group C — 汇总结论:** Task 6
  - Theme: 合并四维度去重排序,给结论 + 改造优先级。