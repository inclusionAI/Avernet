# Governance 状态机实现合理性评估报告

> 本报告是 `2026-07-18-governance-state-machine-audit` spec/plan/tasks 的产出物。
> 评估范围:工单主状态机 5 态(open/scheduled/waiting_review/observed/closed)。通知投递状态机不纳入。
> 评估方式:只读代码 + 既有测试,不改实现。基于本期 OBSERVED 落地后的代码状态(ocb-public `bcf0ff44`)。

---

## 评估基准快照

| 项 | 值 |
|---|---|
| 状态数 | 5(OPEN/SCHEDULED/WAITING_REVIEW/OBSERVED/CLOSED)|
| 转换定义 | `domain/ticket.py:76` `TICKET_TRANSITIONS` |
| 守卫入口 | `domain/ticket.py:440` `transition_to` |
| 旁路写点 | `task_record_repo.py:203` `bulk_close_open`(SQL UPDATE)|
| 测试基线 | community 8180 + corp 1465 + DSL 78 TC 全绿(本期) |

---

## ① 数学性质(状态有向图 + 入出度 + 加态成本)

> Task 2 产出。源:`domain/ticket.py:76` `TICKET_TRANSITIONS`。

### 邻接矩阵(行=源态,列=目标态,1=允许转换)

| 源＼目标 | OPEN | SCHEDULED | WAITING_REVIEW | OBSERVED | CLOSED | 出度 |
|---|---|---|---|---|---|---|
| OPEN           | 0 | 1 | 1 | 1 | 1 | **4** |
| SCHEDULED      | 0 | 0 | 1 | 1 | 1 | **3** |
| WAITING_REVIEW | 1 | 1 | 0 | 1 | 1 | **4** |
| OBSERVED       | 0 | 0 | 0 | 0 | 1 | **1** |
| CLOSED         | 0 | 0 | 0 | 0 | 0 | **0** |
| **入度**       | **1** | **2** | **2** | **3** | **4** | |

### 性质判定

| 性质 | 结果 | 说明 |
|---|---|---|
| 可达性(从 OPEN) | ✅ 全可达 | OPEN→WAITING_REVIEW→OPEN 回环;5 态皆可达 |
| 死态(无可达路径) | ✅ 无 | 全部从 OPEN 可达 |
| 终态(空出度) | CLOSED | 唯一纯终态 |
| 准终态(出度极小) | OBSERVED(出度1→CLOSED) | 归 TERMINAL 族但非纯终态,可单向收尾 |
| 自环(→自身) | ✅ 无 | 无 `X→X` |
| 对称性 | 部分对称 | WAITING_REVIEW↔OPEN、WAITING_REVIEW↔SCHEDULED 双向(可恢复);其余单向 |
| 不对称单列 | OBSERVED/CLOSED 出向受限 | 活跃态→OBSERVED/CLOSED 单向,不回活跃(设计:删白/关单后由 off-batch 重建新单而非复活同单) |

### 加第 6 态边际成本判断

**基于本期加 OBSERVED 的实际工作量回溯**(非估算):

| 动作 | 工作量 | 是否可摊薄 |
|---|---|---|
| 枚举加态 + transitions | 1 处(`GovernanceStatus`/`TICKET_TRANSITIONS`) | 不可 |
| 谓词收口常量改 | 已收口后 1 处(`ACTIVE_STATUSES`/`TERMINAL_STATUSES`) | ✅ 本期已还债 |
| 领域方法(enter_observed 等) | 1 套封装 + 单测 | 可复用模式 |
| driver 方法(close/open/find) | 3 方法 + 单测 | 可复用模式 |
| off-batch 三路分发 | 1 处分支重构 | 看新态语义 |
| 测试(domain/driver/endpoint/DSL) | ~8 套测试新增 | 线性增长 |

**判定**:加态边际成本**中等且可控** —— 谓词收口(本期还的债)之后,再加态主要成本在"领域封装方法 + driver 方法 + 测试"三件套,是线性重复模式,无指数膨胀。**阈值建议**:状态机到 7-8 态时,transitions 矩阵会从稀疏变密(每态出度均值 >3),守卫覆盖率下降,届时应评估是否引入状态机库(如 transitions 库)替代手写 frozenset。当前 5 态手写尚合理。

