"""TaskService facade:系统唯一对外入口,内部持 ExecutionEngine 编排核。对齐 plan §3.7。

facade 内部 ``_build_engine`` 构造 ExecutionEngine(收传输端口 bot/bcs/discover,由 DI 从配置注入);
引擎 ``_build_*`` 内部 new 引擎自带策略 + 接线 TaskExecutor,无子类化、无外部 reach-in setter。
回投经 ``callback``(TaskLoopCallback)适配层 → 编排核 on_report(非 facade 直暴露)。
engine 对调用方不可见(无 engine property)。测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    NodeOpResult,
    Status,
    TaskCallbackData,
    TaskExecutionGraph,
    TaskNode,
    TaskNodePatch,
    TaskOpResult,
    TaskSourceType,
    TaskSpec,
    TaskType,
)
from agentclaw.community.core.task.domain.requests import (
    RequestContext,
    RequestGoal,
    RequestMetadata,
    RequestTaskSpec,
    TaskInfoRequest,
)
from agentclaw.community.core.task.repository.types import (
    TaskInfoRecord,
    TaskNodeRecord,
    TaskNodeRunInfoRecord,
)
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
)
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin

logger = logging.getLogger("task.service")


def _resolve_coop_collab_mode(has_yaml: bool, group_kind: str | None) -> str:
    """Resolve the BCS collaboration mode from task execution metadata."""
    if has_yaml:
        return "state_machine"
    if group_kind in ("chat", "manager_worker"):
        return group_kind
    if group_kind is None:
        return "manager_worker"
    if group_kind == "state_machine":
        raise ValueError("group_kind=state_machine 需要 yaml 定义")
    raise ValueError(f"未知 group_kind: {group_kind!r}")


# TaskService 结构化实现 api.task.task_service.TaskServiceProtocol —— 依 api/README 四层
# 契约,core/ 不 import api/(见 test_service_api_conformance.py:core 服务不继承 api Protocol,
# 由 @runtime_checkable 的 isinstance/issubclass 做结构化一致性校验)。此处置空基类即可。
class TaskService:
    """对外 facade;内部持 ExecutionEngine 编排核 + TaskGraphService + Harness(可选)+ TaskLoopCallback。

    验收 100% 走回调回投;engine 不主动验,无 verify/bbs port。engine 对调用方不可见(无 property)。
    """

    def __init__(
        self,
        graph,
        harness=None,
        *,
        bot=None,
        bcs=None,
        discover=None,
        bcn: BcnService | None = None,
        bcs_identity=None,
        task_info_repo: TaskInfoRepositoryProtocol | None = None,
        callback_repo: TaskCallbackRepositoryProtocol | None = None,
        task_id_provider: Callable[[], str] | None = None,
        task_node_repo: TaskNodeRepositoryProtocol | None = None,
        task_node_run_info_repo: TaskNodeRunInfoRepositoryProtocol | None = None,
        bot_service=None,
        staff_dept: StaffDeptPlugin | None = None,
        task_auth_gate=None,
        task_search_skill_enabled: bool = False,
        task_settings=None,
        api_base_url: str | None = None,
        bot_token_provider=None,
        notify_messages_provider=None,
    ) -> None:
        """graph: TaskGraphService;harness: TaskHarness | None(旁路复位,可选);
        bot/bcs/discover: 传输端口(DI 从配置注入 local/prod/double 实现传给引擎;省略=stub 路径/纯内核单测)。
        BBS 候选通过注入的 BcnService.list_bots_by_task_modes(复用统一 provider 身份)查询。

        ``task_info_repo``(可选):task_info 持久化协议(DI 在 prod 注入真实实现;``None``
        时 execute 跳过持久化,纯内核/单测路径用)。``callback_repo``(可选):回投落库协议(同上,
        ``None`` 时回投不落 ``task_callback``,纯内核/单测路径用)。``task_id_provider``:task_id 生成器(默认 uuid4;
        测试注入确定性 provider)。``task_node_repo``/``task_node_run_info_repo``(可选):workflow/yaml
        分支落 ``task_node``(RUNNING)+ ``task_node_run_info``(retry=0,run_mode,assignee,session_id,
        start_time)用;``None`` 时跳过持久化(纯内核/单测路径用,与 ``task_info_repo`` 同语义)。"""
        self._graph = graph
        self._harness = harness
        self._bcn = bcn
        self._bcs_identity = bcs_identity
        self._task_info_repo = task_info_repo
        self._task_id_provider = task_id_provider or (lambda: str(uuid.uuid4()))
        self._task_node_repo = task_node_repo
        self._run_info_repo = task_node_run_info_repo
        self._callback_repo = callback_repo
        self._bot_service = bot_service
        self._staff_dept = staff_dept
        self._api_base_url = api_base_url
        self._task_auth_gate = task_auth_gate
        self._task_search_skill_enabled = task_search_skill_enabled
        self._task_settings = task_settings
        self._bot_token_provider = bot_token_provider
        self._notify_provider = notify_messages_provider
        # _build_engine(seam)签名保持不变(测试子类按旧签名覆写);claim_on JOIN 经 self._task_auth_gate
        # 传入 ExecutionEngine→dispatcher,不进签名避免破坏覆写 seam。
        self._engine = self._build_engine(bot=bot, bcs=bcs, discover=discover)
        # fire-and-forget 后台推进任务跟踪(防 GC + 异常可见 + drain seam)
        self._bg_tasks: set[asyncio.Task] = set()
        # 回投适配层:执行实体 PUSH → 适配 → 编排核 on_report
        self._callback = TaskLoopCallback(
            CallbackAdapter(), self._engine, callback_repo=callback_repo
        )
        # harness 复位重投入口回填(编排核已建,harness 才能拿到 on_harness)+ 启动旁路巡检 daemon 线程
        if self._harness is not None:
            self._harness.set_on_harness(self._engine.on_harness)
            import threading as _t

            _t.Thread(
                target=self._harness.run_poll_loop, daemon=True, name="task-harness"
            ).start()
            logger.info(
                "[task][task-service] harness 旁路巡检线程已启动(SLA 超时/FAILED 重派/PENDING 派发超时重搜推)"
            )

    def _build_engine(self, *, bot=None, bcs=None, discover=None) -> ExecutionEngine:
        """构造编排核:ExecutionEngine(graph, bot=, bcs=, discover=)。引擎内部 ``_build_*`` new 自带策略 +
        接线 TaskExecutor。测试可经 facade/engine 子类覆写本方法注入 stub 策略/投递的引擎(测试 seam)。

        claim_on JOIN 开关经实例属性 ``self._task_auth_gate`` 传入 ExecutionEngine→dispatcher(strategies),
        不进本方法签名(保持覆写 seam 向后兼容);None/community 路径派发不做 claim_on 交集。"""
        return ExecutionEngine(
            self._graph,
            bot=bot,
            bcs=bcs,
            discover=discover,
            bcn=self._bcn,
            bcs_identity=self._bcs_identity,
            auth_gate=self._task_auth_gate,
            task_search_skill_enabled=self._task_search_skill_enabled,
            task_settings=self._task_settings,
            api_base_url=self._api_base_url,
            bot_token_provider=self._bot_token_provider,
            notify_messages_provider=self._notify_provider,
        )

    async def run_template(self, template_id: str, inputs: dict[str, Any], *,
                           owner_user_id: str, owner_bot_id: str,
                           auto_advance: bool | None = None) -> TaskOpResult:
        """Load and validate a static template, then enter the existing execute path."""
        from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition
        logger.info(
            "[task][template-run] start template=%s owner_bot_id=%s input_keys=%s",
            template_id,
            owner_bot_id,
            sorted(inputs),
        )
        template_dir = Path(__file__).resolve().parents[3] / "configs" / "task-plans"
        template_path = template_dir / f"{template_id}.yaml"
        try:
            definition = StaticPlanDefinition.from_file(template_id, template_dir)
            definition.validate_input(inputs)
            definition.validate_bindings()
        except Exception as exc:
            logger.exception(
                "[task][template-run] validation failed template=%s exc_type=%s",
                template_id,
                type(exc).__name__,
            )
            raise
        logger.info(
            "[task][template-run] validated template=%s nodes=%s entry_bot_id=%s",
            template_id,
            [node.node_id for node in definition.nodes],
            definition.entry_bot_id,
        )
        plan_yaml = template_path.read_text(encoding="utf-8")
        request = TaskInfoRequest(
            task_spec=RequestTaskSpec(
                metadata=RequestMetadata(
                    title=template_id,
                    instruction=f"运行静态模板 {template_id}",
                ),
                context=RequestContext(
                    background="",
                    extend_props={"template_input": dict(inputs)},
                ),
                goal=RequestGoal(objective=template_id),
            ),
            source_type=TaskSourceType.API,
            owner_user_id=owner_user_id,
            owner_bot_id=definition.entry_bot_id or owner_bot_id,
            execution_config={
                "task_type": TaskType.STATIC_PLAN,
                "static_plan_id": template_id,
                "static_plan_yaml": plan_yaml,
                "template_input": dict(inputs),
                "static_auto_report": auto_advance,
            },
        )
        result = await self.execute(request)
        logger.info(
            "[task][template-run] submitted template=%s task=%s success=%s run_id=%s error=%s",
            template_id,
            result.task_id,
            result.success,
            result.run_id,
            result.error,
        )
        return result

    @property
    def callback(self) -> TaskLoopCallback:
        """供执行实体(bot workflow / bcn 协作群)PUSH 回投的入口(适配层 → 编排核 on_report)。"""
        return self._callback

    @staticmethod
    def _normalize_owner_bot_id(request: TaskInfoRequest) -> TaskInfoRequest:
        """Keep new task_info rows semantically split while accepting legacy composite input."""
        bot_id, separator, embedded_owner_id = str(
            request.owner_bot_id or ""
        ).partition(":")
        if not separator:
            return request
        owner_user_id = request.owner_user_id or embedded_owner_id
        if (
            request.owner_user_id
            and embedded_owner_id
            and request.owner_user_id != embedded_owner_id
        ):
            logger.warning(
                "[task][execute] owner identity mismatch: owner_bot_id=%s owner_user_id=%s; "
                "keep explicit owner_user_id",
                request.owner_bot_id,
                request.owner_user_id,
            )
        return replace(request, owner_bot_id=bot_id, owner_user_id=owner_user_id)

    async def execute(self, request: TaskInfoRequest) -> TaskOpResult:
        """提交执行任务:生成 task_id → 持久化 task_info(PENDING)→ initialize_graph →
        后台 on_execute 首帧推进,立即返回 TaskOpResult(含 task_id + run_id)。

        持久化失败(IntegrityError,如 task_id 冲突)→ 返回 success=False,不建图。

        fire-and-forget:on_execute 在后台 asyncio.Task 推进,不阻塞调用方(HTTP 响应秒回);
        长编排(owner bot ``send_and_wait_async`` 分钟级 + dispatch 投递)异步进行,
        调用方经 ``get_task_dashboard`` 轮询观察推进。后台任务异常经 done_callback 记 log
        (不向调用方抛;图停在中间态由 harness 旁路巡检兜底复位)。"""
        request = self._normalize_owner_bot_id(request)
        task_id = self._task_id_provider()
        task_info = request.to_task_info(task_id)
        if self._task_info_repo is not None:
            record = TaskInfoRecord(
                id=0,
                task_id=task_id,
                source_type=request.source_type.value,
                owner_user_id=request.owner_user_id,
                owner_bot_id=request.owner_bot_id,
                execution_config=dict(request.execution_config),
                task_spec=task_info.task_spec.to_dict(),
                status=Status.PENDING,
            )
            try:
                self._task_info_repo.insert(record)
            except IntegrityError as exc:
                return TaskOpResult(
                    task_id=task_id, success=False, error=f"persist failed: {exc}"
                )
        graph = self._graph.initialize_graph(task_info)
        logger.info(
            "[task][execute] task=%s source=%s title=%s → initialize(run_id=%s)+on_execute(后台推进)",
            task_id,
            task_info.owner_bot_id,
            task_info.task_spec.metadata.title,
            graph.run_id,
        )
        task_type = request.execution_config.get("task_type")
        if task_type == TaskType.WORKFLOW:
            return await self._run_workflow(task_id, request, task_info, graph.run_id)
        if task_type == TaskType.YAML:
            return await self._run_yaml(task_id, request, task_info, graph.run_id)
        if task_type == TaskType.STATIC_PLAN:
            if self._harness is not None:
                self._harness.register(task_id)
            bg = asyncio.create_task(self._engine.on_execute(task_id))
            self._bg_tasks.add(bg)
            bg.add_done_callback(self._on_bg_done)
            return TaskOpResult(task_id=task_id, success=True, run_id=graph.run_id)
        # dynamic (default): fire-and-forget on_execute
        if self._harness is not None:
            self._harness.register(task_id)
        bg = asyncio.create_task(self._engine.on_execute(task_id))
        self._bg_tasks.add(bg)
        bg.add_done_callback(self._on_bg_done)
        return TaskOpResult(task_id=task_id, success=True, run_id=graph.run_id)

    async def _run_workflow(self, task_id, request, task_info, run_id):
        ec = request.execution_config
        wf_id = ec.get("workflow_id")
        args = ec.get("args", [])
        message = f"/{wf_id} " + " ".join(args) if wf_id else " ".join(args)
        try:
            bot_result = await self._engine.trigger_single_bot_workflow(
                task_id=task_id,
                bot_id=compose_bot_identity(request.owner_bot_id, request.owner_user_id),
                message=message,
            )
        except Exception as exc:
            return TaskOpResult(
                task_id=task_id,
                success=False,
                error=f"workflow trigger failed: {exc}",
                run_id=run_id,
            )
        session_id = bot_result.session_id if bot_result is not None else None
        # Keep the graph identity semantically split.  ``assignee`` remains the
        # product bot id, while the owner is persisted alongside it so dashboard
        # reads never resolve a duplicate bot name under another user.
        run_extend = {
            "session_id": session_id,
            "assignee_owner_id": request.owner_user_id,
        }
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                status=Status.RUNNING,
                run_mode="single_bot",
                assignee=request.owner_bot_id,
                extend_props_patch=run_extend,
            )
        )
        self._persist_node_run(
            task_id,
            task_info,
            run_mode="single_bot",
            assignee=request.owner_bot_id,
            session_id=session_id,
            extend_props=run_extend,
        )
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)

    async def _run_yaml(self, task_id, request, task_info, run_id):
        from agentclaw.community.core.task.task_dispatch.strategies import (
            GroupFormation,
        )

        ec = request.execution_config
        has_yaml = bool(ec.get("yaml"))
        # 任务描述(目标)取 goal.objective → instruction → title 的第一个非空,作为 BCS 建群的 context,
        # 注入 <GroupContext> 的 `目标` 行(BCS resolve_session_topic:session input→group.context→label)。
        _ts = task_info.task_spec
        _task_context = (
            (_ts.goal.objective or _ts.metadata.instruction or _ts.metadata.title) or ""
        ).strip()
        gf = GroupFormation(
            bot_ids=[
                compose_bot_identity(request.owner_bot_id, request.owner_user_id),
                *ec.get("participant_bot_ids", []),
            ],
            collab_mode=_resolve_coop_collab_mode(has_yaml, ec.get("group_kind")),
            group_name=ec.get("group_name", f"task-{task_id}"),
            members_info=[],
            extend_props={
                "definition_yaml": ec.get("yaml"),
                "task_id": task_id,
                "api_base_url": self._api_base_url,
                # YAML 路径不手动 start_state_machine_run:让 BCS 建群即自动开跑初始状态机。
                "start_initial_run": True,
                # 逻辑角色→产品 bot 绑定为创建 bcn 协作群接口的入参(非 yaml 模板内字段):
                # 经 execution_config 透传 → TaskExecutor.form_coop_group 注入 BCS create_group
                # (state_machine participant_bindings)。群 master 复用底层 driver_bot(bot_ids[0]=owner)。
                "participant_bindings": ec.get("participant_bindings"),
                # state_machine 面板组件由 execute.execution_config 指定，透传给 BCS。
                "panel_component_name": ec.get("panel_component_name"),
                # 任务描述(目标)→ BCS 建群 context → <GroupContext> `目标` 行。
                "task_context": _task_context or None,
                "task_objective": task_info.task_spec.goal.objective,
                "task_instruction": task_info.task_spec.metadata.instruction,
                "acceptances": [
                    {"id": a.id, "description": a.description}
                    for a in task_info.task_spec.goal.acceptances
                ],
            },
        )
        try:
            start = await self._engine.start_coop_group(gf)
        except Exception as exc:
            return TaskOpResult(
                task_id=task_id,
                success=False,
                error=f"yaml group failed: {exc}",
                run_id=run_id,
            )
        run_extend = {"group_id": start.group_id, "session_id": start.session_id}
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                status=Status.RUNNING,
                run_mode="coop_group",
                assignee=start.group_id,
                extend_props_patch=run_extend,
            )
        )
        self._persist_node_run(
            task_id,
            task_info,
            run_mode="coop_group",
            assignee=start.group_id,
            session_id=start.session_id,
            extend_props=run_extend,
        )
        extend_props = {"group_id": start.group_id}
        return TaskOpResult(
            task_id=task_id, success=True, run_id=run_id, extend_props=extend_props
        )

    def _persist_node_run(
        self, task_id, task_info, *, run_mode, assignee, session_id, extend_props=None
    ):
        # The aggregate graph repository already persists the node and runtime
        # snapshot from the preceding graph mutation. Avoid duplicate inserts
        # when the shared persistence path is enabled; retain the legacy direct
        # repositories for lightweight/in-memory fixtures.
        if self._graph.has_repository:
            return
        if self._task_node_repo is not None:
            self._task_node_repo.insert(
                TaskNodeRecord(
                    id=0,
                    task_id=task_id,
                    node_id=task_id,
                    task_spec=task_info.task_spec.to_dict(),
                    status=Status.RUNNING,
                )
            )
        if self._run_info_repo is not None:
            now_ms = int(time.time() * 1000)
            self._run_info_repo.insert(
                TaskNodeRunInfoRecord(
                    id=0,
                    node_id=task_id,
                    task_id=task_id,
                    run_mode=run_mode,
                    assignee=assignee,
                    output=None,
                    acceptance_result=None,
                    retry=0,
                    session_id=session_id,
                    extend_props=extend_props,
                    start_time=now_ms,
                    update_time=now_ms,
                    end_time=None,
                )
            )

    async def converge_by_session(
        self, session_id: str, *, success: bool, output: Any = None
    ) -> bool:
        """BCN/ClawMind 终态回调后收敛:按 ``session_id`` 查 ``task_node_run_info`` → 框架
        ``(task_id, node_id)`` → 构造 ``TaskCallbackData`` (``loop_task_id``)→ ``report_result``
        → ``on_report`` → 翻态(引擎验收 + 传播 + 根收敛)。

        ``session_id`` = BCN 回调 ``scope.session_id`` / ClawMind ``ext_info.flow_runs.origin_session_id``,
        与 ``task_node_run_info.session_id`` (派发时由 ``_persist_node_run`` 写入 BCS session) 同源,
        据此反查框架节点 → 走标准 ``on_report`` 收敛(非直接改图)。"""
        if not session_id or self._run_info_repo is None:
            return False
        rec = self._run_info_repo.get_by_session_id(session_id)
        if rec is None:
            logger.warning(
                "[task][converge] session_id=%s 在 task_node_run_info 中未找到",
                session_id,
            )
            return False
        loop_task_id = f"{rec.task_id}::{rec.node_id}"
        result: dict[str, Any] = {"success": success}
        if output is not None:
            result["data"] = output
        if not success:
            # 失败收敛须带 gaps:CallbackAdapter.adapt 对"success=False 且无 gaps"会产出
            # exec_error(→ harness 重投),而非验收 FAIL(→ 终态 FAILED)。补一个 gap 让失败
            # 正确翻态为 FAILED(失败详情已随 output 落 run_info.output)。
            result["gaps"] = ["external collaboration ended without success"]
        data = TaskCallbackData(
            data={
                "loop_task_id": loop_task_id,
                "workflow_source": "bcn",
                "result": result,
            }
        )
        try:
            await self._callback.report_result(data)
            logger.info(
                "[task][converge] session_id=%s → loop_task_id=%s success=%s → on_report 收敛已触发",
                session_id,
                loop_task_id,
                success,
            )
            return True
        except Exception as exc:  # noqa: BLE001 收敛失败不阻断落库(回调查询/落库已完成)
            logger.warning(
                "[task][converge] session_id=%s → on_report 失败: %s", session_id, exc
            )
            return False

    async def apply_manager_worker_event(self, raw: dict) -> None:
        """manager_worker(BCN 任务协作群)CloudEvent 回调处理。

        parse → merge 进 latest session 行的 ``execution_graph`` → upsert ``task_callback``(单 session 行,
        ``(run_id=session_id, node_id="")``);``session.completed`` → ``converge_by_session`` 收敛整协作。
        非 manager_worker 事件(parse None)→ no-op。幂等:同 session 单行 upsert、重复 ``session.completed``
        重投由 ``converge_by_session → on_report`` 终态幂等吞错兜底。"""
        import json as _json
        from agentclaw.community.adapters.http.task.translator import (
            merge_manager_worker_execution_graph,
            parse_manager_worker_bcn,
        )
        from agentclaw.community.core.task.repository.types import TaskCallbackRecord
        from agentclaw.community.core.task.task_runner.integration.callback_data_enricher import (
            _manager_worker_status,
        )

        parsed = parse_manager_worker_bcn(raw)
        if parsed is None:
            return
        sid = parsed.get("session_id") or ""
        et = parsed.get("event_type") or ""
        data = parsed.get("data") or {}
        if sid and self._callback_repo is not None:
            try:
                existing_rec = self._callback_repo.get_latest_by_session(sid)
                existing_graph = (
                    existing_rec.execution_graph if existing_rec is not None else None
                )
                merged = merge_manager_worker_execution_graph(existing_graph, parsed)
                rec = TaskCallbackRecord(
                    id=0,
                    invoker="bcn_manager_worker",
                    run_id=sid,
                    node_id="",
                    main_session_id=sid,
                    # 回调行 status 按 manager_worker 事件映射到 Status 枚举(对齐 state_machine):
                    # 终态事件 task.completed/session.completed→DONE,其余→RUNNING;
                    # 真终态 DONE/FAILED 由 session.completed 的 converge_by_session(data.reason)收敛。
                    status=_manager_worker_status(et).value,
                    orig_callback_data=_json.dumps(
                        raw, ensure_ascii=False, default=str
                    ),
                    execution_graph=merged,
                    result=None,
                    result_success=None,
                    exec_error=None,
                    extend_props=(
                        {"event_id": parsed.get("event_id")}
                        if parsed.get("event_id")
                        else None
                    ),
                )
                self._callback_repo.upsert(rec)
            except Exception as exc:  # noqa: BLE001 落库失败不阻断收敛
                logger.warning(
                    "[task][manager_worker] upsert task_callback 失败 session_id=%s: %s",
                    sid,
                    exc,
                )
        if et == "session.completed" and sid:
            try:
                success = data.get("reason") == "completed"
                await self.converge_by_session(
                    sid, success=success, output=data.get("summary")
                )
            except Exception as exc:  # noqa: BLE001 收敛失败不阻断落库
                logger.warning(
                    "[task][manager_worker] session.completed 收敛失败 session_id=%s: %s",
                    sid,
                    exc,
                )

    def _on_bg_done(self, bg: "asyncio.Task") -> None:
        """后台 on_execute 完成:脱离跟踪集 + 异常可见(记 log,不抛)。"""
        self._bg_tasks.discard(bg)
        if bg.cancelled():
            return
        exc = bg.exception()
        if exc is not None:
            logger.error("[task][execute] 后台 on_execute 异常: %s", exc, exc_info=exc)

    async def redrive_task(self, task_id: str) -> None:
        """Recovery resume:重投一个已 hydrate 的非终态任务(实例重启 / 滚动发布后)。

        走 ``ExecutionEngine.redrive`` 重派 PENDING 叶节点;终态图冻结。幂等:派发飞行态/
        状态机卫重复。recovery worker 在取得租约后调此方法。"""
        bg = asyncio.create_task(self._engine.redrive(task_id))
        self._bg_tasks.add(bg)
        bg.add_done_callback(self._on_bg_done)
        logger.info("[task][redrive] task=%s 后台 redrive 已调度", task_id)

    async def drain_background(self) -> None:
        """await 所有在途后台 on_execute 推进完成。

        fire-and-forget 语义下供测试确定性(等首帧落定后再断言图态)与优雅停机用;
        生产 HTTP 调用方不调用(经 dashboard 观察)。"""
        if not self._bg_tasks:
            return
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)

    def get_task_dashboard(
        self,
        task_id: str,
        node_id: str | None = None,
        *,
        include_action_log: bool = False,
    ) -> TaskExecutionGraph:
        """任务执行详情可视化(整图或按 node_id 子树投影),只读。

        按 root(node_id==task_id)的 ``run_info.extend_props['session_id']`` 反查 ``task_callback`` 最新回调,
        把回调审计的 ``execution_graph``(BCN/ClawMind DAG 快照)挂在图级,便于 dashboard 可见;无 session_id /
        无 callback / 未配 ``callback_repo`` → 留 ``None``。子树投影(node_id 入参)不挂(root 不在投影内)。"""
        graph = self._graph.query_task_dashboard(task_id, node_id)
        if include_action_log:
            self._graph.load_action_logs(graph)
        root = next((n for n in graph.tasks if n.node_id == task_id), None)
        sid = root.run_info.extend_props.get("session_id") if root else None
        if sid and self._callback_repo is not None:
            try:
                rec = self._callback_repo.get_latest_by_session(sid)
            except Exception as exc:  # noqa: BLE001 反查失败不阻断只读 dashboard
                logger.warning(
                    "[task][dashboard] execution_graph 反查失败 session_id=%s: %s",
                    sid,
                    exc,
                )
                rec = None
            if rec is not None and rec.execution_graph is not None:
                graph.execution_graph = rec.execution_graph
        self._attach_assignee_bot_info(graph)
        return graph

    def _attach_assignee_bot_info(self, graph: TaskExecutionGraph) -> None:
        """Attach exact Bot/owner display metadata to single-bot nodes.

        New execution rows carry ``assignee_owner_id`` separately.  When it is
        available, resolve the ``(bot_id, owner_id)`` pair instead of the
        ambiguous bot id alone.  Composite legacy assignees are split in place.
        Old rows without owner metadata retain the historical best-effort lookup
        for compatibility, but new workflow rows never take that path.
        """
        if self._bot_service is None:
            return
        pair_cache: dict[tuple[str, str], dict | None] = {}
        bot_cache: dict[str, dict | None] = {}
        pair_lookup = getattr(self._bot_service, "list_bots_by_owner_bot_pairs", None)
        for node in graph.tasks:
            if node.run_info.run_mode not in ("single_bot", "bbs"):
                continue
            assignee = (node.run_info.assignee or "").strip()
            if not assignee:
                continue
            bot_id, composite_owner_id = self._split_owner_bot_id(assignee, "")
            owner_id = str(
                node.run_info.extend_props.get("assignee_owner_id")
                or composite_owner_id
                or ""
            ).strip()
            info: dict | None = None
            if owner_id and callable(pair_lookup):
                key = (bot_id, owner_id)
                if key not in pair_cache:
                    try:
                        result = pair_lookup(pairs=[key], page=1, page_size=1) or {}
                        items = result.get("items") or []
                        pair_cache[key] = items[0] if items else None
                    except Exception as exc:  # noqa: BLE001 display-only enrichment
                        logger.warning(
                            "[task][dashboard] exact bot lookup failed bot_id=%s owner_id=%s: %s",
                            bot_id,
                            owner_id,
                            exc,
                        )
                        pair_cache[key] = None
                info = pair_cache[key]
            elif not owner_id:
                # Compatibility for old graph rows that predate split identity
                # fields.  New rows always persist the owner and use the exact
                # pair branch above.
                if bot_id not in bot_cache:
                    try:
                        bot_cache[bot_id] = self._bot_service.get_bot_by_id(bot_id)
                    except Exception as exc:  # noqa: BLE001 display-only enrichment
                        logger.warning(
                            "[task][dashboard] get_bot_by_id failed bot_id=%s: %s",
                            bot_id,
                            exc,
                        )
                        bot_cache[bot_id] = None
                info = bot_cache[bot_id]
            if isinstance(info, dict):
                node.run_info.extend_props["assignee_owner_id"] = info.get("owner_id")
                node.run_info.extend_props["assignee_name"] = info.get("bot_name")

    @staticmethod
    def _split_owner_bot_id(owner_bot_id: str, owner_user_id: str) -> tuple[str, str]:
        """Normalize legacy ``bot_id:owner_id`` storage without writing it back."""
        bot_id, separator, embedded_owner_id = str(owner_bot_id or "").partition(":")
        effective_owner_id = (
            embedded_owner_id if separator and embedded_owner_id else owner_user_id
        )
        return bot_id, effective_owner_id

    def _enrich_task_owner_display(
        self, records: list[TaskInfoRecord]
    ) -> list[TaskInfoRecord]:
        """Return list records with normalized owner IDs and best-effort names.

        Name lookup is display enrichment only. Missing optional ports, missing
        records, and lookup failures produce ``None`` and never fail the list API.
        """
        if not records:
            return []

        normalized: list[tuple[TaskInfoRecord, str, str]] = []
        for record in records:
            bot_id, owner_id = self._split_owner_bot_id(
                record.owner_bot_id, record.owner_user_id
            )
            normalized.append((record, bot_id, owner_id))

        bot_names: dict[tuple[str, str], str | None] = {}
        if self._bot_service is not None:
            pairs = list(
                dict.fromkeys(
                    (bot_id, owner_id)
                    for _, bot_id, owner_id in normalized
                    if bot_id and owner_id
                )
            )
            if pairs:
                try:
                    result = (
                        self._bot_service.list_bots_by_owner_bot_pairs(
                            pairs=pairs, page=1, page_size=len(pairs)
                        )
                        or {}
                    )
                    for item in result.get("items") or []:
                        if not isinstance(item, dict):
                            continue
                        key = (
                            str(item.get("bot_id") or ""),
                            str(item.get("owner_id") or ""),
                        )
                        if key[0] and key[1]:
                            bot_names[key] = item.get("bot_name")
                except Exception as exc:  # noqa: BLE001 display enrichment only
                    logger.warning("[task][list] owner bot name lookup failed: %s", exc)

        user_names: dict[str, str | None] = {}
        if self._staff_dept is not None:
            for _, _, owner_id in normalized:
                if not owner_id or owner_id in user_names:
                    continue
                try:
                    profile = self._staff_dept.get_profile_by_work_no(work_no=owner_id)
                    user_names[owner_id] = getattr(profile, "nick_name", None)
                except Exception as exc:  # noqa: BLE001 display enrichment only
                    logger.warning(
                        "[task][list] owner user name lookup failed user_id=%s: %s",
                        owner_id,
                        exc,
                    )
                    user_names[owner_id] = None

        return [
            replace(
                record,
                owner_bot_id=bot_id,
                owner_user_id=owner_id,
                owner_bot_name=bot_names.get((bot_id, owner_id)),
                owner_user_name=user_names.get(owner_id),
            )
            for record, bot_id, owner_id in normalized
        ]

    def list_tasks(
        self,
        status: str | None = None,
        owner_user_id: str | None = None,
    ) -> list[TaskInfoRecord]:
        """列持久化 ``task_info`` 记录,可选按状态和 owner 过滤。"""
        if self._task_info_repo is None:
            return []
        st = Status(status) if status else None
        records = self._task_info_repo.list_records(st, owner_user_id=owner_user_id)
        return self._enrich_task_owner_display(records)

    def list_tasks_page(
        self,
        status: str | None = None,
        owner_user_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskInfoRecord], int]:
        """列持久化 ``task_info`` 记录的一页(1-based),可选按状态和 owner 过滤。"""
        if self._task_info_repo is None:
            return [], 0
        st = Status(status) if status else None
        records, total = self._task_info_repo.list_records_page(
            st, owner_user_id=owner_user_id, page=page, page_size=page_size
        )
        return self._enrich_task_owner_display(records), total

    def claim_bbs_task(self, task_id: str, bot_id: str) -> NodeOpResult:
        """BBS 接力步②:任务根级 CAS 占有(委托 TaskGraphService.claim_bbs_owner)。

        供 bbs/claim 路由(FR-PICK-02)调用:恰一赢,输者/非 bbs 任务 → TaskStateError。
        """
        return self._graph.claim_bbs_owner(task_id, bot_id)

    def attach_bbs_node(
        self, task_id: str, parent_node_id: str, task_spec: TaskSpec, bot_id: str
    ) -> TaskNode:
        """BBS 接力步④:在 parent 下挂 run_mode=bbs scoped 节点 + PENDING→RUNNING(create+start 合一)。

        供 bbs 接力执行实体(FR-PICK-04)调用,委托 TaskGraphService.attach_bbs_node:
        owner 校验 + 深度闸 + 翻 RUNNING + bbs_relay_count++。
        """
        return self._graph.attach_bbs_node(task_id, parent_node_id, task_spec, bot_id)

    async def report_bbs_result(
        self,
        task_id: str,
        node_id: str,
        bot_id: str,
        acceptance_result: AcceptanceResult | None = None,
        output_patch: dict | None = None,
        exec_error: str | None = None,
    ) -> NodeOpResult:
        """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim(经 ``on_bbs_report``);收口由框架自行判定(非 bot 声明)。

        供 bbs 接力执行实体(FR-PICK-05)回投:``acceptance_result``(PASS→DONE / FAIL+gaps→FAILED)/
        ``output_patch``(checkpoint fold)/``exec_error``(执行报错 fold)。根目标是否满足由框架经 owner
        复核(``on_bbs_report``→``_on_pass_collect``→``plan(root)``→``_maybe_finish_graph``)判定,
        **非 bot 自报**(故无 ``root_verified``)。``bot_id`` 须为当前 ``bbs_owner``(经 on_bbs_report 持有者校验),
        否则 ``TaskStateError``。
        """
        patch = TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            assignee=bot_id,
            acceptance_result=acceptance_result,
            output_patch=output_patch,
            exec_error=exec_error,
        )
        return await self._engine.on_bbs_report(patch)


def run_execute(facade: TaskService, request: TaskInfoRequest) -> TaskOpResult:
    """同步执行 ``execute``(无事件循环依赖的调用方/单测用)。"""
    return asyncio.new_event_loop().run_until_complete(facade.execute(request))
