"""TaskDispatcher 派发编排壳(零 case 知识)+ 内置策略库(first-match-wins)。

对齐 plan §3.3 + §3.4 + 派发文档 ue1ie0g3supwo2uf。零参构造(持 graph 只读 config),内置默认策略池
[DirectDispatchStrategy, SearchBasedDispatchStrategy];策略库(first-match-wins by priority,
类 SQL optimizer,据 execution_config 动态匹配:config 有 bot→direct 跳搜推;否则 search 兜底)。
不持 runner(HIT_MULTI_BOTS 拉群归编排核+runner);不写图、不起 run。
引擎自带能力,不开放自定义;corp 经 ocb 仓覆写 ``_build_*`` 替换策略版本(待后续 PR 落 strategies.py)。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskNode


class TaskDispatcher:
    """派发编排壳:对每节点 first-match-wins 选策略(graph 级 config 匹配)→ apply 填 run_info 后返回。

    不写图、不起 run(编排核落库+起 run)。BBS 节点(run_mode 已 "bbs")退化为直接维持(不走策略)。
    HIT_MULTI_BOTS 时填 run_mode="coop_group"+extend_props["pending_group_formation"],assignee 留空
    (拉群归编排核调 runner.form_coop_group 后填 assignee)。不持 runner。策略契约 + SearchResult/
    GroupFormation/SearchOutcome + 默认 stub 类定义待后续 PR 落 task_dispatch/strategies.py。
    """

    def __init__(self, graph) -> None:
        """graph: TaskGraphService(读图级 execution_config 匹配策略用,不写)。
        零参构造,内置默认策略池 [DirectDispatchStrategy, SearchBasedDispatchStrategy]
        (首批壳,策略池接线待后续 PR 落 strategies.py)。不持 runner。"""
        self._graph = graph
        self._strategies = None  # list[DispatchStrategy](首批壳,待后续 PR)

    def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        """入参=待派发节点;返回=填充执行者信息后的 list[TaskNode](对齐派发文档签名)。
        不写图、不起 run;per node first-match 策略 apply SearchResult → 填 node.run_info:
        HIT_SINGLE→single_bot/bot_id;HIT_GROUP→coop_group/group_id;
        HIT_MULTI_BOTS→coop_group/pending_group_formation(assignee 留空,编排核拉群填);
        MISS→不填+标 miss_events。BBS 节点(run_mode 已 "bbs")→ 退化直接维持。"""
        raise NotImplementedError
