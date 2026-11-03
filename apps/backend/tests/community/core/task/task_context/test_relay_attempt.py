"""core fence 单函数 verify_relay_attempt 的契约(spec §14.5:fence 下沉 core)。

只钉领域语义三件:匹配放行 / 串改或空值拒绝 / 图上无 token 拒绝。
节点构造只填校验触碰的字段(extend_props),与 task_center 测试的
SimpleNamespace run_info 习惯一致。
"""
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import Status, TaskNode
from agentclaw.community.core.task.task_context.relay_attempt import (
    verify_relay_attempt,
)


def _node(token: str | None) -> TaskNode:
    return TaskNode(
        node_id="node-1",
        task_id="task-1",
        status=Status.RUNNING,
        task_spec=None,
        run_info=SimpleNamespace(
            run_mode="relay",
            extend_props={"relay_token": token} if token else {},
        ),
        node_run_graph=None,
    )


def test_matching_token_passes_silently():
    verify_relay_attempt(_node("gen-1"), "gen-1")


def test_tampered_token_is_rejected():
    with pytest.raises(TaskStateError, match="attempt has expired"):
        verify_relay_attempt(_node("gen-1"), "gen-2")


def test_empty_or_missing_graph_token_is_rejected():
    with pytest.raises(TaskStateError, match="attempt has expired"):
        verify_relay_attempt(_node("gen-1"), "")

    with pytest.raises(TaskStateError, match="attempt has expired"):
        verify_relay_attempt(_node(None), "gen-1")
