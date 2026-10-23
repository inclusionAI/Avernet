import pytest

from clawevolve_diagnose.judge.query_fidelity import ReplayQueryFidelityPolicy
from clawevolve_diagnose.models import SessionRow
from clawevolve_diagnose.utils import (
    assess_replayability,
    compact_replay_text,
    redact_secrets,
    replayability_issues,
)


def test_slash_command_with_named_id_is_replayable() -> None:
    query = "/data-preprocessing id:111111111310322425"
    assessment = assess_replayability(query)
    assert assessment.replayable
    assert assessment.query_type == "slash_command"
    assert assessment.action == "/data-preprocessing"
    assert not assessment.hard_failures
    assert not replayability_issues(query)


def test_short_structured_request_is_not_rejected_for_missing_prose_verb() -> None:
    assessment = assess_replayability("fetch_biz_data task_id=abc123")
    assert assessment.replayable
    assert assessment.query_type == "structured_request"
    assert "structured_request_without_natural_language_action" in assessment.warnings


def test_unresolved_context_is_still_a_hard_failure() -> None:
    assert "unresolved_context_reference" in replayability_issues("继续处理这个")


@pytest.mark.parametrize("assignment", [
    "token=example-value",
    "api_key=example-value",
    "DINGTALK_CLIENT_SECRET=example-value",
    "DINGTALK_CLIENT_SECRET=[REDACTED_SECRET]",
    "cookie=example-value",
    "Authorization: Bearer example-value",
])
def test_credential_shaped_text_does_not_reject_case(assignment: str) -> None:
    assessment = assess_replayability(f"请执行服务启动任务，配置 {assignment}")
    assert assessment.replayable
    assert not assessment.hard_failures


def test_credential_redaction_is_preserved() -> None:
    text = "请启动服务 DINGTALK_CLIENT_SECRET=example-value"
    assert redact_secrets(text) == "请启动服务 DINGTALK_CLIENT_SECRET=[REDACTED_SECRET]"


def test_natural_language_warning_does_not_drop_substantive_case() -> None:
    assessment = assess_replayability("处理数据预处理")
    assert assessment.replayable
    assert assessment.warnings
    assert not assessment.hard_failures


def test_contextual_word_with_explicit_file_anchor_is_replayable() -> None:
    assessment = assess_replayability("请处理这个文件 /tmp/input.csv")
    assert assessment.replayable
    assert "context_reference_resolved_by_explicit_anchor" in assessment.warnings
    assert not assessment.hard_failures


@pytest.mark.parametrize("query", [
    "请启动服务 DINGTALK_CLIENT_SECRET=example-value",
    "/start-service token=example-value",
    "请请求服务 Authorization: Bearer example-value",
])
def test_replay_query_preserves_execution_arguments(query: str) -> None:
    assert compact_replay_text(query) == query
    row = SessionRow(
        session_id="replay-arguments",
        path="/tmp/session.jsonl",
        bot_id="bot",
        created_at="2026-10-23T00:00:00+00:00",
        first_question=query,
        user_text=query,
        assistant_text="",
        tool_text="",
        raw_text=query,
    )
    assert ReplayQueryFidelityPolicy().decide(row, query).query == query
