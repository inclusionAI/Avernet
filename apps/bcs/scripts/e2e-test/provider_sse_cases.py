"""SSE success and fault journeys through protected Workbench delivery."""
import json
from pathlib import Path
import os
from urllib.parse import quote
import uuid


def exercise(story, socket_client, group_id, session_id, lease) -> None:
    bot = story.bot
    session_path = "/sessions/" + quote(session_id, safe="") + "/messages"
    # Each fault is followed by a healthy turn, proving that transport failure
    # releases the queue lane instead of leaving the next request Unknown.
    modes = ("terminal", "eof", "terminal", "read_error", "terminal",
             "oversized_frame", "terminal", "oversized_buffer", "terminal", "lag_recovery")
    for mode in modes:
        marker = "protected SSE " + mode + " " + uuid.uuid4().hex
        frame_id = "sse-" + uuid.uuid4().hex
        story.post("/control/provider/sse", {
            "provider_bot_ref": bot["provider_bot_ref"], "text": marker, "mode": mode,
        }, mock=True)
        socket_client.send({"type": "req", "id": frame_id, "method": "chat.send",
            "params": {"group_id": group_id, "session_id": session_id,
                "bot_id": "human_" + story.args.human, "message": marker,
                "mentions": [bot["bot_uuid"]], "leaseId": lease["leaseId"], "leaseEpoch": lease["epoch"]}})
        admitted = socket_client.receive(frame_id)
        assert admitted.get("ok") is True, "Workbench rejected SSE " + mode

        def matching_ride(rows):
            return next((row["body"] for row in rows
                if row["body"].get("method") == "chat.send"
                and row["body"].get("to_bot", {}).get("provider_bot_ref") == bot["provider_bot_ref"]
                and marker in json.dumps(row["body"].get("message", {}))), None)

        rides = story.wait("SSE " + mode + " outbound request",
            lambda: story.request("/control/provider/requests", mock=True)["requests"],
            lambda rows: matching_ride(rows) is not None)
        ride = matching_ride(rides)
        assert ride["extensions"]["caller_context"]
        assert ride["extensions"]["caller_execution_token"]["token"]
        assert ride["session_id"] == session_id
        message_id = admitted["payload"]["queue_admission"]["message_id"]
        deliveries_path = ("/openapi/v1/collaboration/messages/" + quote(message_id, safe="")
                           + "/deliveries?session_id=" + quote(session_id, safe=""))
        expected = "completed" if mode in ("terminal", "lag_recovery") else "failed"
        rows = story.wait("SSE " + mode + " terminal delivery",
            lambda: story.request(deliveries_path),
            lambda rows: any(row["target_bot_id"] == bot["bot_uuid"] and row["status"] == expected for row in rows))
        matching = [row for row in rows if row["target_bot_id"] == bot["bot_uuid"]]
        assert len(matching) == 1 and matching[0]["status"] == expected, "SSE duplicated the terminal delivery"
        if expected == "completed":
            messages = story.wait("SSE " + mode + " assistant reply",
                lambda: story.request(session_path),
                lambda rows: any(row.get("role") == "assistant" and marker in json.dumps(row.get("content", "")) for row in rows))
            assert sum(row.get("role") == "assistant" and marker in json.dumps(row.get("content", ""))
                       for row in messages) == 1, "SSE duplicated the assistant reply"
        # Logs corroborate the close path when the normal Singlebox producer
        # supplies its existing BCS_LOG path. No log mutation or synthetic hits.
        log_path = os.environ.get("BCS_LOG")
        if log_path:
            reason = mode if mode not in ("oversized_frame", "oversized_buffer", "lag_recovery") else (
                "terminal" if mode == "lag_recovery" else "oversized_frame")
            def summaries():
                return [line for line in Path(log_path).read_text().splitlines()
                        if "event_code=\"sse.closed\"" in line and "run_id=" + ride["id"] + " " in line]
            summaries_found = story.wait("SSE close summary", summaries, bool)
            assert len(summaries_found) == 1 and 'reason="' + reason + '"' in summaries_found[0]
            if mode == "lag_recovery":
                edges = [line for line in Path(log_path).read_text().splitlines()
                         if 'event_code="sse.lag_' in line and "run_id=" + ride["id"] + " " in line]
                assert len(edges) == 4, "SSE did not report both lag/recovery pairs"
                assert sum('log_class="business_key"' in line for line in edges) == 2, "Later lag pairs must remain ordinary"
        print("  protected SSE " + mode + ": one terminal delivery, next turn remains usable", flush=True)
