"""BBS 主动触发(bid→select→claim→dispatch)两 dream-bot 端到端 —— 进程内 double 驱动。

# 场景

任务 MISS→HUNG→升 BBS(``miss_depth_exhausted`` + ``bbs_mode`` + 根 PLANNING 可恢复态)后,
引擎 ``_schedule_bbs_notify`` fire-and-forget ``runner.run_bbs`` → ``bbs_runner.notify`` 向
**两个 dream-mode bot** 并发 bid(``send_and_wait_async`` 自评 ``completion_rate``)→ 选最高者 B →
引擎服务端 ``claim_bbs_owner(B)``(并 recover 清 HUNG 死分支)→ 给 B 派发 ``bbs-relay-single-task``
任务消息 → B 模拟跑该 skill:``attach_bbs_node`` 挂 ``run_mode=bbs`` scoped 节点 → 执行 → ``on_bbs_report``
回投 PASS + ``architects`` 产出 → ``_on_pass_collect`` 复核根 gap 闭 → 图 DONE。

断言"两个 bot 都参与了 bid、winner 是其中之一、且经 bbs scoped 节点执行收口"——**不固定赢家**
(LLM 自评不确定;本测用 double 给定 rate 使 B 必胜,仅供可复现,真实链路 winner 由各 bot 自评决定)。

# 为什么是进程内 double 而非 singlebox live

两条前置使 live 链路当前跑不起来,故先用进程内 double 验证 bid 编排逻辑本身:

1. **dream-mode 入 roster 阻塞**:BCS ``task_dream_mode`` 唯一 setter 是 principal-gated 的
   ``PATCH /openapi/v1/collaboration/bots/{id}``(``bcs-api-http`` openapi v1,需
   ``AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE`` 签的 gateway principal token);singlebox 该 key
   未设时 /openapi/v1 全 401。bcs-http(mock-auth 面,``set_bcs_visibility`` 用的)无 dream setter,
   Avernet 也未暴露 dream-mode 端点。故 live 链路要把两 bot 放进 ``list_bots_by_task_modes(dream=True)``
   roster,需先补 principal key + 自铸 token(或给 Avernet 加 setter)——见本目录 README/plan。
2. **execution_graph.task_id 接缝(已修)**:``bbs_runner.notify``/``TaskRunner.run_bbs`` 取
   ``execution_graph.task_id``,而 ``TaskExecutionGraph`` 原无该字段;引擎 ``_schedule_bbs_notify``
   传的是 ``query_task_dashboard`` 结果 → live 触发即 ``AttributeError``(单测用 MagicMock.mask 掉)。
   本提交已给 ``TaskExecutionGraph`` 加 ``task_id``(initialize/子树投影透传),使真触发链可用;

   因此本测能驱动**真** ``_schedule_bbs_notify→run_bbs→bbs_runner.notify``(非 mock 触发),仅把
   传输端口(bot/bcs)换 double:dream roster 注入 + bid 回复 + winner 执行(attach/result)模拟。
   ``ExecutionEngine``/``TaskGraphService``/``TaskExecutor``/``bbs_runner``/``claim_bbs_owner``/
   ``attach_bbs_node``/``on_bbs_report`` 收口路径全部走真代码。

# 用例聚焦

证明 bid 编排:**两 dream bot 并发 bid → 选 completion_rate 最高者 → 引擎占根给 winner →
winner 经 bbs scoped 节点 attach→execute→result → 图 DONE**。winner 自评/执行细节(LM)不在本测范围
(live 测才覆盖);引擎触发接线(``_schedule_bbs_notify`` 是否 fire)由 ``test_engine_bbs_trigger`` 覆盖。
"""
from __future__ import annotations

import asyncio
import json
import re

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    PlanResult,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BotTaskModeRoster,
)
from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor
from agentclaw.community.core.task.task_runner.runner import TaskRunner


TASK_ID = "t_bbs_bid_two_dream"
_BOT_A = "dream-bot-a"
_BOT_B = "dream-bot-b"
_BACKEND_URL = "http://test-backend:8888"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ===== domain helpers =====
def _task_info(task_id: str = TASK_ID, max_depth: int = 1) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="基础架构方向架构师名册", instruction="整理 3 位架构师"),
            context=Context(background="架构师名册梳理"),
            goal=Goal(objective="整理 3 位核心架构师(姓名/角色 + 职责)",
                      acceptances=[AcceptanceCriteria(id="ac_arch", description="给出 3 位架构师姓名/角色 + 职责")]),
        ),
        source_type="bot", owner_bot_id="owner-bot",
        execution_config={"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3},
    )


