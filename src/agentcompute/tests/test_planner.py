from agentcompute.community import register_plugin_option
from agentcompute.community.bootstrap import Config, set_config
from agentcompute.community.core import PlanError, Planner
from agentcompute.community.plugins import register_agents, register_plugins
from agentcompute.community.spi import AgentSpec


class _BadProvider:
    def complete(self, prompt, **kwargs):
        return "not json at all"


def _setup(provider="stub"):
    register_plugins()
    register_plugin_option("llm_provider", "bad", _BadProvider)
    set_config(Config(llm_provider=provider, options={"llm": {}}))
    register_agents([AgentSpec(name="searcher", role="finds")])


def test_planner_returns_valid_dag_with_stub_provider():
    _setup()
    plan = Planner().plan("goal", [AgentSpec(name="searcher", role="finds")])
    plan.validate()
    assert "1" in plan.nodes


def test_planner_raises_after_max_retries():
    _setup(provider="bad")
    try:
        Planner().plan("goal", [AgentSpec(name="searcher", role="finds")])
    except PlanError as exc:
        assert "valid JSON DAG" in str(exc)
    else:
        raise AssertionError("expected PlanError")


def test_planner_retries_then_succeeds():
    attempts = {"n": 0}

    class _FlakyProvider:
        def complete(self, prompt, **kwargs):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return "nope"
            return '{"nodes": {"n1": {"agent": "searcher", "input": {"goal": "g"}}}, "edges": []}'

    register_plugin_option("llm_provider", "flaky", _FlakyProvider)
    _setup(provider="flaky")
    plan = Planner().plan("goal", [AgentSpec(name="searcher", role="finds")])
    assert attempts["n"] == 3
    assert plan.nodes["n1"].agent == "searcher"
