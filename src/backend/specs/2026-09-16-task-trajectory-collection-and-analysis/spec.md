# Spec — 任务轨迹采集与关键原因分析 (Task Trajectory Collection & Root-Cause Analysis)

> **状态**: `draft / decisions-confirmed (2026-09-17,含 analysis 触发模型 do_analysis + 双执行者裁决)`
> 日期: 2026-09-16(主消费方/范围决策已确认,见文末"已确认决策")。
> `plan.md` / `tasks.md` 由 writing-plans 产出。

## 假设(已确认 2026-09-17)

- **主消费方**: 工程师排障根因定位 —— 某次任务失败/异常,需快速找到根因(派发选择 / 底层接口报错 / 执行超时 / 卡死)。**已确认**。
- **分析形式**: 确定性规则归因为主(从富化后的轨迹事件按 `ReasonCatalog` 分类出原因),分析执行者可扩展为 LLM / tc_bot(`analysis_type=llm|tc_bot|rule`,见 REQ-9);原"P2 独立 LLM 叙述层"已裁剪。**注**:此指分析的**形态/分类**(`TrajectoryAnalysis` 扁平原因字符串 + `ReasonCatalog`);首期 **live 执行者走 `tc_bot`**(`rule`/`llm` 备用,见"已确认决策"第 10/11 条),由 `GET /trajectory?do_analysis=true` 触发。
- **理由**: 用户列举的"关键原因"(派发给该执行者的原因、底层接口有报错、执行超时)都是可被确定性归因的运维失败模式;轨迹体系作为**独立旁路采集**对正向驱动零侵入(2026-09-17 修正:与 `task_action_log` 无关,原"复用 action log 骨干"的前提作废,见"已确认决策"第 8 条)。
- **不锁死**: 采集层(独立发射)对所有消费方(看板/LLM/提交者透明)完全相同,仅**分析呈现层**随消费方不同;选定排障作为主消费方不影响后续叠加看板或 LLM 层。

## 概述

在 task 模块新增**任务轨迹采集 + 关键原因分析**能力:把一个任务从「提交 → 规划 → 派发 → 实际执行」的完整轨迹构建出来,并分析其中关键原因(派发为何选该执行者、任务为何异常——底层接口报错/执行超时等)。

**关键前提(2026-09-17 修正)**: 轨迹体系**与 `task_action_log` 完全无关**——不读、不改、不富化 action log;轨迹事件由**独立采集链路**在生命周期闸门(submit/plan/dispatch/execute/verify/reset/transition)直接发射并**即时落库**至 `task_trajectory`/`task_trajectory_events`(REQ-11)。本 spec 的工作是**采集 + 组装 + 分析 + 落库的从零建设**,不依赖任何既有轨迹数据。

**轨迹事件必须覆盖的信号**(当前仅散落在内存态/日志/结果性 payload 中,无"为何"侧记录):
- **派发"为何"**: 候选集、候选分、决策模式、策略名、JOIN 滤掉的 bot 及理由、search-skill prompt/LLM 响应摘要 → DISPATCH 轨迹事件(REQ-2)。→ 回答"派发为何选这个执行者"。
- **PLAN 重试逐轮信号**: `_plan_with_retry`(engine.py:599,最多 `MAX_HARNESS` 次)每次尝试的失败原因(`plan_call_fail`/`plan_parse_fail`)、规划 prompt/owner-bot 响应 digest → 每次尝试一条 PLAN 轨迹事件(REQ-3)。→ 规划阶段失败的轨迹不断链。
- **RESET 计量**: `elapsed_ms` 与 SLA 阈值 → RESET 轨迹事件(REQ-4)。→ 直接归因"执行超时"。
- **EXECUTE/VERIFY 错误分类**: 区分**底层接口报错**(bot 侧 200 但 `exec_error` 非空)与执行侧逻辑失败/解析失败 → 事件 `error_type`(REQ-5)。→ 直接归因"底层接口有报错"。
- **提交段**: 提交时点的来源/task_spec 摘要 → SUBMIT 轨迹事件(REQ-6)。
- **Relay 回投段(2026-09-20 补充)**: `/api/v1/collaboration/tasks/callback/report` 的 `EXECUTION_RESULT | PLAN_RESULT | DISPATCH_RESULT | SEARCH_RESULT` 分支在鉴权后记录 `callback_reported` 正常推进证据；DTO 校验或业务处理异常记录 `callback_report_failed`，包含阶段、异常类、错误消息、事件标识及脱敏后的 relay turn 前缀。轨迹发射仍遵循决策 #14，不改变原 HTTP/领域异常语义；鉴权失败只写服务日志，不接受未认证输入污染任务轨迹。
- **回调↔节点关联**: `CallbackCorrelationRegistry` 仅内存态,实例重启后无法把在途回调重新关联回节点 → REQ-P1 持久化。

## 关联文档

- `2026-08-09-task-goal-driven-execution-framework/spec.md` —— 任务目标驱动执行框架(任务中心/图谱/规划/派发/执行/Harness 六模块),本 spec 所处上层子系统。
- `2026-08-24-task-graph-shared-persistence/spec.md` —— `task_action_log` 表(**对照;本 spec 不依赖**,仅闸门坐标参考)。
- `2026-08-20-task-execute-request-persistence/spec.md` —— `task_callback` 回调审计表(执行侧原始证据来源)。
- `core/task/domain/models.py:57`(`NodeAction`)、`:161`(`NodeActionEvent`)、`core/task/task_context/task_graph_service.py:512`(`append_action_event`)、`core/task/task_center/engine.py:713`(`_log_action`)—— 既有 action 事件发射链路(**本 spec 不改它**;轨迹事件在相同闸门位置另起旁路发射)。
- `core/task/task_dispatch/strategies.py`(`SearchBasedDispatchStrategy`/`DirectDispatchStrategy`)、`task_dispatch/dispatcher.py`—— 派发决策点(本 spec 富化重点)。

## 领域定位

- 轨迹体系是**独立旁路**: 自有发射链路(`_log_trajectory`,与 `_log_action` 在相同闸门位置、互不相干)、自有落库表;**不读不写 `task_action_log`**。
- 发射零侵入: 全程 `try/except` 吞异常(**不抛出**,永不阻塞正向驱动),失败记 **WARNING 日志**(观测可见,非 DEBUG),由 AGENTS.md "propagate persistence write failures" 对此 fire-and-forget 观测旁路**明示豁免**——发射失败不影响任何调用方结果,故吞而不 "propagate as error",但用 WARNING 使丢轨迹数据可被运营发现(见"已确认决策"第 14 条);事件**发射即 INSERT** `task_trajectory_events`(append-only,无唯一约束,重复发射可能产生重复行,业务可接受)。
- 新增子模块 `core/task/task_trajectory/`,与 `task_center/task_plan/task_dispatch/task_runner` 平级,定位:**采集旁路 + 只读组装/分析**;不改状态机、不改正向驱动链路。

