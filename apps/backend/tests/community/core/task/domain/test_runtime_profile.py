from agentclaw.community.core.task.domain.models import TaskRuntimeProfile


def test_relay_runtime_profile_does_not_select_central_planner() -> None:
    relay = TaskRuntimeProfile.from_execution_config(
        {"orchestration_mode": "relay", "runtime_profile": {"dispatcher_strategy": "relay_handoff"}}
    )
    assert relay.planner_strategy is None
    assert "planner_strategy" not in relay.to_dict()
    assert relay.to_dict()["dispatcher_strategy"] == "relay_handoff"
    assert relay.to_dict()["recovery_strategy"] == "relay"

    relay_default = TaskRuntimeProfile.from_execution_config({"orchestration_mode": "relay"})
    assert relay_default.to_dict()["runner_strategy"] == "relay_prompt"

    central = TaskRuntimeProfile.from_execution_config({"orchestration_mode": "centralized"})
    assert central.to_dict()["planner_strategy"] == "default"
    assert central.to_dict()["recovery_strategy"] == "centralized"
