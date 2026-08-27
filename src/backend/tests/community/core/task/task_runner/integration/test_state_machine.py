import asyncio

import httpx

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsCreateGroupResult, BcsHttpAdapter,
)
from agentclaw.community.core.task.task_runner.integration.prompt_formatter import PromptFormatterImpl
from agentclaw.community.core.task.task_runner.integration.task_executor import (
    TaskExecutor, _BCN_EVENT_CALLBACK_PATH,
)
from agentclaw.community.core.task.task_runner.integration.double.double_bcs_bot_identity_resolver import (
    _DoubleBcsBotIdentityResolver,
)


class _Tok:
    token = "drv"
    secret = "s3c"
    base_url = "http://bcs"


def _adapter(handler):
    return BcsHttpAdapter(_Tok(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                                base_url="http://bcs"))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_start_state_machine_run_returns_run_id():
    def h(req):
        assert req.url.path == "/groups/g1/state-machine-runs"
        body = req.read().decode()
        assert '"id":"d1"' in body and '"version":1' in body
        return httpx.Response(202, json={"run": {"run_id": "run_9"}})

    a = _adapter(h)
    rid = _run(a.start_state_machine_run("g1", definition_yaml=None,
                                         definition_ref={"id": "d1", "version": 1},
                                         session_id=None, input={"query": "q"}))
    assert rid == "run_9"


def test_get_state_machine_run():
    def h(req):
        assert req.url.path == "/state-machine-runs/run_9"
        return httpx.Response(200, json={"status": "completed", "output": {"x": 1}})

    d = _run(_adapter(h).get_state_machine_run("run_9"))
    assert d["status"] == "completed"


def _node(group_id="g1"):
    return TaskNode(node_id="n1", task_id="t1", status=Status.RUNNING,
                    task_spec=TaskSpec(Metadata("t1", "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode="coop_group", assignee=group_id),
                    node_run_graph=None)  # type: ignore[arg-type]


class _Bcs:
    def __init__(self):
        self.run_input = None
        self.created_req = None

    async def create_group(self, req):
        self.created_req = req
        return BcsCreateGroupResult(group_id="g1", definition_ref={"id": "d1", "version": 1})

    async def start_state_machine_run(self, group_id, *, definition_yaml, definition_ref, session_id, input):
        self.run_input = input
        return "run_9"

    async def get_state_machine_run(self, run_id):
        return {"status": "completed", "output": {}}


class _Poller:
    def __init__(self):
        self.registered = []

    def register(self, h):
        self.registered.append(h)


class _Ctx:
    def build(self, task_id, node_id):
        return {"mode": "execute"}


def test_dispatch_state_machine_registers_run_handle():
    bcs = _Bcs()
    poller = _Poller()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=poller, identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(bot_ids=["drv"], collab_mode="state_machine",
                                            members_info=[{"bot_id": "drv", "role": "manager"}],
                                            extend_props={"collaboration_definition_yaml": "kind: collab"})))
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    h = poller.registered[0]
    assert h.run_id == "run_9" and h.session_id is None and h.collab_mode == "state_machine"
    assert bcs.run_input["query"]  # format_execute 产出


def test_state_machine_binding_keys_are_roles_and_values_are_bcs_uuids():
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["mgr", "worker"],
        collab_mode="state_machine",
        members_info=[
            {"bot_id": "mgr", "role": "manager"},
            {"bot_id": "worker", "role": "researcher"},
        ],
        extend_props={"collaboration_definition_yaml": "kind: collab"},
    )))
    req = bcs.created_req
    assert req is not None
    assert set(req.participant_bindings) == {"manager", "researcher"}
    assert req.participant_bindings["manager"]["bot_ids"] == ["mgr:double-owner"]
    assert req.participant_bindings["researcher"]["bot_ids"] == ["worker:double-owner"]
    # state_machine 群 participants 不带 role(BCS 按 bot_uuid vs driver_bot 自行推断;带 role 会被 BCS 400);
    # 逻辑角色经 participant_bindings 绑定。
    assert all(set(p) == {"bot_uuid"} for p in req.participants), "state_machine participants 不得带 role"
    assert [p["bot_uuid"] for p in req.participants] == ["mgr:double-owner", "worker:double-owner"]


class _Graph:
    """记录 update_task_node_info 的 patch(白盒断言动态派发写了哪些 extend_props)。"""

    def __init__(self):
        self.patches: list = []

    def update_task_node_info(self, patch):
        self.patches.append(patch)


