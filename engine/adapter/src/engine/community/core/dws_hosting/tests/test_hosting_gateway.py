"""Offline real WebSocket conformance, independent of existing chat clients/pool."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from engine.community.core.dws_hosting.execution import HostingExecutor, invocation_for
from engine.community.kernel.dws_execution import EngineUpdate, ExecutionError
from engine.community.plugins.dws_hosting.openclaw import OpenClawHostingEngine
from engine.community.plugins.dws_hosting.openclaw_transport import (
    HostingGatewayConfig,
    HostingGatewayTransport,
    _private_key,
)

from .test_execution import build_execution, result_json


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("ping", "gateway_ping_timeout"),
        ("closed", "gateway_connection_closed"),
        ("deadline", "gateway_result_timeout"),
    ],
)
async def test_owned_connection_failures_keep_safe_diagnostics(
    execution, key_file, monkeypatch, failure, expected
):
    from websockets.frames import Close
    from engine.community.plugins.dws_hosting import openclaw_transport as transport

    class BrokenConnection:
        def __init__(self, *args, **kwargs):
            assert kwargs["ping_interval"] == 60
            assert kwargs["ping_timeout"] == 120

        async def __aenter__(self):
            if failure == "deadline":
                raise TimeoutError("private data")
            close = Close(
                1011 if failure == "ping" else 1008,
                "keepalive ping timeout" if failure == "ping" else "private data",
            )
            raise ConnectionClosed(None, close)

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(transport, "_NoRedirectConnect", BrokenConnection)
    config = HostingGatewayConfig(
        url="ws://localhost", device_key_file=key_file, token="test"
    )
    state, ledger, turn, _ = execution
    result = await HostingExecutor(
        state, ledger, OpenClawHostingEngine(config)
    ).execute("binding", turn.turn_id)
    assert result.state == "unknown"
    assert result.reason == expected
    assert result.decision is None


@pytest.mark.parametrize("old_defaults", [True, False])
async def test_delayed_pong_does_not_discard_late_agent_result(
    execution, key_file, monkeypatch, old_defaults
):
    from websockets.frames import OP_PONG
    from engine.community.plugins.dws_hosting import openclaw_transport as transport

    connect = transport._NoRedirectConnect

    def accelerated(*args, **kwargs):
        # Preserve the 20+20 versus 60+120 ratios without a minutes-long test.
        kwargs["ping_interval"] = (
            0.02 if old_defaults else kwargs["ping_interval"] / 1000
        )
        kwargs["ping_timeout"] = 0.02 if old_defaults else kwargs["ping_timeout"] / 1000
        return connect(*args, **kwargs)

    monkeypatch.setattr(transport, "_NoRedirectConnect", accelerated)

    async def handler(ws, req):
        send_frame = ws.protocol.send_frame
        ws.protocol.send_frame = lambda frame: (
            None if frame.opcode == OP_PONG else send_frame(frame)
        )
        run = req["params"]["idempotencyKey"]
        await response(ws, req, {"runId": run, "status": "accepted"})
        await asyncio.sleep(0.12)
        await response(
            ws,
            req,
            {
                "runId": run,
                "status": "ok",
                "summary": "completed",
                "result": {"payloads": [{"text": "completed without resend"}]},
            },
        )

    async with gateway(key_file, handler) as (config, requests, _):
        updates = []
        if old_defaults:
            with pytest.raises(ExecutionError, match="gateway_ping_timeout"):
                async for update in OpenClawHostingEngine(config).execute(
                    invocation_for(execution[2])
                ):
                    updates.append(update)
            assert [v.status for v in updates] == ["accepted"]
        else:
            updates = [
                v
                async for v in OpenClawHostingEngine(config).execute(
                    invocation_for(execution[2])
                )
            ]
            assert [v.status for v in updates] == ["accepted", "completed"]
        assert len(requests) == 1


@pytest.fixture
def execution(tmp_path):
    return build_execution(tmp_path)


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "device.pem"
    path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    return path


@asynccontextmanager
async def gateway(key_file, handler, *, handshake="ok", snapshot=None):
    requests, errors = [], []
    closed = asyncio.Event()

    async def serve_client(ws):
        try:
            await ws.send(
                json.dumps(
                    {
                        "type": "event",
                        "event": "connect.challenge",
                        "payload": {"nonce": "test-nonce"},
                    }
                    if handshake != "bad-challenge"
                    else {}
                )
            )
            if handshake == "bad-challenge":
                await ws.wait_closed()
                return
            connect = json.loads(await ws.recv())
            assert connect["method"] == "connect"
            p = connect["params"]
            assert p["scopes"] == ["operator.read", "operator.write"]
            assert p["minProtocol"] == 3 and p["maxProtocol"] == 4
            device = p["device"]
            signed = "|".join(
                [
                    "v3",
                    device["id"],
                    "gateway-client",
                    "backend",
                    "operator",
                    ",".join(p["scopes"]),
                    str(device["signedAt"]),
                    p["auth"]["token"],
                    "test-nonce",
                    "python",
                    "",
                ]
            )
            public = Ed25519PublicKey.from_public_bytes(
                base64.urlsafe_b64decode(device["publicKey"] + "==")
            )
            public.verify(
                base64.urlsafe_b64decode(device["signature"] + "=="), signed.encode()
            )
            await ws.send(
                json.dumps(
                    {
                        "type": "res",
                        "id": connect["id"],
                        "ok": handshake == "ok",
                        "payload": {
                            "type": "hello-ok",
                            "protocol": 4,
                            "snapshot": snapshot,
                        },
                    }
                )
            )
            if handshake != "ok":
                await ws.wait_closed()
                return
            request = json.loads(await ws.recv())
            requests.append(request)
            await handler(ws, request)
            await ws.wait_closed()
        except ConnectionClosed:
            pass
        except Exception as exc:
            errors.append(exc)
        finally:
            closed.set()

    async with serve(serve_client, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        config = HostingGatewayConfig(
            url=f"ws://127.0.0.1:{port}",
            device_key_file=key_file,
            token="test-token",
            run_timeout=1,
            connection_timeout=1,
        )
        yield config, requests, closed
    assert not errors, errors


async def response(ws, req, payload, *, ok=True, **extra):
    await ws.send(
        json.dumps(
            {"type": "res", "id": req["id"], "ok": ok, "payload": payload} | extra
        )
    )


@pytest.mark.parametrize("status", ["ok", "error", "in_flight", "rejected"])
@pytest.mark.parametrize("method", ["repair", "review_reply"])
async def test_repair_requires_tool_free_raw_model_mode(
    execution, key_file, status, method
):
    request = invocation_for(execution[2])

    async def handler(ws, req):
        p = req["params"]
        assert p["modelRun"] is True and p["promptMode"] == "none"
        assert p["deliver"] is False
        run = p["idempotencyKey"]
        await response(ws, req, {"runId": run, "status": "accepted"})
        await response(
            ws,
            req,
            {
                "runId": run,
                "status": status,
                "summary": "completed",
                "result": json.loads(result_json()),
            },
            ok=status != "rejected",
        )

    async with gateway(key_file, handler) as (config, requests, _):
        engine = OpenClawHostingEngine(config)
        if status == "ok":
            assert json.loads(await getattr(engine, method)(request)) == json.loads(
                result_json()
            )
        else:
            with pytest.raises(ExecutionError):
                await getattr(engine, method)(request)
        assert len(requests) == 1


@pytest.mark.parametrize("method", ["repair", "review_reply"])
async def test_raw_model_ack_without_result_is_not_approval(
    execution, key_file, monkeypatch, method
):
    request = invocation_for(execution[2])
    engine = OpenClawHostingEngine(
        HostingGatewayConfig(
            url="ws://localhost", device_key_file=key_file, token="test"
        )
    )

    async def ack_only(request, *, raw_model):
        assert raw_model is True
        yield EngineUpdate(run_id=request.run_id, status="accepted")

    monkeypatch.setattr(engine, "_execute", ack_only)
    with pytest.raises(ExecutionError, match="unavailable"):
        await getattr(engine, method)(request)


@pytest.mark.parametrize("cached", [False, True])
async def test_real_rpc_to_durable_decision(execution, key_file, cached):
    state, ledger, turn, _ = execution

    async def handler(ws, req):
        assert req["method"] == "agent"
        params = req["params"]
        assert params["deliver"] is False
        if params["idempotencyKey"].endswith("-review"):
            assert params["modelRun"] is True and params["promptMode"] == "none"
            assert ":review:" in params["sessionKey"]
            final = result_json('{"verdict":"approve"}')
        else:
            assert "modelRun" not in params and "promptMode" not in params
            assert "rule_version" in params["extraSystemPrompt"]
            final = result_json()
        assert params["sessionKey"].startswith("agent:main:dws:")
        run = params["idempotencyKey"]
        # Unrelated session events and other RPCs must never become this turn's result.
        await ws.send(
            json.dumps(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {"state": "final", "text": "wrong"},
                }
            )
        )
        await response(ws, {"id": "other"}, {"runId": run, "status": "error"})
        if not cached:
            await response(ws, req, {"runId": run, "status": "accepted"})
        await response(
            ws,
            req,
            {
                "runId": run,
                "status": "ok",
                "summary": "completed",
                "result": json.loads(final),
            },
            cached=cached,
        )

    async with gateway(key_file, handler) as (config, requests, closed):
        worker = HostingExecutor(state, ledger, OpenClawHostingEngine(config))
        result = await worker.execute("binding", turn.turn_id)
        assert result.state == "resolved" and result.decision.action == "reply"
        await asyncio.wait_for(closed.wait(), 1)
        assert len(requests) == 2
        assert (
            requests[0]["params"]["sessionKey"] != requests[1]["params"]["sessionKey"]
        )


@pytest.mark.parametrize(
    "status,expected",
    [("in_flight", "unknown"), ("error", "failed"), ("timeout", "failed")],
)
async def test_non_success_terminal(execution, key_file, status, expected):
    req = invocation_for(execution[2])

    async def handler(ws, frame):
        await response(ws, frame, {"runId": req.run_id, "status": status})

    async with gateway(key_file, handler) as (config, _, _):
        updates = [u async for u in OpenClawHostingEngine(config).execute(req)]
    assert [u.status for u in updates] == [expected]


@pytest.mark.parametrize(
    "payload,ok",
    [
        ({"runId": "wrong", "status": "ok"}, True),
        (None, True),
        ({"status": "unexpected"}, True),
        ({}, "true"),
    ],
)
async def test_invalid_response_never_accepted(execution, key_file, payload, ok):
    async def handler(ws, frame):
        await response(ws, frame, payload, ok=ok)

    async with gateway(key_file, handler) as (config, _, _):
        with pytest.raises(ExecutionError):
            _ = [
                u
                async for u in OpenClawHostingEngine(config).execute(
                    invocation_for(execution[2])
                )
            ]


async def test_failed_ack_does_not_expose_error_details(execution, key_file):
    async def handler(ws, frame):
        await response(
            ws, frame, None, ok=False, error={"message": "private diagnostic"}
        )

    async with gateway(key_file, handler) as (config, _, _):
        updates = [
            u
            async for u in OpenClawHostingEngine(config).execute(
                invocation_for(execution[2])
            )
        ]
    assert updates[0].reason == "agent_rejected"
    assert "private diagnostic" not in str(updates)


@pytest.mark.parametrize(
    "failure", ["close", "timeout", "malformed", "array", "oversize"]
)
async def test_gateway_failure_is_unknown_without_redispatch(
    execution, key_file, failure
):
    state, ledger, turn, _ = execution

    async def handler(ws, frame):
        await response(
            ws,
            frame,
            {"runId": frame["params"]["idempotencyKey"], "status": "accepted"},
        )
        if failure == "close":
            await ws.close()
        elif failure == "malformed":
            await ws.send("not-json")
        elif failure == "array":
            await ws.send("[]")
        elif failure == "oversize":
            await ws.send("x" * 262145)

    async with gateway(key_file, handler) as (config, requests, closed):
        worker = HostingExecutor(
            state, ledger, OpenClawHostingEngine(replace(config, run_timeout=0.1))
        )
        result = await worker.execute("binding", turn.turn_id)
        assert result.state == "unknown"
        await asyncio.wait_for(closed.wait(), 1)
        assert len(requests) == 1


@pytest.mark.parametrize(
    "ended_at,status,expected",
    [
        (123, "ok", True),
        (123, "error", True),
        (123, "timeout", True),
        (None, "timeout", False),
        (True, "ok", False),
        (-1, "ok", False),
        (123, "running", False),
        (float("nan"), "ok", False),
    ],
)
async def test_wait_is_status_only_and_timeout_is_not_completion(
    key_file, ended_at, status, expected
):
    async def handler(ws, req):
        assert req["method"] == "agent.wait"
        assert req["params"] == {"runId": "run", "timeoutMs": 1}
        await response(ws, req, {"runId": "run", "status": status, "endedAt": ended_at})

    async with gateway(key_file, handler) as (config, requests, _):
        assert (
            await OpenClawHostingEngine(config).inspect_run("run")
        ).ended == expected
        assert len(requests) == 1


@pytest.mark.parametrize("handshake", ["bad-challenge", "denied"])
async def test_handshake_failure_never_sends_prompt(execution, key_file, handshake):
    async def unused(*_):
        pytest.fail("must not send agent request")

    async with gateway(key_file, unused, handshake=handshake) as (config, requests, _):
        with pytest.raises(ExecutionError):
            _ = [
                u
                async for u in OpenClawHostingEngine(config).execute(
                    invocation_for(execution[2])
                )
            ]
        assert requests == []


async def test_cancellation_closes_only_owned_socket(execution, key_file):
    started = asyncio.Event()

    async def handler(ws, req):
        started.set()

    async with gateway(key_file, handler) as (config, requests, closed):
        state, ledger, turn, _ = execution
        task = asyncio.create_task(
            HostingExecutor(state, ledger, OpenClawHostingEngine(config)).execute(
                "binding", turn.turn_id
            )
        )
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), 1)
        assert ledger.get("binding", turn.turn_id).state == "unknown"
        assert [r["method"] for r in requests] == ["agent", "chat.abort"]
        assert requests[-1]["params"] == {
            "sessionKey": invocation_for(turn).session_key,
            "runId": invocation_for(turn).run_id,
        }


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "ws://example.com",
        "wss://user:pass@example.com",
        "wss://example.com?secret=x",
        "wss://example.com#fragment",
    ],
)
def test_unsafe_endpoints_rejected(key_file, url):
    with pytest.raises(ValueError):
        HostingGatewayConfig(url=url, device_key_file=key_file, token="test")


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan")])
def test_invalid_timeouts_rejected(key_file, timeout):
    with pytest.raises(ValueError):
        HostingGatewayConfig(
            url="ws://localhost",
            device_key_file=key_file,
            token="test",
            run_timeout=timeout,
        )


@pytest.mark.parametrize(
    "kind", ["shared", "symlink", "corrupt", "large", "wrong-type"]
)
def test_key_is_never_replaced(key_file, kind):
    if kind == "shared":
        key_file.chmod(0o644)
    elif kind == "symlink":
        alias = key_file.with_suffix(".link")
        alias.symlink_to(key_file)
        key_file = alias
    elif kind == "corrupt":
        key_file.write_bytes(b"corrupt")
    elif kind == "wrong-type":
        key_file.write_bytes(
            ec.generate_private_key(ec.SECP256R1()).private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    else:
        key_file.write_bytes(b"x" * 16385)
    before = key_file.read_bytes()
    with pytest.raises((ValueError, OSError, ExecutionError)):
        _private_key(key_file)
    assert key_file.read_bytes() == before


async def test_unrelated_rpc_not_supported(key_file):
    config = HostingGatewayConfig(
        url="ws://localhost", device_key_file=key_file, token="test"
    )
    assert "token=" not in repr(config)
    with pytest.raises(ExecutionError):
        _ = [
            r
            async for r in HostingGatewayTransport(config).responses(
                "id", "chat.send", {}, timeout=1
            )
        ]


@pytest.mark.parametrize(
    "changes", [{"token": ""}, {"device_key_file": Path("relative.pem")}]
)
def test_explicit_identity_required(key_file, changes):
    with pytest.raises(ValueError):
        HostingGatewayConfig(
            **(
                {"url": "ws://localhost", "device_key_file": key_file, "token": "test"}
                | changes
            )
        )


@pytest.mark.parametrize(
    "kind",
    [
        "status",
        "missing-result",
        "nan",
        "large",
        "exhausted",
        "wait-wrong-run",
        "wait-empty",
    ],
)
async def test_port_checks_uncertain_terminal_shapes(execution, key_file, kind):
    req = invocation_for(execution[2])

    class Wire:
        async def responses(self, *args, **kwargs):
            if kind in ("exhausted", "wait-empty"):
                return
            payload = {
                "runId": req.run_id,
                "status": "ok",
                "summary": "completed",
                "result": json.loads(result_json()),
            }
            if kind == "status":
                payload["status"] = "other"
            elif kind == "missing-result":
                del payload["result"]
            elif kind == "nan":
                payload["result"] = {"invalid": float("nan")}
            elif kind == "large":
                payload["result"] = {"text": "x" * 262145}
            elif kind == "wait-wrong-run":
                payload["runId"] = "wrong"
            yield {"ok": True, "payload": payload}

    engine = OpenClawHostingEngine(
        HostingGatewayConfig(
            url="ws://localhost", device_key_file=key_file, token="test"
        )
    )
    engine._transport = Wire()
    if kind.startswith("wait-"):
        with pytest.raises(ExecutionError):
            await engine.inspect_run(req.run_id)
    elif kind == "exhausted":
        assert [u async for u in engine.execute(req)] == []
    else:
        with pytest.raises(ExecutionError):
            _ = [u async for u in engine.execute(req)]


async def test_redirect_never_receives_credentials(key_file, execution):
    connections = []

    async def unexpected(ws):
        connections.append(ws)

    async with serve(unexpected, "127.0.0.1", 0) as target:
        destination = f"ws://127.0.0.1:{target.sockets[0].getsockname()[1]}"

        async def redirect(connection, request):
            reply = connection.respond(302, "redirect")
            reply.headers["Location"] = destination
            return reply

        async with serve(
            unexpected, "127.0.0.1", 0, process_request=redirect
        ) as source:
            config = HostingGatewayConfig(
                url=f"ws://127.0.0.1:{source.sockets[0].getsockname()[1]}",
                device_key_file=key_file,
                token="test-token",
            )
            with pytest.raises(ExecutionError):
                _ = [
                    u
                    async for u in OpenClawHostingEngine(config).execute(
                        invocation_for(execution[2])
                    )
                ]
    assert connections == []
