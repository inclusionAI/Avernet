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
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult, NodeOpResult, Status, TaskCallbackData, TaskExecutionGraph, TaskNode, TaskNodePatch,
    TaskOpResult, TaskSpec, TaskSummary, TaskType,
)
from agentclaw.community.core.task.domain.requests import TaskInfoRequest
from agentclaw.community.core.task.repository.types import (
    TaskInfoRecord, TaskNodeRecord, TaskNodeRunInfoRecord,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
)

logger = logging.getLogger("task.service")


# TaskService 结构化实现 api.task.task_service.TaskServiceProtocol —— 依 api/README 四层
# 契约,core/ 不 import api/(见 test_service_api_conformance.py:core 服务不继承 api Protocol,
# 由 @runtime_checkable 的 isinstance/issubclass 做结构化一致性校验)。此处置空基类即可。
class TaskService:
    """对外 facade;内部持 ExecutionEngine 编排核 + TaskGraphService + Harness(可选)+ TaskLoopCallback。

    验收 100% 走回调回投;engine 不主动验,无 verify/bbs port。engine 对调用方不可见(无 property)。
    """

    def __init__(self, graph, harness=None, *, bot=None, bcs=None, discover=None, bot_public=None,
                 bcs_identity=None, task_info_repo: TaskInfoRepositoryProtocol | None = None,
                 callback_repo: TaskCallbackRepositoryProtocol | None = None,
                 task_id_provider: Callable[[], str] | None = None,
                 task_node_repo: TaskNodeRepositoryProtocol | None = None,
                 task_node_run_info_repo: TaskNodeRunInfoRepositoryProtocol | None = None,
                 bot_service=None,
                 api_base_url: str | None = None) -> None:
        """graph: TaskGraphService;harness: TaskHarness | None(旁路复位,可选);
        bot/bcs/discover: 传输端口(DI 从配置注入 local/prod/double 实现传给引擎;省略=stub 路径/纯内核单测)。
        bot_public: BotPublicServiceProtocol|None(TEMP e2e)供 bbs_runner 按关键字取 dream bot roster。
        任务模式 roster 圈定的 provider 取自 bcs.provider_id(端口自带凭据),不再单独透传。

        ``task_info_repo``(可选):task_info 持久化协议(DI 在 prod 注入真实实现;``None``
        时 execute 跳过持久化,纯内核/单测路径用)。``callback_repo``(可选):回投落库协议(同上,
        ``None`` 时回投不落 ``task_callback``,纯内核/单测路径用)。``task_id_provider``:task_id 生成器(默认 uuid4;
        测试注入确定性 provider)。``task_node_repo``/``task_node_run_info_repo``(可选):workflow/yaml
        分支落 ``task_node``(RUNNING)+ ``task_node_run_info``(retry=0,run_mode,assignee,session_id,
        start_time)用;``None`` 时跳过持久化(纯内核/单测路径用,与 ``task_info_repo`` 同语义)。"""
        self._graph = graph
        self._harness = harness
        self._bcs_identity = bcs_identity
        self._task_info_repo = task_info_repo
        self._task_id_provider = task_id_provider or (lambda: str(uuid.uuid4()))
        self._task_node_repo = task_node_repo
        self._run_info_repo = task_node_run_info_repo
        self._callback_repo = callback_repo
        self._bot_service = bot_service
        self._api_base_url = api_base_url
        self._engine = self._build_engine(bot=bot, bcs=bcs, discover=discover, bot_public=bot_public)
        # fire-and-forget 后台推进任务跟踪(防 GC + 异常可见 + drain seam)
        self._bg_tasks: set[asyncio.Task] = set()
        # 回投适配层:执行实体 PUSH → 适配 → 编排核 on_report
        self._callback = TaskLoopCallback(CallbackAdapter(), self._engine, callback_repo=callback_repo)
        # harness 复位重投入口回填(编排核已建,harness 才能拿到 on_harness)+ 启动旁路巡检 daemon 线程
        if self._harness is not None:
            self._harness.set_on_harness(self._engine.on_harness)
            import threading as _t
            _t.Thread(target=self._harness.run_poll_loop, daemon=True, name="task-harness").start()
            logger.info("[task-service] harness 旁路巡检线程已启动(SLA 超时/FAILED 重派/PENDING 派发超时重搜推)")

    def _build_engine(self, *, bot=None, bcs=None, discover=None, bot_public=None) -> ExecutionEngine:
        """构造编排核:ExecutionEngine(graph, bot=, bcs=, discover=, bot_public=)。引擎内部 ``_build_*`` new 自带策略 +
        接线 TaskExecutor。测试可经 facade/engine 子类覆写本方法注入 stub 策略/投递的引擎(测试 seam)。"""
        return ExecutionEngine(
            self._graph, bot=bot, bcs=bcs, discover=discover, bot_public=bot_public,
            bcs_identity=self._bcs_identity,
            api_base_url=self._api_base_url,
        )

    @property
    def callback(self) -> TaskLoopCallback:
        """供执行实体(bot workflow / bcn 协作群)PUSH 回投的入口(适配层 → 编排核 on_report)。"""
        return self._callback

    async def execute(self, request: TaskInfoRequest) -> TaskOpResult:
        """提交执行任务:生成 task_id → 持久化 task_info(PENDING)→ initialize_graph →
        后台 on_execute 首帧推进,立即返回 TaskOpResult(含 task_id + run_id)。

        持久化失败(IntegrityError,如 task_id 冲突)→ 返回 success=False,不建图。

        fire-and-forget:on_execute 在后台 asyncio.Task 推进,不阻塞调用方(HTTP 响应秒回);
        长编排(owner bot ``send_and_wait_async`` 分钟级 + dispatch 投递)异步进行,
        调用方经 ``get_task_dashboard`` 轮询观察推进。后台任务异常经 done_callback 记 log
        (不向调用方抛;图停在中间态由 harness 旁路巡检兜底复位)。"""
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
                return TaskOpResult(task_id=task_id, success=False, error=f"persist failed: {exc}")
        graph = self._graph.initialize_graph(task_info)
        logger.info("[execute] task=%s source=%s title=%s → initialize(run_id=%s)+on_execute(后台推进)",
                    task_id, task_info.owner_bot_id,
                    task_info.task_spec.metadata.title, graph.run_id)
        task_type = request.execution_config.get("task_type")
        if task_type == TaskType.WORKFLOW:
            return await self._run_workflow(task_id, request, task_info, graph.run_id)
        if task_type == TaskType.YAML:
            return await self._run_yaml(task_id, request, task_info, graph.run_id)
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
                task_id=task_id, bot_id=request.owner_bot_id, message=message)
        except Exception as exc:
            return TaskOpResult(task_id=task_id, success=False,
                                error=f"workflow trigger failed: {exc}", run_id=run_id)
        session_id = bot_result.session_id if bot_result is not None else None
        run_extend = {"session_id": session_id}
        self._graph.update_task_node_info(TaskNodePatch(
            task_id=task_id, node_id=task_id, status=Status.RUNNING,
            run_mode="single_bot", assignee=request.owner_bot_id,
            extend_props_patch=run_extend))
        self._persist_node_run(task_id, task_info, run_mode="single_bot",
                               assignee=request.owner_bot_id, session_id=session_id,
                               extend_props=run_extend)
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)

    async def _run_yaml(self, task_id, request, task_info, run_id):
        from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
        ec = request.execution_config
        has_yaml = bool(ec.get("yaml"))
        # 任务描述(目标)取 goal.objective → instruction → title 的第一个非空,作为 BCS 建群的 context,
        # 注入 <GroupContext> 的 `目标` 行(BCS resolve_session_topic:session input→group.context→label)。
        _ts = task_info.task_spec
        _task_context = ((_ts.goal.objective or _ts.metadata.instruction or _ts.metadata.title) or "").strip()
        gf = GroupFormation(
            bot_ids=[request.owner_bot_id, *ec.get("participant_bot_ids", [])],
            collab_mode="state_machine" if has_yaml else "manager_worker",
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
                # 任务描述(目标)→ BCS 建群 context → <GroupContext> `目标` 行。
                "task_context": _task_context or None,
            },
        )
        try:
            start = await self._engine.start_coop_group(gf)
        except Exception as exc:
            return TaskOpResult(task_id=task_id, success=False,
                                error=f"yaml group failed: {exc}", run_id=run_id)
        run_extend = {"group_id": start.group_id, "session_id": start.session_id}
        self._graph.update_task_node_info(TaskNodePatch(
            task_id=task_id, node_id=task_id, status=Status.RUNNING,
            run_mode="coop_group", assignee=start.group_id,
            extend_props_patch=run_extend))
        self._persist_node_run(task_id, task_info, run_mode="coop_group",
                               assignee=start.group_id, session_id=start.session_id,
                               extend_props=run_extend)
        extend_props = {"group_id": start.group_id}
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id, extend_props=extend_props)

    def _persist_node_run(self, task_id, task_info, *, run_mode, assignee, session_id,
                          extend_props=None):
        if self._task_node_repo is not None:
            self._task_node_repo.insert(TaskNodeRecord(
                id=0, task_id=task_id, node_id=task_id,
                task_spec=task_info.task_spec.to_dict(), status=Status.RUNNING))
        if self._run_info_repo is not None:
            now_ms = int(time.time() * 1000)
            self._run_info_repo.insert(TaskNodeRunInfoRecord(
                id=0, node_id=task_id, task_id=task_id, run_mode=run_mode, assignee=assignee,
                output=None, acceptance_result=None, retry=0, session_id=session_id,
                extend_props=extend_props, start_time=now_ms, update_time=now_ms, end_time=None))

    async def converge_by_session(self, session_id: str, *, success: bool, output: Any = None) -> bool:
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
            logger.warning("[converge] session_id=%s 在 task_node_run_info 中未找到", session_id)
            return False
        loop_task_id = f"{rec.task_id}::{rec.node_id}"
        result: dict[str, Any] = {"success": success}
        if output is not None:
            result["data"] = output
        data = TaskCallbackData(data={
            "loop_task_id": loop_task_id,
            "workflow_source": "bcn",
            "result": result,
        })
        try:
            await self._callback.report_result(data)
            logger.info("[converge] session_id=%s → loop_task_id=%s success=%s → on_report 收敛已触发",
                        session_id, loop_task_id, success)
            return True
        except Exception as exc:  # noqa: BLE001 收敛失败不阻断落库(回调查询/落库已完成)
            logger.warning("[converge] session_id=%s → on_report 失败: %s", session_id, exc)
            return False

    async def apply_manager_worker_event(self, raw: dict) -> None:
        """manager_worker(BCN 任务协作群)CloudEvent 回调处理。

        parse → merge 进 latest session 行的 ``execution_graph`` → upsert ``task_callback``(单 session 行,
        ``(run_id=session_id, node_id="")``);``session.completed`` → ``converge_by_session`` 收敛整协作。
        非 manager_worker 事件(parse None)→ no-op。幂等:同 session 单行 upsert、重复 ``session.completed``
        重投由 ``converge_by_session → on_report`` 终态幂等吞错兜底。"""
        import json as _json
        from agentclaw.community.adapters.http.task.translator import (
            merge_manager_worker_execution_graph, parse_manager_worker_bcn,
        )
        from agentclaw.community.core.task.repository.types import TaskCallbackRecord

        parsed = parse_manager_worker_bcn(raw)
        if parsed is None:
            return
        sid = parsed.get("session_id") or ""
        et = parsed.get("event_type") or ""
        data = parsed.get("data") or {}
        if sid and self._callback_repo is not None:
            try:
                existing_rec = self._callback_repo.get_latest_by_session(sid)
                existing_graph = (existing_rec.execution_graph
                                  if existing_rec is not None else None)
                merged = merge_manager_worker_execution_graph(existing_graph, parsed)
                rec = TaskCallbackRecord(
                    id=0, invoker="bcn_manager_worker", run_id=sid, node_id="",
                    main_session_id=sid, status=et,
                    orig_callback_data=_json.dumps(raw, ensure_ascii=False, default=str),
                    execution_graph=merged,
                    result=None, result_success=None, exec_error=None,
                    extend_props=({"event_id": parsed.get("event_id")}
                                   if parsed.get("event_id") else None),
                )
                self._callback_repo.upsert(rec)
            except Exception as exc:  # noqa: BLE001 落库失败不阻断收敛
                logger.warning("[manager_worker] upsert task_callback 失败 session_id=%s: %s", sid, exc)
        if et == "session.completed" and sid:
            try:
                success = (data.get("reason") == "completed")
                await self.converge_by_session(sid, success=success, output=data.get("summary"))
            except Exception as exc:  # noqa: BLE001 收敛失败不阻断落库
                logger.warning("[manager_worker] session.completed 收敛失败 session_id=%s: %s", sid, exc)

    def _on_bg_done(self, bg: "asyncio.Task") -> None:
        """后台 on_execute 完成:脱离跟踪集 + 异常可见(记 log,不抛)。"""
        self._bg_tasks.discard(bg)
        if bg.cancelled():
            return
        exc = bg.exception()
        if exc is not None:
            logger.error("[execute] 后台 on_execute 异常: %s", exc, exc_info=exc)

    async def drain_background(self) -> None:
        """await 所有在途后台 on_execute 推进完成。

        fire-and-forget 语义下供测试确定性(等首帧落定后再断言图态)与优雅停机用;
        生产 HTTP 调用方不调用(经 dashboard 观察)。"""
        if not self._bg_tasks:
            return
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)

    def get_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """任务执行详情可视化(整图或按 node_id 子树投影),只读。

        按 root(node_id==task_id)的 ``run_info.extend_props['session_id']`` 反查 ``task_callback`` 最新回调,
        把回调审计的 ``execution_graph``(BCN/ClawMind DAG 快照)挂在图级,便于 dashboard 可见;无 session_id /
        无 callback / 未配 ``callback_repo`` → 留 ``None``。子树投影(node_id 入参)不挂(root 不在投影内)。"""
        graph = self._graph.query_task_dashboard(task_id, node_id)
        root = next((n for n in graph.tasks if n.node_id == task_id), None)
        sid = (root.run_info.extend_props.get("session_id") if root else None)
        if sid and self._callback_repo is not None:
            try:
                rec = self._callback_repo.get_latest_by_session(sid)
            except Exception as exc:  # noqa: BLE001 反查失败不阻断只读 dashboard
                logger.warning("[dashboard] execution_graph 反查失败 session_id=%s: %s", sid, exc)
                rec = None
            if rec is not None and rec.execution_graph is not None:
                graph.execution_graph = rec.execution_graph
        self._attach_assignee_bot_info(graph)
        return graph

    def _attach_assignee_bot_info(self, graph: TaskExecutionGraph) -> None:
        """single_bot / bbs 节点:按 assignee(纯 bot_id)查 ``BotServiceProtocol.get_bot_by_id``,
        把 ``owner_id`` / ``bot_name`` 折进节点 ``run_info.extend_props``(assignee_owner_id / assignee_name;
        只读投影,供前端展示 bot 归属+名)。coop_group(assignee 是 group_id 非机器人)、未配
        ``bot_service``、未命中或异常 → 不写、不阻断 dashboard。同 bot_id 缓存(一次查询)。"""
        if self._bot_service is None:
            return
        cache: dict[str, dict | None] = {}
        for node in graph.tasks:
            if node.run_info.run_mode not in ("single_bot", "bbs"):
                continue
            bot_id = (node.run_info.assignee or "").strip()
            if not bot_id:
                continue
            if bot_id not in cache:
                try:
                    cache[bot_id] = self._bot_service.get_bot_by_id(bot_id)
                except Exception as exc:  # noqa: BLE001 查 bot 失败不阻断只读 dashboard
                    logger.warning("[dashboard] get_bot_by_id 失败 bot_id=%s: %s", bot_id, exc)
                    cache[bot_id] = None
            info = cache.get(bot_id)
            if isinstance(info, dict):
                node.run_info.extend_props["assignee_owner_id"] = info.get("owner_id")
                node.run_info.extend_props["assignee_name"] = info.get("bot_name")

    def list_tasks(
        self,
        status: str | None = None,
        owner_user_id: str | None = None,
    ) -> list[TaskInfoRecord]:
        """列持久化 ``task_info`` 记录,可选按状态和 owner 过滤。"""
        if self._task_info_repo is None:
            return []
        st = Status(status) if status else None
        return self._task_info_repo.list_records(st, owner_user_id=owner_user_id)

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
        self, task_id: str, node_id: str, bot_id: str,
        acceptance_result: AcceptanceResult | None = None,
        output_patch: dict | None = None, exec_error: str | None = None,
    ) -> NodeOpResult:
        """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim(经 ``on_bbs_report``);收口由框架自行判定(非 bot 声明)。

        供 bbs 接力执行实体(FR-PICK-05)回投:``acceptance_result``(PASS→DONE / FAIL+gaps→FAILED)/
        ``output_patch``(checkpoint fold)/``exec_error``(执行报错 fold)。根目标是否满足由框架经 owner
        复核(``on_bbs_report``→``_on_pass_collect``→``plan(root)``→``_maybe_finish_graph``)判定,
        **非 bot 自报**(故无 ``root_verified``)。``bot_id`` 须为当前 ``bbs_owner``(经 on_bbs_report 持有者校验),
        否则 ``TaskStateError``。
        """
        patch = TaskNodePatch(
            task_id=task_id, node_id=node_id, assignee=bot_id,
            acceptance_result=acceptance_result, output_patch=output_patch, exec_error=exec_error,
        )
        return await self._engine.on_bbs_report(patch)


def run_execute(facade: TaskService, request: TaskInfoRequest) -> TaskOpResult:
    """同步执行 ``execute``(无事件循环依赖的调用方/单测用)。"""
    return asyncio.new_event_loop().run_until_complete(facade.execute(request))
