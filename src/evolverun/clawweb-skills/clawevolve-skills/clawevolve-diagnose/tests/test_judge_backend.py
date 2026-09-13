from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from clawevolve_diagnose.judge.analyzer_factory import create_session_analyzer
from clawevolve_diagnose.judge.agent_session_contract import validate_agent_session_result
from clawevolve_diagnose.judge.keyless_session_analyzer import KeylessSubagentSessionAnalyzer
from clawevolve_diagnose.judge.ocsa_session_report_analyzer import (
    OcsaSessionReportAnalyzer,
    OcsaSessionReportJudgeConfig,
)
from clawevolve_diagnose.judge.openclaw_subagent_client import (
    OpenClawJsonSubagentClient,
    SubagentJudgeError,
    SubagentPromptResult,
    _agent_store_dir,
    _assistant_text_from_jsonl,
    _diagnostic_excerpt,
    _verify_agent_registered,
)
from clawevolve_diagnose.judge.native_session_analysis import (
    build_native_session_analysis_input,
)
from clawevolve_diagnose.judge.runtime import resolve_judge_runtime
from clawevolve_diagnose.models import (
    CasePreference,
    JudgeRuntimeConfig,
    LlmRuntimeConfig,
    RunRequest,
    SessionRow,
    SubagentJudgeConfig,
)


def _request(tmp_path: Path, **overrides: object) -> RunRequest:
    values: dict[str, object] = {
        "api_key": "",
        "message": "diagnose",
        "output_dir": tmp_path,
        "task_id": "EV-20260819-ABCDEF",
        "step_id": "STEP-1",
        "model": "antchat/GLM-5.2",
    }
    values.update(overrides)
    return RunRequest(**values)  # type: ignore[arg-type]


def test_no_backend_and_no_key_defaults_to_subagent(tmp_path: Path) -> None:
    runtime = resolve_judge_runtime(_request(tmp_path))

    assert runtime.backend == "subagent"
    assert runtime.subagent.transport == "local"
    assert runtime.subagent.fallback_to_cli is True
    assert runtime.subagent.agent_id.startswith("clawevolve-diagnose-")
    assert runtime.subagent.workspace.endswith("/workspace")


def test_legacy_key_without_backend_keeps_api(tmp_path: Path) -> None:
    runtime = resolve_judge_runtime(_request(tmp_path, api_key="legacy-key"))

    assert runtime.backend == "api"
    assert runtime.api.api_key == "legacy-key"


def test_explicit_subagent_wins_even_when_key_exists(tmp_path: Path) -> None:
    runtime = resolve_judge_runtime(
        _request(tmp_path, api_key="unused-key", judge_backend="subagent")
    )

    assert runtime.backend == "subagent"
    assert runtime.api.api_key == ""


def test_explicit_api_requires_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="API judge requires"):
        resolve_judge_runtime(_request(tmp_path, judge_backend="api"))


def test_factory_creates_selected_analyzer(tmp_path: Path) -> None:
    preference = CasePreference(raw_message="diagnose")
    api_runtime = resolve_judge_runtime(_request(tmp_path, api_key="key"))
    subagent_runtime = resolve_judge_runtime(_request(tmp_path))

    assert isinstance(
        create_session_analyzer(api_runtime, preference, max_concurrent_tasks=1),
        OcsaSessionReportAnalyzer,
    )
    assert isinstance(
        create_session_analyzer(subagent_runtime, preference, max_concurrent_tasks=1),
        KeylessSubagentSessionAnalyzer,
    )


def test_agent_store_uses_configured_openclaw_home(tmp_path: Path) -> None:
    assert _agent_store_dir("judge", str(tmp_path)) == tmp_path / "agents" / "judge"


def test_agent_registration_verification_retries_until_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = SubagentJudgeConfig(
        agent_id="clawevolve-diagnose-task",
        workspace=str(tmp_path / "workspace"),
    )
    outputs = iter(
        [
            "Agents:\n- main (default)\n",
            "Agents:\n- main (default)\n",
            "Agents:\n- main (default)\n- clawevolve-diagnose-task\n",
        ]
    )
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_command(
        _config: SubagentJudgeConfig,
        args: list[str],
        **_kwargs: object,
    ) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")

    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client._run_openclaw_command",
        fake_command,
    )
    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    _verify_agent_registered(config)

    assert calls == [["agents", "list"]] * 3
    assert sleeps == [30.0, 30.0]


