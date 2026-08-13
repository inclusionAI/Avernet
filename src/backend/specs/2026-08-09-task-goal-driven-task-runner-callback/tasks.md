# Tasks — task_loop callback 服务子模块(inbound PUSH,单 bot workflow / bcn 协作群)

> 参考羽雀「任务Loop执行」→「回调服务_供单bot workflow或者bcn协作群任务使用」(`lxg2mwgmtfqg6d95`)。
> 落点:`src/backend`(core `on_start`/`adapt_start`/registry + `adapters/http/task` 边缘 + `di/modules/task_module` 装配);PUSH 与 runner-integration poller 共存(修订 runner spec §7.9)。
> 权威 spec:`specs/2026-08-09-task-goal-driven-task-runner-callback/spec.md`;实现计划:`plan.md`。

## 实现原则(对齐 AGENTS.md)
- **SSOT 不绕过**:所有入站回调一律 `on_start`/`on_report`→`update_task_node_info`;router/translator/registry 不得直写图、不得直改 `TaskNode.status`。
- **不破上游契约**:`TaskCallbackData`/`CallbackAdapter.adapt`/`engine.on_report`/`TaskLoopCallbackProtocol`/`loop_task_id="task_id::node_id"` 不变;`on_start`/`adapt_start` 为纯新增;不注入 `TaskModule` 时现行 121 单测全绿。
- **HTTP 边缘翻译,SSOT 精简**:不扩 `TaskCallbackData`;羽雀丰富字段(`goal`/`status`/`is_success`/`output`/`failed_info`/`ext_info`/`node_id`)在 translator 边缘折叠进 SSOT 既有字段 + `result["_ext_info"]`(由 `adapt`/`adapt_start` 折 `extend_props_patch`)。
- **协程化(对齐 README)**:`on_start` per-task `threading.RLock` 锁内同步写图(`update_task_node_info` 内存同步),**锁内不 await**;与 `on_report` 一致;router handler `async def`。
- **必填非可选**:`workflow_source`/`workflow_id`/`workflow_instance_id`/`task_id`/`node_id` 必填;`T|None` 仅契约态(`goal`/`output`/`failed_info`/`ext_info`/`loop_task_id` 可空,None 触发兜底)。
- **错误分层**:`CallbackAuthError`(401)/`CallbackCorrelationError`(400) 为 `DomainError` 子类进中央 `_DOMAIN_ERROR_STATUS_MAP`;`TaskStateError`/`TaskNotFoundError`/`NodeNotFoundError` 属 `TaskError`(非 `DomainError`),router 层显式 try/except 映射。
- **零 case 知识红线**:callback 模块源码 `grep -rE 'N_overview|N_market|N_aggregate|N_verify|N_report|N_practice|n_root|dim_'` 必须 0 命中(节点名仅存 case 策略 stub/测试)。
- **开源边界**:HMAC 镜像 ocb `BcsHttpClient` 签名模式但**不 import ocb**;社区分布只绑 `NoopCallbackAuthenticator`,CORP/prod 的 `HmacCallbackAuthenticator`+密钥由 corp adapter 覆写。
- **TDD / 主 seam 优先**:R2 singlebox E2E 最高 seam(依赖 runner-integration 派发期登记 seam 落地);R0–R1 契约单测覆盖 4 端点×分支。

## 依赖与落地序(R0 边缘+核心骨架 → R1 鉴权+契约 → R2 e2e+收口)

```
R0  DomainError 子类+status map
 → engine.on_start(status-direct PENDING→RUNNING 幂等)
 → CallbackAdapter.adapt_start + 激活 start_run + adapt 折 _ext_info
 → CallbackCorrelationRegistry(task 级→节点寻址 seam)
 → Pydantic v2 schema + CallbackRequestTranslator(边缘→SSOT)
 → callback router 4 端点(Noop auth + 错误映射 + 幂等 ack)
 → TaskModule DI 装配 + profile 登记 + app 挂载 router
R1  HMAC CallbackAuthenticator + loop_task_id 回声透传契约 + 幂等/错误映射收口
R2  singlebox E2E(double 回调驱动三态闭环 + 重投幂等) + 零 case grep + 全量回归
```
R2 依赖 runner-integration 委托期 `CallbackCorrelationRegistry.register(...)` 登记动作落地;R0–R1 可独立交付(进程内 wired 回投)。

