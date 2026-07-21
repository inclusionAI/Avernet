# Plan: 加白转态单一驱动收口

## Approach

lifecycle driver 加"加白→转 OBSERVED"单条语义方法 `observe_for_whitelist`(由现有 `close_for_whitelist_hit` 重命名收口)+ 批量版 `bulk_observe_by_ticket_ids`(对齐 `bulk_close_by_ticket_ids` 范式复用单条)。四条加白入口统一经它。核心修复:`bulk_whitelist` 第4步从 `bulk_close_by_ticket_ids`(→CLOSED)改调 `bulk_observe_by_ticket_ids`(→OBSERVED)。不动转换矩阵/枚举/族(守红线)。

## Affected Components

- `services/lifecycle_service.py` — 重命名 `close_for_whitelist_hit`→`observe_for_whitelist`(单条)+ 新增 `bulk_observe_by_ticket_ids`(批量复用单条)
- `services/service_protocols.py` — Protocol 签名同步(重命名 + 新增 bulk_observe)
- `services/whitelist_service.py:136` — `bulk_whitelist` 第4步改调 `bulk_observe_by_ticket_ids`(核心修复)
- `services/record_process_whitelist.py:72` — 路1 调用方同步重命名(`observe_for_whitelist`)
- `domain/ticket.py:530` / `services/record_process_whitelist.py` docstring — 引用同步
- 测试:`test_lifecycle_service.py`(重命名测试 + 新 bulk_observe 测试)+ `test_whitelist_service.py`(bulk_whitelist 转 OBSERVED)+ 可能端点/DSL

## Data Model Changes

无。不改 DDL、不改枚举、不改转换矩阵。

## API / Interface Changes

- driver 方法重命名(`close_for_whitelist_hit`→`observe_for_whitelist`):内部 service 间契约,非对外 HTTP。调用方同步改(record_process 路1)。
- 新增 `bulk_observe_by_ticket_ids`:内部 service 间方法。
- `/admin/whitelist:bulk-add` 对外响应不变(`{whitelisted, cancelled}`),仅被关工单的状态从 CLOSED→OBSERVED(行为修正)。

## Key Files & Functions

### ① 重命名 + 单条语义方法(`lifecycle_service.py`)

- `close_for_whitelist_hit`(L268)→ `observe_for_whitelist(self, ticket_id, *, close_reason, now) -> bool`
  - 行为不变:find → `enter_observed(close_reason)` → save → `_cancel_pending`(best-effort)。`del now`(OBSERVED 不设 closed_at)。
  - docstring 改:从"scan 清理白名单 bot 残留活跃单"泛化为"加白→转 OBSERVED 单条语义方法(scan 兜底/批量加白漏关均经此)"。
  - `now` 参数保留(签名兼容),`del now` 不变。

### ② 批量语义方法(`lifecycle_service.py`)

- 新增 `bulk_observe_by_ticket_ids(self, ticket_ids, *, now, close_reason=CloseReason.WHITELIST_APPROVED) -> int`
  - 范式对齐 `bulk_close_by_ticket_ids`(L632):逐条调 `observe_for_whitelist`,守卫激活,幂等(已 OBSERVED/CLOSED/not-found 返 False 不计)。
  - close_reason 默认 `WHITELIST_APPROVED`(批量加白 admin 主动,语义同审批加白);调用方可覆盖。
  - 返回实际转 OBSERVED 的工单数。
- 新增 `bulk_observe_by_ticket_ids` 到 Protocol(`service_protocols.py`)。

### ③ 核心修复:`bulk_whitelist` 第4步(`whitelist_service.py:136`)

- `self._lifecycle_svc.bulk_close_by_ticket_ids(ticket_ids, now=now)` → `self._lifecycle_svc.bulk_observe_by_ticket_ids(ticket_ids, now=now)`
- 上方 `bulk_cancel_by_bots` 的 `close_reason=ADMIN_CLOSED`(L129)是**通知侧**取消原因,不动(通知取消仍 ADMIN_CLOSED 语义;工单转 OBSERVED 才是加白语义)—— plan 须确认这两套 close_reason 不冲突(通知侧 ADMIN_CLOSED 是通知关停原因,工单侧 WHITELIST_APPROVED 是工单转态原因,各自独立列)。
- 审计 `ADMIN_WHITELIST`(L142)不变。

### ④ 调用方同步重命名

- `record_process_whitelist.py:72` `close_for_whitelist_hit` → `observe_for_whitelist`
- docstring 引用(`ticket.py:530`、`record_process_whitelist.py:4/57`、`lifecycle_service.py:338` 自引用)同步

