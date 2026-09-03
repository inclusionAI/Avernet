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
