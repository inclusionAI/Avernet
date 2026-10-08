# Tasks — task_runner integration 子模块（单 bot / 协作群真实执行接入）

> 参考羽雀「任务Loop执行」（`lxg2mwgmtfqg6d95`）+ BaaS Open API 脚本（`test_tc_open_api/`）+ corp ocb `BcsHttpClient`（参考模式，自包含重写）。
> 落点：`src/backend/src/agentclaw/community/core/task/task_runner/integration/`（新建）+ `runner.py` 注入点改造；结果回流一律经 `ResultSink → TaskLoopCallback.report_result → engine.on_report → update_task_node_info`（SSOT）。
> 上游 spec：`specs/2026-08-09-task-goal-driven-execution-framework/`（M0–M6 已落地，121 单测）；权威 spec/plan：本目录 `spec.md` / `plan.md`。

## 实现原则（对齐 AGENTS.md）
- **不破上游契约**：`TaskRunner.start_run(toDoTaskList) -> list[bool]` async 签名不变；`TaskLoopCallback`/`CallbackAdapter`/`TaskCallbackData`/`loop_task_id="task_id::node_id"` 不变；不注入 `execution_backend` 时现行 stub 行为 + 121 单测全绿。
- **SSOT 不绕过**：integration 不直写图、不直改 `TaskNode.status`；结果回流一律 `ResultSink.report_result(TaskCallbackData)` → `on_report` → `update_task_node_info`。
- **开源边界**：integration 不 `import` corp ocb `ecb`；单 bot 走 BaaS HTTP Open API（httpx async，**不 import in-repo `secbaas` BotRunner**）；BCS client 自包含重写（httpx async + HMAC，镜像 ocb `BcsHttpClient` 模式）。
- **零 case 知识红线**：`PromptFormatter`/adapter/executor 仅消费 `_build_context` dict 字段 + `TaskNode.task_spec` + `run_info.assignee`/`run_mode`，**禁止**节点名字面量（`N_market`/`N_overview` 等）；grep 0 命中，单测断言。
- **必填非可选**：端口必填参数不加 `| None`；`None` 仅契约态（`session_id is None`→`create_session`、`bot_id` 不在 `allowed_bots`→`ensure_grant`、`run_id is None`→session 模）。
- **协程化（对齐 README）**：`engine._drain` 锁外 `await runner.start_run`；`dispatch` async + `gather`+`Semaphore(8)`；poller `_poll_once` 为 `async def`（端口 async），daemon 线程持自有 loop 跑 `run_poll_loop`；锁内不 await。
- **派发即返回（不等待结果）**：`dispatch` 仅 await 到捕获 `run_id`/`session_id`（grant+send / create_session / start_state_machine_run），失败→该 node 返 `False`；结果回收归 `TaskExecutorResultPoller` 旁路 sidecar。
- **TDD / 主 seam 优先**：R4 singlebox E2E（复用剧本 `gwqie46v7hzr1w6h`）最高 seam；R0–R3 契约单测（httpx `MockTransport`）补 e2e 覆盖不到的分支。
- **httpx 单测不经网络**：一律 `httpx.MockTransport(handler)`；async 单测经 `asyncio.new_event_loop().run_until_complete`（不用 `@pytest.mark.asyncio`）。

## 依赖与落地序（R0 骨架 → R1 单 bot → R2 BCS chat/manager_worker → R3 state_machine → R4 e2e 收口）

```
R0  ports.py(5 Port Protocol) + translators.py(三翻译器)
 → TaskExecutor 骨架(dispatch async + bbs no-op) + TaskRunner.__init__(graph, execution_backend=None) 注入点(默认 stub 不破)
R1  OpenApiBotAdapter(ensure_grant + send_message + get_run,httpx async)
 → TaskExecutorResultPoller sidecar + single_bot 模
 → _dispatch_single_bot 接通(grant→send→register) + PromptFormatter + _RunnerContextBuilder
R2  BcsHttpAdapter(httpx async + HMAC) + BcsCreateGroupRequest/Result + chat/manager_worker 端点(create_group/create_session/get_group/get_session_messages)
 → form_coop_group 真实建群(三态分流) + _dispatch_coop_group session 模 + poller session 模
R3  state_machine:start_state_machine_run/get_state_machine_run + _dispatch_state_machine(run 模) + poller run 模 + start_initial_run=False 约束
R4  singlebox double(_DoubleOpenApiBot/_DoubleBcsClient/_DoubleApiKeyProvider/_DoubleContextProvider/_DoubleSink) + build_integration 组合根 + poller bcs 模
 → singlebox wiring 注入 + 复用剧本 gwqie46v7hzr1w6h 5 类 e2e + bbs no-op 断言 + 零 case grep
```
R2 依赖 R1 的 poller/executor；R3 依赖 R2 的 BcsHttpAdapter；R4 依赖 R0–R3 全就绪 + singlebox wiring 点定位。

