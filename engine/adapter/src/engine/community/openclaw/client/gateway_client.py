"""
OpenClaw Gateway WebSocket 客户端

连接 OpenClaw Gateway 的 WebSocket 客户端，支持：
- 连接管理（challenge 握手 + 设备签名认证）
- 请求/响应匹配（通过 request id）
- 事件订阅和分发
- 心跳保活（tick 事件）
- 设备身份签名（Ed25519）

与 MoltisGatewayClient 的关键差异：
- 握手：先接收 connect.challenge 事件，再发送 connect 请求带 token 和设备签名
- chat.send 参数：{ sessionKey, message, timeoutMs, idempotencyKey, attachments }
- 无 gateway_password
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional


from engine.community.openclaw.protocol import (
    ErrorCodes,
    EventFrame,
    RequestFrame,
    ResponseFrame,
)
from engine.community.openclaw.config import OpenClawConfig

from engine.community.openclaw.client.gateway_connection import GatewayConnection

log = logging.getLogger("openclaw-client")
_DEBUG = os.getenv("OPENCLAW_DEBUG_EVENTS", "").lower() in {"1", "true", "yes", "on"}
_DEFAULT_EARLY_FINAL_GRACE_SECONDS = 3.0
# Long-running subagent workflows can be quiet while work continues upstream.
_DEFAULT_CHAT_STREAM_TIMEOUT_SECONDS = 20 * 60
_MAX_PRE_ACK_BYTES = 1_572_864


def _summarize_attachments(attachments: Any) -> dict[str, Any]:
    if attachments is None:
        return {"present": False, "count": 0}
    if not isinstance(attachments, list):
        return {"present": True, "valid": False, "type": type(attachments).__name__}

    items: list[dict[str, Any]] = []
    for index, item in enumerate(attachments):
        if not isinstance(item, dict):
            items.append({"index": index, "valid": False, "type": type(item).__name__})
            continue
        content = item.get("content")
        source = item.get("source")
        source_data = source.get("data") if isinstance(source, dict) else None
        items.append(
            {
                "index": index,
                "valid": True,
                "type": item.get("type"),
                "mimeType": item.get("mimeType") or item.get("media_type"),
                "fileName": item.get("fileName") or item.get("filename"),
                "contentLength": len(content) if isinstance(content, str) else None,
                "sourceType": source.get("type") if isinstance(source, dict) else None,
                "sourceMediaType": source.get("media_type") if isinstance(source, dict) else None,
                "sourceDataLength": len(source_data) if isinstance(source_data, str) else None,
            }
        )
    return {"present": True, "valid": True, "count": len(attachments), "items": items}


# ── 设备密钥管理 ──────────────────────────────────────────────────────────────


def _get_early_final_grace_seconds() -> float:
    raw = (os.getenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_EARLY_FINAL_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        log.warning(
            "Invalid OPENCLAW_EARLY_FINAL_GRACE_SECONDS=%r, using %.1fs",
            raw,
            _DEFAULT_EARLY_FINAL_GRACE_SECONDS,
        )
        return _DEFAULT_EARLY_FINAL_GRACE_SECONDS
    return max(value, 0.0)


class OpenClawGatewayClient(GatewayConnection):
    """OpenClaw RPC and chat events over a managed gateway connection."""

    async def _dispatch_event(self, event: EventFrame) -> None:
        """分发事件到监听器"""
        if _DEBUG:
            payload = event.payload if isinstance(event.payload, dict) else {}
            log.info(
                "[debug][client.recv] event=%s runId=%s sessionKey=%s keys=%s",
                event.event,
                payload.get("runId"),
                payload.get("sessionKey"),
                sorted(payload.keys()) if payload else [],
            )

        listeners = self._event_listeners.get(event.event, [])
        for listener in listeners:
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                log.exception(f"Event listener error: {e}")

        # 通配符监听器
        wildcard_listeners = self._event_listeners.get("*", [])
        for listener in wildcard_listeners:
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                log.exception(f"Wildcard event listener error: {e}")

    def on_event(self, event_name: str, listener: Callable[[EventFrame], None]) -> None:
        """注册事件监听器"""
        if event_name not in self._event_listeners:
            self._event_listeners[event_name] = []
        self._event_listeners[event_name].append(listener)

    def off_event(self, event_name: str, listener: Callable[[EventFrame], None]) -> None:
        """移除事件监听器"""
        if event_name in self._event_listeners:
            try:
                self._event_listeners[event_name].remove(listener)
            except ValueError:
                pass

    # ── RPC 请求 ────────────────────────────────────────────────────────────

    async def send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """发送 RPC 请求并等待响应"""
        request_id = self._next_id()
        return await self.send_request_with_id(request_id, method, params, timeout)

    async def send_request_with_id(
        self,
        request_id: str,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """发送 RPC 请求并等待响应（保留调用方提供的 request id）"""
        if not self._connected or not self._ws:
            raise ConnectionError("Not connected")

        if request_id in self._pending_requests:
            raise ValueError(f"Request id already pending: {request_id}")

        request = RequestFrame(id=request_id, method=method, params=params)

        future: asyncio.Future[ResponseFrame] = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            if method == "chat.send":
                request_params = params or {}
                log.info(
                    "[attachments][gateway_send_request] requestId=%s hasAttachments=%s "
                    "paramKeys=%s summary=%s",
                    request_id,
                    "attachments" in request_params,
                    sorted(request_params.keys()),
                    _summarize_attachments(request_params.get("attachments")),
                )
            await self._ws.send(request.to_json())
            log.debug(f"Sent request: method={method}, id={request_id}")

            response = await asyncio.wait_for(future, timeout=timeout)
            return response

        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise TimeoutError(f"Request timeout: {method}")
        except Exception:
            self._pending_requests.pop(request_id, None)
            raise

    async def send_raw_frame(self, frame: Dict[str, Any]) -> None:
        """按原样转发任意帧到 OpenClaw Gateway"""
        if not self._connected or not self._ws:
            raise ConnectionError("Not connected")

        await self._ws.send(json.dumps(frame))
        log.debug("Forwarded raw frame: type=%s", frame.get("type"))

    # ── 聊天 ────────────────────────────────────────────────────────────────

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
        on_ack: Callable[[ResponseFrame], Awaitable[None]] | None = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        发送聊天消息并流式返回事件

        OpenClaw chat.send 参数: { sessionKey, message, timeoutMs, idempotencyKey, attachments }
        """
        if not self._connected:
            raise ConnectionError("Not connected")

        event_queue: asyncio.Queue[Optional[EventFrame]] = asyncio.Queue()
        ack_pending = on_ack is not None
        early_events: list[tuple[Callable[[EventFrame], None], EventFrame]] = []
        early_bytes = 0
        early_overflow = False

        def buffer_before_ack(handler: Callable[[EventFrame], None], event: EventFrame) -> bool:
            nonlocal early_bytes, early_overflow
            if not ack_pending:
                return False
            payload = event.payload or {}
            event_session = payload.get("sessionKey")
            # COSEC: Reject known foreign sessions before charging this request's
            # buffer. Keep same-session runs: their actual ID may arrive in ACK.
            if (
                payload.get("runId") != expected_run_id
                and isinstance(event_session, str)
                and event_session
                and event_session.strip().lower().removeprefix("agent:main:")
                != session_key.strip().lower().removeprefix("agent:main:")
            ):
                return True
            # COSEC: Bound uncorrelated events until the upstream run identity is known.
            early_bytes += len(event.to_json().encode("utf-8"))
            if early_bytes > _MAX_PRE_ACK_BYTES:
                early_overflow = True
            elif not early_overflow:
                early_events.append((handler, event))
            return True

        done = False
        expected_run_id = idempotency_key or uuid.uuid4().hex
        run_id: Optional[str] = expected_run_id
        allowed_run_ids: set[str] = {expected_run_id}
        trusted_parent_session_keys: set[str] = {session_key}
        pending_early_final: Optional[EventFrame] = None
        emitted_non_terminal = False
        early_final_grace_seconds = _get_early_final_grace_seconds()
        filter_drop_count = 0
        pending_compaction_finals = 0
        suppressed_compaction_lifecycle_ends = 0
        pending_announces = 0
        waiting_for_subagent_resume = False
        approval_suffixes = ("approval.requested", "approval.resolved")

        def event_summary(event: EventFrame, payload: Dict[str, Any]) -> Dict[str, Any]:
            data = payload.get("data")
            message = payload.get("message")
            return {
                "event": event.event,
                "state": payload.get("state"),
                "stream": payload.get("stream"),
                "runId": payload.get("runId"),
                "sessionKey": payload.get("sessionKey"),
                "seq": payload.get("seq"),
                "phase": data.get("phase") if isinstance(data, dict) else None,
                "name": data.get("name") if isinstance(data, dict) else None,
                "hasMessage": isinstance(message, dict),
                "hasText": bool(payload.get("text")),
                "expectedRunId": expected_run_id,
                "currentRunId": run_id,
                "pendingAnnounces": pending_announces,
                "pendingCompactionFinals": pending_compaction_finals,
            }

        def is_empty_final_payload(payload: Dict[str, Any]) -> bool:
            if payload.get("state") != "final":
                return False
            if payload.get("text"):
                return False
            message = payload.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list) and content:
                    return False
                if message.get("text"):
                    return False
            return True

        def is_announce_event(payload: Dict[str, Any]) -> bool:
            payload_run_id = payload.get("runId")
            # Correlated foreground identity overrides an announce-like prefix.
            return (
                isinstance(payload_run_id, str)
                and payload_run_id.startswith("announce:")
                and payload_run_id not in allowed_run_ids
                and payload_run_id != run_id
            )

        def remember_trusted_parent_session(payload: Dict[str, Any]) -> None:
            payload_run_id = payload.get("runId")
            payload_session_key = payload.get("sessionKey")
            if (
                is_announce_event(payload)
                or not isinstance(payload_run_id, str)
                or not payload_run_id
                or not isinstance(payload_session_key, str)
                or not payload_session_key
            ):
                return
            if payload_run_id in allowed_run_ids or payload_run_id == run_id:
                # COSEC: Only the foreground run may establish the parent session.
                trusted_parent_session_keys.add(payload_session_key)

        def announce_matches_parent_session(payload: Dict[str, Any]) -> bool:
            payload_session_key = payload.get("sessionKey")
            return isinstance(payload_session_key, str) and payload_session_key in trusted_parent_session_keys

        def continuation_matches_parent_session(payload: Dict[str, Any]) -> bool:
            payload_session_key = payload.get("sessionKey")
            return isinstance(payload_session_key, str) and (
                payload_session_key.strip().lower().removeprefix("agent:main:")
                == session_key.strip().lower().removeprefix("agent:main:")
            )

        def is_parent_continuation(event: EventFrame, payload: Dict[str, Any]) -> bool:
            data = payload.get("data")
            stream = payload.get("stream")
            return (event.event == "chat" and payload.get("state") == "delta") or (
                event.event == "agent"
                and (
                    stream in {"assistant", "tool", "item"}
                    or (stream == "lifecycle" and isinstance(data, dict) and data.get("phase") == "start")
                )
            )

        def matches_current_stream(event: EventFrame, payload: Dict[str, Any]) -> bool:
            payload_run_id = payload.get("runId")
            if isinstance(payload_run_id, str) and payload_run_id:
                if payload_run_id in allowed_run_ids or payload_run_id == run_id:
                    return True
                if payload_run_id.startswith("inject-"):
                    return True
                if is_announce_event(payload) and pending_announces > 0:
                    return announce_matches_parent_session(payload)
                if waiting_for_subagent_resume and continuation_matches_parent_session(payload):
                    return (
                        is_parent_continuation(event, payload)
                        or event.event.endswith(approval_suffixes)
                        or payload.get("state") in {"error", "aborted"}
                    )
                if pending_early_final is not None and payload.get("state") not in ("final", "error", "aborted"):
                    payload_session_key = payload.get("sessionKey")
                    # COSEC: Fold only the default-agent alias, preserving other agents' isolation.
                    return isinstance(payload_session_key, str) and (
                        payload_session_key.strip().lower().removeprefix("agent:main:")
                        == session_key.strip().lower().removeprefix("agent:main:")
                    )
                return False

            payload_session_key = payload.get("sessionKey")
            if payload_session_key:
                return payload_session_key == session_key
            return True

        def consume_parent_resume(event: EventFrame, payload: Dict[str, Any]) -> bool:
            nonlocal pending_announces, waiting_for_subagent_resume
            payload_run_id = payload.get("runId")
            resumable = is_parent_continuation(event, payload) or event.event.endswith(approval_suffixes)
            if not (
                waiting_for_subagent_resume
                and pending_announces > 0
                and resumable
                and not is_announce_event(payload)
                and isinstance(payload_run_id, str)
                and payload_run_id
                and continuation_matches_parent_session(payload)
            ):
                return False
            allowed_run_ids.add(payload_run_id)
            pending_announces -= 1
            waiting_for_subagent_resume = False
            log.info(
                "[chat_stream][subagent_parent_resumed] sessionKey=%s "
                "runId=%s pendingAnnounces=%d",
                payload.get("sessionKey"), payload_run_id, pending_announces,
            )
            return True

        def on_chat_event(event: EventFrame) -> None:
            nonlocal done, emitted_non_terminal, filter_drop_count, pending_announces
            nonlocal pending_compaction_finals, pending_early_final
            nonlocal suppressed_compaction_lifecycle_ends, waiting_for_subagent_resume
            if done:
                return
            if buffer_before_ack(on_chat_event, event):
                return
            payload = event.payload or {}
            remember_trusted_parent_session(payload)
            if is_announce_event(payload) and not announce_matches_parent_session(payload):
                # COSEC: Reject cross-session completion events before any
                # pending counter or stream terminal state can be changed.
                filter_drop_count += 1
                log.info("[chat_stream][foreign_announce_drop]")
                return
            if not matches_current_stream(event, payload):
                filter_drop_count += 1
                log.info("[chat_stream][filter_drop] %s", event_summary(event, payload))
                return

            data = payload.get("data") or {}
            stream_val = payload.get("stream", "")
            data_phase = data.get("phase") if isinstance(data, dict) else None
            state = payload.get("state")
            payload_run_id = payload.get("runId")
            is_foreground_run = isinstance(payload_run_id, str) and (
                payload_run_id in allowed_run_ids or payload_run_id == run_id
            )
            if (
                event.event == "agent"
                and is_foreground_run
                and stream_val == "compaction"
                and data_phase == "end"
                and data.get("willRetry") is True
            ):
                pending_compaction_finals += 1
                log.info(
                    "[chat_stream][compaction_retry_pending] runId=%s pending=%d",
                    payload.get("runId", ""),
                    pending_compaction_finals,
                )

            if (
                event.event == "agent"
                and is_foreground_run
                and stream_val == "lifecycle"
                and data_phase == "end"
                and suppressed_compaction_lifecycle_ends < pending_compaction_finals
            ):
                suppressed_compaction_lifecycle_ends += 1
                log.info(
                    "[chat_stream][compaction_lifecycle_end_suppressed] "
                    "runId=%s pending=%d suppressedLifecycleEnds=%d",
                    payload.get("runId", ""),
                    pending_compaction_finals,
                    suppressed_compaction_lifecycle_ends,
                )
                return

            is_continuation_event = is_parent_continuation(event, payload)
            if consume_parent_resume(event, payload):
                is_foreground_run = True
            if (
                is_foreground_run
                and is_continuation_event
                and pending_compaction_finals > suppressed_compaction_lifecycle_ends
            ):
                cleared_retries = (
                    pending_compaction_finals - suppressed_compaction_lifecycle_ends
                )
                pending_compaction_finals = suppressed_compaction_lifecycle_ends
                log.info(
                    "[chat_stream][compaction_continuation_without_terminal] "
                    "runId=%s clearedRetries=%d",
                    payload_run_id,
                    cleared_retries,
                )

            spawn_result = data.get("result") if isinstance(data, dict) else None
            spawn_details = spawn_result.get("details") if isinstance(spawn_result, dict) else None
            is_spawn_result = (
                stream_val == "tool"
                and isinstance(data, dict)
                and data.get("name") == "sessions_spawn"
                and data.get("phase") == "result"
            )
            if (
                is_spawn_result
                and not data.get("isError")
                and isinstance(spawn_details, dict)
                and spawn_details.get("status") == "accepted"
                and isinstance(spawn_details.get("childSessionKey"), str)
                and bool(spawn_details.get("childSessionKey", "").strip())
                and isinstance(spawn_details.get("runId"), str)
                and bool(spawn_details.get("runId", "").strip())
            ):
                pending_announces += 1
                log.info(
                    "[chat_stream][sessions_spawn_accepted] pendingAnnounces=%d",
                    pending_announces,
                )
            elif is_spawn_result:
                log.info(
                    "[chat_stream][sessions_spawn_not_accepted] status=%s isError=%s",
                    spawn_details.get("status") if isinstance(spawn_details, dict) else None,
                    data.get("isError"),
                )

            if (
                pending_early_final is not None
                and state not in ("final", "error", "aborted")
                and isinstance(payload_run_id, str)
                and payload_run_id
            ):
                allowed_run_ids.add(payload_run_id)
                log.info(
                    "[chat_stream][early_final_followup_run] runId=%s allowedRunIds=%s",
                    payload_run_id,
                    sorted(allowed_run_ids),
                )
            if state in ("final", "error", "aborted"):
                log.info("[chat_stream][terminal_recv] %s", event_summary(event, payload))

            if state in ("error", "aborted"):
                # Hard stop — always terminate regardless of pending announces.
                payload["_source_event"] = event.event
                event_queue.put_nowait(event)
                done = True
                event_queue.put_nowait(None)

            elif state == "final":
                payload_run_id = payload.get("runId", "")
                is_inject = isinstance(payload_run_id, str) and payload_run_id.startswith("inject-")
                if is_inject:
                    payload["_source_event"] = event.event
                    event_queue.put_nowait(event)
                elif (
                    event.event == "chat"
                    and is_foreground_run
                    and pending_compaction_finals > 0
                    and suppressed_compaction_lifecycle_ends > 0
                ):
                    pending_compaction_finals -= 1
                    suppressed_compaction_lifecycle_ends -= 1
                    log.info(
                        "[chat_stream][compaction_final_suppressed] "
                        "runId=%s pending=%d",
                        payload_run_id,
                        pending_compaction_finals,
                    )
                elif pending_announces > 0:
                    is_announce = is_announce_event(payload)
                    if is_announce:
                        pending_announces -= 1
                    if is_announce and pending_announces <= 0:
                        payload["_source_event"] = event.event
                        event_queue.put_nowait(event)
                        done = True
                        event_queue.put_nowait(None)
                    else:
                        if is_foreground_run:
                            waiting_for_subagent_resume = True
                        # Keep parent/intermediate announce finals internal.
                        log.info(
                            "[chat_stream][subagent_parent_final_deferred] "
                            "pendingAnnounces=%d runId=%s",
                            pending_announces,
                            payload.get("runId", "")[:40],
                        )
                else:
                    payload["_source_event"] = event.event
                    if (
                        not emitted_non_terminal
                        and early_final_grace_seconds > 0
                        and is_empty_final_payload(payload)
                    ):
                        pending_early_final = event
                        event_queue.put_nowait(event)
                        log.info(
                            "[chat_stream][early_final_defer_done] %s",
                            event_summary(event, payload),
                        )
                        return
                    event_queue.put_nowait(event)
                    done = True
                    event_queue.put_nowait(None)

            else:
                # All other events (lifecycle, assistant, tool, delta, etc.)
                payload["_source_event"] = event.event
                event_queue.put_nowait(event)

        def forward_approval(
            event: EventFrame, state: str, handler: Callable[[EventFrame], None]
        ) -> None:
            if buffer_before_ack(handler, event):
                return
            if done:
                return
            payload = dict(event.payload or {})
            payload.setdefault("approvalId", payload.get("requestId") or payload.get("id"))
            payload.setdefault("runId", run_id)
            payload.setdefault("sessionKey", session_key)
            if not matches_current_stream(event, payload):
                return
            consume_parent_resume(event, payload)
            payload.setdefault("state", state)
            payload["event"] = event.event
            payload["_source_event"] = event.event
            event_queue.put_nowait(EventFrame(event="chat", payload=payload))

        def on_approval_requested(event: EventFrame) -> None:
            forward_approval(event, "approval_requested", on_approval_requested)

        def on_approval_resolved(event: EventFrame) -> None:
            forward_approval(event, "approval_resolved", on_approval_resolved)

        # 注册事件监听
        self.on_event("chat", on_chat_event)
        self.on_event("agent", on_chat_event)
        self.on_event("approval.requested", on_approval_requested)
        self.on_event("approval.resolved", on_approval_resolved)
        self.on_event("exec.approval.requested", on_approval_requested)
        self.on_event("exec.approval.resolved", on_approval_resolved)

        try:
            # OpenClaw chat.send 参数
            params: Dict[str, Any] = {
                "sessionKey": session_key,
                "message": message,
                "idempotencyKey": expected_run_id,
            }
            if timeout_ms:
                params["timeoutMs"] = timeout_ms
            if attachments is not None:
                params["attachments"] = attachments
                log.info(
                    "[attachments][gateway_chat_stream] sessionKey=%s expectedRunId=%s summary=%s",
                    session_key,
                    expected_run_id,
                    _summarize_attachments(attachments),
                )

            log.info(
                "[chat_stream][send] sessionKey=%s expectedRunId=%s timeoutMs=%s "
                "providedIdempotencyKey=%s",
                session_key,
                expected_run_id,
                timeout_ms,
                bool(idempotency_key),
            )
            response = await self.send_request("chat.send", params)
            resp_payload = response.payload or {}
            if early_overflow:
                raise BufferError("OpenClaw pre-ACK event buffer exceeded")
            if on_ack is not None:
                await on_ack(response)
                if not response.ok or resp_payload.get("status") == "ok":
                    return
                if early_overflow:
                    raise BufferError("OpenClaw pre-ACK event buffer exceeded")
            if not response.ok:
                error_msg = response.error.message if response.error else "Unknown error"
                log.info(
                    "[chat_stream][send_response] ok=false sessionKey=%s expectedRunId=%s "
                    "errorCode=%s errorMessage=%s",
                    session_key,
                    expected_run_id,
                    response.error.code if response.error else "UNKNOWN",
                    error_msg,
                )
                yield {
                    "state": "error",
                    "errorMessage": error_msg,
                    "errorCode": response.error.code if response.error else "UNKNOWN",
                }
                return

            run_id = resp_payload.get("runId")
            if isinstance(run_id, str) and run_id:
                allowed_run_ids.add(run_id)
            ack_pending = False
            for handler, event in early_events:
                handler(event)
            early_events.clear()
            log.info(
                "[chat_stream][send_response] ok=true sessionKey=%s expectedRunId=%s "
                "responseRunId=%s payloadKeys=%s",
                session_key,
                expected_run_id,
                run_id,
                sorted(resp_payload.keys()) if isinstance(resp_payload, dict) else [],
            )
            if not run_id:
                log.warning("chat.send response missing runId, falling back to unfiltered mode")

            timeout = (
                timeout_ms / 1000
                if timeout_ms
                else _DEFAULT_CHAT_STREAM_TIMEOUT_SECONDS
            )
            while True:
                try:
                    wait_timeout = (
                        early_final_grace_seconds
                        if pending_early_final is not None
                        else timeout
                    )
                    event = await asyncio.wait_for(event_queue.get(), timeout=wait_timeout)
                    if event is None:
                        if pending_early_final is not None:
                            payload = pending_early_final.payload or {}
                            log.info(
                                "[chat_stream][early_final_flush_on_done] %s",
                                event_summary(pending_early_final, payload),
                            )
                            yield payload
                        break

                    payload = event.payload or {}
                    state = payload.get("state")
                    is_terminal = state in ("final", "error", "aborted")
                    is_inject_terminal = (
                        is_terminal
                        and isinstance(payload.get("runId"), str)
                        and payload.get("runId", "").startswith("inject-")
                    )

                    if (
                        is_empty_final_payload(payload)
                        and not emitted_non_terminal
                        and not is_inject_terminal
                        and early_final_grace_seconds > 0
                    ):
                        pending_early_final = event
                        log.info(
                            "[chat_stream][early_final_hold] graceSeconds=%.3f %s",
                            early_final_grace_seconds,
                            event_summary(event, payload),
                        )
                        continue

                    if pending_early_final is not None and not is_terminal:
                        log.info(
                            "[chat_stream][early_final_discard] heldFinal=%s firstFollowup=%s",
                            event_summary(pending_early_final, pending_early_final.payload or {}),
                            event_summary(event, payload),
                        )
                        pending_early_final = None

                    log.info("[chat_stream][yield] %s", event_summary(event, payload))
                    yield payload

                    if not is_terminal:
                        emitted_non_terminal = True
                    # 只有 chat 事件的 final/error/aborted 状态才结束流
                    # lifecycle/end 只表示 agent 运行结束，但不代表 chat 事件已结束
                    # chat.inject 的 runId 以 "inject-" 开头，不应终止流
                    if is_terminal:
                        payload_run_id = payload.get("runId", "")
                        if isinstance(payload_run_id, str) and payload_run_id.startswith("inject-"):
                            continue
                        break

                except asyncio.TimeoutError:
                    if pending_early_final is not None:
                        payload = pending_early_final.payload or {}
                        log.info(
                            "[chat_stream][early_final_flush_on_timeout] %s",
                            event_summary(pending_early_final, payload),
                        )
                        yield payload
                        break

                    # 诊断：推断 timeout 最可能的原因
                    if not self._connected or self._ws is None:
                        reason = "ws_disconnected"
                    elif not emitted_non_terminal and filter_drop_count == 0:
                        reason = "no_event_received_from_upstream"
                    elif not emitted_non_terminal and filter_drop_count > 0:
                        reason = f"all_events_filtered_drop_count={filter_drop_count}"
                    elif pending_announces > 0:
                        reason = f"waiting_subagent_final_pending_announces={pending_announces}"
                    elif pending_compaction_finals > 0:
                        reason = (
                            "waiting_compaction_retry_final_pending="
                            f"{pending_compaction_finals}"
                        )
                    else:
                        reason = "stream_stalled_mid_response"

                    log.warning(
                        "[chat_stream][timeout] sessionKey=%s expectedRunId=%s "
                        "currentRunId=%s timeoutSeconds=%.1f reason=%s "
                        "emittedNonTerminal=%s filterDropCount=%d pendingAnnounces=%d "
                        "pendingCompactionFinals=%d "
                        "allowedRunIds=%s wsConnected=%s",
                        session_key,
                        expected_run_id,
                        run_id,
                        timeout,
                        reason,
                        emitted_non_terminal,
                        filter_drop_count,
                        pending_announces,
                        pending_compaction_finals,
                        sorted(allowed_run_ids),
                        self._connected,
                    )

                    yield {
                        "state": "error",
                        "errorMessage": "Chat stream timeout",
                        "errorCode": ErrorCodes.AGENT_TIMEOUT,
                    }
                    break

        finally:
            done = True
            self.off_event("chat", on_chat_event)
            self.off_event("agent", on_chat_event)
            self.off_event("approval.requested", on_approval_requested)
            self.off_event("approval.resolved", on_approval_resolved)
            self.off_event("exec.approval.requested", on_approval_requested)
            self.off_event("exec.approval.resolved", on_approval_resolved)

    async def chat_abort(
        self,
        session_key: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """中止聊天会话"""
        params: Dict[str, Any] = {"sessionKey": session_key}
        if run_id:
            params["runId"] = run_id

        response = await self.send_request("chat.abort", params)
        if response.ok:
            return {"success": True, "payload": response.payload}
        else:
            return {
                "success": False,
                "error": response.error.to_dict() if response.error else {"code": "UNKNOWN", "message": "Unknown error"},
            }

    async def approval_resolve(
        self,
        approval_id: str,
        approved: bool,
        comment: Optional[str] = None,
        command: Optional[str] = None,
    ) -> Dict[str, Any]:
        """提交审批结果"""
        params: Dict[str, Any] = {
            "approvalId": approval_id,
            "requestId": approval_id,
            "approved": approved,
            "decision": "approved" if approved else "denied",
        }
        if comment is not None:
            params["comment"] = comment
        if command is not None:
            params["command"] = command

        response = await self.send_request("exec.approval.resolve", params)
        if response.ok:
            return {"success": True, "payload": response.payload}
        else:
            return {
                "success": False,
                "error": response.error.to_dict() if response.error else {"code": "UNKNOWN", "message": "Unknown error"},
            }

    async def session_reset(self, session_key: str) -> Dict[str, Any]:
        """重置会话"""
        response = await self.send_request("sessions.reset", {"key": session_key})
        if response.ok:
            return {"success": True, "payload": response.payload}
        else:
            return {
                "success": False,
                "error": response.error.to_dict() if response.error else {"code": "UNKNOWN", "message": "Unknown error"},
            }


# ── 连接池 ──────────────────────────────────────────────────────────────────


class ConnectionPool:
    """OpenClaw Gateway WS 连接池。

    管理 N 条 OpenClawGatewayClient，round-robin 分配。
    每条连接独立维护 _recv_loop 和 event_listeners。
    ws_pool_size=1 时行为等同旧 singleton 模式。
    """

    def __init__(self, size: int = 3, config: Optional["OpenClawConfig"] = None):
        self._size = max(1, size)
        self._config = config
        self._clients: list[OpenClawGatewayClient] = []
        self._lock: Optional[asyncio.Lock] = None
        self._index = 0
        self._initialized = False

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._get_lock():
            if self._initialized:
                return
            for i in range(self._size):
                client = OpenClawGatewayClient(config=self._config)
                self._clients.append(client)
            self._initialized = True
            log.info("[pool] initialized %d slots", self._size)

    async def get(self) -> OpenClawGatewayClient:
        """获取一条可用连接（round-robin + 自动重连）。"""
        await self._ensure_initialized()
        for _ in range(self._size):
            idx = self._index % self._size
            self._index += 1
            client = self._clients[idx]
            if client.connected:
                return client
            try:
                await client.connect()
                if client.connected:
                    return client
                log.warning("[pool] slot %d is unavailable after connect", idx)
            except Exception as e:
                log.warning("[pool] connect failed for slot %d: %s", idx, e)
        raise ConnectionError("All pool connections failed")

    async def get_least_busy(self) -> OpenClawGatewayClient:
        """获取 pending_requests 最少的连接。"""
        await self._ensure_initialized()
        connected = [c for c in self._clients if c.connected]
        if not connected:
            return await self.get()
        return min(connected, key=lambda c: len(c._pending_requests))

    async def shutdown(self) -> None:
        """关闭所有连接。"""
        for client in self._clients:
            try:
                await client.disconnect()
            except Exception as e:
                log.warning("[pool] disconnect failed: %s", e)
        self._clients.clear()
        self._initialized = False
        log.info("[pool] shutdown complete")


# ── 模块级 API（兼容旧接口） ──────────────────────────────────────────────

_pool: Optional[ConnectionPool] = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        from engine.community.openclaw.config import get_config
        cfg = get_config()
        _pool = ConnectionPool(size=cfg.ws_pool_size, config=cfg)
    return _pool


def get_shared_client() -> OpenClawGatewayClient:
    """Return the first pool slot synchronously (construction-time use, no connect).

    Plugins typically hold this instance and connect lazily via their own
    `_ensure_connected()` on first use.
    """
    pool = _get_pool()
    if not pool._clients:
        pool._clients.append(OpenClawGatewayClient(config=pool._config))
        pool._initialized = True
    return pool._clients[0]


async def get_client() -> OpenClawGatewayClient:
    """获取一条可用连接（round-robin，兼容旧接口）。"""
    return await _get_pool().get()


async def close_client() -> None:
    """关闭连接池。"""
    global _pool
    if _pool:
        await _pool.shutdown()
        _pool = None
