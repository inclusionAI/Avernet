import asyncio

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import OpenApiAuthError
from agentclaw.community.core.task.task_runner.integration.prompt_formatter import PromptFormatterImpl
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor


def _node(assignee="bot9:ent1"):
    return TaskNode(node_id="c1", task_id="t1", status=Status.RUNNING,
                    task_spec=TaskSpec(Metadata("t1", "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode="single_bot", assignee=assignee),
                    node_run_graph=None)  # type: ignore[arg-type]


class _Bot:
    def __init__(self, run_id="mid_1", grant_fail=False):
        self._rid = run_id; self._gf = grant_fail; self.sent = []
    async def ensure_grant(self, bot_id):
        if self._gf: raise OpenApiAuthError("403")
    async def send_message(self, *, bot_id, message, metadata):
        self.sent.append((bot_id, message, metadata)); return self._rid


class _Poller:
    def __init__(self): self.registered = []
    def register(self, h): self.registered.append(h)
    def pending(self): return len(self.registered)


class _Ctx:
    def build(self, task_id, node_id): return {"mode": "execute", "node_spec": None}


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_dispatch_single_bot_registers_handle():
    bot = _Bot(); poller = _Poller()
    fmt = PromptFormatterImpl()
    exe = TaskExecutor(bot=bot, bcs=None, formatter=fmt, context=_Ctx(), sink=None, poller=poller)
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    assert bot.sent[0][0] == "bot9:ent1"
    assert poller.registered[0].run_id == "mid_1"
    assert poller.registered[0].loop_task_id == "t1::c1"


def test_dispatch_single_bot_grant_fail_returns_false():
    bot = _Bot(grant_fail=True); poller = _Poller()
    exe = TaskExecutor(bot=bot, bcs=None, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=poller)
    ok = _run(exe.dispatch([_node()]))
    assert ok == [False]
    assert poller.registered == []


def test_prompt_formatter_uses_context_and_node_spec():
    fmt = PromptFormatterImpl()
    n = _node()
    s = fmt.format_execute({"mode": "execute", "node_instruction": "分析行业"}, n)
    assert "分析行业" in s