---

## R0 — Port + 翻译器 + executor 骨架 + 注入点（默认 stub 不破）
- **T0.1** `integration/ports.py`：5 `@runtime_checkable` Protocol——`OpenApiBotPort`(ensure_grant/send_message/get_run async)/`BcsClientPort`(create_group/create_session/get_group/get_session_messages/start_state_machine_run/get_state_machine_run/validate_definition async)/`ApiKeyProvider`(api_key/api_key_prefix/base_url/cookie/referer 属性)/`TaskContextBuilder`(build→dict)/`PromptFormatter`(format_execute/format_verify)/`ResultSink`(async report_result)；`BcsCreateGroupRequest`/`Result` 先 Protocol 占位（T7 换真实 dataclass + `TYPE_CHECKING` import，不打环）。
- **T0.2** `integration/translators.py`：`SingleBotRunTranslator.adapt(run_dict, loop_task_id)`（status 大小写不敏感；`TIME_OUT`→`timeout`）、`BcsSessionTranslator.adapt(group_dict, messages)`（output 缺失取末条 assistant content）、`BcsStateMachineRunTranslator.adapt(run_dict)`（`aborted`→fail_detail）；均返 `TaskCallbackData`（`workflow_type` single_bot/bcn_coop_group，`workflow_id`/`instance_id`=0）。
- **T0.3** `integration/task_executor.py` `TaskExecutor(*, bot, bcs, formatter, context, sink, poller)`：`async dispatch(toDoTaskList) -> list[bool]`（`gather`+`Semaphore(8)`，按 `run_mode` 三态分流；`bbs` 仅 `logger.info` no-op 返 True；unknown→False；`single_bot`/`coop_group` 占位 True 待 T6/T8 替换）、`async form_coop_group(gf)`（stub `grp_{uuid}` 占位待 T8）、`async aclose()`、`_group_meta` dict。
- **T0.4** `runner.py` 改造：`__init__(graph, execution_backend=None)`；`start_run._deliver_one` 注入时委托 `execution_backend.dispatch([node]) == [True]`；`form_coop_group` 注入时委托；未注入走原 stub（`_run_log`/`_groups` 行为不变，121 单测不破）。
- ✅ 验收：T0.1 ports 4 用例；T0.2 translators 10 用例；T0.3 executor 骨架 4 用例（bbs no-op/unknown False/每节点一 bool/无 backend stub fallback）；T0.4 既有 121 单测全绿。

