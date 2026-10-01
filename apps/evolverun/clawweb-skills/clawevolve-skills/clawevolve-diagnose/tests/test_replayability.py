from clawevolve_diagnose.utils import assess_replayability, replayability_issues


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


def test_unresolved_context_and_secret_are_hard_failures() -> None:
    assert "unresolved_context_reference" in replayability_issues("继续处理这个")
    assert "contains_secret_like_material" in replayability_issues("执行任务 token=abc123")


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