def test_dispatch_state_machine_persists_group_run_ids_to_node_extend_props():
    """动态派发 state_machine 后,group_id/run_id 应落进节点 run_info.extend_props(dashboard 可见),
    补齐 _run_yaml 路径之外动态 coop_group 节点的 group/run 透出 gap。"""
    bcs = _Bcs()
    poller = _Poller()
    graph = _Graph()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=poller, identity_resolver=_DoubleBcsBotIdentityResolver(), graph=graph)
    _run(exe.form_coop_group(GroupFormation(bot_ids=["drv"], collab_mode="state_machine",
                                            members_info=[{"bot_id": "drv", "role": "manager"}],
                                            extend_props={"collaboration_definition_yaml": "kind: collab"})))
    _run(exe.dispatch([_node()]))
    ep_patches = [p for p in graph.patches if p.extend_props_patch]
    assert ep_patches, "动态派发未写节点 extend_props"
    patch = ep_patches[-1]
    assert patch.task_id == "t1" and patch.node_id == "n1"
    assert patch.extend_props_patch.get("group_id") == "g1"
    assert patch.extend_props_patch.get("run_id") == "run_9"


class _LatestSessionBcs:
    """fake BcsClientPort for get_group_session:get_group 返 latest_running_session_id;
    create_session 被调即失败(断言不再新建 session)。"""

    def __init__(self, latest):
        self.latest = latest
        self.create_called = False

    async def get_group(self, group_id):
        return {"latest_running_session_id": self.latest}

    async def create_session(self, group_id, *, bootstrap_prompt=None, idempotency_key=None):
        self.create_called = True
        raise AssertionError("get_group_session 不得再 create_session 新建 session")


def test_get_group_session_reads_latest_running_session_id_not_create():
    """get_group_session 经 GET /groups/{id} 的 latest_running_session_id 取最近 session,不再 create_session。"""
    bcs = _LatestSessionBcs("sess-latest")
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=None, context=None, sink=None, poller=None)
    sid = _run(exe.get_group_session("grp-1"))
    assert sid == "sess-latest"
    assert not bcs.create_called


def test_form_coop_group_subscribes_bcn_event_callback_with_api_base_url():
    """form_coop_group 用 api_base_url + task 回调路径订阅 BCN 事件 webhook(event_subscriptions.sink.url)。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["drv"], collab_mode="state_machine",
        members_info=[{"bot_id": "drv", "role": "manager"}],
        extend_props={"collaboration_definition_yaml": "kind: collab",
                      "api_base_url": "https://cb.example.com/"},
    )))
    subs = bcs.created_req.event_subscriptions
    assert subs and len(subs) == 1
    sub = subs[0]
    assert sub["name"] == "group-webhook"
    assert sub["sink"]["type"] == "webhook"
    # api_base_url 去尾斜杠 + 回调路径
    assert sub["sink"]["url"] == "https://cb.example.com" + _BCN_EVENT_CALLBACK_PATH
    assert sub["sink"]["url"].endswith("/api/v1/collaboration/tasks/callback/report")
    assert sub["payload"] == {"mode": "metadata_only"}
    assert "state_machine.*" in sub["event_filters"]


def test_form_coop_group_opening_message_params_is_object():
    """state_machine 群带 task_id 时,opening_message.params 必须是 JSON object,不能字符串化。

    BCS 契约(ocb-public/src/bcs/docs/custom-collaboration-opening-message-integration-guide.md §4):
    params 是传给业务组件的 JSON object。字符串化会被真实 BCS 的 untagged enum ``OpeningMessage``
    422("data did not match any variant of untagged enum OpeningMessage");singlebox double 不校验
    opening_message,故此断言守住真实 BCS 契约、防 params 被错字符串化回退。
    """
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["drv"], collab_mode="state_machine",
        members_info=[{"bot_id": "drv", "role": "manager"}],
        extend_props={"collaboration_definition_yaml": "kind: collab",
                      "task_id": "sm_task_1",
                      "api_base_url": "https://cb.example.com/"},
    )))
    om = bcs.created_req.opening_message
    assert om is not None, "state_machine + task_id 应构造 opening_message"
    assert om["type"] == "panel"
    assert om["component"] == "partnerPanel.CollaborationRunView"
    # 契约核心:params 必须是 JSON object(dict),不是字符串(否则真实 BCS 422)
    assert isinstance(om["params"], dict), f"opening_message.params 必须是 object,实际 {type(om['params'])!r}"
    assert om["params"]["taskId"] == "sm_task_1"
    assert om["params"]["apiBaseUrl"] == "https://cb.example.com/"
    assert om["params"]["groupId"] == "{{bcs.group_id}}"
    assert om["params"]["runId"] == "{{bcs.run_id}}"
    assert om["params"]["businessScene"] == "release_review"


def test_form_coop_group_sets_group_context_from_task_context():
    """form_coop_group 把 extend_props['task_context'] 设进 BCS 建群的 context(→ <GroupContext> `目标`)。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["drv"], collab_mode="manager_worker",
        members_info=[{"bot_id": "drv", "role": "manager"}],
        extend_props={"task_context": "写一篇关于远程办公协作工具趋势的短文"},
    )))
    assert "写一篇关于远程办公协作工具趋势的短文" in bcs.created_req.context
    assert "reporter_bot_id=drv" in bcs.created_req.context
    assert "execution_mode=coop_group" in bcs.created_req.context