## Solution

三层架构 + 轨迹持久化:

1. **采集层(独立发射)**: 在 engine 生命周期闸门旁路发射 `TrajectoryEvent`,**发射即落库**(REQ-11);各字段的"为何"信号在发射时定型(REQ-2..7);调度策略额外返回 `DispatchRationale`,随 DISPATCH 事件写入事件行 `ext_info` 列(自由 JSON,领域对象不映射)。
2. **组装层(`TaskTrajectoryAssembler`)**: 给定 `task_id`,读 `task_trajectory_events` 按 `gmt_create` 升序组装 `TaskTrajectory{task_id, timeline, analysis, gmt_create, gmt_modified}`(**无** `phases` / `graph_snapshot`;字段在发射时已定型,组装层仅排序/拼装),并 UPSERT `task_trajectory` 头行;查询端点读持久化表呈现(跨重启可用)。
3. **分析层(`TaskTrajectoryAnalyzer`)**: 取 `TaskTrajectory` → 产出扁平 `TrajectoryAnalysis{analysis_type, analysis_executor, analysis_input, analysis_output, boost_reason: str?, failure_reason: str?, gmt_create}`(无 verdict 对象、不含事件列表);`failure_reason` 由决定性终端事件 `error_type`/`error_msg` 派生,`boost_reason` 由末条 DISPATCH 事件的 `ext_info` 派生;分析结果序列化为 JSON 字符串回填 `TaskTrajectory.analysis` 与各 `TrajectoryEvent.analysis`。分析执行者为多源框架:`rule`(规则引擎)/`llm`(大模型)/`tc_bot`(bot),`analysis_type`/`analysis_executor` 记录实际执行者。
4. **持久化层(REQ-11,2026-09-17 确认)**: 事件在闸门发射时即时 INSERT 落库;头行 `task_trajectory` 组装时 UPSERT;分析结果 UPDATE 回填两表 `analysis` 列与 `gmt_modified`。
5. **时间约定**: 两表持久化的 `gmt_create`/`gmt_modified` 统一使用无时区的 `Asia/Shanghai` 墙上时间;领域对象继续使用 epoch 毫秒。事件发射、头行创建、分析回填及读取反序列化必须使用同一约定,不得混用 naive UTC 或宿主机本地时区。

### 与状态机的关系

不变。轨迹发射是独立旁路(`_log_action`/action log 链路与 `NodeAction` 动作集均不动);`submit` 是**轨迹动作类型集合**的新成员(REQ-6),不改 `NodeAction` 枚举。

---

## 需求列表

### REQ-1: 新增 `task_trajectory` 子模块与轨迹/分析领域模型

- **描述**: 新增 `core/task/task_trajectory/{__init__,models,assembler,analyzer,payloads}.py`,定义:
  - `TrajectoryEvent{task_id, node_id, action_type, action_input: str?, action_result, status_from, status_to, attempt, error_type: ReasonCatalog?, error_msg: str?, boost_reason: str?, holder_id: str?, analysis: str?, gmt_create, gmt_modified}` —— **扁平事件行**(无 `phase`/`payload`/`rationale` 内联)。`gmt_create`/`gmt_modified` = 创建/最后修改时间戳——事件行 append-only,`gmt_create` = 事件发射时间(timeline 排序依据,对应 `task_trajectory_events.gmt_create`),`gmt_modified` 仅在分析回填 `analysis` 时更新(未回填时等于 `gmt_create`);`action_type` = 动作类型(`submit|plan|dispatch|execute|verify|reset|transition`);`action_result` = 该动作结果枚举(`success|hit_single|hit_multi|miss|failed|sla_timeout|pending_dispatch_stuck|exec_failed_retry|bbs_lease_expired|parse_fail|call_fail|accept_pass|accept_fail|...`);`action_input` = 该动作的**输入内容**(完整,**不截断**),由采集层发射时按动作类型定型:`submit→task_spec_digest`(REQ-6)、`plan→prompt_digest`(REQ-3)、`dispatch→下发对象/节点规格内容`(**候选/分写入事件行 `ext_info` 列,不进领域字段**,REQ-9 经 ext_info 读取)、`execute/verify→request_input`(REQ-5,下发请求原文)、`reset/transition→null`(触发原因已由 `action_result`/ext_info 承载);与 `error_msg` 不同,输入不是错误,**成功动作也可非空**;无输入载荷时为 `null`;`error_type` 仅在出错时填 `ReasonCatalog` 分类(成功为 `null`);`error_msg` 为截断消息(成功为 `null`);`analysis` = 内嵌的 `TrajectoryAnalysis` **JSON 字符串**(对象序列化产物,**发射时为 `null`,分析完成后统一回填**;`TrajectoryAnalysis` 已不含事件列表,可直接全量序列化,无递归风险,见 REQ-9)。
  - `TaskTrajectory{task_id, timeline: list[TrajectoryEvent], analysis: str?, gmt_create, gmt_modified}` —— **仅时间线**,不含 `phases`、不含 `graph_snapshot`;`analysis` = 内嵌的 `TrajectoryAnalysis` JSON 字符串(**组装层产出时为 `null`,分析完成后回填**);`gmt_create` = 组装产出时间,`gmt_modified` = 回填 `analysis` 时更新(未分析时等于 `gmt_create`)。
  - `TrajectoryAnalysis{analysis_type, analysis_executor, analysis_input, analysis_output, boost_reason: str?, failure_reason: str?, gmt_create}` —— 派发原因/失败原因均为**扁平字符串**(无 `DispatchVerdict`/`FailureVerdict` 对象,也**不含事件列表/时间线**)——事件已在 `TaskTrajectory.timeline` 中并通过 `analysis` 内嵌关联,分析对象不重复携带:`analysis_type` = 分析类型(取值 `llm | tc_bot | rule`——LLM 大模型分析 / tc_bot 分析 / 确定性规则归因,2026-09-17 确认);`analysis_executor` = 执行分析的**主体自身 id**(`llm` 时为大模型名字、`tc_bot` 时为该 bot 的 id、`rule` 时为规则引擎标识如 `rule_engine`);`analysis_input` = 分析输入内容(喂给分析器的结构化输入:轨迹事件摘要 + ext_info 概要);`analysis_output` = 分析结论汇总文本(`boost_reason`/`failure_reason` 的综合呈现);`boost_reason`(原 `dispatch_reason`)由末条 DISPATCH 事件的 `ext_info`(DispatchRationale)派生(见 REQ-9);`failure_reason` 由决定性终端事件的 `error_type`/`error_msg` 派生;`gmt_create` = 分析产出时间。
  - `ReasonCatalog` 枚举:`execution_timeout | underlying_interface_error | dispatch_stuck | hung | plan_failure | acceptance_failed | parse_error | transport_error | terminal_invalid | unclassified`(及 dispatch 侧 `join_dropped | no_candidates | score_below_threshold | claim_mode_off | catalog_miss`)
