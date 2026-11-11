#!/usr/bin/env python3
"""Cancel real queued Workbench messages without losing or resurrecting history.

Only public HTTP/WS APIs mutate state. The existing Provider mock controls when
work completes; pause_dispatch and observed delivery states remove timing races.
"""

from __future__ import annotations

import argparse
import copy
import time
import uuid

from caller_identity_story import WorkbenchSocket
from fixed_loop_story import segment
from result_queue_story import ResultQueueStory


class QueuedMessageCancellationStory(ResultQueueStory):
    def set_paused(self, paused: bool) -> None:
        policy = copy.deepcopy(self.test_policy)
        policy["pause_dispatch"] = paused
        # Keep this story independent of any TTL/retry settings left by callers.
        policy["queue_ttl_ms"] = None
        policy["safe_retry"] = None
        self.replace_policy(policy)

    def by_client_id(self, client_id: str) -> list[dict]:
        return self.post(
            f"/openapi/v1/collaboration/sessions/{segment(self.session_id)}/message-deliveries/query",
            {"client_msg_id": client_id},
        )

    def send(self, ws: WorkbenchSocket, targets: list[dict], *, camel_case=False) -> tuple[str, str]:
        client_id = "queue-client-" + uuid.uuid4().hex
        request_id = "queue-request-" + uuid.uuid4().hex
        legacy_id = "queue-legacy-" + uuid.uuid4().hex
        ws.send({
            "type": "req", "id": request_id, "method": "chat.send",
            "params": {
                "group_id": self.group_id, "session_id": self.session_id,
                "bot_id": "human_" + self.args.human,
                "message": "Identical queued content must not identify a message",
                "mentions": [bot["bot_uuid"] for bot in targets],
                "idempotency_key": legacy_id,
                "clientMessageId" if camel_case else "client_message_id": client_id,
            },
        })
        response = ws.receive(request_id)
        assert response.get("ok") is True, "Workbench must accept the queued send"
        rows = self.by_client_id(client_id)
        assert len(rows) == len(self.bots), "Client correlation must find Send and Inject recipients"
        assert {row["target_bot_id"] for row in rows if row["kind"] == "send"} == {
            bot["bot_uuid"] for bot in targets
        }, "Only the explicitly mentioned Bots should receive Send deliveries"
        message_ids = {row["message_id"] for row in rows}
        assert len(message_ids) == 1, "Targets must share one canonical message"
        message_id = message_ids.pop()
        assert message_id not in (client_id, request_id, response["payload"]["runId"]), \
            "Canonical, client, request and assistant run IDs are separate identities"
        assert self.by_client_id(legacy_id) == [], "Explicit client ID must override the legacy key"
        return client_id, message_id

    def cancel(self, message_id: str, delivery_id: str = "") -> list[dict]:
        suffix = "/" + segment(delivery_id) if delivery_id else ""
        return self.post(
            f"/openapi/v1/collaboration/messages/{segment(message_id)}/deliveries{suffix}/cancel",
            {"session_id": self.session_id},
        )

    def cancel_queued(self, message_id: str, delivery_id: str = "") -> list[dict]:
        # The live scheduler may CAS the paused wait_reason between lookup and
        # cancellation. Retry only the documented stale-state result; storage,
        # authorization and unsupported-abort failures must still fail the story.
        def attempt():
            results = self.cancel(message_id, delivery_id)
            assert results, "Cancellation must find the admitted deliveries"
            for result in results:
                assert result.get("error") in (None, "delivery_state_changed"), \
                    "Unexpected failure while cancelling queued work"
                if result.get("error"):
                    assert not result.get("client_message_id"), "Errors cannot authorize removal"
            return results

        return self.wait("queued cancellation after scheduler CAS", attempt,
                         lambda rows: all(not row.get("error") for row in rows))

    def history(self, *, limit=100) -> list[dict]:
        return self.request(f"/sessions/{segment(self.session_id)}/messages?limit={limit}")

    def history_contains(self, message_id: str) -> bool:
        return any(row["id"] == message_id for row in self.history())

    def complete_dispatched_work(self) -> None:
        # The Provider sink is shared by the full suite: unrelated Bots can
        # continue sending after clear(). Scope callbacks to this Session and
        # these Bots rather than assuming the capture buffer is exclusive.
        bots = {bot["provider_bot_ref"]: bot for bot in self.bots}
        for request in self.captured():
            body = request["body"]
            if body.get("method") != "chat.send" or body.get("session_id") != self.session_id:
                continue
            bot = bots.get(body.get("to_bot", {}).get("provider_bot_ref"))
            if bot is not None:
                self.callback(request, bot, "NO_REPLY")

    def exercise_session_delete(self, ws: WorkbenchSocket) -> None:
        """Deleting a Session settles its queue: queued work is never sent."""
        kept_session = self.session_id
        self.set_paused(True)
        self.session_id = self.post(f"/groups/{segment(self.group_id)}/sessions", {
            "session_title": "Deleted while its messages are queued",
        })["session_id"]
        doomed_session = self.session_id
        doomed_ws = WorkbenchSocket(self.args.base_url, self.args.human, self.group_id, doomed_session)
        try:
            assert doomed_ws.receive(doomed_ws.connect_id).get("ok") is True, "Workbench must join the Session"
            _, doomed_id = self.send(doomed_ws, self.bots)
            rows = self.deliveries(doomed_id)
            assert len(rows) == 2 and all(row["status"] == "queued" for row in rows)
        finally:
            doomed_ws.close()
        self.request(f"/sessions/{segment(doomed_session)}?bot_id={segment(self.args.driver)}", method="DELETE")
        self.session_id = kept_session

        # Resume dispatch and prove the scheduler is live with a probe in the
        # kept Session; the deleted Session's queued work must never reach
        # the Provider, before or after the probe is dispatched. Earlier
        # phases can leave Bot-to-Bot replies in flight on the same lanes
        # (max_running=1), so keep completing other dispatched work while
        # waiting instead of assuming the lanes are idle.
        self.post("/control/provider/clear", mock=True)
        self.set_paused(False)
        _, probe_id = self.send(ws, [self.driver])
        probe_run = next(row for row in self.deliveries(probe_id) if row["kind"] == "send")["run_id"]
        bots = {bot["provider_bot_ref"]: bot for bot in self.bots}
        completed: set[str] = set()

        def complete_kept(skip: str | None) -> list[dict]:
            # Each run is called back exactly once; the probe is skipped
            # until it has been observed dispatching.
            for request in self.captured():
                body = request["body"]
                run_id = body.get("id")
                if (body.get("method") != "chat.send" or body.get("session_id") != kept_session
                        or run_id == skip or run_id in completed):
                    continue
                bot = bots.get(body.get("to_bot", {}).get("provider_bot_ref"))
                if bot is not None:
                    self.callback(request, bot, "NO_REPLY")
                    completed.add(run_id)
            return self.deliveries(probe_id)

        self.wait("probe dispatched after the delete", lambda: complete_kept(probe_run),
                  lambda rows: any(row["kind"] == "send" and row["status"] in ("dispatching", "running")
                                   for row in rows))
        self.wait("Provider receives the probe", self.captured,
                  lambda requests: any(item["body"].get("id") == probe_run for item in requests))
        for _ in range(10):
            assert not any(item["body"].get("session_id") == doomed_session for item in self.captured()), \
                "Queued messages of a deleted Session must be cancelled, not dispatched"
            time.sleep(0.1)
        self.wait("probe work completes", lambda: complete_kept(None),
                  lambda rows: bool(rows) and all(row["status"] in ("completed", "cancelled")
                                                for row in rows if row["kind"] == "send"))
        print("  PASS: deleting a Session cancels its queued messages", flush=True)

    def exercise(self) -> None:
        group = self.post("/groups", {
            "driver_bot": self.args.driver, "label": "Queue cancellation E2E",
            "participants": [{"bot_uuid": bot["bot_uuid"]} for bot in self.bots]
                            + [{"bot_uuid": "human_" + self.args.human}],
        })
        self.group_id = group["id"]
        self.groups.append(self.group_id)
        self.session_id = self.post(f"/groups/{segment(self.group_id)}/sessions", {
            "session_title": "Queued messages survive only while needed",
        })["session_id"]
        self.set_paused(True)
        self.post("/control/provider/clear", mock=True)
        ws = WorkbenchSocket(self.args.base_url, self.args.human, self.group_id, self.session_id)
        try:
            assert ws.receive(ws.connect_id).get("ok") is True, "Workbench must join the Session"
            # Same text, different IDs. The first message later occupies only
            # the driver's lane, leaving the observer available for mixed state.
            _, neighbour_id = self.send(ws, [self.driver])
            client_id, message_id = self.send(ws, self.bots, camel_case=True)
            assert message_id != neighbour_id, "Repeated text must create independent messages"
            rows = self.deliveries(message_id)
            assert len(rows) == 2 and all(row["status"] == "queued" for row in rows)
            assert not self.captured(), "Paused admission must not dispatch to the Provider"
            assert self.history(limit=1)[0]["id"] == message_id

            first = self.cancel_queued(message_id, rows[0]["delivery_id"])
            assert len(first) == 1 and first[0]["delivery"]["status"] == "cancelled"
            assert not first[0].get("error") and not first[0].get("client_message_id"), \
                "Cancelling one target must not authorize removing a queued sibling"
            assert self.history_contains(message_id) and self.history_contains(neighbour_id)

            last = self.cancel_queued(message_id, rows[1]["delivery_id"])
            assert len(last) == 1 and last[0]["delivery"]["status"] == "cancelled"
            assert not last[0].get("error") and last[0].get("client_message_id") == client_id, \
                "The last unsent target must return the exact client correlation key"
            assert last[0]["delivery"]["message_id"] == message_id
            for _ in range(2):
                assert not self.history_contains(message_id), "Reload must not resurrect cancelled text"
                page = self.history(limit=1)
                assert len(page) == 1 and page[0]["id"] == neighbour_id, \
                    "Cancelled messages must be filtered before LIMIT, not after pagination"
            durable = self.by_client_id(client_id)
            assert len(durable) == 2 and all(row["status"] == "cancelled" for row in durable), \
                "Display hiding must retain canonical lookup and durable delivery records"
            print("  PASS: WS client correlation, partial/final cancellation, reload and pagination", flush=True)

            # Public whole-message cancellation must authorize removal too.
            all_client, all_id = self.send(ws, self.bots)
            cancelled = self.cancel_queued(all_id)
            assert len(cancelled) == 2 and all(
                not row.get("error") and row.get("client_message_id") == all_client
                and row["delivery"]["status"] == "cancelled" for row in cancelled
            )
            assert not self.history_contains(all_id) and self.history_contains(neighbour_id)

            self.set_paused(False)
            self.wait("driver lane occupied", lambda: self.deliveries(neighbour_id),
                      lambda rows: any(row["kind"] == "send" and row["status"] in ("dispatching", "running") for row in rows))
            _, mixed_id = self.send(ws, self.bots)
            mixed = self.wait("one queued sibling and one sent sibling", lambda: self.deliveries(mixed_id),
                              lambda rows: len(rows) == 2
                              and any(row["status"] == "queued" for row in rows)
                              and any(row["status"] in ("dispatching", "running") for row in rows))
            sent = next(row for row in mixed if row["status"] != "queued")
            self.wait("Provider receives the sent sibling", self.captured,
                      lambda requests: any(item["body"].get("id") == sent["run_id"] for item in requests))
            self.set_paused(True)
            queued = next(row for row in mixed if row["status"] == "queued")
            result = self.cancel_queued(mixed_id, queued["delivery_id"])
            assert len(result) == 1 and not result[0].get("error")
            assert result[0]["delivery"]["status"] == "cancelled"
            assert not result[0].get("client_message_id"), "A potentially sent sibling must block removal"
            assert self.history_contains(mixed_id)
            refused = self.cancel(mixed_id)
            assert len(refused) == 2 and all(not row.get("client_message_id") for row in refused)
            assert any(row.get("error") == "exact_abort_not_supported" for row in refused), \
                "Provider active work must not be presented as an exact queued cancellation"
            assert self.history_contains(mixed_id) and self.history_contains(neighbour_id)

            # Finish only actually dispatched work, without creating chat replies
            # or leaving busy lanes that could affect subsequent E2E stories.
            self.complete_dispatched_work()
            for retained_id in (neighbour_id, mixed_id):
                self.wait("retained message work completes", lambda mid=retained_id: self.deliveries(mid),
                          lambda rows: bool(rows) and all(row["status"] in ("completed", "cancelled")
                                                        for row in rows if row["kind"] == "send"))
                assert self.history_contains(retained_id), "Sent siblings must keep canonical history visible"
            print("  PASS: whole-message cancellation and sent-sibling/unsupported-abort safety", flush=True)
            self.exercise_session_delete(ws)
        finally:
            ws.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-url", "mock-url", "human"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--mock-sink-url")
    story = QueuedMessageCancellationStory(parser.parse_args())
    try:
        story.setup()
        story.exercise()
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
