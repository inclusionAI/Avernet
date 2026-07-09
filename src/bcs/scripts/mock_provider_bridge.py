#!/usr/bin/env python3
"""Mock provider bridge: BCS downlink SSE  <->  engine WebSocket.

Bridges one chat turn:

    BCS  --POST /webhook (ProviderWebhookRequest)-->  this bridge
    this bridge  --WS connect + chat.send-->  engine (ws://host:20003/api/<engine>/ws)
    engine  --event stream-->  this bridge  --filter+transform-->  SSE frames  -->  BCS

The bridge speaks the BCS 2.0 downlink protocol on the HTTP/SSE side and the
engine v3 WS protocol on the WS side, normalizing openclaw / claude_code
event vocab into the unified BCS stream protocol.

Engine WS contract mirrors src/bcs/scripts/frontend_engine_ws.py.
BCS SSE contract: see docs/specs/2026-06-24-bcs-downlink-protocol-simplified.md
and crates/adapters/http/bcs-provider-http/src/sse.rs.

Run:
    python3 mock_provider_bridge.py --port 28080 --engine openclaw \
        --engine-host localhost --engine-port 20003
"""

import argparse
import asyncio
import json
import logging
import uuid
from typing import Any, Optional

import aiohttp
from aiohttp import web
import websockets

LOG = logging.getLogger("mock_provider")

# ---------------------------------------------------------------------------
# Engine WS client — one connection per chat turn
# ---------------------------------------------------------------------------


class _DownstreamClosed(Exception):
    """Raised when the BCS-facing SSE stream is closed mid-turn."""


def build_connect_frame(request_id: str) -> dict:
    """v3 protocol handshake, mirrors frontend_engine_ws.py:build_connect_frame."""
    return {
        "type": "req",
        "id": request_id,
        "method": "connect",
        "params": {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {
                "id": f"mock-provider-{uuid.uuid4().hex[:8]}",
                "version": "1.0.0",
                "platform": "python",
                "mode": "operator",
                "displayName": "mock_provider_bridge.py",
            },
            "role": "operator",
            "scopes": ["operator.admin", "operator.read", "operator.write"],
            "locale": "zh-CN",
            "userAgent": "mock_provider_bridge.py",
            "timezone": "Asia/Shanghai",
        },
    }


def build_chat_send_frame(
    request_id: str,
    session_key: str,
    message: str,
    engine: str,
    permission_mode: Optional[str] = None,
) -> dict:
    params = {
        "sessionKey": session_key,
        "message": message,
        "engine": engine,
        "idempotencyKey": uuid.uuid4().hex,
    }
    if permission_mode:
        params["permissionMode"] = permission_mode
    return {
        "type": "req",
        "id": request_id,
        "method": "chat.send",
        "params": params,
    }


def build_chat_inject_frame(request_id: str, session_key: str, message: str) -> dict:
    """v3 chat.inject — mirrors baas _bot_websocket_client.chat_inject.

    Injects a message into the session (engine writes it to context / history)
    WITHOUT triggering a bot reply. Fire-and-forget: caller waits only for the
    `res` ack, not for an event stream.
    """
    return {
        "type": "req",
        "id": request_id,
        "method": "chat.inject",
        "params": {
            "sessionKey": session_key,
            "message": message,
            # engine local mock runs disable the IAM zero-check; send the
            # same sentinel baas uses when no token is minted.
            "x-iam-token": "OPEN_API:NOT_PROVIDED",
        },
    }


def build_chat_abort_frame(request_id: str, session_key: str, abort_run_id: Optional[str]) -> dict:
    """v3 chat.abort — cancel an in-flight run for the session."""
    params: dict[str, Any] = {"sessionKey": session_key}
    if abort_run_id:
        params["runId"] = abort_run_id
    return {"type": "req", "id": request_id, "method": "chat.abort", "params": params}


def engine_ws_url(host: str, port: int, engine: str) -> str:
    return f"ws://{host}:{port}/api/{engine}/ws"


