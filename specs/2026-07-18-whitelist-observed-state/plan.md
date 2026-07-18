# Plan: 白名单观察态(Whitelist OBSERVED State)

## Approach

两步走,各自独立可 review/回滚:

- **Step 1(纯重构)**:把 ticket 侧 `governance_status` 的 SQL 谓词收口到 `enums` 模块的命名常量组(`ACTIVE_STATUSES` / 终态判断),repo 层所有 `in_(...)`/`==` 引用之。行为零变化,回归测试钉死结果集一致。
- **Step 2(加态)**:在收口后的常量上加 `GovernanceStatus.OBSERVED`(closed 族),定义 transitions,改三路产生/转入,加刷新路径,删白收尾。

核心不变式驱动设计:**OBSERVED 属 closed 族,`ACTIVE_STATUSES` 不含它** → `find_active_ticket` 天然不返回观察单 → 不发通知、不被投递/admin 操作,全靠"归属族"这一条收口后的常量决定,而非散点特判。

## Affected Components

- `domain/enums.py` — 加 `OBSERVED`;加状态族常量 `ACTIVE_STATUSES`/`TERMINAL_STATUSES`;顺带统一 `CloseReason` 的 `whitelisted` 字面量 drift(见下)。
- `domain/ticket.py` — `TICKET_TRANSITIONS` 加 OBSERVED 进出;`review()` 的 `approve_whitelist` 分支转 OBSERVED(不转 CLOSED)。
- `repositories/task_record_query.py` — 谓词收口(Step1);新增 `find_observed_ticket(worker_id)`(Step2)。
- `repositories/task_record_repo.py` — 谓词收口(Step1, L225/231)。
- `repositories/orm.py` — `GovernanceTicketOrm.is_active` 等 Python 属性也已存在(613-636),核对与常量一致。
- `services/lifecycle_service.py` — `close_for_whitelist_hit` 转 OBSERVED;新增 `open_observed_ticket`(建观察单瘦路径) + `close_observed_for_removal`(删白收尾)。
- `services/record_process_service.py` — `_handle_whitelist_hit` 三路重写:有活跃单→不变(走 scan 兜底);有 OBSERVED 单→刷新;无单→建观察单。
- `services/whitelist_service.py` — `delete_whitelist_entry` 删白后触发 OBSERVED→CLOSED 收尾(注入 lifecycle svc)。
- `adapters/http/workflow_router.py` + `schemas` — 工单列表/详情按状态过滤天然可见 OBSERVED(可能需补 status 枚举校验)。

## Data Model Changes

- **无 DDL**:OBSERVED 复用 `governance_status` 既有 `String(16)` 列,新增枚举值 `"observed"`,无需 ALTER。
- `CloseReason` 统一:ticket.py:576 `"whitelisted"` 字面量与枚举 `CloseReason.WHITELIST_APPROVED` 不一致(已存在 drift),本次统一为枚举常量。**注意**:这会改变该列历史/新写入的字面量从 `"whitelisted"`→`"whitelist_approved"`,需确认无下游硬编码读 `"whitelisted"`(Step1 调研)。

## API / Interface Changes

- 无新 HTTP 端点。工单列表 `GET /workflow/tickets` 按状态过滤,`statuses` 入参若校验枚举,需把 `observed` 加进合法集。
- service 间新增 `lifecycle_svc.open_observed_ticket` / `close_observed_for_removal` / `close_for_whitelist_hit`(签名不变,行为改)。

## Key Files & Functions

### Step 1 — 谓词收口(纯重构)

- `domain/enums.py`(新常量):
  ```python
  ACTIVE_STATUSES = frozenset({OPEN, SCHEDULED, WAITING_REVIEW})
  TERMINAL_STATUSES = frozenset({CLOSED})  # Step2 扩为 {CLOSED, OBSERVED}
  ```
- `repositories/task_record_query.py`:
  - L35 `find_active_ticket` 的 `in_("open","scheduled","waiting_review")` → `in_(ACTIVE_STATUSES)`
  - L80 `find_latest_closed_by_worker` 的 `=="closed"` → `==CLOSED`(暂不引常量,它语义是单纯 closed)
  - L146/L199/L226 `=="open"`、L169 `=="scheduled"`、L257/298/335/352 `in_(statuses)`(这些 `statuses` 是调用方传入,逐个确认调用方语义,收口到常量或保留显式)
- `repositories/task_record_repo.py`:
  - L225 `in_(("open","scheduled"))`、L231 `:"closed"` → 引常量
