import json

import pytest

from engine.community.core.dws_hosting.results import parse_decision
from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.kernel.dws_execution import ExecutionError
from .test_execution import Engine, build_execution, result_json, decision_json


@pytest.mark.parametrize("separator", ["", "\n", " \t"])
@pytest.mark.parametrize("count", [1, 2, 8])
def test_exact_repetition_is_lossless(separator, count):
    raw = decision_json()
    assert parse_decision(
        result_json(separator.join([raw] * count)), 1
    ) == parse_decision(result_json(raw), 1)


@pytest.mark.parametrize(
    "tail",
    [
        "prose",
        "{}",
        "[]",
        "null",
        '{"action":"ignore"}',
        '{"action":"reply","action":"ignore"}',
        '{"x":NaN}',
        "{",
    ],
)
def test_mixed_or_malformed_output_rejected(tail):
    with pytest.raises(ExecutionError):
        parse_decision(result_json(decision_json() + tail), 1)


def test_conflicting_text_and_repetition_limit():
    raw = decision_json()
    changed = json.loads(raw)
    changed["text"] = "different answer"
    for value in (raw + json.dumps(changed), raw * 9):
        with pytest.raises(ExecutionError):
            parse_decision(result_json(value), 1)


def test_repeated_wrong_version_still_rejected():
    with pytest.raises(ExecutionError, match="rule_version_mismatch"):
        parse_decision(result_json(decision_json(version=9) * 2), 1)


async def test_duplicate_still_reviewed_once_without_redispatch(tmp_path):
    state, ledger, turn, _ = build_execution(tmp_path)
    engine = Engine(text=decision_json() * 2)
    record = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert record.state == "resolved"
    assert len(engine.calls) == len(engine.reviews) == 1


@pytest.mark.parametrize("action", ["reply", "ignore", "escalate"])
@pytest.mark.parametrize("separator", ["", "\n", " \t"])
def test_identical_fenced_decisions(action, separator):
    raw = decision_json(action)
    fenced = "```json\n" + raw + "\n```"
    assert parse_decision(
        result_json(separator.join([fenced] * 2)), 1
    ) == parse_decision(result_json(raw), 1)


@pytest.mark.parametrize(
    "bad", ["prose", "```json\n{}\n```", "```json\n{}", "[]", '```json\n{"x":NaN}\n```']
)
def test_fenced_repetitions_reject_trailing_or_conflicting_data(bad):
    fenced = "```json\n" + decision_json() + "\n```"
    with pytest.raises(ExecutionError):
        parse_decision(result_json(fenced + bad), 1)


def test_fenced_limits_duplicate_keys_and_string_delimiters():
    raw = json.loads(decision_json())
    raw["text"] = "literal ```json and } in the reply"
    text = json.dumps(raw)
    fenced = "```json\n" + text + "\n```"
    assert parse_decision(result_json(fenced * 8), 1).text == raw["text"]
    for value in (fenced * 9, '```json\n{"action":"ignore","action":"reply"}\n```'):
        with pytest.raises(ExecutionError):
            parse_decision(result_json(value), 1)


@pytest.mark.parametrize("action", ["ignore", "reply"])
async def test_actual_fenced_duplicate_payload_is_resolved_once(tmp_path, action):
    state, ledger, turn, _ = build_execution(tmp_path)
    fenced = "```json\n" + decision_json(action) + "\n```"
    engine = Engine(text=fenced * 2)
    record = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert record.state == "resolved"
    assert record.decision.action == action
    assert len(engine.calls) == 1
    assert len(engine.reviews) == (1 if action == "reply" else 0)
