"""Strict, non-generative parsing: no substring guessing or selecting history[-1]."""

import json

import pytest

from engine.community.core.dws_hosting.results import parse_decision
from engine.community.kernel.dws_execution import ExecutionError

from .test_execution import decision_json, result_json


@pytest.mark.parametrize("action", ["reply", "ignore", "escalate"])
@pytest.mark.parametrize("fence", [False, True])
def test_valid_single_decision(action, fence):
    text = decision_json(action)
    if fence:
        text = f"```json\n{text}\n```"
    assert parse_decision(result_json(text), 1).action == action


@pytest.mark.parametrize(
    "changes",
    [
        {"recipient": "someone"},
        {"action": "send"},
        {"text": " "},
        {"text": "x" * 8001},
        {"schema_version": True},
        {"schema_version": 2},
        {"rule_version": True},
        {"rule_version": "1"},
        {"rule_version": 2},
        {"summary": "extra"},
        {"action": "ignore"},
        {"action": "escalate", "summary": "needs help"},
    ],
)
def test_invalid_decision_never_sent(changes):
    value = json.loads(decision_json()) | changes
    with pytest.raises(ExecutionError):
        parse_decision(result_json(json.dumps(value)), 1)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Working...",
        "prefix " + decision_json(),
        decision_json() + " trailing",
        "[]",
        "null",
        "NaN",
        '{"schema_version":1,"schema_version":1}',
        "[[" * 1000,
        '{"action":"ignore"}',
    ],
)
def test_malformed_and_progress_not_misidentified(text):
    with pytest.raises(ExecutionError):
        parse_decision(result_json(text), 1)


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "bad",
        "x" * 262145,
        '{"payloads":[],"payloads":[]}',
        json.dumps({"payloads": []}),
        json.dumps({"payloads": [None]}),
        json.dumps({"payloads": [{"text": 4}]}),
        json.dumps({"payloads": [{"text": decision_json(), "isError": True}]}),
        json.dumps({"payloads": [{"text": decision_json(), "mediaUrl": "file"}]}),
        json.dumps(
            {"payloads": [{"text": decision_json()}], "meta": {"aborted": True}}
        ),
        json.dumps({"payloads": [{"text": decision_json()}], "meta": []}),
        json.dumps({"payloads": [{"text": decision_json()}] * 2}),
        json.dumps({"payloads": [{"text": "progress"}, {"text": decision_json()}]}),
    ],
)
def test_invalid_ambiguous_or_failed_envelopes(raw):
    with pytest.raises(ExecutionError):
        parse_decision(raw, 1)


def test_empty_escalation_is_not_a_notification():
    with pytest.raises(ExecutionError):
        parse_decision(
            result_json(
                '{"schema_version":1,"rule_version":1,"action":"escalate","summary":" "}'
            ),
            1,
        )


def test_blank_payload_does_not_hide_single_result():
    assert (
        parse_decision(
            json.dumps({"payloads": [{"text": " "}, {"text": decision_json()}]}), 1
        ).action
        == "reply"
    )
