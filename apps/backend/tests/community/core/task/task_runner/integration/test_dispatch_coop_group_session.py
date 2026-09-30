import asyncio

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import BcsCreateGroupResult
from agentclaw.community.core.task.task_runner.client.prompt_formatter import PromptFormatterImpl
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import TaskExecutor
from agentclaw.community.core.task.task_runner.client.double.double_bcs_bot_identity_resolver import (
    _DoubleBcsBotIdentityResolver,
)


def _node(group_id="g1", task_id="t1"):
    return TaskNode(node_id="n1", task_id=task_id, status=Status.RUNNING,
                    task_spec=TaskSpec(Metadata(task_id, "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode="coop_group", assignee=group_id),
                    node_run_graph=None)  # type: ignore[arg-type]


class _Bcs:
    def __init__(self):
        self.created = []
        self.sessions = []

    async def create_group(self, req):
        self.created.append(req)
        # 建群自带初始 session(BCS create_group 返 session_id);chat 派发复用之,不再 create_session。
        return BcsCreateGroupResult(group_id="g1", session_id="s1", definition_ref=None)

    async def create_session(self, group_id, *, bootstrap_prompt=None, idempotency_key=None):
        self.sessions.append((group_id, bootstrap_prompt))
        return "s1"

    async def get_group(self, group_id):
        return {"session": {"status": "completed", "output": {"r": 1}}}

    async def get_session_messages(self, sid, *, limit=50, since_msg_id=None):
        return []


class _Poller:
    def __init__(self):
        self.registered = []

    def register(self, h):
        self.registered.append(h)


class _Ctx:
    def build(self, task_id, node_id):
        return {"mode": "execute"}



class _TaskSettingsOff:
    def is_enabled(self, setting_type):
        return False


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_form_coop_group_chat_stores_meta_and_returns_gid():
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    gid = _run(exe.form_coop_group(GroupFormation(bot_ids=["drv", "w1"], collab_mode="chat")))
    assert gid == "g1"
    assert exe._group_meta["g1"]["collab_mode"] == "chat"
    assert bcs.created[0].group_strategy is None  # chat 省略
    assert bcs.created[0].driver_bot == "drv:double-owner"
    assert bcs.created[0].master_bot is None  # master_bot 仅 manager_worker 设置
    assert [p["bot_uuid"] for p in bcs.created[0].participants] == [
        "drv:double-owner", "w1:double-owner"
    ]


def test_form_coop_group_manager_worker_sets_strategy():
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(bot_ids=["mgr", "w1"], collab_mode="manager_worker",
                                            extend_props={"manager_bot_id": "mgr"})))
    assert bcs.created[0].group_strategy == "manager_worker"
    assert bcs.created[0].driver_bot == "mgr:double-owner"
    assert bcs.created[0].master_bot == "mgr:double-owner"  # master 即 driver/manager

def test_form_coop_group_relay_footer_only_reporter_no_duplicate_protocol():
    """# 接力协作群(static_plan):task_instruction 已含 format_execute(# 接自 分支)注入的执行闭环
    (禁联网/平台回收/接力交接,不含 HTTP 上报协议),form_coop_group 只补 driver/reporter 定位脚注,
    不再重复 目标/验收标准/任务上下文——静态接力 Goal.acceptances=[] 会打印空 验收标准:[](末尾偏置
    误导 bot 跳过验收)。"""
    bcs = _Bcs()
    fmt = PromptFormatterImpl()
    relay_body = "# 接自:上游Bot\n## 上游产出正文\n上游摘要\n## 本角色任务\n执行投放"
    n = TaskNode(node_id="n1", task_id="t1", status=Status.RUNNING,
                 task_spec=TaskSpec(Metadata("t1", "T", relay_body), Context("bg"), Goal("O", [])),
                 run_info=RuntimeInfo(), node_run_graph=None)  # type: ignore[arg-type]
    fc_msg = fmt.format_execute({
        "mode": "execute", "node_instruction": relay_body, "skill_report_enabled": True,
        "backend": "http://b", "task_id": "t1", "node_id": "n1",
    }, n)
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=fmt, context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["mgr", "w1"], collab_mode="manager_worker",
        extend_props={"manager_bot_id": "mgr", "loop_task_id": "t1::n1", "task_instruction": fc_msg},
    )))
    ctx = bcs.created[0].context
    # static_plan 接力:不注入 HTTP 上报协议(回调地址/请求体/verdict/acceptances_metric)
    assert "回调地址" not in ctx and "callback/report" not in ctx
    assert '"verdict"' not in ctx and '"acceptances_metric"' not in ctx
    # 接力脚注仅保留 driver/reporter 定位(协作群分工,不提上报回投),无 mock 字样
    assert "reporter_bot_id=mgr" in ctx and "reporter_role=master/manager" in ctx
    assert "协作群分工" in ctx
    assert "mock" not in ctx and "演示" not in ctx
    # 不再重复打印空验收标准 / 任务上下文
    assert "验收标准:" not in ctx
    assert "回投请求体只能包含" not in ctx
    assert "任务上下文:" not in ctx