## R1 — 单 bot 真实链路（Open API + grant + poller single_bot）
- **T1.1** `integration/open_api_bot_adapter.py`：`OpenApiBotAdapter(ApiKeyProvider, *, http_client)` 实现 `OpenApiBotPort`——`ensure_grant`（`GET /api/v1/api-keys/{prefix}/allowed-bots` Bearer；缺则 `POST .../grant` **Cookie+Referer** 鉴权）、`send_message`（`POST /openapi/v1/messages` Bearer→`data.message_id`=run_id）、`get_run`（`GET /openapi/v1/messages/{id}`→`data`，status 大小写不敏感）；异常 `OpenApiAuthError`(401/403 不可重试)/`OpenApiBadRequestError`(4xx)/`OpenApiRateLimitError`(429)/`OpenApiServerError`(5xx)/`OpenApiTimeoutError`；`parse_bot_id(bot_id) -> (real, entity)`（`<real>:<entity>`）。
- **T1.2** `integration/task_executor_result_poller.py`：`SingleBotHandle`/`BcsGroupHandle` dataclass；`TaskExecutorResultPoller(*, bot, bcs, clock, sleep, interval, default_sla)`——`register`/`set_on_result`/`pending`/`stop`、`async _poll_once()`、`async _poll_one(handle)`（SLA 超时→FAIL `sla_timeout`+注销；连续 5 次端口异常→FAIL `poll_exhausted`+注销；终态→翻译→`report_result`+注销）、`run_poll_loop(stop_event)`（daemon 线程持自有 loop）。single_bot 模：`get_run`→`{COMPLETED,FAILED}`→`SingleBotRunTranslator`。
- **T1.3** `task_executor.py._dispatch_single_bot(node, sem)`：`ensure_grant(assignee)`→`ctx=context.build`→`message=formatter.format_execute(ctx,node)`→`send_message`→`run_id`→`poller.register(SingleBotHandle(loop_task_id=task::node, run_id, bot_id, registered_at))`；`OpenApiAuthError`/`OpenApiBadRequestError`→返 `False`（不阻塞其它 node）。
- **T1.4** `integration/prompt_formatter.py`：`PromptFormatterImpl`（零 case，消费 `node_instruction`/`goal`/`sibling_outputs` + `node.task_spec`；`format_verify` 用 `acceptances`/`child_outputs`）+ `_RunnerContextBuilder(runner)`（包 `runner._build_context`，integration 内聚访问）。
- ✅ 验收：T1.1 open_api_bot 7 用例（grant 查/补/403、send Bearer+请求体、get_run 大小写不敏感、5xx）；T1.2 poller single_bot 4 用例（终态上报注销/非终态无上报/SLA 超时 FAIL/连续 5 次 poll_exhausted）；T1.3 dispatch_single_bot 3 用例（register handle/grant 403→False/prompt 用 context）；T1.4 formatter 用 context 字段不写节点名；上游 121 全绿。

## R2 — BCS client（chat / manager_worker）+ form_coop_group + session 模
- **T2.1** `integration/bcs_token_provider.py`：`BcsTokenProvider` Protocol（`token`/`secret`/`base_url` 属性；real 由 corp 注入）。
- **T2.2** `integration/bcs_http_adapter.py`：`BcsCreateGroupRequest`/`BcsCreateGroupResult` dataclass（§7.1 字段；result 含 `group_id`/`session_id`/`run_id`/`definition_ref`）；`BcsHttpAdapter(BcsTokenProvider, *, http_client)` 实现 `BcsClientPort`——HMAC 头（`X-ECB-Token`/`X-ECB-Timestamp`/`X-ECB-Signature`，签串 `f"{ts}{method}{path}"`）、`Idempotency-Key`、`create_group`（三态：state_machine 强制 `group_strategy=state_machine`+`start_initial_run=false`+yaml+participant_bindings；chat 省略 strategy；manager_worker 带角色）、`create_session`、`get_group`、`get_session_messages`（`since_msg_id` 进 query）；异常 `BcsServerError`(5xx)/`BcsClientRequestError`(4xx)/`BcsRateLimitError`(429)/`BcsTimeoutError`；`start_state_machine_run`/`get_state_machine_run` 占位 `NotImplementedError`（T3.1 替换）。
- **T2.3** `ports.py` 调整：`BcsCreateGroupRequest`/`Result` 占位 Protocol 换为 `TYPE_CHECKING` import 真实 dataclass（`bcs_http_adapter` 不 import `ports`，无运行时环）。
- **T2.4** `task_executor.py.form_coop_group(gf)`：按 `gf.collab_mode` 三态构造 `BcsCreateGroupRequest`（state_machine 取 `extend_props["collaboration_definition_yaml"]`+`participant_bindings={bid:{source:manual,bot_ids:[bid]}}`+`start_initial_run=False`；manager_worker manager=`extend_props["manager_bot_id"]` or `bot_ids[0]`；service_spec 透传）→`create_group`→存 `_group_meta[group_id]={collab_mode, gf, definition_ref, session_id}`→返 `group_id`。
- **T2.5** `task_executor.py._dispatch_coop_group(node, sem)`（chat/manager_worker 分支）：`meta=_group_meta[assignee]`，`collab_mode!=state_machine`→`create_session(group_id, bootstrap_prompt=format_execute(ctx,node))`→`session_id`→`poller.register(BcsGroupHandle(session_id=..., run_id=None, collab_mode, registered_at))`；state_machine 分支占位 T3.2。
- ✅ 验收：T2.2 bcs_http_adapter 7 用例（HMAC 签名+Idempotency-Key、三态 create_group 请求体、create_session、get_group、since_msg_id 游标、5xx/4xx 异常）；T2.4/T2.5 form_coop_group 3 用例（chat 存 meta/manager_worker strategy/session 模 register handle）；上游 121 全绿。

