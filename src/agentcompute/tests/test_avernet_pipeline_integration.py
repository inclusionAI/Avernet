from __future__ import annotations

import json

from _avernet_fake_gateway import FakeAvernetGateway

from agentcompute.community._plugin_registry import register_plugin_option
from agentcompute.community.bootstrap import Config, set_config
from agentcompute.community.core import Driver, Planner
from agentcompute.community.plugins import register_agents, register_plugins
from agentcompute.community.plugins.llm._stub import StubLLMProvider
from agentcompute.community.spi import AgentSpec


class _SingleNodePlannerStub(StubLLMProvider):
    def complete(self, prompt: str, **kwargs: object) -> str:
        if kwargs.get("kind") == "planner":
            return json.dumps(
                {
                    "nodes": {
                        "1": {
                            "agent": "searcher",
                            "input": {"goal": "integration test goal"},
                        }
                    },
                    "edges": [],
                }
            )
        return super().complete(prompt, **kwargs)


def test_avernet_agent_full_pipeline_against_fake_gateway():
    fake = FakeAvernetGateway()
    try:
        url = fake.start()
        register_plugins()
        register_plugin_option(
            "llm_provider",
            "single-node-stub",
            lambda: _SingleNodePlannerStub(),
        )
        set_config(
            Config(
                llm_provider="single-node-stub",
                options={
                    "llm": {},
                    "avernet": {
                        "gateway_base_url": url,
                        "principal_token": "test-token",
                        "user_id": "test-user",
                        "manifest_template": "name: {role}\nrole: {role}\ngoal: {goal}",
                        "poll_timeout": 5,
                        "poll_interval": 0.01,
                    },
                },
            )
        )
        specs = [
            AgentSpec(
                name="searcher",
                role="finds things",
                metadata={"type": "avernet"},
            )
        ]
        register_agents(specs)

        plan = Planner().plan("integration test goal", specs)
        result = Driver().run(plan)

        assert result.succeeded is True
        assert result.log.statuses["1"].value == "SUCCEEDED"
        output = result.final_output
        assert isinstance(output, str)
        assert "Integration" in output
        assert "test passed" in output

        with fake.lock:
            recorded = list(fake.recorded_requests)
        assert len(recorded) == 4
        methods = [r["method"] for r in recorded]
        assert methods == ["POST", "GET", "POST", "DELETE"]
        for r in recorded:
            auth = r["headers"].get("authorization", "")
            assert "Bearer test-token" in auth, f"missing auth on {r['method']} {r['path']}"
            assert "user_id=test-user" in r["path"], f"missing user_id on {r['method']}"
        assert any(r["method"] == "DELETE" for r in recorded), "bot was not cleaned up"
    finally:
        fake.stop()
