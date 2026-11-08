"""Robustness tests for DecisionParser (TypeError kinds, string-aware JSON
extraction, candidate continuation, and size caps).

These pin the repair-loop safety properties of `parse()`: every malformed LLM
decision must come back as `DecisionParse(decision=None, error=...)`, never
as an exception, and oversized decisions must be rejected as repair-able
errors instead of being accepted and persisted.
"""
from __future__ import annotations

import json

from agentclaw.community.core.agentswarm.domain.models import (
    FinishDecision,
    SpawnDecision,
    WaitDecision,
)
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    MAX_AGENTS,
    MAX_FIELD_CHARS,
    MAX_SCHEMA_CHARS,
    DecisionParser,
)


def _spawn(agents):
    return json.dumps({"kind": "spawn_agent", "agents": agents})


class TestUnhashableEnumInputs:
    """Fix 1: dict/list values must produce error parses, not TypeError."""

    def test_kind_dict_returns_error_not_raise(self):
        parse = DecisionParser().parse(json.dumps({"kind": {}}))
        assert parse.decision is None
        assert parse.error is not None and "kind" in parse.error

    def test_kind_list_returns_error_not_raise(self):
        parse = DecisionParser().parse(json.dumps({"kind": []}))
        assert parse.decision is None
        assert parse.error is not None and "kind" in parse.error

    def test_mode_dict_returns_error_not_raise(self):
        raw = json.dumps({"kind": "wait", "refs": ["a"], "mode": {}})
        parse = DecisionParser().parse(raw)
        assert parse.decision is None
        assert parse.error is not None and "mode" in parse.error

    def test_mode_list_returns_error_not_raise(self):
        raw = json.dumps({"kind": "wait", "refs": ["a"], "mode": []})
        parse = DecisionParser().parse(raw)
        assert parse.decision is None
        assert parse.error is not None and "mode" in parse.error


class TestStringAwareExtraction:
    """Fix 2a: braces inside JSON string values must not break extraction."""

    def test_finish_answer_with_unbalanced_brace_parses(self):
        parse = DecisionParser().parse(
            '{"kind": "finish", "answer": "the function ends with } so be it"}'
        )
        assert isinstance(parse.decision, FinishDecision)
        assert parse.decision.answer == "the function ends with } so be it"
        assert parse.error is None

    def test_spawn_objective_with_unbalanced_brace_parses(self):
        raw = (
            '{"kind": "spawn_agent", "agents": ['
            '{"name": "coder", "objective": "close the } block in src/app.py"}'
            "]}"
        )
        parse = DecisionParser().parse(raw)
        assert isinstance(parse.decision, SpawnDecision)
        assert parse.decision.agents[0].objective == (
            "close the } block in src/app.py"
        )

    def test_escaped_quote_with_brace_parses(self):
        answer = 'he said \\"ok }\\" then left'
        raw = '{"kind": "finish", "answer": "%s"}' % answer
        parse = DecisionParser().parse(raw)
        assert isinstance(parse.decision, FinishDecision)

    def test_brace_in_key_string_parses(self):
        raw = '{"kind{ing}": 1, "kind": "finish", "answer": "v2"}'
        parse = DecisionParser().parse(raw)
        assert isinstance(parse.decision, FinishDecision)
        assert parse.decision.answer == "v2"


class TestCandidateContinuation:
    """Fix 2b: after a malformed first candidate, keep scanning."""

    def test_valid_second_object_after_malformed_first(self):
        raw = (
            "partial {\"kind\": \"wait\", \"refs\": [b c]} noise "
            '{"kind": "finish", "answer": "all good"}'
        )
        parse = DecisionParser().parse(raw)
        assert isinstance(parse.decision, FinishDecision)
        assert parse.decision.answer == "all good"

    def test_valid_second_object_after_truncated_first(self):
        raw = '{"kind": "spawn_agent", "unclosed... {"kind": "finish", "answer": "x"}'
        parse = DecisionParser().parse(raw)
        assert isinstance(parse.decision, FinishDecision)
        assert parse.decision.answer == "x"

    def test_wait_after_malformed_first(self):
        raw = (
            "junk {broken: x}"
            '{"kind": "wait", "refs": ["a1"], "mode": "any"}'
        )
        parse = DecisionParser().parse(raw)
        assert isinstance(parse.decision, WaitDecision)
        assert parse.decision.refs == ("a1",)
        assert parse.error is None

    def test_all_candidates_malformed_still_errors(self):
        parse = DecisionParser().parse('{"kind": {"a"} and {"wait" "x"}')
        assert parse.decision is None
        assert parse.error is not None