# ---------------------------------------------------------------------------
# Engine event  ->  BCS SSE frame transform (per-engine normalization)
# ---------------------------------------------------------------------------


def _extract_text(message: Any) -> Optional[str]:
    """Pull cumulative text out of a message.content[] array if present."""
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    text = "".join(parts)
    return text or None


def transform_event(frame: dict, engine: str) -> Optional[dict]:
    """Map one engine v3 event frame to a BCS SSE frame {event, data}.

    Returns None for frames that carry no BCS-relevant signal (dropped:
    assistant/item/content_block/command_output/message/tick/lifecycle-noise).
    The returned dict is {"event": "agent"|"chat", "data": {...}} ready to be
    serialized into an SSE block; caller assigns the monotonic seq/id.
    """
    if frame.get("type") != "event":
        return None
    event = frame.get("event")
    payload = frame.get("payload") or {}

    # ---- chat: body text + terminal (close anchor) ----
    if event == "chat":
        state = payload.get("state")
        if state not in {"delta", "final", "error", "aborted"}:
            # openclaw folds approval into chat state=approval_requested -> gated; drop.
            return None
        data: dict[str, Any] = {"state": state}
        # openclaw uses deltaText, claude_code uses delta; normalize to deltaText.
        delta_text = payload.get("deltaText")
        if delta_text is None:
            delta_text = payload.get("delta")
        if state == "delta" and delta_text is not None:
            data["deltaText"] = delta_text
        if payload.get("message") is not None:
            data["message"] = payload["message"]
        if payload.get("stopReason") is not None:
            data["stopReason"] = payload["stopReason"]
        if state == "error":
            data["errorMessage"] = payload.get("errorMessage") or payload.get("error") or "engine error"
        return {"event": "chat", "data": data}

    if event != "agent":
        # interaction.requested / interaction.resolved / unknown top-level -> drop in v1
        return None

    stream = payload.get("stream")
    pdata = payload.get("data") or {}

    # ---- thinking: identical field shape across engines ----
    if stream == "thinking":
        out = {"stream": "thinking"}
        if pdata.get("delta") is not None:
            out["delta"] = pdata["delta"]
        if pdata.get("text") is not None:
            out["text"] = pdata["text"]
        return {"event": "agent", "data": out}

    # ---- tool: normalize openclaw {phase,name,args,result} vs cc {type,toolName,input,output} ----
    if stream == "tool":
        return _transform_tool(pdata, engine)

    if stream == "command_output":
        return _transform_command_output(pdata)

    # ---- lifecycle: optional, informational; forward start/end (not close) ----
    if stream == "lifecycle":
        phase = pdata.get("phase")
        if phase in {"start", "end"}:
            out = {"stream": "lifecycle", "phase": phase}
            for k in ("model", "agentMode"):
                if pdata.get(k) is not None:
                    out[k] = pdata[k]
            return {"event": "agent", "data": out}
        return None

    # assistant / item / content_block / message / phase(noise) -> drop
    return None


def _transform_command_output(pdata: dict) -> Optional[dict]:
    """Normalize Claude Code command_output:end to a BCS tool result."""
    if pdata.get("phase") != "end":
        return None
    out: dict[str, Any] = {"stream": "tool", "phase": "result"}
    if pdata.get("toolCallId"):
        out["toolCallId"] = pdata["toolCallId"]
    output = pdata.get("output")
    if output is not None:
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        out["result"] = {"content": [{"type": "text", "text": text}]}
    exit_code = pdata.get("exitCode")
    if exit_code is not None:
        out["exitCode"] = exit_code
    if pdata.get("durationMs") is not None:
        out["durationMs"] = pdata["durationMs"]
    if pdata.get("cwd") is not None:
        out["cwd"] = pdata["cwd"]
    out["isError"] = isinstance(exit_code, int) and exit_code != 0
    return {"event": "agent", "data": out}


