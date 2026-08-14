"""TaskExecutor:三模态派发(single_bot/coop_group/bbs)+ 旁路 poller 登记入口。

dispatch(async):上游 start_run caller loop 上 gather+Semaphore await 端口 IO,拿到 run_id 即返回
(不等待结果);bbs 仅记日志。form_coop_group(async):BCS 建群壳。poller 为独立 daemon sidecar(同 TaskHarness)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import BcsCreateGroupRequest
from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiAuthError, OpenApiBadRequestError,
)
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    BcsGroupHandle, SingleBotHandle,
)

logger = logging.getLogger(__name__)
_DISPATCH_CONCURRENCY = 8


class TaskExecutor:
    def __init__(self, *, bot, bcs, formatter, context, sink, poller) -> None:
        """bot: OpenApiBotPort|None; bcs: BcsClientPort|None; formatter: PromptFormatter|None;
        context: TaskContextBuilder|None; sink: ResultSink|None; poller: TaskExecutorResultPoller|None。
        R0 骨架允许 None;bbs 路径不依赖任何端口。"""
        self._bot = bot
        self._bcs = bcs
        self._formatter = formatter
        self._context = context
        self._sink = sink
        self._poller = poller
        self._group_meta: dict[str, dict[str, Any]] = {}  # group_id -> {collab_mode, gf, definition_ref, session_id}

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        sem = asyncio.Semaphore(_DISPATCH_CONCURRENCY)

        async def _one(node: TaskNode) -> bool:
            mode = node.run_info.run_mode
            if mode == "bbs":
                logger.info("[task_executor] bbs node dispatched (no-op): task=%s node=%s assignee=%s",
                            node.task_id, node.node_id, node.run_info.assignee)
                return True
            if mode == "single_bot":
                return await self._dispatch_single_bot(node, sem)
            if mode == "coop_group":
                return await self._dispatch_coop_group(node, sem)
            return False

        return list(await asyncio.gather(*[_one(n) for n in toDoTaskList]))

    async def _dispatch_single_bot(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        bot_id = node.run_info.assignee
        loop_task_id = f"{node.task_id}::{node.node_id}"
        async with sem:
            try:
                await self._bot.ensure_grant(bot_id)
                ctx = self._context.build(node.task_id, node.node_id)
                message = self._formatter.format_execute(ctx, node)
                run_id = await self._bot.send_message(
                    bot_id=bot_id, message=message,
                    metadata={"biz_task_id": node.task_id},
                )
            except (OpenApiAuthError, OpenApiBadRequestError):
                return False
            self._poller.register(SingleBotHandle(
                loop_task_id=loop_task_id, run_id=run_id, bot_id=bot_id,
                registered_at=time.monotonic(),
            ))
            return True

    async def _dispatch_coop_group(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        group_id = node.run_info.assignee
        meta = self._group_meta.get(group_id)
        collab_mode = (meta or {}).get("collab_mode", "chat")
        loop_task_id = f"{node.task_id}::{node.node_id}"
        async with sem:
            if collab_mode == "state_machine":
                return await self._dispatch_state_machine(node, group_id, meta, loop_task_id)
            ctx = self._context.build(node.task_id, node.node_id)
            prompt = self._formatter.format_execute(ctx, node)
            session_id = await self._bcs.create_session(group_id, bootstrap_prompt=prompt)
            self._poller.register(BcsGroupHandle(
                loop_task_id=loop_task_id, group_id=group_id, collab_mode=collab_mode,
                registered_at=time.monotonic(), session_id=session_id, run_id=None,
            ))
            return True

    async def _dispatch_state_machine(self, node, group_id, meta, loop_task_id) -> bool:
        ctx = self._context.build(node.task_id, node.node_id)
        prompt = self._formatter.format_execute(ctx, node)
        definition_ref = (meta or {}).get("definition_ref")
        run_id = await self._bcs.start_state_machine_run(
            group_id, definition_yaml=None, definition_ref=definition_ref,
            session_id=None, input={"query": prompt},
        )
        self._poller.register(BcsGroupHandle(
            loop_task_id=loop_task_id, group_id=group_id, collab_mode="state_machine",
            registered_at=time.monotonic(), session_id=None, run_id=run_id,
        ))
        return True

    async def form_coop_group(self, gf: GroupFormation) -> str:
        bot_ids = list(gf.bot_ids)
        mode = gf.collab_mode
        participants = [{"bot_uuid": b} for b in bot_ids]
        req_kwargs: dict[str, Any] = {"driver_bot": bot_ids[0], "participants": participants}
        if mode == "manager_worker":
            mgr = gf.extend_props.get("manager_bot_id") or bot_ids[0]
            req_kwargs["group_strategy"] = "manager_worker"
            req_kwargs["driver_bot"] = mgr
            req_kwargs["participants"] = [
                {"bot_uuid": mgr, "role": "manager"}] + [
                {"bot_uuid": b, "role": "worker"} for b in bot_ids if b != mgr]
        elif mode == "state_machine":
            req_kwargs["group_strategy"] = "state_machine"
            # GroupFormation.extend_props["definition_yaml"] → BCS collaboration_definition_yaml
            def_yaml = gf.extend_props.get("definition_yaml") or gf.extend_props.get("collaboration_definition_yaml")
            if def_yaml is not None:
                req_kwargs["collaboration_definition_yaml"] = def_yaml
            req_kwargs["participant_bindings"] = {b: {"source": "manual", "bot_ids": [b]} for b in bot_ids}
            req_kwargs["start_initial_run"] = False
        service_spec = gf.extend_props.get("service_spec")
        if service_spec:
            req_kwargs["service_spec"] = service_spec
        req = BcsCreateGroupRequest(**req_kwargs)
        res = await self._bcs.create_group(req)
        self._group_meta[res.group_id] = {
            "collab_mode": mode, "gf": gf,
            "definition_ref": res.definition_ref, "session_id": res.session_id,
        }
        return res.group_id

    async def aclose(self) -> None:
        if self._poller is not None:
            self._poller.stop()
