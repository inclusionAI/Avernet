"""TaskExecutor:三模态派发(single_bot/coop_group/bbs)+ 旁路 poller 登记入口。

dispatch(async):上游 start_run caller loop 上 gather+Semaphore await 端口 IO,拿到 run_id 即返回
(不等待结果);bbs 仅记日志。form_coop_group(async):BCS 建群壳。poller 为独立 daemon sidecar(同 TaskHarness)。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiAuthError, OpenApiBadRequestError,
)
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    SingleBotHandle,
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
        return True  # T8/T9 替换:create_session/start_state_machine_run→poller.register

    async def form_coop_group(self, gf: GroupFormation) -> str:
        gid = f"grp_{uuid.uuid4().hex[:8]}"  # T8 替换:真实 BcsHttpAdapter.create_group
        self._group_meta[gid] = {"collab_mode": gf.collab_mode, "gf": gf,
                                 "definition_ref": None, "session_id": None}
        return gid

    async def aclose(self) -> None:
        if self._poller is not None:
            self._poller.stop()
