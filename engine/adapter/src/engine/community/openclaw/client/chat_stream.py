"""OpenClaw chat event filtering and child-run handling."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional, TYPE_CHECKING

from engine.community.openclaw.protocol import ErrorCodes, EventFrame, ResponseFrame
from engine.community.openclaw.client.child_runs import ChildRuns, is_child_event, is_parent_continuation
from engine.community.openclaw.client.merged_announce import MergedAnnounce
from engine.community.openclaw.client.reasoning_announce import ReasoningAnnounce, empty_announce

if TYPE_CHECKING:
    from engine.community.openclaw.client.gateway_client import OpenClawGatewayClient
    from engine.community.openclaw.client.chat_dispatch import ChatDispatch

log = logging.getLogger("openclaw-client")
_DEFAULT_EARLY_FINAL_GRACE_SECONDS = 3.0
_DEFAULT_CHAT_STREAM_TIMEOUT_SECONDS = 20 * 60

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


async def chat_stream_events(
    self: OpenClawGatewayClient,
    session_key: str,
    message: str,
    timeout_ms: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    attachments: Optional[list[dict[str, Any]]] = None,
    on_ack: Callable[[ResponseFrame], Awaitable[None]] | None = None,
    *,
    completion: ChatDispatch | None = None,
    max_pre_ack_bytes: int = 1_572_864,
) -> AsyncIterator[Dict[str, Any]]:
    """
    发送聊天消息并流式返回事件

    OpenClaw chat.send 参数: { sessionKey, message, timeoutMs, idempotencyKey, attachments }
    """
    if not self._connected:
        raise ConnectionError("Not connected")

    event_queue: asyncio.Queue[EventFrame | MergedAnnounce | None] = asyncio.Queue()
    revision = 0
    merge_check: asyncio.Task[None] | None = None
    retry_probe: ReasoningAnnounce | None = None
    retry_check: asyncio.Task[None] | None = None
    ack_pending = on_ack is not None or completion is not None
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
            and not is_child_event(event.event, payload)
        ):
            return True
        # COSEC: Bound uncorrelated events until the upstream run identity is known.
        early_bytes += len(event.to_json().encode("utf-8"))
        if early_bytes > max_pre_ack_bytes:
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
    early_final_grace_seconds = 0 if completion else _get_early_final_grace_seconds()
    filter_drop_count = 0
    pending_compaction_finals = 0
    suppressed_compaction_lifecycle_ends = 0
    children = ChildRuns(session_key)
    waiting_for_subagent_resume = False
    resumed_parent_run_id: str | None = None
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
            "pendingAnnounces": children.pending_count,
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

    def matches_current_stream(event: EventFrame, payload: Dict[str, Any]) -> bool:
        payload_run_id = payload.get("runId")
        if isinstance(payload_run_id, str) and payload_run_id:
            if payload_run_id in allowed_run_ids or payload_run_id == run_id:
                return True
            if payload_run_id.startswith("inject-"):
                return True
            if is_announce_event(payload):
                return (
                    announce_matches_parent_session(payload)
                    and children.announce_child(payload_run_id) is not None
                )
            if (
                waiting_for_subagent_resume
                and children.has_completed
                and children.matches_parent(payload)
            ):
                return (
                    is_parent_continuation(event.event, payload)
                    or event.event.endswith(approval_suffixes)
                    or payload.get("state") in {"error", "aborted"}
                )
            if pending_early_final is not None and not children.runs and payload.get("state") not in ("final", "error", "aborted"):
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

    def authorize_parent_resume(event: EventFrame, payload: Dict[str, Any]) -> bool:
        nonlocal waiting_for_subagent_resume, resumed_parent_run_id, retry_probe
        payload_run_id = payload.get("runId")
        resumable = is_parent_continuation(event.event, payload) or event.event.endswith(approval_suffixes)
        if not (
            waiting_for_subagent_resume
            and children.has_completed
            and resumable
            and not is_announce_event(payload)
            and isinstance(payload_run_id, str)
            and payload_run_id
            # A superseded foreground run cannot reclaim the resume cursor.
            and not (resumed_parent_run_id and payload_run_id in allowed_run_ids and payload_run_id != resumed_parent_run_id)
            and children.matches_parent(payload)
        ):
            return False
        allowed_run_ids.add(payload_run_id)
        resumed_parent_run_id = payload_run_id
        waiting_for_subagent_resume = False
        if retry_probe is not None:
            # The parent now owns the answer. Settle the receipt without
            # forwarding it, including any subsequent duplicate final.
            children.complete_announce(retry_probe.run)
            retry_probe = None
            if retry_check is not None:
                retry_check.cancel()
        log.info(
            "[chat_stream][subagent_parent_resumed] sessionKey=%s "
            "runId=%s pendingAnnounces=%d",
            payload.get("sessionKey"), payload_run_id, children.pending_count,
        )
        return True

    async def check_merged_announce(probe: MergedAnnounce) -> None:
        if done or revision != probe.revision:
            return
        parent_key = probe.event.payload["sessionKey"]
        try:
            response = await self.send_request("sessions.describe", {"key": parent_key}, timeout=2)
            session = (response.payload or {}).get("session") or {}
            verified = response.ok and session.get("key") == parent_key and await asyncio.wait_for(
                probe.verify_persisted(self._config.session_transcript_dir, session["sessionId"]),
                timeout=2,
            )
        except Exception as exc:
            log.info("[chat_stream][merged_announce_unverified] reason=%s", type(exc).__name__)
            return
        if verified and not done and revision == probe.revision:
            event_queue.put_nowait(probe)

    async def recover_announce(probe: ReasoningAnnounce) -> None:
        event = await probe.resolve(self, self._config.session_transcript_dir,
                                    timeout_ms / 1000 if timeout_ms else _DEFAULT_CHAT_STREAM_TIMEOUT_SECONDS)
        if not done and retry_probe is probe:
            if not waiting_for_subagent_resume or len(children.runs) != 1:
                event = probe.event
            on_chat_event(event, settled=True)

    def on_chat_event(event: EventFrame, *, settled: bool = False, dispatch_settled: bool = False) -> None:
        nonlocal revision, merge_check, retry_probe, retry_check
        nonlocal done, emitted_non_terminal, filter_drop_count
        nonlocal pending_compaction_finals, pending_early_final
        nonlocal suppressed_compaction_lifecycle_ends, waiting_for_subagent_resume
        if done:
            return
        if buffer_before_ack(on_chat_event, event):
            return
        payload = event.payload or {}
        if completion is not None and (not isinstance(payload.get("runId"), str) or not payload["runId"]):
            # Strict completion requires a correlated identity, not session-only
            # or anonymous terminal broadcasts.
            return
        if retry_probe is not None:
            retry_probe.observe(event)
        if children.observe_event(event.event, payload):
            return
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

        if completion is not None:
            if not dispatch_settled and completion.hold(event):
                return
            if dispatch_settled:
                pending_compaction_finals = 0
                suppressed_compaction_lifecycle_ends = 0

        data = payload.get("data") or {}
        stream_val = payload.get("stream", "")
        data_phase = data.get("phase") if isinstance(data, dict) else None
        state = payload.get("state")
        payload_run_id = payload.get("runId")
        if is_announce_event(payload) and state == "final":
            child = children.announce_child(payload_run_id)
            if (not settled and event.event == "chat" and child and not child.announced
                    and not child.parent_covered and waiting_for_subagent_resume
                    and len(children.runs) == 1 and empty_announce(payload)):
                if retry_probe is None:
                    retry_probe = ReasoningAnnounce(event, next(iter(children.runs)), child.session_key)
                    retry_check = asyncio.create_task(recover_announce(retry_probe))
                return
            if retry_probe is not None and payload_run_id == retry_probe.run:
                retry_probe = None
            if not children.complete_announce(payload_run_id):
                return
        revision += 1
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

        is_continuation_event = is_parent_continuation(event.event, payload)
        if authorize_parent_resume(event, payload):
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
            child_run_id = spawn_details["runId"]
            child_session_key = spawn_details["childSessionKey"]
            # COSEC: Child identities must not alias an authorized parent.
            if child_run_id in allowed_run_ids or child_session_key in trusted_parent_session_keys:
                log.info("[chat_stream][child_spawn_rejected] reason=parent_identity")
            else:
                children.register(child_run_id, child_session_key)
        elif is_spawn_result:
            log.info(
                "[chat_stream][sessions_spawn_not_accepted] status=%s isError=%s",
                spawn_details.get("status") if isinstance(spawn_details, dict) else None,
                data.get("isError"),
            )

        if (
            pending_early_final is not None and not children.runs
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
            if is_foreground_run and resumed_parent_run_id and payload_run_id != resumed_parent_run_id:
                log.info("[chat_stream][stale_parent_final] runId=%s", payload_run_id)
                return
            if is_foreground_run and not pending_compaction_finals and not waiting_for_subagent_resume:
                children.cover_completed()
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
            elif (
                (is_announce_event(payload) and children.unannounced_count > 0)
                or (is_foreground_run and children.pending_count > 0)
                or (is_foreground_run and waiting_for_subagent_resume)
            ):
                if is_foreground_run:
                    waiting_for_subagent_resume = True
                elif (
                    event.event == "chat" and is_announce_event(payload)
                    and waiting_for_subagent_resume and children.pending_count == 0
                    and len(children.runs) == 2 and merge_check is None
                    and children.unannounced_count == 1 and payload.get("message")
                ):
                    source = children.announce_child(payload_run_id)
                    sibling_run, sibling = next(
                        (r, c) for r, c in children.runs.items() if not (c.announced or c.parent_covered)
                    )
                    if source and source.session_key != sibling.session_key:
                        merge_check = asyncio.create_task(check_merged_announce(MergedAnnounce(
                            event, revision, source.session_key, sibling.session_key, sibling_run,
                        )))
                # Never flush this old final when a child later completes.
                log.info(
                    "[chat_stream][subagent_parent_final_deferred] "
                    "pendingAnnounces=%d runId=%s",
                    children.pending_count,
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
        authorize_parent_resume(event, payload)
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
        if completion is not None:
            completion.sent = True
        response = await self.send_request("chat.send", params)
        if completion is not None:
            completion.accept(response)
        resp_payload = response.payload or {}
        if early_overflow:
            raise BufferError("OpenClaw pre-ACK event buffer exceeded")
        if on_ack is not None:
            await on_ack(response)
            if not response.ok or resp_payload.get("status") == "ok":
                if completion is not None and response.ok:
                    # Cached ACKs have no live subscription. Preserve the caller's
                    # existing ACK behavior, but do not authorize a new dispatch.
                    completion.confirmed = completion.cached_confirmed
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
        if completion is not None:
            completion.task = asyncio.create_task(completion.watch(
                lambda event: on_chat_event(event, dispatch_settled=True), timeout,
            ))
        while True:
            try:
                wait_timeout = (
                    early_final_grace_seconds
                    if pending_early_final is not None
                    else timeout
                )
                if completion is not None and not completion.finished.is_set():
                    # Let the completion monitor report its bounded failure first.
                    wait_timeout = timeout + 1
                event = await asyncio.wait_for(event_queue.get(), timeout=wait_timeout)
                if isinstance(event, MergedAnnounce):
                    probe = event
                    if done or revision != probe.revision:
                        continue
                    event = probe.event
                    event.payload["_source_event"] = "chat"
                    log.info("[chat_stream][merged_announce_final] verified=true")
                    done = True
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
                elif children.pending_count > 0:
                    reason = f"waiting_subagent_final_pending_announces={children.pending_count}"
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
                    children.pending_count,
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
        if completion is not None:
            await completion.close()
        if retry_check is not None:
            retry_check.cancel()
            await asyncio.gather(retry_check, return_exceptions=True)
        if merge_check is not None:
            merge_check.cancel()
            await asyncio.gather(merge_check, return_exceptions=True)
        self.off_event("chat", on_chat_event)
        self.off_event("agent", on_chat_event)
        self.off_event("approval.requested", on_approval_requested)
        self.off_event("approval.resolved", on_approval_resolved)
        self.off_event("exec.approval.requested", on_approval_requested)
        self.off_event("exec.approval.resolved", on_approval_resolved)
