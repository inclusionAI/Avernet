from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clawevolve_diagnose._ocsa_session_report.evaluators.session_report import evaluator as evaluator_module
from clawevolve_diagnose._ocsa_session_report.evaluators.session_report.evaluator import SessionReportEvaluator
from clawevolve_diagnose.judge.keyless_session_prompt import (
    build_session_analysis_prompt,
    session_analysis_prompt_prefix,
)
from clawevolve_diagnose.judge.local_session_judge_provider import _rows_debug
from clawevolve_diagnose.models import SessionRow
from clawevolve_diagnose.run.runner import _apply_session_analysis_limit
from clawevolve_diagnose.run.summary import _local_session_scan_limit


def test_session_analysis_prompt_renders_json_contract() -> None:
    prompt = build_session_analysis_prompt({"session": {"path": "/tmp/example.jsonl"}})

    assert '"intent_match": {"is_match": true' in prompt
    assert "/tmp/example.jsonl" in prompt
    assert "{{INPUT_JSON}}" not in prompt
    assert session_analysis_prompt_prefix()


def test_default_session_budget_retains_only_ten_post_filter_rows(tmp_path: Any) -> None:
    from clawevolve_diagnose.models import CasePreference, RunRequest

    request = RunRequest(api_key="", message="diagnose", output_dir=tmp_path, task_id="EV-1")
    preference = CasePreference()

    _apply_session_analysis_limit(request, preference)

    assert preference.max_sessions == 10
    assert _local_session_scan_limit(request, preference) == 10


def test_explicit_session_budget_remains_configurable(tmp_path: Any) -> None:
    from clawevolve_diagnose.models import CasePreference, RunRequest

    request = RunRequest(
        api_key="", message="diagnose", output_dir=tmp_path, task_id="EV-1", max_sessions=6
    )
    preference = CasePreference(max_sessions=20)

    _apply_session_analysis_limit(request, preference)

    assert preference.max_sessions == 6
    assert _local_session_scan_limit(request, preference) == 6


def test_rows_debug_returns_bounded_non_secret_metadata() -> None:
    rows = [
        SessionRow(
            session_id=f"session-{index}",
            path=f"/tmp/session-{index}.jsonl",
            bot_id="bot",
            created_at="2026-08-13T10:00:00+08:00",
            first_question="x" * 200,
            user_text="",
            assistant_text="",
            tool_text="",
            raw_text="",
        )
        for index in range(10)
    ]

    result = _rows_debug(rows)

    assert len(result) == 9
    assert result[-1] == {"remaining_rows_not_logged": 2}
    assert result[0]["session_id"] == "session-0"
    assert result[0]["first_question_preview"].endswith("...<truncated 40 chars>")
    assert "raw_text" not in result[0]


def test_task_boundary_llm_call_includes_session_id(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_call(
        user_prompt: str,
        system_prompt: str,
        timeout: int,
        call_name: str,
    ) -> tuple[dict[str, Any], str]:
        captured.update(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            timeout=timeout,
            call_name=call_name,
        )
        return {
            "tasks": [
                {
                    "start_at_user_message": 1,
                    "task_description": "first task",
                }
            ]
        }, "test-model"

    monkeypatch.setattr(evaluator_module, "_call_judge_llm_internal", fake_call)
    evaluator = SessionReportEvaluator(timeout=17)

    tasks, model = evaluator._identify_task_boundaries(
        "user: test",
        session_id="session-123",
    )

    assert captured["call_name"] == "task_split:session-123"
    assert captured["timeout"] == 17
    assert tasks[0]["task_index"] == 1
    assert model == "test-model"


def test_local_judge_provider_defaults_to_four_api_workers() -> None:
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        DEFAULT_API_JUDGE_MAX_CONCURRENT_TASKS,
        DEFAULT_JUDGE_SESSION_BATCH_SIZE,
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import JudgeRuntimeConfig, LlmRuntimeConfig

    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )

    assert DEFAULT_API_JUDGE_MAX_CONCURRENT_TASKS == 8
    assert DEFAULT_JUDGE_SESSION_BATCH_SIZE == 8
    assert provider.max_concurrent_tasks == 8
    assert provider.session_batch_size == 8
    assert provider.judge_limit == 8


