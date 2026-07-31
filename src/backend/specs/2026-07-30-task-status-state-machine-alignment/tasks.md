# 任务状态机对齐 — 任务清单(tasks.md)

> 负责人:栖真。spec:`spec.md`;plan:`plan.md`(同目录)。
> 代码落点:Avernet `src/backend/`(community 内核)+ `ocb/skills/task-recognition/` + `ocb/src/backend/tests/corp/`。
> 编辑纪律:只改状态名相关行,不顺手格式化;Avernet 风格(`from __future__ import annotations`、`Optional[T]`、`StrEnum`、dataclass)。
> 标记约定:`[ ]` 待办 / `[x]` 完成 / `[!]` 阻塞。每条带**验证点**(`✅`)。

---

## Slice 1 — 枚举(`models.py`)

- [ ] 1.1 `TaskStatus`:8 态 → 7 态。`INTAKE/DISCUSSING→DRAFTING`、`PLANNED→DEFINED`、`VALIDATING→REVIEWING`、`DELIVERED→DONE`、删 `HUNG`、加 `FAILED`、`EXECUTING/CANCELLED` 同名保留。值串小写(`drafting/defined/executing/reviewing/done/cancelled/failed`)。
- [ ] 1.2 `NodeStatus`:删 `PARTIAL_FAILED`;`FAILED` 承载验收失败+执行失败。值串 `pending/running/done/failed/skipped/human_required`。
- [ ] 1.3 不动 `GraphStatus`(`ON_PLAZA/AWAITING_HUMAN_ACCEPT/VERIFIED` 不依赖 TaskStatus)。
  - ✅ 验证:`uv run python -c "from agentclaw.community.core.task.domain.models import TaskStatus, NodeStatus; assert TaskStatus.DRAFTING.value=='drafting'; assert not hasattr(NodeStatus,'PARTIAL_FAILED'); assert TaskStatus.FAILED.value=='failed'"`(cwd=Avernet `src/backend`)

## Slice 2 — 状态机迁移表(`state_machine.py`)

- [ ] 2.1 `TERMINAL_TASK_STATUSES` = `{DONE, CANCELLED, FAILED}`(删 `DELIVERED/HUNG`,加 `DONE/FAILED`)。
- [ ] 2.2 `_BASE_TASK_TRANSITIONS` 整表替换为 plan §2.1:`DRAFTING→{DEFINED}`、`DEFINED→{EXECUTING}`、`EXECUTING→{REVIEWING,EXECUTING,FAILED}`、`REVIEWING→{DONE,EXECUTING}`、三终态空集。
- [ ] 2.3 `TASK_TRANSITIONS` 推导:任意非终态 `| {CANCELLED}`(删 `HUNG`)。
- [ ] 2.4 `NODE_TRANSITIONS` 删 `PARTIAL_FAILED` 行;`RUNNING→{DONE,FAILED,HUMAN_REQUIRED}`;`FAILED→{RUNNING,DONE}`(plan §2.2)。
- [ ] 2.5 `TERMINAL_NODE_STATUSES`={SKIPPED} 不变。守卫 helper 签名不变。
  - ✅ 验证:`can_task_transition(EXECUTING,FAILED)==True`;`can_task_transition(DRAFTING,EXECUTING)==False`(必须经 DEFINED);`can_node_transition(RUNNING,PARTIAL_FAILED)`→ `NameError`/`False`(枚举已删);`can_task_transition(REVIEWING,EXECUTING)==True`(返工)。

## Slice 3 — TaskService(`task_service.py`)

