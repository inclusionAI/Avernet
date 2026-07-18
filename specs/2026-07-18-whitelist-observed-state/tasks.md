# Tasks: 白名单观察态(Whitelist OBSERVED State)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

---

## Step 1 — 谓词收口(纯重构,零行为变化) ✅ 已落地

### Task 1: 加状态族常量
- **Goal:** 在 `domain/enums.py` 引入 `ACTIVE_STATUSES`/`TERMINAL_STATUSES` 命名常量,作为 ticket 侧 `governance_status` 谓词的公共来源。
- **Files:** `src/agentclaw/community/core/economy/governance/domain/enums.py`
- **Done when:**
  - [x] `ACTIVE_STATUSES = frozenset({GovernanceStatus.OPEN, SCHEDULED, WAITING_REVIEW})` 定义(Step1 时不含 OBSERVED)。
  - [x] `TERMINAL_STATUSES = frozenset({CLOSED})` 定义(Step2 扩为 {CLOSED, OBSERVED})。
  - [x] 常量带 docstring 说明"ticket 侧谓词唯一来源,通知侧同名列不引此常量"。
  - [x] 单测 `test_status_constants` 断言 OPEN∈ACTIVE、CLOSED∈TERMINAL、二者无交集。
- **Depends on:** —

### ✅ Task 2: 收口 task_record_query.py 谓词
- **Goal:** 将该文件 `GovernanceTicketOrm.governance_status` 的**多态集合查询**改为引用 Task1 常量,**单态精确查询**只换枚举不引常量。行为零变化。
- **Files:** `src/agentclaw/community/core/economy/governance/repositories/task_record_query.py`
- **收口判据(逐处套用):**
  - **多态集合查询**(语义集合,会因加态而变,散落重复) → **引常量**。例:`in_(("open","scheduled","waiting_review"))` → `in_(ACTIVE_STATUSES)`。收益:加态改一处即全同步,消除散点。
  - **单态精确查询**("就是等于某态",加态不影响它) → **只换枚举,不引常量**。例:`== "open"` → `== GovernanceStatus.OPEN`。理由:`== open` 一秒可读,包成 `in_(OPEN_ONLY)` 是为统一而统一,损可读性无收益。
  - **调用方传入的 `statuses` 参数** → 查每个调用方:传 FIXED 集则引常量,动态集则保留。
- **Done when:**
  - [x] L35 `find_active_ticket` 的 `in_("open","scheduled","waiting_review")` → `in_(ACTIVE_STATUSES)`(多态集合,引常量)。
  - [x] L146/L199/L226 `=="open"`、L169 `=="scheduled"`、L80 `=="closed"`:单态精确查询 → 只换枚举(`==GovernanceStatus.OPEN` 等),不引常量。L80 同理 `==GovernanceStatus.CLOSED`。
  - [x] L257/298/335/352 `in_(statuses)`:按"调用方传入参数"判据逐个查调用方,FIXED 集引常量,动态保留。
  - [x] 每处改动旁注明判据归类(多态引常量 / 单态换枚举 / 传参保留),便于 review。
  - [x] `test_status_predicate_refactor.py` 新增:对 `find_active_ticket`/`find_latest_closed_by_worker` 用随机数据断言改前后结果集(按 ticket_id 排序)逐行一致。
- **Depends on:** Task 1

### ✅ Task 3: 收口 task_record_repo.py 谓词
- **Goal:** 将该文件 `governance_status` SQL 谓词改为引用常量,行为零变化。
- **Files:** `src/agentclaw/community/core/economy/governance/repositories/task_record_repo.py`
- **Done when:**
  - [x] L225 `in_(("open","scheduled"))` → `in_(...)` 引适当常量或显式枚举(确认该查询语义:open/scheduled 活跃累积,不含 waiting_review → 不能用 ACTIVE_STATUSES,需新建子集常量 `OPEN_OR_SCHEDULED` 或保留显式)。**核查后定**。
  - [x] L231 `:"closed"` 写入 → `:GovernanceStatus.CLOSED`。
  - [x] 回归断言该查询改前后结果集一致。
