"""Shared fixtures and request helpers for the task commission end-to-end case."""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    OpenOpportunity,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenApplicationCommand,
    OpenMethodOpsServiceProtocol,
    ProfileOpAcceptedResult,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    role_binding_refs,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
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
from tests.community.factories.task_commission import commission_report_request


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


ProgressReporter = Callable[[int, str, str, str, str], None]


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
    collet_id: str,
    task_id: str,
    service: TaskCommissionServiceProtocol,
    report_progress: ProgressReporter,
) -> list[str]:
    """执行本地 AI coding 模拟,并通过回调逐步上报 L4 Work 进度。

    这是测试夹具的业务动作,不是 Bot 的内置方法;Bot runtime 只负责
    调用该函数、发送 progress/submit 入站消息。PR 仍是本地提案文件,
    不冒充真实远端建 PR 或 LLM 执行。
    """
    plan, code = tmp_path / "plan.md", tmp_path / "calculator.py"
    test_result, pr = tmp_path / "test-result.txt", tmp_path / "pull-request.md"
    artifacts = [path.as_uri() for path in (plan, code, test_result, pr)]

    assert service.detail_task(collet_id).status == "in_progress"
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
        progress = f"已完成 {index + 1}/{len(_AI_CODING_STEPS)} 个步骤：{note}"
        reason = f"产物已生成：{artifacts[index]}"
        report_progress(percent, note, progress, "", reason)
        live = service.detail_task(collet_id)
        assert live.status == "in_progress"  # 100% is not accept or submit.
        assert live.events[-1]["payload"] == {
            "note": note,
            "percent": percent,
            "progress": progress,
            "error_msg": "",
            "reason": reason,
            "task_id": task_id,
        }
        assert live.events[-1]["actor_id"] == BOT_ASSIGNEE
        assert all(
            event["event_kind"] != "task.submitted" for event in live.events
        )

    assert test_result.read_text(encoding="utf-8") == "3 passed"
    assert all(path.exists() for path in (plan, code, test_result, pr))
    return artifacts


