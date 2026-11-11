# SDD ledger — plan: apps/backend/specs/2026-11-09-bbs-context-collaboration-protocol/plan.md

- worktree: context-collab-p1 (branch worktree-context-collab-p1, base origin/dev 833a7db84)
- spec committed as docs 59fd168ea (spec.md + plan.md carried from dev_bbs_help_1019_2 checkout)
- baseline: uv sync --frozen ok; smoke tests 4 passed (task models schema + task intake)

## Pre-flight scan(2026-11-11)

| 任务对/任务 | 检查点 | 结果 |
|---|---|---|
| T1↔T8 | 模型列名/表名与仓储 SQL 引用一致 | 一致 |
| T2↔T3 | ContextSpec 消费 refs/枚举,签名一致 | 一致 |
| T3↔T5 | TaskStatus 词汇 与 状态机归一(ready→created, active→in_progress) | 一致(用户裁决) |
| T4↔T10 | validate_event_body 服务层调用 | 一致 |
| T5↔T10 | GUARD_REGISTRY/GuardEvaluationInput/resolve_transition | 一致 |
| T6↔T8/9/11 | compute_is_trunk/ensure_expected_version/project_events/build_progress 签名 | 发现冲突→Ruling R1/R2 |
| T7↔T8/10/11 | 仓储协议/服务协议 dataclass 命名(InitialEventDraft 等) | 一致(grep 复核) |
| T11↔T13(service↔adapter DTO)| 服务 Projection / adapter §6.5 wire 名分工 | 一致 |
| T12→T13/14 | DI 绑定先于 Injected 解析 | 顺序正确 |
| T13↔T14 共用 schemas_context_collab.py | T13 创建、T14 复用 | 顺序正确 |
| T13/T14 修改文件集合 | app.py+responses.py 与 openapi_v1/__init__+规则表 | 不相交 |
| T4 注册表含 task.completed/assigned 但 T5 无规则 | 有意设计(接纳不迁移) | 一致(T12 A04 已钉) |
| T5 spec §4.1 blocked 无入边 vs §4.4 #2 全状态可达 | 发现冲突→Ruling R3 |
| T2/T3 内部 | from_dict round-trip 与拒绝断言成对 | 一致 |
| T15 ↔ §12.2 #1-6 | 每条验收一断言 | 完整 |

- **Ruling R1:** build_progress 的 current_event_id 沿用调用方传入的 collet.current_event_id(不取 events[-1])— spec §6.4 示例权威(evt_004 current、evt_005 pending);若错,GET 响应的 current 标识与示例不符,前端取错当前节点。
- **Ruling R2:** completed/active/blocked 计数仅统计 primitive_type != Message 的事件 — spec §6.4 示例 completed_count=2(Message evt_002 不计)由此自洽;若错,进度统计多算互动消息。
- **Ruling R3:** validate_definition 可达性校验豁免 governance_kind ∈ {waiting, blocked} 的状态 — spec §4.3 规定 blocked 只能由失败事件进入,§4.1 自身 blocked 即无入边;若错,内置状态机被自己的校验拒绝。
- 计划文件已按 R1-R3 修订并 commit。

Task 1: minor (deferred): report claims collet ORM column order == DDL order — inaccurate, harmless (report-only)
Task 1: minor (deferred): test GREEN output has 17 pre-existing pydantic/utcnow warnings (not introduced by task)
Task 1: minor (deferred): models.py `_base_axes()[:3]` slice reads awkward vs a dedicated append-table axes helper
Task 1: complete (commits bd2d78d..bb04ff2, review clean)

Task 2: minor (deferred): _required_str helper duplicated across l2/l3 modules — extract on 3rd occurrence
Task 2: minor (deferred→Task 7 dispatch): principal_type/agent_type closed sets documented but NOT enforced in refs — Task 7 service/DTO layer must not assume screening happened here
Task 2: complete (commits bb04ff2..9d6ef7a, review clean)

Task 3: minor (deferred): Participant docstring says principal_ref/agent_ref 二选一(from_dict only rejects both-None) — align docstring or enforce XOR in a later touching task
Task 3: minor (deferred): _required_str/_optional_str copied per-file in 6 new l4 modules — collect into package-local wire helpers if a 3rd copy appears
Task 3: minor (deferred): GREEN output 17 warnings unattributed — confirm pre-existing at whole-branch review
Task 3: complete (commits 9d6ef7a..397e795, review clean)

Task 4: minor (deferred): "requires primitive_type Message" rejection message lacks offending-value interpolation unlike siblings
Task 4: minor (deferred): non-dict payload reuses content_ref error message (misleading); payload: dict pinned by signature
Task 4: minor (deferred): test_registered_task_kinds_pass covers only Activity for task.* kinds
Task 4: complete (commits 397e795..50d6e79, review clean)

