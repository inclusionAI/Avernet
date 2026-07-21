# Tasks: Governance 状态机守卫加固

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> 三项小改 + 回归,守红线(不动 TICKET_TRANSITIONS/状态枚举/族划分)。每 task 独立 commit。

## Task 1: enter_observed 收口 + open_observed_ticket 复用
- **Goal:** 让"转 OBSERVED"单一入口:`enter_observed` 的 close_reason 改可选(状态机动作与关单语义解绑),`open_observed_ticket` 改调它替手写。
- **Files:** `src/agentclaw/community/core/economy/governance/domain/ticket.py:516`(enter_observed 签名);`src/agentclaw/community/core/economy/governance/services/lifecycle_service.py:190-191`(open_observed_ticket)
- **Done when:**
  - [ ] `enter_observed(self, *, close_reason: str | None = None)`,None 时不设 close_reason(建单场景符合)。
  - [ ] `enter_observed` 行为不变:转 OBSERVED + 释放 assignee + 清 remind_at + 不设 closed_at + 不碰 cooldown。
  - [ ] `enter_observed` docstring 更新:close_reason 可选(None=建单,传值=关单转态),状态机动作是主职责。
  - [ ] `open_observed_ticket` 删手写 `transition_to(OBSERVED)+assignee=None`,改 `ticket.enter_observed()`(不传 close_reason)。
  - [ ] add_ticket 字段映射不变(assignee 仍取 `ticket.assignee`,enter_observed 已设 None)。
  - [ ] 两路关单调用方**不动**:`ticket.py:599` review approve_whitelist、`lifecycle_service.py:287` close_for_whitelist_hit 仍传 close_reason。
  - [ ] 既有测试全过(TestTicketEnterObserved/TestCloseForWhitelistHit/TestOpenObservedTicket/test_review_approve_whitelist),行为零变化。
  - [ ] 加一条 `test_open_observed_ticket_uses_enter_observed`:建单后 status=OBSERVED+assignee=None+close_reason=None+closed_at=None(与 enter_observed 一致)。
- **Depends on:** —

## Task 2: bulk_close_open WHERE 谓词断言
- **Goal:** 钉死 batch 关单的 SQL WHERE 谓词不含 waiting_review/observed(防误改宽)。
- **Files:** `src/backend/tests/community/core/economy/governance/test_lifecycle_service.py`
- **Done when:**
  - [ ] 新增 `test_bulk_close_does_not_touch_waiting_review_or_observed`。
  - [ ] seed:open/scheduled/waiting_review/observed/closed 各一条(每态不同 worker 避 active_worker 唯一约束)。
  - [ ] 调 `bulk_close_open` 后断言:waiting_review 单仍 waiting_review;observed 单仍 observed;两者 close_reason 未被设成 batch 原因。
  - [ ] 测试语义:若 WHERE 谓词被改宽(误含 waiting_review/observed),本测试能抓。
- **Depends on:** —

## Task 3: I2/I4 不变式反向断言
- **Goal:** 钉死关闭态 closed_at 必非空(I2)+ ACTIVE 态 active_worker 必非空(I4)。
- **Files:** `src/backend/tests/community/core/economy/governance/test_domain_model.py`
- **Done when:**
  - [ ] I2:`test_close_sets_closed_at_nonnull` — `close()` 后 `assert closed_at is not None`。
  - [ ] I4:`test_active_states_have_active_worker` — 参数化 OPEN/SCHEDULED/WAITING_REVIEW,`assert assignee is not None`。
  - [ ] I4 反向(可选,贴既有):`close()`/`enter_observed()` 后 `assignee is None`(close 补,enter_observed 已有)。
  - [ ] 断言就近 TestTicketEnterObserved/TestTicketTransitions 类。
- **Depends on:** —

## Task 4: 回归验证
- **Goal:** 全量回归确认三项零退步。
- **Files:** —
- **Done when:**
  - [ ] governance + lifecycle 契约套件绿(基线 754+ 不退步)。
  - [ ] community 全量 8180 不退步。
  - [ ] (稳妥)DSL admin 套件确认 OBSERVED 路径不破。
  - [ ] gitlink:`git add ocb-public` 顶住本期 commits(守 singlebox 不被拽回)。
- **Depends on:** Task 1, 2, 3

---

## Groups

- **Group A — 收口重构(核心):** Task 1
  - Theme: enter_observed 状态机动作与关单语义解绑,open_observed_ticket 复用,消除"转 OBSERVED"散两处。
- **Group B — 补测守卫:** Tasks 2, 3
  - Theme: bulk WHERE 谓词断言 + I2/I4 不变式反向断言(可并行,纯补测)。
- **Group C — 回归:** Task 4
  - Theme: 全量回归 + gitlink 顶住。