# 白名单观察态(Whitelist OBSERVED State)

## Summary

为治理工单状态机引入第 5 态 `OBSERVED`。当一名 bot 进入治理白名单后，其治理画像不再冻结于加白时那条 CLOSED 工单，而是持续由 offline-batch 刷新到一条处于 `OBSERVED` 态的工单上。评审复用现有工单列表/详情即可看到该白名单 bot 的最新治理信号（命中维度、节省率、dt_version 等），据此判断是否继续留白。OBSERVED 态不发通知、不占治理人力；删白后该单转为 CLOSED 终态，后续 offline-batch 按正常链路重建新 OPEN 单。

## Motivation

当前白名单是"硬吞过滤器"：`record_process_service` Step 2 命中白名单即提前 return，新 record 携带的治理信号（`governance_decision`/`hit_dimensions`/`saving_ratio` 等）全部丢弃，仅留一条不带业务字段的光秃 `scan_whitelisted` 审计。后果：

- 被加白 bot 从加白那一刻起，治理画像冻结在加白时那条已关闭工单的快照上，一直不变。
- 评审无法回答"这个白名单 bot 最新的治理状态是什么、是否还有继续冒头、是否该继续留白"——因为根本没有最新数据。

治理画像的唯一结构化载体是 ticket record 表（快照字段平铺在 `ac_governance_task_record`）。因此"白名单 bot 可见最新画像" ⟺ "白名单 bot 在 ticket record 表上有一行被持续刷新、但不发通知的工单"。CLOSED 语义是"终态、不观察、不刷新"，承载不了"持续刷新"，故需一个新态 `OBSERVED`。

## User Stories

- 作为治理评审，我想在工单列表/详情里看到白名单 bot 的**最新**治理画像（命中维度/节省率/dt_version），以便评估它是否还能继续待在白名单上。
- 作为治理评审，我不想被白名单 bot 的任何通知打扰——它在白名单期间不发 first_send、不发 reminder。
- 作为治理 ops，我想在删白之后系统自动恢复正常治理：老观察单收尾，下次 offline-batch 数据来了正常重建 OPEN 单通知 owner，不需要我手动干预，也不需要 cooldown。
- 作为系统维护者，我希望加一个新状态时不必逐一审改散落在 25 处的状态谓词——状态谓词应当有公共来源。

## Acceptance Criteria

### 变更纪律(前置)

- [ ] **Step 1 为纯重构先行落地**：`governance_status` 的 SQL 谓词统一收口到命名常量组（如 `ACTIVE_STATUSES`/`TERMINAL_STATUSES`），所有 repo 层 `in_(...)`/`==` 引用之。重构前后查询结果集逐字符一致（回归测试钉死）。
- [ ] Step 1 不引入 `OBSERVED`，不改动任何状态机行为，单独 commit 并全量测试绿。
- [ ] Step 2（加态）在 Step 1 收口后的常量基础上进行，不在裸字符串上扩展。

### 状态机语义

- [ ] `GovernanceStatus` 枚举新增 `OBSERVED = "observed"`。
- [ ] `TICKET_TRANSITIONS` 在收口常量基础上定义 `OBSERVED` 的进出：可由 `OPEN/SCHEDULED/WAITING_REVIEW → OBSERVED`（加白关单），`OBSERVED → CLOSED`（删白收尾）。`OBSERVED` 不向其他活跃态转换。
- [ ] `OBSERVED` 归属 **closed 族**（terminal），不进 `ACTIVE_STATUSES`。

### OBSERVED 单的产生（三路）

- [ ] **审批加白**：`review_ticket(..., action="approve_whitelist")` 不再转 CLOSED，而是 `WAITING_REVIEW → OBSERVED`，`close_reason="whitelisted"`，清 `remind_at`、释放 `active_worker`、加白名单条目、取消 pending 通知、写审计。
- [ ] **scan 兜底关残留**：`close_for_whitelist_hit` 由 `活跃 → CLOSED(scan_whitelisted)` 改为 `活跃 → OBSERVED(scan_whitelisted)`，副作用（取消 pending）不变。
- [ ] **offline-batch 命中无活跃单**：`record_process_service` 白名单命中且无活跃单时，**新建一条 OBSERVED 工单**（用当前 record 快照建单，状态直接落 OBSERVED，**不创建任何通知**），写审计。即"加白动作本身不建单，offline-batch 来数据时才建观察单"。