def test_agent_registration_verification_fails_after_bounded_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = SubagentJudgeConfig(
        agent_id="clawevolve-diagnose-task",
        workspace=str(tmp_path / "workspace"),
    )
    calls = 0
    sleeps: list[float] = []

    def fake_command(
        _config: SubagentJudgeConfig,
        _args: list[str],
        **_kwargs: object,
    ) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            returncode=0,
            stdout="Agents:\n- main (default)\n",
            stderr="",
        )

    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client._run_openclaw_command",
        fake_command,
    )
    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    with pytest.raises(SubagentJudgeError, match="attempts=4"):
        _verify_agent_registered(config)

    assert calls == 4
    assert sleeps == [30.0, 30.0, 60.0]


def test_client_close_cleans_sessions_and_deletes_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str]] = []
    config = SubagentJudgeConfig(
        agent_id="clawevolve-diagnose-task",
        workspace=str(tmp_path / "workspace"),
        openclaw_home=str(tmp_path / ".openclaw"),
        transport="cli",
        fallback_to_cli=False,
    )
    client = OpenClawJsonSubagentClient(config)
    client._registered = True
    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client.cleanup_agent_sessions",
        lambda agent_id, openclaw_home="": calls.append(("sessions", agent_id)),
    )
    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client._delete_agent",
        lambda _config, agent_id: calls.append(("agent", agent_id)),
    )

    client.close()
    client.close()

    assert calls == [
        ("sessions", "clawevolve-diagnose-task"),
        ("agent", "clawevolve-diagnose-task"),
    ]


def test_subagent_local_transport_adds_local_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    config = SubagentJudgeConfig(
        agent_id="clawevolve-diagnose-task",
        workspace=str(tmp_path / "workspace"),
        openclaw_home=str(tmp_path / ".openclaw"),
        transport="local",
        fallback_to_cli=False,
    )
    client = OpenClawJsonSubagentClient(config)
    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client.cleanup_agent_sessions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client._load_transcript_text",
        lambda *_args, **_kwargs: ('{"ok": true}', None),
    )

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "clawevolve_diagnose.judge.openclaw_subagent_client.subprocess.run",
        fake_run,
    )

    result = client._run_prompt_with_preferred_transport("judge", timeout_seconds=30)

    assert result["status"] == "success"
    assert result["transport"] == "local"
    assert commands[0][1:3] == ["agent", "--local"]


def test_filtered_agent_result_keeps_complete_request_and_response(tmp_path: Path) -> None:
    runtime = resolve_judge_runtime(_request(tmp_path))
    analyzer = create_session_analyzer(
        runtime,
        CasePreference(raw_message="只关注语雀 MCP 失败"),
        max_concurrent_tasks=1,
        artifact_dir=tmp_path / "judge",
    )
    assert isinstance(analyzer, KeylessSubagentSessionAnalyzer)
    response = {
        "schema_version": "clawevolve-diagnose-native-session-output.v1",
        "session_id": "session-filtered",
        "is_evaluable": False,
        "case_type": "bad",
        "query": "",
        "intent_match": {
            "is_match": False,
            "confidence": 0.99,
            "matched_aspects": [],
            "unmatched_aspects": ["Yuque MCP"],
            "reason": "not about Yuque MCP",
        },
        "original_query": "open a web page",
        "symptom_class": "UNRELATED",
        "root_cause_class": "UNKNOWN",
        "evolution_failure_mode": "unknown_failure_mode",
        "root_cause_summary": "The session is unrelated.",
        "evidence": [],
        "requires_search": False,
        "confidence": 0.99,
        "quality_score": 0.0,
        "quality_notes": [],
        "tool_hints": [],
        "evidence_file_hints": [],
        "failure_controllability": "low",
        "optimization_value": "low",
        "reject_reason": "unrelated_to_user_request",
        "reject_category": "unrelated_to_user_request",
        "reject_detail": "The session does not involve Yuque MCP.",
    }
    raw_response = json.dumps(response, ensure_ascii=False)

    class FakeClient:
        def run_json_prompt_captured(self, prompt: str, timeout: int) -> SubagentPromptResult:
            assert "/tmp/session-filtered.jsonl" in prompt
            return SubagentPromptResult(raw_text=raw_response, parsed=response)

        def close(self) -> None:
            return None

    analyzer.client = FakeClient()  # type: ignore[assignment]
    from clawevolve_diagnose.models import SessionRow

    diagnoses = analyzer.analyze(
        [
            SessionRow(
                session_id="session-filtered",
                path="/tmp/session-filtered.jsonl",
                bot_id="bot",
                created_at="2026-08-19T00:00:00+00:00",
                first_question="open a web page",
                user_text="",
                assistant_text="",
                tool_text="",
                raw_text="",
            )
        ],
        "bot",
    )

    artifact_dir = tmp_path / "judge" / "session-filtered"
    assert diagnoses == []
    assert json.loads((artifact_dir / "request.json").read_text())["prompt"]
    assert (artifact_dir / "response.txt").read_text() == raw_response
    assert json.loads((artifact_dir / "response.json").read_text()) == response
    validation = json.loads((artifact_dir / "validation.json").read_text())
    assert validation["valid"] is True
    assert validation["decision"] == "filtered_after_contract_validation"