class TestSizeCaps:
    """Fix 3: oversized decisions return error parses, never acceptance."""

    def test_agents_over_cap_rejected(self):
        agents = [{"name": f"a{i}", "objective": "o"} for i in range(MAX_AGENTS + 1)]
        parse = DecisionParser().parse(_spawn(agents))
        assert parse.decision is None
        assert parse.error is not None and "age" in parse.error
        assert str(MAX_AGENTS) in parse.error

    def test_agents_at_cap_accepted(self):
        agents = [{"name": f"a{i}", "objective": "o"} for i in range(MAX_AGENTS)]
        parse = DecisionParser().parse(_spawn(agents))
        assert parse.decision is not None and parse.error is None
        assert isinstance(parse.decision, SpawnDecision)
        assert len(parse.decision.agents) == MAX_AGENTS

    def test_oversized_objective_rejected(self):
        agent = {"name": "a", "objective": "x" * (MAX_FIELD_CHARS + 1)}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is None
        assert parse.error is not None and "objective" in parse.error
        assert str(MAX_FIELD_CHARS) in parse.error

    def test_oversized_boundaries_rejected(self):
        agent = {
            "name": "a",
            "objective": "o",
            "boundaries": "b" * (MAX_FIELD_CHARS + 1),
        }
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is None
        assert parse.error is not None and "boundaries" in parse.error

    def test_objective_at_cap_accepted(self):
        agent = {"name": "a", "objective": "x" * MAX_FIELD_CHARS}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is not None and parse.error is None

    def test_oversized_answer_rejected(self):
        raw = json.dumps({
            "kind": "finish",
            "answer": "a" * (MAX_FIELD_CHARS + 1),
        })
        parse = DecisionParser().parse(raw)
        assert parse.decision is None
        assert parse.error is not None and "answer" in parse.error

    def test_oversized_wait_ref_rejected(self):
        raw = json.dumps({
            "kind": "wait",
            "refs": ["r" * (MAX_FIELD_CHARS + 1)],
        })
        parse = DecisionParser().parse(raw)
        assert parse.decision is None
        assert parse.error is not None and "ref" in parse.error

    def test_oversized_parent_goal_rejected(self):
        agent = {"name": "a", "objective": "o", "parent_goal": "p" * (MAX_FIELD_CHARS + 1)}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is None
        assert parse.error is not None and "parent_goal" in parse.error


class TestOutputSchemaValidation:
    """output_schema becomes a jsonschema validator key downstream (D42):
    the parse boundary must accept only JSON objects within the size cap —
    anything else is a repair-able error, never a poisoned validator."""

    def test_non_dict_string_schema_rejected(self):
        agent = {"name": "a", "objective": "o", "output_schema": "object"}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is None
        assert parse.error is not None and "output_schema" in parse.error

    def test_non_dict_list_schema_rejected(self):
        agent = {"name": "a", "objective": "o", "output_schema": [{"type": "object"}]}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is None
        assert parse.error is not None and "output_schema" in parse.error

    def test_non_dict_int_schema_rejected(self):
        agent = {"name": "a", "objective": "o", "output_schema": 3}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is None
        assert parse.error is not None and "output_schema" in parse.error

    def test_non_dict_bool_schema_rejected(self):
        agent = {"name": "a", "objective": "o", "output_schema": True}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is None
        assert parse.error is not None and "output_schema" in parse.error

    def test_non_dict_schema_in_later_agent_rejected(self):
        agents = [
            {"name": "ok", "objective": "o"},
            {"name": "bad", "objective": "o", "output_schema": "nope"},
        ]
        parse = DecisionParser().parse(_spawn(agents))
        assert parse.decision is None
        assert parse.error is not None and "output_schema" in parse.error

    def test_oversize_schema_rejected(self):
        schema = {"k": "v" * (MAX_SCHEMA_CHARS + 1)}
        agent = {"name": "a", "objective": "o", "output_schema": schema}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is None
        assert parse.error is not None and "output_schema" in parse.error
        assert str(MAX_SCHEMA_CHARS) in parse.error

    def test_schema_at_cap_accepted(self):
        # An ASCII char grows the dump by exactly 1: pad from the empty value to the cap.
        pad = MAX_SCHEMA_CHARS - len(json.dumps({"k": ""}))
        schema = {"k": "x" * pad}
        assert len(json.dumps(schema)) == MAX_SCHEMA_CHARS
        agent = {"name": "a", "objective": "o", "output_schema": schema}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is not None and parse.error is None
        assert parse.decision.agents[0].output_schema == schema

    def test_valid_small_dict_schema_accepted(self):
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        agent = {"name": "a", "objective": "o", "output_schema": schema}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is not None and parse.error is None
        assert isinstance(parse.decision, SpawnDecision)
        assert parse.decision.agents[0].output_schema == schema

    def test_missing_schema_defaults_to_empty_dict(self):
        agent = {"name": "a", "objective": "o"}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is not None and parse.error is None
        assert isinstance(parse.decision, SpawnDecision)
        assert parse.decision.agents[0].output_schema == {}

    def test_null_schema_defaults_to_empty_dict(self):
        agent = {"name": "a", "objective": "o", "output_schema": None}
        parse = DecisionParser().parse(_spawn([agent]))
        assert parse.decision is not None and parse.error is None
        assert parse.decision.agents[0].output_schema == {}