def _transform_tool(pdata: dict, engine: str) -> Optional[dict]:
    """Normalize a tool-stream data object to BCS {phase,name,toolCallId,args/result,isError}."""
    if engine == "claude_code":
        # cc: data.type in start/update/result; toolName; input; output
        ctype = pdata.get("type")
        if ctype == "update":
            # partialInput streaming fragment — drop (we synthesize start from final shape)
            return None
        phase = {"start": "start", "result": "result"}.get(ctype)
        if phase is None:
            return None
        out: dict[str, Any] = {"stream": "tool", "phase": phase}
        if pdata.get("toolName"):
            out["name"] = pdata["toolName"]
        if pdata.get("toolCallId"):
            out["toolCallId"] = pdata["toolCallId"]
        if phase == "start" and pdata.get("input") is not None:
            out["args"] = pdata["input"]
        if phase == "result":
            output = pdata.get("output")
            if output is not None:
                # wrap raw output into result.content[] text shape
                if isinstance(output, str):
                    out["result"] = {"content": [{"type": "text", "text": output}]}
                else:
                    out["result"] = output if isinstance(output, dict) else {"content": [{"type": "text", "text": json.dumps(output)}]}
            out["isError"] = bool(pdata.get("isError", False))
        return {"event": "agent", "data": out}

    # openclaw: data already {phase,name,toolCallId,args,result,isError} — pass relevant keys.
    phase = pdata.get("phase")
    if phase not in {"start", "update", "result"}:
        return None
    out = {"stream": "tool", "phase": phase}
    for k in ("name", "toolCallId", "args", "result", "partialResult", "isError", "exitCode", "durationMs", "cwd"):
        if pdata.get(k) is not None:
            out[k] = pdata[k]
    return {"event": "agent", "data": out}


# ---------------------------------------------------------------------------
# SSE framing
# ---------------------------------------------------------------------------


def sse_block(event: str, seq: int, data: dict) -> bytes:
    """Serialize one SSE frame: event:/id:/data: lines, blank-line terminated."""
    return (
        f"event: {event}\n"
        f"id: {seq}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# One chat turn: drive engine WS, stream transformed frames to the SSE response
# ---------------------------------------------------------------------------


async def drive_turn(
    resp: web.StreamResponse,
    run_id: str,
    session_key: str,
    message: str,
    engine: str,
    cfg: "BridgeConfig",
) -> None:
    """Open an engine WS, run one chat.send, stream transformed events as SSE."""
    seq = 0
    url = engine_ws_url(cfg.engine_host, cfg.engine_port, engine)
    LOG.info("turn %s: connecting engine %s", run_id, url)

    async def write(event: str, data: dict) -> None:
        nonlocal seq
        seq += 1
        # carry the engine-internal run id opaquely; BCS correlates by connection.
        data.setdefault("runId", run_id)
        data["seq"] = seq
        try:
            await resp.write(sse_block(event, seq, data))
        except (ConnectionResetError, RuntimeError) as exc:
            # BCS closed the SSE stream (e.g. its own deadline/abort). Stop the
            # turn cleanly rather than spewing tracebacks on every later frame.
            raise _DownstreamClosed() from exc

    try:
        async with websockets.connect(
            url, max_size=16 * 1024 * 1024,
            additional_headers={"Origin": cfg.engine_origin},
        ) as ws:
            # v3 handshake
            connect_id = uuid.uuid4().hex
            await ws.send(json.dumps(build_connect_frame(connect_id)))
            # chat.send
            chat_id = uuid.uuid4().hex
            await ws.send(json.dumps(build_chat_send_frame(
                chat_id, session_key, message, engine, cfg.permission_mode,
            )))

            accepted = False
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=cfg.idle_timeout_s)
                frame = json.loads(raw)
                ftype = frame.get("type")

                if ftype == "res":
                    if frame.get("id") == chat_id and not frame.get("ok", True):
                        await write("chat", {"state": "error", "errorMessage": str(frame.get("error"))})
                        return
                    if frame.get("id") == chat_id:
                        accepted = True
                    continue

                out = transform_event(frame, engine)
                if out is not None:
                    await write(out["event"], out["data"])

                # terminal close anchor: chat final/error/aborted
                payload = frame.get("payload") or {}
                if accepted and frame.get("event") == "chat" and payload.get("state") in {"final", "error", "aborted"}:
                    LOG.info("turn %s: terminal state=%s, closing", run_id, payload.get("state"))
                    return
    except _DownstreamClosed:
        # BCS closed the SSE stream; nothing more to write. Quiet return.
        LOG.info("turn %s: downstream (BCS) closed the SSE stream; ending turn", run_id)
        return
    except asyncio.TimeoutError:
        LOG.warning("turn %s: engine idle timeout; synthesizing error terminal", run_id)
        try:
            await write("chat", {"state": "error", "errorMessage": "engine idle timeout"})
        except _DownstreamClosed:
            pass
    except Exception as exc:  # noqa: BLE001 - bridge must never crash a turn silently
        LOG.warning("turn %s: engine bridge error: %s; synthesizing error terminal", run_id, exc)
        try:
            await write("chat", {"state": "error", "errorMessage": f"bridge error: {exc}"})
        except _DownstreamClosed:
            pass


