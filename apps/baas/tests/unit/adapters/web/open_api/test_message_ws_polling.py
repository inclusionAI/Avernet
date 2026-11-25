"""Hermetic polling contract: real loopback WS frames, storage, and ASGI.

Runs in the default contract suite; no Engine or external service is required.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from websockets.asyncio.server import serve

from secbaas.community.adapters.web.routers.open_api.dependencies import (
    validate_api_key,
)
from secbaas.community.api.bot_runtime import BotBindingInfo
from secbaas.community.bootstrap._chat_transport import build_chat_transport
from secbaas.community.core.service.bot_run import (
    AsyncChatClient,
    AsyncChatClientPool,
    TaskMessageDispatcher,
)
from secbaas.community.core.service.bot_run._chat_errors import BotSessionError
from secbaas.community.core.service.bot_run._claw_service import (
    BotServiceConfig,
    ClawBotService,
)
from secbaas.community.core.service.bot_run._engine_adapter_registry import (
    BotEngineAdapterRegistry,
)
from secbaas.community.core.service.bot_run._executor import BotRunRequestExecutor

from .test_message_router import _make_api_key_record

CODE = " 007-未知 "


@pytest_asyncio.fixture
async def poll_client(bootstrap_init, monkeypatch):
    from secbaas.community.adapters.web.app import app

    assert app.container is bootstrap_init
    key = _make_api_key_record()
    key.policy = json.dumps({"allowed_bots": ["*"]})
    monkeypatch.setitem(app.dependency_overrides, validate_api_key, lambda: key)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, key


async def _respond(ws, scenario, before_reply):
    hello = json.loads(await ws.recv())
    assert hello["method"] == "connect"
    await ws.send(json.dumps({"type": "res", "id": hello["id"], "ok": True}))
    request = json.loads(await ws.recv())
    assert request["method"] == (
        "chat.inject" if scenario == "inject-ack" else "chat.send"
    )
    before_reply()
    request_id = request["id"]
    session = request["params"]["sessionKey"]
    ack = {
        "type": "res",
        "id": request_id,
        "ok": True,
        "payload": {"status": "started", "runId": "this-run"},
    }
    event = {
        "type": "event",
        "event": "chat",
        "payload": {
            "sessionKey": session,
            "runId": "this-run",
            "state": "error",
            "errorCode": CODE,
            "errorMessage": "engine failure",
        },
    }
    if scenario in {"ack", "bad-ack-code", "bad-ack-shape", "inject-ack"}:
        rejection = {
            "type": "res",
            "id": request_id,
            "ok": False,
            "error": {"code": CODE, "message": "engine failure"},
        }
        # An unrelated response must not win over the response to this request.
        await ws.send(
            json.dumps({**rejection, "id": "retired", "error": {"code": "STALE"}})
        )
        if scenario == "bad-ack-code":
            rejection["error"]["code"] = 7
        elif scenario == "bad-ack-shape":
            rejection["error"] = []
        await ws.send(json.dumps(rejection))
    else:
        early = scenario in {"early-event", "canonical-early"}
        if scenario in {"canonical-early", "canonical-event"}:
            ack["payload"]["sessionKey"] = "canonical-without-request-key"
            event["payload"]["sessionKey"] = ack["payload"]["sessionKey"]
        elif scenario == "openclaw-prefix":
            event["payload"]["sessionKey"] = "agent:main:" + session
        if early:
            await ws.send(json.dumps(event))
        if scenario == "no-ack-run":
            ack["payload"] = {"accepted": True}
        if scenario == "cached-ack":
            ack["payload"]["status"] = "in_flight"
        await ws.send(json.dumps(ack))
        if scenario == "late-old-run":
            event["payload"]["runId"] = "previous-run"
        elif scenario == "wrong-session":
            # The legacy contains matcher accepts this. It is not proof for the code.
            event["payload"]["sessionKey"] = "different-" + session
        elif scenario == "missing-event-code":
            del event["payload"]["errorCode"]
        elif scenario == "peer-declared-termination":
            event["payload"]["errorCode"] = "STREAM_TERMINATED_WITHOUT_FINAL"
        elif scenario == "success":
            event["payload"]["state"] = "final"
        if not early:
            await ws.send(json.dumps(event))
    await ws.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution", "scenario", "stored_code"),
    [
        ("direct", "ack", CODE),
        ("direct", "aborted", None),
        ("direct", "inject-ack", CODE),
        ("direct", "bad-ack-code", None),
        ("direct", "bad-ack-shape", None),
        ("direct", "event", CODE),
        ("direct", "early-event", CODE),
        ("direct", "canonical-event", CODE),
        ("direct", "openclaw-prefix", CODE),
        ("direct", "late-old-run", None),
        ("direct", "wrong-session", None),
        ("direct", "no-ack-run", None),
        ("direct", "cached-ack", None),
        ("direct", "missing-event-code", None),
        ("direct", "peer-declared-termination", "STREAM_TERMINATED_WITHOUT_FINAL"),
        ("direct", "success", None),
        ("queued", "ack", CODE),
        ("queued", "event", CODE),
        ("queued", "same-worker-reclaim", None),
        ("queued", "legacy-reclaim", None),
        ("queued", "metadata", CODE),
        ("queue-stream", "ack", CODE),
        ("queue-stream", "early-event", CODE),
        ("queue-stream", "canonical-early", CODE),
        ("queue-stream", "late-old-run", None),
    ],
)
async def test_poll_ws_failure(
    execution,
    scenario,
    stored_code,
    bootstrap_init,
    poll_client,
    monkeypatch,
):
    repo = bootstrap_init.repository.bot_run_repository()
    http_client, key = poll_client
    run_id = uuid4().hex
    bot_id = f"bot-{run_id}:entity-1"
    session_id = f"session-{run_id}"
    binding = BotBindingInfo(
        bot_id=bot_id,
        entity_id="entity-1",
        sandbox_id="sandbox-1",
        engine_type="openclaw",
        bot_type="personal",
    )
    repo.insert_run(
        run_id=run_id,
        bot_id=bot_id,
        api_key_prefix=key.api_key_prefix,
        message_long="hello",
        metadata={
            "app_id": key.app_id,
            "stream": "true" if execution == "queue-stream" else "false",
        },
    )

    queue = bootstrap_init.repository.bot_run_queue_repository()

    def before_reply():
        if scenario == "aborted":
            repo.update_aborted(run_id, "client cancelled")
        elif scenario == "same-worker-reclaim":
            old = queue.get_by_run_id(run_id)
            assert old is not None and old.claim_id
            assert queue.release_to_pending(run_id, "ws-test") == 1
            new = queue.claim_pending_by_bot(bot_id, "ws-test")
            assert new is not None and new.claim_id and new.claim_id != old.claim_id
            # Legacy metadata snapshots cannot affect the new, separate claim.
            assert queue.update_meta(
                run_id, old.meta | {"_baas_queue_claim_id": old.claim_id}
            )
            assert queue.request_abort(run_id)
            assert queue.get_by_run_id(run_id).claim_id == new.claim_id
        elif scenario == "legacy-reclaim":
            # Old binaries write status and assigned_worker, not claim_id.
            from sqlalchemy import text

            with queue._database.orm_session() as session:
                session.execute(
                    text(
                        "UPDATE baas_bot_run_queue SET assigned_worker = 'legacy' WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
        elif scenario == "metadata":
            assert queue.update_meta(run_id, {"_baas_queue_claim_id": "not-a-claim"})
        elif execution.startswith("queue"):
            queue.touch_heartbeat(run_id, "ws-test")

    async def peer(ws):
        await _respond(ws, scenario, before_reply)

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        pool = AsyncChatClientPool(
            transport_factory=build_chat_transport, max_retries=0
        )
        service = ClawBotService(
            config=BotServiceConfig(
                proxy_base_url="http://127.0.0.1",
                proxy_ws_base_url="ws://127.0.0.1",
                adapter_port=port,
            ),
            client_pool=pool,
            secret_store=MagicMock(),
            engine_adapter_registry=BotEngineAdapterRegistry({}),
        )
        monkeypatch.setattr(
            service, "_build_ws_url", lambda *args: f"ws://127.0.0.1:{port}/ws"
        )
        monkeypatch.setattr(service, "_get_headers", lambda *args: {})
        dispatcher = TaskMessageDispatcher(run_repository=repo)
        args = dict(
            bot_service=service,
            run_id=run_id,
            session_id=session_id,
            message="hello",
            binding_info=binding,
            timeout=2.0,
        )
        try:
            if execution == "direct":
                if scenario == "inject-ack":
                    args.pop("timeout")
                    await dispatcher.dispatch_inject(**args)
                else:
                    await dispatcher.dispatch_send(**args)
            else:
                queue.insert_queue(
                    run_id=run_id,
                    bot_id=bot_id,
                    session_id=session_id,
                    meta={"timeout": 2.0},
                )
                work = queue.claim_pending_by_bot(bot_id, "ws-test")
                assert work is not None
                executor = BotRunRequestExecutor(
                    run_repository=repo,
                    binding_resolver=MagicMock(
                        resolve_binding=AsyncMock(return_value=binding)
                    ),
                    bot_service_selector=MagicMock(
                        select=MagicMock(return_value=service)
                    ),
                    chunk_repository=bootstrap_init.repository.bot_run_queue_chunk_repository(),
                    cache_plugin=bootstrap_init.plugins.cache_plugin(),
                    api_key_repository=bootstrap_init.repository.api_gateway_repository(),
                    eval_session_log=bootstrap_init.services.eval_session_log(),
                )
                await executor.execute(work)

            path = f"/openapi/v1/messages/{run_id}"
            async with asyncio.timeout(4):
                while True:
                    response = await http_client.get(path)
                    assert response.status_code == 200, response.text
                    data = response.json()["data"]
                    if data["status"] not in {"PENDING", "RUNNING"}:
                        break
                    await asyncio.sleep(0.01)
        finally:
            await pool.close_all()

    expected_status = {"success": "COMPLETED", "aborted": "ABORTED"}.get(
        scenario, "FAILED"
    )
    assert data["status"] == expected_status
    row = repo.get_by_run_id(run_id)
    assert row is not None and row.error_code == stored_code
    assert data["error_code"] == (
        stored_code or "DEFAULT" if expected_status == "FAILED" else None
    )
    if scenario == "aborted":
        assert data["error"] == "client cancelled"
    elif scenario not in {"success", "bad-ack-shape"}:
        assert "engine failure" in data["error"]


@pytest.mark.asyncio
async def test_canonical_error_targets_its_run_not_an_overlapping_session():
    async def peer(ws):
        hello = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "res", "id": hello["id"], "ok": True}))
        sends = {
            req["params"]["sessionKey"]: req
            for req in (json.loads(await ws.recv()), json.loads(await ws.recv()))
        }

        async def ack(key, run, **extra):
            await ws.send(
                json.dumps(
                    {
                        "type": "res",
                        "id": sends[key]["id"],
                        "ok": True,
                        "payload": {"status": "started", "runId": run, **extra},
                    }
                )
            )

        event = {
            "type": "event",
            "event": "chat",
            "payload": {
                "sessionKey": "canonical",
                "runId": "owner-run",
                "state": "error",
                "errorCode": CODE,
                "errorMessage": "only the owning run fails",
            },
        }
        await ack("canonical", "other-run")
        await ack("owner", "owner-run", sessionKey="canonical")
        await ws.send(json.dumps(event))
        # The other active session must receive its own successful terminal.
        payload = event["payload"] | {"runId": "other-run", "state": "final"}
        await ws.send(json.dumps(event | {"payload": payload}))
        await ws.wait_closed()

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = AsyncChatClient(
            transport=build_chat_transport(
                uri=f"ws://127.0.0.1:{port}",
                mode="off",
                headers=None,
                ignore_case=False,
                proxypass_token_minter=None,
            )
        )
        await client.connect()
        try:
            failed, successful = await asyncio.gather(
                client.send_message("first", session_key="owner", timeout=2.0),
                client.send_message("second", session_key="canonical", timeout=2.0),
                return_exceptions=True,
            )
            assert isinstance(failed, BotSessionError)
            assert failed.ws_failure is not None and failed.ws_failure.code == CODE
            assert successful == ("", [])
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_legacy_failure_does_not_wait_for_another_sessions_ack():
    reply_to_other = asyncio.Event()

    async def peer(ws):
        hello = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "res", "id": hello["id"], "ok": True}))
        requests = (json.loads(await ws.recv()), json.loads(await ws.recv()))
        ids = {req["params"]["sessionKey"]: req["id"] for req in requests}
        await ws.send(
            json.dumps(
                {
                    "type": "res",
                    "id": ids["legacy"],
                    "ok": True,
                    "payload": {"accepted": True},
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "sessionKey": "legacy",
                        "runId": "legacy-run",
                        "state": "error",
                        "errorCode": CODE,
                        "errorMessage": "expected legacy failure",
                    },
                }
            )
        )
        # Keep the unrelated ACK genuinely pending until the legacy request ends.
        await reply_to_other.wait()
        await ws.send(json.dumps({"type": "res", "id": ids["other"], "ok": True}))
        await ws.send(
            json.dumps(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "sessionKey": "other",
                        "state": "final",
                    },
                }
            )
        )
        await ws.wait_closed()

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = AsyncChatClient(
            transport=build_chat_transport(
                uri=f"ws://127.0.0.1:{port}",
                mode="off",
                headers=None,
                ignore_case=False,
                proxypass_token_minter=None,
            )
        )
        await client.connect()
        other = asyncio.create_task(client.send_message("other", session_key="other"))
        try:
            with pytest.raises(
                BotSessionError, match="expected legacy failure"
            ) as error:
                await client.send_message("legacy", session_key="legacy", timeout=0.5)
            assert error.value.ws_failure is None
        finally:
            reply_to_other.set()
            async with asyncio.timeout(2):
                assert await other == ("", [])
            await client.close()