def test_local_judge_provider_runs_eight_api_sessions_concurrently(
    monkeypatch: Any,
) -> None:
    import threading

    from clawevolve_diagnose.judge import local_session_judge_provider as provider_module
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import (
        CasePreference,
        JudgeRuntimeConfig,
        LlmRuntimeConfig,
    )

    rows = [
        SessionRow(
            session_id=f"session-{index}",
            path=f"/tmp/session-{index}.jsonl",
            bot_id="bot",
            created_at=f"2026-08-13T10:00:0{index}+08:00",
            first_question=f"诊断任务 {index}",
            user_text=f"诊断任务 {index}",
            assistant_text="",
            tool_text="",
            raw_text=f"诊断任务 {index}",
        )
        for index in range(8)
    ]
    barrier = threading.Barrier(8)
    lock = threading.Lock()
    active = 0
    max_active = 0
    analyzed_session_ids: list[str] = []

    class FakeAnalyzer:
        def analyze(
            self, batch: list[SessionRow], bot_id: str = ""
        ) -> list[Any]:
            nonlocal active, max_active
            assert len(batch) == 1
            assert bot_id == "bot"
            with lock:
                active += 1
                max_active = max(max_active, active)
                analyzed_session_ids.append(batch[0].session_id)
            try:
                barrier.wait(timeout=2)
            finally:
                with lock:
                    active -= 1
            return []

    monkeypatch.setattr(
        provider_module,
        "create_session_analyzer",
        lambda *args, **kwargs: FakeAnalyzer(),
    )
    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )

    result = provider.analyze_until_selectable(
        rows,
        "bot",
        CasePreference(case_limit=8, include_good=False),
    )

    assert max_active == 8
    assert set(analyzed_session_ids) == {row.session_id for row in rows}
    assert result.stats["judged_session_count"] == 8
    assert result.stats["session_batch_size"] == 8
    assert result.stats["max_concurrent_tasks"] == 8
    assert result.stats["judge_thread_count"] == 8


def test_local_judge_provider_keeps_subagent_sequential() -> None:
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import JudgeRuntimeConfig

    provider = LocalSessionJudgeProvider(JudgeRuntimeConfig(backend="subagent"))

    assert provider.max_concurrent_tasks == 1
    assert provider.session_batch_size == 8


def test_local_judge_provider_executes_one_batch_without_missing_helpers(
    monkeypatch: Any,
) -> None:
    from clawevolve_diagnose.judge import local_session_judge_provider as provider_module
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import (
        CasePreference,
        Diagnosis,
        JudgeRuntimeConfig,
        LlmRuntimeConfig,
    )

    row = SessionRow(
        session_id="session-main-path",
        path="/tmp/session-main-path.jsonl",
        bot_id="bot",
        created_at="2026-08-13T10:00:00+08:00",
        first_question="完成数据预处理",
        user_text="完成数据预处理",
        assistant_text="已完成",
        tool_text="fetch_biz_data",
        raw_text="完成数据预处理 已完成",
    )
    diagnosis = Diagnosis(
        session=row,
        case_type="good",
        symptom_class="COMPLETED",
        root_cause_class="COMPLETED",
        common_problem_key="completed",
        evolution_failure_mode="completed",
        query="完成数据预处理",
        root_cause_summary="任务已完成",
        quality_score=0.9,
        confidence=0.9,
    )

    class FakeAnalyzer:
        def analyze(
            self, rows: list[SessionRow], bot_id: str = ""
        ) -> list[Diagnosis]:
            assert rows == [row]
            assert bot_id == "bot"
            return [diagnosis]

    monkeypatch.setattr(
        provider_module,
        "create_session_analyzer",
        lambda *args, **kwargs: FakeAnalyzer(),
    )
    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )
    preference = CasePreference(
        raw_message="分析语雀 MCP 问题并输出 1 个 good case",
        requires_broad_recall=True,
        case_limit=1,
        include_good=True,
        good_case_count=1,
        bad_case_count=0,
    )

    result = provider.analyze_until_selectable([row], "bot", preference)

    assert result.diagnoses == [diagnosis]
    assert result.stats["judged_session_count"] == 1
    assert result.stats["judge_stop_reason"] == "selection_satisfied"
    assert not result.warnings


def test_remaining_judge_limit_shrinks_to_unresolved_quota() -> None:
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        _remaining_judge_limit,
    )

    assert _remaining_judge_limit(
        {
            "requested_count": 5,
            "selected_count": 0,
            "required_good": 0,
            "selected_good": 0,
            "required_bad": 5,
            "selected_bad": 0,
        },
        4,
    ) == 4
    assert _remaining_judge_limit(
        {
            "requested_count": 5,
            "selected_count": 4,
            "required_good": 0,
            "selected_good": 0,
            "required_bad": 5,
            "selected_bad": 4,
        },
        4,
    ) == 1
    assert _remaining_judge_limit(
        {
            "requested_count": 5,
            "selected_count": 5,
            "required_good": 1,
            "selected_good": 0,
            "required_bad": 4,
            "selected_bad": 5,
        },
        4,
    ) == 1


