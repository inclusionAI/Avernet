"""TaskDispatcher 搜推分发(决定"谁来做",不写图不起 run)+ BotDiscoverPort seam。

对齐 plan §3.3 + 派发文档 ue1ie0g3supwo2uf。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskNode


class TaskDispatcher:
    """据搜推 4 态选执行主体 + 多 bot 动态拉协作群;把 run_mode(str)/assignee 填到
    TaskNode.run_info 后返回 list[TaskNode],不写图不起 run(编排核落库+起 run)。

    分层:BotDiscoverPort(seam,搜推,stub/corp)↔ TaskDispatcher(编排)。BotDiscoverPort
    Protocol + SearchResult/GroupFormation 定义延后(后续 task_dispatch/protocols.py,
    待 stub/真实搜推就位)。
    """

    def __init__(self, discover, runner):
        """discover: BotDiscoverPort seam;runner: TaskRunner(form_coop_group 用)。
        首批均不强类型,Protocol 定义延后到 task_dispatch/protocols.py(待 stub/真实搜推就位)。"""
        self._discover = discover
        self._runner = runner

    def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        """入参=待派发节点;返回=填充执行者信息后的 list[TaskNode](对齐派发文档签名);
        不写图、不起 run;per node 仅按 node.task_spec 搜推,把结果填 node.run_info:
        HIT_SINGLE→single_bot/bot_id;HIT_GROUP→coop_group/group_id;
        HIT_MULTI_BOTS→form_coop_group(gf)→coop_group/gid;MISS→不填(run_mode/assignee 仍 None,
        status 仍 PENDING),标 extend_props.miss_events。BBS 节点退化为直接标 bbs+bot_id(不走搜推)。"""
        raise NotImplementedError