- **验收标准**:
  - 模型纯 dataclass / 无副作用;`TrajectoryEvent` 为**扁平投影**(不含嵌套 `payload`/`rationale`/`phase`);`ReasonCatalog` 覆盖 §概述中所有既有失败信号(hung_reason 现有 taxonomy 全量入列)及 REQ-5 的 `exec_error_origin` 分类。
  - **定型列 + ext_info 两层(关键)**: 轨迹事件行(`task_trajectory_events`)由**定型列**(`action_input`/`action_result`/`error_type`/`error_msg` 等,即 `TrajectoryEvent` 领域对象)与一个 `ext_info` 自由 JSON 列(附加素材:`DispatchRationale`、RESET 计量、SUBMIT 来源等,带 `schema_v` 版本号)组成;领域对象不整体映射 ext_info,仅定向投影 `holder_id` 供 API/HTML 排障展示,analyzer 按需读取完整 JSON;`action_input` **不截断**。两表与 `task_action_log` 无任何关联列/外键。
- **改动文件**: `core/task/task_trajectory/models.py`(新增)
- **状态**: 待实现

### REQ-2: 采集层 — 富化 DISPATCH 事件(回答"派发为何选该执行者")

- **描述**:
  - `DirectDispatchStrategy` / `SearchBasedDispatchStrategy.apply` 在返回 outcome 的同时返回 `DispatchRationale`:`{strategy_name: direct|search|replay|bbs, decision_mode: direct|rule|skill|replay|bbs, candidates:[{bot_id, recommend_score, short_profile}], prefetch_tokens:list[str], join_filter_applied:bool, join_dropped:[{bot_id, reason}], skill_prompt_digest:str?, skill_response_digest:str?}`(digest=SHA-256 over prompt + 截断 500 字响应)。
  - `TaskDispatcher.dispatch`(dispatcher.py:62)把 `DispatchRationale` 线程注入节点 patch,供 `engine.py` 在 DISPATCH 闸门(engine.py:2964/2871/2113)旁路发射轨迹事件:定型字段按 REQ-1 填写,**rationale 整体写入事件行 `ext_info` 列**(JSON + `schema_v`)。**`task_action_log` 不做任何改动**。
