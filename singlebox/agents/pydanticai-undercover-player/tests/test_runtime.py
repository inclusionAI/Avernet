from __future__ import annotations

import asyncio

from conftest import send_frame

from pydanticai_player.domain import PlayerAnswer
from pydanticai_player.runtime import Runtime


class ImmediatePlayer:
    async def answer(self, task):
        return PlayerAnswer("天气变化时会带着。", 1, 5, 5)


class Sink(list):
    async def send(self, frame):
        self.append(frame)


async def finish(runtime):
    await asyncio.gather(*(r.work for r in runtime.runs.values() if r.work))


def abort(run_id, session="g:a", request_id="abort"):
    return {
        "type": "req",
        "id": request_id,
        "method": "chat.abort",
        "params": {"run_id": run_id, "session_key": session},
    }


async def test_ack_precedes_one_canonical_final():
    sink = Sink()
    runtime = Runtime(ImmediatePlayer(), sink.send, private_seats={})
    await runtime.dispatch(send_frame())
    await finish(runtime)
    assert sink[0]["payload"]["run_id"] == sink[1]["payload"]["runId"]
    assert sink[1]["payload"]["sessionId"] == "g:a"
    assert sink[1]["payload"]["state"] == "final"
    assert sink[1]["seq"] == sink[1]["payload"]["seq"] == 1
    assert len(sink) == 2


async def test_bcs_delivery_identity_is_retained_and_duplicate_does_not_rerun():
    sink = Sink()
    runtime = Runtime(ImmediatePlayer(), sink.send, private_seats={})
    frame = send_frame()
    await runtime.dispatch(frame)
    await runtime.dispatch(frame)
    await finish(runtime)
    assert list(runtime.runs) == [frame["id"]]
    assert sink[0]["payload"]["run_id"] == frame["id"]
    assert len([f for f in sink if f["type"] == "event"]) == 1
    frame["params"].update(session_key="g:other", bcs_session_id="g:other")
    await runtime.dispatch(frame)
    assert sink[-1]["ok"] is False


async def test_abort_drops_result_even_if_provider_swallows_cancel():
    started = asyncio.Event()

    class LatePlayer:
        async def answer(self, task):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return PlayerAnswer("迟到的结果", 1, 0, 0)

    sink = Sink()
    runtime = Runtime(LatePlayer(), sink.send, private_seats={})
    await runtime.dispatch(send_frame())
    await started.wait()
    run_id = sink[0]["payload"]["run_id"]
    await runtime.dispatch(abort(run_id))
    events = [f["payload"] for f in sink if f["type"] == "event"]
    assert [event["state"] for event in events] == ["aborted"]
    assert "迟到" not in str(sink)
    assert sink[-1]["payload"]["aborted_run_ids"] == [run_id]
    await runtime.dispatch(abort(run_id))
    assert sink[-1]["payload"]["aborted_run_ids"] == []


async def test_abort_cannot_affect_another_session_or_unknown_run():
    sink = Sink()
    runtime = Runtime(ImmediatePlayer(), sink.send, private_seats={})
    await runtime.dispatch(send_frame())
    run_id = sink[0]["payload"]["run_id"]
    await runtime.dispatch(abort(run_id, "g:another"))
    assert sink[-1]["error"]["code"] == "invalid_request"
    await runtime.dispatch(abort("not-real"))
    assert sink[-1]["error"]["code"] == "not_found"
    await finish(runtime)
    assert len([f for f in sink if f.get("payload", {}).get("state") == "final"]) == 1


async def test_missing_canonical_session_is_rejected_before_ack():
    sink = Sink()
    runtime = Runtime(ImmediatePlayer(), sink.send, private_seats={})
    frame = send_frame()
    del frame["params"]["bcs_session_id"]
    await runtime.dispatch(frame)
    assert sink[0]["ok"] is False and not runtime.runs


async def test_model_failure_is_not_published_as_a_fabricated_success():
    class FailingPlayer:
        async def answer(self, task):
            raise RuntimeError("SECRET_KEY_AND_PRIVATE_WORD")

    sink = Sink()
    runtime = Runtime(FailingPlayer(), sink.send, private_seats={})
    await runtime.dispatch(send_frame())
    await finish(runtime)
    assert sink[-1]["payload"]["state"] == "error"
    assert "SECRET" not in str(sink)


async def test_injection_and_history_do_not_expose_private_prompts():
    sink = Sink()
    runtime = Runtime(ImmediatePlayer(), sink.send, private_seats={})
    frame = send_frame()
    frame["method"] = "chat.inject"
    await runtime.dispatch(frame)
    await runtime.dispatch(
        {"type": "req", "id": "hist", "method": "chat.history", "params": {"session_key": "g:a"}}
    )
    assert not runtime.runs and not runtime.private
    assert sink[-1]["payload"]["messages"] == []


async def test_timeout_is_a_single_error_terminal():
    class Slow:
        async def answer(self, task):
            await asyncio.Event().wait()

    sink = Sink()
    runtime = Runtime(Slow(), sink.send, private_seats={})
    await runtime.dispatch(send_frame(timeout_ms=1))
    await finish(runtime)
    assert sink[-1]["payload"]["state"] == "error" and len(sink) == 2


async def test_close_cancels_inflight_work_without_publishing():
    started = asyncio.Event()

    class Slow:
        async def answer(self, task):
            started.set()
            await asyncio.Event().wait()

    sink = Sink()
    runtime = Runtime(Slow(), sink.send, private_seats={})
    await runtime.dispatch(send_frame())
    await started.wait()
    await runtime.close()
    assert len(sink) == 1 and runtime.runs[sink[0]["payload"]["run_id"]].work.done()