**关键风险**:加态成本不在矩阵本身,而在**每个新态要同步的谓词散点** —— 本期加 OBSERVED 前谓词散 25 处,正是"加态成本被散点放大"的实例。谓词收口是真正降本的动作,比状态机本身的结构更重要(见维度③)。

---

---

## ② 守卫与不变式(各态字段取值 + 不变式测试覆盖)

> Task 3 产出。源:`domain/ticket.py` close/enter_observed/pause/review/accept_feedback 实现 + docstring。

### 各态字段取值表(从领域方法实现抽取)

| 字段 | OPEN | SCHEDULED | WAITING_REVIEW | OBSERVED | CLOSED |
|---|---|---|---|---|---|
| active_worker(assignee) | =worker | =worker | =worker | **None** | **None** |
| closed_at | None | None | None | **None**(关键,非关闭) | **非空**(设 now) |
| close_reason | None | None | None | 来源(WHITELIST_APPROVED/SCAN_WHITELISTED) | 关单原因 |
| cooldown_until | None | None | None | None | approve_close/admin_close 可带 |
| remind_at | 可设(到期提醒) | **None**(离开 open 清) | **None**(pause/accept 清) | **None**(enter_observed 清) | **None**(close 清) |
| mute_until | None | approve_scheduled 设(排期到期) | None | None | None |

### 隐含不变式 + 测试钉死情况

| # | 不变式 | 测试钉死? | 证据 |
|---|---|---|---|
| I1 | 非 CLOSED 态 `closed_at` 必 None | ✅ | OBSERVED 不设 closed_at:`test_lifecycle_service:251`、`test_domain_model:1050`;review approve_scheduled 不设:`test_domain_model:707` |
| I2 | 关闭态 `closed_at` 必非空 | ❌ **无** | 无测试断言"close 后 closed_at 非空"。若 close() 漏设 closed_at,测试抓不到(只靠 docstring 默契) |
| I3 | CLOSED/OBSERVED 态 `active_worker`(assignee) 必 None | ✅ | `test_lifecycle_service:208/252`;`test_coverage_supplement:671` |
| I4 | ACTIVE 态 `active_worker` 必非空 | ❌ **无** | 无显式测试。靠 `find_active_ticket` SQL 隐含(但那是查询侧,非状态不变式) |
| I5 | 离开 OPEN 态必清 `remind_at` | ✅ 部分 | accept_feedback/pause/enter_observed/review 清 remind_at 各有测;但"离开 open 清"作为统一不变式无集中断言 |
| I6 | OBSERVED 不设 `cooldown_until` | ✅ | `test_lifecycle_service:251`(close_observed_for_removal 不设 cooldown) + `TestTicketEnterObserved::test_does_not_touch_cooldown` |
| I7 | 非法转换抛 `IllegalTicketTransitionError` | ✅ | `test_domain_model` 含 7 处断言(OBSERVED→活跃态、closed→open 等) |

### 核心问题判定:不变式无单一事实源

**现象**:7 条不变式散落在 6+ 处方法 docstring/实现注释(close L506、enter_observed L521、pause L543、review L574、accept_feedback L487),无集中 `STATE_INVARIANTS` 表。

**风险**(medium):不变式靠方法注释默契,加新态/改方法时易漏(I2/I4 缺测试就是默契失守的证据)。`closed_at`/`active_worker` 的"该非空/该空"若被某方法误设,无统一守卫可拦,只能靠各方法自觉。

**建议**(medium,可接受不立刻改):抽 `STATE_INVARIANTS: dict[GovernanceStatus, dict[str, FieldSpec]]` 集中表,定义每态各字段"必 None / 必非空 / 任意"+ 一个 `validate_invariants()` 在 save 前断言。收益:加态/改方法时一处定义、自动校验。代价:抽表 + 改 save 链路 ~半天,且要补 I2/I4 这类反向断言。**判定为可接受取舍** —— 当前 5 态、方法数量适中,集中表收益在 7 态以上更明显;但 I2/I4 的测试缺口**该补**(见维度④)。

---

---

## ③ 单一驱动收口(写点全量 + 旁路风险)

