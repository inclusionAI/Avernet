import asyncio
import threading
import time

from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
    SingleboxEngineAdapter,
    _extract_final_text,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_worker_collector_has_no_adapter_business_timeout_by_default():
    adapter = SingleboxEngineAdapter(backend_base_url="http://backend", user_id="u1")
    try:
        assert adapter._collect_timeout is None
    finally:
        _run(adapter._aclose())


def test_extract_final_text_joins_all_text_blocks():
    payload = {
        "message": {
            "content": [
                {"type": "tool", "name": "ignored"},
                {"type": "text", "text": '{"success":true,'},
                {"type": "text", "text": '"data":"ok","gaps":[]}'},
            ]
        }
    }
    assert _extract_final_text(payload) == '{"success":true,\n"data":"ok","gaps":[]}'


def test_cancel_run_stops_collector_and_marks_run_cancelled():
    adapter = SingleboxEngineAdapter(backend_base_url="http://backend", user_id="u1")
    started = threading.Event()

    async def _resolved(bot_id):
        return "localhost:20010", "session-1"

    async def _roundtrip(target, session_key, message, timeout):
        started.set()
        await asyncio.sleep(60)
        return {"status": "COMPLETED", "result": {"content": "late"}}

    adapter._resolve_roundtrip_inputs = _resolved
    adapter._ws_chat_roundtrip = _roundtrip
    try:
        sent = _run(adapter.send_message(bot_id="bot1", message="do", metadata={}))
        run_id = sent.run_id
        assert sent.session_id == "session-1"
        deadline = time.monotonic() + 2
        while not started.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.is_set()

        _run(adapter.cancel_run(run_id))
        run = _run(adapter.get_run(run_id))
        assert run == {"status": "FAILED", "error": "cancelled"}
        assert run_id not in adapter._collectors
    finally:
        _run(adapter._aclose())