- **Depends on:** Task 1

### ✅ Task 4: Step1 回归验证 + 提交
- **Goal:** 确认 Step1 零行为变化,全量测试绿,单独 commit。
- **Files:** —
- **Done when:**
  - [x] `cd ocb-public/src/backend && DEPLOY_PROFILE=test uv run pytest tests/community -q` 全绿,计数与基线一致(8132±)。
  - [x] `test_status_predicate_refactor.py` 全过。
  - [x] 单独 commit: `refactor(governance): 收口 ticket governance_status SQL 谓词到命名常量`。
  - [x] commit 不含任何 OBSERVED 相关改动(纯重构)。
- **Depends on:** Task 2, Task 3

---

## Step 2 — 加 OBSERVED 态 ✅ 已落地

### Task 5: 加枚举 + transitions
- **Goal:** 定义 `OBSERVED` 状态及其状态机转换规则。
- **Files:** `src/agentclaw/community/core/economy/governance/domain/enums.py`, `src/agentclaw/community/core/economy/governance/domain/ticket.py`
- **Done when:**
  - [x] `GovernanceStatus.OBSERVED = "observed"` 加入枚举。
  - [x] `TERMINAL_STATUSES = frozenset({CLOSED, OBSERVED})`;`ACTIVE_STATUSES` 确认仍不含 OBSERVED。
  - [x] `TICKET_TRANSITIONS`:OPEN/SCHEDULED/WAITING_REVIEW 可转集加 OBSERVED;新增 `OBSERVED: frozenset({CLOSED})`。
  - [x] 单测:`transition_to(OBSERVED)` 从三活跃态成功;从 OBSERVED 仅可转 CLOSED;OBSERVED→其他抛 `IllegalTicketTransitionError`。
- **Depends on:** Task 4

### Task 6: approve_whitelist 转 OBSERVED + close_reason 统一
- **Goal:** 审批加白不再转 CLOSED,转 OBSERVED;顺带统一 close_reason 字面量 drift。
- **Files:** `src/agentclaw/community/core/economy/governance/domain/ticket.py`, `src/agentclaw/community/core/economy/governance/services/lifecycle_service.py`(review_ticket 调用点), `tests/.../test_domain_model.py`
- **Done when:**
  - [x] `ticket.py:575` `approve_whitelist` 分支:`transition_to(CLOSED)` → `transition_to(OBSERVED)`;`close_reason = close_reason or CloseReason.WHITELIST_APPROVED`(替字面量 `"whitelisted"`)。
  - [x] 保留:清 `remind_at`、释放 `active_worker`、不设 cooldown、取消 pending(在 review_ticket 驱动侧)。
  - [x] docstring L550 `approve_whitelist → CLOSED` 改为 `→ OBSERVED`。
  - [x] `test_domain_model.py:954` 断言改 `close_reason == CloseReason.WHITELIST_APPROVED`(或 `== "whitelist_approved"`)。
  - [x] grep 确认无其他读 `close_reason == "whitelisted"` 的生产代码(测试已仅此一处)。
  - [x] 单测:approve_whitelist 后 `governance_status == OBSERVED`,`close_reason == WHITELIST_APPROVED`。
- **Depends on:** Task 5

### Task 7: close_for_whitelist_hit 转 OBSERVED
- **Goal:** scan 兜底关残留活跃单由转 CLOSED 改为转 OBSERVED。
- **Files:** `src/agentclaw/community/core/economy/governance/services/lifecycle_service.py`
- **Done when:**
  - [x] `close_for_whitelist_hit`(L217):转的目标 CLOSED → OBSERVED;`CloseReason.SCAN_WHITELISTED` 不变;`_cancel_pending` 副作用不变。
  - [x] docstring 同步改"→ OBSERVED(scan_whitelisted)"。
  - [x] 单测:白名单命中且有活跃单 → 单转 OBSERVED、close_reason=SCAN_WHITELISTED、pending 通知被取消。