---

## R0 — 边缘 + 核心骨架(可独立测试)
- **T0.1** `core/errors.py` 新增 `CallbackAuthError`/`CallbackCorrelationError`(`DomainError` 子类);`adapters/http/app.py:_DOMAIN_ERROR_STATUS_MAP` 补 `CallbackAuthError→401`/`CallbackCorrelationError→400`(架构测试 `test_domain_error_status_map_complete` 枚举到 2 新子类且 map 覆盖)。
- **T0.2** `ExecutionEngine.on_start(patch: TaskNodePatch) -> NodeOpResult`(新增,`task_center/engine.py`):锁内 `query_task_dashboard` 取节点;`PENDING→RUNNING` 经 `update_task_node_info`(`_DIRECT_TRANSITIONS`);已 `RUNNING`→no-op `NodeOpResult(prev=new=RUNNING,success=True)`;`DONE/FAILED/HUNG/PLANNING`→raise `TaskStateError`(stale);node 不存在→raise `NodeNotFoundError`;**不**触发 `_drain`/传播/side-effect。
- **T0.3** `CallbackAdapter.adapt_start(data) -> TaskNodePatch`(`callback_adapter.py`):`loop_task_id.split("::",1)` + `status=Status.RUNNING` + 折 `result["_ext_info"]`→`extend_props_patch`;`adapt`(result) 追加折 `_ext_info`(与既有 `fail_detail` 合并,既有单测不设 `_ext_info` 故不受影响)。
- **T0.4** 激活 `TaskLoopCallback.start_run`(`callback_adapter.py`):由 no-op 升级为 `await engine.on_start(adapt_start(data))`;`report_result` 不变;`TaskLoopCallbackProtocol` 签名不变。
- **T0.5** `core/task/task_runner/callback_correlation.py`:`CorrelationRecord(task_id/node_id/loop_task_id/workflow_id:int/instance_id:int)` + `CallbackCorrelationRegistry` Protocol(`register(...)`/`resolve(source,instance_id_str)->CorrelationRecord|None`,`@runtime_checkable`) + `InMemoryCallbackCorrelationRegistry`(dict+RLock,key=`(source,instance_id_str)`,in-mem 不落库)。
- **T0.6** `adapters/http/task/schemas.py`(Pydantic v2):`TaskCallbackRequest`(task_id/workflow_source:Literal["claw_mind","bcn"]/workflow_id/workflow_instance_id/status/is_success 必填;goal/output/failed_info/ext_info/loop_task_id 可空)、`TaskNodeCallbackRequest(+node_id 必填)`、`CallbackResponse(success/code=200/message="OK")`。
- **T0.7** `adapters/http/task/translator.py`:`translate(req,disposition,registry)->TranslatedCallback(disposition,data:TaskCallbackData)`;`loop_task_id` 解析(回声 > node 直拼 `task::node` > registry > `CallbackCorrelationError`);`workflow_source`→`workflow_type`(`claw_mind`→`single_bot`/`bcn`→`bcn_coop_group`);registry 取 SSOT int id(未登记 node 级回退 0 + str 存 `_ext_info`);`is_success/output/failed_info`→`result{success,data,fail_detail}`;`ext_info/goal`→`result["_ext_info"]`。
- **T0.8** `adapters/http/task/router.py`:`APIRouter(prefix="/task_loop/callback",tags=["task-callback"])` 4 端点(`workflow_start`/`workflow_result`/`node_start`/`node_result`);handler `async def`,经 `_get_svc/_get_auth/_get_registry` 三个 `Depends` provider 取 `Injected(TaskServiceProtocol/CallbackAuthenticator/CallbackCorrelationRegistry)`;`_dispatch`:读 raw body→`model_validate_json`→`auth.verify(source=req.workflow_source,headers,raw_body,method,path)`→`translate`→`start_run`/`report_result`;错误映射:`TaskNotFoundError`/`NodeNotFoundError`→404,`TaskStateError`→result 路径 re-query 已终态→200 idempotent 否则 409,start 路径→409;Pydantic→422。
- **T0.9** `di/modules/task_module.py`:`TaskModule(Module)` singleton 绑 `TaskGraphService`(零参)/`TaskService`(`@inject` 注入 graph,harness 默认 None)/`InMemoryCallbackCorrelationRegistry`/`NoopCallbackAuthenticator`;`@provider` 暴露 `TaskServiceProtocol`/`CallbackCorrelationRegistry`/`CallbackAuthenticator`(镜像 `QualityModule`)。
- **T0.10** `di/profile_modules.py`:`modules_for` TEST/SINGLEBOX column 登记 `TaskModule()`;`adapters/http/app.py` import + `app.include_router(task_callback_router)`(mandatory 块,非 `OptionalRouters`)。
- ✅ 验收:T0.1 架构测试通过;T0.2 `on_start` 5 用例(PENDING→RUNNING/已RUNNING no-op/终态+PLANNING stale/unknown node 404/不触发 drain)绿;T0.3-T0.4 既有 `test_callback_adapter` 更新 `test_start_run_is_noop`→`test_start_run_routes_to_on_start` + `adapt_start`/`_ext_info` 用例绿;T0.5 registry 6 用例(register/resolve/幂等覆盖/source 区分/并发/runtime_checkable)绿;T0.6 schema 5 用例必填校验绿;T0.7 translator 11 用例(loop_task_id 解析 4/字段折叠 5/disposition 2)绿;T0.8 router 10 用例(4 端点 happy + 幂等/409/404/400/422)绿;T0.9 `test_task_module` singleton 解析绿;T0.10 app import 不破、121 既有单测全绿。