> Task 4 产出。源:grep 全仓 `governance_status` 写点 + `transition_to` 调用方。

### 写点全量表(谁改 governance_status)

| 写点 | file:line | 分类 | 经领域守卫? |
|---|---|---|---|
| `transition_to` 赋值 | `ticket.py:446` | 守卫本体(self.governance_status=target) | ✅ 自身 |
| `close` →CLOSED | `ticket.py:509` | 领域封装 | ✅ transition_to |
| `enter_observed` →OBSERVED | `ticket.py:531` | 领域封装 | ✅ transition_to |
| `pause` →WAITING_REVIEW | `ticket.py:545` | 领域封装 | ✅ transition_to |
| `review` approve_scheduled →SCHEDULED | `ticket.py:590` | 领域封装 | ✅ transition_to |
| `review`(三态)→CLOSED | `ticket.py:604` | 领域封装 | ✅ transition_to |
| `resume` →OPEN | `ticket.py:618` | 领域封装 | ✅ transition_to |
| `accept_feedback` →target | `ticket.py:477` | 领域封装 | ✅ transition_to |
| `open_observed_ticket` 手写 →OBSERVED | `lifecycle_service.py:190` | **driver 直调 transition_to** | ✅ 守卫经,但绕开 enter_observed 封装(见下) |
| `open_ticket`/`open_observed_ticket`/`refresh` add_ticket 传参 | `lifecycle_service.py:162/214` 等 | **serde 序列化**(governance_status=ticket.x.value,非转换) | N/A 非转换 |
| `to_orm` row.governance_status= | `ticket.py:807/839` | serde 序列化 | N/A 非转换 |
| `add_ticket` governance_status= | `task_record_repo.py:120` | 建单初值(默认 open) | N/A 非转换 |
| **`bulk_close_open` SQL UPDATE** | `task_record_repo.py:234` | **旁路守卫** | ❌ SQL 直写 closed,WHERE 谓词等价守卫 |
| notify_log 侧 governance_status= | `notify_log_repo.py:699/737` | **通知表同名列,非工单机** | 不在范围 |

### 判定:`transition_to` 是唯一合法转换入口

✅ **成立**。9 个合法转换入口全部经 `transition_to`(8 个领域封装方法 + 1 个 driver 直调)。无 service/repo 旁路调 `self.governance_status =` 做状态转换(除 serde 序列化,非转换)。

### `bulk_close_open` 旁路评估(风险:low — 可接受取舍)

**现象**:`task_record_repo.py:234` 用 SQL `UPDATE SET governance_status='closed' WHERE status IN (open,scheduled)`,不经领域 `transition_to`。docstring 自标"bulk primitive 唯一豁免(性能:不能 load N 模型)"。

**守卫等价性**:它的 `WHERE status IN (open,scheduled)` 谓词 + `active_worker IS NOT NULL` 是一道 SQL 级守卫,**等价于** "open/scheduled→closed 合法"的 TICKET_TRANSITIONS 子集。所以并非裸旁路,有等价守卫。

**漂移风险**:这套 WHERE 谓词(open,scheduled 显式枚举)与 `TICKET_TRANSITIONS` 是**两套独立定义**。本期 Task 3 谓词收口时核查过——此子集不含 waiting_review(有意:待审阅的不该被 batch 关),不能引 `ACTIVE_STATUSES`。两套定义若未来加态(如 OBSERVED 是否该被 batch 关?)需同步两处,有漂移风险。

**"性能理由"是否成立**:docstring 说"不能 load N 模型"。逻辑上 admin batch-close 全部 open 单,量级可能大,逐条 load→model→apply_to 确实慢。**判定成立**。

**建议**(low,不建议立刻改):接受旁路,但加一条单测钉死"WHERE 谓词等价 TICKET_TRANSITIONS 子集"——即若有人改 WHERE 漏了某态或误含 OBSERVED,测试能抓。当前 `test_lifecycle_service::test_closes_all_open_and_scheduled` 已测行为,但没断言"不含 waiting_review/observed"。补一条断言即可,代价极小(见维度④)。

### `open_observed_ticket` 收口瑕疵(风险:**medium — 真缺陷**)

