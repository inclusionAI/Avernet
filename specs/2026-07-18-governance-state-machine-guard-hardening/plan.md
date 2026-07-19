# Plan: Governance 状态机守卫加固

## Approach

三项小改,主题一致(状态机守卫加固),不动转换矩阵/状态枚举/族划分(守红线)。①是核心(消除"转 OBSERVED"散两处),②③是补测。三项各自独立可 review/可回滚,但一个 spec 收口(主题一致)。

## Affected Components

- `domain/ticket.py` — ①改 `enter_observed` 签名(close_reason 可选)+ `open_observed_ticket` 复用(实现在 lifecycle_service,但触发领域方法)
- `services/lifecycle_service.py:190` — ①`open_observed_ticket` 改调 `enter_observed()` 替手写
- `tests/community/core/economy/governance/test_lifecycle_service.py` — ②bulk WHERE 断言 + ③I2/I4(部分)
- `tests/community/core/economy/governance/test_domain_model.py` — ③I2/I4 领域层断言

## Data Model Changes

无。不改 DDL、不改枚举、不改转换矩阵。

## API / Interface Changes

无对外 HTTP 契约变更。`enter_observed` 是领域私有方法(下划线无,但 ticket 领域内部),签名加默认值 = 向后兼容(既有两路调用方仍传 close_reason,不破)。

## Key Files & Functions

### ① open_observed_ticket 收口(核心)

- `domain/ticket.py:516` `enter_observed(self, *, close_reason: str)` → `close_reason: str | None = None`
  - 实现行 `self.close_reason = close_reason`(L534 区)保持 — None 时设 None(建单场景符合:无关单原因)。
  - docstring 更新:说明 close_reason 可选(None=建单,传值=关单转态),状态机动作(转 OBSERVED+释放 assignee+清 remind_at+不设 closed_at+不碰 cooldown)是主职责,close_reason 是附带语义。
- `services/lifecycle_service.py:190-191` `open_observed_ticket`:
  - 删 `ticket.transition_to(GovernanceStatus.OBSERVED)` + `ticket.assignee = None`
  - 改 `ticket.enter_observed()`(不传 close_reason,建单无需)
  - 其余 add_ticket 字段映射不变(assignee 仍 `ticket.assignee` — enter_observed 已设 None)
- **不动**两路关单调用方:
  - `domain/ticket.py:598` review approve_whitelist:`enter_observed(close_reason=close_reason or WHITELIST_APPROVED)` 不变
  - `services/lifecycle_service.py:287` close_for_whitelist_hit:`enter_observed(close_reason=CloseReason.SCAN_WHITELISTED)` 不变

### ② bulk_close_open WHERE 谓词断言

- `tests/.../test_lifecycle_service.py::TestBulkCloseOpen`(或 test_closes_all_open_and_scheduled 附近,~L514)
- 新增测试 `test_bulk_close_does_not_touch_waiting_review_or_observed`:
  - seed:同 worker 池里 open + scheduled + waiting_review + observed + closed 各一条(不同 worker 避唯一约束)
  - 调 `bulk_close_open`
  - 断言:waiting_review 单 status 仍 waiting_review;observed 单仍 observed;close_reason 未被设成 admin_closed/batch 原因
  - 钉死"WHERE 谓词不含 waiting_review/observed"

### ③ I2/I4 不变式反向断言

- `tests/.../test_domain_model.py`(TestTicketEnterObserved 附近,L864 区)
  - I2:`test_close_sets_closed_at_nonnull` — `_make_ticket(OPEN)`, `t.close(close_reason=..., closed_at=now)`, `assert t.closed_at is not None`
  - I4:`test_active_states_have_active_worker` — 参数化 OPEN/SCHEDULED/WAITING_REVIEW,断言 `_make_ticket(status)`, `assert t.assignee is not None`(create 默认 active=worker)
  - I4 可补一条反向:close/enter_observed 后 `assignee is None`(已有 enter_observed 测了,close 补一条)

## Dependencies

无新依赖。

## Risks & Mitigations

- **Risk**:enter_observed 改可选后,关单两路若有人误传 None 致 close_reason 漏设。
  **Mitigation**:plan 已确认两路都传值(review L599 `close_reason or WHITELIST_APPROVED`、scan L287 `SCAN_WHITELISTED`);改可选不改这两处调用。加一条测试:关单转态后 close_reason 非空(已有 `test_review_approve_whitelist` 断 `close_reason==WHITELIST_APPROVED`、`TestCloseForWhitelistHit` 断 `SCAN_WHITELISTED`,覆盖)。
- **Risk**:open_observed_ticket 改调 enter_observed 后,assignee 设置时序变(原手写在 add_ticket 前,新走 enter_observed 也在 add_ticket 前)——时序一致,无风险。
- **Risk**:②测试 seed 多态单时撞 active_worker 唯一约束。
  **Mitigation**:每态用不同 worker(参照本期 `test_non_observed_idempotent_noop` 的 `worker="owner-1:bot-2"` 范式);closed/observed 的 active_worker=None 不撞(SQLite 允许多 NULL)。

## Alternatives Considered

- **加 enter_observed 强制校验 close_reason 非空(关单场景)**:过度防御,两路调用方都传值,靠测试守即可。弃。
- **把 open_observed_ticket 也传个"建单专用"close_reason(如 OBSERVED_CREATED)**:引入新枚举值,语义噪音(建单非关单,不该有 close_reason)。弃,保持 None。
- **不碰 open_observed_ticket,只补②③测试**:留 medium 真缺陷不修,未来 enter_observed 加副作用仍分叉。弃,本次正是修它。

## Rollout

- 三项可一个 commit(主题一致)或拆三个 commit(各自可回滚)。建议拆三个:①重构、②补测、③补测,任一出问题精准回滚。
- 无 feature flag、无 DDL、无对外契约变更,直接落。
- 回归:governance + lifecycle 契约 + endpoint 套件必须绿;DSL e2e 不需重跑(本期改动不碰 e2e 路径,但若稳妥可跑 admin 套件确认 OBSERVED 路径)。

## Test Strategy

- ①:既有 `TestTicketEnterObserved` + `TestCloseForWhitelistHit` + `TestOpenObservedTicket` + `test_review_approve_whitelist` 全过(行为零变化是核心验证);可加一条 `test_open_observed_ticket_uses_enter_observed`(间接:建单后 status=OBSERVED+assignee=None+close_reason=None+closed_at=None,与 enter_observed 一致)。
- ②:`test_bulk_close_does_not_touch_waiting_review_or_observed` 新增。
- ③:`test_close_sets_closed_at_nonnull` + `test_active_states_have_active_worker` 新增。
- 全量:governance 754+ 不退步;community 全量 8180 不退步。