Task 5: Ruling R4: brief-mandated `actor == executor` guard comparison was structurally dead (PrincipalRef vs AgentRef dataclass equality) — fixed to value-based comparison (actor.principal_type=="bot" and actor.principal_id==executor.agent_id); cost if wrong: bot executor still cannot start tasks / or over-permissive if comparison too loose (tests pin both sides)
Task 5: fix round 1/5 (2 addressed: R4 guard comparison + match= hardening & fail-closed pinning; 0 open; commits 63c167c..3b1fed8)

Task 5: fix round 2/5 (1 addressed: terminal-edge match= pinning; 0 open; commits 3b1fed8..dc46fee)
Task 5: minor (deferred): {status.value for status in TaskStatus} set comprehension rebuilt per iteration in _validate_states (state_machine.py ~:167) + known_methods same pattern
Task 5: minor (deferred): executor-bot guard test lacks a negative for ambiguous human-actor with same principal_id as executor agent_id
Task 5: complete (commits 50d6e79..dc46fee, 2 fix rounds, review clean)

Task 6: minor (deferred): compute_is_trunk branch "previous None→True" untested
Task 6: minor (deferred): build_progress docstring repeats ambiguous denominator wording; actual rule = counted statuses (completed+active+blocked, pending excluded) per pinned 75%
Task 6: handoff→Task 11: project_events preserves input order — Task 11 must pass sequence_no-ordered events; current_event_id absent from set → all-pending fallback
Task 6: handoff→Task 11: STATUS_BLOCKED count structurally unreachable today (projection never emits "blocked") — forward-compat only
Task 6: complete (commits dc46fee..d78a57a, review clean)

Task 7: minor (deferred→Task 10 dispatch): replayed create backfills progress.current_event_id with the CREATION event id even if context has advanced — on replay use record.collet.current_event_id instead
Task 7: minor (deferred): _created_snapshot_projection relies on CurrentSnapshot.to_dict() returning fresh nested dicts — copy progress in place or pin freshness
Task 7: handoff→Task 8: InitialEventDraft.actor_ref = authenticated caller (ActorContext.actor); confirm forward string annotations in persistence/repository.py resolve once Task 8/9 types land
Task 7: complete (commits d78a57a..a9bccd9, review clean)

Task 8: minor (deferred): _replay_record initial-event lookup keyed (collab_context_id, request_id) — add sequence_no==1 filter if Task 9 ever risks key collision (uk_context_collab_event_request should prevent)
Task 8: minor (deferred): _replay_record raises plain RuntimeError on corruption — consider domain error class later
Task 8: minor (deferred): _transaction falls back to orm_session when transactional absent — atomicity then depends on commit-on-exit of every DatabasePlugin impl
Task 8: handoff→Task 11: creation-event visibility pinned "public" (least-privileged NOT NULL default) — ratify at projection layer
Task 8: Ruling: occurred_at read-path UTC normalization adopted (write path was UTC, reads naive on SQLite+MySQL DATETIME)
Task 8: fix round 1/5 (1 addressed: occurred_at tz normalization; 0 open; commits 7775c3b..f59e28f)
Task 8: complete (commits a9bccd9..f59e28f, 1 fix round, review clean)

Task 9: Ruling R5: replay check precedes expected_version check — plan bullet order listed version-check first, but spec §9.4 #5 (幂等只能成功一次) + G2 Idempotency require duplicate request_id to return the original event, not 409; cost if wrong: legitimate retry of a timed-out append returns Conflict instead of idempotent 200

Task 9: minor (deferred): replay-after-later-appends collet asymmetry unpinned — add test pinning that r1 replay returns latest persisted collet.context_version
Task 9: complete (commits f59e28f..c49e075, review clean, opus-reviewed)
Task 9: handoff→Task 10: replay returns CURRENT collet row (not append-time state); creation-event visibility "public" convention established

Task 10: Ruling: create-replay backfill uses record.collet.current_event_id (计双读法冲突以 collet.current_event_id 为准,跳过回填会使快照报 None)
Task 10: minor (deferred): guard_results dict evaluates a shared guard once per referencing rule (deterministic, harmless)
Task 10: minor (deferred): non-principal-accept test uses human-typed actor with bot id — a bot-typed actor would be less contrived
Task 10: minor (deferred): isinstance-in-_make_service protocol checks run per test (cosmetic)
Task 10: complete (commits c49e075..4bdf39e, review clean)

Task 11: Ruling R6: service always fetches trunk_events=False and derives progress/current_event from the full visible list; trunk flag only filters task_progress.events[] — spec §12.2 #4 identity binding; the voided plan text("repo 层 trunk 过滤") bicycle is a plan defect caught by review
Task 11: minor (deferred): repo.get_current_status(trunk_events=True) path now server-side dead — protocol seam remains for future callers
Task 11: fix round 1/5 (1 addressed: derive-before-trunk-filter + 3 promoter tests + carried minor; 0 open; commits 4d4010e..5f5c28c)
Task 11: complete (commits 4bdf39e..5f5c28c, 1 fix round, review clean)
Task 11: handoff→Task 13: current_event dict carries occurred_at beyond the 7-field projection set — pin in adapter tests against serialization drift

