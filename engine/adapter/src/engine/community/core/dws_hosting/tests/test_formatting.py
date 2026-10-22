"""Syntax normalization never generates missing content or re-runs the agent."""

import json

import pytest

from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.core.dws_hosting.formatting import repair_format
from engine.community.kernel.dws_execution import ExecutionError

from .test_execution import Engine, build_execution, decision_json, result_json


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version":1 "rule_version":1 "action":"reply" "text":"Hello"}',
        '{"schema_version":1,"rule_version":1,"action":"reply","text":"Hello",}',
        '```json\n{"schema_version":1,"rule_version":1,"action":"reply","text":"Hello",}\n```',
    ],
)
async def test_repair_commas_preserves_values_no_second_call(tmp_path, text):
    state, ledger, turn, _ = build_execution(tmp_path)
    engine = Engine(text=text)
    record = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert record.state == "resolved" and record.decision.text == "Hello"
    assert len(engine.calls) == 1
    assert record.result.result_json == result_json(text)


@pytest.mark.parametrize(
    "text",
    [
        "plain text",
        "{}",
        "{",
        '{"action":}',
        '{"schema_version":1,"rule_version":2,"action":"ignore",}',
        '{"schema_version":1,"rule_version":1,"action":"reply",}',
        '{"schema_version":1,"rule_version":1,"action":"ignore","action":"reply",}',
        '{"schema_version":1,"rule_version":1,"action":"ignore","recipient":"other",}',
        '{"schema_version":1,"rule_version":1,"action":"ignore",,}',
        '{"schema_version":1,"rule_version":1,"action":"ignore","x":{}}',
        '{"schema_version":1,"rule_version":1,"action":"ignore"} trailing',
        '{"schema_version":1,"rule_version":1,"action":"ignore"} {}',
        "{1:2}",
        '{"action" "reply"}',
    ],
)
async def test_ambiguous_or_incomplete_output_requires_manual(tmp_path, text):
    state, ledger, turn, _ = build_execution(tmp_path)
    engine = Engine(text=text)
    record = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert record.state == "manual" and record.decision is None
    assert len(engine.calls) == 1


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"payloads": []},
        {"payloads": [{"text": decision_json()}, {"text": decision_json()}]},
        {"payloads": [{"text": decision_json(), "mediaUrl": "x"}]},
        {"payloads": [{"text": decision_json()}], "meta": {"aborted": True}},
    ],
)
def test_non_single_result_never_repaired(value):
    with pytest.raises(ExecutionError):
        repair_format(json.dumps(value), 1)


def test_quoted_punctuation_in_reply_is_preserved():
    text = 'commas , } { "quoted" text'
    decision = {"schema_version": 1, "rule_version": 1, "action": "reply", "text": text}
    raw = json.dumps(decision)[:-1] + ",  }"
    assert repair_format(result_json(raw), 1).text == text