## R1 — HMAC 鉴权 + 回声契约 + 错误映射收口
- **T1.1** `adapters/http/task/auth.py`:`CallbackAuthenticator` Protocol(`verify(*,source,headers,raw_body,method,path)->None`,失败 raise `CallbackAuthError`);`HmacCallbackAuthenticator(secrets:Mapping[str,str],max_skew_s=300)`——签串 `f"{timestamp}{method}{path}{body_sha256_hex}"`,头 `X-TaskLoop-Token`/`X-TaskLoop-Timestamp`/`X-TaskLoop-Signature`(`hmac.compare_digest`,不 import ocb);`NoopCallbackAuthenticator`(直通)。
- **T1.2** `loop_task_id` 回声透传契约:派发到 claw_mind/bcn 时(TaskRunner.start_run 真实派发,runner-integration executor/corp adapter)把 `loop_task_id` 作 callback_token 透传给外部引擎;translator 回声优先(回声存在跳过 registry 寻址),缺失 registry 兜底——双保险。**登记动作属 runner spec/corp adapter**,本任务只定「`loop_task_id` 必须可被引擎回带」契约 + registry port(T0.5)。
- **T1.3** 幂等/错误映射收口:router `_dispatch` 的 `TaskStateError` 分支 re-query `svc.get_task_dashboard(task_id)` 取节点当前态,result 路径已终态(`DONE/FAILED/HUNG`)→200 idempotent `CallbackResponse(success=True,message="idempotent")`,否则 409;start 路径 `TaskStateError`→409(`on_start` 已内化 RUNNING no-op);`CallbackAuthError`/`CallbackCorrelationError` 由中央 `@app.exception_handler(DomainError)`→401/400。
- ✅ 验收:`test_auth` 6 用例(verify 通过/坏签名/未知 source/超窗时间戳/body 篡改/Noop 直通)绿;router 幂等/409/401(中央)/400 用例绿;HMAC 实现自包含无 ocb import。