def test_dynamic_small_batches_do_not_skip_unselected_sessions(
    monkeypatch: Any,
) -> None:
    from clawevolve_diagnose.judge import local_session_judge_provider as provider_module
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import (
        CasePreference,
        JudgeRuntimeConfig,
        LlmRuntimeConfig,
    )

    rows = [
        SessionRow(
            session_id=f"session-small-batch-{index}",
            path=f"/tmp/session-small-batch-{index}.jsonl",
            bot_id="bot",
            created_at=f"2026-08-13T10:00:0{index}+08:00",
            first_question=question,
            user_text=question,
            assistant_text="",
            tool_text="",
            raw_text=question,
        )
        for index, question in enumerate(
            ["生成季度财务报表", "排查支付链路超时", "查询语雀知识库权限"]
        )
    ]
    analyzed: list[str] = []

    class EmptyAnalyzer:
        def analyze(
            self, batch: list[SessionRow], bot_id: str = ""
        ) -> list[Any]:
            analyzed.extend(row.session_id for row in batch)
            return []

    monkeypatch.setattr(
        provider_module,
        "create_session_analyzer",
        lambda *args, **kwargs: EmptyAnalyzer(),
    )
    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )

    result = provider.analyze_until_selectable(
        rows,
        "bot",
        CasePreference(case_limit=1, include_good=False),
    )

    assert set(analyzed) == {row.session_id for row in rows}
    assert result.stats["judged_session_count"] == 3
    assert [batch["raw_batch_size"] for batch in result.stats["batch_stats"]] == [
        1,
        1,
        1,
    ]


def test_local_judge_provider_handles_empty_session_scope() -> None:
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import (
        CasePreference,
        JudgeRuntimeConfig,
        LlmRuntimeConfig,
    )

    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )

    result = provider.analyze_until_selectable([], "bot", CasePreference())

    assert result.diagnoses == []
    assert result.stats["no_sessions"] is True
    assert result.warnings
    assert "No local sessions were discovered" in result.warnings[0]


def test_local_judge_provider_cleanup_failure_does_not_override_result() -> None:
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import JudgeRuntimeConfig, LlmRuntimeConfig

    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )

    class Analyzer:
        def close(self) -> None:
            raise RuntimeError("agent already absent")

    provider._active_analyzer = Analyzer()
    provider._close_active_analyzer()

    assert provider._active_analyzer is None


def test_local_judge_provider_redacts_api_key_on_analyzer_init_failure(
    monkeypatch: Any,
) -> None:
    from clawevolve_diagnose.judge import local_session_judge_provider as provider_module
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import (
        CasePreference,
        JudgeRuntimeConfig,
        LlmRuntimeConfig,
    )

    secret = "secret-test-api-key"
    row = SessionRow(
        session_id="session-init-failure",
        path="/tmp/session-init-failure.jsonl",
        bot_id="bot",
        created_at="2026-08-13T10:00:00+08:00",
        first_question="诊断任务",
        user_text="诊断任务",
        assistant_text="",
        tool_text="",
        raw_text="诊断任务",
    )

    def fail_to_create(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"authentication failed for {secret}")

    monkeypatch.setattr(
        provider_module,
        "create_session_analyzer",
        fail_to_create,
    )
    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key=secret,
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )

    result = provider.analyze_until_selectable([row], "bot", CasePreference())

    assert result.diagnoses == []
    assert result.stats["judge_unavailable"] is True
    assert secret not in result.stats["error"]
    assert all(secret not in warning for warning in result.warnings)


def test_analyzer_factory_uses_ocsa_session_report() -> None:
    from clawevolve_diagnose.judge.analyzer_factory import create_session_analyzer
    from clawevolve_diagnose.judge.ocsa_session_report_analyzer import (
        OcsaSessionReportAnalyzer,
    )
    from clawevolve_diagnose.models import (
        CasePreference,
        JudgeRuntimeConfig,
        LlmRuntimeConfig,
    )

    analyzer = create_session_analyzer(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        ),
        CasePreference(raw_message="诊断失败会话"),
        max_concurrent_tasks=2,
    )

    assert isinstance(analyzer, OcsaSessionReportAnalyzer)
    assert analyzer.config.max_concurrent_tasks == 2


