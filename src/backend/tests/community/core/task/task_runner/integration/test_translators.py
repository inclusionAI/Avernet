from agentclaw.community.core.task.task_runner.client.translators import (
    BcsSessionTranslator, BcsStateMachineRunTranslator, SingleBotRunTranslator,
)


def test_single_bot_completed():
    d = SingleBotRunTranslator.adapt(
        {"status": "COMPLETED", "result": {"content": '{"success":true,"data":"行业全貌","gaps":[]}'}},
        "t1::c1",
    )
    assert d.data["loop_task_id"] == "t1::c1"
    assert d.data["result"]["success"] is True
    assert d.data["result"]["data"] == "行业全貌"
    assert "fail_detail" not in d.data["result"]


def test_single_bot_failed_with_error():
    # run FAILED = 执行报错(非验收)→ exec_error(→ harness 重投)
    d = SingleBotRunTranslator.adapt({"status": "FAILED", "error": "boom"}, "t1::c1")
    assert d.data["result"]["success"] is False
    assert d.data["result"]["exec_error"] == "boom"


def test_single_bot_status_case_insensitive():
    d = SingleBotRunTranslator.adapt(
        {"status": "completed", "result": {"content": {"success": True, "data": "ok", "gaps": []}}},
        "t1::c1",
    )
    assert d.data["result"]["success"] is True


def test_single_bot_timeout_mapped():
    d = SingleBotRunTranslator.adapt({"status": "FAILED", "error": "TIME_OUT"}, "t1::c1")
    assert d.data["result"]["exec_error"] == "timeout"


def test_bcs_session_completed():
    group = {"session": {"status": "completed", "output": {"success": True, "data": {"r": 1}, "gaps": []}, "error_message": None}}
    d = BcsSessionTranslator.adapt(group, [], "t1::g1")
    assert d.data["result"]["success"] is True
    assert d.data["result"]["data"] == {"r": 1}


def test_bcs_session_failed():
    group = {"session": {"status": "failed", "output": None, "error_message": "err"}}
    d = BcsSessionTranslator.adapt(group, [], "t1::g1")
    assert d.data["result"]["success"] is False
    assert d.data["result"]["exec_error"] == "err"


def test_bcs_session_output_fallback_to_last_assistant_msg():
    group = {"session": {"status": "completed", "output": None, "error_message": None}}
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": '{"success":true,"data":"ans","gaps":[]}'}]
    d = BcsSessionTranslator.adapt(group, msgs, "t1::g1")
    assert d.data["result"]["data"] == "ans"


def test_state_machine_completed():
    d = BcsStateMachineRunTranslator.adapt(
        {"status": "completed", "output": {"success": True, "data": {"x": 1}, "gaps": []}, "error": None},
        "t1::g1",
    )
    assert d.data["result"]["success"] is True
    assert d.data["result"]["data"] == {"x": 1}


def test_state_machine_aborted():
    d = BcsStateMachineRunTranslator.adapt({"status": "aborted"}, "t1::g1")
    assert d.data["result"]["success"] is False
    assert d.data["result"]["exec_error"] == "aborted"


def test_state_machine_failed_with_error():
    d = BcsStateMachineRunTranslator.adapt({"status": "failed", "error": "boom"}, "t1::g1")
    assert d.data["result"]["success"] is False
    assert d.data["result"]["exec_error"] == "boom"


def test_completed_plain_text_is_terminal_contract_error():
    d = SingleBotRunTranslator.adapt(
        {"status": "COMPLETED", "result": {"content": "普通报告文本"}}, "t1::c1"
    )
    assert d.data["result"]["success"] is False
    assert d.data["result"]["exec_error"].startswith("terminal_result_invalid")


def test_success_string_is_not_coerced_to_bool():
    d = SingleBotRunTranslator.adapt(
        {"status": "COMPLETED", "result": {"content": '{"success":"false","data":"x"}'}},
        "t1::c1",
    )
    assert d.data["result"]["exec_error"].startswith("terminal_result_invalid")


def test_fail_requires_non_empty_gaps():
    d = SingleBotRunTranslator.adapt(
        {"status": "COMPLETED", "result": {"content": {"success": False, "data": "partial", "gaps": []}}},
        "t1::c1",
    )
    assert d.data["result"]["exec_error"].startswith("terminal_result_invalid")


def test_fail_detail_is_normalized_to_gaps_for_compatibility():
    d = SingleBotRunTranslator.adapt(
        {"status": "COMPLETED", "result": {"content": {"success": False, "data": "partial", "fail_detail": "缺市场数据"}}},
        "t1::c1",
    )
    assert d.data["result"]["success"] is False
    assert d.data["result"]["gaps"] == ["缺市场数据"]


def test_gap_items_must_be_strings():
    d = SingleBotRunTranslator.adapt(
        {"status": "COMPLETED", "result": {"content": {"success": False, "gaps": [123]}}},
        "t1::c1",
    )
    assert d.data["result"]["exec_error"].startswith("terminal_result_invalid")
