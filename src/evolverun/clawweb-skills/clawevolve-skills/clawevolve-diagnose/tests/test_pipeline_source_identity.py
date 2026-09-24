from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clawevolve_diagnose.acquisition import runtime_identity
from clawevolve_diagnose.models import CasePreference, RunRequest
from clawevolve_diagnose.run import runner


@pytest.mark.parametrize(
    ("source", "requested_bot", "expected_bot"),
    [
        ("local", "ocb-test-bot", "ocb-test-bot"),
        ("local", "", "agent_temporary-business-agent"),
        ("service_export", "service-request", "exported-service-bot"),
    ],
)
def test_pipeline_preserves_source_identity_in_plan_handoff(
    monkeypatch, tmp_path, source, requested_bot, expected_bot
):
    # The local environment lacks OCB metadata and contains a temporary Agent.
    # Exercise the actual pipeline and resolver, including Plan Source output.
    monkeypatch.setattr(
        runner, "discover_layout", lambda *_: {"agent_id": "temporary-business-agent"}
    )
    monkeypatch.setattr(runtime_identity, "_candidate_bcs_session_files", lambda: [])
    monkeypatch.setattr(runtime_identity, "_candidate_workspace_state_files", lambda *_: [])
    monkeypatch.setattr(runner, "parse_preference", lambda *_: (CasePreference(), []))
    monkeypatch.setattr(runner, "discover_sessions", lambda *a, **kw: [])
    monkeypatch.setattr(
        runner,
        "acquire_exported_sessions",
        lambda **kw: SimpleNamespace(
            rows=[], source_bot_id="exported-service-bot", source_metadata={}
        ),
    )
    observed_bots = []

    def sample(rows, bot_id, *args, **kwargs):
        observed_bots.append(bot_id)
        return [], [], {}

    monkeypatch.setattr(runner, "_diagnose_and_sample", sample)
    result = runner.run_pipeline(
        RunRequest(
            api_key="test-key",
            message="Inspect available sessions",
            output_dir=tmp_path,
            task_id="TASK-identity",
            step_id="STEP-identity",
            judge_backend="api",
            model="test-model",
            session_source=source,
            source_bot_id=requested_bot,
        )
    )

    plan_source = json.loads((tmp_path / "plan-source.json").read_text())
    assert observed_bots == [expected_bot]
    assert plan_source["source"]["bot_id"] == expected_bot
    assert result.summary["bot_id"] == expected_bot