- **回归测试**:新增 `tests/.../test_status_predicate_refactor.py`,对每个改动的查询,断言改前后结果集(按 env 全量)逐行一致;photo-snapshot 工单+通知表随机数据。

### Step 2 — 加 OBSERVED 态

- `domain/enums.py`:加 `OBSERVED = "observed"`;`TERMINAL_STATUSES = frozenset({CLOSED, OBSERVED})`;`ACTIVE_STATUSES` 不含 OBSERVED。
- `domain/ticket.py:78` `TICKET_TRANSITIONS`:
  - `OPEN/SCHEDULED/WAITING_REVIEW` 的可转集加 `OBSERVED`
  - 新增 `OBSERVED: frozenset({CLOSED})`  (仅删白收尾转 CLOSED)
- `domain/ticket.py:575` `review()` `approve_whitelist` 分支:`self.transition_to(GovernanceStatus.OBSERVED)`(替 CLOSED);`close_reason = close_reason or CloseReason.WHITELIST_APPROVED`;保留清 remind_at、释放 active_worker、不设 cooldown。**docstring L550 同步改**。
- `services/lifecycle_service.py:217` `close_for_whitelist_hit`:`ticket` 转的目标从 CLOSED 改 OBSERVED;`CloseReason.SCAN_WHITELISTED` 不变;副作用 `_cancel_pending` 不变。**新增 method `open_observed_ticket`(瘦建单)**:建 `GovernanceTicket.create(...)` → 直接 `transition_to(OBSERVED)`(不经 OPEN)→ `save_ticket`;**不建 notify_log、不 update_delivery_status**。签名对齐 `open_ticket` 但落 OBSERVED。
- `services/lifecycle_service.py` **新增 `close_observed_for_removal(ticket_id, *, now)`**:`OBSERVED → CLOSED`,`close_reason = CloseReason.WHITELIST_APPROVED`(或新增 `WHITELIST_REMOVED`?见 Open Q),不设 cooldown,写审计由调用方(whitelist_service)持有(对齐 admin_close 的审计归属约定)。
- `services/record_process_service.py:220-242` `process_record` 主流程改:
  - 取单目标:`active = find_active_ticket`;若 `is_whitelisted` 且 active 为 None → `observed = find_observed_ticket(worker_key)`。
  - 白名单命中且 **有 OBSERVED 单** → 复用 `_handle_active_ticket_refresh` 的刷新链路(它不查状态只按 ticket_id 刷,天然适用),但审计 action 用观察语义(Open Q);**状态不变**。
  - 白名单命中且 **无活跃单无 OBSERVED 单** → `lifecycle_svc.open_observed_ticket(...)`(瘦建单),审计 WHITELIST_OBSERVED。
  - 非白名单 → 原流程。
- `services/whitelist_service.py:157` `delete_whitelist_entry`:`remove` 之后,注入的 `lifecycle_svc.close_observed_for_removal` 找该 worker OBSERVED 单转 CLOSED(best-effort,无单则跳过)。**跨域依赖**:whitelist_service 当前不依赖 lifecycle/task_repo,需 DI 注入 `GovernanceLifecycleServiceProtocol`(Injector 已支持)。
- `repositories/task_record_query.py`:新增 `find_observed_ticket(worker_id)` — `governance_status == OBSERVED`,按 `gmt_modified DESC` 取最近一条。

## 不发通知不变式的落点(Step2 验证)

- `find_active_ticket` 用 `ACTIVE_STATUSES`(不含 OBSERVED)→ delivery_service.py:399/admin_service.py:468 等消费方天然不操作观察单。
- `open_observed_ticket` 不调 `notify_repo.add_notification` / 不调 `update_delivery_status`。
- 守卫测试:off-batch 刷一条 OBSERVED 单后,`ac_governance_notify_log` 该 ticket 无新增行。

## cooldown 不受影响(Step2 验证)

- `find_latest_closed_by_worker`(task_record_query.py:80)仍 `==CLOSED`,不含 OBSERVED → 观察单不进 cooldown。
- 删白 OBSERVED→CLOSED 后,该单 close_reason 不带 cooldown_until → 下次 off-batch `is_whitelisted=False`、active=None → Step5 看不到 cooldown → Step6 建 OPEN 新单。回归测试覆盖。

## Risks & Mitigations

- **Risk**:Step1 重构虽然行为不变,但横切 ~14 处谓词,misjudge 一处 `in_(statuses)` 的调用方语义 → 静默改变查询集。
  **Mitigation**:Step1 单独 commit;每处改动配针对性回归断言(改前后 in_ 集合的字符串化完全一致);先跑全量 community 套件(8132 用例)做基线,改后必须同绿同数。
