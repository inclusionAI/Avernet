"""Tests for the DynamicReplanner plugin."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentcompute.community import register_plugin_option
from agentcompute.community.bootstrap import Config, set_config
from agentcompute.community.core import DAGNode, DAGPlan, NodeStatus
from agentcompute.community.plugins import DynamicReplanner, register_agents, register_plugins
from agentcompute.community.spi import AgentSpec, HaltReason


class _ScriptedProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append(prompt)
        if not self._responses:
            pytest.fail("ScriptedProvider ran out of responses")
        return self._responses.pop(0)


def _setup_with_scripted_provider(responses: list[str]) -> _ScriptedProvider:
    register_plugins()
    register_agents([AgentSpec(name="searcher", role="finds")])
    provider = _ScriptedProvider(responses)
    register_plugin_option("llm_provider", "scripted", lambda: provider)
    set_config(Config(llm_provider="scripted", options={"llm": {}}))
    return provider


def _plan_with_done_node() -> DAGPlan:
    plan = DAGPlan(goal="g", available_agents=["searcher"])
    n = DAGNode(id="1", agent="searcher", input={"goal": "find X"})
    n.status = NodeStatus.SUCCEEDED
    n.result = "X found"
    plan.add_node(n)
    return plan


def test_extend_adds_new_nodes():
    raw = json.dumps({
        "nodes": {"r1": {"agent": "searcher", "input": {"goal": "verify X"}}},
        "edges": [["1", "r1"]],
        "halt_reason": None,
        "rationale": "follow-up needed",
    })
    _setup_with_scripted_provider([raw])
    replanner = DynamicReplanner()
    plan = _plan_with_done_node()

    ext = replanner.extend(
        goal="g",
        agents=["searcher"],
        prior=plan,
        results={"1": "X found"},
        errors={},
    )
    assert len(ext.extensions) == 1
    assert ext.extensions[0].id == "2"
    assert ext.extensions[0].agent == "searcher"
    assert ext.new_edges == [("1", "2")]
    assert ext.halt_reason is None
    assert ext.rationale == "follow-up needed"


def test_extend_returns_halt_done():
    raw = json.dumps({
        "nodes": {},
        "edges": [],
        "halt_reason": "done",
        "rationale": "goal achieved",
    })
    _setup_with_scripted_provider([raw])
    replanner = DynamicReplanner()
    plan = _plan_with_done_node()

    ext = replanner.extend("g", ["searcher"], plan, {"1": "ok"}, {})
    assert ext.halt_reason == HaltReason.DONE
    assert ext.extensions == []


def test_extend_rejects_invalid_halt_reason():
    raw = json.dumps({"nodes": {}, "edges": [], "halt_reason": "bogus"})
    _setup_with_scripted_provider([raw])
    replanner = DynamicReplanner(max_parse_retries=0)
    plan = _plan_with_done_node()

    ext = replanner.extend("g", ["searcher"], plan, {}, {})
    assert ext.halt_reason is None
    assert ext.extensions == []
    assert "invalid" in ext.rationale


def test_max_extensions_aborts_after_cap():
    raw = json.dumps({"nodes": {}, "edges": []})
    _setup_with_scripted_provider([raw, raw])
    replanner = DynamicReplanner(max_extensions=2)
    plan = _plan_with_done_node()

    for _ in range(2):
        ext = replanner.extend("g", ["searcher"], plan, {}, {})
        assert ext.halt_reason is None
    third = replanner.extend("g", ["searcher"], plan, {}, {})
    assert third.halt_reason == HaltReason.ABORT
    assert "max_extensions" in third.rationale


def test_max_total_nodes_aborts():
    _setup_with_scripted_provider([json.dumps({"nodes": {}, "edges": []})])
    replanner = DynamicReplanner(max_total_nodes=1)
    plan = _plan_with_done_node()
    ext = replanner.extend("g", ["searcher"], plan, {}, {})
    assert ext.halt_reason == HaltReason.ABORT
    assert "max_total_nodes" in ext.rationale


def test_dedup_skip_dup_goals_filters_existing_goal():
    raw = json.dumps({
        "nodes": {
            "r1": {"agent": "searcher", "input": {"goal": "find X"}},
            "r2": {"agent": "searcher", "input": {"goal": "verify Y"}},
        },
        "edges": [],
    })
    _setup_with_scripted_provider([raw])
    replanner = DynamicReplanner(dedup_strategy="skip_dup_goals")
    plan = _plan_with_done_node()
    plan.nodes["1"].input = {"goal": "find X"}

    ext = replanner.extend("g", ["searcher"], plan, {"1": "ok"}, {})
    ids = {n.id for n in ext.extensions}
    assert ids == {"2"}


def test_replan_count_increments_even_on_empty_extension():
    raw = json.dumps({
        "nodes": {},
        "edges": [],
        "halt_reason": None,
        "rationale": "no-op",
    })
    _setup_with_scripted_provider([raw])
    replanner = DynamicReplanner(max_extensions=1)
    plan = _plan_with_done_node()

    replanner.extend("g", ["searcher"], plan, {}, {})
    assert replanner._replan_count == 1
    second = replanner.extend("g", ["searcher"], plan, {}, {})
    assert second.halt_reason == HaltReason.ABORT