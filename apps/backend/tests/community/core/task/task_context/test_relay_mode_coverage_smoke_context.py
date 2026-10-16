"""Temporary pass-through coverage for the relay mode-coverage smoke flag."""

from agentclaw.community.core.task.domain.models import TaskSourceType, TaskType
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance,
    RequestContext,
    RequestGoal,
    RequestTaskSpec,
    TaskInfoRequest,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def test_execute_request_smoke_state_reaches_node_context():
    smoke_state = {
        "version": 3,
        "current_phase": "plan_single_bot",
        "planned_modes": [],
    }
    task_id = "relay-smoke-state"
    request = TaskInfoRequest(
        task_spec=RequestTaskSpec(
            context=RequestContext(
                title="存储行业尽调",
                background="AI 基础设施驱动的存储行业变化",
                extend_props={
                    "deliverables": ["尽调报告"],
                    "constraints": ["全球市场"],
                    "resources": [],
                    "relay_mode_coverage_smoke": smoke_state,
                },
            ),
            goal=RequestGoal(
                objective="产出存储行业尽调报告",
                acceptances=[
                    RequestAcceptance(id="ac1", acceptance="形成5条核心投资判断"),
                ],
            ),
        ),
        source_type=TaskSourceType.BOT,
        owner_user_id="u1",
        owner_bot_id="task-assistant",
        execution_config={"task_type": TaskType.DYNAMIC},
    )
    service = TaskGraphService()
    service.initialize_graph(request.to_task_info(task_id))

    context = service.get_node_context(task_id, task_id)

    assert context.root_task_spec.context.extend_props[
        "relay_mode_coverage_smoke"
    ] == smoke_state
    assert context.current_task_spec.context.extend_props[
        "relay_mode_coverage_smoke"
    ] == smoke_state