- **Depends on:** Task 5

### Task 8: 新增 find_observed_ticket 查询
- **Goal:** 提供按 worker 取最近 OBSERVED 单的查询,供刷新与删白收尾用。
- **Files:** `src/agentclaw/community/core/economy/governance/repositories/task_record_query.py`, `domain/protocols.py`
- **Done when:**
  - [x] `find_observed_ticket(worker_id)`:`governance_status == OBSERVED`、按 `gmt_modified DESC`、`worker_id == worker_id`(用 worker_id 非 active_worker,对齐 find_latest_closed 口径)。
  - [x] 加入 `TaskRecordRepositoryProtocol`(protocols.py)。
  - [x] 单测:有 OBSERVED 单返回最近一条;无返回 None;不影响 find_active_ticket。
- **Depends on:** Task 5

### Task 9: open_observed_ticket 瘦建单路径
- **Goal:** lifecycle driver 上新增建 OBSERVED 单的方法,不建通知、不设 delivery_status。
- **Files:** `src/agentclaw/community/core/economy/governance/services/lifecycle_service.py`, `services/service_protocols.py`
- **Done when:**
  - [x] `open_observed_ticket(*, ticket: GovernanceTicket) -> str`:建 `GovernanceTicket.create` → 直接 `transition_to(OBSERVED)`(不经 OPEN)→ `save_ticket`;**不调** notify_repo、**不调** update_delivery_status。
  - [x] Protocol `GovernanceLifecycleServiceProtocol` 加该方法签名。
  - [x] 单测:建出的单 status=OBSERVED、notify_log 零新增、delivery_status 未被写。
- **Depends on:** Task 5, Task 8

### Task 10: record_process_service 三路重写
- **Goal:** 白名单命中分支按三路分发:有 OBSERVED 单刷新 / 无单建观察单。
- **Files:** `src/agentclaw/community/core/economy/governance/services/record_process_service.py`
- **Done when:**
  - [x] `process_record` Step2-4 重排:`is_whitelisted` 命中时先 `find_observed_ticket`;有 OBSERVED 单 → 复用 `refresh_snapshot` 链路(状态不变、dt_version guard 生效、不发通知);无活跃单无 OBSERVED 单 → `lifecycle_svc.open_observed_ticket`。
  - [x] 有活跃单(非白名单场景)仍走原 `_handle_active_ticket_refresh`。
  - [x] OBSERVED 刷新审计 action 用 `AuditAction.WHITELIST_OBSERVED`(Task11 加枚举)。
  - [x] 单测三路各一例:①白名单+有OBSERVED→刷新+状态不变+notify零增;②白名单+无单→建观察单+notify零增;③非白名单→原流程。
- **Depends on:** Task 8, Task 9

### Task 11: 审计动作 + close_reason 收尾定稿
- **Goal:** 落定 Open Q1/Q2:观察刷新/删白收尾的审计与 close_reason。
- **Files:** `domain/enums.py`(AuditAction, CloseReason), `services/record_process_service.py`, `services/whitelist_service.py`, `services/lifecycle_service.py`
- **Done when:**
  - [x] **新增** `AuditAction.WHITELIST_OBSERVED`(已定)——用于观察刷新(白名单 bot 已有 OBSERVED 单时刷新其快照)。与 `SCAN_WHITELISTED`(无活跃单命中加白)区分:`WHITELIST_OBSERVED`=持续刷新观察单;`SCAN_WHITELISTED`=scan 兜底关残留/记加白命中。
  - [x] Task 10 的观察刷新审计改用 `AuditAction.WHITELIST_OBSERVED`(替原"复用 STILL_ACTIONABLE"的暂定)。
  - [x] 删白收尾 OBSERVED→CLOSED 的 close_reason 用 `CloseReason.WHITELIST_APPROVED`(复用,不新加,语义=加白结束)。
  - [x] `AuditAction.WHITELIST_OBSERVED` 新枚举值的单测覆盖(断言其 value 字符串、可在审计行写入读回)。
