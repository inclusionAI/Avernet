from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from engine.community.api.session.runtime_status import router
from engine.community.api.transport.ws_server import EngineWebSocketServer
from engine.community.kernel.frames import ErrorShape, ResponseFrame


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["tool", "assistant", "lifecycle"])
async def test_real_events_advance_http_sequence_without_messages(stream):
    server = EngineWebSocketServer()
    run, _ = server._openclaw_resume.create(session_key="session:test", run_id="run-1")
    run.state = "running"
    relay = SimpleNamespace(
        forward_request=AsyncMock(
            return_value=ResponseFrame.ok_response("access", {}),
        )
    )
    manager = SimpleNamespace(engine="openclaw", relay=relay)
    app = FastAPI()
    app.include_router(router)
    with (
        patch(
            "engine.community.api.session.runtime_status.EngineManager.get_instance",
            return_value=manager,
        ),
        patch(
            "engine.community.api.session.runtime_status.get_server",
            return_value=server,
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://engine"
        ) as client:
            for index, phase in enumerate((None, "start", "update", "result")):
                if phase:
                    await server._emit_stream_event(
                        SimpleNamespace(send_text=AsyncMock()),
                        "agent",
                        {
                            "stream": stream,
                            "runId": run.run_id,
                            "data": {"phase": phase, "toolCallId": "tool-1"},
                        },
                        resume_run=run,
                    )
                response = await client.get(
                    "/api/sessions/runtime/status",
                    params={"session_id": run.session_key},
                )
                assert response.status_code == 200
                data = response.json()["data"]
                assert data["last_event_seq"] == index
                assert data["run_id"] == "run-1"
                assert data["started_at"] == run.started_at_ms
                assert "resumeTicket" not in data
    assert relay.forward_request.await_count == 4
    assert relay.forward_request.await_args.kwargs["method"] == "chat.history"
    assert not run._subscribers  # Query never attaches, replays or resumes.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,expected",
    [
        ("missing", "NO_ACTIVE_RUN"),
        ("ambiguous", "AMBIGUOUS_RUN"),
        ("unsupported", "UNSUPPORTED_ENGINE"),
    ],
)
async def test_absent_or_ambiguous_records_are_not_dead_runs(case, expected):
    server = EngineWebSocketServer()
    if case == "ambiguous":
        server._openclaw_resume.create(session_key="s", run_id="r1")
        server._openclaw_resume.create(session_key="s", run_id="r2")
    relay = SimpleNamespace(
        forward_request=AsyncMock(return_value=ResponseFrame.ok_response("a", {}))
    )
    manager = SimpleNamespace(
        engine="claude_code" if case == "unsupported" else "openclaw", relay=relay
    )
    app = FastAPI()
    app.include_router(router)
    with (
        patch(
            "engine.community.api.session.runtime_status.EngineManager.get_instance",
            return_value=manager,
        ),
        patch(
            "engine.community.api.session.runtime_status.get_server",
            return_value=server,
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://engine"
        ) as client:
            result = await client.get(
                "/api/sessions/runtime/status", params={"session_id": "s"}
            )
    assert result.status_code == 200
    assert result.json()["data"] == {
        "session_id": "s",
        "found": False,
        "reason": expected,
    }


@pytest.mark.asyncio
async def test_history_access_denial_never_reads_registry():
    relay = SimpleNamespace(
        forward_request=AsyncMock(
            return_value=ResponseFrame.err_response(
                "access",
                ErrorShape("FORBIDDEN", "private"),
            )
        )
    )
    app = FastAPI()
    app.include_router(router)
    with (
        patch(
            "engine.community.api.session.runtime_status.EngineManager.get_instance",
            return_value=SimpleNamespace(engine="openclaw", relay=relay),
        ),
        patch(
            "engine.community.api.session.runtime_status.get_server",
        ) as registry,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://engine"
        ) as client:
            result = await client.get(
                "/api/sessions/runtime/status", params={"session_id": "s"}
            )
    assert result.status_code == 403
    registry.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_record_and_expiry():
    server = EngineWebSocketServer()
    run, _ = server._openclaw_resume.create(session_key="s", run_id="r")
    await run.publish("chat", {"runId": "r", "state": "final"})
    await run.finish()
    assert server.get_session_runtime_status("s")["state"] == "final"
    run.finished_at -= 181
    assert server.get_session_runtime_status("s")["reason"] == "NO_ACTIVE_RUN"


@pytest.mark.asyncio
async def test_http_runtime_status_propagates_configured_mcp_identity():
    relay = SimpleNamespace(
        forward_request=AsyncMock(return_value=ResponseFrame.ok_response("access", {})),
    )
    server = EngineWebSocketServer()
    app = FastAPI()
    app.include_router(router)
    with (
        patch(
            "engine.community.api.session.runtime_status.EngineManager.get_instance",
            return_value=SimpleNamespace(engine="openclaw", relay=relay),
        ),
        patch(
            "engine.community.api.session.runtime_status.get_server",
            return_value=server,
        ),
        patch(
            "engine.community.api.session.runtime_status.load_mcp_token_settings",
            return_value=SimpleNamespace(header_name="x-test-mcp"),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://engine",
        ) as client:
            response = await client.get(
                "/api/sessions/runtime/status",
                params={"session_id": "s"},
                headers={"x-test-mcp": "test-only-identity"},
            )
    assert response.status_code == 200
    assert relay.forward_request.await_args.kwargs["auth"].token == "test-only-identity"
    assert relay.forward_request.await_args.kwargs["params"] == {
        "sessionKey": "s",
        "limit": 1,
    }