def test_form_coop_group_relay_appends_closure_for_raw_instruction():
    """# 真正多 bot 协作群(static_plan):task_instruction 由 engine 直取 raw metadata.instruction
    (未走 format_execute,无 _static_relay_closure/中文约束),form_coop_group 按需补承接→执行→交接
    三步硬约束 + 中文约束,避免协作群 bot 塌缩只做执行、跳过接力接自/gap与派发。"""
    bcs = _Bcs()
    fmt = PromptFormatterImpl()
    raw_relay = (
        "# 接自:营销Bot(营销Bot 执行完计算 gap 后规划的下一步任务=商场与平台侧评审,其交付=完整营销方案见上方\"## 上游产出正文\")\n"
        "## 群组成\n- 商场运营Bot(driver/总结者)\n- 线上平台Bot\n"
        "## 上游产出正文\n营销方案:新客体验券+护理套餐+会员机制。\n"
        "## 本群任务\n【接力执行】\n"
        "接力上下文:接自营销Bot,承接评审任务。职责边界:给修订条件,默认不打回。\n"
        "执行产出:逐项给修订条件——商场:展位报批;平台:券有效期明示。\n"
        "gap与交接:对照合规计算 gap,定下一步任务=利润核算,经搜推命中店主Bot,派发执行。\n"
    )
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=fmt, context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["mgr", "w1"], collab_mode="manager_worker",
        extend_props={"manager_bot_id": "mgr", "loop_task_id": "t1::n1", "task_instruction": raw_relay},
    )))
    ctx = bcs.created[0].context
    # raw 指令(无 closure)按需补承接→执行→交接三步硬约束 + 中文约束
    assert "三步缺一不可" in ctx
    assert "获取上方最新统一上下文" in ctx
    # 协作群多轮接力:driver先承接→派发非human成员+driver自执行→成员不卡流程→收齐汇总;单人接力一次性
    assert "协作群多轮接力" in ctx and "单人接力" in ctx
    assert "bcs_assign_task" in ctx and "bcs_route" in ctx
    assert "driver 自己也是执行者" in ctx and "不得把全部执行甩给成员" in ctx
    assert "human 仅为观察者" in ctx
    assert "视为已提供全部所需上下文" in ctx and "严禁以“缺详情/需完整方案”为由" in ctx
    assert "交接只描述,不路由群外" in ctx and "正文不重复输出" in ctx
    assert "bcs_task_complete" in ctx and "bcs_fuse" in ctx
    assert "按问题智能匹配能力" not in ctx
    assert "必须使用中文" in ctx
    # 仍只补 driver/reporter 定位脚注(协作群分工),不注入上报协议/mock
    assert "reporter_bot_id=mgr" in ctx and "协作群分工" in ctx
    assert "回调地址" not in ctx and "callback/report" not in ctx
    assert "mock" not in ctx and "演示" not in ctx


def test_dispatch_coop_group_session_mode_registers_session_handle():
    bcs = _Bcs()
    poller = _Poller()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=poller, identity_resolver=_DoubleBcsBotIdentityResolver(),
                       task_settings=_TaskSettingsOff())
    _run(exe.form_coop_group(GroupFormation(bot_ids=["drv"], collab_mode="chat")))
    ok = _run(exe.dispatch([_node(group_id="g1")]))
    assert ok == [True]
    assert bcs.sessions == [], "chat 派发应复用建群初始 session,不再 create_session"
    h = poller.registered[0]
    assert h.session_id == "s1" and h.run_id is None and h.collab_mode == "chat"
    assert h.loop_task_id == "t1::n1"


def test_form_coop_group_appends_human_observer_when_owner_present():
    """P1:有 owner_user_id 时,人类观察者(不发言)被追加为 participant,且 routing_policy.inject_observers 默认生效。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["drv", "w1"], collab_mode="chat",
        extend_props={"owner_user_id": "35983"},
    )))
    req = bcs.created[0]
    assert {"bot_uuid": "human_35983", "bot_name": "35983", "role": "observer"} in req.participants
    assert req.routing_policy == {"default_bot_final_delivery": "inject_observers"}
    assert req.originator is None  # originator 须为 Bot Actor(BCS 拒 human);人类仅作 participant 观察者


def test_form_coop_group_no_human_observer_when_owner_absent():
    """无 owner_user_id → 不追加人类观察者、不设 routing_policy(向后兼容,现有协作群不变)。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(bot_ids=["drv", "w1"], collab_mode="chat")))
    req = bcs.created[0]
    assert all(not str(p.get("bot_uuid", "")).startswith("human_") for p in req.participants)
    assert req.routing_policy is None


class _OwnerDash:
    def __init__(self, owner_user_id):
        self.extend_props = {"owner_user_id": owner_user_id}


