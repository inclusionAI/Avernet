import pytest

from agentcompute.community.bootstrap import Config, get_container, set_config
from agentcompute.community.core import Driver, Planner
from agentcompute.community.plugins import register_agents, register_plugins
from agentcompute.community.spi import AgentSpec


@pytest.fixture(autouse=True)
def _registered():
    register_plugins()
    set_config(Config(llm_provider="stub", options={"llm": {}}))
    register_agents(
        [
            AgentSpec(name="searcher", role="Searches sources"),
            AgentSpec(name="summarizer", role="Summarizes findings"),
        ]
    )


def test_stub_provider_is_deterministic_and_offline():
    provider = get_container().plugins().llm_provider()
    assert provider.complete("hello") == provider.complete("hello")
    assert provider.complete("hello").startswith("[agent]")


def test_planner_produces_valid_dag():
    specs = [
        AgentSpec(name="searcher", role="Searches sources"),
        AgentSpec(name="summarizer", role="Summarizes findings"),
    ]
    plan = Planner().plan("research X", specs)
    plan.validate()
    assert len(plan.nodes) == 2
    assert plan.edges == [("1", "2")]


def test_driver_executes_and_tracks_status():
    specs = [
        AgentSpec(name="searcher", role="Searches sources"),
        AgentSpec(name="summarizer", role="Summarizes findings"),
    ]
    plan = Planner().plan("research X", specs)
    result = Driver().run(plan)
    assert result.succeeded
    assert result.log.statuses["1"].value == "SUCCEEDED"
    assert result.log.statuses["2"].value == "SUCCEEDED"
    assert result.final_output is not None


def test_registry_does_not_hardcode_agents():
    from agentcompute.community import get_registry

    names = get_registry().names("agent")
    assert "searcher" in names
    assert "summarizer" in names