class SimulatedWorkingBot:
    """测试用 Bot runtime:自己查询机会并申请参与,执行中上报,成功后才提交成果。"""

    def __init__(
        self,
        client: TestClient,
        signed_principal: str,
        profile: ProductProfileDefinition,
        bot_id: str,
        owner_id: str,
        *,
        open_method: OpenMethodOpsServiceProtocol,
    ):
        self.bot_uuid = bot_id  # Exact UUID used in the signed gateway principal.
        self.bot_id, self.owner_id = self.bot_uuid, owner_id
        self.profile = profile
        self.profile_ref = profile.ref
        self.client = client
        self.headers = {"X-Avernet-Principal": signed_principal}
        self._open_method = open_method
        # 本地直接调用的可信身份夹具;不冒充 HTTP/JWT 认证。
        self.actor_context = ActorContext(
            actor=PrincipalRef("bot", self.bot_uuid),
            source="a2a",
            accountable_principal_ref=PrincipalRef("human", self.owner_id),
        )

    def query_opportunities_by_skill(self) -> tuple[OpenOpportunity, ...]:
        """由模拟 Bot 以自身身份调用注入的 L5 Open 能力查询机会。"""
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 以自身身份调用 L5 Open.query_opportunities")
        return self._open_method.query_opportunities(
            actor_context=self.actor_context,
        )

    def submit_application_by_skill(
        self,
        *,
        opportunity: OpenOpportunity,
        capability_evidence_refs: list[str],
        requested_role: str,
    ) -> ProfileOpAcceptedResult:
        """按 Product Profile 的角色名申请,向 L5 传递对应的 L4 角色绑定引用。"""
        declared_role_refs = {
            role.name: ref
            for role, ref in zip(
                self.profile.scenario_roles,
                role_binding_refs(opportunity.scope_ref, profile=self.profile),
                strict=True,
            )
        }
        if requested_role not in declared_role_refs:
            raise ContextCollabValidationError(
                f"product profile does not declare role {requested_role!r}"
            )
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 以自身身份调用 L5 Open.submit_application")
        return self._open_method.submit_application(
            command=OpenApplicationCommand(
                opportunity_ref=opportunity.opportunity_id,
                actor_context=self.actor_context,
                applicant_agent_ref=self.bot_uuid,
                capability_evidence_refs=capability_evidence_refs,
                requested_role_refs=[declared_role_refs[requested_role]],
                reason="task_commission.bot_self_application",
            ),
        )

    def request(self, request: dict, *, expected_status="SUCCEEDED") -> dict:
        message = request["params"]["message"]
        operation = message["parts"][0]["data"]
        request["id"] = str(uuid4())
        message["messageId"] = str(uuid4())
        operation["operation_id"] = str(uuid4())
        operation["context"]["request_id"] = str(uuid4())
        operation["context"]["product_profile_ref"]["version"] = str(
            self.profile_ref.version
        )
        response = self.client.post(
            "/a2a/task-commission", json=request, headers=self.headers
        )
        assert response.status_code == 200, response.text
        parts = response.json()["result"]["parts"]
        assert parts[0]["data"]["status"] == expected_status, parts
        return {
            "result": parts[0]["data"],
            "data": parts[1]["data"] if len(parts) > 1 else {},
        }

    def report_request(
        self, created, action="start", revision=None, payload=None
    ) -> dict:
        request = commission_report_request(
            created,
            action,
            created.context_version if revision is None else revision,
            payload,
        )
        ctx = request["params"]["message"]["parts"][0]["data"]["context"]
        ctx["caller"]["identity_id"] = self.bot_id
        ctx["accountable_principal_ref"]["identity_id"] = self.owner_id
        return request

    def application_request(self, opportunity: dict) -> dict:
        context_id = opportunity["collab_context_id"]
        request = self.report_request(
            TaskCommissionCreatedView(context_id, "", "", "", 0)
        )
        op = request["params"]["message"]["parts"][0]["data"]
        op.update(
            layer="L5",
            operation_family="open",
            action="apply",
            mode="COMMAND",
            expected_revision=None,
            target_ref={
                "object_type": "OpenOpportunity",
                "object_id": opportunity["opportunity_id"],
                "version": None,
                "issuer": "avernet",
            },
            payload={},
        )
        op["context"]["method_binding_ref"]["object_id"] = opportunity[
            "method_binding_ref"
        ]
        return request

    def run(
        self,
        created: TaskCommissionCreatedView,
        execute: Callable[[ProgressReporter], list[str]],
        *,
        task_id: str,
        initial_revision: int,
    ) -> list[str]:
        """执行已由 L4 Work 启动的任务;Bot 不拥有 start_task 方法。"""
        revision = initial_revision

        def send(action: str, payload: dict) -> None:
            nonlocal revision
            request = self.report_request(created, action, revision, payload)
            _flow_log(
                _TEST_DIRECTORY,
                f"模拟 Bot → POST /a2a/task-commission: action={action}, "
                f"task={task_id}, collet={created.collet_id}, expected_revision={revision}",
            )
            result = self.request(request)["result"]
            revision = int(result["result_refs"][0]["version"])
            _flow_log(
                _TEST_DIRECTORY, f"模拟 Bot 收到 {action} 成功回执, revision={revision}"
            )

        def report_progress(
            percent: int, note: str, progress: str, error_msg: str, reason: str
        ) -> None:
            send("progress", {
                "percent": percent,
                "note": note,
                "progress": progress,
                "error_msg": error_msg,
                "reason": reason,
                "task_id": task_id,
            })

        # 调用方已直接通过 L4 Work.start 启动任务。
        # Bot 只上报实际执行过程,不能把自身的运行器当成 L4 Work API。
        # 不是 finally:执行或进度上报失败时不能伪造成功并自动提交。
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 开始执行任务业务函数")
        artifacts = execute(report_progress)
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 业务函数成功返回产物,自动触发 submit")
        send(
            "submit",
            {"note": "执行完成,提交方案、代码、测试结果与 PR", "artifacts": artifacts},
        )
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
