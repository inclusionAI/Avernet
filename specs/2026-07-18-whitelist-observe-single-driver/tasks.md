# Tasks: 加白转态单一驱动收口

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> 系统修复:加白→转 OBSERVED 单一驱动收口,治本(非补 bulk_whitelist 一个 bug)。
> 守红线:不动 TICKET_TRANSITIONS/枚举/族划分/运维关单路径。

## Task 1: 重命名 close_for_whitelist_hit → observe_for_whitelist(单条语义方法)
- **Goal:** 把"加白→转 OBSERVED"单条语义方法从 scan-兜底命名泛化为加白通用命名,为四入口收口铺路。
- **Files:** `services/lifecycle_service.py`(L268 重命名);`services/service_protocols.py`(Protocol 签名);`services/record_process_whitelist.py:72`(调用方);docstring:`domain/ticket.py:530`、`record_process_whitelist.py:4/57`、`lifecycle_service.py:338`
- **Done when:**
  - [ ] `close_for_whitelist_hit` 重命名为 `observe_for_whitelist(self, ticket_id, *, close_reason, now) -> bool`。
  - [ ] 行为零变化:find → enter_observed(close_reason) → save → _cancel_pending;del now(OBSERVED 不设 closed_at)。
  - [ ] docstring 泛化:从"scan 清理白名单 bot 残留活跃单"改为"加白→转 OBSERVED 单条语义方法(scan 兜底/批量加白漏关均经此)"。
  - [ ] Protocol `GovernanceLifecycleServiceProtocol` 签名同步重命名。
  - [ ] `record_process_whitelist.py:72` 路1 调用方改调 `observe_for_whitelist`,docstring 同步。
  - [ ] grep 全仓无 `close_for_whitelist_hit` 残留(除 git 历史)。
  - [ ] 既有 `TestCloseForWhitelistHit` 改名 `TestObserveForWhitelist`,断言不变,全过。
- **Depends on:** —

## Task 2: 新增 bulk_observe_by_ticket_ids(批量复用单条)
- **Goal:** 提供批量"加白→转 OBSERVED"方法,逐条复用 observe_for_whitelist 守卫,对齐 bulk_close_by_ticket_ids 范式。
- **Files:** `services/lifecycle_service.py`(新增);`services/service_protocols.py`(Protocol)
- **Done when:**
  - [ ] `bulk_observe_by_ticket_ids(self, ticket_ids, *, now, close_reason=CloseReason.WHITELIST_APPROVED) -> int`。
  - [ ] 逐条调 `observe_for_whitelist`,守卫激活;幂等(已 OBSERVED/CLOSED/not-found/非法态返 False 不计)。
  - [ ] 返回实际转 OBSERVED 的工单数。
  - [ ] Protocol 加签名。
  - [ ] docstring:对齐 bulk_close_by_ticket_ids 风格,说明 close_reason 默认 WHITELIST_APPROVED(批量加白 admin 主动,语义同审批加白)。
- **Depends on:** Task 1

## Task 3: 核心修复 — bulk_whitelist 第4步改调 bulk_observe_by_ticket_ids
- **Goal:** 批量加白(/admin/whitelist:bulk-add)把活跃单转 OBSERVED(非 CLOSED),与加白语义对齐。
- **Files:** `services/whitelist_service.py:136`
- **Done when:**
  - [ ] `self._lifecycle_svc.bulk_close_by_ticket_ids(ticket_ids, now=now)` → `self._lifecycle_svc.bulk_observe_by_ticket_ids(ticket_ids, now=now)`。
  - [ ] 通知侧 `bulk_cancel_by_bots` 的 `close_reason=ADMIN_CLOSED`(L129)**不动** — 那是通知关停原因(独立列),与工单转态 close_reason 分开。
  - [ ] 审计 `ADMIN_WHITELIST`(L142)不变。
  - [ ] 返回值 `{whitelisted, cancelled}` 契约不变。
  - [ ] bulk_whitelist 第4步注释从"Ticket-side close"改为"Ticket-side observe → OBSERVED"。
- **Depends on:** Task 2

## Task 4: 测试 — 单条重命名 + bulk_observe + bulk_whitelist 修复
- **Goal:** 钉死单一驱动收口行为 + bulk_whitelist 修正。
- **Files:** `tests/community/core/economy/governance/test_lifecycle_service.py`;`tests/community/core/economy/governance/test_whitelist_service.py`
- **Done when:**
  - [ ] `TestObserveForWhitelist`(原 TestCloseForWhitelistHit 改名):行为零变化,全过。
  - [ ] 新增 `TestBulkObserveByTicketIds`:批量转 OBSERVED + 幂等(含已 OBSERVED 单不重转、计数正确)+ close_reason=WHITELIST_APPROVED + not-found 不计。
  - [ ] `test_bulk_whitelist_transitions_to_observed`:批量加白后活跃单 status=OBSERVED(非 CLOSED)+ close_reason=WHITELIST_APPROVED + 通知侧 ADMIN_CLOSED(独立列,断言 notify_log.close_reason 仍是 ADMIN_CLOSED)+不发通知(notify 无新增 first_send)。
  - [ ] 既有 bulk_whitelist 测试(`test_bulk_whitelist` 等)若断言 CLOSED 需同步改 OBSERVED — 核查后改。
- **Depends on:** Task 3

## Task 5: 回归 + gitlink
- **Goal:** 全量回归确认零退步 + 顶 gitlink。
- **Files:** —
- **Done when:**
  - [ ] governance + lifecycle 契约 + endpoint 套件绿(基线 1256+ 不退步)。
  - [ ] community 全量 8198 不退步。
  - [ ] DSL:查有无 bulk_whitelist step,有则确认/加 TC 钉死转 OBSERVED;无则端点测试已覆盖(Task 4)。
  - [ ] `git add ocb-public` 顶住本期 commits(守 singlebox 不被拽回,本期教训)。
- **Depends on:** Task 4

---

## Groups

- **Group A — 单一驱动收口:** Tasks 1, 2
  - Theme: 重命名单条语义方法 + 新增批量版,建立"加白→转 OBSERVED"单一驱动。
- **Group B — 修复 + 测试:** Tasks 3, 4
  - Theme: bulk_whitelist 改调 + 钉死行为(含幂等/两套 close_reason 独立)。
- **Group C — 回归:** Task 5
  - Theme: 全量回归 + 顶 gitlink。