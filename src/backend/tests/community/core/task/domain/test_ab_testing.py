from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.ab_testing import apply_ab_test
from agentclaw.community.core.task.domain.errors import TaskStateError


def _config(*variants, **ab_overrides):
    ab_test = {
        "experiment_id": "planner-exp",
        "variants": list(variants)
        or [
            {"name": "control", "weight": 1},
            {"name": "treatment", "weight": 1},
        ],
        **ab_overrides,
    }
    return {"task_type": "dynamic", "ab_test": ab_test}


def _apply(config, *, task_id="task-1", user="user-1", bot="bot-1"):
    return apply_ab_test(
        config,
        task_id=task_id,
        owner_user_id=user,
        owner_bot_id=bot,
    )


def test_no_experiment_and_disabled_experiment_preserve_compatibility():
    assert _apply({"task_type": "dynamic", "ab_assignment": {"old": True}}) == {
        "task_type": "dynamic"
    }
    disabled = _config(enabled=False)
    disabled["ab_assignment"] = {"old": True}
    assert "ab_assignment" not in _apply(disabled)


def test_assignment_is_stable_by_selected_unit_and_does_not_expose_unit_value():
    config = _config(unit="owner_user")
    first = _apply(config, task_id="task-a", user="private-user")
    second = _apply(config, task_id="task-b", user="private-user")
    assert first["ab_assignment"] == second["ab_assignment"]
    assert "private-user" not in repr(first["ab_assignment"])
    assert len(first["ab_assignment"]["assignment_key_digest"]) == 64


def test_variant_overrides_are_frozen_on_top_of_base_runtime_profile():
    config = _config(
        {
            "name": "control",
            "weight": 1,
            "runtime_profile": {
                "planner_strategy": "control-plan",
                "allowed_run_modes": ["single_bot", "single_bot", "bbs"],
            },
            "orchestration_mode": "centralized",
        },
        {
            "name": "treatment",
            "weight": 1,
            "runtime_profile": {
                "planner_strategy": "treatment-plan",
                "dispatcher_strategy": "search-v2",
                "search_strategy": "catalog-v2",
                "runner_strategy": "runner-v2",
            },
            "orchestration_mode": "relay",
        },
    )
    config["runtime_profile"] = {"dispatcher_strategy": "base-search"}
    resolved = _apply(config)
    assignment = resolved["ab_assignment"]
    assert assignment["experiment_id"] == "planner-exp"
    assert assignment["total_weight"] == 2
    assert assignment["bucket"] in {0, 1}
    if assignment["variant"] == "control":
        assert resolved["orchestration_mode"] == "centralized"
        assert resolved["runtime_profile"] == {
            "dispatcher_strategy": "base-search",
            "planner_strategy": "control-plan",
            "allowed_run_modes": ["single_bot", "bbs"],
        }
    else:
        assert resolved["orchestration_mode"] == "relay"
        assert resolved["runtime_profile"] == {
            "dispatcher_strategy": "search-v2",
            "planner_strategy": "treatment-plan",
            "search_strategy": "catalog-v2",
            "runner_strategy": "runner-v2",
        }


def test_non_mapping_base_profile_is_replaced_and_owner_bot_unit_is_supported():
    config = _config(
        {"name": "a", "weight": 10, "runtime_profile": {}},
        {"name": "b", "weight": 1},
        unit="owner_bot",
    )
    config["runtime_profile"] = "legacy-invalid"
    resolved = _apply(config, bot="bot-owner")
    if resolved["ab_assignment"]["variant"] == "a":
        assert resolved["runtime_profile"] == {}


def test_weighted_selection_covers_early_and_last_variant():
    config = _config(
        {"name": "first", "weight": 1},
        {"name": "last", "weight": 1},
    )
    selected = {
        _apply(config, task_id=f"task-{index}")["ab_assignment"]["variant"]
        for index in range(20)
    }
    assert selected == {"first", "last"}


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"ab_test": "bad"}, "must be an object"),
        (_config(extra=True), "unsupported fields"),
        (_config(enabled="yes"), "enabled must be a boolean"),
        (_config(experiment_id=" "), "experiment_id must be a non-empty string"),
        (_config(unit="session"), "unit must be task"),
        (_config(unit="owner_user"), "unit 'owner_user' is empty"),
        ({"ab_test": {"experiment_id": "x", "variants": []}}, "at least two"),
        (_config("bad", {"name": "b", "weight": 1}), r"variants\[0\] must be an object"),
        (_config({"name": "a", "weight": 1, "extra": 1}, {"name": "b", "weight": 1}), "unsupported fields"),
        (_config({"name": " ", "weight": 1}, {"name": "b", "weight": 1}), "name must be a non-empty string"),
        (_config({"name": "a", "weight": True}, {"name": "b", "weight": 1}), "positive integer"),
        (_config({"name": "a", "weight": 1.5}, {"name": "b", "weight": 1}), "positive integer"),
        (_config({"name": "a", "weight": 0}, {"name": "b", "weight": 1}), "positive integer"),
        (_config({"name": "same", "weight": 1}, {"name": "same", "weight": 1}), "names must be unique"),
        (_config({"name": "a", "weight": 1, "runtime_profile": "bad"}, {"name": "b", "weight": 1}), "runtime_profile must be an object"),
        (_config({"name": "a", "weight": 1, "runtime_profile": {"unknown": "x"}}, {"name": "b", "weight": 1}), "unsupported fields"),
        (_config({"name": "a", "weight": 1, "runtime_profile": {"planner_strategy": ""}}, {"name": "b", "weight": 1}), "planner_strategy must be a non-empty string"),
        (_config({"name": "a", "weight": 1, "runtime_profile": {"search_strategy": ""}}, {"name": "b", "weight": 1}), "search_strategy must be a non-empty string"),
        (_config({"name": "a", "weight": 1, "runtime_profile": {"allowed_run_modes": []}}, {"name": "b", "weight": 1}), "non-empty list"),
        (_config({"name": "a", "weight": 1, "runtime_profile": {"allowed_run_modes": ["shell"]}}, {"name": "b", "weight": 1}), "only supports"),
        (_config({"name": "a", "weight": 1, "orchestration_mode": "other"}, {"name": "b", "weight": 1}), "centralized or relay"),
    ],
)
def test_invalid_experiment_contracts_fail_clearly(config, message):
    kwargs = {"user": ""} if "unit 'owner_user'" in message else {}
    with pytest.raises(TaskStateError, match=message):
        _apply(config, **kwargs)