def test_agent_session_discovery_builds_locator_without_parsing_content(
    monkeypatch: Any, tmp_path: Path
) -> None:
    from clawevolve_diagnose.acquisition import sessions as sessions_module

    session_file = tmp_path / "raw-unknown.jsonl"
    session_file.write_text('{"unsupported":"new-shape"}\n', encoding="utf-8")
    (tmp_path / "sessions.json").write_text(
        json.dumps({
            "agent:main:explicit:raw-unknown": {
                "sessionId": "raw-unknown",
                "sessionFile": str(session_file),
                "sessionStartedAt": "2026-08-26T00:00:00Z",
                "chatType": "direct",
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sessions_module,
        "_parse_jsonl_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Agent Judge acquisition must not parse content")
        ),
    )

    rows = sessions_module.discover_sessions(
        {"session_dirs": [str(tmp_path)]},
        max_sessions=5,
        parse_content=False,
    )

    assert [row.session_id for row in rows] == ["raw-unknown"]
    assert rows[0].path == str(session_file)
    assert rows[0].first_question == ""
    assert rows[0].raw_session == {}


def test_local_judge_excludes_analysis_failures_from_eval_candidates(
    monkeypatch: Any,
) -> None:
    from clawevolve_diagnose.judge import local_session_judge_provider as provider_module
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import (
        CasePreference,
        Diagnosis,
        JudgeRuntimeConfig,
        LlmRuntimeConfig,
    )

    row = SessionRow(
        session_id="session-ocsa-failure",
        path="/tmp/session-ocsa-failure.jsonl",
        bot_id="bot",
        created_at="2026-08-13T10:00:00+08:00",
        first_question="分析数据任务",
        user_text="分析数据任务",
        assistant_text="",
        tool_text="",
        raw_text="分析数据任务",
    )
    error_diagnosis = Diagnosis(
        session=row,
        case_type="bad",
        symptom_class="OCSA_SESSION_REPORT_JUDGE_ERROR",
        root_cause_class="UNKNOWN",
        common_problem_key="unknown",
        evolution_failure_mode="unknown",
        query="分析数据任务",
        root_cause_summary="OCSA 上游调用失败",
    )

    class FailedAnalyzer:
        def analyze(
            self, rows: list[SessionRow], bot_id: str = ""
        ) -> list[Diagnosis]:
            return [error_diagnosis]

    monkeypatch.setattr(
        provider_module,
        "create_session_analyzer",
        lambda *args, **kwargs: FailedAnalyzer(),
    )
    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )

    result = provider.analyze_until_selectable(
        [row],
        "bot",
        CasePreference(case_limit=1, include_good=False),
    )

    assert result.diagnoses == []
    assert result.stats["failed_session_count"] == 1
    assert result.stats["final_diagnosis_count"] == 0
    assert result.stats["batch_stats"][0]["judge_failed_session_count"] == 1


def test_local_judge_keeps_equivalent_ocsa_outputs_from_different_sessions(
    monkeypatch: Any,
) -> None:
    from clawevolve_diagnose.judge import local_session_judge_provider as provider_module
    from clawevolve_diagnose.judge.local_session_judge_provider import (
        LocalSessionJudgeProvider,
    )
    from clawevolve_diagnose.models import (
        CasePreference,
        Diagnosis,
        JudgeRuntimeConfig,
        LlmRuntimeConfig,
    )

    rows = [
        SessionRow(
            session_id=f"same-task-session-{index}",
            path=f"/tmp/same-task-session-{index}.jsonl",
            bot_id="bot",
            created_at=f"2026-08-13T10:00:0{index}+08:00",
            first_question="执行同一个数据处理任务",
            user_text="执行同一个数据处理任务",
            assistant_text="工具执行失败",
            tool_text="same_tool",
            raw_text="执行同一个数据处理任务 工具执行失败",
        )
        for index in range(2)
    ]

    class FakeAnalyzer:
        def analyze(
            self, batch: list[SessionRow], bot_id: str = ""
        ) -> list[Diagnosis]:
            assert bot_id == "bot"
            return [
                Diagnosis(
                    session=row,
                    case_type="bad",
                    symptom_class="TOOL_FAILURE",
                    root_cause_class="TOOL_FAILURE",
                    common_problem_key="tool_failure",
                    evolution_failure_mode="TOOL_FAILURE",
                    query="执行同一个数据处理任务",
                    root_cause_summary="同一种工具失败。",
                    quality_score=0.9,
                    confidence=0.9,
                )
                for row in batch
            ]

    monkeypatch.setattr(
        provider_module,
        "create_session_analyzer",
        lambda *args, **kwargs: FakeAnalyzer(),
    )
    provider = LocalSessionJudgeProvider(
        JudgeRuntimeConfig(
            backend="api",
            api=LlmRuntimeConfig(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
        )
    )

    result = provider.analyze_until_selectable(
        rows,
        "bot",
        CasePreference(
            case_limit=2,
            include_good=False,
            bad_case_count=2,
            good_case_count=0,
        ),
    )

    assert len(result.diagnoses) == 2
    assert {case.session.session_id for case in result.diagnoses} == {
        row.session_id for row in rows
    }
    assert result.stats["dedupe"]["scope"] == "input_session_identity_only"
    assert result.stats["dedupe"]["post_ocsa_dedupe_enabled"] is False
    assert result.stats["batch_stats"][0]["judge_accepted_diagnosis_count"] == 2