def test_transcript_extraction_returns_only_last_assistant_message(tmp_path: Path) -> None:
    transcript = tmp_path / "judge.jsonl"
    events = [
        {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "full prompt"}]}},
        {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "working"}]}},
        {"type": "message", "message": {"role": "toolResult", "content": [{"type": "text", "text": '{"traceSchema":"openclaw-trajectory"}'}]}},
        {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": '{"schema_version":"diagnose"}'}]}},
    ]
    transcript.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    assert _assistant_text_from_jsonl(transcript) == '{"schema_version":"diagnose"}'


def test_evaluable_agent_result_accepts_only_minimum_case_fields() -> None:
    row = SessionRow(
        session_id="session-bad",
        path="/tmp/session-bad.jsonl",
        bot_id="bot",
        created_at="2026-08-19T00:00:00+00:00",
        first_question="open yuque",
        user_text="",
        assistant_text="",
        tool_text="",
        raw_text="",
    )
    result = {
        "schema_version": "clawevolve-diagnose-native-session-output.v1",
        "session_id": "session-bad",
        "is_evaluable": True,
        "case_type": "bad",
        "query": "Open the Yuque document with the available MCP tool.",
        "intent_match": {"is_match": True},
        "root_cause_class": "CAPABILITY_BOUNDARY",
        "evidence": [{"source": "session", "snippet": "The agent gave up before trying MCP."}],
    }

    validation = validate_agent_session_result(row, result)

    assert validation["valid"] is True
    assert validation["errors"] == []


def test_rejected_agent_result_requires_only_reject_reason() -> None:
    row = SessionRow(
        session_id="session-rejected",
        path="/tmp/session-rejected.jsonl",
        bot_id="bot",
        created_at="2026-08-19T00:00:00+00:00",
        first_question="unrelated task",
        user_text="",
        assistant_text="",
        tool_text="",
        raw_text="",
    )
    result = {
        "schema_version": "clawevolve-diagnose-native-session-output.v1",
        "session_id": "session-rejected",
        "is_evaluable": False,
        "intent_match": {"is_match": False},
        "reject_reason": "unrelated_to_user_request",
    }

    assert validate_agent_session_result(row, result)["valid"] is True

    result.pop("reject_reason")
    validation = validate_agent_session_result(row, result)
    assert validation["valid"] is False
    assert validation["errors"] == ["reject_reason is required when is_evaluable=false"]


def test_subagent_error_excerpt_keeps_actionable_tail() -> None:
    noisy = "startup noise\n" * 500
    noisy += "gateway connect failed: scope upgrade pending approval requestId=req-1\n"

    excerpt = _diagnostic_excerpt(noisy, 300)

    assert "scope upgrade pending approval" in excerpt
    assert "requestId=req-1" in excerpt


