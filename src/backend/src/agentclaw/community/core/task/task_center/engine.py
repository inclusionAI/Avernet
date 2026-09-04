"""ExecutionEngine 内部编排核(事件驱动 + 状态条件触发)。对齐 plan.md §3.0。

非独立模块,TaskService 内部实现细节,对外不暴露。构造期收传输端口(bot/bcs/discover,由 DI 从配置注入),
``_build_*`` 内部 new 引擎自带策略(TaskPlanner/TaskDispatcher/TaskRunner)+ 接线 TaskExecutor(三模态投递+poller)。
引擎自身实现 ResultSink(poller 终态回投直接调 on_report)与 TaskContextBuilder(执行上下文派生),
消除"先建 stub 再外部注入真实 body/接线点"的后填,无引擎子类化、无 reach-in setter。验收 100% 走 on_report
回投(gap 计算即验收,无主动 verify dispatch);BBS 与其它模态统一经 runner.start_run 投递。
零 case 知识:engine 不含任何节点名字面量。测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。

Step2 改造(状态机解耦 + PlanResult + 显式 target + harness 执行报错区分):
- 状态机:PLANNING=规划中(显式委托态;on_pass 翻父 RUNNING→PLANNING 后 plan),RUNNING=执行中(子执行/自身执行);
  add_task_nodes 翻父→RUNNING(委托)。终验=根 gap 闭(plan 返 []+has_gap=F)→翻根 DONE + 图 DONE。
- plan(graph, target_node_id):on_execute→None(自发现根)/on_pass→parent/on_fail→failed 叶/on_miss→miss 叶。
  返 PlanResult(children, has_gap, gap_detail) 四象限:children→add+dispatch;空+has_gap=F→gap 闭 DONE;
  空+has_gap=T→深度闸门(升 BBS/HUNG)。
- harness 执行报错(exec_error):bot 压根没跑通(run FAILED/SLA/poll 耗尽)≠ 验收不过(run COMPLETED+FAIL)。
  执行报错→on_harness 复位 RUNNING→PENDING 重投;计 harness_retries,达 MAX_HARNESS(默认 2)→HUNG 不再流转。
  验收不过(acceptance FAIL+gaps)→on_fail 补救重规划(深度闸门)。

协程化(CR 反馈:任务执行是耗时任务):全链路 ``async def``。锁内 await plan/dispatch(同 task 串行 IO,设计意图);
投递/拉群 IO 锁外 await,gather+Semaphore 下沉。副作用收集:on_* 锁内 async collect → 锁外 ``_drain`` await 执行。
注:``threading.RLock`` 跨线程正确串行;corp 单持久 loop 并发同 task 回投需切 ``asyncio.Lock``(ocb 接入时定)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from dataclasses import dataclass, replace
from typing import Any

from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    NodeAction,
    NodeOpResult,
    PlanResult,
    Status,
    TaskCallbackData,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    effective_run_mode,
    TaskNodeQueryCriteria,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation


logger = logging.getLogger("task.engine")

_DEFAULT_MAX_HARNESS = 2  # 执行报错 harness 重投上限(达上限→HUNG)

# 固定流程兜底 mock 的"产出摘要"(节点真实上报未在 fallback 超时内闭环时,以此真实内容代替 [auto] 占位)。
# 取自 okr-implementation-relay 剧本(服装多平台大促)各节点"本跳产出正文"摘要,供下游 ## 上游产出正文 可读、
# 流程不因占位无意义文本而读不通。仅服务默认真实上报 + 80s 兜底路径;真实上报先到则本表内容不被使用。
_STATIC_MOCK_SUMMARY: dict[str, str] = {
    "marketing_strategy": (
        "双十一大促三平台差异化营销策略:淘宝预售蓄水(10.15起品类券)+11.11返场爆品,目标占GMV55%;"
        "京东近仓速达/价保/无忧退提客单,占25%;拼多多拉新组合+证质门槛,占20%。"
        "三平台统一主视觉、优惠规则前置公示。"
        "无法独立闭环:人群未分层、主推商品池未定需圈人/选品细化;多平台比价、低质拉新、负面舆情需风控评估。"
    ),
    "strategy_generation_group": (
        "完整大促营销策略(三平台差异化+人群分层+选品商品池+玩法):"
        "淘宝站内+直通车精准+品类券(满300减40),京东搜索/京准通+服务券(价保/速达),拼多多多多进宝拉新+门槛券;"
        "人群:淘宝老客分层(高价值/沉睡/流失召回)、京东品质人群+蓝海潜客、拼多多下沉新客设证质门槛;"
        "选品:淘宝冬装基本款+羽绒服爆款、京东高端羽绒+配饰、拼多多低价引流款设证质拦截刷单;"
        "玩法:预售10.15起定金翻倍、11.4–11.11品类券、11.11–11.15返场,总投入不超1000万。"
        "无法闭环:多平台比价、低质拉新、负面舆情需风控评估;舆情监测无人承接需安全架构师补。"
    ),
    "risk_lead": (
        "锁定8项重点风险点与评审范围:①券规则被中介套利(三平台品类券);②拼多多低质拉新二次客诉(下沉新客);"
        "③部分地区发货延迟(羽绒服重货);④客诉赔付超日常1.5倍;⑤京东价保争议;⑥热销缺货/预售超卖;"
        "⑦大促负面舆情无承接系统(无人承接,需安全架构师补舆情监测);⑧跨平台比价套利。建议风险评审群重点审②③④⑦。"
        "无法闭环:舆情监测无对接系统无承接团队,业务风控也补不了,带进风险评审群标无人承接/需安全架构师补。"
    ),
    "risk_assessment": (
        "8项风险逐项定级与约束:客诉赔付(高,赔付预案¥300万+夜间客服扩容)、发货延迟(高,热销品前置入仓+运力保底)、"
        "低质拉新(高,证质门槛+拉新黑名单)、券套利(中,单平台限购+实名校验)、价保争议(中,规则前置公示)、"
        "库存缺货(高,预售库存强校验)、跨平台比价(中,每日价差监控)、舆情监测(无人承接,转安全架构师)。"
        "无人承接任务:舆情监测系统无承接团队无对接系统→转BBS安全架构师承接。unhandled_tasks见上报硬字段。"
    ),
    "risk_unhandled_to_bbs": (
        "交付大促舆情监控补齐方案(舆情监测MVP):覆盖三平台店铺评论+社媒关键词,情感分类,负面按严重度分级,"
        "阈值触发告警→客服/运营闭环,接入审核/实施dashboard;MVP就位可供审核与实施接入。"
        "业务风险(逐项成因/等级/约束):低质拉新二次客诉(高,赔付预案¥300万+夜间客服扩容)、发货延迟(高,热销前置入仓+运力保底)、"
        "券套利(中,单平台限购+实名校验)、价保争议(中,规则前置公示)、库存缺货/预售超卖(高,预售库存强校验)、跨平台比价(中,每日价差监控)。"
        "技术/系统风险:券核销与库存并发须防超卖(强校验)、价差监控告警链路延迟、舆情监测接入告警通道联调。"
        "carry-forward上游全料:①三平台差异化营销策略 ②人群分层+选品商品池+玩法 ③上述风险逐项结论与约束,供下游审核一次审齐。"
    ),
    "strategy_approval": (
        "审核结论:批准有条件通过。问题清单:高—低质拉新二次客诉(证质门槛+黑名单,需实施盯控负向清单);"
        "高—发货延迟(热销前置入仓,需盯入仓率);高—客诉赔付(赔付预案¥300万+客服扩容,需盯赔付率);"
        "中—券套利(单平台限购);中—价保争议(规则前置);中—跨平台比价(每日监控)。舆情监测MVP就绪,实施接入告警通道。"
    ),
    "implementation": (
        "三平台投放配置单已定并挂监控:淘宝预售10.15起定金翻倍、品类券满300减40、11.11爆品返场,监控入仓率/赔付率;"
        "京东价保+速达服务券、搜索/京准通投放,监控价保工单;拼多多拉新组合+证质门槛+黑名单,监控低质拉新占比;"
        "跨平台每日价差监控;舆情MVP接入告警→客服。异常处置预案:触发阈值→降量/限购/赔付/公关。"
    ),
    "notify_done": (
        "大促方案已实施,通知负责人收尾。各跳交接摘要:①专家出三平台策略方向→②策略生成群细化人群+选品+玩法→"
        "③'风控圈8项风险点→③风险评审群评审定级与约束→④安全架构师补舆情监测MVP+带全料→⑤审核批准有条件通过+三项硬约束→"
        "⑥落地配置单+监控+预案。当前状态:已实施待执行日,三项高风险需盯控(低质拉新/发货延迟/客诉赔付),舆情监测已上线。"
    ),
    # ---- merchant-operations-goal-to-plan 节点 mock(理发店18周年店庆) ----
    "set_redlines": (
        "18周年店庆任务单(全套公共约束):活动窗口2026.10.15-11.15(32天)、营业时段10:00-22:00、促销预算≤20万、备货现金占用≤8万、价格红线(补贴率≤30%、最低折扣85折、护理套餐最低价298)、平台合规红线(券有效期/预约限制须明示、禁止全城最低)、客流目标(新客1500/护理200)。自持单品成本、毛利底线、授权清单留给利润核算。发门店运营测产能。"
    ),
    "ops_capacity": (
        "门店能力画像(运营子集测算):剪发位8、护理位3、技师12(剪发8/护理4);剪发45min、护理90min;周末剪发新增约15人/天、护理新增约8人/天(扣自然客流),工作日余量较足;可扩:临时剪发技师+2→剪发+10人/天、延营至23:00→周末+5人/天;护理耗材现有30份、备货8万可补到60份;目标新客1500(≈47人/天)需扩产能可达。营销约束原样透传:促销预算≤20万、价格红线、平台合规红线、客流目标(不解读不使用)。交营销出方案。"
    ),
    "marketing_plan": (
        "营销方案(能力内):新客王牌剪发体验券原价68→体验价48(补贴29%≤30%),按产能分批投放冲1500;老客头皮+基础护理套餐90min、398(≥298红线),首批按库存60份与护理位产能限量、预约制;会员周年双倍积分+会员日提前购;节奏:预约占用率>75%暂停放量。送评审群审。"
    ),
    "review_group": (
        "评审群结论(商场运营+线上平台两路,默认给修订条件不打回):商场侧——展位报批通过、传单限入口、营业时段内执行;平台侧——券有效期与预约限制须在活动页明示、禁止全城最低、设等待超时安抚、客诉超阈值暂停投放。过审带修订条件,交店主利润核算。"
    ),
    "profit_accounting": (
        "利润核算表(自持成本):体验券单次成本25/售价48达标;护理套餐成本180/售价398达标;某引流款低于毛利底线标红。授权清单:体验券分批投放可自主、护理套餐放量需店主审批、扩产能(临时技师)需店主审批。待取舍:低价引流款是否保留、临时技师成本是否接受。交投放实施。"
    ),
    "launch": (
        "投放实施阶段发现:周年活动券当前通过公众号和门店渠道发放,"
        "现有发券与核销工具无法可靠判断同一用户是否已领取过同类活动券,"
        "重复领取拦截能力无法确认,需要补齐后再继续放量。"
    ),
}

# 固定流程兜底 mock 的"不可实现任务"(risk_assessment 上报无结构化 unhandled_tasks 时兜底)。
# 大促剧本:风险评审认为差外部舆情监控能力,内部找不到对应舆情监控 bot → 转 BBS 广场由安全架构师承接。
_UHT_MOCK: list[dict[str, str]] = [
    {
        "id": "uht-sentiment-1",
        "title": "舆情监控方案缺失",
        "reason": "风险评审认为差外部舆情监控能力(发货慢/尺码偏小等负面舆情无监测,需告警→客服闭环),内部找不到对应舆情监控 bot,转 BBS 广场由安全架构师承接",
    },
]

# 陈旧飞行态阈值(dispatching=True 超此即视为崩溃遗留,redrive 可清理重派)。
# 默认 60s:正常 start_run 在途远小于此;与默认 recovery lease(60s)对齐——崩溃任务经 recovery
# 拾起时 dispatching_at 已超阈值,判陈旧;新鲜在途派发保留,不与 redrive 双派发。可经 env 调。
_DISPATCHING_STALE_SECONDS = int(os.environ.get("OCB_DISPATCHING_STALE_SECONDS", "60"))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_stale_dispatching(node: "object") -> bool:
    """``dispatching=True`` 是否崩溃遗留的陈旧飞行态(redrive 清理判定用,纯 timestamp)。

    有 dispatching_at 且在途阈值内 → 新鲜在途派发(不清,否则与 start_run 双派发);
    无 dispatching_at(改动前数据)或超阈值 → 陈旧(崩溃遗留)→ 可清。dispatch_error 不在此处理
    (harness 自有重试链,不靠 redrive 清)。"""
    ep = node.run_info.extend_props
    if not ep.get("dispatching"):
        return False
    at = ep.get("dispatching_at")
    if at is None:
        return True
    return (_now_ms() - int(at)) >= _DISPATCHING_STALE_SECONDS * 1000


@dataclass(frozen=True)
class CoopGroupStart:
    """Result of starting a BCN coop group: the group id + its initial session_id."""

    group_id: str
    session_id: str | None


class ExecutionEngine:
    """事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution(协程化,全链路 async)。

    构造期收传输端口(bot/bcs/discover,DI 从配置注入),``_build_*`` 内部 new 引擎自带策略 + 接线 TaskExecutor。
    引擎自当 ResultSink(poller 终态回投→on_report)与 TaskContextBuilder(执行上下文派生),消除后填/back-reach-in。
    on_* 入参统一收口 TaskNodePatch。按事件 + 状态条件分段协调。同 task_id 串行(per-task RLock);
    跨 task 并行。投递/拉群 IO 锁外 await,gather+Semaphore 并发。loop_round 仅升 BBS 时 ++。
    测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。"""

    def __init__(
        self,
        graph,
        *,
        bot=None,
        bcs=None,
        discover=None,
        bcn: BcnService | None = None,
        bcs_identity=None,
        auth_gate=None,
        task_search_skill_enabled: bool = False,
        task_settings=None,
        api_base_url: str = "",
        bot_token_provider=None,
        notify_messages_provider=None,
    ) -> None:
        """graph: TaskGraphService;bot: OpenApiBotPort;bcs: BcsClientPort;discover: BotDiscoverServiceProtocol。
        端口由 DI 从配置注入(local/prod/double 只换端口实现,引擎代码不变)。prod 必传;测试子类覆写
        ``_build_*`` 注入 stub 策略/投递时可省略(走 super 路径默认 berth)。

        BBS 任务模式候选通过 ``bcn.list_bots_by_task_modes``(注入的 BcnService,复用统一 provider 身份)查询。

        ``api_base_url``:任务后端 base url,经 _build_executor 透传给 TaskExecutor→bbs_runner.notify,
        拼成发给胜出 bot 的任务消息(spec §5:主动触发回投路径)。"""
        self._graph = graph
        self._bot = bot
        self._bcs = bcs
        self._discover = discover
        self._bcn = bcn
        self._bcs_identity = bcs_identity
        self._auth_gate = auth_gate
        self._task_search_skill_enabled = task_search_skill_enabled
        self._task_settings = task_settings
        self._api_base_url = api_base_url
        self._bot_token_provider = bot_token_provider
        self._notify_provider = notify_messages_provider
        self._bg_tasks: set[object] = set()
        self._bbs_loop: asyncio.AbstractEventLoop | None = None
        self._bbs_loop_thread: threading.Thread | None = None
        self._bbs_loop_ready = threading.Event()
        self._bbs_loop_guard = threading.RLock()
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.RLock()
        from agentclaw.community.core.task.task_runner.callback_adapter import (
            CallbackAdapter,
        )

        self._cb_adapter = CallbackAdapter()
        self._poller_thread = None
        self._executor = self._build_executor()
        self._planner = self._build_planner()
        self._dispatcher = self._build_dispatcher()
        self._runner = self._build_runner()
        logger.info(
            "[task][engine] 构造完成 bot=%s bcs=%s discover=%s bcn=%s executor=%s",
            type(bot).__name__ if bot is not None else "None",
            type(bcs).__name__ if bcs is not None else "None",
            type(discover).__name__ if discover is not None else "None",
            "BcnService" if bcn is not None else "None",
            type(self._executor).__name__
            if self._executor is not None
            else "None(退桩)",
        )

    # ===== 任务类型分流 seams(委托 self._runner;TaskService.execute 调用)=====
    async def start_coop_group(self, gf: GroupFormation) -> CoopGroupStart:
        """Create the BCN coop group and fetch its initial session_id by default."""
        group_id = await self._runner.form_coop_group(gf)
        session_id = await self._runner.get_group_session(group_id)
        return CoopGroupStart(group_id=group_id, session_id=session_id)

    # ===== protected 工厂方法(测试子类可覆写注入 stub 策略/投递;引擎自带默认接真实端口)=====
    def _build_executor(self):
        if self._bot is None or self._bcs is None:
            logger.warning(
                "[task][engine] execution_backend 不装配(bot=%s bcs=%s)→ form_coop_group/start_run/"
                "BBS start_run 全退 Avernet 桩(grp_<8hex>/stub_<8hex>/无 poller,任务卡 RUNNING 不收敛)。"
                "corp 排查: 确认 DEPLOY_PROFILE=corp + grep [task][corp-task] not configured 看哪个端口空。",
                "None" if self._bot is None else type(self._bot).__name__,
                "None" if self._bcs is None else type(self._bcs).__name__,
            )
            return None
        from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
            TaskExecutor,
        )
        from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
            TaskExecutorResultPoller,
        )
        from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
            PromptFormatterImpl,
        )

        poller = TaskExecutorResultPoller(bot=self._bot, bcs=self._bcs)
        poller.set_on_result(self)
        exe = TaskExecutor(
            bot=self._bot,
            bcs=self._bcs,
            formatter=PromptFormatterImpl(),
            context=self,
            sink=self,
            poller=poller,
            identity_resolver=self._bcs_identity,
            graph=self._graph,
            api_base_url=self._api_base_url,
            bcn=self._bcn,
            bot_token_provider=self._bot_token_provider,
            task_settings=self._task_settings,
            on_bbs_report=self.on_bbs_report,
        )
        import threading as _t

        self._poller_thread = _t.Thread(
            target=poller.run_poll_loop, daemon=True, name="task-exec-poller"
        )
        self._poller_thread.start()
        logger.info(
            "[task][engine] execution_backend 已装配 TaskExecutor + poller 启动 bot=%s bcs=%s",
            type(self._bot).__name__,
            type(self._bcs).__name__,
        )
        return exe

    def _build_planner(self):
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        from agentclaw.community.core.task.task_plan.strategies import (
            GapBasedPlanningStrategy,
            WorkflowPlanningStrategy,
        )

        pool = [WorkflowPlanningStrategy()]
        if self._bot is not None:
            pool.append(GapBasedPlanningStrategy(self._bot))
        else:
            pool.append(GapBasedPlanningStrategy())
        return TaskPlanner(self._graph, pool=pool)

    def _build_dispatcher(self):
        from agentclaw.community.core.task.task_dispatch.dispatcher import (
            TaskDispatcher,
        )
        from agentclaw.community.core.task.task_dispatch.strategies import (
            DirectDispatchStrategy,
            SearchBasedDispatchStrategy,
        )

        pool = [DirectDispatchStrategy()]
        if self._bot is not None and self._discover is not None:
            pool.append(
                SearchBasedDispatchStrategy(
                    self._bot,
                    self._discover,
                    bcn=self._bcn,
                    join_gate=self._auth_gate,
                    use_search_skill=self._task_search_skill_enabled,
                    task_settings=self._task_settings,
                )
            )
        else:
            pool.append(SearchBasedDispatchStrategy())
        return TaskDispatcher(self._graph, pool=pool)

    def _build_runner(self):
        from agentclaw.community.core.task.task_runner.task_runner import TaskRunner

        return TaskRunner(self._graph, execution_backend=self._executor)

    # ===== ResultSink impl:poller 终态回投直接调 on_report =====
    async def report_result(self, data: "TaskCallbackData") -> None:
        """引擎自当 ResultSink:TaskExecutorResultPoller 终态→TaskCallbackData→TaskNodePatch→on_report。
        与外部 HTTP push 回投(TaskLoopCallback.report_result→on_report)收敛同一入口。"""
        patch = self._cb_adapter.adapt(data)
        await self.on_report(patch)

    # ===== TaskContextBuilder impl =====
    def build(self, task_id: str, node_id: str) -> dict:
        """引擎自当 TaskContextBuilder:派生 execute 模式上下文(叶子/聚合均 execute;gap 计算即验收,
        无 verify 模式 dispatch)。siblings_outputs 取本节点的兄弟(DONE 的 run_info.output)。"""
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            return {
                "mode": "execute",
                "parent_node_id": None,
                "parent_spec": None,
                "sibling_outputs": {},
                "node_spec": node.task_spec if node else None,
            }
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        sibling_outputs = {
            s.node_id: s.run_info.output
            for s in siblings
            if s.status == Status.SUCCESS and s.node_id != node_id
        }
        return {
            "mode": "execute",
            "parent_node_id": parent.node_id,
            "parent_spec": parent.task_spec,
            "sibling_outputs": sibling_outputs,
            "node_spec": node.task_spec if node else None,
        }

    def _lock_for(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            lk = self._locks.get(task_id)
            if lk is None:
                lk = threading.RLock()
                self._locks[task_id] = lk
            return lk

    def _root(self, task_id: str) -> TaskNode | None:
        graph = self._graph.query_task_dashboard(task_id)
        for n in graph.tasks:
            if self._graph.get_parent_task(task_id, n.node_id) is None:
                return n
        return None

    def _max_harness(self, task_id: str) -> int:
        cfg = self._graph._execution_config(task_id)
        return int(cfg.get("MAX_HARNESS", _DEFAULT_MAX_HARNESS))

    def _max_plan_round(self, task_id: str) -> int:
        """节点级重规划次数上限 MAX_PLAN_ROUND(default 3)。父节点子全 SUCCESS→gap 未闭→重 plan 产新子,
        每次该路径走一次 +1;达上限父节点 HUNG(不再产子)"""
        cfg = self._graph._execution_config(task_id)
        return int(cfg.get("MAX_PLAN_ROUND", 3))

    def _task_type(self, task_id: str) -> str:
        """Return the immutable execution type recorded on the task graph."""
        try:
            raw = self._graph._execution_config(task_id).get("task_type", "dynamic")
        except Exception:  # noqa: BLE001 - missing/legacy graph defaults to dynamic
            return "dynamic"
        return str(getattr(raw, "value", raw) or "dynamic").strip().lower()

    def _is_external_managed_task(self, task_id: str) -> bool:
        """Whether a third party owns execution and next-node transitions."""
        return self._task_type(task_id) in {"workflow", "yaml"}

    def _is_graph_terminal(self, task_id: str) -> bool:
        """图级终态(DONE/SUCCESS/HUNG)判定。终态后自动驱动(plan/dispatch/harness/回投推进)一律冻结:
        MAX_LOOP 达上限→图 HUNG 后,后续 on_pass/on_miss/on_harness 不再推进(避免 loop_round 失控飙升
        与节点无限增生);on_bbs_report(BBS 接力恢复)是唯一可从 HUNG 恢复的路径,不在本守卫范围。"""
        try:
            return self._graph.query_task_dashboard(task_id).status in {
                Status.DONE,
                Status.SUCCESS,
                Status.HUNG,
            }
        except Exception:  # noqa: BLE001  图不存在等→视为非终态,让正常入口逻辑处理
            return False

    async def _plan_with_retry(
        self, task_id: str, graph, target_node_id: str | None = None
    ):
        """plan 容错重试:planning 调用失败(parse/not_completed/empty 等,gap_detail 以 ``plan_`` 前缀)
        → 重试最多 MAX_HARNESS 次;耗尽后返回最后结果(has_gap=True → 编排核走深度闸门/HUNG)。
        非 ``plan_`` 前缀的空结果(gap 闭 has_gap=F / 真拆不出 has_gap=T)不经重试直接返回。
        planning 是 owner bot 的耗时工作,失败同 exec_error 应重试而非静默 DONE/立即 HUNG。"""
        max_h = self._max_harness(task_id)
        pr = None
        for attempt in range(max_h):
            try:
                pr = await self._planner.plan(graph, target_node_id=target_node_id)
            except Exception as exc:  # 传输/HTTP 异常(sofa_tracer httpx send hook 等)->plan_call_fail 重试,不 abort on_execute
                logger.warning(
                    "[task][plan-retry] task=%s attempt=%d/%d plan() 抛异常(将重试): %r",
                    task_id, attempt + 1, max_h, exc,
                )
                pr = PlanResult(children=[], has_gap=True, gap_detail="plan_call_fail")
            if pr.children or not (pr.gap_detail or "").startswith("plan_"):
                break  # 有子 / 真 gap 闭 / 真拆不出 → 不重试
            logger.warning(
                "[task][plan-retry] task=%s attempt=%d/%d gap_detail=%s",
                task_id,
                attempt + 1,
                max_h,
                pr.gap_detail,
            )
        # 可观测:落最近一次 plan 结果到图 extend_props(dashboard 可见,便于诊断 plan 为何产 []/HUNG)
        self._graph.update_task_graph_info(
            task_id,
            TaskGraphPatch(
                extend_props_patch={
                    "last_plan_target": target_node_id or "<root>",
                    "last_plan_children": len(pr.children),
                    "last_plan_has_gap": pr.has_gap,
                    "last_plan_detail": pr.gap_detail,
                }
            ),
        )
        # 动作历史:PLAN 事件(gap 计算 + 产子结果)挂到被规划目标节点(根 gap 反复计算的轨迹留痕)
        target_id = target_node_id
        if target_id is None:
            root = self._root(task_id)
            target_id = root.node_id if root else None
        if target_id is not None:
            self._log_action(
                task_id,
                target_id,
                NodeAction.PLAN,
                {
                    "target": target_node_id or "<root>",
                    "children": [c.node_id for c in pr.children],
                    "has_gap": pr.has_gap,
                    "gap_detail": pr.gap_detail,
                },
                status_from=Status.PLANNING,
                status_to=Status.PLANNING,
            )
        return pr

    def _mark_planning(self, task_id: str, node_id: str) -> None:
        """节点进入规划委托态:PENDING→PLANNING(幂等,已 PLANNING 不重翻)。
        规划是编排态(Status.PLANNING),不是执行模式:run_mode/assignee 保持 None。
        规划者(owner bot)隐式来自 graph.extend_props.owner_bot_id,不落节点 run_info。
        叶子派发执行时由 _prepare_into 覆写为 single_bot/coop_group/bbs+worker。"""
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        if node is None or node.status not in {Status.PENDING, Status.HUNG}:
            return  # 已 PLANNING / 其他终态 → 幂等不翻
        self._graph.update_task_node_info(
            TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PLANNING)
        )

    def _rollup_done_children_output(
        self, task_id: str, parent_node_id: str
    ) -> dict | None:
        """结构父(非执行)gap 闭翻 DONE 时,把直接已 DONE 子交付物滚入父 ``run_info.output``,
        使祖父一跳 ``done_children`` 看到交付物(否则空 output → gap_no_progress 死循环)。

        存储 output 恒为 dict;单子透传子存储 output(保 ``{"output":<c>}`` 单键形,dashboard 仍展平);
        多子按 node_id 聚合(``{<node_id>: <child.output>}``)。"""
        children = self._graph.get_child_tasks(task_id, parent_node_id)
        done = [
            c for c in children
            if c.status == Status.SUCCESS and c.run_info.output
        ]
        if not done:
            return None
        if len(done) == 1:
            return dict(done[0].run_info.output)
        return {c.node_id: dict(c.run_info.output) for c in done}

    def _build_parent_acceptance_result(
        self, parent: TaskNode, pr: PlanResult | None
    ) -> AcceptanceResult:
        """结构父/根 gap 闭(自身验收通过)翻 DONE 时的父自身验收结果(验收执行者=owner)。

        调用上下文已 ``not pr.has_gap`` → verdict 恒 DONE。``pr.acceptance_verdicts`` 非空 →
        用 owner bot plan 逐条结论填 ``acceptances_metric``(每条 ac 的 reason);否则回退合成"验收通过"。"""
        ac_ids = [a.id for a in parent.task_spec.goal.acceptances]
        verdicts: list[dict] = []
        if pr is not None:
            verdicts = getattr(pr, "acceptance_verdicts", None) or []
        if verdicts:
            vmap: dict[str, dict] = {}
            for v in verdicts:
                if isinstance(v, dict):
                    vmap[str(v.get("ac_id", ""))] = v
            metrics: list[Any] = []
            for ac_id in ac_ids:
                v = vmap.get(ac_id)
                reason = str(v.get("reason") or "") if v else ""
                metrics.append({ac_id: reason or "验收通过(子节点交付达成)"})
            return AcceptanceResult(
                verdict=AcceptanceVerdict.DONE, acceptances_metric=metrics, gaps=[]
            )
        metrics = [{ac_id: "验收通过(子节点交付达成)"} for ac_id in ac_ids]
        if not metrics:
            metrics = [{"all": "验收通过"}]
        return AcceptanceResult(
            verdict=AcceptanceVerdict.DONE, acceptances_metric=metrics, gaps=[]
        )

    def _log_action(
        self,
        task_id: str,
        node_id: str,
        action: NodeAction,
        payload: dict,
        *,
        attempt: int | None = None,
        status_from: Status | None = None,
        status_to: Status | None = None,
    ) -> None:
        """追加节点动作历史快照(append-only;零侵入驱动逻辑)。

        供各逻辑动作(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/TRANSITION)完成时调用,
        纯可观测旁路:不翻态、不读回驱动。``attempt`` 省略时取节点 harness_retries 快照;
        ``status_from``/``status_to`` 省略时由调用方按动作前/后态传(未翻态可不传)。
        """
        if attempt is None:
            node = next(
                (
                    n
                    for n in self._graph.query_task_dashboard(task_id).tasks
                    if n.node_id == node_id
                ),
                None,
            )
            attempt = (
                int(node.run_info.extend_props.get("harness_retries", 0)) if node else 0
            )
        try:
            self._graph.append_action_event(
                task_id,
                node_id,
                action,
                payload,
                attempt=attempt,
                status_from=status_from,
                status_to=status_to,
            )
        except Exception as ex:  # noqa: BLE001  历史快照写入失败不影响驱动
            logger.warning(
                "[task][action-log] task=%s node=%s action=%s 追加失败:%s",
                task_id,
                node_id,
                action.value,
                ex,
            )

    def _static_runtime(self, task_id: str):
        from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition
        from agentclaw.community.core.task.task_plan.static_plan_runtime import StaticPlanRuntime
        cfg = self._graph._execution_config(task_id)
        # 判据:cfg 含 ``static_plan_id`` 或 ``static_plan_yaml`` 任一即视为预置模板 plan(不依赖 task_type 字符串);
        # task_type 仍可显式 STATIC_PLAN 兼容旧调用方,但默认 dynamic caller 经 execute 内容路由命中后,
        # 也会在此处回填 static_plan_id/static_plan_yaml 进入预置 plan runtime。
        template_id = cfg.get("static_plan_id")
        yaml_text = cfg.get("static_plan_yaml")
        if not template_id and not yaml_text and cfg.get("task_type") != "static_plan":
            return None
        if not yaml_text and template_id:
            # 显式只传 task_type/static_plan_id 未带 yaml → 从仓库 plans 懒加载
            from pathlib import Path
            plans_dir = Path(__file__).resolve().parents[1] / "task_plan" / "plans"
            plans_path = plans_dir / f"{template_id}.yaml"
            if not plans_path.exists():
                return None
            yaml_text = plans_path.read_text(encoding="utf-8")
        try:
            definition = StaticPlanDefinition.from_yaml(str(yaml_text) if yaml_text else "")
            runtime = StaticPlanRuntime(definition, dict(cfg.get("template_input") or {}))
        except Exception:
            logger.exception(
                "[task][static-plan] runtime init failed task=%s template=%s",
                task_id,
                template_id,
            )
            raise
        logger.debug(
            "[task][static-plan] runtime loaded task=%s template=%s",
            task_id,
            template_id or definition.template_id,
        )
        return runtime

    async def _on_static_execute(self, task_id: str) -> None:
        runtime = self._static_runtime(task_id)
        if runtime is None:
            return
        graph = self._graph.query_task_dashboard(task_id)
        root = self._root(task_id)
        logger.info(
            "[task][static-plan] execute task=%s template=%s graph_status=%s root=%s relation_count=%s",
            task_id,
            runtime.definition.template_id,
            graph.status.value,
            root.node_id if root else None,
            len(graph.relations),
        )
        if root is None or graph.relations:
            logger.info(
                "[task][static-plan] execute task=%s skip materialize root_exists=%s already_materialized=%s",
                task_id,
                root is not None,
                bool(graph.relations),
            )
            return
        # 分波揭示:首波仅入图 depends_on 为空的节点(root 结构父);
        # 后续波由 _on_static_report -> _static_next_wave 按依赖完成度增量补入,
        # 避免 dashboard 一开始就暴露完整定制计划。
        all_nodes = runtime.nodes(task_id, root.task_spec)
        wave0_ids = {d.node_id for d in runtime.definition.nodes if not d.depends_on}
        nodes = [n for n in all_nodes if n.node_id in wave0_ids]
        if not nodes:
            logger.info(
                "[task][static-plan] execute task=%s no wave0 nodes, skip materialize",
                task_id,
            )
            return
        self._graph.add_task_nodes(nodes, root.node_id)
        logger.info(
            "[task][static-plan] materialized wave0 task=%s node_count=%s nodes=%s",
            task_id,
            len(nodes),
            [n.node_id for n in nodes],
        )
        side: list[tuple] = []
        await self._prepare_static(task_id, runtime, side)
        logger.info(
            "[task][static-plan] execute task=%s prepared side_effects=%s",
            task_id,
            [item[0] for item in side],
        )
        await self._drain(task_id, side)

    def _static_next_wave(self, task_id: str, runtime) -> int:
        """分波揭示:补加可入图的下一波(defs 全 DONE 且未在图中)。

        结构父统一用 root(全程 PLANNING,可委托,复用 add_task_nodes 触发 cond_c);
        实体 DAG 依赖边(deps -> node)由 task_graph_service.add_relations 补写,
        使多入合并点(如 strategy_approval 依赖 risk/marketing/crowd/product 四路)
        在 dashboard 上可渲染为 DAG。返回本次新入图节点数。"""
        graph = self._graph.query_task_dashboard(task_id)
        done = {n.node_id for n in graph.tasks if n.status == Status.SUCCESS}
        in_graph = {n.node_id for n in graph.tasks}
        wave_defs = [
            d for d in runtime.definition.nodes
            if d.node_id not in in_graph and set(d.depends_on).issubset(done)
        ]
        if not wave_defs:
            return 0
        root = self._root(task_id)
        if root is None:
            return 0
        wave_ids = {d.node_id for d in wave_defs}
        all_nodes = runtime.nodes(task_id, root.task_spec)
        # attach_dependency=False:后续波节点入图不挂 root 锚定边,真依赖边由下面 add_relations
        # 补 deps→X(crowd/product 依赖 marketing、approval 四路合并、impl 依赖 approval 等),
        # 避免 dashboard 把 root(okr-implementation) 误连到所有后续节点。
        wave_nodes = [n for n in all_nodes if n.node_id in wave_ids]
        self._graph.add_task_nodes(wave_nodes, root.node_id, attach_dependency=False)
        edges: list[tuple[str, str]] = []
        for d in wave_defs:
            edges.extend((dep, d.node_id) for dep in d.depends_on)
        if edges:
            self._graph.add_relations(task_id, edges)
        logger.info(
            "[task][static-plan] wave added task=%s count=%s nodes=%s edges=%s",
            task_id, len(wave_defs), [d.node_id for d in wave_defs], len(edges),
        )
        return len(wave_defs)

    def _static_auto_report_on(self, task_id: str) -> bool:
        """演示自驱开关:开启后静态 plan 节点不做真实派发/拉群,转为后台自回投 mock 结果,
        复用同一 on_report 通路推进图态,便于上报/skill 未就绪时也能跑通全链路。
        优先级:按任务 execution_config.static_auto_report(bool) → 服务端 env OCB_TASK_STATIC_AUTO_REPORT。"""
        # 与 _static_runtime 同判据:预置模板 plan 任务才需要 static_auto_report 开关。
        cfg = self._graph._execution_config(task_id)
        if not (cfg.get("static_plan_id") or cfg.get("static_plan_yaml") or cfg.get("task_type") == "static_plan"):
            return False
        flag = cfg.get("static_auto_report")
        if isinstance(flag, bool):
            return flag
        return os.environ.get("OCB_TASK_STATIC_AUTO_REPORT", "").lower() in {"1", "true", "yes", "on"}

    async def _prepare_static(self, task_id: str, runtime, side: list[tuple]) -> None:
        # cascade loop:enabled_when 未满足的节点(skip)被翻 DONE 后,会解锁依赖它的后续节点
        # (如 implementation skip 后 notify_done 的 depends_on={implementation} 满足),
        # 必须立即 _static_next_wave 揭示该后续波并继续 dispatch,否则后续节点(如 notify_done)
        # 永不入图,terminal 因 all_in_graph=False 永不翻 DONE,导致 graph 卡 EXECUTING、root 卡
        # "尚未开始"。max_rounds 兜底防依赖环导致的无限揭示。
        max_rounds = 8
        for round_idx in range(max_rounds):
            graph = self._graph.query_task_dashboard(task_id)
            readiness = runtime.ready(graph)
            logger.info(
                "[task][static-plan] prepare task=%s round=%s ready=%s skipped=%s",
                task_id,
                round_idx,
                [node.node_id for node in readiness.ready],
                [node.node_id for node in readiness.skipped],
            )
            for node in readiness.skipped:
                logger.info(
                    "[task][static-plan] skip node task=%s node=%s reason=enabled_when",
                    task_id,
                    node.node_id,
                )
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        status=Status.DONE,
                        output_patch={"skipped": True},
                        extend_props_patch={"static_blocked": None},
                    )
                )
            if readiness.ready:
                logger.info(
                    "[task][static-plan] dispatch ready nodes task=%s round=%s nodes=%s",
                    task_id,
                    round_idx,
                    [node.node_id for node in readiness.ready],
                )
                # Static nodes use the YAML-bound bot directly; skip catalog search
                # and claim-join so dependencies are never dispatched ahead of time
                # and the bound bot_id (e.g. strategy_approval/implementation) is
                # honored instead of being replaced by whatever catalog returns.
                await self._prepare_static_into(task_id, runtime, readiness.ready, side)
            # 本轮既无 skip 也无 ready:已达稳态,退出 cascade。
            if not readiness.skipped and not readiness.ready:
                break
            # 本轮有 skip:已把节点翻 DONE,揭示依赖它的后续波(notify_done 等),下一轮 ready 它并 dispatch。
            if readiness.skipped:
                self._static_next_wave(task_id, runtime)
        else:
            logger.warning(
                "[task][static-plan] prepare cascade hit max_rounds=%s task=%s,可能存在依赖环",
                max_rounds, task_id,
            )

    async def _prepare_static_into(
        self, task_id: str, runtime, ready_nodes, side: list[tuple]
    ) -> None:
        """Static DAG 节点跳过搜推,直接用 YAML 绑定的 bot 指派。

        type=bot → single_bot + assignee=definition.bot_id → start_run;
        type=collaboration → pending_group_formation → form_coop_group。
        不进 dispatcher.dispatch / 不查 catalog / 不做 claim_join,故未 ready 的依赖节点
        (strategy_approval/implementation)不会被提前搜推成 MISS/claim_mode_off,且 YAML 绑定
        的 bot_id 永远被尊重(不会被 catalog 命中的其他 bot 替换)。依赖顺序由 runtime.ready 保证。"""
        to_run: list[TaskNode] = []
        # 固定流程默认真实上报 + fallback 兜底:每个真实派发节点都额外调度一条延迟 mock 上报,
        # 真实回投先到则自跳过(mock 兜底延迟改为随机:单 bot 20-40s,协作群 40-80s,无固定超时),
        # 否则超时后由 mock 推进;auto=True 演示模式仍走短延迟(_static_auto_report 内按 mode 取 delay)。
        auto_nodes: list[TaskNode] = []
        for node in ready_nodes:
            definition = runtime.by_id.get(node.node_id)
            if definition is None:
                logger.warning(
                    "[task][static-plan] task=%s node=%s 无定义,跳过",
                    task_id,
                    node.node_id,
                )
                continue
            # notify 终端节点:任务实施完成通知触发用户(DingTalk account_id),不派发 bot;
            # 受信方=execution_config.owner_account_id(缺省 guoke.gk),凭证未配/投递失败→provider.send
            # 返 None,节点仍置 DONE 不阻塞 graph 终态(与 NullProvider 降级语义一致);不走 on_report 避免
            # acceptance PENDING+PASS 非法翻态,PENDING→DONE 经 status 直驱表允许。
            if getattr(definition, "node_type", "bot") == "notify":
                cfg = self._graph._execution_config(task_id)
                owner_acct = cfg.get("owner_account_id") if isinstance(cfg, dict) else None
                recipient = owner_acct or "guoke.gk"
                logger.info(
                    "[task][static-plan] notify task=%s node=%s recipient=%s template=%s",
                    task_id, node.node_id, recipient, runtime.definition.template_id,
                )
                ext_id = None
                send_err: str | None = None
                provider = self._notify_provider
                if provider is not None:
                    try:
                        from agentclaw.community.plugin_api.notify_sender import NotifyMessage
                        title = f"OKR 实施完成：{runtime.definition.template_id}"
                        body = (
                            f"OKR 任务已实施完成。\n模板: {runtime.definition.template_id}\n"
                            f"通过 OKR实现Bot 完成风险评估 / 营销策略 / 审核 / 投放实施流程。"
                        )
                        ext_id = provider.send(
                            NotifyMessage(title=title, body=body, recipient=recipient),
                            channel="tc_card",
                        )
                    except Exception as ex:  # noqa: BLE101 provider never raise,防实现越界
                        send_err = f"{type(ex).__name__}: {ex}"
                        logger.warning(
                            "[task][static-plan] notify send 异常 task=%s node=%s: %s",
                            task_id, node.node_id, send_err,
                        )
                else:
                    logger.info(
                        "[task][static-plan] notify provider 未注入(NullProvider noop) task=%s node=%s",
                        task_id, node.node_id,
                    )
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id, node_id=node.node_id,
                        status=Status.DONE, run_mode="notify",
                        assignee=f"dingtalk:{recipient}",
                        output_patch={
                            "notify_result": {
                                "recipient": recipient,
                                "sent": ext_id is not None,
                                "external_id": ext_id,
                                "error": send_err,
                                "channel": "tc_card",
                            }
                        },
                    )
                )
                logger.info(
                    "[task][static-plan] notify dispatched task=%s node=%s sent=%s ext_id=%s",
                    task_id, node.node_id, ext_id is not None, ext_id,
                )
                continue
            # bbs_handoff 旁路:不可实现任务转 BBS 广场(与 approval 并行,不阻塞主实施线)。
            # 阶段① 入广场:写 assignee=安全架构师 + bbs_status=posted_in_square(dashboard 即可点开安全架构师
            # bot 主会话,不再空 assignee 致点不开);30s 后由 _bbs_handoff_claim 被接:真实 start_run 发
            # 安全架构师 并落 session_id + 翻 claimed。
            if getattr(definition, "node_type", "bot") == "bbs_handoff":
                bot_id = getattr(definition, "bot_id", None) or ""
                static_input = node.task_spec.context.extend_props.get("static_input") or {}
                items = static_input.get("unhandled_tasks")
                if isinstance(items, list) and items:
                    pass  # 真实风险评估上报了结构化不可实现任务 → 原样用其内容
                else:
                    # 真实评估为自然语言、无结构化 unhandled_tasks → 兜底 mock 占位(与 _static_auto_report
                    # 的 _UHT_MOCK 同口径),安全架构师 不致收到空;真实检测到时优先用真实内容。
                    items = list(_UHT_MOCK)
                    logger.info(
                        "[task][static-plan] bbs_handoff 真实无结构化 unhandled_tasks,兜底 mock 占位 task=%s node=%s",
                        task_id, node.node_id,
                    )
                logger.info(
                    "[task][static-plan] task=%s node=%s -> bbs_handoff posted items=%s rnd_bot=%s",
                    task_id, node.node_id,
                    items if isinstance(items, list) else type(items).__name__, bot_id,
                )
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="bbs",
                        assignee=bot_id,
                        extend_props_patch={
                            "dispatching": True,
                            "dispatching_at": _now_ms(),
                            "bbs_status": "posted_in_square",
                            "bbs_owner": "",
                            "bbs_handed_to": "",
                            "bbs_task_items": items,
                        },
                    )
                )
                side.append(("bbs_handoff", node, bot_id, items))
                continue
            # auto 模式仍走真实派发(group/run),不跳过;仅额外调度延迟 mock 上报(见 _static_auto_report)
            gf = node.run_info.extend_props.get("pending_group_formation")
            if gf is not None:
                gf.extend_props.setdefault(
                    "task_objective", node.task_spec.goal.objective
                )
                gf.extend_props.setdefault(
                    "task_instruction", node.task_spec.metadata.instruction
                )
                gf.extend_props.setdefault(
                    "acceptances",
                    [
                        {"id": a.id, "description": a.description}
                        for a in node.task_spec.goal.acceptances
                    ],
                )
                logger.info(
                    "[task][static-plan] task=%s node=%s → group(collab=%s bot_ids=%s) 跳过搜推",
                    task_id,
                    node.node_id,
                    gf.collab_mode,
                    list(gf.bot_ids),
                )
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="coop_group",
                        extend_props_patch={"dispatching": True, "dispatching_at": _now_ms()},
                    )
                )
                side.append(("group", node, gf))
                auto_nodes.append(node)  # 拉群真实派发 + 调度兜底 mock 上报
                continue
            bot_id = getattr(definition, "bot_id", None)
            if bot_id:
                node.run_info.run_mode = "single_bot"
                node.run_info.assignee = bot_id
                logger.info(
                    "[task][static-plan] task=%s node=%s → run(assignee=%s) 跳过搜推",
                    task_id,
                    node.node_id,
                    bot_id,
                )
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="single_bot",
                        assignee=bot_id,
                        extend_props_patch={"dispatching": True, "dispatching_at": _now_ms()},
                    )
                )
                to_run.append(node)
                auto_nodes.append(node)  # 单 bot 真实派发 + 调度兜底 mock 上报
            else:
                logger.warning(
                    "[task][static-plan] task=%s node=%s 无 bot 绑定也无 group,跳过",
                    task_id,
                    node.node_id,
                )
        if to_run:
            side.append(("run", to_run))
        if auto_nodes:
            side.append(("auto", auto_nodes))

    async def _static_auto_report(self, task_id: str, node_id: str) -> None:
        """固定流程兜底上报:节点真实派发后,若在随机延迟内(单 bot 20-40s,协作群 40-80s;取消固定 80s 超时)无真实回投,
        则以 mock 信息回投 PASS→SUCCESS 推进图态,避免单节点不上报致整流程卡死;
        auto=True 演示模式改走短("demo")延迟。

        mock 只替代"上报信息",不替代派发——拉群/发消息仍走真实路径(_drain group/run)。
        延迟到期后仅在节点处 RUNNING 态(真实派发成功)时才回投 PASS→SUCCESS;
        若派发失败留 PENDING / 已被真实上报翻 DONE,则跳过(暴露真实失败,不掩盖,不重复翻态)。"""
        runtime = self._static_runtime(task_id)
        if runtime is None:
            return
        auto = self._static_auto_report_on(task_id)
        delay = (
            self._static_auto_report_delay(task_id)
            if auto
            else self._static_mock_fallback_delay(task_id, node_id)
        )
        logger.info(
            "[task][static-plan] %s scheduled task=%s node=%s in %.2fs",
            "auto-report" if auto else "fallback-report",
            task_id, node_id, delay,
        )
        await asyncio.sleep(delay)
        # 仅真实派发成功(RUNNING)才 mock 上报;派发失败/已真实上报则跳过
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        if node is None or node.status != Status.RUNNING:
            logger.info(
                "[task][static-plan] auto-report skip task=%s node=%s status=%s (非 RUNNING,留给真实派发/上报)",
                task_id, node_id, node.status.value if node is not None else None,
            )
            return
        definition = runtime.by_id.get(node_id)
        # 兜底产出摘要:用各节点真实产出(剧本)代替 [auto] 占位,使下游 ## 上游产出正文 可读、流程不因
        # 无意义占位文本读不通。真实上报先到则本兜底自跳过,不被使用。
        mock_result: Any = {
            "summary": _STATIC_MOCK_SUMMARY.get(node_id, f"[auto] node={node_id}"),
            "random": f"{random.randrange(10 ** 6):06d}",
        }
        if definition is not None and any(
            isinstance(v, str) and v.startswith("$.result.approved")
            for v in definition.output.values()
        ):
            mock_result["approved"] = True
        # 造不可实现任务列表(仅当节点 output 含 $.result.unhandled_tasks,如 risk_assessment 群):
        # 大促剧本兜底=舆情监控方案缺失(内部无舆情监控 bot,转 BBS 安全架构师)。
        if definition is not None and any(
            isinstance(v, str) and v.startswith("$.result.unhandled_tasks")
            for v in definition.output.values()
        ):
            mock_result["unhandled_tasks"] = [dict(t) for t in _UHT_MOCK]
        logger.info(
            "[task][static-plan] auto-report fire task=%s node=%s mock=%s -> on_report",
            task_id, node_id, mock_result,
        )
        await self.on_report(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                acceptance_result=AcceptanceResult(
                    verdict=AcceptanceVerdict.DONE,
                    acceptances_metric=["static_auto"],
                ),
                output_patch={"result": mock_result},
                extend_props_patch={"dispatching": None},
            )
        )


    async def _static_bbs_handoff_auto_report(
        self, task_id: str, node_id: str, rnd_bot_id: str, items: Any
    ) -> None:
        """固定流程 bbs_handoff 兜底上报:与节点级 _static_auto_report 同语义——真实 poller 先到则自跳过(节点非 RUNNING);
        否则随机延迟(单 bot/node 同单 bot 20-40s)后 mock PASS→SUCCESS,避免旁路节点长挂致整流程不终态。
        auto 演示模式用短 demo 延迟。"""
        auto = self._static_auto_report_on(task_id)
        delay = (
            self._static_auto_report_delay(task_id)
            if auto
            else self._static_mock_fallback_delay(task_id, node_id)
        )
        logger.info(
            "[task][static-plan] %s scheduled task=%s node=%s in %.2fs",
            "bbs-auto-report" if auto else "bbs-fallback-report",
            task_id, node_id, delay,
        )
        await asyncio.sleep(delay)
        g2 = self._graph.query_task_dashboard(task_id)
        n2 = next((x for x in g2.tasks if x.node_id == node_id), None)
        if n2 is None or n2.status != Status.RUNNING:
            logger.info(
                "[task][static-plan] bbs_handoff report-skip task=%s node=%s status=%s (真实闭环/未RUNNING)",
                task_id, node_id, n2.status.value if n2 is not None else None,
            )
            return
        await self.on_report(
            TaskNodePatch(
                task_id=task_id, node_id=node_id,
                acceptance_result=AcceptanceResult(
                    verdict=AcceptanceVerdict.DONE,
                    acceptances_metric=["bbs_handoff"],
                ),
                output_patch={
                    "result": {
                        "summary": _STATIC_MOCK_SUMMARY.get(node_id, f"[bbs-handoff] node={node_id}"),
                        "handed_to": rnd_bot_id,
                        "items": items,
                        "random": f"{random.randrange(10 ** 6):06d}",
                    }
                },
                extend_props_patch={"dispatching": None},
            )
        )

    def _static_fallback_delay(self, task_id: str) -> float:
        """固定流程真实上报的兜底超时:节点真实派发后,若该时长内无真实回投,则以 mock 兜底推进,
        避免整流程因单节点不上报而卡死。仅固定 plan 任务生效(由调度点保证)。
        优先级:execution_config.static_fallback_timeout → env OCB_TASK_STATIC_FALLBACK_TIMEOUT → 80.0。"""
        cfg = self._graph._execution_config(task_id)
        v = cfg.get("static_fallback_timeout")
        if v in (None, ""):
            raw = os.environ.get("OCB_TASK_STATIC_FALLBACK_TIMEOUT")
            v = raw if raw not in (None, "") else None
        try:
            return float(v) if v is not None else 80.0
        except (TypeError, ValueError):
            return 80.0

    def _static_mock_fallback_delay(self, task_id: str, node_id: str) -> float:
        """固定流程真实上报兜底 mock 的随机延迟(取消固定 fallback 超时):
        单 bot 节点随机 20-40s,协作群节点随机 40-80s;auto 演示模式仍走 _static_auto_report_delay 短延迟。
        无法定 node_type 时按单 bot 处理(20-40)。真实回投先到则本兜底自跳过,不被使用。"""
        runtime = self._static_runtime(task_id)
        definition = runtime.by_id.get(node_id) if runtime is not None else None
        is_group = definition is not None and definition.node_type == "collaboration"
        return random.uniform(40.0, 80.0) if is_group else random.uniform(20.0, 40.0)

    def _static_auto_report_delay(self, task_id: str) -> float:
        """自驱 mock 上报延迟秒数:execution_config.static_auto_report_delay →
        env OCB_TASK_STATIC_AUTO_REPORT_DELAY → random.uniform(20,60)(每节点完成节奏不一,
        演示时能看出节点状态逐次流转而非瞬间全 DONE)。"""

        cfg = self._graph._execution_config(task_id)
        v = cfg.get("static_auto_report_delay")
        if v in (None, ""):
            raw = os.environ.get("OCB_TASK_STATIC_AUTO_REPORT_DELAY")
            v = raw if raw not in (None, "") else None
        try:
            return float(v) if v is not None else random.uniform(20.0, 60.0)
        except (TypeError, ValueError):
            return random.uniform(20.0, 60.0)

    def _bbs_handoff_delay(self, task_id: str) -> float:
        """BBS 交接"被接"延迟秒数(①入广场→②被接):execution_config.bbs_handoff_claim_delay →
        env OCB_BBS_HANDOFF_CLAIM_DELAY → 30.0。"""
        cfg = self._graph._execution_config(task_id)
        v = cfg.get("bbs_handoff_claim_delay")
        if v in (None, ""):
            raw = os.environ.get("OCB_BBS_HANDOFF_CLAIM_DELAY")
            v = raw if raw not in (None, "") else None
        try:
            return float(v) if v is not None else 30.0
        except (TypeError, ValueError):
            return 30.0

    def _on_bbs_handoff_done(self, t: "asyncio.Task") -> None:
        """BBS 交接后台任务完成:脱离跟踪集 + 异常可见。"""
        self._bg_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("[task][static-plan] bbs_handoff bg task 异常: %s", exc, exc_info=exc)

    async def _bbs_handoff_claim(
        self, task_id: str, node_id: str, rnd_bot_id: str, items: Any
    ) -> None:
        """BBS 交接"被接":延迟后真实 start_run 发安全架构师,成功翻 claimed + 展示安全架构师;
        auto 模式随即 mock 上报 PASS→SUCCESS(派发完成即交接完成),真实模式留给 poller。
        失败留 PENDING(post_failed),不掩盖真实派发失败。"""
        delay = self._bbs_handoff_delay(task_id)
        logger.info(
            "[task][static-plan] bbs_handoff claim scheduled task=%s node=%s in %.1fs rnd_bot=%s",
            task_id, node_id, delay, rnd_bot_id,
        )
        await asyncio.sleep(delay)
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        if node is None or node.status != Status.PENDING:
            logger.info(
                "[task][static-plan] bbs_handoff skip task=%s node=%s status=%s (非 PENDING)",
                task_id, node_id, node.status.value if node is not None else None,
            )
            return
        # ① 图中保留 bbs 来源语义；实际交接给胜出的研发 bot 时，构造 single_bot
        #    执行视图并走统一 start_run，避免把已 claim 的任务再次投回 BBS 广场。
        node.run_info.assignee = rnd_bot_id
        node.run_info.run_mode = "bbs"
        with self._lock_for(task_id):
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id, node_id=node_id,
                    run_mode="bbs", assignee=rnd_bot_id,
                    extend_props_patch={"bbs_owner": rnd_bot_id, "bbs_handed_to": rnd_bot_id},
                )
            )
        delivery_node = replace(
            node,
            run_info=replace(
                node.run_info,
                run_mode="single_bot",
                output=dict(node.run_info.output),
                extend_props=dict(node.run_info.extend_props),
                action_log=list(node.run_info.action_log),
            ),
        )
        # ② start_run([delivery_node]) 真实投递给研发 bot；持久化节点保持 bbs 语义。
        ok = False
        node.run_info.run_mode = "single_bot"
        try:
            results = await self._runner.start_run([delivery_node])
            ok = bool(results[0]) if results else False
        except Exception as ex:  # noqa: BLE101
            logger.warning(
                "[task][static-plan] bbs_handoff 安全架构师 派发异常 task=%s node=%s rnd_bot=%s: %s",
                task_id, node_id, rnd_bot_id, ex,
            )
            ok = False
        finally:
            node.run_info.run_mode = "bbs"
            # 清掉临时 single_bot 派发(单 bot→group 旁路)在 extend_props 泄漏的 actual_run_mode,
            # 否则 effective_run_mode() 优先读 actual_run_mode 仍判 single_bot,致 dashboard 误显单人。
            node.run_info.extend_props["actual_run_mode"] = "bbs"
        with self._lock_for(task_id):
            if not ok:
                # 真派发失败:不回 PENDING/不清 assignee,直接 no-op 翻 RUNNING(bbs 路径仍由兜底推进),
                # dashboard 可按 assignee 点开安全架构师 主会话;dispatch_error 留痕便于排查。
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id, node_id=node_id,
                        status=Status.RUNNING, run_mode="bbs", assignee=rnd_bot_id,
                        extend_props_patch={
                            "dispatching": None,
                            "actual_run_mode": "bbs",
                            "dispatch_error": "bbs_rnd_dispatch_fallback_noop",
                            "bbs_status": "claimed_by_rnd",
                        },
                    )
                )
            else:
                # 安全架构师 真发成功:session_id/run_id 已由 dispatcher 写入 extend_props,bbs 翻 RUNNING+claimed。
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id, node_id=node_id,
                        status=Status.RUNNING, run_mode="bbs", assignee=rnd_bot_id,
                        extend_props_patch={"dispatching": None, "actual_run_mode": "bbs", "bbs_status": "claimed_by_rnd"},
                    )
                )
        logger.info(
            "[task][static-plan] bbs_handoff claimed task=%s node=%s rnd_bot=%s items=%s",
            task_id, node_id, rnd_bot_id,
            items if isinstance(items, list) else type(items).__name__,
        )
        # 交接完成:固定流程 bbs_handoff 始终调度兜底上报——真实 poller 在 timeout 内闭环则自跳过;
        # 否则超时后 mock PASS→SUCCESS,避免旁路节点长挂致整个固定流程永不终态。
        # auto 演示模式用短延迟;默认(非 auto)用 fallback 超时(80s)兜底。
        _bbs_t = asyncio.create_task(
            self._static_bbs_handoff_auto_report(task_id, node_id, rnd_bot_id, items)
        )
        self._bg_tasks.add(_bbs_t)
        _bbs_t.add_done_callback(self._on_auto_report_done)

    async def _on_static_report(self, task_id: str, node_id: str) -> None:
        runtime = self._static_runtime(task_id)
        if runtime is None:
            return
        graph = self._graph.query_task_dashboard(task_id)
        reported = next((n for n in graph.tasks if n.node_id == node_id), None)
        definition = runtime.by_id.get(node_id)
        logger.info(
            "[task][static-plan] report task=%s node=%s node_found=%s definition_found=%s status=%s output_keys=%s",
            task_id,
            node_id,
            reported is not None,
            definition is not None,
            reported.status.value if reported is not None else None,
            sorted(reported.run_info.output) if reported is not None else [],
        )
        if reported is not None and definition is not None:
            raw = dict(reported.run_info.output)
            mapped: dict[str, Any] = {}
            for key, expression in definition.output.items():
                if expression in ("$.result", "$.report.result"):
                    mapped[key] = raw.get("result", raw)
                elif expression.startswith("$.result."):
                    current: Any = raw.get("result", raw)
                    for part in expression[len("$.result."):].split("."):
                        current = current.get(part) if isinstance(current, dict) else None
                    mapped[key] = current
            if mapped:
                self._graph.update_task_node_info(
                    TaskNodePatch(task_id=task_id, node_id=node_id, output_patch=mapped)
                )
        # 分波揭示:上报后先补加可入图的新波节点(结构父=root,DAG 依赖边经 add_relations 补),
        # 再 prepare,使新波节点在本轮即可被 readiness 选中派发。
        wave_added = self._static_next_wave(task_id, runtime)
        side: list[tuple] = []
        await self._prepare_static(task_id, runtime, side)
        current = self._graph.query_task_dashboard(task_id)
        # terminal 必须要求"全部定义节点均已入图" + 已入图节点全部终态,
        # 否则分波会导致未入图节点被遗漏而误判提前 DONE。
        all_def_ids = set(runtime.by_id)
        materialized_ids = {n.node_id for n in current.tasks} & all_def_ids
        all_in_graph = materialized_ids == all_def_ids
        terminal = all_in_graph and all(
            n.status in {Status.DONE, Status.SUCCESS, Status.FAILED, Status.HUNG}
            for n in current.tasks
            if n.node_id in all_def_ids
        )
        logger.info(
            "[task][static-plan] report processed task=%s node=%s wave_added=%s next_side_effects=%s terminal=%s materialized=%s/%s node_states=%s",
            task_id,
            node_id,
            wave_added,
            [item[0] for item in side],
            terminal,
            len(materialized_ids),
            len(all_def_ids),
            {n.node_id: n.status.value for n in current.tasks if n.node_id in all_def_ids},
        )
        if terminal:
            # 终态镜像:root 先翻 SUCCESS(下方 if 块),再 _sync_graph_status_to_root 镜像 graph(不再 graph 独立先写 status)
            root_node = next((n for n in current.tasks if n.node_id == task_id), None)
            if root_node is not None and root_node.status not in {Status.DONE, Status.SUCCESS, Status.FAILED, Status.HUNG}:
                try:
                    self._graph.update_task_node_info(
                        TaskNodePatch(task_id=task_id, node_id=task_id, status=Status.SUCCESS)
                    )
                except Exception as ex:  # noqa: BLE101 翻态非法不阻塞 graph DONE
                    logger.warning(
                        "[task][static-plan] root flip-to-DONE skipped task=%s status=%s: %s",
                        task_id, root_node.status.value, ex,
                    )
            self._sync_graph_status_to_root(task_id)
            logger.info("[task][static-plan] completed task=%s template=%s", task_id, runtime.definition.template_id)
            return
        await self._drain(task_id, side)

    # ===== on_execute =====
    async def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph 后,条件 a(根 PENDING)→ plan(None 自发现根)→add→dispatch→start_run。"""
        if self._is_external_managed_task(task_id):
            logger.info("[task][on_execute] task=%s external-managed, skip Avernet orchestration", task_id)
            return
        if self._is_graph_terminal(task_id):
            logger.info(
                "[task][on_execute] task=%s 图已终态(%s),冻结驱动",
                task_id,
                self._graph.query_task_dashboard(task_id).status.value,
            )
            return
        if self._static_runtime(task_id) is not None:
            await self._on_static_execute(task_id)
            return
        side: list[tuple] = []
        with self._lock_for(task_id):
            root = self._root(task_id)
            logger.info(
                "[task][on_execute] task=%s root=%s status=%s",
                task_id,
                root.node_id if root else None,
                root.status if root else None,
            )
            if root is None or root.status != Status.PENDING:
                logger.info(
                    "[task][on_execute] task=%s 非条件 a(根非 PENDING),跳过", task_id
                )
                return
            graph = self._graph.query_task_dashboard(task_id)
            self._mark_planning(task_id, root.node_id)  # root 由 owner bot 规划
            pr = await self._plan_with_retry(
                task_id, graph
            )  # None → 自发现根(含 plan 容错重试)
            logger.info(
                "[task][on_execute] task=%s plan 产 %d 子节点: %s",
                task_id,
                len(pr.children),
                [n.node_id for n in pr.children],
            )
            if pr.children:
                self._graph.add_task_nodes(pr.children, root.node_id)
                await self._prepare_into(task_id, side)
            elif not pr.has_gap:
                self._maybe_finish_graph(task_id, pr)  # 根 gap 初始即闭(罕见)
            else:
                self._hung_and_escalate(
                    task_id, root.node_id, "root_gap_no_decompose"
                )  # 有 gap 拆不出 → HUNG 升 BBS
        await self._drain(task_id, side)

    async def redrive(self, task_id: str) -> None:
        """Recovery resume entrypoint: re-dispatch pending leaf nodes of a
        hydrated non-terminal task after an instance restart / rolling deploy.

        Mirrors the dispatch tail of ``on_execute`` but starts from the
        already-hydrated graph (``query_task_dashboard`` hydrates from the shared
        store on cache miss): collect未派发 PENDING 叶 → dispatch → start_run.
        Only non-terminal runtime statuses are recoverable (the worker filters),
        and terminal graphs freeze immediately. Idempotent: ``_prepare_into``
        skips nodes already ``dispatching`` and the status machine guards repeats.
        """
        if self._is_external_managed_task(task_id):
            logger.info("[task][redrive] task=%s external-managed, skip Avernet redrive", task_id)
            return
        if self._is_graph_terminal(task_id):
            logger.info("[task][redrive] task=%s 图已终态,冻结重投", task_id)
            return
        side: list[tuple] = []
        with self._lock_for(task_id):
            graph = self._graph.query_task_dashboard(task_id)
            logger.info(
                "[task][redrive] task=%s status=%s resume dispatch",
                task_id,
                graph.status.value,
            )
            # redrive unstick:清崩溃在途遗留的陈旧 dispatching=True(超阈值的飞行态),否则
            # _prepare_into/harness 永久跳过 → 节点卡死 PENDING 无法重派。新鲜在途(dispatching_at
            # < 阈值)不动——redrive 可在本实例正在驱动时被周期 recovery 触发,盲清会与在途 start_run 双派发。
            for _rn in graph.tasks:
                if _rn.status == Status.PENDING and _is_stale_dispatching(_rn):
                    logger.info(
                        "[task][redrive] task=%s node=%s 清陈旧飞行态 dispatching(崩溃遗留)→可重派",
                        task_id, _rn.node_id,
                    )
                    self._graph.update_task_node_info(
                        TaskNodePatch(
                            task_id=task_id, node_id=_rn.node_id,
                            extend_props_patch={"dispatching": None, "dispatching_at": None},
                        )
                    )
            await self._prepare_into(task_id, side)
        await self._drain(task_id, side)

    # ===== on_start =====
    async def on_start(self, patch: TaskNodePatch) -> NodeOpResult:
        """入站 start 回调:PENDING→RUNNING(幂等)。纯节点态翻转,不触发传播/side-effect。"""
        with self._lock_for(patch.task_id):
            graph = self._graph.query_task_dashboard(patch.task_id)
            node = next((n for n in graph.tasks if n.node_id == patch.node_id), None)
            if node is None:
                raise NodeNotFoundError(
                    f"on_start: node not found {patch.task_id}::{patch.node_id}"
                )
            if node.status == Status.RUNNING:
                return NodeOpResult(
                    task_id=patch.task_id,
                    node_id=patch.node_id,
                    success=True,
                    prev_status=Status.RUNNING,
                    new_status=Status.RUNNING,
                )
            if node.status in {
                Status.DONE,
                Status.FAILED,
                Status.HUNG,
                Status.PLANNING,
            }:
                raise TaskStateError(
                    f"on_start: stale/illegal start on {node.status} node "
                    f"{patch.task_id}::{patch.node_id}"
                )
            return self._graph.update_task_node_info(patch)

    # ===== on_report:三路分流(exec_error→harness / PASS→on_pass / FAIL→on_fail)=====
    async def on_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """回投事件:patch 内含 (task_id,node_id)+终态翻转依据。
        三路分流(互斥):
        ① ``exec_error`` 非空 → 执行报错(bot 没跑通)→ on_harness 复位重投(计数,达上限 HUNG);
        ② ``acceptance_result`` PASS → on_pass(DONE 传播/前向 plan);
        ③ ``acceptance_result`` FAIL+gaps → on_fail(补救重规划,深度闸门);
        无两者 → 仅 fold,返回。验收 100% 来自回投,engine 不主动验。"""
        logger.info(
            "[task_callback][on_report] task=%s node=%s exec_error=%s verdict=%s",
            patch.task_id,
            patch.node_id,
            patch.exec_error,
            patch.acceptance_result.verdict if patch.acceptance_result else "fold-only",
        )
        with self._lock_for(patch.task_id):
            logger.info("[task_callback][on_report] begin update task node info, task=%s,", patch.task_id)
            # 固定 plan 的"执行报错(exec_error)"不进 harness 重投/HUNG:V2 relay 下发给 bot 的是纯
            # 交接正文(无 {success,data,gaps} poller 协议),bot 常回自然语言 → poller 误判 exec_error
            # (terminal_result_invalid)。若仍 reset+重派×MAX_HARNESS→HUNG,会在 80s 兜底
            # (_static_auto_report)之前把节点挂死,整固定流程读不往下走(等同 on_harness 入口守卫
            # 39636835f 的意图,补在 poller→on_report 这条未守的路径上)。此处对固定 plan 的
            # exec_error:不翻 FAILED、不重派,保持节点 RUNNING,仅记 last_exec_error 留痕,让
            # 80s 兜底推进 DONE;真实 {success} 回投先到则走下方 acceptance DONE 自跳过。
            if (
                self._static_runtime(patch.task_id) is not None
                and patch.exec_error is not None
            ):
                logger.info(
                    "[task_callback][on_report] task=%s node=%s 固定 plan exec_error=%s 忽略翻态/重派,交给 static fallback 兜底",
                    patch.task_id, patch.node_id, patch.exec_error,
                )
                patch.status = None  # 保持 RUNNING,不落 FAILED
                patch.acceptance_result = None
                _ep = dict(patch.extend_props_patch) if patch.extend_props_patch else {}
                _ep.setdefault("last_exec_error", patch.exec_error)
                patch.extend_props_patch = _ep
                result = self._graph.update_task_node_info(patch)
                self._reconcile_root_hung_if_blocked(patch.task_id)
                return result
            # 先落验收结果，再处理状态。验收失败不作为普通 DONE 参与父节点成功
            # 聚合:保留 acceptance_result/gaps 作为诊断上下文,随后升级当前节点 HUNG,
            # 由 _maybe_propagate_hung 冒泡到根并进入 BBS。
            acceptance_failed = (
                patch.acceptance_result is not None
                and patch.acceptance_result.verdict == AcceptanceVerdict.FAILED
                and not self._is_external_managed_task(patch.task_id)
            )
            if acceptance_failed:
                # 在同一次 SSOT 写入中选择 HUNG,避免先落 DONE 再二次翻态。
                # acceptance_result/gaps 仍会保留在 run_info 中供 BBS 复核。
                patch.status = Status.HUNG
                patch.extend_props_patch = {
                    **(patch.extend_props_patch or {}),
                    "hung_reason": "acceptance_failed",
                }
            result = self._graph.update_task_node_info(patch)
            if self._static_runtime(patch.task_id) is not None:
                # Static plans use the same harness contract as dynamic tasks.
                if patch.exec_error is not None:
                    side: list[tuple] = []
                    await self._on_harness_collect(
                        patch.task_id, patch.node_id, patch.exec_error, side
                    )
                    await self._drain(patch.task_id, side)
                elif patch.acceptance_result is not None:
                    await self._on_static_report(patch.task_id, patch.node_id)
                self._reconcile_root_hung_if_blocked(patch.task_id)
                return result
            # 动作历史:EXECUTE(执行产出)+ VERIFY(验收结论)——回投即一个执行动作闭环
            _out = dict(patch.output_patch) if patch.output_patch else {}
            if patch.exec_error is not None:
                self._log_action(
                    patch.task_id,
                    patch.node_id,
                    NodeAction.EXECUTE,
                    {"success": False, "exec_error": patch.exec_error, "output": _out},
                    status_from=result.prev_status,
                    status_to=result.new_status,
                )
            elif patch.acceptance_result is not None:
                _ar = patch.acceptance_result
                self._log_action(
                    patch.task_id,
                    patch.node_id,
                    NodeAction.EXECUTE,
                    {"success": _ar.verdict == AcceptanceVerdict.DONE, "output": _out},
                    status_from=result.prev_status,
                    status_to=result.new_status,
                )
                self._log_action(
                    patch.task_id,
                    patch.node_id,
                    NodeAction.VERIFY,
                    {
                        "verdict": _ar.verdict.value,
                        "acceptances_metric": list(_ar.acceptances_metric),
                        "gaps": list(_ar.gaps),
                    },
                    status_from=result.prev_status,
                    status_to=result.new_status,
                )
            if self._is_external_managed_task(patch.task_id):
                # Third-party execution owns transitions. Graph status mirrors
                # root via the single terminal-sync point(不再独立写 status)。
                self._sync_graph_status_to_root(patch.task_id)
                logger.info(
                    "[task][on_report] task=%s external-managed, graph update only",
                    patch.task_id,
                )
                return result
            if patch.exec_error is not None:
                side: list[tuple] = []
                await self._on_harness_collect(
                    patch.task_id, patch.node_id, patch.exec_error, side
                )
                await self._drain(patch.task_id, side)
                self._reconcile_root_hung_if_blocked(patch.task_id)
                return result
            if patch.acceptance_result is None:
                self._reconcile_root_hung_if_blocked(patch.task_id)
                return result  # 仅 fold,无翻态
            if self._is_graph_terminal(patch.task_id):
                logger.info(
                    "[task][on_report] task=%s 图已终态,fold 已落但冻结驱动",
                    patch.task_id,
                )
                return result
            side = []
            verdict = patch.acceptance_result.verdict
            if verdict == AcceptanceVerdict.DONE:
                logger.info("[task_callback][on_report] accept_pass,task=%s", patch.task_id)
                await self._on_pass_collect(patch.task_id, patch.node_id, side)
            else:  # 验收未通过:节点已升级 HUNG,冒泡到根并进入 BBS
                logger.info(
                    "[task_callback][on_report] acceptance_not_passed -> HUNG/BBS,task=%s",
                    patch.task_id,
                )
                self._escalate_hung(
                    patch.task_id, patch.node_id, "acceptance_failed"
                )
            await self._drain(patch.task_id, side)
            self._reconcile_root_hung_if_blocked(patch.task_id)
            return result

    # ===== on_bbs_report:BBS 接力步⑤回投 =====
    async def on_bbs_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """BBS 接力步⑤回投:翻 scoped 节点终态 + 释放 claim,**收口交给 engine 既有路径(非 bot 声明)**。

        不再有 ``root_verified``:根目标是否满足由框架经 owner 复核(``_on_pass_collect``→``plan(root)``→
        ``has_gap=False``→``_maybe_finish_graph``)判定,**不由接力 bot 自报**。BBS 回投表示本次接力执行与验收已完成,统一将 scoped 节点置为 SUCCESS。BBS 回投
        不删除节点、不根据回投内容判定 FAILED。
        最后清根 ``bbs_owner`` 释放 claim。

        持有者校验:``root.run_info.extend_props['bbs_owner']`` 须 == ``patch.assignee``(调用方
        ``report_bbs_result`` 设 ``patch.assignee=bot_id``);非持有者 → ``TaskStateError``(在校验抛,
        不清 claim)。

        释放安全:scoped 终态翻转(fold)收在 ``try`` 内,``finally`` 无条件清根 ``bbs_owner`` —— 翻态抛错也
        释放 claim,避免持卡者死锁(他 bot claim 被 CAS 拒)。owner 校验在 ``try`` 之前,非持有者抛错不清他卡。

        无 owner bot 时(单测)``plan(root)`` 返 ``has_gap=True``(no_planning_port)→ ``gap_no_progress`` → 父
        HUNG;故收口需 owner planner(live 有),单测只验 mechanics(scoped SUCCESS + claim 释放)。"""
        if self._is_external_managed_task(patch.task_id):
            logger.info("[task][on_bbs_report] task=%s external-managed, graph update only", patch.task_id)
            return self._graph.update_task_node_info(patch)
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            graph = self._graph.query_task_dashboard(patch.task_id)
            root = next((n for n in graph.tasks if n.node_id == patch.task_id), None)
            if (
                root is None
                or root.run_info.extend_props.get("bbs_owner") != patch.assignee
            ):
                raise TaskStateError(
                    f"on_bbs_report: 非claim持有者 task={patch.task_id}"
                )
            # BBS 回投成功后将 scoped 节点置为 SUCCESS,使根节点进入正常
            # owner 复核/重新规划路径;不删除节点。
            completion_patch = TaskNodePatch(
                task_id=patch.task_id,
                node_id=patch.node_id,
                # A completed BBS relay is the successful execution/acceptance
                # handoff that unlocks the normal parent/root planning path.
                status=Status.SUCCESS,
                assignee=patch.assignee,
                output_patch=patch.output_patch,
                extend_props_patch=patch.extend_props_patch,
            )
            try:
                result = self._graph.update_task_node_info(completion_patch)
            finally:
                # 无论 scoped 节点翻态是否抛错,都清根 bbs_owner 释放 claim。
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=patch.task_id,
                        node_id=patch.task_id,
                        extend_props_patch={"bbs_owner": None},
                    )
                )
            # BBS scoped SUCCESS 进入统一通过收敛,由 owner 复核根 gap 并继续规划。
            # HUNG 是 BBS 可恢复态:attach 阶段保持根 HUNG,必须允许本次 SUCCESS
            # 回投进入 _on_pass_collect,再由其将根置为 PLANNING。
            if self._graph.query_task_dashboard(patch.task_id).status in {Status.DONE, Status.SUCCESS}:
                logger.info(
                    "[task][on_bbs_report] task=%s 图已终态,不再驱动", patch.task_id
                )
            else:
                node = next(
                    (
                        n
                        for n in self._graph.query_task_dashboard(patch.task_id).tasks
                        if n.node_id == patch.node_id
                    ),
                    None,
                )
                if node is not None and node.status == Status.SUCCESS:
                    await self._on_pass_collect(patch.task_id, patch.node_id, side)
                elif node is not None and node.status == Status.FAILED:
                    await self._on_fail_collect(patch.task_id, patch.node_id, side)
        await self._drain(patch.task_id, side)
        return result

    async def _on_pass_collect(
        self, task_id: str, node_id: str, side: list[tuple]
    ) -> None:
        """PASS→SUCCESS 后:查结构父 P。v4 父恒 PLANNING(委托态),无需翻态:
        兄弟仍有未终态(RUNNING/PLANNING/PENDING)→等待;兄弟全 SUCCESS(plan-ready)→ plan(target=parent):
          有子→节点级 plan_round++(达 MAX_PLAN_ROUND→父 HUNG)+add+dispatch;
          空+has_gap=F→gap 闭:非根传播 DONE 上行/根→图 DONE;空+has_gap=T→HUNG 升 BBS。
        兄弟全终态含 HUNG/FAILED→终态传播。
        v5:重规划产子由**节点级 plan_round** 闸(根+中间父统一计数);loop_round 收敛为只数升 BBS。"""
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            side.append(("finish", task_id))
            return
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        triggering = next(
            (n for n in siblings if n.node_id == node_id),
            None,
        )
        root = self._root(task_id)
        is_root_parent = parent.node_id == (root.node_id if root else None)
        is_bbs_recovery = (
            is_root_parent
            and triggering is not None
            and effective_run_mode(triggering) == "bbs"
        )
        logger.info(
            "[task][on_pass] task=%s node=%s 父=%s 父态=%s 兄弟=%s bbs_recovery=%s",
            task_id,
            node_id,
            parent.node_id,
            parent.status,
            [(s2.node_id, s2.status.value) for s2 in siblings],
            is_bbs_recovery,
        )
        # BBS scoped 节点是对根下 HUNG 占位节点的恢复交付。原 HUNG 节点
        # 仍保留用于审计，不能阻断本次 BBS 成功后的根重新规划。
        if not is_bbs_recovery:
            if any(
                st.status in {Status.RUNNING, Status.PLANNING, Status.PENDING}
                for st in siblings
            ):
                logger.info("[task][on_pass] task=%s 兄弟未全终态,等待", task_id)
                return
            if not all(st.status == Status.SUCCESS for st in siblings):
                self._propagate_terminal(task_id, parent, siblings, side)
                return
        # BBS 可恢复态守卫:图已升 BBS(bbs_mode=true)且根未被 BBS 接力持有(bbs_owner=None)。
        # 走到此守卫前 step①②已保证兄弟全终态且全 DONE;"停手等 BBS 接力"此时是死锁(无在途接力,
        # root 非 HUNG 不重升 BBS → 无人收口)。故无论触发叶是否 bbs scoped,一律放行 owner 复核根 gap:
        # gap 闭→_maybe_finish_graph(根 mode① HUNG→DONE);未闭→重 plan / HUNG 重升 BBS。
        if is_root_parent:
            g_ext = self._graph.query_task_dashboard(task_id).extend_props
            if g_ext.get("bbs_mode") and not g_ext.get("bbs_owner"):
                if is_bbs_recovery:
                    logger.info(
                        "[task][on_pass] task=%s bbs scoped 节点 SUCCESS→放行 owner 复核根 gap 收口",
                        task_id,
                    )
                else:
                    # step①② 已保证兄弟全 SUCCESS 且 bbs_owner=None(无在途接力):停手会死锁
                    # (无在途接力,root 非 HUNG 不重升 BBS → 无人收口)。普通叶最后 DONE 亦放行 owner 复核根 gap。
                    logger.info(
                        "[task][on_pass] task=%s 图 bbs_mode 未 claim,普通叶最后 SUCCESS→放行 owner 复核根 gap(避免死锁)",
                        task_id,
                    )
        self._mark_planning(task_id, parent.node_id)
        graph = self._graph.query_task_dashboard(task_id)
        pr = await self._plan_with_retry(task_id, graph, target_node_id=parent.node_id)
        logger.info(
            "[task][on_pass] task=%s 父=%s 委托 plan 产 %d 子 has_gap=%s",
            task_id,
            parent.node_id,
            len(pr.children),
            pr.has_gap,
        )
        if pr.children:
            # 节点级重规划次数闸 MAX_PLAN_ROUND(父节点"子全 DONE→gap 未闭→重 plan 产新子"计数):
            # 每个父节点各自计数(extend_props.plan_round);达上限 → 父 HUNG(gap_no_progress_plan_round)
            # + 冒泡终态传播,不再 add 新子。首帧 plan(on_execute)不计;on_miss 拆细不计。
            plan_round = int(parent.run_info.extend_props.get("plan_round", 0))
            max_plan_round = self._max_plan_round(task_id)
            if plan_round >= max_plan_round:
                logger.warning(
                    "[task][on_pass] task=%s 父=%s plan_round=%d/%d 达上限→HUNG(不再产子)",
                    task_id,
                    parent.node_id,
                    plan_round,
                    max_plan_round,
                )
                self._hung_and_escalate(task_id, parent.node_id, "plan_round_exhausted")
                return
            # 先判后+1:plan_round 现值为已产次数,0→产首子并+1,1→产第二子并+1,…,MAX-1→产第 MAX 子并+1=MAX,
            # 下次 plan_round=MAX>=MAX 撞顶不产(故 MAX_PLAN_ROUND=N 允许 N 次重规划产子)。
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=parent.node_id,
                    extend_props_patch={"plan_round": plan_round + 1},
                )
            )
            logger.info(
                "[task][on_pass] task=%s 父=%s plan_round=%d/%d 重规划产 %d 子",
                task_id,
                parent.node_id,
                plan_round,
                max_plan_round,
                len(pr.children),
            )
            self._graph.add_task_nodes(pr.children, parent.node_id)
            await self._prepare_into(task_id, side)
        elif not pr.has_gap:
            if is_root_parent:
                self._maybe_finish_graph(task_id, pr)
                return
            # 结构父(非执行态)gap 闭翻 DONE 时补全 run_info:验收执行者=owner 落 run_mode/assignee,
            # 父自身 acceptance_result(owner 逐条验收结论)补全,output 滚直接已 SUCCESS 子交付物
            # (否则结构父 output 恒空 → 祖父一跳 done_children 看不到 → gap_no_progress 死循环)。
            if not parent.run_info.run_mode:
                _done_out = self._rollup_done_children_output(task_id, parent.node_id)
                _parent_acc = self._build_parent_acceptance_result(parent, pr)
                _owner = graph.extend_props.get("owner_bot_id") or ""
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id, node_id=parent.node_id,
                        acceptance_result=_parent_acc, output_patch=_done_out,
                        run_mode="single_bot" if _owner else None,
                        assignee=_owner or None,
                    )
                )
            else:
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id, node_id=parent.node_id, status=Status.SUCCESS
                    )
                )
            # 动作历史:TRANSITION(非根 gap 闭传播 DONE)
            self._log_action(
                task_id,
                parent.node_id,
                NodeAction.TRANSITION,
                {"reason": "gap_closed_propagate", "to": "DONE"},
                status_from=Status.PLANNING,
                status_to=Status.SUCCESS,
            )
            await self._on_pass_collect(task_id, parent.node_id, side)
        else:
            self._hung_and_escalate(task_id, parent.node_id, "gap_no_progress")

    async def _on_fail_collect(
        self, task_id: str, node_id: str, side: list[tuple]
    ) -> None:
        """兼容旧调用入口。验收失败已在 ``on_report`` 中原子折叠为 HUNG 并升级 BBS；
        此处不重复改变节点状态或安排重试。"""
        logger.info(
            "[task][on_fail] task=%s node=%s acceptance_not_passed already handled by on_report",
            task_id,
            node_id,
        )

    async def _on_harness_collect(
        self, task_id: str, node_id: str, exec_error: str, side: list[tuple]
    ) -> None:
        """harness 重试仅处理执行失败(exec_error:网络抖动、超时、崩溃、poll 耗尽)。
        验收未通过不进入此路径,由图服务记录为 DONE 并保留验收结论。
        """
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        if node is None:
            return
        retries = int(node.run_info.extend_props.get("harness_retries", 0))
        max_harness = self._max_harness(task_id)
        if retries >= max_harness:
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    extend_props_patch={"last_exec_error": exec_error},
                )
            )
            logger.warning(
                "[task][on_harness] task=%s node=%s 达 MAX_HARNESS(%d)→HUNG(retries=%d)",
                task_id,
                node_id,
                max_harness,
                retries,
            )
            self._hung_and_escalate(task_id, node_id, "exec_stuck")
            return
        retries += 1
        logger.info(
            "[task][on_harness] task=%s node=%s reason=%s retries=%d/%d",
            task_id,
            node_id,
            exec_error,
            retries,
            max_harness,
        )
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                extend_props_patch={
                    "harness_retries": retries,
                    "last_exec_error": exec_error,
                },
            )
        )
        # 复位到 PENDING 重新派发执行:FAILED/RUNNING→PENDING;PENDING 派发卡住(搜推无响应/派发失败)清
        # dispatch_error 让 prepare 重新派发(harness owns 重试计数+HUNG 上限,正常 cycle 跳过 dispatch_error 节点)
        if node.status in {Status.FAILED, Status.RUNNING}:
            _prev = node.status
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PENDING)
            )
            # 动作历史:RESET(harness 重新派发执行重试)
            self._log_action(
                task_id,
                node_id,
                NodeAction.RESET,
                {
                    "reason": exec_error or "failed_retry",
                    "prev_status": _prev.value,
                    "harness_retries_after": retries,
                },
                attempt=retries,
                status_from=_prev,
                status_to=Status.PENDING,
            )
        elif node.status == Status.PENDING and node.run_info.extend_props.get(
            "dispatch_error"
        ):
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    extend_props_patch={"dispatch_error": None},
                )
            )
        # static plan:harness 重派走 static prepare(只派发 readiness.ready 的绑定 bot),
        # 不进搜推/claim_join,避免依赖未满足的节点(strategy_approval/implementation)被提前搜推
        # 派给 catalog 命中的错误 bot(如 default:35983)。
        _static_runtime = self._static_runtime(task_id)
        if _static_runtime is not None:
            await self._prepare_static(task_id, _static_runtime, side)
        else:
            await self._prepare_into(task_id, side)

    # ===== on_miss =====
    async def on_miss(self, patch: TaskNodePatch) -> None:
        """dispatcher MISS(搜推未匹配执行者)→深度闸门:
        depth>=MAX → HUNG 升 BBS(拆不动,无 bot);depth<MAX → mark_planning + plan(target=miss 叶)拆细:
        有子→add(父置 PLANNING)+dispatch;空+has_gap=F→gap 闭不推进(罕见);空+has_gap=T→HUNG 升 BBS。
        MISS 不进 harness(无 bot 无可重试执行体)。"""
        if self._is_external_managed_task(patch.task_id):
            logger.info("[task][on_miss] task=%s external-managed, skip dynamic planning", patch.task_id)
            return
        if self._is_graph_terminal(patch.task_id):
            logger.info(
                "[task][on_miss] task=%s 图已终态,冻结 MISS 推进", patch.task_id
            )
            return
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            depth = self._graph._node_depth(patch.task_id, patch.node_id)
            cfg = self._graph._execution_config(patch.task_id)
            max_depth = cfg["MAX_DEPTH"]
            # 动作历史:DISPATCH(MISS 搜推未命中执行者)
            _miss_reason = ""
            _ep = patch.extend_props_patch or {}
            if isinstance(_ep.get("miss_events"), list) and _ep["miss_events"]:
                _miss_reason = str(_ep["miss_events"][0])
            self._log_action(
                patch.task_id,
                patch.node_id,
                NodeAction.DISPATCH,
                {
                    "outcome": "MISS",
                    "miss_reason": _miss_reason,
                    "depth": depth,
                    "max_depth": max_depth,
                },
                status_from=Status.PENDING,
                status_to=Status.PENDING,
            )
            if depth >= max_depth:
                logger.info(
                    "[task][on_miss] task=%s node=%s depth=%d/%d 拆不动→HUNG",
                    patch.task_id,
                    patch.node_id,
                    depth,
                    max_depth,
                )
                self._hung_and_escalate(
                    patch.task_id, patch.node_id, "miss_depth_exhausted"
                )
                await self._drain(patch.task_id, side)
                return
            self._mark_planning(patch.task_id, patch.node_id)
            graph = self._graph.query_task_dashboard(patch.task_id)
            pr = await self._plan_with_retry(
                patch.task_id, graph, target_node_id=patch.node_id
            )
            logger.info(
                "[task][on_miss] task=%s node=%s depth=%d/%d plan 产 %d 子 has_gap=%s",
                patch.task_id,
                patch.node_id,
                depth,
                max_depth,
                len(pr.children),
                pr.has_gap,
            )
            if pr.children:
                self._graph.add_task_nodes(pr.children, patch.node_id)
                await self._prepare_into(patch.task_id, side)
            elif not pr.has_gap:
                pass
            else:
                self._hung_and_escalate(
                    patch.task_id, patch.node_id, "miss_no_decompose"
                )
        await self._drain(patch.task_id, side)

    # ===== on_harness(harness 旁路入口:超时/崩溃/FAILED 巡检;复用 _on_harness_collect)=====
    async def on_harness(self, patch: TaskNodePatch) -> None:
        """Harness 旁路入口:exec_error 语义(超时/崩溃/FAILED 巡检)→ 复用 _on_harness_collect 重新派发重试/上限 HUNG。"""
        if self._is_external_managed_task(patch.task_id):
            logger.info("[task][on_harness] task=%s external-managed, skip Avernet retry", patch.task_id)
            return
        if self._is_graph_terminal(patch.task_id):
            logger.info(
                "[task][on_harness] task=%s 图已终态,冻结 harness 推进", patch.task_id
            )
            return
        if self._static_runtime(patch.task_id) is not None:
            # 固定 plan 任务:真实上报兜底由 _static_auto_report(默认 80s mock PASS→SUCCESS)承担,
            # V2 relay 节点下发为纯交接正文(不含 {success,data,gaps} poller 协议),bot 常回自然语言
            # → poller 误判 exec_error;若仍走 harness 重投×MAX_HARNESS→HUNG,会在 80s fallback 之前就把
            # 节点 HUNG,80s 兜底因 status!=RUNNING 而跳过,致单节点挂死、整流程不往下走。
            # 此处对固定 plan 跳过 harness 重投/HUNG,把恢复交给 static fallback:真实回投先到则自跳过,
            # 否则 80s 由 mock 推进 DONE,流程不卡(真实派发已完成,不重复派发)。
            logger.info(
                "[task][on_harness] task=%s node=%s 固定 plan,由 static fallback 兜底,跳过 harness 重投/HUNG",
                patch.task_id, patch.node_id,
            )
            return
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            await self._on_harness_collect(
                patch.task_id,
                patch.node_id,
                patch.exec_error or "external_harness",
                side,
            )
        await self._drain(patch.task_id, side)

    # ===== HUNG + 升 BBS(loop_round++ / 图 HUNG) + 终态传播 =====
    def _sync_graph_status_to_root(self, task_id: str) -> None:
        """终态镜像:graph.status := root.status(仅终态 DONE/HUNG/FAILED/CANCELLED)。

        不变量:graph.status 是 root.status 的同步镜像——root 是什么终态 graph 就什么终态,
        graph 不脱离 root 独立翻终态。root 非终态(PENDING/PLANNING/RUNNING)时不动 graph 的
        RUNNING 进行态(建图/中间态不镜像)。BBS 可恢复态(root HUNG 但等接力)由 _maybe_propagate_hung
        自管,不走本方法。"""
        root = self._root(task_id)
        if root is None or root.status not in {Status.DONE, Status.SUCCESS, Status.HUNG, Status.FAILED, Status.CANCELLED}:
            return
        g = self._graph.query_task_dashboard(task_id)
        if g.status == root.status:
            return
        self._graph.update_task_graph_info(task_id, TaskGraphPatch(status=root.status))

    def _bump_loop_round(self, task_id: str) -> None:
        """图级 loop_round 升 BBS 计次(先判后+1):当前 loop_round>=MAX_LOOP → root HUNG(loop_exhausted)
        + graph 终态镜像 HUNG(硬停);否则 loop_round+1。MAX_LOOP=N 允许 N 次升 BBS 接力,第 N+1 次撞顶。

        终态镜像:不再 graph 独立写 HUNG——先置 root HUNG(loop_exhausted),再 _sync_graph_status_to_root
        镜像 graph(保证 graph.status≡root.status)。"""
        graph = self._graph.query_task_dashboard(task_id)
        max_loop = self._graph._execution_config(task_id)["MAX_LOOP"]
        if graph.loop_round >= max_loop:
            root = self._root(task_id)
            if root is not None and root.status != Status.HUNG:
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=root.node_id,
                        status=Status.HUNG,
                        extend_props_patch={"hung_reason": "loop_exhausted"},
                    )
                )
            # graph 终态镜像 root(HUNG)+ 保留图级 loop_exhausted 诊断标记
            self._sync_graph_status_to_root(task_id)
            self._graph.update_task_graph_info(
                task_id,
                TaskGraphPatch(extend_props_patch={"hung_reason": "loop_exhausted"}),
            )
            logger.warning(
                "[task][loop_round] task=%s 达 MAX_LOOP(%d)→root/graph HUNG(loop_exhausted)",
                task_id,
                max_loop,
            )
            return
        self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(loop_round_increment=1)
        )

    def _escalate_hung(self, task_id: str, node_id: str, hung_reason: str) -> None:
        """传播节点 HUNG 的影响,但不在节点级消耗根 BBS 轮次。

        ``_maybe_propagate_hung`` 负责判断阻塞是否已扩散到根;只有根节点确认进入
        BBS 的分支才设置 ``bbs_mode``、递增 ``loop_round`` 并经 ``start_run`` 调度。
        **不置节点态**——调用方须保证节点已 HUNG。乙' a+R1:验收 FAIL 节点已由
        on_report 折叠直驱 HUNG,故 _on_fail_collect 直接调用本方法;其余 HUNG
        (miss/harness/plan_round/gap_no_progress)经 _hung_and_escalate 写 HUNG 后复用本方法。
        """
        # 节点级 HUNG 只做影响传播;根级 BBS 计数在 _maybe_propagate_hung 收口。
        self._maybe_propagate_hung(task_id, node_id, hung_reason)

    def _hung_and_escalate(self, task_id: str, node_id: str, hung_reason: str) -> None:
        """节点置 HUNG + 升级传播(锁内同步)。乙' a+R1:验收 FAIL 不再经此(_on_fail_collect 节点已折叠
        HUNG,直接 _escalate_hung);其余 HUNG 仍经此一次写 HUNG。"""
        _prev = next(
            (
                n.status
                for n in self._graph.query_task_dashboard(task_id).tasks
                if n.node_id == node_id
            ),
            None,
        )
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.HUNG,
                extend_props_patch={"hung_reason": hung_reason},
            )
        )
        # 动作历史:TRANSITION(节点 HUNG)
        self._log_action(
            task_id,
            node_id,
            NodeAction.TRANSITION,
            {"reason": hung_reason, "to": "HUNG"},
            status_from=_prev,
            status_to=Status.HUNG,
        )
        logger.info(
            "[task][hung] task=%s node=%s reason=%s → 向上评估根级 BBS",
            task_id,
            node_id,
            hung_reason,
        )
        self._escalate_hung(task_id, node_id, hung_reason)

    def _on_bg_done(self, bg: object) -> None:
        """后台任务完成:脱离跟踪集 + 异常/取消可见(不抛,不阻塞 on_*)。"""
        self._bg_tasks.discard(bg)
        cancelled = getattr(bg, "cancelled", lambda: False)()
        if cancelled:
            logger.warning("[task][engine] background task cancelled task=%s", getattr(bg, "_bbs_task_id", ""))
            return
        exc = getattr(bg, "exception", lambda: None)()
        if exc is not None:
            logger.error("[task][engine] background task 异常: %s", exc, exc_info=exc)
            return
        result = getattr(bg, "result", lambda: None)()
        if isinstance(result, list) and any(item is not True for item in result):
            logger.error(
                "[task][engine] background start_run 投递失败 task=%s results=%s",
                getattr(bg, "_bbs_task_id", ""),
                result,
            )

    def _ensure_bbs_loop(self) -> asyncio.AbstractEventLoop:
        """Return an engine-owned loop that outlives Harness' temporary loop.

        Harness invokes ``on_harness`` through ``asyncio.run``. BBS is a
        minutes-long workflow, so scheduling it on that loop makes it a child of
        a short-lived request and silently cancels it when Harness returns.
        """
        with self._bbs_loop_guard:
            if self._bbs_loop is not None and self._bbs_loop.is_running():
                return self._bbs_loop
            self._bbs_loop_ready.clear()

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                with self._bbs_loop_guard:
                    self._bbs_loop = loop
                    self._bbs_loop_thread = threading.current_thread()
                    self._bbs_loop_ready.set()
                loop.run_forever()

            self._bbs_loop_thread = threading.Thread(
                target=_run,
                daemon=True,
                name="task-bbs-loop",
            )
            self._bbs_loop_thread.start()

        if not self._bbs_loop_ready.wait(timeout=5.0):
            raise RuntimeError("BBS background event loop failed to start")
        loop = self._bbs_loop
        if loop is None or not loop.is_running():
            raise RuntimeError("BBS background event loop is not running")
        return loop

    def _on_auto_report_done(self, t: "asyncio.Task") -> None:
        """静态自驱 on_report 后台任务完成:脱离跟踪集 + 异常可见。"""
        self._bg_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("[task][static-plan] auto-report bg task 异常: %s", exc, exc_info=exc)

    def _reset_root_plan_round(self, task_id: str) -> None:
        """升 BBS 收口时将根节点 plan_round 重置为 loop_round:on_pass 计数为"先判后+1"
        (plan_round 现值<MAX 产子并 +1,达 MAX 撞顶),故从 plan_round=loop_round 起产子 = MAX-loop_round
        (plan_round loop..MAX-1 产,MAX 撞),逐轮递减;MAX_LOOP 仍作总轮次兜底。避免 plan_round 残留撞顶值
        → BBS 接力 on_pass 回根立即再撞 plan_round_exhausted、重新规划白做。"""
        root = self._root(task_id)
        if root is None:
            return
        loop_round = self._graph.query_task_dashboard(task_id).loop_round
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=root.node_id,
                extend_props_patch={"plan_round": loop_round},
            )
        )

    def _schedule_bbs_notify(self, task_id: str, execution_graph) -> None:
        """可恢复拦截点(spec §5):fire-and-forget ``runner.start_run([bbs_node])``。

        命中根 BBS 可恢复态(bbs_mode + 未 claim)时调用——主动 bid→select→claim→
        dispatch 给 claim-enabled bot。上层只使用统一 start_run seam，不感知 BBS 专用方法。
        不持锁、不阻塞 ``on_*``/``_maybe_propagate_hung`` 汇报路径:``asyncio.create_task``
        调度后台协程,异常经 ``_on_bg_done`` 记 log。端口不全(无 runner/bot/bcs,如单测 stub)→ 静默跳过。"""
        logger.info("[task][bbs_mode], begin schedule bbs notify, task_id=%s", task_id)
        if not self._runner:
            logger.info("[task][bbs_mode], _runner is none, skip, task_id=%s", task_id)
            return
        root = next(
            (node for node in execution_graph.tasks if node.node_id == task_id),
            None,
        )
        if root is None:
            logger.error(
                "[task][bbs_mode] root missing, skip start_run task_id=%s", task_id
            )
            return
        # BBS 是本次执行请求的目标模态，不改写图中根节点原有 run_mode/assignee。
        # 具体 BBS 语义由执行 adapter 隐藏，Runner 只接收普通 TaskNode。
        bbs_node = replace(
            root,
            run_info=replace(
                root.run_info,
                run_mode="bbs",
                output=dict(root.run_info.output),
                extend_props=dict(root.run_info.extend_props),
                action_log=list(root.run_info.action_log),
            ),
        )
        loop = self._ensure_bbs_loop()
        coroutine = self._runner.start_run([bbs_node])
        try:
            bg = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except Exception:
            coroutine.close()
            raise
        # concurrent.futures.Future is intentionally tracked here. It belongs
        # to the durable BBS loop, not the caller's Harness asyncio loop.
        bg._bbs_task_id = task_id  # type: ignore[attr-defined]
        self._bg_tasks.add(bg)
        bg.add_done_callback(self._on_bg_done)
        logger.info(
            "[task][engine] task=%s 升BBS可恢复态→提交 durable BBS loop thread=%s",
            task_id,
            self._bbs_loop_thread.name if self._bbs_loop_thread else "unknown",
        )

    def _enter_root_bbs(self, task_id: str, execution_graph) -> bool:
        """Enter one root-level BBS round and schedule the relay if budget remains.

        ``loop_round`` counts root-to-BBS escalations only. A node-level HUNG caller
        reaches this helper only after ``_maybe_propagate_hung`` has confirmed that
        the root is blocked.
        """
        if execution_graph.status == Status.HUNG:
            return False
        root = self._root(task_id)
        if root is None or root.run_info.extend_props.get("bbs_owner"):
            return False
        self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True})
        )
        self._bump_loop_round(task_id)
        current = self._graph.query_task_dashboard(task_id)
        if current.status == Status.HUNG:
            return False
        self._reset_root_plan_round(task_id)
        self._schedule_bbs_notify(task_id, current)
        return True

    def _maybe_propagate_hung(
        self, task_id: str, node_id: str, hung_reason: str = ""
    ) -> None:
        """自 node 往上:若父的子全终态且含 HUNG → 父 HUNG(不计额外 loop_round,纯冒泡)→ 继续上行。
        到根 → 图终态收口(HUNG)。若图已 HUNG(loop_exhausted 等已收口)→ 不覆盖 hung_reason。

        **BBS 可恢复态(spec §10.5,调度优化)**:只要阻塞已经传播到根节点(任一 ``hung_reason``:
        ``miss_depth_exhausted``/``root_gap_no_decompose``/``gap_no_progress``/
        ``plan_round_exhausted``/``exec_stuck``/``child_hung`` 等),且根未被 claim,即进入根级 BBS
        可恢复态(经 ``start_run`` 派发 BBS);``loop_exhausted`` 由 ``_bump_loop_round`` 置根/图 HUNG,
        被上方 ``g.status==HUNG`` 短路拦截、不再调度,保留反失控兜底。在途 BBS(``bbs_owner``
        非空)亦跳过,不重复派发。``loop_round`` 只在根确认进入 BBS 时递增。
        """
        # 任意 HUNG 都必须沿依赖链冒泡到根。根 HUNG 后再由统一入口进入 BBS，
        # 不允许 miss_depth_exhausted 之类的特殊分支把根留在 PLANNING/EXECUTING。
        cur = node_id
        while True:
            parent = self._graph.get_parent_task(task_id, cur)
            if parent is None:
                # cur 是根 → 图级收口(根 HUNG → 图 HUNG);不覆盖已设的图级 hung_reason。
                # 根 HUNG 且图未进入硬终态时,由根级入口统一计数并调度 BBS。
                root = self._root(task_id)
                if (
                    root is not None
                    and root.node_id == cur
                    and root.status == Status.HUNG
                ):
                    g = self._graph.query_task_dashboard(task_id)
                    if g.status == Status.HUNG:
                        return
                    if not g.extend_props.get("bbs_owner"):
                        # 根 HUNG 才进入 BBS;此处统一递增根级 loop_round。
                        logger.info(
                            "[task][hung-propagate] task=%s 根 HUNG(reason=%s)→升 BBS 可恢复态",
                            task_id,
                            hung_reason,
                        )
                        self._enter_root_bbs(task_id, g)
                        return
                    # 无 bbs_mode 或已被 BBS claim → 硬 HUNG 收口(不再调度,等在途 BBS 回投)
                    # 终态镜像:root 已 HUNG → graph 经单一同步点镜像 HUNG;保留 root_stuck 诊断
                    self._sync_graph_status_to_root(task_id)
                    self._graph.update_task_graph_info(
                        task_id,
                        TaskGraphPatch(extend_props_patch={"hung_reason": "root_stuck"}),
                    )
                return
            siblings = self._graph.get_child_tasks(task_id, parent.node_id)
            if any(
                st.status in {Status.RUNNING, Status.PLANNING, Status.PENDING}
                for st in siblings
            ):
                return  # 还有活子,等
            if any(st.status == Status.HUNG for st in siblings):
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=parent.node_id,
                        status=Status.HUNG,
                        extend_props_patch={"hung_reason": "child_hung"},
                    )
                )
                logger.info(
                    "[task][hung-propagate] task=%s 父=%s 因子含 HUNG→HUNG",
                    task_id,
                    parent.node_id,
                )
                cur = parent.node_id
                continue
            return

    def _reconcile_root_hung_if_blocked(self, task_id: str) -> None:
        """Close a missed root-HUNG transition after a late sibling completion.

        HUNG propagation can observe an active sibling and return. A later
        completion may arrive through a status-only or fold-only callback path
        that does not call ``_on_pass_collect``. Re-scan the root boundary after
        every callback so an all-terminal root with any HUNG child cannot remain
        in PLANNING/RUNNING forever.
        """
        root = self._root(task_id)
        if root is None or root.status in {Status.HUNG, Status.DONE, Status.SUCCESS, Status.FAILED, Status.CANCELLED}:
            return
        siblings = self._graph.get_child_tasks(task_id, root.node_id)
        if not siblings or any(
            node.status in {Status.PENDING, Status.PLANNING, Status.RUNNING}
            for node in siblings
        ):
            return
        hung = next((node for node in siblings if node.status == Status.HUNG), None)
        if hung is None:
            return
        reason = str(hung.run_info.extend_props.get("hung_reason") or "child_hung")
        logger.warning(
            "[task][hung-reconcile] task=%s root=%s all children terminal with HUNG child=%s "
            "-> force root propagation reason=%s",
            task_id, root.node_id, hung.node_id, reason,
        )
        self._maybe_propagate_hung(task_id, hung.node_id, reason)

    def _propagate_terminal(
        self, task_id: str, parent: TaskNode, siblings: list, side: list[tuple]
    ) -> None:
        """on_pass 时兄弟全终态但非全 DONE(含 HUNG):子含 HUNG→父 HUNG 冒泡(经 _maybe_propagate_hung)。
        FAILED 子由 harness 巡检补救,此处若仅 FAILED(无 HUNG)不在此处理(等 harness 补救/转 HUNG)。"""
        if any(st.status == Status.HUNG for st in siblings):
            self._maybe_propagate_hung(
                task_id, siblings[0].node_id if siblings else parent.node_id
            )

    # ===== 派发+执行(通用)=====
    async def _prepare_into(self, task_id: str, side: list[tuple]) -> None:
        """查「未派发」PENDING 节点 → await dispatcher.dispatch 返填执行者 → HIT 先落 run_mode/assignee
        + 飞行标记 ``dispatching``(保持 PENDING),start_run/form_coop_group 成功后由 _drain 翻 RUNNING(side 'run'/'group')
        并清 dispatching;MISS(side 'miss');派发异常(side 'dispatch_fail',留 PENDING 交 harness 按超时重试搜推)。

        状态机:RUNNING=真执行;派发命中只填执行者+置 dispatching,PENDING 维持到 start_run 成功后才翻。
        跳过:① dispatching=True 节点(已交付 _drain 待翻 RUNNING 的飞行态,防双派发);② dispatch_error 节点
        (搜推异常/派发失败,harness owns 重试+HUNG 上限,正常 cycle 不重复搜推防 bot 调用风暴);
        ③ effective_run_mode(node)=="bbs" 节点(FR-EXT-06:bbs 由 bot 经 bbs/attach 自驱,框架不自动派发/翻态)。
        reset 节点(FAILED/RUNNING→PENDING 复位,无 dispatching)不在跳过之列→重新派发执行。"""
        if self._is_external_managed_task(task_id):
            logger.info("[task][prepare] task=%s external-managed, skip dynamic dispatch", task_id)
            return
        all_pending = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(status=Status.PENDING)
        )
        pending = [
            n
            for n in all_pending
            if not n.run_info.extend_props.get("dispatching")
            and not n.run_info.extend_props.get("dispatch_error")
            and effective_run_mode(n) != "bbs"
        ]
        if not pending:
            return
        dispatch_started_at = _now_ms()
        for node in pending:
            # start_time is the first dispatch lifecycle timestamp, not the
            # beginning of each Harness retry attempt. Preserve it across
            # RUNNING→PENDING→RUNNING retries so elapsed time stays truthful.
            if node.run_info.start_time is None:
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        start_time=dispatch_started_at,
                    )
                )
        logger.info(
            "[task][prepare] task=%s 待派发节点=%s dispatch_started_at=%s",
            task_id,
            [n.node_id for n in pending],
            dispatch_started_at,
        )
        dispatched = await self._dispatcher.dispatch(pending)
        to_run: list[TaskNode] = []
        for node in dispatched:
            miss = node.run_info.extend_props.get("miss_events")
            gf = node.run_info.extend_props.pop("pending_group_formation", None)
            if gf is not None:
                logger.info(
                    "[task][prepare] task=%s node=%s → group(HIT_MULTI_BOTS collab=%s bot_ids=%s)",
                    task_id,
                    node.node_id,
                    gf.collab_mode,
                    gf.bot_ids,
                )
                # 群验收需要完整 goal/instruction，而不是只有一句 task_context。
                gf.extend_props.setdefault(
                    "task_objective", node.task_spec.goal.objective
                )
                gf.extend_props.setdefault(
                    "task_instruction", node.task_spec.metadata.instruction
                )
                gf.extend_props.setdefault(
                    "acceptances",
                    [
                        {"id": a.id, "description": a.description}
                        for a in node.task_spec.goal.acceptances
                    ],
                )
                # 飞行标记:group 交付 _drain 拉群前置,防并发 cycle 双搜推双拉群
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="coop_group",
                        extend_props_patch={"dispatching": True, "dispatching_at": _now_ms()},
                    )
                )
                side.append(("group", node, gf))
                continue
            if node.run_info.run_mode and node.run_info.assignee:
                logger.info(
                    "[task][prepare] task=%s node=%s → run(mode=%s assignee=%s)",
                    task_id,
                    node.node_id,
                    node.run_info.run_mode,
                    node.run_info.assignee,
                )
                # HIT:落执行者+飞行标记 dispatching(保持 PENDING);start_run 成功后 _drain 翻 RUNNING+清 dispatching
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode=node.run_info.run_mode,
                        assignee=node.run_info.assignee,
                        extend_props_patch={"dispatching": True, "dispatching_at": _now_ms()},
                    )
                )
                to_run.append(node)
            elif miss:
                logger.info(
                    "[task][prepare] task=%s node=%s → miss(%s)",
                    task_id,
                    node.node_id,
                    miss,
                )
                side.append(
                    (
                        "miss",
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=node.node_id,
                            extend_props_patch={"miss_events": miss},
                        ),
                    )
                )
            else:
                # 派发未产出执行者也非 MISS(dispatcher 已容错吞异常):标 dispatch_error 留 PENDING,harness 按超时重试搜推
                derr = node.run_info.extend_props.get("dispatch_error") or "no_result"
                logger.warning(
                    "[task][prepare] task=%s node=%s 派发未产出(%s)→留 PENDING 待 harness",
                    task_id,
                    node.node_id,
                    derr,
                )
                side.append(
                    (
                        "dispatch_fail",
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=node.node_id,
                            extend_props_patch={"dispatch_error": derr},
                        ),
                    )
                )
        if to_run:
            side.append(("run", to_run))

    async def _drain(self, task_id: str, side: list[tuple]) -> None:
        """锁外统一执行 side effects。投递/拉群 IO 锁外 await;翻态(side effect)收口锁内。
        v4 状态机:run 经 start_run 投递,成功后才翻 RUNNING+清 dispatching(对齐"调执行方法后置 RUNNING");
        失败→清执行者+清 dispatching+标 dispatch_error 留 PENDING 交 harness 重试搜推。group 经 form_coop_group
        拉群后翻 RUNNING+清 dispatching。miss 递归推进与 run 投递不互相阻塞。"""
        run_nodes: list[TaskNode] = []
        miss_tasks: list[TaskNodePatch] = []
        dispatch_fail_patches: list[TaskNodePatch] = []
        auto_nodes: list[TaskNode] = []
        for kind, *payload in side:
            if kind == "run":
                run_nodes.extend(payload[0])
            elif kind == "group":
                node, gf = payload
                logger.info(
                    "[task][drain] task=%s node=%s 拉群开始 collab=%s bot_ids=%s members=%s",
                    task_id,
                    node.node_id,
                    gf.collab_mode,
                    list(getattr(gf, "bot_ids", []) or []),
                    list(getattr(gf, "members_info", []) or []),
                )
                # 协作群叶子:注入 loop_task_id 供 form_coop_group 写入群 context,
                # 供 driver/owner bot 验收后 push 回投 /callback/report 定位执行节点
                # (acceptance 段4;single_bot 走 poll,不经此拉群路径)。
                gf.extend_props.setdefault(
                    "loop_task_id", f"{node.task_id}::{node.node_id}"
                )
                try:
                    gid = await self._runner.form_coop_group(gf)
                    logger.info(
                        "[task][drain] task=%s node=%s 拉群成功 group_id=%s collab=%s",
                        task_id,
                        node.node_id,
                        gid,
                        gf.collab_mode,
                    )
                except Exception as ex:  # noqa: BLE001  拉群异常→清 dispatching 留 PENDING 交 harness
                    logger.exception(
                        "[task][drain] task=%s node=%s 拉群失败 exc_type=%s exc=%s collab=%s bot_ids=%s",
                        task_id,
                        node.node_id,
                        type(ex).__name__,
                        ex,
                        gf.collab_mode,
                        list(getattr(gf, "bot_ids", []) or []),
                    )
                    with self._lock_for(task_id):
                        self._graph.update_task_node_info(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                run_mode="",
                                assignee="",
                                extend_props_patch={
                                    "dispatching": None,
                                    "dispatch_error": "form_group_failed",
                                },
                            )
                        )
                    continue
                node.run_info.assignee = gid
                with self._lock_for(task_id):
                    self._graph.update_task_node_info(
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=node.node_id,
                            status=Status.RUNNING,
                            run_mode=node.run_info.run_mode,
                            assignee=gid,
                            extend_props_patch={"dispatching": None},
                        )
                    )
                # 动作历史:DISPATCH(HIT_MULTI 协作群)
                self._log_action(
                    task_id,
                    node.node_id,
                    NodeAction.DISPATCH,
                    {
                        "outcome": "HIT_MULTI",
                        "run_mode": "coop_group",
                        "assignee": gid,
                        "collab_mode": getattr(gf, "collab_mode", None),
                        "bot_ids": list(getattr(gf, "bot_ids", []) or []),
                    },
                    status_from=Status.PENDING,
                    status_to=Status.RUNNING,
                )
                run_nodes.append(node)
            elif kind == "auto":
                auto_nodes.extend(payload[0])
            elif kind == "bbs_handoff":
                node, bot_id, items = payload
                t = asyncio.create_task(
                    self._bbs_handoff_claim(task_id, node.node_id, bot_id, items)
                )
                # 强引用保活(同 auto-report),避免 sleep 期间被 GC 回收
                self._bg_tasks.add(t)
                t.add_done_callback(self._on_bbs_handoff_done)
                logger.info(
                    "[task][static-plan] bbs_handoff claim scheduled task=%s node=%s rnd_bot=%s in %.1fs",
                    task_id, node.node_id, bot_id, self._bbs_handoff_delay(task_id),
                )
            elif kind == "miss":
                miss_tasks.append(payload[0])
            elif kind == "dispatch_fail":
                dispatch_fail_patches.append(payload[0])
            elif kind == "finish":
                logger.info("[task][drain] task=%s finish(根 gap 闭→图 DONE)", task_id)
                self._maybe_finish_graph(payload[0])
        # ① run:start_run 投递,成功后翻 RUNNING+清 dispatching;失败清执行者+清 dispatching+标 dispatch_error 留 PENDING
        if run_nodes:
            logger.info(
                "[task][drain] task=%s start_run %d 节点:%s",
                task_id,
                len(run_nodes),
                [n.node_id for n in run_nodes],
            )
            try:
                results = await self._runner.start_run(run_nodes)
            except Exception as ex:  # noqa: BLE001  start_run 异常→全部当失败,清 dispatching 留 PENDING 交 harness
                logger.warning(
                    "[task][drain] task=%s start_run 异常:%s→全部留 PENDING 待 harness",
                    task_id,
                    ex,
                )
                results = [False] * len(run_nodes)
            with self._lock_for(task_id):
                cur_map = {
                    x.node_id: x
                    for x in self._graph.query_task_nodes(
                        task_id,
                        TaskNodeQueryCriteria(node_ids=[n.node_id for n in run_nodes]),
                    )
                }
                for node, ok in zip(run_nodes, results):
                    if not ok:
                        logger.warning(
                            "[task][drain] task=%s node=%s start_run 失败→清执行者留 PENDING 待 harness",
                            task_id,
                            node.node_id,
                        )
                        # 清 run_mode/assignee(置空串)+清 dispatching 使其重新可搜推;标 dispatch_error
                        self._graph.update_task_node_info(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                run_mode="",
                                assignee="",
                                extend_props_patch={
                                    "dispatching": None,
                                    "dispatch_error": "start_run_failed",
                                },
                            )
                        )
                        continue
                    cur = cur_map.get(node.node_id)
                    if cur is not None and cur.status == Status.PENDING:
                        self._graph.update_task_node_info(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                status=Status.RUNNING,
                                extend_props_patch={"dispatching": None},
                            )
                        )
                        # 动作历史:DISPATCH(HIT_SINGLE 单 bot 派发执行)
                        self._log_action(
                            task_id,
                            node.node_id,
                            NodeAction.DISPATCH,
                            {
                                "outcome": "HIT_SINGLE",
                                "run_mode": cur.run_info.run_mode,
                                "assignee": cur.run_info.assignee,
                            },
                            status_from=Status.PENDING,
                            status_to=Status.RUNNING,
                        )
        # ④ 固定流程兜底上报:每个真实派发节点都调度一条延迟 mock 兜底(auto=True 短延迟演示,
        #    默认真实模式 fallback 超时 80s);真实回投先到则 _static_auto_report 内自跳过。
        if auto_nodes:
            logger.info(
                "[task][drain] task=%s fallback-mock scheduled %d nodes: %s",
                task_id, len(auto_nodes), [n.node_id for n in auto_nodes],
            )
            for n in auto_nodes:
                t = asyncio.create_task(self._static_auto_report(task_id, n.node_id))
                # 保活:存强引用,避免 sleep 期间被 GC 回收导致 on_report 永不触发(asyncio 官方坑)
                self._bg_tasks.add(t)
                t.add_done_callback(self._on_auto_report_done)
                logger.info(
                    "[task][static-plan] report-fallback scheduled task=%s node=%s task_obj=%s",
                    task_id, n.node_id, id(t),
                )
        # ② dispatch_fail:落 dispatch_error(留 PENDING,harness 按超时重试搜推)
        for patch in dispatch_fail_patches:
            self._graph.update_task_node_info(patch)
        # ③ miss 推进(递归 collect+drain)
        for m in miss_tasks:
            await self.on_miss(m)

    def _maybe_finish_graph(self, task_id: str, pr: PlanResult | None = None) -> None:
        """根 gap 闭(终验通过)→ root 翻 SUCCESS(并补全 run_info)→ graph 终态镜像 SUCCESS。

        终态镜像:root 先 SUCCESS,再 _sync_graph_status_to_root 镜像 graph(保证 graph.status≡root.status,
        不再 graph 独立先写 status)。验收执行者=owner 落 run_mode/assignee,根自身 acceptance_result
        (owner 逐条验收结论),output 滚直接已 SUCCESS 子交付物。两写均经 SSOT 网关(锁内同步)。"""
        root = self._root(task_id)
        if root is not None and root.status not in {Status.DONE, Status.SUCCESS}:
            _rprev = root.status
            graph = self._graph.query_task_dashboard(task_id)
            _owner = graph.extend_props.get("owner_bot_id") or ""
            _root_out = self._rollup_done_children_output(task_id, root.node_id)
            _root_acc = self._build_parent_acceptance_result(root, pr)
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id, node_id=root.node_id,
                    acceptance_result=_root_acc, output_patch=_root_out,
                    run_mode="single_bot" if _owner else None,
                    assignee=_owner or None,
                )
            )
            # 动作历史:TRANSITION(根 gap 闭终验通过 → root SUCCESS)
            self._log_action(
                task_id,
                root.node_id,
                NodeAction.TRANSITION,
                {"reason": "root_gap_closed", "to": "SUCCESS"},
                status_from=_rprev,
                status_to=Status.SUCCESS,
            )
        # 终态镜像:root 已 DONE → graph 镜像 DONE(all_done 标记);不再 graph 独立先写 status
        self._sync_graph_status_to_root(task_id)
        self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(output_patch={"result": "all_done"})
        )
