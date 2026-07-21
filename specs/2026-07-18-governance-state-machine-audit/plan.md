# Plan: Governance 状态机实现合理性评估

## Approach

本 plan 是**评估执行计划**,不是代码改造计划。产出一份评估报告(`audit-report.md`,与本 spec/plan 同目录),按 spec 四维度逐项给出"现状 + 判定 + 问题”。评估全部基于读代码 + 既有测试,不写新代码、不改实现。

执行形态:逐维度查证 → 填报告表/矩阵 → 汇总问题清单 → 给结论。每维度产出可独立 review。

## Affected Components

报告只读,不改组件。评估触及的代码(只读对照):

- `domain/enums.py` — `GovernanceStatus` / `TICKET_TRANSITIONS`(虽定义在 ticket.py)/ `ACTIVE_STATUSES` / `TERMINAL_STATUSES`
- `domain/ticket.py` — `transition_to` 守卫 + 9 个合法转换入口(close/enter_observed/accept_feedback/review/pause/resume/transition_schedule_due/open_observed_ticket)
- `repositories/task_record_repo.py:203` — `bulk_close_open` 旁路守卫的 SQL UPDATE
- `repositories/task_record_query.py` — status 谓词收口现状
- `services/lifecycle_service.py` — driver 各方法 + L190 直接调 transition_to 的收口瑕疵
- `tests/community/core/economy/governance/` — 测试覆盖矩阵的素材

## Data Model Changes

无(纯评估)。

## API / Interface Changes

无。

## Key Files & Functions(评估对照点)

- `domain/ticket.py:76` `TICKET_TRANSITIONS` — 数学性质评估的源
- `domain/ticket.py:440` `transition_to` — 守卫唯一性判定
- `domain/ticket.py:509/531/545/590/604/618` 等 9 处 `transition_to(` 调用 — 合法入口盘点
- `services/lifecycle_service.py:190` `open_observed_ticket` 内 `ticket.transition_to(OBSERVED)` — 收口瑕疵(未经领域封装方法)
- `repositories/task_record_repo.py:226-234` `bulk_close_open` SQL — 唯一旁路守卫
- `domain/ticket.py:506/521/543/574` 各方法 docstring — 不变式散落点(无集中表)
- `tests/community/core/economy/governance/test_domain_model.py` `TestTicketTransitions` + `TestTicketObservedTransitions` + `TestTicketEnterObserved` — 转换覆盖素材
- `tests/community/core/economy/governance/test_lifecycle_service.py` driver 各 Test 类 — driver 覆盖素材

## 评估执行步骤(按维度)

### 维度①数学性质 — 产出:状态有向图 + 入出度表 + 加态成本判断

1. 从 `TICKET_TRANSITIONS` 抽出 5 态 × 5 态邻接矩阵,标注每态入度/出度。
2. 判定:可达性(每态是否从 OPEN 可达)、死态(无可达路径)、终态(CLOSED/OBSERVED)、自环(无)。
3. 反对称性核查:WAITING_REVIEW↔OPEN/SCHEDULED 双向(可恢复),其余单向。
4. **加态成本判断**:基于本期加 OBSERVED 的实际工作量(谓词收口 + 9 处守卫 + 测试),估"加第 6 态"的边际成本,给出"状态机膨胀阈值"建议。

### 维度②守卫与不变式 — 产出:各态字段取值表 + 不变式测试覆盖表

1. 逐态×逐字段(active_worker/closed_at/close_reason/cooldown_until/remind_at/mute_until/assignee)填取值表(从各方法 docstring + 实现抽)。
2. 列隐含不变式(如"非 CLOSED 态 closed_at 必 null""OBSERVED 不设 closed_at""ACTIVE 态 active_worker 必非 null")。
3. 逐条不变式 grep 测试是否钉死;标注"靠默契无测试"的不变式 = 问题点。
4. **核心问题**:不变式散在 6+ 处方法注释,无单一事实源 → 评估建议是否要一个 `STATE_INVARIANTS` 集中表。

### 维度③单一驱动收口 — 产出:写点全量表 + 旁路风险评级

1. grep 全仓 `.governance_status =` 写点,分类:经领域守卫 / serde(to_orm 序列化,非转换)/ 旁路。
2. 确认领域 `transition_to` 是唯一合法转换入口;9 个调用方逐个判是否经领域封装方法。
3. **`bulk_close_open` 旁路**:核对它的 SQL `WHERE status IN (open,scheduled)` 是否等价于 TICKET_TRANSITIONS 守卫(两套定义)。评"性能理由"是否成立 + 漂移风险 + 可选改造(SQL CASE WHEN 等价守卫 / 接受旁路但加单测钉死 WHERE 谓词)。
4. **`open_observed_ticket` 收口瑕疵**(lifecycle_service:190 直接调 transition_to 而非 enter_observed):评估是否该改用领域封装方法。

### 维度④测试覆盖完整性 — 产出:转换分支覆盖矩阵 + 缺口清单

1. 合法分支(矩阵每条非空转换)× 测试:grep `test_domain_model.py`/`test_lifecycle_service.py` 是否覆盖,填矩阵(✅/❌)。
2. 非法分支(每态试图转非法态抛 IllegalTicketTransitionError)× 测试:同上。
3. driver 每方法分支(not found / 非法转换 / 成功 / 幂等)× 测试:同上。
4. 已补缺口记录(enter_observed/close_observed_for_removal 本期补的)。
5. DSL e2e 覆盖的端到端转换链单列(78 TC 涵盖哪些转换路径)。

### 汇总:问题清单 + 结论

1. 四维度发现的问题合并去重,按 high/medium/low 排序,每条【现象+file:line+风险+建议】。
2. 结论一句话:状态机实现是否合理 + 是否建议近期重构 + 若重构优先级最高的 1-2 项。

## Risks & Mitigations

- **Risk**:评估停留在"列现象"不给可执行建议。
  **Mitigation**:每个问题必须含【建议】,且建议落到具体改造方向(不是"应该改进")。
- **Risk**:评估把"设计取舍"误判为"问题"(如 bulk 旁路是性能取舍,不是 bug)。
  **Mitigation**:问题清单区分"真缺陷"vs"可接受的取舍但有漂移风险",后者标 low 不建议改。
- **Risk**:评估结论过度激进(建议大重构),与团队"加固 feature 不碰状态机"红线冲突。
  **Mitigation**:结论显式标注每项建议与现状红线的冲突,给"不重构"选项的代价。

## Alternatives Considered

- **不评估,直接补测试缺口**:本期能已补 enter_observed/close_observed_for_removal。但散落的不变式/旁路守卫是结构问题,补测试治标不治本。评估先于改造。
- **评估 + 顺手重构**:违反"本 spec 不动代码"(用户拍板的产出形态)。改造若有,另开 spec。
- **只评 OBSERVED 态**:用户拍板全局,放弃。

## Rollout

- 评估报告 `audit-report.md` 落在本 spec/plan 同目录,纯文档可随时 review。
- 不涉及任何线上/release 变更。
- 结论若建议改造,后续按其优先级另开 spec(不在本评估 spec 范围内推进)。

## Test Strategy

评估本身不需要跑测试(只读对照既有测试)。但维度④产出"testing 是否绿"作为覆盖矩阵的置信度背书 —— 引用本期已跑结果:community 8180 + corp 1465 + DSL 78 TC 全绿。