class _OwnerGraph:
    """form_coop_group 反查 owner_user_id 用:query_task_dashboard 返带 owner 的快照。"""

    def __init__(self, owner_user_id):
        self._owner = owner_user_id
        self.patches = []

    def update_task_node_info(self, patch):
        self.patches.append(patch)

    def query_task_dashboard(self, task_id, node_id=None):
        return _OwnerDash(self._owner)


def test_form_coop_group_recovers_owner_via_task_id_for_run_yaml_path():
    """P1:_run_yaml/start_coop_group 路径的 GF 只带 task_id(无 owner_user_id/loop_task_id),
    经 graph.query_task_dashboard(task_id).extend_props[owner_user_id] 回补 → 仍追加人类观察者 +
    routing_policy + originator=human_<owner>(对齐拉人接口示例)。"""
    bcs = _Bcs()
    exe = TaskExecutor(
        bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
        poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver(),
        graph=_OwnerGraph("35983"),
    )
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["drv", "w1"], collab_mode="chat",
        extend_props={"task_id": "t1"},
    )))
    req = bcs.created[0]
    assert {"bot_uuid": "human_35983", "bot_name": "35983", "role": "observer"} in req.participants
    assert req.routing_policy == {"default_bot_final_delivery": "inject_observers"}
    assert req.originator is None  # originator 须为 Bot Actor(BCS 拒 human);人类仅作 participant 观察者


def test_form_coop_group_singlebot_2_group_uses_single_business_protocol():
    """单 Bot 退化群也走 manager-worker 的统一业务协议，而不嵌套旧 prompt。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver(),
                       api_base_url="http://b")
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["mgr"], collab_mode="manager_worker",
        members_info=[{"bot_id": "mgr", "role": "manager"}],
            extend_props={
                "manager_bot_id": "mgr", "dynamic_task_node_protocol": True,
                "loop_task_id": "t1::n1", "task_id": "t1",
                "task_objective": "O", "task_instruction": "分析存储行业",
                "acceptances": [{"id": "a1", "description": "d"}],
            },
    )))
    ctx = bcs.created[0].context
    assert "【业务节点执行协议】" in ctx
    assert "POST http://b/api/v1/collaboration/tasks/callback/report" in ctx
    assert "acceptances_metric 必须逐条且仅一次覆盖" in ctx
    assert "阶段1 执行" not in ctx
    assert "bcs_assign_task" not in ctx
    assert "bcs_task_complete" not in ctx
    assert ctx.count("[task-execute]") == 1
    assert "完整协作群执行输出" not in ctx


def test_dynamic_group_rewrites_legacy_business_envelope_to_unified_protocol():
    """动态群不得因旧指令文本而回退到旧通用上下文。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver(),
                       api_base_url="http://b")
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["mgr", "worker"], collab_mode="manager_worker",
        members_info=[{"bot_id": "mgr", "role": "manager"}, {"bot_id": "worker", "role": "worker"}],
        extend_props={
            "manager_bot_id": "mgr", "dynamic_task_node_protocol": True,
            "loop_task_id": "t1::n1", "task_id": "t1", "task_objective": "O",
            "task_instruction": "请严格按以下阶段执行，执行、校验、验收、上报均不可跳过。旧协议正文",
            "acceptances": [{"id": "a1", "description": "d"}],
        },
    )))

    context = bcs.created[0].context
    assert "【业务节点执行协议】" in context
    assert "旧协议正文" in context
    assert context.count("[task-execute]") == 1


def test_manager_worker_uses_unified_business_protocol_for_one_or_many_bots():
    """单 Bot 退化群和多 Bot 群共享业务协议；差别仅在执行者名单。"""
    contexts = []
    for bot_ids in (["mgr"], ["mgr", "worker"]):
        bcs = _Bcs()
        exe = TaskExecutor(
            bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
            poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver(),
            api_base_url="http://backend",
        )
        _run(exe.form_coop_group(GroupFormation(
            bot_ids=bot_ids,
            collab_mode="manager_worker",
            members_info=[{"bot_id": bot_id} for bot_id in bot_ids],
            extend_props={
                "manager_bot_id": "mgr",
                "dynamic_task_node_protocol": True,
                "loop_task_id": "t1::n1",
                "task_id": "t1",
                "task_objective": "给出可验收结论",
                "task_instruction": "分析给定材料",
                "acceptances": [{"id": "a1", "description": "结论可复核"}],
                "upstream_outputs": {"n0": "上游结论"},
            },
        )))
        contexts.append(bcs.created[0].context)

    for context in contexts:
        assert "【业务节点执行协议】" in context
        assert "[task-loop] loop_task_id=t1::n1; backend=http://backend" in context
        assert "POST http://backend/api/v1/collaboration/tasks/callback/report" in context
        assert "acceptances_metric 必须逐条且仅一次覆盖" in context
        assert "bcs_assign_task" not in context
        assert "bcs_task_complete" not in context
        assert context.count("/api/v1/collaboration/tasks/callback/report") == 1
    assert '本群执行者: ["mgr"]' in contexts[0]
    assert '本群执行者: ["mgr", "worker"]' in contexts[1]