## R3 — BCS state_machine（run 模 + run_id 捕获约束）
- **T3.1** `bcs_http_adapter.py` 落地两端点：`start_state_machine_run(group_id, *, definition_yaml, definition_ref, session_id, input) -> str`（`POST /groups/{id}/state-machine-runs` body `{definition_ref, input, ...}`→`run.run_id`，带 Idempotency-Key）、`get_state_machine_run(run_id) -> dict`（`GET /state-machine-runs/{run_id}`）。
- **T3.2** `task_executor.py._dispatch_state_machine(node, group_id, meta, loop_task_id)`：`input={"query": format_execute(ctx,node)}`+`definition_ref=meta["definition_ref"]`→`start_state_machine_run`→`run_id`→`poller.register(BcsGroupHandle(run_id=..., session_id=None, collab_mode=state_machine, registered_at))`；**不**用 `create_session`。
- **T3.3** poller run 模：`BcsGroupHandle` 且 `run_id` 非空→`get_state_machine_run(run_id)`→`run.status ∈ {completed,failed,aborted}`→`BcsStateMachineRunTranslator`→`report_result`（已在 T1.2 `_poll_terminal` 内置分支，T3 验证）。
- ✅ 验收：T3.1 start/get_state_machine_run 2 用例（run_id 解析、GET 路径）；T3.2 dispatch_state_machine 1 用例（register run handle + input.query=格式化）；T3.3 poller run 模经 double 验证（R4 T4.3）；`start_initial_run=False` 约束在 T2.4 create_group 已强制；全 integration 单测回归绿。

## R4 — singlebox double + E2E 收口
- **T4.1** `integration/double/double_context_provider.py`：`_DoubleApiKeyProvider`（固定静态凭据）、`_DoubleContextProvider`（canned `{"mode":"execute"}`）、`_DoubleSink`（收集 `TaskCallbackData`）。
- **T4.2** `integration/double/double_open_api_bot.py` `_DoubleOpenApiBot(*, final_status, content, error)`（进程内 grant→send→get_run，不经网络）+ `integration/double/double_bcs_client.py` `_DoubleBcsClient(*, session_status, session_output, sm_status, sm_output)`（三态 create_group→session/run poll→终态，可注入 FAIL/timeout）。
- **T4.3** `integration/__init__.py` `build_integration(*, double, sink, runner, poller_thread) -> TaskExecutor`：`double=True` 装配 `_DoubleOpenApiBot`/`_DoubleBcsClient`/`_DoubleContextProvider`/`PromptFormatterImpl`+`_DoubleSink`，`poller.set_on_result(sink)`，可选起 poller daemon 线程；`real` 分支 import 在函数内（corp 提供 `_RealToken`/真实 keys，社区不发）。
- **T4.4** poller bcs 模单测：`test_poller_bcs.py` 验 session 模（`get_group` completed→上报）+ run 模（`get_state_machine_run` completed→上报）经 `_DoubleBcsClient`/`_DoubleSink`。
- **T4.5** singlebox wiring：`grep -rn "gwqie46v7hzr1w6h\|_wire_facade" tests/` 定位装配点；`TaskService(g)` 构造后 `exe = build_integration(double=True, sink=svc.callback, poller_thread=True)` 并 `svc._engine._runner._execution_backend = exe`（内部访问 wiring）。
- **T4.6** 复用剧本 `gwqie46v7hzr1w6h` 5 类 e2e（三模态 happy→DONE / FAIL 补救治愈 / MISS 升 BBS / BBS STUCK→HUNG / dashboard 终态）：以**真实集成形态（double，非纯 stub）**驱动同一闭环；BBS 类用例断言 `TaskExecutor` 对 `bbs` 仅记日志、不改节点状态、不登记 poller。
- **T4.7** 零 case grep 红线：`test_zero_case.py` 断言 integration 9 文件（ports/translators/open_api_bot_adapter/bcs_http_adapter/bcs_token_provider/prompt_formatter/task_executor/task_executor_result_poller/__init__）0 命中 `N_overview`/`N_market`/`N_aggregate`/`N_verify`/`N_report`/`N_practice`/`n_root`/`dim_`。
- ✅ 验收：T4.1–T4.3 double 5 用例 + 组合根；T4.4 poller bcs 2 用例；T4.5/T4.6 singlebox 5 类 e2e 真实集成形态全绿 + bbs no-op 断言；T4.7 grep 0 命中；既有 121 + integration 全量回归绿；pre-push Backend SAST gate 通过。

