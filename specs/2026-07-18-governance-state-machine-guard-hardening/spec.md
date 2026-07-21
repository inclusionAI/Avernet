# Governance 状态机守卫加固

## Summary

修复状态机评估(sound audit-report.md 维度③)发现的 1 个 medium 真缺陷 + 2 个 low 测试缺口,三者主题一致(状态机守卫加固)。核心:`enter_observed` 职责越界,逼建单路径手写状态机动作,造成"转 OBSERVED"散两处 → 让 `enter_observed` 只管状态机动作,`close_reason` 改可选,建单路径复用它。**不动 `TICKET_TRANSITIONS`/状态枚举/族划分**(守红线)。

## Motivation

评估报告 `audit-report.md` ③节判定:
- **medium 真缺陷**:`enter_observed(close_reason)` 把"转 OBSERVED 状态机动作"和"关单语义(带 close_reason)"绑死。建观察单路径 `open_observed_ticket`(非关单,不需 close_reason)被逼绕开它,手写 `transition_to(OBSERVED)+assignee=None`,注释自承"对齐 enter_observed"。状态机动作散两处,未来 `enter_observed` 加副作用时建单路径不会跟,行为分叉。
- **low 缺口①**:`bulk_close_open` 旁路守卫有 SQL 等价 WHERE 谓词,但无断言钉死"WHERE 不含 waiting_review/observed",谓词若被改宽测不出。
- **low 缺口②**:不变式 I2(关闭态 closed_at 必非空)、I4(ACTIVE 态 active_worker 必非空)无测试钉死,靠方法注释默契。

三者都是"状态机守卫"层面的加固,改动小、收益清晰,一个 spec 收口。

## User Stories

- 作为状态机维护者,我希望"转 OBSERVED"只有 `enter_observed` 一个入口,这样给它加副作用(如清 mute_until)时,建单和关单转态两条路径自动一致,不漏不改。
- 作为评审,我希望 `bulk_close_open` 的 WHERE 谓词有断言钉死,batch 关单不会误关 waiting_review/observed 单。
- 作为评审,我希望关闭态 closed_at 非空、ACTIVE 态 active_worker 非空这两条不变式有测试守,谁改坏 close() 漏设 closed_at 能被抓到。

## Acceptance Criteria

### ① open_observed_ticket 收口(核心)

- [ ] `enter_observed` 签名改为 `close_reason: str | None = None`(可选)。
- [ ] `enter_observed` 行为不变:仍转 OBSERVED + 释放 assignee + 清 remind_at + 不设 closed_at + 不碰 cooldown。`close_reason=None` 时不设 close_reason(建单场景);传值时设(关单转态场景,行为同今)。
- [ ] `open_observed_ticket`(lifecycle_service)改调 `ticket.enter_observed()`(不传 close_reason),删除手写 `transition_to(OBSERVED)+assignee=None`。
- [ ] 既有两路调用方(`review approve_whitelist`、`close_for_whitelist_hit`)仍传 close_reason,行为零变化。
- [ ] **不动** `TICKET_TRANSITIONS`、`GovernanceStatus` 枚举、`ACTIVE_STATUSES`/`TERMINAL_STATUSES`(守"加固 feature 不碰状态机"红线 — 本次只动领域方法职责,不动转换矩阵/状态划分)。

### ② bulk_close_open WHERE 谓词断言

- [ ] 新增测试:批量 close 后,无 waiting_review / observed 单被转 closed(断言这两态的 close_reason 不含 admin_closed/batch 原因,或这两态单 governance_status 仍原态)。
- [ ] 测试钉死"WHERE 谓词不含 waiting_review/observed",若谓词被改宽(误含)能测出。

### ③ I2/I4 不变式反向断言

- [ ] I2:新增测试 `close()` 后 `closed_at is not None`(钉死关闭态 closed_at 必非空)。
- [ ] I4:新增测试 ACTIVE 态(OPEN/SCHEDULED/WAITING_REVIEW)`active_worker is not None`(钉死活跃态 active_worker 必非空)。
- [ ] 断言放领域模型测试或 driver 测试,就近 `test_domain_model.py`/`test_lifecycle_service.py`。

### 全局

- [ ] 全量 governance + lifecycle 契约 + endpoint 套件绿(基线 754+ 应不退步)。
- [ ] 不引入新依赖、不动 DDL、不动对外 HTTP 契约。

## In Scope

- `enter_observed` 签名改可选 + `open_observed_ticket` 复用它(①核心)。
- bulk_close_open WHERE 谓词断言(②)。
- I2/I4 不变式反向断言(③)。

## Out of Scope

- `TICKET_TRANSITIONS`/`GovernanceStatus`/状态族划分任何改动(守红线)。
- `bulk_close_open` 旁路守卫本身不改(评估判定 low/可接受取舍,只补断言)。
- `STATE_INVARIANTS` 集中表(评估标 medium 但可接受不立刻改,另议)。
- OBSERVED 族别重评(从 TERMINAL 挪回 ACTIVE,评估 Open Q,不在本期)。
- 通知投递状态机。

## Open Questions

- `enter_observed` 改可选后,关单转态两路会不会有人显式传 `close_reason=None` 致漏设?——倾向靠调用方自觉(review/scan 兜底都传枚举),不加强制校验(避免过度防御)。plan 阶段确认调用方都传值即可。