- **验收标准**:
  - DISPATCH 轨迹事件行的 `ext_info` 含完整 `DispatchRationale`;候选集与 `recommend.score` 来自 `_prefetch_candidates`(strategies.py:539)实际命中结果(非空);search-skill 模式记录 prompt/response digest;JOIN 滤掉的 bot 带 `reason`(扩展现有仅 `claim_mode_off` → 增加 `catalog_miss`/`score_below_threshold`,见 REQ-7)。
  - rationale 组装全程 `try/except`,任一子字段缺失不影响 DISPATCH 轨迹事件正常发射落库,仅 `ext_info=None`(或缺失字段) + WARNING 日志(决策 #14)。
- **改动文件**: `core/task/task_dispatch/strategies.py`、`task_dispatch/dispatcher.py`、`core/task/task_center/engine.py`(DISPATCH 三处闸门挂轨迹发射)、`core/task/task_trajectory/payloads.py`(发射 helper)
- **状态**: 待实现

### REQ-3: 采集层 — PLAN 重试每轮发事件 + LLM prompt/响应 digest

- **描述**: `_plan_with_retry`(engine.py:599)处**每次尝试发射一条** PLAN 轨迹事件(`attempt=n`):中间失败条带 `error_type/error_msg`(reason code)且 `action_input=prompt_digest`,`ext_info` 存 `gap_detail`+`raw_response_digest`;最终成功那条带 `has_gap/children[node_ids]`(ext_info)。策略名(`workflow`/`gap_based`)来自 `TaskPlanner`(planner.py:62 first-match),写入 ext_info。**`task_action_log` 不做任何改动**。
- **验收标准**: 对 `MAX_HARNESS=2` 的失败重试,`task_trajectory_events` 出现 ≥2 条 `action_type=plan`,`attempt` 递增,中间条 error 字段非空;成功路径仅 1 条 ext_info 带 children。digest 仅在 `strategies.py:159`(gap-based)planning prompt 非空时计算;workflow 策略 `prompt_digest=None`(action_input 同为 null)。
- **改动文件**: `core/task/task_center/engine.py`(`_plan_with_retry` 挂轨迹发射)、`core/task/task_plan/planner.py`、`core/task/task_plan/strategies.py`
- **状态**: 待实现

### REQ-4: 采集层 — RESET 事件带 `trigger`/`elapsed_ms`/`sla_threshold`(回答"执行超时")

- **描述**: harness 复位(engine.py:2057 区)发射 RESET 轨迹事件:`action_result` 按 trigger 映射(`sla_timeout|exec_failed_retry|pending_dispatch_stuck|bbs_lease_expired|harness_max`),`ext_info` 存 `{trigger, elapsed_ms: int, sla_threshold_ms: int?, attempts_seen: int}`。`elapsed_ms = now - run_info.start_time`;`sla_threshold_ms` 取自 `harness.py:131` 当前 SLA(单 bot 600s / 协作群 900s / pending dispatch 180s)。**`task_action_log` 不做任何改动**。
- **验收标准**: 任一 SLA 超时复位轨迹事件能凭 `action_result` + `ext_info` 得出"在 N ms 触发超时,阈值 M ms";`pending_dispatch_stuck` 带停留时长;非超时 trigger `sla_threshold_ms=null`。
- **改动文件**: `core/task/task_harness/harness.py`、`core/task/task_center/engine.py`(RESET 闸门挂轨迹发射)
- **状态**: 待实现

### REQ-5: 采集层 — EXECUTE/VERIFY 区分 `exec_error_origin`(回答"底层接口有报错")

- **描述**: EXECUTE/VERIFY 轨迹事件(engine.py:1691/1701/1709 闸门)发射时定型:`error_type` 按 `exec_error_origin` 分类映射(`bot_interface→underlying_interface_error` / `parse→parse_error` / `terminal_invalid→terminal_invalid` / `transport→transport_error`)、`error_msg` 带截断消息、`action_input` 填下发给 bot 的请求原文(**不截断**;try/except 内采集,失败仅置 None)、`ext_info` 存 `interface_error_code?`。**`task_action_log` 不做任何改动**。来源分类:
  - `bot_interface`: callback 来的 `TaskNodePatch.exec_error` 非空来自 bot 侧(callback_adapter.py:181,即"底层接口有报错")。
  - `parse`: callback 整体不可解析(ingest_parse_error, callback_adapter.py:412)。
  - `terminal_invalid`: success 非 bool / failed 无 gaps(callback_adapter.py:194)。
  - `transport`: 规划/调度 HTTP 层异常(plan_call_fail / dispatch_exception)。
- **验收标准**: 给定一条 `exec_error` 来自 bot 回调的轨迹,对应 EXECUTE/VERIFY 轨迹事件 `error_type=underlying_interface_error`、`error_msg` 带接口消息;解析失败类 `error_type=parse_error`;`action_input` 带完整请求内容。
- **改动文件**: `core/task/task_center/engine.py`(EXECUTE/VERIFY 闸门挂轨迹发射)、`core/task/task_runner/callback_adapter.py`(把 origin 透出到 patch,供发射点读取)
- **task_runner 启动轨迹补充(2026-09-21)**: `TaskExecutor.dispatch` 对 `single_bot` 与 `coop_group` 的真实投递记录 `EXECUTE` 事件。投递成功分别使用 `single_bot_started` / `coop_group_started`;投递失败使用 `*_start_failed`,保留原有返回值或异常传播语义。`ext_info` 至少包含 `execution_mode`、`assignee`、`phase`，异常时补 `exception_type`;single-bot 退化群失败并回退直发时记录 `single_bot_group_fallback`。轨迹写入失败仅 WARNING，不得影响主链路。
- **状态**: 待实现

### REQ-6: 采集层 — 新增 `SUBMIT` 动作事件(补齐"从提交"段)

- **描述**: `TaskService.execute`(task_service.py:326,持久化 `task_info` 处 engine.py:351-357)之后发射 SUBMIT 轨迹事件:`action_input` = `task_spec_digest`,`ext_info` 存 `{source: openapi|internal, task_type, owner_user_id, owner_bot_id, submitted_at}`。**`submit` 是轨迹动作类型集合的新成员,不改 `NodeAction` 枚举、不写 `task_action_log`**。
- **验收标准**: 任意任务轨迹 `timeline[0].action_type=submit`;现有各执行分支(workflow/yaml/bbs/外部)均覆盖。
- **改动文件**: `core/task/task_center/task_service.py`、`core/task/task_center/engine.py`
- **状态**: 待实现

### REQ-7: 采集层 — `unauthorized_bots` reason 微分类

- **描述**: JOIN 滤除(strategies.py:359-461)与 catalog 缺失场景的 `reason` 由仅 `claim_mode_off` 扩为:`claim_mode_off | catalog_miss | score_below_threshold | claim_filter_disabled`。写入 `DispatchRationale.join_dropped`(REQ-2,随 DISPATCH 轨迹事件 `ext_info` 落库)。
- **验收标准**: 分类可在 DISPATCH 轨迹事件 `ext_info.join_dropped[].reason` 还原出具体丢因。
- **改动文件**: `core/task/task_dispatch/strategies.py`
- **状态**: 待实现

### REQ-8: 组装层 — `TaskTrajectoryAssembler` + `GET /tasks/{id}/trajectory`(加 `do_analysis`,2026-09-17 合并原 REQ-10)

- **描述**: `TaskTrajectoryAssembler.assemble(task_id)` 读 `TaskTrajectoryRepository`(新,读 `task_trajectory_events`;**不读 `task_action_log`**),按 `gmt_create` 升序把每行还原为 `TrajectoryEvent{task_id, node_id, gmt_create, gmt_modified, action_type, action_result, action_input, status_from, status_to, attempt, error_type, error_msg, boost_reason, holder_id, analysis}`——各字段在发射时已定型,组装层仅读取/排序/拼装——产出 `TaskTrajectory{task_id, timeline, analysis, gmt_create, gmt_modified}`(无 phases / graph_snapshot,读回时 `analysis` 取已落库值,未落库过则 `null`),并 UPSERT `task_trajectory` 头行(REQ-11,不覆盖已有 `analysis`)。HTTP 端点 `GET /api/v1/collaboration/tasks/{task_id}/trajectory` 与 OpenAPI 镜像 `GET /openapi/v1/collaboration/tasks/{task_id}/trajectory`——**读 REQ-11 持久化表呈现**(跨重启可用)——加查询参数 `do_analysis`(bool,默认 `false`)**触发 analysis 的唯一入口**;原 `GET /tasks/{id}/trajectory/analysis` 端点(原 REQ-10)取消、并入此端点。两模式返回形态一致(均 `TaskTrajectory`),仅 `analysis` 是否被刷新不同:
  - **`do_analysis=false`(默认,纯读)**: 不触发任何分析、不写库;原样返回组装后的 `TaskTrajectory`,`analysis` 取已落库值或 `null`。
  - **`do_analysis=true`(触发 bot 总体分析)**: 组装轨迹后,调用 **DI 配置注入**的 bot(`analysis_type=tc_bot`、`analysis_executor=<bot_id>`,bot_id 由部署级配置 `task_trajectory_analysis_bot_id` 注入、**非请求参数、调用方不可选 bot**)做"总体分析";analyzer 按 `tc_bot` 分派到 bot 调用,产出 `TrajectoryAnalysis` JSON,**覆盖回填** `task_trajectory.analysis` 与 `task_trajectory_events.analysis`+两表 `gmt_modified`(REQ-9 回填流程 / REQ-11),再返回携带新 analysis 的同形态 `TaskTrajectory`;**每次 `do_analysis=true` 都重新调 bot 并覆盖**(刷新语义)。首期**同步执行带超时**(超时返 504、**不**回填),不引异步调度。
  - 多执行者框架(rule/llm/tc_bot)见 REQ-9;首期仅 `tc_bot` 经 `do_analysis=true` 触发,`rule`/`llm` 首期不自动触发(将来可加 `analysis_type`/`do_analysis` 取值参数)。
  - 原 `?narrative=true` 不恢复(随 REQ-P2 裁剪)。
- **验收标准**:
  - timeline 按 `gmt_create` 升序;SUBMIT 在首、terminal TRANSITION 在尾。
  - 轨迹体系上线前的旧任务无轨迹数据,接口返回空 timeline(**不读 action log 兜底**)。
  - 现有 `dashboard?include_action_log=true` 行为完全不变(轨迹体系与 action log 无任何交互)。
  - 端点响应 schema(`TaskTrajectory`)+ `do_analysis` query 参数 在 OpenAPI schemas.py 注册;e2e 测试覆盖。
  - `display=html` 的每条事件展示 `boost_reason`(推进原因)和 Relay `holder_id`(执行人);存在错误时分别展示 `error_type` 与 `error_msg`,所有动态文本均 HTML 转义。
  - 默认 `do_analysis=false`:只读,返回 `TaskTrajectory`;从未分析过的任务 `analysis=null`;不写库(两表 `gmt_modified` 不变)。
  - `do_analysis=true`:返回的 `TaskTrajectory.analysis` 为合法 JSON、`analysis_type=tc_bot`、`analysis_executor` 为 DI 配置 bot_id,且已回填两表 `analysis`(`gmt_modified` 更新);再次 `do_analysis=true` 覆盖、对象 `gmt_create`/两表 `gmt_modified` 刷新。
  - `do_analysis=true` 调 bot 失败/超时:返 504 且**不**回填(`analysis` 保持原值),不产生 5xx 之外副作用。
  - e2e 覆盖至少 success / interface_error / timeout / hung 四类(均经 `do_analysis=true` 触发)。
- **改动文件**: `core/task/task_trajectory/assembler.py`(新增,**组装纯读**)、`adapters/http/task/router.py`、`adapters/http/openapi_v1/task/router.py`+`schemas.py`(`do_analysis` 参数 + `TaskTrajectory` 响应 schema)、`core/task/api/task/...`(service facade:组装 + 按 `do_analysis` 调 bot/回填)、`core/task/task_trajectory/analyzer.py`(`tc_bot` 执行者分派)、`core/repository/implementations/task/task_trajectory_repository.py`(回填 UPDATE 复用 REQ-9)、config/DI(注入 `task_trajectory_analysis_bot_id`)
- **状态**: 待实现

### REQ-9: 分析层 — `TaskTrajectoryAnalyzer` 派生扁平原因字符串

- **描述**: `TaskTrajectoryAnalyzer.analyze(trajectory, ext_info_lookup) -> TrajectoryAnalysis{analysis_type, analysis_executor, analysis_input, analysis_output, boost_reason: str?, failure_reason: str?, gmt_create}`(`ext_info_lookup` 按事件唯一键取 `task_trajectory_events.ext_info` 列(JSON)——因 `TrajectoryEvent` 领域对象不内联候选/分/计量;**无 verdict 对象,原因为扁平字符串**):
  - `analysis_type`/`analysis_executor` 由实际执行分析的主体决定;多执行者框架(rule/llm/tc_bot)保留:**首期仅 `tc_bot` 经 `GET /trajectory?do_analysis=true` 触发**(DI 配置注入 bot_id,`analysis_executor=该 bot_id`,见 REQ-8),`rule`(内置规则归因固定 `analysis_type=rule`、`analysis_executor=rule_engine`,产出同样形态 `TrajectoryAnalysis` JSON)**首期不自动触发**,可将来加 `analysis_type`/`do_analysis` 参数按需调用;`llm` 同为可选执行者。
  - `analysis_input` 记录本次分析的输入摘要(事件数 + ext_info 概要);`analysis_output` 为 `boost_reason`/`failure_reason` 拼装后的结论汇总文本;`gmt_create` = 分析产出时间。
  - **分析结果回填(关键流程)**: 分析完成后,把 `TrajectoryAnalysis` **直接全量 `json.dumps`**(对象不含事件列表,无递归风险)为字符串,统一回填到 `TaskTrajectory.analysis` 与 `timeline` 中每个 `TrajectoryEvent.analysis`(各事件回填同一份分析 JSON),同时把 `TaskTrajectory.gmt_modified` 及各事件 `gmt_modified` 更新为回填时间;回填**持久化**——UPDATE `task_trajectory.analysis` 与 `task_trajectory_events.analysis`,并更新两表 `gmt_modified`(REQ-11)。**覆盖语义**(`analysis` 为 single TEXT、只存一份,见"已确认决策"第 13 条):每次回填(含 `do_analysis=true` 多次刷新)**覆盖**前值,最终落库"最近一次分析";历史分析保留首期不做(YAGNI,后续如需加 `task_trajectory_analysis_history` 表);并发回填首期接受"最后写入者胜"(无行锁/版本号)。
  - `failure_reason`(以下为 **`rule` 执行者的确定性派生规则**;`tc_bot`/`llm` 执行者按各自分析填充同名字段、可用 `ReasonCatalog` 作引导——首期 live 链路走 `tc_bot`,见 REQ-8)(仅当任务终止态非 SUCCESS——按末条 **terminal** TRANSITION 事件的 `status_to` 判定,不再落独立 `terminal_status` 字段,反向遍历取决定性终端事件,按优先级拼 `error_type`+`error_msg`;**终端态回退**:若无 terminal TRANSITION 行(如验收 FAIL 经 `_escalate_hung` 翻 HUNG 未走 transition 闸门,该路径下 VERIFY 事件 `action_result="accept_fail"` + `status_to=HUNG` 但无 terminal TRANSITION 行),回退取最后一条 `status_to ∈ terminal 集合({SUCCESS,FAILED,HUNG,CANCELLED})` 的事件的 `status_to` —— 轨迹事件携带 `status_to`(EXECUTE/VERIFY/RESET 记录动作后状态),故无独立 transition 行亦可还原图终态;仅当没有任何事件含 terminal `status_to` 时方为 None):
    - 末个 RESET `action_result=sla_timeout`/`error_type=execution_timeout` → `"execution_timeout: 在 {elapsed}ms 触发,阈值 {threshold}ms"`(elapsed/threshold 取该 RESET 事件 `ext_info` 的 `elapsed_ms`/`sla_threshold_ms`,REQ-4)
    - 最近 EXECUTE/VERIFY `error_type=underlying_interface_error` → `"underlying_interface_error: {error_msg}"`
    - RESET `action_result=pending_dispatch_stuck` → `"dispatch_stuck: 停留 {elapsed}ms"`(elapsed 取 ext_info)
    - `error_type=hung`(或节点 hung_reason) → `"hung: {hung_reason}"`
    - plan 终条 `action_result∈{parse_fail,call_fail}` → `"plan_failure: {origin}"`
    - acceptance FAIL → `"acceptance_failed: {acceptance_result}"`
    - 无匹配 → `"unclassified: {最近事件摘要}"`
  - `boost_reason`(原 `dispatch_reason`): 取末条 `action_type=dispatch` 事件 → `"策略={strategy_name} 模式={decision_mode} 选中={assignee}({action_result}); 候选{candidate_count} 取最优; JOIN 丢={join_dropped 摘要}"`(经 `ext_info_lookup` 读该事件 `ext_info` 中的 `DispatchRationale`)。
- **验收标准**:
  - 对每种 `ReasonCatalog` 分类各有一个 fixture 轨迹,断言 `failure_reason` 以对应前缀开头。
  - `failure_reason` 仅依赖扁平 `TrajectoryEvent`,不读日志/不调外部;`boost_reason` 经 `ext_info_lookup` 纯查 `task_trajectory_events.ext_info`。纯函数易测。
  - 分析完成后 `TaskTrajectory.analysis` 与每个 `TrajectoryEvent.analysis` 均为合法 JSON 字符串,解析后含 `analysis_type`/`analysis_executor`/`boost_reason`/`failure_reason`/`gmt_create`,且不含事件列表(无递归);回填后 `TaskTrajectory.gmt_modified` 与各事件 `gmt_modified` 更新为回填时间(组装时 `gmt_modified=gmt_create`)。
  - 成功任务 `failure_reason=None`。
- **改动文件**: `core/task/task_trajectory/analyzer.py`(新增)、`core/task/task_trajectory/models.py`(ReasonCatalog)
- **状态**: 待实现

### REQ-10: ~~HTTP 端点 `GET /tasks/{id}/trajectory/analysis`~~ — 已并入 REQ-8(2026-09-17)

- **描述**: 原 `GET /api/v1/collaboration/tasks/{task_id}/trajectory/analysis`(及 OpenAPI 镜像)端点**取消**;其 `do_analysis` 参数及触发 bot 总体分析的语义已并入 REQ-8 的 `GET /tasks/{id}/trajectory?do_analysis=`。不再单独保留 `/analysis` 端点(保留 REQ 编号以稳定后续 REQ-11 等引用,不另起实现)。
- **状态**: 已并入 REQ-8(不做独立端点)

### REQ-11: 存储层 — `task_trajectory` / `task_trajectory_events` 轨迹落库(2026-09-17 确认纳入首期)

- **描述**: 轨迹事件**在采集时(闸门发射点)即时 INSERT 落库**,头行在组装时 UPSERT,新增两表(**与 `task_action_log` 无任何关联**)。轨迹事件自身即数据源,不存在"从 action log 收集"的过程:
  - `task_trajectory`(一任务一行,`task_id` 唯一):`id, task_id, analysis(TEXT NULL), gmt_create, gmt_modified`——组装时按 `task_id` UPSERT;已有行的 `analysis`/`gmt_modified` 不被覆盖(未分析时保持首建值)。
  - `task_trajectory_events`(一事件一行):`id, task_id, node_id, action_type, action_input(TEXT), action_result, status_from, status_to, attempt, error_type, error_msg, ext_info(TEXT NULL,自由 JSON), analysis(TEXT NULL), gmt_create, gmt_modified`——闸门发射即 INSERT(append-only,无唯一约束,重复发射可能产生重复行);**完全独立的轨迹事件实体,不读不写 `task_action_log`**。

  **DDL**(`core/task/sql/2026_09_17_task_trajectory.sql`;本地 SQLite 由 `create_all` 自动建表):

  ```sql
  -- task_trajectory / task_trajectory_events: collected trajectory snapshots + analysis backfill.
  -- Operator-provisioned in dev/pre/prod; SQLite tests use ORM metadata.
  CREATE TABLE IF NOT EXISTS `task_trajectory` (
      `id`         bigint(20)   NOT NULL AUTO_INCREMENT                            COMMENT '主键ID',
      `task_id`    varchar(128) NOT NULL                                           COMMENT '任务 ID(一任务一行)',
      `analysis`   text         DEFAULT NULL                                       COMMENT '内嵌 TrajectoryAnalysis JSON 字符串(未分析为 NULL,分析回填时写入)',
      `gmt_create` timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP                 COMMENT '组装产出时间',
      `gmt_modified` timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间(分析回填时间)',
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_task_trajectory_task` (`task_id`)
  ) DEFAULT CHARSET = utf8mb4 COMMENT='任务轨迹头行(组装时 UPSERT,分析回填 analysis)';

  CREATE TABLE IF NOT EXISTS `task_trajectory_events` (
      `id`            bigint(20)   NOT NULL AUTO_INCREMENT                         COMMENT '主键ID',
      `task_id`       varchar(128) NOT NULL                                        COMMENT '归属任务 ID',
      `node_id`       varchar(128) NOT NULL                                        COMMENT '节点 ID',
      `action_type`   varchar(64)  NOT NULL                                        COMMENT '动作类型(submit|plan|dispatch|execute|verify|reset|transition)',
      `action_input`  text         DEFAULT NULL                                    COMMENT '动作输入内容(submit=task_spec_digest;plan=prompt_digest;dispatch=下发对象/节点规格;execute/verify=下发请求原文;reset/transition=NULL)',
      `action_result` text         DEFAULT NULL                                    COMMENT '动作结果枚举(success|hit_single|hit_multi|miss|failed|sla_timeout|pending_dispatch_stuck|exec_failed_retry|...)',
      `status_from`   varchar(64)  DEFAULT NULL                                    COMMENT '动作前节点状态',
      `status_to`     varchar(64)  DEFAULT NULL                                    COMMENT '动作后节点状态',
      `attempt`       int          NOT NULL DEFAULT 0                              COMMENT 'harness 重试序号快照',
      `error_type`    varchar(64)  DEFAULT NULL                                    COMMENT 'ReasonCatalog 错误分类(成功为 NULL)',
      `error_msg`     text         DEFAULT NULL                                    COMMENT '截断后的错误消息(成功为 NULL)',
      `ext_info`      text         DEFAULT NULL                                    COMMENT '扩展信息 JSON(DispatchRationale/RESET计量/SUBMIT来源等(后续可扩展素材);带 schema_v;领域对象不映射,analyzer 按需读)',
      `analysis`      text         DEFAULT NULL                                    COMMENT '内嵌 TrajectoryAnalysis JSON 字符串(未回填为 NULL,分析回填时写入)',
      `gmt_create`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP              COMMENT '事件发生时间(timeline 排序依据)',
      `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间(分析回填时间)',
      PRIMARY KEY (`id`),
      KEY `idx_task_trajectory_events_task` (`task_id`, `gmt_create`),
      KEY `idx_task_trajectory_events_node` (`task_id`, `node_id`, `gmt_create`)
  ) DEFAULT CHARSET = utf8mb4 COMMENT='任务轨迹事件投影行(TrajectoryEvent 物化)';
  ```

  **行为约束**:
  - 事件行**无唯一约束**(append-only):闸门每次触发产生一行,重复触发/重试可能产生重复行,业务接受(分析取末条即可;以自增 `id` 兜底区分)。
  - 两表与 `task_action_log` 无任何关联列/外键/查询依赖。
  - `(task_id, gmt_create)` / `(task_id, node_id, gmt_create)` 支持按任务/节点还原 timeline 升序(无 `seq`,同 `gmt_create` 排序歧义接受)。
  - 两表 UPDATE **仅**发生在分析回填(REQ-9):写 `analysis` + `gmt_modified`;`gmt_create`/定型列/`ext_info` 永不修改。
  - `action_input`/`analysis` 为 TEXT 可变长(原文不截断,见"风险与边界"之 digest 隐私例外);`error_msg` 为截断消息;`ext_info` 为带 `schema_v` 的自由 JSON。
- **验收标准**:
  - 闸门触发后事件行**即时**出现在 `task_trajectory_events`(无需等待组装);按 `gmt_create` 升序可还原 timeline。
  - 发射失败(如库异常)仅 WARNING 日志(决策 #14),不阻塞正向驱动;重复发射可能产生重复事件行(无唯一约束,业务可接受)。
  - 任务推进后再次组装,头行 UPSERT 保留已回填的 `analysis`。
  - 分析回填后两表 `analysis` 非空、`gmt_modified` 更新(REQ-9)。
  - 实例重启后轨迹/分析接口仍可读(数据在库)。
  - 表 DDL 走 `core/task/sql/2026_09_17_task_trajectory.sql`;本地 SQLite 由 `create_all` 自动建表。
- **改动文件**: `repository/models.py`、`core/repository/implementations/task/task_trajectory_repository.py`(新增,含发射 INSERT/头行 UPSERT/回填 UPDATE)、`core/task/task_trajectory/payloads.py`(发射 helper `_log_trajectory`,挂到 engine 各闸门)、`core/task/task_trajectory/assembler.py`、`core/task/task_trajectory/analyzer.py`、`core/task/sql/2026_09_17_task_trajectory.sql`(新增)
- **状态**: 待实现

---

## 跨重启轨迹连续性(2026-09-17 确认纳入首期)

### REQ-P1: 持久化回调↔节点关联

- **描述**: `CallbackCorrelationRegistry`(callback_correlation.py)当前仅内存;新增小表 `task_callback_correlation(event_id, main_session_id, task_id, node_id, retry, gmt_create)`,在 `TaskLoopCallback` 注册时写、回调处理完按 `event_id` 幂等。Assembler 可据此把重启后到达的回调关联回节点。
- **验收标准**: 重启后 in-flight 任务的回调仍能在 trajectory 中关联回正确 (node, retry);表 DDL 走 `core/task/sql/2026_09_16_task_callback_correlation.sql`;本地 SQLite 由 `create_all` 自动建表。
- **改动文件**: `repository/models.py`、`core/repository/implementations/task/task_callback_correlation_repository.py`(新增)、`core/task/task_runner/callback_correlation.py`、`core/task/task_runner/callback_adapter.py`
- **状态**: 持久化层已交付(2026-09-18,commit `f64bbb178`):`InMemoryCallbackCorrelationRegistry` 可选注入 `TaskCallbackCorrelationRepository`,register→`upsert_on_register` 落库(幂等)、resolve 内存未命中→`find_by_event_id` 回填,全程 best-effort(WARNING,不阻塞路由)。**外部依赖(未落地)**:`registry.register()` 在生产代码中无任何调用点 —— 其 dispatch 期登记动作原计划由 `2026-08-09-task-goal-driven-task-runner-callback` 的 runner-integration 后续 spec 落地(见该 spec `plan.md:1694`/`tasks.md:31`),该后续 spec 尚未落地;故 `task_callback_correlation` 表在 `register()` 接入前不会被写入,resolve 的跨重启恢复为 latent infra(`register()` 接入后即自动生效)。`TaskTrajectoryAssembler joins task_callback_correlation`(本 REQ 描述的 step 2)随之 **deferred** —— `register()` 接入前该表为空,join 为死代码。结论:本 trajectory spec 交付了 REQ-P1 的"持久化"半边(旁路观测层的持久化与恢复机制,单测已证明 mechanics);`register()` 接入 + assembler-join 的 prod 激活取决于 runner-integration spec,超出本 spec 范围。生产侧 node-级回调的 (node) 关联回退已由 callback payload 内嵌的 `loop_task_id`(`task_id::node_id`,回调时 split)在跨重启下覆盖,不依赖本 registry。

## P2(已裁剪,不做)

### REQ-P2: LLM 叙述层

- **描述**: ~~`?narrative=true` 时把 `TrajectoryAnalysis` 喂 owner bot 产出自然语言叙述~~ **不做(2026-09-17 确认)**:轨迹/事件中的 `analysis` 本身就可能是请求大模型或 bot 得到的分析结果(`analysis_type=llm|tc_bot`,REQ-9 多执行者框架),无需在其上再叠独立叙述层。`?narrative` 参数不提供(原 REQ-10 `GET /analysis` 端点已并入 REQ-8 `GET /trajectory?do_analysis`,无独立叙述层)。
- **状态**: 已裁剪(不做)

---

## 验收(端到端)

- 能对一个 `task_id` 调一次轨迹接口得到 submit→plan→dispatch→execute 全段 timeline。
- **闸门触发即事件落库**(`task_trajectory_events`,发射时即时 INSERT);分析回填后两表 `analysis` 非空、`gmt_modified` 更新;**实例重启后轨迹/分析接口仍可读**;`task_action_log` 相关测试零变化(完全未触碰)。
- **分析触发**(REQ-8):`GET /tasks/{id}/trajectory?do_analysis=false`(默认)纯读返回 `TaskTrajectory`(`analysis` 为已落库值或 `null`、不写库);`do_analysis=true` 调用 DI 配置注入的 bot 做"总体分析"(`analysis_type=tc_bot`、`analysis_executor=bot_id`)并覆盖回填两表 `analysis`/`gmt_modified`、返回**同形态** `TaskTrajectory`;`rule` 执行者首期不自动触发。原 `GET /tasks/{id}/trajectory/analysis` 端点取消、并入本端点。
- 对"底层接口报错"案例 `failure_reason` 以 `underlying_interface_error` 开头且带接口错误消息。
- 对"执行超时"案例 `failure_reason` 以 `execution_timeout` 开头且带 elapsed/threshold。
- 对"派发给该执行者"案例 `boost_reason` 能给出策略+决策模式+选中+候选数+JOIN 丢因。
- 现有 e2e 测试(`tests/community/core/task/e2e/test_task_pre_e2e_*`)不回归;新增 `tests/community/core/task/task_trajectory/` 单测覆盖发射 helper/assembler/analyzer/落库各分支(发射 INSERT/头行 UPSERT/回填 UPDATE)。

## 风险与边界

- **`ext_info` 自由 JSON 的 schema 漂移**: `task_trajectory_events.ext_info` 依赖 `schema_v` 版本号 + analyzer 对缺失字段防御性降级;定型列不受影响。
- **发射阻塞正向驱动**: 所有轨迹发射(含 ext_info 组装)`try/except`(**不**向上抛,不影响闸门主逻辑);失败记 **WARNING 日志**(非 DEBUG,观测可见)+ 该字段 null/事件缺失。AGENTS.md "propagate persistence write failures" 对此 fire-and-forget 观测旁路**明示豁免**(见"已确认决策"第 14 条)。
- **digest 隐私**: prompt/response 仅存 SHA-256 digest + 截断 500 字,不存全文(避免 token/敏感信息全量落库)。**例外**:`TrajectoryEvent.action_input` 不截断——execute/verify 的 `request_input` 为下发请求**原文落库**,dispatch 的节点规格内容亦为原文,且经 REQ-11 落到 `task_trajectory_events.action_input` **长期保存**;权限收敛已确认**暂不做**(见"已确认决策"第 4 条),原文暴露与膨胀风险接受、后续迭代再议。
- **范围边界**: 不改状态机、不改六模块正向 API 契约、不重构 `extend_props`(既有自由字段保持,本 spec 不收敛它们,避免回归)。

## 已确认决策(2026-09-17)

1. **主消费方**: 确认为"工程师排障根因定位"。
2. **跨重启关联 + 轨迹落库纳入首期**: 轨迹数据**每次收集后存储到数据库**,新建 `task_trajectory`、`task_trajectory_events` 两表(见 REQ-11);`task_callback_correlation` 关联表纳入首期(见 REQ-P1)。
3. **P2 独立 LLM 叙述层不做**: 轨迹表/轨迹事件中的 `analysis` 本身就可能是请求大模型或 bot 得到的分析结果,无需独立叙述层(REQ-P2 已裁剪)。
4. **分析接口权限收敛暂不做**(轨迹含候选/错误/请求原文,暴露风险接受,后续迭代再议)。
5. (原"分析层是否进一步精简"未直接回复;按第 3/6 条答案,REQ-9 薄 analyzer 保留,并按第 6 条定位为 rule/llm/tc_bot 多执行者框架。如要求连 REQ-9/REQ-10 一并去掉请指正。)
6. **execute 输入需要**: REQ-5 的 `request_input` 采集保留,`action_input` 对 execute/verify 映射下发请求原文。
7. **`analysis_type`/`analysis_executor` 取值确认**: `analysis_type ∈ {llm, tc_bot, rule}`;`analysis_executor` 记录执行者自身 id——`llm` 为大模型名字、`tc_bot` 为 bot id、`rule` 为规则引擎标识。连带影响:`analysis` 全量回填每事件的 N 份重复按现状保留(REQ-11 落库后同表冗存,如需事件级独立分析再调整);`gmt_create`/`gmt_modified` 随 REQ-11 成为**真实存储列**(原"内存时间戳、不落库"的结论作废)。
8. **轨迹体系与 `task_action_log` 完全解耦(2026-09-17 再次修正,推翻原"轨迹骨干已存在"前提)**: 轨迹事件由独立采集链路(`_log_trajectory`)在生命周期闸门直接发射并即时落库 `task_trajectory_events`,**不读、不改、不富化 action log**。连带设计(本轮新增,如与预期不符请指正):
    - 事件行新增 **`ext_info` 自由 JSON 列**承载 analyzer 素材(`DispatchRationale` 候选/分、RESET 的 elapsed/threshold、SUBMIT 来源),领域对象不映射——否则 `boost_reason`/超时归因无处取数;
    - 写入时机为**闸门发射即时 INSERT**(非"组装后批量落库"),无唯一约束(append-only,重复发射可能产生重复行,业务接受以末条/自增 `id` 为准);
    - `submit` 属轨迹动作类型集合,不改 `NodeAction` 枚举;
    - 轨迹体系上线前的旧任务无轨迹数据(无 action log 兜底)。
9. **`task_trajectory_events` 表设计微调(2026-09-17)**: 移除唯一键 `uk_task_trajectory_event`(`task_id`,`node_id`,`action_type`,`gmt_create`),事件行改为 append-only(无唯一约束,重复发射可能产生重复行,业务接受以自增 `id`/索引区分、分析取末条);自由 JSON 列 `detail` 更名为 `ext_info`(承载 `DispatchRationale`/RESET 计量/SUBMIT 来源等,后续新增扩展素材统一入此列,领域对象不映射)。原"唯一键幂等/重复发射不产生重复事件行"表述作废,REQ-11 行为约束/验收标准/风险已同步。

---

## 已确认决策(续,2026-09-17 第二轮 — analysis 触发时机 `do_analysis`)

10. **analysis 触发模型用 `GET /trajectory?do_analysis=`(2026-09-17 确认,`/analysis` 端点取消)**: 不另起引擎旁路自动触发、不新增 POST 端点、**不留独立 `/analysis`**;触发语义并入 `GET /api/v1/collaboration/tasks/{task_id}/trajectory`(及 OpenAPI 镜像)的查询参数 `do_analysis`(bool,默认 `false`):
    - `do_analysis=false`(默认)= 纯读:返回 `TaskTrajectory`(timeline + analysis),`analysis` 取已落库值或 `null`,不跑任何分析、不写库;
    - `do_analysis=true` = 触发 bot "总体分析":调用 **DI 配置注入**的 bot(`analysis_type=tc_bot`、`analysis_executor=<bot_id>`,bot_id 由部署级配置 `task_trajectory_analysis_bot_id` 注入、**非请求参数、调用方不可选 bot**),产出 `TrajectoryAnalysis` JSON **覆盖回填**两表 `analysis`+`gmt_modified`(REQ-9/REQ-11),再返回**同样形态**的 `TaskTrajectory`(携带新 analysis);每次 `true` 都重新调 bot 并覆盖(刷新语义);首期**同步带超时**(超时返 504、不落库),不引异步调度;
    - 两模式返回形态统一(均为 `TaskTrajectory`),仅 `analysis` 是否被刷新不同。
11. **`rule` 执行者首期不自动触发(2026-09-17 确认)**: 原"决策#2 确定性规则归因为主"在首期降为"**bot 为主、rule 备用**"——`rule`/`llm`/`tc_bot` 多执行者框架(REQ-9)保留,首期仅 `tc_bot` 经 `do_analysis=true` 触发;`rule`(及 `llm`)首期不自动触发,将来可加 `analysis_type` 或 `do_analysis` 取值参数按需调用。REQ-9 的 7 条 `failure_reason` 派生规则保留为 `rule` 执行者的实现、首期不进入 live 链路(其单测仍保留,纯函数易测)。
12. **不自动触发终态/卡死分析(2026-09-17 确认)**: 推翻早先提案"engine 在 terminal TRANSITION/卡死 RESET 旁路自动跑 rule 分析"——首期**不在引擎闸门挂任何分析触发**,分析完全由 `GET /trajectory?do_analysis=true` 按需驱动;终态/卡死任务不调 `do_analysis=true` 则 `analysis=null`,GET 默认返回空分析(不读 action log 兜底)。
13. **回填覆盖语义(2026-09-17 确认)**: `analysis` 为 single TEXT、只存一份,每次 `do_analysis=true` **覆盖**前值,最终落库"最近一次分析";历史分析保留首期不做(YAGNI,后续如需加 `task_trajectory_analysis_history` 表);并发回填首期"最后写入者胜"(无行锁/版本号),严格化后续再议。
14. **轨迹发射失败处理 = 吞而不抛 + WARNING + AGENTS.md 豁免(2026-09-17 确认)**: AGENTS.md "Propagate database and persistence write failures as errors; never silently swallow failed writes and return success" 对**轨迹发射**(`_log_trajectory`)做**明示豁免**:轨迹发射是 fire-and-forget 观测旁路,其写入成败不影响任何调用方结果(无人依赖"轨迹已落库"这一效果),故吞而不作为 error 抛出,满足 spec 零侵入核心;但为不违背 AGENTS.md "失败可见"精神,**日志级别由 DEBUG 提为 WARNING**(发射失败、丢轨迹数据可被运营发现)。豁免边界:仅限观测旁路发射;`task_callback`/业务正写等仍按 AGENTS.md 严格 propagate。回填层(`backfill_analysis`)的写失败**不豁免**——两表 UPDATE 须事务原子(`transactional_orm_session` 兜底 `orm_session`),失败须传播。
