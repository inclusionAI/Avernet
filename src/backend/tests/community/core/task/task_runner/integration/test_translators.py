from agentclaw.community.core.task.task_runner.integration.translators import (
    BcsSessionTranslator, BcsStateMachineRunTranslator, SingleBotRunTranslator,
)


def test_single_bot_completed():
    d = SingleBotRunTranslator.adapt({"status": "COMPLETED", "result": {"content": "行业全貌"}}, "t1::c1")
    assert d.loop_task_id == "t1::c1"
    assert d.result["success"] is True
    assert d.result["data"] == "行业全貌"
    assert "fail_detail" not in d.result


def test_single_bot_failed_with_error():
    # run FAILED = 执行报错(非验收)→ exec_error(→ harness 重投)
    d = SingleBotRunTranslator.adapt({"status": "FAILED", "error": "boom"}, "t1::c1")
    assert d.result["success"] is False
    assert d.result["exec_error"] == "boom"


def test_single_bot_status_case_insensitive():
    d = SingleBotRunTranslator.adapt({"status": "completed"}, "t1::c1")
    assert d.result["success"] is True


def test_single_bot_timeout_mapped():
    d = SingleBotRunTranslator.adapt({"status": "FAILED", "error": "TIME_OUT"}, "t1::c1")
    assert d.result["exec_error"] == "timeout"


def test_bcs_session_completed():
    group = {"session": {"status": "completed", "output": {"r": 1}, "error_message": None}}
    d = BcsSessionTranslator.adapt(group, [], "t1::g1")
    assert d.result["success"] is True
    assert d.result["data"] == {"r": 1}


def test_bcs_session_failed():
    group = {"session": {"status": "failed", "output": None, "error_message": "err"}}
    d = BcsSessionTranslator.adapt(group, [], "t1::g1")
    assert d.result["success"] is False
    assert d.result["exec_error"] == "err"


def test_bcs_session_output_fallback_to_last_assistant_msg():
    group = {"session": {"status": "completed", "output": None, "error_message": None}}
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "ans"}]
    d = BcsSessionTranslator.adapt(group, msgs, "t1::g1")
    assert d.result["data"] == "ans"


def test_state_machine_completed():
    d = BcsStateMachineRunTranslator.adapt({"status": "completed", "output": {"x": 1}, "error": None}, "t1::g1")
    assert d.result["success"] is True
    assert d.result["data"] == {"x": 1}


def test_state_machine_aborted():
    d = BcsStateMachineRunTranslator.adapt({"status": "aborted"}, "t1::g1")
    assert d.result["success"] is False
    assert d.result["exec_error"] == "aborted"


def test_state_machine_failed_with_error():
    d = BcsStateMachineRunTranslator.adapt({"status": "failed", "error": "boom"}, "t1::g1")
    assert d.result["success"] is False
    assert d.result["exec_error"] == "boom"