# ---------------------------------------------------------------------------
# Callback-streaming driver: JSON-ack the webhook, then read the engine event
# stream and REVERSE-POST events to BCS /bot/events. Two body shapes:
#   - callback-1.0: only the terminal chat, as {run_id,seq,state,message.text}
#   - callback-2.0: each completion event, as {run_id,seq,event,payload}
# Runs as a background task so the webhook can return its JSON ack immediately.
# ---------------------------------------------------------------------------


async def _post_bot_event(cfg: "BridgeConfig", body: dict) -> None:
    """Reverse-POST one callback event to BCS /bot/events (ProviderAdmin auth)."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.provider_admin_token}",
        "X-BCN-Provider-Id": cfg.provider_id,
        "X-BCN-Provider-Bot-Ref": cfg.provider_bot_ref,
    }
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(cfg.bcs_events_url, json=body, headers=headers) as resp:
                txt = await resp.text()
                if resp.status >= 300:
                    LOG.warning("callback POST /bot/events -> %s: %s", resp.status, txt[:200])
                else:
                    LOG.info("callback POST /bot/events ok (%s) %s", resp.status, txt[:120])
    except Exception as exc:  # noqa: BLE001 - never crash the turn on a callback error
        LOG.warning("callback POST /bot/events failed: %s", exc)


def _is_completion_event(out: dict) -> bool:
    """Per spec §11.1.1, callback streaming forwards only completion events:
    chat final/error/aborted, agent tool phase=result, thinking. Drop deltas,
    tool start/update, lifecycle, etc."""
    event = out.get("event")
    data = out.get("data") or {}
    if event == "chat":
        return data.get("state") in {"final", "error", "aborted"}
    if event == "agent":
        stream = data.get("stream")
        if stream == "tool":
            return data.get("phase") == "result"
        if stream == "thinking":
            return True
    return False


async def drive_turn_callback(run_id: str, session_key: str, message: str, cfg: "BridgeConfig") -> None:
    """Read the engine stream and reverse-POST callbacks to BCS.

    Mirrors drive_turn's engine WS handling, but instead of writing SSE frames
    it POSTs to BCS /bot/events. For callback-1.0 it forwards only the terminal
    chat; for callback-2.0 it forwards each §11.1.1 completion event.
    """
    url = engine_ws_url(cfg.engine_host, cfg.engine_port, cfg.engine)
    LOG.info("turn %s: (callback %s) connecting engine %s", run_id, cfg.mode, url)
    seq = 0
    try:
        async with websockets.connect(
            url, max_size=16 * 1024 * 1024,
            additional_headers={"Origin": cfg.engine_origin},
        ) as ws:
            await ws.send(json.dumps(build_connect_frame(uuid.uuid4().hex)))
            chat_id = uuid.uuid4().hex
            await ws.send(json.dumps(build_chat_send_frame(
                chat_id, session_key, message, cfg.engine, cfg.permission_mode,
            )))

            accepted = False
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=cfg.idle_timeout_s)
                frame = json.loads(raw)
                ftype = frame.get("type")
                if ftype == "res":
                    if frame.get("id") == chat_id and not frame.get("ok", True):
                        seq += 1
                        await _post_bot_event(cfg, {
                            "run_id": run_id, "seq": seq,
                            "state": "error",
                            "message": {"text": str(frame.get("error"))},
                        })
                        return
                    if frame.get("id") == chat_id:
                        accepted = True
                    continue

                out = transform_event(frame, cfg.engine)
                payload = frame.get("payload") or {}
                is_terminal = (
                    accepted
                    and frame.get("event") == "chat"
                    and payload.get("state") in {"final", "error", "aborted"}
                )

                if cfg.mode == "callback-1.0":
                    # 1.0: only the terminal chat, as legacy state/message.text.
                    if is_terminal:
                        seq += 1
                        state = payload.get("state")
                        text = ""
                        msg = payload.get("message")
                        if isinstance(msg, dict):
                            text = _message_text(msg)
                        await _post_bot_event(cfg, {
                            "run_id": run_id, "seq": seq,
                            "state": state,
                            "message": {"text": text},
                        })
                        LOG.info("turn %s: (1.0) terminal=%s posted, closing", run_id, state)
                        return
                else:
                    # callback-2.0: forward each completion event as event/payload.
                    if out is not None and _is_completion_event(out):
                        seq += 1
                        await _post_bot_event(cfg, {
                            "run_id": run_id, "seq": seq,
                            "event": out["event"],
                            "payload": out["data"],
                        })
                    if is_terminal:
                        LOG.info("turn %s: (2.0 callback) terminal=%s, closing", run_id, payload.get("state"))
                        return
    except asyncio.TimeoutError:
        LOG.warning("turn %s: (callback) engine idle timeout; posting error terminal", run_id)
        seq += 1
        await _post_bot_event(cfg, _terminal_error_body(run_id, seq, cfg, "engine idle timeout"))
    except Exception as exc:  # noqa: BLE001
        LOG.warning("turn %s: (callback) engine error: %s; posting error terminal", run_id, exc)
        seq += 1
        await _post_bot_event(cfg, _terminal_error_body(run_id, seq, cfg, f"bridge error: {exc}"))


def _terminal_error_body(run_id: str, seq: int, cfg: "BridgeConfig", msg: str) -> dict:
    if cfg.mode == "callback-1.0":
        return {"run_id": run_id, "seq": seq, "state": "error", "message": {"text": msg}}
    return {
        "run_id": run_id, "seq": seq, "event": "chat",
        "payload": {"state": "error", "errorMessage": msg},
    }


# ---------------------------------------------------------------------------
# Fire-and-forget WS request (chat.inject / chat.abort): connect, send one
# request frame, wait only for its `res` ack, then close. No event stream.
# ---------------------------------------------------------------------------


async def drive_request(run_id: str, frame: dict, engine: str, cfg: "BridgeConfig") -> bool:
    """Open an engine WS, send one request frame, await its res ack, close.

    Returns True if the engine ack'd ok (or no explicit res arrived before a
    short timeout — inject is fire-and-forget). Used for chat.inject /
    chat.abort, which do not produce an event stream.
    """
    url = engine_ws_url(cfg.engine_host, cfg.engine_port, engine)
    req_id = frame.get("id")
    method = frame.get("method")
    LOG.info("req %s: connecting engine %s for %s", run_id, url, method)
    try:
        async with websockets.connect(
            url, max_size=16 * 1024 * 1024,
            additional_headers={"Origin": cfg.engine_origin},
        ) as ws:
            await ws.send(json.dumps(build_connect_frame(uuid.uuid4().hex)))
            await ws.send(json.dumps(frame))
            # Wait briefly for the matching res ack; inject is fire-and-forget,
            # so a short timeout that returns ok is acceptable.
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=cfg.idle_timeout_s)
                f = json.loads(raw)
                if f.get("type") == "res" and f.get("id") == req_id:
                    ok = bool(f.get("ok", True))
                    LOG.info("req %s: %s res ok=%s", run_id, method, ok)
                    return ok
    except asyncio.TimeoutError:
        LOG.info("req %s: %s no res before timeout; treating as fire-and-forget ok", run_id, method)
        return True
    except Exception as exc:  # noqa: BLE001 - never crash the webhook on inject
        LOG.warning("req %s: %s engine error: %s", run_id, method, exc)
        return False


# ---------------------------------------------------------------------------
# HTTP webhook handler (BCS -> bridge)
# ---------------------------------------------------------------------------


def _message_text(message: Any) -> str:
    """Extract the user prompt from the ProviderWebhookRequest.message field.

    BCS forwards chat.send params.message verbatim — could be a plain string
    or an object like {"role":"user","content":"..."}.
    """
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        text = _extract_text(message)
        if text:
            return text
        if content is not None:
            return json.dumps(content, ensure_ascii=False)
    if message is None:
        return ""
    return json.dumps(message, ensure_ascii=False)


def make_webhook_handler(cfg: "BridgeConfig"):
    async def handle(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        method = body.get("method")
        run_id = body.get("id") or uuid.uuid4().hex
        session_id = body.get("session_id") or f"agent:main:session:{uuid.uuid4()}:user:mock"
        message = _message_text(body.get("message"))
        auth = request.headers.get("Authorization", "")
        proto = request.headers.get("X-BCN-Protocol-Version", "?")
        LOG.info(
            "webhook: method=%s run_id=%s proto=%s auth=%s msg=%r",
            method, run_id, proto, "yes" if auth else "no", message[:120],
        )

        # chat.inject / chat.abort: forward to the engine over WS as a
        # fire-and-forget request, then JSON-ack. The engine writes the
        # injected message into the session (no reply); BCS (2.0) accepts the
        # JSON ack now that deliver() dispatches non-send methods to ack.
        if method == "chat.inject":
            ok = await drive_request(
                run_id, build_chat_inject_frame(uuid.uuid4().hex, session_id, message), cfg.engine, cfg
            )
            return web.json_response({"ok": ok})
        if method == "chat.abort":
            abort_run_id = body.get("abort_run_id") or body.get("run_id")
            ok = await drive_request(
                run_id, build_chat_abort_frame(uuid.uuid4().hex, session_id, abort_run_id), cfg.engine, cfg
            )
            return web.json_response({"ok": ok})
        # history / interaction.resolve / unknown: simple JSON ack (mock holds
        # no local history; history could forward to engine/relay in future).
        if method != "chat.send":
            return web.json_response({"ok": True})

        # chat.send — answer per the configured mode:
        #   callback-1.0 / callback-2.0: JSON-ack now, then drive the engine in
        #   the background and reverse-POST events to BCS /bot/events.
        if cfg.mode in ("callback-1.0", "callback-2.0"):
            asyncio.ensure_future(drive_turn_callback(run_id, session_id, message, cfg))
            return web.json_response({"ok": True})

        # mode == "sse": stream the engine events back as the SSE response body.
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-BCN-Protocol-Version": "2.0",
                "X-BCN-Run-Id": run_id,
            },
        )
        await resp.prepare(request)
        await drive_turn(resp, run_id, session_id, message, cfg.engine, cfg)
        # BCS may have already closed the SSE connection (e.g. after the chat
        # terminal); write_eof on a closed transport raises — tolerate it.
        try:
            await resp.write_eof()
        except (ConnectionResetError, RuntimeError) as exc:
            LOG.debug("turn %s: write_eof on closed transport: %s", run_id, exc)
        return resp

    return handle


# ---------------------------------------------------------------------------
# Config + entrypoint
# ---------------------------------------------------------------------------


class BridgeConfig:
    def __init__(
        self,
        engine: str,
        engine_host: str,
        engine_port: int,
        idle_timeout_s: float,
        mode: str = "sse",
        bcs_events_url: str = "",
        provider_id: str = "",
        provider_bot_ref: str = "",
        provider_admin_token: str = "",
        permission_mode: str = "",
        engine_origin: str = "https://teamclaw.localhost",
    ):
        self.engine = engine
        self.engine_host = engine_host
        self.engine_port = engine_port
        self.idle_timeout_s = idle_timeout_s
        self.permission_mode = permission_mode
        self.engine_origin = engine_origin
        # send response shape:
        #   "sse"          -> 2.0 SSE: respond with text/event-stream, stream frames.
        #   "callback-2.0" -> 2.0 callback streaming: JSON-ack the POST, then
        #                     reverse-POST completion events to BCS /bot/events
        #                     using the new {run_id,seq,event,payload} body.
        #   "callback-1.0" -> 1.0 callback: JSON-ack, then reverse-POST ONLY the
        #                     terminal chat as {run_id,seq,state,message.text}.
        self.mode = mode
        # Reverse-callback wiring (required for callback-* modes):
        self.bcs_events_url = bcs_events_url          # e.g. http://127.0.0.1:21000/bot/events
        self.provider_id = provider_id
        self.provider_bot_ref = provider_bot_ref
        self.provider_admin_token = provider_admin_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock provider bridge (BCS SSE <-> engine WS)")
    parser.add_argument("--port", type=int, default=28080, help="HTTP/SSE port BCS calls")
    parser.add_argument("--engine", default="openclaw", choices=["openclaw", "claude_code"])
    parser.add_argument("--engine-host", default="localhost")
    parser.add_argument("--engine-port", type=int, default=20003)
    parser.add_argument("--idle-timeout", type=float, default=120.0, help="engine idle timeout (s)")
    parser.add_argument(
        "--mode",
        default="sse",
        choices=["sse", "callback-2.0", "callback-1.0"],
        help="how to answer chat.send (see BridgeConfig). callback-* need --bcs-events-url etc.",
    )
    parser.add_argument("--bcs-events-url", default="", help="BCS upstream callback URL (POST /bot/events)")
    parser.add_argument("--provider-id", default="", help="provider_id for callback auth headers")
    parser.add_argument("--provider-bot-ref", default="", help="provider_bot_ref for callback auth headers")
    parser.add_argument("--provider-admin-token", default="", help="provider_admin_token (Bearer) for callback auth")
    parser.add_argument("--permission-mode", default="", help="optional engine chat.send permissionMode")
    parser.add_argument("--engine-origin", default="https://teamclaw.localhost", help="Origin header for engine websocket calls")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [mock_provider] %(message)s",
    )

    if args.mode in ("callback-2.0", "callback-1.0") and not args.bcs_events_url:
        parser.error(f"--mode {args.mode} requires --bcs-events-url (BCS /bot/events)")

    cfg = BridgeConfig(
        args.engine, args.engine_host, args.engine_port, args.idle_timeout,
        mode=args.mode,
        bcs_events_url=args.bcs_events_url,
        provider_id=args.provider_id,
        provider_bot_ref=args.provider_bot_ref,
        provider_admin_token=args.provider_admin_token,
        permission_mode=args.permission_mode,
        engine_origin=args.engine_origin,
    )
    app = web.Application()
    app.router.add_post("/webhook", make_webhook_handler(cfg))
    app.router.add_get("/health", lambda _r: web.json_response({"ok": True, "engine": cfg.engine, "mode": cfg.mode}))

    LOG.info(
        "mock provider on :%d -> engine '%s' at %s:%d (POST /webhook) mode=%s%s",
        args.port, cfg.engine, cfg.engine_host, cfg.engine_port, cfg.mode,
        f" -> callback {cfg.bcs_events_url}" if cfg.mode.startswith("callback") else "",
    )
    web.run_app(app, host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()