- [ ] 3.1 `create` + `_apply_event TASK_CREATED`:`root_phase=TaskStatus.DRAFTING`(两处,was INTAKE)。
- [ ] 3.2 `amend`:删 `if task.status==INTAKE: _advance_phase(DISCUSSING)` 分支(amend 不切态,留 DRAFTING)。
- [ ] 3.3 `_apply_event SPEC_AMENDED`:删 INTAKE→DISCUSSING 推进;只 `_apply_spec_patch` 后 return。
- [ ] 3.4 `finalize_plan`:合法源态 `{DISCUSSING,PLANNED}` → `{DRAFTING}`;目标态 `PLANNED` → `DEFINED`。
- [ ] 3.5 `_apply_event` 节点事件:`NODE_REJECTED` → `FAILED`(was `PARTIAL_FAILED`),`acceptance_result="fail"` 保留;`NODE_ACCEPTED/NODE_FAILED` 目标态不变(FAILED 本就连 RUNNING)。
- [ ] 3.6 `_apply_event GOAL_VERIFIED` → `_advance_phase(DONE)`(was DELIVERED);`GOAL_REJECTED` BBS 分支删 `HUNG`,统一走 `_apply_goal_verdict(fail)`;删 `HUNG` event 分支。
- [ ] 3.7 `_apply_goal_verdict`:pass → `VERIFIED` + `DONE`;fail → `AWAITING_HUMAN_ACCEPT` + 返工(确认返工目标态 `EXECUTING`,守卫支持 `REVIEWING→EXECUTING`)。
- [ ] 3.8 `cancel` → `CANCELLED`(同名,无改)。
  - ✅ 验证:`pytest tests/community/api/task/test_task_service_api_conformance.py -v`(部分用例需先按 Slice 7 更新,可暂跳待 Slice 7 一并跑)。

## Slice 4 — TaskScheduler(`task_scheduler.py`)

- [ ] 4.1 `start`:`PLANNED` → `DEFINED` 守卫 + 日志串。
- [ ] 4.2 `tick` `_all_settled` 真 → `_advance(REVIEWING)`(was VALIDATING);return `"advance_validating"` → `"advance_reviewing"`。
- [ ] 4.3 `tick` 新增 FAILED 分支(termination guard 之前):`gap=compute_gap(task); if gap["unrecoverable_failed"]: _advance(FAILED); save; return {"action":"task_failed"}`。
- [ ] 4.4 `tick` termination guard:`_advance(VALIDATING)` → `_advance(REVIEWING)`(FAILED 已被 4.3 拦截);日志串 VALIDATING→REVIEWING。
- [ ] 4.5 `compute_gap` refactor:删 `PARTIAL_FAILED` 分流;按 `acceptance_result`/`attempted_executors`/`atomic` 分 reroute/split/unrecoverable;新增 `unrecoverable_failed` 字段(plan §4.3)。
- [ ] 4.6 `_watchdog` 注释 "force VALIDATING" → "force REVIEWING";逻辑不变。
- [ ] 4.7 顶部模块 docstring `EXECUTING → VALIDATING` → `EXECUTING → REVIEWING`。
  - ✅ 验证:`pytest tests/community/api/task/test_scheduler.py -v`(需先按 Slice 7 更新断言)。

## Slice 5 — Noop / schemas / router / events

- [ ] 5.1 `plugins/community/task/__init__.py` `NoopTaskService.get_task_graph`:`root_phase:"intake"`→`"drafting"`;docstring "at INTAKE"→"at DRAFTING"。
- [ ] 5.2 `adapters/http/task/schemas.py`:grep `Literal` 状态约束(预期无);若有则替换;否则不动。
- [ ] 5.3 `adapters/http/task/router.py`:注释/文档串旧状态名 → 新;逻辑不动(status 走 `.value`)。
- [ ] 5.4 `domain/events.py`:`EventKind.HUNG` 保留枚举(向前兼容日志)但标注 deprecated;不新增 event kind(FAILED 走 scheduler 内部 `_advance`)。
  - ✅ 验证:`uv run python -c "from agentclaw.community.plugins.community.task import NoopTaskService; g=NoopTaskService().get_task_graph('x'); assert g['root_phase']=='drafting'"`

## Slice 6 — Avernet community 测试全量更新