- **Depends on:** Task 10

### Task 12: 删白触发 OBSERVED→CLOSED 收尾
- **Goal:** `delete_whitelist_entry` 删白后把对应 OBSERVED 单收尾为 CLOSED。
- **Files:** `src/agentclaw/community/core/economy/governance/services/whitelist_service.py`, DI binding
- **Done when:**
  - [x] whitelist_service 注入 `GovernanceLifecycleServiceProtocol`(单向,验证无循环依赖)。
  - [x] `delete_whitelist_entry`:`remove` 之后 best-effort `find_observed_ticket(worker_id)` → 命中则 `close_observed_for_removal`(OBSERVED→CLOSED,不设 cooldown);无单跳过。
  - [x] `lifecycle_service` 加 `close_observed_for_removal(ticket_id, *, now)`。
  - [x] 单测:删白→OBSERVED 单转 CLOSED;再 off-batch(is_whitelisted=False)→ 建 OPEN 新单 + 发通知。
- **Depends on:** Task 9, Task 11

### Task 13: 工单列表/详情可见 OBSERVED
- **Goal:** 确认 workflow 工单列表/详情按状态过滤能见 OBSERVED 单,补 status 枚举校验。
- **Files:** `adapters/http/workflow_router.py`, `adapters/http/economy/schemas.py`
- **Done when:**
  - [x] `list_review_tickets` 的 statuses 入参校验把 `observed` 加进合法集(若有校验)。
  - [x] 不新增端点;评审按 status=observed 可筛出观察单。
  - [x] 单测:GET 列表带 statuses=observed 返回观察单及其最新快照。
- **Depends on:** Task 10

---

## Task 14: 验收 & 全量回归
- **Goal:** 逐一过 spec 验收项,全量测试绿。
- **Files:** —
- **Done when:**
  - [x] Step1 验收:谓词收口纯重构、全量 community 绿、单独 commit。
  - [x] 状态机:OBSERVED 进出 transitions 符合 spec;属 closed 族,ACTIVE_STATUSES 不含。
  - [x] 三路产 OBSERVED 各一例绿。
  - [x] 不发通知守卫:OBSERVED 单全周期 notify_log 零增量。
  - [x] cooldown 隔离:OBSERVED 不被 find_latest_closed_by_worker 命中;删白后无 cooldown 立即重建。
  - [x] 删白路径:OBSERVED→CLOSED→off-batch 重建 OPEN。
  - [x] 画像可见:workflow 列表可见 OBSERVED + 最新快照。
  - [x] `DEPLOY_PROFILE=test uv run pytest tests/community -q` 全绿。
  - [x] `DEPLOY_PROFILE=corp_test uv run pytest tests/corp -q` 全绿(corp 若有依赖)。
- **Depends on:** all

---

## Groups

- **Group A — 谓词收口(纯重构):** Tasks 1, 2, 3, 4 ✅
  - Theme: 把 ticket 侧 governance_status SQL 谓词收口到命名常量,零行为变化,单独落地验证。
- **Group B — OBSERVED 状态机骨架:** Tasks 5, 6, 7 ✅
  - Theme: 加枚举/transitions,审批与 scan 兜底两路转 OBSERVED。
- **Group C — 观察单生产与刷新:** Tasks 8, 9, 10, 11 ✅
  - Theme: 查询/瘦建单/三路分发/审计定稿,让白名单 bot 持续刷新画像不发通知。
- **Group D — 删白收尾与可见性:** Tasks 12, 13 ✅
  - Theme: 删白 OBSERVED→CLOSED,评审列表可见观察单。
- **Group E — 验收:** Task 14 ✅ (全量 community 8180 passed + corp 1465 passed)
- **Group E — 验收:** Task 14
  - Theme: 全 spec 验收项 + 全量回归。