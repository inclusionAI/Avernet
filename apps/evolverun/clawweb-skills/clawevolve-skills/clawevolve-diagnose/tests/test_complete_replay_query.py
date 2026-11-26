"""Judge replay input must survive Diagnose, Plan rendering and Bench parsing."""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from clawevolve_diagnose.judge.native_session_analysis import map_native_session_analysis_result
from clawevolve_diagnose.models import SessionRow

SKILLS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS_ROOT / "clawevolve-plan"))
from clawevolve_plan.bench.template_builder import _render_case_markdown

spec = importlib.util.spec_from_file_location(
    "bench_complete_query_test", SKILLS_ROOT / "clawbench-base/scripts/lib_tasks.py"
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def map_query(query, case_type="bad"):
    row = SessionRow(
        session_id="review", path="/tmp/review.jsonl", bot_id="bot",
        created_at="2026-11-10", first_question="请评审业务文档",
        user_text="请评审业务文档", assistant_text="", tool_text="", raw_text="",
    )
    return map_native_session_analysis_result(
        row,
        {
            "schema_version": "clawevolve-diagnose-native-session-output.v1",
            "is_evaluable": True, "case_type": case_type, "query": query,
            "root_cause_class": "COMPLETED" if case_type == "good" else "TOOL_FAILURE",
        },
        source_note="test",
    )


@pytest.mark.parametrize("case_type", ["bad", "good"])
@pytest.mark.parametrize("context", ["", "补充业务上下文。\n" * 150])
def test_complete_query_reaches_bench_prompt(tmp_path, case_type, context):
    # Reproduce the archived cases' metadata + request + review URL structure,
    # using a public example URL without real identifiers or credentials.
    query = (
        "# 当前步骤信息\n- 主任务ID：123\n- 步骤描述：采用客权评审技能进行评审\n\n"
        "# 任务内容\n使用相关技能进行客权评审。\n\n"
        + context + "## 任务输入信息\n"
        "评审：https://example.org/review/join?source=doc;mode=review&item=1#details\n"
        "必须等待用户确认后才能上报。"
    )
    diagnosis = map_query(query, case_type)
    assert diagnosis is not None
    assert diagnosis.query == query
    case = {"case_id": "task_review", "query": diagnosis.query, "case_type": case_type}
    markdown = _render_case_markdown({}, case, "task_review", "train", "")
    task_file = tmp_path / "task_review.md"
    task_file.write_text(markdown, encoding="utf-8")
    task = runtime.TaskLoader(tmp_path).load_task(task_file)
    assert task.prompt == re.sub(r"(?m)^##(?=[ \t]+\S)", "###", query)
    assert case["query"] == query
    assert task.expected_behavior
    assert task.llm_judge_rubric


def test_short_query_keeps_punctuation():
    query = "请检查评审结果，并等待用户确认。"
    assert map_query(query).query == query


@pytest.mark.parametrize("query", [None, "", " \n\t"])
def test_empty_query_is_still_rejected(query):
    assert map_query(query) is None
