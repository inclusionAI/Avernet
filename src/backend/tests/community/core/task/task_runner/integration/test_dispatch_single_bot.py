import asyncio

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition
from agentclaw.community.core.task.task_plan.static_plan_runtime import StaticPlanRuntime
from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiAuthError,
    OpenApiBadRequestError,
)
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    PromptFormatterImpl,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
    TaskExecutor,
)
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsCreateGroupResult,
)
from agentclaw.community.core.task.task_runner.client.double.double_bcs_bot_identity_resolver import (
    _DoubleBcsBotIdentityResolver,
)


def _node(assignee="bot9:ent1", extend_props=None):
    return TaskNode(
        node_id="c1",
        task_id="t1",
        status=Status.RUNNING,
        task_spec=TaskSpec(
            Metadata("t1", "T", "do"),
            Context("bg"),
            Goal("O", [AcceptanceCriteria("a1", "d")]),
        ),
        run_info=RuntimeInfo(
            run_mode="single_bot", assignee=assignee, extend_props=extend_props or {}
        ),
        node_run_graph=None,
    )  # type: ignore[arg-type]


class _Bot:
    def __init__(self, run_id="mid_1", session_id=None, grant_fail=False, send_error=None):
        self._rid = run_id
        self._sid = session_id
        self._gf = grant_fail
        self._send_error = send_error
        self.sent = []

    async def ensure_grant(self, bot_id):
        # 任务认领授权已由前端经 task_grant_service 透传到 secbaas;派发不调 ensure_grant(协议契约保留)。
        if self._gf:
            raise OpenApiAuthError("403")

    async def send_message(self, *, bot_id, message, metadata):
        from agentclaw.community.core.task.task_runner.client.ports import (
            BotSendResult,
        )

        if self._send_error is not None:
            raise self._send_error
        self.sent.append((bot_id, message, metadata))
        return BotSendResult(run_id=self._rid, session_id=self._sid)


class _Graph:
    """Capture _persist_dispatch_ids patches for assertion."""

    def __init__(self):
        self.patches = []

    def update_task_node_info(self, patch):
        self.patches.append(patch)

    def query_task_dashboard(self, task_id, node_id=None):
        return None


class _Poller:
    def __init__(self):
        self.registered = []

    def register(self, h):
        self.registered.append(h)

    def pending(self):
        return len(self.registered)


class _Ctx:
    def build(self, task_id, node_id):
        return {"mode": "execute", "node_spec": None}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_dispatch_single_bot_registers_handle():
    bot = _Bot()
    poller = _Poller()
    fmt = PromptFormatterImpl()
    exe = TaskExecutor(
        bot=bot, bcs=None, formatter=fmt, context=_Ctx(), sink=None, poller=poller,
        task_settings=_TaskSettingsOff()
    )
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    assert bot.sent[0][0] == "bot9:ent1"
    assert poller.registered[0].run_id == "mid_1"
    assert poller.registered[0].loop_task_id == "t1::c1"


@pytest.mark.parametrize(
    "send_error",
    [OpenApiAuthError("403"), OpenApiBadRequestError("400")],
    ids=["auth", "bad-request"],
)
def test_dispatch_single_bot_rejects_non_retryable_openapi_error(send_error):
    """不可重试的 OpenAPI 4xx 派发失败应留给 harness，不能注册虚假执行句柄。"""
    bot = _Bot(send_error=send_error)
    poller = _Poller()
    executor = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=poller,
        task_settings=_TaskSettingsOff(),
    )

    assert _run(executor.dispatch([_node()])) == [False]
    assert bot.sent == []
    assert poller.registered == []



def test_dispatch_single_bot_composes_owner_for_pure_assignee():
    bot = _Bot()
    poller = _Poller()
    exe = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=poller,
        task_settings=_TaskSettingsOff(),
    )

    ok = _run(exe.dispatch([_node("default", {"assignee_owner_id": "146836"})]))

    assert ok == [True]
    assert bot.sent[0][0] == "default:146836"
    assert poller.registered[0].bot_id == "default:146836"


def test_dispatch_single_bot_rebuilds_composite_with_explicit_owner():
    bot = _Bot()
    poller = _Poller()
    exe = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=poller,
        task_settings=_TaskSettingsOff(),
    )

    ok = _run(
        exe.dispatch([_node("default:old-owner", {"assignee_owner_id": "146836"})])
    )

    assert ok == [True]
    assert bot.sent[0][0] == "default:146836"
    assert poller.registered[0].bot_id == "default:146836"


def test_dispatch_single_bot_uses_explicit_owner_for_composite_assignee():
    bot = _Bot()
    poller = _Poller()
    exe = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=poller,
        task_settings=_TaskSettingsOff(),
    )

    ok = _run(
        exe.dispatch([_node("default:146836", {"assignee_owner_id": "other-owner"})])
    )

    assert ok == [True]
    assert bot.sent[0][0] == "default:other-owner"