Task 12: Ruling: third DI binding (ContextCollabAuthorizerProtocol→StructuralContextCollabAuthorizer) accepted — python-injector ignores defaults on annotated params; the service constructor default is dead under DI; P2 overrides via same protocol key
Task 12: minor (deferred): conftest SQLite harness duplicated verbatim between repository/task/context_collab and core/task/context_collab — promote to shared support module in cleanup
Task 12: minor (deferred): full-profile DI smoke mutates global _app_injector (established pattern, empirically isolated)
Task 12: handoff→Task 13: task_module.py now ~837 lines — approaching 1000-line CI cap; keep additions minimal
Task 12: complete (commits 5f5c28c..ea74e59, review clean)

Task 13: Ruling: app.py duplicate-include removal (lines 980-981) accepted — same router objects, primary block intact, de-dupes route table, keeps app.py ≤1000
Task 13: minor (deferred): parent_event/payload have DTO defaults though §6.3 marks 必填 (service G4 still rejects; lenient wire)
Task 13: minor (deferred): ColletCreateRequest extra="ignore" vs ContextSpec extra="forbid" inconsistent strictness
Task 13: minor (deferred): _principal_id returns None for malformed ref — consider raising
Task 13: tracking: app.py at exactly 1000 lines; next include forces the deferred split
Task 13: complete (commits ea74e59..7a82cba, review clean, opus-reviewed)

Task 14: Ruling: AdmissionMode.REFUSED (refuse app-only callers) for the three openapi ops accepted — operator is the L2 human accountability subject; OPEN would admit app-only callers that bind empty principal; revisit single place if machine-principal operators legitimized later
Task 14: Ruling: two out-of-brief touches accepted as hard CI gates, verified behavior-neutral (schema Field descriptions + RST scrub; test_explicit_user_id data registration 144→147)
Task 14: handoff→Task 15: gateway spanner route_security wiring for /openapi/v1/collaboration/task/** is OUT of this repo — verify at singlebox/gateway level, note in PR description
Task 14: minor (deferred): _boot_verifier/_principal JWT-minting machinery duplicated 3rd copy — extract on 4th consumer
Task 14: minor (deferred): bad-header 401 only asserted on create route (shared middleware; parametrize for uniformity)
Task 14: complete (commits 7a82cba..f06410f, review clean, opus-reviewed)

Task 15: Ruling: 6-file CI-gate fix commit accepted (api boundary decl, gateway namespaces incl. application.yaml, context_collab_status.py merge + app.py 999 lines, http-only registry) — each traced 1:1 to a deterministic gate failure and a sibling precedent
Task 15: minor (deferred): report overstated progress anchor as five-field — actually asserts four-of-five
Task 15: minor (deferred): missing-principal anchor only at PrincipalRef.from_dict; empty-valued direct PrincipalRef construction untested
Task 15: minor (deferred): app.py Caller* row compression creates 1 line headroom (999/1000) — next include forces split
Task 15: minor (deferred): generated dependents.md untracked — add to .gitignore in a later touching task
Task 15: fix round 1/5 (1 addressed: wire-level steps pin + report correction; 0 open; commits 3f2e672..2bcb122)
Task 15: complete (commits f06410f..2bcb122, 1 fix round, review clean, opus-reviewed; full CI gate green 30787 passed / 91.12% / changed-line 96.15%)

ALL 15 TASKS COMPLETE — proceeding to whole-branch final review

## Final whole-branch review (2026-11-11)
Verdict: With fixes — 2 Important (OpenAPI primitive_type description advertises rejected "Artifact" value; docs/arch/generated/ untracked despite ledger-promise), 0 Critical.
All R1-R6 rulings audited consistent with spec. Spec errata filed mentally: §6.3 example current_status "in_progress" after user_interaction.posted contradicts machine (code follows machine — flagged for PR description).
12 Minors triaged: 2 FIX-BEFORE-MERGE (the Importants), rest SHIP with named reasons.

Final fix wave: fix round 1/1 (2 addressed: Artifact doc claim + .gitignore entry; 0 open; commits 2bcb122..ad612f9, re-review clean)
BRANCH COMPLETE: 24 commits, origin/dev 833a7db84..ad612f97d. Final review clean. Workspace retired; git history is the record.

## 合并后变更(2026-11-11)

- **Ruling R7(spec 作者指示):** 创建接口去除幂等 —— `request_id` 不再是创建请求参数,`ac_context_collab_collet` 去掉 `create_request_id` 列与 `uk_context_collab_collet_request` 唯一键;重复调用即重复创建,防重由调用方保证。事件侧 `request_id` 幂等(`uk_context_collab_event_request`)不变。初始事件的 `request_id` 列取其自身 `event_id`(列 NOT NULL)。
- 同步范围:spec §6.2/§9.2/§9.4/§9.5,ORM 模型,DDL(分支内建表,未落库 dev,原地修订),仓储(删除三层重放链),service_api/服务/DTO(replayed 字段移除),9 个测试文件(重复创建改为"各建一个上下文"的正向覆盖)。