## 关键设计决策(plan 定稿)

1. **close_reason**:bulk_whitelist 转态用 `CloseReason.WHITELIST_APPROVED`(复用,不新增枚举)。批量加白是 admin 主动,语义同审批加白(都"加白")。`SCAN_WHITELISTED` 留给 scan 兜底(它语义是"scan 遇到白名单 bot 顺手收尾",非 admin 主动加白)。
2. **方法形态**:单条 `observe_for_whitelist` + 批量 `bulk_observe_by_ticket_ids` 复用单条,对齐既有 `bulk_close_by_ticket_ids` 范式。不做成单一方法支持两种(保持与既有 bulk_close/open 范式一致)。
3. **重命名 close_for_whitelist_hit**:它是"加白→转 OBSERVED"单条版,命名偏 scan 兜底,重命名收口。影响面小(1 个生产调用方 + docstring)。

## Risks & Mitigations

- **Risk**:重命名 `close_for_whitelist_hit` 漏改调用方致 import/name 漂移。
  **Mitigation**:grep 全仓调用方(plan 已查:record_process_whitelist L72 唯一生产调用 + 若干 docstring);重命名后跑 governance 全套确认无 NameError。
- **Risk**:bulk_whitelist 通知侧 `ADMIN_CLOSED` 与工单侧 `WHITELIST_APPROVED` 两套 close_reason 语义混淆。
  **Mitigation**:plan 已析清——通知侧 close_reason 是"通知关停原因",工单侧 close_reason 是"工单转态原因",两列独立(ac_governance_notify_log.close_reason vs ac_governance_task_record.close_reason)。加测试分别断言两列。
- **Risk**:bulk_observe 逐条守卫,若 ticket_ids 含已 OBSERVED 单(竞态:加白时该单已是观察态),幂等返 False 不计,不重复转。
  **Mitigation**:`observe_for_whitelist` 经 `enter_observed` 守卫(OBSERVED→OBSERVED 非法抛错被 audit_illegal 捕获返 False),幂等。加测试:含已 OBSERVED 单的批量不重转、计数正确。
- **Risk**:DSL `bulk_whitelist` step(若存在)端到端覆盖。
  **Mitigation**:查 DSL 有无 bulk_whitelist step;有则加 TC 钉死转 OBSERVED,无则端点测试补。

## Alternatives Considered

- **不重命名 close_for_whitelist_hit,只新加 observe_for_whitelist**:两方法同语义并存,收口不彻底,未来仍可能各调各的。弃,重命名才收口。
- **bulk_observe 直接 SQL UPDATE 转态(同 bulk_close_open 范式)**:旁路守卫,与"单一驱动经领域守卫"矛盾。弃,逐条走 enter_observed 守卫。
- **单一方法支持单条+批量(参数 ticket_ids: str | list)**:签名歧义,与既有 bulk_close_by_ticket_ids/admin_close 范式不一致。弃,单条+批量两方法。
- **新增 CloseReason.BULK_WHITELIST_APPROVED 枚举**:枚举膨胀,且语义同 WHITELIST_APPROVED(都加白)。弃,复用。

## Rollout

- 可拆三 commit:①重命名+单条语义方法(含 Protocol)②新增 bulk_observe_by_ticket_ids ③bulk_whitelist 改调 + 测试。或一个 commit(主题一致)。建议拆,任一出问题精准回滚。
- 无 feature flag、无 DDL、无对外契约变更(响应壳不变,仅被关工单状态修正)。
- 回归:governance + lifecycle 契约 + endpoint 套件 + DSL(若有 bulk step)。

## Test Strategy

- ①重命名:既有 `TestCloseForWhitelistHit` 改名 `TestObserveForWhitelist`,断言不变(行为零变化);确认 record_process 路1 测试不退步。
- ②bulk_observe:新增 `TestBulkObserveByTicketIds` — 批量转 OBSERVED + 幂等(含已 OBSERVED 单不重转)+ close_reason=WHITELIST_APPROVED + not-found 不计。
- ③bulk_whitelist 修复:`test_bulk_whitelist_transitions_to_observed` — 批量加白后活跃单 status=OBSERVED(非 CLOSED)+ close_reason=WHITELIST_APPROVED + 通知侧 ADMIN_CLOSED 独立 + 不发通知。
- 全量:governance 754+ / community 8198 不退步;DSL 若有 bulk_whitelist step 加 TC。