- **Risk**:notify_log 侧 `governance_status` 是通知表同名列,语义不同(建通知时工单状态快照),误收口会污染通知投递过滤。
  **Mitigation**:**Step1 只收口 ticket 侧(`GovernanceTicketOrm`)谓词,notify_log 侧(`GovernanceNotificationOrm`)8 处不触**。plan/spec 双重划界。
- **Risk**:`whitelisted`→`whitelist_approved` 字面量统一可能撞下游(前端/审计读)硬编码。
  **Mitigation**:Step1 前先 grep 全仓 `"whitelisted"`(含 ocb 主仓 corp 与前端)排查下游;若撞则保留旧字面量、仅新写入统一,或在 plan 再议。
- **Risk**:whitelist_service 注入 lifecycle svc 产生循环依赖(whitelist ↔ lifecycle)。
  **Mitigation**:单向注入(whitelist → lifecycle),lifecycle 不反向依赖 whitelist;DI binding 单测验证可解析。
- **Risk**:OBSERVED 单无 active_worker(加白关单时已释放),`find_observed_ticket` 用 `worker_id` 而非 `active_worker` 查(参考 `find_latest_closed_by_worker` 用 worker_id 的理由:closed 后 active_worker 置 NULL)。
  **Mitigation**:`find_observed_ticket` 查询用 `worker_id == worker_key`,与 `find_latest_closed_by_worker` 同口径。

## Alternatives Considered

- **加字段 `is_observed` 不动状态机**:blast radius 小,但"观察"是生命周期态有进有出,状态机表达更完整;且用户已拍板要状态机。放弃。
- **复用 `_create_new_ticket` 建观察单再跳过通知**:该函数深度耦合 first_send(render MD、建 notify_log、update_delivery_status、审计 ENQUEUED),条件跳过会埋多条 if、偏离 SRP。改用独立瘦路径 `open_observed_ticket`,职责单一。
- **删白时 OBSERVED→OPEN 复活同单**:违背用户"删白后等 off-batch 正常触发新建"语义,且复活会立即触发通知。放弃,用 OBSERVED→CLOSED 终态。
- **OBSERVED 进 ACTIVE_STATUSES**(被 find_active_ticket 看到):则 delivery/admin 会操作观察单、删白后无法重建(总被当成有活跃单)。违背"不发通知""删白可重建"。放弃,归 closed 族。

## Rollout

- 无 feature flag 需求(状态枚举值纯新增,旧数据无 observed)。
- 灰序:Step1 先合(纯重构,零行为风险)→ Step2 再合。两步均可独立回滚。
- 向后兼容:存量加白 CLOSED 工单**不回填** OBSERVED(spec Out of Scope);仅新流转走 OBSERVED。

## Test Strategy

- **Step1 回归**:`test_status_predicate_refactor.py` — 全量 env 数据快照断言每个谓词查询结果集改前后一致;全量 community 套件同绿同计数。
- **Step2 契约**:
  - 三路产 OBSERVED:审批加白/scan兜底/off-batch新建,各一例断言终态=OBSERVED、notify_log 无新增。
  - 刷新:off-batch 对 OBSERVED 单刷新,dt_version guard 生效(stale 跳过),状态不变,notify_log 无新增。
  - 删白:OBSERVED→CLOSED,再 off-batch 重建 OPEN 新单 + 发通知。
  - 不发通知守卫:OBSERVED 单全周期 notify_log 零增量。
  - cooldown 隔离:OBSERVED 单不被 `find_latest_closed_by_worker` 命中。
  - 谓词族:`ACTIVE_STATUSES` 不含 OBSERVED 断言(防回归)。
- 复用 `tests/community/core/economy/governance/test_governance_delete.py` 既有白名单用例基线。

## Open Questions(继承 spec + plan 新增)

1. 审计动作:观察刷新新增 `AuditAction.WHITELIST_OBSERVED` vs 复用 `SCAN_WHITELISTED`?倾向新增。
2. `close_reason`:OBSERVED 单的 close_reason 用 `CloseReason.WHITELIST_APPROVED`/`SCAN_WHITELISTED` 区分来源;删白收尾转 CLOSED 时 close_reason 用哪个(新增 `WHITELIST_REMOVED`?或复用 `ADMIN_CLOSED`)?
3. `"whitelisted"` 字面量统一到 `whitelist_approved` 是否撞下游硬编码 — Step1 前需 grep 排查,可能影响本 plan 范围。