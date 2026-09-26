import asyncio

import httpx

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsCreateGroupResult, BcsHttpAdapter,
)
from agentclaw.community.core.task.task_runner.client.prompt_formatter import PromptFormatterImpl
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import TaskExecutor
from agentclaw.community.core.task.task_runner.client.double.double_bcs_bot_identity_resolver import (
    _DoubleBcsBotIdentityResolver,
)


class _Tok:
    token = "drv"
    secret = "s3c"
    base_url = "http://bcs"


def _adapter(handler):
    return BcsHttpAdapter(_Tok(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                                base_url="http://bcs"))



class _TaskSettingsOff:
    def is_enabled(self, setting_type):
        return False


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
                       poller=poller, identity_resolver=_DoubleBcsBotIdentityResolver(),
                       task_settings=_TaskSettingsOff())
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


def test_form_coop_group_does_not_attach_event_subscriptions():
    """BCN event_subscriptions 仅 manager_worker/state_machine 模态(and _sink_base)内联挂;chat 群不挂,
    终结态收敛交 result poller。本测验 chat 群即便带 api_base_url 也不挂订阅。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["drv"], collab_mode="chat",
        members_info=[{"bot_id": "drv", "role": "manager"}],
        extend_props={"api_base_url": "https://cb.example.com/"},
    )))
    assert not bcs.created_req.event_subscriptions


def test_form_coop_group_compares_referenced_bots_by_pure_bot_id():
    """owner 切分补全丢 ``:owner`` 后 ``bot_ids[0]`` 为纯 bot_id,而 ``participant_bindings`` 透传全 ``bot:owner`` 串;
    校验须按纯 bot_id 归一化比对,否则 owner 同时被 ``bot_ids``(纯)与 bindings(全串)引用时被假性判"不在
    GroupFormation.bot_ids"(回归:预发 e2e owner=20260825_bohtfhe6:35983 兼 binding writer,被报
    ``group bindings reference bots outside GroupFormation.bot_ids: [..bohtfhe6:35983]``)。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["b1", "e1:35983"], collab_mode="state_machine",
        extend_props={"collaboration_definition_yaml": "kind: collab",
                      "participant_bindings": {"writer": ["b1:35983"], "editor": ["e1:35983"]}},
    )))
    req = bcs.created_req
    assert req is not None
    assert set(req.participant_bindings) == {"writer", "editor"}
    # double resolver 对每个 id 拼 :double-owner(全串 b1:35983 → b1:35983:double-owner),证明流程确实到 resolve
    assert req.participant_bindings["writer"]["bot_ids"] == ["b1:35983:double-owner"]
    assert req.participant_bindings["editor"]["bot_ids"] == ["e1:35983:double-owner"]


def test_form_coop_group_adds_binding_targets_to_participants():
    """Every participant_binding target must also be listed in participants."""
    bcs = _Bcs()
    exe = TaskExecutor(
        bot=None,
        bcs=bcs,
        formatter=PromptFormatterImpl(),
        context=_Ctx(),
        sink=None,
        poller=_Poller(),
        identity_resolver=_DoubleBcsBotIdentityResolver(),
    )

    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["default:146836", "default:153364"],
        collab_mode="state_machine",
        extend_props={
            "collaboration_definition_yaml": "kind: collab",
            "participant_bindings": {
                "writer": ["default:153364"],
                "editor": ["default:146836"],
            },
        },
    )))

    req = bcs.created_req
    assert req is not None
    participant_ids = {p["bot_uuid"] for p in req.participants}
    assert participant_ids == {
        "default:146836:double-owner",
        "default:153364:double-owner",
    }
    assert req.participant_bindings["editor"]["bot_ids"] == ["default:146836:double-owner"]
    assert req.participant_bindings["writer"]["bot_ids"] == ["default:153364:double-owner"]


