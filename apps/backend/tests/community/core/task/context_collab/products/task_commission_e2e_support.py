"""Shared fixtures and request helpers for the task commission end-to-end case."""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionCreatedView,
    TaskCommissionServiceProtocol,
)
from tests.community.factories.task_commission import (
    BOT_ID as BOT_ASSIGNEE,
)


def opportunities_query_request(bot: Any) -> dict:
    """HTTP 边界测试用 L4 Query 信封;不是 Bot 内置的机会查询能力。"""
    request = bot.report_request(TaskCommissionCreatedView("", "", "", "", 0))
    op = request["params"]["message"]["parts"][0]["data"]
    ctx = op["context"]
    # Product-wide discovery needs no Collet handoff or existing membership.
    ctx["collet_ref"] = ctx["method_binding_ref"] = None
    ctx["product_profile_ref"]["version"] = str(bot.profile_ref.version)
    op.update(
        layer="L4",
        operation_family="query",
        action="opportunities",
        mode="QUERY",
        target_ref=ctx["product_profile_ref"].copy(),
        expected_revision=None,
        payload={},
    )
    return request


_AI_CODING_STEPS = (
    (20, "写方案完成"),
    (50, "编码完成"),
    (80, "测试通过"),
    (100, "生成 PR 提案,等待用户验收"),
)


_TEST_DIRECTORY = __name__.rsplit(".", 1)[0].replace(".", "/")


def _flow_log(directory: str, message: str) -> None:
    print(f"[commission-flow][{directory}] {message}", flush=True)


def _simulate_ai_coding(
    *,
    tmp_path: Path,
    task_id: str,
    service: TaskCommissionServiceProtocol,
    report_progress: Callable[[int, str], None],
) -> list[str]:
    """执行本地 AI coding 模拟,并通过回调逐步上报 L4 Work 进度。

    这是测试夹具的业务动作,不是 Bot 的内置方法;Bot runtime 只负责
    调用该函数、发送 progress/submit 入站消息。PR 仍是本地提案文件,
    不冒充真实远端建 PR 或 LLM 执行。
    """
    plan, code = tmp_path / "plan.md", tmp_path / "calculator.py"
    test_result, pr = tmp_path / "test-result.txt", tmp_path / "pull-request.md"
    artifacts = [path.as_uri() for path in (plan, code, test_result, pr)]

    assert service.detail_task(task_id).status == "in_progress"
    for index, (percent, note) in enumerate(_AI_CODING_STEPS):
        if index == 0:
            plan.write_text(
                "# 方案\n新增 add(a, b),覆盖正数、负数及零。", encoding="utf-8"
            )
        elif index == 1:
            code.write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8"
            )
        elif index == 2:
            add = runpy.run_path(str(code))["add"]
            for a, b, expected in ((1, 2, 3), (-1, 1, 0), (0, 0, 0)):
                assert add(a, b) == expected
            test_result.write_text("3 passed", encoding="utf-8")
        else:
            pr.write_text(
                "# PR 提案\n新增求和能力;方案、代码、3 项测试已完成。",
                encoding="utf-8",
            )

        _flow_log(_TEST_DIRECTORY, f"5. AI coding: {note}, progress={percent}%")
        report_progress(percent, note)
        live = service.detail_task(task_id)
        assert live.status == "in_progress"  # 100% is not accept or submit.
        assert live.events[-1]["payload"] == {"note": note, "percent": percent}
        assert live.events[-1]["actor_id"] == BOT_ASSIGNEE
        assert all(
            event["event_kind"] != "task.submitted" for event in live.events
        )

    assert test_result.read_text(encoding="utf-8") == "3 passed"
    assert all(path.exists() for path in (plan, code, test_result, pr))
    return artifacts


def _participation_snapshot(service, injector, created):
    """SQL observable state and all relevant Method facts, including dispatch."""
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    context_id, binding = created.collet_id, top_level_binding_id(created.collet_id)
    return (
        service.detail_task(context_id),
        store.get_binding(axes, binding, context_id),
        store.get_roster(axes, binding, context_id),
        tuple(store.list_members(axes, binding, context_id)),
        tuple(store.list_participant_bindings(axes, binding, context_id)),
        tuple(store.list_opportunities(axes, binding, context_id)),
        tuple(store.list_applications(axes, binding, context_id)),
        tuple(store.list_decisions(axes, binding, context_id)),
        injector.get(A2aDeliveryPort).bridge_records(),
    )
