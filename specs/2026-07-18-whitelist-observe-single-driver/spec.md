# 加白转态单一驱动收口

## Summary

把"加白 → 工单转 OBSERVED"这个业务语义**收口到 lifecycle driver 的单一语义方法**,让所有加白入口(审批/批量加白/scan 兜底/off-batch 建观察单)都经它,而不是各自独立调不同的状态转换方法。直接动因:批量加白(`/admin/whitelist:bulk-add`)漏转 OBSERVED,把活跃单转成了 CLOSED —— 这是"加白转态无统一收口"的结构缺陷症状,不是孤立笔误。

## Motivation

OBSERVED 观察态落地后,有四条"加白语义"入口都该把工单转 OBSERVED(持续观察画像,不发通知):

| 入口 | 触发 | 当前目标态 | 是否对 |
|---|---|---|---|
| `review approve_whitelist` | 审批加白 | OBSERVED | ✅ |
| `close_for_whitelist_hit` | scan 兜底关白单 bot 残留活跃单 | OBSERVED | ✅ |
| `open_observed_ticket` | off-batch 命中白名单建观察单 | OBSERVED | ✅ |
| **`bulk_whitelist` 第4步** | **批量加白关活跃单** | **CLOSED/admin_closed** | **❌** |

`bulk_whitelist`(/admin/whitelist:bulk-add 端点)把活跃单走 `bulk_close_by_ticket_ids → admin_close` 转成 CLOSED/ADMIN_CLOSED,与加白语义矛盾 —— 加白 bot 的工单被关成终态 CLOSED,丢失"持续观察画像"的语义,且与另三条加白入口行为不一致。

**根因不是 bulk_whitelist 这一处写错,而是结构缺陷**:四条加白入口各自独立调不同状态转换方法(enter_observed / close_for_whitelist_hit / open_observed_ticket / bulk_close_by_ticket_ids),没有统一的"加白→转 OBSERVED"收口。本期 OBSERVED 落地时改了三条、漏了第四条,编译器/测试都没拦住。未来加第五条加白入口,同样的漏转还会发生。

运维关单(admin_close/cancel_pending/close_all_open/stale_replace/auto_silence_close)转 CLOSED 是正确的(运维语义非加白语义),不在本 spec 范围。

## User Stories

- 作为治理 ops,我批量加白一组 bot 后,这些 bot 的活跃工单应转 OBSERVED(持续观察),而非 CLOSED(丢画像),与单条审批加白行为一致。
- 作为状态机维护者,我希望"加白→转 OBSERVED"只有一个驱动入口,加新加白入口时强制走它,结构性防漏转。
- 作为评审,我希望 bulk_whitelist 与 review approve_whitelist / close_for_whitelist_hit 行为一致(都 OBSERVED),不再因入口不同而状态分叉。

## Acceptance Criteria

### bulk_whitelist 转态修复

- [ ] `bulk_whitelist`(/admin/whitelist:bulk-add)批量加白后,被关的活跃单转 **OBSERVED**(非 CLOSED),close_reason 表加白来源(如 SCAN_WHITELISTED 或新增的批量加白枚举)。
- [ ] 通知侧取消 pending 不变(OBSERVED 不发通知,取消活跃单的 pending 通知仍正确)。
- [ ] 返回值 `{"whitelisted": N, "cancelled": N}` 契约不变。

### 单一驱动收口

- [ ] lifecycle driver 提供一个"加白→转 OBSERVED"语义方法(暂名 `observe_for_whitelist` 或复用/扩展 `close_for_whitelist_hit`),封装:转 OBSERVED + 释放 active_worker + 不设 closed_at + cancel pending + best-effort 审计归属。
- [ ] 四条加白入口统一经此方法(或其封装),不再各自直接调 close/admin_close/bulk_close_by_ticket_ids 转态。
- [ ] 单条 vs 批量:语义方法提供单条版,批量版复用单条(逐条守卫激活),或语义方法本身支持批量 —— plan 阶段定具体形态,但必须单一收口。

### 状态机红线

- [ ] **不动** `TICKET_TRANSITIONS` / `GovernanceStatus` 枚举 / `ACTIVE_STATUSES` / `TERMINAL_STATUSES`(守"加固不碰状态机"红线;本次是加白转态的驱动收口,不动转换矩阵/状态划分)。
- [ ] 不动运维关单路径(admin_close/cancel_pending/close_all_open/stale_replace/auto_silence)的 CLOSED 语义。

### 测试

- [ ] bulk_whitelist 转态:批量加白后活跃单 status=OBSERVED + close_reason=加白来源 + pending 通知取消 + 不发通知(notify 无新增)。
- [ ] 单一驱动:四条加白入口转态后字段一致(status/assignee/closed_at/close_reason 语义对齐)。
- [ ] DSL e2e:批量加白端点用一个 TC 覆盖(若 DSL 有 bulk_whitelist step;否则补端点测试)。
- [ ] 既有 OBSERVED 路径测试不退步。

## In Scope

- bulk_whitelist 转态修复(CLOSED→OBSERVED)。
- 加白→转 OBSERVED 的 lifecycle driver 单一语义方法收口,四条加白入口统一。
- 对应单测 + 端点/DSL 测试。

## Out of Scope

- `TICKET_TRANSITIONS`/状态枚举/族划分任何改动(守红线)。
- 运维关单路径(admin_close/cancel_pending/close_all_open/stale_replace/auto_silence)的语义。
- OBSERVED 族别重评(TERMINAL vs ACTIVE,评估 Open Q,另议)。
- 通知投递状态机。
- `STATE_INVARIANTS` 集中表(评估标 medium 可接受不立刻改)。

## Open Questions

- **批量加白的 close_reason**:bulk_whitelist 转态后 close_reason 用哪个?复用 `SCAN_WHITELISTED`(scan 兜底也是这种"批量加白漏关"语义)还是新增 `AuditAction`/`CloseReason` 枚举(如 `BULK_WHITELIST_APPROVED`)?倾向复用 `WHITELIST_APPROVED`(审批加白同源,语义=加白),但 bulk 是 admin 主动操作非审批,plan 阶段定。
- **语义方法形态**:单条 `observe_for_whitelist(ticket_id, close_reason)` + 批量 `bulk_observe_by_ticket_ids` 复用单条(对齐既有 `bulk_close_by_ticket_ids` 范式),还是单一方法支持两种?plan 阶段定,倾向对齐既有 bulk_close_by_ticket_ids 范式。
- **close_for_whitelist_hit 是否并入**:它已是"加白→转 OBSERVED"的单条语义方法,只是命名偏"scan 兜底"。是否重命名为 `observe_for_whitelist` 统一?倾向重命名收口,但要注意它现有调用方(record_process 路1)签名稳定。plan 阶段定。