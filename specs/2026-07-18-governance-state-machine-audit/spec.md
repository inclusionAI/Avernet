# Governance 状态机实现合理性评估 (Audit)

## Summary

加完 OBSERVED 第 5 态后,回头审视治理工单状态机的**全局结构合理性**。本 spec 不产代码,只产**现状画像 + 问题清单 + 改造建议**,为后续是否动手重构提供依据。评估范围:工单主状态机(open/scheduled/waiting_review/observed/closed);通知投递状态机不纳入(仅作工单机的下游副作用看待)。

评估维度四项:① 数学性质(完备性/死态/可达性);② 守卫与不变式;③ 单一驱动收口;④ 测试覆盖完整性。

## Motivation

状态机是治理域的核心骨架,任何并发入口(offline-batch / cron tick / 用户反馈 / 管理审批 / 删白)都靠它保证工单流转一致。本期加 OBSERVED 是首次"加态",过程中暴露了几个结构性问题(谓词散落 25 处需收口、bulk_close_open 旁路守卫、enter_observed/close_observed_for_removal 测试缺口事后补)。在状态机还小(5 态)时评估一次,比等到 7-8 态再重构代价低得多。

## 现状画像(评估基准)

### 状态集合(5 态)

| 态 | 语义 | 族 | active_worker | closed_at | 发通知 |
|---|---|---|---|---|---|
| OPEN | 待治理/待反馈 | ACTIVE | set | null | 是 |
| SCHEDULED | 排期观察(need_time 同意) | ACTIVE | set | null | 是(到期 reminder) |
| WAITING_REVIEW | 待管理员审批 | ACTIVE | set | null | 否(暂停投递) |
| OBSERVED | 白名单观察态 | TERMINAL | null | null | 否 |
| CLOSED | 终态/归档 | TERMINAL | null | set | 否 |

### 转换矩阵(TICKET_TRANSITIONS)

```
OPEN           → {SCHEDULED, WAITING_REVIEW, OBSERVED, CLOSED}
SCHEDULED      → {WAITING_REVIEW, OBSERVED, CLOSED}
WAITING_REVIEW → {OPEN, SCHEDULED, OBSERVED, CLOSED}
OBSERVED       → {CLOSED}
CLOSED         → {}  (终态)
```

### 写点分布(谁改 governance_status)

- **经领域守卫**(transition_to / enter_observed / close / accept_feedback / review): 全部单工单转换,守卫激活。
- **bulk_close_open**(task_record_repo.py:226-234): SQL `UPDATE ... SET governance_status='closed' WHERE status IN (open,scheduled)`,**旁路领域守卫**(arch 标注的"bulk primitive 唯一豁免",性能理由)。
- notify_log_repo.py:699/737: 写的是**通知表** `GovernanceNotificationOrm.governance_status`(通知侧快照,非工单机),不在评估范围。

## Acceptance Criteria(评估产出物)

- [ ] **数学性质**:列出每态的入度/出度,标注可达态、不可达态、死态、自环;给出"加第 6 态的边际成本"判断。
- [ ] **守卫与不变式**:逐条列出状态机隐含的不变式(active_worker/closed_at/close_reason/cooldown_until/remind_at 各态取值),标注哪些有测试钉死、哪些靠默契。
- [ ] **单一驱动收口**:列出所有 governance_status 写点,判定每个是否经领域守卫;对 bulk_close_open 旁路给出风险评级与可选改造。
- [ ] **测试覆盖完整性**:转换矩阵每个合法分支 × 非法分支的测试覆盖矩阵(已覆盖/未覆盖);driver 每方法的分支覆盖;列出已发现的缺口(enter_observed/close_observed_for_removal 本期已补,记录于此)。
- [ ] **问题清单**:按严重度(high/medium/low)列出发现的结构问题,每条含【现象】【风险】【建议】。
- [ ] **结论**:一句话判定"当前状态机实现是否合理 + 是否建议近期重构"。

## In Scope

- 工单主状态机 5 态的结构性评估(四维度)。
- governance_status 写点全量盘点(经守卫 vs 旁路)。
- 转换矩阵 + 不变式的测试覆盖矩阵。
- bulk_close_open 旁路守卫的风险评估与改造选项。

## Out of Scope

- 通知投递状态机(pending→sending→sent/failed/cancelled)的评估。
- 任何代码改动(本 spec 只评估;改造另开 spec/plan/tasks)。
- 性能/并发/锁的深度评估(仅触及 bulk 旁路的"性能理由"是否成立)。
- 前端状态展示。

## Open Questions

- bulk_close_open 的"性能理由"(不能 load N 模型)在当前工单量级是否仍成立?若不成立,旁路守卫的代价是否大于收益?——留待评估时取后台真实工单量级数据判断,或按"即使成立也该有等价守卫"建议加 SQL 级合法性校验。
- OBSERVED 归 TERMINAL 族但"持续刷新"——它是不是语义上更接近"第五个活跃态"?本次落地选 TERMINAL(不进 ACTIVE_STATUSES)是为了不发通知/不被 find_active 命中,评估需复核这个归属是否埋了长期隐患。