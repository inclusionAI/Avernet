#!/usr/bin/env python3
"""Deleted group members cannot block healthy queued Workbench recipients.

Only public HTTP/WS operations mutate state. The real scheduler delivers to the
existing Provider mock; public delivery queries verify durable failure/replay.
"""
from __future__ import annotations

import argparse
import copy
import uuid

from caller_identity_story import WorkbenchSocket
from fixed_loop_story import segment
from result_queue_story import ResultQueueStory


class QueuedTargetFailureStory(ResultQueueStory):
    def set_paused(self, paused: bool) -> None:
        policy = copy.deepcopy(self.test_policy)
        policy.update(pause_dispatch=paused, queue_ttl_ms=None, safe_retry=None)
        self.replace_policy(policy)

    def send(self, ws: WorkbenchSocket, targets: list[dict], client_id: str):
        request_id = "target-request-" + uuid.uuid4().hex
        ws.send({
            "type": "req", "id": request_id, "method": "chat.send",
            "params": {
                "group_id": self.group_id, "session_id": self.session_id,
                "bot_id": "human_" + self.args.human,
                "message": "A deleted sibling must not block this queued message",
                "mentions": [bot["bot_uuid"] for bot in targets],
                "client_message_id": client_id,
            },
        })
        response = ws.receive(request_id)
        assert response.get("ok") is True, "A deleted member must not cause dispatch_error"
        rows = self.post(
            f"/openapi/v1/collaboration/sessions/{segment(self.session_id)}/message-deliveries/query",
            {"client_msg_id": client_id},
        )
        assert len(rows) == 2, "Both the healthy and failed recipients must be persisted"
        assert len({row["message_id"] for row in rows}) == 1
        return response["payload"], {row["target_bot_id"]: row for row in rows}

    def assert_failed(self, row: dict, kind: str) -> None:
        assert row["target_bot_id"] == self.observer["bot_uuid"]
        assert row["kind"] == kind and row["status"] == "failed"
        assert row.get("admission_error") == "delivery_target_unavailable"

    def retire(self, bot: dict) -> dict:
        return self.request(
            f"/providers/{segment(self.provider['provider_id'])}/bots/{segment(bot['provider_bot_ref'])}",
            method="DELETE", headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]},
        )

    def cleanup(self) -> None:
        try:
            for group in self.groups:
                self.request(f"/groups/{segment(group)}?bot_id={segment(self.args.driver)}", method="DELETE")
        finally:
            if self.original_policy is not None:
                self.replace_policy(self.original_policy)
            for bot in self.bots:
                self.retire(bot)
            self.post("/control/reset", mock=True)

    def exercise(self) -> None:
        group = self.post("/groups", {
            "driver_bot": self.args.driver, "label": "Queued target failure E2E",
            "participants": [{"bot_uuid": bot["bot_uuid"]} for bot in self.bots]
                            + [{"bot_uuid": "human_" + self.args.human}],
        })
        self.group_id = group["id"]
        self.groups.append(self.group_id)
        self.session_id = self.post(f"/groups/{segment(self.group_id)}/sessions", {
            "session_title": "Healthy recipients survive a deleted sibling",
        })["session_id"]
        self.set_paused(True)
        retired = self.retire(self.observer)
        assert retired["deleted"] is True and retired["bot_uuid"] == self.observer["bot_uuid"]
        self.post("/control/provider/clear", mock=True)
        ws = WorkbenchSocket(self.args.base_url, self.args.human, self.group_id, self.session_id)
        try:
            assert ws.receive(ws.connect_id).get("ok") is True
            client_id = "target-client-" + uuid.uuid4().hex
            payload, first = self.send(ws, [self.driver], client_id)
            assert payload["status"] == "queued"
            self.assert_failed(first[self.observer["bot_uuid"]], "inject")
            assert first[self.args.driver]["status"] == "queued"
            _, replay = self.send(ws, [self.driver], client_id)
            for bot_id, row in first.items():
                assert replay[bot_id]["delivery_id"] == row["delivery_id"], "Replay must retain delivery identity"
                assert replay[bot_id]["message_id"] == row["message_id"]
                assert replay[bot_id]["status"] == row["status"]
            self.assert_failed(replay[self.observer["bot_uuid"]], "inject")

            payload, multiple = self.send(ws, self.bots, "target-client-" + uuid.uuid4().hex)
            assert payload["status"] == "queued"
            self.assert_failed(multiple[self.observer["bot_uuid"]], "send")
            assert multiple[self.args.driver]["status"] == "queued"

            payload, failed = self.send(ws, [self.observer], "target-client-" + uuid.uuid4().hex)
            assert payload["status"] == "failed", "Observer context cannot disguise failure of every reply target"
            self.assert_failed(failed[self.observer["bot_uuid"]], "send")
            assert failed[self.args.driver]["kind"] == "inject"
            assert failed[self.args.driver]["status"] == "pending_context"
            self.set_paused(False)

            for admitted in (first, multiple):
                healthy = admitted[self.args.driver]
                requests = self.wait("healthy recipient dispatched", self.captured,
                                     lambda rows: any(row["body"].get("id") == healthy["run_id"] for row in rows))
                request = next(row for row in requests if row["body"].get("id") == healthy["run_id"])
                assert request["body"]["to_bot"]["provider_bot_ref"] == self.driver["provider_bot_ref"]
                self.callback(request, self.driver, "NO_REPLY")
                rows = self.wait("healthy recipient completed", lambda: self.deliveries(healthy["message_id"]),
                                 lambda rows: any(row["target_bot_id"] == self.args.driver
                                                  and row["status"] == "completed" for row in rows))
                self.assert_failed(next(row for row in rows if row["target_bot_id"] == self.observer["bot_uuid"]),
                                   admitted[self.observer["bot_uuid"]]["kind"])
            delivered = [row["body"] for row in self.captured()
                         if row["body"].get("session_id") == self.session_id
                         and row["body"].get("method") == "chat.send"]
            assert len(delivered) == 2, "Failed or replayed targets must never reach the Provider"
            assert {row["id"] for row in delivered} == {
                first[self.args.driver]["run_id"], multiple[self.args.driver]["run_id"],
            }
            print("Queued target failures: deleted Send/Inject isolated, replay stable, healthy Bots completed", flush=True)
        finally:
            ws.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-url", "mock-url", "human"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--mock-sink-url")
    story = QueuedTargetFailureStory(parser.parse_args())
    try:
        story.setup()
        story.exercise()
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
