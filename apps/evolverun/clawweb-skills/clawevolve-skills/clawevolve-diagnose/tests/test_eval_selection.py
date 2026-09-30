from clawevolve_diagnose.judge.ocsa_session_report_analyzer import map_session_report_result
from clawevolve_diagnose.models import CasePreference, Diagnosis, SessionRow
from clawevolve_diagnose.selection import CandidatePoolBuilder, CaseTypeQuota, SelectionRequest, TimeWindow


COMMAND = "/data-preprocessing id:111111111310322425"


def _row() -> SessionRow:
    return SessionRow(
        session_id="183c9029",
        path="/tmp/session.jsonl",
        bot_id="bot",
        created_at="2026-08-13T10:00:00+08:00",
        first_question=COMMAND,
        user_text=COMMAND,
        assistant_text="已完成数据预处理。",
        tool_text="fetch_biz_data",
        raw_text=COMMAND,
    )


def test_completed_command_session_is_mapped_to_good_case() -> None:
    result = map_session_report_result(
        _row(),
        {
            "judge_report": {
                "tasks": [
                    {
                        "task_index": 0,
                        "task_description": COMMAND,
                        "is_complete": True,
                        "task_failure_class": "COMPLETED",
                        "reasoning": "任务完成",
                        "skills": [],
                        "mcps": [],
                    }
                ]
            }
        },
    )
    assert len(result) == 1
    assert result[0].case_type == "good"
    assert result[0].query == COMMAND


def test_good_case_with_replayability_warning_is_qualified() -> None:
    diagnosis = Diagnosis(
        session=_row(),
        case_type="good",
        symptom_class="COMPLETED",
        root_cause_class="COMPLETED",
        common_problem_key="good_regression",
        evolution_failure_mode="good_regression",
        query="处理数据预处理",
        root_cause_summary="完成",
        quality_score=0.7,
    )
    request = SelectionRequest(
        preference=CasePreference(case_limit=1),
        quota=CaseTypeQuota(good=1),
        time_window=TimeWindow(),
    )
    pool = CandidatePoolBuilder(request).build([diagnosis])
    assert len(pool.qualified) == 1
    assert pool.qualified[0].session.session_id == diagnosis.session.session_id
    assert any(note.startswith("replayability_warning:") for note in diagnosis.quality_notes)


def test_llm_rewrite_cannot_replace_source_slash_command(monkeypatch) -> None:
    from clawevolve_diagnose.judge import ocsa_session_report_analyzer as analyzer
    from clawevolve_diagnose.judge.query_rewriter import EvalQueryRewriteResult
    from clawevolve_diagnose.models import LlmRuntimeConfig

    monkeypatch.setattr(
        analyzer,
        "rewrite_eval_query_with_llm",
        lambda task, row, runtime: EvalQueryRewriteResult(
            query="执行数据预处理流程，并验证知识文档状态是否回写。"
        ),
    )
    query, original_query, notes = analyzer.build_replayable_case_query(
        {"task_description": "验证数据预处理步骤25"},
        _row(),
        runtime=LlmRuntimeConfig(
            api_key="test-key", base_url="https://example.invalid/v1", model="test-model"
        ),
    )

    assert query == COMMAND
    assert original_query == COMMAND
    assert "replay_query_source:source_slash_command" in notes


def test_native_result_mapper_restores_source_slash_command() -> None:
    from clawevolve_diagnose.judge.native_session_analysis import (
        map_native_session_analysis_result,
    )

    diagnosis = map_native_session_analysis_result(
        _row(),
        {
            "schema_version": "clawevolve-diagnose-native-session-output.v1",
            "is_evaluable": True,
            "case_type": "bad",
            "query": "执行数据预处理流程，并验证知识链接是否回写。",
            "root_cause_class": "TOOL_FAILURE",
            "evolution_failure_mode": "tool_execution_failure",
            "root_cause_summary": "语雀 MCP 调用失败。",
            "evidence": [
                {"source": "session", "path": _row().path, "snippet": "MCP error"}
            ],
        },
        source_note="test",
    )

    assert diagnosis is not None
    assert diagnosis.query == COMMAND
    assert diagnosis.original_query == COMMAND
    assert "replay_query_source:source_slash_command" in diagnosis.quality_notes


