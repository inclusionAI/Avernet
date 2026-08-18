# BBS 自主接力 skill 任务清单(tasks.md)

> 详细步骤(文件/接口/TDD 代码/提交)见同目录 `plan.md`。本文件为可勾选任务索引 + 依赖 + 验证点。
> 上游:`2026-08-09-task-goal-driven-execution-framework`(权威)。声明不破坏上游契约。分支 `feat/task-goal-driven-collab-dev`。

## 依赖图

```
T1(bbs_mode直出) ─┐
T2(BBS_MAX_DEPTH) ─┤
T3(claim_bbs_owner+facade) ──┬── T4(bbs/claim 路由)
                              ├── T5(attach_bbs_node) ─── T6(bbs/attach 路由)
                              ├── T7(on_bbs_report+facade) ── T8(bbs/result 路由)
                              └── T10(harness bbs 到期)
T9(drain 跳过 bbs 守卫) 独立
T1..T10 ── T11(bbs-relay-pickup skill) ── T12(E2E singlebox)
```

## 任务清单

| # | 任务 | 依赖 | 关键验证 | 状态 |
|---|---|---|---|---|
| T1 | `TaskSummary`/`TaskSummaryDTO` 暴露 `bbs_mode`;`list_task_summaries` 填充 | — | summary `bbs_mode` 为 True/False | ☐ |
| T2 | `_execution_config` 加 `BBS_MAX_DEPTH` 默认 3 | — | `cfg["BBS_MAX_DEPTH"]==3`,可被 `execution_config` 覆盖 | ☐ |
| T3 | `TaskGraphService.claim_bbs_owner`(任务根级 CAS,`bbs_owner`)+ `TaskService.claim_bbs_task` + Protocol | — | 两 bot claim 恰一赢输者 `TaskStateError`;同 bot 幂等;非 bbs 任务拒 | ☐ |
| T4 | `POST /api/task/bbs/claim` 路由 + `BbsClaimDTO` | T3 | 路由 200,第二 claim 409 | ☐ |
| T5 | `TaskGraphService.attach_bbs_node`(`add_task_nodes`+create+start+`bbs_relay_count`/深度闸)+ facade + Protocol | T2,T3 | attach 产 `run_mode=bbs` RUNNING 节点;非 owner 拒;深度达上限→图 HUNG+拒 | ☐ |
| T6 | `POST /api/task/bbs/attach` 路由 + `BbsAttachDTO` | T5 | 路由返回 `node_id`;非 owner/前提不满足 409 | ☐ |
| T7 | `ExecutionEngine.on_bbs_report`(collector-free 翻态+`root_verified` 收口+清 `bbs_owner`)+ `TaskService.report_bbs_result` + Protocol | T3 | PASS+root_verified→图 DONE+owner 清;FAIL+gaps→ FAILED+owner 清+checkpoint 保留;非 owner 拒 | ☐ |
| T8 | `POST /api/task/bbs/result` 路由 + `BbsResultDTO`/`AcceptanceResultDTO` | T7 | root_verified DONE;非 owner 409 | ☐ |
| T9 | `_prepare_into` 跳过 `run_mode=="bbs"`(drain 守卫,FR-EXT-06) | — | PENDING bbs 叶不被自动翻 RUNNING/改 assignee | ☐ |
| T10 | `_poll_once` RUNNING 扫:`run_mode=="bbs"` SLA 到期→清 `bbs_owner`+scoped 节点 `FAILED`(gaps=`bbs_lease_expired`),不重派 | T3 | 到期后 owner 清+节点 FAILED(非 PENDING 重派) | ☐ |
| T11 | 内容 skill `bbs-relay-pickup`(`SKILL.md`+`references/{task-api,judge-rubric,idempotency}`) | T1,T4,T6,T8 | skill 流程门:claim 成功才 attach;写回经 bbs/result | ☐ |
| T12 | E2E singlebox:claim race(C)/接力(B)/崩溃 lease(D)/图级 HUNG skip(G) | T1..T11 | 两 bot 恰一赢;崩溃后接力看到 DONE/checkpoint;图级 HUNG 被 skip | ☐ |

## 计划级 refinement(实现完回填 spec)

1. `bbs/result` 走新增 `on_bbs_report`(collector-free),非直调 `on_report`(spec FR-EXT-03 措辞需同步: collector-free 因 `on_report` 的 `_on_pass_collect` 会经 owner-bot 重规划,与 §10.4 接力冲突)。
2. 根目标收口由 `bbs/result` 带 `root_verified: bool` 触发(spec FR-PICK-05"根 acceptance→图 DONE"的 HOW 落点)。
3. BBS 深度闸 = per-task `bbs_relay_count`(图 `extend_props`),非 `loop_round`(spec §10.4/FR-IDEM-04 措辞需补 `bbs_relay_count`)。
4. claim 仅校验 `bbs_mode=True`;"图空闲+根 PLANNING"由 attach 经 `add_task_nodes` a/b/c/d 裁(spec §10.5/FR-PICK-04 前提措辞一致)。

## 非 TDD / 容易漏

- `api/task_service.py` `TaskServiceProtocol` runtime_checkable Protocol 每加一方法,T3/T5/T7 同步加签名(否则 `Injected(TaskServiceProtocol)` 路由侧类型/补全受影响,但 runtime 仅查方法名存在)。
- `TaskError` 不在 `_DOMAIN_ERROR_STATUS_MAP`:沿用 router `try/except→HTTPException`(T4/T6/T8),勿擅自把 bbs 错误做成 `DomainError` 子类(否则触发 `tests/architecture/test_domain_error_status_map_complete.py`)。
- harness `_EXEC_MODES` 是 `_poll_once` 内 local tuple(`"bbs"` 已含);T10 在该分支内判 `mode=="bbs"` 分流,勿改 tuple。
- T9 守卫插在 `_prepare_into` 候选筛选循环,与现有 `dispatching`/`dispatch_error` 跳过并列。
- T11 skill 落点路径 plan 评审(候选 `src/backend/skills/bbs-relay-pickup/`);范本 `tests/community/core/task/singlebox_e2e/skills/{planning,search,acceptance}/SKILL.md`。