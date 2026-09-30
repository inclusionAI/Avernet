"""Execution-mode branches extracted from :mod:`task_service`."""
from __future__ import annotations

import logging
import time

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskNode,
    TaskNodePatch,
    TaskOpResult,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import (
    TaskNodeRecord,
    TaskNodeRunInfoRecord,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_center.task_service_support import (
    resolve_coop_collab_mode,
)

logger = logging.getLogger("task.service")


class TaskServiceExecutionMixin:
    """Workflow/YAML/BBS execution entry branches for ``TaskService``."""

    async def _run_workflow(self, task_id, request, task_info, run_id):
        ec = request.execution_config
        wf_id = ec.get("workflow_id")
        args = ec.get("args", [])
        message = f"/{wf_id} " + " ".join(args) if wf_id else " ".join(args)

        logger.error("[task][task_service] run_workflow, message=%s", message)
        try:
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=task_id,
                    status=Status.RUNNING,
                    run_mode="single_bot",
                    assignee=request.owner_bot_id,
                    extend_props_patch={},
                )
            )

            task_node = TaskNode(
                node_id=task_id,
                task_id=task_id,
                status=Status.RUNNING,
                task_spec=TaskSpec(
                    metadata=Metadata(
                        task_id=task_id,
                        title=message,
                        instruction=""
                    ),
                    context=Context(
                        background="",
                        extend_props={}
                    ),
                    goal=Goal(
                        objective=message,
                        acceptances=list()
                    )
                ),
                run_info=RuntimeInfo(
                    run_mode="single_bot",
                    assignee=request.owner_bot_id,
                    extend_props={
                        "assignee_owner_id": request.owner_user_id
                    }
                ),
                node_run_graph=None
            )

            logger.error("[task][task_service] run_workflow_begin, task_id=%s", task_id)
            await self._engine._runner.start_run([task_node])
            logger.error("[task][task_service] run_workflow_end, task_id=%s", task_id)

            return TaskOpResult(task_id=task_id, success=True, run_id=run_id)
        except Exception as exc:
            logger.error("[task][task_service] run_workflow_meet_exception, %s", exc)
            return TaskOpResult(
                task_id=task_id,
                success=False,
                error=f"workflow trigger failed: {exc}",
                run_id=run_id,
            )

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
        # owner bot 寻址:_normalize 已在"归属≠执行用户"时保留 owner_bot_id 复合(真实归属),
        # 此处直接用 composite;bare(无内嵌归属)时才 compose 执行用户作归属。
        _owner_bot = request.owner_bot_id or ""
        if ":" not in _owner_bot:
            _owner_bot = compose_bot_identity(_owner_bot, request.owner_user_id)
        gf = GroupFormation(
            bot_ids=[
                _owner_bot,
                *ec.get("participant_bot_ids", []),
            ],
            collab_mode=resolve_coop_collab_mode(has_yaml, ec.get("group_kind")),
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

    async def _run_bbs(self, task_id, request, task_info, run_id) -> TaskOpResult:
        logger.info("[task][bbs_mode], begin_run_bbs, task_id=%s", task_id)

        self._engine._hung_and_escalate(task_id=task_id, node_id=task_id, hung_reason="创建BBS接力任务")

        logger.info("[task][bbs_mode], finish_run_bbs, task_id=%s", task_id)
        return TaskOpResult(
            task_id=task_id, success=True, run_id=run_id, extend_props={}
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