def test_dispatch_single_bot_sends_assignee_verbatim():
    """single_bot 直接按 assignee 原样派发(claim_on JOIN 在派发策略层完成,执行器不做表级授权 JOIN)。"""
    bot = _Bot()
    poller = _Poller()
    exe = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=poller,
        task_settings=_TaskSettingsOff(),
    )
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    assert bot.sent[0][0] == "bot9:ent1"  # assignee 原样,不归一


def test_prompt_formatter_uses_context_and_node_spec():
    fmt = PromptFormatterImpl()
    n = _node()
    s = fmt.format_execute({"mode": "execute", "node_instruction": "分析行业"}, n)
    assert "分析行业" in s
    assert "验收标准" in s
    # 默认 skill_report_enabled=true,使用 HTTP Push 协议。
    assert '"status": "SUCCESS"' in s
    assert '"task_id"' in s
    assert '"acceptance_result"' in s
    assert '"verdict": "DONE"' in s
    assert '"acceptances_metric"' in s
    assert '"gaps": []' in s


def test_prompt_formatter_disabled_report_does_not_inject_platform_protocol():
    """skill_report_enabled=false 时只禁止 Bot 主动 callback,不注入平台回收协议。"""
    fmt = PromptFormatterImpl()
    n = _node()
    s = fmt.format_execute({
        "mode": "execute",
        "node_instruction": "分析行业",
        "execution_mode": "single_bot",
        "skill_report_enabled": False,
    }, n)
    assert "本节点结果由平台接口负责回收" in s
    assert "不要主动调用 /callback/report" in s
    assert "callback/report" in s  # 仅作为禁止主动调用的边界说明
    assert '"success": true' not in s
    assert "poller" not in s


def test_prompt_formatter_skill_report_on_uses_http_post():
    """skill_report_enabled=true 时使用 HTTP Push 协议。"""
    fmt = PromptFormatterImpl()
    n = _node()
    s = fmt.format_execute({
        "mode": "execute",
        "node_instruction": "分析行业",
        "execution_mode": "single_bot",
        "skill_report_enabled": True,
    }, n)
    assert "callback/report" in s
    assert '"status": "SUCCESS"' in s
    assert '"success": true' not in s
    assert '"verdict": "DONE"' in s
    assert '"acceptances_metric"' in s
    assert "阶段1 在本条消息正文给出的真实业务产出" in s
    assert "收到 HTTP 200 即视为本节点上报完成" in s
    assert "严禁在收尾轮再次贴出阶段1执行产出" in s
    assert "上报只能用 exec/curl" in s
    assert "不得对同一节点重复 POST" in s

def test_prompt_formatter_relay_appends_protocol_and_chinese_constraint():
    """# 接自 接力分支(static_plan):交接正文 + 执行闭环(禁联网/平台回收/接力交接,不含 HTTP 上报协议)+ 中文输出约束。"""
    from agentclaw.community.core.task.domain.models import (
        Goal, Metadata, Context, TaskSpec, TaskNode, RuntimeInfo, Status,
    )
    fmt = PromptFormatterImpl()
    relay = "# 接自:上游Bot\n## 上游产出正文\n上游摘要\n## 本角色任务\n执行投放"
    n = TaskNode(node_id="n1", task_id="t1", status=Status.RUNNING,
                 task_spec=TaskSpec(Metadata("t1", "T", relay), Context("bg"), Goal("O", [])),
                 run_info=RuntimeInfo(), node_run_graph=None)  # type: ignore[arg-type]
    s = fmt.format_execute({
        "mode": "execute", "node_instruction": relay,
        "skill_report_enabled": True, "backend": "http://b", "task_id": "t1", "node_id": "n1",
    }, n)
    assert "# 接自:上游Bot" in s and "执行投放" in s
    # static_plan 接力分支不注入 HTTP 上报协议(结果由平台统一回收,不让 bot 真去调上报接口)
    assert "回调地址" not in s and "callback/report" not in s
    assert '"task_id": "t1"' not in s and '"verdict": "DONE"' not in s
    # 注入执行闭环(禁联网 + 平台统一回收 + 接力执行),无 mock/演示字样
    assert "执行约束" in s and "平台统一回收" in s and "无需你主动调用上报接口" in s
    assert "mock" not in s and "演示" not in s
    # 强制承接→执行→交接三步顺序,三步缺一不可;回复不得标 step 编号
    assert "三步缺一不可" in s
    assert "接力上下文" in s and "执行产出" in s and "gap与交接" in s
    assert "step1" not in s and "step2" not in s and "step3" not in s
    assert "获取上方最新统一上下文" in s
    # 协作群多轮接力:driver先承接→派发非human成员+driver自执行→成员不卡流程→收齐汇总;单人接力一次性
    assert "协作群多轮接力" in s and "单人接力" in s
    assert "bcs_assign_task" in s and "bcs_route" in s
    assert "driver 自己也是执行者" in s and "不得把全部执行甩给成员" in s
    assert "human 仅为观察者" in s
    assert "视为已提供全部所需上下文" in s and "严禁以“缺详情/需完整方案”为由" in s
    assert "交接只描述,不路由群外" in s and "正文不重复输出" in s
    assert "bcs_task_complete" in s and "bcs_fuse" in s
    assert "按问题智能匹配能力" not in s
    # 中文输出约束
    assert "必须使用中文" in s