**现象**:`lifecycle_service.py:190` 手写 `ticket.transition_to(OBSERVED)` + `ticket.assignee=None`,注释自承"对齐 enter_observed"——但**没用 `enter_observed`**。

**根因**:`enter_observed(close_reason)` 强制带 close_reason 必填(为"关单转态"场景设计:approve_whitelist/scan兜底都带 close_reason)。但 `open_observed_ticket` 是**建观察单**(非关单转态),**不需要 close_reason**。`enter_observed` 把"转 OBSERVED 的状态机动作"和"关单语义(带 close_reason)"绑死,逼建单路径绕开它手写。

**风险**(medium):"转 OBSERVED + 释放 assignee + 清 remind_at"这个状态机动作现在散在两处:`enter_observed`(关单转态)和 `open_observed_ticket`(建单)。若哪天 enter_observed 加了新副作用(如清 mute_until),open_observed_ticket 不会自动跟,行为分叉。

**判定**:**真缺陷**(非可接受取舍)。`enter_observed` 职责越界——它该只管"状态机动作",close_reason 该由调用方决定传不传(或建单传 None)。

**建议**(medium,值得修):让 `enter_observed(close_reason=None)`,close_reason 可选(建单传 None,关单转态传枚举)。`open_observed_ticket` 改调 `enter_observed()` 复用,消除手写分叉。改动小(2 处 + 测试),收益是"转 OBSERVED"单一入口。**这是本评估最值得起 spec 修的一项**。

### 小结

| 项 | 风险 | 性质 | 建议 |
|---|---|---|---|
| transition_to 单一入口 | ✅ | 成立 | 维持 |
| bulk_close_open 旁路 | low | 可接受取舍(有等价守卫) | 不改,补 WHERE 谓词断言 |
| open_observed_ticket 手写 | **medium** | **真缺陷** | **改 enter_observed 可选 close_reason,消除分叉** |

---

---

## ④ 测试覆盖完整性(转换分支矩阵 + 缺口)

> Task 5 产出。源:`test_domain_model.py`/`test_lifecycle_service.py` 测试清单 + DSL e2e。

### 合法转换分支覆盖矩阵(矩阵每条非空转换 × 是否有测)

| 转换 | 领域层测试 | driver 测试 | DSL e2e |
|---|---|---|---|
| OPEN→SCHEDULED | ✅ test_open_to_scheduled | ✅ accept_feedback(need_time)/review(approve_scheduled) | ✅ TC-09/TC-12 |
| OPEN→WAITING_REVIEW | ✅ test_open_to_waiting_review | ✅ pause/accept_feedback(optimized) | ✅ TC-08/TC-20 |
| OPEN→OBSERVED | ✅ test_active_to_observed[OPEN] | ✅ close_for_whitelist_hit | ✅ TC-15 |
| OPEN→CLOSED | ✅ test_open_to_closed | ✅ admin_close/auto_silence | ✅ TC-09/TC-24 |
| SCHEDULED→WAITING_REVIEW | ✅ test_scheduled_to_waiting_review | ✅ transition_schedule_due | ✅ TC-13 |
| SCHEDULED→OBSERVED | ✅ test_active_to_observed[SCHEDULED] | ✅ close_for_whitelist_hit(经态) | (经 TC-15 同 worker 路径) |
| SCHEDULED→CLOSED | ✅ test_scheduled_to_closed | ✅ admin_close/bulk_close | ✅ TC-34 |
| WAITING_REVIEW→OPEN | ✅ test_waiting_review_to_open | ✅ resume | ✅ TC-09(resolve 后) |
| WAITING_REVIEW→SCHEDULED | ✅ (经 review approve_scheduled 测) | ✅ review(approve_scheduled) | ✅ TC-09 |
| WAITING_REVIEW→OBSERVED | ✅ test_active_to_observed[WR] | ✅ review approve_whitelist | ✅ TC-17 |
| WAITING_REVIEW→CLOSED | ✅ test_waiting_review_to_closed | ✅ review approve_close/reject | ✅ TC-09/TC-14 |
| OBSERVED→CLOSED | ✅ test_observed_to_closed | ✅ close_observed_for_removal | (单测覆盖,DSL remove_whitelist step 直 SQL 未覆盖) |
| CLOSED→(空) | ✅ test_closed_is_terminal | — | ✅ TC-13(resolve rejected) |