def test_api_judge_uses_one_raw_fallback_for_unknown_session_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_path = tmp_path / "unknown.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "id": "unknown", "timestamp": "2026-08-26T00:00:00Z"}, ensure_ascii=False),
                json.dumps({
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "请完成语雀周报"}],
                    },
                }, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runtime = JudgeRuntimeConfig(
        backend="api",
        api=LlmRuntimeConfig(
            api_key="test-secret-key",
            base_url="https://example.invalid/v1",
            model="test-model",
        ),
    )
    analyzer = OcsaSessionReportAnalyzer(
        OcsaSessionReportJudgeConfig(
            runtime=runtime,
            preference=CasePreference(raw_message="关注语雀周报失败"),
        )
    )
    analyzer.evaluator.evaluate = lambda _payload: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("OCSA evaluator must not start for an invalid payload")
    )
    calls: list[str] = []

    def fake_chat_json(**kwargs: object) -> dict[str, object]:
        calls.append(str(kwargs["user"]))
        return {
            "schema_version": "clawevolve-diagnose-native-session-output.v1",
            "session_id": "unknown",
            "is_evaluable": True,
            "case_type": "bad",
            "query": "请完成语雀周报",
            "intent_match": {"is_match": True},
            "root_cause_class": "CAPABILITY_BOUNDARY",
            "evidence": [{"source": "session", "snippet": "任务没有完成"}],
        }

    monkeypatch.setattr(
        "clawevolve_diagnose.judge.ocsa_session_report_analyzer.chat_json",
        fake_chat_json,
    )
    row = SessionRow(
        session_id="unknown",
        path=str(session_path),
        bot_id="bot",
        created_at="2026-08-26T00:00:00Z",
        first_question="",
        user_text="",
        assistant_text="",
        tool_text="",
        raw_text="",
    )

    diagnoses = analyzer.analyze([row], "bot")

    assert len(calls) == 1
    assert "请完成语雀周报" in calls[0]
    assert len(diagnoses) == 1
    assert diagnoses[0].query == "请完成语雀周报"


def test_api_judge_uses_raw_fallback_when_adapter_payload_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_path = tmp_path / "empty-adapter.jsonl"
    session_path.write_text('{"future_format":true}\n', encoding="utf-8")
    runtime = JudgeRuntimeConfig(
        backend="api",
        api=LlmRuntimeConfig(
            api_key="test-secret-key",
            base_url="https://example.invalid/v1",
            model="test-model",
        ),
    )
    analyzer = OcsaSessionReportAnalyzer(OcsaSessionReportJudgeConfig(runtime=runtime))
    row = SessionRow(
        session_id="empty-adapter",
        path=str(session_path),
        bot_id="bot",
        created_at="2026-08-26T00:00:00Z",
        first_question="",
        user_text="",
        assistant_text="",
        tool_text="",
        raw_text="",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "clawevolve_diagnose.judge.ocsa_session_report_analyzer.load_session_payload",
        lambda _row: {},
    )
    monkeypatch.setattr(
        analyzer,
        "_analyze_raw_session",
        lambda value: calls.append(value.session_id),
    )

    assert analyzer.analyze([row], "bot") == []
    assert calls == ["empty-adapter"]


def test_api_raw_session_content_is_redacted_and_bounded(tmp_path: Path) -> None:
    secret = "fixture-sensitive-value"
    session_path = tmp_path / "large.jsonl"
    session_path.write_text(
        json.dumps({"type": "message", "token": secret, "content": "x" * 5000})
        + "\n",
        encoding="utf-8",
    )
    row = SessionRow(
        session_id="large",
        path=str(session_path),
        bot_id="bot",
        created_at="2026-08-26T00:00:00Z",
        first_question="",
        user_text="",
        assistant_text="",
        tool_text="",
        raw_text="",
    )

    payload = build_native_session_analysis_input(
        row,
        CasePreference(),
        include_session_content=True,
        max_session_content_chars=600,
        session_content_secrets=[secret],
    )
    session = payload["session"]

    assert len(session["content"]) <= 600
    assert secret not in session["content"]
    assert "[REDACTED_SECRET]" in session["content"]
    assert session["content_truncated"] is True
    assert session["content_format"] == "openclaw-session-jsonl.raw-bounded.v1"
