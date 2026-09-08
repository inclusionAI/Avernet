"""Tests for register_agents() dispatch: plain specs → LLMBackedAgent, avernet specs → AvernetAgent."""

from __future__ import annotations

import pytest

from agentcompute.community._plugin_registry import get_registry
from agentcompute.community.bootstrap import Config, set_config
from agentcompute.community.plugins._register import register_agents
from agentcompute.community.plugins.agents import LLMBackedAgent
from agentcompute.community.spi import AgentSpec


@pytest.fixture(autouse=True)
def _reset_config() -> None:
    set_config(Config())


def test_mixed_roster_dispatches_correct_classes() -> None:
    plain = AgentSpec(name="plain-dispatch", role="does thing")
    avernet = AgentSpec(
        name="avernet-dispatch",
        role="does avernet thing",
        metadata={"type": "avernet"},
    )
    register_agents([plain, avernet])

    plain_instance = get_registry().get("agent", "plain-dispatch").factory()
    assert isinstance(plain_instance, LLMBackedAgent)

    with pytest.raises(ValueError) as exc_info:
        get_registry().get("agent", "avernet-dispatch").factory()
    assert "gateway_base_url" in str(exc_info.value)


def test_plain_roster_yields_only_llm_agents() -> None:
    spec1 = AgentSpec(name="plain-a", role="role-a")
    spec2 = AgentSpec(name="plain-b", role="role-b")
    register_agents([spec1, spec2])

    inst1 = get_registry().get("agent", "plain-a").factory()
    inst2 = get_registry().get("agent", "plain-b").factory()
    assert isinstance(inst1, LLMBackedAgent)
    assert isinstance(inst2, LLMBackedAgent)


def test_avernet_spec_without_config_raises_valueerror() -> None:
    avernet = AgentSpec(
        name="avernet-only",
        role="role",
        metadata={"type": "avernet"},
    )
    register_agents([avernet])

    with pytest.raises(ValueError) as exc_info:
        get_registry().get("agent", "avernet-only").factory()
    assert "gateway_base_url" in str(exc_info.value)