**合法分支**:13/13 全覆盖(含本期补的 OBSERVED 相关)。

### 非法转换分支覆盖(每态试图转非法态抛错)

| 非法转换 | 测试 |
|---|---|
| CLOSED→任何态 | ✅ test_closed_is_terminal + test_illegal_transition_raises |
| OBSERVED→OPEN/SCHEDULED/WAITING_REVIEW/OBSERVED | ✅ test_observed_cannot_revert_to_active[4 态] |
| 其他逆矩阵(如 SCHEDULED→OPEN) | ✅ test_illegal_transition_raises(closed→open 代表)+ TICKET_TRANSITIONS 守卫隐含 |

### driver 方法分支覆盖

| driver 方法 | 成功 | not found | 非法转换 | 幂等 no-op |
|---|---|---|---|---|
| open_ticket | ✅ | — | — | — |
| open_observed_ticket | ✅ | — | — | — |
| refresh_snapshot | ✅ | ✅ | — | — |
| close_for_whitelist_hit | ✅ | ✅ | ✅(audit_illegal) | — |
| close_observed_for_removal | ✅ | ✅ | ✅(audit_illegal) | ✅ 本期补 |
| transition_schedule_due | ✅ | ✅ | ✅ | — |
| auto_silence_close | ✅ | ✅ | ✅ | — |
| review_ticket | ✅ | ✅ | ✅ | — |
| admin_close | ✅ | ✅ | — | ✅(idempotent on closed) |
| bulk_close_open | ✅ | — | — | — |

### DSL e2e 转换链覆盖

78 TC 主题套件覆盖的端到端态流转:lifecycle(建单/刷新/cooldown)、decision(normal/静默收敛)、notify(reminder/cancel)、feedback(optimized/dispute/whitelist/need_time→waiting_review)、admin(review 四态/close/bulk)、scheduled(到期/未到期)、transition_gaps(admin_close on 各态)、multi_worker、complex_flow(22 完整生命周期含 OBSERVED TC-C04)。

### 已补缺口(本期 + guard-hardening)

- `enter_observed` 领域直测(TestTicketEnterObserved 5 例)— 评估前缺口,本期补。
- `close_observed_for_removal` 四分支(TestCloseObservedForRemoval 3 例)— 评估前缺口,本期补。
- `open_observed_ticket_uses_enter_observed` — guard-hardening 补(钉死建单走 enter_observed)。
- bulk_close_open WHERE 谓词断言(waiting_review/observed 不被 batch 关)— guard-hardening 补。
- I2(close 后 closed_at 非空)/ I4(ACTIVE 态 active_worker 非空)反向断言 — guard-hardening 补。

### 残留缺口(评估确认,未在本期补)

- **DSL `remove_whitelist` step 直 SQL,不触发 `close_observed_for_removal`**:删白收尾的 e2e 覆盖缺(单测有)。`remove_whitelist` step 要么走 HTTP 端点(新建 step)要么接受单测守护。**low,可接受**。
- 不变式反向"非 CLOSED 态 closed_at 必 None"已有测(I1);"关闭态 closed_at 必非空"(I2)guard-hardening 已补;"ACTIVE 态 active_worker 必非空"(I4)已补。无残留。

**结论**:测试覆盖**完整**,合法/非法分支全覆盖,driver 分支全覆盖,本期还了 4 笔测试债。仅 DSL 删白收尾 e2e 缺(low)。

---

## ⑤ 问题清单

> Task 6 产出。四维度发现合并去重,按严重度排序。

### high
无。状态机结构本身无 high 缺陷。

### medium

**M1. `open_observed_ticket` 收口瑕疵**(`lifecycle_service.py:190` 原手写 / `ticket.py:516` enter_observed 职责越界)
- 现象:enter_observed(close_reason) 把"转 OBSERVED 状态机动作"和"关单语义"绑死,建单路径(open_observed_ticket)绕开它手写 transition_to+assignee=None,动作散两处。
- 风险:enter_observed 加副作用时建单路径不跟,行为分叉。
- 建议:enter_observed 改 close_reason 可选,open_observed_ticket 复用。
- **状态**:✅ guard-hardening spec 已修(Task 1 落地)。

