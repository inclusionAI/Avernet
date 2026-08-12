"""TaskDispatcher 搜推分发(决定"谁来做",不写图不起 run)+ BotDiscoverPort seam。

对齐 plan.md §3.3 + tasks.md T4.2。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_dispatch.protocols import (
    BotDiscoverPort,
    SearchOutcome,
)


class TaskDispatcher:
    """据搜推 4 态填 run_mode(str)/assignee 到 TaskNode.run_info 后返回 list[TaskNode]。
    不写图、不起 run(编排核 M2 落库+起 run)。BBS 节点(run_mode 已 "bbs")退化为直接维持。"""

    def __init__(self, discover: BotDiscoverPort, runner):
        """discover: BotDiscoverPort seam;runner: TaskRunner(form_coop_group 用)。"""
        self._discover = discover
        self._runner = runner

    def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        """入参=待派发节点;返回=填充执行者信息后的 list[TaskNode](对齐派发文档签名)。
        不写图、不起 run;per node 按 node.task_spec 搜推,结果填 node.run_info:
        HIT_SINGLE→single_bot/bot_id;HIT_GROUP→coop_group/group_id;
        HIT_MULTI_BOTS→form_coop_group(gf)→coop_group/gid;MISS→不填+标 miss_events。
        BBS 节点(run_mode 已 "bbs")→ 退化直接维持(不走搜推)。"""
        out: list[TaskNode] = []
        for node in toDoTaskList:
            if node.run_info.run_mode == "bbs":
                # BBS 节点:认领 bot 已标 run_mode="bbs"+assignee=bot_id,退化为直接维持
                out.append(node)
                continue
            result = self._discover.search(node)
            if result.outcome == SearchOutcome.HIT_SINGLE:
                node.run_info.run_mode = "single_bot"
                node.run_info.assignee = result.bot_id
            elif result.outcome == SearchOutcome.HIT_GROUP:
                node.run_info.run_mode = "coop_group"
                node.run_info.assignee = result.group_id
            elif result.outcome == SearchOutcome.HIT_MULTI_BOTS:
                gid = self._runner.form_coop_group(result.group_formation)
                node.run_info.run_mode = "coop_group"
                node.run_info.assignee = gid
            else:  # MISS
                node.run_info.extend_props["miss_events"] = [result.miss_reason or "no_bot"]
            out.append(node)
        return out