def _child(node_id: str) -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=TASK_ID, status=Status.PENDING,
        task_spec=_task_info().task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _relay_task_spec() -> TaskSpec:
    """winner 模拟 bbs-relay-single-task 自组织的 scoped 子任务规格。"""
    return TaskSpec(
        metadata=Metadata(task_id="N_bbs_relay", title="BBS 中继:架构师名册", instruction="补完架构师名册"),
        context=Context(background="升 BBS 后中继段"),
        goal=Goal(objective="整理 3 位核心架构师",
                  acceptances=[AcceptanceCriteria(id="ac_arch", description="给出 3 位架构师姓名/角色 + 职责")]),
    )


def _parse_task_id_from_msg(message: str) -> str:
    """从 bbs_runner._task_msg 里取 task_id(skill 进场亦从消息里读)。"""
    m = re.search(r"task_id=([^;\s]+)", message)
    assert m, f"task 消息缺 task_id:{message!r}"
    return m.group(1)


# ===== doubles =====
class _DreamBcs:
    """double BcsClientPort:list_bots_by_task_modes 返两 dream-mode bot;记调用。"""

    def __init__(self, roster: list[BotTaskModeRoster]) -> None:
        self._roster = roster
        self.roster_calls: list[dict] = []

    async def list_bots_by_task_modes(self, *, dream=None, claim=None, match="any"):
        self.roster_calls.append({"dream": dream, "claim": claim, "match": match})
        return list(self._roster)


class _DreamBot:
    """double OpenApiBotPort:
    - send_and_wait_async = bid 回复(自评 completion_rate JSON);
    - send_message = winner 收到 bbs-relay-single-task 任务消息 → 模拟 attach→execute→result 收口。
    """

    def __init__(self, rates: dict[str, int]) -> None:
        self._rates = rates
        self.bids: list[str] = []              # send_and_wait_async 调过的 bot_id(bid)
        self.dispatched: list[tuple] = []      # send_message 调过的 (bot_id, message, metadata)
        self._svc: TaskGraphService | None = None
        self._engine: ExecutionEngine | None = None

    def bind(self, svc: TaskGraphService, engine: ExecutionEngine) -> None:
        self._svc = svc
        self._engine = engine

    async def send_and_wait_async(self, *, bot_id, message, metadata=None, timeout=180.0, poll_interval=2.0):
        self.bids.append(bot_id)
        rate = self._rates[bot_id]
        return {"status": "COMPLETED",
                "result": {"content": json.dumps({"completion_rate": rate})}}

    async def send_message(self, *, bot_id, message, metadata):
        # 模拟 winner(bbs-relay-single-task skill):占根已由引擎 claim → 直接 attach → execute → result
        self.dispatched.append((bot_id, message, metadata))
        assert self._svc is not None and self._engine is not None
        task_id = _parse_task_id_from_msg(message)
        node = self._svc.attach_bbs_node(task_id, parent_node_id=task_id,
                                         task_spec=_relay_task_spec(), bot_id=bot_id)
        await self._engine.on_bbs_report(TaskNodePatch(
            task_id=task_id, node_id=node.node_id, assignee=bot_id,
            acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS,
                                               acceptances_metric=["ac_arch"], gaps=[]),
            output_patch={"architects": [
                {"name": "张工", "role": "基础架构师", "responsibility": "主导计算层架构"},
                {"name": "李工", "role": "存储架构师", "responsibility": "主导存储层架构"},
                {"name": "王工", "role": "网络架构师", "responsibility": "主导网络层架构"},
            ]},
        ))
        return BotSendResult(run_id=f"r_{bot_id}", session_id=None)


class _ClosingPlanner:
    """stub planner:gap 闭判据——任一 DONE 子节点 output 含 architects → has_gap=False 收口。
    镜像 planning-arch「单一交付物:架构师名册」收口语义(bbs scoped 节点的 architects 产出满足根 gap)。"""

    async def plan(self, graph, target_node_id: str | None = None) -> PlanResult:
        root_id = graph.task_id or (graph.tasks[0].task_id if graph.tasks else "")
        has_arch = any(
            t.node_id != root_id and t.status == Status.DONE
            and isinstance(t.run_info.output, dict) and t.run_info.output.get("architects")
            for t in graph.tasks
        )
        return PlanResult(children=[], has_gap=not has_arch,
                          gap_detail="done" if has_arch else "缺架构师名册")


class _NoDispatcher:
    async def dispatch(self, toDoTaskList):  # noqa: D401,N803 (stub;本测不经派发,仅占位)
        return []


