"""Legacy run content is projected explicitly, without provider extensions."""

import pytest

from agentclaw.community.adapters.http.openapi_v1.routines.converter import map_runs
from agentclaw.community.adapters.http.openapi_v1.routines.relay import _job_log


@pytest.mark.parametrize("field", ["messages", "conversation", "turns"])
def test_legacy_messages_and_content_aliases_are_allowlisted(field):
    result = map_runs({"data": {"input": "historical prompt", "runs": [{
        "job_id": "run", "output": "answer", "error": "execution failed",
        "credentials": {"private": "must-not-expose"},
        field: [
            {"role": "user", "text": "question", "private": "must-not-expose"},
            {"role": "assistant", "output": "answer"},
            {"content": "partial message"},
        ],
    }]}}, "routine")
    run = result[0]
    assert run.input == "historical prompt"
    assert run.output == "answer" and run.error == "execution failed"
    assert [entry.model_dump() for entry in run.messages] == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
        {"role": "assistant", "content": "partial message"},
    ]
    assert "must-not-expose" not in run.model_dump_json()


def test_missing_malformed_content_is_not_stringified_or_invented():
    runs = map_runs({"data": {"runs": [{"job_id": "a"}, {
        "job_id": "b", "input": {"private": "ignored"}, "output": [], "error": 42,
        "messages": [None, "invalid", {"content": {"private": "ignored"}}, {"content": "  "}],
        "conversation": [{"role": "tool", "content": "tool text"}],
    }]}}, "routine")
    assert runs[0].messages == []
    assert runs[0].input is None and runs[0].output is None and runs[0].error is None
    assert runs[1].input is None and runs[1].output is None and runs[1].error is None
    assert runs[1].messages[0].model_dump() == {"role": "tool", "content": "tool text"}


def test_fanout_keeps_parent_inputs_and_context_separate_for_duplicate_run_ids():
    rows = [{"success": True, "runtime_stage": stage, "device_uuid": device,
             "operator_id": caller, "data": {"input": prompt, "runs": [{"job_id": "same"}]}}
            for stage, device, caller, prompt in [
                ("verify", "instance-a", "caller-a", "prompt-a"),
                ("online", "instance-b", "caller-b", "prompt-b"),
            ]]
    rows[1]["data"]["runs"].append({"job_id": "own-input", "input": "specific input"})
    rows.append({"success": False, "data": {"input": "failed instance"}})
    runs = map_runs({"data": {"results": rows}}, "routine")
    assert [(run.run_id, run.input, run.runtime_stage, run.device_uuid, run.operator_id) for run in runs] == [
        ("same", "prompt-a", "verify", "instance-a", "caller-a"),
        ("same", "prompt-b", "online", "instance-b", "caller-b"),
        ("own-input", "specific input", "online", "instance-b", "caller-b"),
    ]


def test_new_transcript_fields_do_not_expand_relay_logs():
    logged = _job_log({"runs": [{"job_id": "run", "input": "historical-only",
                                 "messages": [{"content": "transcript-only"}], "error": "error-only"}]})
    assert "historical-only" not in str(logged)
    assert "transcript-only" not in str(logged)
    assert "error-only" not in str(logged)