def test_ocsa_task_label_is_preserved_without_taxonomy_translation() -> None:
    report = {
        "judge_report": {
            "tasks": [
                {
                    "task_index": 0,
                    "task_description": "打开知识库文档",
                    "is_complete": 0,
                    "task_failure_class": "RETRIEVAL_NOT_CALLED",
                    "reasoning": "首次未调用正确的检索能力。",
                    "skills": [],
                    "mcps": [],
                }
            ]
        }
    }

    result = map_session_report_result(_row(), report)

    assert len(result) == 1
    diagnosis = result[0]
    assert diagnosis.case_type == "bad"
    assert diagnosis.root_cause_class == "RETRIEVAL_NOT_CALLED"
    assert diagnosis.evolution_failure_mode == "RETRIEVAL_NOT_CALLED"
    assert diagnosis.ocsa["task_failure_class"] == "RETRIEVAL_NOT_CALLED"
    assert diagnosis.ocsa["session_report"] is report


def test_completed_task_with_ocsa_tool_failure_remains_bad() -> None:
    result = map_session_report_result(
        _row(),
        {
            "judge_report": {
                "tasks": [
                    {
                        "task_index": 0,
                        "task_description": COMMAND,
                        "is_complete": 1,
                        "task_failure_class": "COMPLETED",
                        "reasoning": "任务最终完成，但首次工具选择错误。",
                        "skills": [],
                        "mcps": [
                            {
                                "name": "wrong_retriever",
                                "is_correct": 0,
                                "execution": {
                                    "status": "failure",
                                    "failure_category": "WRONG_TOOL_SELECTION",
                                },
                            }
                        ],
                    }
                ]
            }
        },
    )

    assert len(result) == 1
    diagnosis = result[0]
    assert diagnosis.case_type == "bad"
    assert diagnosis.root_cause_class == "WRONG_TOOL_SELECTION"
    assert diagnosis.evolution_failure_mode == "WRONG_TOOL_SELECTION"
    assert diagnosis.ocsa["is_complete"] == 1
    assert diagnosis.ocsa["error_labels"] == ["WRONG_TOOL_SELECTION"]


def test_completed_task_without_ocsa_errors_remains_good() -> None:
    result = map_session_report_result(
        _row(),
        {
            "judge_report": {
                "tasks": [
                    {
                        "task_index": 0,
                        "task_description": COMMAND,
                        "is_complete": 1,
                        "task_failure_class": "COMPLETED",
                        "reasoning": "任务一次完成。",
                        "skills": [],
                        "mcps": [],
                    }
                ]
            }
        },
    )

    assert len(result) == 1
    assert result[0].case_type == "good"
    assert result[0].root_cause_class == "COMPLETED"


def _bad_diagnosis(row: SessionRow, query: str, task_index: int) -> Diagnosis:
    return Diagnosis(
        session=row,
        case_type="bad",
        symptom_class="TOOL_FAILURE",
        root_cause_class="TOOL_FAILURE",
        common_problem_key="tool_failure",
        evolution_failure_mode="TOOL_FAILURE",
        query=query,
        root_cause_summary="工具执行失败。",
        quality_score=0.9,
        confidence=0.9,
        ocsa={"task_index": task_index},
    )


def _select_two_bad_cases(diagnoses: list[Diagnosis]):
    from clawevolve_diagnose.selection import SelectionEngine

    return SelectionEngine(
        SelectionRequest(
            preference=CasePreference(
                case_limit=2,
                include_good=False,
                bad_case_count=2,
                good_case_count=0,
            ),
            quota=CaseTypeQuota(bad=2, good=0),
            time_window=TimeWindow(),
        )
    ).select(diagnoses)


def test_selection_keeps_equivalent_cases_from_different_sessions() -> None:
    first = _row()
    second = SessionRow(
        session_id="183c9030",
        path="/tmp/session-2.jsonl",
        bot_id="bot",
        created_at=first.created_at,
        first_question=first.first_question,
        user_text=first.user_text,
        assistant_text=first.assistant_text,
        tool_text=first.tool_text,
        raw_text=first.raw_text,
    )
    result = _select_two_bad_cases(
        [
            _bad_diagnosis(first, "调用数据预处理工具处理对象 111", 0),
            _bad_diagnosis(second, "调用数据预处理工具处理对象 111", 0),
        ]
    )

    assert len(result.selected) == 2
    assert {case.session.session_id for case in result.selected} == {
        first.session_id,
        second.session_id,
    }
    assert result.report["dedupe"]["enabled"] is False


def test_selection_keeps_multiple_ocsa_cases_from_one_session() -> None:
    row = _row()
    result = _select_two_bad_cases(
        [
            _bad_diagnosis(row, "读取对象 111 的源数据", 0),
            _bad_diagnosis(row, "生成对象 111 的分析报告", 1),
        ]
    )

    assert len(result.selected) == 2
    assert {case.ocsa["task_index"] for case in result.selected} == {0, 1}
    assert len({id(case) for case in result.selected}) == 2