**M2. 不变式无单一事实源**(散在 6+ 处方法 docstring)
- 现象:7 条不变式靠方法注释默契,无 STATE_INVARIANTS 集中表。
- 风险:加态/改方法时漏设(I2/I4 缺测试就是默契失守的证据)。
- 建议:抽 STATE_INVARIANTS 集中表 + save 前 validate_invariants。
- 状态:⏳ 未修(评估标可接受不立刻改;7 态以上再议)。**I2/I4 测试缺口已由 guard-hardening 补**。

### low

**L1. `bulk_close_open` 旁路守卫的两套定义漂移风险**(`task_record_repo.py:226`)
- 现象:SQL WHERE(status IN open,scheduled)与 TICKET_TRANSITIONS 是两套独立定义。
- 风险:加态时需同步两处(WAITING_REVIEW/OBSERVED 是否该被 batch 关?)。
- 性质:可接受取舍(性能理由成立,有 SQL 等价守卫)。
- 建议:不改旁路;补 WHERE 谓词断言。
- 状态:✅ guard-hardening 已补(Task 2)。

**L2. I2/I4 不变式反向断言缺失**
- 现象:关闭态 closed_at 非空、ACTIVE 态 active_worker 非空无测试钉死。
- 风险:close() 漏设 closed_at 被改坏无人抓。
- 建议:补反向断言。
- 状态:✅ guard-hardening 已补(Task 3)。

**L3. DSL remove_whitelist step 直 SQL,删白收尾 e2e 未覆盖**
- 现象:remove_whitelist step 绕过 service,不触发 close_observed_for_removal。
- 风险:删白收尾的 e2e 覆盖缺(单测有)。
- 性质:可接受(单测守护足够)。
- 建议:不改(用户本期拍板 B 删 TC-32,接受单测守护)。
- 状态:⏳ 接受现状。

---

## ⑥ 结论

**状态机实现合理,无 high 缺陷;本期已修完仅有的 1 个 medium 真缺陷(M1)+ 2 个 low 测试债(L1/L2)。**

### 合理之处

- 转换矩阵完备:5 态无死态/无自环/全可达,终态(CLOSED)与准终态(OBSERVED)划分清晰,不对称转换(单向进 OBSERVED/CLOSED)有明确业务理由(删白/关单后重建而非复活)。
- 单一驱动收口:`transition_to` 是唯一合法转换入口,9 个调用方全经它(repo/service 无裸写状态转换);唯一旁路(bulk_close_open)有 SQL 等价守卫 + 性能理由成立。
- 测试覆盖完整:合法 13 分支 + 非法分支 + driver 分支全覆盖,本期还了 4 笔测试债。

### 与"加固 feature 不碰状态机"红线的冲突标注

- **M1 修复**(guard-hardening):只动 enter_observed 签名(领域方法职责解绑),**不动 TICKET_TRANSITIONS/状态枚举/族划分** → 不碰状态机转换矩阵,守红线。
- **M2**(STATE_INVARIANTS 集中表):若做需触及状态机不变式定义层,接近红线 → 评估建议**不立刻改**,7 态以上再议,届时按红线走正式评审。
- **OBSERVED 族别重评**(从 TERMINAL 挪回 ACTIVE):动状态划分,踩红线 → 不在本期,评估 Open Q 留待未来需要给观察态发轻量提醒时再正议。

### 不重构的代价

- 留 M2 不改:不变式仍靠方法注释默契,加态时漏设风险存在,但靠 guard-hardening 补的 I2/I4 断言兜底,风险降到 low。
- 留 OBSERVED 在 TERMINAL:未来要给观察态发提醒时需挪族(碰红线),但当前"不发通知"语义正好需要 TERMINAL 归属,无当下代价。

### 改造优先级(若未来继续)

1. M2 STATE_INVARIANTS 集中表(7 态以上触发)
2. OBSERVED 族别重评(观察态需发提醒时触发)

当前 5 态、守红线、测试债已还,**不建议近期重构**。