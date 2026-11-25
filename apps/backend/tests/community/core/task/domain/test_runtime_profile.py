from __future__ import annotations

from types import SimpleNamespace

from agentclaw.community.core.task.domain.models import TaskRuntimeProfile


def test_execution_config_defaults_to_all_successor_modes() -> None:
    profile = TaskRuntimeProfile.from_execution_config(
        {"orchestration_mode": "relay"}
    )

    assert list(profile.allowed_run_modes) == [
        "single_bot",
        "coop_group",
        "bbs",
    ]
    assert profile.to_dict()["allowed_run_modes"] == [
        "single_bot",
        "coop_group",
        "bbs",
    ]


def test_execution_config_preserves_caller_allowlist() -> None:
    profile = TaskRuntimeProfile.from_execution_config(
        {
            "orchestration_mode": "relay",
            "runtime_profile": {
                "allowed_run_modes": ["coop_group", "single_bot", "coop_group"]
            },
        }
    )

    assert list(profile.allowed_run_modes) == ["coop_group", "single_bot"]


def test_graph_reading_defaults_and_deduplicates_frozen_modes() -> None:
    default_graph = SimpleNamespace(
        extend_props={"runtime_profile": {"allowed_run_modes": []}}
    )
    frozen_graph = SimpleNamespace(
        extend_props={
            "runtime_profile": {
                "allowed_run_modes": [" single_bot ", "coop_group", "coop_group"]
            }
        }
    )

    assert TaskRuntimeProfile.run_modes_from_graph(SimpleNamespace(extend_props={})) == (
        "single_bot",
        "coop_group",
        "bbs",
    )
    assert TaskRuntimeProfile.run_modes_from_graph(default_graph) == (
        "single_bot",
        "coop_group",
        "bbs",
    )
    assert TaskRuntimeProfile.run_modes_from_graph(frozen_graph) == (
        "single_bot",
        "coop_group",
    )