def test_form_coop_group_manager_worker_attaches_event_subscriptions():
    """manager_worker 群内联挂 §4 event_subscriptions(BCS 主动推回 /callback/report,激活既有
    apply_manager_worker_event → execution_graph audit 快照 + converge_by_session)。鉴权用既有
    caller_bot_token(Bearer)+ HMAC,无 cookie(见 spec §4.3)。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver(),
                       api_base_url="https://api.example.com")
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["mgr", "worker"],
        collab_mode="manager_worker",
        members_info=[{"bot_id": "mgr", "role": "manager"}, {"bot_id": "worker", "role": "worker"}],
    )))
    subs = bcs.created_req.event_subscriptions
    assert subs and len(subs) == 1
    s = subs[0]
    assert s["name"] == "avernet-manager-worker"
    assert s["payload"] == {"mode": "full"}
    assert set(s["event_filters"]) == {
        "session.created",
        "task.assigned", "task.completed", "session.completed",   # §4(group.created 不再订阅)
    }
    assert s["sink"]["type"] == "webhook"
    assert s["sink"]["url"] == "https://api.example.com/api/v1/collaboration/tasks/callback/report"
    assert s["sink"]["request_timeout_ms"] == 10000
    # manager/worker 参与者照常
    assert sorted(p["role"] for p in bcs.created_req.participants) == ["manager", "worker"]


def test_form_coop_group_manager_worker_without_api_base_url_skips_subscriptions():
    """_api_base_url 未配(sink.url 不能空/相对)→ 跳过 event_subscriptions + warn;manager_worker 群照常建,poller 兜底。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())  # api_base_url 缺省 ""
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["mgr", "worker"], collab_mode="manager_worker",
        members_info=[{"bot_id": "mgr", "role": "manager"}, {"bot_id": "worker", "role": "worker"}],
    )))
    assert not bcs.created_req.event_subscriptions


def test_form_coop_group_chat_does_not_attach_event_subscriptions():
    """chat 群不挂订阅(本次不给 chat 事件流);既有 state_machine 否定测试 + chat 不挂,确保只有 manager_worker 挂。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver(),
                       api_base_url="https://api.example.com")
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["b1", "b2"], collab_mode="chat", members_info=[],
    )))
    assert not bcs.created_req.event_subscriptions


def test_form_coop_group_passes_caller_bot_token_from_provider():
    """注入 BcsBotTokenProvider 时,form_coop_group 取 driver_bot(BCS uuid)的 token 填 caller_bot_token(参考 ocb Bearer)。"""
    captured = {}

    class _TokProvider:
        def get_token(self, bcs_bot_uuid):
            captured["queried"] = bcs_bot_uuid
            return f"tok-for-{bcs_bot_uuid}"

    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver(),
                       bot_token_provider=_TokProvider())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["drv"], collab_mode="state_machine",
        members_info=[{"bot_id": "drv", "role": "manager"}],
        extend_props={"collaboration_definition_yaml": "kind: collab"},
    )))
    # driver_bot 经 _DoubleBcsBotIdentityResolver 解析为 BCS uuid `drv:double-owner`
    assert captured["queried"] == "drv:double-owner"
    assert bcs.created_req.caller_bot_token == "tok-for-drv:double-owner"


def test_form_coop_group_without_provider_omits_caller_bot_token():
    """未注入 provider(默认)→ caller_bot_token=None(去订阅后 HMAC 匿名建群亦成,不依赖 token)。"""
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None,
                       poller=_Poller(), identity_resolver=_DoubleBcsBotIdentityResolver())
    _run(exe.form_coop_group(GroupFormation(
        bot_ids=["drv"], collab_mode="state_machine",
        members_info=[{"bot_id": "drv", "role": "manager"}],
        extend_props={"collaboration_definition_yaml": "kind: collab"},
    )))
    assert bcs.created_req.caller_bot_token is None


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
                      "api_base_url": "https://cb.example.com/",
                      "panel_component_name": "customPanel.CustomRunView"},
    )))
    om = bcs.created_req.opening_message
    assert om is not None, "state_machine + task_id 应构造 opening_message"
    assert om["type"] == "panel"
    assert om["component"] == "customPanel.CustomRunView"
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