## R2 — singlebox E2E + 零 case + 回归收口(依赖 runner-integration 派发期登记 seam)
- **T2.1** singlebox e2e:`NoopCallbackAuthenticator` + 派发期预登记 `InMemoryCallbackCorrelationRegistry` + 进程内回投;模拟 claw_mind/bcn 回调驱动三态闭环:`workflow_start→workflow_result`(root 节点 RUNNING→DONE→finish)、`node_result` FAIL→补救/BBS 升级、`node_start/node_result` 子节点链路;重投幂等(result 重投不二次传播/finish)。
- **T2.2** 零 case grep 红线:`tests/community/adapters/http/task/test_zero_case.py` 断言 `schemas/translator/auth/router/__init__.py` 0 命中 `N_overview/N_market/N_aggregate/N_verify/N_report/N_practice/n_root/dim_`。
- **T2.3** 全量回归 + pre-push:`tests/community/core/task/` + `tests/community/adapters/http/task/` + `tests/community/di/test_task_module.py` + `tests/community/architecture/` 全绿;pre-push Backend SAST gate 通过(默认 lint-only)。
- ✅ 验收:singlebox e2e 复用既有闭环断言(DONE/FAIL 补救/MISS 升 BBS/dashboard 终态)+ 重投幂等断言;零 case 0 命中;121 既有 + 新增全绿。

## Cross-cutting
- 架构宪法(`docs/arch/arch.rules.md` Rule 7:core transport-free——`CallbackAuthError`/`CallbackCorrelationError` 语义错误落 `core/errors.py`,HTTP 状态仅 `app.py` 映射)。
- CI gates(`docs/arch/ci.enforce.md`)、依赖边界(core 不 import transport;translator/router 可 import core + di)。
- `loop_task_id` mint 在 `TaskRunner.start_run`(runner.py:75 `f"{task.task_id}::{node.node_id}"`);回声透传 + registry 双保险。
- 事件日志/审计位点:`loop_task_id`、`workflow_source`、`workflow_instance_id`、`prev/new_status`(幂等 ack 判定)。

## Risks / 待实现期定的项
- CORP/prod 的 `HmacCallbackAuthenticator`+密钥 wiring(社区只发 Noop;corp adapter 覆写 `CallbackAuthenticator` 绑定 + 真实 `loop_task_id` 回声透传)。
- 派发期 `CallbackCorrelationRegistry.register(...)` 登记动作(runner-integration executor/corp adapter 落地;R2 e2e 依赖)。
- `workflow_id/instance_id` SSOT int vs 羽雀 str:registry 存 SSOT int;未登记 node 级回退 0 + str 存 `ext_info`;`adapt`/`on_report` 不消费(仅信息字段)。
- task 级无 `node_id`:回声 `loop_task_id` 优先;registry `(source,instance_id)→node` 兜底;缺失→400。
- `on_report` 早退 fold-only(`acceptance_result is None`):新增 `on_start`(status-direct)承载 `*_start`;`on_report` 不动。
- `PLANNING` 态收 start:委托态拒绝外部 start→`TaskStateError`→409。
- 持久化:registry in-mem(与 `TaskHarness._dispatched_at` 同级);ORM 适配后续。
- 重投幂等 vs 非法翻转:`TaskStateError` 既覆盖二者,router 按 re-query 节点态区分(已终态→200,非终态→409)。

## 里程碑(R0–R1 本计划交付;R2 依赖 runner-integration)
- ⬜ **R0** 边缘 + 核心骨架(`on_start`/`adapt_start`/registry/schema/translator/router/TaskModule 装配),4 端点 Noop auth 可跑通进程内回投。
- ⬜ **R1** HMAC 鉴权 + `loop_task_id` 回声契约 + 幂等/错误映射收口。
- ⬜ **R2** singlebox E2E(三态闭环 + 重投幂等) + 零 case grep + 全量回归。

> 实现逐任务 TDD 细节见 `plan.md`(每任务含失败测试→实现→通过→commit 可执行步骤);契约依据见 `spec.md`。