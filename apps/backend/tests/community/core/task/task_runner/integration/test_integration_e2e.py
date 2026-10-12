import asyncio
import logging
from unittest.mock import MagicMock

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
    TaskExecutor,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _executor() -> TaskExecutor:
    """Build the production executor with in-memory test doubles.

    No transport operation happens in this case: the BBS path must fail closed
    before any transport call when its graph dependency is absent.
    """
    return TaskExecutor(
        bot=MagicMock(),
        bcs=MagicMock(),
        formatter=MagicMock(),
        context=MagicMock(),
        sink=MagicMock(),
        poller=MagicMock(),
        graph=None,
    )


def test_bbs_dispatch_without_graph_fails_without_changing_node_status(caplog):
    exe = _executor()
    node = TaskNode(
        node_id="b1",
        task_id="t1",
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context("bg", title="T"),
            goal=Goal("O", [AcceptanceCriteria("a1", "d")]),
        ),
        run_info=RuntimeInfo(run_mode="bbs", assignee="bbs_bot"),
        node_run_graph=None,  # type: ignore[arg-type]
    )
    with caplog.at_level(logging.INFO):
        assert _run(exe.dispatch([node])) == [False]
    assert node.status == Status.RUNNING
    assert "dispatch failed: graph missing" in caplog.text