## Cross-cutting
- 架构宪法（`docs/arch/arch.rules.md` 核心 transport-agnostic）：integration 是 adapter 层，可 import core + httpx，core 不 import integration。
- `loop_task_id` mint 在 `TaskRunner.start_run`（`runner.py:75` `f"{task_id}::{node_id}"`）；single_bot/coop_group 的 poller handle 均带 `loop_task_id` 回流。
- 事件日志/审计位点：`run_id`/`session_id`/`group_id`/`loop_task_id`/`poller.registered_at`/`fail_detail`（`sla_timeout`/`poll_exhausted`/`timeout`/`aborted`）。
- 组合根：`build_integration` 唯一装配点；`real` wiring（真实 keys/BCS token/BaaS base_url）属 corp adapter（社区只发 double/singlebox）。

## Risks / 待实现期定的项
- ocb `BcsHttpClient` 缺 `group_strategy`（纯客户端封装缺口，server 端支持）→T2.2 自包含补齐。
- state_machine 默认 `start_initial_run=true` 不回 `run_id`→T2.4 强制 `start_initial_run=False` + T3.2 显式 `start_state_machine_run` 捕获。
- BCS 无入站 webhook（协作群只能轮询）→T1.2/T4.4 `TaskExecutorResultPoller` session/run 模。
- 单 bot grant 需登录态（`grant` 走 Cookie/Referer，非 Bearer；`create_api_key.py`）→T1.1 `ensure_grant` 双头分离；静态配置 `secbaas_cookie`/`secbaas_referer`（stub/singlebox 可空）；grant 403→T1.3 该 node 返 False。
- 单 bot 结果只能轮询（Open API `/messages` 无 PUSH）→T1.2 single_bot 模 `GET /messages/{run_id}`。
- 单 bot 与 `secbaas` 跨包身份（旧设计经 in-repo `BotRunner` 需 `BotChatContext`）→改走 Open API HTTP，仅需静态 API key，不 import `secbaas`。
- ocb `BCSGroupService` 同步包装层 TD-5/6/7（硬编码/吞异常/`asyncio.run` 不可在 running loop）→不复用，直接 async `BcsHttpAdapter`。
- facade 2 API 未挂 HTTP（旧 `/task/callback` 路由）→**本 spec 不新增任何入站路由**；poller 进程内 wired 回投（`ResultSink=svc.callback`）。
- 持久化：poller 登记表 in-mem（与 `TaskHarness._dispatched_at` 同级）；ORM 适配后续。
- singlebox wiring 经 `svc._engine._runner._execution_backend = exe` 直赋（内部访问）；若后续 DI 化需 corp 覆写 `_build_runner`。
- `build_integration(real)` 的真实 token/密钥/BCS base_url 属 corp adapter（社区只发 double）。

## 里程碑（R0–R4，本计划交付）
- ⬜ **R0** Port + 翻译器 + executor 骨架 + `TaskRunner` 注入点（默认 stub 不破）。
- ⬜ **R1** 单 bot 真实链路（OpenApiBotAdapter + grant + poller single_bot 模 + PromptFormatter）。
- ⬜ **R2** BCS client（chat/manager_worker：BcsHttpAdapter HMAC + form_coop_group 三态 + session 模 dispatch/poll）。
- ⬜ **R3** BCS state_machine（start/get_state_machine_run + run 模 dispatch/poll + `start_initial_run=False` 约束）。
- ⬜ **R4** singlebox double + `build_integration` 组合根 + 复用剧本 5 类 e2e（真实集成形态）+ bbs no-op 断言 + 零 case grep 红线。

> 实现逐任务 TDD 细节见 `plan.md`（每任务含失败测试→实现→通过→commit 可执行步骤）；契约依据见 `spec.md`；与 `2026-08-09-task-goal-driven-task-runner-callback`（inbound PUSH 回调）为互补关系——本计划为 poller 模（无 workflow 引擎的执行主体），callback spec 为 PUSH 模（有 workflow 引擎的执行主体），两路同 sink `on_report`。