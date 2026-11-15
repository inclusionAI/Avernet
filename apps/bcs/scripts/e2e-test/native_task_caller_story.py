#!/usr/bin/env python3
"""Leased Human -> native WebSocket Owner Manager -> protected Caller Worker.

Exercise the public bot agent/tool-start and task.dispatch frames, not the
inline coordination-result path. Verify parent binding, single-use correlation,
request replay and fail-closed rejection through live WS/HTTP boundaries.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import uuid

from caller_delegation_chain_story import DelegationChainStory
from caller_identity_story import WorkbenchSocket, segment
from group_session_ws_probe import _read_upgrade_response


class NativeBotSocket(WorkbenchSocket):
    """A real v2 Bot connection that acknowledges and records chat downlinks."""

    def __init__(self, base: str, owner: str):
        url = urllib.parse.urlsplit(base)
        self.conn = socket.create_connection((url.hostname, url.port or 80), timeout=10)
        self.conn.settimeout(15)
        self.sends = []
        key = base64.b64encode(os.urandom(16)).decode()
        self.conn.sendall(
            ("GET /ws/bot HTTP/1.1\r\n"
             f"Host: {url.netloc}\r\n"
             "Upgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
             f"X-Mock-User-Id: {owner}\r\n\r\n").encode())
        assert b"101 Switching Protocols" in _read_upgrade_response(self.conn)

    def read_frame(self):
        opcode, size = self._read_exact(2)
        length = size & 127
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        raw = self._read_exact(length)
        assert opcode & 15 != 8, "Bot WebSocket closed before the story completed"
        if opcode & 15 != 1:
            return {}
        frame = json.loads(raw)
        if frame.get("type") == "req":
            if frame["method"] == "chat.send":
                self.sends.append(frame)
                payload = {"run_id": frame["id"]}
            else:
                assert frame["method"] == "chat.history", frame
                payload = {"messages": []}
            self.send({"type": "res", "id": frame["id"], "ok": True, "payload": payload})
        return frame

    def receive(self, frame_id, attempts=200):
        for _ in range(attempts):
            frame = self.read_frame()
            if frame.get("type") == "res" and frame.get("id") == frame_id:
                return frame
        raise AssertionError(f"Bot never answered frame {frame_id}")

    def request(self, method, params, request_id=None):
        request_id = str(uuid.uuid4()) if request_id is None else request_id
        self.send({"type": "req", "id": request_id, "method": method, "params": params})
        return self.receive(request_id)

    def wait_send(self, text):
        for _ in range(200):
            matching = [frame for frame in self.sends
                        if text in json.dumps(frame["params"].get("message"))]
            if matching:
                return matching[0]
            self.read_frame()
        raise AssertionError(f"Bot never received chat.send containing {text}")

    def final(self, frame, text):
        self.send({"type": "event", "event": "chat.event", "seq": 0, "payload": {
            "run_id": frame["id"], "bcs_group_id": frame["params"]["bcs_group_id"],
            "state": "final", "stop_reason": "complete", "message": {
                "role": "assistant", "content": [{"type": "text", "text": text}],
                "timestamp": int(time.time() * 1000)},
        }})


class NativeTaskCallerStory(DelegationChainStory):
    def __init__(self, args):
        super().__init__(args)
        self.bot_socket = None
        self.web_socket = None
        self.parent = None
        self.parent_run_id = None

    def register_manager(self, headers):
        # Gateway-provider bots deliberately reject WS uplinks. Register an
        # ordinary Owner Bot through bot.connect instead of bypassing that fence.
        self.bot_socket = NativeBotSocket(self.args.base_url, self.args.human)
        connected = self.bot_socket.request("bot.connect", {"protocol_version": 2})
        assert connected.get("ok") is True, connected
        manager = connected["payload"]
        self.post("/bots/onboard", {
            "name": "Native delegating manager", "summary": "Owner-mode native tasks",
            "domains": [], "skills": [], "scopes": [],
        }, headers={"Authorization": "Bearer " + manager["token"]})
        ready = self.bot_socket.request("bot.status", {"status": "idle"})
        assert ready.get("ok") is True, ready
        return manager

    def open_parent(self):
        group = self.post("/groups", {
            "label": "Native Caller task delegation", "driver_bot": self.manager["bot_uuid"],
            "group_strategy": "manager_worker", "create_initial_session": False,
            "participants": [
                {"bot_uuid": self.manager["bot_uuid"], "role": "manager"},
                {"bot_uuid": self.worker["bot_uuid"], "role": "worker"},
                {"bot_uuid": "human_" + self.args.human, "role": "observer"},
            ],
        })
        self.group_id = group["id"]
        session = self.post(f"/groups/{segment(self.group_id)}/sessions", {
            "group_context_delivery": "inject", "input": self.context_marker,
        })
        self.session_id = session["id"]
        self.web_socket = WorkbenchSocket(self.args.base_url, self.args.human,
                                          self.group_id, self.session_id)
        connected = self.web_socket.receive(self.web_socket.connect_id)
        assert connected.get("ok") is True, connected
        self.web_socket.send({"type": "req", "id": "native-lease", "method": "lease.acquire",
                              "params": {"groupId": self.group_id}})
        acquired = self.web_socket.receive("native-lease")
        assert acquired.get("ok") is True, acquired
        lease = acquired["payload"]
        marker = "native-task-human-" + uuid.uuid4().hex
        self.web_socket.send({"type": "req", "id": "native-speak", "method": "chat.send",
            "params": {"group_id": self.group_id, "session_id": self.session_id,
                       "bot_id": "human_" + self.args.human, "message": marker,
                       "mentions": [self.manager["bot_uuid"]],
                       "leaseId": lease["leaseId"], "leaseEpoch": lease["epoch"]}})
        admitted = self.web_socket.receive("native-speak")
        assert admitted.get("ok") is True, admitted
        self.parent = self.bot_socket.wait_send(marker)
        assert self.context_marker in json.dumps(self.parent["params"]["message"])
        assert not (self.parent["params"].get("extensions") or {}).get("caller_execution_token")
        # The WS request ID is an accepted transport alias, not the canonical
        # Managed run. Resolve the latter from this human message's public view.
        message_id = admitted["payload"]["queue_admission"]["message_id"]
        path = ("/openapi/v1/collaboration/messages/" + segment(message_id)
                + "/deliveries?session_id=" + segment(self.session_id))
        rows = self.wait("canonical Manager run", lambda: self.request(path),
                         lambda rows: any(row["target_bot_id"] == self.manager["bot_uuid"]
                                          and row["run_id"] for row in rows))
        parent = next(row for row in rows if row["target_bot_id"] == self.manager["bot_uuid"])
        self.parent_run_id = parent["run_id"]
        return lease

    def task_args(self, message):
        return {"target_bot": self.worker["bot_uuid"], "message": message,
                "response_mode": "full", "timeout_s": 120}

    def start(self, args, invocation=None):
        invocation = invocation if invocation is not None else "toolu_" + uuid.uuid4().hex
        self.bot_socket.send({"type": "event", "event": "agent", "payload": {
            "run_id": self.parent["id"],
            "bcs_group_id": self.parent["params"]["bcs_group_id"],
            "bcs_session_id": self.session_id, "stream": "tool",
            "ts": int(time.time() * 1000), "data": {
                "phase": "start", "name": "bcs_assign_task", "toolCallId": invocation,
                "args": args,
            },
        }})
        return invocation

    def dispatch(self, args, request_id=None):
        return self.bot_socket.request("task.dispatch", {
            "group_id": self.group_id, "session_id": self.session_id, **args,
        }, request_id)

    def refused(self, args, reason, request_id=None):
        response = self.dispatch(args, request_id)
        assert response.get("ok") is False, response
        assert reason in response["error"]["message"], response

    def provider_requests(self):
        return self.request("/control/provider/requests", mock=True)["requests"]

    def worker_sends(self):
        return [item for item in self.provider_requests()
                if item["body"].get("method") == "chat.send"
                and (item["body"].get("to_bot") or {}).get("provider_bot_ref")
                == self.worker["provider_bot_ref"]]

    def verify_protected_ride(self, message):
        rides = self.wait("native protected Worker send", self.worker_sends, bool)
        assert len(rides) == 1, "Native task must dispatch exactly once"
        ride = rides[0]
        body = ride["body"]
        token = body["extensions"]["caller_execution_token"]
        assert token["format"] == "jwt" and token["token"], "Worker execution proof missing"
        assert body["id"] != self.parent_run_id
        assert body["session_id"] == self.session_id
        assert ride["authorization"] == "Bearer " + self.provider["bcs_to_provider_token"]
        assert message in json.dumps(body["message"])
        assert token["token"] not in json.dumps(body["message"]), "Proof leaked into model input"
        requests = self.provider_requests()
        ensures = [item for item in requests[:requests.index(ride)]
                   if item["body"].get("method") == "caller.container.ensure"]
        assert len(ensures) == 1, "Exactly one ensure must precede the protected send"
        ensure = ensures[0]["body"]
        binding = ensure["extensions"]["run"]
        assert binding["run_id"] == body["id"]
        assert binding["parent_run_id"] == self.parent_run_id
        assert binding["root_run_id"] == self.parent_run_id
        assert ensure["session_id"] == self.session_id
        assert ensure["extensions"]["bot_id"] == self.worker["bot_uuid"]
        assert ensure["extensions"]["caller_subject"] == body["extensions"]["caller_context"]["subject"]
        return body["id"], binding["message_turn_id"]

    def check_rejections(self):
        missing = self.task_args("native missing start")
        self.refused(missing, "tool start missing", "missing-start")
        self.start(missing)
        # A failed request is an uncertain receipt, not an opportunity to retry
        # after manufacturing new evidence. A new request needs a fresh action.
        self.refused(missing, "previous outcome unconfirmed", "missing-start")

        ambiguous = self.task_args("native ambiguous starts")
        self.start(ambiguous)
        self.start(ambiguous)
        self.refused(ambiguous, "ambiguous or already consumed")

        invalid = self.task_args("native empty invocation")
        self.start(invalid, invocation="")
        self.refused(invalid, "ambiguous or already consumed")
        self.refused(self.task_args("native invalid request"), "invalid native request identity",
                     request_id="x" * 257)
        print("  missing/late, ambiguous and invalid starts fail closed", flush=True)

    def run(self):
        try:
            self.setup()
            lease = self.open_parent()
            self.post("/control/provider/clear", mock=True)
            self.check_rejections()
            assert not self.worker_sends(), "Rejected requests reached the Worker"

            args = self.task_args("native worker handles the delegated leg")
            invocation = self.start(args)
            self.start(args, invocation)  # Exact start retransmission is harmless.
            # Serial WS processing gives an event barrier without fixed sleeps.
            ready = self.bot_socket.request("bot.status", {"status": "idle"})
            assert ready.get("ok") is True, ready
            assert not self.worker_sends(), "A tool start must not itself dispatch"
            accepted = self.dispatch(args, "native-success")
            assert accepted.get("ok") is True, accepted
            assert accepted["payload"]["task_id"], accepted
            run_id, turn_id = self.verify_protected_ride(args["message"])

            replay = self.dispatch(args, "native-success")
            assert replay.get("ok") is True and replay["payload"] == accepted["payload"], replay
            self.refused({**args, "message": "changed payload"},
                         "request identity reused with different input", "native-success")
            self.refused(args, "ambiguous or already consumed", "changed-request-id")
            print("  protected native task inherits parent identity; replay returns the same task", flush=True)

            answer = "native worker finished the delegated leg"
            self.reply_chat_final(run_id, answer, {
                "X-BCN-Provider-Id": self.provider["provider_id"],
                "Authorization": "Bearer " + self.worker["bot_runtime_token"],
            })
            deliveries = ("/openapi/v1/collaboration/messages/" + segment(turn_id)
                          + "/deliveries?session_id=" + segment(self.session_id))
            self.wait("completed native Worker delivery", lambda: self.request(deliveries),
                      lambda rows: any(row["target_bot_id"] == self.worker["bot_uuid"]
                                       and row["status"] == "completed" for row in rows))
            self.bot_socket.final(self.parent, "manager delegated the work")
            result = self.bot_socket.wait_send(answer)
            assert result["id"] != self.parent["id"]
            assert not (result["params"].get("extensions") or {}).get("caller_execution_token")
            self.bot_socket.final(result, "manager finished delegated work")
            assert len(self.worker_sends()) == 1, "Replay or refusal created another Worker send"
            self.web_socket.send({"type": "req", "id": "native-release", "method": "lease.release",
                "params": {"groupId": self.group_id, "leaseId": lease["leaseId"],
                           "leaseEpoch": lease["epoch"]}})
            released = self.web_socket.receive("native-release")
            assert released.get("ok") is True, released
            print("  Worker completion and result delivery verified over public APIs", flush=True)
        finally:
            self.restore_delivery_policy()
            if self.group_id and self.manager:
                try:
                    self.request(f"/groups/{segment(self.group_id)}"
                                 f"?bot_id={segment(self.manager['bot_uuid'])}", method="DELETE")
                except Exception as error:  # noqa: BLE001 — best-effort cleanup
                    print(f"  group cleanup skipped: {error}", flush=True)
            for client in (self.web_socket, self.bot_socket):
                if client:
                    try:
                        client.close()
                    except OSError:
                        client.conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mock-url", required=True)
    parser.add_argument("--human", required=True)
    NativeTaskCallerStory(parser.parse_args()).run()


if __name__ == "__main__":
    main()