- [ ] 6.1 `tests/community/api/task/test_scheduler.py`:状态名全局替换;`compute_gap` 用例删 `PARTIAL_FAILED`,加 `unrecoverable_failed` 断言;`PLANNED→EXECUTING`→`DEFINED→EXECUTING`;终态 `VALIDATING`→`REVIEWING`。
- [ ] 6.2 新增 FAILED 用例(plan §8.2):(a) 原子终止 → tick 后 `FAILED`;(b) 节点 attempts 耗尽 → `FAILED`;恢复优先 → reroute 不升 FAILED。
- [ ] 6.3 `test_task_service_api_conformance.py`:amend 后断言 `drafting`(不变态);finalize_plan 源态 `drafting`;goal.verified→`done`;NODE_REJECTED→`FAILED`+`acceptance_result=="fail"`。
- [ ] 6.4 `test_e2e_case_a_d.py` 及其余 task 测试:状态名替换;`HUNG` 用例改 `failed`/`human_required`。
- [ ] 6.5 `test_local_executor.py` / `test_protocols.py`:事件 kind 不变;Noop 双不动(确认无 PARTIAL_FAILED 残留)。
- [ ] 6.6 全量:`cd src/backend && uv run pytest tests/community/api/task -v`。
  - ✅ 验证:全绿(此前 247 用例 + 新增 3 条 FAILED 用例)。
- [ ] 6.7 全量回归:`cd src/backend && uv run pytest tests/community -v`(防 amend/状态机改动波及其他套件)。

## Slice 7 — ocb-public 同步(用户手动)+ corp smoke

- [ ] 7.1 [阻塞-用户] 用户将 Avernet task 内核改动手动 sync 到 `ocb-public` submodule(14 个 task 文件)。
- [ ] 7.2 `ocb/src/backend/tests/corp/endpoints/test_task_loop_smoke.py`:`intake→drafting`、`planned→defined`、`("executing","validating")→("executing","reviewing")`、`validating→reviewing`、`delivered→done`、`root_phase "intake"→"drafting"`。
- [ ] 7.3 `cd ocb/src/backend && DEPLOY_PROFILE=corp_test uv run pytest tests/corp/endpoints/test_task_loop_smoke.py -v`。
  - ✅ 验证:3 用例全绿(baseline / catalog-gap / full-loop→done)。

## Slice 8 — task-recognition skill + card

- [ ] 8.1 `ocb/skills/task-recognition/SKILL.md`:按 plan §9.1 表替换状态引用(frontmatter description、Phase C1/C2/D3/D4/E 标题与正文、`## 状态流转对照` 全表、`## 异常处理` 删 amend 未迁移条、payload status 枚举串、各 Phase 输出示例、`### 各 Phase 产出` 表)。
- [ ] 8.2 `SKILL.md` `## 状态流转对照` 补一行:`REVIEWING/DONE/FAILED` 为内核+owner-bot 自驱推进,skill 不主动触发,card 需能渲染。
- [ ] 8.3 `ocb/skills/task-recognition/card.jsx`:grep `status` 判定分支,按 `intake→drafting/discussing→drafting/planned→defined/validating→reviewing/delivered→done/hung→failed` 替换。
  - ✅ 验证:人工 review SKILL.md 无旧状态名残留(`grep -nE "INTAKE|DISCUSSING|PLANNED|VALIDATING|DELIVERED|HUNG|intake|discussing|planned|validating|delivered|hung" SKILL.md` 仅命中 §9.3 说明行);card.jsx 同。

---

## 出场标准(全部 slice ✅)

- [ ] TaskStatus 7 态 / NodeStatus 6 态,无 PARTIAL_FAILED、无 HUNG(任务级)。
- [ ] 全回路态名断言绿:create→DRAFTING、amend→仍 DRAFTING、finalize_plan→DEFINED、approve→EXECUTING、全 settled→REVIEWING、goal.verified→DONE、否决→CANCELLED、失败→FAILED。
- [ ] R4 a/b 两条 FAILED 触发均有实现 + 用例;恢复优先语义用例绿。
- [ ] 守卫覆盖所有合法迁移,非法迁移(`EXECUTING→DONE` 直跳、终态再迁移)拒绝。
- [ ] Avernet community task 套件全绿 + 全量回归绿。
- [ ] ocb corp smoke 3 用例绿(用户 sync 后)。
- [ ] SKILL.md / card.jsx 旧状态名清零。

---

> 待用户 "proceed" 后进 implement 阶段,按 Slice 1→8 顺序执行,逐条勾选。阻塞项(7.1 用户 sync)依用户节奏。