class _BidEngine(ExecutionEngine):
    """测试引擎:bot/bcs 用 double,executor 不起 poller 线程,planner 用收口 stub,runner 用真 TaskExecutor 后端。"""

    def __init__(self, graph, *, bot, bcs, planner, api_base_url):
        self._case_planner = planner
        super().__init__(graph, bot=bot, bcs=bcs, api_base_url=api_base_url)

    def _build_planner(self):
        return self._case_planner

    def _build_dispatcher(self):
        return _NoDispatcher()

    def _build_executor(self):
        # 真 TaskExecutor(bbs 路径只用 bot/bcs/graph/api_base_url);poller=None 不起后台线程
        return TaskExecutor(bot=self._bot, bcs=self._bcs, formatter=None, context=None,
                            sink=None, poller=None, identity_resolver=None,
                            graph=self._graph, api_base_url=self._api_base_url)

    def _build_runner(self):
        return TaskRunner(self._graph, execution_backend=self._executor)


# ===== test =====
def test_two_dream_bots_bid_winner_executes_to_done():
    """升 BBS → 引擎 bid 两 dream bot → 选 winner → claim → dispatch → winner attach/execute/result → 图 DONE。"""
    svc = TaskGraphService()
    bot = _DreamBot(rates={_BOT_A: 60, _BOT_B: 90})   # B 自评更高 → winner
    bcs = _DreamBcs([
        BotTaskModeRoster(bot_id=_BOT_A, name="DreamA", env="dev", task_claim_mode=True, task_dream_mode=True),
        BotTaskModeRoster(bot_id=_BOT_B, name="DreamB", env="dev", task_claim_mode=True, task_dream_mode=True),
    ])
    svc.initialize_graph(_task_info(TASK_ID, max_depth=1))
    svc.add_task_nodes([_child("N_architects")], parent_node_id=TASK_ID)

    eng = _BidEngine(svc, bot=bot, bcs=bcs, planner=_ClosingPlanner(), api_base_url=_BACKEND_URL)
    bot.bind(svc, eng)

    async def _drive() -> None:
        # on_miss@MAX_DEPTH=1 → miss_depth_exhausted → 升 BBS(bbs_mode)→ 根可恢复拦截 →
        # _schedule_bbs_notify fire-and-forget run_bbs(真链路,非 mock 触发)
        await eng.on_miss(TaskNodePatch(task_id=TASK_ID, node_id="N_architects",
                                        extend_props_patch={"miss_events": ["no_bot"]}))
        # 等后台 bid→claim→dispatch→winner 执行收口 跑完
        if eng._bg_tasks:
            await asyncio.gather(*eng._bg_tasks, return_exceptions=True)

    _run(_drive())

    # ===== 断言:bidding 编排 =====
    assert bcs.roster_calls and bcs.roster_calls[0]["dream"] is True, \
        f"未按 dream=True 拉 roster:{bcs.roster_calls}"
    assert set(bot.bids) == {_BOT_A, _BOT_B}, f"两个 dream bot 未都 bid:{bot.bids}"
    assert len(bot.dispatched) == 1, f"应恰派发一次任务消息给 winner:{bot.dispatched}"
    assert bot.dispatched[0][0] == _BOT_B, f"winner 应为自评最高的 B:{bot.dispatched[0][0]}"

    # ===== 断言:执行收口(真图态)=====
    g = svc.query_task_dashboard(TASK_ID)
    assert g.status == Status.DONE, f"图未 DONE:{g.status}(bid 后 winner 未执行/未收口?)"
    assert g.extend_props.get("bbs_mode") is True, "未升 BBS(未 miss_depth_exhausted?)"

    root = next(n for n in g.tasks if n.node_id == TASK_ID)
    assert root.status == Status.DONE, f"根未 DONE:{root.status}"
    # claim 已释放(on_bbs_report finally 清根 bbs_owner)
    assert root.run_info.extend_props.get("bbs_owner") is None, "收口后 bbs_owner 未释放"

    scoped = [n for n in g.tasks
              if n.node_id != TASK_ID and (n.run_info.run_mode == "bbs")]
    assert len(scoped) == 1, (
        f"应恰 1 个 bbs scoped 中继节点(1 个 winner 执行段):"
        f"{[(n.node_id, n.run_info.run_mode, n.status) for n in g.tasks]}")
    sc = scoped[0]
    assert sc.status == Status.DONE, f"scoped 节点未 DONE:{sc.status}"
    assert sc.run_info.assignee == _BOT_B, f"scoped assignee 非 winner B:{sc.run_info.assignee}"
    ar = sc.run_info.acceptance_result
    assert ar is not None and ar.verdict == AcceptanceVerdict.PASS, f"scoped 未 PASS:{ar}"
    assert sc.run_info.output.get("architects"), f"scoped 无 architects 产出:{sc.run_info.output}"

    # recover 清掉了原 MISS 的 HUNG 死分支 N_architects(bbs 接力视为推倒重做)
    assert all(n.node_id != "N_architects" for n in g.tasks), "claim recover 未清 HUNG 死分支 N_architects"
