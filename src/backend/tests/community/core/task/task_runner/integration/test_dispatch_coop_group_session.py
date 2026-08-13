import asyncio

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import BcsCreateGroupResult
from agentclaw.community.core.task.task_runner.integration.prompt_formatter import PromptFormatterImpl
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor


def _node(group_id="g1", task_id="t1"):
    return TaskNode(node_id="n1", task_id=task_id, status=Status.RUNNING,
                    task_spec=TaskSpec(Metadata(task_id, "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode="coop_group", assignee=group_id),
                    node_run_graph=None)  # type: ignore[arg-type]


class _Bcs:
    def __init__(self): self.created = []; self.sessions = []
    async def create_group(self, req):
        self.created.append(req); return BcsCreateGroupResult(group_id="g1", definition_ref=None)
    async def create_session(self, group_id, *, bootstrap_prompt=None, idempotency_key=None):
        self.sessions.append((group_id, bootstrap_prompt)); return "s1"
    async def get_group(self, group_id): return {"session": {"status": "completed", "output": {"r": 1}}}
    async def get_session_messages(self, sid, *, limit=50, since_msg_id=None): return []


class _Poller:
    def __init__(self): self.registered = []
    def register(self, h): self.registered.append(h)


class _Ctx:
    def build(self, task_id, node_id): return {"mode": "execute"}


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_form_coop_group_chat_stores_meta_and_returns_gid():
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=_Poller())
    gid = _run(exe.form_coop_group(GroupFormation(bot_ids=["drv", "w1"], collab_mode="chat")))
    assert gid == "g1"
    assert exe._group_meta["g1"]["collab_mode"] == "chat"
    assert bcs.created[0].group_strategy is None  # chat 省略


def test_form_coop_group_manager_worker_sets_strategy():
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=_Poller())
    _run(exe.form_coop_group(GroupFormation(bot_ids=["mgr", "w1"], collab_mode="manager_worker",
                                            extend_props={"manager_bot_id": "mgr"})))
    assert bcs.created[0].group_strategy == "manager_worker"


def test_dispatch_coop_group_session_mode_registers_session_handle():
    bcs = _Bcs(); poller = _Poller()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=poller)
    _run(exe.form_coop_group(GroupFormation(bot_ids=["drv"], collab_mode="chat")))
    ok = _run(exe.dispatch([_node(group_id="g1")]))
    assert ok == [True]
    assert bcs.sessions[0][0] == "g1"
    h = poller.registered[0]
    assert h.session_id == "s1" and h.run_id is None and h.collab_mode == "chat"
    assert h.loop_task_id == "t1::n1"
