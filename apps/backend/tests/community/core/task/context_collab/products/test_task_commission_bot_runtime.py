"""The simulated Bot discovers opportunities and applies by declared product role."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import create_autospec

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.metrics import (
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
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    OpenOpportunity,
    OpportunityState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenApplicationCommand,
    OpenMethodOpsServiceProtocol,
    ProfileOpAcceptedResult,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    init_product_profile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionCreatedView,
)
from fastapi.testclient import TestClient

from tests.community.core.task.context_collab.products.task_commission_e2e_support import (
    SimulatedWorkingBot,
)


@pytest.fixture
def bot_runtime():
    open_method = create_autospec(OpenMethodOpsServiceProtocol, instance=True)
    bot = SimulatedWorkingBot(
        client=create_autospec(TestClient, instance=True),
        signed_principal="test-principal",
        profile=init_product_profile(),
        bot_id="bot_discoverer:u_bot_owner",
        owner_id="u_bot_owner",
        open_method=open_method,
        telemetry_service=create_autospec(TelemetryServiceProtocol, instance=True),
    )
    return bot, open_method


@pytest.mark.parametrize(
    "opportunities",
    [
        (),
        (
            OpenOpportunity(
                opportunity_id="opp_001",
                method_binding_id="mb_001",
                scope_ref="collet_001",
                participation_type=ParticipationType.COLLET_PARTICIPANT,
                opens_at="2026-11-17T00:00:00+00:00",
                state=OpportunityState.OPEN,
            ),
        ),
    ],
    ids=["empty", "available"],
)
def test_bot_queries_injected_open_service_as_itself(bot_runtime, opportunities):
    working_bot, open_method = bot_runtime
    open_method.query_opportunities.return_value = opportunities

    assert working_bot.query_opportunities_by_skill() is opportunities

    open_method.query_opportunities.assert_called_once_with(
        actor_context=ActorContext(
            actor=PrincipalRef("bot", working_bot.bot_uuid),
            source="a2a",
            accountable_principal_ref=PrincipalRef("human", working_bot.owner_id),
        )
    )
    open_method.submit_application.assert_not_called()
    working_bot.client.post.assert_not_called()


def test_bot_propagates_opportunity_query_failure(bot_runtime):
    working_bot, open_method = bot_runtime
    open_method.query_opportunities.side_effect = ContextCollabValidationError(
        "query denied"
    )

    with pytest.raises(ContextCollabValidationError, match="query denied"):
        working_bot.query_opportunities_by_skill()


@pytest.fixture
def opportunity():
    return OpenOpportunity(
        opportunity_id="opp_001",
        method_binding_id="mb_001",
        scope_ref="collet_001",
        participation_type=ParticipationType.COLLET_PARTICIPANT,
        opens_at="2026-11-17T00:00:00+00:00",
        state=OpportunityState.OPEN,
    )


@pytest.mark.parametrize(
    ("requested_role", "binding_suffix"),
    [
        ("assignee", "selected-agent/participant_executor_ref"),
        ("owner", "responsible_user/participant_principal_ref"),
    ],
)
@pytest.mark.parametrize(
    ("scope_ref", "encoded_scope"),
    [("collet_001", "collet_001"), ("collet/a b", "collet%2Fa%20b")],
)
def test_bot_applies_with_profile_role_binding_in_opportunity_scope(
    bot_runtime, opportunity, requested_role, binding_suffix, scope_ref, encoded_scope
):
    working_bot, open_method = bot_runtime
    opportunity = replace(opportunity, scope_ref=scope_ref)
    accepted = ProfileOpAcceptedResult(
        accepted=True,
        collab_context_id=scope_ref,
        method_binding_id=opportunity.method_binding_id,
        object_kind="application",
        object_id="application_001",
        state="SUBMITTED",
    )
    open_method.submit_application.return_value = accepted

    submitted = working_bot.submit_application_by_skill(
        opportunity=opportunity,
        capability_evidence_refs=["evidence://calculator-coding"],
        requested_role=requested_role,
    )

    assert submitted is accepted
    open_method.submit_application.assert_called_once_with(
        command=OpenApplicationCommand(
            opportunity_ref=opportunity.opportunity_id,
            actor_context=working_bot.actor_context,
            applicant_agent_ref=working_bot.bot_uuid,
            capability_evidence_refs=["evidence://calculator-coding"],
            requested_role_refs=[f"{encoded_scope}/role-bindings/{binding_suffix}"],
            reason="task_commission.bot_self_application",
        )
    )
    open_method.query_opportunities.assert_not_called()
    working_bot.client.post.assert_not_called()


@pytest.mark.parametrize("requested_role", ["unknown", "selected-agent", ""])
def test_bot_rejects_roles_not_declared_in_profile(
    bot_runtime, opportunity, requested_role
):
    working_bot, open_method = bot_runtime

    with pytest.raises(ContextCollabValidationError, match="does not declare role"):
        working_bot.submit_application_by_skill(
            opportunity=opportunity,
            capability_evidence_refs=["evidence://calculator-coding"],
            requested_role=requested_role,
        )

    open_method.submit_application.assert_not_called()
    working_bot.client.post.assert_not_called()


def test_bot_propagates_application_submission_failure(bot_runtime, opportunity):
    working_bot, open_method = bot_runtime
    open_method.submit_application.side_effect = ContextCollabValidationError(
        "application denied"
    )

    with pytest.raises(ContextCollabValidationError, match="application denied"):
        working_bot.submit_application_by_skill(
            opportunity=opportunity,
            capability_evidence_refs=["evidence://calculator-coding"],
            requested_role="assignee",
        )


@pytest.fixture
def execution_reports(bot_runtime):
    bot, _ = bot_runtime
    task_ref = ObjectRef("Task", "task-1", None, "avernet")
    context = TelemetryContext(
        trace_id="trace-1",
        span_id="span-1",
        protocol_versions={"ACCS": "0.2"},
        collet_ref=ObjectRef("Collet", "collet-1", None, "avernet"),
        task_ref=task_ref,
    )
    now = datetime.now(timezone.utc)
    observation = Observation(
        observation_id="observation-1",
        observation_type="EVENT",
        observed_at=now,
        recorded_at=now,
        observer=IdentityRef("AGENT", bot.bot_uuid, "avernet"),
        subject_ref=task_ref,
        context=context,
        schema_version="execution_step@1",
        extensions={"detail": "Tests passed"},
    )
    sample = MetricSample(
        metric_ref=ObjectRef("MetricDefinition", "execution.percent", "1", "avernet"),
        subject_ref=task_ref,
        value=100,
        measured_at=now,
        window=None,
        evidence_refs=(),
        dimensions={},
    )
    outcome = Outcome(
        outcome_id="outcome-1",
        subject_ref=task_ref,
        improvement_objective_refs=(),
        status="SUCCEEDED",
        produced_at=now,
        result_refs=(),
        evidence_refs=(),
        metric_samples=(sample,),
        evaluator_ref=None,
        protocol_versions={"ACCS": "0.2"},
    )
    return context, observation, sample, outcome


@pytest.mark.parametrize("telemetry_unavailable", [False, True])
def test_bot_directly_reports_three_g3_types_then_submits_without_http_progress(
    bot_runtime, execution_reports, caplog, telemetry_unavailable
):
    bot, open_method = bot_runtime
    context, observation, sample, outcome = execution_reports
    telemetry = bot.telemetry_service
    if telemetry_unavailable:
        for method in (
            telemetry.record_observation,
            telemetry.record_metric_sample,
            telemetry.record_outcome,
        ):
            method.side_effect = RuntimeError("G3 storage unavailable")
    response = bot.client.post.return_value
    response.status_code = 200
    response.json.return_value = {
        "result": {
            "parts": [
                {
                    "data": {
                        "status": "SUCCEEDED",
                        "result_refs": [{"version": "5"}],
                    }
                }
            ]
        }
    }
    artifacts = ["file:///tmp/result.md"]

    def execute(report):
        for record in (observation, sample, outcome):
            report(record, context)
        # G3 acquisition never sends an L4 command, including at 100%/SUCCEEDED.
        bot.client.post.assert_not_called()
        return artifacts

    assert (
        bot.run(
            TaskCommissionCreatedView("collet-1", "Task", "", "created", 2),
            execute,
            task_id="task-1",
            initial_revision=4,
        )
        == artifacts
    )

    telemetry.record_observation.assert_called_once_with(
        observation, actor_context=bot.actor_context
    )
    telemetry.record_metric_sample.assert_called_once_with(
        sample, context=context, actor_context=bot.actor_context
    )
    telemetry.record_outcome.assert_called_once_with(
        outcome, context=context, actor_context=bot.actor_context
    )
    bot.client.post.assert_called_once()
    operation = bot.client.post.call_args.kwargs["json"]["params"]["message"]["parts"][
        0
    ]["data"]
    assert operation["action"] == "submit"
    assert operation["expected_revision"] == 4
    assert operation["payload"]["artifacts"] == artifacts
    assert len(
        [r for r in caplog.records if r.message == "G3 telemetry acquisition failed"]
    ) == (3 if telemetry_unavailable else 0)
    open_method.query_opportunities.assert_not_called()
    open_method.submit_application.assert_not_called()


def test_bot_does_not_submit_when_execution_fails(bot_runtime):
    bot, _ = bot_runtime

    def execute(report):
        raise RuntimeError("code execution failed")

    with pytest.raises(RuntimeError, match="code execution failed"):
        bot.run(
            TaskCommissionCreatedView("collet-1", "Task", "", "created", 2),
            execute,
            task_id="task-1",
            initial_revision=4,
        )
    bot.client.post.assert_not_called()
