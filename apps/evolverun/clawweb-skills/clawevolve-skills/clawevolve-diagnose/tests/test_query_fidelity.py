from clawevolve_diagnose.judge.query_fidelity import ReplayQueryFidelityPolicy
from clawevolve_diagnose.models import SessionRow


def test_gateway_metadata_is_removed_but_slash_invocation_is_preserved() -> None:
    raw = """Sender (untrusted metadata):
```json
{"label":"gateway-client","id":"gateway-client"}
```

[Thu 2026-08-06 14:16 GMT+8] /data-preprocessing id：111111111310322385"""
    row = SessionRow(
        session_id="s1",
        path="/tmp/s1.jsonl",
        bot_id="bot",
        created_at="2026-08-06T14:16:00+08:00",
        first_question=raw,
        user_text=raw,
        assistant_text="",
        tool_text="",
        raw_text=raw,
    )

    decision = ReplayQueryFidelityPolicy().decide(
        row,
        "执行数据预处理流程并验证语雀知识链接回写。",
    )

    assert decision.query == "/data-preprocessing id：111111111310322385"
    assert decision.overridden is True
    assert "replay_query_source:source_slash_command" in decision.notes


def test_natural_language_source_keeps_generated_replay_query() -> None:
    row = SessionRow(
        session_id="s2",
        path="/tmp/s2.jsonl",
        bot_id="bot",
        created_at="2026-08-06T14:16:00+08:00",
        first_question="请检查语雀知识链接是否正确回写",
        user_text="请检查语雀知识链接是否正确回写",
        assistant_text="",
        tool_text="",
        raw_text="请检查语雀知识链接是否正确回写",
    )

    decision = ReplayQueryFidelityPolicy().decide(
        row,
        "检查语雀知识链接是否正确回写到本地数据文件。",
    )

    assert decision.query == "检查语雀知识链接是否正确回写到本地数据文件"
    assert not decision.source_invocation
    assert not decision.notes