def test_static_relay_prompt_waits_for_every_member_and_preserves_markdown():
    """静态协作接力必须在全员完成后唯一汇总，并要求结构化 Markdown 产出。"""
    relay = (
        "# 接自:上游Bot\n"
        "## 群组成\n- driver Bot\n- consultant Bot\n"
        "## 上游产出正文\n上游结论\n"
        "## 本角色任务\n联合完成评审"
    )
    node = _node()
    node.task_spec.metadata.instruction = relay

    prompt = PromptFormatterImpl().format_execute(
        {"mode": "execute", "node_instruction": relay},
        node,
    )

    assert "只有当所有成员(含 driver 作 worker)都已在本群回复各自产出后" in prompt
    assert "若仍有成员未回复,继续等待,不得提前收尾" in prompt
    assert "执行产出与 gap交接只在此轮给出" in prompt
    assert "正文须保留 markdown 排版" in prompt
    assert "标题、段落、列表、表格、加粗" in prompt
    assert "4) 汇总与交接" not in prompt



def test_dispatch_single_bot_persists_session_and_run_id_to_extend_props():
    bot = _Bot(run_id="r_1", session_id="s_1")
    poller = _Poller()
    graph = _Graph()
    exe = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=poller,
        graph=graph,
    )
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    assert len(graph.patches) == 1
    patch = graph.patches[0]
    assert patch.task_id == "t1"
    assert patch.node_id == "c1"
    ep = patch.extend_props_patch
    assert ep.get("session_id") == "s_1"
    assert ep.get("run_id") == "r_1"
    assert "group_id" not in ep  # 单 bot 无群 id,不写 group_id 键


class _TaskSettingsOn:
    """Fake task settings: skill_report 开关返回 True(skill HTTP 上报链路)。"""

    def is_enabled(self, setting_type):
        return setting_type == "skill_report_enabled"

    def get_enabled(self, *, setting_type, env):
        return setting_type == "skill_report_enabled"

    def set_enabled(self, *, setting_type, enabled, env, operator=None):
        return setting_type == "skill_report_enabled" and enabled


class _TaskSettingsOff:
    """Fake task settings: skill_report_enabled=false，使用 poller Pull 回收。"""

    def is_enabled(self, setting_type):
        return False

    def get_enabled(self, *, setting_type, env):
        return False

    def set_enabled(self, *, setting_type, enabled, env, operator=None):
        return False


def test_static_plan_owner_reaches_final_openapi_bot_identity():
    """static plan owner 透传必须跨 runtime、dispatcher 和 executor 到达最终 Bot ID。"""
    definition = StaticPlanDefinition.from_yaml(
        """
        template_id: owner-propagation
        nodes:
          - id: worker
            type: bot
            bot_id: worker-bot
            task: 完成执行
        """
    )
    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        task_id="t1",
        extend_props={"owner_user_id": "owner-1"},
    )
    root = TaskNode(
        node_id="t1",
        task_id="t1",
        status=Status.PLANNING,
        task_spec=TaskSpec(
            Metadata("t1", "root", "root"),
            Context(""),
            Goal("root", []),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=graph,
    )
    graph.tasks.append(root)
    runtime = StaticPlanRuntime(definition, {})
    graph.tasks.extend(runtime.nodes("t1", root.task_spec))
    ready = list(runtime.ready(graph).ready)

    class _GraphView:
        def query_task_dashboard(self, task_id):
            assert task_id == "t1"
            return graph

    dispatched = _run(TaskDispatcher(_GraphView()).dispatch(ready))
    bot = _Bot()
    executor = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=_Poller(),
        task_settings=_TaskSettingsOff(),
    )

    assert _run(executor.dispatch(dispatched)) == [True]
    assert bot.sent[0][0] == "worker-bot:owner-1"