### 刷新语义

- [ ] offline-batch 对白名单 bot 且已有 OBSERVED 单时，复用既有 `refresh_snapshot` 链路刷新其快照，`dt_version` 严格更新 guard（既有逻辑，复用不新写）。
- [ ] OBSERVED 状态在刷新过程中不变（仍是 OBSERVED），不发通知、不建通知行。
- [ ] stale `dt_version`（≤ 现有）跳过刷新，仅写审计（复用 `still_actionable` 的 skip 语义或新增观察专用审计动作）。

### 不发通知（不变式）

- [ ] `find_active_ticket`（用 `ACTIVE_STATUSES`）天然不返回 OBSERVED 单 → delivery/admin 等活跃单消费方不会投递或操作观察单。
- [ ] OBSERVED 单不创建 `ac_governance_notify_log` 行（既有 first_send 创建链路只对活跃新建单触发）。
- [ ] 新增测试守卫:给定一条 OBSERVED 单，对其走 offline-batch 刷新后，notify_log 不新增任何行。

### cooldown 不受影响

- [ ] `find_latest_closed_by_worker` 仍只查 `closed`（不含 `observed`）→ OBSERVED 单不参与 cooldown 语义。
- [ ] 删白（OBSERVED → CLOSED）后，该单变为 closed 终态；下次 offline-batch record `is_whitelisted=False`、无活跃单 → Step 5 cooldown 检查看不到删白刚转的 closed 单带 cooldown（`approve_whitelist`/观察态关单不带 cooldown）→ 直接 Step 6 建新 OPEN 单。

### 删白路径

- [ ] 删白名单条目动作触发对应 OBSERVED 单 `OBSERVED → CLOSED`（终态收尾，不再被刷新）。
- [ ] 删白不主动建新单、不设 cooldown，等下次 offline-batch 正常触发 Step 6。

### 画像可见性

- [ ] 评审通过现有 workflow 工单列表/详情端点即可看到 OBSERVED 单及其最新快照（按状态过滤即可，无需新端点）。
- [ ] 前端是否加"观察态"筛选 tab 由前端仓自行决定，本 spec 不含前端改动。

## In Scope

- repo 层 `governance_status` SQL 谓词收口到命名常量组（纯重构）。
- 新增 `GovernanceStatus.OBSERVED` 及其 transitions。
- `approve_whitelist` / `close_for_whitelist_hit` / offline-batch 白名单命中三路产生/转入 OBSERVED。
- OBSERVED 单的刷新（复用 `refresh_snapshot` + dt_version guard）。
- 删白 → OBSERVED 转 CLOSED。
- 契约/回归测试钉死上述行为与"不发通知""不参与 cooldown""谓词收口无行为变化"。

## Out of Scope

- 前端观察态筛选视图（前端仓自行决定）。
- 白名单观察的频控/上限（依赖既有 dt_version 严格更新 guard 节流，本 spec 不加额外频控）。
- 给加白关单加 cooldown（明确不加，删白后立即重建）。
- 通知侧状态机（pending→sending→sent/failed）改动。
- 历史已 CLOSED 的加白工单回填为 OBSERVED（仅新增/新流转走 OBSERVED，存量不动）。

## Open Questions

- **审计动作命名**：观察刷新是否新增 `AuditAction.WHITELIST_OBSERVED`（刷新观察单）与既有 `SCAN_WHITELISTED`（无活跃单命中）区分，还是复用现有 action 靠 source/reason 区分？倾向新增以利评审审计流阅读，留待 plan 定。
- **offline-batch 新建 OBSERVED 单的 ticket_id/快照构建**：是否复用既有 `_create_new_ticket` 的建单路径再覆写状态为 OBSERVED、跳过 first_send，还是新增一条更瘦的建单路径？倾向复用并条件跳过通知创建，留待 plan 定。
- **scan 兜底关残留改 OBSERVED 后的审计**：`close_for_whitelist_hit` 现 audit `scan_whitelisted`，转 OBSERVED 后审计 action 是否需调整语义描述？留待 plan 定。