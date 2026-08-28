import asyncio

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiAuthError,
)
from agentclaw.community.core.task.task_runner.integration.prompt_formatter import (
    PromptFormatterImpl,
)
from agentclaw.community.core.task.task_runner.integration.task_executor import (
    TaskExecutor,
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
    def __init__(self, run_id="mid_1", session_id=None, grant_fail=False):
        self._rid = run_id
        self._sid = session_id
        self._gf = grant_fail
        self.sent = []

    async def ensure_grant(self, bot_id):
        # 任务认领授权已由前端经 task_grant_service 透传到 secbaas;派发不调 ensure_grant(协议契约保留)。
        if self._gf:
            raise OpenApiAuthError("403")

    async def send_message(self, *, bot_id, message, metadata):
        from agentclaw.community.core.task.task_runner.integration.ports import (
            BotSendResult,
        )

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
        bot=bot, bcs=None, formatter=fmt, context=_Ctx(), sink=None, poller=poller
    )
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    assert bot.sent[0][0] == "bot9:ent1"
    assert poller.registered[0].run_id == "mid_1"
    assert poller.registered[0].loop_task_id == "t1::c1"



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
    assert '"success"' in s
    assert '"gaps"' in s


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