def test_dispatch_skill_report_on_skips_poller_registration():
    """开关开启时 single_bot 走 skill HTTP 上报链路,不注册 poller(与 poller 互斥,不并存)。"""
    bot = _Bot()
    poller = _Poller()
    exe = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=poller,
        task_settings=_TaskSettingsOn(),
    )
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    assert bot.sent  # 仍投递消息给 bot
    assert poller.registered == []  # skill 上报模式下不注册 poller,避免双链路并存


def test_dispatch_skill_report_off_registers_poller():
    """skill_report_enabled=false 时注册 poller Pull 回收。"""
    bot = _Bot()
    poller = _Poller()
    exe = TaskExecutor(
        bot=bot,
        bcs=None,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=poller,
        task_settings=_TaskSettingsOff(),
    )
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    assert poller.registered and poller.registered[0].run_id == "mid_1"


# ── P2:singlebot_2_group 旁路(默认 true)── 二人群 + 人类观察者 + coop_group 收敛 ──


class _Bcs2:
    def __init__(self):
        self.created = []

    async def create_group(self, req):
        self.created.append(req)
        return BcsCreateGroupResult(group_id="g2g", session_id="s2g", definition_ref=None)

    async def get_group(self, group_id):
        return {"latest_running_session_id": "s2g"}

    async def get_session_messages(self, sid, *, limit=50, since_msg_id=None):
        return []

    def task_callback_url(self):
        return ""


class _Dash:
    def __init__(self, execution_config):
        self.extend_props = {"execution_config": execution_config, "owner_user_id": "35983"}


class _Graph2:
    """query_task_dashboard 返带 execution_config 的快照;update_task_node_info 捕获 patch。"""

    def __init__(self, execution_config=None):
        self._ec = execution_config if execution_config is not None else {}
        self.patches = []

    def update_task_node_info(self, patch):
        self.patches.append(patch)

    def query_task_dashboard(self, task_id, node_id=None):
        return _Dash(self._ec)


def _exe2(*, execution_config=None):
    bot = _Bot()
    bcs = _Bcs2()
    poller = _Poller()
    graph = _Graph2(execution_config)
    exe = TaskExecutor(
        bot=bot, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
        poller=poller, graph=graph, identity_resolver=_DoubleBcsBotIdentityResolver(),
    )
    return exe, bot, bcs, poller, graph


def test_dispatch_single_bot_2_group_bypass_creates_two_person_group():
    """默认 true + owner 在场 + bcs 已接 → 建 manager_worker 群(single bot 作 manager,人类 owner 作
    观察者 participant),走 BcsGroupHandle 收敛,不发 send_message;originator 不设人类(BCS 拒人类
    建群 → 403);run_mode 落库 single_bot→coop_group 且 extend_props.actual_run_mode=single_bot。"""
    exe, bot, bcs, poller, graph = _exe2()  # execution_config={} → singlebot_2_group 默认 true
    ok = _run(exe.dispatch([_node("drv", {"assignee_owner_id": "35983"})]))
    assert ok == [True]
    assert bot.sent == []  # 旁路:不直发
    assert len(bcs.created) == 1
    req = bcs.created[0]
    assert req.group_strategy == "manager_worker"
    assert {"bot_uuid": "drv:double-owner", "role": "manager"} in req.participants
    assert {"bot_uuid": "human_35983", "bot_name": "35983", "role": "observer"} in req.participants
    assert req.routing_policy == {"default_bot_final_delivery": "inject_observers"}
    assert req.originator is None  # manager 作建群 caller,不设人类 originator(BCS 拒人类建群)
    assert poller.registered == []  # 默认 Push(skill_report),singlebot_2_group 不注册 Pull poller
    flip = [p for p in graph.patches if p.run_mode == "coop_group"]
    assert flip and flip[0].extend_props_patch.get("actual_run_mode") == "single_bot"


def test_dispatch_single_bot_2_group_disabled_falls_back_to_send():
    """singlebot_2_group=false(owner+bcs 在场)→ 走老链路:send_message + SingleBotHandle,不建群。"""
    exe, bot, bcs, poller, graph = _exe2(execution_config={"singlebot_2_group": False})
    ok = _run(exe.dispatch([_node("drv", {"assignee_owner_id": "35983"})]))
    assert ok == [True]
    assert bot.sent and bot.sent[0][0] == "drv:35983"  # 老链路直发
    assert bcs.created == []
    assert poller.registered == []  # 默认 Push，不注册 poller


def test_dispatch_single_bot_2_group_no_owner_falls_back_to_send():
    """owner 缺失 → 即便 singlebot_2_group 默认 true,也回退老链路(二人群需要人类)。"""
    exe, bot, bcs, poller, graph = _exe2()  # 默认 true
    ok = _run(exe.dispatch([_node("drv")]))  # 无 assignee_owner_id
    assert ok == [True]
    assert bot.sent  # 老链路
    assert bcs.created == []
    assert poller.registered == []  # 默认 Push，不注册 poller
