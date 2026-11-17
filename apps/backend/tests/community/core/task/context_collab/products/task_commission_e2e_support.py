"""Shared fixtures and request helpers for the task commission end-to-end case."""

from __future__ import annotations

import logging
import runpy
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.metrics import (
    MetricDefinition,
    MetricSample,
    Outcome,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.service_api import (
    TelemetryServiceProtocol,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.telemetry import (
    Observation,
    TelemetryContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
    ObjectRef,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
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
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.topology import (
    product_execution_binding_ref,
    product_method_binding_ref,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionCreatedView,
    TaskCommissionServiceProtocol,
)
from fastapi.testclient import TestClient

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


TelemetryReporter = Callable[
    [Observation | MetricSample | Outcome, TelemetryContext], None
]
logger = logging.getLogger(__name__)


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
    working_bot: SimulatedWorkingBot,
    report_telemetry: TelemetryReporter,
) -> list[str]:
    """执行本地 AI coding 模拟,通过 G3 记录步骤、指标和结果。

    这是测试夹具的业务动作;Bot runtime 直接调用 G3 服务采集观测,
    成功执行后才发送 submit 入站消息。PR 仍是本地提案文件,
    不冒充真实远端建 PR 或 LLM 执行。
    """
    plan, code = tmp_path / "plan.md", tmp_path / "calculator.py"
    test_result, pr = tmp_path / "test-result.txt", tmp_path / "pull-request.md"
    artifacts = [path.as_uri() for path in (plan, code, test_result, pr)]

    before = service.detail_task(collet_id)
    assert before.status == "in_progress"
    task_ref = ObjectRef("Task", task_id, None, "avernet")
    collet_ref = ObjectRef("Collet", collet_id, None, "avernet")
    trace_id = str(uuid4())
    metric = MetricDefinition(
        metric_id=f"task_commission.execution_percent:{working_bot.bot_uuid}",
        name="Bot execution progress",
        direction="HIGHER_IS_BETTER",
        unit="percent",
        aggregation="LAST",
        scope="Task",
        owner=IdentityRef("AGENT", working_bot.bot_uuid, "avernet"),
        schema_version="1.0",
    )
    metric_ref = ObjectRef("MetricDefinition", metric.metric_id, "1", "avernet")
    working_bot.capture_telemetry(
        lambda: working_bot.telemetry_service.define_metric(
            metric,
            version="1",
            actor_context=working_bot.actor_context,
        )
    )
    evidence_refs = []
    for index, (percent, note) in enumerate(_AI_CODING_STEPS):
        if index == 0:
            plan.write_text(
                "# 方案\n新增 add(a, b),覆盖正数、负数及零。", encoding="utf-8"
            )
        elif index == 1:
            code.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
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
        now = datetime.now(timezone.utc)
        observation = Observation(
            observation_id=str(uuid4()),
            observation_type="EVENT",
            observed_at=now,
            recorded_at=now,
            observer=IdentityRef("AGENT", working_bot.bot_uuid, "avernet"),
            subject_ref=task_ref,
            context=TelemetryContext(
                trace_id=trace_id,
                span_id=str(uuid4()),
                protocol_versions={"ACCS": "0.2"},
                agent_ref=ObjectRef("Agent", working_bot.bot_uuid, None, "avernet"),
                principal_ref=ObjectRef(
                    "Principal", working_bot.owner_id, None, "avernet"
                ),
                collet_ref=collet_ref,
                task_ref=task_ref,
                method_binding_ref=ObjectRef(
                    "MethodBinding",
                    product_execution_binding_ref(collet_id, working_bot.profile),
                    None,
                    "avernet",
                ),
                product_profile_ref=ObjectRef(
                    "ProductProfile",
                    working_bot.profile_ref.profile_id,
                    str(working_bot.profile_ref.version),
                    "avernet",
                ),
            ),
            schema_version="task_commission.execution_step@1",
            extensions={
                "step": index + 1,
                "total_steps": len(_AI_CODING_STEPS),
                "note": note,
                "detail": progress,
                "error_msg": "",
                "reason": reason,
                "artifact_ref": artifacts[index],
            },
        )
        report_telemetry(observation, observation.context)
        evidence_refs.append(
            ObjectRef("Observation", observation.observation_id, None, "avernet")
        )
        sample = MetricSample(
            metric_ref=metric_ref,
            subject_ref=task_ref,
            value=percent,
            measured_at=now,
            window=None,
            evidence_refs=(evidence_refs[-1],),
            dimensions={"step": str(index + 1)},
        )
        report_telemetry(sample, observation.context)
        live = service.detail_task(collet_id)
        assert live == before  # Even 100% changes no L4 state, events or revision.
        observations = working_bot.telemetry_service.query_observations(
            task_ref,
            collet_ref=collet_ref,
            actor_context=working_bot.actor_context,
        )
        assert len(observations) == index + 1
        assert observations[-1].extensions == observation.extensions
        assert observations[-1].observer == observation.observer
        assert observations[-1].context == observation.context
        assert "percent" not in observations[-1].extensions
        samples = working_bot.telemetry_service.query_metric_samples(
            task_ref,
            collet_ref=collet_ref,
            actor_context=working_bot.actor_context,
        )
        assert [s.value for s in samples] == [
            percent for percent, _ in _AI_CODING_STEPS[: index + 1]
        ]
        assert samples[-1] == sample

    outcome = Outcome(
        outcome_id=str(uuid4()),
        subject_ref=task_ref,
        improvement_objective_refs=(),
        status="SUCCEEDED",
        produced_at=datetime.now(timezone.utc),
        result_refs=tuple(ObjectRef("Artifact", uri, None, None) for uri in artifacts),
        evidence_refs=tuple(evidence_refs),
        metric_samples=(sample,),
        evaluator_ref=None,
        protocol_versions={"ACCS": "0.2"},
    )
    report_telemetry(outcome, observation.context)
    assert working_bot.telemetry_service.query_outcomes(
        task_ref,
        collet_ref=collet_ref,
        actor_context=working_bot.actor_context,
    ) == (outcome,)
    # Bot's execution success is not submission or the owner's acceptance.
    assert service.detail_task(collet_id) == before
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
        telemetry_service: TelemetryServiceProtocol,
    ):
        self.bot_uuid = bot_id  # Exact UUID used in the signed gateway principal.
        self.bot_id, self.owner_id = self.bot_uuid, owner_id
        self.profile = profile
        self.profile_ref = profile.ref
        self.client = client
        self.headers = {"X-Avernet-Principal": signed_principal}
        self._open_method = open_method
        self.telemetry_service = telemetry_service
        # 本地直接调用的可信身份夹具;不冒充 HTTP/JWT 认证。
        self.actor_context = ActorContext(
            actor=PrincipalRef("bot", self.bot_uuid),
            source="a2a",
            accountable_principal_ref=PrincipalRef("human", self.owner_id),
        )

    def query_opportunities_by_skill(self) -> tuple[OpenOpportunity, ...]:
        """由模拟 Bot 以自身身份调用注入的 L5 Open 能力查询机会。"""
        _flow_log(
            _TEST_DIRECTORY, "模拟 Bot 以自身身份调用 L5 Open.query_opportunities"
        )
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
                role_binding_refs(
                    opportunity.scope_ref,
                    profile=self.profile,
                    executor_ref=AgentRef("bot", self.bot_uuid),
                ),
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
        ctx["method_binding_ref"]["object_id"] = product_execution_binding_ref(
            created.collet_id, self.profile
        )
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

    @staticmethod
    def capture_telemetry(acquire: Callable[[], object]) -> None:
        try:
            acquire()
        except Exception:
            # No mandatory evidence gate in this Profile. Acquisition failures
            # are visible but must not block execution or claim recorded success.
            logger.warning("G3 telemetry acquisition failed", exc_info=True)

    def run(
        self,
        created: TaskCommissionCreatedView,
        execute: Callable[[TelemetryReporter], list[str]],
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

        def report_telemetry(
            record: Observation | MetricSample | Outcome, context: TelemetryContext
        ) -> None:
            if isinstance(record, Observation):
                self.capture_telemetry(
                    lambda: self.telemetry_service.record_observation(
                        record,
                        actor_context=self.actor_context,
                    )
                )
            elif isinstance(record, MetricSample):
                self.capture_telemetry(
                    lambda: self.telemetry_service.record_metric_sample(
                        record,
                        context=context,
                        actor_context=self.actor_context,
                    )
                )
            else:
                self.capture_telemetry(
                    lambda: self.telemetry_service.record_outcome(
                        record,
                        context=context,
                        actor_context=self.actor_context,
                    )
                )

        # L4 Work.start already ran. Telemetry does not change its revision.
        # Not finally: execution failure must never auto-submit a success.
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 开始执行任务业务函数")
        artifacts = execute(report_telemetry)
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 业务函数成功返回产物,自动触发 submit")
        send(
            "submit",
            {"note": "执行完成,提交方案、代码、测试结果与 PR", "artifacts": artifacts},
        )
        return artifacts


def _participation_snapshot(service, injector, created):
    """SQL observable state and all relevant Method facts, including dispatch."""
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    context_id = created.collet_id
    record = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        context_id
    )
    profile = injector.get(ProductCatalog).get(
        record.product_instance.profile_ref.profile_id,
        record.product_instance.profile_ref.version,
    )
    binding = product_method_binding_ref(context_id, profile, "Open")
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
